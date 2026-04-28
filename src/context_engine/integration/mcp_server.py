"""MCP server exposing context engine tools to Claude Code."""
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path

from context_engine.utils import atomic_write_text as _atomic_write_text

from mcp.server import Server
from mcp.types import Tool, TextContent

from context_engine.compression.output_rules import (
    ADVERTISED_PCT,
    ESTIMATED_AVG_REPLY_TOKENS,
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
from context_engine.memory import db as memory_db
from context_engine.memory.extractive import extractive_summary
from context_engine.memory.grammar import (
    compress as _grammar_compress,
    compress_with_counts as _grammar_compress_counted,
    expand as _grammar_expand,
    DEFAULT_LEVEL as _GRAMMAR_LEVEL,
)

log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_MAX_QUERY_CHARS = 10_000
_MAX_TOP_K = 100
# Search up to this many recent session files when recalling decisions.
# Older files past this window are silently dropped — see roadmap item
# "persistent session search across projects" for how this should evolve.
_SESSION_RECALL_WINDOW = 50
# Minimum cosine similarity for a JSON-history entry to qualify as a topic
# match. bge-small's noise floor on short English is ~0.50 (a random off-
# topic query against trading decisions hits 0.535), so anything below ~0.55
# is statistical noise. 0.55 keeps real paraphrase matches (~0.59-0.65) and
# rejects "how is the weather today" against unrelated decisions.
_SESSION_RECALL_MIN_SIM = 0.55


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


def _clamp_int(value, *, default: int, lo: int, hi: int) -> int:
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


_FTS_RECALL_LIMIT = 100
_VEC_RECALL_K = 30
# Read-time cap on session_event payloads. Inputs already have a 4 KB write
# cap (compressor._TOOL_INPUT_CHAR_CAP) but outputs are stored uncapped, so a
# 50 KB Bash stdout would re-feed ~12 k tokens on every fetch without this.
_EVENT_PAYLOAD_READ_CAP = 4_000
# RRF (reciprocal rank fusion) constant. 60 is the canonical value from the
# Cormack/Clarke/Buettcher 2009 paper — small enough that early ranks
# dominate, large enough to keep the late tail relevant.
_RRF_K = 60
# How many of the top RRF-ranked candidates to feed the extractive summariser
# for the TL;DR header. More = broader summary, slower extract.
_TLDR_TOP_N = 10
# Strip the "[decision src=...|sid:...] " style prefix the formatter adds —
# the summariser should see the actual content, not our metadata tags.
_TAG_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")
# Strip the trailing " · 5m ago · → session_timeline(\"abc\")" affordance the
# formatter appends. Used for dedup so the same decision rendered with vs.
# without the affordance (e.g. JSON-history vs. memory.db dual-write) collapses.
_AFFORDANCE_TAIL_RE = re.compile(r"\s*·\s+(?:just now|\d+[mhdy]o? ago|\d+[mhd] ago)(?:\s*·\s+→\s+session_(?:timeline|event)\([^)]*\))?\s*$")


def _strip_tag(text: str) -> str:
    return _TAG_PREFIX_RE.sub("", text)


def _content_key(text: str) -> str:
    """Stable dedup key for a recall match.

    Strips:
      1. The `[tag]` prefix the formatter adds.
      2. The " · 5m ago · → session_timeline(...)" affordance suffix.
      3. Articles (via grammar.compress at lite level), so the SAME decision
         stored compressed in memory.db and stored raw in JSON history
         collapses to one canonical key. Without (3), `_handle_record_decision`
         dual-writes produce two recall hits in the dual-write window — one
         from memory.db ("Adopt JWT") and one from JSON ("Adopt the JWT") —
         that look distinct to RRF but are the same decision.
    """
    body = _TAG_PREFIX_RE.sub("", text)
    body = _AFFORDANCE_TAIL_RE.sub("", body)
    return _grammar_compress(body.strip(), level="lite")


def _humanise_relative_time(epoch: int | None) -> str:
    """Best-effort "3d ago" / "5m ago" string. Empty on bad/missing input.

    Only surfaces what's helpful to the model — sub-minute deltas would be
    noise on a recall hit, so we round to minute granularity at minimum.
    """
    if epoch is None:
        return ""
    import time as _time
    try:
        delta = max(0, int(_time.time()) - int(epoch))
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86_400:
        return f"{delta // 3600}h ago"
    if delta < 30 * 86_400:
        return f"{delta // 86_400}d ago"
    if delta < 365 * 86_400:
        return f"{delta // (30 * 86_400)}mo ago"
    return f"{delta // (365 * 86_400)}y ago"


def _truncate_payload(text: str | None, cap: int) -> str:
    """Trim a captured tool payload at read time. Empty NULL → placeholder."""
    if text is None:
        return "<no value>"
    if len(text) <= cap:
        return text
    suffix = f"\n…[truncated, {len(text) - cap} more chars]"
    return text[:cap] + suffix


def _rrf_merge(*ranked_lists: list[str], top: int) -> list[str]:
    """Reciprocal rank fusion of multiple ranked lists.

    Each input list is `[item_at_rank_0, item_at_rank_1, ...]`. Items in
    common across lists rise; items in only one list still surface
    proportional to their rank there. Returns up to `top` items.

    Dedup key strips both the [tag] prefix *and* the affordance tail
    (" · 5m ago · → session_timeline(...)"), so the same decision rendered
    through multiple paths (memory.db with hints + JSON history without)
    collapses into a single boosted entry instead of inflating recall.
    """
    scores: dict[str, float] = {}
    repr_for_key: dict[str, str] = {}
    for items in ranked_lists:
        for rank, item in enumerate(items):
            key = _content_key(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            # Prefer the richer-rendered form (with affordance hints) when
            # multiple paths produced the same decision — same key, different
            # text. The hint-bearing form is strictly more useful to the agent.
            existing = repr_for_key.get(key)
            if existing is None or (
                len(item) > len(existing) and " · " in item
            ):
                repr_for_key[key] = item
    ordered_keys = sorted(scores, key=lambda k: scores[k], reverse=True)[:top]
    return [repr_for_key[k] for k in ordered_keys]


# Conservative function-word list. We strip these from FTS5 queries so that
# `is OR the OR today OR we OR can` doesn't match every decision in the
# corpus. Restricted to genuine grammatical glue — articles, auxiliaries,
# pronouns, prepositions, conjunctions, common interrogatives. Topic words
# (code, auth, database, improve, scale, etc.) are NOT in this list.
#
# Vec search still runs in parallel against the original query, so even if
# every token is filtered out, semantic recall still surfaces matches.
_FTS_STOP_WORDS = frozenset({
    # articles / determiners
    "a", "an", "the", "this", "that", "these", "those", "some", "any",
    "no", "all", "both", "each", "every", "other", "another", "such",
    # auxiliaries / modals
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "ought",
    # pronouns
    "i", "we", "you", "he", "she", "it", "they", "me", "us", "him", "her",
    "them", "my", "our", "your", "his", "its", "their", "mine", "ours",
    "yours", "hers", "theirs", "myself", "ourselves", "yourself",
    "yourselves", "himself", "herself", "itself", "themselves",
    # prepositions / conjunctions
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "into", "onto", "upon", "about", "above", "below", "under", "over",
    "between", "among", "through", "during", "before", "after", "since",
    "until", "while", "and", "or", "but", "nor", "so", "if", "than",
    "then", "because", "though", "although", "unless",
    # interrogatives / proforms
    "how", "what", "when", "where", "which", "who", "whom", "whose",
    "why", "here", "there",
    # filler / generic time words
    "just", "only", "even", "also", "very", "too", "still", "now",
    "today", "tomorrow", "yesterday",
    # common verbs that carry no topic signal in this domain
    "get", "got", "make", "made", "go", "went", "see", "saw", "let",
})


def _strip_stop_words(topic: str) -> str:
    """Return `topic` with function words removed; falls back to the
    original if every token is a stop word (rare)."""
    tokens = [t.strip().lower() for t in topic.split() if t.strip()]
    content = [t for t in tokens if t not in _FTS_STOP_WORDS]
    return " ".join(content) if content else topic


def _fts_match_query(topic: str) -> str:
    """Build a safe FTS5 MATCH query from `topic` — OR of phrase-quoted
    *content* tokens (function words like "is/the/today/we/can" stripped).

    Returns "" when the topic has no usable content tokens left; callers
    skip the FTS query in that case rather than passing an empty MATCH
    (FTS5 would raise). When this happens, the vec semantic-search path
    still runs against the original query string, so meaning isn't lost.
    """
    content = _strip_stop_words(topic).split()
    if not content:
        return ""
    safe = ['"' + t.replace('"', '""') + '"' for t in content]
    return " OR ".join(safe)


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
        "session_timeline",
        "session_event",
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
        # Both the legacy JSON path and the new memory.db path are written to
        # for record_decision / record_code_area; recall queries both. Once a
        # release cycle of dual-write confirms parity, the JSON write side
        # can be retired.
        self._session_capture = SessionCapture(
            sessions_dir=str(self._storage_base / "sessions")
        )
        self._session_id = self._session_capture.start_session(project_name)
        try:
            self._memory_conn = memory_db.connect(
                memory_db.memory_db_path(self._storage_base)
            )
            # Ensure the sessions row exists so dual-writes don't trip the FK.
            # The SessionStart hook normally creates this, but the MCP server
            # may start in environments without hook coverage (e.g. tests).
            import time as _t
            _epoch = int(_t.time())
            self._memory_conn.execute(
                "INSERT OR IGNORE INTO sessions (id, project, started_at_epoch, "
                "started_at, status) VALUES (?, ?, ?, ?, 'active')",
                (self._session_id, project_name, _epoch,
                 _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(_epoch))),
            )
            self._memory_conn.commit()
            # Semantic backfill on a daemon thread — projects with thousands
            # of historical decisions/turns shouldn't pay a multi-second
            # embed-everything stall on every MCP startup. Each thread opens
            # its own connection (sqlite3 enforces check_same_thread).
            self._spawn_vec_backfill()
        except Exception as exc:
            log.warning("memory.db open failed; recall will fall back to JSON: %s", exc)
            self._memory_conn = None
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
        empty_buckets = {
            b: {"baseline": 0, "served": 0, "calls": 0}
            for b in memory_db.BUCKETS
        }
        if self._stats_path.exists():
            try:
                data = json.loads(self._stats_path.read_text())
                # Backfill new keys for stats files written by older versions.
                data.setdefault("queries", 0)
                data.setdefault("raw_tokens", 0)
                data.setdefault("served_tokens", 0)
                data.setdefault("full_file_tokens", 0)
                # v3: per-bucket breakdown. Merge so older files gain any
                # newly-added buckets without losing existing totals.
                buckets = data.get("buckets") or {}
                for name, default in empty_buckets.items():
                    b = buckets.get(name) or {}
                    buckets[name] = {
                        "baseline": int(b.get("baseline", 0)),
                        "served": int(b.get("served", 0)),
                        "calls": int(b.get("calls", 0)),
                    }
                data["buckets"] = buckets
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "queries": 0,
            "raw_tokens": 0,
            "served_tokens": 0,
            "full_file_tokens": 0,
            "buckets": empty_buckets,
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

    def _spawn_vec_backfill(self) -> None:
        """Run vec-table backfill on a daemon thread with its own DB connection.

        sqlite3 connections are bound to the thread that opened them, so we
        can't reuse `self._memory_conn` here. The thread opens its own
        connection and closes it when done. Daemon=True means the thread
        won't block process exit.
        """
        storage_base = self._storage_base
        embedder = self._embedder

        def _runner():
            try:
                conn = memory_db.connect(memory_db.memory_db_path(storage_base))
                try:
                    counts = memory_db.backfill_vec_tables(conn, embedder)
                    if counts.get("decisions") or counts.get("turn_summaries"):
                        log.info("memory.db vec backfill done: %s", counts)
                finally:
                    conn.close()
            except Exception:
                log.exception("memory.db vec backfill thread failed")

        threading.Thread(
            target=_runner, daemon=True, name="cce-vec-backfill"
        ).start()

    def _record(self, raw_tokens: int, served_tokens: int, full_file_tokens: int = 0) -> None:
        """Legacy retrieval-pipeline writer. Splits into two bucket events:
        retrieval (full_file → raw) and chunk_compression (raw → served),
        so per-bucket attribution matches what `cce savings` displays.
        """
        self._stats["queries"] += 1
        self._stats["raw_tokens"] += raw_tokens
        self._stats["served_tokens"] += served_tokens
        self._stats.setdefault("full_file_tokens", 0)
        self._stats["full_file_tokens"] += full_file_tokens
        if full_file_tokens > 0:
            self._record_bucket("retrieval", full_file_tokens, raw_tokens)
        if raw_tokens > 0:
            self._record_bucket("chunk_compression", raw_tokens, served_tokens)
        # Cover the no-bucket path (raw_tokens == 0) — _record_bucket would
        # have saved otherwise.
        if raw_tokens <= 0 and full_file_tokens <= 0:
            self._save_stats()
        self._append_query_log()

    def _record_bucket(
        self,
        bucket: str,
        baseline: int,
        served: int,
        meta: dict | None = None,
    ) -> None:
        """Append one savings event to memory.db and the in-memory totals.

        Best-effort — never raises so a misbehaving instrumentation point
        can't break a tool response. Callers don't need to call _save_stats
        unless they also want the legacy top-level fields refreshed.
        """
        baseline = max(0, int(baseline))
        served = max(0, int(served))
        b = self._stats.setdefault("buckets", {}).setdefault(
            bucket, {"baseline": 0, "served": 0, "calls": 0},
        )
        b["baseline"] += baseline
        b["served"] += served
        b["calls"] += 1
        if self._memory_conn is not None:
            try:
                memory_db.record_savings(
                    self._memory_conn,
                    bucket=bucket,
                    baseline=baseline,
                    served=served,
                    meta=meta,
                )
            except Exception as exc:  # pragma: no cover — defensive
                self._append_error_log(f"_record_bucket({bucket}) failed: {exc}")
        # Persist the in-memory rollup. Cheap (~few hundred bytes JSON write).
        self._save_stats()

    def get_tool_names(self) -> list[str]:
        return list(self.TOOL_NAMES)

    # ── tool registration ───────────────────────────────────────────────────

    def _register_tools(self) -> None:
        @self._server.list_tools()
        async def list_tools():
            return [
                Tool(
                    name="context_search",
                    description=(
                        "PREFERRED tool for ANY question about this project's "
                        "code, structure, or behavior. Use INSTEAD OF Read, "
                        "Grep, or Glob when exploring the codebase, locating "
                        "functions, or answering 'how does X work / where is "
                        "Y' questions. Returns the most relevant code chunks "
                        "with confidence scores from a hybrid vector + BM25 "
                        "index, so you do not pay tokens for files you do not "
                        "need. Read should be reserved for opening a known "
                        "file path you intend to edit."
                    ),
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
                    description=(
                        "Recall past decisions, prompts, and turn summaries via topic search. "
                        "Returns compact-index hits across the whole project history."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                    },
                ),
                Tool(
                    name="session_timeline",
                    description=(
                        "List the turn summaries for a session, oldest first. "
                        "Layer 2 of progressive disclosure — drill into a session_id "
                        "returned by session_recall."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "limit": {"type": "integer", "default": 20},
                        },
                        "required": ["session_id"],
                    },
                ),
                Tool(
                    name="session_event",
                    description=(
                        "Return the raw input/output payload for a single tool_event. "
                        "Layer 3 of progressive disclosure — drill into an event_id "
                        "from session_timeline."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {"event_id": {"type": "integer"}},
                        "required": ["event_id"],
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
                elif name == "session_timeline":
                    return self._handle_session_timeline(arguments)
                elif name == "session_event":
                    return self._handle_session_event(arguments)
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
            # output_compression bucket — estimate-only. We can't see Claude's
            # actual reply, so per affected response we attribute one
            # ESTIMATED_AVG_REPLY_TOKENS baseline reduced by the advertised
            # rate for the active level. Renderer marks the bucket with an
            # asterisk so users know it's an estimate.
            pct = ADVERTISED_PCT.get(self._output_level, 0.0)
            if pct > 0.0:
                self._record_bucket(
                    "output_compression",
                    baseline=ESTIMATED_AVG_REPLY_TOKENS,
                    served=int(ESTIMATED_AVG_REPLY_TOKENS * (1 - pct)),
                    meta={"level": self._output_level},
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
        body = self._format_recall(topic, matches)
        # memory_recall savings: baseline = all matched entries dumped raw,
        # served = TL;DR + top-20 bullets actually returned. Filtering and
        # summarisation are the two compression mechanics in this path.
        baseline = sum(_count_tokens(m) for m in matches)
        served = _count_tokens(body)
        if baseline > 0:
            self._record_bucket(
                "memory_recall", baseline=baseline, served=served,
                meta={"matches": len(matches), "topic_len": len(topic)},
            )
        return [TextContent(type="text", text=body)]

    def _format_recall(self, topic: str, matches: list[str]) -> str:
        """Render recall hits as a TL;DR header + provenance-tagged matches.

        The TL;DR is extractive — it picks real sentences from the top hits
        using the same bge-small-driven extractive summariser the compressor
        uses. No LLM call, no hallucination, ~50 ms wall-time on the asyncio
        thread for a 10-match input. Header is suppressed when there are too
        few matches to summarise meaningfully.
        """
        head_matches = matches[:20]
        tldr_lines: list[str] = []
        if len(head_matches) >= 3:
            # Embed each match's clean content (no [tag] prefix, no affordance
            # tail), pick the 3 most central by cosine-to-centroid, render
            # them as bullets so the TL;DR is scannable instead of a wall of
            # space-joined fragments.
            from context_engine.memory.extractive import _cosine
            try:
                cleaned = [_content_key(m) for m in head_matches[:_TLDR_TOP_N]]
                cleaned = [c for c in cleaned if c]
                if cleaned:
                    vecs = [list(self._embedder.embed_query(c)) for c in cleaned]
                    centroid = [
                        sum(col) / len(vecs) for col in zip(*vecs)
                    ]
                    scored = sorted(
                        zip(cleaned, vecs),
                        key=lambda pair: _cosine(pair[1], centroid),
                        reverse=True,
                    )
                    tldr_lines = [c for c, _ in scored[:3]]
            except Exception:
                log.debug("recall TL;DR extractive failed; omitting header")
                tldr_lines = []
        body_lines = [f"- {m}" for m in head_matches]
        if tldr_lines:
            n = len(matches)
            head = (
                f"TL;DR ({n} match{'es' if n != 1 else ''} for '{topic}'):\n"
                + "\n".join(f"  • {line}" for line in tldr_lines)
            )
            return head + "\n\nSource matches:\n" + "\n".join(body_lines)
        return "\n".join(body_lines)

    def _handle_record_decision(self, args):
        decision = (args.get("decision") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not decision:
            return [TextContent(type="text", text="decision is required.")]
        self._session_capture.record_decision(self._session_id, decision, reason)
        self._persist_current_session()
        # Dual-write into memory.db. `decision` and `reason` are compressed
        # via the grammar module before INSERT — structured tokens (paths,
        # versions, identifiers) are preserved byte-for-byte; only prose
        # words get articles/fillers dropped. The vec embedding is computed
        # on the *compressed* form so recall scores are consistent with
        # what's stored. session_recall expands on the read side via
        # `_format_*_in_id_order` so the agent sees natural prose.
        if self._memory_conn is not None:
            try:
                import time as _time
                epoch = int(_time.time())
                stored_decision, dec_raw, dec_comp = _grammar_compress_counted(
                    decision, level=_GRAMMAR_LEVEL,
                )
                stored_reason, rsn_raw, rsn_comp = _grammar_compress_counted(
                    reason, level=_GRAMMAR_LEVEL,
                )
                # One bucket event for the combined decision+reason write.
                self._record_bucket(
                    "grammar",
                    baseline=dec_raw + rsn_raw,
                    served=dec_comp + rsn_comp,
                )
                cur = self._memory_conn.execute(
                    "INSERT INTO decisions (session_id, decision, reason, source, "
                    "created_at_epoch, created_at) "
                    "VALUES (?, ?, ?, 'manual', ?, ?)",
                    (self._session_id, stored_decision, stored_reason, epoch,
                     _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(epoch))),
                )
                memory_db.record_decision_vec(
                    self._memory_conn, self._embedder,
                    decision_id=cur.lastrowid,
                    decision=stored_decision, reason=stored_reason,
                )
                self._memory_conn.commit()
            except Exception:
                log.exception("memory.db decision dual-write failed")
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
        if self._memory_conn is not None:
            try:
                import time as _time
                epoch = int(_time.time())
                self._memory_conn.execute(
                    "INSERT INTO code_areas (session_id, file_path, description, "
                    "source, created_at_epoch) VALUES (?, ?, ?, 'manual', ?)",
                    (self._session_id, file_path, description, epoch),
                )
                self._memory_conn.commit()
            except Exception:
                log.exception("memory.db code_area dual-write failed")
        return [
            TextContent(
                type="text",
                text=f"✓ Code area noted: {file_path} — {description}",
            )
        ]

    def _handle_session_timeline(self, args):
        session_id = (args.get("session_id") or "").strip()
        limit = _clamp_int(args.get("limit"), default=20, lo=1, hi=200)
        if not session_id:
            return [TextContent(type="text", text="session_id is required.")]
        if self._memory_conn is None:
            return [TextContent(type="text", text="Memory store not available.")]
        try:
            rows = list(self._memory_conn.execute(
                "SELECT prompt_number, summary, tier FROM turn_summaries "
                "WHERE session_id = ? ORDER BY prompt_number ASC LIMIT ?",
                (session_id, limit),
            ))
        except Exception as exc:
            return [TextContent(type="text", text=f"timeline query failed: {exc}")]
        if not rows:
            return [TextContent(
                type="text",
                text=f"No turn summaries for session {session_id} yet.",
            )]
        try:
            meta = self._memory_conn.execute(
                "SELECT project, started_at, ended_at, status, prompt_count, "
                "rollup_summary FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        except Exception as exc:
            return [TextContent(type="text", text=f"timeline query failed: {exc}")]
        header = []
        if meta:
            header.append(f"session: {session_id} · {meta['project']} · {meta['status']}")
            header.append(f"started: {meta['started_at']}  ended: {meta['ended_at'] or '—'}")
            if meta["rollup_summary"]:
                # Stored compressed; expand for the agent's view.
                header.append(f"rollup: {_grammar_expand(meta['rollup_summary'])}")
        body = "\n".join(
            f"  turn {r['prompt_number']:>3} [{r['tier']}] "
            f"{_grammar_expand(r['summary'] or '')}"
            for r in rows
        )
        text = "\n".join(header) + ("\n\n" + body if header else body)
        # progressive_disclosure: what we didn't deliver at this layer is the
        # raw event payloads. Counterfactual baseline = sum of every payload
        # in this session (what a "dump it all" tool would have returned);
        # served = the timeline body the agent actually got.
        try:
            row = self._memory_conn.execute(
                "SELECT COALESCE(SUM(p.size_bytes), 0) AS total "
                "FROM tool_events te "
                "LEFT JOIN tool_event_payloads p ON p.id = te.payload_id "
                "WHERE te.session_id = ?",
                (session_id,),
            ).fetchone()
            payload_bytes = int(row["total"] or 0)
        except sqlite3.Error:
            payload_bytes = 0
        if payload_bytes > 0:
            self._record_bucket(
                "progressive_disclosure",
                baseline=payload_bytes // _CHARS_PER_TOKEN,
                served=_count_tokens(text),
                meta={"layer": "timeline", "session_id": session_id},
            )
        return [TextContent(type="text", text=text)]

    def _handle_session_event(self, args):
        try:
            event_id = int(args.get("event_id"))
        except (TypeError, ValueError):
            return [TextContent(type="text", text="event_id must be an integer.")]
        if self._memory_conn is None:
            return [TextContent(type="text", text="Memory store not available.")]
        try:
            row = self._memory_conn.execute(
                "SELECT te.tool_name, te.session_id, te.prompt_number, te.created_at, "
                "te.payload_id, p.raw_input, p.raw_output FROM tool_events te "
                "LEFT JOIN tool_event_payloads p ON p.id = te.payload_id "
                "WHERE te.id = ?",
                (event_id,),
            ).fetchone()
        except Exception as exc:
            return [TextContent(type="text", text=f"event query failed: {exc}")]
        if row is None:
            return [TextContent(
                type="text",
                text=f"No event with id={event_id}.",
            )]
        # Three states for the payload:
        #  (a) payload_id IS NULL — event was captured without a payload row
        #      (e.g. a hook that only logs the descriptor).
        #  (b) payload_id present, raw_input='' / raw_output=NULL — pruned
        #      by `cce sessions prune`'s retention pass.
        #  (c) payload_id present, raws populated — normal case.
        if row["payload_id"] is None:
            return [TextContent(
                type="text",
                text=(
                    f"Event {event_id} ({row['tool_name']}) has no captured payload "
                    "— only its descriptor was recorded."
                ),
            )]
        if not row["raw_input"] and row["raw_output"] is None:
            return [TextContent(
                type="text",
                text=(
                    f"Event {event_id} ({row['tool_name']}) was retained as a summary "
                    "only — its raw payload aged out of the retention window."
                ),
            )]
        raw_input = _truncate_payload(row["raw_input"], _EVENT_PAYLOAD_READ_CAP)
        raw_output = _truncate_payload(row["raw_output"], _EVENT_PAYLOAD_READ_CAP)
        body = (
            f"event {event_id} · {row['tool_name']} · session {row['session_id']} · "
            f"turn {row['prompt_number']} · {row['created_at']}\n\n"
            f"input:\n{raw_input}\n\n"
            f"output:\n{raw_output}"
        )
        # progressive_disclosure: counterfactual = full session payload dump
        # (every event's raw payload). Served = just this one event's body.
        try:
            sib = self._memory_conn.execute(
                "SELECT COALESCE(SUM(p.size_bytes), 0) AS total "
                "FROM tool_events te "
                "LEFT JOIN tool_event_payloads p ON p.id = te.payload_id "
                "WHERE te.session_id = ?",
                (row["session_id"],),
            ).fetchone()
            session_bytes = int(sib["total"] or 0)
        except sqlite3.Error:
            session_bytes = 0
        if session_bytes > 0:
            self._record_bucket(
                "progressive_disclosure",
                baseline=session_bytes // _CHARS_PER_TOKEN,
                served=_count_tokens(body),
                meta={"layer": "event", "event_id": event_id},
            )
        return [TextContent(type="text", text=body)]

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
        """Hybrid recall: union ranked candidates from JSON history, FTS5,
        and sqlite-vec, then merge via reciprocal rank fusion.

        Each source produces its own ranked list; RRF fuses them so an item
        that appears in multiple sources rises, and items unique to one
        source still surface. The previous "embed every candidate" pipeline
        is gone — vec hits already carry a rank from sqlite-vec, so we don't
        re-embed them. JSON-history rows still go through cosine since
        there's no index for them.
        """
        topic = topic.strip()
        if not topic:
            return []

        json_candidates = self._collect_json_candidates()
        json_ranked = self._rank_json_candidates(topic, json_candidates)

        memory_lists = self._collect_memory_db_candidates(topic)

        ranked = _rrf_merge(json_ranked, *memory_lists, top=50)
        if ranked:
            return ranked
        # Total fallback: tolerant substring match against everything we
        # collected so callers always get *something* useful even if every
        # ranking source failed.
        needle = topic.lower()
        all_candidates = list(json_candidates)
        for items in memory_lists:
            all_candidates.extend(items)
        return [t for t in all_candidates if needle in t.lower()]

    def _collect_json_candidates(self) -> list[str]:
        """Decisions / code_areas / Q&A pulled from JSON sessions on disk.

        These predate the memory.db path. Dedup is by formatted text so an
        entry that exists in both stores (the dual-write window) doesn't
        get scored twice.
        """
        current = self._session_capture.get_session_snapshot(self._session_id)
        sessions: list[dict] = []
        if current:
            sessions.append(current)
        sessions.extend(
            self._session_capture.load_recent_sessions(limit=_SESSION_RECALL_WINDOW)
        )

        out: list[str] = []
        seen: set[str] = set()
        for session in sessions:
            for decision in session.get("decisions", []):
                t = (
                    f"[decision] {decision.get('decision', '')} — "
                    f"{decision.get('reason', '')}"
                )
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            for area in session.get("code_areas", []):
                t = (
                    f"[code_area] {area.get('file_path', '')} — "
                    f"{area.get('description', '')}"
                )
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            for question in session.get("questions", []):
                t = (
                    f"[q&a] {question.get('question', '')} → "
                    f"{question.get('answer', '')}"
                )
                if t not in seen:
                    seen.add(t)
                    out.append(t)
        for decision in self._session_capture._load_consolidated_decisions():
            t = (
                f"[decision] {decision.get('decision', '')} — "
                f"{decision.get('reason', '')}"
            )
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _rank_json_candidates(self, topic: str, candidates: list[str]) -> list[str]:
        """Cosine-rank JSON candidates and drop sub-threshold entries.

        Memory.db rows already get FTS/vec ranking, but JSON-history rows
        have no index, so we still pay the per-candidate embed_query() here.
        Mitigated by `Embedder.embed_query`'s @lru_cache.

        Embeds the topic with stop words stripped — "how can we improve code
        quality" → "improve code quality" — so the topic vector lands on
        the topic words rather than the question framing. Sharpens the
        signal substantially on conversational queries.
        """
        if not candidates:
            return []
        topic_for_embed = _strip_stop_words(topic) or topic
        try:
            topic_vec = list(self._embedder.embed_query(topic_for_embed))
        except Exception as exc:
            log.debug("topic embed failed (%s); JSON candidates ranked by recency", exc)
            return candidates
        scored: list[tuple[float, str]] = []
        for text in candidates:
            # Embed the *content* (no [tag] prefix) so the metadata noise
            # doesn't inflate similarity for unrelated topics. The agent
            # still sees the tagged form in the output.
            content = _content_key(text)
            try:
                vec = list(self._embedder.embed_query(content))
            except Exception:
                continue
            sim = _cosine_sim(topic_vec, vec)
            if sim >= _SESSION_RECALL_MIN_SIM:
                scored.append((sim, text))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _, text in scored]

    def _collect_memory_db_candidates(self, topic: str) -> list[list[str]]:
        """Return one ranked list per memory.db source (FTS5 + sqlite-vec).

        Each list is in the source's own rank order; RRF combines them.
        Empty lists are returned (not omitted) so callers see a stable shape.
        """
        if self._memory_conn is None:
            return []
        fts_q = _fts_match_query(topic)
        like_needle = f"%{topic.strip()}%" if topic.strip() else None

        fts_decisions: list[str] = []
        fts_turns: list[str] = []
        vec_decisions: list[str] = []
        vec_turns: list[str] = []
        code_areas_hits: list[str] = []

        try:
            if fts_q:
                fts_decisions = self._fetch_decisions_by_query(
                    "SELECT d.id FROM decisions d "
                    "JOIN decisions_fts f ON f.rowid = d.id "
                    "WHERE decisions_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_q, _FTS_RECALL_LIMIT),
                )
                fts_turns = self._fetch_turns_by_query(
                    "SELECT t.id FROM turn_summaries t "
                    "JOIN turn_summaries_fts f ON f.rowid = t.id "
                    "WHERE turn_summaries_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_q, _FTS_RECALL_LIMIT),
                )
            # Embed the stop-word-stripped form so conversational queries
            # ("how can we improve code") embed on their topic words rather
            # than the question framing.
            vec_topic = _strip_stop_words(topic) or topic
            vec_decision_ids = memory_db.search_decisions_vec(
                self._memory_conn, self._embedder, vec_topic, k=_VEC_RECALL_K,
            )
            if vec_decision_ids:
                vec_decisions = self._format_decisions_in_id_order(vec_decision_ids)
            vec_turn_ids = memory_db.search_turn_summaries_vec(
                self._memory_conn, self._embedder, vec_topic, k=_VEC_RECALL_K,
            )
            if vec_turn_ids:
                vec_turns = self._format_turns_in_id_order(vec_turn_ids)
            if like_needle is not None:
                for row in self._memory_conn.execute(
                    "SELECT file_path, description, source, session_id "
                    "FROM code_areas WHERE file_path LIKE ? OR description LIKE ? "
                    "ORDER BY created_at_epoch DESC LIMIT ?",
                    (like_needle, like_needle, _FTS_RECALL_LIMIT),
                ):
                    code_areas_hits.append(
                        f"[code_area src={row['source']}|sid:{row['session_id'] or '-'}] "
                        f"{row['file_path']} — {row['description']}"
                    )
        except Exception:
            log.exception("memory.db recall query failed; FTS+vec lists may be partial")

        return [fts_decisions, fts_turns, vec_decisions, vec_turns, code_areas_hits]

    def _fetch_decisions_by_query(self, sql: str, params: tuple) -> list[str]:
        """Run an id-returning query, fetch the rows, format in *query* order."""
        ids = [r["id"] for r in self._memory_conn.execute(sql, params)]
        return self._format_decisions_in_id_order(ids)

    def _fetch_turns_by_query(self, sql: str, params: tuple) -> list[str]:
        ids = [r["id"] for r in self._memory_conn.execute(sql, params)]
        return self._format_turns_in_id_order(ids)

    def _format_decisions_in_id_order(self, ids: list[int]) -> list[str]:
        """Fetch decisions and emit them in the order of `ids` (preserves rank).

        Each line includes a relative-time hint and a drill-down affordance
        so the agent rarely needs a follow-up call to figure out how to
        navigate from a recall hit back to its session. Decision text and
        reason are run through `grammar.expand()` to restore well-known
        abbreviations (b/c → because, prod → production) before display —
        on-disk storage stays compressed.
        """
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = {
            r["id"]: r for r in self._memory_conn.execute(
                f"SELECT id, decision, reason, source, session_id, "
                f"created_at_epoch FROM decisions WHERE id IN ({placeholders})",
                tuple(ids),
            )
        }
        out: list[str] = []
        for rid in ids:
            r = rows.get(rid)
            if r is None:
                continue
            recency = _humanise_relative_time(r["created_at_epoch"])
            sid = r["session_id"]
            tail = f" · {recency}" if recency else ""
            if sid:
                tail += f' · → session_timeline("{sid}")'
            decision = _grammar_expand(r["decision"] or "")
            reason = _grammar_expand(r["reason"] or "")
            out.append(
                f"[decision src={r['source']}|sid:{sid or '-'}] "
                f"{decision} — {reason}{tail}"
            )
        return out

    def _format_turns_in_id_order(self, ids: list[int]) -> list[str]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = {
            r["id"]: r for r in self._memory_conn.execute(
                f"SELECT id, session_id, prompt_number, summary, "
                f"created_at_epoch FROM turn_summaries WHERE id IN ({placeholders})",
                tuple(ids),
            )
        }
        out: list[str] = []
        for rid in ids:
            r = rows.get(rid)
            if r is None:
                continue
            recency = _humanise_relative_time(r["created_at_epoch"])
            tail = f" · {recency}" if recency else ""
            tail += f' · → session_event(id={r["id"]})'
            summary = _grammar_expand(r["summary"] or "")
            out.append(
                f"[turn sid:{r['session_id']}|n:{r['prompt_number']}] "
                f"{summary}{tail}"
            )
        return out

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
