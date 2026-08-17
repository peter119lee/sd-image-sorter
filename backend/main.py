"""
FastAPI backend for SD Image Sorter.
Provides REST API for image management, tagging, and sorting.

This is the main application entry point. Endpoints are organized into routers:
- routers/images.py - Image retrieval and serving
- routers/tags.py - Tag management and tagging
- routers/sorting.py - Scanning, moving, and manual sorting
- routers/censor.py - NSFW detection and censoring
"""
import os
import sys
import asyncio
import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING
from contextlib import asynccontextmanager

if TYPE_CHECKING:
    from tagger import WD14Tagger

# Add current dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from runtime_env import prepare_onnxruntime_environment

prepare_onnxruntime_environment()

from app_info import APP_VERSION
from config import (
    SERVER_HOST,
    SERVER_PORT,
    LOG_LEVEL,
    LOG_ACCESS_ENABLED,
    LOG_FILE_ENABLED,
    LOG_FILE_PATH,
    LOG_FILE_MAX_BYTES,
    LOG_FILE_BACKUP_COUNT,
    BACKEND_DIR,
    validate_config,
    ensure_directories,
)
from model_console_logging import StarterConsoleFilter, StarterConsoleFormatter

# Configure logging
def configure_console_logging() -> None:
    """Keep normal console output quiet while preserving a support log file."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    formatter = logging.Formatter(log_format, datefmt="%H:%M:%S")
    logging.basicConfig(level=level, format=log_format, datefmt="%H:%M:%S")
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    console_formatter = StarterConsoleFormatter()
    console_filter = StarterConsoleFilter()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler,
            RotatingFileHandler,
        ):
            handler.setFormatter(console_formatter)
            handler.addFilter(console_filter)
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if LOG_ACCESS_ENABLED else logging.WARNING
    )

    if not LOG_FILE_ENABLED:
        return

    log_path = Path(LOG_FILE_PATH)
    existing_paths = {
        str(getattr(handler, "baseFilename", ""))
        for handler in root_logger.handlers
        if isinstance(handler, RotatingFileHandler)
    }
    if str(log_path) in existing_paths:
        return

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger("sd-image-sorter").warning(
            "Could not initialize file log at %s: %s",
            LOG_FILE_PATH,
            exc,
        )


configure_console_logging()
logger = logging.getLogger("sd-image-sorter")

from PIL import Image as _PILImage
_PILImage.MAX_IMAGE_PIXELS = 178956970  # ~13400x13400, prevents decompression bombs

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import app_static
from app_diagnostics import build_support_diagnostics, open_support_log_file
from app_security import _is_loopback_host, configure_security_middleware
from app_static import mount_frontend_static, serve_frontend_index, static_cache_bust_token

_STATIC_CACHE_BUST_RE = app_static._STATIC_CACHE_BUST_RE

import database as db
from ai_runtime_guard import AiRuntimeBusyError
from exceptions import (
    SDImageSorterError,
    ImageNotFoundError,
    ImageFileNotFoundError,
    TaggingError,
    ScanError,
    ConfigurationError,
    ValidationError,
    FileOperationError,
    DatabaseError,
    ModelLoadError,
    OperationInProgressError,
    PathSecurityError,
)

# Import routers
from routers import images, tags, sorting, censor, prompts, similarity, artists, models, obfuscation, aesthetic, updates, disk, vlm, colors, tags_bulk, dataset, dataset_projects, annotations, smart_tag, collections, entry, libraries, duplicates, publish, metadata_repair, masks

# Import services
from services import (
    ImageService,
    SortingService,
    CensorService,
    SimilarityService,
)


# Service instances (singleton pattern)
_image_service: Optional[ImageService] = None
_sorting_service: Optional[SortingService] = None
_censor_service: Optional[CensorService] = None
_similarity_service: Optional[SimilarityService] = None

# Lock for thread-safe singleton creation
_service_lock = threading.Lock()


def get_tagger(
    model_name: Optional[str] = None,
    model_path: Optional[str] = None,
    tags_path: Optional[str] = None,
    threshold: float = 0.35,
    character_threshold: float = 0.85,
    use_gpu: bool = True
) -> "WD14Tagger":
    """Get or create the tagger instance with given settings."""
    from tagger import get_tagger as _get_tagger, DEFAULT_MODEL

    model_name = model_name or DEFAULT_MODEL

    return _get_tagger(
        model_name=model_name,
        model_path=model_path,
        tags_path=tags_path,
        threshold=threshold,
        character_threshold=character_threshold,
        use_gpu=use_gpu
    )


def get_image_service() -> ImageService:
    """Get the ImageService singleton with thread-safe double-checked locking."""
    global _image_service
    if _image_service is None:
        with _service_lock:
            if _image_service is None:
                _image_service = ImageService()
    return _image_service



def get_sorting_service() -> SortingService:
    """Get the SortingService singleton with thread-safe double-checked locking."""
    global _sorting_service
    if _sorting_service is None:
        with _service_lock:
            if _sorting_service is None:
                _sorting_service = SortingService()
    return _sorting_service


def get_censor_service() -> CensorService:
    """Get the CensorService singleton with thread-safe double-checked locking."""
    global _censor_service
    if _censor_service is None:
        with _service_lock:
            if _censor_service is None:
                _censor_service = CensorService()
    return _censor_service


def get_similarity_service() -> SimilarityService:
    """Get the SimilarityService singleton with thread-safe double-checked locking."""
    global _similarity_service
    if _similarity_service is None:
        with _service_lock:
            if _similarity_service is None:
                _similarity_service = SimilarityService()
    return _similarity_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown handler."""
    # Startup
    logger.info("SD Image Sorter backend starting...")
    _install_windows_loop_exception_handler()

    # Capture the running server loop so the tagging pipeline dispatcher can
    # re-submit a VLM caption batch restored from disk — its original request
    # loop is gone after a restart. Harmless no-op for gallery/smart jobs.
    from services.tagging_pipeline_service import set_server_loop
    set_server_loop(asyncio.get_running_loop())

    # Ensure all required directories exist
    ensure_directories()

    # Validate configuration and log warnings
    config_warnings = validate_config()
    for warning in config_warnings:
        logger.warning(f"Configuration warning: {warning}")

    db.init_db()

    # Purge stale Dataset Maker scan-token manifests so data/dataset-scans/
    # doesn't grow without bound across long-running installs. Tokens are
    # NDJSON files created by /api/dataset/folder-scan; the frontend re-issues
    # a fresh scan when an expired token is referenced, so deleting old ones
    # is safe. Best-effort: a failure here must not block startup.
    try:
        from services.dataset_session_service import purge_expired_scan_manifests
        removed = purge_expired_scan_manifests()
        if removed:
            logger.info("Purged %d expired dataset scan-token manifest(s).", removed)
    except Exception:
        logger.warning("Dataset scan-token purge failed; continuing startup.", exc_info=True)

    bind_host = os.environ.get("SD_IMAGE_SORTER_BIND_HOST", SERVER_HOST)
    if not _is_loopback_host(bind_host):
        raise RuntimeError(
            "This application only allows localhost binding. Set SD_IMAGE_SORTER_BIND_HOST to 127.0.0.1 or localhost."
        )

    # Initialize services
    logger.info("Initializing services...")
    image_svc = get_image_service()
    tagging_svc = tags.get_tagging_service()
    sorting_svc = get_sorting_service()
    censor_svc = get_censor_service()
    similarity_svc = get_similarity_service()

    # Load sort session from disk
    sorting_svc.load_session_from_disk()

    # Set tagger getter for tagging service
    tagging_svc.set_tagger_getter(get_tagger)

    # Configure routers with service instances
    images.set_image_service(image_svc)
    tags.set_tagging_service(tagging_svc)
    sorting.set_sorting_service(sorting_svc)
    censor.set_censor_service(censor_svc)
    similarity.set_similarity_service(similarity_svc)

    logger.info("Services initialized successfully")

    yield
    # Shutdown
    logger.info("Shutting down...")


app = FastAPI(
    title="SD Image Sorter",
    description="""
# SD Image Sorter API

A local web application for managing, tagging, sorting, and censoring Stable Diffusion generated images.

## Features

- **Image Management**: Scan folders, retrieve images with filters, serve files
- **AI Tagging**: WD14 tagger for automatic image tagging
- **Sorting**: Batch move operations and manual keyboard sorting sessions
- **Censoring**: NSFW detection with multiple backends (YOLOv8, NudeNet, SAM3)
- **Similarity Search**: CLIP-based image similarity and duplicate detection
- **Prompt Generation**: Intelligent prompt builder with exclusion rules

## Authentication

**None required.** This is a local-only application with CORS restricted to localhost.

## Interactive Documentation

- **Swagger UI**: `/docs`
- **ReDoc**: `/redoc`

## Getting Started

1. Scan a folder: `POST /api/scan`
2. Tag images: `POST /api/tag`
3. Browse images: `GET /api/images`
4. Sort manually: `POST /api/sort/start`

For detailed documentation, see `docs/API.md` in the GitHub repository:
https://github.com/Rinne414/sd-image-sorter/blob/main/docs/API.md
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "images", "description": "Image retrieval, filtering, and file serving"},
        {"name": "tags", "description": "Tag management, AI tagging, and import/export"},
        {"name": "sorting", "description": "Scanning, moving, batch operations, and manual sorting"},
        {"name": "censor", "description": "NSFW detection and image censoring"},
        {"name": "prompts", "description": "Prompt generation and tag categorization"},
        {"name": "similarity", "description": "Image similarity search and duplicate detection"},
        {"name": "artists", "description": "Artist/style identification"},
        {"name": "models", "description": "Model inventory and first-run preparation"},
        {"name": "updates", "description": "Package-local application update checks and apply flow"},
    ]
)


configure_security_middleware(app)


# Serve frontend static files
frontend_path = str(BACKEND_DIR.parent / "frontend")
mount_frontend_static(app, frontend_path=frontend_path)


def _static_cache_bust_token(asset_path: str) -> str:
    """Compatibility shim for existing cache-bust tests."""
    return static_cache_bust_token(
        asset_path,
        frontend_path=frontend_path,
        app_version=APP_VERSION,
    )


# Include routers
app.include_router(images.router)
app.include_router(tags.router)
app.include_router(sorting.router)
app.include_router(censor.router)
app.include_router(prompts.router)
app.include_router(similarity.router)
app.include_router(artists.router)
app.include_router(models.router)
app.include_router(obfuscation.router)
app.include_router(aesthetic.router)
app.include_router(updates.router)
app.include_router(disk.router)
app.include_router(vlm.router)
app.include_router(colors.router)
app.include_router(tags_bulk.router)
app.include_router(dataset.router)
app.include_router(dataset_projects.router)
app.include_router(annotations.router)
app.include_router(smart_tag.router)
app.include_router(collections.router)
app.include_router(entry.router)
app.include_router(libraries.router)
app.include_router(duplicates.router)
app.include_router(masks.router)
app.include_router(publish.router)
app.include_router(metadata_repair.router)


# ============================================================
# Exception Handlers
# ============================================================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle request validation errors with user-friendly messages."""
    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error.get("loc", []))
        msg = error.get("msg", "Invalid value")
        errors.append({"field": field, "message": msg})

    logger.warning(
        "Validation error on %s %s: %s",
        request.method,
        request.url.path,
        errors
    )
    return JSONResponse(
        status_code=400,
        content={
            "error": "Invalid request parameters",
            "type": "ValidationError",
            "details": errors
        }
    )


@app.exception_handler(SDImageSorterError)
async def sd_image_sorter_exception_handler(request: Request, exc: SDImageSorterError) -> JSONResponse:
    """Handle custom SD Image Sorter exceptions with appropriate HTTP status codes."""
    # Map exception types to HTTP status codes
    status_code_map = {
        ImageNotFoundError: 404,
        ImageFileNotFoundError: 404,
        ValidationError: 400,
        PathSecurityError: 400,
        ConfigurationError: 500,
        TaggingError: 500,
        ScanError: 500,
        FileOperationError: 500,
        DatabaseError: 500,
        ModelLoadError: 500,
        OperationInProgressError: 409,  # Conflict
    }

    status_code = status_code_map.get(type(exc), 500)

    # Log with appropriate level based on status code
    log_level = logging.WARNING if status_code < 500 else logging.ERROR
    logger.log(
        log_level,
        "%s on %s %s: %s",
        exc.__class__.__name__,
        request.method,
        request.url.path,
        exc.message,
        exc_info=exc if status_code >= 500 else None
    )

    response_content = exc.to_dict()

    # For 500 errors, don't expose internal details to client
    if status_code >= 500:
        response_content = {"error": exc.message, "type": exc.__class__.__name__}

    return JSONResponse(status_code=status_code, content=response_content)


@app.exception_handler(AiRuntimeBusyError)
async def ai_runtime_busy_exception_handler(
    request: Request, exc: AiRuntimeBusyError
) -> JSONResponse:
    """A busy AI runtime is a 409, not a 500.

    Every heavy model route takes the same lease, so mapping it once here
    covers Censor detect, similarity search, aesthetic scoring, artist
    identification and ``POST /api/tag/single`` together, instead of repeating
    the same try/except in each router. The request was refused by policy —
    something else legitimately holds the runtime — which is a conflict, not a
    server fault and not a broken endpoint.

    ``reason`` is passed through because "someone is working" and "the lock is
    held but the job that claimed it is gone" need different answers: the first
    resolves itself, the second needs a restart.
    """
    logger.warning(
        "AI runtime busy on %s %s (%s): %s",
        request.method,
        request.url.path,
        getattr(exc, "reason", "busy"),
        exc,
    )
    return JSONResponse(
        status_code=409,
        content={
            "error": str(exc),
            "type": "AiRuntimeBusyError",
            "status_code": 409,
            "reason": getattr(exc, "reason", "busy"),
            "blocker": getattr(exc, "blocker", None),
            "waited_seconds": getattr(exc, "waited_seconds", None),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTPExceptions with consistent JSON format."""
    logger.warning(
        "HTTP %d on %s %s: %s",
        exc.status_code,
        request.method,
        request.url.path,
        exc.detail
    )
    if isinstance(exc.detail, dict):
        payload = dict(exc.detail)
        if "message" in payload and "error" not in payload:
            payload["error"] = payload["message"]
        payload.setdefault("type", "HTTPException")
        payload.setdefault("status_code", exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=payload)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if isinstance(exc.detail, str) else "Request failed", "type": "HTTPException", "status_code": exc.status_code}
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions and return a safe JSON error response.

    Stack traces are logged server-side but never sent to the client.
    """
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "type": "UnhandledException"}
    )


@app.get("/api/support/diagnostics")
async def support_diagnostics(lines: int = 200):
    """Return a bounded support diagnostics bundle for copy/paste debugging."""
    return build_support_diagnostics(max_lines=lines)


@app.post("/api/support/open-log")
async def support_open_log():
    """Open the rotating support log file in the OS file manager."""
    return open_support_log_file()


@app.get("/")
async def root():
    """
    Serve the main frontend page.

    Injects ``?v=APP_VERSION`` cache-busters onto every ``/static/*.js`` and
    ``/static/*.css`` reference in ``index.html``. This ensures that when a
    user upgrades the app (e.g. v3.2.0 -> v3.2.1) the browser refetches the
    JS/CSS bundles on a normal F5, instead of silently serving the old
    cached language packs and breaking new i18n keys until the user does a
    hard refresh (ctrl+shift+r). DB rows, scan progress, filters and
    selections live in localStorage / SQLite so this is purely a transport
    fix; no user data is touched.
    """
    return serve_frontend_index(frontend_path=frontend_path, app_version=APP_VERSION)


# ============== Run Server ==============

def _check_host_security(host: str) -> None:
    """Enforce localhost-only binding."""
    if not _is_loopback_host(host):
        raise ValueError("This application only allows localhost binding. Use 127.0.0.1 or localhost.")


def _configure_event_loop_policy() -> None:
    """Use the selector event loop on Windows to avoid noisy Proactor shutdown resets."""
    if sys.platform != "win32":
        return

    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return

    current_policy = asyncio.get_event_loop_policy()
    if isinstance(current_policy, selector_policy):
        return

    asyncio.set_event_loop_policy(selector_policy())


def _should_ignore_windows_shutdown_connection_reset(context: Dict[str, Any]) -> bool:
    """Ignore the known Proactor shutdown reset noise on Windows without hiding real app errors."""
    if sys.platform != "win32":
        return False

    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    if getattr(exc, "winerror", None) != 10054:
        return False

    message = str(context.get("message") or "")
    handle_repr = repr(context.get("handle")) if context.get("handle") else ""
    transport_marker = "_ProactorBasePipeTransport._call_connection_lost"
    return transport_marker in message or transport_marker in handle_repr


def _install_windows_loop_exception_handler() -> None:
    """Suppress the specific Proactor shutdown reset callback noise on Windows."""
    if sys.platform != "win32":
        return

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        if _should_ignore_windows_shutdown_connection_reset(context):
            logger.debug("Ignored Windows Proactor shutdown ConnectionResetError noise: %s", context.get("message"))
            return

        if previous_handler is not None:
            previous_handler(loop, context)
            return

        loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _maybe_open_browser_when_ready(host: str, port: int) -> None:
    """Open the app in the default browser once the server accepts connections.

    Only runs when a launcher opted in via ``SD_IMAGE_SORTER_OPEN_BROWSER=1``.
    Done in-process with Python's ``webbrowser`` instead of from the launcher's
    shell, so the portable package never spawns a hidden PowerShell process —
    some antivirus engines (e.g. Huorong / 火绒) flag a hidden-window PowerShell
    that makes HTTP requests in a loop as trojan-like behavior.
    """
    flag = os.environ.get("SD_IMAGE_SORTER_OPEN_BROWSER", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return

    # Wildcard binds aren't connectable targets; use loopback for the browser.
    browser_host = "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host
    url = f"http://{browser_host}:{port}"

    def _wait_and_open() -> None:
        import socket
        import webbrowser

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((browser_host, port), timeout=1.0):
                    break
            except OSError:
                time.sleep(0.4)
        else:
            # Never became ready — don't pop a browser onto a connection error.
            return
        try:
            webbrowser.open(url)
        except Exception:
            logger.debug("Could not open browser at %s", url)

    threading.Thread(target=_wait_and_open, name="open-browser", daemon=True).start()


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="SD Image Sorter Backend")
    parser.add_argument("--host", default=SERVER_HOST, help=f"Host to bind to (default: {SERVER_HOST})")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help=f"Port to bind to (default: {SERVER_PORT})")
    args = parser.parse_args()

    _check_host_security(args.host)
    _configure_event_loop_policy()
    os.environ["SD_IMAGE_SORTER_BIND_HOST"] = args.host
    os.environ["SD_IMAGE_SORTER_PORT"] = str(args.port)
    logger.info(
        "Starting server on %s:%s (access_log=%s, log_level=%s, log_file=%s)",
        args.host,
        args.port,
        "on" if LOG_ACCESS_ENABLED else "off",
        LOG_LEVEL.upper(),
        LOG_FILE_PATH if LOG_FILE_ENABLED else "off",
    )
    _maybe_open_browser_when_ready(args.host, args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=LOG_ACCESS_ENABLED,
        log_level=LOG_LEVEL.lower(),
    )
