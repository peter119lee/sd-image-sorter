"""Duplicate-cleanup workflow endpoints (v3.5.0 Tier 1).

Wraps services.duplicate_group_service in the shared bulk-job machinery:
the scan runs in the background with progress + cancel via the existing
``GET /api/bulk-jobs/{id}`` polling; results persist across restarts.

Deletion intentionally reuses the existing trash-backed delete endpoints —
this router only produces the review data.
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from services import duplicate_group_service
from services.bulk_job_service import (
    BulkJobHandle,
    JOB_KIND_DUPLICATE_SCAN,
    TERMINAL_STATUSES,
    get_bulk_job_service,
)

router = APIRouter(prefix="/api", tags=["duplicates"])


class DuplicateScanRequest(BaseModel):
    threshold: float = Field(
        default=duplicate_group_service.DEFAULT_THRESHOLD,
        ge=duplicate_group_service.MIN_THRESHOLD,
        le=duplicate_group_service.MAX_THRESHOLD,
        description="Cosine similarity floor for two images to count as near-duplicates",
    )


@router.post(
    "/duplicates/scan",
    summary="Start a whole-library duplicate group scan",
    description="""
Scans every CLIP-embedded image into near-duplicate GROUPS (union-find over
neighbor pairs >= threshold) with a suggested keeper per group, ranked by
user rating, aesthetic score, resolution, then file size. Runs as a bulk
background job — poll `GET /api/bulk-jobs/{job_id}`. Only one scan runs at
a time (409 otherwise). Requires CLIP embeddings (build them in Similarity
tools first).
    """,
)
def start_duplicate_scan(request: DuplicateScanRequest, background_tasks: BackgroundTasks):
    """Kick off the background duplicate-group scan."""
    service = get_bulk_job_service()
    job_id = service.create_job(JOB_KIND_DUPLICATE_SCAN, total=100, message="Queued")
    if not duplicate_group_service.claim_active_job_id(job_id):
        rejected_job = service.cancel_job(job_id)
        if rejected_job is None:
            raise RuntimeError(
                f"Duplicate scan job disappeared before rejection: job_id={job_id}"
            )
        raise HTTPException(status_code=409, detail="A duplicate scan is already running")
    threshold = request.threshold

    def _worker(handle: BulkJobHandle) -> None:
        duplicate_group_service.run_duplicate_scan(handle, threshold=threshold)

    def _run_job() -> None:
        try:
            service.run_job(job_id, _worker)
        finally:
            duplicate_group_service.release_active_job_id(job_id)

    background_tasks.add_task(_run_job)
    return {"job_id": job_id, "threshold": threshold}


@router.get(
    "/duplicates/scan-status",
    summary="Get the active duplicate scan job id (if any)",
)
def get_scan_status():
    """Return the running scan's job id so a reopened UI can re-attach."""
    job_id = duplicate_group_service.get_active_job_id()
    job = get_bulk_job_service().get_job(job_id) if job_id else None
    active = bool(job is not None and job["status"] not in TERMINAL_STATUSES)
    return {"active": active, "job_id": job_id if active else None, "job": job}


@router.get(
    "/duplicates/groups",
    summary="Page through the last duplicate scan's groups",
    description="""
Returns the persisted result of the most recent scan: summary counts
(groups / redundant images / reclaimable bytes) plus a page of groups.
Each group lists members sorted best-first with `suggested_keep` flags.
`available: false` means no scan has completed yet.

The summary also carries coverage: `total_images`, `embedded_count`,
`pending_count` and `coverage`. `exact: false` means only the embedded
subset was compared, so a `group_count` of 0 is not a whole-library
verdict — the pending images need embeddings before it can be.
    """,
)
def get_duplicate_groups(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Serve the persisted duplicate groups for the review UI."""
    return duplicate_group_service.get_groups_page(offset=offset, limit=limit)
