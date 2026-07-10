# Token-Savings Program — Design

**Date:** 2026-07-03
**Status:** Approved direction (all four phases), pending spec review
**Goal:** Make CCE measurably better at saving user tokens and reducing model usage — and make that value visible and credible to users.

## Problem

The 2026-07-03 full-project review found that CCE's core value proposition leaks in
four places:

1. **Retrieval wastes tokens.** The retriever packs the token budget full regardless
   of chunk quality; overlapping chunks duplicate content; recency scoring is inert
   (chunk metadata never persisted); ranking bugs (fixed separately on
   `fix/review-findings-2026-07-03`) let keyword-incidental chunks outrank semantic hits.
2. **Savings numbers aren't trustworthy.** The ledger inflates on compression
   retries, `saved_pct` can go negative in one code path, and two endpoints compute
   savings differently.
3. **Compression is all-or-nothing per level.** Every served chunk gets the same
   treatment regardless of how confident retrieval is about it.
4. **Session startup re-explores.** The SessionStart resume exists but is not
   token-capped or signal-ranked, so models still spend exploration rounds
   re-learning the codebase.

## Non-goals

- No new ML dependencies (no cross-encoder reranker — adds latency and its own
  energy cost, contrary to the environment goal).
- No remote-backend work (`RemoteBackend` is dead code; separate decision).
- No schema rework of the memory subsystem beyond what Phase 4 needs.

## Phase 1 — Retrieval precision

**Files:** `src/context_engine/retrieval/retriever.py`,
`src/context_engine/retrieval/confidence.py`,
`src/context_engine/storage/vector_store.py`, `src/context_engine/config.py`,
`benchmarks/`.

Depends on the ranking fixes from `fix/review-findings-2026-07-03` (correct
`_distance` handling, per-token FTS queries, cosine metric).

1. **Confidence cutoff.** New config `retrieval.min_confidence` (default `0.35`).
   Chunks scoring below it are dropped before token packing. The top-1 result is
   always kept (never return empty for a matching query). Overflow/`expand_chunk`
   pointers are still emitted for dropped chunks so nothing becomes unreachable.
2. **Marginal-utility stop.** New config `retrieval.marginal_ratio` (default `0.5`).
   After sorting by confidence, stop adding chunks once
   `score < marginal_ratio * top_score`. Replaces "fill the budget because it's
   there". Fixed `top_k` remains as the hard upper bound.
3. **Overlap dedup.** Two chunks from the same file whose line ranges overlap by
   more than 50% collapse to the higher-confidence one before packing.
4. **Persist `modified_ts`.** Add a dedicated `modified_ts REAL` column (not a
   generic metadata JSON blob) to the `chunks` table with a schema-version bump
   and rebuild-safe migration, populated from file mtime at indexing. This activates the existing 0.1 recency weight in
   `ConfidenceScorer` which currently always returns the neutral 0.5.
5. **Benchmark harness.** `benchmarks/retrieval_bench.py`: replay a committed query
   set (~30 queries with expected-file labels, drawn from this repo itself) against
   the index; report per-query and aggregate: tokens served, hit@k (expected file
   present), and chunks served. Run before/after each phase; results table checked
   into `benchmarks/results/`. This is the evidence standard for every later claim.

**Success criteria:** tokens-served per query drops ≥ 25% on the benchmark set with
hit@k unchanged or better.

## Phase 2 — Honest savings + environment report

**Files:** `src/context_engine/cli.py`, `src/context_engine/dashboard/server.py`,
`src/context_engine/memory/hooks.py`, `src/context_engine/pricing.py`, docs.

1. **One baseline definition, documented in code and docs:**
   *baseline = tokens the model would have spent Reading the full files containing
   the served chunks; saved = baseline − tokens actually served.* Savings recorded
   only when a search response is actually served; never on retries, fallbacks, or
   re-compressions (ledger-integrity fixes land on the fix branch; this phase adds
   the definition and audits every `record_savings` call site against it).
2. **Clamp and unify.** All savings percentages computed by one shared helper,
   clamped to `[0, 100]`; `/api/status`, `/api/savings`, the CLI banner, and the
   badge all call it.
3. **Per-session summary.** At SessionEnd, write a one-line summary into the
   session record; `cce sessions show` and the dashboard display it:
   `saved 41,200 tokens this session (63% of baseline)`.
4. **`cce savings --report`.** Cumulative report: total tokens saved, cost saved
   (existing pricing module), and an energy/CO₂-equivalent estimate using a
   published per-token inference energy figure. Constants live in `pricing.py`
   with a source citation and are printed with the report
   (`estimates based on <source>, <year>`). Conservative rounding; labeled
   "estimate" in every surface. If the number is small, it shows small.

**Success criteria:** the same savings number appears on every surface; a
from-scratch session's reported savings can be manually reproduced from its
serve log.

## Phase 3 — Compression tiers by confidence

**Files:** `src/context_engine/retrieval/retriever.py`,
`src/context_engine/compression/compressor.py`, `src/context_engine/config.py`.

1. **Tiering rule.** Config `retrieval.tier_full` (default `0.75`) and
   `retrieval.tier_compressed` (default `0.5`): confidence ≥ `tier_full` serves
   full source; between the two serves the existing compressed form; below
   `tier_compressed` (but above Phase 1's `min_confidence`) serves a
   signature-skeleton (def/class lines + docstring first line — derivable from the
   chunker's existing structure, no LLM needed). Every non-full chunk carries its
   `expand_chunk` id — the escape hatch already exists and is documented in
   CLAUDE.md templates.
2. **Cache honesty.** Cached compressions record the producing method
   (`llm` vs `truncation`); truncation-fallback output is not persisted under the
   LLM cache key, so quality recovers automatically when Ollama comes back.
3. Tier thresholds are bypassed when the client explicitly sets
   `set_output_compression` to a fixed level (existing behavior wins).

**Success criteria:** additional ≥ 15% tokens-served reduction on the benchmark
set with hit@k unchanged; `expand_chunk` usage observed in real sessions stays
below ~1 in 5 queries (if models constantly expand, tiers are too aggressive —
thresholds are config, so tuning is cheap).

## Phase 4 — Smarter session-start briefs

**Files:** `src/context_engine/memory/hooks.py` (resume builder),
`src/context_engine/memory/db.py` (queries only).

Depends on the memory-desync fixes (turn-summary triggers, vec bootstrap) from the
fix branch.

1. **Hard token cap** on the SessionStart resume (config `memory.brief_tokens`,
   default `500`), enforced by the same tokenizer used for savings accounting.
2. **Signal ranking within the cap:** binding decisions first, then recent
   decisions (deduped), then hot files (code_areas touched in the last N sessions,
   ranked by recency × frequency), then an unfinished-work marker (last session's
   final turn summary if it ended mid-task). Grammar `expand()` is not applied to
   user-authored text (mangle bug fixed on the fix branch).
3. **Measurement:** log brief size and, per session, the count of
   `context_search`/Read calls in the first 5 turns — compare across sessions with
   briefs on/off to estimate exploration rounds saved.

**Success criteria:** brief ≤ cap in 100% of sessions; measurable drop in
first-5-turn exploration calls on this repo.

## Sequencing and delivery

Each phase is one branch/PR, in order 1 → 2 → 3 → 4, each carrying its
before/after benchmark table in the PR description. Phase 1's harness merges
first and gates the rest. All work builds on top of
`fix/review-findings-2026-07-03`.

## Error handling

- Cutoffs/tiers degrade toward current behavior: config values of `0` disable
  each mechanism.
- Migration for `modified_ts` must be forward-only and tolerate old rows
  (NULL → neutral recency 0.5, exactly today's behavior).
- The environment estimate must never block `cce savings` — pricing fetch
  failures fall back to static constants (existing pattern).

## Testing

- Unit tests per mechanism (cutoff, marginal stop, dedup, tier selection, clamp
  helper, brief cap/ranking).
- The benchmark harness doubles as the integration test for Phases 1 and 3.
- Savings reproducibility test: synthetic serve log → assert reported totals.
