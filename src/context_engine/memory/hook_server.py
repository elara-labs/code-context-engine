"""Loopback HTTP server for Claude Code hook payloads.

Bound to 127.0.0.1 on a random free port. The port is written to
`<storage_base>/serve.port` so the hook shell script can find it without
configuration. No auth — the listener is loopback-only.

Started as a background asyncio task from `_run_serve` (the MCP server
process). Stopped gracefully on shutdown.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from aiohttp import web

from context_engine.memory import db as memory_db
from context_engine.memory.hooks import add_routes

log = logging.getLogger(__name__)


# Name of the lock file, next to the authoritative serve.port, that serializes
# every publication and every compare-and-delete of the port files across the
# `cce serve` processes of one project.
PORT_LOCK_NAME = "serve.port.lock"
_PORT_LOCK_TIMEOUT_S = 2.0
# Directory, next to serve.port, holding one entry per running server (file
# name = its port). On exit, a server whose port is still published hands the
# rendezvous over to the most recent live peer instead of leaving none.
PORT_REGISTRY_NAME = "serve.ports"
_PEER_PROBE_TIMEOUT_S = 0.2


@contextlib.contextmanager
def _port_files_lock(lock_path: Path, timeout: float = _PORT_LOCK_TIMEOUT_S) -> Iterator[bool]:
    """Hold an exclusive cross-process lock on `lock_path`; yields whether it is held.

    Without it, another server could publish its port between this server's
    "does the file still hold my port?" read and its unlink, and lose its
    rendezvous. Gives up after `timeout` (yields False) rather than hang a
    startup or a shutdown on a wedged peer.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    held = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    log.warning("serve.port lock busy after %.1fs: %s", timeout, lock_path)
                    break
                time.sleep(0.02)
        yield held
    finally:
        if held:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)


def _register_port(registry: Path, port: int) -> None:
    try:
        registry.mkdir(parents=True, exist_ok=True)
        (registry / str(port)).write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        log.warning("serve.port registry write failed for %s: %s", registry, exc)


def _port_is_live(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PEER_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _live_successor(registry: Path, own: int | None) -> int | None:
    """Most recently registered peer that still accepts connections.

    Entries of peers that are gone (killed without cleanup) are removed on
    the way, so the registry does not grow across crashes.
    """
    try:
        entries = [e for e in registry.iterdir() if e.name.isdigit()]
    except OSError:
        return None
    candidates = []
    for entry in entries:
        port = int(entry.name)
        if port == own:
            continue
        try:
            candidates.append((entry.stat().st_mtime, port, entry))
        except OSError:
            continue
    for _, port, entry in sorted(candidates, reverse=True):
        if _port_is_live(port):
            return port
        entry.unlink(missing_ok=True)
    return None


def _prune_dead_peers(registry: Path, own: int) -> None:
    """Drop every registry entry whose port no longer accepts connections.

    Servers killed without cleanup (TerminateProcess on Windows, SIGKILL)
    never remove their entry, and `_live_successor` stops at the first live
    peer, so without this sweep the registry only grows. Run at each start,
    under the port-files lock.
    """
    try:
        entries = [e for e in registry.iterdir() if e.name.isdigit() and int(e.name) != own]
    except OSError:
        return
    for entry in entries:
        if not _port_is_live(int(entry.name)):
            entry.unlink(missing_ok=True)


def _publish_port(
    port_files: list[Path], port: int, lock_path: Path, registry: Path | None = None
) -> None:
    """Write `port` to every port file, under the port-files lock."""
    with _port_files_lock(lock_path):
        if registry is not None:
            _prune_dead_peers(registry, port)
            _register_port(registry, port)
        written: set[Path] = set()
        for f in port_files:
            try:
                key = f.resolve()
                if key in written:
                    continue
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(str(port), encoding="utf-8")
                written.add(key)
            except OSError as exc:
                # Non-fatal for the rendezvous copy — capture still works for
                # users with default storage.
                log.warning("serve.port write failed for %s: %s", f, exc)


def _release_port_files(
    port_files: list[Path], own: int | None, lock_path: Path, registry: Path
) -> None:
    """Remove, or hand over to a live peer, the port files still holding `own`."""
    # Compare-and-delete under the same lock as publication, so a peer
    # cannot write its port between the read and the unlink.
    with _port_files_lock(lock_path) as held:
        if not held and own is not None:
            # Unknown state: leave the files rather than risk deleting a
            # peer's rendezvous (a stale file is caught by the hook's
            # socket-liveness probe).
            return
        if own is not None:
            (registry / str(own)).unlink(missing_ok=True)
        successor = _live_successor(registry, own)
        for f in port_files:
            try:
                if own is not None and f.read_text(encoding="utf-8").strip() != str(own):
                    continue
                if successor is not None:
                    f.write_text(str(successor), encoding="utf-8")
                    continue
                f.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("serve.port cleanup failed for %s: %s", f, exc)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_hook_server(
    *,
    storage_base: Path,
    project_name: str,
) -> tuple[web.AppRunner, int]:
    """Spin up the hook HTTP listener. Returns (runner, port).

    Caller is responsible for `await runner.cleanup()` on shutdown.
    """
    db_path = memory_db.memory_db_path(storage_base)
    conn = memory_db.connect(db_path)

    app = web.Application()
    app["memory_db"] = conn
    app["project_name"] = project_name
    add_routes(app)

    # Authoritative port file lives in the project's storage_base.
    port_file = Path(storage_base) / "serve.port"
    lock_path = Path(storage_base) / PORT_LOCK_NAME
    registry = Path(storage_base) / PORT_REGISTRY_NAME
    # Stable rendezvous file at the *default* storage location. The hook
    # shell script always looks here (`${HOME}/.cce/projects/<name>/serve.port`)
    # because it has no way to read the user's config.yaml. When storage_path
    # is customised, this is the only way capture stays wired up.
    default_rendezvous = (
        Path.home() / ".cce" / "projects" / project_name / "serve.port"
    )
    app["_port_files"] = [port_file, default_rendezvous]

    async def _close_db(app):
        try:
            app["memory_db"].close()
        except Exception:
            log.exception("memory_db close failed")

    # Filled once the site is bound; the app is frozen by then, so the port
    # lives in this closure rather than in app state.
    bound: dict[str, int] = {}

    async def _unlink_port_files(app):
        # Covers graceful shutdown paths only. SIGKILL bypasses
        # `app.on_cleanup` entirely, so the residual-port-file class of
        # bugs (#66) still needs the corresponding socket-liveness probe
        # in the hook shell script. What this handler does cleanly cover
        # is the orderly SIGINT/SIGTERM/Ctrl-D path so the next session
        # doesn't inherit a stale serve.port from a normal exit.
        #
        # Several `cce serve` processes can share one project (one per
        # agent session, or several agents on the same repo). Each start
        # overwrites the port files with its own port, so by the time this
        # one exits they may name another live server: only remove a file
        # that still holds *this* server's port, or the survivors' hooks
        # lose their rendezvous.
        #
        # A file that does hold this server's port is handed over to the
        # most recent live peer rather than deleted: otherwise a short-lived
        # server started after a long-running one (e.g. a headless agent run
        # next to an interactive session) would leave the survivor without
        # a rendezvous on exit.
        #
        # The lock wait (up to _PORT_LOCK_TIMEOUT_S) and the peer probes are
        # blocking, so they run in a worker thread to keep the event loop
        # free for the other cleanup handlers.
        await asyncio.to_thread(
            _release_port_files,
            app.get("_port_files", []), bound.get("port"), lock_path, registry,
        )

    app.on_cleanup.append(_close_db)
    app.on_cleanup.append(_unlink_port_files)

    runner = web.AppRunner(app)
    await runner.setup()

    port = _find_free_port()
    site = web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()
    bound["port"] = port

    await asyncio.to_thread(
        _publish_port, [port_file, default_rendezvous], port, lock_path, registry
    )

    log.info("Memory hook server listening on 127.0.0.1:%d", port)
    return runner, port
