"""A prompt-terms rule must still select rows whose text moved to sidecar_caption.

Migration 042 (``21fd5e8``) moved ``.txt`` sidecar text out of ``images.prompt``
into ``images.sidecar_caption`` so ``prompt`` keeps meaning "the SD generation
prompt that produced this image". The scan upsert writes ``prompt = ?``
unconditionally, so the owner's next rescan rewrites those rows with
``prompt = NULL`` and the text in ``sidecar_caption``.

Measured read-only against ``data/images.db`` on 2026-08-17: of 1,721
prompt-bearing rows, 962 still have their file on disk, **all 962** have a
``.txt`` sidecar beside them and **all 962** carry ``generator = 'unknown'``
(no embedded SD metadata). So the post-rescan shape is not hypothetical — it is
14% of the owner's library.

``21fd5e8`` extended ``_apply_search_filter`` to cover the new column but not
``_apply_prompt_terms_filter``, and ``prompt_terms`` is the selection criterion
for Auto-Separate batch moves. A saved rule "prompt contains silver_hair" would
therefore have quietly moved fewer files after the rescan.

These tests assert the **selected/moved set**, never the SQL text — asserting a
query string contains a column name is what let this through review the first
time. Both consumers are covered: the browsing list and the
batch-move/selection path.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import BackgroundTasks

import services.sorting_service as ss

# The post-rescan shape: prompt is NULL, the only text lives in sidecar_caption.
SIDECAR_SILVER = "1girl, silver_hair, smile"
SIDECAR_RED = "1girl, red_hair, smile"
SIDECAR_CATGIRL = "catgirl, solo"
SIDECAR_PROSE = "A girl with silver hair stands in a field."
PROMPT_SILVER = "1girl, silver_hair, smile"


def _write_png(path: Path, color: str) -> Path:
    from PIL import Image

    Image.new("RGB", (16, 16), color=color).save(path)
    return path


@pytest.fixture
def sidecar_library(test_db, tmp_path):
    """A library in the shape the owner's becomes after his next rescan.

    ``sidecar_only`` is the row under test: a downloaded image with no embedded
    SD metadata whose Danbooru tag list sits in a ``.txt`` file, so migration
    042 stores it in ``sidecar_caption`` and leaves ``prompt`` empty.
    ``prompt_only`` is the control that the filter has always matched.
    """
    library = tmp_path / "library"
    library.mkdir()

    rows = {
        "sidecar_only": {
            "filename": "sidecar_silver.png",
            "color": "silver",
            "generator": "unknown",
            "prompt": None,
            "sidecar_caption": SIDECAR_SILVER,
        },
        "prompt_only": {
            "filename": "prompt_silver.png",
            "color": "white",
            "generator": "webui",
            "prompt": PROMPT_SILVER,
            "sidecar_caption": None,
        },
        "sidecar_other": {
            "filename": "sidecar_red.png",
            "color": "red",
            "generator": "unknown",
            "prompt": None,
            "sidecar_caption": SIDECAR_RED,
        },
        "sidecar_catgirl": {
            "filename": "sidecar_catgirl.png",
            "color": "orange",
            "generator": "unknown",
            "prompt": None,
            "sidecar_caption": SIDECAR_CATGIRL,
        },
        "sidecar_prose": {
            "filename": "sidecar_prose.png",
            "color": "blue",
            "generator": "unknown",
            "prompt": None,
            "sidecar_caption": SIDECAR_PROSE,
        },
    }

    ids = {}
    paths = {}
    for key, spec in rows.items():
        path = _write_png(library / spec["filename"], spec["color"])
        paths[key] = path
        ids[key] = test_db.add_image(
            path=str(path),
            filename=spec["filename"],
            generator=spec["generator"],
            prompt=spec["prompt"],
            sidecar_caption=spec["sidecar_caption"],
            metadata_json="{}",
        )

    # Precondition: the rows really are in the post-rescan shape.
    with test_db.get_db() as conn:
        row = conn.execute(
            "SELECT prompt, sidecar_caption FROM images WHERE id = ?",
            (ids["sidecar_only"],),
        ).fetchone()
    assert not (row["prompt"] or "").strip(), "precondition: prompt is empty"
    assert row["sidecar_caption"] == SIDECAR_SILVER

    return {"db": test_db, "ids": ids, "paths": paths, "library": library}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A fresh SortingService with its persisted-session files redirected."""
    monkeypatch.setattr(ss, "SESSION_FILE", str(tmp_path / "session.json"), raising=False)
    monkeypatch.setattr(ss, "LEGACY_SESSION_FILE", str(tmp_path / "legacy.json"), raising=False)
    return ss.SortingService()


def _selected_ids(db, **filters):
    """The exact id set batch-move snapshots and then moves."""
    selected = []
    for chunk in db.iter_filtered_image_id_chunks(chunk_size=50, **filters):
        selected.extend(chunk)
    return set(selected)


def _expected_silver(ids, match_mode):
    """Who a ``silver_hair`` rule should select, per match mode.

    ``contains`` additionally reaches the prose sidecar because it is a
    substring search — the same way it has always reached a prose *prompt*.
    ``exact`` cannot, because a sentence has no comma-delimited ``silver hair``
    token. The modes differing is the pre-existing contract, not a new one.
    """
    expected = {ids["sidecar_only"], ids["prompt_only"]}
    if match_mode == "contains":
        expected.add(ids["sidecar_prose"])
    return expected


# ===========================================================================
# The batch-move / selection path — what an Auto-Separate rule actually moves
# ===========================================================================


class TestAutoSeparateMovesSidecarTextRows:
    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_rule_moves_the_file_whose_text_is_now_in_the_sidecar(
        self, sidecar_library, svc, tmp_path, match_mode
    ):
        """The decisive test: run the real move and look at the destination.

        Before the fix the sidecar-only image stayed behind while the identical
        image with the same text in ``prompt`` was moved — the same rule, two
        different outcomes, no message to the user.
        """
        destination = tmp_path / "silver"

        background_tasks = BackgroundTasks()
        started = svc.batch_move_images(
            ss.BatchMoveRequest(
                destination_folder=str(destination),
                operation="move",
                prompts=["silver_hair"],
                prompt_match_mode=match_mode,
            ),
            background_tasks,
        )
        assert started.get("count") or started.get("total"), started
        background_tasks.tasks[0].func()

        progress = svc.get_batch_move_progress()
        assert progress["errors"] == 0, progress["recent_errors"]

        expected = {"sidecar_silver.png", "prompt_silver.png"}
        if match_mode == "contains":
            expected.add("sidecar_prose.png")

        moved = {p.name for p in destination.iterdir()}
        assert moved == expected, (
            "an Auto-Separate rule for silver_hair must move the sidecar-only "
            "image too, exactly as it did before its text was relocated"
        )
        assert sidecar_library["paths"]["sidecar_other"].exists(), (
            "a non-matching image must stay where it is"
        )

    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_preflight_count_matches_what_gets_moved(self, sidecar_library, match_mode):
        """The "N images will be moved" number the user confirms.

        ``batch_move_images`` reads this count before starting and returns it as
        ``total``; if it disagrees with the snapshot the progress bar lies.
        """
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]
        filters = {"prompt_terms": ["silver_hair"], "prompt_match_mode": match_mode}
        expected = _expected_silver(ids, match_mode)

        assert _selected_ids(db, **filters) == expected
        assert db.get_filtered_image_count(**filters) >= len(expected)

    def test_selection_token_chunks_reach_the_sidecar_row(self, test_client, sidecar_library):
        """`POST /api/images/selection-token` + selection-chunk is the other
        "move all matching" entry point (gallery move, delete-selected).

        ``test_client`` is requested first on purpose: the fixture repoints
        ``database.DATABASE_PATH`` at its own file, so rows seeded before it
        exists would land in a database the HTTP app never opens.
        """
        ids = sidecar_library["ids"]
        token_response = test_client.post(
            "/api/images/selection-token",
            json={"prompts": ["silver_hair"], "promptMatchMode": "exact"},
        )
        assert token_response.status_code == 200, token_response.text
        token = token_response.json()["selection_token"]

        chunk = test_client.get(
            "/api/images/selection-chunk",
            params={"selection_token": token, "limit": 100},
        )
        assert chunk.status_code == 200, chunk.text
        assert set(chunk.json()["image_ids"]) == {ids["sidecar_only"], ids["prompt_only"]}


# ===========================================================================
# The browsing path — the same filter must agree with the move
# ===========================================================================


class TestGalleryBrowsingAgreesWithTheMove:
    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_gallery_list_shows_the_sidecar_row(self, sidecar_library, match_mode):
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]

        listed = {
            img["id"]
            for img in db.get_images(
                prompt_terms=["silver_hair"], prompt_match_mode=match_mode, limit=100
            )
        }
        assert listed == _expected_silver(ids, match_mode)

    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_paginated_gallery_shows_the_sidecar_row(self, sidecar_library, match_mode):
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]

        page = db.get_images_paginated(
            prompt_terms=["silver_hair"], prompt_match_mode=match_mode, limit=100
        )
        assert {img["id"] for img in page["images"]} == _expected_silver(ids, match_mode)

    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_http_image_list_shows_the_sidecar_row(
        self, test_client, sidecar_library, match_mode
    ):
        """``test_client`` first: it repoints DATABASE_PATH at its own file."""
        ids = sidecar_library["ids"]
        response = test_client.get(
            "/api/images",
            params={"prompts": "silver_hair", "prompt_match_mode": match_mode, "limit": 100},
        )
        assert response.status_code == 200, response.text
        assert {
            img["id"] for img in response.json()["images"]
        } == _expected_silver(ids, match_mode)


# ===========================================================================
# The rule must not widen into a different rule
# ===========================================================================


class TestMatchingStaysAsNarrowAsItClaims:
    def test_exact_mode_still_means_whole_tag(self, sidecar_library):
        """``cat`` must not select ``catgirl`` just because the text moved
        columns. The sidecar arm of the SQL is a deliberately broad LIKE
        pre-filter; only the post-filter decides, and it tokenizes on commas.
        """
        db = sidecar_library["db"]
        assert _selected_ids(db, prompt_terms=["cat"], prompt_match_mode="exact") == set()

    def test_contains_mode_is_still_a_substring_search(self, sidecar_library):
        """Contains mode has always been substring-over-prompt; over the
        sidecar it behaves identically rather than inventing a third rule."""
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]
        assert _selected_ids(db, prompt_terms=["cat"], prompt_match_mode="contains") == {
            ids["sidecar_catgirl"]
        }

    def test_prose_sidecar_matches_only_in_contains_mode(self, sidecar_library):
        """A natural-language sidecar has no comma-delimited tags, so exact
        mode cannot see ``silver hair`` inside the sentence while contains
        mode can. That is the same asymmetry the two modes already had for
        prose prompts — no ``sidecar_caption_format`` gate is needed to get it.
        """
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]

        exact = _selected_ids(db, prompt_terms=["silver_hair"], prompt_match_mode="exact")
        contains = _selected_ids(
            db, prompt_terms=["silver_hair"], prompt_match_mode="contains"
        )
        assert ids["sidecar_prose"] not in exact
        assert ids["sidecar_prose"] in contains

    def test_all_terms_must_still_match(self, sidecar_library):
        """AND semantics across terms survive: one hit is not enough."""
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]
        assert _selected_ids(
            db, prompt_terms=["silver_hair", "smile"], prompt_match_mode="exact"
        ) == {ids["sidecar_only"], ids["prompt_only"]}
        assert _selected_ids(
            db, prompt_terms=["silver_hair", "cowboy_shot"], prompt_match_mode="exact"
        ) == set()


# ===========================================================================
# The exclude twin — an include that sees text its negation cannot is a
# wrong set in the more dangerous direction (it moves files the user excluded)
# ===========================================================================


class TestExcludeSeesTheSameText:
    @pytest.mark.parametrize("match_mode", ["contains", "exact"])
    def test_excluding_a_term_drops_the_sidecar_row(self, sidecar_library, match_mode):
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]

        kept = _selected_ids(
            db,
            generators=["unknown", "webui"],
            exclude_prompts=["silver_hair"],
            prompt_match_mode=match_mode,
        )
        assert ids["sidecar_only"] not in kept, (
            "an exclude rule that cannot see sidecar text moves files the user "
            "explicitly ruled out"
        )
        assert ids["prompt_only"] not in kept
        assert ids["sidecar_other"] in kept

    def test_exclude_exact_mode_does_not_over_exclude(self, sidecar_library):
        """The v3.4.0 whole-token rule for excludes must hold for sidecar text
        too: excluding ``cat`` must not also hide ``catgirl``."""
        db = sidecar_library["db"]
        ids = sidecar_library["ids"]

        kept = _selected_ids(
            db,
            generators=["unknown", "webui"],
            exclude_prompts=["cat"],
            prompt_match_mode="exact",
        )
        assert ids["sidecar_catgirl"] in kept

    def test_include_and_exclude_of_the_same_term_select_nothing(self, sidecar_library):
        """The coherence property: whatever the include matches, the exclude
        must reject. Any row surviving both is a filter contradiction."""
        db = sidecar_library["db"]
        assert (
            _selected_ids(
                db,
                prompt_terms=["silver_hair"],
                exclude_prompts=["silver_hair"],
                prompt_match_mode="exact",
            )
            == set()
        )
