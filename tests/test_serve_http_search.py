"""Tests for POST /search on the loopback HTTP server (PR #96).

The endpoint is a thin wrapper around HybridRetriever, so what needs covering is
not the retrieval itself (tested elsewhere) but the input handling added in
review: empty query, over-length query, and non-numeric top_k /
confidence_threshold all return 400 rather than raising.

The retriever is stubbed so these tests stay hermetic — no embedder, no backend,
no index. That also lets the happy-path test assert the clamped values actually
reach retrieve(), which is the part a smoke test would miss.
"""
from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from context_engine.models import Chunk, ChunkType
from context_engine.serve_http import _MAX_QUERY_CHARS, ContextEngineHTTP


class _StubRetriever:
    """Records the arguments it was called with and returns one fixed chunk."""

    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else []
        self.calls: list[dict] = []

    async def retrieve(self, query, top_k=10, confidence_threshold=0.2):
        self.calls.append(
            {"query": query, "top_k": top_k, "confidence_threshold": confidence_threshold}
        )
        return self.chunks


def _chunk() -> Chunk:
    return Chunk(
        id="b1294739d28245a1",
        content="def record_usage(model, provider, input_tokens, ...)",
        chunk_type=ChunkType.FUNCTION,
        file_path="memory/journal.py",
        start_line=81,
        end_line=86,
        language="python",
        metadata={"_distance": 0.774},
        confidence_score=0.878,
    )


def _server(chunks=None) -> ContextEngineHTTP:
    """A ContextEngineHTTP with its retriever swapped for the stub.

    __init__ builds a real HybridRetriever from a backend and embedder, neither of
    which these tests need, so the instance is created without running __init__ and
    only the attribute handle_search touches is set.
    """
    server = ContextEngineHTTP.__new__(ContextEngineHTTP)
    server.retriever = _StubRetriever(chunks)
    return server


def _request(payload) -> web.Request:
    """A mocked POST /search carrying `payload` as its JSON body."""
    body = json.dumps(payload).encode()
    req = make_mocked_request(
        "POST", "/search",
        headers={"Content-Type": "application/json"},
        payload=body,
    )

    async def _json(*args, **kwargs):
        return json.loads(body)

    req.json = _json
    return req


def _body(response: web.Response) -> dict:
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_search_happy_path_returns_results():
    """A valid query returns 200 and a serialised results list."""
    server = _server([_chunk()])
    resp = await server.handle_search(_request({"query": "cost tracking", "top_k": 5}))

    assert resp.status == 200
    results = _body(resp)["results"]
    assert len(results) == 1

    r = results[0]
    assert r["id"] == "b1294739d28245a1"
    assert r["file_path"] == "memory/journal.py"
    assert r["start_line"] == 81
    assert r["end_line"] == 86
    assert r["chunk_type"] == "function"          # the enum is serialised by .value
    assert r["language"] == "python"
    assert r["confidence_score"] == pytest.approx(0.878)
    assert r["metadata"] == {"_distance": 0.774}

    # top_k must reach the retriever, not just be accepted and dropped.
    assert server.retriever.calls == [
        {"query": "cost tracking", "top_k": 5, "confidence_threshold": 0.2}
    ]


@pytest.mark.asyncio
async def test_search_empty_results_is_still_200():
    """No matches is a valid answer, not an error."""
    server = _server([])
    resp = await server.handle_search(_request({"query": "nothing matches this"}))

    assert resp.status == 200
    assert _body(resp)["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", None])
async def test_search_empty_query_returns_400(query):
    """Empty, whitespace-only and missing queries are all rejected."""
    server = _server([_chunk()])
    resp = await server.handle_search(_request({"query": query}))

    assert resp.status == 400
    assert "empty" in _body(resp)["error"]
    assert server.retriever.calls == [], "retriever must not be reached on a bad request"


@pytest.mark.asyncio
async def test_search_over_length_query_returns_400():
    """A query past _MAX_QUERY_CHARS is rejected before it is embedded."""
    server = _server([_chunk()])
    resp = await server.handle_search(_request({"query": "x" * (_MAX_QUERY_CHARS + 1)}))

    assert resp.status == 400
    assert "too long" in _body(resp)["error"]
    assert server.retriever.calls == []


@pytest.mark.asyncio
async def test_search_at_max_query_length_is_accepted():
    """The limit is inclusive — exactly _MAX_QUERY_CHARS must pass."""
    server = _server([])
    resp = await server.handle_search(_request({"query": "x" * _MAX_QUERY_CHARS}))

    assert resp.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "ok", "top_k": "abc"},
        {"query": "ok", "top_k": None},
        {"query": "ok", "top_k": [5]},
        {"query": "ok", "confidence_threshold": "high"},
        {"query": "ok", "confidence_threshold": {}},
    ],
)
async def test_search_non_numeric_params_return_400(payload):
    """Non-numeric top_k / confidence_threshold return 400, never a 500."""
    server = _server([_chunk()])
    resp = await server.handle_search(_request(payload))

    assert resp.status == 400
    assert "top_k" in _body(resp)["error"]
    assert server.retriever.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given, expected",
    [(0, 1), (-5, 1), (1, 1), (100, 100), (101, 100), (10_000, 100)],
)
async def test_search_top_k_is_clamped(given, expected):
    """top_k clamps to 1..100 rather than being rejected or passed through."""
    server = _server([])
    resp = await server.handle_search(_request({"query": "ok", "top_k": given}))

    assert resp.status == 200
    assert server.retriever.calls[0]["top_k"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given, expected",
    [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.5, 1.0)],
)
async def test_search_confidence_threshold_is_clamped(given, expected):
    """confidence_threshold clamps to 0.0..1.0."""
    server = _server([])
    resp = await server.handle_search(
        _request({"query": "ok", "confidence_threshold": given})
    )

    assert resp.status == 200
    assert server.retriever.calls[0]["confidence_threshold"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_search_defaults_when_params_omitted():
    """Omitted params fall back to top_k=10, confidence_threshold=0.2."""
    server = _server([])
    resp = await server.handle_search(_request({"query": "ok"}))

    assert resp.status == 200
    assert server.retriever.calls[0]["top_k"] == 10
    assert server.retriever.calls[0]["confidence_threshold"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_search_query_is_stripped():
    """Surrounding whitespace is trimmed before the query reaches the retriever."""
    server = _server([])
    resp = await server.handle_search(_request({"query": "  cost tracking  "}))

    assert resp.status == 200
    assert server.retriever.calls[0]["query"] == "cost tracking"
