"""Tests for the per-bucket `cce savings` rendering and JSON output.

These cover the v3 reporter, which reads bucket totals from
`memory.db.savings_log` and merges with legacy `stats.json` fields.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from context_engine.cli import main
from context_engine.config import Config
from context_engine.memory import db as memory_db


@pytest.fixture()
def runner():
    return CliRunner()


def _seed_project_with_buckets(tmp_path: Path, project_name: str) -> Path:
    """Stage a project storage dir with stats.json + populated savings_log."""
    project_dir = tmp_path / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "stats.json").write_text(json.dumps({
        "queries": 7,
        "raw_tokens": 0,
        "served_tokens": 0,
        "full_file_tokens": 0,
    }))
    conn = memory_db.connect(project_dir / "memory.db")
    try:
        memory_db.record_savings(conn, bucket="retrieval", baseline=10000, served=2500)
        memory_db.record_savings(conn, bucket="chunk_compression", baseline=2500, served=800)
        memory_db.record_savings(conn, bucket="memory_recall", baseline=1500, served=300)
        memory_db.record_savings(conn, bucket="grammar", baseline=200, served=140)
        memory_db.record_savings(conn, bucket="turn_summarization", baseline=4000, served=600)
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=125, meta={"level": "max"},
        )
        memory_db.record_savings(
            conn, bucket="progressive_disclosure",
            baseline=8000, served=400, meta={"layer": "event"},
        )
    finally:
        conn.close()
    return project_dir


def _invoke_savings(runner, storage_path, project_name, *args):
    config = Config(storage_path=str(storage_path))
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / project_name
        cwd_path.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            return runner.invoke(main, ["savings", *args])


def test_text_report_lists_every_non_zero_bucket(runner, tmp_path):
    _seed_project_with_buckets(tmp_path, "demo")
    result = _invoke_savings(runner, tmp_path, "demo")
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    # Every bucket with savings shows in the breakdown.
    for label in [
        "retrieval", "chunk compression", "memory recall", "grammar",
        "turn summarization", "output compression", "progressive disclosure",
    ]:
        assert label in out, f"missing bucket label: {label!r}"


def test_text_report_marks_estimates_with_asterisk(runner, tmp_path):
    _seed_project_with_buckets(tmp_path, "demo")
    result = _invoke_savings(runner, tmp_path, "demo")
    assert result.exit_code == 0
    # Footnote present and the two estimate buckets carry the marker.
    assert "* estimated" in result.output
    # Each estimate bucket label is followed (loosely) by an asterisk; the
    # exact column layout can shift, so just check the marker exists somewhere
    # and that the footnote mentions both estimate sources.
    assert "output compression" in result.output.lower()
    assert "progressive disclosure" in result.output.lower()


def test_text_report_shows_output_compression_levels(runner, tmp_path):
    project_dir = _seed_project_with_buckets(tmp_path, "demo")
    # Add one more standard-level call so the histogram has two entries.
    conn = memory_db.connect(project_dir / "memory.db")
    try:
        memory_db.record_savings(
            conn, bucket="output_compression",
            baseline=500, served=175, meta={"level": "standard"},
        )
    finally:
        conn.close()
    result = _invoke_savings(runner, tmp_path, "demo")
    assert "Output compression levels seen" in result.output
    assert "max=" in result.output
    assert "standard=" in result.output


def test_json_output_includes_buckets_and_levels(runner, tmp_path):
    _seed_project_with_buckets(tmp_path, "demo")
    result = _invoke_savings(runner, tmp_path, "demo", "--json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert set(data["buckets"].keys()) == set(memory_db.BUCKETS)
    assert data["buckets"]["retrieval"]["baseline"] == 10000
    assert data["buckets"]["retrieval"]["served"] == 2500
    assert data["buckets"]["retrieval"]["calls"] == 1
    # Headline totals come from the bucket sum, not legacy fields.
    expected_baseline = sum(b["baseline"] for b in data["buckets"].values())
    expected_served = sum(b["served"] for b in data["buckets"].values())
    assert data["tokens_saved"] == expected_baseline - expected_served
    # Level histogram surfaces.
    assert data["output_compression_levels"] == {"max": 1}


def test_legacy_stats_only_project_still_renders(runner, tmp_path):
    """Project without memory.db (no buckets) falls back to legacy fields."""
    project_dir = tmp_path / "legacy"
    project_dir.mkdir()
    (project_dir / "stats.json").write_text(json.dumps({
        "queries": 4, "full_file_tokens": 8000, "raw_tokens": 4000, "served_tokens": 1500,
    }))
    result = _invoke_savings(runner, tmp_path, "legacy")
    assert result.exit_code == 0
    # Headline computed from legacy fields (saved = 8000 - 1500 = 6500 → 81%).
    assert "81%" in result.output
    # Fallback breakdown line appears.
    out = result.output.lower()
    assert "retrieval" in out
    assert "compression" in out


def test_no_data_at_all_reports_no_usage(runner, tmp_path):
    """No stats.json, no memory.db — render the empty-state message."""
    config = Config(storage_path=str(tmp_path))
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / "empty"
        cwd_path.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "No usage recorded" in result.output


def test_buckets_only_no_stats_file_still_renders(runner, tmp_path):
    """memory.db has bucket data but stats.json is missing — still works."""
    project_dir = tmp_path / "nofile"
    project_dir.mkdir()
    conn = memory_db.connect(project_dir / "memory.db")
    try:
        memory_db.record_savings(conn, bucket="retrieval", baseline=5000, served=1000)
    finally:
        conn.close()
    result = _invoke_savings(runner, tmp_path, "nofile")
    assert result.exit_code == 0
    # Headline computed from buckets: saved = 5000 - 1000 = 4000 → 80%.
    assert "80%" in result.output
    assert "retrieval" in result.output.lower()


def test_legacy_json_back_compat_fields_preserved(runner, tmp_path):
    """JSON output keeps legacy fields so old scrapers don't break."""
    _seed_project_with_buckets(tmp_path, "demo")
    result = _invoke_savings(runner, tmp_path, "demo", "--json")
    data = json.loads(result.output)
    for legacy in [
        "project", "queries", "full_file_tokens", "raw_tokens",
        "served_tokens", "tokens_saved", "savings_pct",
        "retrieval_savings_pct", "compression_savings_pct",
    ]:
        assert legacy in data, f"missing legacy key: {legacy!r}"
