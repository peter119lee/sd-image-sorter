"""The one caption-format classifier: tag list vs prose vs mixed vs unknown.

Why this exists
===============
``images.sidecar_caption`` (migration 042) holds text somebody else wrote in a
``.txt``/``.json`` next to the image. That text arrives in two genuinely
different *formats*: Danbooru-style comma-separated tags, and natural-language
prose. Downstream features have to know which — a natural-language-first target
model (AGENTS.md: "Treat Krea 2 as a natural-language-first target") must not be
fed a booru tag dump, and a booru-tag target must not be fed a paragraph.

Format is a different axis from provenance. ``prompt`` / ``ai_caption`` /
``nl_caption`` / ``sidecar_caption`` split text by *who wrote it*, which cannot
be derived from the text. Format *can* be derived from the text, so it is
recorded as an attribute, never as another column of text.

Hard rule pinned here: **the marker may never shorten, normalize or drop text.**
Detection is a pure function of a string to a label; it returns no text at all,
so no caller can accidentally substitute a "cleaned" version for the original.
"""
from __future__ import annotations

import pytest

import caption_format as cf


DANBOORU_TAGS = (
    "masterpiece, best quality, 1girl, hinomori shizuku, solo, slippers, dress, "
    "looking at viewer, open mouth, white thighhighs, full body, window, blush"
)
UNDERSCORED_TAGS = "1girl, silver_hair, red_eyes, looking_at_viewer, masterpiece"
PROSE_CAPTION = (
    "This digital artwork features a blonde, fair-skinned anime-style woman "
    "with blue eyes and a blue hair ribbon. She is depicted holding a glowing, "
    "floating light in her right hand."
)
MIXED_CAPTION = "1girl, solo. She is standing in a field."


class TestVocabulary:
    def test_the_four_values_are_the_contract(self):
        assert cf.CAPTION_FORMAT_TAGS == "tags"
        assert cf.CAPTION_FORMAT_NATURAL == "natural"
        assert cf.CAPTION_FORMAT_MIXED == "mixed"
        assert cf.CAPTION_FORMAT_UNKNOWN == "unknown"
        assert cf.CAPTION_FORMATS == frozenset(
            {"tags", "natural", "mixed", "unknown"}
        )

    def test_every_result_is_one_of_the_four(self):
        for text in (
            DANBOORU_TAGS,
            UNDERSCORED_TAGS,
            PROSE_CAPTION,
            MIXED_CAPTION,
            "",
            "   ",
            "-----",
            '{"a": 1}',
            "1girl",
            "?",
            "\n\n",
        ):
            assert cf.detect_caption_format(text) in cf.CAPTION_FORMATS


class TestTheThreeFormatsThatMatter:
    def test_danbooru_style_tag_list_is_tags(self):
        assert cf.detect_caption_format(DANBOORU_TAGS) == "tags"

    def test_underscored_tag_list_is_tags(self):
        assert cf.detect_caption_format(UNDERSCORED_TAGS) == "tags"

    def test_prose_caption_is_natural(self):
        assert cf.detect_caption_format(PROSE_CAPTION) == "natural"

    def test_genuinely_mixed_file_is_mixed(self):
        assert cf.detect_caption_format(MIXED_CAPTION) == "mixed"

    def test_tags_then_prose_on_separate_lines_is_mixed(self):
        text = "1girl, solo, blue_eyes, school_uniform\nShe is reading a book by the window."
        assert cf.detect_caption_format(text) == "mixed"

    def test_prose_with_internal_commas_is_not_mistaken_for_tags(self):
        """The trap: prose clauses are comma-separated too."""
        assert cf.detect_caption_format(
            "A girl, wearing a red dress, stands in a field."
        ) == "natural"

    def test_all_multi_word_booru_tags_are_still_tags(self):
        """The opposite trap: real danbooru tags are multi-word with spaces."""
        assert cf.detect_caption_format(
            "looking at viewer, hand on own hip, arms behind back, standing on one leg"
        ) == "tags"

    def test_danbooru_punctuation_tags_do_not_fake_a_sentence(self):
        """``?``, ``!``, ``!!``, ``^_^`` are real tags in the owner's library."""
        for text in (
            "masterpiece, best quality, 1girl, socks, ?, white shirt, full body",
            "masterpiece, 1boy, happy birthday, gift, !, !!, blonde hair, balloon",
            "masterpiece, 1girl, open mouth, candy apple, ^_^, blue kimono, bokeh",
        ):
            assert cf.detect_caption_format(text) == "tags", text


class TestDefectsFoundOnTheOwnersRealLibrary:
    """Both of these were real misclassifications, found by running the
    classifier over the owner's 5,242 real sidecar files read-only and
    adjudicating every disagreement with an independent surface oracle.
    """

    def test_a_tag_that_reads_like_a_clause_does_not_make_a_file_mixed(self):
        """``hands on own cheeks`` sits between the ``!?`` and ``?`` tags in a
        real file. Splitting on ``!?`` left it alone in its own chunk, where
        three words plus a function word looked like a short sentence."""
        text = (
            "hanasato minori, masterpiece, best quality, 4girls, star (symbol), "
            "smile, border, !?, hands on own cheeks, ?, no shoes, medium hair, lying"
        )
        assert cf.detect_caption_format(text) == "tags"

    def test_prose_with_four_short_comma_clauses_is_not_mixed(self):
        """Each clause here is individually tag-shaped and the mean length hits
        the relaxed tag ceiling exactly, so this sentence used to register as a
        tag region inside an otherwise entirely prose file."""
        text = (
            "This digital anime-style illustration features a blonde character. "
            "She wears a white, off-shoulder, frilled top with black accents, "
            "and a short black skirt. The background is dark and abstract."
        )
        assert cf.detect_caption_format(text) == "natural"

    def test_a_tag_list_with_one_appended_sentence_is_still_mixed(self):
        """The real shape of the owner's 208 genuinely-mixed files: a dense tag
        run plus one sentence about relative character heights. Fixing the two
        defects above must not cost this."""
        text = (
            "yoisaki kanade, asahina mafuyu, masterpiece, best quality, 2girls, "
            "multiple girls, long hair, purple hair, shirt, parted lips, "
            "track jacket, sidelocks. yoisaki kanade is shorter than asahina mafuyu."
        )
        assert cf.detect_caption_format(text) == "mixed"


class TestUnknownRatherThanAConfidentGuess:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "\n\t\n",
            "-----",
            "...",
            '{"description": "x"}',
            "aGVsbG8" * 40,
        ],
    )
    def test_unrecognizable_or_empty_yields_unknown(self, text):
        assert cf.detect_caption_format(text) == "unknown"

    @pytest.mark.parametrize("value", [None, 12, [], {}, object()])
    def test_non_string_input_is_unknown_not_an_exception(self, value):
        assert cf.detect_caption_format(value) == "unknown"


class TestDetectionCannotAlterOrDropText:
    """The marker exists to decide presentation, never to edit the text."""

    SAMPLES = (
        DANBOORU_TAGS,
        UNDERSCORED_TAGS,
        PROSE_CAPTION,
        MIXED_CAPTION,
        "  leading and trailing spaces are content too  ",
        "line one, tags\nline two is a sentence about it.",
        '{"a": 1}',
        "",
    )

    def test_the_public_api_can_only_return_labels_booleans_or_none(self):
        """No public helper here may hand a caller a rewritten caption.

        If one ever did, a call site could store the "cleaned" version instead
        of the original and the user's text would be silently edited.
        """
        for name in sorted(n for n in dir(cf) if not n.startswith("_")):
            value = getattr(cf, name)
            if not callable(value):
                continue
            for text in self.SAMPLES:
                returned = value(text)
                assert returned is None or isinstance(returned, bool) or (
                    isinstance(returned, str) and returned in cf.CAPTION_FORMATS
                ), f"{name}({text!r}) returned free text: {returned!r}"

    def test_the_same_text_keeps_the_same_identity_through_detection(self):
        for text in self.SAMPLES:
            before = text
            cf.detect_caption_format(text)
            assert text is before and text == before


class TestStorageHelper:
    def test_no_text_means_no_marker_rather_than_unknown(self):
        """NULL = "there is no sidecar text"; 'unknown' = "text we could not read"."""
        for empty in (None, "", "   ", "\n"):
            assert cf.caption_format_for_storage(empty) is None

    def test_text_always_gets_a_marker(self):
        assert cf.caption_format_for_storage(DANBOORU_TAGS) == "tags"
        assert cf.caption_format_for_storage(PROSE_CAPTION) == "natural"
        assert cf.caption_format_for_storage(MIXED_CAPTION) == "mixed"
        assert cf.caption_format_for_storage("-----") == "unknown"


class TestConsolidatedSegmentShapeFilter:
    """The segment-shape rule now has exactly one implementation.

    ``vlm_providers.base._looks_like_garbage_tag`` was the only evidence-backed
    "is this comma segment a tag or prose" test in the tree, built from real VLM
    damage. It moved into ``caption_format`` so the sidecar classifier and Smart
    Tag's VLM parser share it instead of drifting apart. Same object, so Smart
    Tag's answers cannot change.
    """

    def test_smart_tag_and_the_sidecar_classifier_share_one_function(self):
        from vlm_providers import base as vlm_base

        assert vlm_base._looks_like_garbage_tag is cf.looks_like_garbage_tag

    def test_the_shared_constant_table_is_the_same_object(self):
        from vlm_providers import base as vlm_base

        assert vlm_base._PROSE_SUFFIX_CHARS is cf.PROSE_SUFFIX_CHARS

    def test_the_rule_tables_now_have_exactly_one_home(self):
        """Consolidation means one home, not two names for one object.

        ``_MARKDOWN_PREFIXES`` / ``_FORBIDDEN_SUBSTRINGS`` were only ever read
        inside ``_looks_like_garbage_tag``, which moved. Re-exporting them from
        ``vlm_providers.base`` would leave a second name for something with a
        single owner. (Migration 012 keeps its own frozen inline copy on purpose:
        a shipped migration must not change behaviour when a shared module does.)
        """
        from vlm_providers import base as vlm_base

        assert not hasattr(vlm_base, "_MARKDOWN_PREFIXES")
        assert not hasattr(vlm_base, "_FORBIDDEN_SUBSTRINGS")
        assert cf.MARKDOWN_PREFIXES and cf.FORBIDDEN_SUBSTRINGS

    @pytest.mark.parametrize(
        "tag, expected_garbage",
        [
            ("### 1. Address the issue", True),
            ("$$x = 1$$", True),
            ("```python", True),
            ("see https://example.com/x", True),
            ("Description: a beautiful image", True),
            ("1. If you moved your folders", True),
            ("2) The second item is important", True),
            ("a", True),
            ("", True),
            ("hatsune miku", False),
            ("blue archive", False),
            ("saori (blue archive)", False),
            ("long_hair", False),
            ("1girl", False),
        ],
    )
    def test_shared_filter_keeps_its_existing_answers(self, tag, expected_garbage):
        assert cf.looks_like_garbage_tag(tag) is expected_garbage


class TestDetectorsThatWereDeliberatelyNotMerged:
    """Two of the three "same-looking" detectors solve different problems.

    Merging them would have been a regression dressed as cleanup, so this pins
    the disagreement on purpose: if someone later routes these callers through
    ``detect_caption_format`` these assertions fail and say why.
    """

    def test_dataset_translate_tag_router_is_a_cost_heuristic_not_a_classifier(self):
        """It calls any <=5-word string a tag list; that is fine for cache
        routing (a wrong answer only changes token-vs-line caching) and wrong
        for presentation."""
        from services.dataset_translate_parsing import _looks_like_tag_list

        short_prose = "she is standing outside"
        assert _looks_like_tag_list(short_prose) is True
        assert cf.detect_caption_format(short_prose) != "tags"

        prose_with_commas = "A girl, wearing a red dress, stands in a field."
        assert _looks_like_tag_list(prose_with_commas) is True
        assert cf.detect_caption_format(prose_with_commas) == "natural"

    def test_toriigate_structured_check_answers_a_different_question(self):
        """It answers "should I run the caption->tag mapper on this model's
        output", so JSON and prose both return True. Those are different
        formats to us."""
        from toriigate_tagger import ToriiGateTagger

        structured = ToriiGateTagger._looks_like_structured_caption
        assert structured('{"description": "a girl"}') is True
        assert structured("The girl is standing in a field.") is True

        assert cf.detect_caption_format('{"description": "a girl"}') == "unknown"
        assert cf.detect_caption_format("The girl is standing in a field.") == "natural"
