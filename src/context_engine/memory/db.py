"""Per-project memory.db bootstrap and connection helper.

Schema version 3 — see docs/specs/2026-04-28-memory-claude-mem-parity-design.md.

v1: core memory tables + FTS5 virtual tables for lexical recall.
v2: adds sqlite-vec `vec0` virtual tables for semantic recall on
    decisions and turn_summaries (the two surfaces session_recall reads).
v3: adds `savings_log` — append-only ledger of token savings per bucket
    (retrieval, chunk_compression, output_compression, memory_recall,
    grammar, turn_summarization, progressive_disclosure). Feeds the
    `cce savings` per-bucket breakdown.

Idempotent: opening an existing db is a no-op; opening an empty file creates
the schema and stamps version=3. Older dbs are upgraded in place additively.
"""
from __future__ import annotations

import logging
import sqlite3
import struct
import time
from pathlib import Path

log = logging.getLogger(__name__)

CURRENT_VERSION = 3

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


_SCHEMA_V3 = [
    # Append-only savings ledger. Each row is one accounting event from a
    # bucket (retrieval, grammar, memory_recall, etc.) with baseline (what
    # would have been spent without CCE) and served (what was actually
    # spent). `meta` carries bucket-specific context as JSON — e.g.
    # {"level": "max"} for output_compression.
    """
    CREATE TABLE IF NOT EXISTS savings_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      bucket TEXT NOT NULL,
      baseline INTEGER NOT NULL,
      served INTEGER NOT NULL,
      meta TEXT,
      ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_savings_bucket_ts ON savings_log(bucket, ts)",
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


def _vec_trigger_stmts() -> list[str]:
    """Cleanup triggers — when a source row is deleted, drop its vec row too.

    Without these, FK cascades / explicit deletes would leak rows in the vec
    tables (FTS gets cleaned up by its own existing triggers).
    """
    return [
        """
        CREATE TRIGGER IF NOT EXISTS decisions_vec_ad AFTER DELETE ON decisions BEGIN
          DELETE FROM decisions_vec WHERE rowid = old.id;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS turn_summaries_vec_ad AFTER DELETE ON turn_summaries BEGIN
          DELETE FROM turn_summaries_vec WHERE rowid = old.id;
        END
        """,
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
    except AttributeError:
        log.warning(
            "sqlite-vec load failed; semantic recall disabled. "
            "Python was compiled without SQLite extension support. "
            "Reinstall CCE with Homebrew Python: "
            "uv tool install --python /opt/homebrew/bin/python3 --force code-context-engine"
        )
        return False
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
    # Give concurrent writers (hooks + auto-prune) up to 5 seconds to acquire
    # the write lock instead of failing immediately with "database is locked".
    conn.execute("PRAGMA busy_timeout = 5000")
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
                for stmt in _vec_trigger_stmts():
                    cur.execute(stmt)
            for stmt in _SCHEMA_V3:
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

    # Existing db — apply additive upgrades up to CURRENT_VERSION.
    # v1 → v2: add vec tables + cleanup triggers (needs sqlite-vec).
    # v2 → v3: add savings_log (no extension dependency).
    # If sqlite-vec is unavailable we can still apply v3, but we don't
    # stamp the version row so a future connection with vec loaded will
    # complete the v1 → v2 step.
    current = schema_version(conn)
    if current >= CURRENT_VERSION:
        return
    cur.execute("BEGIN")
    try:
        if current < 2 and has_vec:
            for stmt in _vec_table_stmts(_VEC_DIM):
                cur.execute(stmt)
            for stmt in _vec_trigger_stmts():
                cur.execute(stmt)
        if current < 3:
            for stmt in _SCHEMA_V3:
                cur.execute(stmt)
        if current < 2 and not has_vec:
            # No version bump — vec step still pending.
            conn.commit()
            return
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
    # Safe: table name is an internal constant, never from user input.
    try:
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))  # nosemgrep: sqlalchemy-execute-raw-query
        conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
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
    """Embed any source rows that don't yet have a vec entry.

    Idempotent and incremental — runs at MCP startup so:
      - Projects upgrading from v1 get their full backlog embedded.
      - Decisions imported by `cce sessions migrate` (which runs without
        an embedder) pick up semantic recall on next `cce serve`.
      - Sessions captured while the vec extension was unavailable get
        retroactively indexed once it loads.

    The previous "only run if the vec table is empty" guard meant a single
    manually-recorded decision permanently disabled all future backfill,
    so any subsequent migrated rows were invisible to semantic recall.
    """
    counts = {"decisions": 0, "turn_summaries": 0}
    if not has_vec_tables(conn):
        return counts
    for row in conn.execute(
        "SELECT d.id, d.decision, d.reason FROM decisions d "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM decisions_vec v WHERE v.rowid = d.id"
        ")"
    ):
        record_decision_vec(
            conn, embedder,
            decision_id=row["id"],
            decision=row["decision"] or "",
            reason=row["reason"] or "",
        )
        counts["decisions"] += 1
    for row in conn.execute(
        "SELECT t.id, t.summary FROM turn_summaries t "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM turn_summaries_vec v WHERE v.rowid = t.id"
        ")"
    ):
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


# Maximum L2 distance accepted from sqlite-vec MATCH. bge-small produces
# unit-normalised vectors, so L2² = 2·(1 - cosine_sim). Empirically bge-small's
# *noise floor* on short English text is around cosine_sim ≈ 0.50 — random
# unrelated queries land there. So we set the threshold at cosine_sim ≥ 0.58
# (L2 ≤ √(2·0.42) ≈ 0.917) to keep paraphrases ("risk management" ↔ "Risk
# limit at 2% per trade", measured at 0.638) while rejecting "how is the
# weather today" (max 0.535 against the same corpus).
_VEC_MAX_DISTANCE = 0.92


def search_decisions_vec(
    conn, embedder, topic: str, *, k: int = 20,
    max_distance: float = _VEC_MAX_DISTANCE,
) -> list[int]:
    """Return decision rowids ranked by semantic similarity to `topic`,
    filtered by `max_distance` (default `_VEC_MAX_DISTANCE`). Empty list
    on failure or no good match. Tests can pass a permissive max_distance
    to use a deterministic fake embedder whose vectors don't satisfy
    bge-small-tuned thresholds.
    """
    if not has_vec_tables(conn) or not topic.strip():
        return []
    try:
        vec = embedder.embed_query(topic)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid, distance FROM decisions_vec "
            "WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (_serialize_vec(vec), k),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.debug("decisions_vec search failed: %s", exc)
        return []
    return [r["rowid"] for r in rows if r["distance"] <= max_distance]


def search_turn_summaries_vec(
    conn, embedder, topic: str, *, k: int = 20,
    max_distance: float = _VEC_MAX_DISTANCE,
) -> list[int]:
    """Return turn_summary rowids ranked by semantic similarity, distance-filtered."""
    if not has_vec_tables(conn) or not topic.strip():
        return []
    try:
        vec = embedder.embed_query(topic)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid, distance FROM turn_summaries_vec "
            "WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (_serialize_vec(vec), k),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.debug("turn_summaries_vec search failed: %s", exc)
        return []
    return [r["rowid"] for r in rows if r["distance"] <= max_distance]


# ── PII redaction toggle ────────────────────────────────────────────────────
# Set at process start by the MCP server / CLI from `Config.memory_redact_pii`.
# Defaults to True so a misconfigured caller errs on the side of redaction.
# Stored as module-level state because the write helpers are called from
# many entry points (mcp_server, compressor, migrate) without easy access
# to the live Config — and the value never changes within a process.
_PII_REDACTION_ENABLED = True


def set_pii_redaction(enabled: bool) -> None:
    """Toggle PII scrubbing globally for memory.db writes."""
    global _PII_REDACTION_ENABLED
    _PII_REDACTION_ENABLED = bool(enabled)


def scrub_pii(text: str) -> str:
    """Apply PII redaction (emails / IPs / SSNs / cards / phones) when
    enabled. Returns the original text unchanged when off, or the input
    is empty. Centralised so every memory.db write goes through one
    place — wrapping each INSERT site directly was error-prone.
    """
    if not text or not _PII_REDACTION_ENABLED:
        return text
    from context_engine.indexer.secrets import redact_pii as _redact_pii
    out, fired = _redact_pii(text)
    if fired:
        log.debug("memory: scrubbed %s from incoming text", ",".join(sorted(set(fired))))
    return out


# ── Savings ledger ──────────────────────────────────────────────────────────

# Canonical bucket names — keep in sync with the renderer in cli.py.
BUCKETS = (
    "retrieval",
    "chunk_compression",
    "output_compression",
    "memory_recall",
    "grammar",
    "turn_summarization",
    "progressive_disclosure",
)


def record_savings(
    conn: sqlite3.Connection,
    *,
    bucket: str,
    baseline: int,
    served: int,
    meta: dict | None = None,
) -> None:
    """Append one savings event. Best-effort — swallows write errors so a
    misbehaving instrumentation point can never break a tool response.
    """
    if bucket not in BUCKETS:
        log.warning("record_savings: unknown bucket %r — skipping", bucket)
        return
    try:
        import json as _json
        conn.execute(
            "INSERT INTO savings_log (bucket, baseline, served, meta, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                bucket,
                int(baseline),
                int(served),
                _json.dumps(meta) if meta else None,
                int(time.time()),
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        log.debug("record_savings(%s) failed: %s", bucket, exc)


def aggregate_savings(conn: sqlite3.Connection) -> dict[str, dict]:
    """Roll up `savings_log` into per-bucket totals for the savings report.

    Returns a dict keyed by bucket name with `{baseline, served, calls}`.
    Missing buckets are filled with zeros so the renderer can iterate
    over the canonical BUCKETS tuple unconditionally.
    """
    out = {b: {"baseline": 0, "served": 0, "calls": 0} for b in BUCKETS}
    try:
        rows = conn.execute(
            "SELECT bucket, SUM(baseline) AS baseline, SUM(served) AS served, "
            "COUNT(*) AS calls FROM savings_log GROUP BY bucket"
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        b = r["bucket"]
        if b in out:
            out[b] = {
                "baseline": int(r["baseline"] or 0),
                "served": int(r["served"] or 0),
                "calls": int(r["calls"] or 0),
            }
    return out


def aggregate_output_compression_levels(conn: sqlite3.Connection) -> dict[str, int]:
    """Histogram of output_compression levels seen in the ledger.

    Reads `meta.level` from each output_compression row. Used by the
    renderer to show "max=21 calls, standard=4 calls" alongside the
    estimated savings.
    """
    out: dict[str, int] = {}
    try:
        import json as _json
        rows = conn.execute(
            "SELECT meta FROM savings_log WHERE bucket = 'output_compression'"
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        if not r["meta"]:
            continue
        try:
            meta = _json.loads(r["meta"])
            level = meta.get("level")
            if level:
                out[level] = out.get(level, 0) + 1
        except (ValueError, TypeError):
            continue
    return out


# ── Retention ───────────────────────────────────────────────────────────────

def prune_old_payloads(conn, *, days: int = 30) -> dict[str, int]:
    """NULL-out raw_input/raw_output on tool_event_payloads older than `days`.

    The summary lives on `tool_events.summary` (or as a turn_summary), so
    callers can still get the gist of an aged-out event — the raw payload
    is the expensive part and the only thing that grows unbounded. The
    `session_event` MCP tool already has a "raw payload aged out of the
    retention window" branch; this is what makes that branch reachable.

    Returns counts: {"payloads_pruned", "bytes_freed_estimate"}.
    """
    cutoff = conn.execute(
        "SELECT strftime('%s','now') - ? * 86400 AS cutoff", (days,),
    ).fetchone()["cutoff"]
    # tool_event_payloads has no created_at of its own — it inherits time
    # from tool_events. Find payloads referenced only by old events, where
    # the raw fields aren't already nulled.
    # raw_input has NOT NULL in v1 schema, so we use '' as the aged-out
    # sentinel for it; raw_output is already nullable. Callers detect aged
    # rows via "not raw_input and raw_output is None".
    rows = conn.execute(
        "SELECT p.id, p.size_bytes "
        "FROM tool_event_payloads p "
        "WHERE p.raw_input != '' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM tool_events te "
        "  WHERE te.payload_id = p.id "
        "  AND te.created_at_epoch >= ?"
        ")",
        (cutoff,),
    ).fetchall()
    if not rows:
        return {"payloads_pruned": 0, "bytes_freed_estimate": 0}
    ids = [r["id"] for r in rows]
    bytes_freed = sum(r["size_bytes"] or 0 for r in rows)
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE tool_event_payloads "
        f"SET raw_input = '', raw_output = NULL, size_bytes = 0 "
        f"WHERE id IN ({placeholders})",
        tuple(ids),
    )
    conn.commit()
    log.info("pruned %d tool payloads older than %dd (~%d bytes freed)",
             len(ids), days, bytes_freed)
    return {"payloads_pruned": len(ids), "bytes_freed_estimate": bytes_freed}


# ── Row-level retention for memory tables ──────────────────────────────────
# Defaults err on the generous side — a 6-month-old decision can still be
# valuable, but unbounded growth eventually drops recall quality. Override
# via config (memory_decision_retention_days, etc.) or by passing different
# values to prune_old_rows() in tests.
DEFAULT_TURN_RETENTION_DAYS = 180        # 6 months
DEFAULT_DECISION_RETENTION_DAYS = 365    # 1 year — decisions tend to be load-bearing
DEFAULT_CODE_AREA_RETENTION_DAYS = 180   # 6 months
DEFAULT_AUTO_ARCHIVE = True              # write rows to a json file before delete


def prune_old_rows(
    conn: sqlite3.Connection,
    *,
    storage_base,
    turn_days: int = DEFAULT_TURN_RETENTION_DAYS,
    decision_days: int = DEFAULT_DECISION_RETENTION_DAYS,
    code_area_days: int = DEFAULT_CODE_AREA_RETENTION_DAYS,
    archive: bool = DEFAULT_AUTO_ARCHIVE,
) -> dict[str, int]:
    """Delete decisions / turn_summaries / code_areas older than the
    configured TTLs. Optionally archives deleted rows to a JSON file
    under `storage_base/archives/` before deletion, so power users can
    grep history that's no longer indexed.

    Returns counts: {"decisions_pruned", "turns_pruned", "code_areas_pruned"}.

    Recall guard: rows referenced by a `decisions_vec` / `turn_summaries_vec`
    entry are NOT skipped — vec triggers (see `_vec_trigger_stmts`) cascade
    the delete cleanly. The bigger risk is deleting a row that just
    surfaced in a recall hit, but we don't track per-row recall timestamps;
    the long retention defaults make that vanishingly unlikely.
    """
    import json as _json
    from pathlib import Path as _Path
    counts = {"decisions_pruned": 0, "turns_pruned": 0, "code_areas_pruned": 0}
    cutoffs = {
        "turn_summaries": int(time.time()) - turn_days * 86400,
        "decisions":      int(time.time()) - decision_days * 86400,
        "code_areas":     int(time.time()) - code_area_days * 86400,
    }

    archive_dir = _Path(storage_base) / "archives"
    if archive:
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        archive_path = archive_dir / f"pruned-{ts}.json"
    else:
        archive_path = None

    archived: dict[str, list[dict]] = {}

    # Safe: table and col_list are internal constants, never from user input.
    def _harvest_and_delete(table: str, columns: list[str], cutoff: int) -> int:
        col_list = ", ".join(columns)
        rows = conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
            f"SELECT {col_list} FROM {table} WHERE created_at_epoch < ?",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0
        if archive:
            archived[table] = [dict(r) for r in rows]
        conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
            f"DELETE FROM {table} WHERE created_at_epoch < ?",
            (cutoff,),
        )
        return len(rows)

    counts["turns_pruned"] = _harvest_and_delete(
        "turn_summaries",
        ["id", "session_id", "prompt_number", "summary", "tier", "created_at_epoch"],
        cutoffs["turn_summaries"],
    )
    counts["decisions_pruned"] = _harvest_and_delete(
        "decisions",
        ["id", "session_id", "decision", "reason", "source",
         "created_at_epoch", "created_at"],
        cutoffs["decisions"],
    )
    counts["code_areas_pruned"] = _harvest_and_delete(
        "code_areas",
        ["id", "session_id", "file_path", "description", "source", "created_at_epoch"],
        cutoffs["code_areas"],
    )
    conn.commit()

    if archive and archived and archive_path is not None:
        try:
            archive_path.write_text(_json.dumps(archived, indent=2, default=str))
            log.info("memory: archived pruned rows to %s", archive_path)
        except OSError as exc:
            log.warning("memory: archive write failed (%s); rows still deleted", exc)

    total = sum(counts.values())
    if total:
        log.info(
            "memory: pruned %d row(s) across decisions/turns/code_areas",
            total,
        )
    return counts


# Defaults exposed so tests can inject smaller values without monkey-patching.
AUTO_PRUNE_INITIAL_DELAY_SECONDS = 120  # stagger past vec backfill / compress
AUTO_PRUNE_INTERVAL_SECONDS = 86_400  # one pass per day


async def auto_prune_loop(
    storage_base,
    *,
    days: int = 30,
    initial_delay: float = AUTO_PRUNE_INITIAL_DELAY_SECONDS,
    interval: float = AUTO_PRUNE_INTERVAL_SECONDS,
    stop_event=None,
) -> None:
    """Background task: periodically age out old raw tool payloads.

    Runs forever, sleeping `interval` between passes. Each pass opens its
    own SQLite connection (so we don't pin a long-lived conn across the
    day-long sleep) and dispatches the actual prune to a worker thread.
    Cancellable via `stop_event` (preferred) or `task.cancel()`.

    Extracted from `cli._run_serve` so it's testable without spinning up
    the whole MCP server. Exposed defaults for `initial_delay` and
    `interval` let tests run iterations in milliseconds.
    """
    import asyncio
    from pathlib import Path
    db_path = memory_db_path(Path(storage_base))

    if initial_delay > 0:
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
                return  # stop_event fired during stagger
            else:
                await asyncio.sleep(initial_delay)
        except asyncio.TimeoutError:
            pass  # normal: timeout means stagger elapsed without stop

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            def _do_prune():
                conn = connect(db_path)
                try:
                    payload = prune_old_payloads(conn, days=days)
                    rows = prune_old_rows(conn, storage_base=Path(storage_base))
                    return {**payload, **rows}
                finally:
                    conn.close()
            out = await asyncio.to_thread(_do_prune)
            if out.get("payloads_pruned"):
                log.info(
                    "auto-prune: aged out %d raw payloads (~%d KB)",
                    out["payloads_pruned"],
                    out["bytes_freed_estimate"] // 1024,
                )
            row_total = (
                out.get("decisions_pruned", 0)
                + out.get("turns_pruned", 0)
                + out.get("code_areas_pruned", 0)
            )
            if row_total:
                log.info(
                    "auto-prune: removed %d expired memory rows "
                    "(decisions=%d turns=%d code_areas=%d)",
                    row_total,
                    out.get("decisions_pruned", 0),
                    out.get("turns_pruned", 0),
                    out.get("code_areas_pruned", 0),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-prune iteration failed; backing off")

        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            else:
                await asyncio.sleep(interval)
        except asyncio.TimeoutError:
            pass
