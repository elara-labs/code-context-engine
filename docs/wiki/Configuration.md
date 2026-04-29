# Configuration

CCE works with zero configuration out of the box. This page covers all available options for when you need to tune it.

---

## Global Configuration

File: `~/.cce/config.yaml`

This file is created automatically on first use. Override any value you want to change.

```yaml
compression:
  level: standard        # How much to compress code chunks before sending to Claude
                         # Options: minimal | standard | full
  output: standard       # How much to compress Claude's own responses
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
  top_k: 20              # Maximum number of chunks to return per query
  confidence_threshold: 0.5  # Minimum confidence score to include a result (0.0–1.0)

embedding:
  model: BAAI/bge-small-en-v1.5  # Embedding model (fastembed-compatible)

pricing:
  model: opus              # Which Claude model to use for cost estimates in `cce savings`
                           # Options: opus | sonnet | haiku
                           # Prices are fetched from Anthropic's docs and cached for 7 days
```

---

## Per-Project Configuration

File: `.context-engine.yaml` in your project root.

Per-project settings override the global config for that project only. You typically only need this if a project has unusual structure or size.

```yaml
compression:
  level: full            # Use minimal compression for this project

indexer:
  ignore:
    - .git
    - node_modules
    - dist
    - coverage
    - "*.generated.ts"   # Glob patterns work too
```

---

## Compression Levels Explained

### Input compression (`compression.level`)

Controls how much CCE compresses code chunks before including them in Claude's context.

| Level | Behavior |
|-------|----------|
| `minimal` | Truncation only — keeps signature + docstring, drops body |
| `standard` | Truncation + light summarization if Ollama is available |
| `full` | Full LLM summarization via Ollama (requires Ollama running) |

### Output compression (`compression.output`)

Controls how verbose Claude's own responses are. Set via `set_output_compression` MCP tool or via config.

| Level | Style | Typical token savings |
|-------|-------|----------------------|
| `off` | Full Claude output | 0% |
| `lite` | Removes filler and hedging | ~30% |
| `standard` | Shorter phrasing, fragments where possible | ~65% |
| `max` | Telegraphic, minimal prose | ~75% |

Code blocks, file paths, commands, and error messages are never compressed regardless of level.

Change at runtime by telling Claude:
```
Switch to max output compression
Turn off output compression
```

---

## Resource Profiles

CCE auto-detects available RAM and adjusts its behavior:

| RAM | Profile | Behavior |
|-----|---------|----------|
| Less than 12 GB | `light` | Truncation only, small embedding batches |
| 12 to 32 GB | `standard` | Full pipeline, standard batch sizes |
| More than 32 GB | `full` | Larger Ollama models, larger batches |

You do not need to set this manually — it is detected at startup.

---

## Retrieval Tuning

**`top_k`** — how many chunks the retriever returns per query. Higher values surface more context but cost more tokens. Default: 20.

**`confidence_threshold`** — minimum score to include a result. Range 0.0 to 1.0. Lower values return more results; higher values return only strong matches. Default: 0.5.

At runtime, Claude can pass `top_k` and `max_tokens` directly to `context_search`:
```
context_search(query="payment processing", top_k=5, max_tokens=3000)
```

---

## Ignoring Files

The `indexer.ignore` list supports:

- Directory names: `node_modules`, `dist`
- File patterns: `"*.generated.ts"`, `"*.min.js"`
- Relative paths: `"src/legacy/"`

Files matching `.gitignore` are also skipped automatically.

---

## Changing the Embedding Model

```yaml
embedding:
  model: sentence-transformers/all-mpnet-base-v2
```

Any model available in fastembed works. Changing the model requires a full re-index:

```bash
cce clear --yes && cce index --full
```

**Note:** The default `BAAI/bge-small-en-v1.5` is recommended for most use cases — it balances quality, speed, and size well. Larger models improve retrieval quality but are slower to embed.

---

## Service Port Configuration

The dashboard defaults to a random free port when started with `cce dashboard`, or port 8080 when started with `cce services start dashboard`.

```bash
# Custom port via CLI
cce services start dashboard --port 9090

# Or via cce dashboard directly
cce dashboard --port 9090
```

PID and port files are stored in `~/.cce/pids/`.
