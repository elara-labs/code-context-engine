# Lightweight Install: Pluggable Embedding Backends

**Date:** 2026-05-01
**Status:** Approved

## Problem

Installing `code-context-engine` pulls 189 MB of dependencies. 172 MB of that
is fastembed + ONNX Runtime + numpy + Pillow, used solely for embedding
generation. The core engine (indexing, search, MCP server, hooks, compression)
needs only ~17 MB.

For a developer tool, 189 MB is heavy. Users who already run Ollama (common
for compression) gain nothing from bundling a second inference runtime.

## Solution

Make fastembed an optional dependency. Add an Ollama embedding backend that
uses the existing local Ollama server via HTTP. Auto-detect which backend is
available at runtime.

### Install paths

- `pip install code-context-engine` = core only (~17 MB)
- `pip install code-context-engine[local]` = includes fastembed (~189 MB)

### Backend auto-detection

On `cce init` and any embedding operation, the `Embedder` class picks a
backend automatically:

1. fastembed importable? Use `FastembedBackend`.
2. Ollama running at `localhost:11434`? Use `OllamaBackend` with
   `nomic-embed-text` (pulls model automatically if missing).
3. Neither available? Raise a clear error listing both install options.

No config fields for backend selection. Auto-detection covers all cases.
If a user explicitly wants one backend over the other, they control it by
installing or uninstalling fastembed.

### Embedding backend protocol

```python
class EmbeddingBackend(Protocol):
    def embed_texts(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Embed a list of texts, return list of float vectors."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...
```

### FastembedBackend

Wraps the existing `TextEmbedding` usage. Behavior identical to current code.
Lives in `embedder.py` behind a lazy import so the module loads even when
fastembed is not installed.

### OllamaBackend

Calls Ollama's `/api/embed` HTTP endpoint (no Python SDK needed).
Default model: `nomic-embed-text` (768 dims, widely used).

- Uses `requests.post` or `urllib` (requests is already a dependency).
- Batches texts in a single API call (Ollama supports batch embedding).
- If the model is not pulled, calls `/api/pull` with a progress message.

### Dimension mismatch handling

Different backends produce different embedding dimensions (bge-small: 384,
nomic-embed-text: 768). When `cce init` or `cce index` runs:

1. Read the stored embedding dimension from the index metadata.
2. Compare with the current backend's dimension.
3. If mismatched, auto-reindex with a message explaining why.

This prevents cryptic search errors when users switch backends.

## Files to change

| File | Change |
|---|---|
| `pyproject.toml` | Move `fastembed` to `[project.optional-dependencies] local = ["fastembed>=0.4"]` |
| `src/context_engine/indexer/embedder.py` | Add `EmbeddingBackend` protocol, `FastembedBackend`, `OllamaBackend`, auto-detect factory, update `Embedder` to delegate |
| `src/context_engine/cli.py` | Update `cce init` to show which backend was detected, handle model pull for Ollama |
| `src/context_engine/indexer/pipeline.py` | No change (uses `Embedder` public API) |
| `src/context_engine/config.py` | Add `ollama_embed_model` field (default: `nomic-embed-text`) |
| Tests | Mock HTTP for Ollama backend, test fastembed-missing fallback, test dimension mismatch reindex |

## What stays the same

- `Embedder.embed(chunks)` and `Embedder.embed_query(query)` public API
- `EmbeddingCache` (backend-agnostic, caches by content hash)
- Retriever, MCP server, CLI commands, compression
- All existing tests (fastembed path unchanged)

## Risks

- **Ollama model quality vs bge-small:** nomic-embed-text scores comparably
  on MTEB retrieval benchmarks. Retrieval quality should be equivalent.
- **Ollama availability:** Users without Ollama and without fastembed get a
  clear error. No silent failure.
- **Re-indexing on switch:** Automatic, but takes time on large repos. The
  message should set expectations.

## Out of scope

- Remote embedding APIs (OpenAI, Cohere). Can be added later as another
  backend behind the same protocol.
- Config-driven backend selection. Auto-detection is sufficient for now.
