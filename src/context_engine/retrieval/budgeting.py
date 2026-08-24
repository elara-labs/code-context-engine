"""Progressive disclosure token budgets."""

from __future__ import annotations

from typing import Literal


DisclosureLevel = Literal["summary", "standard", "full"]

DEFAULT_DISCLOSURE_BUDGETS: dict[DisclosureLevel, int] = {
    "summary": 1500,
    "standard": 6000,
    "full": 50000,
}


def resolve_context_budget(
    *,
    level: DisclosureLevel = "standard",
    max_tokens: int | None = None,
) -> int:
    """Return hard token budget for a disclosure level, honoring explicit caps."""
    budget = DEFAULT_DISCLOSURE_BUDGETS[level]
    return min(budget, max_tokens) if max_tokens is not None else budget
