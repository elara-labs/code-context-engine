"""Tests for the background memory compression worker (PR 3)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from context_engine.memory import db as memory_db
from context_engine.memory import compressor as memory_compressor


class _StubEmbedder:
    """Same approach as test_extractive — fixed vectors based on a marker."""

    def embed_query(self, text: str) -> list[float]:
        if "KEY" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    c = memory_db.connect(db_path)
    yield c
    c.close()


def _seed_session(conn, session_id: str = "s1"):
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at) "
        "VALUES (?, 'demo', 1700000000, '2023-11-14T22:13:20')",
        (session_id,),
    )


def _seed_turn(conn, session_id: str, prompt_number: int, prompt_text: str):
    conn.execute(
        "INSERT INTO prompts (session_id, prompt_number, prompt_text, "
        "created_at_epoch, created_at) VALUES (?, ?, ?, 1700000000, "
        "'2023-11-14T22:13:20')",
        (session_id, prompt_number, prompt_text),
    )


def _seed_tool_event(conn, session_id, prompt_number, tool_name, tool_input, tool_output):
    cur = conn.execute(
        "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
        "VALUES (?, ?, ?)",
        (json.dumps(tool_input), tool_output, len(tool_output)),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
        "payload_id, created_at_epoch, created_at) VALUES (?, ?, ?, ?, "
        "1700000000, '2023-11-14T22:13:20')",
        (session_id, prompt_number, tool_name, pid),
    )


def test_compress_turn_writes_summary_with_extractive_tier(conn):
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "Look at KEY thing carefully. Also KEY matters here. Random unrelated text.")
    _seed_tool_event(conn, "s1", 1, "Read", {"file_path": "/tmp/foo.py"}, "KEY appears here too.")
    conn.commit()

    summary = memory_compressor.compress_turn(
        conn, session_id="s1", prompt_number=1, embedder=_StubEmbedder(),
    )
    conn.commit()

    assert "KEY" in summary
    row = conn.execute(
        "SELECT summary, tier FROM turn_summaries "
        "WHERE session_id = 's1' AND prompt_number = 1"
    ).fetchone()
    assert row["tier"] == "extractive"
    assert "KEY" in row["summary"]


def test_compress_turn_falls_back_to_truncation_without_embedder(conn):
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "Some text that is fairly long and would be summarised normally.")
    conn.commit()

    memory_compressor.compress_turn(
        conn, session_id="s1", prompt_number=1, embedder=None,
    )
    conn.commit()
    row = conn.execute(
        "SELECT tier FROM turn_summaries WHERE session_id = 's1'"
    ).fetchone()
    assert row["tier"] == "truncation"


def test_session_rollup_combines_turn_summaries(conn):
    _seed_session(conn)
    for n, t in enumerate([
        "First turn KEY discussed.",
        "Second turn KEY revisited.",
        "Third turn random other content.",
    ], start=1):
        _seed_turn(conn, "s1", n, t)
    conn.commit()

    for n in range(1, 4):
        memory_compressor.compress_turn(
            conn, session_id="s1", prompt_number=n, embedder=_StubEmbedder(),
        )
    conn.commit()

    rollup = memory_compressor.compress_session_rollup(
        conn, session_id="s1", embedder=_StubEmbedder(),
    )
    conn.commit()
    assert "KEY" in rollup
    row = conn.execute(
        "SELECT rollup_summary, rollup_summary_at_epoch FROM sessions WHERE id = 's1'"
    ).fetchone()
    assert row["rollup_summary"] == rollup
    assert row["rollup_summary_at_epoch"] is not None


def test_session_rollup_with_no_turns_is_empty(conn):
    _seed_session(conn)
    conn.commit()
    rollup = memory_compressor.compress_session_rollup(
        conn, session_id="s1", embedder=_StubEmbedder(),
    )
    conn.commit()
    assert rollup == ""


async def test_drain_one_processes_oldest_pending(conn):
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "Turn one with KEY content here. KEY appears twice. Other text.")
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch) VALUES ('turn', 's1', 1, 1700000000)"
    )
    conn.commit()

    did_work = await memory_compressor._drain_one(conn, _StubEmbedder())
    assert did_work is True

    pending = conn.execute("SELECT COUNT(*) AS n FROM pending_compressions").fetchone()["n"]
    assert pending == 0

    summary_row = conn.execute(
        "SELECT summary, tier FROM turn_summaries WHERE session_id = 's1'"
    ).fetchone()
    assert summary_row is not None
    assert summary_row["tier"] == "extractive"


async def test_drain_one_returns_false_when_queue_empty(conn):
    did_work = await memory_compressor._drain_one(conn, _StubEmbedder())
    assert did_work is False


async def test_compression_loop_drains_then_idles(conn):
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "KEY text here. KEY again. Random.")
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch) VALUES ('turn', 's1', 1, 1700000000)"
    )
    conn.commit()

    stop = asyncio.Event()
    task = asyncio.create_task(
        memory_compressor.compression_loop(
            conn, _StubEmbedder(), interval_seconds=0.05, stop_event=stop,
        )
    )
    # Give the loop one iteration to drain.
    await asyncio.sleep(0.2)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.TimeoutError:
        task.cancel()

    summary_row = conn.execute(
        "SELECT tier FROM turn_summaries WHERE session_id = 's1'"
    ).fetchone()
    assert summary_row is not None
    assert summary_row["tier"] == "extractive"
