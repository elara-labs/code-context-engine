import pytest
from context_engine.indexer.manifest import Manifest

@pytest.fixture
def manifest(tmp_path):
    return Manifest(manifest_path=tmp_path / "manifest.json")

def test_empty_manifest_has_no_entries(manifest):
    assert manifest.get_hash("anything.py") is None

def test_update_and_get(manifest):
    manifest.update("src/main.py", "abc123hash")
    assert manifest.get_hash("src/main.py") == "abc123hash"

def test_has_changed_detects_new_file(manifest):
    assert manifest.has_changed("src/main.py", "abc123") is True

def test_has_changed_detects_modification(manifest):
    manifest.update("src/main.py", "old_hash")
    assert manifest.has_changed("src/main.py", "new_hash") is True

def test_has_changed_returns_false_if_same(manifest):
    manifest.update("src/main.py", "same_hash")
    assert manifest.has_changed("src/main.py", "same_hash") is False

def test_save_and_load(tmp_path):
    path = tmp_path / "manifest.json"
    m1 = Manifest(manifest_path=path)
    m1.update("a.py", "hash_a")
    m1.save()
    m2 = Manifest(manifest_path=path)
    assert m2.get_hash("a.py") == "hash_a"

def test_remove(manifest):
    manifest.update("a.py", "hash_a")
    manifest.remove("a.py")
    assert manifest.get_hash("a.py") is None


# --- Schema versioning tests ---

def test_default_schema_version(manifest):
    """A fresh manifest should report CURRENT_SCHEMA_VERSION (2)."""
    from context_engine.indexer.manifest import CURRENT_SCHEMA_VERSION
    assert manifest.schema_version == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 2


def test_old_manifest_detected_as_version_1(tmp_path):
    """A plain-dict manifest (no __schema_version key) is treated as version 1."""
    import json
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"src/a.py": "oldhash"}))
    m = Manifest(manifest_path=path)
    assert m.schema_version == 1


def test_schema_mismatch_flags_needs_reindex(tmp_path):
    """When the stored schema version differs from CURRENT_SCHEMA_VERSION, needs_reindex is True."""
    import json
    path = tmp_path / "manifest.json"
    # Write a version-1 (plain dict) manifest
    path.write_text(json.dumps({"src/a.py": "oldhash"}))
    m = Manifest(manifest_path=path)
    assert m.needs_reindex is True


def test_schema_match_no_reindex(tmp_path):
    """A manifest written by the current version should not trigger needs_reindex."""
    path = tmp_path / "manifest.json"
    m1 = Manifest(manifest_path=path)
    m1.update("src/a.py", "hash_a")
    m1.save()
    m2 = Manifest(manifest_path=path)
    assert m2.schema_version == 2
    assert m2.needs_reindex is False
