"""Tests for indexer.secrets — filename-level + content-level credential
detection used during indexing.

The whole module is conservative on purpose: false positives (over-redaction
of docs/README examples) are recoverable; false negatives (a real secret
leaked into the vector DB) are not. Tests reflect that asymmetry — when in
doubt the test asserts redaction, not skip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from context_engine.indexer.secrets import (
    is_secret_file,
    redact_secrets,
    scan_and_redact,
)


# ── Filename-level detection ────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".env.production", ".env.test",
    "credentials.json", "credentials.yaml", "secrets.yml",
    "service-account.json", "kube-config", ".git-credentials",
    ".npmrc", ".pypirc", ".netrc", "auth.json",
    "id_rsa", "private.pem", "server.key", "client.crt",
    "keystore.p12", "bundle.pfx", "store.jks", "ca.cer",
    "key.gpg", "secret.kdbx", "putty.ppk",
])
def test_secret_files_are_skipped(name):
    """Common credential filenames are flagged regardless of path."""
    assert is_secret_file(Path(name))
    assert is_secret_file(Path("subdir") / name)


@pytest.mark.parametrize("name", [
    "main.py", "README.md", "config.yaml", "package.json",
    "Dockerfile", "Makefile", "test_something.py",
])
def test_innocent_files_are_not_skipped(name):
    """Ordinary source files don't match the credential heuristics."""
    assert not is_secret_file(Path(name))


def test_case_insensitive_filename_match():
    """Real-world filesystems are case-insensitive on Windows/macOS — so
    `.ENV` and `Credentials.JSON` should match too.
    """
    assert is_secret_file(Path(".ENV"))
    assert is_secret_file(Path("Credentials.JSON"))
    assert is_secret_file(Path("ID_RSA.PEM"))


# ── Content-level redaction ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("AWS_KEY = AKIA1234567890ABCDEF", "AWS_ACCESS_KEY"),
    ("export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "GITHUB_PAT"),
    ('jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjF9.signature_part"', "JWT"),
    # Google API key = "AIza" prefix + exactly 35 chars.
    ("AIza" + "x" * 35, "GOOGLE_API_KEY"),
    ("xoxb-1234567890-abcdef-1234567890abcdef1234", "SLACK_TOKEN"),
])
def test_known_credentials_redacted(text, kind):
    out, fired = redact_secrets(text)
    assert kind in fired
    assert "[REDACTED:" + kind + "]" in out


def test_aws_key_replaces_full_match_not_just_prefix():
    """Regression: capture group on AWS prefix used to cause partial
    replacement that leaked the 16-char suffix.
    """
    out, fired = redact_secrets("key=AKIAQ4ZRTPPPK1ABCDEF")
    assert "AKIAQ4ZRTPPPK1ABCDEF" not in out
    assert fired == ["AWS_ACCESS_KEY"]


def test_private_key_block_redacted_whole():
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
        "KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQJAIJLixBy2qpFoS4DSmoEm\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, fired = redact_secrets(text)
    assert "[REDACTED:PRIVATE_KEY_BLOCK]" in out
    assert "BEGIN RSA PRIVATE KEY" not in out


def test_generic_credential_pattern():
    text = 'config["password"] = "sup3r-s3cr3t-p4ssw0rd-12345"'
    out, fired = redact_secrets(text)
    assert fired == ["GENERIC_CREDENTIAL"]
    assert 'config["password"] = "[REDACTED:GENERIC_CREDENTIAL]"' in out


def test_innocent_text_is_untouched():
    text = "def authenticate(user, password):\n    return user.check(password)"
    out, fired = redact_secrets(text)
    assert out == text
    assert fired == []


# ── Placeholder false-positive guards ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    'api_key = "your-api-key-here"',
    'secret = "your_secret_value"',
    'token = "my-token-goes-here"',
    'password = "<your-password>"',
    'API_KEY = "REPLACE_WITH_YOUR_KEY"',
    'PASSWORD = "xxxxxxxxxxxxxxxx"',
    'SECRET = "changeme"',
    'api_key: "sk-test_NOT_REAL_PLACEHOLDER"',
    'AWS_KEY = AKIAIOSFODNN7EXAMPLE',  # real-looking but contains EXAMPLE
])
def test_placeholders_are_not_redacted(text):
    """README/docs/example values shouldn't be redacted — that breaks
    indexing of legitimate documentation.
    """
    out, fired = redact_secrets(text)
    assert fired == [], f"unexpected redaction in {text!r}: fired={fired}"
    assert out == text


# ── scan_and_redact integration ────────────────────────────────────────────

def test_scan_and_redact_skips_secret_filename(tmp_path):
    """Filename match returns None for content (file should be skipped)."""
    out, labels = scan_and_redact(tmp_path / ".env", "DB_PASSWORD=hunter2")
    assert out is None
    assert labels == ["SECRET_FILENAME"]


def test_scan_and_redact_redacts_inline_secrets(tmp_path):
    """Non-secret filename + inline credential → redacted content returned."""
    text = "# comment\ntoken = ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
    out, labels = scan_and_redact(tmp_path / "config.py", text)
    assert out is not None
    assert "ghp_" not in out
    assert "GITHUB_PAT" in labels


def test_scan_and_redact_passes_clean_content_through(tmp_path):
    """Clean file + clean content → bytes-identical pass-through."""
    text = "def foo():\n    return 42\n"
    out, labels = scan_and_redact(tmp_path / "main.py", text)
    assert out == text
    assert labels == []


# ── Extension hooks ────────────────────────────────────────────────────────

# ── PII redaction ──────────────────────────────────────────────────────────

from context_engine.indexer.secrets import redact_pii  # noqa: E402


@pytest.mark.parametrize("text,kind", [
    ("Contact alice@example.com", "EMAIL"),
    ("Server is at 203.0.113.42", "IPV4"),
    ("SSN: 123-45-6789", "SSN"),
    ("Card 4532015112830366", "CREDIT_CARD"),  # Luhn-valid
    ("Call +1 555-867-5309", "PHONE_E164"),
])
def test_pii_known_patterns(text, kind):
    out, fired = redact_pii(text)
    assert kind in fired, f"{kind} not fired on {text!r}; got {fired}"
    assert f"[REDACTED:{kind}]" in out


def test_pii_localhost_ips_are_not_redacted():
    """Common dev IPs (127.0.0.1, 0.0.0.0, 10.0.0.1, 192.168.x.x) survive."""
    out, fired = redact_pii(
        "Server: 127.0.0.1, fallback 0.0.0.0, lan 192.168.1.5"
    )
    assert fired == []
    assert "127.0.0.1" in out
    assert "0.0.0.0" in out
    assert "192.168.1.5" in out


def test_pii_credit_card_requires_luhn():
    """Long digit runs that fail Luhn (order numbers, hashes) aren't
    redacted — would be a false positive otherwise.
    """
    out, fired = redact_pii("Order #12345678901234 was shipped")
    assert fired == []
    assert "12345678901234" in out


def test_pii_clean_text_passes_through():
    text = "Refactor auth middleware to use JWT instead of sessions."
    out, fired = redact_pii(text)
    assert out == text
    assert fired == []


def test_extra_patterns_are_honoured():
    """Users can add per-project patterns via config; they fire alongside
    the built-ins and must use the same return shape.
    """
    import re
    custom = [(re.compile(r"\binternal_token_[a-z0-9]{8}\b"), "INTERNAL_TOKEN")]
    out, fired = redact_secrets("the value is internal_token_deadbeef now",
                                extra_patterns=custom)
    assert "INTERNAL_TOKEN" in fired
    assert "internal_token_deadbeef" not in out
