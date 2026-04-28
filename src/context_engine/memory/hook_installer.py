"""Install/uninstall the 5 Claude Code lifecycle hooks for memory capture.

Two pieces of state to manage:

1. The shell script `cce_hook.sh` lives at `~/.cce/hooks/cce_hook.sh`. It's
   tiny (~20 lines) and reads stdin → POSTs to the local memory hook server.

2. The project-level `<project>/.claude/settings.json` gets entries under
   `hooks.<HookName>` pointing to the script. Existing CCE entries (and any
   user-added entries) are preserved.

Install is idempotent: re-running adds nothing new if the entries already
exist. Uninstall removes only the entries we added (matched by command
substring).
"""
from __future__ import annotations

import json
import logging
import stat
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# On Windows, Claude Code hook commands are passed to cmd.exe rather than sh,
# so we install a .cmd script. On POSIX (macOS/Linux), the original .sh.
HOOK_SCRIPT_NAME = "cce_hook.cmd" if _is_windows() else "cce_hook.sh"
HOOK_DIR = Path.home() / ".cce" / "hooks"
HOOK_PATH = HOOK_DIR / HOOK_SCRIPT_NAME

# Marker substring used to identify hooks we own — survives subsequent
# format/path tweaks as long as the script name stays.
HOOK_MARKER = "cce_hook"

LIFECYCLE_HOOKS = [
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "SessionEnd",
]

# Per-hook matcher overrides. Claude Code accepts a regex-like alternation
# in the matcher field; SessionStart's subtypes are:
#   startup  — fresh new conversation
#   clear    — `/clear` command was run
#   compact  — `/compact` command was run
# Re-firing SessionStart on `clear`/`compact` is the trigger that re-
# injects the memory resume after the model's context window is wiped —
# without it, "/clear" would erase your prior-decisions context.
# All other hooks default to matcher="" (any).
HOOK_MATCHERS = {
    "SessionStart": "startup|clear|compact",
}

_HOOK_SCRIPT_BODY_POSIX = """#!/bin/sh
# CCE memory hook — installed by `cce init`. Forwards Claude Code hook
# payloads (JSON on stdin) to the local memory capture server.
#
# Failure is silent — capture is best-effort and must never block the
# user's flow. The hook name is passed as $1 (first argument).
#
# Special case: SessionStart's HTTP response is written to stdout so
# Claude Code injects it into the model's context at session start
# (this is what prevents last week's decisions from being re-explained).
# Other hooks discard their response.
set -u

HOOK_NAME="${1:-unknown}"
PORT_FILE="${HOME}/.cce/projects/$(basename "${PWD}")/serve.port"
[ -r "${PORT_FILE}" ] || exit 0
PORT="$(cat "${PORT_FILE}" 2>/dev/null)"
[ -n "${PORT}" ] || exit 0

if [ "${HOOK_NAME}" = "SessionStart" ]; then
    RESPONSE="$(curl -sf -m 2 -X POST -H "Content-Type: application/json" \\
        --data-binary @- "http://127.0.0.1:${PORT}/hooks/${HOOK_NAME}" \\
        2>/dev/null || true)"
    if [ -n "${RESPONSE}" ]; then
        printf "%s\\n" "${RESPONSE}"
    fi
else
    curl -sf -m 1 -X POST -H "Content-Type: application/json" \\
        --data-binary @- "http://127.0.0.1:${PORT}/hooks/${HOOK_NAME}" \\
        >/dev/null 2>&1 || true
fi
exit 0
"""

# Windows .cmd equivalent. PowerShell would be more flexible but cmd is
# always present and avoids the execution-policy gotcha. The same fail-
# closed semantics: any error → exit 0.
#
# SessionStart's response is written to stdout for context injection (see
# the POSIX comment above); other hooks discard their response.
_HOOK_SCRIPT_BODY_WIN = """@echo off
REM CCE memory hook — installed by `cce init`. Forwards Claude Code hook
REM payloads (JSON on stdin) to the local memory capture server.
REM Failure is silent (always exit 0) so capture never blocks the user.
setlocal enabledelayedexpansion

set "HOOK_NAME=%~1"
if "%HOOK_NAME%"=="" set "HOOK_NAME=unknown"

for %%I in ("%CD%") do set "PROJECT_NAME=%%~nxI"
set "PORT_FILE=%USERPROFILE%\\.cce\\projects\\%PROJECT_NAME%\\serve.port"
if not exist "%PORT_FILE%" exit /b 0

set /p PORT=<"%PORT_FILE%"
if "%PORT%"=="" exit /b 0

if /i "%HOOK_NAME%"=="SessionStart" (
    set "TMP_RESP=%TEMP%\\cce_hook_resp_%RANDOM%.txt"
    curl -sf -m 2 -X POST -H "Content-Type: application/json" ^
        --data-binary @- "http://127.0.0.1:%PORT%/hooks/%HOOK_NAME%" ^
        > "%TMP_RESP%" 2>nul
    if exist "%TMP_RESP%" type "%TMP_RESP%"
    if exist "%TMP_RESP%" del "%TMP_RESP%" >nul 2>&1
) else (
    curl -sf -m 1 -X POST -H "Content-Type: application/json" ^
        --data-binary @- "http://127.0.0.1:%PORT%/hooks/%HOOK_NAME%" >nul 2>&1
)
exit /b 0
"""


def _hook_script_body() -> str:
    return _HOOK_SCRIPT_BODY_WIN if _is_windows() else _HOOK_SCRIPT_BODY_POSIX


def install_hook_script(target: Path = HOOK_PATH) -> bool:
    """Write the platform-appropriate hook script to ~/.cce/hooks/.
    Returns True if created/updated.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    body = _hook_script_body()
    existing = target.read_text() if target.exists() else None
    if existing == body:
        return False
    target.write_text(body)
    if not _is_windows():
        target.chmod(
            target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
    return True


def install_settings(project_dir: Path) -> dict:
    """Wire all 5 lifecycle hooks into <project>/.claude/settings.json.

    Idempotent. Preserves any existing user hooks. Returns a summary dict
    with `added` (hook names we wrote) and `skipped` (hook names already
    present).
    """
    settings_dir = project_dir / ".claude"
    settings_path = settings_dir / "settings.json"
    settings_dir.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text() or "{}")
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            log.warning("Existing settings.json is invalid JSON; rewriting.")
            data = {}

    hooks = data.setdefault("hooks", {})
    added: list[str] = []
    skipped: list[str] = []

    for hook_name in LIFECYCLE_HOOKS:
        bucket = hooks.setdefault(hook_name, [])
        if _has_cce_hook(bucket):
            skipped.append(hook_name)
            continue
        # Claude Code passes `command` to `sh -c`, so an unquoted path
        # tokenises on whitespace. shlex.quote handles `~/Users/Alice Smith/...`
        # paths cleanly without us needing to know what shell-special chars
        # might appear.
        import shlex
        bucket.append({
            "matcher": HOOK_MATCHERS.get(hook_name, ""),
            "hooks": [{
                "type": "command",
                "command": f"{shlex.quote(str(HOOK_PATH))} {hook_name}",
            }],
        })
        added.append(hook_name)

    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"added": added, "skipped": skipped, "settings_path": str(settings_path)}


def uninstall_settings(project_dir: Path) -> dict:
    """Remove our 5 hook entries from settings.json. Idempotent."""
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"removed": [], "settings_path": str(settings_path)}
    try:
        data = json.loads(settings_path.read_text() or "{}")
    except json.JSONDecodeError:
        return {"removed": [], "settings_path": str(settings_path)}
    if not isinstance(data, dict):
        return {"removed": [], "settings_path": str(settings_path)}

    hooks = data.get("hooks") or {}
    removed: list[str] = []
    for hook_name in LIFECYCLE_HOOKS:
        bucket = hooks.get(hook_name)
        if not bucket:
            continue
        kept = [entry for entry in bucket if not _has_cce_hook([entry])]
        if len(kept) != len(bucket):
            removed.append(hook_name)
        if kept:
            hooks[hook_name] = kept
        else:
            del hooks[hook_name]

    if removed:
        if not hooks:
            data.pop("hooks", None)
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return {"removed": removed, "settings_path": str(settings_path)}


def _has_cce_hook(bucket: list) -> bool:
    """True if any entry in the bucket runs our hook script."""
    for entry in bucket:
        for h in entry.get("hooks", []) or []:
            cmd = h.get("command", "") or ""
            if HOOK_MARKER in cmd:
                return True
    return False
