"""Tests for the extractive summariser (PR 3)."""
from __future__ import annotations

from context_engine.memory import extractive


class _StubEmbedder:
    """Returns a deterministic vector — the position of the keyword 'KEY'.

    Sentences containing KEY get a vector pointing in one direction; others
    point in another. Centroid lies near KEY-bearing sentences if more than
    half the sentences mention it.
    """

    def embed_query(self, text: str) -> list[float]:
        if "KEY" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


def test_split_sentences_basic():
    text = "Hello there. How are you? I am fine!"
    assert extractive.split_sentences(text) == [
        "Hello there.", "How are you?", "I am fine!",
    ]


def test_split_sentences_handles_newlines():
    text = "Line one.\nLine two\nLine three.\n\n"
    out = extractive.split_sentences(text)
    assert "Line one." in out
    assert "Line two" in out
    assert "Line three." in out


def test_extractive_returns_centroid_neighbours():
    text = (
        "KEY one is special. "
        "KEY two also matters. "
        "KEY three is here. "
        "Random noise. "
        "Another distractor."
    )
    summary = extractive.extractive_summary(
        text, embedder=_StubEmbedder(), top_k=3,
    )
    # Top 3 should all be KEY-bearing sentences (since 3/5 sentences carry
    # KEY, the centroid points toward them).
    assert summary.count("KEY") == 3


def test_extractive_short_input_returns_verbatim():
    text = "Only one sentence here."
    summary = extractive.extractive_summary(
        text, embedder=_StubEmbedder(), top_k=3,
    )
    assert summary == "Only one sentence here."


def test_extractive_preserves_source_order():
    text = "First. Second. Third. Fourth. Fifth."
    summary = extractive.extractive_summary(
        text, embedder=_StubEmbedder(), top_k=3,
    )
    # The stub gives all sentences identical embeddings (none have KEY),
    # so any 3 are equally central. Whatever 3 we pick must be in source
    # order — verify by checking for monotonic substring positions.
    positions = [text.find(s) for s in summary.split() if s.endswith(".")]
    assert positions == sorted(positions)


def test_truncation_summary_short_input():
    assert extractive.truncation_summary("hi") == "hi"


def test_truncation_summary_long_input():
    text = "x" * 500
    out = extractive.truncation_summary(text, max_chars=50)
    assert len(out) == 50
    assert out.endswith("…")
