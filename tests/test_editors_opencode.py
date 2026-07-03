"""Tests for OpenCode MCP configuration in editors.py."""
from __future__ import annotations

import json
from unittest.mock import patch

from context_engine.editors import (
    _strip_jsonc_comments, configure_mcp, detect_editors, remove_mcp,
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


# ── _strip_jsonc_comments string-awareness ────────────────────────────
# Regression: the old regex `//.*?$` truncated any string value containing
# `//` (e.g. "https://..."), json.loads failed, data was reset to {} and the
# user's opencode.json was overwritten with only the context-engine entry.

def test_strip_jsonc_preserves_url_in_string():
    text = '{\n  "url": "https://example.com/mcp"\n}'
    assert json.loads(_strip_jsonc_comments(text)) == {
        "url": "https://example.com/mcp"
    }


def test_strip_jsonc_strips_line_comments():
    text = '{\n  // a comment\n  "a": 1 // trailing\n}'
    assert json.loads(_strip_jsonc_comments(text)) == {"a": 1}


def test_strip_jsonc_strips_block_comments():
    text = '{\n  /* block\n     comment */ "a": 1,\n  "b": /* inline */ 2\n}'
    assert json.loads(_strip_jsonc_comments(text)) == {"a": 1, "b": 2}


def test_strip_jsonc_preserves_comment_markers_inside_strings():
    text = '{\n  "a": "not // a comment",\n  "b": "not /* a comment */"\n}'
    assert json.loads(_strip_jsonc_comments(text)) == {
        "a": "not // a comment",
        "b": "not /* a comment */",
    }


def test_strip_jsonc_handles_escaped_quotes_in_strings():
    text = '{\n  "a": "quote \\" then //", // real comment\n  "b": 1\n}'
    assert json.loads(_strip_jsonc_comments(text)) == {
        "a": 'quote " then //',
        "b": 1,
    }


def test_configure_opencode_preserves_server_with_url(tmp_path):
    """A remote MCP server URL must not be truncated as a // comment."""
    existing = {
        "mcp": {
            "remote-thing": {"type": "remote", "url": "https://example.com/mcp"},
        }
    }
    (tmp_path / "opencode.json").write_text(json.dumps(existing))

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(tmp_path, "opencode")

    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["mcp"]["remote-thing"]["url"] == "https://example.com/mcp"
    assert "context-engine" in data["mcp"]


def test_configure_opencode_jsonc_with_url_and_comments(tmp_path):
    (tmp_path / "opencode.jsonc").write_text(
        '{\n'
        '  // my servers\n'
        '  "mcp": {\n'
        '    "remote-thing": {"type": "remote", "url": "https://example.com/mcp"}\n'
        '  }\n'
        '}\n'
    )

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "opencode")
    assert changed is True

    data = json.loads((tmp_path / "opencode.jsonc").read_text())
    assert data["mcp"]["remote-thing"]["url"] == "https://example.com/mcp"
    assert "context-engine" in data["mcp"]


# ── unparseable configs are skipped, never overwritten ────────────────

def test_configure_opencode_skips_unparseable_file(tmp_path):
    """Truly invalid JSON must be left alone (return None), not clobbered."""
    original = '{"mcp": {"other": '  # truncated JSON
    (tmp_path / "opencode.json").write_text(original)

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = configure_mcp(tmp_path, "opencode")

    assert result is None
    assert (tmp_path / "opencode.json").read_text() == original


def test_configure_json_editor_skips_unparseable_file(tmp_path):
    """Generic json editors (e.g. VS Code) must also skip unparseable files."""
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    original = '{\n  // VS Code allows comments here\n  "servers": {"other": {}}\n}'
    (vscode_dir / "mcp.json").write_text(original)

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = configure_mcp(tmp_path, "vscode")

    assert result is None
    assert (vscode_dir / "mcp.json").read_text() == original


# ── non-dict servers key handled gracefully ───────────────────────────

def test_configure_opencode_null_mcp_key(tmp_path):
    (tmp_path / "opencode.json").write_text('{"model": "test", "mcp": null}')

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "opencode")
    assert changed is True

    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["model"] == "test"
    assert "context-engine" in data["mcp"]


def test_configure_json_editor_non_dict_servers_key(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text('{"servers": []}')

    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(tmp_path, "vscode")
    assert changed is True

    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert "context-engine" in data["servers"]


def test_remove_opencode(tmp_path):
    config = {"mcp": {"context-engine": {"type": "local", "command": ["/usr/bin/cce"]}}}
    (tmp_path / "opencode.json").write_text(json.dumps(config))

    result = remove_mcp(tmp_path, "opencode")
    assert result is not None
    assert "opencode" in result.lower()
