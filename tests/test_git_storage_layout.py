"""Tests for repository-scoped CCE storage layout."""

from pathlib import Path
from types import SimpleNamespace

from context_engine.git import repository_storage_layout, resolve_git_repository_context

from tests.test_git_repository_context import git, init_repo


def config(storage_root: Path) -> SimpleNamespace:
    return SimpleNamespace(storage_path=str(storage_root))


def test_repository_storage_layout_splits_shared_base_from_worktree_overlay(tmp_path):
    repo = init_repo(tmp_path / "repo")
    worktree = tmp_path / "repo-linked"
    git(repo, "worktree", "add", "-b", "feature", str(worktree))

    repo_context = resolve_git_repository_context(repo)
    worktree_context = resolve_git_repository_context(worktree)
    assert repo_context is not None
    assert worktree_context is not None

    cfg = config(tmp_path / ".cce" / "projects")
    repo_layout = repository_storage_layout(cfg, repo, repo_context)
    worktree_layout = repository_storage_layout(cfg, worktree, worktree_context)

    assert repo_layout.repository_root == worktree_layout.repository_root
    assert repo_layout.repository_root == tmp_path / ".cce" / "repos" / repo_context.repository_id
    assert repo_layout.base_dir == worktree_layout.base_dir
    assert repo_layout.worktree_dir != worktree_layout.worktree_dir
    assert repo_layout.worktree_dir.parent == worktree_layout.worktree_dir.parent


def test_repository_storage_layout_preserves_legacy_project_storage_reference(tmp_path):
    repo = init_repo(tmp_path / "repo")
    repo_context = resolve_git_repository_context(repo)
    assert repo_context is not None

    cfg = config(tmp_path / ".cce" / "projects")
    legacy = tmp_path / ".cce" / "projects" / "repo"
    legacy.mkdir(parents=True)
    (legacy / "marker.txt").write_text("legacy")

    layout = repository_storage_layout(cfg, repo, repo_context)

    assert layout.legacy_project_dir.exists()
    assert (layout.legacy_project_dir / "marker.txt").read_text() == "legacy"
    assert layout.base_dir == tmp_path / ".cce" / "repos" / repo_context.repository_id / "base"


def test_repository_storage_layout_can_resolve_without_migrating_legacy_storage(tmp_path):
    repo = init_repo(tmp_path / "repo")
    repo_context = resolve_git_repository_context(repo)
    assert repo_context is not None

    cfg = config(tmp_path / ".cce" / "projects")
    legacy = tmp_path / ".cce" / "projects" / "repo"
    legacy.mkdir(parents=True)

    layout = repository_storage_layout(cfg, repo, repo_context, migrate_legacy=False)

    assert legacy.exists()
    assert layout.legacy_project_dir.name.startswith("repo-")
    assert not layout.legacy_project_dir.exists()
