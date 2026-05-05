"""HTTP handlers backing the 5 Claude Code lifecycle hooks.

Endpoints (all loopback-only, no auth):
    POST /hooks/SessionStart        -> insert sessions row
    POST /hooks/UserPromptSubmit    -> insert prompts row, enqueue prev-turn compress
    POST /hooks/PostToolUse         -> insert tool_event + tool_event_payload
    POST /hooks/Stop                -> mark turn complete, enqueue compress
    POST /hooks/SessionEnd          -> mark session complete, enqueue rollup

Hooks are best-effort: every write is wrapped so a payload-shape error never
500s back to the hook script (which is `set -e` shell). On error we log + return
202 so the user's flow is never blocked. The dashboard surfaces error counts.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from aiohttp import web

log = logging.getLogger(__name__)


def _now_epoch() -> int:
    return int(time.time())


def _now_iso(epoch: int | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch or _now_epoch()))


async def _read_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception as exc:
        log.warning("Hook payload not JSON: %s", exc)
        return {}


def _conn(request: web.Request) -> sqlite3.Connection:
    return request.app["memory_db"]


_RESUME_RECENT_DECISIONS = 5
_RESUME_DECISION_REASON_CHARS = 200


def _build_savings_line(conn: sqlite3.Connection) -> str:
    """One-line savings summary from the savings_log table.

    Returns something like:
      "CCE saved 95% of input tokens across 14 queries (48.0k baseline, 2.4k served)"
    or "" if no savings data exists.
    """
    from context_engine.memory.db import aggregate_savings

    try:
        buckets = aggregate_savings(conn)
    except Exception:
        return ""

    # Use retrieval bucket for the true baseline (full-file tokens) and query
    # count.  For served tokens, prefer chunk_compression (the final pipeline
    # stage) when available, otherwise fall back to retrieval served.  This
    # avoids double-counting that would occur if we summed baselines across
    # all buckets (retrieval baseline feeds into chunk_compression baseline).
    retrieval = buckets.get("retrieval", {"baseline": 0, "served": 0, "calls": 0})
    compression = buckets.get("chunk_compression", {"baseline": 0, "served": 0, "calls": 0})

    total_baseline = retrieval["baseline"]
    total_served = compression["served"] if compression["calls"] > 0 else retrieval["served"]
    total_queries = retrieval["calls"]

    if total_baseline <= 0 or total_queries <= 0:
        return ""

    saved_pct = (1 - total_served / total_baseline) * 100

    def _fmt_k(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    return (
        f"CCE saved {saved_pct:.0f}% of input tokens across {total_queries} queries "
        f"({_fmt_k(total_baseline)} baseline, {_fmt_k(total_served)} served)"
    )


def build_session_resume(conn: sqlite3.Connection, project: str) -> str:
    """Compose a short text block summarising recent state for the model.

    Returned as plain text and printed by the hook shell script to stdout.
    Claude Code injects SessionStart hook stdout into the model's context at
    conversation start — so this is the mechanism that prevents "decisions
    you made last week have to be re-explained today." Empty string for
    a brand-new project so there's no awkward header on the first session.
    """
    parts: list[str] = []

    last_rollup = conn.execute(
        "SELECT id, rollup_summary, ended_at "
        "FROM sessions "
        "WHERE rollup_summary IS NOT NULL AND rollup_summary != '' "
        "ORDER BY started_at_epoch DESC LIMIT 1"
    ).fetchone()

    decisions = list(conn.execute(
        "SELECT decision, reason, source, session_id, created_at "
        "FROM decisions "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (_RESUME_RECENT_DECISIONS,),
    ))

    savings_line = _build_savings_line(conn)

    if not last_rollup and not decisions and not savings_line:
        return ""

    parts.append(f"## CCE memory · resuming {project}")
    # Stored values went through grammar.compress on the write side; expand
    # before display so the resume reads as natural prose.
    from context_engine.memory.grammar import expand as _grammar_expand

    if savings_line:
        parts.append("")
        parts.append(f"**{savings_line}**")

    if last_rollup:
        when = last_rollup["ended_at"] or "in progress"
        parts.append("")
        parts.append(f"**Previous session** ({when}):")
        rollup = _grammar_expand((last_rollup["rollup_summary"] or "").strip())
        for line in rollup.split("\n"):
            line = line.strip()
            if line:
                parts.append(f"  {line}")
    if decisions:
        parts.append("")
        parts.append("**Recent decisions** (most-recent first):")
        for d in decisions:
            decision = _grammar_expand((d["decision"] or "").strip())
            # Truncate before expand so the cap operates on stored bytes,
            # not on post-expand bytes — otherwise two reasons that stored
            # equal length display unequal length depending on how many
            # abbreviations expand.
            stored_reason = (d["reason"] or "").strip()
            if len(stored_reason) > _RESUME_DECISION_REASON_CHARS:
                stored_reason = stored_reason[:_RESUME_DECISION_REASON_CHARS] + "…"
            reason = _grammar_expand(stored_reason)
            tag = ""
            if d["source"] != "manual":
                tag = f" _[{d['source']}]_"
            sid_hint = ""
            if d["session_id"]:
                sid_hint = f' (session: `{d["session_id"]}`)'
            parts.append(f"  - {decision} — {reason}{tag}{sid_hint}")
    parts.append("")
    parts.append(
        "Call `session_recall(\"<topic>\")` to find more, or "
        "`session_timeline(\"<sid>\")` to drill into a session."
    )
    return "\n".join(parts)


async def handle_session_start(request: web.Request) -> web.Response:
    """Insert the new session row and return resume context as plain text.

    The body of the response is captured by the hook shell script and
    printed to stdout, which Claude Code injects into the model's context
    at session start. That's how prior-week decisions surface without a
    tool call.
    """
    data = await _read_json(request)
    session_id = data.get("session_id") or data.get("sessionId")
    if not session_id:
        return web.Response(text="", status=400)
    project = data.get("project") or request.app.get("project_name", "")
    started_epoch = int(data.get("started_at") or _now_epoch())

    conn = _conn(request)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, project, started_at_epoch, started_at, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (session_id, project, started_epoch, _now_iso(started_epoch)),
        )
        conn.commit()
    except Exception:
        log.exception("SessionStart insert failed")
        return web.Response(text="", status=202)

    try:
        resume = build_session_resume(conn, project)
    except Exception:
        log.exception("SessionStart resume build failed")
        resume = ""
    return web.Response(text=resume, content_type="text/plain")


async def handle_user_prompt_submit(request: web.Request) -> web.Response:
    data = await _read_json(request)
    session_id = data.get("session_id")
    prompt_text = data.get("prompt_text") or data.get("prompt") or ""
    prompt_number = data.get("prompt_number")
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)

    conn = _conn(request)
    try:
        if prompt_number is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(prompt_number), 0) + 1 AS next "
                "FROM prompts WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            prompt_number = int(row["next"])

        epoch = _now_epoch()
        conn.execute(
            "INSERT OR IGNORE INTO prompts "
            "(session_id, prompt_number, prompt_text, created_at_epoch, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, int(prompt_number), str(prompt_text), epoch, _now_iso(epoch)),
        )
        conn.execute(
            "UPDATE sessions SET prompt_count = prompt_count + 1 WHERE id = ?",
            (session_id,),
        )
        # Enqueue previous turn for compression. The session may have N-1
        # prompts now; compress turn N-1.
        if int(prompt_number) > 1:
            _enqueue_compression(
                conn, kind="turn",
                session_id=session_id,
                prompt_number=int(prompt_number) - 1,
            )
        conn.commit()
    except Exception:
        log.exception("UserPromptSubmit insert failed")
        return web.json_response({"ok": False}, status=202)
    return web.json_response({"ok": True, "prompt_number": prompt_number})


async def handle_post_tool_use(request: web.Request) -> web.Response:
    data = await _read_json(request)
    session_id = data.get("session_id")
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input") or data.get("tool_input_json") or {}
    tool_output = data.get("tool_output") or data.get("tool_output_json") or ""
    prompt_number = data.get("prompt_number")

    raw_input = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
    raw_output = tool_output if isinstance(tool_output, str) else json.dumps(tool_output)
    size = len(raw_input) + len(raw_output)

    conn = _conn(request)
    try:
        if prompt_number is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(prompt_number), 0) AS cur FROM prompts "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            prompt_number = int(row["cur"]) or 0

        cur = conn.execute(
            "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
            "VALUES (?, ?, ?)",
            (raw_input, raw_output, size),
        )
        payload_id = cur.lastrowid
        epoch = _now_epoch()
        conn.execute(
            "INSERT INTO tool_events "
            "(session_id, prompt_number, tool_name, payload_id, created_at_epoch, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, int(prompt_number), tool_name, payload_id, epoch, _now_iso(epoch)),
        )
        conn.commit()
    except Exception:
        log.exception("PostToolUse insert failed")
        return web.json_response({"ok": False}, status=202)
    return web.json_response({"ok": True})


async def handle_stop(request: web.Request) -> web.Response:
    data = await _read_json(request)
    session_id = data.get("session_id")
    prompt_number = data.get("prompt_number")
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)

    conn = _conn(request)
    try:
        if prompt_number is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(prompt_number), 0) AS cur FROM prompts "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            prompt_number = int(row["cur"]) or 0
        if int(prompt_number) > 0:
            _enqueue_compression(
                conn, kind="turn",
                session_id=session_id, prompt_number=int(prompt_number),
            )
        conn.commit()
    except Exception:
        log.exception("Stop enqueue failed")
        return web.json_response({"ok": False}, status=202)
    return web.json_response({"ok": True})


async def handle_session_end(request: web.Request) -> web.Response:
    data = await _read_json(request)
    session_id = data.get("session_id")
    exit_reason = data.get("exit_reason") or "normal"
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)

    conn = _conn(request)
    try:
        epoch = _now_epoch()
        conn.execute(
            "UPDATE sessions SET status = 'completed', exit_reason = ?, "
            "ended_at_epoch = ?, ended_at = ? WHERE id = ?",
            (exit_reason, epoch, _now_iso(epoch), session_id),
        )
        _enqueue_compression(
            conn, kind="session_rollup",
            session_id=session_id, prompt_number=None,
        )
        conn.commit()
    except Exception:
        log.exception("SessionEnd update failed")
        return web.json_response({"ok": False}, status=202)
    return web.json_response({"ok": True})


def _enqueue_compression(
    conn: sqlite3.Connection,
    *,
    kind: str,
    session_id: str,
    prompt_number: int | None,
) -> None:
    """Add a (kind, session_id, prompt_number) row to pending_compressions.

    UNIQUE(kind, session_id, prompt_number) guards against double-enqueue when
    a prompt fires both Stop *and* the next UserPromptSubmit's "compress prev"
    trigger in quick succession.
    """
    conn.execute(
        "INSERT OR IGNORE INTO pending_compressions "
        "(kind, session_id, prompt_number, enqueued_at_epoch) "
        "VALUES (?, ?, ?, ?)",
        (kind, session_id, prompt_number, _now_epoch()),
    )


def add_routes(app: web.Application) -> None:
    """Attach the 5 hook routes to an existing aiohttp app."""
    app.router.add_post("/hooks/SessionStart", handle_session_start)
    app.router.add_post("/hooks/UserPromptSubmit", handle_user_prompt_submit)
    app.router.add_post("/hooks/PostToolUse", handle_post_tool_use)
    app.router.add_post("/hooks/Stop", handle_stop)
    app.router.add_post("/hooks/SessionEnd", handle_session_end)
