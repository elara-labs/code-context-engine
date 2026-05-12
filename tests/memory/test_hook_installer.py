"""Tests for hook script + settings.json installation."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path


from context_engine.memory import hook_installer as hi


def test_install_hook_script_writes_executable(tmp_path: Path):
    target = tmp_path / "cce_hook.sh"
    created = hi.install_hook_script(target)
    assert created is True
    assert target.exists()
    body = target.read_text()
    if sys.platform == "win32":
        assert body.startswith("@echo off")
    else:
        assert body.startswith("#!/bin/sh")
        assert "curl" in body
        # Owner exec bit set (not applicable on Windows).
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


# ─── Issue #67 regression coverage: liveness probe in hook scripts ─────


def test_posix_hook_has_tcp_liveness_probe(monkeypatch):
    """POSIX hook must short-circuit when nothing's listening on the port
    so a stale serve.port doesn't waste 1-2s/curl call (#67)."""
    monkeypatch.setattr(hi, "_is_windows", lambda: False)
    body = hi._hook_script_body()
    # The probe uses bash's /dev/tcp pseudo-device.
    assert "/dev/tcp/127.0.0.1" in body
    # The probe gates curl: if it fails we exit BEFORE the curl call.
    probe_pos = body.index("/dev/tcp/127.0.0.1")
    curl_pos = body.index("curl -sf")
    assert probe_pos < curl_pos, (
        "Liveness probe must run before any curl invocation"
    )


def test_windows_hook_has_tcp_liveness_probe(monkeypatch):
    monkeypatch.setattr(hi, "_is_windows", lambda: True)
    body = hi._hook_script_body()
    # TcpClient is the cheapest port probe available without extra deps.
    assert "TcpClient" in body
    probe_pos = body.index("TcpClient")
    curl_pos = body.index("curl -sf")
    assert probe_pos < curl_pos


# ─── Copilot review: PORT must be validated before shell interpolation ─


def test_posix_hook_rejects_non_numeric_port(monkeypatch):
    """Corrupted/hostile serve.port (containing $() or backticks) must
    not be interpolated into the bash -c command (Copilot security
    review on #70)."""
    monkeypatch.setattr(hi, "_is_windows", lambda: False)
    body = hi._hook_script_body()
    assert "*[!0-9]*" in body, "POSIX script must reject non-digit PORT"
    assert "-le 65535" in body, "POSIX script must cap PORT at 65535"
    validation_pos = body.index("*[!0-9]*")
    probe_pos = body.index("/dev/tcp/127.0.0.1")
    curl_pos = body.index("curl -sf")
    assert validation_pos < probe_pos < curl_pos


def test_windows_hook_rejects_non_numeric_port(monkeypatch):
    monkeypatch.setattr(hi, "_is_windows", lambda: True)
    body = hi._hook_script_body()
    assert "findstr /R" in body
    assert "LSS 1" in body
    assert "GTR 65535" in body
    validation_pos = body.index("findstr /R")
    probe_pos = body.index("TcpClient")
    curl_pos = body.index("curl -sf")
    assert validation_pos < probe_pos < curl_pos


def test_install_settings_uses_cmd_quoting_on_windows(tmp_path: Path, monkeypatch):
    """On Windows, the hook command must use cmd.exe-style double quotes
    around the path. POSIX single-quotes (shlex.quote) would not dequote
    correctly under cmd.exe and would silently break capture."""
    monkeypatch.setattr(hi, "_is_windows", lambda: True)
    spaced_dir = tmp_path / "Alice Smith" / ".cce" / "hooks"
    spaced_dir.mkdir(parents=True)
    spaced_path = spaced_dir / "cce_hook.cmd"
    monkeypatch.setattr(hi, "HOOK_PATH", spaced_path)
    project = tmp_path / "proj"
    project.mkdir()
    hi.install_settings(project)
    data = json.loads((project / ".claude" / "settings.json").read_text())
    cmd = data["hooks"][hi.LIFECYCLE_HOOKS[0]][0]["hooks"][0]["command"]
    assert cmd.startswith('"'), f"cmd.exe path should start with \": {cmd}"
    assert "Alice Smith" in cmd
    assert "'" not in cmd, f"Windows must not use POSIX single quotes: {cmd}"


def test_install_settings_quotes_command_for_paths_with_spaces(tmp_path: Path, monkeypatch):
    """A HOOK_PATH containing a space must be quoted in the command.

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
    # The path must be quoted: single quotes on POSIX, double quotes on Windows.
    assert "Alice Smith" in cmd
    assert cmd.endswith(f" {hi.LIFECYCLE_HOOKS[0]}")
    if sys.platform == "win32":
        assert '"' in cmd, f"Windows path should be double-quoted: {cmd}"
    else:
        assert "'" in cmd, f"POSIX path should be single-quoted: {cmd}"


def test_session_start_matcher_covers_clear_and_compact(tmp_path: Path):
    """SessionStart must fire on `/clear` and `/compact` too, not just startup.

    Without that, the resume context (handle_session_start's stdout
    response) doesn't re-inject after the user clears or compacts the
    conversation — exactly when they need it most.
    """
    project = tmp_path / "myproj"
    project.mkdir()
    hi.install_settings(project)
    data = json.loads((project / ".claude" / "settings.json").read_text())
    matcher = data["hooks"]["SessionStart"][0]["matcher"]
    for trigger in ("startup", "clear", "compact"):
        assert trigger in matcher, f"SessionStart missing {trigger!r}: {matcher!r}"
    # Other hooks keep an empty matcher (fire on every event).
    for hook_name in ("UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"):
        assert data["hooks"][hook_name][0]["matcher"] == ""


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
