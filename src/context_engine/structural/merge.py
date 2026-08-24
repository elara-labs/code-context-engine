"""Merge shared structural base with worktree overlay context."""

from __future__ import annotations

from context_engine.git.diff import GitWorktreeDiff
from context_engine.structural.models import Relationship, SourceRange, StructuralContext, SymbolKey


def merge_structural_contexts(
    *,
    base: StructuralContext,
    overlay: StructuralContext,
    diff: GitWorktreeDiff,
) -> StructuralContext:
    """Return effective structural context for a worktree."""
    shadowed_paths = diff.added | diff.modified | diff.deleted
    overlay_symbols = set(overlay.impact)
    overlay_relationship_sources = {relationship.source for relationship in overlay.relationships}

    return StructuralContext(
        sources=_merge_sources(base.sources, overlay.sources, shadowed_paths),
        relationships=_merge_relationships(
            base.relationships,
            overlay.relationships,
            shadowed_paths,
            diff.deleted,
            overlay_relationship_sources,
        ),
        impact=[
            *overlay.impact,
            *[
                symbol
                for symbol in base.impact
                if symbol not in overlay_symbols and not _symbol_is_shadowed(symbol, shadowed_paths)
            ],
        ],
        provider=f"{base.provider}+{overlay.provider}",
        metadata={"base": base.metadata, "overlay": overlay.metadata},
    )


def _merge_sources(
    base_sources: list[SourceRange],
    overlay_sources: list[SourceRange],
    shadowed_paths: set[str],
) -> list[SourceRange]:
    merged = list(overlay_sources)
    seen = {_source_key(source) for source in overlay_sources}
    for source in base_sources:
        if source.path in shadowed_paths:
            continue
        key = _source_key(source)
        if key not in seen:
            merged.append(source)
            seen.add(key)
    return merged


def _merge_relationships(
    base_relationships: list[Relationship],
    overlay_relationships: list[Relationship],
    shadowed_paths: set[str],
    deleted_paths: set[str],
    overlay_relationship_sources: set[SymbolKey],
) -> list[Relationship]:
    merged = list(overlay_relationships)
    seen = set(overlay_relationships)
    for relationship in base_relationships:
        if _symbol_is_shadowed(relationship.source, shadowed_paths):
            continue
        if relationship.target.path in deleted_paths:
            continue
        if relationship.source in overlay_relationship_sources:
            continue
        if relationship not in seen:
            merged.append(relationship)
            seen.add(relationship)
    return merged


def _source_key(source: SourceRange) -> tuple[str, int, int]:
    return (source.path, source.start_line, source.end_line)


def _symbol_is_shadowed(symbol: SymbolKey, shadowed_paths: set[str]) -> bool:
    return symbol.path in shadowed_paths
