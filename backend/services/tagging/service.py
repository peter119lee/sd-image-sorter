"""TaggingService assembly: mixin composition, shared state, tagger getter.

The method bodies live in the sibling mixin modules (moved verbatim from
services/tagging_service.py, decomposition 2026-07); this module owns the
class statement, __init__ state, and the tagger-getter setter.
"""

import threading
from typing import Any, Callable, Dict, Optional

from services.state_compat import MutableStateProxy
from services.tagging.catalog import CatalogMixin
from services.tagging.exports import ExportsMixin
from services.tagging.jobs import JobsMixin
from services.tagging.library_io import LibraryIOMixin
from services.tagging.progress import ProgressMixin, _build_tag_progress_state
from services.tagging.runtime_plan import RuntimePlanMixin
from services.tagging.validation import ValidationMixin


class TaggingService(
    CatalogMixin,
    ValidationMixin,
    RuntimePlanMixin,
    ProgressMixin,
    JobsMixin,
    ExportsMixin,
    LibraryIOMixin,
):
    """Service for AI tagging and tag management."""

    def __init__(self):
        """Initialize the tagging service."""
        self._progress: Dict[str, Any] = _build_tag_progress_state("idle")
        self._lock = threading.Lock()
        self._progress_proxy = MutableStateProxy(self.get_progress, self.set_progress)
        self._get_tagger: Optional[Callable] = None
        self._cancel_requested = False
        self._worker_process: Optional[Any] = None
        self._worker_cancel_event: Optional[Any] = None
        self._active_run_id = 0
        self._pending_run_id: Optional[int] = None
        # v3.3.2 Phase-1: background batch tag-export job. The underlying
        # export_tags_batch is monolithic, so this runs it off the request thread
        # to avoid freezing the browser; no mid-run cancel. The terminal payload
        # embeds the full export result.
        self._export_progress: Dict[str, Any] = (
            self._build_default_export_progress_state()
        )
        self._export_lock = threading.Lock()
        self._export_run_id = 0
        # Which run a worker has entered, and whether it is still inside. This
        # is what lets the reset tell an abandoned run from a slow one; a
        # FastAPI background task runs on a pooled anyio thread that outlives
        # it, so thread liveness cannot answer that question.
        self._export_worker_run_id: Optional[int] = None
        self._export_worker_running = False

    def set_tagger_getter(self, tagger_getter: Callable) -> None:
        """Set the tagger getter function from main module."""
        self._get_tagger = tagger_getter
