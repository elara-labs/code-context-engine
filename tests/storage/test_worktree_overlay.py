"""Tests for semantic base+worktree overlay backend."""

import pytest

from context_engine.git.diff import GitWorktreeDiff
from context_engine.models import Chunk, ChunkType, EdgeType, GraphNode, NodeType
from context_engine.storage.worktree_overlay import WorktreeOverlayBackend


def chunk(chunk_id: str, file_path: str, score: float = 0.5) -> Chunk:
    return Chunk(
        id=chunk_id,
        content=chunk_id,
        chunk_type=ChunkType.FUNCTION,
        file_path=file_path,
        start_line=1,
        end_line=2,
        language="python",
        confidence_score=score,
    )


def node(node_id: str, file_path: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        node_type=NodeType.FUNCTION,
        name=node_id,
        file_path=file_path,
    )


class StubBackend:
    def __init__(
        self,
        chunks: list[Chunk] | None = None,
        *,
        neighbors: list[GraphNode] | None = None,
        related_paths: list[str] | None = None,
    ) -> None:
        self.chunks = chunks or []
        self.neighbors = neighbors or []
        self.related_paths = related_paths or ["changed.py", "stable.py"]
        self.deleted: list[str] = []
        self.cache: dict[tuple[str, str], str] = {}

    async def ingest(self, chunks, nodes, edges):
        self.chunks.extend(chunks)

    async def vector_search(self, query_embedding, top_k=10, filters=None):
        return self.chunks[:top_k]

    async def fts_search(self, query, top_k=30):
        return [(item.id, -rank) for rank, item in enumerate(self.chunks[:top_k])]

    async def graph_neighbors(self, node_id, edge_type=None):
        return self.neighbors

    async def get_chunk_by_id(self, chunk_id):
        return next((item for item in self.chunks if item.id == chunk_id), None)

    async def get_chunks_by_ids(self, chunk_ids):
        wanted = set(chunk_ids)
        return [item for item in self.chunks if item.id in wanted]

    async def delete_by_file(self, file_path):
        self.deleted.append(file_path)

    async def get_related_file_paths(self, file_paths):
        return self.related_paths

    def count_chunks(self):
        return len(self.chunks)

    def get_cached_compression(self, chunk_id, level):
        return self.cache.get((chunk_id, level))

    def put_cached_compression(self, chunk_id, level, content):
        self.cache[(chunk_id, level)] = content


@pytest.mark.asyncio
async def test_vector_search_suppresses_base_chunks_from_modified_and_deleted_paths():
    base = StubBackend([
        chunk("base_changed", "changed.py", 0.99),
        chunk("base_deleted", "deleted.py", 0.98),
        chunk("base_stable", "stable.py", 0.2),
    ])
    overlay = StubBackend([chunk("overlay_changed", "changed.py", 0.5)])
    backend = WorktreeOverlayBackend(
        base=base,
        overlay=overlay,
        diff=GitWorktreeDiff(modified={"changed.py"}, deleted={"deleted.py"}),
    )

    results = await backend.vector_search([0.1], top_k=10)

    assert [item.id for item in results] == ["overlay_changed", "base_stable"]


@pytest.mark.asyncio
async def test_vector_search_preserves_relevance_across_overlay_and_base_results():
    base_chunk = chunk("base_strong", "stable.py", 0.99)
    base_chunk.metadata["_distance"] = 0.1
    overlay_chunk = chunk("overlay_weak", "new.py", 0.2)
    overlay_chunk.metadata["_distance"] = 1.2
    backend = WorktreeOverlayBackend(
        base=StubBackend([base_chunk]),
        overlay=StubBackend([overlay_chunk]),
        diff=GitWorktreeDiff(added={"new.py"}),
    )

    results = await backend.vector_search([0.1], top_k=10)

    assert [item.id for item in results] == ["base_strong", "overlay_weak"]


@pytest.mark.asyncio
async def test_vector_search_suppresses_base_chunks_from_recreated_paths():
    backend = WorktreeOverlayBackend(
        base=StubBackend([chunk("base_recreated", "recreated.py")]),
        overlay=StubBackend([chunk("overlay_recreated", "recreated.py")]),
        diff=GitWorktreeDiff(added={"recreated.py"}),
    )

    results = await backend.vector_search([0.1], top_k=10)

    assert [item.id for item in results] == ["overlay_recreated"]


@pytest.mark.asyncio
async def test_fts_search_filters_stale_base_ids_before_hydration():
    base = StubBackend([chunk("base_changed", "changed.py"), chunk("base_stable", "stable.py")])
    overlay = StubBackend([chunk("overlay_changed", "changed.py")])
    backend = WorktreeOverlayBackend(
        base=base,
        overlay=overlay,
        diff=GitWorktreeDiff(modified={"changed.py"}),
    )

    results = await backend.fts_search("changed", top_k=10)

    assert [chunk_id for chunk_id, _ in results] == ["overlay_changed", "base_stable"]


@pytest.mark.asyncio
async def test_fts_search_preserves_ranked_base_hits_ahead_of_weak_overlay_hits():
    base = StubBackend([
        chunk("base_strong", "stable.py"),
        chunk("base_second", "stable2.py"),
    ])
    overlay = StubBackend([chunk("overlay_weak", "new.py")])
    backend = WorktreeOverlayBackend(
        base=base,
        overlay=overlay,
        diff=GitWorktreeDiff(added={"new.py"}),
    )

    results = await backend.fts_search("query", top_k=10)

    assert [chunk_id for chunk_id, _ in results][:2] == ["base_strong", "overlay_weak"]


@pytest.mark.asyncio
async def test_get_chunk_by_id_never_returns_shadowed_base_chunk():
    backend = WorktreeOverlayBackend(
        base=StubBackend([chunk("base_changed", "changed.py")]),
        overlay=StubBackend([]),
        diff=GitWorktreeDiff(modified={"changed.py"}),
    )

    assert await backend.get_chunk_by_id("base_changed") is None


@pytest.mark.asyncio
async def test_update_diff_refreshes_shadowed_paths():
    backend = WorktreeOverlayBackend(
        base=StubBackend([chunk("base_changed", "changed.py")]),
        overlay=StubBackend([]),
        diff=GitWorktreeDiff(),
    )

    assert await backend.get_chunk_by_id("base_changed") is not None

    backend.update_diff(GitWorktreeDiff(modified={"changed.py"}))

    assert await backend.get_chunk_by_id("base_changed") is None


def test_count_chunks_requires_overlay_when_worktree_has_changed_paths():
    backend = WorktreeOverlayBackend(
        base=StubBackend([chunk("base_changed", "changed.py")]),
        overlay=StubBackend([]),
        diff=GitWorktreeDiff(modified={"changed.py"}),
    )

    assert backend.count_chunks() == 0


@pytest.mark.asyncio
async def test_ingest_and_delete_target_overlay_only():
    base = StubBackend([])
    overlay = StubBackend([])
    backend = WorktreeOverlayBackend(base=base, overlay=overlay, diff=GitWorktreeDiff())

    await backend.ingest([chunk("overlay", "new.py")], [], [])
    await backend.delete_by_file("new.py")

    assert [item.id for item in overlay.chunks] == ["overlay"]
    assert overlay.deleted == ["new.py"]
    assert base.chunks == []


@pytest.mark.asyncio
async def test_related_paths_suppress_deleted_base_paths():
    backend = WorktreeOverlayBackend(
        base=StubBackend([]),
        overlay=StubBackend([]),
        diff=GitWorktreeDiff(deleted={"changed.py"}),
    )

    assert await backend.get_related_file_paths(["seed.py"]) == ["stable.py"]


@pytest.mark.asyncio
async def test_graph_neighbors_include_overlay_only_relationships():
    backend = WorktreeOverlayBackend(
        base=StubBackend([chunk("base", "stable.py")], neighbors=[node("stable", "stable.py")]),
        overlay=StubBackend(
            [chunk("overlay", "new.py")],
            neighbors=[node("changed", "changed.py")],
            related_paths=["changed.py"],
        ),
        diff=GitWorktreeDiff(modified={"changed.py"}, added={"new.py"}),
    )

    neighbors = await backend.graph_neighbors("new.py:caller", EdgeType.CALLS)
    related_paths = await backend.get_related_file_paths(["new.py"])

    assert [item.id for item in neighbors] == ["changed"]
    assert related_paths == ["changed.py"]


@pytest.mark.asyncio
async def test_graph_expansion_drops_base_outgoing_edges_for_modified_sources():
    backend = WorktreeOverlayBackend(
        base=StubBackend(related_paths=["old_auth.py"]),
        overlay=StubBackend(related_paths=["new_auth.py"]),
        diff=GitWorktreeDiff(modified={"auth.py"}),
    )

    related_paths = await backend.get_related_file_paths(["auth.py"])

    assert related_paths == ["new_auth.py"]


def test_compression_cache_reads_overlay_then_base_and_writes_overlay():
    base = StubBackend([])
    overlay = StubBackend([])
    base.cache[("chunk", "standard")] = "base cached"
    backend = WorktreeOverlayBackend(base=base, overlay=overlay, diff=GitWorktreeDiff())

    assert backend.get_cached_compression("chunk", "standard") == "base cached"

    backend.put_cached_compression("chunk", "standard", "overlay cached")

    assert backend.get_cached_compression("chunk", "standard") == "overlay cached"
    assert overlay.cache[("chunk", "standard")] == "overlay cached"
