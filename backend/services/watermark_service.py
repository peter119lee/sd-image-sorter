"""Deterministic, opt-in watermark transforms for exported image copies."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal, Optional

from PIL import Image, ImageColor, ImageDraw, ImageFont

from utils.atomic_staging import (
    create_staging_sibling,
    discard_staging_file,
    publish_staging_file,
)


WatermarkPosition = Literal[
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "center",
]
WatermarkRemovalMethod = Literal["telea", "ns"]


class WatermarkServiceError(ValueError):
    """Raised when a watermark transform cannot be applied safely."""


@dataclass(frozen=True)
class TextWatermarkConfig:
    enabled: bool
    text: str
    position: WatermarkPosition
    opacity: int
    size_percent: int
    margin_percent: int
    color: str

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise WatermarkServiceError("watermark.enabled must be a boolean")
        if not isinstance(self.text, str) or len(self.text) > 200:
            raise WatermarkServiceError("watermark.text must contain at most 200 characters")
        if self.enabled and not self.text.strip():
            raise WatermarkServiceError("watermark.text is required when watermark is enabled")
        if self.position not in {
            "top_left", "top_right", "bottom_left", "bottom_right", "center",
        }:
            raise WatermarkServiceError(f"Unsupported watermark position: {self.position!r}")
        if type(self.opacity) is not int or not 1 <= self.opacity <= 100:
            raise WatermarkServiceError("watermark.opacity must be an integer from 1 to 100")
        if type(self.size_percent) is not int or not 1 <= self.size_percent <= 20:
            raise WatermarkServiceError(
                "watermark.size_percent must be an integer from 1 to 20"
            )
        if type(self.margin_percent) is not int or not 0 <= self.margin_percent <= 10:
            raise WatermarkServiceError(
                "watermark.margin_percent must be an integer from 0 to 10"
            )
        try:
            ImageColor.getrgb(self.color)
        except (TypeError, ValueError) as exc:
            raise WatermarkServiceError(
                "watermark.color must be a valid CSS color such as #FFFFFF"
            ) from exc


@dataclass(frozen=True)
class WatermarkRegion:
    """One rectangle in basis points of the full visual image (0..10000)."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(type(value) is not int for value in values):
            raise WatermarkServiceError(
                "watermark removal region coordinates must be integers"
            )
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise WatermarkServiceError(
                "watermark removal region x/y must be non-negative and width/height positive"
            )
        if self.x > 10000 or self.y > 10000 or self.width > 10000 or self.height > 10000:
            raise WatermarkServiceError(
                "watermark removal region values must be within 0..10000"
            )
        if self.x + self.width > 10000 or self.y + self.height > 10000:
            raise WatermarkServiceError(
                "watermark removal region must stay within 0..10000"
            )


@dataclass(frozen=True)
class WatermarkRemovalConfig:
    enabled: bool
    method: WatermarkRemovalMethod
    radius: int
    padding_percent: int
    regions: tuple[WatermarkRegion, ...]

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise WatermarkServiceError("watermark_removal.enabled must be a boolean")
        if self.method not in {"telea", "ns"}:
            raise WatermarkServiceError(
                f"Unsupported watermark removal method: {self.method!r}"
            )
        if type(self.radius) is not int or not 1 <= self.radius <= 20:
            raise WatermarkServiceError(
                "watermark_removal.radius must be an integer from 1 to 20"
            )
        if type(self.padding_percent) is not int or not 0 <= self.padding_percent <= 10:
            raise WatermarkServiceError(
                "watermark_removal.padding_percent must be an integer from 0 to 10"
            )
        if not isinstance(self.regions, tuple) or len(self.regions) > 8:
            raise WatermarkServiceError(
                "watermark_removal.regions must contain at most 8 regions"
            )
        if any(not isinstance(region, WatermarkRegion) for region in self.regions):
            raise WatermarkServiceError(
                "watermark_removal.regions contains an invalid region"
            )
        if self.enabled and not self.regions:
            raise WatermarkServiceError(
                "watermark_removal requires at least one region when enabled"
            )


def _font_for_image(image: Image.Image, size_percent: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_size = max(12, round(min(image.width, image.height) * size_percent / 100))
    return ImageFont.load_default(size=font_size)


def _text_position(
    image: Image.Image,
    text_box: tuple[int, int, int, int],
    position: WatermarkPosition,
    margin_percent: int,
) -> tuple[int, int]:
    margin = round(min(image.width, image.height) * margin_percent / 100)
    left, top, right, bottom = text_box
    text_width = right - left
    text_height = bottom - top
    positions = {
        "top_left": (margin - left, margin - top),
        "top_right": (image.width - margin - right, margin - top),
        "bottom_left": (margin - left, image.height - margin - bottom),
        "bottom_right": (
            image.width - margin - right,
            image.height - margin - bottom,
        ),
        "center": (
            (image.width - text_width) // 2 - left,
            (image.height - text_height) // 2 - top,
        ),
    }
    return positions[position]


def apply_text_watermark(image: Image.Image, config: TextWatermarkConfig) -> Image.Image:
    """Return a new image with an optional readable text overlay."""
    if not isinstance(image, Image.Image):
        raise WatermarkServiceError("watermark source must be a Pillow image")
    if not config.enabled:
        return image.copy()

    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font_for_image(base, config.size_percent)
    stroke_width = max(1, round(font.size / 12))
    text_box = draw.textbbox(
        (0, 0),
        config.text,
        font=font,
        stroke_width=stroke_width,
    )
    x, y = _text_position(base, text_box, config.position, config.margin_percent)
    rgb = ImageColor.getrgb(config.color)
    alpha = round(255 * config.opacity / 100)
    draw.text(
        (x, y),
        config.text,
        font=font,
        fill=(*rgb, alpha),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, alpha),
    )
    return Image.alpha_composite(base, overlay)


def write_text_watermarked_copy(
    source_path: str,
    destination_path: str,
    config: TextWatermarkConfig,
) -> None:
    """Render a text watermark into a destination without touching the source.

    Staging and publishing both go through ``utils.atomic_staging``.
    ``tempfile.mkstemp(dir=...)`` cannot stage here: ``publish_service`` aims this
    writer at the folder the user picked for a publish, and its
    ``os.makedirs(..., exist_ok=True)`` returns cleanly on an existing read-only
    folder, so this was the FIRST write into that folder — and ``mkstemp`` read
    the folder's refusal as a name collision and retried it up to
    ``tempfile.TMP_MAX`` (2,147,483,647 on the shipped interpreter), turning a
    per-image error into an export that never answered. The plain copy branch
    beside this one failed fast all along.

    Publishing is shared for the same reason as the censor writer: sequential
    output names plus ``overwrite`` mean this lands on images the user already
    has, and a bare ``os.replace`` would sever a hard link and leave the alias
    holding the copy WITHOUT the watermark (``dd11296``).
    """
    source = os.path.abspath(source_path)
    destination = os.path.abspath(destination_path)
    if os.path.exists(destination):
        try:
            if os.path.samefile(source, destination):
                raise WatermarkServiceError(
                    "watermark destination must not be the source image"
                )
        except OSError as exc:
            raise WatermarkServiceError(
                f"watermark source/destination identity could not be checked: {exc}"
            ) from exc
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    suffix = os.path.splitext(destination)[1].lower()
    formats = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".webp": "WEBP",
    }
    image_format = formats.get(suffix)
    if image_format is None:
        raise WatermarkServiceError(
            f"watermark export does not support image extension {suffix!r}"
        )
    destination_path = Path(destination)
    staging_path: Optional[Path] = None
    try:
        with Image.open(source) as opened:
            opened.load()
            transformed = apply_text_watermark(opened, config)
            if image_format == "JPEG":
                transformed = transformed.convert("RGB")
            elif image_format == "PNG":
                transformed = transformed.convert("RGBA")
            save_options: dict[str, int | bool] = {}
            if image_format == "JPEG":
                save_options = {"quality": 95, "subsampling": 0, "optimize": True}
            elif image_format == "PNG":
                save_options = {"compress_level": 9, "optimize": True}
            elif image_format == "WEBP":
                save_options = {"lossless": True, "quality": 100, "method": 6}
            staging_path, descriptor = create_staging_sibling(destination_path)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                transformed.save(handle, format=image_format, **save_options)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        publish_staging_file(staging_path, destination_path)
        staging_path = None
    except (OSError, ValueError) as exc:
        raise WatermarkServiceError(
            f"failed to write watermarked image: source={source!r}, destination={destination!r}, error={exc}"
        ) from exc
    finally:
        if staging_path is not None:
            discard_staging_file(staging_path)


def _region_box(
    region: WatermarkRegion,
    image_size: tuple[int, int],
    padding_percent: int,
) -> tuple[int, int, int, int]:
    width, height = image_size
    left = round(width * region.x / 10000)
    top = round(height * region.y / 10000)
    right = round(width * (region.x + region.width) / 10000)
    bottom = round(height * (region.y + region.height) / 10000)
    padding = round(min(width, height) * padding_percent / 100)
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, max(left + 1, right + padding)),
        min(height, max(top + 1, bottom + padding)),
    )


def apply_watermark_removal(
    image: Image.Image,
    config: WatermarkRemovalConfig,
) -> Image.Image:
    """Inpaint only explicitly selected rectangles and return a new image."""
    if not isinstance(image, Image.Image):
        raise WatermarkServiceError("watermark removal source must be a Pillow image")
    if not config.enabled:
        return image.copy()
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise WatermarkServiceError(
            "Watermark removal requires OpenCV and NumPy. Prepare the censor/OpenCV dependencies, then retry."
        ) from exc

    rgba = image.convert("RGBA")
    source_rgb = np.array(rgba.convert("RGB"), dtype=np.uint8)
    mask = np.zeros((rgba.height, rgba.width), dtype=np.uint8)
    for region in config.regions:
        left, top, right, bottom = _region_box(
            region,
            rgba.size,
            config.padding_percent,
        )
        cv2.rectangle(mask, (left, top), (right - 1, bottom - 1), 255, thickness=-1)
    method = cv2.INPAINT_NS if config.method == "ns" else cv2.INPAINT_TELEA
    result_rgb = cv2.inpaint(source_rgb, mask, float(config.radius), method)
    result = Image.fromarray(result_rgb, mode="RGB")
    result.putalpha(rgba.getchannel("A"))
    if image.mode == "RGBA":
        return result
    if image.mode == "RGB":
        return result.convert("RGB")
    if image.mode == "L":
        return result.convert("L")
    return result.convert("RGB")
