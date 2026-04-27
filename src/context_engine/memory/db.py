"""Per-project memory.db bootstrap and connection helper.

Schema version 1 — see docs/specs/2026-04-28-memory-claude-mem-parity-design.md.

Idempotent: opening an existing db is a no-op; opening an empty file creates
the schema and stamps version=1.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_VERSION = 1

_SCHEMA_V1 = [
    """
    CREATE TABLE sessions (
      id TEXT PRIMARY KEY,
      project TEXT NOT NULL,
      started_at_epoch INTEGER NOT NULL,
      started_at TEXT NOT NULL,
      ended_at_epoch INTEGER,
      ended_at TEXT,
      exit_reason TEXT,
      prompt_count INTEGER DEFAULT 0,
      status TEXT CHECK(status IN ('active','completed','failed')) NOT NULL DEFAULT 'active',
      rollup_summary TEXT,
      rollup_summary_at_epoch INTEGER
    )
    """,
    "CREATE INDEX idx_sessions_started ON sessions(started_at_epoch DESC)",

    """
    CREATE TABLE prompts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      prompt_number INTEGER NOT NULL,
      prompt_text TEXT NOT NULL,
      created_at_epoch INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(session_id, prompt_number)
    )
    """,
    "CREATE INDEX idx_prompts_session ON prompts(session_id, prompt_number)",

    """
    CREATE TABLE tool_event_payloads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      raw_input TEXT NOT NULL,
      raw_output TEXT,
      size_bytes INTEGER NOT NULL
    )
    """,

    """
    CREATE TABLE tool_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      prompt_number INTEGER NOT NULL,
      tool_name TEXT NOT NULL,
      payload_id INTEGER REFERENCES tool_event_payloads(id) ON DELETE SET NULL,
      summary TEXT,
      created_at_epoch INTEGER NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_events_session_turn ON tool_events(session_id, prompt_number)",

    """
    CREATE TABLE turn_summaries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      prompt_number INTEGER NOT NULL,
      summary TEXT NOT NULL,
      tier TEXT NOT NULL,
      created_at_epoch INTEGER NOT NULL,
      UNIQUE(session_id, prompt_number)
    )
    """,

    """
    CREATE TABLE decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
      decision TEXT NOT NULL,
      reason TEXT NOT NULL,
      source TEXT NOT NULL CHECK(source IN ('manual','migrated','auto')) DEFAULT 'manual',
      created_at_epoch INTEGER NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_decisions_created ON decisions(created_at_epoch DESC)",
    "CREATE INDEX idx_decisions_source ON decisions(source)",

    """
    CREATE TABLE code_areas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
      file_path TEXT NOT NULL,
      description TEXT NOT NULL,
      source TEXT NOT NULL CHECK(source IN ('manual','migrated','auto')) DEFAULT 'manual',
      created_at_epoch INTEGER NOT NULL
    )
    """,
    "CREATE INDEX idx_code_areas_file ON code_areas(file_path)",

    """
    CREATE TABLE pending_compressions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL CHECK(kind IN ('turn','session_rollup')),
      session_id TEXT NOT NULL,
      prompt_number INTEGER,
      enqueued_at_epoch INTEGER NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      UNIQUE(kind, session_id, prompt_number)
    )
    """,

    # Tracks files consumed by `cce sessions migrate` so reruns are idempotent.
    """
    CREATE TABLE migrated_files (
      source_path TEXT PRIMARY KEY,
      imported_at_epoch INTEGER NOT NULL
    )
    """,

    # FTS5 virtual tables — search index for session_recall.
    "CREATE VIRTUAL TABLE prompts_fts USING fts5(prompt_text, content='prompts', content_rowid='id')",
    "CREATE VIRTUAL TABLE decisions_fts USING fts5(decision, reason, content='decisions', content_rowid='id')",
    "CREATE VIRTUAL TABLE turn_summaries_fts USING fts5(summary, content='turn_summaries', content_rowid='id')",

    # Triggers keep the FTS shadow tables in sync with their source tables.
    """
    CREATE TRIGGER prompts_ai AFTER INSERT ON prompts BEGIN
      INSERT INTO prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
    END
    """,
    """
    CREATE TRIGGER prompts_ad AFTER DELETE ON prompts BEGIN
      INSERT INTO prompts_fts(prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
    END
    """,
    """
    CREATE TRIGGER prompts_au AFTER UPDATE ON prompts BEGIN
      INSERT INTO prompts_fts(prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
      INSERT INTO prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
    END
    """,

    """
    CREATE TRIGGER decisions_ai AFTER INSERT ON decisions BEGIN
      INSERT INTO decisions_fts(rowid, decision, reason) VALUES (new.id, new.decision, new.reason);
    END
    """,
    """
    CREATE TRIGGER decisions_ad AFTER DELETE ON decisions BEGIN
      INSERT INTO decisions_fts(decisions_fts, rowid, decision, reason) VALUES('delete', old.id, old.decision, old.reason);
    END
    """,
    """
    CREATE TRIGGER decisions_au AFTER UPDATE ON decisions BEGIN
      INSERT INTO decisions_fts(decisions_fts, rowid, decision, reason) VALUES('delete', old.id, old.decision, old.reason);
      INSERT INTO decisions_fts(rowid, decision, reason) VALUES (new.id, new.decision, new.reason);
    END
    """,

    """
    CREATE TRIGGER turn_summaries_ai AFTER INSERT ON turn_summaries BEGIN
      INSERT INTO turn_summaries_fts(rowid, summary) VALUES (new.id, new.summary);
    END
    """,
    """
    CREATE TRIGGER turn_summaries_ad AFTER DELETE ON turn_summaries BEGIN
      INSERT INTO turn_summaries_fts(turn_summaries_fts, rowid, summary) VALUES('delete', old.id, old.summary);
    END
    """,
    """
    CREATE TRIGGER turn_summaries_au AFTER UPDATE ON turn_summaries BEGIN
      INSERT INTO turn_summaries_fts(turn_summaries_fts, rowid, summary) VALUES('delete', old.id, old.summary);
      INSERT INTO turn_summaries_fts(rowid, summary) VALUES (new.id, new.summary);
    END
    """,

    """
    CREATE TABLE schema_versions (
      version INTEGER PRIMARY KEY,
      applied_at_epoch INTEGER NOT NULL
    )
    """,
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the per-project memory.db at `db_path`.

    Bootstraps the schema if the file is empty. Idempotent: re-opening an
    initialised db just returns a configured connection.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Foreign keys must be enabled per-connection in SQLite.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL gives concurrent readers (the dashboard) decent isolation while the
    # MCP server writes; no impact on single-process use.
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # If schema_versions exists, the schema has been bootstrapped at least
    # once. Future migrations would compare CURRENT_VERSION here.
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'"
    ).fetchone()
    if row is not None:
        return
    cur.execute("BEGIN")
    try:
        for stmt in _SCHEMA_V1:
            cur.execute(stmt)
        cur.execute(
            "INSERT INTO schema_versions (version, applied_at_epoch) "
            "VALUES (?, strftime('%s','now'))",
            (CURRENT_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS v FROM schema_versions"
    ).fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def memory_db_path(storage_base: str | Path) -> Path:
    """Canonical location of the memory db inside a project's storage dir."""
    return Path(storage_base) / "memory.db"
