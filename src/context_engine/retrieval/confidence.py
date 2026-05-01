"""Confidence scoring for retrieved chunks.

The score is a weighted sum of three factors, each normalised to [0, 1]:

- vector similarity: 1 - (cosine distance from query embedding).
- keyword / file-hint match: a lightweight query-parser bonus when the chunk's
  file path or content hits the parsed query intent. Replaces what used to be
  labelled "graph hops" before the graph store was removed.
- recency: exponential decay based on the chunk's `modified_ts` metadata.

The weights live here as module constants so they're easy to find and tune.
"""
import time
from context_engine.models import Chunk

_VECTOR_WEIGHT = 0.5
_KEYWORD_WEIGHT = 0.4
_RECENCY_WEIGHT = 0.1
_MAX_KEYWORD_DISTANCE = 5
_RECENCY_HALF_LIFE = 7 * 24 * 3600  # 1 week


class ConfidenceScorer:
    def score(
        self,
        chunk: Chunk,
        vector_distance: float,
        keyword_distance: int,
    ) -> float:
        vector_score = max(0.0, 1.0 - vector_distance)
        keyword_score = max(0.0, 1.0 - (keyword_distance / _MAX_KEYWORD_DISTANCE))
        recency_score = self._recency_score(chunk)
        combined = (
            _VECTOR_WEIGHT * vector_score
            + _KEYWORD_WEIGHT * keyword_score
            + _RECENCY_WEIGHT * recency_score
        )
        return min(1.0, max(0.0, combined))

    def _recency_score(self, chunk: Chunk) -> float:
        modified_ts = chunk.metadata.get("modified_ts")
        if modified_ts is None:
            return 0.5
        age_seconds = time.time() - modified_ts
        if age_seconds <= 0:
            return 1.0
        return 0.5 ** (age_seconds / _RECENCY_HALF_LIFE)
