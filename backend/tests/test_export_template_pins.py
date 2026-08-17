"""Characterization pins for ``services.export_template_engine``.

Step-0 safety net locking today's *verbatim* behavior of the LoRA-caption
template engine before any future split. The module is a pure, stateless string
processor (constants + compiled regexes + dataclasses + functions; no globals
rebind, no ``__file__`` reads, no env, no DB, no models, no file IO), so every
pin here runs hermetically on plain dict/list inputs.

Complements the two existing reader suites — ``test_export_template_engine.py``
(blacklist / dedup / kaomoji / multiline) and
``test_export_training_guarantees.py`` (per-image rating / quality buckets /
single-line / Anima category sections). This file adds direct helper-unit
coverage, template-render edge cases, consumer identity seams, and a value-parity
guard, so a split that renames or relocates a name fails loudly here.

DORMANT behaviors are pinned AS-IS, not fixed:
  * ``process_tags`` sorts a ``confidence`` of exactly ``0.0`` as if it were the
    ``1.0`` default (``t.get("confidence") or 1.0`` treats 0.0 as falsy).
  * ``is_kaomoji_tag`` classifies any underscore tag whose every segment is
    <=1 char as an emoticon, so ``"a_"`` / ``"a_b"`` count as kaomoji.
  * ``render_template`` leaves a brace token that fails the variable regex
    (e.g. ``{123}``) as literal text.
"""

from __future__ import annotations


from services import export_template_engine as engine
from services.export_template_engine import (
    DEFAULT_LORA_PRESERVE_PREFIXES,
    PRESETS,
    TEMPLATE_VARIABLES,
    TagProcessingConfig,
    TemplateContext,
    build_export_caption,
    canonical_rating_word,
    flatten_single_line,
    is_kaomoji_tag,
    list_presets,
    normalize_lora_tag,
    process_tags,
    quality_from_aesthetic_score,
    render_template,
    resolve_canonical_rating,
)


# ====================================================================
# Preset registry + documentation surface
# ====================================================================


class TestPresetRegistry:
    EXPECTED_IDS = [
        "anima",
        "anima_tags_only",
        "illustrious_pony",
        "noobai",
        "flux",
        "kohya_sd15",
        "custom",
    ]

    def test_seven_builtin_presets_in_declared_order(self):
        assert list(PRESETS.keys()) == self.EXPECTED_IDS

    def test_every_preset_carries_the_load_bearing_keys(self):
        for preset_id, preset in PRESETS.items():
            for key in ("name", "description", "template", "separator", "single_line"):
                assert key in preset, f"{preset_id} missing {key}"
            assert isinstance(preset["template"], str) and preset["template"]

    def test_custom_preset_is_the_only_multi_line_default(self):
        # v3.4.3: custom templates may span lines; every trainer preset is single-line.
        assert PRESETS["custom"]["single_line"] is False
        for preset_id in (
            "anima",
            "anima_tags_only",
            "illustrious_pony",
            "noobai",
            "flux",
            "kohya_sd15",
        ):
            assert PRESETS[preset_id]["single_line"] is True

    def test_list_presets_exposes_id_and_template_for_each(self):
        listed = list_presets()
        assert [entry["id"] for entry in listed] == self.EXPECTED_IDS
        for entry in listed:
            assert entry["template"] == PRESETS[entry["id"]]["template"]
            assert "name" in entry and "separator" in entry

    def test_template_variables_is_a_named_description_catalog(self):
        names = {row["name"] for row in TEMPLATE_VARIABLES}
        for slot in (
            "{trigger}",
            "{tags:filtered}",
            "{artists:@}",
            "{safety}",
            "{count}",
            "{rating}",
        ):
            assert slot in names
        for row in TEMPLATE_VARIABLES:
            assert isinstance(row["name"], str) and isinstance(row["description"], str)


# ====================================================================
# Tag processing pipeline: blacklist -> replace -> max N -> append
# ====================================================================


class TestTagProcessingPipeline:
    def test_sorts_by_confidence_descending(self):
        tags = [
            {"tag": "low", "confidence": 0.10},
            {"tag": "high", "confidence": 0.99},
            {"tag": "mid", "confidence": 0.50},
        ]
        assert process_tags(tags, TagProcessingConfig()) == ["high", "mid", "low"]

    def test_zero_confidence_sorts_as_full_confidence_DORMANT(self):
        """DORMANT: ``0.0`` confidence is falsy, so ``... or 1.0`` treats it as
        top confidence and it sorts to the front rather than the back."""
        tags = [
            {"tag": "zero", "confidence": 0.0},
            {"tag": "mid", "confidence": 0.5},
            {"tag": "high", "confidence": 0.9},
        ]
        assert process_tags(tags, TagProcessingConfig()) == ["zero", "high", "mid"]

    def test_blacklist_folds_case_and_underscore_when_converting(self):
        tags = [
            {"tag": "Multiple_Girls", "confidence": 0.9},
            {"tag": "keep", "confidence": 0.8},
        ]
        config = TagProcessingConfig(
            blacklist=["multiple girls"], underscore_to_space=True
        )
        assert process_tags(tags, config) == ["keep"]

    def test_replace_then_reblacklist_drops_renamed_blocked_tag(self):
        # Bug 1 at the process_tags surface: blacklist re-checked after replace.
        tags = [
            {"tag": "old", "confidence": 0.9},
            {"tag": "keep", "confidence": 0.8},
        ]
        config = TagProcessingConfig(
            blacklist=["blocked"], replace_rules={"old": "blocked"}
        )
        assert process_tags(tags, config) == ["keep"]

    def test_max_tags_truncates_before_append_is_added(self):
        tags = [
            {"tag": "a", "confidence": 0.9},
            {"tag": "b", "confidence": 0.8},
            {"tag": "c", "confidence": 0.7},
        ]
        config = TagProcessingConfig(max_tags=2, append=["z"])
        # max_tags caps the tag body (a, b); append is added afterwards, uncapped.
        assert process_tags(tags, config) == ["a", "b", "z"]

    def test_empty_tags_returns_blacklist_filtered_append_only(self):
        config = TagProcessingConfig(append=["keep", "drop"], blacklist=["drop"])
        assert process_tags([], config) == ["keep"]
        assert process_tags([], TagProcessingConfig()) == []

    def test_append_dedupes_against_existing_tags(self):
        tags = [{"tag": "solo", "confidence": 0.9}]
        config = TagProcessingConfig(append=["solo", "extra"])
        assert process_tags(tags, config) == ["solo", "extra"]

    def test_underscore_conversion_preserves_prefixes(self):
        tags = [
            {"tag": "long_hair", "confidence": 0.9},
            {"tag": "score_9", "confidence": 0.8},
        ]
        config = TagProcessingConfig(
            underscore_to_space=True, preserve_underscore_prefixes=["score_"]
        )
        assert process_tags(tags, config) == ["long hair", "score_9"]


# ====================================================================
# Kaomoji recognition + underscore formatting
# ====================================================================


class TestKaomoji:
    def test_curated_emoticons_are_recognized(self):
        for tag in ["^_^", "0_0", "o_o", "=_=", ">_<", "@_@", ";_;", "uwu"]:
            assert is_kaomoji_tag(tag) is True, tag

    def test_short_segment_shape_heuristic_DORMANT(self):
        # Every segment <=1 char => emoticon. This is broad by design:
        assert is_kaomoji_tag("v_v") is True
        assert is_kaomoji_tag("a_b") is True  # DORMANT: ordinary-looking a_b counts
        assert is_kaomoji_tag("a_") is True  # DORMANT: trailing-underscore counts
        # ...but multi-char segments and a lone underscore do not.
        assert is_kaomoji_tag("ab_cd") is False
        assert is_kaomoji_tag("_") is False
        assert is_kaomoji_tag("") is False

    def test_format_tag_underscore_keeps_kaomoji_and_prefixes(self):
        assert engine._format_tag_underscore("^_^", []) == "^_^"
        assert engine._format_tag_underscore("score_9", ["score_"]) == "score_9"
        assert engine._format_tag_underscore("long_hair", ["score_"]) == "long hair"


# ====================================================================
# normalize_lora_tag (public re-export used by the .txt exporter)
# ====================================================================


class TestNormalizeLoraTag:
    def test_falsy_input_passes_through_verbatim(self):
        assert normalize_lora_tag("") == ""
        assert normalize_lora_tag(None) is None

    def test_default_preserves_score_prefix_and_spaces_the_rest(self):
        assert normalize_lora_tag("long_hair") == "long hair"
        assert normalize_lora_tag("score_9_up") == "score_9_up"

    def test_empty_prefix_list_converts_every_underscore(self):
        assert normalize_lora_tag("score_9_up", []) == "score 9 up"

    def test_default_preserve_prefix_constant_is_score(self):
        assert DEFAULT_LORA_PRESERVE_PREFIXES == ["score_"]


# ====================================================================
# Rating / quality resolution
# ====================================================================


class TestRatingResolution:
    def test_canonical_word_maps_shorthands_and_markers(self):
        assert canonical_rating_word("g") == "general"
        assert canonical_rating_word("safe") == "general"
        assert canonical_rating_word("rating:explicit") == "explicit"
        assert canonical_rating_word("QUESTIONABLE") == "questionable"

    def test_canonical_word_unknown_is_none(self):
        assert canonical_rating_word("not-a-rating") is None
        assert canonical_rating_word("") is None

    def test_resolve_priority_override_beats_field_beats_tag_rows(self):
        image = {"rating": "sensitive"}
        tags = [{"tag": "explicit", "confidence": 0.99}]
        assert (
            resolve_canonical_rating(image, tags, override="questionable")
            == "questionable"
        )
        assert resolve_canonical_rating(image, tags) == "sensitive"
        assert resolve_canonical_rating({}, tags) == "explicit"

    def test_resolve_from_tag_rows_takes_highest_confidence(self):
        tags = [
            {"tag": "general", "confidence": 0.30},
            {"tag": "explicit", "confidence": 0.90},
        ]
        assert resolve_canonical_rating({}, tags) == "explicit"

    def test_resolve_unrated_returns_empty_string(self):
        assert resolve_canonical_rating({}, [{"tag": "solo", "confidence": 0.9}]) == ""


class TestQualityBuckets:
    def test_lower_edges_are_inclusive(self):
        assert quality_from_aesthetic_score(7.0) == "masterpiece, best quality"
        assert quality_from_aesthetic_score(6.0) == "best quality"
        assert quality_from_aesthetic_score(5.0) == "good quality"
        assert quality_from_aesthetic_score(4.0) == ""  # normal band renders nothing
        assert quality_from_aesthetic_score(3.0) == "low quality"

    def test_below_lowest_bucket_is_worst_quality(self):
        assert quality_from_aesthetic_score(2.9) == "worst quality"
        assert quality_from_aesthetic_score(0.0) == "worst quality"

    def test_none_and_non_numeric_return_none(self):
        assert quality_from_aesthetic_score(None) is None
        assert quality_from_aesthetic_score("not a number") is None


# ====================================================================
# Whitespace flatten + separator cleanup + token dedup
# ====================================================================


class TestFlattenAndCleanup:
    def test_flatten_collapses_all_whitespace_including_newlines(self):
        assert flatten_single_line("a\n\nb\tc  d") == "a b c d"
        assert flatten_single_line(None) == ""

    def test_cleanup_collapses_duplicate_and_edge_separators(self):
        assert engine._cleanup_separators(", , tag1, tag2, ", ", ") == "tag1, tag2"
        assert engine._cleanup_separators("tag1, , , tag2", ", ") == "tag1, tag2"

    def test_cleanup_strips_trailing_period_for_short_tag_tail(self):
        assert engine._cleanup_separators("tag1, tag2.", ", ") == "tag1, tag2"

    def test_cleanup_keeps_period_for_long_sentence_tail(self):
        text = "tag1, tag2, this is a long sentence tail here."
        assert engine._cleanup_separators(text, ", ") == text


class TestDedupTokens:
    def test_collapses_exact_underscore_space_and_case_variants(self):
        assert engine._dedup_tokens("a, a, b", ", ") == "a, b"
        assert engine._dedup_tokens("my_oc, My OC, other", ", ") == "my_oc, other"

    def test_sentence_zone_strips_leading_tag_echo_but_keeps_prose(self):
        # First ". " splits tag-zone from prose; a leading run of already-seen
        # tags in the sentence is shed, remaining prose (with its commas) stays.
        text = "1girl, long hair. 1girl, long hair, a girl stands"
        assert engine._dedup_tokens(text, ", ") == "1girl, long hair. a girl stands"


# ====================================================================
# Template-value helpers
# ====================================================================


class TestTemplateValueHelpers:
    def test_split_template_value_drops_empty_chunks(self):
        assert engine._split_template_value("a, b,, c", ", ") == ["a", "b", "c"]
        assert engine._split_template_value("", ", ") == []

    def test_filter_template_value_removes_blacklisted_chunks(self):
        config = TagProcessingConfig(blacklist=["bad"])
        assert (
            engine._filter_template_value("good, bad, ok", config, ", ") == "good, ok"
        )

    def test_extract_count_tag_matches_danbooru_count_forms(self):
        assert engine._extract_count_tag(["solo", "2girls", "tree"]) == "2girls"
        assert engine._extract_count_tag(["6+boys"]) == "6+boys"
        assert engine._extract_count_tag(["no count here"]) == ""

    def test_category_norm_folds_underscore_and_case(self):
        assert engine._category_norm("Blue_Archive") == "blue archive"

    def test_split_tags_by_type_prefers_category_then_heuristic(self):
        buckets = engine._split_tags_by_type(
            ["miku", "vocaloid", "artistx", "long hair", "hatsune miku (vocaloid)"],
            {"miku": "character", "vocaloid": "copyright", "artistx": "artist"},
        )
        assert buckets["characters"] == ["miku", "hatsune miku (vocaloid)"]
        assert buckets["copyright"] == ["vocaloid"]
        assert buckets["artists"] == ["artistx"]
        assert buckets["general"] == ["long hair"]


# ====================================================================
# render_template variable substitution
# ====================================================================


class TestRenderTemplate:
    def _ctx(self):
        return TemplateContext(tags_filtered=["a", "b", "c", "d"], separator=", ")

    def test_tags_n_numeric_takes_top_n(self):
        assert render_template("{tags:2}", self._ctx()) == "a, b"

    def test_tags_filtered_returns_all(self):
        assert render_template("{tags:filtered}", self._ctx()) == "a, b, c, d"

    def test_invalid_tags_suffix_renders_empty(self):
        assert render_template("{tags:xyz}", self._ctx()) == ""

    def test_unknown_variable_renders_empty(self):
        assert render_template("{unknown_var}", self._ctx()) == ""

    def test_non_identifier_brace_is_left_literal_DORMANT(self):
        # DORMANT: {123} fails the [a-zA-Z_]+ variable regex, so it is not a
        # variable and survives verbatim.
        assert render_template("lit {123} text", self._ctx()) == "lit {123} text"

    def test_artists_at_modifier_prefixes_each_artist(self):
        ctx = TemplateContext(
            tags_filtered=["some_artist"],
            category_by_norm={"some artist": "artist"},
            separator=", ",
        )
        assert render_template("{artists:@}", ctx) == "@some_artist"


# ====================================================================
# build_export_caption integration contracts (beyond the reader suites)
# ====================================================================


class TestBuildExportCaption:
    def test_unknown_preset_falls_back_to_custom_template(self):
        rendered = build_export_caption(
            {}, [{"tag": "x", "confidence": 1.0}], preset_id="does_not_exist"
        )
        # custom template is "{tags:filtered}"
        assert rendered == "x"

    def test_preset_default_append_renders_when_no_override(self):
        rendered = build_export_caption(
            {},
            [{"tag": "solo", "confidence": 0.9}],
            preset_id="illustrious_pony",
            trigger="t",
        )
        # illustrious_pony keeps underscores; default_append is "masterpiece, best_quality"
        assert rendered == "t, solo, masterpiece, best_quality"

    def test_user_append_lands_in_tags_filtered_not_append_slot(self):
        rendered = build_export_caption(
            {},
            [{"tag": "solo", "confidence": 0.9}],
            preset_id="illustrious_pony",
            trigger="t",
            append=["myextra"],
        )
        # user-supplied append flows through the pipeline into {tags:filtered};
        # the {append} slot renders empty (preset default suppressed).
        assert rendered == "t, solo, myextra"

    def test_rating_slot_removes_rating_markers_from_tag_variables(self):
        rendered = build_export_caption(
            {},
            [
                {"tag": "explicit", "confidence": 0.9},
                {"tag": "solo", "confidence": 0.8},
            ],
            preset_id="custom",
            template_override="{rating}, {tags:filtered}",
        )
        # rating marker rendered once via {rating}; stripped out of {tags:filtered}
        assert rendered == "explicit, solo"


# ====================================================================
# Consumer identity seams + value parity
# ====================================================================


class TestConsumerSeams:
    def test_smart_tag_consensus_shares_is_kaomoji_identity(self):
        from services.smart_tag import consensus

        assert consensus.is_kaomoji_tag is engine.is_kaomoji_tag

    def test_smart_tag_results_shares_is_kaomoji_identity(self):
        from services.smart_tag import results

        assert results.is_kaomoji_tag is engine.is_kaomoji_tag

    def test_lora_preserve_prefix_value_parity_with_tag_export(self):
        # tag_export/captions.py redeclares this list literally rather than
        # importing the constant; pin their VALUES equal so a future engine
        # change to the convention surfaces the drift here.
        from services.tag_export import captions

        assert (
            captions.LORA_PRESERVE_UNDERSCORE_PREFIXES == DEFAULT_LORA_PRESERVE_PREFIXES
        )
