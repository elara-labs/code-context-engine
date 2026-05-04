# Benchmark: fiber

**Date:** 2026-05-04
**Project:** fiber (396 files, 999,439 tokens)
**Index:** 4382 chunks from 387 files in 212.2s

## Results Summary

| Metric | Value |
|--------|-------|
| Retrieval savings | **93.3%** (full files → relevant chunks) |
| Compression savings | **82.7%** (chunks → signatures) |
| Combined | **98.8%** (full files → compressed chunks) |
| Avg full-file baseline | 51,397 tokens/query |
| Avg after retrieval | 3,453 tokens/query |
| Avg after compression | 599 tokens/query |
| Precision@10 | 0.03 |
| Recall@10 | 0.07 |
| Latency p50 | 1.8ms |
| Queries tested | 20 |

## Per-Layer Savings (each measured independently)

Each layer has its own baseline. These are NOT stacked.

| Layer | What it does | Savings | Method |
|-------|-------------|---------|--------|
| **Retrieval** | Full files → relevant code chunks | 93% | measured |
| **Chunk Compression** | Raw chunks → signatures + docstrings | 83% | measured |
| **Output Compression** | Reduces Claude's reply length | 65% | estimated |
| **Grammar** | Drops articles/fillers from memory text | 13% | measured |

## Token Flow

```
Full files (avg):      51,397 tokens
  → After retrieval:   3,453 tokens  (93.3% saved)
  → After compression: 599 tokens  (82.7% more saved)
Combined savings:      98.8%
```

## Per-Query Results

| Query | Full file | Chunks | Compressed | Retrieval | Compression | P@10 | R@10 |
|-------|-----------|--------|------------|-----------|-------------|------|------|
| How does fiber's App struct initialize a | 20,763 | 2,876 | 489 | 86% | 83% | 0.00 | 0.00 |
| How does fiber implement its Ctx (contex | 59,080 | 3,065 | 638 | 95% | 79% | 0.00 | 0.00 |
| How does fiber handle route registration | 32,494 | 3,266 | 577 | 90% | 82% | 0.17 | 0.50 |
| How does fiber's router match incoming r | 50,651 | 2,927 | 549 | 94% | 81% | 0.00 | 0.00 |
| How does fiber implement middleware chai | 41,434 | 5,095 | 629 | 88% | 88% | 0.00 | 0.00 |
| How does fiber handle path parameters an | 65,879 | 2,190 | 514 | 97% | 76% | 0.00 | 0.00 |
| How does fiber implement request body pa | 78,943 | 5,313 | 650 | 93% | 88% | 0.00 | 0.00 |
| How does fiber's error handling and Erro | 46,136 | 1,112 | 555 | 98% | 50% | 0.00 | 0.00 |
| How does fiber implement route groups wi | 56,133 | 1,089 | 611 | 98% | 44% | 0.33 | 1.00 |
| How does the fiber Logger middleware log | 19,620 | 4,898 | 716 | 75% | 85% | 0.00 | 0.00 |
| How does fiber implement static file ser | 39,628 | 2,448 | 541 | 94% | 78% | 0.00 | 0.00 |
| How does fiber's Recover middleware hand | 53,773 | 3,419 | 675 | 94% | 80% | 0.00 | 0.00 |
| How does fiber implement WebSocket suppo | 26,279 | 4,149 | 576 | 84% | 86% | 0.00 | 0.00 |
| How does fiber's CORS middleware handle  | 52,916 | 5,198 | 687 | 90% | 87% | 0.00 | 0.00 |
| How does fiber implement rate limiting m | 64,420 | 3,575 | 692 | 94% | 81% | 0.00 | 0.00 |
| How does fiber use fasthttp under the ho | 72,654 | 4,621 | 637 | 94% | 86% | 0.00 | 0.00 |
| How does fiber implement hooks for appli | 68,361 | 2,407 | 558 | 96% | 77% | 0.00 | 0.00 |
| How does fiber's compress middleware han | 92,985 | 3,758 | 701 | 96% | 81% | 0.00 | 0.00 |
| How does fiber implement Server-Sent Eve | 34,036 | 3,780 | 559 | 89% | 85% | 0.00 | 0.00 |
| How does fiber's cache middleware store  | 51,760 | 3,868 | 426 | 92% | 89% | 0.00 | 0.00 |

## Go-Specific Observations

**Three-way comparison: FastAPI (Python) vs chi (Go) vs fiber (Go monorepo)**

| Metric | FastAPI | chi | fiber |
|--------|---------|-----|-------|
| Files | 53 | 94 | 396 |
| Total tokens | 179,794 | 87,817 | 999,439 |
| Retrieval savings | 94.1% | 75.7% | 93.3% |
| Combined savings | 99.4% | 96.5% | 98.8% |
| Recall@10 | 0.90 | 0.67 | 0.07 |
| Latency p50 | 0.4ms | 0.3ms | 1.8ms |

**Token savings scale with repo size:**
Fiber is 11x larger than chi (~1M tokens vs 87K). The larger the repo, the more CCE has to cut — retrieval savings jump back up to 93.3% and combined savings reach 98.8%, on par with FastAPI.

**Recall degrades in monorepos:**
Fiber packages all middleware inside the same repo (`middleware/logger/`, `middleware/cors/`, `middleware/cache/`, etc.) across 396 files and 4,382 chunks. With such a large search space, the retriever's top-10 chunks get diluted — the specific middleware file a query targets rarely surfaces in the top results. Only 2 of 20 queries found the expected files (Recall@10 = 0.07).

Chi, by contrast, has dedicated one-file-per-feature middleware files in a flat structure — queries targeting those files all scored R=1.00.

**Latency scales with index size:**
Searching 4,382 chunks (fiber) takes 1.8ms p50 vs 0.3ms for chi's 429 chunks — still fast in absolute terms, but a 6x increase worth tracking as repos grow larger.

**Suggested improvement:**
Monorepo recall could be improved by scoping retrieval to a subdirectory when the query context implies it (e.g. restrict search to `middleware/cors/` for a CORS query). This is a known limitation of flat vector search across large codebases.

## How to Reproduce

```bash
uv run python benchmarks/run_benchmark.py \
  --repo https://github.com/gofiber/fiber.git \
  --source-dir . \
  --queries benchmarks/fiber_queries.json \
  --output benchmarks/results/fiber.md \
  --json-output benchmarks/results/fiber.json
```

Results generated by CCE benchmark suite on 2026-05-04.
