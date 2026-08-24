"""Repository-scoped storage layout for shared base plus worktree overlays."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from context_engine.git.repository import GitRepositoryContext
from context_engine.utils import project_storage_dir, resolve_project_storage_dir


@dataclass(frozen=True)
class RepositoryStorageLayout:
    repository_root: Path
    base_dir: Path
    worktree_dir: Path
    legacy_project_dir: Path


def repository_storage_layout(
    config: object,
    project_dir: Path,
    git_context: GitRepositoryContext,
    *,
    migrate_legacy: bool = True,
) -> RepositoryStorageLayout:
    """Return shared repository storage plus this worktree's overlay namespace."""
    repository_root = _repository_storage_root(config) / git_context.repository_id
    legacy_project_dir = (
        project_storage_dir(config, project_dir)
        if migrate_legacy
        else resolve_project_storage_dir(config, project_dir)
    )
    return RepositoryStorageLayout(
        repository_root=repository_root,
        base_dir=repository_root / "base",
        worktree_dir=repository_root / "worktrees" / git_context.worktree_id,
        legacy_project_dir=legacy_project_dir,
    )


def _repository_storage_root(config: object) -> Path:
    project_storage_root = Path(config.storage_path)  # type: ignore[union-attr]
    return project_storage_root.parent / "repos"
