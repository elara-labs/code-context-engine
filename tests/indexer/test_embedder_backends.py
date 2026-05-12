"""Tests for the pluggable embedding backends (fastembed + Ollama).

These exercise the auto-detect factory, the Ollama HTTP path (mocked), the
error message when neither backend is available, and dimension-mismatch
detection on the manifest. The real fastembed path is covered by the
existing test_embedder.py.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from context_engine.indexer.embedder import (
    Embedder,
    FastembedBackend,
    OllamaBackend,
    select_backend,
    _fastembed_available,
    _ollama_available,
)
from context_engine.indexer.manifest import Manifest
from context_engine.models import Chunk, ChunkType


# ── Auto-detect ──────────────────────────────────────────────────────────


def test_select_backend_prefers_fastembed_when_available(monkeypatch):
    monkeypatch.delenv("CCE_EMBED_BACKEND", raising=False)
    monkeypatch.setattr(
        "context_engine.indexer.embedder._fastembed_available", lambda: True
    )
    monkeypatch.setattr(
        "context_engine.indexer.embedder._ollama_available", lambda url=None: True
    )
    with patch.object(FastembedBackend, "__init__", return_value=None) as init_mock:
        fake = FastembedBackend.__new__(FastembedBackend)
        fake.name = "fastembed"
        fake.dimension = 384
        fake.model_name = "x"
        with patch(
            "context_engine.indexer.embedder.FastembedBackend",
            return_value=fake,
        ) as ctor:
            backend = select_backend(model_name="m")
        assert backend is fake
        ctor.assert_called_once()
    _ = init_mock  # silence unused


def test_select_backend_falls_back_to_ollama(monkeypatch):
    monkeypatch.delenv("CCE_EMBED_BACKEND", raising=False)
    monkeypatch.setattr(
        "context_engine.indexer.embedder._fastembed_available", lambda: False
    )
    monkeypatch.setattr(
        "context_engine.indexer.embedder._ollama_available", lambda url=None: True
    )
    fake = OllamaBackend.__new__(OllamaBackend)
    fake.name = "ollama"
    fake.dimension = 768
    fake.model_name = "nomic-embed-text"
    with patch(
        "context_engine.indexer.embedder.OllamaBackend", return_value=fake
    ):
        backend = select_backend()
    assert backend is fake


def test_select_backend_errors_when_neither_available(monkeypatch):
    monkeypatch.delenv("CCE_EMBED_BACKEND", raising=False)
    monkeypatch.setattr(
        "context_engine.indexer.embedder._fastembed_available", lambda: False
    )
    monkeypatch.setattr(
        "context_engine.indexer.embedder._ollama_available", lambda url=None: False
    )
    with pytest.raises(RuntimeError) as exc:
        select_backend()
    msg = str(exc.value)
    assert "code-context-engine[local]" in msg
    assert "Ollama" in msg


def test_env_var_forces_backend(monkeypatch):
    """CCE_EMBED_BACKEND=ollama forces Ollama even if fastembed is available."""
    monkeypatch.setenv("CCE_EMBED_BACKEND", "ollama")
    monkeypatch.setattr(
        "context_engine.indexer.embedder._fastembed_available", lambda: True
    )
    fake = OllamaBackend.__new__(OllamaBackend)
    fake.name = "ollama"
    fake.dimension = 768
    fake.model_name = "nomic-embed-text"
    with patch(
        "context_engine.indexer.embedder.OllamaBackend", return_value=fake
    ) as ctor:
        backend = select_backend()
    assert backend.name == "ollama"
    ctor.assert_called_once()


# ── OllamaBackend HTTP plumbing ─────────────────────────────────────────


class _MockResp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_ollama_backend_pulls_missing_model(monkeypatch):
    """If /api/tags reports the model is not pulled, _ensure_model calls /api/pull."""
    pulled = []

    def fake_get(url, timeout=None):
        # No models installed yet.
        return _MockResp({"models": []})

    def fake_post(url, json=None, timeout=None):
        # The probe embed after pull returns a single 768-dim vector.
        if url.endswith("/api/embed"):
            return _MockResp({"embeddings": [[0.0] * 768]})
        raise AssertionError(f"unexpected POST to {url}")

    class _StreamCtx:
        def __init__(self, *a, **kw):
            pulled.append((a, kw))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self):
            return iter([json.dumps({"status": "pulling"})])

        def raise_for_status(self):
            pass

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.stream", lambda *a, **kw: _StreamCtx(*a, **kw))

    backend = OllamaBackend(model_name="nomic-embed-text")
    assert backend.dimension == 768
    assert backend.name == "ollama"
    assert len(pulled) == 1  # /api/pull was invoked exactly once


def test_ollama_backend_skips_pull_when_model_present(monkeypatch):
    """If the model is already pulled, /api/pull is never called."""
    pulled = []

    def fake_get(url, timeout=None):
        return _MockResp({"models": [{"name": "nomic-embed-text:latest"}]})

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/embed"):
            return _MockResp({"embeddings": [[0.0] * 768]})
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(
        "httpx.stream", lambda *a, **kw: pulled.append((a, kw)) or MagicMock()
    )

    OllamaBackend(model_name="nomic-embed-text")
    assert pulled == []


def test_ollama_backend_embed_texts_batches(monkeypatch):
    calls = []

    def fake_get(url, timeout=None):
        return _MockResp({"models": [{"name": "nomic-embed-text"}]})

    def fake_post(url, json=None, timeout=None):
        calls.append(json["input"])
        return _MockResp(
            {"embeddings": [[float(i)] * 4 for i in range(len(json["input"]))]}
        )

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)

    backend = OllamaBackend(model_name="nomic-embed-text")
    # The init probe is the first POST; reset and exercise embed_texts.
    calls.clear()
    out = backend.embed_texts(["a", "b", "c", "d", "e"], batch_size=2)
    assert len(out) == 5
    assert [len(c) for c in calls] == [2, 2, 1]


def test_ollama_backend_embed_query(monkeypatch):
    def fake_get(url, timeout=None):
        return _MockResp({"models": [{"name": "nomic-embed-text"}]})

    def fake_post(url, json=None, timeout=None):
        return _MockResp({"embeddings": [[0.1, 0.2, 0.3]]})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)

    backend = OllamaBackend(model_name="nomic-embed-text")
    vec = backend.embed_query("hello")
    assert vec == [0.1, 0.2, 0.3]


# ── Embedder integration ────────────────────────────────────────────────


class _StubBackend:
    name = "stub"
    model_name = "stub-model"
    dimension = 4

    def __init__(self):
        self.queries = 0

    def embed_texts(self, texts, batch_size=64):
        return [[float(i)] * 4 for i, _ in enumerate(texts)]

    def iter_embed(self, texts, batch_size=64):
        for i, _ in enumerate(texts):
            yield [float(i)] * 4

    def embed_query(self, query):
        self.queries += 1
        return [0.5, 0.5, 0.5, 0.5]


def test_embedder_delegates_to_supplied_backend():
    chunks = [
        Chunk(id="c1", content="a", chunk_type=ChunkType.FUNCTION,
              file_path="x.py", start_line=1, end_line=1, language="python"),
        Chunk(id="c2", content="b", chunk_type=ChunkType.FUNCTION,
              file_path="x.py", start_line=2, end_line=2, language="python"),
    ]
    emb = Embedder(backend=_StubBackend())
    emb.embed(chunks)
    assert chunks[0].embedding == [0.0] * 4
    assert chunks[1].embedding == [1.0] * 4
    assert emb.backend_name == "stub"
    assert emb.dimension == 4


def test_embedder_query_uses_backend():
    emb = Embedder(backend=_StubBackend())
    assert list(emb.embed_query("q")) == [0.5, 0.5, 0.5, 0.5]


# ── Manifest dimension migration ────────────────────────────────────────


def test_manifest_persists_embedding_dim(tmp_path):
    path = tmp_path / "manifest.json"
    m1 = Manifest(manifest_path=path)
    m1.embedding_dim = 384
    m1.update("a.py", "hash_a")
    m1.save()
    m2 = Manifest(manifest_path=path)
    assert m2.embedding_dim == 384


def test_manifest_clear_entries_drops_files(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest(manifest_path=path)
    m.update("a.py", "h1")
    m.update("b.py", "h2")
    m.clear_entries()
    assert m.get_hash("a.py") is None
    assert m.get_hash("b.py") is None


def test_manifest_dim_defaults_to_none(tmp_path):
    """A virgin manifest reports dim=None so the migration check skips it."""
    m = Manifest(manifest_path=tmp_path / "manifest.json")
    assert m.embedding_dim is None


# ── Helper probes ───────────────────────────────────────────────────────


def test_fastembed_available_returns_bool():
    # Both True and False are acceptable; the function must not raise.
    assert isinstance(_fastembed_available(), bool)


def test_ollama_available_handles_unreachable(monkeypatch):
    def boom(*a, **kw):
        raise OSError("no route to host")

    monkeypatch.setattr("httpx.get", boom)
    assert _ollama_available("http://10.255.255.1:11434") is False
