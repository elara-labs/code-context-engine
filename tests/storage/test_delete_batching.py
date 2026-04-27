"""Regression: delete_by_files must not trip SQLite's bound-parameter limit.

Pre-batching, vector/fts/graph stores built one IN(...) clause with
len(file_paths) placeholders. SQLite's SQLITE_MAX_VARIABLE_NUMBER (999 on
older builds, 32766 on modern) means a project-wide prune touching >999
files raised `OperationalError: too many SQL variables`.

Each test below ingests a few rows then asks the store to delete by a list
of >1500 file_paths to force multiple batches through batched_params, and
asserts the affected rows are gone — exercises the batching helper end-to-
end so a future regression in any single store would surface here.
"""
from __future__ import annotations

import asyncio

import pytest  # noqa: F401  (xdist scheduler hook)

from context_engine.models import (
    Chunk,
    ChunkType,
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
)
from context_engine.storage.fts_store import FTSStore
from context_engine.storage.graph_store import GraphStore
from context_engine.storage.vector_store import VectorStore


def _bulk_paths(n: int = 1500) -> list[str]:
    return [f"path/file_{i}.py" for i in range(n)]


def test_vector_store_delete_handles_oversize_path_list(tmp_path):
    vs = VectorStore(db_path=str(tmp_path / "vec"))
    chunk = Chunk(
        id="c1", content="x", chunk_type=ChunkType.FUNCTION,
        file_path="real.py", start_line=1, end_line=1, language="python",
        embedding=[0.1] * 4,
    )
    asyncio.run(vs.ingest([chunk]))
    # Mix in the real file so we know the delete actually executes.
    paths = _bulk_paths() + ["real.py"]
    asyncio.run(vs.delete_by_files(paths))
    assert vs.count() == 0


def test_fts_store_delete_handles_oversize_path_list(tmp_path):
    fts = FTSStore(db_path=str(tmp_path / "fts"))
    chunk = Chunk(
        id="c1", content="hello world", chunk_type=ChunkType.FUNCTION,
        file_path="real.py", start_line=1, end_line=1, language="python",
    )
    asyncio.run(fts.ingest([chunk]))
    paths = _bulk_paths() + ["real.py"]
    asyncio.run(fts.delete_by_files(paths))
    results = asyncio.run(fts.search("hello"))
    assert results == []


def test_graph_store_delete_handles_oversize_path_list(tmp_path):
    gs = GraphStore(db_path=str(tmp_path / "graph"))
    node = GraphNode(
        id="n1", node_type=NodeType.FUNCTION, name="f", file_path="real.py"
    )
    edge = GraphEdge(source_id="n1", target_id="n1", edge_type=EdgeType.DEFINES)
    asyncio.run(gs.ingest([node], [edge]))

    paths = _bulk_paths() + ["real.py"]
    asyncio.run(gs.delete_by_files(paths))
    nodes = asyncio.run(gs.get_nodes_by_file("real.py"))
    assert nodes == []
