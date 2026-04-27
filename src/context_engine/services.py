"""Service management for CCE — Ollama and Dashboard start/stop/status.

PID files live in <storage_base>/pids/ where storage_base is resolved
from config.yaml (defaults to ~/.cce):
  ollama.pid       PID of the ollama process CCE started
  dashboard.pid    PID of the dashboard process CCE started
  dashboard.port   Port the dashboard is running on
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_DASHBOARD_DEFAULT_PORT = 8080


def _storage_base() -> Path:
    """Resolve storage base from config, falling back to default."""
    try:
        from context_engine.config import load_config
        config = load_config()
        return Path(config.storage_path).parent
    except Exception as exc:
        log.debug("Could not load config for storage base, using default: %s", exc)
        from context_engine.config import _CCE_HOME
        return _CCE_HOME


def _pid_dir() -> Path:
    d = _storage_base() / "pids"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_pid(name: str) -> int | None:
    p = _pid_dir() / f"{name}.pid"
    try:
        return int(p.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_pid(name: str, pid: int) -> None:
    (_pid_dir() / f"{name}.pid").write_text(str(pid))


def _remove_pid(name: str) -> None:
    p = _pid_dir() / f"{name}.pid"
    p.unlink(missing_ok=True)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but owned by another user
        return True


def _check_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _ollama_running() -> bool:
    """Check if Ollama is responding on its default port."""
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _mcp_running() -> bool:
    """Check if a cce serve process is running via pgrep (or ps fallback)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cce serve"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return True
        # returncode 1 = no matches (normal). Any other code or stderr
        # suggests pgrep itself failed — fall through to ps fallback.
        if result.returncode == 1 and not result.stderr.strip():
            return False
    except FileNotFoundError:
        pass
    except Exception:
        pass
    # Fallback: ps with grep exclusion
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            if "cce serve" in line and "grep" not in line:
                return True
    except Exception:
        pass
    return False


# ── Public status API ─────────────────────────────────────────────────────────

def get_ollama_status() -> dict:
    running = _ollama_running()
    managed_pid = _read_pid("ollama")
    managed = managed_pid is not None and _process_alive(managed_pid)

    detail = ""
    if running:
        detail = "localhost:11434"
        if not managed:
            detail += " (external)"

    return {
        "name": "ollama",
        "running": running,
        "managed": managed,
        "detail": detail,
    }


def get_dashboard_status() -> dict:
    port_file = _pid_dir() / "dashboard.port"
    try:
        port = int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        port = None

    managed_pid = _read_pid("dashboard")
    managed = managed_pid is not None and _process_alive(managed_pid)

    running = False
    detail = ""
    if port and _check_port_open(port):
        running = True
        detail = f"http://localhost:{port}"
    elif managed:
        # PID alive but port not answering yet (starting up)
        running = True
        detail = "starting..."

    return {
        "name": "dashboard",
        "running": running,
        "managed": managed,
        "port": port,
        "detail": detail,
    }


def get_mcp_status() -> dict:
    running = _mcp_running()
    return {
        "name": "mcp",
        "running": running,
        "managed": False,  # always managed by Claude Code
        "detail": "managed by Claude Code" if running else "",
    }


# ── Start/stop ────────────────────────────────────────────────────────────────

def start_ollama() -> tuple[bool, str]:
    """Start ollama serve in the background. Returns (success, message)."""
    if _ollama_running():
        return False, "Ollama is already running."
    try:
        proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach from CCE's process group so SIGINT to the CLI doesn't
            # kill the background daemon. Works on both Linux and macOS.
            start_new_session=True,
        )
        _write_pid("ollama", proc.pid)
        return True, f"Ollama started (PID {proc.pid})"
    except FileNotFoundError:
        return False, "ollama not found. Install: https://ollama.com (or `brew install ollama` on macOS)"
    except Exception as exc:
        return False, f"Failed to start Ollama: {exc}"


def stop_ollama() -> tuple[bool, str]:
    """Stop the Ollama process CCE started."""
    pid = _read_pid("ollama")
    if pid is None:
        if _ollama_running():
            return False, "Ollama is running but was not started by CCE (external process)."
        return False, "Ollama is not running."
    if not _process_alive(pid):
        _remove_pid("ollama")
        return False, "Ollama process already stopped."
    try:
        os.kill(pid, signal.SIGTERM)
        _remove_pid("ollama")
        return True, f"Ollama stopped (PID {pid})"
    except Exception as exc:
        return False, f"Failed to stop Ollama: {exc}"


def start_dashboard(port: int = _DASHBOARD_DEFAULT_PORT) -> tuple[bool, str]:
    """Start CCE dashboard as a background process."""
    status = get_dashboard_status()
    if status["running"]:
        return False, f"Dashboard is already running at {status['detail']}"
    try:
        from context_engine.utils import resolve_cce_binary
        cce_bin = resolve_cce_binary()
        proc = subprocess.Popen(
            [cce_bin, "dashboard", "--no-browser", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _write_pid("dashboard", proc.pid)
        (_pid_dir() / "dashboard.port").write_text(str(port))
        return True, f"Dashboard started at http://localhost:{port} (PID {proc.pid})"
    except Exception as exc:
        return False, f"Failed to start dashboard: {exc}"


def stop_dashboard() -> tuple[bool, str]:
    """Stop the CCE dashboard process."""
    pid = _read_pid("dashboard")
    if pid is None:
        return False, "Dashboard is not running (no PID on record)."
    if not _process_alive(pid):
        _remove_pid("dashboard")
        (_pid_dir() / "dashboard.port").unlink(missing_ok=True)
        return False, "Dashboard process already stopped."
    try:
        os.kill(pid, signal.SIGTERM)
        _remove_pid("dashboard")
        (_pid_dir() / "dashboard.port").unlink(missing_ok=True)
        return True, f"Dashboard stopped (PID {pid})"
    except Exception as exc:
        return False, f"Failed to stop dashboard: {exc}"
