"""Bulk job endpoints: delete-selected/* · remove-selected/* · bulk-jobs/* (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 1309-1544 (registration
position 7 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from typing import Union

from fastapi import BackgroundTasks, Depends, HTTPException, Path as FastAPIPath, Query

from routers.images import (
    BulkJobEnvelopeResponse,
    DeleteSelectedImagesRequest,
    DeleteSelectedImagesResponse,
    RemoveSelectedImagesRequest,
    RemoveSelectedImagesResponse,
    get_image_service,
    router,
)
from services.bulk_job_service import get_bulk_job_service
from services.image_service import ImageService


@router.post(
    "/images/delete-selected",
    response_model=Union[DeleteSelectedImagesResponse, BulkJobEnvelopeResponse],
    summary="Move selected image files to OS trash",
    description="""
Move the selected image files to the operating system Trash / Recycle Bin and
remove their database rows.

This is a destructive action and requires explicit confirmation from the client.
The response reports partial failures per image instead of hiding them. The
backend must not fall back to permanent deletion when trash is unavailable.
    """,
)
async def delete_selected_images(
    request: DeleteSelectedImagesRequest,
    background_tasks: BackgroundTasks,
    service: ImageService = Depends(get_image_service),
):
    """Move selected image files to OS trash with partial-failure reporting."""
    if not request.confirm_delete_files:
        raise HTTPException(
            status_code=400,
            detail="Deleting image files requires explicit confirmation",
        )

    # Debt-22: opt into a durable-id background job for large selections. The
    # ids are snapshotted server-side before any file is trashed; poll via
    # GET /api/bulk-jobs/{job_id} and cancel via POST /api/bulk-jobs/{job_id}/cancel.
    if request.background:
        return service.start_delete_bulk_job(request, background_tasks)

    if request.selection_token:
        return service.delete_selected_image_files_by_token(request.selection_token)
    return service.delete_selected_image_files(request.image_ids or [])


@router.post(
    "/images/delete-selected/start",
    summary="Start a background delete-to-trash job for selected images",
    description="""
Move the selected image files to the OS Trash / Recycle Bin and remove their
database rows as a **background job** with progress polling. Cloned from the
gallery move job (``/api/move/start``) so large selections stream progress
instead of freezing the request.

This is a destructive action and requires explicit confirmation from the client.
The selected ids are snapshotted server-side before any deletion. The final
progress payload reports ``deleted`` and per-image ``failed`` entries, matching
the synchronous endpoint's shape.
    """,
)
async def start_delete_selected_images_job(
    request: DeleteSelectedImagesRequest,
    background_tasks: BackgroundTasks,
    service: ImageService = Depends(get_image_service),
):
    """Start the background delete-to-trash job with partial-failure reporting."""
    if not request.confirm_delete_files:
        raise HTTPException(
            status_code=400,
            detail="Deleting image files requires explicit confirmation",
        )
    return service.start_delete_selected_job(request, background_tasks)


@router.get(
    "/images/delete-selected/progress",
    summary="Get delete-to-trash job progress",
)
async def get_delete_selected_images_progress(
    service: ImageService = Depends(get_image_service),
):
    """Get current gallery delete-to-trash job progress."""
    return service.get_delete_progress()


@router.post(
    "/images/delete-selected/cancel",
    summary="Stop the delete-to-trash job",
)
async def cancel_delete_selected_images(
    service: ImageService = Depends(get_image_service),
):
    """Request cooperative cancellation of the active delete-to-trash job."""
    return service.cancel_delete()


@router.post(
    "/images/delete-selected/reset",
    summary="Reset a stuck delete-to-trash job",
)
async def reset_delete_selected_images(
    service: ImageService = Depends(get_image_service),
):
    """Reset a stuck delete-to-trash job."""
    return service.reset_delete_progress()


@router.post(
    "/images/remove-selected",
    response_model=Union[RemoveSelectedImagesResponse, BulkJobEnvelopeResponse],
    summary="Remove selected images from the gallery index",
    description="""
Remove selected database rows from the local gallery without deleting the backing
image files from disk. Re-scanning the source folder can add them back later.
    """,
)
async def remove_selected_images(
    request: RemoveSelectedImagesRequest,
    background_tasks: BackgroundTasks,
    service: ImageService = Depends(get_image_service),
):
    """Remove selected images from the gallery index without touching files."""
    # Debt-22: opt into a durable-id background job for large selections.
    if request.background:
        return service.start_remove_bulk_job(request, background_tasks)

    if request.selection_token:
        return service.remove_selected_images_from_gallery_by_token(request.selection_token)
    return service.remove_selected_images_from_gallery(request.image_ids or [])


@router.post(
    "/images/remove-selected/start",
    summary="Start a background remove-from-gallery job for selected images",
    description="""
Remove selected database rows from the local gallery (files stay on disk) as a
**background job** with progress polling. Cloned from the move/delete jobs so
large selections stream progress instead of freezing the request. The selected
ids are snapshotted server-side before any removal.
    """,
)
async def start_remove_selected_images_job(
    request: RemoveSelectedImagesRequest,
    background_tasks: BackgroundTasks,
    service: ImageService = Depends(get_image_service),
):
    """Start the background remove-from-gallery job (DB rows only, files kept)."""
    return service.start_remove_selected_job(request, background_tasks)


@router.get(
    "/images/remove-selected/progress",
    summary="Get remove-from-gallery job progress",
)
async def get_remove_selected_images_progress(
    service: ImageService = Depends(get_image_service),
):
    """Get current gallery remove-from-gallery job progress."""
    return service.get_remove_progress()


@router.post(
    "/images/remove-selected/cancel",
    summary="Stop the remove-from-gallery job",
)
async def cancel_remove_selected_images(
    service: ImageService = Depends(get_image_service),
):
    """Request cooperative cancellation of the active remove-from-gallery job."""
    return service.cancel_remove()


@router.post(
    "/images/remove-selected/reset",
    summary="Reset a stuck remove-from-gallery job",
)
async def reset_remove_selected_images(
    service: ImageService = Depends(get_image_service),
):
    """Reset a stuck remove-from-gallery job."""
    return service.reset_remove_progress()


# ---------------------------------------------------------------------------
# Debt-22: unified durable bulk-job registry (delete / remove / export).
# The operation-specific "start" happens on the existing sync endpoints with
# ``background: true`` (and on POST /api/tags/export-batch); these routes let a
# client poll, cancel, and list any bulk job by its durable id.
# ---------------------------------------------------------------------------
@router.get(
    "/bulk-jobs",
    summary="List bulk background jobs",
    description=(
        "List token-scoped bulk jobs (delete-files / remove-from-gallery / "
        "export-sidecars) tracked by the durable BulkJobService. Pass "
        "``active_only=true`` to hide finished jobs (Debt-22)."
    ),
)
async def list_bulk_jobs(
    active_only: bool = Query(
        default=False,
        description="Only return non-terminal (queued/running) jobs.",
    ),
):
    """List durable bulk background jobs."""
    return {"jobs": get_bulk_job_service().list_jobs(active_only=active_only)}


@router.get(
    "/bulk-jobs/{job_id}",
    summary="Get a bulk background job by id",
    description=(
        "Poll one durable bulk job by id. Returns ``status`` "
        "(queued/running/done/error/cancelled), ``processed``/``total``, "
        "``error_count``, bounded ``error_samples``, and — on completion — the "
        "operation ``result`` (Debt-22)."
    ),
)
async def get_bulk_job(
    job_id: str = FastAPIPath(..., min_length=1, max_length=64),
):
    """Return one durable bulk job's status snapshot."""
    job = get_bulk_job_service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk job not found")
    return job


@router.post(
    "/bulk-jobs/{job_id}/cancel",
    summary="Cancel a bulk background job by id",
    description=(
        "Request cooperative cancellation of a running bulk job. The worker "
        "stops at the next chunk boundary and settles as ``cancelled`` with "
        "partial progress (Debt-22)."
    ),
)
async def cancel_bulk_job(
    job_id: str = FastAPIPath(..., min_length=1, max_length=64),
):
    """Request cancellation of a durable bulk job."""
    job = get_bulk_job_service().cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk job not found")
    return job
