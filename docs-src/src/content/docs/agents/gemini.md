---
title: Gemini CLI
description: Setting up CCE with Google's Gemini CLI.
---

CCE integrates with the Gemini CLI through its settings file and an instruction file.

## Quick setup

```bash
cce init              # Auto-detects Gemini CLI if .gemini/ or GEMINI.md exists
cce init --agent all  # Explicitly includes Gemini
```

## Files created

### `.gemini/settings.json`

Registers the CCE MCP server for Gemini CLI.

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

### `GEMINI.md`

Contains instructions for Gemini to prefer `context_search` over reading files directly. The CCE block is wrapped in markers so your own content is preserved.

## Verify it's working

1. After `cce init`, start a new Gemini CLI session in your project directory
2. Ask a code question:

```
What's the main entry point of this project?
```

3. Check the tool output for `context_search` calls
4. Run `cce savings` to see token savings

## Cross-agent memory

If you use both Gemini CLI and Claude Code on the same project, decisions recorded in one session are available to the other via `session_recall`. Memory is stored per-project in `memory.db`, not per-agent.

## Troubleshooting

### Gemini doesn't use context_search

Check that `GEMINI.md` exists and contains the CCE instructions block. Gemini CLI reads this file at session start. If missing, re-run `cce init`.

### "cce: command not found"

Gemini CLI inherits PATH from your shell. Ensure `~/.local/bin` is in your PATH if you installed with `uv tool install`.

### Auto-detection doesn't find Gemini

CCE looks for `.gemini/` directory or `GEMINI.md` in the project root. If neither exists, use `cce init --agent all` to force configuration.
