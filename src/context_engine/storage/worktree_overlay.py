"""Semantic base+worktree overlay storage backend."""

from __future__ import annotations

import asyncio

from context_engine.git.diff import GitWorktreeDiff
from context_engine.models import Chunk, EdgeType, GraphEdge, GraphNode
from context_engine.storage.backend import StorageBackend


class WorktreeOverlayBackend:
    """Search shared base plus overlay while hiding stale base paths."""

    def __init__(
        self,
        *,
        base: StorageBackend,
        overlay: StorageBackend,
        diff: GitWorktreeDiff,
    ) -> None:
        self._base = base
        self._overlay = overlay
        self._diff = diff

    def update_diff(self, diff: GitWorktreeDiff) -> None:
        self._diff = diff

    async def ingest(
        self,
        chunks: list[Chunk],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> None:
        await self._overlay.ingest(chunks, nodes, edges)

    async def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[Chunk]:
        base_results, overlay_results = await asyncio.gather(
            self._base.vector_search(query_embedding, top_k=top_k, filters=filters),
            self._overlay.vector_search(query_embedding, top_k=top_k, filters=filters),
        )
        return self._merge_chunks(base_results, overlay_results, top_k)

    async def fts_search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        base_hits, overlay_hits = await asyncio.gather(
            self._base.fts_search(query, top_k=top_k),
            self._overlay.fts_search(query, top_k=top_k),
        )
        base_chunks = await self._base.get_chunks_by_ids(
            [chunk_id for chunk_id, _ in base_hits]
        )
        visible_base_ids = {chunk.id for chunk in self._visible_base_chunks(base_chunks)}
        return _merge_ranked_hits(
            [
                (rank, hit)
                for rank, hit in enumerate(base_hits, start=1)
                if hit[0] in visible_base_ids
            ],
            list(enumerate(overlay_hits, start=1)),
            top_k,
        )

    async def graph_neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
    ) -> list[GraphNode]:
        if _node_file_path(node_id) in self._diff.added | self._diff.modified:
            overlay_nodes = await self._overlay.graph_neighbors(node_id, edge_type)
            return _dedupe_nodes(
                [node for node in overlay_nodes if not self._is_deleted(node.file_path)]
            )

        base_nodes, overlay_nodes = await asyncio.gather(
            self._base.graph_neighbors(node_id, edge_type),
            self._overlay.graph_neighbors(node_id, edge_type),
        )
        return _dedupe_nodes(
            [
                *[node for node in overlay_nodes if not self._is_deleted(node.file_path)],
                *[node for node in base_nodes if not self._is_deleted(node.file_path)],
            ]
        )

    async def get_chunk_by_id(self, chunk_id: str) -> Chunk | None:
        chunk = await self._overlay.get_chunk_by_id(chunk_id)
        if chunk is not None:
            return chunk
        chunk = await self._base.get_chunk_by_id(chunk_id)
        if chunk is not None and self._is_shadowed(chunk.file_path):
            return None
        return chunk

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        overlay_chunks, base_chunks = await asyncio.gather(
            self._overlay.get_chunks_by_ids(chunk_ids),
            self._base.get_chunks_by_ids(chunk_ids),
        )
        return [*overlay_chunks, *self._visible_base_chunks(base_chunks)]

    def count_chunks(self) -> int:
        overlay_count = _count_chunks(self._overlay)
        changed_paths = self._diff.added | self._diff.modified
        if changed_paths and overlay_count == 0:
            return 0
        return _count_chunks(self._base) + overlay_count

    def needs_overlay_index(self) -> bool:
        return (
            bool(self._diff.added | self._diff.modified)
            and _count_chunks(self._overlay) == 0
        )

    async def delete_by_file(self, file_path: str) -> None:
        await self._overlay.delete_by_file(file_path)

    async def delete_by_files(self, file_paths: list[str]) -> None:
        delete_many = getattr(self._overlay, "delete_by_files", None)
        if delete_many is not None:
            await delete_many(file_paths)
            return
        for file_path in file_paths:
            await self._overlay.delete_by_file(file_path)

    async def get_related_file_paths(self, file_paths: list[str]) -> list[str]:
        base_related = getattr(self._base, "get_related_file_paths", None)
        overlay_related = getattr(self._overlay, "get_related_file_paths", None)
        active_base_files = [
            file_path
            for file_path in file_paths
            if file_path not in self._diff.added | self._diff.modified
        ]
        base_paths, overlay_paths = await asyncio.gather(
            base_related(active_base_files)
            if base_related is not None and active_base_files
            else _empty_paths(),
            overlay_related(file_paths) if overlay_related is not None else _empty_paths(),
        )
        return _dedupe_paths([*overlay_paths, *base_paths], deleted=self._diff.deleted)

    def get_cached_compression(self, chunk_id: str, level: str) -> str | None:
        overlay_cache = getattr(self._overlay, "get_cached_compression", None)
        if overlay_cache is not None:
            cached = overlay_cache(chunk_id, level)
            if cached is not None:
                return cached
        base_cache = getattr(self._base, "get_cached_compression", None)
        return base_cache(chunk_id, level) if base_cache is not None else None

    def put_cached_compression(self, chunk_id: str, level: str, content: str) -> None:
        overlay_cache = getattr(self._overlay, "put_cached_compression", None)
        if overlay_cache is not None:
            overlay_cache(chunk_id, level, content)

    def _merge_chunks(
        self,
        base_chunks: list[Chunk],
        overlay_chunks: list[Chunk],
        top_k: int,
    ) -> list[Chunk]:
        ranked: dict[tuple[str, int, int], Chunk] = {}
        for chunk in self._visible_base_chunks(base_chunks):
            ranked[_source_key(chunk)] = chunk
        for chunk in overlay_chunks:
            ranked[_source_key(chunk)] = chunk
        return sorted(ranked.values(), key=_chunk_rank_key)[:top_k]

    def _visible_base_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        return [chunk for chunk in chunks if not self._is_shadowed(chunk.file_path)]

    def _is_shadowed(self, file_path: str) -> bool:
        return (
            file_path in self._diff.added
            or file_path in self._diff.modified
            or file_path in self._diff.deleted
        )

    def _is_deleted(self, file_path: str) -> bool:
        return file_path in self._diff.deleted


async def _empty_paths() -> list[str]:
    return []


def _dedupe_paths(paths: list[str], *, deleted: set[str]) -> list[str]:
    deduped = []
    seen = set()
    for path in paths:
        if path in deleted or path in seen:
            continue
        deduped.append(path)
        seen.add(path)
    return deduped


def _dedupe_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    deduped = []
    seen = set()
    for node in nodes:
        key = (node.id, node.file_path, node.name)
        if key in seen:
            continue
        deduped.append(node)
        seen.add(key)
    return deduped


def _merge_ranked_hits(
    base_hits: list[tuple[int, tuple[str, float]]],
    overlay_hits: list[tuple[int, tuple[str, float]]],
    top_k: int,
) -> list[tuple[str, float]]:
    hits_by_id: dict[str, tuple[str, float]] = {}
    rank_scores: dict[str, float] = {}
    for hits in (base_hits, overlay_hits):
        for rank, hit in hits:
            chunk_id, _ = hit
            hits_by_id[chunk_id] = hit
            rank_scores[chunk_id] = rank_scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
    return sorted(
        hits_by_id.values(),
        key=lambda hit: (-rank_scores[hit[0]], hit[0]),
    )[:top_k]


def _chunk_rank_key(chunk: Chunk) -> tuple[float, float, str]:
    distance = chunk.metadata.get("_distance")
    if isinstance(distance, int | float):
        return (0.0, float(distance), chunk.id)
    return (1.0, -chunk.confidence_score, chunk.id)


def _source_key(chunk: Chunk) -> tuple[str, int, int]:
    return (chunk.file_path, chunk.start_line, chunk.end_line)


def _node_file_path(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def _count_chunks(backend: StorageBackend) -> int:
    count_chunks = getattr(backend, "count_chunks", None)
    if count_chunks is not None:
        return count_chunks()
    return backend._vector_store.count()
