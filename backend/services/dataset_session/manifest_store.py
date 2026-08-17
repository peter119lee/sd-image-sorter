"""Scan-token manifest store for the Dataset Maker session service.

Moved from services/dataset_session_service.py (decomposition 2026-07) with every body VERBATIM except one seam:
the five _get_scan_dir() call sites resolve through _svc() at call time,
because the REBIND global pair _SCAN_DIR/_get_scan_dir stays homed on the
facade FILE and the pin suite patches it there
(tests/test_dataset_session_pins.py monkeypatch.setattr(dss, "_get_scan_dir",
...)); a bare local call here would make that patch miss and manifests would
hit the real data tree.

iter_scan_manifest_entries keeps its pinned side effect: surfaced paths are
registered with the thumbnail allowlist ONLY on full consumption of the
generator (the trailing _register_thumbnail_paths calls) — do
not "optimize" them into per-yield calls.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_session.allowlist import _register_thumbnail_paths

# Same logger channel as the pre-split monolith (report seam: logger verbatim).
logger = logging.getLogger("services.dataset_session_service")


def _svc():
    """Resolve facade-patched seams through services.dataset_session_service at call time.

    The pin suite patches ``_get_scan_dir`` on the facade module object; a bare
    local call here would freeze the unpatched binding. The lazy import avoids a facade<->submodule load
    cycle.
    """
    import services.dataset_session_service as dataset_session_service

    return dataset_session_service

_SCAN_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")

# Scan-token manifests under data/dataset-scans/ used to accumulate
# forever — every folder-scan wrote a new NDJSON file and nothing ever
# removed it. This TTL cap (in seconds) is enforced by
# ``purge_expired_scan_manifests`` which runs on app startup and after
# each successful folder-scan. Old tokens are safe to delete: the
# frontend re-issues a fresh scan when an expired token is referenced.
SCAN_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _scan_manifest_path(scan_token: str) -> Path:
    token = str(scan_token or "")
    if not _SCAN_TOKEN_RE.fullmatch(token):
        raise ValueError("Invalid folder scan token")
    return _svc()._get_scan_dir() / f"{token}.json"


def purge_expired_scan_manifests(*, max_age_seconds: int = SCAN_TOKEN_TTL_SECONDS) -> int:
    """Delete scan-token manifests older than ``max_age_seconds``.

    Returns the number of token files removed. Safe to call repeatedly:
    only files matching ``<32-hex>.json`` / ``<32-hex>.paths.jsonl`` /
    ``<32-hex>.tmp`` under the scan dir are considered, so unrelated
    files in ``data/dataset-scans/`` are left alone.

    This closes the unbounded-growth hole where every folder-scan wrote
    a new NDJSON manifest and nothing ever removed them — long-running
    installs accumulated thousands of stale manifest files.
    """
    import time as _time

    scan_dir = _svc()._get_scan_dir()
    cutoff = _time.time() - int(max_age_seconds)
    removed = 0
    token_pattern = re.compile(r"^([a-f0-9]{32})(?:\.paths\.jsonl|\.json|\.tmp)$")
    try:
        for entry in os.scandir(scan_dir):
            if not entry.is_file():
                continue
            match = token_pattern.fullmatch(entry.name)
            if not match:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    os.unlink(entry.path)
                    removed += 1
                except OSError:
                    # Best-effort: a locked/in-use manifest is left for
                    # the next sweep rather than crashing the caller.
                    continue
    except OSError:
        return removed
    return removed


def _scan_manifest_paths_path(scan_token: str) -> Path:
    token = str(scan_token or "")
    if not _SCAN_TOKEN_RE.fullmatch(token):
        raise ValueError("Invalid folder scan token")
    return _svc()._get_scan_dir() / f"{token}.paths.jsonl"


def _iter_folder_image_entries(base: Path, recursive: bool) -> Iterator[Dict[str, Any]]:
    """Yield lightweight image entries using ``os.scandir``.

    ``Path.rglob`` + per-file ``resolve`` is very slow on large Windows-mounted
    folders under WSL. Dataset Maker only needs stable absolute paths for the
    initial manifest; dimensions/thumbnails are hydrated later when visible.
    """
    stack = [os.fspath(base)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if recursive and entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=True):
                            continue
                    except OSError:
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in ALLOWED_IMAGE_EXTENSIONS:
                        continue
                    yield {
                        "path": os.path.abspath(entry.path),
                        "filename": entry.name,
                    }
        except OSError as exc:
            logger.debug("dataset-session: cannot scan %s: %s", current, exc)


def _build_scan_manifest(base: Path, recursive: bool) -> Tuple[str, Dict[str, Any]]:
    """Walk a folder once and cache image paths for later pages.

    Metadata and base64 thumbnails are intentionally generated page-by-page so
    a 100k-image folder does not produce a huge JSON response or DOM payload.
    The path manifest is NDJSON, not a JSON array, so a 1M-image folder can be
    paged and consumed by backend jobs without materialising every path in
    Python memory.
    """
    token = uuid.uuid4().hex
    meta_path = _scan_manifest_path(token)
    paths_path = _scan_manifest_paths_path(token)
    tmp_meta = meta_path.with_suffix(".tmp")
    tmp_paths = paths_path.with_suffix(".tmp")
    total = 0
    try:
        with tmp_paths.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in _iter_folder_image_entries(base, recursive):
                handle.write(json.dumps(entry, ensure_ascii=False))
                handle.write("\n")
                total += 1
        tmp_paths.replace(paths_path)
    except Exception:
        tmp_paths.unlink(missing_ok=True)
        paths_path.unlink(missing_ok=True)
        raise

    manifest = {
        "folder_path": str(base),
        "recursive": bool(recursive),
        "paths_file": paths_path.name,
        "manifest_format": "jsonl-items-v2",
        "total_files_seen": total,
    }
    try:
        tmp_meta.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tmp_meta.replace(meta_path)
    except Exception:
        tmp_meta.unlink(missing_ok=True)
        paths_path.unlink(missing_ok=True)
        raise
    return token, manifest


def _load_scan_manifest(scan_token: str) -> Dict[str, Any]:
    path = _scan_manifest_path(scan_token)
    if not path.exists():
        raise ValueError("Folder scan token expired. Scan the folder again.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Folder scan token is corrupt. Scan the folder again.") from exc
    if not isinstance(data, dict):
        raise ValueError("Folder scan token is invalid. Scan the folder again.")
    if isinstance(data.get("paths"), list):
        return data
    paths_file = data.get("paths_file")
    if not isinstance(paths_file, str) or not paths_file:
        raise ValueError("Folder scan token is invalid. Scan the folder again.")
    paths_path = _svc()._get_scan_dir() / Path(paths_file).name
    if not paths_path.exists():
        raise ValueError("Folder scan token path manifest expired. Scan the folder again.")
    return data


def iter_scan_manifest_paths(scan_token: str) -> Iterator[str]:
    """Yield cached folder-scan paths without loading the whole manifest.

    Old JSON-array manifests are still supported for compatibility with jobs
    started before this change, but new scans use a streaming NDJSON path file.
    """
    manifest = _load_scan_manifest(scan_token)
    legacy_paths = manifest.get("paths")
    if isinstance(legacy_paths, list):
        for path in legacy_paths:
            value = str(path or "").strip()
            if value:
                yield value
        return

    for entry in iter_scan_manifest_entries(scan_token):
        value = str(entry.get("path") or "").strip()
        if value:
            yield value


def iter_scan_manifest_entries(scan_token: str) -> Iterator[Dict[str, Any]]:
    """Yield cached folder-scan entries without loading the whole manifest.

    Each yielded entry's ``path`` is registered with the Dataset Maker
    session path allowlist so the local-thumbnail endpoint can later
    serve it. This is the security boundary that stops
    ``/api/dataset/local-thumbnail?path=<anywhere>`` from reading
    arbitrary host files: only paths the backend itself surfaced here
    become thumbnail-readable.
    """
    manifest = _load_scan_manifest(scan_token)
    legacy_paths = manifest.get("paths")
    if isinstance(legacy_paths, list):
        legacy_registered: List[str] = []
        for index, path in enumerate(legacy_paths):
            value = str(path or "").strip()
            if value:
                p = Path(value)
                legacy_registered.append(value)
                yield {
                    "path": value,
                    "filename": p.name,
                    "scan_index": index,
                    "size": 0,
                    "mtime": 0.0,
                }
        _register_thumbnail_paths(legacy_registered)
        return

    paths_file = str(manifest.get("paths_file") or "")
    paths_path = _svc()._get_scan_dir() / Path(paths_file).name
    streamed_registered: List[str] = []
    try:
        with paths_path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    path = str(value.get("path") or "").strip()
                    if not path:
                        continue
                    streamed_registered.append(path)
                    yield {
                        "path": path,
                        "filename": str(value.get("filename") or Path(path).name),
                        "scan_index": int(value.get("scan_index", index) or index),
                        "size": int(value.get("size", 0) or 0),
                        "mtime": float(value.get("mtime", 0.0) or 0.0),
                    }
                    continue
                path = str(value or "").strip()
                if path:
                    streamed_registered.append(path)
                    yield {
                        "path": path,
                        "filename": Path(path).name,
                        "scan_index": index,
                        "size": 0,
                        "mtime": 0.0,
                    }
        _register_thumbnail_paths(streamed_registered)
    except OSError as exc:
        raise ValueError("Folder scan token path manifest is unreadable. Scan the folder again.") from exc


def get_scan_manifest_paths(scan_token: str) -> List[str]:
    """Return cached folder-scan paths for compatibility tests/small callers.

    Large jobs should use ``iter_scan_manifest_paths`` so they do not hold a
    100k-1M path manifest in memory.
    """
    return list(iter_scan_manifest_paths(scan_token))


def count_scan_manifest_paths(scan_token: str, exclude_paths: Optional[Iterable[str]] = None) -> int:
    exclude_set = {str(path) for path in (exclude_paths or []) if path}
    if not exclude_set:
        manifest = _load_scan_manifest(scan_token)
        if "total_files_seen" in manifest:
            return int(manifest.get("total_files_seen") or 0)
    return sum(1 for path in iter_scan_manifest_paths(scan_token) if str(path) not in exclude_set)


