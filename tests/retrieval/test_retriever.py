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


# ---------------------------------------------------------------------------
# FTS-only chunks must not get free vector-similarity credit
# ---------------------------------------------------------------------------

def _mk_chunk(chunk_id: str, distance: float | None = None) -> Chunk:
    chunk = Chunk(id=chunk_id, content=f"def {chunk_id}(): pass",
                  chunk_type=ChunkType.FUNCTION, file_path=f"{chunk_id}.py",
                  start_line=1, end_line=1, language="python")
    if distance is not None:
        chunk.metadata["_distance"] = distance
    return chunk


class _StubEmbedder:
    def embed_query(self, query):
        return (0.1, 0.2, 0.3, 0.4)


class _StubBackend:
    """Minimal backend: fixed vector results, fixed FTS hits, hydration map.

    Deliberately has no get_related_file_paths so graph expansion is skipped.
    """

    def __init__(self, vector_chunks, fts_results, hydrated):
        self._vector_chunks = vector_chunks
        self._fts_results = fts_results
        self._hydrated = hydrated

    async def vector_search(self, query_embedding, top_k=10, filters=None):
        return list(self._vector_chunks)

    async def fts_search(self, query, top_k=30):
        return list(self._fts_results)

    async def get_chunks_by_ids(self, chunk_ids):
        return [self._hydrated[i] for i in chunk_ids if i in self._hydrated]


def _spy_scorer(retriever, seen: dict):
    orig = retriever._scorer.score

    def spy(chunk, vector_distance, keyword_distance):
        seen[chunk.id] = vector_distance
        return orig(chunk, vector_distance=vector_distance,
                    keyword_distance=keyword_distance)

    retriever._scorer.score = spy


@pytest.mark.asyncio
async def test_fts_only_chunk_gets_no_free_vector_credit():
    """A chunk hydrated from an FTS-only hit (no _distance metadata) must not
    receive a better (lower) vector distance than a genuine vector hit at
    moderate distance. Regression: the 0.0 default meant perfect similarity."""
    vec_moderate = _mk_chunk("vec_moderate", distance=0.4)
    vec_far = _mk_chunk("vec_far", distance=1.2)
    fts_only = _mk_chunk("fts_only")

    backend = _StubBackend(
        vector_chunks=[vec_moderate, vec_far],
        fts_results=[("fts_only", -5.0)],
        hydrated={"fts_only": fts_only},
    )
    retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    seen: dict[str, float] = {}
    _spy_scorer(retriever, seen)

    await retriever.retrieve("some query", top_k=5)

    assert "fts_only" in seen and "vec_moderate" in seen
    # Higher normalised distance == lower vector-similarity component.
    assert seen["fts_only"] >= seen["vec_moderate"]
    # It should also be at least as bad as the worst genuine vector hit.
    assert seen["fts_only"] >= seen["vec_far"]


@pytest.mark.asyncio
async def test_fts_only_without_vector_results_gets_worst_case_distance():
    """With no vector evidence at all, FTS-only chunks get the worst-case
    cosine distance (normalised to 1.0), not a perfect 0.0."""
    fts_only = _mk_chunk("fts_only")
    backend = _StubBackend(
        vector_chunks=[],
        fts_results=[("fts_only", -5.0)],
        hydrated={"fts_only": fts_only},
    )
    retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    seen: dict[str, float] = {}
    _spy_scorer(retriever, seen)

    await retriever.retrieve("some query", top_k=5)

    assert seen["fts_only"] == pytest.approx(1.0)
