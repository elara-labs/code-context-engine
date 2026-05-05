"""Regression: indexing must surface phase markers + per-batch progress so
large repos do not look hung.

Reported behavior: after `cce init` on a 7035-file project, the chunking
progress bar reaches 100% and then the embedder silently runs ONNX inference
in a fastembed worker pool for many minutes. Users repeatedly Ctrl-C'd
thinking it had deadlocked.

This test pins:
  1. `run_indexing` calls phase_fn with an "Embedding…" message BEFORE
     calling the embedder, so the user knows new work has started.
  2. `Embedder.embed` invokes the same progress callback as inference
     advances, so the user sees motion instead of staring at the same line
     for tens of minutes.
"""
from __future__ import annotations


import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing


@pytest.fixture
def small_project(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    # A handful of files so chunking actually happens; we don't need volume
    # because phase_fn fires unconditionally before embedding starts.
    for i in range(3):
        (project_dir / f"mod_{i}.py").write_text(
            f"def fn_{i}():\n    return {i}\n"
        )
    storage = tmp_path / "storage"
    storage.mkdir()
    config = load_config()
    config.storage_path = str(storage)
    return project_dir, config


@pytest.mark.asyncio
async def test_phase_fn_announces_embedding_and_ingest(small_project):
    project_dir, config = small_project
    captured: list[str] = []

    result = await run_indexing(
        config,
        project_dir,
        full=True,
        phase_fn=lambda msg: captured.append(msg),
    )
    assert result.total_chunks > 0, "test invariant: project must produce chunks"

    joined = "\n".join(captured)
    assert "Embedding" in joined, (
        f"expected an 'Embedding…' phase marker before the embed call; "
        f"captured: {captured!r}"
    )
    assert "Writing" in joined, (
        f"expected a 'Writing…' phase marker before backend.ingest so the "
        f"user knows the gap between embedding-done and init-done is filled; "
        f"captured: {captured!r}"
    )

    # phase_fn comes first ("Embedding 3 chunks…") then any per-chunk
    # progress lines from the embedder. The header must fire before any
    # per-batch line, otherwise the first thing the user sees mid-embed is
    # a half-status without context.
    embed_idx = next(i for i, m in enumerate(captured) if "Embedding" in m and "chunks" in m)
    assert embed_idx == 0 or "embedded" not in captured[0], (
        f"per-batch progress emitted before the 'Embedding…' header; order: {captured!r}"
    )


def test_embedder_calls_progress_fn_during_inference(small_project):
    """Direct unit test on the Embedder side — verifies the per-batch
    progress hook fires at least once during inference. Embedder.embed
    takes a `(current, total) -> None` callback (the canonical numeric
    API used by cli.py to drive its progress bar). The "still alive"
    intent the WIP originally wanted is delivered by cli.py rendering
    a live `chunks/total` bar from these numeric ticks.
    """
    from context_engine.indexer.embedder import Embedder
    from context_engine.models import Chunk, ChunkType

    chunks = [
        Chunk(
            id=f"c{i}",
            content=f"def f{i}(): return {i}",
            chunk_type=ChunkType.FUNCTION,
            file_path=f"f{i}.py",
            start_line=1,
            end_line=1,
            language="python",
        )
        for i in range(3)
    ]

    seen: list[tuple[int, int]] = []
    embedder = Embedder()
    embedder.embed(
        chunks,
        batch_size=1,  # force per-chunk callback granularity
        progress_fn=lambda current, total: seen.append((current, total)),
    )

    assert seen, "expected at least one progress callback during embedding"
    # Final tick must report the full chunk count.
    assert seen[-1] == (3, 3), (
        f"expected final tick = (3, 3); got {seen!r}"
    )
    # Embeddings actually attached.
    assert all(c.embedding is not None for c in chunks)
