# Code Context Engine

Code Context Engine (CCE) is a local-first MCP server that gives Claude Code a persistent, searchable brain for your codebase. Instead of re-reading files every session, Claude queries an index — fetching only the chunks it actually needs.

## Why CCE Exists

Every Claude Code session starts cold. Claude has no memory of your project. The typical workaround is pasting files manually, which is expensive and repetitive.

| Without CCE | With CCE |
|-------------|----------|
| Paste 3-4 files every session to set context | Claude queries the index on demand |
| Claude re-reads the same code over and over | Each chunk is fetched once |
| Decisions made last week have to be re-explained | Architectural decisions persist across sessions |
| Large repos burn tokens just to orient Claude | Only relevant chunks are retrieved |
| Silent result truncation when context is full | Overflow items listed as expandable references |

**The token cost (benchmarked on FastAPI, 20 queries):**

```
Without CCE:  read full files     = 75,355 tokens/query (avg)
With CCE:     context_search      =  5,381 tokens/query (avg)
Savings:      93% retrieval, 90% additional from compression
```

These numbers are reproducible. Run `python benchmarks/run_benchmark.py --repo https://github.com/fastapi/fastapi.git --source-dir fastapi` to verify.

## Quick Navigation

- [Examples](Examples.md) — real conversations showing what you type and what Claude does
- [CCE In Practice](CCE-In-Practice.md) — same scenarios with token counts and internals
- [How It Works](How-It-Works.md) — deep dive into indexing, retrieval, graph expansion, compression
- [CLI Reference](CLI-Reference.md) — every command with examples and expected output
- [Project Commands](Project-Commands.md) — rules, preferences, and per-project commands for Claude
- [Tech Stack](Tech-Stack.md) — every library: what it does, where it's used, why it was chosen
- [Configuration](Configuration.md) — all config options, global and per-project

## Getting Started in 3 Steps

**Install:**
```bash
uv tool install code-context-engine
```

**Index your project:**
```bash
cd /path/to/your/project
cce init
```

**Restart Claude Code.** Claude now has `context_search` and eight other MCP tools available automatically.

## What Claude Gets

Once CCE is connected, Claude has access to these tools without any setup:

| Tool | What it does |
|------|-------------|
| `context_search` | Semantic search across your indexed codebase |
| `expand_chunk` | Get full source for a compressed or overflow chunk |
| `related_context` | Find related code via graph edges (calls, imports) |
| `session_recall` | Recall past decisions and code area notes |
| `record_decision` | Save a decision for future sessions |
| `record_code_area` | Record which files you worked in and why |
| `index_status` | Check when the index was last updated |
| `reindex` | Trigger re-indexing of a file or the full project |
| `set_output_compression` | Adjust response verbosity (off / lite / standard / max) |
