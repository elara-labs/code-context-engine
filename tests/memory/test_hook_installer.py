"""Tests for hook script + settings.json installation."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from context_engine.memory import hook_installer as hi


def test_install_hook_script_writes_executable(tmp_path: Path):
    target = tmp_path / "cce_hook.sh"
    created = hi.install_hook_script(target)
    assert created is True
    assert target.exists()
    body = target.read_text()
    assert body.startswith("#!/bin/sh")
    assert "curl" in body
    # Owner exec bit set.
    assert target.stat().st_mode & stat.S_IXUSR


def test_install_hook_script_idempotent(tmp_path: Path):
    target = tmp_path / "cce_hook.sh"
    assert hi.install_hook_script(target) is True
    assert hi.install_hook_script(target) is False


def test_install_settings_adds_all_5_hooks(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    summary = hi.install_settings(project)
    assert sorted(summary["added"]) == sorted(hi.LIFECYCLE_HOOKS)
    settings_path = project / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text())
    for hook_name in hi.LIFECYCLE_HOOKS:
        bucket = data["hooks"][hook_name]
        assert len(bucket) == 1
        cmd = bucket[0]["hooks"][0]["command"]
        assert hi.HOOK_SCRIPT_NAME in cmd
        assert hook_name in cmd


def test_windows_hook_script_body_when_platform_win(monkeypatch):
    """On Windows, install_hook_script writes the .cmd body."""
    monkeypatch.setattr(hi, "_is_windows", lambda: True)
    body = hi._hook_script_body()
    assert body.startswith("@echo off")
    assert "%PORT_FILE%" in body
    assert "exit /b 0" in body


def test_posix_hook_script_body_when_platform_not_win(monkeypatch):
    monkeypatch.setattr(hi, "_is_windows", lambda: False)
    body = hi._hook_script_body()
    assert body.startswith("#!/bin/sh")
    assert "${PORT_FILE}" in body
    assert "curl -sf" in body


def test_install_settings_quotes_command_for_paths_with_spaces(tmp_path: Path, monkeypatch):
    """A HOOK_PATH containing a space must be shell-quoted in the command.

    Without quoting, Claude Code passes `command` to sh -c, which would
    tokenise on the space and try to exec the wrong binary. This is the
    classic /Users/Firstname Lastname onboarding footgun on macOS.
    """
    spaced_dir = tmp_path / "Alice Smith" / ".cce" / "hooks"
    spaced_dir.mkdir(parents=True)
    spaced_path = spaced_dir / hi.HOOK_SCRIPT_NAME
    monkeypatch.setattr(hi, "HOOK_PATH", spaced_path)
    project = tmp_path / "proj"
    project.mkdir()
    hi.install_settings(project)
    data = json.loads((project / ".claude" / "settings.json").read_text())
    cmd = data["hooks"][hi.LIFECYCLE_HOOKS[0]][0]["hooks"][0]["command"]
    # The path must be shell-quoted (single quotes around the spaced path),
    # and the hook name must appear unquoted after a space.
    assert "'" in cmd, f"path with space should be shell-quoted: {cmd}"
    assert "Alice Smith" in cmd
    assert cmd.endswith(f" {hi.LIFECYCLE_HOOKS[0]}")


def test_install_settings_idempotent(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    s1 = hi.install_settings(project)
    s2 = hi.install_settings(project)
    assert sorted(s1["added"]) == sorted(hi.LIFECYCLE_HOOKS)
    assert s2["added"] == []
    assert sorted(s2["skipped"]) == sorted(hi.LIFECYCLE_HOOKS)
    # No duplicate entries created.
    data = json.loads((project / ".claude" / "settings.json").read_text())
    for hook_name in hi.LIFECYCLE_HOOKS:
        assert len(data["hooks"][hook_name]) == 1


def test_install_settings_preserves_user_hooks(tmp_path: Path):
    project = tmp_path / "myproj"
    settings_dir = project / ".claude"
    settings_dir.mkdir(parents=True)
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{
                "matcher": "",
                "hooks": [{"type": "command", "command": "echo my-existing-hook"}],
            }],
        },
        "enabledPlugins": {"some-plugin": True},
    }))

    summary = hi.install_settings(project)
    assert "SessionStart" in summary["added"]

    data = json.loads(settings_path.read_text())
    # User's plugin config preserved.
    assert data["enabledPlugins"] == {"some-plugin": True}
    # User's existing hook still present alongside ours.
    sb = data["hooks"]["SessionStart"]
    assert len(sb) == 2
    cmds = [entry["hooks"][0]["command"] for entry in sb]
    assert any("my-existing-hook" in c for c in cmds)
    assert any(hi.HOOK_SCRIPT_NAME in c for c in cmds)


def test_uninstall_removes_only_our_entries(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    hi.install_settings(project)
    settings_path = project / ".claude" / "settings.json"
    # Add a user-owned hook into the same bucket.
    data = json.loads(settings_path.read_text())
    data["hooks"]["SessionStart"].append({
        "matcher": "",
        "hooks": [{"type": "command", "command": "echo user"}],
    })
    settings_path.write_text(json.dumps(data))

    summary = hi.uninstall_settings(project)
    assert sorted(summary["removed"]) == sorted(hi.LIFECYCLE_HOOKS)

    data = json.loads(settings_path.read_text())
    # SessionStart bucket still exists with the user's hook.
    cmds = [entry["hooks"][0]["command"] for entry in data["hooks"]["SessionStart"]]
    assert cmds == ["echo user"]
    # Other buckets removed entirely (they had only ours).
    for hook_name in ["UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"]:
        assert hook_name not in data["hooks"]


def test_uninstall_on_missing_settings_is_noop(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    summary = hi.uninstall_settings(project)
    assert summary["removed"] == []
