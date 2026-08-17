"""Typed, read-only issue aggregation for the Dataset Review Cockpit."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import secrets
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

import database as db
from metadata_parser import PARSED_METADATA_VERSION
from services import duplicate_group_service
from services.dataset_consistency_service import RATING_TAG_NAMES, _fold
from tag_writer_provenance import ImageContentFingerprint, TagWriterProvenance


REVIEW_SCHEMA_VERSION = 1
MAX_REVIEW_IMAGES = 20_000
LOW_TAG_CONFIDENCE_THRESHOLD = 0.50
_CURSOR_SIGNING_KEY = secrets.token_bytes(32)

PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
ExplicitModelAssetSourceMode = Literal[
    "webui_parameters",
    "forge_parameters",
    "reforge_parameters",
    "fooocus_comment",
    "easy_diffusion_text",
    "invokeai_metadata",
    "swarmui_parameters",
    "drawthings_xmp",
    "fast_path",
    "nai_usercomment",
    "nai_comment",
    "nai_description",
    "nai_software_tag",
    "explicit_metadata",
]
FallbackModelAssetSourceMode = Literal[
    "global_candidate_fallback",
    "workflow_widget_fallback",
    "global_graph_fallback",
]
ModelAssetSourceMode = ExplicitModelAssetSourceMode | FallbackModelAssetSourceMode
SidecarCarrier = Literal["txt", "json", "xmp"]
SidecarField = Literal["prompt", "negative_prompt", "checkpoint", "loras"]

_FALLBACK_MODEL_ASSET_SOURCE_MODES = frozenset(
    {
        "global_candidate_fallback",
        "workflow_widget_fallback",
        "global_graph_fallback",
    }
)
_PORTABLE_BASENAME_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class ReviewIssueKind(str, Enum):
    FILE_MISSING = "file_missing"
    IMAGE_UNREADABLE = "image_unreadable"
    EMPTY_CAPTION = "empty_caption"
    RATING_CONFLICT = "rating_conflict"
    LOW_TAG_CONFIDENCE = "low_tag_confidence"
    METADATA_PROVENANCE_RISK = "metadata_provenance_risk"
    SIDECAR_METADATA_DEPENDENCY = "sidecar_metadata_dependency"
    SMALL_IMAGE = "small_image"
    LOW_AESTHETIC = "low_aesthetic"
    DUPLICATE_GROUP = "duplicate_group"


class CaptionState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image_id: PositiveStrictInt = Field(...)
    has_content: StrictBool = Field(...)


class DatasetReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = Field(...)
    image_ids: List[PositiveStrictInt] = Field(...)
    caption_states: List[CaptionState] = Field(...)
    logical_count: PositiveStrictInt = Field(...)
    local_path_count: NonNegativeStrictInt = Field(...)
    minimum_dimension: Optional[Annotated[StrictInt, Field(gt=0, le=8192)]] = Field(...)
    minimum_aesthetic: Optional[Annotated[float, Field(strict=True, ge=0.0, le=10.0)]] = Field(...)
    include_persisted_duplicates: StrictBool = Field(...)
    issue_kinds: List[ReviewIssueKind] = Field(..., min_length=1)
    cursor: Optional[StrictStr] = Field(...)
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = Field(...)

    @field_validator("image_ids")
    @classmethod
    def validate_image_scope(cls, values: List[int]) -> List[int]:
        unique_ids = set(values)
        if not unique_ids:
            raise ValueError("image_ids must contain at least one positive database image id")
        if len(unique_ids) != len(values):
            raise ValueError("image_ids must not contain duplicates")
        if len(unique_ids) > MAX_REVIEW_IMAGES:
            raise ValueError(
                f"image_ids contains {len(unique_ids)} unique ids; maximum is {MAX_REVIEW_IMAGES}"
            )
        return values

    @field_validator("issue_kinds")
    @classmethod
    def validate_issue_kinds(cls, values: List[ReviewIssueKind]) -> List[ReviewIssueKind]:
        if len(set(values)) != len(values):
            raise ValueError("issue_kinds must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_scope_evidence(self) -> "DatasetReviewRequest":
        scope = set(self.image_ids)
        caption_ids = [state.image_id for state in self.caption_states]
        if len(set(caption_ids)) != len(caption_ids):
            raise ValueError("caption_states must contain each image_id exactly once")
        caption_scope = set(caption_ids)
        if caption_scope != scope:
            missing = sorted(scope - caption_scope)
            unexpected = sorted(caption_scope - scope)
            raise ValueError(
                "caption_states must exactly cover image_ids; "
                f"missing={missing[:20]}, unexpected={unexpected[:20]}"
            )
        minimum_logical_count = len(scope) + self.local_path_count
        if self.logical_count < minimum_logical_count:
            raise ValueError(
                "logical_count must include every unique database image and local-path item; "
                f"minimum={minimum_logical_count}, received={self.logical_count}"
            )
        return self


class ReviewSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: PositiveStrictInt
    filename: Optional[StrictStr]
    source_path: Optional[StrictStr]


class ReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_en: StrictStr
    label_zh: StrictStr
    value_en: StrictStr
    value_zh: StrictStr


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["open_image"]
    availability: Literal["available", "not_available"]
    reason_en: StrictStr
    reason_zh: StrictStr


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: StrictStr
    kind: ReviewIssueKind
    severity: Literal["high", "medium", "low"]
    title_en: StrictStr
    title_zh: StrictStr
    detail_en: StrictStr
    detail_zh: StrictStr
    subjects: List[ReviewSubject] = Field(..., min_length=1)
    evidence: List[ReviewEvidence] = Field(..., min_length=1)
    source_provider: Literal[
        "database",
        "caption_states",
        "metadata_provenance",
        "persisted_duplicates",
    ]
    evidence_status: Literal["available", "partial", "not_available"]
    heuristic: StrictBool
    action: ReviewAction


class ReviewProviderState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "scope",
        "file_integrity",
        "caption_integrity",
        "tag_integrity",
        "dimensions",
        "aesthetic_scores",
        "metadata_provenance",
        "persisted_duplicates",
    ]
    status: Literal["available", "partial", "not_available", "not_requested"]
    reason_en: StrictStr
    reason_zh: StrictStr
    observed_at: Optional[StrictStr]


class DatasetReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    scope_fingerprint: StrictStr
    issues: List[ReviewIssue]
    total: NonNegativeStrictInt
    has_more: StrictBool
    next_cursor: Optional[StrictStr]
    provider_states: List[ReviewProviderState]


class _StoredImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: PositiveStrictInt
    path: StrictStr
    filename: StrictStr
    width: Optional[StrictInt]
    height: Optional[StrictInt]
    aesthetic_score: Optional[float]
    is_readable: Literal[0, 1]
    read_error: Optional[StrictStr]
    content_fingerprint: Optional[StrictStr]

    @field_validator("aesthetic_score")
    @classmethod
    def mark_non_finite_aesthetic_unavailable(
        cls,
        value: Optional[float],
    ) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            return None
        return value


class _StoredTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag: StrictStr
    confidence: Optional[Annotated[float, Field(strict=True)]] = Field(...)

    @field_validator("confidence")
    @classmethod
    def mark_non_finite_confidence_unknown(
        cls,
        value: Optional[float],
    ) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            return None
        return value


class _StoredProvenanceTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag: StrictStr
    confidence: Optional[Annotated[float, Field(strict=True)]] = Field(...)
    source: Optional[StrictStr] = Field(...)
    category: Optional[StrictStr] = Field(...)

    @field_validator("source")
    @classmethod
    def reject_numeric_source_storage(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is not None and value.strip().isdigit():
            raise ValueError("tag source must be a textual provenance identifier")
        return value


class _StoredTagWriterProvenance(TagWriterProvenance):
    content_fingerprint: ImageContentFingerprint
    created_at: Optional[StrictStr]


class _CurrentWriterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    image_id: PositiveStrictInt
    writer_family: Literal["wd14"]
    provider: Literal["huggingface", "local_onnx"]
    model: StrictStr
    revision: StrictStr
    runtime_provider: StrictStr
    content_fingerprint: ImageContentFingerprint


class _StoredProvenanceImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: PositiveStrictInt
    metadata_json: Optional[StrictStr]
    ai_caption: Optional[StrictStr]
    nl_caption: Optional[StrictStr]


class _MetadataJsonObject(RootModel[Dict[StrictStr, JsonValue]]):
    pass


class _StoredModelAssetCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: StrictStr = Field(..., min_length=1)
    source_mode: ModelAssetSourceMode
    match_type: StrictStr = Field(..., min_length=1)
    confidence: StrictStr = Field(..., min_length=1)

    @field_validator("name", "match_type", "confidence")
    @classmethod
    def reject_blank_candidate_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model asset candidate fields must not be blank")
        return value


class _StoredModelAssets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checkpoint_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    unet_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    diffusion_model_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    model_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    lora_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    vae_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    clip_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    yolo_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    workflow_widget_lora_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    global_lora_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)
    global_yolo_candidates: Optional[List[_StoredModelAssetCandidate]] = Field(default=None)


class _StoredParsedMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Optional[StrictInt] = Field(default=None)
    model_assets: Optional[_StoredModelAssets] = Field(default=None)


class _StoredMetadataEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parsed: Optional[_StoredParsedMetadata] = Field(default=None, alias="_parsed")


class _StoredSidecarFallbackEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carrier: SidecarCarrier
    basename: StrictStr = Field(..., min_length=1, max_length=255)
    method: Literal["sidecar_fallback"]
    confidence: Literal["high"]
    parser_version: PositiveStrictInt
    fields: List[SidecarField] = Field(..., min_length=1)

    @field_validator("basename")
    @classmethod
    def validate_basename(cls, value: str) -> str:
        contains_invalid_character = any(
            ord(character) < 32
            or character in _PORTABLE_BASENAME_INVALID_CHARACTERS
            for character in value
        )
        reserved_stem = value.split(".", maxsplit=1)[0].rstrip(" .").upper()
        if (
            value != value.strip()
            or value in {".", ".."}
            or value.endswith(".")
            or contains_invalid_character
            or reserved_stem in _WINDOWS_RESERVED_BASENAMES
        ):
            raise ValueError(
                "basename must be one portable filename without path, control, "
                "reserved, or platform-invalid syntax"
            )
        return value

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: List[SidecarField]) -> List[SidecarField]:
        canonical_order = {
            "prompt": 0,
            "negative_prompt": 1,
            "checkpoint": 2,
            "loras": 3,
        }
        if len(set(values)) != len(values):
            raise ValueError("fields must not contain duplicates")
        if values != sorted(values, key=canonical_order.__getitem__):
            raise ValueError("fields must use canonical order")
        return values

    @model_validator(mode="after")
    def validate_carrier_suffix(self) -> "_StoredSidecarFallbackEvidence":
        expected_suffix = f".{self.carrier}"
        if not self.basename.lower().endswith(expected_suffix):
            raise ValueError(
                f"basename must end with {expected_suffix} for carrier={self.carrier}"
            )
        return self


class _StoredSidecarFallbackState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    evaluated: Literal[True]
    evidence: List[_StoredSidecarFallbackEvidence]


class _StoredSidecarParsedMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Optional[StrictInt] = Field(default=None)
    sidecar_fallback: Optional[_StoredSidecarFallbackState] = Field(default=None)


class _StoredSidecarMetadataEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parsed: Optional[_StoredSidecarParsedMetadata] = Field(default=None, alias="_parsed")


class _DuplicateSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embedded_count: NonNegativeStrictInt
    group_count: NonNegativeStrictInt
    redundant_count: NonNegativeStrictInt
    reclaimable_bytes: NonNegativeStrictInt
    threshold: float


class _DuplicateMember(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: PositiveStrictInt
    path: StrictStr
    filename: StrictStr
    width: Optional[StrictInt]
    height: Optional[StrictInt]
    file_size: Optional[NonNegativeStrictInt]
    aesthetic_score: Optional[float]
    user_rating: StrictInt
    suggested_keep: StrictBool


class _DuplicateGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    group_id: NonNegativeStrictInt
    similarity: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    members: List[_DuplicateMember] = Field(..., min_length=2)

    @model_validator(mode="after")
    def validate_unique_members(self) -> "_DuplicateGroup":
        member_ids = [member.id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("duplicate group members must have unique image ids")
        return self


class _DuplicateState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: StrictInt
    scanned_at: float
    threshold: float
    summary: _DuplicateSummary
    groups: List[_DuplicateGroup]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """Accept exactly the payload version the scan writes today.

        Bound to the writer's constant rather than repeated as a literal: a
        second copy of the number goes stale the moment the payload is
        versioned forward, and this reader would then reject every scan as
        invalid without anyone noticing.
        """
        expected = duplicate_group_service._RESULT_VERSION
        if value != expected:
            raise ValueError(
                f"unsupported duplicate scan payload version {value}; expected {expected}"
            )
        return value

    @field_validator("scanned_at")
    @classmethod
    def validate_scanned_at(cls, value: float) -> float:
        try:
            datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError(
                "scanned_at must be a finite timestamp representable as UTC datetime"
            ) from exc
        return value

    @model_validator(mode="after")
    def validate_unique_groups_and_members(self) -> "_DuplicateState":
        group_ids = [group.group_id for group in self.groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("duplicate scan groups must have unique group ids")
        member_ids = [
            member.id
            for group in self.groups
            for member in group.members
        ]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError(
                "duplicate scan member image ids must belong to exactly one group"
            )
        return self


class _CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    scope_fingerprint: StrictStr
    filter_fingerprint: StrictStr
    evidence_fingerprint: StrictStr
    last_key: List[StrictInt | StrictStr] = Field(..., min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_last_key(self) -> "_CursorPayload":
        if not (
            isinstance(self.last_key[0], int)
            and not isinstance(self.last_key[0], bool)
            and isinstance(self.last_key[1], int)
            and not isinstance(self.last_key[1], bool)
            and isinstance(self.last_key[2], str)
        ):
            raise ValueError("last_key must contain integer, integer, string")
        return self


IssueSortKey = Tuple[int, int, str]

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_KIND_ORDER = {
    ReviewIssueKind.FILE_MISSING: 0,
    ReviewIssueKind.IMAGE_UNREADABLE: 1,
    ReviewIssueKind.EMPTY_CAPTION: 2,
    ReviewIssueKind.RATING_CONFLICT: 3,
    ReviewIssueKind.LOW_TAG_CONFIDENCE: 4,
    ReviewIssueKind.METADATA_PROVENANCE_RISK: 5,
    ReviewIssueKind.SIDECAR_METADATA_DEPENDENCY: 6,
    ReviewIssueKind.LOW_AESTHETIC: 7,
    ReviewIssueKind.SMALL_IMAGE: 8,
    ReviewIssueKind.DUPLICATE_GROUP: 9,
}


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unique_image_ids(values: List[int]) -> List[int]:
    return list(dict.fromkeys(values))


def _scope_fingerprint(image_ids: List[int]) -> str:
    return _canonical_hash({"image_ids": sorted(image_ids)})


def _filter_fingerprint(request: DatasetReviewRequest) -> str:
    return _canonical_hash(
        {
            "include_persisted_duplicates": request.include_persisted_duplicates,
            "issue_kinds": sorted(kind.value for kind in request.issue_kinds),
            "local_path_count": request.local_path_count,
            "logical_count": request.logical_count,
            "minimum_aesthetic": request.minimum_aesthetic,
            "minimum_dimension": request.minimum_dimension,
        }
    )


def _subject(image_id: int, record: Optional[_StoredImage]) -> ReviewSubject:
    return ReviewSubject(
        image_id=image_id,
        filename=record.filename if record is not None else None,
        source_path=record.path if record is not None else None,
    )


def _evidence(label_en: str, label_zh: str, value: object) -> ReviewEvidence:
    if isinstance(value, bool):
        rendered_en = "Yes" if value else "No"
        rendered_zh = "是" if value else "否"
    elif value is None:
        rendered_en = "Not available"
        rendered_zh = "不可用"
    else:
        rendered_en = str(value)
        rendered_zh = rendered_en
    return ReviewEvidence(
        label_en=label_en,
        label_zh=label_zh,
        value_en=rendered_en,
        value_zh=rendered_zh,
    )


def _localized_evidence(
    label_en: str,
    label_zh: str,
    value_en: str,
    value_zh: str,
) -> ReviewEvidence:
    return ReviewEvidence(
        label_en=label_en,
        label_zh=label_zh,
        value_en=value_en,
        value_zh=value_zh,
    )


def _open_action(available: bool, reason_en: str, reason_zh: str) -> ReviewAction:
    return ReviewAction(
        kind="open_image",
        availability="available" if available else "not_available",
        reason_en=reason_en,
        reason_zh=reason_zh,
    )


def _image_can_open(record: Optional[_StoredImage]) -> bool:
    return bool(
        record is not None
        and record.is_readable == 1
        and Path(record.path).is_file()
    )


def _image_action(record: Optional[_StoredImage]) -> ReviewAction:
    if _image_can_open(record):
        return _open_action(
            True,
            "The image is available in the current Dataset Maker scope.",
            "该图片可在当前 Dataset Maker 范围中打开。",
        )
    return _open_action(
        False,
        "The stored image cannot be opened because its file is missing or unreadable.",
        "该图片文件缺失或不可读取，当前无法打开。",
    )


def _image_issue(
    *,
    image_id: int,
    record: Optional[_StoredImage],
    kind: ReviewIssueKind,
    severity: Literal["high", "medium", "low"],
    title_en: str,
    title_zh: str,
    detail_en: str,
    detail_zh: str,
    evidence: List[ReviewEvidence],
    source_provider: Literal["database", "caption_states", "metadata_provenance"],
    heuristic: bool,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=f"{kind.value}:{image_id}",
        kind=kind,
        severity=severity,
        title_en=title_en,
        title_zh=title_zh,
        detail_en=detail_en,
        detail_zh=detail_zh,
        subjects=[_subject(image_id, record)],
        evidence=evidence,
        source_provider=source_provider,
        evidence_status="available",
        heuristic=heuristic,
        action=_image_action(record),
    )


def _parse_database_records(
    raw_records: Dict[int, Dict[str, object]],
) -> Dict[int, _StoredImage]:
    records: Dict[int, _StoredImage] = {}
    for image_id, raw_record in raw_records.items():
        record = _StoredImage.model_validate(raw_record, strict=True)
        if record.id != int(image_id):
            raise ValueError(
                f"Database image lookup returned mismatched key={image_id}, row_id={record.id}"
            )
        records[record.id] = record
    return records


def _parse_provenance_records(
    raw_records: Dict[int, Dict[str, object]],
) -> Dict[int, _StoredProvenanceImage]:
    records: Dict[int, _StoredProvenanceImage] = {}
    for image_id, raw_record in raw_records.items():
        try:
            record = _StoredProvenanceImage.model_validate(raw_record, strict=True)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid metadata provenance fields for image id={image_id}: {exc}"
            ) from exc
        if record.id != int(image_id):
            raise ValueError(
                f"Database provenance lookup returned mismatched key={image_id}, row_id={record.id}"
            )
        records[record.id] = record
    return records


def _database_issues(
    request: DatasetReviewRequest,
    image_ids: List[int],
    records: Dict[int, _StoredImage],
) -> Tuple[List[ReviewIssue], int, int]:
    requested_kinds = set(request.issue_kinds)
    issues: List[ReviewIssue] = []
    unavailable_dimensions = 0
    unavailable_aesthetics = 0

    for image_id in image_ids:
        record = records.get(image_id)
        file_exists = bool(record is not None and Path(record.path).is_file())
        if (
            record is None
            and ReviewIssueKind.SMALL_IMAGE in requested_kinds
            and request.minimum_dimension is not None
        ):
            unavailable_dimensions += 1
        if (
            record is None
            and ReviewIssueKind.LOW_AESTHETIC in requested_kinds
            and request.minimum_aesthetic is not None
        ):
            unavailable_aesthetics += 1
        if ReviewIssueKind.FILE_MISSING in requested_kinds and not file_exists:
            issues.append(
                _image_issue(
                    image_id=image_id,
                    record=record,
                    kind=ReviewIssueKind.FILE_MISSING,
                    severity="high",
                    title_en="Image file is missing",
                    title_zh="图片文件缺失",
                    detail_en="The database entry no longer points to a file on disk. Restore or remove the entry before export.",
                    detail_zh="数据库记录指向的文件已不存在。导出前请恢复文件或移除该记录。",
                    evidence=[
                        _evidence("Database image ID", "数据库图片 ID", image_id),
                        _evidence("Stored path", "已存路径", record.path if record is not None else None),
                    ],
                    source_provider="database",
                    heuristic=False,
                )
            )
        if (
            ReviewIssueKind.IMAGE_UNREADABLE in requested_kinds
            and record is not None
            and record.is_readable == 0
        ):
            issues.append(
                _image_issue(
                    image_id=image_id,
                    record=record,
                    kind=ReviewIssueKind.IMAGE_UNREADABLE,
                    severity="high",
                    title_en="Image is marked unreadable",
                    title_zh="图片被标记为不可读取",
                    detail_en="The last library scan could not decode this image. Repair or replace it before export.",
                    detail_zh="最近一次图库扫描无法解码该图片。导出前请修复或替换。",
                    evidence=[
                        _evidence("Readable flag", "可读取标记", False),
                        _evidence("Read error", "读取错误", record.read_error),
                    ],
                    source_provider="database",
                    heuristic=False,
                )
            )
        if (
            ReviewIssueKind.SMALL_IMAGE in requested_kinds
            and request.minimum_dimension is not None
            and record is not None
        ):
            if record.width is None or record.height is None:
                unavailable_dimensions += 1
            elif min(record.width, record.height) < request.minimum_dimension:
                issues.append(
                    _image_issue(
                        image_id=image_id,
                        record=record,
                        kind=ReviewIssueKind.SMALL_IMAGE,
                        severity="low",
                        title_en="Image is below the minimum dimension",
                        title_zh="图片尺寸低于最低要求",
                        detail_en="The shorter edge is below the selected review threshold. Confirm that upscaling or exclusion is appropriate.",
                        detail_zh="图片短边低于当前审阅阈值。请确认是否需要放大或排除。",
                        evidence=[
                            _evidence("Stored dimensions", "已存尺寸", f"{record.width}x{record.height}"),
                            _evidence("Minimum dimension", "最低尺寸", request.minimum_dimension),
                        ],
                        source_provider="database",
                        heuristic=False,
                    )
                )
        if (
            ReviewIssueKind.LOW_AESTHETIC in requested_kinds
            and request.minimum_aesthetic is not None
            and record is not None
        ):
            if record.aesthetic_score is None:
                unavailable_aesthetics += 1
            elif record.aesthetic_score < request.minimum_aesthetic:
                issues.append(
                    _image_issue(
                        image_id=image_id,
                        record=record,
                        kind=ReviewIssueKind.LOW_AESTHETIC,
                        severity="medium",
                        title_en="Stored aesthetic score is below the threshold",
                        title_zh="已存美学评分低于阈值",
                        detail_en="This is a heuristic based only on a previously stored score. Inspect the image before deciding.",
                        detail_zh="这是仅基于历史已存评分的启发式提示。请先查看图片再决定。",
                        evidence=[
                            _evidence("Stored aesthetic score", "已存美学评分", record.aesthetic_score),
                            _evidence("Minimum aesthetic", "最低美学评分", request.minimum_aesthetic),
                        ],
                        source_provider="database",
                        heuristic=True,
                    )
                )
    return issues, unavailable_dimensions, unavailable_aesthetics


def _caption_issues(
    request: DatasetReviewRequest,
    records: Dict[int, _StoredImage],
) -> List[ReviewIssue]:
    if ReviewIssueKind.EMPTY_CAPTION not in set(request.issue_kinds):
        return []
    issues: List[ReviewIssue] = []
    for state in request.caption_states:
        if state.has_content:
            continue
        record = records.get(state.image_id)
        issues.append(
            _image_issue(
                image_id=state.image_id,
                record=record,
                kind=ReviewIssueKind.EMPTY_CAPTION,
                severity="medium",
                title_en="Caption is empty",
                title_zh="Caption 为空",
                detail_en="The effective caption currently shown in Dataset Maker has no content. Add a caption before export.",
                detail_zh="Dataset Maker 当前生效的 caption 没有内容。请在导出前补充。",
                evidence=[
                    _evidence("Browser caption content", "浏览器 caption 内容", False),
                    _localized_evidence(
                        "Evidence source",
                        "证据来源",
                        "Current Dataset Maker state",
                        "当前 Dataset Maker 状态",
                    ),
                ],
                source_provider="caption_states",
                heuristic=False,
            )
        )
    return issues


def _localized_rating_categories(categories: List[str]) -> Tuple[str, str]:
    translations = {
        "general": "普通",
        "sensitive": "敏感",
        "questionable": "存疑",
        "explicit": "露骨",
    }
    return (
        ", ".join(categories),
        "、".join(translations[category] for category in categories),
    )


def _rating_conflict_issue(
    image_id: int,
    tags: List[_StoredTag],
    record: Optional[_StoredImage],
) -> Optional[ReviewIssue]:
    rating_categories = {_fold(tag.tag).strip() for tag in tags}
    ordered_categories = [
        category
        for category in RATING_TAG_NAMES
        if category in rating_categories
    ]
    if len(ordered_categories) <= 1:
        return None
    categories_en, categories_zh = _localized_rating_categories(
        ordered_categories
    )
    return _image_issue(
        image_id=image_id,
        record=record,
        kind=ReviewIssueKind.RATING_CONFLICT,
        severity="medium",
        title_en="Multiple rating categories are assigned",
        title_zh="图片包含多个评级类别",
        detail_en="Conflicting rating tags can make filtering and dataset policy checks unreliable. Review the image and keep one rating category.",
        detail_zh="相互冲突的评级标签会影响筛选和数据集规则检查。请查看图片并只保留一个评级类别。",
        evidence=[
            _localized_evidence(
                "Rating categories",
                "评级类别",
                categories_en,
                categories_zh,
            ),
            _evidence(
                "Distinct rating category count",
                "不同评级类别数量",
                len(ordered_categories),
            ),
        ],
        source_provider="database",
        heuristic=True,
    )


def _low_tag_confidence_issue(
    image_id: int,
    tags: List[_StoredTag],
    record: Optional[_StoredImage],
) -> Optional[ReviewIssue]:
    low_confidence_by_tag: Dict[str, float] = {}
    for tag in tags:
        normalized_tag = _fold(tag.tag).strip()
        confidence = tag.confidence
        if (
            not normalized_tag
            or confidence is None
            or confidence <= 0.0
            or confidence >= LOW_TAG_CONFIDENCE_THRESHOLD
        ):
            continue
        existing_confidence = low_confidence_by_tag.get(normalized_tag)
        if existing_confidence is None or confidence < existing_confidence:
            low_confidence_by_tag[normalized_tag] = confidence
    if not low_confidence_by_tag:
        return None

    ordered_values = [
        f"{tag} ({low_confidence_by_tag[tag]:.4f})"
        for tag in sorted(low_confidence_by_tag)
    ]
    return _image_issue(
        image_id=image_id,
        record=record,
        kind=ReviewIssueKind.LOW_TAG_CONFIDENCE,
        severity="medium",
        title_en="Current tags include low-confidence assignments",
        title_zh="当前标签包含低置信结果",
        detail_en="These current persisted tags are below the existing 0.50 confidence bucket. Review the image before changing any tag.",
        detail_zh="这些当前已存标签低于现有的 0.50 置信区间。修改任何标签前请先查看图片。",
        evidence=[
            _localized_evidence(
                "Low-confidence tags",
                "低置信标签",
                ", ".join(ordered_values),
                "、".join(ordered_values),
            ),
            _evidence(
                "Low-confidence threshold",
                "低置信阈值",
                f"{LOW_TAG_CONFIDENCE_THRESHOLD:.4f}",
            ),
        ],
        source_provider="database",
        heuristic=True,
    )


def _tag_issues(
    request: DatasetReviewRequest,
    image_ids: List[int],
    records: Dict[int, _StoredImage],
    raw_tag_map: Dict[int, List[Dict[str, object]]],
) -> List[ReviewIssue]:
    requested_kinds = set(request.issue_kinds)
    requested_rating = ReviewIssueKind.RATING_CONFLICT in requested_kinds
    requested_low_confidence = (
        ReviewIssueKind.LOW_TAG_CONFIDENCE in requested_kinds
    )
    if not requested_rating and not requested_low_confidence:
        return []

    issues: List[ReviewIssue] = []
    for image_id in image_ids:
        tags = [
            _StoredTag.model_validate(row, strict=True)
            for row in raw_tag_map.get(image_id, [])
        ]
        if requested_rating:
            rating_issue = _rating_conflict_issue(
                image_id,
                tags,
                records.get(image_id),
            )
            if rating_issue is not None:
                issues.append(rating_issue)
        if requested_low_confidence:
            confidence_issue = _low_tag_confidence_issue(
                image_id,
                tags,
                records.get(image_id),
            )
            if confidence_issue is not None:
                issues.append(confidence_issue)
    return issues


def _load_review_tag_rows(
    request: DatasetReviewRequest,
    image_ids: List[int],
) -> Dict[int, List[Dict[str, object]]]:
    requested_kinds = set(request.issue_kinds)
    if not requested_kinds.intersection(
        {
            ReviewIssueKind.RATING_CONFLICT,
            ReviewIssueKind.LOW_TAG_CONFIDENCE,
            ReviewIssueKind.METADATA_PROVENANCE_RISK,
        }
    ):
        return {}
    chunk_maps: List[Dict[int, List[Dict[str, object]]]] = [
        db.get_image_tags_map(image_ids[start : start + 500])
        for start in range(0, len(image_ids), 500)
    ]
    return {
        image_id: rows
        for chunk_map in chunk_maps
        for image_id, rows in chunk_map.items()
    }


def _load_review_tag_writer_provenance(
    request: DatasetReviewRequest,
    image_ids: List[int],
) -> Dict[int, List[_StoredTagWriterProvenance]]:
    if ReviewIssueKind.METADATA_PROVENANCE_RISK not in set(request.issue_kinds):
        return {}
    raw_map = db.get_tag_writer_provenance_map(image_ids)
    parsed: Dict[int, List[_StoredTagWriterProvenance]] = {}
    for image_id, rows in raw_map.items():
        try:
            parsed[image_id] = [
                _StoredTagWriterProvenance.model_validate(row, strict=True)
                for row in rows
            ]
        except ValidationError as exc:
            raise ValueError(
                f"Invalid tag writer provenance row for image id={image_id}: {exc}"
            ) from exc
    return parsed


def _current_writer_rows(
    record: Optional[_StoredImage],
    writer_rows: List[_StoredTagWriterProvenance],
) -> List[_StoredTagWriterProvenance]:
    if record is None or record.content_fingerprint is None:
        return []
    return [
        writer
        for writer in writer_rows
        if hmac.compare_digest(writer.content_fingerprint, record.content_fingerprint)
    ]


def _current_writer_evidence(
    request: DatasetReviewRequest,
    image_ids: List[int],
    records: Dict[int, _StoredImage],
    writer_provenance_map: Dict[int, List[_StoredTagWriterProvenance]],
) -> List[_CurrentWriterEvidence]:
    if ReviewIssueKind.METADATA_PROVENANCE_RISK not in set(request.issue_kinds):
        return []
    evidence = [
        _CurrentWriterEvidence(
            image_id=image_id,
            writer_family=writer.writer_family,
            provider=writer.provider,
            model=writer.model,
            revision=writer.revision,
            runtime_provider=writer.runtime_provider,
            content_fingerprint=writer.content_fingerprint,
        )
        for image_id in image_ids
        for writer in _current_writer_rows(
            records.get(image_id),
            writer_provenance_map.get(image_id, []),
        )
    ]
    return sorted(
        evidence,
        key=lambda item: (
            item.image_id,
            item.writer_family,
            item.provider,
            item.model,
            item.revision,
            item.runtime_provider,
            item.content_fingerprint,
        ),
    )


def _model_asset_candidates(
    assets: _StoredModelAssets,
) -> List[_StoredModelAssetCandidate]:
    candidate_groups = (
        assets.checkpoint_candidates,
        assets.unet_candidates,
        assets.diffusion_model_candidates,
        assets.model_candidates,
        assets.lora_candidates,
        assets.vae_candidates,
        assets.clip_candidates,
        assets.yolo_candidates,
        assets.workflow_widget_lora_candidates,
        assets.global_lora_candidates,
        assets.global_yolo_candidates,
    )
    return [
        candidate
        for group in candidate_groups
        if group is not None
        for candidate in group
    ]


def _model_asset_provenance_evidence(
    record: _StoredProvenanceImage,
) -> List[ReviewEvidence]:
    if record.metadata_json is None:
        return [
            _localized_evidence(
                "Model asset provenance",
                "模型资产来源",
                "Missing",
                "缺失",
            )
        ]
    try:
        metadata_object = _MetadataJsonObject.model_validate_json(
            record.metadata_json,
            strict=True,
        )
    except ValidationError as exc:
        raise ValueError(
            f"Invalid metadata_json for image id={record.id}: expected a JSON object; {exc}"
        ) from exc

    try:
        metadata = _StoredMetadataEnvelope.model_validate(
            metadata_object.root,
            strict=True,
        )
    except ValidationError:
        return [
            _localized_evidence(
                "Model asset provenance",
                "模型资产来源",
                "Malformed",
                "格式错误",
            )
        ]

    if metadata.parsed is None:
        return [
            _localized_evidence(
                "Model asset provenance",
                "模型资产来源",
                "Missing",
                "缺失",
            )
        ]
    parsed = metadata.parsed
    if parsed.version is None:
        return [
            _localized_evidence(
                "Model asset provenance",
                "模型资产来源",
                "Malformed",
                "格式错误",
            )
        ]

    risks_en: List[str] = []
    risks_zh: List[str] = []
    if parsed.version < PARSED_METADATA_VERSION:
        risks_en.append(
            f"Parsed metadata version {parsed.version} is older than current version {PARSED_METADATA_VERSION}"
        )
        risks_zh.append(
            f"已解析元数据版本 {parsed.version} 早于当前版本 {PARSED_METADATA_VERSION}"
        )
    elif parsed.version > PARSED_METADATA_VERSION:
        risks_en.append(
            f"Parsed metadata version {parsed.version} is newer than current version {PARSED_METADATA_VERSION}"
        )
        risks_zh.append(
            f"已解析元数据版本 {parsed.version} 晚于当前版本 {PARSED_METADATA_VERSION}"
        )

    if parsed.model_assets is None:
        risks_en.append("Missing")
        risks_zh.append("缺失")
    else:
        candidates = _model_asset_candidates(parsed.model_assets)
        if not candidates:
            risks_en.append("Missing")
            risks_zh.append("缺失")
        non_explicit_count = sum(
            candidate.match_type
            not in {
                "explicit_metadata",
                "workflow_widget_value",
                "explicit_input",
                "serialized_field",
            }
            for candidate in candidates
        )
        weak_confidence_count = sum(
            candidate.confidence != "high"
            for candidate in candidates
        )
        fallback_source_modes = sorted(
            {
                candidate.source_mode
                for candidate in candidates
                if candidate.source_mode in _FALLBACK_MODEL_ASSET_SOURCE_MODES
            }
        )
        if non_explicit_count:
            risks_en.append(f"Non-explicit candidate: {non_explicit_count}")
            risks_zh.append(f"非显式候选项：{non_explicit_count}")
        if weak_confidence_count:
            risks_en.append(f"Below high confidence: {weak_confidence_count}")
            risks_zh.append(f"低于高置信度：{weak_confidence_count}")
        if fallback_source_modes:
            rendered_source_modes = ", ".join(fallback_source_modes)
            risks_en.append(f"Fallback source mode: {rendered_source_modes}")
            risks_zh.append(f"回退来源模式：{rendered_source_modes}")

    if not risks_en:
        return []
    return [
        _localized_evidence(
            "Model asset provenance",
            "模型资产来源",
            "; ".join(risks_en),
            "；".join(risks_zh),
        )
    ]


def _metadata_provenance_issues(
    request: DatasetReviewRequest,
    image_ids: List[int],
    records: Dict[int, _StoredImage],
    provenance_records: Dict[int, _StoredProvenanceImage],
    raw_tag_map: Dict[int, List[Dict[str, object]]],
    writer_provenance_map: Dict[int, List[_StoredTagWriterProvenance]],
) -> List[ReviewIssue]:
    if ReviewIssueKind.METADATA_PROVENANCE_RISK not in set(request.issue_kinds):
        return []

    parsed_tags: Dict[int, List[_StoredProvenanceTag]] = {}
    for image_id in image_ids:
        try:
            parsed_tags[image_id] = [
                _StoredProvenanceTag.model_validate(row, strict=True)
                for row in raw_tag_map.get(image_id, [])
            ]
        except ValidationError as exc:
            raise ValueError(
                f"Invalid metadata provenance tag row for image id={image_id}: {exc}"
            ) from exc

    issues: List[ReviewIssue] = []
    for image_id in image_ids:
        provenance_record = provenance_records.get(image_id)
        if provenance_record is None:
            continue
        evidence = _model_asset_provenance_evidence(provenance_record)
        caption_fields = [
            field_name
            for field_name, value in (
                ("ai_caption", provenance_record.ai_caption),
                ("nl_caption", provenance_record.nl_caption),
            )
            if value is not None and value.strip()
        ]
        if caption_fields:
            evidence.append(
                _localized_evidence(
                    "Unversioned caption fields",
                    "未版本化的 caption 字段",
                    ", ".join(caption_fields),
                    "、".join(caption_fields),
                )
            )
        unknown_tag_source_count = sum(
            tag.source is None
            or not tag.source.strip()
            or tag.source not in {"manual", "tagger", "vlm", "trigger"}
            for tag in parsed_tags.get(image_id, [])
        )
        if unknown_tag_source_count:
            evidence.append(
                _localized_evidence(
                    "Persisted tag source",
                    "已存标签来源",
                    f"{unknown_tag_source_count} legacy/unknown row(s)",
                    f"{unknown_tag_source_count} 条旧版/未知记录",
                )
            )
        tagger_tag_count = sum(
            tag.source == "tagger"
            for tag in parsed_tags.get(image_id, [])
        )
        writer_rows = writer_provenance_map.get(image_id, [])
        record = records.get(image_id)
        current_writer_rows = _current_writer_rows(record, writer_rows)
        if tagger_tag_count and not current_writer_rows and not writer_rows:
            evidence.append(
                _localized_evidence(
                    "Tag writer provenance",
                    "标签写入器来源",
                    "Legacy/unknown tag-writer identity",
                    "旧版/未知标签写入器身份",
                )
            )
        if tagger_tag_count and writer_rows and not current_writer_rows:
            fingerprint_detail_en = (
                "Current image fingerprint is unavailable"
                if record is None or record.content_fingerprint is None
                else "Writer evidence fingerprint does not match the current image"
            )
            fingerprint_detail_zh = (
                "当前图片指纹不可用"
                if record is None or record.content_fingerprint is None
                else "写入器证据指纹与当前图片不匹配"
            )
            evidence.append(
                _localized_evidence(
                    "Tag writer provenance",
                    "标签写入器来源",
                    fingerprint_detail_en,
                    fingerprint_detail_zh,
                )
            )
        if writer_rows and not tagger_tag_count:
            evidence.append(
                _localized_evidence(
                    "Tag writer provenance",
                    "标签写入器来源",
                    "Writer identity exists without current tagger rows",
                    "存在写入器身份，但当前没有对应 tagger 标签行",
                )
            )
        if len(writer_rows) > 1:
            evidence.append(
                _localized_evidence(
                    "Tag writer provenance",
                    "标签写入器来源",
                    f"Multiple WD14 writer records: {len(writer_rows)}",
                    f"多个 WD14 写入器记录：{len(writer_rows)}",
                )
            )
        if evidence and tagger_tag_count and current_writer_rows:
            writer = current_writer_rows[0]
            evidence.append(
                _localized_evidence(
                    "Tag writer identity",
                    "标签写入器身份",
                    (
                        f"{writer.provider} / {writer.model}; "
                        f"runtime={writer.runtime_provider}; revision={writer.revision}"
                    ),
                    (
                        f"{writer.provider} / {writer.model}；"
                        f"运行时={writer.runtime_provider}；版本={writer.revision}"
                    ),
                )
            )
        if not evidence:
            continue
        issues.append(
            _image_issue(
                image_id=image_id,
                record=records.get(image_id),
                kind=ReviewIssueKind.METADATA_PROVENANCE_RISK,
                severity="medium",
                title_en="Persisted metadata provenance needs review",
                title_zh="已存元数据来源需要审阅",
                detail_en="Persisted evidence does not explicitly attribute every derived field. Review provenance before export.",
                detail_zh="已存证据未明确标注每个派生字段的来源。请在导出前审阅来源信息。",
                evidence=evidence,
                source_provider="metadata_provenance",
                heuristic=False,
            )
        )
    return issues


def _stored_sidecar_fallback_state(
    record: _StoredProvenanceImage,
) -> Optional[_StoredSidecarFallbackState]:
    if record.metadata_json is None:
        return None
    try:
        metadata_object = _MetadataJsonObject.model_validate_json(
            record.metadata_json,
            strict=True,
        )
    except ValidationError as exc:
        raise ValueError(
            f"Invalid sidecar fallback metadata_json for image id={record.id}: "
            f"expected a JSON object; {exc}"
        ) from exc
    try:
        metadata = _StoredSidecarMetadataEnvelope.model_validate(
            metadata_object.root,
            strict=True,
        )
    except ValidationError as exc:
        raise ValueError(
            f"Invalid sidecar fallback evidence for image id={record.id}: {exc}"
        ) from exc
    if metadata.parsed is None or metadata.parsed.sidecar_fallback is None:
        return None
    if metadata.parsed.version is None or metadata.parsed.version <= 0:
        raise ValueError(
            f"Invalid sidecar fallback evidence for image id={record.id}: "
            "_parsed.version must be a positive integer"
        )
    state = metadata.parsed.sidecar_fallback
    mismatched_versions = sorted(
        {
            evidence.parser_version
            for evidence in state.evidence
            if evidence.parser_version != metadata.parsed.version
        }
    )
    if mismatched_versions:
        raise ValueError(
            f"Invalid sidecar fallback evidence for image id={record.id}: "
            f"evidence parser_version={mismatched_versions} does not match "
            f"_parsed.version={metadata.parsed.version}"
        )
    return state


def _sidecar_issue_evidence(
    entries: List[_StoredSidecarFallbackEvidence],
) -> List[ReviewEvidence]:
    rows: List[ReviewEvidence] = []
    multiple_entries = len(entries) > 1
    for index, entry in enumerate(entries, start=1):
        prefix_en = f"Sidecar {index} " if multiple_entries else "Sidecar "
        prefix_zh = f"Sidecar {index} " if multiple_entries else "Sidecar "
        rows.extend(
            [
                _localized_evidence(
                    f"{prefix_en}carrier",
                    f"{prefix_zh}载体",
                    entry.carrier.upper(),
                    entry.carrier.upper(),
                ),
                _evidence(
                    f"{prefix_en}file",
                    f"{prefix_zh}文件",
                    entry.basename,
                ),
                _localized_evidence(
                    f"{prefix_en}affected fields" if multiple_entries else "Affected fields",
                    f"{prefix_zh}受影响字段" if multiple_entries else "受影响字段",
                    ", ".join(entry.fields),
                    "、".join(entry.fields),
                ),
                _evidence(
                    f"{prefix_en}extraction method" if multiple_entries else "Extraction method",
                    f"{prefix_zh}提取方式" if multiple_entries else "提取方式",
                    entry.method,
                ),
                _evidence(
                    f"{prefix_en}confidence" if multiple_entries else "Confidence",
                    f"{prefix_zh}置信度" if multiple_entries else "置信度",
                    entry.confidence,
                ),
                _evidence(
                    f"{prefix_en}parser version" if multiple_entries else "Parser version",
                    f"{prefix_zh}解析器版本" if multiple_entries else "解析器版本",
                    entry.parser_version,
                ),
            ]
        )
    return rows


def _sidecar_metadata_dependency_issues(
    request: DatasetReviewRequest,
    image_ids: List[int],
    records: Dict[int, _StoredImage],
    provenance_records: Dict[int, _StoredProvenanceImage],
) -> Tuple[List[ReviewIssue], int]:
    if ReviewIssueKind.SIDECAR_METADATA_DEPENDENCY not in set(request.issue_kinds):
        return [], 0

    issues: List[ReviewIssue] = []
    unevaluated_count = 0
    for image_id in image_ids:
        provenance_record = provenance_records.get(image_id)
        if provenance_record is None:
            continue
        state = _stored_sidecar_fallback_state(provenance_record)
        if state is None:
            unevaluated_count += 1
            continue
        if not state.evidence:
            continue
        issues.append(
            _image_issue(
                image_id=image_id,
                record=records.get(image_id),
                kind=ReviewIssueKind.SIDECAR_METADATA_DEPENDENCY,
                severity="medium",
                title_en="Metadata depends on a sidecar fallback",
                title_zh="元数据依赖 Sidecar 回退",
                detail_en=(
                    "Stored extraction evidence shows that these fields became available "
                    "only after sidecar fallback at parse time. Keep the sidecar with the "
                    "source image or explicitly reparse after moving it."
                ),
                detail_zh=(
                    "已存提取证据表明，这些字段在解析时仅于 Sidecar 回退后出现。"
                    "请让 Sidecar 与源图片保持在一起，移动后应显式重新解析。"
                ),
                evidence=_sidecar_issue_evidence(state.evidence),
                source_provider="metadata_provenance",
                heuristic=False,
            )
        )
    return issues, unevaluated_count


def _partial_scope_reason(
    request: DatasetReviewRequest,
    database_count: int,
) -> Tuple[str, str]:
    unmaterialized_count = request.logical_count - database_count - request.local_path_count
    return (
        f"Review covers {database_count} database image(s) only; "
        f"{request.local_path_count} local-path item(s) and "
        f"{unmaterialized_count} other logical item(s) are not materialized.",
        f"当前仅审阅 {database_count} 张数据库图片；"
        f"{request.local_path_count} 个本地路径项目和 "
        f"{unmaterialized_count} 个其他逻辑项目尚未物化。",
    )


def _scope_provider_state(
    request: DatasetReviewRequest,
    database_count: int,
) -> ReviewProviderState:
    if request.logical_count > database_count:
        reason_en, reason_zh = _partial_scope_reason(request, database_count)
        return ReviewProviderState(
            provider="scope",
            status="partial",
            reason_en=reason_en,
            reason_zh=reason_zh,
            observed_at=None,
        )
    return ReviewProviderState(
        provider="scope",
        status="available",
        reason_en="Every logical item in this review is a loaded database image.",
        reason_zh="本次审阅中的每个逻辑项目都是已加载的数据库图片。",
        observed_at=None,
    )


def _file_provider_state(request: DatasetReviewRequest) -> ReviewProviderState:
    requested = bool(
        {
            ReviewIssueKind.FILE_MISSING,
            ReviewIssueKind.IMAGE_UNREADABLE,
        }
        & set(request.issue_kinds)
    )
    if not requested:
        return ReviewProviderState(
            provider="file_integrity",
            status="not_requested",
            reason_en="File integrity issues are excluded by the current issue filter.",
            reason_zh="当前问题筛选未包含文件完整性问题。",
            observed_at=None,
        )
    return ReviewProviderState(
        provider="file_integrity",
        status="available",
        reason_en="Stored readability and current file presence were checked for every database image.",
        reason_zh="已检查每张数据库图片的已存可读状态和当前文件是否存在。",
        observed_at=None,
    )


def _caption_provider_state(
    request: DatasetReviewRequest,
    database_count: int,
) -> ReviewProviderState:
    if ReviewIssueKind.EMPTY_CAPTION not in set(request.issue_kinds):
        return ReviewProviderState(
            provider="caption_integrity",
            status="not_requested",
            reason_en="Caption integrity issues are excluded by the current issue filter.",
            reason_zh="当前问题筛选未包含 caption 完整性问题。",
            observed_at=None,
        )
    if request.logical_count > database_count:
        reason_en, reason_zh = _partial_scope_reason(request, database_count)
        return ReviewProviderState(
            provider="caption_integrity",
            status="partial",
            reason_en=reason_en,
            reason_zh=reason_zh,
            observed_at=None,
        )
    return ReviewProviderState(
        provider="caption_integrity",
        status="available",
        reason_en="Current browser caption evidence covers every database image in scope.",
        reason_zh="当前浏览器 caption 证据覆盖范围内的每张数据库图片。",
        observed_at=None,
    )


def _tag_provider_state(request: DatasetReviewRequest) -> ReviewProviderState:
    requested_kinds = set(request.issue_kinds)
    if not requested_kinds.intersection(
        {
            ReviewIssueKind.RATING_CONFLICT,
            ReviewIssueKind.LOW_TAG_CONFIDENCE,
        }
    ):
        return ReviewProviderState(
            provider="tag_integrity",
            status="not_requested",
            reason_en="Tag integrity issues are excluded by the current issue filter.",
            reason_zh="当前问题筛选未包含标签完整性问题。",
            observed_at=None,
        )
    return ReviewProviderState(
        provider="tag_integrity",
        status="available",
        reason_en="Current persisted tag rows were checked for every database image without running inference.",
        reason_zh="已检查每张数据库图片的当前已存标签，没有启动推理。",
        observed_at=None,
    )


def _metadata_provenance_provider_state(
    request: DatasetReviewRequest,
    database_count: int,
    unevaluated_sidecar_count: int,
    records: Dict[int, _StoredImage],
    raw_tag_map: Dict[int, List[Dict[str, object]]],
    writer_provenance_map: Dict[int, List[_StoredTagWriterProvenance]],
) -> ReviewProviderState:
    requested_kinds = set(request.issue_kinds)
    provenance_requested = ReviewIssueKind.METADATA_PROVENANCE_RISK in requested_kinds
    sidecar_requested = ReviewIssueKind.SIDECAR_METADATA_DEPENDENCY in requested_kinds
    if not provenance_requested and not sidecar_requested:
        return ReviewProviderState(
            provider="metadata_provenance",
            status="not_requested",
            reason_en="Metadata provenance risks are excluded by the current issue filter.",
            reason_zh="当前问题筛选未包含元数据来源风险。",
            observed_at=None,
        )
    partial_reasons_en: List[str] = []
    partial_reasons_zh: List[str] = []
    if request.logical_count > database_count:
        scope_reason_en, scope_reason_zh = _partial_scope_reason(request, database_count)
        partial_reasons_en.append(scope_reason_en)
        partial_reasons_zh.append(scope_reason_zh)
    if sidecar_requested and unevaluated_sidecar_count:
        partial_reasons_en.append(
            "Sidecar fallback evidence is unevaluated for "
            f"{unevaluated_sidecar_count} image(s); no source was inferred."
        )
        partial_reasons_zh.append(
            f"{unevaluated_sidecar_count} 张图片尚未评估 Sidecar 回退来源；"
            "没有推断其来源。"
        )
    writer_reason_en = ""
    writer_reason_zh = ""
    if provenance_requested:
        tagger_image_ids = {
            image_id
            for image_id, rows in raw_tag_map.items()
            if any(row.get("source") == "tagger" for row in rows)
        }
        known_writer_ids = {
            image_id
            for image_id in tagger_image_ids
            if _current_writer_rows(
                records.get(image_id),
                writer_provenance_map.get(image_id, []),
            )
        }
        unknown_writer_count = len(tagger_image_ids - known_writer_ids)
        identity_labels = sorted(
            {
                f"{writer.provider}/{writer.model}; runtime={writer.runtime_provider}"
                for image_id in known_writer_ids
                for writer in _current_writer_rows(
                    records.get(image_id),
                    writer_provenance_map.get(image_id, []),
                )
            }
        )
        rendered_identities = "; ".join(identity_labels[:3])
        if len(identity_labels) > 3:
            rendered_identities += f"; +{len(identity_labels) - 3} more"
        writer_reason_en = (
            f"WD14 writer provenance is available for {len(known_writer_ids)} image(s); "
            f"{unknown_writer_count} tagger image(s) remain legacy/unknown."
        )
        writer_reason_zh = (
            f"WD14 写入器来源已覆盖 {len(known_writer_ids)} 张图片；"
            f"{unknown_writer_count} 张 tagger 图片仍为旧版/未知。"
        )
        if rendered_identities:
            writer_reason_en += f" Identities: {rendered_identities}."
            writer_reason_zh += f" 身份：{rendered_identities}。"
    if partial_reasons_en:
        if writer_reason_en:
            partial_reasons_en.append(writer_reason_en)
            partial_reasons_zh.append(writer_reason_zh)
        return ReviewProviderState(
            provider="metadata_provenance",
            status="partial",
            reason_en=" ".join(partial_reasons_en),
            reason_zh=" ".join(partial_reasons_zh),
            observed_at=None,
        )
    if sidecar_requested and not provenance_requested:
        return ReviewProviderState(
            provider="metadata_provenance",
            status="available",
            reason_en=(
                "Persisted sidecar fallback evidence was checked for every database "
                "image without reading source files or running inference."
            ),
            reason_zh=(
                "已检查每张数据库图片的已存 Sidecar 回退证据，"
                "没有读取源文件或运行推理。"
            ),
            observed_at=None,
        )
    if provenance_requested and not sidecar_requested:
        return ReviewProviderState(
            provider="metadata_provenance",
            status="available",
            reason_en=(
                "Persisted metadata, caption fields, and tag sources were checked "
                "without reading source files or running inference. "
                f"{writer_reason_en}"
            ),
            reason_zh=(
                "已检查已存元数据、caption 字段和标签来源，"
                f"没有读取源文件或运行推理。{writer_reason_zh}"
            ),
            observed_at=None,
        )
    return ReviewProviderState(
        provider="metadata_provenance",
        status="available",
        reason_en=(
            "Persisted metadata, caption fields, tag sources, and requested sidecar "
            "fallback evidence were checked without reading source files or running inference. "
            f"{writer_reason_en}"
        ),
        reason_zh=(
            "已检查已存元数据、caption 字段、标签来源和请求的 Sidecar 回退证据，"
            f"没有读取源文件或运行推理。{writer_reason_zh}"
        ),
        observed_at=None,
    )


def _dimension_provider_state(
    request: DatasetReviewRequest,
    unavailable_dimensions: int,
) -> ReviewProviderState:
    requested = (
        ReviewIssueKind.SMALL_IMAGE in set(request.issue_kinds)
        and request.minimum_dimension is not None
    )
    if not requested:
        return ReviewProviderState(
            provider="dimensions",
            status="not_requested",
            reason_en="Set a minimum side and include image-size issues to run this stored-data check.",
            reason_zh="设置最短边下限并包含图片尺寸问题后，才会执行这项已存数据检查。",
            observed_at=None,
        )
    if unavailable_dimensions:
        return ReviewProviderState(
            provider="dimensions",
            status="partial",
            reason_en=f"Stored dimensions are unavailable for {unavailable_dimensions} image(s).",
            reason_zh=f"{unavailable_dimensions} 张图片没有已存尺寸。",
            observed_at=None,
        )
    return ReviewProviderState(
        provider="dimensions",
        status="available",
        reason_en="Stored dimensions were checked for every database image.",
        reason_zh="已检查每张数据库图片的已存尺寸。",
        observed_at=None,
    )


def _aesthetic_provider_state(
    request: DatasetReviewRequest,
    unavailable_aesthetics: int,
) -> ReviewProviderState:
    requested = (
        ReviewIssueKind.LOW_AESTHETIC in set(request.issue_kinds)
        and request.minimum_aesthetic is not None
    )
    if not requested:
        return ReviewProviderState(
            provider="aesthetic_scores",
            status="not_requested",
            reason_en="Set a minimum stored aesthetic and include quality issues to run this check.",
            reason_zh="设置已存美学分下限并包含质量问题后，才会执行这项检查。",
            observed_at=None,
        )
    if unavailable_aesthetics:
        return ReviewProviderState(
            provider="aesthetic_scores",
            status="partial",
            reason_en=f"Stored aesthetic scores are unavailable for {unavailable_aesthetics} image(s).",
            reason_zh=f"{unavailable_aesthetics} 张图片没有已存美学评分。",
            observed_at=None,
        )
    return ReviewProviderState(
        provider="aesthetic_scores",
        status="available",
        reason_en="Stored aesthetic scores were checked without running inference.",
        reason_zh="已检查现有美学评分，没有启动推理。",
        observed_at=None,
    )


def _duplicate_state_unavailable(reason: Literal["missing", "invalid"]) -> ReviewProviderState:
    if reason == "missing":
        return ReviewProviderState(
            provider="persisted_duplicates",
            status="not_available",
            reason_en="A persisted duplicate scan has not been completed yet.",
            reason_zh="尚未完成可复用的重复图片扫描。",
            observed_at=None,
        )
    return ReviewProviderState(
        provider="persisted_duplicates",
        status="not_available",
        reason_en="The persisted duplicate scan state is invalid or incompatible. Run the scan again.",
        reason_zh="已保存的重复图片扫描状态无效或不兼容，请重新扫描。",
        observed_at=None,
    )


def _duplicate_issue(
    group: _DuplicateGroup,
    members: List[_DuplicateMember],
    records: Dict[int, _StoredImage],
    scanned_at: float,
) -> ReviewIssue:
    ordered_members = sorted(
        members,
        key=lambda member: not _image_can_open(records.get(member.id)),
    )
    member_ids = sorted(member.id for member in members)
    identity = _canonical_hash({"member_ids": member_ids})[:16]
    subjects = [
        ReviewSubject(
            image_id=member.id,
            filename=member.filename,
            source_path=member.path,
        )
        for member in ordered_members
    ]
    can_open = _image_can_open(records.get(ordered_members[0].id))
    return ReviewIssue(
        issue_id=f"duplicate_group:{identity}",
        kind=ReviewIssueKind.DUPLICATE_GROUP,
        severity="medium",
        title_en="Possible duplicate image group",
        title_zh="可能的重复图片组",
        detail_en="This advisory group comes from a historical CLIP scan without a current Dataset scope fingerprint. Review every member before acting.",
        detail_zh="该建议组来自历史 CLIP 扫描，缺少当前 Dataset 范围指纹。执行任何操作前请逐张确认。",
        subjects=subjects,
        evidence=[
            _evidence("Minimum group similarity", "组内最低相似度", f"{group.similarity:.4f}"),
            _evidence("Members in current scope", "当前范围内成员数", len(members)),
            _evidence("Historical scan time", "历史扫描时间", scanned_at),
        ],
        source_provider="persisted_duplicates",
        evidence_status="partial",
        heuristic=True,
        action=_open_action(
            can_open,
            "At least one group member can be opened in Dataset Maker."
            if can_open
            else "No group member can currently be opened.",
            "至少有一个组成员可在 Dataset Maker 中打开。"
            if can_open
            else "当前没有可打开的组成员。",
        ),
    )


def _persisted_duplicate_evidence(
    request: DatasetReviewRequest,
    scope_ids: set[int],
    records: Dict[int, _StoredImage],
) -> Tuple[List[ReviewIssue], ReviewProviderState]:
    requested = (
        request.include_persisted_duplicates
        and ReviewIssueKind.DUPLICATE_GROUP in set(request.issue_kinds)
    )
    if not requested:
        return [], ReviewProviderState(
            provider="persisted_duplicates",
            status="not_requested",
            reason_en="Persisted duplicate evidence was not requested.",
            reason_zh="本次未请求已保存的重复图片证据。",
            observed_at=None,
        )

    raw_state = duplicate_group_service.load_result()
    if raw_state is None:
        try:
            state_exists = duplicate_group_service._state_path().is_file()
        except OSError:
            state_exists = True
        return [], _duplicate_state_unavailable("invalid" if state_exists else "missing")
    try:
        state = _DuplicateState.model_validate(raw_state, strict=True)
    except ValidationError:
        return [], _duplicate_state_unavailable("invalid")

    issues: List[ReviewIssue] = []
    for group in state.groups:
        members = [member for member in group.members if member.id in scope_ids]
        if len(members) < 2:
            continue
        issues.append(_duplicate_issue(group, members, records, state.scanned_at))
    observed_at = datetime.fromtimestamp(state.scanned_at, tz=timezone.utc).isoformat()
    return issues, ReviewProviderState(
        provider="persisted_duplicates",
        status="partial",
        reason_en="Historical duplicate evidence is advisory because the scan has no current Dataset scope fingerprint.",
        reason_zh="历史重复图片证据缺少当前 Dataset 范围指纹，因此仅供参考。",
        observed_at=observed_at,
    )


def _issue_sort_key(issue: ReviewIssue) -> IssueSortKey:
    return (
        _SEVERITY_ORDER[issue.severity],
        _KIND_ORDER[issue.kind],
        issue.issue_id,
    )


def _encode_cursor(payload: _CursorPayload) -> str:
    raw = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.digest(_CURSOR_SIGNING_KEY, raw, "sha256")
    encoded_payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def _decode_cursor(value: str) -> _CursorPayload:
    try:
        encoded_payload, encoded_signature = value.split(".")
        payload_padding = "=" * (-len(encoded_payload) % 4)
        signature_padding = "=" * (-len(encoded_signature) % 4)
        raw = base64.b64decode(
            (encoded_payload + payload_padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        signature = base64.b64decode(
            (encoded_signature + signature_padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        expected_signature = hmac.digest(_CURSOR_SIGNING_KEY, raw, "sha256")
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Review cursor signature is invalid")
        decoded = json.loads(raw.decode("utf-8"))
        return _CursorPayload.model_validate(decoded, strict=True)
    except (
        binascii.Error,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid review cursor. Request the first page again with cursor=null.",
        ) from exc


def _validate_cursor(
    cursor: _CursorPayload,
    scope_fingerprint: str,
    filter_fingerprint: str,
    evidence_fingerprint: str,
) -> None:
    if cursor.scope_fingerprint != scope_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Review cursor scope changed. Request the first page again with cursor=null.",
        )
    if cursor.filter_fingerprint != filter_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Review cursor filters changed. Request the first page again with cursor=null.",
        )
    if cursor.evidence_fingerprint != evidence_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Review queue evidence changed. Request the first page again with cursor=null.",
        )


def _evidence_fingerprint(
    issues: List[ReviewIssue],
    provider_states: List[ReviewProviderState],
    current_writer_evidence: List[_CurrentWriterEvidence],
) -> str:
    return _canonical_hash(
        {
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "provider_states": [state.model_dump(mode="json") for state in provider_states],
            "current_writer_evidence": [
                evidence.model_dump(mode="json")
                for evidence in current_writer_evidence
            ],
        }
    )


def build_dataset_review_queue(request: DatasetReviewRequest) -> DatasetReviewResponse:
    """Materialize stored evidence, then return one stable keyset page."""
    image_ids = _unique_image_ids(request.image_ids)
    raw_records: Dict[int, Dict[str, object]] = db.get_images_by_ids(image_ids)
    records = _parse_database_records(raw_records)
    provenance_requested = bool(
        {
            ReviewIssueKind.METADATA_PROVENANCE_RISK,
            ReviewIssueKind.SIDECAR_METADATA_DEPENDENCY,
        }.intersection(request.issue_kinds)
    )
    provenance_records = (
        _parse_provenance_records(raw_records)
        if provenance_requested
        else {}
    )
    writer_provenance_map = _load_review_tag_writer_provenance(request, image_ids)
    database_issues, unavailable_dimensions, unavailable_aesthetics = _database_issues(
        request,
        image_ids,
        records,
    )
    caption_issues = _caption_issues(request, records)
    raw_tag_map = _load_review_tag_rows(request, image_ids)
    tag_issues = _tag_issues(request, image_ids, records, raw_tag_map)
    provenance_issues = _metadata_provenance_issues(
        request,
        image_ids,
        records,
        provenance_records,
        raw_tag_map,
        writer_provenance_map,
    )
    sidecar_issues, unevaluated_sidecar_count = _sidecar_metadata_dependency_issues(
        request,
        image_ids,
        records,
        provenance_records,
    )
    duplicate_issues, duplicate_state = _persisted_duplicate_evidence(
        request,
        set(image_ids),
        records,
    )
    issues = [
        *database_issues,
        *caption_issues,
        *tag_issues,
        *provenance_issues,
        *sidecar_issues,
        *duplicate_issues,
    ]
    issues.sort(key=_issue_sort_key)

    provider_states = [
        _scope_provider_state(request, len(records)),
        _file_provider_state(request),
        _caption_provider_state(request, len(records)),
        _tag_provider_state(request),
        _metadata_provenance_provider_state(
            request,
            len(records),
            unevaluated_sidecar_count,
            records,
            raw_tag_map,
            writer_provenance_map,
        ),
        _dimension_provider_state(request, unavailable_dimensions),
        _aesthetic_provider_state(request, unavailable_aesthetics),
        duplicate_state,
    ]
    scope_fingerprint = _scope_fingerprint(image_ids)
    filter_fingerprint = _filter_fingerprint(request)
    current_writer_evidence = _current_writer_evidence(
        request,
        image_ids,
        records,
        writer_provenance_map,
    )
    evidence_fingerprint = _evidence_fingerprint(
        issues,
        provider_states,
        current_writer_evidence,
    )

    start_index = 0
    if request.cursor is not None:
        cursor = _decode_cursor(request.cursor)
        _validate_cursor(
            cursor,
            scope_fingerprint,
            filter_fingerprint,
            evidence_fingerprint,
        )
        last_key = tuple(cursor.last_key)
        while start_index < len(issues) and _issue_sort_key(issues[start_index]) <= last_key:
            start_index += 1

    page = issues[start_index : start_index + request.limit]
    has_more = start_index + len(page) < len(issues)
    next_cursor: Optional[str] = None
    if has_more and page:
        next_cursor = _encode_cursor(
            _CursorPayload(
                version=REVIEW_SCHEMA_VERSION,
                scope_fingerprint=scope_fingerprint,
                filter_fingerprint=filter_fingerprint,
                evidence_fingerprint=evidence_fingerprint,
                last_key=list(_issue_sort_key(page[-1])),
            )
        )

    return DatasetReviewResponse(
        schema_version=REVIEW_SCHEMA_VERSION,
        scope_fingerprint=scope_fingerprint,
        issues=page,
        total=len(issues),
        has_more=has_more,
        next_cursor=next_cursor,
        provider_states=provider_states,
    )
