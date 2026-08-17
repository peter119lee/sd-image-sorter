"""Synchronous WD14 tagging of ONE arbitrary file — no database row involved.

Why this exists
---------------
``tagger.tag_image(path)`` (``backend/tagger.py``) has always been
database-free: it takes a filesystem path, not an ``images.id``. But the only
HTTP surface for WD14 was the bulk job runner (``POST /api/tag`` +
``/tag/progress`` + ``/tag/cancel``), which walks database rows. A working
capability was therefore unreachable for any file the owner had not scanned
into the library.

Contract
--------
* Input is a filesystem path. It may be a library file, a loose file anywhere
  on disk, or the ``source_temp_path`` the Reader's ``POST /api/parse-image``
  returns for an upload it deliberately retains (24 h TTL). Nothing here
  resolves, creates, reads or updates a database row.
* Path validation is the shared ``utils.path_validation.validate_file_path``
  guard with the image-extension allow-list, and NO ``allowed_base`` — exactly
  the contract ``routers/obfuscation.py`` already uses for its single-image
  path endpoints. No directory allow-list was widened to make the Reader temp
  directory acceptable; it passes the same checks any other local image does.
* The response mirrors the tagger's own buckets, minus ``raw_scores`` (the
  ``tag_scores`` persistence payload, which belongs to the batch writer, not to
  an interactive caller).

Concurrency note
----------------
``WD14Tagger.tag`` takes the shared ``exclusive_ai_runtime("wd14-tagger")``
lease internally at ``PRIORITY_NORMAL``, so this endpoint queues behind a
running batch chunk exactly like every other single-image AI endpoint in the
app (VLM caption, artist identify, aesthetic score). Giving interactive work
its own priority lane is a separate, deliberate change — see
``.plans/sd-image-sorter-release/decisions.md``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ai_runtime_guard import AiRuntimeBusyError
from config import ALLOWED_IMAGE_EXTENSIONS
from utils.path_validation import normalize_user_path, validate_file_path

logger = logging.getLogger(__name__)

# Buckets copied straight through from the tagger result, in report order.
_TAG_BUCKETS = ("general_tags", "character_tags", "copyright_tags")


class SingleImageTagRequest(BaseModel):
    """Body for ``POST /api/tag/single``.

    Field names deliberately match Smart Tag's request vocabulary
    (``tagger_model`` / ``*_threshold`` / ``use_gpu``) so one client can drive
    the tagger-only mode and the Smart Tag modes from the same form state.
    """

    image_path: str = Field(..., min_length=1, max_length=4096)
    tagger_model: str = Field(default="", max_length=200)
    general_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    character_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    copyright_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    use_gpu: bool = True


def _load_tagger(**kwargs: Any):
    """Resolve the configured WD14 tagger singleton.

    Tests monkeypatch THIS function: everything above it needs ~450 MB of ONNX
    weights, everything below it is pure response shaping. The import is lazy
    because ``tagger`` pulls onnxruntime.
    """
    import tagger  # noqa: PLC0415 - heavy optional runtime

    return tagger.get_tagger(**kwargs)


def _resolve_source_path(raw_path: str) -> Path:
    """Validate an arbitrary local image path, or raise the right HTTP error."""
    candidate = str(raw_path or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="image_path cannot be empty")

    is_valid, error = validate_file_path(
        candidate,
        allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
    )
    if not is_valid:
        # Same mapping as routers/obfuscation.py's single-image path guard.
        status = 404 if error == "File does not exist" else 400
        raise HTTPException(status_code=status, detail=error or "Invalid image path")

    return Path(normalize_user_path(candidate)).resolve()


def _thresholds(request: SingleImageTagRequest) -> Dict[str, float]:
    """Only forward thresholds the caller actually set.

    Omitted values must fall through to the tagger's own per-model defaults
    (``TAGGER_MODELS[...]['default_threshold']``) rather than being pinned here,
    so this endpoint cannot drift from the batch path's defaults.
    """
    supplied: Dict[str, float] = {}
    if request.general_threshold is not None:
        supplied["threshold"] = float(request.general_threshold)
    if request.character_threshold is not None:
        supplied["character_threshold"] = float(request.character_threshold)
    if request.copyright_threshold is not None:
        supplied["copyright_threshold"] = float(request.copyright_threshold)
    return supplied


def _clean_entries(entries: Any) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag") or "").strip()
        if not tag:
            continue
        item: Dict[str, Any] = {"tag": tag, "confidence": float(entry.get("confidence") or 0.0)}
        category = entry.get("category")
        if category:
            item["category"] = str(category)
        cleaned.append(item)
    return cleaned


def tag_single_image(request: SingleImageTagRequest) -> Dict[str, Any]:
    """Tag one file and return its tags inline. Writes nothing, anywhere."""
    source = _resolve_source_path(request.image_path)
    model_name = (request.tagger_model or "").strip()

    loader_kwargs: Dict[str, Any] = {"use_gpu": bool(request.use_gpu)}
    if model_name:
        loader_kwargs["model_name"] = model_name

    started = time.perf_counter()
    try:
        tagger = _load_tagger(**loader_kwargs)
        result = tagger.tag(str(source), **_thresholds(request))
    except HTTPException:
        raise
    except AiRuntimeBusyError:
        # "Busy" is not "could not run". The tagger is fine; something else
        # holds the shared runtime. main.py answers this with a 409 that names
        # the blocker, which is recoverable advice — folding it into the 503
        # below would tell the user to go check WD14 in the Model Center for a
        # problem that resolves itself.
        raise
    except Exception as exc:  # noqa: BLE001 - one actionable answer for any runtime failure
        logger.exception(
            "Single-image tagging failed", extra={"model": model_name or "default"}
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"The tagger could not run: {exc} "
                "/ 打标模型无法运行，请在模型中心检查 WD14 是否就绪。"
            ),
        ) from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=503,
            detail="The tagger returned an unusable result for this image.",
        )

    # The batch engine reports per-image failures by returning an EMPTY result
    # carrying ``error`` instead of raising. Passing that off as "no tags found"
    # would be a false success, which is this project's most common defect.
    engine_error = str(result.get("error") or "").strip()
    if engine_error:
        raise HTTPException(
            status_code=422,
            detail=f"This image could not be tagged: {engine_error}",
        )

    payload: Dict[str, Any] = {
        "image_path": str(source),
        "model": model_name or getattr(tagger, "model_name", "") or "",
        "rating": str(result.get("rating") or "unknown"),
        "rating_confidences": {
            str(name): float(value)
            for name, value in (result.get("rating_confidences") or {}).items()
        },
        "elapsed_ms": elapsed_ms,
        # Explicit, asserted-in-tests contract: this endpoint never touches the
        # library, so a caller can offer it for files the user does not want
        # indexed.
        "stored": False,
    }
    for bucket in _TAG_BUCKETS:
        payload[bucket] = _clean_entries(result.get(bucket))
    payload["all_tags"] = _clean_entries(result.get("all_tags"))
    payload["tags"] = [entry["tag"] for entry in payload["all_tags"]]
    return payload
