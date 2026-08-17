"""Dataset Session service — the "small gallery" workspace.

Implements the v3.2.2 vision from issue #5 point 5: a Dataset Maker that
can pull images from the main library OR scan a folder directly without
adding rows to the main ``images.db``.

Goals
-----
1. Folder import: ``scan_folder_for_dataset(folder_path, recursive)``
   returns a list of session items with thumbnail+metadata, derived
   purely from on-disk inspection. No DB writes. Each item carries a
   stable ``ds_id`` (``ds:<sha1(abspath)[:16]>``) the frontend can use
   to reference the local item across requests.
2. Path resolution: ``resolve_paths(paths)`` validates a list of file
   paths against ``ALLOWED_IMAGE_EXTENSIONS`` and returns absolute
   normalised paths the rest of the dataset pipeline can consume.

What this module is NOT
-----------------------
* It does not own a database table — the dataset session lives entirely
  in the client. The backend just answers "scan this folder" and
  "write a sidecar at this path" requests.
* It does not load AI models. Smart Tag / aesthetic / similarity reach
  into ``oppai_oracle_tagger`` / ``aesthetic`` / ``similarity`` directly
  the same way they do for DB-backed images; the only difference is
  that the caller passes a path string rather than an image_id.

Design rationale
----------------
A "real" small gallery would need a ``dataset_session`` SQL table and
session-scoped IDs; that's a significant migration. For v3.2.2 we keep
the data path-only so:

* The big-library invariant ("scanning a folder always adds to the
  main DB") is preserved exactly — the new flow just doesn't use it.
* Captions for local items live in the frontend ``localStorage`` keyed
  by absolute path, so they survive page reloads but are decoupled
  from the DB.
* If the user later wants to promote local items to the main library,
  they can run the regular ``/api/scan`` over the same folder.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from PIL import Image, UnidentifiedImageError

from config import ALLOWED_IMAGE_EXTENSIONS
from utils.path_validation import normalize_user_path, validate_folder_path


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the id/item builders, the scan-token manifest
# store, the session-path allowlist, and the scan/upload entry points live in
# the services/dataset_session/ package, re-imported below. THIS module
# remains a real FILE and the single monkeypatch surface
# (tests/test_dataset_session_pins.py):
#   * The two REBIND global pairs stay DEFINED here — _SCAN_DIR with
#     _get_scan_dir, and _UPLOAD_DIR with _get_upload_dir. A ``global``
#     rebind only affects the defining module's binding, and both seams are
#     patched on THIS module object (dss._get_scan_dir by the pin suite;
#     dataset_session_module._UPLOAD_DIR by the reader suite), so the moved
#     callers resolve them back through _svc() at call time instead of
#     binding them locally.
#   * _session_path_cache is a pure in-place CONTAINER (never rebound): it is
#     defined ONCE in services/dataset_session/allowlist.py and re-exported
#     here as the SAME dict object, so the reader suite's
#     ``_session_path_cache.clear()`` and the live /local-thumbnail gate hit
#     one dict. _session_path_lock travels with it.
#   * MAX_SCAN_RESULTS, _SESSION_PATH_CACHE_MAX, and _try_import_rarfile are
#     patched on this namespace by the suites; the moved readers
#     (scan_folder_for_dataset's clamp, _register_session_paths' LRU bound,
#     _extract_rar_into_dataset's soft import) read them back through _svc()
#     at call time.
#   * The header import block above is kept verbatim (per-file F401 ignore in
#     pyproject.toml) so every historical attribute keeps resolving here.
#   * Downstream identity seams (dataset_export/planning.py, the
#     dataset_export_service facade, dataset_export/engine.py, and
#     routers/dataset.py origin-import count/iter_scan_manifest_paths,
#     virtual_image_record_for_path, MAX_SCAN_RESULTS, and the scan/upload
#     entry points) keep ``is`` identity through these re-exports.
# ---------------------------------------------------------------------------
from services.dataset_session.ids_and_items import (
    SCAN_THUMB_WORKERS,
    THUMBNAIL_JPEG_QUALITY,
    THUMBNAIL_MAX_PX,
    _ds_id_for_path,
    _is_image_path,
    _manifest_item_for_path,
    _read_image_metadata,
    _session_item_for_indexed_path,
    _session_item_for_path,
    _session_items_for_page_paths,
)
from services.dataset_session.manifest_store import (
    SCAN_TOKEN_TTL_SECONDS,
    _SCAN_TOKEN_RE,
    _build_scan_manifest,
    _iter_folder_image_entries,
    _load_scan_manifest,
    _scan_manifest_path,
    _scan_manifest_paths_path,
    count_scan_manifest_paths,
    get_scan_manifest_paths,
    iter_scan_manifest_entries,
    iter_scan_manifest_paths,
    purge_expired_scan_manifests,
)
from services.dataset_session.allowlist import (
    _SESSION_PATH_CACHE_MAX,
    _SESSION_PATH_TTL_SECONDS,
    _normalize_session_path,
    _register_session_paths,
    _session_path_cache,
    _session_path_lock,
    is_path_in_dataset_session,
    register_scan_manifest_paths_for_session,
    virtual_image_record_for_path,
)
from services.dataset_session.scan import (
    MAX_SCAN_RESULTS,
    resolve_paths_for_dataset,
    scan_folder_for_dataset,
)
from services.dataset_session.upload import (
    _MAX_ARCHIVE_ENTRIES,
    _MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    _ArchiveExtractResult,
    _append_upload_item,
    _extract_rar_into_dataset,
    _safe_uploaded_name,
    _try_import_rarfile,
    upload_files_for_dataset,
)


_SCAN_DIR: Optional[Path] = None


def _get_scan_dir() -> Path:
    """Return the temp manifest directory used for large folder scans."""
    global _SCAN_DIR
    if _SCAN_DIR is None or not _SCAN_DIR.exists():
        data_dir = Path(__file__).resolve().parent.parent / "data" / "dataset-scans"
        data_dir.mkdir(parents=True, exist_ok=True)
        _SCAN_DIR = data_dir
    return _SCAN_DIR


# Persistent upload directory so files survive the request lifecycle.
_UPLOAD_DIR: Optional[Path] = None


def _get_upload_dir() -> Path:
    """Return (and lazily create) a persistent temp directory for uploads."""
    global _UPLOAD_DIR
    if _UPLOAD_DIR is not None:
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        return _UPLOAD_DIR
    if _UPLOAD_DIR is None or not _UPLOAD_DIR.exists():
        # Use data/dataset-uploads so it lives alongside other runtime data
        from pathlib import Path as _P
        data_dir = _P(__file__).resolve().parent.parent / "data" / "dataset-uploads"
        data_dir.mkdir(parents=True, exist_ok=True)
        _UPLOAD_DIR = data_dir
    return _UPLOAD_DIR


