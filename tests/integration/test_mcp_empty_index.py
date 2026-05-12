"""Tests for context_search behaviour on an empty index (#67).

Previously, the first context_search against an empty index silently
blocked while a full reindex ran underneath — from the MCP client's
side the call looked hung. The fix returns a status response and
spawns the reindex as a background task instead.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from context_engine.config import Config
from context_engine.integration.mcp_server import ContextEngineMCP


def _make_mcp(tmp_path, monkeypatch, *, chunk_count: int):
    project_dir = tmp_path / "demo"
    project_dir.mkdir(parents=True, exist_ok=True)
    storage_path = tmp_path / "storage"
    monkeypatch.chdir(project_dir)
    config = Config(
        storage_path=str(storage_path),
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    backend = MagicMock()
    backend._vector_store.count.return_value = chunk_count
    compressor = MagicMock()
    embedder = MagicMock()
    retriever = MagicMock()
    return ContextEngineMCP(
        retriever=retriever, backend=backend, compressor=compressor,
        embedder=embedder, config=config,
    )


@pytest.mark.asyncio
async def test_context_search_returns_status_when_index_empty(
    tmp_path, monkeypatch
):
    mcp = _make_mcp(tmp_path, monkeypatch, chunk_count=0)

    # Patch run_indexing so the background task can't actually fire off a
    # real pipeline run during the test (it would try to read the empty
    # tmp dir but the import would still cost real wall-time).
    indexing_called: list[bool] = []

    async def _fake_run_indexing(*a, **kw):
        indexing_called.append(True)

    monkeypatch.setattr(
        "context_engine.indexer.pipeline.run_indexing",
        _fake_run_indexing,
    )

    result = await mcp._handle_context_search({"query": "anything"})
    assert len(result) == 1
    text = result[0].text
    # Must NOT pretend the search ran — has to tell the user the index
    # is empty and what to do.
    assert "empty" in text.lower()
    assert "cce index" in text.lower() or "indexing" in text.lower()


@pytest.mark.asyncio
async def test_context_search_uses_real_retrieval_when_index_populated(
    tmp_path, monkeypatch
):
    """A populated index must NOT return the empty-index status message —
    confirms the guard runs the right way around."""
    mcp = _make_mcp(tmp_path, monkeypatch, chunk_count=5)

    # Stub the retriever + compressor to keep the path lightweight.
    async def _fake_retrieve(*a, **kw):
        return []

    async def _fake_compress(chunks, level):
        return chunks

    mcp._retriever.retrieve = _fake_retrieve
    mcp._compressor.compress = _fake_compress

    result = await mcp._handle_context_search({"query": "anything"})
    assert len(result) == 1
    text = result[0].text
    # The empty-index banner must not appear when the store has rows.
    assert "indexing has been started" not in text.lower()
    assert "is empty" not in text.lower()


@pytest.mark.asyncio
async def test_ensure_indexed_returns_false_for_empty_and_true_for_populated(
    tmp_path, monkeypatch
):
    mcp_empty = _make_mcp(tmp_path / "a", monkeypatch, chunk_count=0)

    async def _fake_run_indexing(*a, **kw):
        return None

    monkeypatch.setattr(
        "context_engine.indexer.pipeline.run_indexing", _fake_run_indexing,
    )

    assert await mcp_empty._ensure_indexed() is False

    mcp_full = _make_mcp(tmp_path / "b", monkeypatch, chunk_count=10)
    assert await mcp_full._ensure_indexed() is True
