<p align="center">
  <img src="https://raw.githubusercontent.com/elara-labs/code-context-engine/main/docs/logo.svg" alt="Code Context Engine" width="140">
</p>

<h1 align="center">Code Context Engine</h1>

<p align="center">
  <strong>Index your codebase. AI searches instead of re-reading files.<br>94% token savings, reproducibly benchmarked.</strong>
</p>

<p align="center">
  <a href="https://elara-labs.github.io/code-context-engine/">Website</a> · <a href="https://elara-labs.github.io/code-context-engine/guide/">Docs</a> · <a href="https://elara-labs.github.io/code-context-engine/guide/why-cce/">Why CCE?</a> · <a href="https://elara-labs.github.io/code-context-engine/blog/benchmark-fastapi.html">Benchmark</a> · <a href="https://github.com/elara-labs/code-context-engine">GitHub</a>
</p>

<br>

<p align="center">
  <a href="https://pypi.org/project/code-context-engine/"><img src="https://img.shields.io/pypi/v/code-context-engine?style=flat-square&color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://pepy.tech/project/code-context-engine"><img src="https://img.shields.io/pepy/dt/code-context-engine?style=flat-square&label=downloads&color=blue" alt="Downloads"></a>
  <a href="https://github.com/elara-labs/code-context-engine/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/elara-labs/code-context-engine/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="https://registry.modelcontextprotocol.io/?q=code-context-engine"><img src="https://img.shields.io/badge/MCP_Registry-listed-brightgreen?style=flat-square" alt="MCP Registry"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/elara-labs/code-context-engine"><img src="https://img.shields.io/github/stars/elara-labs/code-context-engine?style=flat-square&label=stars" alt="Stars"></a>
</p>

<p align="center">
  <sub>Python 3.11+ · macOS · Linux · Windows</sub>
</p>

<br>

<p align="center">
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Claude_Code-352318?style=for-the-badge&logo=anthropic&logoColor=D4A27F" alt="Claude Code"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/VS_Code-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white" alt="VS Code"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Cursor-000?style=for-the-badge" alt="Cursor"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Gemini_CLI-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini CLI"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Codex_CLI-412991?style=for-the-badge" alt="Codex CLI"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/OpenCode-22C55E?style=for-the-badge&logo=gnometerminal&logoColor=white" alt="OpenCode"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Tabnine-4B32C3?style=for-the-badge&logo=tabnine&logoColor=white" alt="Tabnine"></a>&nbsp;
  <a href="#install-and-see-savings-in-60-seconds"><img src="https://img.shields.io/badge/Pi-000?style=for-the-badge" alt="Pi"></a>
</p>

<p align="center">
  <sub>One command. Auto-detects your editor. Zero cloud, zero config.</sub>
</p>

<br>

<p align="center">
  <img src="https://raw.githubusercontent.com/elara-labs/code-context-engine/main/docs/demo.gif" alt="CCE Demo" width="720">
</p>

---

## Use cases

| | Use case | How CCE helps |
|---|---|---|
| **💰** | **Reduce Claude Code costs** | 94% fewer input tokens per session |
| **🔒** | **Keep code private** | Everything local, no cloud indexing |
| **🔄** | **Multi-editor teams** | One index across Claude Code, Cursor, VS Code, Gemini CLI |
| **🧠** | **Cross-session memory** | Decisions and context survive restarts |
| **⚡** | **Faster responses** | Less context = faster Claude replies |
| **📊** | **Track actual savings** | Dollar amounts, not estimates |

---

## Quick start

One command. 30 seconds.

```bash
uvx --from "code-context-engine[local]" cce init    # install + index + configure, one shot
```

Or if you prefer a persistent install:

```bash
uv tool install "code-context-engine[local]"    # or: pipx install "code-context-engine[local]"
cd /path/to/your/project
cce init
```

Restart your editor. Done. Every question now hits the index instead of re-reading files.

> **Already have Ollama?** Skip `[local]` and use `uv tool install code-context-engine` instead. CCE auto-detects Ollama at localhost:11434 and uses `nomic-embed-text`.

<details>
<summary><strong>System requirements</strong></summary>

Python 3.11+ and a C compiler (for tree-sitter grammars).

| Platform | Setup |
|----------|-------|
| **macOS** | `xcode-select --install` |
| **Ubuntu/Debian** | `sudo apt install build-essential cmake` |
| **Fedora/RHEL** | `sudo dnf install gcc gcc-c++ cmake` |
| **Windows** | [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (C++ workload) + [CMake](https://cmake.org/download/) |

Tested on macOS, Linux, Windows with Python 3.11/3.12/3.13.
</details>

`cce init` auto-detects your editor and writes the right config. To target a
specific agent, use `--agent claude`, `--agent codex`, `--agent copilot`, or
`--agent all`.

| Editor | Config written | Instructions |
|--------|---------------|--------------|
| Claude Code | `.mcp.json` | `CLAUDE.md` |
| VS Code / Copilot | `.vscode/mcp.json` | `.github/copilot-instructions.md` |
| Cursor | `.cursor/mcp.json` | `.cursorrules` |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` |
| OpenAI Codex | `~/.codex/config.toml` (user-global, per-project section) | `AGENTS.md` |
| OpenCode | `opencode.json` | |
| Tabnine | `.tabnine/agent/settings.json` | `TABNINE.md` |

Multiple editors in the same project? All get configured in one command.

**Codex note:** Codex CLI reads MCP servers from `~/.codex/config.toml` only —
it has no per-project config. `cce init` adds one `[mcp_servers.cce-<project>-<hash>]`
section per project so multiple projects coexist; `cce uninstall` removes only
the section for the current project.

```
  my-project · 38 queries · last query 5m ago

  ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶  88% tokens saved

  Input savings   1.9M  tokens   $27.78
  Output savings  4.8k  tokens   $0.36
  ──────────────────────────────────────────
  Total saved   1.9M  tokens   $28.15

  Breakdown:
    retrieval              84%  ▰▰▰▰▰▰▰▰▰▰    1.8M   $26.76 · 12 calls
    chunk compression       3%  ▰▱▱▱▱▱▱▱▱▱   68.5k    $1.03 · 12 calls
    output compression*    <1%  ▰▱▱▱▱▱▱▱▱▱    4.8k    $0.36 · 12 calls

  Cost estimate based on Opus pricing (input $15.0/1M, output $75.0/1M)
```

Supports Anthropic, OpenAI, and Google model pricing. Configure via `pricing.model` in `~/.cce/config.yaml`.

---

## Why this matters

Input tokens are 85-95% of your Claude Code bill. CCE cuts them by 94% ([benchmarked on FastAPI](#benchmark-fastapi-reproducible)).

```
Without CCE:    Claude reads payments.py + shipping.py   = 45,000 tokens
With CCE:       context_search "payment flow"            =    800 tokens
```

| | Without CCE | With CCE |
|---|---|---|
| Session startup | Re-reads files every time | Queries the index |
| Finding a function | Read entire 800-line file | Get the 40-line function |
| Cross-session memory | None | Decisions + code areas persisted |
| Token cost (Sonnet, medium project) | ~$0.14/session | ~$0.04/session |

---

## Benchmark: FastAPI (reproducible)

We benchmarked CCE against [FastAPI](https://github.com/fastapi/fastapi) (53 source files, 180K tokens) with 20 real coding questions. No cherry-picking, no synthetic queries.

**Methodology:** For each query, "without CCE" means reading the full content of every file the query touches. "With CCE" means the relevant chunks after compression.

**Important baseline note:** The 94% number is measured against full-file reads, not against what Claude Code actually does. In practice, Claude Code already uses grep, partial file reads, and targeted tools, so the real-world savings compared to normal Claude Code behavior will be lower than 94%. We use full-file as the baseline because it's reproducible and deterministic (no agent behavior variability). The benchmark measures CCE's retrieval efficiency, not a head-to-head comparison with Claude Code's built-in exploration.

| Metric | Result |
|--------|--------|
| **Retrieval savings** | **94%** (83,681 → 4,927 tokens/query) |
| Compression (additional, on retrieved chunks) | 89% (4,927 → 523 tokens/query) |
| Recall@10 (found the right files) | 0.90 |
| Latency p50 | 0.4ms |
| Queries tested | 20 |

### Per-Layer Savings (each measured independently)

| Layer | What it does | Savings | Method |
|-------|-------------|---------|--------|
| **Retrieval** | Full files → relevant code chunks | 94% | measured |
| **Chunk Compression** | Raw chunks → signatures + docstrings | 89% | measured |
| **Grammar** | Drops articles/fillers from memory text | 13% | measured |

Output compression (reducing Claude's reply length) provides additional savings (~65% estimated) but is not included in the headline number above.

### Multi-language benchmarks

| Repo | Language | Files | Retrieval savings | Recall@10 |
|------|----------|-------|-------------------|-----------|
| [FastAPI](benchmarks/results/fastapi.md) | Python | 53 | **94%** | 0.90 |
| [chi](benchmarks/results/chi.md) | Go | 94 | **76%** | 0.67 |
| [fiber](benchmarks/results/fiber.md) | Go (monorepo) | 396 | **93%** | 0.07 |

Go's shorter files reduce the retrieval headroom (smaller baseline). Monorepos dilute recall at top-10 (fiber). Middleware queries with one-feature-per-file hit R=1.00 consistently.

**Reproduce it yourself:**

```bash
pip install code-context-engine
python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi
python benchmarks/run_benchmark.py --repo https://github.com/go-chi/chi.git --source-dir .
```

Full results in [`benchmarks/results/`](benchmarks/results/). Queries and methodology in [`benchmarks/`](benchmarks/).

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

**Dollar estimates** with multi-provider pricing (Anthropic, OpenAI, Google):

```bash
cce savings --all    # see savings across all projects
```

---

## How it works

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

CCE saves on **input** tokens (94% retrieval savings on FastAPI, [reproducibly benchmarked](#benchmark-fastapi-reproducible)). Input is 85-95% of your bill.

### It actually understands your code

Not a text search. Tree-sitter AST parsing creates semantic chunks. Hybrid retrieval merges vector similarity with BM25 keyword matching via Reciprocal Rank Fusion. A confidence scorer blends similarity (50%), keyword match (30%), and recency (20%). Graph expansion walks CALLS/IMPORTS edges to pull in related code.

### It remembers

`record_decision("use JWT for auth", reason="session tokens flagged by legal")` is stored in SQLite and surfaces via `session_recall` in the next session. No re-explaining your architecture.

### It tracks real savings

Not estimates. Actual tokens served vs full-file baseline, broken down by buckets (retrieval, compression, output, memory, grammar). Dollar costs fetched from Anthropic's pricing page. Savings summary shown at every session start.

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
<summary><strong>Multi-Provider Pricing</strong></summary>

Dollar estimates in `cce savings` support 15+ models across Anthropic, OpenAI, and Google. Static pricing ships with CCE, live Anthropic pricing is fetched and cached 7 days. Configure `pricing.model` (e.g. `gpt-4o`, `gemini-2.5-pro`, `sonnet`) or override with `pricing.input` / `pricing.output` for custom rates.
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
  ollama_url: http://localhost:11434   # point at a remote Ollama if desired

retrieval:
  top_k: 20
  confidence_threshold: 0.5

pricing:
  model: opus              # opus | sonnet | haiku | gpt-4o | gemini-2.5-pro | ...
  # input: 15.0            # override $/1M input tokens
  # output: 75.0           # override $/1M output tokens
```

**Remote Ollama:** If you run Ollama on another machine in your network, set `compression.ollama_url` (e.g. `http://nas.local:11434`) or export `CCE_OLLAMA_URL` — the env var wins. CCE probes the endpoint and falls back to truncation-only compression when it's unreachable, so a flaky link won't break indexing.

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
| Core install (Ollama backend) | ~17 MB |
| With `[local]` extra (fastembed + ONNX) | ~189 MB |
| Embedding model (one-time download) | ~60 MB (fastembed) or managed by Ollama |
| Index per project (small/medium/large) | 5-60 MB |

No GPU required. With Ollama, embeddings are handled by the Ollama server. With the `[local]` extra, the embedding model runs on CPU via ONNX Runtime.

---

## Supported Languages

**AST-aware chunking (tree-sitter parsed, 10 extensions):**

| Language | Extensions |
|----------|-----------|
| Python | `.py` |
| JavaScript | `.js`, `.jsx` |
| TypeScript | `.ts`, `.tsx` |
| PHP | `.php` |
| Go | `.go` |
| Rust | `.rs` |
| Java | `.java` |

**Language-aware fallback chunking (40+ extensions):**

| Category | Languages |
|----------|-----------|
| Web | HTML, CSS, SCSS, LESS, Vue, Svelte |
| Systems | C, C++, C#, Zig, Nim |
| Mobile | Swift, Kotlin, Dart |
| Functional | Haskell, Scala, Clojure, Elixir, Erlang, F# |
| Scripting | Ruby, Perl, Lua, R, Bash/Zsh |
| Data/Config | JSON, YAML, TOML, XML, SQL, GraphQL, Protobuf |
| DevOps | Terraform, HCL, Dockerfile |
| Docs | Markdown |

All other text files are chunked by line range. Binary files are skipped.

---

## Documentation

| Page | Content |
|------|---------|
| [How Much Are You Spending on AI Coding Tokens?](https://elara-labs.github.io/code-context-engine/blog/real-cost-of-ai-coding-tokens.html) | The math on input vs output tokens |
| [What is CCE? (Complete Guide)](https://elara-labs.github.io/code-context-engine/blog/what-is-code-context-engine.html) | Setup, tools, how it works, FAQ |
| [How to Save Claude Code Tokens](https://elara-labs.github.io/code-context-engine/blog/save-claude-code-tokens.html) | Cost breakdown and savings guide |
| [Benchmark Deep Dive](https://elara-labs.github.io/code-context-engine/blog/benchmark-fastapi.html) | Full FastAPI benchmark methodology |
| [Comparison with Alternatives](https://elara-labs.github.io/code-context-engine/comparison.html) | CCE vs Cursor, Aider, Continue, Greptile |
| [Examples](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Examples.md) | Real conversations with Claude |
| [How It Works](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/How-It-Works.md) | Full 9-stage pipeline |
| [CLI Reference](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/CLI-Reference.md) | Every command with output |
| [Configuration](https://github.com/elara-labs/code-context-engine/blob/main/docs/wiki/Configuration.md) | All config options |

---

## FAQ

### Does CCE affect response quality?

No. Quality stays the same or slightly improves.

CCE replaces "dump the entire file" with "search for the relevant function." The model still gets the code it needs (0.90 Recall@10 in benchmarks). Less irrelevant context means less noise competing for attention, which can improve the model's focus on your actual question.

### How does output token savings work?

CCE writes output compression rules directly into your agent's instruction files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, etc.) during `cce init`. These rules apply to the **entire session**, not just CCE tool responses, so every reply from the agent follows them.

Set the level in `~/.cce/config.yaml` or `.context-engine.yaml`:

```yaml
compression:
  output: max       # off | lite | standard | max
```

Then re-run `cce init` to update instruction files. Or change at runtime:

```
set_output_level output_level=max
```

| Level | Savings | What it does |
|-------|---------|--------------|
| `off` | 0% | No compression |
| `lite` | ~25% | Removes filler/hedging/pleasantries + diff-only for code changes |
| `standard` | ~70% | Drops articles, fragments, short synonyms + diff-only for code |
| `max` | ~80% | Telegraphic style + diff-only for code |

Default is `standard`. All levels include **code output rules** that tell the model to show only changed lines (not full file rewrites), which is where most output tokens go in coding sessions. The `max` level produces very terse prose (similar to "caveman mode"). Code blocks, paths, and commands are never compressed regardless of level.

### Where do the savings come from?

Most savings are **input tokens** (what goes into the model):

| Layer | Type | Typical savings |
|-------|------|-----------------|
| Retrieval | Input | 94% (full files → relevant chunks) |
| Chunk compression | Input | 89% (chunks → signatures) |
| Grammar compression | Input | 13% (article/filler removal) |
| Turn summarization | Input | varies (session history) |
| Progressive disclosure | Input | varies (tool payloads) |
| Output compression | Output | 25-80% (depends on level) |

Output tokens cost 5x more per token (e.g. Opus: $15/1M input vs $75/1M output), so even a small output reduction has outsized cost impact.

---

## Roadmap

- [x] Multi-repo benchmarks (FastAPI, chi, fiber)
- [ ] More benchmarks (Django, Express)
- [ ] Tree-sitter support for C, C++, Ruby, Swift, Kotlin
- [ ] Docker support for remote mode

See [CHANGELOG.md](CHANGELOG.md) for shipped features.

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

<!-- mcp-name: io.github.ai-elara/code-context-engine -->
