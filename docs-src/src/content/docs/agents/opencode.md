---
title: OpenCode
description: Setting up CCE with OpenCode.
---

OpenCode uses a single `opencode.json` file in the project root for all configuration, including MCP servers.

## Quick setup

```bash
cce init              # Auto-detects OpenCode if opencode.json exists
cce init --agent all  # Explicitly includes OpenCode
```

## Files created

### `opencode.json`

CCE adds its MCP server entry to the existing `opencode.json` (or creates one if it does not exist).

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

## No instruction file

OpenCode does not use a separate instruction file. The MCP server registration is sufficient for OpenCode to discover and use CCE's tools.

## Auto-detection

CCE detects OpenCode when an `opencode.json` file exists in your project root. No explicit `--agent` flag is needed.
