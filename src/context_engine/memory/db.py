"""Per-project memory.db bootstrap and connection helper.

Schema version 2 — see docs/specs/2026-04-28-memory-claude-mem-parity-design.md.

v1: core memory tables + FTS5 virtual tables for lexical recall.
v2: adds sqlite-vec `vec0` virtual tables for semantic recall on
    decisions and turn_summaries (the two surfaces session_recall reads).

Idempotent: opening an existing db is a no-op; opening an empty file creates
the schema and stamps version=2. Existing v1 dbs are upgraded in place by
adding the empty vec tables; `backfill_vec_tables(conn, embedder)` populates
them lazily once an embedder is available.
"""
from __future__ import annotations

import logging
import sqlite3
import struct
from pathlib import Path

log = logging.getLogger(__name__)

CURRENT_VERSION = 2

# bge-small-en-v1.5 — the default embedder used everywhere else in cce.
# If the project's embedder swaps to a different model, vec tables are
# rebuilt on first access (see `_ensure_vec_dim`).
_VEC_DIM = 384

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


def _vec_table_stmts(dim: int) -> list[str]:
    """vec0 virtual tables for the two surfaces session_recall actually reads.

    We don't add vec for prompts (too noisy — the user's raw text is rarely
    the right semantic anchor) or code_areas (already keyed by file path,
    which a substring filter handles well enough).
    """
    return [
        f"CREATE VIRTUAL TABLE IF NOT EXISTS decisions_vec USING vec0(embedding float[{dim}])",
        f"CREATE VIRTUAL TABLE IF NOT EXISTS turn_summaries_vec USING vec0(embedding float[{dim}])",
    ]


def _serialize_vec(vec) -> bytes:
    """Pack a float vector into bytes for sqlite-vec."""
    v = list(vec) if not isinstance(vec, list) else vec
    return struct.pack(f"{len(v)}f", *v)


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension. Returns False if unavailable.

    A False return means the db opens fine but the v2 vec tables can't be
    created or queried. Callers that need semantic recall should treat this
    as a soft degradation and fall back to FTS5-only.
    """
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:
        log.warning("sqlite-vec load failed; semantic recall disabled: %s", exc)
        return False


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the per-project memory.db at `db_path`.

    Bootstraps the schema if the file is empty, upgrades v1 → v2 in place,
    and loads the sqlite-vec extension. Idempotent.
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
    has_vec = _try_load_vec(conn)
    _ensure_schema(conn, has_vec=has_vec)
    return conn


def _ensure_schema(conn: sqlite3.Connection, *, has_vec: bool) -> None:
    cur = conn.cursor()
    bootstrap_row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'"
    ).fetchone()

    if bootstrap_row is None:
        cur.execute("BEGIN")
        try:
            for stmt in _SCHEMA_V1:
                cur.execute(stmt)
            if has_vec:
                for stmt in _vec_table_stmts(_VEC_DIM):
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
        return

    # Existing db — upgrade v1 → v2 by adding the vec tables. Backfill is
    # deferred: callers with an embedder should run `backfill_vec_tables`.
    current = schema_version(conn)
    if current >= CURRENT_VERSION or not has_vec:
        return
    cur.execute("BEGIN")
    try:
        for stmt in _vec_table_stmts(_VEC_DIM):
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


# ── Vector helpers ──────────────────────────────────────────────────────────

def has_vec_tables(conn: sqlite3.Connection) -> bool:
    """True iff the v2 vec tables exist (extension loaded + schema upgraded)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('decisions_vec','turn_summaries_vec')"
    ).fetchall()
    return len(rows) == 2


def _decision_vec_text(decision: str, reason: str) -> str:
    if decision and reason:
        return f"{decision} — {reason}"
    return decision or reason or ""


def _write_vec_row(conn, table: str, rowid: int, vec) -> None:
    """Best-effort vec write. Swallows dim mismatches so a swapped embedder
    doesn't break inserts on the source table — the failed row simply won't
    be semantically searchable until the vec tables are rebuilt.
    """
    try:
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
        conn.execute(
            f"INSERT INTO {table}(rowid, embedding) VALUES (?, ?)",
            (rowid, _serialize_vec(vec)),
        )
    except sqlite3.OperationalError as exc:
        log.debug("vec write skipped on %s rowid=%s: %s", table, rowid, exc)


def record_decision_vec(conn, embedder, *, decision_id: int, decision: str, reason: str) -> None:
    """Embed a decision row and write it to decisions_vec. Idempotent on rowid."""
    if not has_vec_tables(conn):
        return
    text = _decision_vec_text(decision, reason)
    if not text.strip():
        return
    try:
        vec = embedder.embed_query(text)
    except Exception:
        log.exception("embedder failed for decision %s", decision_id)
        return
    _write_vec_row(conn, "decisions_vec", decision_id, vec)


def record_turn_summary_vec(conn, embedder, *, turn_id: int, summary: str) -> None:
    """Embed a turn summary and write it to turn_summaries_vec."""
    if not has_vec_tables(conn):
        return
    if not summary.strip():
        return
    try:
        vec = embedder.embed_query(summary)
    except Exception:
        log.exception("embedder failed for turn_summary %s", turn_id)
        return
    _write_vec_row(conn, "turn_summaries_vec", turn_id, vec)


def backfill_vec_tables(conn, embedder) -> dict[str, int]:
    """Populate vec tables from existing rows when they're empty.

    Called once at MCP server startup after an embedder is available, so a
    project that ran on v1 picks up semantic recall on the next launch.
    Returns counts per surface for logging.
    """
    counts = {"decisions": 0, "turn_summaries": 0}
    if not has_vec_tables(conn):
        return counts
    if conn.execute("SELECT 1 FROM decisions_vec LIMIT 1").fetchone() is None:
        for row in conn.execute("SELECT id, decision, reason FROM decisions"):
            record_decision_vec(
                conn, embedder,
                decision_id=row["id"],
                decision=row["decision"] or "",
                reason=row["reason"] or "",
            )
            counts["decisions"] += 1
    if conn.execute("SELECT 1 FROM turn_summaries_vec LIMIT 1").fetchone() is None:
        for row in conn.execute("SELECT id, summary FROM turn_summaries"):
            record_turn_summary_vec(
                conn, embedder,
                turn_id=row["id"],
                summary=row["summary"] or "",
            )
            counts["turn_summaries"] += 1
    if counts["decisions"] or counts["turn_summaries"]:
        conn.commit()
        log.info("vec backfill: decisions=%d turn_summaries=%d",
                 counts["decisions"], counts["turn_summaries"])
    return counts


def search_decisions_vec(conn, embedder, topic: str, *, k: int = 20) -> list[int]:
    """Return decision rowids ranked by semantic similarity to `topic`. Empty list on failure."""
    if not has_vec_tables(conn) or not topic.strip():
        return []
    try:
        vec = embedder.embed_query(topic)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid FROM decisions_vec WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (_serialize_vec(vec), k),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.debug("decisions_vec search failed: %s", exc)
        return []
    return [r["rowid"] for r in rows]


def search_turn_summaries_vec(conn, embedder, topic: str, *, k: int = 20) -> list[int]:
    """Return turn_summary rowids ranked by semantic similarity. Empty on failure."""
    if not has_vec_tables(conn) or not topic.strip():
        return []
    try:
        vec = embedder.embed_query(topic)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid FROM turn_summaries_vec WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (_serialize_vec(vec), k),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.debug("turn_summaries_vec search failed: %s", exc)
        return []
    return [r["rowid"] for r in rows]
