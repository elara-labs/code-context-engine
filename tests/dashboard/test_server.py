"""Tests for the CCE dashboard FastAPI server."""
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from context_engine.config import Config
from context_engine.dashboard.server import create_app
from context_engine.utils import project_storage_dir


def _setup_storage(tmp_path: Path, project_name: str = "my-project") -> tuple[Path, Path]:
    """Create storage dir with stats.json and manifest.json; return (storage_root, project_dir)."""
    project_dir = tmp_path / "workspace" / project_name
    project_dir.mkdir(parents=True)
    config = Config(storage_path=str(tmp_path / "storage"))
    storage_base = project_storage_dir(config, project_dir)
    storage_base.mkdir(parents=True, exist_ok=True)
    return storage_base, project_dir


def _make_client(tmp_path: Path, project_name: str = "my-project") -> tuple[TestClient, Path]:
    storage_base, project_dir = _setup_storage(tmp_path, project_name)
    config = Config(storage_path=str(tmp_path / "storage"))
    app = create_app(config, project_dir)
    return TestClient(app), storage_base


def test_get_root_returns_html(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()


def test_status_no_data(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["initialized"] is False
    assert data["chunks"] == 0
    assert data["files"] == 0
    assert data["queries"] == 0
    assert data["tokens_saved_pct"] == 0
    assert data["output_level"] == "standard"


def test_status_with_stats(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 38, "full_file_tokens": 48000, "served_tokens": 14200, "raw_tokens": 14200}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"src/cli.py": "abc123", "src/config.py": "def456"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["initialized"] is True
    assert data["files"] == 2
    assert data["queries"] == 38
    assert data["tokens_saved_pct"] == 70


def test_status_with_custom_output_level(tmp_path):
    client, storage_base = _make_client(tmp_path)
    (storage_base / "state.json").write_text(json.dumps({"output_level": "max"}))
    r = client.get("/api/status")
    assert r.json()["output_level"] == "max"


def test_files_empty(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/files")
    assert r.status_code == 200
    assert r.json() == []


def test_files_with_manifest(tmp_path):
    client, storage_base = _make_client(tmp_path)
    # Create project files on disk matching the manifest
    project_dir = tmp_path / "workspace" / "my-project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)

    content = "def foo(): pass\n"
    h = hashlib.sha256(content.encode()).hexdigest()
    (project_dir / "src" / "cli.py").write_text(content)

    manifest = {"src/cli.py": h}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    assert r.status_code == 200
    files = r.json()
    assert len(files) == 1
    assert files[0]["path"] == "src/cli.py"
    assert files[0]["status"] == "ok"
    assert files[0]["chunks"] == 0  # no LanceDB table in this test


def test_files_stale_detection(tmp_path):
    client, storage_base = _make_client(tmp_path)
    project_dir = tmp_path / "workspace" / "my-project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)
    (project_dir / "src" / "cli.py").write_text("def foo(): pass\n")

    # Manifest has a different hash → stale
    manifest = {"src/cli.py": "oldhash000"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    files = r.json()
    assert files[0]["status"] == "stale"


def test_files_missing_detection(tmp_path):
    client, storage_base = _make_client(tmp_path)
    # File in manifest but NOT on disk
    manifest = {"src/gone.py": "somehash"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.get("/api/files")
    files = r.json()
    assert files[0]["status"] == "missing"


def test_sessions_empty(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_sessions_returns_persisted(tmp_path):
    client, storage_base = _make_client(tmp_path)
    sessions_dir = storage_base / "sessions"
    sessions_dir.mkdir(parents=True)
    session = {
        "id": "abc123", "project": "my-project", "started_at": 1700000000.0,
        "ended_at": 1700000120.0,
        "decisions": [{"decision": "use JWT", "reason": "stateless", "timestamp": 1700000060.0}],
        "code_areas": [],
        "questions": [],
    }
    (sessions_dir / "abc123.json").write_text(json.dumps(session))
    r = client.get("/api/sessions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert len(data[0]["decisions"]) == 1


@patch("context_engine.pricing._fetch", return_value=None)
@patch("context_engine.pricing._load_cache", return_value=None)
def test_savings_no_data(mock_cache, mock_fetch, tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/savings")
    assert r.status_code == 200
    data = r.json()
    assert data["queries"] == 0
    assert data["tokens_saved"] == 0
    assert data["savings_pct"] == 0
    assert "pricing_model" in data
    assert "available_models" in data
    assert isinstance(data["available_models"], list)


@patch("context_engine.pricing._fetch", return_value=None)
@patch("context_engine.pricing._load_cache", return_value=None)
def test_savings_with_data(mock_cache, mock_fetch, tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 38, "full_file_tokens": 48000, "served_tokens": 14200, "raw_tokens": 14200}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    r = client.get("/api/savings")
    data = r.json()
    assert data["queries"] == 38
    assert data["served_tokens"] == 14200
    assert data["baseline_tokens"] == 48000
    assert data["tokens_saved"] == 33800
    assert data["savings_pct"] == 70
    assert data["pricing_model"] == "opus"
    assert data["cost_saved"] == round(33800 * 15.0 / 1_000_000, 2)


def test_export_returns_combined(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 5, "full_file_tokens": 1000, "served_tokens": 300, "raw_tokens": 300}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"foo.py": "hash1"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))
    r = client.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    data = r.json()
    assert "stats" in data
    assert "manifest" in data
    assert "sessions" in data


def test_reindex_full(tmp_path):
    client, _ = _make_client(tmp_path)
    mock_result = MagicMock(total_chunks=10, indexed_files=["a.py"], errors=[],
                            deleted_files=[], skipped_files=[])
    with patch("context_engine.dashboard.server.run_indexing", new=AsyncMock(return_value=mock_result)):
        r = client.post("/api/reindex", json={"full": True})
    assert r.status_code == 200
    data = r.json()
    assert data["total_chunks"] == 10
    assert data["indexed_files"] == ["a.py"]
    assert data["errors"] == []


def test_reindex_single_file(tmp_path):
    client, _ = _make_client(tmp_path)
    mock_result = MagicMock(total_chunks=3, indexed_files=["src/cli.py"], errors=[],
                            deleted_files=[], skipped_files=[])
    with patch("context_engine.dashboard.server.run_indexing", new=AsyncMock(return_value=mock_result)):
        r = client.post("/api/reindex/src%2Fcli.py", json={})
    assert r.status_code == 200
    assert r.json()["indexed_files"] == ["src/cli.py"]


def test_clear_index(tmp_path):
    client, storage_base = _make_client(tmp_path)
    stats = {"queries": 5, "raw_tokens": 1000, "served_tokens": 300, "full_file_tokens": 1000}
    (storage_base / "stats.json").write_text(json.dumps(stats))
    manifest = {"foo.py": "hash1"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.post("/api/clear")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    raw = json.loads((storage_base / "manifest.json").read_text())
    assert raw["files"] == {}
    assert json.loads((storage_base / "stats.json").read_text())["queries"] == 0


def test_delete_file(tmp_path):
    client, storage_base = _make_client(tmp_path)
    manifest = {"src/cli.py": "hash1", "src/config.py": "hash2"}
    (storage_base / "manifest.json").write_text(json.dumps(manifest))

    r = client.delete("/api/files/src%2Fcli.py")

    assert r.status_code == 200
    raw = json.loads((storage_base / "manifest.json").read_text())
    remaining = raw.get("files", raw)
    assert "src/cli.py" not in remaining
    assert "src/config.py" in remaining


def test_set_compression(tmp_path):
    client, storage_base = _make_client(tmp_path)
    r = client.post("/api/compression", json={"level": "max"})
    assert r.status_code == 200
    assert r.json()["level"] == "max"
    assert json.loads((storage_base / "state.json").read_text())["output_level"] == "max"


def test_set_compression_invalid(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/compression", json={"level": "turbo"})
    assert r.status_code == 422


def test_status_with_versioned_manifest(tmp_path):
    """Dashboard must read the real Manifest.save() schema, not treat the
    top-level dict as {file_path: hash}. Regression for 2026-04-27 review."""
    from context_engine.indexer.manifest import Manifest

    client, storage_base = _make_client(tmp_path)
    manifest = Manifest(manifest_path=storage_base / "manifest.json")
    manifest.update("src/cli.py", "abc123")
    manifest.update("src/config.py", "def456")
    manifest.last_git_sha = "deadbeef"
    manifest.save()

    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["initialized"] is True
    # 2 real files, not 3 (which would mean __schema_version/files/last_git_sha
    # were being counted as paths).
    assert data["files"] == 2


def test_files_with_versioned_manifest(tmp_path):
    """/api/files must iterate the real manifest entries, not schema keys."""
    from context_engine.indexer.manifest import Manifest

    client, storage_base = _make_client(tmp_path)
    project_dir = tmp_path / "workspace" / "my-project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)
    content = "def foo(): pass\n"
    h = hashlib.sha256(content.encode()).hexdigest()
    (project_dir / "src" / "cli.py").write_text(content)

    manifest = Manifest(manifest_path=storage_base / "manifest.json")
    manifest.update("src/cli.py", h)
    manifest.save()

    r = client.get("/api/files")
    assert r.status_code == 200
    files = r.json()
    paths = [f["path"] for f in files]
    assert paths == ["src/cli.py"]
    assert "__schema_version" not in paths
    assert "last_git_sha" not in paths
    assert files[0]["status"] == "ok"


def test_delete_with_versioned_manifest_preserves_schema(tmp_path):
    """Deleting a file must keep __schema_version + last_git_sha intact."""
    from context_engine.indexer.manifest import Manifest

    client, storage_base = _make_client(tmp_path)
    manifest = Manifest(manifest_path=storage_base / "manifest.json")
    manifest.update("src/cli.py", "hash1")
    manifest.update("src/config.py", "hash2")
    manifest.last_git_sha = "deadbeef"
    manifest.save()

    r = client.delete("/api/files/src%2Fcli.py")
    assert r.status_code == 200

    raw = json.loads((storage_base / "manifest.json").read_text())
    assert raw["__schema_version"] == 2
    assert raw["last_git_sha"] == "deadbeef"
    assert "src/cli.py" not in raw["files"]
    assert "src/config.py" in raw["files"]

    # Reload through Manifest to confirm round-trip is still valid.
    reloaded = Manifest(manifest_path=storage_base / "manifest.json")
    assert reloaded.schema_version == 2
    assert reloaded.last_git_sha == "deadbeef"
    assert reloaded.get_hash("src/config.py") == "hash2"


def test_clear_writes_versioned_manifest(tmp_path):
    """/api/clear must write the current Manifest schema, not a bare {}."""
    from context_engine.indexer.manifest import Manifest

    client, storage_base = _make_client(tmp_path)
    manifest = Manifest(manifest_path=storage_base / "manifest.json")
    manifest.update("foo.py", "h1")
    manifest.save()

    r = client.post("/api/clear")
    assert r.status_code == 200

    raw = json.loads((storage_base / "manifest.json").read_text())
    assert raw == {"__schema_version": 2, "files": {}, "last_git_sha": None}
    # Confirm the cleared file round-trips through Manifest with no needs_reindex.
    reloaded = Manifest(manifest_path=storage_base / "manifest.json")
    assert reloaded.needs_reindex is False


def test_find_free_port():
    from context_engine.cli import _find_free_port
    port = _find_free_port()
    assert 1024 < port < 65535


def test_dashboard_command_no_browser(tmp_path):
    """cce dashboard --no-browser calls uvicorn.run with a valid port."""
    import threading
    from unittest.mock import patch
    from click.testing import CliRunner
    from context_engine.cli import main

    config = Config(storage_path=str(tmp_path / "storage"))
    project_dir = tmp_path / "workspace" / "proj"
    project_dir.mkdir(parents=True)

    captured = {}
    started = threading.Event()

    def fake_uvicorn_run(app, **kwargs):
        captured["port"] = kwargs.get("port")
        started.set()

    runner = CliRunner()
    with patch("context_engine.cli.load_config", return_value=config), \
         patch("context_engine.cli.Path.cwd", return_value=project_dir), \
         patch("uvicorn.run", side_effect=fake_uvicorn_run):
        result = runner.invoke(main, ["dashboard", "--no-browser"])

    assert result.exit_code == 0
    assert "localhost:" in result.output
    assert captured.get("port") is not None
    assert 1024 < captured["port"] < 65535
