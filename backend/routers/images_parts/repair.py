"""Reconnect/repair endpoints: reconnect-missing/* · repair-candidates · repair-confirm (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 804-937 (registration
position 3 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from fastapi import BackgroundTasks, Depends, Query

from routers.images import (
    ReconnectMissingFilesRequest,
    RepairConfirmRequest,
    get_image_service,
    router,
)
from services.image_service import ImageService


@router.post(
    "/images/reconnect-missing/start",
    summary="Find moved files for missing gallery records",
    description="Start a background search that reconnects missing library records to files found under a user-selected folder. It does not move, delete, or modify image files.",
)
async def start_reconnect_missing_files(
    request: ReconnectMissingFilesRequest,
    background_tasks: BackgroundTasks,
    service: ImageService = Depends(get_image_service),
):
    return service.start_reconnect_missing_files(request, background_tasks)


@router.get(
    "/images/reconnect-missing/progress",
    summary="Get moved-file search progress",
)
async def get_reconnect_missing_files_progress(
    service: ImageService = Depends(get_image_service),
):
    return service.get_reconnect_progress()


@router.post(
    "/images/reconnect-missing/cancel",
    summary="Stop moved-file search",
)
async def cancel_reconnect_missing_files(
    service: ImageService = Depends(get_image_service),
):
    return service.cancel_reconnect_missing_files()


@router.get(
    "/images/repair-candidates",
    summary="List ambiguous missing-file matches awaiting review",
    description="""
Roadmap-C missing-file repair. After a reconnect run, discovered files that
matched several missing library rows by name+size are persisted as *pending*
reviews (the run never touches those rows). This lists them, enriched with each
candidate's current row (path / size / mtime) and whether the candidate's own
file is still missing on disk. Candidate ids deleted since the run are omitted.

Declared above `GET /api/images/{image_id}` so the dynamic-id route does not
shadow it.
    """,
    responses={
        200: {
            "description": "Pending (or scoped) reviews with enriched candidates",
            "content": {
                "application/json": {
                    "example": {
                        "total": 1,
                        "items": [
                            {
                                "review_id": 12,
                                "filename": "same.png",
                                "found_path": "D:/new/same.png",
                                "found_exists": True,
                                "candidate_count": 2,
                                "run_started_at": 1717430000.0,
                                "status": "pending",
                                "resolution": None,
                                "candidates": [
                                    {"image_id": 3, "path": "D:/old/same.png", "file_size": 2048,
                                     "source_mtime_ns": 1700000000000000000, "still_missing": True},
                                    {"image_id": 9, "path": "D:/other/same.png", "file_size": 2048,
                                     "source_mtime_ns": 1700000000000000000, "still_missing": True},
                                ],
                            }
                        ],
                    }
                }
            },
        }
    },
)
async def get_repair_candidates(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str = Query(
        default="pending",
        description="Filter by review status: pending | resolved | conflict | all.",
    ),
    service: ImageService = Depends(get_image_service),
):
    """List persisted ambiguous-match reviews with enriched candidate rows."""
    return service.get_repair_candidates(limit=limit, offset=offset, status=status)


@router.post(
    "/images/repair-confirm",
    summary="Resolve one ambiguous missing-file match",
    description="""
Roadmap-C missing-file repair. Resolve one pending review:

- **pick** — relink `chosen_image_id` to the review's found file.
- **merge** — relink `chosen_image_id` AND delete the other still-existing candidate rows.
- **skip** — record the decision; touch no image rows.

Refuses with 409 while a reconnect run is active. If the found path is already
indexed as a different row, the review is marked `conflict` and 409 is returned
(never silently duplicating a row). Declared above `GET /api/images/{image_id}`.
    """,
    responses={
        200: {
            "description": "Review resolved",
            "content": {
                "application/json": {
                    "example": {
                        "status": "resolved",
                        "review_id": 12,
                        "resolution": "merge",
                        "image_id": 3,
                        "new_path": "D:/new/same.png",
                        "deleted_ids": [9],
                    }
                }
            },
        },
        404: {"description": "Review not found"},
        409: {"description": "Reconnect run active, review already resolved, or found-path conflict"},
    },
)
async def confirm_repair(
    request: RepairConfirmRequest,
    service: ImageService = Depends(get_image_service),
):
    """Resolve one ambiguous missing-file match (pick / merge / skip)."""
    return service.confirm_repair(
        review_id=request.review_id,
        action=request.action,
        chosen_image_id=request.chosen_image_id,
    )
