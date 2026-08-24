"""Tests for worktree overlay benchmark helper."""

from click.testing import CliRunner

from context_engine.cli import main
from context_engine.git import benchmark_worktree_overlay
from tests.test_git_repository_context import git, init_repo


def test_worktree_benchmark_counts_only_diff_files(tmp_path):
    repo = init_repo(tmp_path / "repo")
    for index in range(20):
        (repo / f"stable_{index}.py").write_text(f"VALUE = {index}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "stable files")

    (repo / "base.py").write_text("def base():\n    return 'changed'\n")
    (repo / "new.py").write_text("def new():\n    return True\n")

    result = benchmark_worktree_overlay(repo)

    assert result is not None
    assert result.changed_file_count == 2
    assert result.modified_count == 1
    assert result.added_count == 1


def test_worktree_benchmark_returns_none_outside_git(tmp_path):
    assert benchmark_worktree_overlay(tmp_path) is None


def test_worktree_benchmark_cli_outputs_json(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(main, ["worktree-benchmark", "--json"])

    assert result.exit_code == 0
    assert '"changed_file_count"' in result.output
