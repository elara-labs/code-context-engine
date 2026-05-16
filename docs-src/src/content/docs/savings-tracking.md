---
title: Savings Tracking
description: How to measure and understand token savings with cce savings and cce dashboard.
---

CCE tracks every query made through the MCP server and records how many tokens were served versus how many would have been needed without CCE. This data powers the `cce savings` command and the dashboard.

## Using `cce savings`

```bash
cce savings
```

Example output:

```
  my-project · 42 queries

  ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶  93% tokens saved

  Without CCE   48.0k  tokens   $0.24
  With CCE       3.4k  tokens   $0.02
  ──────────────────────────────────────────
  Saved         44.6k  tokens   $0.22
  ~81 tokens / query  ~<$0.01 / query

  How:  retrieval 93%  +  compression 90%
  Cost estimate based on Opus input pricing ($5/1M tokens)
```

## Understanding the input/output split

Savings come from two independent stages:

- **Retrieval savings (input).** Instead of sending the entire codebase, CCE returns only the chunks relevant to the query. This is measured as: `1 - (served_tokens / full_codebase_tokens)`.

- **Compression savings (input).** The retrieved chunks are further compressed (truncation, summarization) before being sent to the agent. This is measured as: `1 - (compressed_tokens / raw_chunk_tokens)`.

The combined effect is multiplicative. If retrieval cuts 90% and compression cuts another 50%, the total savings are 95%.

## Per-bucket breakdown

The `How:` line in the output shows the contribution of each stage:

```
How:  retrieval 93%  +  compression 90%
```

- **retrieval** represents the savings from selecting only relevant chunks.
- **compression** represents the savings from compressing those chunks.

## Configuring the pricing model

Cost estimates use model-specific input pricing. Configure which model to estimate for:

```yaml
# ~/.cce/config.yaml or .context-engine.yaml
pricing:
  model: opus    # opus (default) | sonnet | haiku
```

Prices are fetched from Anthropic's documentation and cached for 7 days.

## Using `cce dashboard`

```bash
cce dashboard
```

The dashboard opens in your browser and provides a visual view of:

- Total tokens saved over time (line chart).
- Per-query breakdown.
- Compression level controls (change input/output compression live).
- File staleness detection.

## Cross-project savings

```bash
cce savings --all
```

Shows a combined report across every project you have indexed, useful for understanding total cost reduction.

## JSON output

```bash
cce savings --json
```

Returns machine-readable data for integration with other tools:

```json
{
  "project": "my-project",
  "queries": 42,
  "served_tokens": 14200,
  "raw_tokens": 26000,
  "full_file_tokens": 48000,
  "tokens_saved": 33800,
  "savings_pct": 70,
  "retrieval_savings_pct": 46,
  "compression_savings_pct": 45
}
```

## Populating savings before a session

If you have zero queries recorded (fresh install), run a test search to seed the stats:

```bash
cce search 'how does the main module work'
```

This updates the savings tracker so `cce status` and the dashboard show non-zero values.
