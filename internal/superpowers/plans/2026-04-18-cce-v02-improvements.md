# CCE v0.2 Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hybrid BM25 search, token-aware packing, knowledge graph, git history search, performance optimisations, benchmarks, and ONNX runtime migration to Claude Context Engine.

**Architecture:** Each feature is layered onto the existing storage/retrieval/indexer/MCP stack. New stores (FTS, graph) plug into `LocalBackend` alongside the existing `VectorStore`. The retriever gains RRF merging and token packing. Git history becomes a new chunk source. ONNX replaces PyTorch for embeddings. All async methods wrap blocking I/O via `asyncio.to_thread`.

**Tech Stack:** Python 3.11+, SQLite FTS5, LanceDB, ONNX Runtime, optimum, tree-sitter, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-18-cce-v02-improvements-design.md`

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `src/context_engine/storage/fts_store.py` | SQLite FTS5 full-text search |
| `src/context_engine/indexer/git_indexer.py` | Parse git log into searchable chunks |
| `benchmarks/run_benchmark.py` | Benchmark suite (savings, precision, latency) |
| `benchmarks/sample_queries.json` | Curated queries with expected files |
| `docs/benchmarks.md` | Published results |
| `tests/storage/test_fts_store.py` | FTS store tests |
| `tests/indexer/test_git_indexer.py` | Git indexer tests |
| `tests/test_token_packing.py` | Token-aware packing tests |
| `tests/test_performance.py` | Performance regression tests |

### Modified Files
| File | Changes |
|------|---------|
| `src/context_engine/indexer/manifest.py` | Add `schema_version` + `last_git_sha` |
| `src/context_engine/storage/backend.py` | Add `fts_search` + `get_chunks_by_ids` to protocol |
| `src/context_engine/storage/local_backend.py` | Wire FTS + graph stores, concurrent ingest |
| `src/context_engine/storage/remote_backend.py` | Add `fts_search` + `get_chunks_by_ids` stubs |
| `src/context_engine/storage/graph_store.py` | Replace no-ops with SQLite implementation |
| `src/context_engine/storage/vector_store.py` | Add `get_chunks_by_ids` + ANN index |
| `src/context_engine/retrieval/retriever.py` | Add RRF merging + token packing + pseudo-path handling |
| `src/context_engine/models.py` | Add `token_count` property to Chunk |
| `src/context_engine/indexer/pipeline.py` | Wire git indexer + import detection |
| `src/context_engine/indexer/chunker.py` | Extract import statements from AST |
| `src/context_engine/indexer/embedder.py` | ONNX runtime + LRU cache + batch_size |
| `src/context_engine/integration/mcp_server.py` | Add `max_tokens` to `context_search`, fix `related_context` |
| `src/context_engine/cli.py` | Remove Python 3.14 warning |
| `pyproject.toml` | ONNX deps, remove torch, remove Python cap |
| `README.md` | `uv tool install` as primary |

---

## Task 1: Schema Versioning in Manifest

**Files:**
- Modify: `src/context_engine/indexer/manifest.py`
- Modify: `tests/indexer/test_manifest.py`

- [ ] **Step 1: Write failing test for schema version detection**

```python
# tests/indexer/test_manifest.py — append to existing file

def test_schema_version_defaults_to_current(tmp_path):
    """New manifests start at current schema version."""
    m = Manifest(tmp_path / "manifest.json")
    assert m.schema_version == 2


def test_old_manifest_without_version_is_v1(tmp_path):
    """Pre-v0.2 manifests (plain dict) are treated as version 1."""
    path = tmp_path / "manifest.json"
    path.write_text('{"src/main.py": "abc123"}')
    m = Manifest(path)
    assert m.schema_version == 1


def test_schema_mismatch_flags_reindex(tmp_path):
    """When stored version < current, needs_reindex returns True."""
    path = tmp_path / "manifest.json"
    path.write_text('{"__schema_version": 1, "files": {"src/main.py": "abc123"}}')
    m = Manifest(path)
    assert m.needs_reindex is True


def test_schema_match_no_reindex(tmp_path):
    """When stored version == current, needs_reindex returns False."""
    m = Manifest(tmp_path / "manifest.json")
    m.update("src/main.py", "abc123")
    m.save()
    m2 = Manifest(tmp_path / "manifest.json")
    assert m2.needs_reindex is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/indexer/test_manifest.py -v -k "schema"`
Expected: FAIL — `Manifest` has no `schema_version` or `needs_reindex`

- [ ] **Step 3: Implement schema versioning**

Replace `src/context_engine/indexer/manifest.py` contents:

```python
"""Content hash manifest for incremental indexing."""
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2


class Manifest:
    def __init__(self, manifest_path: Path) -> None:
        self._path = manifest_path
        self._entries: dict[str, str] = {}
        self._schema_version: int = CURRENT_SCHEMA_VERSION
        self._last_git_sha: str | None = None
        if self._path.exists():
            try:
                with open(self._path) as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if "__schema_version" in loaded:
                        self._schema_version = loaded["__schema_version"]
                        self._entries = loaded.get("files", {})
                        self._last_git_sha = loaded.get("last_git_sha")
                    else:
                        # Pre-v0.2 manifest: plain dict of {path: hash}
                        self._schema_version = 1
                        self._entries = loaded
                else:
                    log.warning(
                        "Manifest at %s was not a dict (got %s); starting empty.",
                        self._path,
                        type(loaded).__name__,
                    )
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Manifest at %s unreadable (%s); starting empty.", self._path, exc)
                self._entries = {}

    @property
    def schema_version(self) -> int:
        return self._schema_version

    @property
    def needs_reindex(self) -> bool:
        return self._schema_version < CURRENT_SCHEMA_VERSION

    @property
    def last_git_sha(self) -> str | None:
        return self._last_git_sha

    @last_git_sha.setter
    def last_git_sha(self, value: str | None) -> None:
        self._last_git_sha = value

    def get_hash(self, file_path: str) -> str | None:
        return self._entries.get(file_path)

    def update(self, file_path: str, content_hash: str) -> None:
        self._entries[file_path] = content_hash

    def remove(self, file_path: str) -> None:
        self._entries.pop(file_path, None)

    def has_changed(self, file_path: str, content_hash: str) -> bool:
        return self._entries.get(file_path) != content_hash

    def save(self) -> None:
        """Atomic save — write to a tempfile in the same dir then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "__schema_version": CURRENT_SCHEMA_VERSION,
            "files": self._entries,
            "last_git_sha": self._last_git_sha,
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/indexer/test_manifest.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/indexer/manifest.py tests/indexer/test_manifest.py
git commit -m "feat: add schema versioning to manifest for safe index migration"
```

---

## Task 2: Extend StorageBackend Protocol

**Files:**
- Modify: `src/context_engine/storage/backend.py`
- Modify: `src/context_engine/storage/remote_backend.py`

- [ ] **Step 1: Add `fts_search` and `get_chunks_by_ids` to protocol**

Replace `src/context_engine/storage/backend.py`:

```python
"""Storage backend protocol — implemented by local and remote backends."""
from typing import Protocol, runtime_checkable

from context_engine.models import Chunk, GraphNode, GraphEdge, NodeType, EdgeType


@runtime_checkable
class StorageBackend(Protocol):
    async def ingest(
        self,
        chunks: list[Chunk],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> None: ...

    async def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[Chunk]: ...

    async def fts_search(
        self,
        query: str,
        top_k: int = 30,
    ) -> list[tuple[str, float]]: ...

    async def graph_neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
    ) -> list[GraphNode]: ...

    async def get_chunk_by_id(self, chunk_id: str) -> Chunk | None: ...

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]: ...

    async def delete_by_file(self, file_path: str) -> None: ...
```

- [ ] **Step 2: Add stubs to RemoteBackend**

Append to `src/context_engine/storage/remote_backend.py` after the `delete_by_file` method (line 78):

```python
    async def fts_search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/fts/search",
                    json={"query": query, "top_k": top_k})
                resp.raise_for_status()
                return [(r["id"], r["score"]) for r in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/chunks/batch",
                    json={"ids": chunk_ids})
                resp.raise_for_status()
                return [self._dict_to_chunk(d) for d in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `source .venv/bin/activate && pytest tests/storage/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/context_engine/storage/backend.py src/context_engine/storage/remote_backend.py
git commit -m "feat: extend StorageBackend protocol with fts_search and get_chunks_by_ids"
```

---

## Task 3: FTS Store (SQLite FTS5)

**Files:**
- Create: `src/context_engine/storage/fts_store.py`
- Create: `tests/storage/test_fts_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/storage/test_fts_store.py
import pytest
from context_engine.models import Chunk, ChunkType
from context_engine.storage.fts_store import FTSStore


@pytest.fixture
def fts(tmp_path):
    return FTSStore(db_path=str(tmp_path / "fts"))


@pytest.fixture
def sample_chunks():
    return [
        Chunk(id="c1", content="def calculate_tax(amount, rate): return amount * rate",
              chunk_type=ChunkType.FUNCTION, file_path="finance.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c2", content="def process_payment(card, amount): charge the card",
              chunk_type=ChunkType.FUNCTION, file_path="payment.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c3", content="class ShippingCalculator: calculates shipping costs",
              chunk_type=ChunkType.CLASS, file_path="shipping.py",
              start_line=1, end_line=5, language="python"),
    ]


@pytest.mark.asyncio
async def test_ingest_and_search(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    results = await fts.search("calculate_tax", top_k=5)
    assert len(results) > 0
    ids = [r[0] for r in results]
    assert "c1" in ids


@pytest.mark.asyncio
async def test_search_returns_scores(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    results = await fts.search("payment", top_k=5)
    assert all(isinstance(score, float) for _, score in results)


@pytest.mark.asyncio
async def test_delete_by_file(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    await fts.delete_by_file("finance.py")
    results = await fts.search("calculate_tax", top_k=5)
    ids = [r[0] for r in results]
    assert "c1" not in ids


@pytest.mark.asyncio
async def test_search_empty_store(fts):
    results = await fts.search("anything", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_special_chars_in_query(fts, sample_chunks):
    """FTS5 operators in queries don't cause errors."""
    await fts.ingest(sample_chunks)
    for q in ['"quoted"', "a-b", "fn(x)", "col:val", "wild*card"]:
        results = await fts.search(q, top_k=5)
        assert isinstance(results, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/storage/test_fts_store.py -v`
Expected: FAIL — `fts_store` module does not exist

- [ ] **Step 3: Implement FTSStore**

```python
# src/context_engine/storage/fts_store.py
"""SQLite FTS5 full-text search store."""
import asyncio
import logging
import os
import sqlite3

from context_engine.models import Chunk

log = logging.getLogger(__name__)


def _escape_fts5(query: str) -> str:
    """Wrap user input as an FTS5 phrase to avoid operator injection."""
    return '"' + query.replace('"', '""') + '"'


class FTSStore:
    def __init__(self, db_path: str) -> None:
        os.makedirs(db_path, exist_ok=True)
        self._conn = sqlite3.connect(
            os.path.join(db_path, "fts.db"), check_same_thread=False
        )
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(id UNINDEXED, content, file_path, language, chunk_type)"
        )
        self._conn.commit()

    def _ingest_sync(self, chunks: list[Chunk]) -> None:
        cursor = self._conn.cursor()
        for chunk in chunks:
            cursor.execute(
                "INSERT OR REPLACE INTO chunks_fts(id, content, file_path, language, chunk_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (chunk.id, chunk.content, chunk.file_path, chunk.language, chunk.chunk_type.value),
            )
        self._conn.commit()

    def _search_sync(self, escaped_query: str, top_k: int) -> list[tuple[str, float]]:
        cursor = self._conn.execute(
            "SELECT id, rank FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (escaped_query, top_k),
        )
        return [(row[0], float(row[1])) for row in cursor.fetchall()]

    def _delete_sync(self, file_path: str) -> None:
        self._conn.execute(
            "DELETE FROM chunks_fts WHERE file_path = ?", (file_path,)
        )
        self._conn.commit()

    async def ingest(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await asyncio.to_thread(self._ingest_sync, chunks)

    async def search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_sync, _escape_fts5(query), top_k)

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.to_thread(self._delete_sync, file_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/storage/test_fts_store.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/storage/fts_store.py tests/storage/test_fts_store.py
git commit -m "feat: add SQLite FTS5 full-text search store"
```

---

## Task 4: Knowledge Graph Store (SQLite)

**Files:**
- Modify: `src/context_engine/storage/graph_store.py`
- Modify: `tests/storage/test_graph_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/storage/test_graph_store.py — replace contents
import pytest
from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType
from context_engine.storage.graph_store import GraphStore


@pytest.fixture
def graph(tmp_path):
    return GraphStore(db_path=str(tmp_path / "graph"))


@pytest.fixture
def sample_data():
    nodes = [
        GraphNode(id="file_math", node_type=NodeType.FILE, name="math.py", file_path="math.py"),
        GraphNode(id="func_add", node_type=NodeType.FUNCTION, name="add", file_path="math.py"),
        GraphNode(id="func_mul", node_type=NodeType.FUNCTION, name="multiply", file_path="math.py"),
        GraphNode(id="file_util", node_type=NodeType.FILE, name="util.py", file_path="util.py"),
    ]
    edges = [
        GraphEdge(source_id="file_math", target_id="func_add", edge_type=EdgeType.DEFINES),
        GraphEdge(source_id="file_math", target_id="func_mul", edge_type=EdgeType.DEFINES),
        GraphEdge(source_id="func_add", target_id="func_mul", edge_type=EdgeType.CALLS),
        GraphEdge(source_id="file_util", target_id="file_math", edge_type=EdgeType.IMPORTS),
    ]
    return nodes, edges


@pytest.mark.asyncio
async def test_ingest_and_get_neighbors(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    neighbors = await graph.get_neighbors("file_math")
    names = [n.name for n in neighbors]
    assert "add" in names
    assert "multiply" in names


@pytest.mark.asyncio
async def test_get_neighbors_with_edge_filter(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    neighbors = await graph.get_neighbors("func_add", edge_type=EdgeType.CALLS)
    assert len(neighbors) == 1
    assert neighbors[0].name == "multiply"


@pytest.mark.asyncio
async def test_get_nodes_by_file(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    file_nodes = await graph.get_nodes_by_file("math.py")
    assert len(file_nodes) == 3  # file + 2 functions


@pytest.mark.asyncio
async def test_get_nodes_by_type(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    functions = await graph.get_nodes_by_type(NodeType.FUNCTION)
    assert len(functions) == 2


@pytest.mark.asyncio
async def test_delete_by_file(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    await graph.delete_by_file("math.py")
    remaining = await graph.get_nodes_by_file("math.py")
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_ingest_empty(graph):
    await graph.ingest([], [])
    neighbors = await graph.get_neighbors("nonexistent")
    assert neighbors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/storage/test_graph_store.py -v`
Expected: FAIL — graph store is no-op, returns empty

- [ ] **Step 3: Implement SQLite graph store**

Replace `src/context_engine/storage/graph_store.py`:

```python
"""Graph store — SQLite-backed relationship storage."""
import asyncio
import json
import logging
import os
import sqlite3

from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType

log = logging.getLogger(__name__)


class GraphStore:
    def __init__(self, db_path: str) -> None:
        os.makedirs(db_path, exist_ok=True)
        self._conn = sqlite3.connect(
            os.path.join(db_path, "graph.db"), check_same_thread=False
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

    def _ingest_sync(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        cursor = self._conn.cursor()
        for node in nodes:
            cursor.execute(
                "INSERT OR REPLACE INTO nodes(id, node_type, name, file_path, properties) "
                "VALUES (?, ?, ?, ?, ?)",
                (node.id, node.node_type.value, node.name, node.file_path,
                 json.dumps(node.properties)),
            )
        for edge in edges:
            cursor.execute(
                "INSERT OR REPLACE INTO edges(source_id, target_id, edge_type, properties) "
                "VALUES (?, ?, ?, ?)",
                (edge.source_id, edge.target_id, edge.edge_type.value,
                 json.dumps(edge.properties)),
            )
        self._conn.commit()

    def _get_neighbors_sync(
        self, node_id: str, edge_type: EdgeType | None
    ) -> list[GraphNode]:
        if edge_type:
            cursor = self._conn.execute(
                "SELECT n.id, n.node_type, n.name, n.file_path, n.properties "
                "FROM nodes n JOIN edges e ON n.id = e.target_id "
                "WHERE e.source_id = ? AND e.edge_type = ?",
                (node_id, edge_type.value),
            )
        else:
            cursor = self._conn.execute(
                "SELECT n.id, n.node_type, n.name, n.file_path, n.properties "
                "FROM nodes n JOIN edges e ON n.id = e.target_id "
                "WHERE e.source_id = ?",
                (node_id,),
            )
        return [
            GraphNode(
                id=row[0], node_type=NodeType(row[1]), name=row[2],
                file_path=row[3], properties=json.loads(row[4]),
            )
            for row in cursor.fetchall()
        ]

    def _get_nodes_by_file_sync(self, file_path: str) -> list[GraphNode]:
        cursor = self._conn.execute(
            "SELECT id, node_type, name, file_path, properties FROM nodes WHERE file_path = ?",
            (file_path,),
        )
        return [
            GraphNode(
                id=row[0], node_type=NodeType(row[1]), name=row[2],
                file_path=row[3], properties=json.loads(row[4]),
            )
            for row in cursor.fetchall()
        ]

    def _get_nodes_by_type_sync(self, node_type: NodeType) -> list[GraphNode]:
        cursor = self._conn.execute(
            "SELECT id, node_type, name, file_path, properties FROM nodes WHERE node_type = ?",
            (node_type.value,),
        )
        return [
            GraphNode(
                id=row[0], node_type=NodeType(row[1]), name=row[2],
                file_path=row[3], properties=json.loads(row[4]),
            )
            for row in cursor.fetchall()
        ]

    def _delete_by_file_sync(self, file_path: str) -> None:
        # Delete edges where source or target is a node from this file
        self._conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM nodes WHERE file_path = ?) "
            "OR target_id IN (SELECT id FROM nodes WHERE file_path = ?)",
            (file_path, file_path),
        )
        self._conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
        self._conn.commit()

    async def ingest(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        if not nodes and not edges:
            return
        await asyncio.to_thread(self._ingest_sync, nodes, edges)

    async def get_neighbors(
        self, node_id: str, edge_type: EdgeType | None = None
    ) -> list[GraphNode]:
        return await asyncio.to_thread(self._get_neighbors_sync, node_id, edge_type)

    async def get_nodes_by_file(self, file_path: str) -> list[GraphNode]:
        return await asyncio.to_thread(self._get_nodes_by_file_sync, file_path)

    async def get_nodes_by_type(self, node_type: NodeType) -> list[GraphNode]:
        return await asyncio.to_thread(self._get_nodes_by_type_sync, node_type)

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.to_thread(self._delete_by_file_sync, file_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/storage/test_graph_store.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/storage/graph_store.py tests/storage/test_graph_store.py
git commit -m "feat: implement SQLite-backed graph store replacing no-op stubs"
```

---

## Task 5: Wire FTS + Graph into LocalBackend

**Files:**
- Modify: `src/context_engine/storage/local_backend.py`
- Modify: `src/context_engine/storage/vector_store.py`
- Modify: `tests/storage/test_local_backend.py`

- [ ] **Step 1: Add `get_chunks_by_ids` to VectorStore**

Append to `src/context_engine/storage/vector_store.py` after the `get_by_id` method (after line 141):

```python
    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        with self._lock:
            if self._table is None:
                try:
                    self._table = self._db.open_table(TABLE_NAME)
                except Exception:
                    return []
            quoted = ", ".join(_escape_sql_literal(i) for i in chunk_ids)
            results = (
                self._table.search()
                .where(f"id IN ({quoted})")
                .limit(len(chunk_ids))
                .to_list()
            )
        return [self._row_to_chunk(r) for r in results]
```

- [ ] **Step 2: Rewrite LocalBackend to wire all three stores**

Replace `src/context_engine/storage/local_backend.py`:

```python
"""Local storage backend — LanceDB vectors + SQLite FTS + SQLite graph."""
import asyncio
from pathlib import Path

from context_engine.models import Chunk, GraphNode, GraphEdge, EdgeType
from context_engine.storage.vector_store import VectorStore
from context_engine.storage.fts_store import FTSStore
from context_engine.storage.graph_store import GraphStore


class LocalBackend:
    def __init__(self, base_path: str) -> None:
        self._vector_store = VectorStore(db_path=str(Path(base_path) / "vectors"))
        self._fts_store = FTSStore(db_path=str(Path(base_path) / "fts"))
        self._graph_store = GraphStore(db_path=str(Path(base_path) / "graph"))

    async def ingest(
        self,
        chunks: list[Chunk],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> None:
        await asyncio.gather(
            self._vector_store.ingest(chunks),
            self._fts_store.ingest(chunks),
            self._graph_store.ingest(nodes, edges),
        )

    async def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[Chunk]:
        return await self._vector_store.search(query_embedding, top_k, filters)

    async def fts_search(
        self,
        query: str,
        top_k: int = 30,
    ) -> list[tuple[str, float]]:
        return await self._fts_store.search(query, top_k)

    async def graph_neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
    ) -> list[GraphNode]:
        return await self._graph_store.get_neighbors(node_id, edge_type)

    async def get_chunk_by_id(self, chunk_id: str) -> Chunk | None:
        return await self._vector_store.get_by_id(chunk_id)

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        return await self._vector_store.get_chunks_by_ids(chunk_ids)

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.gather(
            self._vector_store.delete_by_file(file_path),
            self._fts_store.delete_by_file(file_path),
            self._graph_store.delete_by_file(file_path),
        )
```

- [ ] **Step 3: Write test for wired backend**

Append to `tests/storage/test_local_backend.py`:

```python
@pytest.mark.asyncio
async def test_fts_search_returns_results(tmp_path):
    backend = LocalBackend(base_path=str(tmp_path))
    chunks = [
        Chunk(id="c1", content="def calculate_tax(): pass",
              chunk_type=ChunkType.FUNCTION, file_path="tax.py",
              start_line=1, end_line=1, language="python",
              embedding=[0.1, 0.2, 0.3, 0.4]),
    ]
    await backend.ingest(chunks, [], [])
    results = await backend.fts_search("calculate_tax", top_k=5)
    assert len(results) > 0
    assert results[0][0] == "c1"


@pytest.mark.asyncio
async def test_graph_neighbors_returns_results(tmp_path):
    backend = LocalBackend(base_path=str(tmp_path))
    nodes = [
        GraphNode(id="f1", node_type=NodeType.FILE, name="a.py", file_path="a.py"),
        GraphNode(id="fn1", node_type=NodeType.FUNCTION, name="foo", file_path="a.py"),
    ]
    edges = [GraphEdge(source_id="f1", target_id="fn1", edge_type=EdgeType.DEFINES)]
    chunks = [
        Chunk(id="c1", content="def foo(): pass", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[0.1, 0.2, 0.3, 0.4]),
    ]
    await backend.ingest(chunks, nodes, edges)
    neighbors = await backend.graph_neighbors("f1")
    assert len(neighbors) == 1
    assert neighbors[0].name == "foo"


@pytest.mark.asyncio
async def test_get_chunks_by_ids(tmp_path):
    backend = LocalBackend(base_path=str(tmp_path))
    chunks = [
        Chunk(id="c1", content="def a(): pass", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[0.1, 0.2, 0.3, 0.4]),
        Chunk(id="c2", content="def b(): pass", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.5, 0.6, 0.7, 0.8]),
    ]
    await backend.ingest(chunks, [], [])
    result = await backend.get_chunks_by_ids(["c1", "c2"])
    assert len(result) == 2
```

- [ ] **Step 4: Run all storage tests**

Run: `source .venv/bin/activate && pytest tests/storage/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/storage/vector_store.py src/context_engine/storage/local_backend.py tests/storage/test_local_backend.py
git commit -m "feat: wire FTS and graph stores into LocalBackend with concurrent ingest"
```

---

## Task 6: Token-Aware Packing + Chunk.token_count

**Files:**
- Modify: `src/context_engine/models.py`
- Modify: `src/context_engine/retrieval/retriever.py`
- Modify: `src/context_engine/integration/mcp_server.py`
- Create: `tests/test_token_packing.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_packing.py
import pytest
from context_engine.models import Chunk, ChunkType

def _make_chunk(id, content, score=0.5):
    c = Chunk(id=id, content=content, chunk_type=ChunkType.FUNCTION,
              file_path="test.py", start_line=1, end_line=1, language="python")
    c.confidence_score = score
    return c


def test_token_count_property():
    c = _make_chunk("c1", "x" * 330)
    assert c.token_count == 100  # 330 / 3.3 = 100


def test_token_count_uses_compressed_if_available():
    c = _make_chunk("c1", "x" * 330)
    c.compressed_content = "x" * 33  # 33 / 3.3 = 10
    assert c.token_count == 10


def test_token_count_minimum_is_1():
    c = _make_chunk("c1", "")
    assert c.token_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_token_packing.py -v`
Expected: FAIL — `Chunk` has no `token_count` property

- [ ] **Step 3: Add `token_count` to Chunk**

In `src/context_engine/models.py`, add after line 63 (`compressed_content: str | None = None`):

```python
    _CHARS_PER_TOKEN_CODE = 3.3

    @property
    def token_count(self) -> int:
        text = self.compressed_content or self.content
        return max(1, int(len(text) / self._CHARS_PER_TOKEN_CODE))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_token_packing.py -v`
Expected: ALL PASS

- [ ] **Step 5: Add `max_tokens` to retriever**

In `src/context_engine/retrieval/retriever.py`, modify the `retrieve` method signature (line 26-31) to:

```python
    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        confidence_threshold: float = 0.0,
        max_tokens: int | None = None,
    ) -> list[Chunk]:
```

And replace the return statement at line 70 (`return [chunk for chunk, _ in scored[:top_k]]`) with:

```python
        ranked = [chunk for chunk, _ in scored[:top_k]]

        if max_tokens is None:
            return ranked

        packed: list[Chunk] = []
        budget = max_tokens
        for chunk in ranked:
            tokens = chunk.token_count
            if tokens <= budget:
                packed.append(chunk)
                budget -= tokens
            elif chunk.compressed_content:
                compressed_tokens = max(1, int(len(chunk.compressed_content) / 3.3))
                if compressed_tokens <= budget:
                    packed.append(chunk)
                    budget -= compressed_tokens
        return packed
```

- [ ] **Step 6: Add `max_tokens` to MCP context_search**

In `src/context_engine/integration/mcp_server.py`, update the `context_search` tool schema (line 137-143) to include `max_tokens`:

```python
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "top_k": {"type": "integer", "default": 10},
                            "max_tokens": {"type": "integer", "default": 8000},
                        },
                        "required": ["query"],
                    },
```

And in `_handle_context_search` (line 261-301), add after line 272 (`top_k = _clamp_top_k(...)`):

```python
        max_tokens = args.get("max_tokens", 8000)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 8000
```

And update the retriever call (line 274-278) to pass `max_tokens`:

```python
        chunks = await self._retriever.retrieve(
            query,
            top_k=top_k,
            confidence_threshold=self._config.retrieval_confidence_threshold,
            max_tokens=max_tokens,
        )
```

- [ ] **Step 7: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v --timeout=60`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/models.py src/context_engine/retrieval/retriever.py src/context_engine/integration/mcp_server.py tests/test_token_packing.py
git commit -m "feat: add token-aware packing to retriever and MCP context_search"
```

---

## Task 7: Hybrid Search (RRF Merging in Retriever)

**Files:**
- Modify: `src/context_engine/retrieval/retriever.py`
- Modify: `tests/retrieval/test_retriever.py`

- [ ] **Step 1: Write failing test**

Append to `tests/retrieval/test_retriever.py`:

```python
@pytest.mark.asyncio
async def test_retrieve_uses_fts_for_exact_keyword(seeded_retriever, backend):
    """Exact keyword 'UserAuth' should rank high thanks to FTS boost."""
    # The seeded_retriever's backend now has FTS wired in
    results = await seeded_retriever.retrieve("UserAuth", top_k=5)
    assert len(results) > 0
    # UserAuth should be in top results thanks to FTS exact match
    file_paths = [c.file_path for c in results]
    assert "auth.py" in file_paths
```

- [ ] **Step 2: Implement RRF merging**

Replace `src/context_engine/retrieval/retriever.py`:

```python
"""Hybrid retrieval — vector search + FTS BM25 + RRF merging + confidence scoring."""
import logging

from context_engine.models import Chunk
from context_engine.storage.backend import StorageBackend
from context_engine.indexer.embedder import Embedder
from context_engine.retrieval.confidence import ConfidenceScorer
from context_engine.retrieval.query_parser import QueryParser

log = logging.getLogger(__name__)

_DEPRIORITISED_PATHS = {"tests/", "test_", "docs/", "spec", "plan"}
_RRF_K = 60  # Standard RRF constant


class HybridRetriever:
    def __init__(self, backend: StorageBackend, embedder: Embedder) -> None:
        self._backend = backend
        self._embedder = embedder
        self._scorer = ConfidenceScorer()
        self._parser = QueryParser()
        self._fts_warned = False

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        confidence_threshold: float = 0.0,
        max_tokens: int | None = None,
    ) -> list[Chunk]:
        parsed = self._parser.parse(query)
        query_embedding = self._embedder.embed_query(query)

        # Vector search
        vector_results = await self._backend.vector_search(
            query_embedding=query_embedding,
            top_k=max(top_k * 3, 1),
        )

        # FTS search (graceful fallback if unavailable)
        fts_ids: dict[str, float] = {}
        try:
            fts_results = await self._backend.fts_search(query, top_k=top_k * 3)
            fts_ids = {id_: rank for rank, (id_, _) in enumerate(fts_results)}
        except Exception:
            if not self._fts_warned:
                log.warning("FTS search unavailable; falling back to vector-only")
                self._fts_warned = True

        # Build RRF scores for vector results
        vector_ranks: dict[str, int] = {}
        chunk_map: dict[str, Chunk] = {}
        seen_keys: set[str] = set()

        for rank, chunk in enumerate(vector_results):
            dedup_key = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            vector_ranks[chunk.id] = rank
            chunk_map[chunk.id] = chunk

        # Hydrate FTS-only results (not in vector results)
        fts_only_ids = [id_ for id_ in fts_ids if id_ not in chunk_map]
        if fts_only_ids:
            try:
                hydrated = await self._backend.get_chunks_by_ids(fts_only_ids)
                for chunk in hydrated:
                    chunk_map[chunk.id] = chunk
            except Exception:
                pass  # FTS-only chunks won't be included; vector results still work

        # Compute RRF score for all known chunks
        all_ids = set(vector_ranks.keys()) | set(fts_ids.keys())
        rrf_scores: dict[str, float] = {}
        for id_ in all_ids:
            score = 0.0
            if id_ in vector_ranks:
                score += 1.0 / (_RRF_K + vector_ranks[id_])
            if id_ in fts_ids:
                score += 1.0 / (_RRF_K + fts_ids[id_])
            rrf_scores[id_] = score

        # Score with confidence scorer
        scored: list[tuple[Chunk, float]] = []
        for id_, rrf_score in rrf_scores.items():
            chunk = chunk_map.get(id_)
            if chunk is None:
                continue

            distance = chunk.metadata.get("_distance", 0.0)
            normalised_distance = min(max(distance / 2.0, 0.0), 1.0)
            keyword_distance = self._estimate_keyword_distance(chunk, parsed)
            conf_score = self._scorer.score(
                chunk,
                vector_distance=normalised_distance,
                keyword_distance=keyword_distance,
            )

            # Blend RRF with confidence: RRF handles rank fusion,
            # confidence handles semantic + recency signals
            final_score = 0.5 * conf_score + 0.5 * min(rrf_score * _RRF_K, 1.0)
            final_score = self._apply_path_penalty(chunk.file_path, final_score)
            chunk.confidence_score = final_score

            if final_score >= confidence_threshold:
                scored.append((chunk, final_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [chunk for chunk, _ in scored[:top_k]]

        if max_tokens is None:
            return ranked

        packed: list[Chunk] = []
        budget = max_tokens
        for chunk in ranked:
            tokens = chunk.token_count
            if tokens <= budget:
                packed.append(chunk)
                budget -= tokens
            elif chunk.compressed_content:
                compressed_tokens = max(1, int(len(chunk.compressed_content) / 3.3))
                if compressed_tokens <= budget:
                    packed.append(chunk)
                    budget -= compressed_tokens
        return packed

    @staticmethod
    def _apply_path_penalty(file_path: str, score: float) -> float:
        if file_path.startswith("git:"):
            return score
        fp_lower = file_path.lower()
        for marker in _DEPRIORITISED_PATHS:
            if marker in fp_lower:
                return score * 0.8
        return score

    def _estimate_keyword_distance(self, chunk, parsed) -> int:
        if parsed.file_hints:
            for hint in parsed.file_hints:
                if hint in chunk.file_path:
                    return 0
        for keyword in parsed.keywords:
            if keyword.lower() in chunk.content.lower():
                return 0
        return 2
```

- [ ] **Step 3: Run tests**

Run: `source .venv/bin/activate && pytest tests/retrieval/ -v --timeout=60`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/context_engine/retrieval/retriever.py tests/retrieval/test_retriever.py
git commit -m "feat: add RRF hybrid search merging vector + FTS results"
```

---

## Task 8: Import Detection in Chunker

**Files:**
- Modify: `src/context_engine/indexer/chunker.py`
- Modify: `tests/indexer/test_chunker.py`

- [ ] **Step 1: Write failing test**

Append to `tests/indexer/test_chunker.py`:

```python
def test_extract_imports_python():
    source = "import os\nfrom pathlib import Path\n\ndef main(): pass\n"
    chunker = Chunker()
    chunks, imports = chunker.chunk_with_imports(source, file_path="main.py", language="python")
    assert len(chunks) > 0
    assert "os" in imports
    assert "pathlib" in imports


def test_extract_imports_javascript():
    source = "import React from 'react';\nimport { useState } from 'react';\nfunction App() {}\n"
    chunker = Chunker()
    chunks, imports = chunker.chunk_with_imports(source, file_path="App.js", language="javascript")
    assert len(chunks) > 0
    assert "react" in imports


def test_chunk_still_works_without_imports():
    """Existing chunk() method unchanged."""
    source = "def hello(): pass\n"
    chunker = Chunker()
    chunks = chunker.chunk(source, file_path="hello.py", language="python")
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/indexer/test_chunker.py -v -k "import"`
Expected: FAIL — `chunk_with_imports` method doesn't exist

- [ ] **Step 3: Add import extraction to Chunker**

Add these node types and method to `src/context_engine/indexer/chunker.py`:

After line 14 (`_CLASS_TYPES = {...}`), add:

```python
_IMPORT_TYPES = {
    "import_statement", "import_from_statement",  # Python
    "import_declaration",  # JavaScript/TypeScript
}
```

After the `_fallback_chunk` method, add:

```python
    def chunk_with_imports(
        self, source: str, file_path: str, language: str
    ) -> tuple[list[Chunk], list[str]]:
        """Chunk source code and also extract imported module names."""
        chunks = self.chunk(source, file_path, language)
        imports = self._extract_imports(source, language)
        return chunks, imports

    def _extract_imports(self, source: str, language: str) -> list[str]:
        parser = self._get_parser(language)
        if parser is None:
            return []
        tree = parser.parse(source.encode("utf-8"))
        imports: list[str] = []
        self._walk_imports(tree.root_node, source, imports)
        return imports

    def _walk_imports(self, node, source: str, imports: list[str]) -> None:
        if node.type in _IMPORT_TYPES:
            text = source[node.start_byte:node.end_byte]
            module = self._parse_import_module(text, node.type)
            if module:
                imports.append(module)
        for child in node.children:
            self._walk_imports(child, source, imports)

    @staticmethod
    def _parse_import_module(text: str, node_type: str) -> str | None:
        """Extract module name from import statement text."""
        text = text.strip()
        if node_type == "import_statement":
            # Python: import os / import os.path
            parts = text.replace("import ", "").split(",")
            return parts[0].strip().split(" as ")[0].strip().split(".")[0]
        elif node_type == "import_from_statement":
            # Python: from pathlib import Path
            if text.startswith("from "):
                module = text.split("import")[0].replace("from ", "").strip()
                return module.split(".")[0]
        elif node_type == "import_declaration":
            # JS: import X from 'module' / import { X } from 'module'
            for quote in ("'", '"'):
                if quote in text:
                    start = text.index(quote) + 1
                    end = text.index(quote, start)
                    return text[start:end].split("/")[0]
        return None
```

- [ ] **Step 4: Run tests**

Run: `source .venv/bin/activate && pytest tests/indexer/test_chunker.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/indexer/chunker.py tests/indexer/test_chunker.py
git commit -m "feat: extract import statements from AST for graph edges"
```

---

## Task 9: Wire Import Edges into Pipeline

**Files:**
- Modify: `src/context_engine/indexer/pipeline.py`

- [ ] **Step 1: Update pipeline to use `chunk_with_imports`**

In `src/context_engine/indexer/pipeline.py`, replace the chunking block (lines 185-232) with:

```python
        try:
            chunks, imported_modules = chunker.chunk_with_imports(
                content, file_path=rel_path, language=language
            )
        except Exception as exc:  # pragma: no cover - defensive
            result.errors.append(f"Chunking failed for {rel_path}: {exc}")
            log.warning("Chunking failed for %s", rel_path, exc_info=exc)
            continue
        elapsed = time.monotonic() - t0
        if log_fn:
            log_fn(f"  [index] {rel_path} — {len(chunks)} chunks ({elapsed:.3f}s)")

        await backend.delete_by_file(rel_path)

        file_node = GraphNode(
            id=f"file_{rel_path}",
            node_type=NodeType.FILE,
            name=file_path.name,
            file_path=rel_path,
        )
        all_nodes.append(file_node)

        # Add IMPORTS edges for detected import statements
        for module in imported_modules:
            all_edges.append(
                GraphEdge(
                    source_id=file_node.id,
                    target_id=f"module_{module}",
                    edge_type=EdgeType.IMPORTS,
                )
            )

        for chunk in chunks:
            node_type = (
                NodeType.FUNCTION
                if chunk.chunk_type.value == "function"
                else NodeType.CLASS
            )
            node_name = (
                chunk.content.split("(")[0].split(":")[-1].strip()
                if "(" in chunk.content
                else chunk.id
            )
            all_nodes.append(
                GraphNode(
                    id=chunk.id,
                    node_type=node_type,
                    name=node_name,
                    file_path=rel_path,
                )
            )
            all_edges.append(
                GraphEdge(
                    source_id=file_node.id,
                    target_id=chunk.id,
                    edge_type=EdgeType.DEFINES,
                )
            )
        all_chunks.extend(chunks)
```

- [ ] **Step 2: Run existing tests**

Run: `source .venv/bin/activate && pytest tests/ -v --timeout=60 -x`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/context_engine/indexer/pipeline.py
git commit -m "feat: wire import detection into indexing pipeline for IMPORTS graph edges"
```

---

## Task 10: Git History Search

**Files:**
- Create: `src/context_engine/indexer/git_indexer.py`
- Create: `tests/indexer/test_git_indexer.py`
- Modify: `src/context_engine/indexer/pipeline.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/indexer/test_git_indexer.py
import subprocess
import pytest
from context_engine.indexer.git_indexer import index_commits
from context_engine.models import ChunkType, NodeType, EdgeType


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with 3 commits."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=True)
    for i in range(3):
        (tmp_path / f"file{i}.py").write_text(f"def fn{i}(): pass\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", f"Add file{i}"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


@pytest.mark.asyncio
async def test_index_commits_returns_chunks(git_repo):
    chunks, nodes, edges = await index_commits(git_repo, max_commits=10)
    assert len(chunks) == 3
    assert all(c.chunk_type == ChunkType.COMMIT for c in chunks)


@pytest.mark.asyncio
async def test_commit_chunks_have_metadata(git_repo):
    chunks, _, _ = await index_commits(git_repo, max_commits=10)
    for chunk in chunks:
        assert "author" in chunk.metadata
        assert "hash" in chunk.metadata
        assert chunk.file_path.startswith("git:")


@pytest.mark.asyncio
async def test_commit_nodes_and_edges(git_repo):
    chunks, nodes, edges = await index_commits(git_repo, max_commits=10)
    assert len(nodes) >= 3
    assert all(n.node_type == NodeType.COMMIT for n in nodes)
    # Each commit should have at least one MODIFIES edge
    assert len(edges) > 0
    assert all(e.edge_type == EdgeType.MODIFIES for e in edges)


@pytest.mark.asyncio
async def test_incremental_since_sha(git_repo):
    chunks_all, _, _ = await index_commits(git_repo, max_commits=10)
    first_sha = chunks_all[-1].metadata["hash"]  # oldest commit
    chunks_new, _, _ = await index_commits(git_repo, since_sha=first_sha)
    assert len(chunks_new) < len(chunks_all)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/indexer/test_git_indexer.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement git indexer**

```python
# src/context_engine/indexer/git_indexer.py
"""Parse git log into searchable chunks."""
import asyncio
import logging
import subprocess
from pathlib import Path

from context_engine.models import (
    Chunk, ChunkType, GraphNode, GraphEdge, NodeType, EdgeType,
)

log = logging.getLogger(__name__)

_SEPARATOR = "---CCE_COMMIT_END---"


async def index_commits(
    project_dir: Path,
    since_sha: str | None = None,
    max_commits: int = 200,
) -> tuple[list[Chunk], list[GraphNode], list[GraphEdge]]:
    """Parse recent git history into searchable chunks."""
    args = ["git", "log"]
    if since_sha:
        args.append(f"{since_sha}..HEAD")
    else:
        args.append(f"-{max_commits}")
    args += [
        f"--format=%H%n%an%n%ai%n%s%n%b{_SEPARATOR}",
        "--stat",
    ]

    result = await asyncio.to_thread(
        subprocess.run, args, cwd=project_dir,
        capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        log.warning("git log failed: %s", result.stderr.strip())
        return [], [], []

    return _parse_log(result.stdout)


def _parse_log(
    output: str,
) -> tuple[list[Chunk], list[GraphNode], list[GraphEdge]]:
    chunks: list[Chunk] = []
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    blocks = output.split(_SEPARATOR)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if len(lines) < 4:
            continue

        commit_hash = lines[0].strip()
        author = lines[1].strip()
        date = lines[2].strip()
        subject = lines[3].strip()

        # Body is everything between subject and the stat lines
        body_lines = []
        stat_lines = []
        changed_files = []
        in_stats = False
        for line in lines[4:]:
            stripped = line.strip()
            if stripped and ("|" in stripped or "changed" in stripped):
                in_stats = True
            if in_stats:
                stat_lines.append(line)
                if "|" in stripped:
                    fname = stripped.split("|")[0].strip()
                    if fname:
                        changed_files.append(fname)
            else:
                body_lines.append(line)

        body = "\n".join(body_lines).strip()
        stats = "\n".join(stat_lines).strip()
        content = f"{subject}\n\n{body}\n\n{stats}".strip()
        short_hash = commit_hash[:7]

        chunk = Chunk(
            id=f"commit_{short_hash}",
            content=content,
            chunk_type=ChunkType.COMMIT,
            file_path=f"git:{short_hash}",
            start_line=0,
            end_line=0,
            language="git",
            metadata={
                "author": author,
                "date": date,
                "hash": commit_hash,
                "chunk_kind": "commit",
            },
        )
        chunks.append(chunk)

        node = GraphNode(
            id=f"commit_{short_hash}",
            node_type=NodeType.COMMIT,
            name=subject,
            file_path=f"git:{short_hash}",
        )
        nodes.append(node)

        for fname in changed_files:
            edges.append(
                GraphEdge(
                    source_id=f"commit_{short_hash}",
                    target_id=f"file_{fname}",
                    edge_type=EdgeType.MODIFIES,
                )
            )

    return chunks, nodes, edges
```

- [ ] **Step 4: Run tests**

Run: `source .venv/bin/activate && pytest tests/indexer/test_git_indexer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Wire into pipeline**

In `src/context_engine/indexer/pipeline.py`, add import at top (after line 20):

```python
from context_engine.indexer.git_indexer import index_commits
```

In `_run_indexing_locked`, add after the file loop ends and before `if all_chunks:` (before the embedding block):

```python
    # Index git history on full runs
    if full and not target_path:
        try:
            git_chunks, git_nodes, git_edges = await index_commits(
                project_dir, since_sha=manifest.last_git_sha
            )
            all_chunks.extend(git_chunks)
            all_nodes.extend(git_nodes)
            all_edges.extend(git_edges)
            if git_chunks:
                # Store HEAD sha for incremental git indexing
                head_result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "rev-parse", "HEAD"],
                    cwd=project_dir, capture_output=True, text=True, check=False,
                )
                if head_result.returncode == 0:
                    manifest.last_git_sha = head_result.stdout.strip()
                if log_fn:
                    log_fn(f"  [git] {len(git_chunks)} commit(s) indexed")
        except Exception as exc:
            log.warning("Git history indexing failed: %s", exc)
```

Also add `import subprocess` at the top of the file if not already present.

- [ ] **Step 6: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v --timeout=60 -x`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/context_engine/indexer/git_indexer.py tests/indexer/test_git_indexer.py src/context_engine/indexer/pipeline.py
git commit -m "feat: add git history search — index commits as searchable chunks"
```

---

## Task 11: Performance — ANN Index + Query Cache

**Files:**
- Modify: `src/context_engine/storage/vector_store.py`
- Modify: `src/context_engine/indexer/embedder.py`

- [ ] **Step 1: Add ANN index to VectorStore**

In `src/context_engine/storage/vector_store.py`, add after `TABLE_NAME = "chunks"` (line 11):

```python
_INDEX_THRESHOLD = 10_000
```

Add a new method after `get_chunks_by_ids`:

```python
    async def _maybe_create_index(self) -> None:
        """Create an IVF_PQ index once the table exceeds the threshold."""
        with self._lock:
            if self._table is None:
                return
            try:
                count = self._table.count_rows()
            except Exception:
                return
            if count < _INDEX_THRESHOLD:
                return
            try:
                import math
                num_partitions = max(256, int(math.sqrt(count)))
                await asyncio.to_thread(
                    self._table.create_index,
                    metric="cosine",
                    num_partitions=num_partitions,
                    num_sub_vectors=16,
                )
                log.info("Created ANN index on %d chunks", count)
            except Exception as exc:
                log.debug("ANN index creation skipped: %s", exc)
```

Add `import asyncio` and `import logging` to the imports, and `log = logging.getLogger(__name__)` after imports.

Call `_maybe_create_index` at the end of `ingest` (after line 96):

```python
        await self._maybe_create_index()
```

- [ ] **Step 2: Add LRU cache to embedder query method**

In `src/context_engine/indexer/embedder.py`, add to imports:

```python
from functools import lru_cache
```

Change `embed_query` (line 85-86) to return a tuple (hashable) and add caching:

```python
    @lru_cache(maxsize=256)
    def embed_query(self, query: str) -> tuple:
        vec = self._model.encode(query, show_progress_bar=False).tolist()
        return tuple(vec)
```

Note: callers that need `list[float]` will need `list(embedder.embed_query(q))`. Update `retriever.py` to convert:

In `src/context_engine/retrieval/retriever.py`, the line that calls `embed_query` should convert the tuple:

```python
        query_embedding = list(self._embedder.embed_query(query))
```

- [ ] **Step 3: Add batch_size to embedder**

In `src/context_engine/indexer/embedder.py`, modify `embed` (line 77-83):

```python
    def embed(self, chunks: list[Chunk], batch_size: int = 32) -> None:
        if not chunks:
            return
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.content for c in batch]
            embeddings = self._model.encode(texts, show_progress_bar=False)
            for chunk, emb in zip(batch, embeddings):
                chunk.embedding = emb.tolist()
```

- [ ] **Step 4: Run tests**

Run: `source .venv/bin/activate && pytest tests/ -v --timeout=60 -x`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/storage/vector_store.py src/context_engine/indexer/embedder.py src/context_engine/retrieval/retriever.py
git commit -m "perf: add ANN index, query embedding cache, and batch embedding"
```

---

## Task 12: Benchmarks

**Files:**
- Create: `benchmarks/run_benchmark.py`
- Create: `benchmarks/sample_queries.json`
- Create: `docs/benchmarks.md`

- [ ] **Step 1: Create sample queries**

```json
[
    {
        "query": "How does the chunker split code into chunks?",
        "expected_files": ["src/context_engine/indexer/chunker.py"]
    },
    {
        "query": "vector search implementation",
        "expected_files": ["src/context_engine/storage/vector_store.py"]
    },
    {
        "query": "confidence scoring formula",
        "expected_files": ["src/context_engine/retrieval/confidence.py"]
    },
    {
        "query": "MCP server tools",
        "expected_files": ["src/context_engine/integration/mcp_server.py"]
    },
    {
        "query": "how does indexing pipeline work",
        "expected_files": ["src/context_engine/indexer/pipeline.py"]
    },
    {
        "query": "calculate_tax function",
        "expected_files": ["src/context_engine/retrieval/retriever.py"]
    },
    {
        "query": "FTS5 full text search",
        "expected_files": ["src/context_engine/storage/fts_store.py"]
    },
    {
        "query": "graph neighbors query",
        "expected_files": ["src/context_engine/storage/graph_store.py"]
    }
]
```

Save to `benchmarks/sample_queries.json`.

- [ ] **Step 2: Create benchmark runner**

```python
# benchmarks/run_benchmark.py
"""Benchmark suite for CCE token savings, retrieval quality, and latency."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from context_engine.config import load_config
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.pipeline import run_indexing
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.storage.local_backend import LocalBackend


def _count_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def run_benchmarks(project_dir: Path, queries_path: Path) -> dict:
    config = load_config(project_dir)
    storage_base = Path(config.storage_path) / project_dir.name
    storage_base.mkdir(parents=True, exist_ok=True)

    # Index
    print("Indexing project...")
    result = await run_indexing(config, project_dir, full=True)
    print(f"  Indexed {len(result.indexed_files)} files, {result.total_chunks} chunks")

    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)

    with open(queries_path) as f:
        queries = json.load(f)

    # Token savings
    print("\n--- Token Savings ---")
    total_full = 0
    total_served = 0
    for file in project_dir.rglob("*.py"):
        if ".venv" not in str(file) and "__pycache__" not in str(file):
            try:
                total_full += _count_tokens(file.read_text(errors="ignore"))
            except OSError:
                pass

    for q in queries:
        chunks = await retriever.retrieve(q["query"], top_k=10)
        served = sum(_count_tokens(c.content) for c in chunks)
        total_served += served

    avg_served = total_served / len(queries) if queries else 0
    savings_pct = (1 - avg_served / total_full) * 100 if total_full > 0 else 0
    print(f"  Full project: {total_full:,} tokens")
    print(f"  Avg per query: {avg_served:,.0f} tokens")
    print(f"  Savings: {savings_pct:.1f}%")

    # Precision / Recall
    print("\n--- Precision@10 / Recall@10 ---")
    precision_sum = 0
    recall_sum = 0
    for q in queries:
        chunks = await retriever.retrieve(q["query"], top_k=10)
        result_files = {c.file_path for c in chunks}
        expected = set(q["expected_files"])
        hits = result_files & expected
        precision = len(hits) / len(result_files) if result_files else 0
        recall = len(hits) / len(expected) if expected else 0
        precision_sum += precision
        recall_sum += recall
        status = "HIT" if hits else "MISS"
        print(f"  [{status}] {q['query'][:50]} — P={precision:.2f} R={recall:.2f}")

    avg_precision = precision_sum / len(queries)
    avg_recall = recall_sum / len(queries)
    print(f"  Avg Precision@10: {avg_precision:.2f}")
    print(f"  Avg Recall@10: {avg_recall:.2f}")

    # Latency
    print("\n--- Latency ---")
    latencies = []
    for _ in range(3):  # Warm up
        await retriever.retrieve("test query", top_k=10)
    for q in queries:
        for _ in range(5):
            t0 = time.perf_counter()
            await retriever.retrieve(q["query"], top_k=10)
            latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"  p50: {p50:.1f}ms  p95: {p95:.1f}ms  p99: {p99:.1f}ms")

    results = {
        "token_savings_pct": round(savings_pct, 1),
        "avg_precision_at_10": round(avg_precision, 2),
        "avg_recall_at_10": round(avg_recall, 2),
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
    }
    print(f"\n{json.dumps(results, indent=2)}")
    return results


if __name__ == "__main__":
    project = Path(__file__).parent.parent
    queries = Path(__file__).parent / "sample_queries.json"
    asyncio.run(run_benchmarks(project, queries))
```

- [ ] **Step 3: Create docs/benchmarks.md placeholder**

```markdown
# CCE Benchmarks

Benchmarks run on the CCE repository itself (~200 files, ~700 chunks).

## Results

| Metric | Value |
|--------|-------|
| Token savings vs full-file reads | TBD% |
| Precision@10 | TBD |
| Recall@10 | TBD |
| Query latency p50 | TBD ms |
| Query latency p95 | TBD ms |

## Methodology

Run: `python benchmarks/run_benchmark.py`

- **Token savings**: Compare full project token count vs average tokens served per query
- **Precision/Recall**: Curated queries with known-relevant files in `benchmarks/sample_queries.json`
- **Latency**: 5 iterations per query, report percentiles (after 3 warm-up runs)
```

- [ ] **Step 4: Run benchmark to populate results**

Run: `source .venv/bin/activate && python benchmarks/run_benchmark.py`
Expected: Outputs benchmark results; update `docs/benchmarks.md` with actual numbers.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/ docs/benchmarks.md
git commit -m "feat: add benchmark suite for token savings, precision, recall, latency"
```

---

## Task 13: ONNX Runtime Migration

**Files:**
- Modify: `src/context_engine/indexer/embedder.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `src/context_engine/cli.py`

> **IMPORTANT**: This is the highest-risk change. Run benchmarks before AND after to verify embedding parity.

- [ ] **Step 1: Run pre-ONNX benchmarks**

Run: `source .venv/bin/activate && python benchmarks/run_benchmark.py > benchmarks/pre-onnx-results.txt 2>&1`
Save the precision@10 and recall@10 numbers.

- [ ] **Step 2: Update pyproject.toml dependencies**

In `pyproject.toml`, replace the dependencies list (lines 20-32):

```toml
dependencies = [
    "click>=8.1",
    "pyyaml>=6.0",
    "lancedb>=0.6",
    "optimum[onnxruntime]>=1.19",
    "onnxruntime>=1.17",
    "tokenizers>=0.19",
    "transformers>=4.41",
    "numpy>=1.24",
    "tree-sitter>=0.22",
    "tree-sitter-python>=0.21",
    "tree-sitter-javascript>=0.21",
    "tree-sitter-typescript>=0.21",
    "watchdog>=4.0",
    "mcp>=1.0",
    "httpx>=0.27",
]
```

Change `requires-python`:
```toml
requires-python = ">=3.11"
```

Add torch as optional:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
]
torch = ["sentence-transformers>=3.0"]
```

- [ ] **Step 3: Rewrite embedder for ONNX**

Replace `src/context_engine/indexer/embedder.py`:

```python
"""Embedding generation using ONNX Runtime."""
import logging
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer

from context_engine.models import Chunk

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _model_cache_dir() -> Path:
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    transformers_cache = os.environ.get("TRANSFORMERS_CACHE")
    if transformers_cache:
        return Path(transformers_cache)
    return Path.home() / ".cache" / "huggingface" / "hub"


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        resolved = f"sentence-transformers/{model_name}" if "/" not in model_name else model_name

        _noisy_loggers = [
            "transformers.modeling_utils", "transformers",
            "huggingface_hub.file_download", "huggingface_hub",
            "optimum", "onnxruntime",
        ]
        _prior_levels = {n: logging.getLogger(n).level for n in _noisy_loggers}
        for name in _noisy_loggers:
            logging.getLogger(name).setLevel(logging.ERROR)
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(resolved)
            self._model = ORTModelForFeatureExtraction.from_pretrained(
                resolved, export=True
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model '{model_name}'. "
                f"If you're offline, pre-download it once with internet access, "
                f"or set HF_HOME to point at an existing cache. Original error: {exc}"
            ) from exc
        finally:
            for name, level in _prior_levels.items():
                logging.getLogger(name).setLevel(level)

    def _mean_pool(self, last_hidden_state, attention_mask):
        mask = attention_mask[..., None].astype(np.float32)
        summed = (last_hidden_state * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        return summed / counts

    def embed(self, chunks: list[Chunk], batch_size: int = 32) -> None:
        if not chunks:
            return
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.content for c in batch]
            inputs = self._tokenizer(
                texts, padding=True, truncation=True, return_tensors="np"
            )
            outputs = self._model(**inputs)
            embeddings = self._mean_pool(
                outputs.last_hidden_state, inputs["attention_mask"]
            )
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
            embeddings = embeddings / norms
            for chunk, emb in zip(batch, embeddings):
                chunk.embedding = emb.tolist()

    @lru_cache(maxsize=256)
    def embed_query(self, query: str) -> tuple:
        inputs = self._tokenizer(query, return_tensors="np", truncation=True)
        outputs = self._model(**inputs)
        emb = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"])[0]
        emb = emb / max(float(np.linalg.norm(emb)), 1e-9)
        return tuple(emb.tolist())
```

- [ ] **Step 4: Remove Python 3.14 warning from cli.py**

In `src/context_engine/cli.py`, remove the Python 3.14 version check block (lines 9-15 approximately).

- [ ] **Step 5: Update README.md install instructions**

Replace the install section in `README.md`:

```markdown
### 1. Install

```bash
brew tap fazleelahhee/tap && brew install claude-context-engine  # macOS (recommended)
# or
uv tool install claude-context-engine                             # all platforms (recommended)
# or
pipx install claude-context-engine                                # all platforms
# or
pip install claude-context-engine                                 # inside a virtualenv
```
```

- [ ] **Step 6: Install new dependencies and run tests**

Run:
```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v --timeout=120 -x
```
Expected: ALL PASS (some tests may need adjustment for tuple vs list return from `embed_query`)

- [ ] **Step 7: Run post-ONNX benchmarks and compare**

Run: `source .venv/bin/activate && python benchmarks/run_benchmark.py > benchmarks/post-onnx-results.txt 2>&1`

Compare precision@10 and recall@10 with pre-ONNX results. Must be within ±2 percentage points.

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/indexer/embedder.py src/context_engine/cli.py pyproject.toml README.md benchmarks/
git commit -m "feat: migrate from PyTorch to ONNX Runtime — 50x smaller install"
```

---

## Task 14: Update MCP `related_context` Tool

**Files:**
- Modify: `src/context_engine/integration/mcp_server.py`

- [ ] **Step 1: Update `related_context` handler**

Replace the `_handle_related_context` method (lines 336-355) in `mcp_server.py`:

```python
    async def _handle_related_context(self, args):
        chunk_id = (args.get("chunk_id") or "").strip()
        if not chunk_id:
            return [TextContent(type="text", text="chunk_id is required.")]
        neighbors = await self._backend.graph_neighbors(chunk_id)
        if not neighbors:
            return [
                TextContent(
                    type="text",
                    text="No related context found for this chunk.",
                )
            ]
        lines = [
            f"- {n.node_type.value}: {n.name} ({n.file_path})" for n in neighbors
        ]
        return [TextContent(type="text", text="\n".join(lines))]
```

- [ ] **Step 2: Run MCP server tests**

Run: `source .venv/bin/activate && pytest tests/integration/test_mcp_server.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/context_engine/integration/mcp_server.py
git commit -m "feat: related_context MCP tool now returns real graph neighbors"
```

---

## Task 15: Create Feature Branch + Final Integration Test

- [ ] **Step 1: Create feature branch with all changes**

```bash
git checkout -b feature/cce-v02-improvements
```

(If you've been committing on main, create the branch from the first commit of this work and cherry-pick, or reset main and create the branch.)

- [ ] **Step 2: Run full test suite**

```bash
source .venv/bin/activate && pytest tests/ -v --timeout=120
```
Expected: ALL PASS

- [ ] **Step 3: Run benchmarks and update docs/benchmarks.md**

```bash
source .venv/bin/activate && python benchmarks/run_benchmark.py
```

Update `docs/benchmarks.md` with actual numbers.

- [ ] **Step 4: Run `cce init` and `cce savings` to verify end-to-end**

```bash
source .venv/bin/activate
cce init
cce savings
```

Expected: Index builds successfully with FTS + graph data; savings display works.

- [ ] **Step 5: Final commit**

```bash
git add docs/benchmarks.md
git commit -m "docs: update benchmarks with v0.2 results"
```
