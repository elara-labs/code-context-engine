"""Structural provider protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from context_engine.structural.models import StructuralContext


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    available: bool
    detail: str = ""
    metadata: dict = field(default_factory=dict)


class BaseStructuralProvider(Protocol):
    async def status(self, project_root: Path) -> ProviderStatus: ...

    async def explore(self, query: str, project_root: Path) -> StructuralContext: ...

    async def impact(self, symbol: str, project_root: Path) -> StructuralContext: ...
