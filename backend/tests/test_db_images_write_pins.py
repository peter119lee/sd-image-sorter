"""Characterization pins for ``db_images_write`` (god-file split, step 0).

``db_images_write.py`` is the write side of the image layer: the scan upsert
(``add_image`` / ``add_images_batch`` / ``_upsert_image_record``), the
cursor-taking derived-state maintainers (``_clear_image_derived_state``,
``_sync_image_loras`` / ``_sync_image_prompt_tokens``,
``_copy_image_derived_state``), the ``mark_*`` / ``update_*`` mutators, the
source-path reconnect, and the deletions. It owns no connection of its own
(every entry point opens ``db_core.get_db()``), holds **no module-level mutable
state**, and rebinds no globals — the recon verdict is a stateless pure
sibling-module split (the ``db_images_read`` / ``db_query`` template).

The defining contract is an **import fan-in by reference**: three consumers
origin-import names from this module *by name* and call them verbatim ---
``database.py`` re-exports all 27 public+private names, ``db_collections``
pulls ``_compact_persisted_metadata_json``, and ``db_tags`` pulls
``_mark_image_tagged``. These pins lock:

* the identity re-export union (``consumer.X is db_images_write.X`` for every
  imported name) so a later tiling split cannot silently drop, rename, or
  wrapper a name (``image_manager`` / ``sorting_service`` reach the writers
  through ``from database import ...`` so the facade identity chain matters),
* the stateless-module verdict (no module-level assignment, no ``global``),
* the upsert new/updated status, the ``raw_metadata_gz`` L3 invariant, the
  ``metadata_status == "pending"`` "don't consume the source fingerprint yet"
  rule, and the loras/prompt-token index sync (populate + wipe-on-rescan),
* the derived-state clear/preserve **contrast** — ``mark_image_unreadable``
  and pixel-change rewrites clear expensive derived state while
  ``reconnect_image_source_path`` deliberately preserves it,
* the caption / user-rating / color / copy-derived / delete / reparse writers
  and their return contracts (bool rowcount, ValueError bounds, no-op guards).

Everything runs against the temp-file SQLite built by the shared ``test_db``
fixture (conftest.py). No real ``data/images.db`` is touched. Consumers in
production reach these functions through the ``database`` facade, so pins drive
``import database as db`` and assert the re-export chain. Complements
``test_database.py`` (basic add/update/delete integration) and
``test_derived_state_contract.py`` (the derived-writer allowlist); these pins
target the export surface and the write-path edge contracts a split must keep.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

# Match the sibling test modules' import bootstrap.
sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import db_images_write
import db_core
import db_collections
import db_tags
from db_images_write import _normalize_indexed_image_path


# The exact name set the ``database`` facade re-exports from ``db_images_write``
# (database.py ``from db_images_write import (...)``, 28 names). This list IS the
# public re-export contract a split must preserve; ``image_manager`` and
# ``sorting_service`` do ``from database import add_images_batch/update_image_path``,
# so the facade binding must stay identical to the origin object.
_REEXPORTED_WRITE_NAMES = [
    "_clear_image_derived_state",
    "_sync_image_loras",
    "_sync_image_prompt_tokens",
    "add_image",
    "_get_existing_images_by_paths",
    "_compact_persisted_metadata_json",
    "_upsert_image_record",
    "add_images_batch",
    "get_image_scan_state_by_paths",
    "reconnect_image_source_path",
    "_mark_image_tagged",
    "delete_images_by_ids",
    "delete_images_by_paths",
    "mark_pending_images_metadata_error",
    "_copy_image_derived_state",
    "copy_image_derived_state",
    "set_image_captions",
    "update_image_caption",
    "set_user_rating",
    "update_image_colors",
    "update_image_path",
    "update_image_metadata",
    "update_reparsed_prompt_fields",
    "update_reparsed_sidecar_caption",
    "mark_image_unreadable",
    "mark_image_unreadable_by_path",
    "mark_image_readable",
    "delete_image",
]


def _add(path, **kwargs):
    """Add a minimal image row and return its id (keeps pins terse)."""
    kwargs.setdefault("filename", path.rsplit("/", 1)[-1])
    return db.add_image(path=path, **kwargs)


def _row(image_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        return dict(row) if row else None


def _loras(image_id):
    with db.get_db() as conn:
        return sorted(
            r[0]
            for r in conn.execute(
                "SELECT lora_name FROM image_loras WHERE image_id = ?", (image_id,)
            ).fetchall()
        )


def _tokens(image_id):
    with db.get_db() as conn:
        return sorted(
            r[0]
            for r in conn.execute(
                "SELECT token FROM image_prompt_tokens WHERE image_id = ?", (image_id,)
            ).fetchall()
        )


def _tag_count(image_id):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM tags WHERE image_id = ?", (image_id,)
        ).fetchone()[0]


# ===========================================================================
# Re-export identity union (the defining contract)
# ===========================================================================


class TestReExportIdentityUnion:
    def test_write_module_owns_every_reexported_name(self):
        """db_images_write must define every name the facade re-exports."""
        assert len(_REEXPORTED_WRITE_NAMES) == 28
        for name in _REEXPORTED_WRITE_NAMES:
            assert hasattr(db_images_write, name), f"db_images_write lost {name}"

    def test_database_facade_bindings_are_identical_objects(self):
        """database.X must be the identical object exported by db_images_write.X.

        A split that leaves a name behind (or shadows it with a wrapper) breaks
        every reader that imports through the facade — including
        ``from database import add_images_batch`` in sorting_service and
        ``from database import update_image_path`` in image_manager.
        """
        for name in _REEXPORTED_WRITE_NAMES:
            assert hasattr(db, name), f"database facade missing {name}"
            assert getattr(db, name) is getattr(db_images_write, name), (
                f"{name} diverged between database and db_images_write"
            )

    def test_db_collections_shares_compact_metadata_helper(self):
        """db_collections imports _compact_persisted_metadata_json by reference."""
        assert (
            db_collections._compact_persisted_metadata_json
            is db_images_write._compact_persisted_metadata_json
        )

    def test_db_tags_shares_mark_image_tagged(self):
        """db_tags imports _mark_image_tagged by reference."""
        assert db_tags._mark_image_tagged is db_images_write._mark_image_tagged

    def test_write_module_shares_the_injected_connection_provider(self):
        """db_images_write.get_db is the single db_core-injected factory.

        All db_* modules must share one connection provider (set once in
        database.py via db_core.set_connection_provider); a split must not
        re-bind a private get_db.
        """
        assert db_images_write.get_db is db_core.get_db


# ===========================================================================
# Stateless-module verdict (recon: pure sibling-module split)
# ===========================================================================


class TestStatelessModuleContract:
    def test_module_has_no_top_level_state_and_no_global_rebinds(self):
        """Lock the stateless verdict: no module-level assignment, no ``global``.

        The proven split (db_images_read / db_query) is a pure, stateless tiling
        because the module keeps zero mutable module state and zero global
        rebinds. If a split introduces a module-level cache/lock or a ``global``
        statement, the statefulness profile changed — fail loudly here.
        """
        source = Path(db_images_write.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        top_level_assignments = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        assert top_level_assignments == [], (
            f"unexpected module-level state: {top_level_assignments}"
        )

        global_statements = [
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Global)
            for name in node.names
        ]
        assert global_statements == [], (
            f"unexpected global rebinds: {global_statements}"
        )


# ===========================================================================
# add_image / _upsert_image_record — status, indexes, source/raw invariants
# ===========================================================================


class TestAddImageUpsert:
    def test_add_image_returns_bare_id_by_default(self, test_db):
        image_id = _add("/w/plain.png")
        assert isinstance(image_id, int)
        assert _row(image_id)["path"].endswith("plain.png")

    def test_return_status_reports_new_then_updated_for_same_path(self, test_db):
        first = db.add_image(
            path="/w/upsert.png", filename="upsert.png", return_status=True
        )
        second = db.add_image(
            path="/w/upsert.png", filename="upsert.png", return_status=True
        )
        assert first == (first[0], "new")
        assert second == (first[0], "updated")  # same row id, upsert path

    def test_add_image_syncs_prompt_tokens_from_comma_list(self, test_db):
        image_id = _add("/w/tok.png", prompt="cat, dog")
        assert _tokens(image_id) == ["cat", "dog"]

    def test_loras_index_is_populated_then_wiped_by_a_lora_less_rescan(self, test_db):
        # Bare-name loras lists DO populate the normalized image_loras index and
        # the loras column ...
        _add("/w/lora.png", prompt="1girl", loras=["detail_tweaker", "add_detail"])
        image_id = db.get_image_by_path("/w/lora.png")["id"]
        assert _loras(image_id) == ["add_detail", "detail_tweaker"]
        assert _row(image_id)["loras"] == '["detail_tweaker", "add_detail"]'

        # ... but re-adding the same path WITHOUT loras (a lora-less rescan)
        # deletes the index rows and NULLs the column (full-row upsert, no
        # COALESCE on loras). Pinned as-is: a dormant bug, not desired behaviour.
        _add("/w/lora.png", prompt="1girl")
        assert _loras(image_id) == []
        assert _row(image_id)["loras"] is None

    def test_pending_update_preserves_source_fingerprint_until_complete(self, test_db):
        _add(
            "/w/src.png",
            source_mtime_ns=100,
            source_size=200,
            metadata_status="complete",
        )
        image_id = db.get_image_by_path("/w/src.png")["id"]

        # A "pending" placeholder rescan must NOT consume the new source
        # fingerprint (the final metadata backfill still needs to compare pixels).
        _add(
            "/w/src.png",
            source_mtime_ns=999,
            source_size=888,
            metadata_status="pending",
        )
        pending_row = _row(image_id)
        assert pending_row["source_mtime_ns"] == 100
        assert pending_row["source_size"] == 200

        # A "complete" update does consume it.
        _add(
            "/w/src.png",
            source_mtime_ns=999,
            source_size=888,
            metadata_status="complete",
        )
        complete_row = _row(image_id)
        assert complete_row["source_mtime_ns"] == 999
        assert complete_row["source_size"] == 888

    def test_raw_metadata_envelope_is_cleared_once_a_prompt_is_recovered(self, test_db):
        # L3 invariant: a raw envelope only lives on rows whose prompt is missing.
        _add("/w/raw.png", prompt=None, raw_metadata_gz=b"RAW1", metadata_status="pending")
        image_id = db.get_image_by_path("/w/raw.png")["id"]
        assert _row(image_id)["raw_metadata_gz"] == b"RAW1"

        # A successful parse (prompt present) on the same path clears the stale raw.
        _add("/w/raw.png", prompt="a recovered prompt", raw_metadata_gz=None)
        assert _row(image_id)["raw_metadata_gz"] is None


# ===========================================================================
# add_images_batch — counts, status map, empty guard
# ===========================================================================


class TestBatchWrites:
    def test_empty_batch_returns_zero_counts(self, test_db):
        assert db.add_images_batch([]) == {
            "new": 0,
            "updated": 0,
            "skipped_other_library": 0,
        }
        assert db.add_images_batch([], return_statuses=True) == {
            "new": 0,
            "updated": 0,
            "skipped_other_library": 0,
            "statuses": {},
        }

    def test_batch_counts_new_and_updated_and_maps_status_by_path(self, test_db):
        first = db.add_images_batch(
            [
                {"path": "/w/b1.png", "filename": "b1.png"},
                {"path": "/w/b2.png", "filename": "b2.png"},
            ]
        )
        assert first == {
            "new": 2,
            "updated": 0,
            "skipped_other_library": 0,
        }

        second = db.add_images_batch(
            [
                {"path": "/w/b2.png", "filename": "b2.png"},
                {"path": "/w/b3.png", "filename": "b3.png"},
            ],
            return_statuses=True,
        )
        assert second["new"] == 1
        assert second["updated"] == 1
        assert second["statuses"] == {"/w/b2.png": "updated", "/w/b3.png": "new"}


# ===========================================================================
# get_image_scan_state_by_paths — folder-scan fast path
# ===========================================================================


class TestScanStateReader:
    def test_scan_state_empty_and_returns_row_dict_keyed_by_path(self, test_db):
        assert db.get_image_scan_state_by_paths([]) == {}

        _add("/w/scan.png")
        state = db.get_image_scan_state_by_paths(["/w/scan.png"])
        assert list(state.keys()) == ["/w/scan.png"]
        assert "id" in state["/w/scan.png"]


# ===========================================================================
# Derived-state clear/preserve contrast + readability transitions
# ===========================================================================


class TestReadabilityAndDerivedState:
    def _seed_derived(self, image_id):
        db.add_tags(
            image_id,
            [{"tag": "kept_tag", "confidence": 0.9}],
            content_fingerprint="fp-1",
        )

    def test_reconnect_restores_readable_and_preserves_derived_state(self, test_db):
        _add("/w/gone.png")
        image_id = db.get_image_by_path("/w/gone.png")["id"]
        self._seed_derived(image_id)
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET is_readable = 0, read_error = ?, metadata_status = ? WHERE id = ?",
                ("File not found", "error", image_id),
            )

        db.reconnect_image_source_path(
            image_id, "/w/found.png", source_mtime_ns=55, source_size=66
        )

        row = _row(image_id)
        # Path moved + a "not found" read_error is healed back to readable ...
        assert row["path"] == _normalize_indexed_image_path("/w/found.png")
        assert row["filename"] == "found.png"
        assert row["is_readable"] == 1
        assert row["read_error"] is None
        assert row["metadata_status"] == "complete"
        # ... but the expensive derived state is deliberately KEPT (contrast with
        # mark_image_unreadable / pixel-change rewrites).
        assert row["tagged_at"] is not None
        assert row["content_fingerprint"] == "fp-1"
        assert _tag_count(image_id) == 1

    def test_mark_image_unreadable_clears_pixel_caches_but_keeps_the_tags(self, test_db):
        """An unreachable file is not a changed file, so the tags still hold.

        This assertion used to require ``_tag_count == 0``, which pinned a
        data-loss bug: the same call runs from an ordinary gallery listing, so an
        unplugged drive plus one page load deleted every tag on it. It also made
        the "derived state is deliberately KEPT" behaviour of
        ``reconnect_image_source_path`` above unreachable — there was never
        anything left to keep by the time a reconnect ran.
        """
        _add("/w/bad.png")
        image_id = db.get_image_by_path("/w/bad.png")["id"]
        self._seed_derived(image_id)

        db.mark_image_unreadable(image_id, "Truncated File Read")

        row = _row(image_id)
        assert row["is_readable"] == 0
        assert row["read_error"] == "Truncated File Read"
        assert row["metadata_status"] == "error"
        # Recomputed from pixels we can no longer read, so these must go.
        assert row["tagged_at"] is None
        assert row["content_fingerprint"] is None
        # Entered against this image, and unrecoverable if dropped.
        assert _tag_count(image_id) == 1

    def test_mark_image_unreadable_by_path_marks_every_equivalent_row(self, test_db):
        stored_paths = (
            r"C:\Library\Bad.png",
            "/mnt/c/Library/Bad.png",
        )
        unrelated_path = "/mnt/c/Library/Other.png"
        with db.get_db() as conn:
            conn.executemany(
                "INSERT INTO images (path, filename) VALUES (?, ?)",
                [
                    *[(path, "Bad.png") for path in stored_paths],
                    (unrelated_path, "Other.png"),
                ],
            )
            image_ids_by_path = {
                str(row["path"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, path FROM images ORDER BY id"
                ).fetchall()
            }

        for image_id in image_ids_by_path.values():
            self._seed_derived(image_id)

        db.mark_image_unreadable_by_path(
            r"c:\library\BAD.png",
            "File not found on disk",
        )

        for path in stored_paths:
            image_id = image_ids_by_path[path]
            row = _row(image_id)
            assert row["is_readable"] == 0
            assert row["read_error"] == "File not found on disk"
            assert row["metadata_status"] == "error"
            assert row["tagged_at"] is None
            assert row["content_fingerprint"] is None
            # Same rule as mark_image_unreadable: pixel caches go, tags stay.
            assert _tag_count(image_id) == 1

        unrelated_id = image_ids_by_path[unrelated_path]
        unrelated_row = _row(unrelated_id)
        assert unrelated_row["is_readable"] == 1
        assert unrelated_row["tagged_at"] is not None
        assert unrelated_row["content_fingerprint"] == "fp-1"
        assert _tag_count(unrelated_id) == 1

    def test_mark_image_readable_restores_complete_flags(self, test_db):
        _add("/w/heal.png")
        image_id = db.get_image_by_path("/w/heal.png")["id"]
        db.mark_image_unreadable(image_id, "boom")

        db.mark_image_readable(image_id)

        row = _row(image_id)
        assert row["is_readable"] == 1
        assert row["read_error"] is None
        assert row["metadata_status"] == "complete"

    def test_mark_image_unreadable_does_not_store_a_drive_path(self, test_db):
        """The unreadable write is the funnel: callers must not be able to persist a folder.

        Tagging, similarity, Manual Sort, and the workbench all pass
        ``verify_image_readable(...).read_error`` through unchanged. Pillow
        answers ``cannot identify image file '<absolute path>'``. That string
        used to land in ``images.read_error`` as-is.
        """
        image_id = _add("/w/kept.png")
        db.mark_image_unreadable(
            image_id,
            r"cannot identify image file 'L:\OwnersLibrary\kept.png'",
        )

        stored = _row(image_id)["read_error"] or ""
        assert stored
        assert "kept.png" in stored
        assert "cannot identify" in stored
        assert re.search(r"[A-Za-z]:\\", stored) is None, stored
        assert "OwnersLibrary" not in stored

    def test_mark_image_unreadable_by_path_does_not_store_a_drive_path(self, test_db):
        image_id = _add("/w/by_path.png")
        db.mark_image_unreadable_by_path(
            "/w/by_path.png",
            r"cannot identify image file 'L:\OwnersLibrary\by_path.png'",
        )

        stored = _row(image_id)["read_error"] or ""
        assert "by_path.png" in stored
        assert re.search(r"[A-Za-z]:\\", stored) is None, stored
        assert "OwnersLibrary" not in stored

    def test_already_normalised_cause_is_byte_stable_on_a_second_pass(self, test_db, tmp_path):
        """Scan/move already run the cause through the normaliser; a second pass must not change it."""
        from metadata_parser import verify_image_readable
        from utils.reported_cause import describe_readability_failure, normalize_reported_cause

        broken = tmp_path / "00042.png"
        broken.write_bytes(b"truncated image data")
        readable, raw = verify_image_readable(str(broken))
        assert readable is False and raw

        already = describe_readability_failure(raw)
        assert already == normalize_reported_cause(already)

        image_id = _add(str(broken).replace("\\", "/"), filename="00042.png")
        db.mark_image_unreadable(image_id, already)
        assert _row(image_id)["read_error"] == already

        db.mark_image_unreadable(image_id, already)
        assert _row(image_id)["read_error"] == already

    @pytest.mark.parametrize(
        "literal",
        ("File not found", "Unreadable image", "Truncated File Read", "boom"),
    )
    def test_hardcoded_english_literals_stay_themselves(self, test_db, literal):
        image_id = _add(f"/w/{literal.replace(' ', '_')}.png")
        db.mark_image_unreadable(image_id, literal)
        assert _row(image_id)["read_error"] == literal

    def test_none_and_empty_read_error_keep_current_db_shape(self, test_db):
        none_id = _add("/w/none-cause.png")
        empty_id = _add("/w/empty-cause.png")

        db.mark_image_unreadable(none_id, None)
        db.mark_image_unreadable(empty_id, "")

        assert _row(none_id)["read_error"] is None
        assert _row(empty_id)["read_error"] == ""


# ===========================================================================
# Caption writers — COALESCE pipeline vs explicit-set editor
# ===========================================================================


class TestCaptionWriters:
    def test_update_image_caption_coalesces_nl_caption(self, test_db):
        image_id = _add("/w/cap.png")

        # nl_caption None preserves the existing value (COALESCE); ai_caption set.
        db.update_image_caption(image_id, "composed caption", nl_caption=None)
        row = _row(image_id)
        assert row["ai_caption"] == "composed caption"
        assert row["nl_caption"] is None

        db.update_image_caption(image_id, "composed 2", nl_caption="pure nl")
        assert _row(image_id)["nl_caption"] == "pure nl"

    def test_set_image_captions_explicit_set_semantics(self, test_db):
        image_id = _add("/w/setcap.png")

        # No set_* flags -> no-op, returns False, writes nothing.
        assert db.set_image_captions(image_id) is False

        # An explicit set writes even an empty string (deliberate clear), and
        # touches only the flagged field.
        assert (
            db.set_image_captions(image_id, ai_caption="", set_ai_caption=True) is True
        )
        assert _row(image_id)["ai_caption"] == ""

        # Missing row -> rowcount 0 -> False.
        assert (
            db.set_image_captions(9_999_999, ai_caption="x", set_ai_caption=True)
            is False
        )


# ===========================================================================
# set_user_rating — validation + rowcount bool
# ===========================================================================


class TestUserRating:
    def test_valid_rating_persists_and_missing_row_returns_false(self, test_db):
        image_id = _add("/w/star.png")
        assert db.set_user_rating(image_id, 4) is True
        assert _row(image_id)["user_rating"] == 4
        assert db.set_user_rating(9_999_999, 3) is False

    def test_rating_rejects_out_of_range_and_non_integer(self, test_db):
        image_id = _add("/w/star2.png")
        for bad in (6, -1, "x"):
            with pytest.raises(ValueError):
                db.set_user_rating(image_id, bad)

    def test_rating_truncates_float_via_int_cast(self, test_db):
        image_id = _add("/w/star3.png")
        # int(3.9) == 3: a float in-range is truncated, not rejected. Pinned as-is.
        assert db.set_user_rating(image_id, 3.9) is True
        assert _row(image_id)["user_rating"] == 3


# ===========================================================================
# update_image_colors — empty no-op + column write
# ===========================================================================


class TestColorWriter:
    def test_empty_color_data_is_noop_and_dict_writes_columns(self, test_db):
        image_id = _add("/w/color.png")

        db.update_image_colors(image_id, {})  # falsy -> early return
        assert _row(image_id)["avg_brightness"] is None

        db.update_image_colors(
            image_id, {"avg_brightness": 0.42, "color_temperature": "warm"}
        )
        row = _row(image_id)
        assert row["avg_brightness"] == 0.42
        assert row["color_temperature"] == "warm"


# ===========================================================================
# copy_image_derived_state — same-id no-op + cached-field + artist copy
# ===========================================================================


class TestCopyDerivedState:
    def test_same_source_and_target_is_noop(self, test_db):
        image_id = _add("/w/self.png")
        db.add_tags(image_id, [{"tag": "x", "confidence": 0.9}], content_fingerprint="fp")
        # Must not raise and must not disturb the row.
        db.copy_image_derived_state(image_id, image_id)
        assert _row(image_id)["content_fingerprint"] == "fp"

    def test_copies_cached_fields_and_artist_prediction(self, test_db):
        source = _add("/w/dup_src.png")
        db.add_tags(source, [{"tag": "x", "confidence": 0.9}], content_fingerprint="fp-src")
        with db.get_db() as conn:
            conn.execute(
                "UPDATE images SET ai_caption = ?, aesthetic_score = ?, embedding = ? WHERE id = ?",
                ("cap", 7.5, b"emb", source),
            )
            conn.execute(
                """
                INSERT INTO artist_predictions
                    (image_id, artist, confidence, top_predictions, identified_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source, "rembrandt", 0.9, "[]", "2026-01-01T00:00:00"),
            )
        target = _add("/w/dup_tgt.png")

        db.copy_image_derived_state(source, target)

        row = _row(target)
        assert row["ai_caption"] == "cap"
        assert row["aesthetic_score"] == 7.5
        assert row["embedding"] == b"emb"
        assert row["content_fingerprint"] == "fp-src"
        with db.get_db() as conn:
            artist = conn.execute(
                "SELECT artist FROM artist_predictions WHERE image_id = ?", (target,)
            ).fetchone()
        assert artist["artist"] == "rembrandt"


# ===========================================================================
# _mark_image_tagged — cursor helper (db_tags reaches it by reference)
# ===========================================================================


class TestMarkImageTagged:
    def test_sets_tagged_at_and_coalesces_fingerprint(self, test_db):
        image_id = _add("/w/tagged.png", content_fingerprint="fp-existing")
        with db.get_db() as conn:
            db_images_write._mark_image_tagged(conn.cursor(), image_id, None)
        row = _row(image_id)
        assert row["tagged_at"] is not None
        assert row["content_fingerprint"] == "fp-existing"  # None preserves


# ===========================================================================
# Deletions — count semantics + empty guards
# ===========================================================================


class TestDeletions:
    def test_delete_images_by_ids_empty_and_counts_only_removed(self, test_db):
        assert db.delete_images_by_ids([]) == 0
        keep = _add("/w/keep.png")
        drop = _add("/w/drop.png")
        # A mix of an existing and a missing id counts only the real removal.
        assert db.delete_images_by_ids([drop, 9_999_999]) == 1
        assert _row(drop) is None
        assert _row(keep) is not None

    def test_delete_images_by_paths_empty_and_removes_by_path(self, test_db):
        assert db.delete_images_by_paths([]) == 0
        image_id = _add("/w/bypath.png")
        assert db.delete_images_by_paths(["/w/bypath.png"]) == 1
        assert _row(image_id) is None

    def test_delete_image_removes_single_row(self, test_db):
        image_id = _add("/w/single.png")
        db.delete_image(image_id)
        assert _row(image_id) is None


# ===========================================================================
# mark_pending_images_metadata_error — falsy-id drop + pending-only scope
# ===========================================================================


class TestPendingMetadataError:
    def test_drops_falsy_ids_and_targets_pending_rows_only(self, test_db):
        assert db.mark_pending_images_metadata_error([0, None], "err") == 0

        pending = _add("/w/pend.png", metadata_status="pending")
        complete = _add("/w/done.png", metadata_status="complete")

        updated = db.mark_pending_images_metadata_error(
            [pending, complete], "read boom"
        )

        # Only the pending row flips; the complete row is untouched.
        assert updated == 1
        assert _row(pending)["metadata_status"] == "error"
        assert _row(pending)["read_error"] == "read boom"
        assert _row(complete)["metadata_status"] == "complete"


# ===========================================================================
# update_image_metadata — is_readable COALESCE preserve
# ===========================================================================


class TestUpdateImageMetadata:
    def test_is_readable_none_preserves_existing_flag(self, test_db):
        image_id = _add("/w/meta.png")  # is_readable defaults to 1

        db.update_image_metadata(
            image_id=image_id,
            generator="webui",
            prompt="p",
            negative_prompt=None,
            metadata_json="{}",
            width=None,
            height=None,
            file_size=None,
            checkpoint=None,
            loras=[],
            is_readable=None,  # COALESCE(?, is_readable) -> keeps 1
        )
        assert _row(image_id)["is_readable"] == 1

        db.update_image_metadata(
            image_id=image_id,
            generator="webui",
            prompt="p",
            negative_prompt=None,
            metadata_json="{}",
            width=None,
            height=None,
            file_size=None,
            checkpoint=None,
            loras=[],
            is_readable=False,  # explicit False writes 0
        )
        assert _row(image_id)["is_readable"] == 0


# ===========================================================================
# update_reparsed_prompt_fields — targeted prompt write (L3 re-parse)
# ===========================================================================


class TestReparsePromptFields:
    def test_updates_prompt_clears_raw_and_coalesces_optional_fields(self, test_db):
        _add(
            "/w/reparse.png",
            prompt="old",
            negative_prompt="old neg",
            generator="webui",
            checkpoint="ckptA",
            raw_metadata_gz=b"RAWD",
        )
        image_id = db.get_image_by_path("/w/reparse.png")["id"]

        db.update_reparsed_prompt_fields(
            image_id,
            prompt="new prompt, tag2",
            negative_prompt=None,
            generator=None,
            checkpoint=None,
            loras=None,
        )

        row = _row(image_id)
        assert row["prompt"] == "new prompt, tag2"
        # Optional fields with None fall back to the existing value (COALESCE) ...
        assert row["negative_prompt"] == "old neg"
        assert row["generator"] == "webui"
        assert row["checkpoint"] == "ckptA"
        # ... and the recovered row drops its raw envelope.
        assert row["raw_metadata_gz"] is None
        # The token index is rebuilt from the merged prompt.
        assert _tokens(image_id) == ["new prompt", "tag2"]


# ===========================================================================
# Internal call-chain insulation (co-location hazard)
# ===========================================================================


class TestInternalCallChainInsulation:
    def test_add_image_uses_module_local_upsert_not_the_facade(
        self, test_db, monkeypatch
    ):
        """add_image calls the module-local ``_upsert_image_record``, never the
        facade's re-export.

        Patching ``database._upsert_image_record`` must NOT affect ``add_image``
        (it resolves the bare name in db_images_write's own namespace). A split
        that separates ``add_image`` from ``_upsert_image_record`` must keep the
        internal call wired module-to-module, not routed back through the
        monkeypatchable ``database`` facade. This mirrors the read-side
        ``get_images_paginated`` -> ``_get_filtered_count`` co-location concern.
        """

        def _boom(*args, **kwargs):
            raise AssertionError("facade _upsert_image_record must not be called")

        monkeypatch.setattr(db, "_upsert_image_record", _boom)

        image_id = _add("/w/insulated.png", prompt="cat")
        assert isinstance(image_id, int)
        assert _tokens(image_id) == ["cat"]
