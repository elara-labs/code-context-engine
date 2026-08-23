"""Git hook installer and handler for triggering re-indexing."""
import shlex
import shutil
import stat
import sys
from pathlib import Path

HOOK_MARKER = "# cce hook"
HOOK_END_MARKER = "# cce hook end"
HOOK_NAMES = ["post-commit", "post-checkout", "post-merge"]

# Paths that indicate an ephemeral/throwaway worktree created by AI
# agent harnesses.  Indexing these is wasted work since the tree is
# deleted minutes later.
_EPHEMERAL_PATH_MARKERS = [
    "/private/tmp/",
    "/tmp/",
    "/.claude/worktrees/",
]


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
    """Generate the shell snippet inserted into git hook files.

    The script:
    1. Skips ephemeral worktree paths (agent-created throwaway trees).
    2. Holds a global (machine-wide) lock via mkdir (POSIX atomic, works
       on macOS and Linux without flock/shlock).  At most one hook-triggered
       indexer runs at a time.  The previous version used bare `&` with no
       cap, which with N worktrees produced N detached indexers (#159).
    3. Runs at nice 10 so indexing never competes with foreground work.
    4. Stale lock cleanup: if the lock dir exists but the PID inside is
       dead, the lock is reclaimed.
    """
    bin_path = shlex.quote(_resolve_cce_binary())
    # Build the ephemeral-path skip check as shell conditions.
    # These are shell glob patterns inside `case`, not arguments, so they
    # must NOT be shlex.quote'd (quoting turns them into literal strings
    # that never match).
    skip_checks = " || ".join(
        f'case "$PWD" in *{m}*) true;; *) false;; esac'
        for m in _EPHEMERAL_PATH_MARKERS
    )
    return f"""{HOOK_MARKER}
# Skip ephemeral worktree paths (agent-created throwaway trees)
if {skip_checks}; then
  exit 0
fi
# Global concurrency cap: one hook-triggered indexer machine-wide.
# Uses mkdir as an atomic lock (POSIX portable, no flock needed). #159
_cce_lock_dir="${{TMPDIR:-/tmp}}/cce-index-hook.lock"
_cce_try_lock() {{
  if mkdir "$_cce_lock_dir" 2>/dev/null; then
    echo $$ > "$_cce_lock_dir/pid"
    return 0
  fi
  # Check for stale lock (owner process dead)
  if [ -f "$_cce_lock_dir/pid" ]; then
    _old_pid=$(cat "$_cce_lock_dir/pid" 2>/dev/null)
    if [ -n "$_old_pid" ] && ! kill -0 "$_old_pid" 2>/dev/null; then
      rm -rf "$_cce_lock_dir"
      if mkdir "$_cce_lock_dir" 2>/dev/null; then
        echo $$ > "$_cce_lock_dir/pid"
        return 0
      fi
    fi
  fi
  return 1
}}
(
  if _cce_try_lock; then
    trap 'rm -rf "$_cce_lock_dir"' EXIT
    nice -n 10 {bin_path} index >/dev/null 2>&1
  fi
) &
{HOOK_END_MARKER}
"""


def _resolve_hooks_dir(project_dir: str) -> Path | None:
    """Resolve the hooks directory, following git worktree indirection.

    In a worktree, `.git` is a file containing `gitdir: <path>`.  Hook
    lookup resolves through the common dir, so we install there to avoid
    writing hooks into a worktree-local path that git ignores.  Returns
    None if the project is not a git repo.
    """
    dot_git = Path(project_dir) / ".git"
    if dot_git.is_file():
        # Worktree: .git is a file, not a directory.  Resolve common dir.
        import subprocess
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, timeout=5,
                cwd=project_dir,
            )
            if result.returncode == 0:
                common = Path(result.stdout.strip())
                if not common.is_absolute():
                    common = (dot_git.parent / common).resolve()
                hooks_dir = common / "hooks"
                if hooks_dir.exists() or hooks_dir.parent.exists():
                    hooks_dir.mkdir(exist_ok=True)
                    return hooks_dir
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None
    elif dot_git.is_dir():
        hooks_dir = dot_git / "hooks"
        if not hooks_dir.exists():
            hooks_dir.mkdir(exist_ok=True)
        return hooks_dir
    return None


def install_hooks(project_dir: str) -> list[str]:
    """Install CCE git hooks. Returns [] gracefully if not a git repo."""
    hooks_dir = _resolve_hooks_dir(project_dir)
    if hooks_dir is None:
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
        existing = hook_path.read_text(encoding="utf-8")
        if HOOK_MARKER in existing:
            # Re-install: replace only the CCE block, preserving any user
            # content before AND after it.
            marker_idx = existing.index(HOOK_MARKER)
            prefix = existing[:marker_idx].rstrip()
            # Find end of old block: end-marker (new format) or marker + one line (legacy)
            end_idx = existing.find(HOOK_END_MARKER, marker_idx)
            if end_idx >= 0:
                suffix = existing[end_idx + len(HOOK_END_MARKER):]
            else:
                # Legacy: marker + one command line
                after_marker = existing[marker_idx + len(HOOK_MARKER):]
                lines_after = after_marker.split("\n", 2)
                suffix = "\n" + lines_after[2] if len(lines_after) > 2 else ""
            suffix = suffix.strip()
            new_content = (prefix or "#!/bin/sh") + "\n\n" + script
            if suffix:
                new_content = new_content.rstrip() + "\n\n" + suffix + "\n"
            hook_path.write_text(new_content, encoding="utf-8")
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)
            return
        new_content = existing.rstrip() + "\n\n" + script
    else:
        new_content = "#!/bin/sh\n\n" + script
    hook_path.write_text(new_content, encoding="utf-8")
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
