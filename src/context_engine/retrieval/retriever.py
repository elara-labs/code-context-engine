"""Hybrid retrieval — vector search + FTS BM25 + RRF merging + confidence scoring."""
import logging

from context_engine.models import Chunk
from context_engine.storage.backend import StorageBackend
from context_engine.indexer.embedder import Embedder
from context_engine.retrieval.confidence import ConfidenceScorer
from context_engine.retrieval.query_parser import QueryIntent, QueryParser

log = logging.getLogger(__name__)

_DEPRIORITISED_PATHS = {"tests/", "test_", "docs/", "spec", "plan"}
_RRF_K = 60
# Confidence weight in the final blend. The remainder goes to RRF, normalised to
# [0,1] by the best score in the candidate set so an exact-match FTS rank-1 hit
# scores the same as a vector rank-1 hit instead of being clamped to ~1.0.
_CONFIDENCE_WEIGHT = 0.5
# Max chunks from the same file in the final result set. Prevents one large
# file from dominating results and improves file-level precision.
_MAX_CHUNKS_PER_FILE = 3
# When the parsed query looks like a code lookup, give FTS more pull because
# exact-identifier hits are usually what the user wants.
_FTS_BOOST_CODE_LOOKUP = 1.5


class HybridRetriever:
    def __init__(self, backend: StorageBackend, embedder: Embedder) -> None:
        self._backend = backend
        self._embedder = embedder
        self._scorer = ConfidenceScorer()
        self._parser = QueryParser()
        self._fts_warned = False

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        confidence_threshold: float = 0.0,
        max_tokens: int | None = None,
    ) -> list[Chunk]:
        parsed = self._parser.parse(query)
        query_embedding = self._embedder.embed_query(query)

        # embed_query returns tuple for LRU cache hashability; vector_store
        # now handles the conversion internally via _to_list().

        vector_results = await self._backend.vector_search(
            query_embedding=query_embedding,
            top_k=max(top_k * 3, 1),
        )

        # FTS search with graceful fallback
        fts_ids: dict[str, int] = {}
        try:
            fts_results = await self._backend.fts_search(query, top_k=top_k * 3)
            fts_ids = {id_: rank for rank, (id_, _) in enumerate(fts_results)}
        except Exception:
            if not self._fts_warned:
                log.warning("FTS search unavailable; falling back to vector-only")
                self._fts_warned = True

        # Build vector rankings and chunk map
        vector_ranks: dict[str, int] = {}
        chunk_map: dict[str, Chunk] = {}
        seen_keys: set[str] = set()

        for rank, chunk in enumerate(vector_results):
            dedup_key = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            vector_ranks[chunk.id] = rank
            chunk_map[chunk.id] = chunk

        # Hydrate FTS-only results
        fts_only_ids = [id_ for id_ in fts_ids if id_ not in chunk_map]
        if fts_only_ids:
            try:
                hydrated = await self._backend.get_chunks_by_ids(fts_only_ids)
                for chunk in hydrated:
                    chunk_map[chunk.id] = chunk
            except Exception as exc:
                log.warning("Failed to hydrate FTS-only chunks: %s", exc)

        # Compute RRF scores. Boost FTS contribution when the parsed intent
        # is CODE_LOOKUP — exact identifier matches are almost always what the
        # user wants and would otherwise be drowned by semantic-similarity hits.
        fts_weight = (
            _FTS_BOOST_CODE_LOOKUP if parsed.intent == QueryIntent.CODE_LOOKUP else 1.0
        )
        all_ids = set(vector_ranks.keys()) | set(fts_ids.keys())
        rrf_scores: dict[str, float] = {}
        for id_ in all_ids:
            score = 0.0
            if id_ in vector_ranks:
                score += 1.0 / (_RRF_K + vector_ranks[id_])
            if id_ in fts_ids:
                score += fts_weight * (1.0 / (_RRF_K + fts_ids[id_]))
            rrf_scores[id_] = score

        # Normalise RRF to [0, 1] by the best score in this candidate set.
        # The previous `min(rrf * _RRF_K, 1.0)` saturated nearly every result to
        # ~1.0, so confidence_score dominated the blend and FTS rank carried
        # almost no signal past the top few. Rank-normalising restores gradient.
        max_rrf = max(rrf_scores.values()) if rrf_scores else 0.0

        # Score with confidence scorer
        scored: list[tuple[Chunk, float]] = []
        for id_, rrf_score in rrf_scores.items():
            chunk = chunk_map.get(id_)
            if chunk is None:
                continue

            distance = chunk.metadata.get("_distance", 0.0)
            normalised_distance = min(max(distance / 2.0, 0.0), 1.0)
            keyword_distance = self._estimate_keyword_distance(chunk, parsed)
            conf_score = self._scorer.score(
                chunk,
                vector_distance=normalised_distance,
                keyword_distance=keyword_distance,
            )

            normalised_rrf = (rrf_score / max_rrf) if max_rrf > 0 else 0.0
            final_score = (
                _CONFIDENCE_WEIGHT * conf_score
                + (1.0 - _CONFIDENCE_WEIGHT) * normalised_rrf
            )
            final_score = self._apply_path_penalty(chunk.file_path, final_score)
            chunk.confidence_score = final_score

            if final_score >= confidence_threshold:
                scored.append((chunk, final_score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # File diversity: cap chunks per file so one large file doesn't
        # dominate the result set. This improves precision by letting
        # chunks from more files surface into the top-k.
        file_counts: dict[str, int] = {}
        diverse: list[Chunk] = []
        for chunk, _ in scored:
            count = file_counts.get(chunk.file_path, 0)
            if count < _MAX_CHUNKS_PER_FILE:
                diverse.append(chunk)
                file_counts[chunk.file_path] = count + 1
                if len(diverse) >= top_k:
                    break
        ranked = diverse

        # Graph expansion: fetch 1-2 bonus chunks from files reachable via
        # CALLS/IMPORTS edges from the top results.
        if ranked and hasattr(self._backend, "get_related_file_paths"):
            try:
                top_files = list({c.file_path for c in ranked[:3]})
                related_files = await self._backend.get_related_file_paths(top_files)
                qe_list = (
                    list(query_embedding)
                    if not isinstance(query_embedding, list)
                    else query_embedding
                )
                for rel_fp in related_files[:2]:  # max 2 bonus files
                    bonus = await self._backend.vector_search(
                        query_embedding=qe_list,
                        top_k=2,
                        filters={"file_path": rel_fp},
                    )
                    for b in bonus:
                        dedup_key = (
                            f"{b.file_path}:{b.start_line}-{b.end_line}"
                        )
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            dist = b.metadata.get("_distance", 1.0)
                            b.confidence_score = max(0.0, 1.0 - dist) * 0.85
                            if b.confidence_score >= confidence_threshold:
                                ranked.append(b)
            except Exception as exc:
                log.debug("Graph expansion skipped: %s", exc)

        if max_tokens is None:
            return ranked

        packed: list[Chunk] = []
        budget = max_tokens
        for chunk in ranked:
            tokens = chunk.token_count
            if tokens <= budget:
                packed.append(chunk)
                budget -= tokens
            elif chunk.compressed_content:
                compressed_tokens = max(1, int(len(chunk.compressed_content) / 3.3))
                if compressed_tokens <= budget:
                    packed.append(chunk)
                    budget -= compressed_tokens
        return packed

    @staticmethod
    def _apply_path_penalty(file_path: str, score: float) -> float:
        if file_path.startswith("git:"):
            return score
        fp_lower = file_path.lower()
        for marker in _DEPRIORITISED_PATHS:
            if marker in fp_lower:
                return score * 0.8
        return score

    def _estimate_keyword_distance(self, chunk, parsed) -> int:
        if parsed.file_hints:
            for hint in parsed.file_hints:
                if hint in chunk.file_path:
                    return 0
        for keyword in parsed.keywords:
            if keyword.lower() in chunk.content.lower():
                return 0
        return 2
