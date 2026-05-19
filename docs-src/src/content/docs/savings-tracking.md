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

The report separates input and output token savings because they have different pricing. Output tokens cost 5x more than input (e.g. Opus: $75/1M output vs $15/1M input).

**Input savings** come from:

- **Retrieval.** Only relevant chunks returned instead of full files (biggest contributor, often 94%).
- **Chunk compression.** Chunks truncated to signatures/docstrings or summarized via Ollama.
- **Grammar compression.** Articles and filler removed from context.
- **Turn summarization.** Session history compressed.
- **Progressive disclosure.** Tool payloads filtered.

**Output savings** come from:

- **Output compression.** Session-wide style directives written into instruction files (`CLAUDE.md`, `AGENTS.md`, etc.) during `cce init`. These tell the agent to use compressed prose and diff-only code changes across the entire session. Configure the level in `cce.yaml` (`compression.output`: off/lite/standard/max).

## Per-bucket breakdown

The breakdown shows each savings layer with its contribution:

```
  Breakdown:
    retrieval              48%  ▰▰▰▰▰▰▰▰▰▰    6.0k    $0.09 · 1 call
    chunk compression      20%  ▰▰▰▰▱▱▱▱▱▱    2.6k    $0.04 · 1 call
    output compression*     2%  ▰▱▱▱▱▱▱▱▱▱     325    $0.02 · 1 call
```

Each row uses the correct pricing (input rate for input buckets, output rate for the output compression bucket). Buckets marked with `*` use estimated values.

## Configuring the pricing model

Cost estimates use model-specific pricing for both input and output tokens. Configure which model to estimate for:

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
