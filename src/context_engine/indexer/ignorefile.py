"""`.cceignore` parser — gitignore-style patterns for the indexer.

Supports the practical subset of `.gitignore` syntax that covers ~95% of
real-world use:

  · Glob patterns: `*.log`, `temp/*`, `**/build/`
  · Directory matches: `node_modules/` (trailing slash)
  · Comments: lines starting with `#`
  · Blank lines: ignored

Deliberate deviation from strict gitignore: `*` here matches across
path separators (fnmatch behaviour), so `temp/*` excludes everything
under `temp/`, not just direct children. In our experience that's what
users actually want from an indexer ignore file.

NOT supported (intentionally — adds dependency and complexity for
diminishing returns):

  · Negation patterns (`!keep.log`)
  · Anchored patterns (leading `/`) — all patterns match anywhere in the tree
  · Character classes beyond what `fnmatch` provides

Users who need full gitignore semantics can add `pathspec` to their
project and wire a custom matcher; this module covers the common case
without a third-party dependency.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

CCEIGNORE_FILENAME = ".cceignore"


def load_ignore_patterns(project_dir: Path) -> list[str]:
    """Read `.cceignore` from `project_dir` and return its non-comment,
    non-blank lines. Returns an empty list if the file doesn't exist.

    Patterns are returned verbatim (whitespace stripped); matching is
    delegated to `matches_any`.
    """
    path = project_dir / CCEIGNORE_FILENAME
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except OSError:
        return []
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def matches_any(rel_path: str, is_dir: bool, patterns: list[str]) -> bool:
    """True if `rel_path` matches any of the given patterns.

    `rel_path` is the path relative to the project root, using forward
    slashes regardless of platform. `is_dir` distinguishes directories
    so trailing-slash patterns (e.g. `build/`) only match directories.
    """
    if not patterns:
        return False
    # Normalise: forward slashes, no leading "./"
    rel = rel_path.replace("\\", "/").lstrip("./")
    name = rel.rsplit("/", 1)[-1]
    for pat in patterns:
        # Trailing slash → directory-only pattern.
        is_dir_pat = pat.endswith("/")
        p = pat[:-1] if is_dir_pat else pat
        if is_dir_pat and not is_dir:
            continue
        # Pattern with no slash → match against basename anywhere in tree.
        # Pattern with a slash → match against the relative path from root.
        if "/" not in p:
            if fnmatch.fnmatchcase(name, p):
                return True
        else:
            # Strip a leading `/` if user wrote it (anchored), our matcher
            # is implicitly anchored against the project root anyway.
            anchored = p.lstrip("/")
            if fnmatch.fnmatchcase(rel, anchored):
                return True
            # `**` support — fnmatch treats it as `*`. We extend by also
            # trying the pattern with `**/` stripped from the front, so
            # `**/build/foo` matches `build/foo` and `src/build/foo`.
            if anchored.startswith("**/"):
                tail = anchored[3:]
                if fnmatch.fnmatchcase(rel, tail):
                    return True
                if fnmatch.fnmatchcase(rel, f"*/{tail}"):
                    return True
    return False
