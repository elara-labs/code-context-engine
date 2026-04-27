"""Extractive summarisation using the embedding model already loaded for the index.

The summary is always real text from the source — no synthesis, no
hallucination. Algorithm:

  1. Split candidate text into sentences.
  2. Embed each with bge-small (or whatever embedder is passed in).
  3. Compute the centroid as the mean of all embeddings.
  4. Rank sentences by cosine similarity to the centroid.
  5. Take the top K, restored to their original order.

Failure modes:
  - Empty / single-sentence input: return the input verbatim.
  - Embedder raises: caller falls back to truncation.

This module has no dependency on the rest of the memory package; it operates
on plain strings and any object exposing `embed_query(str) -> tuple[float]`.
"""
from __future__ import annotations

import re
from typing import Iterable, Protocol


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class _EmbedderLike(Protocol):
    def embed_query(self, query: str) -> Iterable[float]: ...


def split_sentences(text: str) -> list[str]:
    """Coarse sentence split. Good enough for chat-shaped text.

    Newlines are treated as sentence boundaries too — Claude often emits
    multi-line tool output where each line is its own statement.
    """
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pieces.extend(s.strip() for s in _SENTENCE_SPLIT.split(line) if s.strip())
    return pieces


def _cosine(a: list[float], b: list[float]) -> float:
    # No numpy here — keep this module standalone and free of imports the
    # caller might not want to pay for in cold paths.
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def extractive_summary(
    text: str,
    *,
    embedder: _EmbedderLike,
    top_k: int = 3,
) -> str:
    """Return the top-K most central sentences from `text`, in source order.

    Returns the input verbatim if there's nothing to rank.
    """
    sentences = split_sentences(text)
    if len(sentences) <= top_k:
        return " ".join(sentences) if sentences else text.strip()

    embeddings: list[list[float]] = []
    for s in sentences:
        v = list(embedder.embed_query(s))
        embeddings.append(v)

    if not embeddings or not embeddings[0]:
        return " ".join(sentences[:top_k])

    dim = len(embeddings[0])
    centroid = [0.0] * dim
    for emb in embeddings:
        for i in range(dim):
            centroid[i] += emb[i]
    n = len(embeddings)
    centroid = [c / n for c in centroid]

    scored = [
        (i, _cosine(emb, centroid))
        for i, emb in enumerate(embeddings)
    ]
    scored.sort(key=lambda p: p[1], reverse=True)
    chosen_indices = sorted(i for i, _ in scored[:top_k])
    return " ".join(sentences[i] for i in chosen_indices)


def truncation_summary(text: str, *, max_chars: int = 200) -> str:
    """Final fallback when the embedder isn't available."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
