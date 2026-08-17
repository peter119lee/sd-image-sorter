"""Near-duplicate GROUP scanning for the cleanup workflow (v3.5.0 Tier 1).

The old ``find_duplicates`` endpoint returns ranked PAIRS, is synchronous,
and refuses libraries above DUPLICATE_SYNC_MAX_EMBEDDINGS (Debt-17). The
cleanup workflow needs the whole library organized into GROUPS with a
suggested keeper, so users can review side-by-side and clear the rest.

This service runs as a bulk background job (progress + cancel via
``bulk_job_service``):

1. Load every CLIP embedding (id + vector) and L2-normalize.
2. Discover candidates via the optional hnswlib ANN and collision-checked
   fingerprints for identical embeddings, or use an exact chunked fallback.
3. Re-score ANN candidates exactly in bounded blocks, then partition them into
   direct-neighbor groups. Every suggested loser must have a direct edge to the
   keeper that remains; transitive chains are never destructive suggestions.
4. Enrich members with metadata and rank each group: highest
   (user_rating, aesthetic_score, resolution, file_size) first — that
   member becomes the suggested keeper.
5. Persist the full result to ``<state>/duplicate-groups.json`` so the
   review UI can page through it and survive restarts.

Deletion is deliberately NOT implemented here — the frontend feeds the
checked ids to the existing trash-backed delete pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeAlias

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.95
MIN_THRESHOLD = 0.80
MAX_THRESHOLD = 0.999
_ANN_NEIGHBOR_K = 24          # neighbors fetched per item on the ANN path
_MATMUL_CHUNK = 512           # rows per chunk on the exact path
_EMBEDDING_FINGERPRINT_BYTES = 16
_GROUP_CANCEL_CHECK_INTERVAL = 256
# v3: the summary gained the coverage fields. A v2 result cannot say how much
# of the library its verdict covered, so it is discarded and rescanned rather
# than shown as a "0 duplicates" answer nobody can vouch for.
_RESULT_VERSION = 3
_STATE_FILENAME = "duplicate-groups.json"

_SCAN_LOCK = threading.Lock()
_ACTIVE_JOB_ID: Optional[str] = None

SimilarityPair: TypeAlias = tuple[int, int, float]
NeighborMap: TypeAlias = Dict[int, Dict[int, float]]
DirectNeighborGroup: TypeAlias = tuple[List[int], float]
ExactDirectMatches: TypeAlias = tuple[List[int], Optional[float]]
CancellationCheck: TypeAlias = Callable[[], bool]
FingerprintFunction: TypeAlias = Callable[[np.ndarray], bytes]


class DuplicateGroupPersistenceError(RuntimeError):
    """Raised when the completed duplicate scan cannot be stored atomically."""


def _state_path() -> Path:
    import config

    return Path(config.get_state_dir()) / _STATE_FILENAME


# ---------------------------------------------------------------------------
# Scan registration (one at a time)
# ---------------------------------------------------------------------------

def get_active_job_id() -> Optional[str]:
    with _SCAN_LOCK:
        return _ACTIVE_JOB_ID


def claim_active_job_id(job_id: str) -> bool:
    """Claim the scan slot unless its current registry owner is inactive."""
    from services.bulk_job_service import TERMINAL_STATUSES, get_bulk_job_service

    global _ACTIVE_JOB_ID
    with _SCAN_LOCK:
        if _ACTIVE_JOB_ID is not None:
            current = get_bulk_job_service().get_job(_ACTIVE_JOB_ID)
            if current is not None and current["status"] not in TERMINAL_STATUSES:
                return False
        _ACTIVE_JOB_ID = job_id
        return True


def release_active_job_id(job_id: str) -> bool:
    """Release the scan slot only when ``job_id`` still owns it."""
    global _ACTIVE_JOB_ID
    with _SCAN_LOCK:
        if _ACTIVE_JOB_ID != job_id:
            return False
        _ACTIVE_JOB_ID = None
        return True


# ---------------------------------------------------------------------------
# Scan worker
# ---------------------------------------------------------------------------

def _load_embeddings(handle) -> tuple:
    """Return (ids, normalized_matrix) for all readable embedded images."""
    import database as db
    from similarity import bytes_to_embedding

    handle.set_progress(processed=0, total=100, message="正在读取嵌入向量 / Loading embeddings")
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, embedding FROM images
            WHERE embedding IS NOT NULL AND COALESCE(is_readable, 1) = 1
            ORDER BY id
            """
        ).fetchall()
    ids = [int(r[0]) for r in rows]
    if len(ids) < 2:
        return ids, None
    vectors = np.stack([bytes_to_embedding(r[1]) for r in rows]).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return ids, vectors / norms


def _coverage_summary(embedded_count: int) -> Dict[str, Any]:
    """Describe how much of the library the scan was actually able to compare.

    A scan only sees images that already have a CLIP embedding, so "0
    duplicate groups" over a partly-embedded library is not a statement about
    the library. Field names mirror SimilarityService.get_stats(); ``exact``
    is the same partial-result flag the gallery count endpoint uses, and is
    False whenever readable images were left out of the comparison.
    """
    import database as db

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM images WHERE COALESCE(is_readable, 1) = 1"
        ).fetchone()
    total = int(row[0] or 0) if row else 0
    pending = max(0, total - embedded_count)
    return {
        "total_images": total,
        "pending_count": pending,
        "coverage": round(embedded_count / total * 100, 1) if total > 0 else 0,
        "exact": pending == 0,
    }


def _embedding_fingerprint(vector: np.ndarray) -> bytes:
    contiguous = np.ascontiguousarray(vector)
    payload = memoryview(contiguous).cast("B")
    return hashlib.blake2b(
        payload,
        digest_size=_EMBEDDING_FINGERPRINT_BYTES,
    ).digest()


def _find_identical_candidate_indices(
    matrix: np.ndarray,
    fingerprint: FingerprintFunction,
    is_cancelled: CancellationCheck,
) -> Optional[set[int]]:
    """Find repeated rows while verifying every fingerprint hit exactly."""
    primary_representatives: Dict[bytes, int] = {}
    alternate_representatives: Dict[bytes, List[int]] = {}
    candidate_indices: set[int] = set()

    for index in range(matrix.shape[0]):
        if (
            index % _GROUP_CANCEL_CHECK_INTERVAL == 0
            and is_cancelled()
        ):
            return None
        digest = fingerprint(matrix[index])
        primary = primary_representatives.get(digest)
        if primary is None:
            primary_representatives[digest] = index
            continue
        if np.array_equal(matrix[index], matrix[primary]):
            candidate_indices.add(primary)
            candidate_indices.add(index)
            continue

        alternates = alternate_representatives.get(digest)
        matched_alternate: Optional[int] = None
        if alternates is not None:
            for alternate in alternates:
                if np.array_equal(matrix[index], matrix[alternate]):
                    matched_alternate = alternate
                    break
        if matched_alternate is not None:
            candidate_indices.add(matched_alternate)
            candidate_indices.add(index)
        elif alternates is None:
            alternate_representatives[digest] = [index]
        else:
            alternates.append(index)

    return None if is_cancelled() else candidate_indices


def _find_ann_candidate_indices(
    matrix: np.ndarray,
    threshold: float,
    ann,
    handle,
) -> Optional[List[int]]:
    """Return image indices whose initial ANN window contains a direct match."""
    node_count = matrix.shape[0]
    query_size = min(_ANN_NEIGHBOR_K, node_count)
    candidate_indices: set[int] = set()
    for index in range(node_count):
        if index % 512 == 0:
            if handle.cancelled:
                return None
            handle.set_progress(
                processed=20 + int(50 * index / max(1, node_count)),
                total=100,
                message=f"Searching neighbors {index}/{node_count}",
            )
        labels = ann.query(matrix[index], query_size)
        if labels is None:
            return None
        similarities = matrix[labels] @ matrix[index]
        for neighbor, similarity in zip(labels.tolist(), similarities.tolist()):
            if neighbor == index or similarity < threshold:
                continue
            candidate_indices.add(index)
            candidate_indices.add(int(neighbor))
    return None if handle.cancelled else sorted(candidate_indices)


def _pairs_exact(
    matrix: np.ndarray, threshold: float, handle
) -> List[SimilarityPair]:
    """Exact upper-triangle chunked matmul — no size cap, streamed."""
    n = matrix.shape[0]
    pairs: List[SimilarityPair] = []
    total_chunks = (n + _MATMUL_CHUNK - 1) // _MATMUL_CHUNK
    done_chunks = 0
    for i in range(0, n, _MATMUL_CHUNK):
        if handle.cancelled:
            return pairs
        chunk = matrix[i:i + _MATMUL_CHUNK]
        sims = chunk @ matrix[i:].T  # only columns >= i (upper triangle)
        rows_idx, cols_idx = np.nonzero(sims >= threshold)
        for ci, cj in zip(rows_idx.tolist(), cols_idx.tolist()):
            a, b = i + ci, i + cj
            if a < b:
                pairs.append((a, b, float(sims[ci, cj])))
        done_chunks += 1
        handle.set_progress(
            processed=20 + int(60 * done_chunks / max(1, total_chunks)), total=100,
            message=f"Comparing block {done_chunks}/{total_chunks}",
        )
    return pairs


def _rank_key(member: Dict[str, Any]) -> tuple:
    return (
        member.get("user_rating") or 0,
        member.get("aesthetic_score") or 0.0,
        (member.get("width") or 0) * (member.get("height") or 0),
        member.get("file_size") or 0,
        -(member.get("id") or 0),  # stable: older image wins final ties
    )


def _load_metadata(
    member_ids: List[int],
    handle,
) -> Optional[Dict[int, Dict[str, Any]]]:
    import database as db

    metadata: Dict[int, Dict[str, Any]] = {}
    with db.get_db() as conn:
        for start in range(0, len(member_ids), 900):
            if handle.cancelled:
                return None
            batch = member_ids[start:start + 900]
            placeholders = ",".join("?" * len(batch))
            for row in conn.execute(
                f"""
                SELECT id, path, filename, width, height, file_size,
                       aesthetic_score, user_rating
                FROM images WHERE id IN ({placeholders})
                """,
                batch,
            ).fetchall():
                metadata[int(row[0])] = {
                    "id": int(row[0]),
                    "path": row[1],
                    "filename": row[2],
                    "width": row[3],
                    "height": row[4],
                    "file_size": row[5],
                    "aesthetic_score": row[6],
                    "user_rating": row[7],
                }
    return metadata


def _build_neighbor_map(pairs: List[SimilarityPair]) -> NeighborMap:
    neighbors: NeighborMap = {}
    for first, second, similarity in pairs:
        if first == second:
            continue
        first_neighbors = neighbors.setdefault(first, {})
        second_neighbors = neighbors.setdefault(second, {})
        first_neighbors[second] = max(first_neighbors.get(second, 0.0), similarity)
        second_neighbors[first] = max(second_neighbors.get(first, 0.0), similarity)
    return neighbors


def _partition_direct_neighbor_groups(
    ids: List[int],
    neighbors: NeighborMap,
    metadata: Dict[int, Dict[str, Any]],
    is_cancelled: CancellationCheck,
) -> Optional[List[DirectNeighborGroup]]:
    """Return disjoint keeper-first groups backed by direct similarity edges."""
    remaining = {
        index
        for index in neighbors
        if 0 <= index < len(ids) and ids[index] in metadata
    }
    ranked = sorted(
        remaining,
        key=lambda index: _rank_key(metadata[ids[index]]),
        reverse=True,
    )
    rank_positions = {index: position for position, index in enumerate(ranked)}
    groups: List[DirectNeighborGroup] = []

    for position, keeper in enumerate(ranked):
        if position % _GROUP_CANCEL_CHECK_INTERVAL == 0 and is_cancelled():
            return None
        if keeper not in remaining:
            continue
        direct_losers = sorted(
            (
                neighbor
                for neighbor in neighbors.get(keeper, {})
                if neighbor != keeper and neighbor in remaining
            ),
            key=rank_positions.__getitem__,
        )
        remaining.remove(keeper)
        if not direct_losers:
            continue

        remaining.difference_update(direct_losers)
        minimum_similarity = min(
            neighbors[keeper][loser] for loser in direct_losers
        )
        groups.append(([keeper, *direct_losers], minimum_similarity))

    return None if is_cancelled() else groups


def _find_exact_direct_matches(
    keeper: int,
    matrix: np.ndarray,
    ranked_candidates: List[int],
    remaining: set[int],
    threshold: float,
    is_cancelled: CancellationCheck,
) -> Optional[ExactDirectMatches]:
    """Exactly score one keeper against current candidates in bounded blocks."""
    current_candidates = [
        candidate
        for candidate in ranked_candidates
        if candidate != keeper and candidate in remaining
    ]
    direct_matches: List[int] = []
    minimum_similarity: Optional[float] = None

    for start in range(0, len(current_candidates), _MATMUL_CHUNK):
        if is_cancelled():
            return None
        candidate_block = current_candidates[start:start + _MATMUL_CHUNK]
        block_indices = np.asarray(candidate_block, dtype=np.intp)
        similarities = matrix[block_indices] @ matrix[keeper]
        matching_offsets = np.flatnonzero(similarities >= threshold)
        for offset in matching_offsets.tolist():
            direct_matches.append(candidate_block[offset])
            similarity = float(similarities[offset])
            minimum_similarity = (
                similarity
                if minimum_similarity is None
                else min(minimum_similarity, similarity)
            )

    if is_cancelled():
        return None
    return direct_matches, minimum_similarity


def _partition_ann_direct_neighbor_groups(
    ids: List[int],
    matrix: np.ndarray,
    candidate_indices: List[int],
    metadata: Dict[int, Dict[str, Any]],
    threshold: float,
    handle,
) -> Optional[List[DirectNeighborGroup]]:
    """Build keeper-first groups from exact scores without dense pair state."""
    remaining = {
        index
        for index in candidate_indices
        if 0 <= index < len(ids) and ids[index] in metadata
    }
    ranked = sorted(
        remaining,
        key=lambda index: _rank_key(metadata[ids[index]]),
        reverse=True,
    )
    groups: List[DirectNeighborGroup] = []

    def is_cancelled() -> bool:
        return bool(handle.cancelled)

    for position, keeper in enumerate(ranked):
        if position % _GROUP_CANCEL_CHECK_INTERVAL == 0:
            if handle.cancelled:
                return None
            handle.set_progress(
                processed=70 + int(15 * position / max(1, len(ranked))),
                total=100,
                message=f"Grouping matches {position}/{len(ranked)}",
            )
        if keeper not in remaining:
            continue
        exact_matches = _find_exact_direct_matches(
            keeper,
            matrix,
            ranked,
            remaining,
            threshold,
            is_cancelled,
        )
        if exact_matches is None:
            return None
        direct_losers, minimum_similarity = exact_matches
        remaining.remove(keeper)
        if not direct_losers:
            continue
        remaining.difference_update(direct_losers)
        if minimum_similarity is None:
            raise RuntimeError(
                "Duplicate candidate scoring returned matches without similarity"
            )
        groups.append((
            [keeper, *direct_losers],
            minimum_similarity,
        ))

    return None if handle.cancelled else groups


def _format_groups(
    ids: List[int],
    partitioned: List[DirectNeighborGroup],
    metadata: Dict[int, Dict[str, Any]],
    handle,
) -> Optional[List[Dict[str, Any]]]:
    groups: List[Dict[str, Any]] = []
    for group_index, (member_indices, minimum_similarity) in enumerate(partitioned):
        if group_index % _GROUP_CANCEL_CHECK_INTERVAL == 0 and handle.cancelled:
            return None
        entries = [dict(metadata[ids[index]]) for index in member_indices]
        for position, entry in enumerate(entries):
            entry["suggested_keep"] = position == 0
        groups.append({
            "similarity": round(minimum_similarity, 4),
            "members": entries,
        })

    if handle.cancelled:
        return None
    groups.sort(key=lambda group: (
        len(group["members"]),
        group["similarity"],
    ), reverse=True)
    for group_index, group in enumerate(groups):
        group["group_id"] = group_index
    return groups


def _groups_via_ann(
    ids: List[int],
    matrix: np.ndarray,
    threshold: float,
    handle,
) -> Optional[List[Dict[str, Any]]]:
    """Group ANN candidates using collision-safe dense-cluster recovery."""
    import similarity_ann

    if not similarity_ann.hnswlib_available():
        return None
    node_count = matrix.shape[0]
    handle.set_progress(processed=20, total=100, message="Building neighbor index")
    ann = similarity_ann.build_index(
        matrix,
        (node_count, node_count),
        None,
        persist=False,
    )
    if ann is None or handle.cancelled:
        return None

    def is_cancelled() -> bool:
        return bool(handle.cancelled)

    identical_candidate_indices = _find_identical_candidate_indices(
        matrix,
        _embedding_fingerprint,
        is_cancelled,
    )
    if identical_candidate_indices is None:
        return None
    ann_candidate_indices = _find_ann_candidate_indices(
        matrix,
        threshold,
        ann,
        handle,
    )
    if ann_candidate_indices is None:
        return None
    candidate_indices = sorted(
        identical_candidate_indices.union(ann_candidate_indices)
    )
    if not candidate_indices:
        return []
    member_ids = [ids[index] for index in candidate_indices]
    metadata = _load_metadata(member_ids, handle)
    if metadata is None:
        return None
    partitioned = _partition_ann_direct_neighbor_groups(
        ids,
        matrix,
        candidate_indices,
        metadata,
        threshold,
        handle,
    )
    if partitioned is None:
        return None
    return _format_groups(ids, partitioned, metadata, handle)


def _build_groups(
    ids: List[int], pairs: List[SimilarityPair], handle
) -> Optional[List[Dict[str, Any]]]:
    handle.set_progress(processed=85, total=100, message="Clustering groups")
    if handle.cancelled:
        return None
    neighbors = _build_neighbor_map(pairs)
    member_ids = [
        ids[index]
        for index in neighbors
        if 0 <= index < len(ids)
    ]
    if not member_ids:
        return []
    metadata = _load_metadata(member_ids, handle)
    if metadata is None:
        return None

    def is_cancelled() -> bool:
        return bool(handle.cancelled)

    partitioned = _partition_direct_neighbor_groups(
        ids,
        neighbors,
        metadata,
        is_cancelled,
    )
    if partitioned is None:
        return None
    return _format_groups(ids, partitioned, metadata, handle)


def run_duplicate_scan(handle, threshold: float = DEFAULT_THRESHOLD) -> None:
    """Bulk-job worker: scan the whole library into duplicate groups."""
    threshold = max(MIN_THRESHOLD, min(MAX_THRESHOLD, float(threshold)))
    ids, matrix = _load_embeddings(handle)
    if matrix is None:
        if handle.cancelled:
            return
        result = _empty_result(threshold, embedded_count=len(ids))
        _commit_result(handle, result)
        return
    if handle.cancelled:
        return

    groups = _groups_via_ann(ids, matrix, threshold, handle)
    if groups is None and not handle.cancelled:
        handle.set_progress(processed=20, total=100, message="Searching neighbors")
        pairs = _pairs_exact(matrix, threshold, handle)
        if handle.cancelled:
            return
        groups = _build_groups(ids, pairs, handle)
    if groups is None or handle.cancelled:
        return
    redundant = sum(len(g["members"]) - 1 for g in groups)
    reclaimable = sum(
        m.get("file_size") or 0
        for g in groups for m in g["members"] if not m["suggested_keep"]
    )
    result = {
        "version": _RESULT_VERSION,
        "scanned_at": time.time(),
        "threshold": threshold,
        "summary": {
            "embedded_count": len(ids),
            "group_count": len(groups),
            "redundant_count": redundant,
            "reclaimable_bytes": int(reclaimable),
            "threshold": threshold,
            **_coverage_summary(len(ids)),
        },
        "groups": groups,
    }
    if handle.cancelled:
        return
    _commit_result(handle, result)


def _empty_result(threshold: float, embedded_count: int) -> Dict[str, Any]:
    return {
        "version": _RESULT_VERSION,
        "scanned_at": time.time(),
        "threshold": threshold,
        "summary": {
            "embedded_count": embedded_count,
            "group_count": 0,
            "redundant_count": 0,
            "reclaimable_bytes": 0,
            "threshold": threshold,
            **_coverage_summary(embedded_count),
        },
        "groups": [],
    }


# ---------------------------------------------------------------------------
# Persisted results
# ---------------------------------------------------------------------------

def _cleanup_temp_file(temp_path: Path, context: str) -> None:
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        raise DuplicateGroupPersistenceError(
            "Failed to clean duplicate-group temp file "
            f"{temp_path} after {context}: {exc}"
        ) from exc


def _prepare_result_file(result: Dict[str, Any]) -> tuple[Path, Path]:
    path = _state_path()
    temp_path = path.with_suffix(".tmp")
    try:
        payload = json.dumps(result, separators=(",", ":"))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(payload, encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        try:
            _cleanup_temp_file(temp_path, "result preparation failure")
        except DuplicateGroupPersistenceError as cleanup_exc:
            raise DuplicateGroupPersistenceError(
                f"Failed to prepare duplicate groups for {path}: {exc}; "
                f"cleanup also failed: {cleanup_exc}"
            ) from cleanup_exc
        raise DuplicateGroupPersistenceError(
            f"Failed to prepare duplicate groups for {path}: {exc}"
        ) from exc
    return path, temp_path


def _commit_result(handle, result: Dict[str, Any]) -> bool:
    path, temp_path = _prepare_result_file(result)

    def publish() -> None:
        os.replace(temp_path, path)

    try:
        committed = handle.commit_result(
            publish_callback=publish,
            result={"summary": result["summary"]},
            processed=100,
            total=100,
            message="Done",
        )
    except OSError as exc:
        try:
            _cleanup_temp_file(temp_path, "result publish failure")
        except DuplicateGroupPersistenceError as cleanup_exc:
            raise DuplicateGroupPersistenceError(
                f"Failed to publish duplicate groups to {path}: {exc}; "
                f"cleanup also failed: {cleanup_exc}"
            ) from cleanup_exc
        raise DuplicateGroupPersistenceError(
            f"Failed to publish duplicate groups to {path}: {exc}"
        ) from exc
    except Exception as exc:
        try:
            _cleanup_temp_file(temp_path, "result commit failure")
        except DuplicateGroupPersistenceError as cleanup_exc:
            raise DuplicateGroupPersistenceError(
                f"Failed to commit duplicate groups for {path}: {exc}; "
                f"cleanup also failed: {cleanup_exc}"
            ) from cleanup_exc
        raise

    if not committed:
        _cleanup_temp_file(temp_path, "cancelled result commit")
    return committed


def load_result() -> Optional[Dict[str, Any]]:
    path = _state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != _RESULT_VERSION
            or not isinstance(data.get("groups"), list)
        ):
            return None
        return data
    except Exception as exc:
        logger.warning("Failed to load duplicate groups state: %s", exc)
        return None


def get_groups_page(offset: int = 0, limit: int = 50) -> Dict[str, Any]:
    """Page through the last persisted scan (group-level pagination)."""
    data = load_result()
    if data is None:
        return {
            "available": False,
            "summary": None,
            "groups": [],
            "total_groups": 0,
            "offset": 0,
            "limit": limit,
            "has_more": False,
        }
    groups = data.get("groups") or []
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    page = groups[offset:offset + limit]
    return {
        "available": True,
        "scanned_at": data.get("scanned_at"),
        "threshold": data.get("threshold"),
        "summary": data.get("summary"),
        "groups": page,
        "total_groups": len(groups),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(groups),
    }
