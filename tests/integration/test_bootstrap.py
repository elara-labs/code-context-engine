import pytest
from context_engine.models import Chunk, ChunkType
from context_engine.integration.bootstrap import BootstrapBuilder


@pytest.fixture
def builder():
    return BootstrapBuilder(max_tokens=10000)


def test_build_payload_structure(builder):
    chunks = [
        Chunk(id="c1", content="def main(): pass", chunk_type=ChunkType.FUNCTION,
              file_path="app.py", start_line=1, end_line=1, language="python",
              confidence_score=0.9, compressed_content="main(): entry point"),
    ]
    payload = builder.build(project_name="my-project", chunks=chunks,
                           recent_commits=["fix: resolve login bug", "feat: add user profile"])
    assert "## Project: my-project" in payload
    assert "### Architecture" in payload
    assert "### Recent Activity" in payload
    assert "main()" in payload


def test_build_respects_token_limit(builder):
    chunks = [
        Chunk(id=f"c{i}", content=f"def func_{i}(): pass" * 50,
              chunk_type=ChunkType.FUNCTION, file_path=f"file_{i}.py",
              start_line=1, end_line=1, language="python",
              confidence_score=0.9, compressed_content=f"func_{i}: does thing {i} " * 20)
        for i in range(100)
    ]
    payload = builder.build(project_name="big-project", chunks=chunks)
    estimated_tokens = len(payload) / 4
    assert estimated_tokens < 12000


def test_build_groups_by_confidence(builder):
    chunks = [
        Chunk(id="low", content="x", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              confidence_score=0.3, compressed_content="low relevance"),
        Chunk(id="high", content="y", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              confidence_score=0.95, compressed_content="high relevance"),
    ]
    payload = builder.build(project_name="test", chunks=chunks)
    high_pos = payload.find("high relevance")
    assert high_pos >= 0


def test_build_empty_project(builder):
    payload = builder.build(project_name="empty")
    assert "## Project: empty" in payload


def test_build_with_working_state(builder):
    working_state = [
        "Branch: feat/my-feature",
        "Modified (unstaged):",
        "  M src/main.py",
    ]
    payload = builder.build(
        project_name="test",
        working_state=working_state,
    )
    assert "### Working State" in payload
    assert "feat/my-feature" in payload
    assert "src/main.py" in payload


def test_build_with_active_decisions(builder):
    decisions = [
        "[decision] Use ONNX instead of PyTorch — smaller install",
        "[decision] Keep all 3 storage backends — needed for hybrid search",
    ]
    payload = builder.build(
        project_name="test",
        active_decisions=decisions,
    )
    assert "### Active Context" in payload
    assert "ONNX" in payload
    assert "3 storage backends" in payload


def test_build_full_init(builder):
    """Test the complete init payload with all sections populated."""
    chunks = [
        Chunk(id="c1", content="class Server: pass", chunk_type=ChunkType.CLASS,
              file_path="server.py", start_line=1, end_line=1, language="python",
              confidence_score=0.9, compressed_content="Server: main HTTP handler"),
    ]
    payload = builder.build(
        project_name="my-app",
        chunks=chunks,
        recent_commits=["abc1234 feat: add dashboard"],
        active_decisions=["[decision] use Flask for dashboard"],
        working_state=["Branch: main", "Ahead of remote by 2 commit(s)"],
    )
    assert "## Project: my-app" in payload
    assert "### Architecture" in payload
    assert "### Recent Activity" in payload
    assert "### Working State" in payload
    assert "### Active Context" in payload
    assert "abc1234" in payload
    assert "Flask" in payload
