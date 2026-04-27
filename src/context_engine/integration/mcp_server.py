"""MCP server exposing context engine tools to Claude Code."""
import json
import logging
from pathlib import Path

from context_engine.utils import atomic_write_text as _atomic_write_text

from mcp.server import Server
from mcp.types import Tool, TextContent

from context_engine.compression.output_rules import (
    get_output_rules,
    get_level_description,
    LEVELS,
)
from context_engine.integration.bootstrap import BootstrapBuilder
from context_engine.integration.git_context import (
    get_recent_commits,
    get_recently_modified_files,
    get_working_state,
)
from context_engine.integration.session_capture import SessionCapture

log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_MAX_QUERY_CHARS = 10_000
_MAX_TOP_K = 100
# Search up to this many recent session files when recalling decisions.
# Older files past this window are silently dropped — see roadmap item
# "persistent session search across projects" for how this should evolve.
_SESSION_RECALL_WINDOW = 50
# Minimum cosine-derived similarity (1 - distance) for a session entry to
# qualify as a topic match. Tuned conservatively — substring grep would
# return 0 results for paraphrases, vector recall now returns paraphrase
# matches, but we want to avoid drowning the caller in unrelated decisions.
_SESSION_RECALL_MIN_SIM = 0.35


def _count_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _cosine_sim(a, b) -> float:
    """Cosine similarity between two equal-length numeric sequences. Returns 0
    on degenerate input (zero norm) instead of NaN.

    Length mismatch returns 0 and logs at debug — the embedder always returns
    fixed-dimension vectors, so a mismatch means something is wrong upstream
    (model swap mid-process, corrupted cached vector). We prefer "no match"
    over a silently truncated similarity that zip()'d to the shorter length.
    """
    if len(a) != len(b):
        log.debug("_cosine_sim length mismatch: %d vs %d", len(a), len(b))
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na**0.5 * nb**0.5)


def _clamp_top_k(value, default: int = 10) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, _MAX_TOP_K))


def _split_inline_overflow(
    chunks: list, max_tokens: int
) -> tuple[list, list]:
    """Split chunks into inline (fits budget) and overflow (references only)."""
    inline: list = []
    overflow: list = []
    budget = max_tokens
    for chunk in chunks:
        served_text = chunk.compressed_content or chunk.content
        chunk_tokens = _count_tokens(served_text)
        if chunk_tokens <= budget:
            inline.append(chunk)
            budget -= chunk_tokens
        else:
            overflow.append(chunk)
    return inline, overflow


def _format_results_with_overflow(inline_chunks: list, overflow_chunks: list) -> str:
    """Format inline results and append compact overflow references."""
    parts = []
    for chunk in inline_chunks:
        served_text = chunk.compressed_content or chunk.content
        parts.append(
            f"[{chunk.file_path}:{chunk.start_line}] "
            f"(confidence: {chunk.confidence_score:.2f})\n{served_text}"
        )

    if overflow_chunks:
        lines = [
            f"\n---\n{len(overflow_chunks)} more result(s) available "
            f"(not shown to save tokens):"
        ]
        for chunk in overflow_chunks:
            lines.append(
                f'  expand_chunk(chunk_id="{chunk.id}")  '
                f"→ {chunk.file_path}:{chunk.start_line} "
                f"(confidence: {chunk.confidence_score:.2f})"
            )
        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts) if parts else "No results found."


class ContextEngineMCP:
    TOOL_NAMES = [
        "context_search",
        "expand_chunk",
        "related_context",
        "session_recall",
        "record_decision",
        "record_code_area",
        "index_status",
        "reindex",
        "set_output_compression",
    ]

    def __init__(self, retriever, backend, compressor, embedder, config) -> None:
        self._retriever = retriever
        self._backend = backend
        self._compressor = compressor
        self._embedder = embedder
        self._config = config
        self._server = Server("code-context-engine")

        project_name = Path.cwd().name
        self._project_name = project_name
        self._project_dir = str(Path.cwd())
        self._storage_base = Path(config.storage_path) / project_name
        self._storage_base.mkdir(parents=True, exist_ok=True)
        self._stats_path = self._storage_base / "stats.json"
        self._state_path = self._storage_base / "state.json"
        self._stats = self._load_stats()

        # `state.json` overrides the config default so `set_output_compression`
        # survives server restarts.
        persisted_state = self._load_state()
        self._output_level = persisted_state.get(
            "output_level", config.output_compression
        )

        # Session capture — persists decisions and code-area notes across runs.
        self._session_capture = SessionCapture(
            sessions_dir=str(self._storage_base / "sessions")
        )
        self._session_id = self._session_capture.start_session(project_name)
        # Cheap maintenance on start: if the project has accumulated more than
        # _PRUNE_THRESHOLD session files, consolidate the oldest decisions
        # into decisions_log.json and remove the source files. No-op when
        # under threshold (the common case).
        try:
            summary = self._session_capture.prune_old_sessions()
            if summary.get("pruned"):
                log.info(
                    "Pruned %d old session files (%d decisions archived)",
                    summary["pruned"],
                    summary.get("decisions_appended", 0),
                )
        except Exception as exc:
            log.debug("Session prune skipped: %s", exc)

        # Bootstrap builder — used by the `context-engine-init` prompt handler.
        self._bootstrap = BootstrapBuilder(max_tokens=config.bootstrap_max_tokens)

        # Lazy indexing flag — triggers on first context_search if index is empty.
        self._lazy_indexed = False

        self._register_tools()
        self._register_prompts()

    # ── state / stats persistence ───────────────────────────────────────────

    def _load_stats(self) -> dict:
        if self._stats_path.exists():
            try:
                data = json.loads(self._stats_path.read_text())
                # Backfill new keys for stats files written by older versions.
                data.setdefault("queries", 0)
                data.setdefault("raw_tokens", 0)
                data.setdefault("served_tokens", 0)
                data.setdefault("full_file_tokens", 0)
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "queries": 0,
            "raw_tokens": 0,
            "served_tokens": 0,
            "full_file_tokens": 0,
        }

    def _save_stats(self) -> None:
        try:
            _atomic_write_text(self._stats_path, json.dumps(self._stats))
        except Exception as exc:
            self._append_error_log(f"_save_stats failed: {exc}")

    def _append_query_log(self) -> None:
        import datetime
        try:
            # Verify the write actually landed
            on_disk = self._stats_path.read_text() if self._stats_path.exists() else "missing"
            log_path = self._storage_base / "query.log"
            q = self._stats["queries"]
            entry = (
                f"{datetime.datetime.now().isoformat()} query #{q} "
                f"stats_written={self._stats_path} "
                f"disk_queries={on_disk} "
                f"cwd={self._project_dir}\n"
            )
            with log_path.open("a") as f:
                f.write(entry)
        except OSError:
            pass

    def _append_error_log(self, msg: str) -> None:
        import datetime
        try:
            log_path = self._storage_base / "query.log"
            entry = f"{datetime.datetime.now().isoformat()} ERROR {msg}\n"
            with log_path.open("a") as f:
                f.write(entry)
        except OSError:
            pass

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self) -> None:
        try:
            state = {"output_level": self._output_level}
            _atomic_write_text(self._state_path, json.dumps(state))
        except OSError:
            pass

    def _record(self, raw_tokens: int, served_tokens: int, full_file_tokens: int = 0) -> None:
        self._stats["queries"] += 1
        self._stats["raw_tokens"] += raw_tokens
        self._stats["served_tokens"] += served_tokens
        self._stats.setdefault("full_file_tokens", 0)
        self._stats["full_file_tokens"] += full_file_tokens
        self._save_stats()
        self._append_query_log()

    def get_tool_names(self) -> list[str]:
        return list(self.TOOL_NAMES)

    # ── tool registration ───────────────────────────────────────────────────

    def _register_tools(self) -> None:
        @self._server.list_tools()
        async def list_tools():
            return [
                Tool(
                    name="context_search",
                    description="Search project context — code, docs, session history",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "top_k": {"type": "integer", "default": 10},
                            "max_tokens": {"type": "integer", "default": 8000},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="expand_chunk",
                    description="Get the full original content for a compressed chunk",
                    inputSchema={
                        "type": "object",
                        "properties": {"chunk_id": {"type": "string"}},
                        "required": ["chunk_id"],
                    },
                ),
                Tool(
                    name="related_context",
                    description="Find related code via graph edges",
                    inputSchema={
                        "type": "object",
                        "properties": {"chunk_id": {"type": "string"}},
                        "required": ["chunk_id"],
                    },
                ),
                Tool(
                    name="session_recall",
                    description="Recall past decisions and code-area notes recorded in this or prior sessions",
                    inputSchema={
                        "type": "object",
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                    },
                ),
                Tool(
                    name="record_decision",
                    description="Record a decision (with reason) for future session_recall",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "decision": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["decision", "reason"],
                    },
                ),
                Tool(
                    name="record_code_area",
                    description="Record a code area (file + description) worked on, for future session_recall",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["file_path", "description"],
                    },
                ),
                Tool(
                    name="index_status",
                    description="Check when the index was last updated",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="reindex",
                    description="Trigger re-indexing of a file or the entire project",
                    inputSchema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                ),
                Tool(
                    name="set_output_compression",
                    description=(
                        "Set output compression level to reduce response token cost. "
                        "Levels: off, lite, standard, max"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "level": {
                                "type": "string",
                                "enum": list(LEVELS),
                                "description": (
                                    "off=normal, lite=no filler, standard=fragments "
                                    "~65% savings, max=telegraphic ~75% savings"
                                ),
                            },
                        },
                        "required": ["level"],
                    },
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict):
            arguments = arguments or {}
            try:
                if name == "context_search":
                    return await self._handle_context_search(arguments)
                elif name == "expand_chunk":
                    return await self._handle_expand_chunk(arguments)
                elif name == "related_context":
                    return await self._handle_related_context(arguments)
                elif name == "session_recall":
                    return await self._handle_session_recall(arguments)
                elif name == "record_decision":
                    return self._handle_record_decision(arguments)
                elif name == "record_code_area":
                    return self._handle_record_code_area(arguments)
                elif name == "index_status":
                    return await self._handle_index_status()
                elif name == "reindex":
                    return await self._handle_reindex(arguments)
                elif name == "set_output_compression":
                    return self._handle_set_output_compression(arguments)
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("MCP tool %s failed", name)
                return [TextContent(type="text", text=f"Tool {name} failed: {exc}")]

    # ── tool handlers ───────────────────────────────────────────────────────

    async def _ensure_indexed(self) -> None:
        """Lazy indexing: if the index is empty, trigger indexing on first query."""
        if self._lazy_indexed:
            return
        self._lazy_indexed = True
        try:
            count = self._backend._vector_store.count()
            if count > 0:
                return
        except Exception:
            pass
        # Index is empty — trigger on-the-fly indexing
        log.info("Index empty — triggering lazy indexing for %s", self._project_name)
        try:
            from context_engine.indexer.pipeline import run_indexing
            await run_indexing(self._config, self._project_dir, full=False)
        except Exception as exc:
            log.warning("Lazy indexing failed: %s", exc)

    async def _handle_context_search(self, args):
        query = (args.get("query") or "").strip()
        if not query:
            return [TextContent(type="text", text="Query cannot be empty.")]
        if len(query) > _MAX_QUERY_CHARS:
            return [
                TextContent(
                    type="text",
                    text=f"Query too long (max {_MAX_QUERY_CHARS} characters).",
                )
            ]

        # Lazy index if this is the first query and index is empty
        await self._ensure_indexed()

        top_k = _clamp_top_k(args.get("top_k", 10))
        max_tokens = args.get("max_tokens", 8000)
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 8000

        # Fetch 2x candidates so overflow can offer references
        all_chunks = await self._retriever.retrieve(
            query,
            top_k=top_k * 2,
            confidence_threshold=self._config.retrieval_confidence_threshold,
            max_tokens=None,
        )
        all_chunks = await self._compressor.compress(all_chunks, self._config.compression_level)

        inline_chunks, overflow_chunks = _split_inline_overflow(all_chunks, max_tokens)

        # Accounting
        raw_tokens = 0
        served_tokens = 0
        seen_files: set[str] = set()
        for chunk in inline_chunks:
            served_text = chunk.compressed_content or chunk.content
            raw_tokens += _count_tokens(chunk.content)
            served_tokens += _count_tokens(served_text)
            seen_files.add(chunk.file_path)
        for chunk in overflow_chunks:
            raw_tokens += _count_tokens(chunk.content)
            served_tokens += 30  # compact reference ~30 tokens
            seen_files.add(chunk.file_path)

        full_file_tokens = self._estimate_full_file_tokens(seen_files)

        # Auto-capture: every file that surfaced as a relevant result counts as
        # "touched" — we can't tell from here whether Claude will act on it,
        # but a file appearing in a search result is a stronger signal than
        # silence. Persisted into the session log alongside explicit
        # record_code_area calls.
        self._session_capture.touch_files(self._session_id, seen_files)
        self._persist_current_session()

        body = _format_results_with_overflow(inline_chunks, overflow_chunks)
        if get_output_rules(self._output_level):
            body += (
                f"\n\n---\n[Respond using {self._output_level} output compression]"
            )
        self._record(raw_tokens, served_tokens, full_file_tokens)
        return [TextContent(type="text", text=body)]

    def _estimate_full_file_tokens(self, file_paths: set[str]) -> int:
        """Estimate token count if the user had read the full source files.

        Uses file size (~4 bytes per token, the typical English/code ratio
        produced by `_count_tokens` heuristic) rather than reading every file
        into memory — that ran on every search and could load hundreds of MB.
        """
        from pathlib import Path as _Path
        total = 0
        project_dir = _Path.cwd()
        for fp in file_paths:
            full_path = project_dir / fp
            try:
                size = full_path.stat().st_size
            except OSError:
                continue
            total += max(1, size // _CHARS_PER_TOKEN)
        return total

    async def _handle_expand_chunk(self, args):
        chunk_id = (args.get("chunk_id") or "").strip()
        if not chunk_id:
            return [TextContent(type="text", text="chunk_id is required.")]
        chunk = await self._backend.get_chunk_by_id(chunk_id)
        if chunk is None:
            return [TextContent(type="text", text="Chunk not found.")]
        tokens = _count_tokens(chunk.content)
        self._record(tokens, tokens)
        # Opening a chunk is a much stronger "I care about this file" signal
        # than just seeing it in a result list — bump the touch counter.
        self._session_capture.touch_files(self._session_id, [chunk.file_path])
        self._persist_current_session()
        return [
            TextContent(
                type="text",
                text=(
                    f"[{chunk.file_path}:{chunk.start_line}-{chunk.end_line}]\n"
                    f"{chunk.content}"
                ),
            )
        ]

    async def _handle_related_context(self, args):
        chunk_id = (args.get("chunk_id") or "").strip()
        if not chunk_id:
            return [TextContent(type="text", text="chunk_id is required.")]
        neighbors = await self._backend.graph_neighbors(chunk_id)
        if not neighbors:
            return [
                TextContent(
                    type="text",
                    text="No related context found for this chunk.",
                )
            ]
        lines = [
            f"- {n.node_type.value}: {n.name} ({n.file_path})" for n in neighbors
        ]
        return [TextContent(type="text", text="\n".join(lines))]

    async def _handle_session_recall(self, args):
        topic = (args.get("topic") or "").strip()
        if not topic:
            return [TextContent(type="text", text="topic is required.")]
        matches = self._search_sessions(topic)
        if not matches:
            return [
                TextContent(
                    type="text",
                    text=(
                        f"No recorded decisions or code-area notes matching '{topic}'. "
                        "Use record_decision or record_code_area to capture notes "
                        "during the session."
                    ),
                )
            ]
        body = "\n".join(f"- {m}" for m in matches[:20])
        return [TextContent(type="text", text=body)]

    def _handle_record_decision(self, args):
        decision = (args.get("decision") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not decision:
            return [TextContent(type="text", text="decision is required.")]
        self._session_capture.record_decision(self._session_id, decision, reason)
        self._persist_current_session()
        return [
            TextContent(
                type="text",
                text=f"✓ Decision recorded: {decision}",
            )
        ]

    def _handle_record_code_area(self, args):
        file_path = (args.get("file_path") or "").strip()
        description = (args.get("description") or "").strip()
        if not file_path:
            return [TextContent(type="text", text="file_path is required.")]
        self._session_capture.record_code_area(
            self._session_id, file_path, description
        )
        self._persist_current_session()
        return [
            TextContent(
                type="text",
                text=f"✓ Code area noted: {file_path} — {description}",
            )
        ]

    async def _handle_index_status(self):
        queries = self._stats["queries"]
        raw = self._stats["raw_tokens"]
        served = self._stats["served_tokens"]
        saved = raw - served
        pct = int(saved / raw * 100) if raw > 0 else 0

        status_parts = [
            "Index status: operational",
            f"Output compression: {self._output_level} — "
            f"{get_level_description(self._output_level)}",
        ]
        if queries > 0:
            status_parts.append(
                f"Token savings ({queries} queries): {raw:,} raw → {served:,} served "
                f"({saved:,} saved, {pct}%)"
            )
        else:
            status_parts.append("Token savings: no queries recorded yet")
        return [TextContent(type="text", text="\n".join(status_parts))]

    async def _handle_reindex(self, args):
        """Run the real indexing pipeline, either project-wide or on a path."""
        from context_engine.indexer.pipeline import run_indexing

        path = (args.get("path") or "").strip() or None
        try:
            result = await run_indexing(
                self._config,
                self._project_dir,
                full=False,
                target_path=path,
            )
        except Exception as exc:
            log.exception("reindex failed")
            return [TextContent(type="text", text=f"✗ Re-index failed: {exc}")]

        lines = [
            "✓ Re-index complete",
            f"  Indexed: {len(result.indexed_files)} file(s), {result.total_chunks} chunk(s)",
        ]
        if result.deleted_files:
            lines.append(f"  Pruned stale: {len(result.deleted_files)}")
        if result.skipped_files:
            lines.append(f"  Skipped (binary/unreadable): {len(result.skipped_files)}")
        if result.errors:
            lines.append(f"  Errors: {len(result.errors)}")
            lines.extend(f"    - {e}" for e in result.errors[:5])
        return [TextContent(type="text", text="\n".join(lines))]

    def _handle_set_output_compression(self, args):
        level = (args.get("level") or "standard").strip()
        if level not in LEVELS:
            return [
                TextContent(
                    type="text",
                    text=f"Invalid level: {level}. Use: {', '.join(LEVELS)}",
                )
            ]
        self._output_level = level
        self._save_state()  # persist so restarts keep the user's choice
        desc = get_level_description(level)
        rules = get_output_rules(level)
        if rules:
            return [
                TextContent(
                    type="text",
                    text=f"Output compression set to: {level}\n{desc}\n\n{rules}",
                )
            ]
        return [
            TextContent(
                type="text",
                text="Output compression disabled. Claude will respond normally.",
            )
        ]

    # ── session helpers ─────────────────────────────────────────────────────

    def _persist_current_session(self) -> None:
        """Flush the in-memory current session to disk after every record.

        `SessionCapture.end_session` normally flushes on shutdown, but the MCP
        process doesn't always get a clean shutdown signal, so we persist after
        each record to avoid data loss.
        """
        sessions_dir = Path(self._session_capture._sessions_dir)  # noqa: SLF001
        session = self._session_capture.get_session_snapshot(self._session_id)
        if not session:
            return
        try:
            file_path = sessions_dir / f"{self._session_id}.json"
            _atomic_write_text(file_path, json.dumps(session, indent=2))
        except OSError:
            log.warning("Failed to persist session %s", self._session_id)

    def _search_sessions(self, topic: str) -> list[str]:
        """Search decisions, code areas, and Q&A across recent sessions.

        Uses the same embedder as code search so paraphrases match — recording
        "Use JWT with RS256" and querying "auth" now surfaces the decision
        instead of returning empty as the prior substring grep did. Falls back
        to substring matching only if embedding fails (e.g. embedder not loaded).
        """
        topic = topic.strip()
        if not topic:
            return []

        # Collect candidate entries from current + recent sessions.
        current = self._session_capture.get_session_snapshot(self._session_id)
        sessions: list[dict] = []
        if current:
            sessions.append(current)
        sessions.extend(
            self._session_capture.load_recent_sessions(limit=_SESSION_RECALL_WINDOW)
        )

        candidates: list[str] = []
        seen: set[str] = set()
        for session in sessions:
            for decision in session.get("decisions", []):
                text = (
                    f"[decision] {decision.get('decision', '')} — "
                    f"{decision.get('reason', '')}"
                )
                if text not in seen:
                    seen.add(text)
                    candidates.append(text)
            for area in session.get("code_areas", []):
                text = (
                    f"[code_area] {area.get('file_path', '')} — "
                    f"{area.get('description', '')}"
                )
                if text not in seen:
                    seen.add(text)
                    candidates.append(text)
            for question in session.get("questions", []):
                text = (
                    f"[q&a] {question.get('question', '')} → "
                    f"{question.get('answer', '')}"
                )
                if text not in seen:
                    seen.add(text)
                    candidates.append(text)

        # Also include the consolidated decisions archive — `prune_old_sessions`
        # writes decisions into decisions_log.json before deleting the source
        # session files, so without this step a recall on a long-lived project
        # would silently forget anything past the most-recent
        # _SESSION_RECALL_WINDOW files. The CLI's `cce sessions prune`
        # docstring already promises this works.
        for decision in self._session_capture._load_consolidated_decisions():
            text = (
                f"[decision] {decision.get('decision', '')} — "
                f"{decision.get('reason', '')}"
            )
            if text not in seen:
                seen.add(text)
                candidates.append(text)

        if not candidates:
            return []

        # Vector recall: embed topic + each candidate, rank by cosine similarity.
        try:
            topic_vec = list(self._embedder.embed_query(topic))
            scored: list[tuple[float, str]] = []
            for text in candidates:
                vec = list(self._embedder.embed_query(text))
                sim = _cosine_sim(topic_vec, vec)
                if sim >= _SESSION_RECALL_MIN_SIM:
                    scored.append((sim, text))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return [text for _, text in scored]
        except Exception as exc:
            # If embedding fails for any reason, fall back to a tolerant
            # substring match so callers always get *something* useful.
            log.debug("Session vector recall failed (%s); falling back to substring", exc)
            needle = topic.lower()
            return [t for t in candidates if needle in t.lower()]

    # ── MCP prompts ─────────────────────────────────────────────────────────

    def _register_prompts(self):
        """Register MCP prompts for session-start context injection."""
        from mcp.types import Prompt, PromptMessage, PromptArgument

        @self._server.list_prompts()
        async def list_prompts():
            return [
                Prompt(
                    name="context-engine-init",
                    description=(
                        "Initialize context engine with project overview and "
                        "output compression rules"
                    ),
                    arguments=[
                        PromptArgument(
                            name="output_level",
                            description="Output compression level: off, lite, standard, max",
                            required=False,
                        ),
                    ],
                ),
            ]

        @self._server.get_prompt()
        async def get_prompt(name: str, arguments: dict | None = None):
            if name != "context-engine-init":
                return None
            level = (arguments or {}).get("output_level", self._output_level)

            # Compose a rich project bootstrap with git context, session
            # decisions, and chunks relevant to current work.
            try:
                # Start with architecture overview chunks
                chunks = await self._retriever.retrieve(
                    "architecture overview", top_k=10
                )
                # Also retrieve chunks for recently modified files so the
                # init prompt reflects current work, not just static structure.
                modified_files = get_recently_modified_files(self._project_dir)
                if modified_files:
                    file_query = " ".join(
                        f.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        for f in modified_files[:5]
                    )
                    try:
                        recent_chunks = await self._retriever.retrieve(
                            file_query, top_k=5
                        )
                        # Merge without duplicates
                        seen_ids = {c.id for c in chunks}
                        for c in recent_chunks:
                            if c.id not in seen_ids:
                                chunks.append(c)
                                seen_ids.add(c.id)
                    except Exception as exc:
                        log.debug("Recent-file chunk retrieval failed: %s", exc)
            except Exception as exc:
                log.warning("Init prompt chunk retrieval failed: %s", exc)
                chunks = []

            # Git history and working state
            recent_commits = get_recent_commits(self._project_dir)
            working_state = get_working_state(self._project_dir)

            # Surface the files that got the most attention in the most-recent
            # past session. Auto-captured every time a file appears in a
            # context_search result or is opened via expand_chunk — gives the
            # next session a "where you left off" hint without requiring
            # Claude to have explicitly called record_code_area.
            recent_sessions = self._session_capture.load_recent_sessions(limit=1)
            if recent_sessions:
                touched = recent_sessions[0].get("touched_files") or {}
                if touched:
                    top = sorted(touched.items(), key=lambda kv: kv[1], reverse=True)[:5]
                    working_state = list(working_state or [])
                    working_state.append(
                        "Recently touched files (prior session): "
                        + ", ".join(f"{fp} ({n})" for fp, n in top)
                    )

            # Active decisions from past sessions — surface the most recent
            # entries unconditionally rather than substring-matching on the
            # word "decision" (which usually misses since recorded decisions
            # rarely contain that literal token).
            active_decisions = self._session_capture.get_recent_decisions(limit=10)

            # Get total indexed chunk count for the status line.
            try:
                chunk_count = self._backend._vector_store.count()
            except Exception:
                chunk_count = 0

            # Load project-specific commands from .cce/commands.yaml
            from context_engine.project_commands import load_commands, format_for_prompt
            proj_commands = load_commands(self._project_dir)
            proj_commands_text = format_for_prompt(proj_commands)

            bootstrap_text = self._bootstrap.build(
                project_name=self._project_name,
                chunks=chunks,
                recent_commits=recent_commits,
                active_decisions=active_decisions,
                working_state=working_state,
                chunk_count=chunk_count,
                project_commands_text=proj_commands_text,
            )

            # Tool routing instructions — injected at session start so the
            # model uses context_search instead of Read for exploration.
            tool_instructions = (
                "\n\n---\n"
                "## Tool Routing (context-engine)\n\n"
                "This project has a semantic search index. "
                "**You MUST use the `context_search` MCP tool** for ANY of these:\n"
                "- Questions about the codebase (\"what does X do?\", \"how does Y work?\")\n"
                "- Exploring code, finding functions, understanding structure\n"
                "- Finding related code or patterns\n\n"
                "Use `Read` ONLY when you need to edit a specific file.\n\n"
                "Call `context_search` with a natural language query. "
                "Example: `context_search({\"query\": \"twitter feed layout\"})`\n"
                "Do NOT use Read, Glob, or Grep to answer questions about the code.\n"
            )

            rules = get_output_rules(level)
            content = bootstrap_text + tool_instructions
            if rules:
                content += f"\n\n{rules}"
            return {
                "messages": [
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=content),
                    ),
                ],
            }

    async def run_stdio(self):
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )
