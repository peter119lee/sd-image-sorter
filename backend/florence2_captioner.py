"""Pinned native Florence-2 Base natural-language captioner."""
from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ContextManager, Protocol, cast

from PIL import Image

from ai_runtime_guard import PRIORITY_BATCH, exclusive_ai_runtime
from config import get_florence2_model_dir
from model_download_sources import (
    endpoint_label,
    format_hf_download_error,
    get_hf_endpoint_order,
    hf_error_metadata,
    log_model_artifact_status,
    missing_model_artifacts,
)

logger = logging.getLogger(__name__)

FLORENCE2_MODEL_ID = "florence-community/Florence-2-base"
FLORENCE2_REVISION = "00921df66db728a9ceb750f5eca43e5c203a2051"
FLORENCE2_TASK = "<MORE_DETAILED_CAPTION>"
FLORENCE2_REQUIRED_FILES = (
    "added_tokens.json",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "processor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


class Florence2Error(RuntimeError):
    """Base exception for actionable Florence-2 failures."""


class Florence2UnavailableError(Florence2Error):
    """Raised when the pinned checkpoint or runtime is unavailable."""


class Florence2CudaError(Florence2Error):
    """Raised when explicitly requested CUDA execution is unavailable."""


class Florence2InferenceError(Florence2Error):
    """Raised when Florence-2 cannot produce a valid prose caption."""


class _BatchInputs(Protocol):
    def to(self, device: str) -> "_BatchInputs": ...
    def __getitem__(self, key: str) -> object: ...


class _Florence2Model(Protocol):
    def to(self, device: str) -> "_Florence2Model": ...
    def eval(self) -> "_Florence2Model": ...
    def generate(self, **kwargs: object) -> object: ...


class _Florence2Processor(Protocol):
    def __call__(
        self,
        *,
        text: str,
        images: Image.Image,
        return_tensors: str,
    ) -> _BatchInputs: ...

    def batch_decode(
        self,
        generated_ids: object,
        *,
        skip_special_tokens: bool,
    ) -> Sequence[str]: ...

    def post_process_generation(
        self,
        text: str,
        *,
        task: str,
        image_size: tuple[int, int],
    ) -> Mapping[str, object]: ...


class _TorchModule(Protocol):
    def inference_mode(self) -> ContextManager[None]: ...


_runtime_lock = threading.Lock()
_runtime_by_device: dict[str, tuple[_Florence2Model, _Florence2Processor]] = {}


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def missing_checkpoint_files(model_dir: Path) -> tuple[str, ...]:
    """Return required Florence-2 files absent from a candidate directory."""
    return missing_model_artifacts(model_dir, FLORENCE2_REQUIRED_FILES)


def get_checkpoint_path() -> str | None:
    """Return the complete local Florence-2 checkpoint directory, if prepared."""
    model_dir = Path(get_florence2_model_dir())
    if not missing_checkpoint_files(model_dir):
        return str(model_dir.resolve())
    return None


def prepare_checkpoint() -> str:
    """Download only the commit-pinned Florence-2 snapshot."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise Florence2UnavailableError(
            "huggingface_hub is required to prepare Florence-2 Base. "
            "Run Prepare / Download again after installing application dependencies. "
            "/ 准备 Florence-2 Base 需要 huggingface_hub。"
        ) from exc

    model_dir = Path(get_florence2_model_dir())
    model_dir.mkdir(parents=True, exist_ok=True)
    endpoints = get_hf_endpoint_order(model_name="Florence-2 Base")
    last_error: Exception | None = None
    selected_endpoint = ""
    for endpoint in endpoints:
        try:
            snapshot_download(
                repo_id=FLORENCE2_MODEL_ID,
                revision=FLORENCE2_REVISION,
                local_dir=str(model_dir),
                allow_patterns=list(FLORENCE2_REQUIRED_FILES),
                endpoint=endpoint,
            )
            selected_endpoint = endpoint
            break
        except Exception as exc:  # noqa: BLE001 - Hub transports vary by endpoint
            last_error = exc
            logger.warning(
                "Florence-2 checkpoint download failed",
                extra={
                    "model_id": FLORENCE2_MODEL_ID,
                    "revision": FLORENCE2_REVISION,
                    "endpoint": endpoint_label(endpoint),
                    **hf_error_metadata(exc),
                },
            )
    else:
        attempted = ", ".join(endpoint_label(endpoint) for endpoint in endpoints)
        raise Florence2UnavailableError(
            f"{format_hf_download_error(model_id=FLORENCE2_MODEL_ID, revision=FLORENCE2_REVISION, endpoint=attempted, error=last_error)} "
            "/ Florence-2 下载失败，请检查网络后重试。"
        ) from last_error

    missing = log_model_artifact_status(
        logger,
        model_id=FLORENCE2_MODEL_ID,
        revision=FLORENCE2_REVISION,
        endpoint=selected_endpoint or "unknown",
        model_dir=model_dir,
        required_files=FLORENCE2_REQUIRED_FILES,
    )
    if missing:
        raise Florence2UnavailableError(
            f"Florence-2 download completed but required files are missing in {model_dir}: "
            f"{', '.join(missing)}. Retry Prepare / Download. "
            "/ Florence-2 下载不完整，请重试。"
        )
    return str(model_dir.resolve())


def _resolve_device(*, use_gpu: bool) -> str:
    if not use_gpu:
        return "cpu"
    try:
        import torch
    except ImportError as exc:
        raise Florence2UnavailableError(
            "Florence-2 requires PyTorch. Open Model Manager, run Prepare / Download, "
            "then restart the app. / Florence-2 缺少 PyTorch 运行环境。"
        ) from exc

    cuda = getattr(torch, "cuda", None)
    is_available = getattr(cuda, "is_available", None)
    if not callable(is_available) or is_available() is not True:
        raise Florence2CudaError(
            "Florence-2 CUDA was requested, but CUDA is not available to this app. "
            "Open Model Manager, run Prepare / Download, restart the app, or explicitly "
            "disable GPU for a CPU run. No automatic CPU fallback was used. "
            "/ 已请求 Florence-2 CUDA，但当前应用无法使用 CUDA；未自动回退到 CPU。"
        )
    return "cuda"


def _load_runtime(device: str) -> tuple[_Florence2Model, _Florence2Processor]:
    cached = _runtime_by_device.get(device)
    if cached is not None:
        return cached

    with _runtime_lock:
        cached = _runtime_by_device.get(device)
        if cached is not None:
            return cached

        checkpoint_path = get_checkpoint_path()
        if checkpoint_path is None:
            model_dir = Path(get_florence2_model_dir())
            missing = ", ".join(missing_checkpoint_files(model_dir))
            raise Florence2UnavailableError(
                "Florence-2 model files are missing. Open Model Manager and run "
                f"Prepare / Download for Florence-2. Expected {model_dir}; missing: {missing}. "
                "/ Florence-2 模型文件缺失，请在模型管理器中执行“准备 / 下载”。"
            )

        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:
            raise Florence2UnavailableError(
                "Florence-2 runtime is unavailable. Open Model Manager and run "
                "Prepare / Download for Florence-2, then restart the app. "
                "/ Florence-2 运行环境不可用，请准备后重启应用。"
            ) from exc

        try:
            with exclusive_ai_runtime("florence2-load"):
                loaded_model = AutoModelForImageTextToText.from_pretrained(
                    checkpoint_path,
                    trust_remote_code=False,
                    local_files_only=True,
                    use_safetensors=True,
                )
                loaded_processor = AutoProcessor.from_pretrained(
                    checkpoint_path,
                    trust_remote_code=False,
                    local_files_only=True,
                )
                model = cast(_Florence2Model, loaded_model)
                processor = cast(_Florence2Processor, loaded_processor)
                model.to(device)
                model.eval()
        except ImportError as exc:
            raise Florence2UnavailableError(
                f"Florence-2 native runtime dependency is missing while loading "
                f"{checkpoint_path}: {exc}. Run Prepare / Download, then restart the app."
            ) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error_type = Florence2CudaError if device == "cuda" else Florence2UnavailableError
            raise error_type(
                f"Florence-2 failed to load on {device} from {checkpoint_path}: {exc}. "
                f"/ Florence-2 无法在 {device} 上加载。"
            ) from exc

        runtime = (model, processor)
        _runtime_by_device[device] = runtime
        return runtime


def _require_batch_inputs(value: object) -> _BatchInputs:
    if not isinstance(value, Mapping) or not callable(getattr(value, "to", None)):
        raise Florence2InferenceError(
            "Florence-2 processor returned invalid inputs; expected a tensor mapping "
            "with a device-transfer method."
        )
    if "input_ids" not in value or "pixel_values" not in value:
        raise Florence2InferenceError(
            "Florence-2 processor output is missing input_ids or pixel_values."
        )
    return cast(_BatchInputs, value)


def _require_caption(value: object) -> str:
    if not isinstance(value, Mapping):
        raise Florence2InferenceError(
            "Florence-2 post-processing returned an invalid response mapping."
        )
    caption_value = value.get(FLORENCE2_TASK)
    if not isinstance(caption_value, str) or not caption_value.strip():
        raise Florence2InferenceError(
            "Florence-2 must return a non-empty natural-language caption for "
            f"task {FLORENCE2_TASK}."
        )
    return caption_value.strip()


def caption_image(image_path: str, *, use_gpu: bool) -> str:
    """Generate one required Florence-2 natural-language caption."""
    source_path = Path(image_path)
    if not source_path.is_file():
        raise FileNotFoundError(
            f"Florence-2 image does not exist or is not a file: {source_path}"
        )

    device = _resolve_device(use_gpu=use_gpu)
    try:
        import torch
    except ImportError as exc:
        raise Florence2UnavailableError(
            "Florence-2 requires PyTorch. Run Prepare / Download in Model Manager, "
            "then restart the app."
        ) from exc
    torch_module = cast(_TorchModule, torch)
    model, processor = _load_runtime(device)

    try:
        with Image.open(source_path) as opened:
            source = opened.convert("RGB")
        # Batch lane: reached only from Smart Tag's caption phase, which runs as
        # a polled background job rather than a synchronous request.
        with exclusive_ai_runtime("florence2-inference", priority=PRIORITY_BATCH):
            inputs = _require_batch_inputs(
                processor(
                    text=FLORENCE2_TASK,
                    images=source,
                    return_tensors="pt",
                )
            ).to(device)
            with torch_module.inference_mode():
                generated_ids = model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024,
                    do_sample=False,
                    num_beams=3,
                )
            decoded = processor.batch_decode(
                generated_ids,
                skip_special_tokens=False,
            )
        if isinstance(decoded, (str, bytes)) or not isinstance(decoded, Sequence) or not decoded:
            raise Florence2InferenceError(
                "Florence-2 decoder returned no generated text."
            )
        decoded_text = decoded[0]
        if not isinstance(decoded_text, str):
            raise Florence2InferenceError(
                "Florence-2 decoder returned a non-text result."
            )
        processed = processor.post_process_generation(
            decoded_text,
            task=FLORENCE2_TASK,
            image_size=source.size,
        )
        return _require_caption(processed)
    except Florence2Error:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        error_type = Florence2CudaError if device == "cuda" else Florence2InferenceError
        raise error_type(
            f"Florence-2 caption generation failed on {device} for {source_path}: {exc}. "
            f"/ Florence-2 在 {device} 上生成描述失败。"
        ) from exc


class Florence2Captioner:
    """External local-captioner interface used by the Smart Tag pipeline."""

    def __init__(self, *, use_gpu: bool) -> None:
        self.use_gpu = use_gpu

    def load(self) -> None:
        device = _resolve_device(use_gpu=self.use_gpu)
        _load_runtime(device)

    def caption(self, image_path: str) -> str:
        return caption_image(image_path, use_gpu=self.use_gpu)


def get_florence2_captioner(
    *,
    use_gpu: bool,
    force_reload: bool,
) -> Florence2Captioner:
    """Return the Smart Tag Florence-2 connector with explicit device policy."""
    if force_reload:
        device = _resolve_device(use_gpu=use_gpu)
        _runtime_by_device.pop(device, None)
    return Florence2Captioner(use_gpu=use_gpu)
