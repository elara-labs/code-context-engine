"""Tests for the `cce sessions status` CLI command."""
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


def _invoke(runner, storage_path, project_name):
    config = Config(storage_path=str(storage_path))
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / project_name
        cwd_path.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            return runner.invoke(main, ["sessions", "status"])


def test_status_when_db_missing(runner, tmp_path):
    """A project that's never run `cce serve` reports "not initialised"."""
    result = _invoke(runner, tmp_path, "fresh-project")
    assert result.exit_code == 0, result.output
    assert "not initialised" in result.output
    assert "cce serve" in result.output


def test_status_reports_db_size_and_schema(runner, tmp_path):
    """A populated db surfaces session counts, decisions, schema version."""
    project_name = "demo"
    storage_base = tmp_path / project_name
    storage_base.mkdir(parents=True, exist_ok=True)
    db_path = memory_db.memory_db_path(storage_base)
    conn = memory_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
            "status) VALUES ('s1', 'demo', 1700000000, '2023-11-14T22:13:20', "
            "'completed')"
        )
        conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) VALUES ('d', 'r', 'manual', "
            "1700000000, '2023-11-14T22:13:20')"
        )
        conn.commit()
    finally:
        conn.close()

    result = _invoke(runner, tmp_path, project_name)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "schema:" in out
    assert "v2" in out
    assert "completed=1" in out
    assert "manual=1" in out
    assert "queue:" in out
    assert "drained" in out


def test_status_flags_stuck_queue(runner, tmp_path):
    """A pending row with attempts>1 surfaces an explicit warning."""
    project_name = "stuck"
    storage_base = tmp_path / project_name
    storage_base.mkdir(parents=True, exist_ok=True)
    conn = memory_db.connect(memory_db.memory_db_path(storage_base))
    try:
        conn.execute(
            "INSERT INTO pending_compressions (kind, session_id, prompt_number, "
            "enqueued_at_epoch, attempts) VALUES ('turn', 's1', 1, 1700000000, 4)"
        )
        conn.commit()
    finally:
        conn.close()

    result = _invoke(runner, tmp_path, project_name)
    assert result.exit_code == 0, result.output
    assert "1 pending" in result.output
    assert "max attempts=4" in result.output
