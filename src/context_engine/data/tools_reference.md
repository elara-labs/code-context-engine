# MCP Tools Reference

Detailed parameter documentation for Code Context Engine's MCP tools.
Loaded on demand by the agent when more detail is needed.

## context_search

Search the codebase using hybrid vector + BM25 retrieval.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | | Natural language query |
| top_k | integer | no | 10 | Maximum results to return |
| max_tokens | integer | no | 8000 | Token budget for results |

Returns ranked code chunks with confidence scores. When
`structural.provider: codegraph` is enabled, the same response may also include
structural sources, relationships, and impact items with exact file/line
provenance. If CodeGraph is unavailable, the tool returns semantic results
only. Use this instead of Read, Grep, or Glob when exploring code.

## expand_chunk

Get the full original content for a compressed chunk.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chunk_id | string | yes | ID from a context_search result |

## related_context

Find related code via graph edges (calls, imports).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chunk_id | string | yes | ID from a context_search result |

## session_recall

Recall past decisions and turn summaries via topic search.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| topic | string | yes | Topic phrase (not a single word) |

Pass a descriptive phrase, not a single word. e.g. `session_recall("auth flow")`
not `session_recall("auth")`.

## session_timeline

List turn summaries for a session, oldest first. Use to drill into a
session_id returned by session_recall.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| session_id | string | yes | | Session ID from recall results |
| limit | integer | no | 20 | Max turns to return |

## session_event

Return raw input/output payload for a single tool event. Use to drill
into an event_id from session_timeline.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| event_id | integer | yes | Event ID from timeline results |

## record_decision

Record a decision with reasoning for future session_recall.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| decision | string | yes | What was decided |
| reason | string | yes | Why this choice was made |

## record_code_area

Record a code area worked on for future session_recall.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file_path | string | yes | Path to the file |
| description | string | yes | What was done |

## index_status

Check when the index was last updated. No parameters.

## reindex

Trigger re-indexing of a file or the full project.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| path | string | no | File path to re-index (omit for full project) |

## set_output_compression

Set output compression level to reduce response token cost.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| level | string | yes | `off`, `lite`, `standard`, or `max` |

Levels: off = normal output, lite = no filler (~30% savings),
standard = fragments (~65% savings), max = telegraphic (~75% savings).
Code blocks and commands are never compressed.
