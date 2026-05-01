"""Integration tests for the 5 lifecycle-hook HTTP endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from context_engine.memory import db as memory_db
from context_engine.memory.hooks import add_routes


@pytest.fixture
async def hook_app(tmp_path: Path):
    """An aiohttp Application with the memory db wired up + hook routes."""
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    app = web.Application()
    app["memory_db"] = conn
    app["project_name"] = "demo"
    add_routes(app)
    yield app, conn
    conn.close()


async def test_session_start_inserts_session(hook_app, aiohttp_client):
    app, conn = hook_app
    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "abc", "project": "demo", "started_at": 1700000000},
    )
    assert resp.status == 200
    # First-ever session — no prior decisions/rollups to surface.
    text = await resp.text()
    assert text == ""

    rows = list(conn.execute("SELECT id, project, status FROM sessions"))
    assert len(rows) == 1
    assert rows[0]["id"] == "abc"
    assert rows[0]["project"] == "demo"
    assert rows[0]["status"] == "active"


async def test_session_start_idempotent(hook_app, aiohttp_client):
    app, conn = hook_app
    client = await aiohttp_client(app)
    for _ in range(3):
        resp = await client.post(
            "/hooks/SessionStart",
            json={"session_id": "abc", "project": "demo"},
        )
        assert resp.status == 200
    n = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    assert n == 1


async def test_session_start_resume_expands_compressed_text(hook_app, aiohttp_client):
    """Stored decisions / rollups went through grammar.compress on write.
    The resume body must run them through expand() so the agent sees natural
    prose ("because") rather than the storage form ("b/c"). This guards
    against a regression where someone removes the expand call."""
    app, conn = hook_app
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "ended_at_epoch, ended_at, status, rollup_summary, "
        "rollup_summary_at_epoch) VALUES "
        "('compr', 'demo', 1700000000, '2023-11-14T22:13:20', "
        "1700003600, '2023-11-14T23:13:20', 'completed', "
        "'Picked auth via JWT b/c mesh issues keys', 1700003600)"
    )
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('Use prod config for tests', 'Avoids dev/prod drift', 'manual', "
        "1700001000, '2023-11-14T22:30:00')"
    )
    conn.commit()

    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "newcompr", "project": "demo"},
    )
    text = await resp.text()
    # b/c → because (rollup), prod → production (decision)
    assert "because" in text, f"expand() not applied: {text!r}"
    assert "production" in text, f"expand() not applied to decision: {text!r}"
    assert "b/c" not in text


async def test_session_start_resume_with_only_decisions(hook_app, aiohttp_client):
    """Common week-1 state: decisions exist but no session has rollup yet.
    The resume must still surface the decisions section."""
    app, conn = hook_app
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('Use Postgres for primary store', 'Boring, complete, ACID', 'manual', "
        "1700000000, '2023-11-14T22:13:20')"
    )
    conn.commit()
    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "newdec", "project": "demo"},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "Recent decisions" in text
    assert "Use Postgres" in text
    assert "Previous session" not in text  # no rollup → no rollup section


async def test_session_start_resume_with_only_rollup(hook_app, aiohttp_client):
    """Inverse case: a prior session has a rollup but no decisions were
    recorded. The resume must still show the rollup."""
    app, conn = hook_app
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "ended_at_epoch, ended_at, status, rollup_summary, "
        "rollup_summary_at_epoch) VALUES "
        "('roll', 'demo', 1700000000, '2023-11-14T22:13:20', "
        "1700003600, '2023-11-14T23:13:20', 'completed', "
        "'Investigated cache miss on tag lookup; pinpointed serialiser fork.', "
        "1700003600)"
    )
    conn.commit()
    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "newroll", "project": "demo"},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "Previous session" in text
    assert "cache miss" in text
    assert "Recent decisions" not in text  # no decisions → no decisions section


async def test_session_start_returns_resume_with_prior_rollup_and_decisions(
    hook_app, aiohttp_client,
):
    """The big one — fixes 'decisions you made last week have to be re-explained today.'

    A session with a rollup_summary and a few decisions should surface both
    in the SessionStart hook's response, ready to be injected into the
    model's context at the start of the new session.
    """
    app, conn = hook_app
    # Seed a completed prior session.
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "ended_at_epoch, ended_at, status, prompt_count, "
        "rollup_summary, rollup_summary_at_epoch) VALUES "
        "('prev', 'demo', 1700000000, '2023-11-14T22:13:20', "
        "1700003600, '2023-11-14T23:13:20', 'completed', 5, "
        "'Worked on auth: chose JWT/RS256, refresh tokens rotate.', 1700003600)"
    )
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, session_id, "
        "created_at_epoch, created_at) VALUES "
        "('Use JWT with RS256', 'Mesh issues these', 'manual', 'prev', "
        "1700001000, '2023-11-14T22:30:00')"
    )
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, session_id, "
        "created_at_epoch, created_at) VALUES "
        "('Risk limit at 2% per trade', 'Kelly criterion', 'manual', 'prev', "
        "1700002000, '2023-11-14T22:46:40')"
    )
    conn.commit()

    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "new", "project": "demo"},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "## CCE memory · resuming demo" in text
    assert "Previous session" in text
    assert "JWT/RS256" in text
    assert "Recent decisions" in text
    assert "Use JWT with RS256" in text
    assert "Risk limit at 2% per trade" in text
    assert "session_recall" in text  # affordance hint at the bottom


async def test_user_prompt_submit_inserts_and_assigns_number(hook_app, aiohttp_client):
    app, conn = hook_app
    client = await aiohttp_client(app)
    await client.post(
        "/hooks/SessionStart", json={"session_id": "abc", "project": "demo"},
    )
    r1 = await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "hello"},
    )
    r2 = await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "world"},
    )
    assert (await r1.json())["prompt_number"] == 1
    assert (await r2.json())["prompt_number"] == 2

    rows = list(conn.execute(
        "SELECT prompt_number, prompt_text FROM prompts ORDER BY prompt_number"
    ))
    assert [(r["prompt_number"], r["prompt_text"]) for r in rows] == [
        (1, "hello"), (2, "world"),
    ]
    # Second prompt enqueued compression for prior turn (prompt 1).
    pending = list(conn.execute(
        "SELECT kind, session_id, prompt_number FROM pending_compressions"
    ))
    assert pending == [{"kind": "turn", "session_id": "abc", "prompt_number": 1}] \
        if isinstance(pending[0], dict) else len(pending) == 1


async def test_post_tool_use_inserts_event_and_payload(hook_app, aiohttp_client):
    app, conn = hook_app
    client = await aiohttp_client(app)
    await client.post(
        "/hooks/SessionStart", json={"session_id": "abc", "project": "demo"},
    )
    await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "hi"},
    )
    resp = await client.post(
        "/hooks/PostToolUse",
        json={
            "session_id": "abc",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/foo.py"},
            "tool_output": "x = 1\n",
        },
    )
    assert resp.status == 200
    events = list(conn.execute(
        "SELECT tool_name, payload_id, prompt_number FROM tool_events"
    ))
    assert len(events) == 1
    assert events[0]["tool_name"] == "Read"
    assert events[0]["prompt_number"] == 1
    payload_id = events[0]["payload_id"]
    payload = conn.execute(
        "SELECT raw_input, raw_output, size_bytes FROM tool_event_payloads "
        "WHERE id = ?", (payload_id,),
    ).fetchone()
    assert "/tmp/foo.py" in payload["raw_input"]
    assert "x = 1" in payload["raw_output"]
    assert payload["size_bytes"] > 0


async def test_stop_enqueues_turn_compression(hook_app, aiohttp_client):
    app, conn = hook_app
    client = await aiohttp_client(app)
    await client.post(
        "/hooks/SessionStart", json={"session_id": "abc", "project": "demo"},
    )
    await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "hi"},
    )
    resp = await client.post(
        "/hooks/Stop",
        json={"session_id": "abc"},
    )
    assert resp.status == 200
    pending = list(conn.execute(
        "SELECT kind, session_id, prompt_number FROM pending_compressions"
    ))
    assert any(
        p["kind"] == "turn" and p["prompt_number"] == 1 for p in pending
    )


async def test_session_end_marks_completed_and_enqueues_rollup(
    hook_app, aiohttp_client,
):
    app, conn = hook_app
    client = await aiohttp_client(app)
    await client.post(
        "/hooks/SessionStart", json={"session_id": "abc", "project": "demo"},
    )
    resp = await client.post(
        "/hooks/SessionEnd",
        json={"session_id": "abc", "exit_reason": "normal"},
    )
    assert resp.status == 200
    row = conn.execute(
        "SELECT status, exit_reason, ended_at_epoch FROM sessions WHERE id = ?",
        ("abc",),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["exit_reason"] == "normal"
    assert row["ended_at_epoch"] is not None

    pending = list(conn.execute(
        "SELECT kind, session_id, prompt_number FROM pending_compressions "
        "WHERE kind = 'session_rollup'"
    ))
    assert len(pending) == 1
    assert pending[0]["session_id"] == "abc"


async def test_missing_session_id_returns_400(hook_app, aiohttp_client):
    app, _ = hook_app
    client = await aiohttp_client(app)
    for endpoint in [
        "/hooks/SessionStart",
        "/hooks/UserPromptSubmit",
        "/hooks/PostToolUse",
        "/hooks/Stop",
        "/hooks/SessionEnd",
    ]:
        resp = await client.post(endpoint, json={})
        assert resp.status == 400, f"{endpoint} should require session_id"


async def test_compression_queue_dedupes(hook_app, aiohttp_client):
    """Stop and the next UserPromptSubmit can both enqueue the same turn."""
    app, conn = hook_app
    client = await aiohttp_client(app)
    await client.post(
        "/hooks/SessionStart", json={"session_id": "abc", "project": "demo"},
    )
    await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "hi"},
    )
    await client.post("/hooks/Stop", json={"session_id": "abc"})
    # Next prompt would also enqueue prev turn (1).
    await client.post(
        "/hooks/UserPromptSubmit",
        json={"session_id": "abc", "prompt_text": "next"},
    )
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_compressions "
        "WHERE kind = 'turn' AND session_id = 'abc' AND prompt_number = 1"
    ).fetchone()["n"]
    assert n == 1, "double-enqueue should be deduped by UNIQUE constraint"


# ── Savings visibility tests ──────────────────────────────────────────────


async def test_build_savings_line_empty_db(hook_app):
    """No savings data → empty string."""
    _, conn = hook_app
    from context_engine.memory.hooks import _build_savings_line
    assert _build_savings_line(conn) == ""


async def test_build_savings_line_with_data(hook_app):
    """With savings_log rows, returns a formatted one-liner.

    Uses retrieval baseline (full-file tokens) and chunk_compression served
    (final compressed tokens) to avoid double-counting across pipeline stages.
    Query count comes from retrieval calls only.
    """
    _, conn = hook_app
    from context_engine.memory.hooks import _build_savings_line
    memory_db.record_savings(conn, bucket="retrieval", baseline=10000, served=1000)
    memory_db.record_savings(conn, bucket="retrieval", baseline=20000, served=2000)
    memory_db.record_savings(conn, bucket="chunk_compression", baseline=3000, served=300)

    line = _build_savings_line(conn)
    assert "CCE saved" in line
    assert "2 queries" in line
    # retrieval baseline=30000, chunk_compression served=300 → 99% savings
    assert "99%" in line
    assert "30.0k" in line
    assert "300" in line


async def test_session_start_includes_savings(hook_app, aiohttp_client):
    """SessionStart resume should include the savings line when data exists."""
    app, conn = hook_app
    # Need at least one decision or rollup for resume to be non-empty
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('Use SQLite', 'Simpler than Postgres', 'manual', "
        "1700001000, '2023-11-14T22:30:00')"
    )
    # Add savings data
    memory_db.record_savings(conn, bucket="retrieval", baseline=50000, served=5000)
    conn.commit()

    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "savings-test", "project": "demo"},
    )
    text = await resp.text()
    assert "CCE saved" in text, f"Savings line missing from resume: {text!r}"
    assert "90%" in text


async def test_session_start_no_savings_no_line(hook_app, aiohttp_client):
    """SessionStart resume should NOT include savings when no data exists."""
    app, conn = hook_app
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('Use JWT', 'Legal requirement', 'manual', "
        "1700001000, '2023-11-14T22:30:00')"
    )
    conn.commit()

    client = await aiohttp_client(app)
    resp = await client.post(
        "/hooks/SessionStart",
        json={"session_id": "no-savings", "project": "demo"},
    )
    text = await resp.text()
    assert "CCE saved" not in text
