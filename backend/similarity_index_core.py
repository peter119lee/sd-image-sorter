"""Search/ranking method family of ``SimilarityIndex`` (split from similarity.py, 2026-07).

Methods moved verbatim from similarity.py: the progress trio get_progress / _record_issue / request_cancel
(lines 360-384) and the search/ranking core search_by_id / search_by_upload /
search_by_text / find_duplicates / _normalize_similarity_window /
_rank_candidate_rows / _iter_embedding_candidate_chunks /
_search_ranked_candidates / _stream_ranked_candidates (lines 585-992).
``__init__`` and ``embed_batch`` stay on the facade class body (report
section 5 pattern 1: the shared ``_embed_lock`` and the pinned-by-value
``backend_file=__file__`` anchor live there).

Manifested lines (the ONLY non-verbatim edits, marked ``# decomposition:``):
every read of a name the suites patch on the ``similarity`` module object --
``bytes_to_embedding`` / ``embed_image_pil`` / ``embed_text`` and the mutable
constants DUPLICATE_SYNC_MAX_EMBEDDINGS / DUPLICATE_CHUNK_SIZE /
SIMILARITY_SEARCH_MAX_WINDOW / SIMILARITY_SEARCH_CHUNK_SIZE -- resolves
through _svc() at call time; a from-import here would freeze independent
bindings those patches silently miss. The exception
classes are imported from their defining module ``similarity_errors``
(identity is preserved by the facade re-export; they are raise sites, not
patch surfaces), and the def-time default constants come from ``config``
exactly as the original module bound them. The
logger keeps the historical "similarity" channel.
"""

import heapq
import io
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import (
    SIMILARITY_DEFAULT_LIMIT,
    SIMILARITY_DEFAULT_THRESHOLD,
    DUPLICATE_THRESHOLD,
)
from similarity_errors import (
    SimilarityImageNotFoundError,
    SimilarityEmbeddingMissingError,
    SimilarityInvalidImageError,
    SimilarityInsufficientEmbeddingsError,
    SimilarityDuplicateSearchTooLargeError,
    SimilaritySearchWindowTooLargeError,
)

# NOTE(decomposition): keep the historical logger channel so log routing and
# output stay identical to the pre-split single-file module.
logger = logging.getLogger("similarity")


def _svc():
    """Resolve patched seam names / mutable constants through the facade at call time.

    The suites patch these on the ``similarity`` module object
    (tests/test_similarity_pins.py, test_resource_safety.py,
    test_semantic_text_search.py, test_routers). Lazy import: the facade
    imports this module at its own import time, so a module-level
    ``import similarity`` here could observe a partially initialized module.
    """
    import similarity

    return similarity


class _IndexCoreMixin:
    """Search entry points, duplicate scan and streaming ranking core."""

    def get_progress(self) -> Dict[str, Any]:
        """Get current embedding progress (snapshot guarded by lock)."""
        with self._progress_lock:
            snapshot = dict(self._progress)
            snapshot["recent_issues"] = list(self._progress["recent_issues"])
            return snapshot

    def _record_issue(self, kind: str, image_id: int, image_path: str, reason: str) -> None:
        entry = {
            "kind": kind,
            "image_id": image_id,
            "filename": os.path.basename(image_path),
            "path": image_path,
            "reason": reason,
        }
        with self._progress_lock:
            issues = self._progress["recent_issues"]
            issues.append(entry)
            self._progress["recent_issues"] = issues[-10:]

    def request_cancel(self) -> bool:
        if not self._progress.get("running"):
            return False
        self._cancel_requested = True
        return True

    def search_by_id(
        self,
        image_id: int,
        limit: int = SIMILARITY_DEFAULT_LIMIT,
        threshold: float = SIMILARITY_DEFAULT_THRESHOLD,
        offset: int = 0,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """Find images similar to a given image ID.

        When ``allowed_ids`` is provided, results are restricted to that set of
        image ids (e.g. a collection or Favorites). ``None`` searches the whole
        library — the long-standing default behavior.
        """
        with self.db.get_db() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT id, embedding FROM images WHERE id = ?", (image_id,))
            row = cursor.fetchone()
            if not row:
                raise SimilarityImageNotFoundError(image_id)
            if not row[1]:
                raise SimilarityEmbeddingMissingError(image_id)

            query_emb = _svc().bytes_to_embedding(row[1])  # decomposition: facade-resolved

        page_limit = max(1, int(limit))
        page_offset = max(0, int(offset))
        page, total, has_more = self._search_ranked_candidates(
            query_emb,
            threshold,
            page_limit,
            page_offset,
            exclude_id=image_id,
            allowed_ids=allowed_ids,
        )
        return {
            "results": page,
            "total": total,
            "has_more": has_more,
            "offset": page_offset,
            "limit": page_limit,
        }

    def search_by_upload(
        self,
        image_data: bytes,
        limit: int = SIMILARITY_DEFAULT_LIMIT,
        threshold: float = SIMILARITY_DEFAULT_THRESHOLD,
        offset: int = 0,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """Find images similar to an uploaded image.

        When ``allowed_ids`` is provided, results are restricted to that set of
        image ids (e.g. a collection or Favorites). ``None`` searches the whole
        library — the long-standing default behavior.
        """
        with io.BytesIO(image_data) as image_buffer:
            try:
                pil_image = Image.open(image_buffer)
            except (UnidentifiedImageError, OSError) as exc:
                raise SimilarityInvalidImageError() from exc

            try:
                try:
                    pil_image.load()
                except (UnidentifiedImageError, OSError) as exc:
                    raise SimilarityInvalidImageError() from exc
                query_emb = _svc().embed_image_pil(pil_image)  # decomposition: patched on similarity
            finally:
                pil_image.close()

        if query_emb is None:
            page_limit = max(1, int(limit))
            page_offset = max(0, int(offset))
            return {
                "results": [],
                "total": 0,
                "has_more": False,
                "offset": page_offset,
                "limit": page_limit,
            }

        page_limit = max(1, int(limit))
        page_offset = max(0, int(offset))
        page, total, has_more = self._search_ranked_candidates(
            query_emb,
            threshold,
            page_limit,
            page_offset,
            allowed_ids=allowed_ids,
        )
        return {
            "results": page,
            "total": total,
            "has_more": has_more,
            "offset": page_offset,
            "limit": page_limit,
        }

    def search_by_text(
        self,
        query: str,
        limit: int = SIMILARITY_DEFAULT_LIMIT,
        threshold: float = 0.0,
        offset: int = 0,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """Semantic search: rank library images against a natural-language
        query via CLIP text-image cosine.

        Cross-modal scores run FAR lower than image-image ones (matching
        pairs typically land around 0.2-0.35 for ViT-B/32), which is why the
        default threshold is 0.0 — pure top-k ranking — instead of the 0.5
        image-search cutoff. Reuses the exact paginated ranking path the
        upload search uses.
        """
        query_emb = _svc().embed_text(query)  # decomposition: patched on similarity
        page_limit = max(1, int(limit))
        page_offset = max(0, int(offset))
        if query_emb is None:
            return {
                "results": [],
                "total": 0,
                "has_more": False,
                "offset": page_offset,
                "limit": page_limit,
            }
        page, total, has_more = self._search_ranked_candidates(
            query_emb,
            threshold,
            page_limit,
            page_offset,
            allowed_ids=allowed_ids,
        )
        return {
            "results": page,
            "total": total,
            "has_more": has_more,
            "offset": page_offset,
            "limit": page_limit,
        }

    def find_duplicates(
        self,
        threshold: float = DUPLICATE_THRESHOLD,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Find near-duplicate image pairs above similarity threshold."""
        with self.db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM images
                WHERE embedding IS NOT NULL
                  AND COALESCE(is_readable, 1) = 1
                """
            )
            embedded_count = int(cursor.fetchone()[0] or 0)

            if embedded_count < 2:
                raise SimilarityInsufficientEmbeddingsError(embedded_count)

            if embedded_count > _svc().DUPLICATE_SYNC_MAX_EMBEDDINGS:  # decomposition: patched on similarity
                raise SimilarityDuplicateSearchTooLargeError(
                    embedded_count,
                    _svc().DUPLICATE_SYNC_MAX_EMBEDDINGS,  # decomposition: patched on similarity
                )

            cursor.execute(
                """
                SELECT id, path, filename, embedding
                FROM images
                WHERE embedding IS NOT NULL
                  AND COALESCE(is_readable, 1) = 1
                """
            )
            rows = cursor.fetchall()

        # Build embedding matrix
        ids = [r[0] for r in rows]
        paths = [r[1] for r in rows]
        filenames = [r[2] for r in rows]
        embeddings = np.array([_svc().bytes_to_embedding(r[3]) for r in rows])  # decomposition: facade-resolved

        # Normalize for efficient cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = embeddings / norms

        # Find the globally best duplicates above threshold using a bounded heap.
        # This avoids the old "stop when limit is reached" behavior that could
        # silently hide better matches later in the scan.
        total_matches = 0
        top_matches_heap: List[Tuple[float, int, int, Dict[str, Any]]] = []
        page_limit = max(1, limit)
        page_offset = max(0, offset)
        keep_count = page_limit + page_offset + 1
        chunk_size = _svc().DUPLICATE_CHUNK_SIZE  # decomposition: patched on similarity

        for i in range(0, len(rows), chunk_size):
            chunk = normalized[i:i + chunk_size]
            # Compare chunk against all embeddings after it
            for j in range(i, len(rows), chunk_size):
                other = normalized[j:j + chunk_size]
                sim_matrix = chunk @ other.T

                for ci in range(sim_matrix.shape[0]):
                    for cj in range(sim_matrix.shape[1]):
                        actual_i = i + ci
                        actual_j = j + cj
                        if actual_i >= actual_j:
                            continue  # Skip self and already-compared pairs

                        sim = float(sim_matrix[ci, cj])
                        if sim >= threshold:
                            total_matches += 1
                            pair = {
                                "image_a": {
                                    "id": ids[actual_i],
                                    "path": paths[actual_i],
                                    "filename": filenames[actual_i],
                                },
                                "image_b": {
                                    "id": ids[actual_j],
                                    "path": paths[actual_j],
                                    "filename": filenames[actual_j],
                                },
                                "similarity": round(sim, 4),
                            }

                            if len(top_matches_heap) < keep_count:
                                heapq.heappush(top_matches_heap, (sim, ids[actual_i], ids[actual_j], pair))
                            elif sim > top_matches_heap[0][0]:
                                heapq.heapreplace(top_matches_heap, (sim, ids[actual_i], ids[actual_j], pair))

        ranked_duplicates = [
            pair for _similarity, _id_a, _id_b, pair in sorted(
                top_matches_heap,
                key=lambda item: item[0],
                reverse=True,
            )
        ]
        page = ranked_duplicates[page_offset:page_offset + page_limit]
        has_more = total_matches > (page_offset + len(page))
        return {
            "duplicates": page,
            "total": total_matches,
            "has_more": has_more,
            "offset": page_offset,
            "limit": page_limit,
            "threshold": threshold,
        }

    def _normalize_similarity_window(self, limit: int, offset: int) -> Tuple[int, int, int]:
        page_limit = max(1, int(limit))
        page_offset = max(0, int(offset))
        keep_count = page_limit + page_offset + 1
        if keep_count > _svc().SIMILARITY_SEARCH_MAX_WINDOW:  # decomposition: patched on similarity
            raise SimilaritySearchWindowTooLargeError(keep_count, _svc().SIMILARITY_SEARCH_MAX_WINDOW)  # decomposition: patched on similarity
        return page_limit, page_offset, keep_count

    def _rank_candidate_rows(
        self,
        query_emb: np.ndarray,
        candidates: list,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Rank one bounded chunk of candidate images by similarity."""
        if not candidates:
            return []

        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return []

        valid_rows = []
        embeddings = []
        for candidate in candidates:
            embedding = _svc().bytes_to_embedding(candidate[3])  # decomposition: facade-resolved
            if embedding.shape != query_emb.shape:
                logger.warning(
                    "Skipping similarity candidate %s because embedding shape %s does not match query shape %s",
                    candidate[0],
                    embedding.shape,
                    query_emb.shape,
                )
                continue
            valid_rows.append(candidate)
            embeddings.append(embedding)

        if not embeddings:
            return []

        candidate_embs = np.vstack(embeddings).astype(np.float32, copy=False)
        candidate_norms = np.linalg.norm(candidate_embs, axis=1)
        candidate_norms[candidate_norms == 0] = 1
        similarities = candidate_embs @ query_emb / (candidate_norms * query_norm)

        results = []
        for idx, sim in enumerate(similarities):
            if sim >= threshold:
                row = valid_rows[idx]
                results.append({
                    "id": row[0],
                    "path": row[1],
                    "filename": row[2],
                    "similarity": round(float(sim), 4),
                })
        return results

    def _iter_embedding_candidate_chunks(self, exclude_id: Optional[int] = None):
        """Yield readable embedding rows in DB-sized chunks without materializing all rows."""
        with self.db.get_db() as conn:
            cursor = conn.cursor()
            params: Tuple[Any, ...] = ()
            exclude_clause = ""
            if exclude_id is not None:
                exclude_clause = "AND id != ?"
                params = (exclude_id,)

            cursor.execute(
                f"""
                SELECT id, path, filename, embedding
                FROM images
                WHERE embedding IS NOT NULL
                  AND COALESCE(is_readable, 1) = 1
                  {exclude_clause}
                ORDER BY id
                """,
                params,
            )
            while True:
                rows = cursor.fetchmany(_svc().SIMILARITY_SEARCH_CHUNK_SIZE)  # decomposition: patched on similarity
                if not rows:
                    break
                yield rows

    def _search_ranked_candidates(
        self,
        query_emb: np.ndarray,
        threshold: float,
        limit: int,
        offset: int,
        *,
        exclude_id: Optional[int] = None,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """Rank candidates, preferring the in-memory vectorized cache.

        Validates the pagination window once, then tries the cached matmul path.
        If the cache is unavailable (disabled, not buildable, dimension mismatch,
        or any failure) it transparently falls back to the streaming heap scan so
        results are identical and never capped.

        When ``allowed_ids`` is set, both paths restrict candidates to that id set
        (collection / Favorites scope) before threshold filtering and pagination,
        so ``total`` / ``has_more`` reflect the scoped result set.
        """
        page_limit, page_offset, keep_count = self._normalize_similarity_window(limit, offset)

        cached = self._try_cached_ranked_candidates(
            query_emb, threshold, page_limit, page_offset,
            exclude_id=exclude_id, allowed_ids=allowed_ids,
        )
        if cached is not None:
            return cached

        return self._stream_ranked_candidates(
            query_emb, threshold, page_limit, page_offset, keep_count,
            exclude_id=exclude_id, allowed_ids=allowed_ids,
        )

    def _stream_ranked_candidates(
        self,
        query_emb: np.ndarray,
        threshold: float,
        page_limit: int,
        page_offset: int,
        keep_count: int,
        *,
        exclude_id: Optional[int] = None,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """Stream candidates through a bounded heap to avoid loading every embedding at once.

        When ``allowed_ids`` is set, candidates outside that id set are skipped so
        ``total`` counts only scoped matches, keeping pagination/``has_more`` correct.
        """
        total = 0
        top_heap: List[Tuple[float, int, Dict[str, Any]]] = []

        for chunk in self._iter_embedding_candidate_chunks(exclude_id=exclude_id):
            for result in self._rank_candidate_rows(query_emb, chunk, threshold):
                if allowed_ids is not None and int(result["id"]) not in allowed_ids:
                    continue
                total += 1
                item = (float(result["similarity"]), -int(result["id"]), result)
                if len(top_heap) < keep_count:
                    heapq.heappush(top_heap, item)
                elif item[:2] > top_heap[0][:2]:
                    heapq.heapreplace(top_heap, item)

        ranked = [
            result for _similarity, _negative_id, result in sorted(
                top_heap,
                key=lambda item: item[:2],
                reverse=True,
            )
        ]
        page = ranked[page_offset:page_offset + page_limit]
        has_more = total > (page_offset + len(page))
        return page, total, has_more
