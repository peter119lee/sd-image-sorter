"""Read-only exact-output readiness planning for Dataset Maker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple

import database as db
from PIL import Image, UnidentifiedImageError

from caption_format import caption_format_for_storage
from services.dataset_bucket_service import (
    BucketTransformError,
    plan_center_bucket_resize,
    plan_subject_aware_bucket_resize,
)
from services.dataset_crop_service import SubjectCropError, compute_subject_crop_box
from services.dataset_image_geometry import (
    normalize_mask_orientation,
    normalized_exif_size,
    read_exif_orientation,
)
from services.dataset_export._constants import DATASET_EXPORT_DB_CHUNK_SIZE
from services.dataset_export.artifacts import (
    _mask_export_mode,
    _trainer_config_mode,
    _validate_export_request_read_only,
)
from services.dataset_export.captions import (
    _build_dataset_template_options,
    _render_dataset_sidecar,
    _split_image_overrides,
    _split_keyed_str_map,
    caption_dialect_advisories,
    project_target_model,
    render_training_caption_content,
)
from services.dataset_export.annotations import (
    ResolvedAnnotationSelection,
    resolve_annotation_selections,
)
from services.dataset_export.models import (
    DatasetReadinessIssue,
    DatasetReadinessIssueEvidence,
    DatasetReadinessPair,
    DatasetReadinessReport,
    DatasetReadinessRequest,
    DatasetReadinessSummary,
)
from services.caption_dialect import CaptionDialectAdvisory, nl_compose_advisory
from services.export_validation import ExportValidator
from services.dataset_export.planning import (
    _dataset_sidecar_extension,
    _iter_chunks,
    _iter_requested_scan_paths,
    _iter_unique_image_ids,
    _output_mode,
    _paths_share_file_identity,
    _plan_mask_destination,
    _plan_beside_image_sidecar,
    _plan_single_training_pair,
    _requested_item_count,
    _resolve_dataset_image_path,
)
from services.dataset_session_service import virtual_image_record_for_path
from utils.path_validation import normalize_user_path


DATASET_READINESS_RULE_VERSION = "dataset-readiness-v1"
DATASET_READINESS_ISSUE_LIMIT = 100
DATASET_READINESS_PAIR_SAMPLE_LIMIT = 20

ReadinessProgressCallback = Callable[[int, int, str], None]
CancellationRequested = Callable[[], bool]
ImageRecord = Dict[str, object]
TagRows = List[object]
SourceIdentity = Tuple[str, int, int, str, int, int]

_CAPTION_RECORD_FIELDS = (
    "id",
    "path",
    "filename",
    "generator",
    "prompt",
    "negative_prompt",
    "ai_caption",
    "nl_caption",
    "rating",
    "checkpoint",
    "metadata",
    "metadata_json",
    "loras",
    "model_hash",
    "width",
    "height",
    "aesthetic_score",
)
_CAPTION_TAG_FIELDS = ("tag", "confidence", "category", "source")


class DatasetReadinessCancelledError(RuntimeError):
    """Raised when a readiness caller requests cooperative cancellation."""


def _stable_json(value: object) -> bytes:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _make_issue(
    *,
    severity: Literal["blocker", "warning"],
    code: str,
    message: str,
    image_id: Optional[int],
    source_path: Optional[str],
    destination: Optional[str],
    observed: str,
    expected: str,
    action: str,
) -> DatasetReadinessIssue:
    identity = hashlib.sha256(_stable_json({
        "rule_version": DATASET_READINESS_RULE_VERSION,
        "code": code,
        "image_id": image_id,
        "source_path": source_path,
        "destination": destination,
        "observed": observed,
        "expected": expected,
    })).hexdigest()[:24]
    return DatasetReadinessIssue(
        severity=severity,
        code=code,
        message=message,
        issue_id=identity,
        rule_version=DATASET_READINESS_RULE_VERSION,
        evidence=DatasetReadinessIssueEvidence(
            observed=observed,
            expected=expected,
        ),
        action=action,
        destination=destination,
        image_id=image_id,
        source_path=source_path,
    )


def _normalize_fingerprint_path(raw_path: str) -> str:
    try:
        normalized = normalize_user_path(raw_path)
        return str(Path(normalized).resolve()) if normalized else ""
    except (OSError, ValueError):
        return str(raw_path).strip()


def _normalize_keyed_values(values: Dict[str, str]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for raw_key, value in values.items():
        try:
            key = str(int(raw_key))
        except (TypeError, ValueError):
            key = _normalize_fingerprint_path(str(raw_key))
        normalized[key] = str(value)
    return dict(sorted(normalized.items()))


def dataset_readiness_fingerprint(request: DatasetReadinessRequest) -> str:
    """Hash the effective request without mutating it or traversing sources."""
    payload = request.model_dump(mode="json")
    payload.pop("readiness_report_id", None)
    payload.pop("readiness_input_fingerprint", None)
    payload["image_ids"] = list(_iter_unique_image_ids(request.image_ids))
    payload["image_paths"] = [
        _normalize_fingerprint_path(path)
        for path in request.image_paths
    ]
    payload["output_folder"] = _normalize_fingerprint_path(request.output_folder)
    payload["dataset_scan_tokens"] = [
        {
            "scan_token": source.scan_token,
            "exclude_paths": sorted(source.exclude_paths),
        }
        for source in request.dataset_scan_tokens
    ]
    payload["blacklist"] = sorted({
        str(tag).strip().lower()
        for tag in request.blacklist
        if str(tag).strip()
    })
    payload["image_overrides"] = _normalize_keyed_values(request.image_overrides)
    payload["image_types"] = _normalize_keyed_values(request.image_types)
    payload["image_nl_overrides"] = _normalize_keyed_values(request.image_nl_overrides)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _inspect_source(
    raw_path: str,
    cancellation_requested: CancellationRequested,
) -> Optional[SourceIdentity]:
    resolved = _resolve_dataset_image_path(raw_path)
    if resolved is None:
        return None
    try:
        digest = hashlib.sha256()
        with Path(resolved).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if cancellation_requested():
                    raise DatasetReadinessCancelledError(
                        f"Dataset readiness cancelled while reading {resolved!r}"
                    )
                digest.update(chunk)
        with Image.open(resolved) as image:
            raw_width, raw_height = image.size
            image.verify()
        with Image.open(resolved) as image:
            orientation = read_exif_orientation(image)
        width, height = normalized_exif_size(
            (raw_width, raw_height),
            orientation,
        )
        if width <= 0 or height <= 0:
            return None
        stat = Path(resolved).stat()
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    return (
        resolved,
        int(stat.st_size),
        int(stat.st_mtime_ns),
        digest.hexdigest(),
        int(width),
        int(height),
    )


def _inspect_auxiliary_file(
    path: Path,
    cancellation_requested: CancellationRequested,
) -> Optional[Dict[str, object]]:
    resolved = path.resolve()
    try:
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if cancellation_requested():
                    raise DatasetReadinessCancelledError(
                        f"Dataset readiness cancelled while reading {resolved!r}"
                    )
                digest.update(chunk)
        stat = resolved.stat()
    except OSError:
        return None
    return {
        "path": str(resolved),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest.hexdigest(),
    }


def _dialect_advisory_issue(
    advisory: CaptionDialectAdvisory,
    *,
    affected: int,
    sample_paths: List[str],
) -> DatasetReadinessIssue:
    """One aggregated warning for a dialect problem that spans many items.

    Aggregated rather than per item on purpose: a 5,000-item project would
    otherwise fill the 100-slot issue list with identical notices and crowd out
    real blockers. The per-item flag lives on the Dataset Project response, which
    has no such limit. ``warning_count`` still reflects the aggregate, so the
    report status becomes ``warnings`` and never ``blocked``.
    """
    return _make_issue(
        severity="warning",
        code=advisory.code,
        message=f"{advisory.message} Affected items: {affected}.",
        image_id=None,
        source_path=sample_paths[0] if sample_paths else None,
        destination=None,
        observed=(
            f"{affected} caption(s) render as {advisory.caption_format}; "
            f"first: {', '.join(sample_paths)}"
            if sample_paths
            else f"{affected} caption(s) render as {advisory.caption_format}"
        ),
        expected=(
            f"captions in the {advisory.expected_dialect} dialect"
            if advisory.expected_dialect
            else "a caption source that matches the requested composition mode"
        ),
        action=advisory.action,
    )


def _readiness_status(blocker_count: int, warning_count: int) -> str:
    if blocker_count > 0:
        return "blocked"
    if warning_count > 0:
        return "warnings"
    return "ready"


def run_dataset_readiness(
    request: DatasetReadinessRequest,
    *,
    readiness_report_id: str,
    progress_callback: ReadinessProgressCallback,
    cancellation_requested: CancellationRequested,
) -> DatasetReadinessReport:
    """Traverse every requested source and plan pairs without output writes."""
    _validate_export_request_read_only(request)
    total_requested = _requested_item_count(request)
    output_mode = _output_mode(request)
    output_folder = Path(normalize_user_path(request.output_folder))
    caption_extension = _dataset_sidecar_extension(request.content_mode)
    mask_export_mode = _mask_export_mode(request)
    subject_crop_enabled = request.subject_crop.enabled
    bucket_resize_enabled = request.bucket_resize.enabled
    watermark_removal_enabled = request.watermark_removal.enabled
    pixel_transform_enabled = (
        subject_crop_enabled or bucket_resize_enabled or watermark_removal_enabled
    )
    trainer_config_mode = _trainer_config_mode(request)
    anima_mask_required = (
        trainer_config_mode == "anima_lora_toml"
        and mask_export_mode == "anima_lora"
    )
    kohya_mask_required = (
        trainer_config_mode == "kohya_toml"
        and mask_export_mode == "kohya"
    )
    blacklist_set = {
        str(tag).strip().lower()
        for tag in request.blacklist
        if str(tag).strip()
    }
    image_overrides_int, image_overrides_path = _split_image_overrides(request)
    image_types_int, image_types_path = _split_keyed_str_map(request.image_types)
    nl_overrides_int, nl_overrides_path = _split_keyed_str_map(request.image_nl_overrides)
    resolved_annotations = resolve_annotation_selections(request)
    strict_annotations = bool(request.annotation_selections)
    used_annotation_keys: set[str] = set()
    target_model = project_target_model(request)
    # code -> (advisory, affected count, up to three sample source paths)
    dialect_advisories: Dict[str, Tuple[CaptionDialectAdvisory, int, List[str]]] = {}
    issues: List[DatasetReadinessIssue] = []
    sample_pairs: List[DatasetReadinessPair] = []
    total_issues = 0
    blocker_count = 0
    warning_count = 0
    processed = 0
    trainable_pairs = 0
    export_index = 0
    used_image_paths: set[str] = set()
    used_caption_paths: set[str] = set()
    used_mask_paths: set[str] = set()
    seen_virtual_paths: set[str] = set()
    input_hasher = hashlib.sha256(
        bytes.fromhex(dataset_readiness_fingerprint(request))
    )

    def update_input(kind: str, value: object) -> None:
        input_hasher.update(_stable_json({"kind": kind, "value": value}))

    def add_issue(issue: DatasetReadinessIssue) -> None:
        nonlocal issues, total_issues, blocker_count, warning_count
        total_issues += 1
        if issue.severity == "blocker":
            blocker_count += 1
        else:
            warning_count += 1
        if len(issues) < DATASET_READINESS_ISSUE_LIMIT:
            issues = [*issues, issue]

    def note_dialect_advisory(
        advisory: Optional[CaptionDialectAdvisory],
        source_path: str,
    ) -> None:
        if advisory is None:
            return
        existing = dialect_advisories.get(advisory.code)
        if existing is None:
            dialect_advisories[advisory.code] = (advisory, 1, [source_path])
            return
        kept_advisory, count, samples = existing
        dialect_advisories[advisory.code] = (
            kept_advisory,
            count + 1,
            samples if len(samples) >= 3 else [*samples, source_path],
        )

    def emit_progress() -> None:
        progress_callback(
            processed,
            total_requested,
            f"Checked {processed} of {total_requested} dataset items",
        )

    def process_unreadable(image_id: int, raw_path: str) -> None:
        nonlocal processed
        normalized_path = _normalize_fingerprint_path(raw_path) if raw_path else ""
        update_input("unreadable", {"image_id": image_id, "path": normalized_path})
        add_issue(_make_issue(
            severity="blocker",
            code="source_unreadable",
            message=f"Source image is missing or unreadable: {raw_path!r}",
            image_id=image_id if image_id > 0 else None,
            source_path=normalized_path or None,
            destination=None,
            observed="missing, unreadable, or not a valid image",
            expected="a readable Pillow-verifiable image with positive dimensions",
            action="Restore or replace the source image, then run readiness again.",
        ))
        processed += 1
        emit_progress()

    def process_record(
        record: ImageRecord,
        tags: TagRows,
        source_identity: SourceIdentity,
    ) -> None:
        nonlocal export_index, processed, trainable_pairs, sample_pairs
        if cancellation_requested():
            raise DatasetReadinessCancelledError(
                f"Dataset readiness cancelled after {processed} of {total_requested} items"
            )

        raw_image_id = record.get("id")
        image_id = (
            int(raw_image_id)
            if isinstance(raw_image_id, (int, str))
            and not isinstance(raw_image_id, bool)
            else 0
        )
        source_path, source_size, source_mtime_ns, source_digest, width, height = (
            source_identity
        )
        annotation_key = str(image_id) if image_id > 0 else source_path
        annotation: ResolvedAnnotationSelection | None = resolved_annotations.get(
            annotation_key
        )
        normalized_record = dict(record)
        normalized_record["path"] = source_path
        normalized_record["filename"] = str(
            normalized_record.get("filename") or os.path.basename(source_path)
        )
        caption_record = {
            field: normalized_record.get(field)
            for field in _CAPTION_RECORD_FIELDS
        }
        caption_tags = [
            {
                field: tag.get(field)
                for field in _CAPTION_TAG_FIELDS
            }
            if isinstance(tag, dict)
            else str(tag)
            for tag in tags
        ]
        update_input("source", {
            "path": source_path,
            "size": source_size,
            "mtime_ns": source_mtime_ns,
            "sha256": source_digest,
            "width": width,
            "height": height,
            "caption_record": caption_record,
            "caption_tags": caption_tags,
        })
        export_index += 1

        output_image_path: Optional[Path]
        output_caption_path: Optional[Path]
        skip_reason: Optional[str]
        if output_mode == "beside_image":
            output_image_path = None
            output_caption_path, skip_reason = _plan_beside_image_sidecar(
                normalized_record,
                caption_extension=caption_extension,
                overwrite_policy=request.overwrite_policy,
                used_caption_paths=used_caption_paths,
            )
        else:
            output_image_path, output_caption_path, skip_reason = _plan_single_training_pair(
                normalized_record,
                output_folder=output_folder,
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
        if output_caption_path is None:
            update_input("planned_pair", {
                "image_id": image_id,
                "source_path": source_path,
                "output_image_path": (
                    str(output_image_path) if output_image_path is not None else None
                ),
                "output_caption_path": None,
                "skip_reason": skip_reason,
            })
            issue_code = (
                skip_reason
                if skip_reason in {
                    "caption_destination_collision",
                    "mask_destination_collision",
                    "unpaired_sidecar",
                }
                else "unpaired_output"
            )
            add_issue(_make_issue(
                severity="blocker",
                code=issue_code,
                message=f"No paired output can be planned for {source_path!r}: {skip_reason or 'unknown reason'}",
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=str(output_folder) if output_mode == "folder" else str(Path(source_path).parent),
                observed=skip_reason or "no caption destination",
                expected="one unique image destination and one caption with the same final stem",
                action="Change the naming or overwrite policy so every image and caption keep one shared stem.",
            ))
            processed += 1
            emit_progress()
            return

        update_input("planned_pair", {
            "image_id": image_id,
            "source_path": source_path,
            "output_image_path": (
                str(output_image_path) if output_image_path is not None else None
            ),
            "output_caption_path": str(output_caption_path),
            "skip_reason": skip_reason,
        })

        if output_mode == "folder" and output_image_path is not None:
            try:
                same_source = _paths_share_file_identity(
                    Path(source_path),
                    output_image_path,
                )
            except ValueError as exc:
                same_source = True
                alias_error = str(exc)
            else:
                alias_error = (
                    "planned image output resolves to the source file"
                    if same_source
                    else ""
                )
            if same_source:
                issue_code = (
                    "transform_source_destination_alias"
                    if pixel_transform_enabled
                    else "source_destination_alias"
                )
                add_issue(_make_issue(
                    severity="blocker",
                    code=issue_code,
                    message=(
                        f"Dataset export cannot write {output_image_path!s}: "
                        f"{alias_error}"
                    ),
                    image_id=image_id if image_id > 0 else None,
                    source_path=source_path,
                    destination=str(output_image_path),
                    observed=alias_error,
                    expected="an export destination that is not the source file or an alias",
                    action="Choose a different output folder or naming pattern, then run readiness again.",
                ))
                processed += 1
                emit_progress()
                return

        if strict_annotations and annotation is None:
            add_issue(_make_issue(
                severity="blocker",
                code="annotation_selection_missing",
                message=(
                    "Strict Dataset annotation selection is missing for "
                    f"{source_path!r}"
                ),
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=str(output_caption_path),
                observed=f"no selection for key {annotation_key!r}",
                expected="exactly one revision_ref or frozen_draft selection",
                action="Reload the Dataset Project captions and run readiness again.",
            ))
            processed += 1
            emit_progress()
            return

        if annotation is not None:
            used_annotation_keys.add(annotation_key)
            provenance = annotation["provenance"]
            update_input(
                "selected_annotation",
                {
                    "key": annotation_key,
                    **(
                        provenance
                        if provenance is not None
                        else {"kind": "dynamic_source"}
                    ),
                },
            )

        compose_advisories: List[CaptionDialectAdvisory] = []
        try:
            if annotation is not None and annotation["content"] is not None:
                content = annotation["content"]
                caption = render_training_caption_content(
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
                caption = _render_dataset_sidecar(
                    normalized_record,
                    tags,
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
        except Exception as exc:  # noqa: BLE001 - the failure becomes an actionable blocker
            add_issue(_make_issue(
                severity="blocker",
                code="caption_render_failed",
                message=f"Caption rendering failed for {source_path!r}: {type(exc).__name__}: {exc}",
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=str(output_caption_path),
                observed=f"{type(exc).__name__}: {exc}",
                expected="caption rendering completes without an exception",
                action="Correct the caption template or annotation that caused rendering to fail.",
            ))
            processed += 1
            emit_progress()
            return

        update_input("selected_caption", {
            "image_id": image_id,
            "output_caption_path": str(output_caption_path),
            "utf8_sha256": hashlib.sha256(caption.encode("utf-8")).hexdigest(),
        })

        # Dialect advisories are derived entirely from inputs already folded into
        # the fingerprint above, so they are deliberately NOT fed to
        # ``update_input``: a notice must not change the authorization identity of
        # an otherwise identical export.
        for advisory in caption_dialect_advisories(
            caption,
            target_model,
            compose_advisories,
        ):
            note_dialect_advisory(advisory, source_path)

        if not caption.strip():
            add_issue(_make_issue(
                severity="blocker",
                code="empty_caption",
                message=f"Caption renders empty for {source_path!r}",
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=str(output_caption_path),
                observed="empty caption",
                expected="a non-empty rendered caption",
                action="Add tags or a caption override, then run readiness again.",
            ))
            processed += 1
            emit_progress()
            return

        validator_options = (
            _build_dataset_template_options(request, blacklist_set)
            if str(request.content_mode).strip().lower() == "template"
            else request.template_options
        )
        validator = ExportValidator(
            content_mode=request.content_mode,
            template_options=validator_options,
        )
        validator.add(
            output_path=str(output_caption_path),
            content=caption,
            image_path=str(output_image_path or source_path),
        )
        for warning in validator.summary()["warnings"]:
            warning_code = str(warning["code"])
            add_issue(_make_issue(
                severity="warning",
                code=warning_code,
                message=str(warning["message"]),
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=str(output_caption_path),
                observed=caption[:512],
                expected=str(warning["message"]),
                action="Review and edit this caption before export.",
            ))

        bucket_mask_required = bucket_resize_enabled and (
            request.bucket_resize.subject_aware or mask_export_mode != "none"
        )
        stored_mask_required = (
            mask_export_mode != "none"
            or subject_crop_enabled
            or bucket_mask_required
        )
        stored_mask: Optional[Path] = None
        mask_identity: Optional[Dict[str, object]] = None
        mask_destination: Optional[Path] = None
        mask_plan_error: Optional[str] = None
        mask_image: Optional[Image.Image] = None
        mask_read_error: Optional[str] = None
        source_size = (width, height)
        raw_source_size = source_size
        source_orientation = 1

        if stored_mask_required:
            from services import mask_service

            try:
                with Image.open(source_path) as source_image:
                    raw_source_size = source_image.size
                    source_orientation = read_exif_orientation(source_image)
            except (OSError, UnidentifiedImageError, ValueError) as exc:
                mask_read_error = (
                    "source EXIF orientation could not be inspected for mask geometry: "
                    f"{exc}"
                )

            stored_mask = mask_service.get_mask_file(image_id) if image_id > 0 else None
            if mask_export_mode != "none":
                mask_image_path = output_image_path or Path(source_path)
                mask_destination, mask_plan_error = _plan_mask_destination(
                    mask_export_mode,
                    mask_image_path,
                    output_folder if output_mode == "folder" else None,
                )
            mask_identity = (
                _inspect_auxiliary_file(stored_mask, cancellation_requested)
                if stored_mask is not None
                else None
            )
            alias_pairs = (
                (source_path, output_image_path, "image"),
                (source_path, mask_destination, "mask"),
                (stored_mask, output_image_path, "image"),
                (stored_mask, mask_destination, "mask"),
            )
            for alias_source, alias_target, target_label in alias_pairs:
                if alias_source is None or alias_target is None:
                    continue
                try:
                    aliases_source = _paths_share_file_identity(
                        Path(alias_source),
                        Path(alias_target),
                    )
                except ValueError as exc:
                    aliases_source = True
                    alias_error = str(exc)
                else:
                    alias_error = (
                        "planned destination resolves to a source file"
                        if aliases_source
                        else ""
                    )
                if not aliases_source:
                    continue
                source_label = (
                    "stored training mask"
                    if alias_source == stored_mask
                    else "source image"
                )
                add_issue(_make_issue(
                    severity="blocker",
                    code=(
                        "mask_source_destination_alias"
                        if source_label == "stored training mask"
                        else "source_destination_alias"
                    ),
                    message=(
                        f"Dataset {target_label} destination aliases the {source_label}: "
                        f"{alias_error}"
                    ),
                    image_id=image_id if image_id > 0 else None,
                    source_path=source_path,
                    destination=str(alias_target),
                    observed=alias_error,
                    expected="a destination that is distinct from every source and stored mask",
                    action="Choose a different output folder or naming pattern, then run readiness again.",
                ))
                break
            update_input("requested_mask", {
                "image_id": image_id,
                "stored_mask": mask_identity,
                "destination": (
                    str(mask_destination) if mask_destination is not None else None
                ),
                "plan_error": mask_plan_error,
            })
            if mask_read_error is not None:
                pass
            elif stored_mask is None:
                mask_read_error = "stored training mask is missing"
            elif mask_identity is None:
                mask_read_error = "stored training mask is unreadable"
            elif int(mask_identity["size"]) <= 0:
                mask_read_error = "stored training mask is empty"
            else:
                try:
                    with Image.open(stored_mask) as opened_mask:
                        opened_mask.load()
                        mask_image = opened_mask.copy()
                    if mask_image.size != raw_source_size:
                        raise BucketTransformError(
                            f"mask size {mask_image.size!r} does not match source raw pixel "
                            f"size {raw_source_size!r} before EXIF orientation"
                        )
                    mask_image = normalize_mask_orientation(
                        mask_image,
                        source_orientation,
                    )
                except (
                    OSError,
                    UnidentifiedImageError,
                    BucketTransformError,
                    ValueError,
                ) as exc:
                    mask_read_error = str(exc)

        working_size = source_size
        working_mask = mask_image
        subject_crop_error: Optional[str] = None
        if subject_crop_enabled:
            if mask_read_error is not None:
                subject_crop_error = mask_read_error
            elif (
                request.subject_crop.background_mode == "transparent_rgba"
                and output_image_path is not None
                and output_image_path.suffix.lower()
                not in {".png", ".tif", ".tiff", ".webp"}
            ):
                subject_crop_error = (
                    "transparent_rgba requires a PNG, WebP, or TIFF output image"
                )
            elif working_mask is None:
                subject_crop_error = "stored training mask is unavailable"
            else:
                try:
                    subject_box = compute_subject_crop_box(
                        working_mask,
                        alpha_threshold=request.subject_crop.alpha_threshold,
                        padding_percent=request.subject_crop.padding_percent,
                    )
                    working_mask = working_mask.crop(subject_box)
                    working_size = working_mask.size
                except (SubjectCropError, ValueError) as exc:
                    subject_crop_error = str(exc)
            if subject_crop_error is not None:
                add_issue(_make_issue(
                    severity="blocker",
                    code="subject_crop_mask_invalid",
                    message=(
                        f"Subject crop cannot use the stored mask for {source_path!r}: "
                        f"{subject_crop_error}"
                    ),
                    image_id=image_id,
                    source_path=source_path,
                    destination=(
                        str(mask_destination) if mask_destination is not None else None
                    ),
                    observed=subject_crop_error,
                    expected=(
                        "a non-empty readable stored mask matching the source dimensions "
                        "with subject pixels above alpha_threshold"
                    ),
                    action="Repair or regenerate the stored training mask, then run readiness again.",
                ))

        if bucket_resize_enabled and subject_crop_error is None:
            bucket_error: Optional[str] = None
            try:
                if request.bucket_resize.subject_aware:
                    if mask_read_error is not None:
                        raise BucketTransformError(mask_read_error)
                    if working_mask is None:
                        raise BucketTransformError(
                            "stored training mask is unavailable"
                        )
                    bucket_size, bucket_crop_box = plan_subject_aware_bucket_resize(
                        working_size,
                        working_mask,
                        alpha_threshold=request.bucket_resize.alpha_threshold,
                        trainer_resolution=request.trainer_resolution,
                    )
                else:
                    if mask_export_mode != "none" and mask_read_error is not None:
                        raise BucketTransformError(mask_read_error)
                    bucket_size, bucket_crop_box = plan_center_bucket_resize(
                        working_size,
                        trainer_resolution=request.trainer_resolution,
                    )
                update_input("bucket_resize", {
                    "image_id": image_id,
                    "source_size": working_size,
                    "bucket_size": bucket_size,
                    "crop_box": bucket_crop_box,
                    "subject_aware": request.bucket_resize.subject_aware,
                    "alpha_threshold": request.bucket_resize.alpha_threshold,
                })
            except (BucketTransformError, ValueError) as exc:
                bucket_error = str(exc)
            if bucket_error is not None:
                add_issue(_make_issue(
                    severity="blocker",
                    code="bucket_resize_mask_invalid",
                    message=(
                        f"Bucket preprocessing cannot plan {source_path!r}: "
                        f"{bucket_error}"
                    ),
                    image_id=image_id,
                    source_path=source_path,
                    destination=(
                        str(output_image_path) if output_image_path is not None else None
                    ),
                    observed=bucket_error,
                    expected=(
                        "a legal SDXL bucket and, when required, a readable mask whose subject "
                        "fits without clipping"
                    ),
                    action="Repair the mask or disable subject-aware bucket preprocessing, then run readiness again.",
                ))

        if (anima_mask_required or kohya_mask_required) and mask_identity is None:
            issue_prefix = "anima" if anima_mask_required else "kohya"
            trainer_name = "Anima" if anima_mask_required else "Kohya"
            mask_name = "loss" if anima_mask_required else "conditioning"
            missing_code = (
                f"{issue_prefix}_mask_missing"
                if stored_mask is None
                else f"{issue_prefix}_mask_unreadable"
            )
            add_issue(_make_issue(
                severity="blocker",
                code=missing_code,
                message=(
                    f"{trainer_name} {mask_name} mask is missing or "
                    f"unreadable for {source_path!r}"
                ),
                image_id=image_id if image_id > 0 else None,
                source_path=source_path,
                destination=(
                    str(mask_destination) if mask_destination is not None else None
                ),
                observed="no readable stored mask is available for this image",
                expected=f"one stored {mask_name} mask for every exported image",
                action="Create or import the mask, then run readiness again.",
            ))

        trainable_pairs += 1
        if len(sample_pairs) < DATASET_READINESS_PAIR_SAMPLE_LIMIT:
            sample_pairs = [*sample_pairs, DatasetReadinessPair(
                image_id=image_id,
                source_path=source_path,
                output_image_path=(
                    str(output_image_path)
                    if output_image_path is not None
                    else None
                ),
                output_caption_path=str(output_caption_path),
            )]
        processed += 1
        emit_progress()

    progress_callback(0, total_requested, f"Checking {total_requested} dataset items")
    for image_id_chunk in _iter_chunks(
        _iter_unique_image_ids(request.image_ids),
        DATASET_EXPORT_DB_CHUNK_SIZE,
    ):
        if cancellation_requested():
            raise DatasetReadinessCancelledError(
                f"Dataset readiness cancelled after {processed} of {total_requested} items"
            )
        ids = [int(image_id) for image_id in image_id_chunk]
        images_map = db.get_images_by_ids(ids) if ids else {}
        tags_map = db.get_image_tags_map(ids) if ids else {}
        for image_id in ids:
            record = images_map.get(image_id)
            if record is None:
                process_unreadable(image_id, "")
                continue
            source_identity = _inspect_source(
                str(record.get("path") or ""),
                cancellation_requested,
            )
            if source_identity is None:
                export_index += 1
                process_unreadable(image_id, str(record.get("path") or ""))
                continue
            process_record(
                dict(record),
                list(tags_map.get(image_id, []) or []),
                source_identity,
            )

    def process_path(raw_path: str) -> None:
        nonlocal processed
        if cancellation_requested():
            raise DatasetReadinessCancelledError(
                f"Dataset readiness cancelled after {processed} of {total_requested} items"
            )
        source_identity = _inspect_source(raw_path, cancellation_requested)
        if source_identity is None:
            process_unreadable(0, str(raw_path))
            return
        source_path = source_identity[0]
        if source_path in seen_virtual_paths:
            update_input("duplicate", {"path": source_path})
            add_issue(_make_issue(
                severity="warning",
                code="duplicate_source",
                message=f"Duplicate source is skipped: {source_path!r}",
                image_id=None,
                source_path=source_path,
                destination=None,
                observed="the same normalized source appeared more than once",
                expected="one export attempt per normalized source",
                action="Remove the duplicate source from the Dataset Maker selection.",
            ))
            processed += 1
            emit_progress()
            return
        seen_virtual_paths.add(source_path)
        process_record(
            dict(virtual_image_record_for_path(source_path, read_dimensions=False)),
            [],
            source_identity,
        )

    for image_path in request.image_paths:
        process_path(image_path)
    for image_path in _iter_requested_scan_paths(request):
        process_path(image_path)

    for annotation_key in sorted(set(resolved_annotations) - used_annotation_keys):
        add_issue(_make_issue(
            severity="blocker",
            code="annotation_selection_extra",
            message=(
                "Strict Dataset annotation selection does not match any exported item: "
                f"key={annotation_key!r}"
            ),
            image_id=None,
            source_path=None,
            destination=None,
            observed=f"unused selection key {annotation_key!r}",
            expected="exactly one selection for every exported item and no extras",
            action="Reload the Dataset Project membership and run readiness again.",
        ))

    for _code, (advisory, affected, sample_paths) in sorted(dialect_advisories.items()):
        add_issue(_dialect_advisory_issue(
            advisory,
            affected=affected,
            sample_paths=sample_paths,
        ))

    if trainable_pairs == 0:
        add_issue(_make_issue(
            severity="blocker",
            code="zero_trainable_pairs",
            message="The request produces zero trainable image-caption pairs",
            image_id=None,
            source_path=None,
            destination=str(output_folder) if output_mode == "folder" else None,
            observed="zero trainable pairs",
            expected="at least one valid image-caption pair",
            action="Repair the blocking issues or add a valid source before exporting.",
        ))

    status = _readiness_status(blocker_count, warning_count)
    return DatasetReadinessReport(
        report_id=readiness_report_id,
        input_fingerprint=input_hasher.hexdigest(),
        rule_version=DATASET_READINESS_RULE_VERSION,
        summary=DatasetReadinessSummary(
            status=status,
            total_requested=total_requested,
            processed=processed,
            trainable_pairs=trainable_pairs,
            blocker_count=blocker_count,
            warning_count=warning_count,
        ),
        issues=issues,
        total_issues=total_issues,
        issues_truncated=total_issues > len(issues),
        sample_pairs=sample_pairs,
        sample_pairs_truncated=trainable_pairs > len(sample_pairs),
    )
