"""Tests for the `cce savings` CLI command."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from context_engine.cli import main
from context_engine.config import Config


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def stats_dir(tmp_path):
    """Create a temporary storage directory with a stats.json file."""
    project_name = "test-project"
    project_dir = tmp_path / project_name
    project_dir.mkdir(parents=True)
    stats = {"queries": 10, "full_file_tokens": 5000, "raw_tokens": 3000, "served_tokens": 2000}
    (project_dir / "stats.json").write_text(json.dumps(stats))
    return tmp_path, project_name


def _invoke_savings(runner, storage_path, project_name, *args):
    """Helper: invoke `cce savings` with a patched storage path."""
    from unittest.mock import patch

    config = Config(storage_path=str(storage_path))
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / project_name
        cwd_path.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            result = runner.invoke(main, ["savings", *args])
    return result


def test_savings_no_data(runner, tmp_path):
    """savings command reports no usage when stats.json is absent."""
    config = Config(storage_path=str(tmp_path))
    with runner.isolated_filesystem():
        project_dir = Path.cwd() / "my-project"
        project_dir.mkdir()
        from unittest.mock import patch
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=project_dir):
            result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "No usage recorded" in result.output


def test_savings_with_data(runner, stats_dir):
    """savings command shows the honest split between retrieval and compression."""
    storage_path, project_name = stats_dir
    result = _invoke_savings(runner, storage_path, project_name)
    assert result.exit_code == 0
    assert "test-project" in result.output
    # Legacy project: saved = 5000 - 2000 = 3000, all attributed to input.
    assert "Input savings" in result.output
    assert "3.0k" in result.output      # total saved tokens
    assert "Total saved" in result.output
    # Split percentages reported separately so the headline doesn't conflate them.
    # Retrieval = (5000-3000)/5000 = 40%; Compression = (3000-2000)/3000 = 33%.
    assert "40%" in result.output
    assert "33%" in result.output
    # Bucket / fallback labels — case-insensitive so renderer styling can shift
    # without churning the test.
    lower = result.output.lower()
    assert "retrieval" in lower
    assert "compression" in lower


def test_savings_json_output(runner, stats_dir):
    """--json flag returns valid JSON with correct fields."""
    storage_path, project_name = stats_dir
    result = _invoke_savings(runner, storage_path, project_name, "--json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["queries"] == 10
    assert data["full_file_tokens"] == 5000
    assert data["served_tokens"] == 2000
    assert data["tokens_saved"] == 3000
    assert data["savings_pct"] == 60


def test_savings_json_no_data(runner, tmp_path):
    """--json flag returns zeroed JSON when no usage data exists."""
    config = Config(storage_path=str(tmp_path))
    from unittest.mock import patch
    with runner.isolated_filesystem():
        project_dir = Path.cwd() / "no-data-project"
        project_dir.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=project_dir):
            result = runner.invoke(main, ["savings", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["full_file_tokens"] == 0
    assert data["tokens_saved"] == 0


def test_savings_all_projects(runner, tmp_path):
    """--all shows a report for each project with recorded stats."""
    for name, full, served in [("proj-a", 8000, 3000), ("proj-b", 2000, 1500)]:
        d = tmp_path / name
        d.mkdir()
        (d / "stats.json").write_text(json.dumps({
            "queries": 5, "full_file_tokens": full, "raw_tokens": full, "served_tokens": served,
        }))

    config = Config(storage_path=str(tmp_path))
    from unittest.mock import patch
    with runner.isolated_filesystem():
        cwd = Path.cwd() / "proj-a"
        cwd.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["savings", "--all"])
    assert result.exit_code == 0
    assert "proj-a" in result.output
    assert "proj-b" in result.output


def test_savings_all_projects_json(runner, tmp_path):
    """--all --json returns a list of project entries."""
    for name, full, served in [("p1", 4000, 1000), ("p2", 6000, 3000)]:
        d = tmp_path / name
        d.mkdir()
        (d / "stats.json").write_text(json.dumps({
            "queries": 3, "full_file_tokens": full, "raw_tokens": full, "served_tokens": served,
        }))

    config = Config(storage_path=str(tmp_path))
    from unittest.mock import patch
    with runner.isolated_filesystem():
        cwd = Path.cwd() / "p1"
        cwd.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["savings", "--all", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    projects = {p["project"]: p for p in data["projects"]}
    assert "p1" in projects
    assert projects["p1"]["tokens_saved"] == 3000
    assert projects["p2"]["savings_pct"] == 50


def test_savings_all_projects_empty(runner, tmp_path):
    """--all-projects with no data reports no usage."""
    config = Config(storage_path=str(tmp_path))
    from unittest.mock import patch
    with runner.isolated_filesystem():
        cwd = Path.cwd() / "empty-project"
        cwd.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["savings", "--all"])
    assert result.exit_code == 0
    assert "No usage recorded" in result.output


def test_savings_badge_with_data(runner, stats_dir):
    """--badge outputs Markdown badge and shields.io URL."""
    storage_path, project_name = stats_dir
    result = _invoke_savings(runner, storage_path, project_name, "--badge")
    assert result.exit_code == 0
    assert "shields.io" in result.output
    assert "![" in result.output  # Markdown badge syntax
    assert "CCE" in result.output


def test_savings_badge_no_data(runner, tmp_path):
    """--badge with no data prints a helpful message instead of a badge."""
    config = Config(storage_path=str(tmp_path))
    from unittest.mock import patch
    with runner.isolated_filesystem():
        cwd = Path.cwd() / "empty-project"
        cwd.mkdir()
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["savings", "--badge"])
    assert result.exit_code == 0
    assert "No savings data" in result.output


def test_savings_shortcut_badge(runner, stats_dir):
    """cce-savings --badge shortcut also outputs a shields.io badge."""
    from context_engine.cli import savings_shortcut
    from unittest.mock import patch

    storage_path, project_name = stats_dir
    config = Config(storage_path=str(storage_path))

    import click
    with runner.isolated_filesystem():
        cwd_path = Path.cwd() / project_name
        cwd_path.mkdir(parents=True, exist_ok=True)

        @click.command()
        @click.option("--json", "as_json", is_flag=True)
        @click.option("--all", "all_projects", is_flag=True)
        @click.option("--badge", "show_badge", is_flag=True)
        def _cmd(as_json, all_projects, show_badge):
            from context_engine.cli import _print_savings_badge, _run_savings_report
            if show_badge:
                _print_savings_badge(config)
                return
            _run_savings_report(config, as_json=as_json, all_projects=all_projects)

        with patch("context_engine.cli.Path.cwd", return_value=cwd_path):
            result = runner.invoke(_cmd, ["--badge"])

    assert result.exit_code == 0
    assert "shields.io" in result.output
