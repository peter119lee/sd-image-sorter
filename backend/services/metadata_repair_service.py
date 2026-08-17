"""Metadata L3 repair: re-parse missing-prompt images through the current parser.

Second half of the raw-retention layer (migration 023). Scans store a gzipped
envelope of the original metadata chunks whenever parsing produced no positive
prompt; this service replays those envelopes — and, as a fallback, the files
themselves — through today's parser. Every parser improvement (new node
support, better tracing, scorer upgrades) therefore applies retroactively to
the whole library with one click instead of requiring a full folder rescan.

Runs inside the shared bulk-job machinery (progress + cancel via
``GET /api/bulk-jobs/{id}``); the health query powers the settings-page
counter that tells the user whether a re-parse is worth running.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from database import get_db, update_reparsed_prompt_fields
from image_manager import reparse_image_metadata
from services.bulk_job_service import BulkJobHandle

logger = logging.getLogger(__name__)

# Per-chunk row count. Raw replays are pure CPU + one small UPDATE, but the
# file fallback stats + fully re-parses images, so keep chunks modest to make
# cancellation responsive.
REPARSE_CHUNK_SIZE = 100

# Missing-prompt rows must still be readable to be worth retrying; unreadable
# rows are the scanner's problem, not the parser's. COALESCE matches the guard
# used everywhere else: is_readable was added later and NULL means "never
# assessed", not "unreadable" (see migrations/003_legacy_backfills.py). A bare
# ``is_readable = 1`` silently drops those rows from the repair job.
_MISSING_PROMPT_WHERE = "(prompt IS NULL OR TRIM(prompt) = '') AND COALESCE(is_readable, 1) = 1"

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
    """Per-generator parse-coverage counts for the settings health row."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(generator, 'unknown') AS generator,
                   COUNT(*) AS total,
                   SUM(CASE WHEN prompt IS NULL OR TRIM(prompt) = '' THEN 1 ELSE 0 END) AS missing_prompt,
                   SUM(CASE WHEN raw_metadata_gz IS NOT NULL THEN 1 ELSE 0 END) AS with_raw
            FROM images
            GROUP BY COALESCE(generator, 'unknown')
            ORDER BY total DESC
            """
        ).fetchall()
    generators = [
        {
            "generator": row["generator"],
            "total": int(row["total"] or 0),
            "missing_prompt": int(row["missing_prompt"] or 0),
            "with_raw": int(row["with_raw"] or 0),
        }
        for row in rows
    ]
    totals = {
        "total": sum(item["total"] for item in generators),
        "missing_prompt": sum(item["missing_prompt"] for item in generators),
        "with_raw": sum(item["with_raw"] for item in generators),
    }
    return {"generators": generators, "totals": totals}


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


def _replay_raw_envelope(parser, row: Dict[str, Any]) -> bool:
    """Re-run the parser over the stored envelope. True when a prompt landed."""
    envelope = _decode_raw_envelope(row.get("raw_metadata_gz"))
    if envelope is None:
        return False
    try:
        parsed = parser._detect_and_parse(envelope, image_path=row.get("path"))
    except Exception as exc:
        logger.debug("raw replay failed for image %s: %s", row.get("id"), exc)
        return False
    prompt = parsed.get("prompt")
    if not (isinstance(prompt, str) and prompt.strip()):
        return False
    generator = parsed.get("generator")
    update_reparsed_prompt_fields(
        int(row["id"]),
        prompt=prompt,
        negative_prompt=parsed.get("negative_prompt"),
        checkpoint=parsed.get("checkpoint"),
        loras=parsed.get("loras") or None,
        generator=generator if generator and generator != "unknown" else None,
    )
    return True


def _replay_file(row: Dict[str, Any]) -> Optional[bool]:
    """Full file re-parse fallback. None = file gone, bool = prompt recovered."""
    path = row.get("path")
    if not path or not os.path.isfile(path):
        return None
    try:
        metadata = reparse_image_metadata(int(row["id"]), path, preserve_derived_state=True)
    except Exception as exc:
        logger.debug("file re-parse failed for image %s: %s", row.get("id"), exc)
        return False
    prompt = metadata.get("prompt")
    return bool(isinstance(prompt, str) and prompt.strip())


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

    recovered = used_raw = used_file = missing_source = still_missing = 0
    errors: List[str] = []
    for row in rows:
        try:
            if _replay_raw_envelope(parser, row):
                recovered += 1
                used_raw += 1
                continue
            file_outcome = _replay_file(row)
            if file_outcome is None:
                if row.get("raw_metadata_gz"):
                    # Raw exists but today's parser still can't crack it;
                    # a future upgrade may. Not a missing source.
                    still_missing += 1
                else:
                    missing_source += 1
                continue
            used_file += 1
            if file_outcome:
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
            "still_missing": still_missing,
            "used_raw": used_raw,
            "used_file": used_file,
            "missing_source": missing_source,
        },
    }


def run_reparse_job(handle: BulkJobHandle) -> None:
    """Worker body for the reparse bulk job."""
    from services.bulk_job_service import BulkJobService

    worker = BulkJobService.chunked_worker(
        snapshot_missing_prompt_ids,
        _process_chunk,
        chunk_size=REPARSE_CHUNK_SIZE,
    )
    worker(handle)
