"""Tests for the work_profile table and extractive builder.

work_profile is the "how the user works on this project" companion to
project_summary. Covered here:

  - schema v4 migration adds the table
  - cadence calculation (count, span, avg prompts, last-active)
  - top file aggregation from code_areas
  - recurring-theme extraction with stop-word stripping
  - upsert / load / is_stale round-trip
  - format_profile_block omits empty sections
  - refresh_work_profile honours the TTL but rebuilds on force
  - build_session_resume includes the work-profile block
"""
from __future__ import annotations

import time

from context_engine.memory import db as memory_db
from context_engine.memory.hooks import build_session_resume
from context_engine.memory.work_profile import (
    WORK_PROFILE_TTL_SECONDS,
    build_work_profile,
    format_profile_block,
    is_stale,
    load_work_profile,
    refresh_work_profile,
    upsert_work_profile,
)


# ── Schema ─────────────────────────────────────────────────────────────


def test_work_profile_table_exists_after_connect(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='work_profile'"
        ).fetchone()
        assert row is not None, "work_profile table missing"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(work_profile)")}
        for expected in (
            "project", "cadence", "top_files", "recurring_themes",
            "open_decisions", "generated_at_epoch",
        ):
            assert expected in cols, f"missing column {expected}"
    finally:
        conn.close()


# ── Upsert / load / is_stale ───────────────────────────────────────────


def test_upsert_round_trip(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        payload = {
            "cadence": "4 sessions over 6 days · ~3 prompts/session",
            "top_files": "src/a.py (×5), src/b.py (×2)",
            "recurring_themes": "retry, cache, auth",
            "open_decisions": 7,
            "generated_at_epoch": 1700000000,
        }
        upsert_work_profile(conn, "demo", payload)
        loaded = load_work_profile(conn, "demo")
        assert loaded is not None
        assert loaded["cadence"].startswith("4 sessions")
        assert "src/a.py (×5)" in loaded["top_files"]
        assert loaded["recurring_themes"] == "retry, cache, auth"
        assert loaded["open_decisions"] == 7
        assert loaded["generated_at_epoch"] == 1700000000
    finally:
        conn.close()


def test_load_returns_none_when_absent(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert load_work_profile(conn, "nope") is None
    finally:
        conn.close()


def test_is_stale_true_when_old():
    profile = {
        "generated_at_epoch": int(time.time()) - WORK_PROFILE_TTL_SECONDS - 10,
    }
    assert is_stale(profile) is True


def test_is_stale_false_when_fresh():
    assert is_stale({"generated_at_epoch": int(time.time())}) is False


# ── Cadence ────────────────────────────────────────────────────────────


def _seed_session(conn, sid, started_epoch, ended_epoch, prompt_count, rollup=None):
    conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "ended_at_epoch, ended_at, status, prompt_count, "
        "rollup_summary, rollup_summary_at_epoch) VALUES "
        "(?, 'demo', ?, ?, ?, ?, 'completed', ?, ?, ?)",
        (
            sid, started_epoch, f"start-{sid}",
            ended_epoch, f"end-{sid}",
            prompt_count,
            rollup, ended_epoch if rollup else None,
        ),
    )


def test_cadence_skipped_with_only_one_session(tmp_path):
    """One data point isn't a cadence — leave the line blank."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed_session(conn, "only", 1700000000, 1700001000, 5)
        conn.commit()
        profile = build_work_profile(conn)
        assert profile["cadence"] == ""
        assert profile["session_count"] == 1
    finally:
        conn.close()


def test_cadence_reports_span_and_average(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        # 3 sessions, 2 days apart, average 4 prompts/session.
        for i, prompts in enumerate([3, 5, 4]):
            _seed_session(
                conn, f"s{i}",
                started_epoch=1700000000 + i * 86400 * 2,
                ended_epoch=1700001000 + i * 86400 * 2,
                prompt_count=prompts,
            )
        conn.commit()
        profile = build_work_profile(conn)
        assert "3 sessions" in profile["cadence"]
        assert "days" in profile["cadence"]
        # avg of 3, 5, 4 = 4 → "~4 prompts/session"
        assert "~4 prompts/session" in profile["cadence"]


    finally:
        conn.close()


# ── Top files ──────────────────────────────────────────────────────────


def test_top_files_ranks_by_frequency(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        for path, n in [
            ("src/auth.py", 5),
            ("src/db.py", 3),
            ("src/cli.py", 1),
        ]:
            for i in range(n):
                conn.execute(
                    "INSERT INTO code_areas (file_path, description, source, "
                    "created_at_epoch) VALUES (?, ?, 'manual', ?)",
                    (path, f"work {i}", 1700000000 + i),
                )
        conn.commit()
        profile = build_work_profile(conn)
        top = profile["top_files"]
        # auth.py first (5), then db.py (3), then cli.py (1)
        assert top.index("auth.py") < top.index("db.py") < top.index("cli.py")
        assert "(×5)" in top
        assert "(×3)" in top
        assert "(×1)" in top
    finally:
        conn.close()


def test_top_files_empty_when_no_code_areas(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert build_work_profile(conn)["top_files"] == ""
    finally:
        conn.close()


# ── Recurring themes ──────────────────────────────────────────────────


def test_themes_extract_repeating_tokens_and_drop_stopwords(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        rollups = [
            "Built retry logic for the auth client. Used jittered backoff.",
            "Fixed cache invalidation in the auth path; retry budgets confirmed.",
            "Worked on cache eviction and retry semantics; we keep refining auth.",
            "Investigated retry storms when cache misses spike.",
        ]
        for i, r in enumerate(rollups):
            _seed_session(
                conn, f"s{i}",
                started_epoch=1700000000 + i * 86400,
                ended_epoch=1700001000 + i * 86400,
                prompt_count=3,
                rollup=r,
            )
        conn.commit()
        themes = build_work_profile(conn)["recurring_themes"]
        # "retry" and "cache" appear in all four — must be top themes.
        assert "retry" in themes
        assert "cache" in themes
        assert "auth" in themes
        # Stopwords / common verbs must NOT leak in.
        for stop in ("the", "for", "and", "fixed", "used", "worked"):
            assert stop not in themes.split(", "), (
                f"stopword {stop!r} leaked into themes: {themes!r}"
            )
    finally:
        conn.close()


def test_themes_require_min_repeats(tmp_path):
    """Single-occurrence tokens don't count as recurring."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        # 'globally' and 'whimsical' each appear once → must NOT make it.
        # 'cache' appears twice → should appear.
        _seed_session(
            conn, "s1", 1700000000, 1700001000, 3,
            rollup="Did one whimsical thing with the cache today.",
        )
        _seed_session(
            conn, "s2", 1700100000, 1700101000, 3,
            rollup="Refined cache invalidation again globally.",
        )
        conn.commit()
        themes = build_work_profile(conn)["recurring_themes"]
        assert "cache" in themes
        assert "whimsical" not in themes
        assert "globally" not in themes
    finally:
        conn.close()


def test_themes_empty_with_no_rollups(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        # session exists but no rollup_summary → no theme source
        _seed_session(conn, "s1", 1700000000, 1700001000, 3)
        conn.commit()
        assert build_work_profile(conn)["recurring_themes"] == ""
    finally:
        conn.close()


# ── Decision count ────────────────────────────────────────────────────


def test_open_decisions_count(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        for i in range(7):
            conn.execute(
                "INSERT INTO decisions (decision, reason, source, "
                "created_at_epoch, created_at) VALUES "
                "(?, ?, 'manual', ?, ?)",
                (f"Decision {i}", f"because {i}", 1700000000 + i, f"t{i}"),
            )
        conn.commit()
        assert build_work_profile(conn)["open_decisions"] == 7
    finally:
        conn.close()


# ── format_profile_block ───────────────────────────────────────────────


def test_format_block_empty_returns_empty():
    assert format_profile_block({
        "cadence": "", "top_files": "",
        "recurring_themes": "", "open_decisions": 0,
    }) == ""


def test_format_block_omits_missing_sections():
    block = format_profile_block({
        "cadence": "4 sessions over 6 days",
        "top_files": "",
        "recurring_themes": "",
        "open_decisions": 0,
    })
    assert "Your work profile" in block
    assert "4 sessions" in block
    assert "Most-touched files" not in block
    assert "Recurring themes" not in block


def test_format_block_pluralises_decisions():
    one = format_profile_block({
        "cadence": "x", "top_files": "", "recurring_themes": "",
        "open_decisions": 1,
    })
    assert "1 decision on file" in one
    assert "1 decisions" not in one
    many = format_profile_block({
        "cadence": "x", "top_files": "", "recurring_themes": "",
        "open_decisions": 4,
    })
    assert "4 decisions on file" in many


# ── refresh_work_profile (TTL + force) ─────────────────────────────────


def test_refresh_reuses_fresh_profile(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed_session(conn, "s1", 1700000000, 1700001000, 3)
        _seed_session(conn, "s2", 1700100000, 1700101000, 4)
        conn.commit()
        first = refresh_work_profile(conn, "demo")
        # Insert a third session AFTER the first refresh — without --force
        # the cached row should be served back.
        _seed_session(conn, "s3", 1700200000, 1700201000, 5)
        conn.commit()
        second = refresh_work_profile(conn, "demo")
        assert second["generated_at_epoch"] == first["generated_at_epoch"]


    finally:
        conn.close()


def test_refresh_force_rebuilds(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        _seed_session(conn, "s1", 1700000000, 1700001000, 3)
        _seed_session(conn, "s2", 1700100000, 1700101000, 4)
        conn.commit()
        first = refresh_work_profile(conn, "demo")
        time.sleep(1.05)  # ensure the generated_at_epoch advances
        second = refresh_work_profile(conn, "demo", force=True)
        assert second["generated_at_epoch"] > first["generated_at_epoch"]


    finally:
        conn.close()


# ── build_session_resume integration ───────────────────────────────────


def test_resume_includes_work_profile_block(tmp_path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        upsert_work_profile(conn, "demo", {
            "cadence": "5 sessions over 8 days · ~3 prompts/session",
            "top_files": "src/auth.py (×4)",
            "recurring_themes": "retry, cache, auth",
            "open_decisions": 2,
            "generated_at_epoch": int(time.time()),
        })
        text = build_session_resume(conn, "demo")
        assert "Your work profile" in text
        assert "5 sessions over 8 days" in text
        assert "src/auth.py" in text
        assert "retry" in text
        assert "2 decisions on file" in text
    finally:
        conn.close()


def test_resume_tolerates_missing_work_profile_table(tmp_path):
    """An old db without the v4 table must not crash the resume — it
    should skip the block and render the rest."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        conn.execute("DROP TABLE work_profile")
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) VALUES "
            "('Use SQLite', 'simple', 'manual', 1700000000, 't')"
        )
        conn.commit()
        text = build_session_resume(conn, "demo")
        assert "Use SQLite" in text
        assert "Your work profile" not in text
    finally:
        conn.close()


def test_resume_empty_when_no_state(tmp_path):
    """Virgin db with no profile, no rollups, no decisions, no savings."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert build_session_resume(conn, "demo") == ""
    finally:
        conn.close()
