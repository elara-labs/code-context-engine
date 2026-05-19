---
title: Configuration
description: Full reference for all CCE configuration options.
---

CCE works with zero configuration out of the box. This page covers all available options for when you need to tune behavior.

## Config file locations

- **Global:** `~/.cce/config.yaml` (created automatically on first use)
- **Per-project:** `.context-engine.yaml` in your project root (overrides global for that project)

## Full config reference

```yaml
compression:
  level: standard        # How much to compress code chunks before sending to the agent
                         # Options: minimal | standard | full
  output: standard       # How much to compress agent responses
                         # Options: off | lite | standard | max
  model: phi3:mini       # Ollama model for LLM-based summarization
                         # Auto-detected if Ollama is running. Ignored if Ollama is off.

indexer:
  watch: true            # Keep index in sync via git hooks
  ignore:                # Directories and patterns to skip during indexing
    - .git
    - node_modules
    - __pycache__
    - .venv
    - dist
    - build

retrieval:
  top_k: 20              # Maximum chunks returned per query
  confidence_threshold: 0.5  # Minimum score to include a result (0.0 to 1.0)

embedding:
  model: BAAI/bge-small-en-v1.5  # Embedding model (fastembed-compatible)

pricing:
  model: opus            # Model for cost estimates in `cce savings`
                         # Options: opus | sonnet | haiku
```

## Compression levels

### Input compression (`compression.level`)

Controls how much CCE compresses code chunks before including them in the agent's context.

| Level | Behavior |
|-------|----------|
| `minimal` | Truncation only. Keeps signature and docstring, drops body. |
| `standard` | Truncation plus light summarization if Ollama is available. |
| `full` | Full LLM summarization via Ollama (requires Ollama running). |

### Output compression (`compression.output`)

Controls how verbose the agent's responses are. During `cce init`, the configured level is written into instruction files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, etc.) so it applies to the **entire session**, not just CCE tool responses.

| Level | Style | Typical savings |
|-------|-------|----------------|
| `off` | Full output | 0% |
| `lite` | No filler/hedging, diff-only code | ~25% |
| `standard` | Fragments, short synonyms, diff-only code | ~70% |
| `max` | Telegraphic, abbreviations, diff-only code | ~80% |

All levels include code output rules: show only changed lines, never rewrite entire files, never echo back unchanged code. Code blocks, paths, commands, and error messages are never compressed. Security warnings use full clarity.

Change the level and re-run `cce init` to update instruction files, or change at runtime:

```
set_output_level output_level=max
```

## Embedding model

```yaml
embedding:
  model: sentence-transformers/all-mpnet-base-v2
```

Any model available in fastembed works. Changing the model requires a full re-index:

```bash
cce clear --yes && cce index --full
```

The default `BAAI/bge-small-en-v1.5` is recommended for most use cases. It balances quality, speed, and size well.

## Retrieval tuning

**`top_k`** controls how many chunks the retriever returns per query. Higher values surface more context but cost more tokens. Default: 20.

**`confidence_threshold`** sets the minimum score to include a result. Range 0.0 to 1.0. Lower values return more results; higher values return only strong matches. Default: 0.5.

At runtime, the agent can pass `top_k` and `max_tokens` directly to `context_search`:

```
context_search(query="payment processing", top_k=5, max_tokens=3000)
```

## Ignoring files

The `indexer.ignore` list supports:

- Directory names: `node_modules`, `dist`
- File patterns: `"*.generated.ts"`, `"*.min.js"`
- Relative paths: `"src/legacy/"`

Files matching `.gitignore` are also skipped automatically.

## Pricing model

```yaml
pricing:
  model: sonnet   # opus (default) | sonnet | haiku
```

This determines which model's pricing is used for cost estimates in `cce savings`. Prices are fetched from Anthropic's docs and cached for 7 days.

## Ollama URL

If Ollama is running on a non-default address, set it via environment variable:

```bash
export OLLAMA_HOST=http://localhost:11434
```

## Resource profiles

CCE auto-detects available RAM and adjusts behavior:

| RAM | Profile | Behavior |
|-----|---------|----------|
| Less than 12 GB | `light` | Truncation only, small embedding batches |
| 12 to 32 GB | `standard` | Full pipeline, standard batch sizes |
| More than 32 GB | `full` | Larger Ollama models, larger batches |

You do not need to set this manually.

## Security

- All data stays local. No code is sent to external services (unless you use a cloud embedding model).
- Index data is stored in `~/.cce/projects/`.
- The MCP server only listens on stdio (not network) when launched by an agent.
