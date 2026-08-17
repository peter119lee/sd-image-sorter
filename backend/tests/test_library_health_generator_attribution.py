"""A generator the parser could not name is a fact, not a fault.

Background
==========
``issue_counts.unknown_generator`` reads **4,420** on the owner's real library,
carries a **0.6 quality penalty**, and has **no recommendation at all** — the
panel subtracts from his score for a condition it never tells him how to fix.

What the key actually means, read out of the parser rather than inferred:
``metadata_parser/__init__.py`` starts every parse at ``generator = "unknown"``
and promotes it to ``"others"`` the moment *any* SD field turns up (prompt,
negative prompt, checkpoint or LoRA list). So ``"unknown"`` survives only when
nothing in the file named a tool **and** no generation data was found — i.e.
nothing generated the image. Measured on his snapshot: ``generator = 'unknown'``
is exactly the 4,420 rows with no text at all, and ``'others'`` is exactly the
962 rows that do carry text. Two spellings of "no SD tool claimed this", and the
panel penalised one of them.

That makes the whole-library count a composition statistic — the same
resolution ``missing_prompt`` (``7c10fb6``) and ``missing_checkpoint``
(``5332c02``) reached. The actionable subset hiding inside it is the parser's own
invariant turned around: a row that records SD generation data yet has **no**
generator is a row the current parser could not have written, so its attribution
is stale and re-parsing it re-derives one. That subset is
``issue_counts.unattributed_sd_metadata``, and it reads 0 on his library.

``missing_negative_prompt`` (5,382 of 5,382 on his library, zero rows with one)
and ``missing_file_size`` are covered here too: nothing renders either today,
which is exactly why they had to be decided before someone iterates
``issue_counts`` in the UI and draws two more permanent bars.

Fixtures mirror his real shape: dead rows, ``generator = 'others'`` and
``'unknown'``, caption-only rows via both ``prompt IS NULL`` and
``TRIM(prompt) = ''``, ``sidecar_caption_format`` NULL for pre-044 rows, and no
checkpoint anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"
PROSE_CAPTION = "A woman with silver hair stands in a sunlit doorway."

# Spelled out here rather than imported, so each test states the contract
# independently of the production constants it is checking.
_READABLE_SQL = "COALESCE(is_readable, 1) = 1"
_NO_GENERATOR_SQL = "LOWER(COALESCE(generator, '')) IN ('', 'unknown')"
_HAS_SD_DATA_SQL = (
    "((prompt IS NOT NULL AND TRIM(prompt) != '') "
    "OR (negative_prompt IS NOT NULL AND TRIM(negative_prompt) != '') "
    "OR (checkpoint_normalized IS NOT NULL AND TRIM(checkpoint_normalized) != '') "
    "OR (loras IS NOT NULL AND TRIM(loras) NOT IN ('', '[]')))"
)
_INCOMPLETE_RECORD_SQL = (
    "((width IS NULL OR height IS NULL OR width <= 0 OR height <= 0) "
    "OR (file_size IS NULL OR file_size <= 0))"
)


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    generator: str = "unknown",
    checkpoint: Optional[str] = None,
    loras: Optional[List[str]] = None,
    readable: bool = True,
    tagged: bool = True,
    dimensions: bool = True,
    file_size: Optional[int] = 1024,
) -> int:
    """Insert one row with the exact shape under test.

    ``tagged`` and ``dimensions`` default to "fine" so the axis a test is asking
    about is the only one in play; a row that is also untagged would be counted
    and sampled for that instead, and the assertion would stop asking its
    question.
    """
    image_id = int(
        db.add_image(
            path=str(folder / name),
            filename=name,
            generator=generator,
            prompt=prompt,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            loras=loras or [],
            width=64 if dimensions else None,
            height=64 if dimensions else None,
            file_size=file_size,
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
        if not dimensions:
            conn.execute(
                "UPDATE images SET width = NULL, height = NULL WHERE id = ?", (image_id,)
            )
        if file_size is None:
            conn.execute("UPDATE images SET file_size = NULL WHERE id = ?", (image_id,))
        if tagged:
            conn.execute(
                "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?", (image_id,)
            )
    if not readable:
        db.mark_image_unreadable(image_id, "original file is gone")
    return image_id


def _report(sample_limit: int = 25) -> Dict[str, Any]:
    return db.get_library_health_report(sample_limit=sample_limit)


def _count_where(sql: str) -> int:
    with db.get_db() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM images WHERE {sql}").fetchone()[0])


def _set_generator(image_ids: List[int], value: str) -> None:
    placeholders = ",".join("?" for _ in image_ids)
    with db.get_db() as conn:
        conn.execute(
            f"UPDATE images SET generator = ? WHERE id IN ({placeholders})",
            [value, *image_ids],
        )


@pytest.fixture
def art_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """The owner's shape: artwork plus sidecars, nothing generated, no checkpoint.

    Every readable row carries caption text, so ``missing_text`` is zero and the
    generator label is the only axis these tests can be measuring.
    """
    folder = tmp_path / "training-set"
    folder.mkdir()

    unknown = [
        _seed(folder, "caption-null-prompt.png", caption=DANBOORU_CAPTION),
        _seed(folder, "caption-blank-prompt.png", prompt="", caption=PROSE_CAPTION),
        _seed(folder, "plain.png", caption=DANBOORU_CAPTION),
    ]
    # 'others' means the parser found text it could not attribute — the same
    # "nothing claimed this" verdict, spelled differently. On his library a
    # rescan moves rows from this bucket into the one above.
    others = [
        _seed(folder, "prompt-shaped-tags.png", prompt=DANBOORU_CAPTION, generator="others"),
    ]
    dead = [_seed(folder, "dead.png", caption=DANBOORU_CAPTION, readable=False)]

    return {"folder": folder, "unknown": unknown, "others": others, "dead": dead}


class TestALibraryNothingGenerated:
    def test_the_same_library_is_not_scored_differently_by_two_words_for_nothing(
        self, art_library
    ):
        """The decisive one, and the one that is about to bite him.

        'unknown' and 'others' are both "no SD tool claimed this image"; which
        one a row carries depends only on whether the parser happened to find
        text to look at. Nothing about image quality differs, so nothing about
        the quality score may.
        """
        before = _report()["summary"]

        _set_generator(art_library["unknown"], "others")
        after = _report()["summary"]

        assert after["quality_score"] == before["quality_score"], (
            "relabelling 'unknown' as 'others' changed the score, so the panel "
            "is penalising the parser's choice of word for 'nothing made this'"
        )
        assert after["actionable_count"] == before["actionable_count"]

    def test_an_unnamed_generator_is_not_in_the_issue_vocabulary(self, art_library):
        report = _report()

        assert "unknown_generator" not in report["issue_counts"], (
            "issue_counts renders as bars, feeds actionable_count and carries "
            "quality weights; a fact true of every image and fixable in none "
            "does not belong in it"
        )

    def test_the_composition_figure_survives_as_a_statistic(self, art_library):
        """Worth knowing how much of the library carries SD provenance; it is
        simply not a problem list."""
        report = _report()

        assert report["statistics"]["unknown_generator"] == len(art_library["unknown"])
        assert report["statistics"]["unknown_generator"] == _count_where(
            f"{_READABLE_SQL} AND generator = 'unknown'"
        )

    def test_nothing_offers_a_fix_for_a_generator_that_never_existed(self, art_library):
        kinds = {item["kind"] for item in _report()["recommendations"]}

        assert "unknown_generator" not in kinds
        assert "unattributed_sd_metadata" not in kinds

    def test_no_art_row_is_offered_as_an_attention_sample(self, art_library):
        """A sample list is an invitation to act; these rows are complete."""
        sample_ids = {int(item["id"]) for item in _report()["issue_samples"]}

        assert sample_ids.isdisjoint(art_library["unknown"])
        assert sample_ids.isdisjoint(art_library["others"])


class TestALibraryWhoseAttributionIsGenuinelyStale:
    """The subset hiding inside ``unknown_generator``.

    ``metadata_parser/__init__.py:1041`` promotes ``unknown`` to ``others`` as
    soon as a prompt, negative prompt, checkpoint or LoRA list is present, so the
    current parser cannot produce a row that has SD data and no generator. A
    stored row in that state came from an older parser, is missing from the
    gallery's generator tabs and from every SD-attributed query, and re-parsing
    it re-derives an attribution. That is a real gap with a real remedy.
    """

    @pytest.fixture
    def mixed_library(self, test_db, tmp_path: Path) -> Dict[str, Any]:
        folder = tmp_path / "mixed"
        folder.mkdir()

        attributed = [
            _seed(folder, "gen-ok.png", prompt="1girl, cinematic", generator="webui",
                  checkpoint="ponyRealism.safetensors"),
        ]
        # Legacy rows: SD data stored against no generator at all.
        stale = [
            _seed(folder, "legacy-prompt.png", prompt="1girl, cinematic", generator="unknown",
                  checkpoint="ponyRealism.safetensors"),
            _seed(folder, "legacy-blank-generator.png", prompt="1boy, rain", generator="",
                  checkpoint="ponyRealism.safetensors"),
        ]
        # The near-miss that must NOT be counted: 'others' is the parser saying
        # "I found text and no detector claimed it", which is a complete verdict.
        others_with_text = [
            _seed(folder, "others-prompt.png", prompt=DANBOORU_CAPTION, generator="others"),
        ]
        # No prompt, no checkpoint, no generator: nothing made it, nothing is
        # missing. This is the row the whole-library count used to charge for.
        art = [_seed(folder, "art.png", caption=PROSE_CAPTION, generator="unknown")]

        return {
            "folder": folder,
            "attributed": attributed,
            "stale": stale,
            "others_with_text": others_with_text,
            "art": art,
        }

    def test_sd_data_stored_against_no_generator_is_reported(self, mixed_library):
        report = _report()

        assert report["issue_counts"]["unattributed_sd_metadata"] == len(
            mixed_library["stale"]
        )
        assert report["issue_counts"]["unattributed_sd_metadata"] == _count_where(
            f"{_READABLE_SQL} AND ({_NO_GENERATOR_SQL}) AND {_HAS_SD_DATA_SQL}"
        )

    def test_a_complete_others_verdict_is_not_a_gap(self, mixed_library):
        """'others' already answers the question. Counting it would recreate the
        100% bar one name over."""
        report = _report()

        assert mixed_library["others_with_text"], "fixture must exercise the near-miss"
        assert report["issue_counts"]["unattributed_sd_metadata"] == len(
            mixed_library["stale"]
        )

    def test_art_with_no_sd_data_is_not_a_gap(self, mixed_library):
        assert mixed_library["art"], "fixture must contain a plain art row"
        assert _report()["issue_counts"]["unattributed_sd_metadata"] == len(
            mixed_library["stale"]
        )

    def test_the_advice_carries_the_rows_it_can_help(self, mixed_library):
        by_kind = {item["kind"]: item for item in _report()["recommendations"]}

        assert by_kind["unattributed_sd_metadata"]["count"] == len(mixed_library["stale"])

    def test_re_deriving_the_attribution_is_what_clears_the_work(self, mixed_library):
        """Asserted on the summary the user reads, so it fails on the number."""
        before = _report()["summary"]

        _set_generator(mixed_library["stale"], "webui")
        after = _report()["summary"]

        assert _report()["issue_counts"]["unattributed_sd_metadata"] == 0
        assert after["actionable_count"] == before["actionable_count"] - len(
            mixed_library["stale"]
        )
        assert after["quality_score"] > before["quality_score"]

    def test_stale_rows_are_offered_as_attention_samples(self, mixed_library):
        sample_ids = {int(item["id"]) for item in _report()["issue_samples"]}

        assert set(mixed_library["stale"]) <= sample_ids
        assert sample_ids.isdisjoint(mixed_library["others_with_text"])
        assert sample_ids.isdisjoint(mixed_library["attributed"])


class TestAnAbsentNegativePrompt:
    @pytest.fixture
    def clean_sd_library(self, test_db, tmp_path: Path) -> Dict[str, Any]:
        """A library with nothing wrong with it — and no negative prompts.

        Plenty of real WebUI and NovelAI renders carry none, and an image
        nothing generated has none by definition, so no action can add one.
        """
        folder = tmp_path / "renders"
        folder.mkdir()
        rows = [
            _seed(folder, "a.png", prompt="1girl", generator="webui",
                  checkpoint="ponyRealism.safetensors"),
            _seed(folder, "b.png", prompt="1boy", generator="comfyui",
                  checkpoint="ponyRealism.safetensors"),
        ]
        return {"folder": folder, "rows": rows}

    def test_a_library_with_nothing_wrong_claims_no_problem(self, clean_sd_library):
        """The decisive shape: every readable row lacks a negative prompt and
        that is all that is 'missing', so nothing in the issue vocabulary that
        claims a problem may be non-zero.

        Scoped to keys that claim a problem — a key the vocabulary declares as
        reported-only coverage (``missing_embedding`` / ``missing_aesthetic``,
        whose complements are already published as percentages) is not a claim
        that anything is wrong, and both are rendered deliberately even at zero.
        The scoping is read out of the vocabulary, never listed here.
        """
        from db_facets import ISSUE_VOCABULARY

        report = _report()
        claims_a_problem = {spec.key for spec in ISSUE_VOCABULARY if spec.remedy is not None}
        nonzero = {
            key: value
            for key, value in report["issue_counts"].items()
            if value and key in claims_a_problem
        }

        assert nonzero == {}, (
            "a key nothing renders today becomes a permanent bar the moment "
            "someone iterates issue_counts: " + repr(nonzero)
        )
        assert _report()["summary"]["actionable_count"] == 0
        assert _report()["recommendations"] == []

    def test_the_negative_prompt_count_survives_as_a_statistic(self, clean_sd_library):
        report = _report()

        assert report["statistics"]["missing_negative_prompt"] == len(
            clean_sd_library["rows"]
        )
        assert "missing_negative_prompt" not in report["issue_counts"]


class TestAnIncompleteScanRecord:
    """``missing_file_size`` is the other unrendered key, and unlike the negative
    prompt it *is* a defect: the scanner records a size for every readable image,
    so a readable row without one has an incomplete record and a re-scan fixes
    it. On the owner's library it is 63 rows — the same 63 that are missing
    dimensions, which is why the advice must count rows and not counters.
    """

    @pytest.fixture
    def interrupted_scan_library(self, test_db, tmp_path: Path) -> Dict[str, Any]:
        folder = tmp_path / "interrupted"
        folder.mkdir()
        both = [
            _seed(folder, "both-a.png", caption=PROSE_CAPTION, dimensions=False, file_size=None),
            _seed(folder, "both-b.png", caption=PROSE_CAPTION, dimensions=False, file_size=None),
        ]
        size_only = [
            _seed(folder, "size-only.png", caption=PROSE_CAPTION, file_size=None),
        ]
        dimensions_only = [
            _seed(folder, "dims-only.png", caption=PROSE_CAPTION, dimensions=False),
        ]
        complete = [_seed(folder, "fine.png", caption=PROSE_CAPTION)]
        return {
            "folder": folder,
            "both": both,
            "size_only": size_only,
            "dimensions_only": dimensions_only,
            "complete": complete,
        }

    def test_the_advice_counts_rows_not_counters(self, interrupted_scan_library):
        """Two counters over overlapping rows summed to twice the work on his
        library. The number beside the action is the number of rows it visits."""
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}

        distinct_rows = _count_where(f"{_READABLE_SQL} AND {_INCOMPLETE_RECORD_SQL}")
        summed_counters = (
            report["issue_counts"]["missing_dimensions"]
            + report["issue_counts"]["missing_file_size"]
        )

        assert summed_counters > distinct_rows, "fixture must exercise the overlap"
        assert by_kind["incomplete_scan_record"]["count"] == distinct_rows

    def test_a_readable_row_without_a_size_is_still_counted(self, interrupted_scan_library):
        report = _report()

        assert report["issue_counts"]["missing_file_size"] == len(
            interrupted_scan_library["both"]
        ) + len(interrupted_scan_library["size_only"])
        assert "missing_file_size" not in report["statistics"]


class TestTheReconnectAdviceStopsDoubleCountingDeadRows:
    """Found by the invariant, not by inspection: ``mark_image_unreadable`` sets
    ``metadata_status = 'error'``, so every dead row lands in both
    ``issue_counts.unreadable`` and ``issue_counts.metadata_error``, and the
    ``reparse_or_reconnect`` card added the two together. On the owner's library
    that advertised **3,074** rows to re-parse where **1,537** exist.
    """

    @pytest.fixture
    def dead_row_library(self, test_db, tmp_path: Path) -> Dict[str, Any]:
        folder = tmp_path / "moved-away"
        folder.mkdir()
        dead = [
            _seed(folder, f"gone-{index}.png", caption=PROSE_CAPTION, readable=False)
            for index in range(3)
        ]
        alive = [_seed(folder, "here.png", caption=PROSE_CAPTION)]
        return {"folder": folder, "dead": dead, "alive": alive}

    def test_the_advice_names_the_rows_that_exist(self, dead_row_library):
        report = _report()
        by_kind = {item["kind"]: item for item in report["recommendations"]}

        distinct_rows = _count_where(
            f"NOT ({_READABLE_SQL}) "
            "OR LOWER(COALESCE(metadata_status, 'complete')) = 'error'"
        )

        assert distinct_rows == len(dead_row_library["dead"])
        assert by_kind["reparse_or_reconnect"]["count"] == distinct_rows


class TestEndpoint:
    def test_the_endpoint_serves_the_split(self, test_client, test_db, tmp_path: Path):
        from services.sorting_service import invalidate_library_health_cache

        folder = tmp_path / "served"
        folder.mkdir()
        unknown = [_seed(folder, "art.png", caption=PROSE_CAPTION)]
        stale = [
            _seed(folder, "legacy.png", prompt="1girl", generator="unknown",
                  checkpoint="ponyRealism.safetensors"),
        ]

        invalidate_library_health_cache()
        response = test_client.get("/api/library-health?sample_limit=25")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "unknown_generator" not in payload["issue_counts"]
        assert "missing_negative_prompt" not in payload["issue_counts"]
        assert payload["statistics"]["unknown_generator"] >= len(unknown)
        assert payload["issue_counts"]["unattributed_sd_metadata"] >= len(stale)
        invalidate_library_health_cache()
