"""Library health must not report caption-only rows as problems to fix.

Background
==========
Migration 042 gave sidecar-derived text its own column, ``images.sidecar_caption``,
so ``prompt`` keeps meaning "the SD generation prompt". Measured read-only on the
owner's real library (schema_version 44, 6,919 rows): **962** readable rows hold
their only text in ``prompt`` today, every one of them has a ``.txt`` beside the
image, and **zero** rows in the whole library carry a checkpoint. His next rescan
relocates all 962 into ``sidecar_caption``, which pushes
``issue_counts.missing_prompt`` from 4,420 to 5,382 — every readable image in the
library — while the "Recover Missing Text" run can only ever change 4,420 of
them. The other 962 have no SD generation parameters to recover, so the panel
would report 962 permanent problems and offer a repair that provably cannot
clear them.

``services/metadata_repair_service`` already owns the distinction:
``missing_text`` means "neither an SD prompt nor a sidecar caption", and that is
the counter the recovery job actually drives down. These tests hold the facets to
the same vocabulary, and hold the reported number to what the repair can change.

Fixtures mirror the real shape on purpose: dead rows
(``COALESCE(is_readable, 1) = 0``), ``generator = 'others'``, caption-only rows
reached both through ``prompt IS NULL`` and ``TRIM(prompt) = ''``, pre-044 rows
whose ``sidecar_caption_format`` is NULL, and no checkpoint anywhere. A fixture of
clean SD prompts would pass while his library stayed wrong.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db
from services import metadata_repair_service as mrs

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"
PROSE_CAPTION = "A woman with silver hair stands in a sunlit doorway."

# Spelled out here rather than imported so the test states the contract
# independently of the production constant it is checking.
_TEXTLESS_READABLE_SQL = (
    "(prompt IS NULL OR TRIM(prompt) = '') "
    "AND (sidecar_caption IS NULL OR TRIM(sidecar_caption) = '') "
    "AND COALESCE(is_readable, 1) = 1"
)


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    caption_format: Optional[str] = None,
    generator: str = "unknown",
    readable: bool = True,
    tagged: bool = False,
    checkpoint: Optional[str] = None,
) -> int:
    """Insert one row, then force the exact text/format shape under test.

    ``add_image`` derives ``sidecar_caption_format`` from the caption, so the
    format is overwritten afterwards: NULL reproduces every row indexed before
    migration 044, which is most of the owner's library.
    """
    image_id = int(
        db.add_image(
            path=str(folder / name),
            filename=name,
            generator=generator,
            prompt=prompt,
            checkpoint=checkpoint,
            width=64,
            height=64,
            file_size=1024,
            metadata_json="{}",
        )
    )
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE images
            SET prompt = ?, sidecar_caption = ?, sidecar_caption_format = ?
            WHERE id = ?
            """,
            (prompt, caption, caption_format, image_id),
        )
        if tagged:
            conn.execute(
                "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
                (image_id,),
            )
    if not readable:
        db.mark_image_unreadable(image_id, "original file is gone")
    return image_id


@pytest.fixture
def owner_shaped_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """A library shaped like the owner's the moment after his rescan."""
    folder = tmp_path / "library"
    folder.mkdir()

    caption_only = [
        # prompt IS NULL + a booru-style sidecar, format never derived (pre-044).
        _seed(folder, "caption-null-prompt.png", caption=DANBOORU_CAPTION,
              generator="others"),
        # TRIM(prompt) = '' + a prose sidecar: the same row shape, other spelling.
        _seed(folder, "caption-blank-prompt.png", prompt="", caption=PROSE_CAPTION,
              generator="unknown"),
    ]
    textless = [
        _seed(folder, "textless-a.png", generator="unknown"),
        _seed(folder, "textless-b.png", prompt="   ", generator="others"),
        _seed(folder, "textless-c.png", generator="unknown"),
    ]
    # Dead rows are the scanner's problem, not the parser's, and every other
    # counter in the report is guarded on COALESCE(is_readable, 1) = 1.
    dead = [_seed(folder, "dead-textless.png", generator="unknown", readable=False)]
    prompted = [
        _seed(folder, "real-prompt.png", prompt="masterpiece, 1girl, cyberpunk city",
              generator="others"),
    ]

    return {
        "folder": folder,
        "caption_only": caption_only,
        "textless": textless,
        "dead": dead,
        "prompted": prompted,
    }


def _report(sample_limit: int = 8) -> Dict[str, Any]:
    return db.get_library_health_report(sample_limit=sample_limit)


def _ids_where(sql: str) -> List[int]:
    with db.get_db() as conn:
        return [int(row["id"]) for row in conn.execute(f"SELECT id FROM images WHERE {sql}")]


class TestCaptionOnlyRowsAreNotProblems:
    def test_text_shortfall_counts_only_rows_with_no_text_at_all(
        self, owner_shaped_library
    ):
        """The number the panel shows must exclude rows that do have text."""
        report = _report()

        assert report["issue_counts"]["missing_text"] == len(
            owner_shaped_library["textless"]
        ), (
            "a row whose only text is a sidecar caption still has text; counting "
            "it here is the false problem report this slice removes"
        )

    def test_no_prompt_survives_as_a_statistic_outside_the_issue_list(
        self, owner_shaped_library
    ):
        """How many images carry real SD parameters is worth knowing — but it is
        not a defect, so it must not sit in the issue vocabulary."""
        report = _report()

        expected = len(
            owner_shaped_library["caption_only"] + owner_shaped_library["textless"]
        )
        assert report["statistics"]["missing_prompt"] == expected
        assert "missing_prompt" not in report["issue_counts"]

    def test_reported_shortfall_equals_what_the_recovery_job_can_still_change(
        self, owner_shaped_library
    ):
        """The count shown and the count a repair can move must be the same number.

        The job deliberately walks every promptless row (a parser upgrade may yet
        find a prompt in one), so its snapshot is a superset. What it can still
        *change* is the textless rows — and that is what the panel must report.
        """
        snapshot = set(mrs.snapshot_missing_prompt_ids())
        reducible = set(_ids_where(_TEXTLESS_READABLE_SQL))

        assert reducible < snapshot, "fixture must exercise the superset gap"
        assert snapshot == set(
            owner_shaped_library["caption_only"] + owner_shaped_library["textless"]
        )
        assert reducible == set(owner_shaped_library["textless"])

        report = _report()
        assert report["issue_counts"]["missing_text"] == len(reducible)
        assert report["statistics"]["missing_prompt"] == len(snapshot)

    def test_recovery_is_recommended_for_the_textless_rows_only(
        self, owner_shaped_library
    ):
        """A recommendation is an offer to act; it may not name a dead end."""
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}

        assert "missing_prompt" not in by_kind, (
            "nothing may offer a repair for images that were never generated by "
            "Stable Diffusion"
        )
        assert by_kind["missing_text"]["count"] == len(
            owner_shaped_library["textless"]
        )

    def test_losing_the_caption_text_is_what_makes_those_rows_problems(
        self, owner_shaped_library
    ):
        """Same rows, same library: only the caption column differs."""
        before = _report()
        caption_only = owner_shaped_library["caption_only"]

        placeholders = ",".join("?" for _ in caption_only)
        with db.get_db() as conn:
            conn.execute(
                f"UPDATE images SET sidecar_caption = NULL WHERE id IN ({placeholders})",
                caption_only,
            )
        after = _report()

        # Asserted on the summary the user actually reads, and before any new
        # key, so this fails on the number rather than on the payload shape.
        assert after["summary"]["actionable_count"] == before["summary"][
            "actionable_count"
        ] + len(caption_only), (
            "while the caption was there those rows needed no attention; losing "
            "it is what turns them into work"
        )
        assert after["summary"]["quality_score"] < before["summary"]["quality_score"], (
            "genuinely textless images must still cost the score; only the "
            "caption-bearing rows stopped counting"
        )
        assert after["issue_counts"]["missing_text"] == before["issue_counts"][
            "missing_text"
        ] + len(caption_only)

    def test_dead_rows_are_not_counted_as_a_recoverable_text_shortfall(
        self, owner_shaped_library
    ):
        """The recovery job skips unreadable rows, so the panel must too."""
        report = _report()

        assert set(mrs.snapshot_missing_prompt_ids()).isdisjoint(
            owner_shaped_library["dead"]
        )
        assert report["issue_counts"]["unreadable"] == len(owner_shaped_library["dead"])
        assert report["issue_counts"]["missing_text"] == len(
            owner_shaped_library["textless"]
        )


class TestIssueSamples:
    def test_a_caption_only_row_is_not_offered_as_an_attention_sample(
        self, test_db, tmp_path: Path
    ):
        """A sample list is an invitation to act. Text is the only defect axis
        here, so nothing else can pull the caption-only row into the list."""
        folder = tmp_path / "sampled"
        folder.mkdir()
        caption_only = _seed(
            folder, "has-caption.png", caption=DANBOORU_CAPTION,
            generator="others", tagged=True, checkpoint="model.safetensors",
        )
        textless = _seed(
            folder, "has-nothing.png", generator="others", tagged=True,
            checkpoint="model.safetensors",
        )

        sample_ids = [int(item["id"]) for item in _report()["issue_samples"]]

        assert textless in sample_ids
        assert caption_only not in sample_ids

    def test_samples_carry_the_caption_so_a_reason_can_be_told_truthfully(
        self, owner_shaped_library
    ):
        """Rows listed for another reason must still be describable.

        No image in the owner's library carries a checkpoint, so every row is
        sampled on that axis whatever its text. A consumer reading only ``prompt``
        can then say nothing but "missing prompt" about a row that does carry
        text — which is the same false report in a third shape.
        """
        samples = {
            int(item["id"]): item for item in _report(sample_limit=25)["issue_samples"]
        }

        caption_only = owner_shaped_library["caption_only"]
        assert set(caption_only) <= set(samples), (
            "fixture must exercise a caption-bearing row sampled for another reason"
        )
        for image_id in caption_only:
            assert str(samples[image_id]["sidecar_caption"] or "").strip()
        for image_id in owner_shaped_library["textless"]:
            assert not str(samples[image_id]["sidecar_caption"] or "").strip()


class TestFolderRows:
    def test_folder_rows_report_the_text_shortfall_beside_the_prompt_statistic(
        self, owner_shaped_library
    ):
        rows = [
            row for row in _report()["top_folders"]
            if int(row["count"] or 0) == 7
        ]

        assert len(rows) == 1, "all seven fixture rows share one folder"
        folder_row = rows[0]
        assert folder_row["missing_text"] == len(owner_shaped_library["textless"])
        assert folder_row["missing_prompt"] == len(
            owner_shaped_library["caption_only"] + owner_shaped_library["textless"]
        )


class TestScanScopeCoverage:
    def test_scope_coverage_separates_no_prompt_from_no_text(
        self, owner_shaped_library
    ):
        """The scan-completion shortfall reads this, and its docstring promises it
        cannot disagree with the repair job."""
        counts = db.count_prompt_coverage_in_folder_scope(
            str(owner_shaped_library["folder"]), True
        )

        assert counts["total"] == 6, "readable rows in scope"
        assert counts["missing_prompt"] == len(
            owner_shaped_library["caption_only"] + owner_shaped_library["textless"]
        )
        assert counts["missing_text"] == len(owner_shaped_library["textless"])


class TestLibraryHealthEndpoint:
    def test_endpoint_serves_the_text_shortfall(
        self, test_client, owner_shaped_library
    ):
        from services.sorting_service import invalidate_library_health_cache

        invalidate_library_health_cache()
        response = test_client.get("/api/library-health?sample_limit=8")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["issue_counts"]["missing_text"] == len(
            owner_shaped_library["textless"]
        )
        assert payload["statistics"]["missing_prompt"] == len(
            owner_shaped_library["caption_only"] + owner_shaped_library["textless"]
        )
        invalidate_library_health_cache()
