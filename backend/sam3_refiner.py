"""
SAM3 mask refinement using HuggingFace transformers' Sam3Model.

The original ``sam3`` 0.1.3 PyPI package's loader is incompatible with the
checkpoint shapes facebook publishes today: PyTorch 2.6+ rejects its pickle
``weights_only=True`` path, and the ModelScope/HF mirrors only ship the
transformers-format ``model.safetensors``. transformers ≥5.6 ships a
faithful native port (``Sam3Model`` + ``Sam3Processor``) that loads the
same trained weights from the standard safetensors distribution and
supports text and box prompting natively.

Public surface (preserved for callers):
- ``SAM3Refiner.is_available()``
- ``SAM3Refiner.load()``
- ``SAM3Refiner.refine_box(image, box, text_prompt=None, confidence_threshold=None)``
- ``SAM3Refiner.refine_boxes(image, detections)``
- ``SAM3Refiner.segment_by_text(image, text)``
- ``SAM3Refiner.detect_privacy_regions(image, conf_threshold, prompts)``
- ``get_sam3_refiner(checkpoint_path, source)``
- ``SAM3_PRIVACY_PROMPTS``
"""
from __future__ import annotations

import contextlib
import copy
import gc
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from config import get_sam3_model_dir
from ai_runtime_guard import (
    PRIORITY_INTERACTIVE,
    PRIORITY_NORMAL,
    exclusive_ai_runtime,
)


logger = logging.getLogger(__name__)


_sam3_model = None
_sam3_processor = None
_sam3_device: Optional[str] = None
_sam3_lock = threading.Lock()
_sam3_available: Optional[bool] = None


# Files the transformers SAM3 loader needs in the checkpoint directory.
_SAM3_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "processor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
)


def _check_sam3_available() -> bool:
    """Whether transformers' SAM3 image variant + CUDA are usable *right now*.

    The transformers-import result is cached in ``_sam3_available`` because it
    cannot change within a process, but ``torch.cuda.is_available()`` is
    re-evaluated on EVERY call. A transient CUDA outage (e.g. GPU contention at
    the moment this process happened to start) must NOT permanently disable
    SAM3 for the whole process lifetime -- that previously turned a momentary
    blip into a hard "SAM3 not installed" failure for every later request.
    """
    global _sam3_available
    if _sam3_available is None:
        try:
            from transformers.models.sam3.modeling_sam3 import Sam3Config, Sam3Model  # noqa: F401
            from transformers.models.sam3.image_processing_sam3 import Sam3ImageProcessor  # noqa: F401
            from transformers.models.sam3.processing_sam3 import Sam3Processor  # noqa: F401
            _sam3_available = True
        except ImportError as exc:
            _sam3_available = False
            logger.warning("SAM3 runtime is unavailable: %s", exc)
    if not _sam3_available:
        return False
    try:
        import torch
        if torch.cuda.is_available():
            return True
        if getattr(getattr(torch, "version", None), "cuda", None) is None:
            logger.warning(
                "SAM3 runtime is installed, but this Python environment is using CPU-only PyTorch."
            )
        else:
            logger.warning(
                "SAM3 runtime is installed, but CUDA is not accessible right now."
            )
        return False
    except Exception as exc:  # torch import / CUDA probe failure must not be cached
        logger.warning("SAM3 CUDA probe failed: %s", exc)
        return False


def _resolve_checkpoint_dir(checkpoint_path: Optional[str] = None) -> Optional[str]:
    """Find the directory holding a complete transformers SAM3 checkpoint.

    Accepts either an explicit dir path, an explicit file path (its parent
    is checked), or falls back to standard ``models/sam3`` subdirectories.
    """
    if checkpoint_path:
        candidate = Path(checkpoint_path)
        if candidate.is_dir() and (candidate / "config.json").exists():
            return str(candidate.resolve())
        if candidate.is_file() and (candidate.parent / "config.json").exists():
            return str(candidate.parent.resolve())

    sam3_dir = Path(get_sam3_model_dir())
    candidates = [
        sam3_dir / "facebook-sam3-modelscope",
        sam3_dir / "facebook-sam3",
        sam3_dir,
    ]
    for candidate in candidates:
        if (candidate / "config.json").exists() and (candidate / "model.safetensors").exists():
            return str(candidate.resolve())
    return None


def _missing_checkpoint_files(checkpoint_dir: str) -> List[str]:
    return [name for name in _SAM3_REQUIRED_FILES if not (Path(checkpoint_dir) / name).exists()]


def _load_from_modelscope() -> str:
    """Download SAM3 from ModelScope, returning the snapshot directory.

    Skips the legacy 3.45 GB ``sam3.pt`` (only the unmaintained sam3 0.1.3
    package needs that); transformers reads ``model.safetensors`` directly.
    """
    try:
        from modelscope import snapshot_download  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ModelScope SDK is not installed. Install `modelscope` or place a transformers SAM3 "
            "checkpoint directory (config.json + model.safetensors + tokenizer files) in models/sam3."
        ) from exc

    logger.info("Downloading SAM3 from ModelScope (skipping legacy sam3.pt)...")
    cache_dir = Path(get_sam3_model_dir()) / "facebook-sam3-modelscope"
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_dir = snapshot_download(
        "facebook/sam3",
        cache_dir=str(cache_dir),
        ignore_file_pattern=[r".*\.pt$"],
    )
    return str(Path(model_dir).resolve())


def _build_sam3_model(checkpoint_dir: str, device: str):
    """Construct ``Sam3Model`` + ``Sam3Processor`` from a checkpoint dir.

    facebook/sam3 ships the SAM3 video config (detector + tracker) as the
    top-level ``config.json``. For static-image segmentation we only need
    the detector half; build ``Sam3Config`` from the ``detector_config``
    sub-dict so transformers resolves to the image-only variant rather
    than the video-only ``Sam3VideoModel``.
    """
    from transformers import AutoTokenizer
    from transformers.models.sam3.modeling_sam3 import Sam3Config, Sam3Model
    from transformers.models.sam3.image_processing_sam3 import Sam3ImageProcessor
    from transformers.models.sam3.processing_sam3 import Sam3Processor

    with open(Path(checkpoint_dir) / "config.json", "r", encoding="utf-8") as fh:
        full_config = json.load(fh)
    detector_dict = dict(full_config.get("detector_config") or {})
    detector_dict["model_type"] = "sam3"
    sam3_cfg = Sam3Config(**detector_dict)

    model = Sam3Model.from_pretrained(
        checkpoint_dir,
        config=sam3_cfg,
        ignore_mismatched_sizes=True,
    )
    model.eval().to(device)

    image_processor = Sam3ImageProcessor.from_pretrained(checkpoint_dir)
    tokenizer = AutoTokenizer.from_pretrained(checkpoint_dir)
    processor = Sam3Processor(image_processor=image_processor, tokenizer=tokenizer)
    return model, processor


def _load_sam3(checkpoint_path: Optional[str] = None, source: str = "huggingface"):
    """Load the SAM3 model + processor singleton."""
    global _sam3_model, _sam3_processor, _sam3_device

    if _sam3_model is not None:
        return _sam3_model, _sam3_processor

    with _sam3_lock:
        if _sam3_model is not None:
            return _sam3_model, _sam3_processor

        if not _check_sam3_available():
            raise RuntimeError(
                "SAM3 runtime is not installed correctly. Install transformers (>=5.6) and ensure CUDA is available."
            )

        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        checkpoint_dir = _resolve_checkpoint_dir(checkpoint_path)

        if not checkpoint_dir:
            # ``source`` historically defaulted to "huggingface" but transformers
            # auto-download from HF requires reachable hub access (often blocked
            # for China users). Try ModelScope as a graceful fallback.
            try:
                checkpoint_dir = _load_from_modelscope()
            except Exception as exc:
                raise RuntimeError(
                    "No SAM3 checkpoint found and ModelScope download failed: "
                    f"{exc}. Place a transformers SAM3 checkpoint directory under models/sam3."
                ) from exc

        missing = _missing_checkpoint_files(checkpoint_dir)
        if missing:
            raise RuntimeError(
                f"SAM3 checkpoint dir {checkpoint_dir} is missing required files: {missing}. "
                "Re-run the SAM3 prepare step to fetch the full transformers checkpoint."
            )

        logger.info("Loading SAM3 (transformers) on %s from %s", device, checkpoint_dir)
        with exclusive_ai_runtime("sam3-load"):
            model, processor = _build_sam3_model(checkpoint_dir, device)

        _sam3_model = model
        _sam3_processor = processor
        _sam3_device = device

    return _sam3_model, _sam3_processor


SAM3_PRIVACY_PROMPTS = [
    {"prompt": "exposed female breast", "class": "breasts"},
    {"prompt": "exposed female nipple", "class": "breasts"},
    {"prompt": "exposed female genitalia", "class": "pussy"},
    {"prompt": "exposed male genitalia", "class": "dick"},
    {"prompt": "exposed anus", "class": "anus"},
    {"prompt": "exposed buttocks", "class": "buttocks"},
]


# Empirically chosen on real anime/SD images (see docs/AI_DECISION_LOG.md):
# absent prompts produce sigmoid(presence_logits) in [0.001, 0.030]; real
# detections sit at >= 0.5. 0.5 cleanly separates the two clusters.
_DEFAULT_PRESENCE_THRESHOLD = 0.5
# Floor on the per-query score for the chosen mask. Presence is the primary
# gate; this just rejects total noise (top-1 < 0.05 is never a real detection).
_DEFAULT_SCORE_FLOOR = 0.05
# Cap mask area as a sanity check: when SAM3 collapses to a whole-body
# silhouette for an absent concept it covers > 30 % of the image. Real
# privacy regions are tiny relative to the canvas.
_DEFAULT_MAX_AREA_RATIO = 0.30
# Explicit user text prompts are high-intent: the user is asking for THIS
# concept by name, so the presence gate is loosened well below the 0.5
# auto-detect default. It stays ~5x above the absent-prompt noise ceiling
# (sigmoid in [0.001, 0.030]) so clear-absence still returns no_match, but
# moderately-present concepts (common on anime/illustration, where presence
# logits run lower than on photoreal) are no longer silently rejected.
_DEFAULT_TEXT_PRESENCE_THRESHOLD = 0.15


def _best_mask(processed_results, score_threshold: float = 0.0) -> Optional[np.ndarray]:
    """Pick the highest-scoring mask above ``score_threshold`` from
    ``Sam3Processor.post_process_instance_segmentation`` output."""
    if not processed_results:
        return None
    result = processed_results[0]
    scores = result.get("scores")
    masks = result.get("masks")
    if scores is None or masks is None or scores.numel() == 0:
        return None
    best_idx = int(scores.argmax().item())
    if float(scores[best_idx].item()) < score_threshold:
        return None
    mask_t = masks[best_idx]
    if mask_t.dtype.is_floating_point:
        mask_t = mask_t > 0.5
    return mask_t.detach().cpu().numpy().astype(np.uint8)


def _presence_prob(outputs) -> Optional[float]:
    """Return max sigmoid(presence_logits) from a Sam3 output, or None.

    SAM3 emits a per-text-query "is this concept present in the image at all"
    logit alongside the per-query masks. For absent concepts the model still
    produces non-zero query scores (often a whole-body collapse on the same
    'junk' query index), so gating by score alone yields oversized false
    positives. Presence reliably separates present (>0.5) from absent (<0.05).
    """
    pres = getattr(outputs, "presence_logits", None)
    if pres is None:
        return None
    try:
        import torch
    except Exception:
        return None
    if pres.numel() == 0:
        return None
    return float(torch.sigmoid(pres.detach().cpu().float()).max().item())


class SAM3Refiner:
    """transformers-backed SAM3 segmentation: text + box prompting → pixel masks."""

    def __init__(self, checkpoint_path: Optional[str] = None, source: str = "huggingface"):
        self.checkpoint_path = checkpoint_path
        self.source = source
        self._model = None
        self._processor = None

    @staticmethod
    def is_available() -> bool:
        return _check_sam3_available()

    def load(self):
        self._model, self._processor = _load_sam3(self.checkpoint_path, source=self.source)

    @property
    def model(self):
        if self._model is None:
            self.load()
        return self._model

    @property
    def processor(self):
        if self._processor is None:
            self.load()
        return self._processor

    def _device(self) -> str:
        return _sam3_device or "cpu"

    def _inference_context(self):
        try:
            import torch
        except Exception:
            return contextlib.nullcontext()
        if self._device().startswith("cuda"):
            return torch.amp.autocast(device_type="cuda", enabled=False)
        return contextlib.nullcontext()

    def _run_segmentation(
        self,
        image: Image.Image,
        text: Optional[str] = None,
        box: Optional[List[float]] = None,
        score_threshold: float = _DEFAULT_SCORE_FLOOR,
        presence_threshold: float = _DEFAULT_PRESENCE_THRESHOLD,
        max_area_ratio: float = _DEFAULT_MAX_AREA_RATIO,
        priority: int = PRIORITY_NORMAL,
    ) -> Optional[np.ndarray]:
        """Shared segmentation core for every SAM3 entry point.

        ``priority`` arrives from the public method rather than being pinned
        here, because this helper is shared by single-image work the user is
        waiting on (``segment_by_text``, ``detect_privacy_regions``) AND by
        ``refine_box``, which ``POST /api/censor/batch-refine-mask`` calls once
        per item in a sequential loop.
        """
        if not text and not box:
            return None
        rgb = image.convert("RGB")
        kwargs: Dict[str, Any] = {"images": rgb, "return_tensors": "pt"}
        if text:
            kwargs["text"] = text
        if box and len(box) == 4:
            kwargs["input_boxes"] = [[[float(v) for v in box]]]

        import torch

        with exclusive_ai_runtime(
            "sam3-inference", priority=priority
        ), self._inference_context():
            inputs = self.processor(**kwargs).to(self._device())
            with torch.no_grad():
                out = self.model(**inputs)

            # Presence gate: refuse text prompts the model says aren't in the image.
            # Skipped for box-only prompting (presence_logits are text-conditioned).
            if text and presence_threshold > 0:
                pres = _presence_prob(out)
                if pres is not None and pres < presence_threshold:
                    return None

            results = self.processor.post_process_instance_segmentation(
                out, target_sizes=[(rgb.height, rgb.width)], threshold=0.0
            )
        mask = _best_mask(results, score_threshold=score_threshold)
        if mask is None:
            return None

        # Sanity cap: a privacy region covering > max_area_ratio of the image
        # is the whole-body collapse pattern even when presence happens to
        # squeak past — refuse it.
        if max_area_ratio and max_area_ratio < 1.0:
            h, w = mask.shape[:2]
            if float(mask.sum()) / float(max(1, h * w)) > max_area_ratio:
                return None
        return mask

    def refine_box(
        self,
        image: Image.Image,
        box: List[int],
        text_prompt: Optional[str] = None,
        confidence_threshold: Optional[float] = None,
        priority: int = PRIORITY_NORMAL,
    ) -> Optional[np.ndarray]:
        """Refine a bounding box into a pixel mask, optionally confidence-gated.

        ``confidence_threshold`` (0..1) gates mask acceptance the same way the
        text-segmentation path uses ``presence_threshold``:

        - the chosen mask's per-query score must reach the threshold (floored
          at ``_DEFAULT_SCORE_FLOOR`` so a slider at 0 still rejects raw noise),
        - and when ``text_prompt`` is given, the text-conditioned presence gate
          uses the same value (``_run_segmentation`` skips presence for
          box-only prompting, where presence_logits are meaningless).

        ``None`` preserves the historical defaults (score floor 0.05; presence
        gate 0.5 for text prompts).

        ``priority`` defaults to the normal lane on purpose: this is the method
        ``batch_refine_mask`` drives in a loop, so it must never be the one that
        claims the interactive lane.
        """
        gate_kwargs: Dict[str, Any] = {}
        if confidence_threshold is not None:
            gate = max(0.0, min(1.0, float(confidence_threshold)))
            gate_kwargs["score_threshold"] = max(gate, _DEFAULT_SCORE_FLOOR)
            gate_kwargs["presence_threshold"] = gate
        try:
            return self._run_segmentation(
                image, text=text_prompt, box=box, priority=priority, **gate_kwargs
            )
        except Exception as exc:
            logger.error("SAM3 box refinement failed: %s", exc)
            return None

    def refine_boxes(self, image: Image.Image, detections: List[Dict]) -> List[Dict]:
        refined: List[Dict] = []
        for det in detections:
            refined_det = copy.deepcopy(det)
            box = det.get("box", [])
            cls_name = det.get("class", "")
            mask = self.refine_box(image, box, text_prompt=cls_name if cls_name else None)
            refined_det["mask"] = mask if mask is not None else refined_det.get("mask")
            refined_det["mask_refined"] = mask is not None
            refined.append(refined_det)
        return refined

    def segment_by_text(
        self,
        image: Image.Image,
        text_prompt: str,
        presence_threshold: Optional[float] = None,
        priority: int = PRIORITY_INTERACTIVE,
    ) -> Optional[np.ndarray]:
        """Segment an explicit, user-supplied text prompt.

        Unlike :meth:`detect_privacy_regions` (which sweeps canned NSFW prompts
        blindly across every image and needs the strict 0.5 presence gate to
        avoid false positives on SFW content), this path is driven by an
        explicit user request, so it defaults to the looser
        ``_DEFAULT_TEXT_PRESENCE_THRESHOLD``. Callers may override per request.

        Being explicitly user-driven is also why the interactive lane is the
        default here: both callers (Segment Text and Remove Background) serve one
        image with the user waiting, and no batch loop reaches this method.
        """
        gate = (
            _DEFAULT_TEXT_PRESENCE_THRESHOLD
            if presence_threshold is None
            else max(0.0, min(1.0, presence_threshold))
        )
        try:
            return self._run_segmentation(
                image, text=text_prompt, presence_threshold=gate, priority=priority
            )
        except Exception as exc:
            logger.error("SAM3 text segmentation failed: %s", exc)
            return None

    def detect_privacy_regions(
        self,
        image: Image.Image,
        conf_threshold: float = _DEFAULT_PRESENCE_THRESHOLD,
        prompts: Optional[List[Dict[str, str]]] = None,
        priority: int = PRIORITY_INTERACTIVE,
    ) -> List[Dict]:
        """Run every privacy prompt and collect mask detections.

        ``conf_threshold`` is the presence-probability gate (sigmoid of
        SAM3's per-text presence logit), parallel to NudeNet's score gate:
        higher = stricter, lower = more recall. Empirically, real
        detections sit at >= 0.5 and absent-prompt false positives are <0.03.
        """
        prompts = prompts or SAM3_PRIVACY_PROMPTS
        rgb = image.convert("RGB")
        detections: List[Dict] = []
        seen = set()

        for entry in prompts:
            text = entry["prompt"]
            cls_name = entry["class"]
            mask = self._run_segmentation(
                rgb,
                text=text,
                score_threshold=_DEFAULT_SCORE_FLOOR,
                presence_threshold=conf_threshold,
                priority=priority,
            )
            if mask is None:
                continue

            ys, xs = np.where(mask > 0)
            if len(xs) == 0:
                continue

            # Floor on absolute pixel area (to drop single-pixel artefacts) but
            # not on relative area: real nipple/genitalia masks can be < 0.1%
            # of a high-resolution canvas and were previously dropped here.
            area = int(np.sum(mask > 0))
            if area < 64:
                continue

            x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            box_area = max(1, (x2 - x1) * (y2 - y1))
            confidence = round(min(1.0, area / box_area), 4)
            key = (cls_name, x1, y1, x2, y2)
            if key in seen:
                continue
            seen.add(key)
            detections.append({
                "class": cls_name,
                "confidence": confidence,
                "box": [x1, y1, x2, y2],
                "mask": mask,
                "source": "sam3",
            })
            gc.collect()
        return detections


_sam3_refiner: Optional[SAM3Refiner] = None


def get_sam3_refiner(
    checkpoint_path: Optional[str] = None,
    source: str = "huggingface",
) -> SAM3Refiner:
    global _sam3_refiner
    if _sam3_refiner is None:
        _sam3_refiner = SAM3Refiner(checkpoint_path=checkpoint_path, source=source)
    return _sam3_refiner
