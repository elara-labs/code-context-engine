import os
import stat
import pytest
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


# ─── Issue #67 regression coverage ──────────────────────────────────────


def test_post_commit_hook_does_not_use_removed_flag(git_repo):
    """`cce index --changed-only` flag was removed but the template still
    referenced it, so every commit silently errored. The hook must invoke
    bare `cce index` (which already does incremental indexing)."""
    install_hooks(project_dir=str(git_repo))
    for name in ("post-commit", "post-checkout", "post-merge"):
        content = (git_repo / ".git" / "hooks" / name).read_text()
        assert "--changed-only" not in content, (
            f"{name} still references the removed --changed-only flag"
        )
        # Sanity: the hook still triggers an index.
        assert " index " in content or "index>" in content


def test_post_commit_hook_quotes_bin_path_with_spaces(tmp_path, monkeypatch):
    """A resolved cce binary path with spaces (e.g. Alice Smith) must be
    shell-quoted in the generated hook so it isn't tokenised by sh
    (Copilot review on #70)."""
    from context_engine.indexer import git_hooks

    spaced = tmp_path / "Alice Smith" / "bin" / "cce"
    spaced.parent.mkdir(parents=True)
    spaced.write_text("#!/bin/sh\necho cce\n")
    monkeypatch.setattr(
        git_hooks, "_resolve_cce_binary", lambda: str(spaced),
    )

    git_repo = tmp_path / "repo"
    (git_repo / ".git" / "hooks").mkdir(parents=True)
    git_hooks.install_hooks(project_dir=str(git_repo))

    body = (git_repo / ".git" / "hooks" / "post-commit").read_text()
    # The path must be quoted as one shell token. shlex.quote single-
    # quotes anything containing whitespace, so the literal `Alice Smith`
    # must live inside `'...'`. Confirm a `'` appears before any space
    # in the path.
    assert "'" in body, f"bin_path should be shell-quoted: {body!r}"
    assert "Alice Smith" in body
    quoted_idx = body.index("'")
    space_in_path = body.index("Alice Smith") + len("Alice")
    assert quoted_idx < space_in_path
