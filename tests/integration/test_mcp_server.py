import pytest
from unittest.mock import MagicMock
from context_engine.integration.mcp_server import ContextEngineMCP


def test_mcp_server_has_required_tools():
    server = ContextEngineMCP.__new__(ContextEngineMCP)
    tool_names = server.get_tool_names()
    assert "context_search" in tool_names
    assert "expand_chunk" in tool_names
    assert "related_context" in tool_names
    assert "session_recall" in tool_names
    assert "index_status" in tool_names
    assert "reindex" in tool_names


def _make_server(tmp_path):
    """Build a minimal ContextEngineMCP with a tmp storage dir."""
    config = MagicMock()
    config.storage_path = str(tmp_path)
    config.output_compression = "standard"
    server = ContextEngineMCP.__new__(ContextEngineMCP)
    server._config = config
    server._output_level = "standard"
    server._stats_path = tmp_path / "stats.json"
    server._stats = server._load_stats()
    # _record_bucket already guards on this; tests don't need a real db.
    server._memory_conn = None
    server._storage_base = tmp_path
    return server


def test_apply_output_compression_appends_directive(tmp_path):
    """When level != off, the helper appends the directive and bumps the bucket."""
    server = _make_server(tmp_path)
    server._output_level = "max"
    out = server._apply_output_compression("body content")
    assert "body content" in out
    assert "[Respond using max output compression]" in out
    # Bucket gained one event.
    bucket = server._stats["buckets"]["output_compression"]
    assert bucket["calls"] == 1
    assert bucket["baseline"] > bucket["served"] > 0


def test_apply_output_compression_noop_when_off(tmp_path):
    """level=off returns the body untouched and records nothing."""
    server = _make_server(tmp_path)
    server._output_level = "off"
    out = server._apply_output_compression("body content")
    assert out == "body content"
    assert server._stats["buckets"]["output_compression"]["calls"] == 0


def test_recall_display_cap_default():
    """Without the env var, cap defaults to 7."""
    import os
    from context_engine.integration.mcp_server import _recall_display_cap
    os.environ.pop("CCE_RECALL_DISPLAY_CAP", None)
    assert _recall_display_cap() == 7


def test_recall_display_cap_env_override(monkeypatch):
    """CCE_RECALL_DISPLAY_CAP raises (or lowers) the cap for power users."""
    from context_engine.integration.mcp_server import _recall_display_cap
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "20")
    assert _recall_display_cap() == 20
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "3")
    assert _recall_display_cap() == 3


def test_recall_display_cap_invalid_falls_back(monkeypatch):
    """Garbage values are ignored — never break recall on a typo."""
    from context_engine.integration.mcp_server import _recall_display_cap
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "not-a-number")
    assert _recall_display_cap() == 7
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "0")
    assert _recall_display_cap() == 7
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "-5")
    assert _recall_display_cap() == 7


def test_audit_log_no_op_when_disabled(tmp_path):
    """Audit log writes only when config.audit_log_enabled is True."""
    server = _make_server(tmp_path)
    server._session_id = "test-session"
    server._config.audit_log_enabled = False
    server._append_audit_log(
        query="hello world", top_k=10,
        served_chunks=[], score_range=None,
    )
    assert not (tmp_path / "audit.log").exists()


def test_audit_log_writes_one_jsonline_per_call(tmp_path):
    """When enabled, each call appends one well-formed JSON line."""
    import json as _json
    server = _make_server(tmp_path)
    server._session_id = "test-session"
    server._config.audit_log_enabled = True
    server._append_audit_log(
        query="how does auth work?", top_k=7,
        served_chunks=[
            {"file": "src/auth.py", "lines": "10-40", "score": 0.812, "kind": "inline"},
        ],
        score_range=(0.812, 0.812),
    )
    server._append_audit_log(
        query="another query", top_k=5,
        served_chunks=[], score_range=None,
    )
    audit = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(audit) == 2
    entry = _json.loads(audit[0])
    assert entry["session_id"] == "test-session"
    assert entry["top_k"] == 7
    assert entry["query_len"] == len("how does auth work?")
    # Query text is hashed (12-char prefix), never stored raw.
    assert "auth" not in entry["query_hash"]
    assert len(entry["query_hash"]) == 12
    assert entry["served"][0]["file"] == "src/auth.py"
    assert entry["score_range"] == [0.812, 0.812]


@pytest.mark.asyncio
async def test_index_status_no_queries(tmp_path):
    server = _make_server(tmp_path)
    result = await server._handle_index_status()
    text = result[0].text
    assert "waiting for first context_search call" in text


@pytest.mark.asyncio
async def test_index_status_with_tracked_stats(tmp_path):
    server = _make_server(tmp_path)
    server._stats = {"queries": 5, "raw_tokens": 1000, "served_tokens": 400}
    result = await server._handle_index_status()
    text = result[0].text
    assert "5 queries" in text
    assert "1,000" in text   # raw
    assert "400" in text     # served
    assert "600" in text     # saved
    assert "60%" in text
