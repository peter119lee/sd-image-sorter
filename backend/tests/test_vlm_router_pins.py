"""Characterization pins for routers/vlm.py (decomposition step 0).

``backend/routers/vlm.py`` (~1,247 lines) is the VLM captioning ROUTER: 17
endpoints on ONE module-level ``APIRouter(prefix="/api/vlm", tags=["vlm"])``
(settings + connection test + single caption + a background *caption batch*
coordinator + Ollama local-model management), PLUS a nest of process-global
state that a later split into a ``routers/vlm_parts/`` family (all registering
on the SAME ``router``, routers/images + tags_bulk precedent) MUST preserve:

  * ``_batch_state`` (16-key mutable dict) + ``_batch_state_lock`` — the batch
    progress payload every ``/caption-batch/*`` endpoint reads/writes.
  * THREE rebind seams (module globals reassigned at runtime, ``global`` stmt):
      - ``_batch_task``  — strong ref to the fire-and-forget batch task; the
        event loop only weak-refs tasks, so losing this ref silently wedges
        ``running=True`` forever. Rebound by ``_set_batch_task`` /
        ``_on_batch_task_done``.
      - ``_pull_task``   — same, for the Ollama pull task.
      - ``_debug_chat_next_id`` — monotonic id source for the debug-chat ring
        buffer. Rebound by ``_append_debug_chat_event`` / ``_reset_debug_chat_events``.
  * ``_pull_state`` (4-key dict) — Ollama pull progress.
  * 12 module-owned classes: the persistence family (``_StoredVLMTagRow``,
    ``_PersistedVLMTagRow``, ``_VLMImageUpdate`` TypedDicts + ``_VLMPersistenceStore``
    Protocol + ``VLMResultPersistenceError``), ``_BatchImageSource`` (frozen
    dataclass), and 6 pydantic request models.

The existing behavior net ``test_routers/test_vlm.py`` (22 tests) already drives
the batch pipeline end-to-end: two-image ok/error progress, debug-chat redaction,
selection-token / filters snapshot sources, bounded gather, persistence-failure
rollback (single + batch), the unified-queue mutual-exclusion matrix, the
queued-dispatch entry point, slot release on source-resolution failure, and the
off-event-loop count resolution. This file locks the currently-UNCOVERED
*structural* seams a split must keep identical, plus a compact self-standing HTTP
layer so the pins stand on their own. It DELIBERATELY does NOT re-implement the
reader net's full batch-execution / persistence-rollback matrix (that would
duplicate ~15 monkeypatch tests for no split-safety gain — deferred by
design). The structural seams pinned here:

1. Route-table identity — the exact (path, sorted(methods), name, endpoint)
   tuples in REGISTRATION order. Decorator order == OpenAPI order; a split that
   re-imports groups out of order silently reshuffles the schema. Pinned as both
   a readable literal AND a single sha256 canary.
2. The ``router`` object — an ``APIRouter`` importable at ``routers.vlm.router``
   with prefix ``/api/vlm`` and tag ``vlm`` (main.py:363 mounts it by that path).
3. Module constants + the two mutable state-dict key sets + rebind-seam defaults.
4. The THREE rebind lifecycles, driven directly (fake tasks, no event loop):
   batch-task retain/crash-release, pull-task retain/crash-release, debug-chat id
   monotonicity + ring-buffer trim. Plus the claim/release slot lifecycle.
5. The cross-module + monkeypatch import-seam census — every symbol other
   services import from ``routers.vlm`` (the coordinator quartet + ``_build_config``)
   and every symbol the reader net patches on the module must stay reachable; and
   the ``count_selection_token_ids`` / ``iter_selection_token_id_chunks`` lazy
   seam lives on ``services.tag_export_service`` (NOT module-level on vlm) — the
   dtranslate module-level-name-check precedent.
6. Request-model validation contracts (source XOR, field bounds) — pydantic
   layer, no DB.
7. Config-building + endpoint-normalize + secret-redaction contracts — the parts
   of ``_build_config`` / ``_normalize_openai_endpoint`` / ``_redact_*`` /
   settings-secret-masking the reader net does not exercise (proxy + vertex
   round-trip, provider-gated /v1 padding, display-field stripping).

Behavior layer (HTTP, standard test_client temp DB, section 8):
8. Per-endpoint smoke + envelope key sets: read endpoints (/providers, /presets
   incl. krea2_long_nl, /caption-batch/progress + /debug-chat, /local-models/pull/
   progress), the no-endpoint 400s (/test, /models), caption 404, the caption-batch
   400 ValidationError envelope (main.py:380 remaps 422→400), and the empty-cancel
   400. This is the only self-standing proof each route stays wired after a split.

Machine-state isolation: structural + model pins touch no DB. Stateful pins run
under ``vlm_globals_guard`` (snapshots + restores ``_batch_state``,
``_pull_state``, ``_debug_chat_events``, ``_debug_chat_next_id``, ``_batch_task``,
``_pull_task`` in place, preserving object identity). Settings pins patch
``VLM_SETTINGS_PATH`` to a tmp file so the real ``data/config/vlm-settings.json``
is never read or written. No network, no Ollama probe, no models, no
``data/images.db``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi import APIRouter
from pydantic import ValidationError

# conftest.py already inserts backend/ on sys.path, but the structural pins
# below import at module load; guard it so collection order can never matter.
sys.path.insert(0, str(Path(__file__).parent.parent))

import routers.vlm as vlm
import services.tag_export_service as tag_export_service


# The full route surface today, in decorator/registration order. A pure
# decomposition MUST keep every tuple, in this order, on the shared ``router``.
EXPECTED_ROUTE_TABLE = [
    ("/api/vlm/providers", ["GET"], "get_providers", "get_providers"),
    (
        "/api/vlm/detect-provider",
        ["POST"],
        "detect_provider_endpoint",
        "detect_provider_endpoint",
    ),
    ("/api/vlm/presets", ["GET"], "get_presets", "get_presets"),
    ("/api/vlm/settings", ["GET"], "get_settings", "get_settings"),
    ("/api/vlm/settings", ["POST"], "save_settings", "save_settings"),
    ("/api/vlm/test", ["POST"], "test_connection", "test_connection"),
    (
        "/api/vlm/probe-concurrency",
        ["POST"],
        "probe_concurrency",
        "probe_concurrency",
    ),
    ("/api/vlm/models", ["POST"], "fetch_models", "fetch_models"),
    ("/api/vlm/caption", ["POST"], "caption_single", "caption_single"),
    ("/api/vlm/caption-batch", ["POST"], "caption_batch", "caption_batch"),
    ("/api/vlm/caption-batch/progress", ["GET"], "batch_progress", "batch_progress"),
    (
        "/api/vlm/caption-batch/debug-chat",
        ["GET"],
        "batch_debug_chat",
        "batch_debug_chat",
    ),
    ("/api/vlm/caption-batch/cancel", ["POST"], "batch_cancel", "batch_cancel"),
    (
        "/api/vlm/local-models/recommended",
        ["GET"],
        "get_recommended_models",
        "get_recommended_models",
    ),
    ("/api/vlm/local-models/pull", ["POST"], "pull_model", "pull_model"),
    ("/api/vlm/local-models/pull/progress", ["GET"], "pull_progress", "pull_progress"),
    ("/api/vlm/local-models/delete", ["POST"], "delete_model", "delete_model"),
    ("/api/vlm/local-models/start-ollama", ["POST"], "start_ollama", "start_ollama"),
]

# sha256 of the serialized table — a single canary that flips on ANY route
# add/remove/rename/reorder/method change. Recompute intentionally when the
# route surface is meant to change; never silently.
EXPECTED_ROUTE_TABLE_SHA256 = (
    "168f9030ce44a42e33415a960f170a0327e82a265be2ac33d155c7e9ed7208e3"
)


def _live_route_table():
    return [
        (route.path, sorted(route.methods), route.name, route.endpoint.__name__)
        for route in vlm.router.routes
    ]


# ============================================================================
# Global-state guard: snapshots + restores every mutable module global the
# stateful pins touch, IN PLACE (preserving object identity so external holders
# of _batch_state / _debug_chat_events keep seeing the same object).
# ============================================================================


@pytest.fixture
def vlm_globals_guard():
    saved_batch = dict(vlm._batch_state)
    saved_pull = dict(vlm._pull_state)
    saved_events = list(vlm._debug_chat_events)
    saved_next_id = vlm._debug_chat_next_id
    saved_batch_task = vlm._batch_task
    saved_pull_task = vlm._pull_task
    try:
        yield vlm
    finally:
        with vlm._batch_state_lock:
            vlm._batch_state.clear()
            vlm._batch_state.update(saved_batch)
            vlm._pull_state.clear()
            vlm._pull_state.update(saved_pull)
            vlm._debug_chat_events[:] = saved_events
            vlm._debug_chat_next_id = saved_next_id
        vlm._batch_task = saved_batch_task
        vlm._pull_task = saved_pull_task


class _FakeTask:
    """Stand-in for an asyncio.Task with just the surface the rebind seams read.

    ``_set_*_task`` calls ``add_done_callback``; the ``_on_*_task_done`` handlers
    read ``cancelled()`` and ``exception()``. No event loop required.
    """

    def __init__(
        self, *, cancelled: bool = False, exception: BaseException | None = None
    ):
        self._cancelled = cancelled
        self._exception = exception
        self.callbacks: list = []

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self):
        return self._exception

    def add_done_callback(self, cb) -> None:
        self.callbacks.append(cb)


# ============================================================================
# 1. Route-table identity (structural, no DB)
# ============================================================================


class TestRouteTableIdentity:
    def test_route_table_literal_and_order(self):
        """Registration order == OpenAPI order; a split must preserve it exactly."""
        assert _live_route_table() == EXPECTED_ROUTE_TABLE

    def test_route_table_sha256_guard(self):
        """One value that flips on any route add/remove/rename/reorder/method change."""
        blob = json.dumps(
            _live_route_table(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == EXPECTED_ROUTE_TABLE_SHA256

    def test_router_is_apirouter_with_prefix_and_tag(self):
        """main.py:363 mounts ``vlm.router`` by this attribute path."""
        assert isinstance(vlm.router, APIRouter)
        assert vlm.router.prefix == "/api/vlm"
        assert vlm.router.tags == ["vlm"]

    def test_every_route_is_single_method(self):
        """Each vlm route serves exactly one HTTP verb (the two /settings routes
        are two DISTINCT single-verb registrations, GET + POST)."""
        for route in vlm.router.routes:
            assert len(route.methods) == 1

    def test_route_count_is_eighteen(self):
        assert len(vlm.router.routes) == 18


# ============================================================================
# 2. Module constants + state-dict shapes (structural, no DB)
# ============================================================================


class TestModuleConstantsAndStateShape:
    def test_scalar_constants(self):
        assert vlm._DEBUG_CHAT_LIMIT == 80
        assert vlm._BATCH_ID_CHUNK_SIZE == 500

    def test_settings_path_is_under_config_dir(self):
        """A split must keep this pointing at CONFIG_DIR/vlm-settings.json —
        _load/_save read this module global by name."""
        assert vlm.VLM_SETTINGS_PATH.name == "vlm-settings.json"
        assert vlm.VLM_SETTINGS_PATH.parent == vlm.CONFIG_DIR

    def test_batch_state_key_set(self):
        """The progress dict keeps a fixed 16-key shape (values vary at runtime);
        every /caption-batch/* endpoint and the reader net depend on it."""
        assert set(vlm._batch_state.keys()) == {
            "running",
            "cancel_requested",
            "total",
            "completed",
            "failed",
            "tokens_used",
            "errors",
            "current_image",
            "active_requests",
            "api_status",
            "api_message",
            "api_ok",
            "api_error",
            "last_api_latency_ms",
            "last_api_error",
            "output_format",
        }

    def test_batch_state_default_output_format(self):
        assert vlm._batch_state["output_format"] == "nl_caption"

    def test_pull_state_key_set_and_defaults(self):
        assert set(vlm._pull_state.keys()) == {"pulling", "model", "percent", "status"}


# ============================================================================
# 3. Rebind-seam statefulness — the three module-global reassignments
#    (structural + unit behavior, driven with fake tasks; no event loop)
# ============================================================================


class TestBatchTaskRebindSeam:
    def test_batch_state_lock_is_real_stable_singleton(self):
        import importlib

        lock_type = type(threading.Lock())
        assert isinstance(vlm._batch_state_lock, lock_type)
        reimported = importlib.import_module("routers.vlm")
        assert reimported._batch_state_lock is vlm._batch_state_lock

    def test_set_batch_task_retains_ref_and_registers_done_callback(
        self, vlm_globals_guard
    ):
        task = _FakeTask()
        vlm._set_batch_task(task)
        assert vlm._batch_task is task
        assert vlm._on_batch_task_done in task.callbacks

    def test_set_batch_task_none_clears_ref(self, vlm_globals_guard):
        vlm._set_batch_task(_FakeTask())
        vlm._set_batch_task(None)
        assert vlm._batch_task is None

    def test_batch_task_crash_releases_running_flag(self, vlm_globals_guard):
        """A batch task that dies before its own finally must not wedge running=True."""
        with vlm._batch_state_lock:
            vlm._batch_state["running"] = True
        vlm._on_batch_task_done(_FakeTask(exception=RuntimeError("boom")))
        assert vlm._batch_task is None
        assert vlm._batch_state["running"] is False
        assert vlm._batch_state["api_status"] == "error"
        assert vlm._batch_state["last_api_error"] == "boom"

    def test_batch_task_clean_completion_leaves_running_untouched(
        self, vlm_globals_guard
    ):
        """Normal completion early-returns: _run_batch's own finally owns the flag,
        the callback only handles the crash path — it must NOT flip running."""
        with vlm._batch_state_lock:
            vlm._batch_state["running"] = True
            vlm._batch_state["api_status"] = "done"
        vlm._on_batch_task_done(_FakeTask(cancelled=False, exception=None))
        assert vlm._batch_task is None
        assert vlm._batch_state["running"] is True
        assert vlm._batch_state["api_status"] == "done"

    def test_cancelled_task_with_running_flag_is_labeled_error_not_cancelled(
        self, vlm_globals_guard
    ):
        """DORMANT QUIRK, pinned AS-IS.

        The early-return guard is ``if exc is None and not task.cancelled()``, so
        a CANCELLED task (exc is None, but cancelled() is True) does NOT return
        early — it falls through to the crash-release block and, when running is
        still True, stamps ``api_status='error'`` /
        ``api_message='VLM batch task stopped unexpectedly'`` rather than a
        'cancelled' status. In practice ``_run_batch``'s finally sets
        running=False before the task settles, so the ``if running`` guard is
        usually False and this is inert — hence dormant. Locked so a split cannot
        silently change either the guard or the label. A dormant bug pinned
        AS-IS, not desired behaviour."""
        with vlm._batch_state_lock:
            vlm._batch_state["running"] = True
        vlm._on_batch_task_done(_FakeTask(cancelled=True, exception=None))
        assert vlm._batch_state["running"] is False
        assert vlm._batch_state["api_status"] == "error"
        assert vlm._batch_state["api_message"] == "VLM batch task stopped unexpectedly"


class TestPullTaskRebindSeam:
    def test_set_pull_task_retains_ref_and_registers_callback(self, vlm_globals_guard):
        task = _FakeTask()
        vlm._set_pull_task(task)
        assert vlm._pull_task is task
        assert vlm._on_pull_task_done in task.callbacks

    def test_pull_task_crash_clears_pulling_and_sets_error(self, vlm_globals_guard):
        vlm._pull_state["pulling"] = True
        vlm._on_pull_task_done(_FakeTask(exception=RuntimeError("pull boom")))
        assert vlm._pull_task is None
        assert vlm._pull_state["pulling"] is False
        assert vlm._pull_state["status"] == "error: pull boom"

    def test_pull_task_clean_completion_leaves_pulling_untouched(
        self, vlm_globals_guard
    ):
        vlm._pull_state["pulling"] = True
        vlm._on_pull_task_done(_FakeTask(cancelled=False, exception=None))
        assert vlm._pull_task is None
        assert vlm._pull_state["pulling"] is True


class TestDebugChatIdRebindSeam:
    def test_reset_then_append_ids_start_at_one_and_increment(self, vlm_globals_guard):
        vlm._reset_debug_chat_events()
        first = vlm._append_debug_chat_event({"phase": "request"})
        second = vlm._append_debug_chat_event({"phase": "response"})
        assert (first, second) == (1, 2)
        assert vlm._debug_chat_events[0]["id"] == 1
        assert "at" in vlm._debug_chat_events[0]
        assert vlm._debug_chat_events[0]["phase"] == "request"

    def test_ring_buffer_trims_to_limit_but_ids_stay_monotonic(self, vlm_globals_guard):
        vlm._reset_debug_chat_events()
        last_id = 0
        for _ in range(vlm._DEBUG_CHAT_LIMIT + 5):
            last_id = vlm._append_debug_chat_event({"phase": "request"})
        assert last_id == vlm._DEBUG_CHAT_LIMIT + 5
        assert len(vlm._debug_chat_events) == vlm._DEBUG_CHAT_LIMIT
        # Oldest 5 were dropped; ids remain contiguous and monotonic.
        assert vlm._debug_chat_events[0]["id"] == 6
        assert vlm._debug_chat_events[-1]["id"] == vlm._DEBUG_CHAT_LIMIT + 5


class TestCaptionBatchSlotLifecycle:
    def test_claim_sets_running_and_second_claim_conflicts(self, vlm_globals_guard):
        from fastapi import HTTPException

        with vlm._batch_state_lock:
            vlm._batch_state["running"] = False
        vlm.claim_caption_batch_slot()
        assert vlm._batch_state["running"] is True
        assert vlm._batch_state["cancel_requested"] is False
        assert vlm.is_caption_batch_active() is True
        with pytest.raises(HTTPException) as excinfo:
            vlm.claim_caption_batch_slot()
        assert excinfo.value.status_code == 409

    def test_release_clears_running_and_records_error(self, vlm_globals_guard):
        with vlm._batch_state_lock:
            vlm._batch_state["running"] = True
        vlm.release_caption_batch_slot(error="bad start")
        assert vlm._batch_state["running"] is False
        assert vlm._batch_state["api_status"] == "error"
        assert vlm._batch_state["last_api_error"] == "bad start"


# ============================================================================
# 4. Cross-module + monkeypatch import-seam census (structural, no DB)
# ============================================================================


class TestImportSeamCensus:
    # Public functions OTHER services import from ``routers.vlm``. A split MUST
    # keep every one reachable at ``routers.vlm.*`` or those importers break.
    #   - coordinator quartet: services/tagging_pipeline_service.py
    #   - _build_config: dataset_translate_service, smart_tag/pipeline, smart_tag/request
    CROSS_MODULE_PUBLIC_SEAMS = (
        "is_caption_batch_active",
        "claim_caption_batch_slot",
        "release_caption_batch_slot",
        "start_caption_batch_from_queue",
        "_build_config",
    )

    # Every endpoint the route table names by ``__name__``.
    ENDPOINT_FUNCTIONS = (
        "get_providers",
        "detect_provider_endpoint",
        "get_presets",
        "get_settings",
        "save_settings",
        "test_connection",
        "fetch_models",
        "caption_single",
        "caption_batch",
        "batch_progress",
        "batch_debug_chat",
        "batch_cancel",
        "get_recommended_models",
        "pull_model",
        "pull_progress",
        "delete_model",
        "start_ollama",
    )

    # Symbols the reader net (test_routers/test_vlm.py) monkeypatches on the
    # ``vlm`` module object; a split that relocates any without re-exporting
    # breaks those tests silently.
    READER_PATCHED_ATTRS = (
        "get_provider",
        "_build_config",
        "_load_vlm_settings",
        "_save_vlm_settings",
        "_run_batch",
        "_build_batch_image_source",
        "resolve_existing_indexed_image_path",
        "_persist_vlm_result",
        "_persist_tags",
        "asyncio",
        "_batch_state",
        "_batch_state_lock",
        "VLMConfig",
        "BatchCaptionRequest",
        "_BatchImageSource",
        "_build_debug_request_event",
    )

    # The 12 module-owned classes (persistence family + dataclass + request models).
    MODULE_CLASSES = (
        "_StoredVLMTagRow",
        "_PersistedVLMTagRow",
        "_VLMImageUpdate",
        "_VLMPersistenceStore",
        "VLMResultPersistenceError",
        "_BatchImageSource",
        "DetectProviderRequest",
        "SaveSettingsRequest",
        "CaptionSingleRequest",
        "BatchCaptionRequest",
        "PullModelRequest",
        "DeleteModelRequest",
    )

    # Private helpers structuring the batch/persistence/debug flows; a split
    # must keep them reachable (in place or re-exported).
    INTERNAL_HELPERS = (
        "_load_vlm_settings",
        "_save_vlm_settings",
        "_utc_now_iso",
        "_redact_debug_text",
        "_redact_debug_endpoint",
        "_coerce_int_setting",
        "_coerce_float_setting",
        "_resolve_image_path",
        "_append_debug_chat_event",
        "_build_debug_request_event",
        "_append_debug_response_event",
        "_reset_debug_chat_events",
        "_normalize_openai_endpoint",
        "_build_config",
        "_persist_vlm_result",
        "_persist_tags",
        "_iter_image_id_chunks",
        "_filters_to_selection_kwargs",
        "_create_selection_token_from_filters",
        "_build_batch_image_source",
        "_start_claimed_caption_batch",
        "_run_batch",
        "_record_error",
        "_set_batch_task",
        "_on_batch_task_done",
        "_set_pull_task",
        "_on_pull_task_done",
        "_do_pull",
    )

    @pytest.mark.parametrize("name", CROSS_MODULE_PUBLIC_SEAMS)
    def test_cross_module_public_seam_reachable(self, name):
        assert callable(getattr(vlm, name))

    @pytest.mark.parametrize("name", ENDPOINT_FUNCTIONS)
    def test_endpoint_function_exists(self, name):
        assert callable(getattr(vlm, name))

    @pytest.mark.parametrize("name", READER_PATCHED_ATTRS)
    def test_reader_patched_attr_is_reachable(self, name):
        assert hasattr(vlm, name)

    @pytest.mark.parametrize("name", MODULE_CLASSES)
    def test_module_class_is_importable(self, name):
        assert isinstance(getattr(vlm, name), type)

    @pytest.mark.parametrize("name", INTERNAL_HELPERS)
    def test_internal_helper_exists(self, name):
        assert callable(getattr(vlm, name))

    def test_selection_token_helpers_live_on_tag_export_service_not_vlm(self):
        """DTRANSLATE MODULE-LEVEL-NAME-CHECK precedent.

        ``_build_batch_image_source`` imports ``count_selection_token_ids`` and
        ``iter_selection_token_id_chunks`` LAZILY from
        ``services.tag_export_service`` (product lines ~722, ~741). The reader
        net patches them THERE, on that module — NOT on ``routers.vlm`` (they are
        deliberately not module-level names on vlm). A split must keep this seam
        pointing at tag_export_service; if vlm ever hoists these to module-level
        imports the reader's patches would silently stop taking effect."""
        assert hasattr(tag_export_service, "count_selection_token_ids")
        assert hasattr(tag_export_service, "iter_selection_token_id_chunks")
        assert not hasattr(vlm, "count_selection_token_ids")
        assert not hasattr(vlm, "iter_selection_token_id_chunks")

    def test_provider_registry_symbols_are_module_level_on_vlm(self):
        """``get_provider`` / ``list_providers`` / ``detect_provider`` /
        ``PROMPT_PRESETS`` are imported at module top, so the reader net patches
        them on ``vlm`` directly — a split must keep them module-level."""
        assert callable(vlm.get_provider)
        assert callable(vlm.list_providers)
        assert callable(vlm.detect_provider)
        assert isinstance(vlm.PROMPT_PRESETS, dict)


# ============================================================================
# 5. Request-model validation contracts (pydantic layer, no DB)
# ============================================================================


class TestBatchCaptionRequestContracts:
    def test_zero_sources_rejected(self):
        with pytest.raises(ValidationError, match="Either image_ids"):
            vlm.BatchCaptionRequest()

    def test_more_than_one_source_rejected(self):
        with pytest.raises(ValidationError, match="Provide only one"):
            vlm.BatchCaptionRequest(image_ids=[1], selection_token="tok")

    def test_image_ids_alone_accepted(self):
        request = vlm.BatchCaptionRequest(image_ids=[1, 2])
        assert request.image_ids == [1, 2]
        assert request.selection_token is None
        assert request.filters is None

    def test_selection_token_alone_accepted(self):
        request = vlm.BatchCaptionRequest(selection_token="tok")
        assert request.selection_token == "tok"

    def test_empty_filters_dict_counts_as_a_source(self):
        """``filters is not None`` is the source test, so an empty dict is a
        valid (whole-library) source — pinned AS-IS."""
        request = vlm.BatchCaptionRequest(filters={})
        assert request.filters == {}

    def test_empty_selection_token_rejected_by_min_length(self):
        with pytest.raises(ValidationError):
            vlm.BatchCaptionRequest(selection_token="")


class TestOtherRequestModelContracts:
    def test_caption_single_requires_image_id(self):
        with pytest.raises(ValidationError):
            vlm.CaptionSingleRequest()

    def test_caption_single_tags_default_none(self):
        assert vlm.CaptionSingleRequest(image_id=5).tags is None

    def test_detect_provider_requires_endpoint(self):
        with pytest.raises(ValidationError):
            vlm.DetectProviderRequest()

    def test_pull_and_delete_require_model(self):
        with pytest.raises(ValidationError):
            vlm.PullModelRequest()
        with pytest.raises(ValidationError):
            vlm.DeleteModelRequest()


class TestSaveSettingsBounds:
    @pytest.mark.parametrize(
        "field,bad",
        [
            ("max_retries", 11),
            ("max_retries", -1),
            ("concurrent_requests", 0),
            ("concurrent_requests", 17),
            ("timeout_seconds", 0),
            ("timeout_seconds", 601),
            ("max_image_size", 127),
            ("max_image_size", 4097),
            ("caption_max_tokens", 63),
            ("caption_max_tokens", 8193),
            ("caption_temperature", -0.1),
            ("caption_temperature", 2.1),
        ],
    )
    def test_out_of_range_field_rejected(self, field, bad):
        with pytest.raises(ValidationError):
            vlm.SaveSettingsRequest(**{field: bad})

    def test_all_fields_optional_none_default(self):
        """An empty body is valid: save_settings merges only non-None updates."""
        request = vlm.SaveSettingsRequest()
        assert request.provider is None
        assert request.concurrent_requests is None


# ============================================================================
# 6. Config-building, endpoint-normalize, redaction contracts (unit, no DB)
# ============================================================================


class TestNormalizeOpenAIEndpoint:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://aihubmix.com", "https://aihubmix.com/v1"),
            ("https://aihubmix.com/", "https://aihubmix.com/v1"),
            ("https://gw.test/v1", "https://gw.test/v1"),
            ("https://gw.test/openai/v1", "https://gw.test/openai/v1"),
            ("https://gw.test/api/proxy", "https://gw.test/api/proxy"),
            ("not a url", "not a url"),
            ("", ""),
        ],
    )
    def test_padding_rules(self, url, expected):
        assert vlm._normalize_openai_endpoint(url) == expected


class TestBuildConfigMapping:
    def _patch_settings(self, monkeypatch, settings):
        monkeypatch.setattr(vlm, "_load_vlm_settings", lambda: dict(settings))

    def test_defaults_when_settings_empty(self, monkeypatch):
        self._patch_settings(monkeypatch, {})
        config = vlm._build_config()
        assert config.provider == "openai_compat"
        assert config.output_format == "nl_caption"
        assert config.concurrent_requests == 2
        assert config.max_retries == 3
        assert config.include_tags_as_context is True
        assert config.vertex_location == "us-central1"

    def test_proxy_fields_round_trip(self, monkeypatch):
        self._patch_settings(
            monkeypatch,
            {
                "http_proxy": "http://p:1",
                "https_proxy": "https://p:2",
                "socks_proxy": "socks5://p:3",
            },
        )
        config = vlm._build_config()
        assert config.http_proxy == "http://p:1"
        assert config.https_proxy == "https://p:2"
        assert config.socks_proxy == "socks5://p:3"

    def test_vertex_fields_round_trip(self, monkeypatch):
        self._patch_settings(
            monkeypatch,
            {
                "use_vertex": True,
                "vertex_project": "proj-x",
                "vertex_location": "europe-west4",
                "service_account_json": '{"k":"v"}',
            },
        )
        config = vlm._build_config()
        assert config.use_vertex is True
        assert config.vertex_project == "proj-x"
        assert config.vertex_location == "europe-west4"
        assert config.service_account_json == '{"k":"v"}'

    def test_openai_compat_endpoint_is_v1_padded(self, monkeypatch):
        self._patch_settings(
            monkeypatch, {"provider": "openai_compat", "endpoint": "https://gw.test"}
        )
        assert vlm._build_config().endpoint == "https://gw.test/v1"

    def test_non_openai_compat_endpoint_is_not_padded(self, monkeypatch):
        """The /v1 auto-pad is gated on ``provider == 'openai_compat'``; an
        anthropic/gemini endpoint is passed through verbatim."""
        self._patch_settings(
            monkeypatch,
            {"provider": "anthropic", "endpoint": "https://api.anthropic.test"},
        )
        assert vlm._build_config().endpoint == "https://api.anthropic.test"

    def test_overrides_win_and_none_overrides_ignored(self, monkeypatch):
        self._patch_settings(
            monkeypatch, {"model": "base-model", "max_image_size": 512}
        )
        config = vlm._build_config({"model": "override-model", "max_image_size": None})
        assert config.model == "override-model"
        # None override is dropped, so the stored value survives.
        assert config.max_image_size == 512


class TestRedaction:
    def test_redact_endpoint_strips_userinfo_query_fragment(self):
        redacted = vlm._redact_debug_endpoint(
            "https://user:secret@example.test/v1/chat?token=abc#frag"
        )
        assert redacted == "https://example.test/v1/chat?..."
        assert "secret" not in redacted
        assert "token=abc" not in redacted
        assert "frag" not in redacted

    def test_redact_endpoint_empty_is_empty(self):
        assert vlm._redact_debug_endpoint("") == ""
        assert vlm._redact_debug_endpoint(None) == ""

    def test_redact_endpoint_non_url_drops_query_tail(self):
        """No scheme/netloc → the fallback strips at '?' and truncates."""
        assert vlm._redact_debug_endpoint("bareword?token=secret") == "bareword"

    def test_redact_text_truncates_over_limit(self):
        out = vlm._redact_debug_text("x" * 20, limit=5)
        assert out.startswith("xxxxx")
        assert "truncated 15 chars" in out

    def test_redact_text_none_is_empty(self):
        assert vlm._redact_debug_text(None) == ""

    def test_debug_request_event_never_leaks_key_and_caps_tags(self):
        """The request event carries a redacted endpoint and caps the tag list at
        120 while keeping the true count — no api_key field is ever present."""
        event = vlm._build_debug_request_event(
            image_id=1,
            image_name="s.png",
            config=vlm.VLMConfig(
                endpoint="https://user:sekret@ex.test/v1?token=zzz",
                api_key="sk-should-not-appear",
                model="m",
                system_prompt="sys",
            ),
            provider_name="fake",
            tags=[f"t{i}" for i in range(200)],
            user_message="describe",
        )
        assert "api_key" not in event
        assert "sk-should-not-appear" not in json.dumps(event)
        assert "sekret" not in event["endpoint"]
        assert len(event["tags"]) == 120
        assert event["tags_count"] == 200


# ============================================================================
# 7. Settings secret-masking round-trip (real _load/_save via tmp path, no DB)
# ============================================================================


class TestSettingsSecretMasking:
    def test_save_strips_display_fields_and_get_masks_secrets(
        self, tmp_path, monkeypatch
    ):
        """_save_vlm_settings drops the *_display fields before writing; the file
        never persists raw display markers, and get_settings masks api_key +
        service_account_json so raw secrets never leave the process."""
        settings_path = tmp_path / "vlm-settings.json"
        monkeypatch.setattr(vlm, "VLM_SETTINGS_PATH", settings_path)

        vlm._save_vlm_settings(
            {
                "provider": "openai_compat",
                "api_key": "sk-abcdefgh-secret",
                "service_account_json": '{"private":"key"}',
                "api_key_display": "should-not-persist",
                "service_account_json_display": "should-not-persist",
            }
        )
        on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "api_key_display" not in on_disk
        assert "service_account_json_display" not in on_disk
        assert on_disk["api_key"] == "sk-abcdefgh-secret"

    def test_get_settings_masks_via_http(self, test_client, tmp_path, monkeypatch):
        import routers.vlm as vlm_router

        settings_path = tmp_path / "vlm-settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "provider": "openai_compat",
                    "model": "m",
                    "api_key": "sk-abcdefgh-secret",
                    "service_account_json": '{"private":"key"}',
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(vlm_router, "VLM_SETTINGS_PATH", settings_path)

        payload = test_client.get("/api/vlm/settings").json()
        assert "api_key" not in payload
        assert payload["api_key_display"] == "sk-abcde***"
        assert "service_account_json" not in payload
        assert payload["service_account_json_display"] == "*** (configured)"
        assert payload["model"] == "m"


# ============================================================================
# 8. HTTP endpoint smokes + envelope key sets (standard test_client temp DB)
# ============================================================================


def test_providers_endpoint_lists_three_providers(test_client):
    payload = test_client.get("/api/vlm/providers").json()
    ids = {p["id"] for p in payload["providers"]}
    assert ids == {"openai_compat", "anthropic", "gemini"}


def test_presets_endpoint_includes_krea2_long_nl(test_client):
    """The krea2_long_nl preset (4ac11d5) is served verbatim by /presets; a split
    must keep the endpoint wired to vlm_providers.PROMPT_PRESETS."""
    payload = test_client.get("/api/vlm/presets").json()
    presets = payload["presets"]
    assert "krea2_long_nl" in presets
    krea2 = presets["krea2_long_nl"]
    assert krea2["output_format"] == "nl_caption"
    assert set(krea2.keys()) >= {
        "name",
        "output_format",
        "system_prompt",
        "user_prompt",
        "user_prompt_with_tags",
    }


def test_detect_provider_endpoint_returns_provider(test_client):
    resp = test_client.post(
        "/api/vlm/detect-provider", json={"endpoint": "https://api.openai.com/v1"}
    )
    assert resp.status_code == 200
    assert "provider" in resp.json()


def test_caption_batch_progress_envelope(test_client):
    """/progress returns the full _batch_state key set PLUS pipeline_queue."""
    payload = test_client.get("/api/vlm/caption-batch/progress").json()
    assert set(payload.keys()) == set(vlm._batch_state.keys()) | {"pipeline_queue"}


def test_debug_chat_envelope(test_client):
    payload = test_client.get("/api/vlm/caption-batch/debug-chat").json()
    assert set(payload.keys()) == {"events", "limit", "running"}
    assert payload["limit"] == vlm._DEBUG_CHAT_LIMIT
    assert isinstance(payload["events"], list)


def test_pull_progress_envelope(test_client):
    """A pure read of _pull_state — no Ollama probe, safe to hit directly."""
    payload = test_client.get("/api/vlm/local-models/pull/progress").json()
    assert set(payload.keys()) == {"pulling", "model", "percent", "status"}


def test_test_connection_no_endpoint_is_400(test_client, tmp_path, monkeypatch):
    import routers.vlm as vlm_router

    monkeypatch.setattr(vlm_router, "VLM_SETTINGS_PATH", tmp_path / "absent.json")
    resp = test_client.post("/api/vlm/test")
    assert resp.status_code == 400
    assert "No endpoint configured" in resp.json()["error"]


def test_fetch_models_no_endpoint_is_400(test_client, tmp_path, monkeypatch):
    import routers.vlm as vlm_router

    monkeypatch.setattr(vlm_router, "VLM_SETTINGS_PATH", tmp_path / "absent.json")
    resp = test_client.post("/api/vlm/models")
    assert resp.status_code == 400
    assert "No endpoint configured" in resp.json()["error"]


def test_caption_single_missing_image_is_404(test_client):
    resp = test_client.post("/api/vlm/caption", json={"image_id": 9_999_999})
    assert resp.status_code == 404
    assert resp.json()["error"] == "Image not found"


CAPTION_BATCH_VALIDATION_CASES = [
    ("no source", {}),
    ("multiple sources", {"image_ids": [1], "selection_token": "tok"}),
]


@pytest.mark.parametrize(
    "label,body",
    CAPTION_BATCH_VALIDATION_CASES,
    ids=[case[0] for case in CAPTION_BATCH_VALIDATION_CASES],
)
def test_caption_batch_bad_source_is_400_validation_envelope(label, body, test_client):
    """main.py:380 remaps FastAPI's 422 to a 400 ValidationError envelope; the
    BatchCaptionRequest source XOR is rejected before any provider/DB access."""
    resp = test_client.post("/api/vlm/caption-batch", json=body)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["type"] == "ValidationError"
    assert isinstance(payload["details"], list)
    assert payload["details"]


def test_caption_batch_cancel_with_nothing_running_is_400(test_client):
    resp = test_client.post("/api/vlm/caption-batch/cancel")
    assert resp.status_code == 400
    assert "No batch in progress" in resp.json()["error"]
