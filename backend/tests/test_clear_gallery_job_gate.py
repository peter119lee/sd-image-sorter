"""Atomic Clear Gallery guards for image-index background jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import NoReturn, Protocol

import pytest
from fastapi import BackgroundTasks
from httpx import Response
from PIL import Image
from pytest import MonkeyPatch

from services.aesthetic_service import AestheticService


class _IndexedImageDatabase(Protocol):
    def add_image(self, *, path: str, filename: str, generator: str) -> int: ...

    def get_image_by_id(self, image_id: int) -> object | None: ...


class _ApiTestClient(Protocol):
    test_db: _IndexedImageDatabase

    def delete(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...


def _add_indexed_image(test_client: _ApiTestClient) -> int:
    return int(test_client.test_db.add_image(
        path="/test/clear-gate.png",
        filename="clear-gate.png",
        generator="unknown",
    ))


def _assert_image_remains(test_client: _ApiTestClient, image_id: int) -> None:
    assert test_client.test_db.get_image_by_id(image_id) is not None


@pytest.fixture
def isolated_aesthetic_service() -> Iterator[AestheticService]:
    from routers import aesthetic as aesthetic_router

    original = aesthetic_router.get_aesthetic_service()
    service = AestheticService()
    aesthetic_router.set_aesthetic_service(service)
    try:
        yield service
    finally:
        aesthetic_router.set_aesthetic_service(original)


def test_clear_gallery_rejects_active_scan(test_client: _ApiTestClient) -> None:
    from routers import sorting as sorting_router

    image_id = _add_indexed_image(test_client)
    sorting_router.get_sorting_service().set_scan_progress({
        "run_id": 1,
        "source": "manual",
        "status": "running",
        "step": "importing",
    })

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_active_gallery_tag(test_client: _ApiTestClient) -> None:
    from routers import tags as tags_router

    image_id = _add_indexed_image(test_client)
    tags_router.get_tagging_service().set_progress({"status": "running"})

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_active_smart_tag(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from services import tagging_pipeline_service

    image_id = _add_indexed_image(test_client)
    monkeypatch.setattr(
        tagging_pipeline_service.smart_tag_service,
        "get_active_job",
        lambda: SimpleNamespace(job_id="smart-active", status="running"),
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    assert "smart_tag" in response.json()["jobs"]
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_active_vlm_caption(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    import routers.vlm as vlm_router

    image_id = _add_indexed_image(test_client)
    monkeypatch.setitem(vlm_router._batch_state, "running", True)

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    assert "vlm_caption" in response.json()["jobs"]
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_active_aesthetic_scoring(
    test_client: _ApiTestClient,
    isolated_aesthetic_service: AestheticService,
) -> None:
    image_id = _add_indexed_image(test_client)
    isolated_aesthetic_service.start_scoring_progress(total=1)

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_calls_delete_once_when_all_jobs_are_idle(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import sorting as sorting_router

    image_id = _add_indexed_image(test_client)
    sorting_service = sorting_router.get_sorting_service()
    original_clear_gallery = sorting_service.clear_gallery
    clear_calls: list[str] = []

    def record_clear_gallery() -> dict[str, str]:
        clear_calls.append("clear")
        return original_clear_gallery()

    monkeypatch.setattr(sorting_service, "clear_gallery", record_clear_gallery)

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 200
    assert clear_calls == ["clear"]
    assert test_client.test_db.get_image_by_id(image_id) is None


def test_clear_gallery_rejects_unknown_tag_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import tags as tags_router

    image_id = _add_indexed_image(test_client)
    tagging_service = tags_router.get_tagging_service()

    def raise_progress_error() -> NoReturn:
        raise RuntimeError("injected tag progress failure")

    monkeypatch.setattr(tagging_service, "get_progress", raise_progress_error)

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_scan_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import sorting as sorting_router

    image_id = _add_indexed_image(test_client)
    sorting_service = sorting_router.get_sorting_service()
    monkeypatch.setattr(
        sorting_service,
        "get_scan_progress",
        lambda: {"status": "unexpected"},
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_scan_worker_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import sorting as sorting_router

    image_id = _add_indexed_image(test_client)
    sorting_service = sorting_router.get_sorting_service()
    monkeypatch.setattr(sorting_service, "is_scan_worker_active", lambda: "false")

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_gallery_tag_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import tags as tags_router

    image_id = _add_indexed_image(test_client)
    monkeypatch.setattr(tags_router.get_tagging_service(), "get_progress", lambda: {})

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_ai_activity_type(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from services.tagging_pipeline_service import get_tagging_pipeline_service

    image_id = _add_indexed_image(test_client)
    pipeline = get_tagging_pipeline_service()

    def malformed_activity(*, legacy_service: object) -> SimpleNamespace:
        del legacy_service
        return SimpleNamespace(state="idle", jobs=(), detail="")

    monkeypatch.setattr(
        pipeline,
        "get_gallery_mutation_activity",
        malformed_activity,
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


@pytest.mark.parametrize(
    ("state", "jobs"),
    [
        ("busy", ()),
        ("unknown", ()),
        ("idle", ("gallery_tag",)),
    ],
)
def test_clear_gallery_rejects_contradictory_ai_activity(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
    state: str,
    jobs: tuple[str, ...],
) -> None:
    from services.tagging_pipeline_service import (
        GalleryMutationActivity,
        get_tagging_pipeline_service,
    )

    image_id = _add_indexed_image(test_client)
    pipeline = get_tagging_pipeline_service()

    def malformed_activity(*, legacy_service: object) -> GalleryMutationActivity:
        del legacy_service
        return GalleryMutationActivity(
            state=state,
            jobs=jobs,
            detail="injected contradictory aggregate",
        )

    monkeypatch.setattr(
        pipeline,
        "get_gallery_mutation_activity",
        malformed_activity,
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_single_aesthetic_score_during_inference(
    test_client: _ApiTestClient,
    isolated_aesthetic_service: AestheticService,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    import aesthetic

    image_path = tmp_path / "single-aesthetic-clear-gate.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)
    image_id = int(test_client.test_db.add_image(
        path=str(image_path),
        filename=image_path.name,
        generator="unknown",
    ))
    inference_entered = threading.Event()
    release_inference = threading.Event()
    score_responses: list[Response] = []
    thread_errors: list[Exception] = []

    def paused_predict_score(path: str, **_kwargs) -> float:
        assert path == str(image_path)
        inference_entered.set()
        if not release_inference.wait(timeout=5):
            raise TimeoutError("test did not release paused Aesthetic inference")
        return 7.5

    def run_score() -> None:
        try:
            score_responses.append(test_client.post(
                f"/api/aesthetic/score/{image_id}",
                json={},
            ))
        except Exception as exc:
            thread_errors.append(exc)

    monkeypatch.setattr(aesthetic, "predict_score", paused_predict_score)
    score_thread = threading.Thread(target=run_score, name="test-aesthetic-score")
    score_thread.start()
    assert inference_entered.wait(timeout=2)

    try:
        response = test_client.delete("/api/clear-gallery")
        assert response.status_code == 409
        assert response.json()["code"] == "gallery_clear_jobs_active"
        assert "aesthetic" in response.json()["jobs"]
        _assert_image_remains(test_client, image_id)
    finally:
        release_inference.set()
        score_thread.join(timeout=5)

    assert not score_thread.is_alive()
    assert thread_errors == []
    assert score_responses[0].status_code == 200


def test_single_aesthetic_not_found_releases_activity_before_clear(
    test_client: _ApiTestClient,
    isolated_aesthetic_service: AestheticService,
    monkeypatch: MonkeyPatch,
) -> None:
    import aesthetic

    image_id = _add_indexed_image(test_client)
    predict_calls: list[str] = []

    def record_unexpected_prediction(path: str) -> float:
        predict_calls.append(path)
        return 7.5

    monkeypatch.setattr(aesthetic, "predict_score", record_unexpected_prediction)

    score_response = test_client.post(
        f"/api/aesthetic/score/{image_id + 1_000_000}",
        json={},
    )

    assert score_response.status_code == 404
    assert predict_calls == []
    _assert_image_remains(test_client, image_id)

    clear_response = test_client.delete("/api/clear-gallery")

    assert clear_response.status_code == 200
    assert clear_response.json()["status"] == "ok"
    assert test_client.test_db.get_image_by_id(image_id) is None


def test_clear_gallery_rejects_single_vlm_caption_during_provider_await(
    test_client: _ApiTestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    import routers.vlm as vlm_router
    from vlm_providers.base import VLMResult

    image_path = tmp_path / "single-vlm-clear-gate.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)
    image_id = int(test_client.test_db.add_image(
        path=str(image_path),
        filename=image_path.name,
        generator="unknown",
    ))
    provider_entered = threading.Event()
    release_provider = threading.Event()
    caption_responses: list[Response] = []
    thread_errors: list[Exception] = []

    class PausedProvider:
        async def caption_image(
            self,
            image_path_arg: str,
            *,
            tags: list[str] | None,
        ) -> VLMResult:
            assert image_path_arg == str(image_path)
            assert tags == []
            provider_entered.set()
            released = await asyncio.to_thread(release_provider.wait, 5)
            if not released:
                raise TimeoutError("test did not release paused VLM provider")
            return VLMResult(caption="provider completed", model="test-model")

    def run_caption() -> None:
        try:
            caption_responses.append(test_client.post(
                "/api/vlm/caption",
                json={"image_id": image_id},
            ))
        except Exception as exc:
            thread_errors.append(exc)

    monkeypatch.setattr(
        vlm_router,
        "_build_config",
        lambda: vlm_router.VLMConfig(
            endpoint="https://example.test/v1",
            model="test-model",
        ),
    )
    monkeypatch.setattr(vlm_router, "get_provider", lambda config: PausedProvider())
    caption_thread = threading.Thread(target=run_caption, name="test-vlm-caption")
    caption_thread.start()
    assert provider_entered.wait(timeout=2)

    try:
        response = test_client.delete("/api/clear-gallery")
        assert response.status_code == 409
        assert response.json()["code"] == "gallery_clear_jobs_active"
        assert "vlm_caption" in response.json()["jobs"]
        _assert_image_remains(test_client, image_id)
    finally:
        release_provider.set()
        caption_thread.join(timeout=5)

    assert not caption_thread.is_alive()
    assert thread_errors == []
    assert caption_responses[0].status_code == 200


def test_clear_gallery_rejects_unknown_smart_tag_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from services import tagging_pipeline_service

    image_id = _add_indexed_image(test_client)

    def raise_smart_probe_error() -> NoReturn:
        raise RuntimeError("injected Smart Tag status failure")

    monkeypatch.setattr(
        tagging_pipeline_service.smart_tag_service,
        "get_active_job",
        raise_smart_probe_error,
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_smart_tag_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from services import tagging_pipeline_service

    image_id = _add_indexed_image(test_client)
    monkeypatch.setattr(
        tagging_pipeline_service.smart_tag_service,
        "get_active_job",
        lambda: SimpleNamespace(job_id="smart-malformed", status="unexpected"),
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_vlm_state(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    import routers.vlm as vlm_router

    image_id = _add_indexed_image(test_client)
    monkeypatch.setitem(vlm_router._batch_state, "running", "false")

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_malformed_aesthetic_state(
    test_client: _ApiTestClient,
    isolated_aesthetic_service: AestheticService,
    monkeypatch: MonkeyPatch,
) -> None:
    image_id = _add_indexed_image(test_client)
    monkeypatch.setattr(
        isolated_aesthetic_service,
        "get_scoring_progress",
        lambda: {"running": "false"},
    )

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 503
    assert response.json()["code"] == "gallery_clear_status_unknown"
    _assert_image_remains(test_client, image_id)


def test_clear_gallery_rejects_queued_ai_tag(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    import routers.vlm as vlm_router
    from routers import tags as tags_router
    from services import tagging_pipeline_service
    from services.tagging_pipeline_service import get_tagging_pipeline_service
    from services.tagging_service import TagRequest

    image_id = _add_indexed_image(test_client)
    monkeypatch.setattr(
        tagging_pipeline_service.smart_tag_service,
        "get_active_job",
        lambda: SimpleNamespace(job_id="smart-active", status="running"),
    )
    monkeypatch.setattr(vlm_router, "is_caption_batch_active", lambda: False)
    pipeline = get_tagging_pipeline_service()
    queued = pipeline.start_gallery_tagging(
        TagRequest(image_ids=[image_id]),
        BackgroundTasks(),
        legacy_service=tags_router.get_tagging_service(),
    )
    assert queued["status"] == "queued"

    response = test_client.delete("/api/clear-gallery")

    assert response.status_code == 409
    assert response.json()["code"] == "gallery_clear_jobs_active"
    _assert_image_remains(test_client, image_id)


def test_scan_start_waits_until_clear_gallery_deletion_finishes(
    test_client: _ApiTestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from routers import sorting as sorting_router
    from services.sorting_models import SCAN_SOURCE_MANUAL, ScanRequest

    _add_indexed_image(test_client)
    sorting_service = sorting_router.get_sorting_service()
    original_clear_gallery = sorting_service.clear_gallery
    clear_entered = threading.Event()
    release_clear = threading.Event()
    start_attempted = threading.Event()
    start_finished = threading.Event()
    clear_responses: list[Response] = []
    thread_errors: list[Exception] = []
    start_results: list[dict[str, object]] = []

    def paused_clear_gallery() -> dict[str, str]:
        clear_entered.set()
        if not release_clear.wait(timeout=5):
            raise TimeoutError("test did not release paused Clear Gallery deletion")
        return original_clear_gallery()

    def run_clear() -> None:
        try:
            clear_responses.append(test_client.delete("/api/clear-gallery"))
        except Exception as exc:
            thread_errors.append(exc)

    def run_scan_start() -> None:
        start_attempted.set()
        try:
            result = sorting_service.start_scan(
                ScanRequest(
                    folder_path=str(tmp_path),
                    recursive=False,
                    quick_import=True,
                    cleanup_missing=False,
                    force_reparse=False,
                ),
                BackgroundTasks(),
                SCAN_SOURCE_MANUAL,
            )
            start_results.append(dict(result))
        except Exception as exc:
            thread_errors.append(exc)
        finally:
            start_finished.set()

    monkeypatch.setattr(sorting_service, "clear_gallery", paused_clear_gallery)
    clear_thread = threading.Thread(target=run_clear, name="test-clear-gallery")
    start_thread = threading.Thread(target=run_scan_start, name="test-scan-start")
    clear_thread.start()
    assert clear_entered.wait(timeout=2)
    start_thread.start()
    assert start_attempted.wait(timeout=2)

    try:
        assert not start_finished.wait(timeout=0.2)
    finally:
        release_clear.set()
        clear_thread.join(timeout=5)
        start_thread.join(timeout=5)

    assert not clear_thread.is_alive()
    assert not start_thread.is_alive()
    assert thread_errors == []
    assert clear_responses[0].status_code == 200
    assert start_results[0]["status"] == "started"


def test_queued_ai_dispatch_waits_for_gallery_transition(
    test_client: _ApiTestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    import routers.vlm as vlm_router
    from routers import tags as tags_router
    from services import tagging_pipeline_service
    from services.gallery_job_gate import gallery_job_transition
    from services.tagging_pipeline_service import get_tagging_pipeline_service
    from services.tagging_service import TagRequest

    image_id = _add_indexed_image(test_client)
    pipeline = get_tagging_pipeline_service()
    legacy_service = tags_router.get_tagging_service()
    smart_job: dict[str, SimpleNamespace | None] = {
        "value": SimpleNamespace(job_id="smart-active", status="running")
    }
    monkeypatch.setattr(
        tagging_pipeline_service.smart_tag_service,
        "get_active_job",
        lambda: smart_job["value"],
    )
    monkeypatch.setattr(vlm_router, "is_caption_batch_active", lambda: False)

    queued = pipeline.start_gallery_tagging(
        TagRequest(image_ids=[image_id]),
        BackgroundTasks(),
        legacy_service=legacy_service,
    )
    assert queued["status"] == "queued"

    started_image_ids: list[list[int]] = []

    def record_start(request: TagRequest, background_tasks: object) -> dict[str, str]:
        del background_tasks
        started_image_ids.append(list(request.image_ids))
        return {"status": "started"}

    monkeypatch.setattr(legacy_service, "start_tagging", record_start)
    smart_job["value"] = None

    dispatch_attempted = threading.Event()
    dispatch_finished = threading.Event()
    dispatch_results: list[bool] = []
    thread_errors: list[Exception] = []

    def run_dispatch() -> None:
        dispatch_attempted.set()
        try:
            dispatch_results.append(pipeline.dispatch_pending_once())
        except Exception as exc:
            thread_errors.append(exc)
        finally:
            dispatch_finished.set()

    dispatch_thread = threading.Thread(target=run_dispatch, name="test-ai-dispatch")
    with gallery_job_transition():
        dispatch_thread.start()
        assert dispatch_attempted.wait(timeout=2)
        assert not dispatch_finished.wait(timeout=0.2)

    dispatch_thread.join(timeout=5)

    assert not dispatch_thread.is_alive()
    assert thread_errors == []
    assert dispatch_results == [True]
    assert started_image_ids == [[image_id]]
