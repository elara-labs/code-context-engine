"""Regression tests for embedder dimension changes on the memory vec tables.

The vec tables are bootstrapped at `_VEC_DIM` (bge-small = 384), but a project
can point cce at any embedder — e.g. Ollama's default `nomic-embed-text` emits
768-dim vectors. Before `_ensure_vec_dim` existed, a dimension change made every
`_write_vec_row` INSERT and every `search_*_vec` MATCH raise a mismatch error
that the callers swallow at debug level, silently disabling semantic recall
forever. These tests pin the self-healing rebuild.
"""
from __future__ import annotations

from pathlib import Path

from context_engine.memory import db as memory_db


class _FixedDimEmbedder:
    """Deterministic embedder emitting a vector of exactly `dim` floats."""

    def __init__(self, dim: int):
        self._dim = dim

    def embed_query(self, text: str):
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return tuple((digest[i % 32] / 255.0) - 0.5 for i in range(self._dim))


def _insert_decision(conn, decision: str, reason: str) -> int:
    cur = conn.execute(
        "INSERT INTO decisions (decision, reason, source, "
        "created_at_epoch, created_at) "
        "VALUES (?, ?, 'manual', 1700000000, '2023-11-14T22:13:20')",
        (decision, reason),
    )
    return cur.lastrowid


def test_bootstrap_dim_is_384(tmp_path: Path):
    """Sanity: the vec tables start at the bge-small default dimension."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        assert memory_db._declared_vec_dim(conn) == memory_db._VEC_DIM == 384
    finally:
        conn.close()


def test_record_with_larger_dim_rebuilds_and_persists(tmp_path: Path):
    """A 768-dim embedder (e.g. nomic-embed-text) must rebuild the 384-dim vec
    tables and actually persist the row, not silently drop it."""
    conn = memory_db.connect(tmp_path / "memory.db")
    embedder = _FixedDimEmbedder(768)
    try:
        did = _insert_decision(
            conn, "Use nomic-embed-text", "Runs locally via Ollama"
        )
        memory_db.record_decision_vec(
            conn, embedder,
            decision_id=did,
            decision="Use nomic-embed-text",
            reason="Runs locally via Ollama",
        )
        conn.commit()

        # The tables were rebuilt to the new dimension...
        assert memory_db._declared_vec_dim(conn) == 768
        # ...and the vec row was written (pre-fix this was silently swallowed).
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM decisions_vec WHERE rowid = ?", (did,)
        ).fetchone()["n"]
        assert n == 1
        # ...so semantic recall finds it again.
        hits = memory_db.search_decisions_vec(
            conn, embedder, "nomic embed", k=5, max_distance=99.0,
        )
        assert did in hits
    finally:
        conn.close()


def test_matching_dim_does_not_rebuild(tmp_path: Path):
    """The default 384-dim embedder must not trigger a rebuild that would drop
    previously written rows."""
    conn = memory_db.connect(tmp_path / "memory.db")
    embedder = _FixedDimEmbedder(memory_db._VEC_DIM)
    try:
        first = _insert_decision(conn, "First decision", "Reason one")
        memory_db.record_decision_vec(
            conn, embedder, decision_id=first,
            decision="First decision", reason="Reason one",
        )
        second = _insert_decision(conn, "Second decision", "Reason two")
        memory_db.record_decision_vec(
            conn, embedder, decision_id=second,
            decision="Second decision", reason="Reason two",
        )
        conn.commit()

        assert memory_db._declared_vec_dim(conn) == memory_db._VEC_DIM
        rows = conn.execute("SELECT COUNT(*) AS n FROM decisions_vec").fetchone()["n"]
        assert rows == 2, "matching-dim writes must not drop earlier rows"
    finally:
        conn.close()


def test_backfill_after_dim_change_repopulates(tmp_path: Path):
    """After an embedder swap, backfill must rebuild the vec tables and re-embed
    every existing source row at the new dimension."""
    conn = memory_db.connect(tmp_path / "memory.db")
    try:
        # Populate the vec tables at the original 384 dimension first.
        did = _insert_decision(conn, "Adopt hybrid recall", "FTS + vec union")
        conn.execute(
            "INSERT INTO sessions (id, project, started_at_epoch, started_at, "
            "status) VALUES ('s1', 'demo', 1700000000, "
            "'2023-11-14T22:13:20', 'active')"
        )
        conn.execute(
            "INSERT INTO turn_summaries (session_id, prompt_number, summary, "
            "tier, created_at_epoch) VALUES ('s1', 1, ?, 'extractive', 1700000001)",
            ("Discussed hybrid recall tradeoffs.",),
        )
        conn.commit()
        memory_db.backfill_vec_tables(conn, _FixedDimEmbedder(384))
        assert memory_db._declared_vec_dim(conn) == 384

        # Now swap to a 768-dim embedder and backfill again.
        big = _FixedDimEmbedder(768)
        counts = memory_db.backfill_vec_tables(conn, big)

        assert memory_db._declared_vec_dim(conn) == 768
        # Every source row was re-embedded at the new dimension.
        assert counts["decisions"] == 1
        assert counts["turn_summaries"] == 1
        assert did in memory_db.search_decisions_vec(
            conn, big, "hybrid recall", k=5, max_distance=99.0,
        )
    finally:
        conn.close()
