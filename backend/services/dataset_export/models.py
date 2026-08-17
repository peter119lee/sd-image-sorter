"""Pydantic request/response models for the dataset export service.

Moved verbatim from services/dataset_export_service.py (decomposition 2026-07). Defined ONCE here and re-exported by the
facade so the from-import bindings in routers/dataset.py keep class identity for
FastAPI response_model coercion and request validation. No duplicate definition
of these classes may ever exist.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.annotation_models import (
    AnnotationAuthorClass,
    AnnotationRevisionProvenance,
    AnnotationRevisionSource,
    TrainingCaptionContentV1,
)
from services.dataset_export._constants import TRAINING_TAG_CONTENT_MODES
from services.dataset_trigger import (
    DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    DatasetTrigger,
)


ExportProgressCallback = Callable[[Dict[str, Any]], None]

DatasetAnnotationRevisionSource = AnnotationRevisionSource
DatasetAnnotationAuthorClass = AnnotationAuthorClass

_ANNOTATION_SELECTION_CONTENT_MODES = {
    *TRAINING_TAG_CONTENT_MODES,
    "nl_caption",
    "template",
}


class DatasetTemplateOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    preset_id: str = Field(default="custom", max_length=100)
    template_override: Optional[str] = Field(default=None, max_length=4096)
    trigger: DatasetTrigger = ""
    blacklist: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    replace_rules: Dict[str, str] = Field(default_factory=dict, max_length=1000)
    max_tags: int = Field(default=0, ge=0, le=1000)
    append: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    quality_override: Optional[str] = Field(default=None, max_length=100)
    safety_override: Optional[str] = Field(default=None, max_length=100)
    rating_override: Optional[str] = Field(default=None, max_length=100)
    underscore_to_space_override: Optional[bool] = None
    preserve_underscore_prefixes_override: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )


class _StrictAnnotationSelectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DatasetRevisionAnnotationSelection(_StrictAnnotationSelectionModel):
    kind: Literal["revision_ref"]
    revision_id: int = Field(strict=True, ge=1)


class DatasetFrozenDraftAnnotationSelection(_StrictAnnotationSelectionModel):
    kind: Literal["frozen_draft"]
    content: TrainingCaptionContentV1


class DatasetDynamicSourceAnnotationSelection(_StrictAnnotationSelectionModel):
    kind: Literal["dynamic_source"]


DatasetAnnotationSelection = Annotated[
    DatasetRevisionAnnotationSelection
    | DatasetFrozenDraftAnnotationSelection
    | DatasetDynamicSourceAnnotationSelection,
    Field(discriminator="kind"),
]


def _validate_annotation_selection_contract(
    request: "DatasetExportRequest | DatasetExportPreviewRequest",
) -> "DatasetExportRequest | DatasetExportPreviewRequest":
    project_id = request.dataset_project_id
    project_revision = request.dataset_project_revision
    if (project_id is None) != (project_revision is None):
        raise ValueError(
            "dataset_project_id and dataset_project_revision must be provided together"
        )
    selections = request.annotation_selections
    if any(not key.strip() for key in selections):
        raise ValueError("annotation_selections keys must be non-empty")
    content_mode = str(request.content_mode).strip().lower()
    if selections and content_mode not in _ANNOTATION_SELECTION_CONTENT_MODES:
        raise ValueError(
            "annotation_selections require a training caption content_mode; "
            f"got {request.content_mode!r}"
        )
    if selections and (
        request.image_overrides
        or request.image_types
        or request.image_nl_overrides
    ):
        raise ValueError(
            "annotation_selections cannot be combined with image_overrides, "
            "image_types, or image_nl_overrides"
        )
    if project_id is not None and not selections:
        raise ValueError(
            "annotation_selections are required for a named Dataset Project export"
        )
    has_revision = any(
        isinstance(selection, DatasetRevisionAnnotationSelection)
        for selection in selections.values()
    )
    if has_revision and project_id is None:
        raise ValueError(
            "dataset_project_id and dataset_project_revision are required for "
            "revision_ref annotation_selections"
        )
    return request


def _validate_template_options_contract(
    request: "DatasetExportRequest | DatasetExportPreviewRequest",
) -> "DatasetExportRequest | DatasetExportPreviewRequest":
    options = request.template_options
    if options is None:
        return request
    request_fields = request.model_fields_set
    option_fields = options.model_fields_set
    if (
        "trigger" in request_fields
        and "trigger" in option_fields
        and request.trigger != options.trigger
    ):
        raise ValueError(
            "template_options.trigger must match the top-level trigger"
        )
    if (
        "blacklist" in request_fields
        and "blacklist" in option_fields
        and request.blacklist != options.blacklist
    ):
        raise ValueError(
            "template_options.blacklist must match the top-level blacklist"
        )
    return request


class DatasetSubjectCropSettings(BaseModel):
    """One explicit, backward-compatible mask-driven crop configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = Field(strict=True)
    alpha_threshold: int = Field(strict=True, ge=1, le=255)
    padding_percent: int = Field(strict=True, ge=0, le=100)
    background_mode: Literal[
        "keep_background",
        "transparent_rgba",
        "solid_color",
    ]
    solid_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


def disabled_subject_crop_settings() -> DatasetSubjectCropSettings:
    """Return the neutral settings used when an older client omits the field."""
    return DatasetSubjectCropSettings(
        enabled=False,
        alpha_threshold=1,
        padding_percent=0,
        background_mode="keep_background",
        solid_color="#000000",
    )


class DatasetBucketResizeSettings(BaseModel):
    """One explicit, backward-compatible bucket preprocessing configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = Field(strict=True)
    subject_aware: bool = Field(strict=True)
    alpha_threshold: int = Field(strict=True, ge=1, le=255)


def disabled_bucket_resize_settings() -> DatasetBucketResizeSettings:
    """Return neutral bucket settings when an older client omits the field."""
    return DatasetBucketResizeSettings(
        enabled=False,
        subject_aware=False,
        alpha_threshold=128,
    )


class DatasetWatermarkRegion(BaseModel):
    """One normalized watermark rectangle in basis points of the image."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    x: int = Field(strict=True, ge=0, le=10000)
    y: int = Field(strict=True, ge=0, le=10000)
    width: int = Field(strict=True, gt=0, le=10000)
    height: int = Field(strict=True, gt=0, le=10000)

    @model_validator(mode="after")
    def validate_bounds(self) -> "DatasetWatermarkRegion":
        if self.x + self.width > 10000 or self.y + self.height > 10000:
            raise ValueError("watermark removal region must stay within 0..10000")
        return self


class DatasetWatermarkRemovalSettings(BaseModel):
    """Explicit CPU inpainting settings for Dataset export copies."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: bool = Field(default=False, strict=True)
    method: Literal["telea", "ns"] = "telea"
    radius: int = Field(default=3, strict=True, ge=1, le=20)
    padding_percent: int = Field(default=0, strict=True, ge=0, le=10)
    regions: List[DatasetWatermarkRegion] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_regions(self) -> "DatasetWatermarkRemovalSettings":
        if self.enabled and not self.regions:
            raise ValueError(
                "watermark_removal.regions requires at least one region when enabled"
            )
        return self


def disabled_watermark_removal_settings() -> DatasetWatermarkRemovalSettings:
    """Return neutral settings for clients that do not request cleanup."""
    return DatasetWatermarkRemovalSettings(
        enabled=False,
        method="telea",
        radius=3,
        padding_percent=0,
        regions=[],
    )


class DatasetExportRequest(BaseModel):
    """Request schema for ``POST /api/dataset/export``.

    The UI still behaves best for curated LoRA-sized sets, but the API no
    longer imposes an arbitrary image-count cap. Large folder imports should
    use ``dataset_scan_tokens`` so the browser sends only a compact token while
    the backend streams the manifest.

    Two import sources are supported in one request:

    * ``image_ids`` — IDs from the main library DB, resolved via
      ``database.get_images_by_ids`` (legacy + 'send selection' flow).
    * ``image_paths`` — absolute file paths supplied by the Dataset
      Maker session for items the user imported directly from a folder
      (issue #5 point 5: "small gallery" without DB pollution). The
      export pipeline builds virtual records for these paths so the
      same rename + caption + sidecar logic applies.

    At least one of the two must be non-empty.
    """
    image_ids: List[int] = Field(default_factory=list)
    image_paths: List[str] = Field(default_factory=list)
    dataset_scan_tokens: List[DatasetReadinessScanToken] = Field(
        default_factory=list,
        max_length=100,
    )
    output_folder: str = Field(default="", max_length=4096)
    output_mode: str = Field(default="folder", max_length=24)

    naming_pattern: str = Field(default="{filename}", min_length=1, max_length=200)
    trigger: DatasetTrigger = ""
    image_op: str = Field(default="copy")
    overwrite_policy: str = Field(default="unique")

    # Caption rendering options — match the export-template engine knobs
    # the Dataset Maker UI exposes.
    content_mode: str = Field(default="template", max_length=32)
    prefix: str = Field(default="", max_length=256)
    template_options: Optional[DatasetTemplateOptions] = None
    caption_transforms: Optional[Dict[str, Any]] = None
    blacklist: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    common_tags: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    normalize_tag_underscores: bool = True

    # User-edited captions, keyed by either ``str(image_id)`` (for
    # gallery-source items) or absolute path (for local-source items).
    # Empty string means "use whatever the template engine renders".
    image_overrides: Dict[str, str] = Field(default_factory=dict)

    # Per-image natural-language caption type (point 3: two-box editor). Keyed
    # like ``image_overrides``. Values: ``"booru"`` (tags only — the default and
    # the back-compat path; absent keys behave identically), ``"nl"`` (replace
    # tags with the natural-language sentence), ``"both"`` (tags then sentence).
    # ``image_nl_overrides`` carries the user-edited NL-box text per image so a
    # freshly-rendered booru caption can be paired with an edited sentence
    # without freezing the whole caption.
    image_types: Dict[str, str] = Field(default_factory=dict)
    image_nl_overrides: Dict[str, str] = Field(default_factory=dict)
    dataset_project_id: Optional[int] = Field(default=None, strict=True, ge=1)
    dataset_project_revision: Optional[int] = Field(default=None, strict=True, ge=1)
    annotation_selections: Dict[str, DatasetAnnotationSelection] = Field(
        default_factory=dict,
    )

    # Export stored masks in the selected trainer's exact layout. Kohya and
    # Anima use different names and directories, so their modes are distinct.
    mask_export: str = Field(default="none", max_length=16)

    subject_crop: DatasetSubjectCropSettings = Field(
        default_factory=disabled_subject_crop_settings
    )
    bucket_resize: DatasetBucketResizeSettings = Field(
        default_factory=disabled_bucket_resize_settings
    )
    watermark_removal: DatasetWatermarkRemovalSettings = Field(
        default_factory=disabled_watermark_removal_settings
    )

    # Optional pinned trainer handoff. Kohya and Anima use distinct verified
    # TOML schemas and incompatible mask layouts; "none" writes no config.
    trainer_config: str = Field(default="none", max_length=16)
    trainer_repeats: int = Field(default=10, ge=1, le=1000)
    trainer_batch: int = Field(default=2, ge=1, le=64)
    trainer_resolution: int = Field(default=1024, ge=256, le=4096)
    # keep_tokens: how many leading caption tokens stay FIXED while the
    # rest shuffle (official config example: shuffle_caption = true +
    # keep_tokens = N). This is how the trigger word survives shuffling.
    # 0 = don't emit shuffle/keep lines at all.
    trainer_keep_tokens: int = Field(default=0, ge=0, le=50)

    # Public export transports require both values. They stay optional in the
    # shared model so read-only readiness and the internal engine can use the
    # same request shape without fabricating an authorization proof.
    readiness_report_id: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{32}$",
    )
    readiness_input_fingerprint: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def validate_annotation_selection_contract(self) -> "DatasetExportRequest":
        _validate_template_options_contract(self)
        return _validate_annotation_selection_contract(self)


class DatasetExportPreviewRequest(BaseModel):
    """Request schema for ``POST /api/dataset/export-preview``.

    This mirrors the export request but does not require an output folder.
    The preview must render captions through the exact same helper as export
    so the text the user edits is the text that lands in sidecars.
    """

    model_config = ConfigDict(extra="ignore")

    image_ids: List[int] = Field(default_factory=list)
    image_paths: List[str] = Field(default_factory=list)
    dataset_scan_tokens: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)
    output_folder: str = Field(default="", max_length=4096)
    output_mode: str = Field(default="folder", max_length=24)

    naming_pattern: str = Field(default="{filename}", min_length=1, max_length=200)
    trigger: DatasetTrigger = ""
    overwrite_policy: str = Field(default="unique")

    content_mode: str = Field(default="template", max_length=32)
    prefix: str = Field(default="", max_length=256)
    template_options: Optional[DatasetTemplateOptions] = None
    caption_transforms: Optional[Dict[str, Any]] = None
    blacklist: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    common_tags: List[str] = Field(
        default_factory=list,
        max_length=DATASET_CAPTION_TAG_LIST_MAX_LENGTH,
    )
    normalize_tag_underscores: bool = True
    image_overrides: Dict[str, str] = Field(default_factory=dict)
    image_types: Dict[str, str] = Field(default_factory=dict)
    image_nl_overrides: Dict[str, str] = Field(default_factory=dict)
    dataset_project_id: Optional[int] = Field(default=None, strict=True, ge=1)
    dataset_project_revision: Optional[int] = Field(default=None, strict=True, ge=1)
    annotation_selections: Dict[str, DatasetAnnotationSelection] = Field(
        default_factory=dict,
    )
    mask_export: str = Field(default="none", max_length=16)
    subject_crop: DatasetSubjectCropSettings = Field(
        default_factory=disabled_subject_crop_settings
    )
    bucket_resize: DatasetBucketResizeSettings = Field(
        default_factory=disabled_bucket_resize_settings
    )
    watermark_removal: DatasetWatermarkRemovalSettings = Field(
        default_factory=disabled_watermark_removal_settings
    )
    trainer_resolution: int = Field(default=1024, ge=256, le=4096)
    limit: int = Field(default=72, ge=1, le=500)

    @model_validator(mode="after")
    def validate_annotation_selection_contract(self) -> "DatasetExportPreviewRequest":
        _validate_template_options_contract(self)
        return _validate_annotation_selection_contract(self)


class DatasetExportItemResult(BaseModel):
    image_id: int
    src_image_path: Optional[str] = None
    dst_image_path: Optional[str] = None
    dst_caption_path: Optional[str] = None
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


class DatasetExportWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    code: Literal["backup_cleanup_failed"]
    message: str
    backup_path: str
    error_type: str
    error: str


class DatasetExportResponse(BaseModel):
    status: str  # "ok" | "partial" | "failed" | "cancelled"
    exported: int
    skipped: int
    error_count: int
    masks_written: int = 0
    masks_missing: int = 0
    trainer_config_path: Optional[str] = None
    output_folder: str
    output_mode: str = "folder"
    items: List[DatasetExportItemResult]
    total_items: int = 0
    items_truncated: bool = False
    error_messages: List[str]
    warnings: List[DatasetExportWarning] = Field(default_factory=list)
    package_status: Literal["not_requested", "complete", "incomplete"] = "not_requested"
    package_run_id: Optional[str] = None
    package_manifest_path: Optional[str] = None


class DatasetExportStartResponse(BaseModel):
    status: str
    job_id: str
    total: int
    output_folder: str
    message: str


class DatasetReadinessScanToken(BaseModel):
    """One typed Dataset Maker scan-manifest source."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    scan_token: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")
    exclude_paths: List[str] = Field(default_factory=list, max_length=100_000)


class DatasetReadinessRequest(DatasetExportRequest):
    """Read-only exact-output preflight using the current export settings."""

    model_config = ConfigDict(extra="ignore", strict=True)



class DatasetReadinessIssueEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observed: str
    expected: str


class DatasetReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    severity: Literal["blocker", "warning"]
    code: str
    message: str
    issue_id: str
    rule_version: str
    evidence: DatasetReadinessIssueEvidence
    action: str
    destination: Optional[str]
    image_id: Optional[int]
    source_path: Optional[str]


class DatasetReadinessPair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image_id: int
    source_path: str
    output_image_path: Optional[str]
    output_caption_path: str


class DatasetReadinessSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ready", "warnings", "blocked"]
    total_requested: int
    processed: int
    trainable_pairs: int
    blocker_count: int
    warning_count: int


class DatasetReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    report_id: str
    input_fingerprint: str
    rule_version: str
    summary: DatasetReadinessSummary
    issues: List[DatasetReadinessIssue]
    total_issues: int
    issues_truncated: bool
    sample_pairs: List[DatasetReadinessPair]
    sample_pairs_truncated: bool


class DatasetReadinessStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    job_id: str
    kind: Literal["dataset_readiness"]
    status: Literal["queued"]
    total: int
    processed: int
    message: str


class DatasetReadinessConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal[
        "readiness_report_required",
        "readiness_report_not_found",
        "readiness_report_expired",
        "readiness_report_wrong_kind",
        "readiness_report_cancelled",
        "readiness_report_not_ready",
        "readiness_report_unavailable",
        "readiness_rule_mismatch",
        "readiness_request_mismatch",
        "readiness_fingerprint_mismatch",
        "readiness_input_mismatch",
        "readiness_blocked",
    ]
    message: str
    action: str
    report_id: Optional[str]
    expected_input_fingerprint: Optional[str]
    observed_input_fingerprint: Optional[str]
    rule_version: str
    issues: List[DatasetReadinessIssue]


class _StrictPackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DatasetPackageSourceIdentity(_StrictPackageModel):
    image_id: int = Field(ge=0)
    filename: str = Field(min_length=1, max_length=1024)
    path_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: Optional[int] = Field(ge=0)
    mtime_ns: Optional[int] = Field(ge=0)
    sha256: Optional[str] = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetPackageAnnotationSnapshot(_StrictPackageModel):
    kind: Literal["legacy_snapshot"]
    revision_id: None
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetPackageRevisionAnnotation(AnnotationRevisionProvenance):
    kind: Literal["revision_ref"]
    revision_id: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rendered_caption_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetPackageFrozenDraftAnnotation(_StrictPackageModel):
    kind: Literal["frozen_draft"]
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rendered_caption_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


DatasetPackageAnnotation = Annotated[
    DatasetPackageAnnotationSnapshot
    | DatasetPackageRevisionAnnotation
    | DatasetPackageFrozenDraftAnnotation,
    Field(discriminator="kind"),
]


class DatasetPackageArtifact(_StrictPackageModel):
    role: Literal["image", "caption", "mask", "trainer_config"]
    path: str = Field(min_length=1, max_length=4096)
    required: Literal[True]
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetPackageInventoryRecord(_StrictPackageModel):
    index: int = Field(ge=1)
    source: DatasetPackageSourceIdentity
    disposition: Literal["exported", "skipped", "failed"]
    reason: Optional[str]
    annotation: Optional[DatasetPackageAnnotation]
    outputs: Tuple[DatasetPackageArtifact, ...]


class DatasetPackageTrainer(_StrictPackageModel):
    id: Literal["kohya_sd_scripts", "anima_lora"]
    wire_value: Literal["kohya_toml", "anima_lora_toml"]
    contract_version: str = Field(min_length=1, max_length=64)
    upstream_repository: str = Field(min_length=1, max_length=4096)
    upstream_tag: str = Field(min_length=1, max_length=256)
    upstream_commit: str = Field(pattern=r"^[a-f0-9]{40}$")


class DatasetPackageOptions(_StrictPackageModel):
    content_mode: str = Field(min_length=1, max_length=32)
    caption_extension: str = Field(min_length=1, max_length=16)
    mask_export: str = Field(min_length=1, max_length=16)
    naming_pattern: str = Field(min_length=1, max_length=200)
    image_op: Literal["copy"]
    overwrite_policy: Literal["unique", "overwrite", "skip"]
    trainer_repeats: int = Field(ge=1, le=1000)
    trainer_batch: int = Field(ge=1, le=64)
    trainer_resolution: int = Field(ge=256, le=4096)
    trainer_keep_tokens: int = Field(ge=0, le=50)
    trigger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetPackageCounts(_StrictPackageModel):
    requested: int = Field(ge=0)
    processed: int = Field(ge=0)
    exported: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    masks_written: int = Field(ge=0)
    masks_missing: int = Field(ge=0)
    inventory_records: int = Field(ge=0)


class DatasetPackageInventorySummary(_StrictPackageModel):
    path: Literal["export_inventory.jsonl"]
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_count: int = Field(ge=0)


class DatasetPackageManifest(_StrictPackageModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_id: Literal["sd-image-sorter.dataset-package"] = Field(alias="schema")
    manifest_version: Literal[2]
    producer: Literal["SD Image Sorter"]
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    package_status: Literal["building", "complete", "incomplete"]
    started_at: str = Field(min_length=1, max_length=64)
    finished_at: Optional[str] = Field(max_length=64)
    trainer: DatasetPackageTrainer
    options: DatasetPackageOptions
    readiness: None
    counts: DatasetPackageCounts
    inventory: Optional[DatasetPackageInventorySummary]
    package_artifacts: Tuple[DatasetPackageArtifact, ...]
    errors: Tuple[str, ...]


class DatasetPackageVerificationRequest(_StrictPackageModel):
    output_folder: str = Field(min_length=1, max_length=4096)
    expected_run_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class DatasetPackageVerificationIssue(_StrictPackageModel):
    code: str = Field(min_length=1, max_length=128)
    path: Optional[str] = Field(max_length=4096)
    expected: str = Field(max_length=4096)
    observed: str = Field(max_length=4096)


class DatasetPackageVerificationResponse(_StrictPackageModel):
    status: Literal["complete", "incomplete", "invalid", "missing"]
    valid: bool
    run_id: Optional[str]
    checked_records: int = Field(ge=0)
    checked_artifacts: int = Field(ge=0)
    issues: Tuple[DatasetPackageVerificationIssue, ...]
