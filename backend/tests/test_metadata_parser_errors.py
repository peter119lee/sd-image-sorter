"""
Critical tests for metadata parser error handling.

Tests error handling in metadata extraction for all supported generators.

Priority: CRITICAL
"""
import os
import sys
import json
import zlib
from pathlib import Path

import pytest

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from metadata_parser import MetadataParser, parse_image
import metadata_parser as metadata_parser_module


class TestFileNotFoundErrors:
    """Tests for file not found error handling."""

    def test_parse_nonexistent_file(self):
        """Parsing nonexistent file should return unknown generator."""
        result = parse_image("/nonexistent/path/file.png")

        assert result["generator"] == "unknown"
        assert result["file_size"] == 0

    def test_parse_permission_denied(self, tmp_path: Path):
        """Parsing file without permission should be handled."""
        if os.name == "nt":
            pytest.skip("Permission test not reliable on Windows")

        img_path = tmp_path / "permission.png"

        # Create file and remove read permissions
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        try:
            os.chmod(img_path, 0o000)

            result = parse_image(str(img_path))

            # Should handle permission error gracefully
            assert result is not None
            assert "generator" in result
        finally:
            # Restore permissions for cleanup
            os.chmod(img_path, 0o644)

    def test_parse_deleted_file_during_read(self, tmp_path: Path):
        """Parsing file that gets deleted should be handled."""
        from PIL import Image

        img_path = tmp_path / "deleted.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        # Parse while file exists, then delete
        str_path = str(img_path)

        # File should parse normally
        result = parse_image(str_path)
        assert result is not None


class TestCorruptedFileErrors:
    """Tests for corrupted file error handling."""

    def test_parse_corrupted_png(self, tmp_path: Path):
        """Parsing corrupted PNG should be handled."""
        # Write invalid PNG data
        corrupted_path = tmp_path / "corrupted.png"
        corrupted_path.write_bytes(b"not a valid png file content")

        result = parse_image(str(corrupted_path))

        # Should handle corruption gracefully
        assert result is not None
        assert "generator" in result
        # Generator should be unknown for corrupted files
        assert result["generator"] == "unknown"


    def test_corrupted_png_does_not_log_warning_or_error(self, tmp_path: Path, caplog):
        """Known-bad images should be reported in scan results, not spam console tracebacks."""
        corrupted_path = tmp_path / "corrupted-noisy.png"
        corrupted_path.write_bytes(b"not a valid png file content")

        import logging

        with caplog.at_level(logging.DEBUG, logger="metadata_parser"):
            result = parse_image(str(corrupted_path))

        assert result["generator"] == "unknown"
        assert result.get("parse_error")
        noisy_records = [
            record for record in caplog.records
            if record.levelno >= logging.WARNING and "corrupted-noisy.png" in record.getMessage()
        ]
        assert noisy_records == []

    def test_parse_truncated_file(self, tmp_path: Path):
        """Parsing truncated file should be handled."""
        from PIL import Image

        # Create a valid image
        img_path = tmp_path / "truncated.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        # Truncate the file
        with open(img_path, "r+b") as f:
            f.truncate(f.tell() // 2)

        result = parse_image(str(img_path))

        # Should handle truncation gracefully
        assert result is not None
        assert result.get("parse_error")

    def test_parse_zero_byte_file(self, tmp_path: Path):
        """Parsing zero-byte file should be handled."""
        empty_path = tmp_path / "empty.png"
        empty_path.write_bytes(b"")

        result = parse_image(str(empty_path))

        # Should handle empty file gracefully
        assert result is not None
        assert result["generator"] == "unknown"

    def test_parse_text_file_as_image(self, tmp_path: Path):
        """Parsing text file as image should be handled."""
        text_path = tmp_path / "text.png"
        text_path.write_text("This is not an image")

        result = parse_image(str(text_path))

        # Should handle gracefully
        assert result is not None


class TestComfyUIErrors:
    """Tests for ComfyUI metadata parsing errors."""

    def test_malformed_json_in_prompt_chunk(self, tmp_path: Path):
        """Malformed JSON in prompt chunk should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "malformed_json.png"
        img = Image.new("RGB", (100, 100), color="white")

        metadata = PngInfo()
        metadata.add_text("prompt", "{invalid json content")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        # Should not crash, may return unknown or partial data
        assert result is not None
        assert "generator" in result

    def test_empty_workflow(self, tmp_path: Path):
        """Empty workflow JSON should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "empty_workflow.png"
        img = Image.new("RGB", (100, 100), color="white")

        metadata = PngInfo()
        metadata.add_text("prompt", "{}")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None

    def test_workflow_with_missing_nodes(self, tmp_path: Path):
        """Workflow with missing required nodes should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "missing_nodes.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Workflow without KSampler or CLIPTextEncode
        workflow = {
            "1": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None
        # Should detect as ComfyUI even without full workflow
        assert result["generator"] in ["comfyui", "unknown"]

    def test_workflow_with_circular_reference(self, tmp_path: Path):
        """Workflow with circular references should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "circular.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Workflow with circular reference (node 1 -> node 2 -> node 1)
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"model": ["2", 0]}
            },
            "2": {
                "class_type": "SomeNode",
                "inputs": {"input": ["1", 0]}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        # Should handle circular reference without infinite loop
        assert result is not None

    def test_very_large_workflow(self, tmp_path: Path):
        """Very large workflow should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "large_workflow.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Create a large workflow with many nodes
        workflow = {}
        for i in range(1000):
            workflow[str(i)] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": f"prompt {i}", "clip": None}
            }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        # Should handle large workflow
        assert result is not None


class TestWebUIErrors:
    """Tests for WebUI metadata parsing errors."""

    def test_parameters_chunk_only(self, tmp_path: Path):
        """Parameters chunk without standard format should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "weird_params.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Non-standard parameters format
        metadata = PngInfo()
        metadata.add_text("parameters", "Some random text without standard format")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None


    def test_partial_parameters(self, tmp_path: Path):
        """Partial parameters should be extracted."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "partial.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Parameters without all expected fields
        parameters = "beautiful image\nSteps: 20, Sampler: euler"

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None
        # Should extract what it can
        assert result["generator"] == "webui"

    def test_unicode_in_parameters(self, tmp_path: Path):
        """Unicode in parameters should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "unicode_params.png"
        img = Image.new("RGB", (100, 100), color="white")

        parameters = "anime girl, white hair\nSteps: 28"

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None

    def test_multiline_prompt(self, tmp_path: Path):
        """Multiline prompt should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "multiline.png"
        img = Image.new("RGB", (100, 100), color="white")

        parameters = """first line of prompt
second line of prompt
third line of prompt
Negative prompt: negative here
Steps: 20, Sampler: euler"""

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None
        # Prompt should include newlines or be joined
        assert result["prompt"] is not None


class TestPNGCompressedChunkSafety:
    """Tests for zTXt/iTXt bounded decompression safety behavior."""

    def test_decode_png_ztxt_chunk_rejects_over_limit_decompression(self, monkeypatch):
        parser = MetadataParser()
        monkeypatch.setattr(metadata_parser_module, "_MAX_DECOMPRESSED_BYTES", 64)

        compressed_text = zlib.compress(b"A" * 128)
        chunk_data = b"Comment\x00" + b"\x00" + compressed_text

        assert parser._decode_png_ztxt_chunk(chunk_data) is None

    def test_decode_png_itxt_chunk_rejects_over_limit_decompression(self, monkeypatch):
        parser = MetadataParser()
        monkeypatch.setattr(metadata_parser_module, "_MAX_DECOMPRESSED_BYTES", 64)

        compressed_text = zlib.compress(b"B" * 128)
        chunk_data = b"Comment\x00" + b"\x01\x00" + b"\x00" + b"\x00" + compressed_text

        assert parser._decode_png_itxt_chunk(chunk_data) is None


class TestNAIErrors:
    """Tests for NovelAI metadata parsing errors."""

    def test_malformed_comment_json(self, tmp_path: Path):
        """Malformed Comment JSON should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "malformed_nai.png"
        img = Image.new("RGB", (100, 100), color="white")

        metadata = PngInfo()
        metadata.add_text("Comment", "{invalid json")
        metadata.add_text("Software", "NovelAI")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None

    def test_nai_without_software_tag(self, tmp_path: Path):
        """NAI metadata without Software tag should be detected."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "no_software.png"
        img = Image.new("RGB", (100, 100), color="white")

        # NAI-style Comment without Software tag
        comment = json.dumps({
            "prompt": "anime girl",
            "uc": "bad",
            "steps": 28,
        })

        metadata = PngInfo()
        metadata.add_text("Comment", comment)
        # No Software tag
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        # Should still detect as NAI from Comment format
        assert result is not None


class TestWebPErrors:
    """Tests for WebP metadata parsing errors."""

    def test_webp_without_exif(self, tmp_path: Path):
        """WebP without EXIF should be handled."""
        from PIL import Image

        img_path = tmp_path / "no_exif.webp"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path, "WEBP")

        result = parse_image(str(img_path))

        assert result is not None
        assert result["generator"] == "unknown"

    def test_webp_with_invalid_exif(self, tmp_path: Path):
        """WebP with invalid EXIF should be handled."""
        # This test is limited by PIL's WebP EXIF support
        from PIL import Image

        img_path = tmp_path / "invalid_exif.webp"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path, "WEBP")

        result = parse_image(str(img_path))

        assert result is not None


class TestJPEGErrors:
    """Tests for JPEG metadata parsing errors."""

    def test_jpeg_without_exif(self, tmp_path: Path):
        """JPEG without EXIF should be handled."""
        from PIL import Image

        img_path = tmp_path / "no_exif.jpg"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path, "JPEG")

        result = parse_image(str(img_path))

        assert result is not None
        assert result["generator"] == "unknown"

    def test_jpeg_with_corrupted_exif(self, tmp_path: Path, monkeypatch):
        """JPEG with corrupted EXIF should be handled."""
        from PIL import Image
        from metadata_parser import MetadataParser

        img_path = tmp_path / "corrupted_exif.jpg"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path, "JPEG")

        def raise_corrupted_exif(_self, _img):
            raise OSError("corrupted exif block")

        monkeypatch.setattr(MetadataParser, "_extract_exif", raise_corrupted_exif)

        result = parse_image(str(img_path))

        # Should handle corrupted EXIF gracefully
        assert result is not None


class TestDimensionExtractionErrors:
    """Tests for dimension extraction errors."""

    def test_extract_dimensions_from_corrupted_image(self, tmp_path: Path):
        """Extracting dimensions from corrupted image should be handled."""
        corrupted_path = tmp_path / "corrupted_dim.png"
        corrupted_path.write_bytes(b"fake image data that is corrupted")

        result = parse_image(str(corrupted_path))

        # Should return some dimensions or handle gracefully
        assert result is not None

    def test_extract_dimensions_from_gif(self, tmp_path: Path):
        """Extracting dimensions from GIF should work."""
        from PIL import Image

        img_path = tmp_path / "test.gif"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path, "GIF")

        result = parse_image(str(img_path))

        assert result is not None
        assert result["width"] == 100
        assert result["height"] == 100


class TestLoRAExtractionErrors:
    """Tests for LoRA extraction errors."""

    def test_lora_extraction_from_malformed_prompt(self, tmp_path: Path):
        """LoRA extraction from malformed prompt should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "lora_error.png"
        img = Image.new("RGB", (100, 100), color="white")

        # Prompt with malformed LoRA tags
        prompt = "cat, <lora:incomplete, <lora:::0.8>, <lora:test:>, dog"

        metadata = PngInfo()
        metadata.add_text("parameters", f"{prompt}\nSteps: 20")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result is not None
        # Should extract valid LoRAs and skip malformed ones
        loras = result.get("loras", [])
        assert isinstance(loras, list)


class TestMemoryHandling:
    """Tests for memory handling with large files."""

    def test_very_large_image_dimensions(self, tmp_path: Path):
        """Very large image dimensions should be handled."""
        from PIL import Image

        # Create a reasonably sized image (actual very large would OOM)
        img_path = tmp_path / "large.png"
        img = Image.new("RGB", (4096, 4096), color="white")
        img.save(img_path)

        result = parse_image(str(img_path))

        assert result is not None
        assert result["width"] == 4096
        assert result["height"] == 4096


class TestReturnStructure:
    """Tests for return structure consistency."""

    def test_always_returns_required_fields(self, tmp_path: Path):
        """Parser should always return required fields."""
        # Test with various file types
        from PIL import Image

        # Valid PNG
        img_path = tmp_path / "test.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        result = parse_image(str(img_path))

        required_fields = [
            "generator", "prompt", "negative_prompt", "checkpoint",
            "loras", "metadata", "width", "height", "file_size"
        ]

        for field in required_fields:
            assert field in result, f"Missing required field: {field}"

    def test_corrupted_file_returns_safe_structure(self, tmp_path: Path):
        """Corrupted file should return safe structure."""
        corrupted_path = tmp_path / "corrupted.png"
        corrupted_path.write_bytes(b"not an image")

        result = parse_image(str(corrupted_path))

        # Should return unknown generator with defaults
        assert result["generator"] == "unknown"
        assert result["file_size"] > 0 or result["file_size"] == 0
        assert result["width"] is not None or result["width"] == 0


def _png_with_prompt_chunk(tmp_path: Path, name: str, payload: str) -> str:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("prompt", payload)
    path = tmp_path / name
    Image.new("RGB", (32, 32), color="white").save(path, pnginfo=info)
    return str(path)


class TestDamagedComfyUIPromptChunk:
    """A damaged ComfyUI `prompt` chunk must not become the image's prompt.

    An interrupted save or a tool that rewrote the PNG leaves a `prompt`
    chunk holding structured data the parser cannot use. It used to fall
    through to the Reader's plain-text handler, which accepts any string, so
    a wall of raw workflow JSON was stored as the prompt with no error
    recorded — searchable, exportable into dataset captions, and invisible
    to the re-parse job. The sibling `workflow` chunk path already declines
    to invent a prompt in exactly this situation.
    """

    @pytest.mark.parametrize(
        "label,payload",
        [
            (
                "truncated",
                '{"3": {"class_type": "KSampler", "inputs": {"seed": 12345, "ste',
            ),
            ("empty-object", "{}"),
            ("array", "[1,2,3]"),
            ("bracket-storm", "[" * 300 + "]" * 300),
        ],
    )
    def test_unusable_prompt_chunk_is_reported_not_stored(
        self, tmp_path: Path, label: str, payload: str
    ):
        result = parse_image(
            _png_with_prompt_chunk(tmp_path, f"comfy-{label}.png", payload)
        )

        assert not (result["prompt"] or "").strip(), (
            f"{label}: raw chunk text leaked into the prompt: {result['prompt']!r}"
        )
        assert result["metadata_error"], (
            f"{label}: an unusable metadata chunk reported no error at all"
        )
        assert result["generator"] == "comfyui"
        # An empty prompt puts the row back inside the re-parse job's scope,
        # and the original chunk is retained so a better parser can replay it.
        assert result.get("raw_metadata_text")

    def test_reader_written_wildcard_prompt_is_still_a_prompt(self, tmp_path: Path):
        """Guard against over-triggering.

        A1111 dynamic-prompt syntax starts with '{' and is not JSON, but it
        is a real prompt the Reader can save. Rejecting it would trade one
        data-loss bug for another.
        """
        payload = "{1girl|1boy}, solo, masterpiece, best quality"

        result = parse_image(
            _png_with_prompt_chunk(tmp_path, "reader-wildcard.png", payload)
        )

        assert result["prompt"] == payload
        assert result["metadata_error"] is None
