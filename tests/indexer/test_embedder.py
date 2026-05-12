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
    assert _resolve_parallel() == 2


def test_resolve_parallel_invalid_env_falls_through(monkeypatch):
    monkeypatch.setenv("CCE_EMBED_PARALLEL", "not-a-number")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert _resolve_parallel() == 2


# ─── Issue #67 regression coverage ──────────────────────────────────────


def test_resolve_cache_dir_default(monkeypatch, tmp_path):
    """No env var set → ~/.cache/fastembed, NOT /tmp."""
    from context_engine.indexer.embedder import _resolve_cache_dir
    monkeypatch.delenv("CCE_FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))
    got = _resolve_cache_dir()
    assert got == fake_home / ".cache" / "fastembed"


def test_resolve_cache_dir_respects_fastembed_env(monkeypatch, tmp_path):
    from context_engine.indexer.embedder import _resolve_cache_dir
    monkeypatch.delenv("CCE_FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "custom"))
    assert _resolve_cache_dir() == tmp_path / "custom"


def test_resolve_cache_dir_cce_override_wins(monkeypatch, tmp_path):
    """CCE_FASTEMBED_CACHE_PATH overrides fastembed's own env var so users
    with multiple tools sharing the fastembed default can isolate CCE's
    cache."""
    from context_engine.indexer.embedder import _resolve_cache_dir
    monkeypatch.setenv("CCE_FASTEMBED_CACHE_PATH", str(tmp_path / "cce_path"))
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fast_path"))
    assert _resolve_cache_dir() == tmp_path / "cce_path"


def test_resolve_cache_dir_xdg(monkeypatch, tmp_path):
    from context_engine.indexer.embedder import _resolve_cache_dir
    monkeypatch.delenv("CCE_FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert _resolve_cache_dir() == tmp_path / "xdg" / "fastembed"


def test_sweep_incomplete_removes_stale_partial(tmp_path):
    """Issue #67: a stalled huggingface_hub download leaves a 0-byte
    `model_optimized.onnx.incomplete` file that crashes every subsequent
    load. We must remove these on startup."""
    from context_engine.indexer.embedder import _sweep_incomplete_downloads
    nested = tmp_path / "models--qdrant--bge" / "snapshots" / "abc"
    nested.mkdir(parents=True)
    bad = nested / "model_optimized.onnx.incomplete"
    bad.write_bytes(b"")
    good = nested / "tokenizer.json"
    good.write_text("{}")

    removed = _sweep_incomplete_downloads(tmp_path)
    assert removed == 1
    assert not bad.exists()
    assert good.exists()  # other cache files must survive


def test_sweep_incomplete_missing_dir_is_noop(tmp_path):
    from context_engine.indexer.embedder import _sweep_incomplete_downloads
    missing = tmp_path / "does_not_exist_yet"
    assert _sweep_incomplete_downloads(missing) == 0
