# CLI Reference

Complete reference for every `cce` command with expected output.

All commands use colorful, structured output with line-by-line animation on TTY:
- `●` green bullet = healthy/active
- `○` yellow bullet = warning/inactive
- `✓` green check = success
- `·` yellow dot = skipped/warning
- `✗` red cross = error/removed
- `──` cyan section headers with styled dividers
- Dim gray text for secondary information and tips

---

## cce

Running `cce` with no subcommand shows a welcome banner with project status at a glance:

```
╭─────────────────────────── Code Context Engine v0.3.1 ────────────────────────────╮
│                                                                                     │
│                                     ⬡  C C E  ⬡                                     │
│                                                                                     │
│                                     my-project                                      │
│               standard profile  ·  /Users/you/projects/my-project                   │
│                                                                                     │
├────────────────────────────────────────��───┬──────────────────────────────────────��─┤
│ Status                                     │ Getting started                        │
│  ● Indexed      1,247 chunks               │  cce status    full diagnostics        │
│  ● Embedding    BAAI/bge-small-en-v1.5     │  cce savings   token savings           │
│  ○ Ollama       not running                │  cce list      all commands            │
│  ● Compress     truncation                 │ ────────────────────────────────────── │
│  ● Savings      68% over 42 queries        │  Embed:  BAAI/bge-small-en-v1.5        │
│                                            │  Ollama: not running                   │
╰────────────────────────────────────────────┴────────────────────────────────────────╯
```

The icon next to "C C E" changes randomly each time. The left column shows engine status. The right column shows tips and engine details.

---

## cce list

Shows every available command grouped by category:

```
  ── Setup ─────────────────────────────────────────
    cce init                            Index project, install git hooks, write .mcp.json
    cce index                           Re-index changed files
    cce index --full                    Force full re-index of every file
    cce index --path <file>             Index one file or directory

  ── Status & Savings ──────────────────────────────
    cce status                          Index health, config, embedding model, Ollama status
    cce status --json                   Machine-readable output
    cce savings                         Token savings report with visual grid
    cce savings --all                   Savings across every indexed project
    cce savings --json                  Machine-readable savings output

  ── Index Management ──────────────────────────────
    cce clear                           Clear all index data (asks for confirmation)
    cce clear --yes                     Skip confirmation
    cce prune                           Remove data for deleted projects
    cce prune --dry-run                 Preview without deleting

  ── Services ──────────────────────────────────────
    cce services                        Show status of Ollama, dashboard, MCP
    cce services start                  Start Ollama + dashboard
    cce services start ollama           Start only Ollama
    cce services start dashboard        Start dashboard on default port
    cce services stop                   Stop everything CCE started

  ── Dashboard ─────────────────────────────────────
    cce dashboard                       Open web dashboard in browser
    cce dashboard --port 8080           Custom port
    cce dashboard --no-browser          Server only, no browser open

  ── Project Commands ──────────────────────────────
    cce commands list                   Show all rules, preferences, and hooks
    cce commands add-rule '<rule>'      Add a project rule
    cce commands remove-rule '<rule>'   Remove a rule
    cce commands set-pref <key> <val>   Set a preference
    cce commands remove-pref <key>      Remove a preference
    cce commands add <hook> '<cmd>'     Add to before_push / before_commit / on_start
    cce commands remove <hook> '<cmd>'  Remove from a hook
    cce commands add-custom <n> '<c>'   Add a named custom command

  ── Search ────────────────────────────────────────
    cce search '<query>'                Run a test query and update savings stats
    cce search '<query>' --top-k 10     Return more results

  ── Shortcuts ─────────────────────────────────────
    cce start                           Start all services (Ollama + dashboard)
    cce stop                            Stop all services
    cce start ollama                    Start only Ollama
    cce stop dashboard                  Stop only dashboard

  ── Lifecycle ─────────────────────────────────────
    cce init                            Install CCE in project
    cce upgrade                         Upgrade CCE and refresh project config
    cce upgrade --check                 Check install method without upgrading
    cce uninstall                       Remove CCE from project (hooks, MCP, CLAUDE.md)
    cce serve                           Start MCP server (used by Claude Code)

  ── Other ─────────────────────────────────────────
    cce list                            This command
    cce --version                       Show version
    cce --help                          Show help
```

---

## cce init

One-time setup for a project. Checks dependencies, indexes all code, installs git hooks, and connects Claude Code via MCP.

```bash
cd /path/to/your/project
cce init
```

**Expected output:**

```
  Code Context Engine  ·  my-project
  ────────────────────────────────────────────

  Checking embedding model... downloading if needed (60 MB, first time only)... ready.
  Ollama not running — using truncation compression.
  Tip: ollama pull phi3:mini for LLM summarization

  ✓ Git hooks installed  (3 hooks, auto-updates on commit)
  ✓ MCP server registered in .mcp.json
  ✓ CLAUDE.md created with CCE instructions
  ✓ .gitignore updated with CCE entries

  Indexing project...
    ██████████████████████████████  134/134 files  100%

  ✓ Indexed 1,247 chunks from 89 files

  Done!  Restart Claude Code to activate CCE.
```

**With Ollama running:**

```
  Ollama detected — LLM summarization enabled.
```

**On a non-git project:**

```
  · Not a git repository — git hook skipped
    Run `cce index` manually after making changes.
```

**What it does:**

- Warms the embedding model (downloads on first run)
- Checks Ollama status and reports compression mode
- Builds vector, FTS, and graph indexes
- Installs `post-commit` and `pre-push` git hooks
- Writes `.mcp.json` pointing Claude Code at the MCP server
- Creates or updates `CLAUDE.md` with CCE instructions
- Adds per-machine files to `.gitignore`

---

## cce index

Re-index files that have changed since the last run.

```bash
cce index
```

**Expected output:**

```
  Indexing...
    ████████░░░░░░░░░░░░░░░░░░░░░░  14/52 files  26%

  ✓ Indexed 38 chunks from 3 files
```

On unchanged repos (nothing to update):

```
  Indexing...

  ✓ Indexed 0 chunks from 0 files
```

**Variants:**

```bash
# Force a full re-index of every file (ignores change detection)
cce index --full

# Index only a specific file or directory
cce index --path src/payments/processor.py
cce index --path src/payments/

# Verbose — shows each file being processed
cce index -v
```

The git hook installed by `cce init` calls `cce index` automatically after every commit.

---

## cce status

Show index health and a token savings summary for the current project.

```bash
cce status
```

**Expected output:**

```
  ── Status · my-project ──────────────────────────

    ● Storage       /Users/you/.cce/projects
    ● Compression   standard
    ● Profile       standard
    ● Embedding     BAAI/bge-small-en-v1.5
    ○ Ollama        not running
    ● Compress      truncation (signatures + docstrings)

  ── Token Savings ─────────────────────────────────

    Queries:        42
    Full codebase:  58,000 tokens
    Served:         18,400 tokens
    ✓ Saved: 39,600 tokens (68%)
```

**When not yet indexed:**

```
  · Project not indexed yet — run: cce init
```

**Options:**

```bash
# Single-line output (used by the SessionStart hook)
cce status --oneline

# JSON output
cce status --json

# Verbose — lists all indexed projects
cce status -v
```

**Oneline output example** (shown at the top of each Claude Code session):

```
CCE v0.3.1 · my-project · 1247 chunks indexed · 68% saved over 42 queries
USE context_search MCP tool for all code questions. Do NOT use Read/Grep to explore code.
```

---

## cce savings

Visual token savings report.

```bash
cce savings
```

**Expected output:**

```
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶   my-project · 42 queries
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶   14.2k served · 26.0k chunks raw · 48.0k full-file baseline
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶   Token savings (split)
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶   ⛁ Retrieval:    46%  vs reading full files
     ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶   ⛶ Compression:  45%  chunk → summary
```

The filled grid cells (`⛁`) represent tokens used. Empty cells (`⛶`) represent tokens saved. CCE reports retrieval savings (targeted chunks vs full files) and compression savings (summarized chunks vs raw chunks) separately.

**Variants:**

```bash
# Savings across all indexed projects
cce savings --all

# Machine-readable JSON
cce savings --json
```

**JSON output:**

```json
{
  "project": "my-project",
  "queries": 42,
  "served_tokens": 14200,
  "raw_tokens": 26000,
  "full_file_tokens": 48000,
  "retrieval_savings_pct": 46,
  "compression_savings_pct": 45
}
```

---

## cce commands

Manage project-specific rules, preferences, and commands. Stored in `.cce/commands.yaml`.

```bash
# Add rules Claude must follow
cce commands add-rule 'Never generate down() in migrations'
cce commands add-rule 'Use UUID for primary keys'

# Set project preferences
cce commands set-pref database PostgreSQL
cce commands set-pref auth Sanctum

# Add commands to lifecycle hooks
cce commands add before_push 'composer test'
cce commands add before_commit 'php-cs-fixer fix --dry-run'

# Add named custom commands
cce commands add-custom deploy 'kubectl apply -f k8s/'

# List all (merged with workspace if present)
cce commands list

# Remove
cce commands remove-rule 'Never generate down() in migrations'
cce commands remove-pref database
cce commands remove before_push 'composer test'
cce commands remove custom deploy
```

**Workspace support:** Place a `.cce/commands.yaml` in a parent directory to define shared rules across multiple projects. Project configs extend the workspace. See [Project Commands](Project-Commands) for full details.

---

## cce clear

Clear all index data and reset stats for the current project.

```bash
cce clear
```

CCE asks for confirmation before deleting:

```
  ── Clear Index ───────────────────────────────────

    Delete all index data for my-project? [y/N]:
```

**After clearing:**

```
    ✓ Cleared index data for my-project
    Run cce index to rebuild
```

```bash
# Skip the confirmation prompt
cce clear --yes
```

After clearing, run `cce index --full` to rebuild.

---

## cce prune

Remove index data for projects whose directories no longer exist on disk.

```bash
cce prune
```

**Expected output:**

```
  ── Prune ─────────────────────────────────────────

    ✗ removed      old-project  /Users/raj/projects/old-project
    ✓ kept         my-project   /Users/raj/projects/my-project
```

```bash
# Preview without deleting
cce prune --dry-run
```

**Dry-run output:**

```
  ── Prune (dry run) ───────────────────────────────

    · would remove  old-project  /Users/raj/projects/old-project
    ✓ kept          my-project   /Users/raj/projects/my-project
```

---

## cce dashboard

Open the web dashboard in your browser.

```bash
cce dashboard
```

**Output:**

```
  CCE Dashboard  at  http://localhost:52341
  Press Ctrl+C to stop.
```

The dashboard provides four views:

- **Overview** — chunks indexed, files indexed, queries run, tokens saved, live charts
- **Files** — full file list with staleness detection (`ok`, `stale`, `missing`)
- **Sessions** — architectural decisions and code areas from past Claude sessions
- **Savings** — token usage breakdown with compression controls

**Variants:**

```bash
cce dashboard --port 8080
cce dashboard --no-browser
```

---

## cce services

Manage Ollama and the Dashboard as background processes.

### Check status

```bash
cce services
```

**Expected output:**

```
  ── Services ──────────────────────────────────────

    ● ollama       running   localhost:11434 (external)
    ○ dashboard    stopped
    ● mcp          running   managed by Claude Code
```

`ollama` and `dashboard` can be started and stopped by CCE. `mcp` is managed by Claude Code and shown read-only.

### Start

```bash
cce services start              # Ollama + Dashboard
cce services start ollama
cce services start dashboard
cce services start dashboard --port 9000
```

**Output:**

```
  ✓ Ollama started (PID 12345)
  ✓ Dashboard started at http://localhost:8080 (PID 12346)
```

If already running:

```
  · Ollama is already running.
```

### Stop

```bash
cce services stop               # stop everything CCE started
cce services stop dashboard
cce services stop ollama
```

CCE can only stop processes it started. Externally started processes show as `running (external)` and are not stopped.

---

## cce commands

Manage per-project rules, preferences, and shell hooks that CCE surfaces to Claude.

### Add a rule

```bash
cce commands add-rule 'NEVER generate down() in migrations — forward-only'
```

```
  ✓ Rule added: NEVER generate down() in migrations — forward-only
```

### Set a preference

```bash
cce commands set-pref database PostgreSQL
cce commands set-pref auth Sanctum
```

```
  ✓ Preference set: database = PostgreSQL
```

### Add a hook command

```bash
cce commands add before_push 'composer test'
cce commands add before_commit 'php-cs-fixer fix --dry-run'
cce commands add on_start 'echo "Deploy freeze until Friday"'
```

```
  ✓ Added to before_push: composer test
```

### Add a custom command

```bash
cce commands add-custom deploy 'kubectl apply -f k8s/'
```

```
  ✓ Added custom command 'deploy': kubectl apply -f k8s/
```

### List

```bash
cce commands list
```

```yaml
rules:
  - NEVER generate down() in migrations — forward-only
  - Use UUID for primary keys
preferences:
  database: PostgreSQL
  auth: Sanctum
before_push:
  - composer test
  - phpstan analyse
custom:
  deploy: kubectl apply -f k8s/
```

### Remove

```bash
cce commands remove before_push 'composer test'
cce commands remove-rule 'Use UUID for primary keys'
cce commands remove-pref database
```

---

## cce uninstall

Remove CCE from the current project. Reverses everything `cce init` did: git hooks, `.mcp.json` entry, the CCE block in `CLAUDE.md`, and the local `.cce/` directory.

```bash
cce uninstall
```

**Expected output:**

```
  ── Uninstall · my-project ────────────────────────

    ✗ Removed 3 git hooks
    ✗ Removed context-engine from .mcp.json
    ✗ Removed CCE block from CLAUDE.md
    ✗ Removed .cce/ directory

    Index data in ~/.cce is preserved.
    Run cce clear to remove index data too.
```

**What it removes:**

- Git hooks (`post-commit`, `post-checkout`, `post-merge`) that contain CCE markers
- The `context-engine` entry from `.mcp.json`
- The CCE instruction block from `CLAUDE.md` (detects versioned, legacy heading, and `CCE:BEGIN`/`CCE:END` marker formats)
- The `.cce/` directory (local project cache)

**What it keeps:**

- Index data in `~/.cce/projects/<name>/` so you can re-initialize without a full re-index
- Run `cce clear` afterwards to remove the index data too

---

## cce upgrade

Upgrade code-context-engine to the latest version. Automatically detects whether you installed via uv, pipx, or pip and runs the correct upgrade command. After upgrading, refreshes git hooks, MCP config, CLAUDE.md, and the SessionStart hook in the current project.

```bash
cce upgrade
```

**Expected output:**

```
  ── Upgrade ───────────────────────────────────────
    Current version: 0.3.1
    Install method:  uv
    Running:         uv tool upgrade code-context-engine

  ✓ Upgraded 0.3.1 → 0.3.2

  Refreshing project config...
  ✓ MCP server config is current
  ✓ CLAUDE.md upgraded to current CCE instructions
  ✓ Git hooks refreshed

  Done!  Restart Claude Code to pick up changes.
```

**When already on the latest version:**

```
  ✓ Already on latest version (0.3.1)
```

**Check without upgrading:**

```bash
cce upgrade --check
```

```
  ── Upgrade ───────────────────────────────────────
    Current version: 0.3.1
    Install method:  uv

    To upgrade: uv tool upgrade code-context-engine
```

This is useful to see which package manager CCE was installed with before running the upgrade.

---

## cce search

Run a test query against the index and display results. Also updates the savings stats, which fixes the "0 queries" issue when no MCP queries have been made yet.

```bash
cce search 'how does authentication work'
```

**Expected output:**

```
  ── Search · how does authentication work ────────

    1. auth/session.py:34-68
       def validate_token(token: str) -> User | None:
    2. auth/middleware.py:12-45
       class AuthMiddleware:
    3. utils/jwt.py:8-28
       def decode_jwt(token: str) -> dict | None:
    4. config/redis.py:1-15
       REDIS_SESSION_TTL = 86400
    5. CLAUDE.md:1-74
       <!-- cce-block-version: 2 -->

    ✓ 5 results  1201 tokens served vs 3800 full file tokens
```

**Variants:**

```bash
# Return more results
cce search 'payment processing' --top-k 10
```

This is useful for verifying the index is working, testing query quality, and populating the savings dashboard before opening a Claude Code session.

---

## cce start / cce stop

Shortcuts for `cce services start` and `cce services stop`.

```bash
cce start                # Start all services (Ollama + dashboard)
cce stop                 # Stop all services
cce start ollama         # Start only Ollama
cce stop dashboard       # Stop only dashboard
```

These behave identically to the `cce services start` and `cce services stop` commands.

---

## cce serve

Start the MCP server. Claude Code calls this automatically via `.mcp.json` — you do not need to run this manually.

```bash
cce serve

# Point at a specific project directory (useful for debugging)
cce serve --project-dir /path/to/your/project
```
