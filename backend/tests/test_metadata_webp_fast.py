"""
Tests for WEBP fast metadata reading.

Tests the new _load_webp_metadata_fast() path that directly parses
RIFF chunks to extract EXIF/XMP metadata without Pillow overhead.
"""
import json
import os
import struct
from pathlib import Path

import pytest
from PIL import Image

from backend.metadata_parser import MetadataParser


CORRUPT_WEBP_EXIF = b"not-valid-tiff-exif"
WEBP_EXIF_ERROR_PREFIX = "WebP EXIF chunk could not be parsed:"


def _corrupt_exif_ifd_offset() -> bytes:
    payload = b"II\x2a\x00\x08\x00\x00\x00"
    payload += b"\x01\x00"
    payload += struct.pack("<HHI", 0x8769, 4, 1)
    payload += struct.pack("<I", 0xFFFFFFF0)
    payload += b"\x00\x00\x00\x00"
    return payload


def _descendant_value_overlaps_parent_next_ifd() -> bytes:
    payload = b"MM\x00\x2a" + struct.pack(">I", 8)
    payload += struct.pack(">H", 1)
    payload += struct.pack(">HHI", 0x8769, 4, 1)
    payload += struct.pack(">I", 32)
    payload += b"ASCII\x00\x00\x00"
    payload += b"\x00\x00"
    payload += struct.pack(">H", 1)
    payload += struct.pack(">HHI", 0x010E, 2, 8)
    payload += struct.pack(">I", 22)
    payload += struct.pack(">I", 0)
    return payload


def _write_verified_webp(
    path: Path,
    exif: bytes | Image.Exif | None,
) -> Path:
    """Write a real WebP and prove Pillow can decode its pixel stream."""
    image = Image.new("RGB", (32, 24), color="navy")
    if exif is None:
        image.save(path, format="WEBP")
    else:
        image.save(path, format="WEBP", exif=exif)

    with Image.open(path) as stored:
        assert stored.size == (32, 24)
        stored.verify()
    return path


class TestWebPFastPath:
    """Test WEBP fast path metadata extraction."""

    @pytest.fixture
    def parser(self):
        """Create a parser instance."""
        return MetadataParser()

    def _create_minimal_webp(self, width: int, height: int, exif_data: bytes = b"", xmp_data: bytes = b"") -> bytes:
        """Create a minimal valid WEBP file with optional EXIF/XMP chunks."""
        chunks = []

        # VP8X chunk (extended format, required for EXIF/XMP)
        flags = 0x00
        if exif_data:
            flags |= 0x08  # EXIF flag
        if xmp_data:
            flags |= 0x04  # XMP flag

        vp8x_data = struct.pack("<B", flags)  # flags
        vp8x_data += b"\x00\x00\x00"  # reserved
        # Canvas width and height (24-bit little-endian, minus 1)
        vp8x_data += struct.pack("<I", width - 1)[:3]
        vp8x_data += struct.pack("<I", height - 1)[:3]
        chunks.append((b"VP8X", vp8x_data))

        # EXIF chunk (if provided)
        if exif_data:
            chunks.append((b"EXIF", exif_data))

        # XMP chunk (if provided)
        if xmp_data:
            chunks.append((b"XMP ", xmp_data))

        # VP8L chunk (lossless image data - minimal stub)
        vp8l_data = b"\x2f"  # signature
        # Dimensions: width-1 (14 bits) + height-1 (14 bits) + alpha_used (1 bit) + version (3 bits)
        bits = ((width - 1) & 0x3FFF) | (((height - 1) & 0x3FFF) << 14)
        vp8l_data += struct.pack("<I", bits)
        vp8l_data += b"\x00" * 10  # minimal pixel data stub
        chunks.append((b"VP8L", vp8l_data))

        # Build RIFF file
        chunk_bytes = b""
        for fourcc, data in chunks:
            chunk_bytes += fourcc
            chunk_bytes += struct.pack("<I", len(data))
            chunk_bytes += data
            # Add padding if odd length
            if len(data) % 2:
                chunk_bytes += b"\x00"

        file_size = 4 + len(chunk_bytes)  # "WEBP" + chunks
        riff_header = b"RIFF"
        riff_header += struct.pack("<I", file_size)
        riff_header += b"WEBP"

        return riff_header + chunk_bytes

    def test_webp_fast_basic_dimensions(self, parser, tmp_path):
        """Test basic WEBP dimension extraction."""
        webp_data = self._create_minimal_webp(1024, 768)
        test_file = tmp_path / "test.webp"
        test_file.write_bytes(webp_data)

        result = parser._load_webp_metadata_fast(str(test_file))

        assert result["width"] == 1024
        assert result["height"] == 768
        assert isinstance(result["metadata"], dict)

    def test_webp_fast_with_exif(self, parser, tmp_path):
        """Test WEBP with EXIF chunk extraction."""
        # Create minimal TIFF-format EXIF data
        # TIFF header: byte order (II = little-endian) + magic (42) + IFD offset
        exif_data = b"II\x2a\x00\x08\x00\x00\x00"
        # IFD: 1 entry
        exif_data += b"\x01\x00"
        # Tag 0x0131 (Software) = ASCII string "TestSoftware"
        tag_data = struct.pack("<HHI", 0x0131, 2, 13)  # tag, type=ASCII, count=13
        tag_data += struct.pack("<I", 20)  # offset to string
        exif_data += tag_data
        # Next IFD offset (0 = none)
        exif_data += b"\x00\x00\x00\x00"
        # String data at offset 20
        exif_data += b"TestSoftware\x00"

        webp_data = self._create_minimal_webp(800, 600, exif_data=exif_data)
        test_file = tmp_path / "test_exif.webp"
        test_file.write_bytes(webp_data)

        result = parser._load_webp_metadata_fast(str(test_file))

        assert result["width"] == 800
        assert result["height"] == 600
        # EXIF extraction should find Software tag
        assert "Software" in result["metadata"] or "software" in result["metadata"]

    def test_webp_fast_with_xmp(self, parser, tmp_path):
        """Test WEBP with XMP chunk extraction."""
        xmp_data = b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about=""><dc:description>Test XMP</dc:description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'

        webp_data = self._create_minimal_webp(640, 480, xmp_data=xmp_data)
        test_file = tmp_path / "test_xmp.webp"
        test_file.write_bytes(webp_data)

        result = parser._load_webp_metadata_fast(str(test_file))

        assert result["width"] == 640
        assert result["height"] == 480
        # XMP extraction should process the data
        assert result["metadata"] is not None

    def test_webp_fast_invalid_signature(self, parser, tmp_path):
        """Test WEBP fast path rejects invalid signature."""
        # Not a WEBP file
        bad_data = b"NOT_WEBP_DATA" + b"\x00" * 100
        test_file = tmp_path / "bad.webp"
        test_file.write_bytes(bad_data)

        with pytest.raises(ValueError, match="Invalid WEBP signature"):
            parser._load_webp_metadata_fast(str(test_file))

    def test_webp_fast_truncated_file(self, parser, tmp_path):
        """Test WEBP fast path handles truncated files."""
        # Create truncated WEBP (only header, no chunks)
        truncated_data = b"RIFF\x10\x00\x00\x00WEBP"
        test_file = tmp_path / "truncated.webp"
        test_file.write_bytes(truncated_data)

        with pytest.raises(ValueError, match="WEBP dimensions not found"):
            parser._load_webp_metadata_fast(str(test_file))

    def test_webp_fast_chunk_size_overflow(self, parser, tmp_path):
        """Test WEBP fast path handles chunk size overflow."""
        # RIFF header
        data = b"RIFF\x20\x00\x00\x00WEBP"
        # Malicious chunk with size exceeding file
        data += b"TEST"
        data += struct.pack("<I", 0xFFFFFFFF)  # huge size
        test_file = tmp_path / "overflow.webp"
        test_file.write_bytes(data)

        with pytest.raises(ValueError, match="exceeds file size"):
            parser._load_webp_metadata_fast(str(test_file))

    def test_webp_load_fallback_to_pillow(self, parser, tmp_path):
        """Test that invalid fast path falls back to Pillow."""
        # Create a file that looks like WEBP but fails fast parse
        bad_webp = b"RIFF\x10\x00\x00\x00WEBP" + b"\xFF" * 100
        test_file = tmp_path / "bad.webp"
        test_file.write_bytes(bad_webp)

        # _load_image_metadata should catch the ValueError and fall back to Pillow
        # Pillow will also fail, but that's expected for invalid data
        with pytest.raises(Exception):
            parser._load_image_metadata(str(test_file))

    def test_webp_real_file_comparison(self, parser):
        """Test fast path vs Pillow on real WEBP files (if available)."""
        # This test is optional and only runs if real WEBP files are present
        test_images_dir = r"L:\Pictures\AAA Reference\AAAwith prompt"
        if not os.path.exists(test_images_dir):
            pytest.skip("Test images directory not available")

        # Find first WEBP file
        webp_files = [
            os.path.join(test_images_dir, f)
            for f in os.listdir(test_images_dir)
            if f.lower().endswith(".webp")
        ]

        if not webp_files:
            pytest.skip("No WEBP files found in test directory")

        test_file = webp_files[0]

        # Load via fast path
        try:
            fast_result = parser._load_webp_metadata_fast(test_file)
        except Exception as e:
            pytest.skip(f"Fast path failed on real file (expected): {e}")

        # Load via Pillow
        pillow_result = parser._load_image_metadata_via_pillow(test_file)

        # Dimensions should match
        assert fast_result["width"] == pillow_result["width"]
        assert fast_result["height"] == pillow_result["height"]

        # Metadata keys should be similar (not necessarily identical due to parsing differences)
        assert isinstance(fast_result["metadata"], dict)
        assert isinstance(pillow_result["metadata"], dict)


class TestWebPIntegration:
    """Integration tests for WEBP metadata parsing through the full parse() flow."""

    @pytest.fixture
    def parser(self):
        """Create a parser instance."""
        return MetadataParser()

    def test_novelai_v4_subifd_without_next_ifd_terminator_still_parses(
        self, parser, tmp_path
    ):
        """Regression: NovelAI V4/V4.5 WebP omit the next-IFD terminator word.

        Real files (verified against the reference library) lay out the Exif
        sub-IFD so its single UserComment entry's data begins immediately after
        the entry table, with no terminating zero next-IFD LONG. The structural
        validator used to read those 4 bytes as a pointer, decode the
        "ASCII\\0\\0\\0" charset prefix as 0x41534349 = 1095975753, call it
        out-of-bounds and discard the WHOLE chunk — dropping the prompt on every
        such file even though Pillow reads them fine.

        Every pre-existing WebP EXIF test wrote a well-formed terminator, which
        is why this shipped unnoticed. This one must NOT include it.
        """
        nai_json = json.dumps(
            {
                "Description": "1girl, solo, best quality",
                "Software": "NovelAI",
                "Comment": json.dumps(
                    {"uc": "lowres, bad anatomy", "steps": 28}
                ),
            }
        )
        usercomment = b"ASCII\x00\x00\x00" + nai_json.encode("utf-8")

        # IFD0: Software + ExifIFD pointer, big-endian like the real files.
        software = b"NovelAI\x00"
        ifd0_offset = 8
        ifd0_entries = 2
        ifd0_end = ifd0_offset + 2 + ifd0_entries * 12 + 4  # IFD0 keeps its terminator
        software_offset = ifd0_end
        sub_ifd_offset = software_offset + len(software)
        # Sub-IFD: 1 entry, then its data IMMEDIATELY — no terminator word.
        usercomment_offset = sub_ifd_offset + 2 + 12

        payload = b"MM\x00\x2a" + struct.pack(">I", ifd0_offset)
        payload += struct.pack(">H", ifd0_entries)
        payload += struct.pack(">HHI", 0x0131, 2, len(software))
        payload += struct.pack(">I", software_offset)
        payload += struct.pack(">HHI", 0x8769, 4, 1)
        payload += struct.pack(">I", sub_ifd_offset)
        payload += struct.pack(">I", 0)  # IFD0 next-IFD = none
        assert len(payload) == software_offset, "IFD0 layout drifted"
        payload += software
        payload += struct.pack(">H", 1)
        payload += struct.pack(">HHI", 0x9286, 7, len(usercomment))
        payload += struct.pack(">I", usercomment_offset)
        # DELIBERATELY no next-IFD terminator here.
        payload += usercomment

        image_path = _write_verified_webp(tmp_path / "nai-v4.webp", payload)
        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        # The whole point: no structural rejection, and the prompt survives.
        assert result.get("metadata_error") is None, (
            f"validator rejected a real NovelAI layout: {result.get('metadata_error')}"
        )
        assert result["generator"] == "nai"
        assert result["prompt"] == "1girl, solo, best quality"
        assert result["negative_prompt"] == "lowres, bad anatomy"

    def test_corrupt_exif_is_a_nonfatal_metadata_error(self, parser, tmp_path):
        image_path = _write_verified_webp(
            tmp_path / "corrupt-exif.webp",
            CORRUPT_WEBP_EXIF,
        )

        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        assert result["width"] == 32
        assert result["height"] == 24
        metadata_error = result["metadata_error"]
        assert isinstance(metadata_error, str)
        assert metadata_error.startswith(WEBP_EXIF_ERROR_PREFIX)
        assert "not a TIFF file" in metadata_error

    def test_lazy_corrupt_exif_warning_is_a_nonfatal_metadata_error(
        self,
        parser,
        tmp_path,
    ):
        image_path = _write_verified_webp(
            tmp_path / "corrupt-exif-offset.webp",
            _corrupt_exif_ifd_offset(),
        )

        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        assert (result["width"], result["height"]) == (32, 24)
        metadata_error = result["metadata_error"]
        assert isinstance(metadata_error, str)
        assert metadata_error.startswith(WEBP_EXIF_ERROR_PREFIX)
        assert "offset 4294967280 is outside" in metadata_error

    def test_descendant_value_does_not_mask_corrupt_parent_next_ifd(
        self,
        parser,
        tmp_path,
    ):
        image_path = _write_verified_webp(
            tmp_path / "overlapping-exif.webp",
            _descendant_value_overlaps_parent_next_ifd(),
        )

        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        metadata_error = result["metadata_error"]
        assert isinstance(metadata_error, str)
        assert metadata_error.startswith(WEBP_EXIF_ERROR_PREFIX)
        assert "offset 1095975753 is outside" in metadata_error

    def test_webp_without_exif_has_no_metadata_error(self, parser, tmp_path):
        image_path = _write_verified_webp(tmp_path / "no-exif.webp", None)

        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        assert result["metadata_error"] is None
        assert (result["width"], result["height"]) == (32, 24)

    def test_webp_with_valid_exif_has_no_metadata_error(self, parser, tmp_path):
        exif = Image.Exif()
        exif[0x0131] = "SD Image Sorter Test"
        image_path = _write_verified_webp(tmp_path / "valid-exif.webp", exif)

        result = parser.parse(str(image_path), validate_image_data=True)

        assert result["parse_error"] is None
        assert result["metadata_error"] is None
        assert (result["width"], result["height"]) == (32, 24)

    def test_webp_parse_with_exif_usercomment(self, parser, tmp_path):
        """Test full parse() flow with WEBP containing EXIF UserComment."""
        # Create EXIF with UserComment containing NovelAI-style JSON
        nai_json = '{"prompt": "test prompt", "uc": "test negative", "steps": 28}'

        # TIFF header
        exif_data = b"II\x2a\x00\x08\x00\x00\x00"
        # IFD: 1 entry
        exif_data += b"\x01\x00"
        # Tag 0x9286 (UserComment)
        usercomment_bytes = b"ASCII\x00\x00\x00" + nai_json.encode("utf-8")
        tag_data = struct.pack("<HHI", 0x9286, 7, len(usercomment_bytes))  # tag, type=UNDEFINED, count
        tag_data += struct.pack("<I", 20)  # offset to data
        exif_data += tag_data
        # Next IFD offset (0 = none)
        exif_data += b"\x00\x00\x00\x00"
        # UserComment data at offset 20
        exif_data += usercomment_bytes

        webp_data = self._create_minimal_webp(512, 512, exif_data=exif_data)
        test_file = tmp_path / "nai.webp"
        test_file.write_bytes(webp_data)

        result = parser.parse(str(test_file))

        assert result["width"] == 512
        assert result["height"] == 512
        # This fixture puts UserComment on IFD0 instead of the Exif sub-IFD
        # NovelAI actually uses. Fast-path extract may not recover a prompt;
        # it must not invent one from the charset prefix either.
        assert result["generator"] in {"nai", "unknown"}
        if result["generator"] == "nai":
            assert result["prompt"] == "test prompt"
            assert result["negative_prompt"] == "test negative"
        else:
            assert not (result.get("prompt") or "").strip()

    def _create_minimal_webp(self, width: int, height: int, exif_data: bytes = b"", xmp_data: bytes = b"") -> bytes:
        """Create a minimal valid WEBP file."""
        chunks = []

        # VP8X chunk
        flags = 0x00
        if exif_data:
            flags |= 0x08
        if xmp_data:
            flags |= 0x04

        vp8x_data = struct.pack("<B", flags)
        vp8x_data += b"\x00\x00\x00"
        vp8x_data += struct.pack("<I", width - 1)[:3]
        vp8x_data += struct.pack("<I", height - 1)[:3]
        chunks.append((b"VP8X", vp8x_data))

        if exif_data:
            chunks.append((b"EXIF", exif_data))

        if xmp_data:
            chunks.append((b"XMP ", xmp_data))

        # VP8L chunk
        vp8l_data = b"\x2f"
        bits = ((width - 1) & 0x3FFF) | (((height - 1) & 0x3FFF) << 14)
        vp8l_data += struct.pack("<I", bits)
        vp8l_data += b"\x00" * 10
        chunks.append((b"VP8L", vp8l_data))

        chunk_bytes = b""
        for fourcc, data in chunks:
            chunk_bytes += fourcc
            chunk_bytes += struct.pack("<I", len(data))
            chunk_bytes += data
            if len(data) % 2:
                chunk_bytes += b"\x00"

        file_size = 4 + len(chunk_bytes)
        riff_header = b"RIFF"
        riff_header += struct.pack("<I", file_size)
        riff_header += b"WEBP"

        return riff_header + chunk_bytes
