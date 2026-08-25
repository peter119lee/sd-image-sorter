"""ComfyUI UI-workflow prompt recovery (Get/Set buses, ShowText cache, truncated parameters).

Owner sample ``L:\\Downloads\\Deta_DT_00045_.png`` is a ComfyUI PNG with:
- a UI ``workflow`` chunk (no API ``prompt`` graph)
- CLIPTextEncode text wired through kjnodes GetNode/SetNode buses
- executed prompt cached on ShowText|pysssss (list widget)
- a WD14 ShowText decoy that must not win
- a truncated A1111 ``parameters`` chunk (has ``Negative prompt:``, no Steps/Sampler)

Infinite Image Browser reads that parameters blob via
``is_img_created_by_comfyui_with_webui_gen_info``. We must recover the same
prompt without reclassifying the image as webui (workflow LoRAs stay).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from metadata_parser import MetadataParser, parse_image

DETA_PNG = Path(r"L:\Downloads\Deta_DT_00045_.png")

FULL_POS = (
    "masterpiece, best quality, 1girl, fox ears, white hair, chinese clothes, "
    "plum blossoms, looking at viewer"
)
FULL_NEG = (
    "worst quality, low quality, bad anatomy, extra fingers, watermark, "
    "jpeg artifacts"
)
WD14_TAGS = (
    "1girl, smile, long hair, blue eyes, school uniform, looking at viewer, "
    "outdoors, cherry blossoms, cowboy shot, medium breasts, day, standing"
)
FRAGMENT = "fox ears, white hair"


def _write_png(tmp_path: Path, filename: str, chunks: dict) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    path = tmp_path / filename
    info = PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
    return path


def _deta_like_workflow() -> dict:
    """Minimized UI-workflow reproducing the Deta Get/Set + ShowText shape."""
    return {
        "last_node_id": 70,
        "last_link_id": 8,
        "nodes": [
            {
                "id": 10,
                "type": "CLIPTextEncode",
                "widgets_values": [""],
                "inputs": [
                    {"name": "clip", "type": "CLIP", "link": None},
                    {"name": "text", "type": "STRING", "link": 1, "widget": {"name": "text"}},
                ],
                "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [7]}],
            },
            {
                "id": 11,
                "type": "CLIPTextEncode",
                "widgets_values": [""],
                "inputs": [
                    {"name": "clip", "type": "CLIP", "link": None},
                    {"name": "text", "type": "STRING", "link": 2, "widget": {"name": "text"}},
                ],
                "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [8]}],
            },
            {
                "id": 20,
                "type": "GetNode",
                "title": "Get_final_pos",
                "widgets_values": ["final_pos"],
                "inputs": [],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [1]}],
            },
            {
                "id": 21,
                "type": "GetNode",
                "title": "Get_final_neg",
                "widgets_values": ["final_neg"],
                "inputs": [],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [2]}],
            },
            {
                "id": 30,
                "type": "SetNode",
                "title": "Set_final_pos",
                "widgets_values": ["final_pos"],
                "inputs": [{"name": "STRING", "type": "STRING", "link": 3}],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [5]}],
            },
            {
                "id": 31,
                "type": "SetNode",
                "title": "Set_final_neg",
                "widgets_values": ["final_neg"],
                "inputs": [{"name": "STRING", "type": "STRING", "link": 4}],
            },
            {
                "id": 40,
                "type": "ShowText|pysssss",
                "widgets_values": [[FULL_POS]],
                "inputs": [{"name": "text", "type": "STRING", "link": 5}],
            },
            {
                "id": 41,
                "type": "ShowText|pysssss",
                "widgets_values": [[WD14_TAGS]],
                "inputs": [{"name": "text", "type": "STRING", "link": 6}],
            },
            {
                "id": 50,
                "type": "WeiLinPromptUIWithoutLora",
                "title": "负面提示词",
                "widgets_values": [FULL_NEG, False, "", "", ""],
                "inputs": [
                    {
                        "name": "positive",
                        "type": "STRING",
                        "link": None,
                        "widget": {"name": "positive"},
                    }
                ],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [4]}],
            },
            {
                "id": 51,
                "type": "WeiLinPromptUIWithoutLora",
                "title": "正面提示词",
                "widgets_values": [FRAGMENT, False, "", "", ""],
                "inputs": [
                    {
                        "name": "positive",
                        "type": "STRING",
                        "link": None,
                        "widget": {"name": "positive"},
                    }
                ],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [3]}],
            },
            {
                "id": 60,
                "type": "WD14Tagger|pysssss",
                "widgets_values": [
                    "wd-v3",
                    0.35,
                    1,
                    False,
                    False,
                    "",
                    "1boy, 1girl, bad quality, extra fingers, completely nude",
                ],
                "inputs": [],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [6]}],
            },
            {
                "id": 70,
                "type": "AnimaFlowCorrectiveSampler",
                "widgets_values": [1, "fixed", 20],
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": None},
                    {"name": "positive", "type": "CONDITIONING", "link": 7},
                    {"name": "negative", "type": "CONDITIONING", "link": 8},
                    {"name": "latent_image", "type": "LATENT", "link": None},
                ],
            },
        ],
        "links": [
            [1, 20, 0, 10, 1, "STRING"],
            [2, 21, 0, 11, 1, "STRING"],
            [3, 51, 0, 30, 0, "STRING"],
            [4, 50, 0, 31, 0, "STRING"],
            [5, 30, 0, 40, 0, "STRING"],
            [6, 60, 0, 41, 0, "STRING"],
            [7, 10, 0, 70, 1, "CONDITIONING"],
            [8, 11, 0, 70, 2, "CONDITIONING"],
        ],
    }


class TestComfyUIWorkflowUiPromptRecovery:
    def test_get_set_bus_and_showtext_cache_recover_executed_prompt(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "workflow_ui.png",
            {"workflow": json.dumps(_deta_like_workflow())},
        )
        result = parse_image(str(path))

        assert result["generator"] == "comfyui"
        assert result["prompt"]
        assert "masterpiece" in result["prompt"]
        assert "fox ears" in result["prompt"]
        assert "chinese clothes" in result["prompt"]
        assert result["negative_prompt"]
        assert "worst quality" in result["negative_prompt"]
        assert "bad anatomy" in result["negative_prompt"]
        # WD14 reverse tags / exclude list must not replace the generation prompt.
        assert "school uniform" not in (result["prompt"] or "")
        assert "completely nude" not in (result["prompt"] or "")
        assert "completely nude" not in (result["negative_prompt"] or "")

    def test_truncated_parameters_fill_empty_comfyui_prompt_without_reclassifying(
        self, tmp_path: Path
    ):
        """IIB parity: parameters with Negative prompt: but no Steps/Sampler."""
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CheckpointLoaderSimple",
                    "widgets_values": ["anima-base-v1.0.safetensors"],
                },
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": [""],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                    ],
                },
            ],
            "links": [],
        }
        parameters = (
            f"{FULL_POS}\nNegative prompt: {FULL_NEG}"
        )
        path = _write_png(
            tmp_path,
            "comfy_params.png",
            {
                "workflow": json.dumps(workflow),
                "parameters": parameters,
            },
        )
        result = parse_image(str(path))

        assert result["generator"] == "comfyui"
        assert result["prompt"] == FULL_POS
        assert result["negative_prompt"] == FULL_NEG
        assert result["checkpoint"] == "anima-base-v1.0.safetensors"

    def test_negative_clip_listed_first_is_not_swapped_to_positive(self, tmp_path: Path):
        """UI node order often lists the negative CLIPTextEncode first."""
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CLIPTextEncode",
                    "widgets_values": [FULL_NEG],
                    "inputs": [
                        {
                            "name": "text",
                            "type": "STRING",
                            "link": None,
                            "widget": {"name": "text"},
                        }
                    ],
                },
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": [FULL_POS],
                    "inputs": [
                        {
                            "name": "text",
                            "type": "STRING",
                            "link": None,
                            "widget": {"name": "text"},
                        }
                    ],
                },
            ],
            "links": [],
        }
        path = _write_png(tmp_path, "clip_order.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))

        assert result["generator"] == "comfyui"
        assert "masterpiece" in (result["prompt"] or "")
        assert "worst quality" not in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_parameters_only_truncated_a1111_still_yields_prompt(self, tmp_path: Path):
        """A parameters chunk without Steps/Sampler is still a prompt blob."""
        path = _write_png(
            tmp_path,
            "params_only.png",
            {"parameters": f"{FULL_POS}\nNegative prompt: {FULL_NEG}"},
        )
        result = parse_image(str(path))

        assert result["prompt"] == FULL_POS
        assert result["negative_prompt"] == FULL_NEG
        assert result["generator"] in {"webui", "others"}

    def test_random_parameters_without_markers_are_not_a_prompt(self, tmp_path: Path):
        path = _write_png(
            tmp_path,
            "random_params.png",
            {"parameters": "a watercolor of a fox resting under maple trees, soft warm light"},
        )
        result = parse_image(str(path))
        assert not (result.get("prompt") or "").strip()

    def test_parameters_replace_graph_junk_like_iib(self, tmp_path: Path):
        """IIB uses the parameters blob when ComfyUI graph text is not a prompt.

        Graph widgets like kjnodes bus titles must lose to executed geninfo.
        """
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "GetNode",
                    "widgets_values": ["【模型】主生图节点束加载"],
                    "inputs": [],
                },
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": ["【模型】主生图节点束加载"],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                    ],
                },
            ],
            "links": [],
        }
        parameters = f"{FULL_POS}\nNegative prompt: {FULL_NEG}"
        path = _write_png(
            tmp_path,
            "junk_plus_params.png",
            {"workflow": json.dumps(workflow), "parameters": parameters},
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert result["prompt"] == FULL_POS
        assert result["negative_prompt"] == FULL_NEG

    def test_parameters_do_not_overwrite_existing_comfyui_prompt(self, tmp_path: Path):
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CLIPTextEncode",
                    "widgets_values": [FULL_POS],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                    ],
                },
            ],
            "links": [],
        }
        decoy = (
            "DECOY WRONG PROMPT, 1girl, looking at viewer, extra decoy tags\n"
            "Negative prompt: decoy negative"
        )
        path = _write_png(
            tmp_path,
            "params_decoy.png",
            {"workflow": json.dumps(workflow), "parameters": decoy},
        )
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "masterpiece" in (result["prompt"] or "")
        assert "DECOY" not in (result["prompt"] or "")

    def test_tagger_showtext_via_set_get_bus_does_not_win(self, tmp_path: Path):
        long_wd14 = (
            "1girl, smile, long hair, blue eyes, school uniform, looking at viewer, "
            "outdoors, cherry blossoms, cowboy shot, medium breasts, day, standing, "
            "completely nude, extra filler tags to make this longer than the real prompt"
        )
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CLIPTextEncode",
                    "widgets_values": [FULL_POS],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                    ],
                },
                {
                    "id": 2,
                    "type": "WD14Tagger|pysssss",
                    "widgets_values": ["wd", 0.35],
                    "outputs": [{"name": "STRING", "type": "STRING", "links": [1]}],
                },
                {
                    "id": 3,
                    "type": "SetNode",
                    "title": "Set_wd14",
                    "widgets_values": ["wd14"],
                    "inputs": [{"name": "STRING", "type": "STRING", "link": 1}],
                },
                {
                    "id": 4,
                    "type": "GetNode",
                    "title": "Get_wd14",
                    "widgets_values": ["wd14"],
                    "outputs": [{"name": "STRING", "type": "STRING", "links": [3]}],
                },
                {
                    "id": 5,
                    "type": "ShowText|pysssss",
                    "widgets_values": [[long_wd14]],
                    "inputs": [{"name": "text", "type": "STRING", "link": 3}],
                },
            ],
            "links": [
                [1, 2, 0, 3, 0, "STRING"],
                [3, 4, 0, 5, 0, "STRING"],
            ],
        }
        path = _write_png(tmp_path, "wd14_bus.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        prompt = result["prompt"] or ""
        assert "masterpiece" in prompt
        assert "school uniform" not in prompt
        assert "completely nude" not in prompt
        assert "completely nude" not in (result["negative_prompt"] or "")

    def test_longer_stale_showtext_does_not_replace_live_danbooru(self, tmp_path: Path):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        stale = (
            "STALE WRONG PROMPT, 1girl, looking at viewer, prika (nikke), "
            + ", ".join(f"filler{i}" for i in range(40))
        )
        selection = json.dumps({
            "selections": [{
                "post_id": "1",
                "prompt": (
                    "honkai: star rail, sparkle (honkai: star rail), "
                    "1girl, looking at viewer"
                ),
            }],
        })
        prompt_data = {
            "19": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1, "steps": 4, "cfg": 1.0,
                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                    "model": ["66", 0],
                    "positive": ["11", 0],
                    "negative": ["12", 0],
                    "latent_image": ["28", 0],
                },
            },
            "11": {"class_type": "CLIPTextEncode", "inputs": {"text": ["52", 0], "clip": ["66", 1]}},
            "12": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "low quality, worst quality, bad anatomy", "clip": ["66", 1]},
            },
            "52": {
                "class_type": "ShowText|pysssss",
                "inputs": {"text_0": stale, "text": ["51", 0]},
            },
            "51": {
                "class_type": "DanbooruGalleryNode",
                "inputs": {"selection_data": selection},
            },
            "66": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
            "28": {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64}},
        }
        path = tmp_path / "vlm_long_stale.png"
        info = PngInfo()
        info.add_text("prompt", json.dumps(prompt_data))
        Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
        result = parse_image(str(path))
        prompt = result["prompt"] or ""
        assert "sparkle" in prompt
        assert "STALE WRONG PROMPT" not in prompt
        assert "prika" not in prompt

    def test_weilin_negative_title_with_weak_indicators_still_negative(self, tmp_path: Path):
        weak_neg = "blurry, extra fingers"
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CLIPTextEncode",
                    "widgets_values": [FULL_POS],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": None, "widget": {"name": "text"}}
                    ],
                    "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [1]}],
                },
                {
                    "id": 2,
                    "type": "CLIPTextEncode",
                    "widgets_values": [""],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": 2, "widget": {"name": "text"}}
                    ],
                    "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [3]}],
                },
                {
                    "id": 3,
                    "type": "WeiLinPromptUIWithoutLora",
                    "title": "负面提示词",
                    "widgets_values": [weak_neg, False, "", "", ""],
                    "inputs": [
                        {
                            "name": "positive",
                            "type": "STRING",
                            "link": None,
                            "widget": {"name": "positive"},
                        }
                    ],
                    "outputs": [{"name": "STRING", "type": "STRING", "links": [2]}],
                },
                {
                    "id": 4,
                    "type": "KSampler",
                    "widgets_values": [1, "fixed", 20],
                    "inputs": [
                        {"name": "positive", "type": "CONDITIONING", "link": 1},
                        {"name": "negative", "type": "CONDITIONING", "link": 3},
                    ],
                },
            ],
            "links": [
                [1, 1, 0, 4, 0, "CONDITIONING"],
                [2, 3, 0, 2, 1, "STRING"],
                [3, 2, 0, 4, 1, "CONDITIONING"],
            ],
        }
        path = _write_png(tmp_path, "weilin_weak_neg.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        assert "masterpiece" in (result["prompt"] or "")
        assert "blurry" in (result["negative_prompt"] or "")
        assert "extra fingers" in (result["negative_prompt"] or "")
        assert "blurry" not in (result["prompt"] or "")

    def test_unknown_custom_node_widget_prompt_is_harvested(self, tmp_path: Path):
        """A node class the parser has never seen still yields a prompt.

        Custom packs store text in widgets_values under arbitrary class names.
        Harvest must score the string, not the node type.
        """
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "XyzUnknownBox",
                    "widgets_values": [FULL_POS],
                    "inputs": [],
                },
                {
                    "id": 2,
                    "type": "QweMysteryNegPack",
                    "widgets_values": [FULL_NEG],
                    "inputs": [],
                },
            ],
            "links": [],
        }
        path = _write_png(tmp_path, "unknown_node.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "masterpiece" in (result["prompt"] or "")
        assert "fox ears" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_unknown_loader_widget_is_checkpoint(self, tmp_path: Path):
        """Any *Loader holding a weight file is a checkpoint, name irrelevant."""
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "MysteryBoosterLoader",
                    "widgets_values": ["custom-base-v1.safetensors", "auto", False],
                },
                {
                    "id": 2,
                    "type": "XyzUnknownBox",
                    "widgets_values": [FULL_POS],
                },
            ],
            "links": [],
        }
        path = _write_png(tmp_path, "unknown_loader.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert result["checkpoint"] == "custom-base-v1.safetensors"
        assert "masterpiece" in (result["prompt"] or "")

    def test_flux_guidance_passthrough_reaches_encoder(self, tmp_path: Path):
        """IIB walks through FluxGuidance to the encoder (conditioning link)."""
        prose = "A cinematic photograph of a fox standing in snow at dusk."
        prompt_data = {
            "1": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": prose, "clip_l": ""},
            },
            "2": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": FULL_NEG, "clip_l": ""},
            },
            "4": {
                "class_type": "FluxGuidance",
                "inputs": {"guidance": 3.5, "conditioning": ["1", 0]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1, "steps": 8, "cfg": 1.0,
                    "sampler_name": "euler", "scheduler": "simple",
                    "positive": ["4", 0],
                    "negative": ["2", 0],
                },
            },
        }
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        path = tmp_path / "flux_guidance.png"
        info = PngInfo()
        info.add_text("prompt", json.dumps(prompt_data))
        Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
        result = parse_image(str(path))
        assert "fox standing in snow" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_populated_text_wins_over_wildcard_template(self, tmp_path: Path):
        """IIB prefers ImpactWildcardProcessor populated_text (executed) over the template."""
        prompt_data = {
            "1": {
                "class_type": "ImpactWildcardProcessor",
                "inputs": {
                    "wildcard_text": "{cat|dog} in a garden",
                    "populated_text": FULL_POS,
                },
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": ["1", 0]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": FULL_NEG},
            },
            "4": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1, "steps": 4, "cfg": 1.0,
                    "sampler_name": "euler", "scheduler": "simple",
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                },
            },
        }
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        path = tmp_path / "wildcard_populated.png"
        info = PngInfo()
        info.add_text("prompt", json.dumps(prompt_data))
        Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
        result = parse_image(str(path))
        assert "masterpiece" in (result["prompt"] or "")
        assert "{cat|dog}" not in (result["prompt"] or "")

    def test_flux_t5xxl_natural_language_is_the_prompt(self, tmp_path: Path):
        """CLIPTextEncodeFlux stores NL on t5xxl, not `text` (IIB reads this key)."""
        prose = (
            "A cinematic photograph of a young woman with silver hair standing "
            "under cherry blossoms at dusk, wearing a traditional embroidered coat."
        )
        prompt_data = {
            "1": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": prose, "clip_l": "portrait photo", "guidance": 3.5},
            },
            "2": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {"t5xxl": FULL_NEG, "clip_l": ""},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1, "steps": 8, "cfg": 1.0,
                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                },
            },
        }
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        path = tmp_path / "flux_t5.png"
        info = PngInfo()
        info.add_text("prompt", json.dumps(prompt_data))
        Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
        result = parse_image(str(path))
        assert "silver hair" in (result["prompt"] or "")
        assert "cherry blossoms" in (result["prompt"] or "")
        assert "worst quality" in (result["negative_prompt"] or "")

    def test_unknown_node_natural_language_widget_is_harvested(self, tmp_path: Path):
        prose = (
            "The character is sitting on a wooden bench reading a book while "
            "golden light filters through the trees."
        )
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "NeverBeforeSeenKreaBox",
                    "widgets_values": [prose],
                    "inputs": [],
                },
            ],
            "links": [],
        }
        path = _write_png(tmp_path, "nl_unknown.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        assert "wooden bench" in (result["prompt"] or "")
        assert "golden light" in (result["prompt"] or "")

    def test_reroute_between_text_and_clip_is_followed(self, tmp_path: Path):
        """ComfyUI Reroute is plumbing, not a custom pack — follow it."""
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "PrimitiveString",
                    "widgets_values": [FULL_POS],
                    "inputs": [
                        {"name": "value", "type": "STRING", "link": None, "widget": {"name": "value"}}
                    ],
                    "outputs": [{"name": "STRING", "type": "STRING", "links": [1]}],
                },
                {
                    "id": 2,
                    "type": "Reroute",
                    "widgets_values": [],
                    "inputs": [{"name": "", "type": "*", "link": 1}],
                    "outputs": [{"name": "*", "type": "*", "links": [2]}],
                },
                {
                    "id": 3,
                    "type": "CLIPTextEncode",
                    "widgets_values": [""],
                    "inputs": [
                        {"name": "text", "type": "STRING", "link": 2, "widget": {"name": "text"}}
                    ],
                },
            ],
            "links": [
                [1, 1, 0, 2, 0, "STRING"],
                [2, 2, 0, 3, 0, "STRING"],
            ],
        }
        path = _write_png(tmp_path, "reroute.png", {"workflow": json.dumps(workflow)})
        result = parse_image(str(path))
        assert result["generator"] == "comfyui"
        assert "masterpiece" in (result["prompt"] or "")

    def test_imagedescription_workflow_prefix_is_comfyui(self):
        """WebP/JPEG ImageDescription `Workflow: {json}` is the IIB EXIF shape."""
        parsed = MetadataParser()._detect_and_parse({
            "ImageDescription": "Workflow: " + json.dumps(_deta_like_workflow()),
            "Software": "ComfyUI",
        })
        assert parsed["generator"] == "comfyui"
        assert "masterpiece" in (parsed.get("prompt") or "")
        assert "worst quality" in (parsed.get("negative_prompt") or "")

    def test_usercomment_utf16_nul_tags_become_prompt(self):
        raw = "nsfw, 1girl, solo, upper body, sitting on the edge"
        mojibake = raw.encode("utf-16-le").decode("utf-8", errors="replace")
        parsed = MetadataParser()._detect_and_parse({"UserComment": mojibake})
        prompt = parsed.get("prompt") or ""
        assert "1girl" in prompt
        assert "sitting on the edge" in prompt
        assert "\x00" not in prompt


WEBP_WORKFLOW_SAMPLE = Path(r"L:\Pictures\AAA Reference\undid\137457462_p0.webp")


@pytest.mark.skipif(not WEBP_WORKFLOW_SAMPLE.is_file(), reason="owner webp sample not on this machine")
class TestOwnerWebpWorkflowCarrier:
    def test_webp_imagedescription_workflow_yields_prompt(self):
        result = parse_image(str(WEBP_WORKFLOW_SAMPLE))
        assert result["generator"] == "comfyui"
        prompt = result.get("prompt") or ""
        assert "masterpiece" in prompt or "best quality" in prompt or "1girl" in prompt
        assert prompt.strip()


@pytest.mark.skipif(not DETA_PNG.is_file(), reason="owner Deta sample not on this machine")
class TestOwnerDetaSample:
    def test_deta_png_recovers_positive_and_negative(self):
        result = parse_image(str(DETA_PNG))
        assert result["parse_error"] is None
        assert result["generator"] == "comfyui"
        prompt = result["prompt"] or ""
        negative = result["negative_prompt"] or ""
        assert "masterpiece" in prompt
        assert "ushikani kassen" in prompt
        assert "fox ears" in prompt
        assert "worst quality" in negative
        assert "bad anatomy" in negative
        # WD14 exclude-tag garbage must not become the prompt.
        assert "asphyxiation" not in prompt
        assert result["checkpoint"]
        assert "safetensors" in result["checkpoint"].lower()
