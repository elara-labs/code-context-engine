"""Provider-independent structural context models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SymbolKey:
    qualified_name: str
    kind: str | None
    path: str | None = None
    signature: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class SourceRange:
    path: str
    start_line: int
    end_line: int
    content: str | None = None


@dataclass(frozen=True)
class Relationship:
    source: SymbolKey
    target: SymbolKey
    kind: str


@dataclass(frozen=True)
class StructuralContext:
    sources: list[SourceRange] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    impact: list[SymbolKey] = field(default_factory=list)
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
