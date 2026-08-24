"""Git worktree diff model for overlay indexing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from context_engine.git.repository import _git


@dataclass(frozen=True)
class GitWorktreeDiff:
    modified: set[str] = field(default_factory=set)
    added: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    renamed: dict[str, str] = field(default_factory=dict)
    base_sha: str | None = None
    head_sha: str | None = None


def get_worktree_diff(
    worktree_root: Path,
    *,
    base_sha: str | None,
    head_sha: str | None,
) -> GitWorktreeDiff:
    """Return committed, staged, unstaged, and untracked worktree changes."""
    builder = _DiffBuilder(base_sha=base_sha, head_sha=head_sha)

    if base_sha and head_sha and base_sha != head_sha:
        builder.apply_name_status(_git(worktree_root, ["diff", "--name-status", f"{base_sha}...HEAD"]))

    builder.apply_name_status(_git(worktree_root, ["diff", "--cached", "--name-status"]))
    builder.apply_name_status(_git(worktree_root, ["diff", "--name-status"]))
    builder.apply_untracked(_git(worktree_root, ["ls-files", "--others", "--exclude-standard"]))

    return builder.build()


@dataclass
class _DiffBuilder:
    modified: set[str] = field(default_factory=set)
    added: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    renamed: dict[str, str] = field(default_factory=dict)
    base_sha: str | None = None
    head_sha: str | None = None

    def apply_name_status(self, output: str | None) -> None:
        if not output:
            return
        for line in output.splitlines():
            parts = line.split("\t")
            if not parts:
                continue
            status = parts[0]
            if status.startswith("R") and len(parts) >= 3:
                self._rename(parts[1], parts[2])
            elif status.startswith("A") and len(parts) >= 2:
                self._add(parts[1])
            elif status.startswith("D") and len(parts) >= 2:
                self._delete(parts[1])
            elif len(parts) >= 2:
                self._modify(parts[1])

    def apply_untracked(self, output: str | None) -> None:
        if not output:
            return
        for path in output.splitlines():
            if path:
                self._add(path)

    def build(self) -> GitWorktreeDiff:
        return GitWorktreeDiff(
            modified=set(self.modified),
            added=set(self.added),
            deleted=set(self.deleted),
            renamed=dict(self.renamed),
            base_sha=self.base_sha,
            head_sha=self.head_sha,
        )

    def _add(self, path: str) -> None:
        self.deleted.discard(path)
        self.modified.discard(path)
        self.added.add(path)

    def _delete(self, path: str) -> None:
        self.added.discard(path)
        self.modified.discard(path)
        self.deleted.add(path)

    def _modify(self, path: str) -> None:
        if path not in self.added and path not in self.deleted:
            self.modified.add(path)

    def _rename(self, old_path: str, new_path: str) -> None:
        self.renamed[old_path] = new_path
        self._delete(old_path)
        self._add(new_path)
