---
title: Cursor
description: Setting up CCE with Cursor editor.
---

Cursor has built-in codebase indexing, but CCE adds compressed retrieval, cross-session memory, and token savings tracking on top.

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
      "args": ["serve", "--project-dir", "/path/to/your/project"]
    }
  }
}
```

### `.cursorrules`

Contains instructions for Cursor's AI to prefer `context_search` over raw file reads. The CCE block is wrapped in markers so your own rules are preserved.

## Working with Cursor's built-in indexing

Cursor indexes your codebase for its own retrieval. CCE complements this by:

- **Compressed context** that uses fewer tokens per query (Cursor's index returns full file content, CCE returns relevant chunks with signature compression)
- **Token savings tracking** so you can measure the cost difference
- **Graph-aware retrieval** that follows code relationships (imports, calls)
- **Cross-session memory** that persists decisions across restarts

Both systems run side by side without conflict. Cursor's indexing handles in-editor completions, CCE handles chat/agent queries.

## Verify it's working

1. Restart Cursor after running `cce init`
2. Open the Composer or Chat panel
3. Ask a code question:

```
Where is the database connection configured?
```

4. Check the tool call output. If Cursor used `context_search`, CCE is active
5. Run `cce savings` in your terminal to see token savings

## Troubleshooting

### Cursor ignores CCE and reads files directly

Cursor may prefer its built-in indexing for some queries. Check that `.cursorrules` contains the CCE instructions block. The instructions tell Cursor to prefer `context_search`, but Cursor's own heuristics may override this for simple lookups.

### "cce: command not found"

Cursor inherits PATH from how it was launched. Ensure `~/.local/bin` (or wherever `cce` is installed) is in your shell profile, then launch Cursor from a terminal with `cursor .`

### MCP tools not showing

Restart Cursor completely (not just reload). MCP config is read at startup, not on config file change.

### Windows path issues

If your project path contains spaces, ensure the path in `.cursor/mcp.json` is correctly quoted. `cce init` handles this automatically, but manual edits can break it.
