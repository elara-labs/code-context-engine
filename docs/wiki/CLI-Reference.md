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

  ── Status & Savings ──────────────────────────────
    cce status                          Index health, config, embedding, Ollama
    cce savings                         Token savings report with visual grid
    cce savings --all                   Savings across every indexed project

  ── Services ──────────────────────────────────────
    cce services                        Show status of Ollama, dashboard, MCP
    cce services start                  Start Ollama + dashboard
    cce services stop                   Stop everything CCE started

  ── Project Commands ──────────────────────────────
    cce commands list                   Show all rules, preferences, and hooks
    cce commands add-rule '<rule>'      Add a project rule
    cce commands set-pref <key> <val>   Set a preference

  ── Lifecycle ─────────────────────────────────────
    cce uninstall                       Remove CCE from project (hooks, MCP, CLAUDE.md)
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
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶   my-project · 42 queries
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶   18.4k / 58.0k tokens used (32%)
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶   Token savings
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶   ⛁ With CCE:     18,400 tokens  (32%)
     ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶   ⛶ Tokens saved:  39,600 tokens  (68%)
```

The filled grid cells (`⛁`) represent tokens used. Empty cells (`⛶`) represent tokens saved.

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
  "served_tokens": 18400,
  "full_file_tokens": 58000,
  "tokens_saved": 39600,
  "savings_pct": 68
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

## cce serve

Start the MCP server. Claude Code calls this automatically via `.mcp.json` — you do not need to run this manually.

```bash
cce serve

# Point at a specific project directory (useful for debugging)
cce serve --project-dir /path/to/your/project
```
