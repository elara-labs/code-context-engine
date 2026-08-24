"""Tests for structural base+overlay merge semantics."""

from context_engine.git.diff import GitWorktreeDiff
from context_engine.structural import (
    Relationship,
    SourceRange,
    StructuralContext,
    SymbolKey,
    merge_structural_contexts,
)


def symbol(name: str, path: str) -> SymbolKey:
    return SymbolKey(qualified_name=name, kind="function", path=path)


def source(path: str) -> SourceRange:
    return SourceRange(path=path, start_line=1, end_line=3, content=path)


def relationship(source_symbol: SymbolKey, target_symbol: SymbolKey) -> Relationship:
    return Relationship(source=source_symbol, target=target_symbol, kind="calls")


def test_modified_file_sources_shadow_base_sources():
    merged = merge_structural_contexts(
        base=StructuralContext(
            sources=[source("auth.py"), source("stable.py")],
            provider="base",
        ),
        overlay=StructuralContext(
            sources=[SourceRange("auth.py", 1, 4, "new auth")],
            provider="overlay",
        ),
        diff=GitWorktreeDiff(modified={"auth.py"}),
    )

    assert [item.content for item in merged.sources] == ["new auth", "stable.py"]


def test_deleted_file_sources_and_symbols_are_tombstoned():
    deleted = symbol("deleted", "deleted.py")
    stable = symbol("stable", "stable.py")

    merged = merge_structural_contexts(
        base=StructuralContext(
            sources=[source("deleted.py"), source("stable.py")],
            impact=[deleted, stable],
            provider="base",
        ),
        overlay=StructuralContext(provider="overlay"),
        diff=GitWorktreeDiff(deleted={"deleted.py"}),
    )

    assert [item.path for item in merged.sources] == ["stable.py"]
    assert merged.impact == [stable]


def test_overlay_outgoing_edges_replace_base_outgoing_edges_for_same_symbol():
    base_b = symbol("B", "b.py")
    c = symbol("C", "c.py")
    d = symbol("D", "d.py")
    e = symbol("E", "e.py")

    merged = merge_structural_contexts(
        base=StructuralContext(
            relationships=[relationship(base_b, c), relationship(base_b, d)],
            provider="base",
        ),
        overlay=StructuralContext(
            relationships=[relationship(base_b, c), relationship(base_b, e)],
            provider="overlay",
        ),
        diff=GitWorktreeDiff(modified={"b.py"}),
    )

    assert merged.relationships == [relationship(base_b, c), relationship(base_b, e)]


def test_unmodified_incoming_base_edge_survives_when_target_replaced():
    a = symbol("A", "a.py")
    b = symbol("B", "b.py")
    c = symbol("C", "c.py")

    merged = merge_structural_contexts(
        base=StructuralContext(
            relationships=[relationship(a, b), relationship(b, c)],
            provider="base",
        ),
        overlay=StructuralContext(
            relationships=[relationship(b, c)],
            provider="overlay",
        ),
        diff=GitWorktreeDiff(modified={"b.py"}),
    )

    assert relationship(a, b) in merged.relationships
    assert relationship(b, c) in merged.relationships
    assert len(merged.relationships) == 2


def test_unmodified_incoming_base_edge_drops_when_target_deleted():
    stable = symbol("A", "stable.py")
    deleted = symbol("B", "deleted.py")

    merged = merge_structural_contexts(
        base=StructuralContext(
            relationships=[relationship(stable, deleted)],
            provider="base",
        ),
        overlay=StructuralContext(provider="overlay"),
        diff=GitWorktreeDiff(deleted={"deleted.py"}),
    )

    assert merged.relationships == []
