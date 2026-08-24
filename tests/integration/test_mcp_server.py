import pytest
from unittest.mock import AsyncMock, MagicMock
from context_engine.cli import _run_index, _runtime_storage_backend, _runtime_storage_base
from context_engine.integration.mcp_server import ContextEngineMCP
from context_engine.models import Chunk, ChunkType
from context_engine.retrieval.retriever import HybridRetriever
from context_engine.structural import (
    ProviderStatus,
    Relationship,
    SourceRange,
    StructuralContext,
    SymbolKey,
)
from tests.test_git_repository_context import init_repo


def test_mcp_server_has_required_tools():
    server = ContextEngineMCP.__new__(ContextEngineMCP)
    tool_names = server.get_tool_names()
    assert "context_search" in tool_names
    assert "expand_chunk" in tool_names
    assert "related_context" in tool_names
    assert "session_recall" in tool_names
    assert "index_status" in tool_names
    assert "reindex" in tool_names


def _make_server(tmp_path):
    """Build a minimal ContextEngineMCP with a tmp storage dir."""
    config = MagicMock()
    config.storage_path = str(tmp_path)
    config.output_compression = "standard"
    server = ContextEngineMCP.__new__(ContextEngineMCP)
    server._config = config
    server._output_level = "standard"
    server._stats_path = tmp_path / "stats.json"
    server._state_path = tmp_path / "state.json"
    server._default_top_k = 10
    server._default_max_tokens = 8000
    server._stats = server._load_stats()
    # _record_bucket already guards on this; tests don't need a real db.
    server._memory_conn = None
    server._storage_base = tmp_path
    # Memory nudge state (see ContextEngineMCP.__init__)
    server._searches_since_last_decision = 0
    server._has_recorded_decision = False
    return server


def _embedded_chunk(chunk_id, file_path, content):
    return Chunk(
        id=chunk_id,
        content=content,
        chunk_type=ChunkType.FUNCTION,
        file_path=file_path,
        start_line=1,
        end_line=2,
        language="python",
        embedding=[0.1, 0.2, 0.3, 0.4],
    )


class _StubEmbedder:
    def embed_query(self, query):
        return [0.1, 0.2, 0.3, 0.4]


def _symbol(name, path, line=1):
    return SymbolKey(qualified_name=name, kind="function", path=path, line=line)


class _StubStructuralProvider:
    def __init__(self, context, *, available=True):
        self._context = context
        self._available = available

    async def status(self, project_root):
        return ProviderStatus("codegraph", self._available, metadata={"index": {"state": "complete"}})

    async def explore(self, query, project_root):
        return self._context

    async def impact(self, symbol, project_root):
        return StructuralContext(impact=list(self._context.impact), provider="codegraph")


@pytest.mark.asyncio
async def test_context_search_uses_worktree_overlay_version(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    (repo / "base.py").write_text("def base():\n    return 'worktree value'\n")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    cfg.output_compression = "off"
    cfg.compression_level = "off"
    cfg.retrieval_confidence_threshold = 0.0
    cfg.retrieval_marginal_ratio = 0.0
    monkeypatch.chdir(repo)

    _, backend, _ = _runtime_storage_backend(cfg, repo)
    await backend._base.ingest(
        [_embedded_chunk("base-old", "base.py", "def base():\n    return 'base value'\n")],
        [],
        [],
    )
    await backend.ingest(
        [_embedded_chunk("overlay-new", "base.py", "def base():\n    return 'worktree value'\n")],
        [],
        [],
    )

    server = _make_server(tmp_path)
    server._config = cfg
    server._backend = backend
    server._retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    server._compressor = MagicMock()
    server._compressor.compress = AsyncMock(side_effect=lambda chunks, _level: chunks)
    server._session_capture = MagicMock()
    server._session_capture.touch_files = MagicMock()
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [],
        "code_areas": [],
        "touched_files": {},
    })
    server._persist_current_session = MagicMock()
    server._record = MagicMock()
    server._append_audit_log = MagicMock()
    server._session_id = "test-session"

    result = await server._handle_context_search({"query": "base value", "top_k": 5})
    text = result[0].text

    assert "worktree value" in text
    assert "base value" not in text


@pytest.mark.asyncio
async def test_context_search_returns_codegraph_structural_context(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    cfg.output_compression = "off"
    cfg.compression_level = "off"
    cfg.retrieval_confidence_threshold = 0.0
    cfg.retrieval_marginal_ratio = 0.0
    cfg.structural_provider = "codegraph"
    monkeypatch.chdir(repo)

    _, backend, _ = _runtime_storage_backend(cfg, repo)
    await backend._base.ingest(
        [_embedded_chunk("semantic", "base.py", "def base():\n    return 'semantic'\n")],
        [],
        [],
    )
    server = _make_server(tmp_path)
    server._config = cfg
    server._project_dir = str(repo)
    server._backend = backend
    server._retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    server._compressor = MagicMock()
    server._compressor.compress = AsyncMock(side_effect=lambda chunks, _level: chunks)
    server._session_capture = MagicMock()
    server._session_capture.touch_files = MagicMock()
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [],
        "code_areas": [],
        "touched_files": {},
    })
    server._persist_current_session = MagicMock()
    server._record = MagicMock()
    server._append_audit_log = MagicMock()
    server._session_id = "test-session"
    auth = _symbol("auth.login", "auth.py", line=12)
    token = _symbol("token.validate", "token.py", line=3)
    server._structural_provider = _StubStructuralProvider(StructuralContext(
        sources=[SourceRange("auth.py", 12, 18, "def login(): pass")],
        relationships=[Relationship(auth, token, "calls")],
        impact=[token],
        provider="codegraph",
    ))

    result = await server._handle_context_search({"query": "login", "top_k": 5})
    text = result[0].text

    assert "Relevant source chunks:" in text
    assert "def base()" in text
    assert "Structural sources:" in text
    assert "auth.py:12-18" in text
    assert "auth.login (function at auth.py:12) calls token.validate (function at token.py:3)" in text
    assert "Structural impact:" in text


@pytest.mark.asyncio
async def test_context_search_shadows_modified_codegraph_base_source(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    (repo / "base.py").write_text("def base():\n    return 'worktree auth'\n")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    cfg.output_compression = "off"
    cfg.compression_level = "off"
    cfg.retrieval_confidence_threshold = 0.0
    cfg.retrieval_marginal_ratio = 0.0
    cfg.structural_provider = "codegraph"
    monkeypatch.chdir(repo)

    _, backend, _ = _runtime_storage_backend(cfg, repo)
    await backend.ingest(
        [_embedded_chunk("overlay", "base.py", "def base():\n    return 'worktree auth'\n")],
        [],
        [],
    )
    server = _make_server(tmp_path)
    server._config = cfg
    server._project_dir = str(repo)
    server._backend = backend
    server._retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    server._compressor = MagicMock()
    server._compressor.compress = AsyncMock(side_effect=lambda chunks, _level: chunks)
    server._session_capture = MagicMock()
    server._session_capture.touch_files = MagicMock()
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [],
        "code_areas": [],
        "touched_files": {},
    })
    server._persist_current_session = MagicMock()
    server._record = MagicMock()
    server._append_audit_log = MagicMock()
    server._session_id = "test-session"
    server._structural_provider = _StubStructuralProvider(StructuralContext(
        sources=[SourceRange("base.py", 1, 2, "def base():\n    return 'base auth'")],
        provider="codegraph",
    ))

    result = await server._handle_context_search({"query": "auth", "top_k": 5})
    text = result[0].text

    assert "worktree auth" in text
    assert "base auth" not in text


@pytest.mark.asyncio
async def test_context_search_degrades_when_codegraph_unavailable(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    cfg.output_compression = "off"
    cfg.compression_level = "off"
    cfg.retrieval_confidence_threshold = 0.0
    cfg.retrieval_marginal_ratio = 0.0
    cfg.structural_provider = "codegraph"
    monkeypatch.chdir(repo)

    _, backend, _ = _runtime_storage_backend(cfg, repo)
    await backend._base.ingest(
        [_embedded_chunk("semantic", "base.py", "def base():\n    return 'semantic'\n")],
        [],
        [],
    )
    server = _make_server(tmp_path)
    server._config = cfg
    server._project_dir = str(repo)
    server._backend = backend
    server._retriever = HybridRetriever(backend=backend, embedder=_StubEmbedder())
    server._compressor = MagicMock()
    server._compressor.compress = AsyncMock(side_effect=lambda chunks, _level: chunks)
    server._session_capture = MagicMock()
    server._session_capture.touch_files = MagicMock()
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [],
        "code_areas": [],
        "touched_files": {},
    })
    server._persist_current_session = MagicMock()
    server._record = MagicMock()
    server._append_audit_log = MagicMock()
    server._session_id = "test-session"
    server._structural_provider = _StubStructuralProvider(
        StructuralContext(),
        available=False,
    )

    result = await server._handle_context_search({"query": "semantic", "top_k": 5})
    text = result[0].text

    assert "def base()" in text
    assert "Structural sources:" not in text


@pytest.mark.asyncio
async def test_runtime_backend_refreshes_diff_after_worktree_change(tmp_path):
    repo = init_repo(tmp_path / "repo")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    _, backend, refresh_diff = _runtime_storage_backend(cfg, repo)
    await backend._base.ingest(
        [_embedded_chunk("base-old", "base.py", "def base():\n    return 'base value'\n")],
        [],
        [],
    )

    assert await backend.get_chunk_by_id("base-old") is not None

    (repo / "base.py").write_text("def base():\n    return 'worktree value'\n")
    assert refresh_diff is not None
    refresh_diff()

    assert await backend.get_chunk_by_id("base-old") is None


@pytest.mark.asyncio
async def test_run_index_uses_runtime_storage_base_for_git_repositories(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    cfg = MagicMock()
    cfg.storage_path = str(tmp_path / "storage")
    expected_storage = _runtime_storage_base(cfg, repo)
    indexing_kwargs: list[dict] = []

    async def fake_run_indexing(*args, **kwargs):
        from context_engine.indexer.pipeline import IndexResult

        indexing_kwargs.append(kwargs)
        return IndexResult()

    monkeypatch.setattr("context_engine.indexer.pipeline.run_indexing", fake_run_indexing)

    await _run_index(cfg, str(repo))

    assert indexing_kwargs[0]["storage_base_override"] == expected_storage


def test_apply_output_compression_appends_directive(tmp_path):
    """When level != off, the helper appends the directive and bumps the bucket."""
    server = _make_server(tmp_path)
    server._output_level = "max"
    out = server._apply_output_compression("body content")
    assert "body content" in out
    assert "[Respond using max output compression]" in out
    # Bucket gained one event.
    bucket = server._stats["buckets"]["output_compression"]
    assert bucket["calls"] == 1
    assert bucket["baseline"] > bucket["served"] > 0


def test_apply_output_compression_noop_when_off(tmp_path):
    """level=off returns the body untouched and records nothing."""
    server = _make_server(tmp_path)
    server._output_level = "off"
    out = server._apply_output_compression("body content")
    assert out == "body content"
    assert server._stats["buckets"]["output_compression"]["calls"] == 0


def test_recall_display_cap_default():
    """Without the env var, cap defaults to 7."""
    import os
    from context_engine.integration.mcp_server import _recall_display_cap
    os.environ.pop("CCE_RECALL_DISPLAY_CAP", None)
    assert _recall_display_cap() == 7


def test_recall_display_cap_env_override(monkeypatch):
    """CCE_RECALL_DISPLAY_CAP raises (or lowers) the cap for power users."""
    from context_engine.integration.mcp_server import _recall_display_cap
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "20")
    assert _recall_display_cap() == 20
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "3")
    assert _recall_display_cap() == 3


def test_recall_display_cap_invalid_falls_back(monkeypatch):
    """Garbage values are ignored — never break recall on a typo."""
    from context_engine.integration.mcp_server import _recall_display_cap
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "not-a-number")
    assert _recall_display_cap() == 7
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "0")
    assert _recall_display_cap() == 7
    monkeypatch.setenv("CCE_RECALL_DISPLAY_CAP", "-5")
    assert _recall_display_cap() == 7


def test_audit_log_no_op_when_disabled(tmp_path):
    """Audit log writes only when config.audit_log_enabled is True."""
    server = _make_server(tmp_path)
    server._session_id = "test-session"
    server._config.audit_log_enabled = False
    server._append_audit_log(
        query="hello world", top_k=10,
        served_chunks=[], score_range=None,
    )
    assert not (tmp_path / "audit.log").exists()


def test_audit_log_writes_one_jsonline_per_call(tmp_path):
    """When enabled, each call appends one well-formed JSON line."""
    import json as _json
    server = _make_server(tmp_path)
    server._session_id = "test-session"
    server._config.audit_log_enabled = True
    server._append_audit_log(
        query="how does auth work?", top_k=7,
        served_chunks=[
            {"file": "src/auth.py", "lines": "10-40", "score": 0.812, "kind": "inline"},
        ],
        score_range=(0.812, 0.812),
    )
    server._append_audit_log(
        query="another query", top_k=5,
        served_chunks=[], score_range=None,
    )
    audit = (tmp_path / "audit.log").read_text().strip().splitlines()
    assert len(audit) == 2
    entry = _json.loads(audit[0])
    assert entry["session_id"] == "test-session"
    assert entry["top_k"] == 7
    assert entry["query_len"] == len("how does auth work?")
    # Query text is hashed (12-char prefix), never stored raw.
    assert "auth" not in entry["query_hash"]
    assert len(entry["query_hash"]) == 12
    assert entry["served"][0]["file"] == "src/auth.py"
    assert entry["score_range"] == [0.812, 0.812]


@pytest.mark.asyncio
async def test_index_status_no_queries(tmp_path):
    server = _make_server(tmp_path)
    result = await server._handle_index_status()
    text = result[0].text
    assert "waiting for first context_search call" in text


@pytest.mark.asyncio
async def test_index_status_with_tracked_stats(tmp_path):
    server = _make_server(tmp_path)
    server._stats = {"queries": 5, "raw_tokens": 1000, "served_tokens": 400}
    result = await server._handle_index_status()
    text = result[0].text
    assert "5 queries" in text
    assert "1,000" in text   # raw
    assert "400" in text     # served
    assert "600" in text     # saved
    assert "60%" in text


def _make_search_server(tmp_path, dropped_low_value):
    """Server wired for _handle_context_search with a stub retriever whose
    stats_out reports the given dropped_low_value."""
    from unittest.mock import AsyncMock
    from context_engine.models import Chunk, ChunkType

    server = _make_server(tmp_path)

    stub_chunk = Chunk(
        id="c1", content="def foo(): pass",
        chunk_type=ChunkType.FUNCTION, file_path="src/foo.py",
        start_line=1, end_line=1, language="python",
    )
    stub_chunk.confidence_score = 0.8

    async def fake_retrieve(query, top_k=10, confidence_threshold=0.0,
                            marginal_ratio=0.0, max_tokens=None,
                            stats_out=None):
        if stats_out is not None:
            stats_out["candidates"] = 1 + dropped_low_value
            stats_out["selected"] = 1
            stats_out["dropped_low_value"] = dropped_low_value
        return [stub_chunk]

    server._retriever = MagicMock()
    server._retriever.retrieve = fake_retrieve
    server._compressor = MagicMock()
    server._compressor.compress = AsyncMock(return_value=[stub_chunk])
    server._session_capture = MagicMock()
    server._session_capture.touch_files = MagicMock()
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [], "code_areas": [], "touched_files": {},
    })
    server._persist_current_session = MagicMock()
    server._record = MagicMock()
    server._append_audit_log = MagicMock()
    server._ensure_indexed = AsyncMock(return_value=True)

    server._config.retrieval_top_k = 5
    server._config.retrieval_confidence_threshold = 0.99
    server._config.retrieval_marginal_ratio = 0.5
    server._config.output_compression = "off"
    server._output_level = "off"
    server._project_name = "test-project"
    server._session_id = "test-session"
    return server


@pytest.mark.asyncio
async def test_context_search_appends_omitted_note_when_drops_reported(tmp_path):
    """Note appears only when the retriever reports threshold/marginal drops."""
    server = _make_search_server(tmp_path, dropped_low_value=2)
    result = await server._handle_context_search({"query": "find something", "top_k": 5})
    text = result[0].text
    assert "lower-confidence results omitted" in text


@pytest.mark.asyncio
async def test_context_search_no_note_when_nothing_dropped(tmp_path):
    """Fewer chunks than retrieval_top_k with zero drops must NOT trigger the
    note — that was the false-positive the count heuristic produced."""
    server = _make_search_server(tmp_path, dropped_low_value=0)
    result = await server._handle_context_search({"query": "find something", "top_k": 5})
    text = result[0].text
    assert "lower-confidence results omitted" not in text


@pytest.mark.asyncio
async def test_decision_nudge_fires_after_threshold(tmp_path):
    """After 4 context_search calls with no record_decision, the nudge appears."""
    server = _make_search_server(tmp_path, dropped_low_value=0)
    # First 3 searches: no nudge
    for _ in range(3):
        result = await server._handle_context_search({"query": "q", "top_k": 5})
        assert "[CCE]" not in result[0].text
    # 4th search: nudge fires
    result = await server._handle_context_search({"query": "q", "top_k": 5})
    assert "[CCE] 4 context searches" in result[0].text
    assert "record_decision" in result[0].text


@pytest.mark.asyncio
async def test_decision_nudge_resets_after_record(tmp_path):
    """Recording a decision resets the search counter."""
    server = _make_search_server(tmp_path, dropped_low_value=0)
    # Trigger nudge
    for _ in range(4):
        await server._handle_context_search({"query": "q", "top_k": 5})
    # Simulate record_decision resetting state
    server._searches_since_last_decision = 0
    server._has_recorded_decision = True
    # Next search: no nudge (counter reset, higher threshold now)
    result = await server._handle_context_search({"query": "q", "top_k": 5})
    assert "[CCE]" not in result[0].text


@pytest.mark.asyncio
async def test_code_area_nudge_fires_with_unrecorded_files(tmp_path):
    """Nudge appears when 3+ files are touched but none recorded as code areas."""
    server = _make_search_server(tmp_path, dropped_low_value=0)
    # Simulate 3 touched files with no code_areas recorded
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [], "code_areas": [],
        "touched_files": {"a.py": 1, "b.py": 2, "c.py": 1},
    })
    result = await server._handle_context_search({"query": "q", "top_k": 5})
    assert "files explored but not recorded" in result[0].text
    assert "record_code_area" in result[0].text


@pytest.mark.asyncio
async def test_code_area_nudge_excludes_recorded_files(tmp_path):
    """Files that were record_code_area'd don't count toward the threshold."""
    server = _make_search_server(tmp_path, dropped_low_value=0)
    server._session_capture.get_session_snapshot = MagicMock(return_value={
        "decisions": [], "touched_files": {"a.py": 1, "b.py": 2, "c.py": 1},
        "code_areas": [
            {"file_path": "a.py", "description": "x", "timestamp": 0},
            {"file_path": "b.py", "description": "y", "timestamp": 0},
        ],
    })
    result = await server._handle_context_search({"query": "q", "top_k": 5})
    # Only 1 unrecorded file (c.py) — below threshold
    assert "[CCE]" not in result[0].text or "files explored" not in result[0].text
