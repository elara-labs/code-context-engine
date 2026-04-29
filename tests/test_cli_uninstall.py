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
        return runner.invoke(main, ["uninstall"])
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
