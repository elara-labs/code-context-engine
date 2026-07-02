---
title: VS Code / Copilot
description: Setting up CCE with VS Code and GitHub Copilot.
---

CCE integrates with GitHub Copilot's chat agent in VS Code through MCP configuration and a Copilot instructions file.

## Quick setup

```bash
cce init --agent copilot
```

Or let CCE auto-detect (if `.vscode/` exists in your project):

```bash
cce init
```

## Files created

### `.vscode/mcp.json`

Registers the CCE MCP server for Copilot's agent mode.

```json
{
  "servers": {
    "context-engine": {
      "command": "cce",
      "args": ["serve", "--project-dir", "/path/to/your/project"]
    }
  }
}
```

Note: VS Code uses `"servers"` as the key, not `"mcpServers"`.

### `.github/copilot-instructions.md`

Contains instructions for Copilot to use `context_search` for code questions. The CCE block is wrapped in markers so your own Copilot instructions are preserved during upgrades.

## Verify it's working

1. After `cce init`, reload VS Code (Cmd/Ctrl+Shift+P, then "Developer: Reload Window")
2. Open Copilot Chat (Ctrl+Shift+I or the Copilot icon)
3. Switch to Agent mode (click the mode selector at the top of the chat panel)
4. Ask a code question:

```
How does the payment processing work?
```

Copilot should call `context_search` and return results from your indexed codebase. Check the tool call output to confirm.

Then verify savings:

```bash
cce savings
```

## Requirements

- VS Code 1.99+ (MCP support was added in early 2025)
- GitHub Copilot extension installed and active
- Agent mode enabled in Copilot Chat settings

If you don't see MCP tools in Copilot Chat, check that "Agent mode" is enabled:
Settings → Extensions → GitHub Copilot → enable "Chat: Agent"

## Working with existing MCP servers

If you already have a `.vscode/mcp.json` with other MCP servers, `cce init` merges the CCE entry without touching your existing servers.

## Troubleshooting

### Copilot doesn't use context_search

1. Confirm Agent mode is active (not "Edit" or "Chat" mode)
2. Check `.github/copilot-instructions.md` exists and contains the CCE block
3. Reload VS Code window after setup

### "cce: command not found"

VS Code inherits PATH from how it was launched. If you installed `cce` with `uv tool install`:

- **macOS/Linux:** Add `~/.local/bin` to your shell profile, then launch VS Code from a new terminal with `code .`
- **Windows:** The installer usually adds to PATH automatically. If not, add `%USERPROFILE%\.local\bin` to your system PATH, then restart VS Code

### Windows: UnicodeDecodeError during init

Upgrade to CCE v0.4.24+ which fixes Windows encoding issues. Run:

```bash
uv tool install "code-context-engine[local]" --upgrade
```

### MCP server starts but Copilot can't connect

Check that no firewall or antivirus is blocking localhost connections. CCE's MCP server communicates via stdio (not HTTP) by default, so this is rare.
