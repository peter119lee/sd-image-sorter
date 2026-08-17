"""Dataset export service.

Implements the user-flow that issue #5 point 6 was asking for:
"copy/move images and write matching .txt sidecars to one folder, all
renamed consistently". Previously the only way to get a LoRA training
dataset out was a two-step dance — Auto-Separate to move images,
then export-batch with beside_image to write captions next to them —
and the renaming feature didn't exist at all.

This service handles the whole thing in one transaction:

  1. Validate output folder (creates if missing, blocks traversal).
  2. For each row, plan the rename via ``dataset_naming.render_stem`` +
     ``resolve_collision`` (the streaming export path plans per-row so
     a 100k-image scan-token export does not materialise a full plan
     before the first file is written). ``dataset_naming.plan_renames``
     remains available as a batch helper for tests and small callers.
  3. For each non-skipped row:
       a. Copy or move the image to its renamed destination.
       b. Render the caption via the same export-template engine the
          rest of the app uses (so the user's blacklist / common-tags /
          underscore-to-space settings line up with the live preview
          they saw in the Dataset Maker UI).
       c. Write the caption to ``{stem}.txt`` next to the renamed image.
  4. If the image copy fails after the caption is partially written, we
     remove the orphaned caption file so the trainer doesn't see broken
     pairs.

Reuses:
  - ``services.dataset_naming``: deterministic stem + collision logic.
  - ``services.tag_export_service.build_sidecar_content``: same caption
    rendering pipeline as ``/api/tags/export-batch``.
  - ``utils.path_validation.validate_folder_path``: the same checks the
    other write endpoints use.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import database as db
from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_naming import NamingError, render_stem, resolve_collision
from services.bulk_job_service import (
    JOB_KIND_DATASET_EXPORT,
    BulkJobAlreadyRunningError,
    BulkJobHandle,
    get_bulk_job_service,
)
from services.dataset_session_service import (
    count_scan_manifest_paths,
    iter_scan_manifest_paths,
    virtual_image_record_for_path,
)
from services.tag_export_service import (
    NL_COMPOSE_MODES,
    VALID_OUTPUT_MODES,
    VALID_CONTENT_MODES,
    apply_caption_transforms,
    build_sidecar_content,
    compose_caption_with_nl,
)
from utils.path_validation import normalize_user_path, validate_folder_path

# ---------------------------------------------------------------------------
# Decomposition (2026-07): the constants, Pydantic models, pure helpers, and
# the two streaming engine functions live in the services/dataset_export/
# package, re-imported below. THIS module remains a real FILE and the single
# monkeypatch surface (tests/test_dataset_export_pins.py):
#   * Synchronous export dependencies remain available through this facade for
#     tests that patch export_service.shutil.copy2, des.db.update_image_path,
#     des.render_stem, and des.DATASET_EXPORT_RESPONSE_ITEM_LIMIT. The engine
#     reads those bindings back through _svc() at call time.
#   * Background lifecycle state belongs exclusively to BulkJobService. This
#     facade only adapts export progress and results into the shared job.
#   * Every Pydantic model is defined ONCE in services/dataset_export/models.py
#     and re-exported here, so the from-import bindings in routers/dataset.py
#     keep class identity for response_model coercion.
# ---------------------------------------------------------------------------
from services.dataset_export._constants import (
    DATASET_EXPORT_DB_CHUNK_SIZE,
    DATASET_EXPORT_RECENT_ERROR_LIMIT,
    DATASET_EXPORT_RESPONSE_ITEM_LIMIT,
    DATASET_LEGACY_TEMPLATE,
    EXPORT_MANIFEST_FILENAME,
    EXPORT_MANIFEST_VERSION,
    PACKAGE_HASH_CHUNK_SIZE,
    PACKAGE_INVENTORY_FILENAME,
    PACKAGE_MANIFEST_SCHEMA,
    PACKAGE_MANIFEST_VERSION,
    TRAINING_TAG_CONTENT_MODES,
    VALID_IMAGE_OPS,
    VALID_MASK_EXPORT_MODES,
    VALID_OVERWRITE_POLICIES,
    VALID_TRAINER_CONFIGS,
)
from services.dataset_export.models import (
    DatasetExportItemResult,
    DatasetExportPreviewRequest,
    DatasetExportRequest,
    DatasetExportResponse,
    DatasetExportStartResponse,
    DatasetPackageVerificationRequest,
    DatasetPackageVerificationResponse,
    ExportProgressCallback,
)
from services.dataset_export.planning import (
    _allocate_sidecar_path,
    _dataset_sidecar_extension,
    _iter_chunks,
    _iter_requested_scan_paths,
    _iter_unique_image_ids,
    _output_mode,
    _plan_beside_image_sidecar,
    _plan_single_rename,
    _reconcile_moved_image_path,
    _requested_item_count,
    _resolve_dataset_image_path,
)
from services.dataset_export.captions import (
    _NL_COMPOSE_MODES,
    _append_common_tags_for_mode,
    _build_dataset_template_options,
    _compose_nl_caption,
    _normalise_common_tag,
    _render_dataset_sidecar,
    _split_image_overrides,
    _split_keyed_str_map,
)
from services.dataset_export.artifacts import (
    _build_export_manifest,
    _manifest_item,
    _mask_export_mode,
    _validate_export_request,
    _validate_export_request_read_only,
    _write_export_manifest,
)
from services.dataset_export.kohya_contract import (
    _toml_path_literal,
    write_kohya_dataset_config as _write_kohya_dataset_config,
)
from services.dataset_export.anima_contract import (
    write_anima_dataset_config as _write_anima_dataset_config,
)
from services.dataset_export.package_integrity import (
    PackageIntegrityError,
    package_requested,
    preflight_package_targets,
    publish_pending_dataset_package,
    verify_dataset_package,
)
from services.dataset_export.engine import (
    export_dataset as _export_dataset_engine,
    export_dataset_job as _export_dataset_job_engine,
    preview_dataset_export,
)
from services.dataset_export.readiness_authorization import authorize_dataset_export


def export_dataset(
    request: DatasetExportRequest,
    *,
    progress_callback: Optional[ExportProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pending_package_run_id: Optional[str] = None,
) -> DatasetExportResponse:
    """Authorize and execute a synchronous public dataset export."""
    authorize_dataset_export(request)
    return _export_dataset_engine(
        request,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        pending_package_run_id=pending_package_run_id,
    )


def export_dataset_job(
    request: DatasetExportRequest,
    *,
    progress_callback: ExportProgressCallback,
    cancel_event: threading.Event,
    pending_package_run_id: str,
    completion_gate: Callable[[], bool],
) -> DatasetExportResponse:
    """Re-authorize at the async worker boundary before artifact writes."""
    authorize_dataset_export(request)
    output_path = _validate_export_request(request)
    if output_path is None:
        raise PackageIntegrityError(
            "Verified trainer package requires an output folder"
        )
    publish_pending_dataset_package(
        output_path,
        request,
        _requested_item_count(request),
        _dataset_sidecar_extension(request.content_mode),
        pending_package_run_id,
    )
    return _export_dataset_job_engine(
        request,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        pending_package_run_id=pending_package_run_id,
        completion_gate=completion_gate,
    )


def start_dataset_export(
    request: DatasetExportRequest,
    background_tasks: BackgroundTasks,
) -> DatasetExportStartResponse:
    """Queue a cancellable dataset export in the shared bulk-job registry."""
    authorize_dataset_export(request)
    output_mode = _output_mode(request)
    output_path = _validate_export_request_read_only(request)
    requested_total = _requested_item_count(request)
    service = get_bulk_job_service()
    package_run_id = uuid.uuid4().hex if package_requested(request) else None
    package_manifest_path = (
        str(output_path / EXPORT_MANIFEST_FILENAME)
        if output_path is not None and package_run_id is not None
        else None
    )
    queued_cancel_result = DatasetExportResponse(
        status="cancelled",
        exported=0,
        skipped=0,
        error_count=0,
        output_folder=str(output_path or ""),
        output_mode=output_mode,
        items=[],
        total_items=0,
        items_truncated=False,
        error_messages=[],
        package_status="incomplete" if package_requested(request) else "not_requested",
        package_run_id=package_run_id,
        package_manifest_path=package_manifest_path,
    ).model_dump(mode="json")
    try:
        job_id = service.create_single_flight_job(
            JOB_KIND_DATASET_EXPORT,
            total=requested_total,
            message=f"Starting dataset export for {requested_total} images...",
            queued_cancel_result=queued_cancel_result,
        )
    except BulkJobAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail="Dataset export already in progress",
        ) from exc

    def worker(handle: BulkJobHandle) -> None:
        progress: Dict[str, Any] = {
            "step": "starting",
            "current": 0,
            "total": requested_total,
            "exported": 0,
            "skipped": 0,
            "errors": 0,
            "current_item": None,
            "recent_errors": [],
            "output_folder": str(output_path or ""),
            "output_mode": output_mode,
            "items_truncated": False,
        }
        reported_error_count = 0

        def publish(updates: Dict[str, Any]) -> None:
            nonlocal progress, reported_error_count
            progress = {**progress, **updates}
            current = int(progress.get("current", 0) or 0)
            total = int(progress.get("total", requested_total) or requested_total)
            message = str(progress.get("message") or "Exporting dataset...")
            handle.set_progress(processed=current, total=total, message=message)
            current_error_count = int(progress.get("errors", 0) or 0)
            if current_error_count > reported_error_count:
                new_count = current_error_count - reported_error_count
                recent_errors = [str(item) for item in progress.get("recent_errors") or []]
                handle.record_errors(new_count, recent_errors[-new_count:])
                reported_error_count = current_error_count
            handle.set_result({"progress": dict(progress)})

        try:
            if package_run_id is None:
                result = export_dataset(
                    request,
                    progress_callback=publish,
                    cancel_event=handle.cancel_event,
                )
            else:
                result = export_dataset_job(
                    request,
                    progress_callback=publish,
                    cancel_event=handle.cancel_event,
                    pending_package_run_id=package_run_id,
                    completion_gate=handle.begin_completion,
                )
        except Exception as exc:
            detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            failure = DatasetExportResponse(
                status="failed",
                exported=int(progress.get("exported", 0) or 0),
                skipped=int(progress.get("skipped", 0) or 0),
                error_count=max(1, int(progress.get("errors", 0) or 0)),
                output_folder=str(output_path or ""),
                output_mode=output_mode,
                items=[],
                total_items=int(progress.get("current", 0) or 0),
                items_truncated=bool(progress.get("items_truncated", False)),
                error_messages=[detail],
                package_status="incomplete" if package_requested(request) else "not_requested",
                package_run_id=package_run_id,
                package_manifest_path=package_manifest_path,
            )
            handle.set_result(failure.model_dump(mode="json"))
            raise

        if result.error_count > reported_error_count:
            handle.record_errors(
                result.error_count - reported_error_count,
                result.error_messages[-(result.error_count - reported_error_count):],
            )
        current = int(progress.get("current", 0) or 0)
        total = int(progress.get("total", requested_total) or requested_total)
        message = (
            f"Cancelled at {current}/{total}. Exported {result.exported} images."
            if result.status == "cancelled"
            else (
                f"Dataset export finished: {result.exported} exported, "
                f"{result.error_count} failed, {result.skipped} skipped."
            )
        )
        result_payload = result.model_dump(mode="json")
        if result.status != "cancelled":
            handle.complete_result(
                result=result_payload,
                processed=result.total_items,
                total=total,
                message=message,
            )
            return
        handle.set_result(result_payload)
        handle.set_progress(
            processed=current,
            total=total,
            message=message,
        )

    try:
        background_tasks.add_task(service.run_job, job_id, worker)
    except Exception:
        service.cancel_job(job_id)
        raise

    return DatasetExportStartResponse(
        status="started",
        job_id=job_id,
        total=requested_total,
        output_folder=str(output_path or ""),
        message=f"Dataset export started for {requested_total} images.",
    )
