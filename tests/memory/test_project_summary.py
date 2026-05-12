"""Tests for the project_summary table and extractive builder.

Covers:
  - schema v4 migration (table exists after connect on a fresh db)
  - pitch extraction from README.md / pyproject.toml fallback
  - tech_stack tally from indexed chunks
  - recent_focus from code_areas
  - upsert + load round-trip
  - is_stale + TTL
  - format_summary_block omits empty sections
  - build_session_resume includes the new summary block AND last 3 sessions
"""
from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock


from context_engine.memory import db as memory_db
from context_engine.memory.hooks import build_session_resume
from context_engine.memory.project_summary import (
    SUMMARY_TTL_SECONDS,
    build_project_summary,
    format_summary_block,
    is_stale,
    load_project_summary,
    upsert_project_summary,
)


# ── Schema migration ────────────────────────────────────────────────────


def test_project_summary_table_exists_after_connect(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='project_summary'"
        ).fetchone()
        assert row is not None, "project_summary table missing"
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(project_summary)")
        }
        for expected in (
            "project", "pitch", "tech_stack", "recent_focus",
            "source_file_count", "generated_at_epoch",
        ):
            assert expected in cols, f"missing column {expected}"
    finally:
        conn.close()


# ── Upsert / load round-trip ───────────────────────────────────────────


def test_upsert_and_load_round_trip(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        payload = {
            "pitch": "A tool for X",
            "tech_stack": "Python (10), JavaScript (3)",
            "recent_focus": "src/a.py — hot loop",
            "source_file_count": 13,
            "generated_at_epoch": 1700000000,
        }
        upsert_project_summary(conn, "demo", payload)
        loaded = load_project_summary(conn, "demo")
        assert loaded is not None
        assert loaded["pitch"] == "A tool for X"
        assert loaded["tech_stack"] == "Python (10), JavaScript (3)"
        assert loaded["recent_focus"] == "src/a.py — hot loop"
        assert loaded["source_file_count"] == 13
        assert loaded["generated_at_epoch"] == 1700000000
    finally:
        conn.close()


def test_upsert_replaces_existing(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        upsert_project_summary(conn, "demo", {
            "pitch": "old", "tech_stack": "", "recent_focus": "",
            "source_file_count": 0, "generated_at_epoch": 1700000000,
        })
        upsert_project_summary(conn, "demo", {
            "pitch": "new", "tech_stack": "", "recent_focus": "",
            "source_file_count": 0, "generated_at_epoch": 1700001000,
        })
        loaded = load_project_summary(conn, "demo")
        assert loaded["pitch"] == "new"
        # And no duplicate rows.
        rows = list(conn.execute("SELECT project FROM project_summary"))
        assert len(rows) == 1
    finally:
        conn.close()


def test_load_returns_none_when_absent(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert load_project_summary(conn, "nope") is None
    finally:
        conn.close()


# ── is_stale ───────────────────────────────────────────────────────────


def test_is_stale_true_when_old():
    summary = {"generated_at_epoch": int(time.time()) - (SUMMARY_TTL_SECONDS + 10)}
    assert is_stale(summary) is True


def test_is_stale_false_when_fresh():
    summary = {"generated_at_epoch": int(time.time())}
    assert is_stale(summary) is False


# ── Pitch extraction ───────────────────────────────────────────────────


def _make_vector_store(paths: list[str]):
    """Build a vector_store stub that exposes a _conn with `chunks` rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE chunks (id TEXT, content TEXT, file_path TEXT)"
    )
    for i, p in enumerate(paths):
        conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?)",
            (str(i), "x", p),
        )
    conn.commit()
    store = MagicMock()
    store._conn = conn
    return store


def test_extract_pitch_from_readme(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n"
        "![badge](https://example.com/x.png) "
        "[link](https://example.com)\n\n"
        "Demo is a small library for parsing TOML files and emitting\n"
        "warnings about non-canonical whitespace.\n"
    )
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store([]),
        )
        assert "Demo is a small library" in summary["pitch"], summary["pitch"]
        # Badges/links must be stripped.
        assert "badge" not in summary["pitch"]
        assert "https://" not in summary["pitch"]
    finally:
        memory_db_conn.close()


def test_extract_pitch_falls_back_to_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n'
        'description = "A tool for indexing things efficiently"\n'
        'version = "0.1.0"\n'
    )
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store([]),
        )
        assert summary["pitch"] == "A tool for indexing things efficiently"
    finally:
        memory_db_conn.close()


def test_pitch_empty_when_no_readme_or_pyproject(tmp_path):
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store([]),
        )
        assert summary["pitch"] == ""
    finally:
        memory_db_conn.close()


# ── Tech stack ─────────────────────────────────────────────────────────


def test_tech_stack_tallies_extensions(tmp_path):
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        paths = (
            ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
            + ["app/x.ts", "app/y.ts"]
            + ["README.md"]
        )
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store(paths),
        )
        assert "Python" in summary["tech_stack"]
        assert "(4)" in summary["tech_stack"]
        assert "TypeScript" in summary["tech_stack"]
        assert summary["source_file_count"] == 7
    finally:
        memory_db_conn.close()


def test_tech_stack_handles_empty_index(tmp_path):
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store([]),
        )
        assert summary["tech_stack"] == ""
        assert summary["source_file_count"] == 0
    finally:
        memory_db_conn.close()


# ── Recent focus from code_areas ───────────────────────────────────────


def test_recent_focus_from_code_areas(tmp_path):
    memory_db_conn = memory_db.connect(tmp_path / "memory.db")
    try:
        # Insert a few code_areas, varying recency.
        for i, (path, desc, t) in enumerate([
            ("src/oldest.py", "old work", 1700000000),
            ("src/middle.py", "middle work", 1700001000),
            ("src/newest.py", "newest work", 1700002000),
        ]):
            memory_db_conn.execute(
                "INSERT INTO code_areas (file_path, description, source, "
                "created_at_epoch) VALUES (?, ?, 'manual', ?)",
                (path, desc, t),
            )
        memory_db_conn.commit()
        summary = build_project_summary(
            project_dir=tmp_path,
            memory_conn=memory_db_conn,
            vector_store=_make_vector_store([]),
        )
        focus = summary["recent_focus"]
        # Most-recent first.
        assert focus.index("newest.py") < focus.index("oldest.py")
        assert "newest work" in focus
    finally:
        memory_db_conn.close()


# ── format_summary_block ───────────────────────────────────────────────


def test_format_summary_block_omits_empty_sections():
    block = format_summary_block({
        "pitch": "A demo tool",
        "tech_stack": "",
        "recent_focus": "",
    })
    assert "**Project summary**" in block
    assert "A demo tool" in block
    assert "_Stack:_" not in block
    assert "_Recent focus:_" not in block


def test_format_summary_block_empty_returns_empty():
    assert format_summary_block({
        "pitch": "", "tech_stack": "", "recent_focus": "",
    }) == ""


# ── build_session_resume integration ───────────────────────────────────


def test_resume_includes_project_summary(tmp_path):
    """The new feature: SessionStart resume must prepend the project
    summary so each Claude/Codex session sees what the project is."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        upsert_project_summary(conn, "demo", {
            "pitch": "Local context engine for AI coding assistants",
            "tech_stack": "Python (200), Markdown (15)",
            "recent_focus": "src/context_engine/cli.py — main entry",
            "source_file_count": 215,
            "generated_at_epoch": int(time.time()),
        })
        text = build_session_resume(conn, "demo")
        assert "Project summary" in text
        assert "Local context engine" in text
        assert "Python (200)" in text
        assert "cli.py" in text
    finally:
        conn.close()


def test_resume_shows_last_three_sessions(tmp_path):
    """Previously only 1 prior session was shown — now last 3."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        for i, sid in enumerate(["s1", "s2", "s3", "s4"]):
            conn.execute(
                "INSERT INTO sessions (id, project, started_at_epoch, "
                "started_at, ended_at_epoch, ended_at, status, "
                "rollup_summary, rollup_summary_at_epoch) VALUES "
                "(?, 'demo', ?, ?, ?, ?, 'completed', ?, ?)",
                (
                    sid,
                    1700000000 + i * 1000,
                    f"start-{i}",
                    1700001000 + i * 1000,
                    f"end-{i}",
                    f"Session {sid} did thing {i}.",
                    1700001000 + i * 1000,
                ),
            )
        conn.commit()

        text = build_session_resume(conn, "demo")
        # Three most-recent should appear (s4, s3, s2), s1 omitted.
        assert "did thing 3" in text  # s4
        assert "did thing 2" in text  # s3
        assert "did thing 1" in text  # s2
        assert "did thing 0" not in text  # s1 dropped
        # Header reflects plurality.
        assert "Previous 3 sessions" in text
    finally:
        conn.close()


def test_resume_uses_singular_header_when_only_one_session(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, "
            "started_at, ended_at_epoch, ended_at, status, "
            "rollup_summary, rollup_summary_at_epoch) VALUES "
            "('only', 'demo', 1700000000, 's', 1700001000, 'e', "
            "'completed', 'Only session work', 1700001000)"
        )
        conn.commit()
        text = build_session_resume(conn, "demo")
        assert "Previous session" in text
        assert "Previous 1 sessions" not in text
    finally:
        conn.close()


def test_resume_empty_when_no_state(tmp_path):
    """Brand-new project, no summary, no rollups, no decisions → blank."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert build_session_resume(conn, "demo") == ""
    finally:
        conn.close()


def test_resume_tolerates_missing_project_summary_table(tmp_path):
    """An old db without the v4 table must not crash the resume — it
    should just skip the summary block."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        conn.execute("DROP TABLE project_summary")
        # Still need at least one piece of state so the function gets past
        # its early-return.
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) VALUES "
            "('Use SQLite', 'simple', 'manual', 1700000000, 't')"
        )
        conn.commit()
        text = build_session_resume(conn, "demo")
        assert "Use SQLite" in text
        # No project summary block because the table is gone.
        assert "Project summary" not in text
    finally:
        conn.close()
