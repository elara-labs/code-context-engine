"""CSRF defense tests for the loopback HTTP server (issue #129).

A malicious web page can POST to http://127.0.0.1:<port>/ingest from
a browser. The auth middleware must reject such requests by checking the
Origin header on mutating methods and requiring application/json
Content-Type.
"""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from context_engine.serve_http import _make_auth_middleware


def _make_request(method: str, path: str, headers: dict | None = None, remote: str = "127.0.0.1"):
    """Build a mocked aiohttp Request for middleware testing."""
    req = make_mocked_request(method, path, headers=headers or {})
    # Override the remote property so it looks like a loopback client
    req._transport_peername = (remote, 12345)
    return req


async def _run_middleware(request, expected_token=None):
    """Run the auth middleware and return the Response (or None if handler was called)."""
    middleware_factory = _make_auth_middleware(expected_token)
    called = []

    async def handler(req):
        called.append(req)
        return web.Response(status=200, text="OK")

    # The middleware is a coroutine function decorated with @web.middleware
    response = await middleware_factory(request, handler)
    return response, called


@pytest.mark.asyncio
async def test_loopback_get_allowed_without_origin():
    """GET /health from loopback with no Origin header must return 200."""
    req = _make_request("GET", "/health")
    resp, called = await _run_middleware(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_loopback_post_ingest_cross_origin_rejected():
    """POST /ingest from loopback with a non-loopback Origin must be rejected with 403."""
    req = _make_request(
        "POST", "/ingest",
        headers={"Origin": "http://evil.com", "Content-Type": "application/json"},
    )
    resp, called = await _run_middleware(req)
    assert resp.status == 403
    assert not called, "Handler should not have been called for cross-origin request"


@pytest.mark.asyncio
async def test_loopback_post_ingest_no_origin_allowed():
    """POST /ingest from loopback with no Origin and correct Content-Type passes auth."""
    req = _make_request(
        "POST", "/ingest",
        headers={"Content-Type": "application/json"},
    )
    resp, called = await _run_middleware(req)
    # Auth middleware must not block it (handler is called or non-403/415 response)
    assert resp.status not in (403, 415), (
        f"Local tool call with no Origin was incorrectly blocked: {resp.status}"
    )


@pytest.mark.asyncio
async def test_loopback_post_wrong_content_type_rejected():
    """POST /ingest from loopback with Content-Type: text/plain must return 415."""
    req = _make_request(
        "POST", "/ingest",
        headers={"Content-Type": "text/plain"},
    )
    resp, called = await _run_middleware(req)
    assert resp.status == 415
    assert not called, "Handler should not have been called for wrong Content-Type"
