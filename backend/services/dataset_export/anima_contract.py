"""Strict contract and TOML writer for the pinned Anima LoRA trainer."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_export.models import DatasetExportRequest, DatasetPackageOptions
from services.tag_export.captions import VALID_CONTENT_MODES
from utils.atomic_staging import create_staging_sibling, publish_staging_file


ANIMA_CONTRACT_VERSION = "1.0.0"
ANIMA_UPSTREAM_REPOSITORY = "https://github.com/sorryhyun/anima_lora"
ANIMA_UPSTREAM_TAG = "v1.14.2.hotfix"
ANIMA_UPSTREAM_COMMIT = "13eaf97a3903405baa939d7cb4a524f8f3e11303"


class AnimaTrainerContractError(ValueError):
    """Raised when Anima options, TOML, or artifacts violate the contract."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AnimaUpstreamPin(_StrictModel):
    repository: Literal["https://github.com/sorryhyun/anima_lora"]
    tag: Literal["v1.14.2.hotfix"]
    commit: Literal["13eaf97a3903405baa939d7cb4a524f8f3e11303"]
    license: Literal["MIT"]
    python_requirement: Literal["==3.13.*"]


class AnimaCapabilities(_StrictModel):
    caption_extensions: Tuple[Literal[".txt"], ...]
    separate_loss_masks: bool
    loss_mask_suffix: Literal["_mask.png"]
    class_tokens_behavior: Literal["forbidden"]


class AnimaIntegerBounds(_StrictModel):
    minimum: int
    maximum: int
    default: int


class AnimaOptionBounds(_StrictModel):
    repeats: AnimaIntegerBounds
    batch_size: AnimaIntegerBounds
    resolution: AnimaIntegerBounds
    keep_tokens: AnimaIntegerBounds


class AnimaGeneratedArtifacts(_StrictModel):
    dataset_config: Literal["dataset_config.toml"]
    caption_sidecar: Literal["<image-stem>.txt"]
    loss_mask: Literal["<relative-path>/<image-stem>_mask.png"]
    mask_directory: Literal["mask"]


class AnimaVerificationBoundary(_StrictModel):
    module: Literal["library.config.loader"]
    required_flags: Tuple[Literal["--support_dropout"], ...]
    validates_upstream_schema: bool
    validates_artifact_completeness: bool
    requires_module_path_match: Literal[True]
    artifact_completeness_gate: Literal[
        "all_captions_and_requested_masks_before_generation"
    ]
    starts_training: bool


class AnimaTrainerContract(_StrictModel):
    id: Literal["anima_lora"]
    display_name: Literal["Anima LoRA"]
    wire_value: Literal["anima_lora_toml"]
    contract_version: Literal["1.0.0"]
    verified: Literal[True]
    mask_export_modes: Tuple[Literal["none"], Literal["anima_lora"]]
    upstream: AnimaUpstreamPin
    capabilities: AnimaCapabilities
    option_bounds: AnimaOptionBounds
    generated_artifacts: AnimaGeneratedArtifacts
    verification_boundary: AnimaVerificationBoundary


class AnimaDatasetConfigOptions(_StrictModel):
    image_dir: Path
    caption_extension: Literal[".txt"]
    num_repeats: int = Field(ge=1, le=1000)
    batch_size: int = Field(ge=1, le=64)
    mask_dir: Optional[Path]


class AnimaArtifactCompleteness(_StrictModel):
    image_count: int = Field(ge=1)
    caption_count: int = Field(ge=1)
    mask_count: int = Field(ge=0)


_ANIMA_CONTRACT = AnimaTrainerContract(
    id="anima_lora",
    display_name="Anima LoRA",
    wire_value="anima_lora_toml",
    contract_version=ANIMA_CONTRACT_VERSION,
    verified=True,
    mask_export_modes=("none", "anima_lora"),
    upstream=AnimaUpstreamPin(
        repository=ANIMA_UPSTREAM_REPOSITORY,
        tag=ANIMA_UPSTREAM_TAG,
        commit=ANIMA_UPSTREAM_COMMIT,
        license="MIT",
        python_requirement="==3.13.*",
    ),
    capabilities=AnimaCapabilities(
        caption_extensions=(".txt",),
        separate_loss_masks=True,
        loss_mask_suffix="_mask.png",
        class_tokens_behavior="forbidden",
    ),
    option_bounds=AnimaOptionBounds(
        repeats=AnimaIntegerBounds(minimum=1, maximum=1000, default=10),
        batch_size=AnimaIntegerBounds(minimum=1, maximum=64, default=2),
        resolution=AnimaIntegerBounds(minimum=1024, maximum=1024, default=1024),
        keep_tokens=AnimaIntegerBounds(minimum=0, maximum=0, default=0),
    ),
    generated_artifacts=AnimaGeneratedArtifacts(
        dataset_config="dataset_config.toml",
        caption_sidecar="<image-stem>.txt",
        loss_mask="<relative-path>/<image-stem>_mask.png",
        mask_directory="mask",
    ),
    verification_boundary=AnimaVerificationBoundary(
        module="library.config.loader",
        required_flags=("--support_dropout",),
        validates_upstream_schema=True,
        validates_artifact_completeness=False,
        requires_module_path_match=True,
        artifact_completeness_gate=(
            "all_captions_and_requested_masks_before_generation"
        ),
        starts_training=False,
    ),
)

_IMAGE_EXTENSIONS = frozenset(str(extension).lower() for extension in ALLOWED_IMAGE_EXTENSIONS)


def get_anima_trainer_contract() -> AnimaTrainerContract:
    return _ANIMA_CONTRACT


def _validate_anima_option_values(
    content_mode: str,
    caption_extension: str,
    trainer_keep_tokens: int,
    trainer_resolution: int,
    mask_export: str,
) -> None:
    normalized_content_mode = content_mode.strip().lower()
    if normalized_content_mode not in VALID_CONTENT_MODES:
        raise AnimaTrainerContractError(
            "Anima contract received an unsupported content_mode: "
            f"content_mode={content_mode!r}"
        )
    if normalized_content_mode == "json" or caption_extension != ".txt":
        raise AnimaTrainerContractError(
            "Anima contract requires text captions with caption_extension='.txt'"
        )
    if trainer_keep_tokens != 0:
        raise AnimaTrainerContractError(
            "Anima contract requires trainer_keep_tokens=0; caption shuffling and "
            "keep_tokens are not part of this contract"
        )
    if trainer_resolution != 1024:
        raise AnimaTrainerContractError(
            "Anima contract requires trainer_resolution=1024 because resolution is "
            "owned by the pinned trainer and is not emitted in dataset TOML"
        )
    mask_mode = mask_export.strip().lower()
    if mask_mode not in {"none", "anima_lora"}:
        raise AnimaTrainerContractError(
            "Anima contract requires mask_export='none' or 'anima_lora'; "
            f"received={mask_export!r}"
        )


def validate_anima_request(request: DatasetExportRequest) -> None:
    if str(request.trainer_config).strip().lower() != "anima_lora_toml":
        raise AnimaTrainerContractError(
            "Anima contract requires trainer_config='anima_lora_toml'"
        )
    content_mode = str(request.content_mode).strip().lower()
    _validate_anima_option_values(
        content_mode,
        ".json" if content_mode == "json" else ".txt",
        request.trainer_keep_tokens,
        request.trainer_resolution,
        str(request.mask_export),
    )


def validate_anima_package_options(options: DatasetPackageOptions) -> None:
    _validate_anima_option_values(
        options.content_mode,
        options.caption_extension,
        options.trainer_keep_tokens,
        options.trainer_resolution,
        options.mask_export,
    )


def _require_nonnegative_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnimaTrainerContractError(
            f"Anima contract requires {field_name} to be a non-negative integer"
        )
    return value


def _toml_path_literal(path: Path) -> str:
    return str(path).replace("\\", "/")


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _reject_extra_keys(
    data: Mapping[str, object],
    allowed: set[str],
    path: str,
) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise AnimaTrainerContractError(
            f"Anima TOML contains unsupported fields at {path}: {', '.join(extra)}"
        )


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise AnimaTrainerContractError(
            f"Anima TOML failed strict option validation: {field_name} must be a string"
        )
    return value


def validate_anima_toml_text(content: str) -> AnimaDatasetConfigOptions:
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise AnimaTrainerContractError(f"Anima TOML is not parseable: {exc}") from exc
    _reject_extra_keys(document, {"datasets"}, "root")
    datasets = document.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1:
        raise AnimaTrainerContractError(
            "Anima TOML must contain exactly one datasets table"
        )
    dataset = datasets[0]
    if not isinstance(dataset, dict):
        raise AnimaTrainerContractError("Anima TOML datasets[0] must be a table")
    _reject_extra_keys(dataset, {"batch_size", "subsets"}, "datasets[0]")
    subsets = dataset.get("subsets")
    if not isinstance(subsets, list) or len(subsets) != 1:
        raise AnimaTrainerContractError(
            "Anima TOML must contain exactly one datasets[0].subsets table"
        )
    subset = subsets[0]
    if not isinstance(subset, dict):
        raise AnimaTrainerContractError(
            "Anima TOML datasets[0].subsets[0] must be a table"
        )
    _reject_extra_keys(
        subset,
        {"image_dir", "caption_extension", "num_repeats", "mask_dir"},
        "datasets[0].subsets[0]",
    )
    try:
        mask_value = subset.get("mask_dir")
        options = AnimaDatasetConfigOptions(
            image_dir=Path(_require_string(subset["image_dir"], "image_dir")),
            caption_extension=subset["caption_extension"],
            num_repeats=subset["num_repeats"],
            batch_size=dataset["batch_size"],
            mask_dir=(
                Path(_require_string(mask_value, "mask_dir"))
                if mask_value is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise AnimaTrainerContractError(
            f"Anima TOML failed strict option validation: {exc}"
        ) from exc
    return options


def render_anima_dataset_config(options: AnimaDatasetConfigOptions) -> str:
    lines = [
        "# Generated by SD Image Sorter for anima_lora v1.14.2.hotfix.",
        "# Artifact completeness is validated before this config is written.",
        "[[datasets]]",
        f"batch_size = {options.batch_size}",
        "",
        "  [[datasets.subsets]]",
        f"  image_dir = {_toml_basic_string(_toml_path_literal(options.image_dir))}",
        f"  caption_extension = {_toml_basic_string(options.caption_extension)}",
        f"  num_repeats = {options.num_repeats}",
    ]
    if options.mask_dir is not None:
        lines.append(
            f"  mask_dir = {_toml_basic_string(_toml_path_literal(options.mask_dir))}"
        )
    content = "\n".join((*lines, ""))
    validate_anima_toml_text(content)
    return content


def _training_images(image_dir: Path) -> Tuple[Path, ...]:
    if not image_dir.exists():
        raise AnimaTrainerContractError(
            f"Anima artifact image directory does not exist: image_dir={image_dir}"
        )
    if not image_dir.is_dir():
        raise AnimaTrainerContractError(
            f"Anima artifact image path is not a directory: image_dir={image_dir}"
        )
    try:
        images = tuple(
            sorted(
                (
                    entry
                    for entry in image_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() in _IMAGE_EXTENSIONS
                ),
                key=lambda entry: entry.name.casefold(),
            )
        )
    except OSError as exc:
        raise AnimaTrainerContractError(
            "Anima artifact image directory could not be read: "
            f"image_dir={image_dir}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    if not images:
        raise AnimaTrainerContractError(
            f"Anima artifact completeness found no training images: image_dir={image_dir}"
        )
    return images


def _require_unique_stems(images: Tuple[Path, ...]) -> None:
    seen: dict[str, Path] = {}
    for image in images:
        key = image.stem.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise AnimaTrainerContractError(
                "Anima artifact completeness found a duplicate image stem: "
                f"stem={image.stem!r}, first={previous}, second={image}"
            )
        seen[key] = image


def _require_caption(image: Path, caption_extension: str) -> None:
    caption = image.with_suffix(caption_extension)
    if not caption.is_file():
        raise AnimaTrainerContractError(
            "Anima artifact caption is missing: "
            f"image={image}, expected_caption={caption}"
        )
    try:
        content = caption.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AnimaTrainerContractError(
            "Anima artifact caption could not be read as UTF-8: "
            f"caption={caption}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    if not content.strip():
        raise AnimaTrainerContractError(
            f"Anima artifact caption is empty: image={image}, caption={caption}"
        )


def _require_loss_mask(image: Path, image_dir: Path, mask_dir: Path) -> Path:
    relative_parent = image.relative_to(image_dir).parent
    relative_mask = mask_dir / relative_parent / f"{image.stem}_mask.png"
    if relative_mask.is_file():
        return relative_mask
    raise AnimaTrainerContractError(
        "Anima loss masks require mask_dir/<relative-path>/<image-stem>_mask.png "
        "for every image: "
        f"image={image}, expected_mask={relative_mask}"
    )


def validate_anima_artifact_completeness(
    options: AnimaDatasetConfigOptions,
) -> AnimaArtifactCompleteness:
    images = _training_images(options.image_dir)
    _require_unique_stems(images)
    masks: list[Path] = []
    for image in images:
        _require_caption(image, options.caption_extension)
        if options.mask_dir is not None:
            masks.append(_require_loss_mask(image, options.image_dir, options.mask_dir))
    return AnimaArtifactCompleteness(
        image_count=len(images),
        caption_count=len(images),
        mask_count=len(masks),
    )


def _options_from_request(
    output_folder: Path,
    request: DatasetExportRequest,
    *,
    masks_written: object,
    masks_missing: object,
) -> tuple[AnimaDatasetConfigOptions, int, int]:
    validate_anima_request(request)
    written = _require_nonnegative_integer(masks_written, "masks_written")
    missing = _require_nonnegative_integer(masks_missing, "masks_missing")
    mask_mode = str(request.mask_export).strip().lower()
    if mask_mode == "none" and (written != 0 or missing != 0):
        raise AnimaTrainerContractError(
            "Anima mask counts must be zero when mask_export='none': "
            f"masks_written={written}, masks_missing={missing}"
        )
    if mask_mode == "anima_lora" and missing > 0:
        raise AnimaTrainerContractError(
            "Anima loss masks require a loss mask for every exported image: "
            f"masks_written={written}, masks_missing={missing}"
        )
    try:
        options = AnimaDatasetConfigOptions(
            image_dir=output_folder,
            caption_extension=".txt",
            num_repeats=request.trainer_repeats,
            batch_size=request.trainer_batch,
            mask_dir=(output_folder / "mask" if mask_mode == "anima_lora" else None),
        )
    except ValidationError as exc:
        raise AnimaTrainerContractError(
            f"Anima dataset options failed strict validation: {exc}"
        ) from exc
    return options, written, missing


def _write_anima_config_atomically(target: Path, content: str) -> None:
    """Stage the config beside its target, then publish it over the target.

    Both halves come from ``utils.atomic_staging``. ``tempfile`` cannot stage
    here: it read an unwritable destination folder's refusal as a name collision
    and retried it up to ``tempfile.TMP_MAX`` — measured at 2,147,483,647 on this
    interpreter, not the 10,000 the docs imply — so a config write into a folder
    the process cannot write to hung instead of reporting the refusal. Publishing
    is shared for the same reason as the dataset row writer — a bare
    ``os.replace`` severs a hard link on the destination. A hardlinked target is
    not reachable through the export today, because
    ``artifacts._invalidate_existing_anima_config`` moves any existing
    ``dataset_config.toml`` aside before this runs; sharing the publish path
    keeps a future caller from reintroducing the hazard.
    """
    temporary_path: Path | None = None
    try:
        temporary_path, descriptor = create_staging_sibling(target)
        os.close(descriptor)
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    "short write while preparing Anima dataset config: "
                    f"expected_characters={len(content)}, written_characters={written}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        publish_staging_file(temporary_path, target)
    except OSError as exc:
        cleanup_error: OSError | None = None
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as unlink_error:
                cleanup_error = unlink_error
        cleanup_detail = (
            "; temporary_cleanup_error_type="
            f"{type(cleanup_error).__name__}, temporary_cleanup_error={cleanup_error}"
            if cleanup_error is not None
            else ""
        )
        raise AnimaTrainerContractError(
            "Anima dataset config could not be written atomically: "
            f"target={target}, temporary_path={temporary_path}, "
            f"error_type={type(exc).__name__}, error={exc}{cleanup_detail}"
        ) from exc


def write_anima_dataset_config(
    output_folder: Path,
    request: DatasetExportRequest,
    *,
    masks_written: object,
    masks_missing: object,
) -> str:
    options, written, _missing = _options_from_request(
        output_folder,
        request,
        masks_written=masks_written,
        masks_missing=masks_missing,
    )
    completeness = validate_anima_artifact_completeness(options)
    if written != completeness.mask_count:
        raise AnimaTrainerContractError(
            "Anima artifact mask count mismatch: "
            f"reported_masks_written={written}, actual_masks={completeness.mask_count}, "
            f"image_count={completeness.image_count}, mask_dir={options.mask_dir}"
        )
    content = render_anima_dataset_config(options)
    target = output_folder / "dataset_config.toml"
    _write_anima_config_atomically(target, content)
    return str(target)


__all__: List[str] = [
    "ANIMA_CONTRACT_VERSION",
    "ANIMA_UPSTREAM_COMMIT",
    "ANIMA_UPSTREAM_REPOSITORY",
    "ANIMA_UPSTREAM_TAG",
    "AnimaArtifactCompleteness",
    "AnimaDatasetConfigOptions",
    "AnimaTrainerContract",
    "AnimaTrainerContractError",
    "get_anima_trainer_contract",
    "render_anima_dataset_config",
    "validate_anima_artifact_completeness",
    "validate_anima_package_options",
    "validate_anima_request",
    "validate_anima_toml_text",
    "write_anima_dataset_config",
]
