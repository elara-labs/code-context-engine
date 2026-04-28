"""Tests for memory.db schema bootstrap."""
from __future__ import annotations

from pathlib import Path

import pytest

from context_engine.memory import db as memory_db


def test_connect_creates_schema_on_empty_file(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        assert memory_db.schema_version(conn) == memory_db.CURRENT_VERSION
        # All declared tables exist.
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for required in [
            "sessions", "prompts", "tool_events", "tool_event_payloads",
            "turn_summaries", "decisions", "code_areas",
            "pending_compressions", "migrated_files", "schema_versions",
        ]:
            assert required in tables, f"missing table {required}"
        # FTS virtual tables exist too.
        for fts in ["prompts_fts", "decisions_fts", "turn_summaries_fts"]:
            assert fts in tables, f"missing fts {fts}"
    finally:
        conn.close()


def test_connect_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    c1 = memory_db.connect(db_path)
    c1.close()
    c2 = memory_db.connect(db_path)
    try:
        # Re-opening must not re-stamp the version row.
        rows = list(c2.execute("SELECT version FROM schema_versions"))
        assert len(rows) == 1
        assert rows[0]["version"] == memory_db.CURRENT_VERSION
    finally:
        c2.close()


def test_foreign_keys_enabled(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert result == 1
    finally:
        conn.close()


def test_decisions_fts_search(tmp_path: Path):
    """A decision inserted into the parent table is searchable via fts."""
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'manual', 1700000000, '2023-11-14T22:13:20')",
            ("Use SQLite for memory store", "Per-project files, FTS5 builtin"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT decisions.decision FROM decisions_fts "
            "JOIN decisions ON decisions.id = decisions_fts.rowid "
            "WHERE decisions_fts MATCH 'sqlite'"
        ).fetchall()
        assert len(rows) == 1
        assert "SQLite" in rows[0]["decision"]
    finally:
        conn.close()


def test_status_check_constraint(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO sessions (id, project, started_at_epoch, "
                "started_at, status) VALUES (?, ?, ?, ?, 'bogus')",
                ("abc", "demo", 1700000000, "2023-11-14T22:13:20"),
            )
    finally:
        conn.close()


# ── v2: sqlite-vec semantic recall ──────────────────────────────────────────


class _FakeEmbedder:
    """Deterministic stand-in for the real bge-small embedder.

    Returns a 384-dim vector seeded by a stable hash of the input. Two
    inputs that share words land near each other; otherwise vectors are
    roughly orthogonal. Good enough to validate wiring without paying the
    cost of loading fastembed in a unit test.
    """

    def __init__(self, dim: int = 384):
        self._dim = dim

    def embed_query(self, query: str):
        import hashlib, struct
        words = query.lower().split() or [query.lower()]
        acc = [0.0] * self._dim
        for w in words:
            digest = hashlib.sha256(w.encode("utf-8")).digest()
            # Tile the 32-byte digest across the vector deterministically.
            for i in range(self._dim):
                b = digest[i % 32]
                acc[i] += (b / 255.0) - 0.5
        # Normalise so cosine ~ dot.
        n = sum(x * x for x in acc) ** 0.5 or 1.0
        return tuple(x / n for x in acc)


def test_v2_creates_vec_tables(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        assert memory_db.schema_version(conn) == 2
        assert memory_db.has_vec_tables(conn)
    finally:
        conn.close()


def test_record_decision_vec_writes_and_searches(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    embedder = _FakeEmbedder()
    try:
        cur = conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'manual', 1700000000, '2023-11-14T22:13:20')",
            ("Use sqlite-vec for semantic recall", "Tiny binary, hybrid w/ FTS"),
        )
        memory_db.record_decision_vec(
            conn, embedder,
            decision_id=cur.lastrowid,
            decision="Use sqlite-vec for semantic recall",
            reason="Tiny binary, hybrid w/ FTS",
        )
        conn.commit()
        hits = memory_db.search_decisions_vec(
            conn, embedder, "sqlite-vec semantic", k=5, max_distance=99.0,
        )
        assert cur.lastrowid in hits


    finally:
        conn.close()


def test_record_turn_summary_vec_roundtrip(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    embedder = _FakeEmbedder()
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, "
            "started_at, status) VALUES ('s1', 'demo', 1700000000, "
            "'2023-11-14T22:13:20', 'active')"
        )
        cur = conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) VALUES ('s1', 1, ?, 'extractive', 1700000001)",
            ("User asked about hybrid recall; suggested sqlite-vec union.",),
        )
        memory_db.record_turn_summary_vec(
            conn, embedder,
            turn_id=cur.lastrowid,
            summary="User asked about hybrid recall; suggested sqlite-vec union.",
        )
        conn.commit()
        hits = memory_db.search_turn_summaries_vec(
            conn, embedder, "hybrid recall sqlite-vec", k=5, max_distance=99.0,
        )
        assert cur.lastrowid in hits
    finally:
        conn.close()


def test_backfill_populates_existing_rows(tmp_path: Path):
    """A db that has decisions but an empty vec table gets backfilled."""
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    embedder = _FakeEmbedder()
    try:
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'migrated', 1700000000, '2023-11-14T22:13:20')",
            ("Pick option A", "Lower latency"),
        )
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, "
            "started_at, status) VALUES ('s2', 'demo', 1700000000, "
            "'2023-11-14T22:13:20', 'active')"
        )
        conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) VALUES ('s2', 1, ?, 'extractive', 1700000001)",
            ("Discussion of option A latency tradeoffs.",),
        )
        conn.commit()

        counts = memory_db.backfill_vec_tables(conn, embedder)
        assert counts["decisions"] == 1
        assert counts["turn_summaries"] == 1

        # Second call is a no-op once the vec tables have any row.
        counts2 = memory_db.backfill_vec_tables(conn, embedder)
        assert counts2["decisions"] == 0
        assert counts2["turn_summaries"] == 0
    finally:
        conn.close()


def test_decisions_vec_cleaned_up_on_source_delete(tmp_path: Path):
    """Trigger should drop the vec row when its decisions row is deleted."""
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    embedder = _FakeEmbedder()
    try:
        cur = conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'manual', 1700000000, '2023-11-14T22:13:20')",
            ("Adopt RRF for hybrid recall", "Cheap and well-cited"),
        )
        decision_id = cur.lastrowid
        memory_db.record_decision_vec(
            conn, embedder,
            decision_id=decision_id,
            decision="Adopt RRF for hybrid recall",
            reason="Cheap and well-cited",
        )
        conn.commit()
        # Sanity: vec row exists.
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM decisions_vec WHERE rowid = ?",
            (decision_id,),
        ).fetchone()["n"]
        assert before == 1

        conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) AS n FROM decisions_vec WHERE rowid = ?",
            (decision_id,),
        ).fetchone()["n"]
        assert after == 0, "trigger should have dropped the orphaned vec row"
    finally:
        conn.close()


def test_turn_summaries_vec_cleaned_up_on_source_delete(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    embedder = _FakeEmbedder()
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, "
            "started_at, status) VALUES ('sx', 'demo', 1700000000, "
            "'2023-11-14T22:13:20', 'active')"
        )
        cur = conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) VALUES ('sx', 1, ?, 'extractive', 1700000001)",
            ("test summary",),
        )
        turn_id = cur.lastrowid
        memory_db.record_turn_summary_vec(
            conn, embedder, turn_id=turn_id, summary="test summary",
        )
        conn.commit()

        conn.execute("DELETE FROM turn_summaries WHERE id = ?", (turn_id,))
        conn.commit()

        n = conn.execute(
            "SELECT COUNT(*) AS n FROM turn_summaries_vec WHERE rowid = ?",
            (turn_id,),
        ).fetchone()["n"]
        assert n == 0
    finally:
        conn.close()


def test_prune_old_payloads_nulls_aged_raws_and_keeps_summaries(tmp_path: Path):
    """Old payloads have raw_input/raw_output NULLed; summaries stay intact."""
    import time
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
            "status) VALUES ('sret', 'demo', 1700000000, '2023-11-14T22:13:20', "
            "'active')"
        )
        # Old payload (90 days ago) — should be aged out.
        old_epoch = int(time.time()) - 90 * 86_400
        old_pid = conn.execute(
            "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
            "VALUES (?, ?, ?)",
            ('{"cmd":"ls"}', "x" * 5000, 5012),
        ).lastrowid
        conn.execute(
            "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
            "payload_id, summary, created_at_epoch, created_at) "
            "VALUES ('sret', 1, 'Bash', ?, 'ran ls', ?, '2023-11-14T22:13:20')",
            (old_pid, old_epoch),
        )
        # Recent payload (1 day ago) — should be kept.
        recent_epoch = int(time.time()) - 86_400
        recent_pid = conn.execute(
            "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
            "VALUES (?, ?, ?)",
            ('{"cmd":"pwd"}', "y" * 100, 113),
        ).lastrowid
        conn.execute(
            "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
            "payload_id, summary, created_at_epoch, created_at) "
            "VALUES ('sret', 2, 'Bash', ?, 'ran pwd', ?, '2025-01-01T00:00:00')",
            (recent_pid, recent_epoch),
        )
        conn.commit()

        out = memory_db.prune_old_payloads(conn, days=30)
        assert out["payloads_pruned"] == 1
        assert out["bytes_freed_estimate"] == 5012

        old_row = conn.execute(
            "SELECT raw_input, raw_output FROM tool_event_payloads WHERE id = ?",
            (old_pid,),
        ).fetchone()
        # raw_input is NOT NULL in the schema, so we use '' as the aged
        # sentinel; raw_output is nullable.
        assert old_row["raw_input"] == ""
        assert old_row["raw_output"] is None

        recent_row = conn.execute(
            "SELECT raw_input, raw_output FROM tool_event_payloads WHERE id = ?",
            (recent_pid,),
        ).fetchone()
        assert recent_row["raw_input"] is not None
        assert recent_row["raw_output"] is not None

        # Summary on tool_events is untouched.
        old_evt = conn.execute(
            "SELECT summary FROM tool_events WHERE payload_id = ?", (old_pid,)
        ).fetchone()
        assert old_evt["summary"] == "ran ls"

        # Idempotent — second call is a no-op.
        out2 = memory_db.prune_old_payloads(conn, days=30)
        assert out2["payloads_pruned"] == 0
    finally:
        conn.close()


async def test_auto_prune_loop_runs_one_iteration(tmp_path: Path):
    """auto_prune_loop with initial_delay=0 + tiny interval drains old payloads
    on its first pass and exits cleanly when stop_event is set."""
    import asyncio
    import time
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    try:
        old_epoch = int(time.time()) - 60 * 86_400
        pid = conn.execute(
            "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
            "VALUES (?, ?, ?)",
            ('{"x":1}', "y" * 200, 207),
        ).lastrowid
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
            "status) VALUES ('sap', 'demo', 1700000000, '2023-11-14T22:13:20', "
            "'completed')"
        )
        conn.execute(
            "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
            "payload_id, created_at_epoch, created_at) "
            "VALUES ('sap', 1, 'Read', ?, ?, '2023-11-14T22:13:20')",
            (pid, old_epoch),
        )
        conn.commit()
    finally:
        conn.close()

    stop = asyncio.Event()
    task = asyncio.create_task(
        memory_db.auto_prune_loop(
            tmp_path, days=30, initial_delay=0.0, interval=0.05,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    conn = memory_db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT raw_input, raw_output FROM tool_event_payloads WHERE id = ?",
            (pid,),
        ).fetchone()
        assert row["raw_input"] == ""
        assert row["raw_output"] is None
    finally:
        conn.close()


async def test_auto_prune_loop_stop_event_short_circuits_initial_delay(tmp_path: Path):
    """stop_event firing during the initial_delay stagger exits the loop
    cleanly without doing any work — matters for cce serve shutting down
    before the first pass."""
    import asyncio
    db_path = tmp_path / "memory.db"
    conn = memory_db.connect(db_path)
    conn.close()

    stop = asyncio.Event()
    task = asyncio.create_task(
        memory_db.auto_prune_loop(
            tmp_path, days=30, initial_delay=10.0, interval=10.0,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


def test_v1_to_v2_upgrade_in_place(tmp_path: Path):
    """A db stamped at v1 (no vec tables) gains them on the next connect()."""
    import sqlite3
    db_path = tmp_path / "memory.db"
    # Bootstrap a real db at v2 first, then forge it back to v1.
    conn = memory_db.connect(db_path)
    conn.execute("DROP TABLE decisions_vec")
    conn.execute("DROP TABLE turn_summaries_vec")
    conn.execute("DELETE FROM schema_versions WHERE version = 2")
    conn.execute(
        "INSERT INTO schema_versions (version, applied_at_epoch) "
        "VALUES (1, strftime('%s','now'))"
    )
    conn.commit()
    conn.close()
    # Sanity: looks like v1 now.
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    assert raw.execute(
        "SELECT MAX(version) AS v FROM schema_versions"
    ).fetchone()["v"] == 1
    raw.close()

    # Reopening should run the v1 → v2 migration.
    conn = memory_db.connect(db_path)
    try:
        assert memory_db.schema_version(conn) == 2
        assert memory_db.has_vec_tables(conn)
    finally:
        conn.close()
