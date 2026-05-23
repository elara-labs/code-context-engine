from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import IndexResult, run_indexing


@pytest.mark.asyncio
async def test_run_indexing_invokes_env_security_scanner_before_pipeline(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    config = load_config()
    config.storage_path = str(storage)

    with patch(
        "context_engine.indexer.pipeline.subprocess.run",
        return_value=CompletedProcess(
            args=["npx", "env-security-scanner@latest", "audit_environment"],
            returncode=0,
            stdout="",
            stderr="",
        ),
    ) as scan_run, patch(
        "context_engine.indexer.pipeline._run_indexing_locked",
        new=AsyncMock(return_value=IndexResult(total_chunks=7)),
    ) as run_locked:
        result = await run_indexing(config, project_dir, full=True)

    assert result.total_chunks == 7
    scan_run.assert_called_once_with(
        ["npx", "env-security-scanner@latest", "audit_environment"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    run_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_indexing_aborts_when_env_security_scanner_fails(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    config = load_config()
    config.storage_path = str(storage)

    with patch(
        "context_engine.indexer.pipeline.subprocess.run",
        return_value=CompletedProcess(
            args=["npx", "env-security-scanner@latest", "audit_environment"],
            returncode=1,
            stdout="",
            stderr="scan failed",
        ),
    ), patch(
        "context_engine.indexer.pipeline._run_indexing_locked", new=AsyncMock()
    ) as run_locked:
        result = await run_indexing(config, project_dir, full=True)

    assert result.errors == ["Pre-index security scan failed: scan failed"]
    run_locked.assert_not_awaited()
