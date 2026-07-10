# Retrieval Precision (Token-Savings Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut tokens-served per `context_search` query ≥25% (benchmark-measured, hit-rate unchanged) via overlap dedup, a marginal-utility stop, an activated recency signal, and evidence-tuned confidence cutoff.

**Architecture:** All ranking changes live in `HybridRetriever.retrieve()`; the recency signal requires persisting `modified_ts` through the vector store (schema + row mapping) and stamping it in the indexing pipeline. Measurement extends the existing `benchmarks/run_benchmark.py` with an A/B mode that runs each query with old vs new retrieval parameters against the same index.

**Tech Stack:** Python 3.12+, sqlite-vec, pytest (run via `uv run --no-sync pytest`), existing benchmark harness.

## Global Constraints

- Spec: `docs/specs/2026-07-03-token-savings-design.md` (Phase 1 section).
- Branch: work on `fix/review-findings-2026-07-03` (Phase 1 builds on the review fixes).
- Config values of `0` disable each new mechanism (spec "Error handling").
- `modified_ts` migration is forward-only; old rows read as NULL → recency stays neutral 0.5 (today's behavior).
- Run tests with `uv run --no-sync pytest ...` — plain `uv run` re-syncs a stale wheel in this venv.
- No new dependencies.
- Do NOT add "Co-Authored-By" lines to commits.

---

### Task 1: Persist `modified_ts` through the vector store

**Files:**
- Modify: `src/context_engine/storage/vector_store.py` (`_ensure_tables` ~line 70, `_chunk_to_row` ~line 155, `_row_to_chunk` ~line 165, `ingest` ~line 179, `search` ~line 220, `get_by_id` ~line 373, `get_chunks_by_ids` ~line 388)
- Test: `tests/storage/test_vector_store.py`

**Interfaces:**
- Consumes: existing `Chunk.metadata` dict (`context_engine/models.py:60`).
- Produces: chunks returned by `search`/`get_by_id`/`get_chunks_by_ids` carry `chunk.metadata["modified_ts"]` (float epoch seconds) when the stored column is non-NULL. Task 2 writes it; Task 5 measures its effect. `ConfidenceScorer._recency_score` (`retrieval/confidence.py:40-47`) already consumes it — no scorer change needed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/storage/test_vector_store.py` (follow the file's existing fixture pattern for constructing a store and chunks — reuse its helper if one exists):

```python
import sqlite3
import time

import pytest

from context_engine.models import Chunk, ChunkType
from context_engine.storage.vector_store import VectorStore


def _mk_chunk(cid: str, mtime: float | None = None) -> Chunk:
    c = Chunk(
        id=cid,
        content=f"def {cid}():\n    pass\n",
        chunk_type=ChunkType.FUNCTION,
        file_path="src/mod.py",
        start_line=1,
        end_line=2,
        language="python",
        embedding=[0.1, 0.2, 0.3],
    )
    if mtime is not None:
        c.metadata["modified_ts"] = mtime
    return c


@pytest.mark.asyncio
async def test_modified_ts_round_trips(tmp_path):
    store = VectorStore(db_path=str(tmp_path))
    now = time.time()
    await store.ingest([_mk_chunk("with_ts", mtime=now)])

    results = await store.search([0.1, 0.2, 0.3], top_k=1)
    assert results, "expected one search hit"
    assert results[0].metadata["modified_ts"] == pytest.approx(now)

    by_id = await store.get_by_id("with_ts")
    assert by_id.metadata["modified_ts"] == pytest.approx(now)

    by_ids = await store.get_chunks_by_ids(["with_ts"])
    assert by_ids[0].metadata["modified_ts"] == pytest.approx(now)


@pytest.mark.asyncio
async def test_modified_ts_absent_stays_absent(tmp_path):
    store = VectorStore(db_path=str(tmp_path))
    await store.ingest([_mk_chunk("no_ts")])
    results = await store.search([0.1, 0.2, 0.3], top_k=1)
    assert "modified_ts" not in results[0].metadata


@pytest.mark.asyncio
async def test_legacy_db_without_column_is_migrated(tmp_path):
    # Simulate a pre-Phase-1 DB: create the store, then drop the column
    # by rebuilding the table without it, then reopen.
    store = VectorStore(db_path=str(tmp_path))
    await store.ingest([_mk_chunk("old_row")])
    conn = sqlite3.connect(str(tmp_path / "vectors.db"))
    conn.executescript(
        """
        CREATE TABLE chunks_old AS
            SELECT id, content, chunk_type, file_path, start_line, end_line, language
            FROM chunks;
        DROP TABLE chunks;
        ALTER TABLE chunks_old RENAME TO chunks;
        """
    )
    conn.commit()
    conn.close()

    reopened = VectorStore(db_path=str(tmp_path))  # must not raise
    row = await reopened.get_by_id("old_row")
    assert row is not None
    assert "modified_ts" not in row.metadata  # NULL column → neutral recency
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/storage/test_vector_store.py -q -k "modified_ts or legacy_db"`
Expected: FAIL — `KeyError: 'modified_ts'` (round-trip) and/or `sqlite3.OperationalError` (legacy reopen may pass; the two metadata tests must fail).

- [ ] **Step 3: Implement**

In `src/context_engine/storage/vector_store.py`:

1. `_ensure_tables` — add the column to the CREATE and migrate legacy tables. Replace the `CREATE TABLE IF NOT EXISTS chunks (...)` statement with:

```python
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    modified_ts REAL
                )
            """)
            # Forward-only migration: pre-Phase-1 DBs lack modified_ts.
            # Old rows stay NULL → ConfidenceScorer keeps neutral recency.
            cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(chunks)")
            }
            if "modified_ts" not in cols:
                self._conn.execute(
                    "ALTER TABLE chunks ADD COLUMN modified_ts REAL"
                )
```

2. `_chunk_to_row` — append the value:

```python
    def _chunk_to_row(self, chunk: Chunk) -> tuple:
        content = chunk.content
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n...[truncated]"
        return (
            chunk.id, content, chunk.chunk_type.value,
            chunk.file_path, chunk.start_line, chunk.end_line,
            chunk.language, chunk.metadata.get("modified_ts"),
        )
```

3. `_row_to_chunk` — restore it (row layout: 7 base columns, optional 8th `modified_ts`):

```python
    def _row_to_chunk(self, row, distance: float | None = None) -> Chunk:
        chunk = Chunk(
            id=row[0],
            content=row[1],
            chunk_type=ChunkType(row[2]),
            file_path=row[3],
            start_line=row[4],
            end_line=row[5],
            language=row[6],
        )
        if len(row) > 7 and row[7] is not None:
            chunk.metadata["modified_ts"] = row[7]
        if distance is not None:
            chunk.metadata["_distance"] = distance
        return chunk
```

4. `ingest` — extend the upsert (now 8 placeholders):

```python
                    rowid = cursor.execute(
                        "INSERT INTO chunks "
                        "(id, content, chunk_type, file_path, start_line, end_line, language, modified_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "content = excluded.content, "
                        "chunk_type = excluded.chunk_type, "
                        "file_path = excluded.file_path, "
                        "start_line = excluded.start_line, "
                        "end_line = excluded.end_line, "
                        "language = excluded.language, "
                        "modified_ts = excluded.modified_ts "
                        "RETURNING rowid",
                        row,
                    ).fetchone()[0]
```

5. `search` — both SELECTs gain `c.modified_ts` before `v.distance`:

```sql
                        SELECT c.id, c.content, c.chunk_type, c.file_path,
                               c.start_line, c.end_line, c.language,
                               c.modified_ts, v.distance
```

and the return line becomes:

```python
        return [self._row_to_chunk(row[:8], distance=row[8]) for row in rows]
```

6. `get_by_id` and `get_chunks_by_ids` — SELECT list gains `, modified_ts` after `language` (no other change; `_row_to_chunk` handles the 8th column).

- [ ] **Step 4: Run the storage suite**

Run: `uv run --no-sync pytest tests/storage/test_vector_store.py -q`
Expected: PASS (all, including the pre-existing cosine/rollback tests).

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/storage/vector_store.py tests/storage/test_vector_store.py
git commit -m "feat(retrieval): persist chunk modified_ts through vector store"
```

---

### Task 2: Stamp `modified_ts` in the indexing pipeline

**Files:**
- Modify: `src/context_engine/indexer/pipeline.py` (~line 628, right after `chunks, imported_modules = chunk_outcome` inside the `for (file_path, rel_path, content, content_hash, language), chunk_outcome in zip(...)` loop)
- Test: `tests/indexer/test_pipeline_modified_ts.py` (create)

**Interfaces:**
- Consumes: Task 1's column (transparent — pipeline only sets `chunk.metadata["modified_ts"]`; the store persists it).
- Produces: every chunk ingested by `run_indexing` carries `metadata["modified_ts"] == source file's st_mtime`. This is what makes `ConfidenceScorer._recency_score` return a real decay value instead of the neutral 0.5.

- [ ] **Step 1: Write the failing test**

Create `tests/indexer/test_pipeline_modified_ts.py` (mirror the fixture style of `tests/indexer/test_pipeline_target_path.py`, which already builds a minimal project + storage dir and calls `run_indexing`; reuse its config/fixture helpers rather than inventing new ones):

```python
import asyncio
import os
from pathlib import Path

import pytest

from context_engine.config import Config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend


@pytest.mark.asyncio
async def test_indexed_chunks_carry_file_mtime(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = project / "app.py"
    src.write_text("def handler():\n    return 42\n")
    known_mtime = 1_700_000_000.0
    os.utime(src, (known_mtime, known_mtime))

    storage = tmp_path / "storage"
    config = Config()
    config.storage_path = str(storage)

    result = await run_indexing(config, project, full=True)
    assert not result.errors

    backend = LocalBackend(base_path=str(storage / project.name))
    chunks = await backend.get_chunks_by_ids(
        [cid for cid in await _all_chunk_ids(backend)]
    )
    assert chunks, "expected indexed chunks"
    for c in chunks:
        assert c.metadata.get("modified_ts") == pytest.approx(known_mtime)


async def _all_chunk_ids(backend) -> list[str]:
    # LocalBackend exposes the vector store; list ids straight from the table.
    store = backend._vector_store
    with store._lock:
        rows = store._conn.execute("SELECT id FROM chunks").fetchall()
    return [r[0] for r in rows]
```

(If `LocalBackend`'s attribute is named differently — check `src/context_engine/storage/local_backend.py` for the vector-store attribute name and use that. If the existing pipeline tests construct `Config`/storage differently, follow them.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/indexer/test_pipeline_modified_ts.py -q`
Expected: FAIL — `modified_ts` is None/missing.

- [ ] **Step 3: Implement**

In `src/context_engine/indexer/pipeline.py`, immediately after `chunks, imported_modules = chunk_outcome` (~line 628):

```python
                    # Stamp source mtime so retrieval's recency weight has
                    # real signal (spec: Phase 1 item 4). stat() failure is
                    # non-fatal — chunks just keep neutral recency.
                    try:
                        _mtime = file_path.stat().st_mtime
                    except OSError:
                        _mtime = None
                    if _mtime is not None:
                        for _c in chunks:
                            _c.metadata["modified_ts"] = _mtime
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/indexer/test_pipeline_modified_ts.py tests/indexer -q`
Expected: PASS (new test + full indexer suite).

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/indexer/pipeline.py tests/indexer/test_pipeline_modified_ts.py
git commit -m "feat(indexer): stamp chunks with source file mtime for recency scoring"
```

---

### Task 3: Overlap dedup in the retriever

**Files:**
- Modify: `src/context_engine/retrieval/retriever.py` (new static method + one call after `scored.sort(...)` at line 150)
- Test: `tests/retrieval/test_retriever.py`

**Interfaces:**
- Consumes: `scored: list[tuple[Chunk, float]]` sorted descending (existing local in `retrieve`).
- Produces: `HybridRetriever._dedupe_overlaps(scored) -> list[tuple[Chunk, float]]` — same-file chunks whose line ranges overlap >50% of the shorter chunk collapse to the higher-scored one. Task 4 iterates its output.

- [ ] **Step 1: Write the failing tests**

Append to `tests/retrieval/test_retriever.py` (the file already has a `_mk_chunk`-style helper and stub-backend fixtures from the scoring-fix work — reuse them; the dedup tests below only need the static method, no backend):

```python
from context_engine.models import Chunk, ChunkType
from context_engine.retrieval.retriever import HybridRetriever


def _chunk_at(cid, fp, start, end):
    return Chunk(
        id=cid, content="x", chunk_type=ChunkType.FUNCTION,
        file_path=fp, start_line=start, end_line=end, language="python",
    )


def test_dedupe_overlaps_collapses_majority_overlap():
    scored = [
        (_chunk_at("a", "src/m.py", 10, 30), 0.9),   # kept (highest)
        (_chunk_at("b", "src/m.py", 12, 28), 0.7),   # 17/17 lines inside a → dropped
        (_chunk_at("c", "src/m.py", 29, 60), 0.6),   # 2/32 overlap → kept
        (_chunk_at("d", "src/other.py", 10, 30), 0.5),  # other file → kept
    ]
    kept = HybridRetriever._dedupe_overlaps(scored)
    assert [c.id for c, _ in kept] == ["a", "c", "d"]


def test_dedupe_overlaps_keeps_disjoint_ranges():
    scored = [
        (_chunk_at("a", "src/m.py", 1, 10), 0.9),
        (_chunk_at("b", "src/m.py", 11, 20), 0.8),
    ]
    kept = HybridRetriever._dedupe_overlaps(scored)
    assert len(kept) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/retrieval/test_retriever.py -q -k dedupe`
Expected: FAIL with `AttributeError: ... has no attribute '_dedupe_overlaps'`.

- [ ] **Step 3: Implement**

In `src/context_engine/retrieval/retriever.py`, add below `_apply_path_penalty`:

```python
    @staticmethod
    def _dedupe_overlaps(
        scored: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """Collapse same-file chunks whose line ranges overlap by more than
        half of the shorter chunk, keeping the higher-scored one. Input must
        be sorted by score descending; earlier (better) entries win.
        Candidate sets are small (≤ top_k*3 per source), so O(n²) is fine.
        """
        kept: list[tuple[Chunk, float]] = []
        for chunk, score in scored:
            duplicate = False
            for kept_chunk, _ in kept:
                if kept_chunk.file_path != chunk.file_path:
                    continue
                overlap = (
                    min(chunk.end_line, kept_chunk.end_line)
                    - max(chunk.start_line, kept_chunk.start_line)
                    + 1
                )
                if overlap <= 0:
                    continue
                shorter = min(
                    chunk.end_line - chunk.start_line + 1,
                    kept_chunk.end_line - kept_chunk.start_line + 1,
                )
                if shorter > 0 and overlap / shorter > 0.5:
                    duplicate = True
                    break
            if not duplicate:
                kept.append((chunk, score))
        return kept
```

Then wire it in `retrieve()` — directly after `scored.sort(key=lambda x: x[1], reverse=True)`:

```python
        scored = self._dedupe_overlaps(scored)
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/retrieval -q`
Expected: PASS (new + existing retriever tests).

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/retrieval/retriever.py tests/retrieval/test_retriever.py
git commit -m "feat(retrieval): collapse overlapping same-file chunks before packing"
```

---

### Task 4: Marginal-utility stop + top-1 guarantee + config plumbing

**Files:**
- Modify: `src/context_engine/config.py` (dataclass field after `retrieval_top_k` line 70, `_EXPECTED_TYPES` line 131, `_apply_dict_to_config` mapping line 156)
- Modify: `src/context_engine/retrieval/retriever.py` (`retrieve()` signature line 37, threshold filter ~line 147, diversity loop ~line 155)
- Modify: `src/context_engine/integration/mcp_server.py` (the `retrieve(...)` call at line 975)
- Test: `tests/retrieval/test_retriever.py`, `tests/test_config.py` (or wherever config mapping tests live — `grep -rl "retrieval_confidence_threshold" tests/` to find it)

**Interfaces:**
- Consumes: Task 3's deduped `scored` list.
- Produces: `HybridRetriever.retrieve(query, top_k=10, confidence_threshold=0.0, max_tokens=None, marginal_ratio=0.0)` — new keyword arg, `0.0` = disabled (current behavior). `Config.retrieval_marginal_ratio: float = 0.5`, YAML key `retrieval.marginal_ratio`. Behavior: (a) once at least one chunk is selected, stop selecting when `score < marginal_ratio * top_score`; (b) if the `confidence_threshold` filter empties the candidate set but candidates existed, the single best candidate is returned anyway (top-1 guarantee, spec Phase 1 item 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/retrieval/test_retriever.py`. These need the stub backend/embedder used by the existing FTS-distance tests in this file — reuse that fixture; the sketch below assumes a helper `make_retriever(vector_results=...)` exists or is trivially added from the existing stubs:

```python
import pytest


@pytest.mark.asyncio
async def test_marginal_ratio_stops_low_value_tail(retriever_factory):
    # Three vector hits with confidences ~[high, high, low] once scored.
    # With marginal_ratio=0.5 the third (score < 0.5 * top) is dropped.
    retriever, chunks = retriever_factory(
        distances=[0.1, 0.3, 1.8],  # → vector scores 0.95, 0.85, 0.1
    )
    results = await retriever.retrieve("query", top_k=10, marginal_ratio=0.5)
    assert len(results) == 2

    all_results = await retriever.retrieve("query", top_k=10, marginal_ratio=0.0)
    assert len(all_results) == 3  # 0 disables the stop


@pytest.mark.asyncio
async def test_top1_guarantee_when_threshold_filters_everything(retriever_factory):
    retriever, chunks = retriever_factory(distances=[1.6, 1.8])
    results = await retriever.retrieve(
        "query", top_k=10, confidence_threshold=0.99
    )
    assert len(results) == 1  # best candidate survives an over-tight threshold
```

(`retriever_factory` is whatever this test file's existing stub pattern provides — adapt names to the file. The distances → score mapping above is illustrative; assert on relative counts, not absolute scores.)

Config test (in the file found by `grep -rl "retrieval_confidence_threshold" tests/`):

```python
def test_marginal_ratio_config_mapping(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("retrieval:\n  marginal_ratio: 0.7\n")
    from context_engine.config import load_config
    config = load_config(global_path=cfg_file)
    assert config.retrieval_marginal_ratio == 0.7


def test_marginal_ratio_default():
    from context_engine.config import Config
    assert Config().retrieval_marginal_ratio == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/retrieval/test_retriever.py -q -k "marginal or top1" && uv run --no-sync pytest tests/ -q -k marginal_ratio_config`
Expected: FAIL — `TypeError: retrieve() got an unexpected keyword argument 'marginal_ratio'` / `AttributeError: retrieval_marginal_ratio`.

- [ ] **Step 3: Implement**

`src/context_engine/config.py`:

```python
    # Retrieval
    retrieval_confidence_threshold: float = 0.2
    retrieval_top_k: int = 20
    # Stop adding result chunks once a chunk's score falls below this
    # fraction of the top score. 0 disables (always fill to top_k).
    retrieval_marginal_ratio: float = 0.5
    bootstrap_max_tokens: int = 10000
```

`_EXPECTED_TYPES`: add `"retrieval_marginal_ratio": (int, float),` after the `retrieval_confidence_threshold` entry.
`_apply_dict_to_config` mapping: add `("retrieval", "marginal_ratio"): "retrieval_marginal_ratio",` after the `confidence_threshold` line.

`src/context_engine/retrieval/retriever.py` — signature:

```python
    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        confidence_threshold: float = 0.0,
        max_tokens: int | None = None,
        marginal_ratio: float = 0.0,
    ) -> list[Chunk]:
```

Threshold filter + top-1 guarantee — replace the current append-if-above-threshold block (lines 147-148) and sort with:

```python
            scored.append((chunk, final_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        scored = self._dedupe_overlaps(scored)

        # Confidence cutoff with a top-1 guarantee: an over-tight threshold
        # must never turn a matching query into an empty result.
        filtered = [(c, s) for c, s in scored if s >= confidence_threshold]
        if not filtered and scored:
            filtered = scored[:1]
        scored = filtered
```

(The unconditional `scored.append` replaces the old `if final_score >= confidence_threshold:` guard inside the loop.)

Diversity loop — add the marginal stop (`scored` is sorted, so `top_score` is element 0):

```python
        top_score = scored[0][1] if scored else 0.0
        file_counts: dict[str, int] = {}
        diverse: list[Chunk] = []
        for chunk, score in scored:
            if diverse and marginal_ratio > 0 and score < marginal_ratio * top_score:
                break
            count = file_counts.get(chunk.file_path, 0)
            if count < _MAX_CHUNKS_PER_FILE:
                diverse.append(chunk)
                file_counts[chunk.file_path] = count + 1
                if len(diverse) >= top_k:
                    break
        ranked = diverse
```

`src/context_engine/integration/mcp_server.py` line 975 — add the parameter to the existing call:

```python
        all_chunks = await self._retriever.retrieve(
            ...existing args...,
            confidence_threshold=self._config.retrieval_confidence_threshold,
            marginal_ratio=self._config.retrieval_marginal_ratio,
        )
```

(Keep every existing argument as-is; only add `marginal_ratio`.)

Also in `mcp_server.py`: the spec requires that cutoff-dropped chunks stay discoverable ("nothing becomes unreachable"). `retrieve()` gains an optional `stats_out: dict | None = None` keyword; when provided it is filled with `candidates` (post-dedup, pre-filter count), `selected` (returned count), and `dropped_low_value` (candidates excluded specifically by the confidence threshold or the marginal stop — NOT by the per-file diversity cap or `top_k`). The `context_search` handler passes a stats dict and appends one compact note line to the response only when `stats["dropped_low_value"] > 0`:

```python
            note = (
                "[note: lower-confidence results omitted — raise top_k or "
                "lower retrieval.confidence_threshold to include them]"
            )
```

(Rationale: an earlier draft compared the post-compression chunk count against `retrieval_top_k`, which false-positives on nearly every query — compression and unrelated filters shrink the list too.)

Add tests: retriever `stats_out` accounting (threshold drop, marginal-stop drop, and no-drop → 0), and MCP-server note behavior (present when `dropped_low_value > 0`; absent when retrieval merely returned fewer than `retrieval_top_k` with no drops).

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/retrieval tests/integration -q && uv run --no-sync pytest tests/ -q -k "config"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/config.py src/context_engine/retrieval/retriever.py src/context_engine/integration/mcp_server.py tests/
git commit -m "feat(retrieval): marginal-utility stop with top-1 guarantee, config-driven"
```

---

### Task 5: Benchmark A/B mode + evidence-based threshold tuning

**Files:**
- Modify: `benchmarks/run_benchmark.py` (add `--ab` mode)
- Create: `benchmarks/results/cce-phase1-ab.md` (generated output, committed as evidence)
- Possibly modify: `src/context_engine/config.py` (only the `retrieval_confidence_threshold` default, only if evidence supports it)

**Interfaces:**
- Consumes: `HybridRetriever.retrieve(..., confidence_threshold=..., marginal_ratio=...)` from Task 4 — same index, two parameterizations.
- Produces: a committed A/B report; the Phase 1 success-gate numbers (spec: ≥25% tokens-served reduction, hit-rate unchanged).

- [ ] **Step 1: Add the A/B mode**

In `benchmarks/run_benchmark.py`, add CLI args and a comparison pass. Add to `main()`'s argparse block:

```python
    parser.add_argument("--ab", action="store_true",
                        help="Run each query twice: baseline retrieval vs tuned "
                             "(threshold + marginal ratio) and print the delta")
    parser.add_argument("--threshold", type=float, default=0.35,
                        help="Tuned confidence_threshold for --ab (default 0.35)")
    parser.add_argument("--marginal-ratio", type=float, default=0.5,
                        help="Tuned marginal_ratio for --ab (default 0.5)")
```

Add this function after `run_benchmark`:

```python
async def run_ab(
    project_dir: Path,
    queries: list[dict],
    storage_dir: Path,
    threshold: float,
    marginal_ratio: float,
) -> dict:
    """Index once, then run every query with baseline vs tuned retrieval
    parameters against the same index. No stacking with compression —
    this isolates the retrieval-precision change."""
    config = Config()
    config.storage_path = str(storage_dir)

    print("Indexing project once for A/B...")
    idx = await run_indexing(config, project_dir, full=True)
    print(f"  {idx.total_chunks} chunks from {len(idx.indexed_files)} files")

    storage_base = Path(config.storage_path) / project_dir.name
    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)

    rows = []
    for q in queries:
        base = await retriever.retrieve(q["query"], top_k=10)
        tuned = await retriever.retrieve(
            q["query"], top_k=10,
            confidence_threshold=threshold,
            marginal_ratio=marginal_ratio,
        )
        expected = set(q.get("expected_files", []))

        def _measure(chunks):
            files = {c.file_path for c in chunks}
            return {
                "tokens": sum(_count_tokens(c.content) for c in chunks),
                "chunks": len(chunks),
                "hit": bool(files & expected) if expected else None,
            }

        rows.append({"query": q["query"],
                     "base": _measure(base), "tuned": _measure(tuned)})
        b, t = rows[-1]["base"], rows[-1]["tuned"]
        print(f"  {q['query'][:45]:<45} tokens {b['tokens']:>6} → {t['tokens']:>6}  "
              f"chunks {b['chunks']:>2} → {t['chunks']:>2}  "
              f"hit {b['hit']} → {t['hit']}")

    def _agg(side):
        tok = sum(r[side]["tokens"] for r in rows)
        hits = sum(1 for r in rows if r[side]["hit"])
        judged = sum(1 for r in rows if r[side]["hit"] is not None)
        return tok, hits, judged

    base_tok, base_hits, judged = _agg("base")
    tuned_tok, tuned_hits, _ = _agg("tuned")
    reduction = (1 - tuned_tok / base_tok) * 100 if base_tok else 0.0
    print(f"\nTokens served: {base_tok:,} → {tuned_tok:,}  ({reduction:.1f}% reduction)")
    print(f"Hit rate: {base_hits}/{judged} → {tuned_hits}/{judged}")
    return {
        "threshold": threshold, "marginal_ratio": marginal_ratio,
        "base_tokens": base_tok, "tuned_tokens": tuned_tok,
        "reduction_pct": round(reduction, 1),
        "base_hits": base_hits, "tuned_hits": tuned_hits,
        "judged": judged, "rows": rows,
    }
```

Wire into `main()` before the normal `run_benchmark` call:

```python
    if args.ab:
        results = asyncio.run(run_ab(
            project_dir, queries, storage_dir,
            args.threshold, args.marginal_ratio,
        ))
        if args.json_output:
            Path(args.json_output).write_text(json.dumps(results, indent=2) + "\n")
        return
```

(Reuse the existing `finally` cleanup — place the `if args.ab:` branch inside the existing `try`.)

- [ ] **Step 2: Run the A/B benchmark on this repo**

Run: `uv run --no-sync python benchmarks/run_benchmark.py --ab --json-output benchmarks/results/cce-phase1-ab.json 2>&1 | tail -30`
Expected: per-query table plus aggregate lines. Success gate: `reduction_pct >= 25` AND `tuned_hits >= base_hits`.

- [ ] **Step 3: Tune if the gate fails**

- If hit rate DROPPED: retry with `--threshold 0.25`, then `--threshold 0.2` (marginal stop alone often carries the reduction). Use the highest threshold with no hit-rate loss.
- If reduction < 25% but hit rate held: try `--marginal-ratio 0.6`.
- Record every run's numbers; the final report must state which values won and what was rejected.

- [ ] **Step 4: Apply the winning default + write the report**

- If the winning threshold ≠ 0.2, update `retrieval_confidence_threshold`'s default in `src/context_engine/config.py` to the winning value (spec proposed 0.35 — evidence decides).
- Write `benchmarks/results/cce-phase1-ab.md` by hand from the JSON: date, repo, chosen defaults, the aggregate table (baseline vs tuned tokens, hit rate), and the rejected parameter values with their numbers.

- [ ] **Step 5: Full suite + commit**

Run: `uv run --no-sync pytest -q`
Expected: all pass.

```bash
git add benchmarks/run_benchmark.py benchmarks/results/cce-phase1-ab.md benchmarks/results/cce-phase1-ab.json src/context_engine/config.py
git commit -m "feat(benchmarks): A/B mode for retrieval precision; tune Phase 1 defaults from evidence"
```

---

### Task 6: Record the outcome

**Files:** none (MCP tool call + task bookkeeping)

- [ ] **Step 1:** Call `record_decision` with the final Phase 1 numbers: chosen `retrieval_confidence_threshold` and `retrieval_marginal_ratio` defaults, measured tokens-served reduction, hit-rate before/after, and a pointer to `benchmarks/results/cce-phase1-ab.md`.
- [ ] **Step 2:** Report the numbers to the user with the success-gate verdict (met / not met, and why).
