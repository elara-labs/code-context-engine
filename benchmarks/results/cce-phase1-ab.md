# CCE Phase 1 A/B Benchmark: Retrieval Precision Tuning

**Date:** 2026-07-03
**Repo:** code-context-engine (this repo, indexed from `.`)
**Branch:** fix/review-findings-2026-07-03
**Index:** 4,162 chunks from 285 files
**Queries:** 8 (from `benchmarks/sample_queries.json`)

## Winning Defaults

| Parameter | Old Default | New Default | Effect |
|-----------|------------|-------------|--------|
| `retrieval_confidence_threshold` | 0.2 | **0.2** (unchanged) | No effect — all scores ≥ 0.70 on this corpus |
| `retrieval_marginal_ratio` | 0.5 | **0.75** | Achieved 26.1% token reduction |

## Aggregate Results (Winning Config)

| Metric | Baseline | Tuned (threshold=0.2, marginal_ratio=0.75) |
|--------|----------|---------------------------------------------|
| Total tokens served | 32,138 | 23,741 |
| Token reduction | — | **26.1%** ✓ (gate: ≥25%) |
| Hit rate | 4/8 | **4/8** ✓ (gate: ≥ baseline) |

**Gate verdict: PASS** — 26.1% reduction ≥ 25%, hit rate unchanged.

## Per-Query Detail (Winning Run)

| Query | Base tokens | Tuned tokens | Base chunks | Tuned chunks | Hit |
|-------|------------|--------------|-------------|--------------|-----|
| How does the chunker split code into chunks? | 2,533 | 2,319 | 10 | 7 | True → True |
| vector search implementation | 4,600 | 3,233 | 10 | 8 | False → False |
| confidence scoring formula | 3,359 | 3,359 | 10 | 10 | False → False |
| MCP server tools | 4,221 | 4,221 | 10 | 10 | True → True |
| how does indexing pipeline work | 6,331 | 6,008 | 10 | 9 | True → True |
| FTS5 full text search | 4,321 | 736 | 10 | 2 | False → False |
| graph neighbors query | 2,059 | 1,123 | 10 | 6 | False → False |
| git commit history indexing | 4,714 | 2,742 | 10 | 6 | True → True |

## All Runs (Including Rejected)

| Run | threshold | marginal_ratio | Tokens: base → tuned | Reduction | Hits | Gate |
|-----|-----------|----------------|----------------------|-----------|------|------|
| 1 | 0.35 | 0.50 | 32,026 → 32,026 | 0.0% | 4/8 → 4/8 | FAIL (reduction < 25%) |
| 2 | 0.35 | 0.60 | 32,138 → 32,138 | 0.0% | 4/8 → 4/8 | FAIL (reduction < 25%) |
| 3 | 0.35 | 0.70 | 32,138 → 30,482 | 5.2% | 4/8 → 4/8 | FAIL (reduction < 25%) |
| 4 | 0.35 | 0.75 | 32,138 → 23,741 | 26.1% | 4/8 → 4/8 | PASS |
| 5 | 0.35 | 0.80 | 32,138 → 20,061 | 37.6% | 4/8 → 4/8 | PASS (higher reduction, but 0.75 preferred as minimum-passing) |
| **6 (winner)** | **0.2** | **0.75** | **32,138 → 23,741** | **26.1%** | **4/8 → 4/8** | **PASS** |

### Notes on Tuning

- The `confidence_threshold` parameter (0.35, then 0.2) had **no effect** on this corpus: all retrieved chunk confidence scores fell in the 0.70–0.95 range, well above any tested threshold. The ladder's instruction to use `--threshold 0.25` or `--threshold 0.2` when hit rate drops was not needed — hits were stable throughout.
- Reduction came **entirely from `marginal_ratio`**: this parameter stops adding chunks whose score falls below `ratio × top_score`. With scores clustered at the top (spread ≈ 0.25), a ratio of 0.75 is required to create meaningful cuts.
- `marginal_ratio=0.75` is the minimum value that clears the 25% gate. `0.80` provides more reduction (37.6%) with the same hit rate, but `0.75` was chosen as the conservative minimum-passing value.
- The brief's tuning ladder (tries 0.5, 0.6) did not cover the actual operating range of this corpus (needed 0.75+). Extended ladder runs (0.70, 0.75, 0.80) were added to find the gate-passing threshold.

### Limitations of this evidence

- **The 0.75 default sits on a cliff, not a plateau.** The reduction curve is
  0.70 → 5.2%, 0.75 → 26.1%, 0.80 → 37.6% — a 21-point jump across one step.
  Because the marginal stop cuts relative to the corpus's score distribution,
  a project whose score floor sits slightly higher (e.g. 0.77 instead of
  0.70) would see little or no reduction at 0.75, and one with a wider spread
  could see much more. Treat 0.75 as a starting default, not stable guidance;
  it is tunable per project via `retrieval.marginal_ratio`.
- **Sample size: 8 queries, one corpus (this repo).** The gate verdict is
  real but narrow. Re-validate on at least one external corpus (the existing
  `fastapi`/`chi`/`fiber` query sets) before citing these numbers in docs or
  README.
- Run 1's baseline total (32,026) differs from later runs (32,138) by 112
  tokens — the index was rebuilt between runs 1 and 2. The winning run's
  numbers are internally consistent (per-row sums verified).

## Config Changes Applied

- `src/context_engine/config.py`: `retrieval_marginal_ratio` updated `0.5 → 0.75`
- `tests/test_config.py`: `test_marginal_ratio_default` updated to assert `0.75`
- `retrieval_confidence_threshold` left at `0.2` (unchanged — threshold had no effect)
