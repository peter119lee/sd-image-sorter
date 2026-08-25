"""Decode signed Stealth PNG Info carriers from PNG pixel channels."""

import json
import re
import zlib
from collections.abc import Iterator, Mapping, Sequence
from typing import Literal, Optional

from PIL.Image import Image as PillowImage


PNG_STEALTH_ERROR_PREFIX = "PNG Stealth metadata could not be parsed: "
PNG_STEALTH_SIGNATURE_BYTES = 15
PNG_STEALTH_LENGTH_BYTES = 4
PNG_STEALTH_SIGNATURE_BITS = PNG_STEALTH_SIGNATURE_BYTES * 8

_StealthChannel = Literal["alpha", "rgb"]
_STEALTH_SIGNATURES: Mapping[bytes, tuple[_StealthChannel, bool]] = {
    b"stealth_pnginfo": ("alpha", False),
    b"stealth_pngcomp": ("alpha", True),
    b"stealth_rgbinfo": ("rgb", False),
    b"stealth_rgbcomp": ("rgb", True),
}


class PNGStealthMetadataError(ValueError):
    """A signed Stealth PNG carrier is present but cannot be decoded safely."""


def png_stealth_probe_layout(height: int, color_type: int) -> Optional[tuple[int, int, int]]:
    """Return scanline count, prefix bytes, and bytes per pixel for a signature probe."""
    if height <= 0:
        return None
    if color_type == 6:
        bytes_per_pixel = 4
        required_pixels = PNG_STEALTH_SIGNATURE_BITS
    elif color_type == 2:
        bytes_per_pixel = 3
        required_pixels = PNG_STEALTH_SIGNATURE_BITS // 3
    else:
        return None

    rows_to_decode = min(height, required_pixels)
    columns_to_decode = (required_pixels + height - 1) // height
    return rows_to_decode, columns_to_decode * bytes_per_pixel, bytes_per_pixel


def unfilter_png_scanline_prefix(
    filter_type: int,
    filtered_prefix: bytes,
    previous_prefix: bytes,
    bytes_per_pixel: int,
) -> bytes:
    """Reverse one PNG filter for the prefix needed by the signature probe."""
    if len(filtered_prefix) != len(previous_prefix):
        raise ValueError("PNG probe scanline prefixes have different lengths")
    if filter_type not in {0, 1, 2, 3, 4}:
        raise ValueError(f"Invalid PNG scanline filter type: {filter_type}")

    reconstructed = bytearray(len(filtered_prefix))
    for index, raw_value in enumerate(filtered_prefix):
        left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        above = previous_prefix[index]
        upper_left = previous_prefix[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = above
        elif filter_type == 3:
            predictor = (left + above) // 2
        else:
            predictor = _paeth_predictor(left, above, upper_left)
        reconstructed[index] = (raw_value + predictor) & 0xFF
    return bytes(reconstructed)


def probe_png_stealth_signature(
    scanline_prefixes: Sequence[bytes],
    height: int,
    color_type: int,
) -> Optional[bytes]:
    """Return a recognized signature from reconstructed scanline prefixes."""
    if color_type == 6:
        alpha_signature = _signature_from_channel(
            scanline_prefixes,
            height,
            4,
            (3,),
        )
        if alpha_signature in {b"stealth_pnginfo", b"stealth_pngcomp"}:
            return alpha_signature

    if color_type in {2, 6}:
        bytes_per_pixel = 3 if color_type == 2 else 4
        rgb_signature = _signature_from_channel(
            scanline_prefixes,
            height,
            bytes_per_pixel,
            (0, 1, 2),
        )
        if rgb_signature in {b"stealth_rgbinfo", b"stealth_rgbcomp"}:
            return rgb_signature
    return None


def probe_pillow_stealth_signature(image: PillowImage) -> Optional[bytes]:
    """Read only the signed magic from Pillow pixels (WebP/JPEG/PNG fallback)."""
    if image.mode == "RGBA":
        alpha_signature = _signature_from_pillow_channel(image, "alpha")
        if alpha_signature in {b"stealth_pnginfo", b"stealth_pngcomp"}:
            return alpha_signature
    if image.mode in {"RGB", "RGBA"}:
        rgb_signature = _signature_from_pillow_channel(image, "rgb")
        if rgb_signature in {b"stealth_rgbinfo", b"stealth_rgbcomp"}:
            return rgb_signature
    return None


def _signature_from_pillow_channel(
    image: PillowImage,
    channel: _StealthChannel,
) -> Optional[bytes]:
    try:
        return _read_exact_bytes(
            _iter_channel_bits(image, channel),
            PNG_STEALTH_SIGNATURE_BYTES,
            "signature",
        )
    except PNGStealthMetadataError:
        return None


def decode_png_stealth_metadata(
    image: PillowImage,
    expected_signature: bytes,
    max_encoded_bytes: int,
    max_decompressed_bytes: int,
) -> dict[str, object]:
    """Decode one confirmed carrier into metadata consumed by the existing detector."""
    carrier = _STEALTH_SIGNATURES.get(expected_signature)
    if carrier is None:
        raise PNGStealthMetadataError(f"unsupported signature {expected_signature!r}")
    channel, compressed = carrier
    bits = _iter_channel_bits(image, channel)

    signature = _read_exact_bytes(bits, PNG_STEALTH_SIGNATURE_BYTES, "signature")
    if signature != expected_signature:
        raise PNGStealthMetadataError(
            f"signature changed during pixel decode: expected {expected_signature!r}, got {signature!r}"
        )

    length_bytes = _read_exact_bytes(bits, PNG_STEALTH_LENGTH_BYTES, "payload length")
    payload_bit_length = int.from_bytes(length_bytes, byteorder="big")
    if payload_bit_length <= 0:
        raise PNGStealthMetadataError("declared payload length is zero")
    if payload_bit_length % 8 != 0:
        raise PNGStealthMetadataError(
            f"declared payload length {payload_bit_length} bits is not byte-aligned"
        )

    payload_byte_length = payload_bit_length // 8
    if payload_byte_length > max_encoded_bytes:
        raise PNGStealthMetadataError(
            f"encoded payload is {payload_byte_length} bytes and exceeds the "
            f"{max_encoded_bytes}-byte encoded payload limit"
        )

    channel_count = 1 if channel == "alpha" else 3
    available_payload_bits = (
        image.width * image.height * channel_count
        - (PNG_STEALTH_SIGNATURE_BYTES + PNG_STEALTH_LENGTH_BYTES) * 8
    )
    if payload_bit_length > available_payload_bits:
        raise PNGStealthMetadataError(
            f"carrier declares {payload_bit_length} payload bits but image capacity "
            f"is only {max(0, available_payload_bits)} bits"
        )

    payload = _read_exact_bytes(bits, payload_byte_length, "payload")
    if compressed:
        decoded_bytes = _decompress_gzip_limited(payload, max_decompressed_bytes)
    else:
        if payload_byte_length > max_decompressed_bytes:
            raise PNGStealthMetadataError(
                f"payload is {payload_byte_length} bytes and exceeds the "
                f"{max_decompressed_bytes}-byte decompressed payload limit"
            )
        decoded_bytes = payload

    try:
        decoded_text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PNGStealthMetadataError(
            f"payload is not valid UTF-8 at byte {exc.start}"
        ) from exc
    if not decoded_text.strip():
        raise PNGStealthMetadataError("payload is empty after decoding")
    return _metadata_from_stealth_text(decoded_text)


def _signature_from_channel(
    scanline_prefixes: Sequence[bytes],
    height: int,
    bytes_per_pixel: int,
    channel_indexes: tuple[int, ...],
) -> Optional[bytes]:
    bits: list[int] = []
    column_count = min(
        (len(row) // bytes_per_pixel for row in scanline_prefixes),
        default=0,
    )
    for column in range(column_count):
        for row in scanline_prefixes[:height]:
            pixel_offset = column * bytes_per_pixel
            for channel_index in channel_indexes:
                bits.append(row[pixel_offset + channel_index] & 1)
                if len(bits) == PNG_STEALTH_SIGNATURE_BITS:
                    return _bits_to_bytes(bits)
    return None


def _iter_channel_bits(image: PillowImage, channel: _StealthChannel) -> Iterator[int]:
    if channel == "alpha" and image.mode != "RGBA":
        raise PNGStealthMetadataError(
            f"alpha carrier requires RGBA pixels, got image mode {image.mode!r}"
        )
    if channel == "rgb" and image.mode not in {"RGB", "RGBA"}:
        raise PNGStealthMetadataError(
            f"RGB carrier requires RGB or RGBA pixels, got image mode {image.mode!r}"
        )

    pixels = image.load()
    for x in range(image.width):
        for y in range(image.height):
            pixel = pixels[x, y]
            if channel == "alpha":
                yield int(pixel[3]) & 1
            else:
                yield int(pixel[0]) & 1
                yield int(pixel[1]) & 1
                yield int(pixel[2]) & 1


def _read_exact_bytes(bits: Iterator[int], byte_count: int, label: str) -> bytes:
    output = bytearray(byte_count)
    try:
        for byte_index in range(byte_count):
            value = 0
            for _ in range(8):
                value = (value << 1) | next(bits)
            output[byte_index] = value
    except StopIteration as exc:
        raise PNGStealthMetadataError(f"image ended while reading carrier {label}") from exc
    return bytes(output)


def _decompress_gzip_limited(payload: bytes, max_output_bytes: int) -> bytes:
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = decompressor.decompress(payload, max_output_bytes + 1)
        if len(output) > max_output_bytes or decompressor.unconsumed_tail:
            raise PNGStealthMetadataError(
                f"gzip payload exceeds the {max_output_bytes}-byte decompressed payload limit"
            )
        remaining = max_output_bytes + 1 - len(output)
        if remaining > 0:
            output += decompressor.flush(remaining)
    except zlib.error as exc:
        raise PNGStealthMetadataError(f"invalid gzip payload: {exc}") from exc

    if len(output) > max_output_bytes:
        raise PNGStealthMetadataError(
            f"gzip payload exceeds the {max_output_bytes}-byte decompressed payload limit"
        )
    if not decompressor.eof:
        raise PNGStealthMetadataError("invalid gzip payload: compressed stream is truncated")
    if decompressor.unused_data:
        raise PNGStealthMetadataError("invalid gzip payload: trailing compressed data")
    return output


def _metadata_from_stealth_text(decoded_text: str) -> dict[str, object]:
    try:
        decoded_json = json.loads(decoded_text)
    except json.JSONDecodeError as exc:
        stripped_text = decoded_text.lstrip()
        looks_like_webui = re.search(
            r"(?m)^Steps:\s*\d+\s*,[^\r\n]*\bSampler:\s*[^\r\n]+(?:\r?\n)?\Z",
            decoded_text,
        ) is not None
        if stripped_text.startswith(("{", "[")) and not looks_like_webui:
            raise PNGStealthMetadataError(
                f"invalid JSON payload at character {exc.pos}: {exc.msg}"
            ) from exc
        return {"parameters": decoded_text}

    if isinstance(decoded_json, dict):
        if not all(isinstance(key, str) for key in decoded_json):
            raise PNGStealthMetadataError("JSON object contains a non-string metadata key")
        return dict(decoded_json)
    if isinstance(decoded_json, str) and decoded_json.strip():
        return {"parameters": decoded_json}
    raise PNGStealthMetadataError(
        f"JSON payload must be an object or string, got {type(decoded_json).__name__}"
    )


def _bits_to_bytes(bits: Sequence[int]) -> bytes:
    if len(bits) % 8 != 0:
        raise ValueError("Bit sequence is not byte-aligned")
    return bytes(
        sum(bits[offset + bit_index] << (7 - bit_index) for bit_index in range(8))
        for offset in range(0, len(bits), 8)
    )


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left
