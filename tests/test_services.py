"""Tests for services.py — PID utilities and status checks."""
import os
from pathlib import Path

import pytest

from context_engine.services import (
    _pid_dir,
    _read_pid,
    _write_pid,
    _remove_pid,
    _process_alive,
    _check_port_open,
    _is_remote_url,
    get_dashboard_status,
    get_ollama_status,
    start_ollama,
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
    # PID 2**22 is beyond the max PID on all supported platforms (Linux max=4194304=2^22,
    # macOS max=99998). Using it guarantees ProcessLookupError without relying on timing.
    assert _process_alive(4_200_000) is False


# ── Port check ────────────────────────────────────────────────────────────────

def test_check_port_open_closed():
    # Port 19999 is in the unregistered range (1024-49151) and not assigned by IANA.
    # If this flakes in CI, replace with a dynamically allocated port.
    assert _check_port_open(19999) is False


# ── Dashboard status when nothing is running ─────────────────────────────────

def test_get_dashboard_status_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    status = get_dashboard_status()
    assert status["running"] is False
    assert status["name"] == "dashboard"


# ── Remote-URL classification + Ollama wiring ────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "http://[::1]:11434",
    "http://0.0.0.0:11434",
    "https://localhost:11434",
])
def test_is_remote_url_local(url):
    assert _is_remote_url(url) is False


@pytest.mark.parametrize("url", [
    "http://nas.local:11434",
    "http://192.168.1.50:11434",
    "https://ollama.example.com",
])
def test_is_remote_url_remote(url):
    assert _is_remote_url(url) is True


def test_get_ollama_status_reports_configured_url(tmp_path, monkeypatch):
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    monkeypatch.setenv("CCE_OLLAMA_URL", "http://nas.local:11434")
    status = get_ollama_status()
    assert status["url"] == "http://nas.local:11434"
    # Hostname must not be silently rewritten back to localhost in the
    # human-readable detail string.
    assert "nas.local" in status["detail"] or status["detail"] == ""


def test_start_ollama_refuses_when_remote_configured(tmp_path, monkeypatch):
    """Spawning a local `ollama serve` is wrong when the user has pointed
    CCE at a remote endpoint — would run a server nothing in CCE talks to."""
    monkeypatch.setattr("context_engine.services._pid_dir", lambda: tmp_path)
    monkeypatch.setenv("CCE_OLLAMA_URL", "http://nas.local:11434")
    ok, msg = start_ollama()
    assert ok is False
    assert "remote" in msg.lower()
    assert "nas.local" in msg
