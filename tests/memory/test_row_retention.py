"""Tests for row-level memory retention (decisions, turn_summaries, code_areas).

The TTL-based pruning is what keeps recall quality from degrading after
months of accumulation. Tests cover: things older than the cutoff are
deleted, things newer survive, optional archive-to-JSON works, and counts
returned match what was actually removed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from context_engine.memory import db as memory_db


def _seed(conn, *, table: str, age_days: float, **fields) -> int:
    """Insert a row with a synthetic created_at_epoch `age_days` ago.
    Returns lastrowid for assertions.
    """
    epoch = int(time.time()) - int(age_days * 86400)
    if table == "decisions":
        cur = conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) VALUES (?, ?, 'manual', ?, ?)",
            (
                fields.get("decision", "test decision"),
                fields.get("reason", "test reason"),
                epoch,
                time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)),
            ),
        )
    elif table == "code_areas":
        cur = conn.execute(
            "INSERT INTO code_areas (file_path, description, source, "
            "created_at_epoch) VALUES (?, ?, 'manual', ?)",
            (
                fields.get("file_path", "src/x.py"),
                fields.get("description", "touched"),
                epoch,
            ),
        )
    elif table == "turn_summaries":
        # turn_summaries has FK to sessions — seed one if not present.
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, project, started_at_epoch, "
            "started_at, status) VALUES ('s', 'demo', 0, '1970-01-01', 'completed')"
        )
        cur = conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) VALUES ('s', ?, ?, 'extractive', ?)",
            (
                fields.get("prompt_number", 1),
                fields.get("summary", "summary text"),
                epoch,
            ),
        )
    else:
        raise ValueError(table)
    conn.commit()
    return cur.lastrowid


def test_old_decisions_deleted_recent_survive(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed(conn, table="decisions", age_days=400, decision="ancient")
        _seed(conn, table="decisions", age_days=10, decision="fresh")
        out = memory_db.prune_old_rows(
            conn, storage_base=tmp_path,
            decision_days=365, archive=False,
        )
        assert out["decisions_pruned"] == 1
        rows = conn.execute("SELECT decision FROM decisions").fetchall()
        assert len(rows) == 1
        assert rows[0]["decision"] == "fresh"
    finally:
        conn.close()


def test_each_table_uses_its_own_cutoff(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed(conn, table="decisions",     age_days=300)  # < 365 → kept
        _seed(conn, table="turn_summaries", age_days=300)  # > 180 → deleted
        _seed(conn, table="code_areas",    age_days=300)  # > 180 → deleted
        out = memory_db.prune_old_rows(
            conn, storage_base=tmp_path,
            turn_days=180, decision_days=365, code_area_days=180,
            archive=False,
        )
        assert out["decisions_pruned"] == 0
        assert out["turns_pruned"] == 1
        assert out["code_areas_pruned"] == 1
    finally:
        conn.close()


def test_archive_writes_pruned_rows_to_json(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed(conn, table="decisions", age_days=400,
              decision="ancient", reason="historical")
        out = memory_db.prune_old_rows(
            conn, storage_base=tmp_path,
            decision_days=365, archive=True,
        )
        assert out["decisions_pruned"] == 1
        archives = list((tmp_path / "archives").glob("pruned-*.json"))
        assert len(archives) == 1
        data = json.loads(archives[0].read_text())
        assert "decisions" in data
        assert data["decisions"][0]["decision"] == "ancient"
    finally:
        conn.close()


def test_no_archive_when_nothing_pruned(tmp_path):
    """If nothing is old enough, the archives/ dir gets created but no
    file is written for the empty pass.
    """
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed(conn, table="decisions", age_days=10)
        memory_db.prune_old_rows(
            conn, storage_base=tmp_path,
            decision_days=365, archive=True,
        )
        archives = list((tmp_path / "archives").glob("pruned-*.json"))
        assert archives == []
    finally:
        conn.close()


def test_returns_zero_counts_when_db_empty(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        out = memory_db.prune_old_rows(conn, storage_base=tmp_path, archive=False)
        assert out == {
            "decisions_pruned": 0,
            "turns_pruned": 0,
            "code_areas_pruned": 0,
        }
    finally:
        conn.close()
