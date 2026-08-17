"""
Image service for SD Image Sorter.

Handles business logic for image retrieval, filtering, and file operations.
"""
import logging
import base64
import binascii
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional, Dict, Any, List, Callable

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

import database as db
from constants import VALID_ASPECT_RATIOS
from image_manager import reparse_image_metadata
from metadata_parser import parse_image, verify_image_readable
from services.indexed_file_mutation_service import save_and_reconcile_checked
from services.image_metadata_writer import (
    JPEG_LIMITATION_WARNING,
    WEBP_LIMITATION_WARNING,
    build_exif_bytes,
    build_pnginfo,
    build_sd_parameters_text,
    normalize_edited_metadata,
    prepare_image_for_save,
)
from services.bulk_job_service import (
    JOB_KIND_DELETE_FILES,
    JOB_KIND_REMOVE_FROM_GALLERY,
    get_bulk_job_service,
)
from services.tag_export_service import extract_generation_params
from thumbnail_cache import (
    get_thumbnail_async,
    generate_placeholder_thumbnail,
    clear_cache as clear_thumbnail_cache,
    cleanup_old_cache,
    enforce_cache_size_limit,
    get_cache_stats,
    SUPPORTED_SIZES,
)
from utils.path_validation import (
    ALLOWED_IMAGE_EXTENSIONS,
    PathValidationError,
    validate_file_path,
    normalize_user_path,
    validate_folder_path,
    validate_image_output_path,
)
from utils.pagination_cursor import (
    decode_image_cursor,
    encode_image_cursor_from_image,
)
from utils.source_paths import resolve_existing_indexed_image_path

# ---------------------------------------------------------------------------
# Decomposition (2026-07): the ImageService method bodies live in the
# services/image/ package as mixins, assembled below. THIS module remains a
# real FILE and the single monkeypatch surface (tests/test_image_service_pins.py):
#   * move_file_to_trash stays DEFINED here (module-global UNSAFE seam,
#     patched six times by tests/test_routers/test_images.py); the
#     jobs_delete mixin calls it through this module at call time so those
#     patches keep landing.
#   * resolve_existing_indexed_image_path stays imported here (the pin suite
#     patches it as a facade attribute); the gallery/serving mixins resolve
#     it through this module at call time.
#   * Module constants live in services/image/_constants.py and are
#     re-imported here so image_service.SELECTION_IDS_MAX_RESPONSE /
#     SELECTION_TOKEN_VERSION / VALID_SORT_OPTIONS etc. remain facade
#     attributes; mixin BODY reads resolve through this module at call time,
#     and only def-time signature defaults bind the leaf values directly
#     (identical freeze-at-def semantics).
#   * The selection coercers/sanitizers live in services/image/_filters.py
#     and are re-imported here (the pin suite calls image_service._coerce_*
#     and image_service._sanitize_filter_values directly).
#   * tests/test_architecture_contract.py and
#     tests/test_indexed_file_mutation_contract.py read THIS file's source
#     text: it must stay a file (not a package __init__), must not define
#     _normalize_edited_metadata / _build_sd_parameters_text, and must not
#     own overwrite-preflight logic.
#   * _BACKEND_FILE below is passed as backend_file= by the three indexed-path
#     healing methods; utils/source_paths derives backend_root from
#     dirname(dirname(abspath(backend_file))), so it must stay THIS file's
#     __file__ — a mixin's one-level-deeper __file__ would silently break
#     relative/legacy-row resolution (pinned by
#     test_backend_file_argument_resolves_to_the_backend_root).
# Imports above are intentionally kept verbatim even where the facade body no
# longer calls them (seam + re-export surface) — F401 is ignored for this
# file in pyproject.toml, same as database.py and sorting_service.py.
from services.image._constants import (
    DEFAULT_PAGE_SIZE,
    DELETE_FETCH_CHUNK,
    DIMENSION_MAX,
    DIMENSION_MIN,
    LIMIT_MAX,
    OFFSET_MAX,
    PROMPT_MATCH_MODE_CONTAINS,
    PROMPT_MATCH_MODE_EXACT,
    RECONNECT_MTIME_TOLERANCE_NS,
    RECONNECT_PROGRESS_EVERY_N_FILES,
    RECONNECT_PROGRESS_MIN_INTERVAL_SECONDS,
    RECONNECT_REVIEW_MAX_PENDING_PER_RUN,
    RECONNECT_REVIEW_RESOLVED_HISTORY_KEEP,
    SEARCH_MAX_LENGTH,
    SELECTION_IDS_FETCH_CHUNK,
    SELECTION_IDS_MAX_RESPONSE,
    SELECTION_TOKEN_DEFAULT_CHUNK,
    SELECTION_TOKEN_MAX_CHUNK,
    SELECTION_TOKEN_MAX_EXCLUDED_IDS,
    SELECTION_TOKEN_RANDOM_SORT_ERROR,
    SELECTION_TOKEN_VERSION,
    VALID_BRIGHTNESS_DISTRIBUTIONS,
    VALID_COLOR_TEMPERATURES,
    VALID_PROMPT_MATCH_MODES,
    VALID_SORT_OPTIONS,
)
from services.image._filters import (
    _DATE_FILTER_RE,
    _coerce_optional_bool_filter,
    _coerce_optional_date_filter,
    _coerce_optional_float_filter,
    _coerce_optional_int_filter,
    _coerce_optional_string_filter,
    _coerce_prompt_match_mode,
    _coerce_selection_id_list,
    _coerce_tag_mode,
    _invalid_selection_token,
    _sanitize_filter_list,
    _sanitize_filter_value,
    _sanitize_filter_values,
)
from services.image.gallery import GalleryMixin
from services.image.jobs_delete import DeleteJobsMixin
from services.image.jobs_remove import RemoveJobsMixin
from services.image.reconnect import ReconnectMixin
from services.image.repair import RepairReviewMixin
from services.image.selection import SelectionMixin
from services.image.serving import (
    ServingMixin,
    _allocate_reader_upload_path,
    _cleanup_stale_reader_uploads,
)


logger = logging.getLogger(__name__)


# The backend-root anchor passed as backend_file= by the indexed-path healing
# methods (gallery._filter_and_mark_missing_images,
# serving.resolve_image_source_path, serving.save_image_with_edited_metadata).
# utils/source_paths computes backend_root = dirname(dirname(abspath(it))), so
# this must stay THIS file's __file__ (backend/services/image_service.py ->
# backend/). Pinned by test_backend_file_argument_resolves_to_the_backend_root.
_BACKEND_FILE = __file__


def move_file_to_trash(path: str) -> None:
    """Move a file to the OS trash/recycle bin without falling back to permanent delete.

    On Windows, ``send2trash`` uses ``IFileOperation`` which normally moves the
    file to the per-volume Recycle Bin (e.g. ``I:\\$RECYCLE.BIN``). When the
    target volume has Recycle Bin disabled, is over quota, or is a network /
    removable drive without trash support, the call may silently succeed
    without actually moving the file (older Windows versions) or move it
    cross-volume in a way that surprises the user. We verify the source is
    gone after the call and raise a clear error otherwise so the caller can
    surface "Trash unavailable on this drive" instead of pretending success.
    """
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise RuntimeError(
            "Trash support is not installed. Reinstall dependencies and try again."
        ) from exc

    if not path:
        raise RuntimeError("Cannot move to trash: empty path")

    target = Path(path)
    if not target.exists():
        raise RuntimeError(f"Cannot move to trash: file does not exist at {path}")
    if target.is_dir():
        # Defensive: the indexed path should always point to a file. If a
        # directory ever sneaks in (data corruption, manual edit) we refuse
        # to trash it because that would surprise the user with a folder
        # appearing in the Recycle Bin alongside their images.
        raise RuntimeError(
            f"Refusing to move directory to trash (expected a file): {path}"
        )

    logger.info("Moving file to trash: %s", path)
    send2trash(path)

    if target.exists():
        # send2trash returned without raising but the file is still there.
        # This is the symptom users have reported on drives where Windows
        # silently disables Recycle Bin support. Surface it as a real error
        # so the caller adds the image to the failed[] array instead of
        # claiming success.
        raise RuntimeError(
            f"send2trash reported success but the file still exists on disk: {path}. "
            "The drive may have Recycle Bin disabled or be a network/removable volume "
            "without trash support."
        )


class ImageService(
    ReconnectMixin,
    RepairReviewMixin,
    GalleryMixin,
    DeleteJobsMixin,
    RemoveJobsMixin,
    SelectionMixin,
    ServingMixin,
):
    """Service for image retrieval, filtering, and file operations."""

    def __init__(self):
        self._reconnect_progress: Dict[str, Any] = self._build_default_reconnect_progress_state()
        self._reconnect_lock = threading.Lock()
        self._reconnect_cancel_event: Optional[threading.Event] = None
        self._reconnect_run_id = 0
        # v3.3.2 Phase-1: gallery "delete selected" runs as a background job
        # (cloned from SortingService.start_move_job) so large selections stream
        # progress instead of freezing the browser on one blocking POST. Run-id
        # epoch + cooperative cancel-event guard exactly as the move job does.
        self._delete_progress: Dict[str, Any] = self._build_default_delete_progress_state()
        self._delete_lock = threading.Lock()
        self._delete_cancel_event: Optional[threading.Event] = None
        self._delete_run_id = 0
        # v3.3.2 Phase-1: background "remove from gallery" job (DB-only, no file
        # ops), cloned from the delete job above.
        self._remove_progress: Dict[str, Any] = self._build_default_remove_progress_state()
        self._remove_lock = threading.Lock()
        self._remove_cancel_event: Optional[threading.Event] = None
        self._remove_run_id = 0
