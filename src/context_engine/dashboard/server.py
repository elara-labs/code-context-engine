"""FastAPI dashboard server for CCE index inspection."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import quote

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from context_engine.config import Config
from context_engine.dashboard._page import PAGE_HTML
from context_engine.indexer.pipeline import PathOutsideProjectError, run_indexing
from context_engine.storage.local_backend import LocalBackend

# Mutating HTTP methods require a same-origin browser request OR a non-browser
# client (Sec-Fetch-Site absent). This blocks CSRF from a malicious local page
# without breaking the dashboard's own fetch() calls or curl/tests.
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Optional bearer token for mutating endpoints. When CCE_DASHBOARD_TOKEN is set
# in the environment, mutating requests must include `Authorization: Bearer
# <token>` (the dashboard JS picks the token up from a `?token=` URL param so
# users can paste a single URL into a browser). When the env var is unset the
# dashboard remains open like before — the CSRF check above is the only guard.
_DASHBOARD_TOKEN_ENV = "CCE_DASHBOARD_TOKEN"


class ReindexRequest(BaseModel):
    full: bool = False


class CompressionRequest(BaseModel):
    level: Literal["off", "lite", "standard", "max"]


def create_app(config: Config, project_dir: Path) -> FastAPI:
    """Build and return the FastAPI application.

    All route handlers close over `storage_base` and `project_dir` so the
    app is self-contained and trivial to test with TestClient.
    """
    project_name = project_dir.name
    storage_base = Path(config.storage_path) / project_name

    app = FastAPI(title="CCE Dashboard", docs_url=None, redoc_url=None)

    expected_token = (os.environ.get(_DASHBOARD_TOKEN_ENV) or "").strip() or None

    @app.middleware("http")
    async def csrf_and_auth(request: Request, call_next):
        if request.method in _MUTATING_METHODS:
            # CSRF: browser cross-origin requests are rejected. Non-browser
            # clients (curl, tests) don't send Sec-Fetch-Site at all and are
            # allowed to proceed to the auth check.
            sfs = request.headers.get("sec-fetch-site")
            if sfs is not None and sfs != "same-origin":
                return JSONResponse(
                    {"error": "cross-origin requests not allowed"},
                    status_code=403,
                )
            # Auth: only enforced when CCE_DASHBOARD_TOKEN is set. Use
            # constant-time comparison so a token-guessing attacker can't
            # learn anything from response timing.
            if expected_token is not None:
                auth = request.headers.get("authorization", "")
                presented = ""
                if auth.startswith("Bearer "):
                    presented = auth[len("Bearer "):]
                if not presented or not hmac.compare_digest(presented, expected_token):
                    return JSONResponse(
                        {"error": "invalid or missing bearer token"},
                        status_code=401,
                    )
        return await call_next(request)

    backend = LocalBackend(base_path=str(storage_base))

    # ── helpers ────────────────────────────────────────────────────────────

    def _read_json(path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _read_manifest() -> dict[str, str]:
        """Return {file_path: content_hash} regardless of on-disk schema.

        Manifest.save() writes {"__schema_version": 2, "files": {...},
        "last_git_sha": ...}. Older installs may have left the flat
        {file_path: hash} form. Both shapes collapse to the same dict here.
        """
        raw = _read_json(storage_base / "manifest.json")
        if isinstance(raw.get("files"), dict):
            return raw["files"]
        # Legacy flat manifest, or empty / unreadable file.
        return raw if raw and "__schema_version" not in raw else {}

    def _read_stats() -> dict:
        return _read_json(storage_base / "stats.json")

    def _read_state() -> dict:
        return _read_json(storage_base / "state.json")

    def _read_sessions(limit: int = 20) -> list[dict]:
        sessions_dir = storage_base / "sessions"
        if not sessions_dir.exists():
            return []
        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for f in files[:limit]:
            try:
                result.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                pass
        return result

    # ── routes ─────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def serve_page() -> str:
        return PAGE_HTML

    @app.get("/api/status")
    async def get_status() -> dict:
        stats = _read_stats()
        manifest = _read_manifest()
        state = _read_state()

        try:
            chunks = backend.count_chunks()
        except Exception:
            chunks = 0

        full_file = stats.get("full_file_tokens", 0)
        served = stats.get("served_tokens", 0)
        baseline = full_file if full_file > 0 else stats.get("raw_tokens", 0)
        saved_pct = max(0, int((1 - served / baseline) * 100)) if baseline > 0 else 0

        output_level = state.get("output_level", config.output_compression)

        return {
            "project": project_name,
            "initialized": bool(manifest),
            "chunks": chunks,
            "files": len(manifest),
            "queries": stats.get("queries", 0),
            "tokens_saved_pct": saved_pct,
            "output_level": output_level,
        }

    @app.get("/api/files")
    async def get_files() -> list:
        manifest = _read_manifest()
        if not manifest:
            return []

        chunk_counts = backend.file_chunk_counts()

        result = []
        for rel_path, stored_hash in sorted(manifest.items()):
            abs_path = project_dir / rel_path
            if not abs_path.exists():
                status = "missing"
            else:
                try:
                    current = abs_path.read_text(encoding="utf-8", errors="strict")
                    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
                    status = "ok" if current_hash == stored_hash else "stale"
                except (UnicodeDecodeError, OSError):
                    status = "ok"  # binary file, trust the manifest
            result.append({
                "path": rel_path,
                "chunks": chunk_counts.get(rel_path, 0),
                "status": status,
            })
        return result

    @app.get("/api/sessions")
    async def get_sessions() -> list:
        return _read_sessions()

    # ── memory.db API (PR 5) ────────────────────────────────────────────────
    # These endpoints expose the per-project memory.db introduced in PRs 1–4.
    # All queries open a fresh connection (cheap on SQLite WAL) so the
    # dashboard process never holds a long-lived handle that would block the
    # MCP server's writes.

    def _open_memory_conn():
        from context_engine.memory import db as memory_db
        path = memory_db.memory_db_path(storage_base)
        if not path.exists():
            return None
        return memory_db.connect(path)

    @app.get("/api/memory/sessions")
    async def memory_sessions_list(limit: int = 50) -> list:
        """Sessions list: most-recent first. Limited to `limit` rows."""
        conn = _open_memory_conn()
        if conn is None:
            return []
        try:
            rows = list(conn.execute(
                "SELECT id, project, started_at, ended_at, status, "
                "prompt_count, rollup_summary FROM sessions "
                "ORDER BY started_at_epoch DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ))
        finally:
            conn.close()
        return [dict(r) for r in rows]

    @app.get("/api/memory/sessions/{session_id}/timeline")
    async def memory_session_timeline(session_id: str) -> dict:
        """A single session's turn summaries plus header metadata."""
        conn = _open_memory_conn()
        if conn is None:
            return {"session": None, "turns": []}
        try:
            session_row = conn.execute(
                "SELECT id, project, started_at, ended_at, status, "
                "prompt_count, rollup_summary FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                return {"session": None, "turns": []}
            turn_rows = list(conn.execute(
                "SELECT prompt_number, summary, tier, created_at_epoch "
                "FROM turn_summaries WHERE session_id = ? "
                "ORDER BY prompt_number ASC",
                (session_id,),
            ))
        finally:
            conn.close()
        return {
            "session": dict(session_row),
            "turns": [dict(r) for r in turn_rows],
        }

    @app.get("/api/memory/decisions")
    async def memory_decisions_search(
        q: str = "", source: str | None = None, limit: int = 50
    ) -> list:
        """FTS5 search over decisions, optionally filtered by source."""
        conn = _open_memory_conn()
        if conn is None:
            return []
        limit = max(1, min(limit, 500))
        try:
            params: list = []
            sql = (
                "SELECT d.id, d.session_id, d.decision, d.reason, d.source, "
                "d.created_at FROM decisions d "
            )
            if q.strip():
                sql += (
                    "JOIN decisions_fts ON decisions_fts.rowid = d.id "
                    "WHERE decisions_fts MATCH ? "
                )
                # Wrap in a phrase quote so FTS5's tokeniser treats user
                # input as literal text — without this, characters like '-'
                # parse as operators (`bge-small` becomes "bge AND NOT
                # small") and queries silently miss real hits.
                params.append('"' + q.strip().replace('"', '""') + '"')
            else:
                sql += "WHERE 1=1 "
            if source in {"manual", "auto", "migrated"}:
                sql += "AND d.source = ? "
                params.append(source)
            sql += "ORDER BY d.created_at_epoch DESC LIMIT ?"
            params.append(limit)
            try:
                rows = list(conn.execute(sql, params))
            except Exception:
                # FTS5 syntax errors on weird queries — fall back to unranked.
                rows = list(conn.execute(
                    "SELECT id, session_id, decision, reason, source, "
                    "created_at FROM decisions ORDER BY created_at_epoch "
                    "DESC LIMIT ?",
                    (limit,),
                ))
        finally:
            conn.close()
        return [dict(r) for r in rows]

    @app.get("/api/savings")
    async def get_savings() -> dict:
        stats = _read_stats()
        full_file = stats.get("full_file_tokens", 0)
        served = stats.get("served_tokens", 0)
        raw = stats.get("raw_tokens", 0)
        baseline = full_file if full_file > 0 else raw
        saved = max(0, baseline - served)
        pct = int(saved / baseline * 100) if baseline > 0 else 0
        return {
            "queries": stats.get("queries", 0),
            "baseline_tokens": baseline,
            "served_tokens": served,
            "tokens_saved": saved,
            "savings_pct": pct,
        }

    # ── action routes ──────────────────────────────────────────────────────

    @app.post("/api/reindex")
    async def reindex(req: ReindexRequest) -> dict:
        result = await run_indexing(config, project_dir, full=req.full)
        return {
            "total_chunks": result.total_chunks,
            "indexed_files": result.indexed_files,
            "deleted_files": result.deleted_files,
            "skipped_files": result.skipped_files,
            "errors": result.errors,
        }

    @app.post("/api/reindex/{file_path:path}", response_model=None)
    async def reindex_file(file_path: str) -> dict | JSONResponse:
        try:
            result = await run_indexing(config, project_dir, target_path=file_path)
        except PathOutsideProjectError:
            return JSONResponse({"error": "invalid file_path"}, status_code=400)
        return {
            "total_chunks": result.total_chunks,
            "indexed_files": result.indexed_files,
            "deleted_files": result.deleted_files,
            "skipped_files": result.skipped_files,
            "errors": result.errors,
        }

    @app.post("/api/clear")
    async def clear_index() -> dict:
        await backend.clear()
        from context_engine.utils import atomic_write_text

        atomic_write_text(
            storage_base / "manifest.json",
            json.dumps({"__schema_version": 2, "files": {}, "last_git_sha": None}),
        )
        atomic_write_text(
            storage_base / "stats.json",
            json.dumps({"queries": 0, "raw_tokens": 0, "served_tokens": 0, "full_file_tokens": 0}),
        )
        return {"ok": True}

    @app.delete("/api/files/{file_path:path}", response_model=None)
    async def delete_file(file_path: str) -> dict | JSONResponse:
        # Reject absolute paths and traversal — the manifest stores project-relative
        # paths, so anything else is either an attacker probe or a bug.
        if file_path.startswith("/") or ".." in Path(file_path).parts:
            return JSONResponse({"error": "invalid file_path"}, status_code=400)
        files = _read_manifest()
        if file_path not in files:
            return JSONResponse({"error": "file not indexed"}, status_code=404)
        await backend.delete_by_file(file_path)
        files.pop(file_path, None)
        # Preserve schema fields (last_git_sha) when rewriting.
        raw = _read_json(storage_base / "manifest.json")
        if isinstance(raw.get("files"), dict):
            raw["files"] = files
            payload = raw
        else:
            payload = {"__schema_version": 2, "files": files, "last_git_sha": None}
        from context_engine.utils import atomic_write_text

        atomic_write_text(storage_base / "manifest.json", json.dumps(payload))
        return {"ok": True, "deleted": file_path}

    @app.post("/api/compression")
    async def set_compression(req: CompressionRequest) -> dict:
        state = _read_state()
        state["output_level"] = req.level
        (storage_base / "state.json").write_text(json.dumps(state))
        return {"level": req.level}

    @app.get("/api/export")
    async def export_data():
        payload = {
            "project": project_name,
            "stats": _read_stats(),
            "manifest": _read_manifest(),
            "sessions": _read_sessions(),
        }
        safe_name = quote(project_name, safe="")
        return Response(
            content=json.dumps(payload, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={safe_name}-cce-export.json"
            },
        )

    return app
