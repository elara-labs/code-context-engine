"""Tests for `cce sessions migrate` — JSON to memory.db importer."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from context_engine.memory import db as memory_db, migrate as memory_migrate


def _write_session(sessions_dir: Path, session_id: str, body: dict) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    p = sessions_dir / f"{session_id}.json"
    p.write_text(json.dumps(body))
    return p


def test_migrate_imports_decisions_from_session_json(tmp_path: Path):
    storage = tmp_path / "storage"
    sessions = storage / "sessions"
    _write_session(sessions, "abc123", {
        "id": "abc123",
        "decisions": [
            {"decision": "Use bge-small for extractive", "reason": "Already loaded", "timestamp": 1700000000},
            {"decision": "5 hooks not 1", "reason": "claude-mem parity", "timestamp": 1700000100},
        ],
        "code_areas": [
            {"file_path": "src/foo.py", "description": "memory db init", "timestamp": 1700000200},
        ],
    })

    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    try:
        summary = memory_migrate.migrate(
            conn, project_name="demo", storage_base=storage, archive=False,
        )
    finally:
        conn.close()

    assert summary.files_imported == 1
    assert summary.decisions_imported == 2
    assert summary.code_areas_imported == 1

    # Verify the rows landed and source='migrated'.
    conn = memory_db.connect(db_path)
    try:
        rows = list(conn.execute(
            "SELECT decision, source FROM decisions ORDER BY created_at_epoch"
        ))
        assert len(rows) == 2
        assert all(r["source"] == "migrated" for r in rows)
        assert "bge-small" in rows[0]["decision"]
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path: Path):
    storage = tmp_path / "storage"
    sessions = storage / "sessions"
    _write_session(sessions, "abc123", {
        "id": "abc123",
        "decisions": [{"decision": "X", "reason": "Y", "timestamp": 1700000000}],
        "code_areas": [],
    })

    db_path = memory_db.memory_db_path(storage)

    conn = memory_db.connect(db_path)
    try:
        s1 = memory_migrate.migrate(
            conn, project_name="demo", storage_base=storage, archive=False,
        )
    finally:
        conn.close()
    assert s1.files_imported == 1

    # Re-write the source so the second pass would see content if it
    # weren't tracking already-imported files.
    _write_session(sessions, "abc123", {
        "id": "abc123",
        "decisions": [{"decision": "X", "reason": "Y", "timestamp": 1700000000}],
        "code_areas": [],
    })

    conn = memory_db.connect(db_path)
    try:
        s2 = memory_migrate.migrate(
            conn, project_name="demo", storage_base=storage, archive=False,
        )
    finally:
        conn.close()
    assert s2.files_imported == 0
    assert s2.files_skipped == 1

    conn = memory_db.connect(db_path)
    try:
        n_decisions = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
    finally:
        conn.close()
    # Only the first migrate should have imported the decision.
    assert n_decisions == 1


def test_migrate_archives_consumed_files(tmp_path: Path):
    storage = tmp_path / "storage"
    sessions = storage / "sessions"
    p = _write_session(sessions, "abc123", {
        "id": "abc123",
        "decisions": [{"decision": "X", "reason": "Y", "timestamp": 1700000000}],
        "code_areas": [],
    })

    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    try:
        summary = memory_migrate.migrate(
            conn, project_name="demo", storage_base=storage, archive=True,
        )
    finally:
        conn.close()

    assert summary.files_archived == 1
    assert not p.exists()
    archive = sessions / "migrated.zip"
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "abc123.json" in names


def test_migrate_decisions_log_top_level_list(tmp_path: Path):
    """decisions_log.json (the consolidated archive) is a top-level list."""
    storage = tmp_path / "storage"
    sessions = storage / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    log_path = sessions / "decisions_log.json"
    log_path.write_text(json.dumps([
        {"decision": "Pick A", "reason": "B", "timestamp": 1700000000, "session_id": "s1"},
        {"decision": "Pick C", "reason": "D", "timestamp": 1700000100, "session_id": "s2"},
    ]))

    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    try:
        summary = memory_migrate.migrate(
            conn, project_name="demo", storage_base=storage, archive=False,
        )
    finally:
        conn.close()
    assert summary.decisions_imported == 2

    conn = memory_db.connect(db_path)
    try:
        rows = list(conn.execute(
            "SELECT decision, source FROM decisions ORDER BY created_at_epoch"
        ))
    finally:
        conn.close()
    assert len(rows) == 2
    assert all(r["source"] == "migrated" for r in rows)


def test_migrate_no_legacy_dirs_returns_empty_summary(tmp_path: Path):
    storage = tmp_path / "storage"
    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    try:
        summary = memory_migrate.migrate(
            conn, project_name="ghost", storage_base=storage, archive=False,
        )
    finally:
        conn.close()
    assert summary.files_imported == 0
    # candidate_session_dirs returns paths that may not exist; only existing
    # ones go in `sources_scanned`.
    assert summary.sources_scanned == []
