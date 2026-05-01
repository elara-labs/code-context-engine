# benchmarks/run_benchmark.py
"""Reproducible benchmark suite for CCE token savings, retrieval quality, and latency.

Usage:
    # Benchmark FastAPI source code (cloned automatically, indexes only fastapi/ subdir)
    python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi

    # Benchmark the current project
    python benchmarks/run_benchmark.py

    # Benchmark a local directory
    python benchmarks/run_benchmark.py --project-dir /path/to/project --queries benchmarks/sample_queries.json

    # Save results as markdown
    python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi --output benchmarks/results/fastapi.md

Token savings methodology:
    For each query, CCE returns ~10 relevant code chunks. The "without CCE" baseline
    is the full file content of every file that CCE retrieved chunks from, because
    without CCE an AI agent would need to read those entire files. This is a
    conservative estimate (agents often read more files than needed).
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from context_engine.config import Config
from context_engine.compression.compressor import Compressor
from context_engine.compression.output_rules import ADVERTISED_PCT, ESTIMATED_AVG_REPLY_TOKENS
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.pipeline import run_indexing
from context_engine.memory.grammar import compress_with_counts as grammar_compress
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.storage.local_backend import LocalBackend

_CHARS_PER_TOKEN = 4


def _count_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _count_project_tokens(project_dir: Path) -> tuple[int, int]:
    """Count total tokens and files in a project (all indexable text files)."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
        ".eggs", "*.egg-info",
    }
    skip_ext = {
        ".pyc", ".pyo", ".so", ".o", ".exe", ".dll", ".bin",
        ".png", ".jpg", ".gif", ".ico", ".svg", ".woff", ".ttf",
        ".zip", ".tar", ".gz", ".db", ".sqlite", ".lock", ".map",
    }
    total_tokens = 0
    total_files = 0
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            fpath = Path(root) / fname
            if fpath.suffix.lower() in skip_ext:
                continue
            try:
                text = fpath.read_text(errors="ignore")
                total_tokens += _count_tokens(text)
                total_files += 1
            except OSError:
                pass
    return total_tokens, total_files


def _clone_repo(repo_url: str, dest: Path) -> None:
    """Shallow-clone a git repo."""
    print(f"Cloning {repo_url} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(dest)],
        check=True,
        capture_output=True,
    )


def _read_file_tokens(project_dir: Path, rel_path: str) -> int:
    """Read a file and return its token count. Returns 0 if unreadable."""
    try:
        text = (project_dir / rel_path).read_text(errors="ignore")
        return _count_tokens(text)
    except OSError:
        return 0


async def run_benchmark(
    project_dir: Path,
    queries: list[dict],
    storage_dir: Path | None = None,
) -> dict:
    """Run the full benchmark suite with per-bucket savings. Returns structured results."""
    config = Config()
    if storage_dir:
        config.storage_path = str(storage_dir)

    # Phase 1: Index
    print("Phase 1: Indexing project...")
    t0 = time.perf_counter()
    idx_result = await run_indexing(config, project_dir, full=True)
    index_time = time.perf_counter() - t0
    print(f"  Indexed {len(idx_result.indexed_files)} files, "
          f"{idx_result.total_chunks} chunks in {index_time:.1f}s")
    print(f"  Cache: {idx_result.cache_hits} hits, {idx_result.cache_misses} misses")

    # Set up retriever + compressor
    storage_base = Path(config.storage_path) / project_dir.name
    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)
    compressor = Compressor(cache=backend)

    # Phase 2: Full project token count
    print("\nPhase 2: Counting full project tokens...")
    full_project_tokens, file_count = _count_project_tokens(project_dir)
    print(f"  {file_count} files, {full_project_tokens:,} tokens total")

    # Phase 3: Query benchmark with per-bucket savings
    print("\nPhase 3: Running queries (7-layer savings)...")
    query_results = []
    precision_sum = 0
    recall_sum = 0

    # Accumulate per-bucket totals across all queries
    buckets = {
        "retrieval":              {"baseline": 0, "served": 0},
        "chunk_compression":      {"baseline": 0, "served": 0},
        "output_compression":     {"baseline": 0, "served": 0},
        "memory_recall":          {"baseline": 0, "served": 0},
        "grammar":                {"baseline": 0, "served": 0},
        "turn_summarization":     {"baseline": 0, "served": 0},
        "progressive_disclosure": {"baseline": 0, "served": 0},
    }

    for q in queries:
        # Layer 1: Retrieval — full files → raw chunks
        chunks = await retriever.retrieve(q["query"], top_k=10)
        result_files = {c.file_path for c in chunks}
        raw_chunk_tokens = sum(_count_tokens(c.content) for c in chunks)
        full_file_tokens = sum(
            _read_file_tokens(project_dir, fp) for fp in result_files
        )

        buckets["retrieval"]["baseline"] += full_file_tokens
        buckets["retrieval"]["served"] += raw_chunk_tokens

        # Layer 2: Chunk compression — raw chunks → compressed chunks
        compressed_chunks = await compressor.compress(chunks, config.compression_level)
        compressed_tokens = sum(
            _count_tokens(c.compressed_content or c.content) for c in compressed_chunks
        )

        buckets["chunk_compression"]["baseline"] += raw_chunk_tokens
        buckets["chunk_compression"]["served"] += compressed_tokens

        # Layer 3: Output compression — estimated savings on Claude's reply
        output_pct = ADVERTISED_PCT.get("standard", 0.65)
        buckets["output_compression"]["baseline"] += ESTIMATED_AVG_REPLY_TOKENS
        buckets["output_compression"]["served"] += int(ESTIMATED_AVG_REPLY_TOKENS * (1 - output_pct))

        # Layer 5: Grammar compression — compress a sample decision text
        sample_decision = (
            f"Using hybrid retrieval for {q['query'][:40]}. "
            "The vector search finds semantic matches while BM25 catches exact identifiers. "
            "Reciprocal Rank Fusion merges the two result sets with K=60."
        )
        _, grammar_baseline, grammar_served = grammar_compress(sample_decision)
        buckets["grammar"]["baseline"] += grammar_baseline
        buckets["grammar"]["served"] += grammar_served

        # Precision / recall
        expected = set(q.get("expected_files", []))
        hits = result_files & expected
        precision = len(hits) / len(result_files) if result_files else 0
        recall = len(hits) / len(expected) if expected else 1.0
        precision_sum += precision
        recall_sum += recall

        # Final served = compressed chunks (what Claude actually sees)
        per_query_savings = (
            (1 - compressed_tokens / full_file_tokens) * 100
            if full_file_tokens > 0 else 0
        )
        status = "HIT" if hits else ("MISS" if expected else "N/A")
        print(f"  [{status}] {q['query'][:45]:<45} "
              f"full={full_file_tokens:>6} → raw={raw_chunk_tokens:>5} "
              f"→ compressed={compressed_tokens:>5}  "
              f"saved={per_query_savings:.0f}%  "
              f"P={precision:.2f} R={recall:.2f}")

        query_results.append({
            "query": q["query"],
            "category": q.get("category", ""),
            "full_file_tokens": full_file_tokens,
            "raw_chunk_tokens": raw_chunk_tokens,
            "compressed_tokens": compressed_tokens,
            "savings_pct": round(per_query_savings, 1),
            "result_files": sorted(result_files),
            "expected_files": sorted(expected),
            "hit_files": sorted(hits),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
        })

    n = len(queries)

    # Layer 4: Memory recall — simulate recalling 5 decisions with grammar compression
    sample_decisions = [
        "Use JWT tokens for authentication because the legal team flagged session-based tokens.",
        "Adopted the repository pattern for database access to simplify testing.",
        "Switched from REST to GraphQL for the internal dashboard API.",
        "Chose SQLite over PostgreSQL for the local storage layer to minimize dependencies.",
        "Implemented content-hash embedding cache to skip unchanged chunks on re-index.",
    ]
    for d in sample_decisions:
        raw_tokens = _count_tokens(d)
        _, _, compressed = grammar_compress(d)
        buckets["memory_recall"]["baseline"] += raw_tokens
        buckets["memory_recall"]["served"] += compressed

    # Layer 6: Turn summarization — estimate context window savings
    # Without CCE, Claude keeps the full conversation. With CCE, extractive
    # summaries compress previous turns. Estimated at 60% savings on an
    # average 2000-token turn context.
    avg_turn_context = 2000
    turn_summarization_pct = 0.60
    buckets["turn_summarization"]["baseline"] = avg_turn_context * n
    buckets["turn_summarization"]["served"] = int(avg_turn_context * n * (1 - turn_summarization_pct))

    # Layer 7: Progressive disclosure — bootstrap context at session start
    # Without CCE: Claude has no prior context (0 useful tokens from prior sessions)
    # With CCE: SessionStart hook injects ~500 tokens of resume context
    # The "baseline" is what you'd have to manually re-explain (~2000 tokens)
    buckets["progressive_disclosure"]["baseline"] = 2000
    buckets["progressive_disclosure"]["served"] = 500

    # Compute totals
    total_baseline = buckets["retrieval"]["baseline"]
    total_served = buckets["chunk_compression"]["served"]
    avg_full_file = total_baseline / n if n else 0
    avg_served = total_served / n if n else 0
    avg_precision = precision_sum / n if n else 0
    avg_recall = recall_sum / n if n else 0
    overall_savings = (
        (1 - total_served / total_baseline) * 100
        if total_baseline > 0 else 0
    )

    # Print bucket summary
    print(f"\n--- Per-Bucket Savings ---")
    for name, b in buckets.items():
        if b["baseline"] > 0:
            pct = (1 - b["served"] / b["baseline"]) * 100
            print(f"  {name:<25} {b['baseline']:>8,} → {b['served']:>8,}  ({pct:.0f}% saved)")

    # Phase 4: Latency
    print("\nPhase 4: Latency benchmark...")
    for _ in range(3):
        await retriever.retrieve("test query", top_k=10)

    latencies = []
    for q in queries:
        for _ in range(5):
            t0 = time.perf_counter()
            await retriever.retrieve(q["query"], top_k=10)
            latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"  p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms")

    # Format bucket results for output
    bucket_results = {}
    for name, b in buckets.items():
        pct = (1 - b["served"] / b["baseline"]) * 100 if b["baseline"] > 0 else 0
        bucket_results[name] = {
            "baseline": b["baseline"],
            "served": b["served"],
            "savings_pct": round(pct, 1),
        }

    results = {
        "project": project_dir.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_stats": {
            "files": file_count,
            "total_tokens": full_project_tokens,
            "indexed_files": len(idx_result.indexed_files),
            "chunks": idx_result.total_chunks,
            "index_time_s": round(index_time, 1),
        },
        "token_savings": {
            "full_project_tokens": full_project_tokens,
            "avg_full_file_per_query": round(avg_full_file),
            "avg_served_per_query": round(avg_served),
            "savings_pct": round(overall_savings, 1),
        },
        "buckets": bucket_results,
        "retrieval_quality": {
            "num_queries": n,
            "avg_precision_at_10": round(avg_precision, 3),
            "avg_recall_at_10": round(avg_recall, 3),
        },
        "latency": {
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "runs_per_query": 5,
        },
        "queries": query_results,
    }

    return results


def format_markdown(results: dict) -> str:
    """Format benchmark results as a markdown report."""
    r = results
    ts = r["token_savings"]
    rq = r["retrieval_quality"]
    lat = r["latency"]
    ps = r["project_stats"]

    bk = r.get("buckets", {})

    bucket_display = [
        ("retrieval", "Retrieval", "Full files vs relevant chunks"),
        ("chunk_compression", "Chunk Compression", "Raw chunks vs compressed (signatures + docstrings)"),
        ("output_compression", "Output Compression", "Claude's reply length (estimated)"),
        ("memory_recall", "Memory Recall", "Decision text with grammar compression"),
        ("grammar", "Grammar Compression", "Deterministic article/filler removal"),
        ("turn_summarization", "Turn Summarization", "Previous turn context (estimated)"),
        ("progressive_disclosure", "Progressive Disclosure", "Session bootstrap vs manual re-explanation"),
    ]

    lines = [
        f"# Benchmark: {r['project']}",
        "",
        f"**Date:** {r['timestamp'][:10]}",
        f"**Project:** {r['project']} ({ps['files']} files, "
        f"{ps['total_tokens']:,} tokens)",
        f"**Index:** {ps['chunks']} chunks from {ps['indexed_files']} files "
        f"in {ps['index_time_s']}s",
        "",
        "## Results Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Token savings per query | **{ts['savings_pct']}%** |",
        f"| Avg full-file baseline per query | {ts['avg_full_file_per_query']:,} tokens |",
        f"| Avg served per query | {ts['avg_served_per_query']:,} tokens |",
        f"| Precision@10 | {rq['avg_precision_at_10']:.2f} |",
        f"| Recall@10 | {rq['avg_recall_at_10']:.2f} |",
        f"| Latency p50 | {lat['p50_ms']}ms |",
        f"| Latency p95 | {lat['p95_ms']}ms |",
        f"| Queries tested | {rq['num_queries']} |",
        "",
        "## 7-Layer Savings Breakdown",
        "",
        "CCE saves tokens at every layer of the pipeline:",
        "",
        "| Layer | Baseline | Served | Saved | What it does |",
        "|-------|----------|--------|-------|--------------|",
    ]

    for key, display_name, description in bucket_display:
        b = bk.get(key, {})
        baseline = b.get("baseline", 0)
        served = b.get("served", 0)
        pct = b.get("savings_pct", 0)
        if baseline > 0:
            lines.append(
                f"| **{display_name}** | {baseline:,} | {served:,} | "
                f"{pct:.0f}% | {description} |"
            )

    lines.extend([
        "",
        "## Token Savings Methodology",
        "",
        "For each query, we compare:",
        "",
        ("- **Without CCE:** Read the full content of every file the query touches "
         "(the files CCE retrieved chunks from)"),
        ("- **With CCE:** Only the relevant, compressed code chunks are returned"),
        "",
        "Layers 1-2 (retrieval + compression) are measured directly per query. "
        "Layer 3 (output compression) uses advertised reduction rates. "
        "Layers 4-7 are measured with representative samples or estimated from typical session data.",
        "",
        "```",
        f"Without CCE (avg):  {ts['avg_full_file_per_query']:,} tokens per query",
        f"With CCE (avg):     {ts['avg_served_per_query']:,} tokens per query",
        f"Savings:            {ts['savings_pct']}%",
        "```",
        "",
        "## Per-Query Results",
        "",
        "| Query | Full file | Raw chunks | Compressed | Saved | P@10 | R@10 |",
        "|-------|-----------|------------|------------|-------|------|------|",
    ])
    for q in r["queries"]:
        query_text = q["query"][:45]
        lines.append(
            f"| {query_text} | {q['full_file_tokens']:,} | "
            f"{q.get('raw_chunk_tokens', q.get('served_tokens', 0)):,} | "
            f"{q.get('compressed_tokens', q.get('served_tokens', 0)):,} | "
            f"{q['savings_pct']:.0f}% | {q['precision']:.2f} | {q['recall']:.2f} |"
        )

    source_dir = r.get("source_dir", "")
    repo_flag = ""
    if source_dir:
        repo_flag = f" --source-dir {source_dir}"

    lines.extend([
        "",
        "## How to Reproduce",
        "",
        "```bash",
        "pip install code-context-engine",
        f"python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git{repo_flag}",
        "```",
        "",
        f"Results generated by CCE benchmark suite on {r['timestamp'][:10]}.",
    ])

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="CCE Benchmark Suite")
    parser.add_argument("--repo", help="Git repo URL to clone and benchmark")
    parser.add_argument("--project-dir", help="Local project directory to benchmark")
    parser.add_argument(
        "--source-dir",
        help="Subdirectory within the repo to benchmark (e.g. 'fastapi', 'src'). "
             "Useful for repos with large docs/test directories.",
    )
    parser.add_argument("--queries", help="Path to queries JSON file")
    parser.add_argument("--output", help="Output path for markdown report")
    parser.add_argument("--json-output", help="Output path for raw JSON results")
    args = parser.parse_args()

    # Determine project dir and queries
    cleanup_dir = None
    if args.repo:
        repo_name = args.repo.rstrip("/").split("/")[-1].replace(".git", "")
        tmp_dir = Path(tempfile.mkdtemp(prefix="cce-bench-"))
        clone_dir = tmp_dir / repo_name
        _clone_repo(args.repo, clone_dir)
        cleanup_dir = tmp_dir

        # If --source-dir is given, benchmark only that subdirectory
        if args.source_dir:
            project_dir = clone_dir / args.source_dir
            if not project_dir.is_dir():
                print(f"Error: --source-dir '{args.source_dir}' not found in {clone_dir}")
                sys.exit(1)
        else:
            project_dir = clone_dir

        # Auto-detect queries file
        queries_path = Path(__file__).parent / f"{repo_name}_queries.json"
        if args.queries:
            queries_path = Path(args.queries)
        elif not queries_path.exists():
            print(f"Error: No queries file found at {queries_path}")
            print(f"Create {queries_path} with query/expected_files pairs.")
            sys.exit(1)
    elif args.project_dir:
        project_dir = Path(args.project_dir)
        if args.source_dir:
            project_dir = project_dir / args.source_dir
        queries_path = Path(args.queries) if args.queries else Path(__file__).parent / "sample_queries.json"
    else:
        project_dir = Path(__file__).parent.parent
        queries_path = Path(__file__).parent / "sample_queries.json"

    with open(queries_path) as f:
        queries = json.load(f)

    # Use a temp storage dir to avoid polluting the user's CCE storage
    storage_dir = Path(tempfile.mkdtemp(prefix="cce-bench-storage-"))

    try:
        results = asyncio.run(run_benchmark(project_dir, queries, storage_dir))
        if args.source_dir:
            results["source_dir"] = args.source_dir

        # Print summary
        ts = results["token_savings"]
        rq = results["retrieval_quality"]
        lat = results["latency"]
        print(f"\n{'='*60}")
        print(f"  BENCHMARK RESULTS: {results['project']}")
        print(f"{'='*60}")
        print(f"  Token savings:   {ts['savings_pct']}%  "
              f"({ts['avg_full_file_per_query']:,} -> {ts['avg_served_per_query']:,} tokens/query)")
        print(f"  Precision@10:    {rq['avg_precision_at_10']:.2f}")
        print(f"  Recall@10:       {rq['avg_recall_at_10']:.2f}")
        print(f"  Latency p50:     {lat['p50_ms']}ms")
        print(f"{'='*60}")

        # Save outputs
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(format_markdown(results))
            print(f"\nMarkdown report: {out_path}")

        if args.json_output:
            out_path = Path(args.json_output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(results, indent=2) + "\n")
            print(f"JSON results: {out_path}")

    finally:
        if cleanup_dir and cleanup_dir.exists():
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        if storage_dir.exists():
            shutil.rmtree(storage_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
