"""ANN top-k bypass family of ``SimilarityIndex`` (split from similarity.py, 2026-07).

Methods moved verbatim from similarity.py: _ensure_ann_index / top_k_similar / _stream_top_k (lines
1337-1469), routing through the EXISTING ``similarity_ann`` sibling module.

Manifested line (the ONLY non-verbatim edit, marked ``# decomposition:``):
the ``SIMILARITY_ANN_ENABLED`` read resolves through _svc() at call time
because test_similarity_ann.py patches it on the ``similarity`` module
object. ``similarity_ann.hnswlib_available`` / ``load_index`` /
``build_index`` stay module-qualified calls on the real sibling module so
patches on that module object keep landing.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

import similarity_ann


def _svc():
    """Resolve the patched ``SIMILARITY_ANN_ENABLED`` flag through the facade at call time.

    test_similarity_ann.py / tests/test_similarity_pins.py patch it on the
    ``similarity`` module object. Lazy import: the facade imports this module
    at its own import time, so a module-level ``import similarity`` here
    could observe a partially initialized module.
    """
    import similarity

    return similarity


class _TopKMixin:
    """hnswlib-accelerated (optional) top-k nearest with exact re-scoring."""

    # ------------------------------------------------------------------
    # Top-k nearest-neighbor bypass (v3.3.2 Phase 1, slice 4b)
    #
    # A SEPARATE entry point from the paginated exact search, for the workbench /
    # dedup "give me the k most similar" use case. It may use the optional
    # hnswlib ANN index to pick candidates sub-linearly at large scale, then
    # re-scores those candidates EXACTLY from the cache matrix (so similarities
    # are exact even though candidate selection is approximate). It deliberately
    # does NOT touch _search_ranked_candidates, so search_by_id's exact
    # total / has_more / allowed_ids contract is untouched.
    # ------------------------------------------------------------------

    def _ensure_ann_index(self, cache: Dict[str, Any]):
        """Return a live ANN index for the current signature, or None.

        Mirrors the vector-cache lifecycle: reuse if the signature matches, else
        load from disk, else build (and persist). Returns None when ANN is
        disabled or hnswlib is unavailable — callers fall back to exact ranking.
        """
        if not _svc().SIMILARITY_ANN_ENABLED or not similarity_ann.hnswlib_available():  # decomposition: patched on similarity
            return None
        signature = cache.get("signature")
        if signature is None:
            return None

        ann = self._ann
        if ann is not None and ann.signature == signature and ann.dim == cache["dim"]:
            return ann

        with self._ann_lock:
            ann = self._ann
            if ann is not None and ann.signature == signature and ann.dim == cache["dim"]:
                return ann
            index_dir = self._get_index_dir()
            loaded = similarity_ann.load_index(signature, index_dir)
            if loaded is not None and loaded.dim == cache["dim"]:
                self._ann = loaded
                return loaded
            built = similarity_ann.build_index(cache["matrix"], signature, index_dir, persist=True)
            self._ann = built
            return built

    def top_k_similar(
        self,
        query_emb: np.ndarray,
        k: int = 20,
        *,
        exclude_id: Optional[int] = None,
        allowed_ids: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to ``k`` nearest images (no threshold, no pagination).

        Uses the hnswlib ANN index when available and unscoped; otherwise ranks
        exactly over the in-memory cache (or the streaming scan when no cache is
        available). Similarities are always exact cosine; only candidate
        *selection* may be approximate. Results are ``{id, path, filename,
        similarity}`` ordered by similarity desc, id asc.
        """
        k = max(1, int(k))
        cache = self._ensure_vector_cache()
        if cache is None:
            return self._stream_top_k(query_emb, k, exclude_id=exclude_id, allowed_ids=allowed_ids)

        if int(query_emb.shape[0]) != cache["dim"]:
            return []
        query_norm = float(np.linalg.norm(query_emb))
        if query_norm == 0:
            return []
        query_unit = query_emb.astype(np.float32, copy=False) / query_norm

        matrix = cache["matrix"]
        ids = cache["ids"]
        total_rows = int(matrix.shape[0])

        candidate_rows = None
        # ANN accelerates only the unscoped case; scoped/exclude filtering is
        # applied exactly below regardless of how candidates were chosen.
        if allowed_ids is None:
            ann = self._ensure_ann_index(cache)
            if ann is not None:
                # Over-fetch candidates to lift recall; the exact re-rank below
                # then picks the true top-k among them. +1 leaves room to drop
                # the query id when excluded. (ann.query clamps to the count.)
                fetch = max(k * 4, k + 32)
                if exclude_id is not None:
                    fetch += 1
                rows = ann.query(query_unit, fetch)
                if rows is not None and rows.size:
                    candidate_rows = rows

        pool = candidate_rows if candidate_rows is not None else np.arange(total_rows)
        pool_sims = matrix[pool] @ query_unit

        ranked: List[Tuple[float, int, int]] = []
        for local_idx, row in enumerate(pool):
            rid = int(ids[row])
            if exclude_id is not None and rid == int(exclude_id):
                continue
            if allowed_ids is not None and rid not in allowed_ids:
                continue
            ranked.append((float(pool_sims[local_idx]), rid, int(row)))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        paths = cache["paths"]
        filenames = cache["filenames"]
        return [
            {
                "id": rid,
                "path": paths[row],
                "filename": filenames[row],
                "similarity": round(sim, 4),
            }
            for sim, rid, row in ranked[:k]
        ]

    def _stream_top_k(
        self,
        query_emb: np.ndarray,
        k: int,
        *,
        exclude_id: Optional[int] = None,
        allowed_ids: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Exact top-k via the streaming scan, for when no vector cache exists."""
        page, _total, _has_more = self._search_ranked_candidates(
            query_emb,
            threshold=-1.0,
            limit=k,
            offset=0,
            exclude_id=exclude_id,
            allowed_ids=allowed_ids,
        )
        return page
