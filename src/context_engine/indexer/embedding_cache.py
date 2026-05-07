"""SQLite-backed embedding cache keyed by content hash.

Avoids recomputing embeddings for unchanged code chunks across re-index runs.
Inspired by Cursor's approach of caching embeddings by chunk content hash so
identical code is never re-embedded.

Vectors are stored via `struct.pack` (binary float32) rather than JSON — same
encoding the sqlite-vec store uses elsewhere in the codebase. JSON would be
~4× larger on disk for typical 384-dim embeddings.
"""
import hashlib
import logging
import sqlite3
import struct
from pathlib import Path
from threading import RLock

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT PRIMARY KEY,
    dim          INTEGER NOT NULL,
    embedding    BLOB NOT NULL
);
"""


class EmbeddingCache:
    """Maps content SHA-256 → embedding vector, persisted in SQLite.

    When *model_name* is provided the content hash is salted with the model
    identifier so that switching embedding models automatically invalidates
    stale cache entries rather than silently returning vectors with the wrong
    dimensionality or semantics.

    All SQLite access is serialised with an RLock (same pattern as VectorStore
    and FTSStore). ``check_same_thread=False`` only disables Python's ownership
    check; concurrent calls still need explicit locking.
    """

    def __init__(self, cache_path: Path, *, model_name: str = "") -> None:
        self._path = cache_path
        self._model_name = model_name
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._hits = 0
        self._misses = 0

    def content_hash(self, text: str) -> str:
        """SHA-256 of *text*, salted with model name when set."""
        key = f"{self._model_name}:{text}" if self._model_name else text
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _pack(vec) -> bytes:
        v = list(vec) if not isinstance(vec, list) else vec
        return struct.pack(f"{len(v)}f", *v)

    @staticmethod
    def _unpack(blob: bytes, dim: int) -> list[float]:
        return list(struct.unpack(f"{dim}f", blob))

    def get(self, content_hash: str) -> list[float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT dim, embedding FROM embedding_cache WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        if row is None:
            self._misses += 1
            return None
        self._hits += 1
        return self._unpack(row[1], row[0])

    def put(self, content_hash: str, embedding) -> None:
        v = list(embedding) if not isinstance(embedding, list) else embedding
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO embedding_cache (content_hash, dim, embedding) VALUES (?, ?, ?)",
                (content_hash, len(v), self._pack(v)),
            )
            self._conn.commit()

    def put_batch(self, items: list[tuple[str, list[float]]]) -> None:
        rows = [(h, len(e), self._pack(e)) for h, e in items]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache (content_hash, dim, embedding) VALUES (?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def get_batch(self, content_hashes: list[str]) -> dict[str, list[float]]:
        """Retrieve multiple embeddings at once. Returns hash → embedding for hits."""
        if not content_hashes:
            return {}
        results: dict[str, list[float]] = {}
        with self._lock:
            for i in range(0, len(content_hashes), 500):
                batch = content_hashes[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                rows = self._conn.execute(
                    f"SELECT content_hash, dim, embedding FROM embedding_cache "
                    f"WHERE content_hash IN ({placeholders})",
                    batch,
                ).fetchall()
                for h, dim, blob in rows:
                    results[h] = self._unpack(blob, dim)
        self._hits += len(results)
        self._misses += len(content_hashes) - len(results)
        return results

    def prune_orphans(self, known_hashes: set[str]) -> int:
        """Drop cached entries whose content_hash is not in `known_hashes`.

        Cache grows monotonically without this — every chunk content variant
        ever seen accumulates forever even after the source files change or
        get deleted. Call this after a `cce index --full` with the set of
        hashes still present in the live index. Returns the count removed.
        """
        if not known_hashes:
            return 0
        with self._lock:
            cur = self._conn.execute("SELECT content_hash FROM embedding_cache")
            current = {row[0] for row in cur.fetchall()}
            orphans = current - known_hashes
            if not orphans:
                return 0
            removed = 0
            orphan_list = list(orphans)
            for i in range(0, len(orphan_list), 500):
                batch = orphan_list[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                # Safe: placeholders is only "?" chars; values are parameterized.
                self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"DELETE FROM embedding_cache WHERE content_hash IN ({placeholders})",
                    batch,
                )
                removed += len(batch)
            self._conn.commit()
        return removed

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def size(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
