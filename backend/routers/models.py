"""
Unified model inventory + preparation endpoints.

These endpoints back the frontend model manager so users can inspect which
runtime/model assets are ready and trigger first-run downloads explicitly.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from optional_dependencies import (
    UnsafeDependencyInstallError,
    UnsupportedOptionalDependencyError,
)
from services.model_service import (
    ExternalAuthRequiredError,
    ModelPreparationFailedError,
    ModelService,
    get_model_service,
)

_logger = logging.getLogger(__name__)

# In-memory progress mirror for the most recent /prepare invocation.
# NOTE: this state is process-local. The app is designed to run as a single
# uvicorn worker. Running multiple workers will fragment this dict across
# processes and the UI will see inconsistent results.
def _empty_prepare_result() -> Dict[str, Any]:
    # Rich-error fields (manual_steps, external_url, target_dir, provider,
    # error_type) are populated when ExternalAuthRequiredError /
    # ModelPreparationFailedError fire. The frontend prepare-progress poll
    # treats them as the trigger to render an actionable guidance dialog
    # instead of a generic toast — without these, users hitting the Civitai
    # login wall on Privacy YOLO see "Model setup failed" with no recovery
    # path.
    return {
        "active": False,
        "model_id": "",
        "status": "",
        "message": "",
        "error": "",
        "error_type": "",
        "provider": "",
        "manual_steps": [],
        "target_dir": "",
        "external_url": "",
        "restart_recommended": False,
        "installed_packages": [],
        "restart_reason": "",
    }


_prepare_result: Dict[str, Any] = _empty_prepare_result()
_prepare_lock = threading.Lock()


router = APIRouter(prefix="/api/models", tags=["models"])


class PrepareModelRequest(BaseModel):
    model_id: str = Field(..., min_length=1)
    source: Optional[str] = None
    variant: Optional[str] = None


class MirrorRequest(BaseModel):
    mirror: str = Field("auto", pattern="^(auto|hf-mirror|modelscope)$")


@router.get("/mirror")
async def get_mirror():
    from config import get_download_mirror, VALID_MIRRORS
    return {"mirror": get_download_mirror(), "options": list(VALID_MIRRORS)}


@router.post("/mirror")
async def set_mirror(request: MirrorRequest):
    from config import save_download_mirror, get_download_mirror
    save_download_mirror(request.mirror)
    return {"mirror": get_download_mirror()}


@router.get("/download-progress")
async def get_download_progress():
    from services.model_service import get_download_progress
    progress = get_download_progress()
    with _prepare_lock:
        progress["prepare_result"] = dict(_prepare_result)
    return progress


@router.get("/status")
async def get_models_status(service: ModelService = Depends(get_model_service)):
    return service.get_status()


# Models exposed by the selectable bulk-download flow.  The seven recommended
# entries provide at least one prepared model for tagging, similarity, scoring,
# artist ID, captions, training masks, and NudeNet; optional alternatives
# (including SAM3 refinement) remain available on their individual cards.
# ``cl-tagger-v2`` is selectable but not preselected because Hugging Face
# terms/token access are user-specific.  Estimates are compressed/download
# sizes, not VRAM usage.
BULK_MODEL_BUNDLE: list[dict[str, object]] = [
    {
        "id": "wd14",
        "variant": "wd-swinv2-tagger-v3",
        "size_bytes": 446 * 1024 * 1024,
        "label": "WD14 Tagger (default: wd-swinv2-tagger-v3)",
        "feature_key": "tagging",
        "recommended": True,
        "default_selected": True,
        "restart_after_install": False,
    },
    {
        "id": "censor-nudenet",
        "size_bytes": 12 * 1024 * 1024,
        "label": "NudeNet 320n",
        "feature_key": "censor",
        "recommended": True,
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "clip",
        "size_bytes": 600 * 1024 * 1024,
        "label": "CLIP ViT-B/32 vision + text (similarity search, ~580 MB pair)",
        "feature_key": "similarity",
        "recommended": True,
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "aesthetic",
        "size_bytes": int(1.7 * 1024 * 1024 * 1024),
        "label": "Aesthetic predictor (CLIP ViT-L/14 + LAION head)",
        "feature_key": "scoring",
        "recommended": True,
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "artist",
        "size_bytes": int(2.8 * 1024 * 1024 * 1024),
        "label": "Kaloscope 2.0 (Artist ID)",
        "feature_key": "artist_id",
        "recommended": True,
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "sam3",
        "size_bytes": int(3.3 * 1024 * 1024 * 1024),
        "label": "SAM 3 (optional mask refinement)",
        "feature_key": "segmentation",
        "recommended": False,
        "default_selected": False,
        "restart_after_install": True,
    },
    {
        "id": "florence2",
        "size_bytes": 465 * 1024 * 1024,
        "label": "Florence-2 Base (natural-language captions)",
        "feature_key": "natural_language_caption",
        "recommended": True,
        "variant": "base",
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "lucida",
        "size_bytes": 885 * 1024 * 1024,
        "label": "Lucida (training-set subject masks)",
        "feature_key": "training_masks",
        "recommended": True,
        "variant": "pinned",
        "default_selected": True,
        "restart_after_install": True,
    },
    {
        "id": "cl-tagger-v2",
        "size_bytes": int(2.7 * 1024 * 1024 * 1024),
        "label": "CL Tagger v2 (gated optional tagger)",
        "feature_key": "tagging",
        "recommended": False,
        "requires_auth": True,
        "variant": "v2_00",
        "default_selected": False,
        "gated_download": True,
        "auth_url": "https://huggingface.co/cella110n/cl_tagger_v2",
        "restart_after_install": True,
    },
]


@router.get("/bulk-bundle")
async def get_bulk_bundle(service: ModelService = Depends(get_model_service)):
    """Inventory of models the "Download all" button covers.

    Returns each item with its current ready/missing status and an
    estimated download size, plus the total bytes the button would
    fetch if pressed right now (only "missing" entries contribute to
    the total). The frontend uses this to render the confirmation
    dialog showing how much disk space is needed.
    """
    inventory = service.build_model_inventory()
    by_id = {entry["id"]: entry for entry in inventory}

    items = []
    pending_total = 0
    recommended_pending_total = 0
    optional_pending_total = 0
    for spec in BULK_MODEL_BUNDLE:
        entry = by_id.get(spec["id"])
        status = (entry or {}).get("status", "missing")
        is_ready = status == "ready"
        item = {
            "id": spec["id"],
            "label": spec["label"],
            "size_bytes": int(spec["size_bytes"]),
            "status": "ready" if is_ready else "missing",
            "name": (entry or {}).get("name") or spec["id"],
            "group": (entry or {}).get("group") or "",
            "variant": spec.get("variant"),
            "feature_key": spec.get("feature_key") or "",
            "recommended": bool(spec.get("recommended")),
            "optional": not bool(spec.get("recommended")),
            "default_selected": bool(spec.get("default_selected")),
            "requires_auth": bool(spec.get("requires_auth")),
            "gated_download": bool(spec.get("gated_download")),
            "auth_url": spec.get("auth_url"),
            "restart_after_install": bool(spec.get("restart_after_install")),
            "download_supported": bool((entry or {}).get("download_supported", True)),
        }
        items.append(item)
        if not is_ready:
            size_bytes = int(spec["size_bytes"])
            pending_total += size_bytes
            if bool(spec.get("recommended")):
                recommended_pending_total += size_bytes
            else:
                optional_pending_total += size_bytes

    return {
        "items": items,
        "pending_total_bytes": pending_total,
        "recommended_pending_total_bytes": recommended_pending_total,
        "optional_pending_total_bytes": optional_pending_total,
        "all_total_bytes": sum(
            int(s["size_bytes"]) for s in BULK_MODEL_BUNDLE
        ),
        "excluded": [
            {"id": "censor-legacy", "reason": "Privacy YOLO (Wenaka2004) is opt-in for content-safety reasons."},
            {"id": "toriigate", "reason": "ToriiGate is a ~9.6 GB BF16 captioner, not a gallery tagger; Florence-2 already covers local captions."},
            {"id": "oppai-oracle", "reason": "OppaiOracle V1.1 is a ~947 MB alternative tagger; the default WD14 already covers tagging."},
        ],
    }


def _run_prepare_blocking(service: ModelService, model_id: str, source: Optional[str], variant: Optional[str]) -> None:
    _logger.info(
        "[MODEL] prepare_start model_id=%s source=%s variant=%s",
        model_id,
        source or "auto",
        variant or "default",
    )
    try:
        result = service.prepare_model(model_id, source=source, variant=variant)
        result_status = str(result.get("status") or "ok")
        prepare_status = (
            "done"
            if result_status in {"ok", "ready"}
            else "needs_restart"
            if result_status == "needs_restart"
            else "warning"
        )
        with _prepare_lock:
            _prepare_result.update(
                status=prepare_status,
                message=result.get("message", "Ready."),
                error="",
                restart_recommended=bool(result.get("restart_recommended")),
                installed_packages=list(result.get("installed_packages") or []),
                restart_reason=str(result.get("restart_reason") or ""),
            )
        _logger.info(
            "[MODEL] prepare_finished model_id=%s status=%s restart=%s packages=%s",
            model_id,
            prepare_status,
            bool(result.get("restart_recommended")),
            len(result.get("installed_packages") or []),
        )
        if bool(result.get("restart_recommended")) or result_status == "needs_restart":
            packages = ",".join(result.get("installed_packages") or []) or "runtime"
            _logger.warning(
                "[MODEL] restart_required model_id=%s packages=%s action=close_and_restart_then_prepare_again",
                model_id,
                packages,
            )
    except (ExternalAuthRequiredError, ModelPreparationFailedError) as exc:
        # Forward the rich payload (manual_steps, external_url, target_dir,
        # provider, error type) so the frontend can render a guidance
        # dialog instead of swallowing the recovery path into a toast.
        with _prepare_lock:
            _prepare_result.update(
                status="error",
                error=str(exc),
                message=exc.payload.get("message", str(exc)),
                error_type=str(exc.payload.get("type") or ""),
                provider=str(exc.payload.get("provider") or ""),
                manual_steps=list(exc.payload.get("manual_steps") or []),
                target_dir=str(exc.payload.get("target_dir") or ""),
                external_url=str(exc.payload.get("external_url") or ""),
            )
        _logger.warning(
            "[MODEL] prepare_failed model_id=%s error_type=%s message=%s",
            model_id,
            exc.payload.get("type") or type(exc).__name__,
            str(exc),
            extra={
                "starter_console_message": (
                    f"[MODEL] prepare_failed model_id={model_id} "
                    f"error_type={exc.payload.get('type') or type(exc).__name__} "
                    f"provider={exc.payload.get('provider') or 'external'} "
                    "action=follow Model Manager recovery steps and retry"
                ),
            },
        )
    except UnsupportedOptionalDependencyError as exc:
        message = str(exc)
        normalized_model_id = model_id.strip().lower()
        manual_steps = (
            [
                "Keep using the core Gallery, metadata, sorting, and ONNX features on this Mac.",
                "Use a Windows or Linux machine with an NVIDIA CUDA GPU for SAM3.",
            ]
            if normalized_model_id == "sam3"
            else [
                "Keep using the core Gallery, metadata, sorting, and ONNX features on this Mac.",
                "Use Apple Silicon with macOS 14 or newer, Windows, or Linux for Torch-backed AI features.",
            ]
        )
        with _prepare_lock:
            _prepare_result.update(
                status="error",
                error=message,
                message=message,
                error_type="UnsupportedPlatformRuntime",
                provider="Torch / CUDA runtime",
                manual_steps=manual_steps,
            )
        _logger.warning(
            "[MODEL] prepare_failed model_id=%s error_type=%s message=%s",
            model_id,
            "UnsupportedPlatformRuntime",
            message,
        )
    except UnsafeDependencyInstallError as exc:
        message = str(exc)
        with _prepare_lock:
            _prepare_result.update(
                status="error",
                error=message,
                message=message,
                error_type="UnsafeSystemPythonInstall",
                provider="Python runtime",
                manual_steps=[
                    "Close this SD Image Sorter window.",
                    "Start the app with run.bat, run-portable.bat, or run.sh so it uses the app-owned Python runtime.",
                    "Open Feature Setup again and click Prepare for this feature.",
                    "If you intentionally manage your own Python, activate a virtual environment first or set SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL=1.",
                ],
            )
        _logger.warning(
            "[MODEL] prepare_failed model_id=%s error_type=%s message=%s",
            model_id,
            "UnsafeSystemPythonInstall",
            message,
        )
    except ValueError as exc:
        with _prepare_lock:
            _prepare_result.update(status="error", error=str(exc), message=str(exc))
        _logger.warning(
            "[MODEL] prepare_failed model_id=%s error_type=%s message=%s",
            model_id,
            type(exc).__name__,
            str(exc),
        )
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        _logger.exception(
            "Model preparation failed for %s",
            model_id,
            extra={"starter_console_suppress": True},
        )
        with _prepare_lock:
            _prepare_result.update(
                status="error",
                error=error_message,
                message=error_message,
            )
        if "This checkpoint is gated:" in error_message:
            console_message = (
                f"[MODEL] prepare_failed model_id={model_id} error_type={error_type} "
                "action=accept Hugging Face terms, configure a token, then retry Prepare / Download"
            )
        else:
            compact_message = " ".join(error_message.split())
            if len(compact_message) > 320:
                compact_message = compact_message[:317].rstrip() + "..."
            console_message = (
                f"[MODEL] prepare_failed model_id={model_id} error_type={error_type} "
                f"message={compact_message}"
            )
        _logger.error(
            "[MODEL] prepare_failed model_id=%s error_type=%s message=%s",
            model_id,
            error_type,
            error_message,
            extra={"starter_console_message": console_message},
        )
    finally:
        with _prepare_lock:
            _prepare_result["active"] = False


@router.post("/prepare")
async def prepare_model(
    request: PrepareModelRequest,
    service: ModelService = Depends(get_model_service),
):
    global _prepare_result
    with _prepare_lock:
        if _prepare_result.get("active"):
            return {
                "status": "downloading",
                "model_id": _prepare_result["model_id"],
                "message": "A download is already in progress.",
            }
        # Wipe any stale fields from the previous prepare so the UI does not
        # render last-run's success message against this run's model_id.
        _prepare_result = _empty_prepare_result()
        _prepare_result.update(
            active=True,
            model_id=request.model_id,
            status="downloading",
            message="",
            error="",
        )
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_prepare_blocking, service, request.model_id, request.source, request.variant)
    return {"status": "downloading", "model_id": request.model_id, "message": "Download started in background."}
