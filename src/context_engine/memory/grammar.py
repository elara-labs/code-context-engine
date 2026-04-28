"""Deterministic, model-free prose compression for memory.db rows.

Approach (modelled after cavemem's "caveman grammar"):

  1. Tokenise input into typed tokens. Code, paths, URLs, versions, dates,
     identifiers, numbers, and headings are *structured* — they're
     preserved byte-for-byte through compress(). Only `prose` tokens are
     transformed.

  2. Apply prose transformations at one of three intensity levels:
       lite  — drop articles only (a, an, the)
       full  — drop articles + grammatical fillers (default)
       ultra — full + abbreviation lexicon

  3. Round-trip property: structured tokens survive compress() unchanged.
     expand() is a light reversal that restores abbreviations and tidies
     spacing, but is *not* required to recover dropped articles/fillers —
     the compression is intentionally lossy on non-content words.

The whole module is pure: no IO, no external deps, no LLM call. It's
deterministic — same input + same level always yields the same output.

Used by:
  · record_decision dual-write (mcp_server.py)
  · compress_turn after extractive summary (compressor.py)
  · session_recall / session_timeline output formatting (read-side expand)

See `tests/memory/test_grammar.py` for the corpus + round-trip tests.
"""
from __future__ import annotations

import re
from typing import Literal


Level = Literal["lite", "full", "ultra"]

# Single source of truth for the compression level applied to memory.db
# storage. Imported by mcp_server (record_decision), compressor (turn +
# rollup), migrate (legacy import), and the bench (canonical-form match).
# All five paths must agree, otherwise the bench gives misleading numbers
# and the same decision can land in storage at different shapes.
DEFAULT_LEVEL: Level = "lite"


# ── Token classes ──────────────────────────────────────────────────────────
# Any string fragment that matches a structured-token pattern is preserved
# byte-for-byte through compress(). Order matters — patterns earlier in the
# list win.

_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+?`")

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:?\d{2})?)?"
)
_VERSION_RE = re.compile(r"v?\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?")
# Path: requires *substantive* segments (≥2 chars each, or a clear file
# extension). Single-slash bare-word pairs like the abbreviations `b/c`,
# `w/o`, `w/` deliberately don't match — they flow through as prose so
# the lexicon expansion sees them. Same constraint excludes `/c` etc.
_PATH_RE = re.compile(
    r"(?:\.{1,2}/|/)[A-Za-z0-9_\-]{2,}(?:/[A-Za-z0-9_./\-]+)*"
    r"|[A-Za-z0-9_\-]{2,}/[A-Za-z0-9_\-]{2,}(?:/[A-Za-z0-9_./\-]+)*"
    r"|[A-Za-z0-9_\-]{2,}/[A-Za-z0-9_\-]+\.[A-Za-z]{1,5}\b"
)
# Number with unit attached: 100ms, 5GB, 0.3s. Distinct from a bare number
# so we can let bare numbers stay in prose.
_NUMBER_UNIT_RE = re.compile(r"\d+(?:\.\d+)?[A-Za-z]{1,4}\b")
# Identifier: CamelCase / snake_case / dotted.path. Must contain an
# uppercase, a digit, an underscore, or a dot to be flagged as identifier
# (otherwise a single lowercase word is just prose).
_IDENT_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+|"
    r"[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|"
    r"[A-Za-z]+_[A-Za-z0-9_]+"
)

# ── Stop-word lexicons ─────────────────────────────────────────────────────

_ARTICLES = frozenset({"a", "an", "the"})

# Grammatical fillers that carry no topic signal in coding-session prose.
# Conservative — topic words (code, auth, database, scale, etc.) are NOT here.
_FILLERS = _ARTICLES | frozenset({
    # auxiliaries / modals
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had",
    "will", "would", "shall", "should", "may", "might", "must",
    # connectives / weak verbs
    "of", "to", "as", "by", "on", "in", "at", "for", "with", "from",
    "and", "or", "but", "if", "than", "then",
    "that", "this", "these", "those",
    # mild redundancies frequently in decision text
    "just", "very", "quite", "really", "actually", "basically",
})

# Aggressive drop set used at ultra. Adds discourse fillers, common
# pronouns, weak verbs. Trades a few recall points for ~3× the byte
# savings on conversational prose.
_FILLERS_ULTRA = _FILLERS | frozenset({
    "also", "still", "now", "when", "while", "since",
    "i", "we", "you", "he", "she", "it", "they", "them",
    "our", "their", "its", "my", "your", "his", "her",
    "me", "us", "him",
    "do", "does", "did",
    "via", "into", "onto", "upon", "over", "under", "through",
    "much", "more", "most", "less", "least", "some", "any", "all",
    "such", "both", "each", "every", "other", "another",
    "here", "there",
    "let", "get", "got", "take", "took", "give", "gave",
    "truly", "absolutely", "completely", "totally", "entirely",
})

# Abbreviation lexicon for ultra. expand() uses the inverse to restore on read.
_ABBREVIATE: dict[str, str] = {
    # Connectives / discourse
    "because": "b/c",
    "however": "but",
    "therefore": "so",
    "additionally": "+",
    "approximately": "~",
    "particularly": "esp",
    "specifically": "esp",
    "currently": "now",
    "previously": "before",
    "subsequently": "then",
    "throughout": "thru",
    "instead": "vs",
    "without": "w/o",
    "with": "w/",
    "between": "btwn",
    "probably": "likely",
    # Programming jargon
    "configuration": "config",
    "implementation": "impl",
    "documentation": "docs",
    "repository": "repo",
    "performance": "perf",
    "production": "prod",
    "development": "dev",
    "environment": "env",
    "infrastructure": "infra",
    "architecture": "arch",
    # Note: deliberately NOT abbreviating "authentication"/"authorization"/
    # "library" — their natural abbreviations ("auth"/"authz"/"lib") are
    # already real domain words, so expanding "auth" back to "authentication"
    # corrupts text the user wrote as "auth" intentionally.
    "framework": "fw",
    "function": "fn",
    "variable": "var",
    "parameter": "param",
    "argument": "arg",
    "request": "req",
    "response": "resp",
    "database": "db",
    "language": "lang",
    "directory": "dir",
    "execution": "exec",
    "operation": "op",
    "management": "mgmt",
    "deployment": "deploy",
    "synchronisation": "sync",
    "synchronization": "sync",
    "asynchronous": "async",
    "synchronous": "sync",
    "concurrent": "conc",
    "optimisation": "opt",
    "optimization": "opt",
    "automatically": "auto",
    "available": "avail",
    "compatibility": "compat",
    "incompatible": "incompat",
    "information": "info",
    "reference": "ref",
    "different": "diff",
    "specific": "spec",
    "important": "imp",
    "consider": "cons",
    "additional": "extra",
    "responsible": "resp",
}

_EXPAND: dict[str, str] = {abbr: word for word, abbr in _ABBREVIATE.items()}


# ── Tokenisation ───────────────────────────────────────────────────────────

# A token is a (kind, text) tuple. `kind` drives whether it survives
# compress() unchanged or is transformed.
_TokenKind = Literal[
    "fence", "inline_code", "url", "datetime", "version", "path",
    "number_unit", "identifier", "prose",
]


def _tokenise(text: str) -> list[tuple[str, str]]:
    """Split `text` into (kind, fragment) tuples. Concatenating all
    fragments reproduces the input exactly.

    Strategy: in priority order, find structured-token spans and treat the
    gaps between them as `prose`. The priority order is the order patterns
    appear in `_PATTERNS` below — fenced code wins over everything else,
    then inline code, then URL/datetime/version/path/number+unit/identifier.
    """
    if not text:
        return []
    patterns = [
        ("fence", _FENCE_RE),
        ("inline_code", _INLINE_CODE_RE),
        ("url", _URL_RE),
        ("datetime", _DATETIME_RE),
        ("version", _VERSION_RE),
        ("path", _PATH_RE),
        ("number_unit", _NUMBER_UNIT_RE),
        ("identifier", _IDENT_RE),
    ]
    spans: list[tuple[int, int, str]] = []  # (start, end, kind)
    occupied = [False] * len(text)
    for kind, regex in patterns:
        for m in regex.finditer(text):
            s, e = m.start(), m.end()
            if any(occupied[s:e]):
                continue  # overlaps with a higher-priority span
            spans.append((s, e, kind))
            for i in range(s, e):
                occupied[i] = True
    spans.sort()

    tokens: list[tuple[str, str]] = []
    cursor = 0
    for s, e, kind in spans:
        if cursor < s:
            tokens.append(("prose", text[cursor:s]))
        tokens.append((kind, text[s:e]))
        cursor = e
    if cursor < len(text):
        tokens.append(("prose", text[cursor:]))
    return tokens


# ── Prose transformation ───────────────────────────────────────────────────


def _transform_prose(text: str, level: Level) -> str:
    """Apply level-specific transformations to a prose fragment.

    Splits on whitespace and filters / abbreviates words. Punctuation
    attached to a word (e.g. "auth,") is preserved by separating it before
    matching against the stop-word set.
    """
    if not text.strip():
        return text  # pure whitespace passthrough
    if level == "lite":
        drop_set = _ARTICLES
        do_abbreviate = False
    elif level == "full":
        drop_set = _FILLERS
        do_abbreviate = False
    else:  # "ultra"
        drop_set = _FILLERS_ULTRA
        do_abbreviate = True

    out: list[str] = []
    # Tokenise on whitespace boundaries while preserving the whitespace
    # itself, so a prose fragment like "use the   JWT" becomes "use   JWT"
    # not "useJWT".
    parts = re.split(r"(\s+)", text)
    for part in parts:
        if not part:
            continue
        if part.isspace():
            out.append(part)
            continue
        # Strip leading/trailing punctuation so "the," / "auth." match.
        leading_match = re.match(r"^[^\w]*", part)
        trailing_match = re.search(r"[^\w]*$", part)
        leading = leading_match.group() if leading_match else ""
        trailing = trailing_match.group() if trailing_match else ""
        core = part[len(leading): len(part) - len(trailing)]
        if not core:
            out.append(part)
            continue
        lower = core.lower()
        if lower in drop_set:
            # Drop the word but keep attached punctuation if any (e.g. ",").
            kept = leading + trailing
            if kept:
                out.append(kept)
            continue
        if do_abbreviate and lower in _ABBREVIATE:
            replaced = _ABBREVIATE[lower]
            # Preserve original capitalisation (Title-case → Title-case)
            if core[0].isupper():
                replaced = replaced[0].upper() + replaced[1:] if replaced else replaced
            out.append(leading + replaced + trailing)
            continue
        out.append(part)

    result = "".join(out)
    # Collapse runs of internal whitespace that resulted from drops, AND
    # collapse multi-space runs in the leading/trailing whitespace so that
    # when this fragment sits next to a structured token (e.g. an
    # identifier) the seam reads as a single space, not two. Newlines are
    # preserved so block layout survives.
    leading_ws = re.match(r"^\s*", result).group()
    trailing_ws = re.search(r"\s*$", result).group()
    middle = result[len(leading_ws): len(result) - len(trailing_ws) if trailing_ws else None]
    middle = re.sub(r"\s+", " ", middle)
    leading_ws = re.sub(r"[ \t]{2,}", " ", leading_ws)
    trailing_ws = re.sub(r"[ \t]{2,}", " ", trailing_ws)
    return leading_ws + middle + trailing_ws


# ── Public API ─────────────────────────────────────────────────────────────


def compress(text: str, level: Level = "full") -> str:
    """Deterministically compress prose while preserving structured tokens.

    Code blocks, file paths, URLs, versions, dates, and identifiers all
    survive compress() byte-for-byte. Only prose words are subject to
    drop-articles / drop-fillers / abbreviate transformations.

    Levels:
      · "lite"  — drop articles (a/an/the)
      · "full"  — drop articles + grammatical fillers (default)
      · "ultra" — full + lexicon abbreviations
    """
    if not text:
        return text
    tokens = _tokenise(text)
    out: list[str] = []
    for kind, frag in tokens:
        if kind == "prose":
            out.append(_transform_prose(frag, level))
        else:
            # Structured tokens pass through byte-for-byte. Crucially we do
            # NOT call _normalise_seams or any whitespace tweak across the
            # whole output — that would collapse spacing *inside* fenced
            # code blocks. Each prose fragment self-collapses internally
            # in _transform_prose; that's enough.
            out.append(frag)
    return "".join(out)


def expand(text: str) -> str:
    """Restore well-known abbreviations to their full forms and tidy
    spacing. Structured tokens pass through unchanged. Lossy: dropped
    articles/fillers are NOT recovered.

    Used on the read side (session_recall, session_timeline) so the agent
    sees natural-ish prose. Stored bytes remain compressed.
    """
    if not text:
        return text
    tokens = _tokenise(text)
    out: list[str] = []
    for kind, frag in tokens:
        if kind != "prose":
            out.append(frag)
            continue
        # Word-by-word abbreviation reversal.
        parts = re.split(r"(\s+)", frag)
        for part in parts:
            if not part or part.isspace():
                out.append(part)
                continue
            leading_match = re.match(r"^[^\w/]*", part)
            trailing_match = re.search(r"[^\w/]*$", part)
            leading = leading_match.group() if leading_match else ""
            trailing = trailing_match.group() if trailing_match else ""
            core = part[len(leading): len(part) - len(trailing)]
            lower = core.lower()
            if lower in _EXPAND:
                full = _EXPAND[lower]
                if core[:1].isupper():
                    full = full[0].upper() + full[1:] if full else full
                out.append(leading + full + trailing)
            else:
                out.append(part)
    return "".join(out)


# ── Helpers ────────────────────────────────────────────────────────────────


def compression_ratio(original: str, compressed: str) -> float:
    """Convenience for benches: fraction of bytes saved (0.0 = no
    compression, 1.0 = compressed to empty). Negative if compressed is
    larger (rare — only possible if the lexicon expands more than it
    drops, which it shouldn't)."""
    if not original:
        return 0.0
    return 1.0 - len(compressed) / len(original)
