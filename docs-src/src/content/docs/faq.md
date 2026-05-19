---
title: FAQ
description: Frequently asked questions about Code Context Engine.
---

## Does CCE affect answer quality?

No. CCE returns the same code your agent would find by reading files, just compressed and targeted. In practice, answers are often better because the agent receives focused, relevant context instead of entire files full of unrelated code.

## How does output token savings work?

CCE writes output compression rules directly into your agent's instruction files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, etc.) during `cce init`. These apply to the **entire session**, so every response follows them.

Set the level in `cce.yaml`, then re-run `cce init`:

```yaml
compression:
  output: max       # off | lite | standard | max
```

Or change at runtime via the MCP tool:

```
set_output_level output_level=max
```

| Level | Savings | Style |
|-------|---------|-------|
| `off` | 0% | Normal verbosity |
| `lite` | ~25% | No filler/hedging, diff-only code |
| `standard` | ~70% | Fragments, short synonyms, diff-only code |
| `max` | ~80% | Telegraphic, abbreviations, diff-only code |

Default is `standard`. All levels include code output rules that tell the model to show only changed lines instead of full file rewrites. Code blocks, paths, and commands are never compressed. Security warnings use full clarity.

## Where do the savings come from?

**Input tokens** (what goes into the model):

1. **Retrieval.** Only relevant chunks are returned instead of the full codebase. This is the largest contributor (often 94% reduction).
2. **Chunk compression.** Retrieved chunks are truncated to signatures and docstrings, or summarized via Ollama if available.
3. **Grammar compression.** Articles and filler removed from context.
4. **Turn summarization.** Session history compressed.
5. **Progressive disclosure.** Tool payloads filtered.

**Output tokens** (what comes back from the model):

6. **Output compression.** Session-wide style directives in instruction files reduce prose verbosity and enforce diff-only code changes. Output tokens cost 5x more than input (e.g. Opus: $75/1M vs $15/1M), so even moderate output savings have outsized cost impact.

## Is my code sent anywhere?

No. All processing happens locally:

- Embedding uses a local model downloaded to your machine.
- Vector search runs in a local SQLite database.
- The MCP server communicates over stdio (not network).
- Ollama summarization (if enabled) also runs locally.

No code, embeddings, or queries leave your machine unless you explicitly configure a remote embedding model.

## Does it work offline?

Yes, fully. After the initial setup (which downloads the embedding model, ~60 MB), CCE operates entirely offline. Ollama summarization also runs locally if you have it installed.

The only network call CCE makes is fetching model pricing for cost estimates in `cce savings`, and that result is cached for 7 days.

## What languages are supported?

CCE uses Tree-sitter for structural parsing. The following languages have full AST-aware chunking:

- Python
- JavaScript
- TypeScript
- PHP
- Go
- Rust
- Java

Other file types (YAML, Markdown, config files, etc.) are indexed using line-based chunking. They still appear in search results but without function-level granularity.

## Why does `cce init` fail with "No embedding backend available"?

CCE needs an embedding backend to convert code into searchable vectors. You have two options:

1. **Install with `[local]` extra** (recommended): `uv tool install "code-context-engine[local]"`. This includes fastembed, which works offline with no external services.
2. **Use Ollama**: Start Ollama and run `ollama pull nomic-embed-text`. Then install CCE without `[local]`: `uv tool install code-context-engine`.

If you installed without `[local]` and don't have Ollama running, re-install with the extra:

```bash
uv tool install --force "code-context-engine[local]"
```

## Can I use CCE with multiple agents at once?

Yes. Run `cce init --agent all` to configure every supported agent. They all share the same index and MCP server, so there is no duplication or conflict.

## How do I update CCE?

```bash
cce upgrade
```

This detects your install method (uv, pipx, or pip), upgrades the package, and refreshes your project config (hooks, MCP config, instruction files).

## How do I remove CCE from a project?

```bash
cce uninstall
```

This removes git hooks, MCP config entries, instruction file blocks, and the local `.cce/` directory. Index data in `~/.cce` is preserved. Run `cce clear` afterwards to remove that too.

## The savings show 0 queries. What's wrong?

Savings are recorded when your agent calls `context_search` through the MCP server. If you have not used an agent session yet, run a test search to seed the stats:

```bash
cce search 'main entry point'
```
