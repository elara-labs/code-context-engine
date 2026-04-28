#!/usr/bin/env python
"""Recall quality benchmark for session_recall.

Seeds a memory.db with ~50 decisions across 7 topics, runs a fixed set of
queries with known relevant hits, and reports recall@k / precision@k / MRR.

Use this to tune `_SESSION_RECALL_MIN_SIM` (mcp_server.py) and
`_VEC_MAX_DISTANCE` (memory/db.py) against data instead of vibes.

    $ python scripts/bench_recall.py
    $ python scripts/bench_recall.py --min-sim 0.50 --vec-max 0.95
    $ python scripts/bench_recall.py --k 10 --json out.json

The bench creates a throwaway tmp project so it doesn't touch ~/.cce.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock


# ── Corpus: 50 decisions across 7 topics ───────────────────────────────────

CORPUS: list[tuple[str, str, str]] = [
    # (topic_tag, decision, reason)
    ("auth", "Use JWT with RS256", "Mesh already issues RS256 keys"),
    ("auth", "Sessions in Redis with 7-day TTL", "Cheap, simple, scales"),
    ("auth", "Refresh tokens rotate on every use", "Invalidates replay attacks"),
    ("auth", "Adopt OAuth2 PKCE for mobile", "PKCE blocks code-interception"),
    ("auth", "Rate-limit login at 5/min/IP", "Slows credential stuffing"),
    ("auth", "Drop username/password — go passkeys-only", "WebAuthn is supported everywhere now"),
    ("auth", "Hash passwords with argon2id", "Memory-hard; bcrypt is showing its age"),

    ("db", "Use Postgres for primary store", "Boring, complete, ACID"),
    ("db", "Read replicas in same AZ as app", "Cross-AZ latency dominates"),
    ("db", "Migrations run via Alembic", "Already in the Python toolchain"),
    ("db", "Use SQLite for trade journal", "Embedded, atomic, no daemon"),
    ("db", "Avoid stored procedures", "Hard to test and version-control"),
    ("db", "Soft-delete with deleted_at column", "Audit trail beats hard-delete"),
    ("db", "Index every FK", "Postgres doesn't index FKs by default"),

    ("ml", "Pick XGBoost for next-price prediction", "Fast, interpretable, tabular-friendly"),
    ("ml", "Train on rolling 90-day windows", "Avoids regime drift"),
    ("ml", "Validate with walk-forward CV", "Time-series splits leak otherwise"),
    ("ml", "Feature store as a Parquet directory", "No new system to operate"),
    ("ml", "Track experiments in MLflow", "Drop-in for the team's existing flow"),
    ("ml", "Quantise inference to int8", "5x throughput at 1% accuracy loss"),

    ("infra", "Containerise with distroless base", "Smaller attack surface"),
    ("infra", "Single GitHub Actions runner per env", "Avoids stage→prod drift"),
    ("infra", "Use Tailscale for ops backplane", "Skip VPN setup hell"),
    ("infra", "Logs to Loki, metrics to Prometheus", "Free-tier, plenty for our scale"),
    ("infra", "Restore drills monthly", "Untested backups don't work"),
    ("infra", "Terraform in a single root module", "Premature factoring is the enemy"),

    ("perf", "Risk limit at 2% per trade", "Kelly criterion suggests this for our edge"),
    ("perf", "Cache hot reads in Redis 60s TTL", "Trim p99 by ~40%"),
    ("perf", "Profile with py-spy in production", "No source-code changes"),
    ("perf", "Pin pgbouncer pool at 30", "Above 30 = lock contention spikes"),
    ("perf", "Roll positions at expiry-2", "Avoids assignment risk on Friday"),
    ("perf", "Avoid N+1 in REST handlers", "Use selectinload across the board"),

    ("testing", "Integration tests hit a real Postgres", "Mock divergence burned us last quarter"),
    ("testing", "pytest-xdist with 4 workers", "Memory floor for ONNX models"),
    ("testing", "Property tests for parsers via hypothesis", "Caught 3 corner cases pre-merge"),
    ("testing", "Skip slow tests with -m \"not slow\"", "Default suite under 10s"),
    ("testing", "Snapshot tests for HTTP responses", "Pin contract per endpoint"),
    ("testing", "CI fails on coverage drop > 1%", "Anti-rot ratchet"),
    ("testing", "Use freezegun for time-dependent code", "No more flaky midnight tests"),

    ("frontend", "Tailwind for design system", "Cuts CSS bikeshedding"),
    ("frontend", "React Query for data fetching", "Replaces our hand-rolled cache"),
    ("frontend", "Adopt Vite over Webpack", "Cold-start drops from 4s to 0.6s"),
    ("frontend", "Type all API responses with zod", "Runtime + compile-time both"),
    ("frontend", "Suspense boundaries at route level", "Stops layout shift on data load"),
    ("frontend", "TanStack Table for grids", "Better virtualisation than the alternatives"),
    ("frontend", "Drop CSS-in-JS for vanilla CSS modules", "Hydration cost dominated bundle"),
]


# ── Queries with known relevant decisions (referenced by index in CORPUS) ───

@dataclass
class Query:
    text: str
    relevant: list[int]   # indices into CORPUS that *should* be returned
    notes: str = ""


QUERIES: list[Query] = [
    Query(
        text="auth flow",
        relevant=[0, 1, 2, 3, 4, 5, 6],
        notes="all 7 auth decisions",
    ),
    Query(
        text="how do we hash passwords",
        relevant=[6],
        notes="argon2id; lexical 'hash passwords' present",
    ),
    Query(
        text="JWT token refresh",
        relevant=[0, 2],
        notes="RS256 + rotation",
    ),
    Query(
        text="database choice",
        relevant=[7, 10],
        notes="Postgres + SQLite",
    ),
    Query(
        text="machine learning model",
        relevant=[14, 19],
        notes="XGBoost + int8 quantisation (semantic)",
    ),
    Query(
        text="risk management",
        relevant=[26, 30],
        notes="Kelly limit + roll positions",
    ),
    Query(
        text="testing strategy",
        relevant=[32, 33, 34, 35, 36, 37, 38],
        notes="all testing decisions",
    ),
    Query(
        text="ci pipeline",
        relevant=[22, 37],
        notes="GH Actions + coverage ratchet",
    ),
    Query(
        text="frontend bundle size",
        relevant=[41, 45],
        notes="Vite + drop CSS-in-JS",
    ),
    Query(
        text="backup strategy",
        relevant=[24],
        notes="restore drills",
    ),
    Query(
        text="how is the weather today",
        relevant=[],
        notes="should reject as off-topic",
    ),
    Query(
        text="best ice cream flavour",
        relevant=[],
        notes="should reject as off-topic",
    ),
]


# ── Metrics ─────────────────────────────────────────────────────────────────

def recall_at_k(returned_indices: list[int], relevant: list[int], k: int) -> float:
    if not relevant:
        return 1.0 if not returned_indices[:k] else 0.0
    hit = sum(1 for i in returned_indices[:k] if i in relevant)
    return hit / len(relevant)


def precision_at_k(returned_indices: list[int], relevant: list[int], k: int) -> float:
    top = returned_indices[:k]
    if not top:
        return 1.0 if not relevant else 0.0
    hit = sum(1 for i in top if i in relevant)
    return hit / len(top)


def mrr(returned_indices: list[int], relevant: list[int]) -> float:
    if not relevant:
        return 1.0 if not returned_indices else 0.0
    for rank, idx in enumerate(returned_indices, start=1):
        if idx in relevant:
            return 1.0 / rank
    return 0.0


# ── Bench harness ───────────────────────────────────────────────────────────

def _build_mcp(storage_path: Path, project_name: str):
    """Construct a real ContextEngineMCP wired to bge-small."""
    from context_engine.config import Config
    from context_engine.indexer.embedder import Embedder
    from context_engine.integration.mcp_server import ContextEngineMCP

    config = Config(
        storage_path=str(storage_path),
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    embedder = Embedder()
    backend = MagicMock()
    backend._vector_store.count.return_value = 0
    return ContextEngineMCP(
        retriever=MagicMock(),
        backend=backend,
        compressor=MagicMock(),
        embedder=embedder,
        config=config,
    )


def _seed_corpus(mcp) -> None:
    for _, decision, reason in CORPUS:
        mcp._handle_record_decision({"decision": decision, "reason": reason})


def _returned_indices(mcp, query: str) -> list[int]:
    matches = mcp._search_sessions(query)
    indices: list[int] = []
    for line in matches:
        # Each match contains "<decision> — <reason>" somewhere in its body.
        for i, (_, decision, reason) in enumerate(CORPUS):
            needle = f"{decision} — {reason}"
            if needle in line and i not in indices:
                indices.append(i)
                break
    return indices


def run_bench(args) -> dict:
    # Override thresholds via env vars before importing — simpler than monkey-
    # patching the constants, and matches how a deploy would tune them.
    import context_engine.integration.mcp_server as ms
    import context_engine.memory.db as mdb
    if args.min_sim is not None:
        ms._SESSION_RECALL_MIN_SIM = args.min_sim
    if args.vec_max is not None:
        mdb._VEC_MAX_DISTANCE = args.vec_max

    with tempfile.TemporaryDirectory(prefix="cce-bench-") as td:
        storage = Path(td) / "storage"
        project = Path(td) / "proj"
        project.mkdir()
        os.chdir(project)

        mcp = _build_mcp(storage, project.name)
        _seed_corpus(mcp)
        # Let the daemon-thread vec backfill catch up (real bge-small is fast
        # enough that it usually completes before we even query, but be safe).
        time.sleep(1.0)

        rows: list[dict] = []
        recall_total: list[float] = []
        precision_total: list[float] = []
        mrr_total: list[float] = []
        for q in QUERIES:
            ret = _returned_indices(mcp, q.text)
            r_at_k = recall_at_k(ret, q.relevant, args.k)
            p_at_k = precision_at_k(ret, q.relevant, args.k)
            mrr_v = mrr(ret, q.relevant)
            rows.append({
                "query": q.text,
                "relevant_count": len(q.relevant),
                "returned_count": len(ret),
                "recall_at_k": r_at_k,
                "precision_at_k": p_at_k,
                "mrr": mrr_v,
                "notes": q.notes,
            })
            recall_total.append(r_at_k)
            precision_total.append(p_at_k)
            mrr_total.append(mrr_v)

        mcp._memory_conn.close()

    return {
        "config": {
            "k": args.k,
            "min_sim": ms._SESSION_RECALL_MIN_SIM,
            "vec_max": mdb._VEC_MAX_DISTANCE,
            "corpus_size": len(CORPUS),
            "query_count": len(QUERIES),
        },
        "rows": rows,
        "aggregate": {
            "recall_at_k_mean": sum(recall_total) / len(recall_total),
            "precision_at_k_mean": sum(precision_total) / len(precision_total),
            "mrr_mean": sum(mrr_total) / len(mrr_total),
        },
    }


def _print_table(results: dict) -> None:
    cfg = results["config"]
    print()
    print(f"recall@{cfg['k']} bench · bge-small + sqlite-vec hybrid")
    print(f"corpus: {cfg['corpus_size']} decisions  |  queries: {cfg['query_count']}")
    print(f"thresholds: min_sim={cfg['min_sim']:.2f}  vec_max={cfg['vec_max']:.2f}")
    print("-" * 78)
    print(f"{'query':<32} {'rel':>5} {'ret':>5} {'R@k':>6} {'P@k':>6} {'MRR':>6}")
    print("-" * 78)
    for row in results["rows"]:
        print(
            f"{row['query'][:32]:<32} "
            f"{row['relevant_count']:>5} "
            f"{row['returned_count']:>5} "
            f"{row['recall_at_k']:>6.2f} "
            f"{row['precision_at_k']:>6.2f} "
            f"{row['mrr']:>6.2f}"
        )
    print("-" * 78)
    agg = results["aggregate"]
    print(
        f"{'AGGREGATE':<32} {'':>5} {'':>5} "
        f"{agg['recall_at_k_mean']:>6.2f} "
        f"{agg['precision_at_k_mean']:>6.2f} "
        f"{agg['mrr_mean']:>6.2f}"
    )
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--k", type=int, default=5, help="cutoff for recall@k / precision@k")
    p.add_argument("--min-sim", type=float, default=None,
                   help="override _SESSION_RECALL_MIN_SIM")
    p.add_argument("--vec-max", type=float, default=None,
                   help="override _VEC_MAX_DISTANCE")
    p.add_argument("--json", type=str, default=None, help="write results JSON to this path")
    args = p.parse_args()

    results = run_bench(args)
    _print_table(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
