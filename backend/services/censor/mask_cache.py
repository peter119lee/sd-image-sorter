"""Mask cache/encode, mask-bounds coercion, and combined-mask builders.

Methods moved verbatim from services/censor_service.py (decomposition 2026-07) except the lines listed in the split
manifest: the facade-owned constants MASK_CACHE_TTL_SECONDS
(_cleanup_mask_cache) and MASK_INLINE_DATA_PIXEL_THRESHOLD (_build_mask_payload)
resolve through _svc() at call time because tests patch them on the facade
module object. The three class-level cache attrs (_mask_cache_lock /
_mask_cache_index / _mask_cache_dir) stay defined on the composed CensorService
shell in the facade so monkeypatch.setattr(CensorService, ...) and
cls._mask_cache_* keep resolving there.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageDraw

logger = logging.getLogger("services.censor_service")


def _svc():
    """Resolve facade-owned seams/constants through services.censor_service at call time.

    Tests patch module attributes on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.censor_service as censor_service

    return censor_service


class _MaskCacheMixin:
    """Mask cache/encode slice of CensorService (assembled in services/censor_service.py)."""

    @staticmethod
    def _encode_mask_image_as_data_url(mask_image: Image.Image) -> Optional[str]:
        """Encode a mask as a transparent PNG so canvas compositing only affects masked pixels."""
        normalized = mask_image.convert("L")
        if normalized.getbbox() is None:
            # Return 1x1 transparent PNG for empty masks instead of None
            buf = BytesIO()
            Image.new("RGBA", (1, 1), (255, 255, 255, 0)).save(buf, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

        rgba_mask = Image.new("RGBA", normalized.size, (255, 255, 255, 0))
        rgba_mask.putalpha(normalized)

        buffer = BytesIO()
        rgba_mask.save(buffer, format="PNG")
        buffer.seek(0)
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"

    @staticmethod
    def _mask_image_to_png_bytes(mask_image: Image.Image) -> bytes:
        normalized = mask_image.convert("L")
        rgba_mask = Image.new("RGBA", normalized.size, (255, 255, 255, 0))
        rgba_mask.putalpha(normalized)
        buffer = BytesIO()
        rgba_mask.save(buffer, format="PNG")
        return buffer.getvalue()

    @classmethod
    def _ensure_mask_cache_dir(cls) -> Path:
        cls._mask_cache_dir.mkdir(parents=True, exist_ok=True)
        return cls._mask_cache_dir

    @classmethod
    def _cleanup_mask_cache(cls) -> None:
        cutoff = time.time() - _svc().MASK_CACHE_TTL_SECONDS
        stale_entries: List[Dict[str, Any]] = []
        with cls._mask_cache_lock:
            stale_tokens = []
            for mask_ref, entry in cls._mask_cache_index.items():
                last_accessed_at = float(entry.get("last_accessed_at", entry.get("created_at", 0)))
                path = Path(entry.get("path", ""))
                if last_accessed_at < cutoff or not path.exists():
                    stale_tokens.append(mask_ref)
            for mask_ref in stale_tokens:
                entry = cls._mask_cache_index.pop(mask_ref, None)
                if entry:
                    stale_entries.append(entry)

        for entry in stale_entries:
            try:
                Path(entry["path"]).unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove stale cached mask %s", entry.get("path"), exc_info=True)

    @staticmethod
    def _normalize_mask_bounds(
        bounds: Any,
        *,
        image_size: Optional[tuple[int, int]] = None,
    ) -> Optional[tuple[int, int, int, int]]:
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            return None
        try:
            x1, y1, x2, y2 = [int(float(value)) for value in bounds]
        except (TypeError, ValueError):
            return None

        if image_size:
            width, height = image_size
            x1 = max(0, min(width, x1))
            y1 = max(0, min(height, y1))
            x2 = max(0, min(width, x2))
            y2 = max(0, min(height, y2))

        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    @classmethod
    def _cache_mask_image(cls, mask_image: Image.Image) -> Optional[Dict[str, Any]]:
        normalized = mask_image.convert("L")
        bbox = normalized.getbbox()
        if bbox is None:
            return None

        bounds = cls._normalize_mask_bounds(bbox, image_size=normalized.size)
        if bounds is None:
            return None

        cls._ensure_mask_cache_dir()
        cls._cleanup_mask_cache()

        x1, y1, x2, y2 = bounds
        cropped = normalized.crop(bounds)
        mask_ref = uuid.uuid4().hex
        mask_path = cls._mask_cache_dir / f"{mask_ref}.png"
        cropped.save(mask_path, format="PNG", optimize=True)

        now = time.time()
        with cls._mask_cache_lock:
            cls._mask_cache_index[mask_ref] = {
                "path": str(mask_path),
                "bounds": [x1, y1, x2, y2],
                "image_width": normalized.width,
                "image_height": normalized.height,
                "created_at": now,
                "last_accessed_at": now,
            }

        return {
            "mask_ref": mask_ref,
            "mask_bounds": [x1, y1, x2, y2],
            "image_width": normalized.width,
            "image_height": normalized.height,
        }

    @classmethod
    def _get_cached_mask_entry(cls, mask_ref: str) -> Dict[str, Any]:
        token = str(mask_ref or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="Mask reference is required")

        cls._cleanup_mask_cache()
        with cls._mask_cache_lock:
            entry = cls._mask_cache_index.get(token)
            if entry:
                entry["last_accessed_at"] = time.time()
                resolved = dict(entry)
            else:
                resolved = None

        if not resolved:
            raise HTTPException(status_code=404, detail="Cached mask not found")
        if not Path(resolved["path"]).exists():
            with cls._mask_cache_lock:
                cls._mask_cache_index.pop(token, None)
            raise HTTPException(status_code=404, detail="Cached mask file no longer exists")
        return resolved

    @classmethod
    def _build_mask_payload(cls, mask_image: Image.Image) -> Dict[str, Any]:
        normalized = mask_image.convert("L")
        payload: Dict[str, Any] = {
            "mask": None,
            "mask_ref": None,
            "mask_bounds": None,
            "image_width": normalized.width,
            "image_height": normalized.height,
        }
        bbox = normalized.getbbox()
        if bbox is None:
            return payload

        payload["mask_bounds"] = [int(value) for value in bbox]
        if normalized.width * normalized.height <= _svc().MASK_INLINE_DATA_PIXEL_THRESHOLD:
            payload["mask"] = cls._encode_mask_image_as_data_url(normalized.crop(bbox))
            return payload

        cached = cls._cache_mask_image(normalized)
        if cached:
            payload.update(cached)
        else:
            payload["mask"] = cls._encode_mask_image_as_data_url(normalized.crop(bbox))
        return payload

    @staticmethod
    def _build_combined_mask_image(
        image_size: tuple[int, int],
        detections: List[Dict[str, Any]],
        *,
        include_boxes: bool = False,
    ) -> Optional[Image.Image]:
        if not detections:
            return None

        mask_image = Image.new("L", image_size, 0)
        draw = ImageDraw.Draw(mask_image)
        drew_any = False

        for detection in detections:
            raw_mask = detection.get("mask")
            if raw_mask is not None:
                try:
                    import numpy as np
                    arr = np.asarray(raw_mask)
                    if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] > 0:
                        mask_pil = Image.fromarray((arr > 0).astype(np.uint8) * 255, mode="L")
                        if mask_pil.size == image_size:
                            mask_image = Image.composite(
                                Image.new("L", image_size, 255),
                                mask_image,
                                mask_pil,
                            )
                            drew_any = True
                            continue
                        else:
                            mask_pil = mask_pil.resize(image_size, Image.NEAREST)
                            mask_image = Image.composite(
                                Image.new("L", image_size, 255),
                                mask_image,
                                mask_pil,
                            )
                            drew_any = True
                            continue
                except Exception:
                    logger.warning(
                        "censor: failed to composite detection mask (label=%s); "
                        "falling back to polygon/box",
                        detection.get("label") or detection.get("class_name"),
                        exc_info=True,
                    )

            polygon = detection.get("polygon")
            if isinstance(polygon, list):
                points = [
                    (float(point[0]), float(point[1]))
                    for point in polygon
                    if isinstance(point, (list, tuple)) and len(point) >= 2
                ]
                if len(points) >= 3:
                    draw.polygon(points, fill=255)
                    drew_any = True
                    continue

            if include_boxes:
                box = detection.get("box")
                if isinstance(box, list) and len(box) == 4:
                    x1, y1, x2, y2 = [int(float(value)) for value in box]
                    draw.rectangle([x1, y1, x2, y2], fill=255)
                    drew_any = True

        if not drew_any or mask_image.getbbox() is None:
            return None
        return mask_image

    @classmethod
    def _build_combined_mask_payload(
        cls,
        image_size: tuple[int, int],
        detections: List[Dict[str, Any]],
        *,
        include_boxes: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "combined_mask": None,
            "combined_mask_ref": None,
            "combined_mask_bounds": None,
            "image_width": int(image_size[0]),
            "image_height": int(image_size[1]),
        }
        mask_image = cls._build_combined_mask_image(
            image_size,
            detections,
            include_boxes=include_boxes,
        )
        if mask_image is None:
            return payload

        mask_payload = cls._build_mask_payload(mask_image)
        payload["combined_mask"] = mask_payload.get("mask")
        payload["combined_mask_ref"] = mask_payload.get("mask_ref")
        payload["combined_mask_bounds"] = mask_payload.get("mask_bounds")
        payload["image_width"] = int(mask_payload.get("image_width") or image_size[0])
        payload["image_height"] = int(mask_payload.get("image_height") or image_size[1])
        return payload

    @classmethod
    def _build_combined_mask_data_url(
        cls,
        image_size: tuple[int, int],
        detections: List[Dict[str, Any]],
        *,
        include_boxes: bool = False,
    ) -> Optional[str]:
        mask_image = cls._build_combined_mask_image(
            image_size,
            detections,
            include_boxes=include_boxes,
        )
        if mask_image is None:
            return None
        return cls._encode_mask_image_as_data_url(mask_image)

    def get_cached_mask_preview(
        self,
        mask_ref: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ):
        entry = self._get_cached_mask_entry(mask_ref)
        mask_path = Path(entry["path"])

        if not width and not height:
            return FileResponse(
                mask_path,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        with Image.open(mask_path) as mask_file:
            mask_image = mask_file.convert("L")
            target_width = max(1, int(width or 0)) if width else None
            target_height = max(1, int(height or 0)) if height else None
            if target_width and target_height:
                resized = mask_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            elif target_width:
                resized = mask_image.resize(
                    (target_width, max(1, int(round(mask_image.height * (target_width / mask_image.width))))),
                    Image.Resampling.LANCZOS,
                )
            else:
                resized = mask_image.resize(
                    (max(1, int(round(mask_image.width * (target_height / mask_image.height)))), target_height),
                    Image.Resampling.LANCZOS,
                )

        return StreamingResponse(
            BytesIO(self._mask_image_to_png_bytes(resized)),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )
