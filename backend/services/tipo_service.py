"""TIPO tag-upsampling assist (roadmap #8, v1).

TIPO (Text to Image with text Presampling for Optimal prompting,
arXiv:2411.08127, KohakuBlueleaf/KGen) is a small language model trained
to EXPAND a danbooru tag list. WD14-family taggers can only score labels
that exist in their trained label set, so a concept without a label is
invisible to them — and therefore also invisible to the score-band
coverage-gaps flow, which reads stored tagger scores. TIPO proposes tags
from a different direction (language-model continuation over the danbooru
vocabulary), surfacing exactly those blind spots.

v1 guard rails:

* NEVER auto-applies — the endpoint only returns proposals; the frontend
  renders a default-unchecked checklist whose confirmed picks land in the
  least destructive place (the export "Common tags" box).
* every candidate passes the shared vocabulary gate
  (``services/vlm_tag_gate.py``), so out-of-vocab hallucinations are
  dropped before the user ever sees them; input tags are folded
  (case/underscore) and stripped; proposals are capped at 40.

Runtime: ``tipo-kgen`` + ``llama-cpp-python`` (CPU, GGUF) — an OPT-IN
dependency pair mirroring rembg in ``services/mask_service.py``: a missing
install raises a clear bilingual error carrying the exact pip command.
(Note: tipo-kgen also declares torch/transformers as install deps —
``kgen.models`` imports them at module level — but only the llama_cpp GGUF
path is ever exercised here.)

Model licenses (per decision memo — keep documented):

* ``v2.1`` (default) — TIPO-v2.1-1B-A200M Q8_0 GGUF from
  ``KBlueLeaf/TIPO-v2.1-1B-A200M``. KohakUwU MoE (~991M total / ~193M
  active per token), GGUF architecture ``dots1``. License:
  kohaku-license-1.0 — free for local/personal use (this app is a local
  tool); redistribution/commercial hosting restricted. ~1.07 GB.
* ``200m-ft`` — TIPO-200M-ft via the QuantFactory GGUF mirror. License:
  kohaku-license-1.0 — free for local use. Kept as an optional smaller
  fallback.
* ``100m`` — TIPO-100M (KBlueLeaf official F16 GGUF). License: Apache-2.0,
  the license-safest choice.

Weights download on demand into ``DATA_DIR/models/tipo/`` (override with
``SD_IMAGE_SORTER_TIPO_DIR``) — never into the user profile. The default
variant is fetched with ``huggingface_hub.hf_hub_download`` at a pinned
commit (kgen's ``download_gguf`` has no revision argument and cannot see
files under the repo's ``gguf/`` subfolder).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

import config

logger = logging.getLogger(__name__)

PIP_INSTALL_HINT = (
    "pip install --only-binary=llama-cpp-python "
    "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu "
    '"llama-cpp-python>=0.3.24" "tipo-kgen>=0.3.1"'
)

# Import names of the opt-in runtime pair, in install order.
RUNTIME_MODULES: tuple[str, ...] = ("llama_cpp", "kgen")

# First four bytes of every GGUF file (ggml GGUF spec: 0x47 0x47 0x55 0x46).
# A weight file that exists without this header is a failed/interrupted
# download -- an HTML error page, a truncated stub, or a zero-byte placeholder
# -- and the Model Center must say so instead of reporting "not downloaded".
GGUF_MAGIC = b"GGUF"

# User-facing sizes of the two selectable GGUFs (Q8_0 file sizes from HF).
WEIGHT_SIZE_HINT = "1.1 GB"  # TIPO-v2.1-1B-A200M-Q8_0.gguf = 1,072,689,600 bytes
LIGHT_WEIGHT_SIZE_HINT = "210 MB"  # TIPO-200M-ft.Q8_0.gguf = 216,045,920 bytes

# Pinned commit of KBlueLeaf/TIPO-v2.1-1B-A200M (HF lastModified 2026-08-22).
V21_REVISION = "f5a318524a4ab30cdbbf51816cf406170f454e65"

MISSING_DEPS_MESSAGE = (
    "TIPO is not installed. Use Model Center → TIPO → Prepare, or run: "
    f"{PIP_INSTALL_HINT}  (prebuilt CPU wheel, no compiler; default GGUF "
    f"~{WEIGHT_SIZE_HINT} still downloads on first Suggest.) / 未安装 TIPO。"
    "请在模型中心对 TIPO 点「准备」，或在后端环境执行 "
    f"{PIP_INSTALL_HINT}"
    "（官方 CPU 预编译 wheel，不需要编译器；默认权重仍在首次建议时下载）。"
)

# Hard ceiling on returned proposals — a review checklist longer than this
# stops being reviewable, and the model rarely produces more useful ones.
MAX_PROPOSALS = 40

# Buckets of the parsed TIPO result that contain proposable caption tags.
# artist is deliberately excluded (hallucinated artist names are the worst
# failure mode) and quality/meta/rating never belong in dataset captions.
_PROPOSAL_BUCKETS = ("special", "general", "characters", "copyrights")


@dataclass(frozen=True)
class TipoModelSpec:
    repo: str
    filename: str
    license_note: str
    hf_filename: str
    size_hint: str
    revision: Optional[str] = None
    selectable: bool = False


MODEL_SPECS: Dict[str, TipoModelSpec] = {
    "v2.1": TipoModelSpec(
        repo="KBlueLeaf/TIPO-v2.1-1B-A200M",
        filename="TIPO-v2.1-1B-A200M-Q8_0.gguf",
        hf_filename="gguf/TIPO-v2.1-1B-A200M-Q8_0.gguf",
        revision=V21_REVISION,
        size_hint=WEIGHT_SIZE_HINT,
        selectable=True,
        license_note="kohaku-license-1.0 (free for local use)",
    ),
    "200m-ft": TipoModelSpec(
        repo="QuantFactory/TIPO-200M-ft-GGUF",
        filename="TIPO-200M-ft.Q8_0.gguf",
        hf_filename="TIPO-200M-ft.Q8_0.gguf",
        size_hint=LIGHT_WEIGHT_SIZE_HINT,
        selectable=True,
        license_note="kohaku-license-1.0 (free for local use)",
    ),
    "100m": TipoModelSpec(
        repo="KBlueLeaf/TIPO-100M",
        filename="TIPO-100M-F16.gguf",
        hf_filename="TIPO-100M-F16.gguf",
        size_hint="200 MB",
        license_note="Apache-2.0 (license-safest)",
    ),
}


def selectable_tipo_variants() -> List[Dict[str, str]]:
    """The two variants the UI offers: v2.1 (quality) and 200m-ft (lighter).

    TIPOv2-1B-A200M is intentionally absent. It is the same ~1 GB / ~1–2 GB RAM
    class as v2.1, and v2.1 is the corrected retrain of that architecture.
    ``100m`` stays in the API for old clients but is not a picker option.
    """
    return [
        {"id": key, "size_hint": spec.size_hint}
        for key, spec in MODEL_SPECS.items()
        if spec.selectable
    ]

# v2.1 card: Danbooru raw rating words were out of vocabulary in v2.
# https://huggingface.co/KBlueLeaf/TIPO-v2.1-1B-A200M
_V21_RATING_MAP = {
    "general": "safe",
    "g": "safe",
    "safe": "safe",
    "sensitive": "sensitive",
    "s": "sensitive",
    "questionable": "nsfw",
    "q": "nsfw",
    "nsfw": "nsfw",
    "explicit": "nsfw, explicit",
    "e": "nsfw, explicit",
}


class TipoError(ValueError):
    """User-facing TIPO failure (router maps this to HTTP 400)."""


class TipoSuggestRequest(BaseModel):
    image_id: Optional[int] = Field(default=None, ge=1)
    tags: List[str] = Field(..., min_length=1, max_length=200)
    rating: Optional[str] = Field(default=None, max_length=32)
    aspect_ratio: Optional[float] = Field(default=None, gt=0.0, le=100.0)
    target: Literal["short", "long"] = "short"
    model: Literal["v2.1", "200m-ft", "100m"] = "v2.1"


# llama.cpp contexts are not thread-safe and FastAPI runs sync endpoints in
# a threadpool — one lock guards load + generation (singleton model).
_RUNTIME_LOCK = threading.Lock()
_loaded_model_key: Optional[str] = None


DEFAULT_MODEL_KEY = "v2.1"


def tipo_model_dir_path() -> Path:
    """Resolve the weight home WITHOUT creating it.

    The Model Center health probe must be able to answer "is TIPO installed?"
    without writing anything into DATA_DIR, so directory creation is deferred
    to ``tipo_model_dir`` (only the generation path needs it).
    """
    override = (os.environ.get("SD_IMAGE_SORTER_TIPO_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(config.DATA_DIR) / "models" / "tipo"


def tipo_model_dir() -> Path:
    """Model weight home: DATA_DIR/models/tipo, env-overridable — the same
    stay-portable policy as rembg's ``_rembg_session_home``."""
    path = tipo_model_dir_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


def tipo_weight_path(model_key: str, model_dir: Optional[Path] = None) -> Path:
    """Expected on-disk weight path for one variant.

    kgen's ``download_gguf`` renames the fetched file to
    ``{repo_tail}_{filename}`` inside its ``model_dir``; ``_ensure_model_loaded``
    probes the same name, so the health probe must derive it identically or the
    card and the loader would disagree.
    """
    spec = MODEL_SPECS[model_key]
    base = model_dir if model_dir is not None else tipo_model_dir_path()
    return base / f"{spec.repo.split('/')[-1]}_{spec.filename}"


def _hf_hub_download(**kwargs: Any) -> str:
    """Thin wrapper so tests can record the Hugging Face call without a network."""
    from huggingface_hub import hf_hub_download  # noqa: PLC0415 - optional at import

    return hf_hub_download(**kwargs)


def _download_weight(spec: TipoModelSpec, dest_dir: Path) -> Path:
    """Fetch one GGUF into dest_dir and rename it to the kgen on-disk form.

    kgen's ``download_gguf`` writes ``{repo_tail}_{filename}`` and has no
    revision argument, so this path calls ``huggingface_hub`` directly (already
    a first-class dependency) and then applies the same rename. Files that
    live under a subfolder on the hub (v2.1 Q8_0 is under ``gguf/``) cannot
    be fetched by kgen's helper at all.
    """
    target = dest_dir / f"{spec.repo.split('/')[-1]}_{spec.filename}"
    if target.is_file() and target.stat().st_size > 0:
        return target
    dest_dir.mkdir(parents=True, exist_ok=True)
    kwargs: Dict[str, Any] = {
        "repo_id": spec.repo,
        "filename": spec.hf_filename,
        "repo_type": "model",
        "local_dir": str(dest_dir),
    }
    if spec.revision:
        kwargs["revision"] = spec.revision
    try:
        downloaded = Path(_hf_hub_download(**kwargs))
    except Exception as exc:
        raise TipoError(
            f"TIPO model download failed: {exc} / TIPO 模型下载失败：{exc}"
        ) from exc
    if downloaded.resolve() != target.resolve():
        if target.exists():
            target.unlink()
        shutil.move(str(downloaded), str(target))
        leftover = downloaded.parent
        if leftover != dest_dir:
            try:
                leftover.rmdir()
            except OSError:
                pass
    return target


def _rating_for_model(model_key: str, rating: Optional[str]) -> Optional[str]:
    """Map a caller rating onto the vocabulary the selected checkpoint learned.

    v2.1's card documents that v2 passed Danbooru raw values (``general``,
    ``questionable``, ``explicit``) which were out of vocabulary. The default
    checkpoint therefore needs the TIPO words; the v1 GGUFs keep the raw
    Danbooru spelling they were trained on.
    """
    if rating is None:
        return None
    folded = str(rating).strip().lower().replace(" ", "_")
    if not folded:
        return None
    if model_key == "v2.1":
        return _V21_RATING_MAP.get(folded, folded.replace("_", " "))
    return folded


def _missing_runtime_modules() -> List[str]:
    """Import names of the opt-in runtime pair that are not installed."""
    missing: List[str] = []
    for module_name in RUNTIME_MODULES:
        try:
            found = importlib.util.find_spec(module_name) is not None
        except Exception:  # noqa: BLE001 - a broken dist must read as missing
            found = False
        if not found:
            missing.append(module_name)
    return missing


def _weight_file_state(path: Path) -> str:
    """Classify one weight file as ``ready``, ``broken``, or ``missing``."""
    try:
        if not path.is_file():
            return "missing"
        if path.stat().st_size <= 0:
            return "broken"
        with open(path, "rb") as handle:
            header = handle.read(len(GGUF_MAGIC))
    except OSError:
        return "broken"
    return "ready" if header == GGUF_MAGIC else "broken"


def probe_tipo_installation() -> Dict[str, Any]:
    """Read-only Model Center health probe for TIPO.

    Returns the same shape the other opt-in-runtime cards use
    (``available`` / ``missing_dependencies`` / ``message``) plus the
    per-variant split the download-on-demand layout needs:

    * ``installed_variants`` — variants whose GGUF is present and valid;
    * ``broken_variants``   — variants whose GGUF exists but is unusable;
    * ``weight_state``      — ``ready`` if any variant is usable, else
      ``broken`` if any file exists at all, else ``missing``.

    Creates nothing and imports neither llama_cpp nor kgen.
    """
    model_dir = tipo_model_dir_path()
    installed: List[str] = []
    broken: List[str] = []
    for model_key in MODEL_SPECS:
        state = _weight_file_state(tipo_weight_path(model_key, model_dir))
        if state == "ready":
            installed.append(model_key)
        elif state == "broken":
            broken.append(model_key)

    if installed:
        weight_state = "ready"
    elif broken:
        weight_state = "broken"
    else:
        weight_state = "missing"

    missing_dependencies = _missing_runtime_modules()
    available = weight_state == "ready" and not missing_dependencies

    if weight_state == "ready" and available:
        message = (
            "TIPO prompt expansion is ready ("
            + ", ".join(installed)
            + ")."
        )
    elif weight_state == "ready":
        message = (
            "TIPO weights are installed, but the opt-in runtime is missing: "
            + ", ".join(missing_dependencies)
            + f". Install it with: {PIP_INSTALL_HINT}"
        )
    elif weight_state == "broken":
        message = (
            "TIPO weight files are present but unreadable (not valid GGUF): "
            + ", ".join(broken)
            + ". Delete them from the path below; the next run re-downloads."
        )
    elif missing_dependencies:
        message = (
            "TIPO runtime packages are missing: "
            + ", ".join(missing_dependencies)
            + f". Install them with: {PIP_INSTALL_HINT}"
            f" Default weights (~{WEIGHT_SIZE_HINT}) download on first use."
        )
    else:
        message = (
            "TIPO weights are not downloaded yet. v2.1 downloads on first use "
            f"(~{WEIGHT_SIZE_HINT}); 200m-ft is the lighter option "
            f"(~{LIGHT_WEIGHT_SIZE_HINT})."
        )

    try:
        resolved_dir = str(model_dir.resolve())
    except OSError:
        resolved_dir = str(model_dir)

    return {
        "available": available,
        "weight_state": weight_state,
        "installed_variants": installed,
        "broken_variants": broken,
        "missing_dependencies": missing_dependencies,
        "model_dir": resolved_dir,
        "default_variant": DEFAULT_MODEL_KEY,
        "selectable_variants": selectable_tipo_variants(),
        "message": message,
    }


def _fold(tag: str) -> str:
    """Case/underscore fold matching ``vlm_tag_gate.normalize_tag`` output."""
    return (tag or "").strip().lower().replace(" ", "_")


def _import_kgen() -> Dict[str, Any]:
    """Import the opt-in TIPO runtime, or raise the actionable 400 message.

    tipo-kgen 0.2.0+ verified API (src/kgen; 0.3.1 lists v2.1 first):
    * ``models.model_dir`` (module global), ``models.download_gguf(repo,
      filename)``, ``models.load_model(path, gguf=True, device="cpu")``
    * ``formatter.seperate_tags(tags) -> tag_map``
    * ``executor.tipo.parse_tipo_request(tag_map, nl_prompt, ...) ->
      (meta, operations, general, nl_prompt)``
    * ``executor.tipo.tipo_runner(meta, operations, general, nl_prompt,
      seed=..) -> (parsed, timing)``
    """
    try:
        import llama_cpp  # noqa: F401, PLC0415 - heavy opt-in dependency
        import kgen.models as kgen_models  # noqa: PLC0415
        from kgen.executor.tipo import (  # noqa: PLC0415
            parse_tipo_request,
            tipo_runner,
        )
        from kgen.formatter import seperate_tags  # noqa: PLC0415
    except ImportError as exc:
        raise TipoError(MISSING_DEPS_MESSAGE) from exc
    return {
        "models": kgen_models,
        "seperate_tags": seperate_tags,
        "parse_tipo_request": parse_tipo_request,
        "tipo_runner": tipo_runner,
    }


def _ensure_model_loaded(model_key: str) -> Dict[str, Any]:
    """Lazy singleton load of the requested GGUF. Caller holds the lock.

    Downloads on first real use only — never at import or startup. The
    on-disk name matches kgen's ``{repo_tail}_{filename}`` scheme so the
    health probe and the loader stay in agreement.
    """
    global _loaded_model_key
    api = _import_kgen()
    models = api["models"]
    models.model_dir = tipo_model_dir()
    if _loaded_model_key == model_key and models.text_model is not None:
        return api
    spec = MODEL_SPECS[model_key]
    target = tipo_weight_path(model_key, models.model_dir)
    try:
        if not target.is_file():
            logger.info(
                "TIPO: downloading %s/%s (%s) into %s",
                spec.repo,
                spec.hf_filename,
                spec.license_note,
                models.model_dir,
            )
            _download_weight(spec, models.model_dir)
        models.load_model(str(target), gguf=True, device="cpu")
    except TipoError:
        raise
    except Exception as exc:
        raise TipoError(
            f"TIPO model load failed: {exc} / TIPO 模型加载失败：{exc}"
        ) from exc
    _loaded_model_key = model_key
    logger.info("TIPO model %s loaded (%s)", model_key, spec.license_note)
    return api


def _generate_candidates(
    input_tags: List[str],
    rating: Optional[str],
    aspect_ratio: Optional[float],
    target: str,
    model_key: str,
) -> List[str]:
    """Run one TIPO pass and return the RAW candidate tag strings.

    Tests monkeypatch THIS function — everything above it needs the real
    runtime, everything below it (dedup, vocab gate, cap, categorization)
    is pure post-processing.
    """
    with _RUNTIME_LOCK:
        api = _ensure_model_loaded(model_key)
        tag_map = api["seperate_tags"](list(input_tags))
        if rating:
            tag_map["rating"] = [str(rating).strip().lower()]
        meta, operations, general, nl_prompt = api["parse_tipo_request"](
            tag_map,
            "",
            tag_length_target=target,
            nl_length_target=target,
            generate_extra_nl_prompt=False,
            add_quality=False,
        )
        if aspect_ratio:
            # Same convention as KohakuBlueleaf's z-tipo-extension: aspect
            # ratio rides the meta block of the TIPO prompt.
            meta["aspect_ratio"] = f"{float(aspect_ratio):.1f}"
        try:
            parsed, _timing = api["tipo_runner"](meta, operations, general, nl_prompt)
        except Exception as exc:
            raise TipoError(
                f"TIPO generation failed: {exc} / TIPO 生成失败：{exc}"
            ) from exc
    candidates: List[str] = []
    for bucket in _PROPOSAL_BUCKETS:
        value = parsed.get(bucket) or []
        if isinstance(value, list):
            candidates.extend(str(item) for item in value)
    return candidates


def _fill_aspect_ratio_from_image(image_id: int) -> Optional[float]:
    """Derive width/height aspect ratio from the gallery record."""
    import database as db  # noqa: PLC0415 - keep module import-light (router imports the request model)

    record = (db.get_images_by_ids([int(image_id)]) or {}).get(int(image_id))
    if not record:
        raise LookupError(f"Image {image_id} not found in library")
    width = record.get("width")
    height = record.get("height")
    if width and height:
        return float(width) / float(height)
    return None


def suggest_upsample(request: TipoSuggestRequest) -> Dict[str, Any]:
    """Propose vocabulary-gated tags the input list does not already carry.

    Returns ``{proposed_tags: [{tag, category}], model, elapsed_ms,
    input_tags}``. Read-only: nothing is written to the database — applying
    any proposal is a separate, human-confirmed frontend action.
    """
    input_tags = [str(tag).strip() for tag in request.tags if str(tag or "").strip()]
    if not input_tags:
        raise TipoError(
            "No usable input tags — send the queue's current tag list. "
            "/ 没有可用的输入标签 — 请传入队列当前的标签列表。"
        )

    aspect_ratio = request.aspect_ratio
    if aspect_ratio is None and request.image_id is not None:
        aspect_ratio = _fill_aspect_ratio_from_image(request.image_id)

    started = time.perf_counter()
    raw = _generate_candidates(
        input_tags,
        _rating_for_model(request.model, request.rating),
        aspect_ratio,
        request.target,
        request.model,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    # Lazy imports: the vocab gate pulls the ~140k-row bundled CSV and
    # tag_rules is a heavy rule table — neither belongs at module import.
    from services.vlm_tag_gate import filter_vlm_tags  # noqa: PLC0415
    from tag_rules import categorize_tag  # noqa: PLC0415

    folded_inputs = {_fold(tag) for tag in input_tags}
    fresh = [tag for tag in raw if _fold(tag) not in folded_inputs]
    accepted, dropped = filter_vlm_tags(fresh)
    proposals = [tag for tag in accepted if tag not in folded_inputs][:MAX_PROPOSALS]
    logger.info(
        "TIPO suggest: %s input tags -> %s raw / %s gated-out / %s proposed (%s ms)",
        len(input_tags),
        len(raw),
        dropped,
        len(proposals),
        elapsed_ms,
    )
    return {
        "proposed_tags": [
            {"tag": tag, "category": categorize_tag(tag)} for tag in proposals
        ],
        "model": request.model,
        "elapsed_ms": elapsed_ms,
        "input_tags": len(input_tags),
    }
