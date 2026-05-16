---
title: VS Code / Copilot
description: Setting up CCE with VS Code and GitHub Copilot.
---

CCE integrates with GitHub Copilot's chat agent in VS Code through MCP configuration and a Copilot instructions file.

## Quick setup

```bash
cce init --agent copilot
```

## Files created

### `.vscode/mcp.json`

Registers the CCE MCP server for Copilot's agent mode.

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

### `.github/copilot-instructions.md`

Contains instructions for Copilot to use `context_search` for code questions. The CCE block is wrapped in markers:

```markdown
<!-- CCE:BEGIN -->
...instructions...
<!-- CCE:END -->
```

Your own Copilot instructions above or below the markers are preserved during upgrades.

## Usage

Once configured, Copilot's chat agent will have access to the `context_search` tool. Ask questions about your codebase in Copilot Chat and it will use CCE's compressed retrieval instead of sending full files.

## Restarting after setup

After running `cce init`, reload the VS Code window (Cmd+Shift+P, then "Developer: Reload Window") to pick up the MCP server.
