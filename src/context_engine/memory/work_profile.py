"""User work-profile, persisted in memory.db.

Companion to project_summary: where project_summary describes *what the
project is*, this module describes *how the user works on it* — derived
purely from their accumulated session history so each new Claude /
Codex session opens with a "you typically work in X area, ship Y
sessions a week, and these are your recurring themes" preamble.

Sources mined (all already populated by the lifecycle hooks):

  * ``sessions``        — cadence, prompt counts, last active
  * ``code_areas``      — most-touched files via record_code_area()
  * ``sessions.rollup_summary``
                          — recurring keywords after stop-word stripping
  * ``decisions``       — total decision count (proxy for "open
                          choices the agent should respect")

Entirely extractive, no LLM dependency, no embeddings — runs in
milliseconds on a typical project's memory.db. Persisted in the v4
``work_profile`` table and re-rendered by SessionStart's resume builder.
Regenerated lazily; callers refresh when the row is older than
``WORK_PROFILE_TTL_SECONDS`` (3 days by default — shorter than
project_summary's 7 because user patterns shift faster than codebase
architecture).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import Counter

from context_engine.memory.grammar import _FILLERS_ULTRA

log = logging.getLogger(__name__)


WORK_PROFILE_TTL_SECONDS = 3 * 24 * 60 * 60

_TOP_FILES_N = 5
_TOP_THEMES_N = 6
# Minimum repetitions a token needs to count as a "recurring" theme.
# At 1 every word in the most-recent rollup would qualify, which adds
# noise rather than signal.
_MIN_THEME_REPEATS = 2
# Length filter for theme tokens — drops single letters and lone digits
# that aren't useful even after stop-word stripping.
_MIN_THEME_LENGTH = 3

# Token regex: words, dotted module names (auth.routes), and
# snake_case / kebab-case identifiers. Keeps domain terms together
# rather than splitting "vector_store" into "vector" + "store".
_TOKEN_RE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")

# Stop-word set tuned for the contents of rollup summaries — combines
# grammar.py's _FILLERS_ULTRA (articles, modals, pronouns, weak verbs)
# with a small extra set of conversational-prose connectives that show
# up frequently in rollups but tell us nothing about *what was worked
# on*. Kept conservative: domain terms (cache, auth, retry, parser,
# index, …) are NOT in this list.
_THEME_STOPWORDS = _FILLERS_ULTRA | frozenset({
    "session", "sessions", "previous", "next", "again",
    "fix", "fixed", "fixes", "fixing",
    "add", "added", "adds", "adding",
    "update", "updated", "updates", "updating",
    "use", "used", "uses", "using",
    "change", "changed", "changes", "changing",
    "make", "made", "makes", "making",
    "get", "got", "gets", "getting",
    "set", "sets", "setting", "settings",
    "new", "old", "good", "bad",
    "one", "two", "three", "four", "five",
    "first", "second", "third", "next", "last", "final",
    "today", "yesterday", "tomorrow",
    "ok", "yes", "no", "maybe",
    "work", "works", "worked", "working",
    "etc", "eg", "ie",
    # Common boilerplate from auto-generated rollups
    "ran", "running", "run", "runs",
    "added", "removed", "changed",
})


# ── Cadence ────────────────────────────────────────────────────────────


def _compute_cadence(conn: sqlite3.Connection) -> tuple[str, int]:
    """Return ("N sessions in D days · …", session_count).

    Empty string when fewer than 2 sessions exist (one data point can't
    establish a cadence). second tuple element is the raw session count
    so the caller can decide whether the whole block is worth emitting.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "MIN(started_at_epoch) AS first_epoch, "
        "MAX(COALESCE(ended_at_epoch, started_at_epoch)) AS last_epoch, "
        "AVG(NULLIF(prompt_count, 0)) AS avg_prompts "
        "FROM sessions"
    ).fetchone()
    if not row or not row["n"]:
        return ("", 0)
    n = int(row["n"])
    if n < 2:
        return ("", n)

    span_seconds = max(0, int(row["last_epoch"] or 0) - int(row["first_epoch"] or 0))
    span_days = max(1, span_seconds // 86_400)
    parts = [f"{n} sessions over {span_days} day{'s' if span_days != 1 else ''}"]

    avg_prompts = row["avg_prompts"]
    if avg_prompts is not None and avg_prompts >= 0.5:
        parts.append(f"~{round(float(avg_prompts))} prompts/session")

    last_active_days = max(
        0, (int(time.time()) - int(row["last_epoch"] or 0)) // 86_400
    )
    if last_active_days == 0:
        parts.append("active today")
    elif last_active_days == 1:
        parts.append("last active yesterday")
    else:
        parts.append(f"last active {last_active_days} days ago")

    return (" · ".join(parts), n)


# ── Top files ──────────────────────────────────────────────────────────


def _top_touched_files(conn: sqlite3.Connection) -> str:
    """Top N file_paths by code_area count, formatted as a single line.

    `code_areas` is the explicit "I worked on this and want future-me
    to find it fast" surface — much higher signal than mtime scans
    because the user is curating it.
    """
    try:
        rows = conn.execute(
            "SELECT file_path, COUNT(*) AS n "
            "FROM code_areas "
            "GROUP BY file_path "
            "ORDER BY n DESC, MAX(created_at_epoch) DESC "
            "LIMIT ?",
            (_TOP_FILES_N,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("top_touched_files query failed: %s", exc)
        return ""
    if not rows:
        return ""
    return ", ".join(f"{r['file_path']} (×{r['n']})" for r in rows)


# ── Recurring themes ──────────────────────────────────────────────────


def _extract_themes(conn: sqlite3.Connection) -> str:
    """Tokenize every rollup_summary, drop stop-words, return top tokens.

    A "theme" is a token that:
      - matches _TOKEN_RE (word, dotted, or snake/kebab identifier)
      - is at least _MIN_THEME_LENGTH chars long
      - is not in _THEME_STOPWORDS
      - appears at least _MIN_THEME_REPEATS times across all rollups
    Result is a comma-separated list of the top _TOP_THEMES_N tokens.
    """
    try:
        rows = conn.execute(
            "SELECT rollup_summary FROM sessions "
            "WHERE rollup_summary IS NOT NULL AND rollup_summary != ''"
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("rollup theme scan failed: %s", exc)
        return ""
    if not rows:
        return ""

    counts: Counter[str] = Counter()
    for r in rows:
        text = (r["rollup_summary"] or "").lower()
        for tok in _TOKEN_RE.findall(text):
            if len(tok) < _MIN_THEME_LENGTH:
                continue
            if tok in _THEME_STOPWORDS:
                continue
            counts[tok] += 1

    themes = [
        tok for tok, n in counts.most_common(_TOP_THEMES_N * 3)
        if n >= _MIN_THEME_REPEATS
    ]
    return ", ".join(themes[:_TOP_THEMES_N])


# ── Decision volume ───────────────────────────────────────────────────


def _open_decisions_count(conn: sqlite3.Connection) -> int:
    """Total decisions recorded — proxy for "how many durable choices
    the agent should respect across sessions"."""
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0


# ── Public API ────────────────────────────────────────────────────────


def build_work_profile(conn: sqlite3.Connection) -> dict:
    """Compute (but do not persist) a fresh work profile dict."""
    cadence, session_count = _compute_cadence(conn)
    return {
        "cadence": cadence,
        "top_files": _top_touched_files(conn),
        "recurring_themes": _extract_themes(conn),
        "open_decisions": _open_decisions_count(conn),
        "session_count": session_count,
        "generated_at_epoch": int(time.time()),
    }


def upsert_work_profile(
    conn: sqlite3.Connection, project: str, profile: dict
) -> None:
    """Persist `profile` for `project`, replacing any prior row."""
    conn.execute(
        """
        INSERT INTO work_profile
          (project, cadence, top_files, recurring_themes,
           open_decisions, generated_at_epoch)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project) DO UPDATE SET
          cadence = excluded.cadence,
          top_files = excluded.top_files,
          recurring_themes = excluded.recurring_themes,
          open_decisions = excluded.open_decisions,
          generated_at_epoch = excluded.generated_at_epoch
        """,
        (
            project,
            profile.get("cadence", ""),
            profile.get("top_files", ""),
            profile.get("recurring_themes", ""),
            int(profile.get("open_decisions", 0)),
            int(profile.get("generated_at_epoch", time.time())),
        ),
    )
    conn.commit()


def load_work_profile(
    conn: sqlite3.Connection, project: str
) -> dict | None:
    """Return the persisted profile dict for `project`, or None."""
    row = conn.execute(
        "SELECT cadence, top_files, recurring_themes, open_decisions, "
        "generated_at_epoch FROM work_profile WHERE project = ?",
        (project,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def is_stale(
    profile: dict, ttl_seconds: int = WORK_PROFILE_TTL_SECONDS
) -> bool:
    """True when `profile` was generated more than `ttl_seconds` ago."""
    age = int(time.time()) - int(profile.get("generated_at_epoch", 0))
    return age > ttl_seconds


def format_profile_block(profile: dict) -> str:
    """Render `profile` as a Markdown block for the resume hook.

    Empty string when there's nothing to say (e.g. one-session
    project with no recorded code_areas or decisions) so the caller
    can suppress the section on a fresh project.
    """
    cadence = (profile.get("cadence") or "").strip()
    files = (profile.get("top_files") or "").strip()
    themes = (profile.get("recurring_themes") or "").strip()
    decisions = int(profile.get("open_decisions", 0) or 0)
    if not (cadence or files or themes or decisions):
        return ""

    lines = ["**Your work profile** (extracted from prior sessions)"]
    if cadence:
        lines.append(f"  {cadence}")
    if files:
        lines.append(f"  _Most-touched files:_ {files}")
    if themes:
        lines.append(f"  _Recurring themes:_ {themes}")
    if decisions:
        lines.append(
            f"  _{decisions} decision{'s' if decisions != 1 else ''} on "
            "file — call `session_recall(\"<topic>\")` to read them._"
        )
    return "\n".join(lines)


def refresh_work_profile(
    conn: sqlite3.Connection, project: str, *, force: bool = False
) -> dict:
    """Rebuild and persist if missing, stale, or force=True.

    Idempotent; returns the (possibly-just-regenerated) profile dict.
    Callers should use this rather than calling build/upsert directly
    so the TTL stays in one place.
    """
    if not force:
        existing = load_work_profile(conn, project)
        if existing and not is_stale(existing):
            return existing
    profile = build_work_profile(conn)
    upsert_work_profile(conn, project, profile)
    return profile
