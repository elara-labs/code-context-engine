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


class _DimEmbedder:
    """Deterministic 384-dim embedder so vec-table writes actually land
    (the 2-dim stub trips the float[384] dim check and gets swallowed)."""

    def embed_query(self, text: str) -> list[float]:
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [((digest[i % 32] / 255.0) - 0.5) for i in range(384)]
        n = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / n for x in vec]


def test_recompress_turn_keeps_fts_and_vec_consistent(conn):
    """Re-compressing the same turn (Stop + next UserPromptSubmit both
    enqueue it) must not leave dangling FTS entries or stale vec rows.
    `INSERT OR REPLACE` silently skipped the delete triggers because
    recursive_triggers is OFF by default."""
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "KEY discussion first pass. KEY again matters. Filler text here.")
    conn.commit()

    for _ in range(2):
        memory_compressor.compress_turn(
            conn, session_id="s1", prompt_number=1, embedder=_DimEmbedder(),
        )
        conn.commit()

    ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM turn_summaries "
            "WHERE session_id = 's1' AND prompt_number = 1"
        )
    }
    assert len(ids) == 1

    # FTS5 external-content integrity check raises SQLITE_CORRUPT_VTAB on
    # dangling index entries.
    conn.execute(
        "INSERT INTO turn_summaries_fts(turn_summaries_fts) "
        "VALUES('integrity-check')"
    )

    # Every FTS hit maps back to a live source row.
    fts_ids = {
        r["rowid"] for r in conn.execute(
            "SELECT rowid FROM turn_summaries_fts "
            "WHERE turn_summaries_fts MATCH 'KEY'"
        )
    }
    assert fts_ids <= ids, f"dangling FTS rowids: {fts_ids - ids}"

    # No stale vec rows pointing at replaced rowids.
    vec_ids = {
        r["rowid"] for r in conn.execute(
            "SELECT rowid FROM turn_summaries_vec"
        )
    }
    assert vec_ids <= ids, f"stale vec rowids: {vec_ids - ids}"


def test_drain_skips_rows_at_attempt_cap(conn):
    """A row that has already failed _MAX_ATTEMPTS times is dead-lettered:
    left in the table for inspection but never picked again."""
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "some text")
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch, attempts) VALUES ('turn', 's1', 1, 1700000000, ?)",
        (memory_compressor._MAX_ATTEMPTS,),
    )
    conn.commit()

    did_work = memory_compressor._drain_one_sync(conn, _StubEmbedder())
    assert did_work is False, "capped row must not be picked"
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_compressions"
    ).fetchone()["n"]
    assert n == 1, "dead-letter row stays for inspection"


def test_failing_row_does_not_starve_queue(conn, monkeypatch):
    """A deterministically-failing older row must stop being retried after
    _MAX_ATTEMPTS so younger rows still drain."""
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "poison turn text")
    _seed_turn(conn, "s1", 2, "healthy KEY turn text. KEY again. Filler.")
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch) VALUES ('turn', 's1', 1, 100)"
    )
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch) VALUES ('turn', 's1', 2, 200)"
    )
    conn.commit()

    real_build = memory_compressor._build_turn_text

    def _poisoned(c, *, session_id, prompt_number):
        if prompt_number == 1:
            raise RuntimeError("boom")
        return real_build(c, session_id=session_id, prompt_number=prompt_number)

    monkeypatch.setattr(memory_compressor, "_build_turn_text", _poisoned)

    # Enough drains for the poison row to hit the cap plus one for the
    # healthy row.
    for _ in range(memory_compressor._MAX_ATTEMPTS + 1):
        memory_compressor._drain_one_sync(conn, _StubEmbedder())

    healthy = conn.execute(
        "SELECT COUNT(*) AS n FROM turn_summaries "
        "WHERE session_id = 's1' AND prompt_number = 2"
    ).fetchone()["n"]
    assert healthy == 1, "healthy row must drain despite the poison row"

    poison = conn.execute(
        "SELECT attempts FROM pending_compressions "
        "WHERE prompt_number = 1"
    ).fetchone()
    assert poison is not None
    assert poison["attempts"] == memory_compressor._MAX_ATTEMPTS


def test_failed_compression_records_no_savings(conn, monkeypatch):
    """Savings must only be recorded when the summary write succeeds —
    otherwise every retry of a failing row inflates the ledger."""
    _seed_session(conn)
    _seed_turn(conn, "s1", 1, "KEY text that fails late. KEY again. Filler.")
    conn.execute(
        "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
        "enqueued_at_epoch) VALUES ('turn', 's1', 1, 1700000000)"
    )
    conn.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("decision extraction exploded")

    monkeypatch.setattr(memory_compressor, "_auto_capture_decisions", _boom)

    for _ in range(2):
        memory_compressor._drain_one_sync(conn, _StubEmbedder())

    n = conn.execute("SELECT COUNT(*) AS n FROM savings_log").fetchone()["n"]
    assert n == 0, "failed compressions must not record savings"
    attempts = conn.execute(
        "SELECT attempts FROM pending_compressions"
    ).fetchone()["attempts"]
    assert attempts == 2


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
