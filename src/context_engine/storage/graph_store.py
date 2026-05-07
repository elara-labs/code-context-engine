"""Graph store — SQLite-backed implementation."""

import asyncio
import json
import sqlite3
from threading import RLock

from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType

_DDL = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    node_type   TEXT NOT NULL,
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    properties  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    properties  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source_id, target_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file   ON nodes  (file_path);
"""


def _row_to_node(row: tuple) -> GraphNode:
    node_id, node_type, name, file_path, properties = row
    return GraphNode(
        id=node_id,
        node_type=NodeType(node_type),
        name=name,
        file_path=file_path,
        properties=json.loads(properties),
    )


class GraphStore:
    """Single-connection SQLite graph store, serialised with an RLock.

    `check_same_thread=False` only disables thread ownership checks; concurrent
    operations on one connection are still unsafe. Mirrors VectorStore's
    locking pattern.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path + ".db"
        self._lock = RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Sync internals (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    def _sync_ingest(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for node in nodes:
                cur.execute(
                    "INSERT OR REPLACE INTO nodes (id, node_type, name, file_path, properties) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (node.id, node.node_type.value, node.name, node.file_path,
                     json.dumps(node.properties)),
                )
            for edge in edges:
                cur.execute(
                    "INSERT OR REPLACE INTO edges (source_id, target_id, edge_type, properties) "
                    "VALUES (?, ?, ?, ?)",
                    (edge.source_id, edge.target_id, edge.edge_type.value,
                     json.dumps(edge.properties)),
                )
            self._conn.commit()

    def _sync_get_neighbors(self, node_id: str, edge_type: EdgeType | None) -> list[GraphNode]:
        with self._lock:
            cur = self._conn.cursor()
            if edge_type is None:
                cur.execute(
                    "SELECT n.id, n.node_type, n.name, n.file_path, n.properties "
                    "FROM edges e JOIN nodes n ON e.target_id = n.id "
                    "WHERE e.source_id = ?",
                    (node_id,),
                )
            else:
                cur.execute(
                    "SELECT n.id, n.node_type, n.name, n.file_path, n.properties "
                    "FROM edges e JOIN nodes n ON e.target_id = n.id "
                    "WHERE e.source_id = ? AND e.edge_type = ?",
                    (node_id, edge_type.value),
                )
            return [_row_to_node(row) for row in cur.fetchall()]

    def _sync_get_nodes_by_file(self, file_path: str) -> list[GraphNode]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT id, node_type, name, file_path, properties FROM nodes WHERE file_path = ?",
                (file_path,),
            )
            return [_row_to_node(row) for row in cur.fetchall()]

    def _sync_neighbors_for_files(
        self,
        file_paths: list[str],
        edge_types: list[EdgeType],
        node_types: list[NodeType] | None = None,
    ) -> list[GraphNode]:
        """Single query: target-nodes of edges originating from any node belonging
        to any of `file_paths`, filtered by edge_type (and optionally source-node
        type). Replaces N+1 calls to get_nodes_by_file + get_neighbors per result.
        """
        if not file_paths or not edge_types:
            return []
        with self._lock:
            cur = self._conn.cursor()
            file_placeholders = ",".join("?" * len(file_paths))
            edge_placeholders = ",".join("?" * len(edge_types))
            params: list = list(file_paths) + [et.value for et in edge_types]
            node_filter = ""
            if node_types:
                node_placeholders = ",".join("?" * len(node_types))
                node_filter = f" AND src.node_type IN ({node_placeholders})"
                params.extend(nt.value for nt in node_types)
            cur.execute(
                f"SELECT DISTINCT tgt.id, tgt.node_type, tgt.name, tgt.file_path, tgt.properties "
                f"FROM nodes src "
                f"JOIN edges e ON e.source_id = src.id "
                f"JOIN nodes tgt ON tgt.id = e.target_id "
                f"WHERE src.file_path IN ({file_placeholders}) "
                f"  AND e.edge_type IN ({edge_placeholders})"
                f"{node_filter}",
                params,
            )
            return [_row_to_node(row) for row in cur.fetchall()]

    def _sync_get_nodes_by_type(self, node_type: NodeType) -> list[GraphNode]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT id, node_type, name, file_path, properties FROM nodes WHERE node_type = ?",
                (node_type.value,),
            )
            return [_row_to_node(row) for row in cur.fetchall()]

    def _sync_delete_by_file(self, file_path: str) -> None:
        self._sync_delete_by_files([file_path])

    def _sync_delete_by_files(self, file_paths: list[str]) -> None:
        if not file_paths:
            return
        from context_engine.utils import batched_params

        with self._lock:
            cur = self._conn.cursor()
            # Collect node IDs in batches to respect SQLite param limits.
            # Safe: ph is only "?" chars; values are parameterized.
            node_ids: list[str] = []
            for batch in batched_params(file_paths):
                ph = ",".join("?" * len(batch))
                cur.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"SELECT id FROM nodes WHERE file_path IN ({ph})", batch
                )
                node_ids.extend(row[0] for row in cur.fetchall())
            # Delete edges and nodes in batches.
            for batch in batched_params(node_ids):
                ph = ",".join("?" * len(batch))
                cur.execute(  # nosemgrep: sqlalchemy-execute-raw-query
                    f"DELETE FROM edges WHERE source_id IN ({ph}) "
                    f"OR target_id IN ({ph})",
                    batch + batch,
                )
                cur.execute(f"DELETE FROM nodes WHERE id IN ({ph})", batch)  # nosemgrep: sqlalchemy-execute-raw-query
            self._conn.commit()

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def ingest(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        await asyncio.to_thread(self._sync_ingest, nodes, edges)

    async def get_neighbors(self, node_id: str, edge_type: EdgeType | None = None) -> list[GraphNode]:
        return await asyncio.to_thread(self._sync_get_neighbors, node_id, edge_type)

    async def get_nodes_by_file(self, file_path: str) -> list[GraphNode]:
        return await asyncio.to_thread(self._sync_get_nodes_by_file, file_path)

    async def neighbors_for_files(
        self,
        file_paths: list[str],
        edge_types: list[EdgeType],
        node_types: list[NodeType] | None = None,
    ) -> list[GraphNode]:
        return await asyncio.to_thread(
            self._sync_neighbors_for_files, file_paths, edge_types, node_types
        )

    async def get_nodes_by_type(self, node_type: NodeType) -> list[GraphNode]:
        return await asyncio.to_thread(self._sync_get_nodes_by_type, node_type)

    async def delete_by_file(self, file_path: str) -> None:
        await asyncio.to_thread(self._sync_delete_by_file, file_path)

    async def delete_by_files(self, file_paths: list[str]) -> None:
        await asyncio.to_thread(self._sync_delete_by_files, file_paths)

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM nodes")
            self._conn.commit()
