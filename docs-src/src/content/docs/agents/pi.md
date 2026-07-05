---
title: Pi
description: Setting up CCE with the Pi coding agent.
---

Pi does not support MCP natively. CCE provides the standard `.mcp.json` config (consumed by a companion adapter) and writes `AGENTS.md` (loaded automatically by Pi at startup).

## Quick setup

```bash
cce init              # Auto-detects Pi if .pi/ exists in the project
cce init --agent pi   # Explicitly configure Pi
cce init --agent all  # Includes Pi alongside all other editors
```

## Adapter required

Because Pi has no built-in MCP client, you need a companion adapter extension that reads `.mcp.json` and bridges Pi to the CCE MCP server. One option is [pi-mcp-adapter](https://github.com/nicobailon/pi-mcp-adapter).

Without the adapter, Pi can still read `AGENTS.md` and follow the CCE usage instructions, but it cannot call `context_search` as an MCP tool.

## Files created

### `.mcp.json`

Registers the CCE MCP server. Shared with Claude Code if both are configured.

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

### `AGENTS.md`

Contains instructions for Pi to prefer `context_search` for code retrieval. The CCE block is wrapped in markers so your own content is preserved during upgrades.

## Auto-detection

`cce init` (without `--agent`) detects Pi when a `.pi/` directory exists in the project root.

## Verify it's working

1. Install and activate the MCP adapter for Pi
2. Restart Pi after running `cce init`
3. Ask Pi a question about your code
4. Run `cce savings` to confirm queries are being tracked

## Troubleshooting

### Pi doesn't call context\_search

Pi needs a MCP adapter to forward tool calls. Confirm the adapter is installed and points at the `.mcp.json` in your project root.

### "cce: command not found"

Ensure `cce` is on your PATH. Add `~/.local/bin` to your shell profile if installed with `uv tool install`.

### Auto-detection doesn't find Pi

CCE looks for a `.pi/` directory in the project root. If it doesn't exist, use `cce init --agent pi` to force configuration.
