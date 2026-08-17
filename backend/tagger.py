"""
WD14 Tagger using ONNX Runtime for image tagging.
Supports automatic model download from HuggingFace and local model loading.
"""

import csv
import gc
import hmac
import os
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    import onnxruntime as ort  # type: ignore
from PIL import Image

import config
from config import (
    TAGGER_MODELS as MODELS,
    DEFAULT_TAGGER_MODEL as DEFAULT_MODEL,
    TAGGER_GENERAL_THRESHOLD,
    TAGGER_CHARACTER_THRESHOLD,
    TAGGER_USE_GPU,
    get_wd14_model_dir,
)
from ai_runtime_guard import (
    PRIORITY_NORMAL,
    exclusive_ai_runtime,
    looks_like_cuda_oom,
)
from model_download_sources import endpoint_label, get_hf_endpoint_order
from tag_writer_provenance import ModelFileIdentity, model_file_snapshot
from utils.path_validation import normalize_user_path

logger = logging.getLogger(__name__)
CUSTOM_WD14_PROFILE_MODEL = "wd-swinv2-tagger-v3"

# Bounded thread pool that overlaps CPU-bound image decode/preprocess so the GPU
# is not left idle waiting on a single core between batches. PIL decode/resize and
# numpy release the GIL, so threads give a real speedup. Shared + lazily created
# (mirrors thumbnail_cache); the GPU inference itself stays serialized by
# exclusive_ai_runtime, so this pool only parallelizes the CPU preprocessing.
_PREPROCESS_MAX_WORKERS = min(8, (os.cpu_count() or 4))
_preprocess_executor: Optional[ThreadPoolExecutor] = None
_preprocess_executor_lock = threading.Lock()


def _get_preprocess_executor() -> ThreadPoolExecutor:
    global _preprocess_executor
    if _preprocess_executor is None:
        with _preprocess_executor_lock:
            if _preprocess_executor is None:
                _preprocess_executor = ThreadPoolExecutor(
                    max_workers=_PREPROCESS_MAX_WORKERS,
                    thread_name_prefix="wd14-preprocess",
                )
    return _preprocess_executor


# Will be imported lazily
ort = None
hf_hub = None
# Serializes the one-time heavy import so two concurrent first-callers don't
# both run prepare_onnxruntime_environment() / import onnxruntime at once.
_ensure_imports_lock = threading.Lock()


def _ensure_imports():
    """Lazily import heavy dependencies."""
    global ort, hf_hub
    # Fast path: both already imported, no lock needed.
    if ort is not None and hf_hub is not None:
        return
    with _ensure_imports_lock:
        if ort is None:
            from runtime_env import prepare_onnxruntime_environment

            prepare_onnxruntime_environment()
            import onnxruntime as ort_module  # type: ignore

            ort = ort_module
            preload = getattr(ort, "preload_dlls", None)
            if callable(preload):
                try:
                    preload()
                except Exception as exc:
                    logger.debug("onnxruntime.preload_dlls() was not usable: %s", exc)
        if hf_hub is None:
            import huggingface_hub as hf_module

            hf_hub = hf_module


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the pure method families of WD14Tagger live in the
# tagger_* sibling modules as mixins.
# THIS module remains a real FILE named ``tagger`` and the single monkeypatch
# surface:
#   * The LAZY-IMPORT family stays DEFINED here in one namespace -- the
#     ``ort`` / ``hf_hub`` globals, _ensure_imports_lock, and _ensure_imports
#     -- along with EVERY ``ort.`` read site (_build_session_options /
#     _create_session / _load_locked / _recreate_session stay on the class
#     body below). The reader suites patch ``tagger.ort`` / ``tagger.hf_hub``
#     on this module; the moved hf_hub readers (tagger_download) and the
#     moved executor reader (tagger_preprocess._preprocess_paths) resolve
#     back through _svc() at call time so those patches keep landing.
#   * The SINGLETON family stays whole at the bottom of this file --
#     _tagger / _current_settings / _tagger_lock / _ConfiguredTaggerProxy /
#     get_tagger -- as does the preprocess-executor pair above.
#   * The GPU/OOM machinery (_run_true_batch_with_backoff /
#     _fallback_to_cpu_session / _recreate_session) is byte-verbatim below.
# The header import block above is kept verbatim (per-file F401 ignore in
# pyproject.toml) so every historical attribute keeps resolving here.
# ---------------------------------------------------------------------------
from tagger_download import _DownloadMixin
from tagger_inference import _InferenceFlowMixin
from tagger_preprocess import _PreprocessMixin
from tagger_scoring import _ScoringMixin
from tagger_tagtable import _TagTableMixin


class WD14Tagger(
    _ScoringMixin,
    _TagTableMixin,
    _DownloadMixin,
    _PreprocessMixin,
    _InferenceFlowMixin,
):
    """WD14 Tagger for anime-style image tagging using ONNX."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        model_path: Optional[str] = None,
        tags_path: Optional[str] = None,
        model_dir: Optional[str] = None,
        threshold: float = TAGGER_GENERAL_THRESHOLD,
        character_threshold: float = TAGGER_CHARACTER_THRESHOLD,
        use_gpu: bool = TAGGER_USE_GPU,
    ):
        """
        Initialize the tagger.

        Args:
            model_name: One of the supported model names (for auto-download)
            model_path: Direct path to .onnx file (overrides model_name)
            tags_path: Direct path to selected_tags.csv or metadata JSON (optional if model-adjacent metadata exists)
            model_dir: Directory to store/load models. If None, uses config default.
            threshold: Confidence threshold for general tags
            character_threshold: Confidence threshold for character tags
            use_gpu: Whether to use GPU acceleration (CUDA) if available
        """
        _ensure_imports()

        self.model_name = self._resolve_model_profile(model_name, model_path)
        self.model_path = normalize_user_path(model_path) if model_path else model_path
        self.tags_path = normalize_user_path(tags_path) if tags_path else tags_path
        self.model_dir = model_dir or get_wd14_model_dir()
        self.threshold = threshold
        self.character_threshold = character_threshold
        self.use_gpu = use_gpu

        self.session: Optional["ort.InferenceSession"] = None
        self.tags: List[str] = []
        self.general_tags: List[Tuple[int, str]] = []
        self.copyright_tags: List[Tuple[int, str]] = []
        self.character_tags: List[Tuple[int, str]] = []
        self.rating_tags: List[Tuple[int, str]] = []
        self.rating_indices: Dict[str, int] = {}  # Map rating name to index
        self._general_category_overrides: Dict[str, str] = {}

        self._loaded = False
        # Guards load() so two concurrent callers can't double-load the ~1GB
        # model. get_tagger() returns the instance under _tagger_lock but defers
        # .load() to the first tag() call, which runs OUTSIDE that lock, so the
        # idempotency check must be protected here at the instance level.
        self._load_lock = threading.Lock()
        self._resolved_model_path: Optional[str] = None
        self._resolved_tags_path: Optional[str] = None
        self._loaded_model_file_identity: Optional[ModelFileIdentity] = None
        self._loaded_model_file_sha256: Optional[str] = None
        self._input_name: Optional[str] = None
        self._input_hw: Tuple[int, int] = (448, 448)
        self._supports_true_batch: bool = False
        self._input_layout: str = "nhwc"
        self._input_normalization: str = "wd14_bgr"
        self._output_activation: str = "identity"
        self._output_index: int = 0
        self._pad_color: Tuple[int, int, int] = (255, 255, 255)
        self._metadata_format: str = "wd14_csv"
        self._resize_mode: str = "letterbox"
        self._rating_fallback_mode: str = "none"

        # Session recreation counters (BSOD prevention for GPU mode)
        self._images_since_session_create: int = 0
        self._session_refresh_interval: int = 0
        self._learned_stable_gpu_batch_size: Optional[int] = None
        self._successful_gpu_batch_runs: int = 0

    @staticmethod
    def _resolve_model_profile(model_name: str, model_path: Optional[str]) -> str:
        """Map custom-local aliases to a real model profile."""
        if not model_path:
            return model_name
        normalized = str(model_name or "").strip().lower()
        custom_profile_aliases = {
            "",
            "custom",
            "wd14",
            "wd14-compatible",
            "wd14_csv",
        }
        if normalized in custom_profile_aliases:
            return CUSTOM_WD14_PROFILE_MODEL
        return model_name

    def _build_session_options(self, gpu_enabled: bool) -> "ort.SessionOptions":
        """Build ONNX Runtime session options optimized for the current hardware mode."""
        sess_options = ort.SessionOptions()

        import multiprocessing

        cpu_count = max(1, multiprocessing.cpu_count())
        if gpu_enabled:
            num_threads = 2
        else:
            # Leave headroom instead of pinning every core: a long CPU tagging run held at
            # 100% on all cores can trip a CPU machine-check / thermal event on marginal
            # hardware. Default to half-minus-one core; override with TAGGER_CPU_THREADS.
            env_threads = os.environ.get("TAGGER_CPU_THREADS", "").strip()
            if env_threads.isdigit() and int(env_threads) > 0:
                num_threads = min(cpu_count, int(env_threads))
            else:
                num_threads = min(cpu_count, max(2, (cpu_count // 2) - 1))

        sess_options.intra_op_num_threads = num_threads
        sess_options.inter_op_num_threads = max(1, num_threads // 2)
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        sess_options.enable_cpu_mem_arena = not gpu_enabled
        sess_options.enable_mem_pattern = not gpu_enabled

        logger.debug(
            "ONNX session configured with %s intra / %s inter thread(s), gpu_enabled=%s, mem_arena=%s",
            num_threads,
            max(1, num_threads // 2),
            gpu_enabled,
            not gpu_enabled,
        )
        return sess_options

    def _create_verified_session(
        self,
        model_path: str,
        sess_options: "ort.SessionOptions",
        providers: List[str],
    ) -> "ort.InferenceSession":
        identity_before, sha256_before = model_file_snapshot(model_path)
        session = ort.InferenceSession(
            model_path,
            sess_options=sess_options,
            providers=providers,
        )
        identity_after, sha256_after = model_file_snapshot(model_path)
        if (
            identity_before != identity_after
            or not hmac.compare_digest(sha256_before, sha256_after)
        ):
            raise RuntimeError(
                "Model file changed while ONNX Runtime was loading the session"
            )
        self._loaded_model_file_identity = identity_after
        self._loaded_model_file_sha256 = sha256_after
        return session

    def _create_session(
        self,
        model_path: str,
        tags_path: str,
        sess_options: "ort.SessionOptions",
        providers: List[str],
    ) -> "ort.InferenceSession":
        """Create an ONNX session, retrying once after repairing a corrupted model."""
        try:
            return self._create_verified_session(
                model_path,
                sess_options,
                providers,
            )
        except Exception as e:
            error_msg = str(e)
            if not self.model_path and (
                "INVALID_PROTOBUF" in error_msg
                or "Protobuf parsing failed" in error_msg
            ):
                logger.error(f"Model file is corrupted: {model_path}")
                logger.info("Attempting to delete and re-download...")

                try:
                    os.remove(model_path)
                    logger.info("Deleted corrupted model file.")
                except Exception as del_error:
                    logger.warning(f"Could not delete corrupted file: {del_error}")

                logger.info("Re-downloading model...")
                model_path, tags_path = self._download_model()
                self._resolved_model_path = model_path
                self._resolved_tags_path = tags_path
                try:
                    return self._create_verified_session(
                        model_path,
                        sess_options,
                        providers,
                    )
                except Exception as e2:
                    raise RuntimeError(
                        f"Failed to load model even after re-download. Error: {e2}"
                    ) from e2

            raise RuntimeError(f"Failed to load ONNX model: {error_msg}") from e

    def load(self):
        """Load the model and tags. Idempotent and thread-safe.

        Double-checked locking: the common already-loaded case returns without
        taking the lock; the slow path serializes the one-time init so two
        concurrent first-callers can't both load the model.
        """
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._load_locked()

    def _load_locked(self):
        """Perform the one-time model + tag load. Caller must hold _load_lock."""
        model_path, tags_path = self._get_model_paths()
        self._resolved_model_path = model_path
        self._resolved_tags_path = tags_path

        model_config = MODELS.get(self.model_name, {})
        self._input_layout = str(model_config.get("input_layout", "nhwc")).lower()
        self._input_normalization = str(
            model_config.get("input_normalization", "wd14_bgr")
        ).lower()
        self._output_activation = str(
            model_config.get("output_activation", "identity")
        ).lower()
        self._output_index = int(model_config.get("output_index", 0))
        self._metadata_format = str(
            model_config.get("metadata_format", "wd14_csv")
        ).lower()
        self._resize_mode = str(model_config.get("resize_mode", "letterbox")).lower()
        self._rating_fallback_mode = str(
            model_config.get("rating_fallback_mode", "none")
        ).lower()
        pad_color = model_config.get("pad_color", [255, 255, 255])
        if isinstance(pad_color, (list, tuple)) and len(pad_color) >= 3:
            self._pad_color = (int(pad_color[0]), int(pad_color[1]), int(pad_color[2]))

        # Load ONNX model with error handling
        logger.info(f"Loading model from {model_path}...")

        # Choose providers based on use_gpu setting.
        # Provider preference: CUDA (NVIDIA) -> DirectML (Intel/AMD on Windows) -> CPU.
        # Providers not actually installed are filtered out below, so this is safe
        # for NVIDIA-only setups: DmlExecutionProvider simply falls off the list
        # when onnxruntime-gpu is installed without DirectML support.
        if self.use_gpu:
            providers = [
                "CUDAExecutionProvider",
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            ]
        else:
            providers = ["CPUExecutionProvider"]

        available_providers = ort.get_available_providers()
        providers = [p for p in providers if p in available_providers]
        session_uses_gpu = self.use_gpu and (
            "CUDAExecutionProvider" in providers or "DmlExecutionProvider" in providers
        )
        if self.use_gpu and not session_uses_gpu:
            logger.info(
                f"Using providers: {providers} (GPU requested, but no GPU execution provider is installed — running on CPU)"
            )
        elif self.use_gpu:
            logger.info(f"Using providers: {providers} (GPU enabled)")
        else:
            logger.info(f"Using providers: {providers} (GPU disabled)")
        sess_options = self._build_session_options(gpu_enabled=session_uses_gpu)

        try:
            self.session = self._create_session(
                model_path, tags_path, sess_options, providers
            )
        except RuntimeError as e:
            if session_uses_gpu:
                logger.warning(
                    "Failed to initialize %s on GPU, retrying on CPU: %s",
                    self.model_name,
                    e,
                )
                cpu_providers = ["CPUExecutionProvider"]
                cpu_options = self._build_session_options(gpu_enabled=False)
                self.session = self._create_session(
                    model_path, tags_path, cpu_options, cpu_providers
                )
                self.use_gpu = False
            else:
                raise

        if self.session is not None and not self._session_uses_gpu():
            self.use_gpu = False

        # Load tags
        self._load_tags(tags_path)
        self._refresh_session_metadata()

        self._loaded = True
        logger.info(f"Model loaded. Using providers: {self.session.get_providers()}")

    def _fallback_to_cpu_session(self, error: Exception) -> None:
        """Rebuild the active ONNX session on CPU."""
        if not self._resolved_model_path or not self._resolved_tags_path:
            raise RuntimeError(
                "Cannot switch tagger to CPU before model paths are resolved."
            ) from error

        logger.warning(
            "GPU inference failed for %s, switching to CPU: %s",
            self.model_name,
            error,
        )
        cpu_options = self._build_session_options(gpu_enabled=False)
        self.session = self._create_session(
            self._resolved_model_path,
            self._resolved_tags_path,
            cpu_options,
            ["CPUExecutionProvider"],
        )
        self.use_gpu = False
        self._learned_stable_gpu_batch_size = None
        self._successful_gpu_batch_runs = 0
        self._refresh_session_metadata()

    def _run_true_batch_with_backoff(
        self,
        prepared_inputs: List[np.ndarray],
        prepared_indices: List[int],
        image_paths: List[str],
        *,
        initial_chunk_size: Optional[int] = None,
        min_chunk_size: int = 1,
        retry_cooldown_seconds: float = 0.15,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        copyright_threshold: Optional[float] = None,
    ) -> Tuple[List[Optional[Dict[str, Any]]], Dict[str, Any]]:
        """Run batched inference with adaptive backoff before giving up on GPU."""
        results: List[Optional[Dict[str, Any]]] = [None] * len(image_paths)
        prepared_count = len(prepared_indices)
        if prepared_count == 0:
            return results, {
                "initial_chunk_size": 0,
                "final_chunk_size": 0,
                "backoff_steps": [],
                "used_cpu_fallback": False,
                "attempted_gpu_backoff": False,
            }

        preferred_chunk_size = max(
            1, min(initial_chunk_size or prepared_count, prepared_count)
        )
        learned_chunk_size = (
            self._learned_stable_gpu_batch_size if self._session_uses_gpu() else None
        )
        if learned_chunk_size:
            chunk_size = max(
                1, min(int(learned_chunk_size), preferred_chunk_size, prepared_count)
            )
        else:
            chunk_size = preferred_chunk_size
        min_chunk_size = max(1, min(min_chunk_size, chunk_size))
        initial_chunk_size = chunk_size
        backoff_steps: List[Dict[str, Any]] = []
        attempted_gpu_backoff = False
        used_cpu_fallback = False
        cursor = 0
        raised_after_stable_runs = False

        while cursor < prepared_count:
            current_chunk_size = min(chunk_size, prepared_count - cursor)
            current_inputs = prepared_inputs[cursor : cursor + current_chunk_size]
            current_indices = prepared_indices[cursor : cursor + current_chunk_size]

            try:
                batch_input = np.stack(current_inputs, axis=0)
                output = self._run_inference(batch_input, allow_gpu_fallback=False)
                for output_index, source_index in enumerate(current_indices):
                    results[source_index] = self._process_probs(
                        output[output_index],
                        threshold=threshold,
                        character_threshold=character_threshold,
                        copyright_threshold=copyright_threshold,
                    )
                self._finalize_processed_images(len(current_indices))
                if self._session_uses_gpu():
                    self._learned_stable_gpu_batch_size = max(
                        int(self._learned_stable_gpu_batch_size or 1),
                        int(current_chunk_size),
                    )
                    self._successful_gpu_batch_runs += 1
                    if (
                        not raised_after_stable_runs
                        and current_chunk_size < preferred_chunk_size
                        and self._successful_gpu_batch_runs >= 2
                    ):
                        next_candidate = min(
                            preferred_chunk_size,
                            max(current_chunk_size + 1, current_chunk_size * 2),
                        )
                        if next_candidate > chunk_size:
                            chunk_size = next_candidate
                            raised_after_stable_runs = True
                del batch_input
                del output
                cursor += current_chunk_size
                continue
            except Exception as error:
                session_uses_gpu = self._session_uses_gpu()
                is_oom = looks_like_cuda_oom(error)
                logger.warning(
                    "True batched WD14 inference failed for chunk size %d on %s (%s): %s",
                    current_chunk_size,
                    "GPU" if session_uses_gpu else "CPU",
                    "OOM" if is_oom else "non-OOM error",
                    error,
                )

                # Only halve the GPU batch for genuine out-of-memory errors. A
                # non-OOM GPU failure (driver glitch, bad input) won't be cured by
                # a smaller batch, so skip straight to the CPU fallback below
                # instead of wastefully halving 64 -> 1 first.
                if session_uses_gpu and current_chunk_size > min_chunk_size and is_oom:
                    attempted_gpu_backoff = True
                    next_chunk_size = max(min_chunk_size, current_chunk_size // 2)
                    if (
                        next_chunk_size == current_chunk_size
                        and current_chunk_size > min_chunk_size
                    ):
                        next_chunk_size = current_chunk_size - 1
                    backoff_steps.append(
                        {
                            "from": current_chunk_size,
                            "to": next_chunk_size,
                            "mode": "gpu_backoff",
                            "error": str(error),
                        }
                    )
                    self._learned_stable_gpu_batch_size = max(
                        1,
                        min(
                            next_chunk_size,
                            int(self._learned_stable_gpu_batch_size or next_chunk_size),
                        ),
                    )
                    self._successful_gpu_batch_runs = 0
                    raised_after_stable_runs = False
                    self._recreate_session()
                    if retry_cooldown_seconds > 0:
                        time.sleep(retry_cooldown_seconds)
                    chunk_size = next_chunk_size
                    continue

                if session_uses_gpu:
                    attempted_gpu_backoff = True
                    backoff_steps.append(
                        {
                            "from": current_chunk_size,
                            "to": 1,
                            "mode": "cpu_fallback",
                            "error": str(error),
                        }
                    )
                    self._fallback_to_cpu_session(error)
                    used_cpu_fallback = True
                    chunk_size = 1
                    self._successful_gpu_batch_runs = 0
                    raised_after_stable_runs = False
                    if retry_cooldown_seconds > 0:
                        time.sleep(retry_cooldown_seconds)
                    continue

                # CPU mode: also backoff chunk size if batch > 1
                if current_chunk_size > 1:
                    next_chunk_size = max(1, current_chunk_size // 2)
                    backoff_steps.append(
                        {
                            "from": current_chunk_size,
                            "to": next_chunk_size,
                            "mode": "cpu_backoff",
                            "error": str(error),
                        }
                    )
                    logger.warning(
                        "CPU batch inference failed at chunk %d, backing off to %d",
                        current_chunk_size,
                        next_chunk_size,
                    )
                    chunk_size = next_chunk_size
                    gc.collect()
                    if retry_cooldown_seconds > 0:
                        time.sleep(retry_cooldown_seconds * 2)
                    continue

                for prepared_index, source_index in enumerate(current_indices):
                    try:
                        single_input = np.expand_dims(
                            current_inputs[prepared_index], axis=0
                        )
                        output = self._run_inference(single_input)
                        results[source_index] = self._process_probs(
                            output[0],
                            threshold=threshold,
                            character_threshold=character_threshold,
                        )
                        self._finalize_processed_images(1)
                        del single_input
                        del output
                    except Exception as single_error:
                        logger.error(
                            "Error tagging %s: %s",
                            image_paths[source_index],
                            single_error,
                        )
                        results[source_index] = self._build_empty_result(
                            str(single_error)
                        )
                cursor += current_chunk_size

        return results, {
            "initial_chunk_size": initial_chunk_size,
            "final_chunk_size": chunk_size,
            "backoff_steps": backoff_steps,
            "used_cpu_fallback": used_cpu_fallback,
            "attempted_gpu_backoff": attempted_gpu_backoff,
        }

    def _recreate_session(self) -> None:
        """
        Destroy and rebuild the ONNX inference session to release VRAM.

        ONNX Runtime does not expose a VRAM release API, so after extended GPU
        inference the only way to reclaim leaked device memory is to delete the
        session object entirely and create a fresh one.  This prevents the
        accumulative VRAM leak that leads to Windows BSOD after ~300 images.
        """
        if not self._resolved_model_path or not self._resolved_tags_path:
            logger.warning("Cannot recreate session: model paths not yet resolved.")
            return

        logger.info(
            "Recreating ONNX session after %d images to release VRAM.",
            self._images_since_session_create,
        )

        try:
            if self.session is not None:
                del self.session
                self.session = None
            gc.collect()

            if self.use_gpu:
                providers = [
                    "CUDAExecutionProvider",
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                ]
            else:
                providers = ["CPUExecutionProvider"]

            available_providers = ort.get_available_providers()
            providers = [p for p in providers if p in available_providers]

            session_uses_gpu = self.use_gpu and (
                "CUDAExecutionProvider" in providers
                or "DmlExecutionProvider" in providers
            )
            sess_options = self._build_session_options(gpu_enabled=session_uses_gpu)

            self.session = self._create_session(
                self._resolved_model_path,
                self._resolved_tags_path,
                sess_options,
                providers,
            )
            if self.session is not None and not self._session_uses_gpu():
                self.use_gpu = False
            self._refresh_session_metadata()
            self._images_since_session_create = 0
            self._successful_gpu_batch_runs = 0
            logger.info(
                "ONNX session recreated successfully. Providers: %s",
                self.session.get_providers(),
            )
        except Exception as exc:
            logger.error("Failed to recreate ONNX session: %s", exc)
            # Attempt CPU fallback if GPU recreation failed
            if self.use_gpu:
                try:
                    self._fallback_to_cpu_session(exc)
                    self._images_since_session_create = 0
                except Exception as fallback_exc:
                    logger.error(
                        "CPU fallback after session recreation failure also failed: %s",
                        fallback_exc,
                    )
                    raise


# Singleton instance
_tagger = None
_current_settings = {}
_tagger_lock = threading.Lock()


class _ConfiguredTaggerProxy:
    """Attach request-specific thresholds to a shared loaded tagger instance."""

    def __init__(
        self,
        tagger: WD14Tagger,
        *,
        threshold: float,
        character_threshold: float,
        copyright_threshold: Optional[float] = None,
    ):
        self._tagger = tagger
        self._threshold = threshold
        self._character_threshold = character_threshold
        self._copyright_threshold = (
            copyright_threshold if copyright_threshold is not None else threshold
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tagger, name)

    def tag(
        self,
        image_path: str,
        *,
        threshold: Optional[float] = None,
        character_threshold: Optional[float] = None,
        copyright_threshold: Optional[float] = None,
        priority: int = PRIORITY_NORMAL,
    ) -> Dict[str, Any]:
        return self._tagger.tag(
            image_path,
            threshold=self._threshold if threshold is None else threshold,
            character_threshold=self._character_threshold
            if character_threshold is None
            else character_threshold,
            copyright_threshold=self._copyright_threshold
            if copyright_threshold is None
            else copyright_threshold,
            priority=priority,
        )

    def tag_batch(self, image_paths: List[str], **kwargs: Any) -> Any:
        kwargs.setdefault("threshold", self._threshold)
        kwargs.setdefault("character_threshold", self._character_threshold)
        kwargs.setdefault("copyright_threshold", self._copyright_threshold)
        return self._tagger.tag_batch(image_paths, **kwargs)


def get_tagger(
    model_name: str = DEFAULT_MODEL,
    model_path: Optional[str] = None,
    tags_path: Optional[str] = None,
    threshold: float = 0.35,
    character_threshold: float = 0.85,
    copyright_threshold: Optional[float] = None,
    use_gpu: bool = True,
    force_reload: bool = False,
) -> WD14Tagger:
    """Get or create the tagger instance."""
    global _tagger, _current_settings
    resolved_model_name = model_name or DEFAULT_MODEL

    with _tagger_lock:
        new_settings = {
            "model_name": WD14Tagger._resolve_model_profile(
                resolved_model_name, model_path
            ),
            "model_path": model_path,
            "tags_path": tags_path,
            "use_gpu": use_gpu,
        }

        # Reload if settings changed or forced
        if force_reload or _tagger is None or new_settings != _current_settings:
            _tagger = WD14Tagger(
                model_name=resolved_model_name,
                model_path=model_path,
                tags_path=tags_path,
                threshold=threshold,
                character_threshold=character_threshold,
                use_gpu=use_gpu,
            )
            _current_settings = new_settings
        return _ConfiguredTaggerProxy(
            _tagger,
            threshold=threshold,
            character_threshold=character_threshold,
            copyright_threshold=copyright_threshold,
        )


def get_available_models() -> List[str]:
    """Get list of available model names."""
    return list(MODELS.keys())


def tag_image(image_path: str, threshold: float = 0.35) -> Dict[str, Any]:
    """Convenience function to tag a single image."""
    return get_tagger(threshold=threshold).tag(image_path)
