"""Characterization pins for services/image_service.py (decomposition step 0).

These lock the load-bearing, currently-UNCOVERED seams of ``ImageService`` and
its module-level helpers so a later split into ``services/image/`` can be proven
behavior-preserving. Priority order mirrors the sorting_service step-0 pins:

1. The cross-service contract ``_iter_selection_token_snapshot_chunks`` —
   ``services/sorting/move.py`` constructs ``ImageService()`` purely to call this
   private generator, so its call shape + chunking are a hard contract.
2. The selection-token wire format (encode/decode round-trip + snake_case
   ``.get()`` compat) — tokens are base64 JSON that pre-date newer filters.
3. ``move_file_to_trash`` — a module-global monkeypatch seam AND the Windows
   "trash silently no-op" safety guard, both fully uncovered.
4. The delete-vs-remove id-normalization asymmetry (delete keeps <=0, remove
   drops them) and the cancel/reset idle+running branches.
5. The ``__file__`` -> backend-root contract on the file-serving paths (the
   location-sensitive trap a split must preserve).

Everything here is machine-state isolated: no real DB (``image_service.db`` is
monkeypatched — a SAFE shared-module seam), no downloaded models, no data-dir,
only ``tmp_path`` files.
"""

import os
import sys
import types

import pytest
from fastapi import HTTPException

from services import image_service
from services.image_service import ImageService


# ---------------------------------------------------------------------------
# 1. Module import surface — the names external code binds to. A split that
#    relocates these must keep them re-exported from services.image_service or
#    these imports/attribute reads go red (the intended signal).
# ---------------------------------------------------------------------------
class TestModuleImportSurface:
    def test_image_service_constructs_with_no_args(self):
        # routers/images.py, main.py, and services/sorting/move.py all do
        # ImageService() with no arguments; __init__ must stay side-effect free.
        svc = ImageService()
        assert isinstance(svc, ImageService)

    def test_move_file_to_trash_is_a_module_global(self):
        # test_routers/test_images.py patches image_service.move_file_to_trash;
        # it must remain reachable as a module attribute after any split.
        assert callable(image_service.move_file_to_trash)

    def test_db_alias_is_the_shared_database_module(self):
        # The SAFE monkeypatch seam: image_service.db must be the real database
        # module object so `image_service.db.<fn>` patches reach every caller.
        import database

        assert image_service.db is database

    def test_selection_constants_are_stable(self):
        assert image_service.SELECTION_TOKEN_VERSION == 2
        assert image_service.SELECTION_IDS_MAX_RESPONSE == 100000
        assert isinstance(image_service.SELECTION_IDS_FETCH_CHUNK, int)

    def test_random_is_a_valid_sort_option(self):
        # random is accepted by the gallery list path but rejected by the
        # chunked-token path; both invariants are pinned below.
        assert "random" in image_service.VALID_SORT_OPTIONS


# ---------------------------------------------------------------------------
# 2. Selection-filter coercers (module-level pure functions). These validate
#    the token payload and pre-date most filters, so their exact accept/reject
#    surface is the compat contract.
# ---------------------------------------------------------------------------
class TestSelectionFilterCoercers:
    def test_optional_int_filter(self):
        assert image_service._coerce_optional_int_filter(None, "f") is None
        assert image_service._coerce_optional_int_filter("5", "f") == 5
        assert image_service._coerce_optional_int_filter(5, "f") == 5

    def test_optional_int_filter_rejects_bool_and_garbage(self):
        # bool is a subclass of int — explicitly rejected so True can't mean 1.
        with pytest.raises(HTTPException) as ei:
            image_service._coerce_optional_int_filter(True, "f")
        assert ei.value.status_code == 400
        with pytest.raises(HTTPException):
            image_service._coerce_optional_int_filter("x", "f")

    def test_optional_float_filter(self):
        assert image_service._coerce_optional_float_filter(None, "f") is None
        assert image_service._coerce_optional_float_filter("1.5", "f") == 1.5
        with pytest.raises(HTTPException):
            image_service._coerce_optional_float_filter(True, "f")
        with pytest.raises(HTTPException):
            image_service._coerce_optional_float_filter("x", "f")

    def test_optional_date_filter_validates_shape_not_calendar(self):
        # Regex is ^\d{4}-\d{2}-\d{2}$ — SHAPE only. A shaped-but-impossible day
        # passes; wrong separators/None-shape are rejected 400.
        assert image_service._coerce_optional_date_filter(None, "d") is None
        assert (
            image_service._coerce_optional_date_filter("2026-07-13", "d")
            == "2026-07-13"
        )
        assert (
            image_service._coerce_optional_date_filter("  2026-07-13  ", "d")
            == "2026-07-13"
        )
        assert (
            image_service._coerce_optional_date_filter("2026-13-99", "d")
            == "2026-13-99"
        )
        with pytest.raises(HTTPException):
            image_service._coerce_optional_date_filter("2026/07/13", "d")
        with pytest.raises(HTTPException):
            image_service._coerce_optional_date_filter(20260713, "d")

    def test_optional_string_filter_strips_and_nulls_empty(self):
        assert image_service._coerce_optional_string_filter(None, "s") is None
        assert image_service._coerce_optional_string_filter("  hi ", "s") == "hi"
        assert image_service._coerce_optional_string_filter("   ", "s") is None
        for bad in ({}, [], (), set()):
            with pytest.raises(HTTPException):
                image_service._coerce_optional_string_filter(bad, "s")

    def test_optional_bool_filter_tristate(self):
        assert image_service._coerce_optional_bool_filter(None, "b") is None
        assert image_service._coerce_optional_bool_filter(True, "b") is True
        assert image_service._coerce_optional_bool_filter(1, "b") is True
        assert image_service._coerce_optional_bool_filter(0, "b") is False
        for truthy in ("true", "1", "YES", "on"):
            assert image_service._coerce_optional_bool_filter(truthy, "b") is True
        for falsy in ("false", "0", "no", "OFF"):
            assert image_service._coerce_optional_bool_filter(falsy, "b") is False
        for bad in (2, "maybe", 1.5):
            with pytest.raises(HTTPException):
                image_service._coerce_optional_bool_filter(bad, "b")

    def test_prompt_match_mode_defaults_and_lowercases(self):
        assert image_service._coerce_prompt_match_mode(None) == "exact"
        assert image_service._coerce_prompt_match_mode("CONTAINS") == "contains"
        with pytest.raises(HTTPException):
            image_service._coerce_prompt_match_mode("fuzzy")

    def test_tag_mode_defaults_and_lowercases(self):
        assert image_service._coerce_tag_mode(None) == "and"
        assert image_service._coerce_tag_mode("OR") == "or"
        with pytest.raises(HTTPException):
            image_service._coerce_tag_mode("nand")

    def test_selection_id_list_dedup_intcast_and_bounds(self):
        assert image_service._coerce_selection_id_list(None, "ids", max_length=10) == []
        assert image_service._coerce_selection_id_list(
            [3, "3", 2], "ids", max_length=10
        ) == [3, 2]
        with pytest.raises(HTTPException) as ei:
            image_service._coerce_selection_id_list([1, 2, 3], "ids", max_length=2)
        assert ei.value.status_code == 400
        assert "exceeds max length" in ei.value.detail
        for bad in ([True], [0], [-1], ["x"], {"a": 1}):
            with pytest.raises(HTTPException):
                image_service._coerce_selection_id_list(bad, "ids", max_length=10)

    def test_sanitize_filter_values_normalizes_shapes(self):
        assert image_service._sanitize_filter_values(None) is None
        assert image_service._sanitize_filter_values("a, b ,,") == ["a", "b"]
        assert image_service._sanitize_filter_values(["x", "", "  y "]) == ["x", "y"]
        assert image_service._sanitize_filter_values([]) is None


# ---------------------------------------------------------------------------
# 3. Selection-token wire format — encode/decode round-trip + snake_case compat.
# ---------------------------------------------------------------------------
class TestSelectionTokenWireFormat:
    def test_encode_decode_round_trip_preserves_filters(self):
        svc = ImageService()
        contract = svc._build_selection_filter_contract(
            sort_by="name_asc",
            tags=["a", "b"],
            min_user_rating=3,
            has_metadata=True,
        )
        token = svc._encode_selection_token(contract)
        decoded = svc._decode_selection_token(token)
        assert decoded["sortBy"] == "name_asc"
        assert decoded["tags"] == ["a", "b"]
        assert decoded["minUserRating"] == 3
        assert decoded["hasMetadata"] is True

    def test_decode_rejects_garbage_and_wrong_version(self):
        svc = ImageService()
        with pytest.raises(HTTPException) as ei:
            svc._decode_selection_token("!!!not-base64!!!")
        assert ei.value.status_code == 400

        import base64
        import json

        bad_version = (
            base64.urlsafe_b64encode(json.dumps({"v": 1, "filters": {}}).encode())
            .decode()
            .rstrip("=")
        )
        with pytest.raises(HTTPException):
            svc._decode_selection_token(bad_version)

        not_dict_filters = (
            base64.urlsafe_b64encode(json.dumps({"v": 2, "filters": []}).encode())
            .decode()
            .rstrip("=")
        )
        with pytest.raises(HTTPException):
            svc._decode_selection_token(not_dict_filters)

    def test_decode_rejects_non_list_where_list_expected(self):
        svc = ImageService()
        import base64
        import json

        payload = (
            base64.urlsafe_b64encode(
                json.dumps({"v": 2, "filters": {"tags": "not-a-list"}}).encode()
            )
            .decode()
            .rstrip("=")
        )
        with pytest.raises(HTTPException):
            svc._decode_selection_token(payload)

    def test_decode_accepts_legacy_snake_case_keys(self):
        # Tokens minted by older builds use tag_mode / prompt_match_mode /
        # min_user_rating / collection_id; the decoder's `.get(camel) or
        # .get(snake)` compat must still resolve them.
        svc = ImageService()
        import base64
        import json

        payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "v": 2,
                        "filters": {
                            "sortBy": "newest",
                            "tag_mode": "or",
                            "prompt_match_mode": "contains",
                            "min_user_rating": 4,
                            "collection_id": 9,
                        },
                    }
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        decoded = svc._decode_selection_token(payload)
        assert decoded["tagMode"] == "or"
        assert decoded["promptMatchMode"] == "contains"
        assert decoded["minUserRating"] == 4
        assert decoded["collectionId"] == 9


# ---------------------------------------------------------------------------
# 4. THE cross-service seam. services/sorting/move.py does:
#        decoder = ImageService()
#        for chunk in decoder._iter_selection_token_snapshot_chunks(token, ...):
#    so the call shape, re-chunk-by-chunk_size behavior, and random-sort refusal
#    are a hard contract the split must not move off the facade.
# ---------------------------------------------------------------------------
class TestSelectionTokenSnapshotChunks:
    def _token_for(self, svc, **overrides):
        overrides.setdefault("sort_by", "newest")
        contract = svc._build_selection_filter_contract(**overrides)
        return svc._encode_selection_token(contract)

    def test_snapshot_rechunks_by_requested_chunk_size(self, monkeypatch):
        svc = ImageService()
        token = self._token_for(svc)

        def fake_iter(**_kwargs):
            # db yields in its own chunking; the snapshot must re-chunk the
            # flattened stream by the caller's chunk_size, not the db's.
            yield [1, 2, 3]
            yield [4, 5]

        monkeypatch.setattr(
            image_service.db, "iter_filtered_image_id_chunks", fake_iter
        )
        out = list(svc._iter_selection_token_snapshot_chunks(token, chunk_size=2))
        assert out == [[1, 2], [3, 4], [5]]

    def test_snapshot_yields_nothing_for_empty_selection(self, monkeypatch):
        svc = ImageService()
        token = self._token_for(svc)
        monkeypatch.setattr(
            image_service.db, "iter_filtered_image_id_chunks", lambda **_k: iter(())
        )
        assert (
            list(svc._iter_selection_token_snapshot_chunks(token, chunk_size=500)) == []
        )

    def test_snapshot_refuses_random_sort_token(self, monkeypatch):
        svc = ImageService()
        token = self._token_for(svc, sort_by="random")
        # Should refuse on decode, before touching the db iterator.
        monkeypatch.setattr(
            image_service.db,
            "iter_filtered_image_id_chunks",
            lambda **_k: (_ for _ in ()).throw(
                AssertionError("db must not be reached")
            ),
        )
        with pytest.raises(HTTPException) as ei:
            list(svc._iter_selection_token_snapshot_chunks(token, chunk_size=500))
        assert ei.value.status_code == 400
        assert "random sort" in ei.value.detail

    def test_expand_delete_ids_uses_snapshot_for_token(self, monkeypatch):
        svc = ImageService()
        token = self._token_for(svc)
        monkeypatch.setattr(
            image_service.db,
            "iter_filtered_image_id_chunks",
            lambda **_k: iter([[10, 11], [12]]),
        )
        # selection_token path flattens the snapshot; image_ids path dedups.
        assert svc._expand_delete_request_ids(None, token) == [10, 11, 12]

    def test_create_selection_token_refuses_random(self):
        svc = ImageService()
        with pytest.raises(HTTPException) as ei:
            svc.create_selection_token(sort_by="random")
        assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# 5. move_file_to_trash — module-global seam + Windows "silent no-op" guard.
#    The import is done lazily inside the function, so we drive it via sys.modules.
# ---------------------------------------------------------------------------
class TestMoveFileToTrash:
    def _install_fake_send2trash(self, monkeypatch, impl):
        fake = types.ModuleType("send2trash")
        fake.send2trash = impl
        monkeypatch.setitem(sys.modules, "send2trash", fake)

    def test_success_when_file_actually_gone(self, monkeypatch, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"data")
        self._install_fake_send2trash(monkeypatch, lambda p: os.unlink(p))
        assert image_service.move_file_to_trash(str(f)) is None
        assert not f.exists()

    def test_raises_when_send2trash_silently_noops(self, monkeypatch, tmp_path):
        # The reported Windows failure: send2trash returns without raising but the
        # file is still there. Must surface as an error, never a fake success.
        f = tmp_path / "img.png"
        f.write_bytes(b"data")
        self._install_fake_send2trash(monkeypatch, lambda p: None)
        with pytest.raises(RuntimeError) as ei:
            image_service.move_file_to_trash(str(f))
        assert "still exists on disk" in str(ei.value)

    def test_rejects_empty_missing_and_directory(self, monkeypatch, tmp_path):
        self._install_fake_send2trash(monkeypatch, lambda p: None)
        with pytest.raises(RuntimeError, match="empty path"):
            image_service.move_file_to_trash("")
        with pytest.raises(RuntimeError, match="does not exist"):
            image_service.move_file_to_trash(str(tmp_path / "nope.png"))
        with pytest.raises(RuntimeError, match="Refusing to move directory"):
            image_service.move_file_to_trash(str(tmp_path))

    def test_missing_send2trash_dependency_is_a_clear_error(
        self, monkeypatch, tmp_path
    ):
        f = tmp_path / "img.png"
        f.write_bytes(b"data")
        # A None entry in sys.modules makes `from send2trash import ...` raise
        # ImportError, which the function must convert to a friendly RuntimeError.
        monkeypatch.setitem(sys.modules, "send2trash", None)
        with pytest.raises(RuntimeError, match="Trash support is not installed"):
            image_service.move_file_to_trash(str(f))


# ---------------------------------------------------------------------------
# 6. Per-item delete helper routes trash through the module-global seam.
# ---------------------------------------------------------------------------
class TestDeleteOneImageToTrash:
    def test_success_path_calls_module_global_trash_then_deletes_row(
        self, monkeypatch, tmp_path
    ):
        svc = ImageService()
        f = tmp_path / "x.png"
        f.write_bytes(b"x")
        monkeypatch.setattr(svc, "resolve_image_source_path", lambda _i, _p: str(f))
        trashed, deleted = [], []
        monkeypatch.setattr(
            image_service, "move_file_to_trash", lambda p: trashed.append(p)
        )
        monkeypatch.setattr(
            image_service.db, "delete_image", lambda i: deleted.append(i)
        )
        result = svc._delete_one_image_to_trash(
            7, {"filename": "x.png", "path": str(f)}
        )
        assert result == {"id": 7, "success": True, "filename": "x.png"}
        assert trashed == [str(f)]
        assert deleted == [7]

    def test_missing_image_returns_not_found_row(self):
        svc = ImageService()
        result = svc._delete_one_image_to_trash(7, None)
        assert result == {
            "id": 7,
            "success": False,
            "filename": None,
            "error": "Image not found",
        }


# ---------------------------------------------------------------------------
# 7. Delete-vs-remove id normalization asymmetry (documented invariant).
# ---------------------------------------------------------------------------
class TestIdNormalizationAsymmetry:
    def test_delete_normalization_keeps_nonpositive_ids(self):
        # _normalize_delete_ids intentionally does NOT drop <=0 (that is a
        # remove-from-gallery concern); it only int-casts + dedups in order.
        svc = ImageService()
        assert svc._normalize_delete_ids([3, "3", -1, 0, 2, 2]) == [3, -1, 0, 2]

    def test_remove_expansion_drops_nonpositive_ids(self):
        svc = ImageService()
        assert svc._expand_remove_request_ids([3, 3, -1, 0, 2], None) == [3, 2]


# ---------------------------------------------------------------------------
# 8. Cancel / reset idle+running branches for the two singleton jobs. Reset
#    raises 409 on a genuinely running job for BOTH: remove used to answer 200
#    with a dict instead, which read as success to any caller checking the
#    status code (see test_stuck_job_reset.py).
# ---------------------------------------------------------------------------
class TestCancelResetBranches:
    def test_cancel_delete_idle_is_noop(self):
        svc = ImageService()
        assert svc.cancel_delete() == {
            "status": "idle",
            "message": "No delete task is running",
        }

    def test_cancel_remove_idle_is_noop(self):
        svc = ImageService()
        assert svc.cancel_remove() == {
            "status": "idle",
            "message": "No remove in progress",
        }

    def test_reset_delete_idle_is_nothing_to_reset(self):
        svc = ImageService()
        assert svc.reset_delete_progress() == {
            "status": "idle",
            "message": "Nothing to reset",
        }

    def test_reset_delete_while_running_raises_409(self):
        svc = ImageService()
        svc._delete_progress["status"] = "running"
        with pytest.raises(HTTPException) as ei:
            svc.reset_delete_progress()
        assert ei.value.status_code == 409

    def test_reset_remove_idle_is_nothing_to_reset(self):
        svc = ImageService()
        assert svc.reset_remove_progress() == {
            "status": "idle",
            "message": "Nothing to reset",
        }

    def test_reset_remove_while_running_raises_409(self):
        # Matches delete: a running job is refused with 409, not a 200 dict.
        svc = ImageService()
        svc._remove_progress["status"] = "running"
        with pytest.raises(HTTPException) as ei:
            svc.reset_remove_progress()
        assert ei.value.status_code == 409

    def test_cancel_delete_running_sets_cancelling_and_event(self):
        import threading

        svc = ImageService()
        svc._delete_progress["status"] = "running"
        svc._delete_progress["current"] = 2
        svc._delete_progress["total"] = 5
        event = threading.Event()
        svc._delete_cancel_event = event
        out = svc.cancel_delete()
        assert out == {
            "status": "cancelling",
            "message": "Delete cancellation requested",
        }
        assert event.is_set()
        assert svc._delete_progress["status"] == "cancelling"


# ---------------------------------------------------------------------------
# 9. File-serving paths + the __file__ -> backend-root location contract.
# ---------------------------------------------------------------------------
class TestFileServingPaths:
    def test_resolve_image_source_path_returns_existing_absolute_path(self, tmp_path):
        svc = ImageService()
        f = tmp_path / "real.png"
        f.write_bytes(b"x")
        assert svc.resolve_image_source_path(1, str(f)) == str(f)

    def test_resolve_image_source_path_missing_raises_404(self, tmp_path):
        svc = ImageService()
        with pytest.raises(HTTPException) as ei:
            svc.resolve_image_source_path(1, str(tmp_path / "gone.png"))
        assert ei.value.status_code == 404

    def test_backend_file_argument_resolves_to_the_backend_root(self, monkeypatch):
        # THE decomposition trap: source_paths computes
        #   backend_root = dirname(dirname(abspath(backend_file)))
        # so whatever file resolves indexed paths must pass a backend_file whose
        # grandparent dir is `backend/`. Today that is image_service.py's
        # __file__. A naive split into services/image/*.py would pass a __file__
        # one level too deep and silently break relative/legacy-row resolution.
        svc = ImageService()
        captured = {}

        def fake_resolve(primary_path, *, backend_file, allow_symlink=False):
            captured["backend_file"] = backend_file
            return None

        monkeypatch.setattr(
            image_service, "resolve_existing_indexed_image_path", fake_resolve
        )
        with pytest.raises(HTTPException):
            svc.resolve_image_source_path(1, "legacy/relative/path.png")

        backend_file = captured["backend_file"]
        backend_root = os.path.dirname(os.path.dirname(os.path.abspath(backend_file)))
        assert os.path.basename(backend_root) == "backend"

    def test_filter_and_mark_keeps_live_rows(self, tmp_path, monkeypatch):
        svc = ImageService()
        f = tmp_path / "live.png"
        f.write_bytes(b"x")
        marked = []
        monkeypatch.setattr(
            image_service.db, "mark_image_unreadable", lambda *a, **k: marked.append(a)
        )
        live, missing = svc._filter_and_mark_missing_images([{"id": 1, "path": str(f)}])
        assert missing == 0
        assert [row["id"] for row in live] == [1]
        assert marked == []

    def test_filter_and_mark_flags_missing_rows(self, tmp_path, monkeypatch):
        svc = ImageService()
        marked = []
        monkeypatch.setattr(image_service.db, "get_image_by_id", lambda _i: None)
        monkeypatch.setattr(
            image_service.db,
            "mark_image_unreadable",
            lambda image_id, reason: marked.append((image_id, reason)),
        )
        live, missing = svc._filter_and_mark_missing_images(
            [{"id": 5, "path": str(tmp_path / "gone.png")}]
        )
        assert missing == 1
        assert live == []
        assert marked == [(5, "File not found on disk")]


# ---------------------------------------------------------------------------
# 10. Shared gallery-filter validation guards (400 surface).
# ---------------------------------------------------------------------------
class TestValidateCommonGalleryFilters:
    def test_invalid_sort_by_raises_400(self):
        svc = ImageService()
        with pytest.raises(HTTPException) as ei:
            svc._validate_common_gallery_filters(
                sort_by="sideways",
                aspect_ratio=None,
                min_width=None,
                max_width=None,
                min_height=None,
                max_height=None,
            )
        assert ei.value.status_code == 400

    def test_inverted_dimension_ranges_raise_400(self):
        svc = ImageService()
        with pytest.raises(HTTPException):
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio=None,
                min_width=800,
                max_width=100,
                min_height=None,
                max_height=None,
            )

    def test_brightness_bounds_and_range(self):
        svc = ImageService()
        with pytest.raises(HTTPException):
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio=None,
                min_width=None,
                max_width=None,
                min_height=None,
                max_height=None,
                brightness_min=-1,
            )
        with pytest.raises(HTTPException):
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio=None,
                min_width=None,
                max_width=None,
                min_height=None,
                max_height=None,
                brightness_min=200,
                brightness_max=100,
            )

    def test_invalid_color_temperature_and_distribution(self):
        svc = ImageService()
        with pytest.raises(HTTPException):
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio=None,
                min_width=None,
                max_width=None,
                min_height=None,
                max_height=None,
                color_temperature="lukewarm",
            )
        with pytest.raises(HTTPException):
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio=None,
                min_width=None,
                max_width=None,
                min_height=None,
                max_height=None,
                brightness_distribution="sideways",
            )

    def test_valid_filters_pass_silently(self):
        svc = ImageService()
        assert (
            svc._validate_common_gallery_filters(
                sort_by="newest",
                aspect_ratio="square",
                min_width=10,
                max_width=20,
                min_height=10,
                max_height=20,
                brightness_min=10,
                brightness_max=200,
                color_temperature="warm",
                brightness_distribution="balanced",
            )
            is None
        )


# ---------------------------------------------------------------------------
# 11. set_user_rating envelope shape (delegates validation to db.set_user_rating).
# ---------------------------------------------------------------------------
class TestSetUserRating:
    def test_returns_normalized_envelope(self, monkeypatch):
        svc = ImageService()
        monkeypatch.setattr(image_service.db, "set_user_rating", lambda i, s: True)
        assert svc.set_user_rating(4, 5) == {
            "image_id": 4,
            "user_rating": 5,
            "updated": True,
        }

    def test_reports_no_match(self, monkeypatch):
        svc = ImageService()
        monkeypatch.setattr(image_service.db, "set_user_rating", lambda i, s: False)
        assert svc.set_user_rating(999, 3) == {
            "image_id": 999,
            "user_rating": 3,
            "updated": False,
        }
