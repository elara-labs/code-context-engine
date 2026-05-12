import pytest
from context_engine.models import Chunk, ChunkType
from context_engine.indexer.embedder import Embedder, _resolve_parallel


@pytest.fixture
def embedder():
    return Embedder(model_name="all-MiniLM-L6-v2")


@pytest.fixture
def sample_chunks():
    return [
        Chunk(id="c1", content="def add(a, b): return a + b",
              chunk_type=ChunkType.FUNCTION, file_path="math.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c2", content="def subtract(a, b): return a - b",
              chunk_type=ChunkType.FUNCTION, file_path="math.py",
              start_line=3, end_line=3, language="python"),
    ]


def test_embed_chunks_adds_embeddings(embedder, sample_chunks):
    embedder.embed(sample_chunks)
    for chunk in sample_chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) > 0
        assert isinstance(chunk.embedding[0], float)


def test_embed_query_returns_vector(embedder):
    vec = embedder.embed_query("find the add function")
    assert len(vec) > 0
    assert isinstance(vec[0], float)


def test_embedding_dimensions_match(embedder, sample_chunks):
    embedder.embed(sample_chunks)
    query_vec = embedder.embed_query("test")
    assert len(sample_chunks[0].embedding) == len(query_vec)


def test_resolve_parallel_macos_defaults_to_none(monkeypatch):
    monkeypatch.delenv("CCE_EMBED_PARALLEL", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    assert _resolve_parallel() is None


def test_resolve_parallel_linux_uses_cpu_count(monkeypatch):
    monkeypatch.delenv("CCE_EMBED_PARALLEL", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert _resolve_parallel() == 4  # capped at 4


def test_resolve_parallel_env_override_wins(monkeypatch):
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "2")
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.cpu_count", lambda: 12)
    assert _resolve_parallel() == 2


def test_resolve_parallel_invalid_env_falls_through(monkeypatch):
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "not-a-number")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert _resolve_parallel() == 2


# ─── Issue #66 regression coverage ──────────────────────────────────────


def test_resolve_parallel_zero_disables(monkeypatch):
    """CCE_EMBED_PARALLEL=0 → None (single-process), not 1.

    The old behaviour `max(1, int(v))` floored 0 to 1, but parallel=1 still
    takes the multiprocessing path and orphans workers on shutdown
    (#66). Zero now means single-process.
    """
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "0")
    monkeypatch.setattr("sys.platform", "linux")
    assert _resolve_parallel() is None


@pytest.mark.parametrize("token", ["none", "off", "false", "no", "NONE", "Off"])
def test_resolve_parallel_string_tokens_disable(monkeypatch, token):
    monkeypatch.setenv("CCE_EMBED_PARALLEL", token)
    monkeypatch.setattr("sys.platform", "linux")
    assert _resolve_parallel() is None


def test_resolve_parallel_caps_at_cpu_count(monkeypatch):
    """CCE_EMBED_PARALLEL=64 on a 12-CPU host must not actually spawn 64."""
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "64")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.cpu_count", lambda: 12)
    assert _resolve_parallel() == 12


def test_resolve_parallel_negative_disables(monkeypatch):
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "-1")
    monkeypatch.setattr("sys.platform", "linux")
    assert _resolve_parallel() is None


def test_resolve_parallel_is_lazy(monkeypatch):
    """Resolution must happen on each call, not at import.

    `cce serve` relies on setting CCE_EMBED_PARALLEL=0 inside the function
    body and having subsequent embed calls observe it.
    """
    monkeypatch.delenv("CCE_EMBED_PARALLEL", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    first = _resolve_parallel()
    assert first == 4
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "0")
    second = _resolve_parallel()
    assert second is None
