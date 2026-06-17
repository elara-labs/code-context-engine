---
title: OpenCode
description: Setting up CCE with OpenCode terminal assistant.
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
  "mcp": {
    "context-engine": {
      "command": "cce",
      "args": ["serve", "--project-dir", "/path/to/your/project"]
    }
  }
}
```

Note: OpenCode uses `"mcp"` as the servers key.

## No instruction file

OpenCode does not use a separate instruction file. The MCP server registration is sufficient for OpenCode to discover and use CCE's tools.

## Verify it's working

1. Start an OpenCode session after running `cce init`
2. The `context_search` tool should be available
3. Ask a code question and check the tool output for `context_search` calls
4. Run `cce savings` to check if queries are being tracked

## Troubleshooting

### OpenCode doesn't detect the MCP server

Check that `opencode.json` exists in your project root and contains the `context-engine` entry. If you have an existing `opencode.jsonc` (with comments), CCE merges into that file.

### "cce: command not found"

Ensure `cce` is on your PATH. If installed with `uv tool install`, add `~/.local/bin` to your shell profile.
