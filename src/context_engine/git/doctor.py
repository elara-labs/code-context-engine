"""Worktree-aware repository diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from context_engine.git.diff import get_worktree_diff
from context_engine.git.repository import resolve_git_repository_context
from context_engine.git.storage import repository_storage_layout


def worktree_doctor_report(config: object, project_dir: Path) -> dict[str, Any]:
    """Return repository/worktree/storage diagnostics for current project."""
    context = resolve_git_repository_context(project_dir)
    if context is None:
        return {"git": {"available": False, "project_dir": str(project_dir.resolve())}}

    layout = repository_storage_layout(config, project_dir, context, migrate_legacy=False)
    diff = get_worktree_diff(
        context.worktree_root,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
    )
    return {
        "git": {"available": True},
        "repository": {
            "id": context.repository_id,
            "common_dir": str(context.git_common_dir),
        },
        "worktree": {
            "id": context.worktree_id,
            "root": str(context.worktree_root),
            "head_sha": context.head_sha,
            "base_sha": context.base_sha,
        },
        "storage": {
            "repository_root": str(layout.repository_root),
            "base_dir": str(layout.base_dir),
            "worktree_dir": str(layout.worktree_dir),
            "legacy_project_dir": str(layout.legacy_project_dir),
        },
        "overlay": {
            "modified": sorted(diff.modified),
            "added": sorted(diff.added),
            "deleted": sorted(diff.deleted),
            "renamed": dict(sorted(diff.renamed.items())),
            "base_sha": diff.base_sha,
            "head_sha": diff.head_sha,
            "modified_count": len(diff.modified),
            "added_count": len(diff.added),
            "deleted_count": len(diff.deleted),
        },
    }
