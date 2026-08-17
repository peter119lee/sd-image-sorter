"""A tag export whose worker never ran must be clearable; a live one must not be.

The batch tag-export job publishes ``running`` from the request thread and then
hands the work to a FastAPI background task. Starlette runs that task only after
the response body has been sent (``starlette/responses.py``: ``await send(...)``
precedes ``await self.background()``), so a client that disappears before the
response lands leaves ``running`` published with nothing behind it. The job has
no ``cancelling`` state, so ``running`` is the only state that blocks
``start_export_tags_batch_job`` — and it was the one state
``reset_export_progress`` refused to clear. The reset could therefore only ever
answer 409: a recovery path that could not recover anything.

Every test here asserts the user outcome — a new export can start again, or the
work in flight survived — rather than a status code or a payload key.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import BackgroundTasks, HTTPException

import services.tagging_service as tsvc
from services.tagging_service import BatchTagExportRequest, TaggingService

EXPORT_RESULT = {
    "status": "ok",
    "exported": 3,
    "errors": 0,
    "error_count": 0,
    "error_messages": [],
    "skipped": 0,
    "total": 3,
}


def _start(service, ids=(1, 2, 3)):
    """Start the Phase-1 export job without executing its background task."""
    request = BatchTagExportRequest(image_ids=list(ids), output_folder="unused")
    background_tasks = BackgroundTasks()
    service.start_export_tags_batch_job(request, background_tasks)
    return request, background_tasks


def _age_export_run(service, seconds: float) -> None:
    """Rewind the published start/heartbeat stamps to simulate elapsed time."""
    with service._export_lock:
        aged = time.time() - seconds
        service._export_progress = {
            **service._export_progress,
            "started_at": aged,
            "updated_at": aged,
        }


class TestStrandedExportRecovery:
    def test_a_stranded_export_is_cleared_and_a_new_export_can_start(self):
        service = TaggingService()
        request, _background_tasks = _start(service)
        # The background task is never executed: the client vanished before the
        # response flushed, so Starlette dropped it.
        _age_export_run(service, 3600)

        with pytest.raises(HTTPException) as blocked:
            service.start_export_tags_batch_job(request, BackgroundTasks())
        assert blocked.value.status_code == 409

        outcome = service.reset_export_progress()

        assert outcome["status"] == "reset"
        assert outcome["message"] != "Nothing to reset"
        # The user outcome: exporting works again.
        assert (
            service.start_export_tags_batch_job(request, BackgroundTasks())["status"]
            == "started"
        )

    def test_the_stranded_export_recovers_over_http(self, test_client, tmp_path):
        from routers import tags as tags_router

        service = tags_router.get_tagging_service()
        service._export_progress = {
            **service._build_default_export_progress_state(),
            "status": "running",
            "step": "exporting",
            "total": 3,
            "started_at": time.time() - 3600,
            "updated_at": time.time() - 3600,
        }
        payload = {"image_ids": [1], "output_folder": str(tmp_path)}

        assert test_client.post("/api/tags/export-batch/start", json=payload).status_code == 409

        reset = test_client.post("/api/tags/export-batch/reset")
        assert reset.status_code == 200
        assert reset.json()["status"] == "reset"

        restarted = test_client.post("/api/tags/export-batch/start", json=payload)
        assert restarted.status_code == 200
        assert restarted.json()["status"] == "started"

    def test_a_queued_worker_is_not_called_abandoned_before_it_can_start(self):
        """A task that has not had time to begin is still a live run."""
        service = TaggingService()
        _start(service)

        with pytest.raises(HTTPException) as excinfo:
            service.reset_export_progress()

        assert excinfo.value.status_code == 409
        progress = service.get_export_progress()
        assert progress["status"] == "running"
        assert progress["abandoned"] is False


class TestRunningExportIsStillRefused:
    """The 409 must keep meaning 'correctly refused', never 'something broke'."""

    @staticmethod
    def _run_blocking_export(service, entered, release):
        def slow_export(request, **_kwargs):
            entered.set()
            assert release.wait(timeout=15)
            return dict(EXPORT_RESULT)

        service.export_tags_batch = slow_export
        _request, background_tasks = _start(service)
        worker = threading.Thread(target=background_tasks.tasks[0].func, daemon=True)
        worker.start()
        assert entered.wait(timeout=15)
        return worker

    def test_reset_refuses_a_running_export_and_the_export_still_finishes(self):
        service = TaggingService()
        entered, release = threading.Event(), threading.Event()
        worker = self._run_blocking_export(service, entered, release)
        try:
            with pytest.raises(HTTPException) as excinfo:
                service.reset_export_progress()

            assert excinfo.value.status_code == 409
            assert "running" in str(excinfo.value.detail).lower()
            assert service.get_export_progress()["status"] == "running"
        finally:
            release.set()
            worker.join(timeout=15)

        assert service.get_export_progress()["status"] == "done"
        assert service.get_export_progress()["result"]["exported"] == 3

    def test_a_slow_export_is_not_mistaken_for_a_dead_one(self):
        """A live worker that has published nothing for an hour keeps its run.

        Clearing it would let a second export run against the files the first
        one is still writing — worse than the stranding this slice fixes.
        """
        service = TaggingService()
        entered, release = threading.Event(), threading.Event()
        worker = self._run_blocking_export(service, entered, release)
        try:
            _age_export_run(service, 3600)
            progress = service.get_export_progress()
            assert progress["stalled_seconds"] >= 3600
            assert progress["abandoned"] is False

            with pytest.raises(HTTPException) as excinfo:
                service.reset_export_progress()
            assert excinfo.value.status_code == 409
        finally:
            release.set()
            worker.join(timeout=15)

        # The work in flight survived the refusal.
        assert service.get_export_progress()["status"] == "done"
        assert service.get_export_progress()["result"]["exported"] == 3

    def test_a_stalled_live_export_asks_for_attention_without_claiming_death(self):
        service = TaggingService()
        entered, release = threading.Event(), threading.Event()
        worker = self._run_blocking_export(service, entered, release)
        try:
            _age_export_run(service, 3600)
            progress = service.get_export_progress()

            assert progress["attention_required"] is True
            assert progress["attention_message"]
            assert progress["abandoned"] is False
        finally:
            release.set()
            worker.join(timeout=15)


class TestExportHeartbeat:
    def test_the_heartbeat_keeps_a_working_export_from_looking_stalled(
        self, monkeypatch
    ):
        """Without a real beat, ``stalled_seconds`` would just be elapsed time."""
        service = TaggingService()
        monkeypatch.setattr(tsvc, "EXPORT_HEARTBEAT_MIN_INTERVAL_SECONDS", 0.0)
        observed = []

        def fake_export_request(request, **kwargs):
            progress_callback = kwargs.get("progress_callback")
            assert progress_callback is not None
            for processed in (1, 2, 3):
                _age_export_run(service, 3600)
                stale = service.get_export_progress()
                progress_callback({"processed": processed, "total": 3})
                fresh = service.get_export_progress()
                observed.append((stale["attention_required"], fresh, processed))
            return dict(EXPORT_RESULT)

        monkeypatch.setattr(tsvc, "export_tags_batch_request", fake_export_request)
        _request, background_tasks = _start(service)
        background_tasks.tasks[0].func()

        assert len(observed) == 3
        for stale_attention, fresh, processed in observed:
            assert stale_attention is True
            assert fresh["stalled_seconds"] == 0
            assert fresh["attention_required"] is False
            assert fresh["current"] == processed
        assert service.get_export_progress()["status"] == "done"
