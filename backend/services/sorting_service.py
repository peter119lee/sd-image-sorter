"""
Sorting service for SD Image Sorter.

Handles business logic for scanning, moving, batch operations, and manual sort sessions.
"""
import logging
import os
import json
import platform
import string
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from fastapi import HTTPException, BackgroundTasks
from pydantic import ValidationError

from app_info import APP_VERSION, GITHUB_REPOSITORY_URL
from config import MANUAL_SORT_SESSION_FILE, read_float_env
from constants import VALID_ASPECT_RATIOS
import database as db
import image_manager as image_manager_module
from image_manager import scan_folder, move_image, copy_image, parse_metadata_job
from database import add_images_batch
from metadata_parser import verify_image_readable
from services import entry_stats_service
from services.state_compat import MutableStateProxy
from services.sorting_models import (
    BatchMoveRequest,
    BrowseFolderRequest,
    FOLDER_KEY_MAX_LENGTH,
    FolderConfig,
    ManualSortStartRequest,
    MoveRequest,
    ScanRequest,
    SORT_MODE_BRACKET,
    SORT_MODE_CULL,
    SORT_MODE_DEFAULT,
    SORT_MODE_SLOT,
    VALID_BRACKET_ACTIONS,
    VALID_CULL_ACTIONS,
    VALID_FILE_OPERATIONS,
    VALID_PROMPT_MATCH_MODES,
    VALID_SORT_ACTIONS,
    VALID_SORT_MODES,
    ValidatePathRequest,
)
from services.sorting_session_store import (
    SORT_SESSION_SCHEMA_VERSION,
    build_persisted_sort_session_payload,
    discard_persisted_session_files,
    find_existing_session_file,
    get_session_file_candidates,
    parse_persisted_session_version,
    read_persisted_session,
    remove_session_files,
    write_persisted_session,
)
from utils.path_validation import normalize_user_path, validate_folder_path
from utils.source_paths import resolve_existing_indexed_image_path

# ---------------------------------------------------------------------------
# Decomposition (2026-07): the SortingService method bodies live in the
# services/sorting/ package as mixins, assembled below. THIS module remains a
# real FILE and the single monkeypatch surface (tests/test_sorting_service_pins.py):
#   * UNSAFE re-imported seams (verify_image_readable, scan_folder,
#     move_image, copy_image, parse_metadata_job, add_images_batch) and
#     module-scalar constants (SCAN_LOG_HEARTBEAT_SECONDS,
#     SCAN_UI_STALLED_SECONDS, SESSION_FILE, LEGACY_SESSION_FILE,
#     BATCH_MOVE_FETCH_CHUNK, STATS_FACET_LIMIT, ANALYTICS_DEFAULT_LIMIT)
#     stay defined here; mixin code resolves them through this module at
#     call time, so patching services.sorting_service.<name> keeps working.
#   * The library-health TTL cache is module-global here; tests import
#     _LIBRARY_HEALTH_CACHE and invalidate_library_health_cache directly.
#   * tests/test_architecture_contract.py reads THIS file's source text: it
#     must stay a file (not a package __init__) and keep the
#     `from services.sorting_session_store import` delegation.
#   * _resolve_image_path stays here because it passes backend_file=__file__
#     and utils/source_paths derives backend_root from dirname(dirname(it));
#     a mixin's deeper __file__ would silently break indexed-path healing.
# Imports above are intentionally kept verbatim even where the facade body no
# longer calls them (seam + re-export surface) — F401 is ignored for this
# file in pyproject.toml, same as the database.py facade.
from services.sorting.batch_move import BatchMoveMixin
from services.sorting.library import LibraryMixin
from services.sorting.move import MoveMixin
from services.sorting.scan import ScanMixin
from services.sorting.session import SortSessionMixin
from services.sorting.session_state import SessionStateMixin
from services.sorting.state import SortingStateMixin
from services.sorting.workbench import WorkbenchMixin

logger = logging.getLogger(__name__)

__all__ = [
    "BatchMoveRequest",
    "BrowseFolderRequest",
    "FolderConfig",
    "ManualSortStartRequest",
    "MoveRequest",
    "ScanRequest",
    "SORT_MODE_BRACKET",
    "SORT_MODE_CULL",
    "SORT_MODE_DEFAULT",
    "SORT_MODE_SLOT",
    "SORT_SESSION_SCHEMA_VERSION",
    "SortingService",
    "ValidatePathRequest",
    "invalidate_library_health_cache",
]


SESSION_FILE = MANUAL_SORT_SESSION_FILE
LEGACY_SESSION_FILE = os.path.join(os.path.dirname(__file__), '..', 'sort_session.json')

BATCH_MOVE_FETCH_CHUNK = 500
STATS_FACET_LIMIT = 50
ANALYTICS_DEFAULT_LIMIT = 500
SCAN_LOG_HEARTBEAT_SECONDS = max(
    0.0,
    read_float_env("SD_IMAGE_SORTER_SCAN_LOG_HEARTBEAT_SECONDS", 15.0),
)
SCAN_UI_STALLED_SECONDS = max(
    5.0,
    read_float_env(
        "SD_IMAGE_SORTER_SCAN_UI_STALLED_SECONDS",
        max(45.0, SCAN_LOG_HEARTBEAT_SECONDS * 3),
    ),
)


# v3.2.2: TTL cache for /api/library-health.
#
# The underlying SQL aggregates across the whole ``images`` table (~10
# SUM/COUNT operations + duplicate-filename grouping + folder grouping
# + largest-images sort + issue samples). On a 71k-row library the
# cold-cache call takes ~12 seconds. Without caching, the home page,
# gallery, and diagnostics panel all hit the same endpoint and cause
# concurrent reads to time out.
#
# 60s freshness is the right granularity for a "library health" report
# — none of the inputs (image count, embedding completeness, missing
# metadata) change within seconds. Tagging or scanning a batch will
# refresh once the TTL expires; the user can also force a refresh by
# reloading after they trigger a long operation.
#
# Cache keyed by sample_limit so callers asking for different sample
# sizes get the right payload.
_LIBRARY_HEALTH_CACHE_TTL_SECONDS = 60.0
_LIBRARY_HEALTH_CACHE_LOCK = threading.Lock()
_LIBRARY_HEALTH_CACHE: Dict[int, Tuple[float, Dict[str, Any]]] = {}


def invalidate_library_health_cache() -> None:
    """Force the next /api/library-health call to recompute.

    INVARIANT: any operation that mutates the row count or `is_readable` state
    of the `images` table MUST call this, or the cached report goes stale for up
    to the 60s TTL. That report feeds the gallery "N images can't open" banner
    and the diagnostics panel, so a stale cache shows counts that no longer match
    the library. Current callers:
      * clear_gallery()                         (DELETE FROM images)
      * ImageService._remove_selected_image_id_chunk  (bulk gallery removal)
      * ImageService.run_reconnect              (relink flips unreadable->readable)
    Lower-impact writers (move/scan/tag, mark_image_unreadable during background
    scoring) currently rely on the 60s TTL self-healing; wire them here too if a
    stale count after those flows is ever reported.
    """
    with _LIBRARY_HEALTH_CACHE_LOCK:
        _LIBRARY_HEALTH_CACHE.clear()


def _get_library_health_cached(sample_limit: int) -> Dict[str, Any]:
    sample_limit = max(1, min(int(sample_limit), 25))
    now = time.time()
    with _LIBRARY_HEALTH_CACHE_LOCK:
        cached = _LIBRARY_HEALTH_CACHE.get(sample_limit)
        if cached is not None and (now - cached[0]) < _LIBRARY_HEALTH_CACHE_TTL_SECONDS:
            return cached[1]

    # Compute outside the lock: SQL is slow, holding the lock would
    # serialize concurrent callers. Multiple parallel cache misses are
    # acceptable; whichever finishes last wins the cache slot.
    payload = db.get_library_health_report(sample_limit=sample_limit)
    with _LIBRARY_HEALTH_CACHE_LOCK:
        _LIBRARY_HEALTH_CACHE[sample_limit] = (time.time(), payload)
    return payload


class SortingService(
    SortingStateMixin,
    ScanMixin,
    MoveMixin,
    BatchMoveMixin,
    SessionStateMixin,
    SortSessionMixin,
    WorkbenchMixin,
    LibraryMixin,
):
    """Service for scanning, moving, and manual sorting operations."""

    def __init__(self):
        """Initialize the sorting service."""
        self._scan_progress: Dict[str, Any] = self._build_default_scan_progress_state()
        self._scan_lock = threading.Lock()
        self._scan_cancel_event: Optional[threading.Event] = None
        self._scan_worker_thread: Optional[threading.Thread] = None
        self._scan_run_id = 0

        self._sort_session: Dict[str, Any] = self._build_default_sort_session_state()
        self._sort_session_lock = threading.Lock()
        self._scan_progress_proxy = MutableStateProxy(self.get_scan_progress, self.set_scan_progress)
        self._sort_session_proxy = MutableStateProxy(self.get_sort_session, self.set_sort_session)
        
        # Batch move progress
        self._batch_move_progress: Dict[str, Any] = self._build_default_batch_move_progress_state()
        self._batch_move_lock = threading.Lock()
        self._batch_move_run_id = 0
        # Cooperative cancellation for the active batch-move worker.
        # Mirrors ``self._scan_cancel_event`` / ``cancel_scan``: the worker
        # checks ``is_set()`` between chunks and between images so a cancel
        # request lands within a few image iterations rather than waiting
        # for the entire batch to finish.
        self._batch_move_cancel_event: Optional[threading.Event] = None

        # v3.3.0 USR-1: gallery selection move/copy progress.
        # The synchronous ``/api/move`` endpoint stays for tests and
        # programmatic callers, but the gallery UI now drives a background
        # job so large selections stream progress (and the user can see how
        # far the per-file ``shutil.move`` source-deletion has advanced)
        # instead of staring at a silent blocking request. Mirrors the
        # batch-move run-id epoch + cancel-event pattern exactly. The final
        # progress payload embeds the per-id ``results`` list so the frontend
        # success/failure mapping is identical to the sync endpoint.
        self._move_progress: Dict[str, Any] = self._build_default_move_progress_state()
        self._move_lock = threading.Lock()
        self._move_run_id = 0
        self._move_cancel_event: Optional[threading.Event] = None

    @staticmethod
    def _resolve_image_path(path: str) -> Optional[str]:
        """Resolve a library image path across native Windows and WSL mounts."""
        return resolve_existing_indexed_image_path(path, backend_file=__file__)

    def _build_persisted_sort_session_payload(self) -> Dict[str, Any]:
        """Return the on-disk manual-sort session payload."""
        session = self._coerce_sort_session_state(self._sort_session)
        return build_persisted_sort_session_payload(session)

    @staticmethod
    def _parse_persisted_session_version(data: Dict[str, Any]) -> int:
        """Read the persisted schema version, treating missing versions as legacy v0."""
        return parse_persisted_session_version(data)

    def _discard_persisted_session_file(self, reason: str, *, paths: Optional[List[Path]] = None) -> None:
        """Delete unusable persisted session files so future boots do not half-restore them."""
        discard_persisted_session_files(reason, paths or self._get_session_file_candidates())

    @staticmethod
    def _get_session_file_candidates() -> List[Path]:
        """Return persisted-session paths in preferred load/save order."""
        return get_session_file_candidates(SESSION_FILE, LEGACY_SESSION_FILE)

    def _find_existing_session_file(self) -> Optional[Path]:
        """Find the first existing persisted sort-session file."""
        return find_existing_session_file(self._get_session_file_candidates())

    def clear_gallery(self) -> Dict[str, str]:
        """Clear image records for the **current** long-lived library only.

        Other libraries are untouched. Tags cascade via ON DELETE CASCADE.
        """
        try:
            from db_libraries import clear_library_images, resolve_active_library_id

            lid = resolve_active_library_id()
            removed = clear_library_images(lid)
        except Exception:
            # Pre-migration / test DBs without libraries table: legacy full clear.
            with db.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM images")
                removed = int(cursor.rowcount or 0)
            lid = "main"
        # The library-health report (which drives the "N images can't open"
        # gallery banner) is cached for 60s and counts unreadable rows straight
        # from `images`. Without this invalidation the banner keeps showing the
        # pre-clear count until the TTL lapses — the report is now empty, so make
        # the next /api/library-health call recompute it to zero immediately.
        invalidate_library_health_cache()
        return {
            "status": "ok",
            "message": "Library cleared",
            "library_id": str(lid),
            "removed": str(int(removed)),
        }

    def get_library_health(self, sample_limit: int = 8) -> Dict[str, Any]:
        """Get a read-only library quality and archive-readiness report.

        v3.2.2: cached with a 60s TTL because the underlying SQL does
        ~10 SUM/COUNT aggregations and a duplicate-filename grouping
        across the whole ``images`` table. On a 71k-row library the
        first call takes ~12s of cold cache time. Without this cache,
        50 concurrent clients (the gallery view, the home page, the
        diagnostics panel, etc.) cause read timeouts because each
        request re-runs the same expensive scan.

        Cache keyed by ``sample_limit`` because that controls the
        number of sample rows returned in each section, and a request
        for sample_limit=25 should not be served a cached payload
        built with sample_limit=8.
        """
        return _get_library_health_cached(int(sample_limit))

    def _save_session_to_disk(self) -> None:
        """Persist session to disk."""
        try:
            data = self._build_persisted_sort_session_payload()
            session_file = self._get_session_file_candidates()[0]
            write_persisted_session(session_file, data)
        except Exception as e:
            logger.warning("Failed to save session to disk: %s", e)
