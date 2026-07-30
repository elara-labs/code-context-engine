"""Tests for git history indexer."""
import os
import subprocess
from datetime import datetime
from unittest.mock import patch

import pytest
from context_engine.indexer.git_indexer import index_commits
from context_engine.models import ChunkType, NodeType, EdgeType


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with 3 commits."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=True)
    for i in range(3):
        (tmp_path / f"file{i}.py").write_text(f"def fn{i}(): pass\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", f"Add file{i}"],
            cwd=tmp_path, capture_output=True, check=True,
        )
    return tmp_path


@pytest.mark.asyncio
async def test_index_commits_returns_chunks(git_repo):
    chunks, nodes, edges = await index_commits(git_repo, max_commits=10)
    assert len(chunks) == 3
    assert all(c.chunk_type == ChunkType.COMMIT for c in chunks)


@pytest.mark.asyncio
async def test_commit_chunks_have_metadata(git_repo):
    chunks, _, _ = await index_commits(git_repo, max_commits=10)
    for chunk in chunks:
        assert "author" in chunk.metadata
        assert "hash" in chunk.metadata
        assert chunk.file_path.startswith("git:")


@pytest.mark.asyncio
async def test_commit_nodes_and_edges(git_repo):
    chunks, nodes, edges = await index_commits(git_repo, max_commits=10)
    assert len(nodes) >= 3
    assert all(n.node_type == NodeType.COMMIT for n in nodes)
    assert len(edges) > 0
    assert all(e.edge_type == EdgeType.MODIFIES for e in edges)


@pytest.mark.asyncio
async def test_commit_chunks_carry_commit_time_modified_ts(git_repo):
    """Commit chunks must stamp modified_ts (epoch float of the author date)
    so ConfidenceScorer's recency weight applies to git history, not just
    file chunks."""
    fixed_date = "2026-07-03T12:00:00+01:00"
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": fixed_date,
        "GIT_COMMITTER_DATE": fixed_date,
    }
    (git_repo / "dated.py").write_text("def dated(): pass\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Dated commit"],
        cwd=git_repo, capture_output=True, check=True, env=env,
    )

    chunks, _, _ = await index_commits(git_repo, max_commits=10)

    dated = next(c for c in chunks if c.content.startswith("Dated commit"))
    expected = datetime.fromisoformat(fixed_date).timestamp()
    assert isinstance(dated.metadata.get("modified_ts"), float), (
        f"expected modified_ts float, got metadata={dated.metadata}"
    )
    assert dated.metadata["modified_ts"] == pytest.approx(expected)

    # Every commit chunk carries the stamp, not just the fixed-date one.
    for c in chunks:
        assert isinstance(c.metadata.get("modified_ts"), float), (
            f"chunk {c.id} missing modified_ts; got metadata={c.metadata}"
        )


@pytest.mark.asyncio
async def test_incremental_since_sha(git_repo):
    chunks_all, _, _ = await index_commits(git_repo, max_commits=10)
    first_sha = chunks_all[-1].metadata["hash"]  # oldest commit
    chunks_new, _, _ = await index_commits(git_repo, since_sha=first_sha)
    assert len(chunks_new) < len(chunks_all)


@pytest.mark.asyncio
async def test_non_ascii_commit_metadata(tmp_path):
    """Non-ASCII author names and commit messages must not crash indexing,
    even when the system locale prefers a non-UTF-8 encoding (#140)."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "田中太郎"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "tanaka@example.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    (tmp_path / "hello.py").write_text("print('こんにちは')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "機能追加: 挨拶モジュール"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    calls: list[dict] = []
    real_run = subprocess.run

    def spy_run(*args, **kwargs):
        calls.append(kwargs)
        return real_run(*args, **kwargs)

    with patch("subprocess.run", side_effect=spy_run):
        chunks, _, _ = await index_commits(tmp_path, max_commits=10)

    assert len(chunks) == 1
    assert "田中太郎" in chunks[0].metadata["author"]
    assert "機能追加" in chunks[0].content

    # index_commits must pass encoding/errors so non-ASCII never crashes
    git_log_calls = [c for c in calls if c.get("encoding") is not None]
    assert git_log_calls, "index_commits should pass encoding kwarg to subprocess.run"
    for c in git_log_calls:
        assert c["encoding"] == "utf-8"
        assert c["errors"] == "replace"
