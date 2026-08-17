"""Sidecar caption text gets its own field; ``prompt`` keeps its meaning.

Owner decision (2026-08): the text recovered from a ``.txt``/``.json`` sidecar
next to an image is overwhelmingly a Danbooru-style tag list written by a
human or a tagger — NOT the SD generation prompt that produced the image.
Writing it into ``images.prompt`` made it searchable but destroyed the meaning
of the field: Prompt Lab statistics were silently computed over other people's
tags. It now lands in ``images.sidecar_caption``.

Consequences pinned here:
* a caption-only sidecar leaves ``prompt`` empty (honest, and keeps the row
  inside the "re-parse" job's scope);
* a sidecar that really does carry generation parameters still fills
  ``prompt``;
* both fields are reachable from the gallery search box;
* the user-triggered recovery job can populate the caption for rows that were
  already indexed, without inventing a prompt for them.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

import database as db
import image_manager
from metadata_parser import parse_image
from services import metadata_repair_service as mrs


DANBOORU_CAPTION = "1girl, silver_hair, red_eyes, looking at viewer, masterpiece"

A1111_PARAMETERS = (
    "a real generation prompt\n"
    "Negative prompt: lowres\n"
    "Steps: 22, Sampler: Euler a, CFG scale: 7, Seed: 12, Size: 64x64, "
    "Model: real_model.safetensors"
)


def _blank_image(path: Path) -> Path:
    Image.new("RGB", (64, 64), color="white").save(path)
    return path


def _row_for(path: Path) -> dict:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM images WHERE path = ?", (str(path),)
        ).fetchone()
    assert row is not None, f"no indexed row for {path}"
    return dict(row)


class TestParserRoutesSidecarCaptions:
    def test_txt_sidecar_tag_list_becomes_a_caption_not_a_prompt(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "tagged.png")
        (tmp_path / "tagged.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == DANBOORU_CAPTION
        assert not (result["prompt"] or "").strip(), (
            "sidecar tag lists must never be stored as the SD generation prompt"
        )

    def test_txt_sidecar_with_generation_parameters_still_fills_prompt(
        self,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "params.png")
        (tmp_path / "params.png.txt").write_text(A1111_PARAMETERS, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["generator"] == "webui"
        assert result["prompt"] == "a real generation prompt"
        assert result["negative_prompt"] == "lowres"
        assert not (result["sidecar_caption"] or "").strip()

    def test_json_sidecar_caption_key_is_a_caption(self, tmp_path: Path):
        image_path = _blank_image(tmp_path / "jsoncap.png")
        (tmp_path / "jsoncap.json").write_text(
            json.dumps({"caption": DANBOORU_CAPTION}),
            encoding="utf-8",
        )

        result = parse_image(str(image_path))

        assert result["sidecar_caption"] == DANBOORU_CAPTION
        assert not (result["prompt"] or "").strip()

    def test_embedded_generation_prompt_is_unaffected(self, tmp_path: Path):
        image_path = tmp_path / "embedded.png"
        pnginfo = PngInfo()
        pnginfo.add_text("parameters", A1111_PARAMETERS)
        Image.new("RGB", (64, 64), color="white").save(image_path, pnginfo=pnginfo)
        (tmp_path / "embedded.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")

        result = parse_image(str(image_path))

        assert result["prompt"] == "a real generation prompt"
        assert not (result["sidecar_caption"] or "").strip(), (
            "embedded metadata already answered; the sidecar must not be claimed"
        )


class TestScanPersistsSidecarCaption:
    def test_scan_stores_the_caption_and_leaves_prompt_empty(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "scanned.png")
        (tmp_path / "scanned.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert row["sidecar_caption"] == DANBOORU_CAPTION
        assert not (row["prompt"] or "").strip()

    def test_rescan_keeps_a_previously_recovered_caption(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "rescanned.png")
        (tmp_path / "rescanned.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")

        image_manager.scan_folder(str(tmp_path), recursive=False)
        image_manager.scan_folder(str(tmp_path), recursive=False, force_reparse=True)

        row = _row_for(image_path)
        assert row["sidecar_caption"] == DANBOORU_CAPTION
        assert not (row["prompt"] or "").strip()


class TestSidecarArrivingAfterIndexingIsSeen:
    """A sidecar written after the image was indexed used to be invisible forever.

    The scan fingerprint was ``(image mtime, image size)`` only. Writing or
    editing a ``.txt`` next to an already-indexed image changes neither, so
    ``_is_unchanged_scan_hit`` reported the row as unchanged and the new caption
    text was never read - not by this scan, not by any later one. Measured on the
    owner's library: every one of the 5,242 rows whose file still exists has a
    ``.txt`` beside it, and 5,214 of those sidecars are newer than the image they
    describe.
    """

    LATER_CAPTION = "1girl, blue_hair, twintails, smiling, outdoors, highres"
    EDITED_CAPTION = "1boy, short_hair, armor, holding sword"

    def test_a_sidecar_written_after_indexing_is_read_on_the_next_scan(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "late-sidecar.png")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        assert not (_row_for(image_path)["sidecar_caption"] or "").strip()
        indexed_stat = image_path.stat()

        (tmp_path / "late-sidecar.txt").write_text(
            self.LATER_CAPTION, encoding="utf-8"
        )
        image_manager.scan_folder(str(tmp_path), recursive=False)

        # The image itself is untouched, which is exactly why the old
        # (mtime, size) fingerprint could not notice.
        assert image_path.stat().st_mtime_ns == indexed_stat.st_mtime_ns
        assert image_path.stat().st_size == indexed_stat.st_size
        row = _row_for(image_path)
        assert row["sidecar_caption"] == self.LATER_CAPTION
        assert not (row["prompt"] or "").strip(), (
            "a re-read must route sidecar text the same way as a first read (21fd5e8)"
        )

    def test_editing_a_sidecar_after_indexing_replaces_the_stored_text(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "edited-sidecar.png")
        sidecar = tmp_path / "edited-sidecar.txt"
        sidecar.write_text(DANBOORU_CAPTION, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        assert _row_for(image_path)["sidecar_caption"] == DANBOORU_CAPTION

        sidecar.write_text(self.EDITED_CAPTION, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)

        row = _row_for(image_path)
        assert row["sidecar_caption"] == self.EDITED_CAPTION
        assert not (row["prompt"] or "").strip()

    def test_deleting_a_sidecar_after_indexing_clears_the_stored_text(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "removed-sidecar.png")
        sidecar = tmp_path / "removed-sidecar.txt"
        sidecar.write_text(DANBOORU_CAPTION, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        assert _row_for(image_path)["sidecar_caption"] == DANBOORU_CAPTION

        sidecar.unlink()
        image_manager.scan_folder(str(tmp_path), recursive=False)

        assert not (_row_for(image_path)["sidecar_caption"] or "").strip()

    def test_an_untouched_sidecar_keeps_the_scan_cheap(
        self,
        test_db,
        tmp_path: Path,
    ):
        """The fix must not turn every rescan into a full re-parse."""
        image_path = _blank_image(tmp_path / "stable-sidecar.png")
        (tmp_path / "stable-sidecar.txt").write_text(
            DANBOORU_CAPTION, encoding="utf-8"
        )
        image_manager.scan_folder(str(tmp_path), recursive=False)

        result = image_manager.scan_folder(str(tmp_path), recursive=False)

        assert result["unchanged"] == 1
        assert _row_for(image_path)["sidecar_caption"] == DANBOORU_CAPTION

    def test_an_image_with_no_sidecar_at_all_stays_an_unchanged_hit(
        self,
        test_db,
        tmp_path: Path,
    ):
        _blank_image(tmp_path / "bare.png")
        image_manager.scan_folder(str(tmp_path), recursive=False)

        result = image_manager.scan_folder(str(tmp_path), recursive=False)

        assert result["unchanged"] == 1

    def test_a_legacy_row_with_no_stored_fingerprint_only_rereads_when_a_sidecar_exists(
        self,
        test_db,
        tmp_path: Path,
    ):
        """Rows indexed before this column exists must not all re-parse.

        The owner's database is at schema version 41 with 6,842 rows, so every
        row reads NULL here right after the migration. A NULL means "never
        fingerprinted", which is only worth paying for when a sidecar is
        actually present.
        """
        bare = _blank_image(tmp_path / "legacy-bare.png")
        tagged = _blank_image(tmp_path / "legacy-tagged.png")
        (tmp_path / "legacy-tagged.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")
        image_manager.scan_folder(str(tmp_path), recursive=False)
        with db.get_db() as conn:
            conn.execute("UPDATE images SET sidecar_caption = NULL, sidecar_fingerprint = NULL")

        result = image_manager.scan_folder(str(tmp_path), recursive=False)

        assert result["unchanged"] == 1, "only the row with a sidecar should re-read"
        assert not (_row_for(bare)["sidecar_caption"] or "").strip()
        assert _row_for(tagged)["sidecar_caption"] == DANBOORU_CAPTION


class TestBothFieldsAreSearchable:
    def _seed(self, *, path: str, prompt=None, caption=None) -> int:
        image_id = db.add_image(
            path=path,
            filename=Path(path).name,
            generator="unknown",
            prompt=prompt,
            width=64,
            height=64,
            file_size=1024,
            metadata_json="{}",
        )
        if caption is not None:
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE images SET sidecar_caption = ? WHERE id = ?",
                    (caption, image_id),
                )
        return image_id

    def test_search_matches_caption_text(self, test_db):
        caption_id = self._seed(
            path="/lib/caption-only.png", caption=DANBOORU_CAPTION
        )
        self._seed(path="/lib/unrelated.png", prompt="cyberpunk city, neon")

        found = {row["id"] for row in db.get_images(search_query="silver_hair")}

        assert caption_id in found

    def test_caption_search_normalizes_underscores_like_prompt_search(self, test_db):
        caption_id = self._seed(
            path="/lib/underscored.png", caption=DANBOORU_CAPTION
        )

        found = {row["id"] for row in db.get_images(search_query="silver hair")}

        assert caption_id in found

    def test_search_still_matches_prompt_text(self, test_db):
        prompt_id = self._seed(
            path="/lib/prompted.png", prompt="cyberpunk city, neon lights"
        )
        self._seed(path="/lib/other.png", caption=DANBOORU_CAPTION)

        found = {row["id"] for row in db.get_images(search_query="cyberpunk")}

        assert found == {prompt_id}


class TestUserTriggeredCaptionRecovery:
    def test_recovery_job_fills_the_caption_without_inventing_a_prompt(
        self,
        test_db,
        tmp_path: Path,
    ):
        image_path = _blank_image(tmp_path / "legacy.png")
        (tmp_path / "legacy.txt").write_text(DANBOORU_CAPTION, encoding="utf-8")
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

        assert image_id in mrs.snapshot_missing_prompt_ids()

        outcome = mrs._process_chunk([image_id])

        assert outcome["result_delta"]["captions_recovered"] == 1
        row = _row_for(image_path)
        assert row["sidecar_caption"] == DANBOORU_CAPTION
        assert not (row["prompt"] or "").strip()

    def test_metadata_health_separates_no_prompt_from_no_text_at_all(self, test_db):
        db.add_image(
            path="/lib/health-caption.png",
            filename="health-caption.png",
            generator="unknown",
            prompt=None,
            metadata_json="{}",
        )
        db.add_image(
            path="/lib/health-empty.png",
            filename="health-empty.png",
            generator="unknown",
            prompt=None,
            metadata_json="{}",
        )
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET sidecar_caption = ? WHERE path = ?",
                (DANBOORU_CAPTION, "/lib/health-caption.png"),
            )

        totals = mrs.get_metadata_health()["totals"]

        assert totals["missing_prompt"] == 2
        assert totals["missing_text"] == 1, (
            "a row that now carries a caption is no longer textless"
        )
