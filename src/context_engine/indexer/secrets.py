"""Secret detection at index time.

Two layers:

  1. **Filename-based skipping** — files whose names match well-known
     credential patterns (.env*, *.pem, secrets.yml, …) are never read,
     never embedded, never served. This is the cheap first line of
     defence and catches the most common leak.

  2. **Content-based redaction** — for files that DO get indexed, lines
     containing what look like AWS keys, GitHub tokens, JWTs, etc. get
     replaced with `[REDACTED:<reason>]` before chunking. The chunker
     and embedder never see the secret value.

Both layers are conservative on purpose — false positives (over-redaction
of innocent code) are recoverable; false negatives (a real secret leaked
into the vector DB) are not. When in doubt, redact.

Tunable via config:
  · indexer.redact_secrets (default: True) — master switch.
  · indexer.secret_extra_patterns (default: []) — user-added regexes
    for content scanning, merged with the built-in set.
"""
from __future__ import annotations

import re
from pathlib import Path

# ── Filename-level skip list ────────────────────────────────────────────────
# Glob-style suffixes / exact names that almost always mean "credentials".
# Match is case-insensitive against the full filename (not full path).

# Exact filenames (case-insensitive). Entire file is skipped.
_SECRET_FILENAMES = frozenset({
    # Dotenv family (covers .env, .env.local, .env.production, etc. — see _SECRET_PREFIXES)
    ".npmrc", ".pypirc", ".netrc",
    # Cloud / infra
    "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml",
    "service-account.json", "gcp-key.json",
    "kube-config", "kubeconfig",
    # CI / app config that frequently holds tokens
    "auth.json",
    # Git config can carry remote tokens
    ".git-credentials",
    # SSH private keys frequently sit extension-less in ~/.ssh.
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_xmss",
})

# Filename starts with any of these → skip (handles .env, .env.local, etc.).
_SECRET_PREFIXES = (".env",)

# File extensions whose presence is a strong signal of a key/cert. Skip outright.
_SECRET_EXTENSIONS = frozenset({
    ".pem", ".key", ".crt", ".cer", ".der",
    ".p12", ".pfx",          # PKCS#12 cert bundles
    ".jks", ".keystore",     # Java keystores
    ".pgp", ".asc", ".gpg",  # PGP keys
    ".kdbx",                 # KeePass
    ".ppk",                  # PuTTY private keys
})


def is_secret_file(path: Path) -> bool:
    """True if the filename alone is enough to classify as credentials.

    Operates on basename only — callers don't need to pre-normalise the
    path. Case-insensitive across the board (Windows/macOS reality).
    """
    name = path.name.lower()
    if name in _SECRET_FILENAMES:
        return True
    if path.suffix.lower() in _SECRET_EXTENSIONS:
        return True
    for prefix in _SECRET_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


# ── Content-level redaction ─────────────────────────────────────────────────
# Patterns are tuples of (regex, label). Label appears in the redaction
# placeholder so users can tell *what kind* of secret was scrubbed without
# leaking the value itself.
#
# Conservative ordering: patterns earlier in the list win — specific
# vendor formats before generic high-entropy heuristics.

_CONTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # AWS access keys — fixed prefix + 16 base32 chars. Non-capturing
    # group on the prefix so the whole match is the credential value
    # (otherwise we'd only replace AKIA and leak the 16-char suffix).
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY"),
    # AWS secret keys — 40 base64 chars after "aws_secret_access_key"-ish context.
    (re.compile(
        r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"
    ), "AWS_SECRET_KEY"),
    # GitHub tokens (classic + fine-grained + app + OAuth).
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GITHUB_PAT"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"), "GITHUB_FINE_GRAINED_PAT"),
    (re.compile(r"\b(ghs|gho|ghu|ghr)_[A-Za-z0-9]{36}\b"), "GITHUB_OAUTH"),
    # Slack tokens.
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "SLACK_TOKEN"),
    # Stripe live keys (test keys are deliberately not matched — they're
    # safe to commit and matching them would over-redact every Stripe
    # quickstart in the wild).
    (re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{24,}\b"), "STRIPE_LIVE_KEY"),
    # OpenAI / Anthropic API keys.
    (re.compile(r"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b"), "OPENAI_KEY"),
    (re.compile(r"\bsk-ant-(api03|admin01)-[A-Za-z0-9_\-]{93,}\b"), "ANTHROPIC_KEY"),
    # Google API keys.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "GOOGLE_API_KEY"),
    # Generic JWT — three base64url segments separated by dots.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "JWT"),
    # Private key blocks (catch even if filename slipped past the skip list).
    (re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
    ), "PRIVATE_KEY_BLOCK"),
    # Generic high-signal "looks like a secret" heuristic — variable
    # named after a credential, assigned to a long opaque string. Skips
    # placeholders ("xxx", "your-key-here", "<insert>") so the typical
    # README example doesn't trigger a redaction.
    #
    # The keyword is matched anywhere on the line (no `^` anchor) so
    # patterns like `config["password"] = "..."` and dict literals fire.
    # Word boundary on the left edge keeps "ssh_password_dialog" from
    # matching as a fake password assignment.
    (re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|"
        r"private[_-]?key|auth[_-]?token|client[_-]?secret)\b"
        r"['\"\]\s]*[:=]\s*['\"]([^'\"\s]{16,})['\"]"
    ), "GENERIC_CREDENTIAL"),
]


# Common placeholder values that should NOT be redacted even if they
# match the generic pattern. Reduces noise in templates / README files.
_PLACEHOLDER_VALUES = frozenset({
    "your-api-key", "your_api_key", "your-key-here", "your_key_here",
    "<your-key>", "<api-key>", "<your_key>", "<api_key>",
    "xxxxxxxxxxxxxxxx", "0000000000000000",
    "changeme", "change-me", "change_me",
    "placeholder", "example", "sample",
})


_PLACEHOLDER_SUBSTRINGS = (
    "placeholder", "example", "fake", "dummy", "sample",
    "changeme", "change-me", "change_me", "not_real",
    "not-real", "redacted", "<your", "<api",
    # README phrasing variants
    "your_key", "your-key", "your_secret", "your-secret",
    "your_token", "your-token", "your_api", "your-api",
    "your_password", "your-password",
    "replace_with", "replace-with", "insert_your", "insert-your",
)


def _starts_with_placeholder_prefix(value: str) -> bool:
    # Any string that opens with "your-" / "your_" / "my-" / "my_" is a
    # tutorial-style placeholder, not a credential. README examples like
    # "your-api-key-here" or "my_secret_value" all match.
    for prefix in ("your-", "your_", "my-", "my_"):
        if value.startswith(prefix):
            return True
    return False


def _is_placeholder(value: str) -> bool:
    v = value.strip("'\"<>").lower()
    if v in _PLACEHOLDER_VALUES:
        return True
    # Repeated single character ("xxxxxxxxxx", "0000000000") is almost
    # always a placeholder, never a real key.
    if len(set(v)) <= 2:
        return True
    # Substring heuristic — README/docs frequently embed credential-shaped
    # strings with telltale words. Better to over-skip these than to redact
    # innocent documentation.
    for needle in _PLACEHOLDER_SUBSTRINGS:
        if needle in v:
            return True
    if _starts_with_placeholder_prefix(v):
        return True
    return False


def redact_secrets(
    text: str,
    *,
    extra_patterns: list[tuple[re.Pattern, str]] | None = None,
) -> tuple[str, list[str]]:
    """Replace credential-shaped substrings with `[REDACTED:LABEL]`.

    Returns (redacted_text, labels) — `labels` enumerates which pattern
    classes fired, so callers can record telemetry without leaking the
    secret value. Empty list means the text is clean.

    The redaction is line-aware for the generic-credential pattern:
    the entire value gets replaced, but the variable name and assignment
    syntax are preserved so chunked code still parses.
    """
    if not text:
        return text, []
    patterns = list(_CONTENT_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    out = text
    fired: list[str] = []

    for pattern, label in patterns:
        def _sub(match: re.Match, _label: str = label) -> str:
            # If the match has a single capture group, that's the actual
            # credential value — preserve everything around it.
            if match.lastindex:
                value = match.group(match.lastindex)
                if _is_placeholder(value):
                    return match.group(0)
                fired.append(_label)
                return match.group(0).replace(value, f"[REDACTED:{_label}]")
            full = match.group(0)
            if _is_placeholder(full):
                return full
            fired.append(_label)
            return f"[REDACTED:{_label}]"

        out = pattern.sub(_sub, out)

    return out, fired


# ── PII patterns ────────────────────────────────────────────────────────────
# Used by memory.db writes (decisions / turn summaries / code areas) so
# personal data captured during a session doesn't end up persisted in
# searchable form. Lighter touch than secret detection — only the most
# unambiguous patterns. Free-form text shouldn't be aggressively scrubbed.

_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email addresses — simple but effective. Matches RFC-mostly-compliant
    # addresses; over-matches a tiny bit (won't reject quoted local-parts)
    # but that's fine for redaction.
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
    # IPv4 — four 1-3 digit groups. Won't match localhost or 127.0.0.1
    # specifically because those are useful in dev notes.
    (re.compile(
        r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b|10\.0\.0\.1\b|192\.168\.\d+\.\d+\b)"
        r"(?:[1-9]\d?|1\d{2}|2[0-4]\d|25[0-5])"
        r"(?:\.(?:\d{1,3})){3}\b"
    ), "IPV4"),
    # Credit-card-shaped 13-19 digit runs (with optional spaces/dashes).
    # Filtered through Luhn check to avoid false positives on order
    # numbers, hashes, etc.
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "CREDIT_CARD"),
    # US-style SSN.
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),
    # E.164 phone numbers (with leading +).
    (re.compile(r"\+\d{1,3}[ -]?\(?\d{1,4}\)?[ -]?\d{3,4}[ -]?\d{3,4}\b"), "PHONE_E164"),
]


def _passes_luhn(digits: str) -> bool:
    """Luhn check for credit-card validation. Skips invalid candidates so
    we don't redact every long number. Strips non-digits first.
    """
    digits = re.sub(r"\D", "", digits)
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact_pii(
    text: str,
    *,
    extra_patterns: list[tuple[re.Pattern, str]] | None = None,
) -> tuple[str, list[str]]:
    """Replace common PII (emails, IPs, credit cards, SSNs, phones) with
    `[REDACTED:LABEL]`. Same return shape as `redact_secrets`.

    Lighter touch than secret detection: only the unambiguous patterns
    fire, and credit-card candidates are Luhn-validated so order numbers
    and SHA hashes don't get clobbered.
    """
    if not text:
        return text, []
    patterns = list(_PII_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    out = text
    fired: list[str] = []

    for pattern, label in patterns:
        def _sub(match: re.Match, _label: str = label) -> str:
            value = match.group(0)
            # Credit-card pattern needs Luhn validation to avoid false
            # positives. Other PII patterns are accepted as-is.
            if _label == "CREDIT_CARD" and not _passes_luhn(value):
                return value
            fired.append(_label)
            return f"[REDACTED:{_label}]"

        out = pattern.sub(_sub, out)

    return out, fired


# ── Convenience: combined check for the indexer ────────────────────────────

def scan_and_redact(
    file_path: Path,
    content: str,
    *,
    extra_patterns: list[tuple[re.Pattern, str]] | None = None,
) -> tuple[str | None, list[str]]:
    """Indexer-facing entrypoint.

    Returns (text_or_None, labels). `text_or_None` is None when the file
    should be skipped entirely (filename-level secret), otherwise the
    redacted content (which equals `content` if nothing fired). `labels`
    is the list of pattern labels that triggered.
    """
    if is_secret_file(file_path):
        return None, ["SECRET_FILENAME"]
    return redact_secrets(content, extra_patterns=extra_patterns)
