"""Delete-to-trash: the v3.3.2 Phase-1 singleton background job, the
synchronous endpoints, and the Debt-22 durable-id bulk-job path.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: the facade-owned
DELETE_FETCH_CHUNK resolves through _svc() at call time. move_file_to_trash
resolves through the facade so the six existing
monkeypatch(image_service.move_file_to_trash) tests keep landing
(UNSAFE-class seam).
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import database as db
from services.bulk_job_service import (
    JOB_KIND_DELETE_FILES,
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


def move_file_to_trash(*args, **kwargs):
    """Facade-seam proxy (tests patch services.image_service.move_file_to_trash)."""
    return _svc().move_file_to_trash(*args, **kwargs)


class DeleteJobsMixin:
    """Delete-to-trash job slice of ImageService (assembled in services/image_service.py)."""

    @staticmethod
    def _build_default_delete_progress_state() -> Dict[str, Any]:
        """Return the canonical idle delete-to-trash job progress payload.

        Mirrors SortingService._move_progress but reports ``deleted`` instead of
        ``moved`` and embeds the per-id ``failed`` list the frontend already
        consumes from the synchronous /delete-selected response, so the
        background path needs no new client-side mapping.
        """
        return {
            "status": "idle",
            "step": "idle",
            "current": 0,
            "total": 0,
            "message": "",
            "errors": 0,
            "deleted": 0,
            "current_item": None,
            "recent_errors": [],
            "operation": "delete",
            "failed": [],
            "started_at": None,
            "updated_at": None,
        }


    # ------------------------------------------------------------------
    # v3.3.2 Phase-1: background "delete selected" job. Cloned 1:1 from the
    # sorting-service move job (start_move_job / get_move_progress /
    # cancel_move / reset_move_progress / _set_move_progress_if_current /
    # _update_move_progress_if_current). Delete-ONLY for now; it is the concrete
    # template for backgrounding remove/export in a later slice (no generic job
    # abstraction yet, by design).
    # ------------------------------------------------------------------
    def get_delete_progress(self) -> Dict[str, Any]:
        """Get the current gallery delete-to-trash job progress."""
        with self._delete_lock:
            return self._delete_progress.copy()

    def reset_delete_progress(self) -> Dict[str, Any]:
        """Reset a stuck delete job back to idle (refused while it is still running).

        ``cancel_delete`` publishes ``cancelling`` and waits for the worker to
        write the terminal state. A worker that died first leaves the status
        there permanently, and ``start_delete_selected_job`` then rejects every
        later delete with HTTP 409 until the app restarts. Bumping the run id
        neutralizes an abandoned worker that later wakes up.
        """
        with self._delete_lock:
            status = self._delete_progress["status"]
            if status == "running":
                raise HTTPException(status_code=409, detail="Cannot reset delete while it is still running")
            if status == "idle":
                return {"status": "idle", "message": "Nothing to reset"}
            self._delete_run_id += 1
            if self._delete_cancel_event is not None:
                self._delete_cancel_event.set()
                self._delete_cancel_event = None
            self._delete_progress = {
                **self._build_default_delete_progress_state(),
                "message": "Reset by user",
                "updated_at": time.time(),
            }
            return {"status": "reset", "message": "Delete progress reset to idle"}

    def cancel_delete(self) -> Dict[str, Any]:
        """Request cooperative cancellation of the active delete-to-trash job."""
        with self._delete_lock:
            current_status = self._delete_progress.get("status")
            if current_status not in {"running", "cancelling"}:
                return {"status": current_status, "message": "No delete task is running"}

            current = int(self._delete_progress.get("current", 0) or 0)
            total = int(self._delete_progress.get("total", 0) or 0)

            if self._delete_cancel_event is not None:
                self._delete_cancel_event.set()

            self._delete_progress["status"] = "cancelling"
            self._delete_progress["step"] = "cancelling"
            self._delete_progress["message"] = (
                f"Cancelling delete... ({current}/{total})"
                if total > 0
                else "Cancelling delete..."
            )
            self._delete_progress["updated_at"] = time.time()
            return {"status": "cancelling", "message": "Delete cancellation requested"}

    def _set_delete_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        """Only allow the active delete job to replace shared progress state."""
        with self._delete_lock:
            if run_id != self._delete_run_id:
                return False
            self._delete_progress = state
            return True

    def _update_delete_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        """Only allow the active delete job to mutate shared progress state."""
        with self._delete_lock:
            if run_id != self._delete_run_id:
                return False
            self._delete_progress = {
                **self._delete_progress,
                **updates,
            }
            return True

    def _normalize_delete_ids(self, image_ids: List[int]) -> List[int]:
        """Dedup a delete id list preserving order.

        Matches the synchronous path's normalization exactly (int-cast + dedup,
        intentionally NO ``<= 0`` filtering — that is a remove-from-gallery
        concern, not a delete one).
        """
        normalized_ids: List[int] = []
        seen_ids: set[int] = set()
        for raw_image_id in image_ids or []:
            image_id = int(raw_image_id)
            if image_id in seen_ids:
                continue
            seen_ids.add(image_id)
            normalized_ids.append(image_id)
        return normalized_ids

    def _delete_one_image_to_trash(self, image_id: int, image: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Trash one image file + drop its DB row; return a normalized per-id result.

        v3.3.2 Phase-1: shared by the synchronous ``delete_selected_image_files``
        endpoint and the background delete job (mirrors how ``_move_one_image`` is
        shared by ``move_images`` and ``start_move_job``) so both paths produce
        identical ``failed`` rows and the same trash-then-delete-row ordering.
        ``move_file_to_trash`` is referenced as a module global so existing tests
        that monkeypatch ``image_service.move_file_to_trash`` still intercept it.
        """
        if not image:
            return {"id": image_id, "success": False, "filename": None, "error": "Image not found"}

        filename = image.get("filename") or Path(str(image.get("path") or "")).name or f"image_{image_id}"
        try:
            source_path = self.resolve_image_source_path(image_id, image.get("path", ""))
            move_file_to_trash(source_path)
            db.delete_image(image_id)
            return {"id": image_id, "success": True, "filename": filename}
        except HTTPException as exc:
            return {
                "id": image_id,
                "success": False,
                "filename": filename,
                "error": exc.detail or "Image file not found on disk",
            }
        except Exception as exc:
            return {"id": image_id, "success": False, "filename": filename, "error": str(exc)}

    def _expand_delete_request_ids(
        self, image_ids: Optional[List[int]], selection_token: Optional[str]
    ) -> List[int]:
        """Resolve a delete request into a concrete, deduped image-id snapshot.

        v3.3.2 Phase-1: a ``selection_token`` (Select All Filtered scope) is
        snapshotted to a temp file and read back via
        ``_iter_selection_token_snapshot_chunks`` BEFORE any deletion, so the id
        set is frozen and unaffected by the rows the job is about to remove.
        Mirrors ``SortingService._expand_move_request_ids``.
        """
        if selection_token:
            snapshot: List[int] = []
            for chunk in self._iter_selection_token_snapshot_chunks(selection_token, chunk_size=500):
                snapshot.extend(chunk)
            return snapshot
        return self._normalize_delete_ids(image_ids or [])

    def start_delete_selected_job(self, request: Any, background_tasks: Any) -> Dict[str, Any]:
        """v3.3.2 Phase-1: gallery "delete selected" as a background job with
        progress polling, cloned from ``SortingService.start_move_job``. The
        final progress payload embeds ``deleted`` + ``failed`` so the frontend
        mapping is identical to the synchronous ``/delete-selected`` endpoint.
        Files are trashed one at a time as the worker advances, so the progress
        bar tracks deletion. The id set is snapshotted before the worker starts.
        """
        with self._delete_lock:
            if self._delete_progress["status"] in {"running", "cancelling"}:
                raise HTTPException(status_code=409, detail="A delete is already in progress")

        image_ids = self._expand_delete_request_ids(
            getattr(request, "image_ids", None),
            getattr(request, "selection_token", None),
        )
        total_count = len(image_ids)
        if total_count == 0:
            return {
                "status": "done",
                "message": "No images to delete",
                "deleted": 0,
                "failed": [],
                "total": 0,
                "operation": "delete",
                "permanent_delete": False,
                "trash_used": True,
            }

        cancel_event = threading.Event()
        with self._delete_lock:
            self._delete_run_id += 1
            run_id = self._delete_run_id
            self._delete_cancel_event = cancel_event
            self._delete_progress = {
                **self._build_default_delete_progress_state(),
                "status": "running",
                "step": "starting",
                "current": 0,
                "total": total_count,
                "message": f"Starting delete of {total_count} images...",
                "started_at": time.time(),
                "updated_at": time.time(),
            }

        def run_delete():
            deleted = 0
            processed = 0
            errors: List[Dict[str, Any]] = []
            try:
                def _write_cancelled_state() -> None:
                    self._set_delete_progress_if_current(
                        run_id,
                        {
                            **self._build_default_delete_progress_state(),
                            "status": "cancelled",
                            "step": "cancelled",
                            "current": processed,
                            "total": total_count,
                            "errors": len(errors),
                            "deleted": deleted,
                            "message": (
                                f"Cancelled at {processed}/{total_count}. "
                                f"Trashed {deleted} images so far."
                            ),
                            "current_item": None,
                            "recent_errors": errors[-3:],
                            "operation": "delete",
                            "failed": errors,
                            "started_at": self._delete_progress.get("started_at"),
                            "updated_at": time.time(),
                        },
                    )

                # Walk the snapshotted id list in chunks so the per-image DB rows
                # are fetched in batches (matches the sync path's IN(...) chunking)
                # while progress advances per image. Cancel is honored at the
                # chunk boundary AND before each individual trash.
                for start in range(0, total_count, _svc().DELETE_FETCH_CHUNK):
                    if cancel_event.is_set():
                        _write_cancelled_state()
                        return
                    chunk_ids = image_ids[start:start + _svc().DELETE_FETCH_CHUNK]
                    image_map = db.get_images_by_ids(chunk_ids)

                    for image_id in chunk_ids:
                        if cancel_event.is_set():
                            _write_cancelled_state()
                            return

                        image = image_map.get(image_id)
                        result = self._delete_one_image_to_trash(image_id, image)
                        filename = result.get("filename") or f"id-{image_id}"
                        if result.get("success"):
                            deleted += 1
                        else:
                            errors.append({
                                "image_id": image_id,
                                "filename": result.get("filename"),
                                "error": result.get("error") or "Failed",
                            })
                        processed += 1
                        if not self._update_delete_progress_if_current(
                            run_id,
                            step="deleting",
                            current=processed,
                            total=total_count,
                            errors=len(errors),
                            deleted=deleted,
                            message=f"Trashed {filename} ({processed}/{total_count})",
                            current_item=filename,
                            recent_errors=errors[-3:],
                            operation="delete",
                            updated_at=time.time(),
                        ):
                            return

                self._set_delete_progress_if_current(
                    run_id,
                    {
                        **self._build_default_delete_progress_state(),
                        "status": "done",
                        "step": "done",
                        "current": total_count,
                        "total": total_count,
                        "errors": len(errors),
                        "deleted": deleted,
                        "message": f"Completed! Trashed {deleted} images." + (f" {len(errors)} errors." if errors else ""),
                        "current_item": None,
                        "recent_errors": errors[-3:],
                        "operation": "delete",
                        "failed": errors,
                        "started_at": self._delete_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            except Exception as e:
                logger.error("Delete job failed: %s", e)
                self._set_delete_progress_if_current(
                    run_id,
                    {
                        **self._build_default_delete_progress_state(),
                        "status": "error",
                        "step": "error",
                        "current": processed,
                        "total": total_count,
                        "errors": len(errors),
                        "deleted": deleted,
                        "message": "Delete failed due to an internal error",
                        "current_item": None,
                        "recent_errors": errors[-3:],
                        "operation": "delete",
                        "failed": errors,
                        "started_at": self._delete_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            finally:
                with self._delete_lock:
                    if (
                        self._delete_run_id == run_id
                        and self._delete_cancel_event is cancel_event
                    ):
                        self._delete_cancel_event = None

        background_tasks.add_task(run_delete)
        return {
            "status": "started",
            "message": f"Deleting {total_count} images in background",
            "total": total_count,
            "count": total_count,
            "operation": "delete",
        }

    def delete_selected_image_files(self, image_ids: List[int]) -> Dict[str, Any]:
        """Move image files to OS trash and remove their database rows.

        Returns a partial-failure payload so the frontend can show a truthful
        summary instead of pretending the whole batch succeeded.

        v3.3.2 Phase-1: the per-image trash+delete step is shared with the
        background delete job via ``_delete_one_image_to_trash``, so the two
        paths stay byte-for-byte identical in their failure reporting. Kept
        synchronous and unchanged in its public contract for back-compat (tests
        and programmatic callers); the gallery UI now drives ``start_delete_selected_job``.
        """
        deleted = 0
        failed: List[Dict[str, Any]] = []

        # Two-pass: normalize + dedup first so we can batch the DB read.
        # `get_images_by_ids` chunks IN(...) at 500 ids internally so the
        # access pattern stays bounded even under the raised 5M ceiling.
        normalized_ids = self._normalize_delete_ids(image_ids)

        if not normalized_ids:
            return {
                "deleted": 0,
                "failed": [],
                "permanent_delete": False,
                "trash_used": True,
            }

        for batch_start in range(0, len(normalized_ids), _svc().DELETE_FETCH_CHUNK):
            batch_ids = normalized_ids[batch_start:batch_start + _svc().DELETE_FETCH_CHUNK]
            images_map = db.get_images_by_ids(batch_ids)

            for image_id in batch_ids:
                result = self._delete_one_image_to_trash(image_id, images_map.get(image_id))
                if result.get("success"):
                    deleted += 1
                else:
                    failed.append({
                        "image_id": image_id,
                        "filename": result.get("filename"),
                        "error": result.get("error") or "Failed",
                    })

        return {
            "deleted": deleted,
            "failed": failed,
            "permanent_delete": False,
            "trash_used": True,
        }

    def delete_selected_image_files_by_token(self, selection_token: str) -> Dict[str, Any]:
        """Move all images referenced by a filtered-selection token to trash in chunks."""
        deleted = 0
        failed: List[Dict[str, Any]] = []
        for batch_ids in self._iter_selection_token_snapshot_chunks(selection_token, chunk_size=500):
            result = self.delete_selected_image_files(batch_ids)
            deleted += int(result.get("deleted", 0) or 0)
            failed.extend(result.get("failed", []) or [])

        return {
            "deleted": deleted,
            "failed": failed,
            "permanent_delete": False,
            "trash_used": True,
        }

    # ------------------------------------------------------------------
    # Debt-22: durable job-ID background path via the shared BulkJobService.
    # These sit alongside the Phase-1 singleton jobs above; the ``background``
    # opt-in on the sync endpoints routes here so delete / remove appear in the
    # unified /api/bulk-jobs registry (durable id, list, cancel-by-id) while
    # reusing the exact per-item helpers so failure reporting stays identical.
    # The id set is snapshotted server-side BEFORE the worker mutates anything.
    # ------------------------------------------------------------------
    def start_delete_bulk_job(self, request: Any, background_tasks: Any) -> Dict[str, Any]:
        """Start a durable-id background delete-to-trash job and return its envelope."""
        bulk_jobs = get_bulk_job_service()
        image_ids = self._expand_delete_request_ids(
            getattr(request, "image_ids", None),
            getattr(request, "selection_token", None),
        )
        job_id = bulk_jobs.create_job(
            JOB_KIND_DELETE_FILES,
            total=len(image_ids),
            message=f"Deleting {len(image_ids)} images...",
        )

        def process_chunk(chunk_ids: List[int]) -> Dict[str, Any]:
            image_map = db.get_images_by_ids(chunk_ids)
            deleted = 0
            errors: List[str] = []
            for image_id in chunk_ids:
                result = self._delete_one_image_to_trash(image_id, image_map.get(image_id))
                if result.get("success"):
                    deleted += 1
                else:
                    filename = result.get("filename") or f"id-{image_id}"
                    errors.append(f"{filename}: {result.get('error') or 'Failed'}")
            return {
                "processed": len(chunk_ids),
                "errors": errors,
                "result_delta": {"deleted": deleted},
            }

        worker = bulk_jobs.chunked_worker(
            lambda: image_ids, process_chunk, chunk_size=_svc().DELETE_FETCH_CHUNK
        )
        background_tasks.add_task(bulk_jobs.run_job, job_id, worker)
        envelope = bulk_jobs.get_job(job_id) or {}
        envelope["operation"] = "delete"
        return envelope
