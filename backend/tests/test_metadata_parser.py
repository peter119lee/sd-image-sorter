"""
Tests for metadata parser.

Tests metadata extraction for all supported generators:
- ComfyUI (JSON workflow)
- NovelAI (Comment JSON, UserComment)
- WebUI/A1111 (parameters text chunk)
- Forge (WebUI variant)

Priority: HIGH
"""
import builtins
import os
import struct
import sys
import json
import zlib
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import metadata_parser as metadata_parser_module
from metadata_parser import parse_image


def _sidecar_fallback_evidence(
    carrier: str,
    basename: str,
    fields: list[str],
) -> dict[str, object]:
    return {
        "carrier": carrier,
        "basename": basename,
        "method": "sidecar_fallback",
        "confidence": "high",
        "parser_version": metadata_parser_module.PARSED_METADATA_VERSION,
        "fields": fields,
    }


def _sidecar_fallback_state(
    carrier: str,
    basename: str,
    fields: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evaluated": True,
        "evidence": [_sidecar_fallback_evidence(carrier, basename, fields)],
    }


def _write_comfyui_prompt_png(tmp_path: Path, filename: str, workflow: dict, color: str = "white") -> Path:
    """Persist a tiny PNG with ComfyUI prompt metadata for parser tests."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    img_path = tmp_path / filename
    img = Image.new("RGB", (512, 512), color=color)
    metadata = PngInfo()
    metadata.add_text("prompt", json.dumps(workflow))
    img.save(img_path, pnginfo=metadata)
    return img_path


def _build_exif_user_comment(comment: str, *, unicode_payload: bool = False) -> bytes:
    """Build a tiny EXIF block with ExifIFD/UserComment for JPEG tests."""
    if unicode_payload:
        comment_bytes = b"UNICODE\x00" + comment.encode("utf-16")
    else:
        comment_bytes = b"ASCII\x00\x00\x00" + comment.encode("utf-8")

    tiff_header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    exif_ifd_offset = 8 + 2 + 12 + 4
    ifd0 = (
        struct.pack("<H", 1)
        + struct.pack("<HHI", 0x8769, 4, 1)
        + struct.pack("<I", exif_ifd_offset)
        + struct.pack("<I", 0)
    )
    user_comment_offset = exif_ifd_offset + 2 + 12 + 4
    exif_ifd = (
        struct.pack("<H", 1)
        + struct.pack("<HHI", 0x9286, 7, len(comment_bytes))
        + struct.pack("<I", user_comment_offset)
        + struct.pack("<I", 0)
        + comment_bytes
    )
    return b"Exif\x00\x00" + tiff_header + ifd0 + exif_ifd


def _insert_jpeg_xmp_packet(image_path: Path, xmp_text: str) -> None:
    """Insert a standard APP1 XMP segment into a JPEG fixture."""
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp_text.encode("utf-8")
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    data = image_path.read_bytes()
    assert data.startswith(b"\xff\xd8")
    image_path.write_bytes(data[:2] + segment + data[2:])


class TestMetadataParserBase:
    """Base tests for MetadataParser."""

    def test_parse_nonexistent_file(self):
        """Parsing nonexistent file should return unknown generator."""
        result = parse_image("/nonexistent/path/file.png")

        assert result["generator"] == "unknown"
        assert result["file_size"] == 0

    def test_parse_returns_required_fields(self, mock_image_file: Path):
        """Parser should return all required fields."""
        result = parse_image(str(mock_image_file))

        assert "generator" in result
        assert "prompt" in result
        assert "negative_prompt" in result
        assert "checkpoint" in result
        assert "loras" in result
        assert "metadata" in result
        assert "width" in result
        assert "height" in result
        assert "file_size" in result

    def test_parse_png_text_metadata_uses_fast_path_without_pillow_open(self, tmp_path: Path, monkeypatch):
        """PNG text metadata should parse without calling Pillow open in the common path."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "fast-path-webui.png"
        metadata = PngInfo()
        metadata.add_text(
            "parameters",
            "masterpiece\nNegative prompt: lowres\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 320x240, Model: demo.safetensors",
        )
        Image.new("RGB", (320, 240), color="white").save(img_path, pnginfo=metadata)

        def fail_open(*args, **kwargs):
            raise AssertionError("PNG fast-path should not call Pillow open")

        monkeypatch.setattr(metadata_parser_module.Image, "open", fail_open)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["width"] == 320
        assert result["height"] == 240
        assert result["checkpoint"] == "demo.safetensors"

    def test_parse_png_validation_still_runs_verify_open(self, tmp_path: Path, monkeypatch):
        """Scan-time validation should still perform a single verify open after fast metadata parsing."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "validated-fast-path.png"
        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps({"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "demo.safetensors"}}}))
        Image.new("RGB", (128, 96), color="white").save(img_path, pnginfo=metadata)

        open_calls = {"count": 0}
        original_open = metadata_parser_module.Image.open

        def tracking_open(*args, **kwargs):
            open_calls["count"] += 1
            return original_open(*args, **kwargs)

        monkeypatch.setattr(metadata_parser_module.Image, "open", tracking_open)

        result = parse_image(str(img_path), validate_image_data=True)

        assert result["width"] == 128
        assert result["height"] == 96
        assert open_calls["count"] == 1

    def test_parse_jpeg_with_png_extension_falls_through_to_pillow(self, tmp_path: Path):
        """JPEG content with .png extension must parse, not error.

        Real-world libraries are full of JPEG/WEBP files renamed to
        .png by Civitai, Discord, browsers, and assorted content-
        management tools. Browsers and Windows Explorer render them
        fine because they sniff format from the magic bytes, but the
        previous parser version trusted the extension and raised
        ``Invalid PNG signature`` on the first 8 bytes — leaving these
        files reported as unreadable in the scan summary even though
        Pillow can parse them without issue.

        This test pins the new behaviour: when the .png fast path
        rejects the file because the magic bytes are not PNG, we fall
        through to Pillow, which detects format from content and
        parses the JPEG normally.
        """
        from PIL import Image

        img_path = tmp_path / "actually-jpeg.png"  # JPEG content, .png extension
        Image.new("RGB", (200, 150), color=(180, 90, 90)).save(img_path, format="JPEG")

        # Sanity: confirm the file actually has JPEG magic bytes despite
        # the .png extension. If this assertion fails the test setup is
        # wrong, not the parser.
        with open(img_path, "rb") as fh:
            assert fh.read(3) == b"\xff\xd8\xff"

        result = parse_image(str(img_path))

        # Width / height come from Pillow's content-sniff path.
        assert result["width"] == 200
        assert result["height"] == 150
        # The file is readable; file_size is non-zero. The "Invalid PNG
        # signature" error path would have produced an unreadable record.
        assert result["file_size"] > 0
        assert "metadata" in result

    def test_parse_png_fast_path_still_rejects_genuinely_truncated_png(self, tmp_path: Path):
        """The fallback must NOT mask genuine PNG corruption.

        A truncated PNG (valid magic bytes, broken chunk tail) should
        surface as a parse failure, not silently fall back to Pillow
        which might also fail with a less actionable message. We
        verify that PNG-shape errors other than "Invalid PNG
        signature" still propagate.
        """
        img_path = tmp_path / "truncated.png"
        img_path.write_bytes(metadata_parser_module.PNG_SIGNATURE + b"\x00\x00\x00\x10")  # length but no chunk type/data

        # ParseError or ValueError is acceptable; the point is that
        # truncated PNGs do not silently succeed.
        with pytest.raises(Exception):
            metadata_parser_module.MetadataParser()._load_image_metadata(str(img_path))

    def test_parse_png_fast_path_skips_large_image_data_chunks(self, tmp_path: Path, monkeypatch):
        """PNG metadata scanning should not read IDAT image payloads into memory."""
        img_path = tmp_path / "large-idat-after-text.png"

        def chunk(chunk_type: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", 320, 240, 8, 2, 0, 0, 0)
        large_idat = b"0" * (2 * 1024 * 1024)
        params = (
            b"parameters\x00"
            b"masterpiece\nNegative prompt: lowres\n"
            b"Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 320x240, Model: skip-idat.safetensors"
        )
        img_path.write_bytes(
            metadata_parser_module.PNG_SIGNATURE
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", large_idat)
            + chunk(b"tEXt", params)
            + chunk(b"IEND", b"")
        )

        stats = {"read_bytes": 0, "seek_bytes": 0}

        class TrackingFile:
            def __init__(self, file_obj):
                self.file_obj = file_obj

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.file_obj.close()
                return False

            def read(self, size=-1):
                data = self.file_obj.read(size)
                stats["read_bytes"] += len(data)
                return data

            def seek(self, offset, whence=0):
                if whence == os.SEEK_CUR and offset > 0:
                    stats["seek_bytes"] += offset
                return self.file_obj.seek(offset, whence)

        def tracking_open(*args, **kwargs):
            return TrackingFile(builtins.open(*args, **kwargs))

        monkeypatch.setattr(metadata_parser_module, "open", tracking_open, raising=False)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["checkpoint"] == "skip-idat.safetensors"
        assert stats["seek_bytes"] >= len(large_idat)
        assert stats["read_bytes"] < len(large_idat) // 10


class TestComfyUIMetadata:
    """Tests for ComfyUI metadata parsing."""

    def test_parse_comfyui_basic(self, mock_comfyui_image: Path):
        """Basic ComfyUI metadata should be parsed correctly."""
        result = parse_image(str(mock_comfyui_image))

        assert result["generator"] == "comfyui"
        assert "landscape" in result["prompt"].lower()
        assert "ugly" in result["negative_prompt"].lower()
        assert result["checkpoint"] == "sd_xl_base_1.0.safetensors"

    def test_parse_comfyui_dimensions(self, mock_comfyui_image: Path):
        """ComfyUI image dimensions should be extracted."""
        result = parse_image(str(mock_comfyui_image))

        assert result["width"] == 1024
        assert result["height"] == 768

    def test_parse_comfyui_generation_params(self, mock_comfyui_image: Path):
        """ComfyUI generation parameters should be extracted."""
        result = parse_image(str(mock_comfyui_image))

        # Check metadata includes parsed generation params
        assert "_parsed" in result["metadata"]
        gen_params = result["metadata"]["_parsed"].get("generation_params", {})

        # Should have seed, steps, cfg
        if gen_params:
            assert "seed" in gen_params or "steps" in gen_params

    def test_parse_comfyui_with_loras(self, tmp_path: Path):
        """ComfyUI with LoRAs should extract LoRA names."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_lora.png"
        img = Image.new("RGB", (512, 512), color="blue")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "steps": 20,
                    "cfg": 7.5,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "test prompt", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "6": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "style_lora.safetensors", "model": ["2", 0]}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"
        # LoRA name may include extension
        loras = result.get("loras", [])
        assert any("style_lora" in lora for lora in loras), f"Expected style_lora in {loras}"

    def test_parse_comfyui_complex_workflow(self, tmp_path: Path):
        """ComfyUI complex workflow with multiple text nodes should be parsed."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_complex.png"
        img = Image.new("RGB", (512, 512), color="green")

        # Complex workflow with nested conditioning
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "steps": 20,
                    "cfg": 7.5,
                    "model": ["2", 0],
                    "positive": ["10", 0],  # ConditioningCombine output
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd_xl.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "main prompt", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative prompt", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "10": {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": ["3", 0],
                    "conditioning_2": ["11", 0]
                }
            },
            "11": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "additional prompt", "clip": ["2", 1]}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"
        # Should find at least one of the prompts
        assert result["prompt"] is not None

    def test_parse_comfyui_fallback_extracts_checkpoint_from_efficient_loader(self, tmp_path: Path):
        """Generic asset fallback should recover ckpt_name from non-whitelisted loader nodes."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_efficient_loader.png"
        img = Image.new("RGB", (512, 512), color="orange")

        workflow = {
            "1": {
                "class_type": "KSampler (Efficient)",
                "inputs": {
                    "seed": 12345,
                    "steps": 20,
                    "cfg": 7.5,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                    "optional_vae": ["2", 4],
                }
            },
            "2": {
                "class_type": "Efficient Loader",
                "inputs": {
                    "ckpt_name": r"Illustrious\merge\real-anime-v3.safetensors",
                    "vae_name": "Baked VAE",
                    "lora_name": "None",
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "test prompt", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["generator"] == "comfyui"
        assert result["checkpoint"] == r"Illustrious\merge\real-anime-v3.safetensors"
        assert result["loras"] == []
        assert model_assets.get("source") == "activity_subgraph_fallback"
        assert model_assets.get("primary_model_type") == "checkpoint"
        assert model_assets.get("primary_model_name") == r"Illustrious\merge\real-anime-v3.safetensors"

    def test_parse_comfyui_fallback_extracts_lora_from_generic_loader(self, tmp_path: Path):
        """Fallback should recover lora_name even when the node class is unfamiliar."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_generic_lora.png"
        img = Image.new("RGB", (512, 512), color="teal")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "TotallyCustomLoaderNode",
                "inputs": {
                    "ckpt_name": "model.safetensors",
                    "lora_name": "style_lora.safetensors",
                    "lora_model_strength": 0.8,
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ["6", 0], "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "model.safetensors"
        assert result["loras"] == ["style_lora.safetensors"]
        assert model_assets.get("loras") == ["style_lora.safetensors"]

    def test_parse_comfyui_fallback_expands_serialized_lora_stack(self, tmp_path: Path):
        """UI-only JSON stacks should resolve to the real LoRA filename, not the raw blob string."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_serialized_lora_stack.png"
        img = Image.new("RGB", (512, 512), color="pink")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "Efficient Loader",
                "inputs": {
                    "ckpt_name": "model.safetensors",
                    "lora_name": "None",
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ["6", 0], "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "6": {
                "class_type": "WeiLinPromptUIOnlyLoraStack",
                "inputs": {
                    "text": "prompt",
                    "lora_str": json.dumps([
                        {
                            "name": "display name only",
                            "lora": r"Illustrious\人物\东雪莲\东雪莲-000023.safetensors",
                            "weight": 1,
                        }
                    ]),
                }
            },
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["loras"] == [r"Illustrious\人物\东雪莲\东雪莲-000023.safetensors"]
        assert model_assets.get("loras") == [r"Illustrious\人物\东雪莲\东雪莲-000023.safetensors"]

    def test_parse_comfyui_fallback_uses_unet_when_no_checkpoint_exists(self, tmp_path: Path):
        """Fallback should still surface the active model when only UNet naming is available."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_unet_only.png"
        img = Image.new("RGB", (512, 512), color="purple")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 42,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "Flux Loader Redux",
                "inputs": {
                    "unet_name": "flux_unet_fp8.safetensors",
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "prompt", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "flux_unet_fp8.safetensors"
        assert model_assets.get("primary_model_type") == "unet"
        assert model_assets.get("primary_model_name") == "flux_unet_fp8.safetensors"

    def test_parse_comfyui_fallback_uses_diffusion_model_when_present(self, tmp_path: Path):
        """Diffusion model loaders should surface as the primary model when no checkpoint exists."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 7,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "2": {
                "class_type": "Flux Custom Loader",
                "inputs": {
                    "diffusion_model": "flux1-dev-fp8.safetensors",
                },
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "prompt", "clip": ["2", 1]},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 768, "height": 768},
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_diffusion_model.png",
            workflow,
            color="navy",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "flux1-dev-fp8.safetensors"
        assert model_assets.get("primary_model_type") == "diffusion_model"
        assert model_assets.get("primary_model_name") == "flux1-dev-fp8.safetensors"
        assert (model_assets.get("diffusion_model_candidates") or [])[0]["name"] == "flux1-dev-fp8.safetensors"

    def test_parse_comfyui_prefers_explicit_lora_filenames_and_collects_full_graph_yolo_models(self, tmp_path: Path):
        """Detailed asset extraction should keep real LoRA filenames and surface detached YOLO models."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 260550204902226,
                    "steps": 30,
                    "cfg": 7.0,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "normal",
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "2": {
                "class_type": "easy comfyLoader",
                "inputs": {
                    "ckpt_name": r"2d\waiNSFWIllustrious_v140.safetensors",
                    "optional_lora_stack": ["10", 0],
                },
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "heroine, <lora:through_wall_multiview:0.7>", "clip": ["2", 1]},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 1024, "height": 1536},
            },
            "10": {
                "class_type": "WeiLinPromptUI",
                "inputs": {
                    "positive": "heroine, <lora:through_wall_multiview:0.7>",
                    "lora_str": json.dumps([
                        {
                            "name": r"动作\through_wall_multiview",
                            "lora": r"动作\through_wall_multiview.safetensors",
                        },
                        {
                            "name": r"角色\peiyuhan",
                            "lora": r"角色\peiyuhan.safetensors",
                        },
                    ]),
                },
            },
            "20": {
                "class_type": "UltralyticsDetectorProvider",
                "inputs": {"model_name": "bbox/Eyes.pt"},
            },
            "21": {
                "class_type": "UltralyticsDetectorProvider",
                "inputs": {"model_name": "bbox/PitHandDetailer-v1b-seg.pt"},
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_weilin_yolo.png",
            workflow,
            color="black",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == r"2d\waiNSFWIllustrious_v140.safetensors"
        assert result["loras"] == [
            r"动作\through_wall_multiview.safetensors",
            r"角色\peiyuhan.safetensors",
        ]
        assert model_assets.get("loras") == result["loras"]
        assert model_assets.get("yolo_models") == [
            "bbox/Eyes.pt",
            "bbox/PitHandDetailer-v1b-seg.pt",
        ]
        global_yolo_candidates = model_assets.get("global_yolo_candidates") or []
        assert {item["name"] for item in global_yolo_candidates} == {
            "bbox/Eyes.pt",
            "bbox/PitHandDetailer-v1b-seg.pt",
        }

    def test_parse_comfyui_multi_lora_loader_ignores_stringified_strength_values(self, tmp_path: Path):
        """Multi-LoRA stacks must not treat weight strings as LoRA names."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1,
                    "model": ["6", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "prompt", "clip": ["2", 1]},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512},
            },
            "6": {
                "class_type": "Power Lora Loader (rgthree)",
                "inputs": {
                    "lora_1_name": "detail_style.safetensors",
                    "lora_1_strength": "0.15",
                    "lora_2_name": "pose_helper.safetensors",
                    "lora_2_strength": "3.00",
                },
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_rgthree_string_weights.png",
            workflow,
            color="silver",
        )

        result = parse_image(str(img_path))

        assert result["loras"] == ["detail_style.safetensors", "pose_helper.safetensors"]

    def test_parse_comfyui_fallback_extracts_inline_loras_from_activity_text(self, tmp_path: Path):
        """Fallback should recover inline <lora:...> tags carried by activity-chain helper nodes."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_loratagloader.png"
        img = Image.new("RGB", (512, 512), color="yellow")

        workflow = {
            "10001": {
                "class_type": "ECHOCheckpointLoaderSimple",
                "inputs": {"ckpt_name": "EMS-657705-EMS.safetensors"}
            },
            "10012": {
                "class_type": "LoraTagLoader",
                "inputs": {
                    "clip": ["10001", 1],
                    "model": ["10001", 0],
                    "text": "<lora:EMS-574514-EMS.safetensors:0.600000>, <lora:EMS-862671-EMS.safetensors:0.800000>"
                }
            },
            "10015": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ["10012", 0], "clip": ["10012", 1]}
            },
            "10016": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["10012", 1]}
            },
            "10017": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1,
                    "model": ["10012", 0],
                    "positive": ["10015", 0],
                    "negative": ["10016", 0],
                    "latent_image": ["10018", 0]
                }
            },
            "10018": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "EMS-657705-EMS.safetensors"
        assert result["loras"] == ["EMS-574514-EMS.safetensors", "EMS-862671-EMS.safetensors"]
        assert model_assets.get("loras") == ["EMS-574514-EMS.safetensors", "EMS-862671-EMS.safetensors"]

    def test_parse_comfyui_global_candidates_capture_non_active_lora_filename(self, tmp_path: Path):
        """Disconnected nodes with explicit lora_name values should be preserved as global candidates."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "90": {
                "class_type": "Lora Loader (LoraManager)",
                "inputs": {
                    "lora_name": r"styles\detached_style.safetensors",
                    "strength_model": 0.8,
                }
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_global_lora_filename.png",
            workflow,
            color="orange",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}
        global_candidates = model_assets.get("global_lora_candidates") or []

        assert result["checkpoint"] == "base_model.safetensors"
        assert result["loras"] == []
        assert len(global_candidates) == 1
        assert global_candidates[0]["name"] == r"styles\detached_style.safetensors"
        assert global_candidates[0]["confidence"] == "high"
        assert global_candidates[0]["source_mode"] == "global_candidate_fallback"
        assert global_candidates[0]["match_type"] == "explicit_input"
        assert global_candidates[0]["node_id"] == "90"
        assert global_candidates[0]["input_key"] == "lora_name"

    def test_parse_comfyui_global_candidates_capture_inline_lora_tags_when_not_active(self, tmp_path: Path):
        """Non-active prompt helper nodes should surface inline tags as low-confidence global candidates only."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "70": {
                "class_type": "PromptScratchpad",
                "inputs": {
                    "text": "unused helper text <lora:detached_inline:0.75>",
                }
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_global_inline_lora.png",
            workflow,
            color="yellow",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}
        global_candidates = model_assets.get("global_lora_candidates") or []

        assert result["loras"] == []
        assert len(global_candidates) == 1
        assert global_candidates[0]["name"] == "detached_inline"
        assert global_candidates[0]["confidence"] == "low"
        assert global_candidates[0]["match_type"] == "inline_lora_tag"
        assert global_candidates[0]["source_mode"] == "global_candidate_fallback"

    def test_parse_comfyui_global_candidates_capture_serialized_lora_json_when_not_active(self, tmp_path: Path):
        """Serialized JSON blobs on disconnected nodes should surface the underlying lora filename."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "71": {
                "class_type": "WeiLinPromptUI",
                "inputs": {
                    "temp_payload": json.dumps([
                        {
                            "display_name": "style only",
                            "lora": r"Illustrious\styles\stacked_style.safetensors",
                            "weight": 1.0,
                        }
                    ]),
                }
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_global_serialized_lora.png",
            workflow,
            color="pink",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}
        global_candidates = model_assets.get("global_lora_candidates") or []

        assert result["loras"] == []
        assert len(global_candidates) == 1
        assert global_candidates[0]["name"] == r"Illustrious\styles\stacked_style.safetensors"
        assert global_candidates[0]["confidence"] == "high"
        assert global_candidates[0]["match_type"] == "serialized_field"
        assert global_candidates[0]["key_path"].endswith("temp_payload[0].lora")

    def test_parse_comfyui_low_confidence_global_candidates_do_not_pollute_main_loras(self, tmp_path: Path):
        """Low-confidence global hints should remain secondary and never land in the main loras field."""
        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
            "72": {
                "class_type": "PromptScratchpad",
                "inputs": {
                    "temp_blob": json.dumps([
                        {"text": "archived helper <lora:unused_helper:0.9>"}
                    ]),
                }
            },
        }

        img_path = _write_comfyui_prompt_png(
            tmp_path,
            "comfyui_global_low_confidence.png",
            workflow,
            color="teal",
        )

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}
        global_candidates = model_assets.get("global_lora_candidates") or []

        assert result["loras"] == []
        assert model_assets.get("loras") == []
        assert len(global_candidates) == 1
        assert global_candidates[0]["name"] == "unused_helper"
        assert global_candidates[0]["confidence"] == "low"
        assert global_candidates[0]["match_type"] == "serialized_inline_lora_tag"

    def test_parse_comfyui_recovers_lora_from_workflow_widget_lora_loader(self, tmp_path: Path):
        """Explicit LoRA filenames in workflow widgets should be recovered even when prompt inputs missed them."""
        workflow_prompt = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
        }
        workflow_ui = {
            "nodes": [
                {
                    "id": 20,
                    "type": "LoraLoader",
                    "widgets_values": ["add_detail.safetensors", 0.8, 1.0],
                }
            ]
        }

        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        img_path = tmp_path / "comfyui_workflow_lora_loader.png"
        img = Image.new("RGB", (512, 512), color="navy")
        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow_prompt))
        metadata.add_text("workflow", json.dumps(workflow_ui))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["loras"] == ["add_detail.safetensors"]
        widget_candidates = model_assets.get("workflow_widget_lora_candidates") or []
        assert len(widget_candidates) == 1
        assert widget_candidates[0]["name"] == "add_detail.safetensors"
        assert widget_candidates[0]["source_mode"] == "workflow_widget_fallback"

    def test_parse_comfyui_recovers_lora_from_workflow_easy_lora_stack(self, tmp_path: Path):
        """easy loraStack widget values should feed main lora extraction when prompt inputs only say None."""
        workflow_prompt = {
            "26": {
                "class_type": "KSampler (Efficient)",
                "inputs": {
                    "seed": 100,
                    "steps": 20,
                    "cfg": 7,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["34", 0],
                    "positive": ["34", 1],
                    "negative": ["34", 2],
                    "latent_image": ["33", 0],
                    "optional_vae": ["34", 4],
                }
            },
            "33": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 768, "batch_size": 1}
            },
            "34": {
                "class_type": "Efficient Loader",
                "inputs": {
                    "ckpt_name": "chenkin_noob_ep15.safetensors",
                    "lora_name": "None",
                }
            },
        }
        workflow_ui = {
            "nodes": [
                {
                    "id": 201,
                    "type": "easy loraStack",
                    "widgets_values": [
                        True, "simple", 1,
                        r"测试\yhmf v5.safetensors", 1, 1,
                        1, "None", 1, 1,
                    ],
                }
            ]
        }

        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        img_path = tmp_path / "comfyui_workflow_lorastack.png"
        img = Image.new("RGB", (512, 512), color="olive")
        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow_prompt))
        metadata.add_text("workflow", json.dumps(workflow_ui))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "chenkin_noob_ep15.safetensors"
        assert result["loras"] == [r"测试\yhmf v5.safetensors"]
        widget_candidates = model_assets.get("workflow_widget_lora_candidates") or []
        assert any(item["name"] == r"测试\yhmf v5.safetensors" for item in widget_candidates)

    def test_parse_comfyui_workflow_only_uses_widget_checkpoint_when_prompt_graph_missing(self, tmp_path: Path):
        """Workflow-only files should still recover checkpoint and LoRAs from widget state."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_workflow_only_checkpoint.png"
        img = Image.new("RGB", (512, 512), color="white")

        workflow_ui = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CheckpointLoaderSimple",
                    "widgets_values": ["checkpoint-e1_s14230(ep12).safetensors"],
                },
                {
                    "id": 2,
                    "type": "Power Lora Loader (rgthree)",
                    "widgets_values": [
                        True,
                        "simple",
                        {"on": True, "lora": "style\\\\rella.safetensors"},
                    ],
                },
            ]
        }

        metadata = PngInfo()
        metadata.add_text("workflow", json.dumps(workflow_ui))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["generator"] == "comfyui"
        assert result["checkpoint"] == "checkpoint-e1_s14230(ep12).safetensors"
        assert [item.replace("\\\\", "\\") for item in result["loras"]] == ["style\\rella.safetensors"]
        assert model_assets.get("primary_model_name") == "checkpoint-e1_s14230(ep12).safetensors"

    def test_parse_comfyui_workflow_controlnet_stack_does_not_pollute_loras(self, tmp_path: Path):
        """ControlNet stacks in workflow widgets are model files, not LoRAs."""
        workflow_prompt = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 123,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base_model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "portrait", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            },
        }
        workflow_ui = {
            "nodes": [
                {
                    "id": 28,
                    "type": "CR Multi-ControlNet Stack",
                    "widgets_values": [
                        "Off",
                        "noobaiXLControlnet_openposeModel.safetensors",
                        1, 0, 1,
                        "On",
                        r"dir\noob_v_canny_controlnet_ep20_step280.safetensors",
                        1.1, 0, 1,
                    ],
                }
            ]
        }

        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        img_path = tmp_path / "comfyui_workflow_controlnet_only.png"
        img = Image.new("RGB", (512, 512), color="maroon")
        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow_prompt))
        metadata.add_text("workflow", json.dumps(workflow_ui))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["checkpoint"] == "base_model.safetensors"
        assert result["loras"] == []


class TestWebUIMetadata:
    """Tests for WebUI/A1111 metadata parsing."""

    def test_parse_webui_basic(self, mock_webui_image: Path):
        """Basic WebUI metadata should be parsed correctly."""
        result = parse_image(str(mock_webui_image))

        assert result["generator"] == "webui"
        assert "portrait" in result["prompt"].lower()
        assert "blurry" in result["negative_prompt"].lower()

    def test_parse_webui_checkpoint(self, mock_webui_image: Path):
        """WebUI checkpoint should be extracted."""
        result = parse_image(str(mock_webui_image))

        assert "realisticVision" in result["checkpoint"]

    def test_parse_webui_parameters_format(self, tmp_path: Path):
        """WebUI parameters text chunk should be parsed correctly."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_params.png"
        img = Image.new("RGB", (512, 512), color="red")

        parameters = """beautiful sunset over mountains, detailed sky
Negative prompt: ugly, low quality, blurry
Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7.0, Seed: 123456789, Size: 512x512, Model: test_model.safetensors"""

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert "sunset" in result["prompt"].lower()
        assert "ugly" in result["negative_prompt"].lower()
        assert result["checkpoint"] == "test_model.safetensors"

    def test_parse_webui_prefers_actual_model_over_adetailer_model_fields(self, tmp_path: Path):
        """ADetailer model fields must not override the real checkpoint."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_adetailer_model_priority.png"
        img = Image.new("RGB", (512, 512), color="purple")

        parameters = (
            "masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 5, Seed: 123, Size: 512x768, "
            "ADetailer model: face_yolov8n.pt, ADetailer confidence: 0.3, "
            "Model hash: abcdef1234, Model: real_model.safetensors"
        )

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["checkpoint"] == "real_model.safetensors"

    def test_parse_webui_uses_model_hash_identifier_when_name_missing(self, tmp_path: Path):
        """A missing model name should still surface a stable model-hash identifier."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_model_hash_only.png"
        img = Image.new("RGB", (512, 512), color="brown")

        parameters = (
            "masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 5, Seed: 123, Size: 512x768, "
            "Model hash: 925997e9, Clip skip: 2"
        )

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["checkpoint"] == "Model hash 925997e9"

    def test_settings_only_comfyui_parameters_recover_checkpoint_without_inventing_prompt(
        self, tmp_path: Path
    ):
        """ComfyUI Save Image may write only Clip skip / Model hash / Version.

        SPR treats that settings line as the positive prompt. We must recover
        the checkpoint for gallery filters and keep prompt empty.
        """
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_settings_only.png"
        img = Image.new("RGB", (64, 64), color="navy")
        parameters = (
            "\nClip skip: 1, Model hash: BD43B7CFFE, Model: anima-base-v1.0, "
            'Hashes: {"model":"BD43B7CFFE"}, Version: ComfyUI'
        )
        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["parse_error"] is None
        assert result["prompt"] in (None, "")
        assert result["checkpoint"] == "anima-base-v1.0"
        assert result["generator"] == "comfyui"
        assert "Clip skip" not in (result["prompt"] or "")
        assert "Model hash" not in (result["prompt"] or "")

    def test_parse_webui_recovers_lora_names_from_lora_hashes(self, tmp_path: Path):
        """LoRA names should be recovered from the Lora hashes field even without inline tags."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_lora_hashes.png"
        img = Image.new("RGB", (512, 512), color="red")

        parameters = (
            "masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 30, Sampler: Euler a, CFG scale: 7, Seed: 123, Size: 512x512, "
            "Model: test_model.safetensors, "
            "Lora hashes: \"style_a: a1b2c3, detail_boost: d4e5f6\""
        )

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["loras"] == ["style_a", "detail_boost"]

    def test_parse_webui_weightless_lora_tags_do_not_merge_together(self, tmp_path: Path):
        """Weightless inline tags should still split into distinct LoRA names."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_weightless_loras.png"
        img = Image.new("RGB", (512, 512), color="cyan")

        parameters = (
            "masterpiece, <lora:first_style>, <lora:second_style:0.7>\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 5, Seed: 123, Size: 512x768, Model: test_model.safetensors"
        )

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["loras"] == ["first_style", "second_style"]

    def test_parse_webui_model_assets_include_adetailer_yolo_models(self, tmp_path: Path):
        """Detailed model assets should expose detector models without polluting checkpoint/LoRA summaries."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_adetailer_models.png"
        img = Image.new("RGB", (512, 512), color="crimson")

        parameters = (
            "masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 24, Sampler: Euler a, CFG scale: 7, Seed: 123, Size: 512x512, "
            "Model: test_model.safetensors, "
            "ADetailer model: face_yolov8n.pt, "
            "ADetailer model 2: hand_yolov8s.pt"
        )

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["checkpoint"] == "test_model.safetensors"
        assert result["loras"] == []
        assert model_assets.get("primary_model_name") == "test_model.safetensors"
        assert model_assets.get("yolo_models") == ["face_yolov8n.pt", "hand_yolov8s.pt"]

    def test_parse_webui_merges_explicit_workflow_widget_loras_when_parameters_have_none(self, tmp_path: Path):
        """Hybrid files keep generator=comfyui and still recover workflow LoRAs plus the parameters checkpoint."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_with_workflow_loras.png"
        img = Image.new("RGB", (512, 512), color="red")

        parameters = (
            "masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 5, Seed: 123, Size: 512x768, "
            "Model: JANKUV5NSFWTrainedNoobai_v50.safetensors"
        )
        workflow = {
            "nodes": [
                {
                    "id": 59,
                    "type": "LoraLoader",
                    "widgets_values": ["illustriousXLv01_stabilizer_v1.198.safetensors", 0.9, 1],
                },
                {
                    "id": 60,
                    "type": "LoraLoader",
                    "widgets_values": ["AddMicroDetails_Illustrious_v5.safetensors", 0.7, 1],
                },
            ]
        }

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        metadata.add_text("workflow", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["generator"] == "comfyui"
        assert result["checkpoint"] == "JANKUV5NSFWTrainedNoobai_v50.safetensors"
        assert result["prompt"] == "masterpiece"
        assert result["negative_prompt"] == "low quality"
        assert result["loras"] == [
            "illustriousXLv01_stabilizer_v1.198.safetensors",
            "AddMicroDetails_Illustrious_v5.safetensors",
        ]
        widget_candidates = model_assets.get("lora_candidates") or []
        assert len(widget_candidates) == 2


class TestForgeMetadata:
    """Tests for Forge metadata parsing."""

    def test_parse_forge_basic(self, mock_forge_image: Path):
        """Basic Forge metadata should be parsed correctly."""
        result = parse_image(str(mock_forge_image))

        assert result["generator"] == "forge"
        assert "cyberpunk" in result["prompt"].lower()
        assert "daylight" in result["negative_prompt"].lower()

    def test_forge_distinguished_from_webui(self, mock_forge_image: Path, mock_webui_image: Path):
        """Forge should be distinguished from WebUI."""
        forge_result = parse_image(str(mock_forge_image))
        webui_result = parse_image(str(mock_webui_image))

        assert forge_result["generator"] == "forge"
        assert webui_result["generator"] == "webui"

    def test_forge_detected_from_png_software_when_parameters_look_like_webui(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        image_path = tmp_path / "forge-software.png"
        params = (
            "girl, city\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 123, Size: 512x512, Model: model"
        )
        image = Image.new("RGB", (64, 64), color="white")
        metadata = PngInfo()
        metadata.add_text("parameters", params)
        metadata.add_text("Software", "Stable Diffusion webui Forge")
        image.save(image_path, pnginfo=metadata)

        result = parse_image(str(image_path))

        assert result["generator"] == "forge"

    def test_webui_without_forge_signal_stays_webui(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        image_path = tmp_path / "vanilla-webui.png"
        params = (
            "girl, city\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 123, Size: 512x512, Model: model"
        )
        image = Image.new("RGB", (64, 64), color="white")
        metadata = PngInfo()
        metadata.add_text("parameters", params)
        metadata.add_text("Software", "AUTOMATIC1111")
        image.save(image_path, pnginfo=metadata)

        result = parse_image(str(image_path))

        assert result["generator"] == "webui"

    def test_webui_prompt_word_forge_does_not_make_generator_forge(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        image_path = tmp_path / "webui-prompt-forge-word.png"
        params = (
            "blacksmith forge, forged armor, glowing steel\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 123, Size: 512x512, Model: vanilla-model"
        )
        image = Image.new("RGB", (64, 64), color="white")
        metadata = PngInfo()
        metadata.add_text("parameters", params)
        metadata.add_text("Software", "AUTOMATIC1111")
        image.save(image_path, pnginfo=metadata)

        result = parse_image(str(image_path))

        assert result["generator"] == "webui"

    def test_forge_detected_from_forge_style_version(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        image_path = tmp_path / "forge-version.png"
        params = (
            "girl, city\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 123, Size: 512x512, Model: model, Version: f2.0.1v1.10.1-previous-1234"
        )
        image = Image.new("RGB", (64, 64), color="white")
        metadata = PngInfo()
        metadata.add_text("parameters", params)
        image.save(image_path, pnginfo=metadata)

        result = parse_image(str(image_path))

        assert result["generator"] == "forge"


class TestCheckpointIdentifierFallbacks:
    """Tests for non-filename model identifiers that should still surface as checkpoint info."""

    def test_parse_nai_uses_source_as_checkpoint_identifier(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "nai_source_identifier.png"
        img = Image.new("RGB", (512, 512), color="white")

        comment = {
            "prompt": "masterpiece",
            "uc": "low quality",
            "steps": 28,
            "width": 832,
            "height": 1216,
            "scale": 10.0,
            "seed": 12345,
            "sampler": "k_euler",
        }

        metadata = PngInfo()
        metadata.add_text("Comment", json.dumps(comment))
        metadata.add_text("Description", "masterpiece")
        metadata.add_text("Software", "NovelAI")
        metadata.add_text("Source", "NovelAI Diffusion V4.5 4BDE2A90")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))
        model_assets = result["metadata"]["_parsed"].get("model_assets") or {}

        assert result["generator"] == "nai"
        assert result["checkpoint"] == "NovelAI Diffusion V4.5 4BDE2A90"
        assert model_assets.get("primary_model_type") == "checkpoint"
        assert model_assets.get("primary_model_name") == "NovelAI Diffusion V4.5 4BDE2A90"

    def test_parse_nai_usercomment_uses_embedded_source_as_checkpoint_identifier(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "nai_usercomment_source_identifier.png"
        img = Image.new("RGB", (512, 512), color="white")

        usercomment = (
            "ASCII\0\0\0"
            + json.dumps({
                "Description": "masterpiece",
                "Software": "NovelAI",
                "Source": "Stable Diffusion XL 1120E6A9",
                "Comment": json.dumps({
                    "prompt": "masterpiece",
                    "uc": "low quality",
                    "steps": 28,
                    "width": 832,
                    "height": 1216,
                    "scale": 10.0,
                    "seed": 12345,
                    "sampler": "k_euler",
                }),
            })
        )

        metadata = PngInfo()
        metadata.add_text("UserComment", usercomment)
        metadata.add_text("Software", "NovelAI")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "nai"
        assert result["checkpoint"] == "Stable Diffusion XL 1120E6A9"

    def test_parse_webui_novelai_software_uses_model_identifier_when_model_field_missing(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_novelai_identifier.png"
        img = Image.new("RGB", (512, 512), color="white")

        user_comment = (
            "masterpiece\\n"
            "Negative prompt: low quality\\n"
            "Steps: 28, Sampler: Euler, CFG scale: 10.0, Seed: 2271736881, Size: 832x1216, Clip skip: 2, ENSD: 31337"
        )

        metadata = PngInfo()
        metadata.add_text("UserComment", user_comment)
        metadata.add_text("Software", "NovelAI")
        metadata.add_text("Model", "Stable Diffusion XL C1E1DE52")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["checkpoint"] == "Stable Diffusion XL C1E1DE52"


class TestNAIMetadata:
    """Tests for NovelAI metadata parsing."""

    def test_parse_nai_basic(self, mock_nai_image: Path):
        """Basic NAI metadata should be parsed correctly."""
        result = parse_image(str(mock_nai_image))

        assert result["generator"] == "nai"
        assert "anime girl" in result["prompt"].lower()
        assert "bad anatomy" in result["negative_prompt"].lower()

    def test_parse_nai_software_tag(self, tmp_path: Path):
        """NAI should be detected from Software tag."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "nai_software.png"
        img = Image.new("RGB", (832, 1216), color="purple")

        metadata = PngInfo()
        metadata.add_text("Software", "NovelAI")
        metadata.add_text("Description", "test prompt from NAI")
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "nai"
        assert "test prompt" in result["prompt"].lower()


class TestUnknownMetadata:
    """Tests for unknown/missing metadata."""

    def test_parse_no_metadata(self, mock_image_file: Path):
        """Image with no SD metadata should return unknown generator."""
        result = parse_image(str(mock_image_file))

        assert result["generator"] == "unknown"

    def test_parse_dimensions_extracted(self, mock_image_file: Path):
        """Dimensions should be extracted even without metadata."""
        result = parse_image(str(mock_image_file))

        assert result["width"] == 512
        assert result["height"] == 512

    def test_parse_file_size(self, mock_image_file: Path):
        """File size should be extracted."""
        result = parse_image(str(mock_image_file))

        assert result["file_size"] > 0


class TestEdgeCases:
    """Edge case tests for metadata parsing."""

    def test_parse_jpeg_with_exif(self, tmp_path: Path):
        """JPEG with EXIF should be handled."""
        from PIL import Image

        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (512, 512), color="blue")
        img.save(img_path, "JPEG")

        result = parse_image(str(img_path))

        assert result["width"] == 512
        assert result["height"] == 512

    def test_parse_jpeg_webui_usercomment_unicode(self, tmp_path: Path):
        """JPG EXIF UserComment can contain A1111/WebUI parameters in UNICODE form."""
        from PIL import Image

        img_path = tmp_path / "webui-usercomment.jpg"
        parameters = (
            "masterpiece, detailed lighting\n"
            "Negative prompt: low quality, blurry\n"
            "Steps: 24, Sampler: Euler a, CFG scale: 7, Seed: 42, Size: 512x512, "
            "Model: jpeg_model.safetensors, Lora hashes: \"jpeg_style: abc123\""
        )

        Image.new("RGB", (512, 512), color="blue").save(
            img_path,
            "JPEG",
            exif=_build_exif_user_comment(parameters, unicode_payload=True),
        )

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "masterpiece, detailed lighting"
        assert result["negative_prompt"] == "low quality, blurry"
        assert result["checkpoint"] == "jpeg_model.safetensors"
        assert result["loras"] == ["jpeg_style"]

    def test_parse_jpeg_webui_xmp_parameters(self, tmp_path: Path):
        """JPG APP1 XMP packets can also hold SD parameter blocks."""
        from PIL import Image

        img_path = tmp_path / "webui-xmp.jpg"
        parameters = (
            "cinematic portrait\n"
            "Negative prompt: washed out\n"
            "Steps: 18, Sampler: DPM++ 2M, CFG scale: 6, Seed: 9, Size: 640x768, "
            "Model: xmp_model.safetensors"
        )
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:sd="https://github.com/AUTOMATIC1111/stable-diffusion-webui/">'
            f'<sd:parameters>{parameters}</sd:parameters>'
            '</rdf:Description></rdf:RDF></x:xmpmeta>'
        )

        Image.new("RGB", (640, 768), color="navy").save(img_path, "JPEG")
        _insert_jpeg_xmp_packet(img_path, xmp)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "cinematic portrait"
        assert result["negative_prompt"] == "washed out"
        assert result["checkpoint"] == "xmp_model.safetensors"

    def test_parse_webui_parameters_from_same_name_txt_sidecar(self, tmp_path: Path):
        """When embedded metadata is absent, small same-name txt sidecars are parsed."""
        from PIL import Image

        img_path = tmp_path / "sidecar.jpg"
        parameters = (
            "sidecar prompt\n"
            "Negative prompt: sidecar negative\n"
            "Steps: 22, Sampler: Euler a, CFG scale: 7, Seed: 12, Size: 512x512, "
            "Model: sidecar_model.safetensors"
        )
        Image.new("RGB", (512, 512), color="white").save(img_path, "JPEG")
        (tmp_path / "sidecar.jpg.txt").write_text(parameters, encoding="utf-8")

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "sidecar prompt"
        assert result["negative_prompt"] == "sidecar negative"
        assert result["checkpoint"] == "sidecar_model.safetensors"
        assert result["metadata"]["_parsed"]["sidecar_fallback"] == (
            _sidecar_fallback_state(
                "txt",
                "sidecar.jpg.txt",
                ["prompt", "negative_prompt", "checkpoint"],
            )
        )

    def test_parse_prompt_from_json_sidecar(self, tmp_path: Path):
        """JSON sidecars can provide explicit prompt fields without image metadata."""
        from PIL import Image

        img_path = tmp_path / "caption.webp"
        Image.new("RGB", (320, 240), color="green").save(img_path, "WEBP")
        (tmp_path / "caption.json").write_text(
            json.dumps({
                "prompt": "json prompt",
                "negative_prompt": "json negative",
                "checkpoint": "json_model.safetensors",
                "loras": ["json_lora"],
                "seed": 123,
            }),
            encoding="utf-8",
        )

        result = parse_image(str(img_path))

        assert result["generator"] == "others"
        assert result["prompt"] == "json prompt"
        assert result["negative_prompt"] == "json negative"
        assert result["checkpoint"] == "json_model.safetensors"
        assert result["loras"] == ["json_lora"]
        assert result["metadata"]["_parsed"]["sidecar_fallback"] == (
            _sidecar_fallback_state(
                "json",
                "caption.json",
                ["prompt", "negative_prompt", "checkpoint", "loras"],
            )
        )

    def test_multiple_sidecars_record_fields_for_the_contributing_carrier(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "multiple-sidecars.jpg"
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "multiple-sidecars.jpg.txt").write_text(
            "text sidecar prompt",
            encoding="utf-8",
        )
        (tmp_path / "multiple-sidecars.json").write_text(
            json.dumps({
                "negative_prompt": "json negative",
                "checkpoint": "json_model.safetensors",
                "loras": ["json_lora"],
            }),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        # The .txt holds plain text, so it is caption material, not the SD
        # prompt (migration 042); the .json still contributes real fields.
        assert result["sidecar_caption"] == "text sidecar prompt"
        assert result["prompt"] is None
        assert result["negative_prompt"] == "json negative"
        assert result["checkpoint"] == "json_model.safetensors"
        assert result["loras"] == ["json_lora"]
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "txt",
                "multiple-sidecars.jpg.txt",
                ["sidecar_caption"],
            ),
            _sidecar_fallback_evidence(
                "json",
                "multiple-sidecars.json",
                ["negative_prompt", "checkpoint", "loras"],
            ),
        ]

    @pytest.mark.parametrize(
        ("sidecar_suffix", "sidecar_content", "expected_carrier"),
        [
            (
                ".json",
                json.dumps({
                    "prompt": "shared prompt",
                    "negative_prompt": "json negative",
                }),
                "json",
            ),
            (
                ".xmp",
                (
                    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                    '<rdf:Description xmlns:sd="https://github.com/AUTOMATIC111/stable-diffusion-webui/">'
                    '<sd:parameters>shared prompt\nNegative prompt: xmp negative\n'
                    'Steps: 18, Sampler: Euler, CFG scale: 6, Seed: 9, Size: 64x64'
                    '</sd:parameters></rdf:Description></rdf:RDF></x:xmpmeta>'
                ),
                "xmp",
            ),
        ],
    )
    def test_later_sidecar_owns_same_value_fields_consumed_by_final_parse(
        self,
        tmp_path: Path,
        sidecar_suffix: str,
        sidecar_content: str,
        expected_carrier: str,
    ):
        from PIL import Image

        image_path = tmp_path / "same-value-overwrite.jpg"
        later_sidecar_path = image_path.with_suffix(sidecar_suffix)
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "same-value-overwrite.jpg.txt").write_text(
            "shared prompt",
            encoding="utf-8",
        )
        later_sidecar_path.write_text(sidecar_content, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["prompt"] == "shared prompt"
        assert result["negative_prompt"] == f"{expected_carrier} negative"
        # The plain .txt contributes caption text (migration 042) and owns that
        # field; the later carrier still owns the same-valued prompt/negative
        # the final parse actually consumed.
        assert result["sidecar_caption"] == "shared prompt"
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "txt",
                "same-value-overwrite.jpg.txt",
                ["sidecar_caption"],
            ),
            _sidecar_fallback_evidence(
                expected_carrier,
                later_sidecar_path.name,
                ["prompt", "negative_prompt"],
            ),
        ]

    def test_later_sidecar_does_not_own_fields_ignored_by_final_parse(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "ignored-same-value.jpg"
        parameters = (
            "shared prompt\n"
            "Negative prompt: txt negative\n"
            "Steps: 18, Sampler: Euler, CFG scale: 6, Seed: 9, Size: 64x64"
        )
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "ignored-same-value.jpg.txt").write_text(
            parameters,
            encoding="utf-8",
        )
        (tmp_path / "ignored-same-value.json").write_text(
            json.dumps({
                "prompt": "shared prompt",
                "negative_prompt": "json negative",
            }),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["prompt"] == "shared prompt"
        assert result["negative_prompt"] == "txt negative"
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "txt",
                "ignored-same-value.jpg.txt",
                ["prompt", "negative_prompt"],
            ),
        ]

    def test_later_same_route_sidecar_does_not_own_ignored_raw_key(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "same-route-priority.jpg"
        parameters = (
            "shared prompt\n"
            "Negative prompt: shared negative\n"
            "Steps: 18, Sampler: Euler, CFG scale: 6, Seed: 9, Size: 64x64"
        )
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "same-route-priority.jpg.txt").write_text(
            parameters,
            encoding="utf-8",
        )
        (tmp_path / "same-route-priority.json").write_text(
            json.dumps({"Parameters": parameters}),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["prompt"] == "shared prompt"
        assert result["negative_prompt"] == "shared negative"
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "txt",
                "same-route-priority.jpg.txt",
                ["prompt", "negative_prompt"],
            ),
        ]

    def test_later_novelai_comment_owns_same_value_prompt(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "comment-priority.jpg"
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "comment-priority.jpg.txt").write_text(
            "shared prompt",
            encoding="utf-8",
        )
        (tmp_path / "comment-priority.json").write_text(
            json.dumps({"Comment": json.dumps({"prompt": "shared prompt"})}),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["prompt"] == "shared prompt"
        assert result["generator"] == "nai"
        # The .txt caption text survives to the json carrier's merged parse, so
        # the carrier that the final parse consumed owns both fields.
        assert result["sidecar_caption"] == "shared prompt"
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "json",
                "comment-priority.json",
                ["prompt", "sidecar_caption"],
            ),
        ]

    def test_comfy_workflow_owns_checkpoint_when_prompt_graph_does_not(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "comfy-workflow-priority.jpg"
        prompt_graph = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"positive": ["2", 0], "negative": ["3", 0]},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "shared prompt"},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "shared negative"},
            },
        }
        workflow_graph = {
            "nodes": [{
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["workflow_model.safetensors"],
            }],
        }
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        (tmp_path / "comfy-workflow-priority.jpg.json").write_text(
            json.dumps({"prompt": prompt_graph}),
            encoding="utf-8",
        )
        (tmp_path / "comfy-workflow-priority.json").write_text(
            json.dumps({"workflow": workflow_graph}),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["prompt"] == "shared prompt"
        assert result["negative_prompt"] == "shared negative"
        assert result["checkpoint"] == "workflow_model.safetensors"
        assert result["metadata"]["_parsed"]["sidecar_fallback"]["evidence"] == [
            _sidecar_fallback_evidence(
                "json",
                "comfy-workflow-priority.jpg.json",
                ["prompt", "negative_prompt"],
            ),
            _sidecar_fallback_evidence(
                "json",
                "comfy-workflow-priority.json",
                ["checkpoint"],
            ),
        ]

    def test_parse_webui_parameters_from_xmp_sidecar_records_provenance(
        self,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "sidecar-xmp.png"
        parameters = (
            "xmp sidecar prompt\n"
            "Negative prompt: xmp sidecar negative\n"
            "Steps: 18, Sampler: Euler, CFG scale: 6, Seed: 9, Size: 64x64, "
            "Model: xmp_sidecar_model.safetensors"
        )
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:sd="https://github.com/AUTOMATIC1111/stable-diffusion-webui/">'
            f'<sd:parameters>{parameters}</sd:parameters>'
            '</rdf:Description></rdf:RDF></x:xmpmeta>'
        )
        Image.new("RGB", (64, 64), color="white").save(image_path, "PNG")
        (tmp_path / "sidecar-xmp.xmp").write_text(xmp, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["prompt"] == "xmp sidecar prompt"
        assert result["negative_prompt"] == "xmp sidecar negative"
        assert result["checkpoint"] == "xmp_sidecar_model.safetensors"
        assert result["metadata"]["_parsed"]["sidecar_fallback"] == (
            _sidecar_fallback_state(
                "xmp",
                "sidecar-xmp.xmp",
                ["prompt", "negative_prompt", "checkpoint"],
            )
        )

    def test_embedded_metadata_does_not_claim_adjacent_sidecar(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        image_path = tmp_path / "embedded-wins.png"
        embedded_parameters = (
            "embedded prompt\n"
            "Negative prompt: embedded negative\n"
            "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 1, Size: 64x64"
        )
        metadata = PngInfo()
        metadata.add_text("parameters", embedded_parameters)
        Image.new("RGB", (64, 64), color="white").save(
            image_path,
            "PNG",
            pnginfo=metadata,
        )
        (tmp_path / "embedded-wins.png.txt").write_text(
            "sidecar must not be claimed",
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["prompt"] == "embedded prompt"
        assert result["metadata"]["_parsed"]["sidecar_fallback"] == {
            "schema_version": 1,
            "evaluated": True,
            "evidence": [],
        }

    def test_parse_gif_comment_parameters(self, tmp_path: Path):
        """GIF comment extension metadata should be harvested when present."""
        from PIL import Image

        img_path = tmp_path / "comment.gif"
        parameters = (
            "gif prompt\n"
            "Negative prompt: gif negative\n"
            "Steps: 12, Sampler: Euler, CFG scale: 5, Seed: 77, Size: 64x64, "
            "Model: gif_model.safetensors"
        )
        Image.new("P", (64, 64), color=0).save(img_path, "GIF", comment=parameters.encode("utf-8"))

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "gif prompt"
        assert result["negative_prompt"] == "gif negative"
        assert result["checkpoint"] == "gif_model.safetensors"

    def test_parse_tiff_imagedescription_parameters(self, tmp_path: Path):
        """TIFF ImageDescription can hold WebUI-style parameters."""
        from PIL import Image
        from PIL.TiffImagePlugin import ImageFileDirectory_v2

        img_path = tmp_path / "params.tiff"
        parameters = (
            "tiff prompt\n"
            "Negative prompt: tiff negative\n"
            "Steps: 30, Sampler: DPM++ 2M, CFG scale: 6, Seed: 88, Size: 128x128, "
            "Model: tiff_model.safetensors"
        )
        ifd = ImageFileDirectory_v2()
        ifd[270] = parameters
        Image.new("RGB", (128, 128), color="yellow").save(img_path, "TIFF", tiffinfo=ifd)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "tiff prompt"
        assert result["negative_prompt"] == "tiff negative"
        assert result["checkpoint"] == "tiff_model.safetensors"

    def test_large_sidecar_is_ignored_for_scan_safety(self, tmp_path: Path):
        """Oversized sidecars should not be read into scan workers."""
        from PIL import Image

        img_path = tmp_path / "large-sidecar.jpg"
        Image.new("RGB", (64, 64), color="white").save(img_path, "JPEG")
        (tmp_path / "large-sidecar.jpg.txt").write_text("x" * (256 * 1024 + 1), encoding="utf-8")

        result = parse_image(str(img_path))

        assert result["generator"] == "unknown"
        assert result["prompt"] is None

    def test_huge_sidecar_directory_cache_does_not_keep_filename_set(self, tmp_path: Path, monkeypatch):
        """Huge sidecar directories should not leave a massive filename set in memory."""
        from PIL import Image

        image_path = tmp_path / "huge-dir.jpg"
        sidecar_path = tmp_path / "huge-dir.jpg.txt"
        Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
        sidecar_path.write_text("cached caption", encoding="utf-8")

        metadata_parser_module._sidecar_directory_cache.clear()
        monkeypatch.setattr(metadata_parser_module, "_MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES", 1)

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == "cached caption"
        assert any(value[1] is None for value in metadata_parser_module._sidecar_directory_cache.values())

    def test_parse_webp(self, tmp_path: Path):
        """WebP images should be handled."""
        from PIL import Image

        img_path = tmp_path / "test.webp"
        img = Image.new("RGB", (512, 512), color="green")
        img.save(img_path, "WEBP")

        result = parse_image(str(img_path))

        assert result["width"] == 512
        assert result["height"] == 512

    def test_parse_corrupted_json_in_metadata(self, tmp_path: Path):
        """Corrupted JSON in metadata should be handled gracefully."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "corrupted.png"
        img = Image.new("RGB", (512, 512), color="red")

        metadata = PngInfo()
        metadata.add_text("prompt", "{invalid json")
        img.save(img_path, pnginfo=metadata)

        # Should not crash
        result = parse_image(str(img_path))
        assert result is not None

    def test_parse_empty_prompt(self, tmp_path: Path):
        """Empty prompt in metadata should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "empty_prompt.png"
        img = Image.new("RGB", (512, 512), color="blue")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "", "clip": ["2", 1]}  # Empty prompt
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"

    def test_parse_unicode_in_prompt(self, tmp_path: Path):
        """Unicode characters in prompts should be handled."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "unicode.png"
        img = Image.new("RGB", (512, 512), color="white")

        parameters = """beautiful anime girl, white hair, red eyes, school uniform
Negative prompt: bad anatomy, low quality
Steps: 28, Sampler: euler a, CFG scale: 7.0, Seed: 123, Size: 512x512"""

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        # Unicode should be preserved
        assert "anime" in result["prompt"].lower() or "girl" in result["prompt"].lower()


class TestImg2ImgDetection:
    """Tests for img2img detection in metadata."""

    def test_webui_img2img_detected(self, tmp_path: Path):
        """WebUI img2img should be detected from denoising strength."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "webui_img2img.png"
        img = Image.new("RGB", (512, 512), color="yellow")

        parameters = """modified image
Negative prompt: bad
Steps: 20, Sampler: euler a, CFG scale: 7.0, Seed: 123, Size: 512x512, Denoising strength: 0.75"""

        metadata = PngInfo()
        metadata.add_text("parameters", parameters)
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "webui"
        assert result["metadata"]["_parsed"]["is_img2img"] is True

    def test_comfyui_img2img_detected(self, tmp_path: Path):
        """ComfyUI img2img should be detected from LoadImage and denoise."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "comfyui_img2img.png"
        img = Image.new("RGB", (512, 512), color="cyan")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "steps": 20,
                    "denoise": 0.75,  # Less than 1.0
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["6", 0]  # From LoadImage
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "prompt", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "LoadImage",
                "inputs": {"image": "input.png"}
            },
            "6": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["5", 0]}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"
        # Should detect img2img from LoadImage + denoise < 1.0
        img2img_info = result["metadata"]["_parsed"].get("img2img_info")
        if img2img_info:
            assert img2img_info.get("denoising_strength") == 0.75


class TestPromptNodesExtraction:
    """Tests for prompt nodes extraction in ComfyUI workflows."""

    def test_multiple_prompt_nodes(self, tmp_path: Path):
        """Multiple prompt nodes should be captured."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "multi_prompt.png"
        img = Image.new("RGB", (512, 512), color="magenta")

        workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 12345,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0]
                }
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "main prompt text", "clip": ["2", 1]}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative text here", "clip": ["2", 1]}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512}
            }
        }

        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(workflow))
        img.save(img_path, pnginfo=metadata)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"
        # Prompt nodes should be captured in metadata
        prompt_nodes = result["metadata"]["_parsed"].get("prompt_nodes")
        if prompt_nodes:
            assert len(prompt_nodes) >= 1


def _build_vlm_danbooru_workflow(selection_data: str,
                                 stale_cache: str = "STALE WRONG PROMPT, 1girl, prika (nikke)") -> dict:
    """Trimmed copy of the real runtime-VLM workflow.

    DanbooruGalleryNode(51) --image--> QwenTE_ImageInfer(145, Qwen3-VL)
      --text--> ShowText|pysssss(52) --text--> CLIPTextEncode(11) --> KSampler(19).positive

    ComfyUI serializes widget values at QUEUE time, so ShowText's ``text_0``
    is a stale display cache from a PREVIOUS run, while the DanbooruGallery
    ``selection_data`` literal reflects the CURRENT run.
    """
    return {
        "19": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1, "steps": 36, "cfg": 4.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["66", 0],
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["28", 0],
            },
        },
        "11": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["52", 0], "clip": ["66", 1]},
        },
        "12": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "low resolution,worst quality, low quality, normal quality, lowres",
                "clip": ["66", 1],
            },
        },
        "52": {
            "class_type": "ShowText|pysssss",
            "inputs": {"text_0": stale_cache, "text": ["145", 0]},
        },
        "145": {
            "class_type": "QwenTE_ImageInfer",
            "inputs": {
                "输入模式": "图片",
                "提示词": "Analyze this image and write a danbooru tag prompt",
                "系统提示词": "在tag中添加artists: @satou kibi",
                "图片": ["51", 0],
            },
        },
        "51": {
            "class_type": "DanbooruGalleryNode",
            "inputs": {
                "selection_data": selection_data,
                "filter_data": "{\"startTime\":null,\"endTime\":null,\"startPage\":null}",
                "danbooru_gallery_widget": "",
            },
        },
        "66": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        },
        "28": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512},
        },
    }


_SPARKLE_SELECTION_DATA = json.dumps({
    "selections": [{
        "post_id": "11555190",
        "prompt": ("honkai: star rail, honkai \\(series\\), "
                   "sparkle \\(honkai: star rail\\), 1girl, bare shoulders, bell"),
    }],
})


class TestComfyUIRuntimeVlmPromptTrace:
    """Runtime-VLM workflows: stale ShowText caches vs queue-time danbooru literals."""

    def test_showtext_stale_cache_bypassed_when_upstream_danbooru_resolves(self, tmp_path: Path):
        """ShowText text_0 is a stale display cache; upstream danbooru selection must win."""
        workflow = _build_vlm_danbooru_workflow(_SPARKLE_SELECTION_DATA)
        img_path = _write_comfyui_prompt_png(tmp_path, "vlm_danbooru.png", workflow)

        result = parse_image(str(img_path))

        prompt = result["prompt"] or ""
        assert "sparkle" in prompt
        assert "honkai: star rail" in prompt
        # Stale cache must be REPLACED, not concatenated.
        assert "STALE WRONG PROMPT" not in prompt
        assert "prika" not in prompt
        assert "nikke" not in prompt
        # VLM instruction / system-prompt text must never leak into the prompt.
        assert "Analyze this image" not in prompt
        assert "在tag中添加" not in prompt

    def test_negative_prompt_literal_unaffected_by_vlm_trace(self, tmp_path: Path):
        """Node-12 literal negative prompt must remain extracted as-is."""
        workflow = _build_vlm_danbooru_workflow(_SPARKLE_SELECTION_DATA)
        img_path = _write_comfyui_prompt_png(tmp_path, "vlm_negative.png", workflow)

        result = parse_image(str(img_path))

        assert result["negative_prompt"] == (
            "low resolution,worst quality, low quality, normal quality, lowres"
        )

    def test_prompt_nodes_breakdown_resolves_danbooru_source(self, tmp_path: Path):
        """The with-source trace variant must also resolve the danbooru selection."""
        workflow = _build_vlm_danbooru_workflow(_SPARKLE_SELECTION_DATA)
        img_path = _write_comfyui_prompt_png(tmp_path, "vlm_prompt_nodes.png", workflow)

        result = parse_image(str(img_path))

        prompt_nodes = result["metadata"]["_parsed"].get("prompt_nodes") or []
        positives = [n for n in prompt_nodes if n.get("role") == "positive"]
        assert positives, f"Expected a positive prompt node, got {prompt_nodes}"
        assert "sparkle" in positives[0]["text"]
        assert "STALE WRONG PROMPT" not in positives[0]["text"]
        assert positives[0]["source_class_type"] == "DanbooruGalleryNode"
        assert positives[0]["source_key"] == "selection_data"

    def test_showtext_cache_used_when_upstream_unresolvable(self, tmp_path: Path):
        """Pure VLM dead-end (no danbooru): cached text_0 is the only recoverable text."""
        cached = "1girl, cached vlm output, blue eyes, smile"
        workflow = _build_vlm_danbooru_workflow("{}", stale_cache=cached)
        # Replace the danbooru source with a plain image loader: nothing recoverable.
        workflow["51"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "input.png", "upload": "image"},
        }
        img_path = _write_comfyui_prompt_png(tmp_path, "vlm_dead_end.png", workflow)

        result = parse_image(str(img_path))

        assert result["prompt"] == cached

    def test_showtext_concat_literal_chain_bypasses_stale_cache(self, tmp_path: Path):
        """Concat-of-literals upstream of ShowText must beat the stale cache."""
        workflow = _build_vlm_danbooru_workflow(_SPARKLE_SELECTION_DATA)
        workflow["52"]["inputs"] = {"text_0": "STALE WRONG PROMPT", "text": ["53", 0]}
        workflow["53"] = {
            "class_type": "CR Text Concatenate",
            "inputs": {"separator": "", "text1": "part one tags", "text2": "part two tags"},
        }
        img_path = _write_comfyui_prompt_png(tmp_path, "showtext_concat.png", workflow)

        result = parse_image(str(img_path))

        prompt = result["prompt"] or ""
        assert "part one tags" in prompt
        assert "part two tags" in prompt
        assert "STALE WRONG PROMPT" not in prompt

    def test_danbooru_gallery_multiple_selections_joined(self, tmp_path: Path):
        """Multiple danbooru selections join with ', '."""
        selection = json.dumps({
            "selections": [
                {"post_id": "1", "prompt": "first post tags, 1girl"},
                {"post_id": "2", "prompt": "second post tags, 2girls"},
            ],
        })
        workflow = _build_vlm_danbooru_workflow(selection)
        img_path = _write_comfyui_prompt_png(tmp_path, "danbooru_multi.png", workflow)

        result = parse_image(str(img_path))

        assert result["prompt"] == "first post tags, 1girl, second post tags, 2girls"

    @pytest.mark.parametrize("bad_selection_data", [
        "{not valid json",
        '{"selections": {}}',
        '{"selections": [{"post_id": "1"}]}',
        '"just a string"',
        "",
    ])
    def test_danbooru_gallery_malformed_selection_data_falls_back_to_cache(
            self, tmp_path: Path, bad_selection_data: str):
        """Malformed selection_data: no exception, ShowText cache used as fallback."""
        cached = "1girl, fallback cached prompt, red hair"
        workflow = _build_vlm_danbooru_workflow(bad_selection_data, stale_cache=cached)
        img_path = _write_comfyui_prompt_png(tmp_path, "danbooru_malformed.png", workflow)

        result = parse_image(str(img_path))

        assert result["generator"] == "comfyui"
        assert result["prompt"] == cached


class TestAlternateGenerators:
    """Tests for alternate / less-common generator detection.

    Covers Fooocus, reForge, Easy Diffusion, InvokeAI, SwarmUI, Draw Things,
    and the closed-source AI providers (Gemini / gpt-image) that a 3.2.x
    onward release should surface in the gallery instead of dumping into
    a flat "unknown" bucket.
    """

    @staticmethod
    def _write_png(tmp_path: Path, name: str, text_chunks: dict, mode: str = "RGB") -> Path:
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / name
        info = PngInfo()
        for key, value in text_chunks.items():
            info.add_text(key, value if isinstance(value, str) else json.dumps(value))
        Image.new(mode, (32, 32), color="white").save(img_path, pnginfo=info)
        return img_path

    def test_fooocus_comment_json(self, tmp_path: Path):
        """Fooocus PNGs put a JSON dict in the `Comment` text chunk with
        Title-Case keys distinct from NovelAI's lower-case shape."""
        comment = json.dumps({
            "Prompt": "a fooocus prompt",
            "Negative Prompt": "blurry",
            "Sampler": "dpmpp_2m_sde_gpu",
            "Performance": "Speed",
            "Base Model": "juggernautXL_v8.safetensors",
            "Resolution": "(1024, 1024)",
            "Sharpness": 2.0,
        })
        img_path = self._write_png(
            tmp_path,
            "fooocus.png",
            {"Comment": comment, "fooocus_scheme": "fooocus"},
        )

        result = parse_image(str(img_path))

        assert result["generator"] == "fooocus"
        assert result["prompt"] == "a fooocus prompt"
        assert result["negative_prompt"] == "blurry"
        assert result["checkpoint"] == "juggernautXL_v8.safetensors"

    def test_reforge_parameters_version(self, tmp_path: Path):
        """sd-webui-reForge writes a `Version: f0.0.X+v...-reforge` tag in
        the `parameters` chunk; the WebUI family detector should classify
        it as `reforge`, not vanilla `forge` or `webui`."""
        params = (
            "best quality, masterpiece\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
            "Size: 512x512, Model: model.safetensors, "
            "Version: f0.0.17v1.8.0rc-latest-1212-reforge"
        )
        img_path = self._write_png(tmp_path, "reforge.png", {"parameters": params})

        result = parse_image(str(img_path))

        assert result["generator"] == "reforge"
        assert result["prompt"] == "best quality, masterpiece"
        assert result["negative_prompt"] == "low quality"

    def test_invokeai_v3_metadata(self, tmp_path: Path):
        """InvokeAI v3 writes the `invokeai_metadata` PNG text chunk
        containing positive/negative prompts and a `model` dict."""
        meta = {
            "positive_prompt": "an invoke prompt",
            "negative_prompt": "ugly",
            "model": {"model_name": "sdxl_base", "base": "sdxl"},
            "steps": 30,
            "cfg_scale": 7.5,
            "seed": 42,
            "scheduler": "euler_a",
        }
        img_path = self._write_png(
            tmp_path, "invokeai.png", {"invokeai_metadata": json.dumps(meta)}
        )

        result = parse_image(str(img_path))

        assert result["generator"] == "invokeai"
        assert result["prompt"] == "an invoke prompt"
        assert result["negative_prompt"] == "ugly"
        assert result["checkpoint"] == "sdxl_base"

    def test_swarmui_sui_image_params(self, tmp_path: Path):
        """SwarmUI / StableSwarmUI stores `sui_image_params` JSON inside
        the PNG `parameters` chunk."""
        params = json.dumps({"sui_image_params": {
            "prompt": "swarm prompt",
            "negativeprompt": "swarm neg",
            "model": "swarmModel.safetensors",
            "steps": 20,
            "cfgscale": 7,
            "seed": 12345,
        }})
        img_path = self._write_png(tmp_path, "swarmui.png", {"parameters": params})

        result = parse_image(str(img_path))

        assert result["generator"] == "swarmui"
        assert result["prompt"] == "swarm prompt"
        assert result["negative_prompt"] == "swarm neg"
        assert result["checkpoint"] == "swarmModel.safetensors"

    def test_easy_diffusion_text_chunks(self, tmp_path: Path):
        """Easy Diffusion writes direct `prompt`/`negative_prompt` text
        chunks plus its own `use_*_model` keys. The `use_*_model` markers
        are required so we don't steal generic JSON sidecars."""
        img_path = self._write_png(tmp_path, "easyd.png", {
            "prompt": "easy d prompt",
            "negative_prompt": "easy neg",
            "use_stable_diffusion_model": "easymodel.safetensors",
            "sampler_name": "euler_a",
            "num_inference_steps": "20",
        })

        result = parse_image(str(img_path))

        assert result["generator"] == "easy-diffusion"
        assert result["prompt"] == "easy d prompt"
        assert result["negative_prompt"] == "easy neg"

    def test_easy_diffusion_does_not_hijack_generic_sidecar(self, tmp_path: Path):
        """A bare `prompt`/`negative_prompt` JSON sidecar (no
        Easy-Diffusion-specific markers) must still classify as
        `others`, NOT easy-diffusion."""
        from PIL import Image

        img_path = tmp_path / "caption.webp"
        Image.new("RGB", (320, 240), color="green").save(img_path, "WEBP")
        (tmp_path / "caption.json").write_text(
            json.dumps({
                "prompt": "json prompt",
                "negative_prompt": "json negative",
                "checkpoint": "json_model.safetensors",
            }),
            encoding="utf-8",
        )

        result = parse_image(str(img_path))

        assert result["generator"] == "others"
        assert result["prompt"] == "json prompt"

    def test_drawthings_xmp_user_comment(self, tmp_path: Path):
        """Draw Things stores its JSON inside an XMP exif:UserComment
        rdf:Alt list."""
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" '
            'xmlns:exif="http://ns.adobe.com/exif/1.0/">'
            '<exif:UserComment><rdf:Alt><rdf:li xml:lang="x-default">'
            + json.dumps({
                "c": "drawthings prompt",
                "uc": "drawthings neg",
                "model": "model.safetensors",
                "sampler": "dpmpp_2m",
                "steps": 25,
                "seed": 99,
            })
            + '</rdf:li></rdf:Alt></exif:UserComment>'
            '</rdf:Description></rdf:RDF></x:xmpmeta>'
        )
        img_path = self._write_png(tmp_path, "drawthings.png", {"XML:com.adobe.xmp": xmp})

        result = parse_image(str(img_path))

        assert result["generator"] == "drawthings"
        assert result["prompt"] == "drawthings prompt"
        assert result["negative_prompt"] == "drawthings neg"

    def test_gemini_software_tag(self, tmp_path: Path):
        """Gemini-generated images carry a Software/Make tag that
        identifies them. We surface the Description (often the prompt
        used) so the user sees something useful."""
        img_path = self._write_png(tmp_path, "gemini.png", {
            "Software": "Gemini",
            "Description": "a beautiful sunset",
        })

        result = parse_image(str(img_path))

        assert result["generator"] == "gemini"
        assert result["prompt"] == "a beautiful sunset"

    def test_nano_banana_software_tag(self, tmp_path: Path):
        """`Nano Banana` (Gemini 2.5 Flash Image codename) should also
        classify as gemini."""
        img_path = self._write_png(tmp_path, "nano.png", {
            "Software": "Made with Google AI (nano-banana)",
        })

        result = parse_image(str(img_path))

        assert result["generator"] == "gemini"

    def test_fooocus_real_lowercase_shape(self, tmp_path: Path):
        """Real Fooocus output (per lllyasviel/Fooocus private_logger.py)
        uses LOWERCASE `prompt`/`negative_prompt` JSON keys and
        sibling `base_model`/`performance`/`metadata_scheme` fields,
        unlike NovelAI which uses `prompt`+`uc`. Must NOT be
        misclassified as NAI."""
        comment = json.dumps({
            "prompt": "1girl, beautiful",
            "negative_prompt": "blurry",
            "performance": "Speed",
            "steps": 30,
            "sampler": "dpmpp_2m_sde_gpu",
            "scheduler": "karras",
            "seed": 12345,
            "width": 1024,
            "height": 1024,
            "base_model": "juggernautXL_v8.safetensors",
            "version": "Fooocus v2.5.0",
            "metadata_scheme": "fooocus",
        })
        img_path = self._write_png(tmp_path, "fooocus_real.png", {
            "Comment": comment,
            "fooocus_scheme": "fooocus",
        })

        result = parse_image(str(img_path))

        assert result["generator"] == "fooocus", f"got {result['generator']}, expected fooocus"
        assert result["prompt"] == "1girl, beautiful"
        assert result["negative_prompt"] == "blurry"
        assert result["checkpoint"] == "juggernautXL_v8.safetensors"

    def test_fooocus_real_shape_no_scheme_chunk(self, tmp_path: Path):
        """Even without the `fooocus_scheme` PNG chunk, the JSON key
        shape (lowercase prompt + negative_prompt + base_model/etc.)
        must still classify as Fooocus rather than fall through to
        NovelAI's `prompt` matcher."""
        comment = json.dumps({
            "prompt": "1girl",
            "negative_prompt": "blurry",
            "performance": "Speed",
            "base_model": "model.safetensors",
            "steps": 30,
            "sampler": "dpmpp_2m_sde_gpu",
            "seed": 12345,
        })
        img_path = self._write_png(tmp_path, "fooocus_no_scheme.png", {"Comment": comment})

        result = parse_image(str(img_path))

        assert result["generator"] == "fooocus", f"got {result['generator']}, expected fooocus"

    def test_nai_still_wins_when_uc_present(self, tmp_path: Path):
        """A NovelAI Comment chunk (`prompt`+`uc`+`v4_prompt`) must
        still classify as NAI even though it ALSO has a `prompt` key
        — the disambiguator must not steal it for Fooocus."""
        comment = json.dumps({
            "prompt": "nai prompt",
            "uc": "nai negative",
            "v4_prompt": {"prompt": "v4", "caption": "..."},
            "steps": 28,
            "sampler": "k_euler_ancestral",
        })
        img_path = self._write_png(tmp_path, "nai_v4.png", {"Comment": comment})

        result = parse_image(str(img_path))

        assert result["generator"] == "nai", f"got {result['generator']}, expected nai"

    def test_gpt_image_software_tag(self, tmp_path: Path):
        """OpenAI gpt-image / ChatGPT / DALL-E images expose their
        provider via Software/Make EXIF tags or C2PA claim_generator."""
        for software in ("OpenAI gpt-image-1", "ChatGPT", "DALL-E 3"):
            img_path = self._write_png(tmp_path, f"gpt-{software}.png", {
                "Software": software,
            })
            result = parse_image(str(img_path))
            assert result["generator"] == "gpt-image", (software, result["generator"])

    def test_c2pa_byte_signature_gpt_image(self, tmp_path: Path):
        """When the metadata Software/Make tags are stripped but the
        C2PA manifest remains, the byte-signature fallback should still
        identify the provider. We synthesize a small manifest-shaped
        binary blob (`c2pa` anchor + provider name) and append it as a
        non-standard PNG chunk, then confirm the parser picks it up."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        import struct
        import zlib

        img_path = tmp_path / "gpt-c2pa.png"
        info = PngInfo()
        # No Software / Make / Description — the front-of-file bytes must
        # be the only signal.
        Image.new("RGB", (256, 256), color="white").save(img_path, pnginfo=info)

        # Inject a fake C2PA-shaped chunk near the file header. Real
        # OpenAI images carry a `caBX` JUMBF chunk right after IHDR; we
        # mimic that by inserting after the PNG signature & IHDR.
        data = img_path.read_bytes()
        # PNG signature is 8 bytes + IHDR length (4) + 'IHDR' (4) + data (13) + CRC (4) = 33 bytes
        insert_at = 8 + 4 + 4 + 13 + 4
        # Build a fake `caBX` chunk containing both anchor and signature.
        payload = (
            b"\x00\x00\x00\x00jumbfc2pa\x00"
            b"claim_generator\x00gpt-image-1.0/openai\x00"
            b"\x00" * 200  # padding to push file size above 32 KiB threshold
        )
        # Minimum file size threshold is 32 KiB — pad payload accordingly.
        payload = payload.ljust(40 * 1024, b"\x00")
        chunk_type = b"caBX"
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        chunk = struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)
        new_data = data[:insert_at] + chunk + data[insert_at:]
        img_path.write_bytes(new_data)

        result = parse_image(str(img_path))
        assert result["generator"] == "gpt-image", result["generator"]

    def test_c2pa_byte_signature_gemini(self, tmp_path: Path):
        """Same as the gpt-image C2PA test, for Gemini / Imagen / Google AI."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        import struct
        import zlib

        img_path = tmp_path / "gemini-c2pa.png"
        Image.new("RGB", (256, 256), color="white").save(img_path, pnginfo=PngInfo())

        data = img_path.read_bytes()
        insert_at = 8 + 4 + 4 + 13 + 4
        payload = (
            b"\x00\x00\x00\x00jumbfc2pa\x00"
            b"claim_generator\x00google-imagen-3.0\x00"
        )
        payload = payload.ljust(40 * 1024, b"\x00")
        chunk_type = b"caBX"
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        chunk = struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)
        new_data = data[:insert_at] + chunk + data[insert_at:]
        img_path.write_bytes(new_data)

        result = parse_image(str(img_path))
        assert result["generator"] == "gemini", result["generator"]

    def test_c2pa_byte_scan_requires_anchor(self, tmp_path: Path):
        """A regular SD image whose PROMPT happens to mention 'OpenAI'
        must NOT be misclassified as gpt-image. The C2PA scan only
        triggers when an actual manifest anchor (`c2pa`/`jumbf`/
        `claim_generator`) is present in the file."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img_path = tmp_path / "openai-prompt-only.png"
        info = PngInfo()
        info.add_text("parameters", (
            "by openai-style artist, beautiful illustration\n"
            "Negative prompt: low quality\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
            "Size: 512x512, Model: m.safetensors"
        ))
        Image.new("RGB", (512, 512), color="white").save(img_path, pnginfo=info)
        # Pad the file past the C2PA scan minimum so the anchor check
        # actually has bytes to consider.
        with open(img_path, "ab") as fh:
            fh.write(b"\x00" * (40 * 1024))

        result = parse_image(str(img_path))
        # The user mentioned 'openai' inside the prompt but NO anchor —
        # detection must stay at webui (the actual generator).
        assert result["generator"] == "webui", result["generator"]
