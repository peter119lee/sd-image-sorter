"""Gating suite: parse_image vs IIB / sd-prompt-reader executed-text ladder.

Every test writes a real image and calls the shipped ``parse_image`` entry.
Assertions check prompt/negative *content*, not merely non-empty.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from metadata_parser import parse_image

POS_TAGS = (
    "masterpiece, best quality, 1girl, fox ears, white hair, chinese clothes, "
    "plum blossoms, looking at viewer"
)
NEG_TAGS = (
    "worst quality, low quality, bad anatomy, extra fingers, watermark, "
    "jpeg artifacts"
)
NL_PROMPT = (
    "The character is sitting on a wooden bench reading a book while "
    "golden light filters through the trees."
)
FLUX_NL = (
    "A cinematic photograph of a young woman with silver hair standing "
    "under cherry blossoms at dusk, wearing a traditional embroidered coat."
)


def _write_png(tmp_path: Path, name: str, chunks: dict) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    path = tmp_path / name
    info = PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
    return path


def _exif_user_comment(payload: bytes) -> bytes:
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
        + struct.pack("<HHI", 0x9286, 7, len(payload))
        + struct.pack("<I", user_comment_offset)
        + struct.pack("<I", 0)
        + payload
    )
    return b"Exif\x00\x00" + tiff_header + ifd0 + exif_ifd


def _clip_workflow(positive: str, negative: str = NEG_TAGS) -> dict:
    return {
        "nodes": [
            {
                "id": 1,
                "type": "CLIPTextEncode",
                "widgets_values": [positive],
                "inputs": [
                    {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                ],
            },
            {
                "id": 2,
                "type": "CLIPTextEncode",
                "widgets_values": [negative],
                "inputs": [
                    {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                ],
            },
        ],
        "links": [],
    }


class TestA1111Parameters:
    def test_parameters_with_steps_sampler_trailer(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "a1111_full.png",
            {
                "parameters": (
                    f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}\n"
                    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
                    "Size: 64x64, Model: demo.safetensors"
                )
            },
        )
        result = parse_image(str(path))
        assert result["prompt"] == POS_TAGS
        assert result["negative_prompt"] == NEG_TAGS
        assert result["generator"] == "webui"
        assert result["checkpoint"] == "demo.safetensors"

    def test_parameters_without_steps_sampler_trailer(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "a1111_truncated.png",
            {"parameters": f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}"},
        )
        result = parse_image(str(path))
        assert result["prompt"] == POS_TAGS
        assert result["negative_prompt"] == NEG_TAGS


class TestComfyUIGraphs:
    def test_api_prompt_t5xxl_and_flux_guidance(self, tmp_path: Path):
        prompt_data = {
            "1": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": FLUX_NL, "clip_l": ""},
            },
            "2": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": NEG_TAGS, "clip_l": ""},
            },
            "4": {
                "class_type": "FluxGuidance",
                "inputs": {"guidance": 3.5, "conditioning": ["1", 0]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1,
                    "steps": 8,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "positive": ["4", 0],
                    "negative": ["2", 0],
                },
            },
        }
        path = _write_png(tmp_path, "flux.png", {"prompt": json.dumps(prompt_data)})
        result = parse_image(str(path))
        assert "silver hair" in (result["prompt"] or "")
        assert "cherry blossoms" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")
        assert result["generator"] == "comfyui"

    def test_partial_api_prompt_does_not_hide_ui_workflow_clip_widgets(self, tmp_path: Path):
        """WebP savers often store an upscale subgraph as Prompt: plus full Workflow:."""
        subgraph = {
            "177": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "ComfyUI", "images": ["178", 0]},
            },
            "178": {
                "class_type": "ImageScaleBy",
                "inputs": {"upscale_method": "lanczos", "scale_by": 2, "image": ["179", 0]},
            },
            "179": {
                "class_type": "LoadImage",
                "inputs": {"image": "input.png"},
            },
        }
        path = _write_png(
            tmp_path,
            "subgraph_plus_workflow.png",
            {
                "prompt": json.dumps(subgraph),
                "workflow": json.dumps(_clip_workflow(POS_TAGS)),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "chinese clothes" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_damaged_api_prompt_still_reads_ui_workflow(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "damaged_prompt_plus_workflow.png",
            {
                "prompt": '{"1": {"class_type": "CLIPTextEncode"',
                "workflow": json.dumps(_clip_workflow(POS_TAGS)),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_ui_workflow_json_in_prompt_key_recovers_clip_widgets(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "ui_in_prompt_key.png",
            {"prompt": json.dumps(_clip_workflow(POS_TAGS))},
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_ui_workflow_only_recovers_clip_widgets(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "workflow_only.png",
            {"workflow": json.dumps(_clip_workflow(POS_TAGS))},
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_natural_language_without_commas(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "nl.png",
            {
                "workflow": json.dumps({
                    "nodes": [{
                        "id": 1,
                        "type": "NeverSeenKreaBox",
                        "widgets_values": [NL_PROMPT],
                        "inputs": [],
                    }],
                    "links": [],
                })
            },
        )
        result = parse_image(str(path))
        assert "wooden bench" in (result["prompt"] or "")
        assert "golden light" in (result["prompt"] or "")


class TestHybridExecutedText:
    def test_usable_parameters_fill_junk_graph(self, tmp_path: Path):
        workflow = {
            "nodes": [{
                "id": 1,
                "type": "CLIPTextEncode",
                "widgets_values": ["【模型】主生图节点束加载"],
                "inputs": [
                    {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                ],
            }],
            "links": [],
        }
        path = _write_png(
            tmp_path,
            "hybrid_junk.png",
            {
                "workflow": json.dumps(workflow),
                "parameters": f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}",
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert result["prompt"] == POS_TAGS
        assert result["negative_prompt"] == NEG_TAGS

    def test_decoy_parameters_do_not_replace_encoder_prompt(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "hybrid_decoy.png",
            {
                "workflow": json.dumps(_clip_workflow(POS_TAGS)),
                "parameters": (
                    "DECOY WRONG PROMPT, 1girl, looking at viewer, extra decoy tags\n"
                    "Negative prompt: decoy negative"
                ),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "DECOY" not in (result["prompt"] or "")

    def test_fuller_graph_keeps_extra_over_truncated_parameters(self, tmp_path: Path):
        fuller = POS_TAGS + ", extra recovered fox tail, cherry blossoms extra"
        path = _write_png(
            tmp_path,
            "hybrid_fuller.png",
            {
                "workflow": json.dumps(_clip_workflow(fuller)),
                "parameters": f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}",
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "extra recovered fox tail" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_full_parameters_trailer_does_not_reclassify_or_drop_graph(self, tmp_path: Path):
        """A complete A1111 trailer must not skip ComfyUI and claim webui."""
        fuller = POS_TAGS + ", extra recovered fox tail, cherry blossoms extra"
        path = _write_png(
            tmp_path,
            "hybrid_full_trailer.png",
            {
                "workflow": json.dumps(_clip_workflow(fuller)),
                "parameters": (
                    f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}\n"
                    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
                    "Size: 64x64, Model: decoy.safetensors"
                ),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "extra recovered fox tail" in (result["prompt"] or "")
        assert "DECOY" not in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_full_parameters_fill_junk_graph_without_reclassifying(self, tmp_path: Path):
        workflow = {
            "nodes": [{
                "id": 1,
                "type": "CLIPTextEncode",
                "widgets_values": ["【模型】主生图节点束加载"],
                "inputs": [
                    {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                ],
            }],
            "links": [],
        }
        path = _write_png(
            tmp_path,
            "hybrid_full_junk.png",
            {
                "workflow": json.dumps(workflow),
                "parameters": (
                    f"{POS_TAGS}\nNegative prompt: {NEG_TAGS}\n"
                    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
                    "Size: 64x64, Model: demo.safetensors"
                ),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert result["prompt"] == POS_TAGS
        assert result["negative_prompt"] == NEG_TAGS


class TestNovelAIV5:
    def test_v5_reuses_v4_prompt_and_reads_22_character_slots(self, tmp_path: Path):
        """Official V5 (2026-08-21) still writes v4_prompt; characters live in characterPrompts."""
        characters = [
            {
                "prompt": f"char {index} silver hair",
                "uc": f"char {index} extra fingers",
                "center": {"x": 0.1 * (index % 10), "y": 0.2},
            }
            for index in range(22)
        ]
        char_captions = [
            {"char_caption": item["prompt"], "centers": [item["center"]]}
            for item in characters
        ]
        comment = {
            "prompt": "masterpiece, best quality, two girls in a garden",
            "uc": "worst quality, low quality, bad anatomy",
            "steps": 23,
            "sampler": "k_euler_ancestral",
            "scale": 7.0,
            "seed": 1,
            "width": 832,
            "height": 1216,
            "noise_schedule": "karras",
            "params_version": 4,
            "v4_prompt": {
                "caption": {
                    "base_caption": "masterpiece, best quality, two girls in a garden",
                    "char_captions": char_captions,
                },
                "use_coords": True,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {"base_caption": "worst quality, low quality, bad anatomy"},
                "legacy_uc": False,
            },
            "characterPrompts": characters,
        }
        path = _write_png(
            tmp_path,
            "nai_v5.png",
            {
                "Software": "NovelAI",
                "Source": "nai-diffusion-5-full",
                "Description": "masterpiece, best quality, two girls in a garden",
                "Comment": json.dumps(comment),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "nai"
        assert "two girls in a garden" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")
        assert result["checkpoint"] == "nai-diffusion-5-full"
        parsed = (result.get("metadata") or {}).get("_parsed") or {}
        characters = parsed.get("character_prompts")
        assert characters
        assert len(characters) == 22
        assert characters[0]["prompt"] == "char 0 silver hair"
        assert characters[21]["prompt"] == "char 21 silver hair"
        assert characters[21]["negative_prompt"] == "char 21 extra fingers"
        assert "char 21 silver hair" in (parsed.get("character_prompt_text") or "")

    def test_webp_usercomment_inner_comment_json_is_nai(self, tmp_path: Path):
        """Official WebP EXIF maps UserComment to the inner Comment JSON."""
        from PIL import Image

        payload = json.dumps({
            "prompt": "masterpiece, 1girl, silver hair",
            "uc": "worst quality, low quality",
            "steps": 23,
            "sampler": "k_euler_ancestral",
            "v4_prompt": {
                "caption": {"base_caption": "masterpiece, 1girl, silver hair"}
            },
        })
        path = tmp_path / "nai_v5.webp"
        Image.new("RGB", (64, 64), color="white").save(
            path,
            "WEBP",
            exif=_exif_user_comment(b"ASCII\x00\x00\x00" + payload.encode("utf-8")),
        )
        result = parse_image(str(path))
        assert result["generator"] == "nai"
        assert "silver hair" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")


class TestOffPngCarriers:
    def test_webp_imagedescription_workflow_prefix(self, tmp_path: Path):
        from PIL import Image

        path = tmp_path / "workflow.webp"
        img = Image.new("RGB", (64, 64), color="white")
        exif = img.getexif()
        exif[0x010E] = "Workflow: " + json.dumps(_clip_workflow(POS_TAGS))
        exif[0x0131] = "ComfyUI"
        img.save(path, "WEBP", exif=exif)

        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "chinese clothes" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_jpeg_utf16_le_usercomment_without_unicode_prefix(self, tmp_path: Path):
        from PIL import Image

        path = tmp_path / "utf16.jpg"
        raw = "nsfw, 1girl, solo, upper body, sitting on the edge"
        Image.new("RGB", (64, 64), color="blue").save(
            path,
            "JPEG",
            exif=_exif_user_comment(raw.encode("utf-16-le")),
        )
        result = parse_image(str(path))
        prompt = result.get("prompt") or ""
        assert "1girl" in prompt
        assert "sitting on the edge" in prompt
        assert "\x00" not in prompt

    def test_nul_separated_workflow_prefix_in_usercomment(self, tmp_path: Path):
        """IIB splits EXIF on NUL, then matches workflow: / prompt: prefixes."""
        from PIL import Image

        body = "padding\x00Workflow: " + json.dumps(_clip_workflow(POS_TAGS))
        path = tmp_path / "nul_workflow.jpg"
        Image.new("RGB", (64, 64), color="white").save(
            path,
            "JPEG",
            exif=_exif_user_comment(b"ASCII\x00\x00\x00" + body.encode("utf-8")),
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "fox ears" in (result["prompt"] or "")
        assert "chinese clothes" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")


class TestInvokeAINotStolenAsComfyUI:
    def test_invokeai_graph_object_nodes_do_not_hide_metadata_prompt(self, tmp_path: Path):
        """Promotion used to treat any JSON with ``nodes`` as ComfyUI workflow."""
        meta = {
            "positive_prompt": POS_TAGS,
            "negative_prompt": NEG_TAGS,
            "model": {"model_name": "sdxl_base"},
        }
        graph = {
            "id": "graph_edff6276",
            "nodes": {
                "positive_prompt:abc": {
                    "id": "positive_prompt:abc",
                    "value": POS_TAGS,
                    "is_intermediate": True,
                }
            },
        }
        path = _write_png(
            tmp_path,
            "invokeai_both.png",
            {
                "invokeai_metadata": json.dumps(meta),
                "invokeai_graph": json.dumps(graph),
            },
        )
        result = parse_image(str(path))
        assert result["generator"] == "invokeai"
        assert result["prompt"] == POS_TAGS
        assert result["negative_prompt"] == NEG_TAGS
        assert result["checkpoint"] == "sdxl_base"


class TestEmptyWhenNoGenerationText:
    def test_camera_jpeg_has_no_prompt(self, tmp_path: Path):
        from PIL import Image

        path = tmp_path / "camera.jpg"
        Image.new("RGB", (64, 64), color="green").save(path, "JPEG")
        result = parse_image(str(path))
        assert not (result.get("prompt") or "").strip()

    def test_loadimage_only_workflow_has_no_prompt(self, tmp_path: Path):
        workflow = {
            "2": {
                "inputs": {"image": "input.png"},
                "class_type": "LoadImage",
            }
        }
        path = _write_png(tmp_path, "load_only.png", {"prompt": json.dumps(workflow)})
        result = parse_image(str(path))
        assert not (result.get("prompt") or "").strip()
        assert result["generator"] == "comfyui"

    def test_plain_prose_parameters_without_markers_are_not_claimed(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "not_geninfo.png",
            {"parameters": "a watercolor of a fox resting under maple trees, soft warm light"},
        )
        result = parse_image(str(path))
        assert not (result.get("prompt") or "").strip()
