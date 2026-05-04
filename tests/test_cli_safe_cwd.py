"""Tests for `_safe_cwd()` — friendly error when the OS denies
`os.getcwd()`.

Reproduces the macOS Full-Disk-Access / deleted-directory failure mode
without needing a real filesystem permission setup.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import click
from click.testing import CliRunner

from context_engine.cli import _safe_cwd, main


def test_safe_cwd_happy_path():
    """When os.getcwd succeeds, returns the same Path it always would."""
    from pathlib import Path
    assert _safe_cwd() == Path.cwd()


@pytest.mark.parametrize("exc", [
    PermissionError("Operation not permitted"),
    FileNotFoundError("No such file or directory"),
    OSError("Stale file handle"),
])
def test_safe_cwd_translates_os_errors_to_click_exception(exc):
    """Every OS-level cwd failure mode becomes a ClickException — no
    raw stack trace ever escapes to the user.
    """
    with patch("os.getcwd", side_effect=exc):
        with pytest.raises(click.ClickException) as excinfo:
            _safe_cwd()
    msg = excinfo.value.message
    assert exc.__class__.__name__ in msg
    # Message must give the user a concrete next step.
    assert "Full Disk Access" in msg or "cd" in msg


def test_safe_cwd_preserves_original_exception_chain():
    """Click's exception wraps the OSError so debuggers can still reach it."""
    err = PermissionError("Operation not permitted")
    with patch("os.getcwd", side_effect=err):
        try:
            _safe_cwd()
        except click.ClickException as ce:
            assert ce.__cause__ is err


def test_main_command_exits_cleanly_on_cwd_failure():
    """End-to-end: `cce init` returns exit code 1 with a friendly message
    instead of the 30-line pathlib traceback the user originally hit.
    """
    runner = CliRunner()
    with patch("os.getcwd", side_effect=PermissionError("Operation not permitted")):
        result = runner.invoke(main, ["init"])
    # Click prints "Error: <message>" and exits 1 for ClickException.
    assert result.exit_code == 1
    assert "current working directory" in result.output.lower()
    # Crucially: no Python stack trace bleeds through.
    assert "Traceback" not in result.output
    assert "pathlib" not in result.output
