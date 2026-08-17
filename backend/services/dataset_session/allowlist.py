"""Session-path allowlist — the /api/dataset/local-thumbnail security gate.

Moved from services/dataset_session_service.py (decomposition 2026-07). _session_path_cache is the pinned
in-place CONTAINER (never rebound anywhere): this module owns the
one dict object, the facade re-exports the SAME object, and readers
(tests/test_dataset_session_service.py:439 calls _session_path_cache.clear())
mutate it in place. _session_path_lock travels with it.

Bodies are VERBATIM except three seam lines:
  * _register_session_paths reads _SESSION_PATH_CACHE_MAX through _svc()
    (twice) — the pin suite patches it on the facade module object.
  * register_scan_manifest_paths_for_session resolves
    iter_scan_manifest_paths through _svc() — a module-level import of
    manifest_store here would be a load cycle (manifest_store imports
    _register_session_paths from this module), and the call-time facade
    lookup preserves the monolith's patch-visible module-global read.

SECURITY (pinned): explicit scans/uploads call _register_session_paths and
grant identity-bound project-save authorization. Manifest pagination,
audit/export iteration, and persisted Dataset Project reads call
_register_thumbnail_paths and grant only thumbnail access.
resolve_paths_for_dataset must NOT grant either permission.
"""
from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal

from PIL import Image

from services.dataset_session.ids_and_items import _ds_id_for_path
from utils.path_validation import normalize_user_path


def _svc():
    """Resolve facade-patched seams through services.dataset_session_service at call time.

    The pin suite patches ``_SESSION_PATH_CACHE_MAX`` on the facade module
    object, and
    ``iter_scan_manifest_paths`` must resolve through the facade at call time
    (a module-level import here would cycle with manifest_store). The lazy
    import avoids a facade<->submodule load cycle.
    """
    import services.dataset_session_service as dataset_session_service

    return dataset_session_service

# ------------------------------ session path allowlist ------------------------------

# How long an in-memory "this path was served by a Dataset Maker session"
# entry stays trusted. Long enough for a long export/audit pass, short
# enough that a stale process cannot be used to read arbitrary files
# hours after the user walked away.
_SESSION_PATH_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# Bounded LRU of (abs_path_str -> expiry_timestamp). A path is only
# resolvable by the local-thumbnail endpoint if it appears here AND the
# expiry has not passed. Entries are added by scan_folder_for_dataset,
# upload_files_for_dataset, iter_scan_manifest_entries, and verified persisted
# Dataset Project reads — i.e. every path the backend chose to surface to the
# client. The cap is
# generous (a 100k-image manifest) but bounded so a malicious or buggy
# client cannot grow this without limit.
_SESSION_PATH_CACHE_MAX = 200_000
_session_path_cache: "Dict[str, float]" = {}
_project_save_authorization_cache: "Dict[str, tuple[float, int, int, int, int]]" = {}
_session_path_lock = threading.Lock()


def _normalize_session_path(raw: str) -> str:
    """Canonical key for the session path cache.

    Uses ``resolve(strict=False)`` so a path that has since been moved
    (e.g. after a `move` export) still matches its old cache entry. We
    intentionally do NOT require existence here; existence is checked
    by the caller before serving bytes.
    """
    try:
        return str(Path(normalize_user_path(str(raw))).resolve(strict=False))
    except (OSError, ValueError):
        return str(raw or "").strip()


def _clean_session_paths(abs_paths: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    for raw in abs_paths or []:
        key = _normalize_session_path(str(raw))
        if key:
            cleaned.append(key)
    return cleaned


def _evict_expired_or_overflow_entries(now: float) -> None:
    expired_thumbnail_keys = [
        key for key, expiry in _session_path_cache.items() if expiry < now
    ]
    for key in expired_thumbnail_keys:
        _session_path_cache.pop(key, None)
    expired_save_keys = [
        key
        for key, authorization in _project_save_authorization_cache.items()
        if authorization[0] < now
    ]
    for key in expired_save_keys:
        _project_save_authorization_cache.pop(key, None)

    max_entries = _svc()._SESSION_PATH_CACHE_MAX
    thumbnail_overflow = len(_session_path_cache) - max_entries
    if thumbnail_overflow > 0:
        ordered_thumbnail_keys = sorted(
            _session_path_cache,
            key=lambda key: _session_path_cache[key],
        )
        for key in ordered_thumbnail_keys[:thumbnail_overflow]:
            _session_path_cache.pop(key, None)
    save_overflow = len(_project_save_authorization_cache) - max_entries
    if save_overflow > 0:
        ordered_save_keys = sorted(
            _project_save_authorization_cache,
            key=lambda key: _project_save_authorization_cache[key][0],
        )
        for key in ordered_save_keys[:save_overflow]:
            _project_save_authorization_cache.pop(key, None)


def _register_thumbnail_paths(abs_paths: Iterable[str]) -> None:
    """Grant thumbnail access without granting project-save authorization."""
    cleaned = _clean_session_paths(abs_paths)
    if not cleaned:
        return
    now = time.monotonic()
    expiry = now + _SESSION_PATH_TTL_SECONDS
    with _session_path_lock:
        for key in cleaned:
            _session_path_cache[key] = expiry
        _evict_expired_or_overflow_entries(now)


def _read_project_save_identity(path: str) -> tuple[int, int, int, int] | None:
    try:
        source_stat = os.lstat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(source_stat.st_mode):
        return None
    return (
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_dev,
        source_stat.st_ino,
    )


def _register_session_paths(abs_paths: Iterable[str]) -> None:
    """Grant thumbnail access and identity-bound project-save authorization.

    Called only by explicit scan/upload/manifest code paths that surfaced the
    file to the user. A later save must still match the identity captured here.
    """
    cleaned = _clean_session_paths(abs_paths)
    if not cleaned:
        return
    identities = {
        key: identity
        for key in cleaned
        if (identity := _read_project_save_identity(key)) is not None
    }
    now = time.monotonic()
    expiry = now + _SESSION_PATH_TTL_SECONDS
    with _session_path_lock:
        for key in cleaned:
            _session_path_cache[key] = expiry
        for key, identity in identities.items():
            _project_save_authorization_cache[key] = (expiry, *identity)
        _evict_expired_or_overflow_entries(now)


def project_save_authorization_status(
    raw_path: str,
    size: int,
    mtime_ns: int,
    device: int,
    inode: int,
) -> Literal["authorized", "missing", "changed"]:
    """Compare a save request with the identity from the last explicit import."""
    key = _normalize_session_path(raw_path)
    if not key:
        return "missing"
    now = time.monotonic()
    with _session_path_lock:
        authorization = _project_save_authorization_cache.get(key)
        if authorization is None:
            return "missing"
        if authorization[0] < now:
            _project_save_authorization_cache.pop(key, None)
            return "missing"
        expected_identity = authorization[1:]
        if expected_identity != (size, mtime_ns, device, inode):
            return "changed"
        _project_save_authorization_cache[key] = (
            now + _SESSION_PATH_TTL_SECONDS,
            *expected_identity,
        )
    return "authorized"


def is_path_in_dataset_session(raw_path: str) -> bool:
    """Return True if ``raw_path`` was surfaced by an active Dataset Maker session.

    This is the gate the local-thumbnail endpoint uses: a path is only
    readable as a thumbnail if the backend itself put it in front of
    the client via folder-scan, upload-files, a scan-token manifest, or a
    verified persisted Dataset Project.
    That closes the hole where ``?path=<anywhere>`` could read arbitrary
    image bytes off the host.
    """
    key = _normalize_session_path(str(raw_path or ""))
    if not key:
        return False
    now = time.monotonic()
    with _session_path_lock:
        expiry = _session_path_cache.get(key)
        if expiry is None:
            return False
        if expiry < now:
            _session_path_cache.pop(key, None)
            return False
        # Refresh on access so an active editing session keeps its paths.
        _session_path_cache[key] = now + _SESSION_PATH_TTL_SECONDS
    return True


def register_scan_manifest_paths_for_session(scan_token: str) -> int:
    """Trust every path in a scan-token manifest for the local-thumbnail endpoint.

    Called when a manifest is iterated for export/audit/preview. Returns
    the number of paths registered. Cheap to call repeatedly: the cache
    is a dict keyed by normalized path, so re-registration just refreshes
    the expiry.
    """
    try:
        paths = list(_svc().iter_scan_manifest_paths(scan_token))
    except ValueError:
        return 0
    _register_thumbnail_paths(paths)
    return len(paths)


def register_scan_manifest_paths_for_project_save(scan_token: str) -> int:
    """Capture save identities for every path in one new explicit folder scan."""
    try:
        paths = list(_svc().iter_scan_manifest_paths(scan_token))
    except ValueError:
        return 0
    _register_session_paths(paths)
    return len(paths)


def virtual_image_record_for_path(abs_path: str, *, read_dimensions: bool = True) -> Dict[str, Any]:
    """Return a dict shaped like a row from ``database.get_images_by_ids``
    so existing pipelines (export, audit) can consume it without
    branching on the source.

    The synthetic record has:
      - ``id``: 0 (sentinel; never stored)
      - ``path``: absolute path
      - ``filename``: basename
      - ``ai_caption``, ``rating``, ``prompt``, ``negative_prompt``: empty
      - ``width`` / ``height``: filled when readable, else None
    """
    p = Path(abs_path)
    record: Dict[str, Any] = {
        "id": 0,
        "path": str(p),
        "filename": p.name,
        "ai_caption": None,
        "rating": None,
        "prompt": None,
        "negative_prompt": None,
        "checkpoint": None,
        "metadata": None,
        "metadata_json": None,
        "loras": None,
        "model_hash": None,
        "width": None,
        "height": None,
        "ds_id": _ds_id_for_path(str(p)),
    }
    if read_dimensions:
        try:
            with Image.open(p) as img:
                record["width"], record["height"] = img.size
        except Exception:  # noqa: BLE001 - non-fatal here; export will still work
            pass
    return record


