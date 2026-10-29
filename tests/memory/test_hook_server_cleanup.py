"""Tests for hook server lifecycle — port file cleanup on shutdown.

Issue #66: stale serve.port files survived `cce serve` exits, and the next
session's hooks would POST to a dead port. The on_cleanup hook now unlinks
both the storage_base and rendezvous port files.
"""
import asyncio
import threading
from pathlib import Path

import pytest

from context_engine.memory import hook_server
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


@pytest.mark.asyncio
async def test_cleanup_leaves_port_files_owned_by_another_server(tmp_path, monkeypatch):
    """Two `cce serve` on one project: the second start overwrites the port
    files. When the first one exits it must not delete them, or the hooks of
    the server still running lose their rendezvous."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    port_file = storage_base / "serve.port"
    rendezvous = fake_home / ".cce" / "projects" / "proj_shared" / "serve.port"

    first, _ = await start_hook_server(storage_base=storage_base, project_name="proj_shared")
    second, second_port = await start_hook_server(
        storage_base=storage_base, project_name="proj_shared",
    )
    try:
        await first.cleanup()
        assert port_file.read_text() == str(second_port)
        assert rendezvous.read_text() == str(second_port)
    finally:
        await second.cleanup()

    assert not port_file.exists()
    assert not rendezvous.exists()


@pytest.mark.asyncio
async def test_peer_publishing_during_cleanup_keeps_its_rendezvous(tmp_path, monkeypatch):
    """A peer publishing its port between this server's ownership read and
    its unlink must not lose the rendezvous: publication waits on the same
    lock as the compare-and-delete, so it lands after the unlink."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    port_file = storage_base / "serve.port"
    rendezvous = fake_home / ".cce" / "projects" / "proj_race" / "serve.port"

    runner, _ = await start_hook_server(storage_base=storage_base, project_name="proj_race")

    peer_port = 1
    peer = threading.Thread(
        target=hook_server._publish_port,
        args=([port_file, rendezvous], peer_port, storage_base / hook_server.PORT_LOCK_NAME),
    )
    real_read_text = Path.read_text

    def read_then_peer_publishes(self, *args, **kwargs):
        text = real_read_text(self, *args, **kwargs)
        if self == port_file and not peer.is_alive() and peer.ident is None:
            peer.start()
            peer.join(timeout=0.3)  # without the lock the peer writes right here
        return text

    monkeypatch.setattr(Path, "read_text", read_then_peer_publishes)
    await runner.cleanup()
    monkeypatch.setattr(Path, "read_text", real_read_text)
    peer.join(timeout=5)

    assert port_file.read_text() == str(peer_port)
    assert rendezvous.read_text() == str(peer_port)


@pytest.mark.asyncio
async def test_last_started_server_exiting_hands_rendezvous_to_live_peer(tmp_path, monkeypatch):
    """A short-lived server started after a long-running one (a headless agent
    next to an interactive session) holds the port files when it exits: they
    must move to the survivor, not disappear."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    port_file = storage_base / "serve.port"
    rendezvous = fake_home / ".cce" / "projects" / "proj_handover" / "serve.port"

    first, first_port = await start_hook_server(
        storage_base=storage_base, project_name="proj_handover",
    )
    second, second_port = await start_hook_server(
        storage_base=storage_base, project_name="proj_handover",
    )
    try:
        assert port_file.read_text() == str(second_port)
        await second.cleanup()
        assert port_file.read_text() == str(first_port)
        assert rendezvous.read_text() == str(first_port)
    finally:
        await first.cleanup()

    assert not port_file.exists()
    assert not rendezvous.exists()
    assert list((storage_base / hook_server.PORT_REGISTRY_NAME).iterdir()) == []


@pytest.mark.asyncio
async def test_dead_peer_in_registry_is_pruned_not_published(tmp_path, monkeypatch):
    """A peer killed without cleanup leaves its registry entry: the exiting
    server must not hand the rendezvous to that dead port."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    port_file = storage_base / "serve.port"

    runner, _ = await start_hook_server(storage_base=storage_base, project_name="proj_dead")
    registry = storage_base / hook_server.PORT_REGISTRY_NAME
    dead_port = hook_server._find_free_port()  # nothing listens on it
    (registry / str(dead_port)).write_text("0", encoding="utf-8")

    await runner.cleanup()

    assert not port_file.exists()
    assert not (registry / str(dead_port)).exists()


@pytest.mark.asyncio
async def test_start_prunes_registry_entries_of_dead_peers(tmp_path, monkeypatch):
    """Peers killed without cleanup never remove their entry; the next start
    must sweep them, live peers excepted, so the registry does not grow."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    storage_base = tmp_path / "storage"
    registry = storage_base / hook_server.PORT_REGISTRY_NAME
    registry.mkdir(parents=True)
    dead = [hook_server._find_free_port() for _ in range(3)]  # nothing listens on them
    for port in dead:
        (registry / str(port)).write_text("0", encoding="utf-8")

    first, first_port = await start_hook_server(storage_base=storage_base, project_name="proj_sweep")
    second, second_port = await start_hook_server(storage_base=storage_base, project_name="proj_sweep")
    try:
        assert sorted(e.name for e in registry.iterdir()) == sorted([str(first_port), str(second_port)])
    finally:
        await second.cleanup()
        await first.cleanup()


@pytest.mark.asyncio
async def test_cleanup_waiting_on_busy_lock_does_not_block_event_loop(tmp_path, monkeypatch):
    """The port-files lock is polled with time.sleep: cleanup must wait for it
    in a worker thread, or a peer holding the lock freezes the event loop."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    storage_base = tmp_path / "storage"
    storage_base.mkdir()

    runner, _ = await start_hook_server(storage_base=storage_base, project_name="proj_loop")

    locked, release = threading.Event(), threading.Event()

    def hold_lock():
        with hook_server._port_files_lock(storage_base / hook_server.PORT_LOCK_NAME):
            locked.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert locked.wait(timeout=5)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    tick_task = asyncio.create_task(ticker())
    cleanup = asyncio.create_task(runner.cleanup())
    await asyncio.sleep(0.3)
    ticks_while_waiting = ticks
    release.set()
    await cleanup
    tick_task.cancel()
    holder.join(timeout=5)

    assert ticks_while_waiting >= 5
    assert not (storage_base / "serve.port").exists()
