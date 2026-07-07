"""Regression tests for hook rendezvous collision fix (issue #128).

Two projects with the same directory basename (e.g. both called ``api``)
previously resolved to the same port file path in the hook script, so
whichever project started its hook server last would silently capture
events for both projects.

The fix bakes the slug-based port file path into the hook command at
``cce init`` time via the ``CCE_PORT_FILE`` env var prefix.
"""
from __future__ import annotations

import json
from pathlib import Path

from context_engine.memory import hook_installer as hi


def test_two_same_basename_projects_get_different_port_files(tmp_path: Path):
    """install_settings with port_file_path stamps each project's hook with
    its own port file path, so two projects named 'api' at different absolute
    paths do not collide.
    """
    # Two projects with the same basename but different absolute locations
    proj_a = tmp_path / "workspace_a" / "api"
    proj_b = tmp_path / "workspace_b" / "api"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)

    port_file_a = tmp_path / "storage_a" / "serve.port"
    port_file_b = tmp_path / "storage_b" / "serve.port"

    hi.install_settings(proj_a, port_file_path=port_file_a)
    hi.install_settings(proj_b, port_file_path=port_file_b)

    settings_a = json.loads((proj_a / ".claude" / "settings.json").read_text())
    settings_b = json.loads((proj_b / ".claude" / "settings.json").read_text())

    def _first_cmd(settings: dict) -> str:
        return settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    cmd_a = _first_cmd(settings_a)
    cmd_b = _first_cmd(settings_b)

    # Each hook command must reference the project-specific port file
    assert str(port_file_a) in cmd_a, f"port_file_a not in cmd_a: {cmd_a}"
    assert str(port_file_b) in cmd_b, f"port_file_b not in cmd_b: {cmd_b}"

    # The two commands must differ (different port file paths)
    assert cmd_a != cmd_b, "Hook commands for distinct projects are identical — collision not fixed"


def test_install_settings_without_port_file_path_omits_env_var(tmp_path: Path):
    """Backward compat: calling install_settings without port_file_path
    produces a command without CCE_PORT_FILE, falling back to the basename
    resolution in the hook script.
    """
    proj = tmp_path / "myproject"
    proj.mkdir()

    hi.install_settings(proj)  # no port_file_path

    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "CCE_PORT_FILE" not in cmd
