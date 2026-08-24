"""Tests for Git repository/worktree identity and diff modeling."""

from pathlib import Path
import subprocess

from context_engine.git import get_worktree_diff, resolve_git_repository_context
from context_engine.git.repository import resolve_base_sha


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    (path / "base.py").write_text("def base():\n    return 'base'\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "init")
    return path


def test_repository_and_worktree_identity_are_separate_for_linked_worktree(tmp_path):
    repo = init_repo(tmp_path / "repo")
    worktree = tmp_path / "repo-linked"
    git(repo, "worktree", "add", "-b", "feature", str(worktree))

    repo_context = resolve_git_repository_context(repo)
    worktree_context = resolve_git_repository_context(worktree)

    assert repo_context is not None
    assert worktree_context is not None
    assert repo_context.repository_id == worktree_context.repository_id
    assert repo_context.git_common_dir == worktree_context.git_common_dir
    assert repo_context.worktree_id != worktree_context.worktree_id
    assert repo_context.worktree_root == repo.resolve()
    assert worktree_context.worktree_root == worktree.resolve()


def test_repository_context_normalizes_symlinked_checkout(tmp_path):
    repo = init_repo(tmp_path / "repo")
    symlink = tmp_path / "repo-link"
    symlink.symlink_to(repo, target_is_directory=True)

    repo_context = resolve_git_repository_context(repo)
    symlink_context = resolve_git_repository_context(symlink)

    assert repo_context is not None
    assert symlink_context is not None
    assert repo_context == symlink_context


def test_repository_context_returns_none_outside_git(tmp_path):
    assert resolve_git_repository_context(tmp_path) is None


def test_base_sha_uses_unambiguous_main_merge_base(tmp_path):
    repo = init_repo(tmp_path / "repo")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature():\n    return 'feature'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")

    actual = resolve_base_sha(repo, fallback_base_refs=("master",))

    assert actual == base_sha


def test_base_sha_uses_remote_default_not_tracking_feature_branch(tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")

    repo = init_repo(tmp_path / "repo")
    git(repo, "branch", "-M", "main")
    main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    git(repo, "remote", "set-head", "origin", "main")

    git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature():\n    return 'pushed'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "pushed feature")
    git(repo, "push", "-u", "origin", "feature")
    (repo / "local.py").write_text("def local():\n    return 'unpushed'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unpushed feature")

    actual = resolve_base_sha(repo)

    assert actual == main_sha


def test_base_sha_falls_back_to_head_when_no_safe_base_exists(tmp_path):
    repo = init_repo(tmp_path / "repo")
    head_sha = git(repo, "rev-parse", "HEAD")

    actual = resolve_base_sha(repo, fallback_base_refs=())

    assert actual == head_sha


def test_base_sha_falls_back_to_head_when_fallback_refs_disagree(tmp_path):
    repo = init_repo(tmp_path / "repo")
    git(repo, "checkout", "-b", "main")
    (repo / "main.py").write_text("def main():\n    return True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "main")
    git(repo, "checkout", "-b", "feature")
    head_sha = git(repo, "rev-parse", "HEAD")

    actual = resolve_base_sha(repo, fallback_base_refs=("main", "master"))

    assert actual == head_sha


def test_worktree_diff_collects_committed_dirty_untracked_deleted_and_renamed(tmp_path):
    repo = init_repo(tmp_path / "repo")
    base_sha = git(repo, "rev-parse", "HEAD")

    (repo / "committed.py").write_text("def committed():\n    return True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "committed")
    head_sha = git(repo, "rev-parse", "HEAD")

    (repo / "base.py").write_text("def base():\n    return 'dirty'\n")
    (repo / "staged.py").write_text("def staged():\n    return True\n")
    git(repo, "add", "staged.py")
    (repo / "untracked.py").write_text("def untracked():\n    return True\n")
    git(repo, "mv", "committed.py", "renamed.py")

    diff = get_worktree_diff(repo, base_sha=base_sha, head_sha=head_sha)

    assert diff.base_sha == base_sha
    assert diff.head_sha == head_sha
    assert "base.py" in diff.modified
    assert "staged.py" in diff.added
    assert "untracked.py" in diff.added
    assert "committed.py" in diff.deleted
    assert "renamed.py" in diff.added
    assert diff.renamed == {"committed.py": "renamed.py"}
