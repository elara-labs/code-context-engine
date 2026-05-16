---
title: Cursor
description: Setting up CCE with Cursor editor.
---

Cursor has its own built-in codebase indexing, but CCE adds compressed retrieval and token savings tracking on top.

## Quick setup

```bash
cce init              # Auto-detects Cursor if .cursor/ exists
cce init --agent all  # Explicitly includes Cursor
```

## Files created

### `.cursor/mcp.json`

Registers the CCE MCP server for Cursor's agent mode.

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

### `.cursorrules`

Contains instructions for Cursor's AI to prefer `context_search` over raw file reads. The CCE block is wrapped in markers so your own rules are preserved.

## Working with Cursor's built-in indexing

Cursor indexes your codebase for its own retrieval. CCE complements this by:

- Providing compressed context that uses fewer tokens per query.
- Tracking token savings so you can measure cost reduction.
- Offering graph-aware retrieval that follows code relationships.

Both systems can run side by side without conflict.

## Restarting after setup

After running `cce init`, restart Cursor to pick up the new MCP server configuration.
