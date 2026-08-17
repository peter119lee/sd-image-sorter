"""Progress state machines: scan / batch-move / move-job + session accessors.

Moved verbatim from services/sorting_service.py (decomposition 2026-07). All
mutable state is initialized by ``SortingService.__init__`` in
services/sorting_service.py — mixins define methods only and reach the
shared state via ``self``. Hard constraint: no mixin ``__init__``.
"""

import threading
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.sorting_models import (
    SCAN_ACTIVE_STATUSES,
    SCAN_TERMINAL_STATUSES,
    SORT_MODE_DEFAULT,
    ScanAcknowledgeRequest,
    ScanAcknowledgeResult,
    ScanCancelRequest,
    ScanCancelResult,
    ScanSource,
)
from services.state_compat import MutableStateProxy


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


class SortingStateMixin:
    """State-machine slice of SortingService (assembled in services/sorting_service.py)."""

    @staticmethod
    def _build_default_scan_progress_state() -> Dict[str, Any]:
        """Return the canonical idle scan-progress payload."""
        return {
            "run_id": 0,
            "source": None,
            "status": "idle",
            "step": "idle",
            "current": 0,
            "processed": 0,
            "total": 0,
            "counted": 0,
            "total_final": False,
            "import_complete": False,
            "errors": 0,
            "new": 0,
            "updated": 0,
            "skipped_other_library": 0,
            "skipped_other_library_paths": [],
            "removed": 0,
            "library_ready": False,
            "quick_import": True,
            "metadata_processed": 0,
            "metadata_total": 0,
            "metadata_total_final": False,
            "metadata_pending": 0,
            "message": "",
            "current_item": None,
            "recent_errors": [],
            "started_at": None,
            "updated_at": None,
            "attention_required": False,
            "attention_message": "",
            "stalled_seconds": 0,
            "diagnostics_available": True,
            "diagnostics_endpoint": "/api/support/diagnostics",
        }

    @staticmethod
    def _build_default_sort_session_state() -> Dict[str, Any]:
        """Return the canonical inactive manual-sort session payload."""
        return {
            "active": False,
            # v3.3.2 Workbench: which culling/sorting mode this session runs.
            # "slot" == the original WASD slot-sort (default → unchanged behavior).
            "mode": SORT_MODE_DEFAULT,
            "image_ids": [],
            "current_index": 0,
            # v3.3.2 WB-S2: bracket champion pointer (unused in slot mode). The
            # challenger pointer reuses current_index.
            "champion_index": 0,
            "folders": {},
            # v3.3.1: per-slot collection mapping. A slot key (w/a/s/d) whose
            # value is a collection id is "collection-typed": pressing it adds
            # the current image to that collection BY REFERENCE (no file move).
            # Slots with a None value fall back to the normal folder behavior.
            "collection_slots": {},
            "operation_mode": "move",
            "history": [],
            "redo_stack": [],
        }

    def _with_scan_attention_fields(self, progress: Dict[str, Any]) -> Dict[str, Any]:
        """Add UI-facing stalled-scan diagnostics without mutating worker progress."""
        now = time.time()
        updated_at = progress.get("updated_at") or progress.get("started_at") or now
        try:
            idle_for = max(0.0, now - float(updated_at))
        except (TypeError, ValueError):
            idle_for = 0.0

        status = progress.get("status")
        step = str(progress.get("step") or "scan")
        is_active = status in {"running", "cancelling"}
        attention_required = bool(is_active and idle_for >= _svc().SCAN_UI_STALLED_SECONDS)
        if attention_required:
            pending = int(progress.get("metadata_pending", 0) or 0)
            current_item = progress.get("current_item") or "current file"
            if step == "metadata" or pending > 0:
                message = (
                    f"No visible metadata progress for {int(idle_for)}s. "
                    f"Pending metadata jobs: {pending}. Current item: {current_item}. "
                    "The scan may still be waiting on a slow or broken image; copy diagnostics if this keeps growing."
                )
            else:
                message = (
                    f"No visible scan progress for {int(idle_for)}s while step={step}. "
                    f"Current item: {current_item}. Copy diagnostics if this keeps growing."
                )
        else:
            message = ""

        return {
            **progress,
            "attention_required": attention_required,
            "attention_message": message,
            "stalled_seconds": int(idle_for),
            "diagnostics_available": True,
            "diagnostics_endpoint": "/api/support/diagnostics",
        }

    def get_scan_progress(self) -> Dict[str, Any]:
        """Get the current scan progress."""
        with self._scan_lock:
            return self._with_scan_attention_fields(self._scan_progress.copy())

    def is_scan_worker_active(self) -> bool:
        """Return whether the service-owned scan worker is still alive."""
        with self._scan_lock:
            return bool(
                self._scan_worker_thread
                and self._scan_worker_thread.is_alive()
            )

    def get_scan_progress_proxy(self) -> MutableStateProxy:
        """Expose the legacy dict-style scan-progress handle from the service."""
        return self._scan_progress_proxy

    def get_system_info_payload(self) -> Dict[str, Any]:
        """Return hardware info and tagger runtime recommendations for the UI."""
        try:
            from config import DEFAULT_TAGGER_MODEL, TAGGER_MODELS
            from hardware_monitor import get_system_info, recommend_tagger_config

            system_info = get_system_info()
            recommendation = recommend_tagger_config(
                system_info,
                model_name=DEFAULT_TAGGER_MODEL,
                use_gpu=True,
            )
            recommendations_by_model = {}
            for model_name in TAGGER_MODELS.keys():
                recommendations_by_model[model_name] = {
                    "gpu": recommend_tagger_config(system_info, model_name=model_name, use_gpu=True),
                    "cpu": recommend_tagger_config(system_info, model_name=model_name, use_gpu=False),
                }
            recommendations_by_model["custom"] = {
                "gpu": recommend_tagger_config(system_info, model_name="custom", use_gpu=True),
                "cpu": recommend_tagger_config(system_info, model_name="custom", use_gpu=False),
            }
            return {
                "system_info": system_info,
                "recommendation": recommendation,
                "recommendations_by_model": recommendations_by_model,
            }
        except Exception as exc:
            return {
                "system_info": {"error": str(exc)},
                "recommendation": {
                    "recommended_batch_size": 2,
                    "recommended_use_gpu": False,
                    "recommended_session_refresh_interval": 0,
                    "risk_level": "medium",
                    "message": f"Hardware detection failed: {exc}",
                },
                "recommendations_by_model": {},
            }

    def set_scan_progress(self, state: Dict[str, Any]) -> None:
        """Set the scan progress state."""
        with self._scan_lock:
            self._scan_progress = self._coerce_scan_progress_state(state)

    def _set_scan_progress_idle_locked(self, message: str) -> None:
        """Replace scan progress with canonical idle state while holding the lock."""
        self._scan_progress = self._build_default_scan_progress_state()
        self._scan_progress.update({
            "message": message,
            "updated_at": time.time(),
        })
        self._scan_cancel_event = None
        self._scan_worker_thread = None

    def _invalidate_scan_run_locked(self) -> None:
        """Signal and invalidate a queued or stale run while holding the lock."""
        if self._scan_cancel_event is not None:
            self._scan_cancel_event.set()
        progress_run_id = int(self._scan_progress.get("run_id", 0) or 0)
        self._scan_run_id = max(self._scan_run_id, progress_run_id) + 1

    def _require_scan_identity_locked(
        self,
        run_id: int,
        source: ScanSource,
        action: str,
    ) -> str:
        """Return current status when the requested identity owns scan progress."""
        current_run_id = self._scan_progress.get("run_id")
        current_source = self._scan_progress.get("source")
        current_status = self._scan_progress.get("status")
        if current_run_id == run_id and current_source == source:
            return str(current_status)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "scan_identity_mismatch",
                "message": (
                    f"Scan progress changed before {action}. "
                    "Fetch the current progress and retry with that scan identity."
                ),
                "requested": {
                    "run_id": run_id,
                    "source": source,
                },
                "current": {
                    "run_id": current_run_id,
                    "source": current_source,
                    "status": current_status,
                },
            },
        )

    def acknowledge_scan_terminal(
        self,
        request: ScanAcknowledgeRequest,
    ) -> ScanAcknowledgeResult:
        """Atomically clear the exact manual terminal result observed by a client."""
        with self._scan_lock:
            current_status = self._require_scan_identity_locked(
                request.run_id,
                request.source,
                "acknowledgement",
            )
            if current_status not in SCAN_TERMINAL_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "scan_not_terminal",
                        "message": (
                            "The identified scan has not reached a terminal state. "
                            "Wait for done, cancelled, or error before acknowledging it."
                        ),
                        "run_id": request.run_id,
                        "source": request.source,
                        "status": current_status,
                    },
                )

            result: ScanAcknowledgeResult = {
                "status": "acknowledged",
                "run_id": request.run_id,
                "source": request.source,
            }
            self._set_scan_progress_idle_locked("Acknowledged by client")
            return result

    def reset_scan_progress(self) -> Dict[str, Any]:
        """Reset a stuck scan task back to idle."""
        with self._scan_lock:
            status = self._scan_progress["status"]
            worker_alive = bool(self._scan_worker_thread and self._scan_worker_thread.is_alive())
            can_reset = status in SCAN_TERMINAL_STATUSES or (
                status in SCAN_ACTIVE_STATUSES and not worker_alive
            )
            if can_reset:
                self._invalidate_scan_run_locked()
                self._set_scan_progress_idle_locked("Reset by user")
                return {"status": "reset", "message": "Scan progress reset to idle"}
            if worker_alive:
                return {"status": status, "message": "Cannot reset while scan worker is still running"}
            return {"status": status, "message": "Nothing to reset (not running)"}

    def cancel_scan(self, request: ScanCancelRequest) -> ScanCancelResult:
        """Request cooperative cancellation of the current scan task."""
        with self._scan_lock:
            current_status = self._require_scan_identity_locked(
                request.run_id,
                request.source,
                "cancellation",
            )
            if current_status not in SCAN_ACTIVE_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "scan_not_active",
                        "message": (
                            "The identified scan is no longer active. "
                            "Fetch current progress before trying to cancel again."
                        ),
                        "run_id": request.run_id,
                        "source": request.source,
                        "status": current_status,
                    },
                )

            current = int(self._scan_progress.get("current", 0) or 0)
            total = int(self._scan_progress.get("total", 0) or 0)
            total_final = bool(self._scan_progress.get("total_final", False))
            worker_alive = bool(self._scan_worker_thread and self._scan_worker_thread.is_alive())

            if worker_alive:
                if self._scan_cancel_event is not None:
                    self._scan_cancel_event.set()
                self._scan_progress["status"] = "cancelling"
                self._scan_progress["step"] = "cancelling"
                self._scan_progress["message"] = (
                    f"Cancelling scan... ({current}/{total})"
                    if total_final and total > 0
                    else f"Cancelling scan... ({current} scanned)"
                )
                self._scan_progress["updated_at"] = time.time()
                return {
                    "status": "cancelling",
                    "message": "Scan cancellation requested",
                    "run_id": request.run_id,
                    "source": request.source,
                }

            self._invalidate_scan_run_locked()
            self._scan_progress["status"] = "cancelled"
            self._scan_progress["step"] = "cancelled"
            self._scan_progress["message"] = (
                f"Scan cancelled at {current}/{total}."
                if total_final and total > 0
                else f"Scan cancelled after {current} scanned."
            )
            self._scan_progress["updated_at"] = time.time()
            self._scan_cancel_event = None
            self._scan_worker_thread = None
            return {
                "status": "cancelled",
                "message": "Scan cancelled",
                "run_id": request.run_id,
                "source": request.source,
            }

    def _set_scan_worker_refs_if_current(self, run_id: int, cancel_event: threading.Event, worker_thread: Optional[threading.Thread]) -> bool:
        """Only the active scan run may own the shared worker references."""
        with self._scan_lock:
            if run_id != self._scan_run_id:
                return False
            self._scan_cancel_event = cancel_event
            self._scan_worker_thread = worker_thread
            # v3.2.2: transition from "starting" to "running" once the
            # worker thread is actually live. start_scan sets the initial
            # status to "starting" so concurrent /api/scan POSTs see the
            # in-flight slot before the background task picks up the
            # work; run_scan immediately calls this method to flip it
            # to "running".
            if self._scan_progress.get("status") == "starting":
                self._scan_progress = {**self._scan_progress, "status": "running"}
            return True

    def _set_scan_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        """Only the active scan run may replace shared progress state."""
        with self._scan_lock:
            if run_id != self._scan_run_id:
                return False
            self._scan_progress = state
            return True

    def _update_scan_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        """Only the active scan run may mutate shared progress state."""
        with self._scan_lock:
            if run_id != self._scan_run_id:
                return False
            self._scan_progress = {
                **self._scan_progress,
                **updates,
            }
            return True

    def _clear_scan_worker_refs_if_current(self, run_id: int) -> None:
        """Release scan worker references when the active run ends."""
        with self._scan_lock:
            if run_id != self._scan_run_id:
                return
            self._scan_cancel_event = None
            self._scan_worker_thread = None

    def get_sort_session(self) -> Dict[str, Any]:
        """Get the current sort session."""
        with self._sort_session_lock:
            return self._sort_session.copy()

    def get_sort_session_proxy(self) -> MutableStateProxy:
        """Expose the legacy dict-style sort-session handle from the service."""
        return self._sort_session_proxy


    def get_batch_move_progress(self) -> Dict[str, Any]:
        """Get the current batch move progress."""
        with self._batch_move_lock:
            return self._batch_move_progress.copy()

    def reset_batch_move_progress(self) -> Dict[str, Any]:
        """Reset batch move progress to idle."""
        with self._batch_move_lock:
            if self._batch_move_progress["status"] == "running":
                raise HTTPException(status_code=409, detail="Cannot reset batch move while it is still running")
            return {"status": self._batch_move_progress["status"], "message": "Nothing to reset"}

    def cancel_batch_move(self) -> Dict[str, Any]:
        """Request cooperative cancellation of the active batch-move task.

        Mirrors :meth:`cancel_scan`: flips the worker's cancel event and
        publishes a ``cancelling`` progress state so the UI can show a
        "Cancelling..." indicator while the worker walks to its next
        chunk/image boundary. The worker writes the terminal
        ``cancelled`` state itself once it observes the flag, so this
        method never overwrites a finished run's outcome.
        """
        with self._batch_move_lock:
            current_status = self._batch_move_progress.get("status")
            if current_status not in {"running", "cancelling"}:
                return {
                    "status": current_status,
                    "message": "No batch move task is running",
                }

            current = int(self._batch_move_progress.get("current", 0) or 0)
            total = int(self._batch_move_progress.get("total", 0) or 0)
            operation = self._batch_move_progress.get("operation", "move")

            if self._batch_move_cancel_event is not None:
                self._batch_move_cancel_event.set()

            verb = "copy" if operation == "copy" else "move"
            self._batch_move_progress["status"] = "cancelling"
            self._batch_move_progress["step"] = "cancelling"
            self._batch_move_progress["message"] = (
                f"Cancelling batch {verb}... ({current}/{total})"
                if total > 0
                else f"Cancelling batch {verb}..."
            )
            self._batch_move_progress["updated_at"] = time.time()
            return {"status": "cancelling", "message": "Batch move cancellation requested"}

    def _set_batch_move_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        """Only allow the active batch-move task to replace shared progress state."""
        with self._batch_move_lock:
            if run_id != self._batch_move_run_id:
                return False
            self._batch_move_progress = state
            return True

    def _update_batch_move_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        """Only allow the active batch-move task to mutate shared progress state."""
        with self._batch_move_lock:
            if run_id != self._batch_move_run_id:
                return False
            self._batch_move_progress = {
                **self._batch_move_progress,
                **updates,
            }
            return True

    # ------------------------------------------------------------------
    # v3.3.0 USR-1: background gallery move/copy job (progress + cancel).
    # Mirrors the batch-move helpers above.
    # ------------------------------------------------------------------
    def get_move_progress(self) -> Dict[str, Any]:
        """Get the current gallery move/copy job progress."""
        with self._move_lock:
            return self._move_progress.copy()

    def reset_move_progress(self) -> Dict[str, Any]:
        """Reset move job progress to idle (refused while still running)."""
        with self._move_lock:
            if self._move_progress["status"] == "running":
                raise HTTPException(status_code=409, detail="Cannot reset move while it is still running")
            return {"status": self._move_progress["status"], "message": "Nothing to reset"}

    def cancel_move(self) -> Dict[str, Any]:
        """Request cooperative cancellation of the active gallery move/copy job."""
        with self._move_lock:
            current_status = self._move_progress.get("status")
            if current_status not in {"running", "cancelling"}:
                return {"status": current_status, "message": "No move task is running"}

            current = int(self._move_progress.get("current", 0) or 0)
            total = int(self._move_progress.get("total", 0) or 0)
            operation = self._move_progress.get("operation", "move")

            if self._move_cancel_event is not None:
                self._move_cancel_event.set()

            verb = "copy" if operation == "copy" else "move"
            self._move_progress["status"] = "cancelling"
            self._move_progress["step"] = "cancelling"
            self._move_progress["message"] = (
                f"Cancelling {verb}... ({current}/{total})"
                if total > 0
                else f"Cancelling {verb}..."
            )
            self._move_progress["updated_at"] = time.time()
            return {"status": "cancelling", "message": "Move cancellation requested"}

    def _set_move_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        """Only allow the active move job to replace shared progress state."""
        with self._move_lock:
            if run_id != self._move_run_id:
                return False
            self._move_progress = state
            return True

    def _update_move_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        """Only allow the active move job to mutate shared progress state."""
        with self._move_lock:
            if run_id != self._move_run_id:
                return False
            self._move_progress = {
                **self._move_progress,
                **updates,
            }
            return True


    def set_sort_session(self, session: Dict[str, Any]) -> None:
        """Set the sort session."""
        with self._sort_session_lock:
            self._sort_session = self._coerce_sort_session_state(session)
