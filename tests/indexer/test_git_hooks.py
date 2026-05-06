import os
import shutil
import stat
import subprocess

import pytest

from context_engine.indexer.git_hooks import install_hooks, uninstall_hooks


def _git(*args: str, cwd: str) -> str:
    """Run git with config that doesn't require a real user/email — keeps the
    tests usable on a fresh CI box where the user hasn't set git config."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True,
        capture_output=True, text=True,
    ).stdout


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(),
    reason="git binary not available — hook resolution requires real git",
)


@pytest.fixture
def git_repo(tmp_path):
    """A real git repo at tmp_path. Avoids the previous fake-`.git/hooks/` dir
    pattern, which silently masked the install logic — without `git init`,
    `git rev-parse --git-path hooks` correctly refuses to claim the directory
    is a repo and tests would not exercise the production path."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _git("init", "-b", "main", cwd=str(proj))
    return proj


@pytest.fixture
def worktree(git_repo, tmp_path):
    """A worktree of `git_repo` at tmp_path/wt-feat. Returns the worktree dir.

    Worktrees share their hooks directory with the main repo's `.git/hooks/`
    via the gitfile pointer in the worktree's `.git` (which is a *file*, not
    a directory). Pinning install-from-worktree behavior here matches the
    real-world setup that surfaced issue #48.
    """
    (git_repo / "file.txt").write_text("hi\n")
    _git("add", "file.txt", cwd=str(git_repo))
    _git("commit", "-m", "init", cwd=str(git_repo))
    wt = tmp_path / "wt-feat"
    _git("worktree", "add", "-b", "feat", str(wt), cwd=str(git_repo))
    return wt


def test_install_hooks_creates_post_commit(git_repo):
    install_hooks(project_dir=str(git_repo))
    hook_path = git_repo / ".git" / "hooks" / "post-commit"
    assert hook_path.exists()
    assert os.access(hook_path, os.X_OK)
    content = hook_path.read_text()
    assert "cce hook" in content


def test_install_hooks_creates_post_checkout(git_repo):
    install_hooks(project_dir=str(git_repo))
    hook_path = git_repo / ".git" / "hooks" / "post-checkout"
    assert hook_path.exists()


def test_install_hooks_creates_post_merge(git_repo):
    install_hooks(project_dir=str(git_repo))
    hook_path = git_repo / ".git" / "hooks" / "post-merge"
    assert hook_path.exists()


def test_install_hooks_preserves_existing(git_repo):
    existing_hook = git_repo / ".git" / "hooks" / "post-commit"
    existing_hook.parent.mkdir(parents=True, exist_ok=True)
    existing_hook.write_text("#!/bin/sh\necho 'existing'\n")
    existing_hook.chmod(existing_hook.stat().st_mode | stat.S_IEXEC)
    install_hooks(project_dir=str(git_repo))
    content = existing_hook.read_text()
    assert "existing" in content
    assert "cce hook" in content


def test_install_hooks_returns_empty_for_non_git(tmp_path):
    """Non-git directory should return empty list, not raise."""
    result = install_hooks(project_dir=str(tmp_path))
    assert result == []


def test_install_hooks_inside_worktree_writes_to_shared_hooks_dir(worktree, git_repo):
    """Regression for issue #48: installing from inside a worktree previously
    silently no-op'd because `<worktree>/.git` is a file, not a directory.
    Hooks must land in the shared main-repo hooks dir, where they fire for
    every worktree."""
    installed = install_hooks(project_dir=str(worktree))
    assert installed, "install returned [] — worktree path was not resolved"
    shared_hook = git_repo / ".git" / "hooks" / "post-commit"
    assert shared_hook.exists(), (
        f"expected shared hook at {shared_hook}, got installed={installed}"
    )
    assert "cce hook" in shared_hook.read_text()
    # The per-worktree gitdir does not get its own hooks/ — that's by design,
    # git's behavior puts shared hooks in the main repo.
    per_worktree = git_repo / ".git" / "worktrees" / "wt-feat" / "hooks"
    if per_worktree.exists():
        assert not (per_worktree / "post-commit").exists()


def test_uninstall_hooks_removes_only_cce_hooks(git_repo):
    """Uninstall scrubs CCE-installed hooks but keeps unrelated ones."""
    install_hooks(project_dir=str(git_repo))
    foreign = git_repo / ".git" / "hooks" / "pre-commit"  # not in HOOK_NAMES
    foreign.write_text("#!/bin/sh\necho lint\n")
    foreign.chmod(foreign.stat().st_mode | stat.S_IEXEC)

    removed = uninstall_hooks(project_dir=str(git_repo))
    assert removed == 3
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()
    # Foreign hook untouched.
    assert foreign.exists()


def test_uninstall_hooks_from_worktree_removes_shared_hooks(worktree, git_repo):
    """Symmetry with install: uninstall run from a worktree must remove the
    hooks the install path put in the shared dir, otherwise the user is left
    with no way to clean up without cd'ing into the main checkout."""
    install_hooks(project_dir=str(worktree))
    shared_hook = git_repo / ".git" / "hooks" / "post-commit"
    assert shared_hook.exists()

    removed = uninstall_hooks(project_dir=str(worktree))
    assert removed == 3
    assert not shared_hook.exists()


def test_uninstall_hooks_skips_non_cce_content(git_repo):
    """A hand-rolled `post-commit` that has no CCE markers must be left alone.
    Detection is content-based (presence of \"cce\" or \"context-engine\"), so
    a user's pre-existing hook with neither token is preserved."""
    other = git_repo / ".git" / "hooks" / "post-commit"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("#!/bin/sh\necho 'unrelated'\n")
    other.chmod(other.stat().st_mode | stat.S_IEXEC)
    removed = uninstall_hooks(project_dir=str(git_repo))
    assert removed == 0
    assert other.exists()


def test_uninstall_hooks_returns_zero_for_non_git(tmp_path):
    assert uninstall_hooks(project_dir=str(tmp_path)) == 0
