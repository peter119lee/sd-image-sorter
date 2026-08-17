"""Pins that keep the caption-format classifier apart from its look-alikes.

`caption_format.detect_caption_format` answers ONE question: is this human
caption text Danbooru tags, prose, or both? Four other functions in this tree
look adjacent and are not. A previous slice pinned two of them
(`dataset_translate_parsing._looks_like_tag_list`,
`toriigate_caption_parsing._looks_like_structured_caption`) in
`test_sidecar_caption_format_field.py` so that a future "consolidation" would
fail loudly instead of quietly changing behaviour.

This file pins the fourth: `image_metadata_writer._classify_embedded_text`.
Read-only investigation only — the module under inspection is not modified here.
"""
from __future__ import annotations

import json

from caption_format import detect_caption_format
from services.image_metadata_writer import _classify_embedded_text


TAG_LIST = "1girl, solo, long hair, looking at viewer, school uniform"
PROSE = (
    "A young woman stands in the middle of a sunlit classroom, and she is "
    "looking straight at the camera while the light falls from the left."
)


def test_classify_embedded_text_names_a_png_chunk_not_a_caption_dialect() -> None:
    """It answers "which PNG text chunk carries this?", a container question.

    Its whole vocabulary is chunk keys — ``parameters`` for an A1111 block,
    ``prompt`` for a ComfyUI API graph, ``Comment`` for a NovelAI payload,
    ``UserComment`` for everything else. None of those are caption formats, and
    it never returns one of ``detect_caption_format``'s four labels.
    """
    a1111 = "1girl, solo\nNegative prompt: bad\nSteps: 20, Sampler: Euler a"
    comfy = json.dumps({"3": {"class_type": "KSampler", "inputs": {}}})
    novelai = json.dumps({"prompt": "1girl", "uc": "bad quality"})

    assert _classify_embedded_text(a1111) == "parameters"
    assert _classify_embedded_text(comfy) == "prompt"
    assert _classify_embedded_text(novelai) == "Comment"
    assert _classify_embedded_text(PROSE) == "UserComment"

    caption_labels = {"tags", "natural", "mixed", "unknown"}
    for text in (a1111, comfy, novelai, PROSE, TAG_LIST):
        assert _classify_embedded_text(text) not in caption_labels


def test_the_two_detectors_disagree_on_exactly_the_distinction_that_matters() -> None:
    """A tag list and a prose caption are ONE answer there and TWO answers here.

    This is the whole reason the two must not be merged: the chunk classifier is
    deliberately blind to caption dialect, and the caption classifier is
    deliberately blind to serialization envelope. Folding either into the other
    would delete the distinction its own caller depends on.
    """
    assert _classify_embedded_text(TAG_LIST) == _classify_embedded_text(PROSE)
    assert detect_caption_format(TAG_LIST) != detect_caption_format(PROSE)
    assert detect_caption_format(TAG_LIST) == "tags"
    assert detect_caption_format(PROSE) == "natural"


def test_merging_them_would_break_the_reader_metadata_preservation_gate() -> None:
    """Its one non-harvest caller gates DATA PRESERVATION on the chunk name.

    ``_is_irreplaceable_generation_record`` keeps a source chunk only when this
    classifier calls it a full generation record. A ComfyUI graph is JSON, which
    ``detect_caption_format`` cannot place, so a caption-dialect answer wired in
    here would stop protecting the single most valuable thing in a generated PNG.
    """
    from services.image_metadata_writer import _is_irreplaceable_generation_record

    comfy = json.dumps({"3": {"class_type": "KSampler", "inputs": {}}})

    assert _is_irreplaceable_generation_record("prompt", comfy) is True
    assert detect_caption_format(comfy) == "unknown"
    # A plain string that merely sits under "prompt" is not a record and loses
    # to the editor's own value — the behaviour the chunk name is what decides.
    assert _is_irreplaceable_generation_record("prompt", TAG_LIST) is False


def test_the_chunk_classifier_is_already_consolidated_with_its_own_origin() -> None:
    """It is a lazy delegate, not a fourth copy of anything.

    The body forwards to ``obfuscation._text_chunk_key_for`` and only falls back
    to ``UserComment`` if that import fails, so there is no duplicated rule set
    to merge in the first place.
    """
    from obfuscation import _text_chunk_key_for

    for text in (
        "1girl, solo\nSteps: 20, Sampler: Euler a",
        json.dumps({"3": {"class_type": "KSampler"}}),
        json.dumps({"prompt": "1girl", "uc": "bad"}),
        PROSE,
        TAG_LIST,
        "",
    ):
        assert _classify_embedded_text(text) == _text_chunk_key_for(text)
