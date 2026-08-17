"""Vector-cache persistence family of ``SimilarityIndex`` (split from similarity.py, 2026-07).

Methods moved verbatim from similarity.py: invalidate_vector_cache plus the on-disk persistence block --
_get_index_dir / _persist_vector_cache / _load_persisted_vector_cache /
_delete_persisted_vector_cache / _compute_embedding_signature /
_ensure_vector_cache / _build_vector_cache / _try_cached_ranked_candidates
and the _PERSIST_* class constants (lines 994-1335).

Manifested lines (the ONLY non-verbatim edits, marked ``# decomposition:``):
``_get_index_dir`` resolves ``get_state_dir`` through _svc() because
conftest.py:66 autouse-patches ``similarity.get_state_dir`` for the WHOLE
suite (the split-killer; a ``from config import
get_state_dir`` here would leak persistence into the user's real STATE_DIR);
the ``SIMILARITY_VECTOR_CACHE_ENABLED`` and ``bytes_to_embedding`` reads are
facade-resolved for the same reason. ``similarity_ann.delete_index`` stays a
module-qualified call on the real sibling module so patches on that module
object keep landing. The logger keeps the
historical "similarity" channel.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

import similarity_ann

# NOTE(decomposition): keep the historical logger channel so log routing and
# output stay identical to the pre-split single-file module.
logger = logging.getLogger("similarity")


def _svc():
    """Resolve patched seam names through the facade at call time.

    conftest.py:66 autouse-patches ``similarity.get_state_dir`` suite-wide and
    the cache suites patch ``similarity.SIMILARITY_VECTOR_CACHE_ENABLED``.
    Lazy import: the facade imports this module at its own import time, so a
    module-level ``import similarity`` here could observe a partially
    initialized module.
    """
    import similarity

    return similarity


class _VectorCacheMixin:
    """In-RAM vectorized cache + exact on-disk persistence (PERF-1 / v3.3.2)."""

    # ------------------------------------------------------------------
    # Vectorized in-memory cache (PERF-1)
    # ------------------------------------------------------------------

    def invalidate_vector_cache(self) -> None:
        """Drop the cached embedding matrix so the next search rebuilds it."""
        with self._vector_cache_lock:
            self._vector_cache = None
            self._delete_persisted_vector_cache()
        # The ANN index is derived from the same vectors — invalidate it too.
        with self._ann_lock:
            self._ann = None
        similarity_ann.delete_index(self._get_index_dir())

    # ------------------------------------------------------------------
    # On-disk persistence of the exact vector cache (v3.3.2 Phase 1)
    #
    # Persisting the built matrix to STATE_DIR/similarity-index/ lets a cold
    # start (or the first search after a restart) skip re-reading and
    # re-normalizing every embedding BLOB from SQLite. It stays EXACT — the
    # same normalized matrix the in-RAM path uses, just loaded from disk — so
    # results never change. Keyed on the (count, max_id) signature: any row
    # add/delete invalidates by mismatch; re-embeds (same signature, new
    # vectors) are invalidated explicitly via invalidate_vector_cache().
    # All disk I/O is best-effort: any failure silently falls back to rebuilding
    # in RAM, so persistence can never break or cap search.
    # ------------------------------------------------------------------

    _PERSIST_MATRIX_NAME = "matrix.npy"
    _PERSIST_IDS_NAME = "ids.npy"
    _PERSIST_META_NAME = "meta.json"

    def _get_index_dir(self) -> Path:
        return Path(_svc().get_state_dir()) / "similarity-index"  # decomposition: split-killer seam (conftest.py:66)

    def _persist_vector_cache(self, cache: Dict[str, Any]) -> None:
        """Best-effort write of the normalized matrix + parallel arrays to disk."""
        try:
            index_dir = self._get_index_dir()
            index_dir.mkdir(parents=True, exist_ok=True)
            tmp_matrix = index_dir / (self._PERSIST_MATRIX_NAME + ".tmp")
            tmp_ids = index_dir / (self._PERSIST_IDS_NAME + ".tmp")
            tmp_meta = index_dir / (self._PERSIST_META_NAME + ".tmp")

            # Save to a file handle so np.save does not append a second ".npy".
            with open(tmp_matrix, "wb") as handle:
                np.save(handle, np.ascontiguousarray(cache["matrix"], dtype=np.float32))
            with open(tmp_ids, "wb") as handle:
                np.save(handle, np.asarray(cache["ids"], dtype=np.int64))
            meta = {
                "dim": int(cache["dim"]),
                "signature": [int(cache["signature"][0]), int(cache["signature"][1])],
                "paths": list(cache["paths"]),
                "filenames": list(cache["filenames"]),
            }
            tmp_meta.write_text(json.dumps(meta), encoding="utf-8")

            os.replace(tmp_matrix, index_dir / self._PERSIST_MATRIX_NAME)
            os.replace(tmp_ids, index_dir / self._PERSIST_IDS_NAME)
            os.replace(tmp_meta, index_dir / self._PERSIST_META_NAME)
            logger.debug(
                "[Similarity] Persisted vector cache (%s vectors) to %s",
                len(cache["paths"]),
                index_dir,
            )
        except Exception as exc:
            logger.debug("[Similarity] Could not persist vector cache: %s", exc)
            # A partial write is worse than none — clear it so we never load junk.
            self._delete_persisted_vector_cache()

    def _load_persisted_vector_cache(self, signature: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        """Load a persisted cache iff it matches the signature and is self-consistent.

        Returns None (caller rebuilds) when the files are missing, the stored
        signature differs, or any shape/parse check fails. The signature gate makes
        a stale on-disk cache from a different library state simply ignored.
        """
        try:
            index_dir = self._get_index_dir()
            meta_path = index_dir / self._PERSIST_META_NAME
            matrix_path = index_dir / self._PERSIST_MATRIX_NAME
            ids_path = index_dir / self._PERSIST_IDS_NAME
            if not (meta_path.exists() and matrix_path.exists() and ids_path.exists()):
                return None

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            stored_sig = meta.get("signature")
            if (
                not isinstance(stored_sig, list)
                or len(stored_sig) != 2
                or (int(stored_sig[0]), int(stored_sig[1])) != signature
            ):
                return None

            matrix = np.load(matrix_path)
            ids = np.load(ids_path)
            paths = meta.get("paths") or []
            filenames = meta.get("filenames") or []
            dim = int(meta.get("dim", 0))
            rows = int(matrix.shape[0]) if matrix.ndim == 2 else -1
            # Self-consistency: the parallel arrays must all describe the same rows.
            # (rows may be < signature count when build skipped odd-dim embeddings.)
            if not (
                matrix.ndim == 2
                and dim > 0
                and matrix.shape[1] == dim
                and int(ids.shape[0]) == rows
                and len(paths) == rows
                and len(filenames) == rows
            ):
                logger.debug("[Similarity] Persisted cache shape mismatch; ignoring.")
                return None

            return {
                "matrix": np.ascontiguousarray(matrix, dtype=np.float32),
                "ids": np.asarray(ids, dtype=np.int64),
                "paths": list(paths),
                "filenames": list(filenames),
                "dim": dim,
                "signature": signature,
            }
        except Exception as exc:
            logger.debug("[Similarity] Could not load persisted vector cache: %s", exc)
            return None

    def _delete_persisted_vector_cache(self) -> None:
        """Best-effort removal of any persisted cache files (incl. temp writes)."""
        try:
            index_dir = self._get_index_dir()
            for name in (
                self._PERSIST_MATRIX_NAME,
                self._PERSIST_IDS_NAME,
                self._PERSIST_META_NAME,
                self._PERSIST_MATRIX_NAME + ".tmp",
                self._PERSIST_IDS_NAME + ".tmp",
                self._PERSIST_META_NAME + ".tmp",
            ):
                target = index_dir / name
                if target.exists():
                    target.unlink()
        except Exception as exc:
            logger.debug("[Similarity] Could not delete persisted vector cache: %s", exc)

    def _compute_embedding_signature(self) -> Optional[Tuple[int, int]]:
        """Cheap (count, max_id) fingerprint of readable embeddings.

        Returns None when the signature cannot be determined (e.g. a minimal DB
        mock that doesn't answer the aggregate query) — the caller then skips the
        cache and uses the streaming scan.
        """
        try:
            with self.db.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT COUNT(*), COALESCE(MAX(id), 0)
                    FROM images
                    WHERE embedding IS NOT NULL
                      AND COALESCE(is_readable, 1) = 1
                    """
                )
                row = cursor.fetchone()
        except Exception as exc:
            logger.debug("[Similarity] Could not compute embedding signature: %s", exc)
            return None

        if not row or len(row) < 2 or row[0] is None:
            return None
        try:
            return (int(row[0]), int(row[1]))
        except (TypeError, ValueError):
            return None

    def _ensure_vector_cache(self) -> Optional[Dict[str, Any]]:
        """Return a fresh embedding cache, building it if missing or stale.

        Returns None (caller falls back to streaming) when the cache is disabled,
        the DB signature is undeterminable, there are no embeddings, or building
        the matrix fails (including MemoryError on very large libraries).
        """
        if not _svc().SIMILARITY_VECTOR_CACHE_ENABLED:  # decomposition: patched on similarity
            return None

        signature = self._compute_embedding_signature()
        if signature is None or signature[0] == 0:
            return None

        cache = self._vector_cache
        if cache is not None and cache.get("signature") == signature:
            return cache

        with self._vector_cache_lock:
            cache = self._vector_cache
            if cache is not None and cache.get("signature") == signature:
                return cache

            # Prefer a persisted matrix (exact, just deserialized) over re-reading
            # and re-normalizing every embedding BLOB from SQLite.
            loaded = self._load_persisted_vector_cache(signature)
            if loaded is not None:
                self._vector_cache = loaded
                return loaded

            try:
                built = self._build_vector_cache(signature)
            except MemoryError:
                logger.warning(
                    "[Similarity] Not enough memory to build vectorized cache "
                    "(%s embeddings); falling back to streaming search.",
                    signature[0],
                )
                self._vector_cache = None
                return None
            except Exception as exc:
                logger.warning("[Similarity] Failed to build vectorized cache: %s", exc)
                self._vector_cache = None
                return None
            self._vector_cache = built
            if built is not None:
                self._persist_vector_cache(built)
            return built

    def _build_vector_cache(self, signature: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        """Materialize an L2-normalized matrix + parallel id/path/filename arrays.

        Streams rows via fetchmany (never fetchall) so memory stays bounded during
        the build. Rows whose embedding dimension differs from the modal dimension
        are skipped, matching the streaming path's per-row shape guard.
        """
        ids: List[int] = []
        paths: List[str] = []
        filenames: List[str] = []
        vectors: List[np.ndarray] = []
        dim: Optional[int] = None
        skipped = 0

        for chunk in self._iter_embedding_candidate_chunks(exclude_id=None):
            for row in chunk:
                embedding = _svc().bytes_to_embedding(row[3])  # decomposition: facade-resolved
                if dim is None:
                    dim = int(embedding.shape[0])
                if embedding.shape[0] != dim:
                    skipped += 1
                    continue
                ids.append(int(row[0]))
                paths.append(row[1])
                filenames.append(row[2])
                vectors.append(embedding)

        if not vectors or dim is None:
            return None

        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        matrix = matrix / norms[:, None]

        if skipped:
            logger.info(
                "[Similarity] Vector cache skipped %s embedding(s) with mismatched dimension.",
                skipped,
            )

        return {
            "matrix": matrix,
            "ids": np.asarray(ids, dtype=np.int64),
            "paths": paths,
            "filenames": filenames,
            "dim": dim,
            "signature": signature,
        }

    def _try_cached_ranked_candidates(
        self,
        query_emb: np.ndarray,
        threshold: float,
        page_limit: int,
        page_offset: int,
        *,
        exclude_id: Optional[int] = None,
        allowed_ids: Optional[Set[int]] = None,
    ) -> Optional[Tuple[List[Dict[str, Any]], int, bool]]:
        """Vectorized ranking over the cached matrix.

        Returns None to signal the caller should fall back to streaming. Result
        ordering matches _stream_ranked_candidates exactly: filter on the raw
        cosine (>= threshold), sort by the 4-decimal-rounded similarity descending
        with ascending id as the tie-break.

        When ``allowed_ids`` is set, a boolean membership mask over ``cache["ids"]``
        is folded into the threshold/exclude mask before sort + pagination, so the
        scoped result matches the streaming path exactly.
        """
        cache = self._ensure_vector_cache()
        if cache is None:
            return None
        if int(query_emb.shape[0]) != cache["dim"]:
            # Dimension mismatch — streaming handles per-row shape skipping.
            return None

        query_norm = float(np.linalg.norm(query_emb))
        if query_norm == 0:
            return [], 0, False

        query_unit = (query_emb.astype(np.float32, copy=False)) / query_norm
        sims = cache["matrix"] @ query_unit  # cosine, matrix rows are unit vectors

        ids = cache["ids"]
        mask = sims >= threshold
        if exclude_id is not None:
            mask &= ids != int(exclude_id)
        if allowed_ids is not None:
            # Restrict to the scoped id set (collection / Favorites) before ranking.
            allowed_arr = np.fromiter(allowed_ids, dtype=np.int64, count=len(allowed_ids))
            mask &= np.isin(ids, allowed_arr)

        valid_idx = np.nonzero(mask)[0]
        total = int(valid_idx.size)
        if total == 0:
            return [], 0, False

        sub_rounded = np.round(sims[valid_idx], 4)
        sub_ids = ids[valid_idx]
        # lexsort sorts by the LAST key first: primary = -rounded (asc => rounded
        # desc), tie-break = id ascending.
        order = np.lexsort((sub_ids, -sub_rounded))
        ordered_idx = valid_idx[order]

        page_idx = ordered_idx[page_offset:page_offset + page_limit]
        paths = cache["paths"]
        filenames = cache["filenames"]
        page = [
            {
                "id": int(ids[i]),
                "path": paths[i],
                "filename": filenames[i],
                "similarity": round(float(sims[i]), 4),
            }
            for i in page_idx
        ]
        has_more = total > (page_offset + len(page))
        return page, total, has_more
