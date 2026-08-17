"""
Image endpoints for SD Image Sorter.
Handles image retrieval, filtering, and file serving.

Refactored to use Service Layer pattern with dependency injection.
"""
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Any, List, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path as FastAPIPath, Query, UploadFile, File
from pydantic import BaseModel, Field, model_validator

from config import get_temp_dir
from services import entry_stats_service
from services.bulk_job_service import get_bulk_job_service
from services.image_service import ImageService
from services.service_provider import ServiceProvider
from utils.path_validation import PathValidationError


logger = logging.getLogger(__name__)
PROMPT_MATCH_MODE_EXACT = "exact"
PROMPT_MATCH_MODE_CONTAINS = "contains"
VALID_PROMPT_MATCH_MODES = {PROMPT_MATCH_MODE_EXACT, PROMPT_MATCH_MODE_CONTAINS}


router = APIRouter(prefix="/api", tags=["images"])

# Service instance - will be set via dependency injection
_image_service_provider = ServiceProvider(ImageService)
READER_UPLOAD_TEMP_DIR = Path(get_temp_dir()) / "reader_uploads"
READER_UPLOAD_TTL_SECONDS = 24 * 60 * 60
PARSE_IMAGE_UPLOAD_MAX_BYTES = 64 * 1024 * 1024
PARSE_IMAGE_UPLOAD_CHUNK_SIZE = 1024 * 1024


get_image_service = _image_service_provider.get
set_image_service = _image_service_provider.set


# ---------------------------------------------------------------------------
# Decomposition (2026-07): the 40 endpoint registrations and the request/
# response models live in the routers/images_parts/ package, all registering
# on THIS module's single shared ``router`` (tests/test_images_router_pins.py). THIS module remains a real FILE and
# the single import/monkeypatch surface:
#   * ``router`` / ``_image_service_provider`` / ``get_image_service`` /
#     ``set_image_service`` stay DEFINED here — main.py mounts images.router,
#     and main.py + tests/conftest.py bind the service instance via
#     ``images.set_image_service(...)``.
#   * The four upload constants above stay DEFINED here; the parse-image
#     endpoint (images_parts/serving.py) reads them through this module at
#     call time so ``monkeypatch.setattr(images_router,
#     "PARSE_IMAGE_UPLOAD_MAX_BYTES", ...)`` keeps biting.
#   * ``sys`` / ``subprocess`` stay imported here — the open-folder test
#     patches the shared module singletons through this namespace
#     (``images_router.sys.platform`` / ``images_router.subprocess.Popen``).
#   * The request/response models are re-exported below so every historical
#     ``routers.images.<Model>`` import keeps resolving (the pins suite +
#     tests/test_reconnect_missing_files.py import them here).
#   * tests/test_router_service_boundaries.py reads THIS file's source text:
#     it must stay a file and must never import database / metadata_parser
#     (parse + DB logic stays behind ImageService).
#   * ROUTE REGISTRATION ORDER == the import order of the endpoint-group
#     submodules below == the pre-split declaration order. FastAPI matches
#     in registration order: the single-segment static GET routes
#     (selection-chunk, repair-candidates, count) MUST register before
#     GET /api/images/{image_id} or they 422-shadow (pinned structurally and
#     behaviorally). Do NOT reorder these imports.
# Imports above are intentionally kept verbatim even where the facade body no
# longer calls them (seam + re-export surface) — F401 is ignored for this
# file in pyproject.toml, same as services/image_service.py and
# services/censor_service.py.
from routers.images_parts.models import (
    BulkJobEnvelopeResponse,
    DeleteSelectedImagesRequest,
    DeleteSelectedImagesResponse,
    ExportSelectionImage,
    ExportSelectionRequest,
    ExportSelectionResponse,
    FilteredImageCountResponse,
    OpenFolderRequest,
    ReconnectMissingFilesRequest,
    RemoveSelectedImagesRequest,
    RemoveSelectedImagesResponse,
    RepairConfirmRequest,
    SaveEditedMetadataRequest,
    SaveEditedMetadataResponse,
    SelectionChunkResponse,
    SelectionIdsRequest,
    SelectionIdsResponse,
    SelectionTokenRequest,
    SelectionTokenResponse,
)

# Endpoint groups — imported IN REGISTRATION ORDER (see comment above).
from routers.images_parts import listing    # (1) GET /images · /folders · /library-roots
from routers.images_parts import selection  # (2) selection-token · selection-chunk
from routers.images_parts import repair     # (3) reconnect-missing/* · repair-candidates · repair-confirm
from routers.images_parts import counting   # (4) GET /images/count (static, pre-{image_id})
from routers.images_parts import detail     # (5) GET /images/{image_id} · PATCH caption
from routers.images_parts import export     # (6) export-data · selection-ids · POST count
from routers.images_parts import jobs       # (7) delete-selected/* · remove-selected/* · bulk-jobs/*
from routers.images_parts import item_ops   # (8) reparse · rating
from routers.images_parts import serving    # (9) save-edited · file/thumbnail serving · caches · open-folder · parse-image

# Models declared inline next to their endpoints (kept verbatim in their
# groups); re-exported so ``routers.images.<name>`` keeps resolving.
ImageCaptionPatchRequest = detail.ImageCaptionPatchRequest
SetUserRatingRequest = item_ops.SetUserRatingRequest
