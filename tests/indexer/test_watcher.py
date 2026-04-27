# tests/indexer/test_watcher.py
import asyncio
import time
from pathlib import Path
import pytest
from context_engine.indexer.watcher import FileWatcher


@pytest.mark.asyncio
async def test_watcher_detects_new_file(tmp_path):
    events = []

    async def on_change(path: str):
        events.append(path)

    watcher = FileWatcher(
        watch_dir=str(tmp_path), on_change=on_change,
        debounce_ms=100, ignore_patterns=[".git"],
    )
    watcher.start()
    test_file = tmp_path / "hello.py"
    test_file.write_text("print('hello')")
    await asyncio.sleep(0.5)
    watcher.stop()
    assert len(events) > 0
    assert any("hello.py" in e for e in events)


@pytest.mark.asyncio
async def test_watcher_ignores_patterns(tmp_path):
    events = []

    async def on_change(path: str):
        events.append(path)

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    watcher = FileWatcher(
        watch_dir=str(tmp_path), on_change=on_change,
        debounce_ms=100, ignore_patterns=[".git"],
    )
    watcher.start()
    (git_dir / "config").write_text("test")
    await asyncio.sleep(0.5)
    watcher.stop()
    assert not any(".git" in e for e in events)


@pytest.mark.asyncio
async def test_watcher_ignore_matches_component_not_substring(tmp_path):
    """Ignore pattern 'vendor' should not match 'vendor-utils' directory."""
    events = []

    async def on_change(path: str):
        events.append(path)

    # Create dirs: 'vendor' (should be ignored) and 'vendor-utils' (should NOT be ignored)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor-utils").mkdir()

    watcher = FileWatcher(
        watch_dir=str(tmp_path), on_change=on_change,
        debounce_ms=100, ignore_patterns=["vendor"],
    )
    watcher.start()
    (tmp_path / "vendor" / "lib.py").write_text("ignored")
    (tmp_path / "vendor-utils" / "helper.py").write_text("not ignored")
    await asyncio.sleep(0.5)
    watcher.stop()
    assert not any("vendor/lib.py" in e or "vendor\\lib.py" in e for e in events)
    assert any("helper.py" in e for e in events)


@pytest.mark.asyncio
async def test_watcher_always_ignores_cce_dirs(tmp_path):
    """'.cce' is always ignored even without explicit patterns."""
    events = []

    async def on_change(path: str):
        events.append(path)

    (tmp_path / ".cce").mkdir()

    watcher = FileWatcher(
        watch_dir=str(tmp_path), on_change=on_change,
        debounce_ms=100, ignore_patterns=[],
    )
    watcher.start()
    (tmp_path / ".cce" / "commands.yaml").write_text("test")
    (tmp_path / "real.py").write_text("should be seen")
    await asyncio.sleep(0.5)
    watcher.stop()
    assert not any(".cce" in e for e in events)
    assert any("real.py" in e for e in events)


@pytest.mark.asyncio
async def test_watcher_debounces(tmp_path):
    events = []

    async def on_change(path: str):
        events.append(path)

    watcher = FileWatcher(
        watch_dir=str(tmp_path), on_change=on_change,
        debounce_ms=300, ignore_patterns=[],
    )
    watcher.start()
    test_file = tmp_path / "rapid.py"
    for i in range(5):
        test_file.write_text(f"version {i}")
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.8)
    watcher.stop()
    assert len(events) < 5
