---
title: Getting Started
description: Install CCE and start saving tokens in under a minute
---

## System requirements

- Python 3.11+ (tested on 3.11, 3.12, 3.13)
- A C compiler and `cmake` (needed to build tree-sitter grammars)

| Platform | Setup |
|----------|-------|
| **macOS** | `xcode-select --install` |
| **Ubuntu/Debian** | `sudo apt install build-essential cmake` |
| **Fedora/RHEL** | `sudo dnf install gcc gcc-c++ cmake` |
| **Windows** | [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (C++ workload) + [CMake](https://cmake.org/download/) |

## Install

CCE needs an embedding backend to index your code. Pick one:

| Option | Install command | What it needs |
|--------|----------------|---------------|
| **Local (recommended)** | `uv tool install "code-context-engine[local]"` | Nothing else. Includes fastembed + ONNX Runtime (~60 MB download on first run). |
| **Ollama** | `uv tool install code-context-engine` | Ollama running at localhost:11434 with `nomic-embed-text` pulled. |

Using pipx instead of uv:

```bash
pipx install "code-context-engine[local]"
```

:::caution
Installing without `[local]` and without Ollama running will cause `cce init` to fail with "No embedding backend available." Always pick one of the two options above.
:::

## Initialize your project

```bash
cd /path/to/your/project
cce init
```

This does everything:
- Detects your embedding backend (fastembed or Ollama)
- Builds vector, FTS, and graph indexes
- Installs git hooks (auto-updates index on commit)
- Writes MCP config for detected editors
- Creates instruction files with output compression rules

### Target a specific agent

```bash
cce init --agent claude     # Claude Code only
cce init --agent codex      # Codex CLI only
cce init --agent copilot    # VS Code / Copilot only
cce init --agent all        # Every supported editor
```

## Verify it works

Restart your editor, then ask a question about your code. The agent will call `context_search` via MCP instead of reading files.

Check your savings:

```bash
cce savings
```

```
  my-project · 5 queries

  ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶  93% tokens saved

  Input savings   42.1k  tokens   $0.63
  Output savings  1.2k  tokens   $0.09
  ──────────────────────────────────────────
  Total saved   43.3k  tokens   $0.72
```

## Embedding backends

CCE auto-detects the best available backend at init time:

1. **fastembed** (with `[local]` extra) — Uses `BAAI/bge-small-en-v1.5`. Works offline, no external services needed. ~60 MB model downloaded on first run.
2. **Ollama** — If running at localhost:11434 with `nomic-embed-text` pulled. Zero extra Python dependencies.

Force a specific backend with `CCE_EMBED_BACKEND=fastembed` or `CCE_EMBED_BACKEND=ollama`.

## Next steps

- [Multi-agent setup](/code-context-engine/guide/agents/overview/) — Configure all your editors
- [Configuration](/code-context-engine/guide/configuration/) — Tune compression, embedding, and more
- [CLI Reference](/code-context-engine/guide/cli-reference/) — All available commands
