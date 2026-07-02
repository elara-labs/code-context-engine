---
title: Multi-Agent Support
description: How CCE integrates with different AI coding agents and editors.
---

Code Context Engine works with any AI coding agent that supports MCP (Model Context Protocol). The `cce init` command auto-detects which agents are present in your environment and configures them automatically.

## The `--agent` flag

```bash
cce init                   # Default. Detects installed agents.
cce init --agent claude    # Configure only Claude Code
cce init --agent copilot   # Configure only VS Code / Copilot
cce init --agent codex     # Configure only Codex CLI
cce init --agent all       # Configure all supported agents
```

When no `--agent` flag is provided, `cce init` defaults to `auto`, which scans for known config files and editor directories.

## Supported Editors and Agents

| Agent | MCP Config | Instruction File | Scope | Detection |
|-------|-----------|-----------------|-------|-----------|
| [Claude Code](/code-context-engine/guide/agents/claude/) | `.mcp.json` | `CLAUDE.md` | Project | Always configured |
| [VS Code / Copilot](/code-context-engine/guide/agents/copilot/) | `.vscode/mcp.json` | `.github/copilot-instructions.md` | Project | `.vscode/` exists |
| [Cursor](/code-context-engine/guide/agents/cursor/) | `.cursor/mcp.json` | `.cursorrules` | Project | `.cursor/` or `.cursorrules` exists |
| [Gemini CLI](/code-context-engine/guide/agents/gemini/) | `.gemini/settings.json` | `GEMINI.md` | Project | `.gemini/` or `GEMINI.md` exists |
| [Codex CLI](/code-context-engine/guide/agents/codex/) | `~/.codex/config.toml` | `AGENTS.md` | User (global) | `~/.codex/` or VS Code OpenAI extension |
| [OpenCode](/code-context-engine/guide/agents/opencode/) | `opencode.json` | (none) | Project | `opencode.json` exists |
| [Tabnine](/code-context-engine/guide/agents/tabnine/) | `.tabnine/agent/settings.json` | `TABNINE.md` | Project | `.tabnine/` exists |

## How it works

Each agent integration does two things:

1. **Registers the MCP server** so the agent can call `context_search` and other CCE tools.
2. **Writes an instruction file** telling the agent to prefer CCE's search over raw file reads.

The instruction file content is managed by CCE and wrapped in markers (`CCE:BEGIN` / `CCE:END`) so it can be updated on upgrade without touching your own content.

## Cross-agent memory

Decisions, code areas, and session history are stored per-project in `memory.db`, not per-agent. If you switch between Claude Code and Codex on the same project, `session_recall` returns decisions from all prior sessions regardless of which agent created them.

## Re-running for additional agents

You can run `cce init --agent <name>` multiple times. Each run is additive and will not remove previously configured agents.

```bash
cce init --agent claude
cce init --agent copilot   # Adds Copilot config alongside Claude
```

Or configure everything at once:

```bash
cce init --agent all
```

## Common issues across all agents

### "cce: command not found"

The `cce` binary must be on your PATH. Default locations by install method:

| Install method | Binary location |
|---------------|----------------|
| `uv tool install` | `~/.local/bin/cce` |
| `pipx install` | `~/.local/bin/cce` |
| `pip install` | Depends on your Python environment |

Add `~/.local/bin` to your shell profile (`~/.zshrc`, `~/.bashrc`, or equivalent).

### Agent doesn't use context_search

1. Check the instruction file exists (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
2. Verify it contains the `## Context Engine (CCE)` section
3. Restart the agent after setup
4. Re-run `cce init` if the instruction file is missing

### Savings not updating

Savings only increment when the agent calls `context_search` or `expand_chunk`. If the agent uses Read/Grep directly, no savings are recorded. Check `cce savings` for a "last query" timestamp to confirm whether new queries are happening.

### Windows encoding errors

Upgrade to CCE v0.4.24+ which adds explicit UTF-8 encoding to all file I/O. Earlier versions can crash with `UnicodeDecodeError` when config files contain non-ASCII bytes.
