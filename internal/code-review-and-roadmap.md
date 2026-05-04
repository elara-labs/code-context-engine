# Code-Context-Engine — Code Review & Improvement Roadmap

**Date:** 2026-04-18
**Scope:** Full-repo review of `src/context_engine/`, `tests/`, `.github/workflows/`, packaging, and docs.
**Goal:** Capture the current state, call out concrete issues with file/line references, and prescribe fixes so a contributor can act without re-deriving context.

---

## 1. Executive Summary

### Purpose
Code-Context-Engine is a **local-first semantic indexing and retrieval system** designed to reduce token usage in Claude Code workflows. It chunks codebases using tree-sitter AST parsing, embeds chunks with `sentence-transformers`, stores them in LanceDB, and retrieves only the most relevant context when developers ask questions. An MCP server exposes the index to Claude Code.

### Strengths
- **Clean module structure** — `indexer/`, `storage/`, `retrieval/`, `compression/`, `integration/` each have a focused responsibility.
- **88 tests passing, 1 skipped; 54% overall coverage** — core primitives well-covered: `models` (100%), `query_parser` (100%), `watcher` (100%), `event_bus` (100%), `chunker` (98%).
- **Solid README + CONTRIBUTING**; PyPI publishing via OIDC trusted publishing is already wired up.
- Async-aware test suite (`pytest-asyncio`).

### Issues (high-level)
- **0% coverage on core runtime paths**: `pipeline.py`, `daemon.py`, `serve_http.py`.
- **Broad `except Exception:`** in hot paths — silent failures in storage and MCP handlers.
- **Untyped AST traversal** in chunker (`node`, `source`, `file_path` with no annotations).
- **CLI coverage ≈50%** — `init`, `index`, `status`, `savings` paths untested.
- **MCP server coverage 18%** — most tool handlers untested.
- **Publish workflow** has `attestations: false` — supply-chain provenance disabled.

---

## 2. Architecture Snapshot

```
src/context_engine/
├── indexer/          # Chunking, embedding, file watching, manifest
│   ├── chunker.py    # tree-sitter AST walk (Python/JS/TS/JSX/TSX)
│   ├── embedder.py
│   ├── watcher.py
│   └── manifest.py
├── storage/          # Persistence
│   ├── vector_store.py   # LanceDB
│   ├── graph_store.py    # Relationship graph
│   ├── remote_backend.py # Optional remote offload (HTTP)
│   └── local_backend.py
├── retrieval/        # Hybrid retriever + confidence scoring
│   ├── hybrid.py
│   ├── query_parser.py
│   └── scorer.py
├── compression/      # Optional LLM summarization (Ollama + fallback)
│   ├── compressor.py
│   └── quality.py
├── integration/      # Claude Code glue
│   ├── mcp_server.py
│   ├── session_capture.py
│   └── bootstrap.py
├── cli.py            # Entry points: init / index / status / savings / serve
├── daemon.py         # Lifecycle orchestration (UNTESTED)
├── pipeline.py       # Core indexing pipeline (UNTESTED)
└── serve_http.py     # HTTP server (UNTESTED)
```

---

## 3. Issues & Fixes

Every issue below includes: file:line reference, why it matters, and a concrete code-level fix.

### 3.1 Core runtime has 0% test coverage

**Affected files:**
- `src/context_engine/pipeline.py` — 148 lines, 0% coverage
- `src/context_engine/daemon.py` — 0% coverage
- `src/context_engine/serve_http.py` — 82 lines, 0% coverage

**Why it matters:** These are the orchestration backbone. Any regression in the pipeline or daemon breaks every user-facing command, and there is no safety net today.

**Fix — suggested test structure:**

Create `tests/test_pipeline.py`:

```python
import pytest
from pathlib import Path
from context_engine.pipeline import Pipeline
from context_engine.models import Chunk

@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "hello.py").write_text("def hello():\n    return 'world'\n")
    return tmp_path

@pytest.mark.asyncio
async def test_pipeline_indexes_python_file(tmp_project, tmp_path):
    pipeline = Pipeline(project_root=tmp_project, db_path=str(tmp_path / "db"))
    await pipeline.index_all()
    results = await pipeline.query("hello function")
    assert any(c.file_path.endswith("hello.py") for c in results)

@pytest.mark.asyncio
async def test_pipeline_reindexes_changed_file(tmp_project, tmp_path):
    pipeline = Pipeline(project_root=tmp_project, db_path=str(tmp_path / "db"))
    await pipeline.index_all()
    (tmp_project / "hello.py").write_text("def goodbye():\n    return 'bye'\n")
    await pipeline.reindex_file(tmp_project / "hello.py")
    results = await pipeline.query("goodbye function")
    assert any("goodbye" in c.content for c in results)
```

Create `tests/test_daemon.py`:

```python
import pytest
from context_engine.daemon import Daemon

@pytest.mark.asyncio
async def test_daemon_start_stop(tmp_path):
    daemon = Daemon(project_root=tmp_path, db_path=str(tmp_path / "db"))
    await daemon.start()
    assert daemon.is_running
    await daemon.stop()
    assert not daemon.is_running
```

Create `tests/test_serve_http.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from context_engine.serve_http import create_app

@pytest.mark.asyncio
async def test_health_endpoint(tmp_path):
    app = create_app(db_path=str(tmp_path / "db"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
```

**Target:** move each from 0% → 80%+.

---

### 3.2 Broad `except Exception:` hides failures

**Affected locations:**
- `src/context_engine/storage/vector_store.py:37` — `_db.open_table` on init
- `src/context_engine/storage/vector_store.py:46` — `_db.open_table` on ensure
- `src/context_engine/storage/vector_store.py:107` — `_db.open_table` on search
- `src/context_engine/integration/mcp_server.py:127` — MCP tool dispatch
- `src/context_engine/integration/mcp_server.py:310` — tool handler wrapper
- `src/context_engine/integration/mcp_server.py:381` — tool handler wrapper

**Why it matters:** `except Exception` silently swallows `KeyboardInterrupt`-free exceptions — including bugs like `AttributeError`, `KeyError`, or LanceDB schema mismatches. Users see "empty results" instead of the real error.

**Fix — `vector_store.py`:** LanceDB raises `FileNotFoundError` / `ValueError` when a table doesn't exist. Catch those specifically and log:

```python
import logging

logger = logging.getLogger(__name__)

# Line 35-38 — replace
try:
    self._table = self._db.open_table(TABLE_NAME)
except (FileNotFoundError, ValueError) as e:
    logger.debug("Table %s not yet created: %s", TABLE_NAME, e)
    self._table = None
```

Apply the same pattern at lines 46 and 107. At line 107 (the search path), log at `warning` not `debug` — a missing table during a search is suspicious.

**Fix — `mcp_server.py`:** MCP handlers should catch, log with full traceback, and return a structured error to the client rather than silently returning empty:

```python
import logging
import traceback

logger = logging.getLogger(__name__)

# Replace broad except at :127, :310, :381
except Exception as e:  # noqa: BLE001 — last-resort handler at MCP boundary
    logger.exception("MCP tool %s failed", tool_name)
    return {
        "error": {
            "type": type(e).__name__,
            "message": str(e),
        }
    }
```

The comment + `noqa` makes the intent explicit (boundary handler), and `logger.exception` captures the traceback for debugging.

---

### 3.3 Untyped AST traversal in chunker

**Affected:** `src/context_engine/indexer/chunker.py:46-64`

Current code:

```python
def _walk(self, node, source, file_path, language, chunks):
    ...
def _node_to_chunk(self, node, source, file_path, language, chunk_type):
    ...
```

**Why it matters:** tree-sitter's `Node` API is non-obvious. Without types, IDEs can't autocomplete `.start_byte`, `.children`, etc., and a mypy pass won't catch misuse.

**Fix:**

```python
from tree_sitter import Node

def _walk(
    self,
    node: Node,
    source: str,
    file_path: str,
    language: str,
    chunks: list[Chunk],
) -> None:
    if node.type in _FUNCTION_TYPES:
        chunks.append(self._node_to_chunk(node, source, file_path, language, ChunkType.FUNCTION))
    elif node.type in _CLASS_TYPES:
        chunks.append(self._node_to_chunk(node, source, file_path, language, ChunkType.CLASS))
    for child in node.children:
        self._walk(child, source, file_path, language, chunks)

def _node_to_chunk(
    self,
    node: Node,
    source: str,
    file_path: str,
    language: str,
    chunk_type: ChunkType,
) -> Chunk:
    ...
```

**Also:** add `mypy` (or `pyright`) to `pyproject.toml` dev deps and a `tool.mypy` section with `strict = true` for `src/context_engine/indexer/`.

---

### 3.4 CLI coverage ≈50%

**Affected:** `src/context_engine/cli.py` — `init`, `index`, `status`, `savings` commands untested.

**Why it matters:** The CLI is the primary user surface. Breaking `cce init` means new users can't onboard, and nothing in CI catches it.

**Fix — test pattern (Typer CLI):**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner
from context_engine.cli import app

runner = CliRunner()

def test_init_creates_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".context-engine.yaml").exists()

def test_index_runs_on_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.py").write_text("def hello(): pass\n")
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 0
    assert "indexed" in result.stdout.lower()

def test_status_shows_stats(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0

def test_savings_reports_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["savings"])
    assert result.exit_code == 0
```

**Target:** 50% → 90%+.

---

### 3.5 MCP server coverage 18%

**Affected:** `src/context_engine/integration/mcp_server.py` — ~605 lines, most tool handlers untested.

**Why it matters:** This is the integration with Claude Code. Silent breakage means Claude sessions stop getting project context and users won't know why.

**Fix — test each tool in isolation:**

Create `tests/test_mcp_server.py`:

```python
import pytest
from context_engine.integration.mcp_server import ContextEngineMCP

@pytest.fixture
async def server(tmp_path):
    srv = ContextEngineMCP(project_root=tmp_path, db_path=str(tmp_path / "db"))
    await srv.initialize()
    yield srv
    await srv.shutdown()

@pytest.mark.asyncio
async def test_query_tool_returns_chunks(server, tmp_path):
    (tmp_path / "x.py").write_text("def add(a, b): return a + b\n")
    await server.reindex()
    result = await server.handle_tool("query", {"q": "add function"})
    assert result["chunks"]

@pytest.mark.asyncio
async def test_bootstrap_tool_returns_summary(server):
    result = await server.handle_tool("bootstrap", {})
    assert "## Project" in result["text"]

@pytest.mark.asyncio
async def test_unknown_tool_returns_error(server):
    result = await server.handle_tool("nonexistent", {})
    assert "error" in result
```

**Target:** 18% → 75%+.

---

### 3.6 Publish workflow: attestations disabled

**Affected:** `.github/workflows/publish.yml:38`

Current:

```yaml
- name: Publish to PyPI
  uses: pypa/gh-action-pypi-publish@release/v1
  with:
    password: ${{ secrets.PYPI_API_TOKEN }}
    skip-existing: true
    attestations: false
```

**Why it matters:** Build provenance attestations (SLSA-style) prove the package was built from this exact commit in a trusted GitHub Actions runner. Downstream users (and `pip install --require-hashes`-style pipelines) can verify this. Leaving it off weakens supply-chain integrity.

**Also:** The workflow is using `password: ${{ secrets.PYPI_API_TOKEN }}` which is **inconsistent with the OIDC trusted publishing** setup already in place (see `id-token: write` permission at line 9 and `environment: pypi` at line 14). Trusted publishing doesn't need the token.

**Fix:**

```yaml
- name: Publish to PyPI
  uses: pypa/gh-action-pypi-publish@release/v1
  with:
    skip-existing: true
    attestations: true
```

Remove the `password:` line entirely — trusted publishing authenticates via OIDC. Keep the `id-token: write` permission and `environment: pypi`.

---

## 4. Additional Observations

### 4.1 `.mcp.json` has a hard-coded wrong path

`/.mcp.json:4` points to `/Users/raj/projects/Code-Context-Engine/.venv/bin/cce` — a path specific to another machine. The MCP server will not start for anyone cloning the repo.

**Fix options:**

1. Don't commit a machine-specific `.mcp.json`. Add it to `.gitignore` and ship a `.mcp.json.example`.
2. Use the installed `cce` binary directly (assuming `pip install code-context-engine`):

   ```json
   {
     "mcpServers": {
       "context-engine": {
         "command": "cce",
         "args": ["serve"]
       }
     }
   }
   ```

### 4.2 No SessionStart hook — users re-explain project each session

The `integration/session_capture.py` file exists but registers no Claude Code `SessionStart` hook. Without one, Claude only sees project context if it proactively calls the `bootstrap` MCP tool. The persistent LanceDB index doesn't automatically reach new sessions.

**Fix:** Add a `SessionStart` hook that runs `cce bootstrap` and injects the output as additional system context. Reference pattern: `claude-mem` plugin uses a five-hook lifecycle (SessionStart, UserPromptSubmit, Stop, etc.). See `docs/superpowers/` for more.

Example entry in `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "cce bootstrap --max-tokens 8000"
          }
        ]
      }
    ]
  }
}
```

### 4.3 Docs folder is sparse

Only `demo.svg`, `logo.svg`, `index.html`, and an old `review-2026-04-17.md` live here. No API reference, no config-file schema, no design docs.

**Suggested additions:**
- `docs/configuration.md` — schema for `.context-engine.yaml`
- `docs/architecture.md` — data flow diagram; how chunks become embeddings become retrieved context
- `docs/mcp-tools.md` — what each MCP tool does and its input/output schema
- `docs/deployment.md` — local vs remote backend setup

---

## 5. Prioritized Roadmap

| # | Task | Effort | Impact | Files |
|---|------|--------|--------|-------|
| 1 | Test `pipeline.py` + `daemon.py` (target 80%) | L | High | `tests/test_pipeline.py`, `tests/test_daemon.py` |
| 2 | Replace broad exception handlers | S | High | `vector_store.py`, `mcp_server.py` |
| 3 | Type-annotate `chunker._walk` and `_node_to_chunk` | XS | Med | `indexer/chunker.py` |
| 4 | Enable `attestations: true`; drop `PYPI_API_TOKEN` | XS | Med | `.github/workflows/publish.yml` |
| 5 | Expand CLI tests to 90% | M | High | `tests/test_cli.py` |
| 6 | Add MCP server integration tests | M | High | `tests/test_mcp_server.py` |
| 7 | Fix `.mcp.json` — gitignore and ship `.example` | XS | Med | `.mcp.json`, `.gitignore` |
| 8 | Add SessionStart hook for auto-bootstrap | S | High | `.claude/settings.json`, docs |
| 9 | Expand `docs/` with config + architecture refs | M | Med | `docs/` |

**Suggested sequence:** 3 → 4 → 2 → 7 → 1 → 5 → 6 → 8 → 9. Start with the cheap wins (type hints, workflow fix), then the correctness fixes, then the coverage work, then the UX improvements.

---

## 6. How to Verify After Each Fix

```bash
# Tests + coverage
uv run pytest --cov=src/context_engine --cov-report=term-missing

# Type checking (after adding mypy)
uv run mypy src/context_engine/indexer/

# Build + local install check
uv build
pip install dist/*.whl

# Workflow lint
actionlint .github/workflows/publish.yml  # or https://rhysd.github.io/actionlint/
```

Target state after all fixes: **≥80% coverage, zero broad exception handlers outside MCP boundary, mypy-clean indexer, attestations enabled, MCP auto-loads on session start.**
