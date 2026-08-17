"""Mask/geometry rendering primitives for the censor edit-operation engine.

Methods moved verbatim from services/censor/edit_ops.py (mixin re-split
2026-07; originally extracted from services/censor_service.py). SAFETY INVARIANT kept
byte-verbatim here: _apply_mask_crop_style routes unrecognized styles to the
mosaic default, so a mistyped or future style still censors the masked region
instead of exposing raw pixels (never-fallback-to-uncensored).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from PIL import Image, ImageColor, ImageDraw, ImageFilter


class _EditMaskGeometryMixin:
    """Mask/geometry primitives slice of CensorService (assembled in services/censor_service.py)."""

    @staticmethod
    def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError):
            return minimum

    @staticmethod
    def _normalize_operation_points(points: Any) -> List[tuple[float, float]]:
        normalized: List[tuple[float, float]] = []
        for point in points or []:
            if not isinstance(point, dict):
                continue
            try:
                normalized.append((float(point.get("x")), float(point.get("y"))))
            except (TypeError, ValueError):
                continue
        return normalized

    @staticmethod
    def _count_polygon_points(regions: Any) -> int:
        if not isinstance(regions, list):
            return 0
        total = 0
        for region in regions:
            if not isinstance(region, dict):
                continue
            polygon = region.get("polygon")
            if isinstance(polygon, list):
                total += len(polygon)
        return total

    @staticmethod
    def _draw_stroke_mask(mask: Image.Image, points: List[tuple[float, float]], brush_size: float) -> None:
        if not points:
            return

        width = max(1, int(round(brush_size)))
        draw = ImageDraw.Draw(mask)
        if len(points) == 1:
            x, y = points[0]
            radius = brush_size / 2.0
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)
            return

        draw.line(points, fill=255, width=width, joint="curve")
        radius = brush_size / 2.0
        for x, y in (points[0], points[-1]):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)

    @staticmethod
    def _pixelate_image_crop(image: Image.Image, bbox: tuple[int, int, int, int], block_size: int) -> Image.Image:
        crop = image.crop(bbox)
        downscale = max(1, int(block_size))
        small_w = max(1, crop.width // downscale)
        small_h = max(1, crop.height // downscale)
        pixelated = crop.resize((small_w, small_h), Image.Resampling.BILINEAR)
        return pixelated.resize(crop.size, Image.Resampling.NEAREST)

    @staticmethod
    def _build_pen_overlay(size: tuple[int, int], color: str, opacity: float, mask: Image.Image) -> Image.Image:
        rgba = ImageColor.getrgb(color or "#ff0000")
        overlay = Image.new("RGBA", size, (*rgba, 0))
        alpha_mask = mask.point(lambda value: int(value * max(0.0, min(1.0, opacity))))
        overlay.putalpha(alpha_mask)
        return overlay

    @staticmethod
    def _composite_crop_with_mask(
        image: Image.Image,
        effect_crop: Image.Image,
        mask_crop: Image.Image,
        bbox: tuple[int, int, int, int],
    ) -> None:
        base_crop = image.crop(bbox).convert("RGBA")
        composited = Image.composite(effect_crop.convert("RGBA"), base_crop, mask_crop)
        image.paste(composited, bbox)

    @classmethod
    def _apply_mask_crop_style(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        mask_crop: Image.Image,
        bbox: tuple[int, int, int, int],
        *,
        style: str,
        block_size: int,
        blur_radius: int,
        pen_color: str = "#ff0000",
        pen_opacity: float = 1.0,
    ) -> None:
        x1, y1, x2, y2 = [int(value) for value in bbox]
        if x2 <= x1 or y2 <= y1 or mask_crop.getbbox() is None:
            return

        bbox = (x1, y1, x2, y2)
        if mask_crop.size != (x2 - x1, y2 - y1):
            mask_crop = mask_crop.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS)
        normalized_style = str(style or "").strip().lower()

        if normalized_style == "pen":
            overlay = cls._build_pen_overlay(mask_crop.size, pen_color, pen_opacity, mask_crop)
            base_crop = image.crop(bbox).convert("RGBA")
            composited = Image.alpha_composite(base_crop, overlay)
            image.paste(composited, bbox)
            return

        if normalized_style == "eraser":
            cls._composite_crop_with_mask(image, original_image.crop(bbox).convert("RGBA"), mask_crop, bbox)
            return

        if normalized_style in {"black_bar", "solid", "black"}:
            effect_crop = Image.new("RGBA", (x2 - x1, y2 - y1), (0, 0, 0, 255))
            cls._composite_crop_with_mask(image, effect_crop, mask_crop, bbox)
            return

        if normalized_style == "white_bar":
            effect_crop = Image.new("RGBA", (x2 - x1, y2 - y1), (255, 255, 255, 255))
            cls._composite_crop_with_mask(image, effect_crop, mask_crop, bbox)
            return

        if normalized_style == "blur":
            effect_crop = image.crop(bbox).filter(ImageFilter.GaussianBlur(radius=max(1, int(round(blur_radius)))))
            cls._composite_crop_with_mask(image, effect_crop.convert("RGBA"), mask_crop, bbox)
            return

        effect_crop = cls._pixelate_image_crop(image, bbox, max(1, int(round(block_size))))
        cls._composite_crop_with_mask(image, effect_crop.convert("RGBA"), mask_crop, bbox)

    @classmethod
    def _apply_mask_style(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        mask: Image.Image,
        *,
        style: str,
        block_size: int,
        blur_radius: int,
        pen_color: str = "#ff0000",
        pen_opacity: float = 1.0,
    ) -> None:
        bbox = mask.getbbox()
        if not bbox:
            return

        x1, y1, x2, y2 = [int(value) for value in bbox]
        bbox = (x1, y1, x2, y2)
        mask_crop = mask.crop(bbox)
        cls._apply_mask_crop_style(
            image,
            original_image,
            mask_crop,
            bbox,
            style=style,
            block_size=block_size,
            blur_radius=blur_radius,
            pen_color=pen_color,
            pen_opacity=pen_opacity,
        )

    @staticmethod
    def _get_stroke_mask_bounds(
        points: List[tuple[float, float]],
        brush_size: float,
        image_size: tuple[int, int],
    ) -> Optional[tuple[int, int, int, int]]:
        if not points:
            return None

        image_width, image_height = image_size
        if image_width <= 0 or image_height <= 0:
            return None

        radius = max(0.5, brush_size / 2.0)
        # Keep Pillow's inclusive line/ellipse edge pixels inside the crop; the
        # rendered mask is tightened with getbbox() before applying the effect.
        padding = 2.0
        left = max(0, math.floor(min(x for x, _ in points) - radius - padding))
        top = max(0, math.floor(min(y for _, y in points) - radius - padding))
        right = min(
            image_width,
            math.ceil(max(x for x, _ in points) + radius + padding) + 1,
        )
        bottom = min(
            image_height,
            math.ceil(max(y for _, y in points) + radius + padding) + 1,
        )
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    @staticmethod
    def _normalize_geometry_coordinate(
        value: Any,
        *,
        label: str,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            coordinate = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid geometry coordinate at {label}: expected a finite number",
            ) from exc
        if not math.isfinite(coordinate):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid geometry coordinate at {label}: expected a finite number",
            )
        if coordinate < minimum or coordinate > maximum:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid geometry coordinate at {label}: {coordinate!r} is outside "
                    f"the supported range [{minimum}, {maximum}]"
                ),
            )
        # Pillow truncates polygon coordinates to C integers before rasterizing.
        return int(coordinate)

    @staticmethod
    def _get_canvas_candidate_polygon_regions(
        polygon_regions: List[List[tuple[int, int]]],
        image_size: tuple[int, int],
    ) -> List[List[tuple[int, int]]]:
        image_width, image_height = image_size
        return [
            polygon
            for polygon in polygon_regions
            if max(x for x, _ in polygon) >= 0
            and max(y for _, y in polygon) >= 0
            and min(x for x, _ in polygon) < image_width
            and min(y for _, y in polygon) < image_height
        ]

    @staticmethod
    def _get_polygon_mask_bounds(
        polygon_regions: List[List[tuple[int, int]]],
        image_size: tuple[int, int],
    ) -> Optional[tuple[int, int, int, int]]:
        if not polygon_regions:
            return None

        image_width, image_height = image_size
        # Keep global X coordinates so Pillow's float scanline rounding stays
        # byte-identical. Mode 1 stores this height-local strip at one bit per
        # pixel; getbbox() tightens both axes before compositing.
        top = max(0, min(y for polygon in polygon_regions for _, y in polygon) - 1)
        bottom = min(
            image_height,
            max(y for polygon in polygon_regions for _, y in polygon) + 2,
        )
        if bottom <= top:
            return None
        return (0, top, image_width, bottom)

    @staticmethod
    def _get_box_mask_bounds(
        box_regions: List[List[int]],
        image_size: tuple[int, int],
    ) -> Optional[tuple[int, int, int, int]]:
        if not box_regions:
            return None

        for x1, y1, x2, y2 in box_regions:
            if x2 < x1:
                raise ValueError("x1 must be greater than or equal to x0")
            if y2 < y1:
                raise ValueError("y1 must be greater than or equal to y0")

        image_width, image_height = image_size
        visible_boxes: List[List[int]] = [
            box
            for box in box_regions
            if box[2] >= 0 and box[3] >= 0 and box[0] < image_width and box[1] < image_height
        ]
        if not visible_boxes:
            return None

        left = max(0, min(box[0] for box in visible_boxes))
        top = max(0, min(box[1] for box in visible_boxes))
        right = min(image_width, max(box[2] for box in visible_boxes) + 1)
        bottom = min(image_height, max(box[3] for box in visible_boxes) + 1)
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    @staticmethod
    def _normalize_inline_mask_bounds(
        value: Any,
        image_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise HTTPException(
                status_code=400,
                detail="Invalid inline mask bounds: expected [x1, y1, x2, y2]",
            )

        coordinates: List[int] = []
        for index, raw_coordinate in enumerate(value):
            try:
                coordinate = float(raw_coordinate)
            except (TypeError, ValueError, OverflowError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid inline mask bounds coordinate at index {index}",
                ) from exc
            if not math.isfinite(coordinate) or not coordinate.is_integer():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid inline mask bounds coordinate at index {index}",
                )
            coordinates.append(int(coordinate))

        x1, y1, x2, y2 = coordinates
        image_width, image_height = image_size
        if (
            x1 < 0
            or y1 < 0
            or x2 > image_width
            or y2 > image_height
            or x2 <= x1
            or y2 <= y1
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid inline mask bounds: bounds must be non-empty and contained "
                    f"within the {image_width}x{image_height} source image"
                ),
            )
        return x1, y1, x2, y2

    @staticmethod
    def _validate_inline_mask_source_size(
        operation: Dict[str, Any],
        image_size: tuple[int, int],
    ) -> None:
        raw_width = operation.get("mask_image_width")
        raw_height = operation.get("mask_image_height")
        if raw_width is None and raw_height is None:
            return
        if raw_width is None or raw_height is None:
            raise HTTPException(
                status_code=400,
                detail="Invalid inline mask source size: width and height must be provided together",
            )

        try:
            width = float(raw_width)
            height = float(raw_height)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid inline mask source size: expected positive integers",
            ) from exc
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or not width.is_integer()
            or not height.is_integer()
            or width <= 0
            or height <= 0
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid inline mask source size: expected positive integers",
            )

        normalized_size = (int(width), int(height))
        if normalized_size != image_size:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid inline mask source size: expected {image_size[0]}x{image_size[1]}, "
                    f"received {normalized_size[0]}x{normalized_size[1]}"
                ),
            )
