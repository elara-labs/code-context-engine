"""Remote storage backend — proxies DB + LLM operations to a remote server via SSH/HTTP."""
import asyncio
import httpx
from context_engine.models import Chunk, ChunkType, GraphNode, NodeType


class RemoteBackend:
    def __init__(self, host: str, port: int = 8765, fallback_to_local: bool = True):
        self.host = host
        self.port = port
        self.fallback_to_local = fallback_to_local
        if "@" in host:
            self._user, self._hostname = host.split("@", 1)
        else:
            self._user = None
            self._hostname = host
        self._api_base = f"http://{self._hostname}:{port}"

    async def is_reachable(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                self.host, "echo", "ok",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return b"ok" in stdout
        except (asyncio.TimeoutError, OSError):
            return False

    async def vector_search(self, query_embedding, top_k=10, filters=None):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/vector_search",
                    json={"embedding": query_embedding, "top_k": top_k, "filters": filters})
                resp.raise_for_status()
                return [self._dict_to_chunk(d) for d in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

    async def graph_neighbors(self, node_id, edge_type=None):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/graph_neighbors",
                    json={"node_id": node_id, "edge_type": edge_type.value if edge_type else None})
                resp.raise_for_status()
                return [self._dict_to_node(d) for d in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

    async def ingest(self, chunks, nodes, edges):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(f"{self._api_base}/ingest", json={
                    "chunks": [self._chunk_to_dict(c) for c in chunks],
                    "nodes": [self._node_to_dict(n) for n in nodes],
                    "edges": [self._edge_to_dict(e) for e in edges],
                })
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

    async def get_chunk_by_id(self, chunk_id):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._api_base}/chunk/{chunk_id}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._dict_to_chunk(resp.json())
        except (httpx.ConnectError, httpx.TimeoutException):
            return None

    async def delete_by_file(self, file_path):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(f"{self._api_base}/file/{file_path}")
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

    async def fts_search(self, query, top_k=30):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/fts/search",
                    json={"query": query, "top_k": top_k})
                resp.raise_for_status()
                return [(item["chunk_id"], item["score"]) for item in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

    async def get_chunks_by_ids(self, chunk_ids):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._api_base}/chunks/batch",
                    json={"chunk_ids": chunk_ids})
                resp.raise_for_status()
                return [self._dict_to_chunk(d) for d in resp.json()["results"]]
        except (httpx.ConnectError, httpx.TimeoutException):
            return []

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

    def _edge_to_dict(self, edge):
        return {"source_id": edge.source_id, "target_id": edge.target_id, "edge_type": edge.edge_type.value}
