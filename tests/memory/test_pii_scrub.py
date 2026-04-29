"""Tests for memory.db.scrub_pii — process-global PII redaction toggle
applied to decisions / turn_summaries / code_areas / session rollups.
"""
from __future__ import annotations

import pytest

from context_engine.memory import db as memory_db


@pytest.fixture(autouse=True)
def _reset_toggle():
    """Ensure the module-level flag is reset between tests so order
    doesn't matter and parallel runs (xdist) stay deterministic.
    """
    yield
    memory_db.set_pii_redaction(True)


def test_scrub_redacts_emails_and_ips_when_enabled():
    memory_db.set_pii_redaction(True)
    out = memory_db.scrub_pii(
        "Spoke with alice@example.com about the 203.0.113.42 incident."
    )
    assert "alice@example.com" not in out
    assert "203.0.113.42" not in out
    assert "[REDACTED:EMAIL]" in out
    assert "[REDACTED:IPV4]" in out


def test_scrub_is_noop_when_disabled():
    memory_db.set_pii_redaction(False)
    text = "Email alice@example.com — server 203.0.113.42 is up"
    assert memory_db.scrub_pii(text) == text


def test_scrub_handles_empty_input():
    memory_db.set_pii_redaction(True)
    assert memory_db.scrub_pii("") == ""
    assert memory_db.scrub_pii(None) is None  # type: ignore[arg-type]


def test_scrub_is_idempotent():
    """Re-scrubbing already-scrubbed text doesn't double-redact."""
    memory_db.set_pii_redaction(True)
    once = memory_db.scrub_pii("alice@example.com")
    twice = memory_db.scrub_pii(once)
    assert once == twice


def test_scrub_preserves_clean_text_byte_for_byte():
    memory_db.set_pii_redaction(True)
    text = "Refactor auth to use JWT. Document in docs/auth.md."
    assert memory_db.scrub_pii(text) == text
