"""Masked-training mask endpoints (Phase 4 mask editor).

Masks are auxiliary trainer inputs (white = train, black = ignore) stored
per gallery image; see ``services/mask_service.py`` for semantics. All
mutation routes validate the image id against the library first.
"""
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.bulk_job_service import (
    JOB_KIND_MASK_AUTO_BATCH,
    BulkJobAlreadyRunningError,
    BulkJobHandle,
    get_bulk_job_service,
)

router = APIRouter(prefix="/api", tags=["masks"])


class MaskSaveRequest(BaseModel):
    data_url: str = Field(..., min_length=32)


class MaskStatusRequest(BaseModel):
    image_ids: List[int] = Field(..., min_length=1)


class MaskAutoRequest(BaseModel):
    method: str = Field(default="rembg", max_length=32)


class MaskAutoBatchRequest(BaseModel):
    image_ids: List[int] = Field(..., min_length=1, max_length=5000)
    method: str = Field(default="lucida", max_length=32)
    overwrite: bool = False


@router.get("/masks/{image_id}")
def get_mask(image_id: int):
    """Stored training mask as PNG; 404 = no mask (train the whole image)."""
    from services import mask_service

    path = mask_service.get_mask_file(image_id)
    if path is None:
        raise HTTPException(404, f"No mask stored for image {image_id}")
    return FileResponse(str(path), media_type="image/png")


@router.put("/masks/{image_id}")
def save_mask(image_id: int, request: MaskSaveRequest):
    """Save a canvas-edited mask (base64 data URL -> grayscale PNG)."""
    from services import mask_service

    try:
        return mask_service.save_mask_from_data_url(image_id, request.data_url)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except mask_service.MaskError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/masks/{image_id}")
def delete_mask(image_id: int):
    """Remove the stored mask (the image reverts to fully trained)."""
    from services import mask_service

    removed = mask_service.delete_mask(image_id)
    return {"removed": removed, "image_id": image_id}


@router.post("/masks/status")
def mask_status(request: MaskStatusRequest):
    """Which of these images carry a stored mask (queue badge data)."""
    from services import mask_service

    return {"masks": mask_service.mask_status(request.image_ids)}


@router.post("/masks/auto-batch", status_code=202)
def start_auto_mask_batch(
    request: MaskAutoBatchRequest,
    background_tasks: BackgroundTasks,
):
    """Generate and save training masks for many gallery images.

    Skips images that already have a mask unless ``overwrite`` is true.
    Poll ``GET /api/bulk-jobs/{job_id}``.
    """
    from services import mask_service

    method = str(request.method or "lucida").strip().lower()
    if method not in mask_service.VALID_AUTO_METHODS:
        raise HTTPException(
            400,
            f"Unknown auto-mask method {method!r}; supported: {', '.join(mask_service.VALID_AUTO_METHODS)}",
        )
    service = get_bulk_job_service()
    try:
        job_id = service.create_single_flight_job(
            JOB_KIND_MASK_AUTO_BATCH,
            total=len(request.image_ids),
            message="Queued auto-mask batch",
            queued_cancel_result={"saved": 0, "skipped": 0, "error_count": 0, "cancelled": True},
        )
    except BulkJobAlreadyRunningError as exc:
        raise HTTPException(409, "Auto-mask batch already in progress") from exc

    def worker(handle: BulkJobHandle) -> None:
        _run_mask_auto_batch(request, handle)

    background_tasks.add_task(service.run_job, job_id, worker)
    return {
        "id": job_id,
        "job_id": job_id,
        "kind": JOB_KIND_MASK_AUTO_BATCH,
        "status": "queued",
        "total": len(request.image_ids),
        "processed": 0,
        "message": "Queued",
    }


@router.post("/masks/{image_id}/auto")
def auto_mask(image_id: int, request: MaskAutoRequest):
    """Generate a subject-mask preview with the selected engine; do not save it."""
    from services import mask_service

    try:
        return mask_service.generate_auto_mask(image_id, request.method)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except mask_service.MaskError as exc:
        raise HTTPException(400, str(exc))


def _run_mask_auto_batch(payload: MaskAutoBatchRequest, handle: BulkJobHandle) -> None:
    from services import mask_service

    def update_progress(**kwargs) -> None:
        handle.set_progress(
            processed=int(kwargs.get("processed") or 0),
            total=int(kwargs.get("total") or 0),
            message=(
                f"Auto-mask {kwargs.get('processed')}/{kwargs.get('total')} "
                f"(saved {kwargs.get('saved')}, skipped {kwargs.get('skipped')})"
            ),
        )

    report = mask_service.run_auto_mask_batch(
        payload.image_ids,
        payload.method,
        overwrite=payload.overwrite,
        cancellation_requested=lambda: handle.cancelled,
        progress_callback=update_progress,
    )
    handle.complete_result(
        result=report,
        processed=int(report.get("saved") or 0) + int(report.get("skipped") or 0) + int(report.get("error_count") or 0),
        total=int(report.get("total") or 0),
        message=(
            f"Auto-mask finished: saved {report.get('saved')}, "
            f"skipped {report.get('skipped')}, errors {report.get('error_count')}"
        ),
    )
