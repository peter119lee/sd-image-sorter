"""Tag export surface: batch/combined/preview exports and export-job progress.

Moved verbatim from services/tagging_service.py (decomposition 2026-07).
"""

import logging
import time
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from config import read_float_env
from services.bulk_job_service import JOB_KIND_EXPORT_SIDECARS, get_bulk_job_service
from services.tag_export_service import (
    count_selection_token_ids,
    export_tags_batch_request,
    export_tags_combined_request,
    iter_selection_token_id_chunks,
    render_export_preview,
)
from services.tagging.request import (
    BatchTagExportRequest,
    CombinedTagExportRequest,
    ExportPreviewRequest,
)

# NOTE(decomposition): keep the historical logger channel so log routing
# and output stay byte-identical after the services/tagging split.
logger = logging.getLogger("services.tagging_service")

# The export worker publishes its own heartbeat, throttled to this interval so
# a large selection does not pay a lock per image. Without a real heartbeat
# "stalled" would only ever mean "has been running a while", which is exactly
# the kind of statement that trains a user to ignore a warning.
EXPORT_HEARTBEAT_MIN_INTERVAL_SECONDS = max(
    0.0, read_float_env("SD_IMAGE_SORTER_EXPORT_HEARTBEAT_INTERVAL_SECONDS", 0.25)
)
# Advisory only, like the scan job's SCAN_UI_STALLED_SECONDS.
EXPORT_STALLED_SECONDS = max(
    5.0, read_float_env("SD_IMAGE_SORTER_EXPORT_STALLED_SECONDS", 45.0)
)
# How long a queued background task may take to begin before a run with no
# worker behind it is treated as abandoned.
EXPORT_WORKER_START_GRACE_SECONDS = max(
    1.0, read_float_env("SD_IMAGE_SORTER_EXPORT_WORKER_START_GRACE_SECONDS", 30.0)
)


def _elapsed_since(stamp: Any, now: float) -> Optional[float]:
    """Seconds since ``stamp``, or ``None`` when it cannot be read as a time."""
    try:
        return max(0.0, now - float(stamp))
    except (TypeError, ValueError):
        return None


class ExportsMixin:
    """Export slice of TaggingService (assembled in services.tagging.service)."""

    def export_tags_batch(
        self,
        request: BatchTagExportRequest,
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Export tags for each image to individual .txt files.

        ``progress_callback`` is how the background job gets a heartbeat; the
        synchronous endpoint leaves it unset and behaves exactly as before.
        """
        id_chunks = None
        total = None
        if request.selection_token:
            id_chunks = iter_selection_token_id_chunks(request.selection_token)
            total = count_selection_token_ids(request.selection_token)
        result = export_tags_batch_request(
            request,
            id_chunks=id_chunks,
            total=total,
            progress_callback=progress_callback,
        )
        error_count = int(result.get("error_count", 0) or 0)
        exported = int(result.get("exported", 0) or 0)
        skipped = int(result.get("skipped", 0) or 0)
        status = self._resolve_export_status(exported, skipped, error_count)
        return {
            "status": status,
            "exported": exported,
            "errors": error_count,
            "error_count": error_count,
            "error_messages": result.get("error_messages", []),
            "skipped": skipped,
            "total": result.get("total", len(request.image_ids or [])),
            "content_mode": result.get("content_mode", request.content_mode),
            "overwrite_policy": result.get(
                "overwrite_policy", request.overwrite_policy
            ),
            "output_mode": result.get(
                "output_mode", getattr(request, "output_mode", "folder")
            ),
            "nl_sidecars_written": result.get("nl_sidecars_written", 0),
            "validation": result.get("validation"),
        }

    @staticmethod
    def _resolve_export_status(exported: int, skipped: int, error_count: int) -> str:
        """Map export counters to the ok / partial / error status contract."""
        if error_count > 0:
            return "partial" if exported > 0 or skipped > 0 else "error"
        if skipped > 0:
            return "partial"
        return "ok"

    @staticmethod
    def _build_default_export_progress_state() -> Dict[str, Any]:
        """Idle progress payload for the background batch tag-export job. The
        terminal 'done' payload embeds the full ``export_tags_batch`` result under
        ``result`` so the frontend's existing mapping works unchanged."""
        return {
            "status": "idle",
            "step": "idle",
            "current": 0,
            "total": 0,
            "message": "",
            "operation": "export",
            "result": None,
            "started_at": None,
            "updated_at": None,
        }

    def _mark_export_worker(self, run_id: int, *, running: bool) -> None:
        """Record whether a worker is currently inside this run's export."""
        with self._export_lock:
            if running:
                self._export_worker_run_id = run_id
                self._export_worker_running = True
            elif self._export_worker_run_id == run_id:
                self._export_worker_running = False

    def _describe_export_liveness_locked(
        self, progress: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Derive stall and abandonment facts for a progress snapshot.

        Split the way the scan job splits them: ``stalled_seconds`` /
        ``attention_required`` are advisory (``_with_scan_attention_fields``),
        while only the absence of a live worker permits a reset
        (``reset_scan_progress``). A slow export keeps its run however long it
        has been quiet, because clearing it would let a second export run
        against files the first is still writing.
        """
        if progress.get("status") != "running":
            return {
                "stalled_seconds": 0,
                "attention_required": False,
                "attention_message": "",
                "abandoned": False,
            }

        now = time.time()
        quiet_for = _elapsed_since(
            progress.get("updated_at") or progress.get("started_at"), now
        )
        stalled_seconds = int(quiet_for) if quiet_for is not None else 0

        worker_entered = self._export_worker_run_id == self._export_run_id
        if worker_entered and self._export_worker_running:
            abandoned = False
        elif worker_entered:
            # The worker left run_export without publishing a terminal state,
            # so nothing will ever finish this run.
            abandoned = True
        else:
            # No worker ever entered. Starlette runs a background task only
            # after the response body has been sent, so a client that
            # disappears first leaves "running" published with nothing behind
            # it. An unreadable start time proves nothing, so it is not
            # abandonment.
            since_start = _elapsed_since(progress.get("started_at"), now)
            abandoned = (
                since_start is not None
                and since_start >= EXPORT_WORKER_START_GRACE_SECONDS
            )

        if abandoned:
            message = (
                f"No worker is running this tag export, and nothing has reported progress "
                f"for {stalled_seconds}s. Reset it to start a new export."
            )
        elif quiet_for is not None and quiet_for >= EXPORT_STALLED_SECONDS:
            message = (
                f"No visible tag-export progress for {stalled_seconds}s. The export may be "
                "waiting on a slow or unresponsive output folder; it can only be cleared "
                "once it stops."
            )
        else:
            message = ""

        return {
            "stalled_seconds": stalled_seconds,
            "attention_required": bool(message),
            "attention_message": message,
            "abandoned": abandoned,
        }

    def get_export_progress(self) -> Dict[str, Any]:
        with self._export_lock:
            progress = self._export_progress.copy()
            return {**progress, **self._describe_export_liveness_locked(progress)}

    def _clear_export_progress_locked(self) -> None:
        """Publish idle export progress and abandon the current run."""
        self._export_run_id += 1
        self._export_progress = {
            **self._build_default_export_progress_state(),
            "message": "Reset by user",
            "updated_at": time.time(),
        }

    def reset_export_progress(self) -> Dict[str, Any]:
        """Reset a stuck batch tag-export job back to idle.

        Same contract as the move / batch-move / delete / remove resets
        (d7013e2, 2e2ae37): 409 on a genuinely running job, "Nothing to reset"
        only when it really was already idle, and a distinct ``reset`` answer
        when it cleared something. Bumping the run id neutralizes an abandoned
        worker that later wakes up, so it cannot overwrite what this reset just
        published.

        Unlike those four, this job never publishes ``cancelling``, so
        ``running`` is the only state that blocks a new export — which made a
        reset that refused every ``running`` job a recovery path that could not
        recover anything. It now clears a ``running`` job whose worker is
        provably gone, and still refuses one that is merely slow.
        """
        with self._export_lock:
            status = self._export_progress["status"]
            if status == "running":
                if not self._describe_export_liveness_locked(self._export_progress)[
                    "abandoned"
                ]:
                    raise HTTPException(
                        status_code=409,
                        detail="Cannot reset the tag export while it is still running",
                    )
                self._clear_export_progress_locked()
                return {
                    "status": "reset",
                    "message": "Abandoned tag export cleared. You can start a new export.",
                }
            if status == "idle":
                return {"status": "idle", "message": "Nothing to reset"}
            self._clear_export_progress_locked()
            return {"status": "reset", "message": "Tag export progress reset to idle"}

    def _set_export_progress_if_current(
        self, run_id: int, state: Dict[str, Any]
    ) -> bool:
        with self._export_lock:
            if run_id != self._export_run_id:
                return False
            self._export_progress = state
            return True

    def _publish_export_heartbeat(
        self, run_id: int, processed: int, total: int
    ) -> None:
        """Publish the worker's own progress so a stall is a measured fact.

        Without this the job would only ever stamp ``updated_at`` at start, and
        "no progress for N seconds" would just be restating how long the export
        has been running.
        """
        now = time.time()
        with self._export_lock:
            if run_id != self._export_run_id:
                return
            if self._export_progress.get("status") != "running":
                return
            since_last = _elapsed_since(self._export_progress.get("updated_at"), now)
            if (
                processed < total
                and since_last is not None
                and since_last < EXPORT_HEARTBEAT_MIN_INTERVAL_SECONDS
            ):
                return
            self._export_progress = {
                **self._export_progress,
                "current": processed,
                "updated_at": now,
            }

    def start_export_tags_batch_job(
        self, request: BatchTagExportRequest, background_tasks: Any
    ) -> Dict[str, Any]:
        """v3.3.2 Phase-1: run ``export_tags_batch`` as a background job so large
        exports don't freeze the request. There is still no mid-run cancel, but
        the worker now publishes a per-image heartbeat, so a run that has
        stopped advancing can be told apart from one that is simply large. The
        terminal payload embeds the full export result under ``result``.
        """
        with self._export_lock:
            if self._export_progress["status"] == "running":
                raise HTTPException(
                    status_code=409, detail="An export is already in progress"
                )

        if request.selection_token:
            total = count_selection_token_ids(request.selection_token)
        else:
            total = len(request.image_ids or [])

        with self._export_lock:
            self._export_run_id += 1
            run_id = self._export_run_id
            self._export_progress = {
                **self._build_default_export_progress_state(),
                "status": "running",
                "step": "exporting",
                "current": 0,
                "total": total,
                "message": f"Exporting tags for {total} images...",
                "started_at": time.time(),
                "updated_at": time.time(),
            }

        def on_progress(update: Dict[str, Any]) -> None:
            self._publish_export_heartbeat(
                run_id,
                int(update.get("processed") or 0),
                int(update.get("total") or total),
            )

        def run_export():
            self._mark_export_worker(run_id, running=True)
            try:
                result = self.export_tags_batch(
                    request, progress_callback=on_progress
                )
                self._set_export_progress_if_current(
                    run_id,
                    {
                        **self._build_default_export_progress_state(),
                        "status": "done",
                        "step": "done",
                        "current": total,
                        "total": total,
                        "message": f"Export complete: {int(result.get('exported', 0) or 0)} files.",
                        "operation": "export",
                        "result": result,
                        "started_at": self._export_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            except Exception as e:
                logger.error("Export job failed: %s", e)
                self._set_export_progress_if_current(
                    run_id,
                    {
                        **self._build_default_export_progress_state(),
                        "status": "error",
                        "step": "error",
                        "current": 0,
                        "total": total,
                        "message": "Export failed due to an internal error",
                        "operation": "export",
                        "result": {
                            "status": "error",
                            "exported": 0,
                            "errors": 1,
                            "error_count": 1,
                            "error_messages": [str(e)],
                            "skipped": 0,
                            "total": total,
                        },
                        "started_at": self._export_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            finally:
                # Leaving without a terminal state is what a stranded export
                # looks like; the reset needs to be able to see that happen.
                self._mark_export_worker(run_id, running=False)

        background_tasks.add_task(run_export)
        return {
            "status": "started",
            "message": f"Exporting {total} images in background",
            "total": total,
            "operation": "export",
        }

    def start_export_bulk_job(
        self, request: BatchTagExportRequest, background_tasks: Any
    ) -> Dict[str, Any]:
        """Debt-22: same-name sidecar export as a durable-id background job.

        Unlike the Phase-1 ``start_export_tags_batch_job`` (coarse progress, no
        mid-run cancel), this streams per-image progress and stops cooperatively
        when cancelled via the shared BulkJobService. Token selections are
        snapshotted server-side before the export reads them; the single-call
        ``export_tags_batch_request`` keeps its filename de-dup intact.
        """
        bulk_jobs = get_bulk_job_service()
        if request.selection_token:
            total = count_selection_token_ids(request.selection_token)
        else:
            total = len(request.image_ids or [])
        job_id = bulk_jobs.create_job(
            JOB_KIND_EXPORT_SIDECARS,
            total=total,
            message=f"Exporting {total} images...",
        )

        def worker(handle) -> None:
            id_chunks = (
                iter_selection_token_id_chunks(request.selection_token, snapshot=True)
                if request.selection_token
                else None
            )

            def on_progress(update: Dict[str, Any]) -> None:
                handle.set_progress(
                    processed=int(update.get("processed") or 0),
                    total=int(update.get("total") or total),
                )

            result = export_tags_batch_request(
                request,
                id_chunks=id_chunks,
                total=total,
                progress_callback=on_progress,
                cancel_check=lambda: handle.cancelled,
            )
            error_count = int(result.get("error_count", 0) or 0)
            exported = int(result.get("exported", 0) or 0)
            skipped = int(result.get("skipped", 0) or 0)
            handle.record_errors(error_count, result.get("error_messages") or [])
            handle.set_result(
                {
                    "status": self._resolve_export_status(
                        exported, skipped, error_count
                    ),
                    "exported": exported,
                    "skipped": skipped,
                    "errors": error_count,
                    "error_count": error_count,
                    "error_messages": result.get("error_messages", []),
                    "total": result.get("total", total),
                    "content_mode": result.get("content_mode", request.content_mode),
                    "overwrite_policy": result.get(
                        "overwrite_policy", request.overwrite_policy
                    ),
                    "output_mode": result.get(
                        "output_mode", getattr(request, "output_mode", "folder")
                    ),
                    "nl_sidecars_written": result.get("nl_sidecars_written", 0),
                    "validation": result.get("validation"),
                }
            )

        background_tasks.add_task(bulk_jobs.run_job, job_id, worker)
        envelope = bulk_jobs.get_job(job_id) or {}
        envelope["operation"] = "export"
        return envelope

    def export_tags_combined(self, request: CombinedTagExportRequest) -> Dict[str, Any]:
        """Render selected captions into one server-side downloadable file."""
        id_chunks = None
        total = None
        if request.selection_token:
            id_chunks = iter_selection_token_id_chunks(request.selection_token)
            total = count_selection_token_ids(request.selection_token)
        return export_tags_combined_request(request, id_chunks=id_chunks, total=total)

    def export_preview(self, request: ExportPreviewRequest) -> Dict[str, Any]:
        """Render export captions for the live preview modal."""
        return render_export_preview(request)
