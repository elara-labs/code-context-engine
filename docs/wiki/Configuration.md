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
  confidence_threshold: 0.2  # Minimum confidence score to include a result (0.0–1.0)
  marginal_ratio: 0.75    # Stop adding results once score drops below this fraction of the
                           # top score. 0 disables (always fill to top_k). Default 0.75.

embedding:
  model: BAAI/bge-small-en-v1.5  # Embedding model (fastembed-compatible)

pricing:
  model: opus              # Which model to use for cost estimates in `cce savings`
                           # Anthropic: opus | sonnet | haiku
                           # OpenAI: gpt-4o | gpt-4o-mini | gpt-4.1 | gpt-4.1-mini |
                           #         gpt-4.1-nano | o3 | o3-mini | o4-mini | codex-mini
                           # Google: gemini-2.5-pro | gemini-2.5-flash | gemini-2.0-flash
                           # Anthropic prices are fetched live and cached 7 days.
                           # Other providers use static pricing updated with each release.
  # input: 15.0            # override $/1M input tokens (any model)
  # output: 75.0           # override $/1M output tokens (any model)

serve:
  idle_timeout_minutes: 30  # Auto-shutdown `cce serve` after N minutes of inactivity (0 = disabled)
  max_ort_threads: 2        # Max ONNX Runtime threads per `cce serve` process (0 = ORT default)
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
| `minimal` | Truncation only. Keeps signature + docstring, drops body |
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

You do not need to set this manually. It is detected at startup.

---

## Retrieval Tuning

**`top_k`**: how many chunks the retriever returns per query. Higher values surface more context but cost more tokens. Default: 20.

**`confidence_threshold`**: minimum score to include a result. Range 0.0 to 1.0. Lower values return more results; higher values return only strong matches. Default: 0.2.

**`marginal_ratio`**: once results are ranked, any chunk whose score is below this fraction of the top score is dropped. This prunes low-value tail results and reduces tokens served. Range 0.0 to 1.0; 0 disables the cutoff. Default: 0.75.

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

**Note:** The default `BAAI/bge-small-en-v1.5` is recommended for most use cases. It balances quality, speed, and size well. Larger models improve retrieval quality but are slower to embed.

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

---

## Resource Governor (`serve.*`)

When multiple `cce serve` processes run simultaneously (one per project per AI session), they can exhaust system resources. Two config keys control this:

**`serve.idle_timeout_minutes`**: auto-shutdown the MCP server after N minutes of inactivity. Prevents zombie processes from accumulating. Set to 0 to disable. Default: 30. Can also be set via the `CCE_IDLE_TIMEOUT_MINUTES` environment variable.

**`serve.max_ort_threads`**: cap the number of ONNX Runtime threads per `cce serve` process. With many processes, uncapped threads (default = CPU count) create thousands of OS threads competing for cores. Set to 0 to use the ORT default. Default: 2. Can also be set via the `CCE_ORT_THREADS` environment variable.

```yaml
serve:
  idle_timeout_minutes: 30
  max_ort_threads: 2
```
