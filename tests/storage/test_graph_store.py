import pytest
from context_engine.models import GraphNode, GraphEdge, NodeType, EdgeType
from context_engine.storage.graph_store import GraphStore

@pytest.fixture
def graph(tmp_path):
    return GraphStore(db_path=str(tmp_path / "graph"))

@pytest.fixture
def sample_data():
    nodes = [
        GraphNode(id="file_math", node_type=NodeType.FILE, name="math.py", file_path="math.py"),
        GraphNode(id="func_add", node_type=NodeType.FUNCTION, name="add", file_path="math.py"),
        GraphNode(id="func_mul", node_type=NodeType.FUNCTION, name="multiply", file_path="math.py"),
        GraphNode(id="file_util", node_type=NodeType.FILE, name="util.py", file_path="util.py"),
    ]
    edges = [
        GraphEdge(source_id="file_math", target_id="func_add", edge_type=EdgeType.DEFINES),
        GraphEdge(source_id="file_math", target_id="func_mul", edge_type=EdgeType.DEFINES),
        GraphEdge(source_id="func_add", target_id="func_mul", edge_type=EdgeType.CALLS),
        GraphEdge(source_id="file_util", target_id="file_math", edge_type=EdgeType.IMPORTS),
    ]
    return nodes, edges

@pytest.mark.asyncio
async def test_ingest_and_get_neighbors(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    neighbors = await graph.get_neighbors("file_math")
    names = [n.name for n in neighbors]
    assert "add" in names
    assert "multiply" in names

@pytest.mark.asyncio
async def test_get_neighbors_with_edge_filter(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    neighbors = await graph.get_neighbors("func_add", edge_type=EdgeType.CALLS)
    assert len(neighbors) == 1
    assert neighbors[0].name == "multiply"

@pytest.mark.asyncio
async def test_get_nodes_by_file(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    file_nodes = await graph.get_nodes_by_file("math.py")
    assert len(file_nodes) == 3

@pytest.mark.asyncio
async def test_get_nodes_by_type(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    functions = await graph.get_nodes_by_type(NodeType.FUNCTION)
    assert len(functions) == 2

@pytest.mark.asyncio
async def test_delete_by_file(graph, sample_data):
    nodes, edges = sample_data
    await graph.ingest(nodes, edges)
    await graph.delete_by_file("math.py")
    remaining = await graph.get_nodes_by_file("math.py")
    assert len(remaining) == 0

@pytest.mark.asyncio
async def test_ingest_empty(graph):
    await graph.ingest([], [])
    neighbors = await graph.get_neighbors("nonexistent")
    assert neighbors == []

@pytest.mark.asyncio
async def test_ingest_rolls_back_on_midbatch_failure(graph):
    """A mid-batch ingest failure must roll back already-executed inserts so
    the next unrelated commit doesn't flush partial graph state."""
    good = GraphNode(id="n_good", node_type=NodeType.FUNCTION, name="f", file_path="a.py")
    bad = GraphNode(id="n_bad", node_type=NodeType.FUNCTION, name="g", file_path="a.py",
                    properties={"x": object()})  # json.dumps fails
    with pytest.raises(TypeError):
        await graph.ingest([good, bad], [])
    # Trigger an unrelated commit on the same connection.
    await graph.ingest([], [])
    assert await graph.get_nodes_by_file("a.py") == []
