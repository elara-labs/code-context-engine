# CCE Memory: claude-mem Feature Parity

**Status:** approved by product, in implementation
**Branch:** `feature/memory-claude-mem-parity`
**Date:** 2026-04-28

## Goal

Bring CCE's cross-session memory to feature parity with `claude-mem`'s
production-grade capture pipeline, while keeping CCE's per-project,
local-first, no-required-external-services posture.

Today CCE relies on a single `SessionStart` hook plus manual MCP
`record_decision` / `record_code_area` calls. Sessions are written to
per-project JSON files. Effectively, memory only grows when an agent
explicitly chooses to record — which means most sessions leave no trace.

## Approved decisions

Six design decisions were locked during brainstorming:

1. **Capture model.** Auto-capture from hooks **plus** explicit MCP tools.
   A `source` column (`manual` | `auto` | `migrated`) distinguishes them so
   recall can rank manual entries higher.
2. **Compression timing.** A background worker inside `cce serve` (the
   long-running per-project MCP server process) drains a
   `pending_compressions` queue on a 5–10 s tick. Hooks themselves stay
   thin appenders.
3. **Summary granularity.** Per-turn summaries plus a per-session
   rollup. Maps directly onto the three retrieval layers.
4. **Recall MCP surface.** Extend the existing `session_recall(topic)`
   to return compact-index hits (backward compatible with this project's
   `CLAUDE.md` documentation), and add two new tools
   `session_timeline(session_id)` and `session_event(event_id)` for
   layer-2 and layer-3 drill-down.
5. **Migration of existing JSON sessions.** A one-shot
   `cce sessions migrate` CLI command. Idempotent. User-invoked, not
   auto-triggered on startup.
6. **Dashboard scope (v1).** Three panels: sessions list, session
   timeline (drill-into one session), decisions search (FTS5, faceted by
   `source`). Hot-files / queue-health views deferred to a follow-up.

## Compressor tiering (locked separately)

Per the user's "no Ollama on this machine, keep things small"
preference:

```
Tier 1 (default): extractive summarisation using BAAI/bge-small-en-v1.5.
                  The model is already loaded in cce serve for the index;
                  reusing it adds zero new dependencies, zero extra RAM.
                  Algorithm: sentence-split the turn, embed each sentence
                  with bge-small, pick the top-K closest to the turn's
                  centroid, concatenate.
Tier 2 (optional): Ollama if running. Existing compressor.py path. Opt-in.
Tier 3 (fallback): truncation. Used if the embedder isn't ready yet
                   (e.g. very early SessionStart before the model loads).
```

Extractive output is always real source text — no hallucination. That
matters for a memory system whose summaries survive across sessions.

## Architecture

```
                                 ┌─────────────────────────────────────┐
                                 │  Claude Code (per-project)          │
   5 hooks ───────────────►      │  SessionStart                       │
   (settings.json)               │  UserPromptSubmit                   │
                                 │  PostToolUse                        │   thin shell:
                                 │  Stop                               │   ~50ms each, ─────► HTTP POST
                                 │  SessionEnd                         │   no LLM         │   to local
                                 └─────────────────────────────────────┘                  │   serve_http
                                                                                           ▼
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │  cce serve  (already running per-project — adds 2 things)                     │
   │                                                                                │
   │  ┌─ HTTP /hooks/<name>  ─►  append raw event to memory.db (1-2 ms)            │
   │  │                                                                             │
   │  └─ async tick loop (5–10s)  ─►  drain pending_compressions queue              │
   │                                  ┌──────────────────────────────┐              │
   │                                  │ extractive (bge-small)       │              │
   │                                  │  → Ollama (opt-in)           │              │
   │                                  │  → truncation fallback       │              │
   │                                  └──────────────────────────────┘              │
   │                                                                                │
   │  MCP tools: session_recall (extended), session_timeline (new),                │
   │             session_event (new), record_decision, record_code_area            │
   └───────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
              ~/.cce/projects/<name>/memory.db   (per-project SQLite, FTS5)
                                  │
                                  ▼
                          Dashboard /memory pages (3 panels)
```

### Invariants

- Hooks are thin shells; zero latency from LLM work.
- Compression is decoupled from hooks. If it crashes, capture continues.
- Per-project SQLite means two projects cannot collide. Backup = copy
  one file.
- The existing `compressor.py` abstraction is reused.

## Hook contract

Each Claude Code hook becomes a thin shell that POSTs JSON to
`http://127.0.0.1:<port>/hooks/<name>`. The port is written by
`cce serve` to `~/.cce/projects/<name>/serve.port` on startup.

Hook script (`~/.cce/hooks/cce_hook.sh`, ~15 lines):

```sh
#!/bin/sh
# Claude Code hooks pipe JSON on stdin. Forward to cce serve.
# Failure is silent — memory is best-effort, never breaks the user's flow.
PORT=$(cat ~/.cce/projects/$(basename "$PWD")/serve.port 2>/dev/null) || exit 0
curl -sf -m 1 -X POST -H "Content-Type: application/json" \
    --data-binary @- "http://127.0.0.1:${PORT}/hooks/$1" >/dev/null 2>&1 \
    || true
```

| Hook | Payload | Action in `cce serve` |
|---|---|---|
| `SessionStart` | `{session_id, project, started_at}` | Insert row in `sessions`. Show CCE status (existing behaviour preserved). |
| `UserPromptSubmit` | `{session_id, prompt_number, prompt_text, timestamp}` | Insert into `prompts`. Enqueue compression for the *previous* turn (if any). |
| `PostToolUse` | `{session_id, prompt_number, tool_name, tool_input_json, tool_output_json, timestamp}` | Insert into `tool_events` + `tool_event_payloads`. |
| `Stop` | `{session_id, prompt_number, ended_at}` | Mark turn complete. Enqueue compression for the just-ended turn. |
| `SessionEnd` | `{session_id, ended_at, exit_reason}` | Mark session complete. Enqueue session-rollup compression. |

### Failure modes

- `cce serve` not running: curl fails fast (-m 1), hook returns OK, no
  capture for that event. `cce status` surfaces "memory capture: offline".
- Port file missing: hook is a no-op.
- DB write fails inside `cce serve`: log to stderr, increment a
  `hook_errors` counter exposed in dashboard.
- Tool payloads can be huge: stored uncompressed at hook time;
  compression worker later replaces the row's raw JSON with a summary
  and moves originals to a separate retention-bound table.

## SQLite schema

Eight tables in `~/.cce/projects/<name>/memory.db`. Timestamps stored as
both ISO text and epoch int (text for humans, int for sorting). FTS5
virtual tables shown last.

```sql
-- A session = one Claude Code invocation in this project.
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  started_at_epoch INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  ended_at_epoch INTEGER,
  ended_at TEXT,
  exit_reason TEXT,
  prompt_count INTEGER DEFAULT 0,
  status TEXT CHECK(status IN ('active','completed','failed')) NOT NULL DEFAULT 'active',
  rollup_summary TEXT,
  rollup_summary_at_epoch INTEGER
);
CREATE INDEX idx_sessions_started ON sessions(started_at_epoch DESC);

-- One row per UserPromptSubmit.
CREATE TABLE prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  prompt_number INTEGER NOT NULL,
  prompt_text TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, prompt_number)
);
CREATE INDEX idx_prompts_session ON prompts(session_id, prompt_number);

-- One row per PostToolUse.
CREATE TABLE tool_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  prompt_number INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  payload_id INTEGER,
  summary TEXT,
  created_at_epoch INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_events_session_turn ON tool_events(session_id, prompt_number);

-- Sidecar table holding raw tool input/output. Read on layer-3 drill-down.
CREATE TABLE tool_event_payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_input TEXT NOT NULL,
  raw_output TEXT,
  size_bytes INTEGER NOT NULL
);

-- One row per turn after compression. Layer-2 of progressive disclosure.
CREATE TABLE turn_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  prompt_number INTEGER NOT NULL,
  summary TEXT NOT NULL,
  tier TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  UNIQUE(session_id, prompt_number)
);

-- Manual decisions (record_decision MCP tool).
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('manual','migrated','auto')) DEFAULT 'manual',
  created_at_epoch INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_decisions_created ON decisions(created_at_epoch DESC);
CREATE INDEX idx_decisions_source ON decisions(source);

-- Manual code-area annotations.
CREATE TABLE code_areas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  file_path TEXT NOT NULL,
  description TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('manual','migrated','auto')) DEFAULT 'manual',
  created_at_epoch INTEGER NOT NULL
);
CREATE INDEX idx_code_areas_file ON code_areas(file_path);

-- Compression queue.
CREATE TABLE pending_compressions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN ('turn','session_rollup')),
  session_id TEXT NOT NULL,
  prompt_number INTEGER,
  enqueued_at_epoch INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  UNIQUE(kind, session_id, prompt_number)
);

-- FTS5 over prompts, decisions, summaries.
CREATE VIRTUAL TABLE prompts_fts USING fts5(
  prompt_text, content='prompts', content_rowid='id'
);
CREATE VIRTUAL TABLE decisions_fts USING fts5(
  decision, reason, content='decisions', content_rowid='id'
);
CREATE VIRTUAL TABLE turn_summaries_fts USING fts5(
  summary, content='turn_summaries', content_rowid='id'
);

-- Schema version for forward-compatible migrations.
CREATE TABLE schema_versions (
  version INTEGER PRIMARY KEY,
  applied_at_epoch INTEGER NOT NULL
);
INSERT INTO schema_versions (version, applied_at_epoch) VALUES (1, strftime('%s','now'));
```

### Schema notes

- `tool_events.payload_id` is nullable so the compression worker can null
  it out (and delete the payload row) for events older than the
  retention window — keeping only the `summary`. Default retention: 30
  days for raw payloads. Tunable.
- No `touched_files` table. The view is computed on demand from
  `tool_events.tool_input_json` for tools whose input contains a
  `file_path`. Cheaper than maintaining a counter table.
- Three FTS tables, not one combined. Lets `session_recall` weight
  prompts vs decisions vs summaries differently (decisions outrank
  prompts outrank summaries by default).
- `source='migrated'` on decisions/code_areas is what
  `cce sessions migrate` writes when it imports JSON.

## Background compression worker

Runs inside `cce serve`'s asyncio loop. Pseudocode:

```python
async def compression_loop():
    while not shutting_down:
        try:
            row = await db.fetch_oldest_pending()
            if row is None:
                await asyncio.sleep(5)
                continue
            try:
                if row.kind == "turn":
                    await compress_turn(row.session_id, row.prompt_number)
                else:
                    await compress_session_rollup(row.session_id)
                await db.delete_pending(row.id)
            except Exception as exc:
                await db.bump_attempts(row.id, str(exc))
        except Exception:
            log.exception("compression loop iteration failed")
            await asyncio.sleep(10)
```

### Extractive algorithm (turn level)

```
Input: all rows in prompts + tool_events for (session_id, prompt_number)
1. Build candidate sentence list:
     - The user prompt (split into sentences)
     - For each tool_event:
         - Tool name + brief input descriptor (e.g. "Edit cli.py")
         - First N sentences of tool_output (capped)
2. Embed each candidate with the bge-small model already loaded.
3. Compute centroid = mean of all candidate embeddings.
4. Pick top-K (default 3) sentences by cosine similarity to centroid.
5. Concatenate in original order, prefixed by "[turn N]".
6. Write to turn_summaries with tier='extractive'.
```

### Session rollup

Concatenate all `turn_summaries` for the session, run the same
extractive algorithm against that text with K=5. Write to
`sessions.rollup_summary`.

If at any point the embedder isn't loaded yet (rare; only on extreme
cold start), fall back to truncation: first 200 chars of each item,
joined.

## MCP tool surface

| Tool | Behaviour |
|---|---|
| `session_recall(topic)` | **Extended.** FTS5 search across `decisions_fts`, `prompts_fts`, `turn_summaries_fts`. Decisions weighted highest. Returns top-N hits with `{layer: 'index'\|'timeline'\|'event', id, snippet, session_id}`. Backward compatible — the tool name + topic argument are unchanged; the return shape is a strict superset. |
| `session_timeline(session_id, limit=20)` | **New.** Returns the session's `turn_summaries` in order, plus session metadata. Layer 2. |
| `session_event(event_id)` | **New.** Returns the raw payload for a single `tool_events` row (input + output JSON) if still within retention. Layer 3. |
| `record_decision(decision, reason)` | Unchanged. Writes to `decisions` with `source='manual'`. |
| `record_code_area(file_path, description)` | Unchanged. Writes to `code_areas` with `source='manual'`. |

## Migration command

`cce sessions migrate` (idempotent):

1. Locate the per-project DB. If absent, create it (`memory/db.py`
   bootstrap).
2. Walk `~/.cce/projects/<name>/sessions/*.json` and the legacy
   `~/.claude-context-engine/projects/<name>/sessions/*.json`.
3. For each JSON file, parse and import:
   - decisions → `decisions` with `source='migrated'`
   - code_areas → `code_areas` with `source='migrated'`
   - touched_files counts → ignored (replaced by computed view)
4. Skip files already imported (track by source filename in a
   `migrated_files` table).
5. After successful import, archive consumed JSON to
   `~/.cce/projects/<name>/sessions/migrated.zip` and remove the source
   files. Idempotent rerun is a no-op.

## Dashboard panels (v1)

Integrated into the existing dashboard at `dashboard/_page.py` and
`dashboard/server.py`.

1. **Sessions list** (`/memory`). Table: started, ended, prompts,
   tool-uses, status, rollup summary. Click a row → session timeline.
2. **Session timeline** (`/memory/sessions/<id>`). Header: session
   metadata + rollup. Body: ordered turn summaries; each turn
   expandable to its tool events. Tool events expandable to raw
   payload (if still within retention).
3. **Decisions search** (`/memory/decisions`). FTS5 search box, results
   list with `source` facet (manual / auto / migrated). Each result
   links back to its session.

Hot-files panel and queue-health panel are deferred — they're
observability nice-to-haves that we'd add once the core lands and we
know what fails.

## Phasing

This work ships as **5 sequential PRs** off
`feature/memory-claude-mem-parity`. Each PR is independently reviewable
and leaves the tree in a working state.

| PR | Scope | Notes |
|---|---|---|
| **1. Foundation** | `memory/db.py` schema + `memory/migrate.py` + `cce sessions migrate` CLI + tests. | No behaviour change to existing capture path. |
| **2. Capture** | 5 hooks + `cce_hook.sh` + HTTP endpoints in `serve_http.py`. Old JSON path retired. | New writes land in `memory.db`. |
| **3. Compress** | Background asyncio worker in `cce serve`. Extractive summariser using bge-small. | Truncation final fallback. |
| **4. Recall** | Extended `session_recall`; new `session_timeline`, `session_event` MCP tools. | Backward compatible at the topic-argument level. |
| **5. Dashboard** | 3 panels (sessions list, timeline, decisions search). | Builds on existing dashboard scaffolding. |

## Testing strategy

- **Unit tests** for the schema bootstrap (PR 1), migration importer
  (PR 1), extractive summariser (PR 3), MCP tool routing (PR 4).
- **Integration test** in PR 2: post a payload to each
  `/hooks/<name>` endpoint, assert the row lands in the correct table.
- **End-to-end** in PR 3: drive a fake session through hooks, wait for
  the compression worker to drain, assert turn_summaries + rollup are
  populated with the expected tiers.
- The existing 301-test suite must stay green at every commit.

## Out of scope (explicit non-goals)

- Cross-project memory views. Per-project DBs by design; inspecting
  multiple projects at once means opening multiple dashboards.
- Encrypted memory-at-rest. Defer until/unless a concrete user need.
- Auto-extracting decisions from transcripts (vs. just summarising
  turns). The compressor only summarises; decisions remain manual MCP
  input. Auto-decision extraction is a follow-up if extractive turn
  summaries prove rich enough to mine.
- Web viewer as a separate process. Integrated into the existing CCE
  dashboard.
- Embedded abstractive LLM (e.g. flan-t5-small). Extractive with
  bge-small is sufficient for v1. Revisit if turn summaries prove too
  shallow.
