"""Tests for progressive disclosure budgets."""

from context_engine.retrieval import resolve_context_budget


def test_resolve_context_budget_uses_level_default():
    assert resolve_context_budget(level="summary") == 1500
    assert resolve_context_budget(level="standard") == 6000


def test_resolve_context_budget_honors_explicit_lower_cap():
    assert resolve_context_budget(level="full", max_tokens=2000) == 2000


def test_resolve_context_budget_keeps_level_when_explicit_cap_is_higher():
    assert resolve_context_budget(level="summary", max_tokens=9000) == 1500
