"""Tests for project_storage_dir — the slug-based storage path helper."""
from pathlib import Path
from types import SimpleNamespace

from context_engine.utils import project_storage_dir


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(storage_path=str(tmp_path))


def test_same_basename_different_parents_produce_different_dirs(tmp_path):
    """Two projects with the same basename but different parents must NOT collide."""
    cfg = _config(tmp_path)
    dir_a = Path("/home/user/work/api")
    dir_b = Path("/home/user/scratch/api")

    result_a = project_storage_dir(cfg, dir_a)
    result_b = project_storage_dir(cfg, dir_b)

    assert result_a != result_b
    assert result_a.parent == result_b.parent == tmp_path


def test_same_path_is_deterministic(tmp_path):
    """Calling with the same project_dir always returns the same storage dir."""
    cfg = _config(tmp_path)
    project = Path("/home/user/myproject")

    assert project_storage_dir(cfg, project) == project_storage_dir(cfg, project)


def test_slug_contains_basename_and_hex(tmp_path):
    """The slug directory name should be <basename>-<6hex>."""
    cfg = _config(tmp_path)
    project = Path("/home/user/my-app")

    result = project_storage_dir(cfg, project)
    name = result.name

    assert name.startswith("my-app-")
    hex_suffix = name.split("-")[-1]
    assert len(hex_suffix) == 6
    # Must be valid hex
    int(hex_suffix, 16)


def test_legacy_dir_is_migrated(tmp_path):
    """If the legacy (bare basename) dir exists and slug dir does not, rename it."""
    cfg = _config(tmp_path)
    project = tmp_path / "subdir" / "api"
    project.mkdir(parents=True)

    # The legacy dir uses the resolved basename
    legacy = tmp_path / "api"
    legacy.mkdir()
    (legacy / "vectors").mkdir()
    (legacy / "marker.txt").write_text("legacy-data")

    result = project_storage_dir(cfg, project)

    # Legacy dir should have been renamed to the slug dir
    assert result.exists()
    assert (result / "marker.txt").read_text() == "legacy-data"
    assert (result / "vectors").is_dir()
    # Legacy path should no longer exist
    assert not legacy.exists()


def test_no_migration_if_slug_dir_already_exists(tmp_path):
    """If the slug dir already exists, the legacy dir is left alone."""
    cfg = _config(tmp_path)
    project = tmp_path / "subdir" / "api"
    project.mkdir(parents=True)

    # Create both legacy and slug dirs
    legacy = tmp_path / "api"
    legacy.mkdir()
    (legacy / "old.txt").write_text("old")

    slug_dir = project_storage_dir(cfg, project)
    # First call migrated; now create a "new legacy" to verify no second migration
    legacy.mkdir()
    (legacy / "new.txt").write_text("new")

    result = project_storage_dir(cfg, project)

    assert result == slug_dir
    # Legacy dir should still exist (not migrated again)
    assert legacy.exists()
    assert (legacy / "new.txt").read_text() == "new"
