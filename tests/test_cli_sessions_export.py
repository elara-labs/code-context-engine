"""Tests for `cce sessions export`.

Covers: markdown rendering, JSON output, --since/--until filtering,
empty-db case, write-to-file vs stdout.
"""
from __future__ import annotations

import json
import time
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


@pytest.fixture()
def project_with_history(tmp_path):
    """Stage a project memory.db with two decisions + two turn summaries
    spread across time so --since/--until filters can be exercised.
    """
    project_name = "demo"
    project_dir = tmp_path / project_name
    project_dir.mkdir()
    conn = memory_db.connect(project_dir / "memory.db")
    try:
        # Old decision (Q1)
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'manual', ?, ?)",
            ("Adopt JWT for auth", "stateless + standard",
             1700000000, "2023-11-14T22:13:20"),
        )
        # Recent decision
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) "
            "VALUES (?, ?, 'manual', ?, ?)",
            ("Switch to bge-small embeddings", "small + good enough",
             int(time.time()), "2026-04-28T10:00:00"),
        )
        # A session + turn summary so the export has both kinds of rows.
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, "
            "started_at, status) VALUES ('s1', 'demo', 0, '2026-01-01', 'completed')"
        )
        conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) "
            "VALUES ('s1', 1, 'Discussed JWT migration plan', 'extractive', ?)",
            (int(time.time()),),
        )
        conn.commit()
    finally:
        conn.close()
    return tmp_path, project_name


def _invoke_export(runner, storage_path, project_name, *args):
    config = Config(storage_path=str(storage_path))
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / project_name
        cwd_path.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            return runner.invoke(main, ["sessions", "export", *args])


def test_export_default_markdown_to_stdout(runner, project_with_history):
    storage, project = project_with_history
    result = _invoke_export(runner, storage, project)
    assert result.exit_code == 0, result.output
    assert "# demo — session export" in result.output
    assert "Adopt JWT for auth" in result.output
    assert "Switch to bge-small embeddings" in result.output
    assert "Discussed JWT migration plan" in result.output


def test_export_json_format(runner, project_with_history):
    storage, project = project_with_history
    result = _invoke_export(runner, storage, project, "--format", "json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["project"] == "demo"
    assert len(data["decisions"]) == 2
    assert len(data["turn_summaries"]) == 1
    titles = [d["decision"] for d in data["decisions"]]
    assert "Adopt JWT for auth" in titles


def test_export_since_filter_drops_old(runner, project_with_history):
    """--since 2026-01-01 excludes the 2023 decision."""
    storage, project = project_with_history
    result = _invoke_export(runner, storage, project,
                            "--since", "2026-01-01", "--format", "json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    titles = [d["decision"] for d in data["decisions"]]
    assert "Adopt JWT for auth" not in titles
    assert "Switch to bge-small embeddings" in titles


def test_export_until_filter_keeps_old(runner, project_with_history):
    """--until 2024-01-01 keeps only the 2023 decision."""
    storage, project = project_with_history
    result = _invoke_export(runner, storage, project,
                            "--until", "2024-01-01", "--format", "json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    titles = [d["decision"] for d in data["decisions"]]
    assert titles == ["Adopt JWT for auth"]


def test_export_to_file(runner, project_with_history, tmp_path):
    storage, project = project_with_history
    out_path = tmp_path / "out.md"
    result = _invoke_export(runner, storage, project, "-o", str(out_path))
    assert result.exit_code == 0
    text = out_path.read_text()
    assert "Adopt JWT for auth" in text
    # Stdout shows summary only.
    assert "Wrote " in result.output


def test_export_no_db(runner, tmp_path):
    """Project with no memory.db prints helpful message, exits 0."""
    config = Config(storage_path=str(tmp_path))
    with runner.isolated_filesystem():
        cwd = Path.cwd() / "noproject"
        cwd.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["sessions", "export"])
    assert result.exit_code == 0
    assert "nothing to export" in result.output


def test_export_invalid_date_raises(runner, project_with_history):
    storage, project = project_with_history
    result = _invoke_export(runner, storage, project, "--since", "not-a-date")
    assert result.exit_code != 0
    assert "could not parse" in result.output.lower()
