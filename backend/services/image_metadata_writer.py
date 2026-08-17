"""Helpers for saving edited Reader metadata into image files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image, PngImagePlugin


JPEG_LIMITATION_WARNING = "JPEG metadata support is limited; use PNG for the most reliable SD prompt preservation."
WEBP_LIMITATION_WARNING = "WebP metadata support depends on the viewer; use PNG if another tool fails to read the saved prompt."
JPEG_ALPHA_WARNING = "JPEG does not support transparency; transparent pixels were flattened onto a white background."

# How many staging names to try before giving up. Only an abandoned staging file
# from a killed save needs stepping over, so the search stays short — see
# _create_staging_file for why an unbounded retry loop is the hazard here.
STAGING_NAME_ATTEMPTS = 8

EDITED_METADATA_KEY_ALIASES = {
    "negative prompt": "negative_prompt",
    "negative_prompt": "negative_prompt",
    "checkpoint": "model",
    "model_name": "model",
    "cfg": "cfg_scale",
    "cfg_scale": "cfg_scale",
    "cfg scale": "cfg_scale",
    "lora": "loras",
    "lora_text": "loras",
    "lora metadata": "loras",
    "lora_metadata": "loras",
}

PARAMETER_EXPORT_ORDER = [
    ("steps", "Steps"),
    ("sampler", "Sampler"),
    ("cfg_scale", "CFG scale"),
    ("seed", "Seed"),
    ("size", "Size"),
    ("model", "Model"),
    ("model_hash", "Model hash"),
    ("clip_skip", "Clip skip"),
    ("denoising_strength", "Denoising strength"),
    ("schedule_type", "Schedule type"),
    ("loras", "LoRAs"),
]


def normalize_edited_metadata(metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Normalize metadata keys from the editor into a stable backend shape."""
    normalized: dict[str, Any] = {}

    for raw_key, raw_value in (metadata or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            continue

        normalized_key = key.lower().replace("-", "_")
        canonical_key = EDITED_METADATA_KEY_ALIASES.get(normalized_key, normalized_key)
        value: Any = raw_value
        if isinstance(value, (list, tuple, set)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            value = ", ".join(parts) if parts else None
        elif isinstance(value, str):
            stripped = value.strip()
            value = stripped if stripped else None

        if value is None:
            continue

        normalized[canonical_key] = value

    if "size" not in normalized:
        width = normalized.get("width")
        height = normalized.get("height")
        if width is not None and height is not None:
            normalized["size"] = f"{width}x{height}"

    return normalized


def build_sd_parameters_text(metadata: dict[str, Any]) -> str:
    """Build a WebUI-style parameters blob that the existing parser can read back."""
    prompt = str(metadata.get("prompt") or "").strip()
    negative_prompt = str(metadata.get("negative_prompt") or "").strip()
    lines: list[str] = []
    if prompt:
        lines.append(prompt)
    if negative_prompt:
        lines.append(f"Negative prompt: {negative_prompt}")

    parts: list[str] = []
    emitted_keys = set()
    for key, label in PARAMETER_EXPORT_ORDER:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        emitted_keys.add(key)
        parts.append(f"{label}: {value}")

    extra_keys = sorted(
        key for key in metadata.keys()
        if key not in emitted_keys and key not in {"prompt", "negative_prompt", "width", "height"}
    )
    for key in extra_keys:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        label = " ".join(part.capitalize() for part in key.split("_"))
        parts.append(f"{label}: {value}")

    if parts:
        lines.append(", ".join(parts))

    return "\n".join(lines).strip()


def build_pnginfo(metadata: dict[str, Any], parameters_text: str) -> PngImagePlugin.PngInfo:
    pnginfo = PngImagePlugin.PngInfo()
    if parameters_text:
        pnginfo.add_text("parameters", parameters_text)

    pnginfo.add_text("Software", "SD Image Sorter")

    for key, value in metadata.items():
        if value is None or value == "":
            continue
        pnginfo.add_text(str(key), str(value))

    return pnginfo


def build_exif_bytes(image: Image.Image, parameters_text: str) -> Optional[bytes]:
    try:
        exif = image.getexif()
        if parameters_text:
            exif[0x010E] = parameters_text
        exif[0x0131] = "SD Image Sorter"
        return exif.tobytes()
    except Exception:
        return None


def prepare_image_for_save(image: Image.Image, pil_format: str, warnings: list[str]) -> Image.Image:
    """Prepare image mode conversions required by the target output format."""
    if pil_format != "JPEG":
        return image.copy()

    if image.mode in ("RGB", "L", "CMYK"):
        return image.copy()

    converted = image.convert("RGBA")
    background = Image.new("RGBA", converted.size, (255, 255, 255, 255))
    background.alpha_composite(converted)
    warnings.append(JPEG_ALPHA_WARNING)
    return background.convert("RGB")


def _discard_backup(backup_path: Path) -> None:
    """Drop a backup whose image is no longer the one on disk."""
    try:
        backup_path.unlink(missing_ok=True)
    except OSError:
        pass


def _destination_has_other_links(target: Path) -> bool:
    """Return whether other directory entries point at this destination's file."""
    try:
        return os.stat(target).st_nlink > 1
    except OSError:
        return False


def _copy_file_contents(source: Path, destination: Path) -> None:
    """Stream one file onto another, leaving the result durable on disk."""
    with open(source, "rb") as reader, open(destination, "wb") as writer:
        while True:
            block = reader.read(1024 * 1024)
            if not block:
                break
            writer.write(block)
        writer.flush()
        try:
            os.fsync(writer.fileno())
        except OSError:
            pass


def _overwrite_in_place(target: Path, source: Path) -> None:
    """Replace a file's contents without replacing the file itself."""
    with open(source, "rb") as reader, open(target, "r+b") as writer:
        written = 0
        while True:
            block = reader.read(1024 * 1024)
            if not block:
                break
            writer.write(block)
            written += len(block)
        writer.truncate(written)
        writer.flush()
        try:
            os.fsync(writer.fileno())
        except OSError:
            pass


def _create_staging_file(target: Path) -> Tuple[Path, int]:
    """Create the staging sibling, reporting an unwritable folder immediately.

    ``tempfile.NamedTemporaryFile(dir=...)`` cannot be used here. On Windows
    ``mkstemp`` treats ``PermissionError`` as "that random name is already
    taken" and retries up to ``TMP_MAX`` (10,000) times, because ``os.access``
    only inspects the read-only attribute and reports an ACL-protected folder
    such as ``C:\\Windows\\System32`` as writable. Saving into a folder the
    process cannot write to therefore HANGS instead of failing, turning a clean
    403 into a stuck request.

    ``O_CREAT | O_EXCL`` surfaces the real error on the first attempt, and the
    deterministic name matches ``tag_export.sidecars._write_sidecar_atomically``.
    A short bounded search still steps over an abandoned staging file.
    """
    suffix = target.suffix or ".tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    last_error: Optional[OSError] = None

    for attempt in range(STAGING_NAME_ATTEMPTS):
        marker = ".tmp" if attempt == 0 else f".tmp{attempt}"
        candidate = target.with_name(f".{target.name}{marker}{suffix}")
        try:
            return candidate, os.open(candidate, flags)
        except FileExistsError as exc:
            last_error = exc
            continue

    raise last_error if last_error is not None else OSError(
        f"Could not create a staging file beside {target}"
    )


def _encode_to_sibling(
    image: Image.Image,
    target: Path,
    pil_format: str,
    save_kwargs: Dict[str, Any],
) -> Path:
    """Encode the new image beside its destination and flush it to disk."""
    staging, descriptor = _create_staging_file(target)
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        _discard_backup(staging)
        raise

    try:
        with handle:
            image.save(handle, format=pil_format, **save_kwargs)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    except BaseException:
        _discard_backup(staging)
        raise

    return staging


def _publish_preserving_links(staging: Path, target: Path) -> None:
    """Update a hardlinked destination in place, behind an fsync'd backup.

    Rename-publishing here would hand this name a private new inode and leave
    every alias holding the pre-edit image, so the bytes have to go through the
    shared file. This is recovery rather than atomicity: a hard kill mid-write
    can still leave a partial image, but the previous one survives beside it as
    ``.<name>.bak`` instead of being gone. Same contract as
    ``tag_export.sidecars._write_sidecar_preserving_links`` (dd11296).
    """
    backup_path = target.with_name(f".{target.name}.bak")
    _copy_file_contents(target, backup_path)
    try:
        _overwrite_in_place(target, staging)
    except BaseException:
        try:
            _overwrite_in_place(target, backup_path)
        except OSError as restore_error:
            # Keep the backup: it is now the only complete copy of the image.
            raise OSError(
                f"Saving {target.name} failed and the original image could not be "
                f"restored; it is kept at {backup_path}: {restore_error}"
            ) from restore_error
        _discard_backup(backup_path)
        raise
    _discard_backup(backup_path)


def write_image_atomically(
    image: Image.Image,
    output_path: str,
    pil_format: str,
    save_kwargs: Dict[str, Any],
) -> None:
    """Publish an encoded image without ever exposing a half-written destination.

    ``Image.save`` opens its target ``w+b``, which truncates the user's existing
    file before the first new byte exists, and Pillow only deletes a file it
    created itself — so an interrupted overwrite left the original as
    undecodable garbage with no backup and no undo. Encode to an fsync'd sibling
    and publish it, the same temp-then-replace convention as
    ``dataset_export.engine._write_pillow_image_atomic`` and
    ``censor.output_io._save_pillow_image_atomically``.

    ``os.replace`` publishes a NEW inode, which silently severs a hard link and
    leaves every other name for that file holding the pre-edit image, so a
    destination with other links is updated in place instead (dd11296).

    Accepted trade: ``os.replace`` needs ``FILE_SHARE_DELETE`` on any concurrent
    handle and the Windows CRT does not grant it, so a destination open in
    another viewer can now fail where a bare write sometimes succeeded. It fails
    with the user's original intact, which is the correct direction.
    """
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging: Optional[Path] = _encode_to_sibling(image, target, pil_format, save_kwargs)
    try:
        if _destination_has_other_links(target):
            _publish_preserving_links(staging, target)
        else:
            try:
                os.replace(str(staging), str(target))
            except PermissionError as exc:
                raise PermissionError(
                    f"Could not replace {target.name}: the file is open in another "
                    f"program. Close it and save again. ({exc})"
                ) from exc
            staging = None
    finally:
        if staging is not None:
            _discard_backup(staging)
