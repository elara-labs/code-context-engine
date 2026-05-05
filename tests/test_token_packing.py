from context_engine.models import Chunk, ChunkType


def _make_chunk(id, content, score=0.5):
    c = Chunk(id=id, content=content, chunk_type=ChunkType.FUNCTION,
              file_path="test.py", start_line=1, end_line=1, language="python")
    c.confidence_score = score
    return c


def test_token_count_property():
    c = _make_chunk("c1", "x" * 330)
    assert c.token_count == 100  # 330 / 3.3 = 100


def test_token_count_uses_compressed_if_available():
    c = _make_chunk("c1", "x" * 330)
    c.compressed_content = "x" * 33  # 33 / 3.3 = 10
    assert c.token_count == 10


def test_token_count_minimum_is_1():
    c = _make_chunk("c1", "")
    assert c.token_count == 1
