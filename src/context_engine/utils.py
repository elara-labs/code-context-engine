"""Shared utilities for CCE."""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Sequence

# SQLite SQLITE_MAX_VARIABLE_NUMBER defaults to 999; stay safely under.
_SQL_PARAM_BATCH = 500


def batched_params(items: Sequence, size: int = _SQL_PARAM_BATCH) -> Iterator[list]:
    """Yield successive chunks of *items* for safe SQLite IN-clause usage."""
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def atomic_write_text(path: Path, data: str) -> None:
    """Write `data` to `path` via a tempfile + os.replace.

    A plain `path.write_text(data)` truncates the target before writing, so a
    crash mid-write leaves a zero-byte or partial file. The next load reads
    that as `{}` and silently loses everything. The tempfile-then-rename
    pattern keeps the existing file intact until the new one is fully on
    disk; the rename is atomic on POSIX.

    Creates the parent directory if it doesn't exist (or was deleted by a
    concurrent process between an earlier mkdir and this call).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup if anything went wrong before the rename.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def resolve_cce_binary() -> str:
    """Find the globally installed cce binary path.

    Checks user-local then system install paths across both Linux and macOS
    (Homebrew on Apple Silicon installs to /opt/homebrew/bin), then PATH,
    then sys.argv[0] if it looks like cce, then a bare "cce" fallback.
    """
    candidates = [
        Path.home() / ".local" / "bin" / "cce",   # pipx / uv tool default (Linux + macOS)
        Path("/opt/homebrew/bin/cce"),            # macOS Homebrew on Apple Silicon
        Path("/usr/local/bin/cce"),               # macOS Homebrew on Intel + Linux /usr/local
        Path("/opt/local/bin/cce"),               # MacPorts
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("cce")
    if found:
        return found
    arg0 = Path(sys.argv[0]).resolve()
    if arg0.name in ("cce", "code-context-engine"):
        return str(arg0)
    return "cce"
