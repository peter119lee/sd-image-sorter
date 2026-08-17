"""
LSNet-style Artist Identification for SD Image Sorter.

Identifies the artist/style of an image using a classification model.
Based on the LSNet concept from: https://github.com/spawner1145/comfyui-lsnet

Features:
- Identifies artist/style from image
- Returns "undefined" for predictions below threshold
- Supports multiple model sources (HuggingFace, ModelScope, local)

Model Sources:
- HuggingFace: Search for "artist-classification" or "style-classification"
- ModelScope: https://modelscope.cn/models (search for artist/style models)
- Local: Provide path to ONNX or PyTorch model

Usage:
    from artist_identifier import ArtistIdentifier

    identifier = ArtistIdentifier(threshold=0.03)
    result = identifier.identify("path/to/image.png")
    # Returns: {"artist": "some_artist", "confidence": 0.85, "top_predictions": [...]}
"""
import logging
import os
import csv
import hashlib
import sys
import threading
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from PIL import Image
from ai_runtime_guard import exclusive_ai_runtime
from config import (
    ARTIST_MODEL_SOURCE_DEFAULT,
    ARTIST_HF_MODEL_ID,
    ARTIST_MODELSCOPE_MODEL_ID,
    ARTIST_LSNET_CODE_PATH,
    ARTIST_KALOSCOPE_CHECKPOINT,
    ARTIST_KALOSCOPE_CLASS_MAPPING,
    ARTIST_USE_GPU,
    get_artist_model_dir,
)
from model_download_sources import endpoint_label, get_hf_endpoint_order

logger = logging.getLogger("sd-image-sorter.artist")


# ---------------------------------------------------------------------------
# Decomposition re-imports (2026-07).
# artist_identifier stays a FILE facade: tests/test_artist_gpu_toggle.py
# importlib.reload()s THIS module expecting the `from config import ...` block,
# `_model_lock = threading.Lock()`, `_identifier = None`, and the def-time
# default args below to re-execute — a package __init__ reload would not
# cascade into submodules. Every extracted name is re-imported here so
# production readers (routers/artists.py, services/artist_service.py,
# services/model_service.py) and the ~20 monkeypatch call-sites keep resolving
# and patching names on THIS module object; submodules read them back through
# _facade() at call time, so a facade patch is exactly what internal callers
# observe (the section-3 dense facade-patch call graph). Unused-looking imports
# are intentional re-exports (pyproject per-file F401 ignore).
# ---------------------------------------------------------------------------
from artist.assets import (
    _ensure_kaloscope_hf_files,
    _ensure_kaloscope_modelscope_files,
    _locate_existing_kaloscope_files,
    prepare_artist_assets,
)
from artist.default_artists import DEFAULT_ARTISTS
from artist.device import _onnx_providers_for, _resolve_artist_device
from artist.downloads import (
    ARTIST_MODELSCOPE_REVISION,
    _ARTIST_USER_AGENT,
    _artist_override_url,
    _assert_http_download_url,
    _candidate_hf_endpoints,
    _copy_existing_tree,
    _download_and_extract_github_zip,
    _fetch_artist_file,
    _hf_download_with_fallback,
    _materialize_existing_file,
    _modelscope_resolve_url,
    _sha256_file,
    _verify_artist_file_digest,
)
from artist.runtime_paths import (
    _ensure_comfyui_lsnet_runtime,
    _get_artist_model_root,
    _has_lsnet_runtime,
    _resolve_lsnet_runtime_path,
)


# Lazy-loaded model
_model = None
_processor = None
_model_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Confidence policy (measured 2026-08-17, n=250 ground-truth images from a
# Danbooru-named library where the filename token is the artist tag, run
# against the shipped Kaloscope 39,261-class weights).
#
#   top-1 >= 0.20 :  66 labels, 92% correct,  6% out-of-vocabulary
#   0.03 .. 0.20  :  79 labels, 28% correct, 65% out-of-vocabulary
#   top-1 <  0.03 : 105 labels,  2% correct, 97% out-of-vocabulary
#
# The top-1 softmax score does rank correct answers above wrong ones
# (Mann-Whitney AUC 0.946), and none of the alternatives measured on the same
# sample beat it: margin top1-top2 0.947, top1/top2 ratio 0.931, top-5 mass
# 0.930, distribution entropy 0.907. But the two distributions overlap across
# almost the entire range (the band [0.009, 0.882] holds 93% of the correct and
# 94% of the wrong answers), so NO threshold cleanly separates them - at 0.30
# precision is still only 96% while 45% of the correct answers are discarded.
#
# Therefore the score is used to TIER the answer, not to decide whether to
# assert one. Only the high tier may be presented (and stored) as an
# identification; the middle tier is an explicitly unconfirmed suggestion; and
# below the floor we say "probably not in this model's vocabulary", which is
# what 97% of those cases actually are.
ARTIST_THRESHOLD_DEFAULT = 0.03
ARTIST_CONFIDENT_THRESHOLD = 0.20

ARTIST_CONFIDENCE_HIGH = "high"
ARTIST_CONFIDENCE_LOW = "low"
ARTIST_CONFIDENCE_NONE = "none"

_model_source = None
ARTIST_LSNET_RUNTIME_REVISION = "416d945e65b81ced93f1e762349d790ca92106b1"
ARTIST_LSNET_RUNTIME_ZIP_URL = (
    f"https://github.com/spawner1145/comfyui-lsnet/archive/{ARTIST_LSNET_RUNTIME_REVISION}.zip"
)
_MAX_ARTIST_RUNTIME_ZIP_ENTRIES = 1024
_MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


def _is_kaloscope_model_id(model_id: Optional[str]) -> bool:
    normalized = str(model_id or "").strip().lower()
    return normalized == "heathcliff01/kaloscope2.0"


def classify_artist_confidence(
    confidence: float,
    *,
    threshold: float = ARTIST_THRESHOLD_DEFAULT,
) -> str:
    """Bucket a top-1 score into the tier the app is allowed to present.

    ``threshold`` is the caller's own floor. It can only tighten the result:
    a caller cannot lower it far enough to have a guess asserted as a fact.
    """
    value = float(confidence or 0.0)
    floor = max(float(threshold or 0.0), 0.0)
    if value >= ARTIST_CONFIDENT_THRESHOLD and value >= floor:
        return ARTIST_CONFIDENCE_HIGH
    if value > 0.0 and value >= floor and value >= ARTIST_THRESHOLD_DEFAULT:
        return ARTIST_CONFIDENCE_LOW
    return ARTIST_CONFIDENCE_NONE


def artist_confidence_advisory(level: str, *, vocabulary_size: int = 0) -> str:
    """Plain-language explanation of what a tier does and does not mean."""
    vocabulary = (
        f"{vocabulary_size:,}-artist vocabulary" if vocabulary_size else "artist vocabulary"
    )
    if level == ARTIST_CONFIDENCE_HIGH:
        return (
            "Confident match. Measured on a ground-truth sample, about 1 in 13 "
            "matches at this confidence is still wrong. / "
            "高置信度匹配。在实测样本中，这一档仍约有 1/13 是错的。"
        )
    if level == ARTIST_CONFIDENCE_LOW:
        return (
            "Unconfirmed suggestion, not an identification. Most results at this "
            f"confidence are wrong, usually because the real artist is not in the "
            f"model's {vocabulary}. Confirm it yourself before trusting it. / "
            "这只是低置信度候选，不是识别结果。这一档大多数是错的，通常是因为真实画师"
            "不在模型的画师词表里。请自行确认后再使用。"
        )
    return (
        f"No match. The artist is probably not in this model's {vocabulary}, so no "
        "name is offered rather than naming the closest wrong one. / "
        "没有匹配。该画师大概率不在此模型的画师词表内，因此不提供任何名字，"
        "而不是给出最接近的错误名字。"
    )


def _normalize_state_dict_keys(state_dict):
    normalized = {}
    for key, value in state_dict.items():
        normalized[key[7:] if key.startswith("module.") else key] = value
    return normalized


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


# Pinned SHA-256 digests for downloaded artist model files, keyed by the exact
# repo-relative filename (which embeds the version). A downloaded file whose
# bytes do not match a pinned digest is rejected before it is moved into place.
# Files with no entry here are not yet pinned and download unverified — add a
# digest once you can vouch for the artifact. When bumping a model version, add
# the NEW filename+digest; a stale entry simply stops applying once the version
# in the filename changes, so a legitimate version bump never causes a false
# mismatch (it just falls back to unverified until you pin the new digest).
#
# Each value is a TUPLE of acceptable digests. Some files are served with
# byte-level differences across mirrors even though the content is identical:
# HuggingFace serves class_mapping.csv with CRLF line endings while ModelScope
# serves the same 39,262 rows with LF endings, so both digests are legitimate
# and both must be accepted (a single-digest pin would reject the real
# ModelScope download). The checkpoint is byte-identical across both mirrors.
_EXPECTED_ARTIST_FILE_SHA256: Dict[str, Tuple[str, ...]] = {
    "448-90.13/best_checkpoint.pth": (
        "a86ba2fcf430cbb653ac995f7ab9cce34667434ee084973e19edf431808a32ae",
    ),
    "best_checkpoint.pth": (
        # ModelScope serves the checkpoint at the repo root (flat layout); the
        # bytes are identical to the HuggingFace 448-90.13/ copy.
        "a86ba2fcf430cbb653ac995f7ab9cce34667434ee084973e19edf431808a32ae",
    ),
    "class_mapping.csv": (
        "45aa78dacd9751de1c7a7293237845072c093dc915b72f1d4b5597ea2ff92cd4",  # HuggingFace (CRLF)
        "04cf5686e0802a9d8214090e2285ea6e2722e310fa29ee2789a1acc989f8ca8c",  # ModelScope (LF)
    ),
}


class ArtistIdentifier:
    """
    LSNet-style artist identification using classification models.

    Identifies the artist/style of an image and returns:
    - "undefined" if confidence is below threshold
    - Top predictions with confidence scores
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        model_source: str = ARTIST_MODEL_SOURCE_DEFAULT,
        threshold: float = ARTIST_THRESHOLD_DEFAULT,
        artists_list: Optional[List[str]] = None,
        use_gpu: Optional[bool] = None,
    ):
        """
        Initialize the artist identifier.

        Args:
            model_path: Path to local model file (ONNX or PyTorch)
            model_source: "huggingface", "modelscope", or "local"
            threshold: Minimum confidence threshold. Kaloscope logits are
                usually quite low, so values around 0.02-0.08 are more
                realistic than the old 0.35 default.
            artists_list: Custom list of artist names (optional)
            use_gpu: Use CUDA when available. None falls back to the
                ARTIST_USE_GPU config default. Set False (or
                SD_IMAGE_SORTER_ARTIST_USE_GPU=0) to force CPU on GPU stacks
                that freeze under CUDA load (e.g. NVIDIA + Wayland).
        """
        self.model_path = model_path
        self.model_source = model_source
        self.threshold = threshold
        self.use_gpu = ARTIST_USE_GPU if use_gpu is None else bool(use_gpu)
        self.artists = artists_list or DEFAULT_ARTISTS
        # True only when a real label source is present (an explicit
        # ``artists_list``, a Kaloscope class_mapping.csv, or a transformers
        # ``id2label``). Local ONNX / generic torch models have no label
        # source, so ``identify`` must NOT map their raw class indices through
        # the hardcoded DEFAULT_ARTISTS sample list and pass them off as real
        # predictions. See ``identify`` for the honest-refusal path.
        self._has_class_mapping: bool = artists_list is not None
        # Lazily built lowercase index over ``artists`` for knows_artist(); the
        # Kaloscope vocabulary is 39,261 entries, so a linear scan per lookup
        # would be wasteful.
        self._artist_lookup: Optional[set] = None
        self._model: Any = None
        self._session: Any = None
        self._processor: Any = None
        self._transform: Any = None
        self._input_size: int = 224
        self._backend: str = "unknown"
        self._load_error: Optional[str] = None

    def _load_class_mapping_csv(self, csv_path: str) -> List[str]:
        artists: List[str] = []
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "class_id" not in reader.fieldnames or "class_name" not in reader.fieldnames:
                raise RuntimeError("Kaloscope class mapping CSV must contain class_id and class_name columns.")

            rows = []
            for row in reader:
                class_id = int(row["class_id"])
                class_name = str(row["class_name"] or "").strip().strip("'").strip('"')
                rows.append((class_id, class_name or f"unknown_{class_id}"))

        rows.sort(key=lambda item: item[0])
        artists = [name for _, name in rows]
        if not artists:
            raise RuntimeError("Kaloscope class mapping CSV is empty.")
        return artists

    def _load_kaloscope_checkpoint_blob(self, checkpoint_path: str):
        import argparse
        import torch

        torch.serialization.add_safe_globals([argparse.Namespace])
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        if not isinstance(checkpoint, dict):
            raise RuntimeError("Unexpected Kaloscope checkpoint format.")
        return checkpoint

    def _load_kaloscope_runtime_modules(self):
        runtime_path = _resolve_lsnet_runtime_path()
        if not runtime_path:
            try:
                runtime_path = _ensure_comfyui_lsnet_runtime()
            except Exception as exc:
                raise RuntimeError(
                    "Kaloscope2.0 requires the LSNet runtime code.\n"
                    "Automatic download of comfyui-lsnet failed.\n"
                    "Clone either https://github.com/spawner1145/comfyui-lsnet or "
                    "https://github.com/spawner1145/lsnet-test and set "
                    "SD_IMAGE_SORTER_LSNET_CODE_PATH to that repository root."
                ) from exc

        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)

        try:
            from timm.models import create_model
            from timm.data import resolve_data_config
            from timm.data.transforms_factory import create_transform
            try:
                from lsnet_model import lsnet_artist  # noqa: F401
                runtime_kind = "comfyui-lsnet"
            except ImportError as inner_exc:
                if isinstance(inner_exc, ModuleNotFoundError) and inner_exc.name != "lsnet_model":
                    raise
                from model import lsnet_artist  # noqa: F401
                runtime_kind = "lsnet-test"
        except ModuleNotFoundError as exc:
            if exc.name == "triton":
                raise RuntimeError(
                    "Kaloscope2.0 currently requires the LSNet runtime plus `triton`.\n"
                    "On Windows, install `triton-windows`.\n"
                    "On Linux, install a compatible Triton package for your PyTorch/CUDA stack."
                ) from exc
            # Any other missing module (timm, lsnet runtime peer deps, etc.) must also
            # surface as a clear RuntimeError so callers see a real failure instead of
            # an UnboundLocalError on `runtime_kind` further down.
            raise RuntimeError(
                f"Kaloscope2.0 runtime is missing module {exc.name!r}. "
                "Verify the LSNet runtime checkout and that timm and its peer "
                "dependencies are installed."
            ) from exc
        except ImportError as exc:
            raise RuntimeError(
                "Kaloscope2.0 requires `timm` plus a compatible LSNet runtime repository.\n"
                "Install `timm` and point SD_IMAGE_SORTER_LSNET_CODE_PATH at a comfyui-lsnet or lsnet-test checkout."
            ) from exc

        logger.info("Using %s runtime for Kaloscope", runtime_kind)
        return create_model, resolve_data_config, create_transform

    def _initialize_kaloscope(self, checkpoint_path: str, class_mapping_path: str):
        create_model, resolve_data_config, create_transform = self._load_kaloscope_runtime_modules()
        with exclusive_ai_runtime("artist-kaloscope-load"):
            checkpoint = self._load_kaloscope_checkpoint_blob(checkpoint_path)
        args = checkpoint.get("args")
        model_name = getattr(args, "model", None) or "lsnet_xl_artist_448"
        feature_dim = getattr(args, "feature_dim", None)
        self._input_size = int(getattr(args, "input_size", 448) or 448)

        artists = self._load_class_mapping_csv(class_mapping_path)
        state_dict = checkpoint.get("model_ema") or checkpoint.get("model")
        if state_dict is None:
            raise RuntimeError("Kaloscope checkpoint is missing model weights.")
        state_dict = _normalize_state_dict_keys(state_dict)

        with exclusive_ai_runtime("artist-kaloscope-load"):
            model = create_model(
                model_name,
                pretrained=False,
                num_classes=len(artists),
                feature_dim=feature_dim,
            )
            load_result = model.load_state_dict(state_dict, strict=False)
        unexpected = [key for key in load_result.unexpected_keys if not key.startswith("head_dist")]
        if unexpected:
            logger.warning("Kaloscope unexpected keys ignored: %s", unexpected[:10])
        if load_result.missing_keys:
            logger.warning("Kaloscope missing keys during load: %s", load_result.missing_keys[:10])

        device = _resolve_artist_device(use_gpu=self.use_gpu)
        if not self.use_gpu:
            logger.info("Artist ID running on CPU (use_gpu disabled).")
        with exclusive_ai_runtime("artist-kaloscope-load"):
            model.to(device)
        model.eval()

        data_config = resolve_data_config(
            {"input_size": (3, self._input_size, self._input_size)},
            model=model,
        )
        transform = create_transform(**data_config, is_training=False)

        self._model = model
        self._processor = None
        self._transform = transform
        self.artists = artists
        self._has_class_mapping = True
        self._artist_lookup = None
        self._backend = "kaloscope"
        logger.info("Loaded Kaloscope model '%s' with %d artist classes", model_name, len(self.artists))

    def load(self):
        """Load the model (lazy loading)."""

        if self._model is not None:
            return

        with _model_lock:
            if self._model is not None:
                return

            # Try to load based on source
            if self.model_path and os.path.exists(self.model_path):
                self._load_local_model(self.model_path)
            elif self.model_source == "modelscope":
                self._load_from_modelscope()
            else:
                # Default: try HuggingFace or fall back to placeholder
                self._load_from_huggingface()

    def _load_local_model(self, path: str):
        """Load model from local file."""
        try:
            if path.endswith('.onnx'):
                import onnxruntime as ort  # type: ignore
                with exclusive_ai_runtime("artist-onnx-load"):
                    self._session = ort.InferenceSession(
                        path, providers=_onnx_providers_for(ort, use_gpu=self.use_gpu)
                    )
                self._model = "onnx"
                self._backend = "onnx"
            else:
                class_mapping_path = os.path.join(os.path.dirname(path), ARTIST_KALOSCOPE_CLASS_MAPPING)
                if os.path.exists(class_mapping_path):
                    self._initialize_kaloscope(path, class_mapping_path)
                else:
                    # Try generic PyTorch model as legacy fallback. Pass
                    # ``weights_only=False`` because PyTorch 2.6 flipped the
                    # default to ``True`` and most user-supplied artist .pth
                    # files contain pickled config classes outside the safe
                    # globals allowlist; without this override they would
                    # silently fall through to the ONNX path (which fails on
                    # .pth) and end up in placeholder mode. The file is
                    # user-placed inside the artist model directory, so we
                    # treat it as a trusted source.
                    try:
                        import torch
                        # Safety-first: weights_only=True uses the restricted
                        # unpickler and cannot execute arbitrary code. A full
                        # state_dict-only checkpoint loads fine this way. Only if
                        # that fails (e.g. the .pth pickles config classes outside
                        # torch's safe-globals allowlist) do we fall back to the
                        # legacy unsafe full unpickle, and we log a visible WARNING
                        # because weights_only=False on an attacker-controlled file
                        # enables arbitrary code execution during deserialization.
                        with exclusive_ai_runtime("artist-torch-load"):
                            try:
                                self._model = torch.load(
                                    path, map_location='cpu', weights_only=True
                                )
                            except Exception as safe_exc:
                                logger.warning(
                                    "Safe load (weights_only=True) failed for artist model "
                                    "%s: %s. Falling back to an UNSAFE full unpickle "
                                    "(weights_only=False). This can execute arbitrary code "
                                    "if the file is untrusted — only use artist .pth files "
                                    "from sources you trust.",
                                    path,
                                    safe_exc,
                                )
                                self._model = torch.load(
                                    path, map_location='cpu', weights_only=False
                                )
                        self._model.eval()
                        self._backend = "torch-generic"
                    except Exception:
                        # Fall back to ONNX runtime
                        import onnxruntime as ort  # type: ignore
                        with exclusive_ai_runtime("artist-onnx-load"):
                            self._session = ort.InferenceSession(
                                path, providers=_onnx_providers_for(ort, use_gpu=self.use_gpu)
                            )
                        self._model = "onnx"
                        self._backend = "onnx"
            self._load_error = None
            logger.info(f"Loaded model from: {path}")
        except Exception as e:
            logger.warning(f"Failed to load model: {e}")
            self._model = "placeholder"
            self._load_error = str(e)

    def _load_from_huggingface(self):
        """Load model from HuggingFace."""
        try:
            model_name = ARTIST_HF_MODEL_ID

            logger.info(f"Loading from HuggingFace: {model_name}")
            if _is_kaloscope_model_id(model_name):
                prepared = prepare_artist_assets("auto")
                checkpoint_path = prepared["checkpoint_path"]
                class_mapping_path = prepared["class_mapping_path"]
                self._initialize_kaloscope(checkpoint_path, class_mapping_path)
            else:
                from transformers import AutoImageProcessor, AutoModelForImageClassification

                with exclusive_ai_runtime("artist-transformers-load"):
                    self._processor = AutoImageProcessor.from_pretrained(model_name)
                    self._model = AutoModelForImageClassification.from_pretrained(model_name)
                self._model.eval()
                self._backend = "transformers"

                if hasattr(self._model.config, 'id2label'):
                    self.artists = [self._model.config.id2label.get(i, f"unknown_{i}")
                                   for i in range(len(self._model.config.id2label))]
                    self._has_class_mapping = True
                    self._artist_lookup = None

                logger.info(f"Loaded model with {len(self.artists)} styles")
            self._load_error = None
        except Exception as e:
            logger.warning(f"HuggingFace load failed: {e}")
            logger.info("Using placeholder mode (no model loaded)")
            self._model = "placeholder"
            self._load_error = str(e)

    def _load_from_modelscope(self):
        """Load model from ModelScope."""
        try:
            logger.info("Loading from ModelScope")
            prepared = prepare_artist_assets("modelscope")
            self._initialize_kaloscope(prepared["checkpoint_path"], prepared["class_mapping_path"])
            self._load_error = None
        except Exception as e:
            logger.warning(f"ModelScope load failed: {e}")
            logger.info("Using placeholder mode (no model loaded)")
            self._model = "placeholder"
            self._load_error = str(e)

    def identify(
        self,
        image_path: str,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Identify an artist using this instance's configured threshold."""
        return self.identify_with_threshold(
            image_path=image_path,
            top_k=top_k,
            threshold=self.threshold,
        )

    def identify_with_threshold(
        self,
        image_path: str,
        top_k: int,
        threshold: float,
    ) -> Dict[str, Any]:
        """
        Identify the artist/style of an image with a request-local threshold.

        Args:
            image_path: Path to the image file
            top_k: Number of top predictions to return
            threshold: Minimum confidence for assigning the top artist

        Returns:
            {
                "artist": str,  # the identified artist, or "undefined"
                "confidence": float,
                "confidence_level": "high" | "low" | "none",
                "candidate_artist": str | None,  # best guess when not asserted
                "out_of_vocabulary_likely": bool,
                "vocabulary_size": int,
                "advisory": str,
                "top_predictions": [{"artist": str, "confidence": float}, ...],
                "model_loaded": bool,
            }

        ``artist`` is only ever a real name in the "high" tier. Everything below
        it comes back as ``"undefined"`` with the guess carried separately in
        ``candidate_artist``, so a suggestion is never mistaken for a fact by a
        caller that only reads ``artist``.
        """
        self.load()

        result: Dict[str, Any] = {
            "artist": "undefined",
            "confidence": 0.0,
            "top_predictions": [],
            "model_loaded": self._model is not None and self._model != "placeholder",
            "confidence_level": ARTIST_CONFIDENCE_NONE,
            "candidate_artist": None,
            "out_of_vocabulary_likely": False,
            "vocabulary_size": self.vocabulary_size,
            "advisory": "",
        }

        if self._model == "placeholder":
            result["error"] = (
                self._load_error
                or "Artist model unavailable. Install the required dependencies and restart the app, "
                   "or configure a working local model."
            )
            return result

        try:
            # Load and preprocess image
            with Image.open(image_path) as source_image:
                image = source_image.convert("RGB")

            if self._session is not None:
                # ONNX inference
                predictions = self._run_onnx(image)
            elif self._backend == "kaloscope":
                predictions = self._run_kaloscope(image)
            else:
                # PyTorch/Transformers inference
                predictions = self._run_torch_classifier(image)

            # Get top predictions
            top_indices = np.argsort(predictions)[::-1][:top_k]

            for raw_idx in top_indices:
                idx = int(raw_idx)
                if self._has_class_mapping and idx < len(self.artists):
                    artist_name = self.artists[idx]
                else:
                    # No real label source (e.g. a local ONNX / generic torch
                    # model without a class_mapping.csv or embedded id2label).
                    # Surface the raw class index honestly instead of inventing
                    # a name from the hardcoded DEFAULT_ARTISTS sample list.
                    artist_name = f"class_{idx}"
                confidence = float(predictions[idx])
                result["top_predictions"].append({
                    "artist": artist_name,
                    "confidence": round(confidence, 4),
                })

            if not self._has_class_mapping:
                # Refuse to label: raw class indices are not artist names, so we
                # must not pass the top prediction off as an identified artist.
                top_conf = (
                    float(result["top_predictions"][0]["confidence"])
                    if result["top_predictions"]
                    else 0.0
                )
                result["artist"] = "undefined"
                result["confidence"] = top_conf
                result["error"] = (
                    "No artist label mapping found for this local model "
                    "(expected a class_mapping.csv beside the model file, or an "
                    "embedded id2label). Predictions are raw class indices, not "
                    "artist names. / "
                    "此本地模型未找到画师标签映射（需在模型文件旁放置 "
                    "class_mapping.csv，或模型自带 id2label）。下面是原始类别索引，"
                    "并非真实画师名。"
                )
                return result

            # Tier the answer instead of asserting whatever clears a threshold.
            if result["top_predictions"]:
                top = result["top_predictions"][0]
                confidence = float(top["confidence"])
                level = classify_artist_confidence(confidence, threshold=threshold)
                result["confidence"] = confidence
                result["confidence_level"] = level
                result["artist"] = (
                    top["artist"] if level == ARTIST_CONFIDENCE_HIGH else "undefined"
                )
                # Below the floor the top-1 is right ~2% of the time, so naming
                # it is worse than saying nothing; the raw ranking stays in
                # top_predictions for anyone who explicitly wants to inspect it.
                result["candidate_artist"] = (
                    top["artist"] if level != ARTIST_CONFIDENCE_NONE else None
                )
                result["out_of_vocabulary_likely"] = level != ARTIST_CONFIDENCE_HIGH
                result["advisory"] = artist_confidence_advisory(
                    level, vocabulary_size=result["vocabulary_size"]
                )

        except Exception as e:
            logger.error(f"Error identifying {image_path}: {e}")
            result["error"] = str(e)

        return result

    def _run_onnx(self, image: Image.Image) -> np.ndarray:
        """Run inference with ONNX model."""
        # Preprocess image
        img_resized = image.resize((224, 224))
        img_array = np.array(img_resized).astype(np.float32) / 255.0
        img_array = np.transpose(img_array, (2, 0, 1))
        img_array = np.expand_dims(img_array, 0)

        # Get input name
        assert self._session is not None
        input_name = self._session.get_inputs()[0].name

        # Run inference
        with exclusive_ai_runtime("artist-onnx-inference"):
            outputs = self._session.run(None, {input_name: img_array})

        # Apply softmax
        logits = outputs[0][0]
        exp_logits = np.exp(logits - np.max(logits))
        return exp_logits / np.sum(exp_logits)

    def _run_kaloscope(self, image: Image.Image) -> np.ndarray:
        """Run inference with the LSNet/Kaloscope classifier."""
        import torch

        if self._transform is None:
            raise RuntimeError("Kaloscope transform pipeline is not initialized.")

        tensor = self._transform(image).unsqueeze(0)
        assert self._model is not None
        device = next(self._model.parameters()).device
        with torch.no_grad(), exclusive_ai_runtime("artist-kaloscope-inference"):
            logits = self._model(tensor.to(device))
            if isinstance(logits, tuple):
                logits = logits[0]
            logits = logits[0]

        probs = torch.nn.functional.softmax(logits, dim=0)
        return probs.detach().cpu().numpy()

    def _run_torch_classifier(self, image: Image.Image) -> np.ndarray:
        """Run inference with a Transformers-compatible image classifier."""
        import torch

        if self._processor is None:
            raise RuntimeError("Artist processor is not initialized.")

        inputs = self._processor(images=image, return_tensors="pt")

        assert self._model is not None
        with torch.no_grad(), exclusive_ai_runtime("artist-transformers-inference"):
            outputs = self._model(**inputs)
            logits = outputs.logits[0]

        probs = torch.nn.functional.softmax(logits, dim=0)
        return probs.detach().cpu().numpy()

    def set_threshold(self, threshold: float):
        """Set the confidence threshold."""
        self.threshold = threshold

    def get_artists_list(self) -> List[str]:
        """Get the list of known artists."""
        return self.artists.copy()

    @property
    def vocabulary_size(self) -> int:
        """How many artists this model can actually name.

        Zero when no real label source is loaded, so callers never quote the
        hardcoded DEFAULT_ARTISTS sample list as if it were the model's answer
        set.
        """
        return len(self.artists) if self._has_class_mapping else 0

    def knows_artist(self, name: str) -> bool:
        """Whether ``name`` is in the model's answer set.

        A False here is the single most useful thing the feature can tell a
        user: if the artist is not in the vocabulary, every prediction for
        their images is guaranteed to name somebody else.
        """
        if not self._has_class_mapping:
            return False
        normalized = str(name or "").strip().lower()
        if not normalized:
            return False
        if self._artist_lookup is None:
            self._artist_lookup = {str(a).strip().lower() for a in self.artists}
        return normalized in self._artist_lookup

    @staticmethod
    def is_available() -> bool:
        """Check if artist identification is available."""
        try:
            import torch  # noqa: F401
        except ImportError:
            return False

        if _is_kaloscope_model_id(ARTIST_HF_MODEL_ID):
            return _has_lsnet_runtime()

        try:
            from transformers import AutoImageProcessor, AutoModelForImageClassification  # noqa: F401
            return True
        except ImportError:
            return False


# Singleton
_identifier: Optional[ArtistIdentifier] = None
_identifier_lock = threading.Lock()


def _identifier_requires_rebuild(
    identifier: Optional[ArtistIdentifier],
    normalized_path: Optional[str],
    model_source: str,
    resolved_use_gpu: bool,
) -> bool:
    """Return whether a request needs a fresh identifier instance."""
    return (
        identifier is None
        or identifier.model_source != model_source
        or identifier.model_path != normalized_path
        or identifier.use_gpu != resolved_use_gpu
        or identifier._model == "placeholder"
    )


def get_artist_identifier(
    model_path: Optional[str] = None,
    model_source: str = ARTIST_MODEL_SOURCE_DEFAULT,
    threshold: float = ARTIST_THRESHOLD_DEFAULT,
    use_gpu: Optional[bool] = None,
) -> ArtistIdentifier:
    """Get the singleton artist identifier.

    ``use_gpu`` None means "use the ARTIST_USE_GPU config default". A changed
    use_gpu rebuilds the singleton so a GPU-loaded model is not silently reused
    after the user switches to CPU (and vice versa).
    """
    global _identifier
    normalized_path = str(model_path).strip() if model_path else None
    resolved_use_gpu = ARTIST_USE_GPU if use_gpu is None else bool(use_gpu)

    current_identifier = _identifier
    if current_identifier is not None and not _identifier_requires_rebuild(
        current_identifier,
        normalized_path,
        model_source,
        resolved_use_gpu,
    ):
        return current_identifier

    with _identifier_lock:
        current_identifier = _identifier
        if current_identifier is not None and not _identifier_requires_rebuild(
            current_identifier,
            normalized_path,
            model_source,
            resolved_use_gpu,
        ):
            return current_identifier

        identifier = ArtistIdentifier(
            model_path=normalized_path,
            model_source=model_source,
            threshold=threshold,
            use_gpu=resolved_use_gpu,
        )
        _identifier = identifier
        return identifier
