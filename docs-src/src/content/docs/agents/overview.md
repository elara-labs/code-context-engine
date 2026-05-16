---
title: Multi-Agent Support
description: How CCE integrates with different AI coding agents and editors.
---

Code Context Engine works with any AI coding agent that supports MCP (Model Context Protocol). The `cce init` command auto-detects which agents are present in your environment and configures them automatically.

## The `--agent` flag

```bash
cce init --agent auto      # Default. Detects installed agents.
cce init --agent claude    # Configure only Claude Code
cce init --agent cursor    # Configure only Cursor
cce init --agent copilot   # Configure only VS Code / Copilot
cce init --agent gemini    # Configure only Gemini CLI
cce init --agent codex     # Configure only Codex CLI
cce init --agent all       # Configure all supported agents
```

When no `--agent` flag is provided, `cce init` defaults to `auto`, which scans for known config files and editors.

## Supported Editors and Agents

| Agent | MCP Config Path | Instruction File |
|-------|----------------|-----------------|
| Claude Code | `.mcp.json` | `CLAUDE.md` |
| Cursor | `.cursor/mcp.json` | `.cursorrules` |
| VS Code / Copilot | `.vscode/mcp.json` | `.github/copilot-instructions.md` |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` |
| Codex CLI | `~/.codex/config.toml` (global) | `AGENTS.md` |
| OpenCode | `opencode.json` | (none) |
| Tabnine | `.tabnine/agent/settings.json` | `TABNINE.md` |

## How it works

Each agent integration does two things:

1. **Registers the MCP server** so the agent can call `context_search` and other CCE tools.
2. **Writes an instruction file** telling the agent to prefer CCE's search over raw file reads.

The instruction file content is managed by CCE and wrapped in markers (`CCE:BEGIN` / `CCE:END`) so it can be updated on upgrade without touching your own content.

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
