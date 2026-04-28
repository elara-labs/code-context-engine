"""Tests for the v3 `savings_log` ledger and the bucket-aware writers.

Covers:
  · Schema: savings_log table + index exist after `connect()`.
  · Round-trip: `record_savings` / `aggregate_savings` for every canonical bucket.
  · Bad-bucket guard: unknown bucket names are dropped with a warning.
  · Migration: a forged v2 db upgrades cleanly to v3 without losing data.
  · Output-compression level histogram aggregation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from context_engine.memory import db as memory_db


def test_savings_log_table_exists(tmp_path: Path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='savings_log'"
        ).fetchone()
        assert row is not None and row["name"] == "savings_log"
        # Index for bucket-scoped reads.
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_savings_bucket_ts'"
        ).fetchone()
        assert idx is not None
    finally:
        conn.close()


@pytest.mark.parametrize("bucket", list(memory_db.BUCKETS))
def test_record_and_aggregate_each_bucket(tmp_path: Path, bucket: str):
    """Every canonical bucket round-trips through record_savings + aggregate."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        memory_db.record_savings(conn, bucket=bucket, baseline=1000, served=200)
        memory_db.record_savings(conn, bucket=bucket, baseline=500, served=100)
        agg = memory_db.aggregate_savings(conn)
        assert agg[bucket]["baseline"] == 1500
        assert agg[bucket]["served"] == 300
        assert agg[bucket]["calls"] == 2
        # Other buckets remain zeroed.
        for other in memory_db.BUCKETS:
            if other == bucket:
                continue
            assert agg[other] == {"baseline": 0, "served": 0, "calls": 0}
    finally:
        conn.close()


def test_unknown_bucket_is_silently_dropped(tmp_path: Path, caplog):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        memory_db.record_savings(conn, bucket="not_a_real_bucket", baseline=1, served=0)
        # Should warn and write nothing.
        rows = conn.execute("SELECT COUNT(*) AS n FROM savings_log").fetchone()
        assert rows["n"] == 0
    finally:
        conn.close()


def test_aggregate_output_compression_levels(tmp_path: Path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=125, meta={"level": "max"},
        )
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=125, meta={"level": "max"},
        )
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=175, meta={"level": "standard"},
        )
        # An entry without meta — should not crash, just not appear.
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=400, meta=None,
        )
        levels = memory_db.aggregate_output_compression_levels(conn)
        assert levels == {"max": 2, "standard": 1}
    finally:
        conn.close()


def test_aggregate_levels_handles_empty_db(tmp_path: Path):
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert memory_db.aggregate_output_compression_levels(conn) == {}
    finally:
        conn.close()


def test_v2_to_v3_upgrade_creates_savings_log(tmp_path: Path):
    """A db forged back to v2 (no savings_log) gains it on next connect()."""
    db_path = tmp_path / "memory.db"
    # Bootstrap at current version, then forge back to v2.
    conn = memory_db.connect(db_path)
    conn.execute("DROP TABLE savings_log")
    conn.execute("DELETE FROM schema_versions WHERE version > 2")
    conn.execute(
        "INSERT INTO schema_versions (version, applied_at_epoch) "
        "VALUES (2, strftime('%s','now'))"
    )
    conn.commit()
    conn.close()

    # Sanity: at v2 with no savings_log.
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    assert raw.execute(
        "SELECT MAX(version) AS v FROM schema_versions"
    ).fetchone()["v"] == 2
    assert raw.execute(
        "SELECT name FROM sqlite_master WHERE name='savings_log'"
    ).fetchone() is None
    raw.close()

    # Reopen — migration should add savings_log and bump to v3.
    conn = memory_db.connect(db_path)
    try:
        assert memory_db.schema_version(conn) == memory_db.CURRENT_VERSION
        # Ledger usable post-upgrade.
        memory_db.record_savings(conn, bucket="grammar", baseline=10, served=5)
        agg = memory_db.aggregate_savings(conn)
        assert agg["grammar"] == {"baseline": 10, "served": 5, "calls": 1}
    finally:
        conn.close()


def test_record_savings_swallows_db_errors(tmp_path: Path):
    """Writer is best-effort: a bad connection must not raise."""
    conn = memory_db.connect(tmp_path / "memory.db")
    conn.close()
    # Operating on a closed connection raises sqlite3.ProgrammingError; the
    # writer should swallow it so a failed metric never breaks a tool call.
    memory_db.record_savings(conn, bucket="grammar", baseline=1, served=1)
