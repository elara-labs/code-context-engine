---
title: Why CCE?
description: The problem CCE solves, with real numbers, and who benefits most.
---

## The problem

Every time an AI coding agent answers a question about your code, it reads entire files. A 200-line file costs 200 lines of input tokens even when the agent only needs one function. Across a session with 20 queries, this adds up fast.

**Real numbers from a FastAPI project (53 source files, 180K tokens):**

| | Without CCE | With CCE |
|---|---|---|
| Tokens per query (avg) | 83,681 | 523 |
| Cost per query (Opus) | $1.25 | $0.008 |
| Cost for 20 queries | $25.00 | $0.16 |
| Tokens wasted on irrelevant code | ~95% | ~0% |

That's $25 vs $0.16 for the same 20 questions. The agent gets the same answers both ways. The difference is how much irrelevant code it had to read to find them.

## Why this happens

AI coding agents are designed to be thorough. When you ask "how does authentication work?", the agent reads every file that might be relevant. Most of those files contain code that has nothing to do with authentication, but the agent reads them anyway because it can't know in advance which lines matter.

This is the right behavior for correctness. But it's wasteful for cost. You're paying for the agent to read thousands of lines of code it immediately ignores.

## What CCE does differently

CCE sits between your agent and your codebase as an MCP server. Instead of the agent reading files directly, it calls `context_search("authentication")` and gets back only the relevant functions, classes, and modules.

**Three layers of savings:**

1. **Retrieval (94% input savings).** Tree-sitter parses your code into semantic chunks (functions, classes, imports). Vector + keyword search finds the relevant ones. The agent gets 500 tokens of focused code instead of 80,000 tokens of full files.

2. **Compression (up to 89% additional).** Retrieved chunks are compressed to signatures and docstrings (or LLM-summarized via Ollama). If the agent needs the full source, it calls `expand_chunk`.

3. **Output compression (up to 80% output savings).** Session-wide style directives in your instruction files tell the agent to use compressed prose and show only code diffs instead of full file rewrites. Output tokens cost 5x more than input tokens on Opus ($75/1M vs $15/1M), so this has outsized cost impact.

## The memory problem

Without CCE, every agent session starts from zero. The agent doesn't know what you decided yesterday, what architecture choices you made last week, or what code areas you've been working in. You end up re-explaining context every session.

CCE adds cross-session memory:

- **`record_decision`** stores architectural choices ("we chose PostgreSQL over MongoDB because...")
- **`record_code_area`** marks files you've worked on with descriptions
- **`session_recall`** retrieves past decisions at the start of new sessions

The agent stops re-deriving answers it already figured out. Decisions compound instead of being forgotten.

## Who benefits most

**Large codebases.** The more files in your project, the more tokens wasted reading irrelevant code. A 500-file project wastes far more than a 20-file project.

**Opus users.** Opus input tokens cost $15/1M, output $75/1M. A 94% reduction in input and 70% reduction in output saves real money. Sonnet and Haiku users save less in absolute dollars but still benefit from faster responses (fewer tokens = faster inference).

**Multi-agent users.** If you use Claude Code, Cursor, and Codex on the same project, the index is shared. One `cce init --agent all` configures everything. Without CCE, each agent independently reads the same files and wastes the same tokens.

**Teams.** Decisions recorded by one developer are recalled by another. The codebase's institutional knowledge lives in the index, not in individual developers' heads.

## What CCE is NOT

**Not a prompt optimizer.** CCE doesn't rewrite your prompts or modify your agent's system prompt. It provides a search tool and writes output style rules into instruction files.

**Not cloud-based.** Everything runs on your machine. No code, embeddings, or queries leave your system. The only network call is fetching model pricing for cost estimates (cached 7 days).

**Not a replacement for your agent's tools.** When you need to edit a specific file, use your agent's built-in file editor. CCE handles search and context retrieval. Use `context_search` for understanding code, use `Read`/`Edit` for modifying it.

**Not language-limited.** Full AST-aware chunking works for Python, JavaScript, TypeScript, PHP, Go, Rust, and Java. Other file types (YAML, Markdown, config) use line-based chunking and still appear in search results.

## The 60-second test

```bash
uv tool install code-context-engine
cd /path/to/your/project
cce init
```

Ask your agent a question. Then run `cce savings` to see exactly how many tokens and dollars CCE saved. If the numbers don't convince you, run `cce uninstall` to remove everything cleanly.
