"""Scan-ingest write entry points for images (split from db_images_write.py).

``add_image``, ``_get_existing_images_by_paths``, ``add_images_batch``, and
``get_image_scan_state_by_paths`` moved here verbatim in the 2026-07
db_images_write split. Consumers keep importing through the ``database``
facade (which re-exports these via ``db_images_write``); do not import this
module directly from feature code — ``db_images_write`` itself imports this
module at the end of its body, so a direct import that wins the race would
trip the managed import cycle and fail loudly.

The upsert engine ``_upsert_image_record`` stays in ``db_images_write``
(tests/test_derived_state_contract.py pins its writer statement to that
filename); the from-import below binds the SAME function object, so
``add_image`` / ``add_images_batch`` keep calling it as a module-local bare
name — insulated from ``database`` facade patches
(TestInternalCallChainInsulation).

Imports only from db_core / db_helpers / utils.source_paths /
db_images_write / stdlib; it must not import from ``database``.
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple, Union

from utils.source_paths import (
    build_indexed_image_lookup_candidates,
    indexed_image_path_match_key,
)
from db_core import (
    get_db,
    _invalidate_facet_caches,
    _invalidate_tags_cache,
)
from db_helpers import (
    _normalize_indexed_image_path,
    _path_query_match_clause,
    normalize_checkpoint_name,
    _row_to_dict,
)
from db_images_write import _upsert_image_record


def add_image(
    path: str,
    filename: str,
    generator: str = "unknown",
    prompt: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    metadata_json: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    file_size: Optional[int] = None,
    checkpoint: Optional[str] = None,
    loras: Optional[List[str]] = None,
    created_at: Optional[datetime] = None,
    library_order_time: Optional[datetime] = None,
    source_file_mtime: Optional[datetime] = None,
    model_hash: Optional[str] = None,
    is_readable: bool = True,
    read_error: Optional[str] = None,
    source_mtime_ns: Optional[int] = None,
    source_size: Optional[int] = None,
    metadata_status: str = "complete",
    content_fingerprint: Optional[str] = None,
    raw_metadata_gz: Optional[bytes] = None,
    sidecar_caption: Optional[str] = None,
    return_status: bool = False,
) -> Union[int, Tuple[int, str]]:
    """Add an image to the database.

    Returns the image ID by default. When ``return_status`` is True, returns
    ``(image_id, "new" | "updated")`` so callers can report truthful scan
    summaries without duplicating the upsert logic.
    """
    resolved_library_order_time = library_order_time or created_at
    resolved_source_file_mtime = source_file_mtime or created_at
    record = {
        "path": path,
        "filename": filename,
        "generator": generator,
        "prompt": prompt,
        "sidecar_caption": sidecar_caption,
        "negative_prompt": negative_prompt,
        "metadata_json": metadata_json,
        "width": width,
        "height": height,
        "file_size": file_size,
        "checkpoint": checkpoint,
        "checkpoint_normalized": normalize_checkpoint_name(checkpoint),
        "loras": loras,
        "library_order_time": resolved_library_order_time,
        "source_file_mtime": resolved_source_file_mtime,
        "created_at": resolved_library_order_time,
        "model_hash": model_hash,
        "is_readable": is_readable,
        "read_error": read_error,
        "source_mtime_ns": source_mtime_ns,
        "source_size": source_size,
        "metadata_status": metadata_status,
        "content_fingerprint": content_fingerprint,
        "raw_metadata_gz": raw_metadata_gz,
    }

    with get_db() as conn:
        cursor = conn.cursor()
        image_id, write_status = _upsert_image_record(cursor, record)
        _invalidate_tags_cache()
        _invalidate_facet_caches()

        if return_status:
            return image_id, write_status
        return image_id

def _get_existing_images_by_paths(
    cursor: sqlite3.Cursor,
    paths: List[str],
) -> Dict[str, sqlite3.Row]:
    """Fetch existing image rows keyed by normalized indexed path."""
    normalized_paths = [_normalize_indexed_image_path(path) for path in paths if path]
    if not normalized_paths:
        return {}

    requested_candidates = {
        path: build_indexed_image_lookup_candidates(path)
        for path in normalized_paths
    }
    existing_rows: Dict[str, sqlite3.Row] = {}
    chunk_size = 100
    for start in range(0, len(normalized_paths), chunk_size):
        chunk_paths = normalized_paths[start:start + chunk_size]
        query_clause, query_params = _path_query_match_clause(chunk_paths)
        if not query_clause:
            continue
        cursor.execute(
            f"""
                    SELECT id, path, filename, generator, prompt, sidecar_caption, negative_prompt, metadata_json,
                   width, height, file_size, checkpoint, checkpoint_normalized, loras, model_hash,
                   library_order_time, source_file_mtime, created_at,
                   is_readable, read_error, source_mtime_ns, source_size, metadata_status,
                   content_fingerprint, tagged_at, ai_caption, aesthetic_score,
                   COALESCE(library_id, 'main') AS library_id,
                   CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END AS has_embedding,
                   EXISTS(SELECT 1 FROM artist_predictions ap WHERE ap.image_id = images.id) AS has_artist_predictions
            FROM images
            WHERE {query_clause}
            """,
            query_params,
        )
        for row in cursor.fetchall():
            existing_rows[row["path"]] = row

    existing: Dict[str, sqlite3.Row] = {}
    rows_by_match_key = {
        indexed_image_path_match_key(path): row
        for path, row in existing_rows.items()
    }
    for requested_path, candidates in requested_candidates.items():
        for candidate in candidates:
            row = existing_rows.get(candidate)
            if not row:
                row = rows_by_match_key.get(indexed_image_path_match_key(candidate))
            if row:
                existing[requested_path] = row
                break

    return existing

def add_images_batch(image_records: List[Dict[str, Any]], return_statuses: bool = False) -> Dict[str, Any]:
    """Insert or update many images in a single transaction."""
    if not image_records:
        empty_result: Dict[str, Any] = {
            "new": 0,
            "updated": 0,
            "skipped_other_library": 0,
        }
        if return_statuses:
            empty_result["statuses"] = {}
        return empty_result

    normalized_records = []
    for record in image_records:
        normalized = dict(record)
        normalized["path"] = _normalize_indexed_image_path(record["path"])
        normalized_records.append(normalized)

    with get_db() as conn:
        cursor = conn.cursor()
        existing_by_path = _get_existing_images_by_paths(
            cursor,
            [record["path"] for record in normalized_records],
        )
        counts = {"new": 0, "updated": 0, "skipped_other_library": 0}
        statuses: Dict[str, str] = {}

        for record in normalized_records:
            _image_id, status = _upsert_image_record(
                cursor,
                record,
                existing_row=existing_by_path.get(record["path"]),
            )
            counts[status] = int(counts.get(status, 0)) + 1
            statuses[record["path"]] = status

        _invalidate_tags_cache()
        _invalidate_facet_caches()
        if return_statuses:
            return {
                **counts,
                "statuses": statuses,
            }
        return counts

def get_image_scan_state_by_paths(paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch lightweight row state used by folder scan optimizations."""
    if not paths:
        return {}

    with get_db() as conn:
        cursor = conn.cursor()
        rows = _get_existing_images_by_paths(cursor, paths)
        return {
            path: _row_to_dict(row)
            for path, row in rows.items()
        }

