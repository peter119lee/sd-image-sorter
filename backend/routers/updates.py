"""
Self-update endpoints for package-local releases.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.service_provider import ServiceProvider
from services.update_service import UpdateService


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/updates", tags=["updates"])

_update_service_provider = ServiceProvider(UpdateService)


class ApplyUpdateRequest(BaseModel):
    force_check: bool = True
    relaunch: bool = True


class RestartAppRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class UpdateProxyConfigRequest(BaseModel):
    proxy_prefix: str
    channel_name: str = "Custom Proxy"


get_update_service = _update_service_provider.get
set_update_service = _update_service_provider.set


def _schedule_process_exit(delay_seconds: float = 1.0) -> None:
    if os.environ.get("SD_SORTER_TESTING") == "1":
        return

    def _exit_worker() -> None:
        time.sleep(max(0.1, delay_seconds))
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=_exit_worker, daemon=True).start()


@router.get("/status")
def get_update_status(force: bool = Query(False)) -> dict:
    """Check the configured release channel for a newer package version."""
    return get_update_service().get_status(force=force)


@router.get("/channel")
def get_update_channel() -> dict:
    """Return the effective update channel settings."""
    return get_update_service().get_channel_settings()


@router.post("/channel/proxy")
def set_update_channel_proxy(payload: UpdateProxyConfigRequest) -> dict:
    """Store a package-local update proxy prefix and derive channel URLs from it."""
    try:
        return get_update_service().save_proxy_channel(
            payload.proxy_prefix,
            channel_name=payload.channel_name,
        )
    except Exception as exc:
        logger.warning("Failed to save update proxy: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/channel")
def reset_update_channel() -> dict:
    """Remove the package-local update channel override and fall back to defaults."""
    return get_update_service().reset_channel_settings()


@router.post("/restart", summary="Restart the app without applying an update")
def restart_app(payload: RestartAppRequest) -> dict:
    """Relaunch through the original launcher so a finished install takes effect.

    Feature setup needs this when an install replaced a module that was already
    loaded. Nothing is patched or downloaded here -- the worker just waits for
    this process to exit and starts the launcher again.
    """
    reason = payload.reason or ""
    try:
        result = get_update_service().restart_app(reason=reason)
    except Exception as exc:
        logger.warning("Failed to schedule app restart: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result.get("status") == "scheduled":
        _schedule_process_exit()

    return result


@router.post("/apply")
def apply_update(payload: ApplyUpdateRequest) -> dict:
    """
    Download and stage the latest update, then shut down so the worker can patch files.
    """
    try:
        result = get_update_service().prepare_update(
            force_check=payload.force_check,
            relaunch=payload.relaunch,
        )
    except Exception as exc:
        logger.warning("Failed to apply update: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result.get("status") == "scheduled":
        _schedule_process_exit()

    return result
