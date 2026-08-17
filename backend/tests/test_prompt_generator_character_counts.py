"""The generator must not hand the user a prompt he has to correct by hand.

Danbooru character-count semantics, from the official wiki
(danbooru.donmai.us/wiki_pages/solo and .../tag_group:character_count):

* ``solo`` means a single character in the whole image. ``solo + 1girl`` and
  ``solo + 1boy`` are the wiki's own examples, so those are NOT conflicts.
* Counter tags are per sex (girl / boy / other). Two characters means two
  counts, so ``1girl + 1boy`` must use ``solo_focus``, never ``solo``.
* ``solo_focus`` is the tag for "several characters, one is the focus", so
  ``solo_focus + 2girls`` is correct and must not be flagged.
* ``multiple_girls`` implies at least two girls.

A repeated token is not cosmetic either — Stable Diffusion re-weights the
concept, so an accidental second ``1girl`` changes the image.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from prompt_generator import PromptGenerator


# Everything unrelated pinned off so the assertion is about count tags only.
QUIET_CONFIG: Dict[str, Any] = {
    "quality_preset": "none",
    "include_negative": False,
    "outfit": "none",
    "pose": "none",
    "angle": "none",
    "body": "none",
    "expression": "none",
    "background": "none",
    "style": "none",
    "artist": "none",
    "seed": 7,
}


@pytest.fixture
def generator() -> PromptGenerator:
    return PromptGenerator()


def _tokens(result: Dict[str, Any]) -> List[str]:
    return [token.strip() for token in result["positive_prompt"].split(",") if token.strip()]


def _duplicates(tokens: List[str]) -> List[str]:
    seen: set[str] = set()
    dupes: List[str] = []
    for token in tokens:
        key = token.lower().replace(" ", "_")
        if key in seen and token not in dupes:
            dupes.append(token)
        seen.add(key)
    return dupes


class TestNoDuplicateTags:
    def test_character_slot_repeating_the_count_tag_emits_it_once(self, generator):
        """`1girl` in both slots is the default, most common configuration."""
        result = generator.generate({**QUIET_CONFIG, "character": "1girl", "count_tag": "1girl"})

        tokens = _tokens(result)
        assert _duplicates(tokens) == []
        assert tokens.count("1girl") == 1

    def test_default_count_tag_does_not_duplicate_the_chosen_character(self, generator):
        """Reproduces the audit case exactly: count_tag left at its default."""
        result = generator.generate({**QUIET_CONFIG, "character": "1girl"})

        tokens = _tokens(result)
        assert _duplicates(tokens) == []
        assert tokens.count("1girl") == 1
        assert tokens.count("solo") == 1

    def test_case_and_spacing_variants_are_still_one_tag(self, generator):
        result = generator.generate({**QUIET_CONFIG, "character": "1Girl", "count_tag": "1girl"})

        assert _duplicates(_tokens(result)) == []


class TestNoSelfContradictingOutput:
    def test_solo_is_not_injected_next_to_a_multi_character_choice(self, generator):
        """The generator adds `solo` itself; it must not add one that
        contradicts what the user explicitly asked for."""
        result = generator.generate({**QUIET_CONFIG, "character": "2girls", "count_tag": "1girl"})

        tokens = _tokens(result)
        assert "solo" not in tokens
        assert "2girls" in tokens

    def test_conflicting_counts_the_user_chose_are_reported(self, generator):
        """Both values came from user-facing slots, so they are warned about
        rather than silently dropped."""
        result = generator.generate({**QUIET_CONFIG, "character": "2girls", "count_tag": "1girl"})

        warnings = result["warnings"]
        assert warnings, "a prompt asking for one girl and two girls must not report success"
        joined = " ".join(warnings)
        assert "1girl" in joined
        assert "2girls" in joined

    def test_multiple_girls_also_suppresses_the_injected_solo(self, generator):
        """`multiple_girls` is in the built-in character pool, so this is
        reachable straight from the category browser."""
        result = generator.generate({**QUIET_CONFIG, "character": "multiple_girls", "count_tag": "1girl"})

        assert "solo" not in _tokens(result)
        assert result["warnings"]

    def test_a_legitimate_single_character_prompt_keeps_solo_and_warns_nothing(self, generator):
        """Guard against over-blocking: `1girl, solo` is the canonical pair."""
        result = generator.generate({**QUIET_CONFIG, "character": "1girl", "count_tag": "1girl"})

        tokens = _tokens(result)
        assert "solo" in tokens
        assert result["warnings"] == []


class TestManualSlotPath:
    """Prompt Lab's builder always posts `categories`, so this is the path the
    shipped UI actually uses."""

    def test_slot_selection_of_multiple_girls_suppresses_the_injected_solo(self, generator):
        result = generator.generate({
            **QUIET_CONFIG,
            "count_tag": "1girl",
            "categories": {"character": {"tags": ["multiple_girls"]}},
        })

        assert "solo" not in _tokens(result)
        assert result["warnings"]

    def test_solo_and_multiple_girls_in_one_slot_are_reported(self, generator):
        """Both tags sit in the built-in character pool, so a user can pick
        both from the browser and today gets no warning at all."""
        result = generator.generate({
            **QUIET_CONFIG,
            "count_tag": "",
            "categories": {"character": {"tags": ["solo", "multiple_girls"]}},
        })

        assert result["warnings"], "solo plus multiple_girls cannot both be true"

    def test_slot_path_still_reports_clean_for_a_consistent_selection(self, generator):
        result = generator.generate({
            **QUIET_CONFIG,
            "count_tag": "",
            "categories": {"character": {"tags": ["1girl", "solo"]}},
        })

        assert result["warnings"] == []
        assert _duplicates(_tokens(result)) == []


class TestValidateReportsCountContradictions:
    """`/api/prompts/validate` is the Validate button; the same rules must
    reach it, since that is where the user asks "is this prompt sane?"."""

    def test_solo_with_multiple_girls_is_invalid(self, generator):
        result = generator.validate_prompt(["solo", "multiple_girls"])

        assert result["valid"] is False
        conflicting = " ".join(
            tag
            for violation in result["violations"]
            for tag in violation["conflicting_tags"] + violation["triggering_tags"]
        )
        assert "solo" in conflicting
        assert "multiple_girls" in conflicting

    def test_solo_with_two_girls_is_invalid(self, generator):
        assert generator.validate_prompt(["solo", "2girls"])["valid"] is False

    def test_two_different_girl_counts_are_invalid(self, generator):
        assert generator.validate_prompt(["1girl", "2girls"])["valid"] is False

    def test_one_girl_and_one_boy_cannot_be_solo(self, generator):
        """Two characters, so the wiki prescribes solo_focus, not solo."""
        assert generator.validate_prompt(["1girl", "1boy", "solo"])["valid"] is False

    def test_solo_and_solo_focus_together_are_invalid(self, generator):
        assert generator.validate_prompt(["solo", "solo_focus"])["valid"] is False

    @pytest.mark.parametrize("tags", [
        ["1girl", "solo"],
        ["1boy", "solo"],
        ["1girl", "1boy"],
        ["solo_focus", "2girls"],
        ["solo_focus", "1girl", "1boy"],
        ["2girls"],
        ["6+girls"],
        ["1girl", "solo", "blonde_hair", "long_sleeves"],
    ])
    def test_legitimate_combinations_stay_valid(self, generator, tags):
        """Over-blocking would be its own defect: these are all correct
        Danbooru tag sets."""
        assert generator.validate_prompt(tags)["valid"] is True, tags

    def test_six_plus_girls_counts_as_multiple(self, generator):
        assert generator.validate_prompt(["solo", "6+girls"])["valid"] is False

    def test_spacing_variants_are_recognized(self, generator):
        """WD14 and Danbooru spellings differ by underscores and spaces."""
        assert generator.validate_prompt(["solo", "multiple girls"])["valid"] is False
