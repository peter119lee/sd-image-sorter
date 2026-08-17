"""VLM captioning API router."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, TypedDict
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from config import CONFIG_DIR
from services.gallery_job_gate import gallery_job_activity
from utils.source_paths import resolve_existing_indexed_image_path
from vlm_providers import (
    PROMPT_PRESETS,
    VLMConfig,
    detect_provider,
    get_provider,
    list_providers,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vlm", tags=["vlm"])

VLM_SETTINGS_PATH = CONFIG_DIR / "vlm-settings.json"


# ---------------------------------------------------------------------------
# Decomposition (2026-07): request models + persistence-store types live in
# routers/vlm_models.py; _persist_vlm_result/_persist_tags in
# routers/vlm_persistence.py; pure debug/redaction/coercion helpers in
# routers/vlm_debug.py; batch image-source builders in
# routers/vlm_batch_source.py; the 5 Ollama endpoints (route positions 13-17)
# + _pull_state + _do_pull in routers/vlm_ollama.py, which registers on THIS
# module's shared ``router`` at the tail import at the bottom of this file
# (routers/images_parts precedent).
# THIS module stays a real FILE and the single import/monkeypatch surface:
# the 3 rebind seams (_batch_task/_pull_task/_debug_chat_next_id) +
# _batch_state(+lock) + the caption-batch accessor quartet + _build_config +
# _load_/_save_vlm_settings + VLM_SETTINGS_PATH + _resolve_image_path +
# _run_batch and every reader-patched name stay DEFINED here with ALL their
# callers; everything below is re-imported BY REFERENCE so every historical
# ``routers.vlm.<name>`` read (and monkeypatch) resolves to the SAME objects
# (tests/test_vlm_router_pins.py census). Header imports stay verbatim even
# where now unused (F401 ignored in pyproject.toml, like routers/images.py).
from routers.vlm_models import (
    BatchCaptionRequest, CaptionSingleRequest, DeleteModelRequest,
    DetectProviderRequest, ProbeConcurrencyRequest, PullModelRequest,
    SaveSettingsRequest, VLMResultPersistenceError, _BatchImageSource,
    _PersistedVLMTagRow, _StoredVLMTagRow, _VLMImageUpdate, _VLMPersistenceStore,
)
from routers.vlm_persistence import _persist_tags, _persist_vlm_result
from routers.vlm_debug import (
    _build_debug_request_event, _coerce_float_setting, _coerce_int_setting,
    _normalize_openai_endpoint, _redact_debug_endpoint, _redact_debug_text,
    _utc_now_iso,
)
from routers.vlm_batch_source import (
    _BATCH_ID_CHUNK_SIZE, _build_batch_image_source,
    _create_selection_token_from_filters, _filters_to_selection_kwargs,
    _iter_image_id_chunks,
)


_batch_state_lock = threading.Lock()
_batch_state: Dict[str, Any] = {
    "running": False,
    "cancel_requested": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "tokens_used": 0,
    "errors": [],
    "current_image": "",
    "active_requests": 0,
    "api_status": "idle",
    "api_message": "",
    "api_ok": 0,
    "api_error": 0,
    "last_api_latency_ms": None,
    "last_api_error": "",
    "output_format": "nl_caption",
}

_debug_chat_events: List[Dict[str, Any]] = []
_debug_chat_next_id = 1
_DEBUG_CHAT_LIMIT = 80

# Strong references to fire-and-forget asyncio tasks. The event loop only
# keeps weak references, so without these a garbage-collected task would
# silently stop mid-run and leave running/pulling flags stuck True forever.
_batch_task: Optional["asyncio.Task"] = None
_pull_task: Optional["asyncio.Task"] = None


def is_caption_batch_active() -> bool:
    """Narrow probe for the unified tagging coordinator.

    Exposed so services/tagging_pipeline_service.py can mutually exclude the
    VLM caption batch against Smart Tag / AI Tag without reaching into this
    router's internal batch state.
    """
    with _batch_state_lock:
        running = _batch_state.get("running")
        if not isinstance(running, bool):
            raise TypeError("VLM caption batch state must include boolean running")
        return running


def claim_caption_batch_slot() -> None:
    """Atomically claim the caption-batch slot (check-and-set).

    Raises HTTPException(409) if a caption batch is already running,
    otherwise marks the batch state as running. Only the unified tagging
    coordinator calls this (under its shared start lock), for both the
    HTTP start path and queued-job dispatch, so no other tagging job can
    interleave between the check and the claim.
    """
    with _batch_state_lock:
        if _batch_state["running"]:
            raise HTTPException(409, "Batch captioning already in progress")
        # Claim only the running flag here; the full progress payload is
        # filled in once the image source has been resolved.
        _batch_state["running"] = True
        _batch_state["cancel_requested"] = False


def release_caption_batch_slot(error: str = "") -> None:
    """Release a claimed batch slot without running a batch (failed start)."""
    with _batch_state_lock:
        _batch_state["running"] = False
        if error:
            _batch_state["api_status"] = "error"
            _batch_state["api_message"] = "VLM batch failed to start"
            _batch_state["last_api_error"] = str(error)


def _set_batch_task(task: Optional["asyncio.Task"]) -> None:
    """Retain the caption-batch task so it cannot be garbage-collected."""
    global _batch_task
    _batch_task = task
    if task is not None and hasattr(task, "add_done_callback"):
        task.add_done_callback(_on_batch_task_done)


def _on_batch_task_done(task: "asyncio.Task") -> None:
    global _batch_task
    _batch_task = None
    exc = None if task.cancelled() else task.exception()
    if exc is None and not task.cancelled():
        return
    if exc is not None:
        logger.error("VLM caption batch task crashed: %s", exc)
    # The batch coroutine died before its completion block ran; release the
    # running flag so the next start is not wedged behind a ghost batch.
    with _batch_state_lock:
        if _batch_state.get("running"):
            _batch_state["running"] = False
            _batch_state["current_image"] = ""
            _batch_state["active_requests"] = 0
            _batch_state["api_status"] = "error"
            _batch_state["api_message"] = "VLM batch task stopped unexpectedly"
            if exc is not None:
                _batch_state["last_api_error"] = str(exc)


def _set_pull_task(task: Optional["asyncio.Task"]) -> None:
    """Retain the Ollama pull task so it cannot be garbage-collected."""
    global _pull_task
    _pull_task = task
    if task is not None and hasattr(task, "add_done_callback"):
        task.add_done_callback(_on_pull_task_done)


def _on_pull_task_done(task: "asyncio.Task") -> None:
    global _pull_task
    _pull_task = None
    exc = None if task.cancelled() else task.exception()
    if exc is None and not task.cancelled():
        return
    if exc is not None:
        logger.error("Ollama pull task crashed: %s", exc)
        _pull_state["status"] = f"error: {exc}"
    _pull_state["pulling"] = False


def _load_vlm_settings() -> Dict[str, Any]:
    if not VLM_SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(VLM_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_vlm_settings(settings: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in settings.items() if k != "api_key_display" and k != "service_account_json_display"}
    VLM_SETTINGS_PATH.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")


def _resolve_image_path(image: Dict[str, Any]) -> str:
    image_path = str((image or {}).get("path") or "")
    resolved_path = resolve_existing_indexed_image_path(image_path, backend_file=__file__)
    if not resolved_path:
        raise HTTPException(404, "Image file not found on disk")
    return resolved_path


def _append_debug_chat_event(event: Dict[str, Any]) -> int:
    global _debug_chat_next_id
    with _batch_state_lock:
        event_id = _debug_chat_next_id
        _debug_chat_next_id += 1
        safe_event = {"id": event_id, "at": _utc_now_iso(), **event}
        _debug_chat_events.append(safe_event)
        if len(_debug_chat_events) > _DEBUG_CHAT_LIMIT:
            del _debug_chat_events[:-_DEBUG_CHAT_LIMIT]
        return event_id


def _append_debug_response_event(
    *,
    request_event_id: int,
    image_id: int,
    image_name: str,
    result: Any,
    latency_ms: int,
) -> None:
    _append_debug_chat_event({
        "phase": "response" if not getattr(result, "error", None) else "error",
        "request_id": request_event_id,
        "image_id": image_id,
        "image_name": image_name,
        "model": getattr(result, "model", "") or "",
        "latency_ms": latency_ms,
        "tokens_used": int(getattr(result, "tokens_used", 0) or 0),
        "caption": _redact_debug_text(getattr(result, "caption", "")),
        "tags": list(getattr(result, "tags", []) or [])[:120],
        "raw_text": _redact_debug_text(getattr(result, "raw_text", "")),
        "error": _redact_debug_text(getattr(result, "error", "") or ""),
        "error_type": getattr(result, "error_type", "") or "",
        "retries_used": int(getattr(result, "retries_used", 0) or 0),
    })


def _reset_debug_chat_events() -> None:
    global _debug_chat_next_id
    with _batch_state_lock:
        _debug_chat_events.clear()
        _debug_chat_next_id = 1


def _build_config(overrides: Optional[Dict[str, Any]] = None) -> VLMConfig:
    settings = _load_vlm_settings()
    if overrides:
        settings.update({k: v for k, v in overrides.items() if v is not None})
    provider = settings.get("provider", "openai_compat")
    endpoint = settings.get("endpoint", "")
    # OpenAI-compatible gateways always live under /v1; auto-pad missing paths
    # so URLs saved without the suffix still hit /v1/chat/completions and
    # /v1/models correctly.
    if provider == "openai_compat" and endpoint:
        endpoint = _normalize_openai_endpoint(endpoint)
    return VLMConfig(
        provider=provider,
        endpoint=endpoint,
        api_key=settings.get("api_key", ""),
        model=settings.get("model", ""),
        max_retries=_coerce_int_setting(settings.get("max_retries"), 3, minimum=0, maximum=10),
        retry_delay_seconds=_coerce_float_setting(settings.get("retry_delay_seconds"), 2.0, minimum=0.0, maximum=60.0),
        timeout_seconds=_coerce_float_setting(settings.get("timeout_seconds"), 60.0, minimum=1.0, maximum=600.0),
        concurrent_requests=_coerce_int_setting(settings.get("concurrent_requests"), 2, minimum=1, maximum=16),
        system_prompt=settings.get("system_prompt", ""),
        user_prompt=settings.get("user_prompt", ""),
        user_prompt_with_tags=settings.get("user_prompt_with_tags", ""),
        include_tags_as_context=bool(settings.get("include_tags_as_context", True)),
        max_image_size=_coerce_int_setting(settings.get("max_image_size"), 1024, minimum=128, maximum=4096),
        nsfw_retry_prompt=settings.get("nsfw_retry_prompt", ""),
        output_format=settings.get("output_format", "nl_caption"),
        caption_max_tokens=_coerce_int_setting(settings.get("caption_max_tokens"), 1024, minimum=64, maximum=8192),
        caption_temperature=_coerce_float_setting(settings.get("caption_temperature"), 0.3, minimum=0.0, maximum=2.0),
        http_proxy=settings.get("http_proxy", ""),
        https_proxy=settings.get("https_proxy", ""),
        socks_proxy=settings.get("socks_proxy", ""),
        use_vertex=bool(settings.get("use_vertex", False)),
        vertex_project=settings.get("vertex_project", ""),
        vertex_location=settings.get("vertex_location", "us-central1"),
        service_account_json=settings.get("service_account_json", ""),
    )


# === API Endpoints ===


@router.get("/providers")
async def get_providers():
    return {"providers": list_providers()}


@router.post("/detect-provider")
async def detect_provider_endpoint(request: DetectProviderRequest):
    """Auto-detect provider from endpoint URL pattern."""
    provider = detect_provider(request.endpoint)
    return {"provider": provider}


@router.get("/presets")
async def get_presets():
    return {"presets": PROMPT_PRESETS}


@router.get("/settings")
async def get_settings():
    settings = _load_vlm_settings()
    if settings.get("api_key"):
        settings["api_key_display"] = settings["api_key"][:8] + "***"
        del settings["api_key"]
    if settings.get("service_account_json"):
        settings["service_account_json_display"] = "*** (configured)"
        del settings["service_account_json"]
    return settings


@router.post("/settings")
async def save_settings(request: SaveSettingsRequest):
    current = _load_vlm_settings()
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    current.update(updates)
    _save_vlm_settings(current)
    return {"status": "ok"}


@router.post("/test")
async def test_connection():
    config = _build_config()
    if not config.endpoint and not config.use_vertex:
        raise HTTPException(400, "No endpoint configured")
    provider = get_provider(config)
    result = await provider.test_connection()
    return result


async def _run_concurrency_probe_level(provider: Any, level: int) -> Dict[str, Any]:
    """Fire ``level`` concurrent test_connection calls and summarise outcomes."""
    started = time.perf_counter()
    raw = await asyncio.gather(
        *[provider.test_connection() for _ in range(level)],
        return_exceptions=True,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    ok = 0
    errors: List[str] = []
    rate_limited = 0
    for item in raw:
        if isinstance(item, Exception):
            errors.append(str(item)[:200])
            continue
        if not isinstance(item, dict):
            errors.append(f"unexpected result type: {type(item).__name__}")
            continue
        if item.get("status") == "ok":
            ok += 1
            continue
        err_type = str(item.get("error_type") or "")
        err_msg = str(item.get("error") or item.get("detail") or "error")
        if err_type == "rate_limit" or "rate" in err_msg.lower() and "limit" in err_msg.lower():
            rate_limited += 1
        errors.append(err_msg[:200])
    return {
        "level": level,
        "ok": ok,
        "failed": level - ok,
        "rate_limited": rate_limited,
        "elapsed_ms": elapsed_ms,
        "success": ok == level,
        "errors": errors[:5],
    }


@router.post("/probe-concurrency")
async def probe_concurrency(request: ProbeConcurrencyRequest):
    """Ramp concurrent test_connection calls to find a stable worker count.

    User-facing goal: different VLM APIs have different parallel limits; this
    probe discovers the highest N where all N concurrent health checks succeed,
    then optionally writes it to ``concurrent_requests``.
    """
    config = _build_config()
    if not config.endpoint and not config.use_vertex:
        raise HTTPException(400, "No endpoint configured")
    provider = get_provider(config)

    levels: List[Dict[str, Any]] = []
    recommended = 0
    max_level = int(request.max_level)
    for level in range(1, max_level + 1):
        summary = await _run_concurrency_probe_level(provider, level)
        levels.append(summary)
        if summary["success"]:
            recommended = level
            continue
        # First failing level ends the ramp — higher is unlikely to recover.
        break

    if recommended < 1:
        # Even level-1 failed: still return diagnostics, keep current setting.
        current = _coerce_int_setting(config.concurrent_requests, 2, minimum=1, maximum=16)
        return {
            "status": "error",
            "recommended": current,
            "applied": False,
            "levels": levels,
            "message": "Probe failed at concurrency=1; check connection/API key first.",
        }

    applied = False
    if request.apply:
        current = _load_vlm_settings()
        current["concurrent_requests"] = recommended
        _save_vlm_settings(current)
        applied = True

    return {
        "status": "ok",
        "recommended": recommended,
        "applied": applied,
        "levels": levels,
        "message": f"Highest stable concurrency: {recommended}",
    }


@router.post("/models")
async def fetch_models():
    config = _build_config()
    if not config.endpoint and not config.use_vertex:
        raise HTTPException(400, "No endpoint configured")
    provider = get_provider(config)
    models = await provider.list_models()
    return {"models": models}


@router.post("/caption")
async def caption_single(request: CaptionSingleRequest):
    """Caption a single image by ID."""
    with gallery_job_activity("vlm_caption"):
        import database as db

        image = db.get_image_by_id(request.image_id)
        if not image:
            raise HTTPException(404, "Image not found")

        image_path = _resolve_image_path(image)

        tags = request.tags
        if tags is None:
            tag_rows = db.get_image_tags(request.image_id)
            tags = [t["tag"] for t in tag_rows] if tag_rows else []

        config = _build_config()
        if not config.endpoint and not config.use_vertex:
            raise HTTPException(400, "No VLM endpoint configured")

        provider = get_provider(config)
        result = await provider.caption_image(image_path, tags=tags)

        # Persist results based on output format
        dropped_tags = 0
        if not result.error and (result.caption or result.tags):
            try:
                dropped_tags = _persist_vlm_result(
                    db, request.image_id, result.caption, result.tags
                )
            except VLMResultPersistenceError as exc:
                logger.exception(
                    "VLM single-image result persistence failed",
                    extra={"image_id": request.image_id, "error_type": "persistence"},
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "caption": result.caption,
            "tags": result.tags,
            "tokens_used": result.tokens_used,
            "retries_used": result.retries_used,
            "error": result.error,
            "error_type": result.error_type,
            "model": result.model,
            "output_format": config.output_format,
            "dropped_tags": dropped_tags,
        }


async def _start_claimed_caption_batch(
    request: BatchCaptionRequest,
    config: Optional[VLMConfig] = None,
) -> Dict[str, Any]:
    """Resolve the image source and launch the batch task.

    The batch slot MUST already be claimed (claim_caption_batch_slot)
    before calling this — both the HTTP start path and the queued-job
    dispatch path funnel through here.
    """
    if config is None:
        config = _build_config()

    try:
        # Selection-token / filters sources run a filtered COUNT over the whole
        # library (count_selection_token_ids / create_selection_token) — a slow
        # synchronous SQLite query on large libraries. Resolve it in a worker
        # thread so the event loop stays free (same idiom as colors.start_analysis).
        image_source = await asyncio.to_thread(_build_batch_image_source, request)
        _reset_debug_chat_events()
        with _batch_state_lock:
            _batch_state.update({
                "running": True,
                "cancel_requested": False,
                "total": image_source.total,
                "completed": 0,
                "failed": 0,
                "tokens_used": 0,
                "errors": [],
                "current_image": "",
                "active_requests": 0,
                "api_status": "queued" if image_source.total else "idle",
                "api_message": "Waiting to send images to the VLM API" if image_source.total else "No images queued",
                "api_ok": 0,
                "api_error": 0,
                "last_api_latency_ms": None,
                "last_api_error": "",
                "output_format": config.output_format,
            })
    except BaseException:
        # Source resolution failed after the slot was claimed; release it so
        # the next start is not rejected by a ghost batch.
        with _batch_state_lock:
            _batch_state["running"] = False
        raise

    _set_batch_task(asyncio.create_task(_run_batch(image_source)))
    return {
        "status": "started",
        "total": image_source.total,
        "source": image_source.source_type,
        "output_format": config.output_format,
    }


def start_caption_batch_from_queue(
    payload: Dict[str, Any],
    loop: Optional["asyncio.AbstractEventLoop"],
) -> None:
    """Start a queued caption batch from the pipeline dispatcher thread.

    The dispatcher claims the batch slot first (under the pipeline start
    lock), then calls this to schedule the actual start on the server
    event loop captured when the batch was enqueued. A failure inside the
    scheduled start releases the slot and surfaces in
    /api/vlm/caption-batch/progress (api_status="error") instead of dying
    silently, so the queue keeps draining.
    """
    if loop is None or loop.is_closed():
        raise RuntimeError("No usable event loop to start the queued VLM caption batch")

    request = BatchCaptionRequest(**payload)

    async def _runner() -> None:
        try:
            await _start_claimed_caption_batch(request)
        except BaseException as exc:  # noqa: BLE001 — surface, never die silently
            detail = str(getattr(exc, "detail", None) or exc) or exc.__class__.__name__
            logger.error("Queued VLM caption batch failed to start: %s", detail)
            with _batch_state_lock:
                _batch_state["api_status"] = "error"
                _batch_state["api_message"] = "Queued VLM batch failed to start"
                _batch_state["last_api_error"] = detail

    asyncio.run_coroutine_threadsafe(_runner(), loop)


@router.post("/caption-batch")
async def caption_batch(request: BatchCaptionRequest):
    """Start batch captioning, or queue it behind a running AI job.

    Returns {"status": "started", ...} immediately when the batch starts
    now, or {"status": "queued", "queue_position": N, ...} when another AI
    job (gallery AI Tag / Smart Tag / VLM batch) is running and the batch
    was added to the unified FIFO queue (auto-starts when the running job
    finishes; the queue is persisted and restored across restarts). Poll
    /caption-batch/progress either way.
    """
    from routers.tags import get_tagging_service
    from services.tagging_pipeline_service import get_tagging_pipeline_service

    config = _build_config()

    # Mutual exclusion with Smart Tag / gallery AI Tag: the coordinator runs
    # the claim under its shared start lock so no other tagging job can start
    # between these checks and the running flag being set. When another AI
    # job is active the request is queued instead of rejected with 409.
    queued = get_tagging_pipeline_service().start_vlm_caption_batch(
        claim_caption_batch_slot,
        payload=request.model_dump(),
        loop=asyncio.get_running_loop(),
        legacy_service=get_tagging_service(),
    )
    if queued is not None:
        return queued

    return await _start_claimed_caption_batch(request, config)


@router.get("/caption-batch/progress")
async def batch_progress():
    from services.tagging_pipeline_service import KIND_VLM, get_tagging_pipeline_service

    # Read the queue BEFORE the batch state: if the dispatcher starts a
    # queued batch between the two reads the response shows running=True
    # with an already-empty queue (harmless) instead of "idle + empty
    # queue" for a batch that is actually starting.
    queue_info = get_tagging_pipeline_service().queue_snapshot(KIND_VLM)
    with _batch_state_lock:
        payload = dict(_batch_state)
    payload["pipeline_queue"] = queue_info
    return payload


@router.get("/caption-batch/debug-chat")
async def batch_debug_chat():
    with _batch_state_lock:
        return {
            "events": list(_debug_chat_events),
            "limit": _DEBUG_CHAT_LIMIT,
            "running": bool(_batch_state.get("running")),
        }


@router.post("/caption-batch/cancel")
async def batch_cancel():
    from services.tagging_pipeline_service import KIND_VLM, get_tagging_pipeline_service

    # Queued-job cancellation: the existing cancel endpoint also clears any
    # queued (not-yet-started) VLM caption batches.
    removed = get_tagging_pipeline_service().remove_queued_jobs(KIND_VLM)
    with _batch_state_lock:
        if not _batch_state["running"]:
            if removed > 0:
                return {"status": "queue_cleared", "removed_queued": removed}
            raise HTTPException(400, "No batch in progress")
        _batch_state["cancel_requested"] = True
        _batch_state["api_status"] = "cancelling"
        _batch_state["api_message"] = "Cancel requested; waiting for active API calls to finish"
    return {"status": "cancel_requested", "removed_queued": removed}


async def _run_batch(image_source: _BatchImageSource) -> None:
    """Run batch captioning with concurrency control."""
    import database as db

    try:
        config = _build_config()
        provider = get_provider(config)
        worker_count = max(1, int(config.concurrent_requests or 1))
    except Exception as exc:
        message = str(exc) or "Failed to initialize VLM batch"
        with _batch_state_lock:
            _batch_state["running"] = False
            _batch_state["failed"] = int(_batch_state.get("total") or image_source.total or 0)
            _batch_state["current_image"] = ""
            _batch_state["active_requests"] = 0
            _batch_state["api_status"] = "error"
            _batch_state["api_message"] = "Could not start VLM batch"
            _batch_state["last_api_error"] = message
            if len(_batch_state["errors"]) < 50:
                _batch_state["errors"].append({
                    "image_id": None,
                    "error": message,
                    "error_type": "batch_init",
                })
        _append_debug_chat_event({
            "phase": "error",
            "image_id": None,
            "image_name": "",
            "error": _redact_debug_text(message),
            "error_type": "batch_init",
        })
        return

    async def process_one(image_id: int) -> None:
        with _batch_state_lock:
            if _batch_state["cancel_requested"]:
                return

        try:
            image = db.get_image_by_id(image_id)
            if not image:
                _record_error(image_id, "Image not found in DB", "not_found")
                return

            image_path = str(image.get("path") or "")
            with _batch_state_lock:
                _batch_state["current_image"] = Path(image_path).name if image_path else ""

            try:
                resolved_image_path = _resolve_image_path(image)
            except HTTPException:
                _record_error(image_id, "File not found on disk", "file_missing")
                return

            tag_rows = db.get_image_tags(image_id)
            tags = [t["tag"] for t in tag_rows] if tag_rows else []

            user_message = provider.build_user_message(tags)
            image_name = Path(image_path or resolved_image_path).name
            request_event_id = _append_debug_chat_event(_build_debug_request_event(
                image_id=image_id,
                image_name=image_name,
                config=config,
                provider_name=getattr(provider, "name", config.provider),
                tags=tags,
                user_message=user_message,
            ))

            start_time = time.monotonic()
            with _batch_state_lock:
                _batch_state["active_requests"] += 1
                _batch_state["api_status"] = "waiting"
                _batch_state["api_message"] = f"Waiting for API response: {image_name}"
                _batch_state["last_api_error"] = ""

            result = await provider.caption_image(resolved_image_path, tags=tags)
            latency_ms = int((time.monotonic() - start_time) * 1000)
            _append_debug_response_event(
                request_event_id=request_event_id,
                image_id=image_id,
                image_name=image_name,
                result=result,
                latency_ms=latency_ms,
            )

            if not result.error and (result.caption or result.tags):
                try:
                    _persist_vlm_result(
                        db, image_id, result.caption, result.tags
                    )
                except VLMResultPersistenceError as exc:
                    _append_debug_chat_event({
                        "phase": "error",
                        "image_id": image_id,
                        "image_name": image_name,
                        "error": _redact_debug_text(str(exc)),
                        "error_type": "persistence",
                    })
                    with _batch_state_lock:
                        _batch_state["api_error"] += 1
                        _batch_state["tokens_used"] += result.tokens_used
                        _batch_state["last_api_latency_ms"] = latency_ms
                        _batch_state["api_status"] = "error"
                        _batch_state["api_message"] = "VLM result could not be saved"
                        _batch_state["last_api_error"] = str(exc)
                    _record_error(image_id, str(exc), "persistence")
                    return
                with _batch_state_lock:
                    _batch_state["completed"] += 1
                    _batch_state["tokens_used"] += result.tokens_used
                    _batch_state["api_ok"] += 1
                    _batch_state["last_api_latency_ms"] = latency_ms
                    _batch_state["api_status"] = "responded"
                    _batch_state["api_message"] = f"API response OK in {latency_ms} ms"
            else:
                with _batch_state_lock:
                    _batch_state["api_error"] += 1
                    _batch_state["last_api_latency_ms"] = latency_ms
                    _batch_state["api_status"] = "error"
                    _batch_state["api_message"] = f"API response failed in {latency_ms} ms"
                    _batch_state["last_api_error"] = result.error or "No output"
                _record_error(image_id, result.error or "No output", result.error_type or "unknown")

        except Exception as e:
            try:
                _append_debug_chat_event({
                    "phase": "error",
                    "image_id": image_id,
                    "image_name": Path(image_path).name if "image_path" in locals() and image_path else "",
                    "error": _redact_debug_text(str(e)),
                    "error_type": "exception",
                })
            except Exception:
                pass
            with _batch_state_lock:
                _batch_state["api_error"] += 1
                _batch_state["api_status"] = "error"
                _batch_state["api_message"] = "API request failed before a usable response"
                _batch_state["last_api_error"] = str(e)
            _record_error(image_id, str(e), "exception")
        finally:
            with _batch_state_lock:
                _batch_state["active_requests"] = max(0, int(_batch_state.get("active_requests") or 0) - 1)
                if _batch_state["cancel_requested"]:
                    _batch_state["api_status"] = "cancelling"
                    _batch_state["api_message"] = "Cancel requested; waiting for active API calls to finish"
                elif _batch_state["active_requests"] > 0:
                    _batch_state["api_status"] = "waiting"
                    _batch_state["api_message"] = f"Waiting for {_batch_state['active_requests']} API response(s)"

    async def produce_ids(queue: asyncio.Queue) -> None:
        try:
            for chunk in image_source.iter_chunks():
                with _batch_state_lock:
                    if _batch_state["cancel_requested"]:
                        break
                for image_id in chunk:
                    with _batch_state_lock:
                        if _batch_state["cancel_requested"]:
                            break
                    await queue.put(int(image_id))
        except Exception as exc:
            message = str(exc) or "Failed to resolve VLM batch image IDs"
            _append_debug_chat_event({
                "phase": "error",
                "image_id": None,
                "image_name": "",
                "error": _redact_debug_text(message),
                "error_type": "batch_source",
            })
            _record_error(None, message, "batch_source")
        finally:
            for _ in range(worker_count):
                await queue.put(None)

    async def worker(queue: asyncio.Queue) -> None:
        while True:
            image_id = await queue.get()
            try:
                if image_id is None:
                    return
                await process_one(image_id)
            finally:
                queue.task_done()

    queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, worker_count * 2))
    producer_task = asyncio.create_task(produce_ids(queue))
    worker_tasks = [asyncio.create_task(worker(queue)) for _ in range(worker_count)]
    await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)

    with _batch_state_lock:
        _batch_state["running"] = False
        _batch_state["current_image"] = ""
        _batch_state["active_requests"] = 0
        if _batch_state["cancel_requested"]:
            _batch_state["api_status"] = "cancelled"
            _batch_state["api_message"] = "Cancelled"
        elif _batch_state["failed"] > 0:
            _batch_state["api_status"] = "done_with_errors"
            _batch_state["api_message"] = "Finished with API or image errors"
        else:
            _batch_state["api_status"] = "done"
            _batch_state["api_message"] = "Finished"


def _record_error(image_id: Optional[int], message: str, error_type: str) -> None:
    with _batch_state_lock:
        _batch_state["failed"] += 1
        if _batch_state.get("api_status") not in {"error", "cancelling", "cancelled"}:
            _batch_state["api_status"] = "error"
            _batch_state["api_message"] = message
        _batch_state["last_api_error"] = message
        if len(_batch_state["errors"]) < 50:
            _batch_state["errors"].append({
                "image_id": image_id,
                "error": message,
                "error_type": error_type,
            })


# Ollama local-model endpoints -- imported LAST so route positions 13-17
# register after the 12 endpoints above (registration order == route table;
# the sha256 pin in tests/test_vlm_router_pins.py flips on any reorder).
from routers.vlm_ollama import (
    _do_pull, _pull_state, delete_model, get_recommended_models, pull_model,
    pull_progress, start_ollama,
)
