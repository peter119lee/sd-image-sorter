"""Strict transport models for durable Dataset Maker projects."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.dataset_export.models import (
    DatasetBucketResizeSettings,
    DatasetSubjectCropSettings,
    DatasetWatermarkRemovalSettings,
    disabled_bucket_resize_settings,
    disabled_subject_crop_settings,
    disabled_watermark_removal_settings,
)
from services.dataset_trigger import DatasetTrigger
from utils.path_validation import normalize_user_path


PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0)]
ProjectTag = Annotated[
    str,
    Field(min_length=1, max_length=500, pattern=r".*\S.*"),
]
ProjectReplaceRuleText = Annotated[
    str,
    Field(min_length=1, max_length=500, pattern=r".*\S.*"),
]
ProjectReplaceRuleValue = Annotated[str, Field(max_length=500)]
TrainerContractVersion = Annotated[
    str,
    Field(max_length=4096, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"),
]
_ALLOWED_MASK_EXPORTS_BY_TRAINER = {
    "none": frozenset({"none", "onetrainer", "kohya"}),
    "kohya_toml": frozenset({"none", "kohya"}),
    "anima_lora_toml": frozenset({"none", "anima_lora"}),
}
_FIXED_NUMERIC_TRAINERS = frozenset({"none", "anima_lora_toml"})


class DatasetProjectTemplateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template_override: str = Field(min_length=1, max_length=4096)
    replace_rules: dict[ProjectReplaceRuleText, ProjectReplaceRuleValue] = Field(
        max_length=1000
    )
    max_tags: NonNegativeStrictInt = Field(le=200)

    @field_validator("replace_rules")
    @classmethod
    def require_trimmed_replace_rules(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        if any(
            key != key.strip() or replacement != replacement.strip()
            for key, replacement in value.items()
        ):
            raise ValueError("replace_rules keys and values must already be trimmed")
        return value


class DatasetProjectCaptionRenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    trigger: DatasetTrigger
    common_tags: list[ProjectTag] = Field(max_length=1000)
    blacklist: list[ProjectTag] = Field(max_length=1000)
    normalize_tag_underscores: bool = Field(strict=True)
    content_mode: Literal["template"]
    prefix: str = Field(max_length=4096)
    template: DatasetProjectTemplateSettings

    @field_validator("common_tags", "blacklist")
    @classmethod
    def require_trimmed_tags(cls, value: list[str]) -> list[str]:
        if any(tag != tag.strip() for tag in value):
            raise ValueError("caption tag values must already be trimmed")
        return value


class DatasetProjectNamingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    preset: Literal["keep", "renumber", "custom"]
    custom_pattern: str = Field(min_length=1, max_length=4096)


class DatasetProjectOutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["folder", "beside_image"]
    folder: str = Field(max_length=4096)
    image_op: Literal["copy", "move"]
    overwrite_policy: Literal["unique", "overwrite", "skip"]


class DatasetProjectTrainerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    config: Literal["none", "kohya_toml", "anima_lora_toml"]
    contract_version: TrainerContractVersion | None
    mask_export: Literal["none", "onetrainer", "kohya", "anima_lora"]
    repeats: Annotated[int, Field(strict=True, ge=1, le=1000)]
    batch: Annotated[int, Field(strict=True, ge=1, le=64)]
    resolution: Annotated[int, Field(strict=True, ge=256, le=4096)]
    keep_tokens: Annotated[int, Field(strict=True, ge=0, le=50)]


class DatasetProjectPlanningSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    epochs: Annotated[int, Field(strict=True, ge=1, le=1000)]


class DatasetProjectSettingsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    settings_version: Literal[1]
    target_model: Literal["", "sdxl", "flux", "krea2", "anima"]
    caption_render: DatasetProjectCaptionRenderSettings
    naming: DatasetProjectNamingSettings
    output: DatasetProjectOutputSettings
    trainer: DatasetProjectTrainerSettings
    subject_crop: DatasetSubjectCropSettings = Field(
        default_factory=disabled_subject_crop_settings
    )
    bucket_resize: DatasetBucketResizeSettings = Field(
        default_factory=disabled_bucket_resize_settings
    )
    watermark_removal: DatasetWatermarkRemovalSettings = Field(
        default_factory=disabled_watermark_removal_settings
    )
    planning: DatasetProjectPlanningSettings

    @field_validator("settings_version", mode="before")
    @classmethod
    def require_integer_settings_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("settings_version must be integer 1")
        return value

    @model_validator(mode="after")
    def validate_trainer_contract(self) -> Self:
        trainer = self.trainer
        allowed_masks = _ALLOWED_MASK_EXPORTS_BY_TRAINER[trainer.config]
        if trainer.mask_export not in allowed_masks:
            expected_masks = ", ".join(sorted(allowed_masks))
            raise ValueError(
                f"trainer.mask_export must be one of {expected_masks} "
                f"when trainer.config is {trainer.config!r}"
            )
        fixed_numeric_settings = (
            trainer.config == "anima_lora_toml"
            or (trainer.config == "none" and not self.bucket_resize.enabled)
        )
        if fixed_numeric_settings and (
            trainer.resolution != 1024 or trainer.keep_tokens != 0
        ):
            raise ValueError(
                "trainer.resolution must be 1024 and trainer.keep_tokens must be 0 "
                f"when trainer.config is {trainer.config!r}"
            )
        if self.bucket_resize.enabled:
            if trainer.config != "none":
                raise ValueError(
                    "bucket_resize is not supported by verified trainer packages"
                )
            if trainer.resolution % 64 != 0:
                raise ValueError(
                    "trainer.resolution must be a multiple of 64 when bucket_resize is enabled"
                )
            if self.output.mode != "folder" or self.output.image_op != "copy":
                raise ValueError(
                    "bucket_resize requires output.mode='folder' and output.image_op='copy'"
                )
        if self.watermark_removal.enabled:
            if trainer.config != "none":
                raise ValueError(
                    "watermark_removal is not supported by verified trainer packages"
                )
            if self.output.mode != "folder" or self.output.image_op != "copy":
                raise ValueError(
                    "watermark_removal requires output.mode='folder' and output.image_op='copy'"
                )
        if trainer.config == "none":
            if trainer.contract_version is not None:
                raise ValueError(
                    "trainer.contract_version must be null when trainer.config is 'none'"
                )
            return self

        if trainer.contract_version is None:
            raise ValueError(
                "trainer.contract_version must be a semantic version for the selected trainer"
            )
        if self.output.mode != "folder" or self.output.image_op != "copy":
            raise ValueError(
                "verified trainers require output.mode='folder' and output.image_op='copy'"
            )
        return self


class DatasetProjectLibraryItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    item_type: Literal["library"]
    image_id: PositiveStrictInt


class DatasetProjectLocalItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    item_type: Literal["local"]
    path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path must contain a non-whitespace character")
        if not Path(normalize_user_path(value)).is_absolute():
            raise ValueError("path must be absolute")
        return value


DatasetProjectItemRequest = Annotated[
    DatasetProjectLibraryItemRequest | DatasetProjectLocalItemRequest,
    Field(discriminator="item_type"),
]


class DatasetProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=200)
    items: list[DatasetProjectItemRequest]
    settings: DatasetProjectSettingsV1

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must contain a non-whitespace character")
        return normalized

    @model_validator(mode="after")
    def require_distinct_library_images(self) -> "DatasetProjectCreateRequest":
        image_ids = [
            item.image_id
            for item in self.items
            if isinstance(item, DatasetProjectLibraryItemRequest)
        ]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("Library image items must not contain duplicates")
        return self

class DatasetProjectUpdateRequest(DatasetProjectCreateRequest):
    expected_revision: PositiveStrictInt


class DatasetProjectRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: PositiveStrictInt


class DatasetProjectLibraryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    position: NonNegativeStrictInt
    item_type: Literal["library"]
    source_image_id: PositiveStrictInt
    image_id: PositiveStrictInt | None
    missing: bool


class DatasetProjectCaptionDialectAdvisory(BaseModel):
    """Why this item's caption format needs attention for the project's target.

    Purely advisory. It reports; it never blocks a save or an export, and the
    caption text beside it is always the untouched original — the marker exists
    to decide how text is presented or converted, never to withhold it.
    ``convert`` is ``None`` for a hybrid caption because NL+tag captions are
    deliberate for some trainers, so no direction may be asserted.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal["caption_dialect_mismatch", "caption_dialect_partial"]
    target_model: Literal["krea2", "anima"]
    expected_dialect: Literal["tags", "natural"]
    caption_format: Literal["tags", "natural", "mixed"]
    convert: Literal["tags_to_natural", "natural_to_tags"] | None
    message: str
    action: str


class DatasetProjectLocalItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    position: NonNegativeStrictInt
    item_type: Literal["local"]
    ds_id: str = Field(pattern=r"^ds:[0-9a-f]{16}$")
    path: str
    size: NonNegativeStrictInt
    mtime_ns: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    device: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    inode: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    source_status: Literal["available", "missing", "changed"]
    sidecar_caption: str | None
    # Format of ``sidecar_caption``, derived from the text read off disk because
    # a local item has no database row. ``None`` means "no caption text";
    # ``"unknown"`` means "there is text and the classifier declined to guess".
    sidecar_caption_format: Literal["tags", "natural", "mixed", "unknown"] | None
    caption_dialect: DatasetProjectCaptionDialectAdvisory | None


DatasetProjectItemResponse = Annotated[
    DatasetProjectLibraryItemResponse | DatasetProjectLocalItemResponse,
    Field(discriminator="item_type"),
]


class DatasetProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: PositiveStrictInt
    name: str
    revision: PositiveStrictInt
    archived_at: str | None
    created_at: str
    updated_at: str
    missing_image_ids: list[PositiveStrictInt]
    items: list[DatasetProjectItemResponse]
    settings: DatasetProjectSettingsV1


class DatasetProjectSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: PositiveStrictInt
    name: str
    revision: PositiveStrictInt
    archived_at: str | None
    created_at: str
    updated_at: str
    item_count: NonNegativeStrictInt
    missing_image_count: NonNegativeStrictInt


class DatasetProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    projects: list[DatasetProjectSummaryResponse]


class DatasetProjectDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    deleted: bool
    project_id: PositiveStrictInt
