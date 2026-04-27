"""End-to-end test for the cross-session memory loop.

Verifies the load-bearing claim of the project: a decision recorded in one
session is surfaced and recallable in the next, across a real ContextEngineMCP
restart against the same on-disk storage. Also exercises auto-captured
touched_files and the old-session pruning path.

Does not require a real Claude / Anthropic API key — drives the MCP server
directly the same way `cce serve`'s stdio transport would.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_engine.compression.compressor import Compressor
from context_engine.config import Config
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.pipeline import run_indexing
from context_engine.integration.mcp_server import ContextEngineMCP
from context_engine.integration.session_capture import SessionCapture
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.storage.local_backend import LocalBackend


def _build_server(project_dir: Path, storage_root: Path) -> ContextEngineMCP:
    """Construct a fully-wired MCP server against the given dirs.

    Mirrors `cli._run_serve` — uses the real LocalBackend, Embedder,
    HybridRetriever, Compressor, BootstrapBuilder, SessionCapture so the
    test exercises the actual production object graph (no mocks, no
    `__new__` bypass like the older test in test_mcp_server.py).
    """
    config = Config(storage_path=str(storage_root))
    backend = LocalBackend(base_path=str(storage_root / project_dir.name))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)
    compressor = Compressor(model=config.compression_model, cache=backend)
    return ContextEngineMCP(
        retriever=retriever,
        backend=backend,
        compressor=compressor,
        embedder=embedder,
        config=config,
    )


@pytest.fixture()
def project_and_storage(tmp_path, monkeypatch):
    """Fresh project + storage dirs; pretend cwd is the project for ContextEngineMCP."""
    project_dir = tmp_path / "demo-project"
    project_dir.mkdir()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.chdir(project_dir)
    return project_dir, storage_root


@pytest.mark.asyncio
async def test_decision_recorded_then_recalled_across_restart(project_and_storage):
    """Session 1 records a decision → session 2 (fresh server, same storage)
    surfaces it via get_recent_decisions and finds the paraphrase via
    vector-based session_recall."""
    project_dir, storage_root = project_and_storage

    # ── Session 1 ──
    server1 = _build_server(project_dir, storage_root)
    decision_text = "Use snake_case for Python identifiers"
    reason_text = "PEP 8 convention; consistent with stdlib"
    result = server1._handle_record_decision(
        {"decision": decision_text, "reason": reason_text}
    )
    assert "Decision recorded" in result[0].text

    # The decision should have been persisted to the per-session JSON. Locate it.
    sessions_dir = storage_root / project_dir.name / "sessions"
    session_files = [
        f for f in sessions_dir.glob("*.json") if f.name != "decisions_log.json"
    ]
    assert session_files, "expected at least one session file on disk after record"
    on_disk = json.loads(session_files[0].read_text())
    assert any(d["decision"] == decision_text for d in on_disk["decisions"])

    # ── Session 2 — fresh server, same storage ──
    server2 = _build_server(project_dir, storage_root)

    # Bootstrap path: get_recent_decisions surfaces the decision unconditionally
    # (it does not depend on a topic-grep matching the literal word "decision",
    # which was the bug fixed in commit fdd66bc).
    decisions = server2._session_capture.get_recent_decisions(limit=10)
    assert any(decision_text in d for d in decisions), (
        f"expected the prior decision in get_recent_decisions(); got {decisions}"
    )

    # Recall path: paraphrased query finds it via vector similarity. The user
    # never wrote "naming convention" anywhere; this only works if recall is
    # embedding-based, not the old substring grep.
    matches = server2._search_sessions("naming convention")
    assert any(decision_text in m for m in matches), (
        f"expected 'naming convention' to vector-match the recorded decision; "
        f"got {matches}"
    )


@pytest.mark.asyncio
async def test_touched_files_auto_capture_survives_restart(project_and_storage):
    """A file that surfaces in context_search results gets touch-counted
    automatically; the next session's load_recent_sessions sees it without
    Claude having needed to call record_code_area."""
    project_dir, storage_root = project_and_storage

    # Real code so the indexer + retriever have something semantically
    # meaningful to find.
    auth_file = project_dir / "auth.py"
    auth_file.write_text(
        "def validate_token(token: str) -> bool:\n"
        '    """Validate a JWT bearer token."""\n'
        "    return bool(token)\n"
    )

    config = Config(storage_path=str(storage_root))
    await run_indexing(config, project_dir, full=True)

    # ── Session 1: search for it; auto-capture should fire ──
    server1 = _build_server(project_dir, storage_root)
    await server1._handle_context_search({"query": "validate token"})

    snapshot = server1._session_capture.get_session_snapshot(server1._session_id)
    assert snapshot is not None
    touched = snapshot.get("touched_files", {})
    assert "auth.py" in touched, (
        f"expected auth.py to be auto-captured into touched_files; got {touched}"
    )

    # ── Session 2: prior session's touched files visible from disk ──
    server2 = _build_server(project_dir, storage_root)
    recent = server2._session_capture.load_recent_sessions(limit=1)
    assert recent, "expected the prior session to have been persisted"
    assert "auth.py" in (recent[0].get("touched_files") or {})


def test_prune_consolidates_old_sessions_into_decisions_log(tmp_path):
    """Once the per-project sessions/ dir grows past the threshold, the oldest
    session files should be consolidated into decisions_log.json and the
    source files removed; subsequent recall must still see the archived
    decisions via get_recent_decisions."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    capture = SessionCapture(sessions_dir=str(sessions_dir))

    # 110 fake session files — each with one unique decision so we can
    # check the archive contains the right rows.
    import os
    import time

    for i in range(110):
        sid = f"sess{i:03d}"
        path = sessions_dir / f"{sid}.json"
        path.write_text(json.dumps({
            "id": sid,
            "decisions": [{
                "decision": f"decision-{i}",
                "reason": f"reason-{i}",
                "timestamp": float(i),
            }],
        }))
        # Pin mtimes so "newest" ordering is deterministic — relying on the
        # writer's clock gives ties on fast filesystems.
        os.utime(path, (i, i))
        # Avoid hitting the same nanosecond on tmpfs — cheap insurance.
        time.sleep(0.001)

    summary = capture.prune_old_sessions(threshold=100, keep=50)
    assert summary["pruned"] == 60, summary
    assert summary["decisions_appended"] == 60

    # 50 sessions should remain on disk plus the new decisions_log.
    remaining = [
        f for f in sessions_dir.glob("*.json") if f.name != "decisions_log.json"
    ]
    assert len(remaining) == 50

    archive = json.loads((sessions_dir / "decisions_log.json").read_text())
    assert len(archive) == 60
    archived_decisions = {row["decision"] for row in archive}
    # The oldest 60 (sess000..sess059) were consolidated.
    for i in range(60):
        assert f"decision-{i}" in archived_decisions

    # And get_recent_decisions still surfaces archived rows — not just the
    # ones that survived as session files. This is the part that protects
    # long-lived projects from silently forgetting old context.
    surfaced = capture.get_recent_decisions(limit=200)
    assert any("decision-0" in s for s in surfaced), (
        f"expected archived decision to be surfaced via get_recent_decisions; "
        f"sample: {surfaced[:5]}"
    )


@pytest.mark.asyncio
async def test_session_recall_searches_decisions_log_archive(project_and_storage):
    """`session_recall` (i.e. _search_sessions) must include consolidated
    decisions from decisions_log.json — otherwise pruning silently drops the
    archive from recall, contradicting the prune CLI's docstring claim that
    "Decisions remain searchable via session_recall after pruning"."""
    project_dir, storage_root = project_and_storage

    sessions_dir = storage_root / project_dir.name / "sessions"
    sessions_dir.mkdir(parents=True)

    # No on-disk per-session files — only the consolidated archive — so we
    # know the recall hit comes from decisions_log.json, not from a still-
    # present session file. Use very-distinct text so the substring fallback
    # would also have a chance if vector recall returned nothing.
    archive = [
        {
            "decision": "Use RS256 JWT signing for service-to-service auth",
            "reason": "Asymmetric keys mean services can verify without sharing secrets",
            "timestamp": 1.0,
        }
    ]
    (sessions_dir / "decisions_log.json").write_text(json.dumps(archive))

    server = _build_server(project_dir, storage_root)
    matches = server._search_sessions("token signing algorithm")
    assert any("RS256" in m for m in matches), (
        f"expected archived decision to surface via session_recall; got {matches}"
    )
