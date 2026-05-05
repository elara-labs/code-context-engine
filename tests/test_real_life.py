"""Real-life integration tests — simulate actual user workflows.

These tests reproduce the exact scenarios that caused production bugs:
- Non-git project setup
- HTML/non-standard file indexing
- Vector search with tuple embeddings
- Full init→index→search→savings pipeline
- Empty project handling
- Large file handling
"""
import json
import os
import subprocess
import pytest

from context_engine.config import load_config
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.pipeline import run_indexing
from context_engine.storage.local_backend import LocalBackend
from context_engine.storage.vector_store import VectorStore, _to_list
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.compression.compressor import Compressor
from context_engine.integration.mcp_server import ContextEngineMCP
from context_engine.integration.bootstrap import BootstrapBuilder
from context_engine.models import Chunk, ChunkType


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def embedder():
    return Embedder()


@pytest.fixture
def non_git_project(tmp_path):
    """A project directory that is NOT a git repo."""
    (tmp_path / "index.html").write_text(
        "<html><body><h1>Hello World</h1><script>console.log('hi');</script></body></html>"
    )
    (tmp_path / "style.css").write_text("body { color: red; }")
    (tmp_path / "app.py").write_text("def main():\n    print('hello')\n")
    return tmp_path


@pytest.fixture
def git_project(tmp_path):
    """A project with a proper git repo."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "main.py").write_text(
        "def greet(name):\n    return f'Hello, {name}!'\n\n"
        "class UserService:\n    def __init__(self):\n        self.users = []\n"
    )
    (tmp_path / "utils.py").write_text(
        "import os\nimport json\n\ndef load_config(path):\n    with open(path) as f:\n        return json.load(f)\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, capture_output=True)
    return tmp_path


# ── _to_list safety ────────────────────────────────────────────────────

class TestToList:
    """The tuple→list bug that broke all search for weeks."""

    def test_tuple_to_list(self):
        result = _to_list((1.0, 2.0, 3.0))
        assert isinstance(result, list)
        assert result == [1.0, 2.0, 3.0]

    def test_list_passthrough(self):
        original = [1.0, 2.0, 3.0]
        result = _to_list(original)
        assert result is original

    def test_numpy_array(self):
        import numpy as np
        arr = np.array([1.0, 2.0, 3.0])
        result = _to_list(arr)
        assert isinstance(result, list)


# ── Embedder returns tuple ─────────────────────────────────────────────

class TestEmbedderType:
    """embed_query returns tuple for LRU cache; verify downstream handles it."""

    def test_embed_query_returns_tuple(self, embedder):
        result = embedder.embed_query("hello world")
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 384

    def test_embed_chunks_sets_list(self, embedder):
        chunk = Chunk(
            id="test", content="def hello(): pass",
            chunk_type=ChunkType.FUNCTION, file_path="test.py",
            start_line=1, end_line=1, language="python",
        )
        embedder.embed([chunk])
        assert isinstance(chunk.embedding, list), f"Expected list, got {type(chunk.embedding)}"


# ── Vector store search with tuple embedding ───────────────────────────

class TestVectorStoreTupleSearch:
    """Reproduce the exact bug: search with tuple embedding must not fail."""

    @pytest.mark.asyncio
    async def test_search_with_tuple_embedding(self, embedder, tmp_path):
        store = VectorStore(db_path=str(tmp_path / "vectors"))
        chunk = Chunk(
            id="c1", content="Hello world function",
            chunk_type=ChunkType.FUNCTION, file_path="test.py",
            start_line=1, end_line=1, language="python",
        )
        embedder.embed([chunk])
        await store.ingest([chunk])

        # embed_query returns tuple — search must handle it
        query_vec = embedder.embed_query("hello world")
        assert isinstance(query_vec, tuple)

        results = await store.search(query_vec, top_k=5)
        assert len(results) > 0, "Search with tuple embedding returned 0 results"
        assert results[0].id == "c1"

    @pytest.mark.asyncio
    async def test_search_with_list_embedding(self, embedder, tmp_path):
        store = VectorStore(db_path=str(tmp_path / "vectors"))
        chunk = Chunk(
            id="c1", content="Hello world function",
            chunk_type=ChunkType.FUNCTION, file_path="test.py",
            start_line=1, end_line=1, language="python",
        )
        embedder.embed([chunk])
        await store.ingest([chunk])

        query_vec = list(embedder.embed_query("hello world"))
        results = await store.search(query_vec, top_k=5)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_search_empty_store(self, embedder, tmp_path):
        store = VectorStore(db_path=str(tmp_path / "vectors"))
        query_vec = embedder.embed_query("anything")
        results = await store.search(query_vec, top_k=5)
        assert results == []


# ── Full pipeline: init → index → search → savings ────────────────────

class TestFullPipeline:
    """End-to-end test simulating what a real user does."""

    @pytest.mark.asyncio
    async def test_non_git_project_full_pipeline(self, non_git_project, tmp_path, embedder):
        """Non-git project must index, search, and return results."""
        storage_base = tmp_path / "cce_storage"
        storage_base.mkdir()
        config = load_config()
        config.storage_path = str(storage_base)

        # Index — pipeline creates storage_path/project_name internally
        result = await run_indexing(config, str(non_git_project), full=True)
        assert result.total_chunks > 0, f"Expected chunks, got {result.total_chunks}"
        assert len(result.errors) == 0, f"Indexing errors: {result.errors}"

        # Search via retriever — use same path the pipeline wrote to
        project_storage = storage_base / non_git_project.name
        backend = LocalBackend(base_path=str(project_storage))
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        chunks = await retriever.retrieve("hello world", top_k=5)
        assert len(chunks) > 0, "Retriever returned 0 results for non-git project"

    @pytest.mark.asyncio
    async def test_git_project_full_pipeline(self, git_project, tmp_path, embedder):
        """Git project must index code + history and return results."""
        storage_base = tmp_path / "cce_storage"
        storage_base.mkdir()
        config = load_config()
        config.storage_path = str(storage_base)

        result = await run_indexing(config, str(git_project), full=True)
        assert result.total_chunks > 0
        assert len(result.errors) == 0

        project_storage = storage_base / git_project.name
        backend = LocalBackend(base_path=str(project_storage))
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        chunks = await retriever.retrieve("greet function", top_k=5)
        assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_html_file_is_indexed(self, non_git_project, tmp_path):
        """HTML files must be indexed — not skipped."""
        config = load_config()
        config.storage_path = str(tmp_path / "s")

        result = await run_indexing(config, str(non_git_project), full=True)
        assert "index.html" in result.indexed_files

    @pytest.mark.asyncio
    async def test_css_file_is_indexed(self, non_git_project, tmp_path):
        """CSS files must be indexed."""
        config = load_config()
        config.storage_path = str(tmp_path / "s")

        result = await run_indexing(config, str(non_git_project), full=True)
        assert "style.css" in result.indexed_files

    @pytest.mark.asyncio
    async def test_binary_file_is_skipped(self, non_git_project, tmp_path):
        """Binary files must not be indexed."""
        (non_git_project / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        config = load_config()
        config.storage_path = str(tmp_path / "s")

        result = await run_indexing(config, str(non_git_project), full=True)
        assert "image.png" not in result.indexed_files

    @pytest.mark.asyncio
    async def test_empty_project(self, tmp_path):
        """Empty project must not crash."""
        empty = tmp_path / "empty"
        empty.mkdir()
        config = load_config()
        config.storage_path = str(tmp_path / "s")

        result = await run_indexing(config, str(empty), full=True)
        assert result.total_chunks == 0
        assert len(result.errors) == 0


# ── MCP Server simulation ─────────────────────────────────────────────

class TestMCPSimulation:
    """Simulate what Claude Code does when calling context_search."""

    @pytest.mark.asyncio
    async def test_context_search_returns_results(self, git_project, tmp_path, embedder):
        storage_base = tmp_path / "cce_storage"
        storage_base.mkdir()
        config = load_config()
        config.storage_path = str(storage_base)

        await run_indexing(config, str(git_project), full=True)

        project_storage = storage_base / git_project.name
        backend = LocalBackend(base_path=str(project_storage))
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        compressor = Compressor()

        old_cwd = os.getcwd()
        os.chdir(git_project)
        try:
            mcp = ContextEngineMCP(
                retriever=retriever, backend=backend,
                compressor=compressor, embedder=embedder, config=config,
            )

            result = await mcp._handle_context_search({"query": "greet function", "top_k": 5})
            assert len(result) > 0
            text = result[0].text
            assert "No results found" not in text, "context_search returned no results"
        finally:
            os.chdir(old_cwd)

    @pytest.mark.asyncio
    async def test_context_search_updates_stats(self, git_project, tmp_path, embedder):
        storage_base = tmp_path / "cce_storage"
        storage_base.mkdir()
        config = load_config()
        config.storage_path = str(storage_base)

        await run_indexing(config, str(git_project), full=True)

        project_storage = storage_base / git_project.name
        backend = LocalBackend(base_path=str(project_storage))
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        compressor = Compressor()

        old_cwd = os.getcwd()
        os.chdir(git_project)
        try:
            mcp = ContextEngineMCP(
                retriever=retriever, backend=backend,
                compressor=compressor, embedder=embedder, config=config,
            )

            await mcp._handle_context_search({"query": "greet", "top_k": 5})

            stats_path = project_storage / "stats.json"
            assert stats_path.exists(), "stats.json not created"
            stats = json.loads(stats_path.read_text())
            assert stats["queries"] >= 1, f"Expected queries >= 1, got {stats['queries']}"
            assert stats["served_tokens"] > 0, f"Expected served_tokens > 0, got {stats['served_tokens']}"
        finally:
            os.chdir(old_cwd)


# ── Graph expansion end-to-end ─────────────────────────────────────────

class TestGraphExpansion:
    """Test that retriever follows CALLS/IMPORTS edges to find related code."""

    @pytest.fixture
    def linked_project(self, tmp_path):
        """Project with two files where one imports the other."""
        proj = tmp_path / "linked"
        proj.mkdir()
        (proj / "auth.py").write_text(
            "from utils import hash_password\n\n"
            "def login(username, password):\n"
            "    hashed = hash_password(password)\n"
            "    return check_db(username, hashed)\n"
        )
        (proj / "utils.py").write_text(
            "import hashlib\n\n"
            "def hash_password(password: str) -> str:\n"
            "    return hashlib.sha256(password.encode()).hexdigest()\n"
        )
        return proj

    @pytest.mark.asyncio
    async def test_graph_expansion_pulls_related_file(self, linked_project, tmp_path, embedder):
        """Searching for 'login' should also surface utils.py via import edge."""
        storage_base = tmp_path / "storage"
        storage_base.mkdir()
        config = load_config()
        config.storage_path = str(storage_base)

        await run_indexing(config, str(linked_project), full=True)

        project_storage = storage_base / linked_project.name
        backend = LocalBackend(base_path=str(project_storage))
        retriever = HybridRetriever(backend=backend, embedder=embedder)

        chunks = await retriever.retrieve("login authentication", top_k=10)
        file_paths = {c.file_path for c in chunks}
        # auth.py should be a direct hit
        assert "auth.py" in file_paths, f"auth.py not found in {file_paths}"
        # utils.py should appear via graph expansion (auth.py imports it)
        # or via vector similarity — either way it must be discoverable
        assert "utils.py" in file_paths, f"utils.py not found in {file_paths} — graph expansion may be broken"


# ── Bootstrap builder ──────────────────────────────────────────────────

class TestBootstrapRealLife:
    def test_build_with_no_data(self):
        builder = BootstrapBuilder()
        result = builder.build(project_name="empty-project")
        assert "## Project: empty-project" in result

    def test_build_with_all_sections(self):
        builder = BootstrapBuilder()
        chunks = [
            Chunk(id="c1", content="def main(): pass",
                  chunk_type=ChunkType.FUNCTION, file_path="main.py",
                  start_line=1, end_line=1, language="python",
                  confidence_score=0.9, compressed_content="main entry point"),
        ]
        result = builder.build(
            project_name="test",
            chunks=chunks,
            recent_commits=["abc1234 feat: initial"],
            active_decisions=["Use ONNX for embeddings"],
            working_state=["Branch: main", "No uncommitted changes"],
        )
        assert "### Architecture" in result
        assert "### Recent Activity" in result
        assert "### Working State" in result
        assert "### Active Context" in result
