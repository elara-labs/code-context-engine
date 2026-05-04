# Web Dashboard for Index Inspection

**Date:** 2026-04-18
**Status:** Approved

---

## Overview

A local web dashboard launched via `cce dashboard` that lets a developer inspect and control their CCE index. It shows what is indexed, token savings, session history, and exposes the common maintenance actions (reindex, clear, delete, change compression).

The dashboard is a single-developer local tool. No auth, no multi-user concerns.

---

## Architecture

**Stack:** FastAPI + Uvicorn + single self-contained HTML page (embedded in the Python package).

The HTML, CSS, and JS are stored as a Python string constant inside `src/context_engine/dashboard/server.py` — no separate static files to ship. FastAPI serves the page at `/` and provides a small REST API at `/api/*`. The `cce dashboard` CLI command starts Uvicorn on a free port and opens the browser automatically via `webbrowser.open()`.

```
cce dashboard
  └── starts FastAPI (Uvicorn, random free port)
        ├── GET  /                        → serves embedded HTML page
        ├── GET  /api/status              → index stats + health summary
        ├── GET  /api/files               → all indexed files with status
        ├── GET  /api/sessions            → session history + decisions
        ├── GET  /api/savings             → token savings data
        ├── POST /api/reindex             → trigger full or changed-only reindex
        ├── POST /api/reindex/{file_path} → reindex a single file
        ├── DELETE /api/files/{file_id}   → remove a file from the index
        ├── POST /api/clear               → wipe the entire index
        ├── POST /api/compression         → set output compression level
        └── GET  /api/export              → download index data as JSON
```

The browser page polls `/api/status` every 5 seconds to keep stats fresh during a reindex. All other data is fetched on tab switch.

---

## UI

**Layout:** Tabbed navigation (top tabs), dark theme matching CCE's existing `#0d1117` / `#161b22` / `#58a6ff` palette.

### Tab: Overview

Four stat cards at the top: chunks indexed, files indexed, queries run, tokens saved (%).

Below, two side-by-side panels:

- **Index Health** — counts of up-to-date / stale / not-indexed files with "Reindex changed" and "Full reindex" buttons.
- **Recent Sessions** — last 5 sessions with name, decision count, code area count, and relative timestamp.

### Tab: Files

Filter input + "Export JSON" and "Clear index" buttons in a toolbar.

A table of all indexed files showing: file path, chunk count, status badge (ok / stale). Each row has a reindex button (↺) and delete button (✕). Clicking ↺ on a stale file triggers `/api/reindex/{file_path}` and the row refreshes in place.

### Tab: Sessions

List of past sessions, each showing name, timestamp, decision count, code area count, and active/closed badge. Expanding a session shows the recorded decisions inline.

### Tab: Savings

Left panel: token usage bar chart (with CCE vs without CCE) and total tokens saved with percentage. Right panel: output compression level selector (off / lite / standard / max) — clicking a level calls `/api/compression` immediately.

---

## New Files

```
src/context_engine/dashboard/
    __init__.py
    server.py       — FastAPI app, route handlers, embedded HTML constant
    _page.py        — HTML/CSS/JS source (imported as a string by server.py)
```

The CLI gains one new command in `cli.py`:

```python
@main.command()
@click.option("--port", default=0, help="Port (0 = random free port)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.pass_context
def dashboard(ctx, port, no_browser): ...
```

---

## Data Sources

All data is read directly from the existing storage layer — no new storage format:

| API endpoint | Source |
|---|---|
| `/api/status` | `stats.json` + manifest file count + LanceDB chunk count |
| `/api/files` | `manifest.json` (known files) + filesystem mtime comparison for staleness |
| `/api/sessions` | `sessions/*.json` |
| `/api/savings` | `stats.json` |
| `/api/export` | All of the above combined |

Reindex calls `pipeline.run_indexing()` directly (same function used by `cce index`). Clear wipes LanceDB table + manifest. Delete calls `backend.delete_by_file()` + removes the manifest entry.

---

## Dependencies

Adds to `pyproject.toml` optional or core deps:

- `fastapi>=0.110`
- `uvicorn>=0.29`

Both are lightweight. If the user installs via `pip install code-context-engine`, they are included. No build step, no Node.js, no separate frontend tooling.

---

## Error Handling

- Port conflict: try up to 10 random ports before giving up with a clear message.
- Reindex errors: returned as JSON `{"error": "..."}` and displayed inline in the UI.
- No index yet: API returns empty arrays with an `initialized: false` flag; the page shows a "Run `cce init` first" banner.

---

## Testing

- Unit tests for each API route handler (mock the storage layer).
- Test that `cce dashboard --no-browser` starts without error and returns a valid port.
- Test that the HTML page is served at `/`.
- No browser automation tests — the HTML is simple enough that unit coverage of the API is sufficient.

---

## Out of Scope

- Authentication (local tool only).
- WebSocket live push (5-second polling is sufficient).
- Multi-project view (dashboard is always scoped to `Path.cwd()`).
- Dark/light theme toggle.
