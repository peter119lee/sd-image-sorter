"""
Censor service for SD Image Sorter.

Handles business logic for NSFW detection, censoring preview, and save operations.
"""
import logging
import os
import base64
import binascii
import threading
import time
import traceback
import uuid
from typing import Optional, List, Dict, Any
from pathlib import Path
from io import BytesIO

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from PIL import Image, ImageDraw, PngImagePlugin, ImageEnhance, ImageFilter, ImageColor, ImageChops

import database as db
from config import get_temp_dir
from model_health import get_default_legacy_model_path, get_model_health
from services.indexed_file_mutation_service import save_and_reconcile_checked
from utils.source_paths import resolve_existing_indexed_image_path

logger = logging.getLogger(__name__)
MAX_SAVE_DATA_BYTES = 40 * 1024 * 1024
MAX_SAVE_DATA_PIXELS = 40_000_000
MASK_INLINE_DATA_PIXEL_THRESHOLD = 8_000_000
MASK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MASK_CACHE_DIR = Path(get_temp_dir()) / "censor_mask_cache"
MAX_EDIT_OPERATION_COUNT = 5000
MAX_EDIT_STROKE_POINTS = 250_000
MAX_EDIT_GEOMETRY_POINTS = 250_000
MAX_INLINE_OPERATION_MASK_BYTES = 12 * 1024 * 1024
MAX_INLINE_OPERATION_MASK_PIXELS = 12_000_000
MAX_FULL_IMAGE_FILTER_PIXELS = 45_000_000
MAX_SERVER_EDIT_CANVAS_PIXELS = 80_000_000


class CensorDetectRequest(BaseModel):
    """Request model for detection."""
    image_id: int = Field(..., ge=1)
    model_path: str = ""
    model_type: str = Field("legacy", pattern="^(legacy|nudenet|sam3|both)$")
    confidence_threshold: float = Field(0.5, ge=0.0, le=1.0)
    exposed_only: bool = True
    target_classes: Optional[List[str]] = None
    text_prompts: Optional[List[str]] = None


class MaskRefineRequest(BaseModel):
    """Request model for mask refinement."""
    image_id: int = Field(..., ge=1)
    box: List[int] = Field(..., min_length=4, max_length=4)
    text_prompt: Optional[str] = None
    # SAM3 confidence gate for this refinement (mask-score floor; also the
    # presence gate when a text prompt is given). None -> the refiner's
    # built-in defaults on the single endpoint, or the batch-level
    # ``sam3_confidence`` when nested inside a BatchMaskRefineRequest.
    sam3_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class TextSegmentRequest(BaseModel):
    """Request model for text segmentation."""
    image_id: int = Field(..., ge=1)
    text_prompt: str = Field(..., min_length=1)
    # Optional override for SAM3's presence gate. Omitted -> the looser
    # explicit-text default (decoupled from the 0.5 auto-detect gate).
    presence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


class BatchMaskRefineRequest(BaseModel):
    """Request model for batch mask refinement via SAM3."""
    # No hard cap: batch_refine_mask runs sequentially and GC/empty_cache()s
    # every 4 items, so a large batch is slow but not a crash risk.
    items: List[MaskRefineRequest] = Field(..., min_length=1)
    # Confidence gate applied to every item that doesn't set its own
    # ``sam3_confidence``. The frontend slider (sidebar + detect modal)
    # sends this on every batch refine.
    sam3_confidence: float = Field(0.5, ge=0.0, le=1.0)


class CensorApplyRequest(BaseModel):
    """Request model for censor apply/preview."""
    image_id: int = Field(..., ge=1)
    regions: List[List[int]] = Field(..., min_length=1)
    style: str = Field("mosaic", pattern="^(mosaic|blur|solid|black_bar|white_bar|sticker)$")
    block_size: int = Field(16, ge=1)
    blur_radius: int = Field(20, ge=1)
    sticker_path: Optional[str] = None

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, value: List[List[int]]) -> List[List[int]]:
        for region in value:
            if len(region) != 4:
                raise ValueError("Each region must contain exactly 4 coordinates")
        return value


class CensorSaveRequest(BaseModel):
    """Request model for censor save."""
    image_id: int = Field(..., ge=1)
    regions: List[List[int]] = Field(..., min_length=1)
    style: str = Field("mosaic", pattern="^(mosaic|blur|solid|black_bar|white_bar|sticker)$")
    block_size: int = Field(16, ge=1)
    blur_radius: int = Field(20, ge=1)
    sticker_path: Optional[str] = None
    output_folder: str = Field(..., min_length=1)
    filename_suffix: str = "_censored"
    allow_overwrite: bool = False

    @field_validator("regions")
    @classmethod
    def validate_save_regions(cls, value: List[List[int]]) -> List[List[int]]:
        for region in value:
            if len(region) != 4:
                raise ValueError("Each region must contain exactly 4 coordinates")
        return value


class CensorSaveDataRequest(BaseModel):
    """Request to save base64 image data directly."""
    image_data: str = Field(..., max_length=100_000_000)
    filename: str = Field(..., min_length=1)
    output_folder: str = Field(..., min_length=1)
    metadata_option: str = Field("keep", pattern="^(keep|minimal|strip)$")
    output_format: str = Field("png", pattern="^(png|jpg|jpeg|webp)$")
    original_image_id: Optional[int] = Field(None, ge=1)
    allow_overwrite: bool = False


class CensorSaveOperationsRequest(BaseModel):
    """Request to save non-destructive edit operations on top of the original image."""
    original_image_id: int = Field(..., ge=1)
    operations: List[Dict[str, Any]] = Field(default_factory=list, max_length=MAX_EDIT_OPERATION_COUNT)
    filename: str = Field(..., min_length=1)
    output_folder: str = Field(..., min_length=1)
    metadata_option: str = Field("keep", pattern="^(keep|minimal|strip)$")
    output_format: str = Field("png", pattern="^(png|jpg|jpeg|webp)$")
    allow_overwrite: bool = False


class RemoveBackgroundRequest(BaseModel):
    """Request to remove background using SAM3."""
    image_id: int = Field(..., ge=1)
    fill_mode: str = Field("transparent", pattern="^(transparent|white|black)$")
    edge_threshold: float = Field(0.5, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the CensorService method bodies live in the
# services/censor/ package as mixins, assembled below. THIS module remains a
# real FILE and the single monkeypatch surface (tests/test_censor_service_pins.py):
#   * The module constants above and the two model_health seam names
#     (get_model_health / get_default_legacy_model_path) stay defined/imported
#     HERE; mixin bodies resolve them through _svc() at call time so the 11
#     monkeypatch call-sites that patch them on this module object keep
#     landing (the string-form patch in test_resource_safety.py included).
#   * The 10 request models stay defined here (routers/censor.py imports all
#     of them by name; CensorSaveOperationsRequest binds
#     max_length=MAX_EDIT_OPERATION_COUNT at import).
#   * _decode_operation_mask_header + _validate_edit_operation_budget stay
#     byte-verbatim ON THIS CLASS: the resource-budget 413 gates read the
#     patched MAX_* constants as bare module globals, and keeping them here
#     preserves both the bytes and the patch semantics.
#   * The class-level mask-cache attrs (_mask_cache_lock / _mask_cache_index /
#     _mask_cache_dir) and __init__ (self._detector) stay on the composed
#     shell class so monkeypatch.setattr(CensorService, ...) and
#     cls._mask_cache_* resolve regardless of which mixin owns the method.
#   * tests/test_indexed_file_mutation_contract.py read_text()s THIS file at
#     its literal path: it must stay a file (not a package __init__) and must
#     not own overwrite-preflight logic.
#   * _BACKEND_FILE below is passed as backend_file= by the four indexed-path
#     methods (output_io._resolve_source_image_path / save / save_data,
#     edit_ops.save_operations); utils/source_paths and
#     indexed_file_mutation_service derive backend_root =
#     dirname(dirname(abspath(backend_file))), so it must stay THIS file
#     __file__ (a mixin __file__ is one level deeper and would silently break
#     relative/legacy-row resolution; pinned by
#     test_module_sits_two_levels_below_backend).
# Imports above are intentionally kept verbatim even where the facade body no
# longer calls them (seam + re-export surface). F401 is ignored for this file
# in pyproject.toml, same as database.py / sorting_service.py /
# image_service.py / tag_rules.py.
from services.censor.detection import _DetectionMixin
from services.censor.edit_mask_geometry import _EditMaskGeometryMixin
from services.censor.edit_ops import _EditApplyMixin
from services.censor.mask_cache import _MaskCacheMixin
from services.censor.output_io import _OutputMixin, _paths_match_runtime_case
from services.censor.sam3_ops import _Sam3Mixin


# The backend-root anchor passed as backend_file= by the four indexed-path
# methods. utils/source_paths computes backend_root =
# dirname(dirname(abspath(it))), so this must stay THIS file __file__
# (backend/services/censor_service.py -> backend/).
_BACKEND_FILE = __file__


class CensorService(
    _MaskCacheMixin,
    _DetectionMixin,
    _OutputMixin,
    _EditApplyMixin,
    _EditMaskGeometryMixin,
    _Sam3Mixin,
):
    """Service for NSFW detection and image censoring."""

    _mask_cache_lock = threading.Lock()
    _mask_cache_index: Dict[str, Dict[str, Any]] = {}
    _mask_cache_dir = MASK_CACHE_DIR

    def __init__(self):
        """Initialize the censor service."""
        self._detector = None

    @classmethod
    def _decode_operation_mask_header(cls, mask_data: str) -> tuple[bytes, str]:
        mask_bytes, _ = cls._decode_base64_image(mask_data)
        if len(mask_bytes) > MAX_INLINE_OPERATION_MASK_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Inline edit mask is too large. Use cached mask refs for large masks.",
            )
        return mask_bytes, mask_data

    @classmethod
    def _validate_edit_operation_budget(
        cls,
        operations: List[Dict[str, Any]],
        *,
        image_size: tuple[int, int],
    ) -> None:
        width, height = image_size
        total_pixels = width * height
        if total_pixels > MAX_SERVER_EDIT_CANVAS_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Source image is too large for server-side edit saving. Export a smaller version first.",
            )

        stroke_points = 0
        geometry_points = 0
        inline_mask_pixels = 0
        has_full_image_filter = False

        if len(operations or []) > MAX_EDIT_OPERATION_COUNT:
            raise HTTPException(status_code=413, detail="Too many edit operations to save safely")

        for operation in operations or []:
            if not isinstance(operation, dict):
                continue
            kind = str(operation.get("kind") or "").strip().lower()
            if kind == "stroke":
                points = operation.get("points") or []
                if isinstance(points, list):
                    stroke_points += len(points)
            elif kind == "geometry_effect":
                geometry_points += cls._count_polygon_points(operation.get("regions"))
            elif kind == "mask_effect":
                mask_ref = str(operation.get("mask_ref") or "").strip()
                mask_data = str(operation.get("mask_data") or "").strip()
                if mask_ref:
                    entry = cls._get_cached_mask_entry(mask_ref)
                    bounds = cls._normalize_mask_bounds(
                        operation.get("mask_bounds") or entry.get("bounds"),
                        image_size=image_size,
                    )
                    if bounds is None:
                        raise HTTPException(status_code=400, detail="Invalid cached mask bounds")
                    inline_mask_pixels += max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])
                elif mask_data:
                    mask_bytes, _ = cls._decode_operation_mask_header(mask_data)
                    try:
                        with Image.open(BytesIO(mask_bytes)) as mask_image:
                            mask_image.verify()
                            inline_mask_pixels += mask_image.width * mask_image.height
                    except HTTPException:
                        raise
                    except Exception as exc:
                        raise HTTPException(status_code=400, detail="Invalid edit mask data") from exc
            elif kind == "filter":
                has_full_image_filter = True

        if stroke_points > MAX_EDIT_STROKE_POINTS:
            raise HTTPException(
                status_code=413,
                detail=f"Too many brush points to save safely ({stroke_points:,} > {MAX_EDIT_STROKE_POINTS:,})",
            )
        if geometry_points > MAX_EDIT_GEOMETRY_POINTS:
            raise HTTPException(
                status_code=413,
                detail=f"Too many polygon points to save safely ({geometry_points:,} > {MAX_EDIT_GEOMETRY_POINTS:,})",
            )
        if inline_mask_pixels > MAX_INLINE_OPERATION_MASK_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Edit masks are too large to save inline. Re-run mask refinement so cached mask refs are used.",
            )
        if has_full_image_filter and total_pixels > MAX_FULL_IMAGE_FILTER_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Full-image filters are too large for server-side saving on this image. Disable the filter or export a smaller version.",
            )
