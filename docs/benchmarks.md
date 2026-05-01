# CCE Benchmarks

Benchmarked against [FastAPI](https://github.com/fastapi/fastapi) (53 files, 179,794 tokens, 425 chunks).
20 real coding queries, no cherry-picking.

## Results

| Metric | Value |
|--------|-------|
| Token savings vs full-file reads | 94.1% |
| Combined (retrieval + compression) | 99.4% |
| Recall@10 | 0.90 |
| Query latency p50 | 0.4ms |
| Queries tested | 20 |

## Per-Layer Savings

Each layer has its own baseline. These are NOT stacked.

| Layer | What it does | Savings | Method |
|-------|-------------|---------|--------|
| **Retrieval** | Full files → relevant code chunks | 94% | measured |
| **Chunk Compression** | Raw chunks → signatures + docstrings | 89% | measured |
| **Output Compression** | Reduces Claude's reply length | 65% | estimated |
| **Grammar** | Drops articles/fillers from memory text | 13% | measured |

## Methodology

Run: `python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi`

- **Token savings**: Compare full project token count vs average tokens served per query
- **Precision/Recall**: Curated queries with known-relevant files in `benchmarks/sample_queries.json`
- **Latency**: 5 iterations per query, report percentiles (after 3 warm-up runs)

Full results in [`benchmarks/results/fastapi.md`](benchmarks/results/fastapi.md).
