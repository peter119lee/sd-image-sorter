"""Tagger audit trio — P2-19 purpose filter, P2-18 implication dedup,
P1-17 trait candidates (owner-approved 2026-07-07)."""

import pytest

from services import tag_training_filters as filters
from services.tag_export_service import build_sidecar_content
from services.trait_pruning_service import (
    TraitCandidatesRequest,
    classify_trait_family,
    compute_trait_candidates,
)


def _row(tag, category="general", source="tagger", confidence=0.9):
    return {
        "tag": tag,
        "category": category,
        "source": source,
        "confidence": confidence,
    }


class TestPurposeFilterRows:
    def test_character_purpose_drops_character_rows_only_with_trigger(self):
        rows = [
            _row("1girl"),
            _row("hatsune_miku", category="character"),
            _row("vocaloid", category="copyright"),
        ]

        without_trigger = filters.filter_tag_rows_by_training_purpose(
            rows, "character", ""
        )
        assert [r["tag"] for r in without_trigger] == [
            "1girl",
            "hatsune_miku",
            "vocaloid",
        ]

        with_trigger = filters.filter_tag_rows_by_training_purpose(
            rows, "character", "mikuv1"
        )
        assert [r["tag"] for r in with_trigger] == ["1girl", "vocaloid"]

    def test_style_purpose_drops_artist_category_rows(self):
        rows = [_row("1girl"), _row("wlop", category="artist"), _row("scenery")]
        result = filters.filter_tag_rows_by_training_purpose(rows, "style", "")
        assert [r["tag"] for r in result] == ["1girl", "scenery"]

    def test_style_purpose_drops_general_stored_style_tags(self):
        # Regression: WD14 labels these 'general', but they name the STYLE itself
        # (era / art style / medium / coloring) and must be stripped for a style
        # LoRA so the trigger — not an explicit tag — carries the look. A word
        # blacklist can't enumerate them; the semantic classifier catches them by
        # shape. Counts (1girl) and content (long_hair, scenery) must survive.
        rows = [
            _row("1girl"),
            _row("1990s_(style)"),
            _row("retro_artstyle"),
            _row("marker_(medium)"),
            _row("anime_coloring"),
            _row("long_hair"),
            _row("scenery"),
        ]
        result = filters.filter_tag_rows_by_training_purpose(rows, "style", "")
        assert [r["tag"] for r in result] == ["1girl", "long_hair", "scenery"]

    def test_purpose_aliases_match_smart_tag_vocabulary(self):
        # The export engine and Smart Tag share one normalizer — pin the
        # aliases that used to live only in smart_tag_service.
        from services.smart_tag_service import (
            normalize_training_purpose as smart_normalize,
        )

        for alias, expected in [
            ("style_lora", "style"),
            ("char", "character"),
            ("nsfw", "general"),
            ("", "general"),
        ]:
            assert filters.normalize_training_purpose(alias) == expected
            assert smart_normalize(alias) == expected

    def test_legacy_rows_without_category_fall_back_to_classifier(self, monkeypatch):
        # categorize_tag learns character tags from the selected_tags.csv of
        # whichever WD14 taggers THIS MACHINE has downloaded — pin the module
        # caches so the verdict is deterministic (this test was green on the
        # owner's box and red on any fresh checkout/worktree without models).
        import tag_rules
        monkeypatch.setattr(tag_rules, "_wd14_character_tags", {"hatsune_miku"})
        monkeypatch.setattr(
            tag_rules, "_booru_tag_categories", {"hatsune_miku": "character"}
        )
        monkeypatch.setattr(tag_rules, "_storyaura_character_tags", set())
        rows = [_row("hatsune_miku", category=""), _row("silver_hair", category="")]
        result = filters.filter_tag_rows_by_training_purpose(rows, "character", "trig")
        assert [r["tag"] for r in result] == ["silver_hair"]


class TestImplicationDedup:
    def test_child_collapses_parent_transitively(self):
        tags = ["school_swimsuit", "one-piece_swimsuit", "swimsuit", "1girl"]
        assert filters.collapse_implications(tags) == ["school_swimsuit", "1girl"]

    def test_parent_alone_survives(self):
        assert filters.collapse_implications(["animal_ears", "1girl"]) == [
            "animal_ears",
            "1girl",
        ]

    def test_space_and_underscore_forms_match(self):
        assert filters.collapse_implications(["cat ears", "animal_ears"]) == [
            "cat ears"
        ]

    def test_dropin_table_extends_bundled(self, tmp_path, monkeypatch):
        dropin = tmp_path / "danbooru_implications.csv"
        dropin.write_text("custom_child,custom_parent\n", encoding="utf-8")
        monkeypatch.setattr(filters, "_dropin_implications_path", lambda: dropin)
        filters.reset_implication_cache_for_tests()
        try:
            assert filters.collapse_implications(["custom_child", "custom_parent"]) == [
                "custom_child"
            ]
            # bundled entries still active alongside the drop-in
            assert filters.collapse_implications(["katana", "sword", "weapon"]) == [
                "katana"
            ]
        finally:
            filters.reset_implication_cache_for_tests()


class TestExportEngineIntegration:
    IMAGE = {
        "id": 1,
        "prompt": "",
        "negative_prompt": "",
        "ai_caption": "",
        "nl_caption": "",
    }

    def test_tags_mode_applies_purpose_and_implications(self):
        rows = [
            _row("cat_ears"),
            _row("animal_ears"),
            _row("hatsune_miku", category="character"),
            _row("1girl"),
        ]
        rendered = build_sidecar_content(
            self.IMAGE,
            rows,
            content_mode="tags",
            training_purpose="character",
            template_options={"trigger": "mikuv1"},
            dedupe_implications=True,
        )
        assert rendered == "cat ears, 1girl"

    def test_defaults_off_reproduce_previous_output(self):
        rows = [
            _row("cat_ears"),
            _row("animal_ears"),
            _row("hatsune_miku", category="character"),
        ]
        rendered = build_sidecar_content(self.IMAGE, rows, content_mode="tags")
        assert rendered == "cat ears, animal ears, hatsune miku"

    def test_character_purpose_without_trigger_keeps_name(self):
        rows = [_row("hatsune_miku", category="character"), _row("1girl")]
        rendered = build_sidecar_content(
            self.IMAGE, rows, content_mode="tags", training_purpose="character"
        )
        assert rendered == "hatsune miku, 1girl"


class TestTraitFamilies:
    @pytest.mark.parametrize(
        "tag,family",
        [
            ("silver_hair", "hair"),
            ("twin_braids", "hair"),
            ("red_eyes", "eyes"),
            ("slit_pupils", "eyes"),
            ("dark_skin", "skin"),
            ("cat_ears", "body"),
            ("mole_under_eye", "body"),
            ("large_breasts", "body"),
        ],
    )
    def test_trait_tags_classify(self, tag, family):
        assert classify_trait_family(tag) == family

    @pytest.mark.parametrize(
        "tag",
        [
            "school_uniform",
            "looking_at_viewer",
            "1girl",
            "sitting",
            "adjusting_hair",
            "closed_eyes",
            "hair_ornament",
            "shiny_skin",
        ],
    )
    def test_non_traits_rejected(self, tag):
        assert classify_trait_family(tag) is None


class TestTraitCandidatesEndpointLogic:
    def _seed(self, test_db):
        import database as db

        ids = []
        for index in range(4):
            image_id = db.add_image(
                path=f"L:/fake/trait_{index}.png",
                filename=f"trait_{index}.png",
                generator="ComfyUI",
            )
            ids.append(image_id)
        import db_tags

        common = [
            {"tag": "silver_hair", "confidence": 0.9},
            {"tag": "red_eyes", "confidence": 0.9},
            {"tag": "school_uniform", "confidence": 0.9},
        ]
        batch = [{"image_id": image_id, "tags": list(common)} for image_id in ids]
        # one-off trait present in a single image only — must fall below 0.6
        batch[0]["tags"] += [
            {"tag": "wet_hair", "confidence": 0.9},
            {"tag": "cat_ears", "confidence": 0.9},
        ]
        db_tags.add_tags_batch(batch, default_source="tagger")
        return ids

    def test_candidates_ranked_and_thresholded(self, test_db):
        ids = self._seed(test_db)
        result = compute_trait_candidates(
            TraitCandidatesRequest(image_ids=ids, min_ratio=0.6)
        )
        assert result["total_images"] == 4
        tags = {item["tag"]: item for item in result["candidates"]}
        assert set(tags) == {"silver_hair", "red_eyes"}
        assert tags["silver_hair"]["count"] == 4
        assert tags["silver_hair"]["ratio"] == 1.0
        assert tags["silver_hair"]["family"] == "hair"
        # clothing never offered; sub-threshold one-off trait dropped
        assert "school_uniform" not in tags
        assert "cat_ears" not in tags

    def test_low_ratio_surfaces_rare_traits(self, test_db):
        ids = self._seed(test_db)
        result = compute_trait_candidates(
            TraitCandidatesRequest(image_ids=ids, min_ratio=0.2)
        )
        tags = {item["tag"] for item in result["candidates"]}
        assert "cat_ears" in tags

    def test_requires_ids_or_token(self):
        with pytest.raises(ValueError):
            TraitCandidatesRequest()

    def test_endpoint_shape(self, test_client, test_db):
        ids = self._seed(test_db)
        response = test_client.post(
            "/api/tags/trait-candidates", json={"image_ids": ids}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_images"] == 4
        assert all(
            {"tag", "family", "count", "ratio"} <= set(item)
            for item in payload["candidates"]
        )


def test_scan_text_for_blacklisted_terms_folds_underscores():
    from services.tag_training_filters import scan_text_for_blacklisted_terms

    text = "A girl with silver hair and a red ribbon smiling in a park."
    leaks = scan_text_for_blacklisted_terms(text, ["silver_hair", "blue_eyes", "red ribbon"])
    assert leaks == ["silver_hair", "red ribbon"]

    # Word-bounded: "hairband" must not match "hair".
    assert scan_text_for_blacklisted_terms("a silver hairband", ["silver_hair"]) == []
    # Empty inputs never crash.
    assert scan_text_for_blacklisted_terms("", ["x"]) == []
    assert scan_text_for_blacklisted_terms("text", []) == []


def test_format_trait_suppression_block_dedupes_and_folds():
    from services.tag_training_filters import format_trait_suppression_block

    block = format_trait_suppression_block(["silver_hair", "Silver Hair", "red_eyes", ""])
    assert "silver hair" in block
    assert "red eyes" in block
    assert block.count("silver hair") == 1
    assert format_trait_suppression_block([]) == ""
    assert format_trait_suppression_block(None) == ""
