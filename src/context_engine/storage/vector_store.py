"""SQLite-vec backed vector store for chunk embeddings.

Replaces LanceDB (217MB) with sqlite-vec (~2MB). Same API, same search
quality. Uses cosine distance for similarity ranking.

Schema:
  chunks — regular table storing chunk metadata + content
  chunks_vec — vec0 virtual table storing embeddings for vector search
"""
import logging
import os
import sqlite3
import struct
from threading import RLock

from context_engine.models import Chunk, ChunkType

log = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 5_000


def _to_list(embedding) -> list[float]:
    """Ensure embedding is a plain list."""
    if isinstance(embedding, list):
        return embedding
    return list(embedding)


def _serialize_vec(vec) -> bytes:
    """Pack a float vector into bytes for sqlite-vec."""
    v = _to_list(vec)
    return struct.pack(f"{len(v)}f", *v)


class VectorStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = RLock()
        self._dim: int | None = None
        os.makedirs(db_path, exist_ok=True)
        self._db_file = os.path.join(db_path, "vectors.db")
        self._conn = self._connect()
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        import sqlite_vec
        conn = sqlite3.connect(self._db_file, check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_tables(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    language TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_file_path
                ON chunks(file_path)
            """)
            # Cache of LLM-summarised / truncated chunk text. Keyed by
            # (chunk_id, level) because different compression levels produce
            # different output. Cleared automatically when the chunk is
            # re-ingested (delete-by-file via FK-like DELETE in delete_by_file).
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_compressions (
                    chunk_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    compressed TEXT NOT NULL,
                    PRIMARY KEY (chunk_id, level)
                )
            """)
            # Detect vector dimension from existing data
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
            ).fetchone()
            if row:
                # Table exists — read dim from first row
                r = self._conn.execute("SELECT rowid FROM chunks_vec LIMIT 1").fetchone()
                if r:
                    self._dim = self._conn.execute(
                        "SELECT vec_length(embedding) FROM chunks_vec LIMIT 1"
                    ).fetchone()[0]
            self._conn.commit()

    def _ensure_vec_table(self, dim: int) -> None:
        if self._dim == dim:
            return
        with self._lock:
            if self._dim is not None and self._dim != dim:
                log.warning(
                    "Embedding dimension changed (%d -> %d), rebuilding vector table",
                    self._dim, dim,
                )
                # Wipe both halves of the index. Keeping `chunks` while
                # dropping `chunks_vec` would leave previously-indexed rows
                # counted by count_chunks() / file_chunk_counts() but with no
                # vector to retrieve, so search would silently miss them.
                # chunk_compressions is keyed by chunk_id, so flush it too.
                self._conn.execute("DROP TABLE IF EXISTS chunks_vec")
                self._conn.execute("DELETE FROM chunks")
                self._conn.execute("DELETE FROM chunk_compressions")
            # Safe: dim is a validated integer, never from user input.
            self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec "
                f"USING vec0(embedding float[{dim}])"
            )
            self._dim = dim
            self._conn.commit()

    def _chunk_to_row(self, chunk: Chunk) -> tuple:
        content = chunk.content
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n...[truncated]"
        return (
            chunk.id, content, chunk.chunk_type.value,
            chunk.file_path, chunk.start_line, chunk.end_line,
            chunk.language,
        )

    def _row_to_chunk(self, row, distance: float | None = None) -> Chunk:
        chunk = Chunk(
            id=row[0],
            content=row[1],
            chunk_type=ChunkType(row[2]),
            file_path=row[3],
            start_line=row[4],
            end_line=row[5],
            language=row[6],
        )
        if distance is not None:
            chunk.metadata["_distance"] = distance
        return chunk

    async def ingest(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        valid = [c for c in chunks if c.embedding]
        if not valid:
            log.warning("ingest called but no chunks have embeddings")
            return
        dim = len(valid[0].embedding)
        self._ensure_vec_table(dim)
        with self._lock:
            cursor = self._conn.cursor()
            for chunk in valid:
                row = self._chunk_to_row(chunk)
                rowid = cursor.execute(
                    "INSERT INTO chunks "
                    "(id, content, chunk_type, file_path, start_line, end_line, language) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "content = excluded.content, "
                    "chunk_type = excluded.chunk_type, "
                    "file_path = excluded.file_path, "
                    "start_line = excluded.start_line, "
                    "end_line = excluded.end_line, "
                    "language = excluded.language "
                    "RETURNING rowid",
                    row,
                ).fetchone()[0]
                cursor.execute("DELETE FROM chunks_vec WHERE rowid = ?", (rowid,))
                cursor.execute(
                    "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                    (rowid, _serialize_vec(chunk.embedding)),
                )
            self._conn.commit()

    async def search(
        self,
        query_embedding,
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[Chunk]:
        embedding_list = _to_list(query_embedding)
        with self._lock:
            if self._dim is None:
                return []
            try:
                query_bytes = _serialize_vec(embedding_list)
                # Vector search via sqlite-vec
                # sqlite-vec requires k=? in WHERE, not LIMIT
                unsupported = set(filters or {}) - {"file_path"}
                if unsupported:
                    log.warning("Unsupported filter keys ignored: %s", unsupported)
                if filters and "file_path" in filters:
                    fp = filters["file_path"]
                    # First get matching rowids from vec search, then filter
                    rows = self._conn.execute(
                        """
                        SELECT c.id, c.content, c.chunk_type, c.file_path,
                               c.start_line, c.end_line, c.language, v.distance
                        FROM chunks_vec v
                        JOIN chunks c ON c.rowid = v.rowid
                        WHERE v.embedding MATCH ? AND k = ?
                          AND c.file_path = ?
                        ORDER BY v.distance
                        """,
                        (query_bytes, top_k * 3, fp),
                    ).fetchall()[:top_k]
                else:
                    rows = self._conn.execute(
                        """
                        SELECT c.id, c.content, c.chunk_type, c.file_path,
                               c.start_line, c.end_line, c.language, v.distance
                        FROM chunks_vec v
                        JOIN chunks c ON c.rowid = v.rowid
                        WHERE v.embedding MATCH ? AND k = ?
                        ORDER BY v.distance
                        """,
                        (query_bytes, top_k),
                    ).fetchall()
            except Exception as exc:
                log.warning(
                    "vector_store.search failed (returning no results — "
                    "this may indicate index corruption): %s",
                    exc,
                )
                return []
        return [self._row_to_chunk(row[:7], distance=row[7]) for row in rows]

    async def delete_by_file(self, file_path: str) -> None:
        await self.delete_by_files([file_path])

    async def delete_by_files(self, file_paths: list[str]) -> None:
        """Batched delete. Pipeline calls this once per re-index batch instead
        of awaiting per-file deletes serially, which previously bottlenecked
        the indexing loop on small SQLite roundtrips."""
        if not file_paths:
            return
        from context_engine.utils import batched_params

        with self._lock:
            # Safe: placeholders is only "?" chars; values are parameterized.
            for batch in batched_params(file_paths):
                placeholders = ",".join("?" * len(batch))
                if self._dim is not None:
                    self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                        f"DELETE FROM chunks_vec "
                        f"WHERE rowid IN (SELECT rowid FROM chunks WHERE file_path IN ({placeholders}))",
                        batch,
                    )
                self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"DELETE FROM chunk_compressions "
                    f"WHERE chunk_id IN (SELECT id FROM chunks WHERE file_path IN ({placeholders}))",
                    batch,
                )
                self._conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"DELETE FROM chunks WHERE file_path IN ({placeholders})",
                    batch,
                )
            self._conn.commit()

    def get_cached_compression(self, chunk_id: str, level: str) -> str | None:
        """Return the cached compressed text for (chunk_id, level), or None."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT compressed FROM chunk_compressions "
                    "WHERE chunk_id = ? AND level = ?",
                    (chunk_id, level),
                ).fetchone()
            except Exception as exc:
                log.debug("get_cached_compression failed for %s/%s: %s", chunk_id, level, exc)
                return None
        return row[0] if row else None

    def put_cached_compression(self, chunk_id: str, level: str, compressed: str) -> None:
        """Persist a compression result so the same chunk isn't recompressed
        on every retrieval. Silently ignores write errors — caching is best
        effort, the caller already has the value to return to the user."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunk_compressions "
                    "(chunk_id, level, compressed) VALUES (?, ?, ?)",
                    (chunk_id, level, compressed),
                )
                self._conn.commit()
            except Exception as exc:
                log.debug("put_cached_compression failed for %s/%s: %s", chunk_id, level, exc)

    def count(self) -> int:
        with self._lock:
            try:
                row = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
                return row[0] if row else 0
            except Exception as exc:
                # Log so users see "the index is broken" instead of "search
                # returns nothing"; bare-except-and-zero made schema corruption
                # indistinguishable from an empty index.
                log.warning("vector_store.count failed: %s", exc)
                return 0

    def file_chunk_counts(self) -> dict[str, int]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT file_path, COUNT(*) FROM chunks GROUP BY file_path"
                ).fetchall()
                return {fp: count for fp, count in rows}
            except Exception as exc:
                log.warning("vector_store.file_chunk_counts failed: %s", exc)
                return {}

    def clear(self) -> None:
        with self._lock:
            try:
                self._conn.execute("DELETE FROM chunks")
                self._conn.execute("DELETE FROM chunk_compressions")
                if self._dim is not None:
                    self._conn.execute("DROP TABLE IF EXISTS chunks_vec")
                    self._dim = None
                self._conn.commit()
            except Exception as exc:
                log.warning("vector_store.clear failed: %s", exc)

    async def get_by_id(self, chunk_id: str) -> Chunk | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT id, content, chunk_type, file_path, start_line, end_line, language "
                    "FROM chunks WHERE id = ?",
                    (chunk_id,),
                ).fetchone()
            except Exception as exc:
                log.error("get_by_id failed for %s: %s", chunk_id, exc)
                return None
        if not row:
            return None
        return self._row_to_chunk(row)

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        with self._lock:
            try:
                placeholders = ",".join("?" for _ in chunk_ids)
                rows = self._conn.execute(
                    f"SELECT id, content, chunk_type, file_path, start_line, end_line, language "
                    f"FROM chunks WHERE id IN ({placeholders})",
                    chunk_ids,
                ).fetchall()
            except Exception as exc:
                log.error("get_chunks_by_ids failed: %s", exc)
                return []
        return [self._row_to_chunk(r) for r in rows]
