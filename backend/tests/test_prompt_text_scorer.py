"""Tests for the generic prompt-likeness scorer (v3.5.0 metadata L2)."""
from __future__ import annotations

import pytest

import prompt_text_scorer as pts
from metadata_parser import MetadataParser


class TestTokenize:
    def test_strips_weight_syntax_and_lora_tags(self):
        tokens = pts.tokenize_prompt_text("(masterpiece:1.2), [sketch], <lora:detail:0.8>, 1girl")
        assert tokens == ["masterpiece", "sketch", "1girl"]

    def test_normalizes_whitespace(self):
        assert pts.tokenize_prompt_text("long   hair ,  blue eyes") == ["long hair", "blue eyes"]


class TestValueFilter:
    @pytest.mark.parametrize("value", [
        "D:\\models\\checkpoints\\anything.safetensors",
        "Anima\\anime\\miaomiaoHarem_anima13.safetensors",
        "https://example.com/image.png",
        '{"nodes": [1, 2, 3]}',
        "1024x1536",
        "short",
    ])
    def test_rejects_configuration_shaped_values(self, value):
        assert pts.looks_like_non_prompt_value(value) is True

    def test_accepts_prompt_shaped_values(self):
        assert pts.looks_like_non_prompt_value("1girl, black hair, hime cut, purple eyes") is False

    def test_rejects_kjnodes_bus_titles(self):
        assert pts.looks_like_non_prompt_value("【模型】主生图节点束加载") is True
        assert pts.looks_like_non_prompt_value("【提示词】最终正面提示词输出") is True


class TestScore:
    def test_booru_tag_string_scores_high(self):
        result = pts.score_prompt_likeness("1girl, long hair, blue eyes, school uniform, smile")
        if result["vocab_available"]:
            assert result["vocab_hit_ratio"] >= 0.8
        assert result["score"] >= pts.PROMPT_SCORE_FLOOR

    def test_natural_language_sentence_passes_on_structure_alone(self):
        result = pts.score_prompt_likeness(
            "a watercolor painting of a fox resting under maple trees, soft warm light, autumn"
        )
        assert result["score"] >= pts.PROMPT_SCORE_FLOOR

    @pytest.mark.parametrize("text", [
        "A woman standing in a sunlit garden",
        "ultra realistic photo of a cat on a windowsill at golden hour",
        (
            "The character is sitting on a wooden bench reading a book while "
            "golden light filters through the trees."
        ),
        (
            "A cinematic photograph of a young woman with silver hair standing "
            "under cherry blossoms at dusk wearing a traditional embroidered coat"
        ),
    ])
    def test_natural_language_without_commas_still_passes(self, text):
        """Flux/Krea/MJ prompts are prose, not booru comma lists.

        The old comma-token average-length cap rejected any single sentence
        over 60 characters. Infinite Image Browser and sd-prompt-reader do
        not score format at all — graph position is enough — so harvest must
        at least accept caption_format=natural text.
        """
        assert pts.looks_like_non_prompt_value(text) is False
        result = pts.score_prompt_likeness(text)
        assert result["score"] >= pts.PROMPT_SCORE_FLOOR, (text, result)

    def test_sampler_name_fails_the_floor(self):
        result = pts.score_prompt_likeness("dpmpp_2m_sde_gpu karras")
        assert result["score"] < pts.PROMPT_SCORE_FLOOR

    def test_structure_only_when_vocab_missing(self):
        result = pts.score_prompt_likeness(
            "1girl, long hair, blue eyes, school uniform, smile", vocab={}
        )
        # Empty vocab → zero hits, but comma structure still carries it.
        assert result["vocab_hit_ratio"] == 0.0
        assert result["score"] >= pts.PROMPT_SCORE_FLOOR


class TestNegativeClassifier:
    def test_classic_negative(self):
        assert pts.is_negative_prompt_text(
            "worst quality, low quality, bad anatomy, extra fingers, watermark"
        ) is True

    def test_positive_with_no_is_not_negative(self):
        assert pts.is_negative_prompt_text("no humans, scenery, forest, river") is False


def _fake_anima_nodes():
    """Positive text lives in plain Text nodes reachable only via custom
    links; the negative is a literal CLIPTextEncode. Includes decoys."""
    return {
        "1": {"class_type": "AnimaBoosterLoader",
              "inputs": {"model_name": "Anima\\anime\\model.safetensors"}},
        "12": {"class_type": "Text",
               "inputs": {"text": "masterpiece, best quality, year 2025, newest, highres"}},
        "13": {"class_type": "Text",
               "inputs": {"text": "masterpiece, best quality, year 2025, newest, highres, "
                                  "1girl, black hair, hime cut, purple eyes, classroom"}},
        "14": {"class_type": "CR Text Concatenate",
               "inputs": {"separator": ", ", "text1": ["12", 0], "text2": ["13", 0]}},
        "20": {"class_type": "CLIPTextEncode",
               "inputs": {"text": "worst quality, low quality, bad anatomy, extra fingers, "
                                  "watermark, text, blurry", "clip": ["11", 1]}},
        "21": {"class_type": "KSampler",
               "inputs": {"seed": 1, "sampler_name": "dpmpp_2m_sde",
                          "scheduler": "sgm_uniform"}},
    }


class TestHarvestAndPick:
    def test_finds_positive_despite_literal_negative_encoder(self):
        """Regression: the old two-stage fallback returned early on the
        negative CLIPTextEncode hit and never scanned the Text nodes."""
        parser = MetadataParser()
        pos, neg = parser._collect_text_from_nodes(_fake_anima_nodes())
        assert pos is not None
        assert "1girl, black hair" in pos
        assert neg is not None
        assert "worst quality" in neg

    def test_substring_candidates_deduped(self):
        candidates = pts.harvest_prompt_candidates(_fake_anima_nodes(), ("CLIPTextEncode",))
        pos, _neg = pts.pick_positive_negative(candidates)
        # Node 12's text is a strict prefix of node 13's — the longer wins.
        assert pos is not None and "classroom" in pos

    def test_unknown_class_natural_language_widget_is_harvested(self):
        prose = (
            "A cinematic photograph of a young woman with silver hair standing "
            "under cherry blossoms at dusk, looking at the camera with a slight smile."
        )
        nodes = {
            "9": {
                "class_type": "SomeFluxPromptBoxNobodyHasSeen",
                "inputs": {},
                "widgets_values": [prose],
            },
        }
        pos, _neg = pts.pick_positive_negative(
            pts.harvest_prompt_candidates(nodes, ("CLIPTextEncode",))
        )
        assert pos is not None
        assert "silver hair" in pos
        assert "cherry blossoms" in pos

    def test_unknown_class_widgets_are_harvested(self):
        nodes = {
            "9": {
                "class_type": "BrandNewPromptPackV9",
                "inputs": {},
                "widgets_values": [
                    "masterpiece, best quality, 1girl, long hair, school uniform, smile"
                ],
            },
        }
        pos, neg = pts.pick_positive_negative(
            pts.harvest_prompt_candidates(nodes, ("CLIPTextEncode",))
        )
        assert pos is not None
        assert "school uniform" in pos
        assert neg is None

    def test_decoys_never_win(self):
        nodes = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "some_model_v2.safetensors"}},
            "2": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "renders/final_output_image"}},
        }
        parser = MetadataParser()
        assert parser._collect_text_from_nodes(nodes) == (None, None)

    def test_traced_negative_survives_positive_only_fallback(self):
        """_extract_comfyui_data_extended must not overwrite a traced
        negative when only the positive needs the fallback."""
        parser = MetadataParser()
        nodes = _fake_anima_nodes()
        pos, neg, *_rest = parser._extract_comfyui_data_extended(nodes)
        assert pos is not None and "1girl" in pos
        assert neg is not None and "worst quality" in neg


class TestFailOpen:
    def test_scorer_survives_vocab_loader_failure(self, monkeypatch):
        import services.tag_suggest_service as tss

        monkeypatch.setattr(tss, "get_vocab_tag_index", lambda: None)
        result = pts.score_prompt_likeness("1girl, long hair, blue eyes, smile, outdoors")
        assert result["vocab_available"] is False
        assert result["score"] >= pts.PROMPT_SCORE_FLOOR
