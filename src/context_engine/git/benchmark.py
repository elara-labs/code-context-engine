"""Worktree overlay benchmark helpers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import time
from typing import Any

from context_engine.git.diff import get_worktree_diff
from context_engine.git.repository import resolve_git_repository_context


@dataclass(frozen=True)
class WorktreeBenchmarkResult:
    repository_id: str
    worktree_id: str
    base_sha: str | None
    head_sha: str
    changed_file_count: int
    modified_count: int
    added_count: int
    deleted_count: int
    renamed_count: int
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def benchmark_worktree_overlay(project_dir: Path) -> WorktreeBenchmarkResult | None:
    """Measure O(diff) overlay discovery work for one worktree."""
    context = resolve_git_repository_context(project_dir)
    if context is None:
        return None

    start = time.perf_counter()
    diff = get_worktree_diff(
        context.worktree_root,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    changed = diff.modified | diff.added | diff.deleted | set(diff.renamed)
    return WorktreeBenchmarkResult(
        repository_id=context.repository_id,
        worktree_id=context.worktree_id,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        changed_file_count=len(changed),
        modified_count=len(diff.modified),
        added_count=len(diff.added),
        deleted_count=len(diff.deleted),
        renamed_count=len(diff.renamed),
        elapsed_ms=elapsed_ms,
    )
