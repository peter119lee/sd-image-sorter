"""Lifecycle write entry points for images (split from db_images_write.py).

``reconnect_image_source_path``, the deletions (``delete_images_by_ids`` /
``delete_images_by_paths`` / ``delete_image``),
``mark_pending_images_metadata_error``, ``update_image_path``, and the
``mark_image_unreadable`` / ``mark_image_unreadable_by_path`` /
``mark_image_readable`` transitions moved here verbatim in the 2026-07
db_images_write split. Consumers keep importing through the ``database``
facade (which re-exports these via ``db_images_write``); do not import this
module directly from feature code — ``db_images_write`` itself imports this
module at the end of its body, so a direct import that wins the race would
trip the managed import cycle and fail loudly.

The derived-state clear helpers stay in ``db_images_write``
(tests/test_derived_state_contract.py pins their writer statements to that
filename); the from-import below binds the SAME function object.

The unreadable transitions call ``_clear_image_pixel_caches``, NOT
``_clear_image_derived_state``: an unreachable file is not a changed file, so the
pixel-derived caches go and the tags stay. Deleting them here is unrecoverable
once a reconnect hands the row back.

Imports only from db_core / db_helpers / utils.source_paths /
utils.reported_cause / db_images_write / stdlib; it must not import from
``database``.
"""
import os
from datetime import datetime
from typing import Optional, List

from utils.reported_cause import normalize_reported_cause
from utils.source_paths import build_indexed_image_lookup_candidates
from db_core import (
    get_db,
    _invalidate_facet_caches,
    _invalidate_tags_cache,
)
from db_helpers import (
    _normalize_indexed_image_path,
    _path_query_match_clause,
)
from db_images_write import _clear_image_pixel_caches, _sync_image_path_identity


def reconnect_image_source_path(
    image_id: int,
    new_path: str,
    *,
    source_mtime_ns: Optional[int] = None,
    source_size: Optional[int] = None,
    source_file_mtime: Optional[datetime] = None,
) -> None:
    """Reconnect a missing library row to a found file path without clearing derived data."""
    normalized_path = _normalize_indexed_image_path(new_path)
    filename = os.path.basename(normalized_path)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE images
            SET path = ?,
                filename = ?,
                is_readable = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN 1
                    ELSE is_readable
                END,
                read_error = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN NULL
                    ELSE read_error
                END,
                metadata_status = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN 'complete'
                    ELSE COALESCE(metadata_status, 'complete')
                END,
                source_mtime_ns = COALESCE(?, source_mtime_ns),
                source_size = COALESCE(?, source_size),
                source_file_mtime = COALESCE(?, source_file_mtime),
                indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                normalized_path,
                filename,
                source_mtime_ns,
                source_size,
                source_file_mtime,
                image_id,
            ),
        )
        if cursor.rowcount:
            _sync_image_path_identity(cursor, image_id, normalized_path)
    _invalidate_tags_cache()

def delete_images_by_ids(image_ids: List[int]) -> int:
    """Delete many image rows in chunks and return the removed count."""
    if not image_ids:
        return 0

    removed = 0
    batch_size = 500

    with get_db() as conn:
        cursor = conn.cursor()
        for start in range(0, len(image_ids), batch_size):
            batch = image_ids[start:start + batch_size]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"DELETE FROM images WHERE id IN ({placeholders})",
                batch,
            )
            removed += cursor.rowcount or 0

    if removed:
        _invalidate_tags_cache()
        _invalidate_facet_caches()

    return removed

def delete_images_by_paths(paths: List[str]) -> int:
    """Delete image rows by absolute file path."""
    if not paths:
        return 0

    removed = 0
    # Conservative path batch size: each path can expand to several lookup
    # candidates inside _path_query_match_clause, so keep batches small to
    # stay well under SQLite's 999 bound-variable limit per statement.
    batch_size = 100

    with get_db() as conn:
        cursor = conn.cursor()
        for start in range(0, len(paths), batch_size):
            path_batch = paths[start:start + batch_size]
            clause, params = _path_query_match_clause(path_batch)
            if not clause:
                continue
            cursor.execute(f"DELETE FROM images WHERE {clause}", params)
            removed += cursor.rowcount or 0

    if removed:
        _invalidate_tags_cache()
        _invalidate_facet_caches()

    return removed

def mark_pending_images_metadata_error(image_ids: List[int], read_error: str) -> int:
    """Mark pending metadata rows as errored without changing derived state."""
    normalized_ids = [int(image_id) for image_id in image_ids if image_id]
    if not normalized_ids:
        return 0

    placeholders = ",".join("?" for _ in normalized_ids)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE images
            SET is_readable = 0,
                metadata_status = 'error',
                read_error = ?,
                indexed_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
              AND LOWER(COALESCE(metadata_status, '')) = 'pending'
            """,
            [read_error, *normalized_ids],
        )
        return int(cursor.rowcount or 0)

def update_image_path(image_id: int, new_path: str):
    """Update the path of an image after a successful move.

    Gallery requests can race with a move between the filesystem rename and
    this database update. If that stale read marked the old path as missing,
    the successful move must restore the row so the image does not disappear.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        normalized_path = _normalize_indexed_image_path(new_path)
        new_filename = os.path.basename(normalized_path)
        cursor.execute(
            """
            UPDATE images
            SET path = ?,
                filename = ?,
                is_readable = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN 1
                    ELSE is_readable
                END,
                read_error = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN NULL
                    ELSE read_error
                END,
                metadata_status = CASE
                    WHEN TRIM(COALESCE(read_error, '')) = ''
                         OR LOWER(COALESCE(read_error, '')) LIKE '%not found%'
                         OR LOWER(COALESCE(read_error, '')) LIKE '%missing%'
                    THEN 'complete'
                    ELSE COALESCE(metadata_status, 'complete')
                END,
                indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (normalized_path, new_filename, image_id)
        )
        if cursor.rowcount:
            _sync_image_path_identity(cursor, image_id, normalized_path)

def _stored_unreadable_cause(read_error: Optional[str]) -> Optional[str]:
    """Keep SQL NULL as NULL; otherwise the same user-facing cause scan/move store.

    ``describe_readability_failure`` would turn empty/None into
    ``"Unreadable image"``. Callers that pass None or ``""`` must keep today's
    column shape, so this is ``normalize_reported_cause`` with None preserved.
    """
    if read_error is None:
        return None
    return normalize_reported_cause(read_error)

def mark_image_unreadable(image_id: int, read_error: Optional[str]) -> None:
    """Mark an indexed image as unreadable so normal workflows exclude it.

    Clears the pixel caches only. This runs on the "file not found" path — and
    ``ImageService._filter_and_mark_missing_images`` reaches it from an ordinary
    gallery listing — so an unplugged drive plus one page load used to delete
    every tag on that drive with no prompt and no notice. Nothing about the
    image changed; we just cannot see it, and ``reconnect_image_source_path``
    can hand the row back at any time.
    """
    stored_cause = _stored_unreadable_cause(read_error)
    with get_db() as conn:
        cursor = conn.cursor()
        _clear_image_pixel_caches(cursor, image_id)
        cursor.execute(
            """
            UPDATE images
            SET is_readable = 0,
                read_error = ?,
                metadata_status = 'error',
                indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stored_cause, image_id),
        )
    _invalidate_tags_cache()

def mark_image_unreadable_by_path(path: str, read_error: Optional[str]) -> None:
    """Mark an existing image row as unreadable based on its file path.

    Same rule as ``mark_image_unreadable``: pixel caches only, because being
    unreachable is not the same fact as having changed.
    """
    candidates = build_indexed_image_lookup_candidates(path)
    if not candidates:
        return

    with get_db() as conn:
        cursor = conn.cursor()
        clause, params = _path_query_match_clause(candidates)
        last_image_id = 0
        batch_size = 500
        while True:
            rows = cursor.execute(
                f"""
                SELECT id
                FROM images
                WHERE ({clause}) AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                [*params, last_image_id, batch_size],
            ).fetchall()
            if not rows:
                break
            for row in rows:
                _clear_image_pixel_caches(cursor, row["id"])
            last_image_id = int(rows[-1]["id"])
        cursor.execute(
            f"""
            UPDATE images
            SET is_readable = 0,
                read_error = ?,
                metadata_status = 'error',
                indexed_at = CURRENT_TIMESTAMP
            WHERE {clause}
            """,
            [_stored_unreadable_cause(read_error), *params],
        )
    _invalidate_tags_cache()

def mark_image_readable(image_id: int) -> None:
    """Restore an image row to readable state after a successful re-parse."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE images
            SET is_readable = 1,
                read_error = NULL,
                metadata_status = 'complete',
                indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (image_id,),
        )

def delete_image(image_id: int):
    """Delete an image from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images WHERE id = ?", (image_id,))
    _invalidate_tags_cache()
    _invalidate_facet_caches()
