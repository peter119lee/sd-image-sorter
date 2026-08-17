"""Lazy Lucida subject matting for persistent training-mask previews."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, ContextManager, Protocol, Sequence, cast

import numpy as np
from PIL import Image

from ai_runtime_guard import PRIORITY_INTERACTIVE, exclusive_ai_runtime
from config import get_lucida_model_dir
from model_download_sources import (
    endpoint_label,
    format_hf_download_error,
    get_hf_endpoint_order,
    hf_error_metadata,
    log_model_artifact_status,
    missing_model_artifacts,
)

logger = logging.getLogger(__name__)

LUCIDA_MODEL_ID = "egeorcun/lucida"
LUCIDA_REVISION = "6ee11122534c8de59402a589d2293c198cfbf848"
LUCIDA_REQUIRED_FILES = (
    "config.json",
    "BiRefNet_config.py",
    "birefnet.py",
    "model.safetensors",
)


class LucidaError(RuntimeError):
    """Base exception for actionable Lucida failures."""


class LucidaUnavailableError(LucidaError):
    """Raised when the pinned checkpoint or runtime is unavailable."""


class LucidaInferenceError(LucidaError):
    """Raised when a prepared Lucida model cannot produce a valid alpha."""


class LucidaCudaError(LucidaError):
    """Raised when Lucida cannot load or infer on the requested CUDA device."""


class _Tensor(Protocol):
    def unsqueeze(self, dim: int) -> "_Tensor": ...
    def to(self, device: str) -> "_Tensor": ...
    def sigmoid(self) -> "_Tensor": ...
    def __getitem__(self, key: object) -> "_Tensor": ...
    def detach(self) -> "_Tensor": ...
    def cpu(self) -> "_Tensor": ...
    def float(self) -> "_Tensor": ...
    def numpy(self) -> np.ndarray: ...


class _LucidaModel(Protocol):
    def to(self, device: str) -> "_LucidaModel": ...
    def eval(self) -> "_LucidaModel": ...
    def __call__(self, tensor: _Tensor) -> Sequence[_Tensor]: ...


class _TorchModule(Protocol):
    def inference_mode(self) -> ContextManager[None]: ...


class _TransformsModule(Protocol):
    def Compose(self, transforms: Sequence[object]) -> Callable[[Image.Image], _Tensor]: ...
    def Resize(self, size: tuple[int, int]) -> object: ...
    def ToTensor(self) -> object: ...
    def Normalize(self, *, mean: tuple[float, ...], std: tuple[float, ...]) -> object: ...


_model_lock = threading.Lock()
_model_by_device: dict[str, _LucidaModel] = {}


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def get_checkpoint_path() -> str | None:
    """Return the complete local Lucida checkpoint directory, if prepared."""
    model_dir = Path(get_lucida_model_dir())
    if all(_is_nonempty_file(model_dir / filename) for filename in LUCIDA_REQUIRED_FILES):
        return str(model_dir.resolve())
    return None


def missing_checkpoint_files(model_dir: Path) -> tuple[str, ...]:
    """Return required Lucida files absent from a candidate directory."""
    return missing_model_artifacts(model_dir, LUCIDA_REQUIRED_FILES)


def prepare_checkpoint() -> str:
    """Download the commit-pinned Lucida snapshot through configured HF endpoints."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise LucidaUnavailableError(
            "huggingface_hub is required to prepare Lucida. Run Prepare / Download again "
            "after installing the application dependencies. / 准备 Lucida 需要 huggingface_hub。"
        ) from exc

    model_dir = Path(get_lucida_model_dir())
    model_dir.mkdir(parents=True, exist_ok=True)
    endpoints = get_hf_endpoint_order(model_name="Lucida")
    last_error: Exception | None = None
    selected_endpoint = ""
    for endpoint in endpoints:
        try:
            snapshot_download(
                repo_id=LUCIDA_MODEL_ID,
                revision=LUCIDA_REVISION,
                local_dir=str(model_dir),
                allow_patterns=list(LUCIDA_REQUIRED_FILES),
                endpoint=endpoint,
            )
            selected_endpoint = endpoint
            break
        except Exception as exc:  # noqa: BLE001 - HF exposes multiple transport error types
            last_error = exc
            logger.warning(
                "Lucida checkpoint download failed",
                extra={
                    "model_id": LUCIDA_MODEL_ID,
                    "revision": LUCIDA_REVISION,
                    "endpoint": endpoint_label(endpoint),
                    **hf_error_metadata(exc),
                },
            )
    else:
        attempted = ", ".join(endpoint_label(endpoint) for endpoint in endpoints)
        raise LucidaUnavailableError(
            f"{format_hf_download_error(model_id=LUCIDA_MODEL_ID, revision=LUCIDA_REVISION, endpoint=attempted, error=last_error)} "
            "/ Lucida 下载失败。请检查网络后重试“准备 / 下载”。"
        ) from last_error

    missing = log_model_artifact_status(
        logger,
        model_id=LUCIDA_MODEL_ID,
        revision=LUCIDA_REVISION,
        endpoint=selected_endpoint or "unknown",
        model_dir=model_dir,
        required_files=LUCIDA_REQUIRED_FILES,
    )
    if missing:
        raise LucidaUnavailableError(
            f"Lucida download completed but required files are missing in {model_dir}: "
            f"{', '.join(missing)}. Retry Prepare / Download. / Lucida 下载不完整，请重试。"
        )
    return str(model_dir.resolve())


def soft_alpha_to_mask(alpha: np.ndarray, source_size: tuple[int, int]) -> Image.Image:
    """Convert a normalized alpha plane to a source-sized grayscale mask."""
    width, height = source_size
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid source size for Lucida mask: {source_size!r}")

    normalized = np.asarray(alpha, dtype=np.float32)
    if normalized.ndim != 2:
        raise ValueError(
            f"Lucida alpha must be a 2D plane, received shape {normalized.shape!r}"
        )
    if not np.isfinite(normalized).all():
        raise ValueError("Lucida alpha contains non-finite values")

    alpha_bytes = np.rint(np.clip(normalized, 0.0, 1.0) * 255.0).astype(np.uint8)
    mask = Image.fromarray(alpha_bytes, mode="L")
    if mask.size != source_size:
        mask = mask.resize(source_size, Image.Resampling.LANCZOS)
    return mask


def _resolve_device(torch_module: object, use_gpu: bool) -> str:
    cuda = getattr(torch_module, "cuda", None)
    cuda_available = bool(cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available())
    if use_gpu and not cuda_available:
        logger.warning("Lucida GPU was requested but CUDA is unavailable; using CPU")
    return "cuda" if use_gpu and cuda_available else "cpu"


def _load_model(device: str) -> _LucidaModel:
    cached = _model_by_device.get(device)
    if cached is not None:
        return cached

    with _model_lock:
        cached = _model_by_device.get(device)
        if cached is not None:
            return cached

        checkpoint_path = get_checkpoint_path()
        if checkpoint_path is None:
            model_dir = Path(get_lucida_model_dir())
            missing = ", ".join(missing_checkpoint_files(model_dir))
            raise LucidaUnavailableError(
                "Lucida model files are missing. Open Model Manager and run "
                f"Prepare / Download for Lucida. Expected {model_dir}; missing: {missing}. "
                "/ Lucida 模型文件缺失。请在模型管理器中为 Lucida 执行“准备 / 下载”。"
            )

        try:
            from transformers import AutoModelForImageSegmentation
        except ImportError as exc:
            raise LucidaUnavailableError(
                "Lucida runtime is unavailable. Open Model Manager and run Prepare / Download "
                "for Lucida, then restart the app. / Lucida 运行环境不可用。请在模型管理器中"
                "执行“准备 / 下载”，然后重启应用。"
            ) from exc

        try:
            # SECURITY: Lucida requires repository Python code. Loading only the
            # locally prepared, commit-pinned snapshot limits that trust boundary.
            with exclusive_ai_runtime("lucida-load"):
                loaded = AutoModelForImageSegmentation.from_pretrained(
                    checkpoint_path,
                    trust_remote_code=True,
                    local_files_only=True,
                    use_safetensors=True,
                )
                model = cast(_LucidaModel, loaded)
                model.to(device)
                model.eval()
        except ImportError as exc:
            raise LucidaUnavailableError(
                f"Lucida remote-code dependency is missing while loading {checkpoint_path}: {exc}. "
                "Run Prepare / Download for Lucida, then restart the app. / Lucida 远程代码依赖缺失。"
                "请为 Lucida 执行“准备 / 下载”，然后重启应用。"
            ) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            error_type = LucidaCudaError if device == "cuda" else LucidaUnavailableError
            raise error_type(
                f"Lucida failed to load on {device} from {checkpoint_path}: {exc}. "
                f"/ Lucida 无法在 {device} 上加载。"
            ) from exc

        _model_by_device[device] = model
        return model


def _generate_on_device(
    source: Image.Image,
    device: str,
    torch_module: _TorchModule,
    transforms_module: _TransformsModule,
) -> Image.Image:
    source_size = source.size
    rgb = source.convert("RGB")
    model = _load_model(device)
    preprocess = transforms_module.Compose(
        (
            transforms_module.Resize((1024, 1024)),
            transforms_module.ToTensor(),
            transforms_module.Normalize(
                mean=(0.485, 0.456, 0.406),
                std=(0.229, 0.224, 0.225),
            ),
        )
    )

    try:
        input_tensor = cast(_Tensor, preprocess(rgb)).unsqueeze(0)
        # Only reached by generate_subject_mask <- mask_service.generate_auto_mask,
        # a single-image preview the user is waiting on. No batch caller exists.
        with exclusive_ai_runtime("lucida-inference", priority=PRIORITY_INTERACTIVE):
            input_tensor = input_tensor.to(device)
            with torch_module.inference_mode():
                outputs = model(input_tensor)
            if not outputs:
                raise LucidaInferenceError("Lucida returned no alpha outputs")
            alpha = outputs[-1].sigmoid()[0, 0].detach().cpu().float().numpy()
        return soft_alpha_to_mask(alpha, source_size)
    except LucidaError:
        raise
    except (OSError, RuntimeError) as exc:
        error_type = LucidaCudaError if device == "cuda" else LucidaInferenceError
        raise error_type(
            f"Lucida inference failed on {device}: {exc}. / Lucida 在 {device} 上推理失败。"
        ) from exc
    except (IndexError, TypeError, ValueError) as exc:
        raise LucidaInferenceError(
            f"Lucida could not generate a training mask: {exc}. / Lucida 无法生成训练遮罩。"
        ) from exc


def generate_subject_mask(source: Image.Image, use_gpu: bool) -> Image.Image:
    """Generate a source-sized soft Lucida alpha mask without saving it."""
    try:
        import torch
        from torchvision import transforms
    except (ImportError, OSError, RuntimeError) as exc:
        raise LucidaUnavailableError(
            "Lucida runtime packages are missing. Open Model Manager and run Prepare / Download "
            "for Lucida, then restart the app. / Lucida 运行依赖缺失。请在模型管理器中"
            f"执行“准备 / 下载”，然后重启应用。原因：{exc}"
        ) from exc

    torch_module = cast(_TorchModule, torch)
    transforms_module = cast(_TransformsModule, transforms)
    device = _resolve_device(torch, use_gpu)
    try:
        return _generate_on_device(source, device, torch_module, transforms_module)
    except LucidaCudaError as exc:
        if device != "cuda":
            raise
        logger.warning(
            "Lucida CUDA execution failed; retrying explicitly on CPU",
            extra={"device": device, "error": str(exc)},
        )
        # Tail of the same interactive operation: the CPU retry the user is
        # still waiting on, so it keeps the lane rather than dropping to normal.
        with exclusive_ai_runtime("lucida-cleanup", priority=PRIORITY_INTERACTIVE):
            _model_by_device.pop("cuda", None)
            cuda = getattr(torch, "cuda", None)
            empty_cache = getattr(cuda, "empty_cache", None)
            if callable(empty_cache):
                empty_cache()
        return _generate_on_device(source, "cpu", torch_module, transforms_module)
