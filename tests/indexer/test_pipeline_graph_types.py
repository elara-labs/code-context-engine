"""Graph node-type mapping for indexed chunks.

Regression for the 2026-04-27 review: the pipeline used to map every
non-function chunk to NodeType.CLASS, so markdown / yaml / json / module
fallback chunks all polluted the graph as fake classes and degraded
related_context expansion.
"""
from __future__ import annotations

import pytest

from context_engine.config import load_config
from context_engine.indexer.pipeline import run_indexing
from context_engine.models import NodeType
from context_engine.storage.graph_store import GraphStore


@pytest.mark.asyncio
async def test_markdown_fallback_chunk_is_module_not_class(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "README.md").write_text(
        "# Title\n\nThis is some prose, not code.\n"
    )

    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    config = load_config()
    config.storage_path = str(storage_base)

    result = await run_indexing(config, str(project_dir), full=True)
    assert "README.md" in result.indexed_files

    from context_engine.utils import project_storage_dir
    graph = GraphStore(db_path=str(project_storage_dir(config, project_dir) / "graph"))
    classes = await graph.get_nodes_by_type(NodeType.CLASS)
    # No real classes in a markdown file — anything here is a misclassified
    # fallback chunk from the regression.
    md_classes = [n for n in classes if n.file_path == "README.md"]
    assert md_classes == [], (
        f"markdown chunks should not be NodeType.CLASS, got: {md_classes}"
    )

    # The chunk should land as MODULE (the chunker's fallback type).
    modules = await graph.get_nodes_by_type(NodeType.MODULE)
    md_modules = [n for n in modules if n.file_path == "README.md"]
    assert len(md_modules) >= 1, "markdown chunk missing as MODULE node"
