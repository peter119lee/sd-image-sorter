"""Non-destructive edit-operation appliers and save_operations.

Methods moved verbatim from services/censor_service.py (decomposition 2026-07) except the manifest lines:
_apply_mask_effect_operation resolves MAX_INLINE_OPERATION_MASK_PIXELS through
_svc() at call time (facade-patched constant family), and save_operations
passes the facade _BACKEND_FILE as backend_file= (this module sits one level
too deep for backend-root derivation). The resource-budget 413 gates
(_validate_edit_operation_budget + _decode_operation_mask_header) stay
byte-verbatim ON THE FACADE CLASS, where their bare MAX_* reads keep matching
the string-form monkeypatch in test_resource_safety.py. Since the 2026-07
mixin re-split, the mask/geometry rendering primitives (including the
never-fallback-to-uncensored _apply_mask_crop_style) live in the sibling
edit_mask_geometry.py; cross-mixin calls resolve through cls on the composed
CensorService facade class.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

import database as db
from services.censor.output_io import _combine_save_warnings
from services.indexed_file_mutation_service import save_and_reconcile_checked

if TYPE_CHECKING:  # annotation-only; never imported at runtime (no facade cycle)
    from services.censor_service import CensorSaveOperationsRequest

logger = logging.getLogger("services.censor_service")


def _svc():
    """Resolve facade-owned seams/constants through services.censor_service at call time.

    Tests patch module attributes on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.censor_service as censor_service

    return censor_service


class _EditApplyMixin:
    """Edit-operation appliers + save_operations slice of CensorService (assembled in services/censor_service.py)."""

    def save_operations(self, request: CensorSaveOperationsRequest) -> Dict[str, Any]:
        """Save original image with non-destructive censor operations applied server-side."""
        from utils.path_validation import validate_folder_path, sanitize_filename

        is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid output folder")

        image_row = db.get_image_by_id(request.original_image_id)
        if not image_row:
            raise HTTPException(status_code=404, detail="Image not found")

        output_folder = self._ensure_safe_output_directory(request.output_folder)
        source_path = self._resolve_source_image_path(
            image_row["path"],
            image_id=request.original_image_id,
            action_label="Saving edited image",
        )

        try:
            os.makedirs(output_folder, exist_ok=True)

            with Image.open(source_path) as src:
                original_image = src.convert("RGBA")
            width, height = original_image.size
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail="Invalid source image")
            self._validate_edit_operation_budget(request.operations, image_size=(width, height))

            working_image = original_image.copy()
            self._apply_edit_operations(working_image, original_image, request.operations)

            safe_filename = sanitize_filename(request.filename)
            base_name = os.path.splitext(safe_filename)[0]
            output_format = self._normalize_output_format(request.output_format)
            ext = f".{output_format}"
            output_filename = f"{base_name}{ext}"
            output_path = self._ensure_output_path(output_folder, output_filename)

            if request.metadata_option == "strip":
                image_to_save = self._strip_all_metadata(working_image)
                save_kwargs = {}
            else:
                image_to_save = working_image
                save_kwargs = self._prepare_metadata_for_save(
                    working_image,
                    request.original_image_id,
                    request.metadata_option,
                    output_format,
                )

            def _write_operations_save(final_output_path: str, _overwrite_requested: bool) -> List[str]:
                return self._save_image_with_format(image_to_save, final_output_path, output_format, save_kwargs)

            write_result = save_and_reconcile_checked(
                output_path,
                _write_operations_save,
                allow_overwrite=request.allow_overwrite,
                backend_file=_svc()._BACKEND_FILE,
                validation_error_factory=self._output_validation_error,
                conflict_error_factory=self._output_conflict_error,
            )

            return self._save_response(
                output_path,
                output_filename,
                warnings=_combine_save_warnings(write_result.writer_result, write_result.warnings),
                target_existed=write_result.target_existed,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Save operations failed")
            raise HTTPException(status_code=500, detail="Save operations failed")

    @classmethod
    def _apply_clone_operation(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        *,
        points: List[tuple[float, float]],
        brush_size: float,
        clone_offset: Dict[str, Any],
    ) -> None:
        if not points:
            return

        diameter = max(1, int(round(brush_size)))
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
        offset_x = float(clone_offset.get("x", 0))
        offset_y = float(clone_offset.get("y", 0))

        for x, y in points:
            src_x = int(round(x + offset_x))
            src_y = int(round(y + offset_y))
            dst_x = int(round(x))
            dst_y = int(round(y))
            source_patch = original_image.crop((
                src_x - diameter // 2,
                src_y - diameter // 2,
                src_x - diameter // 2 + diameter,
                src_y - diameter // 2 + diameter,
            )).convert("RGBA")
            image.paste(
                source_patch,
                (dst_x - diameter // 2, dst_y - diameter // 2),
                mask,
            )

    @classmethod
    def _apply_stroke_operation(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        operation: Dict[str, Any],
    ) -> None:
        tool = str(operation.get("tool") or "brush").strip().lower()
        points = cls._normalize_operation_points(operation.get("points"))
        if not points:
            return

        brush_size = cls._clamp_float(operation.get("brush_size", 1), 1.0, 4096.0)
        if tool == "clone":
            clone_offset = operation.get("clone_offset") or {}
            cls._apply_clone_operation(
                image,
                original_image,
                points=points,
                brush_size=brush_size,
                clone_offset=clone_offset,
            )
            return

        mask_bounds = cls._get_stroke_mask_bounds(points, brush_size, image.size)
        if mask_bounds is None:
            return
        x1, y1, x2, y2 = mask_bounds
        mask = Image.new("L", (x2 - x1, y2 - y1), 0)
        local_points = [(x - x1, y - y1) for x, y in points]
        cls._draw_stroke_mask(mask, local_points, brush_size)
        local_bbox = mask.getbbox()
        if local_bbox is None:
            return
        effect_bbox = (
            x1 + local_bbox[0],
            y1 + local_bbox[1],
            x1 + local_bbox[2],
            y1 + local_bbox[3],
        )
        cls._apply_mask_crop_style(
            image,
            original_image,
            mask.crop(local_bbox),
            effect_bbox,
            style=operation.get("style") if tool == "brush" else tool,
            block_size=int(operation.get("block_size", 16) or 16),
            blur_radius=int(operation.get("blur_radius", 20) or 20),
            pen_color=str(operation.get("pen_color") or "#ff0000"),
            pen_opacity=cls._clamp_float(operation.get("pen_opacity", 1.0), 0.0, 1.0),
        )

    @classmethod
    def _apply_geometry_mask_crop(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        mask: Image.Image,
        mask_origin: tuple[int, int],
        operation: Dict[str, Any],
    ) -> None:
        local_bbox = mask.getbbox()
        if local_bbox is None:
            return
        origin_x, origin_y = mask_origin
        effect_bbox = (
            origin_x + local_bbox[0],
            origin_y + local_bbox[1],
            origin_x + local_bbox[2],
            origin_y + local_bbox[3],
        )
        cls._apply_mask_crop_style(
            image,
            original_image,
            mask.crop(local_bbox),
            effect_bbox,
            style=str(operation.get("style") or "mosaic"),
            block_size=int(operation.get("block_size", 16) or 16),
            blur_radius=int(operation.get("blur_radius", 20) or 20),
        )

    @classmethod
    def _apply_geometry_effect_operation(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        operation: Dict[str, Any],
    ) -> None:
        regions = operation.get("regions") or []
        if not isinstance(regions, list) or not regions:
            return

        polygon_regions: List[List[tuple[int, int]]] = []
        box_regions: List[List[int]] = []
        image_width, image_height = image.size
        # Product geometry is emitted in source-image coordinates. Keep two
        # canvas extents of clipping tolerance beyond either edge, but reject
        # unbounded values before Pillow's C-int/float scanline rasterizer.
        x_coordinate_range = (-image_width * 2, image_width * 3)
        y_coordinate_range = (-image_height * 2, image_height * 3)

        for region_index, region in enumerate(regions):
            if not isinstance(region, dict):
                continue
            polygon = region.get("polygon")
            if isinstance(polygon, list):
                points: List[tuple[int, int]] = []
                for point_index, point in enumerate(polygon):
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    points.append(
                        (
                            cls._normalize_geometry_coordinate(
                                point[0],
                                label=f"regions[{region_index}].polygon[{point_index}].x",
                                minimum=x_coordinate_range[0],
                                maximum=x_coordinate_range[1],
                            ),
                            cls._normalize_geometry_coordinate(
                                point[1],
                                label=f"regions[{region_index}].polygon[{point_index}].y",
                                minimum=y_coordinate_range[0],
                                maximum=y_coordinate_range[1],
                            ),
                        )
                    )
                if len(points) >= 3:
                    polygon_regions.append(points)
                    continue

            box = region.get("box")
            if isinstance(box, list) and len(box) == 4:
                normalized_box: List[int] = []
                for coordinate_index, value in enumerate(box):
                    coordinate_range = (
                        x_coordinate_range if coordinate_index % 2 == 0 else y_coordinate_range
                    )
                    normalized_box.append(
                        cls._normalize_geometry_coordinate(
                            value,
                            label=f"regions[{region_index}].box[{coordinate_index}]",
                            minimum=coordinate_range[0],
                            maximum=coordinate_range[1],
                        )
                    )
                box_regions.append(normalized_box)

        candidate_polygon_regions = cls._get_canvas_candidate_polygon_regions(
            polygon_regions,
            image.size,
        )
        polygon_bounds = cls._get_polygon_mask_bounds(candidate_polygon_regions, image.size)
        if polygon_bounds is not None:
            x1, y1, x2, y2 = polygon_bounds
            polygon_mask = Image.new("1", (x2 - x1, y2 - y1), 0)
            polygon_draw = ImageDraw.Draw(polygon_mask)
            for polygon in candidate_polygon_regions:
                polygon_draw.polygon([(x - x1, y - y1) for x, y in polygon], fill=1)
            cls._apply_geometry_mask_crop(
                image,
                original_image,
                polygon_mask,
                (x1, y1),
                operation,
            )

        box_bounds = cls._get_box_mask_bounds(box_regions, image.size)
        if box_bounds is not None:
            origin_x, origin_y, right, bottom = box_bounds
            box_mask = Image.new("L", (right - origin_x, bottom - origin_y), 0)
            box_draw = ImageDraw.Draw(box_mask)
            for x1, y1, x2, y2 in box_regions:
                box_draw.rectangle(
                    [x1 - origin_x, y1 - origin_y, x2 - origin_x, y2 - origin_y],
                    fill=255,
                )
            cls._apply_geometry_mask_crop(
                image,
                original_image,
                box_mask,
                (origin_x, origin_y),
                operation,
            )

    @classmethod
    def _apply_mask_effect_operation(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        operation: Dict[str, Any],
    ) -> None:
        mask_data = str(operation.get("mask_data") or "").strip()
        mask_ref = str(operation.get("mask_ref") or "").strip()

        if mask_ref:
            entry = cls._get_cached_mask_entry(mask_ref)
            bounds = cls._normalize_mask_bounds(
                operation.get("mask_bounds") or entry.get("bounds"),
                image_size=image.size,
            )
            if bounds is None:
                raise HTTPException(status_code=400, detail="Invalid cached mask bounds")

            crop_path = Path(entry["path"])
            with Image.open(crop_path) as cached_mask_src:
                crop_mask = cached_mask_src.convert("L")
            expected_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
            if crop_mask.size != expected_size:
                crop_mask = crop_mask.resize(expected_size, Image.Resampling.LANCZOS)
            cls._apply_mask_crop_style(
                image,
                original_image,
                crop_mask,
                bounds,
                style=str(operation.get("style") or "mosaic"),
                block_size=int(operation.get("block_size", 16) or 16),
                blur_radius=int(operation.get("blur_radius", 20) or 20),
            )
            return
        if not mask_data:
            return

        mask_bytes, _ = cls._decode_operation_mask_header(mask_data)
        with Image.open(BytesIO(mask_bytes)) as mask_source:
            mask_image = mask_source.convert("RGBA")
        mask_pixels = mask_image.width * mask_image.height
        if mask_pixels > _svc().MAX_INLINE_OPERATION_MASK_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Inline edit mask is too large. Use cached mask refs for large masks.",
            )
        alpha = mask_image.getchannel("A")

        raw_bounds = operation.get("mask_bounds")
        has_bounded_crop = raw_bounds is not None and raw_bounds != []
        if has_bounded_crop:
            bounds = cls._normalize_inline_mask_bounds(raw_bounds, image.size)
            cls._validate_inline_mask_source_size(operation, image.size)
            expected_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
            if alpha.size != expected_size:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid inline mask crop size: expected {expected_size[0]}x{expected_size[1]}, "
                        f"received {alpha.width}x{alpha.height}"
                    ),
                )
            if alpha.getbbox() is None:
                return
            cls._apply_geometry_mask_crop(
                image,
                original_image,
                alpha,
                (bounds[0], bounds[1]),
                operation,
            )
            return

        if alpha.getbbox() is None:
            return
        if mask_image.size != image.size:
            alpha = alpha.resize(image.size, Image.Resampling.LANCZOS)
        cls._apply_mask_style(
            image,
            original_image,
            alpha,
            style=str(operation.get("style") or "mosaic"),
            block_size=int(operation.get("block_size", 16) or 16),
            blur_radius=int(operation.get("blur_radius", 20) or 20),
        )

    @staticmethod
    def _apply_hue_rotation(image: Image.Image, degrees: float) -> Image.Image:
        if not degrees:
            return image

        alpha = image.getchannel("A") if "A" in image.getbands() else None
        hsv = image.convert("RGB").convert("HSV")
        h, s, v = hsv.split()
        shift = int(round((degrees / 360.0) * 255)) % 256
        h = h.point(lambda value: (value + shift) % 256)
        rotated = Image.merge("HSV", (h, s, v)).convert("RGBA")
        if alpha is not None:
            rotated.putalpha(alpha)
        return rotated

    @staticmethod
    def _apply_temperature_shift(image: Image.Image, temperature: float) -> Image.Image:
        if not temperature:
            return image

        normalized = max(-100.0, min(100.0, float(temperature))) / 100.0
        overlay_color = (255, 176, 64, int(90 * abs(normalized))) if normalized > 0 else (64, 128, 255, int(90 * abs(normalized)))
        overlay = Image.new("RGBA", image.size, overlay_color)
        return Image.alpha_composite(image.convert("RGBA"), overlay)

    @classmethod
    def _apply_vignette_filter(cls, image: Image.Image, amount: float) -> Image.Image:
        if amount <= 0:
            return image

        width, height = image.size
        inner_mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(inner_mask)
        inset_ratio = max(0.0, min(1.0, 1 - amount * 0.5))
        inset_x = int((width * (1 - inset_ratio)) / 2)
        inset_y = int((height * (1 - inset_ratio)) / 2)
        draw.ellipse([inset_x, inset_y, width - inset_x, height - inset_y], fill=255)
        blur_radius = max(1, int(max(width, height) * 0.08))
        soft_inner = inner_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        vignette_alpha = ImageChops.invert(soft_inner).point(
            lambda value: int(value * min(1.0, amount * 0.7))
        )
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay.putalpha(vignette_alpha)
        return Image.alpha_composite(image.convert("RGBA"), overlay)

    @classmethod
    def _apply_filter_operation(
        cls,
        image: Image.Image,
        operation: Dict[str, Any],
    ) -> Image.Image:
        values = operation.get("values") or {}
        if not isinstance(values, dict):
            return image

        result = image.convert("RGBA")
        brightness = float(values.get("brightness", 0) or 0)
        contrast = float(values.get("contrast", 0) or 0)
        saturation = float(values.get("saturation", 0) or 0)
        hue = float(values.get("hue", 0) or 0)
        blur = float(values.get("blur", 0) or 0)
        sharpen = float(values.get("sharpen", 0) or 0)
        temperature = float(values.get("temperature", 0) or 0)
        vignette = float(values.get("vignette", 0) or 0)

        if brightness:
            result = ImageEnhance.Brightness(result).enhance(1 + brightness / 100.0)
        if contrast:
            result = ImageEnhance.Contrast(result).enhance(1 + contrast / 100.0)
        if saturation:
            result = ImageEnhance.Color(result).enhance(1 + saturation / 100.0)
        if hue:
            result = cls._apply_hue_rotation(result, hue)
        if blur > 0:
            result = result.filter(ImageFilter.GaussianBlur(radius=blur))
        if temperature:
            result = cls._apply_temperature_shift(result, temperature)
        if sharpen > 0:
            result = result.filter(ImageFilter.UnsharpMask(radius=1, percent=max(1, int(sharpen * 3)), threshold=0))
        if vignette > 0:
            result = cls._apply_vignette_filter(result, vignette / 100.0)

        return result

    @classmethod
    def _apply_edit_operations(
        cls,
        image: Image.Image,
        original_image: Image.Image,
        operations: List[Dict[str, Any]],
    ) -> None:
        for operation in operations or []:
            if not isinstance(operation, dict):
                continue

            kind = str(operation.get("kind") or "").strip().lower()
            if kind == "stroke":
                cls._apply_stroke_operation(image, original_image, operation)
            elif kind == "geometry_effect":
                cls._apply_geometry_effect_operation(image, original_image, operation)
            elif kind == "mask_effect":
                cls._apply_mask_effect_operation(image, original_image, operation)
            elif kind == "filter":
                next_image = cls._apply_filter_operation(image, operation)
                image.paste(next_image)
