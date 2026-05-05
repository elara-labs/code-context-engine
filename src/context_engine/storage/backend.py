"""Storage backend protocol — implemented by local and remote backends."""
from typing import Protocol, runtime_checkable

from context_engine.models import Chunk, GraphNode, GraphEdge, EdgeType


@runtime_checkable
class StorageBackend(Protocol):
    async def ingest(
        self,
        chunks: list[Chunk],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> None: ...

    async def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[Chunk]: ...

    async def graph_neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
    ) -> list[GraphNode]: ...

    async def get_chunk_by_id(self, chunk_id: str) -> Chunk | None: ...

    async def delete_by_file(self, file_path: str) -> None: ...

    async def fts_search(
        self,
        query: str,
        top_k: int = 30,
    ) -> list[tuple[str, float]]: ...

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]: ...
