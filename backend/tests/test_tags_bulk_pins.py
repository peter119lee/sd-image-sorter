"""Characterization pins for routers/tags_bulk.py (decomposition step 0).

``backend/routers/tags_bulk.py`` (~1,109 lines) is the Mass-Tag-Editor ROUTER:
7 endpoints on ONE module-level ``APIRouter(prefix="/api/tags/bulk")`` plus two
module-level ``threading.Lock`` singletons. The existing behavior net
``test_routers/test_tags_bulk.py`` (39 tests) already covers the mutation
semantics end-to-end: 409-overlap rejection, pre-mutation ID snapshots, tag
provenance carry-through (migration 024), undo/redo journaling, four-op
atomic rollback, journal truncation warnings, and scope-estimate failure. This
file locks the load-bearing, currently-UNCOVERED *structural* seams a later
split into a ``routers/tags_bulk_parts/`` family (all registering on the SAME
``router``, routers/images precedent) must preserve, PLUS a compact
self-standing behavior layer (sections 7-8) that drives every endpoint
end-to-end so the pins stand on their own. It DELIBERATELY does NOT re-implement
the reader net's fault-injection rollback / journal-truncation matrix (that
would duplicate 15+ monkeypatch tests for no split-safety gain — deferred by
design). The structural seams:

1. Route-table identity — the exact (path, methods, name, endpoint) tuples in
   REGISTRATION order. Decorator order == OpenAPI order; a split that re-imports
   groups out of order silently reshuffles the schema. Pinned as both a readable
   literal and a single sha256 guard.
2. The ``router`` object itself — an ``APIRouter`` importable at
   ``routers.tags_bulk.router`` with prefix ``/api/tags/bulk`` and tag
   ``tags-bulk`` (main.py:365 mounts it by that attribute path).
3. Module constants + the mutable ``_op_state`` shape (BULK_TAG_MAX_IMAGE_IDS,
   BULK_TAG_ID_CHUNK_SIZE, VALID_PROMPT_MATCH_MODES, BulkWarningCode literals).
4. The two ``threading.Lock`` singletons (``_op_lock`` counter guard,
   ``_op_run_lock`` overlap gate) — real lock objects, distinct, stable identity.
5. The monkeypatch/import seam census — every symbol the reader suites patch on
   ``tags_bulk`` (BULK_TAG_ID_CHUNK_SIZE, _preserve_row, _estimate_scope_total,
   _op_run_lock, the ``db`` alias) must stay reachable at the module path, and
   ``tags_bulk.db is database`` so both patch spellings hit one object.
6. Request-model validation contracts (mutual-exclusion scope, tag
   normalization, confidence bounds, sort/prompt-mode validation) — asserted at
   the pydantic layer, no DB.
7. The per-operation response envelope key sets (four dry-run ops + /state +
   /ops + unknown-undo 404) — the JSON contract the frontend reads.

Behavior layer (HTTP, standard test_client temp DB, sections 7-8):
8. The request-validation contract at the HTTP boundary — empty / all-blank /
   oversized tag lists, missing-scope and multi-scope bodies, and out-of-range
   cleanup confidence all surface as the app's custom ``ValidationError``
   envelope with status 400 (main.py:380 remaps FastAPI's default 422). Neither
   the reader net nor the pydantic-layer pins above exercise this status seam.
9. A per-endpoint commit + undo-journal integration smoke — find-replace (incl.
   the two-tags-collapse-into-one MERGE path), bulk add, bulk remove, cleanup
   each mutate a scratch-DB row with ``dry_run=False``, journal an ``op_id``,
   and one apply/undo round-trip restores. This is the only self-standing
   proof each endpoint stays wired to a working handler after a split.

DORMANT BUG PINNED AS-IS (see test_tagmode_uppercase_rejected_...): ``tagMode``'s
field-level ``pattern="^(and|or)$"`` pre-empts the model_validator's ``.lower()``
normalization, so ``"OR"`` 422s while the sibling ``promptMatchMode`` accepts
``"CONTAINS"``. A known inconsistency, locked here so a split can't mask it.

Machine-state isolation: structural + model pins touch no DB and mutate no
global; HTTP pins use the standard ``test_client`` (its own temp DB). No real
data/images.db.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import get_args

import pytest
from fastapi import APIRouter
from pydantic import ValidationError

# conftest.py already inserts backend/ on sys.path, but the structural pins
# below import at module load; guard it so collection order can never matter.
sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import routers.tags_bulk as tb

# The full route surface today, in decorator/registration order. A pure
# decomposition MUST keep every tuple, in this order, on the shared ``router``.
EXPECTED_ROUTE_TABLE = [
    ("/api/tags/bulk/find-replace", ["POST"], "find_replace", "find_replace"),
    ("/api/tags/bulk/add", ["POST"], "bulk_add", "bulk_add"),
    ("/api/tags/bulk/remove", ["POST"], "bulk_remove", "bulk_remove"),
    ("/api/tags/bulk/cleanup", ["POST"], "cleanup", "cleanup"),
    ("/api/tags/bulk/state", ["GET"], "get_state", "get_state"),
    ("/api/tags/bulk/ops", ["GET"], "list_bulk_ops", "list_bulk_ops"),
    ("/api/tags/bulk/undo/{op_id}", ["POST"], "undo_bulk_op", "undo_bulk_op"),
]

# sha256 of the serialized table — a single canary that flips on ANY route
# add/remove/rename/reorder/method change. Recompute intentionally when the
# route surface is meant to change; never silently.
EXPECTED_ROUTE_TABLE_SHA256 = (
    "169aa35ab5c04ea7de414083c91d4232660eb2337caa3a615dca69bb3f45103f"
)


def _live_route_table():
    return [
        (route.path, sorted(route.methods), route.name, route.endpoint.__name__)
        for route in tb.router.routes
    ]


# ============================================================================
# 1. Route-table identity (structural, no DB)
# ============================================================================


class TestRouteTableIdentity:
    def test_route_table_literal_and_order(self):
        """Registration order == OpenAPI order; a split must preserve it exactly."""
        assert _live_route_table() == EXPECTED_ROUTE_TABLE

    def test_route_table_sha256_guard(self):
        """One value that flips on any route add/remove/rename/reorder."""
        blob = json.dumps(
            _live_route_table(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == EXPECTED_ROUTE_TABLE_SHA256

    def test_router_is_apirouter_with_prefix_and_tag(self):
        """main.py:365 mounts ``tags_bulk.router`` by this attribute path."""
        assert isinstance(tb.router, APIRouter)
        assert tb.router.prefix == "/api/tags/bulk"
        assert tb.router.tags == ["tags-bulk"]

    def test_every_route_is_single_method(self):
        """Each bulk route serves exactly one HTTP verb (no accidental multi-verb)."""
        for route in tb.router.routes:
            assert len(route.methods) == 1


# ============================================================================
# 2. Module constants + BulkWarningCode literals (structural, no DB)
# ============================================================================


class TestModuleConstants:
    def test_bulk_tag_scale_constants(self):
        assert tb.BULK_TAG_MAX_IMAGE_IDS == 1_000_000
        assert tb.BULK_TAG_ID_CHUNK_SIZE == 500

    def test_valid_prompt_match_modes(self):
        assert tb.VALID_PROMPT_MATCH_MODES == {"exact", "contains"}

    def test_bulk_warning_code_literals(self):
        """The undo-journal warning vocabulary is a closed set the FE branches on."""
        assert set(get_args(tb.BulkWarningCode)) == {
            "undo_journal_truncated",
            "undo_journal_persistence_failed",
        }


# ============================================================================
# 3. Lock statefulness (structural, no DB)
# ============================================================================


class TestLockStatefulness:
    def test_two_module_locks_are_real_distinct_lock_objects(self):
        lock_type = type(threading.Lock())
        assert isinstance(tb._op_lock, lock_type)
        assert isinstance(tb._op_run_lock, lock_type)
        assert tb._op_lock is not tb._op_run_lock

    def test_locks_are_stable_module_singletons(self):
        """A re-import must return the SAME lock objects — the 409 gate and the
        overlap-rejection reader depend on ``tags_bulk._op_run_lock`` identity."""
        import importlib

        reimported = importlib.import_module("routers.tags_bulk")
        assert reimported._op_lock is tb._op_lock
        assert reimported._op_run_lock is tb._op_run_lock

    def test_op_state_key_shape(self):
        """The mutable progress dict keeps a fixed key set (values vary at runtime)."""
        assert set(tb._op_state.keys()) == {
            "running",
            "operation",
            "total",
            "completed",
            "errors",
        }


# ============================================================================
# 4. Monkeypatch / import seam census (structural, no DB)
# ============================================================================


class TestPatchSurfaceCensus:
    # Symbols reader suites monkeypatch on the ``tags_bulk`` module object; a
    # split that relocates any of these without re-exporting breaks those tests.
    PATCHED_MODULE_ATTRS = (
        "BULK_TAG_ID_CHUNK_SIZE",
        "_preserve_row",
        "_estimate_scope_total",
        "_op_run_lock",
        "db",
    )

    # Endpoint functions the route table names by ``__name__`` — must exist so a
    # split keeps them importable/registerable.
    ENDPOINT_FUNCTIONS = (
        "find_replace",
        "bulk_add",
        "bulk_remove",
        "cleanup",
        "get_state",
        "list_bulk_ops",
        "undo_bulk_op",
    )

    # The private helpers that structure the four mutating flows; a split must
    # keep them reachable (either in place or re-exported).
    INTERNAL_HELPERS = (
        "_run_exclusive",
        "_begin_op",
        "_bump_op_progress",
        "_record_op_error",
        "_end_op",
        "_record_scope_estimate_failure",
        "_scope_source",
        "_estimate_scope_total_or_raise",
        "_iter_scope_id_chunks",
        "_filter_contract_db_kwargs",
        "_confidence_from_row",
        "_preserve_row",
        "_row_from_tuple",
        "_commit_tag_updates",
        "_bulk_tag_transaction",
        "_record_journal_if_applied",
        "_do_find_replace",
        "_do_bulk_add",
        "_do_bulk_remove",
        "_do_cleanup",
    )

    @pytest.mark.parametrize("name", PATCHED_MODULE_ATTRS)
    def test_reader_patched_attr_is_reachable(self, name):
        assert hasattr(tb, name)

    @pytest.mark.parametrize("name", ENDPOINT_FUNCTIONS)
    def test_endpoint_function_exists(self, name):
        assert callable(getattr(tb, name))

    @pytest.mark.parametrize("name", INTERNAL_HELPERS)
    def test_internal_helper_exists(self, name):
        assert callable(getattr(tb, name))

    def test_db_alias_is_the_real_database_module(self):
        """Endpoints reach SQL via the module-level ``db`` alias; readers patch
        both ``tags_bulk.db.get_image_tags_map`` and ``database.tag_update_
        transaction`` — pin they resolve to one object."""
        assert tb.db is db

    def test_request_models_are_importable(self):
        """The five request models are the API's typed boundary; a split keeps
        them on the module (or the endpoints lose their signatures)."""
        for name in (
            "BulkTagFilterContract",
            "BulkTagScopeRequest",
            "FindReplaceRequest",
            "BulkAddRequest",
            "BulkRemoveRequest",
            "CleanupRequest",
            "BulkUndoRequest",
        ):
            assert isinstance(getattr(tb, name), type)


# ============================================================================
# 5. Request-model validation contracts (pydantic layer, no DB)
# ============================================================================


class TestScopeRequestContracts:
    def test_zero_scope_is_rejected(self):
        with pytest.raises(ValidationError, match="One of image_ids"):
            tb.BulkAddRequest(tags=["a"])

    def test_more_than_one_scope_is_rejected(self):
        with pytest.raises(ValidationError, match="Provide only one"):
            tb.BulkAddRequest(tags=["a"], image_ids=[1], selection_token="x")

    def test_explicit_image_ids_dedupe_preserves_first_seen_order(self):
        request = tb.BulkAddRequest(tags=["a"], image_ids=[3, 1, 3, 2, 1])
        assert request.image_ids == [3, 1, 2]

    def test_selection_token_scope_accepted_alone(self):
        request = tb.BulkAddRequest(tags=["a"], selection_token="tok")
        assert request.selection_token == "tok"
        assert request.image_ids is None


class TestBulkAddModelContracts:
    def test_normalize_tags_strips_and_case_insensitively_dedupes(self):
        """First casing wins, order preserved — the preview stats and persisted
        rows both consume this normalized list."""
        request = tb.BulkAddRequest(image_ids=[1], tags=[" Foo ", "foo", "FOO", "Bar"])
        assert request.tags == ["Foo", "Bar"]

    def test_all_blank_tags_rejected(self):
        with pytest.raises(ValidationError):
            tb.BulkAddRequest(image_ids=[1], tags=["  ", ""])

    def test_tags_over_200_rejected(self):
        with pytest.raises(ValidationError):
            tb.BulkAddRequest(image_ids=[1], tags=[f"t{i}" for i in range(201)])

    def test_defaults(self):
        request = tb.BulkAddRequest(image_ids=[1], tags=["a"])
        assert request.confidence == 0.85
        assert request.dry_run is False


class TestOtherOpDefaults:
    def test_find_replace_defaults(self):
        request = tb.FindReplaceRequest(image_ids=[1], find="a", replace="b")
        assert (request.case_sensitive, request.regex, request.dry_run) == (
            False,
            False,
            False,
        )

    def test_bulk_remove_defaults(self):
        request = tb.BulkRemoveRequest(image_ids=[1], tags=["a"])
        assert (request.case_sensitive, request.dry_run) == (False, False)

    def test_bulk_undo_default_force_false(self):
        assert tb.BulkUndoRequest().force is False


class TestCleanupModelContracts:
    def test_defaults(self):
        request = tb.CleanupRequest(image_ids=[1])
        assert request.min_confidence == 0.20
        assert request.dedupe is True
        assert request.dry_run is False

    @pytest.mark.parametrize("bad", [1.5, -0.1])
    def test_min_confidence_is_bounded_to_unit_interval(self, bad):
        """v3.2.2: out-of-range confidence used to mean 'remove all' / silent
        no-op; it is now a hard 0.0..1.0 bound."""
        with pytest.raises(ValidationError):
            tb.CleanupRequest(image_ids=[1], min_confidence=bad)


class TestFilterContractContracts:
    def test_random_sort_rejected_for_bulk_scope(self):
        with pytest.raises(ValidationError, match="random sort cannot"):
            tb.BulkTagFilterContract(sortBy="random")

    def test_invalid_sort_rejected(self):
        with pytest.raises(ValidationError, match="Invalid sortBy"):
            tb.BulkTagFilterContract(sortBy="bogus")

    def test_prompt_match_mode_is_normalized(self):
        assert tb.BulkTagFilterContract(promptMatchMode="CONTAINS").promptMatchMode == (
            "contains"
        )

    def test_invalid_prompt_match_mode_rejected(self):
        with pytest.raises(ValidationError, match="must be exact or contains"):
            tb.BulkTagFilterContract(promptMatchMode="fuzzy")

    def test_empty_aspect_ratio_coerced_to_none(self):
        assert tb.BulkTagFilterContract(aspectRatio="").aspectRatio is None

    def test_excluded_image_ids_capped_at_10000(self):
        with pytest.raises(ValidationError):
            tb.BulkTagFilterContract(excludedImageIds=list(range(10001)))

    def test_lowercase_tag_mode_accepted(self):
        assert tb.BulkTagFilterContract(tagMode="or").tagMode == "or"
        assert tb.BulkTagFilterContract(tagMode="and").tagMode == "and"

    def test_tagmode_uppercase_rejected_while_prompt_mode_normalizes(self):
        """DORMANT INCONSISTENCY, pinned AS-IS.

        ``tagMode`` carries a field-level ``pattern="^(and|or)$"`` that runs
        DURING field validation and rejects ``"OR"`` before the
        ``model_validator(mode="after")``'s ``.strip().lower()`` can normalize
        it. The adjacent ``promptMatchMode`` has NO field pattern, so its
        identical-intent normalizer DOES lowercase ``"CONTAINS"``. Two sibling
        fields, same normalization comment, opposite behavior. Locked here so a
        decomposition cannot quietly change either side. A dormant bug pinned
        AS-IS; see the sibling promptMatchMode field for the fix options."""
        with pytest.raises(ValidationError):
            tb.BulkTagFilterContract(tagMode="OR")
        # Sibling field with the same intent behaves the opposite way:
        assert tb.BulkTagFilterContract(promptMatchMode="CONTAINS").promptMatchMode == (
            "contains"
        )


# ============================================================================
# 6. Response-envelope contracts (HTTP, standard test_client temp DB)
# ============================================================================


def _seed_image_with_tag(db_module, tmp_path, name, tag="original", confidence=0.9):
    image_path = tmp_path / name
    image_path.write_bytes(b"not a real image")
    image_id = db_module.add_image(path=str(image_path), filename=image_path.name)
    db_module.add_tags(image_id, [{"tag": tag, "confidence": confidence}])
    return image_id


DRY_RUN_ENVELOPES = [
    (
        "/api/tags/bulk/find-replace",
        {"find": "original", "replace": "renamed"},
        {
            "operation",
            "dry_run",
            "scope_source",
            "total_images_checked",
            "total_images_estimate",
            "affected_images",
            "affected_tags",
            "sample_changes",
            "find",
            "replace",
            "op_id",
            "undo_available",
            "warnings",
        },
    ),
    (
        "/api/tags/bulk/add",
        {"tags": ["extra_tag"]},
        {
            "operation",
            "dry_run",
            "scope_source",
            "total_images_checked",
            "total_images_estimate",
            "affected_images",
            "total_tags_added",
            "sample_changes",
            "tags_to_add",
            "op_id",
            "undo_available",
            "warnings",
        },
    ),
    (
        "/api/tags/bulk/remove",
        {"tags": ["original"]},
        {
            "operation",
            "dry_run",
            "scope_source",
            "total_images_checked",
            "total_images_estimate",
            "affected_images",
            "total_tags_removed",
            "sample_changes",
            "tags_to_remove",
            "op_id",
            "undo_available",
            "warnings",
        },
    ),
    (
        "/api/tags/bulk/cleanup",
        {"min_confidence": 0.5, "dedupe": True},
        {
            "operation",
            "dry_run",
            "scope_source",
            "total_images_checked",
            "total_images_estimate",
            "affected_images",
            "total_low_conf_removed",
            "total_duplicates_removed",
            "sample_changes",
            "min_confidence",
            "dedupe",
            "op_id",
            "undo_available",
            "warnings",
        },
    ),
]


@pytest.mark.parametrize("endpoint,fields,expected_keys", DRY_RUN_ENVELOPES)
def test_dry_run_response_envelope_key_set(
    endpoint, fields, expected_keys, test_client, tmp_path
):
    """Each op's JSON contract (the exact top-level key set) is what the FE
    reads; a split must not drop or rename a field."""
    import database as db_module

    safe = endpoint.rstrip("/").split("/")[-1]
    image_id = _seed_image_with_tag(db_module, tmp_path, f"env-{safe}.png")
    response = test_client.post(
        endpoint,
        json={"image_ids": [image_id], "dry_run": True, **fields},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == expected_keys
    assert payload["dry_run"] is True
    assert payload["scope_source"] == "image_ids"
    # A dry run is never journaled.
    assert payload["op_id"] is None
    assert payload["undo_available"] is False
    assert payload["warnings"] == []


def test_state_endpoint_envelope(test_client):
    """GET /state mirrors the module ``_op_state`` key set for the progress UI."""
    response = test_client.get("/api/tags/bulk/state")
    assert response.status_code == 200
    assert set(response.json().keys()) == {
        "running",
        "operation",
        "total",
        "completed",
        "errors",
    }


def test_ops_endpoint_envelope(test_client):
    """GET /ops returns a plain ``{"ops": [...]}`` envelope."""
    response = test_client.get("/api/tags/bulk/ops")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"ops"}
    assert isinstance(payload["ops"], list)


def test_undo_unknown_op_id_is_404(test_client):
    """An unknown journal id maps KeyError -> 404 (not a 500 leak)."""
    response = test_client.post(
        "/api/tags/bulk/undo/does-not-exist", json={"force": False}
    )
    assert response.status_code == 404
    assert "unknown bulk operation" in response.json()["error"].lower()


# ============================================================================
# 7. Request-validation status contract (HTTP, standard test_client temp DB)
# ============================================================================
#
# main.py:380's RequestValidationError handler remaps FastAPI's default 422 to
# a 400 ``{"error": "Invalid request parameters", "type": "ValidationError",
# "details": [...]}`` envelope. The reader net only exercises the *business*
# 400s raised inside endpoints (malformed token); the pydantic pins above assert
# rejection at the model layer but never cross the HTTP boundary. These lock the
# status + envelope shape a split that keeps the models must preserve.

HTTP_VALIDATION_CASES = [
    ("empty tags list (add)", "/api/tags/bulk/add", {"image_ids": [1], "tags": []}),
    (
        "all-blank tags (add)",
        "/api/tags/bulk/add",
        {"image_ids": [1], "tags": ["  ", ""]},
    ),
    (
        "oversized tags list (add)",
        "/api/tags/bulk/add",
        {"image_ids": [1], "tags": [f"t{i}" for i in range(201)]},
    ),
    (
        "empty tags list (remove)",
        "/api/tags/bulk/remove",
        {"image_ids": [1], "tags": []},
    ),
    ("missing scope (add)", "/api/tags/bulk/add", {"tags": ["x"]}),
    (
        "multiple scopes (add)",
        "/api/tags/bulk/add",
        {"tags": ["x"], "image_ids": [1], "selection_token": "tok"},
    ),
    (
        "confidence out of unit interval (cleanup)",
        "/api/tags/bulk/cleanup",
        {"image_ids": [1], "min_confidence": 1.5},
    ),
]


@pytest.mark.parametrize(
    "label,endpoint,body",
    HTTP_VALIDATION_CASES,
    ids=[case[0] for case in HTTP_VALIDATION_CASES],
)
def test_request_validation_surfaces_400_error_envelope(
    label, endpoint, body, test_client
):
    """Bad bodies never reach the DB; they map to the 400 ValidationError shape.

    Validation runs at the pydantic layer before any scope read, so the bogus
    ``image_ids=[1]`` never has to exist — no seeding required.
    """
    response = test_client.post(endpoint, json=body)
    assert response.status_code == 400
    payload = response.json()
    assert payload["type"] == "ValidationError"
    assert isinstance(payload["details"], list)
    assert payload["details"]


# ============================================================================
# 8. Per-endpoint commit + undo-journal integration (HTTP, temp DB)
# ============================================================================
#
# One self-standing proof per mutating endpoint: a committed op mutates the row,
# journals an op_id, and stays undoable. Kept minimal on purpose — the reader
# net owns the fault-injection rollback / truncation matrix.


def _seed_image_with_tags(db_module, tmp_path, name, rows):
    image_path = tmp_path / name
    image_path.write_bytes(b"not a real image")
    image_id = db_module.add_image(path=str(image_path), filename=image_path.name)
    db_module.add_tags(image_id, rows)
    return image_id


COMMIT_CASES = [
    (
        "/api/tags/bulk/find-replace",
        {"find": "old_name", "replace": "new_name"},
        [{"tag": "old_name", "confidence": 0.9}, {"tag": "keep", "confidence": 0.8}],
        {"new_name", "keep"},
    ),
    (
        "/api/tags/bulk/add",
        {"tags": ["fresh_tag"]},
        [{"tag": "keep", "confidence": 0.8}],
        {"keep", "fresh_tag"},
    ),
    (
        "/api/tags/bulk/remove",
        {"tags": ["drop_me"]},
        [{"tag": "drop_me", "confidence": 0.9}, {"tag": "keep", "confidence": 0.8}],
        {"keep"},
    ),
    (
        "/api/tags/bulk/cleanup",
        {"min_confidence": 0.5, "dedupe": True},
        [{"tag": "low_conf", "confidence": 0.1}, {"tag": "keep", "confidence": 0.8}],
        {"keep"},
    ),
]


@pytest.mark.parametrize(
    "endpoint,fields,seed_rows,expected_tags",
    COMMIT_CASES,
    ids=[case[0].rsplit("/", 1)[-1] for case in COMMIT_CASES],
)
def test_commit_mutates_db_and_journals_op(
    endpoint, fields, seed_rows, expected_tags, test_client, tmp_path
):
    """dry_run=False path: the row changes, the op is journaled, undo stays open."""
    import database as db_module

    safe = endpoint.rsplit("/", 1)[-1]
    image_id = _seed_image_with_tags(
        db_module, tmp_path, f"commit-{safe}.png", seed_rows
    )

    response = test_client.post(
        endpoint, json={"image_ids": [image_id], "dry_run": False, **fields}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is False
    assert payload["scope_source"] == "image_ids"
    assert payload["op_id"]  # a committed op is journaled
    assert payload["undo_available"] is True
    assert {row["tag"] for row in db_module.get_image_tags(image_id)} == expected_tags


def test_find_replace_merges_two_tags_into_one_existing(test_client, tmp_path):
    """Renaming one tag onto an existing sibling collapses via case-insensitive
    dedupe — the 'merge' operation, uncovered by the reader net."""
    import database as db_module

    image_id = _seed_image_with_tags(
        db_module,
        tmp_path,
        "merge.png",
        [{"tag": "cat", "confidence": 0.9}, {"tag": "dog", "confidence": 0.5}],
    )

    response = test_client.post(
        "/api/tags/bulk/find-replace",
        json={
            "image_ids": [image_id],
            "find": "dog",
            "replace": "cat",
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["affected_tags"] == 1
    # Two rows before, one row after: dog folded into the pre-existing cat.
    assert [row["tag"] for row in db_module.get_image_tags(image_id)] == ["cat"]


def test_apply_then_undo_restores_prior_tags(test_client, tmp_path):
    """The one apply/undo round-trip in this file — proves journal integration
    end-to-end without re-covering the reader net's conflict/force branches."""
    import database as db_module

    image_id = _seed_image_with_tags(
        db_module,
        tmp_path,
        "undo-roundtrip.png",
        [{"tag": "before_only", "confidence": 0.9}],
    )

    applied = test_client.post(
        "/api/tags/bulk/add",
        json={"image_ids": [image_id], "tags": ["added_by_bulk"], "dry_run": False},
    )
    assert applied.status_code == 200
    op_id = applied.json()["op_id"]
    assert op_id
    assert {row["tag"] for row in db_module.get_image_tags(image_id)} == {
        "before_only",
        "added_by_bulk",
    }

    undo = test_client.post(f"/api/tags/bulk/undo/{op_id}", json={})

    assert undo.status_code == 200
    assert undo.json()["restored"] == 1
    assert {row["tag"] for row in db_module.get_image_tags(image_id)} == {"before_only"}
