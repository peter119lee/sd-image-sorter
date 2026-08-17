"""A local dataset item's ``.txt`` is rendered by its FORMAT, not comma-split blindly.

A Dataset Maker item scanned from a folder has no ``images`` row, so its caption
is read fresh off disk and turned into rows for the export template. Splitting a
prose sidecar on its commas manufactures pseudo-tags out of real sentences, and
the tag pipeline then truncates, reorders and re-joins them — so ``max_tags`` cut
``"A girl in a red dress, standing in a field, looking at the viewer."`` down to
``"A girl in a red dress"`` and a two-line caption came out with a comma where
its line break was.

The hard rule inherited from the format-marker slice: **a format label may never
discard, truncate or refuse text.** Detection is good (99.96% agreement with an
independent oracle over the owner's 5,242 real sidecars) but not perfect, so when
it is wrong nothing may be lost. Every case below therefore asserts the caption
text itself is still present in full.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from PIL import Image

from services.dataset_export.captions import _render_dataset_sidecar
from services.dataset_export.models import DatasetExportRequest


TAGS_CAPTION = "1girl, solo, long_hair, looking at viewer, hand on own hip"
NATURAL_CAPTION = "A girl in a red dress, standing in a field, looking at the viewer."
MIXED_CAPTION = (
    "1girl, solo, long_hair. She is standing in a field of flowers and "
    "smiling at the camera."
)
# Real Danbooru tags the classifier declines to place: it finds no word at all in
# ``?, !?, ^_^``, so the honest answer is ``unknown`` rather than a guess.
UNKNOWN_CAPTION = "?, !?, ^_^"
MULTILINE_NATURAL_CAPTION = "A girl stands in the rain.\nShe is holding a red umbrella."
ONE_TAG_PER_LINE_CAPTION = "1girl\nsolo\nlong_hair"

_UNSET = object()


def _local_item(
    tmp_path: Path,
    caption: str,
    *,
    stored_format: Any = _UNSET,
) -> Dict[str, Any]:
    """Build a Dataset Maker session record (``id`` 0) with a caption on disk."""
    image = tmp_path / "local-item.png"
    Image.new("RGB", (8, 8), color=(20, 30, 40)).save(image)
    image.with_suffix(".txt").write_text(caption, encoding="utf-8", newline="")
    record: Dict[str, Any] = {
        "id": 0,
        "path": str(image),
        "filename": image.name,
        "sidecar_caption": caption,
    }
    if stored_format is not _UNSET:
        record["sidecar_caption_format"] = stored_format
    return record


def _render(
    record: Dict[str, Any],
    *,
    template_options: Optional[Dict[str, Any]] = None,
) -> str:
    request = DatasetExportRequest(
        naming_pattern="{filename}",
        image_op="copy",
        overwrite_policy="unique",
        content_mode="template",
        # Off so the caption text is byte-comparable; the LoRA underscore
        # convention has its own coverage and is not what these cases are about.
        normalize_tag_underscores=False,
        **({"template_options": template_options} if template_options else {}),
    )
    return _render_dataset_sidecar(
        record,
        [],
        request,
        blacklist_set=set(),
        image_overrides_int={},
        image_overrides_path={},
    )


@pytest.mark.parametrize(
    "state,caption,stored_format",
    [
        ("tags", TAGS_CAPTION, "tags"),
        ("natural", NATURAL_CAPTION, "natural"),
        ("mixed", MIXED_CAPTION, "mixed"),
        ("unknown", UNKNOWN_CAPTION, "unknown"),
        # NULL: a row written before migration 044 carries no marker beside its
        # text. The format has to come from the caption either way.
        ("null_marker", NATURAL_CAPTION, None),
    ],
)
def test_every_format_state_renders_its_caption_in_full(
    tmp_path: Path, state: str, caption: str, stored_format: Optional[str]
) -> None:
    rendered = _render(_local_item(tmp_path, caption, stored_format=stored_format))

    assert caption in rendered, (
        f"the {state} caption lost text on the way to the sidecar: {rendered!r}"
    )
    # The default project template is "{trigger}, {tags:filtered}, {append}" with
    # neither a trigger nor common tags, so a correct render is the caption itself.
    assert rendered == caption


def test_an_empty_sidecar_renders_nothing(tmp_path: Path) -> None:
    assert _render(_local_item(tmp_path, "", stored_format=None)) == ""


class TestATagLimitCannotTruncateProse:
    """``max_tags`` counts tags. Prose has none, so it must not be counted."""

    @pytest.mark.parametrize(
        "state,caption",
        [("natural", NATURAL_CAPTION), ("mixed", MIXED_CAPTION)],
    )
    def test_a_tag_limit_keeps_a_prose_or_hybrid_caption_whole(
        self, tmp_path: Path, state: str, caption: str
    ) -> None:
        rendered = _render(
            _local_item(tmp_path, caption),
            template_options={"max_tags": 1},
        )

        assert rendered == caption, (
            f"a tag limit truncated the {state} caption to {rendered!r}; a prose "
            "sidecar is one caption, not a list of tags to cut down"
        )

    def test_a_numeric_tag_slot_keeps_a_prose_caption_whole(
        self, tmp_path: Path
    ) -> None:
        rendered = _render(
            _local_item(tmp_path, NATURAL_CAPTION),
            template_options={"template_override": "{tags:1}"},
        )

        assert rendered == NATURAL_CAPTION

    def test_a_tag_limit_still_limits_a_real_tag_list(self, tmp_path: Path) -> None:
        """The asymmetry is deliberate: limiting a tag list is what the user asked for."""
        assert (
            _render(
                _local_item(tmp_path, TAGS_CAPTION),
                template_options={"max_tags": 1},
            )
            == "1girl"
        )
        # ``unknown`` keeps the tag-list treatment on purpose. The classifier
        # declined to place the text, and acting on a non-verdict would be a
        # guess — the same reason ``unknown`` raises no dialect advisory.
        assert (
            _render(
                _local_item(tmp_path, UNKNOWN_CAPTION),
                template_options={"max_tags": 1},
            )
            == "?"
        )


class TestLineBreaksMeanDifferentThingsInTagsAndProse:
    def test_a_multi_line_prose_sidecar_keeps_its_line_breaks(
        self, tmp_path: Path
    ) -> None:
        rendered = _render(_local_item(tmp_path, MULTILINE_NATURAL_CAPTION))

        assert rendered == MULTILINE_NATURAL_CAPTION, (
            "a prose line break was rewritten as a tag separator; the exported "
            f"caption no longer says what the user's file says: {rendered!r}"
        )

    def test_a_tag_list_written_one_per_line_still_becomes_separate_tags(
        self, tmp_path: Path
    ) -> None:
        rendered = _render(_local_item(tmp_path, ONE_TAG_PER_LINE_CAPTION))

        assert rendered == "1girl, solo, long_hair"


def test_the_format_comes_from_the_caption_on_disk_not_a_stored_marker(
    tmp_path: Path,
) -> None:
    """A marker derived at scan time can be stale; the text being written cannot.

    The session marker is derived from the ``.txt`` when the folder is scanned, so
    editing the file afterwards leaves it describing text that no longer exists.
    Deriving from the exact caption this export is about to write is what keeps
    the pair from desyncing — and is why a NULL, pre-migration-044 marker cannot
    make the render wrong.
    """
    record = _local_item(tmp_path, NATURAL_CAPTION, stored_format="tags")

    rendered = _render(record, template_options={"max_tags": 1})

    assert rendered == NATURAL_CAPTION
