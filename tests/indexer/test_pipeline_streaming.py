"""Streaming ingest — pipeline must not accumulate the whole project in memory.

For large projects, embedding + ingesting the full chunk list at the very end
peaks memory at ~(chunks * (content + 384*4 bytes)). The pipeline batches
file reads at _BATCH=50 already, but historically built up `all_chunks`,
`all_nodes`, `all_edges` for the whole run before a single embed/ingest
call. This test pins the new contract: each file-batch is embedded and
ingested before the next batch is read.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend


@pytest.fixture
def many_file_project(tmp_path):
    """A project with enough files to span more than one indexing batch."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    # _BATCH = 50 in pipeline.py, so 120 files guarantees 3 batches.
    for i in range(120):
        (project_dir / f"mod_{i:03d}.py").write_text(
            f"def func_{i}():\n    return {i}\n"
        )

    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)
    return project_dir, config


@pytest.mark.asyncio
async def test_ingest_called_per_batch_not_once_at_end(many_file_project):
    """backend.ingest must be invoked multiple times for a multi-batch project.

    A single trailing ingest call means we accumulated the entire project's
    chunks in memory before persisting any of them — that's the OOM behavior
    we're trying to eliminate.
    """
    project_dir, config = many_file_project

    real_ingest = LocalBackend.ingest
    call_count = 0
    per_call_chunk_counts: list[int] = []

    async def counting_ingest(self, chunks, nodes, edges):
        nonlocal call_count
        call_count += 1
        per_call_chunk_counts.append(len(chunks))
        return await real_ingest(self, chunks, nodes, edges)

    with patch.object(LocalBackend, "ingest", new=counting_ingest):
        result = await run_indexing(config, str(project_dir), full=True)

    assert result.total_chunks > 0, "fixture failed — index empty"
    assert call_count >= 2, (
        f"ingest was called {call_count} time(s); streaming pipeline must "
        f"call ingest per file-batch. Per-call chunk counts: "
        f"{per_call_chunk_counts}"
    )
    # No single batch should hold the entire project.
    assert max(per_call_chunk_counts) < result.total_chunks, (
        f"one batch held all {result.total_chunks} chunks "
        f"(per-call: {per_call_chunk_counts}) — not actually streaming"
    )


@pytest.mark.asyncio
async def test_streaming_preserves_total_chunk_count(many_file_project):
    """Streaming must not lose chunks — the index ends up with the same total
    as a non-streaming run would have produced."""
    project_dir, config = many_file_project

    result = await run_indexing(config, str(project_dir), full=True)

    storage = Path(config.storage_path) / project_dir.name
    backend = LocalBackend(base_path=str(storage))
    assert backend.count_chunks() == result.total_chunks
    # 120 single-function files → 120 function chunks (plus possibly module
    # fallback chunks). Just sanity check we didn't drop a batch.
    assert result.total_chunks >= 120
