---
title: Codex CLI
description: Setting up CCE with OpenAI's Codex CLI.
---

Codex CLI uses a global configuration file rather than per-project MCP config. CCE registers itself in the user-level config with a project-specific section.

## Quick setup

```bash
cce init --agent codex
```

Or let CCE auto-detect (if `~/.codex/` exists or the VS Code OpenAI extension is installed):

```bash
cce init
```

## Files created

### `~/.codex/config.toml`

Codex CLI reads MCP servers from this single user-global file. CCE adds one section per project, keyed by a slug derived from the project's absolute path:

```toml
[mcp_servers.cce-my-project-a3f2b1]
command = "cce"
args = ["serve", "--project-dir", "/path/to/your/project"]
```

Multiple projects coexist in the same file. Each gets a unique section name (`cce-<basename>-<hash>`) so two projects named "api" in different directories won't collide.

### `AGENTS.md`

Contains instructions for Codex to use `context_search` for code exploration. The CCE block is wrapped in markers so your own content is preserved during upgrades.

## Important notes

- Codex CLI does **not** support per-project `.mcp.json` files. The global `~/.codex/config.toml` is the only location for MCP server registration.
- Running `cce uninstall` removes only the section for the current project.
- If you're using Codex via the VS Code extension (not the CLI), CCE detects it by looking for `openai.*` directories under `~/.vscode/extensions/`.

## Verify it's working

1. After `cce init`, start a new Codex session in your project directory
2. Ask a code question:

```
How does error handling work in this project?
```

3. Check that Codex calls `context_search` in the tool output
4. Verify savings:

```bash
cce savings
```

## Cross-agent memory

Decisions recorded during Claude Code sessions (`record_decision`) are stored in the project's `memory.db` and shared across all agents. If you switch between Claude Code and Codex on the same project, `session_recall` returns decisions from both.

## Troubleshooting

### "cce: command not found" in Codex

Codex resolves commands from your shell's PATH. If you installed with `uv tool install`:

- **macOS/Linux:** Ensure `~/.local/bin` is in your PATH (add to `~/.zshrc` or `~/.bashrc`)
- **Windows:** Ensure `%USERPROFILE%\.local\bin` is in your system PATH

### Codex doesn't detect the MCP server

Check that `~/.codex/config.toml` exists and contains the `[mcp_servers.cce-...]` section:

```bash
cat ~/.codex/config.toml | grep cce
```

If missing, re-run `cce init --agent codex`.

### Multiple projects interfering

Each project section in `config.toml` includes `--project-dir` pointing to the correct path. If you renamed or moved a project, run `cce uninstall` in the old location and `cce init --agent codex` in the new one.

### Windows: config.toml path

On Windows, the config file is at `%USERPROFILE%\.codex\config.toml`. CCE handles backslash escaping in TOML automatically, but if you edit the file manually, use forward slashes or double backslashes in paths.
