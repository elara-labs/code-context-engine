"""Resource governor — prevents multi-instance CCE from overwhelming the host.

Addresses issue #139: dozens of ``cce serve`` processes (one per project per
AI session) exhaust system memory and cause 10–20 minute desktop freezes on
Linux.

Four mechanisms:

1. **ONNX Runtime thread caps** — each process gets a bounded number of
   intra-op/inter-op threads instead of the OS default (often == CPU count).
2. **Per-project index lock** — only one process indexes a given project at
   a time; others skip and retry later.
3. **Memory-pressure backoff** — on Linux, reads ``/proc/pressure/memory``
   (PSI) and pauses heavy work when the system is under pressure. On other
   platforms this is a no-op (always returns False).
4. **Idle shutdown** — ``cce serve`` exits after N minutes of MCP inactivity
   so zombie processes don't accumulate.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# ── 1. ONNX Runtime thread caps ───────────────────────────────────────────

# How many threads each ONNX Runtime session (= one CCE embedding process)
# may use.  With 68 CCE processes, uncapped threads (default = cpu_count)
# create thousands of OS threads competing for cores and page-cache, which
# is the primary amplifier of the OOM/freeze scenario.
#
# Env-var ``CCE_ORT_THREADS`` overrides; 0 means "use ONNX default".
_DEFAULT_ORT_THREADS = 2


def cap_ort_threads(max_threads: int | None = None) -> int:
    """Set ONNX Runtime thread-count env vars *before* any model is loaded.

    Must be called before ``fastembed.TextEmbedding()`` or any other ONNX
    import.  Returns the value that was applied.
    """
    raw = os.environ.get("CCE_ORT_THREADS", "").strip()
    if raw:
        try:
            n = int(raw)
        except ValueError:
            n = _DEFAULT_ORT_THREADS
    elif max_threads is not None:
        n = max_threads
    else:
        n = _DEFAULT_ORT_THREADS

    if n <= 0:
        # Even when ORT caps are disabled, prevent the tokenizers library
        # from spawning extra threads — it is independent of ORT.
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        return 0  # 0 = "let ONNX pick"

    # Force-set rather than setdefault — pre-existing values (common on dev
    # machines / CI) would silently override the cap and defeat the purpose.
    cap = str(n)
    for var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "ORT_NUM_THREADS",       # some ORT builds read this directly
    ):
        os.environ[var] = cap

    # tokenizers library (used by fastembed) also spawns threads
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    log.debug("ORT thread cap applied: %d", n)
    return n


# ── 2. Per-project index lock ──────────────────────────────────────────────

class ProjectIndexLock:
    """File-lock so only one ``cce serve`` process indexes a given project.

    Usage::

        lock = ProjectIndexLock(storage_base)
        if lock.try_acquire():
            try:
                await run_indexing(...)
            finally:
                lock.release()
        else:
            log.debug("Another process is indexing; skipping this cycle")

    The lock file lives at ``{storage_base}/.index.lock``.  ``fcntl.flock``
    is advisory but sufficient — all competing processes are CCE.
    """

    def __init__(self, storage_base: Path) -> None:
        self._lock_path = storage_base / ".index.lock"
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        """Non-blocking attempt. Returns True if this process now holds it."""
        if fcntl is None:
            return True  # no-op on Windows
        fd = -1
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = fd
            return True
        except (OSError, BlockingIOError):
            # Close the fd if open() succeeded but flock() failed,
            # otherwise we leak a file descriptor on every failed attempt.
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self):
        return self.try_acquire()

    def __exit__(self, *_exc):
        self.release()


# ── 3. Memory-pressure detection (Linux PSI) ──────────────────────────────

_PSI_PATH = Path("/proc/pressure/memory")
# ``some avg10`` above this threshold means the system is under meaningful
# memory pressure.  85% was the peak observed in the issue report; we use
# a much lower trip-wire so CCE backs off before things get critical.
_PSI_THRESHOLD_PCT = 25.0


def is_memory_pressured() -> bool:
    """Return True when the host is under significant memory pressure.

    On Linux, reads PSI (Pressure Stall Information).  On other platforms
    returns False (no-op).
    """
    if sys.platform != "linux":
        return False
    try:
        text = _PSI_PATH.read_text()
        # Format: "some avg10=X.XX avg60=X.XX avg300=X.XX total=N"
        for line in text.splitlines():
            if line.startswith("some "):
                for part in line.split():
                    if part.startswith("avg10="):
                        val = float(part.split("=", 1)[1])
                        if val > _PSI_THRESHOLD_PCT:
                            return True
                break
    except Exception:
        pass
    return False


# ── 4. Idle-timeout tracker ───────────────────────────────────────────────

_DEFAULT_IDLE_TIMEOUT_MINUTES = 30


class IdleTracker:
    """Track MCP activity and signal when the server has been idle too long.

    ``touch()`` is called on every MCP tool invocation.  ``is_idle()``
    returns True when no tool has been called for ``timeout_minutes``.
    """

    def __init__(self, timeout_minutes: int | None = None) -> None:
        raw = os.environ.get("CCE_IDLE_TIMEOUT_MINUTES", "").strip()
        if raw:
            try:
                timeout_minutes = int(raw)
            except ValueError:
                pass
        if timeout_minutes is None:
            timeout_minutes = _DEFAULT_IDLE_TIMEOUT_MINUTES
        # 0 or negative = disabled
        self.timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else 0
        self._last_activity = time.monotonic()

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    def is_idle(self) -> bool:
        if self.timeout_seconds <= 0:
            return False
        return (time.monotonic() - self._last_activity) > self.timeout_seconds

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity
