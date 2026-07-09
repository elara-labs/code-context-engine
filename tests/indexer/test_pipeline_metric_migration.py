"""Cosine-metric migration must not leave an existing index silently empty.

Before this fix, opening a pre-cosine (L2) vector store wiped the on-disk
chunks for the cosine rebuild but left manifest.json claiming every file was
still indexed. An incremental reindex (the default for `cce index` and the
watcher) then skipped every "unchanged" file, so `context_search` returned
nothing until the user ran `cce index --full`.

The pipeline now detects the rebuild (VectorStore.metric_rebuilt) and clears
the manifest so the next incremental run repopulates the index.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend
from context_engine.utils import project_storage_dir


@pytest.fixture
def project(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "app.py").write_text("def handler():\n    return 42\n")
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)
    return project_dir, config


def _chunk_count(config, project_dir: Path) -> int:
    storage = project_storage_dir(config, Path(project_dir))
    return LocalBackend(base_path=str(storage))._vector_store.count()


def _downgrade_vec_table_to_l2(config, project_dir: Path) -> None:
    """Rewrite chunks_vec without distance_metric=cosine to simulate an index
    created by a pre-cosine release."""
    storage = project_storage_dir(config, Path(project_dir))
    vec_db = Path(storage) / "vectors" / "vectors.db"
    import sqlite3

    import sqlite_vec

    conn = sqlite3.connect(str(vec_db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    dim = conn.execute("SELECT vec_length(embedding) FROM chunks_vec LIMIT 1").fetchone()[0]
    conn.execute("DROP TABLE chunks_vec")
    conn.execute(f"CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[{dim}])")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_incremental_reindex_repopulates_after_cosine_migration(project):
    project_dir, config = project

    # 1. Index normally, then downgrade the vec table to legacy L2 on disk.
    result = await run_indexing(config, str(project_dir), full=True)
    assert not result.errors, result.errors
    assert _chunk_count(config, project_dir) > 0
    _downgrade_vec_table_to_l2(config, project_dir)

    # 2. Incremental reindex (full=False) — the file content is unchanged, so
    #    without the manifest-clear the wiped index would stay empty.
    result = await run_indexing(config, str(project_dir), full=False)
    assert not result.errors, result.errors

    # 3. The index must be repopulated, not empty.
    assert _chunk_count(config, project_dir) > 0, (
        "cosine migration wiped the index and the incremental reindex did not "
        "repopulate it — the manifest-clear guard failed"
    )
