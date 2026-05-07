"""SQLite FTS5 full-text search store."""
import asyncio
import logging
import os
import sqlite3
from threading import RLock

from context_engine.models import Chunk

log = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 5_000


def _escape_fts5(query: str) -> str:
    """Wrap user input as an FTS5 phrase to avoid operator injection."""
    return '"' + query.replace('"', '""') + '"'


class FTSStore:
    """Single-connection SQLite FTS store, serialised with an RLock.

    `check_same_thread=False` only disables thread ownership checks; concurrent
    operations on one sqlite3 connection are still unsafe. Mirrors VectorStore's
    locking pattern so dashboard/MCP/reindex calls running through asyncio
    .to_thread don't interleave on the connection.
    """

    def __init__(self, db_path: str) -> None:
        os.makedirs(db_path, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(
            os.path.join(db_path, "fts.db"), check_same_thread=False
        )
        with self._lock:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                "USING fts5(id UNINDEXED, content, file_path, language, chunk_type)"
            )
            self._conn.commit()

    def _ingest_sync(self, chunks: list[Chunk]) -> None:
        # executemany packs all rows into one prepared-statement batch — about
        # 30-50% faster than the per-row INSERT loop on 1000+ chunks.
        rows = [
            (
                chunk.id,
                chunk.content[:_MAX_CONTENT_CHARS] if len(chunk.content) > _MAX_CONTENT_CHARS else chunk.content,
                chunk.file_path,
                chunk.language,
                chunk.chunk_type.value,
            )
            for chunk in chunks
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO chunks_fts(id, content, file_path, language, chunk_type) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def _search_sync(self, escaped_query: str, top_k: int) -> list[tuple[str, float]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, rank FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (escaped_query, top_k),
            )
            return [(row[0], float(row[1])) for row in cursor.fetchall()]

    def _delete_sync(self, file_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM chunks_fts WHERE file_path = ?", (file_path,)
            )
            self._conn.commit()

    def _delete_files_sync(self, file_paths: list[str]) -> None:
        if not file_paths:
            return
        from context_engine.utils import batched_params

        with self._lock:
            for batch in batched_params(file_paths):
                placeholders = ",".join("?" * len(batch))
                # Safe: placeholders is only "?" chars; values are parameterized.
                self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"DELETE FROM chunks_fts WHERE file_path IN ({placeholders})",
                    batch,
                )
            self._conn.commit()

    async def ingest(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await asyncio.to_thread(self._ingest_sync, chunks)

    async def search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_sync, _escape_fts5(query), top_k)

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunks_fts")
            self._conn.commit()

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.to_thread(self._delete_sync, file_path)

    async def delete_by_files(self, file_paths: list[str]) -> None:
        await asyncio.to_thread(self._delete_files_sync, file_paths)
