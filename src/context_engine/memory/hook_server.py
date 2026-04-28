"""Loopback HTTP server for Claude Code hook payloads.

Bound to 127.0.0.1 on a random free port. The port is written to
`<storage_base>/serve.port` so the hook shell script can find it without
configuration. No auth — the listener is loopback-only.

Started as a background asyncio task from `_run_serve` (the MCP server
process). Stopped gracefully on shutdown.
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path

from aiohttp import web

from context_engine.memory import db as memory_db
from context_engine.memory.hooks import add_routes

log = logging.getLogger(__name__)


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

    async def _close_db(app):
        try:
            app["memory_db"].close()
        except Exception:
            log.exception("memory_db close failed")

    app.on_cleanup.append(_close_db)

    runner = web.AppRunner(app)
    await runner.setup()

    port = _find_free_port()
    site = web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()

    # Authoritative port file lives in the project's storage_base.
    port_file = Path(storage_base) / "serve.port"
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(port))

    # Stable rendezvous file at the *default* storage location. The hook
    # shell script always looks here (`${HOME}/.cce/projects/<name>/serve.port`)
    # because it has no way to read the user's config.yaml. When storage_path
    # is customised, this is the only way capture stays wired up.
    default_rendezvous = (
        Path.home() / ".cce" / "projects" / project_name / "serve.port"
    )
    try:
        if default_rendezvous.resolve() != port_file.resolve():
            default_rendezvous.parent.mkdir(parents=True, exist_ok=True)
            default_rendezvous.write_text(str(port))
    except OSError as exc:
        # Non-fatal — capture still works for users with default storage.
        log.warning("rendezvous port file write failed: %s", exc)

    log.info("Memory hook server listening on 127.0.0.1:%d", port)
    return runner, port
