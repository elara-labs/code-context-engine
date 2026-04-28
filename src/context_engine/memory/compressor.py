"""Background compression worker for the memory store.

Drains `pending_compressions` rows on a fixed interval, calls the extractive
summariser for each, writes the result to `turn_summaries` (or
`sessions.rollup_summary` for kind='session_rollup'), and removes the queue
row. Failures bump the row's `attempts` and log; the row remains queued for
retry on the next pass.

Designed to run as an asyncio task inside `cce serve`. Single-flight by
construction — only one worker drains at a time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time

from context_engine.memory.extractive import extractive_summary, truncation_summary

log = logging.getLogger(__name__)

_DEFAULT_TURN_TOP_K = 3
_DEFAULT_ROLLUP_TOP_K = 5
_DEFAULT_INTERVAL_SECONDS = 5.0
_TOOL_OUTPUT_CHAR_CAP = 1500  # avoid embedding multi-MB tool outputs
_TOOL_INPUT_CHAR_CAP = 4000  # skip JSON parsing for huge tool inputs (e.g. patches)


def compress_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    prompt_number: int,
    embedder,
) -> str:
    """Compute and persist a turn summary. Returns the summary text."""
    text = _build_turn_text(conn, session_id=session_id, prompt_number=prompt_number)
    summary, tier = _summarise(text, embedder=embedder, top_k=_DEFAULT_TURN_TOP_K)
    epoch = int(time.time())
    conn.execute(
        "INSERT OR REPLACE INTO turn_summaries "
        "(session_id, prompt_number, summary, tier, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, prompt_number, summary, tier, epoch),
    )
    return summary


def compress_session_rollup(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    embedder,
) -> str:
    """Compute the session rollup summary from existing turn summaries.

    If a session has no turn_summaries yet (e.g. SessionEnd fired before the
    worker drained any turns), we fall through to an empty rollup; the
    session row is still updated so the timeline view shows it as completed.
    """
    rows = list(conn.execute(
        "SELECT summary FROM turn_summaries WHERE session_id = ? "
        "ORDER BY prompt_number ASC",
        (session_id,),
    ))
    text = "\n".join(r["summary"] for r in rows if r["summary"])
    if not text:
        rollup = ""
        tier = "empty"
    else:
        rollup, tier = _summarise(text, embedder=embedder, top_k=_DEFAULT_ROLLUP_TOP_K)
    epoch = int(time.time())
    conn.execute(
        "UPDATE sessions SET rollup_summary = ?, rollup_summary_at_epoch = ? "
        "WHERE id = ?",
        (rollup, epoch, session_id),
    )
    log.debug("session rollup tier=%s len=%d", tier, len(rollup))
    return rollup


def _build_turn_text(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    prompt_number: int,
) -> str:
    """Concatenate prompt + tool inputs/outputs into one big text blob."""
    parts: list[str] = []

    prompt = conn.execute(
        "SELECT prompt_text FROM prompts WHERE session_id = ? AND prompt_number = ?",
        (session_id, prompt_number),
    ).fetchone()
    if prompt and prompt["prompt_text"]:
        parts.append(f"User: {prompt['prompt_text']}")

    events = conn.execute(
        "SELECT te.tool_name, p.raw_input, p.raw_output FROM tool_events te "
        "LEFT JOIN tool_event_payloads p ON p.id = te.payload_id "
        "WHERE te.session_id = ? AND te.prompt_number = ? "
        "ORDER BY te.id ASC",
        (session_id, prompt_number),
    ).fetchall()

    for ev in events:
        descriptor = _describe_input(ev["tool_name"], ev["raw_input"] or "")
        parts.append(descriptor)
        out = (ev["raw_output"] or "").strip()
        if out:
            if len(out) > _TOOL_OUTPUT_CHAR_CAP:
                out = out[:_TOOL_OUTPUT_CHAR_CAP] + "…"
            parts.append(out)
    return "\n".join(parts)


def _describe_input(tool_name: str, raw_input: str) -> str:
    """One-line descriptor of a tool invocation for the summary candidates."""
    if not raw_input:
        return tool_name
    # Skip JSON parsing on oversize payloads (patches, large file contents) —
    # the compression worker runs on the asyncio thread and we don't want it
    # spending tens of ms parsing megabytes just to format a one-liner.
    if len(raw_input) > _TOOL_INPUT_CHAR_CAP:
        return f"{tool_name}: {raw_input[:120]}"
    try:
        data = json.loads(raw_input)
    except (json.JSONDecodeError, ValueError):
        return f"{tool_name}: {raw_input[:120]}"
    if not isinstance(data, dict):
        return f"{tool_name}: {raw_input[:120]}"
    # Surface common high-signal fields explicitly.
    for key in ("file_path", "command", "pattern", "path", "query"):
        if key in data and data[key]:
            return f"{tool_name} {key}={data[key]!r}"
    keys = list(data.keys())[:2]
    return f"{tool_name} {keys}"


def _summarise(text: str, *, embedder, top_k: int) -> tuple[str, str]:
    """Run extractive summarisation, falling back to truncation on failure."""
    if not text.strip():
        return "", "empty"
    if embedder is None:
        return truncation_summary(text), "truncation"
    try:
        out = extractive_summary(text, embedder=embedder, top_k=top_k)
        return out, "extractive"
    except Exception:
        log.exception("extractive failed; falling back to truncation")
        return truncation_summary(text), "truncation"


async def _drain_one(conn: sqlite3.Connection, embedder) -> bool:
    """Pop and process the oldest pending row. Returns True if work was done.

    The compress functions run synchronously on the event-loop thread.
    They're fast in practice (<200 ms for a single-turn extractive
    summary on bge-small) and an in-process call avoids the SQLite
    cross-thread restriction we'd hit if we tried asyncio.to_thread.
    """
    row = conn.execute(
        "SELECT id, kind, session_id, prompt_number, attempts FROM pending_compressions "
        "ORDER BY enqueued_at_epoch ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return False
    try:
        if row["kind"] == "turn":
            compress_turn(
                conn,
                session_id=row["session_id"],
                prompt_number=row["prompt_number"],
                embedder=embedder,
            )
        else:
            compress_session_rollup(
                conn,
                session_id=row["session_id"],
                embedder=embedder,
            )
        conn.execute("DELETE FROM pending_compressions WHERE id = ?", (row["id"],))
        conn.commit()
    except Exception as exc:
        log.exception("Compression failed for %s/%s/%s",
                      row["kind"], row["session_id"], row["prompt_number"])
        conn.execute(
            "UPDATE pending_compressions SET attempts = attempts + 1, "
            "last_error = ? WHERE id = ?",
            (str(exc)[:500], row["id"]),
        )
        conn.commit()
    return True


_BACKLOG_BATCH = 5  # drain at most this many items before yielding to other tasks


async def compression_loop(
    conn: sqlite3.Connection,
    embedder,
    *,
    interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run forever, draining the queue between sleeps. Cancellable via task.cancel().

    `_drain_one` runs synchronously on the event-loop thread (embed + SQLite),
    so under a backlog we yield with `sleep(0)` after each item and take a
    short breath every `_BACKLOG_BATCH` items to keep `mcp.run_stdio()`
    responsive instead of monopolising the loop.
    """
    consecutive = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            did_work = await _drain_one(conn, embedder)
            if did_work:
                consecutive += 1
                if consecutive >= _BACKLOG_BATCH:
                    consecutive = 0
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(0)
            else:
                consecutive = 0
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("compression_loop iteration crashed; backing off")
            consecutive = 0
            await asyncio.sleep(interval_seconds)
