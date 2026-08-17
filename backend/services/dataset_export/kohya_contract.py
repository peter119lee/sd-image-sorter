"""Strict Kohya sd-scripts dataset contract and TOML writer."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.dataset_export.models import DatasetExportRequest, DatasetPackageOptions
from services.tag_export.captions import VALID_CONTENT_MODES
from utils.atomic_staging import create_staging_sibling, publish_staging_file


KOHYA_CONTRACT_VERSION = "1.0.0"
KOHYA_UPSTREAM_REPOSITORY = "https://github.com/kohya-ss/sd-scripts"
KOHYA_UPSTREAM_TAG = "v0.11.1"
KOHYA_UPSTREAM_COMMIT = "6721028c79ee85a78b3a06dfd8954dae310a1cce"


class KohyaTrainerContractError(ValueError):
    """Raised when a request or TOML document violates the pinned contract."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class KohyaUpstreamPin(_StrictModel):
    repository: Literal["https://github.com/kohya-ss/sd-scripts"]
    tag: Literal["v0.11.1"]
    commit: Literal["6721028c79ee85a78b3a06dfd8954dae310a1cce"]


class KohyaCapabilities(_StrictModel):
    caption_extensions: Tuple[Literal[".txt"], ...]
    bucketed_training: bool
    caption_shuffle_keep_tokens: bool
    conditioning_masks: bool
    conditioning_training_args: Tuple[Literal["--masked_loss"], ...]
    class_tokens_behavior: Literal["caption_fallback_only"]


class KohyaIntegerBounds(_StrictModel):
    minimum: int
    maximum: int
    default: int


class KohyaOptionBounds(_StrictModel):
    repeats: KohyaIntegerBounds
    batch_size: KohyaIntegerBounds
    resolution: KohyaIntegerBounds
    keep_tokens: KohyaIntegerBounds


class KohyaGeneratedArtifacts(_StrictModel):
    dataset_config: Literal["dataset_config.toml"]
    caption_sidecar: Literal["<image-stem>.txt"]
    conditioning_directory: Literal["mask"]


class KohyaVerificationBoundary(_StrictModel):
    module: Literal["library.config_util"]
    required_flags: Tuple[
        Literal["--support_dreambooth"],
        Literal["--support_finetuning"],
        Literal["--support_dropout"],
    ]
    conditioning_flag: Literal["--support_controlnet"]
    validates_upstream_schema: bool
    validates_artifact_completeness: bool
    requires_module_path_match: Literal[True]
    artifact_completeness_gate: Literal[
        "all_conditioning_files_before_generation"
    ]
    starts_training: bool


class KohyaTrainerContract(_StrictModel):
    id: Literal["kohya_sd_scripts"]
    display_name: Literal["Kohya sd-scripts"]
    wire_value: Literal["kohya_toml"]
    contract_version: Literal["1.0.0"]
    verified: Literal[True]
    mask_export_modes: Tuple[Literal["none"], Literal["kohya"]]
    upstream: KohyaUpstreamPin
    capabilities: KohyaCapabilities
    option_bounds: KohyaOptionBounds
    generated_artifacts: KohyaGeneratedArtifacts
    verification_boundary: KohyaVerificationBoundary


class KohyaTrainerContractsResponse(_StrictModel):
    trainers: Tuple[KohyaTrainerContract, ...]


class KohyaDatasetConfigOptions(_StrictModel):
    image_dir: Path
    caption_extension: Literal[".txt"]
    num_repeats: int = Field(ge=1, le=1000)
    batch_size: int = Field(ge=1, le=64)
    resolution: int = Field(ge=256, le=4096)
    keep_tokens: int = Field(ge=0, le=50)
    class_tokens: str = Field(max_length=100)
    conditioning_data_dir: Optional[Path]


_KOHYA_CONTRACT = KohyaTrainerContract(
    id="kohya_sd_scripts",
    display_name="Kohya sd-scripts",
    wire_value="kohya_toml",
    contract_version=KOHYA_CONTRACT_VERSION,
    verified=True,
    mask_export_modes=("none", "kohya"),
    upstream=KohyaUpstreamPin(
        repository=KOHYA_UPSTREAM_REPOSITORY,
        tag=KOHYA_UPSTREAM_TAG,
        commit=KOHYA_UPSTREAM_COMMIT,
    ),
    capabilities=KohyaCapabilities(
        caption_extensions=(".txt",),
        bucketed_training=True,
        caption_shuffle_keep_tokens=True,
        conditioning_masks=True,
        conditioning_training_args=("--masked_loss",),
        class_tokens_behavior="caption_fallback_only",
    ),
    option_bounds=KohyaOptionBounds(
        repeats=KohyaIntegerBounds(minimum=1, maximum=1000, default=10),
        batch_size=KohyaIntegerBounds(minimum=1, maximum=64, default=2),
        resolution=KohyaIntegerBounds(minimum=256, maximum=4096, default=1024),
        keep_tokens=KohyaIntegerBounds(minimum=0, maximum=50, default=0),
    ),
    generated_artifacts=KohyaGeneratedArtifacts(
        dataset_config="dataset_config.toml",
        caption_sidecar="<image-stem>.txt",
        conditioning_directory="mask",
    ),
    verification_boundary=KohyaVerificationBoundary(
        module="library.config_util",
        required_flags=(
            "--support_dreambooth",
            "--support_finetuning",
            "--support_dropout",
        ),
        conditioning_flag="--support_controlnet",
        validates_upstream_schema=True,
        validates_artifact_completeness=False,
        requires_module_path_match=True,
        artifact_completeness_gate="all_conditioning_files_before_generation",
        starts_training=False,
    ),
)


def get_kohya_trainer_contract() -> KohyaTrainerContract:
    return _KOHYA_CONTRACT


def get_kohya_trainer_contracts_response() -> KohyaTrainerContractsResponse:
    return KohyaTrainerContractsResponse(trainers=(_KOHYA_CONTRACT,))


def _validate_kohya_option_values(
    content_mode: str,
    caption_extension: str,
    mask_export: str,
) -> None:
    normalized_content_mode = content_mode.strip().lower()
    if normalized_content_mode not in VALID_CONTENT_MODES:
        raise KohyaTrainerContractError(
            "Kohya contract received an unsupported content_mode: "
            f"content_mode={content_mode!r}"
        )
    if normalized_content_mode == "json" or caption_extension != ".txt":
        raise KohyaTrainerContractError(
            "Kohya contract requires text captions with caption_extension='.txt'"
        )
    if mask_export.strip().lower() not in {"none", "kohya"}:
        raise KohyaTrainerContractError(
            "Kohya contract requires mask_export='none' or 'kohya'; "
            f"received={mask_export!r}"
        )


def validate_kohya_request(request: DatasetExportRequest) -> None:
    if str(request.trainer_config).strip().lower() != "kohya_toml":
        raise KohyaTrainerContractError(
            "Kohya contract requires trainer_config='kohya_toml'"
        )
    content_mode = str(request.content_mode).strip().lower()
    _validate_kohya_option_values(
        content_mode,
        ".json" if content_mode == "json" else ".txt",
        str(request.mask_export),
    )


def validate_kohya_package_options(options: DatasetPackageOptions) -> None:
    _validate_kohya_option_values(
        options.content_mode,
        options.caption_extension,
        options.mask_export,
    )


def _require_nonnegative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KohyaTrainerContractError(
            f"Kohya contract requires {field_name} to be a non-negative integer"
        )
    return value


def _toml_path_literal(path: Path) -> str:
    return str(path).replace("\\", "/")


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _options_from_request(
    output_folder: Path,
    request: DatasetExportRequest,
    *,
    masks_written: int,
    masks_missing: int,
) -> KohyaDatasetConfigOptions:
    validate_kohya_request(request)
    written = _require_nonnegative_integer(masks_written, "masks_written")
    missing = _require_nonnegative_integer(masks_missing, "masks_missing")
    mask_mode = str(request.mask_export).strip().lower()
    if mask_mode == "kohya" and missing > 0:
        raise KohyaTrainerContractError(
            "Kohya conditioning requires a conditioning mask for every exported image; "
            f"masks_written={written}, masks_missing={missing}"
        )
    conditioning_data_dir = output_folder / "mask" if mask_mode == "kohya" and written > 0 else None
    try:
        return KohyaDatasetConfigOptions(
            image_dir=output_folder,
            caption_extension=".txt",
            num_repeats=request.trainer_repeats,
            batch_size=request.trainer_batch,
            resolution=request.trainer_resolution,
            keep_tokens=request.trainer_keep_tokens,
            class_tokens=request.trigger.strip(),
            conditioning_data_dir=conditioning_data_dir,
        )
    except ValidationError as exc:
        raise KohyaTrainerContractError(
            f"Kohya dataset options failed strict validation: {exc}"
        ) from exc


def _reject_extra_keys(data: Dict[str, Any], allowed: set[str], path: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise KohyaTrainerContractError(
            f"Kohya TOML contains unsupported fields at {path}: {', '.join(extra)}"
        )


def validate_kohya_toml_text(content: str) -> KohyaDatasetConfigOptions:
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise KohyaTrainerContractError(f"Kohya TOML is not parseable: {exc}") from exc
    _reject_extra_keys(document, {"general", "datasets"}, "root")
    general = document.get("general")
    datasets = document.get("datasets")
    if not isinstance(general, dict):
        raise KohyaTrainerContractError("Kohya TOML general must be a table")
    _reject_extra_keys(general, {"enable_bucket"}, "general")
    if general.get("enable_bucket") is not True:
        raise KohyaTrainerContractError("Kohya TOML general.enable_bucket must be true")
    if not isinstance(datasets, list) or len(datasets) != 1 or not isinstance(datasets[0], dict):
        raise KohyaTrainerContractError("Kohya TOML must contain exactly one datasets table")
    dataset = datasets[0]
    _reject_extra_keys(dataset, {"resolution", "batch_size", "subsets"}, "datasets[0]")
    subsets = dataset.get("subsets")
    if not isinstance(subsets, list) or len(subsets) != 1 or not isinstance(subsets[0], dict):
        raise KohyaTrainerContractError(
            "Kohya TOML must contain exactly one datasets[0].subsets table"
        )
    subset = subsets[0]
    _reject_extra_keys(
        subset,
        {
            "image_dir",
            "caption_extension",
            "num_repeats",
            "shuffle_caption",
            "keep_tokens",
            "conditioning_data_dir",
            "class_tokens",
        },
        "datasets[0].subsets[0]",
    )
    keep_tokens = subset.get("keep_tokens", 0)
    shuffle_caption = subset.get("shuffle_caption")
    try:
        options = KohyaDatasetConfigOptions(
            image_dir=Path(subset["image_dir"]),
            caption_extension=subset["caption_extension"],
            num_repeats=subset["num_repeats"],
            batch_size=dataset["batch_size"],
            resolution=dataset["resolution"],
            keep_tokens=keep_tokens,
            class_tokens=subset.get("class_tokens", ""),
            conditioning_data_dir=(
                Path(subset["conditioning_data_dir"])
                if "conditioning_data_dir" in subset
                else None
            ),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise KohyaTrainerContractError(
            f"Kohya TOML failed strict option validation: {exc}"
        ) from exc
    if options.keep_tokens > 0 and shuffle_caption is not True:
        raise KohyaTrainerContractError(
            "Kohya TOML keep_tokens requires shuffle_caption=true"
        )
    if options.keep_tokens == 0 and shuffle_caption is not None:
        raise KohyaTrainerContractError(
            "Kohya TOML shuffle_caption is only emitted with keep_tokens"
        )
    if options.conditioning_data_dir is not None and options.class_tokens:
        raise KohyaTrainerContractError(
            "Kohya conditioning subsets do not support class_tokens; "
            "the trigger must remain in caption sidecars"
        )
    return options


def render_kohya_dataset_config(options: KohyaDatasetConfigOptions) -> str:
    lines = [
        "# Generated by SD Image Sorter for kohya sd-scripts v0.11.1.",
        "# class_tokens is a fallback when a caption is missing; it does not alter captions.",
        "[general]",
        "enable_bucket = true",
        "",
        "[[datasets]]",
        f"resolution = {options.resolution}",
        f"batch_size = {options.batch_size}",
        "",
        "  [[datasets.subsets]]",
        f"  image_dir = {_toml_basic_string(_toml_path_literal(options.image_dir))}",
        f"  caption_extension = {_toml_basic_string(options.caption_extension)}",
        f"  num_repeats = {options.num_repeats}",
    ]
    if options.keep_tokens > 0:
        lines.extend(("  shuffle_caption = true", f"  keep_tokens = {options.keep_tokens}"))
    if options.conditioning_data_dir is not None:
        lines.append(
            "  conditioning_data_dir = "
            f"{_toml_basic_string(_toml_path_literal(options.conditioning_data_dir))}"
        )
    if options.class_tokens and options.conditioning_data_dir is None:
        lines.append(f"  class_tokens = {_toml_basic_string(options.class_tokens)}")
    content = "\n".join((*lines, ""))
    validate_kohya_toml_text(content)
    return content


def _write_kohya_config_atomically(target: Path, content: str) -> None:
    """Stage the config beside its target, then publish it over the target.

    Both halves come from ``utils.atomic_staging``, for the reasons spelled out
    in ``anima_contract._write_anima_config_atomically``: ``tempfile`` retried an
    unwritable destination folder up to ``tempfile.TMP_MAX`` times instead of
    reporting its refusal, and a bare ``os.replace`` severs a hard link on the
    destination.
    """
    temporary_path: Optional[Path] = None
    try:
        temporary_path, descriptor = create_staging_sibling(target)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    "short write while preparing Kohya dataset config: "
                    f"expected_characters={len(content)}, written_characters={written}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        publish_staging_file(temporary_path, target)
        temporary_path = None
    except OSError as exc:
        cleanup_error: Optional[OSError] = None
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as unlink_error:
                cleanup_error = unlink_error
        raise KohyaTrainerContractError(
            "Kohya dataset config could not be written atomically: "
            f"target={target}, temporary_path={temporary_path}, "
            f"error_type={type(exc).__name__}, error={exc}, "
            f"cleanup_error={cleanup_error}"
        ) from exc


def write_kohya_dataset_config(
    output_folder: Path,
    request: DatasetExportRequest,
    *,
    masks_written: int,
    masks_missing: int,
) -> str:
    options = _options_from_request(
        output_folder,
        request,
        masks_written=masks_written,
        masks_missing=masks_missing,
    )
    content = render_kohya_dataset_config(options)
    target = output_folder / "dataset_config.toml"
    _write_kohya_config_atomically(target, content)
    return str(target)


__all__: List[str] = [
    "KOHYA_CONTRACT_VERSION",
    "KOHYA_UPSTREAM_COMMIT",
    "KOHYA_UPSTREAM_REPOSITORY",
    "KOHYA_UPSTREAM_TAG",
    "KohyaDatasetConfigOptions",
    "KohyaTrainerContract",
    "KohyaTrainerContractError",
    "KohyaTrainerContractsResponse",
    "get_kohya_trainer_contract",
    "get_kohya_trainer_contracts_response",
    "render_kohya_dataset_config",
    "validate_kohya_request",
    "validate_kohya_package_options",
    "validate_kohya_toml_text",
    "write_kohya_dataset_config",
]
