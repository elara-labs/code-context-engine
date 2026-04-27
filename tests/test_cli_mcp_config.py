"""Regression: `cce init` must write a usable `command` path to `.mcp.json`.

Before: `_configure_mcp` derived the cce path from `Path(sys.executable).parent`,
which only happens to be right inside a managed venv. For pipx, Homebrew, or
`pip install --user`, that directory does NOT contain `cce` and the code fell
back to bare `"cce"` — fine if cce is on Claude Code's PATH, broken otherwise.
The session-hook installer already used `resolve_cce_binary()`, so the two
disagreed on which binary to invoke.

This test pins the contract that `_configure_mcp` defers to
`resolve_cce_binary()` so MCP and the SessionStart hook stay in sync.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from context_engine.cli import _configure_mcp


def test_configure_mcp_uses_resolved_cce_binary(tmp_path):
    fake_cce = "/usr/local/totally-real/cce"
    # `_configure_mcp` does `from context_engine.utils import resolve_cce_binary`
    # at call time, so the patch must hit the source module, not `cli`.
    with patch("context_engine.utils.resolve_cce_binary", return_value=fake_cce):
        added = _configure_mcp(tmp_path)
    assert added is True

    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["context-engine"]
    assert entry["command"] == fake_cce
    assert entry["args"] == ["serve", "--project-dir", str(tmp_path)]


def test_configure_mcp_updates_stale_command_path(tmp_path):
    """If `.mcp.json` already has an entry pointing at an old/dead cce path
    (e.g. from a prior install in a removed venv), `cce init` should rewrite
    it to whatever `resolve_cce_binary()` returns now — not silently keep the
    broken path."""
    mcp_path = tmp_path / ".mcp.json"
    stale = {
        "mcpServers": {
            "context-engine": {
                "command": "/old/venv/that/does-not-exist/bin/cce",
                "args": ["serve"],
            }
        }
    }
    mcp_path.write_text(json.dumps(stale))

    new_cce = "/usr/local/bin/cce"
    with patch("context_engine.utils.resolve_cce_binary", return_value=new_cce):
        changed = _configure_mcp(tmp_path)
    assert changed is True

    data = json.loads(mcp_path.read_text())
    entry = data["mcpServers"]["context-engine"]
    assert entry["command"] == new_cce
    assert entry["args"] == ["serve", "--project-dir", str(tmp_path)]


def test_configure_mcp_preserves_other_mcp_servers(tmp_path):
    """Pre-existing entries for unrelated servers must survive — otherwise
    re-running `cce init` would silently destroy the user's other MCP wiring."""
    mcp_path = tmp_path / ".mcp.json"
    existing = {
        "mcpServers": {
            "some-other-server": {"command": "/opt/other/bin/x", "args": ["run"]}
        }
    }
    mcp_path.write_text(json.dumps(existing))

    with patch("context_engine.utils.resolve_cce_binary", return_value="/bin/cce"):
        _configure_mcp(tmp_path)

    data = json.loads(mcp_path.read_text())
    assert "some-other-server" in data["mcpServers"]
    assert data["mcpServers"]["some-other-server"]["command"] == "/opt/other/bin/x"
    assert "context-engine" in data["mcpServers"]
