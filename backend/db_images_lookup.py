"""Light image lookups (split from db_images_read.py).

The folder-scope / library-folder / reconnect-candidate reads and the
single-image, by-ids, untagged, and id-chunk readers moved here verbatim in
the 2026-07 db_images_read split. ``get_missing_image_reconnect_candidates``
keeps its function-scoped lazy import and passes ``backend_file=__file__``;
only the dirname is consumed and this module still lives at backend/ depth,
so path resolution is unchanged. Consumers keep importing through the
``database`` facade; do not import this module directly from feature code.

Imports only from db_core / db_helpers / db_query / utils / stdlib to avoid
an import cycle with the ``database`` facade.
"""
from typing import Optional, List, Dict, Any, Iterator

from db_core import (
    get_db,
)
from db_helpers import (
    MISSING_TEXT_SQL,
    NO_PROMPT_SQL,
    _path_query_match_clause,
    _folder_scope_query_match_clause,
    _row_to_dict,
    _rows_to_dicts,
)
from db_query import (
    _IMAGE_COLUMNS_BARE,
    _RECONNECT_CANDIDATE_COLUMNS,
)
from utils.source_paths import (
    build_indexed_image_lookup_candidates,
    indexed_image_path_match_key,
    is_indexed_image_path_in_folder_scope,
)


def get_images_in_folder_scope(folder_path: str, recursive: bool = True) -> List[Dict[str, Any]]:
    """Return lightweight image rows that fall under a scan root."""
    clause, params = _folder_scope_query_match_clause(folder_path)
    if not clause:
        return []

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, path, filename
            FROM images
            WHERE {clause}
            """,
            params,
        )
        rows = _rows_to_dicts(cursor.fetchall())

    if recursive:
        return rows

    return [
        row for row in rows
        if is_indexed_image_path_in_folder_scope(row["path"], folder_path, recursive=False)
    ]


def count_prompt_coverage_in_folder_scope(
    folder_path: str, recursive: bool = True
) -> Dict[str, int]:
    """Count indexed images under a scan root and how much text they stored.

    Lets a finished scan report the shortfall with its denominator instead of
    only "N images indexed". Uses the same ``COALESCE(is_readable, 1)`` guard
    as the rest of the codebase so legacy rows are not silently excluded.

    Two shortfalls, because since migration 042 they are different questions and
    only one of them is a problem:

    * ``missing_prompt`` — no SD generation prompt. A statistic: an image the
      user drew, downloaded or photographed has none and never will.
    * ``missing_text`` — no prompt AND no sidecar caption. This is the set the
      metadata recovery job can still change, so it is the only one a scan
      summary may point the user at. The predicate is shared with that job
      (``db_helpers.MISSING_TEXT_SQL``) so the two figures cannot disagree.
    """
    clause, params = _folder_scope_query_match_clause(folder_path)
    if not clause:
        return {"total": 0, "missing_prompt": 0, "missing_text": 0}

    with get_db() as conn:
        cursor = conn.cursor()
        # The scope clause is a top-level OR chain, so it MUST be parenthesized
        # before anything is ANDed onto it — otherwise the readable guard binds
        # to the last alternative only and unreadable rows land in the shortfall.
        cursor.execute(
            f"""
            SELECT path,
                   CASE WHEN {NO_PROMPT_SQL} THEN 1 ELSE 0 END,
                   CASE WHEN {MISSING_TEXT_SQL} THEN 1 ELSE 0 END
            FROM images
            WHERE ({clause}) AND COALESCE(is_readable, 1) = 1
            """,
            params,
        )
        rows = cursor.fetchall()

    if not recursive:
        rows = [
            row for row in rows
            if is_indexed_image_path_in_folder_scope(row[0], folder_path, recursive=False)
        ]

    return {
        "total": len(rows),
        "missing_prompt": sum(int(row[1] or 0) for row in rows),
        "missing_text": sum(int(row[2] or 0) for row in rows),
    }


def get_library_folders() -> List[str]:
    """Return the distinct directories that contain (readable) indexed images.

    v3.3.2 Library Navigation. Paths are normalized to forward slashes so the
    frontend can build a folder tree by splitting on "/". Each entry is the
    immediate parent directory of one or more images; intermediate ancestor
    folders are synthesized client-side. Recomputed per call (cheap dirname
    derivation) so the tree stays fresh right after a scan.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT path FROM images WHERE COALESCE(is_readable, 1) != 0"
        ).fetchall()

    folders: set[str] = set()
    for row in rows:
        normalized = str(row["path"] or "").replace("\\", "/")
        slash = normalized.rfind("/")
        if slash > 0:
            folders.add(normalized[:slash])
    return sorted(folders)


def get_missing_image_reconnect_candidates(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return image rows whose stored source path no longer resolves on disk."""
    from utils.source_paths import resolve_existing_indexed_image_path

    query = f"SELECT {_RECONNECT_CANDIDATE_COLUMNS} FROM images ORDER BY id"
    params: List[Any] = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(max(0, int(limit)))

    candidates: List[Dict[str, Any]] = []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in _rows_to_dicts(rows):
                source_path = row.get("path") or ""
                resolved_path = resolve_existing_indexed_image_path(source_path, backend_file=__file__)
                if resolved_path:
                    continue
                candidates.append(row)

    return candidates


def get_image_by_id(image_id: int) -> Optional[Dict[str, Any]]:
    """Get a single image by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_IMAGE_COLUMNS_BARE} FROM images WHERE id = ?",
            (image_id,),
        )
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None


def get_images_missing_color_data(limit: int = 100) -> List[Dict[str, Any]]:
    """Find images that haven't had color analysis run yet (for lazy backfill)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, path FROM images
            WHERE avg_brightness IS NULL AND is_readable = 1
            LIMIT ?
            """,
            (limit,),
        )
        return [{"id": row[0], "path": row[1]} for row in cursor.fetchall()]


def count_images_missing_color_data() -> int:
    """Count images still needing color analysis. Uses indexed column; constant memory."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM images WHERE avg_brightness IS NULL AND is_readable = 1"
        )
        row = cursor.fetchone()
        return int(row[0] if row else 0)


def get_image_by_path(path: str) -> Optional[Dict[str, Any]]:
    """Get a single image by any equivalent indexed path representation."""
    if not path:
        return None

    candidates = build_indexed_image_lookup_candidates(path)
    if not candidates:
        return None

    with get_db() as conn:
        cursor = conn.cursor()
        clause, params = _path_query_match_clause(candidates)
        cursor.execute(
            f"SELECT {_IMAGE_COLUMNS_BARE} FROM images WHERE {clause}",
            params,
        )
        rows = cursor.fetchall()

    rows_by_path = {row["path"]: row for row in rows}
    rows_by_match_key = {
        indexed_image_path_match_key(row["path"]): row
        for row in rows
    }
    for candidate in candidates:
        row = rows_by_path.get(candidate)
        if not row:
            row = rows_by_match_key.get(indexed_image_path_match_key(candidate))
        if row:
            return _row_to_dict(row)
    return None


def get_images_by_ids(image_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Get multiple images by IDs in a single query (avoids N+1).

    Chunks into batches of 500 to stay under SQLite's 999-variable limit.

    Args:
        image_ids: List of image IDs to fetch

    Returns:
        Dictionary mapping image_id -> image data
    """
    if not image_ids:
        return {}

    result: Dict[int, Dict[str, Any]] = {}
    batch_size = 500

    with get_db() as conn:
        cursor = conn.cursor()
        for i in range(0, len(image_ids), batch_size):
            batch = image_ids[i:i + batch_size]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"SELECT {_IMAGE_COLUMNS_BARE}, content_fingerprint FROM images WHERE id IN ({placeholders})",
                batch
            )
            for row in cursor.fetchall():
                result[row['id']] = _row_to_dict(row)

    return result


def get_untagged_images(limit: int = 100) -> List[Dict[str, Any]]:
    """Get images that haven't been tagged yet."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_IMAGE_COLUMNS_BARE} FROM images WHERE tagged_at IS NULL AND COALESCE(is_readable, 1) = 1 LIMIT ?",
            (limit,)
        )
        return _rows_to_dicts(cursor.fetchall())


def get_all_image_ids() -> List[int]:
    """Return all image IDs (lightweight — no row data loaded).

    Used by the tagging pipeline to avoid loading all image rows into
    memory at once. Callers fetch full rows in small batches.
    """
    image_ids: List[int] = []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM images WHERE COALESCE(is_readable, 1) = 1 ORDER BY id")
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            image_ids.extend(int(row[0]) for row in rows)
    return image_ids


def get_untagged_image_ids() -> List[int]:
    """Return IDs of images that have not been tagged yet.

    Lightweight counterpart to get_untagged_images(); callers fetch
    full rows in small batches to avoid OOM on large libraries.
    """
    image_ids: List[int] = []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM images WHERE tagged_at IS NULL AND COALESCE(is_readable, 1) = 1 ORDER BY id")
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            image_ids.extend(int(row[0]) for row in rows)
    return image_ids


def count_all_image_ids() -> int:
    """Count readable image IDs without materializing them."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM images WHERE COALESCE(is_readable, 1) = 1"
        ).fetchone()
        return int(row[0] or 0) if row else 0


def count_untagged_image_ids() -> int:
    """Count readable untagged image IDs without materializing them."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM images WHERE tagged_at IS NULL AND COALESCE(is_readable, 1) = 1"
        ).fetchone()
        return int(row[0] or 0) if row else 0


def iter_all_image_id_chunks(chunk_size: int = 1000) -> Iterator[List[int]]:
    """Yield readable image IDs in database order using cursor.fetchmany()."""
    normalized_chunk_size = max(1, int(chunk_size or 1000))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM images WHERE COALESCE(is_readable, 1) = 1 ORDER BY id")
        while True:
            rows = cursor.fetchmany(normalized_chunk_size)
            if not rows:
                break
            yield [int(row[0]) for row in rows]


def iter_untagged_image_id_chunks(chunk_size: int = 1000) -> Iterator[List[int]]:
    """Yield readable untagged image IDs in database order using cursor.fetchmany()."""
    normalized_chunk_size = max(1, int(chunk_size or 1000))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM images WHERE tagged_at IS NULL AND COALESCE(is_readable, 1) = 1 ORDER BY id")
        while True:
            rows = cursor.fetchmany(normalized_chunk_size)
            if not rows:
                break
            yield [int(row[0]) for row in rows]


def get_image_count() -> int:
    """Get total number of images in database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM images")
        return cursor.fetchone()[0]
