---
title: Introduction
description: What is Code Context Engine and why use it
---

Code Context Engine (CCE) is a local MCP server that indexes your codebase so AI coding agents search for relevant code instead of reading entire files.

## The problem

Every time an AI agent needs to understand your code, it reads entire files. A 500-line file costs 500 lines of input tokens even when the agent only needs one function. Across a session, this adds up to thousands of wasted tokens and real dollars.

## The solution

CCE parses your code into semantic chunks (functions, classes, modules) using Tree-sitter, stores them with vector embeddings, and serves only the relevant pieces when the agent asks a question.

**Result: 94% input token savings, reproducibly benchmarked.**

## What you get

| Tool | Purpose |
|------|---------|
| `context_search` | Hybrid vector + keyword search with graph expansion |
| `get_chunk` | Retrieve a specific chunk by ID |
| `record_decision` | Store architectural decisions for cross-session recall |
| `record_code_area` | Mark areas you've worked on |
| `session_recall` | Recall decisions and code areas |
| `session_timeline` | Browse tool call history |
| `session_event` | Inspect a specific past event |
| `set_output_level` | Control output compression (off/lite/standard/max) |
| `set_scope` | Limit search to specific directories |

## Supported agents

| Editor | Config written | Instructions |
|--------|---------------|--------------|
| Claude Code | `.mcp.json` | `CLAUDE.md` |
| VS Code / Copilot | `.vscode/mcp.json` | `.github/copilot-instructions.md` |
| Cursor | `.cursor/mcp.json` | `.cursorrules` |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` |
| OpenAI Codex | `~/.codex/config.toml` | `AGENTS.md` |
| OpenCode | `opencode.json` | |
| Tabnine | `.tabnine/agent/settings.json` | `TABNINE.md` |

## How it works

1. **Index** — Tree-sitter parses code into semantic chunks. Stored locally with vector embeddings.
2. **Search** — Agent calls `context_search` via MCP. Hybrid vector + BM25 merged with Reciprocal Rank Fusion. Graph expansion adds related imports.
3. **Compress** — Chunks are compressed (truncation or LLM summary with Ollama). Session-wide output compression rules in instruction files reduce reply tokens (diff-only code, no filler).
4. **Track** — Every query recorded. `cce savings` shows tokens and dollars saved.
