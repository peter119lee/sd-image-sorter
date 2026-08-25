# =============================================================================
# metadata_parser._runtime - metadata_parser decomposition stage 3 (2026-07-13).
# Extracted VERBATIM from metadata_parser/__init__.py @ 42de5d3 (1,390 lines);
# source line ranges (stage-1+2 __init__): 207-594, 657-799, 841-892, 1246-1362,
# 1381-1390. Mixin: image loaders + PNG/WebP fast paths and chunk decoders +
# sidecar loading/caching + C2PA byte scan + EXIF-from-bytes + JPEG/WebP XMP,
# plus module function verify_image_readable.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# THE SEAM MODULE: every bare-name runtime READER of the four monkeypatched
# globals (Image / open / _MAX_DECOMPRESSED_BYTES /
# _MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES) lives here (parse() in __init__.py
# also reads Image); the _MetadataParserPackage proxy in __init__.py forwards
# package-level get/set/del of those names to THIS namespace so historical
# monkeypatches on `metadata_parser` keep landing where the readers resolve
# them. `open` is deliberately NOT bound here: bare reads hit the builtin
# until a test sets metadata_parser.open (raising=False), and fall back to
# the builtin after teardown deletes it. See tests/test_metadata_parser_pins.py.
# Regrowth split (2026-07-18): the pure TIFF/EXIF structural validator
# (_validate_webp_exif_payload + its _TIFF_* tables; AST-verified to read no
# patched seams) moved VERBATIM to _exif_validation.py and is re-imported
# below so package-level get/set/del of that name still resolves here.
import json
import logging
import struct
import os
import zlib
from typing import Optional, Dict, Any, Tuple, Set
from PIL import Image
from pathlib import Path
from ._exif_validation import _validate_webp_exif_payload
from .constants import (
    PNG_SIGNATURE,
    _MAX_PNG_CHUNK_BYTES,
    _MAX_DECOMPRESSED_BYTES,
    _MAX_PNG_STEALTH_PROBE_BYTES,
    JPEG_SIGNATURE,
    _MAX_JPEG_SEGMENT_BYTES,
    _MAX_XMP_CHUNK_BYTES,
    _MAX_SIDECAR_BYTES,
    _MAX_SIDECAR_DIRECTORY_CACHE_ENTRIES,
    _MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES,
    SIDECAR_CAPTION_METADATA_KEY,
    SIDECAR_EXTENSIONS,
    _sidecar_directory_cache,
    WEBP_SIGNATURE,
    WEBP_FOURCC,
    _MAX_WEBP_CHUNK_BYTES,
)
from .png_stealth import (
    PNG_STEALTH_ERROR_PREFIX,
    PNGStealthMetadataError,
    decode_png_stealth_metadata,
    png_stealth_probe_layout,
    probe_pillow_stealth_signature,
    probe_png_stealth_signature,
    unfilter_png_scanline_prefix,
)

logger = logging.getLogger(__name__)


class ParserRuntimeMixin:
    """Image loaders, sidecar handling, C2PA scan, EXIF-bytes/XMP extraction."""

    def _load_image_metadata(self, image_path: str) -> Dict[str, Any]:
        """Load dimensions and raw metadata with format-specific fast paths.

        The PNG fast-path is byte-strict: it checks the PNG magic
        signature (89 50 4E 47 0D 0A 1A 0A) and rejects anything else.
        That is correct for genuinely corrupt PNGs, but in practice a
        sizable chunk of SD libraries contain JPEG (or WEBP / GIF)
        files that were renamed to ``.png`` by content-management tools
        — Civitai, Discord, browsers, etc. Browsers and Windows
        Explorer render them fine because they sniff format from
        content; our previous behaviour rejected them as
        ``Invalid PNG signature`` and reported them as unreadable
        files in the scan summary, even though Pillow can parse them
        without issue.

        Strategy: if the PNG fast-path raises ``Invalid PNG
        signature``, fall through to the Pillow path. Pillow detects
        format from content magic bytes regardless of file extension,
        so a .png-extension JPEG is parsed as JPEG. Other PNG-shape
        errors (truncated chunks, bad IEND, …) still bubble up as
        legitimate parse failures.
        """
        ext = os.path.splitext(image_path)[1].lower()

        if ext == ".png":
            try:
                return self._load_png_metadata_fast(image_path)
            except ValueError as exc:
                if "Invalid PNG signature" not in str(exc):
                    raise
                # File extension said PNG, content sniff says otherwise.
                # Pillow handles JPEG/WEBP/GIF content with .png extension.

        if ext == ".webp":
            try:
                return self._load_webp_metadata_fast(image_path)
            except ValueError as exc:
                logger.debug("WEBP fast path failed for %s: %s, falling back to Pillow", image_path, exc)
                # Fall through to Pillow on any fast-path failure

        return self._load_image_metadata_via_pillow(image_path)

    def _load_image_metadata_via_pillow(self, image_path: str) -> Dict[str, Any]:
        """Load metadata through Pillow for formats without a custom fast path."""
        with Image.open(image_path) as img:
            metadata = {}
            if hasattr(img, 'info'):
                metadata = dict(img.info)

            metadata.update(self._extract_gif_comment_metadata(img))

            metadata.update(self._extract_exif(img))
            metadata.update(self._extract_exif_ifd(img))

            if img.format == 'WEBP':
                metadata.update(self._extract_webp_xmp(image_path))

            if img.format in ('JPEG', 'JPG'):
                metadata.update(self._extract_jpeg_xmp(image_path))

            if img.format in ('TIFF', 'MPO'):
                metadata.update(self._extract_tiff_xmp(img))

            if img.format in ('JPEG', 'JPG', 'WEBP'):
                metadata.update(self._extract_jpeg_sd_metadata(img))

            metadata_error = None
            # Pixel LSB is only used by NovelAI/A1111 stealth on lossless
            # RGBA/WebP (and PNG fallback). Do not decode every camera JPEG.
            if (
                img.format in {"WEBP", "PNG"} or img.mode == "RGBA"
            ) and not self._embedded_text_carrier_present(metadata):
                stealth_metadata, metadata_error = self._decode_pixel_stealth_from_image(img)
                metadata = {**stealth_metadata, **metadata}

            return {
                "width": img.width,
                "height": img.height,
                "metadata": metadata,
                "metadata_error": metadata_error,
            }

    def _load_png_metadata_fast(self, image_path: str) -> Dict[str, Any]:
        """Read PNG dimensions + text metadata without a full Pillow open."""
        metadata: Dict[str, Any] = {}
        metadata_error: Optional[str] = None
        width = 0
        height = 0
        bit_depth = 0
        color_type = -1
        interlace_method = -1
        seen_iend = False
        idat_ranges: list[tuple[int, int]] = []

        file_size = os.path.getsize(image_path)

        with open(image_path, "rb") as png_file:
            offset = 0

            signature = png_file.read(len(PNG_SIGNATURE))
            offset += len(signature)
            if signature != PNG_SIGNATURE:
                raise ValueError("Invalid PNG signature")

            while True:
                chunk_length_raw = png_file.read(4)
                offset += len(chunk_length_raw)
                if not chunk_length_raw:
                    break
                if len(chunk_length_raw) != 4:
                    raise ValueError("Truncated PNG chunk length")

                chunk_length = struct.unpack(">I", chunk_length_raw)[0]
                chunk_type = png_file.read(4)
                offset += len(chunk_type)
                if len(chunk_type) != 4:
                    raise ValueError("Truncated PNG chunk type")

                chunk_end = offset + chunk_length + 4
                if chunk_end > file_size:
                    raise ValueError("Truncated PNG chunk data")

                if chunk_type == b"IDAT":
                    idat_ranges.append((offset, chunk_length))

                if chunk_type == b"IEND":
                    if chunk_length != 0:
                        raise ValueError("Invalid PNG IEND chunk")
                    png_file.seek(4, os.SEEK_CUR)
                    offset += 4
                    seen_iend = True
                    break

                should_read_chunk = chunk_type in {b"IHDR", b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
                if not should_read_chunk:
                    png_file.seek(chunk_length + 4, os.SEEK_CUR)
                    offset += chunk_length + 4
                    continue

                if chunk_length > _MAX_PNG_CHUNK_BYTES:
                    break  # abort: metadata chunk too large, likely malformed

                chunk_data = png_file.read(chunk_length)
                offset += len(chunk_data)
                if len(chunk_data) != chunk_length:
                    raise ValueError("Truncated PNG chunk data")

                chunk_crc = png_file.read(4)
                offset += len(chunk_crc)
                if len(chunk_crc) != 4:
                    raise ValueError("Truncated PNG chunk CRC")

                if chunk_type == b"IHDR":
                    if chunk_length != 13:
                        raise ValueError("Invalid PNG IHDR chunk")
                    width, height = struct.unpack(">II", chunk_data[:8])
                    bit_depth = chunk_data[8]
                    color_type = chunk_data[9]
                    interlace_method = chunk_data[12]
                elif chunk_type == b"tEXt":
                    text_item = self._decode_png_text_chunk(chunk_data)
                    if text_item:
                        metadata[text_item[0]] = text_item[1]
                elif chunk_type == b"zTXt":
                    text_item = self._decode_png_ztxt_chunk(chunk_data)
                    if text_item:
                        metadata[text_item[0]] = text_item[1]
                elif chunk_type == b"iTXt":
                    text_item = self._decode_png_itxt_chunk(chunk_data)
                    if text_item:
                        metadata[text_item[0]] = text_item[1]
                elif chunk_type == b"eXIf":
                    metadata.update(self._extract_exif_from_bytes(chunk_data))
                    metadata.update(self._extract_exif_ifd_from_bytes(chunk_data))
                    metadata.update(self._extract_sd_metadata_from_exif_bytes(chunk_data))

        if width <= 0 or height <= 0:
            raise ValueError("PNG dimensions missing")
        if not seen_iend:
            raise ValueError("Truncated PNG missing IEND chunk")

        if bit_depth == 8 and interlace_method == 0 and idat_ranges:
            stealth_metadata, metadata_error = self._load_png_stealth_metadata(
                image_path,
                width,
                height,
                color_type,
                idat_ranges,
            )
            metadata = {**stealth_metadata, **metadata}

        return {
            "width": width,
            "height": height,
            "metadata": metadata,
            "metadata_error": metadata_error,
        }

    def _load_png_stealth_metadata(
        self,
        image_path: str,
        width: int,
        height: int,
        color_type: int,
        idat_ranges: list[tuple[int, int]],
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Probe cheaply, then decode pixels only for a confirmed signed carrier."""
        signature = self._probe_png_stealth_signature(
            image_path,
            width,
            height,
            color_type,
            idat_ranges,
        )
        if signature is None:
            return {}, None

        try:
            with Image.open(image_path) as image:
                image.load()
                decoded = decode_png_stealth_metadata(
                    image,
                    signature,
                    _MAX_PNG_CHUNK_BYTES,
                    _MAX_DECOMPRESSED_BYTES,
                )
            return decoded, None
        except PNGStealthMetadataError as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}{exc}"
        except (OSError, ValueError) as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}pixel decode failed: {exc}"

    def _embedded_text_carrier_present(self, metadata: Dict[str, Any]) -> bool:
        """Skip pixel stealth when a normal SD text chunk/EXIF field is already present."""
        for key in ("parameters", "prompt", "workflow"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return True
        for key in ("Comment", "comment"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip().startswith("{"):
                return True
        software = str(metadata.get("Software") or "").lower()
        description = metadata.get("Description") or metadata.get("ImageDescription")
        if "novelai" in software and isinstance(description, str) and description.strip():
            return True
        for key in ("UserComment", "XPComment"):
            value = metadata.get(key)
            if isinstance(value, bytes) and b"{" in value[:48]:
                return True
            if isinstance(value, str) and "{" in value[:48]:
                return True
        return False

    def _decode_pixel_stealth_from_image(
        self,
        image: Image.Image,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Probe signed stealth magics on an already-open Pillow image, then decode."""
        try:
            image.load()
        except (OSError, ValueError) as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}pixel decode failed: {exc}"
        if image.mode not in {"RGB", "RGBA"}:
            return {}, None
        signature = probe_pillow_stealth_signature(image)
        if signature is None:
            return {}, None
        try:
            decoded = decode_png_stealth_metadata(
                image,
                signature,
                _MAX_PNG_CHUNK_BYTES,
                _MAX_DECOMPRESSED_BYTES,
            )
            return decoded, None
        except PNGStealthMetadataError as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}{exc}"
        except (OSError, ValueError) as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}pixel decode failed: {exc}"

    def _load_pixel_stealth_metadata(
        self,
        image_path: str,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Open pixels only when the container path has no prompt-bearing text."""
        try:
            with Image.open(image_path) as image:
                return self._decode_pixel_stealth_from_image(image)
        except (OSError, ValueError) as exc:
            return {}, f"{PNG_STEALTH_ERROR_PREFIX}pixel decode failed: {exc}"

    def _probe_png_stealth_signature(
        self,
        image_path: str,
        width: int,
        height: int,
        color_type: int,
        idat_ranges: list[tuple[int, int]],
    ) -> Optional[bytes]:
        """Inspect only the scanline prefix needed to confirm a carrier signature."""
        layout = png_stealth_probe_layout(height, color_type)
        if layout is None:
            return None
        rows_to_decode, prefix_bytes, bytes_per_pixel = layout
        row_bytes = width * bytes_per_pixel
        required_output_bytes = rows_to_decode * (row_bytes + 1)
        if required_output_bytes > _MAX_PNG_STEALTH_PROBE_BYTES:
            return None

        decompressed = self._read_png_idat_prefix(
            image_path,
            idat_ranges,
            required_output_bytes,
        )
        if decompressed is None:
            return None

        previous_prefix = bytes(prefix_bytes)
        scanline_prefixes: list[bytes] = []
        for row_index in range(rows_to_decode):
            scanline_offset = row_index * (row_bytes + 1)
            filter_type = decompressed[scanline_offset]
            filtered_prefix = decompressed[
                scanline_offset + 1:scanline_offset + 1 + prefix_bytes
            ]
            try:
                reconstructed = unfilter_png_scanline_prefix(
                    filter_type,
                    filtered_prefix,
                    previous_prefix,
                    bytes_per_pixel,
                )
            except ValueError:
                return None
            scanline_prefixes.append(reconstructed)
            previous_prefix = reconstructed
        return probe_png_stealth_signature(scanline_prefixes, height, color_type)

    def _read_png_idat_prefix(
        self,
        image_path: str,
        idat_ranges: list[tuple[int, int]],
        required_output_bytes: int,
    ) -> Optional[bytes]:
        """Stream IDAT data until the requested decompressed scanline prefix is complete."""
        decompressor = zlib.decompressobj()
        output = bytearray()
        try:
            with open(image_path, "rb") as png_file:
                for data_offset, data_length in idat_ranges:
                    png_file.seek(data_offset)
                    remaining_data = data_length
                    while remaining_data > 0 and len(output) < required_output_bytes:
                        compressed = png_file.read(min(64 * 1024, remaining_data))
                        if not compressed:
                            return None
                        remaining_data -= len(compressed)
                        output.extend(
                            decompressor.decompress(
                                compressed,
                                required_output_bytes - len(output),
                            )
                        )
                    if len(output) >= required_output_bytes:
                        return bytes(output)
        except zlib.error:
            return None
        return None

    def _decode_png_text_chunk(self, chunk_data: bytes) -> Optional[Tuple[str, str]]:
        """Decode a PNG tEXt chunk into a key/value pair."""
        if b"\x00" not in chunk_data:
            return None
        keyword, text = chunk_data.split(b"\x00", 1)
        return (
            keyword.decode("latin-1", errors="replace"),
            text.decode("utf-8", errors="replace"),
        )

    def _safe_zlib_decompress_limited(self, compressed_data: bytes, max_output_bytes: int) -> Optional[bytes]:
        """
        Safely decompress zlib data with a hard output cap.

        Returns None on malformed streams or when decompressed bytes exceed
        the configured limit.
        """
        try:
            decompressor = zlib.decompressobj()
            max_probe = max_output_bytes + 1
            decompressed = decompressor.decompress(compressed_data, max_probe)

            if len(decompressed) > max_output_bytes:
                return None

            if decompressor.unconsumed_tail:
                return None

            remaining_budget = max_probe - len(decompressed)
            if remaining_budget > 0:
                decompressed += decompressor.flush(remaining_budget)

            if len(decompressed) > max_output_bytes:
                return None

            if not decompressor.eof:
                return None

            return decompressed
        except zlib.error:
            return None

    def _decode_png_ztxt_chunk(self, chunk_data: bytes) -> Optional[Tuple[str, str]]:
        """Decode a PNG zTXt chunk into a key/value pair."""
        if b"\x00" not in chunk_data:
            return None
        keyword, remainder = chunk_data.split(b"\x00", 1)
        if len(remainder) < 2:
            return None
        compression_method = remainder[0]
        if compression_method != 0:
            return None
        text = self._safe_zlib_decompress_limited(remainder[1:], _MAX_DECOMPRESSED_BYTES)
        if text is None:
            return None
        return (
            keyword.decode("latin-1", errors="replace"),
            text.decode("utf-8", errors="replace"),
        )

    def _decode_png_itxt_chunk(self, chunk_data: bytes) -> Optional[Tuple[str, str]]:
        """Decode a PNG iTXt chunk into a key/value pair."""
        if b"\x00" not in chunk_data:
            return None
        keyword, remainder = chunk_data.split(b"\x00", 1)
        if len(remainder) < 2:
            return None

        compression_flag = remainder[0]
        compression_method = remainder[1]
        remainder = remainder[2:]

        if b"\x00" not in remainder:
            return None
        _language_tag, remainder = remainder.split(b"\x00", 1)
        if b"\x00" not in remainder:
            return None
        _translated_keyword, text_bytes = remainder.split(b"\x00", 1)

        if compression_flag == 1:
            if compression_method != 0:
                return None
            text_bytes = self._safe_zlib_decompress_limited(text_bytes, _MAX_DECOMPRESSED_BYTES)
            if text_bytes is None:
                return None

        return (
            keyword.decode("latin-1", errors="replace"),
            text_bytes.decode("utf-8", errors="replace"),
        )

    def _load_webp_metadata_fast(self, image_path: str) -> Dict[str, Any]:
        """Read WEBP dimensions + EXIF/XMP metadata without a full Pillow open.

        WEBP uses RIFF container format:
        - RIFF header (12 bytes): "RIFF" + file_size + "WEBP"
        - Chunks: fourcc (4 bytes) + chunk_size (4 bytes, little-endian) + data + optional padding

        This fast path parses EXIF and XMP chunks directly, avoiding the overhead
        of full image decoding. Falls back to Pillow on any parsing error.
        """
        metadata: Dict[str, Any] = {}
        metadata_error: Optional[str] = None
        width = 0
        height = 0

        file_size = os.path.getsize(image_path)
        if file_size < 12:
            raise ValueError("File too small to be valid WEBP")

        with open(image_path, "rb") as webp_file:
            # Read RIFF header
            riff_header = webp_file.read(4)
            if riff_header != WEBP_SIGNATURE:
                raise ValueError("Invalid WEBP signature")

            # Skip file size field (4 bytes)
            webp_file.read(4)
            webp_fourcc = webp_file.read(4)
            if webp_fourcc != WEBP_FOURCC:
                raise ValueError("Invalid WEBP FOURCC")

            offset = 12

            # Parse chunks
            while offset + 8 <= file_size:
                fourcc = webp_file.read(4)
                if len(fourcc) != 4:
                    break

                chunk_size_bytes = webp_file.read(4)
                if len(chunk_size_bytes) != 4:
                    break

                chunk_size = struct.unpack("<I", chunk_size_bytes)[0]
                offset += 8

                # Check bounds
                chunk_end = offset + chunk_size
                if chunk_end > file_size:
                    raise ValueError(f"WEBP chunk {fourcc!r} exceeds file size")

                # Limit individual chunk size
                if chunk_size > _MAX_WEBP_CHUNK_BYTES:
                    logger.debug("Skipping oversized WEBP chunk %s (%d bytes)", fourcc, chunk_size)
                    webp_file.seek(chunk_size + (chunk_size % 2), os.SEEK_CUR)
                    offset = chunk_end + (chunk_size % 2)
                    continue

                should_read_chunk = fourcc in {b"EXIF", b"XMP ", b"VP8 ", b"VP8L", b"VP8X"}
                if not should_read_chunk:
                    # Skip chunk + padding
                    webp_file.seek(chunk_size + (chunk_size % 2), os.SEEK_CUR)
                    offset = chunk_end + (chunk_size % 2)
                    continue

                chunk_data = webp_file.read(chunk_size)
                if len(chunk_data) != chunk_size:
                    raise ValueError(f"Truncated WEBP chunk {fourcc!r}")

                # Parse chunk based on type
                if fourcc == b"EXIF":
                    # EXIF chunk contains TIFF-format EXIF data
                    exif_metadata, exif_error = self._parse_webp_exif_chunk(chunk_data)
                    metadata.update(exif_metadata)
                    if exif_error is not None and metadata_error is None:
                        metadata_error = exif_error

                elif fourcc == b"XMP ":
                    # XMP chunk contains UTF-8 XMP metadata
                    try:
                        xmp_text = chunk_data.decode("utf-8", errors="replace")
                        metadata.update(self._extract_xmp_sd_metadata(xmp_text))
                    except Exception as e:
                        logger.debug("Failed to decode WEBP XMP chunk: %s", e)

                elif fourcc == b"VP8 " and width == 0:
                    # VP8 bitstream: extract dimensions from frame header
                    # Skip frame tag (3 bytes) and start code (3 bytes)
                    if chunk_size >= 10:
                        try:
                            # Dimensions are in bytes 6-9 (14 bits each)
                            dim_bytes = struct.unpack("<HH", chunk_data[6:10])
                            width = (dim_bytes[0] & 0x3FFF)
                            height = (dim_bytes[1] & 0x3FFF)
                        except Exception:
                            pass

                elif fourcc == b"VP8L" and width == 0:
                    # VP8L (lossless) bitstream: dimensions in first 5 bytes after signature
                    if chunk_size >= 5:
                        try:
                            # Signature byte (0x2f) + 4 bytes for dimensions
                            if chunk_data[0] == 0x2f:
                                bits = struct.unpack("<I", chunk_data[1:5])[0]
                                width = (bits & 0x3FFF) + 1
                                height = ((bits >> 14) & 0x3FFF) + 1
                        except Exception:
                            pass

                elif fourcc == b"VP8X" and width == 0:
                    # VP8X (extended format): dimensions in bytes 4-9
                    if chunk_size >= 10:
                        try:
                            # Canvas width (24 bits) + height (24 bits)
                            width = struct.unpack("<I", chunk_data[4:7] + b"\x00")[0] + 1
                            height = struct.unpack("<I", chunk_data[7:10] + b"\x00")[0] + 1
                        except Exception:
                            pass

                # Account for padding (chunks are padded to even byte boundaries)
                padding = chunk_size % 2
                if padding:
                    webp_file.seek(1, os.SEEK_CUR)
                offset = chunk_end + padding

        if width <= 0 or height <= 0:
            raise ValueError("WEBP dimensions not found or invalid")

        if not self._embedded_text_carrier_present(metadata):
            stealth_metadata, stealth_error = self._load_pixel_stealth_metadata(image_path)
            metadata = {**stealth_metadata, **metadata}
            if stealth_error is not None and metadata_error is None:
                metadata_error = stealth_error

        return {
            "width": width,
            "height": height,
            "metadata": metadata,
            "metadata_error": metadata_error,
        }

    def _parse_webp_exif_chunk(
        self,
        exif_bytes: bytes,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Parse a WebP EXIF chunk without making metadata failure fatal to pixels."""
        validation_error = _validate_webp_exif_payload(exif_bytes)
        if validation_error is not None:
            return {}, f"WebP EXIF chunk could not be parsed: {validation_error}"

        try:
            exif = Image.Exif()
            exif.load(exif_bytes)
            metadata = self._extract_exif_mapping(exif)
            metadata.update(self._extract_exif_ifd_mapping(exif))
            metadata.update(self._extract_sd_metadata_from_exif(exif))
        except Exception as exc:
            cause = str(exc).strip() or type(exc).__name__
            return {}, f"WebP EXIF chunk could not be parsed: {cause}"

        return metadata, None

    def _load_sidecar_metadata(self, image_path: str) -> Dict[str, Any]:
        """Load small same-name sidecar metadata only after embedded parsing fails."""
        metadata: Dict[str, Any] = {}
        base_path = Path(image_path)
        candidates = []
        for extension in SIDECAR_EXTENSIONS:
            candidates.append(Path(f"{image_path}{extension}"))
            candidates.append(base_path.with_suffix(extension))

        seen: Set[str] = set()
        for candidate in candidates:
            candidate_key = os.path.abspath(os.fspath(candidate))
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            if not self._sidecar_candidate_exists(candidate):
                continue
            loaded = self._load_one_sidecar(candidate)
            if loaded:
                metadata.update(loaded)

        return metadata

    def _sidecar_candidate_exists(self, sidecar_path: Path) -> bool:
        """Check sidecar existence with a lightweight per-directory listing cache."""
        try:
            directory = sidecar_path.parent
            stat_result = directory.stat()
            cache_key = os.path.abspath(os.fspath(directory))
            fingerprint = (int(stat_result.st_mtime_ns), int(stat_result.st_size))
            cached = _sidecar_directory_cache.get(cache_key)
            if cached is None or cached[0] != fingerprint:
                sidecar_names: Set[str] = set()
                candidate_name = sidecar_path.name
                candidate_found = False
                too_many_sidecars = False
                for entry in os.scandir(directory):
                    if not entry.is_file(follow_symlinks=False) or Path(entry.name).suffix.lower() not in SIDECAR_EXTENSIONS:
                        continue
                    if entry.name == candidate_name:
                        candidate_found = True
                    if not too_many_sidecars:
                        sidecar_names.add(entry.name)
                        if len(sidecar_names) >= _MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES:
                            too_many_sidecars = True
                if len(_sidecar_directory_cache) >= _MAX_SIDECAR_DIRECTORY_CACHE_ENTRIES:
                    _sidecar_directory_cache.clear()
                if too_many_sidecars:
                    _sidecar_directory_cache[cache_key] = (fingerprint, None)
                    return candidate_found
                _sidecar_directory_cache[cache_key] = (fingerprint, sidecar_names)
            else:
                sidecar_names = cached[1]
            if sidecar_names is None:
                return sidecar_path.is_file() and not sidecar_path.is_symlink()
            return sidecar_path.name in sidecar_names
        except OSError:
            return False

    def _load_one_sidecar(self, sidecar_path: Path) -> Dict[str, Any]:
        """Load a supported sidecar if it is small and local to the image."""
        try:
            if not sidecar_path.is_file() or sidecar_path.is_symlink():
                return {}
            if sidecar_path.stat().st_size > _MAX_SIDECAR_BYTES:
                return {}
            text = sidecar_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return {}

        text = text.strip()
        if not text:
            return {}

        suffix = sidecar_path.suffix.lower()
        if suffix == ".xmp":
            metadata = self._extract_xmp_sd_metadata(text)
            metadata["sidecar_path"] = os.fspath(sidecar_path)
            return metadata
        if suffix == ".json":
            return self._parse_json_sidecar(text, sidecar_path)
        if suffix == ".txt":
            return self._parse_text_sidecar(text, sidecar_path)
        return {}

    def _parse_json_sidecar(self, text: str, sidecar_path: Path) -> Dict[str, Any]:
        """Parse a JSON sidecar into known SD metadata fields."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return self._parse_text_sidecar(text, sidecar_path)

        metadata: Dict[str, Any] = {"sidecar_path": os.fspath(sidecar_path)}
        if isinstance(payload, dict):
            if self._looks_like_comfyui_prompt_dict(payload):
                metadata["prompt"] = json.dumps(payload, ensure_ascii=False)
                return metadata
            if "workflow" in payload:
                metadata["workflow"] = json.dumps(payload.get("workflow"), ensure_ascii=False)
            if "prompt" in payload and isinstance(payload.get("prompt"), dict) and self._looks_like_comfyui_prompt_dict(payload["prompt"]):
                metadata["prompt"] = json.dumps(payload["prompt"], ensure_ascii=False)
                return metadata

            key_aliases = {
                "prompt": "prompt",
                "positive_prompt": "prompt",
                # "caption"/"text" are what dataset tooling writes; the author
                # is describing the image, not naming the prompt it came from.
                "caption": SIDECAR_CAPTION_METADATA_KEY,
                "text": SIDECAR_CAPTION_METADATA_KEY,
                "negative_prompt": "negative_prompt",
                "negative prompt": "negative_prompt",
                "uc": "negative_prompt",
                "model": "model",
                "checkpoint": "checkpoint",
                "ckpt": "checkpoint",
                "loras": "loras",
                "lora": "loras",
                "steps": "steps",
                "sampler": "sampler",
                "seed": "seed",
                "cfg_scale": "cfg_scale",
                "cfg scale": "cfg_scale",
                "size": "size",
            }
            for key, value in payload.items():
                canonical_key = key_aliases.get(str(key).strip().lower(), str(key).strip())
                if canonical_key:
                    metadata[canonical_key] = value
            return metadata

        if isinstance(payload, list):
            metadata[SIDECAR_CAPTION_METADATA_KEY] = self._flatten_text_value(payload)
            return {key: value for key, value in metadata.items() if value}

        return self._parse_text_sidecar(str(payload), sidecar_path)

    def _parse_text_sidecar(self, text: str, sidecar_path: Path) -> Dict[str, Any]:
        """Parse a plain text sidecar as WebUI params or a caption.

        A sidecar holding real generation parameters ("Steps:" / "Sampler:")
        is an SD prompt and keeps going down the WebUI route. Anything else is
        free text a human or a tagger wrote next to the image — it is routed to
        the caption key so it can never be mistaken for the prompt that
        rendered the image.
        """
        metadata: Dict[str, Any] = {"sidecar_path": os.fspath(sidecar_path)}
        if "Steps:" in text and "Sampler:" in text:
            metadata["parameters"] = text
        else:
            metadata[SIDECAR_CAPTION_METADATA_KEY] = text
        return metadata

    # Soft cap for the C2PA byte-signature fallback. The C2PA / JUMBF
    # manifest is typically front-loaded near the file header; we only
    # scan the first chunk to keep scan-time IO bounded. 512 KiB is
    # enough for both PNG `caBX` and JPEG `APP11` segments.
    _C2PA_SCAN_BYTES = 512 * 1024
    # Skip files smaller than this — they cannot realistically carry a
    # C2PA manifest with cryptographic signatures.
    _C2PA_MIN_FILE_BYTES = 32 * 1024

    # Distinct byte signatures we look for inside the front of the file.
    # Lowercased haystack is matched. Each tuple is (generator_id, marker
    # bytes that must appear). When matched, we still require the
    # marker to live near a "c2pa", "jumbf", or "claim_generator" anchor
    # to reduce false positives (e.g. an unrelated PNG that happens to
    # mention "openai" inside a tag).
    _C2PA_SIGNATURES = (
        ("gpt-image", (b"gpt-image", b"chatgpt", b"openai")),
        ("gemini",    (b"gemini", b"imagen", b"google ai", b"nano-banana", b"deepmind")),
    )
    _C2PA_ANCHORS = (b"c2pa", b"jumbf", b"claim_generator", b"contentcredentials", b"content credentials")

    def _scan_c2pa_byte_signatures(self, image_path: str, file_size: int) -> Optional[str]:
        """Best-effort C2PA / "Content Credentials" byte scan.

        Many AI providers (OpenAI ChatGPT image, Google Gemini / Imagen)
        cryptographically sign their output with C2PA manifests stored
        in PNG `caBX` chunks or JPEG `APP11` segments. We don't validate
        the signature — that would need the c2pa-python dependency — we
        just look for the provider's name near a manifest anchor (e.g.
        `c2pa`, `jumbf`, `claim_generator`). Bounded to 512 KiB of IO so
        this stays cheap during library scans.
        """
        if file_size < self._C2PA_MIN_FILE_BYTES:
            return None
        try:
            with open(image_path, "rb") as fh:
                blob = fh.read(self._C2PA_SCAN_BYTES)
        except (OSError, IOError) as exc:
            logger.debug("C2PA byte scan failed for %s: %s", image_path, exc)
            return None
        if not blob:
            return None
        haystack = blob.lower()
        # Anchor first — bail early if no manifest-shaped marker is in
        # the file. This keeps detection narrow on regular SD images
        # that happen to contain a tag like "openai" in their prompt.
        if not any(anchor in haystack for anchor in self._C2PA_ANCHORS):
            return None
        for generator_id, markers in self._C2PA_SIGNATURES:
            if any(marker in haystack for marker in markers):
                return generator_id
        return None

    def _extract_exif_from_bytes(self, exif_bytes: bytes) -> dict:
        """Extract top-level EXIF tags from raw EXIF bytes."""
        try:
            exif = Image.Exif()
            exif.load(exif_bytes)
            return self._extract_exif_mapping(exif)
        except Exception as e:
            logger.debug("Error extracting EXIF bytes: %s", e)
            return {}

    def _extract_exif_ifd_from_bytes(self, exif_bytes: bytes) -> dict:
        """Extract EXIF IFD tags from raw EXIF bytes."""
        try:
            exif = Image.Exif()
            exif.load(exif_bytes)
            return self._extract_exif_ifd_mapping(exif)
        except Exception:
            return {}

    def _extract_sd_metadata_from_exif_bytes(self, exif_bytes: bytes) -> dict:
        """Extract SD-style metadata from raw EXIF bytes."""
        try:
            exif = Image.Exif()
            exif.load(exif_bytes)
            return self._extract_sd_metadata_from_exif(exif)
        except Exception as e:
            logger.debug("Error extracting SD metadata from EXIF bytes: %s", e)
            return {}

    def _extract_jpeg_xmp(self, image_path: str) -> dict:
        """Extract XMP metadata from JPEG APP1 segments."""
        try:
            with open(image_path, "rb") as jpeg_file:
                if jpeg_file.read(2) != JPEG_SIGNATURE:
                    return {}

                while True:
                    marker_prefix = jpeg_file.read(1)
                    if not marker_prefix:
                        break
                    if marker_prefix != b"\xff":
                        continue

                    marker = jpeg_file.read(1)
                    while marker == b"\xff":
                        marker = jpeg_file.read(1)
                    if not marker:
                        break

                    marker_code = marker[0]
                    if marker_code in {0xD8, 0xD9} or 0xD0 <= marker_code <= 0xD7:
                        continue
                    if marker_code == 0xDA:
                        break

                    length_bytes = jpeg_file.read(2)
                    if len(length_bytes) != 2:
                        break
                    segment_length = int.from_bytes(length_bytes, "big")
                    payload_length = segment_length - 2
                    if payload_length < 0 or payload_length > _MAX_JPEG_SEGMENT_BYTES:
                        break

                    payload = jpeg_file.read(payload_length)
                    if len(payload) != payload_length:
                        break
                    if not payload.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
                        continue

                    xmp_content = payload[len(b"http://ns.adobe.com/xap/1.0/\x00"):]
                    decoded_xmp = xmp_content.decode("utf-8", errors="replace")
                    return self._extract_xmp_sd_metadata(decoded_xmp)
        except Exception as e:
            logger.debug("Error extracting JPEG XMP from %s: %s", image_path, e, exc_info=True)
        return {}

    def _extract_webp_xmp(self, image_path: str) -> dict:
        """
        Extract XMP metadata from a WebP file manually by parsing chunks.
        WebP is a RIFF container, so we look for the 'XMP ' chunk.
        """
        metadata = {}
        try:
            with open(image_path, 'rb') as f:
                if f.read(4) != b"RIFF":
                    return metadata
                f.seek(4, os.SEEK_CUR)
                if f.read(4) != b"WEBP":
                    return metadata

                while True:
                    chunk_type = f.read(4)
                    if len(chunk_type) != 4:
                        break
                    size_bytes = f.read(4)
                    if len(size_bytes) != 4:
                        break
                    chunk_size = int.from_bytes(size_bytes, "little")
                    padded_size = chunk_size + (chunk_size % 2)
                    if chunk_type != b"XMP ":
                        f.seek(padded_size, os.SEEK_CUR)
                        continue
                    if chunk_size > _MAX_XMP_CHUNK_BYTES:
                        break

                    xmp_content = f.read(chunk_size)
                    try:
                        decoded_xmp = xmp_content.decode('utf-8', errors='replace')
                        metadata.update(self._extract_xmp_sd_metadata(decoded_xmp))
                    except Exception as e:
                        logger.debug("Failed to decode WebP XMP: %s", e)
                    break

        except Exception as e:
            logger.debug("Error extracting WebP XMP from %s: %s", image_path, e, exc_info=True)

        return metadata


def verify_image_readable(image_path: str) -> Tuple[bool, Optional[str]]:
    """Confirm an image can be fully decoded, not just opened for metadata."""
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            img.load()
        return True, None
    except Exception as exc:
        return False, str(exc)
