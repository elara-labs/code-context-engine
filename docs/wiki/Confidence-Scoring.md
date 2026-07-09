# Confidence Scoring

CCE ranks every retrieved chunk with a confidence score before returning it.

## What the score combines

1. **Vector similarity score (50%)**
   - Uses cosine distance from the embedding query match.
   - The raw distance is normalized to `[0, 1]` by dividing by 2, then converted: `max(0, 1 - normalized_distance)`.

2. **Keyword/file-hint score (40%)**
   - Uses parser signal such as matched keywords and file hints.
   - A distance in `[0, 5]` is converted to a score: `max(0, 1 - keyword_distance / 5)`.

3. **Recency score (10%)**
   - Newer chunks get higher weight using exponential decay.
   - Missing `modified_ts` metadata defaults to a neutral score of `0.5`.
   - Half-life is one week.

## Formula

The confidence value is a weighted sum:

```python
confidence = (0.5 * vector_score) + (0.4 * keyword_score) + (0.1 * recency_score)
```

Clamped to `[0.0, 1.0]`.

## Final ranking stages

The confidence value is blended with the hybrid retriever signal (RRF) before filtering:

- Hybrid vector + full-text scores are merged first (RRF), then normalized to `[0, 1]`.
- Each chunk gets a final score that is a 50/50 blend: `0.5 * confidence + 0.5 * normalized_rrf`.
- Path penalties are applied (test files and docs are down-weighted by 20%).
- Chunks below `confidence_threshold` are dropped.
- Remaining chunks are sorted high-to-low by final score.

Note: `confidence_threshold` is compared against the blended final score, not the raw confidence value. A chunk with a strong RRF rank can pass the threshold even if its raw confidence score is modest.

A higher score means the chunk is considered more relevant and trustworthy for that query.

## `confidence_threshold`

Configured under `retrieval.confidence_threshold` in `~/.cce/config.yaml`:

- Lower values (for example `0.1`) return more results.
- Higher values (for example `0.7`) return fewer but tighter matches.
- Default is `0.2`.

```yaml
retrieval:
  confidence_threshold: 0.2
```

## Practical tuning

- If your queries feel too narrow, lower the threshold slightly (for example `0.1`).
- If you want cleaner, fewer results, raise it (for example `0.5`).
- The value is compared against the blended score (confidence + RRF), so it controls both relevance and retrieval rank.
