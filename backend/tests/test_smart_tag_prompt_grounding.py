"""Smart Tag must not put prose into the ``{tags}`` slot of a VLM prompt.

Every ``user_prompt_with_tags`` preset tells the model that the interpolated text
is danbooru-style tags, and the Krea 2 preset says so twice, so a prose clause
there is a false statement about the input rather than a weak hint. The variant
used to be chosen purely on "are there any tags", never on whether the text
actually is tags.
"""
from __future__ import annotations

from caption_format import detect_caption_format, looks_like_garbage_tag
from services.smart_tag.prompts import _vlm_context_tags_for


def test_krea2_prompt_never_receives_prose_in_its_tags_slot() -> None:
    from vlm_providers.base import VLMConfig, VLMProvider
    from vlm_providers.registry import PROMPT_PRESETS

    prose_seed = "She is standing in a sunlit classroom looking at the camera"
    assert detect_caption_format(prose_seed) == "natural"
    partial = {
        "general_names": ["1girl", "solo", prose_seed],
        "copyright_names": [],
        "character_names": [],
    }

    grounding = _vlm_context_tags_for(partial, True, "general", "")

    assert grounding == ["1girl", "solo"]
    preset = PROMPT_PRESETS["krea2_long_nl"]
    config = VLMConfig(
        user_prompt=preset["user_prompt"],
        user_prompt_with_tags=preset["user_prompt_with_tags"],
    )
    message = VLMProvider(config).build_user_message(grounding)
    assert prose_seed not in message


def test_a_prose_only_seed_falls_back_to_the_no_tags_prompt_variant() -> None:
    from vlm_providers.base import VLMConfig, VLMProvider
    from vlm_providers.registry import PROMPT_PRESETS

    prose_seed = (
        "A young woman stands in the middle of a sunlit classroom and looks "
        "straight at the camera."
    )
    partial = {
        "general_names": [prose_seed],
        "copyright_names": [],
        "character_names": [],
    }

    assert _vlm_context_tags_for(partial, True, "general", "") is None

    preset = PROMPT_PRESETS["krea2_long_nl"]
    config = VLMConfig(
        user_prompt=preset["user_prompt"],
        user_prompt_with_tags=preset["user_prompt_with_tags"],
    )
    message = VLMProvider(config).build_user_message(None)
    assert message == preset["user_prompt"]
    assert "danbooru" not in message.lower()


def test_real_wd14_tag_names_survive_the_tags_slot_guard() -> None:
    """Every one of these is a real tag in the owner's library.

    Each primitive alone would delete some of them, which is why the guard
    requires both to agree: ``?``, ``!?``, ``^_^`` and ``@_@`` fail the
    segment-shape rule, while ``kamiyama high school uniform (project sekai)``
    classifies as prose when read on its own.
    """
    names = [
        "1girl",
        "solo",
        "long_hair",
        "looking at viewer",
        "hand on own hip",
        "kamiyama high school uniform (project sekai)",
        "hands on own cheeks",
        "!?",
        "^_^",
        "@_@",
        "?",
        ":d",
        "v",
    ]
    # The blind spots the guard has to survive, stated as facts rather than
    # trusted silently.
    assert looks_like_garbage_tag("!?") is True
    assert looks_like_garbage_tag("?") is True
    assert detect_caption_format("kamiyama high school uniform (project sekai)") == (
        "natural"
    )
    partial = {
        "general_names": list(names),
        "copyright_names": [],
        "character_names": [],
    }

    assert _vlm_context_tags_for(partial, True, "general", "") == names


def test_grounding_stays_off_when_the_user_turned_it_off() -> None:
    partial = {
        "general_names": ["1girl", "solo"],
        "copyright_names": [],
        "character_names": [],
    }

    assert _vlm_context_tags_for(partial, False, "general", "") is None
