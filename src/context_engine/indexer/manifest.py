"""Content hash manifest for incremental indexing."""
import json
import logging
from pathlib import Path

from context_engine.utils import atomic_write_text

log = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2


class Manifest:
    def __init__(self, manifest_path: Path) -> None:
        self._path = manifest_path
        self._entries: dict[str, str] = {}
        self._schema_version: int = CURRENT_SCHEMA_VERSION
        self._last_git_sha: str | None = None
        # Embedding dimension recorded the last time the index was written.
        # Used to detect that the embedding backend (or model) changed under
        # us between runs — when the dim no longer matches the loaded
        # backend, the pipeline forces a full reindex so the vector store
        # can be rebuilt at the new dim.
        self._embedding_dim: int | None = None

        if self._path.exists():
            try:
                with open(self._path) as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if "__schema_version" in loaded:
                        # New versioned format
                        self._schema_version = loaded["__schema_version"]
                        self._entries = loaded.get("files", {})
                        self._last_git_sha = loaded.get("last_git_sha")
                        self._embedding_dim = loaded.get("embedding_dim")
                    else:
                        # Old plain-dict format (pre-v0.2) — treat as version 1
                        self._schema_version = 1
                        self._entries = loaded
                else:
                    log.warning(
                        "Manifest at %s was not a dict (got %s); starting empty.",
                        self._path,
                        type(loaded).__name__,
                    )
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Manifest at %s unreadable (%s); starting empty.", self._path, exc)
                self._entries = {}

    @property
    def schema_version(self) -> int:
        return self._schema_version

    @property
    def needs_reindex(self) -> bool:
        return self._schema_version != CURRENT_SCHEMA_VERSION

    @property
    def last_git_sha(self) -> str | None:
        return self._last_git_sha

    @last_git_sha.setter
    def last_git_sha(self, value: str | None) -> None:
        self._last_git_sha = value

    @property
    def embedding_dim(self) -> int | None:
        return self._embedding_dim

    @embedding_dim.setter
    def embedding_dim(self, value: int | None) -> None:
        self._embedding_dim = value

    def clear_entries(self) -> None:
        """Forget every file's content hash.

        Used when an external invariant changes (embedding dimension or
        schema version) so the next pipeline run must re-embed every file
        even if the file content itself is unchanged. Does not touch the
        on-disk file — the caller is expected to call `save()` after the
        reindex completes.
        """
        self._entries = {}

    def get_hash(self, file_path: str) -> str | None:
        return self._entries.get(file_path)

    def update(self, file_path: str, content_hash: str) -> None:
        self._entries[file_path] = content_hash

    def remove(self, file_path: str) -> None:
        self._entries.pop(file_path, None)

    def has_changed(self, file_path: str, content_hash: str) -> bool:
        return self._entries.get(file_path) != content_hash

    def save(self) -> None:
        payload = {
            "__schema_version": CURRENT_SCHEMA_VERSION,
            "files": self._entries,
            "last_git_sha": self._last_git_sha,
            "embedding_dim": self._embedding_dim,
        }
        atomic_write_text(self._path, json.dumps(payload))
