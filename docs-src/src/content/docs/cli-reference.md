---
title: CLI Reference
description: Complete reference for every cce command.
---

## cce init

One-time setup for a project. Checks dependencies, indexes all code, installs git hooks, and connects AI coding agents via MCP.

```bash
cce init
cce init --agent claude
cce init --agent copilot
cce init --agent codex
cce init --agent all
```

What it does:

- Downloads the embedding model (first run only, ~60 MB).
- Checks Ollama status and reports compression mode.
- Builds vector, FTS, and graph indexes.
- Installs `post-commit`, `post-checkout`, and `post-merge` git hooks.
- Writes MCP config for selected agents.
- Creates or updates agent instruction files.
- Adds per-machine files to `.gitignore`.

## cce index

Re-index files that have changed since the last run.

```bash
cce index              # Incremental (changed files only)
cce index --full       # Force full re-index of every file
cce index --path src/  # Index a specific file or directory
cce index -v           # Verbose output
```

The git hooks installed by `cce init` call `cce index` automatically after every commit.

## cce status

Show index health and token savings summary.

```bash
cce status             # Full status
cce status --oneline   # Single line (used by SessionStart hook)
cce status --json      # Machine-readable output
cce status -v          # Lists all indexed projects
```

## cce savings

Token savings report with cost estimates.

```bash
cce savings            # Current project
cce savings --all      # All indexed projects
cce savings --json     # Machine-readable output
```

## cce search

Run a test query against the index and display results.

```bash
cce search 'how does authentication work'
cce search 'payment processing' --top-k 10
```

Also updates savings stats, useful for populating the dashboard before opening an agent session.

## cce dashboard

Open the web dashboard in your browser.

```bash
cce dashboard
cce dashboard --port 8080
cce dashboard --no-browser
```

The dashboard provides views for: overview, files, sessions, and savings.

## cce services

Manage Ollama and the dashboard as background processes.

```bash
cce services                        # Show status
cce services start                  # Start Ollama + dashboard
cce services start ollama           # Start only Ollama
cce services start dashboard        # Start dashboard
cce services start dashboard --port 9000
cce services stop                   # Stop everything CCE started
cce services stop dashboard         # Stop only dashboard
```

## cce start / cce stop

Shortcuts for `cce services start` and `cce services stop`.

```bash
cce start              # Start all services
cce stop               # Stop all services
cce start ollama       # Start only Ollama
cce stop dashboard     # Stop only dashboard
```

## cce commands

Manage per-project rules, preferences, and shell hooks.

```bash
cce commands list                          # Show all rules and hooks
cce commands add-rule 'Use UUID for PKs'   # Add a rule
cce commands remove-rule 'Use UUID for PKs'
cce commands set-pref database PostgreSQL   # Set a preference
cce commands remove-pref database
cce commands add before_push 'npm test'    # Add hook command
cce commands remove before_push 'npm test'
cce commands add-custom deploy 'kubectl apply -f k8s/'
```

## cce clear

Clear all index data for the current project.

```bash
cce clear              # Asks for confirmation
cce clear --yes        # Skip confirmation
```

After clearing, run `cce index --full` to rebuild.

## cce prune

Remove index data for projects whose directories no longer exist on disk.

```bash
cce prune              # Remove stale project data
cce prune --dry-run    # Preview without deleting
```

## cce upgrade

Upgrade CCE to the latest version. Detects your install method (uv, pipx, or pip) and runs the correct upgrade command. Refreshes project config afterwards.

```bash
cce upgrade            # Upgrade and refresh config
cce upgrade --check    # Show install method without upgrading
```

## cce uninstall

Remove CCE from the current project. Reverses everything `cce init` did.

```bash
cce uninstall
```

Removes: git hooks, MCP config entry, instruction file block, and `.cce/` directory. Index data in `~/.cce` is preserved (use `cce clear` to remove it).

## cce serve

Start the MCP server. Called automatically by agents via `.mcp.json`. You do not need to run this manually.

```bash
cce serve
cce serve --project-dir /path/to/project
```

## cce list

Show every available command grouped by category.

```bash
cce list
```

## Other flags

```bash
cce --version          # Show version
cce --help             # Show help
```
