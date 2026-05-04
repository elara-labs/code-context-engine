# Token Efficiency Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate three external token-saving concepts into CCE: (1) terseness rules in CLAUDE.md, (2) overflow result references so Claude sees what it is missing, (3) graph-aware 1-hop retrieval expansion using CALLS/IMPORTS edges.

**Architecture:** Feature 1 is a string constant edit. Feature 2 restructures the `_handle_context_search` result formatter in `mcp_server.py` to split inline vs overflow chunks. Feature 3 adds `get_related_file_paths` to `LocalBackend` and calls it from `HybridRetriever.retrieve()` after the primary ranking step.

**Tech Stack:** Python stdlib, LanceDB (vector search with file_path filter), SQLite (graph store), existing `GraphStore.get_nodes_by_file` + `get_neighbors`

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/context_engine/cli.py` | Modify | Add terseness block to `_CCE_CLAUDE_MD_BLOCK` constant |
| `src/context_engine/integration/mcp_server.py` | Modify | Split inline/overflow in `_handle_context_search` |
| `src/context_engine/storage/local_backend.py` | Modify | Add `get_related_file_paths` method |
| `src/context_engine/retrieval/retriever.py` | Modify | Call `get_related_file_paths` after ranking, append bonus chunks |
| `tests/test_token_efficiency.py` | Create | Tests for overflow formatting and graph expansion |

---

### Task 1: CLAUDE.md terseness rules

**Files:**
- Modify: `src/context_engine/cli.py`

No tests needed — this is a string constant change.

- [ ] **Step 1: Read the current `_CCE_CLAUDE_MD_BLOCK` constant**

```bash
grep -n "_CCE_CLAUDE_MD_BLOCK" /Users/raj/projects/Claude-Context-Engine/src/context_engine/cli.py | head -5
```

Find the line range of the constant. It starts with `_CCE_CLAUDE_MD_BLOCK = """` and ends with `"""`.

- [ ] **Step 2: Append the terseness block to the constant**

The constant currently ends just before the closing `"""`. Add the following section inside the string, right before the closing `"""`:

```
## Output Style

Be concise. Lead with the answer or action, not reasoning. Skip filler words,
preamble, and phrases like "I'll help you with that" or "Certainly!". Prefer
fragments over full sentences in explanations. No trailing summaries of what
you just did. One sentence if it fits.

Code blocks, file paths, commands, and error messages are always written in full.
```

The updated end of `_CCE_CLAUDE_MD_BLOCK` should look like this:

```python
_CCE_CLAUDE_MD_BLOCK = """\
## Context Engine (CCE)

This project uses Claude Context Engine for intelligent code retrieval.

**IMPORTANT: You MUST use `context_search` instead of reading files directly**
when exploring the codebase, answering questions about code, or understanding
how things work. This is a hard requirement, not a suggestion. The `context_search`
MCP tool routes queries through the semantic search engine, which:
- Returns only the most relevant code chunks (not entire files)
- Tracks token savings automatically
- Provides confidence scores for each result

**When to use `context_search`:**
- Answering questions about the codebase ("how does X work?", "where is Y?")
- Exploring code structure or architecture
- Finding related code, functions, or patterns
- Any time you would otherwise read a file to understand it

**When to use `Read` instead:**
- You need to edit a specific file (read before editing)
- You need the exact, complete content of a known file path

Other useful MCP tools:
- `expand_chunk` — get full source for a compressed result
- `related_context` — find what calls/imports a function
- `session_recall` — retrieve decisions from past sessions
- `record_decision` — persist an important decision for future sessions

## Output Style

Be concise. Lead with the answer or action, not reasoning. Skip filler words,
preamble, and phrases like "I'll help you with that" or "Certainly!". Prefer
fragments over full sentences in explanations. No trailing summaries of what
you just did. One sentence if it fits.

Code blocks, file paths, commands, and error messages are always written in full.
"""
```

- [ ] **Step 3: Verify the constant looks correct**

```bash
cd /Users/raj/projects/Claude-Context-Engine
python -c "from context_engine.cli import _CCE_CLAUDE_MD_BLOCK; print(_CCE_CLAUDE_MD_BLOCK[-200:])"
```

Expected: the last ~200 chars show the Output Style section.

- [ ] **Step 4: Commit**

```bash
git add src/context_engine/cli.py
git commit -m "feat: add output terseness rules to generated CLAUDE.md"
```

---

### Task 2: Overflow result references in context_search

**Files:**
- Create: `tests/test_token_efficiency.py` (overflow tests only for now)
- Modify: `src/context_engine/integration/mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_token_efficiency.py`:

```python
"""Tests for token efficiency features: overflow references and graph expansion."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from context_engine.models import Chunk, ChunkType


def _make_chunk(chunk_id: str, file_path: str, content: str, confidence: float = 0.8) -> Chunk:
    c = Chunk(
        id=chunk_id,
        content=content,
        chunk_type=ChunkType.FUNCTION,
        file_path=file_path,
        start_line=1,
        end_line=10,
        language="python",
    )
    c.confidence_score = confidence
    return c


# ── Overflow references ───────────────────────────────────────────────────────

def test_overflow_format_contains_expand_hints():
    """When results exceed token budget, overflow chunk IDs appear in output."""
    from context_engine.integration.mcp_server import _format_results_with_overflow

    inline_chunk = _make_chunk("id-1", "auth.py", "x" * 100, confidence=0.9)
    overflow_chunk = _make_chunk("id-2", "payments.py", "y" * 500, confidence=0.75)

    body = _format_results_with_overflow([inline_chunk], [overflow_chunk])

    assert "id-2" in body
    assert "payments.py" in body
    assert "expand_chunk" in body


def test_overflow_format_no_overflow():
    """When all results fit inline, no overflow section is added."""
    from context_engine.integration.mcp_server import _format_results_with_overflow

    chunk = _make_chunk("id-1", "auth.py", "x" * 100, confidence=0.9)

    body = _format_results_with_overflow([chunk], [])

    assert "expand_chunk" not in body
    assert "more result" not in body


def test_overflow_split_respects_token_budget():
    """Chunks exceeding max_tokens go to overflow, not inline."""
    from context_engine.integration.mcp_server import _split_inline_overflow

    # Each char ~0.3 tokens, so 3300 chars ≈ 1000 tokens
    big = _make_chunk("big", "big.py", "x" * 3300)
    small = _make_chunk("small", "small.py", "y" * 33)  # ~10 tokens

    inline, overflow = _split_inline_overflow([big, small], max_tokens=50)

    assert small in inline
    assert big in overflow
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/raj/projects/Claude-Context-Engine
uv run pytest tests/test_token_efficiency.py -v 2>&1 | head -20
```

Expected: `ImportError` — `_format_results_with_overflow` and `_split_inline_overflow` don't exist yet.

- [ ] **Step 3: Add the two helper functions to mcp_server.py**

Read `src/context_engine/integration/mcp_server.py` to find where `_handle_context_search` lives and where module-level helpers are defined.

Add these two functions as module-level helpers (near other private helpers in the file, before the `ContextEngineMCP` class or in a logical grouping):

```python
def _split_inline_overflow(
    chunks: list, max_tokens: int
) -> tuple[list, list]:
    """Split chunks into inline (fits budget) and overflow (references only)."""
    inline: list = []
    overflow: list = []
    budget = max_tokens
    for chunk in chunks:
        served_text = chunk.compressed_content or chunk.content
        chunk_tokens = _count_tokens(served_text)
        if chunk_tokens <= budget:
            inline.append(chunk)
            budget -= chunk_tokens
        else:
            overflow.append(chunk)
    return inline, overflow


def _format_results_with_overflow(inline_chunks: list, overflow_chunks: list) -> str:
    """Format inline results and append compact overflow references."""
    parts = []
    for chunk in inline_chunks:
        served_text = chunk.compressed_content or chunk.content
        parts.append(
            f"[{chunk.file_path}:{chunk.start_line}] "
            f"(confidence: {chunk.confidence_score:.2f})\n{served_text}"
        )

    if overflow_chunks:
        lines = [
            f"\n---\n{len(overflow_chunks)} more result(s) available "
            f"(not shown to save tokens):"
        ]
        for chunk in overflow_chunks:
            lines.append(
                f'  expand_chunk(chunk_id="{chunk.id}")  '
                f"→ {chunk.file_path}:{chunk.start_line} "
                f"(confidence: {chunk.confidence_score:.2f})"
            )
        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts) if parts else "No results found."
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_token_efficiency.py::test_overflow_format_contains_expand_hints tests/test_token_efficiency.py::test_overflow_format_no_overflow tests/test_token_efficiency.py::test_overflow_split_respects_token_budget -v
```

Expected: all 3 pass.

- [ ] **Step 5: Update `_handle_context_search` to use the new helpers**

In `mcp_server.py`, find `_handle_context_search`. Replace the section that currently does `retrieve → compress → for chunk in chunks: results.append(...)` with:

```python
        # Fetch 2x candidates so overflow can offer references
        all_chunks = await self._retriever.retrieve(
            query,
            top_k=top_k * 2,
            confidence_threshold=self._config.retrieval_confidence_threshold,
            max_tokens=None,
        )
        all_chunks = await self._compressor.compress(all_chunks, self._config.compression_level)

        inline_chunks, overflow_chunks = _split_inline_overflow(all_chunks, max_tokens)

        # Accounting
        raw_tokens = 0
        served_tokens = 0
        seen_files: set[str] = set()
        for chunk in inline_chunks:
            served_text = chunk.compressed_content or chunk.content
            raw_tokens += _count_tokens(chunk.content)
            served_tokens += _count_tokens(served_text)
            seen_files.add(chunk.file_path)
        for chunk in overflow_chunks:
            raw_tokens += _count_tokens(chunk.content)
            served_tokens += 30  # compact reference ~30 tokens
            seen_files.add(chunk.file_path)

        full_file_tokens = self._estimate_full_file_tokens(seen_files)

        body = _format_results_with_overflow(inline_chunks, overflow_chunks)
        if not inline_chunks and not overflow_chunks:
            body = "No results found."
        if get_output_rules(self._output_level):
            body += (
                f"\n\n---\n[Respond using {self._output_level} output compression]"
            )
        self._record(raw_tokens, served_tokens, full_file_tokens)
        return [TextContent(type="text", text=body)]
```

- [ ] **Step 6: Smoke-test context_search still works**

```bash
cd /Users/raj/projects/Claude-Context-Engine
uv run python -c "
import asyncio
from context_engine.integration.mcp_server import ContextEngineMCP
print('mcp_server imports OK')
"
```

Expected: prints `mcp_server imports OK` with no errors.

- [ ] **Step 7: Run all token efficiency tests**

```bash
uv run pytest tests/test_token_efficiency.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/integration/mcp_server.py tests/test_token_efficiency.py
git commit -m "feat: add overflow result references to context_search"
```

---

### Task 3: Graph-aware retrieval (1-hop expansion)

**Files:**
- Modify: `src/context_engine/storage/local_backend.py`
- Modify: `src/context_engine/retrieval/retriever.py`
- Modify: `tests/test_token_efficiency.py` (add graph tests)

- [ ] **Step 1: Add graph expansion tests**

Append to `tests/test_token_efficiency.py`:

```python
# ── Graph-aware retrieval ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_related_file_paths_returns_neighbors(tmp_path):
    """get_related_file_paths returns file paths of CALLS/IMPORTS neighbors."""
    from context_engine.storage.local_backend import LocalBackend
    from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType

    backend = LocalBackend(base_path=str(tmp_path))

    # Ingest two nodes with a CALLS edge
    node_a = GraphNode(id="fn-a", node_type=NodeType.FUNCTION, name="fn_a",
                       file_path="auth.py", properties={})
    node_b = GraphNode(id="fn-b", node_type=NodeType.FUNCTION, name="fn_b",
                       file_path="utils.py", properties={})
    edge = GraphEdge(source_id="fn-a", target_id="fn-b",
                     edge_type=EdgeType.CALLS, properties={})
    await backend._graph_store.ingest([node_a, node_b], [edge])

    related = await backend.get_related_file_paths(["auth.py"])

    assert "utils.py" in related
    assert "auth.py" not in related  # source file excluded


@pytest.mark.asyncio
async def test_get_related_file_paths_empty_when_no_graph(tmp_path):
    """Returns empty list when no graph edges exist."""
    from context_engine.storage.local_backend import LocalBackend

    backend = LocalBackend(base_path=str(tmp_path))

    related = await backend.get_related_file_paths(["nofile.py"])

    assert related == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/raj/projects/Claude-Context-Engine
uv run pytest tests/test_token_efficiency.py::test_get_related_file_paths_returns_neighbors tests/test_token_efficiency.py::test_get_related_file_paths_empty_when_no_graph -v 2>&1 | head -20
```

Expected: `AttributeError` — `get_related_file_paths` does not exist on `LocalBackend`.

- [ ] **Step 3: Add `get_related_file_paths` to LocalBackend**

Add the following method to `LocalBackend` in `src/context_engine/storage/local_backend.py`, after `graph_neighbors`:

```python
    async def get_related_file_paths(self, file_paths: list[str]) -> list[str]:
        """Return file paths reachable via CALLS or IMPORTS edges from the given files.

        Used by the retriever for 1-hop graph expansion: if your result is in
        auth.py, also surface chunks from files that auth.py calls or imports.
        """
        from context_engine.models import EdgeType, NodeType

        input_set = set(file_paths)
        related: set[str] = set()

        for fp in file_paths:
            nodes = await self._graph_store.get_nodes_by_file(fp)
            for node in nodes:
                if node.node_type not in (NodeType.FUNCTION, NodeType.CLASS,
                                          NodeType.FILE, NodeType.MODULE):
                    continue
                for edge_type in (EdgeType.CALLS, EdgeType.IMPORTS):
                    neighbors = await self._graph_store.get_neighbors(
                        node.id, edge_type
                    )
                    for neighbor in neighbors:
                        if neighbor.file_path and neighbor.file_path not in input_set:
                            related.add(neighbor.file_path)

        return list(related)
```

- [ ] **Step 4: Run the graph tests**

```bash
uv run pytest tests/test_token_efficiency.py::test_get_related_file_paths_returns_neighbors tests/test_token_efficiency.py::test_get_related_file_paths_empty_when_no_graph -v
```

Expected: both pass.

- [ ] **Step 5: Add graph expansion to `HybridRetriever.retrieve()`**

Read `src/context_engine/retrieval/retriever.py`. Find the line where `ranked` is built:

```python
        ranked = [chunk for chunk, _ in scored[:top_k]]
```

Replace it with:

```python
        ranked = [chunk for chunk, _ in scored[:top_k]]

        # Graph expansion: fetch 1-2 bonus chunks from files reachable via
        # CALLS/IMPORTS edges from the top results.
        if ranked and hasattr(self._backend, "get_related_file_paths"):
            try:
                top_files = list({c.file_path for c in ranked[:3]})
                related_files = await self._backend.get_related_file_paths(top_files)
                for rel_fp in related_files[:2]:  # max 2 bonus files
                    bonus = await self._backend.vector_search(
                        query_embedding=_to_list(query_embedding),
                        top_k=2,
                        filters={"file_path": rel_fp},
                    )
                    for b in bonus:
                        dedup_key = (
                            f"{b.file_path}:{b.start_line}-{b.end_line}"
                        )
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            # Slight confidence penalty for graph-hop results
                            b.confidence_score = (
                                b.metadata.get("_distance", 0.5) * 0.85
                            )
                            if b.confidence_score >= confidence_threshold:
                                ranked.append(b)
            except Exception as exc:
                log.debug("Graph expansion skipped: %s", exc)
```

Also add this import at the top of retriever.py if not already present (check first):

```python
from context_engine.storage.vector_store import _to_list
```

Wait — `_to_list` is a private helper in `vector_store.py`. Instead, convert the embedding directly:

Replace `_to_list(query_embedding)` with:

```python
list(query_embedding) if not isinstance(query_embedding, list) else query_embedding
```

So the graph expansion block becomes:

```python
        # Graph expansion: fetch 1-2 bonus chunks from files reachable via
        # CALLS/IMPORTS edges from the top results.
        if ranked and hasattr(self._backend, "get_related_file_paths"):
            try:
                top_files = list({c.file_path for c in ranked[:3]})
                related_files = await self._backend.get_related_file_paths(top_files)
                qe_list = list(query_embedding) if not isinstance(query_embedding, list) else query_embedding
                for rel_fp in related_files[:2]:  # max 2 bonus files
                    bonus = await self._backend.vector_search(
                        query_embedding=qe_list,
                        top_k=2,
                        filters={"file_path": rel_fp},
                    )
                    for b in bonus:
                        dedup_key = (
                            f"{b.file_path}:{b.start_line}-{b.end_line}"
                        )
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            dist = b.metadata.get("_distance", 1.0)
                            b.confidence_score = max(0.0, 1.0 - dist) * 0.85
                            if b.confidence_score >= confidence_threshold:
                                ranked.append(b)
            except Exception as exc:
                log.debug("Graph expansion skipped: %s", exc)
```

- [ ] **Step 6: Run all token efficiency tests**

```bash
uv run pytest tests/test_token_efficiency.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 7: Verify no existing tests are broken**

```bash
uv run pytest tests/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/context_engine/storage/local_backend.py src/context_engine/retrieval/retriever.py tests/test_token_efficiency.py
git commit -m "feat: add graph-aware 1-hop retrieval expansion via CALLS/IMPORTS edges"
```

---

### Task 4: Reinstall and push

- [ ] **Step 1: Reinstall CCE from source**

```bash
uv tool install --editable /Users/raj/projects/Claude-Context-Engine
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/raj/projects/Claude-Context-Engine && uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Quick smoke — verify CLAUDE.md gets the new section**

```bash
cd /tmp && mkdir test_cce_smoke && cd test_cce_smoke && git init && cce init --yes 2>/dev/null; grep "Output Style" CLAUDE.md
```

Expected: prints `## Output Style`.

- [ ] **Step 4: Push**

```bash
cd /Users/raj/projects/Claude-Context-Engine && git push
```
