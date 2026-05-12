"""Git hook installer and handler for triggering re-indexing."""
import shlex
import shutil
import stat
import sys
from pathlib import Path

HOOK_MARKER = "# cce hook"
HOOK_NAMES = ["post-commit", "post-checkout", "post-merge"]


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
    # `cce index` without any flag already performs incremental indexing
    # via the on-disk manifest's content-hash check. The old
    # `--changed-only` flag was removed but the hook template hadn't been
    # updated — every commit silently errored with
    # "No such option: --changed-only" (issue #67).
    #
    # bin_path must be shell-quoted because resolved paths commonly
    # include spaces (e.g. C:\Users\Alice Smith\... on Windows, or
    # /Users/Firstname Lastname/.venv/bin/cce on macOS). git's hook
    # runner invokes the file via POSIX sh on every platform — even
    # git-for-windows ships a bundled sh — so single-quoting with
    # shlex.quote produces a correct token for the shell that actually
    # runs the hook (Copilot review).
    bin_path = shlex.quote(_resolve_cce_binary())
    return f"""{HOOK_MARKER}
{bin_path} index >/dev/null 2>&1 &
"""


def install_hooks(project_dir: str) -> list[str]:
    """Install CCE git hooks. Returns [] gracefully if not a git repo."""
    hooks_dir = Path(project_dir) / ".git" / "hooks"
    if not hooks_dir.exists():
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
