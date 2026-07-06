# Confidence Scoring

CCE ranks every retrieved chunk with a confidence score before returning it.

## What the score combines

1. **Vector similarity score (50%)**
   - Uses cosine-distance from the embedding query match.
   - Computed as `max(0, 1 - vector_distance)`.

2. **Keyword/file-hint score (40%)**
   - Uses parser signal such as matched keywords and file hints.
   - A short distance metric is converted to a score in `[0, 1]`.

3. **Recency score (10%)**
   - Newer chunks get higher weight using exponential decay.
   - Missing `modified_ts` metadata defaults to a neutral score of `0.5`.
   - Half-life is one week.

## Formula

The final confidence value is a weighted sum:

```python
confidence = (0.5 * vector_score) + (0.4 * keyword_score) + (0.1 * recency_score)
```

Clamped to `[0.0, 1.0]`.

## Final ranking stages

The confidence value is merged with the hybrid retriever signal (RRF) before filtering:

- Hybrid vector + full-text scores are merged first (RRF).
- Each chunk gets a blended score (confidence + normalized RRF).
- Chunks below `confidence_threshold` are dropped.
- Remaining chunks are sorted high-to-low by final score.

A higher score means the chunk is considered more relevant and trustworthy for that query.

## `confidence_threshold`

Configured under `retrieval.confidence_threshold` in `~/.cce/config.yaml`:

- Lower values (for example `0.2`) return more results.
- Higher values (for example `0.7`) return fewer but tighter matches.
- Default is `0.5`.

```yaml
retrieval:
  confidence_threshold: 0.5
```

## Practical tuning

- If your queries feel too narrow, lower the threshold slightly (for example `0.4`).
- If you want cleaner, fewer results, raise it (for example `0.7`).
- The value is applied after all scoring and ranking, so it only controls output filtering.
