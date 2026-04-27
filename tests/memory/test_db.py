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
