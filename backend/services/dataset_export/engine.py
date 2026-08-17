"""The two streaming engine functions: export_dataset + preview_dataset_export.

Moved verbatim from services/dataset_export_service.py (decomposition 2026-07) except the five manifested lines:
the closures read DATASET_EXPORT_RESPONSE_ITEM_LIMIT /
DATASET_EXPORT_RECENT_ERROR_LIMIT / DATASET_EXPORT_DB_CHUNK_SIZE through
_svc() at call time because tests patch them on the facade module object
(tests/test_dataset_export_pins.py pins the item-limit read explicitly). The
``shutil`` / ``database`` module singletons are patched on their origin
objects (export_service.shutil.copy2 / des.db.update_image_path) and move
freely; the lazy in-function imports (services.mask_service,
urllib.parse.quote) are origin-module seams and stay verbatim.

[SAFETY] copy never touches the original (shutil.copy2); only move relocates.
[SAFETY] beside_image is a pure sidecar write — it never copies or relocates.
[SAFETY] a missing stored mask is COUNTED (masks_missing), never errored.
BulkJobService owns the asynchronous lifecycle outside this module;
cancellation arrives via cancel_event and progress leaves via progress_callback.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, TypeAlias

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

import database as db
from caption_format import caption_format_for_storage
from services.caption_dialect import nl_compose_advisory
from services.dataset_bucket_service import (
    BucketTransformError,
    apply_center_bucket_resize,
    apply_subject_aware_bucket_resize,
)
from services.dataset_crop_service import SubjectCropError, apply_subject_crop
from services.dataset_image_geometry import (
    normalize_mask_orientation,
    normalize_source_orientation,
)
from services.watermark_service import (
    WatermarkRegion,
    WatermarkRemovalConfig,
    WatermarkServiceError,
    apply_watermark_removal,
)
from services.dataset_export._constants import VALID_OVERWRITE_POLICIES
from services.dataset_export.artifacts import (
    _build_export_manifest,
    _invalidate_existing_anima_config,
    _mask_export_mode,
    _trainer_config_mode,
    _validate_export_request,
    _write_export_manifest,
)
from services.dataset_export.captions import (
    _render_dataset_sidecar,
    _split_image_overrides,
    _split_keyed_str_map,
    caption_dialect_advisories,
    project_target_model,
    render_training_caption_content,
)
from services.dataset_export.annotations import (
    AnnotationProvenance,
    annotation_selection_key,
    resolve_annotation_selections,
    validate_annotation_selection_coverage,
)
from services.dataset_export.models import (
    DatasetExportItemResult,
    DatasetExportPreviewRequest,
    DatasetExportRequest,
    DatasetExportResponse,
    DatasetExportWarning,
    ExportProgressCallback,
)
from services.dataset_export.anima_contract import (
    AnimaTrainerContractError,
    write_anima_dataset_config as _write_anima_dataset_config,
)
from services.dataset_export.kohya_contract import (
    KohyaTrainerContractError,
    write_kohya_dataset_config as _write_kohya_dataset_config,
)
from services.dataset_export.planning import (
    _dataset_sidecar_extension,
    _iter_chunks,
    _iter_requested_scan_paths,
    _iter_unique_image_ids,
    _output_mode,
    _paths_share_file_identity,
    _plan_mask_destination,
    _plan_beside_image_sidecar,
    _plan_single_pair,
    _plan_single_training_pair,
    _reconcile_moved_image_path,
    _requested_item_count,
    _resolve_dataset_image_path,
)
from services.dataset_export.package_integrity import (
    DatasetPackageBuild,
    PackageIntegrityError,
    PackageLockError,
    PackageOwnershipError,
    abort_dataset_package,
    begin_dataset_package,
    build_inventory_record,
    copy_package_file_atomic,
    finalize_dataset_package,
    package_requested,
    resume_pending_dataset_package,
    write_package_text_atomic,
)
from services.dataset_session_service import virtual_image_record_for_path
from services.tag_export_service import VALID_CONTENT_MODES, VALID_OUTPUT_MODES
from utils.atomic_staging import (
    create_staging_sibling,
    discard_staging_file,
    publish_staging_file,
)
from utils.path_validation import normalize_user_path


_PIL_OUTPUT_FORMATS = {
    ".bmp": "BMP",
    ".gif": "GIF",
    ".jpeg": "JPEG",
    ".jpg": "JPEG",
    ".png": "PNG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".webp": "WEBP",
}
_SOFT_ALPHA_OUTPUT_EXTENSIONS = frozenset({".png", ".tif", ".tiff", ".webp"})
StagedRowFile: TypeAlias = tuple[Path, Path]
_LOGGER = logging.getLogger(__name__)


def _pillow_save_options(image_format: str) -> dict[str, int | bool]:
    """Return the explicit quality policy for transformed training images."""
    if image_format == "JPEG":
        return {"quality": 95, "subsampling": 0, "optimize": True}
    if image_format == "WEBP":
        return {"lossless": True, "quality": 100, "method": 6}
    if image_format == "PNG":
        return {"compress_level": 9, "optimize": True}
    return {}


def _write_pillow_image_atomic(image: Image.Image, target: Path) -> None:
    """Encode a transformed image to a sibling staging file, then publish it.

    Both steps go through ``utils.atomic_staging``: ``tempfile`` cannot be used
    to stage beside a destination because on Windows ``mkstemp`` reads a
    ``PermissionError`` as a name collision and retries it up to ``TMP_MAX`` —
    2,147,483,647 on the shipped interpreter, not the 10,000 the docs imply — so
    an output folder this process cannot write to used to hang the export job
    instead of failing it.

    Every caller passes a staging path allocated by
    :func:`_allocate_row_staging_path`, never a user file, so the hard-link
    branch inside ``publish_staging_file`` is unreachable from here — a row's
    real publish is :func:`_publish_staged_row`.
    """
    image_format = _PIL_OUTPUT_FORMATS.get(target.suffix.lower())
    if image_format is None:
        raise ValueError(f"Unsupported transformed image extension: {target.suffix!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging, descriptor = create_staging_sibling(target)
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        discard_staging_file(staging)
        raise
    try:
        with handle:
            image.save(
                handle,
                format=image_format,
                **_pillow_save_options(image_format),
            )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        publish_staging_file(staging, target)
    except BaseException:
        discard_staging_file(staging)
        raise


def _allocate_row_staging_path(target: Path) -> Path:
    """Allocate a unique, absent sibling path that preserves the target suffix.

    Shares ``utils.atomic_staging``'s bounded ``O_CREAT | O_EXCL`` search for the
    same reason as the writer above: this runs FIRST for every row artifact, so a
    ``tempfile`` retry storm here hung the export before any writer was reached.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging_path, descriptor = create_staging_sibling(target)
    os.close(descriptor)
    staging_path.unlink()
    return staging_path


def _write_text_file_atomic(text: str, target: Path) -> None:
    """Write one UTF-8 text file through a sibling temporary path."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _allocate_row_staging_path(target)
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(str(temp_path), str(target))
    finally:
        temp_path.unlink(missing_ok=True)


def _rollback_published_row(
    published: Sequence[tuple[Path, Optional[Path]]],
) -> list[str]:
    """Restore replaced targets or remove newly published targets in reverse order."""
    errors: list[str] = []
    for target, backup in reversed(published):
        try:
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(str(backup), str(target))
        except OSError as exc:
            errors.append(f"target={target}, error={exc}")
    return errors


def _publish_staged_row(
    staged_files: Sequence[StagedRowFile],
) -> List[DatasetExportWarning]:
    """Publish a fully encoded row and restore previous files on any failure."""
    published: list[tuple[Path, Optional[Path]]] = []
    warnings: List[DatasetExportWarning] = []
    current_target: Optional[Path] = None
    current_backup: Optional[Path] = None
    try:
        for staged_path, target in staged_files:
            current_target = target
            current_backup = None
            if target.exists():
                backup_path = _allocate_row_staging_path(target)
                os.replace(str(target), str(backup_path))
                current_backup = backup_path
            try:
                os.replace(str(staged_path), str(target))
            except OSError:
                if current_backup is not None:
                    os.replace(str(current_backup), str(target))
                    current_backup = None
                raise
            published.append((target, current_backup))
            current_target = None
            current_backup = None
    except OSError as exc:
        rollback_errors = _rollback_published_row(published)
        if current_target is not None and current_backup is not None:
            try:
                os.replace(str(current_backup), str(current_target))
            except OSError as rollback_exc:
                rollback_errors.append(
                    f"target={current_target}, error={rollback_exc}"
                )
        if rollback_errors:
            raise OSError(
                "Dataset row publish failed and rollback was incomplete: "
                f"publish_error={exc}; rollback_errors={rollback_errors}"
            ) from exc
        raise
    for _target, backup in published:
        if backup is None:
            continue
        try:
            backup.unlink(missing_ok=True)
        except OSError as exc:
            warning = DatasetExportWarning(
                code="backup_cleanup_failed",
                message=(
                    "The exported row is complete, but an old backup could not be removed. "
                    "After verifying the new output, delete the retained backup manually."
                ),
                backup_path=str(backup),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            warnings.append(warning)
            _LOGGER.warning(
                "Dataset row backup cleanup failed",
                extra={
                    "backup_path": warning.backup_path,
                    "error_type": warning.error_type,
                    "error": warning.error,
                },
            )
    return warnings


def _write_transformed_row_atomic(
    image: Image.Image,
    image_target: Path,
    caption_text: str,
    caption_target: Path,
    mask: Optional[Image.Image],
    mask_target: Optional[Path],
) -> List[DatasetExportWarning]:
    """Encode every transformed row artifact before publishing any final path."""
    if (mask is None) != (mask_target is None):
        raise ValueError(
            "Transformed mask pixels and destination must either both be present or both absent"
        )
    staged_files: list[StagedRowFile] = []
    try:
        image_staging = _allocate_row_staging_path(image_target)
        _write_pillow_image_atomic(image, image_staging)
        staged_files.append((image_staging, image_target))

        caption_staging = _allocate_row_staging_path(caption_target)
        _write_text_file_atomic(caption_text, caption_staging)
        staged_files.append((caption_staging, caption_target))

        if mask is not None and mask_target is not None:
            mask_staging = _allocate_row_staging_path(mask_target)
            _write_pillow_image_atomic(mask.convert("L"), mask_staging)
            staged_files.append((mask_staging, mask_target))

        return _publish_staged_row(staged_files)
    finally:
        for staged_path, _target in staged_files:
            staged_path.unlink(missing_ok=True)


def _write_copied_row_atomic(
    image_source: Path,
    image_target: Path,
    caption_text: str,
    caption_target: Path,
    mask_source: Optional[Path],
    mask_target: Optional[Path],
) -> List[DatasetExportWarning]:
    """Copy an image/caption/optional-mask row before publishing final paths."""
    if (mask_source is None) != (mask_target is None):
        raise ValueError(
            "Stored mask source and destination must either both be present or both absent"
        )
    staged_files: list[StagedRowFile] = []
    try:
        image_staging = _allocate_row_staging_path(image_target)
        shutil.copy2(str(image_source), str(image_staging))
        staged_files.append((image_staging, image_target))

        caption_staging = _allocate_row_staging_path(caption_target)
        _write_text_file_atomic(caption_text, caption_staging)
        staged_files.append((caption_staging, caption_target))

        if mask_source is not None and mask_target is not None:
            mask_staging = _allocate_row_staging_path(mask_target)
            shutil.copy2(str(mask_source), str(mask_staging))
            staged_files.append((mask_staging, mask_target))

        return _publish_staged_row(staged_files)
    finally:
        for staged_path, _target in staged_files:
            staged_path.unlink(missing_ok=True)


def _validate_row_source_destinations(
    source_image: Path,
    image_target: Path,
    source_mask: Optional[Path],
    mask_target: Optional[Path],
) -> None:
    """Reject any output path that aliases an image or stored-mask source."""
    path_pairs: list[tuple[str, Optional[Path], str, Optional[Path]]] = [
        ("image", source_image, "image", image_target),
        ("image", source_image, "mask", mask_target),
        ("stored mask", source_mask, "image", image_target),
        ("stored mask", source_mask, "mask", mask_target),
    ]
    for source_label, source_path, target_label, target_path in path_pairs:
        if source_path is None or target_path is None:
            continue
        if not _paths_share_file_identity(source_path, target_path):
            continue
        source_description = (
            "its source" if source_label == target_label else f"the {source_label} source"
        )
        raise ValueError(
            f"{target_label} destination is the same file as {source_description}; "
            f"source={source_path}, destination={target_path}"
        )


def _svc():
    """Resolve facade-owned constants through services.dataset_export_service at call time.

    Tests patch DATASET_EXPORT_RESPONSE_ITEM_LIMIT (and may patch the sibling
    limits) on the facade module object; a ``from`` import here would freeze
    independent bindings those patches silently miss. The lazy import avoids
    a facade<->submodule load cycle.
    """
    import services.dataset_export_service as dataset_export_service

    return dataset_export_service


def export_dataset(
    request: DatasetExportRequest,
    *,
    progress_callback: Optional[ExportProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pending_package_run_id: Optional[str] = None,
) -> DatasetExportResponse:
    return _export_dataset(
        request,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        pending_package_run_id=pending_package_run_id,
        completion_gate=None,
    )


def export_dataset_job(
    request: DatasetExportRequest,
    *,
    progress_callback: ExportProgressCallback,
    cancel_event: threading.Event,
    pending_package_run_id: str,
    completion_gate: Callable[[], bool],
) -> DatasetExportResponse:
    return _export_dataset(
        request,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        pending_package_run_id=pending_package_run_id,
        completion_gate=completion_gate,
    )


def _export_dataset(
    request: DatasetExportRequest,
    *,
    progress_callback: Optional[ExportProgressCallback],
    cancel_event: Optional[threading.Event],
    pending_package_run_id: Optional[str],
    completion_gate: Optional[Callable[[], bool]],
) -> DatasetExportResponse:
    """Run a full dataset export. Atomic-per-row: a per-image failure
    leaves earlier rows intact and adds an error entry for the failed
    one.

    This is intentionally streaming: scan-token folder exports, explicit path
    exports, and DB-backed image exports are consumed in chunks. The backend no
    longer builds a 100k-1M ``image_records`` list or a full rename plan before
    the first file is written.
    """
    resolved_annotations = resolve_annotation_selections(request)
    validate_annotation_selection_coverage(request, resolved_annotations)
    output_mode = _output_mode(request)
    output_path = _validate_export_request(request)
    output_mode = _output_mode(request)
    requested_total = _requested_item_count(request)
    if requested_total <= 0:
        raise HTTPException(status_code=400, detail="Dataset export has no images after exclusions.")
    if progress_callback:
        progress_callback({
            "step": "loading",
            "current": 0,
            "total": requested_total,
            "message": f"Preparing {requested_total} dataset items...",
            "output_folder": str(output_path or ""),
            "output_mode": output_mode,
        })

    if progress_callback:
        progress_callback({
            "step": "exporting",
            "current": 0,
            "total": requested_total,
            "message": "Exporting dataset...",
            "output_folder": str(output_path or ""),
            "output_mode": output_mode,
        })

    # ---- Pre-build common state for caption rendering ----
    blacklist_set = {str(t).strip().lower() for t in request.blacklist if str(t).strip()}

    image_overrides_int, image_overrides_path = _split_image_overrides(request)
    image_types_int, image_types_path = _split_keyed_str_map(getattr(request, "image_types", None))
    nl_overrides_int, nl_overrides_path = _split_keyed_str_map(getattr(request, "image_nl_overrides", None))
    caption_extension = _dataset_sidecar_extension(request.content_mode)
    mask_export_mode = _mask_export_mode(request)
    subject_crop_enabled = request.subject_crop.enabled
    bucket_resize_enabled = request.bucket_resize.enabled
    watermark_removal_enabled = request.watermark_removal.enabled
    watermark_removal_config = WatermarkRemovalConfig(
        enabled=watermark_removal_enabled,
        method=request.watermark_removal.method,
        radius=request.watermark_removal.radius,
        padding_percent=request.watermark_removal.padding_percent,
        regions=tuple(
            WatermarkRegion(
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
            )
            for region in request.watermark_removal.regions
        ),
    )
    pixel_transform_enabled = (
        subject_crop_enabled or bucket_resize_enabled or watermark_removal_enabled
    )
    package_build: Optional[DatasetPackageBuild] = None
    package_integrity_failed = False
    package_status = "not_requested"
    package_run_id: Optional[str] = None
    package_manifest_path: Optional[str] = None
    if package_requested(request):
        if output_path is None:
            raise HTTPException(
                status_code=400,
                detail="Verified trainer packages require output_mode='folder'",
            )
        try:
            if pending_package_run_id is None:
                package_build = begin_dataset_package(
                    output_path,
                    request,
                    requested_total,
                    caption_extension,
                )
            else:
                package_build = resume_pending_dataset_package(
                    output_path,
                    request,
                    requested_total,
                    caption_extension,
                    pending_package_run_id,
                )
        except (PackageLockError, PackageOwnershipError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PackageIntegrityError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        package_status = "incomplete"
        package_run_id = package_build.run_id

    try:
        _invalidate_existing_anima_config(
            output_path,
            _trainer_config_mode(request),
        )
    except Exception as exc:
        if package_build is not None:
            try:
                abort_dataset_package(
                    package_build,
                    f"Trainer config invalidation failed: {exc}",
                )
            except PackageIntegrityError as abort_exc:
                raise PackageIntegrityError(
                    f"{exc}; package_abort_cleanup_error={abort_exc}"
                ) from exc
        raise

    # ---- Execute the plan ----
    items: List[DatasetExportItemResult] = []
    error_messages: List[str] = []
    warnings: List[DatasetExportWarning] = []
    exported = 0
    skipped = 0
    error_count = 0
    masks_written = 0
    masks_missing = 0
    processed = 0
    total_expected = requested_total
    total_items = 0
    cancelled = False
    export_index = 0
    used_image_paths: set[str] = set()
    used_caption_paths: set[str] = set()
    used_mask_paths: set[str] = set()
    seen_virtual_paths: set[str] = set()

    def _append_package_record(
        *,
        index: int,
        image_id: int,
        source_path: str,
        disposition: Literal["exported", "skipped", "failed"],
        reason: Optional[str],
        image_path: Optional[Path],
        caption_path: Optional[Path],
        mask_path: Optional[Path],
        expected_caption_sha256: Optional[str],
        annotation_provenance: Optional[AnnotationProvenance],
    ) -> None:
        nonlocal package_integrity_failed, error_count
        if package_build is None or package_integrity_failed:
            return
        try:
            record = build_inventory_record(
                package_build.output_folder,
                index,
                image_id,
                source_path,
                disposition,
                reason,
                image_path,
                caption_path,
                mask_path,
                expected_caption_sha256,
                annotation_provenance,
            )
            package_build.inventory_writer.append(record)
        except PackageIntegrityError as exc:
            package_integrity_failed = True
            error_count += 1
            _add_error(str(exc))

    def _append_item(item: DatasetExportItemResult) -> None:
        nonlocal total_items
        total_items += 1
        if len(items) < _svc().DATASET_EXPORT_RESPONSE_ITEM_LIMIT:
            items.append(item)

    def _add_error(message: str) -> None:
        if len(error_messages) < 50:
            error_messages.append(message)
        elif len(error_messages) == 50:
            error_messages.append("... and more errors (showing first 50)")

    def _emit(message: str, current_item: Optional[str] = None) -> None:
        if not progress_callback:
            return
        progress_callback({
            "step": "exporting",
            "current": processed,
            "total": total_expected,
            "exported": exported,
            "skipped": skipped,
            "errors": error_count,
            "current_item": current_item,
            "recent_errors": error_messages[-_svc().DATASET_EXPORT_RECENT_ERROR_LIMIT:],
            "message": message,
            "output_folder": str(output_path or ""),
            "output_mode": output_mode,
            "items_truncated": total_items > _svc().DATASET_EXPORT_RESPONSE_ITEM_LIMIT,
        })

    def _record_error(image_id: int, src_image_path: str, message: str, current_item: Optional[str] = None) -> None:
        nonlocal error_count, processed
        error_count += 1
        processed += 1
        _add_error(message)
        _append_item(DatasetExportItemResult(
            image_id=int(image_id or 0),
            src_image_path=src_image_path or None,
            error=message,
        ))
        _append_package_record(
            index=total_items,
            image_id=image_id,
            source_path=src_image_path,
            disposition="failed",
            reason=message,
            image_path=None,
            caption_path=None,
            mask_path=None,
            expected_caption_sha256=None,
            annotation_provenance=None,
        )
        _emit(f"Failed {current_item or src_image_path or image_id} ({processed}/{total_expected})", current_item)

    def _record_skip(image_id: int, src_image_path: str, reason: str, current_item: Optional[str] = None) -> None:
        nonlocal skipped, processed
        skipped += 1
        processed += 1
        _append_item(DatasetExportItemResult(
            image_id=int(image_id or 0),
            src_image_path=src_image_path or None,
            skipped_reason=reason,
        ))
        _append_package_record(
            index=total_items,
            image_id=image_id,
            source_path=src_image_path,
            disposition="skipped",
            reason=reason,
            image_path=None,
            caption_path=None,
            mask_path=None,
            expected_caption_sha256=None,
            annotation_provenance=None,
        )
        _emit(f"Skipped {current_item or src_image_path or image_id} ({processed}/{total_expected})", current_item)

    def _path_entry_exists(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PackageIntegrityError(
                "Package mask target could not be inspected: "
                f"path={path}, error_type={type(exc).__name__}, error={exc}"
            ) from exc
        return True

    def _export_record(record: Dict[str, Any], tags: Optional[List[Any]] = None) -> bool:
        nonlocal exported, skipped, error_count, processed, export_index, cancelled
        nonlocal masks_written, masks_missing
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            return False

        export_index += 1
        image_id = int(record.get("id") or 0)
        src_image_path = str(record.get("path") or "")
        annotation = resolved_annotations.get(
            annotation_selection_key(image_id, src_image_path)
        )
        filename = os.path.basename(src_image_path) or f"image-{image_id}"
        dst_image_path: Optional[Path] = None
        dst_caption_path: Optional[Path] = None
        skip_reason: Optional[str] = None
        if output_mode == "beside_image":
            dst_caption_path, skip_reason = _plan_beside_image_sidecar(
                record,
                caption_extension=caption_extension,
                overwrite_policy=request.overwrite_policy,
                used_caption_paths=used_caption_paths,
            )
        else:
            if output_path is None:
                _record_error(image_id, src_image_path, "Output folder is required for folder export mode.", filename)
                return True
            if package_build is not None:
                dst_image_path, dst_caption_path, skip_reason = _plan_single_pair(
                    record,
                    output_folder=output_path,
                    pattern=request.naming_pattern,
                    trigger=request.trigger,
                    overwrite_policy=request.overwrite_policy,
                    caption_extension=caption_extension,
                    index=export_index,
                    used_image_paths=used_image_paths,
                    used_caption_paths=used_caption_paths,
                )
            else:
                (
                    dst_image_path,
                    dst_caption_path,
                    skip_reason,
                ) = _plan_single_training_pair(
                    record,
                    output_folder=output_path,
                    pattern=request.naming_pattern,
                    trigger=request.trigger,
                    overwrite_policy=request.overwrite_policy,
                    caption_extension=caption_extension,
                    mask_export_mode=mask_export_mode,
                    index=export_index,
                    used_image_paths=used_image_paths,
                    used_caption_paths=used_caption_paths,
                    used_mask_paths=used_mask_paths,
                )

        if dst_caption_path is None:
            _record_skip(image_id, src_image_path, skip_reason or "skipped", filename)
            return True

        if output_mode == "folder" and dst_image_path is not None:
            try:
                _validate_row_source_destinations(
                    Path(src_image_path),
                    dst_image_path,
                    None,
                    None,
                )
            except ValueError as exc:
                _record_error(image_id, src_image_path, str(exc), filename)
                return True

        transformed_image: Optional[Image.Image] = None
        transformed_mask: Optional[Image.Image] = None
        source_mask_path: Optional[Path] = None
        if pixel_transform_enabled:
            transform_label = (
                "watermark_removal"
                if watermark_removal_enabled
                else ("bucket_resize" if bucket_resize_enabled else "subject_crop")
            )
            if dst_image_path is None:
                _record_error(
                    image_id,
                    src_image_path,
                    f"{transform_label} requires a folder image destination",
                    filename,
                )
                return True
            if not src_image_path or not os.path.exists(src_image_path):
                _record_error(
                    image_id,
                    src_image_path,
                    f"{transform_label} source image is missing on disk: {src_image_path!r}",
                    filename,
                )
                return True
            try:
                mask_required = subject_crop_enabled or (
                    bucket_resize_enabled
                    and (
                        request.bucket_resize.subject_aware
                        or mask_export_mode != "none"
                    )
                )
                needs_mask = mask_required or (
                    watermark_removal_enabled and mask_export_mode != "none"
                )
                source_mask_image: Optional[Image.Image] = None
                if needs_mask:
                    from services import mask_service

                    source_mask_path = mask_service.get_mask_file(image_id)
                    if source_mask_path is None:
                        if mask_required:
                            raise BucketTransformError(
                                f"stored training mask is missing for library image {image_id}"
                            )
                    else:
                        try:
                            mask_size_bytes = source_mask_path.stat().st_size
                        except OSError as exc:
                            if mask_required:
                                raise BucketTransformError(
                                    "stored training mask cannot be inspected for "
                                    f"image {image_id}: {exc}"
                                ) from exc
                            mask_size_bytes = 0
                        if mask_size_bytes <= 0:
                            if mask_required:
                                raise BucketTransformError(
                                    f"stored training mask is empty for library image {image_id}: {source_mask_path}"
                                )
                        else:
                            try:
                                with Image.open(source_mask_path) as opened_mask:
                                    opened_mask.load()
                                    source_mask_image = opened_mask.copy()
                            except (OSError, UnidentifiedImageError, ValueError) as exc:
                                if mask_required:
                                    raise BucketTransformError(
                                        f"stored training mask is unreadable for image {image_id}: {exc}"
                                    ) from exc

                subject_settings = request.subject_crop
                if (
                    subject_crop_enabled
                    and subject_settings.background_mode == "transparent_rgba"
                    and dst_image_path.suffix.lower() not in _SOFT_ALPHA_OUTPUT_EXTENSIONS
                ):
                    suffix = dst_image_path.suffix.lower()
                    suffix_name = (
                        "JPEG" if suffix in {".jpg", ".jpeg"}
                        else suffix.upper().lstrip(".") or "unknown"
                    )
                    raise SubjectCropError(
                        "transparent_rgba cannot be exported to "
                        f"{suffix_name}; use PNG, WebP, or TIFF"
                    )
                with Image.open(src_image_path) as opened_source:
                    opened_source.load()
                    raw_source_size = opened_source.size
                    transformed_image, source_orientation = normalize_source_orientation(
                        opened_source
                    )
                if watermark_removal_enabled:
                    transformed_image = apply_watermark_removal(
                        transformed_image,
                        watermark_removal_config,
                    )
                if source_mask_image is not None:
                    if source_mask_image.size != raw_source_size:
                        raise BucketTransformError(
                            "Stored training mask size must match the source image's raw "
                            f"pixel size before EXIF orientation: mask size="
                            f"{source_mask_image.width}x{source_mask_image.height}, "
                            f"source image size={raw_source_size[0]}x{raw_source_size[1]}"
                        )
                    transformed_mask = normalize_mask_orientation(
                        source_mask_image,
                        source_orientation,
                    )

                if subject_crop_enabled:
                    if transformed_mask is None:
                        raise SubjectCropError(
                            f"subject_crop training mask is missing for image {image_id}"
                        )
                    transformed_image, transformed_mask, _subject_box = apply_subject_crop(
                        transformed_image,
                        transformed_mask,
                        alpha_threshold=subject_settings.alpha_threshold,
                        padding_percent=subject_settings.padding_percent,
                        background_mode=subject_settings.background_mode,
                        solid_color=subject_settings.solid_color,
                    )

                if bucket_resize_enabled:
                    if request.bucket_resize.subject_aware:
                        if transformed_mask is None:
                            raise BucketTransformError(
                                f"subject-aware bucket mask is missing for image {image_id}"
                            )
                        (
                            transformed_image,
                            transformed_mask,
                            _bucket_size,
                            _bucket_crop_box,
                        ) = apply_subject_aware_bucket_resize(
                            transformed_image,
                            transformed_mask,
                            alpha_threshold=request.bucket_resize.alpha_threshold,
                            trainer_resolution=request.trainer_resolution,
                        )
                    else:
                        (
                            transformed_image,
                            transformed_mask,
                            _bucket_size,
                            _bucket_crop_box,
                        ) = apply_center_bucket_resize(
                            transformed_image,
                            transformed_mask,
                            trainer_resolution=request.trainer_resolution,
                        )
            except (
                OSError,
                UnidentifiedImageError,
                BucketTransformError,
                SubjectCropError,
                WatermarkServiceError,
                ValueError,
            ) as exc:
                _record_error(
                    image_id,
                    src_image_path,
                    f"{transform_label} validation failed for image {image_id}: {exc}",
                    filename,
                )
                return True

        if (
            package_build is not None
            and mask_export_mode != "none"
            and image_id > 0
            and request.overwrite_policy in {"unique", "skip"}
        ):
            if dst_image_path is None:
                raise PackageIntegrityError(
                    "Package mask planning requires an image destination"
                )
            planned_mask_path, mask_plan_error = _plan_mask_destination(
                mask_export_mode,
                dst_image_path,
                output_path,
            )
            if mask_plan_error is not None or planned_mask_path is None:
                raise PackageIntegrityError(
                    mask_plan_error or "Package mask destination is missing"
                )
            if _path_entry_exists(planned_mask_path):
                _record_skip(
                    image_id,
                    src_image_path,
                    f"existing mask target: {planned_mask_path}",
                    filename,
                )
                return True

        # Render caption
        try:
            if annotation is not None and annotation["content"] is not None:
                caption_text = render_training_caption_content(
                    annotation["content"],
                    request.caption_transforms or {},
                    request.trigger,
                    request.common_tags,
                )
            else:
                caption_text = _render_dataset_sidecar(
                    record,
                    tags or [],
                    request,
                    blacklist_set=blacklist_set,
                    image_overrides_int=image_overrides_int,
                    image_overrides_path=image_overrides_path,
                    image_types_int=image_types_int,
                    image_types_path=image_types_path,
                    nl_overrides_int=nl_overrides_int,
                    nl_overrides_path=nl_overrides_path,
                )
        except Exception as exc:  # pragma: no cover - defensive
            msg = f"caption render failed for image {image_id}: {exc}"
            _record_error(image_id, src_image_path, msg, filename)
            return True
        expected_caption_sha256 = hashlib.sha256(
            caption_text.encode("utf-8")
        ).hexdigest()

        # Verify source exists
        if not src_image_path or not os.path.exists(src_image_path):
            msg = f"image {image_id} source missing on disk: {src_image_path!r}"
            _record_error(image_id, src_image_path, msg, filename)
            return True

        atomic_row_written = False
        exported_mask_path: Optional[Path] = None
        if pixel_transform_enabled:
            if dst_image_path is None:
                _record_error(
                    image_id,
                    src_image_path,
                    "Dataset pixel transform did not produce an image destination.",
                    filename,
                )
                return True
            transformed_mask_target: Optional[Path] = None
            if mask_export_mode != "none":
                if transformed_mask is not None:
                    transformed_mask_target, mask_plan_error = _plan_mask_destination(
                        mask_export_mode,
                        dst_image_path,
                        output_path,
                    )
                    if mask_plan_error is not None or transformed_mask_target is None:
                        _record_error(
                            image_id,
                            src_image_path,
                            mask_plan_error or "Mask destination is missing",
                            filename,
                        )
                        return True
            try:
                if transformed_image is None:
                    raise BucketTransformError(
                        f"dataset image transform is missing for image {image_id}"
                    )
                _validate_row_source_destinations(
                    Path(src_image_path),
                    dst_image_path,
                    source_mask_path,
                    transformed_mask_target,
                )
                row_warnings = _write_transformed_row_atomic(
                    transformed_image,
                    dst_image_path,
                    caption_text,
                    dst_caption_path,
                    transformed_mask if transformed_mask_target is not None else None,
                    transformed_mask_target,
                )
                warnings.extend(row_warnings)
            except (OSError, ValueError, BucketTransformError) as exc:
                _record_error(
                    image_id,
                    src_image_path,
                    f"failed to write transformed row for image {image_id}: {exc}",
                    filename,
                )
                return True
            atomic_row_written = True
            exported_mask_path = transformed_mask_target
            if exported_mask_path is not None:
                masks_written += 1
            elif mask_export_mode != "none":
                masks_missing += 1

        if (
            not pixel_transform_enabled
            and output_mode == "folder"
            and package_build is None
            and request.image_op == "copy"
            and mask_export_mode != "none"
        ):
            if dst_image_path is None:
                _record_error(
                    image_id,
                    src_image_path,
                    "Atomic mask export did not produce an image destination.",
                    filename,
                )
                return True
            from services import mask_service

            source_mask = mask_service.get_mask_file(image_id) if image_id > 0 else None
            atomic_mask_target: Optional[Path] = None
            if source_mask is not None:
                atomic_mask_target, mask_plan_error = _plan_mask_destination(
                    mask_export_mode,
                    dst_image_path,
                    output_path,
                )
                if mask_plan_error is not None or atomic_mask_target is None:
                    _record_error(
                        image_id,
                        src_image_path,
                        mask_plan_error or "Mask destination is missing",
                        filename,
                    )
                    return True
            try:
                _validate_row_source_destinations(
                    Path(src_image_path),
                    dst_image_path,
                    source_mask,
                    atomic_mask_target,
                )
                row_warnings = _write_copied_row_atomic(
                    Path(src_image_path),
                    dst_image_path,
                    caption_text,
                    dst_caption_path,
                    source_mask,
                    atomic_mask_target,
                )
                warnings.extend(row_warnings)
            except (OSError, ValueError) as exc:
                _record_error(
                    image_id,
                    src_image_path,
                    f"failed to write atomic mask row for image {image_id}: {exc}",
                    filename,
                )
                return True
            atomic_row_written = True
            exported_mask_path = atomic_mask_target
            if exported_mask_path is None:
                masks_missing += 1
            else:
                masks_written += 1

        # Copy / move the image in folder mode only. Beside-image mode is a
        # pure sidecar write and must not duplicate or relocate source images.
        if output_mode == "folder" and not atomic_row_written:
            folder_image_path = dst_image_path
            if folder_image_path is None:
                _record_error(
                    image_id,
                    src_image_path,
                    "Folder export planning did not produce an image destination.",
                    filename,
                )
                return True
            try:
                os.makedirs(folder_image_path.parent, exist_ok=True)
                if request.image_op == "copy":
                    if package_build is not None:
                        copy_package_file_atomic(
                            Path(src_image_path),
                            folder_image_path,
                            package_build.output_folder,
                        )
                    else:
                        # copy2 preserves mtime so trainers and downstream tools
                        # see the original recency.
                        shutil.copy2(src_image_path, str(folder_image_path))
                else:  # move
                    # Move the file first, then reconcile the indexed DB
                    # row. Previously the DB update was wrapped in a bare
                    # ``except Exception: pass`` which silently desynced
                    # the gallery from disk if SQLite failed after the
                    # file move. We now roll the file back to its source
                    # path on DB failure and surface the error, so the
                    # library never points at a non-existent path.
                    shutil.move(src_image_path, str(folder_image_path))
                    if image_id:
                        move_error = _reconcile_moved_image_path(
                            image_id,
                            src_image_path,
                            str(folder_image_path),
                        )
                        if move_error:
                            # Best-effort rollback so the on-disk state
                            # matches the DB row we just failed to update.
                            try:
                                shutil.move(str(folder_image_path), src_image_path)
                            except OSError:
                                # If rollback fails we must still report
                                # the desync rather than hide it.
                                pass
                            msg = (
                                f"moved {filename} but failed to update library path: "
                                f"{move_error}. File restored to original location."
                            )
                            _record_error(image_id, src_image_path, msg, filename)
                            return True
            except Exception as exc:
                msg = f"failed to {request.image_op} image {image_id}: {exc}"
                _record_error(image_id, src_image_path, msg, filename)
                return True

        # Write caption sidecar.
        #
        # Per-row atomicity (v3.4.5): write to a sibling temp file first,
        # then atomically rename into place. A crash mid-write now leaves
        # either the old caption (if any) or no caption — never a
        # half-written file the trainer might pick up. The temp file uses
        # a ``.tmp`` suffix on the SAME directory so the rename is atomic
        # on the same filesystem (POSIX rename + Windows MoveFileEx are
        # both atomic for same-volume renames).
        if not atomic_row_written:
            tmp_caption_path: Optional[Path] = None
            try:
                if package_build is not None:
                    write_package_text_atomic(
                        dst_caption_path,
                        caption_text,
                        package_build.output_folder,
                    )
                else:
                    os.makedirs(dst_caption_path.parent, exist_ok=True)
                    tmp_caption_path = dst_caption_path.with_suffix(
                        dst_caption_path.suffix + ".tmp"
                    )
                    with open(
                        tmp_caption_path,
                        "w",
                        encoding="utf-8",
                        newline="\n",
                    ) as handle:
                        handle.write(caption_text)
                        handle.flush()
                        try:
                            os.fsync(handle.fileno())
                        except OSError:
                            pass
                    os.replace(str(tmp_caption_path), str(dst_caption_path))
            except Exception as exc:
                msg = f"failed to write caption for image {image_id}: {exc}"
                error_count += 1
                processed += 1
                _add_error(msg)
                _append_item(DatasetExportItemResult(
                    image_id=image_id,
                    src_image_path=src_image_path,
                    dst_image_path=(
                        str(dst_image_path) if dst_image_path is not None else None
                    ),
                    error=msg,
                ))
                _emit(
                    f"Failed to write caption for {filename} "
                    f"({processed}/{total_expected})",
                    filename,
                )
                try:
                    if tmp_caption_path is not None and os.path.exists(
                        str(tmp_caption_path)
                    ):
                        os.unlink(str(tmp_caption_path))
                except OSError:
                    pass
                return True

        # Stored masks are keyed by library image id, so path sources cannot
        # satisfy any requested mask mode and must be counted as missing.
        if mask_export_mode != "none" and not atomic_row_written:
            if image_id <= 0:
                masks_missing += 1
            else:
                nonlocal_dst = dst_image_path if dst_image_path is not None else Path(src_image_path)
                exported_mask_path, mask_error = _write_mask_sidecar(
                    image_id,
                    mask_export_mode,
                    exported_image_path=nonlocal_dst,
                    source_image_path=Path(src_image_path),
                    output_folder=output_path,
                )
                if mask_error is None:
                    masks_written += 1
                elif mask_error == "missing":
                    masks_missing += 1
                else:
                    masks_missing += 1
                    _record_error(image_id, src_image_path, mask_error, filename)
                    return True

        exported += 1
        processed += 1
        _append_item(DatasetExportItemResult(
            image_id=image_id,
            src_image_path=src_image_path,
            dst_image_path=str(dst_image_path) if dst_image_path is not None else None,
            dst_caption_path=str(dst_caption_path),
        ))
        _append_package_record(
            index=total_items,
            image_id=image_id,
            source_path=src_image_path,
            disposition="exported",
            reason=None,
            image_path=dst_image_path,
            caption_path=dst_caption_path,
            mask_path=exported_mask_path,
            expected_caption_sha256=expected_caption_sha256,
            annotation_provenance=(
                annotation["provenance"] if annotation is not None else None
            ),
        )
        _emit(f"Exported {filename} ({processed}/{total_expected})", filename)
        return True

    def _write_mask_sidecar(
        image_id: int,
        mode: str,
        *,
        exported_image_path: Path,
        source_image_path: Path,
        output_folder: Optional[Path],
    ) -> tuple[Optional[Path], Optional[str]]:
        """Copy the stored mask next to the exported pair. Returns None on
        success, "missing" when no mask is stored, or an error string."""
        from services import mask_service

        source_mask = mask_service.get_mask_file(image_id)
        if source_mask is None:
            return None, "missing"
        target, target_error = _plan_mask_destination(
            mode,
            exported_image_path,
            output_folder,
        )
        if target_error is not None or target is None:
            return None, target_error or "Mask destination is missing"
        try:
            _validate_row_source_destinations(
                source_image_path,
                exported_image_path,
                Path(source_mask),
                target,
            )
            os.makedirs(target.parent, exist_ok=True)
            if package_build is not None:
                copy_package_file_atomic(
                    Path(source_mask),
                    target,
                    package_build.output_folder,
                )
            else:
                shutil.copy2(str(source_mask), str(target))
            return target, None
        except Exception as exc:  # noqa: BLE001
            return None, f"failed to write mask for image {image_id}: {exc}"

    def _process_path_source(raw_path: Any) -> bool:
        nonlocal cancelled
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            return False
        normalized_path = _resolve_dataset_image_path(raw_path)
        display_path = str(raw_path or "")
        if not normalized_path:
            _record_error(0, display_path, f"path not a readable image: {display_path}", os.path.basename(display_path))
            return True
        if normalized_path in seen_virtual_paths:
            _record_skip(0, normalized_path, "duplicate", os.path.basename(normalized_path))
            return True
        seen_virtual_paths.add(normalized_path)
        record = virtual_image_record_for_path(normalized_path, read_dimensions=False)
        return _export_record(record, [])

    try:
        _emit(f"Exporting 0/{total_expected} images...")

        # ---- DB-source records in bounded chunks ----
        for image_id_chunk in _iter_chunks(_iter_unique_image_ids(request.image_ids or []), _svc().DATASET_EXPORT_DB_CHUNK_SIZE):
            if cancelled:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            ids = [int(image_id) for image_id in image_id_chunk]
            images_map = db.get_images_by_ids(ids) if ids else {}
            tags_map = db.get_image_tags_map(ids) if ids else {}
            for image_id in ids:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                record = images_map.get(image_id)
                if not record:
                    _record_error(image_id, "", f"image {image_id} not found in library", f"id-{image_id}")
                    continue
                if not _export_record(dict(record), tags_map.get(image_id, []) or []):
                    break

        # ---- Explicit path-source records ----
        if not cancelled:
            for raw_path in request.image_paths or []:
                if not _process_path_source(raw_path):
                    break

        # ---- Token-backed folder manifest records ----
        if not cancelled:
            for raw_path in _iter_requested_scan_paths(request):
                if not _process_path_source(raw_path):
                    break

        trainer_config_path = None
        trainer_config_mode = _trainer_config_mode(request)
        trainer_config_selected = (
            trainer_config_mode in {"kohya_toml", "anima_lora_toml"}
            and output_mode == "folder"
            and output_path is not None
            and not cancelled
        )
        trainer_name = "Kohya" if trainer_config_mode == "kohya_toml" else "Anima"
        if trainer_config_selected and error_count > 0:
            _add_error(
                f"{trainer_name} dataset config withheld because the export has errors; "
                "fix the reported items and run the export again."
            )
        elif trainer_config_selected and exported > 0:
            if output_path is None:
                raise RuntimeError(
                    "Trainer config output path is missing for a selected folder export."
                )
            try:
                if trainer_config_mode == "kohya_toml":
                    trainer_config_path = _write_kohya_dataset_config(
                        output_path,
                        request,
                        masks_written=masks_written,
                        masks_missing=masks_missing,
                    )
                else:
                    trainer_config_path = _write_anima_dataset_config(
                        output_path,
                        request,
                        masks_written=masks_written,
                        masks_missing=masks_missing,
                    )
            except (KohyaTrainerContractError, AnimaTrainerContractError) as exc:
                error_count += 1
                _add_error(str(exc))

        if cancel_event is not None and cancel_event.is_set():
            cancelled = True

        if cancelled:
            status = "cancelled"
            _emit(f"Cancelled at {processed}/{total_expected}. Exported {exported} images.")
        elif error_count == 0:
            status = "ok"
        elif exported == 0:
            status = "failed"
        else:
            status = "partial"
    except Exception as exc:
        if package_build is not None:
            try:
                abort_dataset_package(
                    package_build,
                    f"Package export execution failed: {exc}",
                )
            except PackageIntegrityError as abort_exc:
                raise PackageIntegrityError(
                    f"{exc}; package_abort_cleanup_error={abort_exc}"
                ) from exc
        raise

    items_truncated = total_items > len(items)

    if package_build is not None:
        if (
            not cancelled
            and completion_gate is not None
            and not completion_gate()
        ):
            cancelled = True
            status = "cancelled"
        try:
            package_status, package_manifest_path = finalize_dataset_package(
                package_build,
                requested_total,
                processed,
                exported,
                skipped,
                error_count,
                masks_written,
                masks_missing,
                trainer_config_path,
                cancelled,
                tuple(error_messages),
            )
        except PackageIntegrityError as exc:
            package_integrity_failed = True
            error_count += 1
            _add_error(str(exc))
            package_status = "incomplete"
            package_manifest_path = None
        if package_status == "incomplete" and not cancelled:
            status = "failed" if exported == 0 else "partial"

    # Best-effort: drop an ``export_manifest.json`` describing this run into
    # the output folder. Only ``folder`` mode has a single destination folder;
    # ``beside_image`` writes sidecars next to each source image (output_path
    # is None), so there is no one place a run-level manifest belongs.
    if output_mode == "folder" and output_path is not None and package_build is None:
        manifest = _build_export_manifest(
            request,
            status=status,
            output_folder=output_path,
            output_mode=output_mode,
            caption_extension=caption_extension,
            exported=exported,
            skipped=skipped,
            error_count=error_count,
            total_items=total_items,
            items=items,
            items_truncated=items_truncated,
            generated_at=time.time(),
        )
        _write_export_manifest(output_path, manifest)

    return DatasetExportResponse(
        trainer_config_path=trainer_config_path,
        masks_written=masks_written,
        masks_missing=masks_missing,
        status=status,
        exported=exported,
        skipped=skipped,
        error_count=error_count,
        output_folder=str(output_path or ""),
        output_mode=output_mode,
        items=items,
        total_items=total_items,
        items_truncated=items_truncated,
        error_messages=error_messages,
        warnings=warnings,
        package_status=package_status,
        package_run_id=package_run_id,
        package_manifest_path=package_manifest_path,
    )


def preview_dataset_export(request: DatasetExportPreviewRequest) -> Dict[str, Any]:
    """Render a bounded Dataset Maker export preview without writing files."""
    resolved_annotations = resolve_annotation_selections(request)
    validate_annotation_selection_coverage(request, resolved_annotations)
    output_mode = _output_mode(request)
    if output_mode not in VALID_OUTPUT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid output_mode: {output_mode!r}")
    if request.overwrite_policy not in VALID_OVERWRITE_POLICIES:
        raise HTTPException(status_code=400, detail=f"Invalid overwrite_policy: {request.overwrite_policy!r}")
    content_mode = str(request.content_mode or "template").strip().lower()
    if content_mode not in VALID_CONTENT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid content_mode: {request.content_mode!r}")
    if not request.image_ids and not request.image_paths and not request.dataset_scan_tokens:
        return {
            "total": 0,
            "returned": 0,
            "items_truncated": False,
            "content_mode": content_mode,
            "output_mode": output_mode,
            "sidecar_extension": _dataset_sidecar_extension(content_mode),
            "items": [],
        }

    total = _requested_item_count(request)  # type: ignore[arg-type]
    try:
        output_path = Path(normalize_user_path(request.output_folder)).resolve() if request.output_folder else Path("__dataset_preview__").resolve()
    except (OSError, ValueError):
        output_path = Path("__dataset_preview__").resolve()

    blacklist_set = {str(t).strip().lower() for t in request.blacklist if str(t).strip()}
    image_overrides_int, image_overrides_path = _split_image_overrides(request)
    image_types_int, image_types_path = _split_keyed_str_map(getattr(request, "image_types", None))
    nl_overrides_int, nl_overrides_path = _split_keyed_str_map(getattr(request, "image_nl_overrides", None))
    caption_extension = _dataset_sidecar_extension(content_mode)
    limit = max(1, min(int(request.limit or 72), 500))
    preview_target_model = project_target_model(request)
    used_image_paths: set[str] = set()
    used_caption_paths: set[str] = set()
    used_mask_paths: set[str] = set()
    seen_virtual_paths: set[str] = set()
    items: List[Dict[str, Any]] = []
    export_index = 0

    def _thumbnail_url(record: Dict[str, Any]) -> str:
        image_id = int(record.get("id") or 0)
        if image_id > 0:
            return f"/api/image-thumbnail/{image_id}?size=256"
        path = str(record.get("path") or "")
        if not path:
            return ""
        from urllib.parse import quote

        return f"/api/dataset/local-thumbnail?path={quote(path, safe='')}&size=256"

    def _append_preview(record: Dict[str, Any], tags: Optional[List[Any]] = None, *, error: str = "") -> bool:
        nonlocal export_index
        export_index += 1
        if len(items) >= limit:
            return False

        image_id = int(record.get("id") or 0)
        src_image_path = str(record.get("path") or "")
        annotation = resolved_annotations.get(
            annotation_selection_key(image_id, src_image_path)
        )
        if output_mode == "beside_image":
            dst_image_path = None
            dst_caption_path, skip_reason = _plan_beside_image_sidecar(
                record,
                caption_extension=caption_extension,
                overwrite_policy=request.overwrite_policy,
                used_caption_paths=used_caption_paths,
            )
        else:
            dst_image_path, dst_caption_path, skip_reason = _plan_single_training_pair(
                record,
                output_folder=output_path,
                pattern=request.naming_pattern,
                trigger=request.trigger,
                overwrite_policy=request.overwrite_policy,
                caption_extension=caption_extension,
                mask_export_mode=_mask_export_mode(request),
                index=export_index,
                used_image_paths=used_image_paths,
                used_caption_paths=used_caption_paths,
                used_mask_paths=used_mask_paths,
            )
        rendered = ""
        render_error = error
        compose_advisories: List[Any] = []
        if not render_error:
            try:
                if annotation is not None and annotation["content"] is not None:
                    content = annotation["content"]
                    rendered = render_training_caption_content(
                        content,
                        request.caption_transforms or {},
                        request.trigger,
                        request.common_tags,
                    )
                    compose_advisory = nl_compose_advisory(
                        content.caption_type,
                        caption_format_for_storage(content.nl_caption),
                    )
                    if compose_advisory is not None:
                        compose_advisories.append(compose_advisory)
                else:
                    rendered = _render_dataset_sidecar(
                        record,
                        tags or [],
                        request,
                        blacklist_set=blacklist_set,
                        image_overrides_int=image_overrides_int,
                        image_overrides_path=image_overrides_path,
                        image_types_int=image_types_int,
                        image_types_path=image_types_path,
                        nl_overrides_int=nl_overrides_int,
                        nl_overrides_path=nl_overrides_path,
                        advisories=compose_advisories,
                    )
            except Exception as exc:  # pragma: no cover - defensive preview fallback
                render_error = str(exc)

        items.append({
            "index": export_index,
            "image_id": image_id,
            "abs_path": src_image_path,
            "filename": record.get("filename") or os.path.basename(src_image_path) or f"image-{image_id}",
            "thumbnail_url": _thumbnail_url(record),
            "output_image_name": dst_image_path.name if dst_image_path is not None else "",
            "output_caption_name": dst_caption_path.name if dst_caption_path is not None else "",
            "output_image_path": str(dst_image_path) if dst_image_path is not None and request.output_folder else "",
            "output_caption_path": str(dst_caption_path) if dst_caption_path is not None and (request.output_folder or output_mode == "beside_image") else "",
            "caption": rendered,
            # Booru tags (rendered template) live in ``caption``; surface the
            # natural-language sentence separately so the editor's NL box can
            # show / edit it independently of the booru-tags box (point 2/3).
            "ai_caption": str(record.get("ai_caption") or ""),
            "nl_caption": str(record.get("nl_caption") or ""),
            # What format the caption above is in, and anything about it that
            # needs the user's attention. Advisory only: ``caption`` is the exact
            # text export will write either way, and an empty list is the normal
            # case. The preview is the WYSIWYG surface, so this is where "these
            # captions are tag lists, not prose" has to be readable.
            "caption_format": caption_format_for_storage(rendered),
            "caption_advisories": [
                {
                    "code": advisory.code,
                    "caption_format": advisory.caption_format,
                    "expected_dialect": advisory.expected_dialect,
                    "target_model": advisory.target_model,
                    "convert": advisory.convert,
                    "message": advisory.message,
                    "action": advisory.action,
                }
                for advisory in caption_dialect_advisories(
                    rendered,
                    preview_target_model,
                    compose_advisories,
                )
            ],
            "skipped_reason": skip_reason,
            "error": render_error or None,
        })
        return len(items) < limit

    def _preview_path_source(raw_path: Any) -> bool:
        normalized_path = _resolve_dataset_image_path(raw_path)
        display_path = str(raw_path or "")
        if not normalized_path:
            record = {
                "id": 0,
                "path": display_path,
                "filename": os.path.basename(display_path) or "unreadable",
                "generator": "",
            }
            return _append_preview(record, [], error=f"path not a readable image: {display_path}")
        if normalized_path in seen_virtual_paths:
            record = virtual_image_record_for_path(normalized_path, read_dimensions=False)
            return _append_preview(record, [], error="duplicate path in dataset")
        seen_virtual_paths.add(normalized_path)
        return _append_preview(virtual_image_record_for_path(normalized_path, read_dimensions=False), [])

    for image_id_chunk in _iter_chunks(_iter_unique_image_ids(request.image_ids or []), _svc().DATASET_EXPORT_DB_CHUNK_SIZE):
        if len(items) >= limit:
            break
        ids = [int(image_id) for image_id in image_id_chunk]
        images_map = db.get_images_by_ids(ids) if ids else {}
        tags_map = db.get_image_tags_map(ids) if ids else {}
        for image_id in ids:
            if len(items) >= limit:
                break
            record = images_map.get(image_id)
            if not record:
                missing = {
                    "id": image_id,
                    "path": "",
                    "filename": f"image_{image_id}",
                    "generator": "",
                }
                _append_preview(missing, [], error=f"image {image_id} not found in library")
                continue
            _append_preview(dict(record), tags_map.get(image_id, []) or [])

    if len(items) < limit:
        for raw_path in request.image_paths or []:
            if not _preview_path_source(raw_path):
                break

    if len(items) < limit:
        for raw_path in _iter_requested_scan_paths(request):  # type: ignore[arg-type]
            if not _preview_path_source(raw_path):
                break

    return {
        "total": total,
        "returned": len(items),
        "items_truncated": total > len(items),
        "content_mode": content_mode,
        "output_mode": output_mode,
        "sidecar_extension": caption_extension,
        "items": items,
    }
