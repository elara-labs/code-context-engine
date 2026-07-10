"""Each indexed chunk must carry the source file's st_mtime in metadata.

run_indexing stamps chunk.metadata["modified_ts"] so that
ConfidenceScorer._recency_score gets a real signal instead of the
neutral 0.5 it returns when the key is absent.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend
from context_engine.utils import project_storage_dir


@pytest.fixture
def project(tmp_path):
    """Minimal project + isolated storage; returns (project_dir, config)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)
    return project_dir, config


def _backend(config, project_dir: Path) -> LocalBackend:
    storage = project_storage_dir(config, Path(project_dir))
    return LocalBackend(base_path=str(storage))


def _all_chunk_ids(backend: LocalBackend) -> list[str]:
    """List all chunk ids directly from the SQLite table."""
    store = backend._vector_store
    with store._lock:
        rows = store._conn.execute("SELECT id FROM chunks").fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_indexed_chunks_carry_file_mtime(project):
    project_dir, config = project
    src = project_dir / "app.py"
    src.write_text("def handler():\n    return 42\n")
    known_mtime = 1_700_000_000.0
    os.utime(src, (known_mtime, known_mtime))

    result = await run_indexing(config, str(project_dir), full=True)
    assert not result.errors, result.errors

    backend = _backend(config, project_dir)
    chunk_ids = _all_chunk_ids(backend)
    assert chunk_ids, "expected indexed chunks"

    chunks = await backend.get_chunks_by_ids(chunk_ids)
    assert chunks, "expected indexed chunks"
    for c in chunks:
        assert c.metadata.get("modified_ts") == pytest.approx(known_mtime), (
            f"chunk {c.id} missing modified_ts; got metadata={c.metadata}"
        )
