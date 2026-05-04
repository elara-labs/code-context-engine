# CCE v0.2 — Seven Improvements Design Spec

**Date:** 2026-04-18 (revised after design review)
**Scope:** Hybrid search, ONNX migration, token-aware packing, knowledge graph, git history search, benchmarks, performance
**Branch:** `feature/cce-v02-improvements`

---

## 0. Cross-Cutting Requirements

These apply to every feature below and were added after review surfaced correctness gaps.

### 0.1 Index schema versioning

**Modified:** `src/context_engine/indexer/manifest.py`

Add a `schema_version: int` field (start at `2`). On open:
- If stored version < current version, log a warning and force a full reindex.
- If missing, treat as version `1` (pre-v0.2 indexes).

Every v0.2 feature that changes what is persisted (FTS, graph, git chunks, ONNX
embeddings) must bump this when landed. Users should never see silent failures
from stale indexes.

### 0.2 Remote backend parity

`StorageBackend` is `@runtime_checkable`. Every new protocol method added for
v0.2 (`fts_search`, real `graph_neighbors`) must also land on
`src/context_engine/storage/remote_backend.py` — even if the remote impl is a
thin HTTP forwarder with a graceful fallback. Without this, runtime protocol
checks pass while calls fail at the first use.

### 0.3 Blocking I/O inside `async def`

LanceDB, sqlite3, `subprocess.run`, and `git` shell-outs are all blocking. Any
`async def` wrapping them must use `asyncio.to_thread(...)` (or an async-native
driver like `aiosqlite`). Do not leave sync code inside async methods — it
silently blocks the event loop and tanks concurrent MCP throughput.

---

## 1. Hybrid Search (BM25 + Vector via RRF)

### Problem
Vector-only search misses exact keyword matches (e.g., searching for `calculate_tax` may rank semantically similar but wrong functions higher). All top competitors use hybrid search.

### Design

**New file:** `src/context_engine/storage/fts_store.py`

SQLite FTS5 full-text search store. Zero new dependencies (sqlite3 is stdlib).

```python
import asyncio
import sqlite3
from context_engine.models import Chunk


def _escape_fts5(query: str) -> str:
    """FTS5 treats `"`, `:`, `(`, `)`, `-`, `*`, `^` as operators. Wrap the
    raw user input as a single phrase so it's parsed as text, not syntax."""
    return '"' + query.replace('"', '""') + '"'


class FTSStore:
    def __init__(self, db_path: str) -> None:
        # Creates SQLite DB at db_path/fts.db
        # check_same_thread=False lets us hop between threads via asyncio.to_thread
        self._conn = sqlite3.connect(
            f"{db_path}/fts.db", check_same_thread=False
        )
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(id UNINDEXED, content, file_path, language, chunk_type)"
        )

    async def ingest(self, chunks: list[Chunk]) -> None:
        await asyncio.to_thread(self._ingest_sync, chunks)

    async def search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        # Returns list of (chunk_id, bm25_score) using the FTS5 rank function.
        # SELECT id, rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?
        return await asyncio.to_thread(self._search_sync, _escape_fts5(query), top_k)

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.to_thread(
            self._conn.execute,
            "DELETE FROM chunks_fts WHERE file_path = ?", (file_path,),
        )
```

**Modified:** `src/context_engine/storage/local_backend.py`
- Initialize `FTSStore` at `base_path/fts`
- Dual-ingest: chunks go to both vector store and FTS store
- Expose `fts_search(query, top_k)` method
- `delete_by_file` deletes from both stores

**Modified:** `src/context_engine/storage/remote_backend.py`
- Implement `fts_search` — forward to a `/fts/search` endpoint.
- On `httpx.ConnectError` / `httpx.TimeoutException`, return `[]` (matches
  the existing fallback pattern in this backend).
- Required for `@runtime_checkable` protocol parity — see §0.2.

**Modified:** `src/context_engine/retrieval/retriever.py`
- After getting vector results and FTS results, merge with Reciprocal Rank Fusion:
  ```
  rrf_score(doc) = sum(1 / (k + rank_i)) for each ranking that contains doc
  ```
  where k=60 (standard constant).
- Merged results then go through existing ConfidenceScorer for final ranking.
- FTS results need chunk hydration — FTS returns IDs, fetch full chunks via
  `backend.get_chunk_by_id`.
- **Graceful fallback**: if `fts_search` raises or returns empty (old index
  without FTS table, remote backend offline), fall back to vector-only and
  log once at `warning` level.

**Modified:** `src/context_engine/storage/backend.py` (protocol)
- Add `fts_search(query: str, top_k: int) -> list[tuple[str, float]]` to StorageBackend protocol.

**Migration:** existing LanceDB indexes were built without FTS data. Bump
`schema_version` per §0.1; a mismatch forces a full reindex on next open.

### Testing
- Test FTS ingest + search returns expected chunks.
- Test FTS5 escaping: queries containing `"`, `-`, `:`, `(`, `*` do not raise and return phrase matches.
- Test RRF merging produces correct ordering when the same doc appears in both rankings.
- Test exact keyword matches rank higher than vector-only baseline (regression test against a curated query set).
- Test graceful fallback: retriever still returns vector results when FTS table is missing or empty.
- Test migration: a v1 index triggers a full reindex on first open after upgrade.

---

## 2. ONNX Runtime Migration + `uv tool install`

### Problem
PyTorch is ~2GB, Python 3.14 breaks .pth files for editable installs, and users struggle with venv management.

### Design

**Modified:** `src/context_engine/indexer/embedder.py`

Replace `SentenceTransformer` with ONNX Runtime inference. Use a
**pre-exported ONNX model** so torch is never required at runtime —
`export=True` would pull torch+transformers to do the conversion, which
defeats the install-size goal.

```python
import numpy as np
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer

from context_engine.models import Chunk

# Pre-exported ONNX variant — no torch required at install or runtime.
_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder:
    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        # file_name points at a pre-shipped ONNX weights file; no export step.
        self._model = ORTModelForFeatureExtraction.from_pretrained(
            model_name, file_name="onnx/model.onnx"
        )

    def _mean_pool(self, last_hidden_state, attention_mask):
        """Attention-masked mean pooling — matches sentence-transformers.

        Naive `last_hidden_state.mean(axis=1)` averages over padding tokens
        and drifts from sentence-transformers output. Masking keeps embeddings
        comparable so existing indexes (and benchmarks) stay valid.
        """
        mask = attention_mask[..., None].astype(np.float32)
        summed = (last_hidden_state * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        return summed / counts

    def embed(self, chunks: list[Chunk]) -> None:
        texts = [c.content for c in chunks]
        inputs = self._tokenizer(texts, padding=True, truncation=True, return_tensors="np")
        outputs = self._model(**inputs)
        embeddings = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
        embeddings = embeddings / norms
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb.tolist()

    def embed_query(self, query: str) -> list[float]:
        inputs = self._tokenizer(query, return_tensors="np", truncation=True)
        outputs = self._model(**inputs)
        emb = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"])[0]
        emb = emb / max(float(np.linalg.norm(emb)), 1e-9)
        return emb.tolist()
```

**Modified:** `pyproject.toml`
- Replace dependencies:
  ```
  # Remove:
  sentence-transformers>=3.0

  # Add:
  optimum[onnxruntime]>=1.19
  onnxruntime>=1.17
  tokenizers>=0.19
  transformers>=4.41
  numpy>=1.24
  ```
- Change: `requires-python = ">=3.11"` (remove `<3.14` cap).
- Keep PyTorch path as a real optional backend (not a dangling dep):
  ```
  [project.optional-dependencies]
  torch = ["sentence-transformers>=3.0"]
  ```
  and retain a `TorchEmbedder` class behind a config switch (`embedder.backend: onnx|torch`). An optional dep that no code path reads is
  cargo-culted — either wire it in or remove it.

**Modified:** `README.md`
- Primary install: `uv tool install claude-context-engine`
- Secondary: `pipx install claude-context-engine`
- Tertiary: `pip install claude-context-engine` (inside venv)
- Remove Python 3.14 warning (no longer needed)

**Modified:** `cli.py`
- Remove Python 3.14 version check/warning

### Validation gate (prerequisite for merge)

ONNX is the **highest-risk** change in v0.2 — embedding drift silently
invalidates every user's index. Before merging this feature:

1. Benchmark precision@10 and recall@10 on the CCE repo queries (see §6)
   with the current sentence-transformers embedder. Record results.
2. Run the same benchmark with the ONNX embedder.
3. Require parity within ±2 percentage points; otherwise block the merge
   and investigate pooling/normalisation differences.

Bump `schema_version` (§0.1) regardless — embeddings computed by different
models are not comparable, so a reindex is mandatory.

### Testing
- Test embedding dimensions match previous (384 for MiniLM).
- Test attention-masked pooling matches sentence-transformers output within
  1e-4 cosine distance on a fixed text fixture.
- Test install size reduction (sanity check — `pip show` size).

---

## 3. Token-Aware Packing

### Problem
Fixed `top_k` either wastes token budget (too few results) or overflows it (too many). Results should fill the available budget optimally.

### Design

**Modified:** `src/context_engine/retrieval/retriever.py`

Packing runs **after** scoring, not in place of it. `top_k * 3` over-fetch
stays as-is; packing then trims the scored list to fit the budget.

```python
async def retrieve(
    self,
    query: str,
    top_k: int = 10,
    confidence_threshold: float = 0.0,
    max_tokens: int | None = None,  # NEW
) -> list[Chunk]:
    # ... existing vector search + FTS + RRF + scoring ...

    ranked = scored_chunks[:top_k]  # existing behaviour preserved

    if max_tokens is None:
        return ranked

    packed: list[Chunk] = []
    budget = max_tokens
    for chunk in ranked:
        tokens = chunk.token_count
        if tokens <= budget:
            packed.append(chunk)
            budget -= tokens
            continue
        # Chunk larger than remaining budget: try to fit a compressed
        # version, else skip. We do NOT truncate raw content — that produces
        # syntactically broken code fragments.
        if chunk.compressed_content:
            compressed_tokens = len(chunk.compressed_content) // 4
            if compressed_tokens <= budget:
                packed.append(chunk)
                budget -= compressed_tokens
    return packed
```

**Modified:** `src/context_engine/integration/mcp_server.py`
- `context_search` tool accepts optional `max_tokens` input parameter (default: 8000).
- Pass to retriever.

**Modified:** `src/context_engine/models.py`
- Add `token_count` property to `Chunk`. Use a tighter chars-per-token
  ratio for code — empirically ~3.3, not 4 — so we don't routinely
  under-count and overflow the budget:
  ```python
  _CHARS_PER_TOKEN_CODE = 3.3

  @property
  def token_count(self) -> int:
      text = self.compressed_content or self.content
      return max(1, int(len(text) / _CHARS_PER_TOKEN_CODE))
  ```
  If `tiktoken` is available, prefer it — but don't add it as a hard dep
  for a ~15% accuracy gain.

### Edge cases (resolved)

- **Single chunk exceeds budget**: prefer compressed version if available;
  else skip the chunk entirely. Never truncate raw code — partial AST
  fragments mislead the model.
- **Empty budget after packing**: return the packed list as-is; do not pad
  with lower-confidence chunks.

### Testing
- Test packed results total tokens ≤ budget.
- Test higher-confidence chunks are preferred (packing preserves scorer order).
- Test single chunk > budget: compressed version used if present, skipped otherwise.
- Test `max_tokens=None` preserves exact previous `top_k` behaviour.

---

## 4. Knowledge Graph (SQLite)

### Problem
The graph store is fully stubbed at `src/context_engine/storage/graph_store.py`. Nodes and edges are built during indexing but discarded. `related_context` MCP tool returns empty. Competitors use graphs for relationship-aware navigation.

### Design

**Modified:** `src/context_engine/storage/graph_store.py`

Replace the no-op with a threadsafe, non-blocking SQLite implementation.

```python
import asyncio
import json
import os
import sqlite3

from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType


class GraphStore:
    def __init__(self, db_path: str) -> None:
        os.makedirs(db_path, exist_ok=True)
        # check_same_thread=False — sqlite3 connections are pinned to the
        # creating thread by default. asyncio.to_thread pool hops threads.
        self._conn = sqlite3.connect(
            os.path.join(db_path, "graph.db"),
            check_same_thread=False,
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                properties TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                PRIMARY KEY (source_id, target_id, edge_type)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path)")
        self._conn.commit()

    async def ingest(self, nodes, edges) -> None:
        await asyncio.to_thread(self._ingest_sync, nodes, edges)

    async def get_neighbors(self, node_id, edge_type=None):
        return await asyncio.to_thread(self._get_neighbors_sync, node_id, edge_type)

    # ...similarly wrap get_nodes_by_file, get_nodes_by_type, delete_by_file
```

Every public `async` method wraps a `_sync` counterpart via
`asyncio.to_thread` — sqlite3 is blocking and would otherwise stall the
event loop (§0.3). Alternative: adopt `aiosqlite`.

**Modified:** `src/context_engine/storage/local_backend.py`
- Initialize `GraphStore` at `base_path/graph`.
- Pass nodes/edges during `ingest` (currently discarded at line 18).
- Delegate `graph_neighbors()` to the graph store (currently returns `[]` at line 33).

**Modified:** `src/context_engine/storage/remote_backend.py`
- Implement `graph_neighbors` via HTTP — parity with §0.2.

**Modified:** `src/context_engine/indexer/chunker.py` + `src/context_engine/indexer/pipeline.py`
- Detect `IMPORTS` edges via the existing tree-sitter AST, not regex.
  We already have Python + JS/TS parsers loaded; extending `_walk` to also
  emit `import_statement` and `import_from_statement` nodes is free and
  reliable (handles multi-line imports, aliases, relative imports). Regex
  was rejected because it silently mis-parses `from X import (\n  A,\n  B\n)`.
- Pipeline emits `GraphNode(NodeType.FILE)` / `GraphNode(NodeType.MODULE)`
  and `GraphEdge(EdgeType.IMPORTS)` during indexing.

**Modified:** `src/context_engine/integration/mcp_server.py`
- `related_context` tool now returns actual graph neighbors.
- Format: list of related nodes with edge types.

**Migration:** bump `schema_version` (§0.1) — existing indexes have no
graph tables.

### Testing
- Test node/edge ingest and retrieval.
- Test concurrent access from multiple asyncio tasks (validates `check_same_thread=False` + `to_thread` pattern).
- Test neighbor queries with edge type filtering.
- Test `delete_by_file` removes associated nodes and edges.
- Test AST-based import detection for Python (including `from X import (A, B)`) and JS/TS (including `import type`).

---

## 5. Git History Search

### Problem
No temporal context. Developers ask "what changed recently?" or "who modified the auth module?" and CCE can't answer.

### Design

**New file:** `src/context_engine/indexer/git_indexer.py`

```python
import asyncio
import subprocess
from pathlib import Path

from context_engine.models import Chunk, ChunkType, GraphNode, NodeType, GraphEdge, EdgeType


_COMMIT_SEPARATOR = "\n---END---\n"


async def index_commits(
    project_dir: Path,
    since_sha: str | None = None,
    max_commits: int = 200,
) -> tuple[list[Chunk], list[GraphNode], list[GraphEdge]]:
    """Parse recent git history into searchable chunks.

    Incremental: `since_sha` is the last-indexed commit stored in manifest.
    Only new commits (since_sha..HEAD) are walked on subsequent runs. First
    run or missing SHA falls back to `max_commits` depth.
    """
    args = ["git", "log"]
    if since_sha:
        args.append(f"{since_sha}..HEAD")
    else:
        args.append(f"-{max_commits}")
    # %b may contain newlines — the separator includes a leading \n so we
    # split on the full "\n---END---\n" boundary, never mid-body.
    args += ["--format=%H%n%an%n%ai%n%s%n%b---END---", "--stat"]

    result = await asyncio.to_thread(
        subprocess.run, args, cwd=project_dir, capture_output=True, text=True, check=False
    )
    # Parse output into Chunk objects:
    # - chunk_type = ChunkType.COMMIT
    # - content = commit message + file stats
    # - file_path = "git:SHORTHASH"   (see pseudo-path handling below)
    # - metadata = {"author": ..., "date": ..., "hash": ..., "chunk_kind": "commit"}
    #
    # Create GraphNode(NodeType.COMMIT) for each commit.
    # Create GraphEdge(EdgeType.MODIFIES) from commit node to file nodes.
```

**Modified:** `src/context_engine/indexer/pipeline.py`
- On `full=True` indexing, call `git_indexer.index_commits(since_sha=manifest.last_git_sha)`.
- Persist the new `HEAD` SHA back to the manifest so the next run is incremental.
- Add resulting chunks to the embedding + ingest pipeline.
- Add nodes/edges to graph store.

**Modified:** `src/context_engine/indexer/manifest.py`
- Add `last_git_sha: str | None` field.

**Modified:** `src/context_engine/retrieval/retriever.py`
- **Pseudo-path handling**: the existing `_apply_path_penalty` in
  `retriever.py:73` treats paths containing `tests/`, `docs/`, etc. as
  deprioritised. Commit chunks use synthetic paths like `git:abc1234` and
  must be exempted, otherwise `git:` strings that happen to contain those
  markers (rare, but possible) get penalised incorrectly.
  ```python
  if file_path.startswith("git:"):
      return score
  ```
- Optional: recency boost for commit chunks — newer commits score higher.
  Gate behind a config flag; off by default until benchmarks validate.

**Configuration:** add `git.max_commits` (default 200) and `git.enabled`
(default `true`) to `.context-engine.yaml`.

**No MCP changes needed:** `context_search` already returns all chunk types.
Commit chunks surface naturally for queries like "recent changes to auth."

### Testing
- Test parsing of git log output, including commit bodies containing blank lines and `---END---`-like strings (negative test).
- Test incremental indexing: second run with stored `since_sha` fetches only new commits.
- Test chunk creation with correct types and metadata.
- Test pseudo-path exemption: `git:abc1234` is not penalised by `_apply_path_penalty`.
- Test integration with pipeline (commit chunks appear in search).

---

## 6. Benchmarks

### Problem
No published performance data. Competitors like SocratiCode publish concrete numbers that drive adoption.

### Design

**New directory:** `benchmarks/`

**New file:** `benchmarks/run_benchmark.py`

```python
"""Benchmark suite for CCE token savings, retrieval quality, and latency."""

def benchmark_token_savings(project_dir, queries):
    """Compare tokens served by CCE vs reading full files."""
    # For each query:
    # 1. Measure full-file token cost (sum of all file sizes in project).
    # 2. Run CCE context_search, measure served tokens.
    # 3. Calculate savings percentage.

def benchmark_precision_recall(project_dir, queries_with_expected, mode):
    """Measure precision@k and recall@k against known-relevant files.

    `mode` is one of: "vector_only", "hybrid". Run both so the FTS
    investment is measurable in the published numbers — "vs reading full
    files" alone understates the v0.2 story.
    """

def benchmark_latency(project_dir, queries, iterations=50):
    """Measure search latency p50/p95/p99."""

def benchmark_embedding_parity(texts):
    """ONNX vs sentence-transformers cosine distance on a fixed corpus.

    Gates the ONNX migration merge — see §2 validation gate.
    """

if __name__ == "__main__":
    # Run against sample repos, output markdown table.
```

**New file:** `benchmarks/sample_queries.json`
- Curated queries with expected relevant files for CCE's own repo.
- Start self-hosted — we know the ground truth. Expand to external repos
  later once the labelling pipeline exists.

**New file:** `docs/benchmarks.md`
- Published results table with methodology description.
- Three comparisons: full-file baseline → vector-only → hybrid. Shows both
  the outer win (vs no CCE) and the inner win (vs v0.1 CCE).

**No core code changes.** Benchmarks consume existing APIs.

### Testing
- Benchmarks are self-validating (assert non-zero savings, assert latency < threshold, assert hybrid ≥ vector-only on precision@10).

---

## 7. Performance

### Problem
The other v0.2 features are mostly neutral-to-slower on query latency (hybrid
adds an FTS round-trip; graph/git add ingest writes). Only ONNX is a clear
speed win. To make v0.2 a genuine performance release, the following
targeted optimisations land alongside.

**Target metrics** (measured by §6 benchmarks on the CCE repo):
- Query p50 ≤ 80 ms, p95 ≤ 200 ms (currently unmeasured, but hybrid risks
  regression without these fixes).
- First-query cold start ≤ 1.5 s (currently dominated by ~1–2 s torch import
  — removed by §2 ONNX).
- Full reindex of CCE repo (~200 files) under 30 s on a laptop CPU.

### 7.1 Batched chunk hydration in hybrid retriever

**Modified:** `src/context_engine/retrieval/retriever.py`, `src/context_engine/storage/backend.py`, `src/context_engine/storage/vector_store.py`

FTS5 returns IDs; the naïve implementation calls `get_chunk_by_id(id)` per
ID — N LanceDB round trips. Add a batch method:

```python
# backend.py protocol
async def get_chunks_by_ids(self, ids: list[str]) -> list[Chunk]: ...

# vector_store.py
async def get_chunks_by_ids(self, ids: list[str]) -> list[Chunk]:
    if not ids:
        return []
    with self._lock:
        if self._table is None:
            return []
        quoted = ", ".join(_escape_sql_literal(i) for i in ids)
        rows = (
            self._table.search()
            .where(f"id IN ({quoted})")
            .limit(len(ids))
            .to_list()
        )
    return [self._row_to_chunk(r) for r in rows]
```

Cuts hybrid-search added latency from ~N×LanceDB-roundtrip to 1.

### 7.2 ANN index on LanceDB

**Modified:** `src/context_engine/storage/vector_store.py`

Default LanceDB tables use brute-force scan. On >10k chunks this dominates
query latency. Create an IVF_PQ index once the table exceeds a threshold:

```python
_INDEX_THRESHOLD = 10_000

async def _maybe_create_index(self) -> None:
    count = self._table.count_rows()
    if count >= _INDEX_THRESHOLD and not self._index_built:
        # num_partitions ~ sqrt(count) is the LanceDB rule of thumb.
        num_partitions = max(256, int(count ** 0.5))
        await asyncio.to_thread(
            self._table.create_index,
            metric="cosine",
            num_partitions=num_partitions,
            num_sub_vectors=16,
        )
        self._index_built = True
```

Call after every ingest batch. Idempotent; LanceDB skips if already indexed.
Expected speedup at 50k chunks: 10–50×.

### 7.3 Embedder batching audit

**Modified:** `src/context_engine/indexer/embedder.py`

The new ONNX `embed(chunks)` method already batches via tokenizer padding.
Add an explicit `batch_size` parameter (default 32) and chunk the input for
very large files so we don't OOM:

```python
def embed(self, chunks: list[Chunk], batch_size: int = 32) -> None:
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.content for c in batch]
        inputs = self._tokenizer(texts, padding=True, truncation=True, return_tensors="np")
        outputs = self._model(**inputs)
        embeddings = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
        embeddings = embeddings / norms
        for chunk, emb in zip(batch, embeddings):
            chunk.embedding = emb.tolist()
```

Audit the v0.1 path before cutover: if it already embeds per-chunk in a
loop, the ONNX migration is a bigger win than the raw ONNX-vs-torch numbers
suggest.

### 7.4 Query embedding LRU cache

**Modified:** `src/context_engine/indexer/embedder.py`

Repeat queries are common from MCP clients (multi-turn sessions, dashboards,
tests). Cache the computed vector:

```python
from functools import lru_cache

class Embedder:
    @lru_cache(maxsize=256)
    def embed_query(self, query: str) -> tuple[float, ...]:
        # returns tuple (hashable) — caller converts to list if needed
        ...
```

256 entries × 384 floats × 4 bytes ≈ 400 KB. Cheap. Warm-query latency drops
to near zero.

### 7.5 Concurrent dual-ingest

**Modified:** `src/context_engine/storage/local_backend.py`

The current local backend ingests serially. With FTS + graph added, ingest
does three sequential writes. Run them concurrently:

```python
async def ingest(self, chunks, nodes, edges) -> None:
    await asyncio.gather(
        self._vector_store.ingest(chunks),
        self._fts_store.ingest(chunks),
        self._graph_store.ingest(nodes, edges),
    )
```

Each store uses its own lock; no contention. Roughly halves ingest time on
mixed workloads.

### 7.6 Incremental indexing audit

**Modified:** none (audit + tests only)

`manifest.py` tracks per-file sha256 but [pipeline.py](src/context_engine/indexer/pipeline.py) has 0% test
coverage (see main review). Before v0.2 ships, verify that:

1. `pipeline.index_all()` genuinely skips files with unchanged sha256.
2. The watcher (`watcher.py`) triggers single-file reindex, not full.
3. `delete_by_file` runs on file deletion, not just modification.

If any of these are broken, repeated `cce index` runs re-embed unchanged
files — the single biggest wasted time in daily use. Add regression tests
alongside the §1–5 test suites.

### 7.7 Persistent MCP daemon

**Modified:** `src/context_engine/daemon.py`, `src/context_engine/integration/mcp_server.py`, `.mcp.json` docs

Claude Code spawns MCP servers per session by default. Each spawn pays:
- Python interpreter startup (~100 ms)
- ONNX model load (~300–500 ms)
- LanceDB + SQLite connection setup (~100 ms)

That's a ~1 s tax on every Claude session before the first query. Options:

- **Short-term**: document `cce serve --daemon` — users start it once per
  day, Claude reconnects to the existing socket. No code changes beyond a
  `--daemon` flag that detaches and writes a PID file.
- **Longer-term**: HTTP transport with a Unix socket in
  `~/.cache/cce/sock`; MCP config uses the socket directly, no subprocess.

Pick the short-term path for v0.2. Ship the socket path later.

### Testing
- Benchmark: hybrid query latency with/without batched hydration (expect ≥5× improvement).
- Benchmark: vector search at 50k chunks with/without ANN index.
- Cache hit ratio test: same query 10× returns identical vectors, only one model call.
- Ingest test: concurrent dual-ingest matches serial output exactly.
- Incremental test: indexing the same repo twice embeds zero chunks the second time.

---

## Implementation Order

These features are independent **except** where noted. Serial order, lowest
risk first — benchmarks land before ONNX so we have a parity baseline:

1. **Cross-cutting prep** (§0): `schema_version`, remote-backend stubs, `asyncio.to_thread` audit.
2. **Knowledge graph** (§4) — fills a stub; low risk; unblocks git history.
3. **Token-aware packing** (§3) — small change, high impact, independent of others.
4. **Hybrid search** (§1) — biggest search quality improvement; builds on graph migration pattern.
5. **Git history search** (§5) — depends on graph store landing first (edges).
6. **Performance** (§7) — batched hydration, ANN index, cache, concurrent ingest. Lands after the slow paths exist so benchmarks can measure the delta.
7. **Benchmarks** (§6) — validates 2–6 and establishes pre-ONNX baseline.
8. **ONNX migration** (§2) — highest risk; gated on §7 parity check; reindex required.

The previous spec put ONNX first "to unblock Python 3.14." ONNX should be
last — swapping the embedder invalidates every benchmark and every user's
index, so we want the most evidence before landing it.

---

## Out of Scope

- Web dashboard — deferred to v0.3. Would depend on graph + search + performance all being stable.
- Remote backend implementation beyond protocol parity (future work).
- Additional language support beyond current 7 (separate PR).
- CI/CD changes beyond benchmark publishing.
- `tiktoken` as a hard dep — nice-to-have only; the char-ratio estimate is adequate.
- Unix-socket MCP transport — short-term `--daemon` flag only in v0.2; full socket transport is v0.3.
