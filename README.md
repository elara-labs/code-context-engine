<p align="center">
  <img src="docs/logo.svg" alt="Code Context Engine" width="160">
</p>

<h1 align="center">Code Context Engine</h1>

<p align="center">
  <strong>Give Claude exactly the context it needs. Nothing more.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/code-context-engine/"><img src="https://img.shields.io/pypi/v/code-context-engine?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg" alt="MCP Compatible"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <a href="https://github.com/elara-labs/code-context-engine"><img src="https://img.shields.io/github/stars/elara-labs/code-context-engine?style=social" alt="Stars"></a>
</p>

<p align="center">
  Code Context Engine (CCE) is a local-first context engine for Claude Code. It indexes your repository, breaks code into meaningful chunks, and retrieves only the most relevant context for each task — so Claude spends fewer tokens re-reading code it has already seen.
</p>

---

## The Problem

Every Claude Code session starts cold. Claude has no memory of your project. You either paste a lot of files to give it context (burns tokens fast) or paste too little and get weak answers.

Without CCE, every session looks like this:

- You open a new session and Claude knows nothing about your project
- You manually paste 3 to 4 files just to set the scene
- Claude re-reads the same files every session
- Large repos mean huge prompts, which are expensive and slow
- Decisions you made last week have to be re-explained today

**The token cost adds up fast:**

```
Wholesale paste:  payments.py + shipping.py        = 45,000 tokens
Targeted read:    Read(payments.py, lines 45-90)   =  4,200 tokens
CCE retrieval:    context_search "payment ..."     =    800 tokens
```

Targeted reads with `Read` already win the lion's share of that gap. CCE's marginal contribution is on top of that — ranked retrieval, optional summarization, and persistent decisions across sessions. `cce savings` reports retrieval savings and compression savings separately so you can see what each is worth on your repo.

## How CCE Fixes It

CCE builds a persistent, searchable index of your codebase and feeds Claude only the chunks it actually needs.

**Index once, re-index in seconds.** CCE splits your code into semantic chunks (functions, classes, modules) and stores them as vector embeddings locally. A content-hash embedding cache ensures that re-indexing only recomputes what actually changed. Git hooks keep the index current after every commit.

**Retrieve exactly what is relevant.** When Claude needs to find `calculate_shipping`, it searches the index and gets back 600 tokens instead of an entire 800-line file.

**Remember across sessions.** Architectural decisions, which files you touched, why you made a choice — stored and recalled automatically. No re-explaining.

```text
Session start:      Project overview               ->  10k tokens
Search:             "Find payment processing"      ->   800 tokens
Drill-down:         "Show full calculate_shipping" ->   600 tokens
                                                    --------
                                                    11.4k tokens

Without CCE:        Read payments.py + shipping.py ->  45k tokens
```

## When does `context_search` activate?

CCE indexes your code as vector embeddings. When Claude receives a question, it decides whether to call `context_search` based on whether the question is about your codebase. This means:

**Code queries work great.** These reference functions, files, patterns, or architecture in your project. The embeddings match and CCE returns the right chunks.

```
"how does the payment flow work?"          ✅  matches payment-related code chunks
"where is the auth middleware defined?"    ✅  matches auth module and middleware functions
"find all API endpoints"                   ✅  matches route definitions and controllers
"what calls calculate_shipping?"           ✅  matches the function and its callers
"show me the database schema"              ✅  matches models and migrations
```

**General questions skip CCE.** If you ask something that is not about your codebase, Claude answers directly without calling `context_search`. There is nothing to look up in the index for these.

```
"explain the difference between REST and GraphQL"    ⏭️  general knowledge, no code lookup
"what is a good naming convention for variables?"    ⏭️  opinion/style, no code lookup
"write me a Python script to resize images"          ⏭️  new code, not searching existing code
```

**The key insight:** `context_search` is a code retrieval tool, not a general Q&A tool. It shines when your question maps to something that exists in your codebase. Ask about your code and CCE finds the relevant pieces. Ask a general programming question and Claude simply answers from its own knowledge, which is the right behavior.

If you want Claude to use CCE for a general question, frame it around your project: instead of "what is dependency injection?" try "how does this project handle dependency injection?"

## Overview

| Problem | Without CCE | With CCE |
|---------|-------------|----------|
| Session startup | Claude re-reads files and project structure | Claude queries the index |
| Finding a function | Large prompt or manual file sharing | Targeted semantic retrieval |
| Token usage | High and repetitive | Focused and efficient |
| Cross-session memory | None by default | Decisions and code areas persisted |
| Repeated explanations | Re-explain the repo every session | Ask once, retrieve always |

---

## Quick Start

### 1. Install

```bash
uv tool install code-context-engine   # recommended — isolated, no virtualenv needed
# or
pipx install code-context-engine
# or
pip install code-context-engine       # inside a virtualenv
```

### 2. Index your project

```bash
cd /path/to/your/project
cce init
```

`cce init` handles everything in one step:

```
  Code Context Engine  ·  my-project
  ────────────────────────────────────────────

  Checking embedding model... downloading if needed (60 MB, first time only)... ready.
  Ollama not running — using truncation compression.
  Tip: ollama pull phi3:mini for LLM summarization

  ✓ Git hooks installed  (3 hooks, auto-updates on commit)
  ✓ MCP server registered in .mcp.json
  ✓ CLAUDE.md created with CCE instructions
  ✓ .gitignore updated with CCE entries

  Indexing project...
    ██████████████████████████████  89/89 files  100%

  ✓ Indexed 1,247 chunks from 89 files

  Done!  Restart Claude Code to activate CCE.
```

### 3. Run `cce` to verify

```
╭─────────────────────────── Code Context Engine v0.3.1 ────────────────────────────╮
│                                                                                     │
│                                     ⬡  C C E  ⬡                                     │
│                                                                                     │
│                                     my-project                                      │
│               standard profile  ·  /Users/you/projects/my-project                   │
│                                                                                     │
├────────────────────────────────────────────┬────────────────────────────────────────┤
│ Status                                     │ Getting started                        │
│  ● Indexed      1,247 chunks               │  cce status    full diagnostics        │
│  ● Embedding    BAAI/bge-small-en-v1.5     │  cce savings   token savings           │
│  ○ Ollama       not running                │  cce list      all commands            │
│  ● Compress     truncation                 │ ────────────────────────────────────── │
│  ● Savings      70% over 38 queries        │  Embed:  BAAI/bge-small-en-v1.5        │
│                                            │  Ollama: not running                   │
╰────────────────────────────────────────────┴────────────────────────────────────────╯
```

### 4. Restart Claude Code

Once restarted, Claude can call `context_search` and the eight companion MCP tools automatically (nine in total). No setup needed per session.

---

## Documentation

Full documentation is available in the [docs/wiki](docs/wiki) directory:

| Page | What it covers |
|------|---------------|
| [Examples](docs/wiki/Examples.md) | Real conversations — what you type, what Claude does |
| [CCE In Practice](docs/wiki/CCE-In-Practice.md) | Token counts and internals for each scenario |
| [How It Works](docs/wiki/How-It-Works.md) | Full 9-stage pipeline: indexing, retrieval, compression |
| [CLI Reference](docs/wiki/CLI-Reference.md) | Every command with expected output |
| [Tech Stack](docs/wiki/Tech-Stack.md) | Every library: what it does, where it's used, why chosen |
| [Project Commands](docs/wiki/Project-Commands.md) | Rules, preferences, and per-project commands for Claude |
| [Configuration](docs/wiki/Configuration.md) | All config options, global and per-project |

---

## Disk Footprint

CCE is designed to run on a standard developer laptop without special hardware.

### Installed package

| Component | Size | Notes |
|-----------|------|-------|
| CCE source | ~500 KB | The package itself |
| sqlite-vec | ~2 MB | Vector search extension for SQLite |
| ONNX Runtime | ~66 MB | Inference engine for the embedding model |
| fastembed | ~1 MB | Thin wrapper around ONNX Runtime |
| Other dependencies | ~135 MB | click, fastapi, tree-sitter, mcp, httpx, etc. |
| **Total installed** | **~204 MB** | One-time, in your uv/pipx tool environment |

### Embedding model

Downloaded once on first `cce init`, stored in the fastembed cache:

| Model | Size |
|-------|------|
| `BAAI/bge-small-en-v1.5` (default) | ~60 MB |

### Index per project

Stored in `~/.cce/projects/<name>/`. Size depends on project scale:

| Project scale | Approximate index size |
|---------------|----------------------|
| Small (under 50 files) | 5 to 15 MB |
| Medium (50 to 200 files) | 15 to 60 MB |
| Large (200 to 1,000 files) | 60 to 250 MB |

The CCE repository itself (134 files, 1,847 chunks) produces a 55 MB index.

### No GPU required

The embedding model runs via ONNX Runtime on CPU. A standard laptop CPU embeds a full project in seconds.

---

## Web Dashboard

```bash
cce dashboard
```

The dashboard opens in your browser. It provides four views:

**Overview.** Chunks indexed, files indexed, queries run, tokens saved — plus live charts updating every 5 seconds.

**Files.** Full file list with staleness detection: `ok`, `stale` (modified since last index), or `missing` (deleted).

**Sessions.** Past architectural decisions and code areas from Claude sessions, organized with expandable detail.

**Savings.** Token usage breakdown with compression controls.

```bash
cce dashboard --port 8080      # custom port
cce dashboard --no-browser     # server only, no browser open
```

![CCE Dashboard](docs/dashboard.png)

---

## Token Savings

```bash
cce savings
```

```
  my-project · 38 queries

  ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶  70% tokens saved

  Without CCE   48.0k  tokens   $0.24
  With CCE      14.2k  tokens   $0.07
  ──────────────────────────────────────────
  Saved         33.8k  tokens   $0.17
  ~889 tokens / query  ~<$0.01 / query

  How:  retrieval 46%  +  compression 45%
  Cost estimate based on Opus input pricing ($5/1M tokens)
```

The savings report shows a clear before/after comparison with estimated dollar costs (fetched dynamically from Anthropic's pricing page, cached for 7 days). The `⛁`/`⛶` grid gives a quick visual of how much context you are actually using.

CCE reports two distinct savings effects:

- **Retrieval savings** — the fraction you save by serving targeted chunks instead of full files.
- **Compression savings** — the fraction you save on top of that by truncating or LLM-summarising the retrieved chunks before sending.

Configure pricing for your model in `~/.cce/config.yaml` or `.context-engine.yaml`:

```yaml
pricing:
  model: sonnet   # opus (default) | sonnet | haiku
```

---

## How It Works

### 1. Indexing

CCE walks your repository, hashes each file, and builds three stores: a sqlite-vec vector index, a SQLite FTS5 full-text index, and a SQLite code graph. Git hooks keep all three current on every commit.

### 2. Semantic Chunking

Tree-sitter parses each file into its actual structure — functions, classes, imports — so each chunk has a single responsibility.

```text
payments.py  (800 lines, ~12k tokens)
  -> calculate_shipping()    chunk  lines 45–90     (640 tokens)
  -> validate_address()      chunk  lines 92–130    (480 tokens)
  -> ShippingMethod          class  lines 132–200   (820 tokens)
```

### 3. Hybrid Retrieval

Every `context_search` runs vector search (semantic similarity via sqlite-vec) and BM25 keyword search (via SQLite FTS5) in parallel. Results are merged with Reciprocal Rank Fusion so exact-match identifiers rank as well as semantic concepts.

### 4. Graph-Aware Expansion

After primary retrieval, CCE walks the code graph one hop. If the top result is `auth.py:validate_token`, CCE also fetches relevant chunks from files `auth.py` calls or imports.

```text
Query:          "validate user token"
Primary:        auth.py:validate_token      (confidence: 0.91)
Graph expansion: utils.py:decode_jwt        (auth.py CALLS utils.py)
                 db.py:fetch_user_by_id     (auth.py CALLS db.py)
```

### 5. Overflow References

When results exceed the token budget, CCE lists the rest as compact references rather than silently dropping them.

```text
2 more result(s) available (not shown to save tokens):
  expand_chunk(chunk_id="abc123")  → payments.py:45 (confidence: 0.82)
  expand_chunk(chunk_id="def456")  → orders.py:112  (confidence: 0.71)
```

### 6. Compression

Without Ollama: CCE truncates to function signature and docstring.
With Ollama running locally: CCE uses `phi3:mini` for higher-quality LLM summaries. Detected automatically, no configuration needed.

### 7. Content-Hash Embedding Cache

Re-indexing a large codebase should take seconds, not minutes. CCE fingerprints every code chunk by its content hash and caches the resulting embedding vector in a local SQLite store. On re-index, only chunks whose content actually changed go through the embedding model; everything else is served from cache instantly.

This is the same principle production-grade AI code tools use: treat embeddings as a function of content, cache the result, never recompute what hasn't changed. On a typical re-index after editing a few files, 95%+ of embeddings come from cache.

```
  First index:    1,247 chunks embedded                 12.4s
  Re-index:       1,247 chunks, 1,203 from cache (96%)   0.8s
```

`cce status` shows cache size; `cce index` reports the hit rate after every run. Vectors are stored as binary float32 (`struct.pack`) — same encoding as the sqlite-vec store, ~4× smaller on disk than JSON. Orphaned entries are pruned automatically on `cce index --full` so the cache doesn't grow forever.

### 8. Cross-Session Memory

When Claude records a decision (`record_decision`) or a code area (`record_code_area`), CCE stores it in SQLite. `session_recall` surfaces it at the start of the next session — no re-explaining.

---

## CLI Commands

Run `cce list` to see all commands:

```
  ── Setup ─────────────────────────────────────────
    cce init                            Index project, install git hooks, write .mcp.json
    cce index                           Re-index changed files
    cce index --full                    Force full re-index of every file
    cce index --path <file>             Index one file or directory

  ── Status & Savings ──────────────────────────────
    cce status                          Index health, config, embedding model, Ollama status
    cce status --json                   Machine-readable output
    cce savings                         Token savings report with visual grid
    cce savings --all                   Savings across every indexed project
    cce savings --json                  Machine-readable savings output

  ── Index Management ──────────────────────────────
    cce clear                           Clear all index data (asks for confirmation)
    cce clear --yes                     Skip confirmation
    cce prune                           Remove data for deleted projects
    cce prune --dry-run                 Preview without deleting

  ── Services ──────────────────────────────────────
    cce services                        Show status of Ollama, dashboard, MCP
    cce services start                  Start Ollama + dashboard
    cce services start ollama           Start only Ollama
    cce services start dashboard        Start dashboard on default port
    cce services stop                   Stop everything CCE started

  ── Dashboard ─────────────────────────────────────
    cce dashboard                       Open web dashboard in browser
    cce dashboard --port 8080           Custom port
    cce dashboard --no-browser          Server only, no browser open

  ── Project Commands ──────────────────────────────
    cce commands list                   Show all rules, preferences, and hooks
    cce commands add-rule '<rule>'      Add a project rule
    cce commands remove-rule '<rule>'   Remove a rule
    cce commands set-pref <key> <val>   Set a preference
    cce commands remove-pref <key>      Remove a preference
    cce commands add <hook> '<cmd>'     Add to before_push / before_commit / on_start
    cce commands remove <hook> '<cmd>'  Remove from a hook
    cce commands add-custom <n> '<c>'   Add a named custom command

  ── Search ────────────────────────────────────────
    cce search '<query>'                Run a test query and update savings stats
    cce search '<query>' --top-k 10     Return more results

  ── Shortcuts ─────────────────────────────────────
    cce start                           Start all services (Ollama + dashboard)
    cce stop                            Stop all services
    cce start ollama                    Start only Ollama
    cce stop dashboard                  Stop only dashboard

  ── Lifecycle ─────────────────────────────────────
    cce init                            Install CCE in project
    cce upgrade                         Upgrade CCE and refresh project config
    cce upgrade --check                 Check install method without upgrading
    cce uninstall                       Remove CCE from project (hooks, MCP, CLAUDE.md)
    cce serve                           Start MCP server (used by Claude Code)

  ── Other ─────────────────────────────────────────
    cce list                            This command
    cce --version                       Show version
    cce --help                          Show help
```

### `cce status`

```
  ── Status · my-project ──────────────────────────

    ● Storage       /Users/you/.cce/projects
    ● Compression   standard
    ● Profile       standard
    ● Embedding     BAAI/bge-small-en-v1.5
    ○ Ollama        not running
    ● Compress      truncation (signatures + docstrings)

  ── Token Savings ─────────────────────────────────

    Queries:        42
    Full codebase:  58,000 tokens
    Served:         18,400 tokens
    ✓ Saved: 39,600 tokens (68%)
```

### `cce services`

```
  ── Services ──────────────────────────────────────

    ● ollama       running   localhost:11434 (external)
    ○ dashboard    stopped
    ● mcp          running   managed by Claude Code
```

### Dashboard

```bash
cce dashboard                      # open in browser
cce dashboard --port 8080
cce dashboard --no-browser
```

### `cce uninstall`

Removes CCE from the current project. Cleans up git hooks, the `.mcp.json` entry, the CLAUDE.md block, and the local `.cce/` directory.

```
  ── Uninstall · my-project ────────────────────────

    ✗ Removed 3 git hooks
    ✗ Removed context-engine from .mcp.json
    ✗ Removed CCE block from CLAUDE.md
    ✗ Removed .cce/ directory

    Index data in ~/.cce is preserved.
    Run cce clear to remove index data too.
```

Your index data in `~/.cce/projects/<name>/` is kept so you can re-initialize later without a full re-index. Run `cce clear` to remove that too.

---

## MCP Tools

Once connected, Claude has these tools available automatically:

| Tool | What it does |
|------|-------------|
| `context_search` | Hybrid vector + BM25 search with graph expansion |
| `expand_chunk` | Get full source for a compressed or overflow chunk |
| `related_context` | Find related code via graph edges (calls, imports) |
| `session_recall` | Recall past decisions and code area notes |
| `record_decision` | Save a decision for future sessions |
| `record_code_area` | Record which files were worked in and why |
| `index_status` | Check when the index was last updated |
| `reindex` | Trigger re-indexing of a file or the full project |
| `set_output_compression` | Adjust response verbosity: `off`, `lite`, `standard`, `max` |

---

## Output Compression

CCE compresses Claude's own responses to reduce output tokens.

| Level | Style | Typical savings |
|-------|-------|-----------------|
| `off` | Full Claude output | 0% |
| `lite` | No filler or hedging | ~30% |
| `standard` | Shorter phrasing and fragments | ~65% |
| `max` | Telegraphic style | ~75% |

Change at any time by telling Claude:

```
Switch to max output compression
Turn off output compression
```

Code blocks, file paths, commands, and error messages are never compressed.

---

## Configuration

CCE works with zero configuration. Override what you need.

**Global config** — `~/.cce/config.yaml`:

```yaml
compression:
  level: standard        # minimal | standard | full
  output: standard       # off | lite | standard | max
  model: phi3:mini       # Ollama model (auto-detected)

indexer:
  watch: true
  ignore: [.git, node_modules, __pycache__, .venv]

retrieval:
  top_k: 20
  confidence_threshold: 0.5

embedding:
  model: BAAI/bge-small-en-v1.5

pricing:
  model: opus              # opus | sonnet | haiku (for cost estimates in cce savings)
```

**Per-project config** — `.context-engine.yaml` in your project root:

```yaml
compression:
  level: full

indexer:
  ignore: [.git, node_modules, dist, coverage, "*.generated.ts"]
```

### Project commands, rules & preferences

Tell Claude how to work in each project. Stored in `.cce/commands.yaml`:

```bash
cce commands add-rule 'Never generate down() in migrations'
cce commands set-pref database PostgreSQL
cce commands add before_push 'composer test'
cce commands add-custom deploy 'kubectl apply -f k8s/'
```

Claude sees these at every session start and follows them automatically. Supports workspace-level configs for multi-project directories. See [Project Commands](docs/wiki/Project-Commands.md) for details.
---

## Optional Ollama Support

Without Ollama, CCE uses smart truncation. With Ollama, it uses LLM-based summarization automatically — `cce init` tells you which mode is active.

```bash
brew install ollama
ollama pull phi3:mini
ollama serve
```

`cce init` detects Ollama and reports its status during setup. No other configuration required.

---

## Supported Languages

### AST-aware chunking

| Language | Extensions |
|----------|-----------|
| Python | `.py` |
| JavaScript | `.js`, `.jsx` |
| TypeScript | `.ts`, `.tsx` |

### Fallback chunking

All other text-based files (Markdown, YAML, PHP, config files, etc.) are chunked by line range. AST-aware chunking for PHP, Go, Rust, Java, and C is planned.

---

## Roadmap

- [x] Semantic code indexing and retrieval
- [x] Output compression levels (`off` / `lite` / `standard` / `max`)
- [x] Cross-session memory (decisions, code areas)
- [x] Web dashboard with live charts (`cce dashboard`)
- [x] Token savings tracking and reporting (`cce savings`)
- [x] Non-git project support
- [x] Index management (`cce clear`, `cce prune`)
- [x] Service management (`cce services` — Ollama + dashboard background processes)
- [x] Graph-aware 1-hop retrieval expansion via CALLS/IMPORTS edges
- [x] Overflow result references in `context_search`
- [x] Output terseness rules in generated `CLAUDE.md`
- [x] Pre-flight check in `cce init` (embedding model warmup + Ollama hint)
- [x] Comprehensive `.gitignore` for CCE-generated per-machine files
- [x] Live file watcher (auto re-indexes on save during `cce serve`)
- [x] Project commands, rules, and preferences (`cce commands`)
- [x] Welcome banner with 2-column status display (`cce`)
- [x] Colorful CLI output with section headers and line-by-line animation
- [x] sqlite-vec migration (54% smaller install, same search quality)
- [x] Content-hash embedding cache (skip re-embedding unchanged chunks)
- [x] Human-readable savings report with dollar estimates (`cce savings`)
- [x] Dynamic model pricing from Anthropic docs (cached 7 days)
- [x] Per-bucket savings breakdown (retrieval, compression, output, memory)
- [ ] Tree-sitter support for Go, Rust, Java, C, and C++
- [ ] Persistent session search across projects
- [ ] Docker support for remote mode
- [ ] Retrieval quality benchmarks on real repositories

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

Browse [good first issues](https://github.com/elara-labs/code-context-engine/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) if you are looking for a place to start.

---

## License

MIT. See [LICENSE](LICENSE).

## Authors

- [Fazle Elahee](https://github.com/fazleelahhee)
- [Raj](https://github.com/rajkumarsakthivel)

## Acknowledgments

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [MCP](https://modelcontextprotocol.io)
- [sqlite-vec](https://github.com/asg017/sqlite-vec)
- [Tree-sitter](https://tree-sitter.github.io/)
- [fastembed](https://github.com/qdrant/fastembed)
- [Ollama](https://ollama.com/)

If CCE saves you tokens, give it a star.
