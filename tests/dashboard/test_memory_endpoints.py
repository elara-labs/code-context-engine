"""Tests for the memory.db dashboard API endpoints (PR 5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from context_engine.config import Config
from context_engine.dashboard.server import create_app
from context_engine.memory import db as memory_db
from context_engine.utils import project_storage_dir


@pytest.fixture
def client(tmp_path: Path):
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    storage_path = tmp_path / "storage"
    storage_path.mkdir()

    config = Config(
        storage_path=str(storage_path),
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    project_storage = project_storage_dir(config, project_dir)
    project_storage.mkdir(parents=True, exist_ok=True)

    # Minimal manifest so /api/files / /api/status work.
    (project_storage / "manifest.json").write_text(
        json.dumps({"__schema_version": 2, "files": {}, "last_git_sha": None})
    )

    app = create_app(config, project_dir)
    return TestClient(app), project_storage


def _seed_memory_db(project_storage: Path):
    """Open or create memory.db in the project's storage dir; seed sample data."""
    db_path = memory_db.memory_db_path(project_storage)
    conn = memory_db.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "status, prompt_count, rollup_summary) VALUES "
        "('s-recent', 'demo', 1700000200, '2023-11-14T22:16:40', 'completed', "
        "2, 'A two-turn rollup summary')"
    )
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "status, prompt_count) VALUES "
        "('s-old', 'demo', 1700000000, '2023-11-14T22:13:20', 'completed', 0)"
    )
    conn.execute(
        "INSERT INTO turn_summaries (session_id, prompt_number, summary, tier, "
        "created_at_epoch) VALUES ('s-recent', 1, 'first turn KEY', "
        "'extractive', 1700000210)"
    )
    conn.execute(
        "INSERT INTO turn_summaries (session_id, prompt_number, summary, tier, "
        "created_at_epoch) VALUES ('s-recent', 2, 'second turn KEY again', "
        "'extractive', 1700000220)"
    )
    conn.execute(
        "INSERT INTO decisions (session_id, decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('s-recent', 'Use bge-small for KEY recall', 'Already loaded', "
        "'manual', 1700000230, '2023-11-14T22:17:10')"
    )
    conn.execute(
        "INSERT INTO decisions (session_id, decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "(NULL, 'Migrated decision X', 'old reason', 'migrated', 1699000000, "
        "'2023-11-03T11:46:40')"
    )
    conn.commit()
    conn.close()


def test_memory_sessions_returns_recent_first(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/sessions")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ["s-recent", "s-old"]
    assert rows[0]["rollup_summary"] == "A two-turn rollup summary"


def test_memory_sessions_empty_when_no_db(client):
    c, _ = client
    resp = c.get("/api/memory/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_memory_session_timeline_returns_session_and_turns(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/sessions/s-recent/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["id"] == "s-recent"
    assert data["session"]["rollup_summary"] == "A two-turn rollup summary"
    assert len(data["turns"]) == 2
    assert data["turns"][0]["prompt_number"] == 1
    assert data["turns"][1]["prompt_number"] == 2
    assert data["turns"][0]["tier"] == "extractive"


def test_memory_session_timeline_unknown_returns_null(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/sessions/no-such/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"session": None, "turns": []}


def test_memory_decisions_search_fts(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/decisions?q=bge-small")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_memory_decisions_search_filters_by_source(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/decisions?source=migrated")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["source"] == "migrated"


def test_memory_decisions_no_query_returns_all_recent(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/decisions")
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["source"] == "manual"  # most-recent first
    assert rows[1]["source"] == "migrated"


def test_memory_decisions_combined_filters(client):
    c, storage = client
    _seed_memory_db(storage)
    resp = c.get("/api/memory/decisions?q=bge-small&source=migrated")
    rows = resp.json()
    assert rows == []


def test_memory_decisions_handles_special_chars(client):
    """User input with FTS5 metacharacters is treated literally (phrase quoted)."""
    c, storage = client
    _seed_memory_db(storage)
    # Hyphen would parse as an FTS5 operator without phrase-quoting.
    resp = c.get("/api/memory/decisions?q=bge-small")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert "bge-small" in rows[0]["decision"]


def test_memory_decisions_endpoint_expands_compressed_storage(client):
    """The dashboard reads stored bytes that went through grammar.compress.
    Render must expand abbreviations so users see "because" not "b/c"."""
    c, storage = client
    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES "
        "('Use prod config b/c env drift', 'Avoids dev/prod mismatch', "
        "'manual', 1700000000, '2023-11-14T22:13:20')"
    )
    conn.commit()
    conn.close()

    rows = c.get("/api/memory/decisions").json()
    target = next((r for r in rows if "prod" in r["decision"]), None)
    assert target is not None, rows
    # b/c → because, prod → production
    assert "because" in target["decision"]
    assert "production" in target["decision"]


def test_memory_session_timeline_expands_summary_and_rollup(client):
    """rollup_summary and per-turn summary are both stored compressed;
    timeline endpoint must run them through expand."""
    c, storage = client
    db_path = memory_db.memory_db_path(storage)
    conn = memory_db.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "status, prompt_count, rollup_summary, rollup_summary_at_epoch) "
        "VALUES ('s-comp', 'demo', 1700000000, '2023-11-14T22:13:20', "
        "'completed', 1, 'Picked auth flow b/c mesh keys', 1700000300)"
    )
    conn.execute(
        "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
        "tier, created_at_epoch) VALUES ('s-comp', 1, "
        "'Discussed perf w/ team', 'extractive', 1700000200)"
    )
    conn.commit()
    conn.close()

    body = c.get("/api/memory/sessions/s-comp/timeline").json()
    assert "because" in body["session"]["rollup_summary"]
    assert "performance" in body["turns"][0]["summary"]
    assert "with" in body["turns"][0]["summary"]
