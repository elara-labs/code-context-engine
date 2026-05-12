"""Project-level summary, persisted in memory.db.

The SessionStart hook injects a *resume* block at the start of every new
Claude Code conversation. Before this module, that block only carried
prior-session rollups and decisions — nothing told the model what the
project actually IS at a high level, so each fresh conversation re-derived
the basics from scratch.

`build_project_summary()` produces a small, three-section text block:

  * **pitch**         — one sentence pulled from README.md/CONTRIBUTING.md
                        front matter or the pyproject description
  * **tech_stack**    — file-extension distribution from the indexed
                        chunks, top languages first
  * **recent_focus**  — most-touched file paths from the `code_areas`
                        table (the canonical "where work has been
                        happening lately" signal)

Entirely extractive — no LLM dependency — so it can run on `cce init`
without requiring Ollama or fastembed-the-model. Persisted in the v4
`project_summary` table and read back by `build_session_resume()`.

Regenerated on demand; callers should refresh when the row is older than
``SUMMARY_TTL_SECONDS`` (7 days by default) or after a large index
operation finishes.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


# Regenerate the project summary if the cached row is older than this. A
# week balances "fresh enough to reflect new architectural decisions" with
# "not paying the rescan cost on every `cce init`".
SUMMARY_TTL_SECONDS = 7 * 24 * 60 * 60

# Caps for the three sections — kept tight because the resume block goes
# into every session's context window.
_PITCH_MAX_CHARS = 280
_TECH_STACK_TOP_N = 6
_RECENT_FOCUS_TOP_N = 5

# File-extension → display name. Anything not listed falls back to the
# bare extension (e.g. ".rs" → "rs"). Matches the language map in
# indexer/pipeline.py but is intentionally a small subset — we don't need
# every recognised language, only the common ones.
_EXT_LABELS = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JSX", ".tsx": "TSX", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".c": "C", ".cpp": "C++", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".sh": "Shell", ".md": "Markdown",
    ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".json": "JSON",
}


# ── Pitch extraction ────────────────────────────────────────────────────


def _strip_html(line: str) -> str:
    """Trim HTML/Markdown noise that shows up at the top of READMEs."""
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # images
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)  # markdown links
    line = re.sub(r"[#*_`]", "", line)
    return line.strip()


def _extract_pitch_from_readme(project_dir: Path) -> str:
    """Return the first substantive sentence from README-like files.

    Walks a small candidate list in priority order. A "substantive"
    sentence is the first non-empty, non-heading, non-badge line whose
    plain-text form is at least 30 characters — short enough that a
    one-line tagline counts, long enough that "Welcome!" doesn't.
    """
    candidates = [
        project_dir / "README.md",
        project_dir / "README.rst",
        project_dir / "README.txt",
        project_dir / "README",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines()[:80]:
            line = _strip_html(raw).strip()
            if not line or line.startswith("#"):
                continue
            if len(line) < 30:
                continue
            if len(line) > _PITCH_MAX_CHARS:
                line = line[:_PITCH_MAX_CHARS].rsplit(" ", 1)[0] + "…"
            return line
    return ""


def _extract_pitch_from_pyproject(project_dir: Path) -> str:
    """Fallback pitch: the `description` field from pyproject.toml.

    Skipped if the project doesn't have one (e.g. a JS-only repo). The
    parser is intentionally regex-based — pulling in `tomllib` for one
    field is heavier than needed and 3.11+ has it stdlib anyway.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Match `description = "..."` at the top of [project].
    m = re.search(r'^\s*description\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        return ""
    pitch = m.group(1).strip()
    if len(pitch) > _PITCH_MAX_CHARS:
        pitch = pitch[:_PITCH_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return pitch


def _extract_pitch(project_dir: Path) -> str:
    return (
        _extract_pitch_from_readme(project_dir)
        or _extract_pitch_from_pyproject(project_dir)
        or ""
    )


# ── Tech stack distribution ────────────────────────────────────────────


def _extract_tech_stack(vector_store) -> tuple[str, int]:
    """Read distinct file paths from the vector store and tally extensions.

    Returns (label_string, file_count). `label_string` is a comma-joined
    list of the top languages by file count, e.g. "Python (124),
    TypeScript (38), Markdown (12)". Returns ("", 0) if the store hasn't
    been indexed yet (count == 0) or the path-fetching API isn't
    available — the caller is expected to handle the empty case
    gracefully.
    """
    try:
        # VectorStore exposes `count()` but not (yet) a bulk distinct-path
        # call, so we go through the underlying connection. Keeping this
        # behind a try/except so an internals refactor doesn't break the
        # summary builder.
        conn = vector_store._conn  # noqa: SLF001
        rows = conn.execute(
            "SELECT DISTINCT file_path FROM chunks"
        ).fetchall()
    except (AttributeError, sqlite3.Error) as exc:
        log.debug("tech_stack scan unavailable: %s", exc)
        return ("", 0)

    paths = [r[0] for r in rows if r and r[0]]
    if not paths:
        return ("", 0)

    counts: Counter[str] = Counter()
    for p in paths:
        suffix = Path(p).suffix.lower()
        if not suffix:
            continue
        label = _EXT_LABELS.get(suffix, suffix.lstrip(".").upper())
        counts[label] += 1
    if not counts:
        return ("", len(paths))

    top = counts.most_common(_TECH_STACK_TOP_N)
    return (
        ", ".join(f"{label} ({count})" for label, count in top),
        len(paths),
    )


# ── Recent focus (from code_areas) ─────────────────────────────────────


def _extract_recent_focus(conn: sqlite3.Connection) -> str:
    """Return the top N file_paths from code_areas, most-recent first.

    `code_areas` is populated by record_code_area() — the human-curated
    "I worked on this and want future-me to find it fast" signal — and
    is the cleanest proxy for "what's the current focus" without
    requiring git or file-mtime scans.
    """
    try:
        rows = conn.execute(
            "SELECT file_path, description, MAX(created_at_epoch) AS last_seen "
            "FROM code_areas "
            "GROUP BY file_path "
            "ORDER BY last_seen DESC "
            "LIMIT ?",
            (_RECENT_FOCUS_TOP_N,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("recent_focus query failed: %s", exc)
        return ""
    if not rows:
        return ""
    parts = []
    for r in rows:
        file_path = r["file_path"]
        desc = (r["description"] or "").strip()
        if desc:
            # Truncate per-line so one verbose record_code_area call
            # doesn't blow out the resume block.
            if len(desc) > 100:
                desc = desc[:100].rsplit(" ", 1)[0] + "…"
            parts.append(f"{file_path} — {desc}")
        else:
            parts.append(file_path)
    return "\n".join(parts)


# ── Public API ─────────────────────────────────────────────────────────


def build_project_summary(
    project_dir: Path,
    memory_conn: sqlite3.Connection,
    vector_store,
) -> dict:
    """Build (but do not persist) a fresh summary dict.

    Composed from three independent sources so a failure in one section
    doesn't poison the others. The caller persists via
    :func:`upsert_project_summary`.
    """
    pitch = _extract_pitch(project_dir)
    tech_stack, file_count = _extract_tech_stack(vector_store)
    recent_focus = _extract_recent_focus(memory_conn)
    return {
        "pitch": pitch,
        "tech_stack": tech_stack,
        "recent_focus": recent_focus,
        "source_file_count": file_count,
        "generated_at_epoch": int(time.time()),
    }


def upsert_project_summary(
    conn: sqlite3.Connection, project: str, summary: dict
) -> None:
    """Persist `summary` for `project`, replacing any prior row."""
    conn.execute(
        """
        INSERT INTO project_summary
          (project, pitch, tech_stack, recent_focus,
           source_file_count, generated_at_epoch)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project) DO UPDATE SET
          pitch = excluded.pitch,
          tech_stack = excluded.tech_stack,
          recent_focus = excluded.recent_focus,
          source_file_count = excluded.source_file_count,
          generated_at_epoch = excluded.generated_at_epoch
        """,
        (
            project,
            summary.get("pitch", ""),
            summary.get("tech_stack", ""),
            summary.get("recent_focus", ""),
            int(summary.get("source_file_count", 0)),
            int(summary.get("generated_at_epoch", time.time())),
        ),
    )
    conn.commit()


def load_project_summary(
    conn: sqlite3.Connection, project: str
) -> dict | None:
    """Return the persisted summary dict for `project`, or None."""
    row = conn.execute(
        "SELECT pitch, tech_stack, recent_focus, source_file_count, "
        "generated_at_epoch FROM project_summary WHERE project = ?",
        (project,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def is_stale(summary: dict, ttl_seconds: int = SUMMARY_TTL_SECONDS) -> bool:
    """True when `summary` was generated more than `ttl_seconds` ago."""
    age = int(time.time()) - int(summary.get("generated_at_epoch", 0))
    return age > ttl_seconds


def format_summary_block(summary: dict) -> str:
    """Render `summary` as a Markdown block for the resume hook.

    Returns "" when all three sections are empty so the caller can suppress
    the block on a brand-new project.
    """
    pitch = (summary.get("pitch") or "").strip()
    stack = (summary.get("tech_stack") or "").strip()
    focus = (summary.get("recent_focus") or "").strip()
    if not (pitch or stack or focus):
        return ""
    lines = ["**Project summary**"]
    if pitch:
        lines.append(f"  {pitch}")
    if stack:
        lines.append(f"  _Stack:_ {stack}")
    if focus:
        lines.append("  _Recent focus:_")
        for line in focus.split("\n"):
            line = line.strip()
            if line:
                lines.append(f"    - {line}")
    return "\n".join(lines)
