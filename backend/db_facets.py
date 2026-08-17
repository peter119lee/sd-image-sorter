"""Library facet, checkpoint, and health-report read operations.

Extracted from ``database.py`` as part of the database module split. This module
holds metadata-status counts, the library health audit, and checkpoint facets.

Imports only from db_core / db_helpers / db_tags / utils / stdlib to avoid an
import cycle with the ``database`` facade.
"""
from typing import Optional, List, Dict, Any

from db_core import get_db
from db_helpers import (
    MISSING_TEXT_SQL,
    NO_PROMPT_SQL,
    SD_ATTRIBUTED_GENERATOR_SQL,
    UNATTRIBUTED_SD_METADATA_SQL,
    escape_like_pattern,
    normalize_prompt_token,
)
from db_tags import (
    _facet_search_rank_params,
    _facet_search_rank_sql,
    _append_optional_limit,
)
from utils.model_names import checkpoint_identity_key


def _checkpoint_facet_filter(
    search_query: Optional[str],
) -> tuple[str, str, List[Any]]:
    normalized_query = checkpoint_identity_key(search_query or "") or normalize_prompt_token(search_query or "")
    value_expr = "LOWER(checkpoint_normalized)"
    conditions = ["checkpoint_normalized IS NOT NULL", "TRIM(checkpoint_normalized) != ''"]
    where_params: List[Any] = []

    if normalized_query:
        conditions.append(f"{value_expr} LIKE ? ESCAPE '\\'")
        where_params.append(f"%{escape_like_pattern(normalized_query)}%")

    return normalized_query, " AND ".join(conditions), where_params


def get_metadata_status_counts() -> Dict[str, int]:
    """Get image counts grouped by metadata parsing status."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT LOWER(COALESCE(metadata_status, 'complete')) AS status, COUNT(*) AS count
            FROM images
            WHERE COALESCE(is_readable, 1) = 1
            GROUP BY LOWER(COALESCE(metadata_status, 'complete'))
            """
        )
        counts: Dict[str, int] = {}
        for row in cursor.fetchall():
            status = str(row["status"] or "complete").strip().lower() or "complete"
            counts[status] = int(row["count"] or 0)
        return counts


def _library_health_percent(value: float, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((float(value) / float(total)) * 100.0, 2)


def get_library_health_report(*, sample_limit: int = 8) -> Dict[str, Any]:
    """Return a read-only quality audit for the indexed image library.

    ``issue_counts`` is the actionable set: every key in it is something a user
    can do something about.

    ``statistics`` holds counts that are true but are not defects:
    ``missing_prompt``, ``missing_checkpoint``, ``missing_negative_prompt`` and
    ``unknown_generator``, which together say how much of the library carries
    real SD generation provenance. They live outside ``issue_counts``
    deliberately: an image Stable Diffusion never made has no prompt, no
    checkpoint, no negative prompt and no generator to recover, so reporting any
    of them as an issue offers a repair that cannot succeed. On a library of
    downloaded artwork all four read at or near 100%.

    Three of them have a narrower counterpart in ``issue_counts`` covering
    exactly the rows something can still be done for: ``missing_text`` (neither a
    prompt nor a sidecar caption) is the set the L3 recovery job can change,
    ``sd_missing_checkpoint`` is the readable rows a generator actually claimed
    that still record no model name, and ``unattributed_sd_metadata`` is the rows
    that record generation data against no generator at all — impossible for
    today's parser to write, so a stale attribution a re-parse can derive. Same
    split Prompt Lab's ``no_checkpoint_metadata`` reason makes before offering a
    scan.
    """
    bounded_sample_limit = max(1, min(int(sample_limit or 8), 25))

    with get_db() as conn:
        cursor = conn.cursor()
        summary_row = cursor.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 0 THEN 1 ELSE 0 END) AS unreadable,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 THEN 1 ELSE 0 END) AS readable,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND {NO_PROMPT_SQL} THEN 1 ELSE 0 END) AS missing_prompt,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND ({MISSING_TEXT_SQL}) THEN 1 ELSE 0 END) AS missing_text,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND (negative_prompt IS NULL OR TRIM(negative_prompt) = '') THEN 1 ELSE 0 END) AS missing_negative_prompt,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND (checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '') THEN 1 ELSE 0 END) AS missing_checkpoint,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND (checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '') AND {SD_ATTRIBUTED_GENERATOR_SQL} THEN 1 ELSE 0 END) AS sd_missing_checkpoint,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND ({UNATTRIBUTED_SD_METADATA_SQL}) THEN 1 ELSE 0 END) AS unattributed_sd_metadata,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND (width IS NULL OR height IS NULL OR width <= 0 OR height <= 0) THEN 1 ELSE 0 END) AS missing_dimensions,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND (file_size IS NULL OR file_size <= 0) THEN 1 ELSE 0 END) AS missing_file_size,
                -- One re-scan fills both columns, so the advice counts the rows
                -- it visits once each instead of adding the two counters above.
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND ((width IS NULL OR height IS NULL OR width <= 0 OR height <= 0) OR (file_size IS NULL OR file_size <= 0)) THEN 1 ELSE 0 END) AS incomplete_scan_record,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND tagged_at IS NULL THEN 1 ELSE 0 END) AS untagged,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND embedding IS NULL THEN 1 ELSE 0 END) AS missing_embedding,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND aesthetic_score IS NULL THEN 1 ELSE 0 END) AS missing_aesthetic,
                SUM(CASE WHEN LOWER(COALESCE(metadata_status, 'complete')) = 'pending' THEN 1 ELSE 0 END) AS metadata_pending,
                SUM(CASE WHEN LOWER(COALESCE(metadata_status, 'complete')) = 'error' THEN 1 ELSE 0 END) AS metadata_error,
                SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND generator = 'unknown' THEN 1 ELSE 0 END) AS unknown_generator
            FROM images
            """
        ).fetchone()

        total = int(summary_row["total"] or 0) if summary_row else 0
        readable = int(summary_row["readable"] or 0) if summary_row else 0

        issue_counts: Dict[str, int] = {
            "unreadable": int(summary_row["unreadable"] or 0) if summary_row else 0,
            "missing_text": int(summary_row["missing_text"] or 0) if summary_row else 0,
            "sd_missing_checkpoint": int(summary_row["sd_missing_checkpoint"] or 0) if summary_row else 0,
            "unattributed_sd_metadata": int(summary_row["unattributed_sd_metadata"] or 0) if summary_row else 0,
            "missing_dimensions": int(summary_row["missing_dimensions"] or 0) if summary_row else 0,
            "missing_file_size": int(summary_row["missing_file_size"] or 0) if summary_row else 0,
            "untagged": int(summary_row["untagged"] or 0) if summary_row else 0,
            "missing_embedding": int(summary_row["missing_embedding"] or 0) if summary_row else 0,
            "missing_aesthetic": int(summary_row["missing_aesthetic"] or 0) if summary_row else 0,
            "metadata_pending": int(summary_row["metadata_pending"] or 0) if summary_row else 0,
            "metadata_error": int(summary_row["metadata_error"] or 0) if summary_row else 0,
        }
        incomplete_scan_record_images = (
            int(summary_row["incomplete_scan_record"] or 0) if summary_row else 0
        )
        # True, useful, and not a defect: see this function's docstring.
        statistics: Dict[str, int] = {
            "missing_prompt": int(summary_row["missing_prompt"] or 0) if summary_row else 0,
            "missing_negative_prompt": int(summary_row["missing_negative_prompt"] or 0) if summary_row else 0,
            "missing_checkpoint": int(summary_row["missing_checkpoint"] or 0) if summary_row else 0,
            "unknown_generator": int(summary_row["unknown_generator"] or 0) if summary_row else 0,
        }

        duplicate_filename_rows = cursor.execute(
            """
            SELECT filename, COUNT(*) AS count, SUM(COALESCE(file_size, 0)) AS total_size
            FROM images
            WHERE filename IS NOT NULL AND TRIM(filename) != ''
            GROUP BY LOWER(filename)
            HAVING COUNT(*) > 1
            ORDER BY count DESC, filename COLLATE NOCASE ASC
            LIMIT ?
            """,
            (bounded_sample_limit,),
        ).fetchall()
        duplicate_filenames = [dict(row) for row in duplicate_filename_rows]

        duplicate_group_row = cursor.execute(
            """
            SELECT COUNT(*) AS groups_count, COALESCE(SUM(count), 0) AS image_count
            FROM (
                SELECT COUNT(*) AS count
                FROM images
                WHERE filename IS NOT NULL AND TRIM(filename) != ''
                GROUP BY LOWER(filename)
                HAVING COUNT(*) > 1
            ) grouped
            """
        ).fetchone()
        duplicate_filename_groups = int(duplicate_group_row["groups_count"] or 0) if duplicate_group_row else 0
        duplicate_filename_images = int(duplicate_group_row["image_count"] or 0) if duplicate_group_row else 0

        oversized_rows = cursor.execute(
            """
            SELECT id, filename, path, file_size, width, height, generator, checkpoint_normalized
            FROM images
            WHERE COALESCE(is_readable, 1) = 1 AND COALESCE(file_size, 0) > 0
            ORDER BY file_size DESC
            LIMIT ?
            """,
            (bounded_sample_limit,),
        ).fetchall()
        largest_images = [dict(row) for row in oversized_rows]

        folder_rows = cursor.execute(
            f"""
            SELECT folder,
                   COUNT(*) AS count,
                   SUM(COALESCE(file_size, 0)) AS total_size,
                   SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND {NO_PROMPT_SQL} THEN 1 ELSE 0 END) AS missing_prompt,
                   SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND ({MISSING_TEXT_SQL}) THEN 1 ELSE 0 END) AS missing_text,
                   SUM(CASE WHEN COALESCE(is_readable, 1) = 1 AND tagged_at IS NULL THEN 1 ELSE 0 END) AS untagged,
                   SUM(CASE WHEN COALESCE(is_readable, 1) = 0 THEN 1 ELSE 0 END) AS unreadable
            FROM (
                SELECT *,
                       CASE
                           WHEN filename IS NULL OR TRIM(filename) = '' THEN ''
                           WHEN LENGTH(REPLACE(path, '\\', '/')) <= LENGTH(filename) THEN ''
                           WHEN LOWER(SUBSTR(REPLACE(path, '\\', '/'), -LENGTH(filename))) != LOWER(filename) THEN ''
                           ELSE RTRIM(SUBSTR(REPLACE(path, '\\', '/'), 1, LENGTH(REPLACE(path, '\\', '/')) - LENGTH(filename)), '/')
                       END AS folder
                FROM images
            ) foldered
            GROUP BY folder
            ORDER BY count DESC, folder COLLATE NOCASE ASC
            LIMIT ?
            """,
            (bounded_sample_limit,),
        ).fetchall()
        top_folders = [dict(row) for row in folder_rows]

        # A sample list is an invitation to act, so it follows the same rule as
        # the counts: a row whose only text is a sidecar caption is not a text
        # problem, and a row nothing generated is not a checkpoint problem.
        # ``sidecar_caption`` and ``generator`` ride along because rows listed
        # for some other reason still have to be describable — without them a
        # consumer can only report "missing prompt" or "missing checkpoint" for
        # a row where neither is a defect.
        issue_sample_rows = cursor.execute(
            f"""
            SELECT id, filename, path, generator, metadata_status, read_error,
                   prompt, sidecar_caption, checkpoint_normalized, width, height,
                   file_size, tagged_at
            FROM images
            WHERE COALESCE(is_readable, 1) = 0
               OR LOWER(COALESCE(metadata_status, 'complete')) IN ('pending', 'error')
               OR (COALESCE(is_readable, 1) = 1 AND ({MISSING_TEXT_SQL}))
               OR (COALESCE(is_readable, 1) = 1 AND (checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '') AND {SD_ATTRIBUTED_GENERATOR_SQL})
               OR (COALESCE(is_readable, 1) = 1 AND ({UNATTRIBUTED_SD_METADATA_SQL}))
               OR (COALESCE(is_readable, 1) = 1 AND (width IS NULL OR height IS NULL OR width <= 0 OR height <= 0))
               OR (COALESCE(is_readable, 1) = 1 AND tagged_at IS NULL)
            ORDER BY
                CASE
                    WHEN COALESCE(is_readable, 1) = 0 THEN 0
                    WHEN LOWER(COALESCE(metadata_status, 'complete')) = 'error' THEN 1
                    WHEN LOWER(COALESCE(metadata_status, 'complete')) = 'pending' THEN 2
                    WHEN {MISSING_TEXT_SQL} THEN 3
                    WHEN (checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '') AND {SD_ATTRIBUTED_GENERATOR_SQL} THEN 4
                    WHEN {UNATTRIBUTED_SD_METADATA_SQL} THEN 5
                    WHEN width IS NULL OR height IS NULL OR width <= 0 OR height <= 0 THEN 6
                    WHEN tagged_at IS NULL THEN 7
                    ELSE 8
                END,
                id ASC
            LIMIT ?
            """,
            (bounded_sample_limit,),
        ).fetchall()
        issue_samples = [dict(row) for row in issue_sample_rows]

    # "Ready" asks whether we know what is in the image, not whether an SD tool
    # made it: a row carrying sidecar caption text is described, so only genuine
    # textlessness counts against it.
    metadata_ready = max(readable - issue_counts["missing_text"] - issue_counts["missing_dimensions"], 0)
    actionable_count = (
        issue_counts["unreadable"]
        + issue_counts["missing_text"]
        + issue_counts["sd_missing_checkpoint"]
        + issue_counts["unattributed_sd_metadata"]
        + issue_counts["missing_dimensions"]
        + issue_counts["untagged"]
        + duplicate_filename_images
    )
    quality_score = 100.0
    if total > 0:
        weighted_penalty = (
            issue_counts["unreadable"] * 2.0
            + issue_counts["metadata_error"] * 2.0
            + issue_counts["missing_text"] * 1.4
            + issue_counts["missing_dimensions"] * 1.3
            + issue_counts["sd_missing_checkpoint"] * 0.8
            # Inherits the weight the whole-library unknown_generator count used
            # to carry, now charged only where an attribution can be derived.
            + issue_counts["unattributed_sd_metadata"] * 0.6
            + min(issue_counts["untagged"], total) * 0.5
            + min(duplicate_filename_images, total) * 0.5
        )
        average_penalty = weighted_penalty / float(total)
        quality_score = max(0.0, round(100.0 - min(90.0, average_penalty * 22.0), 1))

    return {
        "summary": {
            "total_images": total,
            "readable_images": readable,
            "metadata_ready": metadata_ready,
            "metadata_ready_percent": _library_health_percent(metadata_ready, readable),
            "tagged_percent": _library_health_percent(readable - issue_counts["untagged"], readable),
            "embedding_percent": _library_health_percent(readable - issue_counts["missing_embedding"], readable),
            "aesthetic_percent": _library_health_percent(readable - issue_counts["missing_aesthetic"], readable),
            "quality_score": quality_score,
            "actionable_count": actionable_count,
        },
        "issue_counts": issue_counts,
        "statistics": statistics,
        "duplicate_filenames": {
            "groups": duplicate_filename_groups,
            "images": duplicate_filename_images,
            "samples": duplicate_filenames,
        },
        "largest_images": largest_images,
        "top_folders": top_folders,
        "issue_samples": issue_samples,
        "recommendations": _build_library_health_recommendations(
            total=total,
            issue_counts=issue_counts,
            incomplete_scan_record_images=incomplete_scan_record_images,
            duplicate_filename_images=duplicate_filename_images,
        ),
    }


def _build_library_health_recommendations(
    *,
    total: int,
    issue_counts: Dict[str, int],
    incomplete_scan_record_images: int,
    duplicate_filename_images: int,
) -> List[Dict[str, Any]]:
    recommendations: List[Dict[str, Any]] = []
    if total <= 0:
        return recommendations

    if issue_counts.get("metadata_pending", 0) > 0:
        recommendations.append({
            "kind": "metadata_pending",
            "severity": "info",
            "count": issue_counts["metadata_pending"],
        })
    if issue_counts.get("unreadable", 0) > 0 or issue_counts.get("metadata_error", 0) > 0:
        recommendations.append({
            "kind": "reparse_or_reconnect",
            "severity": "warning",
            "count": issue_counts.get("unreadable", 0) + issue_counts.get("metadata_error", 0),
        })
    # Deliberately keyed on missing_text, not missing_prompt: a recommendation is
    # an offer to act, and re-parsing an image that Stable Diffusion never made
    # cannot produce a prompt however many times it runs.
    if issue_counts.get("missing_text", 0) > 0:
        recommendations.append({
            "kind": "missing_text",
            "severity": "warning" if _library_health_percent(issue_counts["missing_text"], total) >= 10 else "info",
            "count": issue_counts["missing_text"],
        })
    # Keyed on the SD-attributed subset for the same reason missing_text is:
    # "your images lack checkpoint info" is not advice you can act on when
    # nothing generated them. The count travelling with it is the count the
    # advice can help.
    if issue_counts.get("sd_missing_checkpoint", 0) > 0:
        recommendations.append({
            "kind": "sd_missing_checkpoint",
            "severity": "info",
            "count": issue_counts["sd_missing_checkpoint"],
        })
    # The actionable subset of "the generator is unknown": a row that records SD
    # generation data yet names no generator is one today's parser could not have
    # written, so re-parsing it re-derives the attribution.
    if issue_counts.get("unattributed_sd_metadata", 0) > 0:
        recommendations.append({
            "kind": "unattributed_sd_metadata",
            "severity": "info",
            "count": issue_counts["unattributed_sd_metadata"],
        })
    # missing_dimensions and missing_file_size are two facets of one incomplete
    # scan record and usually the same rows, so the offer is one re-scan and the
    # count beside it is the rows that re-scan visits — not the two counters
    # added together, which would advertise twice the work that exists.
    if incomplete_scan_record_images > 0:
        recommendations.append({
            "kind": "incomplete_scan_record",
            "severity": "info",
            "count": incomplete_scan_record_images,
        })
    if issue_counts.get("untagged", 0) > 0:
        recommendations.append({
            "kind": "untagged",
            "severity": "info",
            "count": issue_counts["untagged"],
        })
    if duplicate_filename_images > 0:
        recommendations.append({
            "kind": "duplicate_filenames",
            "severity": "info",
            "count": duplicate_filename_images,
        })
    return recommendations


def get_all_checkpoints(
    *,
    limit: Optional[int] = None,
    search_query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get normalized checkpoint facets with counts for filtering and analytics."""
    normalized_query, where_clause, where_params = _checkpoint_facet_filter(search_query)
    value_expr = "LOWER(checkpoint_normalized)"
    rank_select = ""
    rank_order = ""

    if normalized_query:
        rank_select = f", {_facet_search_rank_sql(value_expr)} AS relevance"
        rank_order = "relevance ASC, "

    with get_db() as conn:
        cursor = conn.cursor()
        query = f"""
            SELECT checkpoint_normalized, COUNT(*) as count{rank_select}
            FROM images
            WHERE {where_clause}
            GROUP BY checkpoint_normalized
            ORDER BY {rank_order}count DESC, checkpoint_normalized COLLATE NOCASE ASC
        """
        params: List[Any] = []
        if normalized_query:
            params.extend(_facet_search_rank_params(normalized_query))
        params.extend(where_params)
        query, params = _append_optional_limit(query, params, limit)
        cursor.execute(query, params)
        return [
            {
                "checkpoint": row["checkpoint_normalized"],
                "checkpoint_normalized": row["checkpoint_normalized"],
                "count": row["count"],
            }
            for row in cursor.fetchall()
        ]


def count_checkpoints(*, search_query: Optional[str]) -> int:
    """Count unique normalized checkpoints matching the library query."""
    _normalized_query, where_clause, where_params = _checkpoint_facet_filter(search_query)
    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT checkpoint_normalized
                FROM images
                WHERE {where_clause}
                GROUP BY checkpoint_normalized
            )
            """,
            where_params,
        ).fetchone()
    return int(row[0] or 0) if row else 0
