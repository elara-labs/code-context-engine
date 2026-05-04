# CCE Services Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cce services` command group that shows status of Ollama and Dashboard, and lets the user start/stop them as background processes.

**Architecture:** A new `services.py` module owns all process management logic (PID files, status checks, start/stop). The CLI layer in `cli.py` adds a `services` command group that calls into that module. PID files and a port file live in `~/.claude-context-engine/pids/`.

**Tech Stack:** Python stdlib (`subprocess`, `os`, `signal`, `socket`), `httpx` (already a dep), `click` (already a dep)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/context_engine/services.py` | Create | All service management logic: PID files, process checks, start/stop for Ollama and Dashboard, MCP status |
| `src/context_engine/cli.py` | Modify | Add `services` command group with `status` (default), `start`, `stop` subcommands |
| `tests/test_services.py` | Create | Unit tests for PID utils and status checks |

---

### Task 1: Write failing tests for PID utilities and status checks

**Files:**
- Create: `tests/test_services.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for services.py — PID utilities and status checks."""
import os
import signal
from pathlib import Path

import pytest

from context_engine.services import (
    _pid_dir,
    _read_pid,
    _write_pid,
    _remove_pid,
    _process_alive,
    _check_port_open,
    get_dashboard_status,
)


# ── PID utilities ────────────────────────────────────────────────────────────

def test_write_and_read_pid(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    _write_pid("testservice", 12345)
    assert _read_pid("testservice") == 12345


def test_read_pid_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    assert _read_pid("nonexistent") is None


def test_remove_pid(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    _write_pid("testservice", 99)
    _remove_pid("testservice")
    assert _read_pid("testservice") is None


def test_remove_pid_noop_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    _remove_pid("nonexistent")  # must not raise


# ── Process alive check ───────────────────────────────────────────────────────

def test_process_alive_self():
    assert _process_alive(os.getpid()) is True


def test_process_alive_dead_pid():
    # PID 1 is always init/launchd and is alive, but a very large PID is almost
    # certainly dead. Use a known-dead approach: fork, exit, check.
    import subprocess
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert _process_alive(proc.pid) is False


# ── Port check ────────────────────────────────────────────────────────────────

def test_check_port_open_closed():
    # Port 19999 is almost certainly not in use
    assert _check_port_open(19999) is False


# ── Dashboard status when nothing is running ─────────────────────────────────

def test_get_dashboard_status_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    status = get_dashboard_status()
    assert status["running"] is False
    assert status["name"] == "dashboard"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/raj/projects/Claude-Context-Engine
uv run pytest tests/test_services.py -v 2>&1 | head -30
```

Expected: `ImportError` — `context_engine.services` does not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_services.py
git commit -m "test: add failing tests for services PID utilities and status checks"
```

---

### Task 2: Implement services.py

**Files:**
- Create: `src/context_engine/services.py`

- [ ] **Step 1: Write the implementation**

```python
"""Service management for CCE — Ollama and Dashboard start/stop/status.

PID files live in ~/.claude-context-engine/pids/:
  ollama.pid       PID of the ollama process CCE started
  dashboard.pid    PID of the dashboard process CCE started
  dashboard.port   Port the dashboard is running on
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from pathlib import Path


_STORAGE_BASE = Path.home() / ".claude-context-engine"
_DASHBOARD_DEFAULT_PORT = 8080


def _pid_dir() -> Path:
    d = _STORAGE_BASE / "pids"
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
    """Check if a cce serve process is running (read-only)."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=3
        )
        return "cce serve" in result.stdout
    except Exception:
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
        )
        _write_pid("ollama", proc.pid)
        return True, f"Ollama started (PID {proc.pid})"
    except FileNotFoundError:
        return False, "ollama not found. Install it: brew install ollama"
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
        cce_bin = Path(sys.argv[0]).resolve()
        proc = subprocess.Popen(
            [str(cce_bin), "dashboard", "--no-browser", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
```

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/test_services.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/context_engine/services.py
git commit -m "feat: add services.py for Ollama and Dashboard process management"
```

---

### Task 3: Add `cce services` command group to cli.py

**Files:**
- Modify: `src/context_engine/cli.py`

- [ ] **Step 1: Locate the insertion point**

Open `src/context_engine/cli.py`. Find the `dashboard` command (around line 706). The `services` group goes after it, before `_run_index`.

- [ ] **Step 2: Add the services command group**

Add this block after the `dashboard` command function and before `_run_index`:

```python
# ── services command group ────────────────────────────────────────────────────

@main.group(invoke_without_command=True)
@click.pass_context
def services(ctx: click.Context) -> None:
    """Show status of CCE services (Ollama, Dashboard)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(services_status)


@services.command(name="status")
def services_status() -> None:
    """Show status of all CCE services."""
    from context_engine.services import get_ollama_status, get_dashboard_status, get_mcp_status

    rows = [
        get_ollama_status(),
        get_dashboard_status(),
        get_mcp_status(),
    ]

    # Header
    click.echo(f"{'SERVICE':<12}{'STATUS':<10}DETAIL")
    click.echo("-" * 48)

    for row in rows:
        status_label = click.style("running", fg="green") if row["running"] else click.style("stopped", fg="red")
        detail = row.get("detail", "")
        name = row["name"]
        # Pad status_label accounting for ANSI codes (10 visible chars)
        click.echo(f"{name:<12}{status_label:<10 + 9}  {detail}")


@services.command(name="start")
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
@click.option("--port", default=8080, show_default=True, help="Dashboard port (only used when starting dashboard)")
def services_start(service: str, port: int) -> None:
    """Start CCE services. SERVICE: ollama | dashboard | all (default)."""
    from context_engine.services import start_ollama, start_dashboard

    targets = ["ollama", "dashboard"] if service == "all" else [service]

    for target in targets:
        if target == "ollama":
            ok, msg = start_ollama()
        else:
            ok, msg = start_dashboard(port=port)
        prefix = click.style("✓", fg="green") if ok else click.style("·", fg="yellow")
        click.echo(f"  {prefix} {msg}")


@services.command(name="stop")
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
def services_stop(service: str) -> None:
    """Stop CCE services. SERVICE: ollama | dashboard | all (default)."""
    from context_engine.services import stop_ollama, stop_dashboard

    targets = ["ollama", "dashboard"] if service == "all" else [service]

    for target in targets:
        if target == "ollama":
            ok, msg = stop_ollama()
        else:
            ok, msg = stop_dashboard()
        prefix = click.style("✓", fg="green") if ok else click.style("·", fg="yellow")
        click.echo(f"  {prefix} {msg}")
```

- [ ] **Step 3: Fix the status_label padding**

The `click.style()` call adds ANSI escape codes that break fixed-width padding. Replace the status echo line:

```python
    for row in rows:
        running = row["running"]
        status_text = "running" if running else "stopped"
        status_col = click.style(f"{status_text:<10}", fg="green" if running else "red")
        detail = row.get("detail", "")
        click.echo(f"{row['name']:<12}{status_col}  {detail}")
```

- [ ] **Step 4: Smoke-test the commands**

```bash
uv run --directory /Users/raj/projects/Claude-Context-Engine cce services
```

Expected output (example):
```
SERVICE     STATUS    DETAIL
------------------------------------------------
ollama      stopped
dashboard   stopped
mcp         stopped
```

```bash
uv run --directory /Users/raj/projects/Claude-Context-Engine cce services start dashboard
```

Expected:
```
  ✓ Dashboard started at http://localhost:8080 (PID 12345)
```

```bash
uv run --directory /Users/raj/projects/Claude-Context-Engine cce services
```

Expected: dashboard shows `running   http://localhost:8080`

```bash
uv run --directory /Users/raj/projects/Claude-Context-Engine cce services stop dashboard
```

Expected:
```
  ✓ Dashboard stopped (PID 12345)
```

- [ ] **Step 5: Commit**

```bash
git add src/context_engine/cli.py
git commit -m "feat: add cce services command for Ollama and Dashboard management"
```

---

### Task 4: Update README CLI table

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add services commands to the CLI table**

In `README.md`, find the CLI Commands table. Add these rows after `cce dashboard --no-browser`:

```markdown
| `cce services` | Show status of Ollama and Dashboard |
| `cce services start` | Start all services (Ollama + Dashboard) |
| `cce services stop` | Stop all services started by CCE |
| `cce services start ollama` | Start Ollama in the background |
| `cce services stop ollama` | Stop Ollama (only if started by CCE) |
| `cce services start dashboard` | Start dashboard on port 8080 |
| `cce services stop dashboard` | Stop dashboard |
| `cce services start dashboard --port 9000` | Start dashboard on a specific port |
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add cce services commands to CLI reference table"
```

---

### Task 5: Reinstall and end-to-end verify

- [ ] **Step 1: Reinstall CCE from source**

```bash
uv tool install --editable /Users/raj/projects/Claude-Context-Engine
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/test_services.py -v
```

Expected: all tests pass.

- [ ] **Step 3: End-to-end test**

```bash
# Status when nothing running
cce services

# Start dashboard in background
cce services start dashboard

# Status — dashboard should show running
cce services

# Open browser manually to confirm: http://localhost:8080

# Stop dashboard
cce services stop dashboard

# Status — dashboard should show stopped
cce services
```

- [ ] **Step 4: Final commit if any cleanup needed, then push**

```bash
git push
```
