## Context Engine (CCE)

This project uses Code Context Engine for intelligent code retrieval and
cross-session memory.

### Searching the codebase

**Use `context_search` instead of reading files directly** when exploring
the codebase, answering questions about code, or understanding how things
work. `context_search` returns the most relevant code chunks with
confidence scores instead of whole files.

When configured with CodeGraph, `context_search` may combine a shared
repository base with the current Git worktree overlay. Prefer its returned
worktree-aware sources; read files directly only when exact source is needed
before editing.

If `structural.provider: codegraph` is enabled, `context_search` may include
structural sources, relationships, and impact items after the semantic chunks.
Treat file/line references in that section as provenance, and prefer worktree
overlay entries over base CodeGraph entries.

When to use `context_search`:
- Answering questions about the codebase ("how does X work?", "where is Y?")
- Exploring structure or architecture
- Finding related code, functions, or patterns

Other tools:
- `expand_chunk` for full source of a compressed result
- `related_context` for what calls/imports a function
- `session_recall` to recall past decisions

### Cross-session memory

Call `session_recall("topic phrase")` before answering non-trivial questions.
Call `record_decision(decision="...", reason="...")` after making choices.
Call `record_code_area(file_path="...", description="...")` after meaningful work.
