import os
import stat
import pytest
from pathlib import Path
from context_engine.indexer.git_hooks import install_hooks


@pytest.fixture
def git_repo(tmp_path):
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    return tmp_path


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
