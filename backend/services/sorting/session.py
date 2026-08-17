"""Manual slot-sort session: start, current image, WASD actions, folders.

Moved verbatim from services/sorting_service.py (decomposition 2026-07).
verify_image_readable resolves through the facade module at call time.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import HTTPException

import database as db
from constants import VALID_ASPECT_RATIOS
from services.sorting_models import (
    FolderConfig,
    SORT_MODE_BRACKET,
    SORT_MODE_CULL,
    SORT_MODE_DEFAULT,
    VALID_PROMPT_MATCH_MODES,
    VALID_SORT_ACTIONS,
    VALID_SORT_MODES,
)
from services.sorting_session_store import remove_session_files
from utils.path_validation import normalize_user_path, validate_folder_path

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


class SortSessionMixin:
    """Slot-sort slice of SortingService (assembled in services/sorting_service.py)."""

    def start_sort_session(
        self,
        generators: Optional[Any] = None,
        tags: Optional[Any] = None,
        tag_mode: str = "and",
        ratings: Optional[Any] = None,
        checkpoints: Optional[Any] = None,
        loras: Optional[Any] = None,
        prompts: Optional[Any] = None,
        prompt_match_mode: str = "exact",
        artist: Optional[str] = None,
        search: Optional[str] = None,
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        min_aesthetic: Optional[float] = None,
        max_aesthetic: Optional[float] = None,
        folders: Optional[Any] = None,
        operation_mode: str = "move",
        replace_existing: bool = False,
        # v3.2.2 per-item exclude filters
        exclude_tags: Optional[Any] = None,
        exclude_generators: Optional[Any] = None,
        exclude_ratings: Optional[Any] = None,
        exclude_checkpoints: Optional[Any] = None,
        exclude_loras: Optional[Any] = None,
        # v3.3.1: per-slot collection ids ({key: collection_id|None}).
        collection_slots: Optional[Any] = None,
        # v3.3.2 Workbench: which culling/sorting mode to run ("slot" = WASD).
        mode: str = SORT_MODE_DEFAULT,
        # v3.3.x gallery-scope parity (trailing kwargs keep positional callers
        # working; None preserves pre-existing behavior exactly).
        min_user_rating: Optional[int] = None,
        brightness_min: Optional[float] = None,
        brightness_max: Optional[float] = None,
        color_temperature: Optional[str] = None,
        brightness_distribution: Optional[str] = None,
        exclude_prompts: Optional[Any] = None,
        exclude_colors: Optional[Any] = None,
        color_hues: Optional[Any] = None,
        exclude_color_hues: Optional[Any] = None,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
        folder: Optional[str] = None,
        has_metadata: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Start a manual sort session."""
        operation_mode = self._validate_file_operation(operation_mode)
        normalized_mode = str(mode or SORT_MODE_DEFAULT).strip().lower()
        if normalized_mode not in VALID_SORT_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort mode. Must be one of: {', '.join(VALID_SORT_MODES)}",
            )
        # Validate aspect_ratio
        if aspect_ratio is not None and aspect_ratio not in VALID_ASPECT_RATIOS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid aspect_ratio. Must be one of: {', '.join(VALID_ASPECT_RATIOS)}"
            )

        with self._sort_session_lock:
            has_active_session = bool(self._sort_session.get("active")) and int(self._sort_session.get("current_index", 0) or 0) < len(self._sort_session.get("image_ids", []) or [])
        if has_active_session and not replace_existing:
            raise HTTPException(
                status_code=409,
                detail="An unfinished manual sort session already exists. Resume it or explicitly start a new session.",
            )

        # Validate dimension ranges
        if min_width is not None and max_width is not None and min_width > max_width:
            raise HTTPException(status_code=400, detail="min_width cannot be greater than max_width")
        if min_height is not None and max_height is not None and min_height > max_height:
            raise HTTPException(status_code=400, detail="min_height cannot be greater than max_height")
        if min_aesthetic is not None and max_aesthetic is not None and min_aesthetic > max_aesthetic:
            raise HTTPException(status_code=400, detail="min_aesthetic cannot be greater than max_aesthetic")
        normalized_prompt_match_mode = str(prompt_match_mode or "exact").strip().lower()
        if normalized_prompt_match_mode not in VALID_PROMPT_MATCH_MODES:
            raise HTTPException(status_code=400, detail="prompt_match_mode must be exact or contains")
        normalized_tag_mode = str(tag_mode or "and").strip().lower()
        if normalized_tag_mode not in {"and", "or"}:
            raise HTTPException(status_code=400, detail="tag_mode must be and or or")

        gen_list = self._coerce_sort_filter_values(generators)
        tag_list = self._coerce_sort_filter_values(tags)
        rating_list = self._coerce_sort_filter_values(ratings)
        cp_list = self._coerce_sort_filter_values(checkpoints)
        lr_list = self._coerce_sort_filter_values(loras)
        prompt_list = self._coerce_sort_filter_values(prompts)
        artist_name = artist.strip() if artist else None
        search_query = search.strip() if search else None

        image_ids = db.get_filtered_image_ids(
            generators=gen_list,
            tags=tag_list,
            tag_mode=normalized_tag_mode,
            ratings=rating_list,
            checkpoints=cp_list,
            loras=lr_list,
            search_query=search_query,
            prompt_terms=prompt_list,
            prompt_match_mode=normalized_prompt_match_mode,
            artist=artist_name,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            aspect_ratio=aspect_ratio,
            min_aesthetic=min_aesthetic,
            max_aesthetic=max_aesthetic,
            exclude_tags=self._coerce_sort_filter_values(exclude_tags),
            exclude_generators=self._coerce_sort_filter_values(exclude_generators),
            exclude_ratings=self._coerce_sort_filter_values(exclude_ratings),
            exclude_checkpoints=self._coerce_sort_filter_values(exclude_checkpoints),
            exclude_loras=self._coerce_sort_filter_values(exclude_loras),
            exclude_prompts=self._coerce_sort_filter_values(exclude_prompts),
            exclude_colors=self._coerce_sort_filter_values(exclude_colors),
            color_hues=self._coerce_sort_filter_values(color_hues),
            exclude_color_hues=self._coerce_sort_filter_values(exclude_color_hues),
            min_user_rating=min_user_rating,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature.strip() if color_temperature else None,
            brightness_distribution=brightness_distribution.strip() if brightness_distribution else None,
            collection_id=collection_id,
            scope=scope,
            folder=folder.strip() if folder else None,
            has_metadata=has_metadata,
        )
        # DB-level filter already excludes images marked unreadable.
        # Per-image verification runs lazily in get_current_sort_image so
        # starting a session doesn't stall on thousands of PIL decodes.

        folder_config = self._parse_sort_folders(folders)
        collection_slot_config = self._coerce_collection_slots(collection_slots)

        # Bracket starts the first candidate (index 0) as champion and the
        # second (index 1) as the first challenger. Slot mode starts at 0.
        initial_index = 1 if normalized_mode == SORT_MODE_BRACKET else 0

        with self._sort_session_lock:
            self._sort_session = self._coerce_sort_session_state({
                "active": True,
                "mode": normalized_mode,
                "image_ids": image_ids,
                "current_index": initial_index,
                "champion_index": 0,
                "folders": folder_config,
                "collection_slots": collection_slot_config,
                "operation_mode": operation_mode,
                "history": [],
                "redo_stack": [],
            })
            self._save_session_to_disk()

        first_image = db.get_image_by_id(image_ids[0]) if image_ids else None

        return {
            "status": "started",
            "total_images": len(image_ids),
            "current": first_image,
            "skipped_unreadable": [],
            "operation_mode": operation_mode,
            "mode": normalized_mode,
        }

    def get_current_sort_image(self) -> Dict[str, Any]:
        """Get the current image in the sort session."""
        with self._sort_session_lock:
            is_bracket = (
                bool(self._sort_session.get("active"))
                and self._sort_session.get("mode") == SORT_MODE_BRACKET
            )
        if is_bracket:
            return self._get_current_bracket_image()
        with self._sort_session_lock:
            is_cull = (
                bool(self._sort_session.get("active"))
                and self._sort_session.get("mode") == SORT_MODE_CULL
            )
        if is_cull:
            return self._get_current_cull_image()
        while True:
            with self._sort_session_lock:
                if not self._sort_session["active"]:
                    return {
                        "active": False,
                        "done": True,
                        "message": "No active sort session",
                        "mode": SORT_MODE_DEFAULT,
                        "image": None,
                        "tags": [],
                        "index": 0,
                        "total": 0,
                        "remaining": 0,
                        "image_ids": [],
                        "folders": {},
                        "collection_slots": {},
                        "operation_mode": "move",
                        **self._get_sort_session_flags([], []),
                    }

                image_ids = self._sort_session["image_ids"]
                if self._sort_session["current_index"] >= len(image_ids):
                    return {
                        "done": True,
                        "message": "All images sorted",
                        "mode": self._sort_session.get("mode", SORT_MODE_DEFAULT),
                    }

                current_id = image_ids[self._sort_session["current_index"]]
                current_index = self._sort_session["current_index"]
                history_snapshot = list(self._sort_session["history"])

            current = db.get_image_by_id(current_id)
            if not current:
                with self._sort_session_lock:
                    self._sort_session["current_index"] += 1
                    self._save_session_to_disk()
                continue

            current_path = self._resolve_image_path(current.get("path") or "")
            if not current_path:
                db.mark_image_unreadable(current_id, "File not found")
                with self._sort_session_lock:
                    self._sort_session["current_index"] += 1
                    self._save_session_to_disk()
                continue

            readable, read_error = verify_image_readable(current_path)
            if not readable:
                db.mark_image_unreadable(current_id, read_error or "Unreadable image")
                with self._sort_session_lock:
                    self._sort_session["current_index"] += 1
                    self._save_session_to_disk()
                continue

            tags = db.get_image_tags(current_id)

            return {
                "image": current,
                "tags": tags,
                "mode": self._sort_session.get("mode", SORT_MODE_DEFAULT),
                "index": current_index,
                "total": len(image_ids),
                "remaining": len(image_ids) - current_index,
                "image_ids": list(image_ids),
                "folders": dict(self._sort_session["folders"]),
                "collection_slots": dict(self._sort_session.get("collection_slots", {})),
                "operation_mode": self._sort_session.get("operation_mode", "move"),
                **self._get_sort_session_flags(history_snapshot, self._sort_session.get("redo_stack", [])),
            }

    def sort_action(
        self,
        action: str,
        folder_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Perform a sort action.

        Slot mode: move, skip, undo, redo, collect. Bracket mode dispatches to
        the A/B handler (champion, challenger, skip, undo, redo).
        """
        with self._sort_session_lock:
            is_bracket = (
                bool(self._sort_session.get("active"))
                and self._sort_session.get("mode") == SORT_MODE_BRACKET
            )
        if is_bracket:
            return self._bracket_action(action)

        with self._sort_session_lock:
            is_cull = (
                bool(self._sort_session.get("active"))
                and self._sort_session.get("mode") == SORT_MODE_CULL
            )
        if is_cull:
            return self._cull_action(action)

        if action not in VALID_SORT_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action. Must be one of: {', '.join(VALID_SORT_ACTIONS)}"
            )

        with self._sort_session_lock:
            if not self._sort_session["active"]:
                raise HTTPException(status_code=400, detail="No active sort session")

            image_ids = self._sort_session["image_ids"]
            operation_mode = self._sort_session.get("operation_mode", "move")

            if action == "undo":
                if self._sort_session["history"]:
                    last = self._sort_session["history"].pop()
                    self._sort_session.setdefault("redo_stack", []).append(last)
                    undone_action = last.get("action")
                    undone_folder_key = last.get("folder_key")
                    if last["action"] == "move":
                        image = db.get_image_by_id(last["image_id"])
                        if image:
                            try:
                                self._undo_file_operation(last)
                            except Exception as e:
                                # Roll the session state back so the user can
                                # retry undo on the same entry. Previously this
                                # silently swallowed the failure and reported
                                # ``status: "undone"`` while the file was
                                # actually still in the destination folder.
                                logger.error(
                                    "Error undoing %s during undo: %s",
                                    last.get("operation") or "move",
                                    e,
                                )
                                self._sort_session["redo_stack"].pop()
                                self._sort_session["history"].append(last)
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"Could not undo last action: {e}",
                                )
                    elif last["action"] == "collect":
                        # v3.3.1: collect adds a membership reference (no file
                        # move). Undo removes that membership. A missing/invalid
                        # collection id can't be reversed; roll the session back
                        # so the user can retry rather than silently advancing.
                        try:
                            self._undo_collect_action(last)
                        except Exception as e:
                            logger.error(
                                "Error undoing collect for image %s: %s",
                                last.get("image_id"),
                                e,
                            )
                            self._sort_session["redo_stack"].pop()
                            self._sort_session["history"].append(last)
                            raise HTTPException(
                                status_code=500,
                                detail=f"Could not undo last action: {e}",
                            )
                    self._sort_session["current_index"] = max(0, self._sort_session["current_index"] - 1)
                else:
                    return {
                        "status": "no_history",
                        "message": "Nothing to undo",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }

                session_flags = self._get_sort_session_flags()

                if self._sort_session["current_index"] < len(image_ids):
                    current_id = image_ids[self._sort_session["current_index"]]
                    current_index = self._sort_session["current_index"]
                    self._save_session_to_disk()
                else:
                    return {
                        "status": "undone",
                        "current_index": self._sort_session["current_index"],
                        "undone_action": undone_action,
                        "folder_key": undone_folder_key,
                        "operation_mode": operation_mode,
                        **session_flags,
                    }

                current = db.get_image_by_id(current_id)
                if not current:
                    current = {"id": current_id, "path": None}
                current_tags = db.get_image_tags(current_id) if current else []
                return {
                    "status": "undone",
                    "undone_action": undone_action,
                    "folder_key": undone_folder_key,
                    "image": current,
                    "tags": current_tags,
                    "index": current_index,
                    "total": len(image_ids),
                    "remaining": len(image_ids) - current_index,
                    "image_ids": list(image_ids),
                    "folders": dict(self._sort_session["folders"]),
                    "operation_mode": operation_mode,
                    **session_flags,
                }

            if action == "redo":
                redo_stack = self._sort_session.setdefault("redo_stack", [])
                if not redo_stack:
                    return {
                        "status": "no_redo",
                        "message": "Nothing to redo",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }

                redo_entry = redo_stack.pop()
                redone_action = redo_entry.get("action")
                redone_folder_key = redo_entry.get("folder_key")
                target_id = redo_entry.get("image_id")
                entry_operation = self._validate_file_operation(redo_entry.get("operation") or operation_mode)

                if redone_action == "move":
                    folder = self._sort_session["folders"].get(redone_folder_key)
                    if not folder:
                        redo_stack.append(redo_entry)
                        return {
                            "error": f"Folder {str(redone_folder_key).upper()} is not configured",
                            "operation_mode": operation_mode,
                            **self._get_sort_session_flags(),
                        }

                    target_image = db.get_image_by_id(target_id) if target_id is not None else None
                    target_path = self._resolve_image_path(target_image.get("path") or "") if target_image else None
                    if not target_image or not target_path:
                        redo_stack.append(redo_entry)
                        return {
                            "error": "Image file not found on disk",
                            "operation_mode": operation_mode,
                            **self._get_sort_session_flags(),
                        }

                    try:
                        operation_result = self._apply_file_operation(
                            operation=entry_operation,
                            image_id=target_image["id"],
                            destination_folder=folder,
                            source_path=target_path,
                        )
                        redo_entry["new_path"] = operation_result["new_path"]
                        redo_entry["copied_image_id"] = operation_result.get("new_image_id")
                    except Exception as e:
                        logger.error("Redo %s failed for image %s: %s", entry_operation, target_id, e)
                        redo_stack.append(redo_entry)
                        return {
                            "error": f"Failed to redo {entry_operation}",
                            "operation_mode": operation_mode,
                            **self._get_sort_session_flags(),
                        }
                elif redone_action == "collect":
                    # v3.3.1: re-add the membership reference (no file move).
                    collection_id = redo_entry.get("collection_id")
                    try:
                        if collection_id is not None and target_id is not None:
                            db.set_collection_membership(int(collection_id), int(target_id), True)
                    except Exception as e:
                        logger.error(
                            "Redo collect failed for image %s into collection %s: %s",
                            target_id,
                            collection_id,
                            e,
                        )
                        redo_stack.append(redo_entry)
                        return {
                            "error": "Failed to redo collect",
                            "operation_mode": operation_mode,
                            **self._get_sort_session_flags(),
                        }

                self._sort_session["history"].append(redo_entry)
                self._sort_session["current_index"] += 1
                session_flags = self._get_sort_session_flags()

                if self._sort_session["current_index"] >= len(image_ids):
                    self._save_session_to_disk()
                    return {
                        "status": "redone",
                        "done": True,
                        "message": "All images sorted",
                        "redone_action": redone_action,
                        "folder_key": redone_folder_key,
                        "operation_mode": operation_mode,
                        **session_flags,
                    }

                next_id = image_ids[self._sort_session["current_index"]]
                next_index = self._sort_session["current_index"]
                self._save_session_to_disk()

                next_image = db.get_image_by_id(next_id)
                next_tags = db.get_image_tags(next_id) if next_image else []

                return {
                    "status": "redone",
                    "redone_action": redone_action,
                    "folder_key": redone_folder_key,
                    "image": next_image,
                    "tags": next_tags,
                    "index": next_index,
                    "total": len(image_ids),
                    "remaining": len(image_ids) - next_index,
                    "image_ids": list(image_ids),
                    "folders": dict(self._sort_session["folders"]),
                    "operation_mode": operation_mode,
                    **session_flags,
                }

            if self._sort_session["current_index"] >= len(image_ids):
                return {"done": True, "operation_mode": operation_mode}

            current_id = image_ids[self._sort_session["current_index"]]
            current_index = self._sort_session["current_index"]

            if action == "move" and not folder_key:
                return {
                    "error": "Folder key is required for move",
                    "operation_mode": operation_mode,
                    **self._get_sort_session_flags(),
                }

            if action == "collect" and not folder_key:
                return {
                    "error": "Folder key is required for collect",
                    "operation_mode": operation_mode,
                    **self._get_sort_session_flags(),
                }

            if action == "move" and folder_key:
                folder = self._sort_session["folders"].get(folder_key)
            else:
                folder = None

            current = db.get_image_by_id(current_id)
            if not current:
                self._sort_session["current_index"] += 1
                self._save_session_to_disk()
                # Skip missing images: fetch next
                session_flags = self._get_sort_session_flags()
                if self._sort_session["current_index"] >= len(image_ids):
                    return {"done": True, "message": "All images sorted", "operation_mode": operation_mode, **session_flags}
                next_id = image_ids[self._sort_session["current_index"]]
                next_index = self._sort_session["current_index"]
                next_image = db.get_image_by_id(next_id)
                next_tags = db.get_image_tags(next_id) if next_image else []
                return {
                    "image": next_image,
                    "tags": next_tags,
                    "index": next_index,
                    "total": len(image_ids),
                    "remaining": len(image_ids) - next_index,
                    "operation_mode": operation_mode,
                    **session_flags,
                }

            if action == "move" and folder_key:
                if not folder:
                    return {
                        "error": f"Folder {folder_key.upper()} is not configured",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
                current_path = self._resolve_image_path(current.get("path") or "")
                if not current_path:
                    return {
                        "error": "Image file not found on disk",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
                try:
                    original_path = current_path
                    operation_result = self._apply_file_operation(
                        operation=operation_mode,
                        image_id=current["id"],
                        destination_folder=folder,
                        source_path=current_path,
                    )
                    self._sort_session["redo_stack"] = []
                    self._sort_session["history"].append({
                        "action": "move",
                        "operation": operation_mode,
                        "image_id": current["id"],
                        "original_path": original_path,
                        "original_folder": os.path.dirname(original_path),
                        "new_path": operation_result["new_path"],
                        "copied_image_id": operation_result.get("new_image_id"),
                        "folder_key": folder_key
                    })
                except Exception as e:
                    logger.error("Sort %s failed for image %d: %s", operation_mode, current["id"], e)
                    return {
                        "error": f"Failed to {operation_mode} image",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
            elif action == "collect":
                # v3.3.1: add the current image to the slot's collection BY
                # REFERENCE. No file is moved/copied — the file stays in place
                # and only a membership row is written, mirroring how the
                # gallery heart/Favorites toggle works.
                collection_id = self._sort_session.get("collection_slots", {}).get(folder_key)
                if not collection_id:
                    return {
                        "error": f"Slot {folder_key.upper()} is not assigned to a collection",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
                try:
                    db.set_collection_membership(int(collection_id), current["id"], True)
                except ValueError as e:
                    logger.error(
                        "Collect failed for image %d into collection %s: %s",
                        current["id"],
                        collection_id,
                        e,
                    )
                    return {
                        "error": "Failed to add image to collection",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
                except Exception as e:
                    logger.error(
                        "Collect failed for image %d into collection %s: %s",
                        current["id"],
                        collection_id,
                        e,
                    )
                    return {
                        "error": "Failed to add image to collection",
                        "operation_mode": operation_mode,
                        **self._get_sort_session_flags(),
                    }
                self._sort_session["redo_stack"] = []
                self._sort_session["history"].append({
                    "action": "collect",
                    "image_id": current["id"],
                    "collection_id": int(collection_id),
                    "folder_key": folder_key,
                })
            elif action == "skip":
                self._sort_session["redo_stack"] = []
                self._sort_session["history"].append({
                    "action": "skip",
                    "image_id": current["id"]
                })

            self._sort_session["current_index"] += 1
            session_flags = self._get_sort_session_flags()

            if self._sort_session["current_index"] >= len(image_ids):
                self._save_session_to_disk()
                return {"done": True, "message": "All images sorted", "operation_mode": operation_mode, **session_flags}

            next_id = image_ids[self._sort_session["current_index"]]
            next_index = self._sort_session["current_index"]
            self._save_session_to_disk()

            next_image = db.get_image_by_id(next_id)
            next_tags = db.get_image_tags(next_id) if next_image else []

            return {
                "image": next_image,
                "tags": next_tags,
                "index": next_index,
                "total": len(image_ids),
                "remaining": len(image_ids) - next_index,
                "operation_mode": operation_mode,
                **session_flags,
            }

    def set_sort_folders(self, config: FolderConfig) -> Dict[str, Any]:
        """Set folder destinations (and optional per-slot collections) for sort keys."""
        normalized_folders = dict(config.folders)
        for key, path in config.folders.items():
            if path:
                normalized_path = normalize_user_path(path)
                is_valid, error = validate_folder_path(normalized_path, allow_create=True)
                if not is_valid:
                    raise HTTPException(status_code=400, detail=error or f"Invalid folder path for key '{key}'")
                try:
                    os.makedirs(normalized_path, exist_ok=True)
                except OSError as exc:
                    raise HTTPException(status_code=400, detail=f"Cannot create folder for key '{key}': {exc}") from exc
                normalized_folders[key] = normalized_path

        # v3.3.1: collection_slots is optional. When provided, validate that
        # each referenced collection actually exists so a stale id can't be
        # silently stored and then no-op at collect time.
        collection_slots = self._validate_collection_slots(config.collection_slots)

        with self._sort_session_lock:
            self._sort_session["folders"] = normalized_folders
            if config.collection_slots is not None:
                self._sort_session["collection_slots"] = collection_slots
            self._save_session_to_disk()
            stored_slots = dict(self._sort_session.get("collection_slots", {}))
        return {"status": "ok", "folders": normalized_folders, "collection_slots": stored_slots}

    def _validate_collection_slots(self, slots: Optional[Any]) -> Dict[str, Optional[int]]:
        """Coerce + verify per-slot collection ids; unknown ids raise a 400."""
        normalized = self._coerce_collection_slots(slots)
        for key, collection_id in normalized.items():
            if collection_id is not None and not db.collection_exists(int(collection_id)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Collection {collection_id} for slot '{key}' does not exist",
                )
        return normalized

    def get_sort_folders(self) -> Dict[str, Any]:
        """Get current folder configuration (and per-slot collections)."""
        with self._sort_session_lock:
            return {
                "folders": self._sort_session["folders"],
                "collection_slots": dict(self._sort_session.get("collection_slots", {})),
            }

    def clear_sort_session(self) -> Dict[str, str]:
        """Clear the current sort session."""
        with self._sort_session_lock:
            self._sort_session = self._build_default_sort_session_state()
        remove_session_files(self._get_session_file_candidates())
        return {'status': 'ok'}
