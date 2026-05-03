"""Reusable indexing pipeline — shared by the CLI (`cce index`) and MCP (`reindex`).

This module owns the full index-a-project flow so the CLI and MCP server don't
duplicate logic and can't drift. Callers pass a structured `IndexResult` back so
they can format their own output (click.echo, MCP text response, logs).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import subprocess

from context_engine.indexer.chunker import Chunker
from context_engine.indexer.embedder import Embedder
from context_engine.indexer.embedding_cache import EmbeddingCache
from context_engine.indexer.git_indexer import index_commits
from context_engine.indexer.manifest import Manifest
from context_engine.models import ChunkType, GraphNode, GraphEdge, NodeType, EdgeType
from context_engine.storage.local_backend import LocalBackend


# Map a chunk's semantic type to its graph node type. Without this every
# non-function chunk used to land as NodeType.CLASS, which polluted the graph
# (e.g. markdown / yaml / json / module-level fallback chunks all looked like
# classes and degraded related_context expansion).
_CHUNK_TO_NODE_TYPE = {
    ChunkType.FUNCTION: NodeType.FUNCTION,
    ChunkType.CLASS: NodeType.CLASS,
    ChunkType.MODULE: NodeType.MODULE,
    ChunkType.DOC: NodeType.DOC,
    ChunkType.COMMENT: NodeType.DOC,
    ChunkType.COMMIT: NodeType.COMMIT,
    ChunkType.SESSION: NodeType.SESSION,
    ChunkType.DECISION: NodeType.DECISION,
}

log = logging.getLogger(__name__)


class PathOutsideProjectError(ValueError):
    """Raised when a target_path resolves outside the project root."""


def _resolve_within(project_dir: Path, target: str | Path) -> Path:
    """Resolve `target` relative to project_dir and assert it stays inside.

    Prevents path traversal via `target_path="../../etc/passwd"` from any caller
    that hands user input to `run_indexing`. Always call this before reading or
    walking `target` against the filesystem.
    """
    p = Path(target)
    if not p.is_absolute():
        p = project_dir / p
    resolved = p.resolve()
    project_resolved = project_dir.resolve()
    try:
        resolved.relative_to(project_resolved)
    except ValueError as exc:
        raise PathOutsideProjectError(
            f"target path escapes project directory: {target}"
        ) from exc
    return resolved


# Serialise indexing runs so a watcher-triggered re-index can't race a manual
# `cce index` or MCP `reindex` tool call on the same LanceDB table.
_PIPELINE_LOCKS: dict[str, asyncio.Lock] = {}


def _pipeline_lock(storage_key: str) -> asyncio.Lock:
    lock = _PIPELINE_LOCKS.get(storage_key)
    if lock is None:
        lock = asyncio.Lock()
        _PIPELINE_LOCKS[storage_key] = lock
    return lock

# Binary / non-text extensions to skip (images, compiled, archives, etc.)
_SKIP_EXTENSIONS = {
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".svg",
    # Compiled / bytecode
    ".pyc", ".pyo", ".class", ".o", ".so", ".dylib", ".dll", ".exe", ".wasm",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    # Data / binary
    ".db", ".sqlite", ".sqlite3", ".bin", ".dat", ".pkl", ".pickle",
    ".parquet", ".arrow", ".lance",
    # Media
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flv", ".ogg", ".webm",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Documents (non-text)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Package locks (huge, not useful for context)
    ".lock",
    # Source maps
    ".map",
}

# Known extension → language mapping for tree-sitter and chunk metadata.
# Files with unlisted extensions are still indexed as "plaintext".
_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "tsx",
    ".md": "markdown",
    ".php": "php",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
    ".xml": "xml",
    ".r": "r",
    ".R": "r",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".scala": "scala",
    ".clj": "clojure",
    ".dart": "dart",
    ".vue": "vue",
    ".svelte": "svelte",
    ".pl": "perl",
    ".pm": "perl",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".zig": "zig",
    ".nim": "nim",
    ".v": "vlang",
    ".tf": "terraform",
    ".hcl": "hcl",
    ".dockerfile": "dockerfile",
}


@dataclass
class IndexResult:
    indexed_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    total_chunks: int = 0
    errors: list[str] = field(default_factory=list)
    # Embedding-cache hit/miss counters from the most-recent embedder run.
    # Surfaced in `cce index` output so users can see how much the cache saved.
    cache_hits: int = 0
    cache_misses: int = 0


def _iter_project_files(
    root: Path,
    ignore_set: set[str],
    skip_extensions: set[str],
    *,
    redact_secrets: bool = True,
    cceignore_patterns: list[str] | None = None,
) -> Iterable[Path]:
    """Yield files under `root` respecting ignore list, skipping symlinks.

    Symlinks are skipped outright to avoid loops; callers who need symlink
    following can resolve them before calling the pipeline.

    When `redact_secrets` is True (default), filenames matching well-known
    credential patterns (.env*, *.pem, secrets.yml, etc.) are skipped at
    the filesystem walk so they're never read or embedded. See
    `indexer/secrets.py` for the full pattern list.

    `cceignore_patterns` (typically loaded from `.cceignore`) supplements
    the name-only `ignore_set` with gitignore-style globs evaluated
    against the path relative to `root`.
    """
    from context_engine.indexer.secrets import is_secret_file as _is_secret_file
    from context_engine.indexer.ignorefile import matches_any as _ignore_matches
    patterns = cceignore_patterns or []
    seen: set[Path] = set()

    def _rel(entry: Path) -> str:
        try:
            return str(entry.relative_to(root)).replace("\\", "/")
        except ValueError:
            return entry.name

    def walk(directory: Path) -> Iterable[Path]:
        try:
            entries = sorted(directory.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.name in ignore_set:
                continue
            if entry.is_symlink():
                continue
            try:
                resolved = entry.resolve()
            except (OSError, RuntimeError):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            # Evaluate .cceignore against the path relative to project root.
            # Done after symlink/seen checks so we don't pay the cost on
            # files we'd skip anyway.
            if patterns and _ignore_matches(_rel(entry), entry.is_dir(), patterns):
                continue
            if entry.is_dir():
                yield from walk(entry)
            elif entry.is_file() and entry.suffix not in skip_extensions:
                if redact_secrets and _is_secret_file(entry):
                    log.info("indexer: skipping secret file %s", entry)
                    continue
                yield entry

    yield from walk(root)


# Skip any single file larger than this — protects the indexer from OOM on
# accidentally-committed log dumps, generated fixtures, vendored bundles, etc.
# 2 MB easily covers normal source files (the largest module in CPython's
# stdlib is ~250 KB) while ruling out the kind of file you'd never want in
# a semantic index anyway.
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _safe_read(file_path: Path) -> str | None:
    """Read file as UTF-8 text; return None for binary, oversized, or unreadable files."""
    try:
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return file_path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return None


async def run_indexing(
    config,
    project_dir: str | Path,
    *,
    full: bool = False,
    target_path: str | None = None,
    log_fn=None,
    progress_fn=None,
    embed_progress_fn=None,
    phase_fn=None,
) -> IndexResult:
    """Run the indexing pipeline. Returns a structured `IndexResult`.

    `target_path` (optional) restricts indexing to a single file or subtree.
    `full=True` ignores the manifest and re-indexes everything visible.
    `log_fn(msg)` is called for verbose progress output if provided.
    `progress_fn(current, total)` is called after each batch with file counts.
    `embed_progress_fn(current, total)` is called as embedding proceeds with
    chunk counts (only for cache misses; cache hits return instantly).
    `phase_fn(msg)` (if provided) is called between major phases —
    "Embedding 32k chunks…", "Writing to index…" — so non-verbose callers
    can announce *what* is starting; embed_progress_fn then drives motion
    *within* the embed phase. Both serve the same goal (don't look hung
    on large repos) and are complementary: phase_fn is per-phase, embed_
    progress_fn is per-batch.
    """
    project_dir = Path(project_dir)
    project_name = project_dir.name
    storage_base = Path(config.storage_path) / project_name
    storage_base.mkdir(parents=True, exist_ok=True)

    async with _pipeline_lock(str(storage_base)):
        return await _run_indexing_locked(
            config,
            project_dir,
            storage_base,
            full=full,
            target_path=target_path,
            log_fn=log_fn,
            progress_fn=progress_fn,
            embed_progress_fn=embed_progress_fn,
            phase_fn=phase_fn,
        )


async def _run_indexing_locked(
    config,
    project_dir: Path,
    storage_base: Path,
    *,
    full: bool,
    target_path: str | None,
    log_fn,
    progress_fn=None,
    embed_progress_fn=None,
    phase_fn=None,
) -> IndexResult:
    """Streaming pipeline: embed + ingest each file-batch as it's produced
    instead of accumulating the whole project in memory before persisting.

    Memory profile: peak chunk-list size is one file-batch (≤ _BATCH files)
    rather than the entire project. On a 10k-file repo this drops peak RSS
    by ~60-70%. The Embedder + EmbeddingCache are created lazily on the
    first non-empty batch and reused across batches so the ONNX model is
    loaded only once.

    Durability semantics preserved:
    - Per-batch deletes happen AFTER embed succeeds, so an embed failure
      cannot wipe previously-indexed rows.
    - Manifest is saved only at the very end, so a mid-pipeline failure
      leaves the on-disk manifest unchanged and the next run will retry
      the affected files. Already-ingested batches stay in the index but
      will be re-ingested idempotently (chunk IDs are deterministic and
      backed by ON CONFLICT DO UPDATE).
    """
    backend = LocalBackend(base_path=str(storage_base))
    chunker = Chunker()
    manifest = Manifest(manifest_path=storage_base / "manifest.json")
    ignore_set = set(config.indexer_ignore)
    # Load .cceignore once per indexing run. Patterns are evaluated against
    # paths relative to project_dir; see indexer/ignorefile.py.
    from context_engine.indexer.ignorefile import load_ignore_patterns
    cceignore_patterns = load_ignore_patterns(project_dir)
    if cceignore_patterns and log_fn:
        log_fn(f"  [.cceignore] {len(cceignore_patterns)} pattern(s) loaded")
    result = IndexResult()

    # Determine the set of files to scan.
    if target_path:
        target = _resolve_within(project_dir, target_path)
        if target.is_file():
            file_iter = [target] if target.suffix not in _SKIP_EXTENSIONS else []
        elif target.is_dir():
            file_iter = list(_iter_project_files(
                target, ignore_set, _SKIP_EXTENSIONS,
                redact_secrets=getattr(config, "indexer_redact_secrets", True),
                cceignore_patterns=cceignore_patterns,
            ))
        else:
            result.errors.append(f"Target path not found: {target_path}")
            return result
    else:
        file_iter = list(_iter_project_files(
            project_dir, ignore_set, _SKIP_EXTENSIONS,
            redact_secrets=getattr(config, "indexer_redact_secrets", True),
            cceignore_patterns=cceignore_patterns,
        ))

    current_rel_paths: set[str] = set()
    # Hashes of every chunk content embedded this run — used at the very end
    # to prune orphans from the embedding cache (full re-index only). Cheap to
    # accumulate (~64 bytes/hash) compared to keeping every chunk + embedding.
    live_hashes: set[str] = set()

    # Lazy-initialised on the first non-empty batch so projects that skip
    # everything via the manifest don't pay the model-load cost.
    embedder: Embedder | None = None
    cache: EmbeddingCache | None = None

    async def _read_file(fp: Path) -> tuple[Path, str | None]:
        return fp, await asyncio.to_thread(_safe_read, fp)

    async def _chunk_file(rel_path: str, content: str, language: str):
        return await asyncio.to_thread(
            chunker.chunk_with_imports, content, rel_path, language
        )

    _BATCH = 50
    total_batches = (len(file_iter) + _BATCH - 1) // _BATCH

    def _ensure_embedder() -> tuple[Embedder, EmbeddingCache]:
        nonlocal embedder, cache
        if cache is None:
            cache = EmbeddingCache(
                storage_base / "embedding_cache.db",
                model_name=config.embedding_model,
            )
        if embedder is None:
            embedder = Embedder(model_name=config.embedding_model, cache=cache)
        return embedder, cache

    async def _embed_and_ingest(
        batch_chunks: list,
        batch_nodes: list[GraphNode],
        batch_edges: list[GraphEdge],
        batch_files_to_replace: list[str],
        *,
        label: str,
    ) -> bool:
        """Embed + ingest one batch. Returns True on success, False on failure
        (caller should bail out — manifest will not be saved)."""
        emb, cch = _ensure_embedder()
        if phase_fn:
            phase_fn(f"Embedding {len(batch_chunks):,} chunks{label}…")
        try:
            emb.embed(batch_chunks, progress_fn=embed_progress_fn)
        except Exception as exc:
            msg = f"Embedding failed: {exc}"
            result.errors.append(msg)
            log.warning(msg, exc_info=exc)
            return False
        if full and not target_path:
            live_hashes.update(cch.content_hash(c.content) for c in batch_chunks)
        if batch_files_to_replace:
            try:
                await backend.delete_by_files(batch_files_to_replace)
            except Exception as exc:
                msg = f"Pre-ingest delete failed: {exc}"
                result.errors.append(msg)
                log.warning(msg, exc_info=exc)
                return False
        if phase_fn:
            phase_fn(
                f"Writing {len(batch_chunks):,} chunks to vector + FTS + graph index{label}…"
            )
        try:
            await backend.ingest(batch_chunks, batch_nodes, batch_edges)
        except Exception as exc:
            msg = f"Backend ingest failed: {exc}"
            result.errors.append(msg)
            log.warning(msg, exc_info=exc)
            return False
        return True

    try:
        for batch_idx, batch_start in enumerate(range(0, len(file_iter), _BATCH)):
            batch_paths = file_iter[batch_start:batch_start + _BATCH]

            read_tasks = [_read_file(fp) for fp in batch_paths]
            read_results = await asyncio.gather(*read_tasks)

            to_chunk: list[tuple[Path, str, str, str, str]] = []
            for file_path, content in read_results:
                rel_path = str(file_path.relative_to(project_dir))
                current_rel_paths.add(rel_path)

                if content is None:
                    result.skipped_files.append(rel_path)
                    if log_fn:
                        log_fn(f"  [skip] {rel_path} (binary or unreadable)")
                    continue

                if getattr(config, "indexer_redact_secrets", True):
                    from context_engine.indexer.secrets import redact_secrets
                    content, fired = redact_secrets(content)
                    if fired:
                        log.info(
                            "indexer: redacted %d secret(s) in %s (kinds: %s)",
                            len(fired), rel_path, ",".join(sorted(set(fired))),
                        )
                        if log_fn:
                            log_fn(f"  [redact] {rel_path} ({len(fired)} secret(s))")

                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if not full and not manifest.has_changed(rel_path, content_hash):
                    if log_fn:
                        log_fn(f"  [skip] {rel_path} (unchanged)")
                    continue

                language = _LANGUAGE_MAP.get(file_path.suffix, "plaintext")
                to_chunk.append((file_path, rel_path, content, content_hash, language))

            batch_chunks: list = []
            batch_nodes: list[GraphNode] = []
            batch_edges: list[GraphEdge] = []
            batch_files_to_replace: list[str] = []
            batch_manifest_updates: list[tuple[str, str]] = []
            batch_indexed: list[str] = []

            if to_chunk:
                chunk_tasks = [
                    _chunk_file(rel_path, content, language)
                    for (_, rel_path, content, _, language) in to_chunk
                ]
                chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

                for (file_path, rel_path, content, content_hash, language), chunk_outcome in zip(
                    to_chunk, chunk_results
                ):
                    if isinstance(chunk_outcome, Exception):
                        result.errors.append(f"Chunking failed for {rel_path}: {chunk_outcome}")
                        log.warning("Chunking failed for %s", rel_path, exc_info=chunk_outcome)
                        continue
                    chunks, imported_modules = chunk_outcome

                    batch_files_to_replace.append(rel_path)

                    file_node = GraphNode(
                        id=f"file_{rel_path}",
                        node_type=NodeType.FILE,
                        name=file_path.name,
                        file_path=rel_path,
                    )
                    batch_nodes.append(file_node)

                    for module in imported_modules:
                        batch_edges.append(
                            GraphEdge(
                                source_id=file_node.id,
                                target_id=f"module_{module}",
                                edge_type=EdgeType.IMPORTS,
                            )
                        )

                    for chunk in chunks:
                        node_type = _CHUNK_TO_NODE_TYPE.get(
                            chunk.chunk_type, NodeType.MODULE
                        )
                        node_name = (
                            chunk.content.split("(")[0].split(":")[-1].strip()
                            if "(" in chunk.content
                            else chunk.id
                        )
                        batch_nodes.append(
                            GraphNode(
                                id=chunk.id,
                                node_type=node_type,
                                name=node_name,
                                file_path=rel_path,
                            )
                        )
                        batch_edges.append(
                            GraphEdge(
                                source_id=file_node.id,
                                target_id=chunk.id,
                                edge_type=EdgeType.DEFINES,
                            )
                        )
                    batch_chunks.extend(chunks)
                    batch_manifest_updates.append((rel_path, content_hash))
                    batch_indexed.append(rel_path)

            if progress_fn:
                progress_fn(min(batch_start + len(batch_paths), len(file_iter)), len(file_iter))

            if batch_chunks:
                label = f" (batch {batch_idx + 1}/{total_batches})" if total_batches > 1 else ""
                ok = await _embed_and_ingest(
                    batch_chunks, batch_nodes, batch_edges, batch_files_to_replace,
                    label=label,
                )
                if not ok:
                    return result
                for rel_path, content_hash in batch_manifest_updates:
                    manifest.update(rel_path, content_hash)
                result.indexed_files.extend(batch_indexed)
                result.total_chunks += len(batch_chunks)
            elif batch_files_to_replace:
                # Files were chunked but produced zero chunks (e.g. an empty
                # file or one the chunker rejected). Still drop their old rows.
                try:
                    await backend.delete_by_files(batch_files_to_replace)
                except Exception as exc:
                    msg = f"Replacement delete failed: {exc}"
                    result.errors.append(msg)
                    log.warning(msg, exc_info=exc)
                    return result
                for rel_path, content_hash in batch_manifest_updates:
                    manifest.update(rel_path, content_hash)
                result.indexed_files.extend(batch_indexed)

        # Index git history as one extra logical batch on full runs.
        _is_git = (Path(project_dir) / ".git").is_dir()
        if full and not target_path and _is_git:
            try:
                git_chunks, git_nodes, git_edges = await index_commits(
                    project_dir, since_sha=manifest.last_git_sha
                )
            except Exception as exc:
                log.warning("Git history indexing failed: %s", exc)
                git_chunks, git_nodes, git_edges = [], [], []

            if git_chunks:
                ok = await _embed_and_ingest(
                    git_chunks, git_nodes, git_edges, [],
                    label=" (git history)" if total_batches > 0 else "",
                )
                if not ok:
                    return result
                result.total_chunks += len(git_chunks)
                head_result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "rev-parse", "HEAD"],
                    cwd=project_dir, capture_output=True, text=True, check=False,
                )
                if head_result.returncode == 0:
                    manifest.last_git_sha = head_result.stdout.strip()
                if log_fn:
                    log_fn(f"  [git] {len(git_chunks)} commit(s) indexed")

        if cache is not None:
            result.cache_hits = cache.hits
            result.cache_misses = cache.misses
            if full and not target_path:
                try:
                    pruned = cache.prune_orphans(live_hashes)
                    if pruned and log_fn:
                        log_fn(f"  [cache] pruned {pruned} orphan embedding(s)")
                except Exception as exc:
                    log.debug("Embedding cache prune skipped: %s", exc)

        # Prune chunks for files that were in the manifest but no longer on disk.
        if not target_path:
            previous_rel_paths = set(manifest._entries.keys())  # noqa: SLF001
            removed = list(previous_rel_paths - current_rel_paths)
            if removed:
                try:
                    await backend.delete_by_files(removed)
                except Exception as exc:  # pragma: no cover - defensive
                    result.errors.append(f"Failed to prune deleted files: {exc}")
                    removed = []
            for deleted in removed:
                try:
                    manifest.remove(deleted)
                    result.deleted_files.append(deleted)
                    if log_fn:
                        log_fn(f"  [delete] {deleted} (no longer on disk)")
                except Exception as exc:  # pragma: no cover - defensive
                    result.errors.append(f"Failed to prune {deleted}: {exc}")

        manifest.save()
        return result
    finally:
        if cache is not None:
            cache.close()
