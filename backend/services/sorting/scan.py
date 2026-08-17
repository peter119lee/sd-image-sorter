"""start_scan: folder-scan orchestration (worker closure, heartbeat, progress).

Moved verbatim from services/sorting_service.py (decomposition 2026-07).
The ``scan_folder`` seam and the heartbeat tuning constant resolve through
the facade module at call time so existing monkeypatches keep landing.
"""

import logging
import sqlite3
import threading
import time
from fastapi import BackgroundTasks, HTTPException

import database as db
import image_manager as image_manager_module
from exceptions import ScanCancelledError, ScanError
from services import entry_stats_service
from services.gallery_job_gate import gallery_job_transition
from services.sorting_models import (
    BACKGROUND_SCAN_SOURCES,
    SCAN_ACTIVE_STATUSES,
    SCAN_SOURCE_MANUAL,
    SCAN_TERMINAL_STATUSES,
    VALID_SCAN_SOURCES,
    ScanRequest,
    ScanSource,
    ScanStartResult,
)
from utils.path_validation import (
    is_directory_symlink_or_junction,
    normalize_user_path,
    validate_folder_path,
)

# NOTE(decomposition): keep the historical logger channel — tests attach
# handlers / caplog filters to "services.sorting_service" (heartbeat pins),
# and log routing/output must stay byte-identical after the package split.
logger = logging.getLogger("services.sorting_service")


# Any real folder holds some images that legitimately carry no prompt
# (screenshots, hand-drawn art, photos). Only call the shortfall out in the
# summary once it is a large enough share of the folder that the user should
# suspect a metadata problem rather than a mixed folder.
SCAN_MISSING_PROMPT_NOTICE_RATIO = 0.10


def _prompt_coverage_for_scope(folder_path: str, recursive: bool) -> dict:
    """How many indexed images in this scan's scope ended up with no prompt.

    Advisory only: a failure here must not turn an otherwise-successful scan
    into an error, but it must not be reported as "zero missing" either — the
    counters come back ``None`` so an unknown coverage stays distinguishable
    from a clean one.
    """
    try:
        counts = db.count_prompt_coverage_in_folder_scope(folder_path, recursive)
        return {
            "total": int(counts.get("total") or 0),
            "missing_prompt": int(counts.get("missing_prompt") or 0),
        }
    except (sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning("Could not measure prompt coverage for %s: %s", folder_path, exc)
        return {"total": None, "missing_prompt": None}


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


def scan_folder(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.scan_folder)."""
    return _svc().scan_folder(*args, **kwargs)


class ScanMixin:
    """Scan-orchestration slice of SortingService (assembled in services/sorting_service.py)."""

    def start_scan(
        self,
        request: ScanRequest,
        background_tasks: BackgroundTasks,
        source: ScanSource,
    ) -> ScanStartResult:
        """Start a user-requested scan and register its runtime folder as a root."""
        return self._start_scan(
            request,
            background_tasks,
            source,
            root_record_path=normalize_user_path(request.folder_path),
            registered_root_id=None,
        )

    def start_registered_root_scan(
        self,
        request: ScanRequest,
        background_tasks: BackgroundTasks,
        source: ScanSource,
        *,
        root_record_path: str,
        root_id: int,
    ) -> ScanStartResult:
        """Start a registered-root scan while preserving its stored display path."""
        return self._start_scan(
            request,
            background_tasks,
            source,
            root_record_path=root_record_path,
            registered_root_id=int(root_id),
        )

    def _start_scan(
        self,
        request: ScanRequest,
        background_tasks: BackgroundTasks,
        source: ScanSource,
        *,
        root_record_path: str,
        registered_root_id: int | None,
    ) -> ScanStartResult:
        """Start scanning a folder for images."""
        if source not in VALID_SCAN_SOURCES:
            raise ValueError(f"Unsupported scan source: {source}")
        normalized_folder_path = normalize_user_path(request.folder_path)
        is_valid, error = validate_folder_path(normalized_folder_path)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid folder path")
        if is_directory_symlink_or_junction(normalized_folder_path):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Scan root cannot be a symbolic link or Windows junction. "
                    "Choose the real target folder."
                ),
            )

        with gallery_job_transition(), self._scan_lock:
            if (
                registered_root_id is not None
                and db.get_library_root(registered_root_id) is None
            ):
                raise HTTPException(status_code=404, detail="Library root not found")
            current_status = self._scan_progress["status"]
            current_source = self._scan_progress.get("source")
            worker_alive = bool(self._scan_worker_thread and self._scan_worker_thread.is_alive())
            # v3.2.2: previously this only rejected when ``status == 'running'``
            # AND ``worker_alive``. Three concurrent POSTs to /api/scan all
            # squeezed through the gate because the worker thread isn't
            # created until later (background_tasks schedules it after the
            # lock is released), so the second/third callers all observed
            # worker_alive=False and incorrectly believed nothing was
            # running. Result: three "Scan started" 200 responses but only
            # one real scan with the others left in an inconsistent
            # progress state.
            #
            # Fix: any non-terminal status counts as "in progress". Stale
            # state (status=running but worker died) is recovered through
            # /api/scan/reset which the UI's "Reset stuck scan" button
            # already calls; we should not silently overwrite it here.
            if current_status in SCAN_ACTIVE_STATUSES:
                if worker_alive or current_status in {"starting", "cancelling"}:
                    raise HTTPException(status_code=409, detail="Scan already in progress")
                raise HTTPException(
                    status_code=409,
                    detail="Previous scan is in a stale state. Call /api/scan/reset first.",
                )
            if (
                source in BACKGROUND_SCAN_SOURCES
                and current_status in SCAN_TERMINAL_STATUSES
                and current_source in {None, SCAN_SOURCE_MANUAL}
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "manual_completion_pending",
                        "message": (
                            "A manual scan result is waiting to be acknowledged. "
                            "Acknowledge that result before starting a library refresh."
                        ),
                        "run_id": self._scan_progress.get("run_id"),
                        "source": current_source,
                        "status": current_status,
                    },
                )

            self._scan_run_id += 1
            run_id = self._scan_run_id
            cancel_event = threading.Event()
            started_at = time.time()
            self._scan_cancel_event = cancel_event
            self._scan_worker_thread = None
            self._scan_progress = {
                "run_id": run_id,
                "source": source,
                "root_record_path": root_record_path,
                "registered_root_id": registered_root_id,
                "suppress_root_record": False,
                "status": "starting",
                "step": "starting",
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
                "quick_import": request.quick_import,
                "metadata_processed": 0,
                "metadata_total": 0,
                "metadata_total_final": False,
                "metadata_pending": 0,
                "metadata_prompt_total": None,
                "metadata_missing_prompt": None,
                "message": "正在同步文件夹索引 / Syncing folder index..." if request.cleanup_missing else "导入前统计图片数量 / Counting images before import...",
                "current_item": None,
                "recent_errors": [],
                "started_at": started_at,
                "updated_at": started_at,
            }

        def run_scan():
            if not self._set_scan_worker_refs_if_current(run_id, cancel_event, threading.current_thread()):
                return

            try:
                logger.info(
                    "Scan started: folder=%s recursive=%s quick_import=%s cleanup_missing=%s force_reparse=%s metadata_workers=%s metadata_backlog_limit=%s metadata_timeout=%ss heartbeat=%ss",
                    normalized_folder_path,
                    request.recursive,
                    request.quick_import,
                    request.cleanup_missing,
                    request.force_reparse,
                    image_manager_module.DEFAULT_METADATA_WORKERS,
                    image_manager_module._metadata_backlog_limit(image_manager_module.DEFAULT_METADATA_WORKERS),
                    image_manager_module.SCAN_METADATA_TIMEOUT_SECONDS,
                    _svc().SCAN_LOG_HEARTBEAT_SECONDS,
                )

                heartbeat_stop = threading.Event()

                def heartbeat_loop() -> None:
                    if _svc().SCAN_LOG_HEARTBEAT_SECONDS <= 0:
                        return
                    while not heartbeat_stop.wait(_svc().SCAN_LOG_HEARTBEAT_SECONDS):
                        progress = self.get_scan_progress()
                        if progress.get("status") not in {"running", "cancelling"}:
                            continue
                        heartbeat_now = time.time()
                        updated_at = float(progress.get("updated_at") or progress.get("started_at") or heartbeat_now)
                        started = float(progress.get("started_at") or started_at or heartbeat_now)
                        logger.info(
                            "Scan heartbeat: folder=%s status=%s step=%s processed=%s/%s counted=%s import_complete=%s library_ready=%s metadata=%s/%s pending=%s errors=%s current=%s idle_for=%.1fs elapsed=%.1fs",
                            normalized_folder_path,
                            progress.get("status", "unknown"),
                            progress.get("step", "unknown"),
                            progress.get("processed", progress.get("current", 0)),
                            progress.get("total", 0) if progress.get("total_final") else "?",
                            progress.get("counted", 0),
                            progress.get("import_complete", False),
                            progress.get("library_ready", False),
                            progress.get("metadata_processed", 0),
                            progress.get("metadata_total", 0),
                            progress.get("metadata_pending", 0),
                            progress.get("errors", 0),
                            progress.get("current_item") or "-",
                            max(0.0, heartbeat_now - updated_at),
                            max(0.0, heartbeat_now - started),
                        )

                heartbeat_thread = threading.Thread(
                    target=heartbeat_loop,
                    name=f"scan-heartbeat-{run_id}",
                    daemon=True,
                )
                heartbeat_thread.start()

                def progress_cb(current, total, filename, details=None):
                    now = time.time()
                    details = details or {}
                    # Read shared scan state through a single locked snapshot. The route
                    # handler reads/writes self._scan_progress under self._scan_lock via
                    # get_scan_progress(); reading the dict directly here would be a data
                    # race. One snapshot also gives a consistent view across all fields.
                    prev_progress = self.get_scan_progress()
                    last_error = details.get("last_error") if isinstance(details, dict) else None
                    phase = details.get("phase") if isinstance(details, dict) else None
                    library_ready = bool(details.get("library_ready", prev_progress.get("library_ready", False))) if isinstance(details, dict) else prev_progress.get("library_ready", False)
                    metadata_processed = int(details.get("metadata_processed", prev_progress.get("metadata_processed", 0)) or 0) if isinstance(details, dict) else int(prev_progress.get("metadata_processed", 0) or 0)
                    metadata_total = int(details.get("metadata_total", prev_progress.get("metadata_total", 0)) or 0) if isinstance(details, dict) else int(prev_progress.get("metadata_total", 0) or 0)
                    metadata_total_final = bool(details.get("metadata_total_final", prev_progress.get("metadata_total_final", False))) if isinstance(details, dict) else bool(prev_progress.get("metadata_total_final", False))
                    total_final = bool(details.get("total_final", prev_progress.get("total_final", False))) if isinstance(details, dict) else bool(prev_progress.get("total_final", False))
                    counted = int(details.get("counted", prev_progress.get("counted", 0)) or 0) if isinstance(details, dict) else int(prev_progress.get("counted", 0) or 0)
                    import_processed = int(details.get("import_processed", current) or 0) if isinstance(details, dict) else int(current or 0)
                    import_total = int(details.get("import_total", total) or 0) if isinstance(details, dict) else int(total or 0)
                    import_complete = bool(details.get("import_complete", prev_progress.get("import_complete", False))) if isinstance(details, dict) else bool(prev_progress.get("import_complete", False))
                    metadata_pending = int(details.get("metadata_pending", prev_progress.get("metadata_pending", 0)) or 0) if isinstance(details, dict) else int(prev_progress.get("metadata_pending", 0) or 0)
                    state_current = import_processed
                    state_total = import_total or total
                    message = f"正在处理 / Processing: {filename}" if filename else "正在扫描文件 / Scanning files..."
                    current_item = filename or None
                    step = "importing"
                    status = "running"
                    removed_count = details.get("removed", prev_progress.get("removed", 0)) if isinstance(details, dict) else prev_progress.get("removed", 0)

                    if phase == "counting":
                        state_current = counted or current
                        state_total = 0
                        message = f"正在统计图片（已发现 {state_current} 张）/ Counting images ({state_current} found)"
                        current_item = None
                        step = "counting"
                    elif phase == "counted":
                        state_current = 0
                        state_total = import_total or total
                        message = f"共发现 {state_total} 张图片，开始导入 / Found {state_total} images, importing..."
                        current_item = None
                        step = "importing"
                    elif phase == "cleanup":
                        message = (
                            f"文件夹同步完成，移除 {removed_count} 条失效记录 / "
                            f"Folder sync complete. Removed {removed_count} missing entr"
                            f"{'y' if removed_count == 1 else 'ies'}."
                        )
                        current_item = None
                        step = "cleanup"
                    elif phase == "library_ready":
                        step = "metadata" if import_complete and metadata_total > metadata_processed else "importing"
                        current_item = None
                        if import_complete and metadata_total > 0:
                            message = f"图库已就绪，后台补齐图片详情（{metadata_processed}/{metadata_total}）/ Library ready, finishing metadata ({metadata_processed}/{metadata_total})..."
                        else:
                            message = f"图库已可浏览，后台继续导入（{state_current}/{state_total or '?'}）/ Library browseable, import continues ({state_current}/{state_total or '?'})..."
                    elif phase == "metadata":
                        if import_complete:
                            step = "metadata"
                            message = f"正在读取图片详情 / Reading details: {filename}" if filename else "正在读取图片详情 / Reading image details..."
                            current_item = filename or None
                        else:
                            step = "importing"
                            message = (
                                f"正在导入并读取详情（{state_current}/{state_total}）/ Importing and reading details ({state_current}/{state_total})"
                                if state_total > 0
                                else "正在导入并读取详情 / Importing and reading details..."
                            )
                            current_item = None
                    elif not total_final:
                        state_current = counted or current
                        state_total = 0
                        message = f"正在统计图片（已发现 {state_current} 张）/ Counting images ({state_current} found)"
                        current_item = None
                        step = "counting"

                    if last_error:
                        message = (
                            f"已跳过无法读取的图片 / Skipped unreadable image: {last_error.get('filename', filename)}"
                            f" ({last_error.get('error', 'Unreadable image')})"
                        )
                    if cancel_event.is_set():
                        status = "cancelling"
                        step = "cancelling"
                        message = (
                            f"正在取消扫描（{state_current}/{state_total}）/ Cancelling scan ({state_current}/{state_total})"
                            if total_final and state_total > 0
                            else f"正在取消扫描（已扫 {state_current}）/ Cancelling scan ({state_current} scanned)"
                        )
                    self._update_scan_progress_if_current(
                        run_id,
                        status=status,
                        current=state_current,
                        processed=state_current,
                        total=state_total,
                        counted=counted,
                        total_final=total_final,
                        import_complete=import_complete,
                        step=step,
                        errors=details.get("errors", prev_progress.get("errors", 0)) if isinstance(details, dict) else prev_progress.get("errors", 0),
                        removed=removed_count,
                        library_ready=library_ready,
                        quick_import=request.quick_import,
                        metadata_processed=metadata_processed,
                        metadata_total=metadata_total,
                        metadata_total_final=metadata_total_final,
                        metadata_pending=metadata_pending,
                        message=message,
                        current_item=current_item,
                        updated_at=now,
                    )

                result = scan_folder(
                    normalized_folder_path,
                    request.recursive,
                    progress_cb,
                    stop_requested=cancel_event.is_set,
                    force_reparse=request.force_reparse,
                    cleanup_missing=request.cleanup_missing,
                    quick_import=request.quick_import,
                )
                now = time.time()
                errors = result.get("errors", 0)
                new_count = result.get("new", 0)
                updated_count = result.get("updated", 0)
                removed_count = result.get("removed", 0)
                prompt_coverage = _prompt_coverage_for_scope(
                    normalized_folder_path, request.recursive
                )
                prompt_total = prompt_coverage["total"]
                missing_prompt = prompt_coverage["missing_prompt"]
                summary = f"完成！已索引 {new_count} 张图片 / Done! {new_count} images indexed."
                if updated_count:
                    summary += f" 更新 {updated_count} 张 / {updated_count} updated."
                if removed_count:
                    summary += f" 移除 {removed_count} 条失效记录 / {removed_count} missing removed."
                if errors:
                    summary += f" {errors} 个问题 / {errors} scan issue(s)."
                # "Done! N indexed." on its own reads as a clean success even
                # when most of those rows stored no prompt at all.
                if (
                    prompt_total
                    and missing_prompt
                    and missing_prompt >= prompt_total * SCAN_MISSING_PROMPT_NOTICE_RATIO
                ):
                    summary += (
                        f" {missing_prompt}/{prompt_total} 张没有提示词，"
                        "可在设置中运行「重新解析缺失提示词」/ "
                        f"{missing_prompt} of {prompt_total} images have no prompt "
                        "stored — run Re-parse Missing Prompts in Settings."
                    )
                recent_errors = result.get("recent_errors") or []
                if recent_errors:
                    filenames = ", ".join(item.get("filename", "unknown") for item in recent_errors[-3:])
                    summary += f" 问题项 / Issues: {filenames}."
                duration_seconds = max(0.0, now - float(self._scan_progress.get("started_at") or now))
                metadata_processed = result.get("metadata_processed", 0)
                metadata_total = result.get("metadata_total", 0)

                # v3.3.2 Library Navigation: scan completion includes atomically
                # registering the root used by navigation and idle auto-refresh.
                entry_stats_service.record_activity(
                    entry_stats_service.KIND_ADDED, new_count
                )
                with gallery_job_transition(), self._scan_lock:
                    should_record_root = (
                        run_id == self._scan_run_id
                        and not self._scan_progress.get("suppress_root_record", False)
                    )
                    if should_record_root:
                        try:
                            db.record_library_root_scan(root_record_path)
                        except (sqlite3.Error, RuntimeError, ValueError) as exc:
                            raise ScanError(
                                message=(
                                    "Image indexing completed, but Library Root persistence "
                                    f"failed for '{root_record_path}': {exc}. "
                                    "Check database write access and scan this folder again."
                                ),
                                path=None,
                                details={
                                    "folder_path": root_record_path,
                                    "database_error": str(exc),
                                },
                            ) from exc

                logger.info(
                    "Scan completed: folder=%s files=%s indexed_new=%s unchanged_or_updated=%s removed=%s metadata=%s/%s missing_prompt=%s/%s errors=%s duration=%.1fs",
                    normalized_folder_path,
                    result.get("total", 0),
                    new_count,
                    updated_count,
                    removed_count,
                    metadata_processed,
                    metadata_total,
                    missing_prompt,
                    prompt_total,
                    errors,
                    duration_seconds,
                )

                if recent_errors:
                    samples = "; ".join(
                        f"{item.get('filename', 'unknown')}: {item.get('error', 'Scan issue')}"
                        for item in recent_errors[-3:]
                    )
                    logger.warning(
                        "Scan completed with %s issue(s). Samples: %s. Open Scan Progress in the UI for recent errors; set SD_IMAGE_SORTER_LOG_LEVEL=DEBUG for parser tracebacks.",
                        errors,
                        samples,
                    )

                self._set_scan_progress_if_current(
                    run_id,
                    {
                        "run_id": run_id,
                        "source": source,
                        "status": "done",
                        "step": "done",
                        "current": result["total"],
                        "processed": result["total"],
                        "total": result["total"],
                        "counted": result.get("counted", result["total"]),
                        "total_final": result.get("total_final", True),
                        "import_complete": result.get("import_complete", True),
                        "errors": errors,
                        "new": new_count,
                        "updated": updated_count,
                        "skipped_other_library": int(result.get("skipped_other_library") or 0),
                        "skipped_other_library_paths": list(
                            result.get("skipped_other_library_paths") or []
                        )[:200],
                        "removed": removed_count,
                        "library_ready": result.get("library_ready", request.quick_import),
                        "quick_import": request.quick_import,
                        "metadata_processed": result.get("metadata_processed", 0),
                        "metadata_total": result.get("metadata_total", 0),
                        "metadata_total_final": result.get("metadata_total_final", True),
                        "metadata_pending": 0,
                        "metadata_prompt_total": prompt_total,
                        "metadata_missing_prompt": missing_prompt,
                        "message": summary,
                        "current_item": None,
                        "started_at": self._scan_progress.get("started_at"),
                        "updated_at": now,
                        "result": result,
                        "recent_errors": recent_errors,
                    }
                )
            except Exception as e:
                now = time.time()
                if isinstance(e, ScanCancelledError):
                    current_state = self.get_scan_progress()
                    logger.info(
                        "Scan cancelled: folder=%s processed=%s total=%s errors=%s",
                        normalized_folder_path,
                        current_state.get("processed", current_state.get("current", 0)),
                        current_state.get("total", 0),
                        current_state.get("errors", 0),
                    )
                    self._set_scan_progress_if_current(
                        run_id,
                        {
                            "run_id": run_id,
                            "source": source,
                            "status": "cancelled",
                            "step": "cancelled",
                            "current": current_state.get("current", 0),
                            "processed": current_state.get("processed", current_state.get("current", 0)),
                            "total": current_state.get("total", 0),
                            "counted": current_state.get("counted", 0),
                            "total_final": current_state.get("total_final", False),
                            "import_complete": current_state.get("import_complete", False),
                            "errors": current_state.get("errors", 0),
                            "new": current_state.get("new", 0),
                            "updated": current_state.get("updated", 0),
                            "removed": current_state.get("removed", 0),
                            "library_ready": current_state.get("library_ready", False),
                            "quick_import": current_state.get("quick_import", True),
                            "metadata_processed": current_state.get("metadata_processed", 0),
                            "metadata_total": current_state.get("metadata_total", 0),
                            "metadata_total_final": current_state.get("metadata_total_final", False),
                            "metadata_pending": current_state.get("metadata_pending", 0),
                            "metadata_prompt_total": current_state.get("metadata_prompt_total"),
                            "metadata_missing_prompt": current_state.get("metadata_missing_prompt"),
                            "message": (
                                f"扫描已取消（{current_state.get('processed', current_state.get('current', 0))}/{current_state.get('total', 0)}）/ Scan cancelled at {current_state.get('processed', current_state.get('current', 0))}/{current_state.get('total', 0)}."
                                if current_state.get("total_final", False) and current_state.get("total", 0)
                                else f"扫描已取消（已扫 {current_state.get('processed', current_state.get('current', 0))}）/ Scan cancelled after {current_state.get('processed', current_state.get('current', 0))} scanned."
                            ),
                            "current_item": current_state.get("current_item"),
                            "recent_errors": current_state.get("recent_errors", []),
                            "started_at": current_state.get("started_at"),
                            "updated_at": now,
                        }
                    )
                else:
                    current_state = self.get_scan_progress()
                    logger.exception(
                        "Scan failed: folder=%s processed=%s total=%s errors=%s",
                        normalized_folder_path,
                        current_state.get("processed", current_state.get("current", 0)),
                        current_state.get("total", 0),
                        current_state.get("errors", 0),
                    )
                    failure_message = (
                        f"扫描失败：{e.message} / Scan failed: {e.message}"
                        if isinstance(e, ScanError)
                        else "扫描因内部错误失败 / Scan failed due to an internal error"
                    )
                    self._set_scan_progress_if_current(
                        run_id,
                        {
                            "run_id": run_id,
                            "source": source,
                            "status": "error",
                            "step": "error",
                            "current": current_state.get("current", 0),
                            "processed": current_state.get("processed", current_state.get("current", 0)),
                            "total": current_state.get("total", 0),
                            "counted": current_state.get("counted", 0),
                            "total_final": current_state.get("total_final", False),
                            "import_complete": current_state.get("import_complete", False),
                            "errors": current_state.get("errors", 0),
                            "new": current_state.get("new", 0),
                            "updated": current_state.get("updated", 0),
                            "removed": current_state.get("removed", 0),
                            "library_ready": current_state.get("library_ready", False),
                            "quick_import": current_state.get("quick_import", True),
                            "metadata_processed": current_state.get("metadata_processed", 0),
                            "metadata_total": current_state.get("metadata_total", 0),
                            "metadata_total_final": current_state.get("metadata_total_final", False),
                            "metadata_pending": current_state.get("metadata_pending", 0),
                            "metadata_prompt_total": current_state.get("metadata_prompt_total"),
                            "metadata_missing_prompt": current_state.get("metadata_missing_prompt"),
                            "message": failure_message,
                            "current_item": current_state.get("current_item"),
                            "recent_errors": current_state.get("recent_errors", []),
                            "started_at": current_state.get("started_at"),
                            "updated_at": now,
                        }
                    )
            finally:
                if "heartbeat_stop" in locals():
                    heartbeat_stop.set()
                if "heartbeat_thread" in locals() and heartbeat_thread.is_alive():
                    heartbeat_thread.join(timeout=0.5)
                current_state = self.get_scan_progress()
                if current_state["status"] == "running":
                    self._update_scan_progress_if_current(
                        run_id,
                        status="error",
                        step="error",
                        message="扫描意外中止 / Scan ended unexpectedly",
                        updated_at=time.time(),
                    )
                self._clear_scan_worker_refs_if_current(run_id)

        background_tasks.add_task(run_scan)
        return {
            "status": "started",
            "message": "扫描已在后台开始 / Scan started in background",
            "run_id": run_id,
            "source": source,
        }
