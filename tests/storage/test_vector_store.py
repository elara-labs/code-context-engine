import sqlite3
import time

import pytest

from context_engine.models import Chunk, ChunkType
from context_engine.storage.vector_store import VectorStore


@pytest.fixture
def store(tmp_path):
    return VectorStore(db_path=str(tmp_path / "vectors"))


@pytest.fixture
def sample_chunks():
    return [
        Chunk(
            id="chunk_1",
            content="def add(a, b): return a + b",
            chunk_type=ChunkType.FUNCTION,
            file_path="math.py",
            start_line=1,
            end_line=1,
            language="python",
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Chunk(
            id="chunk_2",
            content="def subtract(a, b): return a - b",
            chunk_type=ChunkType.FUNCTION,
            file_path="math.py",
            start_line=3,
            end_line=3,
            language="python",
            embedding=[0.5, 0.6, 0.7, 0.8],
        ),
    ]


@pytest.mark.asyncio
async def test_ingest_and_search(store, sample_chunks):
    await store.ingest(sample_chunks)
    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], top_k=2)
    assert len(results) > 0
    assert results[0].id == "chunk_1"


@pytest.mark.asyncio
async def test_search_with_filter(store, sample_chunks):
    await store.ingest(sample_chunks)
    results = await store.search(
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        top_k=2,
        filters={"language": "python"},
    )
    assert all(c.language == "python" for c in results)


@pytest.mark.asyncio
async def test_delete_by_file_path(store, sample_chunks):
    await store.ingest(sample_chunks)
    await store.delete_by_file(file_path="math.py")
    results = await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], top_k=10)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_get_by_id(store, sample_chunks):
    await store.ingest(sample_chunks)
    chunk = await store.get_by_id("chunk_1")
    assert chunk is not None
    assert chunk.content == "def add(a, b): return a + b"


def test_count_empty(tmp_path):
    vs = VectorStore(db_path=str(tmp_path / "db"))
    assert vs.count() == 0


def test_file_chunk_counts_empty(tmp_path):
    vs = VectorStore(db_path=str(tmp_path / "db"))
    assert vs.file_chunk_counts() == {}


def test_clear_resets_table(tmp_path):
    from context_engine.models import Chunk, ChunkType
    vs = VectorStore(db_path=str(tmp_path / "db"))
    chunk = Chunk(
        id="c1", content="def foo(): pass", chunk_type=ChunkType.FUNCTION,
        file_path="foo.py", start_line=1, end_line=1, language="python",
        embedding=[0.1] * 384,
    )
    import asyncio
    asyncio.run(vs.ingest([chunk]))
    assert vs.count() == 1
    vs.clear()
    assert vs.count() == 0


def test_dim_change_clears_chunks_table(tmp_path):
    """Embedding dimension change must wipe the chunks table too.

    Regression for the 2026-04-27 review: previously only chunks_vec was
    dropped, leaving chunks rows that count_chunks/file_chunk_counts still
    reported but that search could never return.
    """
    from context_engine.models import Chunk, ChunkType
    import asyncio

    vs = VectorStore(db_path=str(tmp_path / "db"))
    # First ingest at dim=4.
    asyncio.run(vs.ingest([
        Chunk(id="c1", content="a", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[0.1, 0.2, 0.3, 0.4]),
    ]))
    assert vs.count() == 1

    # Re-ingest a *different* chunk at dim=8 — simulates switching models.
    asyncio.run(vs.ingest([
        Chunk(id="c2", content="b", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.1] * 8),
    ]))

    # Only the new chunk should remain — c1 was orphaned by the dim change.
    assert vs.count() == 1
    assert vs.file_chunk_counts() == {"b.py": 1}
    # And it should be queryable (not just an orphan).
    results = asyncio.run(vs.search(query_embedding=[0.1] * 8, top_k=5))
    assert len(results) == 1
    assert results[0].id == "c2"


def test_file_chunk_counts_after_ingest(tmp_path):
    from context_engine.models import Chunk, ChunkType
    import asyncio
    vs = VectorStore(db_path=str(tmp_path / "db"))
    chunks = [
        Chunk(id="c1", content="a", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[0.1] * 384),
        Chunk(id="c2", content="b", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=2, end_line=2, language="python",
              embedding=[0.2] * 384),
        Chunk(id="c3", content="c", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.3] * 384),
    ]
    asyncio.run(vs.ingest(chunks))
    counts = vs.file_chunk_counts()
    assert counts["a.py"] == 2
    assert counts["b.py"] == 1


@pytest.mark.asyncio
async def test_search_uses_cosine_distance(store):
    """The vec0 table must use cosine distance, matching the retriever's
    distance/2.0 normalisation. Under the L2 default, `diff_dir` (small
    magnitude, different direction) would wrongly rank above `same_dir`
    (large magnitude, same direction as the query)."""
    a = Chunk(id="same_dir", content="a", chunk_type=ChunkType.FUNCTION,
              file_path="a.py", start_line=1, end_line=1, language="python",
              embedding=[10.0, 0.0, 0.0, 0.0])
    b = Chunk(id="diff_dir", content="b", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.6, 0.8, 0.0, 0.0])
    await store.ingest([a, b])
    results = await store.search(query_embedding=[1.0, 0.0, 0.0, 0.0], top_k=2)
    assert [c.id for c in results] == ["same_dir", "diff_dir"]
    assert results[0].metadata["_distance"] == pytest.approx(0.0, abs=1e-5)
    assert results[1].metadata["_distance"] == pytest.approx(0.4, abs=1e-3)


def test_legacy_l2_table_is_rebuilt_with_cosine(tmp_path):
    """Opening a store whose chunks_vec was created without
    distance_metric=cosine (the old L2 default) must rebuild the index empty
    rather than silently mixing metrics."""
    import asyncio
    import sqlite3
    import struct

    import sqlite_vec

    db_dir = tmp_path / "vectors"
    db_dir.mkdir()
    conn = sqlite3.connect(str(db_dir / "vectors.db"))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, content TEXT NOT NULL, "
        "chunk_type TEXT NOT NULL, file_path TEXT NOT NULL, "
        "start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, "
        "language TEXT NOT NULL)"
    )
    conn.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[4])")
    conn.execute(
        "INSERT INTO chunks VALUES ('c1', 'x', 'function', 'a.py', 1, 1, 'python')"
    )
    conn.execute(
        "INSERT INTO chunks_vec(rowid, embedding) VALUES (1, ?)",
        (struct.pack("4f", 0.1, 0.2, 0.3, 0.4),),
    )
    conn.commit()
    conn.close()

    vs = VectorStore(db_path=str(db_dir))
    # Legacy L2 index wiped; repopulated on reindex.
    assert vs.count() == 0
    assert asyncio.run(vs.search(query_embedding=[0.1, 0.2, 0.3, 0.4], top_k=5)) == []

    # New ingest recreates the table with the cosine metric.
    asyncio.run(vs.ingest([
        Chunk(id="c2", content="y", chunk_type=ChunkType.FUNCTION,
              file_path="b.py", start_line=1, end_line=1, language="python",
              embedding=[0.1, 0.2, 0.3, 0.4]),
    ]))
    ddl = vs._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
    ).fetchone()[0]
    assert "distance_metric=cosine" in ddl
    assert vs.count() == 1


@pytest.mark.asyncio
async def test_ingest_rolls_back_on_midbatch_failure(store):
    """A failed chunks_vec insert mid-loop must not leave pending rows that a
    later unrelated commit flushes (chunks with no vector rows are silently
    unfindable)."""
    good = Chunk(id="good", content="a", chunk_type=ChunkType.FUNCTION,
                 file_path="a.py", start_line=1, end_line=1, language="python",
                 embedding=[0.1, 0.2, 0.3, 0.4])
    bad = Chunk(id="bad", content="b", chunk_type=ChunkType.FUNCTION,
                file_path="b.py", start_line=1, end_line=1, language="python",
                embedding=[0.1] * 8)  # wrong dim -> chunks_vec insert fails
    with pytest.raises(Exception):
        await store.ingest([good, bad])
    # Trigger an unrelated commit on the same connection.
    store.put_cached_compression("unrelated", "short", "text")
    assert store.count() == 0
    assert await store.search(query_embedding=[0.1, 0.2, 0.3, 0.4], top_k=5) == []


def _mk_chunk(cid: str, mtime: float | None = None) -> Chunk:
    c = Chunk(
        id=cid,
        content=f"def {cid}():\n    pass\n",
        chunk_type=ChunkType.FUNCTION,
        file_path="src/mod.py",
        start_line=1,
        end_line=2,
        language="python",
        embedding=[0.1, 0.2, 0.3],
    )
    if mtime is not None:
        c.metadata["modified_ts"] = mtime
    return c


@pytest.mark.asyncio
async def test_modified_ts_round_trips(tmp_path):
    store = VectorStore(db_path=str(tmp_path))
    now = time.time()
    await store.ingest([_mk_chunk("with_ts", mtime=now)])

    results = await store.search([0.1, 0.2, 0.3], top_k=1)
    assert results, "expected one search hit"
    assert results[0].metadata["modified_ts"] == pytest.approx(now)

    by_id = await store.get_by_id("with_ts")
    assert by_id.metadata["modified_ts"] == pytest.approx(now)

    by_ids = await store.get_chunks_by_ids(["with_ts"])
    assert by_ids[0].metadata["modified_ts"] == pytest.approx(now)


@pytest.mark.asyncio
async def test_modified_ts_absent_stays_absent(tmp_path):
    store = VectorStore(db_path=str(tmp_path))
    await store.ingest([_mk_chunk("no_ts")])
    results = await store.search([0.1, 0.2, 0.3], top_k=1)
    assert "modified_ts" not in results[0].metadata


@pytest.mark.asyncio
async def test_legacy_db_without_column_is_migrated(tmp_path):
    # Simulate a pre-Phase-1 DB: create the store, then drop the column
    # by rebuilding the table without it, then reopen.
    store = VectorStore(db_path=str(tmp_path))
    await store.ingest([_mk_chunk("old_row")])
    conn = sqlite3.connect(str(tmp_path / "vectors.db"))
    conn.executescript(
        """
        CREATE TABLE chunks_old AS
            SELECT id, content, chunk_type, file_path, start_line, end_line, language
            FROM chunks;
        DROP TABLE chunks;
        ALTER TABLE chunks_old RENAME TO chunks;
        """
    )
    conn.commit()
    conn.close()

    reopened = VectorStore(db_path=str(tmp_path))  # must not raise
    row = await reopened.get_by_id("old_row")
    assert row is not None
    assert "modified_ts" not in row.metadata  # NULL column → neutral recency
