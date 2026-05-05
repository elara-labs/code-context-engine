"""Tests for `cce uninstall` CLAUDE.md cleanup.

Regression for the 2026-04-27 review: uninstall used to look for legacy
<!-- CCE:BEGIN --> / <!-- CCE:END --> markers, but `init` switched to
<!-- cce-block-version: N --> ... <!-- /cce-block -->, so the block was
never removed and CCE routing instructions stayed in CLAUDE.md.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from context_engine.cli import (
    main,
    _CCE_CLAUDE_MD_BLOCK,
    _CCE_CLAUDE_MD_VERSION_TAG,
    _CCE_CLAUDE_MD_END_MARKER,
    _CCE_CLAUDE_MD_MARKER,
)


@pytest.fixture()
def runner():
    return CliRunner()


def _run_uninstall_in(runner, project_dir: Path):
    original = Path.cwd()
    try:
        os.chdir(project_dir)
        return runner.invoke(main, ["uninstall", "--yes"])
    finally:
        os.chdir(original)


def test_uninstall_removes_versioned_block(runner, tmp_path):
    """Current `<!-- cce-block-version: 2 -->` block must be removed."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    user_content = "# My project\n\nSome user notes.\n\n"
    (project_dir / "CLAUDE.md").write_text(user_content + _CCE_CLAUDE_MD_BLOCK)

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output

    remaining = (project_dir / "CLAUDE.md").read_text()
    assert _CCE_CLAUDE_MD_VERSION_TAG not in remaining
    assert _CCE_CLAUDE_MD_END_MARKER not in remaining
    assert _CCE_CLAUDE_MD_MARKER not in remaining
    # User content is preserved.
    assert "Some user notes." in remaining


def test_uninstall_removes_legacy_marker_block(runner, tmp_path):
    """Older `<!-- CCE:BEGIN --> ... <!-- CCE:END -->` block path still works."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    legacy_block = (
        "<!-- CCE:BEGIN -->\nold cce instructions\n<!-- CCE:END -->\n"
    )
    (project_dir / "CLAUDE.md").write_text("# Notes\n\n" + legacy_block)

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output

    remaining = (project_dir / "CLAUDE.md").read_text()
    assert "CCE:BEGIN" not in remaining
    assert "CCE:END" not in remaining
    assert "# Notes" in remaining


def test_uninstall_deletes_claude_md_when_only_cce_block(runner, tmp_path):
    """If CLAUDE.md contained only the CCE block, the file is unlinked."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text(_CCE_CLAUDE_MD_BLOCK)

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output
    assert not (project_dir / "CLAUDE.md").exists()


def test_uninstall_deletes_mcp_json_when_only_cce(runner, tmp_path):
    """If .mcp.json only has context-engine, the file is deleted entirely."""
    import json
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mcp_data = {"mcpServers": {"context-engine": {"command": "cce", "args": ["serve"]}}}
    (project_dir / ".mcp.json").write_text(json.dumps(mcp_data))

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output
    assert not (project_dir / ".mcp.json").exists()
    assert "Removed .mcp.json" in result.output


def test_uninstall_keeps_mcp_json_with_other_servers(runner, tmp_path):
    """If .mcp.json has other servers, only CCE entry is removed."""
    import json
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mcp_data = {
        "mcpServers": {
            "context-engine": {"command": "cce", "args": ["serve"]},
            "other-tool": {"command": "other", "args": []},
        }
    }
    (project_dir / ".mcp.json").write_text(json.dumps(mcp_data))

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output
    assert (project_dir / ".mcp.json").exists()
    remaining = json.loads((project_dir / ".mcp.json").read_text())
    assert "context-engine" not in remaining["mcpServers"]
    assert "other-tool" in remaining["mcpServers"]


def test_uninstall_removes_context_engine_yaml(runner, tmp_path):
    """Per-project .context-engine.yaml is deleted."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".context-engine.yaml").write_text("compression:\n  level: full\n")

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output
    assert not (project_dir / ".context-engine.yaml").exists()


def test_uninstall_removes_cce_hooks_from_settings(runner, tmp_path):
    """CCE hooks are removed from .claude/settings.local.json."""
    import json
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    settings_dir = project_dir / ".claude"
    settings_dir.mkdir()
    settings = {
        "permissions": {"allow": ["Bash(cce *)"]},
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "cce status --oneline"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "echo hello"}]},
            ],
        },
    }
    (settings_dir / "settings.local.json").write_text(json.dumps(settings))

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output

    remaining = json.loads((settings_dir / "settings.local.json").read_text())
    # CCE hook removed, non-CCE hook preserved
    session_hooks = remaining.get("hooks", {}).get("SessionStart", [])
    for h in session_hooks:
        for cmd in h.get("hooks", []):
            assert "cce" not in cmd.get("command", "")
    # permissions preserved
    assert "permissions" in remaining


def test_uninstall_removes_gitignore_cce_entries(runner, tmp_path):
    """CCE entries are removed from .gitignore, other entries kept."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".gitignore").write_text(
        "node_modules/\n.cce/\n*.pyc\n.context-engine.yaml\ndist/\n"
    )

    result = _run_uninstall_in(runner, project_dir)
    assert result.exit_code == 0, result.output

    remaining = (project_dir / ".gitignore").read_text()
    assert ".cce" not in remaining
    assert "context-engine" not in remaining.lower()
    assert "node_modules/" in remaining
    assert "*.pyc" in remaining
    assert "dist/" in remaining


def test_uninstall_removes_index_data(runner, tmp_path):
    """Index data in ~/.cce/projects/<name> is deleted."""
    from unittest.mock import patch as mock_patch
    from context_engine.config import Config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage_root = tmp_path / "storage"
    index_dir = storage_root / "proj"
    index_dir.mkdir(parents=True)
    (index_dir / "stats.json").write_text('{"queries": 5}')
    (index_dir / "manifest.json").write_text("{}")
    (index_dir / "memory.db").write_text("fake")

    config = Config(storage_path=str(storage_root))
    with mock_patch("context_engine.cli.load_config", return_value=config):
        result = _run_uninstall_in(runner, project_dir)

    assert result.exit_code == 0, result.output
    assert not index_dir.exists()
    assert "Removed index data" in result.output


def test_uninstall_full_cleanup(runner, tmp_path):
    """Full uninstall removes all CCE artifacts."""
    import json
    from unittest.mock import patch as mock_patch
    from context_engine.config import Config

    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    # Set up all CCE artifacts
    (project_dir / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"context-engine": {"command": "cce", "args": ["serve"]}}
    }))
    (project_dir / "CLAUDE.md").write_text(_CCE_CLAUDE_MD_BLOCK)
    (project_dir / ".context-engine.yaml").write_text("compression:\n  level: full\n")
    (project_dir / ".gitignore").write_text(".cce/\n.context-engine.yaml\n")

    cce_dir = project_dir / ".cce"
    cce_dir.mkdir()
    (cce_dir / "commands.yaml").write_text("rules: []\n")

    settings_dir = project_dir / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "cce status --oneline"}]},
            ],
        },
    }))

    storage_root = tmp_path / "storage"
    index_dir = storage_root / "proj"
    index_dir.mkdir(parents=True)
    (index_dir / "stats.json").write_text('{"queries": 5}')
    (index_dir / "memory.db").write_text("fake")

    config = Config(storage_path=str(storage_root))
    with mock_patch("context_engine.cli.load_config", return_value=config):
        result = _run_uninstall_in(runner, project_dir)

    assert result.exit_code == 0, result.output

    # Everything gone
    assert not (project_dir / ".mcp.json").exists()
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (project_dir / ".context-engine.yaml").exists()
    assert not (project_dir / ".cce").exists()
    assert not index_dir.exists()
    # .gitignore deleted (was only CCE entries)
    assert not (project_dir / ".gitignore").exists()
    # settings.local.json deleted (was only CCE hooks)
    assert not (settings_dir / "settings.local.json").exists()
