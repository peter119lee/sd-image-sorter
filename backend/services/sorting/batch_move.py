"""batch_move_images: filter-scoped background move/copy with id snapshot.

Moved verbatim from services/sorting_service.py (decomposition 2026-07).
The verify_image_readable seam and BATCH_MOVE_FETCH_CHUNK resolve through
the facade module at call time.
"""

import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException

import database as db
from services import entry_stats_service
from services.sorting.move import describe_readability_failure, normalize_reported_cause
from services.sorting_models import BatchMoveRequest
from utils.path_validation import normalize_user_path, validate_folder_path

_SPLIT_FOLDER_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

# NOTE(decomposition): keep the historical logger channel — tests attach
# handlers / caplog filters to "services.sorting_service" (heartbeat pins),
# and log routing/output must stay byte-identical after the package split.
logger = logging.getLogger("services.sorting_service")


def _svc():
    """Resolve UNSAFE monkeypatch seams through the facade at call time.

    Tests patch re-imported names and module-scalar constants on
    ``services.sorting_service``; a ``from`` import here would freeze an
    independent binding those patches silently miss. The lazy import avoids
    a facade<->mixin load cycle.
    """
    import services.sorting_service as sorting_service

    return sorting_service


def verify_image_readable(*args, **kwargs):
    """Facade-seam proxy (tests patch services.sorting_service.verify_image_readable)."""
    return _svc().verify_image_readable(*args, **kwargs)


class BatchMoveMixin:
    """Batch-move slice of SortingService (assembled in services/sorting_service.py)."""

    @staticmethod
    def _sanitize_split_folder_name(raw: str, *, fallback: str = "unknown") -> str:
        """Make a single path segment safe on Windows/POSIX filesystems."""
        text = str(raw or "").strip()
        if not text:
            return fallback
        text = _SPLIT_FOLDER_UNSAFE.sub("_", text)
        text = text.rstrip(" .")
        # Drop extension noise on checkpoints: "foo.safetensors" → "foo"
        if text.lower().endswith((".safetensors", ".ckpt", ".pt", ".pth", ".bin")):
            text = Path(text).stem
        text = text[:80].strip(" ._") or fallback
        return text

    @classmethod
    def _split_destination_for_image(
        cls,
        base_destination: str,
        image: Dict[str, Any],
        split_by: Optional[str],
    ) -> str:
        """Resolve per-image destination folder when split_by is active (B3-②)."""
        if not split_by:
            return base_destination
        if split_by == "generator":
            value = image.get("generator") or "unknown"
        elif split_by == "checkpoint":
            value = (
                image.get("checkpoint_normalized")
                or image.get("checkpoint")
                or "unknown"
            )
        elif split_by == "rating":
            # Content ratings live in the tags table; the image row has user_rating
            # (0–5 stars). Split by star folders: unrated / star-1 … star-5.
            try:
                stars = int(image.get("user_rating") or 0)
            except (TypeError, ValueError):
                stars = 0
            value = f"star-{stars}" if stars > 0 else "unrated"
        else:
            return base_destination
        leaf = cls._sanitize_split_folder_name(str(value))
        return str(Path(base_destination) / leaf)

    @staticmethod
    def _write_id_snapshot(id_chunks) -> str:
        """Write matching IDs to a temp file before mutating their rows."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            for batch_ids in id_chunks:
                for image_id in batch_ids:
                    handle.write(f"{int(image_id)}\n")
            return handle.name

    @staticmethod
    def _iter_id_snapshot_file(snapshot_path: str, chunk_size: int):
        batch: List[int] = []
        with open(snapshot_path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    image_id = int(line.strip())
                except ValueError:
                    continue
                batch.append(image_id)
                if len(batch) >= chunk_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    def batch_move_images(
        self,
        request: BatchMoveRequest,
        background_tasks: BackgroundTasks
    ) -> Dict[str, Any]:
        """Move all images matching filters to a folder with progress tracking."""
        operation = self._validate_file_operation(request.operation)
        destination_folder = normalize_user_path(request.destination_folder)
        is_valid, error = validate_folder_path(destination_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid destination folder")
        split_by = request.split_by  # already normalized by Pydantic (None or facet key)

        with self._batch_move_lock:
            # "cancelling" is still busy: the worker is alive and draining; a
            # second start would race it for the shared progress/cancel state.
            if self._batch_move_progress["status"] in {"running", "cancelling"}:
                raise HTTPException(status_code=409, detail="Batch move already in progress")

        generators = request.generators if request.generators else None
        tags = request.tags if request.tags else None
        tag_mode = request.tag_mode
        ratings = request.ratings if request.ratings else None
        checkpoints = request.checkpoints if request.checkpoints else None
        loras = request.loras if request.loras else None
        prompts = request.prompts if request.prompts else None
        prompt_match_mode = request.prompt_match_mode
        artist = request.artist.strip() if request.artist else None
        search_query = request.search.strip() if request.search else None
        exclude_tags = request.exclude_tags if request.exclude_tags else None
        exclude_generators = request.exclude_generators if request.exclude_generators else None
        exclude_ratings = request.exclude_ratings if request.exclude_ratings else None
        exclude_checkpoints = request.exclude_checkpoints if request.exclude_checkpoints else None
        exclude_loras = request.exclude_loras if request.exclude_loras else None
        # v3.3.x gallery-scope parity (None preserves pre-existing behavior)
        exclude_prompts = request.exclude_prompts if request.exclude_prompts else None
        exclude_colors = request.exclude_colors if request.exclude_colors else None
        color_hues = request.color_hues if request.color_hues else None
        exclude_color_hues = request.exclude_color_hues if request.exclude_color_hues else None
        min_user_rating = request.min_user_rating
        brightness_min = request.brightness_min
        brightness_max = request.brightness_max
        color_temperature = request.color_temperature.strip() if request.color_temperature else None
        brightness_distribution = request.brightness_distribution.strip() if request.brightness_distribution else None
        collection_id = request.collection_id
        folder_scope = request.folder.strip() if request.folder else None
        has_metadata = request.has_metadata

        total_count = db.get_filtered_image_count(
            generators=generators,
            tags=tags,
            tag_mode=tag_mode,
            ratings=ratings,
            checkpoints=checkpoints,
            loras=loras,
            search_query=search_query,
            prompt_terms=prompts,
            prompt_match_mode=prompt_match_mode,
            artist=artist,
            min_width=request.min_width,
            max_width=request.max_width,
            min_height=request.min_height,
            max_height=request.max_height,
            aspect_ratio=request.aspect_ratio,
            min_aesthetic=request.min_aesthetic,
            max_aesthetic=request.max_aesthetic,
            exclude_tags=exclude_tags,
            exclude_generators=exclude_generators,
            exclude_ratings=exclude_ratings,
            exclude_checkpoints=exclude_checkpoints,
            exclude_loras=exclude_loras,
            exclude_prompts=exclude_prompts,
            exclude_colors=exclude_colors,
            color_hues=color_hues,
            exclude_color_hues=exclude_color_hues,
            min_user_rating=min_user_rating,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            collection_id=collection_id,
            scope=request.scope,
            folder=folder_scope,
            has_metadata=has_metadata,
        )

        if total_count == 0:
            return {"message": "没有符合筛选条件的图片 / No images match the filters", "count": 0}

        # Run actual move in background with progress tracking. The
        # cancel event is allocated under the same lock as run_id so
        # cancel_batch_move() always sees a consistent (run_id, event)
        # pair; the worker checks event.is_set() between chunks and
        # between images so a cancel request lands within a few image
        # iterations rather than after the whole batch completes.
        cancel_event = threading.Event()
        with self._batch_move_lock:
            self._batch_move_run_id += 1
            run_id = self._batch_move_run_id
            self._batch_move_cancel_event = cancel_event
            self._batch_move_progress = {
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
                "started_at": time.time(),
                "updated_at": time.time(),
            }

        def run_batch_move():
            try:
                # Note: previously this called ``_filter_readable_image_ids``
                # before the loop, which did a full pixel decode of every
                # image up front via ``verify_image_readable``. For large
                # batches that blocked the worker for many minutes with no
                # progress emitted, making the operation indistinguishable
                # from a hang. The decode is now done per-image inside the
                # inner loop so progress advances as the worker walks the
                # list (a byte-level move would otherwise silently copy
                # truncated/corrupt PNGs to the destination).

                os.makedirs(destination_folder, exist_ok=True)

                moved = 0
                processed = 0
                errors: List[Dict[str, Any]] = []

                def _report_error(image_id: int, filename: str, cause: str) -> None:
                    """The only way an entry gets into the panel's list.

                    Three branches can fail an image here, and each used to
                    write its own cause. One of them (the readability check)
                    reported Pillow's raw string, absolute path and all, which
                    is exactly the shape errors.js discards — so the panel
                    printed "An unexpected error occurred. Please try again."
                    for a file that will never decode. Normalizing at the one
                    place the list is written keeps the next branch honest too.
                    """
                    errors.append({
                        "image_id": image_id,
                        "filename": filename,
                        "error": normalize_reported_cause(cause),
                    })

                def _write_cancelled_state() -> None:
                    """Publish the cancelled summary for this batch-move run."""
                    completed_verb_local = "Copied" if operation == "copy" else "Moved"
                    self._set_batch_move_progress_if_current(
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
                            "started_at": self._batch_move_progress.get("started_at"),
                            "updated_at": time.time(),
                        }
                    )

                snapshot_path = self._write_id_snapshot(db.iter_filtered_image_id_chunks(
                    chunk_size=_svc().BATCH_MOVE_FETCH_CHUNK,
                    generators=generators,
                    tags=tags,
                    tag_mode=tag_mode,
                    ratings=ratings,
                    checkpoints=checkpoints,
                    loras=loras,
                    search_query=search_query,
                    prompt_terms=prompts,
                    prompt_match_mode=prompt_match_mode,
                    artist=artist,
                    min_width=request.min_width,
                    max_width=request.max_width,
                    min_height=request.min_height,
                    max_height=request.max_height,
                    aspect_ratio=request.aspect_ratio,
                    min_aesthetic=request.min_aesthetic,
                    max_aesthetic=request.max_aesthetic,
                    exclude_tags=exclude_tags,
                    exclude_generators=exclude_generators,
                    exclude_ratings=exclude_ratings,
                    exclude_checkpoints=exclude_checkpoints,
                    exclude_loras=exclude_loras,
                    exclude_prompts=exclude_prompts,
                    exclude_colors=exclude_colors,
                    color_hues=color_hues,
                    exclude_color_hues=exclude_color_hues,
                    min_user_rating=min_user_rating,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    color_temperature=color_temperature,
                    brightness_distribution=brightness_distribution,
                    collection_id=collection_id,
                    scope=request.scope,
                    folder=folder_scope,
                    has_metadata=has_metadata,
                ))
                saw_any_ids = False
                try:
                    snapshot_batches = self._iter_id_snapshot_file(snapshot_path, _svc().BATCH_MOVE_FETCH_CHUNK)
                    for batch_ids in snapshot_batches:
                        if cancel_event.is_set():
                            _write_cancelled_state()
                            return

                        saw_any_ids = True
                        image_map = db.get_images_by_ids(batch_ids)

                        for image_id in batch_ids:
                            if cancel_event.is_set():
                                _write_cancelled_state()
                                return

                            image = image_map.get(image_id)
                            if not image:
                                processed += 1
                                _report_error(image_id, f"id-{image_id}", "Image row not found")
                                continue

                            filename = image.get("filename", "image")
                            error_message = None

                            source_path = self._resolve_image_path(image.get("path") or "")
                            if not source_path:
                                error_message = "Image file not found"
                            else:
                                readable, read_error = verify_image_readable(source_path)
                                if not readable:
                                    error_message = describe_readability_failure(read_error)
                                    db.mark_image_unreadable(image["id"], error_message)
                                else:
                                    try:
                                        # B3-②: optional per-image subfolder under destination.
                                        target_folder = self._split_destination_for_image(
                                            destination_folder, image, split_by
                                        )
                                        if target_folder != destination_folder:
                                            os.makedirs(target_folder, exist_ok=True)
                                        self._apply_file_operation(
                                            operation=operation,
                                            image_id=image["id"],
                                            destination_folder=target_folder,
                                            source_path=source_path,
                                        )
                                        moved += 1
                                    except Exception as e:
                                        # Same descriptor gallery move and
                                        # Manual Sort use: str(e) here kept the
                                        # duplicated FileOperationError preamble,
                                        # the OS line breaks and every absolute
                                        # path, which is the exact shape
                                        # frontend/js/modules/utils/errors.js
                                        # throws away.
                                        error_message = self._describe_operation_failure(operation, e)

                            if error_message:
                                _report_error(image_id, filename, error_message)

                            processed += 1
                            if not self._update_batch_move_progress_if_current(
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
                finally:
                    try:
                        os.unlink(snapshot_path)
                    except OSError:
                        logger.debug("Failed to remove batch move snapshot temp file: %s", snapshot_path)

                if not saw_any_ids:
                    self._set_batch_move_progress_if_current(
                        run_id,
                        {
                            "status": "done",
                            "step": "done",
                            "current": 0,
                            "total": 0,
                            "message": "没有符合筛选条件的图片 / No images match the filters",
                            "errors": 0,
                            "moved": 0,
                            "current_item": None,
                            "recent_errors": [],
                            "operation": operation,
                            "started_at": time.time(),
                            "updated_at": time.time(),
                        }
                    )
                    return

                completed_verb = "Copied" if operation == "copy" else "Moved"
                self._set_batch_move_progress_if_current(
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
                        "started_at": self._batch_move_progress.get("started_at"),
                        "updated_at": time.time(),
                    }
                )
                entry_stats_service.record_activity(
                    entry_stats_service.KIND_MOVED, moved
                )

            except Exception as e:
                logger.error("Batch move failed: %s", e)
                with self._batch_move_lock:
                    current = self._batch_move_progress.get("current", 0) if run_id == self._batch_move_run_id else 0
                    errors_count = self._batch_move_progress.get("errors", 0) if run_id == self._batch_move_run_id else 0
                    moved_count = self._batch_move_progress.get("moved", 0) if run_id == self._batch_move_run_id else 0

                self._set_batch_move_progress_if_current(
                    run_id,
                    {
                        "status": "error",
                        "step": "error",
                        "current": current,
                        "total": total_count,
                        "errors": errors_count,
                        "moved": moved_count,
                        "message": "批量移动因内部错误失败 / Batch move failed due to an internal error",
                        "current_item": None,
                        "recent_errors": self._batch_move_progress.get("recent_errors", []) if run_id == self._batch_move_run_id else [],
                        "operation": operation,
                        "started_at": self._batch_move_progress.get("started_at") if run_id == self._batch_move_run_id else None,
                        "updated_at": time.time(),
                    }
                )
            finally:
                # Release this run's cancel-event reference so cancel_batch_move
                # can't operate on a stale event after the worker has exited.
                # Only clear when we're still the active run — a newer run
                # would have published its own event under the same lock.
                with self._batch_move_lock:
                    if (
                        self._batch_move_run_id == run_id
                        and self._batch_move_cancel_event is cancel_event
                    ):
                        self._batch_move_cancel_event = None

        background_tasks.add_task(run_batch_move)
        progress_verb = "Copying" if operation == "copy" else "Moving"
        return {
            "status": "started",
            "message": f"后台{'复制' if operation == 'copy' else '移动'} {total_count} 张图片中 / {progress_verb} {total_count} images in background",
            "total": total_count,
            "count": total_count,
            "operation": operation,
        }
