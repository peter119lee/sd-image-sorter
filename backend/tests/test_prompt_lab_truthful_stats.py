"""Prompt Lab statistics must describe a population the user can act on.

The owner's real library is the reason these tests exist: 6,842 rows of which
1,537 point at files that no longer exist, not one row records a checkpoint,
and the rows that do carry prompt text were never recognized as SD output
(``generator = 'others'``) because that text came from ``.txt`` tag sidecars.

So every fixture here deliberately mirrors that shape — dead rows,
``generator = 'others'``, sidecar-derived tag text, zero checkpoints. A
fixture of clean SD prompts would pass while the real library stayed broken.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest


# Danbooru-style tag text of the kind harvested from a .txt sidecar: what the
# owner's `prompt` column actually holds today, not an SD generation prompt.
SIDECAR_TAG_TEXT = "masterpiece, best quality, 1girl, solo, red hair, looking at viewer"


def _seed_row(
    db: Any,
    *,
    name: str,
    tags: tuple[str, ...] = (),
    readable: bool = True,
    prompt: str | None = None,
    sidecar_caption: str | None = None,
    checkpoint: str | None = None,
    aesthetic_score: float | None = None,
) -> int:
    image_id = int(db.add_image(
        path=f"/tmp/plab/{name}",
        filename=name,
        generator="others" if prompt else "unknown",
        prompt=prompt,
        metadata_json="{}",
        checkpoint=checkpoint,
        sidecar_caption=sidecar_caption,
        is_readable=readable,
        read_error=None if readable else "missing",
    ))
    with db.get_db() as conn:
        cursor = conn.cursor()
        if aesthetic_score is not None:
            cursor.execute(
                "UPDATE images SET aesthetic_score = ? WHERE id = ?",
                (aesthetic_score, image_id),
            )
        for tag in tags:
            cursor.execute(
                "INSERT INTO tags (image_id, tag, confidence) VALUES (?, ?, ?)",
                (image_id, tag, 0.9),
            )
    return image_id


def _stats(test_client: Any, query: str = "") -> Dict[str, Any]:
    response = test_client.get(f"/api/prompts/stats{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _user_visible_error(response: Any) -> str:
    """The message Prompt Lab actually toasts.

    Mirrors the frontend's own precedence (`formatApiError` in
    app/gallery-filter-helpers.js: detail, then error, then message) so this
    asserts what the user reads rather than which envelope key carried it.
    """
    body = response.json()
    for key in ("detail", "error", "message"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    raise AssertionError(f"no user-visible message in {body!r}")


def _tag_entry(stats: Dict[str, Any], tag: str) -> Dict[str, Any]:
    matches = [entry for entry in stats["top_tags"] if entry["tag"] == tag]
    assert matches, f"{tag!r} missing from top_tags: {stats['top_tags']}"
    return matches[0]


class TestTopTagPercentages:
    """The tag chart is the one thing Prompt Lab exists to tell the owner."""

    def test_percentage_denominator_excludes_rows_whose_file_is_gone(self, test_client, test_db):
        """A tag on every usable image reads 100%, not 4/6.

        Tagging never runs on a missing file, so a dead row can only ever sit
        in the denominator. On the real library that understates every single
        bar by ~22% in the same direction.
        """
        for index in range(4):
            _seed_row(test_db, name=f"live-{index}.png", tags=("solo",) if index else ("solo", "1girl"))
        _seed_row(test_db, name="dead-0.png", readable=False, prompt=SIDECAR_TAG_TEXT)
        _seed_row(test_db, name="dead-1.png", readable=False, prompt=SIDECAR_TAG_TEXT)

        stats = _stats(test_client)

        solo = _tag_entry(stats, "solo")
        assert solo["count"] == 4
        assert solo["pct"] == 100.0
        assert _tag_entry(stats, "1girl")["pct"] == 25.0
        assert stats["total_images"] == 6
        assert stats["usable_images"] == 4

    def test_percentage_numerator_excludes_tags_on_rows_whose_file_is_gone(self, test_client, test_db):
        """Both halves of the fraction must cover the same population."""
        for index in range(3):
            _seed_row(test_db, name=f"live-tagged-{index}.png", tags=("solo",))
        _seed_row(test_db, name="live-untagged.png")
        _seed_row(test_db, name="dead-tagged.png", readable=False, tags=("solo",))

        stats = _stats(test_client)

        solo = _tag_entry(stats, "solo")
        assert solo["count"] == 3, "a tag row on a missing file is not an image the user can act on"
        assert solo["pct"] == 75.0
        assert stats["usable_images"] == 4

    def test_percentage_names_the_population_it_divided_by(self, test_client, test_db):
        """A percentage is only honest if its label can state its scope."""
        _seed_row(test_db, name="live.png", tags=("solo",))
        _seed_row(test_db, name="dead.png", readable=False)

        stats = _stats(test_client)

        assert stats["top_tags_denominator"] == 1
        assert stats["top_tags_denominator_basis"] == "usable_images"
        assert stats["tagged_images"] == 1

    def test_distinct_tag_total_excludes_tags_only_on_missing_files(self, test_client, test_db):
        """The show-more affordance must not promise tags the chart cannot show."""
        _seed_row(test_db, name="live.png", tags=("solo",))
        _seed_row(test_db, name="dead.png", readable=False, tags=("ghost_tag",))

        stats = _stats(test_client)

        assert [entry["tag"] for entry in stats["top_tags"]] == ["solo"]
        assert stats["top_tags_total"] == 1
        assert stats["top_tags_has_more"] is False


class TestPromptLengthScope:
    """"Average prompt length" must say which text it averaged."""

    def test_average_excludes_rows_whose_file_is_gone(self, test_client, test_db):
        """Dead rows drag the owner's average from 491 down to 374."""
        _seed_row(test_db, name="live-a.png", prompt="a" * 10)
        _seed_row(test_db, name="live-b.png", prompt="b" * 20)
        _seed_row(test_db, name="dead-a.png", readable=False, prompt="c" * 100)
        _seed_row(test_db, name="dead-b.png", readable=False, prompt="d" * 100)

        stats = _stats(test_client)

        assert stats["prompt_length"]["avg"] == 15
        assert stats["prompt_length"]["max"] == 20
        assert stats["prompt_length"]["min"] == 10
        assert stats["prompt_length"]["sample"] == 2

    def test_prompt_length_declares_its_population(self, test_client, test_db):
        """The owner has zero SD-attributed prompts; the payload must say so
        rather than letting the label imply these are generation prompts."""
        _seed_row(test_db, name="sidecar-text.png", prompt=SIDECAR_TAG_TEXT)

        stats = _stats(test_client)

        assert stats["prompt_length"]["scope"] == "usable_images_with_prompt_text"
        assert stats["prompt_length"]["sample"] == 1
        assert stats["prompt_length"]["sd_attributed_sample"] == 0, (
            "generator='others' means no SD tool claimed this text"
        )

    def test_prompt_length_counts_a_recognized_generator_as_sd_attributed(self, test_client, test_db):
        """The counter must actually move when a real SD prompt exists."""
        image_id = _seed_row(test_db, name="real-sd.png", prompt="1girl, cinematic lighting")
        with test_db.get_db() as conn:
            conn.execute("UPDATE images SET generator = 'webui' WHERE id = ?", (image_id,))

        stats = _stats(test_client)

        assert stats["prompt_length"]["sample"] == 1
        assert stats["prompt_length"]["sd_attributed_sample"] == 1


class TestCaptionStatisticsStaySeparate:
    """Sidecar captions got their own column so `prompt` keeps one meaning.
    The statistics layer must not quietly merge them back together."""

    def test_caption_text_is_measured_apart_from_prompt_text(self, test_client, test_db):
        _seed_row(test_db, name="prompted.png", prompt="x" * 10)
        _seed_row(test_db, name="captioned.png", sidecar_caption="y" * 40)

        stats = _stats(test_client)

        assert stats["prompt_length"]["avg"] == 10
        assert stats["prompt_length"]["sample"] == 1
        assert stats["caption_length"]["available"] is True
        assert stats["caption_length"]["avg"] == 40
        assert stats["caption_length"]["sample"] == 1
        assert stats["caption_length"]["scope"] == "usable_images_with_sidecar_caption"

    def test_a_row_holding_both_contributes_each_to_its_own_statistic(self, test_client, test_db):
        """Proves no merging: one row, two lengths, neither averaged into the other."""
        _seed_row(test_db, name="both.png", prompt="p" * 12, sidecar_caption="c" * 60)

        stats = _stats(test_client)

        assert stats["prompt_length"]["avg"] == 12
        assert stats["prompt_length"]["sample"] == 1
        assert stats["caption_length"]["avg"] == 60
        assert stats["caption_length"]["sample"] == 1

    def test_caption_average_excludes_rows_whose_file_is_gone(self, test_client, test_db):
        _seed_row(test_db, name="live.png", sidecar_caption="a" * 10)
        _seed_row(test_db, name="dead.png", readable=False, sidecar_caption="b" * 200)

        stats = _stats(test_client)

        assert stats["caption_length"]["avg"] == 10
        assert stats["caption_length"]["sample"] == 1

    def test_stats_still_work_before_migration_042_lands(self, test_db):
        """Migration 042 is additive, so any database last opened by an older
        build reaches this code without the column. Prompt statistics must be
        correct anyway rather than raising on a missing column."""
        from services.prompt_service import PromptService

        _seed_row(test_db, name="pre-migration.png", prompt="z" * 30)
        with test_db.get_db() as conn:
            conn.execute("ALTER TABLE images DROP COLUMN sidecar_caption")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(images)").fetchall()}
        assert "sidecar_caption" not in columns, "precondition: pre-042 schema reproduced"

        stats = PromptService().get_prompt_stats(
            tag_limit=10,
            high_tag_limit=10,
            checkpoint_limit=10,
            leader_limit=10,
            recipe_limit=10,
            scored_limit=10,
        )

        assert stats["prompt_length"]["avg"] == 30
        assert stats["prompt_length"]["sample"] == 1
        assert stats["caption_length"]["available"] is False
        assert stats["caption_length"]["sample"] == 0


class TestEmptyCheckpointPanelsTellTheTruth:
    """Three of six panels are permanently empty on the owner's library and
    the message told him to import more prompt metadata — which cannot work,
    because none of his images carry SD generation parameters at all."""

    def test_no_checkpoint_anywhere_is_reported_as_such(self, test_client, test_db):
        for index in range(4):
            _seed_row(test_db, name=f"live-{index}.png", tags=("solo",), prompt=SIDECAR_TAG_TEXT)
        _seed_row(test_db, name="dead.png", readable=False, prompt=SIDECAR_TAG_TEXT)

        stats = _stats(test_client)

        assert stats["top_checkpoints"] == []
        assert stats["top_checkpoints_empty_reason"] == "no_checkpoint_metadata"
        assert stats["checkpoint_score_leaders_empty_reason"] == "no_checkpoint_metadata"
        assert stats["checkpoint_recipes_empty_reason"] == "no_checkpoint_metadata"
        coverage = stats["checkpoint_coverage"]
        assert coverage["images_with_checkpoint"] == 0
        assert coverage["images_with_checkpoint_any"] == 0
        assert coverage["usable_images"] == 4
        assert coverage["total_images"] == 5

    def test_checkpoints_only_on_missing_files_get_their_own_reason(self, test_client, test_db):
        """A different situation and a different remedy: the metadata exists,
        the files do not."""
        _seed_row(test_db, name="dead-with-cp.png", readable=False, checkpoint="ponyRealism.safetensors")
        _seed_row(test_db, name="live-no-cp.png", tags=("solo",))

        stats = _stats(test_client)

        assert stats["top_checkpoints"] == []
        assert stats["top_checkpoints_empty_reason"] == "checkpoint_metadata_only_on_missing_files"
        assert stats["checkpoint_coverage"]["images_with_checkpoint_any"] == 1
        assert stats["checkpoint_coverage"]["images_with_checkpoint"] == 0

    def test_unscored_checkpoints_blame_scoring_not_metadata(self, test_client, test_db):
        """"Best Checkpoints" needs aesthetic scores as well as checkpoints —
        and unlike checkpoints, scoring is something the user can actually do."""
        for index in range(3):
            _seed_row(test_db, name=f"cp-{index}.png", checkpoint="ponyRealism.safetensors", tags=("solo",))

        stats = _stats(test_client)

        assert stats["top_checkpoints"], "checkpoints exist, so this panel must populate"
        assert stats["top_checkpoints_empty_reason"] is None
        assert stats["checkpoint_score_leaders"] == []
        assert stats["checkpoint_score_leaders_empty_reason"] == "no_scored_images"
        assert stats["checkpoint_coverage"]["scored_usable_images"] == 0
        assert stats["checkpoint_coverage"]["min_scored_images_per_checkpoint"] == 3

    def test_too_few_scored_images_per_checkpoint_is_distinguished(self, test_client, test_db):
        _seed_row(test_db, name="scored.png", checkpoint="ponyRealism.safetensors", aesthetic_score=8.0, tags=("solo",))
        _seed_row(test_db, name="unscored.png", checkpoint="ponyRealism.safetensors", tags=("solo",))

        stats = _stats(test_client)

        assert stats["checkpoint_score_leaders"] == []
        assert stats["checkpoint_score_leaders_empty_reason"] == "not_enough_scored_images_per_checkpoint"
        assert stats["checkpoint_coverage"]["scored_usable_images"] == 1

    def test_a_populated_panel_reports_no_reason(self, test_client, test_db):
        for index in range(3):
            _seed_row(
                test_db,
                name=f"leader-{index}.png",
                checkpoint="ponyRealism.safetensors",
                aesthetic_score=8.0 + index * 0.1,
                tags=("studio_lighting",),
            )

        stats = _stats(test_client)

        assert stats["top_checkpoints_empty_reason"] is None
        assert stats["checkpoint_score_leaders_empty_reason"] is None
        assert stats["checkpoint_recipes_empty_reason"] is None


class TestCheckpointPanelsIgnoreMissingFiles:
    def test_checkpoint_counts_exclude_rows_whose_file_is_gone(self, test_client, test_db):
        """Otherwise every checkpoint count is inflated by the dead rows, the
        same way the tag percentages were."""
        for index in range(2):
            _seed_row(test_db, name=f"live-{index}.png", checkpoint="ponyRealism.safetensors")
        for index in range(3):
            _seed_row(test_db, name=f"dead-{index}.png", readable=False, checkpoint="ponyRealism.safetensors")

        stats = _stats(test_client)

        entry = next(item for item in stats["top_checkpoints"] if item["name"] == "ponyRealism")
        assert entry["count"] == 2

    def test_score_leaders_exclude_rows_whose_file_is_gone(self, test_client, test_db):
        for index in range(3):
            _seed_row(
                test_db,
                name=f"live-{index}.png",
                checkpoint="ponyRealism.safetensors",
                aesthetic_score=8.0,
                tags=("studio_lighting",),
            )
        _seed_row(
            test_db,
            name="dead.png",
            readable=False,
            checkpoint="ponyRealism.safetensors",
            aesthetic_score=2.0,
        )

        stats = _stats(test_client)

        leader = next(item for item in stats["checkpoint_score_leaders"] if item["name"] == "ponyRealism")
        assert leader["count"] == 3
        assert leader["avg_score"] == 8.0, "a missing file must not drag the average down"

    def test_recipe_tags_come_only_from_images_that_still_exist(self, test_client, test_db):
        for index in range(3):
            _seed_row(
                test_db,
                name=f"live-{index}.png",
                checkpoint="ponyRealism.safetensors",
                aesthetic_score=8.0,
                tags=("studio_lighting",),
            )
        _seed_row(
            test_db,
            name="dead.png",
            readable=False,
            checkpoint="ponyRealism.safetensors",
            aesthetic_score=9.0,
            tags=("ghost_tag",),
        )

        stats = _stats(test_client)

        recipe = next(item for item in stats["checkpoint_recipes"] if item["name"] == "ponyRealism")
        assert "studio_lighting" in recipe["tags"]
        assert "ghost_tag" not in recipe["tags"]

    def test_recipe_prompt_fallback_ignores_rows_whose_file_is_gone(self, test_client, test_db):
        """The fallback mines the prompt column when a checkpoint has no tags."""
        _seed_row(test_db, name="live.png", checkpoint="ponyRealism.safetensors", prompt="live_token, shared_token")
        _seed_row(
            test_db,
            name="dead.png",
            readable=False,
            checkpoint="ponyRealism.safetensors",
            prompt="ghost_token, shared_token",
        )

        stats = _stats(test_client)

        recipe = next(item for item in stats["checkpoint_recipes"] if item["name"] == "ponyRealism")
        # extract_prompt_tokens normalizes underscores to spaces, so compare
        # against what the real tokenizer emits rather than the seeded spelling.
        assert "live token" in recipe["tags"]
        assert "ghost token" not in recipe["tags"]


class TestCompareRefusesMissingFiles:
    """A confident-looking diff against a picture that is not there, with a
    broken thumbnail and buttons that cannot work, is worse than an error."""

    def test_compare_against_a_deleted_image_says_the_file_is_missing(self, test_client, test_db):
        kept = _seed_row(test_db, name="kept.png", prompt="1girl, solo", tags=("solo",))
        gone = _seed_row(test_db, name="gone.png", readable=False, prompt="1girl, smile", tags=())

        response = test_client.get(f"/api/prompts/compare?id_a={kept}&id_b={gone}")

        assert response.status_code == 409
        message = _user_visible_error(response)
        assert "gone.png" in message
        assert "kept.png" not in message, "only name the image that is actually missing"
        assert "prompt_common" not in response.json()

    def test_compare_names_both_images_when_both_are_gone(self, test_client, test_db):
        first = _seed_row(test_db, name="gone-a.png", readable=False, prompt="a")
        second = _seed_row(test_db, name="gone-b.png", readable=False, prompt="b")

        response = test_client.get(f"/api/prompts/compare?id_a={first}&id_b={second}")

        assert response.status_code == 409
        message = _user_visible_error(response)
        assert "gone-a.png" in message
        assert "gone-b.png" in message

    def test_compare_of_two_usable_images_still_diffs(self, test_client, test_db):
        first = _seed_row(test_db, name="a.png", prompt="1girl, solo, red hair", tags=("solo",))
        second = _seed_row(test_db, name="b.png", prompt="1girl, solo, blue hair", tags=("solo",))

        response = test_client.get(f"/api/prompts/compare?id_a={first}&id_b={second}")

        assert response.status_code == 200
        data = response.json()
        assert data["prompt_common"] == ["1girl", "solo"]
        assert data["prompt_only_a"] == ["red hair"]
        assert data["prompt_only_b"] == ["blue hair"]

    def test_compare_of_an_unknown_id_is_still_a_404(self, test_client, test_db):
        kept = _seed_row(test_db, name="kept.png", prompt="1girl")

        response = test_client.get(f"/api/prompts/compare?id_a={kept}&id_b=987654")

        assert response.status_code == 404


class TestAestheticTagPanelsIgnoreMissingFiles:
    def test_high_aesthetic_tags_exclude_rows_whose_file_is_gone(self, test_client, test_db):
        _seed_row(test_db, name="live.png", aesthetic_score=8.0, tags=("keeper",))
        _seed_row(test_db, name="dead.png", readable=False, aesthetic_score=9.0, tags=("ghost_tag",))

        stats = _stats(test_client)

        tags = [entry["tag"] for entry in stats["high_aesthetic_tags"]]
        assert tags == ["keeper"]
        assert stats["high_aesthetic_tags_total"] == 1


@pytest.mark.parametrize("field", [
    "usable_images",
    "tagged_images",
    "top_tags_denominator",
    "top_tags_denominator_basis",
    "checkpoint_coverage",
    "top_checkpoints_empty_reason",
    "checkpoint_score_leaders_empty_reason",
    "checkpoint_recipes_empty_reason",
    "caption_length",
])
def test_stats_payload_exposes_every_new_scope_field(test_client, test_db, field):
    """The frontend can only state a scope the payload actually carries."""
    _seed_row(test_db, name="live.png", tags=("solo",))

    assert field in _stats(test_client)
