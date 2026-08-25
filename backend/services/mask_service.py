"""Masked-training mask storage + generation (Phase 4 mask editor).

LoRA trainers support masked loss: a per-image grayscale mask weights the
loss so the model learns the subject and ignores the background (white =
train, black = ignore). This service owns those masks:

* storage — one PNG (mode L) per gallery image under ``DATA_DIR/masks/``,
  keyed by image id. No mask file simply means "train the whole image"
  (the trainers' own default), so absence is never an error.
* manual editing — the frontend canvas sends a data URL; we decode,
  convert to L and write atomically (tmp + os.replace).
* auto generation — rembg (u2net) or the opt-in Lucida subject-matting
  model. The generated mask is returned to the caller for review; nothing
  is saved until the user says so.

Export-side naming (OneTrainer ``<stem>-masklabel.png`` beside the image,
kohya ``mask/<stem>.png`` conditioning folder) lives in
``dataset_export_service`` next to the rename planner.
"""
import base64
import binascii
import io
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

import config
import database as db

logger = logging.getLogger(__name__)

MASKS_DIR: Path = Path(config.DATA_DIR) / "masks"

# Canvas data URLs for a 4k image land around 1-8 MB; 32 MB is a generous
# ceiling that still stops accidental multi-hundred-MB posts.
MAX_MASK_BYTES = 32 * 1024 * 1024

_DATA_URL_RE = re.compile(r"^data:image/(png|webp|jpeg);base64,(?P<body>[A-Za-z0-9+/=\s]+)$")

VALID_AUTO_METHODS = ("rembg", "lucida")


class MaskError(ValueError):
    """User-facing mask failure (router maps this to HTTP 400)."""


def mask_path(image_id: int) -> Path:
    return MASKS_DIR / f"{int(image_id)}.png"


def has_mask(image_id: int) -> bool:
    try:
        return mask_path(image_id).is_file()
    except OSError:
        return False


def get_mask_file(image_id: int) -> Optional[Path]:
    path = mask_path(image_id)
    return path if path.is_file() else None


def mask_status(image_ids: List[int]) -> Dict[str, bool]:
    """Which of these images carry a stored mask (JSON-friendly str keys)."""
    out: Dict[str, bool] = {}
    for value in image_ids or []:
        image_id = int(value)
        if image_id > 0:
            out[str(image_id)] = has_mask(image_id)
    return out


def _require_image_record(image_id: int) -> Dict[str, Any]:
    record = (db.get_images_by_ids([int(image_id)]) or {}).get(int(image_id))
    if not record:
        raise LookupError(f"Image {image_id} not found in library")
    return dict(record)


def _decode_mask_data_url(data_url: str) -> Image.Image:
    raw = str(data_url or "").strip()
    if len(raw) > MAX_MASK_BYTES:
        raise MaskError("Mask payload too large (over 32 MB). / 遮罩数据过大（超过 32 MB）。")
    match = _DATA_URL_RE.match(raw)
    if not match:
        raise MaskError(
            "Expected a base64 image data URL (data:image/png;base64,...). "
            "/ 需要 base64 图片 data URL。"
        )
    try:
        payload = base64.b64decode(match.group("body"), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise MaskError(f"Invalid base64 mask payload: {exc}") from exc
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
    except Exception as exc:
        raise MaskError(f"Mask payload is not a decodable image: {exc}") from exc
    return image


def save_mask_from_data_url(image_id: int, data_url: str) -> Dict[str, Any]:
    """Persist a user-edited mask. Grayscale L, atomic write."""
    record = _require_image_record(image_id)
    image = _decode_mask_data_url(data_url)
    mask = image.convert("L")

    MASKS_DIR.mkdir(parents=True, exist_ok=True)
    target = mask_path(image_id)
    tmp = target.with_suffix(".png.tmp")
    mask.save(tmp, format="PNG")
    os.replace(str(tmp), str(target))
    logger.info("Saved training mask for image %s (%sx%s)", image_id, mask.width, mask.height)
    return {
        "saved": True,
        "image_id": int(image_id),
        "width": mask.width,
        "height": mask.height,
        "filename": record.get("filename"),
    }


def delete_mask(image_id: int) -> bool:
    path = mask_path(image_id)
    if path.is_file():
        path.unlink()
        return True
    return False


def _rembg_session_home() -> str:
    """Keep the ~170 MB u2net download inside the app's data dir instead of
    the user profile, matching how other model assets stay portable."""
    home = Path(config.DATA_DIR) / "models" / "rembg"
    home.mkdir(parents=True, exist_ok=True)
    return str(home)


def generate_auto_mask(image_id: int, method: str = "rembg") -> Dict[str, Any]:
    """Generate a subject mask WITHOUT saving it — the frontend previews the
    result on the canvas and the user decides whether to keep/edit/save."""
    method = str(method or "rembg").strip().lower()
    if method not in VALID_AUTO_METHODS:
        raise MaskError(
            f"Unknown auto-mask method {method!r}; supported: {', '.join(VALID_AUTO_METHODS)}"
        )
    record = _require_image_record(image_id)
    src_path = str(record.get("path") or "")
    if not src_path or not os.path.exists(src_path):
        raise MaskError(f"Source image missing on disk: {src_path!r}")

    with Image.open(src_path) as source:
        rgb = source.convert("RGB")

    if method == "lucida":
        try:
            from lucida_matting import LucidaError, generate_subject_mask

            mask = generate_subject_mask(rgb, use_gpu=config.LUCIDA_USE_GPU)
        except LucidaError as exc:
            raise MaskError(str(exc)) from exc
    else:
        os.environ.setdefault("U2NET_HOME", _rembg_session_home())
        try:
            from rembg import remove  # noqa: PLC0415 - heavy opt-in dependency
        except ImportError as exc:
            raise MaskError(
                "rembg is not installed. Install it into the backend environment "
                "with: pip install rembg  (ONNX Runtime is already bundled; the "
                "u2net model (~170 MB) downloads on first use.) / 未安装 rembg。"
                "请在后端环境执行 pip install rembg（首次使用会自动下载 u2net 模型，约 170 MB）。"
            ) from exc

        result = remove(rgb)
        if result.mode != "RGBA":
            result = result.convert("RGBA")
        mask = result.split()[-1].convert("L")

    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "image_id": int(image_id),
        "method": method,
        "width": mask.width,
        "height": mask.height,
        "data_url": data_url,
        "saved": False,
    }


def generate_and_save_auto_mask(
    image_id: int,
    method: str = "lucida",
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Generate a subject mask and persist it for training export."""
    if has_mask(image_id) and not overwrite:
        record = _require_image_record(image_id)
        return {
            "image_id": int(image_id),
            "method": str(method or "").strip().lower(),
            "saved": False,
            "skipped": True,
            "reason": "exists",
            "filename": record.get("filename"),
        }
    preview = generate_auto_mask(image_id, method)
    saved = save_mask_from_data_url(image_id, preview["data_url"])
    return {
        **saved,
        "method": preview["method"],
        "skipped": False,
    }


def run_auto_mask_batch(
    image_ids: List[int],
    method: str = "lucida",
    *,
    overwrite: bool = False,
    cancellation_requested=None,
    progress_callback=None,
) -> Dict[str, Any]:
    """Generate+save masks for many gallery images. Skips existing unless overwrite."""
    ids: List[int] = []
    seen = set()
    for value in image_ids or []:
        image_id = int(value)
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        ids.append(image_id)
    if not ids:
        raise MaskError("No gallery image ids were provided. / 没有提供图库图片 ID。")

    saved = 0
    skipped = 0
    errors: List[Dict[str, Any]] = []
    total = len(ids)
    for index, image_id in enumerate(ids, start=1):
        if cancellation_requested and cancellation_requested():
            break
        try:
            result = generate_and_save_auto_mask(
                image_id,
                method,
                overwrite=overwrite,
            )
            if result.get("skipped"):
                skipped += 1
            else:
                saved += 1
        except LookupError as exc:
            errors.append({"image_id": image_id, "error": str(exc)})
        except MaskError as exc:
            errors.append({"image_id": image_id, "error": str(exc)})
        if progress_callback:
            progress_callback(
                processed=index,
                total=total,
                saved=saved,
                skipped=skipped,
                errors=len(errors),
                current_image_id=image_id,
            )
    return {
        "method": str(method or "").strip().lower(),
        "overwrite": bool(overwrite),
        "total": total,
        "saved": saved,
        "skipped": skipped,
        "error_count": len(errors),
        "errors": errors[:20],
        "cancelled": bool(cancellation_requested and cancellation_requested()),
    }
