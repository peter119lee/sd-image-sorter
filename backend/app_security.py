"""Security and request middleware wiring for the FastAPI app."""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import (
    CORS_ORIGIN_REGEX,
    RATE_LIMIT_APPLY_TO_LOOPBACK as CONFIG_RATE_LIMIT_APPLY_TO_LOOPBACK,
    RATE_LIMIT_ENABLED as CONFIG_RATE_LIMIT_ENABLED,
    RATE_LIMIT_MAX_REQUESTS as CONFIG_RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS as CONFIG_RATE_LIMIT_WINDOW_SECONDS,
)


logger = logging.getLogger("sd-image-sorter")

LOCALHOST_ALIASES = {"127.0.0.1", "localhost", "::1", "[::1]"}
RATE_LIMIT_ENABLED = CONFIG_RATE_LIMIT_ENABLED
RATE_LIMIT_WINDOW_SECONDS = CONFIG_RATE_LIMIT_WINDOW_SECONDS
RATE_LIMIT_MAX_REQUESTS = CONFIG_RATE_LIMIT_MAX_REQUESTS
RATE_LIMIT_APPLY_TO_LOOPBACK = CONFIG_RATE_LIMIT_APPLY_TO_LOOPBACK
RATE_LIMIT_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json"}
RATE_LIMIT_EXEMPT_PREFIXES = ("/static", "/api/image-file", "/api/image-thumbnail")
_rate_limit_lock = threading.Lock()
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_cleanup_time = [0.0]
_RATE_LIMIT_CLEANUP_INTERVAL = 300


def _is_loopback_host(host: Optional[str]) -> bool:
    """Return True when the host refers to the local machine."""
    if not host:
        return False
    if host == "testclient" and os.environ.get("SD_SORTER_TESTING") == "1":
        return True
    if host in LOCALHOST_ALIASES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_rate_limit_exempt(path: str) -> bool:
    """Return True when a request path should skip in-memory rate limiting."""
    return path in RATE_LIMIT_EXEMPT_PATHS or path.startswith(RATE_LIMIT_EXEMPT_PREFIXES)


async def localhost_only_middleware(request: Request, call_next):
    """Reject non-loopback clients.

    Production bind in ``main.py`` is already loopback. This is a second
    guard if the bind host is ever widened.
    """
    client_host = request.client.host if request.client else None
    if client_host and not _is_loopback_host(client_host):
        logger.warning("Rejected non-local request from %s to %s", client_host, request.url.path)
        return JSONResponse(
            status_code=403,
            content={"error": "This application only accepts local requests", "type": "Forbidden"},
        )
    return await call_next(request)


async def rate_limit_middleware(request: Request, call_next):
    """Apply a lightweight in-memory rate limit to API requests."""
    if not RATE_LIMIT_ENABLED:
        return await call_next(request)

    path = request.url.path
    if _is_rate_limit_exempt(path):
        return await call_next(request)

    client_host = request.client.host if request.client else "unknown"
    if client_host and _is_loopback_host(client_host) and not RATE_LIMIT_APPLY_TO_LOOPBACK:
        return await call_next(request)

    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        bucket = _rate_limit_buckets[client_host]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning("Rate limit exceeded for %s on %s", client_host, path)
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests. Please try again shortly.", "type": "RateLimitExceeded"},
            )
        bucket.append(now)

        if now - _rate_limit_cleanup_time[0] > _RATE_LIMIT_CLEANUP_INTERVAL:
            _rate_limit_cleanup_time[0] = now
            stale_keys = [key for key, value in _rate_limit_buckets.items() if not value]
            for key in stale_keys:
                del _rate_limit_buckets[key]

    return await call_next(request)


async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def library_workspace_middleware(request: Request, call_next):
    """Bind long-lived library id for the request (multi-library isolation)."""
    from library_context import (
        LIBRARY_HEADER,
        bind_library_id_from_header,
        reset_current_library_id,
    )

    token = bind_library_id_from_header(request.headers.get(LIBRARY_HEADER))
    try:
        return await call_next(request)
    finally:
        reset_current_library_id(token)


def configure_security_middleware(app: FastAPI) -> None:
    """Attach CORS, local-only, rate-limit, and security-header middleware."""
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "X-Requested-With",
            "X-SD-Library-Id",
        ],
    )
    app.middleware("http")(localhost_only_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(library_workspace_middleware)
    app.middleware("http")(add_security_headers)

