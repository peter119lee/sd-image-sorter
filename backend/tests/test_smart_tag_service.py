"""Tests for the Smart Tag service: noise-strip, training-purpose, caption assembly.

These pin the four pieces of logic that, if they regress, will silently
ship bad LoRA captions:

1. Noise-tag filter — `masterpiece`, `score_9`, `anime`, `year 2024`,
   etc. must be stripped before going anywhere near the VLM or the
   final caption.
2. Training-purpose alias map — `style_lora` -> `style`, `nsfw` ->
   `general`, etc.
3. Prompt-preset wording — the STYLE / CHARACTER / GENERAL prompts must
   direct the VLM toward the right kind of description for each training
   intent. We assert FUNCTIONAL content (mentions clothing? forbids
   identity features?) rather than verbatim wording so the prose can
   evolve.
4. Caption assembly — trigger word at front (or skipped if already
   present), tags deduped, NL sentences glued on the end.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.smart_tag_service import (  # noqa: E402
    DEFAULT_NOISE_TAGS,
    META_NOISE_TAGS,
    PROMPT_PRESETS,
    SMART_TAG_MAX_ERRORS,
    SmartTagJobState,
    SmartTagRequest,
    QUALITY_NOISE_TAGS,
    SAFETY_NOISE_TAGS,
    SCORE_NOISE_TAGS,
    TIME_NOISE_TAGS,
    TRAINING_PURPOSE_ALIASES,
    _coerce_request,
    _run_pipeline,
    assemble_caption,
    build_vlm_prompt,
    compute_consensus_tags,
    filter_noise_tags,
    filter_tags_by_training_purpose,
    get_caption_results_page,
    is_noise_tag,
    normalize_training_purpose,
)
from services.smart_tag.request import SmartTagCaptionProfile  # noqa: E402
import services.smart_tag_service as smart_tag_service  # noqa: E402


# ---------------------------------------------------------------------------
# Noise-tag filter
# ---------------------------------------------------------------------------


def test_quality_noise_tags_are_stripped() -> None:
    assert is_noise_tag("masterpiece")
    assert is_noise_tag("MASTERPIECE")
    assert is_noise_tag("best quality")
    assert is_noise_tag("worst_quality") is False  # underscore variant we don't track
    # Both the space form and the underscore form are in the set, so:
    assert is_noise_tag("best_quality")
    assert is_noise_tag("high_quality")


def test_score_family_is_stripped_via_regex() -> None:
    """The score_N family includes bare and `_up` rollups, plus the rare
    space-separated form."""
    assert is_noise_tag("score_7")
    assert is_noise_tag("score_9_up")
    assert is_noise_tag("score 7")
    assert is_noise_tag("SCORE_8_UP")
    assert is_noise_tag("score_42")  # regex matches arbitrary digits


def test_safety_and_meta_tags_are_stripped() -> None:
    for t in ["safe", "sensitive", "questionable", "nsfw", "explicit"]:
        assert is_noise_tag(t)
    for t in ["anime", "illustration", "monochrome", "sketch", "official_art"]:
        assert is_noise_tag(t), t
    # OppaiOracle-style rating:* prefix
    for t in ["rating:general", "rating:explicit"]:
        assert is_noise_tag(t)


def test_year_tag_is_stripped() -> None:
    assert is_noise_tag("year 2024")
    assert is_noise_tag("YEAR 2018")
    assert is_noise_tag("2024")


def test_real_tags_are_kept() -> None:
    for tag in ["1girl", "blue_eyes", "long_hair", "smile", "bikini", "outdoors"]:
        assert is_noise_tag(tag) is False, tag


def test_symbolic_junk_is_stripped_but_kaomoji_survive() -> None:
    # Prompt-syntax fragments are still junk...
    for tag in ["::", "--", "//", ";;"]:
        assert is_noise_tag(tag), tag
    # ...but real danbooru emoticon tags are vocabulary, not noise (audit P1-4).
    for tag in [":3", ":p", "@_@", ">_<", "^^^", "^_^", "=_=", "0_0", "!?"]:
        assert is_noise_tag(tag) is False, tag


def test_kaomoji_keep_underscores_through_caption_normalize() -> None:
    from services.smart_tag_service import _normalize_tag

    assert _normalize_tag("^_^") == "^_^"
    assert _normalize_tag("O_O") == "o_o"
    assert _normalize_tag("long_hair") == "long hair"
    assert _normalize_tag("score_9_up") == "score_9_up"


def test_filter_noise_tags_preserves_order_and_drops_noise() -> None:
    inp = ["masterpiece", "1girl", "score_9", "blue eyes", "anime", "long hair"]
    out, stripped = filter_noise_tags(inp)
    assert out == ["1girl", "blue eyes", "long hair"]
    assert stripped == 3


def test_default_noise_set_is_union_of_all_buckets() -> None:
    expected_subset = QUALITY_NOISE_TAGS | SCORE_NOISE_TAGS | SAFETY_NOISE_TAGS | META_NOISE_TAGS | TIME_NOISE_TAGS
    assert expected_subset.issubset(DEFAULT_NOISE_TAGS)


# ---------------------------------------------------------------------------
# Training-purpose normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("style", "style"),
        ("style_lora", "style"),
        ("Style-LoRA", "style"),
        ("art", "style"),
        ("character", "character"),
        ("character_lora", "character"),
        ("char", "character"),
        ("general", "general"),
        ("concept", "concept"),
        ("concept_lora", "concept"),
        ("nsfw", "general"),
        ("nsfw_lora", "general"),
        ("", "general"),
        (None, "general"),
        ("totally_unknown", "general"),
    ],
)
def test_normalize_training_purpose(given, expected) -> None:
    assert normalize_training_purpose(given) == expected


def test_training_purpose_alias_table_is_complete() -> None:
    """Every alias must map to a key that exists in PROMPT_PRESETS."""
    for alias, canonical in TRAINING_PURPOSE_ALIASES.items():
        assert canonical in PROMPT_PRESETS, f"{alias} -> {canonical} missing preset"


# ---------------------------------------------------------------------------
# Prompt presets — sanity-check that each preset directs the VLM toward
# the right kind of natural-language description for its training intent.
# We assert FUNCTIONAL content (does the prompt cover lighting? clothing?
# does it forbid identity features?) rather than verbatim wording so the
# wording can evolve without breaking tests.
# ---------------------------------------------------------------------------


def test_style_preset_describes_content_without_naming_style() -> None:
    prompt = PROMPT_PRESETS["style"].lower()
    for needle in ["subject", "clothing", "setting", "composition"]:
        assert needle in prompt, needle
    assert "do not name" in prompt
    assert "rendering medium" in prompt


def test_character_preset_excludes_fixed_identity_features() -> None:
    """Character LoRA must avoid teaching hair / eye / signature outfit."""
    prompt = PROMPT_PRESETS["character"]
    assert "do not describe" in prompt.lower()
    assert "hair color" in prompt.lower()
    assert "eye color" in prompt.lower()
    assert "signature outfit" in prompt.lower()


def test_general_preset_covers_subject_pose_clothing_background_lighting() -> None:
    prompt = PROMPT_PRESETS["general"]
    for term in ["subject", "pose", "clothing", "background", "lighting"]:
        assert term in prompt.lower(), term


def test_concept_preset_emphasises_the_concept() -> None:
    prompt = PROMPT_PRESETS["concept"]
    assert "concept" in prompt.lower()


def test_build_vlm_prompt_strips_noise_before_substituting() -> None:
    """The VLM must not see `masterpiece, score_9, anime` in its tag list."""
    prompt = build_vlm_prompt("style", ["masterpiece", "1girl", "score_9", "anime", "blue eyes"])
    assert "masterpiece" not in prompt
    assert "score_9" not in prompt
    assert "anime" not in prompt
    assert "1girl" in prompt
    assert "blue eyes" in prompt


def test_build_vlm_prompt_respects_alias_for_unknown_purpose() -> None:
    """Unknown training_purpose -> falls through to general preset."""
    prompt = build_vlm_prompt("totally_unknown", ["1girl"])
    assert prompt == build_vlm_prompt("general", ["1girl"])


# ---------------------------------------------------------------------------
# Training purpose tag filtering
# ---------------------------------------------------------------------------


def test_filter_tags_by_training_purpose_general_keeps_all() -> None:
    """General purpose training keeps all tag categories."""
    general = ["1girl", "blue_eyes", "outdoors"]
    copyright = ["genshin_impact"]
    character = ["raiden_shogun"]

    result = filter_tags_by_training_purpose("general", general, copyright, character)

    assert "1girl" in result
    assert "blue_eyes" in result
    assert "outdoors" in result
    assert "genshin_impact" in result
    assert "raiden_shogun" in result


def test_filter_tags_by_training_purpose_style_removes_style_targets_only() -> None:
    """Style mode preserves content context and removes identifiable style tags."""
    general = ["1girl", "blue_eyes", "lineart", "outdoors"]
    copyright = ["genshin_impact", "honkai_star_rail"]
    character = ["raiden_shogun", "firefly_(honkai_star_rail)"]

    result = filter_tags_by_training_purpose("style", general, copyright, character)

    assert "1girl" in result
    assert "blue_eyes" in result
    assert "outdoors" in result
    assert "lineart" not in result
    assert "genshin_impact" in result
    assert "honkai_star_rail" in result
    assert "raiden_shogun" in result
    assert "firefly_(honkai_star_rail)" in result


def test_filter_tags_by_training_purpose_character_without_trigger_keeps_identity() -> None:
    general = ["1girl", "blue_eyes", "long_hair", "outdoors"]
    copyright = ["genshin_impact"]
    character = ["raiden_shogun"]

    result = filter_tags_by_training_purpose("character", general, copyright, character)

    assert "raiden_shogun" in result
    assert "1girl" in result
    assert "blue_eyes" in result
    assert "long_hair" in result
    assert "outdoors" in result

    assert "genshin_impact" in result


def test_filter_tags_by_training_purpose_character_with_trigger_removes_character_name() -> None:
    general = ["1girl", "blue_eyes", "outdoors"]
    copyright = ["genshin_impact"]
    character = ["raiden_shogun"]

    result = filter_tags_by_training_purpose(
        "character", general, copyright, character, trigger_word="my_character"
    )

    assert result == general + copyright


def test_filter_tags_by_training_purpose_concept_preserves_context() -> None:
    general = ["outdoors", "forest", "sunlight", "nature"]
    copyright = ["genshin_impact"]
    character = ["raiden_shogun"]

    result = filter_tags_by_training_purpose("concept", general, copyright, character)

    assert result == general + copyright + character


def test_filter_tags_by_training_purpose_respects_aliases() -> None:
    """Aliases (style_lora, character_lora, etc.) should map correctly."""
    general = ["1girl", "lineart"]
    copyright = ["original"]
    character = ["test_character"]

    # Test style aliases
    for alias in ["style", "style_lora", "art", "art_style"]:
        result = filter_tags_by_training_purpose(alias, general, copyright, character)
        assert "1girl" in result
        assert "lineart" not in result
        assert "original" in result
        assert "test_character" in result

    # Test character aliases
    for alias in ["character", "character_lora", "char"]:
        result = filter_tags_by_training_purpose(
            alias, general, copyright, character, trigger_word="trigger"
        )
        assert "test_character" not in result
        assert "1girl" in result
        assert "original" in result

    # Test concept aliases
    for alias in ["concept", "concept_lora"]:
        result = filter_tags_by_training_purpose(alias, general, copyright, character)
        assert "1girl" in result
        assert "original" in result
        assert "test_character" in result


def test_filter_tags_by_training_purpose_unknown_falls_back_to_general() -> None:
    """Unknown training_purpose should fall back to general behavior (keep all)."""
    general = ["1girl"]
    copyright = ["original"]
    character = ["test_character"]

    result = filter_tags_by_training_purpose("totally_unknown", general, copyright, character)

    assert "1girl" in result
    assert "original" in result
    assert "test_character" in result


def test_character_purpose_filters_final_caption_and_persisted_rows_with_trigger() -> None:
    partial = {
        "general_names": ["1girl", "outdoors"],
        "copyright_names": ["genshin_impact"],
        "character_names": ["raiden_shogun"],
        "general_rows": [{"tag": "1girl"}, {"tag": "outdoors"}],
        "copyright_rows": [{"tag": "genshin_impact"}],
        "character_rows": [{"tag": "raiden_shogun"}],
        "rating": None,
        "noise_stripped": 0,
    }
    req = SmartTagRequest(
        image_ids=[1], training_purpose="character", trigger_word="my_character"
    )

    result = smart_tag_service._assemble_result_dict(partial, "standing outside", 1, req)

    assert result["character_tags"] == []
    assert result["character_tag_rows"] == []
    assert "raiden shogun" not in result["caption"]
    assert "genshin impact" in result["caption"]
    assert result["caption"].startswith("my_character")


def test_style_purpose_filters_style_tag_from_final_output_but_keeps_context() -> None:
    partial = {
        "general_names": ["1girl", "lineart", "outdoors"],
        "copyright_names": ["original"],
        "character_names": ["test_character"],
        "general_rows": [{"tag": "1girl"}, {"tag": "lineart"}, {"tag": "outdoors"}],
        "copyright_rows": [{"tag": "original"}],
        "character_rows": [{"tag": "test_character"}],
        "rating": None,
        "noise_stripped": 0,
    }
    req = SmartTagRequest(image_ids=[1], training_purpose="style")

    result = smart_tag_service._assemble_result_dict(partial, "a person standing outside", 1, req)

    assert "lineart" not in result["general_tags"]
    assert "lineart" not in result["caption"]
    assert result["copyright_tags"] == ["original"]
    assert result["character_tags"] == ["test_character"]


# ---------------------------------------------------------------------------
# Caption assembly + trigger injection
# ---------------------------------------------------------------------------


def test_assemble_caption_basic_layout() -> None:
    out = assemble_caption(
        rating="questionable",
        general_tags=["1girl", "long_hair", "blue_eyes"],
        character_tags=["furina_(genshin_impact)"],
        nl_text="A young girl stands on a balcony at sunset.",
        trigger_word="myloratrigger",
        auto_strip_noise=True,
    )
    # Trigger first, then characters, then generals, then NL.
    assert out.startswith("myloratrigger,")
    assert "furina (genshin impact)" in out
    assert "1girl" in out
    assert "long hair" in out
    assert "blue eyes" in out
    assert out.endswith("A young girl stands on a balcony at sunset.")


def test_assemble_caption_strips_noise_when_enabled() -> None:
    out = assemble_caption(
        rating="general",
        general_tags=["masterpiece", "1girl", "score_9", "blue_eyes", "anime"],
        character_tags=[],
        nl_text="",
        trigger_word="",
        auto_strip_noise=True,
    )
    assert "masterpiece" not in out
    assert "score_9" not in out
    assert "anime" not in out
    assert "1girl" in out
    assert "blue eyes" in out


def test_assemble_caption_keeps_noise_when_disabled() -> None:
    out = assemble_caption(
        rating="general",
        general_tags=["masterpiece", "1girl"],
        character_tags=[],
        nl_text="",
        trigger_word="",
        auto_strip_noise=False,
    )
    assert "masterpiece" in out
    assert "1girl" in out


def test_assemble_caption_does_not_duplicate_existing_trigger() -> None:
    """If trigger is already in the WD14 tag list, do not prepend it again."""
    out = assemble_caption(
        rating=None,
        general_tags=["mytrigger", "1girl", "blue_eyes"],
        character_tags=[],
        nl_text="",
        trigger_word="mytrigger",
        auto_strip_noise=True,
    )
    # mytrigger appears exactly once in the final caption.
    assert out.count("mytrigger") == 1
    # And the original tag-list ordering is preserved (mytrigger was at
    # index 0 of general_tags, so it stays at the front).
    assert "mytrigger" in out
    # 1girl and blue eyes also stay.
    assert "1girl" in out
    assert "blue eyes" in out


def test_assemble_caption_underscore_to_space() -> None:
    """Underscores in tag names get swapped to spaces, except score_N."""
    out = assemble_caption(
        rating=None,
        general_tags=["long_hair", "blue_eyes", "score_9"],
        character_tags=[],
        nl_text="",
        trigger_word="",
        auto_strip_noise=False,
    )
    assert "long hair" in out
    assert "blue eyes" in out
    # score_9 keeps the underscore (Pony recipe convention)
    assert "score_9" in out


def test_assemble_caption_dedupes_repeated_tags() -> None:
    out = assemble_caption(
        rating=None,
        general_tags=["1girl", "1girl", "BLUE_EYES", "blue eyes"],
        character_tags=["1girl"],
        nl_text="",
        trigger_word="",
        auto_strip_noise=True,
    )
    assert out.count("1girl") == 1
    assert out.count("blue eyes") == 1


def test_assemble_caption_handles_empty_inputs() -> None:
    out = assemble_caption(
        rating=None,
        general_tags=[],
        character_tags=[],
        nl_text="",
        trigger_word="",
        auto_strip_noise=True,
    )
    assert out == ""


def test_assemble_caption_only_nl_when_no_tags() -> None:
    out = assemble_caption(
        rating=None,
        general_tags=[],
        character_tags=[],
        nl_text="A photograph of a sunset.",
        trigger_word="",
        auto_strip_noise=True,
    )
    assert out == "A photograph of a sunset."


def test_assemble_caption_only_trigger_and_tags_when_no_nl() -> None:
    out = assemble_caption(
        rating=None,
        general_tags=["1girl", "blue_eyes"],
        character_tags=[],
        nl_text="",
        trigger_word="myloratrigger",
        auto_strip_noise=True,
    )
    assert "myloratrigger" in out
    assert "1girl" in out
    assert "blue eyes" in out
    assert "." not in out  # no NL sentence appended


# ---------------------------------------------------------------------------
# Multi-tagger booru consensus
# ---------------------------------------------------------------------------


def test_consensus_keeps_copyright_tags_separate_but_or_merged() -> None:
    fused = compute_consensus_tags(
        [
            {
                "model": "tagger-a",
                "weight": 1,
                "general_tags": [
                    {"tag": "1girl", "confidence": 0.95},
                    {"tag": "solo", "confidence": 0.80},
                ],
                "copyright_tags": [{"tag": "touhou", "confidence": 0.70}],
                "character_tags": [{"tag": "reimu_hakurei", "confidence": 0.60}],
                "rating": "general",
            },
            {
                "model": "tagger-b",
                "weight": 1,
                "general_tags": [
                    {"tag": "1girl", "confidence": 0.90},
                    {"tag": "smile", "confidence": 0.88},
                ],
                "copyright_tags": [{"tag": "project_moon", "confidence": 0.72}],
                "character_tags": [],
                "rating": "general",
            },
        ],
        consensus_min=2,
        skip_categories=["character", "copyright"],
    )

    assert [tag["tag"] for tag in fused["general_tags"]] == ["1girl"]
    assert {tag["tag"] for tag in fused["copyright_tags"]} == {"touhou", "project_moon"}
    assert {tag["tag"] for tag in fused["character_tags"]} == {"reimu_hakurei"}


def test_multi_tagger_pipeline_uses_copyright_threshold_and_caption(monkeypatch) -> None:
    calls = []

    class FakeTagger:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def load(self) -> None:
            calls.append(("load", self.model_name))

        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            calls.append((self.model_name, threshold, character_threshold, copyright_threshold))
            if self.model_name == "tagger-a":
                return {
                    "general_tags": [
                        {"tag": "1girl", "confidence": 0.95},
                        {"tag": "solo", "confidence": 0.80},
                    ],
                    "copyright_tags": [{"tag": "touhou", "confidence": 0.70}],
                    "character_tags": [{"tag": "reimu_hakurei", "confidence": 0.60}],
                    "rating": "general",
                }
            return {
                "general_tags": [
                    {"tag": "1girl", "confidence": 0.90},
                    {"tag": "smile", "confidence": 0.88},
                ],
                "copyright_tags": [{"tag": "project_moon", "confidence": 0.72}],
                "character_tags": [],
                "rating": "general",
            }

    def fake_resolve(model_name, **kwargs):
        assert kwargs["copyright_threshold"] == 0.42
        return FakeTagger(model_name)

    monkeypatch.setattr(smart_tag_service, "_resolve_tagger_by_model", fake_resolve)

    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=False,
        taggers=[
            {
                "model": "tagger-a",
                "weight": 1,
                "general_threshold": 0.35,
                "character_threshold": 0.85,
                "copyright_threshold": 0.42,
            },
            {
                "model": "tagger-b",
                "weight": 1,
                "general_threshold": 0.35,
                "character_threshold": 0.85,
                "copyright_threshold": 0.42,
            },
        ],
        consensus_min=2,
        consensus_skip_categories=["character", "copyright"],
        copyright_threshold=0.42,
    )

    # Mirror the orchestrator: pre-compute per-tagger outputs, then call
    # _process_one_image with precomputed_tagger_outputs. The tagger-loading
    # loop now lives in _run_smart_tag_pipeline so models aren't reloaded per
    # image (v3.2.2 multi-tagger refactor).
    precomputed = []
    for entry in req.taggers:
        model_name = entry["model"]
        gen_th = float(entry["general_threshold"])
        char_th = float(entry["character_threshold"])
        copy_th = float(entry["copyright_threshold"])
        one_tagger = smart_tag_service._resolve_tagger_by_model(
            model_name,
            general_threshold=gen_th,
            character_threshold=char_th,
            copyright_threshold=copy_th,
        )
        one_tagger.load()
        out = one_tagger.tag(
            "/tmp/fake.png",
            threshold=gen_th,
            character_threshold=char_th,
            copyright_threshold=copy_th,
        )
        precomputed.append({
            "model": model_name,
            "weight": float(entry.get("weight") or 1.0),
            "general_tags": out.get("general_tags") or [],
            "copyright_tags": out.get("copyright_tags") or [],
            "character_tags": out.get("character_tags") or [],
            "rating": out.get("rating"),
        })

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=None,
        vlm_provider=None,
        precomputed_tagger_outputs=precomputed,
    )

    assert result["general_tags"] == ["1girl"]
    assert set(result["copyright_tags"]) == {"touhou", "project_moon"}
    assert result["character_tags"] == ["reimu_hakurei"]
    assert "touhou" in result["caption"]
    assert "project moon" in result["caption"]
    assert "solo" not in result["caption"]
    assert ("tagger-a", 0.35, 0.85, 0.42) in calls
    assert ("tagger-b", 0.35, 0.85, 0.42) in calls


def test_multi_tagger_pipeline_runs_taggers_then_vlm_with_fused_tag_context(monkeypatch) -> None:
    events = []

    class FakeTagger:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def load(self) -> None:
            events.append(("load", self.model_name))

        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            events.append(("tag", self.model_name))
            if self.model_name == "tagger-a":
                return {
                    "general_tags": [{"tag": "1girl", "confidence": 0.95}],
                    "copyright_tags": [{"tag": "blue_archive", "confidence": 0.80}],
                    "character_tags": [{"tag": "shiroko_(blue_archive)", "confidence": 0.70}],
                    "rating": "general",
                }
            return {
                "general_tags": [
                    {"tag": "1girl", "confidence": 0.90},
                    {"tag": "blue_eyes", "confidence": 0.88},
                ],
                "copyright_tags": [],
                "character_tags": [],
                "rating": "general",
            }

    class FakeVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                user_prompt="original",
                user_prompt_with_tags="original with tags",
                include_tags_as_context=True,
            )
            self.received_tags = None
            self.prompt_seen = None

        async def caption_image(self, image_path, *, tags=None):
            events.append(("vlm", tuple(tags or [])))
            self.received_tags = list(tags or [])
            self.prompt_seen = self.config.user_prompt
            return SimpleNamespace(caption="A natural language sentence.")

    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_tagger_by_model",
        lambda model_name, **kwargs: FakeTagger(model_name),
    )

    vlm = FakeVlm()
    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=True,
        taggers=[
            {"model": "tagger-a", "general_threshold": 0.35, "character_threshold": 0.85},
            {"model": "tagger-b", "general_threshold": 0.35, "character_threshold": 0.85},
        ],
        consensus_min=1,
        consensus_skip_categories=["character", "copyright"],
    )

    # Mirror the orchestrator: pre-compute per-tagger outputs, then call
    # _process_one_image with precomputed_tagger_outputs (post-refactor flow).
    precomputed = []
    for entry in req.taggers:
        model_name = entry["model"]
        gen_th = float(entry["general_threshold"])
        char_th = float(entry["character_threshold"])
        copy_th = float(entry.get("copyright_threshold") or req.copyright_threshold or gen_th)
        one_tagger = smart_tag_service._resolve_tagger_by_model(
            model_name,
            general_threshold=gen_th,
            character_threshold=char_th,
            copyright_threshold=copy_th,
        )
        one_tagger.load()
        out = one_tagger.tag(
            "/tmp/fake.png",
            threshold=gen_th,
            character_threshold=char_th,
            copyright_threshold=copy_th,
        )
        precomputed.append({
            "model": model_name,
            "weight": float(entry.get("weight") or 1.0),
            "general_tags": out.get("general_tags") or [],
            "copyright_tags": out.get("copyright_tags") or [],
            "character_tags": out.get("character_tags") or [],
            "rating": out.get("rating"),
        })

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=None,
        vlm_provider=vlm,
        precomputed_tagger_outputs=precomputed,
    )

    assert events == [
        ("load", "tagger-a"),
        ("tag", "tagger-a"),
        ("load", "tagger-b"),
        ("tag", "tagger-b"),
        ("vlm", ("1girl", "blue_eyes", "blue_archive", "shiroko_(blue_archive)")),
    ]
    assert vlm.received_tags == ["1girl", "blue_eyes", "blue_archive", "shiroko_(blue_archive)"]
    assert "1girl" in vlm.prompt_seen
    assert "blue_archive" in vlm.prompt_seen
    assert "shiroko_(blue_archive)" in vlm.prompt_seen
    assert result["nl_text"] == "A natural language sentence."
    assert "A natural language sentence." in result["caption"]


def test_multi_tagger_pipeline_respects_vlm_include_tags_context_off(monkeypatch) -> None:
    class FakeTagger:
        def load(self) -> None:
            pass

        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            return {
                "general_tags": [{"tag": "1girl", "confidence": 0.95}],
                "copyright_tags": [{"tag": "blue_archive", "confidence": 0.80}],
                "character_tags": [{"tag": "shiroko_(blue_archive)", "confidence": 0.70}],
                "rating": "general",
            }

    class FakeVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                user_prompt="original",
                user_prompt_with_tags="original with tags",
                include_tags_as_context=False,
            )
            self.received_tags = "not-called"
            self.prompt_seen = None

        async def caption_image(self, image_path, *, tags=None):
            self.received_tags = tags
            self.prompt_seen = self.config.user_prompt
            return SimpleNamespace(caption="No tag context sentence.")

    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_tagger_by_model",
        lambda model_name, **kwargs: FakeTagger(),
    )

    vlm = FakeVlm()
    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=True,
        taggers=[{"model": "tagger-a"}],
        consensus_min=1,
    )

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=None,
        vlm_provider=vlm,
    )

    assert vlm.received_tags is None
    assert "1girl" not in vlm.prompt_seen
    assert "blue_archive" not in vlm.prompt_seen
    assert "shiroko_(blue_archive)" not in vlm.prompt_seen
    assert result["nl_text"] == "No tag context sentence."


def test_single_tagger_pipeline_preserves_confidence_rows() -> None:
    class FakeTagger:
        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            return {
                "general_tags": [
                    {"tag": "1girl", "confidence": 0.93},
                    {"tag": "blue_eyes", "confidence": 0.81},
                ],
                "copyright_tags": [{"tag": "blue_archive", "confidence": 0.74}],
                "character_tags": [{"tag": "shiroko_(blue_archive)", "confidence": 0.66}],
                "rating": "general",
            }

    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=False,
        copyright_threshold=0.42,
    )

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=FakeTagger(),
        vlm_provider=None,
    )

    assert result["general_tags"] == ["1girl", "blue_eyes"]
    assert result["copyright_tags"] == ["blue_archive"]
    assert result["character_tags"] == ["shiroko_(blue_archive)"]
    assert result["general_tag_rows"] == [
        {"tag": "1girl", "confidence": 0.93, "category": "general"},
        {"tag": "blue_eyes", "confidence": 0.81, "category": "general"},
    ]
    assert result["copyright_tag_rows"] == [
        {"tag": "blue_archive", "confidence": 0.74, "category": "copyright"}
    ]
    assert result["character_tag_rows"] == [
        {"tag": "shiroko_(blue_archive)", "confidence": 0.66, "category": "character"}
    ]


def test_persist_result_writes_model_confidences(monkeypatch) -> None:
    captured = []
    captured_kwargs = {}

    def _fake_add_tags_batch(rows, **kwargs):
        captured.extend(rows)
        captured_kwargs.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "database",
        SimpleNamespace(add_tags_batch=_fake_add_tags_batch),
    )

    smart_tag_service._persist_result(
        123,
        {
            "caption": "general, shiroko, 1girl, blue eyes",
            "general_tags": ["1girl", "blue_eyes"],
            "copyright_tags": ["blue_archive"],
            "character_tags": ["shiroko_(blue_archive)"],
            "general_tag_rows": [
                {"tag": "1girl", "confidence": 0.93, "category": "general"},
                {"tag": "blue_eyes", "confidence": 0.81, "category": "general"},
            ],
            "copyright_tag_rows": [
                {"tag": "blue_archive", "confidence": 0.74, "category": "copyright"}
            ],
            "character_tag_rows": [
                {"tag": "shiroko_(blue_archive)", "confidence": 0.66, "category": "character"}
            ],
        },
        "replace",
    )

    assert len(captured) == 1
    assert captured[0]["image_id"] == 123
    assert captured[0]["ai_caption"] == "general, shiroko, 1girl, blue eyes"
    assert captured[0]["tags"] == [
        {"tag": "shiroko_(blue_archive)", "confidence": 0.66, "category": "character"},
        {"tag": "1girl", "confidence": 0.93, "category": "general"},
        {"tag": "blue_eyes", "confidence": 0.81, "category": "general"},
        {"tag": "blue_archive", "confidence": 0.74, "category": "copyright"},
    ]
    # Provenance contract: smart-tag writes are pipeline-scoped tagger rows,
    # so user-added manual tags survive a re-run (audit P1-5).
    assert captured_kwargs == {"default_source": "tagger", "replace_scope": "pipeline"}


def test_coerce_request_uses_model_specific_smart_tag_defaults(monkeypatch) -> None:
    monkeypatch.setattr(smart_tag_service, "count_selection_token_ids", lambda token: 10)

    req = _coerce_request({
        "selection_token": "token-abc",
        "tagger_model": "oppai-oracle-v1.1",
        "enable_vlm": False,
    })

    assert req.general_threshold == pytest.approx(0.7927)
    assert req.character_threshold == pytest.approx(1.0)
    assert req.copyright_threshold == pytest.approx(0.7927)
    assert req.max_tags_per_image == 60


def test_coerce_request_uses_each_consensus_tagger_default(monkeypatch) -> None:
    monkeypatch.setattr(smart_tag_service, "count_selection_token_ids", lambda token: 10)

    req = _coerce_request({
        "selection_token": "token-abc",
        "enable_vlm": False,
        "taggers": [
            {"model": "oppai-oracle-v1.1"},
            {"model": "pixai-tagger-v0.9"},
        ],
    })

    assert req.taggers[0]["general_threshold"] == pytest.approx(0.7927)
    assert req.taggers[0]["character_threshold"] == pytest.approx(1.0)
    assert req.taggers[1]["general_threshold"] == pytest.approx(0.45)
    assert req.taggers[1]["character_threshold"] == pytest.approx(0.85)
    assert req.max_tags_per_image == 60


def test_smart_tag_strips_noise_rows_and_caps_general_tags() -> None:
    class FakeTagger:
        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            return {
                "general_tags": [
                    {"tag": "absurdres", "confidence": 0.99},
                    {"tag": "2024", "confidence": 0.98},
                    # "::" is prompt-syntax junk; emoticons like ":3" are
                    # real vocabulary and would legitimately survive (P1-4).
                    {"tag": "::", "confidence": 0.97},
                    {"tag": "keep_a", "confidence": 0.96},
                    {"tag": "keep_b", "confidence": 0.95},
                ],
                "copyright_tags": [{"tag": "series_name", "confidence": 0.40}],
                "character_tags": [{"tag": "character_name", "confidence": 0.30}],
                "rating": "general",
            }

    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=False,
        auto_strip_noise=True,
        max_tags_per_image=3,
    )

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=FakeTagger(),
        vlm_provider=None,
    )

    assert result["general_tags"] == ["keep_a"]
    assert result["copyright_tags"] == ["series_name"]
    assert result["character_tags"] == ["character_name"]
    assert "absurdres" not in result["caption"]
    assert "2024" not in result["caption"]
    assert ":3" not in result["caption"]
    assert "keep b" not in result["caption"]


def test_smart_tag_max_tags_zero_keeps_all_non_noise_tags() -> None:
    class FakeTagger:
        def tag(self, image_path, *, threshold, character_threshold, copyright_threshold):
            return {
                "general_tags": [
                    {"tag": "keep_c", "confidence": 0.10},
                    {"tag": "keep_a", "confidence": 0.96},
                    {"tag": "keep_b", "confidence": 0.95},
                ],
                "copyright_tags": [],
                "character_tags": [],
                "rating": "general",
            }

    req = SmartTagRequest(
        image_paths=["/tmp/fake.png"],
        enable_wd14=True,
        enable_vlm=False,
        auto_strip_noise=True,
        max_tags_per_image=0,
    )

    result = smart_tag_service._process_one_image(
        image_path="/tmp/fake.png",
        image_id=0,
        req=req,
        tagger=FakeTagger(),
        vlm_provider=None,
    )

    assert result["general_tags"] == ["keep_c", "keep_a", "keep_b"]
    assert "keep c" in result["caption"]


# ---------------------------------------------------------------------------
# Large-source contracts — selection/dataset tokens must stay chunked and
# path-source caption results must not accumulate in job memory.
# ---------------------------------------------------------------------------


def test_coerce_request_accepts_selection_token_without_explicit_ids(monkeypatch) -> None:
    monkeypatch.setattr(smart_tag_service, "count_selection_token_ids", lambda token: 123)

    req = _coerce_request({
        "selection_token": "token-abc",
        "enable_wd14": False,
        "enable_vlm": False,
    })

    assert req.image_ids == []
    assert req.selection_token == "token-abc"
    assert req.selection_count == 123


def test_coerce_request_accepts_dataset_scan_token_alias(monkeypatch) -> None:
    monkeypatch.setattr(smart_tag_service, "_count_dataset_scan_token_paths", lambda token: 77)

    req = _coerce_request({
        "scan_token": "0123456789abcdef0123456789abcdef",
        "enable_wd14": False,
        "enable_vlm": False,
    })

    assert req.dataset_scan_token == "0123456789abcdef0123456789abcdef"
    assert req.dataset_scan_count == 77


def test_run_pipeline_streams_selection_token_id_chunks(monkeypatch) -> None:
    observed_resolve_chunks = []
    observed_snapshot_flags = []
    persisted_ids = []

    monkeypatch.setattr(smart_tag_service, "SMART_TAG_ID_CHUNK_SIZE", 2)

    def fake_iter_selection_token_id_chunks(token, chunk_size, snapshot=False):
        observed_snapshot_flags.append(snapshot)
        return iter([[1, 2], [3, 4], [5]])

    monkeypatch.setattr(
        smart_tag_service,
        "iter_selection_token_id_chunks",
        fake_iter_selection_token_id_chunks,
    )

    def fake_resolve(ids):
        observed_resolve_chunks.append(list(ids))
        assert len(ids) <= 2
        return {int(image_id): f"/tmp/image-{image_id}.png" for image_id in ids}

    monkeypatch.setattr(smart_tag_service, "_resolve_image_paths", fake_resolve)
    # No _process_one_image mock needed: with enable_wd14/enable_vlm both off,
    # the windowed pipeline assembles an empty caption and persists via the
    # mocked _persist_result below. This test only asserts chunk streaming +
    # persist order, not caption content.
    monkeypatch.setattr(
        smart_tag_service,
        "_persist_result",
        lambda image_id, result, merge_strategy: persisted_ids.append(image_id),
    )

    job = SmartTagJobState(job_id="selection-job")
    req = SmartTagRequest(
        selection_token="selection-token",
        selection_count=5,
        enable_wd14=False,
        enable_vlm=False,
    )

    _run_pipeline(job, req)

    assert observed_resolve_chunks == [[1, 2], [3, 4], [5]]
    # The pipeline persists tags per window, so the token source must request
    # a pre-mutation ID snapshot or tag-filtered tokens skip images mid-run.
    assert observed_snapshot_flags == [True]
    assert persisted_ids == [1, 2, 3, 4, 5]
    assert job.status == "completed"
    assert job.total == 5
    assert job.processed == 5


def test_skip_existing_drops_already_tagged_images_and_counts_them(monkeypatch) -> None:
    persisted_ids = []
    looked_up_chunks = []

    def fake_already_tagged(ids):
        looked_up_chunks.append(list(ids))
        return {1, 3}

    monkeypatch.setattr(smart_tag_service, "_already_tagged_ids", fake_already_tagged)
    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_image_paths",
        lambda ids: {int(i): f"/tmp/image-{i}.png" for i in ids},
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_persist_result",
        lambda image_id, result, merge_strategy: persisted_ids.append(image_id),
    )

    job = SmartTagJobState(job_id="skip-existing-job")
    req = SmartTagRequest(
        image_ids=[1, 2, 3, 4],
        enable_wd14=False,
        enable_vlm=False,
        skip_existing=True,
    )

    _run_pipeline(job, req)

    assert looked_up_chunks == [[1, 2, 3, 4]]
    assert persisted_ids == [2, 4]
    assert job.status == "completed"
    assert job.skipped == 2
    assert job.succeeded == 2
    # Skipped images count into processed so N/M progress still completes.
    assert job.total == 4
    assert job.processed == 4
    assert "2 skipped (already tagged)" in job.message
    assert job.snapshot()["skipped"] == 2


def test_skip_existing_false_processes_already_tagged_images(monkeypatch) -> None:
    persisted_ids = []
    lookup_calls = []

    monkeypatch.setattr(
        smart_tag_service,
        "_already_tagged_ids",
        lambda ids: lookup_calls.append(list(ids)) or {1, 2, 3, 4},
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_image_paths",
        lambda ids: {int(i): f"/tmp/image-{i}.png" for i in ids},
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_persist_result",
        lambda image_id, result, merge_strategy: persisted_ids.append(image_id),
    )

    job = SmartTagJobState(job_id="no-skip-job")
    req = SmartTagRequest(
        image_ids=[1, 2, 3, 4],
        enable_wd14=False,
        enable_vlm=False,
        skip_existing=False,
    )

    _run_pipeline(job, req)

    assert lookup_calls == []  # disabled -> no DB lookup at all
    assert persisted_ids == [1, 2, 3, 4]
    assert job.skipped == 0
    assert job.processed == 4
    assert "skipped" not in job.message


def test_skip_existing_fails_open_when_tagged_lookup_raises(monkeypatch) -> None:
    persisted_ids = []

    def boom(ids):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(smart_tag_service, "_already_tagged_ids", boom)
    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_image_paths",
        lambda ids: {int(i): f"/tmp/image-{i}.png" for i in ids},
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_persist_result",
        lambda image_id, result, merge_strategy: persisted_ids.append(image_id),
    )

    job = SmartTagJobState(job_id="fail-open-job")
    req = SmartTagRequest(
        image_ids=[1, 2],
        enable_wd14=False,
        enable_vlm=False,
        skip_existing=True,
    )

    _run_pipeline(job, req)

    # Fail-open: worst case is re-tagging, never silently dropping work.
    assert persisted_ids == [1, 2]
    assert job.skipped == 0
    assert job.status == "completed"


def test_skip_existing_never_checks_path_only_sources(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(smart_tag_service, "_get_caption_results_dir", lambda: tmp_path)
    lookup_calls = []
    monkeypatch.setattr(
        smart_tag_service,
        "_already_tagged_ids",
        lambda ids: lookup_calls.append(list(ids)) or set(),
    )

    job = SmartTagJobState(job_id="path-skip-job")
    req = SmartTagRequest(
        image_paths=[f"/tmp/local-{index}.png" for index in range(3)],
        enable_wd14=False,
        enable_vlm=False,
        skip_existing=True,
    )

    _run_pipeline(job, req)

    # Dataset Maker local files have no DB tag state -> nothing to look up.
    assert lookup_calls == []
    assert job.caption_result_count == 3
    assert job.skipped == 0


def test_path_caption_results_are_paged_from_file_not_kept_in_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(smart_tag_service, "_get_caption_results_dir", lambda: tmp_path)
    # No _process_one_image mock: the windowed pipeline assembles the caption and
    # appends each path-source result to the on-disk JSONL itself. We assert the
    # results are paged from that file (not held in memory), keyed by path.

    job = SmartTagJobState(job_id="path-job")
    req = SmartTagRequest(
        image_paths=[f"/tmp/local-{index}.png" for index in range(3)],
        enable_wd14=False,
        enable_vlm=False,
    )

    _run_pipeline(job, req)

    assert not hasattr(job, "caption_results")
    assert job.caption_result_count == 3
    assert len(job.recent_caption_results) == 3

    first_page = get_caption_results_page(job, offset=0, limit=2)
    assert first_page["total"] == 3
    assert first_page["has_more"] is True
    assert [row["path"] for row in first_page["results"]] == [
        "/tmp/local-0.png",
        "/tmp/local-1.png",
    ]

    second_page = get_caption_results_page(job, offset=2, limit=2)
    assert second_page["has_more"] is False
    assert [row["path"] for row in second_page["results"]] == ["/tmp/local-2.png"]


def test_path_caption_results_keep_booru_and_nl_channels_separate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(smart_tag_service, "_get_caption_results_dir", lambda: tmp_path)
    nl_text = "A woman stands beneath warm window light."

    class _FixedVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                concurrent_requests=1,
                include_tags_as_context=True,
                system_prompt="",
                user_prompt="",
                user_prompt_with_tags="",
                output_format="both",
            )

        async def caption_image(self, image_path, *, tags=None):
            return SimpleNamespace(caption=nl_text)

    job = SmartTagJobState(job_id="path-separated-job")
    job.total = 1
    req = SmartTagRequest(
        image_paths=["/tmp/local-separated.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
    )

    smart_tag_service._run_windowed_pipeline(
        job,
        req,
        tagger=_FakeBatchTagger(),
        vlm_provider=_FixedVlm(),
        nl_tagger=None,
    )

    row = get_caption_results_page(job, offset=0, limit=10)["results"][0]
    assert row == {
        "path": "/tmp/local-separated.png",
        "caption": f"1girl, {nl_text}",
        "booru_text": "1girl",
        "nl_text": nl_text,
    }
    assert job.recent_caption_results[0]["booru_text"] == "1girl"
    assert job.recent_caption_results[0]["nl_text"] == nl_text


def test_path_caption_results_keep_legacy_rows_readable(tmp_path) -> None:
    target = tmp_path / "legacy-path-results.jsonl"
    target.write_text(
        '{"path": "/tmp/legacy.png", "caption": "legacy combined text"}\n',
        encoding="utf-8",
    )
    job = SmartTagJobState(job_id="legacy-path-job", caption_result_count=1)
    job.caption_results_path = str(target)

    row = get_caption_results_page(job, offset=0, limit=10)["results"][0]

    assert row == {
        "path": "/tmp/legacy.png",
        "caption": "legacy combined text",
        "booru_text": "",
        "nl_text": "",
    }


def test_path_caption_results_raise_when_result_file_is_missing(tmp_path: Path) -> None:
    target = tmp_path / "missing-path-results.jsonl"
    job = SmartTagJobState(job_id="missing-results", caption_result_count=1)
    job.caption_results_path = str(target)

    with pytest.raises(smart_tag_service.SmartTagResultReadError) as exc_info:
        get_caption_results_page(job, offset=0, limit=10)

    message = str(exc_info.value)
    assert "job_id='missing-results'" in message
    assert str(target) in message


def test_path_caption_results_raise_on_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / "malformed-path-results.jsonl"
    target.write_text('{"path": "/tmp/broken.png"', encoding="utf-8")
    job = SmartTagJobState(job_id="malformed-results", caption_result_count=1)
    job.caption_results_path = str(target)

    with pytest.raises(smart_tag_service.SmartTagResultReadError) as exc_info:
        get_caption_results_page(job, offset=0, limit=10)

    message = str(exc_info.value)
    assert "job_id='malformed-results'" in message
    assert "line=1" in message


def test_path_caption_results_ignore_uncommitted_trailing_row(tmp_path: Path) -> None:
    target = tmp_path / "in-progress-path-results.jsonl"
    target.write_text(
        '{"path": "/tmp/committed.png", "caption": "committed"}\n'
        '{"path": "/tmp/in-progress.png"',
        encoding="utf-8",
    )
    job = SmartTagJobState(job_id="in-progress-results", caption_result_count=1)
    job.caption_results_path = str(target)

    page = get_caption_results_page(job, offset=0, limit=10)

    assert [row["path"] for row in page["results"]] == ["/tmp/committed.png"]


def test_path_caption_results_raise_when_file_is_truncated(tmp_path: Path) -> None:
    target = tmp_path / "truncated-path-results.jsonl"
    target.write_text(
        '{"path": "/tmp/only.png", "caption": "only row"}\n',
        encoding="utf-8",
    )
    job = SmartTagJobState(job_id="truncated-results", caption_result_count=2)
    job.caption_results_path = str(target)

    with pytest.raises(smart_tag_service.SmartTagResultReadError) as exc_info:
        get_caption_results_page(job, offset=0, limit=10)

    message = str(exc_info.value)
    assert "job_id='truncated-results'" in message
    assert "expected_rows=2" in message
    assert "actual_rows=1" in message


def test_run_pipeline_keeps_only_bounded_recent_errors(monkeypatch) -> None:
    # The single-tagger path no longer routes through _process_one_image; it
    # assembles + persists each image via _append_caption_result (path sources).
    # Throw from that per-image persist seam so the error-cap behaviour is still
    # exercised end-to-end through _run_pipeline.
    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(smart_tag_service, "_append_caption_result", _boom)

    count = SMART_TAG_MAX_ERRORS + 5
    job = SmartTagJobState(job_id="error-job")
    req = SmartTagRequest(
        image_paths=[f"/tmp/error-{index}.png" for index in range(count)],
        enable_wd14=False,
        enable_vlm=False,
    )

    _run_pipeline(job, req)

    assert job.failed == count
    assert len(job.errors) == SMART_TAG_MAX_ERRORS
    assert job.errors[0]["image_id"] == f"/tmp/error-{count - SMART_TAG_MAX_ERRORS}.png"


# ---------------------------------------------------------------------------
# Fix B2: VLM endpoint missing -> reject at _coerce_request, not silent fallback
# ---------------------------------------------------------------------------


def test_coerce_request_rejects_vlm_enabled_without_endpoint(monkeypatch) -> None:
    """If enable_vlm=True and nl_mode='vlm' but VLM Settings has no endpoint,
    _coerce_request must raise ValueError so the /start route returns 400
    instead of letting the worker silently fall back to booru-only output.
    """
    fake_config = SimpleNamespace(endpoint="", api_key="")
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: fake_config,
    )

    with pytest.raises(ValueError, match="VLM Settings"):
        _coerce_request({
            "image_paths": [],
            "image_ids": [1],
            "enable_vlm": True,
            "enable_wd14": True,
            "natural_language_mode": "vlm",
        })


def test_coerce_request_rejects_vlm_enabled_with_endpoint_but_no_api_key(monkeypatch) -> None:
    fake_config = SimpleNamespace(
        provider="openai_compat",
        endpoint="https://example.invalid/v1",
        api_key="",
        use_vertex=False,
        vertex_project="",
    )
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: fake_config,
    )

    with pytest.raises(ValueError, match="VLM Settings"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": True,
            "natural_language_mode": "vlm",
        })


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://[::1]:11434/v1",
        "http://host.docker.internal:11434/v1",
        "http://my-rig.local:11434/v1",
        "http://10.0.0.5:8000/v1",
        "http://192.168.1.42:1234/v1",
        "http://172.16.0.5:8080/v1",
    ],
)
def test_coerce_request_allows_local_openai_compat_without_api_key(monkeypatch, endpoint) -> None:
    """Ollama / vLLM / LM Studio on localhost or LAN need no api_key.

    Reproduces the v3.2.2 bug where a configured local endpoint was rejected
    with "VLM Settings has no endpoint or API key configured" because the
    gate required api_key unconditionally.
    """
    fake_config = SimpleNamespace(
        provider="openai_compat",
        endpoint=endpoint,
        api_key="",
        use_vertex=False,
        vertex_project="",
    )
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: fake_config,
    )

    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "vlm",
    })
    assert req.enable_vlm is True


def test_coerce_request_allows_gemini_vertex_without_api_key(monkeypatch) -> None:
    """Vertex AI auth uses service-account creds, not api_key."""
    fake_config = SimpleNamespace(
        provider="gemini",
        endpoint="",
        api_key="",
        use_vertex=True,
        vertex_project="my-project",
    )
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: fake_config,
    )

    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "vlm",
    })
    assert req.enable_vlm is True


def test_coerce_request_rejects_gemini_vertex_without_project(monkeypatch) -> None:
    fake_config = SimpleNamespace(
        provider="gemini",
        endpoint="",
        api_key="",
        use_vertex=True,
        vertex_project="",
    )
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: fake_config,
    )

    with pytest.raises(ValueError, match="Vertex"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": True,
            "natural_language_mode": "vlm",
        })


def test_coerce_request_allows_toriigate_without_vlm_endpoint(monkeypatch) -> None:
    """ToriiGate is a local model — it must NOT require a VLM endpoint."""
    # _build_config should not be called in toriigate mode. Set a sentinel
    # that would raise if invoked.
    def _boom(overrides=None):
        raise AssertionError("VLM config must not be loaded in toriigate mode")

    monkeypatch.setattr("routers.vlm._build_config", _boom)

    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "toriigate",
    })
    assert req.natural_language_mode == "toriigate"


def test_coerce_request_normalizes_toriigate_params(monkeypatch) -> None:
    """caption_length defaults to detailed (owner decision), junk values fall
    back to detailed, explicit token counts clamp to [32, 1024]."""
    monkeypatch.setattr("routers.vlm._build_config", lambda overrides=None: None)

    defaults = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "toriigate",
    })
    assert defaults.toriigate_caption_length == "detailed"
    assert defaults.toriigate_max_new_tokens == 0  # 0 = derive from length
    assert defaults.vlm_grounding is True
    assert defaults.toriigate_grounding is True

    explicit = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "toriigate",
        "toriigate_caption_length": "BRIEF",
        "toriigate_max_new_tokens": 5000,
        "vlm_grounding": False,
        "toriigate_grounding": False,
    })
    assert explicit.toriigate_caption_length == "brief"
    assert explicit.toriigate_max_new_tokens == 1024
    assert explicit.vlm_grounding is False
    assert explicit.toriigate_grounding is False

    junk = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "toriigate",
        "toriigate_caption_length": "epic-novel",
        "toriigate_max_new_tokens": "not-a-number",
    })
    assert junk.toriigate_caption_length == "detailed"
    assert junk.toriigate_max_new_tokens == 0


def test_coerce_request_allows_vlm_disabled_without_endpoint(monkeypatch) -> None:
    """enable_vlm=False short-circuits the endpoint check."""
    def _boom(overrides=None):
        raise AssertionError("VLM config must not be loaded when enable_vlm=False")

    monkeypatch.setattr("routers.vlm._build_config", _boom)

    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": False,
    })
    assert req.enable_vlm is False


# ---------------------------------------------------------------------------
# Fix M3: trigger word with internal whitespace is rejected at validation time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger",
    [
        "my lora trigger",
        "two words",
        "leading_ok and_trailing_ok with space",
        "tab\there",
        "zero\ufeffwidth",
        "next\u0085line",
    ],
)
def test_coerce_request_rejects_trigger_word_with_internal_whitespace(trigger) -> None:
    with pytest.raises(ValueError, match="one token"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": False,
            "trigger_word": trigger,
        })


def test_coerce_request_rejects_trigger_word_that_normalizes_to_empty() -> None:
    with pytest.raises(ValueError, match="characters other than"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": False,
            "trigger_word": "  ",
        })


def test_coerce_request_rejects_trigger_word_over_shared_length_limit() -> None:
    with pytest.raises(ValueError, match="at most 100"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": False,
            "trigger_word": "x" * 101,
        })


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        ("", ""),
        ("single", "single"),
        ("my_lora_trigger", "my_lora_trigger"),
        ("myLoraTrigger", "myLoraTrigger"),
        ("  trailing_and_leading_ok  ", "trailing_and_leading_ok"),
        ("\u0085edge_trigger\u0085", "edge_trigger"),
        ("\ufeffedge_trigger\ufeff", "edge_trigger"),
    ],
)
def test_coerce_request_allows_valid_trigger_words(trigger, expected) -> None:
    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": False,
        "trigger_word": trigger,
    })
    assert req.trigger_word == expected


# ---------------------------------------------------------------------------
# Windowed pipeline: GPU-batched booru + concurrent VLM
# (regression cover for the "batch size too small / GPU underused / serial VLM"
# fix — tag_batch instead of one image per GPU call; config.concurrent_requests
# instead of asyncio.run per image)
# ---------------------------------------------------------------------------


class _FakeBatchTagger:
    """Fake tagger exposing tag_batch (GPU batch) + tag (per-image fallback)."""

    def __init__(self, model_name: str = "wd-eva02-large-tagger-v3") -> None:
        self.model_name = model_name
        self.batch_calls = []
        self.batch_sizes = []
        self.single_calls = 0

    def tag_batch(self, image_paths, *, preferred_batch_size=None, threshold=None,
                  character_threshold=None, copyright_threshold=None, **_kw):
        self.batch_calls.append(list(image_paths))
        self.batch_sizes.append(preferred_batch_size)
        return [
            {
                "general_tags": [{"tag": "1girl", "confidence": 0.95}],
                "copyright_tags": [],
                "character_tags": [],
                "rating": "general",
            }
            for _ in image_paths
        ]

    def tag(self, image_path, *, threshold=None, character_threshold=None,
            copyright_threshold=None):
        self.single_calls += 1
        return {
            "general_tags": [{"tag": "solo", "confidence": 0.9}],
            "copyright_tags": [],
            "character_tags": [],
            "rating": "general",
        }


def _run_pipeline_with_provider_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
    caption_profile: SmartTagCaptionProfile | None,
) -> tuple[
    SmartTagJobState,
    _FakeBatchTagger,
    list[int],
    list[tuple[str, str, str, str]],
]:
    import vlm_providers

    tagger = _FakeBatchTagger()
    persisted_db: list[int] = []
    persisted_paths: list[tuple[str, str, str, str]] = []

    def _raise_provider_error(_config: object) -> None:
        raise RuntimeError("provider construction exploded")

    monkeypatch.setattr(smart_tag_service, "_resolve_tagger", lambda req: tagger)
    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_image_paths",
        lambda image_ids: {
            int(image_id): f"/tmp/db-{int(image_id)}.png"
            for image_id in image_ids
        },
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_recommended_tag_batch_size",
        lambda model_name, use_gpu: 2,
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_apply_memory_pressure",
        lambda job, active_tagger, batch_size: batch_size,
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_persist_result",
        lambda image_id, result, merge_strategy: persisted_db.append(image_id),
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted_paths.append(
            (path, caption, booru_text, nl_text)
        ),
    )
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: SimpleNamespace(
            endpoint="http://127.0.0.1:11434/v1",
            api_key="",
        ),
    )
    monkeypatch.setattr(vlm_providers, "get_provider", _raise_provider_error)

    job = SmartTagJobState(job_id="provider-construction-failure")
    req = SmartTagRequest(
        image_ids=[1],
        image_paths=["/tmp/local.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
        caption_profile=caption_profile,
        skip_existing=False,
    )

    smart_tag_service._run_pipeline(job, req)

    return job, tagger, persisted_db, persisted_paths


def test_krea_profile_provider_construction_failure_is_fail_closed(monkeypatch) -> None:
    job, tagger, persisted_db, persisted_paths = (
        _run_pipeline_with_provider_construction_failure(
            monkeypatch,
            SmartTagCaptionProfile.KREA2_LONG_NL,
        )
    )

    assert job.status == "failed"
    assert job.succeeded == 0
    assert job.processed == 0
    assert job.last_caption_preview == ""
    assert "provider construction exploded" in job.message
    assert tagger.batch_calls == []
    assert persisted_db == []
    assert persisted_paths == []


def test_unprofiled_provider_failure_keeps_legacy_booru_fallback(monkeypatch) -> None:
    job, tagger, persisted_db, persisted_paths = (
        _run_pipeline_with_provider_construction_failure(monkeypatch, None)
    )

    assert job.status == "completed"
    assert job.succeeded == 2
    assert [
        path
        for batch in tagger.batch_calls
        for path in batch
    ] == ["/tmp/db-1.png", "/tmp/local.png"]
    assert persisted_db == [1]
    assert [row[0] for row in persisted_paths] == ["/tmp/local.png"]


class _RecordingToriiTagger:
    """Fake ToriiGate that records the grounding tags passed to tag()."""

    def __init__(self) -> None:
        self.calls = []

    def tag(self, image_path, tags=None):
        self.calls.append((image_path, tags))
        return {
            "general_tags": [],
            "character_tags": [],
            "rating": "general",
            "all_tags": [],
            "raw_text": '{"description": "raw"}',
            "nl_text": "A girl stands in a field.",
        }


def test_caption_phase_passes_booru_tags_to_toriigate_as_grounding(monkeypatch) -> None:
    """ToriiGate must receive the window's WD tags as grounding input (the
    same noise-filtered context the cloud VLM path gets)."""
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result", lambda job, path, caption, booru_text, nl_text: None,
    )

    torii = _RecordingToriiTagger()
    job = SmartTagJobState(job_id="torii-grounding-job")
    job.total = 2
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png", "/tmp/img-1.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
        toriigate_grounding=True,
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=_FakeBatchTagger(), vlm_provider=None, nl_tagger=torii,
    )

    assert len(torii.calls) == 2
    for _path, tags in torii.calls:
        assert tags == ["1girl"], "WD tags must reach ToriiGate as grounding"


def test_caption_phase_omits_grounding_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result", lambda job, path, caption, booru_text, nl_text: None,
    )

    torii = _RecordingToriiTagger()
    job = SmartTagJobState(job_id="torii-no-grounding-job")
    job.total = 1
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
        toriigate_grounding=False,
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=_FakeBatchTagger(), vlm_provider=None, nl_tagger=torii,
    )

    assert torii.calls == [("/tmp/img-0.png", None)]


class _ReleasableBatchTagger(_FakeBatchTagger):
    """Batch tagger that records release ordering for the two-phase tests."""

    def __init__(self, events) -> None:
        super().__init__()
        self.events = events

    def tag_batch(self, image_paths, **kwargs):
        self.events.append("tag_batch")
        return super().tag_batch(image_paths, **kwargs)

    def release_session(self):
        self.events.append("release_booru")


def test_two_phase_toriigate_releases_booru_before_loading(monkeypatch) -> None:
    """The black-screen fix contract: every booru window is tagged first, the
    booru session is released, and only then does ToriiGate load. The two
    heavy models must never be resident together."""
    events = []
    persisted = []
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append((path, caption)),
    )
    torii = _RecordingToriiTagger()

    def _fake_phase2_load(job, req):
        events.append("torii_load")
        return torii

    monkeypatch.setattr(smart_tag_service, "_load_toriigate_for_phase2", _fake_phase2_load)

    tagger = _ReleasableBatchTagger(events)
    job = SmartTagJobState(job_id="two-phase-job")
    job.status = "running"
    job.total = 3
    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(3)],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
    )

    smart_tag_service._run_two_phase_toriigate_pipeline(job, req, tagger=tagger)

    assert events == ["tag_batch", "release_booru", "torii_load"]
    assert len(torii.calls) == 3, "all images captioned in phase 2"
    assert job.status == "completed"
    assert len(persisted) == 3
    assert all("A girl stands in a field." in caption for _p, caption in persisted)


def test_two_phase_toriigate_persists_booru_tags_when_load_fails(monkeypatch) -> None:
    """A failed ToriiGate load (e.g. the RAM guard) must not discard the
    finished booru pass: tag-only captions persist and the job fails with a
    clear message."""
    persisted = []
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append((path, caption)),
    )

    def _boom(job, req):
        raise RuntimeError("ToriiGate CPU mode needs ~13 GB of available RAM")

    monkeypatch.setattr(smart_tag_service, "_load_toriigate_for_phase2", _boom)

    job = SmartTagJobState(job_id="two-phase-load-fail")
    job.total = 2
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png", "/tmp/img-1.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
    )

    smart_tag_service._run_two_phase_toriigate_pipeline(
        job, req, tagger=_FakeBatchTagger(),
    )

    assert job.status == "failed"
    assert "Booru tags were saved" in job.message
    assert len(persisted) == 2, "booru-only captions still persisted"
    assert all("1girl" in caption for _p, caption in persisted)


def test_phase2_toriigate_load_disables_cpu_fallback(monkeypatch) -> None:
    """Smart Tag's GPU ToriiGate phase must not silently retry CPU fp32 after
    WD14 is released; failure should stay explicit and preserve booru tags."""
    captured = {}

    class _FakeTorii:
        def load(self):
            captured["loaded"] = True

    def _fake_get_toriigate_tagger(**kwargs):
        captured.update(kwargs)
        return _FakeTorii()

    import toriigate_tagger as torii_module
    import model_health

    monkeypatch.setattr(torii_module, "get_toriigate_tagger", _fake_get_toriigate_tagger)
    monkeypatch.setattr(
        model_health,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": True,
            "runtime_compatible": True,
            "runtime_compatibility_error": None,
        },
    )

    job = SmartTagJobState(job_id="phase2-load-options")
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
        use_gpu=True,
    )

    smart_tag_service._load_toriigate_for_phase2(job, req)

    assert captured["use_gpu"] is True
    assert captured["allow_cpu_fallback"] is False
    assert captured["loaded"] is True


def test_phase2_toriigate_blocks_incompatible_runtime_before_import(monkeypatch) -> None:
    import model_health

    imported = []
    monkeypatch.setattr(
        model_health,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": True,
            "runtime_compatible": False,
            "runtime_compatibility_error": (
                "PyTorch CUDA 13.0 is incompatible with ONNX Runtime CUDA 12.x. "
                "Open Model Manager, run Prepare, then restart the app."
            ),
        },
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "toriigate_tagger",
        SimpleNamespace(
            get_toriigate_tagger=lambda **kwargs: imported.append(kwargs)
        ),
    )
    job = SmartTagJobState(job_id="phase2-incompatible-runtime")
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
        use_gpu=True,
    )

    with pytest.raises(RuntimeError, match="Model Manager.*Prepare.*restart"):
        smart_tag_service._load_toriigate_for_phase2(job, req)

    assert imported == []


def test_run_pipeline_routes_toriigate_to_two_phase(monkeypatch) -> None:
    """_run_pipeline must NOT load ToriiGate upfront anymore — the toriigate
    mode routes to the two-phase pipeline."""
    routed = {}

    def _fake_two_phase(job, req, *, tagger):
        routed["called"] = True
        job.status = "completed"

    monkeypatch.setattr(
        smart_tag_service, "_run_two_phase_toriigate_pipeline", _fake_two_phase
    )
    monkeypatch.setattr(smart_tag_service, "_request_total", lambda req: 1)
    monkeypatch.setattr(smart_tag_service, "_resolve_tagger", lambda req: _FakeBatchTagger())

    def _no_upfront_load(*args, **kwargs):
        raise AssertionError("ToriiGate must not load before the booru phase")

    import toriigate_tagger as torii_module

    monkeypatch.setattr(torii_module, "get_toriigate_tagger", _no_upfront_load)

    job = SmartTagJobState(job_id="route-two-phase")
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="toriigate",
    )

    smart_tag_service._run_pipeline(job, req)

    assert routed.get("called") is True
    assert job.status == "completed"


@pytest.mark.parametrize("caption_profile", ["", "unsupported_profile"])
def test_coerce_request_rejects_unknown_caption_profile(caption_profile: str) -> None:
    with pytest.raises(ValueError, match=r"caption_profile.*krea2_long_nl"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": False,
            "caption_profile": caption_profile,
        })


def test_coerce_request_rejects_caption_profile_when_vlm_disabled() -> None:
    with pytest.raises(ValueError, match=r"caption_profile requires enable_vlm=true"):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": False,
            "natural_language_mode": "vlm",
            "caption_profile": "krea2_long_nl",
        })


def test_coerce_request_rejects_caption_profile_with_toriigate() -> None:
    with pytest.raises(
        ValueError,
        match=r"caption_profile requires natural_language_mode='vlm'.*toriigate",
    ):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": True,
            "natural_language_mode": "toriigate",
            "caption_profile": "krea2_long_nl",
        })


@pytest.mark.parametrize("natural_language_mode", ["", "off", "banana"])
def test_coerce_request_rejects_caption_profile_with_non_vlm_mode(
    natural_language_mode: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"caption_profile requires natural_language_mode='vlm'",
    ):
        _coerce_request({
            "image_ids": [1],
            "enable_vlm": True,
            "natural_language_mode": natural_language_mode,
            "caption_profile": "krea2_long_nl",
        })


def test_build_caption_phase_applies_explicit_krea2_profile(monkeypatch) -> None:
    from vlm_providers.registry import PROMPT_PRESETS as VLM_PROMPT_PRESETS

    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: SimpleNamespace(
            provider="openai_compat",
            endpoint="http://127.0.0.1:11434/v1",
            api_key="",
            use_vertex=False,
            vertex_project="",
        ),
    )
    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": True,
        "natural_language_mode": "vlm",
        "training_purpose": "character",
        "caption_profile": "krea2_long_nl",
        "suppressed_traits": ["blue_hair"],
    })
    config = SimpleNamespace(
        concurrent_requests=2,
        include_tags_as_context=True,
        system_prompt="settings system prompt",
        user_prompt="settings user prompt",
        user_prompt_with_tags="settings grounded prompt",
        output_format="both",
    )
    vlm = SimpleNamespace(config=config)

    ctx = smart_tag_service._build_caption_phase(req, vlm, None)

    preset = VLM_PROMPT_PRESETS["krea2_long_nl"]
    assert ctx.use_vlm is True
    assert ctx.include_tags_as_context is True
    assert config.system_prompt == preset["system_prompt"]
    assert config.user_prompt.startswith(preset["user_prompt"])
    assert config.user_prompt_with_tags.startswith(preset["user_prompt_with_tags"])
    assert "5-8" in config.user_prompt
    assert "5-8" in config.user_prompt_with_tags
    assert "2-3 plain English sentences" not in config.user_prompt
    assert "blue hair" in config.user_prompt
    assert "blue hair" in config.user_prompt_with_tags
    assert config.output_format == "nl_caption"


def test_build_caption_phase_without_profile_keeps_purpose_prompt() -> None:
    config = SimpleNamespace(
        concurrent_requests=2,
        include_tags_as_context=True,
        system_prompt="settings system prompt",
        user_prompt="settings user prompt",
        user_prompt_with_tags="settings grounded prompt",
        output_format="both",
    )
    vlm = SimpleNamespace(config=config)
    req = SmartTagRequest(
        image_ids=[1],
        enable_vlm=True,
        natural_language_mode="vlm",
        training_purpose="style",
    )

    smart_tag_service._build_caption_phase(req, vlm, None)

    assert config.system_prompt == "settings system prompt"
    assert config.user_prompt == PROMPT_PRESETS["style"]
    assert config.user_prompt_with_tags == PROMPT_PRESETS["style"]
    assert config.output_format == "nl_caption"


def test_krea2_grounding_prompt_treats_machine_tags_as_cues() -> None:
    from vlm_providers.registry import PROMPT_PRESETS as VLM_PROMPT_PRESETS

    prompt = VLM_PROMPT_PRESETS["krea2_long_nl"]["user_prompt_with_tags"].lower()

    assert "ground truth" not in prompt
    assert "machine-generated" in prompt
    assert "grounding cues" in prompt


def test_build_caption_phase_forces_nl_output_format() -> None:
    """Smart Tag's VLM role is prose only — a stored preset with
    output_format="both"/"danbooru_tags" must be overridden per job (the
    provider instance is job-local, so VLM Settings are not mutated)."""
    vlm = SimpleNamespace(
        config=SimpleNamespace(
            concurrent_requests=2,
            include_tags_as_context=True,
            user_prompt="",
            user_prompt_with_tags="",
            output_format="both",
        )
    )
    req = SmartTagRequest(image_ids=[1], enable_vlm=True, natural_language_mode="vlm")

    ctx = smart_tag_service._build_caption_phase(req, vlm, None)

    assert ctx.use_vlm is True
    assert vlm.config.output_format == "nl_caption"


def test_build_caption_phase_respects_vlm_grounding_toggle() -> None:
    vlm = SimpleNamespace(
        config=SimpleNamespace(
            concurrent_requests=2,
            include_tags_as_context=True,
            user_prompt="",
            user_prompt_with_tags="",
            output_format="nl_caption",
        )
    )
    req = SmartTagRequest(
        image_ids=[1],
        enable_vlm=True,
        natural_language_mode="vlm",
        vlm_grounding=False,
    )

    ctx = smart_tag_service._build_caption_phase(req, vlm, None)

    assert ctx.use_vlm is True
    assert ctx.include_tags_as_context is False


class _ConcurrencyTrackingVlm:
    """Fake VLM provider that records the peak number of in-flight captions."""

    def __init__(self, concurrent_requests: int) -> None:
        self.config = SimpleNamespace(
            concurrent_requests=concurrent_requests,
            include_tags_as_context=True,
            user_prompt="",
            user_prompt_with_tags="",
        )
        self._in_flight = 0
        self.max_in_flight = 0

    async def caption_image(self, image_path, *, tags=None):
        import asyncio

        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        await asyncio.sleep(0.02)
        self._in_flight -= 1
        return SimpleNamespace(caption="a caption")


def test_windowed_pipeline_batches_booru_on_gpu(monkeypatch) -> None:
    """Single-tagger booru phase must call tag_batch with the whole window in one
    GPU call (not tag() once per image)."""
    persisted = []
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append((path, caption)),
    )

    tagger = _FakeBatchTagger()
    job = SmartTagJobState(job_id="batch-job")
    job.total = 5
    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(5)],
        enable_wd14=True,
        enable_vlm=False,
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=tagger, vlm_provider=None, nl_tagger=None,
    )

    assert tagger.batch_calls == [[f"/tmp/img-{i}.png" for i in range(5)]]
    assert tagger.single_calls == 0
    assert job.succeeded == 5
    assert all("1girl" in caption for _path, caption in persisted)


def test_recommended_tag_batch_size_is_hardware_aware(monkeypatch) -> None:
    """Smart Tag must size the booru batch from the hardware recommender (like
    the bulk worker), forwarding the real model name + GPU flag, instead of a
    fixed 64 — so an 8GB-VRAM laptop starts at a size that fits."""
    calls = {}

    def _fake_recommend(system_info, *, model_name=None, use_gpu=None):
        calls["model_name"] = model_name
        calls["use_gpu"] = use_gpu
        return {"recommended_batch_size": 16, "recommended_cpu_chunk_size": 8}

    monkeypatch.setattr("hardware_monitor.get_system_info", lambda *a, **k: {"stub": True})
    monkeypatch.setattr("hardware_monitor.recommend_tagger_config", _fake_recommend)

    assert smart_tag_service._recommended_tag_batch_size("wd-eva02-large-tagger-v3", True) == 16
    assert calls["model_name"] == "wd-eva02-large-tagger-v3"
    assert calls["use_gpu"] is True
    # CPU path uses recommended_cpu_chunk_size.
    assert smart_tag_service._recommended_tag_batch_size("wd-eva02-large-tagger-v3", False) == 8


def test_recommended_tag_batch_size_clamps_and_falls_back(monkeypatch) -> None:
    """Over-large recommendations are capped at the ceiling; a probe failure
    degrades to a safe fixed fallback instead of raising."""
    monkeypatch.setattr("hardware_monitor.get_system_info", lambda *a, **k: {})
    monkeypatch.setattr(
        "hardware_monitor.recommend_tagger_config",
        lambda *a, **k: {"recommended_batch_size": 9999, "recommended_cpu_chunk_size": 9999},
    )
    assert (
        smart_tag_service._recommended_tag_batch_size("x", True)
        == smart_tag_service.SMART_TAG_TAG_BATCH_SIZE
    )

    def _boom(*a, **k):
        raise RuntimeError("no hardware probe in this env")

    monkeypatch.setattr("hardware_monitor.recommend_tagger_config", _boom)
    # Must not raise; falls back to a safe non-zero size on either device.
    assert smart_tag_service._recommended_tag_batch_size("x", True) == 16
    assert smart_tag_service._recommended_tag_batch_size("x", False) == 8


def test_windowed_pipeline_uses_hardware_aware_batch_size(monkeypatch) -> None:
    """The single-tagger booru phase must pass the hardware-recommended batch
    size to tag_batch (not the fixed SMART_TAG_TAG_BATCH_SIZE ceiling)."""
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: None,
    )
    monkeypatch.setattr(smart_tag_service, "_recommended_tag_batch_size", lambda model, gpu: 7)
    # Isolate hardware-aware *sizing* from live memory-pressure shrinking (which
    # has its own test); otherwise a high-RAM machine halves 7 -> 3 and this
    # assertion becomes machine-dependent.
    monkeypatch.setattr(
        smart_tag_service, "_apply_memory_pressure", lambda job, tagger, size: size
    )

    tagger = _FakeBatchTagger()
    job = SmartTagJobState(job_id="hw-batch-job")
    job.total = 3
    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(3)],
        enable_wd14=True,
        enable_vlm=False,
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=tagger, vlm_provider=None, nl_tagger=None,
    )

    assert tagger.batch_sizes == [7]  # hardware-aware size, not the 64 ceiling


def test_apply_memory_pressure_shrinks_batch_and_refreshes_session(monkeypatch) -> None:
    """Smart Tag must react to LIVE memory pressure (not just at job start):
    shrink the booru batch when RAM is tight and refresh the tagger session
    when free VRAM is nearly gone. Degrades safely if the probe is unavailable."""
    monkeypatch.setattr(smart_tag_service.time, "sleep", lambda *_a, **_k: None)

    class _FakeTagger:
        def __init__(self):
            self.refreshed = 0

        def _recreate_session(self):
            self.refreshed += 1

    job = SmartTagJobState(job_id="pressure")

    def _pressure(**values):
        base = {"should_pause": False, "should_restart_session": False, "ram_percent_used": 40.0}
        base.update(values)
        monkeypatch.setattr("hardware_monitor.check_memory_pressure", lambda: base)

    # No pressure -> unchanged, no refresh.
    _pressure()
    quiet = _FakeTagger()
    assert smart_tag_service._apply_memory_pressure(job, quiet, 32) == 32
    assert quiet.refreshed == 0

    # Critical RAM (should_pause) -> halved.
    _pressure(should_pause=True, ram_percent_used=96.0)
    assert smart_tag_service._apply_memory_pressure(job, _FakeTagger(), 32) == 16

    # High (>=90%) but not critical RAM -> halved, floor 2.
    _pressure(ram_percent_used=92.0)
    assert smart_tag_service._apply_memory_pressure(job, _FakeTagger(), 32) == 16

    # Free VRAM nearly gone -> refresh the tagger session.
    _pressure(should_restart_session=True)
    tagger = _FakeTagger()
    smart_tag_service._apply_memory_pressure(job, tagger, 32)
    assert tagger.refreshed == 1

    # Probe failure -> unchanged, never raises.
    def _boom():
        raise RuntimeError("no psutil/torch in this env")

    monkeypatch.setattr("hardware_monitor.check_memory_pressure", _boom)
    assert smart_tag_service._apply_memory_pressure(job, _FakeTagger(), 32) == 32


def test_tag_batch_with_thresholds_falls_back_to_per_image() -> None:
    """A tagger without tag_batch degrades to per-image tag() with the same shape."""
    class _NoBatchTagger:
        def __init__(self):
            self.calls = []

        def tag(self, image_path, *, threshold=None, character_threshold=None,
                copyright_threshold=None):
            self.calls.append(image_path)
            return {"general_tags": [{"tag": "x", "confidence": 1.0}],
                    "copyright_tags": [], "character_tags": [], "rating": "general"}

    tagger = _NoBatchTagger()
    out = smart_tag_service._tag_batch_with_thresholds(
        tagger, ["/a.png", "/b.png"],
        general_threshold=0.35, character_threshold=0.85, copyright_threshold=0.35,
    )
    assert tagger.calls == ["/a.png", "/b.png"]
    assert len(out) == 2
    assert out[0]["general_tags"][0]["tag"] == "x"


def test_windowed_pipeline_runs_vlm_concurrently(monkeypatch) -> None:
    """VLM captions must run up to config.concurrent_requests at a time, not
    serially (the bug: asyncio.run per image never used concurrent_requests)."""
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: None,
    )

    tagger = _FakeBatchTagger()
    vlm = _ConcurrencyTrackingVlm(concurrent_requests=4)
    job = SmartTagJobState(job_id="concurrent-job")
    job.total = 8
    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(8)],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=tagger, vlm_provider=vlm, nl_tagger=None,
    )

    assert job.succeeded == 8
    assert vlm.max_in_flight >= 2, "VLM captions did not run concurrently"
    assert vlm.max_in_flight <= 4


def test_windowed_pipeline_vlm_failure_keeps_booru_caption(monkeypatch) -> None:
    """A VLM error on an image must not fail it — it still gets a booru caption."""
    persisted = []
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(caption),
    )

    class _FailingVlm:
        def __init__(self):
            self.config = SimpleNamespace(
                concurrent_requests=2, include_tags_as_context=True,
                user_prompt="", user_prompt_with_tags="",
            )

        async def caption_image(self, image_path, *, tags=None):
            raise RuntimeError("API down")

    job = SmartTagJobState(job_id="vlm-fail-job")
    job.total = 2
    req = SmartTagRequest(
        image_paths=["/tmp/img-0.png", "/tmp/img-1.png"],
        enable_wd14=True, enable_vlm=True, natural_language_mode="vlm",
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=_FakeBatchTagger(), vlm_provider=_FailingVlm(), nl_tagger=None,
    )

    assert job.succeeded == 2  # VLM failure is not an image failure
    assert job.failed == 0
    assert all("1girl" in c for c in persisted)  # booru tag survived


def test_krea_profile_does_not_persist_booru_only_when_vlm_returns_an_error(monkeypatch) -> None:
    persisted = []
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(caption),
    )

    class _TruncatedVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                concurrent_requests=1,
                include_tags_as_context=True,
                system_prompt="",
                user_prompt="",
                user_prompt_with_tags="",
                output_format="nl_caption",
            )

        async def caption_image(self, image_path, *, tags):
            return SimpleNamespace(
                caption="",
                error=(
                    "Model 'qwen3-vl:8b' exhausted the 1024-token caption budget "
                    "before producing answer content. Use qwen3-vl:8b-instruct."
                ),
            )

    job = SmartTagJobState(job_id="krea-vlm-fail-job", status="running", total=1)
    req = SmartTagRequest(
        image_paths=["/tmp/krea.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
        caption_profile=SmartTagCaptionProfile.KREA2_LONG_NL,
    )

    smart_tag_service._run_windowed_pipeline(
        job,
        req,
        tagger=_FakeBatchTagger(),
        vlm_provider=_TruncatedVlm(),
        nl_tagger=None,
    )

    assert persisted == []
    assert job.status == "failed"
    assert job.succeeded == 0
    assert job.failed == 1
    assert job.processed == 1
    assert "qwen3-vl:8b-instruct" in job.errors[0]["error"]
    assert job.errors[0]["error"] in job.message


def test_krea_profile_partial_caption_success_ends_with_warning(monkeypatch) -> None:
    persisted = []
    provider_error = "Caption provider rejected one image."
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(path),
    )

    class _PartiallyFailingVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                concurrent_requests=1,
                include_tags_as_context=True,
                system_prompt="",
                user_prompt="",
                user_prompt_with_tags="",
                output_format="nl_caption",
            )

        async def caption_image(self, image_path, *, tags):
            if image_path.endswith("failed.png"):
                return SimpleNamespace(caption="", error=provider_error)
            return SimpleNamespace(
                caption="A successful natural-language caption.",
                error=None,
            )

    job = SmartTagJobState(job_id="krea-vlm-partial-job", status="running", total=2)
    req = SmartTagRequest(
        image_paths=["/tmp/succeeded.png", "/tmp/failed.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
        caption_profile=SmartTagCaptionProfile.KREA2_LONG_NL,
    )

    smart_tag_service._run_windowed_pipeline(
        job,
        req,
        tagger=_FakeBatchTagger(),
        vlm_provider=_PartiallyFailingVlm(),
        nl_tagger=None,
    )

    assert persisted == ["/tmp/succeeded.png"]
    assert job.status == "warning"
    assert job.succeeded == 1
    assert job.failed == 1
    assert job.processed == 2
    assert job.errors[0]["error"] == provider_error


def test_windowed_pipeline_vlm_prompt_matches_legacy_build_vlm_prompt() -> None:
    """The concurrent path sets the purpose template once + passes per-image tags;
    the provider's build_user_message must produce the SAME prompt the old
    build_vlm_prompt path produced (incl. the always-on noise filter)."""
    from vlm_providers.base import VLMConfig, VLMProvider

    purpose = "character"
    raw_tags = ["1girl", "masterpiece", "solo"]  # masterpiece is noise
    partial = {
        "general_names": list(raw_tags),
        "copyright_names": [],
        "character_names": [],
    }
    template = smart_tag_service.PROMPT_PRESETS[purpose]

    cfg = VLMConfig(
        user_prompt=template, user_prompt_with_tags=template,
        include_tags_as_context=True,
    )
    provider = VLMProvider(cfg)
    ctx_tags = smart_tag_service._vlm_context_tags_for(partial, True)
    built = provider.build_user_message(ctx_tags)
    # Both the old and new paths run the prompt through build_user_message (which
    # .strip()s); compare against that effective form, not the raw build_vlm_prompt.
    expected = smart_tag_service.build_vlm_prompt(purpose, raw_tags, include_tags=True).strip()
    assert built == expected
    assert "masterpiece" not in built  # noise filtered out

    cfg_off = VLMConfig(
        user_prompt=template, user_prompt_with_tags=template,
        include_tags_as_context=False,
    )
    provider_off = VLMProvider(cfg_off)
    ctx_tags_off = smart_tag_service._vlm_context_tags_for(partial, False)
    built_off = provider_off.build_user_message(ctx_tags_off)
    expected_off = smart_tag_service.build_vlm_prompt(purpose, raw_tags, include_tags=False).strip()
    assert built_off == expected_off


def test_windowed_pipeline_cancel_preserves_completed(monkeypatch) -> None:
    """Cancelling mid-run keeps already-captioned images and stops the rest."""
    persisted = []
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(path),
    )

    job = SmartTagJobState(job_id="cancel-job")
    job.total = 5

    class _CancelAfterTwoVlm:
        def __init__(self):
            # concurrent_requests=1 makes the cancel point deterministic.
            self.config = SimpleNamespace(
                concurrent_requests=1, include_tags_as_context=True,
                user_prompt="", user_prompt_with_tags="",
            )
            self.calls = 0

        async def caption_image(self, image_path, *, tags=None):
            self.calls += 1
            if self.calls >= 2:
                job.cancel_requested = True  # cancel after the 2nd caption
            return SimpleNamespace(caption="c")

    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(5)],
        enable_wd14=True, enable_vlm=True, natural_language_mode="vlm",
    )

    smart_tag_service._run_windowed_pipeline(
        job, req, tagger=_FakeBatchTagger(), vlm_provider=_CancelAfterTwoVlm(), nl_tagger=None,
    )

    assert job.status == "cancelled"
    assert job.succeeded == 2
    assert len(persisted) == 2  # completed work preserved, remainder skipped


def test_multi_tagger_pipeline_batches_and_runs_vlm_concurrently(monkeypatch) -> None:
    """The multi-tagger consensus path must also batch booru per tagger and run
    the VLM concurrently (owner-chosen scope), still fusing consensus tags."""
    monkeypatch.setattr(
        smart_tag_service, "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: None,
    )

    batch_taggers = []

    def fake_resolve(model_name, **_kwargs):
        t = _FakeBatchTagger()
        batch_taggers.append(t)
        return t

    monkeypatch.setattr(smart_tag_service, "_resolve_tagger_by_model", fake_resolve)

    vlm = _ConcurrencyTrackingVlm(concurrent_requests=4)
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: SimpleNamespace(endpoint="http://localhost:11434", api_key=""),
    )
    monkeypatch.setattr("vlm_providers.get_provider", lambda config: vlm)

    job = SmartTagJobState(job_id="multi-concurrent-job")
    req = SmartTagRequest(
        image_paths=[f"/tmp/img-{i}.png" for i in range(6)],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
        taggers=[
            {"model": "tagger-a", "general_threshold": 0.35, "character_threshold": 0.85},
            {"model": "tagger-b", "general_threshold": 0.35, "character_threshold": 0.85},
        ],
        consensus_min=1,
    )

    smart_tag_service._run_pipeline(job, req)

    assert job.status == "completed"
    assert job.succeeded == 6
    # Each of the 2 taggers batched its booru pass (one window of 6, not 6 calls).
    assert len(batch_taggers) == 2
    assert all(
        t.batch_calls == [[f"/tmp/img-{i}.png" for i in range(6)]] for t in batch_taggers
    )
    assert all(t.single_calls == 0 for t in batch_taggers)
    # VLM ran concurrently in the consensus phase.
    assert vlm.max_in_flight >= 2


def test_multi_tagger_krea_profile_all_caption_failures_end_failed(monkeypatch) -> None:
    persisted = []
    provider_error = (
        "Model 'qwen3-vl:8b' exhausted the caption budget before producing answer "
        "content. Use qwen3-vl:8b-instruct."
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(path),
    )

    def fake_resolve(model_name, **_kwargs):
        return _FakeBatchTagger()

    class _FailingProfileVlm:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                concurrent_requests=2,
                include_tags_as_context=True,
                system_prompt="",
                user_prompt="",
                user_prompt_with_tags="",
                output_format="nl_caption",
            )

        async def caption_image(self, image_path, *, tags):
            return SimpleNamespace(caption="", error=provider_error)

    monkeypatch.setattr(smart_tag_service, "_resolve_tagger_by_model", fake_resolve)
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda: SimpleNamespace(endpoint="http://localhost:11434", api_key=""),
    )
    monkeypatch.setattr(
        "vlm_providers.get_provider",
        lambda config: _FailingProfileVlm(),
    )

    job = SmartTagJobState(job_id="multi-krea-vlm-fail-job")
    req = SmartTagRequest(
        image_paths=["/tmp/multi-krea-0.png", "/tmp/multi-krea-1.png"],
        enable_wd14=True,
        enable_vlm=True,
        natural_language_mode="vlm",
        caption_profile=SmartTagCaptionProfile.KREA2_LONG_NL,
        taggers=[
            {
                "model": "tagger-a",
                "general_threshold": 0.35,
                "character_threshold": 0.85,
            },
            {
                "model": "tagger-b",
                "general_threshold": 0.35,
                "character_threshold": 0.85,
            },
        ],
        consensus_min=1,
    )

    smart_tag_service._run_pipeline(job, req)

    assert persisted == []
    assert job.status == "failed"
    assert job.succeeded == 0
    assert job.failed == 2
    assert job.processed == 2
    assert len(job.errors) == 2
    assert all(error["error"] == provider_error for error in job.errors)
    assert provider_error in job.message


_TAGGER_LOAD_ERROR = (
    "Download failed for model.onnx via huggingface.co and hf-mirror.com: "
    "offline"
)


class _UnloadableTagger:
    """Tagger whose weights cannot be loaded (not downloaded / no network)."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def load(self) -> None:
        raise RuntimeError(_TAGGER_LOAD_ERROR)


def test_multi_tagger_load_failure_is_reported_not_counted_as_success(
    monkeypatch,
) -> None:
    """A run whose only tagger cannot load must not report success.

    Real trigger: the model was never prepared, or HuggingFace *and* the
    hf-mirror.com fallback are unreachable, so ``load()`` raises. The user
    used to get status ``completed`` / "Done. 2 ok, 0 failed." with zero
    tags written and the reason only in a log line they never see.
    """
    persisted = []
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: persisted.append(path),
    )
    monkeypatch.setattr(
        smart_tag_service,
        "_resolve_tagger_by_model",
        lambda model_name, **_kwargs: _UnloadableTagger(model_name),
    )

    job = SmartTagJobState(job_id="multi-tagger-load-failure")
    req = SmartTagRequest(
        image_paths=["/tmp/load-fail-0.png", "/tmp/load-fail-1.png"],
        enable_wd14=True,
        enable_vlm=False,
        taggers=[
            {
                "model": "wd-swinv2-tagger-v3",
                "general_threshold": 0.35,
                "character_threshold": 0.85,
            }
        ],
        consensus_min=1,
    )

    smart_tag_service._run_pipeline(job, req)

    # The user-visible outcome, not an internal counter: the job is not a
    # success, nothing was written, and the reason reaches the message.
    assert job.status == "failed"
    assert job.succeeded == 0
    assert persisted == []
    assert "wd-swinv2-tagger-v3" in job.message
    assert _TAGGER_LOAD_ERROR in job.message


def test_multi_tagger_partial_load_failure_warns_and_names_the_model(
    monkeypatch,
) -> None:
    """One of two requested taggers failing to load is a degraded run.

    The images do get tags (from the tagger that loaded), so this is the
    existing ``warning`` status rather than ``failed`` — but "Done. 2 ok,
    0 failed." on its own would hide that half the requested models never
    ran and therefore that the consensus is not what was asked for.
    """
    monkeypatch.setattr(
        smart_tag_service,
        "_append_caption_result",
        lambda job, path, caption, booru_text, nl_text: None,
    )

    def fake_resolve(model_name, **_kwargs):
        if model_name == "tagger-b":
            return _UnloadableTagger(model_name)
        return _FakeBatchTagger(model_name)

    monkeypatch.setattr(smart_tag_service, "_resolve_tagger_by_model", fake_resolve)

    job = SmartTagJobState(job_id="multi-tagger-partial-load-failure")
    req = SmartTagRequest(
        image_paths=["/tmp/partial-load-0.png", "/tmp/partial-load-1.png"],
        enable_wd14=True,
        enable_vlm=False,
        taggers=[
            {"model": "tagger-a", "general_threshold": 0.35},
            {"model": "tagger-b", "general_threshold": 0.35},
        ],
        consensus_min=1,
    )

    smart_tag_service._run_pipeline(job, req)

    assert job.status == "warning"
    assert job.succeeded == 2
    assert "tagger-b" in job.message
    assert any("tagger-b" in error.get("error", "") for error in job.errors)


# ---------------------------------------------------------------------------
# Job hygiene: registry pruning + results-file cleanup + total-failure handling
# ---------------------------------------------------------------------------


def test_start_smart_tag_job_exposes_caption_profile_in_settings(monkeypatch) -> None:
    monkeypatch.setattr(smart_tag_service, "_run_pipeline", lambda job, req: None)
    monkeypatch.setattr(smart_tag_service, "_jobs", {})
    monkeypatch.setattr(smart_tag_service, "_active_job_id", None)
    monkeypatch.setattr(
        "routers.vlm._build_config",
        lambda overrides=None: SimpleNamespace(
            provider="openai_compat",
            endpoint="http://127.0.0.1:11434/v1",
            api_key="",
            use_vertex=False,
            vertex_project="",
        ),
    )

    snapshot = smart_tag_service.start_smart_tag_job({
        "image_ids": [1],
        "enable_wd14": False,
        "enable_vlm": True,
        "natural_language_mode": "vlm",
        "caption_profile": "krea2_long_nl",
    })

    assert snapshot["settings"]["caption_profile"] == "krea2_long_nl"


def test_start_smart_tag_job_prunes_finished_jobs_and_results_files(monkeypatch, tmp_path) -> None:
    """Starting a new job must evict old finished jobs (keeping the newest
    SMART_TAG_FINISHED_JOBS_KEPT) and delete their on-disk caption-results
    jsonl files so the registry and data/smart-tag-results/ stop growing
    unboundedly over the life of the process."""
    monkeypatch.setattr(smart_tag_service, "_run_pipeline", lambda job, req: None)
    monkeypatch.setattr(smart_tag_service, "_jobs", {})
    monkeypatch.setattr(smart_tag_service, "_active_job_id", None)

    keep = smart_tag_service.SMART_TAG_FINISHED_JOBS_KEPT
    overflow = 3
    result_files = []
    for index in range(keep + overflow):
        results = tmp_path / f"old-{index}.jsonl"
        results.write_text('{"path": "synthetic.png", "caption": "synthetic"}\n', encoding="utf-8")
        job = SmartTagJobState(job_id=f"old-{index}", status="completed")
        job.finished_at = 1000.0 + index  # old-0 is the oldest
        job.caption_results_path = str(results)
        smart_tag_service._jobs[job.job_id] = job
        result_files.append(results)

    snapshot = smart_tag_service.start_smart_tag_job({
        "image_ids": [1],
        "enable_vlm": False,
    })

    # The oldest `overflow` jobs are evicted along with their jsonl files...
    for index in range(overflow):
        assert f"old-{index}" not in smart_tag_service._jobs
        assert not result_files[index].exists()
    # ...the newest `keep` finished jobs (and their files) survive.
    for index in range(overflow, keep + overflow):
        assert f"old-{index}" in smart_tag_service._jobs
        assert result_files[index].exists()
    assert snapshot["job_id"] in smart_tag_service._jobs


def test_run_pipeline_failure_while_resolving_total_lands_in_failed_state(monkeypatch) -> None:
    """A crash while computing the request total must mark the job failed and
    release the active slot instead of wedging the service until restart."""
    def _boom(req):
        raise RuntimeError("selection token backend exploded")

    monkeypatch.setattr(smart_tag_service, "_request_total", _boom)
    monkeypatch.setattr(smart_tag_service, "_active_job_id", "total-fail")

    job = SmartTagJobState(job_id="total-fail")
    req = SmartTagRequest(image_ids=[1], enable_wd14=False, enable_vlm=False)

    smart_tag_service._run_pipeline(job, req)

    assert job.status == "failed"
    assert "selection token backend exploded" in job.message
    assert job.finished_at is not None
    assert smart_tag_service._active_job_id is None


def test_build_vlm_prompt_appends_trait_suppression() -> None:
    """SEP-2: suppressed_traits appends a dataset-specific never-mention block."""
    prompt = build_vlm_prompt(
        "character",
        ["1girl"],
        suppressed_traits=["silver_hair", "heterochromia"],
    )
    assert "NEVER mention" in prompt
    assert "silver hair" in prompt
    assert "heterochromia" in prompt
    # The block sits after the preset text.
    assert prompt.index("NEVER mention") > prompt.index("WD14 tags")

    base = prompt_without = build_vlm_prompt("character", ["1girl"])
    assert "NEVER mention" not in prompt_without
    assert prompt.startswith(base)


def test_smart_tag_request_parses_suppressed_traits() -> None:
    """SEP-2: payload list is cleaned (blank entries dropped, strings coerced)."""
    from services.smart_tag_service import _coerce_request

    # enable_vlm=False: without it _coerce_request consults the machine's
    # VLM settings file — passes on configured machines, fails on clean
    # clones/CI (latent env dependency found by the pin-test sweep).
    req = _coerce_request({
        "image_ids": [1],
        "enable_vlm": False,
        "suppressed_traits": ["silver_hair", "", "  red_eyes  ", None],
    })
    assert req.suppressed_traits == ["silver_hair", "red_eyes"]

# ---------------------------------------------------------------------------
# BE-1: tag_scores threading through the Smart Tag pipeline reshapers.
# The three historical "reshaper drops keys" sites are pinned here at the
# unit level; the DB write itself is covered by test_tag_scores.py.
# ---------------------------------------------------------------------------

def test_score_sets_pass_through_partial_and_result(monkeypatch, test_db):
    """A raw single-tagger result's tag_scores must survive
    _booru_partial_from_tag_result -> _assemble_result_dict -> _persist_result
    and land in the tag_scores table."""
    import sqlite3

    import database as db
    from services.smart_tag_service import (
        _assemble_result_dict,
        _booru_partial_from_tag_result,
        _coerce_request,
        _persist_result,
    )

    image_id = db.add_image(
        path="/test/smart/seam.png", filename="seam.png",
        generator="comfyui", metadata_json="{}",
    )
    req = _coerce_request({"image_ids": [image_id], "enable_wd14": True, "enable_vlm": False})
    raw = {
        "general_tags": [{"tag": "1girl", "confidence": 0.95, "category": "general"}],
        "character_tags": [],
        "copyright_tags": [],
        "rating": "general",
        "tag_scores": [
            {"tag": "1girl", "score": 0.95, "category": "general"},
            {"tag": "smile", "score": 0.28, "category": "general"},
        ],
    }
    partial = _booru_partial_from_tag_result(raw, req, score_model="wd-test")
    assert partial["tag_score_sets"] == [
        {"model": "wd-test", "scores": raw["tag_scores"]}
    ]

    result = _assemble_result_dict(partial, "", image_id, req)
    assert result["tag_score_sets"], "reshaper #3 must not drop the sets"

    _persist_result(image_id, result, "replace")
    conn = sqlite3.connect(db.DATABASE_PATH)
    try:
        rows = conn.execute(
            "SELECT model, tag, score FROM tag_scores WHERE image_id = ? ORDER BY tag",
            (image_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("wd-test", "1girl", 0.95), ("wd-test", "smile", 0.28)]


def test_multi_model_sets_ride_fused_result(test_db):
    """Multi-tagger mode: per-model score sets attached to the fused dict
    survive into the partial (one set per model, not fused)."""
    from services.smart_tag_service import (
        _booru_partial_from_tag_result,
        _coerce_request,
        compute_consensus_tags,
    )

    req = _coerce_request({"image_ids": [1], "enable_wd14": True, "enable_vlm": False})
    outputs = [
        {
            "model": "wd-a", "weight": 1.0,
            "general_tags": [{"tag": "1girl", "confidence": 0.9, "category": "general"}],
            "copyright_tags": [], "character_tags": [], "rating": "general",
            "tag_scores": [{"tag": "1girl", "score": 0.9, "category": "general"}],
        },
        {
            "model": "wd-b", "weight": 1.0,
            "general_tags": [{"tag": "1girl", "confidence": 0.8, "category": "general"}],
            "copyright_tags": [], "character_tags": [], "rating": "general",
            "tag_scores": [{"tag": "1girl", "score": 0.8, "category": "general"}],
        },
    ]
    fused = compute_consensus_tags(outputs, consensus_min=2)
    fused["tag_score_sets"] = [
        {"model": o["model"], "scores": o["tag_scores"]}
        for o in outputs if o.get("model") and o.get("tag_scores")
    ]
    partial = _booru_partial_from_tag_result(fused, req)
    models = [s["model"] for s in partial["tag_score_sets"]]
    assert models == ["wd-a", "wd-b"]


def test_terminal_outcome_marks_all_failures_as_failed() -> None:
    from services.smart_tag.pipeline import _terminal_job_outcome

    job = SmartTagJobState(
        job_id="all-persistence-failed",
        status="running",
        total=2,
        processed=2,
        failed=2,
        errors=[
            {
                "image_id": "42",
                "error": "injected database persistence failure",
            }
        ],
    )

    status, message = _terminal_job_outcome(
        job,
        SmartTagRequest(
            image_ids=[41, 42],
            enable_wd14=False,
            enable_vlm=False,
        ),
    )

    assert status == "failed"
    assert "2 image" in message
    assert "injected database persistence failure" in message


def test_terminal_outcome_labels_partial_failure_as_warning() -> None:
    from services.smart_tag.pipeline import _terminal_job_outcome

    job = SmartTagJobState(
        job_id="partial-persistence-failure",
        status="running",
        total=2,
        processed=2,
        succeeded=1,
        failed=1,
    )

    status, message = _terminal_job_outcome(
        job,
        SmartTagRequest(
            image_ids=[41, 42],
            enable_wd14=False,
            enable_vlm=False,
        ),
    )

    assert status == "warning"
    assert "warning" in message.lower()
    assert "1 ok, 1 failed" in message
