"""Tests for CodeGraph structural provider adapter."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from context_engine.structural import CodeGraphBaseProvider, CodeGraphClient


class CompletedProcess:
    def __init__(self, stdout: object, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = json.dumps(stdout)
        self.returncode = returncode
        self.stderr = stderr

    def communicate(self, timeout=None):
        self.stdout_file.write(self.stdout)
        self.stdout_file.flush()
        return None, self.stderr


def test_client_runs_codegraph_json_without_shell():
    process = CompletedProcess({"initialized": True})
    with patch("subprocess.Popen", side_effect=bind_process(process)) as popen:
        result = CodeGraphClient(executable="cg", timeout_seconds=3).status(Path("/repo"))

    assert result == {"initialized": True}
    args, kwargs = popen.call_args
    assert args[0] == ["cg", "status", "--json"]
    assert kwargs["cwd"] == Path("/repo")
    assert kwargs["text"] is True
    assert "shell" not in kwargs


def test_client_raises_short_error_on_nonzero_exit():
    process = CompletedProcess({}, returncode=1, stderr="missing index")
    with patch("subprocess.Popen", side_effect=bind_process(process)):
        with pytest.raises(RuntimeError, match="missing index"):
            CodeGraphClient().status(Path("/repo"))


def test_client_reports_oversized_json_without_parsing_truncated_stdout():
    process = CompletedProcess({"items": ["x" * 200]})
    with patch("subprocess.Popen", side_effect=bind_process(process)):
        with pytest.raises(RuntimeError, match="response too large"):
            CodeGraphClient(stdout_limit=10).status(Path("/repo"))


def bind_process(process):
    def _popen(*args, **kwargs):
        process.stdout_file = kwargs["stdout"]
        return process

    return _popen


@pytest.mark.asyncio
async def test_provider_status_reports_complete_index_available():
    provider = CodeGraphBaseProvider(client=StubClient(status={"initialized": True, "index": {"state": "complete"}}))

    status = await provider.status(Path("/repo"))

    assert status.available is True
    assert status.provider == "codegraph"


@pytest.mark.asyncio
async def test_provider_status_reports_indexing_unavailable():
    provider = CodeGraphBaseProvider(client=StubClient(status={"initialized": True, "index": {"state": "indexing"}}))

    status = await provider.status(Path("/repo"))

    assert status.available is False


@pytest.mark.asyncio
async def test_provider_status_reports_degraded_state_unavailable():
    provider = CodeGraphBaseProvider(client=StubClient(status={"initialized": True, "index": {"state": "degraded"}}))

    status = await provider.status(Path("/repo"))

    assert status.available is False


@pytest.mark.asyncio
async def test_provider_maps_query_rows_to_source_ranges():
    provider = CodeGraphBaseProvider(
        client=StubClient(query=[
            {"node": {"filePath": "src/auth.py", "startLine": 4, "endLine": 9, "code": "def login(): ..."}}
        ])
    )

    context = await provider.explore("login", Path("/repo"))

    assert context.provider == "codegraph"
    assert context.sources[0].path == "src/auth.py"
    assert context.sources[0].start_line == 4
    assert context.sources[0].content == "def login(): ..."


@pytest.mark.asyncio
async def test_provider_maps_impact_rows_to_symbol_keys():
    provider = CodeGraphBaseProvider(
        client=StubClient(impact={"affected": [{"name": "login", "kind": "function", "filePath": "src/auth.py"}]})
    )

    context = await provider.impact("login", Path("/repo"))

    assert context.impact[0].qualified_name == "login"
    assert context.impact[0].kind == "function"
    assert context.impact[0].path == "src/auth.py"


class StubClient:
    def __init__(self, *, status=None, query=None, impact=None):
        self._status = status or {}
        self._query = query or []
        self._impact = impact or {}

    def status(self, project_root):
        return self._status

    def query(self, project_root, query, *, limit=20):
        return self._query

    def impact(self, project_root, symbol, *, depth=2):
        return self._impact
