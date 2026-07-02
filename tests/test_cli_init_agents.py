"""Tests for `cce init --agent` target selection and generated files."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from context_engine.cli import _init_editor_targets, main
from context_engine.editors import _project_slug


async def _noop_index(*args, **kwargs):
    return None


def test_init_pi_writes_config_and_agents_md(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = runner.invoke(main, ["init", "--agent", "pi"], catch_exceptions=False, obj={})

    assert result.exit_code == 0

    # MCP config: uses same .mcp.json / mcpServers format as Claude Code
    mcp = json.loads((project / ".mcp.json").read_text())
    assert mcp["mcpServers"]["context-engine"]["command"] == "/usr/bin/cce"
    assert mcp["mcpServers"]["context-engine"]["args"] == ["serve", "--project-dir", str(project)]

    # Instruction file: AGENTS.md (which pi auto-loads)
    assert (project / "AGENTS.md").exists()
    assert "Context Engine (CCE)" in (project / "AGENTS.md").read_text()

    # No CLAUDE.md or other editor-specific instruction files
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".github" / "copilot-instructions.md").exists()
    assert not (project / ".cursorrules").exists()
    assert not (project / "opencode.json").exists()


def test_init_pi_does_not_write_claude_specific_content(tmp_path, monkeypatch):
    """`--agent pi` must NOT write CLAUDE.md or install Claude-specific
    session hooks / memory hooks — those are Claude-only features."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = runner.invoke(main, ["init", "--agent", "pi"], catch_exceptions=False, obj={})

    assert result.exit_code == 0
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".claude" / "settings.local.json").exists()


def test_init_pi_target_resolves_to_pi_only():
    assert _init_editor_targets(Path("/tmp/anywhere"), "pi") == {"pi"}


def test_init_all_targets_every_known_editor():
    from context_engine.editors import EDITORS
    assert _init_editor_targets(Path("/tmp/anywhere"), "all") == set(EDITORS.keys())


def test_init_codex_writes_codex_config_and_agents_md(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = runner.invoke(main, ["init", "--agent", "codex"], catch_exceptions=False, obj={})

    assert result.exit_code == 0
    assert (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".mcp.json").exists()

    codex_config = fake_home / ".codex" / "config.toml"
    parsed = tomllib.loads(codex_config.read_text())
    entry = parsed["mcp_servers"][f"cce-{_project_slug(project)}"]
    assert entry["command"] == "/usr/bin/cce"
    assert entry["args"] == ["serve", "--project-dir", str(project)]


def test_init_claude_does_not_write_other_instruction_files(tmp_path, monkeypatch):
    """`--agent claude` must not append CCE block to pre-existing AGENTS.md or
    copilot-instructions.md just because their files happen to exist."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Existing agents\n")
    (project / ".github").mkdir()
    (project / ".github" / "copilot-instructions.md").write_text("# Existing copilot\n")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.setattr("context_engine.cli._check_memory_capture_reachable", lambda config, project: None)
    monkeypatch.setattr("context_engine.cli._ensure_session_hook", lambda project: None)
    monkeypatch.setattr("context_engine.cli._install_memory_hooks", lambda project: None)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"), patch(
        "context_engine.utils.resolve_cce_binary", return_value="/usr/bin/cce"
    ):
        result = runner.invoke(main, ["init", "--agent", "claude"], catch_exceptions=False, obj={})

    assert result.exit_code == 0
    assert (project / "AGENTS.md").read_text() == "# Existing agents\n"
    assert (project / ".github" / "copilot-instructions.md").read_text() == "# Existing copilot\n"
    assert (project / "CLAUDE.md").exists()
    assert (project / ".mcp.json").exists()


def test_init_copilot_writes_vscode_config_and_copilot_instructions(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = runner.invoke(main, ["init", "--agent", "copilot"], catch_exceptions=False, obj={})

    assert result.exit_code == 0
    assert (project / ".github" / "copilot-instructions.md").exists()
    vscode_mcp = json.loads((project / ".vscode" / "mcp.json").read_text())
    assert vscode_mcp["servers"]["context-engine"]["command"] == "/usr/bin/cce"
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".mcp.json").exists()
    assert not (project / "AGENTS.md").exists()


def test_init_all_writes_every_editor_config_and_instruction_file(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr("context_engine.cli._preflight_check", lambda config: None)
    monkeypatch.setattr("context_engine.cli._run_index", _noop_index)
    monkeypatch.setattr("context_engine.cli._check_memory_capture_reachable", lambda config, project: None)
    monkeypatch.setattr("context_engine.cli._ensure_session_hook", lambda project: None)
    monkeypatch.setattr("context_engine.cli._install_memory_hooks", lambda project: None)
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"), patch(
        "context_engine.utils.resolve_cce_binary", return_value="/usr/bin/cce"
    ):
        result = runner.invoke(main, ["init", "--agent", "all"], catch_exceptions=False, obj={})

    assert result.exit_code == 0

    # Instruction files for every editor that has one (Claude is via _ensure_claude_md;
    # OpenCode has no instruction file).
    assert (project / "CLAUDE.md").exists()
    assert (project / "AGENTS.md").exists()
    assert (project / ".github" / "copilot-instructions.md").exists()
    assert (project / ".cursorrules").exists()
    assert (project / "GEMINI.md").exists()
    assert (project / "TABNINE.md").exists()

    # MCP configs for every editor.
    claude_mcp = json.loads((project / ".mcp.json").read_text())
    assert claude_mcp["mcpServers"]["context-engine"]["command"] == "/usr/bin/cce"

    vscode_mcp = json.loads((project / ".vscode" / "mcp.json").read_text())
    assert vscode_mcp["servers"]["context-engine"]["command"] == "/usr/bin/cce"

    cursor_mcp = json.loads((project / ".cursor" / "mcp.json").read_text())
    assert cursor_mcp["mcpServers"]["context-engine"]["command"] == "/usr/bin/cce"

    gemini_mcp = json.loads((project / ".gemini" / "settings.json").read_text())
    assert gemini_mcp["mcpServers"]["context-engine"]["command"] == "/usr/bin/cce"

    opencode_mcp = json.loads((project / "opencode.json").read_text())
    assert opencode_mcp["mcp"]["context-engine"]["command"][0] == "/usr/bin/cce"

    tabnine_mcp = json.loads((project / ".tabnine" / "agent" / "settings.json").read_text())
    assert tabnine_mcp["mcpServers"]["context-engine"]["command"] == "/usr/bin/cce"

    codex_config = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
    assert f"cce-{_project_slug(project)}" in codex_config["mcp_servers"]
