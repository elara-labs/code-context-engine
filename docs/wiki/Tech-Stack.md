# Tech Stack

Every library in CCE is here for a specific reason. This page covers all 14 dependencies — what each one does, exactly where in the codebase it is used, why it was chosen over alternatives, and what you would lose without it.

---

## Quick Map

```
┌─────────────────────────────────────────────────────────┐
│                         cce CLI                          │
│                       (click, yaml)                      │
└────────┬──────────────────┬──────────────────────────────┘
         │                  │
    ┌────▼────┐        ┌────▼──────────────────────┐
    │ MCP     │        │ Indexer Pipeline           │
    │ Server  │        │ (tree-sitter, fastembed,   │
    │  (mcp)  │        │  numpy, watchdog)          │
    └────┬────┘        └────┬──────────────────────┘
         │                  │
    ┌────▼──────────────────▼──────────────────────┐
    │              Storage Layer                    │
    │   Vector: sqlite-vec    Graph+FTS: sqlite3     │
    └──────────────────────────────────────────────┘
         │
    ┌────▼──────────────────────────────────────────┐
    │   Optional / Peripheral                       │
    │   Compression: httpx → Ollama                 │
    │   Dashboard:   fastapi + uvicorn              │
    │   Remote mode: httpx + aiohttp                │
    │   Config:      pyyaml                         │
    └──────────────────────────────────────────────┘
```

---

## Core Indexing

### tree-sitter

**What it is:** A parser library that converts source code into an Abstract Syntax Tree (AST).

**Where in CCE:** `src/context_engine/indexer/chunker.py`

```python
from tree_sitter import Language, Parser
```

This is the foundation of semantic chunking. When CCE indexes a Python file, tree-sitter parses the file into a real AST and CCE walks it to extract function and class boundaries. Without tree-sitter, CCE would have to chunk files by line count — which means a 50-line function in the middle of an 800-line file gets split in half.

**What it enables:**

```
payments.py  (800 lines, 12,400 tokens)
                         ↓  tree-sitter
  calculate_shipping()   chunk  lines 45–90     (640 tokens)
  validate_address()     chunk  lines 92–130    (480 tokens)
  ShippingMethod         class  lines 132–200   (820 tokens)
  ...
```

Claude retrieves the `calculate_shipping` function (640 tokens) instead of the full file (12,400 tokens).

**Why tree-sitter over alternatives:**

| Approach | Problem |
|----------|---------|
| Regex-based parsing | Breaks on edge cases: nested functions, decorators, multiline strings |
| Python's `ast` module | Python-only, no JavaScript/TypeScript support |
| Language Server Protocol | Heavy dependency, requires running a separate process per language |
| Manual line counting | Arbitrary splits, no awareness of code structure |

Tree-sitter is incremental (sub-millisecond per file), error-tolerant (parses files with syntax errors), and supports 40+ languages through the same API.

---

### tree-sitter-python, tree-sitter-javascript, tree-sitter-typescript

**What they are:** Grammar packages that teach tree-sitter the syntax rules for each language.

**Where in CCE:** `src/context_engine/indexer/chunker.py`

```python
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
```

Tree-sitter itself is a generic parser engine. The grammar packages are language-specific compiled bindings that define what a "function definition" or "class declaration" looks like in each language. CCE bundles grammars for Python, JavaScript, and TypeScript. PHP is handled via the `tree-sitter-languages` bundle.

TypeScript covers `.ts`, `.tsx`, and `.jsx` because the TypeScript grammar is a strict superset of JavaScript.

**Why these three languages first:** Python, JavaScript, and TypeScript account for the majority of projects using Claude Code. Adding a new language means adding one grammar package — the chunking logic itself does not change.

---

### fastembed

**What it is:** A lightweight embedding library that runs models via ONNX Runtime.

**Where in CCE:** `src/context_engine/indexer/embedder.py`

```python
from fastembed import TextEmbedding
```

Every chunk produced by tree-sitter gets converted into a 384-dimensional vector by fastembed. That vector is what gets stored in sqlite-vec and compared at query time. Fastembed also embeds the query itself before running similarity search.

**The model:** `BAAI/bge-small-en-v1.5` (60MB, downloaded once on first use).

| Model | Size | Retrieval Quality (MTEB) | Notes |
|-------|------|--------------------------|-------|
| `all-MiniLM-L6-v2` | 80MB | Good | Common default in other tools |
| `BAAI/bge-small-en-v1.5` | 60MB | Better | CCE default |
| `text-embedding-ada-002` | Cloud | Best | Requires OpenAI API key, sends code to OpenAI |

BGE-small beats MiniLM on the MTEB retrieval benchmark while being smaller. The OpenAI model produces better embeddings but sends your code to a third-party API on every index operation. CCE is local-first by design.

**Why fastembed over PyTorch-based alternatives:**

Fastembed uses ONNX Runtime instead of PyTorch. This means:
- No GPU required
- No CUDA installation
- Under 50ms per batch on a laptop CPU
- No PyTorch dependency (which is 700MB+)

If you wanted to use a sentence-transformers model directly, you would pull in the entire PyTorch stack. Fastembed wraps the same underlying models in a much lighter runtime.

---

### numpy

**What it is:** Numerical array library.

**Where in CCE:** `src/context_engine/indexer/embedder.py`

```python
import numpy as np
```

Fastembed returns embeddings as NumPy arrays. CCE uses NumPy to stack and process batches of these arrays before writing them to the vector store. The vector store expects arrays in a specific shape and dtype — NumPy handles that conversion.

**Why it is a separate dependency:** NumPy ships as a fastembed transitive dependency anyway. CCE lists it explicitly because the embedder code directly calls `np.vstack` and accesses `.tolist()` on arrays. If fastembed changes its return format, the explicit dependency makes the version constraint visible.

---

### watchdog

**What it is:** A cross-platform file system event library.

**Where in CCE:** `src/context_engine/indexer/watcher.py`

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
```

When `cce services start` keeps the daemon running, watchdog monitors the project directory for file changes and triggers `cce index` automatically on saves. It uses OS-native APIs on each platform (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows) rather than polling.

**What it handles:**
- Debouncing: multiple rapid saves to the same file trigger one re-index, not ten
- Ignoring binary files and `.git/` changes
- Detecting new files added to the project

**Why watchdog:** It is the standard Python file-watching library with cross-platform support. The OS-native backends make it far more efficient than polling (`os.stat` in a loop), which would spike CPU usage on large codebases.

The git `post-commit` hook is the primary mechanism for keeping the index current. Watchdog is the secondary mechanism for users who want live indexing without committing.

---

## Storage

### sqlite-vec

**What it is:** A SQLite extension for vector similarity search.

**Where in CCE:** `src/context_engine/storage/vector_store.py`

```python
import sqlite_vec
```

sqlite-vec stores the 384-dimensional embedding for each chunk in a `vec0` virtual table alongside a regular SQLite table for metadata (file path, start line, end line, content). At query time, it runs cosine distance search to find the most semantically similar chunks.

CCE uses two tables: `chunks` for metadata and `chunks_vec` for embeddings, joined by rowid:

```python
# Vector search with cosine distance
rows = conn.execute("""
    SELECT c.*, v.distance FROM chunks_vec v
    JOIN chunks c ON c.rowid = v.rowid
    WHERE v.embedding MATCH ? AND k = ?
""", (query_bytes, top_k))
```

**Why sqlite-vec:**

| Database | Problem for CCE |
|----------|----------------|
| Chroma | Slower writes, higher memory use |
| Qdrant / Weaviate | Require a running server process. Wrong for a local CLI tool. |
| pgvector | Requires PostgreSQL installed. Heavyweight. |
| FAISS | Fast but no built-in persistence, no metadata filtering |
| LanceDB | Works well but adds ~217 MB to install size (with pyarrow) |

sqlite-vec is ~2 MB, runs as a SQLite extension (in-process), uses WAL mode for concurrent reads/writes, and delivers the same search quality as LanceDB at 54% smaller install size.

**Storage location:** `~/.cce/projects/<name>/vectors/`

---

### sqlite3 (standard library)

**What it is:** SQLite database engine, part of Python's standard library.

**Where in CCE:** Two separate stores.

**1. Graph store** — `src/context_engine/storage/graph_store.py`

```python
import sqlite3
```

Stores the code knowledge graph built during indexing:

```sql
nodes (id, node_type, name, file_path, properties)
edges (source_id, target_id, edge_type, properties)
```

Edge types are `CALLS`, `IMPORTS`, and `DEFINES`. After primary retrieval, CCE queries this graph one hop to find related files:

```
Primary result: auth.py:validate_token
Graph query:    SELECT target FROM edges WHERE source IN (auth.py nodes) AND edge_type IN ('CALLS','IMPORTS')
Bonus chunks:   utils/jwt.py:decode_jwt, db/users.py:fetch_user_by_id
```

This surfaces related context that pure semantic search would miss, without requiring a second query from Claude.

**2. FTS store** — `src/context_engine/storage/fts_store.py`

```python
import sqlite3
```

Uses SQLite's built-in FTS5 virtual table for BM25 keyword search over raw chunk text:

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(content, chunk_id UNINDEXED);
```

Every `context_search` runs BM25 and vector search in parallel, then merges results with RRF (Reciprocal Rank Fusion). BM25 catches exact-match cases that vector search ranks too low — identifiers like `stripe_api_key` or `ORDER_STATUS_FULFILLED` that have specific meaning beyond their semantic similarity.

**Why SQLite for both:** The graph is sparse (most functions call fewer than 10 others) and only needs simple `SELECT` queries — no multi-hop traversal. FTS5 is built into SQLite, so it is zero additional dependencies. Both stores live in the same local directory. No server. No migration tooling needed.

**Storage location:** `~/.cce/projects/<name>/graph/` and `fts/`

---

## Protocol and Interface

### mcp

**What it is:** The Python SDK for Anthropic's Model Context Protocol.

**Where in CCE:** `src/context_engine/integration/mcp_server.py`

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
```

MCP is the wire protocol between CCE and Claude Code. CCE runs as an MCP server; Claude Code discovers it via `.mcp.json` and calls its tools by name over stdio.

The full list of tools CCE exposes through MCP:

| Tool | What it does |
|------|-------------|
| `context_search` | Hybrid vector + BM25 search with graph expansion |
| `expand_chunk` | Retrieve full content for a compressed or overflow chunk |
| `related_context` | Find related code via graph edges (calls, imports) |
| `session_recall` | Recall past architectural decisions |
| `record_decision` | Save a decision for future sessions |
| `record_code_area` | Record which files were worked on and why |
| `index_status` | Check index health and token savings |
| `reindex` | Trigger re-indexing of a file or the full project |
| `set_output_compression` | Adjust response verbosity |

**Transport:** stdio (not HTTP). Claude Code spawns the `cce serve` process and communicates via stdin/stdout. No port, no authentication, no network, instant startup.

**Why MCP:** It is Anthropic's official protocol for extending Claude Code. There is no other first-party way to give Claude Code persistent, callable tools. Using MCP means CCE's tools appear natively in Claude's tool list without any prompt engineering.

---

## HTTP and Networking

### httpx

**What it is:** An async HTTP client.

**Where in CCE:** Three places.

**1. Ollama client** — `src/context_engine/compression/ollama_client.py`

```python
import httpx
```

Sends chunk content to the local Ollama API for LLM summarization. Uses async requests so embedding and compression can overlap with other work.

```
POST http://localhost:11434/api/generate
{"model": "phi3:mini", "prompt": "Summarize this function: ..."}
```

**2. Services health check** — `src/context_engine/services.py`

```python
import httpx
```

Used by `cce services` to check whether Ollama is actually responding on port 11434. A process being alive is not enough — the API might still be starting up.

**3. Remote backend** — `src/context_engine/storage/remote_backend.py`

```python
import httpx
```

Powers the experimental remote mode where the CCE storage layer is a separate HTTP server rather than local files. Not used in the default local setup.

**Why httpx over requests:** httpx supports both sync and async APIs. The Ollama client needs async to avoid blocking the MCP server event loop while waiting for LLM responses. `requests` is sync-only.

---

### aiohttp

**What it is:** An async HTTP server and client framework.

**Where in CCE:** `src/context_engine/serve_http.py`

```python
from aiohttp import web
```

Powers `cce serve-http` — the HTTP API for remote mode. When CCE is deployed as a shared service (rather than a local per-project tool), other machines can reach it over HTTP. aiohttp serves the REST endpoints for vector search, FTS, graph queries, compression, and stats.

**This is an optional dependency** (`pip install code-context-engine[http]`). The default local setup never uses aiohttp.

**Why aiohttp not FastAPI here:** serve_http is a thin API layer with no HTML or complex validation. aiohttp is lighter and simpler for raw JSON endpoints. FastAPI's overhead is justified for the dashboard (which has more routes and needs automatic docs), not for this internal API.

---

## CLI and Configuration

### click

**What it is:** A Python framework for building command-line interfaces.

**Where in CCE:** `src/context_engine/cli.py`

```python
import click
```

Every `cce` subcommand is a Click command or group. Click handles argument parsing, `--help` text generation, option validation, and the nested command structure (`cce services start dashboard --port 9090`).

**Why click over argparse:** Click uses decorators instead of imperative parser setup, which makes adding subcommands and options straightforward. Argparse gets verbose fast with nested commands. Click also handles edge cases like prompting for confirmation (`cce clear`) cleanly.

---

### pyyaml

**What it is:** A YAML parser and emitter for Python.

**Where in CCE:** Three places.

**1. Config loading** — `src/context_engine/config.py`

```python
import yaml
```

Reads `~/.cce/config.yaml` (global config) and `.context-engine.yaml` (per-project config). The config system merges them with project settings taking priority.

**2. Project commands** — `src/context_engine/project_commands.py`

```python
import yaml
```

Reads `.cce/commands.yaml` if present — a file users can create to define custom CCE commands for their project.

**3. CLI output** — `src/context_engine/cli.py`

Serializes config and commands for display in the terminal.

**Why YAML over TOML or JSON:** YAML supports inline comments, which matters for config files users are meant to hand-edit. The generated `~/.cce/config.yaml` includes comments explaining each option. TOML also supports comments but YAML is more familiar to most developers for config files. JSON does not support comments.

---

## Dashboard

### fastapi

**What it is:** A modern Python web framework with automatic type validation and API docs.

**Where in CCE:** `src/context_engine/dashboard/server.py`

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
```

The dashboard server exposes these endpoints:

| Endpoint | Returns |
|----------|---------|
| `GET /` | The dashboard HTML (single-page app) |
| `GET /api/stats` | Chunk count, file count, query count, token savings |
| `GET /api/files` | File list with staleness status |
| `GET /api/sessions` | Past decisions and code areas |

The frontend polls these every 5 seconds for live updates.

**Why FastAPI:** Automatic request/response validation, async support, and clean route definitions. For the dashboard, the automatic OpenAPI docs at `/docs` are useful for debugging. FastAPI is overkill for a two-route API — it earns its place here because the dashboard has five routes, JSON serialization of Pydantic models, and async data fetching from SQLite.

---

### uvicorn

**What it is:** An ASGI server for running FastAPI applications.

**Where in CCE:** `src/context_engine/cli.py`

```python
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=port)
```

Called when `cce dashboard` or `cce services start dashboard` starts the web server. Uvicorn is the standard production-grade ASGI server for FastAPI. It handles the HTTP connection layer so FastAPI handles routing and logic.

**Why uvicorn over alternatives:** FastAPI's own documentation recommends uvicorn. Hypercorn and Daphne are alternatives but uvicorn is faster and simpler for local use.

---

## Dev Dependencies

### pytest

**What it is:** The Python testing framework.

**Where in CCE:** All files under `tests/`.

244 tests covering the config system, CLI commands, MCP server handlers, storage backends, retrieval pipeline, services, and token efficiency features. Pytest's fixture system and parametrize decorator make it practical to test async code paths and edge cases without boilerplate.

---

### pytest-asyncio

**What it is:** Pytest plugin for testing async functions.

**Where in CCE:** `tests/test_token_efficiency.py` and any other tests with `async def test_*`.

```python
# pyproject.toml
asyncio_mode = "auto"
```

With `asyncio_mode = "auto"`, any `async def test_*` function runs on an event loop automatically. This is necessary for testing the graph expansion and retrieval pipeline, which are async throughout.

---

### pytest-cov

**What it is:** Coverage reporting plugin for pytest.

Used locally to check which code paths are covered by tests. Not wired into CI yet — that is a roadmap item (issue #35).

---

## Build

### setuptools

**What it is:** The standard Python package build backend.

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

Setuptools reads `pyproject.toml`, discovers the `context_engine` package under `src/`, and produces the wheel and sdist published to PyPI. The `uv build` command invokes it.

**Why setuptools over Hatch or Poetry:** Setuptools is the lowest common denominator — it is what every pip installation expects and it has no opinions about project structure beyond what `pyproject.toml` specifies. Hatch and Poetry both add their own tooling layers that are not needed here.

---

## Dependency Summary

| Library | Version | Role | Optional |
|---------|---------|------|----------|
| click | ≥8.1 | CLI framework | No |
| pyyaml | ≥6.0 | Config file parsing | No |
| sqlite-vec | ≥0.1.6 | Vector similarity search via SQLite | No |
| fastembed | ≥0.4 | Local embedding via ONNX Runtime | No |
| numpy | ≥1.24 | Array processing for embeddings | No |
| tree-sitter | ≥0.22 | AST parser engine | No |
| tree-sitter-python | ≥0.21 | Python grammar | No |
| tree-sitter-javascript | ≥0.21 | JavaScript grammar | No |
| tree-sitter-typescript | ≥0.21 | TypeScript grammar | No |
| watchdog | ≥4.0 | File system change detection | No |
| mcp | ≥1.0 | MCP server protocol | No |
| httpx | ≥0.27 | Async HTTP (Ollama + remote) | No |
| fastapi | ≥0.110 | Dashboard web server | No |
| uvicorn | ≥0.29 | ASGI server for FastAPI | No |
| aiohttp | ≥3.9 | HTTP API for remote mode | Yes (`[http]`) |
| sqlite3 | stdlib | Graph store + FTS store | No |

---

## What "Local-First" Means for Dependencies

Every core dependency runs entirely on your machine with no network calls:

- **sqlite-vec** — SQLite extension, in-process
- **fastembed** — ONNX model downloaded once, then local inference
- **sqlite3** — files on disk, in-process
- **mcp** — stdio, no network
- **tree-sitter** — compiled native bindings, no external service

The only components that ever touch the network are optional:
- **httpx → Ollama** — only if Ollama is running locally (still local, still your machine)
- **httpx → remote backend** — only if you explicitly configure remote mode
- **aiohttp** — only installed with `[http]` extra

No code, no embeddings, no queries leave your machine in the default setup.
