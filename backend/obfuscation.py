"""
Reference-compatible image obfuscation helpers.

This module follows the public dfqtphx workflow closely enough for
round-tripping with the same password scheme:
- generalized Gilbert curve ordering for arbitrary image sizes
- golden-ratio offset pixel rotation
- password digits: step / extra-width / extra-height
- PNG text chunk encryption with legacy and modern modes
"""

from __future__ import annotations

import base64
import io
import json
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from PIL import Image
import database as db
from services.indexed_file_mutation_service import save_and_reconcile_checked


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BIG_TOMATO_MODE = "big_tomato"
SMALL_TOMATO_MODE = "small_tomato"
MAX_OBFUSCATE_SOURCE_BYTES = 50 * 1024 * 1024
MAX_OBFUSCATE_SOURCE_PIXELS = 40_000_000
# Ceiling on a single harvested EXIF/XMP text value. Real A1111 parameter
# blocks are a few kB; an XMP packet large enough to exceed this is a hostile
# or broken file, and carrying it would balloon the protected PNG.
_MAX_CARRIED_TEXT_BYTES = 1024 * 1024

COMPAT_MODE_ALIASES = {
    "": BIG_TOMATO_MODE,
    BIG_TOMATO_MODE: BIG_TOMATO_MODE,
    "big": BIG_TOMATO_MODE,
    "dfqtphx": BIG_TOMATO_MODE,
    "large_tomato": BIG_TOMATO_MODE,
    SMALL_TOMATO_MODE: SMALL_TOMATO_MODE,
    "small": SMALL_TOMATO_MODE,
    "singularpoint": SMALL_TOMATO_MODE,
    "hideimg1": SMALL_TOMATO_MODE,
}


class ImageTooLargeError(ValueError):
    """Raised when obfuscation input exceeds safe memory limits."""


class ObfuscationOverwriteConflictError(RuntimeError):
    """Raised when an output file exists but overwrite was not explicitly allowed."""


def _obfuscate_max_bytes_message() -> str:
    return f"Image file too large (max {MAX_OBFUSCATE_SOURCE_BYTES // (1024 * 1024)}MB)"


def _obfuscate_max_pixels_message() -> str:
    megapixels = MAX_OBFUSCATE_SOURCE_PIXELS / 1_000_000
    return f"Image too large for safe processing (max {megapixels:.1f}MP)"


def _validate_obfuscation_source_byte_length(byte_length: int) -> None:
    if byte_length > MAX_OBFUSCATE_SOURCE_BYTES:
        raise ImageTooLargeError(_obfuscate_max_bytes_message())


def _validate_obfuscation_source_dimensions(width: int, height: int) -> None:
    if width * height > MAX_OBFUSCATE_SOURCE_PIXELS:
        raise ImageTooLargeError(_obfuscate_max_pixels_message())


def _validate_obfuscation_source_bytes(image_bytes: bytes) -> None:
    _validate_obfuscation_source_byte_length(len(image_bytes))
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    _validate_obfuscation_source_dimensions(width, height)


def _validate_obfuscation_source_file(path: str) -> None:
    _validate_obfuscation_source_byte_length(Path(path).stat().st_size)


@dataclass(frozen=True)
class ObfuscationPassword:
    step: int = 1
    extra_width: int = 0
    extra_height: int = 0

    @property
    def key(self) -> Tuple[int, int, int]:
        return (self.step, self.extra_width, self.extra_height)


def normalize_compat_mode(compat_mode: str = "") -> str:
    raw = str(compat_mode or BIG_TOMATO_MODE).strip().lower()
    normalized = COMPAT_MODE_ALIASES.get(raw)
    if normalized:
        return normalized
    raise ValueError(f"Unsupported compat mode: {compat_mode}")


def _resolve_password(password: str, compat_mode: str) -> ObfuscationPassword:
    if normalize_compat_mode(compat_mode) == SMALL_TOMATO_MODE:
        return ObfuscationPassword(step=1, extra_width=0, extra_height=0)
    return parse_password(password)


def parse_password(password: str) -> ObfuscationPassword:
    raw = str(password or "")
    if not raw:
        return ObfuscationPassword()

    step_part = raw[:2]
    extra_width_part = raw[2:3]
    extra_height_part = raw[3:4]

    try:
        step = max(1, int(step_part)) if step_part else 1
    except ValueError:
        step = 1

    def _digit(value: str) -> int:
        if not value:
            return 0
        try:
            return int(value)
        except ValueError:
            return 0

    return ObfuscationPassword(
        step=step,
        extra_width=_digit(extra_width_part),
        extra_height=_digit(extra_height_part),
    )


def _unit_sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _generate_2d(
    x: int,
    y: int,
    ax: int,
    ay: int,
    bx: int,
    by: int,
) -> Iterator[Tuple[int, int]]:
    w = abs(ax + ay)
    h = abs(bx + by)

    dax = _unit_sign(ax)
    day = _unit_sign(ay)
    dbx = _unit_sign(bx)
    dby = _unit_sign(by)

    if h == 1:
        for _ in range(w):
            yield (x, y)
            x += dax
            y += day
        return

    if w == 1:
        for _ in range(h):
            yield (x, y)
            x += dbx
            y += dby
        return

    ax2 = math.floor(ax / 2)
    ay2 = math.floor(ay / 2)
    bx2 = math.floor(bx / 2)
    by2 = math.floor(by / 2)

    w2 = abs(ax2 + ay2)
    h2 = abs(bx2 + by2)

    if 2 * w > 3 * h:
        if (w2 % 2) and (w > 2):
            ax2 += dax
            ay2 += day

        yield from _generate_2d(x, y, ax2, ay2, bx, by)
        yield from _generate_2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by)
        return

    if (h2 % 2) and (h > 2):
        bx2 += dbx
        by2 += dby

    yield from _generate_2d(x, y, bx2, by2, ax2, ay2)
    yield from _generate_2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2)
    yield from _generate_2d(
        x + (ax - dax) + (bx2 - dbx),
        y + (ay - day) + (by2 - dby),
        -bx2,
        -by2,
        -(ax - ax2),
        -(ay - ay2),
    )


def gilbert2d(width: int, height: int) -> List[Tuple[int, int]]:
    if width <= 0 or height <= 0:
        return []

    if width >= height:
        return list(_generate_2d(0, 0, width, 0, 0, height))
    return list(_generate_2d(0, 0, 0, height, width, 0))


def _pixel_positions(width: int, height: int) -> Tuple[List[int], List[int]]:
    total_pixels = width * height
    curve = gilbert2d(width, height)
    offset = round(((math.sqrt(5) - 1) / 2) * total_pixels)

    old_positions: List[int] = [0] * total_pixels
    new_positions: List[int] = [0] * total_pixels

    for index, (old_x, old_y) in enumerate(curve):
        new_x, new_y = curve[(index + offset) % total_pixels]
        old_positions[index] = 4 * (old_x + old_y * width)
        new_positions[index] = 4 * (new_x + new_y * width)

    return old_positions, new_positions


def add_padding_to_rgba(
    rgba: bytes,
    width: int,
    height: int,
    extra_width: int,
    extra_height: int,
) -> bytes:
    new_width = width + extra_width
    new_height = height + extra_height
    output = bytearray(new_width * new_height * 4)

    for y in range(new_height):
        for x in range(new_width):
            output_index = 4 * (x + y * new_width)
            if y < height and x < width:
                source_index = 4 * (x + y * width)
            elif y < height:
                source_index = 4 * ((width - 1) + y * width)
            else:
                last_row = height - 1
                source_x = min(x, width - 1)
                source_index = 4 * (source_x + last_row * width)

            output[output_index:output_index + 4] = rgba[source_index:source_index + 4]

    return bytes(output)


def crop_rgba(rgba: bytes, width: int, height: int, remove_width: int, remove_height: int) -> Tuple[bytes, int, int]:
    cropped_width = width - remove_width
    cropped_height = height - remove_height
    output = bytearray(cropped_width * cropped_height * 4)

    for y in range(cropped_height):
        for x in range(cropped_width):
            output_index = 4 * (x + y * cropped_width)
            source_index = 4 * (x + y * width)
            output[output_index:output_index + 4] = rgba[source_index:source_index + 4]

    return bytes(output), cropped_width, cropped_height


def encrypt_rgba(rgba: bytes, width: int, height: int, password: ObfuscationPassword) -> Tuple[bytes, int, int]:
    old_positions, new_positions = _pixel_positions(width, height)
    current = bytearray(rgba)
    buffer = bytearray(len(current))

    for _ in range(password.step):
        for old_pos, new_pos in zip(old_positions, new_positions):
            buffer[new_pos:new_pos + 4] = current[old_pos:old_pos + 4]
        current[:] = buffer

    padded = add_padding_to_rgba(
        bytes(current),
        width,
        height,
        password.extra_width,
        password.extra_height,
    )
    return padded, width + password.extra_width, height + password.extra_height


def decrypt_rgba(rgba: bytes, width: int, height: int, password: ObfuscationPassword) -> Tuple[bytes, int, int]:
    cropped_rgba, cropped_width, cropped_height = crop_rgba(
        rgba,
        width,
        height,
        password.extra_width,
        password.extra_height,
    )

    old_positions, new_positions = _pixel_positions(cropped_width, cropped_height)
    current = bytearray(cropped_rgba)
    buffer = bytearray(len(current))

    for _ in range(password.step):
        for old_pos, new_pos in zip(old_positions, new_positions):
            buffer[old_pos:old_pos + 4] = current[new_pos:new_pos + 4]
        current[:] = buffer

    return bytes(current), cropped_width, cropped_height


def _encode_base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode_base64_text(value: str) -> Optional[str]:
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def encrypt_text(value: str, key: Sequence[int], legacy_mode: bool = False) -> str:
    source = value if legacy_mode else _encode_base64_text(value)
    chars = []
    for index, char in enumerate(source):
        offset = key[index % len(key)]
        chars.append(chr(ord(char) + offset))
    return "".join(chars)


def decrypt_text(value: str, key: Sequence[int], legacy_mode: bool = False) -> str:
    chars = []
    for index, char in enumerate(value):
        offset = key[index % len(key)]
        chars.append(chr(ord(char) - offset))
    decoded = "".join(chars)
    if legacy_mode:
        return decoded
    return _decode_base64_text(decoded) or ""


def extract_png_text_chunks_from_bytes(data: bytes) -> List[Tuple[str, str]]:
    if not data.startswith(PNG_SIGNATURE):
        return []

    chunks: List[Tuple[str, str]] = []
    offset = len(PNG_SIGNATURE)
    total = len(data)

    while offset + 12 <= total:
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        offset += 12 + length

        if chunk_type not in (b"tEXt", b"iTXt"):
            continue

        try:
            decoded = chunk_data.decode("utf-8", errors="ignore")
        except Exception:
            continue

        parts = decoded.split("\x00")
        if len(parts) < 2:
            continue

        key = parts[0]
        value = parts[-1]
        if key and value:
            chunks.append((key, value))

    return chunks


def _decode_exif_user_comment(value: object) -> str:
    """Decode an EXIF UserComment payload the way SD tools write it.

    Mirrors metadata_parser's decoder for the shapes that matter here. It is
    reimplemented rather than imported so the obfuscation round trip, which only
    needs to move text from one container to another, does not pull the whole
    metadata-parsing stack in behind it.
    """
    if isinstance(value, str):
        text = value
        if text.startswith("ASCII") or text.startswith("UNICODE"):
            text = text[7:]
        return text.strip("\0 ")
    if not isinstance(value, (bytes, bytearray)):
        return ""

    raw = bytes(value)
    if raw.startswith(b"UNICODE\x00"):
        payload = raw[8:]
        if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
            text = payload.decode("utf-16", errors="replace")
        elif len(payload) >= 2 and payload[1:2] == b"\x00":
            text = payload.decode("utf-16-le", errors="replace")
        else:
            text = payload.decode("utf-16-be", errors="replace")
    elif raw.startswith(b"ASCII\x00\x00\x00") or raw.startswith(b"\x00" * 8):
        text = raw[8:].decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.strip("\0 ")


def _text_chunk_key_for(text: str) -> str:
    """Pick the PNG text-chunk key that carries ``text`` back to the parser.

    The obfuscated and restored files are always PNGs, so metadata harvested
    from a JPEG's or WebP's EXIF/XMP has to be re-keyed to whatever the PNG
    reader looks for. Unrecognised text is still carried, under UserComment, so
    a round trip never silently drops something it could not classify.
    """
    stripped = text.strip()
    if "Steps:" in stripped and "Sampler:" in stripped:
        return "parameters"
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except (ValueError, TypeError):
            return "UserComment"
        if isinstance(payload, dict):
            if any(isinstance(v, dict) and "class_type" in v for v in payload.values()):
                return "prompt"
            if "prompt" in payload and "uc" in payload:
                return "Comment"
    return "UserComment"


def _harvest_non_png_text_chunks(data: bytes) -> List[Tuple[str, str]]:
    """Collect SD metadata that a JPEG/WebP/TIFF source carries in EXIF or XMP."""
    chunks: List[Tuple[str, str]] = []
    seen: set = set()

    def _add(key: str, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned or len(cleaned.encode("utf-8", errors="ignore")) > _MAX_CARRIED_TEXT_BYTES:
            return
        if key in seen:
            return
        seen.add(key)
        chunks.append((key, cleaned))

    try:
        with Image.open(io.BytesIO(data)) as image:
            xmp = image.info.get("xmp")
            exif = image.getexif()
    except Exception:  # noqa: BLE001 - an unreadable source simply carries nothing
        return chunks

    try:
        user_comment = exif.get_ifd(0x8769).get(0x9286) if exif else None
    except Exception:  # noqa: BLE001 - malformed Exif IFD
        user_comment = None
    if user_comment:
        decoded = _decode_exif_user_comment(user_comment)
        if decoded:
            _add(_text_chunk_key_for(decoded), decoded)

    try:
        description = exif.get(0x010E) if exif else None
    except Exception:  # noqa: BLE001
        description = None
    if isinstance(description, str) and "Steps:" in description and "Sampler:" in description:
        _add("parameters", description)

    if xmp:
        text = xmp.decode("utf-8", errors="replace") if isinstance(xmp, (bytes, bytearray)) else str(xmp)
        if "Steps:" in text and "Sampler:" in text:
            _add("parameters", text)

    return chunks


def extract_source_text_chunks_from_bytes(data: bytes) -> List[Tuple[str, str]]:
    """Collect the SD metadata a source image carries, whatever its container.

    PNG sources keep their tEXt/iTXt chunks verbatim. JPEG, WebP and TIFF
    sources carry the same information in EXIF or XMP instead, which the
    PNG-only reader used to return ``[]`` for - that is how the prompt went
    missing across a protect/restore round trip for those formats.
    """
    if data.startswith(PNG_SIGNATURE):
        return extract_png_text_chunks_from_bytes(data)
    return _harvest_non_png_text_chunks(data)


def write_png_text_chunks(
    png_bytes: bytes,
    text_chunks: Sequence[Tuple[str, str]],
    password: ObfuscationPassword,
    decrypt_values: bool = False,
    legacy_pnginfo: bool = False,
) -> bytes:
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("Not a PNG file")

    offset = len(PNG_SIGNATURE)
    total = len(png_bytes)
    parsed_chunks: List[Tuple[bytes, bytes, bytes]] = []

    while offset + 12 <= total:
        length = struct.unpack(">I", png_bytes[offset:offset + 4])[0]
        chunk_type = png_bytes[offset + 4:offset + 8]
        chunk_data = png_bytes[offset + 8:offset + 8 + length]
        chunk_crc = png_bytes[offset + 8 + length:offset + 12 + length]
        parsed_chunks.append((chunk_type, chunk_data, chunk_crc))
        offset += 12 + length

    encoded_chunks: List[Tuple[bytes, bytes, bytes]] = []
    for key, value in text_chunks:
        transformed = decrypt_text(value, password.key, legacy_mode=legacy_pnginfo) if decrypt_values else encrypt_text(value, password.key, legacy_mode=legacy_pnginfo)
        payload = key.encode("utf-8") + b"\x00" + transformed.encode("utf-8")
        crc = struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF)
        encoded_chunks.append((b"tEXt", payload, crc))

    insert_at = next((idx for idx, (chunk_type, _, _) in enumerate(parsed_chunks) if chunk_type == b"IDAT"), len(parsed_chunks))
    rebuilt_chunks = [
        *parsed_chunks[:insert_at],
        *encoded_chunks,
        *parsed_chunks[insert_at:],
    ]

    output = bytearray(PNG_SIGNATURE)
    for chunk_type, chunk_data, chunk_crc in rebuilt_chunks:
        output.extend(struct.pack(">I", len(chunk_data)))
        output.extend(chunk_type)
        output.extend(chunk_data)
        output.extend(chunk_crc)

    return bytes(output)


def image_from_rgba(rgba: bytes, width: int, height: int) -> Image.Image:
    return Image.frombytes("RGBA", (width, height), rgba)


def _image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def encode_image_bytes(
    image_bytes: bytes,
    password: str,
    text_chunks: Optional[Sequence[Tuple[str, str]]] = None,
    preserve_metadata: bool = True,
    legacy_pnginfo: bool = False,
    compat_mode: str = BIG_TOMATO_MODE,
) -> bytes:
    _validate_obfuscation_source_bytes(image_bytes)
    normalized_mode = normalize_compat_mode(compat_mode)
    parsed_password = _resolve_password(password, normalized_mode)
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgba_image = image.convert("RGBA")
        width, height = rgba_image.size
        encoded_rgba, encoded_width, encoded_height = encrypt_rgba(
            rgba_image.tobytes(),
            width,
            height,
            parsed_password,
        )

    output_image = image_from_rgba(encoded_rgba, encoded_width, encoded_height)
    png_bytes = _image_to_png_bytes(output_image)

    if preserve_metadata and text_chunks:
        png_bytes = write_png_text_chunks(
            png_bytes,
            text_chunks,
            parsed_password,
            decrypt_values=False,
            legacy_pnginfo=legacy_pnginfo,
        )

    return png_bytes


def decode_image_bytes(
    image_bytes: bytes,
    password: str,
    text_chunks: Optional[Sequence[Tuple[str, str]]] = None,
    preserve_metadata: bool = True,
    legacy_pnginfo: bool = False,
    compat_mode: str = BIG_TOMATO_MODE,
) -> bytes:
    _validate_obfuscation_source_bytes(image_bytes)
    normalized_mode = normalize_compat_mode(compat_mode)
    parsed_password = _resolve_password(password, normalized_mode)
    with Image.open(io.BytesIO(image_bytes)) as image:
        rgba_image = image.convert("RGBA")
        width, height = rgba_image.size
        decoded_rgba, decoded_width, decoded_height = decrypt_rgba(
            rgba_image.tobytes(),
            width,
            height,
            parsed_password,
        )

    output_image = image_from_rgba(decoded_rgba, decoded_width, decoded_height)
    png_bytes = _image_to_png_bytes(output_image)

    if preserve_metadata and text_chunks:
        png_bytes = write_png_text_chunks(
            png_bytes,
            text_chunks,
            parsed_password,
            decrypt_values=True,
            legacy_pnginfo=legacy_pnginfo,
        )

    return png_bytes


def extract_png_text_chunks(path: str) -> Optional[Dict[str, str]]:
    data = Path(path).read_bytes()
    chunks = extract_png_text_chunks_from_bytes(data)
    if not chunks:
        return None
    return {key: value for key, value in chunks}


def encode_image(
    input_path: str,
    output_path: str,
    password: str,
    preserve_metadata: bool = True,
    legacy_pnginfo: bool = False,
    compat_mode: str = BIG_TOMATO_MODE,
    allow_overwrite: bool = False,
) -> dict:
    _validate_obfuscation_source_file(input_path)
    source_bytes = Path(input_path).read_bytes()
    normalized_mode = normalize_compat_mode(compat_mode)
    text_chunks = extract_source_text_chunks_from_bytes(source_bytes) if preserve_metadata else []
    output_bytes = encode_image_bytes(
        source_bytes,
        password=password,
        text_chunks=text_chunks,
        preserve_metadata=preserve_metadata,
        legacy_pnginfo=legacy_pnginfo,
        compat_mode=normalized_mode,
    )
    def _write_encoded_image(final_output_path: str, _overwrite_requested: bool) -> None:
        Path(final_output_path).write_bytes(output_bytes)

    write_result = save_and_reconcile_checked(
        output_path,
        _write_encoded_image,
        allow_overwrite=allow_overwrite,
        backend_file=__file__,
        validation_error_factory=ValueError,
        conflict_error_factory=ObfuscationOverwriteConflictError,
    )
    indexed_output = db.get_image_by_path(output_path)

    with Image.open(io.BytesIO(output_bytes)) as image:
        width, height = image.size

    return {
        "success": True,
        "input": input_path,
        "output": output_path,
        "dimensions": f"{width}x{height}",
        "metadata_preserved": bool(preserve_metadata),
        "metadata_chunks_carried": len(text_chunks),
        "legacy_pnginfo": bool(legacy_pnginfo),
        "compat_mode": normalized_mode,
        "warnings": write_result.warnings or [],
        "overwrote_existing": bool(write_result.target_existed),
        "overwrote_indexed_path": bool(indexed_output),
        "reconciled_image_id": int(indexed_output["id"]) if indexed_output else None,
    }


def decode_image(
    input_path: str,
    output_path: str,
    password: str,
    preserve_metadata: bool = True,
    legacy_pnginfo: bool = False,
    compat_mode: str = BIG_TOMATO_MODE,
    allow_overwrite: bool = False,
) -> dict:
    _validate_obfuscation_source_file(input_path)
    source_bytes = Path(input_path).read_bytes()
    normalized_mode = normalize_compat_mode(compat_mode)
    text_chunks = extract_source_text_chunks_from_bytes(source_bytes) if preserve_metadata else []
    output_bytes = decode_image_bytes(
        source_bytes,
        password=password,
        text_chunks=text_chunks,
        preserve_metadata=preserve_metadata,
        legacy_pnginfo=legacy_pnginfo,
        compat_mode=normalized_mode,
    )
    def _write_decoded_image(final_output_path: str, _overwrite_requested: bool) -> None:
        Path(final_output_path).write_bytes(output_bytes)

    write_result = save_and_reconcile_checked(
        output_path,
        _write_decoded_image,
        allow_overwrite=allow_overwrite,
        backend_file=__file__,
        validation_error_factory=ValueError,
        conflict_error_factory=ObfuscationOverwriteConflictError,
    )
    indexed_output = db.get_image_by_path(output_path)

    with Image.open(io.BytesIO(output_bytes)) as image:
        width, height = image.size

    return {
        "success": True,
        "input": input_path,
        "output": output_path,
        "dimensions": f"{width}x{height}",
        "metadata_preserved": bool(preserve_metadata),
        "metadata_chunks_carried": len(text_chunks),
        "legacy_pnginfo": bool(legacy_pnginfo),
        "compat_mode": normalized_mode,
        "warnings": write_result.warnings or [],
        "overwrote_existing": bool(write_result.target_existed),
        "overwrote_indexed_path": bool(indexed_output),
        "reconciled_image_id": int(indexed_output["id"]) if indexed_output else None,
    }


def batch_process(
    input_paths: List[str],
    output_folder: str,
    password: str,
    mode: str = "encode",
    preserve_metadata: bool = True,
    suffix: str = "",
    progress_callback=None,
    legacy_pnginfo: bool = False,
    compat_mode: str = BIG_TOMATO_MODE,
    allow_overwrite: bool = False,
) -> dict:
    Path(output_folder).mkdir(parents=True, exist_ok=True)

    process_fn = encode_image if mode == "encode" else decode_image
    normalized_mode = normalize_compat_mode(compat_mode)
    results = []
    errors = []

    for index, input_path in enumerate(input_paths):
        try:
            source_path = Path(input_path)
            filename = f"{source_path.stem}{suffix or ''}.png"
            output_path = str(Path(output_folder) / filename)
            result = process_fn(
                input_path,
                output_path,
                password,
                preserve_metadata=preserve_metadata,
                legacy_pnginfo=legacy_pnginfo,
                compat_mode=normalized_mode,
                allow_overwrite=allow_overwrite,
            )
            results.append(result)
        except Exception as exc:
            errors.append({"input": input_path, "error": str(exc)})

        if progress_callback:
            progress_callback(index + 1, len(input_paths))

    return {
        "mode": mode,
        "total": len(input_paths),
        "success": len(results),
        "failed": len(errors),
        "errors": errors,
        "results": results,
        "output_folder": output_folder,
        "legacy_pnginfo": bool(legacy_pnginfo),
        "compat_mode": normalized_mode,
    }
