"""Git repository and worktree context helpers."""

from context_engine.git.benchmark import WorktreeBenchmarkResult, benchmark_worktree_overlay
from context_engine.git.diff import GitWorktreeDiff, get_worktree_diff
from context_engine.git.doctor import worktree_doctor_report
from context_engine.git.repository import GitRepositoryContext, resolve_git_repository_context
from context_engine.git.storage import RepositoryStorageLayout, repository_storage_layout

__all__ = [
    "GitRepositoryContext",
    "GitWorktreeDiff",
    "RepositoryStorageLayout",
    "WorktreeBenchmarkResult",
    "benchmark_worktree_overlay",
    "get_worktree_diff",
    "repository_storage_layout",
    "resolve_git_repository_context",
    "worktree_doctor_report",
]
