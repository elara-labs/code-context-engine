"""End-to-end test: index a project, retrieve context, build bootstrap."""
import pytest
from pathlib import Path

from context_engine.indexer.chunker import Chunker
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.manifest import Manifest
from context_engine.storage.local_backend import LocalBackend
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.compression.compressor import Compressor
from context_engine.integration.bootstrap import BootstrapBuilder
from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType


SAMPLE_PROJECT = {
    "src/auth.py": '''
class AuthService:
    """Handles user authentication."""
    def login(self, username: str, password: str) -> bool:
        """Authenticate a user with username and password."""
        return self._check_credentials(username, password)

    def _check_credentials(self, username: str, password: str) -> bool:
        return username == "admin" and password == "secret"
''',
    "src/user.py": '''
from auth import AuthService

class UserService:
    """Manages user profiles."""
    def __init__(self):
        self.auth = AuthService()

    def get_profile(self, user_id: int) -> dict:
        """Fetch user profile by ID."""
        return {"id": user_id, "name": "Test User"}
''',
    "README.md": "# Test Project\nA sample project for testing the context engine.\n",
}


@pytest.fixture
def sample_project(tmp_path):
    for rel_path, content in SAMPLE_PROJECT.items():
        file_path = tmp_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    return tmp_path


@pytest.mark.asyncio
async def test_full_pipeline(sample_project, tmp_path):
    """Test: index -> retrieve -> compress -> bootstrap."""
    storage_dir = tmp_path / "storage"

    # 1. Index
    chunker = Chunker()
    embedder = Embedder()
    backend = LocalBackend(base_path=str(storage_dir))
    _manifest = Manifest(manifest_path=storage_dir / "manifest.json")

    all_chunks = []
    all_nodes = []
    all_edges = []

    for rel_path, content in SAMPLE_PROJECT.items():
        lang = "python" if rel_path.endswith(".py") else "markdown"
        chunks = chunker.chunk(content, file_path=rel_path, language=lang)

        file_node = GraphNode(
            id=f"file_{rel_path}", node_type=NodeType.FILE,
            name=Path(rel_path).name, file_path=rel_path,
        )
        all_nodes.append(file_node)
        for chunk in chunks:
            all_nodes.append(GraphNode(
                id=chunk.id, node_type=NodeType.FUNCTION,
                name=chunk.id, file_path=rel_path,
            ))
            all_edges.append(GraphEdge(
                source_id=file_node.id, target_id=chunk.id,
                edge_type=EdgeType.DEFINES,
            ))
        all_chunks.extend(chunks)

    embedder.embed(all_chunks)
    await backend.ingest(all_chunks, all_nodes, all_edges)

    # 2. Retrieve
    retriever = HybridRetriever(backend=backend, embedder=embedder)
    results = await retriever.retrieve("authentication login", top_k=5)
    assert len(results) > 0
    assert any("auth" in c.file_path or "login" in c.content.lower() for c in results)

    # 3. Compress
    compressor = Compressor()
    await compressor.compress(results, level="standard")
    for chunk in results:
        assert chunk.compressed_content is not None

    # 4. Bootstrap
    builder = BootstrapBuilder(max_tokens=5000)
    payload = builder.build(
        project_name="test-project",
        chunks=results,
        recent_commits=["feat: add auth service", "feat: add user service"],
    )
    assert "## Project: test-project" in payload
    assert "Recent Activity" in payload
    assert len(payload) > 100
