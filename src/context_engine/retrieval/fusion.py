"""Query-time fusion for semantic and structural worktree context."""

from __future__ import annotations

from dataclasses import dataclass, field

from context_engine.git.diff import GitWorktreeDiff
from context_engine.models import Chunk
from context_engine.retrieval.budgeting import DisclosureLevel, resolve_context_budget
from context_engine.structural.models import StructuralContext


@dataclass(frozen=True)
class RetrievalFusionInput:
    base_chunks: list[Chunk] = field(default_factory=list)
    overlay_chunks: list[Chunk] = field(default_factory=list)
    structural: StructuralContext = field(default_factory=StructuralContext)
    diff: GitWorktreeDiff = field(default_factory=GitWorktreeDiff)


@dataclass(frozen=True)
class FusedRetrievalContext:
    chunks: list[Chunk]
    structural: StructuralContext
    omitted: int = 0


def fuse_retrieval_context(
    data: RetrievalFusionInput,
    *,
    level: DisclosureLevel = "standard",
    max_tokens: int | None = None,
) -> FusedRetrievalContext:
    """Merge semantic overlay/base chunks and keep hard token budget."""
    chunks = _merged_chunks(data.base_chunks, data.overlay_chunks, data.diff)
    budget = resolve_context_budget(level=level, max_tokens=max_tokens)
    packed, omitted = _pack_chunks(
        chunks,
        max(0, budget - _structural_token_count(data.structural)),
    )
    return FusedRetrievalContext(chunks=packed, structural=data.structural, omitted=omitted)


def _merged_chunks(
    base_chunks: list[Chunk],
    overlay_chunks: list[Chunk],
    diff: GitWorktreeDiff,
) -> list[Chunk]:
    shadowed_paths = diff.added | diff.modified | diff.deleted
    merged: dict[tuple[str, int, int], Chunk] = {}
    for chunk in base_chunks:
        if chunk.file_path in shadowed_paths:
            continue
        merged[_chunk_key(chunk)] = chunk
    for chunk in overlay_chunks:
        merged[_chunk_key(chunk)] = chunk
    return sorted(merged.values(), key=_chunk_rank_key)


def _pack_chunks(chunks: list[Chunk], max_tokens: int | None) -> tuple[list[Chunk], int]:
    if max_tokens is None:
        return chunks, 0
    budget = max_tokens
    packed: list[Chunk] = []
    for chunk in chunks:
        if chunk.token_count > budget:
            continue
        packed.append(chunk)
        budget -= chunk.token_count
    return packed, len(chunks) - len(packed)


def _chunk_key(chunk: Chunk) -> tuple[str, int, int]:
    return (chunk.file_path, chunk.start_line, chunk.end_line)


def _chunk_rank_key(chunk: Chunk) -> tuple[float, float, str]:
    distance = chunk.metadata.get("_distance")
    if isinstance(distance, int | float):
        return (0.0, float(distance), chunk.id)
    return (1.0, -chunk.confidence_score, chunk.id)


def _structural_token_count(structural: StructuralContext) -> int:
    text_parts: list[str] = []
    text_parts.extend(source.content or source.path for source in structural.sources)
    text_parts.extend(
        f"{relationship.source.qualified_name} {relationship.kind} {relationship.target.qualified_name}"
        for relationship in structural.relationships
    )
    text_parts.extend(symbol.qualified_name for symbol in structural.impact)
    return max(0, int(sum(len(part) for part in text_parts) / 3.3))
