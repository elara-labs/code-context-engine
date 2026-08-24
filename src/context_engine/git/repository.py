"""Git repository identity and base-revision resolution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import subprocess
from pathlib import Path


GIT_TIMEOUT_SECONDS = 5
DEFAULT_BASE_REFS = ("origin/main", "origin/master", "main", "master")


@dataclass(frozen=True)
class GitRepositoryContext:
    repository_id: str
    git_common_dir: Path
    worktree_id: str
    worktree_root: Path
    head_sha: str
    base_sha: str | None


def resolve_git_repository_context(
    project_dir: Path,
    *,
    base_ref: str | None = None,
    fallback_base_refs: tuple[str, ...] = DEFAULT_BASE_REFS,
) -> GitRepositoryContext | None:
    """Return Git repository/worktree identity, or None outside a usable Git repo."""
    worktree_root = _git_path(project_dir, ["rev-parse", "--show-toplevel"])
    git_common_dir = _git_path(project_dir, ["rev-parse", "--git-common-dir"])
    head_sha = _git(project_dir, ["rev-parse", "--verify", "HEAD"])
    if worktree_root is None or git_common_dir is None or head_sha is None:
        return None

    return GitRepositoryContext(
        repository_id=_path_id(git_common_dir),
        git_common_dir=git_common_dir,
        worktree_id=_path_id(worktree_root),
        worktree_root=worktree_root,
        head_sha=head_sha,
        base_sha=resolve_base_sha(
            worktree_root,
            head_sha=head_sha,
            base_ref=base_ref,
            fallback_base_refs=fallback_base_refs,
        ),
    )


def resolve_base_sha(
    worktree_root: Path,
    *,
    head_sha: str | None = None,
    base_ref: str | None = None,
    fallback_base_refs: tuple[str, ...] = DEFAULT_BASE_REFS,
) -> str | None:
    """Resolve the safest base SHA for a worktree diff."""
    head_sha = head_sha or _git(worktree_root, ["rev-parse", "--verify", "HEAD"])
    if head_sha is None:
        return None

    if base_ref:
        return _merge_base(worktree_root, base_ref)

    remote_default_ref = _remote_default_ref(worktree_root)
    if remote_default_ref:
        remote_default_base = _merge_base(worktree_root, remote_default_ref)
        if remote_default_base:
            return remote_default_base

    fallback_ref = _unambiguous_base_ref(worktree_root, fallback_base_refs)
    if fallback_ref:
        fallback_base = _merge_base(worktree_root, fallback_ref)
        if fallback_base:
            return fallback_base

    return head_sha


def _merge_base(worktree_root: Path, ref: str) -> str | None:
    if _git(worktree_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"]) is None:
        return None
    return _git(worktree_root, ["merge-base", "HEAD", ref])


def _unambiguous_base_ref(worktree_root: Path, refs: tuple[str, ...]) -> str | None:
    found: dict[str, str] = {}
    for ref in refs:
        sha = _git(worktree_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
        if sha:
            found[ref] = sha
    if not found:
        return None
    if len(set(found.values())) != 1:
        return None
    return next(iter(found))


def _remote_default_ref(worktree_root: Path) -> str | None:
    return _git(worktree_root, ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])


def _git_path(cwd: Path, args: list[str]) -> Path | None:
    value = _git(cwd, args)
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _git(cwd: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _path_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
