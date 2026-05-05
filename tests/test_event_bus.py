
import pytest

from context_engine.event_bus import EventBus


@pytest.mark.asyncio
async def test_subscribe_and_emit():
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("file_changed", handler)
    await bus.emit("file_changed", {"path": "src/main.py"})
    assert len(received) == 1
    assert received[0]["path"] == "src/main.py"


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    results = []

    async def handler_a(data):
        results.append(("a", data))

    async def handler_b(data):
        results.append(("b", data))

    bus.subscribe("indexed", handler_a)
    bus.subscribe("indexed", handler_b)
    await bus.emit("indexed", {"file": "x.py"})
    assert len(results) == 2


@pytest.mark.asyncio
async def test_emit_no_subscribers():
    bus = EventBus()
    await bus.emit("unknown_event", {})  # should not raise


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    await bus.emit("evt", {"x": 1})
    assert len(received) == 0
