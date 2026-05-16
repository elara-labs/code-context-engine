---
title: How It Works
description: Architecture overview of CCE's indexing, retrieval, and compression pipelines.
---

CCE sits between your AI coding agent and your codebase. It replaces full-file reads with compressed, relevant chunks, reducing token usage while preserving answer quality.

## Indexing pipeline

When you run `cce init` or `cce index`, the following steps execute:

1. **Tree-sitter parsing.** Each source file is parsed into an AST using language-specific Tree-sitter grammars. This identifies functions, classes, methods, and other structural units.

2. **Chunking.** The AST is split into semantic chunks (one per function, class, or logical block). Each chunk retains its file path, line range, and relationships to other chunks.

3. **Embedding.** Each chunk is embedded using a local model (default: `BAAI/bge-small-en-v1.5` via fastembed). No data leaves your machine.

4. **Storage.** Embeddings, full-text content, and graph edges are written to a local SQLite database with sqlite-vec for vector search and FTS5 for keyword search.

## Search pipeline

When an agent calls `context_search`, the following steps execute:

1. **Query embedding.** The natural language query is embedded using the same model.

2. **Hybrid retrieval.** Two searches run in parallel:
   - Vector similarity search (semantic match via sqlite-vec).
   - Full-text keyword search (BM25 via FTS5).

3. **RRF merge.** Results from both searches are combined using Reciprocal Rank Fusion, which produces a single ranked list without needing score normalization.

4. **Graph expansion.** Top results are expanded by following code relationships (calls, imports, inheritance) to pull in related chunks the query might not have matched directly.

5. **Compression.** The final chunk set is compressed before being returned to the agent.

## Storage

All index data lives in `~/.cce/projects/<project-name>/`:

- **Vector index:** sqlite-vec extension for approximate nearest neighbor search.
- **Full-text index:** FTS5 for keyword/BM25 retrieval.
- **Graph:** Edges representing code relationships (function calls, imports, class inheritance).
- **Metadata:** File hashes for incremental indexing, chunk boundaries, and statistics.

Everything is SQLite. No external database required.

## Compression layers

CCE applies multiple compression stages to minimize tokens while preserving usefulness:

| Layer | What it does |
|-------|-------------|
| **Retrieval** | Only relevant chunks are returned (not the whole codebase). |
| **Chunk compression** | Function bodies are truncated to signature + docstring, or summarized via Ollama. |
| **Output compression** | Agent responses are made more concise (configurable level). |
| **Grammar compression** | Removes syntactic noise (extra whitespace, redundant type annotations) from returned code. |
| **Turn summarization** | Long conversation histories are summarized to reduce context window usage. |
| **Progressive disclosure** | Returns signatures first; the agent can request full bodies only when needed. |

## Supported languages

Tree-sitter grammars are included for:

- Python
- JavaScript
- TypeScript
- PHP
- Go
- Rust
- Java

Other file types are indexed using line-based chunking without AST awareness.
