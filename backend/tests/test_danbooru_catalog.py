"""Pins for the bundled StoryAura/Danbooru-Dataset-csv assets (MIT)."""

from pathlib import Path

import danbooru_catalog
import tag_rules
from services import tag_suggest_service
from services import tag_training_filters as filters

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "backend" / "assets"


def test_storyaura_license_is_mit_and_not_gpl():
    license_text = (ASSETS / "STORYAURA_LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "StoryAura" in license_text
    assert "GPL" not in license_text
    zh = (ASSETS / "danbooru_zh.csv").read_text(encoding="utf-8", errors="replace")
    assert zh.splitlines()[0].startswith("tag,")
    assert "长发" in zh


def test_bundled_cjk_query_resolves_long_hair():
    tag_suggest_service.reset_cache()
    result = tag_suggest_service.suggest(q="长发")
    tags = [row["tag"] for row in result["suggestions"]]
    assert result["danbooru_loaded"] is True
    assert result["zh_loaded"] is True
    assert "long_hair" in tags
    hit = next(row for row in result["suggestions"] if row["tag"] == "long_hair")
    assert hit["zh"]
    assert "长" in hit["zh"]


def test_character_info_includes_copyright_and_cjk_alias():
    tag_suggest_service.reset_cache()
    info = tag_suggest_service.get_tag_info("hatsune_miku")
    assert info["found_in_vocab"] is True
    assert info["canonical"] == "hatsune_miku"
    assert info["copyright"] == "vocaloid"
    assert info["category"] == "character"
    assert info["zh"]

    aliased = tag_suggest_service.get_tag_info("长发")
    assert aliased["canonical"] == "long_hair"
    assert aliased["found_in_vocab"] is True

    miku = tag_suggest_service.get_tag_info("初音未来")
    assert miku["canonical"] == "hatsune_miku"
    assert miku["copyright"] == "vocaloid"


def test_storyaura_parent_implication_collapses_count_tags():
    filters.reset_implication_cache_for_tests()
    try:
        assert filters.collapse_implications(["2girls", "multiple_girls", "smile"]) == [
            "2girls",
            "smile",
        ]
    finally:
        filters.reset_implication_cache_for_tests()


def test_popular_character_classifies_without_wd14(monkeypatch):
    monkeypatch.setattr(tag_rules, "_wd14_character_tags", set())
    monkeypatch.setattr(tag_rules, "_booru_tag_categories", {})
    monkeypatch.setattr(tag_rules, "_storyaura_character_tags", None)
    danbooru_catalog.reset_catalog_for_tests()
    assert tag_rules.categorize_tag("hatsune_miku") == "character"
    assert tag_rules.categorize_tag("zzz_made_up_tag") == "unknown"


def test_extra_vocab_includes_popular_missing_characters():
    danbooru_catalog.reset_catalog_for_tests()
    extras = danbooru_catalog.extra_vocab_rows(existing_tags=set())
    assert extras
    assert all(row[2] == 4 for row in extras)
    character = danbooru_catalog.get_character("hatsune_miku")
    assert character is not None
    assert character["copyright"] == "vocaloid"
    assert character["post_count"] >= 1000
    without_miku = danbooru_catalog.extra_vocab_rows(existing_tags={"hatsune_miku"})
    assert all(row[0] != "hatsune_miku" for row in without_miku)
