"""Characterization pins for TaggingService state machinery (god-file redesign, step 0).

Companion to test_tagging_pins.py — this file pins the TaggingService seams a
decomposition could silently change: progress get/set/proxy coercion,
reset/cancel edge cells, worker-progress merge + drain + cleanup, start_tagging
gatekeeping, the CPU branch of _build_runtime_plan, the ToriiGate CPU hardware
floor, and the background tag-export job state machine. Catalog/library
delegation pins live in test_tagging_pins.py.

No DB and no real models: every external seam is stubbed or patched.
DB-backed pins (worker main, export/import, fix_rating_tags, bulk job) live in
test_tagging_pins_worker.py.

Behaviors marked "QUIRK" are pinned as-is on purpose: if a refactor changes
them, that must be a conscious decision, not an accident.
"""

from __future__ import annotations

import queue as queue_module
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import BackgroundTasks, HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.tagging_service as tsvc  # noqa: E402
from services.tagging_service import (  # noqa: E402
    TAGGER_MODELS,
    TagRequest,
    TaggingService,
    _build_tag_progress_state,
)


class _DeadWorker:
    def is_alive(self) -> bool:
        return False


class _StoppingWorker:
    """Reports alive until join()/terminate() is called."""

    def __init__(self) -> None:
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout=None) -> None:
        self._alive = False

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False


class _FakeIPCQueue:
    """Stands in for the multiprocessing queue in drain/cleanup pins."""

    def __init__(self, payloads=None) -> None:
        self._payloads = list(payloads or [])
        self.closed = False
        self.joined = False

    def get_nowait(self):
        if not self._payloads:
            raise queue_module.Empty
        return self._payloads.pop(0)

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


# ===========================================================================
# Progress get/set + _coerce_progress_state + MutableStateProxy.
# ===========================================================================


def test_initial_progress_is_canonical_idle() -> None:
    assert TaggingService().get_progress() == _build_tag_progress_state("idle")


def test_get_progress_returns_a_defensive_copy() -> None:
    service = TaggingService()
    snapshot = service.get_progress()
    snapshot["status"] = "mutated"
    assert service.get_progress()["status"] == "idle"


def test_set_progress_coerces_onto_canonical_shape_and_drops_extras() -> None:
    """QUIRK: set_progress re-coerces through _coerce_progress_state, so
    unknown keys — INCLUDING last_run_stats — are silently dropped. A router
    (or proxy writer) cannot inject terminal stats through this path."""
    service = TaggingService()
    service.set_progress(
        {
            "status": "running",
            "current": "4",  # numeric strings coerce to int
            "processed": 9,  # honored independently of current
            "total": None,  # None coerces to 0
            "bogus": 1,
            "last_run_stats": {"total_processed": 4},
        }
    )
    progress = service.get_progress()
    assert progress["status"] == "running"
    assert progress["current"] == 4
    assert progress["processed"] == 9
    assert progress["total"] == 0
    assert "bogus" not in progress
    assert "last_run_stats" not in progress


def test_coerce_progress_state_none_yields_idle_and_processed_mirrors() -> None:
    assert TaggingService._coerce_progress_state(None) == _build_tag_progress_state(
        "idle"
    )
    # Without an explicit "processed" the coerced state mirrors current.
    coerced = TaggingService._coerce_progress_state({"status": "running", "current": 3})
    assert coerced["processed"] == 3


def test_progress_proxy_writes_round_trip_through_coercion() -> None:
    service = TaggingService()
    proxy = service.get_progress_proxy()
    proxy["status"] = "running"
    proxy.update({"current": 2, "message": "via proxy"})
    assert service.get_progress()["status"] == "running"
    assert service.get_progress()["current"] == 2
    assert service.get_progress()["message"] == "via proxy"
    assert proxy["message"] == "via proxy"
    assert "status" in proxy
    assert len(proxy) == len(service.get_progress())


# ===========================================================================
# reset_progress matrix.
# ===========================================================================


def test_reset_progress_idle_is_a_noop() -> None:
    service = TaggingService()
    assert service.reset_progress() == {
        "status": "idle",
        "message": "Nothing to reset",
    }


def test_reset_progress_from_done_returns_reset_and_clears_handles() -> None:
    service = TaggingService()
    service._progress = _build_tag_progress_state("done", current=5, total=5)
    service._worker_process = _DeadWorker()
    service._worker_cancel_event = object()
    service._cancel_requested = True

    result = service.reset_progress()

    assert result == {"status": "reset", "message": "Tagging progress reset to idle"}
    progress = service.get_progress()
    assert progress["status"] == "idle"
    assert progress["message"] == "Reset by user"
    assert service._worker_process is None
    assert service._worker_cancel_event is None
    assert service._cancel_requested is False


def test_reset_progress_refuses_while_worker_is_alive() -> None:
    service = TaggingService()
    service._progress = _build_tag_progress_state("running", current=1, total=9)
    service._worker_process = _StoppingWorker()

    assert service.reset_progress() == {
        "status": "running",
        "message": "Cannot reset while the tagger worker is still running",
    }
    assert service.get_progress()["status"] == "running"


def test_reset_progress_allows_running_state_with_dead_worker() -> None:
    """'running' with no live worker is a stuck state; reset must recover it."""
    service = TaggingService()
    service._progress = _build_tag_progress_state("running", current=1, total=9)
    service._worker_process = _DeadWorker()

    assert service.reset_progress()["status"] == "reset"
    assert service.get_progress()["status"] == "idle"


# ===========================================================================
# cancel_tagging edge cells (happy paths are in test_tagging_service.py).
# ===========================================================================


def test_cancel_tagging_when_idle_reports_nothing_running() -> None:
    assert TaggingService().cancel_tagging() == {
        "status": "idle",
        "message": "No tagging task is running",
    }


def test_quirk_cancel_with_stale_run_id_reports_cancelled_without_finalizing() -> None:
    """QUIRK: when the progress row belongs to an older run (run_id !=
    _active_run_id), cancel_tagging still stops the worker and RETURNS
    'cancelled', but the run-id guard skips the final progress write — the
    stored state stays 'cancelling' and the handles stay bound."""
    service = TaggingService()
    service._progress = _build_tag_progress_state(
        "running", current=2, total=5, run_id=1
    )
    service._active_run_id = 99
    service._worker_process = _StoppingWorker()

    result = service.cancel_tagging()

    assert result == {"status": "cancelled", "message": "Tagging cancelled"}
    assert service.get_progress()["status"] == "cancelling"
    assert service._worker_process is not None


# ===========================================================================
# _apply_worker_progress / _drain_worker_queue / _cleanup_worker_handles.
# ===========================================================================


def test_apply_worker_progress_merges_partial_payload_over_previous_state() -> None:
    service = TaggingService()
    service._active_run_id = 3
    service._progress = _build_tag_progress_state(
        "running",
        current=2,
        total=9,
        tagged=2,
        message="old message",
        runtime_backend_target="gpu",
        runtime_backend_actual="gpu",
        run_id=3,
    )

    service._apply_worker_progress({"current": 5, "tagged": 4}, run_id=3)

    progress = service.get_progress()
    assert progress["current"] == 5
    assert progress["tagged"] == 4
    # Untouched fields survive the merge.
    assert progress["status"] == "running"
    assert progress["message"] == "old message"
    assert progress["runtime_backend_target"] == "gpu"
    assert progress["runtime_backend_actual"] == "gpu"
    assert progress["total"] == 9
    # "processed" falls back to the payload's current.
    assert progress["processed"] == 5


def test_apply_worker_progress_carries_terminal_last_run_stats() -> None:
    """FIXED (was the dormant stats-modal bug): the worker's terminal IPC
    payload carries last_run_stats, and app.js pops the post-tag stats modal
    when GET /api/tag/progress exposes that key. The bridge used to rebuild
    the progress dict from an explicit key list that dropped it — the modal
    could never fire on the process-isolated pipeline. It now carries the
    key through, including across same-run straggler payloads (the drain
    keeps applying after a terminal — quirk #9), and reset_progress clears
    it for the next run."""
    service = TaggingService()
    service._active_run_id = 3
    service._progress = _build_tag_progress_state("running", run_id=3)

    service._apply_worker_progress(
        _build_tag_progress_state(
            "done",
            current=2,
            total=2,
            last_run_stats={"total_processed": 2, "top_tags": []},
        ),
        run_id=3,
    )

    progress = service.get_progress()
    assert progress["status"] == "done"
    assert progress["last_run_stats"] == {"total_processed": 2, "top_tags": []}

    # Same-run straggler payloads (drain applies them after the terminal —
    # quirk #9) must not wipe the stats before the frontend polls them.
    service._apply_worker_progress({"status": "done", "message": "straggler"}, run_id=3)
    assert service.get_progress()["last_run_stats"] == {
        "total_processed": 2,
        "top_tags": [],
    }

    # The next run starts clean.
    service.reset_progress()
    assert "last_run_stats" not in service.get_progress()


def test_apply_worker_progress_run_id_fallback_chain() -> None:
    """Payload run_id 0 is falsy — the effective run_id falls back to the
    caller's run_id argument."""
    service = TaggingService()
    service._active_run_id = 7
    service._progress = _build_tag_progress_state("running", run_id=7)

    service._apply_worker_progress({"status": "running", "run_id": 0}, run_id=7)
    assert service.get_progress()["run_id"] == 7

    # And with neither payload run_id nor argument, the previous progress
    # run_id is kept.
    service._apply_worker_progress({"message": "still here"})
    assert service.get_progress()["run_id"] == 7


def test_drain_worker_queue_applies_all_and_flags_terminal_anywhere() -> None:
    service = TaggingService()
    service._active_run_id = 1
    service._progress = _build_tag_progress_state("running", run_id=1)
    fake_queue = _FakeIPCQueue(
        [
            {"status": "running", "current": 1},
            {"status": "done", "current": 2, "message": "finished"},
            {"status": "running", "current": 3, "message": "late straggler"},
        ]
    )

    saw_terminal = service._drain_worker_queue(fake_queue, run_id=1)

    assert saw_terminal is True
    # QUIRK: draining does not stop at the terminal payload — the straggler
    # after "done" still lands in progress.
    progress = service.get_progress()
    assert progress["current"] == 3
    assert progress["message"] == "late straggler"


def test_drain_worker_queue_returns_false_without_terminal_payloads() -> None:
    service = TaggingService()
    service._active_run_id = 1
    service._progress = _build_tag_progress_state("running", run_id=1)
    fake_queue = _FakeIPCQueue([{"status": "running", "current": 1}])
    assert service._drain_worker_queue(fake_queue, run_id=1) is False


def test_cleanup_worker_handles_clears_on_matching_run_and_closes_queue() -> None:
    service = TaggingService()
    service._active_run_id = 2
    service._progress = _build_tag_progress_state("done", run_id=2)
    service._worker_process = _DeadWorker()
    service._worker_cancel_event = object()
    service._cancel_requested = True
    fake_queue = _FakeIPCQueue()

    service._cleanup_worker_handles(fake_queue, run_id=2)

    assert service._worker_process is None
    assert service._worker_cancel_event is None
    assert service._cancel_requested is False
    assert fake_queue.closed is True
    assert fake_queue.joined is True


def test_quirk_cleanup_preserves_cancel_flag_while_status_is_cancelling() -> None:
    """QUIRK: cleanup clears _cancel_requested only when the progress status
    is NOT 'cancelling' — mid-cancel the flag must survive for the
    cancellation flow to finish."""
    service = TaggingService()
    service._active_run_id = 2
    service._progress = _build_tag_progress_state("cancelling", run_id=2)
    service._cancel_requested = True

    service._cleanup_worker_handles(run_id=2)

    assert service._cancel_requested is True


def test_cleanup_worker_handles_skips_state_on_run_mismatch_but_closes_queue() -> None:
    service = TaggingService()
    service._active_run_id = 5
    service._pending_run_id = 5
    worker = _DeadWorker()
    event = object()
    service._worker_process = worker
    service._worker_cancel_event = event
    fake_queue = _FakeIPCQueue()

    service._cleanup_worker_handles(fake_queue, run_id=4)

    assert service._worker_process is worker
    assert service._worker_cancel_event is event
    assert service._pending_run_id == 5
    assert fake_queue.closed is True


# ===========================================================================
# start_tagging gatekeeping + _validate_tag_request registry cells.
# ===========================================================================


def test_start_tagging_without_tagger_getter_is_500() -> None:
    service = TaggingService()
    with pytest.raises(HTTPException) as exc_info:
        service.start_tagging(
            TagRequest(model_name="wd-swinv2-tagger-v3", use_gpu=False),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Tagger not initialized"


def test_request_validation_precedes_tagger_getter_check() -> None:
    """Ordering pin: an invalid model must 400 even when the tagger getter is
    also missing (validation runs before the 500 initialization check)."""
    service = TaggingService()
    with pytest.raises(HTTPException) as exc_info:
        service.start_tagging(TagRequest(model_name="not-a-model"), BackgroundTasks())
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unknown tagger model: not-a-model"


def test_validate_tag_request_disabled_model_is_409_with_reason(monkeypatch) -> None:
    monkeypatch.setitem(
        TAGGER_MODELS,
        "pin-disabled-model",
        {"disabled": True, "disabled_reason": "removed from this build"},
    )
    service = TaggingService()
    with pytest.raises(HTTPException) as exc_info:
        service._validate_tag_request(TagRequest(model_name="pin-disabled-model"))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "removed from this build"


def test_validate_tag_request_disabled_model_fallback_reason(monkeypatch) -> None:
    monkeypatch.setitem(TAGGER_MODELS, "pin-disabled-model", {"disabled": True})
    service = TaggingService()
    with pytest.raises(HTTPException) as exc_info:
        service._validate_tag_request(TagRequest(model_name="pin-disabled-model"))
    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail
        == "Model pin-disabled-model is not available in the current build."
    )


def test_start_tagging_bumps_run_id_and_queues_exactly_one_task() -> None:
    service = TaggingService()
    service.set_tagger_getter(lambda **kwargs: object())
    background_tasks = BackgroundTasks()

    result = service.start_tagging(
        TagRequest(model_name="wd-swinv2-tagger-v3", use_gpu=False), background_tasks
    )

    assert result == {
        "status": "started",
        "message": "Tagging started in background",
    }
    progress = service.get_progress()
    assert progress["status"] == "running"
    assert progress["message"] == "Preparing tagger..."
    assert progress["run_id"] == service._active_run_id == 1
    assert service._pending_run_id == 1
    assert len(background_tasks.tasks) == 1


def test_second_start_rejects_current_pending_run_without_invalidating_it() -> None:
    service = TaggingService()
    service.set_tagger_getter(lambda **kwargs: object())
    request = TagRequest(model_name="wd-swinv2-tagger-v3", use_gpu=False)
    first_tasks = BackgroundTasks()
    second_tasks = BackgroundTasks()

    first_result = service.start_tagging(request, first_tasks)
    with pytest.raises(HTTPException) as exc_info:
        service.start_tagging(request, second_tasks)

    assert first_result["status"] == "started"
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Tagging already in progress"
    assert service._active_run_id == 1
    assert service._pending_run_id == 1
    assert service.get_progress()["run_id"] == 1
    assert len(first_tasks.tasks) == 1
    assert len(second_tasks.tasks) == 0


# ===========================================================================
# _validate_model_hardware_requirements — CPU floor + non-ToriiGate bypass.
# ===========================================================================


def test_hardware_gate_is_skipped_entirely_for_non_toriigate_models() -> None:
    """Only the toriigate backend consults hardware_monitor; every other model
    must pass without even probing the system."""
    service = TaggingService()

    def _boom():
        raise AssertionError("get_system_info must not be called")

    with patch("hardware_monitor.get_system_info", side_effect=_boom):
        service._validate_model_hardware_requirements(
            "wd-swinv2-tagger-v3", use_gpu=True
        )
        service._validate_model_hardware_requirements(
            "oppai-oracle-v1.1", use_gpu=False
        )


def test_toriigate_cpu_floor_blocks_undersized_ram() -> None:
    service = TaggingService()
    with patch(
        "hardware_monitor.get_system_info",
        return_value={"total_ram_gb": 8, "available_ram_gb": 1},
    ):
        with pytest.raises(HTTPException) as exc_info:
            service._validate_model_hardware_requirements(
                "toriigate-0.5", use_gpu=False
            )
    assert exc_info.value.status_code == 409
    assert "ToriiGate CPU mode is blocked" in exc_info.value.detail


def test_quirk_unknown_hardware_passes_cpu_floor_but_fails_gpu_on_cuda() -> None:
    """QUIRK: every RAM/VRAM comparison is gated on `minimum and detected and
    detected < minimum` — a box reporting 0/unknown values sails through the
    CPU floor. The GPU floor still fails, but only via the CUDA flag."""
    service = TaggingService()
    with patch("hardware_monitor.get_system_info", return_value={}):
        # CPU: unknown hardware is allowed.
        service._validate_model_hardware_requirements("toriigate-0.5", use_gpu=False)
        # GPU: the missing torch CUDA runtime is the only failure listed.
        with pytest.raises(HTTPException) as exc_info:
            service._validate_model_hardware_requirements("toriigate-0.5", use_gpu=True)
    assert "PyTorch CUDA runtime is unavailable" in exc_info.value.detail


# ===========================================================================
# _build_runtime_plan — CPU branch + payload shape (GPU/custom/ToriiGate-GPU
# cells are pinned in test_tagging_service.py).
# ===========================================================================

RUNTIME_PLAN_KEYS = {
    "request",
    "model_name",
    "effective_use_gpu",
    "gpu_locked",
    "startup_notice",
    "fetch_batch_size",
    "commit_interval",
    "gc_interval",
    "cpu_pause_seconds",
    "session_refresh_interval",
}


def _cpu_plan(recommendation, **request_kwargs):
    service = TaggingService()
    request = TagRequest(
        model_name="wd-swinv2-tagger-v3", use_gpu=False, **request_kwargs
    )
    with (
        patch("hardware_monitor.get_system_info", return_value={}),
        patch(
            "hardware_monitor.recommend_tagger_config",
            return_value=recommendation,
        ),
    ):
        return service._build_runtime_plan(request)


def test_runtime_plan_payload_key_set_and_request_round_trip() -> None:
    plan = _cpu_plan({"recommended_cpu_chunk_size": 24})
    assert set(plan.keys()) == RUNTIME_PLAN_KEYS
    # The embedded request must re-validate cleanly — this is exactly what
    # the spawned worker does on its side of the IPC boundary.
    round_tripped = TagRequest.model_validate(plan["request"])
    assert round_tripped.use_gpu is False
    assert round_tripped.model_name == "wd-swinv2-tagger-v3"


def test_runtime_plan_cpu_branch_uses_recommended_chunk_and_long_run_pacing() -> None:
    plan = _cpu_plan({"recommended_cpu_chunk_size": 24})
    assert plan["effective_use_gpu"] is False
    assert plan["fetch_batch_size"] == 24
    # commit/gc are derived caps: min(fetch, 10) / min(fetch, 8) with floors.
    assert plan["commit_interval"] == 10
    assert plan["gc_interval"] == 8
    # Chunk >= 12 gets the larger inter-chunk breather.
    assert plan["cpu_pause_seconds"] == 0.02
    # No recommended refresh -> the CPU arena-release fallback of 100.
    assert plan["session_refresh_interval"] == 100
    assert plan["startup_notice"].startswith("CPU mode is using a larger worker chunk")


def test_runtime_plan_cpu_small_chunk_gets_shorter_pause() -> None:
    plan = _cpu_plan({"recommended_cpu_chunk_size": 8})
    assert plan["fetch_batch_size"] == 8
    assert plan["cpu_pause_seconds"] == 0.01


def test_runtime_plan_cpu_chunk_capped_at_cpu_chunk_max() -> None:
    plan = _cpu_plan({"recommended_cpu_chunk_size": 999})
    assert plan["fetch_batch_size"] == tsvc.CPU_CHUNK_MAX == 64


def test_runtime_plan_cpu_missing_recommendation_falls_back_to_12() -> None:
    plan = _cpu_plan({})
    assert plan["fetch_batch_size"] == 12
    assert plan["cpu_pause_seconds"] == 0.02


def test_runtime_plan_cpu_requested_chunk_clamped_with_notice() -> None:
    plan = _cpu_plan({"recommended_cpu_chunk_size": 12}, batch_size=64)
    assert plan["fetch_batch_size"] == 12
    assert "Requested runtime chunk size 64 was reduced to 12" in plan["startup_notice"]


def test_runtime_plan_toriigate_cpu_fixes_chunk_to_one() -> None:
    service = TaggingService()
    with (
        patch("hardware_monitor.get_system_info", return_value={}),
        patch(
            "hardware_monitor.recommend_tagger_config",
            return_value={"recommended_cpu_chunk_size": 24},
        ),
    ):
        plan = service._build_runtime_plan(
            TagRequest(model_name="toriigate-0.5", use_gpu=False)
        )
    assert plan["fetch_batch_size"] == 1
    assert plan["session_refresh_interval"] == 0
    assert "ToriiGate is running on CPU" in plan["startup_notice"]


# ===========================================================================
# Background batch tag-export job (v3.3.2 Phase-1) state machine.
# ===========================================================================


def _start_export_job(service, monkeypatch=None, ids=(1, 2, 3)):
    from services.tagging_service import BatchTagExportRequest

    request = BatchTagExportRequest(image_ids=list(ids), output_folder="unused")
    background_tasks = BackgroundTasks()
    envelope = service.start_export_tags_batch_job(request, background_tasks)
    return request, background_tasks, envelope


def test_export_job_initial_progress_and_reset_matrix() -> None:
    service = TaggingService()
    assert service.get_export_progress()["status"] == "idle"
    # Copy semantics.
    snapshot = service.get_export_progress()
    snapshot["status"] = "mutated"
    assert service.get_export_progress()["status"] == "idle"

    # Reset while running is refused with 409, like the four sibling job
    # families (d7013e2, 2e2ae37). It used to answer 200 with a dict, which any
    # caller checking the status code reads as a successful reset.
    service._export_progress = {
        **service._build_default_export_progress_state(),
        "status": "running",
    }
    with pytest.raises(HTTPException) as excinfo:
        service.reset_export_progress()
    assert excinfo.value.status_code == 409

    # Resetting a terminal state resets, and now says so. The old
    # "Nothing to reset" answer here was the QUIRK this pin used to record.
    service._export_progress = {
        **service._build_default_export_progress_state(),
        "status": "done",
    }
    assert service.reset_export_progress() == {
        "status": "reset",
        "message": "Tag export progress reset to idle",
    }
    assert service.get_export_progress()["status"] == "idle"

    # An already-idle job is the only case that reports nothing happened.
    assert service.reset_export_progress() == {
        "status": "idle",
        "message": "Nothing to reset",
    }


def test_set_export_progress_if_current_guards_stale_runs() -> None:
    service = TaggingService()
    service._export_run_id = 2
    fresh = {**service._build_default_export_progress_state(), "status": "done"}
    assert service._set_export_progress_if_current(1, fresh) is False
    assert service.get_export_progress()["status"] == "idle"
    assert service._set_export_progress_if_current(2, fresh) is True
    assert service.get_export_progress()["status"] == "done"


def test_start_export_job_returns_started_envelope_and_marks_running() -> None:
    service = TaggingService()
    _, background_tasks, envelope = _start_export_job(service)

    assert envelope == {
        "status": "started",
        "message": "Exporting 3 images in background",
        "total": 3,
        "operation": "export",
    }
    progress = service.get_export_progress()
    assert progress["status"] == "running"
    assert progress["step"] == "exporting"
    assert progress["total"] == 3
    assert progress["message"] == "Exporting tags for 3 images..."
    assert progress["started_at"] is not None
    assert len(background_tasks.tasks) == 1


def test_start_export_job_409_when_already_running() -> None:
    service = TaggingService()
    _start_export_job(service)
    with pytest.raises(HTTPException) as exc_info:
        _start_export_job(service)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "An export is already in progress"


def test_export_job_terminal_done_embeds_full_result(monkeypatch) -> None:
    service = TaggingService()
    fake_result = {"status": "ok", "exported": 3, "errors": 0, "skipped": 0}
    monkeypatch.setattr(service, "export_tags_batch", lambda request: fake_result)
    _, background_tasks, _ = _start_export_job(service)

    background_tasks.tasks[0].func()

    progress = service.get_export_progress()
    assert progress["status"] == "done"
    assert progress["step"] == "done"
    assert progress["current"] == progress["total"] == 3
    assert progress["message"] == "Export complete: 3 files."
    assert progress["result"] is fake_result


def test_export_job_failure_embeds_error_result_envelope(monkeypatch) -> None:
    service = TaggingService()

    def _boom(request):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "export_tags_batch", _boom)
    _, background_tasks, _ = _start_export_job(service)

    background_tasks.tasks[0].func()

    progress = service.get_export_progress()
    assert progress["status"] == "error"
    assert progress["message"] == "Export failed due to an internal error"
    assert progress["result"] == {
        "status": "error",
        "exported": 0,
        "errors": 1,
        "error_count": 1,
        "error_messages": ["disk full"],
        "skipped": 0,
        "total": 3,
    }


def test_export_job_stale_run_cannot_write_terminal_state(monkeypatch) -> None:
    service = TaggingService()
    monkeypatch.setattr(
        service, "export_tags_batch", lambda request: {"status": "ok", "exported": 3}
    )
    _, background_tasks, _ = _start_export_job(service)

    # A competing run supersedes the queued one before it executes.
    service._export_run_id += 1
    background_tasks.tasks[0].func()

    progress = service.get_export_progress()
    assert progress["status"] == "running"
    assert progress["result"] is None


def test_start_export_job_uses_selection_token_count_for_total(monkeypatch) -> None:
    from services.tagging_service import BatchTagExportRequest

    monkeypatch.setattr(tsvc, "count_selection_token_ids", lambda token: 42)
    service = TaggingService()
    envelope = service.start_export_tags_batch_job(
        BatchTagExportRequest(selection_token="tok", output_folder="unused"),
        BackgroundTasks(),
    )
    assert envelope["total"] == 42
    assert service.get_export_progress()["total"] == 42
