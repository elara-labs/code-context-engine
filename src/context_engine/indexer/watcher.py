"""File watcher with debouncing using watchdog.

Watches a directory for file changes and triggers an async callback
after a debounce period. Used by `cce serve` to keep the index
up-to-date as files are saved.
"""
import asyncio
import logging
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

log = logging.getLogger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, on_change, debounce_ms, ignore_patterns, watch_dir, loop):
        self._on_change = on_change
        self._debounce_s = debounce_ms / 1000.0
        self._ignore_set = set(ignore_patterns)
        self._watch_dir = Path(watch_dir)
        self._loop = loop
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _should_ignore(self, path: str) -> bool:
        """Check if any path component matches an ignore pattern."""
        try:
            rel = Path(path).relative_to(self._watch_dir)
        except ValueError:
            return False
        for part in rel.parts:
            if part in self._ignore_set:
                return True
            # Always skip CCE's own storage/index files
            if part == ".cce":
                return True
        return False

    def on_any_event(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if self._should_ignore(path):
            return
        with self._lock:
            self._pending[path] = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._flush)
            self._timer.start()

    def _flush(self):
        with self._lock:
            paths = list(self._pending.keys())
            self._pending.clear()
        for path in paths:
            try:
                asyncio.run_coroutine_threadsafe(self._on_change(path), self._loop)
            except RuntimeError:
                # Loop closed — shutting down
                pass


class FileWatcher:
    """Watch a directory for file changes with debounced async callbacks."""

    def __init__(self, watch_dir, on_change, debounce_ms=500, ignore_patterns=None):
        self._watch_dir = watch_dir
        self._on_change = on_change
        self._debounce_ms = debounce_ms
        self._ignore_patterns = ignore_patterns or []
        self._observer = None
        self._handler = None

    def start(self, loop=None):
        """Start watching. Pass the running asyncio loop explicitly."""
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
        self._handler = _DebouncedHandler(
            on_change=self._on_change,
            debounce_ms=self._debounce_ms,
            ignore_patterns=self._ignore_patterns,
            watch_dir=self._watch_dir,
            loop=loop,
        )
        self._observer = Observer()
        self._observer.schedule(self._handler, self._watch_dir, recursive=True)
        self._observer.daemon = True
        self._observer.start()
        log.debug("Watcher started for %s", self._watch_dir)

    def stop(self):
        if self._handler:
            with self._handler._lock:
                if self._handler._timer:
                    self._handler._timer.cancel()
                    self._handler._timer = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            log.debug("Watcher stopped")
