---
title: Tabnine
description: Setting up CCE with Tabnine's AI agent.
---

Tabnine uses a project-local settings file and an instruction file for MCP integration.

## Quick setup

```bash
cce init              # Auto-detects Tabnine if .tabnine/ exists
cce init --agent all  # Explicitly includes Tabnine
```

## Files created

### `.tabnine/agent/settings.json`

Registers the CCE MCP server for Tabnine's agent.

```json
{
  "mcpServers": {
    "context-engine": {
      "command": "cce",
      "args": ["serve", "--project-dir", "/path/to/your/project"]
    }
  }
}
```

### `TABNINE.md`

Contains instructions for Tabnine to prefer `context_search` for code retrieval. The CCE block is wrapped in markers so your own content is preserved during upgrades.

## Verify it's working

1. Restart Tabnine after running `cce init`
2. Use Tabnine's chat to ask a code question
3. Check for `context_search` tool calls in the output
4. Run `cce savings` to verify queries are tracked

## Troubleshooting

### Tabnine doesn't detect the MCP server

Check `.tabnine/agent/settings.json` exists and contains the `context-engine` entry. If missing, re-run `cce init --agent all`.

### "cce: command not found"

Ensure `cce` is on your PATH. Add `~/.local/bin` to your shell profile if installed with `uv tool install`.

### Auto-detection doesn't find Tabnine

CCE looks for a `.tabnine/` directory in the project root. If it doesn't exist, use `cce init --agent all` to force configuration.
