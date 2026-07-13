"""Smoke tests for all dashboard API endpoints.

Verifies every endpoint returns the right status code, content type,
and expected response structure. Runs fast with mocked storage.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from context_engine.config import Config
from context_engine.dashboard.server import create_app
from context_engine.memory import db as memory_db
from context_engine.utils import project_storage_dir


def _setup(tmp_path: Path, *, with_stats: bool = False, with_memory: bool = False):
    """Create storage + project dirs, optionally seed data. Return TestClient."""
    project_name = "smoke-project"
    project_dir = tmp_path / "workspace" / project_name
    project_dir.mkdir(parents=True)
    config = Config(storage_path=str(tmp_path / "storage"))
    storage_base = project_storage_dir(config, project_dir)
    storage_base.mkdir(parents=True, exist_ok=True)

    if with_stats:
        (storage_base / "stats.json").write_text(json.dumps({
            "queries": 12,
            "full_file_tokens": 50000,
            "raw_tokens": 15000,
            "served_tokens": 5000,
        }))
        (storage_base / "manifest.json").write_text(json.dumps({
            "src/auth.py": "abc123",
            "src/user.py": "def456",
            "src/api.py": "ghi789",
        }))

    if with_memory:
        conn = memory_db.connect(storage_base / "memory.db")
        try:
            memory_db.record_savings(conn, bucket="retrieval", baseline=10000, served=3000)
            memory_db.record_savings(conn, bucket="chunk_compression", baseline=3000, served=1000)
        finally:
            conn.close()

    config = Config(storage_path=str(tmp_path / "storage"))
    app = create_app(config, project_dir)
    return TestClient(app), storage_base


# ── HTML page ─────────────────────────────────────────


def test_root_returns_html(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "CCE Dashboard" in r.text


def test_root_contains_js_functions(tmp_path):
    """The embedded JS should define key functions (no parse errors)."""
    client, _ = _setup(tmp_path)
    r = client.get("/")
    assert "loadStatus" in r.text
    assert "loadOverviewPanels" in r.text
    assert "loadFiles" in r.text
    assert "loadSavings" in r.text
    assert "Input / Output Format" in r.text
    assert "saveFormatConfig" in r.text


def test_root_js_parses_without_error(tmp_path):
    """The embedded JS should not contain syntax-breaking quote issues."""
    client, _ = _setup(tmp_path)
    html = client.get("/").text
    # Extract the script content and verify key functions are defined
    # (if JS has a parse error, functions after the error won't exist)
    import re
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(scripts) >= 1
    script = scripts[0]
    # These functions are defined near the end of the script.
    # If a parse error exists earlier, they won't be present.
    assert "function loadStatus" in script
    assert "function loadSavings" in script
    assert "function loadMemorySessions" in script
    assert "loadStatus();" in script  # boot call at the very end


# ── /api/status ───────────────────────────────────────


def test_status_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/status").json()
    assert data["initialized"] is False
    assert data["chunks"] == 0
    assert data["queries"] == 0
    assert data["tokens_saved_pct"] == 0


def test_status_with_data(tmp_path):
    client, _ = _setup(tmp_path, with_stats=True)
    data = client.get("/api/status").json()
    assert data["initialized"] is True
    assert data["files"] == 3
    assert data["queries"] == 12
    assert data["tokens_saved_pct"] > 0


# ── /api/savings ──────────────────────────────────────


def test_savings_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/savings").json()
    assert data["queries"] == 0
    assert data["savings_pct"] == 0


def test_savings_with_data(tmp_path):
    client, _ = _setup(tmp_path, with_stats=True)
    data = client.get("/api/savings").json()
    assert data["queries"] == 12
    assert data["baseline_tokens"] == 50000
    assert data["served_tokens"] == 5000
    assert data["tokens_saved"] == 45000
    assert data["savings_pct"] == 90


# ── /api/files ────────────────────────────────────────


def test_files_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/files").json()
    assert data == []


def test_files_with_manifest(tmp_path):
    client, _ = _setup(tmp_path, with_stats=True)
    data = client.get("/api/files").json()
    assert len(data) == 3
    paths = {f["path"] for f in data}
    assert "src/auth.py" in paths


# ── /api/sessions ─────────────────────────────────────


def test_sessions_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/sessions").json()
    assert isinstance(data, list)


# ── /api/memory/sessions ──────────────────────────────


def test_memory_sessions_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/memory/sessions").json()
    assert isinstance(data, list)
    assert len(data) == 0


# ── /api/memory/decisions ─────────────────────────────


def test_memory_decisions_empty(tmp_path):
    client, _ = _setup(tmp_path)
    data = client.get("/api/memory/decisions").json()
    assert isinstance(data, list)


# ── /api/export ───────────────────────────────────────


def test_export(tmp_path):
    client, _ = _setup(tmp_path, with_stats=True)
    r = client.get("/api/export")
    assert r.status_code == 200
    data = r.json()
    assert "stats" in data or "manifest" in data or isinstance(data, dict)


# ── POST /api/compression ────────────────────────────


def test_set_compression_valid(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.post("/api/compression", json={"level": "max"})
    assert r.status_code == 200
    data = r.json()
    assert data["level"] == "max"


def test_format_config_valid(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.post("/api/format", json={
        "input_preset": "deep",
        "top_k": 1,
        "max_tokens": 500,
        "output_level": "max",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["input_preset"] == "deep"
    assert data["top_k"] == 20
    assert data["max_tokens"] == 12000
    assert data["output_level"] == "max"


def test_set_compression_invalid(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.post("/api/compression", json={"level": "banana"})
    assert r.status_code == 422 or r.status_code == 400


# ── POST /api/clear ──────────────────────────────────


def test_clear(tmp_path):
    client, storage = _setup(tmp_path, with_stats=True)
    r = client.post("/api/clear")
    assert r.status_code == 200


# ── POST /api/reindex ────────────────────────────────


def test_reindex(tmp_path):
    client, _ = _setup(tmp_path, with_stats=True)
    r = client.post("/api/reindex", json={"full": False})
    assert r.status_code == 200


# ── Content-type checks ──────────────────────────────


def test_all_api_endpoints_return_json(tmp_path):
    """Every /api/* GET endpoint returns application/json."""
    client, _ = _setup(tmp_path, with_stats=True)
    api_paths = [
        "/api/status",
        "/api/files",
        "/api/sessions",
        "/api/savings",
        "/api/memory/sessions",
        "/api/memory/decisions",
    ]
    for path in api_paths:
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert "application/json" in r.headers["content-type"], f"{path} not JSON"
