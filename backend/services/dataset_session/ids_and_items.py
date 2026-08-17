"""Item builders + thumbnail/id helpers for the Dataset Maker session.

Constants and bodies moved VERBATIM from services/dataset_session_service.py
(decomposition 2026-07). Pure leaf: no
module state and no facade-patched seams — every name here is unpatched in
the test tree (whole-tree patch census), so sibling modules origin-import these by
name and the facade re-exports them.

The ds_id producers are coupling-pinned (tests/test_dataset_session_pins.py
TestDsIdAlgorithm): _ds_id_for_path / _manifest_item_for_path /
_session_item_for_path must stamp the same id for the same manifest path.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, UnidentifiedImageError

from caption_format import caption_format_for_storage
from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_sidecar import (
    MAX_DATASET_SIDECAR_BYTES,
    read_dataset_sidecar,
)
from utils.dataset_ids import dataset_source_id

# Same logger channel as the pre-split monolith (report seam: logger verbatim).
logger = logging.getLogger("services.dataset_session_service")

# Thumbnail size for the frontend queue + editor. These are embedded directly
# in folder-scan JSON responses, so keep them small enough for 5k preview pages.
THUMBNAIL_MAX_PX = 256
THUMBNAIL_JPEG_QUALITY = 70
SCAN_THUMB_WORKERS = max(4, min(16, (os.cpu_count() or 4)))



def _sidecar_caption_fields(abs_path: str) -> Dict[str, Any]:
    """Read the ``.txt`` beside ``abs_path`` and label the format of what it holds.

    This path never touches the database: the caption is read fresh from disk on
    every scan, so ``images.sidecar_caption_format`` is not available and the
    marker has to be derived from the text just read. The label rides beside the
    text and never replaces it — the caption is returned byte-for-byte as read,
    so the caption editor can render tag chips or a prose field and still edit
    exactly what is on disk.
    """
    caption = read_dataset_sidecar(abs_path, MAX_DATASET_SIDECAR_BYTES)
    return {
        "sidecar_caption": caption,
        "sidecar_caption_format": caption_format_for_storage(caption),
    }


def _ds_id_for_path(abs_path: str) -> str:
    """Stable session id derived from the absolute file path.

    Using the path (rather than a counter) means refreshing the page or
    re-scanning the same folder produces the same ``ds_id``s, which lets
    captions stored in ``localStorage`` survive reloads.
    """
    return dataset_source_id(abs_path)


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS


def _read_image_metadata(path: Path) -> Optional[Tuple[int, int, str]]:
    """Return ``(width, height, thumbnail_b64)`` for ``path`` or None on failure.

    The thumbnail is a 256-px-on-the-long-edge JPEG-encoded base64 string
    suitable for direct injection into an ``<img src="data:image/jpeg;base64,...">``
    tag. We use JPEG instead of WEBP to maximise browser compatibility
    (Safari + older Firefox) and quality 80 to keep payload modest.
    """
    try:
        with Image.open(path) as img:
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            width, height = img.size
            # Make a thumbnail in-place; PIL preserves aspect ratio.
            img.thumbnail((THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY)
            thumb_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return width, height, thumb_b64
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("dataset-session: failed to read %s: %s", path, exc)
        return None


def _manifest_item_for_path(path: Any, index: int) -> Dict[str, Any]:
    """Return path-only item data for full-session membership.

    This intentionally avoids opening the image. It lets a 100k-image folder
    become 100k logical Dataset Maker items while thumbnails/dimensions are
    hydrated page-by-page.
    """
    if isinstance(path, dict):
        abs_path = str(path.get("path") or "").strip()
        filename = str(path.get("filename") or Path(abs_path).name)
        stat_size = int(path.get("size", 0) or 0)
        stat_mtime = float(path.get("mtime", 0.0) or 0.0)
    else:
        abs_path = str(path or "").strip()
        filename = Path(abs_path).name
        stat_size = 0
        stat_mtime = 0.0
    return {
        "ds_id": _ds_id_for_path(abs_path),
        "abs_path": abs_path,
        "filename": filename,
        "width": 0,
        "height": 0,
        "mtime": stat_mtime,
        "size": stat_size,
        "thumb_b64": "",
        "scan_index": index,
        "source_kind": "folder_path",
        "sidecar_capability": "beside_image",
        **_sidecar_caption_fields(abs_path),
    }


def _session_item_for_path(path: Path, scan_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
    try:
        stat = path.stat()
    except OSError as exc:
        logger.warning("dataset-session: stat failed for %s: %s", path, exc)
        return None

    meta = _read_image_metadata(path)
    if meta is None:
        return None

    width, height, thumb_b64 = meta
    abs_path = os.path.abspath(path)
    return {
        "ds_id": _ds_id_for_path(abs_path),
        "abs_path": abs_path,
        "filename": path.name,
        "width": width,
        "height": height,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "thumb_b64": thumb_b64,
        "scan_index": scan_index,
        "source_kind": "folder_path",
        "sidecar_capability": "beside_image",
        **_sidecar_caption_fields(abs_path),
    }


def _session_item_for_indexed_path(indexed_path: Tuple[int, str]) -> Optional[Dict[str, Any]]:
    scan_index, raw_path = indexed_path
    return _session_item_for_path(Path(raw_path), scan_index=scan_index)


def _session_items_for_page_paths(page_paths: List[str], *, start_index: int) -> Tuple[List[Dict[str, Any]], int]:
    indexed = [(start_index + idx, raw_path) for idx, raw_path in enumerate(page_paths)]
    if len(indexed) <= 1:
        items = []
        skipped = 0
        for entry in indexed:
            item = _session_item_for_indexed_path(entry)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        return items, skipped

    workers = min(SCAN_THUMB_WORKERS, len(indexed))
    items: List[Dict[str, Any]] = []
    skipped = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for item in executor.map(_session_item_for_indexed_path, indexed):
            if item is None:
                skipped += 1
            else:
                items.append(item)
    return items, skipped
