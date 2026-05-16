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
      "args": ["serve"]
    }
  }
}
```

### `TABNINE.md`

Contains instructions for Tabnine to prefer `context_search` for code retrieval. The CCE block is wrapped in markers so your own content is preserved.

## Auto-detection

CCE detects Tabnine when a `.tabnine/` directory exists in your project root. No explicit `--agent` flag is needed.
