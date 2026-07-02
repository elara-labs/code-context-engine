"""Session history capture — records decisions, code areas, and Q&A for future recall."""
import json
import logging
import threading
import time
import uuid
from pathlib import Path

from context_engine.utils import atomic_write_text as _atomic_write_text

log = logging.getLogger(__name__)

# Once a project accumulates more session JSONs than this, the oldest are
# consolidated into decisions_log.json (decisions only — the durable signal)
# and the source files are removed. The most recent _PRUNE_KEEP files are
# always preserved verbatim.
_PRUNE_THRESHOLD = 100
_PRUNE_KEEP = 50
_DECISIONS_LOG_NAME = "decisions_log.json"

class SessionCapture:
    """Thread-safe session log. All `_active` access goes through `_lock` so
    concurrent MCP tool calls (e.g. record_decision while end_session flushes)
    can't interleave a half-mutation."""

    def __init__(self, sessions_dir: str) -> None:
        self._sessions_dir = sessions_dir
        Path(sessions_dir).mkdir(parents=True, exist_ok=True)
        self._active: dict[str, dict] = {}
        self._lock = threading.RLock()

    def start_session(self, project_name: str) -> str:
        session_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._active[session_id] = {
                "id": session_id, "project": project_name, "started_at": time.time(),
                "decisions": [], "code_areas": [], "questions": [],
                # touched_files: per-file count of how many times the chunk was
                # surfaced or opened during the session. Auto-captured by the
                # MCP server so even sessions where Claude never explicitly
                # calls `record_code_area` leave a useful breadcrumb.
                "touched_files": {},
            }
        return session_id

    def record_decision(self, session_id, decision, reason):
        with self._lock:
            session = self._active.get(session_id)
            if session:
                session["decisions"].append({"decision": decision, "reason": reason, "timestamp": time.time()})

    def record_code_area(self, session_id, file_path, description):
        with self._lock:
            session = self._active.get(session_id)
            if session:
                session["code_areas"].append({"file_path": file_path, "description": description, "timestamp": time.time()})

    def touch_files(self, session_id, file_paths) -> None:
        """Bump the touched-files counter for each path. Auto-called by the
        MCP server whenever a result references a file or a chunk is opened.
        Cheap (in-memory dict update); persisted on the next flush."""
        if not file_paths:
            return
        with self._lock:
            session = self._active.get(session_id)
            if not session:
                return
            counts = session.setdefault("touched_files", {})
            for fp in file_paths:
                if not fp or fp.startswith("git:"):
                    continue
                counts[fp] = counts.get(fp, 0) + 1

    def get_session_snapshot(self, session_id) -> dict | None:
        """Return a shallow copy of the active session for safe inspection.
        Returns None if the session_id isn't in _active."""
        with self._lock:
            session = self._active.get(session_id)
            if session is None:
                return None
            return dict(session)

    def get_decisions(self, session_id):
        with self._lock:
            session = self._active.get(session_id)
            # Defensive copy so the caller can iterate without holding the lock.
            return list(session["decisions"]) if session else []

    def get_code_areas(self, session_id):
        with self._lock:
            session = self._active.get(session_id)
            return list(session["code_areas"]) if session else []

    def end_session(self, session_id):
        with self._lock:
            session = self._active.pop(session_id, None)
        if session:
            session["ended_at"] = time.time()
            file_path = Path(self._sessions_dir) / f"{session_id}.json"
            _atomic_write_text(file_path, json.dumps(session, indent=2))

    def load_recent_sessions(self, limit=5):
        sessions_path = Path(self._sessions_dir)
        files = [
            f for f in sessions_path.glob("*.json")
            # decisions_log.json is the consolidated archive, not a session.
            if f.name != _DECISIONS_LOG_NAME
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        sessions = []
        for f in files[:limit]:
            try:
                with open(f) as fp:
                    sessions.append(json.load(fp))
            except (json.JSONDecodeError, OSError):
                # Skip corrupt session files; don't blow up recall.
                continue
        return sessions

    def prune_old_sessions(
        self,
        threshold: int = _PRUNE_THRESHOLD,
        keep: int = _PRUNE_KEEP,
    ) -> dict:
        """Consolidate old session JSONs into decisions_log.json + delete them.

        Triggered automatically at server start when there are more than
        `threshold` session files; can also be run from the CLI as
        `cce sessions prune`. Returns a summary dict so the caller can report.

        Only the *decisions* (and their reasons + timestamps + originating
        session id) survive consolidation. code_areas and questions in old
        sessions are dropped — they were heuristic auto-captures and the
        signal-to-noise drops fast as they age.

        Uses an fcntl advisory lock on `.prune.lock` in the sessions dir so
        two processes can't race the read-append-write on decisions_log.json
        (last-write-wins would clobber one process's appended decisions).
        On Windows fcntl is unavailable; we fall through without a lock and
        accept the rare race — Windows isn't a supported deploy target today.
        """
        sessions_path = Path(self._sessions_dir)
        sessions_path.mkdir(parents=True, exist_ok=True)
        lock_path = sessions_path / ".prune.lock"
        # Acquire an exclusive flock; fall back to no-op on platforms where
        # fcntl isn't available so the prune still runs (just unlocked).
        lock_fh = None
        try:
            import fcntl  # POSIX only; ImportError on Windows
            lock_fh = open(lock_path, "w")
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Another process is pruning right now — let it finish.
                lock_fh.close()
                return {"pruned": 0, "kept": -1, "reason": "another prune in progress"}
        except ImportError:
            lock_fh = None

        try:
            return self._prune_locked(sessions_path, threshold, keep)
        finally:
            if lock_fh is not None:
                try:
                    import fcntl
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                lock_fh.close()

    def _prune_locked(
        self,
        sessions_path: Path,
        threshold: int,
        keep: int,
    ) -> dict:
        """The actual prune work. Caller holds the cross-process flock."""
        files = sorted(
            (f for f in sessions_path.glob("*.json") if f.name != _DECISIONS_LOG_NAME),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if len(files) <= threshold:
            return {"pruned": 0, "kept": len(files), "reason": "below threshold"}

        keep_files = files[:keep]
        old_files = files[keep:]

        log_path = sessions_path / _DECISIONS_LOG_NAME
        existing: list[dict] = []
        if log_path.exists():
            try:
                existing = json.loads(log_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, OSError):
                existing = []

        appended = 0
        for f in old_files:
            if f == log_path:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Skipping unreadable session file %s: %s", f, exc)
                continue
            for d in data.get("decisions", []):
                existing.append({
                    "decision": d.get("decision", ""),
                    "reason": d.get("reason", ""),
                    "timestamp": d.get("timestamp", 0.0),
                    "session_id": data.get("id", ""),
                })
                appended += 1

        try:
            _atomic_write_text(log_path, json.dumps(existing, indent=2))
        except OSError as exc:
            log.warning("Failed to write decisions_log: %s", exc)
            return {"pruned": 0, "kept": len(files), "reason": f"write failed: {exc}"}

        deleted = 0
        for f in old_files:
            if f == log_path:
                continue
            try:
                f.unlink()
                deleted += 1
            except OSError as exc:
                log.warning("Failed to remove old session %s: %s", f, exc)

        return {
            "pruned": deleted,
            "kept": len(keep_files),
            "decisions_appended": appended,
            "decisions_log": str(log_path),
        }

    def _load_consolidated_decisions(self) -> list[dict]:
        """Read decisions_log.json (the consolidated archive). Returns []
        when absent or unreadable — never raises."""
        log_path = Path(self._sessions_dir) / _DECISIONS_LOG_NAME
        if not log_path.exists():
            return []
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def get_recent_decisions(self, limit: int = 10, session_limit: int = 50) -> list[str]:
        """Return the most-recent decision strings across recent sessions.

        Used by the bootstrap prompt to inject prior decisions at session
        start without relying on a topic-grep that often returns nothing.
        Includes any decisions in the currently active in-memory session.
        Order: newest first by recorded timestamp.
        """
        decisions: list[tuple[float, str]] = []

        # Active in-memory sessions first (may not yet be flushed to disk).
        # Snapshot under the lock so a concurrent record_decision can't mutate
        # the list while we're iterating it.
        import copy

        with self._lock:
            active_snapshot = copy.deepcopy(list(self._active.values()))
        for session in active_snapshot:
            for d in session.get("decisions", []):
                ts = d.get("timestamp", 0.0)
                text = (
                    f"[decision] {d.get('decision', '')} — {d.get('reason', '')}"
                )
                decisions.append((ts, text))

        for session in self.load_recent_sessions(limit=session_limit):
            for d in session.get("decisions", []):
                ts = d.get("timestamp", 0.0)
                text = (
                    f"[decision] {d.get('decision', '')} — {d.get('reason', '')}"
                )
                decisions.append((ts, text))

        # Pull from the consolidated archive as well — `prune_old_sessions`
        # writes decisions there before deleting the source files, so without
        # this step a recall on a long-lived project would forget anything
        # past the most-recent session_limit files.
        for d in self._load_consolidated_decisions():
            ts = d.get("timestamp", 0.0)
            text = (
                f"[decision] {d.get('decision', '')} — {d.get('reason', '')}"
            )
            decisions.append((ts, text))

        # Dedup keeping the newest occurrence of each text.
        seen: set[str] = set()
        ordered: list[str] = []
        for _, text in sorted(decisions, key=lambda pair: pair[0], reverse=True):
            if text in seen:
                continue
            seen.add(text)
            ordered.append(text)
            if len(ordered) >= limit:
                break
        return ordered
