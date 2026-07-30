"""Parse git log into searchable chunks."""
import asyncio
import datetime
import logging
import re
import subprocess
from pathlib import Path

from context_engine.models import (
    Chunk, ChunkType, GraphNode, GraphEdge, NodeType, EdgeType,
)

log = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Delimiter placed at the START of each commit record so we can split cleanly.
_RECORD_START = "---CCE_START---"


async def index_commits(
    project_dir: Path,
    since_sha: str | None = None,
    max_commits: int = 200,
) -> tuple[list[Chunk], list[GraphNode], list[GraphEdge]]:
    """Parse recent git history into searchable chunks."""
    # Use two separate git calls:
    # 1. git log --format=... to get commit metadata in order
    # 2. git log --name-only to get changed files per commit
    range_arg = f"{since_sha}..HEAD" if since_sha else f"-{max_commits}"

    meta_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "-c", "i18n.logOutputEncoding=UTF-8",
         "log", range_arg, "--format=%H%n%an%n%ai%n%s%n%b%x00"],
        cwd=project_dir, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )

    if meta_result.returncode != 0:
        log.debug("git log skipped: %s", meta_result.stderr.strip())
        return [], [], []

    files_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "-c", "i18n.logOutputEncoding=UTF-8",
         "log", range_arg, "--name-only", "--format=%H"],
        cwd=project_dir, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )

    changed_files_by_hash: dict[str, list[str]] = {}
    if files_result.returncode == 0:
        changed_files_by_hash = _parse_name_only(files_result.stdout)

    return _parse_meta(meta_result.stdout, changed_files_by_hash)


def _parse_name_only(output: str) -> dict[str, list[str]]:
    """Parse `git log --name-only --format=%H` output into {hash: [files]}."""
    result: dict[str, list[str]] = {}
    current_hash: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SHA_RE.match(stripped):
            current_hash = stripped
            result.setdefault(current_hash, [])
        elif current_hash is not None:
            result[current_hash].append(stripped)
    return result


def _parse_meta(
    output: str,
    changed_files_by_hash: dict[str, list[str]],
) -> tuple[list[Chunk], list[GraphNode], list[GraphEdge]]:
    """Parse commit metadata output and build chunks/nodes/edges."""
    chunks: list[Chunk] = []
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # Records are separated by NUL bytes (\x00)
    records = output.split("\x00")
    for record in records:
        record = record.strip()
        if not record:
            continue

        lines = record.splitlines()
        if len(lines) < 4:
            continue

        commit_hash = lines[0].strip()
        if not _SHA_RE.match(commit_hash):
            continue

        author = lines[1].strip()
        date = lines[2].strip()
        subject = lines[3].strip()
        body = "\n".join(lines[4:]).strip()

        content = f"{subject}\n\n{body}".strip()
        short_hash = commit_hash[:7]

        metadata = {
            "author": author,
            "date": date,
            "hash": commit_hash,
            "chunk_kind": "commit",
        }
        # Stamp commit time as epoch so retrieval's recency weight applies
        # to git-history chunks too. `date` is git log %ai, e.g.
        # "2026-07-03 20:15:25 +0100" — fromisoformat parses the ±HHMM
        # offset on Python 3.11+. Parse failure is non-fatal — the chunk
        # just keeps neutral recency (matching the stat() convention in
        # the file-indexing pipeline).
        try:
            metadata["modified_ts"] = datetime.datetime.fromisoformat(
                date
            ).timestamp()
        except ValueError:
            pass

        chunk = Chunk(
            id=f"commit_{short_hash}",
            content=content,
            chunk_type=ChunkType.COMMIT,
            file_path=f"git:{short_hash}",
            start_line=0,
            end_line=0,
            language="git",
            metadata=metadata,
        )
        chunks.append(chunk)

        node = GraphNode(
            id=f"commit_{short_hash}",
            node_type=NodeType.COMMIT,
            name=subject,
            file_path=f"git:{short_hash}",
        )
        nodes.append(node)

        for fname in changed_files_by_hash.get(commit_hash, []):
            edges.append(
                GraphEdge(
                    source_id=f"commit_{short_hash}",
                    target_id=f"file_{fname}",
                    edge_type=EdgeType.MODIFIES,
                )
            )

    return chunks, nodes, edges
