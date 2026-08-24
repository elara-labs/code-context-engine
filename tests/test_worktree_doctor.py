"""Tests for worktree-aware doctor diagnostics."""

from types import SimpleNamespace

from click.testing import CliRunner

from context_engine.cli import main
from context_engine.git import worktree_doctor_report
from tests.test_git_repository_context import git, init_repo


def config(tmp_path):
    return SimpleNamespace(storage_path=str(tmp_path / ".cce" / "projects"))


def test_worktree_doctor_report_includes_repo_storage_and_overlay_counts(tmp_path):
    repo = init_repo(tmp_path / "repo")
    (repo / "base.py").write_text("def base():\n    return 'changed'\n")
    (repo / "new.py").write_text("def new():\n    return True\n")

    report = worktree_doctor_report(config(tmp_path), repo)

    assert report["git"]["available"] is True
    assert report["repository"]["common_dir"].endswith(".git")
    assert report["worktree"]["root"] == str(repo.resolve())
    assert report["storage"]["base_dir"].endswith("/base")
    assert report["storage"]["worktree_dir"].find("/worktrees/") >= 0
    assert report["overlay"]["modified_count"] == 1
    assert report["overlay"]["added_count"] == 1


def test_worktree_doctor_report_does_not_migrate_legacy_project_storage(tmp_path):
    repo = init_repo(tmp_path / "repo")
    legacy = tmp_path / ".cce" / "projects" / "repo"
    legacy.mkdir(parents=True)
    (legacy / "marker.txt").write_text("legacy")

    report = worktree_doctor_report(config(tmp_path), repo)

    assert legacy.exists()
    assert (legacy / "marker.txt").read_text() == "legacy"
    assert report["storage"]["legacy_project_dir"] != str(legacy)


def test_worktree_doctor_report_handles_non_git_directory(tmp_path):
    report = worktree_doctor_report(config(tmp_path), tmp_path)

    assert report == {"git": {"available": False, "project_dir": str(tmp_path.resolve())}}


def test_doctor_json_command_outputs_report(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    git(repo, "status")
    monkeypatch.chdir(repo)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--json"])

    assert result.exit_code == 0
    assert '"repository"' in result.output
    assert '"worktree"' in result.output
