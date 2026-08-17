"""A stuck move / batch-move / delete job must be recoverable without a restart.

``cancel_move``, ``cancel_batch_move`` and ``cancel_delete`` all publish a
``cancelling`` status and leave the terminal state to the worker. When the
worker is gone, that status is permanent — and it is inside the busy set that
``start_move_job`` / ``batch_move_images`` / ``start_delete_selected_job``
reject with HTTP 409. The three "reset a stuck job" endpoints existed for
exactly that situation but never assigned their progress state, so they
answered successfully while the feature stayed blocked until the app restarted.

Each test therefore asserts the job is *runnable again*, not merely that reset
returned something.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

import services.sorting_service as ss


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A fresh SortingService with its persisted-session files redirected."""
    monkeypatch.setattr(ss, "SESSION_FILE", str(tmp_path / "session.json"), raising=False)
    monkeypatch.setattr(ss, "LEGACY_SESSION_FILE", str(tmp_path / "legacy.json"), raising=False)
    return ss.SortingService()


class TestStuckJobReset:
    def test_reset_move_progress_makes_a_stranded_move_runnable_again(
        self, tmp_path, svc
    ):
        request = ss.MoveRequest(image_ids=[999999], destination_folder=str(tmp_path))
        svc._move_progress["status"] = "cancelling"

        with pytest.raises(HTTPException) as excinfo:
            svc.start_move_job(request, BackgroundTasks())
        assert excinfo.value.status_code == 409

        svc.reset_move_progress()

        assert svc.get_move_progress()["status"] == "idle"
        assert svc.start_move_job(request, BackgroundTasks())["status"] == "started"

    def test_reset_batch_move_progress_makes_a_stranded_batch_runnable_again(
        self, test_db, tmp_path, svc
    ):
        request = ss.BatchMoveRequest(
            destination_folder=str(tmp_path), generators=["unknown"]
        )
        svc._batch_move_progress["status"] = "cancelling"

        with pytest.raises(HTTPException) as excinfo:
            svc.batch_move_images(request, BackgroundTasks())
        assert excinfo.value.status_code == 409

        svc.reset_batch_move_progress()

        assert svc.get_batch_move_progress()["status"] == "idle"
        # Empty isolated library: the guard is past, so the job reports "no matches".
        assert svc.batch_move_images(request, BackgroundTasks())["count"] == 0

    def test_reset_delete_progress_makes_a_stranded_delete_runnable_again(self):
        from services.image_service import ImageService

        service = ImageService()
        request = SimpleNamespace(image_ids=[999999], selection_token=None)
        service._delete_progress["status"] = "cancelling"

        with pytest.raises(HTTPException) as excinfo:
            service.start_delete_selected_job(request, BackgroundTasks())
        assert excinfo.value.status_code == 409

        service.reset_delete_progress()

        assert service.get_delete_progress()["status"] == "idle"
        assert (
            service.start_delete_selected_job(request, BackgroundTasks())["status"]
            == "started"
        )

    def test_reset_move_progress_still_refuses_a_running_job(self, svc):
        svc._move_progress["status"] = "running"

        with pytest.raises(HTTPException) as excinfo:
            svc.reset_move_progress()

        assert excinfo.value.status_code == 409
        assert svc.get_move_progress()["status"] == "running"

    def test_reset_batch_move_progress_still_refuses_a_running_job(self, svc):
        svc._batch_move_progress["status"] = "running"

        with pytest.raises(HTTPException) as excinfo:
            svc.reset_batch_move_progress()

        assert excinfo.value.status_code == 409
        assert svc.get_batch_move_progress()["status"] == "running"

    def test_reset_delete_progress_still_refuses_a_running_job(self):
        from services.image_service import ImageService

        service = ImageService()
        service._delete_progress["status"] = "running"

        with pytest.raises(HTTPException) as excinfo:
            service.reset_delete_progress()

        assert excinfo.value.status_code == 409
        assert service.get_delete_progress()["status"] == "running"

    def test_reset_move_progress_abandons_a_stale_worker(self, svc):
        """A zombie worker must not be able to write over the reset state."""
        svc._move_progress["status"] = "cancelling"
        stale_run_id = svc._move_run_id

        svc.reset_move_progress()

        assert (
            svc._update_move_progress_if_current(stale_run_id, status="running") is False
        )
        assert svc.get_move_progress()["status"] == "idle"
