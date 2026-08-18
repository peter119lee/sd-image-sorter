"""DB record builders for image_manager scans.

Moved verbatim from image_manager.py (decomposition 2026-07, stage 1). Stateless. SAFETY invariants pinned
by tests/test_image_manager_pins.py: the placeholder record preserves
library_order_time even when the source fingerprint no longer matches,
and an error record NEVER drops the row. compact_metadata_json is
imported from its origin (never monkeypatched on image_manager); the
fingerprint gates come from image_manager_gates (same split family).
The facade re-imports every name below so ``image_manager.<name>`` keeps
resolving."""

import gzip
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from image_manager_gates import (
    _deserialize_loras,
    _has_source_fingerprint,
    _source_fingerprint_matches,
)
from metadata_storage import compact_metadata_json
from utils.reported_cause import describe_readability_failure, normalize_reported_cause

# NOTE(decomposition): keep the historical logger channel so log routing
# and output stay identical to the pre-split single-file module.
logger = logging.getLogger("image_manager")


def _build_placeholder_record(
    image_path: str,
    filename: str,
    stat_result: os.stat_result,
    existing: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create a fast-import placeholder row before metadata backfill starts."""
    preserve_existing_metadata = bool(existing) and bool(existing.get("is_readable", 1))
    if preserve_existing_metadata and _has_source_fingerprint(existing):
        preserve_existing_metadata = _source_fingerprint_matches(existing, stat_result)
    current_file_time = datetime.fromtimestamp(stat_result.st_mtime)
    library_order_time = (
        existing.get("library_order_time")
        or existing.get("created_at")
        or current_file_time
    ) if existing else current_file_time

    if preserve_existing_metadata:
        return {
            "path": image_path,
            "filename": filename,
            "generator": existing.get("generator"),
            "prompt": existing.get("prompt"),
            "sidecar_caption": existing.get("sidecar_caption"),
            "sidecar_caption_format": existing.get("sidecar_caption_format"),
            "negative_prompt": existing.get("negative_prompt"),
            "metadata_json": existing.get("metadata_json"),
            "width": existing.get("width"),
            "height": existing.get("height"),
            "file_size": int(stat_result.st_size),
            "checkpoint": existing.get("checkpoint"),
            "loras": _deserialize_loras(existing.get("loras")),
            "library_order_time": library_order_time,
            "source_file_mtime": current_file_time,
            "created_at": library_order_time,
            "model_hash": existing.get("model_hash"),
            "is_readable": bool(existing.get("is_readable", 1)),
            "read_error": existing.get("read_error"),
            "source_mtime_ns": int(stat_result.st_mtime_ns),
            "source_size": int(stat_result.st_size),
            "metadata_status": "pending",
            "content_fingerprint": existing.get("content_fingerprint"),
            "sidecar_fingerprint": existing.get("sidecar_fingerprint"),
        }

    return {
        "path": image_path,
        "filename": filename,
        "generator": "unknown",
        "prompt": None,
        "sidecar_caption": None,
        "sidecar_caption_format": None,
        "negative_prompt": None,
        "metadata_json": compact_metadata_json({}),
        "width": None,
        "height": None,
        "file_size": int(stat_result.st_size),
        "checkpoint": None,
        "loras": [],
        "library_order_time": library_order_time,
        "source_file_mtime": current_file_time,
        "created_at": library_order_time,
        "model_hash": None,
        "is_readable": True,
        "read_error": None,
        "source_mtime_ns": int(stat_result.st_mtime_ns),
        "source_size": int(stat_result.st_size),
        "metadata_status": "pending",
        "content_fingerprint": None,
        # None, not '': the placeholder has not questioned the filesystem about
        # sidecars, and the metadata stage that does is what should record it.
        "sidecar_fingerprint": None,
    }


def _compress_raw_metadata_text(raw_text: Any) -> Optional[bytes]:
    """Gzip the L3 raw-metadata envelope captured by the parser.

    Returns None for anything unusable so scans never fail because of the
    optional retention feature.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    try:
        return gzip.compress(raw_text.encode("utf-8"))
    except Exception as exc:
        logger.debug("raw metadata compression failed: %s", exc)
        return None


def _build_metadata_success_record(
    image_path: str,
    filename: str,
    stat_result: os.stat_result,
    metadata: Dict[str, Any],
    *,
    content_fingerprint: Optional[str] = None,
    sidecar_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert parsed metadata into a database row update."""
    metadata_json = compact_metadata_json(metadata.get("metadata"))
    metadata_error = metadata.get("metadata_error")

    gen_params = metadata.get("metadata", {}).get("_parsed", {}).get("generation_params") or {}
    model_hash = gen_params.get("model_hash")

    return {
        "path": image_path,
        "filename": filename,
        "generator": metadata["generator"],
        "prompt": metadata["prompt"],
        "sidecar_caption": metadata.get("sidecar_caption"),
        # What the parse detected. Carried for readers of the record only: the DB
        # write boundary re-derives the marker from the caption text it actually
        # stores, so a stale or missing value here cannot desync the pair.
        "sidecar_caption_format": metadata.get("sidecar_caption_format"),
        "negative_prompt": metadata["negative_prompt"],
        "metadata_json": metadata_json,
        "width": metadata["width"],
        "height": metadata["height"],
        "file_size": int(stat_result.st_size),
        "checkpoint": metadata["checkpoint"],
        "loras": metadata["loras"],
        "library_order_time": datetime.fromtimestamp(stat_result.st_mtime),
        "source_file_mtime": datetime.fromtimestamp(stat_result.st_mtime),
        "created_at": datetime.fromtimestamp(stat_result.st_mtime),
        "model_hash": model_hash,
        "is_readable": True,
        "read_error": normalize_reported_cause(metadata_error) if metadata_error else None,
        "source_mtime_ns": int(stat_result.st_mtime_ns),
        "source_size": int(stat_result.st_size),
        "metadata_status": "error" if metadata_error else "complete",
        "content_fingerprint": content_fingerprint,
        # The sidecars this parse actually saw. Storing it here is what lets the
        # next scan tell an edited caption from an untouched one.
        "sidecar_fingerprint": sidecar_fingerprint,
        "raw_metadata_gz": _compress_raw_metadata_text(metadata.get("raw_metadata_text")),
    }


def _build_metadata_error_record(
    image_path: str,
    filename: str,
    stat_result: Optional[os.stat_result],
    error_message: str,
) -> Dict[str, Any]:
    """Build a DB record for files that failed metadata parsing.

    ``read_error`` is the same user-facing cause the move path stores: one line,
    filename instead of the folder, so a later attention list can print it.
    """
    current_file_time = datetime.fromtimestamp(stat_result.st_mtime) if stat_result else None
    source_mtime_ns = int(stat_result.st_mtime_ns) if stat_result else None
    source_size = int(stat_result.st_size) if stat_result else None

    return {
        "path": image_path,
        "filename": filename,
        "generator": "unknown",
        "prompt": None,
        "sidecar_caption": None,
        "sidecar_caption_format": None,
        "negative_prompt": None,
        "metadata_json": compact_metadata_json({}),
        "width": None,
        "height": None,
        "file_size": source_size,
        "checkpoint": None,
        "loras": [],
        "library_order_time": current_file_time,
        "source_file_mtime": current_file_time,
        "created_at": current_file_time,
        "model_hash": None,
        "is_readable": False,
        "read_error": describe_readability_failure(error_message),
        "source_mtime_ns": source_mtime_ns,
        "source_size": source_size,
        "metadata_status": "error",
        "content_fingerprint": None,
        # An unreadable row is never an unchanged scan hit, so there is nothing
        # for a sidecar fingerprint to gate; leaving it None keeps any stored
        # value (COALESCE in _upsert_image_record) rather than inventing one.
        "sidecar_fingerprint": None,
    }
