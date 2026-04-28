"""Import legacy per-session JSON files into the new memory.db.

Walks each project's `sessions/` directory (and the legacy
`~/.claude-context-engine/projects/<name>/sessions/` path), parses each
*.json, imports decisions and code_areas with `source='migrated'`, then
archives the consumed files into `migrated.zip` and removes them.

Idempotent — `migrated_files` tracks what has already been imported so a
rerun is a no-op.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_DECISIONS_LOG_NAME = "decisions_log.json"


@dataclass
class MigrationSummary:
    decisions_imported: int = 0
    code_areas_imported: int = 0
    files_imported: int = 0
    files_archived: int = 0
    files_skipped: int = 0
    sources_scanned: list[str] = field(default_factory=list)


def candidate_session_dirs(project_name: str, primary_storage_base: Path) -> list[Path]:
    """Return every directory we should scan for legacy session JSON.

    Currently:
      - <storage_base>/sessions/         (current path)
      - ~/.claude-context-engine/projects/<name>/sessions/  (pre-rebrand)
    """
    legacy_root = Path.home() / ".claude-context-engine" / "projects" / project_name / "sessions"
    return [
        Path(primary_storage_base) / "sessions",
        legacy_root,
    ]


def migrate(
    conn: sqlite3.Connection,
    project_name: str,
    storage_base: str | Path,
    *,
    archive: bool = True,
) -> MigrationSummary:
    """Import all legacy JSON sessions for `project_name` into the open db.

    `storage_base` is the per-project storage directory (e.g.
    ~/.cce/projects/<name>). `archive=True` zips and deletes consumed
    JSONs after successful import; pass False from tests that want to
    re-read the source files.
    """
    storage_base = Path(storage_base)
    summary = MigrationSummary()

    for sessions_dir in candidate_session_dirs(project_name, storage_base):
        if not sessions_dir.exists():
            continue
        summary.sources_scanned.append(str(sessions_dir))

        json_files = sorted(sessions_dir.glob("*.json"))
        consumed: list[Path] = []
        decisions_added = 0
        code_areas_added = 0
        for f in json_files:
            if _already_imported(conn, f):
                summary.files_skipped += 1
                continue
            try:
                imported = _import_one(conn, f)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Skipping unreadable session file %s: %s", f, exc)
                continue
            decisions_added += imported.decisions
            code_areas_added += imported.code_areas
            consumed.append(f)

        if not consumed:
            continue

        # Archive *before* marking imported. If zip-write fails we roll back
        # the directory's inserts so a rerun retries cleanly — otherwise
        # files would be permanently flagged imported but never archived.
        if archive:
            try:
                archived = _archive_and_remove(sessions_dir, consumed)
            except OSError as exc:
                log.error(
                    "Archive failed for %s: %s — rolling back imports", sessions_dir, exc
                )
                conn.rollback()
                continue
            summary.files_archived += archived

        for f in consumed:
            _mark_imported(conn, f)
        conn.commit()
        summary.decisions_imported += decisions_added
        summary.code_areas_imported += code_areas_added
        summary.files_imported += len(consumed)

    return summary


@dataclass
class _ImportCounts:
    decisions: int = 0
    code_areas: int = 0


def _import_one(conn: sqlite3.Connection, source: Path) -> _ImportCounts:
    """Import a single legacy JSON file. Returns counts of imported rows."""
    counts = _ImportCounts()
    data = json.loads(source.read_text())

    # decisions_log.json is a top-level list of decision dicts, not a session.
    if source.name == _DECISIONS_LOG_NAME and isinstance(data, list):
        # Memoise per-session existence checks within this archive — the same
        # session_id often appears across many entries.
        exists_cache: dict[str, bool] = {}
        for d in data:
            sid = d.get("session_id")
            if sid is not None and sid not in exists_cache:
                exists_cache[sid] = _session_exists(conn, sid)
            _insert_decision(
                conn,
                session_id=sid if sid is not None and exists_cache.get(sid) else None,
                decision=d.get("decision", ""),
                reason=d.get("reason", ""),
                timestamp=d.get("timestamp"),
            )
            counts.decisions += 1
        return counts

    # Per-session JSON: {"id", "decisions": [...], "code_areas": [...], ...}
    if not isinstance(data, dict):
        return counts

    session_id = data.get("id")
    # session_id is constant for the rest of this file — resolve once.
    fk_session_id = session_id if _session_exists(conn, session_id) else None
    for d in data.get("decisions", []) or []:
        _insert_decision(
            conn,
            session_id=fk_session_id,
            decision=d.get("decision", ""),
            reason=d.get("reason", ""),
            timestamp=d.get("timestamp"),
        )
        counts.decisions += 1
    for c in data.get("code_areas", []) or []:
        _insert_code_area(
            conn,
            session_id=fk_session_id,
            file_path=c.get("file_path", ""),
            description=c.get("description", ""),
            timestamp=c.get("timestamp"),
        )
        counts.code_areas += 1
    return counts


def _insert_decision(conn, *, session_id, decision, reason, timestamp):
    # Use `is not None` so legacy rows with an explicit 0/0.0 timestamp keep
    # their original ordering instead of being stamped to "now".
    epoch = int(timestamp) if timestamp is not None else int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))
    conn.execute(
        "INSERT INTO decisions (session_id, decision, reason, source, "
        "created_at_epoch, created_at) VALUES (?, ?, ?, 'migrated', ?, ?)",
        (session_id, decision, reason, epoch, iso),
    )


def _insert_code_area(conn, *, session_id, file_path, description, timestamp):
    epoch = int(timestamp) if timestamp is not None else int(time.time())
    conn.execute(
        "INSERT INTO code_areas (session_id, file_path, description, source, "
        "created_at_epoch) VALUES (?, ?, ?, 'migrated', ?)",
        (session_id, file_path, description, epoch),
    )


def _session_exists(conn, session_id) -> bool:
    if not session_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row is not None


def _already_imported(conn, source: Path) -> bool:
    row = conn.execute(
        "SELECT 1 FROM migrated_files WHERE source_path = ?",
        (str(source),),
    ).fetchone()
    return row is not None


def _mark_imported(conn, source: Path) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO migrated_files (source_path, imported_at_epoch) "
        "VALUES (?, ?)",
        (str(source), int(time.time())),
    )


def _archive_and_remove(sessions_dir: Path, files: list[Path]) -> int:
    """Append `files` to `sessions_dir/migrated.zip` and remove the originals.

    Returns the number of files actually written to the zip.
    """
    if not files:
        return 0
    archive_path = sessions_dir / "migrated.zip"
    written = 0
    with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as zf:
        existing = set(zf.namelist())
        for f in files:
            arcname = f.name
            if arcname in existing:
                # Already in the archive from a previous run; just delete.
                pass
            else:
                zf.write(f, arcname=arcname)
                written += 1
    for f in files:
        try:
            f.unlink()
        except OSError as exc:
            log.warning("Could not remove migrated file %s: %s", f, exc)
    return written
