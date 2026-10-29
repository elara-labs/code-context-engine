"""Tests for `cce serve` config loading.

Regression for the 2026-04-27 review: with --project-dir we used to chdir
AFTER the config was loaded from the launch cwd, so the project's
.context-engine.yaml was silently ignored.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from context_engine.cli import main
from context_engine.config import PROJECT_CONFIG_NAME


@pytest.fixture()
def runner():
    return CliRunner()


def test_serve_reloads_config_from_project_dir(runner, tmp_path):
    """`cce serve --project-dir P` must read P/.context-engine.yaml even when
    invoked from a different cwd."""
    # Project with a project-specific config.
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    (project_dir / PROJECT_CONFIG_NAME).write_text(
        "embedding:\n  model: project-specific-model\n"
    )

    # Launch cwd: a *different* directory with NO project config.
    launch_dir = tmp_path / "launch_cwd"
    launch_dir.mkdir()

    captured = {}

    async def fake_run_serve(config):
        captured["embedding_model"] = config.embedding_model

    original_cwd = Path.cwd()
    try:
        os.chdir(launch_dir)
        with patch("context_engine.cli._run_serve", new=fake_run_serve):
            result = runner.invoke(
                main, ["serve", "--project-dir", str(project_dir)]
            )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0, result.output
    assert captured.get("embedding_model") == "project-specific-model", (
        "serve --project-dir loaded config from launch cwd, not the target "
        "project — the project's .context-engine.yaml was ignored."
    )


@pytest.mark.parametrize(
    ("value", "disabled"),
    [(None, False), ("", False), ("0", False), ("1", True), ("true", True), (" Yes ", True), ("on", True)],
)
def test_hook_server_disabled_by_env(monkeypatch, value, disabled):
    """Headless agent runs set CCE_NO_HOOK_SERVER so their `cce serve` never
    touches serve.port (their host may kill it without cleanup)."""
    from context_engine import cli

    if value is None:
        monkeypatch.delenv(cli.NO_HOOK_SERVER_ENV, raising=False)
    else:
        monkeypatch.setenv(cli.NO_HOOK_SERVER_ENV, value)
    assert cli._hook_server_disabled() is disabled
