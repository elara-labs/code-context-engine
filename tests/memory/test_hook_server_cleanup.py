"""Tests for hook server lifecycle — port file cleanup on shutdown.

Issue #66: stale serve.port files survived `cce serve` exits, and the next
session's hooks would POST to a dead port. The on_cleanup hook now unlinks
both the storage_base and rendezvous port files.
"""
from pathlib import Path

import pytest

from context_engine.memory.hook_server import start_hook_server


@pytest.mark.asyncio
async def test_port_files_created_and_cleaned_up(tmp_path, monkeypatch):
    # Redirect the rendezvous file (Path.home() / .cce / projects / NAME)
    # to a sandboxed home so we don't pollute the real one.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    storage_base = tmp_path / "storage"
    storage_base.mkdir()

    runner, port = await start_hook_server(
        storage_base=storage_base, project_name="proj_under_test",
    )
    try:
        port_file = storage_base / "serve.port"
        rendezvous = fake_home / ".cce" / "projects" / "proj_under_test" / "serve.port"

        # Both files should exist with the bound port.
        assert port_file.exists()
        assert rendezvous.exists()
        assert port_file.read_text() == str(port)
        assert rendezvous.read_text() == str(port)
    finally:
        await runner.cleanup()

    # After cleanup, both files must be gone.
    assert not (storage_base / "serve.port").exists()
    assert not (fake_home / ".cce" / "projects" / "proj_under_test" / "serve.port").exists()


@pytest.mark.asyncio
async def test_cleanup_tolerates_missing_files(tmp_path, monkeypatch):
    """If a port file was already removed externally, cleanup must not raise."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    storage_base = tmp_path / "storage"
    storage_base.mkdir()

    runner, _ = await start_hook_server(
        storage_base=storage_base, project_name="proj_x",
    )

    # Yank the files out from under cleanup.
    (storage_base / "serve.port").unlink()
    (fake_home / ".cce" / "projects" / "proj_x" / "serve.port").unlink()

    # Must not raise.
    await runner.cleanup()


@pytest.mark.asyncio
async def test_cleanup_when_storage_equals_rendezvous(tmp_path, monkeypatch):
    """If storage_base resolves to the same path as the rendezvous (default
    storage layout), the unlink still happens exactly once and cleanly."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Mimic the default layout: storage IS the rendezvous parent.
    storage_base = fake_home / ".cce" / "projects" / "proj_default"
    storage_base.mkdir(parents=True)

    runner, _ = await start_hook_server(
        storage_base=storage_base, project_name="proj_default",
    )
    try:
        assert (storage_base / "serve.port").exists()
    finally:
        await runner.cleanup()
    assert not (storage_base / "serve.port").exists()
