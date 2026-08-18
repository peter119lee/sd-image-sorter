"""File operations, readability triage, and gallery move/copy flows.

_apply_file_operation / _undo_file_operation / _filter_readable_image_ids /
_move_one_image / move_images / start_move_job, moved verbatim from
services/sorting_service.py (decomposition 2026-07). The move_image /
copy_image / verify_image_readable seams resolve through the facade module
at call time so existing monkeypatches keep landing.
"""

import logging
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException

import database as db
from exceptions import FileOperationError
from services import entry_stats_service
from services.sorting_models import MoveRequest, VALID_FILE_OPERATIONS
from utils.path_validation import normalize_user_path, validate_folder_path
from utils.reported_cause import describe_readability_failure, normalize_reported_cause

# NOTE(decomposition): keep the historical logger channel — tests attach
# handlers / caplog filters to "services.sorting_service" (heartbeat pins),
# and log routing/output must stay byte-identical after the package split.
logger = logging.getLogger("services.sorting_service")


def _is_same_existing_file(left: str, right: str) -> bool:
    """Return whether two existing paths point at the same file on disk."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _describe_file_operation_cause(exc: Exception) -> str:
    """Render an exception as a single-line, path-free, user-readable cause."""
    text = str(exc)
    path = getattr(exc, "path", None)
    operation = getattr(exc, "operation", None)
    # FileOperationError prepends its own "Failed to <op> '<path>':" preamble
    # (exceptions.py). Callers add their own sentence, so drop the duplicate.
    if path and operation:
        text = text.removeprefix(f"Failed to {operation} '{path}': ")
    elif path:
        text = text.removeprefix(f"File operation failed for '{path}': ")

    return normalize_reported_cause(text)


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


def move_image(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.move_image)."""
    return _svc().move_image(*args, **kwargs)


def copy_image(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.copy_image)."""
    return _svc().copy_image(*args, **kwargs)


def verify_image_readable(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.verify_image_readable)."""
    return _svc().verify_image_readable(*args, **kwargs)


class MoveMixin:
    """Move/copy slice of SortingService (assembled in services/sorting_service.py)."""

    @staticmethod
    def _validate_file_operation(operation: Optional[str]) -> str:
        """Normalize file operations to one of the supported modes."""
        normalized = str(operation or "move").strip().lower()
        if normalized not in VALID_FILE_OPERATIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid operation. Must be one of: {', '.join(VALID_FILE_OPERATIONS)}",
            )
        return normalized

    def _apply_file_operation(
        self,
        operation: str,
        image_id: int,
        destination_folder: str,
        source_path: str,
    ) -> Dict[str, Any]:
        """Execute either a move or a copy and return a normalized result payload.

        Copies are file-only (v3.5.0 owner decision): the copy is NOT
        indexed into the library, so ``new_image_id`` is always None on
        the copy branch too.
        """
        normalized_operation = self._validate_file_operation(operation)
        if normalized_operation == "copy":
            result = copy_image(
                image_id=image_id,
                destination_folder=destination_folder,
                image_path=source_path,
            )
            return {
                "operation": "copy",
                "new_path": result["new_path"],
                "new_image_id": result["new_image_id"],
            }

        return {
            "operation": "move",
            "new_path": move_image(image_id, destination_folder, source_path),
            "new_image_id": None,
        }

    @staticmethod
    def _describe_operation_failure(operation: str, exc: Exception) -> str:
        """Build the user-facing text for a failed move/copy, keeping the cause.

        Every failure used to collapse to "Failed to move image", so a
        permission denial, a disconnected drive, a full disk and a file locked
        by another program were indistinguishable to the user even though the
        precise reason was already in the exception.
        """
        cause = _describe_file_operation_cause(exc)
        if not cause:
            return f"Failed to {operation} image"
        return f"Failed to {operation} image: {cause}"

    @staticmethod
    def _restore_file_to_original_path(
        image_id: int,
        source_path: str,
        original_path: str,
    ) -> str:
        """Move a file back to the exact path a sort action took it from.

        An undo restores a file to a location it already owned, so the
        collision suffix ``move_image`` appends — correct for a forward move
        into someone else's folder — is wrong here: it silently renamed
        ``00042.png`` to ``00042_1.png`` and reported the undo as clean. When
        the original path is genuinely taken by a different file we refuse
        instead, so the caller rolls the session back and tells the user rather
        than inventing a new name for their image.

        Serialized by the caller's sort-session lock; the session is
        single-active by design, so no extra locking is needed here.
        """
        restored_path = os.path.abspath(normalize_user_path(original_path))
        current_path = os.path.abspath(source_path)
        if os.path.normcase(current_path) == os.path.normcase(restored_path):
            # Already home: an undo retried after a partial failure, or a move
            # that never left the folder. Nothing to do.
            return restored_path

        destination_folder = os.path.dirname(restored_path)
        is_valid, error = validate_folder_path(destination_folder, allow_create=True)
        if not is_valid:
            raise FileOperationError(
                f"the original folder is no longer usable ({error or 'invalid path'})"
            )
        os.makedirs(destination_folder, exist_ok=True)

        if os.path.exists(restored_path) and not _is_same_existing_file(
            current_path, restored_path
        ):
            raise FileOperationError(
                f"another file named '{os.path.basename(restored_path)}' is already in "
                f"'{os.path.basename(destination_folder) or destination_folder}'. "
                "Move or rename it, then undo again."
            )

        try:
            shutil.move(current_path, restored_path)
        except OSError as exc:
            raise FileOperationError(_describe_file_operation_cause(exc)) from exc

        try:
            db.update_image_path(image_id, restored_path)
        except Exception as db_error:
            try:
                shutil.move(restored_path, current_path)
            except Exception as rollback_error:
                raise FileOperationError(
                    "the library entry could not be updated and the file could not be "
                    f"put back ({_describe_file_operation_cause(db_error)}; "
                    f"{_describe_file_operation_cause(rollback_error)})"
                ) from db_error
            raise FileOperationError(
                "the library entry could not be updated, so the file was left in place "
                f"({_describe_file_operation_cause(db_error)})"
            ) from db_error

        return restored_path

    def _undo_file_operation(self, history_entry: Dict[str, Any]) -> None:
        """Undo a previous move/copy action recorded in manual sort history."""
        operation = self._validate_file_operation(history_entry.get("operation") or history_entry.get("action"))
        if operation == "copy":
            copied_image_id = history_entry.get("copied_image_id")
            copied_path = self._resolve_image_path(history_entry.get("new_path") or "")
            if copied_path and os.path.exists(copied_path):
                os.remove(copied_path)
            if copied_image_id:
                db.delete_image(int(copied_image_id))
            return

        image = db.get_image_by_id(history_entry["image_id"])
        if not image:
            return

        source_path = self._resolve_image_path(image.get("path") or "")
        if not source_path:
            return

        original_path = normalize_user_path(history_entry.get("original_path") or "")
        if original_path:
            self._restore_file_to_original_path(
                history_entry["image_id"], source_path, original_path
            )
            return

        # Legacy history entries recorded before original_path existed can only
        # be restored by folder, which is the pre-v3 behavior.
        original_folder = history_entry.get("original_folder") or ""
        if original_folder:
            move_image(history_entry["image_id"], original_folder, source_path)

    @staticmethod
    def _undo_collect_action(history_entry: Dict[str, Any]) -> None:
        """Undo a previous collect action by removing the membership reference.

        v3.3.1: collect never touches the file, so undo only drops the
        ``collection_items`` row for ``(collection_id, image_id)``.
        """
        collection_id = history_entry.get("collection_id")
        image_id = history_entry.get("image_id")
        if collection_id is None or image_id is None:
            return
        db.set_collection_membership(int(collection_id), int(image_id), False)

    def _filter_readable_image_ids(self, image_ids: List[int]) -> tuple[List[int], List[Dict[str, Any]]]:
        """Drop unreadable images from interactive sorting/move flows and mark them in DB."""
        if not image_ids:
            return [], []

        filtered: List[int] = []
        skipped: List[Dict[str, Any]] = []
        images_map = db.get_images_by_ids(image_ids)

        for image_id in image_ids:
            image = images_map.get(image_id)
            if not image:
                continue

            path = image.get("path") or ""
            source_path = self._resolve_image_path(path)
            filename = image.get("filename") or f"image-{image_id}"
            if not source_path:
                skipped.append({"image_id": image_id, "filename": filename, "error": "File not found"})
                db.mark_image_unreadable(image_id, "File not found")
                continue

            readable, read_error = verify_image_readable(source_path)
            if not readable:
                cause = describe_readability_failure(read_error)
                skipped.append({"image_id": image_id, "filename": filename, "error": cause})
                db.mark_image_unreadable(image_id, cause)
                continue

            filtered.append(image_id)

        return filtered, skipped

    def _move_one_image(
        self,
        image_id: int,
        image: Optional[Dict[str, Any]],
        operation: str,
        destination_folder: str,
    ) -> Dict[str, Any]:
        """Process a single move/copy and return a normalized per-id result.

        v3.3.0 USR-1: shared by the synchronous ``move_images`` endpoint and
        the background move job so both paths produce identical result rows
        and apply the same readability guard before any byte-level move
        deletes a source file.
        """
        source_path = self._resolve_image_path(image.get("path") or "") if image else None
        if not image or not source_path:
            return {"id": image_id, "error": "Image not found", "operation": operation, "success": False}

        readable, read_error = verify_image_readable(source_path)
        if not readable:
            error_message = describe_readability_failure(read_error)
            db.mark_image_unreadable(image_id, error_message)
            return {"id": image_id, "error": error_message, "operation": operation, "success": False}

        try:
            operation_result = self._apply_file_operation(
                operation=operation,
                image_id=image_id,
                destination_folder=destination_folder,
                source_path=source_path,
            )
            return {
                "id": image_id,
                "new_path": operation_result["new_path"],
                "new_image_id": operation_result.get("new_image_id"),
                "operation": operation,
                "success": True,
            }
        except Exception as e:
            logger.error("Failed to %s image %d: %s", operation, image_id, e)
            return {
                "id": image_id,
                "error": self._describe_operation_failure(operation, e),
                "operation": operation,
                "success": False,
            }

    def _expand_move_request_ids(self, request: MoveRequest) -> List[int]:
        """Resolve a MoveRequest into a concrete image-id list.

        v3.2.1 task #34: a ``selection_token`` (Select All Filtered scope) is
        expanded to ids here. ImageService is instantiated directly to avoid
        an import cycle with main.py; the helper is a stateless decoder over
        db (no shared per-instance state matters).
        """
        if request.selection_token:
            from services.image_service import ImageService
            decoder = ImageService()
            image_ids: List[int] = []
            for chunk in decoder._iter_selection_token_snapshot_chunks(
                request.selection_token, chunk_size=500
            ):
                image_ids.extend(chunk)
            return image_ids
        return request.image_ids or []

    def move_images(self, request: MoveRequest) -> Dict[str, Any]:
        """Move specific images to a folder (synchronous; kept for tests and
        programmatic callers — the gallery UI uses ``start_move_job``)."""
        operation = self._validate_file_operation(request.operation)
        destination_folder = normalize_user_path(request.destination_folder)
        is_valid, error = validate_folder_path(destination_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid destination folder")

        image_ids = self._expand_move_request_ids(request)
        if not image_ids:
            return {"results": []}

        # Batch fetch all images in a single query (N+1 fix). The readability
        # check is done per-image inside ``_move_one_image`` so each result
        # lands as it happens (a byte-level move would otherwise silently copy
        # truncated/corrupt PNGs to the destination).
        images_map = db.get_images_by_ids(image_ids)
        try:
            os.makedirs(destination_folder, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create destination folder %s: %s", destination_folder, exc)
            raise HTTPException(
                status_code=400,
                detail=f"Could not create destination folder: {exc}",
            ) from exc

        results = [
            self._move_one_image(image_id, images_map.get(image_id), operation, destination_folder)
            for image_id in image_ids
        ]
        return {"results": results}

    def start_move_job(
        self,
        request: MoveRequest,
        background_tasks: BackgroundTasks,
    ) -> Dict[str, Any]:
        """v3.3.0 USR-1: gallery selection move/copy as a background job with
        progress polling, mirroring ``batch_move_images``. The final progress
        payload embeds the per-id ``results`` list so the frontend mapping is
        identical to the synchronous endpoint. Source files are deleted one at
        a time as the worker advances, so the progress bar tracks deletion."""
        operation = self._validate_file_operation(request.operation)
        destination_folder = normalize_user_path(request.destination_folder)
        is_valid, error = validate_folder_path(destination_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid destination folder")

        with self._move_lock:
            if self._move_progress["status"] in {"running", "cancelling"}:
                raise HTTPException(status_code=409, detail="A move is already in progress")

        image_ids = self._expand_move_request_ids(request)
        total_count = len(image_ids)
        if total_count == 0:
            return {"status": "done", "message": "没有需要移动的图片 / No images to move", "results": [], "total": 0}

        cancel_event = threading.Event()
        with self._move_lock:
            self._move_run_id += 1
            run_id = self._move_run_id
            self._move_cancel_event = cancel_event
            progress_verb = "Copying" if operation == "copy" else "Moving"
            self._move_progress = {
                "status": "running",
                "step": "starting",
                "current": 0,
                "total": total_count,
                "message": f"正在准备{'复制' if operation == 'copy' else '移动'} {total_count} 张图片 / Starting {operation} of {total_count} images...",
                "errors": 0,
                "moved": 0,
                "current_item": None,
                "recent_errors": [],
                "operation": operation,
                "results": [],
                "started_at": time.time(),
                "updated_at": time.time(),
            }

        def run_move():
            results: List[Dict[str, Any]] = []
            moved = 0
            processed = 0
            errors: List[Dict[str, Any]] = []
            try:
                os.makedirs(destination_folder, exist_ok=True)

                def _write_cancelled_state() -> None:
                    completed_verb_local = "Copied" if operation == "copy" else "Moved"
                    self._set_move_progress_if_current(
                        run_id,
                        {
                            "status": "cancelled",
                            "step": "cancelled",
                            "current": processed,
                            "total": total_count,
                            "errors": len(errors),
                            "moved": moved,
                            "message": (
                                f"已取消（{processed}/{total_count}），已{'复制' if operation == 'copy' else '移动'} {moved} 张 / "
                                f"Cancelled at {processed}/{total_count}. "
                                f"{completed_verb_local} {moved} images so far."
                            ),
                            "current_item": None,
                            "recent_errors": errors[-3:],
                            "operation": operation,
                            "results": results,
                            "started_at": self._move_progress.get("started_at"),
                            "updated_at": time.time(),
                        },
                    )

                # Walk the id list in chunks so the per-image DB rows are
                # fetched in batches (matches batch-move's IN(...) chunking)
                # while progress advances per image.
                for start in range(0, total_count, _svc().BATCH_MOVE_FETCH_CHUNK):
                    if cancel_event.is_set():
                        _write_cancelled_state()
                        return
                    chunk_ids = image_ids[start:start + _svc().BATCH_MOVE_FETCH_CHUNK]
                    image_map = db.get_images_by_ids(chunk_ids)

                    for image_id in chunk_ids:
                        if cancel_event.is_set():
                            _write_cancelled_state()
                            return

                        image = image_map.get(image_id)
                        result = self._move_one_image(image_id, image, operation, destination_folder)
                        results.append(result)
                        filename = (image.get("filename") if image else None) or f"id-{image_id}"
                        if result.get("success"):
                            moved += 1
                        else:
                            errors.append({
                                "image_id": image_id,
                                "filename": filename,
                                "error": result.get("error") or "Failed",
                            })
                        processed += 1
                        if not self._update_move_progress_if_current(
                            run_id,
                            step="moving",
                            current=processed,
                            total=total_count,
                            errors=len(errors),
                            moved=moved,
                            message=f"已处理 / Processed: {filename} ({processed}/{total_count})",
                            current_item=filename,
                            recent_errors=errors[-3:],
                            operation=operation,
                            updated_at=time.time(),
                        ):
                            return

                completed_verb = "Copied" if operation == "copy" else "Moved"
                self._set_move_progress_if_current(
                    run_id,
                    {
                        "status": "done",
                        "step": "done",
                        "current": total_count,
                        "total": total_count,
                        "errors": len(errors),
                        "moved": moved,
                        "message": f"完成！已{'复制' if operation == 'copy' else '移动'} {moved} 张图片 / Done! {completed_verb} {moved} images." + (f" {len(errors)} 张失败 / {len(errors)} errors." if errors else ""),
                        "current_item": None,
                        "recent_errors": errors[-3:],
                        "operation": operation,
                        "results": results,
                        "started_at": self._move_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
                entry_stats_service.record_activity(
                    entry_stats_service.KIND_MOVED, moved
                )
            except Exception as e:
                logger.error("Move job failed: %s", e)
                self._set_move_progress_if_current(
                    run_id,
                    {
                        "status": "error",
                        "step": "error",
                        "current": processed,
                        "total": total_count,
                        "errors": len(errors),
                        "moved": moved,
                        "message": "移动因内部错误失败 / Move failed due to an internal error",
                        "current_item": None,
                        "recent_errors": errors[-3:],
                        "operation": operation,
                        "results": results,
                        "started_at": self._move_progress.get("started_at"),
                        "updated_at": time.time(),
                    },
                )
            finally:
                with self._move_lock:
                    if (
                        self._move_run_id == run_id
                        and self._move_cancel_event is cancel_event
                    ):
                        self._move_cancel_event = None

        background_tasks.add_task(run_move)
        return {
            "status": "started",
            "message": f"后台{'复制' if operation == 'copy' else '移动'} {total_count} 张图片中 / {progress_verb} {total_count} images in background",
            "total": total_count,
            "count": total_count,
            "operation": operation,
        }
