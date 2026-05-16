---
title: FAQ
description: Frequently asked questions about Code Context Engine.
---

## Does CCE affect answer quality?

No. CCE returns the same code your agent would find by reading files, just compressed and targeted. In practice, answers are often better because the agent receives focused, relevant context instead of entire files full of unrelated code.

## How can I increase output savings?

Set output compression to a higher level:

```yaml
compression:
  output: max
```

Or tell your agent at runtime: "Switch to max output compression." The `max` level uses telegraphic phrasing and typically saves ~75% on response tokens. Code blocks and file paths are never affected.

## Where do the savings come from?

Three main sources:

1. **Retrieval.** Only relevant chunks are returned instead of the full codebase. This is the largest contributor (often 80%+ reduction).
2. **Chunk compression.** Retrieved chunks are truncated to signatures and docstrings, or summarized via Ollama if available.
3. **Output compression.** Agent responses are shortened by removing filler, hedging, and verbose phrasing.

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
