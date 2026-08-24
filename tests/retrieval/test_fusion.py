"""Tests for semantic/structural retrieval fusion."""

from context_engine.git.diff import GitWorktreeDiff
from context_engine.models import Chunk, ChunkType
from context_engine.retrieval import RetrievalFusionInput, fuse_retrieval_context
from context_engine.structural import SourceRange, StructuralContext


def chunk(chunk_id: str, file_path: str, content: str = "x") -> Chunk:
    return Chunk(
        id=chunk_id,
        content=content,
        chunk_type=ChunkType.FUNCTION,
        file_path=file_path,
        start_line=1,
        end_line=2,
        language="python",
    )


def ranked_chunk(chunk_id: str, file_path: str, *, distance: float) -> Chunk:
    item = chunk(chunk_id, file_path)
    item.metadata["_distance"] = distance
    return item


def test_fusion_suppresses_base_chunks_from_modified_and_deleted_paths():
    result = fuse_retrieval_context(
        RetrievalFusionInput(
            base_chunks=[
                chunk("base_changed", "changed.py"),
                chunk("base_deleted", "deleted.py"),
                chunk("base_stable", "stable.py"),
            ],
            overlay_chunks=[chunk("overlay_changed", "changed.py")],
            diff=GitWorktreeDiff(modified={"changed.py"}, deleted={"deleted.py"}),
        )
    )

    assert {item.id for item in result.chunks} == {"overlay_changed", "base_stable"}


def test_fusion_preserves_relevance_across_overlay_and_base_results():
    result = fuse_retrieval_context(
        RetrievalFusionInput(
            base_chunks=[ranked_chunk("base_strong", "stable.py", distance=0.1)],
            overlay_chunks=[ranked_chunk("overlay_weak", "new.py", distance=1.2)],
            diff=GitWorktreeDiff(added={"new.py"}),
        )
    )

    assert [item.id for item in result.chunks] == ["base_strong", "overlay_weak"]


def test_fusion_suppresses_base_chunks_from_recreated_paths():
    result = fuse_retrieval_context(
        RetrievalFusionInput(
            base_chunks=[chunk("base_recreated", "recreated.py")],
            overlay_chunks=[chunk("overlay_recreated", "recreated.py")],
            diff=GitWorktreeDiff(added={"recreated.py"}),
        )
    )

    assert [item.id for item in result.chunks] == ["overlay_recreated"]


def test_fusion_dedupes_overlay_and_base_source_ranges():
    result = fuse_retrieval_context(
        RetrievalFusionInput(
            base_chunks=[chunk("base", "same.py")],
            overlay_chunks=[chunk("overlay", "same.py")],
        )
    )

    assert [item.id for item in result.chunks] == ["overlay"]


def test_fusion_respects_hard_token_budget_and_counts_omitted_chunks():
    result = fuse_retrieval_context(
        RetrievalFusionInput(
            overlay_chunks=[
                chunk("small", "a.py", "short"),
                chunk("large", "b.py", "x" * 200),
            ],
        ),
        max_tokens=10,
    )

    assert [item.id for item in result.chunks] == ["small"]
    assert result.omitted == 1


def test_fusion_summary_level_applies_default_budget():
    result = fuse_retrieval_context(
        RetrievalFusionInput(overlay_chunks=[chunk("large", "a.py", "x" * 6000)]),
        level="summary",
    )

    assert result.chunks == []
    assert result.omitted == 1


def test_fusion_preserves_structural_context():
    structural = StructuralContext(sources=[SourceRange("a.py", 1, 2)], provider="test")

    result = fuse_retrieval_context(RetrievalFusionInput(structural=structural))

    assert result.structural is structural


def test_fusion_counts_structural_context_against_token_budget():
    structural = StructuralContext(
        sources=[SourceRange("a.py", 1, 2, content="x" * 40)],
        provider="test",
    )

    result = fuse_retrieval_context(
        RetrievalFusionInput(
            overlay_chunks=[chunk("semantic", "semantic.py", "short")],
            structural=structural,
        ),
        max_tokens=10,
    )

    assert result.chunks == []
    assert result.omitted == 1
    assert result.structural is structural
