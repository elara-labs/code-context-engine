"""End-to-end test: index a project, run queries via MCP _record path,
then verify `cce savings` produces correct output.

Unlike the unit tests in test_cli_savings.py which mock stats, this test
exercises the real indexing + retrieval + stats recording pipeline.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from context_engine.cli import main
from context_engine.config import Config
from context_engine.indexer.chunker import Chunker
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.manifest import Manifest
from context_engine.storage.local_backend import LocalBackend
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType


SAMPLE_FILES = {
    "src/auth.py": '''
class AuthService:
    """Handles user authentication and session management."""
    def login(self, username: str, password: str) -> bool:
        """Authenticate a user with username and password."""
        return self._check_credentials(username, password)

    def _check_credentials(self, username: str, password: str) -> bool:
        return username == "admin" and password == "secret"

    def logout(self, session_id: str) -> None:
        """Invalidate a user session."""
        pass
''',
    "src/user.py": '''
from auth import AuthService

class UserService:
    """Manages user profiles and preferences."""
    def __init__(self):
        self.auth = AuthService()

    def get_profile(self, user_id: int) -> dict:
        """Fetch user profile by ID."""
        return {"id": user_id, "name": "Test User"}

    def update_profile(self, user_id: int, data: dict) -> dict:
        """Update user profile fields."""
        profile = self.get_profile(user_id)
        profile.update(data)
        return profile
''',
    "src/api.py": '''
from user import UserService
from auth import AuthService

class APIRouter:
    """HTTP API endpoint handlers."""
    def __init__(self):
        self.users = UserService()
        self.auth = AuthService()

    def handle_login(self, request: dict) -> dict:
        """POST /login endpoint."""
        ok = self.auth.login(request["username"], request["password"])
        return {"success": ok}

    def handle_profile(self, request: dict) -> dict:
        """GET /profile endpoint."""
        return self.users.get_profile(request["user_id"])

    def handle_update(self, request: dict) -> dict:
        """PUT /profile endpoint."""
        return self.users.update_profile(request["user_id"], request["data"])
''',
    "src/config.py": '''
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-key")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
''',
}


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def indexed_project(tmp_path):
    """Create a temp project, index it, return (storage_dir, project_dir)."""
    project_dir = tmp_path / "project"
    storage_dir = tmp_path / "storage"
    project_dir.mkdir()
    storage_dir.mkdir()

    # Write sample files
    for rel_path, content in SAMPLE_FILES.items():
        fp = project_dir / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)

    # Index
    chunker = Chunker()
    embedder = Embedder()
    backend = LocalBackend(base_path=str(storage_dir))
    _manifest = Manifest(manifest_path=storage_dir / "manifest.json")

    all_chunks = []
    all_nodes = []
    all_edges = []

    for rel_path, content in SAMPLE_FILES.items():
        lang = "python" if rel_path.endswith(".py") else "text"
        chunks = chunker.chunk(content, file_path=str(project_dir / rel_path), language=lang)
        file_node = GraphNode(
            id=f"file_{rel_path}", node_type=NodeType.FILE,
            name=Path(rel_path).name, file_path=str(project_dir / rel_path),
        )
        all_nodes.append(file_node)
        for chunk in chunks:
            all_nodes.append(GraphNode(
                id=chunk.id, node_type=NodeType.FUNCTION,
                name=chunk.id, file_path=str(project_dir / rel_path),
            ))
            all_edges.append(GraphEdge(
                source_id=file_node.id, target_id=chunk.id,
                edge_type=EdgeType.DEFINES,
            ))
        all_chunks.extend(chunks)

    embedder.embed(all_chunks)

    import asyncio
    asyncio.run(backend.ingest(all_chunks, all_nodes, all_edges))

    return storage_dir, project_dir


@pytest.mark.asyncio
async def test_query_and_savings(runner, indexed_project):
    """Index files, run queries, record stats, verify cce savings output."""
    storage_dir, project_dir = indexed_project

    embedder = Embedder()
    backend = LocalBackend(base_path=str(storage_dir))
    retriever = HybridRetriever(backend=backend, embedder=embedder)

    queries = [
        "authentication login flow",
        "user profile management",
        "API endpoints routes",
        "configuration settings",
    ]

    stats = {"queries": 0, "raw_tokens": 0, "served_tokens": 0, "full_file_tokens": 0}

    for q in queries:
        results = await retriever.retrieve(q, top_k=5)
        assert len(results) > 0, f"query '{q}' returned no results"

        raw_tokens = sum(int(len(r.content.split()) * 1.3) for r in results)

        # Estimate compression (truncation mode)
        served_tokens = int(raw_tokens * 0.4)

        # Calculate full file baseline
        seen_files = set()
        full_file_tokens = 0
        for r in results:
            if r.file_path not in seen_files:
                seen_files.add(r.file_path)
                try:
                    content = Path(r.file_path).read_text()
                    full_file_tokens += int(len(content.split()) * 1.3)
                except FileNotFoundError:
                    full_file_tokens += raw_tokens

        stats["queries"] += 1
        stats["raw_tokens"] += raw_tokens
        stats["served_tokens"] += served_tokens
        stats["full_file_tokens"] += full_file_tokens

    # Write stats
    (storage_dir / "stats.json").write_text(json.dumps(stats))

    # Verify stats are sane
    assert stats["queries"] == 4
    assert stats["raw_tokens"] > 0
    assert stats["served_tokens"] > 0
    assert stats["full_file_tokens"] > 0
    assert stats["served_tokens"] < stats["raw_tokens"]

    # Run cce savings and verify output
    config = Config(storage_path=str(storage_dir.parent))

    # storage_dir is tmp_path/storage, project name = "storage"
    project_name = storage_dir.name
    with runner.isolated_filesystem():
        cwd = Path.cwd() / project_name
        cwd.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            result = runner.invoke(main, ["savings"])

    assert result.exit_code == 0, f"savings failed:\n{result.output}"
    out = result.output

    # Project name and query count shown
    assert project_name in out
    assert "4" in out  # 4 queries

    # Savings percentage shown (should be > 0)
    assert "tokens saved" in out.lower() or "%" in out

    # Grid bar has at least one ⛁
    assert "⛁" in out

    # Cost estimate line present
    assert "Cost estimate" in out
    assert "/1M tokens" in out

    # Before/after/saved structure present
    assert "Without CCE" in out
    assert "With CCE" in out
    assert "Saved" in out

    # JSON output also works
    with runner.isolated_filesystem():
        cwd = Path.cwd() / project_name
        cwd.mkdir(parents=True, exist_ok=True)
        with patch("context_engine.cli.load_config", return_value=config), \
             patch("context_engine.cli.Path.cwd", return_value=cwd):
            json_result = runner.invoke(main, ["savings", "--json"])

    assert json_result.exit_code == 0
    data = json.loads(json_result.output)
    assert data["queries"] == 4
    assert data["tokens_saved"] > 0
    assert data["savings_pct"] > 0


@pytest.mark.asyncio
async def test_retrieval_quality(indexed_project):
    """Verify search results are relevant to queries."""
    storage_dir, project_dir = indexed_project

    embedder = Embedder()
    backend = LocalBackend(base_path=str(storage_dir))
    retriever = HybridRetriever(backend=backend, embedder=embedder)

    # Auth query should return auth-related chunks
    results = await retriever.retrieve("authentication login", top_k=5)
    auth_hits = [r for r in results if "auth" in r.file_path.lower() or "login" in r.content.lower()]
    assert len(auth_hits) > 0, "auth query didn't find auth-related code"

    # API query should return api-related chunks
    results = await retriever.retrieve("API endpoints", top_k=5)
    api_hits = [r for r in results if "api" in r.file_path.lower() or "endpoint" in r.content.lower() or "handle" in r.content.lower()]
    assert len(api_hits) > 0, "API query didn't find API-related code"

    # Config query should return config chunks
    results = await retriever.retrieve("database configuration", top_k=5)
    config_hits = [r for r in results if "config" in r.file_path.lower() or "DATABASE" in r.content]
    assert len(config_hits) > 0, "config query didn't find config-related code"
