"""Tests for OpenAI Codex MCP and AGENTS.md configuration."""
from __future__ import annotations

from unittest.mock import patch

from context_engine.editors import (
    configure_mcp,
    detect_editors,
    remove_instruction_file,
    write_instruction_file,
)


def test_detect_codex_directory(tmp_path):
    (tmp_path / ".codex").mkdir()

    detected = detect_editors(tmp_path)

    assert "codex" in detected


def test_configure_codex_creates_toml_config(tmp_path):
    (tmp_path / ".codex").mkdir()

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "codex")

    assert changed is True
    content = (tmp_path / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.context-engine]" in content
    assert 'command = "/usr/bin/cce"' in content
    assert f'args = ["serve", "--project-dir", "{tmp_path}"]' in content


def test_codex_instruction_file_uses_agents_md(tmp_path):
    (tmp_path / ".codex").mkdir()

    changed = write_instruction_file(tmp_path, "codex")

    assert changed is True
    content = (tmp_path / "AGENTS.md").read_text()
    assert "## Context Engine (CCE)" in content
    assert "Use `context_search` instead of reading files directly" in content
    assert "Call `session_recall(\"topic phrase\")`" in content


def test_codex_instruction_file_appends_to_existing_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project Rules\n\nRun tests before committing.\n")

    changed = write_instruction_file(tmp_path, "codex")

    assert changed is True
    content = (tmp_path / "AGENTS.md").read_text()
    assert content.startswith("# Project Rules")
    assert "Run tests before committing." in content
    assert "## Context Engine (CCE)" in content


def test_codex_instruction_file_idempotent(tmp_path):
    write_instruction_file(tmp_path, "codex")

    changed = write_instruction_file(tmp_path, "codex")

    assert changed is False
    assert (tmp_path / "AGENTS.md").read_text().count("## Context Engine (CCE)") == 1


def test_remove_codex_instruction_block_preserves_existing_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project Rules\n\nRun tests before committing.\n")
    write_instruction_file(tmp_path, "codex")

    result = remove_instruction_file(tmp_path, "codex")

    assert result == "Removed CCE block from AGENTS.md"
    content = (tmp_path / "AGENTS.md").read_text()
    assert "# Project Rules" in content
    assert "Run tests before committing." in content
    assert "## Context Engine (CCE)" not in content
