"""Reindex durability — old index data must survive embed/ingest failures.

Regression tests for the 2026-04-27 review finding: the pipeline used to
delete previously-indexed chunks BEFORE embedding succeeded, so a transient
fastembed model download or sqlite-vec failure could wipe a working index.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend


@pytest.fixture
def project_with_existing_index(tmp_path):
    """A project that's been indexed once; returns (project_dir, config)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "alpha.py").write_text(
        "def alpha():\n    return 'first version'\n"
    )
    (project_dir / "beta.py").write_text(
        "def beta():\n    return 'second version'\n"
    )

    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)
    return project_dir, config


def _project_storage(config, project_dir: Path) -> Path:
    return Path(config.storage_path) / project_dir.name


def _count_chunks(config, project_dir: Path) -> int:
    backend = LocalBackend(base_path=str(_project_storage(config, project_dir)))
    return backend.count_chunks()


@pytest.mark.asyncio
async def test_embed_failure_preserves_existing_index(project_with_existing_index):
    """If Embedder.embed raises during a re-index, the old chunks survive."""
    project_dir, config = project_with_existing_index

    # First run: real embedder, succeeds. This is the baseline we must not lose.
    first = await run_indexing(config, str(project_dir), full=True)
    assert first.total_chunks > 0, "fixture failed — initial index empty"
    baseline = _count_chunks(config, project_dir)
    assert baseline > 0

    # Mutate a file so it ends up in files_to_replace, then re-index with a
    # forced embedder failure. The old vectors must still be queryable.
    (project_dir / "alpha.py").write_text(
        "def alpha():\n    return 'mutated version'\n"
    )

    with patch(
        "context_engine.indexer.pipeline.Embedder"
    ) as embedder_cls:
        instance = embedder_cls.return_value
        instance.embed.side_effect = RuntimeError("simulated embed failure")
        result = await run_indexing(config, str(project_dir))

    assert any("Embedding failed" in e for e in result.errors), result.errors
    # Old data is intact — we never deleted it because embed failed first.
    assert _count_chunks(config, project_dir) == baseline


@pytest.mark.asyncio
async def test_ingest_failure_does_not_wipe_other_files(project_with_existing_index):
    """If backend.ingest raises, the manifest is not advanced.

    Per Codex: at minimum, embed before deleting old rows. We additionally
    avoid persisting the manifest when ingest fails so the next run will
    re-detect the changed file and try again.
    """
    project_dir, config = project_with_existing_index

    first = await run_indexing(config, str(project_dir), full=True)
    assert first.total_chunks > 0

    manifest_path = _project_storage(config, project_dir) / "manifest.json"
    manifest_before = manifest_path.read_text()

    (project_dir / "alpha.py").write_text(
        "def alpha():\n    return 'mutated again'\n"
    )

    # Patch LocalBackend.ingest on the *instance* the pipeline creates.
    real_ingest = LocalBackend.ingest

    async def boom(self, *args, **kwargs):
        raise RuntimeError("simulated ingest failure")

    with patch.object(LocalBackend, "ingest", new=boom):
        result = await run_indexing(config, str(project_dir))

    assert any("ingest failed" in e.lower() for e in result.errors), result.errors
    # Manifest must NOT be saved on ingest failure — next run will retry.
    assert manifest_path.read_text() == manifest_before

    # Sanity restore (not strictly needed, fixture is fresh per-test).
    LocalBackend.ingest = real_ingest
