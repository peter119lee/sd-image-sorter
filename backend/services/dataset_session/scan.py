"""Folder-scan + path-resolution entry points for the Dataset Maker session.

Moved from services/dataset_session_service.py (decomposition 2026-07). Bodies are VERBATIM except one seam line:
the page-size clamp inside scan_folder_for_dataset reads MAX_SCAN_RESULTS
through _svc() at call time because the reader suite patches it on the
facade module object (tests/test_dataset_session_service.py:299); the
def-time signature default keeps the same value the monolith baked in at
import.

scan_folder_for_dataset keeps the pinned surfaced-paths registration: only
the paths returned in THIS page become local-thumbnail readable. A new
explicit scan separately captures save identities for its complete manifest;
later pagination must never refresh those identities.
resolve_paths_for_dataset stays permissive and must NOT register (SECURITY
pin).
"""
from __future__ import annotations

import logging
from itertools import islice
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_session.allowlist import (
    _register_thumbnail_paths,
    register_scan_manifest_paths_for_project_save,
)
from services.dataset_session.ids_and_items import (
    _manifest_item_for_path,
    _session_items_for_page_paths,
)
from services.dataset_session.manifest_store import (
    _build_scan_manifest,
    _load_scan_manifest,
    iter_scan_manifest_entries,
    iter_scan_manifest_paths,
)
from utils.path_validation import normalize_user_path, validate_folder_path

# Same logger channel as the pre-split monolith (report seam: logger verbatim).
logger = logging.getLogger("services.dataset_session_service")


def _svc():
    """Resolve facade-patched seams through services.dataset_session_service at call time.

    The reader suite patches ``MAX_SCAN_RESULTS`` on the facade module object;
    the clamp must read that binding, exactly as the monolith's module-global
    read did. The lazy import avoids
    a facade<->submodule load cycle.
    """
    import services.dataset_session_service as dataset_session_service

    return dataset_session_service

# Cap each folder-scan preview page to keep thumbnail payloads bounded.
# This is a page size, not a dataset-size cap: large folder scans still
# return a lightweight manifest so "apply to all" operations include images
# whose thumbnails have not been loaded yet.
MAX_SCAN_RESULTS = 5_000



def scan_folder_for_dataset(
    folder_path: str,
    *,
    recursive: bool = False,
    limit: int = MAX_SCAN_RESULTS,
    offset: int = 0,
    scan_token: Optional[str] = None,
    include_thumbnails: bool = True,
) -> Dict[str, Any]:
    """Scan a folder for images and return session-ready metadata.

    Returns a dict with::

        {
            "folder_path": str,
            "items": [
                {
                    "ds_id": "ds:<sha1>",
                    "abs_path": str,
                    "filename": str,
                    "width": int,
                    "height": int,
                    "mtime": float,
                    "size": int,
                    "thumb_b64": str,  # 'data:image/jpeg;base64,...' WITHOUT the prefix
                },
                ...
            ],
            "total_files_seen": int,    # before the limit cap
            "skipped_unreadable": int,
            "truncated": bool,
        }

    Does NOT write anything to the main ``images.db``.
    """
    normalized_limit = max(1, min(int(limit or _svc().MAX_SCAN_RESULTS), int(_svc().MAX_SCAN_RESULTS)))
    normalized_offset = max(0, int(offset or 0))

    if scan_token:
        manifest = _load_scan_manifest(scan_token)
        token = str(scan_token)
        base = Path(str(manifest.get("folder_path") or folder_path or ".")).resolve()
        total_seen = int(manifest.get("total_files_seen") or 0)
    else:
        if not folder_path:
            raise ValueError("Folder path is required")
        normalized = normalize_user_path(folder_path)
        is_valid, error = validate_folder_path(normalized, allow_create=False)
        if not is_valid:
            raise ValueError(error or "Invalid folder path")
        base = Path(normalized).resolve()
        token, manifest = _build_scan_manifest(base, recursive)
        total_seen = int(manifest.get("total_files_seen") or 0)
        register_scan_manifest_paths_for_project_save(token)

    end_offset = min(total_seen, normalized_offset + normalized_limit)
    if include_thumbnails:
        page_paths = list(islice(iter_scan_manifest_paths(token), normalized_offset, normalized_offset + normalized_limit))
        items, skipped = _session_items_for_page_paths(page_paths, start_index=normalized_offset)
    else:
        page_entries = list(islice(iter_scan_manifest_entries(token), normalized_offset, normalized_offset + normalized_limit))
        items = [
            _manifest_item_for_path(entry, scan_index)
            for scan_index, entry in enumerate(page_entries, start=normalized_offset)
        ]
        skipped = 0

    has_more = end_offset < total_seen

    # Trust the paths we just surfaced so the local-thumbnail endpoint
    # can serve them. Without this gate the endpoint would be an
    # arbitrary-host-file read oracle.
    _register_thumbnail_paths(
        item.get("abs_path") for item in items if item.get("abs_path")
    )

    response = {
        "folder_path": str(base),
        "items": items,
        "total_files_seen": total_seen,
        "skipped_unreadable": skipped,
        "truncated": has_more,
        "scan_token": token,
        "offset": normalized_offset,
        "next_offset": end_offset if has_more else None,
        "has_more": has_more,
        "page_size": normalized_limit,
    }
    return response


def resolve_paths_for_dataset(paths: Iterable[str]) -> List[str]:
    """Validate + normalise a list of image paths supplied by the client.

    Returns the absolute paths in the original order. Skips silently:
      - paths that don't exist
      - paths that resolve to a directory (the client should not send those)
      - paths whose extension isn't a recognised image type

    Does NOT enforce ``allowed_base`` because the dataset session is
    deliberately allowed to span arbitrary user folders. Path-traversal
    safety still comes from ``normalize_user_path`` + ``Path.resolve``.

    NOTE: this helper is intentionally permissive. Callers that serve
    content to the browser (e.g. ``/api/dataset/local-thumbnail``) must
    additionally call :func:`is_path_in_dataset_session` so a client
    cannot turn the endpoint into an arbitrary-host-file read oracle.
    """
    out: List[str] = []
    seen: set = set()
    for raw in paths or []:
        if not raw:
            continue
        try:
            normalized = normalize_user_path(str(raw))
            resolved = Path(normalized).resolve()
        except (OSError, ValueError) as exc:
            logger.debug("dataset-session: cannot resolve %r: %s", raw, exc)
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        if resolved.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            continue
        s = str(resolved)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


