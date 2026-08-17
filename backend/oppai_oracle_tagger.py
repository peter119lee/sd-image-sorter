"""OppaiOracle V1.1 ONNX tagger backend.

Grio43/OppaiOracle is a from-scratch ViT (~247M params) anime tagger with a
19,294-tag general-only vocabulary. Unlike the WD14 ONNX models it ships with
TWO ONNX inputs (``pixel_values`` + ``padding_mask``) and is exported with the
sigmoid activation already inside the graph, so we cannot reuse the WD14
single-input inference path. This module is a self-contained implementation
that mirrors the public shape of :mod:`tagger.WD14Tagger` (``load`` /
``tag`` / ``tag_batch`` / ``set_session_refresh_interval``) so the existing
``TaggingService`` worker loop can drive it without special-casing every
call site.

Preprocessing reproduces ``preprocessing.json`` exactly:
    * letterbox to 448x448 keeping aspect ratio
    * pad with RGB ``[114, 114, 114]``
    * normalize ``(x/255 - 0.5) / 0.5`` per channel
    * channel order RGB, layout BCHW, dtype float32

The padding mask is True where pixels are padded and False on the actual
image rectangle so the model can mask attention away from the gray bars.
"""
from __future__ import annotations

import csv
import gc
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:  # pragma: no cover - type-only
    import onnxruntime as ort  # type: ignore

import config
from config import (
    TAGGER_MODELS,
    get_oppai_oracle_model_dir,
)
from ai_runtime_guard import exclusive_ai_runtime, looks_like_cuda_oom
from model_download_sources import endpoint_label, get_hf_endpoint_order
from utils.path_validation import normalize_user_path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "oppai-oracle-v1.1"

# Shared bounded pool to overlap CPU-bound image decode/letterbox so the GPU is
# not starved between batches (PIL + numpy release the GIL). GPU inference stays
# serialized by exclusive_ai_runtime; this only parallelizes preprocessing.
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
                    thread_name_prefix="oppai-preprocess",
                )
    return _preprocess_executor


def _normalize_oppai_model_alias(model_name: Optional[str]) -> str:
    """Map a caller-supplied OppaiOracle model name to a registered key.

    The Model Manager card and the Smart Tag wizard both refer to this
    tagger family by the unversioned id ``oppai-oracle`` (see
    ``services/model_service.py`` and ``services/smart_tag_service.py``).
    The tagger registry in ``config.py::TAGGER_MODELS`` keys the actual
    weights under the version-specific ``oppai-oracle-v1.1`` so future
    variants can sit side-by-side.

    Without this resolver, real-click verification (v3.2.2 T2) failed
    with::

        Failed to initialise pipeline: Unknown OppaiOracle model:
        oppai-oracle. Available: ['oppai-oracle-v1.1']

    The resolver translates the family id to the latest registered
    version. Unknown ids fall through unchanged so ``_model_config``
    can still raise the explicit "Unknown OppaiOracle model" error.
    """
    if not model_name:
        return DEFAULT_MODEL
    name = str(model_name).strip().lower()
    if not name:
        return DEFAULT_MODEL
    if name in TAGGER_MODELS:
        return name
    # Family-level alias used by the Model Manager UI and Smart Tag wizard.
    if name == "oppai-oracle":
        return DEFAULT_MODEL
    return name
DEFAULT_THRESHOLD = 0.7927  # P=R global from pr_thresholds.json (V1.1).
PAD_TAG_INDEX = 0
UNK_TAG_INDEX = 1
RATING_TAG_PREFIX = "rating:"

# Lazy-imported heavy modules (kept aligned with backend/tagger.py).
ort = None
hf_hub = None


def _ensure_imports() -> None:
    global ort, hf_hub
    if ort is None:
        from runtime_env import prepare_onnxruntime_environment
        prepare_onnxruntime_environment()
        import onnxruntime as ort_module  # type: ignore
        ort = ort_module
        preload = getattr(ort, "preload_dlls", None)
        if callable(preload):
            try:
                preload()
            except Exception as exc:  # pragma: no cover - depends on system
                logger.debug("onnxruntime.preload_dlls() was not usable: %s", exc)
    if hf_hub is None:
        import huggingface_hub as hf_module
        hf_hub = hf_module


def letterbox_to_square(
    image: Image.Image,
    *,
    target: int,
    pad_color: Tuple[int, int, int],
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Letterbox ``image`` into a target-sized RGB square.

    Returns the canvas plus the (paste_x, paste_y, new_w, new_h) rectangle
    of the original image inside it so the caller can build a padding mask.
    """
    image = image.convert("RGB")
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"degenerate image size: {src_w}x{src_h}")
    ratio = min(target / src_w, target / src_h)
    new_w = max(1, int(round(src_w * ratio)))
    new_h = max(1, int(round(src_h * ratio)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target, target), pad_color)
    paste_x = (target - new_w) // 2
    paste_y = (target - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))
    return canvas, (paste_x, paste_y, new_w, new_h)


def preprocess_image(
    image: Image.Image,
    *,
    target: int = 448,
    pad_color: Tuple[int, int, int] = (114, 114, 114),
) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(pixel_values [3,H,W] float32, padding_mask [H,W] bool)``.

    Reproduces the math documented in V1.1_onnx/preprocessing.json:
        normalize: (x/255 - 0.5) / 0.5 per channel
        layout:    BCHW, RGB
        mask:      True = padded pixel, False = valid pixel
    """
    canvas, (paste_x, paste_y, new_w, new_h) = letterbox_to_square(
        image, target=target, pad_color=pad_color
    )
    arr = np.array(canvas, dtype=np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    pixel_values = np.transpose(arr, (2, 0, 1)).astype(np.float32, copy=False)

    padding_mask = np.ones((target, target), dtype=bool)
    padding_mask[paste_y : paste_y + new_h, paste_x : paste_x + new_w] = False
    return pixel_values, padding_mask


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the loader / inference method families of
# OppaiOracleTagger live in the oppai_oracle_loader / oppai_oracle_inference
# sibling modules as mixins. THIS
# module remains a real FILE named ``oppai_oracle_tagger`` and the single
# monkeypatch surface:
#   * The LAZY-IMPORT family stays DEFINED here in one namespace -- the
#     ``ort`` / ``hf_hub`` globals and _ensure_imports. The moved readers
#     (_build_session_options / _create_session / load / _recreate_gpu_session
#     / _download_with_fallback) resolve them back through _svc() at call
#     time so ``oppai_oracle_tagger.ort`` / ``.hf_hub`` patches keep landing.
#   * The PREPROCESS-EXECUTOR family stays whole above -- _preprocess_executor
#     / its lock / _get_preprocess_executor -- as do the free functions
#     letterbox_to_square / preprocess_image; the moved _preprocess_paths and
#     tag resolve them back through _svc() at call time (the line-770
#     deep-read pin in tests/test_oppai_pins.py).
#   * The SINGLETON family stays whole at the bottom of this file --
#     _tagger_lock / _tagger_singleton / _singleton_settings /
#     get_oppai_oracle_tagger -- with _normalize_oppai_model_alias and the
#     DEFAULT_MODEL / DEFAULT_THRESHOLD / PAD / UNK / RATING constants, so
#     the sys.modules whole-module swap in tests/test_model_service_pins.py
#     and the alias reader keep resolving everything on THIS module.
# The header import block above is kept verbatim (per-file F401 ignore in
# pyproject.toml) so every historical attribute keeps resolving here.
# ---------------------------------------------------------------------------
from oppai_oracle_inference import _InferenceMixin
from oppai_oracle_loader import _LoaderMixin


class OppaiOracleTagger(_LoaderMixin, _InferenceMixin):
    """OppaiOracle V1.1 ONNX tagger with WD14Tagger-compatible public API."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        model_path: Optional[str] = None,
        tags_path: Optional[str] = None,
        model_dir: Optional[str] = None,
        threshold: float = DEFAULT_THRESHOLD,
        character_threshold: float = 1.0,
        use_gpu: bool = True,
    ) -> None:
        _ensure_imports()
        self.model_name = _normalize_oppai_model_alias(model_name)
        self.model_path: Optional[str] = normalize_user_path(model_path) if model_path else None
        self.tags_path: Optional[str] = normalize_user_path(tags_path) if tags_path else None
        self.model_dir: str = model_dir or get_oppai_oracle_model_dir()
        self.threshold = float(threshold)
        # OppaiOracle has no character category, but we keep the parameter to
        # match the WD14Tagger constructor used by the tagging service.
        self.character_threshold = float(character_threshold)
        self.use_gpu = bool(use_gpu)

        self.session: Optional["ort.InferenceSession"] = None
        self.tags: List[str] = []
        # general_tags / character_tags / rating_tags mirror WD14Tagger so the
        # tagging-service result post-processing keeps working unchanged.
        self.general_tags: List[Tuple[int, str]] = []
        self.character_tags: List[Tuple[int, str]] = []
        self.rating_tags: List[Tuple[int, str]] = []
        self.rating_indices: Dict[str, int] = {}

        self._loaded = False
        self._resolved_model_path: Optional[str] = None
        self._resolved_tags_path: Optional[str] = None
        self._target = 448
        self._pad_color: Tuple[int, int, int] = (114, 114, 114)
        self._supports_true_batch = True
        self._session_refresh_interval = 0
        self._images_since_session_create = 0


# ----- module-level singleton --------------------------------------------

_tagger_lock = threading.Lock()
_tagger_singleton: Optional[OppaiOracleTagger] = None
_singleton_settings: Dict[str, Any] = {}


def get_oppai_oracle_tagger(
    model_name: str = DEFAULT_MODEL,
    model_path: Optional[str] = None,
    tags_path: Optional[str] = None,
    threshold: float = DEFAULT_THRESHOLD,
    character_threshold: float = 1.0,
    use_gpu: bool = True,
    force_reload: bool = False,
) -> OppaiOracleTagger:
    """Process-wide singleton, mirroring :func:`tagger.get_tagger`."""
    global _tagger_singleton, _singleton_settings
    with _tagger_lock:
        canonical_name = _normalize_oppai_model_alias(model_name)
        new_settings = {
            "model_name": canonical_name,
            "model_path": model_path,
            "tags_path": tags_path,
            "use_gpu": bool(use_gpu),
        }
        if force_reload or _tagger_singleton is None or new_settings != _singleton_settings:
            _tagger_singleton = OppaiOracleTagger(
                model_name=canonical_name,
                model_path=model_path,
                tags_path=tags_path,
                threshold=threshold,
                character_threshold=character_threshold,
                use_gpu=use_gpu,
            )
            _singleton_settings = new_settings
        else:
            _tagger_singleton.threshold = float(threshold)
            _tagger_singleton.character_threshold = float(character_threshold)
        return _tagger_singleton


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_THRESHOLD",
    "OppaiOracleTagger",
    "get_oppai_oracle_tagger",
    "letterbox_to_square",
    "preprocess_image",
]
