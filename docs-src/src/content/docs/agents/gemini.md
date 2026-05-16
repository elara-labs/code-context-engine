---
title: Gemini CLI
description: Setting up CCE with Google's Gemini CLI.
---

CCE integrates with the Gemini CLI through its settings file and an instruction file.

## Quick setup

```bash
cce init              # Auto-detects Gemini CLI if .gemini/ exists
cce init --agent gemini
```

## Files created

### `.gemini/settings.json`

Registers the CCE MCP server for Gemini CLI.

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

### `GEMINI.md`

Contains instructions for Gemini to prefer `context_search` over reading files directly. The CCE block is wrapped in markers so your own content is preserved.

## Auto-detection

CCE detects Gemini CLI when a `.gemini/` directory exists in your project root or home directory. No explicit `--agent` flag is needed if the directory is present.
