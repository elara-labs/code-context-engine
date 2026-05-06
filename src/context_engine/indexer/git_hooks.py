"""Git hook installer and handler for triggering re-indexing."""
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HOOK_MARKER = "# cce hook"
HOOK_NAMES = ["post-commit", "post-checkout", "post-merge"]


def _resolve_hooks_dir(project_dir: str) -> Path | None:
    """Return the directory git uses for hooks for `project_dir`, or None.

    Why this is non-trivial: in a regular checkout, hooks live at
    `<project>/.git/hooks/`. In a git worktree, `<project>/.git` is a *file*
    pointing at `<main>/.git/worktrees/<name>/`, and hooks are shared with the
    main repo at `<main>/.git/hooks/`. Hardcoding the regular-checkout layout
    makes the installer silently no-op inside worktrees.

    `git rev-parse --git-path hooks` resolves the right directory in both
    cases (relative `.git/hooks` for regular checkouts, an absolute path to
    the shared hooks dir for worktrees), and also respects `core.hooksPath`
    if a project has overridden it.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=project_dir, capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    hooks_dir = Path(raw)
    if not hooks_dir.is_absolute():
        hooks_dir = Path(project_dir) / hooks_dir
    return hooks_dir


def _resolve_cce_binary() -> str:
    """Find an absolute path to the `cce` launcher.

    Preferring an absolute path means the git hook keeps working when the user
    runs `git commit` from a shell that doesn't pick up the same PATH as the one
    used to install the engine (e.g. different login shell, GUI git client).
    """
    # On Windows the launcher is cce.exe; on POSIX it has no extension.
    exe_suffix = ".exe" if sys.platform.startswith("win") else ""
    candidate = Path(sys.executable).parent / f"cce{exe_suffix}"
    if candidate.exists():
        return str(candidate)
    which = (
        shutil.which("cce") or shutil.which("code-context-engine")
        or shutil.which("cce.exe")  # Windows fallback
    )
    if which:
        return which
    # Last-resort: rely on PATH at hook-run time.
    return "cce"


def _hook_script() -> str:
    bin_path = _resolve_cce_binary()
    return f"""{HOOK_MARKER}
{bin_path} index --changed-only >/dev/null 2>&1 &
"""


def install_hooks(project_dir: str) -> list[str]:
    """Install CCE git hooks. Returns [] gracefully if not a git repo or if
    git is unavailable. Works correctly inside git worktrees, where hooks
    live in the shared main-repo `.git/hooks` directory."""
    hooks_dir = _resolve_hooks_dir(project_dir)
    if hooks_dir is None or not hooks_dir.exists():
        return []
    installed = []
    for hook_name in HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        _install_single_hook(hook_path)
        installed.append(str(hook_path))
    return installed


def _install_single_hook(hook_path: Path) -> None:
    script = _hook_script()
    if hook_path.exists():
        existing = hook_path.read_text()
        if HOOK_MARKER in existing:
            return
        new_content = existing.rstrip() + "\n\n" + script
    else:
        new_content = "#!/bin/sh\n\n" + script
    hook_path.write_text(new_content)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)


def uninstall_hooks(project_dir: str) -> int:
    """Remove CCE-installed hook scripts from this project. Returns the number
    of hook files actually removed. No-op outside a git repo or if git is
    unavailable.

    Detects \"CCE-installed\" by file content (presence of \"cce\" or
    \"context-engine\") rather than the marker alone, so legacy installations
    that pre-date HOOK_MARKER are still cleaned up. Worktree-aware via the
    same `_resolve_hooks_dir` used by install.
    """
    hooks_dir = _resolve_hooks_dir(project_dir)
    if hooks_dir is None or not hooks_dir.exists():
        return 0
    removed = 0
    for hook_name in HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            continue
        try:
            content = hook_path.read_text()
        except OSError:
            continue
        if "cce" in content.lower() or "context-engine" in content.lower():
            try:
                hook_path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def get_changed_files_from_hook() -> list[str]:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []
