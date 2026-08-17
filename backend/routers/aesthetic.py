"""
Aesthetic scoring endpoints.
Uses LAION Aesthetic Predictor (CLIP + linear head) to score images 1-10.
"""
import logging
import threading
from functools import partial

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ai_runtime_guard import PRIORITY_INTERACTIVE
from exceptions import ImageNotFoundError, ServiceError
from services.aesthetic_service import AestheticService
from services.gallery_job_gate import gallery_job_activity, gallery_job_transition
from services.service_provider import ServiceProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["aesthetic"])
_aesthetic_service_provider = ServiceProvider(AestheticService)
get_aesthetic_service = _aesthetic_service_provider.get
set_aesthetic_service = _aesthetic_service_provider.set

# Track whether each per-router error path has already logged its WARNING in
# this process. The frontend polls /api/aesthetic/status (and may re-call
# the score endpoints) repeatedly, and emitting the same "torch import
# failed" line on every call buries other useful log entries. The
# aesthetic module's own is_available() also de-duplicates, but its cache
# only covers the success path; the router's HTTPException paths need
# their own one-shot guard.
_router_warning_lock = threading.Lock()
_router_status_warning_logged = False
_router_score_warning_logged = False


def _log_router_warning_once(slot: str, message: str, exc: BaseException) -> None:
    """Emit a router-level WARNING the first time, then DEBUG afterwards."""
    global _router_status_warning_logged, _router_score_warning_logged
    with _router_warning_lock:
        if slot == "status":
            already = _router_status_warning_logged
            _router_status_warning_logged = True
        else:
            already = _router_score_warning_logged
            _router_score_warning_logged = True
    if already:
        logger.debug("%s: %s", message, exc)
    else:
        logger.warning("%s: %s", message, exc)


@router.get("/aesthetic/status")
def aesthetic_status(service: AestheticService = Depends(get_aesthetic_service)):
    """Check if the aesthetic predictor is available and how many images are scored."""
    try:
        from aesthetic import is_available
        return service.get_status(is_available)
    except (ImportError, OSError) as exc:
        # OSError covers Windows DLL load failures (e.g. broken cudnn). Without
        # it, this endpoint returns 500 instead of a clean "not available"
        # status, breaking the aesthetic settings panel for any user with a
        # damaged torch runtime.
        _log_router_warning_once("status", "Aesthetic predictor unavailable", exc)
        return {
            "available": False,
            "message": "Aesthetic predictor dependencies are not installed or runtime is broken",
            "scored_count": service.get_status(lambda: False)["scored_count"],
        }


@router.post("/aesthetic/score/{image_id}")
def score_single_image(
    image_id: int,
    service: AestheticService = Depends(get_aesthetic_service),
):
    """Score a single image by database ID."""
    with gallery_job_activity("aesthetic"):
        try:
            from aesthetic import predict_score
        except (ImportError, OSError) as exc:
            _log_router_warning_once("score", "Aesthetic predictor torch import failed", exc)
            raise HTTPException(status_code=503, detail="Aesthetic predictor dependencies not installed or runtime is broken")

        try:
            return service.score_single_image(
                image_id=image_id,
                # One image with the user waiting, so this claims the interactive
                # lane. Bound here rather than inside predict_score, which the
                # score-all job below also calls.
                predict_score=partial(predict_score, priority=PRIORITY_INTERACTIVE),
            )
        except ImageNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.message)
        except ServiceError as exc:
            raise HTTPException(status_code=500, detail=exc.message)


# Held across the is-running check and start_scoring_progress so two
# concurrent /score-all requests cannot both observe "not running" and start
# twice (same single-lock start pattern as routers/colors.py start_analysis).
_scoring_start_lock = threading.Lock()


@router.post("/aesthetic/score-all")
def score_all_images(
    background_tasks: BackgroundTasks,
    force: bool = Query(False),
    service: AestheticService = Depends(get_aesthetic_service),
):
    """Score all unscored images in background. Use force=true to rescore all."""
    with gallery_job_transition(), _scoring_start_lock:
        if service.is_scoring_running():
            return {"status": "already_running", **service.get_scoring_progress()}

        try:
            from aesthetic import is_available
            if not is_available():
                raise HTTPException(status_code=503, detail="Aesthetic predictor dependencies not installed")
        except (ImportError, OSError) as exc:
            _log_router_warning_once("score", "Aesthetic predictor torch import failed", exc)
            raise HTTPException(status_code=503, detail="Aesthetic predictor dependencies not installed or runtime is broken")

        total = service.count_images_to_score(force=force)
        service.start_scoring_progress(total=total)

    background_tasks.add_task(_score_batch, force)
    return {"status": "started", "total": total}


def _score_batch(force: bool = False):
    """Background task to score all images."""
    from aesthetic import predict_score

    service = get_aesthetic_service()
    try:
        service.score_batch(
            force=force,
            predict_score=predict_score,
            progress_callback=service.apply_scoring_progress_update,
        )
        service.finish_scoring_progress()
    except Exception as exc:
        logger.error("Aesthetic batch job failed: %s", exc)
        # Surface the error via the progress endpoint so the UI can show a
        # toast instead of treating it as a clean completion.
        service.finish_scoring_progress(error=str(exc))


@router.post("/aesthetic/cancel")
def cancel_scoring(service: AestheticService = Depends(get_aesthetic_service)):
    """Request cancellation of the running aesthetic scoring batch."""
    cancelled = service.request_cancel()
    return {"status": "cancelled" if cancelled else "not_running"}


@router.get("/aesthetic/progress")
def scoring_progress(service: AestheticService = Depends(get_aesthetic_service)):
    """Get the progress of background aesthetic scoring."""
    return service.get_scoring_progress()
