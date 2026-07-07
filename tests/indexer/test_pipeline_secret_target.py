"""Regression tests for target_path secret/ignore bypass (issue #127).

Ensures that when `run_indexing` is called with `target_path` pointing
to a single file, the same exclusion rules applied to whole-directory
scans are also applied:

  · Secret files (e.g. `.env.local`) are not indexed.
  · Files matching `.cceignore` patterns are not indexed.
"""
from __future__ import annotations

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend


def _storage(config, project_dir):
    from context_engine.utils import project_storage_dir
    return project_storage_dir(config, project_dir)


def _chunks_for_file(config, project_dir, rel_path: str) -> list:
    backend = LocalBackend(base_path=str(_storage(config, project_dir)))
    import asyncio
    return asyncio.run(backend.fts_search(rel_path, top_k=50))


@pytest.fixture
def simple_project(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    config = load_config()
    config.storage_path = str(storage_dir)
    return project_dir, config


@pytest.mark.asyncio
async def test_target_path_secret_file_not_indexed(simple_project):
    """run_indexing with target_path pointing to a secret file stores no chunks."""
    project_dir, config = simple_project
    # Create a secret file with real content so chunking would succeed if not blocked
    secret = project_dir / ".env.local"
    secret.write_text("API_KEY=supersecret\nDB_PASSWORD=hunter2\n")

    await run_indexing(config, str(project_dir), target_path=".env.local")

    # No chunks should have been indexed for the secret file
    storage_base = _storage(config, project_dir)
    backend = LocalBackend(base_path=str(storage_base))
    # Count total chunks — must be zero since nothing else was indexed
    assert backend.count_chunks() == 0, (
        "Secret file .env.local was indexed despite being a secret file"
    )


@pytest.mark.asyncio
async def test_target_path_cceignore_respected(simple_project):
    """run_indexing with target_path matching a .cceignore pattern skips the file."""
    project_dir, config = simple_project
    # Write a .cceignore that excludes secrets.txt
    (project_dir / ".cceignore").write_text("secrets.txt\n")
    # Create the target file with real content
    target = project_dir / "secrets.txt"
    target.write_text("password = 'hunter2'\ntoken = 'abc123'\n")

    await run_indexing(config, str(project_dir), target_path="secrets.txt")

    storage_base = _storage(config, project_dir)
    backend = LocalBackend(base_path=str(storage_base))
    assert backend.count_chunks() == 0, (
        "File matching .cceignore pattern was indexed despite being ignored"
    )
