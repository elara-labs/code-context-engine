"""Heuristic decision extractor — zero-LLM, zero-cloud.

Scans natural language text for decision-like patterns and extracts
(decision, reason) pairs. Used by the compression pipeline to auto-populate
the decisions table without user action.

All patterns require an explicit reason clause (because/since/as/so that)
to keep precision high and avoid false positives on code and tool outputs.
"""
from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r'(?<=[.!?\n])\s+')
_MIN_SENT_LEN = 20

# 'as\b' excluded — too noisy ("use chi as the router" is not a decision)
_REASON_CLAUSE = r'(?:because(?:\s+of)?|since|so\s+that|given\s+that)'

# Fast pre-check keywords — skip pattern loop if none present in sentence
_REASON_KEYWORDS = frozenset(["because", "since", "so that", "given that"])

_PATTERNS: list[re.Pattern[str]] = [
    # "chose/choose/choosing/chosen X over Y because Z"
    re.compile(
        r'\b(?:choos(?:e|ing|en)?|chose(?:n)?)\s+(.+?)\s+over\s+\S+(?:\s+\S+){0,4}\s+' + _REASON_CLAUSE + r'\s+(.+)',
        re.I,
    ),
    # "decided to X because Z"
    re.compile(r'\bdecided?\s+to\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "went with X because Z"
    re.compile(r'\bwent\s+with\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "going with X because Z"
    re.compile(r'\bgo(?:ing)?\s+with\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "use/using X because Z"
    re.compile(r'\b(?:use|using)\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "X instead of Y because Z"
    re.compile(
        r'\b(.+?)\s+instead\s+of\s+\S+(?:\s+\S+){0,3}\s+' + _REASON_CLAUSE + r'\s+(.+)',
        re.I,
    ),
    # "prefer/preferred X because Z"
    re.compile(r'\bprefer(?:red|ring)?\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "switched to X because Z"
    re.compile(r'\bswitched?\s+to\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "will use/go with X because Z"
    re.compile(r'\bwill\s+(?:use|go\s+with)\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
    # "opted for X because Z"
    re.compile(r'\bopted?\s+for\s+(.+?)\s+' + _REASON_CLAUSE + r'\s+(.+)', re.I),
]

_MAX_FRAGMENT = 200


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= _MIN_SENT_LEN]


def _clean(text: str) -> str:
    text = re.sub(r'\s+', ' ', text.strip())  # normalize internal whitespace
    return re.sub(r'[\s.,;:]+$', '', text)[:_MAX_FRAGMENT]


def extract_decisions(text: str) -> list[tuple[str, str]]:
    """Return (decision, reason) pairs found in *text*.

    Scans sentence by sentence; first matching pattern per sentence wins.
    Deduplicates on decision text (case-insensitive).
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for sentence in _split_sentences(text):
        # Fast pre-check: skip pattern loop if no reason keyword present
        lower = sentence.lower()
        if not any(kw in lower for kw in _REASON_KEYWORDS):
            continue
        for pattern in _PATTERNS:
            m = pattern.search(sentence)
            if m:
                decision = _clean(m.group(1))
                reason = _clean(m.group(2))
                if not decision or not reason:
                    continue
                key = decision.lower()
                if key not in seen:
                    seen.add(key)
                    results.append((decision, reason))
                break

    return results
