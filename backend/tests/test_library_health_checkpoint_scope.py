"""A missing checkpoint is only a defect for an image Stable Diffusion made.

Background
==========
``issue_counts.missing_checkpoint`` reads **5,382 of 5,382** on the owner's real
library and carries a recommendation. Measured read-only: his main library
records **zero** checkpoints anywhere because it is a LoRA-training-dataset
collection — artwork plus ``.txt`` tag sidecars — not Stable Diffusion output.
``_parsed.generation_params`` is empty on all 6,778 parsed rows and the 124 rows
with raw metadata decompress to Adobe XMP and ``CLIP STUDIO PAINT`` MakerNotes.

No action can add a checkpoint to an image Stable Diffusion never made, so a
100% issue bar with advice attached is a report that the library is entirely
broken when it is entirely normal for what it is.

``missing_prompt`` was resolved the same way one layer over (commit ``7c10fb6``):
kept as a **statistic**, moved out of ``issue_counts``, because ``issue_counts``
is the issue vocabulary that renders as bars and feeds ``actionable_count``.
The nuance here is that for a library that really *is* SD output a missing
checkpoint is worth surfacing — the same split ``no_checkpoint_metadata``
already makes in Prompt Lab, where an action is offered only when one could
help. So the actionable subset gets its own key,
``issue_counts.sd_missing_checkpoint``: readable rows a generator actually
claimed that still record no model name.

Fixtures mirror the owner's real shape: dead rows, ``generator = 'others'``,
caption-only rows via both ``prompt IS NULL`` and ``TRIM(prompt) = ''``,
``sidecar_caption_format`` NULL for pre-044 rows, and no checkpoint anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"
PROSE_CAPTION = "A woman with silver hair stands in a sunlit doorway."

# Spelled out here rather than imported, so the test states the contract
# independently of the production constants it is checking.
_READABLE_SQL = "COALESCE(is_readable, 1) = 1"
_NO_CHECKPOINT_SQL = "(checkpoint_normalized IS NULL OR TRIM(checkpoint_normalized) = '')"
_SD_ATTRIBUTED_SQL = (
    "generator IS NOT NULL AND TRIM(generator) != '' "
    "AND LOWER(TRIM(generator)) NOT IN ('unknown', 'others')"
)


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    generator: str = "unknown",
    checkpoint: Optional[str] = None,
    readable: bool = True,
    tagged: bool = True,
) -> int:
    """Insert one row with the exact text/checkpoint shape under test.

    ``tagged`` defaults to True so the checkpoint is the only defect axis in
    play; a row that is also untagged would be sampled and counted for that
    instead, and the assertions here would stop asking their question.
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
            SET prompt = ?, sidecar_caption = ?, sidecar_caption_format = NULL
            WHERE id = ?
            """,
            (prompt, caption, image_id),
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
def lora_dataset_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """The owner's shape: artwork plus sidecars, no checkpoint anywhere."""
    folder = tmp_path / "training-set"
    folder.mkdir()

    # Every readable row is 'others' on purpose. These rows are the subject of a
    # simulated repair ("the checkpoint gets filled in"), and 'others' is the only
    # unattributed label for which that is a state the parser can actually
    # produce: metadata_parser promotes 'unknown' off itself the moment a
    # checkpoint appears, so writing one against 'unknown' invents a row that
    # trips issue_counts.unattributed_sd_metadata and stops the mutation
    # isolating the checkpoint column. 'unknown' rows are exercised in
    # test_library_health_generator_attribution.py.
    rows = [
        _seed(folder, "caption-null-prompt.png", caption=DANBOORU_CAPTION, generator="others"),
        _seed(folder, "caption-blank-prompt.png", prompt="", caption=PROSE_CAPTION,
              generator="others"),
        _seed(folder, "prompt-shaped-tags.png", prompt=DANBOORU_CAPTION, generator="others"),
        _seed(folder, "plain.png", caption=PROSE_CAPTION, generator="others"),
    ]
    dead = [_seed(folder, "dead.png", caption=DANBOORU_CAPTION, readable=False)]

    return {"folder": folder, "readable": rows, "dead": dead}


@pytest.fixture
def generated_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """A library that really is SD output, mixed in with downloaded artwork."""
    folder = tmp_path / "mixed"
    folder.mkdir()

    with_checkpoint = [
        _seed(folder, "gen-ok.png", prompt="1girl, cinematic", generator="webui",
              checkpoint="ponyRealism.safetensors"),
    ]
    sd_without_checkpoint = [
        _seed(folder, "gen-no-cp-a.png", prompt="1girl, cinematic", generator="webui"),
        _seed(folder, "gen-no-cp-b.png", prompt="1boy, rain", generator="comfyui"),
    ]
    # Same missing column, different meaning: nothing generated these.
    art_without_checkpoint = [
        _seed(folder, "art-a.png", caption=DANBOORU_CAPTION, generator="others"),
        _seed(folder, "art-b.png", caption=PROSE_CAPTION),
    ]
    dead_sd = [
        _seed(folder, "gen-dead.png", prompt="1girl", generator="webui", readable=False),
    ]

    return {
        "folder": folder,
        "with_checkpoint": with_checkpoint,
        "sd_without_checkpoint": sd_without_checkpoint,
        "art_without_checkpoint": art_without_checkpoint,
        "dead_sd": dead_sd,
    }


def _report(sample_limit: int = 25) -> Dict[str, Any]:
    return db.get_library_health_report(sample_limit=sample_limit)


def _count_where(sql: str) -> int:
    with db.get_db() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM images WHERE {sql}").fetchone()[0])


def _set_checkpoint(image_ids: List[int], value: Optional[str]) -> None:
    placeholders = ",".join("?" for _ in image_ids)
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE images SET checkpoint_normalized = ? WHERE id IN ({placeholders})",
            [value, *image_ids],
        )


class TestALibraryStableDiffusionNeverTouched:
    def test_a_checkpointless_art_library_reports_no_checkpoint_issue(
        self, lora_dataset_library
    ):
        """The 100% bar: every readable row lacks a checkpoint and none of them
        is a defect, so the issue vocabulary must not carry the key at all."""
        report = _report()

        assert "missing_checkpoint" not in report["issue_counts"], (
            "issue_counts renders as bars and feeds actionable_count; a fact "
            "that is true of every image and fixable in none does not belong"
        )
        assert report["issue_counts"]["sd_missing_checkpoint"] == 0

    def test_the_composition_figure_survives_as_a_statistic(self, lora_dataset_library):
        """How much of the library carries real SD parameters is worth knowing;
        it is simply not a problem list."""
        report = _report()

        assert report["statistics"]["missing_checkpoint"] == len(
            lora_dataset_library["readable"]
        )
        assert report["statistics"]["missing_checkpoint"] == _count_where(
            f"{_READABLE_SQL} AND {_NO_CHECKPOINT_SQL}"
        )

    def test_nothing_offers_a_fix_for_a_checkpoint_that_cannot_exist(
        self, lora_dataset_library
    ):
        """A recommendation is an offer to act, and no action can add a
        checkpoint to an image Stable Diffusion never made."""
        kinds = {item["kind"] for item in _report()["recommendations"]}

        assert "missing_checkpoint" not in kinds
        assert "sd_missing_checkpoint" not in kinds

    def test_filling_in_every_checkpoint_would_change_nothing(self, lora_dataset_library):
        """Same rows, same library: only the checkpoint column differs.

        If those rows were genuinely broken, repairing all of them would have to
        show up in the two numbers the panel leads with.
        """
        before = _report()["summary"]

        _set_checkpoint(lora_dataset_library["readable"], "someModel.safetensors")
        after = _report()["summary"]

        assert after["actionable_count"] == before["actionable_count"], (
            "these rows were never work; giving them a checkpoint is not a repair"
        )
        assert after["quality_score"] == before["quality_score"]

    def test_a_checkpointless_art_row_is_not_offered_as_an_attention_sample(
        self, lora_dataset_library
    ):
        """A sample list is an invitation to act. The checkpoint is the only
        axis left in this fixture, so nothing else can pull these rows in."""
        sample_ids = {int(item["id"]) for item in _report()["issue_samples"]}

        assert sample_ids.isdisjoint(lora_dataset_library["readable"])


class TestALibraryThatReallyIsGeneratedOutput:
    def test_generated_images_without_a_model_name_are_still_reported(
        self, generated_library
    ):
        """The other half of honesty: for SD output the gap is real, and it
        weakens model-based filtering exactly as the advice says."""
        report = _report()

        assert report["issue_counts"]["sd_missing_checkpoint"] == len(
            generated_library["sd_without_checkpoint"]
        )
        assert report["issue_counts"]["sd_missing_checkpoint"] == _count_where(
            f"{_READABLE_SQL} AND {_NO_CHECKPOINT_SQL} AND {_SD_ATTRIBUTED_SQL}"
        )

    def test_the_advice_carries_the_subset_it_can_help_not_the_library_total(
        self, generated_library
    ):
        """The number beside the action must be the number the action targets."""
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}

        targeted = len(generated_library["sd_without_checkpoint"])
        assert by_kind["sd_missing_checkpoint"]["count"] == targeted
        assert report["statistics"]["missing_checkpoint"] > targeted, (
            "fixture must exercise the gap between the statistic and the subset"
        )
        assert "missing_checkpoint" not in by_kind

    def test_a_dead_generated_row_is_not_counted_as_recoverable(self, generated_library):
        """Nothing can re-read a model name out of a file that is gone."""
        report = _report()

        assert generated_library["dead_sd"], "fixture must contain a dead SD row"
        assert report["issue_counts"]["sd_missing_checkpoint"] == len(
            generated_library["sd_without_checkpoint"]
        )

    def test_losing_a_model_name_on_generated_output_is_what_adds_work(
        self, generated_library
    ):
        """Asserted on the summary the user reads, so it fails on the number."""
        before = _report()["summary"]

        _set_checkpoint(generated_library["with_checkpoint"], None)
        after = _report()["summary"]

        assert after["actionable_count"] == before["actionable_count"] + len(
            generated_library["with_checkpoint"]
        )
        assert after["quality_score"] < before["quality_score"]

    def test_only_the_generated_rows_are_offered_as_attention_samples(
        self, generated_library
    ):
        sample_ids = {int(item["id"]) for item in _report()["issue_samples"]}

        assert set(generated_library["sd_without_checkpoint"]) <= sample_ids
        assert sample_ids.isdisjoint(generated_library["art_without_checkpoint"])


class TestEndpoint:
    def test_the_endpoint_serves_the_split(self, test_client, generated_library):
        from services.sorting_service import invalidate_library_health_cache

        invalidate_library_health_cache()
        response = test_client.get("/api/library-health?sample_limit=25")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["issue_counts"]["sd_missing_checkpoint"] == len(
            generated_library["sd_without_checkpoint"]
        )
        assert "missing_checkpoint" not in payload["issue_counts"]
        assert payload["statistics"]["missing_checkpoint"] == _count_where(
            f"{_READABLE_SQL} AND {_NO_CHECKPOINT_SQL}"
        )
        invalidate_library_health_cache()
