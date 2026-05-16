---
title: Codex CLI
description: Setting up CCE with OpenAI's Codex CLI.
---

Codex CLI uses a global configuration file rather than per-project MCP config. CCE registers itself in the user-level config with a project-specific section.

## Quick setup

```bash
cce init --agent codex
```

## Files created

### `~/.codex/config.toml`

Codex CLI has no per-project MCP configuration. Instead, CCE adds a project section (keyed by a hash of the project path) to the user-global config file.

```toml
[projects."a1b2c3d4"]
path = "/Users/you/projects/my-project"

[projects."a1b2c3d4".mcpServers.context-engine]
command = "cce"
args = ["serve"]
```

### `AGENTS.md`

Contains instructions for Codex to use `context_search` for code exploration. The CCE block is wrapped in markers so your own content is preserved during upgrades.

## Important notes

- Codex CLI does not support per-project `.mcp.json` files. The global `~/.codex/config.toml` is the only location for MCP server registration.
- Each project gets its own section identified by a hash, so multiple projects can coexist in the same config file.
- Running `cce uninstall` removes only the section for the current project.
