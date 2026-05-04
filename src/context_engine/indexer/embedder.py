"""Embedding generation using fastembed (lightweight ONNX-based embeddings).

Uses BAAI/bge-small-en-v1.5 by default — 33% smaller and better quality
than all-MiniLM-L6-v2. Parallel embedding for 3-4x faster indexing.

Supports an optional EmbeddingCache so unchanged code chunks are never
re-embedded across index runs (inspired by Cursor's content-hash cache).
"""
import logging
import os
import sys
from functools import lru_cache

from fastembed import TextEmbedding

from context_engine.indexer.embedding_cache import EmbeddingCache
from context_engine.models import Chunk

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

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


class Embedder:
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache: EmbeddingCache | None = None,
    ) -> None:
        self._cache = cache
        # Resolve short names: "all-MiniLM-L6-v2" → "sentence-transformers/all-MiniLM-L6-v2"
        # but leave fully qualified names like "BAAI/bge-small-en-v1.5" alone.
        if "/" not in model_name:
            resolved = f"sentence-transformers/{model_name}"
        else:
            resolved = model_name
        try:
            self._model = TextEmbedding(resolved)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model '{model_name}'. "
                f"Ensure fastembed is installed and the model name is valid. "
                f"Supported models: TextEmbedding.list_supported_models(). "
                f"Original error: {exc}"
            ) from exc

    def embed(
        self,
        chunks: list[Chunk],
        batch_size: int = 64,
        progress_fn=None,
    ) -> None:
        """Embed chunks in-place. With a cache attached, only chunks whose
        content hash is not already in the cache go through the model.

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
            # Persist newly-computed embeddings back to the cache.
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
        """Embed all chunks via the model (no cache).

        Iterates fastembed's generator one item at a time so we can tick a
        progress callback. The model still embeds in batches internally; we
        just observe one yielded vector at a time.
        """
        texts = [c.content for c in chunks]
        total = len(texts)
        if progress_fn:
            progress_fn(0, total)
        for i, emb in enumerate(self._model.embed(
            texts,
            batch_size=batch_size,
            parallel=_PARALLEL,
        )):
            chunks[i].embedding = emb.tolist()
            if progress_fn and ((i + 1) % batch_size == 0 or i + 1 == total):
                progress_fn(i + 1, total)

    @lru_cache(maxsize=256)
    def embed_query(self, query: str) -> tuple:
        """Embed a single query string. Returns tuple for LRU cache hashability.

        Callers that need a list (e.g. LanceDB) should use list(result)
        or the _to_list() helper in vector_store.
        """
        results = list(self._model.query_embed(query))
        return tuple(results[0].tolist())
