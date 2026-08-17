"""A busy AI runtime must answer 409 and name the blocker, never 500 or 503.

When the bounded cross-process wait expires, ``AiRuntimeBusyError`` reaches
the router layer. Two things must then be true, and both were false before:

* the status code says "conflict" (something else legitimately holds the
  runtime and the request can be retried), not "server error" (the app is
  broken) and not "service unavailable" (the model is missing or damaged --
  which would send the user to the Model Center to fix nothing);
* the body names what is holding the runtime, using the same ``label`` /
  ``elapsed_seconds`` vocabulary ``GET /api/system/ai-jobs`` already
  publishes, so the UI does not need a second source of truth.
"""
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_runtime_guard import (  # noqa: E402
    AiRuntimeBusyError,
    REASON_BUSY,
    REASON_STALE_LOCK,
)


def _png(path: Path) -> Path:
    Image.new("RGB", (16, 16), "white").save(path, "PNG")
    return path


def _busy(reason: str = REASON_BUSY) -> AiRuntimeBusyError:
    return AiRuntimeBusyError(
        "gallery-tag is using the AI runtime (running 47s). Waited 180s.",
        reason=reason,
        blocker={
            "scope": "process",
            "pid": 4242,
            "label": "gallery-tag",
            "elapsed_seconds": 47.0,
            "holder_alive": True,
        },
        waited_seconds=180.0,
        timeout_seconds=180.0,
    )


def test_single_image_tag_answers_409_naming_the_blocking_job(
    test_client, monkeypatch, tmp_path
):
    from services import single_image_tag_service

    def _busy_loader(**_kwargs):
        raise _busy()

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _busy_loader)

    response = test_client.post(
        "/api/tag/single", json={"image_path": str(_png(tmp_path / "busy.png"))}
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["type"] == "AiRuntimeBusyError"
    assert body["reason"] == REASON_BUSY
    assert body["blocker"]["label"] == "gallery-tag"
    assert body["blocker"]["elapsed_seconds"] == 47.0
    assert body["waited_seconds"] == 180.0
    # formatApiError renders "error" verbatim, so the sentence must be there.
    assert "gallery-tag" in body["error"]


def test_single_image_tag_passes_through_a_stale_lock_reason(
    test_client, monkeypatch, tmp_path
):
    """A dead owner needs different advice, so the reason must survive the hop."""
    from services import single_image_tag_service

    def _stale_loader(**_kwargs):
        raise _busy(REASON_STALE_LOCK)

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _stale_loader)

    response = test_client.post(
        "/api/tag/single", json={"image_path": str(_png(tmp_path / "stale.png"))}
    )

    assert response.status_code == 409, response.text
    assert response.json()["reason"] == REASON_STALE_LOCK


def test_single_image_tag_still_reports_a_real_tagger_failure_as_503(
    test_client, monkeypatch, tmp_path
):
    """The 409 branch must not have widened into "every failure is busy"."""
    from services import single_image_tag_service

    def _broken(**_kwargs):
        raise RuntimeError("ONNX Runtime session could not be created")

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _broken)

    response = test_client.post(
        "/api/tag/single", json={"image_path": str(_png(tmp_path / "broken.png"))}
    )

    assert response.status_code == 503, response.text
    assert "ONNX Runtime" in response.json()["error"]


def test_similarity_embed_answers_409_when_clip_is_queued_behind_a_batch(
    test_client, monkeypatch
):
    from services import similarity_service

    def _busy_clip():
        raise _busy()

    monkeypatch.setattr(similarity_service, "ensure_clip_model_ready", _busy_clip)

    response = test_client.post("/api/similarity/embed", json={"image_ids": [1]})

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["reason"] == REASON_BUSY
    assert body["blocker"]["label"] == "gallery-tag"


def test_similarity_embed_still_reports_a_missing_clip_model_as_503(
    test_client, monkeypatch
):
    from services import similarity_service

    def _missing():
        raise RuntimeError("CLIP model weights are not installed")

    monkeypatch.setattr(similarity_service, "ensure_clip_model_ready", _missing)

    response = test_client.post("/api/similarity/embed", json={"image_ids": [1]})

    assert response.status_code == 503, response.text
    assert "CLIP model weights" in response.json()["error"]


@pytest.mark.parametrize("reason", [REASON_BUSY, REASON_STALE_LOCK])
def test_a_busy_error_without_a_blocker_still_answers_409(
    test_client, monkeypatch, tmp_path, reason
):
    """An unnamed holder must still produce a usable answer, not a 500."""
    from services import single_image_tag_service

    def _anonymous(**_kwargs):
        raise AiRuntimeBusyError(
            "Another process is using the AI runtime. Waited 180s.", reason=reason
        )

    monkeypatch.setattr(single_image_tag_service, "_load_tagger", _anonymous)

    response = test_client.post(
        "/api/tag/single", json={"image_path": str(_png(tmp_path / "anon.png"))}
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["reason"] == reason
    assert body["blocker"] is None
    assert "AI runtime" in body["error"]
