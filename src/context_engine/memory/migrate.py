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

    consumed_per_dir: dict[Path, list[Path]] = {}

    for sessions_dir in candidate_session_dirs(project_name, storage_base):
        if not sessions_dir.exists():
            continue
        summary.sources_scanned.append(str(sessions_dir))

        json_files = sorted(
            f for f in sessions_dir.glob("*.json")
            # decisions_log.json is the consolidated archive — also a valid
            # source of decisions, just in a different shape.
        )
        consumed: list[Path] = []
        for f in json_files:
            if _already_imported(conn, f):
                summary.files_skipped += 1
                continue
            try:
                imported = _import_one(conn, f)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Skipping unreadable session file %s: %s", f, exc)
                continue
            summary.decisions_imported += imported.decisions
            summary.code_areas_imported += imported.code_areas
            summary.files_imported += 1
            _mark_imported(conn, f)
            consumed.append(f)
        if consumed:
            consumed_per_dir[sessions_dir] = consumed

    conn.commit()

    if archive and consumed_per_dir:
        for sessions_dir, files in consumed_per_dir.items():
            archived = _archive_and_remove(sessions_dir, files)
            summary.files_archived += archived

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
        for d in data:
            _insert_decision(
                conn,
                session_id=None,
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
    # We do not synthesise a sessions row from migrated data — the legacy
    # JSON files predate the new sessions schema and miss timestamps in a
    # form we can trust. Importing decisions with session_id=None is the
    # safer choice; the FK is nullable on purpose.
    for d in data.get("decisions", []) or []:
        _insert_decision(
            conn,
            session_id=session_id if _session_exists(conn, session_id) else None,
            decision=d.get("decision", ""),
            reason=d.get("reason", ""),
            timestamp=d.get("timestamp"),
        )
        counts.decisions += 1
    for c in data.get("code_areas", []) or []:
        _insert_code_area(
            conn,
            session_id=session_id if _session_exists(conn, session_id) else None,
            file_path=c.get("file_path", ""),
            description=c.get("description", ""),
            timestamp=c.get("timestamp"),
        )
        counts.code_areas += 1
    return counts


def _insert_decision(conn, *, session_id, decision, reason, timestamp):
    epoch = int(timestamp) if timestamp else int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))
    conn.execute(
        "INSERT INTO decisions (session_id, decision, reason, source, "
        "created_at_epoch, created_at) VALUES (?, ?, ?, 'migrated', ?, ?)",
        (session_id, decision, reason, epoch, iso),
    )


def _insert_code_area(conn, *, session_id, file_path, description, timestamp):
    epoch = int(timestamp) if timestamp else int(time.time())
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
