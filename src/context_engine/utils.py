"""Shared utilities for CCE."""
import hashlib
import logging
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


_log = logging.getLogger(__name__)


def _project_slug(project_dir: Path) -> str:
    """Stable per-directory slug: ``<sanitised-basename>-<6hex>``.

    Canonical implementation, also used by ``editors._project_slug`` for
    per-editor config sections. Two projects sharing a basename (``api``,
    ``web``) get distinct slugs. Symlinks are resolved before hashing so
    two paths pointing at the same on-disk directory produce the same slug.
    """
    resolved = project_dir.resolve()
    abs_path = str(resolved)
    h = hashlib.sha256(abs_path.encode()).hexdigest()[:6]
    safe = "".join(
        c if (c.isascii() and (c.isalnum() or c in "-_")) else "-"
        for c in resolved.name
    )
    return f"{safe or 'project'}-{h}"


def project_storage_dir(config: object, project_dir: Path) -> Path:
    """Return the per-project storage directory under ``config.storage_path``.

    Uses a ``<basename>-<6hex>`` slug so two projects sharing the same
    basename (e.g. ``~/work/api`` and ``~/scratch/api``) get separate
    storage directories instead of silently colliding.

    On first call, if the legacy directory (bare basename, no hash suffix)
    exists but the new slug directory does not, the legacy directory is
    renamed in place to preserve existing users' data.
    """
    slug = _project_slug(project_dir)
    storage_root = Path(config.storage_path)  # type: ignore[union-attr]
    slug_path = storage_root / slug
    legacy_path = storage_root / project_dir.resolve().name

    if not slug_path.exists() and legacy_path.exists():
        try:
            legacy_path.rename(slug_path)
            _log.info("Migrated storage %s -> %s", legacy_path, slug_path)
        except OSError:
            if slug_path.exists():
                # Likely a concurrent migration completed first
                _log.info(
                    "Legacy storage %s not migrated; slug path %s already exists",
                    legacy_path,
                    slug_path,
                )
            else:
                _log.warning(
                    "Could not migrate legacy storage %s to %s; "
                    "using slug path (may re-index)",
                    legacy_path,
                    slug_path,
                    exc_info=True,
                )

    return slug_path


def resolve_cce_binary() -> str:
    """Find the globally installed cce binary path.

    Checks user-local then system install paths across Linux, macOS, and
    Windows, then PATH, then sys.argv[0] if it looks like cce, then a bare
    "cce" fallback.
    """
    if sys.platform.startswith("win"):
        local_data = Path(os.environ.get("LOCALAPPDATA", ""))
        app_data = Path(os.environ.get("APPDATA", ""))
        candidates = [
            Path.home() / ".local" / "bin" / "cce.exe",          # uv tool (Windows)
            app_data / "uv" / "tools" / "code-context-engine" / "Scripts" / "cce.exe",
            local_data / "uv" / "tools" / "code-context-engine" / "Scripts" / "cce.exe",
            app_data / "Python" / "Scripts" / "cce.exe",          # pipx (Windows)
        ]
    else:
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
    if arg0.name in ("cce", "cce.exe", "code-context-engine", "code-context-engine.exe"):
        return str(arg0)
    return "cce"
