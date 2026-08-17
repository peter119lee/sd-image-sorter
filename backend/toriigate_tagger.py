"""
ToriiGate 0.5 image tagger backend.

This wraps the multimodal caption model into the same public result shape used
by the WD14 tagger so the existing tagging pipeline and UI can reuse it.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from config import TAGGER_MODELS, get_toriigate_model_dir, read_float_env
from ai_runtime_guard import cuda_has_headroom, exclusive_ai_runtime
from model_download_sources import (
    endpoint_label,
    get_hf_endpoint_order,
    log_model_artifact_status,
    missing_model_artifacts,
)

logger = logging.getLogger(__name__)

# SECURITY: Pin HuggingFace model revision to prevent supply-chain attacks.
# snapshot_download() fetches from a remote repo; without a pinned commit, a
# compromised or hijacked repo could serve malicious model files. The hash lives
# in the catalog so the model-inventory tooling sees the same pin the downloader
# uses; reading it back here keeps the two from drifting apart.
TORIIGATE_COMMIT_HASH = TAGGER_MODELS["toriigate-0.5"]["revision"]
TORIIGATE_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "chat_template.jinja",
)

torch = None
hf_hub = None
AutoProcessor = None
Qwen3_5ForConditionalGeneration = None


class ToriiGateImageGeometryError(ValueError):
    """Raised when an image exceeds ToriiGate's supported geometry."""


TORIIGATE_SYSTEM_PROMPT = (
    "You are image captioning expert. Describe user's picture according to "
    "requested format and instructions."
)
TORIIGATE_SHORT_QUERY = (
    "# Captioning format:\n"
    "The caption for image should be quite short without long purple prose and slop. "
    "Cover main objects and details.\n"
    "Write plain prose sentences only. Do not output JSON, code, lists, or "
    "key-value pairs.\n\n"
    "# Characters on picture:\n"
    "Avoid to guess names for characters.\n"
)
TORIIGATE_DETAILED_QUERY = (
    "# Captioning format:\n"
    "Give a long and detailed description of the picture. Cover the characters, "
    "their appearance, pose and expression, the clothing, the setting, and "
    "notable details.\n"
    "Write plain prose sentences only. Do not output JSON, code, lists, or "
    "key-value pairs.\n\n"
    "# Characters on picture:\n"
    "Avoid to guess names for characters.\n"
)
# Generation token budgets per caption length. brief matches the historical
# 160-token cap; detailed needs headroom for multi-sentence prose (the old
# 160 cap is what used to truncate JSON answers mid-string).
TORIIGATE_BRIEF_MAX_NEW_TOKENS = 160
TORIIGATE_DETAILED_MAX_NEW_TOKENS = 512
TORIIGATE_CAPTION_LENGTHS = ("brief", "detailed")
TORIIGATE_MAX_IMAGE_PIXELS = 1024 * 1024
# The model selects Qwen2VLImageProcessorFast, whose smart_resize rejects
# source images with an aspect ratio above 200:1.
TORIIGATE_MAX_ASPECT_RATIO: float = 200.0
TORIIGATE_CUDA_MEMORY_FRACTION = max(
    0.30,
    min(0.95, read_float_env("SD_TORIIGATE_CUDA_MEMORY_FRACTION", 0.80)),
)
# Free VRAM (MB) required to turn the generation KV cache on. The cache makes
# ToriiGate generation ~2-4x faster but costs a few hundred MB; below this we
# keep it off so a tight GPU does not OOM mid-generation. CPU always uses it.
TORIIGATE_KV_CACHE_MIN_FREE_MB = read_float_env("SD_TORIIGATE_KV_CACHE_MIN_FREE_MB", 3000.0)
# Pre-flight guards (v3.4.3). ToriiGate-0.5 is a ~9.6 GB BF16 Qwen3.5-VL:
# attempting a GPU load without that much free VRAM ends in a driver-level
# reset (the reported "black screen"), and the old unconditional fp32 CPU
# fallback (~19.3 GB weights + 3-5 GB working set) could exhaust system RAM
# and take the whole machine down. All thresholds are env-overridable so an
# unusual setup is degraded, never hard-blocked.
TORIIGATE_GPU_MIN_FREE_MB = read_float_env("SD_TORIIGATE_GPU_MIN_FREE_MB", 11_000.0)
TORIIGATE_CPU_FP32_MIN_AVAILABLE_GB = read_float_env(
    "SD_TORIIGATE_CPU_FP32_MIN_AVAILABLE_GB", 24.0
)
TORIIGATE_CPU_BF16_MIN_AVAILABLE_GB = read_float_env(
    "SD_TORIIGATE_CPU_BF16_MIN_AVAILABLE_GB", 13.0
)
TORIIGATE_ALLOW_CPU_FALLBACK = str(
    os.environ.get("SD_TORIIGATE_ALLOW_CPU_FALLBACK", "")
).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_imports() -> None:
    """Lazy import heavy ToriiGate dependencies."""
    global torch, hf_hub, AutoProcessor, Qwen3_5ForConditionalGeneration
    if torch is None:
        import torch as torch_module  # type: ignore

        torch = torch_module
    if hf_hub is None:
        import huggingface_hub as hf_module

        hf_hub = hf_module
    if AutoProcessor is None or Qwen3_5ForConditionalGeneration is None:
        from transformers import AutoProcessor as processor_cls  # type: ignore
        from transformers import Qwen3_5ForConditionalGeneration as model_cls  # type: ignore

        AutoProcessor = processor_cls
        Qwen3_5ForConditionalGeneration = model_cls


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the pure caption-parsing half of ToriiGateTagger
# lives in toriigate_caption_parsing as a mixin. THIS module remains a real
# FILE named ``toriigate_tagger`` and the single monkeypatch surface:
#   * The LAZY-IMPORT quartet stays DEFINED here in one namespace -- the
#     ``torch`` / ``hf_hub`` / ``AutoProcessor`` /
#     ``Qwen3_5ForConditionalGeneration`` globals and _ensure_imports --
#     along with EVERY runtime method that reads them or the imported-name
#     seams (__init__ / set_generation_params / _download_model / the dtype
#     choosers / the CUDA guards / load / _teardown_model /
#     _recreate_session / _generate_text / tag / tag_batch stay
#     byte-verbatim on the class body below; the v3.4.3 two-stage load
#     ordering is UNTOUCHABLE).
#   * The SINGLETON family stays whole at the bottom of this file --
#     _toriigate_tagger / _current_settings / _toriigate_lock /
#     get_toriigate_tagger (which keeps swallowing worker kwargs).
#   * The geometry family (TORIIGATE_MAX_IMAGE_PIXELS /
#     TORIIGATE_MAX_ASPECT_RATIO / ToriiGateImageGeometryError) stays
#     DEFINED here; the moved _resize_for_inference resolves all three back
#     through _svc() at call time so patches on this module keep landing
#     and the raised exception stays THIS module's class.
# The header import block above is kept verbatim (per-file F401 ignore in
# pyproject.toml) so every historical attribute keeps resolving here.
# ---------------------------------------------------------------------------
from toriigate_caption_parsing import (
    CAPTION_ATTRIBUTE_PATTERNS,
    CAPTION_COUNT_PATTERNS,
    CAPTION_PHRASE_TAGS,
    EXPLICIT_HINT_TAGS,
    RATING_TAGS,
    _CaptionParsingMixin,
)


class ToriiGateTagger(_CaptionParsingMixin):
    """Caption-to-tags adapter for ToriiGate 0.5."""

    def __init__(
        self,
        model_name: str = "toriigate-0.5",
        model_dir: Optional[str] = None,
        use_gpu: bool = True,
        max_new_tokens: int = 0,
        caption_length: str = "detailed",
        allow_cpu_fallback: bool = TORIIGATE_ALLOW_CPU_FALLBACK,
    ) -> None:
        _ensure_imports()
        self.model_name = model_name
        self.model_dir = model_dir or get_toriigate_model_dir()
        self.use_gpu = use_gpu
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.caption_length = "brief"
        self.max_new_tokens = TORIIGATE_BRIEF_MAX_NEW_TOKENS
        self.set_generation_params(caption_length=caption_length, max_new_tokens=max_new_tokens)
        self.model = None
        self.processor = None
        self.device = "cuda" if self.use_gpu else "cpu"
        self._loaded = False
        self._resolved_model_dir: Optional[str] = None
        self._session_refresh_interval = 0
        # Decided lazily on first generation (needs the live device + free VRAM).
        self._use_kv_cache: Optional[bool] = None

    def set_generation_params(
        self,
        caption_length: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> None:
        """Update pure generation parameters without reloading model weights.

        ``max_new_tokens <= 0`` means "derive from caption_length" (brief→160,
        detailed→512).
        """
        if caption_length is not None:
            normalized = str(caption_length).strip().lower()
            self.caption_length = (
                normalized if normalized in TORIIGATE_CAPTION_LENGTHS else "detailed"
            )
        requested = int(max_new_tokens or 0)
        if requested > 0:
            self.max_new_tokens = max(32, min(1024, requested))
        else:
            self.max_new_tokens = (
                TORIIGATE_BRIEF_MAX_NEW_TOKENS
                if self.caption_length == "brief"
                else TORIIGATE_DETAILED_MAX_NEW_TOKENS
            )

    def _download_model(self) -> str:
        config = TAGGER_MODELS[self.model_name]
        local_dir = os.path.join(self.model_dir, self.model_name)
        os.makedirs(local_dir, exist_ok=True)

        model_dir_path = Path(local_dir)
        missing_before = missing_model_artifacts(model_dir_path, TORIIGATE_REQUIRED_FILES)
        if missing_before:
            logger.info("Downloading ToriiGate model %s ...", self.model_name)
            assert hf_hub is not None
            last_error: Optional[Exception] = None
            selected_endpoint = ""
            for endpoint in get_hf_endpoint_order(model_name="ToriiGate 0.5"):
                try:
                    logger.info("Downloading ToriiGate from %s via %s", config["repo_id"], endpoint_label(endpoint))
                    hf_hub.snapshot_download(
                        repo_id=config["repo_id"],
                        revision=TORIIGATE_COMMIT_HASH,
                        local_dir=local_dir,
                        local_dir_use_symlinks=False,
                        allow_patterns=[
                            "*.json",
                            "*.safetensors",
                            "*.txt",
                            "*.jinja",
                        ],
                        endpoint=endpoint,
                    )
                    selected_endpoint = endpoint
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning("ToriiGate download failed via %s: %s", endpoint_label(endpoint), exc)
            else:
                assert last_error is not None
                raise last_error
            missing_after = log_model_artifact_status(
                logger,
                model_id=self.model_name,
                revision=TORIIGATE_COMMIT_HASH,
                endpoint=selected_endpoint or "unknown",
                model_dir=model_dir_path,
                required_files=TORIIGATE_REQUIRED_FILES,
            )
            if missing_after:
                raise RuntimeError(
                    "ToriiGate download completed without required runtime files: "
                    + ", ".join(missing_after)
                    + ". Re-run Prepare / Download."
                )
        else:
            log_model_artifact_status(
                logger,
                model_id=self.model_name,
                revision=TORIIGATE_COMMIT_HASH,
                endpoint="local",
                model_dir=model_dir_path,
                required_files=TORIIGATE_REQUIRED_FILES,
            )
        return local_dir

    def _pick_torch_dtype(self):
        assert torch is not None
        if self.use_gpu and torch.cuda.is_available():
            if getattr(torch.cuda, "is_bf16_supported", None) and torch.cuda.is_bf16_supported():
                return torch.bfloat16
            return torch.float16
        return self._cpu_dtype_for_available_ram()

    def _cpu_dtype_for_available_ram(self):
        """Pick the CPU dtype the machine can actually hold (fp32 → bf16 → error).

        fp32 weights are ~19.3 GB, bf16 halves that. When even bf16 cannot fit
        in the available RAM, raise a clear error so the caption phase fails
        with a message — instead of swapping the OS to death (the reported
        whole-machine crash). Thresholds are env-overridable.
        """
        assert torch is not None
        try:
            import psutil

            available_gb = psutil.virtual_memory().available / (1024 ** 3)
        except Exception:
            # No probe available: keep the legacy fp32 behavior.
            return torch.float32
        if available_gb >= TORIIGATE_CPU_FP32_MIN_AVAILABLE_GB:
            return torch.float32
        if available_gb >= TORIIGATE_CPU_BF16_MIN_AVAILABLE_GB:
            logger.warning(
                "ToriiGate CPU mode: %.1f GB RAM available — loading bf16 weights "
                "instead of fp32 to halve memory use (override via "
                "SD_TORIIGATE_CPU_FP32_MIN_AVAILABLE_GB).",
                available_gb,
            )
            return torch.bfloat16
        raise RuntimeError(
            f"ToriiGate CPU mode needs ~{TORIIGATE_CPU_BF16_MIN_AVAILABLE_GB:.0f} GB of "
            f"available RAM (bf16 weights) but only {available_gb:.1f} GB is free. "
            "Close other applications or free up memory; refusing to load rather "
            "than exhausting system memory and crashing the machine."
        )

    def _apply_cuda_memory_guard(self) -> None:
        assert torch is not None
        if not (self.use_gpu and torch.cuda.is_available()):
            return

        setter = getattr(torch.cuda, "set_per_process_memory_fraction", None)
        if not callable(setter):
            return

        try:
            setter(TORIIGATE_CUDA_MEMORY_FRACTION, 0)
            logger.info(
                "ToriiGate CUDA memory guard set to %.0f%% of VRAM",
                TORIIGATE_CUDA_MEMORY_FRACTION * 100.0,
            )
        except Exception as exc:
            logger.debug("ToriiGate CUDA memory guard was unavailable: %s", exc)

    def _make_prompt(self, tags: Optional[List[str]] = None) -> str:
        query = (
            TORIIGATE_DETAILED_QUERY
            if self.caption_length == "detailed"
            else TORIIGATE_SHORT_QUERY
        )
        # ToriiGate is trained to accept booru tags as grounding input; feeding
        # the WD14 results in markedly improves caption accuracy. P2-13c: use
        # the exact model-card format — a "# Booru tags" block AHEAD of the
        # query with the tag list in brackets. The previous free-form phrasing
        # ("Here are grounding tags…") was wording the model was never
        # trained on.
        if tags:
            tag_str = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
            if tag_str:
                query = (
                    "# Booru tags for the image\n"
                    f"[{tag_str}]\n\n"
                    f"{query}"
                )
        return query

    def _decide_kv_cache(self) -> bool:
        """Enable the generation KV cache (~2-4x faster) when it is safe.

        On CPU it is free speed (no VRAM cost). On GPU the 160-token cache costs
        a few hundred MB, so only enable it when there is comfortable free VRAM;
        a tight card stays cache-off to avoid an OOM mid-generation (which would
        otherwise drop the whole run to CPU via the per-image fallback)."""
        if not self.use_gpu:
            return True
        if torch is None or not getattr(torch, "cuda", None) or not torch.cuda.is_available():
            return False
        try:
            free_bytes, _total = torch.cuda.mem_get_info(0)
            return (free_bytes / (1024 ** 2)) >= TORIIGATE_KV_CACHE_MIN_FREE_MB
        except Exception:
            return False

    def load(self) -> None:
        if self._loaded:
            return

        assert AutoProcessor is not None
        assert Qwen3_5ForConditionalGeneration is not None
        assert torch is not None

        local_dir = self._download_model()
        self._resolved_model_dir = local_dir

        # GPU pre-flight: never start hauling ~10 GB of weights onto a card
        # that cannot hold them — that path ends in a WDDM driver reset
        # ("black screen"), not a clean Python exception. Decide CPU up front.
        if self.use_gpu and not cuda_has_headroom(
            torch, min_free_mb=int(TORIIGATE_GPU_MIN_FREE_MB)
        ):
            if not self.allow_cpu_fallback:
                raise RuntimeError(
                    "ToriiGate GPU mode needs "
                    f"~{TORIIGATE_GPU_MIN_FREE_MB:.0f} MB of free VRAM, but the "
                    "pre-flight check found less. Refusing automatic CPU fallback "
                    "for this GPU run; close other GPU applications, lower "
                    "SD_TORIIGATE_GPU_MIN_FREE_MB only if you know the run fits, "
                    "or explicitly run ToriiGate in CPU mode."
                )
            logger.warning(
                "ToriiGate GPU pre-flight: less than %.0f MB free VRAM — "
                "loading on CPU instead (override via SD_TORIIGATE_GPU_MIN_FREE_MB).",
                TORIIGATE_GPU_MIN_FREE_MB,
            )
            self.use_gpu = False
            self.device = "cpu"

        try:
            dtype = self._pick_torch_dtype()
            self._apply_cuda_memory_guard()
            # SECURITY: trust_remote_code=True allows the model repo to execute
            # arbitrary Python.  This is required by the Qwen architecture but
            # means a compromised repo could run code on load.  Mitigate by
            # pinning TORIIGATE_COMMIT_HASH and only downloading safetensors.
            with exclusive_ai_runtime("toriigate-load"):
                self.processor = AutoProcessor.from_pretrained(
                    local_dir,
                    trust_remote_code=True,
                    padding_side="right",
                    use_safetensors=True,
                )
                self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
                    local_dir,
                    torch_dtype=dtype,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                    use_safetensors=True,
                )
                if self.use_gpu and torch.cuda.is_available():
                    self.model.to("cuda")
                    self.device = "cuda"
                else:
                    self.model.to("cpu")
                    self.device = "cpu"
                    self.use_gpu = False

            self.model.eval()
            self._loaded = True
            logger.info("ToriiGate loaded on %s", self.device)
        except Exception as exc:
            if self.use_gpu and self.allow_cpu_fallback:
                logger.warning("Failed to load ToriiGate on GPU, retrying on CPU: %s", exc)
                self.use_gpu = False
                self.device = "cpu"
                self._teardown_model()
                # RAM-guarded dtype (fp32 → bf16 → clear error). The old
                # unconditional fp32 retry could eat ~22+ GB of system RAM
                # right after a GPU OOM and crash the whole machine.
                cpu_dtype = self._cpu_dtype_for_available_ram()
                with exclusive_ai_runtime("toriigate-load-cpu-retry"):
                    self.processor = AutoProcessor.from_pretrained(
                        local_dir,
                        trust_remote_code=True,  # required by Qwen architecture
                        padding_side="right",
                        use_safetensors=True,
                    )
                    self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
                        local_dir,
                        torch_dtype=cpu_dtype,
                        low_cpu_mem_usage=True,
                        trust_remote_code=True,  # required by Qwen architecture
                        use_safetensors=True,
                    )
                    self.model.to("cpu")
                self.model.eval()
                self._loaded = True
            else:
                raise

    def _teardown_model(self) -> None:
        if self.model is not None:
            del self.model
            self.model = None
        if self.processor is not None:
            del self.processor
            self.processor = None
        gc.collect()
        if torch is not None and getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _recreate_session(self) -> None:
        self._loaded = False
        self._use_kv_cache = None
        self._teardown_model()
        self.load()

    def set_session_refresh_interval(self, interval: int) -> None:
        self._session_refresh_interval = max(0, interval)

    def _generate_text(self, image_path: str, tags: Optional[List[str]] = None) -> str:
        if not self._loaded:
            self.load()

        assert self.model is not None
        assert self.processor is not None
        assert torch is not None

        with Image.open(image_path) as image:
            image = self._resize_for_inference(image.convert("RGB"))
            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": TORIIGATE_SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": self._make_prompt(tags)},
                    ],
                },
            ]
            prompt_text = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            inputs = self.processor(
                text=[prompt_text],
                images=[image],
                return_tensors="pt",
            )

        model_device = next(self.model.parameters()).device
        inputs = {
            key: value.to(model_device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }

        if self._use_kv_cache is None:
            self._use_kv_cache = self._decide_kv_cache()
        with torch.inference_mode(), exclusive_ai_runtime("toriigate-inference"):
            generated = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=self.max_new_tokens,
                use_cache=self._use_kv_cache,
            )

        prompt_tokens = inputs["input_ids"].shape[1]
        new_tokens = generated[:, prompt_tokens:]
        text = self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0]
        return text.strip()

    def tag(self, image_path: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        try:
            return self._build_result(self._generate_text(image_path, tags))
        except Exception as exc:
            logger.error("ToriiGate failed on %s: %s", image_path, exc)
            if (
                not isinstance(exc, ToriiGateImageGeometryError)
                and self.use_gpu
                and self.allow_cpu_fallback
            ):
                logger.warning("ToriiGate switching to CPU after GPU failure.")
                self.use_gpu = False
                self.device = "cpu"
                self._recreate_session()
                try:
                    return self._build_result(self._generate_text(image_path, tags))
                except Exception as retry_exc:
                    logger.error("ToriiGate CPU retry failed on %s: %s", image_path, retry_exc)
                    exc = retry_exc
            return {
                "general_tags": [],
                "character_tags": [],
                "rating": "unknown",
                "rating_confidences": {},
                "all_tags": [],
                "error": str(exc),
            }

    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: Optional[int] = None,
        min_batch_size: int = 1,
        return_runtime_info: bool = False,
    ) -> Any:
        del preferred_batch_size, min_batch_size

        results = [self.tag(path) for path in image_paths]
        runtime_info = {
            "initial_chunk_size": 1,
            "final_chunk_size": 1,
            "backoff_steps": [],
            "used_cpu_fallback": not self.use_gpu,
            "attempted_gpu_backoff": False,
        }
        if return_runtime_info:
            return results, runtime_info
        return results


_toriigate_tagger = None
_current_settings: Dict[str, Any] = {}
_toriigate_lock = threading.Lock()


def get_toriigate_tagger(
    model_name: str = "toriigate-0.5",
    use_gpu: bool = True,
    force_reload: bool = False,
    caption_length: Optional[str] = None,
    max_new_tokens: int = 0,
    allow_cpu_fallback: bool = TORIIGATE_ALLOW_CPU_FALLBACK,
    **_: Any,
) -> ToriiGateTagger:
    """Get or create the ToriiGate singleton.

    ``caption_length`` / ``max_new_tokens`` are pure generation parameters:
    they update the live instance without reloading the multi-GB weights.
    """
    global _toriigate_tagger, _current_settings

    with _toriigate_lock:
        new_settings = {
            "model_name": model_name,
            "use_gpu": use_gpu,
            "allow_cpu_fallback": bool(allow_cpu_fallback),
        }
        if force_reload or _toriigate_tagger is None or new_settings != _current_settings:
            _toriigate_tagger = ToriiGateTagger(
                model_name=model_name,
                use_gpu=use_gpu,
                caption_length=caption_length or "detailed",
                max_new_tokens=max_new_tokens,
                allow_cpu_fallback=bool(allow_cpu_fallback),
            )
            _current_settings = new_settings
        elif caption_length is not None or max_new_tokens > 0:
            _toriigate_tagger.set_generation_params(
                caption_length=caption_length, max_new_tokens=max_new_tokens
            )
        return _toriigate_tagger
