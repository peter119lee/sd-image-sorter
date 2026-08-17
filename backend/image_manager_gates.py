"""Pure metadata/fingerprint gate helpers for image_manager.

Moved verbatim from image_manager.py (decomposition 2026-07, stage 1). Stateless — no DB, no filesystem
writes, and no monkeypatch-seam reads: every input is an argument, and
PARSED_METADATA_VERSION (never patched; pinned by
tests/test_metadata_parser_pins.py) is imported from its origin module
exactly as the facade did. The facade re-imports every name below so
``image_manager.<name>`` keeps resolving for the pin suite and the scan
pipeline."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ALLOWED_IMAGE_EXTENSIONS as IMAGE_EXTENSIONS
from metadata_parser import PARSED_METADATA_VERSION


def _deserialize_loras(value: Any) -> Optional[List[str]]:
    """Best-effort deserialize of the stored loras JSON column."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _source_fingerprint_matches(existing: Optional[Dict[str, Any]], stat_result: os.stat_result) -> bool:
    """Return True when the indexed source fingerprint matches the current file."""
    if not existing:
        return False

    try:
        source_mtime_ns = int(existing.get("source_mtime_ns"))
        source_size = int(existing.get("source_size"))
    except (TypeError, ValueError):
        return False

    return source_mtime_ns == int(stat_result.st_mtime_ns) and source_size == int(stat_result.st_size)


def _has_source_fingerprint(existing: Optional[Dict[str, Any]]) -> bool:
    """Return True when the row already stores a usable source fingerprint."""
    if not existing:
        return False
    try:
        int(existing.get("source_mtime_ns"))
        int(existing.get("source_size"))
        return True
    except (TypeError, ValueError):
        return False


def _is_unchanged_scan_hit(existing: Optional[Dict[str, Any]], stat_result: os.stat_result) -> bool:
    """Skip reparsing files whose source fingerprint and metadata status still match."""
    if not existing or not existing.get("is_readable", 1):
        return False
    if existing.get("metadata_status") != "complete":
        return False
    if _needs_content_fingerprint_backfill(existing):
        return False
    if _needs_metadata_parser_upgrade(existing):
        return False
    return _source_fingerprint_matches(existing, stat_result)


def _has_cached_derived_state(existing: Optional[Dict[str, Any]]) -> bool:
    """Return True when the indexed row already has derived data that may need preservation."""
    if not existing:
        return False
    return any([
        existing.get("tagged_at") is not None,
        existing.get("ai_caption") is not None,
        existing.get("aesthetic_score") is not None,
        bool(existing.get("has_embedding")),
        bool(existing.get("has_artist_predictions")),
    ])


def _needs_content_fingerprint_backfill(existing: Optional[Dict[str, Any]]) -> bool:
    """Return True when the row has derived state but still lacks a content fingerprint."""
    if not _has_cached_derived_state(existing):
        return False
    return not bool(existing.get("content_fingerprint"))


def _stored_parsed_metadata_version(existing: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the parser version stored in the compact metadata payload."""
    if not existing:
        return None
    metadata_json = existing.get("metadata_json")
    if isinstance(metadata_json, bytes):
        metadata_json = metadata_json.decode("utf-8", errors="replace")
    if isinstance(metadata_json, str):
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    elif isinstance(metadata_json, dict):
        metadata = metadata_json
    else:
        return None

    parsed = metadata.get("_parsed") if isinstance(metadata, dict) else None
    if not isinstance(parsed, dict):
        return None

    try:
        return int(parsed.get("version"))
    except (TypeError, ValueError):
        return None


def _needs_metadata_parser_upgrade(existing: Optional[Dict[str, Any]]) -> bool:
    """Return True when an unchanged indexed row was parsed by an older parser.

    Every format the scanner indexes is gated on PARSED_METADATA_VERSION.
    This used to be a per-format map that pinned JPEG at a literal 7 and
    omitted every extension except PNG, so once the parser reached 8 a JPEG
    or WebP row indexed by the v7 parser could never be revisited: its
    fingerprint still matched, its status was already ``complete``, and the
    version gate said it was current. Parser improvements are not
    format-specific — the caption-sidecar fallback that recovers these files'
    prompts applies to all of them — so a per-format literal cannot be kept
    correct, and going stale silently freezes real user data.
    """
    source_path = str((existing or {}).get("path") or (existing or {}).get("filename") or "")
    if Path(source_path).suffix.lower() not in IMAGE_EXTENSIONS:
        return False

    stored_version = _stored_parsed_metadata_version(existing)
    return stored_version is None or stored_version < PARSED_METADATA_VERSION


def _should_compute_content_fingerprint(existing: Optional[Dict[str, Any]]) -> bool:
    """Only compute fingerprints when they are needed for derived-state safety."""
    if not existing:
        return False
    return bool(existing.get("content_fingerprint")) or _needs_content_fingerprint_backfill(existing)
