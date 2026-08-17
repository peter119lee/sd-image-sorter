"""Ollama local-model management endpoints for the VLM captioning router.

Decomposed from routers/vlm.py (2026-07): a verbatim slice of the pre-split
lines 1154-1182, 1187-1224 and 1229-1247 (vlm_local_models row). Import routers.vlm (the facade), NOT
this module: the facade imports this module LAST, so the five @router
endpoints here register at route-table positions 13-17 on the ONE shared
``router`` defined in routers/vlm.py (routers/images_parts precedent; the
registration order is pinned by the sha256 canary in
tests/test_vlm_router_pins.py). _pull_state is defined here and
re-imported BY REFERENCE on the facade (mutation-only; never rebound); the
_pull_task rebind seam and its _set_pull_task/_on_pull_task_done handlers
stay on the facade and mutate the SAME dict.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import HTTPException

from routers.vlm import _set_pull_task, router
from routers.vlm_models import DeleteModelRequest, PullModelRequest


# === Local Model Management (Ollama) ===


_pull_state: Dict[str, Any] = {"pulling": False, "model": "", "percent": 0, "status": ""}


@router.get("/local-models/recommended")
async def get_recommended_models():
    from vlm_providers.local_models import RECOMMENDED_MODELS, OllamaManager
    mgr = OllamaManager()
    installed = await mgr.is_running()
    local = await mgr.list_local_models() if installed else []
    local_ids = {m["id"] for m in local}

    models = []
    for m in RECOMMENDED_MODELS:
        entry = dict(m)
        entry["installed"] = m["id"] in local_ids
        models.append(entry)

    return {
        "ollama_installed": OllamaManager.is_ollama_installed(),
        "ollama_running": installed,
        "install_instructions": OllamaManager.get_install_instructions() if not OllamaManager.is_ollama_installed() else None,
        "models": models,
        "local_models": local,
    }


@router.post("/local-models/pull")
async def pull_model(request: PullModelRequest):
    """Start pulling a model. Poll /local-models/pull/progress for status."""
    from vlm_providers.local_models import OllamaManager

    if _pull_state["pulling"]:
        raise HTTPException(409, f"Already pulling: {_pull_state['model']}")

    mgr = OllamaManager()
    if not await mgr.is_running():
        start_result = await OllamaManager.start_ollama()
        if start_result.get("status") != "ok":
            raise HTTPException(503, start_result.get("error", "Cannot start Ollama"))

    _pull_state.update({"pulling": True, "model": request.model, "percent": 0, "status": "starting"})
    _set_pull_task(asyncio.create_task(_do_pull(request.model)))
    return {"status": "started", "model": request.model}


@router.get("/local-models/pull/progress")
async def pull_progress():
    return dict(_pull_state)


async def _do_pull(model_name: str) -> None:
    from vlm_providers.local_models import OllamaManager
    mgr = OllamaManager()
    try:
        async for progress in mgr.pull_model(model_name):
            _pull_state["percent"] = progress.get("percent", 0)
            _pull_state["status"] = progress.get("status", "")
            if progress.get("status") == "error":
                _pull_state["status"] = f"error: {progress.get('error', '')}"
                break
    except Exception as e:
        _pull_state["status"] = f"error: {e}"
    finally:
        _pull_state["pulling"] = False


@router.post("/local-models/delete")
async def delete_model(request: DeleteModelRequest):
    from vlm_providers.local_models import OllamaManager
    mgr = OllamaManager()
    success = await mgr.delete_model(request.model)
    if not success:
        raise HTTPException(500, "Failed to delete model")
    return {"status": "ok"}


@router.post("/local-models/start-ollama")
async def start_ollama():
    from vlm_providers.local_models import OllamaManager
    result = await OllamaManager.start_ollama()
    if result.get("status") != "ok":
        raise HTTPException(503, result.get("error", "Cannot start Ollama"))
    return result
