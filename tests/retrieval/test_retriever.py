# tests/retrieval/test_retriever.py
import pytest
import pytest_asyncio
from context_engine.models import Chunk, ChunkType, GraphNode, GraphEdge, NodeType, EdgeType
from context_engine.storage.local_backend import LocalBackend
from context_engine.indexer.embedder import Embedder
from context_engine.retrieval.retriever import HybridRetriever

@pytest.fixture
def backend(tmp_path):
    return LocalBackend(base_path=str(tmp_path))

@pytest.fixture
def embedder():
    return Embedder(model_name="all-MiniLM-L6-v2")

@pytest.fixture
def retriever(backend, embedder):
    return HybridRetriever(backend=backend, embedder=embedder)

@pytest_asyncio.fixture
async def seeded_retriever(retriever, backend, embedder):
    chunks = [
        Chunk(id="c1", content="def add(a, b): return a + b",
              chunk_type=ChunkType.FUNCTION, file_path="math.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c2", content="def multiply(a, b): return a * b",
              chunk_type=ChunkType.FUNCTION, file_path="math.py",
              start_line=3, end_line=3, language="python"),
        Chunk(id="c3", content="class UserAuth: handles user authentication and login",
              chunk_type=ChunkType.CLASS, file_path="auth.py",
              start_line=1, end_line=10, language="python"),
    ]
    embedder.embed(chunks)
    nodes = [
        GraphNode(id="func_add", node_type=NodeType.FUNCTION, name="add", file_path="math.py"),
        GraphNode(id="func_mul", node_type=NodeType.FUNCTION, name="multiply", file_path="math.py"),
        GraphNode(id="cls_auth", node_type=NodeType.CLASS, name="UserAuth", file_path="auth.py"),
    ]
    edges = [
        GraphEdge(source_id="func_add", target_id="func_mul", edge_type=EdgeType.CALLS),
    ]
    await backend.ingest(chunks, nodes, edges)
    return retriever

@pytest.mark.asyncio
async def test_retrieve_returns_scored_results(seeded_retriever):
    results = await seeded_retriever.retrieve("addition function", top_k=5)
    assert len(results) > 0
    assert all(c.confidence_score > 0 for c in results)

@pytest.mark.asyncio
async def test_retrieve_sorts_by_confidence(seeded_retriever):
    results = await seeded_retriever.retrieve("add numbers", top_k=5)
    scores = [c.confidence_score for c in results]
    assert scores == sorted(scores, reverse=True)

@pytest.mark.asyncio
async def test_retrieve_respects_top_k(seeded_retriever):
    results = await seeded_retriever.retrieve("function", top_k=2)
    assert len(results) <= 2

@pytest.mark.asyncio
async def test_retrieve_with_max_tokens(seeded_retriever):
    """Token packing respects budget."""
    results = await seeded_retriever.retrieve("function", top_k=10, max_tokens=50)
    total_tokens = sum(c.token_count for c in results)
    assert total_tokens <= 50
