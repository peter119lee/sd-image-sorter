"""Helpers for saving edited Reader metadata into image files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, PngImagePlugin


JPEG_LIMITATION_WARNING = "JPEG metadata support is limited; use PNG for the most reliable SD prompt preservation."
WEBP_LIMITATION_WARNING = "WebP metadata support depends on the viewer; use PNG if another tool fails to read the saved prompt."
JPEG_ALPHA_WARNING = "JPEG does not support transparency; transparent pixels were flattened onto a white background."

# Editing one field used to rebuild the text chunks from scratch, so a save
# destroyed the ComfyUI workflow, the NovelAI Comment block and every
# third-party chunk. The blocks that survive are now reported when they can no
# longer agree with the edit, and named explicitly when the chosen container
# cannot hold them at all.
PRESERVED_GENERATION_RECORD_WARNING = (
    "Kept the original embedded generation record ({keys}) so it is not lost. "
    "It still describes the original generation, so your edits are not reflected inside it."
)
UNCARRIED_CHUNKS_WARNING = (
    "{label} cannot store these embedded metadata blocks, so they were not carried over: "
    "{keys}. Save as PNG to keep them."
)

DROPPED_PARAMETER_SETTINGS_WARNING = (
    "The editor rebuilds the parameter block from the fields it shows, so these "
    "settings from the original are not in the saved file: {keys}."
)


FORMAT_LABELS = {"PNG": "PNG", "WEBP": "WebP", "JPEG": "JPEG"}


# Ceiling on a single carried text chunk, matching obfuscation's own limit for
# harvested EXIF/XMP text: a real parameter block or workflow is a few kB to a
# few hundred kB, and anything larger is a broken or hostile file.
MAX_CARRIED_CHUNK_BYTES = 1024 * 1024

# ``Image.info`` mixes text chunks with decoder state. These keys are never
# user metadata, and writing them back as tEXt would corrupt the output (same
# exclusion list as censor.output_io._copy_png_text_metadata).
NON_TEXT_IMAGE_INFO_KEYS = frozenset({
    "exif", "icc_profile", "dpi", "interlace", "gamma", "chromaticity",
    "transparency", "palette", "xmp", "photoshop", "adobe", "adobe_transform",
    "jfif", "jfif_version", "jfif_unit", "jfif_density", "progression",
    "progressive", "aspect", "background", "loop", "duration", "bits",
    "compression", "extrasamples", "resolution", "srgb", "chromatic",
})

# Chunk names the editor itself always rewrites, so a source copy of them is
# stale by definition and must never be carried forward.
EDITOR_OWNED_CHUNK_KEYS = frozenset({"parameters", "Software"})

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

# Chunk names the editor is entitled to rewrite, derived from the tables above
# rather than restated, so adding an editable parameter extends this set too.
#
# It deliberately does NOT depend on the keys present in one payload: the Reader
# omits a field the user cleared, so a payload-derived set would carry the
# source's stale chunk back and silently undo the clear.
EDITOR_FIELD_CHUNK_KEYS = frozenset(
    {"prompt", "negative_prompt", "width", "height", "size"}
    | set(EDITED_METADATA_KEY_ALIASES.values())
    | {key for key, _label in PARAMETER_EXPORT_ORDER}
)

# Settings names this module knows how to emit, used to tell an A1111 settings
# line apart from a prompt line that merely happens to contain a colon.
KNOWN_PARAMETER_LABELS = frozenset(label for _key, label in PARAMETER_EXPORT_ORDER)


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


def _classify_embedded_text(text: str) -> str:
    """Name the chunk this text belongs in, reusing obfuscation's classifier.

    ``obfuscation._text_chunk_key_for`` (8982d08) already encodes the shape
    tests for a ComfyUI API graph, a NovelAI ``Comment`` payload and an A1111
    parameter block, so it is reused rather than restated here. Imported lazily
    because ``obfuscation`` pulls in ``database`` and the mutation service,
    which this module must not require at import time.
    """
    try:
        from obfuscation import _text_chunk_key_for

        return _text_chunk_key_for(text)
    except Exception:
        return "UserComment"


def _harvest_container_text(image: Image.Image) -> List[Tuple[str, str]]:
    """Collect SD metadata a JPEG/WebP/TIFF source keeps in EXIF or XMP.

    A non-PNG source carries no text chunks, so the prompt lives in EXIF
    ``UserComment`` / ``ImageDescription`` or in an XMP packet and has to be
    re-keyed to whatever the PNG reader looks for. Mirrors
    ``obfuscation._harvest_non_png_text_chunks``, reusing its decoder so the
    UserComment encodings are not restated.
    """
    found: List[Tuple[str, str]] = []
    try:
        from obfuscation import _decode_exif_user_comment
    except Exception:
        return found

    try:
        exif = image.getexif()
    except Exception:
        exif = None

    if exif:
        try:
            user_comment = exif.get_ifd(0x8769).get(0x9286)
        except Exception:
            user_comment = None
        if user_comment:
            decoded = _decode_exif_user_comment(user_comment)
            if decoded:
                found.append((_classify_embedded_text(decoded), decoded))

        try:
            description = exif.get(0x010E)
        except Exception:
            description = None
        # Same test obfuscation applies: only an actual parameter block is
        # carried. A camera's caption keyed as an SD chunk would be metadata
        # this app invented rather than preserved.
        if isinstance(description, str) and "Steps:" in description and "Sampler:" in description:
            found.append(("parameters", description))

    xmp = (image.info or {}).get("xmp")
    if xmp:
        text = xmp.decode("utf-8", errors="replace") if isinstance(xmp, (bytes, bytearray)) else str(xmp)
        if "Steps:" in text and "Sampler:" in text:
            found.append(("parameters", text))

    return found


def harvest_source_text_chunks(image: Image.Image) -> Dict[str, str]:
    """Collect the embedded text a source image carries, whatever its container."""
    chunks: Dict[str, str] = {}

    def _put(key: str, value: str) -> None:
        cleaned = str(value or "").strip()
        if not key or not cleaned:
            return
        if len(cleaned.encode("utf-8", errors="ignore")) > MAX_CARRIED_CHUNK_BYTES:
            return
        chunks.setdefault(key, cleaned)

    for key, value in (image.info or {}).items():
        if not isinstance(key, str) or key in NON_TEXT_IMAGE_INFO_KEYS:
            continue
        if isinstance(value, str):
            _put(key, value)
        elif isinstance(value, (bytes, bytearray)):
            for encoding in ("utf-8", "latin-1"):
                try:
                    _put(key, bytes(value).decode(encoding))
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue

    if str(getattr(image, "format", "") or "").upper() != "PNG":
        for key, value in _harvest_container_text(image):
            _put(key, value)

    return chunks


def _is_irreplaceable_generation_record(key: str, value: str) -> bool:
    """Whether this chunk is a full generation record rather than a loose field.

    A ComfyUI graph is the single most valuable thing in a generated PNG and
    cannot be reconstructed from the editor's flat fields, so it outranks the
    editor's own redundant copy of the chunk name. A plain string that merely
    happens to sit under ``prompt`` — what an earlier Reader save left behind —
    is not a record and loses to the edit.
    """
    if key == "workflow":
        return True
    return _classify_embedded_text(value) in {"prompt", "Comment", "parameters"}


def preservable_source_chunks(
    source_chunks: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Pick the source chunks that must survive an edit.

    Anything outside the editor's own field names is carried through untouched.
    A chunk that shares a name with an editable field loses to the edit unless
    its value is an irreplaceable generation record — otherwise a cleared field
    would be silently resurrected out of the source's stale chunk.
    """
    preserved: Dict[str, str] = {}

    for key, value in (source_chunks or {}).items():
        if key in EDITOR_OWNED_CHUNK_KEYS:
            continue
        if key in EDITOR_FIELD_CHUNK_KEYS and not _is_irreplaceable_generation_record(key, value):
            continue
        preserved[key] = value

    return preserved


def build_pnginfo(
    metadata: dict[str, Any],
    parameters_text: str,
    *,
    source_chunks: Optional[Dict[str, str]] = None,
    warnings: Optional[List[str]] = None,
) -> PngImagePlugin.PngInfo:
    """Build the output text chunks: the edit, plus everything it did not touch."""
    pnginfo = PngImagePlugin.PngInfo()
    written: set[str] = set()

    def _add(key: str, value: str) -> None:
        if key in written:
            return
        pnginfo.add_text(key, value)
        written.add(key)

    if parameters_text:
        _add("parameters", parameters_text)

    _add("Software", "SD Image Sorter")

    preserved = preservable_source_chunks(source_chunks)

    for key, value in metadata.items():
        if value is None or value == "":
            continue
        if str(key) in preserved:
            continue
        _add(str(key), str(value))

    for key, value in preserved.items():
        _add(key, value)

    if warnings is not None and parameters_text:
        record_keys = sorted(
            key for key, value in preserved.items()
            if _is_irreplaceable_generation_record(key, value)
        )
        if record_keys:
            warnings.append(
                PRESERVED_GENERATION_RECORD_WARNING.format(keys=", ".join(record_keys))
            )

    return pnginfo


def _parameter_setting_labels(parameters_text: str) -> set[str]:
    """Extract the ``Label:`` names from an A1111 settings line.

    Only the final line is read, because that is where both A1111 and
    ``build_sd_parameters_text`` put the settings — so a colon inside the prompt
    cannot be mistaken for a setting name. A line that names none of the known
    settings is treated as prose and yields nothing.
    """
    lines = [line for line in str(parameters_text or "").splitlines() if line.strip()]
    if not lines:
        return set()

    labels: set[str] = set()
    for part in lines[-1].split(","):
        label, separator, _value = part.partition(":")
        if separator and label.strip():
            labels.add(label.strip())

    return labels if labels & KNOWN_PARAMETER_LABELS else set()


def dropped_parameter_settings_warning(
    source_chunks: Optional[Dict[str, str]],
    parameters_text: str,
) -> Optional[str]:
    """Name settings the original parameter block had and the rebuilt one lacks.

    The editor owns the ``parameters`` chunk and rebuilds it from the fields it
    displays, so a setting the Reader does not show (``Model hash``,
    ``Clip skip``, an extension's own key) disappears on save. Merging unknown
    A1111 keys back in would mean re-emitting someone else's parameter syntax,
    which risks corrupting the one block the app itself reads, so the loss is
    reported instead of guessed at.
    """
    source_parameters = (source_chunks or {}).get("parameters")
    if not source_parameters:
        return None

    dropped = _parameter_setting_labels(source_parameters) - _parameter_setting_labels(parameters_text)
    if not dropped:
        return None

    return DROPPED_PARAMETER_SETTINGS_WARNING.format(keys=", ".join(sorted(dropped)))


def uncarried_chunk_warning(
    source_chunks: Optional[Dict[str, str]],
    pil_format: str,
    *,
    source_format: Optional[str] = None,
) -> Optional[str]:
    """Name the embedded blocks the chosen container cannot hold.

    PNG keeps every chunk. JPEG and WebP have one text slot, filled by the
    edited parameter block, so a PNG source's other chunks are genuinely lost —
    which the user is told rather than left to discover.

    A non-PNG source is not warned about: its metadata already lives in EXIF,
    and ``build_exif_bytes`` re-serializes the source EXIF including the sub-IFD
    that holds ``UserComment``, so those blocks do survive (verified for both
    JPEG and WebP). Warning there would be crying wolf.
    """
    if pil_format == "PNG":
        return None
    if str(source_format or "").upper() != "PNG":
        return None

    lost = sorted(key for key in (source_chunks or {}) if key not in EDITOR_OWNED_CHUNK_KEYS)
    if not lost:
        return None

    return UNCARRIED_CHUNKS_WARNING.format(
        label=FORMAT_LABELS.get(pil_format, pil_format),
        keys=", ".join(lost),
    )


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

