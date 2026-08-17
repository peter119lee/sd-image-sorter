"""Optional CL Tagger v2 ONNX Runtime backend.

CL Tagger v2 is a gated, user-downloaded SigLIP2 multi-label classifier. The
application only downloads the stable ``v2_00`` files directly from the
official Hugging Face repository and never bundles or mirrors the weights.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Mapping, Optional

import numpy as np
from PIL import Image

import config
from ai_runtime_guard import PRIORITY_BATCH, exclusive_ai_runtime
from model_download_sources import (
    HF_OFFICIAL_ENDPOINT,
    format_hf_download_error,
    hf_error_metadata,
    log_model_artifact_status,
    missing_model_artifacts,
)
from tagger_scoring import _ScoringMixin

logger = logging.getLogger("cl_tagger_v2")

CL_TAGGER_V2_MODEL_NAME = "cl-tagger-v2"
CL_TAGGER_V2_REPO_ID = "cella110n/cl_tagger_v2"
CL_TAGGER_V2_REVISION = "b57909b8e9c63f71e208a26473e7aabdf45ed6b6"
CL_TAGGER_V2_VERSION_DIR = "v2_00"
CL_TAGGER_V2_REQUIRED_FILES = (
    f"{CL_TAGGER_V2_VERSION_DIR}/model.onnx",
    f"{CL_TAGGER_V2_VERSION_DIR}/model.onnx.data",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_vocabulary.json",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_metadata.json",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_tag_metrics.npz",
)
CL_TAGGER_V2_OPTIONAL_FILES = (
    f"{CL_TAGGER_V2_VERSION_DIR}/model_ood_ref.npz",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_split_files/manifest.json",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_split_files/part0.onnx",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_split_files/part0.onnx.data",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_split_files/part1.onnx",
    f"{CL_TAGGER_V2_VERSION_DIR}/model_split_files/part1.onnx.data",
)
CL_TAGGER_V2_REQUIRED_MODULES = ("onnxruntime",)
CL_TAGGER_V2_IMAGE_SIZE = 384
CL_TAGGER_V2_DEFAULT_THRESHOLD = 0.55


class CLTaggerV2Error(RuntimeError):
    """Raised when the CL Tagger v2 package is incomplete or unusable."""


class CLTaggerV2AuthRequiredError(CLTaggerV2Error):
    """Raised when Hugging Face blocks the gated checkpoint download."""


@dataclass(frozen=True)
class CLTagVocabulary:
    """Normalized vocabulary consumed by the shared tag result contract."""

    tags: tuple[str, ...]
    general_tags: tuple[tuple[int, str], ...]
    copyright_tags: tuple[tuple[int, str], ...]
    character_tags: tuple[tuple[int, str], ...]
    rating_tags: tuple[tuple[int, str], ...]
    general_category_overrides: dict[str, str]


def _category_name(raw_category: object, categories: object) -> str:
    if isinstance(raw_category, int):
        if not isinstance(categories, list) or raw_category < 0 or raw_category >= len(categories):
            raise CLTaggerV2Error(
                f"CL Tagger v2 vocabulary has an invalid category index: {raw_category!r}"
            )
        raw_category = categories[raw_category]
    normalized = str(raw_category or "general").strip().lower()
    aliases = {
        "artist": "general",
        "meta": "meta",
        "quality": "quality",
        "rating": "rating",
        "copyright": "copyright",
        "character": "character",
        "general": "general",
    }
    if normalized not in aliases:
        raise CLTaggerV2Error(
            f"CL Tagger v2 vocabulary contains unsupported tag category: {raw_category!r}"
        )
    return aliases[normalized]


def _rating_name(tag_name: str) -> str:
    normalized = str(tag_name).strip()
    if normalized.lower().startswith("rating:"):
        return normalized.split(":", 1)[1].strip().lower()
    if normalized.lower().startswith("rating_"):
        return normalized.split("_", 1)[1].strip().lower()
    return normalized.lower()


def parse_vocabulary(payload: Mapping[str, object]) -> CLTagVocabulary:
    """Validate and normalize the official ``model_vocabulary.json`` payload."""
    raw_idx_to_tag = payload.get("idx_to_tag")
    raw_tag_to_category = payload.get("tag_to_category")
    categories = payload.get("categories")
    if not isinstance(raw_idx_to_tag, dict) or not isinstance(raw_tag_to_category, dict):
        raise CLTaggerV2Error(
            "CL Tagger v2 vocabulary must contain object fields idx_to_tag and tag_to_category."
        )

    indexed_tags: list[tuple[int, str]] = []
    for raw_index, raw_name in raw_idx_to_tag.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise CLTaggerV2Error(
                f"CL Tagger v2 vocabulary contains a non-integer tag index: {raw_index!r}"
            ) from exc
        name = str(raw_name or "").strip()
        if index < 0 or not name:
            raise CLTaggerV2Error(
                f"CL Tagger v2 vocabulary contains an invalid tag entry: {raw_index!r}={raw_name!r}"
            )
        indexed_tags.append((index, name))

    indexed_tags.sort(key=lambda item: item[0])
    if not indexed_tags or [index for index, _name in indexed_tags] != list(range(len(indexed_tags))):
        raise CLTaggerV2Error("CL Tagger v2 vocabulary indices must be contiguous from zero.")

    general: list[tuple[int, str]] = []
    copyright_tags: list[tuple[int, str]] = []
    character: list[tuple[int, str]] = []
    ratings: list[tuple[int, str]] = []
    overrides: dict[str, str] = {}
    for index, name in indexed_tags:
        category = _category_name(raw_tag_to_category.get(name, "general"), categories)
        if category == "character":
            character.append((index, name))
        elif category == "copyright":
            copyright_tags.append((index, name))
        elif category == "rating":
            ratings.append((index, _rating_name(name)))
        else:
            general.append((index, name))
            if category != "general":
                overrides[name] = category

    return CLTagVocabulary(
        tags=tuple(name for _index, name in indexed_tags),
        general_tags=tuple(general),
        copyright_tags=tuple(copyright_tags),
        character_tags=tuple(character),
        rating_tags=tuple(ratings),
        general_category_overrides=overrides,
    )


def has_complete_runtime_files(model_dir: Path) -> bool:
    """Return whether every file needed to open the stable v2_00 graph exists."""
    return not missing_model_artifacts(model_dir, CL_TAGGER_V2_REQUIRED_FILES)


def prepare_checkpoint() -> str:
    """Download the pinned gated checkpoint without creating an inference session."""
    model_dir = config.get_cl_tagger_v2_model_dir()
    tagger = CLTaggerV2Tagger(
        model_name=CL_TAGGER_V2_MODEL_NAME,
        model_path=None,
        tags_path=None,
        model_dir=model_dir,
        threshold=CL_TAGGER_V2_DEFAULT_THRESHOLD,
        character_threshold=CL_TAGGER_V2_DEFAULT_THRESHOLD,
        copyright_threshold=CL_TAGGER_V2_DEFAULT_THRESHOLD,
        use_gpu=False,
    )
    tagger._download_model()
    checkpoint_dir = Path(model_dir) / CL_TAGGER_V2_MODEL_NAME
    if not has_complete_runtime_files(checkpoint_dir):
        missing = [
            filename
            for filename in CL_TAGGER_V2_REQUIRED_FILES
            if not (
                (checkpoint_dir / filename).is_file()
                and (checkpoint_dir / filename).stat().st_size > 0
            )
        ]
        raise CLTaggerV2Error(
            "CL Tagger v2 download completed without all required runtime files: "
            f"root={checkpoint_dir}, missing={missing}."
        )
    return str(checkpoint_dir.resolve())


def preprocess_image(image: Image.Image, target: int) -> np.ndarray:
    """Apply the official fixed 384px SigLIP2 preprocessing contract."""
    if target <= 0:
        raise ValueError(f"CL Tagger v2 target size must be positive, got {target}.")
    rgb = image.convert("RGB").resize((target, target), Image.Resampling.BICUBIC)
    values = np.asarray(rgb, dtype=np.float32) / 255.0
    values = (values - 0.5) / 0.5
    return np.transpose(values, (2, 0, 1)).astype(np.float32, copy=False)


ort: Optional[ModuleType] = None
hf_hub: Optional[ModuleType] = None
_ensure_imports_lock = threading.Lock()


def _ensure_huggingface_hub() -> ModuleType:
    global hf_hub
    if hf_hub is None:
        with _ensure_imports_lock:
            if hf_hub is None:
                import huggingface_hub as hf_module

                hf_hub = hf_module
    if hf_hub is None:  # pragma: no cover - guarded by the assignment above
        raise CLTaggerV2Error("Hugging Face Hub could not be imported.")
    return hf_hub


def _ensure_onnxruntime() -> ModuleType:
    global ort
    if ort is None:
        with _ensure_imports_lock:
            if ort is None:
                from runtime_env import prepare_onnxruntime_environment

                prepare_onnxruntime_environment()
                import onnxruntime as ort_module  # type: ignore

                ort = ort_module
    if ort is None:  # pragma: no cover - guarded by the assignment above
        raise CLTaggerV2Error("ONNX Runtime could not be imported.")
    return ort


class CLTaggerV2Tagger(_ScoringMixin):
    """SigLIP2 CL Tagger v2 with the shared application tag result shape."""

    def __init__(
        self,
        model_name: str,
        model_path: Optional[str],
        tags_path: Optional[str],
        model_dir: Optional[str],
        threshold: float,
        character_threshold: float,
        copyright_threshold: float,
        use_gpu: bool,
    ) -> None:
        self.model_name = model_name.strip().lower() or CL_TAGGER_V2_MODEL_NAME
        self.model_path = model_path
        self.tags_path = tags_path
        self.model_dir = model_dir or config.get_cl_tagger_v2_model_dir()
        self.threshold = float(threshold)
        self.character_threshold = float(character_threshold)
        self.copyright_threshold = float(copyright_threshold)
        self.use_gpu = bool(use_gpu)
        self.session = None
        self.tags: tuple[str, ...] = ()
        self.general_tags: tuple[tuple[int, str], ...] = ()
        self.copyright_tags: tuple[tuple[int, str], ...] = ()
        self.character_tags: tuple[tuple[int, str], ...] = ()
        self.rating_tags: tuple[tuple[int, str], ...] = ()
        self.rating_indices: dict[str, int] = {}
        self._general_category_overrides: dict[str, str] = {}
        self._loaded = False
        self._resolved_model_path: Optional[str] = None
        self._resolved_tags_path: Optional[str] = None
        self._output_activation = "sigmoid"
        self._rating_fallback_mode = "none"
        self._target = CL_TAGGER_V2_IMAGE_SIZE

    def _model_config(self) -> Mapping[str, object]:
        model_config = config.TAGGER_MODELS.get(self.model_name)
        if not isinstance(model_config, dict) or model_config.get("runtime_backend") != "cl-tagger-v2":
            raise CLTaggerV2Error(
                f"Unknown CL Tagger v2 model: {self.model_name!r}."
            )
        return model_config

    def _local_root(self) -> Path:
        return Path(self.model_dir) / self.model_name

    def _download_model(self) -> tuple[str, str]:
        model_config = self._model_config()
        repo_id = str(model_config["repo_id"])
        revision = str(model_config["revision"])
        local_root = self._local_root()
        local_root.mkdir(parents=True, exist_ok=True)
        hub = _ensure_huggingface_hub()

        for filename in CL_TAGGER_V2_REQUIRED_FILES:
            target = local_root / filename
            if target.is_file() and target.stat().st_size > 0:
                continue
            try:
                hub.hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    local_dir=str(local_root),
                    endpoint=HF_OFFICIAL_ENDPOINT,
                )
            except Exception as exc:
                metadata = hf_error_metadata(exc)
                logger.warning(
                    "CL Tagger v2 checkpoint file download failed",
                    extra={
                        "starter_console_suppress": True,
                        "model_id": CL_TAGGER_V2_MODEL_NAME,
                        "repo_id": repo_id,
                        "revision": revision,
                        "endpoint": HF_OFFICIAL_ENDPOINT,
                        "artifact_file": filename,
                        **metadata,
                    },
                )
                message = (
                    f"{format_hf_download_error(model_id=repo_id, revision=revision, endpoint=HF_OFFICIAL_ENDPOINT, error=exc)} "
                    f"file={filename!r}."
                )
                if metadata["gated"] is True:
                    raise CLTaggerV2AuthRequiredError(message) from exc
                raise CLTaggerV2Error(message) from exc

        missing = log_model_artifact_status(
            logger,
            model_id=CL_TAGGER_V2_MODEL_NAME,
            revision=revision,
            endpoint=HF_OFFICIAL_ENDPOINT,
            model_dir=local_root,
            required_files=CL_TAGGER_V2_REQUIRED_FILES,
        )
        if missing:
            raise CLTaggerV2Error(
                "CL Tagger v2 download completed without required runtime files: "
                + ", ".join(missing)
                + ". Retry Prepare / Download."
            )

        model_path = local_root / str(model_config["model_file"])
        vocabulary_path = local_root / str(model_config["tags_file"])
        if not model_path.is_file() or not vocabulary_path.is_file():
            raise CLTaggerV2Error(
                f"CL Tagger v2 download completed without the expected runtime files: "
                f"model={model_path}, vocabulary={vocabulary_path}."
            )
        return str(model_path), str(vocabulary_path)

    def _get_model_paths(self) -> tuple[str, str]:
        if self.model_path:
            model_path = Path(self.model_path)
            if not model_path.is_file():
                raise FileNotFoundError(f"Custom CL Tagger v2 model file not found: {model_path}")
            vocabulary_path = Path(self.tags_path) if self.tags_path else model_path.parent / "model_vocabulary.json"
            if not vocabulary_path.is_file():
                raise FileNotFoundError(
                    f"Custom CL Tagger v2 vocabulary file not found: {vocabulary_path}"
                )
            return str(model_path), str(vocabulary_path)
        return self._download_model()

    def _load_vocabulary(self, vocabulary_path: str) -> None:
        try:
            payload = json.loads(Path(vocabulary_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CLTaggerV2Error(
                f"Could not read CL Tagger v2 vocabulary JSON: {vocabulary_path}; error={exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise CLTaggerV2Error("CL Tagger v2 vocabulary root must be a JSON object.")
        vocabulary = parse_vocabulary(payload)
        self.tags = vocabulary.tags
        self.general_tags = vocabulary.general_tags
        self.copyright_tags = vocabulary.copyright_tags
        self.character_tags = vocabulary.character_tags
        self.rating_tags = vocabulary.rating_tags
        self.rating_indices = {name: index for index, name in vocabulary.rating_tags}
        self._general_category_overrides = vocabulary.general_category_overrides

    def _build_session_options(self, gpu_enabled: bool):
        runtime = _ensure_onnxruntime()
        options = runtime.SessionOptions()
        options.intra_op_num_threads = 2 if gpu_enabled else 1
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = runtime.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = not gpu_enabled
        options.enable_mem_pattern = not gpu_enabled
        return options

    def _session_uses_gpu(self) -> bool:
        if self.session is None:
            return False
        providers = {str(provider) for provider in self.session.get_providers()}
        return bool(providers & {"CUDAExecutionProvider", "DmlExecutionProvider"})

    def load(self) -> None:
        if self._loaded:
            return
        with exclusive_ai_runtime("cl-tagger-v2-load"):
            model_path, vocabulary_path = self._get_model_paths()
            self._resolved_model_path = model_path
            self._resolved_tags_path = vocabulary_path
            self._load_vocabulary(vocabulary_path)
            runtime = _ensure_onnxruntime()
            available = set(runtime.get_available_providers())
            providers = ["CPUExecutionProvider"]
            gpu_requested = self.use_gpu
            if gpu_requested:
                providers = [
                    provider
                    for provider in (
                        "CUDAExecutionProvider",
                        "DmlExecutionProvider",
                        "CPUExecutionProvider",
                    )
                    if provider in available
                ]
            if not providers:
                raise CLTaggerV2Error(
                    "No ONNX Runtime execution provider is available for CL Tagger v2."
                )
            self.session = runtime.InferenceSession(
                model_path,
                sess_options=self._build_session_options(
                    gpu_enabled=gpu_requested
                    and any(provider in available for provider in ("CUDAExecutionProvider", "DmlExecutionProvider"))
                ),
                providers=providers,
            )
            input_names = {item.name for item in self.session.get_inputs()}
            if "pixel_values" not in input_names:
                raise CLTaggerV2Error(
                    f"CL Tagger v2 ONNX graph is missing pixel_values input; inputs={sorted(input_names)}."
                )
            output_names = {item.name for item in self.session.get_outputs()}
            if "logits" not in output_names:
                raise CLTaggerV2Error(
                    f"CL Tagger v2 ONNX graph is missing logits output; outputs={sorted(output_names)}."
                )
            self.use_gpu = self._session_uses_gpu()
            self._loaded = True

    def set_session_refresh_interval(self, _interval: int) -> None:
        """Keep the shared worker contract; this backend has no refresh policy yet."""

    def release_session(self) -> None:
        self.session = None
        self._loaded = False

    def _run_logits(self, pixel_values: np.ndarray) -> np.ndarray:
        if self.session is None:
            raise CLTaggerV2Error("CL Tagger v2 session is not loaded.")
        outputs = self.session.run(
            ["logits"],
            {"pixel_values": pixel_values.astype(np.float32, copy=False)},
        )
        if not outputs:
            raise CLTaggerV2Error("CL Tagger v2 returned no logits output.")
        return np.asarray(outputs[0])

    def tag(
        self,
        image_path: str,
        *,
        threshold: Optional[float],
        character_threshold: Optional[float],
        copyright_threshold: Optional[float],
    ) -> dict[str, object]:
        # Batch lane even though this tags one file: CL Tagger v2 is only ever
        # selected by the gallery tag worker or Smart Tag's booru phase. The
        # single-image endpoint (POST /api/tag/single) always uses WD14.
        with exclusive_ai_runtime("cl-tagger-v2-inference", priority=PRIORITY_BATCH):
            if not self._loaded:
                self.load()
            try:
                with Image.open(image_path) as image:
                    pixels = preprocess_image(image, target=self._target)
                logits = self._run_logits(np.expand_dims(pixels, axis=0))[0]
            except Exception as exc:
                return self._build_empty_result(str(exc))
            return self._process_probs(
                logits,
                threshold=threshold,
                character_threshold=character_threshold,
                copyright_threshold=copyright_threshold,
            )

    def tag_batch(
        self,
        image_paths: list[str],
        *,
        preferred_batch_size: int,
        min_batch_size: int,
        threshold: Optional[float],
        character_threshold: Optional[float],
        copyright_threshold: Optional[float],
        return_runtime_info: bool,
    ):
        del preferred_batch_size, min_batch_size
        with exclusive_ai_runtime("cl-tagger-v2-inference", priority=PRIORITY_BATCH):
            if not self._loaded:
                self.load()
            results: list[dict[str, object]] = [self._build_empty_result() for _ in image_paths]
            pixels: list[np.ndarray] = []
            indices: list[int] = []
            for index, image_path in enumerate(image_paths):
                try:
                    with Image.open(image_path) as image:
                        pixels.append(preprocess_image(image, target=self._target))
                    indices.append(index)
                except Exception as exc:
                    results[index] = self._build_empty_result(str(exc))
            if pixels:
                logits = self._run_logits(np.stack(pixels, axis=0))
                for result_index, source_index in enumerate(indices):
                    results[source_index] = self._process_probs(
                        logits[result_index],
                        threshold=threshold,
                        character_threshold=character_threshold,
                        copyright_threshold=copyright_threshold,
                    )
            if return_runtime_info:
                return results, {
                    "initial_chunk_size": len(image_paths),
                    "final_chunk_size": len(image_paths),
                    "backoff_steps": [],
                    "used_cpu_fallback": not self.use_gpu,
                    "attempted_gpu_backoff": False,
                }
            return results


_tagger_lock = threading.Lock()
_tagger_singleton: Optional[CLTaggerV2Tagger] = None
_singleton_settings: dict[str, object] = {}


def get_cl_tagger_v2_tagger(
    *,
    model_name: str,
    model_path: Optional[str],
    tags_path: Optional[str],
    threshold: float,
    character_threshold: float,
    copyright_threshold: float,
    use_gpu: bool,
    force_reload: bool,
) -> CLTaggerV2Tagger:
    """Return the process-local CL Tagger v2 singleton without loading weights."""
    global _tagger_singleton, _singleton_settings
    settings = {
        "model_name": model_name,
        "model_path": model_path,
        "tags_path": tags_path,
        "threshold": threshold,
        "character_threshold": character_threshold,
        "copyright_threshold": copyright_threshold,
        "use_gpu": use_gpu,
    }
    with _tagger_lock:
        if force_reload or _tagger_singleton is None or settings != _singleton_settings:
            _tagger_singleton = CLTaggerV2Tagger(
                model_name=model_name,
                model_path=model_path,
                tags_path=tags_path,
                model_dir=None,
                threshold=threshold,
                character_threshold=character_threshold,
                copyright_threshold=copyright_threshold,
                use_gpu=use_gpu,
            )
            _singleton_settings = settings
        return _tagger_singleton


__all__ = [
    "CLTaggerV2Error",
    "CLTaggerV2Tagger",
    "CL_TAGGER_V2_DEFAULT_THRESHOLD",
    "CL_TAGGER_V2_IMAGE_SIZE",
    "CL_TAGGER_V2_MODEL_NAME",
    "CL_TAGGER_V2_OPTIONAL_FILES",
    "CL_TAGGER_V2_REQUIRED_FILES",
    "CL_TAGGER_V2_REPO_ID",
    "CL_TAGGER_V2_REVISION",
    "CLTaggerV2AuthRequiredError",
    "get_cl_tagger_v2_tagger",
    "has_complete_runtime_files",
    "prepare_checkpoint",
    "parse_vocabulary",
    "preprocess_image",
]
