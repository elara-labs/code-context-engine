"""Tests for token efficiency features: overflow references and graph expansion."""
import pytest

from context_engine.models import Chunk, ChunkType


def _make_chunk(chunk_id: str, file_path: str, content: str, confidence: float = 0.8) -> Chunk:
    c = Chunk(
        id=chunk_id,
        content=content,
        chunk_type=ChunkType.FUNCTION,
        file_path=file_path,
        start_line=1,
        end_line=10,
        language="python",
    )
    c.confidence_score = confidence
    return c


# ── Overflow references ───────────────────────────────────────────────────────

def test_overflow_format_contains_expand_hints():
    """When results exceed token budget, overflow chunk IDs appear in output."""
    from context_engine.integration.mcp_server import _format_results_with_overflow

    inline_chunk = _make_chunk("id-1", "auth.py", "x" * 100, confidence=0.9)
    overflow_chunk = _make_chunk("id-2", "payments.py", "y" * 500, confidence=0.75)

    body = _format_results_with_overflow([inline_chunk], [overflow_chunk])

    assert "id-2" in body
    assert "payments.py" in body
    assert "expand_chunk" in body


def test_overflow_format_no_overflow():
    """When all results fit inline, no overflow section is added."""
    from context_engine.integration.mcp_server import _format_results_with_overflow

    chunk = _make_chunk("id-1", "auth.py", "x" * 100, confidence=0.9)

    body = _format_results_with_overflow([chunk], [])

    assert "expand_chunk" not in body
    assert "more result" not in body


def test_overflow_split_respects_token_budget():
    """Chunks exceeding max_tokens go to overflow, not inline."""
    from context_engine.integration.mcp_server import _split_inline_overflow

    # Each char ~0.3 tokens, so 3300 chars ≈ 1000 tokens
    big = _make_chunk("big", "big.py", "x" * 3300)
    small = _make_chunk("small", "small.py", "y" * 33)  # ~10 tokens

    inline, overflow = _split_inline_overflow([big, small], max_tokens=50)

    assert small in inline
    assert big in overflow


# ── Graph-aware retrieval ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_related_file_paths_returns_neighbors(tmp_path):
    """get_related_file_paths returns file paths of CALLS/IMPORTS neighbors."""
    from context_engine.storage.local_backend import LocalBackend
    from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType

    backend = LocalBackend(base_path=str(tmp_path))

    # Ingest two nodes with a CALLS edge
    node_a = GraphNode(id="fn-a", node_type=NodeType.FUNCTION, name="fn_a",
                       file_path="auth.py", properties={})
    node_b = GraphNode(id="fn-b", node_type=NodeType.FUNCTION, name="fn_b",
                       file_path="utils.py", properties={})
    edge = GraphEdge(source_id="fn-a", target_id="fn-b",
                     edge_type=EdgeType.CALLS, properties={})
    await backend._graph_store.ingest([node_a, node_b], [edge])

    related = await backend.get_related_file_paths(["auth.py"])

    assert "utils.py" in related
    assert "auth.py" not in related  # source file excluded


@pytest.mark.asyncio
async def test_get_related_file_paths_empty_when_no_graph(tmp_path):
    """Returns empty list when no graph edges exist."""
    from context_engine.storage.local_backend import LocalBackend

    backend = LocalBackend(base_path=str(tmp_path))

    related = await backend.get_related_file_paths(["nofile.py"])

    assert related == []
