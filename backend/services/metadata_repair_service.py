"""Metadata L3 repair: recover missing image text through the current parser.

Second half of the raw-retention layer (migration 023). Scans store a gzipped
envelope of the original metadata chunks whenever parsing produced no positive
prompt; this service replays those envelopes — and, as a fallback, the files
themselves — through today's parser. Every parser improvement (new node
support, better tracing, scorer upgrades) therefore applies retroactively to
the whole library with one click instead of requiring a full folder rescan.

Since migration 042 the job recovers two different things and never confuses
them: the **SD generation prompt** when the metadata really carries one, and
the **sidecar caption** when the only text next to the image is a ``.txt`` /
``.json`` tag list somebody wrote. A caption recovery deliberately leaves
``prompt`` empty — those images were not generated from that text.

Runs inside the shared bulk-job machinery (progress + cancel via
``GET /api/bulk-jobs/{id}``); the health query powers the settings-page
counter that tells the user whether a run is worth starting.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from typing import Any, Dict, List, NamedTuple, Optional

from database import (
    MISSING_TEXT_SQL,
    NO_PROMPT_SQL,
    get_db,
    update_reparsed_prompt_fields,
    update_reparsed_sidecar_caption,
)
from image_manager import reparse_image_metadata
from services.bulk_job_service import BulkJobHandle

logger = logging.getLogger(__name__)

# Per-chunk row count. Raw replays are pure CPU + one small UPDATE, but the
# file fallback stats + fully re-parses images, so keep chunks modest to make
# cancellation responsive.
REPARSE_CHUNK_SIZE = 100

# The one population this module speaks about. Unreadable rows are the
# scanner's problem, not the parser's: the job cannot open the file, cannot
# replay anything into it, and cannot change a single counter about it. COALESCE
# matches the guard used everywhere else: is_readable was added later and NULL
# means "never assessed", not "unreadable" (see migrations/003_legacy_backfills.py).
# A bare ``is_readable = 1`` silently drops those rows from the repair job.
_READABLE_WHERE = "COALESCE(is_readable, 1) = 1"

# Deliberately the WIDER set within that population: the job walks every
# promptless row because a parser upgrade may yet find a prompt in one. What a
# run can still *change* is the ``MISSING_TEXT_SQL`` subset, and that is the
# number any UI may report as work outstanding
# (db_facets.get_library_health_report reads the same constant).
_MISSING_PROMPT_WHERE = f"{NO_PROMPT_SQL} AND {_READABLE_WHERE}"

_active_lock = threading.Lock()
_active_job_id: Optional[str] = None


def claim_active_job_id(job_id: str) -> bool:
    """Claim the re-parse slot unless its registry owner is inactive."""
    from services.bulk_job_service import TERMINAL_STATUSES, get_bulk_job_service

    global _active_job_id
    with _active_lock:
        if _active_job_id is not None:
            current = get_bulk_job_service().get_job(_active_job_id)
            if current is not None and current["status"] not in TERMINAL_STATUSES:
                return False
        _active_job_id = job_id
        return True


def release_active_job_id(job_id: str) -> bool:
    """Release the re-parse slot only when ``job_id`` still owns it."""
    global _active_job_id
    with _active_lock:
        if _active_job_id != job_id:
            return False
        _active_job_id = None
        return True


def get_active_job_id() -> Optional[str]:
    with _active_lock:
        return _active_job_id


def snapshot_missing_prompt_ids() -> List[int]:
    """Materialize the ids to retry before any mutation (bulk-job contract)."""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id FROM images WHERE {_MISSING_PROMPT_WHERE} ORDER BY id"
        ).fetchall()
    return [int(row["id"]) for row in rows]


def get_metadata_health() -> Dict[str, Any]:
    """Per-generator parse-coverage counts for the settings health row.

    Every counter here describes **readable images** — the same population the
    recovery job walks — because these numbers sit next to the button that runs
    it. ``missing_text`` is what the button advertises, so it has to equal what
    a run can still change; guarding only that column would have left it
    disagreeing with the ``total`` printed beside it, so the whole payload took
    one population instead.

    Field by field, all within that scope: ``total`` is the readable images
    recorded for the generator; ``missing_prompt`` is exactly the rows
    :func:`snapshot_missing_prompt_ids` retries; ``missing_text`` is the subset
    of those a run can still turn from "no text" into "has text"; ``with_raw``
    is the rows whose stored envelope can be replayed without the file.
    ``scope`` names the population and ``excluded_unreadable`` accounts for the
    indexed rows left out, so the difference from the library total stays
    visible rather than silently missing. Whole-library composition, including
    unreadable rows, is what ``GET /api/library-health`` reports.
    """
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(generator, 'unknown') AS generator,
                   COUNT(*) AS total,
                   SUM(CASE WHEN {NO_PROMPT_SQL} THEN 1 ELSE 0 END) AS missing_prompt,
                   SUM(CASE WHEN {MISSING_TEXT_SQL} THEN 1 ELSE 0 END) AS missing_text,
                   SUM(CASE WHEN raw_metadata_gz IS NOT NULL THEN 1 ELSE 0 END) AS with_raw
            FROM images
            WHERE {_READABLE_WHERE}
            GROUP BY COALESCE(generator, 'unknown')
            ORDER BY total DESC
            """
        ).fetchall()
        excluded_row = conn.execute(
            f"SELECT COUNT(*) AS excluded FROM images WHERE NOT ({_READABLE_WHERE})"
        ).fetchone()
    generators = [
        {
            "generator": row["generator"],
            "total": int(row["total"] or 0),
            "missing_prompt": int(row["missing_prompt"] or 0),
            "missing_text": int(row["missing_text"] or 0),
            "with_raw": int(row["with_raw"] or 0),
        }
        for row in rows
    ]
    totals = {
        "total": sum(item["total"] for item in generators),
        "missing_prompt": sum(item["missing_prompt"] for item in generators),
        "missing_text": sum(item["missing_text"] for item in generators),
        "with_raw": sum(item["with_raw"] for item in generators),
    }
    return {
        "generators": generators,
        "totals": totals,
        "scope": "readable_images",
        "excluded_unreadable": int(excluded_row["excluded"] or 0) if excluded_row else 0,
    }


def _decode_raw_envelope(raw_gz: Any) -> Optional[Dict[str, Any]]:
    """Gunzip + parse a stored envelope back into a metadata-chunks dict."""
    if not raw_gz:
        return None
    try:
        text = gzip.decompress(bytes(raw_gz)).decode("utf-8")
        envelope = json.loads(text)
    except Exception as exc:
        logger.debug("stored raw metadata envelope unusable: %s", exc)
        return None
    if not isinstance(envelope, dict) or not envelope:
        return None
    return envelope


class _TextOutcome(NamedTuple):
    """What a single replay attempt actually recovered for one row."""

    prompt: bool
    caption: bool

    def __bool__(self) -> bool:
        return self.prompt or self.caption


_NOTHING_RECOVERED = _TextOutcome(prompt=False, caption=False)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _replay_raw_envelope(parser, row: Dict[str, Any]) -> _TextOutcome:
    """Re-run the parser over the stored envelope and persist what it found.

    A prompt recovery clears the stored envelope (the row is repaired). A
    caption-only recovery does NOT: the row still has no prompt, so a future
    parser upgrade must still be able to replay the same bytes.
    """
    envelope = _decode_raw_envelope(row.get("raw_metadata_gz"))
    if envelope is None:
        return _NOTHING_RECOVERED
    try:
        parsed = parser._detect_and_parse(envelope, image_path=row.get("path"))
    except Exception as exc:
        logger.debug("raw replay failed for image %s: %s", row.get("id"), exc)
        return _NOTHING_RECOVERED

    prompt = parsed.get("prompt")
    caption = parsed.get("sidecar_caption")
    if _has_text(prompt):
        generator = parsed.get("generator")
        update_reparsed_prompt_fields(
            int(row["id"]),
            prompt=prompt,
            negative_prompt=parsed.get("negative_prompt"),
            checkpoint=parsed.get("checkpoint"),
            loras=parsed.get("loras") or None,
            generator=generator if generator and generator != "unknown" else None,
            sidecar_caption=caption if _has_text(caption) else None,
        )
        return _TextOutcome(prompt=True, caption=_has_text(caption))
    if _has_text(caption):
        update_reparsed_sidecar_caption(int(row["id"]), caption)
        return _TextOutcome(prompt=False, caption=True)
    return _NOTHING_RECOVERED


def _replay_file(row: Dict[str, Any]) -> Optional[_TextOutcome]:
    """Full file re-parse fallback. None = file gone, else what was recovered."""
    path = row.get("path")
    if not path or not os.path.isfile(path):
        return None
    try:
        metadata = reparse_image_metadata(int(row["id"]), path, preserve_derived_state=True)
    except Exception as exc:
        logger.debug("file re-parse failed for image %s: %s", row.get("id"), exc)
        return _NOTHING_RECOVERED
    return _TextOutcome(
        prompt=_has_text(metadata.get("prompt")),
        caption=_has_text(metadata.get("sidecar_caption")),
    )


def _process_chunk(chunk_ids: List[int]) -> Dict[str, Any]:
    """Bulk-job chunk: replay stored raw first, then the file, else skip."""
    from metadata_parser import MetadataParser

    parser = MetadataParser()
    placeholders = ",".join("?" for _ in chunk_ids)
    with get_db() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT id, path, generator, raw_metadata_gz
                FROM images
                WHERE id IN ({placeholders}) AND {_MISSING_PROMPT_WHERE}
                """,
                chunk_ids,
            ).fetchall()
        ]

    recovered = captions_recovered = 0
    used_raw = used_file = missing_source = still_missing = 0
    errors: List[str] = []
    for row in rows:
        try:
            raw_outcome = _replay_raw_envelope(parser, row)
            if raw_outcome.prompt:
                recovered += 1
                used_raw += 1
                if raw_outcome.caption:
                    captions_recovered += 1
                continue

            # The file is the better source when it is still there: it can see
            # a sidecar the stored envelope never captured. Only fall back to
            # what the envelope gave us once the file is gone.
            file_outcome = _replay_file(row)
            if file_outcome is None:
                if raw_outcome.caption:
                    captions_recovered += 1
                    used_raw += 1
                if row.get("raw_metadata_gz"):
                    # Raw exists but today's parser still can't crack it;
                    # a future upgrade may. Not a missing source.
                    still_missing += 1
                else:
                    missing_source += 1
                continue

            used_file += 1
            if file_outcome.caption:
                captions_recovered += 1
            if file_outcome.prompt:
                recovered += 1
            else:
                still_missing += 1
        except Exception as exc:
            still_missing += 1
            errors.append(f"image {row.get('id')}: {exc}")

    return {
        "processed": len(chunk_ids),
        "errors": errors,
        "result_delta": {
            "recovered": recovered,
            # Sidecar caption text recovered for rows that genuinely have no SD
            # prompt. Counted separately so "recovered" keeps meaning "prompt".
            "captions_recovered": captions_recovered,
            "still_missing": still_missing,
            "used_raw": used_raw,
            "used_file": used_file,
            "missing_source": missing_source,
        },
    }


def run_reparse_job(handle: BulkJobHandle) -> None:
    """Worker body for the text-recovery bulk job."""
    from services.bulk_job_service import BulkJobService

    worker = BulkJobService.chunked_worker(
        snapshot_missing_prompt_ids,
        _process_chunk,
        chunk_size=REPARSE_CHUNK_SIZE,
    )
    worker(handle)
