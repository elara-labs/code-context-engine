# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cce dashboard` command that opens a local FastAPI-powered web dashboard for inspecting and controlling the CCE index.

**Architecture:** FastAPI + Uvicorn serve a single self-contained HTML page embedded as a Python string. Routes read directly from existing storage files (stats.json, manifest.json, sessions/*.json) and call the existing pipeline/backend layer for write operations. The CLI command finds a free port and opens the browser automatically.

**Tech Stack:** FastAPI ≥0.110, Uvicorn ≥0.29, starlette TestClient (tests), existing LocalBackend / Manifest / SessionCapture / pipeline.run_indexing.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `pyproject.toml` | Add fastapi + uvicorn to core deps |
| Modify | `src/context_engine/storage/vector_store.py` | Add `count()`, `file_chunk_counts()`, `clear()` |
| Modify | `src/context_engine/storage/local_backend.py` | Expose `count_chunks()`, `file_chunk_counts()`, `clear()` |
| Create | `src/context_engine/dashboard/__init__.py` | Empty package marker |
| Create | `src/context_engine/dashboard/server.py` | `create_app()` factory + all route handlers |
| Create | `src/context_engine/dashboard/_page.py` | `PAGE_HTML` string constant (full dark-theme SPA) |
| Modify | `src/context_engine/cli.py` | Add `dashboard` command + `_find_free_port()` helper |
| Create | `tests/dashboard/__init__.py` | Empty |
| Create | `tests/dashboard/test_server.py` | All API route tests |

---

## Task 1: Add fastapi + uvicorn to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
dependencies = [
    "click>=8.1",
    "pyyaml>=6.0",
    "lancedb>=0.6",
    "sentence-transformers>=3.0",
    "tree-sitter>=0.22",
    "tree-sitter-python>=0.21",
    "tree-sitter-javascript>=0.21",
    "tree-sitter-typescript>=0.21",
    "watchdog>=4.0",
    "mcp>=1.0",
    "httpx>=0.27",
    "fastapi>=0.110",
    "uvicorn>=0.29",
]
```

- [ ] **Step 2: Sync deps**

```bash
uv sync
```

Expected: resolves without error, installs fastapi and uvicorn.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastapi and uvicorn deps for dashboard"
```

---

## Task 2: Add storage helpers — count, file_chunk_counts, clear

**Files:**
- Modify: `src/context_engine/storage/vector_store.py`
- Modify: `src/context_engine/storage/local_backend.py`
- Create: `tests/storage/test_vector_store_helpers.py` (add to existing `tests/storage/test_vector_store.py`)

- [ ] **Step 1: Write failing tests**

Add to `tests/storage/test_vector_store.py`:

```python
def test_count_empty(tmp_path):
    vs = VectorStore(db_path=str(tmp_path / "db"))
    assert vs.count() == 0


def test_file_chunk_counts_empty(tmp_path):
    vs = VectorStore(db_path=str(tmp_path / "db"))
    assert vs.file_chunk_counts() == {}


def test_clear_resets_table(tmp_path):
    from context_engine.models import Chunk, ChunkType
    vs = VectorStore(db_path=str(tmp_path / "db"))
    chunk = Chunk(
        id="c1", content="def foo(): pass", chunk_type=ChunkType.FUNCTION,
        file_path="foo.py", start_line=1, end_line=1, language="python",
        embedding=[0.1] * 384,
    )
    import asyncio
    asyncio.run(vs.ingest([chunk]))
    assert vs.count() == 1
    vs.clear()
    assert vs.count() == 0


def test_file_chunk_counts_after_ingest(tmp_path):
    from context_engine.models import Chunk, ChunkType
    import asyncio
    vs = VectorStore(db_path=str(tmp_path / "db"))
    chunks = [
        Chunk(id="c1", content="a", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[0.1] * 384),
        Chunk(id="c2", content="b", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=2, end_line=2, language="python",
              embedding=[0.2] * 384),
        Chunk(id="c3", content="c", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.3] * 384),
    ]
    asyncio.run(vs.ingest(chunks))
    counts = vs.file_chunk_counts()
    assert counts["a.py"] == 2
    assert counts["b.py"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/storage/test_vector_store.py::test_count_empty tests/storage/test_vector_store.py::test_file_chunk_counts_empty tests/storage/test_vector_store.py::test_clear_resets_table tests/storage/test_vector_store.py::test_file_chunk_counts_after_ingest -v
```

Expected: 4 FAILED with `AttributeError: 'VectorStore' object has no attribute 'count'`

- [ ] **Step 3: Add methods to VectorStore**

In `src/context_engine/storage/vector_store.py`, add after the `delete_by_file` method:

```python
def count(self) -> int:
    """Return total number of chunks in the table."""
    with self._lock:
        if self._table is None:
            try:
                self._table = self._db.open_table(TABLE_NAME)
            except Exception:
                return 0
        try:
            return self._table.count_rows()
        except Exception:
            return 0

def file_chunk_counts(self) -> dict[str, int]:
    """Return {file_path: chunk_count} for all indexed files."""
    with self._lock:
        if self._table is None:
            try:
                self._table = self._db.open_table(TABLE_NAME)
            except Exception:
                return {}
        try:
            rows = self._table.to_arrow().to_pydict()
            counts: dict[str, int] = {}
            for fp in rows.get("file_path", []):
                counts[fp] = counts.get(fp, 0) + 1
            return counts
        except Exception:
            return {}

def clear(self) -> None:
    """Drop the chunks table, resetting the vector store."""
    with self._lock:
        if self._table is not None:
            try:
                self._db.drop_table(TABLE_NAME)
            except Exception:
                pass
            self._table = None
```

- [ ] **Step 4: Add wrappers to LocalBackend**

In `src/context_engine/storage/local_backend.py`, add after `delete_by_file`:

```python
def count_chunks(self) -> int:
    return self._vector_store.count()

def file_chunk_counts(self) -> dict[str, int]:
    return self._vector_store.file_chunk_counts()

async def clear(self) -> None:
    self._vector_store.clear()
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
PYTHONPATH=src uv run pytest tests/storage/test_vector_store.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/context_engine/storage/vector_store.py src/context_engine/storage/local_backend.py tests/storage/test_vector_store.py
git commit -m "feat: add count, file_chunk_counts, clear to VectorStore and LocalBackend"
```

---

## Task 3: Scaffold dashboard package + read-only API routes

**Files:**
- Create: `src/context_engine/dashboard/__init__.py`
- Create: `src/context_engine/dashboard/server.py`
- Create: `tests/dashboard/__init__.py`
- Create: `tests/dashboard/test_server.py`

- [ ] **Step 1: Write failing tests for GET / and /api/status**

Create `tests/dashboard/__init__.py` (empty).

Create `tests/dashboard/test_server.py`:

```python
"""Tests for the CCE dashboard FastAPI server."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from context_engine.config import Config
from context_engine.dashboard.server import create_app


def _setup_storage(tmp_path: Path, project_name: str = "my-project") -> tuple[Path, Path]:
    """Create storage dir with stats.json and manifest.json; return (storage_root, project_dir)."""
    project_dir = tmp_path / "workspace" / project_name
    project_dir.mkdir(parents=True)
    storage_base = tmp_path / "storage" / project_name
    storage_base.mkdir(parents=True)
    return storage_base, project_dir


def _make_client(tmp_path: Path, project_name: str = "my-project") -> tuple[TestClient, Path]:
    storage_base, project_dir = _setup_storage(tmp_path, project_name)
    config = Config(storage_path=str(tmp_path / "storage"))
    app = create_app(config, project_dir)
    return TestClient(app), storage_base


def test_get_root_returns_html(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()


def test_status_no_data(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["initialized"] is False
    assert data["chunks"] == 0
    assert data["files"] == 0
    assert data["queries"] == 0
    assert data["tokens_saved_pct"] == 0
    assert data["output_level"] == "standard"


def test_status_with_stats(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 38, "full_file_tokens": 48000, "served_tokens": 14200, "raw_tokens": 14200}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"src/cli.py": "abc123", "src/config.py": "def456"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["initialized"] is True
    assert data["files"] == 2
    assert data["queries"] == 38
    assert data["tokens_saved_pct"] == 70


def test_status_with_custom_output_level(tmp_path):
    client, storage_base = _make_client(tmp_path)
    (storage_base / "state.json").write_text(json.dumps({"output_level": "max"}))
    r = client.get("/api/status")
    assert r.json()["output_level"] == "max"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'context_engine.dashboard'`

- [ ] **Step 3: Create package files**

Create `src/context_engine/dashboard/__init__.py` (empty file).

Create `src/context_engine/dashboard/server.py`:

```python
"""FastAPI dashboard server for CCE index inspection."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


def create_app(config, project_dir: Path) -> FastAPI:
    """Build and return the FastAPI application.

    All route handlers close over `storage_base` and `project_dir` so the
    app is self-contained and trivial to test with TestClient.
    """
    project_name = project_dir.name
    storage_base = Path(config.storage_path) / project_name

    app = FastAPI(title="CCE Dashboard", docs_url=None, redoc_url=None)

    # ── helpers ────────────────────────────────────────────────────────────

    def _read_json(path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _read_manifest() -> dict[str, str]:
        return _read_json(storage_base / "manifest.json")

    def _read_stats() -> dict:
        return _read_json(storage_base / "stats.json")

    def _read_state() -> dict:
        return _read_json(storage_base / "state.json")

    def _read_sessions(limit: int = 20) -> list[dict]:
        sessions_dir = storage_base / "sessions"
        if not sessions_dir.exists():
            return []
        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for f in files[:limit]:
            try:
                result.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                pass
        return result

    # ── routes ─────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def serve_page() -> str:
        from context_engine.dashboard._page import PAGE_HTML
        return PAGE_HTML

    @app.get("/api/status")
    async def get_status() -> dict:
        from context_engine.storage.local_backend import LocalBackend

        stats = _read_stats()
        manifest = _read_manifest()
        state = _read_state()

        backend = LocalBackend(base_path=str(storage_base))
        chunks = backend.count_chunks()

        full_file = stats.get("full_file_tokens", 0)
        served = stats.get("served_tokens", 0)
        baseline = full_file if full_file > 0 else stats.get("raw_tokens", 0)
        saved_pct = int((1 - served / baseline) * 100) if baseline > 0 else 0

        output_level = state.get("output_level", config.output_compression)

        return {
            "project": project_name,
            "initialized": bool(manifest),
            "chunks": chunks,
            "files": len(manifest),
            "queries": stats.get("queries", 0),
            "tokens_saved_pct": saved_pct,
            "output_level": output_level,
        }

    return app
```

- [ ] **Step 4: Create `_page.py` placeholder**

Create `src/context_engine/dashboard/_page.py`:

```python
"""Embedded HTML page for the CCE dashboard."""

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>CCE Dashboard</title></head>
<body><h1>CCE Dashboard</h1><p>Loading...</p></body>
</html>"""
```

(This will be replaced with the full page in Task 6.)

- [ ] **Step 5: Run tests to confirm they pass**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add src/context_engine/dashboard/ tests/dashboard/
git commit -m "feat: scaffold dashboard package with GET / and /api/status"
```

---

## Task 4: /api/files route

**Files:**
- Modify: `src/context_engine/dashboard/server.py`
- Modify: `tests/dashboard/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/dashboard/test_server.py`:

```python
def test_files_empty(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/files")
    assert r.status_code == 200
    assert r.json() == []


def test_files_with_manifest(tmp_path):
    client, storage_base = _make_client(tmp_path)
    # Create project files on disk matching the manifest
    project_dir = tmp_path / "workspace" / "my-project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)

    content = "def foo(): pass\n"
    import hashlib
    h = hashlib.sha256(content.encode()).hexdigest()
    (project_dir / "src" / "cli.py").write_text(content)

    manifest = {"src/cli.py": h}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    assert r.status_code == 200
    files = r.json()
    assert len(files) == 1
    assert files[0]["path"] == "src/cli.py"
    assert files[0]["status"] == "ok"
    assert files[0]["chunks"] == 0  # no LanceDB table in this test


def test_files_stale_detection(tmp_path):
    client, storage_base = _make_client(tmp_path)
    project_dir = tmp_path / "workspace" / "my-project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)
    (project_dir / "src" / "cli.py").write_text("def foo(): pass\n")

    # Manifest has a different hash → stale
    manifest = {"src/cli.py": "oldhash000"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    files = r.json()
    assert files[0]["status"] == "stale"


def test_files_missing_detection(tmp_path):
    client, storage_base = _make_client(tmp_path)
    # File in manifest but NOT on disk
    manifest = {"src/gone.py": "somehash"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    files = r.json()
    assert files[0]["status"] == "missing"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py::test_files_empty tests/dashboard/test_server.py::test_files_with_manifest tests/dashboard/test_server.py::test_files_stale_detection tests/dashboard/test_server.py::test_files_missing_detection -v
```

Expected: 4 FAILED with `404 Not Found`

- [ ] **Step 3: Add /api/files route**

In `src/context_engine/dashboard/server.py`, add after `get_status`, inside `create_app`:

```python
    @app.get("/api/files")
    async def get_files() -> list:
        import hashlib
        from context_engine.storage.local_backend import LocalBackend

        manifest = _read_manifest()
        if not manifest:
            return []

        backend = LocalBackend(base_path=str(storage_base))
        chunk_counts = backend.file_chunk_counts()

        result = []
        for rel_path, stored_hash in sorted(manifest.items()):
            abs_path = project_dir / rel_path
            if not abs_path.exists():
                status = "missing"
            else:
                try:
                    current = abs_path.read_text(encoding="utf-8", errors="strict")
                    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
                    status = "ok" if current_hash == stored_hash else "stale"
                except (UnicodeDecodeError, OSError):
                    status = "ok"  # binary file, trust the manifest
            result.append({
                "path": rel_path,
                "chunks": chunk_counts.get(rel_path, 0),
                "status": status,
            })
        return result
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/dashboard/server.py tests/dashboard/test_server.py
git commit -m "feat: add /api/files route with staleness detection"
```

---

## Task 5: /api/sessions, /api/savings, /api/export routes

**Files:**
- Modify: `src/context_engine/dashboard/server.py`
- Modify: `tests/dashboard/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/dashboard/test_server.py`:

```python
def test_sessions_empty(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_sessions_returns_persisted(tmp_path):
    client, storage_base = _make_client(tmp_path)
    sessions_dir = storage_base / "sessions"
    sessions_dir.mkdir(parents=True)
    session = {
        "id": "abc123", "project": "my-project", "started_at": 1700000000.0,
        "ended_at": 1700000120.0,
        "decisions": [{"decision": "use JWT", "reason": "stateless", "timestamp": 1700000060.0}],
        "code_areas": [],
        "questions": [],
    }
    (sessions_dir / "abc123.json").write_text(json.dumps(session))
    r = client.get("/api/sessions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert len(data[0]["decisions"]) == 1


def test_savings_no_data(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/savings")
    assert r.status_code == 200
    data = r.json()
    assert data["queries"] == 0
    assert data["tokens_saved"] == 0
    assert data["savings_pct"] == 0


def test_savings_with_data(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 38, "full_file_tokens": 48000, "served_tokens": 14200, "raw_tokens": 14200}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    r = client.get("/api/savings")
    data = r.json()
    assert data["queries"] == 38
    assert data["served_tokens"] == 14200
    assert data["baseline_tokens"] == 48000
    assert data["tokens_saved"] == 33800
    assert data["savings_pct"] == 70


def test_export_returns_combined(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 5, "full_file_tokens": 1000, "served_tokens": 300, "raw_tokens": 300}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"foo.py": "hash1"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))
    r = client.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    data = r.json()
    assert "stats" in data
    assert "manifest" in data
    assert "sessions" in data
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py::test_sessions_empty tests/dashboard/test_server.py::test_sessions_returns_persisted tests/dashboard/test_server.py::test_savings_no_data tests/dashboard/test_server.py::test_savings_with_data tests/dashboard/test_server.py::test_export_returns_combined -v
```

Expected: 5 FAILED with `404 Not Found`

- [ ] **Step 3: Add routes to server.py**

Inside `create_app` in `src/context_engine/dashboard/server.py`, add after `get_files`:

```python
    @app.get("/api/sessions")
    async def get_sessions() -> list:
        return _read_sessions()

    @app.get("/api/savings")
    async def get_savings() -> dict:
        stats = _read_stats()
        full_file = stats.get("full_file_tokens", 0)
        served = stats.get("served_tokens", 0)
        raw = stats.get("raw_tokens", 0)
        baseline = full_file if full_file > 0 else raw
        saved = max(0, baseline - served)
        pct = int(saved / baseline * 100) if baseline > 0 else 0
        return {
            "queries": stats.get("queries", 0),
            "baseline_tokens": baseline,
            "served_tokens": served,
            "tokens_saved": saved,
            "savings_pct": pct,
        }

    @app.get("/api/export")
    async def export_data():
        from fastapi.responses import Response
        payload = {
            "project": project_name,
            "stats": _read_stats(),
            "manifest": _read_manifest(),
            "sessions": _read_sessions(),
        }
        return Response(
            content=json.dumps(payload, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={project_name}-cce-export.json"},
        )
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/dashboard/server.py tests/dashboard/test_server.py
git commit -m "feat: add /api/sessions, /api/savings, /api/export routes"
```

---

## Task 6: Action routes — reindex, clear, delete, compression

**Files:**
- Modify: `src/context_engine/dashboard/server.py`
- Modify: `tests/dashboard/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/dashboard/test_server.py`:

```python
from unittest.mock import AsyncMock, patch, MagicMock


def test_reindex_full(tmp_path):
    client, _ = _make_client(tmp_path)
    mock_result = MagicMock(total_chunks=10, indexed_files=["a.py"], errors=[],
                            deleted_files=[], skipped_files=[])
    with patch("context_engine.dashboard.server.run_indexing", new=AsyncMock(return_value=mock_result)):
        r = client.post("/api/reindex", json={"full": True})
    assert r.status_code == 200
    data = r.json()
    assert data["total_chunks"] == 10
    assert data["indexed_files"] == ["a.py"]
    assert data["errors"] == []


def test_reindex_single_file(tmp_path):
    client, _ = _make_client(tmp_path)
    mock_result = MagicMock(total_chunks=3, indexed_files=["src/cli.py"], errors=[],
                            deleted_files=[], skipped_files=[])
    with patch("context_engine.dashboard.server.run_indexing", new=AsyncMock(return_value=mock_result)):
        r = client.post("/api/reindex/src%2Fcli.py", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["indexed_files"] == ["src/cli.py"]


def test_clear_index(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 5, "raw_tokens": 1000, "served_tokens": 300, "full_file_tokens": 1000}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"foo.py": "hash1"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    with patch("context_engine.dashboard.server.LocalBackend") as mock_backend_cls:
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend
        r = client.post("/api/clear")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    # manifest and stats should be cleared
    assert json.loads((storage_base / "manifest.json").read_text()) == {}
    cleared_stats = json.loads((storage_base / "stats.json").read_text())
    assert cleared_stats["queries"] == 0


def test_delete_file(tmp_path):
    client, storage_base = _make_client(tmp_path)
    manifest = {"src/cli.py": "hash1", "src/config.py": "hash2"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    with patch("context_engine.dashboard.server.LocalBackend") as mock_backend_cls:
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend
        r = client.delete("/api/files/src%2Fcli.py")

    assert r.status_code == 200
    remaining = json.loads((storage_base / "manifest.json").read_text())
    assert "src/cli.py" not in remaining
    assert "src/config.py" in remaining


def test_set_compression(tmp_path):
    client, storage_base = _make_client(tmp_path)
    r = client.post("/api/compression", json={"level": "max"})
    assert r.status_code == 200
    assert r.json()["level"] == "max"
    saved = json.loads((storage_base / "state.json").read_text())
    assert saved["output_level"] == "max"


def test_set_compression_invalid(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/compression", json={"level": "turbo"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py::test_reindex_full tests/dashboard/test_server.py::test_reindex_single_file tests/dashboard/test_server.py::test_clear_index tests/dashboard/test_server.py::test_delete_file tests/dashboard/test_server.py::test_set_compression tests/dashboard/test_server.py::test_set_compression_invalid -v
```

Expected: 6 FAILED with `405 Method Not Allowed` or `404 Not Found`

- [ ] **Step 3: Add imports at top of server.py**

At the top of `src/context_engine/dashboard/server.py`, update imports to:

```python
"""FastAPI dashboard server for CCE index inspection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend
```

- [ ] **Step 4: Add request models and action routes**

Inside `create_app` in `server.py`, after `export_data`, add:

```python
    # ── action routes ──────────────────────────────────────────────────────

    class ReindexRequest(BaseModel):
        full: bool = False

    class CompressionRequest(BaseModel):
        level: Literal["off", "lite", "standard", "max"]

    @app.post("/api/reindex")
    async def reindex(req: ReindexRequest) -> dict:
        result = await run_indexing(
            config, project_dir, full=req.full
        )
        return {
            "total_chunks": result.total_chunks,
            "indexed_files": result.indexed_files,
            "deleted_files": result.deleted_files,
            "skipped_files": result.skipped_files,
            "errors": result.errors,
        }

    @app.post("/api/reindex/{file_path:path}")
    async def reindex_file(file_path: str) -> dict:
        result = await run_indexing(
            config, project_dir, target_path=file_path
        )
        return {
            "total_chunks": result.total_chunks,
            "indexed_files": result.indexed_files,
            "deleted_files": result.deleted_files,
            "skipped_files": result.skipped_files,
            "errors": result.errors,
        }

    @app.post("/api/clear")
    async def clear_index() -> dict:
        backend = LocalBackend(base_path=str(storage_base))
        await backend.clear()
        # Reset manifest
        manifest_path = storage_base / "manifest.json"
        manifest_path.write_text(json.dumps({}))
        # Reset stats
        stats_path = storage_base / "stats.json"
        stats_path.write_text(json.dumps(
            {"queries": 0, "raw_tokens": 0, "served_tokens": 0, "full_file_tokens": 0}
        ))
        return {"ok": True}

    @app.delete("/api/files/{file_path:path}")
    async def delete_file(file_path: str) -> dict:
        backend = LocalBackend(base_path=str(storage_base))
        await backend.delete_by_file(file_path)
        # Remove from manifest
        manifest_path = storage_base / "manifest.json"
        manifest = _read_manifest()
        manifest.pop(file_path, None)
        manifest_path.write_text(json.dumps(manifest))
        return {"ok": True, "deleted": file_path}

    @app.post("/api/compression")
    async def set_compression(req: CompressionRequest) -> dict:
        state_path = storage_base / "state.json"
        state = _read_state()
        state["output_level"] = req.level
        state_path.write_text(json.dumps(state))
        return {"level": req.level}
```

- [ ] **Step 5: Remove the duplicate `LocalBackend` import inside `get_status` and `get_files`**

The `LocalBackend` is now imported at module level (top of file). Remove the `from context_engine.storage.local_backend import LocalBackend` lines from inside `get_status` and `get_files` route handlers.

- [ ] **Step 6: Run all dashboard tests**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: all PASS

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
PYTHONPATH=src uv run pytest tests/ -x --tb=short
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/dashboard/server.py tests/dashboard/test_server.py
git commit -m "feat: add action routes — reindex, clear, delete, compression"
```

---

## Task 7: Build the full HTML page

**Files:**
- Modify: `src/context_engine/dashboard/_page.py`

- [ ] **Step 1: Replace placeholder with full dark-theme SPA**

Replace the contents of `src/context_engine/dashboard/_page.py` with:

```python
"""Embedded HTML page for the CCE dashboard.

Single-file SPA. Fetches data from /api/* on tab switch.
Polls /api/status every 5 seconds for live updates.
No external dependencies — all CSS and JS inline.
"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CCE Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #c9d1d9; font-family: system-ui, -apple-system, sans-serif; font-size: 14px; }
a { color: #58a6ff; text-decoration: none; }
button { cursor: pointer; }

/* Layout */
.nav { background: #161b22; border-bottom: 1px solid #30363d; padding: 0 20px; display: flex; align-items: center; gap: 24px; }
.nav-brand { color: #e2eeff; font-weight: 700; font-size: 15px; padding: 12px 0; letter-spacing: 2px; }
.nav-project { color: #8b949e; font-size: 12px; }
.tabs { display: flex; margin-left: auto; }
.tab { padding: 12px 16px; font-size: 13px; color: #8b949e; background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer; }
.tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
.tab:hover:not(.active) { color: #c9d1d9; }
.main { padding: 24px 20px; max-width: 1100px; }

/* Cards */
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
.stat-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.stat-value { font-size: 26px; font-weight: 700; margin-bottom: 4px; }
.stat-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.4px; }
.green { color: #3fb950; } .blue { color: #58a6ff; } .yellow { color: #e3b341; } .purple { color: #a5d6ff; }

/* Panels */
.panel-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.panel-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; margin-bottom: 12px; }

/* Badge */
.badge { display: inline-block; padding: 2px 9px; border-radius: 12px; font-size: 11px; font-weight: 500; }
.badge-ok { background: #1a3a1a; color: #3fb950; }
.badge-stale { background: #3a2e1a; color: #e3b341; }
.badge-missing { background: #2a1a1a; color: #f85149; }
.badge-active { background: #1a2e3a; color: #58a6ff; }
.badge-closed { background: #21262d; color: #8b949e; }

/* Health rows */
.health-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #21262d; }
.health-row:last-child { border-bottom: none; }

/* Buttons */
.btn { padding: 6px 14px; border-radius: 6px; font-size: 12px; border: none; }
.btn-primary { background: #1f6feb; color: #fff; }
.btn-primary:hover { background: #388bfd; }
.btn-secondary { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.btn-secondary:hover { background: #30363d; }
.btn-danger { background: #da3633; color: #fff; }
.btn-danger:hover { background: #f85149; }
.btn-icon { background: #21262d; border: 1px solid #30363d; color: #8b949e; padding: 3px 8px; border-radius: 5px; font-size: 12px; }
.btn-icon:hover { color: #c9d1d9; }
.btn-row { display: flex; gap: 8px; margin-top: 14px; padding-top: 12px; border-top: 1px solid #21262d; }

/* Table */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.search-input { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 7px 12px; border-radius: 6px; font-size: 13px; outline: none; flex: 1; max-width: 280px; }
.search-input:focus { border-color: #58a6ff; }
.table { background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
.table-head { display: grid; grid-template-columns: 3fr 80px 90px 80px; padding: 8px 14px; background: #21262d; font-size: 10px; text-transform: uppercase; letter-spacing: 0.4px; color: #8b949e; gap: 10px; }
.table-row { display: grid; grid-template-columns: 3fr 80px 90px 80px; padding: 9px 14px; border-top: 1px solid #21262d; align-items: center; gap: 10px; font-size: 13px; }
.table-row:hover { background: #21262d22; }
.file-path { color: #58a6ff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
.row-actions { display: flex; gap: 5px; }
.no-data { color: #8b949e; text-align: center; padding: 32px; font-size: 13px; }

/* Sessions */
.session-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; margin-bottom: 10px; }
.session-header { display: flex; justify-content: space-between; align-items: flex-start; cursor: pointer; }
.session-name { font-size: 14px; font-weight: 600; color: #c9d1d9; }
.session-meta { font-size: 11px; color: #8b949e; margin-top: 3px; }
.session-body { display: none; margin-top: 12px; padding-top: 12px; border-top: 1px solid #21262d; }
.session-body.open { display: block; }
.decision-item { background: #21262d; border-radius: 5px; padding: 6px 10px; font-size: 12px; margin-bottom: 5px; }

/* Savings */
.savings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.bar-label { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
.bar-track { background: #21262d; border-radius: 4px; height: 8px; margin-bottom: 12px; }
.bar-fill { height: 8px; border-radius: 4px; }
.comp-buttons { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.comp-btn { padding: 5px 12px; border-radius: 5px; font-size: 12px; background: #21262d; border: 1px solid #30363d; color: #8b949e; }
.comp-btn.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }

/* Toast */
.toast { position: fixed; bottom: 20px; right: 20px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 16px; font-size: 13px; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 100; }
.toast.show { opacity: 1; }

/* Spinner */
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Banner */
.banner { background: #1c2d40; border: 1px solid #1f6feb; border-radius: 8px; padding: 14px 18px; color: #79c0ff; font-size: 13px; margin-bottom: 20px; }
</style>
</head>
<body>

<div class="nav">
  <span class="nav-brand">CCE</span>
  <span class="nav-project" id="nav-project">loading...</span>
  <div class="tabs">
    <button class="tab active" onclick="showTab('overview')">Overview</button>
    <button class="tab" onclick="showTab('files')">Files</button>
    <button class="tab" onclick="showTab('sessions')">Sessions</button>
    <button class="tab" onclick="showTab('savings')">Savings</button>
  </div>
</div>

<div class="main">

  <!-- Overview -->
  <div id="tab-overview">
    <div id="uninit-banner" class="banner" style="display:none">
      Index not initialised — run <code>cce init</code> in your project first.
    </div>
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-value green" id="stat-chunks">—</div><div class="stat-label">Chunks indexed</div></div>
      <div class="stat-card"><div class="stat-value blue" id="stat-files">—</div><div class="stat-label">Files indexed</div></div>
      <div class="stat-card"><div class="stat-value yellow" id="stat-queries">—</div><div class="stat-label">Queries run</div></div>
      <div class="stat-card"><div class="stat-value purple" id="stat-saved">—</div><div class="stat-label">Tokens saved</div></div>
    </div>
    <div class="panel-row">
      <div class="panel">
        <div class="panel-title">Index Health</div>
        <div id="health-rows"><div class="no-data">Loading...</div></div>
        <div class="btn-row">
          <button class="btn btn-primary" onclick="doReindex(false)" id="btn-reindex-changed">Reindex changed</button>
          <button class="btn btn-secondary" onclick="doReindex(true)" id="btn-reindex-full">Full reindex</button>
        </div>
      </div>
      <div class="panel">
        <div class="panel-title">Recent Sessions</div>
        <div id="recent-sessions"><div class="no-data">Loading...</div></div>
      </div>
    </div>
  </div>

  <!-- Files -->
  <div id="tab-files" style="display:none">
    <div class="toolbar">
      <input class="search-input" placeholder="Filter files..." oninput="filterFiles(this.value)" id="file-filter">
      <button class="btn btn-secondary" onclick="doExport()">Export JSON</button>
      <button class="btn btn-danger" onclick="doClear()">Clear index</button>
    </div>
    <div class="table">
      <div class="table-head"><div>File</div><div>Chunks</div><div>Status</div><div></div></div>
      <div id="file-rows"><div class="no-data">Loading...</div></div>
    </div>
  </div>

  <!-- Sessions -->
  <div id="tab-sessions" style="display:none">
    <div id="session-list"><div class="no-data">Loading...</div></div>
  </div>

  <!-- Savings -->
  <div id="tab-savings" style="display:none">
    <div class="savings-grid">
      <div class="panel">
        <div class="panel-title">Token Usage</div>
        <div id="savings-detail"><div class="no-data">Loading...</div></div>
      </div>
      <div class="panel">
        <div class="panel-title">Output Compression</div>
        <div style="font-size:12px;color:#8b949e;margin-bottom:8px;">Controls how Claude formats responses</div>
        <div class="comp-buttons" id="comp-buttons">
          <button class="comp-btn" onclick="setCompression('off')">off</button>
          <button class="comp-btn" onclick="setCompression('lite')">lite</button>
          <button class="comp-btn" onclick="setCompression('standard')">standard</button>
          <button class="comp-btn" onclick="setCompression('max')">max</button>
        </div>
      </div>
    </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
const API = '';
let allFiles = [];
let currentOutputLevel = 'standard';

// ── tab switching ──────────────────────────────────────────────────────────

function showTab(name) {
  ['overview','files','sessions','savings'].forEach(t => {
    document.getElementById('tab-' + t).style.display = t === name ? 'block' : 'none';
  });
  document.querySelectorAll('.tab').forEach((el, i) => {
    const names = ['overview','files','sessions','savings'];
    el.classList.toggle('active', names[i] === name);
  });
  if (name === 'files') loadFiles();
  if (name === 'sessions') loadSessions();
  if (name === 'savings') loadSavings();
}

// ── toast ──────────────────────────────────────────────────────────────────

function toast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), duration);
}

// ── helpers ────────────────────────────────────────────────────────────────

function reltime(ts) {
  const diff = Math.floor((Date.now() / 1000) - ts);
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

// ── status (polling) ──────────────────────────────────────────────────────

async function loadStatus() {
  try {
    const r = await fetch(API + '/api/status');
    const d = await r.json();
    document.getElementById('nav-project').textContent = d.project || '';
    document.getElementById('stat-chunks').textContent = d.chunks.toLocaleString();
    document.getElementById('stat-files').textContent = d.files.toLocaleString();
    document.getElementById('stat-queries').textContent = d.queries.toLocaleString();
    document.getElementById('stat-saved').textContent = d.tokens_saved_pct + '%';
    document.getElementById('uninit-banner').style.display = d.initialized ? 'none' : 'block';
    currentOutputLevel = d.output_level;
    updateCompButtons(d.output_level);
    loadHealthAndSessions();
  } catch (e) {}
}

async function loadHealthAndSessions() {
  try {
    const r = await fetch(API + '/api/files');
    const files = await r.json();
    const ok = files.filter(f => f.status === 'ok').length;
    const stale = files.filter(f => f.status === 'stale').length;
    const missing = files.filter(f => f.status === 'missing').length;
    const hr = document.getElementById('health-rows');
    hr.innerHTML = [
      ['Up to date', ok, 'ok'],
      ['Stale', stale, 'stale'],
      ['Missing', missing, 'missing'],
    ].map(([label, count, cls]) =>
      '<div class="health-row"><span>' + label + '</span>' +
      '<span class="badge badge-' + cls + '">' + count + ' files</span></div>'
    ).join('');
  } catch (e) {}

  try {
    const r = await fetch(API + '/api/sessions');
    const sessions = await r.json();
    const el = document.getElementById('recent-sessions');
    if (!sessions.length) { el.innerHTML = '<div class="no-data">No sessions yet</div>'; return; }
    el.innerHTML = sessions.slice(0, 5).map(s =>
      '<div style="border-bottom:1px solid #21262d;padding:6px 0;">' +
      '<div style="font-size:13px;color:#c9d1d9;">' + (s.project || s.id) + '</div>' +
      '<div style="font-size:11px;color:#8b949e;">' +
      (s.decisions || []).length + ' decisions · ' +
      (s.code_areas || []).length + ' code areas' +
      (s.started_at ? ' · ' + reltime(s.started_at) : '') +
      '</div></div>'
    ).join('');
  } catch (e) {}
}

// ── files tab ─────────────────────────────────────────────────────────────

async function loadFiles() {
  const el = document.getElementById('file-rows');
  el.innerHTML = '<div class="no-data"><div class="spinner"></div></div>';
  try {
    const r = await fetch(API + '/api/files');
    allFiles = await r.json();
    renderFiles(allFiles);
  } catch (e) {
    el.innerHTML = '<div class="no-data">Failed to load files</div>';
  }
}

function renderFiles(files) {
  const el = document.getElementById('file-rows');
  if (!files.length) { el.innerHTML = '<div class="no-data">No files indexed yet</div>'; return; }
  el.innerHTML = files.map(f =>
    '<div class="table-row">' +
    '<div class="file-path" title="' + f.path + '">' + f.path + '</div>' +
    '<div style="color:#8b949e">' + f.chunks + '</div>' +
    '<div><span class="badge badge-' + f.status + '">' + f.status + '</span></div>' +
    '<div class="row-actions">' +
    '<button class="btn-icon" title="Reindex" onclick="reindexFile(' + JSON.stringify(f.path) + ')">↺</button>' +
    '<button class="btn-icon" style="color:#f85149" title="Delete" onclick="deleteFile(' + JSON.stringify(f.path) + ')">✕</button>' +
    '</div></div>'
  ).join('');
}

function filterFiles(query) {
  const q = query.toLowerCase();
  renderFiles(q ? allFiles.filter(f => f.path.toLowerCase().includes(q)) : allFiles);
}

// ── sessions tab ──────────────────────────────────────────────────────────

async function loadSessions() {
  const el = document.getElementById('session-list');
  el.innerHTML = '<div class="no-data"><div class="spinner"></div></div>';
  try {
    const r = await fetch(API + '/api/sessions');
    const sessions = await r.json();
    if (!sessions.length) { el.innerHTML = '<div class="no-data">No sessions recorded yet</div>'; return; }
    el.innerHTML = sessions.map((s, i) => {
      const isActive = !s.ended_at;
      const decisions = s.decisions || [];
      const codeAreas = s.code_areas || [];
      return '<div class="session-card">' +
        '<div class="session-header" onclick="toggleSession(' + i + ')">' +
        '<div><div class="session-name">' + (s.project || s.id) + '</div>' +
        '<div class="session-meta">' + decisions.length + ' decisions · ' + codeAreas.length + ' code areas' +
        (s.started_at ? ' · ' + reltime(s.started_at) : '') + '</div></div>' +
        '<span class="badge ' + (isActive ? 'badge-active' : 'badge-closed') + '">' + (isActive ? 'active' : 'closed') + '</span>' +
        '</div>' +
        (decisions.length ? '<div class="session-body" id="sb-' + i + '">' +
          '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:#8b949e;margin-bottom:6px;">Decisions</div>' +
          decisions.map(d => '<div class="decision-item">' + d.decision + '</div>').join('') +
          '</div>' : '') +
        '</div>';
    }).join('');
  } catch (e) {
    el.innerHTML = '<div class="no-data">Failed to load sessions</div>';
  }
}

function toggleSession(i) {
  const el = document.getElementById('sb-' + i);
  if (el) el.classList.toggle('open');
}

// ── savings tab ───────────────────────────────────────────────────────────

async function loadSavings() {
  try {
    const r = await fetch(API + '/api/savings');
    const d = await r.json();
    const el = document.getElementById('savings-detail');
    const usedPct = d.baseline_tokens > 0 ? Math.round(d.served_tokens / d.baseline_tokens * 100) : 0;
    el.innerHTML =
      '<div class="bar-label"><span style="color:#c9d1d9">With CCE</span><span style="color:#58a6ff">' + (d.served_tokens || 0).toLocaleString() + '</span></div>' +
      '<div class="bar-track"><div class="bar-fill" style="background:#58a6ff;width:' + usedPct + '%"></div></div>' +
      '<div class="bar-label"><span style="color:#8b949e">Without CCE</span><span style="color:#8b949e">' + (d.baseline_tokens || 0).toLocaleString() + '</span></div>' +
      '<div class="bar-track"><div class="bar-fill" style="background:#30363d;width:100%"></div></div>' +
      '<div style="display:flex;justify-content:space-between;padding-top:10px;border-top:1px solid #21262d;font-size:13px;">' +
      '<span style="color:#8b949e">Saved</span>' +
      '<span style="color:#3fb950;font-weight:600">' + (d.tokens_saved || 0).toLocaleString() + ' tokens (' + (d.savings_pct || 0) + '%)</span>' +
      '</div>';
  } catch (e) {}
  updateCompButtons(currentOutputLevel);
}

function updateCompButtons(level) {
  document.querySelectorAll('.comp-btn').forEach(btn => {
    btn.classList.toggle('active', btn.textContent === level);
  });
}

// ── actions ───────────────────────────────────────────────────────────────

async function doReindex(full) {
  const btn = document.getElementById(full ? 'btn-reindex-full' : 'btn-reindex-changed');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    const r = await fetch(API + '/api/reindex', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({full})
    });
    const d = await r.json();
    if (d.errors && d.errors.length) toast('Reindex errors: ' + d.errors[0]);
    else toast('Reindexed ' + d.indexed_files.length + ' files (' + d.total_chunks + ' chunks)');
    loadStatus();
  } catch (e) {
    toast('Reindex failed');
  } finally {
    btn.disabled = false;
    btn.textContent = full ? 'Full reindex' : 'Reindex changed';
  }
}

async function reindexFile(path) {
  try {
    const r = await fetch(API + '/api/reindex/' + encodeURIComponent(path), {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    const d = await r.json();
    toast('Reindexed ' + path);
    loadFiles();
    loadStatus();
  } catch (e) { toast('Failed'); }
}

async function deleteFile(path) {
  if (!confirm('Remove ' + path + ' from the index?')) return;
  try {
    await fetch(API + '/api/files/' + encodeURIComponent(path), {method: 'DELETE'});
    toast('Deleted ' + path);
    loadFiles();
    loadStatus();
  } catch (e) { toast('Failed'); }
}

async function doClear() {
  if (!confirm('Clear the entire index? This cannot be undone.')) return;
  try {
    await fetch(API + '/api/clear', {method: 'POST'});
    toast('Index cleared');
    loadStatus();
    loadFiles();
  } catch (e) { toast('Failed'); }
}

async function doExport() {
  window.location.href = API + '/api/export';
}

async function setCompression(level) {
  try {
    await fetch(API + '/api/compression', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({level})
    });
    currentOutputLevel = level;
    updateCompButtons(level);
    toast('Compression set to ' + level);
  } catch (e) { toast('Failed'); }
}

// ── init ──────────────────────────────────────────────────────────────────

loadStatus();
setInterval(loadStatus, 5000);
</script>
</body>
</html>"""
```

- [ ] **Step 2: Verify GET / returns the full page**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py::test_get_root_returns_html -v
```

Expected: PASS (the page now contains full HTML)

- [ ] **Step 3: Run full test suite**

```bash
PYTHONPATH=src uv run pytest tests/ -x --tb=short
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/context_engine/dashboard/_page.py
git commit -m "feat: add full dark-theme dashboard HTML page"
```

---

## Task 8: Add `cce dashboard` CLI command

**Files:**
- Modify: `src/context_engine/cli.py`
- Modify: `tests/dashboard/test_server.py` (add CLI tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/dashboard/test_server.py`:

```python
def test_dashboard_command_no_browser(tmp_path, monkeypatch):
    """cce dashboard --no-browser starts the server and exits cleanly when interrupted."""
    import threading
    import time
    import requests
    from click.testing import CliRunner
    from context_engine.cli import main
    from unittest.mock import patch

    config = Config(storage_path=str(tmp_path / "storage"))
    runner = CliRunner()

    port = None
    started = threading.Event()
    result_holder = {}

    def run_dashboard():
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=tmp_path / "workspace" / "proj"):
            (tmp_path / "workspace" / "proj").mkdir(parents=True, exist_ok=True)
            # Run in isolated thread; we'll kill it after confirming startup
            import uvicorn
            original_run = uvicorn.run
            def patched_run(app, **kwargs):
                nonlocal port
                port = kwargs.get("port", 8000)
                started.set()
                # Don't actually start uvicorn in tests
            with patch("uvicorn.run", side_effect=patched_run):
                result = runner.invoke(main, ["dashboard", "--no-browser"])
                result_holder["result"] = result

    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
    started.wait(timeout=5)
    t.join(timeout=2)

    r = result_holder.get("result")
    assert r is not None
    assert r.exit_code == 0
    assert port is not None
    assert "localhost:" in r.output


def test_find_free_port():
    from context_engine.cli import _find_free_port
    port = _find_free_port()
    assert 1024 < port < 65535
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py::test_dashboard_command_no_browser tests/dashboard/test_server.py::test_find_free_port -v
```

Expected: FAILED — `_find_free_port` not found, `dashboard` command not found.

- [ ] **Step 3: Add `_find_free_port` and `dashboard` command to cli.py**

In `src/context_engine/cli.py`, add after the existing imports:

```python
import socket
```

Add `_find_free_port` helper function before the `main` click group:

```python
def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

Add the `dashboard` command after the `serve` command:

```python
@main.command()
@click.option("--port", default=0, type=int, help="Port to listen on (0 = random free port)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.pass_context
def dashboard(ctx: click.Context, port: int, no_browser: bool) -> None:
    """Start the web dashboard for index inspection."""
    import webbrowser
    import uvicorn
    from context_engine.dashboard.server import create_app

    config = ctx.obj["config"]
    project_dir = Path.cwd()

    if port == 0:
        port = _find_free_port()

    url = f"http://localhost:{port}"
    click.echo(f"CCE Dashboard at {url}")
    click.echo("Press Ctrl+C to stop.")

    if not no_browser:
        webbrowser.open(url)

    app = create_app(config, project_dir)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_server.py -v
```

Expected: all PASS

- [ ] **Step 5: Run full test suite**

```bash
PYTHONPATH=src uv run pytest tests/ -x --tb=short
```

Expected: all PASS

- [ ] **Step 6: Smoke test the command**

```bash
PYTHONPATH=src uv run cce dashboard --no-browser &
sleep 1
curl -s http://localhost:$(PYTHONPATH=src uv run python -c "from context_engine.cli import _find_free_port; print(_find_free_port())") || echo "server running (expected port varies)"
kill %1 2>/dev/null || true
```

(Manual check: run `cce dashboard` in a project directory, confirm browser opens and tabs work.)

- [ ] **Step 7: Update README roadmap**

In `README.md`, change:

```markdown
- Web dashboard for index inspection
```

to:

```markdown
- [x] ~~Web dashboard for index inspection~~
```

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/cli.py tests/dashboard/test_server.py README.md
git commit -m "feat: add cce dashboard command with web UI for index inspection"
```

---

## Task 9: Final cleanup commit

- [ ] **Step 1: Run the full test suite one last time**

```bash
PYTHONPATH=src uv run pytest tests/ --tb=short
```

Expected: all PASS (89+ tests)

- [ ] **Step 2: Check nothing untracked was left behind**

```bash
git status
```

Expected: clean working tree.

- [ ] **Step 3: Final commit (if any stragglers)**

If `git status` shows any remaining changes:

```bash
git add -p   # stage selectively
git commit -m "chore: final dashboard cleanup"
```
