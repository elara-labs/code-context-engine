"""Tests for PR 4 — extended session_recall + new MCP tools.

These tests exercise the recall handlers directly without a stdio transport
by calling the private _handle_* methods on a constructed ContextEngineMCP.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from context_engine.config import Config
from context_engine.integration.mcp_server import ContextEngineMCP
from context_engine.memory import db as memory_db


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    """A ContextEngineMCP bound to a tmp project + storage. Stub deps."""
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    storage_path = tmp_path / "storage"
    monkeypatch.chdir(project_dir)

    config = Config(
        storage_path=str(storage_path),
        embedding_model="BAAI/bge-small-en-v1.5",
    )

    backend = MagicMock()
    backend._vector_store.count.return_value = 0
    compressor = MagicMock()
    embedder = MagicMock()
    # The recall path embeds candidates; return a stable vector so
    # cosine ranking is deterministic.
    embedder.embed_query = lambda text: [1.0, 0.0] if "KEY" in text else [0.0, 1.0]
    retriever = MagicMock()

    server = ContextEngineMCP(
        retriever=retriever, backend=backend, compressor=compressor,
        embedder=embedder, config=config,
    )
    yield server
    if server._memory_conn is not None:
        server._memory_conn.close()


def test_record_decision_dual_writes_to_memory_db(mcp):
    out = mcp._handle_record_decision({
        "decision": "Use bge-small for KEY recall",
        "reason": "Already loaded for the index",
    })
    assert "Decision recorded" in out[0].text

    rows = list(mcp._memory_conn.execute(
        "SELECT decision, reason, source FROM decisions"
    ))
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"
    assert "bge-small" in rows[0]["decision"]


def test_record_code_area_dual_writes_to_memory_db(mcp):
    mcp._handle_record_code_area({
        "file_path": "src/foo.py",
        "description": "memory bootstrap",
    })
    rows = list(mcp._memory_conn.execute(
        "SELECT file_path, description, source FROM code_areas"
    ))
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_session_timeline_returns_turn_summaries_for_session(mcp):
    sid = "tl-test"
    mcp._memory_conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "status, prompt_count) VALUES (?, 'demo', 1700000000, "
        "'2023-11-14T22:13:20', 'completed', 2)",
        (sid,),
    )
    mcp._memory_conn.execute(
        "INSERT INTO turn_summaries (session_id, prompt_number, summary, tier, "
        "created_at_epoch) VALUES (?, 1, 'first turn summary', 'extractive', "
        "1700000010)", (sid,),
    )
    mcp._memory_conn.execute(
        "INSERT INTO turn_summaries (session_id, prompt_number, summary, tier, "
        "created_at_epoch) VALUES (?, 2, 'second turn summary', 'extractive', "
        "1700000020)", (sid,),
    )
    mcp._memory_conn.commit()

    out = mcp._handle_session_timeline({"session_id": sid})
    text = out[0].text
    assert "first turn summary" in text
    assert "second turn summary" in text
    assert "turn   1" in text and "turn   2" in text


def test_session_timeline_empty_session(mcp):
    out = mcp._handle_session_timeline({"session_id": "missing"})
    assert "No turn summaries" in out[0].text


def test_session_timeline_requires_session_id(mcp):
    out = mcp._handle_session_timeline({})
    assert "required" in out[0].text


def test_session_event_returns_raw_payload(mcp):
    mcp._memory_conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at) "
        "VALUES ('sx', 'demo', 1700000000, '2023-11-14T22:13:20')"
    )
    cur = mcp._memory_conn.execute(
        "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
        "VALUES (?, ?, ?)",
        (json.dumps({"file_path": "/tmp/x"}), "x = 1", 5),
    )
    payload_id = cur.lastrowid
    cur = mcp._memory_conn.execute(
        "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
        "payload_id, created_at_epoch, created_at) "
        "VALUES ('sx', 1, 'Read', ?, 1700000000, '2023-11-14T22:13:20')",
        (payload_id,),
    )
    event_id = cur.lastrowid
    mcp._memory_conn.commit()

    out = mcp._handle_session_event({"event_id": event_id})
    text = out[0].text
    assert "Read" in text
    assert "/tmp/x" in text
    assert "x = 1" in text


def test_session_event_returns_no_payload_message_when_payload_id_null(mcp):
    """Event captured without a payload row (descriptor-only) shows that."""
    mcp._memory_conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at) "
        "VALUES ('sx', 'demo', 1700000000, '2023-11-14T22:13:20')"
    )
    cur = mcp._memory_conn.execute(
        "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
        "payload_id, created_at_epoch, created_at) "
        "VALUES ('sx', 1, 'Read', NULL, 1700000000, '2023-11-14T22:13:20')",
    )
    event_id = cur.lastrowid
    mcp._memory_conn.commit()

    out = mcp._handle_session_event({"event_id": event_id})
    assert "no captured payload" in out[0].text
    assert "aged out" not in out[0].text


def test_session_event_returns_aged_out_message_after_prune(mcp):
    """A real prune-path event surfaces the 'aged out' message."""
    from context_engine.memory import db as memory_db
    mcp._memory_conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at) "
        "VALUES ('sxp', 'demo', 1700000000, '2023-11-14T22:13:20')"
    )
    pid = mcp._memory_conn.execute(
        "INSERT INTO tool_event_payloads (raw_input, raw_output, size_bytes) "
        "VALUES (?, ?, ?)",
        ('{"file":"x"}', "old output", 22),
    ).lastrowid
    cur = mcp._memory_conn.execute(
        "INSERT INTO tool_events (session_id, prompt_number, tool_name, "
        "payload_id, created_at_epoch, created_at) "
        "VALUES ('sxp', 1, 'Read', ?, 1700000000, '2023-11-14T22:13:20')",
        (pid,),
    )
    event_id = cur.lastrowid
    mcp._memory_conn.commit()
    # Day-zero retention forces the prune for any payload not freshly written.
    memory_db.prune_old_payloads(mcp._memory_conn, days=0)

    out = mcp._handle_session_event({"event_id": event_id})
    assert "aged out" in out[0].text
    assert "no captured payload" not in out[0].text


def test_session_event_invalid_id(mcp):
    out = mcp._handle_session_event({"event_id": "abc"})
    assert "must be an integer" in out[0].text


def test_session_recall_includes_memory_db_decisions(mcp):
    """A decision in memory.db should surface via session_recall."""
    # Seed memory.db with a relevant decision.
    mcp._memory_conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES (?, ?, 'manual', 1700000000, "
        "'2023-11-14T22:13:20')",
        ("Pick KEY library for X", "KEY rationale here"),
    )
    mcp._memory_conn.commit()

    matches = mcp._search_sessions("KEY")
    # The candidate text contains "[decision src=manual|sid:-]" prefix +
    # decision text. We just need at least one match referencing KEY.
    assert any("KEY" in m for m in matches), matches


def test_tool_names_includes_new_tools(mcp):
    assert "session_timeline" in mcp.TOOL_NAMES
    assert "session_event" in mcp.TOOL_NAMES
    assert "session_recall" in mcp.TOOL_NAMES


def test_rrf_merge_basic():
    """Items appearing in multiple lists should rank above items unique to one."""
    from context_engine.integration.mcp_server import _rrf_merge
    out = _rrf_merge(
        ["A", "B", "C"],     # source 1: A first
        ["B", "A", "D"],     # source 2: B first, A second
        top=10,
    )
    # A and B both appear in both lists at rank ≤ 2; C and D each appear once.
    assert out[0] in {"A", "B"}
    assert out[1] in {"A", "B"}
    assert set(out[:2]) == {"A", "B"}
    assert "C" in out and "D" in out


def test_rrf_merge_handles_empty_lists():
    from context_engine.integration.mcp_server import _rrf_merge
    out = _rrf_merge([], ["X", "Y"], [], top=10)
    assert out == ["X", "Y"]


def test_strip_tag_helper():
    from context_engine.integration.mcp_server import _strip_tag
    assert _strip_tag("[decision src=manual|sid:-] hello — world") == "hello — world"
    assert _strip_tag("[turn sid:abc|n:3] this is a summary") == "this is a summary"
    assert _strip_tag("no tag here") == "no tag here"
    # Multi-bracket safety: only the leading bracketed prefix is stripped.
    assert _strip_tag("[a] [b] x") == "[b] x"


def test_rrf_dedupes_by_stripped_text():
    """Same content with different tag prefixes should collapse, not double up."""
    from context_engine.integration.mcp_server import _rrf_merge
    out = _rrf_merge(
        ["[decision src=manual|sid:abc] use jwt — stateless"],
        ["[decision src=migrated|sid:-] use jwt — stateless"],
        top=10,
    )
    assert len(out) == 1
    assert "use jwt — stateless" in out[0]


def test_humanise_relative_time():
    import time as _time
    from context_engine.integration.mcp_server import _humanise_relative_time
    now = int(_time.time())
    assert _humanise_relative_time(now) == "just now"
    assert _humanise_relative_time(now - 90) == "1m ago"
    assert _humanise_relative_time(now - 3 * 3600) == "3h ago"
    assert _humanise_relative_time(now - 5 * 86_400) == "5d ago"
    assert _humanise_relative_time(None) == ""
    assert _humanise_relative_time("not-an-int") == ""


def test_truncate_payload_caps_long_strings():
    from context_engine.integration.mcp_server import _truncate_payload
    assert _truncate_payload(None, 10) == "<no value>"
    assert _truncate_payload("hi", 10) == "hi"
    out = _truncate_payload("x" * 5000, 100)
    assert out.startswith("x" * 100)
    assert "truncated" in out
    assert "4900 more chars" in out


def test_recall_format_includes_recency_and_drill_affordance(mcp):
    """Each match should carry a relative-time hint and a callable drill hint."""
    import time as _time
    sid = "recency-test"
    mcp._memory_conn.execute(
        "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
        "status) VALUES (?, 'demo', 1700000000, '2023-11-14T22:13:20', 'active')",
        (sid,),
    )
    one_hour_ago = int(_time.time()) - 3600
    mcp._memory_conn.execute(
        "INSERT INTO decisions (decision, reason, source, session_id, "
        "created_at_epoch, created_at) VALUES (?, ?, 'manual', ?, ?, ?)",
        ("Pick KEY library", "KEY rationale", sid, one_hour_ago,
         "2025-01-01T00:00:00"),
    )
    mcp._memory_conn.commit()
    matches = mcp._search_sessions("KEY")
    assert any(
        "1h ago" in m and 'session_timeline("recency-test")' in m
        for m in matches
    ), matches


def test_recall_response_includes_tldr_when_enough_matches(mcp):
    """When ≥3 matches are returned, _handle_session_recall prepends a TL;DR."""
    import asyncio
    for i in range(5):
        mcp._memory_conn.execute(
            "INSERT INTO decisions (decision, reason, source, "
            "created_at_epoch, created_at) VALUES (?, ?, 'manual', 1700000000, "
            "'2023-11-14T22:13:20')",
            (f"Pick KEY library variant {i}", f"KEY rationale variant {i}"),
        )
    mcp._memory_conn.commit()

    out = asyncio.run(mcp._handle_session_recall({"topic": "KEY"}))
    text = out[0].text
    assert text.startswith("TL;DR"), f"expected TL;DR header, got: {text[:120]!r}"
    assert "Source matches:" in text


def test_recall_response_omits_tldr_for_few_matches(mcp):
    """With 1-2 matches, the bullet list is returned without a TL;DR header."""
    import asyncio
    mcp._memory_conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) VALUES (?, ?, 'manual', 1700000000, "
        "'2023-11-14T22:13:20')",
        ("Single KEY decision", "Only one"),
    )
    mcp._memory_conn.commit()

    out = asyncio.run(mcp._handle_session_recall({"topic": "KEY"}))
    text = out[0].text
    assert not text.startswith("TL;DR"), text[:120]
