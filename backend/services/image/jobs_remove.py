"""Remove-from-gallery (DB rows only, no file ops): the Phase-1 singleton
background job, the synchronous endpoints, and the Debt-22 bulk-job path.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: the facade-owned
DELETE_FETCH_CHUNK resolves through _svc() at call time. The lazy in-method
import of sorting_service.invalidate_library_health_cache is original code,
kept verbatim.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import database as db
from services.bulk_job_service import (
    JOB_KIND_REMOVE_FROM_GALLERY,
    get_bulk_job_service,
)

# NOTE(decomposition): keep the historical logger channel so log routing and
# output stay byte-identical after the package split.
logger = logging.getLogger("services.image_service")


def _svc():
    """Resolve facade-owned seams/constants through services.image_service at call time.

    Tests patch module attributes on the facade; a ``from`` import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.image_service as image_service

    return image_service


class RemoveJobsMixin:
    """Remove-from-gallery job slice of ImageService (assembled in services/image_service.py)."""

    def _remove_selected_image_id_chunk(self, image_ids: List[int]) -> Dict[str, Any]:
        normalized_ids: List[int] = []
        seen_ids = set()
        for raw_image_id in image_ids or []:
            image_id = int(raw_image_id)
            if image_id <= 0 or image_id in seen_ids:
                continue
            seen_ids.add(image_id)
            normalized_ids.append(image_id)

        if not normalized_ids:
            return {"removed": 0, "missing_ids": []}

        existing_ids = {
            int(image_id)
            for image_id, image in db.get_images_by_ids(normalized_ids).items()
            if image
        }
        removed = db.delete_images_by_ids(normalized_ids)
        missing_ids = [image_id for image_id in normalized_ids if image_id not in existing_ids]

        if removed:
            # Removing rows (e.g. deleting the broken/unreadable images) changes
            # the cached library-health counts that feed the "N images can't
            # open" gallery banner. Same staleness class as clear_gallery — drop
            # the cache so the banner reflects the smaller library immediately
            # instead of lingering on the pre-delete count for the 60s TTL.
            from services.sorting_service import invalidate_library_health_cache
            invalidate_library_health_cache()

        return {"removed": removed, "missing_ids": missing_ids}

    def remove_selected_images_from_gallery(self, image_ids: List[int]) -> Dict[str, Any]:
        """Remove images from the local gallery index without deleting files."""
        removed = 0
        missing_ids: List[int] = []

        normalized_ids: List[int] = []
        seen_ids = set()
        for raw_image_id in image_ids or []:
            image_id = int(raw_image_id)
            if image_id <= 0 or image_id in seen_ids:
                continue
            seen_ids.add(image_id)
            normalized_ids.append(image_id)

        for batch_start in range(0, len(normalized_ids), 500):
            result = self._remove_selected_image_id_chunk(normalized_ids[batch_start:batch_start + 500])
            removed += int(result.get("removed", 0) or 0)
            missing_ids.extend(result.get("missing_ids", []) or [])

        return {
            "removed": removed,
            "missing_ids": missing_ids,
            "permanent_delete": False,
        }

    def remove_selected_images_from_gallery_by_token(self, selection_token: str) -> Dict[str, Any]:
        """Remove token-selected images from the gallery index in bounded chunks."""
        removed = 0
        missing_ids: List[int] = []
        for batch_ids in self._iter_selection_token_snapshot_chunks(selection_token, chunk_size=500):
            result = self._remove_selected_image_id_chunk(batch_ids)
            removed += int(result.get("removed", 0) or 0)
            missing_ids.extend(result.get("missing_ids", []) or [])

        return {
            "removed": removed,
            "missing_ids": missing_ids,
            "permanent_delete": False,
        }

    @staticmethod
    def _build_default_remove_progress_state() -> Dict[str, Any]:
        """Idle progress payload for the background remove-from-gallery job.

        Mirrors the delete job but reports ``removed`` + ``missing_ids`` (the
        fields the frontend already consumes from the synchronous
        /remove-selected response) instead of ``deleted`` + ``failed``.
        """
        return {
            "status": "idle",
            "step": "idle",
            "current": 0,
            "total": 0,
            "message": "",
            "removed": 0,
            "missing_ids": [],
            "current_item": None,
            "operation": "remove",
            "permanent_delete": False,
            "started_at": None,
            "updated_at": None,
        }

    def get_remove_progress(self) -> Dict[str, Any]:
        with self._remove_lock:
            return self._remove_progress.copy()

    def reset_remove_progress(self) -> Dict[str, Any]:
        with self._remove_lock:
            if self._remove_progress["status"] == "running":
                return {"status": "running", "message": "Cannot reset a running job"}
            self._remove_progress = self._build_default_remove_progress_state()
            return {"status": self._remove_progress["status"], "message": "Nothing to reset"}

    def cancel_remove(self) -> Dict[str, Any]:
        with self._remove_lock:
            current_status = self._remove_progress.get("status")
            if current_status not in {"running", "cancelling"}:
                return {"status": current_status, "message": "No remove in progress"}
            if self._remove_cancel_event is not None:
                self._remove_cancel_event.set()
            self._remove_progress["status"] = "cancelling"
            self._remove_progress["step"] = "cancelling"
            self._remove_progress["message"] = "Stopping remove..."
            self._remove_progress["updated_at"] = time.time()
            return {"status": "cancelling", "message": "Stopping remove..."}

    def _set_remove_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        with self._remove_lock:
            if run_id != self._remove_run_id:
                return False
            self._remove_progress = state
            return True

    def _update_remove_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        with self._remove_lock:
            if run_id != self._remove_run_id:
                return False
            self._remove_progress = {**self._remove_progress, **updates}
            return True

    def _expand_remove_request_ids(
        self, image_ids: Optional[List[int]], selection_token: Optional[str]
    ) -> List[int]:
        """Resolve a remove request into a deduped image-id snapshot (mirrors
        ``_expand_delete_request_ids``; token rows are frozen before mutation)."""
        if selection_token:
            snapshot: List[int] = []
            for chunk in self._iter_selection_token_snapshot_chunks(selection_token, chunk_size=500):
                snapshot.extend(chunk)
            return snapshot
        normalized: List[int] = []
        seen: set = set()
        for raw_image_id in image_ids or []:
            image_id = int(raw_image_id)
            if image_id <= 0 or image_id in seen:
                continue
            seen.add(image_id)
            normalized.append(image_id)
        return normalized

    def start_remove_selected_job(self, request: Any, background_tasks: Any) -> Dict[str, Any]:
        """v3.3.2 Phase-1: gallery "remove from gallery" as a background job with
        progress polling, cloned from ``start_delete_selected_job``. DB-only (no
        file ops); the final payload embeds ``removed`` + ``missing_ids`` so the
        frontend mapping matches the synchronous ``/remove-selected`` endpoint.
        The id set is snapshotted before the worker starts.
        """
        with self._remove_lock:
            if self._remove_progress["status"] in {"running", "cancelling"}:
                raise HTTPException(status_code=409, detail="A remove is already in progress")

        image_ids = self._expand_remove_request_ids(
            getattr(request, "image_ids", None),
            getattr(request, "selection_token", None),
        )
        total_count = len(image_ids)
        if total_count == 0:
            return {
                "status": "done",
                "message": "No images to remove",
                "removed": 0,
                "missing_ids": [],
                "total": 0,
                "operation": "remove",
                "permanent_delete": False,
            }

        cancel_event = threading.Event()
        with self._remove_lock:
            self._remove_run_id += 1
            run_id = self._remove_run_id
            self._remove_cancel_event = cancel_event
            self._remove_progress = {
                **self._build_default_remove_progress_state(),
                "status": "running",
                "step": "starting",
                "current": 0,
                "total": total_count,
                "message": f"Starting remove of {total_count} images...",
                "started_at": time.time(),
                "updated_at": time.time(),
            }

        def run_remove():
            removed = 0
            processed = 0
            missing_ids: List[int] = []
            try:
                def _write_cancelled_state() -> None:
                    self._set_remove_progress_if_current(
                        run_id,
                        {
                            **self._build_default_remove_progress_state(),
                            "status": "cancelled",
                            "step": "cancelled",
                            "current": processed,
                            "total": total_count,
                            "removed": removed,
                            "missing_ids": missing_ids,
                            "message": (
                                f"Cancelled at {processed}/{total_count}. "
                                f"Removed {removed} records so far."
                            ),
                            "operation": "remove",
                            "started_at": self._remove_progress.get("started_at"),
                            "updated_at": time.time(),
                        },
                    )

                # Walk the snapshotted id list in chunks (DB-only); cancel is
                # honored at each chunk boundary.
                for start in range(0, total_count, _svc().DELETE_FETCH_CHUNK):
                    if cancel_event.is_set():
                        _write_cancelled_state()
                        return
                    chunk_ids = image_ids[start:start + _svc().DELETE_FETCH_CHUNK]
                    result = self._remove_selected_image_id_chunk(chunk_ids)
                    removed += int(result.get("removed", 0) or 0)
                    missing_ids.extend(result.get("missing_ids", []) or [])
                    processed += len(chunk_ids)
                    if not self._update_remove_progress_if_current(
                        run_id,
                        step="removing",
                        current=processed,
                        total=total_count,
                        removed=removed,
                        missing_ids=missing_ids,
                        message=f"Removed {removed} records ({processed}/{total_count})",
                        operation="remove",
                        updated_at=time.time(),
                    ):
                        return

                self._set_remove_progress_if_current(
                    run_id,
                    {
                        **self._build_default_remove_progress_state(),
                        "status": "done",
                        "step": "done",
                        "current": total_count,
                        "total": total_count,
                        "removed": removed,
                        "missing_ids": missing_ids,
                        "message": f"Completed! Removed {removed} records." + (
                            f" {len(missing_ids)} already missing." if missing_ids else ""
                        ),
                        "operation": "remove",
                        "started_at": self._remove_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            except Exception as e:
                logger.error("Remove job failed: %s", e)
                self._set_remove_progress_if_current(
                    run_id,
                    {
                        **self._build_default_remove_progress_state(),
                        "status": "error",
                        "step": "error",
                        "current": processed,
                        "total": total_count,
                        "removed": removed,
                        "missing_ids": missing_ids,
                        "message": "Remove failed due to an internal error",
                        "operation": "remove",
                        "started_at": self._remove_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            finally:
                with self._remove_lock:
                    if (
                        self._remove_run_id == run_id
                        and self._remove_cancel_event is cancel_event
                    ):
                        self._remove_cancel_event = None

        background_tasks.add_task(run_remove)
        return {
            "status": "started",
            "message": f"Removing {total_count} images in background",
            "total": total_count,
            "count": total_count,
            "operation": "remove",
        }

    def start_remove_bulk_job(self, request: Any, background_tasks: Any) -> Dict[str, Any]:
        """Start a durable-id background remove-from-gallery job (DB rows only)."""
        bulk_jobs = get_bulk_job_service()
        image_ids = self._expand_remove_request_ids(
            getattr(request, "image_ids", None),
            getattr(request, "selection_token", None),
        )
        job_id = bulk_jobs.create_job(
            JOB_KIND_REMOVE_FROM_GALLERY,
            total=len(image_ids),
            message=f"Removing {len(image_ids)} images...",
        )

        def process_chunk(chunk_ids: List[int]) -> Dict[str, Any]:
            result = self._remove_selected_image_id_chunk(chunk_ids)
            removed = int(result.get("removed", 0) or 0)
            missing = len(result.get("missing_ids", []) or [])
            return {
                "processed": len(chunk_ids),
                "errors": [],
                "result_delta": {"removed": removed, "missing": missing},
            }

        worker = bulk_jobs.chunked_worker(
            lambda: image_ids, process_chunk, chunk_size=_svc().DELETE_FETCH_CHUNK
        )
        background_tasks.add_task(bulk_jobs.run_job, job_id, worker)
        envelope = bulk_jobs.get_job(job_id) or {}
        envelope["operation"] = "remove"
        return envelope
