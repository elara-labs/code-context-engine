"""Embedding generation with pluggable backends.

Two backends are supported:

  - FastembedBackend  — local ONNX via the optional `fastembed` package.
    Same behaviour and model defaults as previous releases. Requires the
    ``pip install code-context-engine[local]`` extra.

  - OllamaBackend     — talks to a local Ollama server over HTTP. No
    Python ML stack required, so the core install stays ~17 MB. Default
    model: ``nomic-embed-text`` (768 dims).

The public :class:`Embedder` is unchanged — it auto-detects an available
backend (fastembed first, then Ollama) and delegates ``embed`` /
``embed_query`` to it. An optional :class:`EmbeddingCache` short-circuits
the backend for chunks whose content has been seen before; this is shared
across backends, keyed by content hash + model identifier.
"""
import logging
import os
import sys
from functools import lru_cache
from typing import Protocol, runtime_checkable

from context_engine.indexer.embedding_cache import EmbeddingCache
from context_engine.models import Chunk

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Passed straight to fastembed's `parallel` argument:
#   None → no data-parallel mp; use onnxruntime's own threading
#   N>0  → spawn N forkserver workers around onnxruntime
#
# Even parallel=1 takes the multiprocessing path — and that path deadlocks on
# macOS (workers idle on SimpleQueue.get while the main thread sits in
# asyncio.poll, leaving `cce init` stuck after the file-scan progress bar
# hits 100%). On Windows, ONNX Runtime worker processes crash with
# ACCESS_VIOLATION (0xC0000005) due to DLL handle inheritance issues.
# Default to None on darwin and win32; allow override via CCE_EMBED_PARALLEL.
def _resolve_parallel() -> int | None:
    override = os.environ.get("CCE_EMBED_PARALLEL")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    if sys.platform == "darwin":
        return None
    if sys.platform == "win32":
        return None
    return min(os.cpu_count() or 2, 4)


_PARALLEL: int | None = _resolve_parallel()


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Minimal interface every embedding source must satisfy."""

    name: str
    model_name: str
    dimension: int

    def embed_texts(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        ...

    def embed_query(self, query: str) -> list[float]:
        ...


def _fastembed_available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _ollama_available(base_url: str = _DEFAULT_OLLAMA_URL) -> bool:
    """Cheap reachability probe — does an Ollama server answer on /api/tags?"""
    try:
        import httpx
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


class FastembedBackend:
    """Wraps fastembed's TextEmbedding. Identical semantics to pre-0.4.20."""

    name = "fastembed"

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        # Lazy import keeps the module importable when fastembed is not
        # installed — the `[local]` extra is now optional.
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed. Install the local-embedding "
                "extra with `pip install code-context-engine[local]`, "
                "or start an Ollama server at localhost:11434."
            ) from exc

        # Resolve short names ("all-MiniLM-L6-v2") to the qualified
        # sentence-transformers/* path fastembed expects, but leave fully
        # qualified names alone.
        resolved = (
            f"sentence-transformers/{model_name}"
            if "/" not in model_name
            else model_name
        )
        self.model_name = resolved
        try:
            self._model = TextEmbedding(resolved)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model '{model_name}'. "
                f"Ensure fastembed is installed and the model name is valid. "
                f"Original error: {exc}"
            ) from exc
        # Probe one vector to learn the dimension (fastembed doesn't expose
        # it on the model object directly).
        probe = next(iter(self._model.embed(["_"])))
        self.dimension = len(probe.tolist())

    def embed_texts(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for emb in self._model.embed(texts, batch_size=batch_size, parallel=_PARALLEL):
            out.append(emb.tolist())
        return out

    def iter_embed(self, texts: list[str], batch_size: int = 64):
        """Streaming variant used by Embedder for per-chunk progress callbacks."""
        for emb in self._model.embed(texts, batch_size=batch_size, parallel=_PARALLEL):
            yield emb.tolist()

    def embed_query(self, query: str) -> list[float]:
        results = list(self._model.query_embed(query))
        return list(results[0].tolist())


class OllamaBackend:
    """Embeds via a local Ollama server. Zero Python ML deps."""

    name = "ollama"

    def __init__(
        self,
        model_name: str = _DEFAULT_OLLAMA_MODEL,
        base_url: str = _DEFAULT_OLLAMA_URL,
        timeout: float = 60.0,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._ensure_model()
        # Learn dimension by embedding a single probe token.
        probe = self._embed_batch(["_"])
        if not probe:
            raise RuntimeError(
                f"Ollama returned no embedding for model '{model_name}'. "
                "Verify the model exists with `ollama pull "
                f"{model_name}`."
            )
        self.dimension = len(probe[0])

    def _ensure_model(self) -> None:
        """If the model isn't pulled yet, pull it (one-time cost)."""
        import httpx
        try:
            tags = httpx.get(f"{self.base_url}/api/tags", timeout=self._timeout).json()
        except Exception as exc:
            raise RuntimeError(
                f"Ollama not reachable at {self.base_url}: {exc}"
            ) from exc
        installed = {m["name"].split(":")[0] for m in tags.get("models", [])}
        if self.model_name.split(":")[0] in installed:
            return
        log.info("Pulling Ollama embedding model %s (first run only)...", self.model_name)
        # /api/pull streams NDJSON progress; we just need the final 200.
        with httpx.stream(
            "POST",
            f"{self.base_url}/api/pull",
            json={"name": self.model_name, "stream": False},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for _ in resp.iter_lines():
                pass

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model_name, "input": texts},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("embeddings", [])

    def embed_texts(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._embed_batch(texts[i:i + batch_size]))
        return out

    def iter_embed(self, texts: list[str], batch_size: int = 64):
        for i in range(0, len(texts), batch_size):
            for vec in self._embed_batch(texts[i:i + batch_size]):
                yield vec

    def embed_query(self, query: str) -> list[float]:
        result = self._embed_batch([query])
        if not result:
            raise RuntimeError(
                f"Ollama returned empty embedding for query (model={self.model_name})"
            )
        return result[0]


def select_backend(
    *,
    model_name: str | None = None,
    ollama_model: str = _DEFAULT_OLLAMA_MODEL,
    ollama_url: str = _DEFAULT_OLLAMA_URL,
    prefer: str | None = None,
) -> EmbeddingBackend:
    """Pick the first available backend.

    Order: fastembed (if installed) → Ollama (if reachable). Override via
    ``prefer`` ("fastembed" | "ollama") or env var ``CCE_EMBED_BACKEND``.
    Raises RuntimeError with a clear two-option remediation when neither
    is available.
    """
    forced = (prefer or os.environ.get("CCE_EMBED_BACKEND") or "").strip().lower()

    if forced == "fastembed":
        return FastembedBackend(model_name or _DEFAULT_MODEL)
    if forced == "ollama":
        return OllamaBackend(model_name=ollama_model, base_url=ollama_url)

    if _fastembed_available():
        return FastembedBackend(model_name or _DEFAULT_MODEL)
    if _ollama_available(ollama_url):
        return OllamaBackend(model_name=ollama_model, base_url=ollama_url)

    raise RuntimeError(
        "No embedding backend available. Either:\n"
        "  1. Install local embeddings:  pip install code-context-engine[local]\n"
        f"  2. Start an Ollama server at {ollama_url} and pull {ollama_model}\n"
        "Then re-run the command."
    )


class Embedder:
    """Public embedding facade — delegates to the auto-detected backend.

    Backwards compatible with the pre-0.4.20 API: callers passing
    ``model_name`` get fastembed if it's installed, exactly like before.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        cache: EmbeddingCache | None = None,
        *,
        ollama_model: str = _DEFAULT_OLLAMA_MODEL,
        ollama_url: str = _DEFAULT_OLLAMA_URL,
        backend: EmbeddingBackend | None = None,
    ) -> None:
        self._cache = cache
        self._backend: EmbeddingBackend = backend or select_backend(
            model_name=model_name,
            ollama_model=ollama_model,
            ollama_url=ollama_url,
        )

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def model_name(self) -> str:
        return self._backend.model_name

    @property
    def dimension(self) -> int:
        return self._backend.dimension

    def embed(
        self,
        chunks: list[Chunk],
        batch_size: int = 64,
        progress_fn=None,
    ) -> None:
        """Embed chunks in-place. With a cache attached, only chunks whose
        content hash is not already in the cache go through the backend.

        `progress_fn(current, total)` is called as embedding proceeds, where
        `total` is the count of chunks that actually needed embedding (cache
        misses). Cache hits return instantly and don't trigger callbacks.
        """
        if not chunks:
            return

        if self._cache is None:
            self._embed_all(chunks, batch_size, progress_fn=progress_fn)
            return

        # Hash + batched lookup: one SQL roundtrip for the whole batch
        # instead of N roundtrips through the per-chunk get() path.
        hashes = [self._cache.content_hash(c.content) for c in chunks]
        cached = self._cache.get_batch(hashes)

        miss_indices: list[int] = []
        for i, h in enumerate(hashes):
            if h in cached:
                chunks[i].embedding = cached[h]
            else:
                miss_indices.append(i)

        if miss_indices:
            miss_chunks = [chunks[i] for i in miss_indices]
            self._embed_all(miss_chunks, batch_size, progress_fn=progress_fn)
            new_entries = [
                (hashes[i], chunks[i].embedding)
                for i in miss_indices
                if chunks[i].embedding is not None
            ]
            if new_entries:
                self._cache.put_batch(new_entries)

        cache_total = len(chunks)
        cache_hits = cache_total - len(miss_indices)
        if cache_hits > 0:
            log.info(
                "Embedding cache: %d/%d hits (%.0f%% reused)",
                cache_hits, cache_total, cache_hits / cache_total * 100,
            )

    def _embed_all(
        self,
        chunks: list[Chunk],
        batch_size: int = 64,
        progress_fn=None,
    ) -> None:
        """Embed all chunks via the backend, ticking progress per yielded vector."""
        texts = [c.content for c in chunks]
        total = len(texts)
        if progress_fn:
            progress_fn(0, total)
        # iter_embed yields one vector at a time so we can tick progress;
        # fall back to embed_texts for protocol-only backends.
        iterator = getattr(self._backend, "iter_embed", None)
        if iterator is None:
            vectors = self._backend.embed_texts(texts, batch_size=batch_size)
            for i, vec in enumerate(vectors):
                chunks[i].embedding = vec
                if progress_fn and ((i + 1) % batch_size == 0 or i + 1 == total):
                    progress_fn(i + 1, total)
            return
        for i, vec in enumerate(iterator(texts, batch_size=batch_size)):
            chunks[i].embedding = vec
            if progress_fn and ((i + 1) % batch_size == 0 or i + 1 == total):
                progress_fn(i + 1, total)

    @lru_cache(maxsize=256)
    def embed_query(self, query: str) -> tuple:
        """Embed a single query string. Returns tuple for LRU cache hashability.

        Callers that need a list (e.g. sqlite-vec) should use list(result).
        """
        return tuple(self._backend.embed_query(query))
