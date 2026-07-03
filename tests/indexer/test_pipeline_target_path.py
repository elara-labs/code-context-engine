"""Single-file `target_path` behaviour of the indexing pipeline.

The watcher funnels every file event through `run_indexing(target_path=...)`,
so the single-file branch must enforce the same protections as the full walk:

  · secret-named files (.env.production, …) are never read or indexed
  · .cceignore patterns apply
  · symlinked project roots (macOS /tmp → /private/tmp) don't blow up
  · a deleted file that's still in the manifest is pruned, not an error
"""
from __future__ import annotations

from pathlib import Path

import pytest

from context_engine.config import load_config
from context_engine.indexer.manifest import Manifest
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend
from context_engine.utils import project_storage_dir


@pytest.fixture
def project(tmp_path):
    """Minimal project + isolated storage; returns (project_dir, config)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "keep.py").write_text("def keep():\n    return 1\n")
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)
    return project_dir, config


def _backend(config, project_dir: Path) -> LocalBackend:
    storage = project_storage_dir(config, Path(project_dir))
    return LocalBackend(base_path=str(storage))


# ── Bug: single-file target bypassed secret + cceignore filters ─────────────

@pytest.mark.asyncio
async def test_target_path_secret_file_is_not_indexed(project):
    """Saving `.env.production` in a watched project routes through
    target_path — is_secret_file must apply there too, not only in the
    directory walk."""
    project_dir, config = project
    (project_dir / ".env.production").write_text(
        "DB_PASSWORD=hunter2longvalue123\nSTRIPE_KEY=" + "sk_live_" + "abcdefghij1234567890abcd\n"
    )

    result = await run_indexing(
        config, str(project_dir), target_path=".env.production"
    )

    assert result.indexed_files == []
    assert result.total_chunks == 0
    assert not result.errors
    assert _backend(config, project_dir).count_chunks() == 0


@pytest.mark.asyncio
async def test_target_path_respects_cceignore(project):
    """A `.cceignore`d file must be skipped on the single-file path too."""
    project_dir, config = project
    (project_dir / ".cceignore").write_text("*.generated.py\nvendor/\n")
    (project_dir / "schema.generated.py").write_text(
        "def generated():\n    return 'machine output'\n"
    )
    vendor = project_dir / "vendor"
    vendor.mkdir()
    (vendor / "lib.py").write_text("def vendored():\n    return 2\n")

    for target in ("schema.generated.py", "vendor/lib.py"):
        result = await run_indexing(config, str(project_dir), target_path=target)
        assert result.indexed_files == [], f"{target} should have been ignored"
        assert result.total_chunks == 0
        assert not result.errors

    assert _backend(config, project_dir).count_chunks() == 0


@pytest.mark.asyncio
async def test_target_path_normal_file_still_indexed(project):
    """Sanity: the filters must not over-block ordinary files."""
    project_dir, config = project
    result = await run_indexing(config, str(project_dir), target_path="keep.py")
    assert result.indexed_files == ["keep.py"]
    assert result.total_chunks > 0
    assert not result.errors


# ── Bug: symlinked project root broke relative_to on reindex ────────────────

@pytest.mark.asyncio
async def test_target_path_through_symlinked_project_root(tmp_path):
    """On macOS /tmp resolves to /private/tmp — target paths resolve() to
    the real root while project_dir stayed unresolved, so relative_to()
    raised ValueError on every watcher-triggered reindex."""
    real = tmp_path / "real_proj"
    real.mkdir()
    (real / "main.py").write_text("def main():\n    return 42\n")
    link = tmp_path / "link_proj"
    link.symlink_to(real)

    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)

    result = await run_indexing(config, str(link), target_path="main.py")

    assert not result.errors
    assert result.indexed_files == ["main.py"]
    assert result.total_chunks > 0


# ── Bug: watcher deletions never pruned the index ────────────────────────────

@pytest.mark.asyncio
async def test_target_path_deleted_file_prunes_index_and_manifest(project):
    """The watcher enqueues deleted paths too. When the target no longer
    exists on disk but is tracked in the manifest, the pipeline must prune
    its chunks and manifest entry instead of erroring."""
    project_dir, config = project
    (project_dir / "gone.py").write_text("def gone():\n    return 'bye'\n")

    first = await run_indexing(config, str(project_dir), full=True)
    assert "gone.py" in first.indexed_files
    backend = _backend(config, project_dir)
    baseline = backend.count_chunks()
    assert baseline > 0

    (project_dir / "gone.py").unlink()
    result = await run_indexing(config, str(project_dir), target_path="gone.py")

    assert not result.errors, result.errors
    assert result.deleted_files == ["gone.py"]
    assert backend.count_chunks() < baseline

    manifest = Manifest(
        manifest_path=project_storage_dir(config, project_dir) / "manifest.json"
    )
    assert manifest.get_hash("gone.py") is None
    assert manifest.get_hash("keep.py") is not None


@pytest.mark.asyncio
async def test_target_path_never_indexed_missing_file_still_errors(project):
    """A path that neither exists nor is tracked stays an error — we only
    treat manifest-tracked paths as deletions."""
    project_dir, config = project
    result = await run_indexing(
        config, str(project_dir), target_path="never_existed.py"
    )
    assert any("not found" in e.lower() for e in result.errors), result.errors
    assert result.deleted_files == []
