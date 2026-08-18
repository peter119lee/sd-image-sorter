"""Library facet, checkpoint, and health-report read operations.

Extracted from ``database.py`` as part of the database module split. This module
holds metadata-status counts, the library health audit, and checkpoint facets.

Imports only from db_core / db_helpers / db_tags / utils / stdlib to avoid an
import cycle with the ``database`` facade.
"""
from typing import Optional, List, Dict, Any, NamedTuple, Tuple

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


_READABLE_SQL = "COALESCE(is_readable, 1) = 1"
_NO_CHECKPOINT_SQL = "(checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '')"
_NO_DIMENSIONS_SQL = "(width IS NULL OR height IS NULL OR width <= 0 OR height <= 0)"
_NO_FILE_SIZE_SQL = "(file_size IS NULL OR file_size <= 0)"
_METADATA_STATUS_SQL = "LOWER(COALESCE(metadata_status, 'complete'))"


class IssueSpec(NamedTuple):
    """One member of the issue vocabulary published as ``issue_counts``.

    ``sql`` carries its own population guard, so the predicate a key publishes
    is readable in one place next to what the key claims.

    ``remedy`` names the recommendation kind that offers the action, or is
    ``None`` for a key that is *reported only*. A reported-only key must give a
    ``reported_only_reason`` and may carry **no** consequence: no quality weight
    and no contribution to ``actionable_count``. That pairing is the whole point.
    Three separate defects in this payload were "a number the user is charged for
    beside no action that can move it" (``missing_prompt`` in ``7c10fb6``,
    ``missing_checkpoint`` in ``5332c02``, the metadata-health population in
    ``62dc568``), so the vocabulary now refuses to express one.

    ``quality_weight`` is the cost of **one row the key's remedy visits**, not of
    one match of this key: the score charges each remedy's union once (see
    :func:`_remedy_quality_weight`), so at most one key per remedy may declare a
    weight and it prices every row in that union. Two keys describing the same
    repair each carrying 2.0 is what deducted twice for every dead row.
    """

    key: str
    sql: str
    remedy: Optional[str]
    quality_weight: float = 0.0
    feeds_actionable: bool = False
    reported_only_reason: str = ""


class IssueRemedy(NamedTuple):
    """An action offered for one or more issue keys.

    ``keys`` decides the advertised count: a remedy reports the number of
    **distinct rows** matched by the union of its keys' predicates, never the sum
    of their counters. Summing was a live defect — every unreadable row is also a
    ``metadata_status = 'error'`` row (``mark_image_unreadable`` sets both), so
    ``reparse_or_reconnect`` advertised 3,074 rows to re-parse on the owner's
    library where 1,537 exist.

    ``action`` records the control the user actually reaches. It cannot be
    verified from here — it is the one part of the invariant that stays a written
    convention — but nothing may claim a remedy without naming one.
    """

    kind: str
    keys: Tuple[str, ...]
    severity: str
    action: str
    escalate_to_warning_at_percent: Optional[float] = None


# Order is the order ``issue_counts`` is published in.
ISSUE_VOCABULARY: Tuple[IssueSpec, ...] = (
    IssueSpec(
        key="unreadable",
        sql="COALESCE(is_readable, 1) = 0",
        remedy="reparse_or_reconnect",
        quality_weight=2.0,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="missing_text",
        sql=f"{_READABLE_SQL} AND ({MISSING_TEXT_SQL})",
        remedy="missing_text",
        quality_weight=1.4,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="sd_missing_checkpoint",
        sql=f"{_READABLE_SQL} AND {_NO_CHECKPOINT_SQL} AND {SD_ATTRIBUTED_GENERATOR_SQL}",
        remedy="sd_missing_checkpoint",
        quality_weight=0.8,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="unattributed_sd_metadata",
        sql=f"{_READABLE_SQL} AND ({UNATTRIBUTED_SD_METADATA_SQL})",
        remedy="unattributed_sd_metadata",
        # Inherits the weight the whole-library unknown_generator count used to
        # carry, now charged only where an attribution can actually be derived.
        quality_weight=0.6,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="missing_dimensions",
        sql=f"{_READABLE_SQL} AND {_NO_DIMENSIONS_SQL}",
        remedy="incomplete_scan_record",
        quality_weight=1.3,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="missing_file_size",
        sql=f"{_READABLE_SQL} AND {_NO_FILE_SIZE_SQL}",
        remedy="incomplete_scan_record",
        # The second facet of one incomplete scan record, and usually the same
        # rows missing_dimensions already names (all 63 of them on the owner's
        # library). The weight for the pair sits on missing_dimensions and is
        # charged once over the union, so these rows do cost the score — they
        # just cannot cost it twice for one re-scan. No actionable contribution
        # for the same reason.
    ),
    IssueSpec(
        key="untagged",
        sql=f"{_READABLE_SQL} AND tagged_at IS NULL",
        remedy="untagged",
        quality_weight=0.5,
        feeds_actionable=True,
    ),
    IssueSpec(
        key="missing_embedding",
        sql=f"{_READABLE_SQL} AND embedding IS NULL",
        remedy=None,
        reported_only_reason=(
            "Optional enrichment coverage, not a defect: the complement is "
            "already published as summary.embedding_percent, and a library that "
            "never uses Find Similar is not broken for having none. Rendered "
            "deliberately as a coverage row (library-health.js keeps it visible "
            "even at zero) rather than as a defect bar, and carries no weight "
            "and no actionable contribution."
        ),
    ),
    IssueSpec(
        key="missing_aesthetic",
        sql=f"{_READABLE_SQL} AND aesthetic_score IS NULL",
        remedy=None,
        reported_only_reason=(
            "Optional enrichment coverage, same reasoning as missing_embedding: "
            "the complement is summary.aesthetic_percent, and no action is urged."
        ),
    ),
    IssueSpec(
        key="metadata_pending",
        sql=f"{_METADATA_STATUS_SQL} = 'pending'",
        remedy="metadata_pending",
    ),
    IssueSpec(
        key="metadata_error",
        sql=f"{_METADATA_STATUS_SQL} = 'error'",
        remedy="reparse_or_reconnect",
        # The weight for a dead row sits on ``unreadable`` and is charged once
        # over the pair's union, which is what this key's rows are: every
        # unreadable row is also a metadata_status = 'error' row, so declaring
        # 2.0 here as well deducted twice for the same 1,537 rows and published
        # 60.0 for a library that scores 69.8. A readable parse failure — the
        # rows this key holds that ``unreadable`` does not — is in the union too,
        # so it still costs the same 2.0 it always did.
    ),
)

# Order is the order ``recommendations`` is published in.
ISSUE_REMEDIES: Tuple[IssueRemedy, ...] = (
    IssueRemedy(
        kind="metadata_pending",
        keys=("metadata_pending",),
        severity="info",
        action="Wait for the running metadata import to finish; the counts settle on their own.",
    ),
    IssueRemedy(
        kind="reparse_or_reconnect",
        keys=("unreadable", "metadata_error"),
        severity="warning",
        action=(
            "Re-scan the source folder, or reconnect the moved files from the "
            "reconnect review, so the rows point at readable images again."
        ),
    ),
    IssueRemedy(
        kind="missing_text",
        keys=("missing_text",),
        severity="info",
        escalate_to_warning_at_percent=10.0,
        action=(
            "Run Recover Missing Text in Settings. Its own snapshot "
            "(metadata_repair_service.snapshot_missing_prompt_ids) walks every "
            "promptless row, and MISSING_TEXT_SQL — this key's predicate, the "
            "same shared constant that module imports — is the subset a run can "
            "still turn from no text into text."
        ),
    ),
    IssueRemedy(
        kind="sd_missing_checkpoint",
        keys=("sd_missing_checkpoint",),
        severity="info",
        action=(
            "Re-scan the generated folders so the model name is read back out of "
            "the file, or record it from the Reader's metadata editor."
        ),
    ),
    IssueRemedy(
        kind="unattributed_sd_metadata",
        keys=("unattributed_sd_metadata",),
        severity="info",
        action=(
            "Re-parse the affected images (per image from the detail modal, or by "
            "re-scanning the folder) so today's parser derives the generator the "
            "stored generation data implies."
        ),
    ),
    IssueRemedy(
        kind="incomplete_scan_record",
        keys=("missing_dimensions", "missing_file_size"),
        severity="info",
        action=(
            "Re-scan the source folder: the scanner re-stats the file and re-reads "
            "its dimensions, which is the only thing that fills these columns."
        ),
    ),
    IssueRemedy(
        kind="untagged",
        keys=("untagged",),
        severity="info",
        action="Run AI tagging over the untagged images from the Gallery tagging bar.",
    ),
)


def _validate_issue_vocabulary(
    vocabulary: Tuple[IssueSpec, ...] = ISSUE_VOCABULARY,
    remedies: Tuple[IssueRemedy, ...] = ISSUE_REMEDIES,
) -> None:
    """Reject a vocabulary that could express a charge with no remedy.

    Called at import, so a key that claims consequence without naming an action
    stops the process rather than quietly becoming a permanent issue bar.
    """
    keys = [spec.key for spec in vocabulary]
    duplicates = {key for key in keys if keys.count(key) > 1}
    if duplicates:
        raise ValueError(f"issue vocabulary declares duplicate keys: {sorted(duplicates)}")
    by_key = {spec.key: spec for spec in vocabulary}

    remedy_kinds = [remedy.kind for remedy in remedies]
    duplicate_kinds = {kind for kind in remedy_kinds if remedy_kinds.count(kind) > 1}
    if duplicate_kinds:
        raise ValueError(f"issue remedies declare duplicate kinds: {sorted(duplicate_kinds)}")

    for remedy in remedies:
        if not remedy.keys:
            raise ValueError(f"remedy {remedy.kind!r} resolves no issue key")
        if not remedy.action.strip():
            raise ValueError(f"remedy {remedy.kind!r} names no action the user can reach")
        unknown = [key for key in remedy.keys if key not in keys]
        if unknown:
            raise ValueError(f"remedy {remedy.kind!r} resolves undeclared keys: {unknown}")
        weighted = [key for key in remedy.keys if by_key[key].quality_weight]
        if len(weighted) > 1:
            raise ValueError(
                f"remedy {remedy.kind!r} charges for its rows twice: "
                f"{sorted(weighted)} each declare a weight, and the score charges "
                "this remedy's rows once, so a row matching both would cost double "
                "for one repair"
            )

    for spec in vocabulary:
        if spec.remedy is None:
            if not spec.reported_only_reason.strip():
                raise ValueError(
                    f"issue key {spec.key!r} has no remedy and no recorded reason for "
                    "being reported anyway"
                )
            if spec.quality_weight or spec.feeds_actionable:
                raise ValueError(
                    f"issue key {spec.key!r} has no remedy yet charges the user "
                    f"(weight={spec.quality_weight}, actionable={spec.feeds_actionable}); "
                    "a number nothing can move must not cost anything"
                )
            continue
        if spec.reported_only_reason.strip():
            raise ValueError(
                f"issue key {spec.key!r} declares both a remedy and a "
                "reported-only reason"
            )
        if spec.remedy not in remedy_kinds:
            raise ValueError(
                f"issue key {spec.key!r} names remedy {spec.remedy!r}, which no "
                "recommendation emits, so its bar would have no fix attached"
            )
        owner = next(remedy for remedy in remedies if remedy.kind == spec.remedy)
        if spec.key not in owner.keys:
            raise ValueError(
                f"issue key {spec.key!r} names remedy {spec.remedy!r} but that "
                "remedy does not resolve it, so its advertised count would "
                "describe different rows"
            )


_validate_issue_vocabulary()


def _issue_union_sql(remedy: IssueRemedy, by_key: Dict[str, IssueSpec]) -> str:
    """The rows a remedy visits: the union of its keys, counted once each."""
    return " OR ".join(f"({by_key[key].sql})" for key in remedy.keys)


def _remedy_quality_weight(remedy: IssueRemedy, by_key: Dict[str, IssueSpec]) -> float:
    """What one row costs the score for this remedy.

    At most one of a remedy's keys may declare a weight
    (:func:`_validate_issue_vocabulary` rejects the alternative), so the price of
    a row is unambiguous however many of the remedy's keys it matches.
    """
    return next(
        (by_key[key].quality_weight for key in remedy.keys if by_key[key].quality_weight),
        0.0,
    )


def get_library_health_report(*, sample_limit: int = 8) -> Dict[str, Any]:
    """Return a read-only quality audit for the indexed image library.

    ``issue_counts`` is the issue vocabulary: its keys render as issue bars and
    most of them feed ``actionable_count`` and the quality score, so anything in
    it is a claim that something is wrong and that the user can act. Every member
    is declared in :data:`ISSUE_VOCABULARY` with the remedy that names its action,
    or with a recorded reason for being reported without one — and a key with no
    remedy may carry no weight and no actionable contribution
    (:func:`_validate_issue_vocabulary` refuses the alternative at import).

    ``summary.quality_score`` deducts per **remedy**, over the distinct rows that
    remedy visits, which is exactly what its recommendation card advertises. Two
    keys describing one repair therefore cost one row once, and at most one of a
    remedy's keys may declare the weight.

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
    by_key = {spec.key: spec for spec in ISSUE_VOCABULARY}

    # One scan for every counter: the issue keys, the rows each remedy visits,
    # and the composition statistics.
    issue_columns = ",\n                ".join(
        f"SUM(CASE WHEN {spec.sql} THEN 1 ELSE 0 END) AS issue_{spec.key}"
        for spec in ISSUE_VOCABULARY
    )
    remedy_columns = ",\n                ".join(
        f"SUM(CASE WHEN {_issue_union_sql(remedy, by_key)} THEN 1 ELSE 0 END) AS remedy_{remedy.kind}"
        for remedy in ISSUE_REMEDIES
    )

    with get_db() as conn:
        cursor = conn.cursor()
        summary_row = cursor.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN {_READABLE_SQL} THEN 1 ELSE 0 END) AS readable,
                {issue_columns},
                {remedy_columns},
                SUM(CASE WHEN {_READABLE_SQL} AND {NO_PROMPT_SQL} THEN 1 ELSE 0 END) AS stat_missing_prompt,
                SUM(CASE WHEN {_READABLE_SQL} AND (negative_prompt IS NULL OR TRIM(negative_prompt) = '') THEN 1 ELSE 0 END) AS stat_missing_negative_prompt,
                SUM(CASE WHEN {_READABLE_SQL} AND {_NO_CHECKPOINT_SQL} THEN 1 ELSE 0 END) AS stat_missing_checkpoint,
                SUM(CASE WHEN {_READABLE_SQL} AND generator = 'unknown' THEN 1 ELSE 0 END) AS stat_unknown_generator
            FROM images
            """
        ).fetchone()

        def _count(column: str) -> int:
            if summary_row is None:
                return 0
            return int(summary_row[column] or 0)

        total = _count("total")
        readable = _count("readable")

        issue_counts: Dict[str, int] = {
            spec.key: _count(f"issue_{spec.key}") for spec in ISSUE_VOCABULARY
        }
        remedy_targets: Dict[str, int] = {
            remedy.kind: _count(f"remedy_{remedy.kind}") for remedy in ISSUE_REMEDIES
        }
        # True, useful, and not a defect: see this function's docstring.
        statistics: Dict[str, int] = {
            "missing_prompt": _count("stat_missing_prompt"),
            "missing_negative_prompt": _count("stat_missing_negative_prompt"),
            "missing_checkpoint": _count("stat_missing_checkpoint"),
            "unknown_generator": _count("stat_unknown_generator"),
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
    # Both totals are summed straight off the vocabulary's own declarations, so a
    # key cannot cost the user anything it did not declare — and cannot declare
    # anything without naming the remedy that moves it.
    actionable_count = sum(
        issue_counts[spec.key] for spec in ISSUE_VOCABULARY if spec.feeds_actionable
    ) + duplicate_filename_images
    quality_score = 100.0
    if total > 0:
        # Charged per remedy over the distinct rows it visits, so the score
        # deducts exactly what the recommendation cards advertise. Summing the
        # per-key counters instead charged a dead row twice — every unreadable
        # row is also a metadata_error row — for 3,074 of penalty where 1,537
        # rows exist, the same false count the cards themselves used to make.
        weighted_penalty = sum(
            remedy_targets[remedy.kind] * _remedy_quality_weight(remedy, by_key)
            for remedy in ISSUE_REMEDIES
        ) + min(duplicate_filename_images, total) * 0.5
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
            remedy_targets=remedy_targets,
            duplicate_filename_images=duplicate_filename_images,
        ),
    }


def _build_library_health_recommendations(
    *,
    total: int,
    remedy_targets: Dict[str, int],
    duplicate_filename_images: int,
) -> List[Dict[str, Any]]:
    """Offer each declared remedy, numbered by the rows it actually visits.

    ``remedy_targets`` holds ``COUNT(DISTINCT row)`` over the union of each
    remedy's issue keys, not the sum of those keys' counters. The distinction is
    the defect this payload keeps producing: ``reparse_or_reconnect`` used to add
    ``unreadable`` to ``metadata_error``, and since ``mark_image_unreadable``
    sets both, it advertised twice the rows that exist — 3,074 against 1,537 on
    the owner's library.
    """
    recommendations: List[Dict[str, Any]] = []
    if total <= 0:
        return recommendations

    for remedy in ISSUE_REMEDIES:
        count = remedy_targets.get(remedy.kind, 0)
        if count <= 0:
            continue
        severity = remedy.severity
        if remedy.escalate_to_warning_at_percent is not None:
            severity = (
                "warning"
                if _library_health_percent(count, total) >= remedy.escalate_to_warning_at_percent
                else "info"
            )
        recommendations.append({
            "kind": remedy.kind,
            "severity": severity,
            "count": count,
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
