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
    """Run the full benchmark suite with per-layer savings. Returns structured results.

    Each layer is measured independently against its own baseline (no stacking):
      - Retrieval: full files → relevant chunks (measured per query)
      - Chunk compression: raw chunks → compressed chunks (measured per query)
      - Output compression: Claude reply reduction (estimated per level)
      - Grammar: prose → grammar-compressed prose (measured on sample text)
    """
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

    # Phase 3: Query benchmark — measure retrieval + compression per query
    print("\nPhase 3: Running queries...")
    query_results = []
    total_full_file = 0
    total_raw_chunks = 0
    total_compressed = 0
    precision_sum = 0
    recall_sum = 0

    for q in queries:
        # Retrieval: full files → raw chunks
        chunks = await retriever.retrieve(q["query"], top_k=10)
        result_files = {c.file_path for c in chunks}
        raw_chunk_tokens = sum(_count_tokens(c.content) for c in chunks)
        full_file_tokens = sum(
            _read_file_tokens(project_dir, fp) for fp in result_files
        )

        # Chunk compression: raw chunks → compressed
        compressed_chunks = await compressor.compress(chunks, config.compression_level)
        compressed_tokens = sum(
            _count_tokens(c.compressed_content or c.content) for c in compressed_chunks
        )

        total_full_file += full_file_tokens
        total_raw_chunks += raw_chunk_tokens
        total_compressed += compressed_tokens

        expected = set(q.get("expected_files", []))
        hits = result_files & expected
        precision = len(hits) / len(result_files) if result_files else 0
        recall = len(hits) / len(expected) if expected else 1.0
        precision_sum += precision
        recall_sum += recall

        retrieval_pct = (
            (1 - raw_chunk_tokens / full_file_tokens) * 100
            if full_file_tokens > 0 else 0
        )
        compression_pct = (
            (1 - compressed_tokens / raw_chunk_tokens) * 100
            if raw_chunk_tokens > 0 else 0
        )
        combined_pct = (
            (1 - compressed_tokens / full_file_tokens) * 100
            if full_file_tokens > 0 else 0
        )

        status = "HIT" if hits else ("MISS" if expected else "N/A")
        print(f"  [{status}] {q['query'][:45]:<45} "
              f"full={full_file_tokens:>6} → chunks={raw_chunk_tokens:>5} "
              f"({retrieval_pct:.0f}%) → compressed={compressed_tokens:>4} "
              f"({compression_pct:.0f}%)  "
              f"P={precision:.2f} R={recall:.2f}")

        query_results.append({
            "query": q["query"],
            "category": q.get("category", ""),
            "full_file_tokens": full_file_tokens,
            "raw_chunk_tokens": raw_chunk_tokens,
            "compressed_tokens": compressed_tokens,
            "retrieval_savings_pct": round(retrieval_pct, 1),
            "compression_savings_pct": round(compression_pct, 1),
            "combined_savings_pct": round(combined_pct, 1),
            "result_files": sorted(result_files),
            "expected_files": sorted(expected),
            "hit_files": sorted(hits),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
        })

    n = len(queries)
    avg_full_file = total_full_file / n if n else 0
    avg_raw_chunks = total_raw_chunks / n if n else 0
    avg_compressed = total_compressed / n if n else 0
    avg_precision = precision_sum / n if n else 0
    avg_recall = recall_sum / n if n else 0

    # Per-layer savings (each against its own baseline, no stacking)
    retrieval_savings = (1 - total_raw_chunks / total_full_file) * 100 if total_full_file > 0 else 0
    compression_savings = (1 - total_compressed / total_raw_chunks) * 100 if total_raw_chunks > 0 else 0
    combined_savings = (1 - total_compressed / total_full_file) * 100 if total_full_file > 0 else 0

    # Grammar compression — measured on representative decision text
    sample_texts = [
        "Use JWT tokens for authentication because the legal team flagged session-based tokens.",
        "Adopted the repository pattern for database access to simplify testing.",
        "Switched from REST to GraphQL for the internal dashboard API.",
        "Chose SQLite over PostgreSQL for the local storage layer to minimize dependencies.",
        "Implemented content-hash embedding cache to skip unchanged chunks on re-index.",
    ]
    grammar_baseline = 0
    grammar_served = 0
    for text in sample_texts:
        _, b, s = grammar_compress(text)
        grammar_baseline += b
        grammar_served += s
    grammar_savings = (1 - grammar_served / grammar_baseline) * 100 if grammar_baseline > 0 else 0

    # Output compression — from advertised rates
    output_savings = ADVERTISED_PCT.get("standard", 0.65) * 100

    # Print layer summary
    print(f"\n--- Per-Layer Savings (each measured independently) ---")
    print(f"  {'Retrieval':<25} full files → chunks          {retrieval_savings:.0f}%  (measured)")
    print(f"  {'Chunk Compression':<25} chunks → signatures         {compression_savings:.0f}%  (measured)")
    print(f"  {'Output Compression':<25} Claude reply reduction      {output_savings:.0f}%  (estimated)")
    print(f"  {'Grammar':<25} prose → compressed prose     {grammar_savings:.0f}%  (measured)")
    print(f"  {'Combined (retrieval+compression)'}")
    print(f"  {'  full files → compressed':<25}                            {combined_savings:.0f}%  (measured)")

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

    layers = {
        "retrieval": {
            "baseline": total_full_file, "served": total_raw_chunks,
            "savings_pct": round(retrieval_savings, 1), "method": "measured",
        },
        "chunk_compression": {
            "baseline": total_raw_chunks, "served": total_compressed,
            "savings_pct": round(compression_savings, 1), "method": "measured",
        },
        "output_compression": {
            "savings_pct": round(output_savings, 1), "method": "estimated",
        },
        "grammar": {
            "baseline": grammar_baseline, "served": grammar_served,
            "savings_pct": round(grammar_savings, 1), "method": "measured",
        },
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
            "avg_raw_chunks_per_query": round(avg_raw_chunks),
            "avg_compressed_per_query": round(avg_compressed),
            "retrieval_savings_pct": round(retrieval_savings, 1),
            "compression_savings_pct": round(compression_savings, 1),
            "combined_savings_pct": round(combined_savings, 1),
        },
        "layers": layers,
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
        f"| Retrieval savings | **{ts['retrieval_savings_pct']}%** (full files → relevant chunks) |",
        f"| Compression savings | **{ts['compression_savings_pct']}%** (chunks → signatures) |",
        f"| Combined | **{ts['combined_savings_pct']}%** (full files → compressed chunks) |",
        f"| Avg full-file baseline | {ts['avg_full_file_per_query']:,} tokens/query |",
        f"| Avg after retrieval | {ts['avg_raw_chunks_per_query']:,} tokens/query |",
        f"| Avg after compression | {ts['avg_compressed_per_query']:,} tokens/query |",
        f"| Precision@10 | {rq['avg_precision_at_10']:.2f} |",
        f"| Recall@10 | {rq['avg_recall_at_10']:.2f} |",
        f"| Latency p50 | {lat['p50_ms']}ms |",
        f"| Queries tested | {rq['num_queries']} |",
        "",
        "## Per-Layer Savings (each measured independently)",
        "",
        "Each layer has its own baseline. These are NOT stacked.",
        "",
        "| Layer | What it does | Savings | Method |",
        "|-------|-------------|---------|--------|",
    ]

    ly = r.get("layers", {})
    layer_display = [
        ("retrieval", "Full files → relevant code chunks", "measured"),
        ("chunk_compression", "Raw chunks → signatures + docstrings", "measured"),
        ("output_compression", "Reduces Claude's reply length", "estimated"),
        ("grammar", "Drops articles/fillers from memory text", "measured"),
    ]
    for key, desc, method in layer_display:
        layer = ly.get(key, {})
        pct = layer.get("savings_pct", 0)
        lines.append(f"| **{key.replace('_', ' ').title()}** | {desc} | {pct:.0f}% | {method} |")

    lines.extend([
        "",
        "## Token Flow",
        "",
        "```",
        f"Full files (avg):      {ts['avg_full_file_per_query']:,} tokens",
        f"  → After retrieval:   {ts['avg_raw_chunks_per_query']:,} tokens  ({ts['retrieval_savings_pct']}% saved)",
        f"  → After compression: {ts['avg_compressed_per_query']:,} tokens  ({ts['compression_savings_pct']}% more saved)",
        f"Combined savings:      {ts['combined_savings_pct']}%",
        "```",
        "",
        "## Per-Query Results",
        "",
        "| Query | Full file | Chunks | Compressed | Retrieval | Compression | P@10 | R@10 |",
        "|-------|-----------|--------|------------|-----------|-------------|------|------|",
    ])
    for q in r["queries"]:
        query_text = q["query"][:40]
        lines.append(
            f"| {query_text} | {q['full_file_tokens']:,} | "
            f"{q.get('raw_chunk_tokens', 0):,} | "
            f"{q.get('compressed_tokens', 0):,} | "
            f"{q.get('retrieval_savings_pct', 0):.0f}% | "
            f"{q.get('compression_savings_pct', 0):.0f}% | "
            f"{q['precision']:.2f} | {q['recall']:.2f} |"
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
        print(f"\n{'='*64}")
        print(f"  BENCHMARK RESULTS: {results['project']}")
        print(f"{'='*64}")
        print(f"  Retrieval:     {ts['retrieval_savings_pct']}%  "
              f"({ts['avg_full_file_per_query']:,} → {ts['avg_raw_chunks_per_query']:,} tokens/query)")
        print(f"  Compression:   {ts['compression_savings_pct']}%  "
              f"({ts['avg_raw_chunks_per_query']:,} → {ts['avg_compressed_per_query']:,} tokens/query)")
        print(f"  Combined:      {ts['combined_savings_pct']}%  "
              f"({ts['avg_full_file_per_query']:,} → {ts['avg_compressed_per_query']:,} tokens/query)")
        print(f"  Precision@10:  {rq['avg_precision_at_10']:.2f}")
        print(f"  Recall@10:     {rq['avg_recall_at_10']:.2f}")
        print(f"  Latency p50:   {lat['p50_ms']}ms")
        print(f"{'='*64}")

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
