"""Tests for OpenCode MCP configuration in editors.py."""
from __future__ import annotations

import json
from unittest.mock import patch

from context_engine.editors import (
    configure_mcp, detect_editors, remove_mcp,
)


def test_detect_opencode_json(tmp_path):
    (tmp_path / "opencode.json").write_text("{}")
    detected = detect_editors(tmp_path)
    assert "opencode" in detected


def test_detect_opencode_jsonc(tmp_path):
    (tmp_path / "opencode.jsonc").write_text("{}")
    detected = detect_editors(tmp_path)
    assert "opencode" in detected


def test_configure_opencode_creates_config(tmp_path):
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "opencode")
    assert changed is True

    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["context-engine"]
    assert entry["type"] == "local"
    assert entry["command"] == ["/usr/bin/cce", "serve", "--project-dir", str(tmp_path)]


def test_configure_opencode_idempotent(tmp_path):
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(tmp_path, "opencode")
        changed = configure_mcp(tmp_path, "opencode")
    assert changed is False


def test_configure_opencode_preserves_existing(tmp_path):
    existing = {"model": "anthropic/claude-sonnet-4-5", "mcp": {"other": {"type": "local"}}}
    (tmp_path / "opencode.json").write_text(json.dumps(existing))

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(tmp_path, "opencode")

    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["model"] == "anthropic/claude-sonnet-4-5"
    assert "other" in data["mcp"]
    assert "context-engine" in data["mcp"]


def test_configure_opencode_uses_jsonc_if_exists(tmp_path):
    (tmp_path / "opencode.jsonc").write_text('{\n  // my config\n  "model": "test"\n}')

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "opencode")
    assert changed is True

    # Should write to .jsonc since that's what existed
    data = json.loads((tmp_path / "opencode.jsonc").read_text())
    assert "context-engine" in data["mcp"]
    assert data["model"] == "test"


def test_remove_opencode(tmp_path):
    config = {"mcp": {"context-engine": {"type": "local", "command": ["/usr/bin/cce"]}}}
    (tmp_path / "opencode.json").write_text(json.dumps(config))

    result = remove_mcp(tmp_path, "opencode")
    assert result is not None
    assert "opencode" in result.lower()
