"""``sidecar_caption_format`` records the format of the caption, never edits it.

The column split in this app is by **provenance**: ``prompt`` is the generator's
own text, ``ai_caption`` is this app's tagger, ``nl_caption`` is this app's VLM,
``sidecar_caption`` is a file somebody else wrote. Provenance cannot be derived
from the text, so it has to be structural.

Tag-list-versus-prose is a different axis — **format** — and it *can* be derived
from the text, so it is one small marker rather than another text column. The
name says which axis it describes: it is the format *of* ``sidecar_caption``,
not a new source of caption text.

The invariant that matters most, pinned here from several angles: **the marker
never shortens, trims, normalizes or drops the stored text.** Whatever the
marker says, the stored caption is byte-identical to the text the parser
returned, which is what a pre-marker build stored for the same file.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

import caption_format as cf
import database as db
import image_manager
from metadata_parser import parse_image
from services import metadata_repair_service as mrs


TAG_SIDECAR = (
    "masterpiece, best quality, 1girl, hinomori shizuku, solo, dress, "
    "looking at viewer, open mouth, white thighhighs, full body, blush"
)
PROSE_SIDECAR = (
    "This digital artwork features a blonde, fair-skinned anime-style woman "
    "with blue eyes and a blue hair ribbon. She is holding a glowing light."
)
MIXED_SIDECAR = "1girl, solo, blue_eyes. She is standing in a field of flowers."
UNREADABLE_SIDECAR = "-----"


def _blank_image(path: Path) -> Path:
    Image.new("RGB", (64, 64), color="white").save(path)
    return path


def _row_for(path: Path) -> dict:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM images WHERE path = ?", (str(path),)).fetchone()
    assert row is not None, f"no indexed row for {path}"
    return dict(row)


class TestParserMarksTheFormatItRead:
    def test_tag_list_sidecar_is_marked_tags(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "tagged.png")
        (tmp_path / "tagged.txt").write_text(TAG_SIDECAR, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == TAG_SIDECAR
        assert result["sidecar_caption_format"] == "tags"

    def test_prose_sidecar_is_marked_natural(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "prose.png")
        (tmp_path / "prose.txt").write_text(PROSE_SIDECAR, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == PROSE_SIDECAR
        assert result["sidecar_caption_format"] == "natural"

    def test_mixed_sidecar_is_marked_mixed(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "mixed.png")
        (tmp_path / "mixed.txt").write_text(MIXED_SIDECAR, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == MIXED_SIDECAR
        assert result["sidecar_caption_format"] == "mixed"

    def test_unrecognizable_sidecar_is_marked_unknown_and_still_stored(
        self, tmp_path: Path
    ):
        image_path = _blank_image(tmp_path / "junk.png")
        (tmp_path / "junk.txt").write_text(UNREADABLE_SIDECAR, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == UNREADABLE_SIDECAR, (
            "an unrecognized format must never cost the user the text"
        )
        assert result["sidecar_caption_format"] == "unknown"

    def test_json_caption_key_is_classified_too(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "jsoncap.png")
        (tmp_path / "jsoncap.json").write_text(
            json.dumps({"caption": TAG_SIDECAR}), encoding="utf-8"
        )

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == TAG_SIDECAR
        assert result["sidecar_caption_format"] == "tags"

    def test_no_sidecar_text_means_no_marker(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "bare.png")

        result = parse_image(str(image_path))

        assert not (result["sidecar_caption"] or "").strip()
        assert result["sidecar_caption_format"] is None, (
            "NULL means 'no sidecar text'; 'unknown' means 'text we could not read'"
        )


class TestScanStoresTheMarker:
    def test_scan_persists_format_alongside_the_caption(self, test_db, tmp_path: Path):
        tagged = _blank_image(tmp_path / "tagged.png")
        prose = _blank_image(tmp_path / "prose.png")
        mixed = _blank_image(tmp_path / "mixed.png")
        junk = _blank_image(tmp_path / "junk.png")
        bare = _blank_image(tmp_path / "bare.png")
        (tmp_path / "tagged.txt").write_text(TAG_SIDECAR, encoding="utf-8")
        (tmp_path / "prose.txt").write_text(PROSE_SIDECAR, encoding="utf-8")
        (tmp_path / "mixed.txt").write_text(MIXED_SIDECAR, encoding="utf-8")
        (tmp_path / "junk.txt").write_text(UNREADABLE_SIDECAR, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)

        assert _row_for(tagged)["sidecar_caption_format"] == "tags"
        assert _row_for(prose)["sidecar_caption_format"] == "natural"
        assert _row_for(mixed)["sidecar_caption_format"] == "mixed"
        assert _row_for(junk)["sidecar_caption_format"] == "unknown"
        assert _row_for(bare)["sidecar_caption_format"] is None

    def test_an_edited_sidecar_updates_the_marker_with_the_text(
        self, test_db, tmp_path: Path
    ):
        image_path = _blank_image(tmp_path / "edited.png")
        sidecar = tmp_path / "edited.txt"
        sidecar.write_text(TAG_SIDECAR, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        assert _row_for(image_path)["sidecar_caption_format"] == "tags"

        sidecar.write_text(PROSE_SIDECAR, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert row["sidecar_caption"] == PROSE_SIDECAR
        assert row["sidecar_caption_format"] == "natural", (
            "a stale marker would describe text that is no longer there"
        )

    def test_reparse_path_stores_the_marker(self, test_db, tmp_path: Path):
        image_path = _blank_image(tmp_path / "reparsed.png")
        (tmp_path / "reparsed.txt").write_text(PROSE_SIDECAR, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        image_id = _row_for(image_path)["id"]
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET sidecar_caption = NULL, sidecar_caption_format = NULL "
                "WHERE id = ?",
                (image_id,),
            )

        image_manager.reparse_image_metadata(image_id, str(image_path))

        row = _row_for(image_path)
        assert row["sidecar_caption"] == PROSE_SIDECAR
        assert row["sidecar_caption_format"] == "natural"

    def test_recovery_job_stores_the_marker(self, test_db, tmp_path: Path):
        image_path = _blank_image(tmp_path / "legacy.png")
        (tmp_path / "legacy.txt").write_text(TAG_SIDECAR, encoding="utf-8")
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            generator="unknown",
            prompt=None,
            width=64,
            height=64,
            file_size=image_path.stat().st_size,
            metadata_json="{}",
        )
        assert _row_for(image_path)["sidecar_caption_format"] is None

        outcome = mrs._process_chunk([image_id])

        assert outcome["result_delta"]["captions_recovered"] == 1
        row = _row_for(image_path)
        assert row["sidecar_caption"] == TAG_SIDECAR
        assert row["sidecar_caption_format"] == "tags"


class TestTheMarkerNeverCostsTheUserText:
    """Detection decides presentation only. Nothing may be dropped because of it."""

    CASES = {
        "tags": TAG_SIDECAR,
        "natural": PROSE_SIDECAR,
        "mixed": MIXED_SIDECAR,
        "unknown": UNREADABLE_SIDECAR,
    }

    def test_stored_text_is_byte_identical_to_the_file_for_every_marker(
        self, test_db, tmp_path: Path
    ):
        for expected_format, text in self.CASES.items():
            image_path = _blank_image(tmp_path / f"{expected_format}.png")
            (tmp_path / f"{expected_format}.txt").write_text(text, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)

        for expected_format, text in self.CASES.items():
            row = _row_for(tmp_path / f"{expected_format}.png")
            assert row["sidecar_caption_format"] == expected_format
            stored = row["sidecar_caption"]
            assert stored == text
            assert stored.encode("utf-8") == text.encode("utf-8"), (
                f"{expected_format}: stored bytes differ from the sidecar's bytes"
            )
            assert len(stored) == len(text)

    def test_stored_text_matches_what_a_premarker_build_stored(
        self, test_db, tmp_path: Path
    ):
        """The strongest form: the caption written with the marker is identical
        to the caption the parser produces, which is exactly what the pre-044
        write path stored. Detection is not in the text's path at all."""
        for name, text in self.CASES.items():
            image_path = _blank_image(tmp_path / f"{name}.png")
            (tmp_path / f"{name}.txt").write_text(text, encoding="utf-8")
            parsed = parse_image(str(image_path))

            image_manager.scan_folder(str(tmp_path), recursive=False)
            stored = _row_for(image_path)["sidecar_caption"]

            assert stored == parsed["sidecar_caption"], name

    def test_pathological_text_survives_with_an_unknown_marker(
        self, test_db, tmp_path: Path
    ):
        """Text nobody can classify is still stored whole, not blanked."""
        awkward = 'aGVsbG8=' * 40 + '\n{"not": "a caption"}\n' + "\u4e2d\u6587\u6ce8\u91ca"
        image_path = _blank_image(tmp_path / "awkward.png")
        (tmp_path / "awkward.txt").write_text(awkward, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert row["sidecar_caption"] == awkward.strip(), (
            "the parser's existing whole-file strip is the only transformation; "
            "the format marker adds none"
        )
        assert row["sidecar_caption_format"] in cf.CAPTION_FORMATS

    def test_a_wrong_marker_cannot_be_used_to_filter_rows_out(self, test_db):
        """Guard: the marker must not gate any read of the caption text."""
        image_id = db.add_image(
            path="/lib/marked.png",
            filename="marked.png",
            generator="unknown",
            prompt=None,
            width=64,
            height=64,
            file_size=1024,
            metadata_json="{}",
        )
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET sidecar_caption = ?, sidecar_caption_format = ? "
                "WHERE id = ?",
                (TAG_SIDECAR, "natural", image_id),
            )

        found = {row["id"] for row in db.get_images(search_query="thighhighs")}
        assert image_id in found, "search must read the text, never the marker"

        rows = db.get_images_by_ids([image_id])
        assert rows[image_id]["sidecar_caption"] == TAG_SIDECAR


class TestMarkerStaysConsistentWithStoredText:
    def test_no_write_path_can_store_a_marker_for_text_it_did_not_write(
        self, test_db, tmp_path: Path
    ):
        """Every write derives the marker from the caption it is storing, so the
        pair cannot desync even if a caller passes a stale format."""
        image_path = _blank_image(tmp_path / "consistent.png")
        (tmp_path / "consistent.txt").write_text(TAG_SIDECAR, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert row["sidecar_caption_format"] == cf.caption_format_for_storage(
            row["sidecar_caption"]
        )

    def test_a_caption_only_recovery_leaves_prompt_alone(self, test_db, tmp_path: Path):
        image_path = _blank_image(tmp_path / "captiononly.png")
        (tmp_path / "captiononly.txt").write_text(MIXED_SIDECAR, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert not (row["prompt"] or "").strip()
        assert row["sidecar_caption_format"] == "mixed"
