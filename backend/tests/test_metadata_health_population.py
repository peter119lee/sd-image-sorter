"""The metadata-health payload must describe one population, field for field.

Background
==========
``GET /api/metadata/health`` drives the settings "metadata health" row and the
"Recover Missing Text" button, whose tooltip advertises ``totals.missing_text``
as the number of images a run will retry. The repair job restricts itself to
``COALESCE(is_readable, 1) = 1`` — a row whose file is gone cannot be re-parsed
by anything — but the payload counted every indexed row, so the advertised
number and the targeted number were different numbers next to each other.

Measured read-only on the owner's real library (schema_version 44, 6,919 rows,
5,382 readable): the button advertised **5,198** while the job could change
**4,420**. The 778-row gap is rows pointing at deleted files, and no repair can
touch one of them.

Fixing only ``missing_text`` would have left it disagreeing with the ``total``
printed beside it, so the whole payload picks one population: **readable
images**, the same set the job walks. ``scope`` names it and
``excluded_unreadable`` accounts for the rows left out, so the difference from
the library total stays visible instead of silently vanishing.

Fixtures mirror the owner's real shape on purpose: dead rows, ``generator =
'others'``, caption-only rows reached through both ``prompt IS NULL`` and
``TRIM(prompt) = ''``, ``sidecar_caption_format`` NULL for pre-044 rows, a
stored raw envelope on a dead row, and no checkpoint anywhere. A fixture of
clean SD output would pass while his library stayed wrong.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db
from services import metadata_repair_service as mrs

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"
PROSE_CAPTION = "A woman with silver hair stands in a sunlit doorway."

# Spelled out here rather than imported, so the test states the contract
# independently of the production constants it is checking.
_READABLE_SQL = "COALESCE(is_readable, 1) = 1"
_NO_PROMPT_SQL = "(prompt IS NULL OR TRIM(prompt) = '')"
_NO_CAPTION_SQL = "(sidecar_caption IS NULL OR TRIM(sidecar_caption) = '')"
_TEXTLESS_READABLE_SQL = f"{_NO_PROMPT_SQL} AND {_NO_CAPTION_SQL} AND {_READABLE_SQL}"

_COUNTER_KEYS = ("total", "missing_prompt", "missing_text", "with_raw")


def _raw_envelope() -> bytes:
    return gzip.compress(json.dumps({"parameters": "unparseable by today"}).encode("utf-8"))


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    generator: str = "unknown",
    readable: bool = True,
    raw: bool = False,
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
            width=64,
            height=64,
            file_size=1024,
            metadata_json="{}",
            raw_metadata_gz=_raw_envelope() if raw else None,
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
    if not readable:
        db.mark_image_unreadable(image_id, "original file is gone")
    return image_id


@pytest.fixture
def owner_shaped_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """A library shaped like the owner's: mostly non-SD art plus dead rows."""
    folder = tmp_path / "library"
    folder.mkdir()

    caption_only = [
        # prompt IS NULL + a booru-style sidecar, format never derived (pre-044).
        _seed(folder, "caption-null-prompt.png", caption=DANBOORU_CAPTION, generator="others"),
        # TRIM(prompt) = '' + a prose sidecar: the same row shape, other spelling.
        _seed(folder, "caption-blank-prompt.png", prompt="", caption=PROSE_CAPTION),
    ]
    live_textless = [
        _seed(folder, "textless-a.png"),
        _seed(folder, "textless-b.png", prompt="   ", generator="others"),
        _seed(folder, "textless-c.png", raw=True, generator="comfyui"),
    ]
    # The rows that made the advertised number bigger than the targeted one:
    # unreadable and textless, so no run can ever move them.
    dead_textless = [
        _seed(folder, "dead-textless-a.png", readable=False),
        _seed(folder, "dead-textless-b.png", readable=False, raw=True, generator="others"),
    ]
    # The owner has 759 of these: file gone, text still on record.
    dead_with_text = [
        _seed(folder, "dead-prompted.png", prompt="masterpiece, 1girl", readable=False),
    ]
    live_prompted = [
        _seed(folder, "real-prompt.png", prompt="masterpiece, 1girl, cyberpunk city", generator="others"),
    ]

    return {
        "folder": folder,
        "caption_only": caption_only,
        "live_textless": live_textless,
        "dead_textless": dead_textless,
        "dead_with_text": dead_with_text,
        "live_prompted": live_prompted,
    }


def _ids_where(sql: str) -> List[int]:
    with db.get_db() as conn:
        return [int(row["id"]) for row in conn.execute(f"SELECT id FROM images WHERE {sql}")]


def _count_where(sql: str) -> int:
    with db.get_db() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM images WHERE {sql}").fetchone()[0])


class TestTheAdvertisedNumberEqualsTheTargetedNumber:
    def test_the_recover_count_equals_what_a_run_can_still_change(self, owner_shaped_library):
        """The tooltip's number and the job's reach must be one number.

        The job deliberately walks every promptless readable row (a parser
        upgrade may yet crack one), so its snapshot is a superset; the subset it
        can still turn from "no text" into "has text" is what may be advertised.
        """
        snapshot = set(mrs.snapshot_missing_prompt_ids())
        reducible = set(_ids_where(_TEXTLESS_READABLE_SQL))

        assert owner_shaped_library["dead_textless"], "fixture must contain unfixable rows"
        assert reducible.isdisjoint(owner_shaped_library["dead_textless"]), (
            "a row whose file is gone is not something a re-parse can change"
        )
        assert reducible < snapshot, "fixture must exercise the superset gap"

        totals = mrs.get_metadata_health()["totals"]

        assert totals["missing_text"] == len(reducible)

    def test_the_prompt_counter_equals_the_rows_the_job_walks(self, owner_shaped_library):
        """The other counter in the same row describes the job's own snapshot."""
        totals = mrs.get_metadata_health()["totals"]

        assert totals["missing_prompt"] == len(mrs.snapshot_missing_prompt_ids())

    def test_a_row_no_repair_can_touch_moves_no_advertised_number(self, owner_shaped_library):
        """The decisive behaviour: indexing another dead file is not new work.

        Every counter a user reads beside the Recover button must be unmoved by
        a row the button cannot reach; only the excluded tally may grow.
        """
        before = mrs.get_metadata_health()

        _seed(owner_shaped_library["folder"], "dead-textless-c.png", readable=False)
        after = mrs.get_metadata_health()

        assert after["totals"] == before["totals"], (
            "a dead, textless row is not work the recovery job can do"
        )
        assert after["excluded_unreadable"] == before["excluded_unreadable"] + 1, (
            "the row must still be accounted for, not silently dropped"
        )


class TestEveryFieldDescribesTheSamePopulation:
    def test_the_payload_names_its_population_and_accounts_for_the_rest(
        self, owner_shaped_library
    ):
        payload = mrs.get_metadata_health()
        readable = _count_where(_READABLE_SQL)
        indexed = _count_where("1 = 1")

        assert payload["scope"] == "readable_images"
        assert payload["totals"]["total"] == readable
        assert payload["excluded_unreadable"] == indexed - readable
        assert payload["excluded_unreadable"] == len(
            owner_shaped_library["dead_textless"] + owner_shaped_library["dead_with_text"]
        )

    def test_no_counter_outgrows_the_total_printed_beside_it(self, owner_shaped_library):
        """Two adjacent fields counting different populations is the defect."""
        payload = mrs.get_metadata_health()

        for row in payload["generators"] + [payload["totals"]]:
            assert row["missing_text"] <= row["missing_prompt"] <= row["total"]
            assert row["with_raw"] <= row["total"]

    def test_generator_rows_sum_to_the_totals_they_are_shown_under(
        self, owner_shaped_library
    ):
        payload = mrs.get_metadata_health()

        for key in _COUNTER_KEYS:
            assert sum(row[key] for row in payload["generators"]) == payload["totals"][key], key

    def test_a_dead_row_is_absent_from_its_own_generator_row_too(
        self, owner_shaped_library
    ):
        """The per-generator breakdown is the same population, split up."""
        payload = mrs.get_metadata_health()
        by_generator = {row["generator"]: row for row in payload["generators"]}

        assert by_generator["others"]["total"] == _count_where(
            f"{_READABLE_SQL} AND generator = 'others'"
        )
        assert by_generator["others"]["missing_text"] == _count_where(
            f"{_TEXTLESS_READABLE_SQL} AND generator = 'others'"
        )

    def test_a_stored_envelope_on_a_dead_row_is_not_offered_as_replayable(
        self, owner_shaped_library
    ):
        """``with_raw`` says "this can be replayed without the file". The job
        never looks at an unreadable row, so its envelope cannot be replayed."""
        payload = mrs.get_metadata_health()

        assert _count_where("raw_metadata_gz IS NOT NULL AND COALESCE(is_readable, 1) = 0") == 1, (
            "fixture must exercise a dead row carrying a stored envelope"
        )
        assert payload["totals"]["with_raw"] == _count_where(
            f"raw_metadata_gz IS NOT NULL AND {_READABLE_SQL}"
        )


class TestEndpoint:
    def test_the_endpoint_serves_the_same_agreed_numbers(
        self, test_client, owner_shaped_library
    ):
        response = test_client.get("/api/metadata/health")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["scope"] == "readable_images"
        assert payload["totals"]["missing_text"] == len(_ids_where(_TEXTLESS_READABLE_SQL))
        assert payload["totals"]["missing_prompt"] == len(mrs.snapshot_missing_prompt_ids())
        assert payload["totals"]["total"] == _count_where(_READABLE_SQL)
