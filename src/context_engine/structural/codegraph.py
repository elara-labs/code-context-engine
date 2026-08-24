"""CodeGraph shared-base structural provider."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from context_engine.structural.base import ProviderStatus
from context_engine.structural.models import SourceRange, StructuralContext, SymbolKey


DEFAULT_CODEGRAPH_TIMEOUT_SECONDS = 8
DEFAULT_CODEGRAPH_STDOUT_LIMIT = 1_000_000


class CodeGraphClient:
    def __init__(
        self,
        *,
        executable: str = "codegraph",
        timeout_seconds: int = DEFAULT_CODEGRAPH_TIMEOUT_SECONDS,
        stdout_limit: int = DEFAULT_CODEGRAPH_STDOUT_LIMIT,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._stdout_limit = stdout_limit

    def status(self, project_root: Path) -> dict[str, Any]:
        return self._run_json(project_root, ["status", "--json"])

    def query(self, project_root: Path, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        data = self._run_json(project_root, ["query", query, "--limit", str(limit), "--json"])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results = data.get("results", [])
            return results if isinstance(results, list) else []
        return []

    def impact(self, project_root: Path, symbol: str, *, depth: int = 2) -> dict[str, Any]:
        return self._run_json(project_root, ["impact", symbol, "--depth", str(depth), "--json"])

    def _run_json(self, project_root: Path, args: list[str]) -> Any:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file:
            process = subprocess.Popen(
                [self._executable, *args],
                cwd=project_root,
                stdout=stdout_file,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _, stderr = process.communicate(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise

            stdout_file.seek(0, 2)
            stdout_size = stdout_file.tell()
            stdout_file.seek(0)
            stdout = stdout_file.read(self._stdout_limit + 1)

        if stdout_size > self._stdout_limit:
            raise CodeGraphError("CodeGraph JSON response too large")
        if process.returncode != 0:
            detail = (stderr or "").strip() or stdout.strip() or f"exit {process.returncode}"
            raise CodeGraphError(detail[:1000])
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CodeGraphError(f"invalid JSON from CodeGraph: {exc.msg}") from exc


class CodeGraphBaseProvider:
    provider_name = "codegraph"

    def __init__(self, client: CodeGraphClient | None = None) -> None:
        self._client = client or CodeGraphClient()

    async def status(self, project_root: Path) -> ProviderStatus:
        try:
            status = self._client.status(project_root)
        except (CodeGraphError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return ProviderStatus(self.provider_name, False, str(exc))
        available = (
            bool(status.get("initialized"))
            and status.get("index", {}).get("state") == "complete"
        )
        return ProviderStatus(
            provider=self.provider_name,
            available=available,
            detail="" if available else "CodeGraph index unavailable or incomplete",
            metadata=status,
        )

    async def explore(self, query: str, project_root: Path) -> StructuralContext:
        rows = self._client.query(project_root, query)
        return StructuralContext(
            sources=[_source_from_query_row(row) for row in rows],
            provider=self.provider_name,
            metadata={"query": query},
        )

    async def impact(self, symbol: str, project_root: Path) -> StructuralContext:
        data = self._client.impact(project_root, symbol)
        affected = data.get("affected", []) if isinstance(data, dict) else []
        return StructuralContext(
            impact=[_symbol_from_impact_row(row) for row in affected if isinstance(row, dict)],
            provider=self.provider_name,
            metadata={"symbol": symbol, "raw": data},
        )


class CodeGraphError(RuntimeError):
    pass


def _source_from_query_row(row: dict[str, Any]) -> SourceRange:
    node = row.get("node", row)
    return SourceRange(
        path=str(node.get("filePath") or node.get("file_path") or ""),
        start_line=int(node.get("startLine") or node.get("start_line") or 0),
        end_line=int(node.get("endLine") or node.get("end_line") or node.get("startLine") or 0),
        content=node.get("code") or node.get("content"),
    )


def _symbol_from_impact_row(row: dict[str, Any]) -> SymbolKey:
    line = row.get("startLine") or row.get("start_line")
    return SymbolKey(
        qualified_name=str(row.get("name") or ""),
        kind=row.get("kind"),
        path=row.get("filePath") or row.get("file_path"),
        signature=row.get("signature"),
        line=int(line) if line is not None else None,
    )
