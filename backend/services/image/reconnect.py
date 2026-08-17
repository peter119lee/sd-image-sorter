"""Missing-file reconnect: match helpers, the one-shot core matcher, and the
background start/cancel state machine.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: facade-owned RECONNECT_*
constants resolve through _svc() at call time, and one static call was
renamed ImageService -> ReconnectMixin. The lazy in-method imports
(image_fingerprint, sorting_service.invalidate_library_health_cache) are
original code, kept verbatim.
"""

import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

import database as db
from utils.path_validation import ALLOWED_IMAGE_EXTENSIONS, normalize_user_path, validate_folder_path

# NOTE(decomposition): keep the historical logger channel so log routing and
# output stay byte-identical after the package split.
logger = logging.getLogger("services.image_service")


def _svc():
    """Resolve facade-owned seams/constants through services.image_service at call time.

    Tests patch module attributes on the facade; a ``from`` import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.image_service as image_service

    return image_service


class ReconnectMixin:
    """Missing-file reconnect slice of ImageService (assembled in services/image_service.py)."""

    @staticmethod
    def _build_default_reconnect_progress_state() -> Dict[str, Any]:
        """Return the canonical idle reconnect-progress payload."""
        return {
            "status": "idle",
            "step": "idle",
            "current": 0,
            "processed": 0,
            "total": 0,
            "total_final": False,
            "checked_files": 0,
            "missing_total": 0,
            "library_missing_total": 0,
            "matched": 0,
            "ambiguous": 0,
            "review_pending_total": 0,
            "conflicts": 0,
            "skipped": 0,
            "errors": 0,
            "message": "",
            "current_item": None,
            "started_at": None,
            "updated_at": None,
        }

    @staticmethod
    def _coerce_reconnect_progress_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        coerced = ReconnectMixin._build_default_reconnect_progress_state()
        if state:
            coerced.update(state)
        return coerced

    def get_reconnect_progress(self) -> Dict[str, Any]:
        """Return current missing-file reconnect progress."""
        with self._reconnect_lock:
            return self._reconnect_progress.copy()

    def _set_reconnect_progress_if_current(self, run_id: int, state: Dict[str, Any]) -> bool:
        with self._reconnect_lock:
            if run_id != self._reconnect_run_id:
                return False
            self._reconnect_progress = self._coerce_reconnect_progress_state(state)
            return True

    def _update_reconnect_progress_if_current(self, run_id: int, **updates: Any) -> bool:
        with self._reconnect_lock:
            if run_id != self._reconnect_run_id:
                return False
            current = self._coerce_reconnect_progress_state(self._reconnect_progress)
            current.update(updates)
            self._reconnect_progress = current
            return True

    @staticmethod
    def _parse_datetime_to_ns(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return int(value.timestamp() * 1_000_000_000)
        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return int(parsed.timestamp() * 1_000_000_000)

    @staticmethod
    def _candidate_expected_size(candidate: Dict[str, Any]) -> Optional[int]:
        for key in ("source_size", "file_size"):
            value = candidate.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _candidate_expected_mtime_ns(cls, candidate: Dict[str, Any]) -> Optional[int]:
        value = candidate.get("source_mtime_ns")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        return cls._parse_datetime_to_ns(candidate.get("source_file_mtime"))

    @staticmethod
    def _mtime_matches(expected_ns: Optional[int], stat_result: os.stat_result) -> bool:
        if expected_ns is None:
            return False
        return abs(int(expected_ns) - int(stat_result.st_mtime_ns)) <= _svc().RECONNECT_MTIME_TOLERANCE_NS

    @staticmethod
    def _normalized_fingerprint(value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        return text or None

    def _find_reconnect_match(
        self,
        found_path: str,
        stat_result: os.stat_result,
        candidates: List[Dict[str, Any]],
        *,
        verify_uncertain: bool,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """Find a safe row match for one discovered file."""
        stat_matches: List[Dict[str, Any]] = []
        fingerprint_candidates: List[Dict[str, Any]] = []
        name_size_only: List[Dict[str, Any]] = []

        for candidate in candidates:
            expected_size = self._candidate_expected_size(candidate)
            if expected_size is not None and expected_size != int(stat_result.st_size):
                continue

            expected_mtime_ns = self._candidate_expected_mtime_ns(candidate)
            if self._mtime_matches(expected_mtime_ns, stat_result):
                stat_matches.append(candidate)
                continue

            if verify_uncertain and self._normalized_fingerprint(candidate.get("content_fingerprint")):
                fingerprint_candidates.append(candidate)
                continue

            if expected_mtime_ns is None and not self._normalized_fingerprint(candidate.get("content_fingerprint")):
                name_size_only.append(candidate)

        if len(stat_matches) == 1:
            return stat_matches[0], "stat"
        if len(stat_matches) > 1:
            return None, "ambiguous"

        if fingerprint_candidates:
            try:
                from image_fingerprint import compute_image_content_fingerprint

                found_fingerprint = self._normalized_fingerprint(compute_image_content_fingerprint(found_path))
            except Exception as exc:
                logger.debug("Could not fingerprint reconnect candidate %s: %s", found_path, exc)
                found_fingerprint = None
            if found_fingerprint:
                verified = [
                    candidate for candidate in fingerprint_candidates
                    if self._normalized_fingerprint(candidate.get("content_fingerprint")) == found_fingerprint
                ]
                if len(verified) == 1:
                    return verified[0], "fingerprint"
                if len(verified) > 1:
                    return None, "ambiguous"

        if len(name_size_only) == 1:
            return name_size_only[0], "name_size"
        if len(name_size_only) > 1:
            return None, "ambiguous"

        return None, "none"

    @staticmethod
    def _iter_reconnect_image_files(search_folder: str, recursive: bool, stop_requested: Optional[Callable[[], bool]] = None):
        pending_dirs = [os.path.abspath(search_folder)]
        while pending_dirs:
            if callable(stop_requested) and stop_requested():
                raise InterruptedError("Reconnect cancelled")
            current_dir = pending_dirs.pop()
            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        if callable(stop_requested) and stop_requested():
                            raise InterruptedError("Reconnect cancelled")
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if recursive:
                                    pending_dirs.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            if Path(entry.name).suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
                                continue
                            yield entry.path, entry.name
                        except FileNotFoundError:
                            continue
            except PermissionError as exc:
                logger.warning("Permission denied while reconnecting missing files in %s: %s", current_dir, exc)
                continue

    def reconnect_missing_files_once(
        self,
        search_folder: str,
        *,
        recursive: bool = True,
        verify_uncertain: bool = True,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        stop_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Search one folder for files that can reconnect missing library rows."""
        normalized_folder = normalize_user_path(search_folder)
        run_started_at = time.time()
        # Roadmap-C: each run snapshots a fresh set of pending repair reviews.
        # Clear the previous run's still-pending rows (they belonged to a stale
        # search) and prune resolved history so the table stays bounded. This
        # only touches the reconnect_reviews table, never image rows.
        try:
            db.delete_pending_reconnect_reviews()
            db.prune_resolved_reconnect_reviews(_svc().RECONNECT_REVIEW_RESOLVED_HISTORY_KEEP)
        except Exception as exc:
            logger.warning("Could not reset reconnect review snapshot: %s", exc)
        missing_candidates = db.get_missing_image_reconnect_candidates()
        candidates_by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for candidate in missing_candidates:
            filename = str(candidate.get("filename") or Path(str(candidate.get("path") or "")).name)
            if filename:
                candidates_by_name[filename].append(candidate)

        result: Dict[str, Any] = {
            "checked_files": 0,
            "missing_total": 0,
            "library_missing_total": len(missing_candidates),
            "matched": 0,
            "ambiguous": 0,
            "review_pending_total": 0,
            "conflicts": 0,
            "skipped": 0,
            "errors": 0,
            "still_missing": 0,
            "updated": [],
            "needs_review": [],
            "conflict_samples": [],
            "still_missing_samples": [],
            "recent_errors": [],
        }
        used_image_ids: set[int] = set()
        accounted_image_ids: set[int] = set()
        target_candidate_ids: set[int] = set()
        used_found_paths: set[str] = set()
        last_emit = 0.0
        # Roadmap-C review persistence counters (bounded per run).
        persisted_review_count = 0
        review_cap_logged = False

        def candidate_id(row: Dict[str, Any]) -> int:
            try:
                return int(row.get("id") or 0)
            except (TypeError, ValueError):
                return 0

        def refresh_scoped_missing_counts() -> None:
            result["missing_total"] = len(target_candidate_ids)
            result["still_missing"] = max(0, len(target_candidate_ids - accounted_image_ids))

        def emit(force: bool = False, current_item: Optional[str] = None) -> None:
            nonlocal last_emit
            if not progress_callback:
                return
            now = time.monotonic()
            if not force and result["checked_files"] % _svc().RECONNECT_PROGRESS_EVERY_N_FILES != 0 and now - last_emit < _svc().RECONNECT_PROGRESS_MIN_INTERVAL_SECONDS:
                return
            last_emit = now
            progress_callback({**result, "current_item": current_item})

        emit(force=True)
        if not missing_candidates:
            return result

        for found_path, filename in self._iter_reconnect_image_files(normalized_folder, recursive, stop_requested):
            if callable(stop_requested) and stop_requested():
                raise InterruptedError("Reconnect cancelled")
            result["checked_files"] += 1
            candidate_rows = [
                row for row in candidates_by_name.get(filename, [])
                if candidate_id(row) not in used_image_ids
            ]
            if not candidate_rows:
                emit(current_item=filename)
                continue

            for row in candidate_rows:
                row_id = candidate_id(row)
                if row_id > 0:
                    target_candidate_ids.add(row_id)
            refresh_scoped_missing_counts()

            try:
                stat_result = os.stat(found_path)
                match, reason = self._find_reconnect_match(
                    found_path,
                    stat_result,
                    candidate_rows,
                    verify_uncertain=verify_uncertain,
                )
                resolved_found_path = os.path.abspath(found_path)
                if match and resolved_found_path not in used_found_paths:
                    image_id = int(match["id"])
                    existing_at_found_path = db.get_image_by_path(resolved_found_path)
                    if existing_at_found_path and int(existing_at_found_path.get("id") or 0) != image_id:
                        result["conflicts"] += 1
                        accounted_image_ids.add(image_id)
                        refresh_scoped_missing_counts()
                        if len(result["conflict_samples"]) < 10:
                            result["conflict_samples"].append({
                                "filename": filename,
                                "old_image_id": image_id,
                                "old_path": match.get("path"),
                                "found_path": resolved_found_path,
                                "existing_image_id": existing_at_found_path.get("id"),
                                "existing_path": existing_at_found_path.get("path"),
                            })
                        emit(current_item=filename)
                        continue

                    db.reconnect_image_source_path(
                        image_id,
                        resolved_found_path,
                        source_mtime_ns=int(stat_result.st_mtime_ns),
                        source_size=int(stat_result.st_size),
                        source_file_mtime=datetime.fromtimestamp(stat_result.st_mtime),
                    )
                    used_image_ids.add(image_id)
                    accounted_image_ids.add(image_id)
                    used_found_paths.add(resolved_found_path)
                    result["matched"] += 1
                    refresh_scoped_missing_counts()
                    if len(result["updated"]) < 10:
                        result["updated"].append({
                            "image_id": image_id,
                            "filename": filename,
                            "old_path": match.get("path"),
                            "new_path": resolved_found_path,
                            "match": reason,
                        })
                elif reason == "ambiguous":
                    result["ambiguous"] += 1
                    for row in candidate_rows:
                        row_id = candidate_id(row)
                        if row_id > 0:
                            accounted_image_ids.add(row_id)
                    refresh_scoped_missing_counts()
                    if len(result["needs_review"]) < 10:
                        result["needs_review"].append({
                            "filename": filename,
                            "found_path": resolved_found_path,
                            "candidate_count": len(candidate_rows),
                            "old_paths": [row.get("path") for row in candidate_rows[:3]],
                        })
                    # Roadmap-C: persist the ambiguous group for later review,
                    # carrying the REAL candidate image ids (the in-memory
                    # needs_review sample above is capped at 10 and has no ids).
                    # This inserts a review row only; the candidate image rows keep
                    # their old paths until the user explicitly confirms (invariant).
                    if persisted_review_count < _svc().RECONNECT_REVIEW_MAX_PENDING_PER_RUN:
                        review_candidate_ids = [
                            candidate_id(row) for row in candidate_rows if candidate_id(row) > 0
                        ]
                        try:
                            db.add_reconnect_review(
                                filename=filename,
                                found_path=resolved_found_path,
                                candidate_ids=review_candidate_ids,
                                candidate_count=len(candidate_rows),
                                run_started_at=run_started_at,
                            )
                            persisted_review_count += 1
                        except Exception as exc:
                            logger.warning(
                                "Could not persist reconnect review for %s: %s", filename, exc
                            )
                    elif not review_cap_logged:
                        review_cap_logged = True
                        logger.warning(
                            "Reconnect review persistence capped at %d pending rows this run; "
                            "further ambiguous matches are still counted but not individually reviewable.",
                            _svc().RECONNECT_REVIEW_MAX_PENDING_PER_RUN,
                        )
                else:
                    result["skipped"] += 1
            except OSError as exc:
                result["errors"] += 1
                result["recent_errors"].append({"filename": filename, "error": str(exc)})
                result["recent_errors"] = result["recent_errors"][-5:]
            emit(current_item=filename)

        refresh_scoped_missing_counts()
        still_missing_samples = []
        for candidate in missing_candidates:
            candidate_id_value = candidate_id(candidate)
            if candidate_id_value not in target_candidate_ids or candidate_id_value in accounted_image_ids:
                continue
            still_missing_samples.append({
                "image_id": candidate_id_value,
                "filename": candidate.get("filename") or Path(str(candidate.get("path") or "")).name,
                "old_path": candidate.get("path"),
            })
            if len(still_missing_samples) >= 10:
                break
        result["still_missing_samples"] = still_missing_samples
        result["review_pending_total"] = persisted_review_count
        emit(force=True)
        return result

    def start_reconnect_missing_files(self, request: Any, background_tasks: Any) -> Dict[str, str]:
        """Start a background task that reconnects missing image rows to found files."""
        normalized_folder = normalize_user_path(request.search_folder)
        is_valid, error = validate_folder_path(normalized_folder)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid folder path")

        with self._reconnect_lock:
            if self._reconnect_progress.get("status") in {"running", "cancelling"}:
                raise HTTPException(status_code=400, detail="Missing-file reconnect already in progress")
            self._reconnect_run_id += 1
            run_id = self._reconnect_run_id
            cancel_event = threading.Event()
            started_at = time.time()
            self._reconnect_cancel_event = cancel_event
            self._reconnect_progress = {
                **self._build_default_reconnect_progress_state(),
                "status": "running",
                "step": "starting",
                "message": "Looking for missing library files...",
                "started_at": started_at,
                "updated_at": started_at,
            }

        def progress_cb(snapshot: Dict[str, Any]) -> None:
            checked = int(snapshot.get("checked_files", 0) or 0)
            missing_total = int(snapshot.get("missing_total", 0) or 0)
            library_missing_total = int(snapshot.get("library_missing_total", 0) or 0)
            matched = int(snapshot.get("matched", 0) or 0)
            ambiguous = int(snapshot.get("ambiguous", 0) or 0)
            conflicts = int(snapshot.get("conflicts", 0) or 0)
            errors = int(snapshot.get("errors", 0) or 0)
            self._update_reconnect_progress_if_current(
                run_id,
                status="running",
                step="searching",
                current=checked,
                processed=checked,
                total=0,
                total_final=False,
                checked_files=checked,
                missing_total=missing_total,
                library_missing_total=library_missing_total,
                matched=matched,
                ambiguous=ambiguous,
                conflicts=conflicts,
                skipped=int(snapshot.get("skipped", 0) or 0),
                errors=errors,
                message=f"Checked {checked} files. Reconnected {matched}/{missing_total} missing files.",
                current_item=snapshot.get("current_item"),
                updated_at=time.time(),
            )

        def run_reconnect() -> None:
            try:
                result = self.reconnect_missing_files_once(
                    normalized_folder,
                    recursive=bool(request.recursive),
                    verify_uncertain=bool(request.verify_uncertain),
                    progress_callback=progress_cb,
                    stop_requested=cancel_event.is_set,
                )
                # Reconnecting flips matched rows from unreadable->readable, so
                # the cached library-health report (and the "N images can't open"
                # banner this flow's own CTA leads to) is stale. The frontend
                # force-refreshes the banner on completion, but that read would
                # hit the 60s backend cache — invalidate it so the banner drops
                # to the real post-reconnect count right away.
                from services.sorting_service import invalidate_library_health_cache
                invalidate_library_health_cache()
                now = time.time()
                self._set_reconnect_progress_if_current(
                    run_id,
                    {
                        **self._build_default_reconnect_progress_state(),
                        "status": "done",
                        "step": "done",
                        "current": result.get("checked_files", 0),
                        "processed": result.get("checked_files", 0),
                        "total": result.get("checked_files", 0),
                        "total_final": True,
                        "checked_files": result.get("checked_files", 0),
                        "missing_total": result.get("missing_total", 0),
                        "library_missing_total": result.get("library_missing_total", 0),
                        "matched": result.get("matched", 0),
                        "ambiguous": result.get("ambiguous", 0),
                        "review_pending_total": result.get("review_pending_total", 0),
                        "conflicts": result.get("conflicts", 0),
                        "skipped": result.get("skipped", 0),
                        "errors": result.get("errors", 0),
                        "message": (
                            f"Reconnected {result.get('matched', 0)} missing files. "
                            f"{result.get('still_missing', 0)} still missing."
                        ),
                        "current_item": None,
                        "started_at": self._reconnect_progress.get("started_at"),
                        "updated_at": now,
                        "result": result,
                    },
                )
            except InterruptedError:
                current = self.get_reconnect_progress()
                now = time.time()
                self._set_reconnect_progress_if_current(
                    run_id,
                    {
                        **current,
                        "status": "cancelled",
                        "step": "cancelled",
                        "message": f"Stopped after checking {current.get('checked_files', 0)} files.",
                        "updated_at": now,
                    },
                )
            except Exception as exc:
                logger.error("Missing-file reconnect failed: %s", exc, exc_info=True)
                current = self.get_reconnect_progress()
                self._set_reconnect_progress_if_current(
                    run_id,
                    {
                        **current,
                        "status": "error",
                        "step": "error",
                        "errors": int(current.get("errors", 0) or 0) + 1,
                        "message": "Could not finish finding moved files.",
                        "updated_at": time.time(),
                    },
                )

        background_tasks.add_task(run_reconnect)
        return {"status": "started", "message": "Missing-file reconnect started in background"}

    def cancel_reconnect_missing_files(self) -> Dict[str, Any]:
        """Request cancellation of the missing-file reconnect task."""
        with self._reconnect_lock:
            if self._reconnect_progress.get("status") not in {"running", "cancelling"}:
                return self._reconnect_progress.copy()
            if self._reconnect_cancel_event:
                self._reconnect_cancel_event.set()
            self._reconnect_progress = {
                **self._reconnect_progress,
                "status": "cancelling",
                "step": "cancelling",
                "message": "Stopping missing-file search...",
                "updated_at": time.time(),
            }
            return self._reconnect_progress.copy()
