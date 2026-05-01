<p align="center">
  <img src="https://raw.githubusercontent.com/elara-labs/code-context-engine/main/docs/logo.svg" alt="Code Context Engine" width="160">
</p>

<h1 align="center">Code Context Engine</h1>

<p align="center">
  <strong>Index your codebase. AI searches instead of re-reading files. Save 70%+ on tokens.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/code-context-engine/"><img src="https://img.shields.io/pypi/v/code-context-engine?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg" alt="MCP Compatible"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <a href="https://github.com/elara-labs/code-context-engine"><img src="https://img.shields.io/github/stars/elara-labs/code-context-engine?style=social" alt="Stars"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude_Code-black?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code">
  <img src="https://img.shields.io/badge/VS_Code-007ACC?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="VS Code">
  <img src="https://img.shields.io/badge/Cursor-000000?style=for-the-badge&logo=cursor&logoColor=white" alt="Cursor">
  <img src="https://img.shields.io/badge/Gemini_CLI-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini CLI">
  <img src="https://img.shields.io/badge/Codex_CLI-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex CLI">
</p>

<p align="center">
  One command. Index your codebase. Your AI coding agent searches instead of reading entire files.<br>
  Works with Claude Code, Cursor, VS Code, Gemini CLI, and OpenAI Codex. Local, zero-cloud.
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/elara-labs/code-context-engine/main/docs/demo.gif" alt="CCE Demo" width="800">
</p>

---

## Install and see savings in 60 seconds

```bash
uv tool install code-context-engine   # or: pipx install code-context-engine
cd /path/to/your/project
cce init                              # index, install hooks, register MCP server
```

Restart your editor. Done. Every question now hits the index instead of re-reading files.

`cce init` auto-detects your editor and writes the right config:

| Editor | Config written | Instructions |
|--------|---------------|--------------|
| Claude Code | `.mcp.json` | `CLAUDE.md` |
| VS Code / Copilot | `.vscode/mcp.json` | |
| Cursor | `.cursor/mcp.json` | `.cursorrules` |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` |
| OpenAI Codex | `.codex/config.toml` | |

Multiple editors in the same project? All get configured in one command.

```
  my-project · 38 queries

  ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶  70% tokens saved

  Without CCE   48.0k  tokens   $0.24
  With CCE      14.2k  tokens   $0.07
  ──────────────────────────────────────────
  Saved         33.8k  tokens   $0.17

  Cost estimate based on Opus input pricing ($5/1M tokens)
```

---

## Why this matters

Input tokens are 85-95% of your Claude Code bill. CCE cuts them significantly.

```
Without CCE:    Claude reads payments.py + shipping.py   = 45,000 tokens
With CCE:       context_search "payment flow"            =    800 tokens
```

| | Without CCE | With CCE |
|---|---|---|
| Session startup | Re-reads files every time | Queries the index |
| Finding a function | Read entire 800-line file | Get the 40-line function |
| Cross-session memory | None | Decisions + code areas persisted |
| Token cost (Opus, medium project) | ~$0.48/session | ~$0.14/session |

---

## Benchmark: FastAPI (independently verified)

We benchmarked CCE against [FastAPI](https://github.com/fastapi/fastapi) (48 source files, 19K lines of Python) with 20 real coding questions. No cherry-picking, no synthetic queries.

**Methodology:** For each query, "without CCE" means reading the full content of every file the query touches. "With CCE" means only the relevant code chunks. This is conservative (agents often read more files than needed).

| Metric | Result |
|--------|--------|
| **Token savings** | **92.9%** (75,355 → 5,381 tokens/query avg) |
| Recall@10 (found the right files) | 0.80 |
| Precision@10 | 0.30 |
| Latency p50 | 0.4ms |
| Queries tested | 20 |

<details>
<summary><strong>Per-query breakdown</strong></summary>

| Query | Full file | Served | Saved |
|-------|-----------|--------|-------|
| How does FastAPI handle dependency injection? | 75,628 | 6,493 | 91% |
| How are route decorators like @app.get implemented? | 94,773 | 1,133 | 99% |
| How does OAuth2 password bearer authentication work? | 9,396 | 8,792 | 6% |
| How does FastAPI generate OpenAPI schema? | 58,499 | 8,233 | 86% |
| How are request body parameters validated? | 95,380 | 4,340 | 95% |
| How does the Swagger UI docs page get served? | 52,119 | 6,962 | 87% |
| How does FastAPI handle CORS middleware? | 95,073 | 4,435 | 95% |
| How are HTTP exceptions and error handlers implemented? | 47,547 | 1,627 | 97% |
| How does the APIRouter class work? | 98,420 | 7,890 | 92% |
| How are WebSocket endpoints defined and handled? | 96,636 | 2,499 | 97% |
| How does the HTTPBearer security scheme work? | 6,999 | 4,447 | 36% |
| How does FastAPI handle background tasks? | 97,997 | 5,824 | 94% |
| How are API key security dependencies implemented? | 38,439 | 3,568 | 91% |
| How does FastAPI integrate with Jinja2 templates? | 103,725 | 5,604 | 95% |
| How does the FastAPI application class initialize? | 56,845 | 5,158 | 91% |
| How are path parameters and query parameters resolved? | 85,321 | 3,943 | 95% |
| How does FastAPI implement Server-Sent Events streaming? | 97,299 | 4,985 | 95% |
| What Pydantic compatibility layer does FastAPI use? | 101,536 | 5,701 | 94% |

</details>

**Reproduce it yourself:**

```bash
pip install code-context-engine
python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi
```

Full results in [`benchmarks/results/fastapi.md`](benchmarks/results/fastapi.md). Queries and methodology in [`benchmarks/`](benchmarks/).

---

## What you get

**9 MCP tools** that Claude uses automatically:

| Tool | What it does |
|------|-------------|
| `context_search` | Hybrid vector + BM25 search with graph expansion |
| `expand_chunk` | Full source for a compressed result |
| `related_context` | Find code via graph edges (calls, imports) |
| `session_recall` | Recall decisions from past sessions |
| `record_decision` | Save a decision for future sessions |
| `record_code_area` | Record which files were worked in |
| `index_status` | Check index freshness |
| `reindex` | Re-index a file or the full project |
| `set_output_compression` | Adjust response verbosity (`off` / `lite` / `standard` / `max`) |

**Live dashboard** with donut charts, file health, and session history:

```bash
cce dashboard
```

![CCE Dashboard](https://raw.githubusercontent.com/elara-labs/code-context-engine/main/docs/dashboard.png)

**Dollar estimates** fetched from live Anthropic pricing:

```bash
cce savings --all    # see savings across all projects
```

---

## How it works (the short version)

1. **Index:** Tree-sitter parses your code into semantic chunks (functions, classes, modules). Stored as vector embeddings locally.
2. **Search:** Claude calls `context_search`. Hybrid vector + BM25 retrieval finds the right chunks. Code graph adds related files automatically.
3. **Compress:** Chunks are truncated to signatures + docstrings (or LLM-summarized if Ollama is running).
4. **Remember:** Decisions and code areas persist across sessions via `session_recall`.
5. **Track:** Every query is logged. `cce savings` shows exactly how much you saved.

Re-indexing after edits takes under 1 second (96% embedding cache hit rate). Git hooks keep the index current automatically.

---

## What makes CCE different

### It saves where the money is

Output compression tools (like Caveman) save 20-75% on output tokens. Output is 5-15% of your bill. Net savings: ~11%.

CCE saves on **input** tokens (92.9% on FastAPI, [independently benchmarked](#benchmark-fastapi-independently-verified)). Input is 85-95% of your bill.

### It actually understands your code

Not a text search. Tree-sitter AST parsing creates semantic chunks. Hybrid retrieval merges vector similarity with BM25 keyword matching via Reciprocal Rank Fusion. A confidence scorer blends similarity (50%), keyword match (30%), and recency (20%). Graph expansion walks CALLS/IMPORTS edges to pull in related code.

### It remembers

`record_decision("use JWT for auth", reason="session tokens flagged by legal")` is stored in SQLite and surfaces via `session_recall` in the next session. No re-explaining your architecture.

### It tracks real savings

Not estimates. Actual tokens served vs full-file baseline, broken down by 7 buckets (retrieval, compression, output, memory, grammar, summarization, progressive disclosure). Dollar costs fetched from Anthropic's pricing page.

### It is secure by default

Secret files (.env, *.pem, credentials.json) are never indexed. Content is scanned for AWS keys, GitHub tokens, Slack tokens, Stripe keys, JWTs, and generic credentials. PII (emails, IPs, SSNs, credit cards) is scrubbed from memory writes. All MCP file paths are validated against path traversal.

---

## Under the hood

<details>
<summary><strong>Content-Hash Embedding Cache</strong></summary>

SHA-256 fingerprint per chunk, salted with model name. Re-index skips unchanged code. Binary float32 storage (10x smaller than JSON). Typical re-index: 96% cache hit, under 1 second.
</details>

<details>
<summary><strong>sqlite-vec: 2 MB instead of 217 MB</strong></summary>

Replaced LanceDB with sqlite-vec. Same cosine-distance quality, 99% smaller install. WAL mode + PRAGMA NORMAL for 80% write speedup. Vectors, FTS5, code graph, and compression cache all in three SQLite files.
</details>

<details>
<summary><strong>Deterministic Grammar Compression</strong></summary>

Memory entries compressed without LLM calls. Drops articles, fillers, pronouns. Three levels (lite/full/ultra, 20-60% savings). Code, paths, URLs preserved byte-for-byte. Same input always yields same output.
</details>

<details>
<summary><strong>Fail-Closed Hook Design</strong></summary>

5 Claude Code lifecycle hooks capture session context. Every hook runs `curl ... || true`, so a crashed server never blocks the user. SessionStart injects bootstrap context; others capture silently.
</details>

<details>
<summary><strong>Dynamic Pricing</strong></summary>

Dollar estimates in `cce savings` come from live Anthropic pricing (HTML table parsed, cached 7 days, offline fallback). No manual updates when rates change.
</details>

<details>
<summary><strong>Append-Only Savings Ledger</strong></summary>

7 buckets track every token saved: retrieval, chunk compression, output compression, memory recall, grammar, turn summarization, progressive disclosure. Survives restarts. Powers CLI and dashboard analytics.
</details>

---

## CLI at a glance

```bash
cce init                    # Index + install hooks + register MCP
cce                         # Status banner
cce savings                 # Token savings with dollar estimates
cce savings --all           # All projects
cce dashboard               # Web dashboard with live charts
cce search "auth flow"      # Test a query
cce status                  # Index health + config
cce services                # Ollama + dashboard + MCP status
cce commands add-rule '...' # Project rules for Claude
cce uninstall               # Clean removal of all CCE artifacts
```

Run `cce list` for the full command reference.

---

## Configuration

Zero-config by default. Override what you need in `~/.cce/config.yaml` or `.context-engine.yaml`:

```yaml
compression:
  level: standard          # minimal | standard | full
  output: standard         # off | lite | standard | max

retrieval:
  top_k: 20
  confidence_threshold: 0.5

pricing:
  model: opus              # opus | sonnet | haiku
```

---

## Output Compression

CCE also compresses Claude's responses (same concept as Caveman):

| Level | Style | Savings |
|-------|-------|---------|
| `off` | Full output | 0% |
| `lite` | No filler or hedging | ~30% |
| `standard` | Fragments, drop articles | ~65% |
| `max` | Telegraphic | ~75% |

Tell Claude: "switch to max compression" or "turn off compression". Code blocks and commands are never compressed.

---

## Disk Footprint

| Component | Size |
|-----------|------|
| Installed package | ~189 MB (ONNX Runtime is 66 MB of that) |
| Embedding model (one-time download) | ~60 MB |
| Index per project (small/medium/large) | 5-60 MB |

No GPU required. Embedding model runs on CPU via ONNX Runtime.

---

## Supported Languages

**AST-aware chunking (10 extensions):**

| Language | Extensions |
|----------|-----------|
| Python | `.py` |
| JavaScript | `.js`, `.jsx` |
| TypeScript | `.ts`, `.tsx` |
| PHP | `.php` |
| Go | `.go` |
| Rust | `.rs` |
| Java | `.java` |

**Fallback chunking:** All other text files (Markdown, YAML, config, etc.) chunked by line range.

---

## Documentation

| Page | Content |
|------|---------|
| [Examples](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Examples.md) | Real conversations with Claude |
| [How It Works](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/How-It-Works.md) | Full 9-stage pipeline |
| [CLI Reference](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/CLI-Reference.md) | Every command with output |
| [Configuration](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Configuration.md) | All config options |
| [Project Commands](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Project-Commands.md) | Rules and preferences for Claude |
| [Tech Stack](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Tech-Stack.md) | Every library and why |

---

## Roadmap

- [x] Semantic indexing + hybrid retrieval + graph expansion
- [x] Cross-session memory (decisions, code areas, session recall)
- [x] Web dashboard with live charts
- [x] Token savings tracking with dollar estimates
- [x] Output compression (off / lite / standard / max)
- [x] Content-hash embedding cache (96% hit rate on re-index)
- [x] sqlite-vec migration (99% smaller install)
- [x] Dynamic pricing from Anthropic docs
- [x] 7-layer security (secrets, PII, path traversal, audit log)
- [x] Clean uninstall (removes all CCE artifacts)
- [x] AST-aware chunking for PHP, Go, Rust, Java (tree-sitter)
- [x] Multi-editor support (Cursor, VS Code/Copilot, Gemini CLI)
- [ ] Tree-sitter support for C, C++, Ruby, Swift, Kotlin
- [ ] Docker support for remote mode

---

## Contributing

Contributions welcome. See [https://github.com/elara-labs/code-context-engine/blob/main/CONTRIBUTING.md](https://github.com/elara-labs/code-context-engine/blob/main/CONTRIBUTING.md) for setup.

---

## License

MIT. See [LICENSE](LICENSE).

## Authors

- [Fazle Elahee](https://github.com/fazleelahhee)
- [Raj](https://github.com/rajkumarsakthivel)

## Acknowledgments

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) · [MCP](https://modelcontextprotocol.io) · [sqlite-vec](https://github.com/asg017/sqlite-vec) · [Tree-sitter](https://tree-sitter.github.io/) · [fastembed](https://github.com/qdrant/fastembed) · [Ollama](https://ollama.com/)

---

<p align="center">
  <strong>If CCE saves you tokens, give it a star.</strong>
</p>
