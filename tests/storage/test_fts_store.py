import pytest
from context_engine.models import Chunk, ChunkType
from context_engine.storage.fts_store import FTSStore


@pytest.fixture
def fts(tmp_path):
    return FTSStore(db_path=str(tmp_path / "fts"))


@pytest.fixture
def sample_chunks():
    return [
        Chunk(id="c1", content="def calculate_tax(amount, rate): return amount * rate",
              chunk_type=ChunkType.FUNCTION, file_path="finance.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c2", content="def process_payment(card, amount): charge the card",
              chunk_type=ChunkType.FUNCTION, file_path="payment.py",
              start_line=1, end_line=1, language="python"),
        Chunk(id="c3", content="class ShippingCalculator: calculates shipping costs",
              chunk_type=ChunkType.CLASS, file_path="shipping.py",
              start_line=1, end_line=5, language="python"),
    ]


@pytest.mark.asyncio
async def test_ingest_and_search(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    results = await fts.search("calculate_tax", top_k=5)
    assert len(results) > 0
    ids = [r[0] for r in results]
    assert "c1" in ids


@pytest.mark.asyncio
async def test_search_returns_scores(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    results = await fts.search("payment", top_k=5)
    assert all(isinstance(score, float) for _, score in results)


@pytest.mark.asyncio
async def test_delete_by_file(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    await fts.delete_by_file("finance.py")
    results = await fts.search("calculate_tax", top_k=5)
    ids = [r[0] for r in results]
    assert "c1" not in ids


@pytest.mark.asyncio
async def test_search_empty_store(fts):
    results = await fts.search("anything", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_special_chars_in_query(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    for q in ['"quoted"', "a-b", "fn(x)", "col:val", "wild*card"]:
        results = await fts.search(q, top_k=5)
        assert isinstance(results, list)


@pytest.mark.asyncio
async def test_multiword_query_matches_nonconsecutive_terms(fts, sample_chunks):
    """Natural-language queries must not be treated as a single strict phrase.

    'process' / 'card' / 'payment' all appear in c2's content but never
    consecutively in this order — the BM25 leg must still find it."""
    await fts.ingest(sample_chunks)
    results = await fts.search("process the card payment", top_k=5)
    assert "c2" in [r[0] for r in results]


@pytest.mark.asyncio
async def test_multiword_query_with_stopwords(fts, sample_chunks):
    await fts.ingest(sample_chunks)
    results = await fts.search("where is the shipping costs calculation", top_k=5)
    assert "c3" in [r[0] for r in results]


@pytest.mark.asyncio
async def test_underscore_identifier_still_matches(fts):
    chunk = Chunk(id="c9",
                  content="async def delete_by_files(self, file_paths): pass",
                  chunk_type=ChunkType.FUNCTION, file_path="store.py",
                  start_line=1, end_line=1, language="python")
    await fts.ingest([chunk])
    results = await fts.search("delete_by_files", top_k=5)
    assert "c9" in [r[0] for r in results]


@pytest.mark.asyncio
async def test_fts5_operators_are_neutralized(fts, sample_chunks):
    """FTS5 operators in user input must not raise or act as operators."""
    await fts.ingest(sample_chunks)
    for q in [
        "payment NEAR card",
        "content: payment",
        "payment AND card",
        "NOT payment",
        "(payment)",
        'pay* card"',
        "payment OR shipping",
        "^payment",
    ]:
        results = await fts.search(q, top_k=5)
        assert isinstance(results, list)


@pytest.mark.asyncio
async def test_wildcard_does_not_prefix_match(fts, sample_chunks):
    """'pay*' must be treated literally (token 'pay'), not as a prefix query,
    so it must not match documents containing the token 'payment'."""
    await fts.ingest(sample_chunks)
    results = await fts.search("pay*", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_reingest_same_id_does_not_duplicate(fts, sample_chunks):
    """INSERT OR REPLACE never replaces on FTS5 virtual tables; re-ingesting
    the same chunk id must not create duplicate rows."""
    await fts.ingest(sample_chunks)
    await fts.ingest(sample_chunks)
    results = await fts.search("calculate_tax", top_k=10)
    assert [r[0] for r in results].count("c1") == 1


@pytest.mark.asyncio
async def test_ingest_failure_leaves_no_partial_rows(fts, sample_chunks):
    """A mid-batch ingest failure must roll back, not leave pending rows that
    the next unrelated commit silently flushes."""
    bad = Chunk(id="bad", content=["not", "a", "string"],  # type: ignore[arg-type]
                chunk_type=ChunkType.FUNCTION, file_path="bad.py",
                start_line=1, end_line=1, language="python")
    with pytest.raises(Exception):
        await fts.ingest(sample_chunks + [bad])
    # Trigger an unrelated commit on the same connection.
    await fts.delete_by_file("nonexistent.py")
    results = await fts.search("calculate_tax", top_k=5)
    assert results == []
