"""HTTP API server for remote context engine — exposes storage + compression endpoints.

Security model:
- Default bind is 127.0.0.1. Use --host 0.0.0.0 explicitly to expose on LAN.
- When bound to a non-loopback host, a bearer token is required. Set via the
  CCE_API_TOKEN env var; requests without a matching `Authorization: Bearer <token>`
  header get 401. Loopback requests skip auth for local development.
"""
import hmac
import os
from pathlib import Path

from context_engine.config import load_config, resolve_ollama_url, PROJECT_CONFIG_NAME
from context_engine.storage.local_backend import LocalBackend
from context_engine.utils import project_storage_dir
from context_engine.indexer.embedder import Embedder
from context_engine.compression.compressor import Compressor
from context_engine.models import Chunk, ChunkType, GraphNode, GraphEdge, NodeType, EdgeType

try:
    from aiohttp import web
except ImportError as e:
    raise ImportError(
        "aiohttp is required for HTTP serve mode. "
        "Install with: pip install 'code-context-engine[http]'"
    ) from e


_MAX_REQUEST_BYTES = 10 * 1024 * 1024  # 10 MB — generous for bulk ingest, not unbounded
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class ContextEngineHTTP:
    def __init__(self, backend: LocalBackend, embedder: Embedder, compressor: Compressor):
        self.backend = backend
        self.embedder = embedder
        self.compressor = compressor

    async def handle_vector_search(self, request: web.Request) -> web.Response:
        data = await _read_json(request)
        embedding = data["embedding"]
        top_k = data.get("top_k", 10)
        results = await self.backend.vector_search(embedding, top_k=top_k)
        return web.json_response({"results": [self._chunk_to_dict(c) for c in results]})

    async def handle_fts_search(self, request: web.Request) -> web.Response:
        data = await _read_json(request)
        query = data["query"]
        top_k = data.get("top_k", 30)
        results = await self.backend.fts_search(query, top_k=top_k)
        return web.json_response({"results": [{"id": i, "score": s} for i, s in results]})

    async def handle_chunks_by_ids(self, request: web.Request) -> web.Response:
        data = await _read_json(request)
        ids = data.get("ids", [])
        if not isinstance(ids, list):
            return web.json_response({"error": "ids must be a list"}, status=400)
        chunks = await self.backend.get_chunks_by_ids(ids)
        return web.json_response({"results": [self._chunk_to_dict(c) for c in chunks]})

    async def handle_graph_neighbors(self, request: web.Request) -> web.Response:
        data = await _read_json(request)
        node_id = data["node_id"]
        edge_type = EdgeType(data["edge_type"]) if data.get("edge_type") else None
        results = await self.backend.graph_neighbors(node_id, edge_type=edge_type)
        return web.json_response({"results": [self._node_to_dict(n) for n in results]})

    async def handle_ingest(self, request: web.Request) -> web.Response:
        data = await _read_json(request)
        chunks = [self._dict_to_chunk(d) for d in data.get("chunks", [])]
        nodes = [self._dict_to_node(d) for d in data.get("nodes", [])]
        edges = [self._dict_to_edge(d) for d in data.get("edges", [])]
        await self.backend.ingest(chunks, nodes, edges)
        return web.json_response({"ok": True})

    async def handle_get_chunk(self, request: web.Request) -> web.Response:
        chunk_id = request.match_info["chunk_id"]
        chunk = await self.backend.get_chunk_by_id(chunk_id)
        if chunk is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(self._chunk_to_dict(chunk))

    async def handle_delete_file(self, request: web.Request) -> web.Response:
        file_path = request.match_info["file_path"]
        # Reject absolute paths and traversal — delete_by_file is SQL-only today,
        # but treating file_path as a relative project path is a safer contract.
        if file_path.startswith("/") or ".." in Path(file_path).parts:
            return web.json_response({"error": "invalid file_path"}, status=400)
        await self.backend.delete_by_file(file_path)
        return web.json_response({"ok": True})

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    def _chunk_to_dict(self, chunk):
        return {"id": chunk.id, "content": chunk.content, "chunk_type": chunk.chunk_type.value,
                "file_path": chunk.file_path, "start_line": chunk.start_line, "end_line": chunk.end_line,
                "language": chunk.language, "embedding": chunk.embedding, "metadata": chunk.metadata}

    def _dict_to_chunk(self, d):
        return Chunk(id=d["id"], content=d["content"], chunk_type=ChunkType(d["chunk_type"]),
                     file_path=d["file_path"], start_line=d["start_line"], end_line=d["end_line"],
                     language=d["language"], embedding=d.get("embedding"), metadata=d.get("metadata", {}))

    def _node_to_dict(self, node):
        return {"id": node.id, "node_type": node.node_type.value, "name": node.name, "file_path": node.file_path}

    def _dict_to_node(self, d):
        return GraphNode(id=d["id"], node_type=NodeType(d["node_type"]), name=d["name"], file_path=d["file_path"])

    def _dict_to_edge(self, d):
        return GraphEdge(source_id=d["source_id"], target_id=d["target_id"], edge_type=EdgeType(d["edge_type"]))


async def _read_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception as e:
        raise web.HTTPBadRequest(
            text=f'{{"error": "invalid JSON: {type(e).__name__}"}}',
            content_type="application/json",
        )


@web.middleware
async def _error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KeyError as e:
        return web.json_response({"error": f"missing field: {e.args[0]}"}, status=400)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


def _make_auth_middleware(expected_token: str | None):
    @web.middleware
    async def _auth(request, handler):
        # Health check is always open — used by liveness probes.
        if request.path == "/health":
            return await handler(request)

        remote = request.remote or ""
        # Loopback requests skip auth regardless of token setting — local dev UX.
        if remote in _LOOPBACK_HOSTS:
            return await handler(request)

        if not expected_token:
            # Bound to non-loopback but no token configured: refuse. Prevents
            # accidentally exposing an unauthenticated server to a network.
            return web.json_response(
                {"error": "server is not configured for non-loopback access; set CCE_API_TOKEN"},
                status=503,
            )

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "missing bearer token"}, status=401)
        presented = auth_header[len("Bearer "):]
        if not hmac.compare_digest(presented, expected_token):
            return web.json_response({"error": "invalid token"}, status=401)
        return await handler(request)

    return _auth


def create_app(backend, embedder, compressor, *, api_token: str | None = None) -> web.Application:
    handler = ContextEngineHTTP(backend, embedder, compressor)
    app = web.Application(
        client_max_size=_MAX_REQUEST_BYTES,
        middlewares=[_make_auth_middleware(api_token), _error_middleware],
    )
    app.router.add_get("/health", handler.handle_health)
    app.router.add_post("/vector_search", handler.handle_vector_search)
    app.router.add_post("/fts_search", handler.handle_fts_search)
    app.router.add_post("/chunks_by_ids", handler.handle_chunks_by_ids)
    app.router.add_post("/graph_neighbors", handler.handle_graph_neighbors)
    app.router.add_post("/ingest", handler.handle_ingest)
    app.router.add_get("/chunk/{chunk_id}", handler.handle_get_chunk)
    app.router.add_delete("/file/{file_path:.*}", handler.handle_delete_file)
    return app


def run_http_server(config=None, host: str = "127.0.0.1", port: int = 8765) -> None:
    if config is None:
        project_path = Path.cwd() / PROJECT_CONFIG_NAME
        config = load_config(project_path=project_path if project_path.exists() else None)

    storage_base = project_storage_dir(config, Path.cwd())
    storage_base.mkdir(parents=True, exist_ok=True)

    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    compressor = Compressor(
        model=config.compression_model,
        ollama_url=resolve_ollama_url(config),
        cache=backend,
    )

    api_token = os.environ.get("CCE_API_TOKEN") or None
    if host not in _LOOPBACK_HOSTS and not api_token:
        raise SystemExit(
            f"Refusing to bind {host}:{port} without CCE_API_TOKEN set. "
            "Either bind --host 127.0.0.1 or export CCE_API_TOKEN=<secret>."
        )

    app = create_app(backend, embedder, compressor, api_token=api_token)
    print(f"Context engine HTTP server starting on {host}:{port}")
    if api_token:
        print("Auth: bearer token required for non-loopback requests")
    web.run_app(app, host=host, port=port, print=None)
