"""Gallery listing, counting, folder/root listing, and single-image lookup.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: facade-owned constants
(VALID_SORT_OPTIONS / VALID_COLOR_TEMPERATURES / VALID_BRIGHTNESS_DISTRIBUTIONS
/ LIMIT_MAX) resolve through _svc() at call time, and the two
_filter_and_mark_missing_images resolver calls pass the facade’s
_BACKEND_FILE (utils/source_paths derives backend_root from
dirname(dirname(backend_file)) — this mixin’s own __file__ is one
level too deep). resolve_existing_indexed_image_path itself resolves through
the facade because the pin suite patches it there.
"""

import os
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import database as db
from constants import VALID_ASPECT_RATIOS
from services.image._constants import DEFAULT_PAGE_SIZE, PROMPT_MATCH_MODE_EXACT
from services.image._filters import (
    _coerce_prompt_match_mode,
    _sanitize_filter_value,
    _sanitize_filter_values,
)
from utils.pagination_cursor import (
    decode_image_cursor,
    encode_image_cursor_from_image,
)
from utils.source_paths import indexed_path_for_runtime


def _svc():
    """Resolve facade-owned seams/constants through services.image_service at call time.

    Tests patch module attributes on the facade; a ``from`` import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.image_service as image_service

    return image_service


def resolve_existing_indexed_image_path(*args, **kwargs):
    """Facade-seam proxy (the pin suite patches services.image_service.resolve_existing_indexed_image_path)."""
    return _svc().resolve_existing_indexed_image_path(*args, **kwargs)


class GalleryMixin:
    """Gallery list/count/lookup slice of ImageService (assembled in services/image_service.py)."""

    def _validate_common_gallery_filters(
        self,
        *,
        sort_by: str,
        aspect_ratio: Optional[str],
        min_width: Optional[int],
        max_width: Optional[int],
        min_height: Optional[int],
        max_height: Optional[int],
        brightness_min: Optional[float] = None,
        brightness_max: Optional[float] = None,
        color_temperature: Optional[str] = None,
        brightness_distribution: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> None:
        """Validate shared gallery filter constraints used by list and selection flows."""
        if scope not in (None, "current_session", "library"):
            raise HTTPException(status_code=400, detail="scope must be current_session or library")
        if sort_by not in _svc().VALID_SORT_OPTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort_by value. Must be one of: {', '.join(_svc().VALID_SORT_OPTIONS)}"
            )

        if aspect_ratio is not None and aspect_ratio not in VALID_ASPECT_RATIOS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid aspect_ratio value. Must be one of: {', '.join(VALID_ASPECT_RATIOS)}"
            )

        if min_width is not None and max_width is not None and min_width > max_width:
            raise HTTPException(
                status_code=400,
                detail="min_width cannot be greater than max_width"
            )
        if min_height is not None and max_height is not None and min_height > max_height:
            raise HTTPException(
                status_code=400,
                detail="min_height cannot be greater than max_height"
            )
        if brightness_min is not None and (brightness_min < 0 or brightness_min > 255):
            raise HTTPException(status_code=400, detail="brightness_min must be between 0 and 255")
        if brightness_max is not None and (brightness_max < 0 or brightness_max > 255):
            raise HTTPException(status_code=400, detail="brightness_max must be between 0 and 255")
        if brightness_min is not None and brightness_max is not None and brightness_min > brightness_max:
            raise HTTPException(status_code=400, detail="brightness_min cannot be greater than brightness_max")
        if color_temperature is not None and color_temperature.lower() not in _svc().VALID_COLOR_TEMPERATURES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid color_temperature value. Must be one of: {', '.join(sorted(_svc().VALID_COLOR_TEMPERATURES))}"
            )
        if brightness_distribution is not None and brightness_distribution.lower() not in _svc().VALID_BRIGHTNESS_DISTRIBUTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid brightness_distribution value. Must be one of: "
                    f"{', '.join(sorted(_svc().VALID_BRIGHTNESS_DISTRIBUTIONS))}"
                )
            )

    def _filter_and_mark_missing_images(self, images: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """Drop rows whose backing files no longer exist and persist that state in SQLite."""
        live_images: List[Dict[str, Any]] = []
        missing_count = 0

        for image in images:
            image_id = int(image.get("id") or 0)
            primary_path = str(image.get("path") or "")
            resolved_path = resolve_existing_indexed_image_path(primary_path, backend_file=_svc()._BACKEND_FILE)
            if resolved_path:
                live_images.append(image)
                continue

            current_image = db.get_image_by_id(image_id) if image_id > 0 else None
            if current_image:
                current_path = str(current_image.get("path") or "")
                current_resolved_path = resolve_existing_indexed_image_path(current_path, backend_file=_svc()._BACKEND_FILE)
                if current_resolved_path:
                    live_images.append(current_image)
                    continue

            missing_count += 1
            if image_id > 0:
                db.mark_image_unreadable(image_id, "File not found on disk")

        return live_images, missing_count

    def set_user_rating(self, image_id: int, stars: int) -> Dict[str, Any]:
        """Set an image's user star rating (0-5; 0 = unrated) — v3.3.2 FF-2.

        ``db.set_user_rating`` validates the range (raising ``ValueError`` for
        out-of-range input, which the router surfaces as HTTP 400) and reports
        whether a row matched so the router can return 404 for an unknown id.
        """
        updated = db.set_user_rating(image_id, stars)
        return {"image_id": int(image_id), "user_rating": int(stars), "updated": bool(updated)}

    def get_images(
        self,
        generators: Optional[str] = None,
        tags: Optional[str] = None,
        tag_mode: str = "and",
        ratings: Optional[str] = None,
        checkpoints: Optional[str] = None,
        loras: Optional[str] = None,
        search: Optional[str] = None,
        artist: Optional[str] = None,
        sort_by: str = "newest",
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: Optional[str] = None,
        offset: Optional[int] = None,
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        prompts: Optional[str] = None,
        prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
        aspect_ratio: Optional[str] = None,
        min_aesthetic: Optional[float] = None,
        max_aesthetic: Optional[float] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_user_rating: Optional[int] = None,  # v3.3.2 FF-2: gallery "★≥N" filter
        excluded_image_ids: Optional[List[int]] = None,
        # v3.2.1 color filters
        brightness_min: Optional[float] = None,
        brightness_max: Optional[float] = None,
        color_temperature: Optional[str] = None,
        brightness_distribution: Optional[str] = None,
        # v3.2.2 per-item exclude filters
        exclude_tags: Optional[str] = None,
        exclude_generators: Optional[str] = None,
        exclude_ratings: Optional[str] = None,
        exclude_checkpoints: Optional[str] = None,
        exclude_loras: Optional[str] = None,
        # v3.3.0 FEAT-EXCLUDE-EXTRA
        exclude_prompts: Optional[str] = None,
        exclude_colors: Optional[str] = None,
        color_hues: Optional[str] = None,  # v3.5.0 dominant-hue include (CSV)
        exclude_color_hues: Optional[str] = None,  # v3.5.0 dominant-hue exclude (CSV)
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
        folder: Optional[str] = None,  # v3.3.2 Library Navigation: recursive folder-subtree scope
        has_metadata: Optional[bool] = None,  # v3.3.2 small-opt: "has SD generation parameters" filter
        # Aurora Phase 3 gallery filters
        no_caption: Optional[bool] = None,  # both ai_caption and nl_caption empty
        aesthetic_unscored: Optional[bool] = None,  # aesthetic_score IS NULL; takes precedence over min/max_aesthetic
        min_saturation: Optional[float] = None,
        max_saturation: Optional[float] = None,
        seed: Optional[int] = None,  # generation seed inside metadata_json
    ) -> Dict[str, Any]:
        """
        Retrieve images with optional filtering using cursor-based pagination.

        Args:
            generators: Comma-separated list of generators
            tags: Comma-separated tags (AND logic)
            ratings: Comma-separated ratings
            checkpoints: Comma-separated checkpoint names
            loras: Comma-separated LoRA names
            search: Free-text search in prompts
            artist: Artist name filter
            sort_by: Sorting method
            limit: Number of images to return
            cursor: Opaque cursor token from a previous page (legacy integer IDs still accepted)
            offset: Offset for fallback pagination when cursor sorting is unavailable
            min_width: Minimum width filter
            max_width: Maximum width filter
            min_height: Minimum height filter
            max_height: Maximum height filter
            prompts: Comma-separated prompt terms
            aspect_ratio: 'square', 'landscape', or 'portrait'
            no_caption: When true, only images with neither an ai_caption nor an nl_caption
            aesthetic_unscored: When true, only images with no aesthetic score. Takes
                precedence over min_aesthetic/max_aesthetic when both are supplied
                (the range is ignored so "unscored" cannot contradict a numeric bound).
            min_saturation/max_saturation: Color-saturation range (requires color analysis)
            seed: Match images generated with this exact seed (read from metadata_json)

        Returns:
            Dict containing images, next_cursor, has_more, total

        Raises:
            HTTPException 400: Invalid parameters
        """
        self._validate_common_gallery_filters(
            sort_by=sort_by,
            aspect_ratio=aspect_ratio,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            scope=scope,
        )

        gen_list = _sanitize_filter_values(generators)
        tag_list = _sanitize_filter_values(tags)
        rating_list = _sanitize_filter_values(ratings)
        cp_list = _sanitize_filter_values(checkpoints)
        lr_list = _sanitize_filter_values(loras)
        prompt_list = _sanitize_filter_values(prompts)
        normalized_prompt_match_mode = _coerce_prompt_match_mode(prompt_match_mode)
        search = _sanitize_filter_value(search) if search else None
        artist = _sanitize_filter_value(artist) if artist else None
        color_temperature = _sanitize_filter_value(color_temperature).lower() if color_temperature else None
        brightness_distribution = _sanitize_filter_value(brightness_distribution).lower() if brightness_distribution else None

        # v3.2.2 per-item exclude filters
        ex_tag_list = _sanitize_filter_values(exclude_tags)
        ex_gen_list = _sanitize_filter_values(exclude_generators)
        ex_rating_list = _sanitize_filter_values(exclude_ratings)
        ex_cp_list = _sanitize_filter_values(exclude_checkpoints)
        ex_lr_list = _sanitize_filter_values(exclude_loras)
        # v3.3.0 FEAT-EXCLUDE-EXTRA
        ex_prompt_list = _sanitize_filter_values(exclude_prompts)
        ex_color_list = _sanitize_filter_values(exclude_colors)
        color_hue_list = _sanitize_filter_values(color_hues)
        ex_color_hue_list = _sanitize_filter_values(exclude_color_hues)

        cursor_payload = None
        if cursor:
            try:
                cursor_payload = decode_image_cursor(cursor)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        supports_cursor_pagination = sort_by in {"newest", "oldest"} and offset is None

        if supports_cursor_pagination:
            collected: List[Dict[str, Any]] = []
            current_cursor = cursor_payload
            total = -1
            total_missing = 0
            fetch_limit = min(max(limit * 2, 32), _svc().LIMIT_MAX)

            while len(collected) < limit + 1:
                result = db.get_images_paginated(
                    folder=folder,
                    has_metadata=has_metadata,
                    no_caption=no_caption,
                    aesthetic_unscored=aesthetic_unscored,
                    min_saturation=min_saturation,
                    max_saturation=max_saturation,
                    seed=seed,
                    generators=gen_list,
                    tags=tag_list,
                    tag_mode=tag_mode,
                    ratings=rating_list,
                    checkpoints=cp_list,
                    loras=lr_list,
                    search_query=search,
                    prompt_terms=prompt_list,
                    prompt_match_mode=normalized_prompt_match_mode,
                    artist=artist,
                    sort_by=sort_by,
                    limit=fetch_limit,
                    cursor_id=current_cursor.image_id if current_cursor else None,
                    cursor_sort_value=current_cursor.sort_value if current_cursor else None,
                    cursor_is_opaque=current_cursor.is_opaque if current_cursor else False,
                    min_width=min_width,
                    max_width=max_width,
                    min_height=min_height,
                    max_height=max_height,
                    aspect_ratio=aspect_ratio,
                    min_aesthetic=min_aesthetic,
                    max_aesthetic=max_aesthetic,
                    date_from=date_from,
                    date_to=date_to,
                    min_user_rating=min_user_rating,
                    brightness_min=brightness_min,
                    brightness_max=brightness_max,
                    color_temperature=color_temperature,
                    brightness_distribution=brightness_distribution,
                    exclude_tags=ex_tag_list,
                    exclude_generators=ex_gen_list,
                    exclude_ratings=ex_rating_list,
                    exclude_checkpoints=ex_cp_list,
                    exclude_loras=ex_lr_list,
                    exclude_prompts=ex_prompt_list,
                    exclude_colors=ex_color_list,
                    color_hues=color_hue_list,
                    exclude_color_hues=ex_color_hue_list,
                    collection_id=collection_id,
                    scope=scope,
                    skip_count=total >= 0,
                )
                if total < 0:
                    total = result.get("total", -1)

                live_images, missing_count = self._filter_and_mark_missing_images(result.get("images", []))
                total_missing += missing_count
                collected.extend(live_images)

                if len(collected) >= limit + 1 or not result.get("has_more") or not result.get("images"):
                    break

                current_cursor = decode_image_cursor(result["next_cursor"])

            has_more = len(collected) > limit
            if has_more:
                collected = collected[:limit]

            if total >= 0:
                total = max(0, total - total_missing)

            return {
                "images": collected,
                "next_cursor": encode_image_cursor_from_image(collected[-1]) if has_more and collected else None,
                "next_offset": None,
                "has_more": has_more,
                "total": total,
            }

        page_offset = max(0, offset or 0)
        fetch_limit = min(max(limit * 2, 32), _svc().LIMIT_MAX)
        scan_offset = page_offset
        images: List[Dict[str, Any]] = []
        total_missing = 0

        while len(images) < limit + 1:
            batch = db.get_images(
                folder=folder,
                has_metadata=has_metadata,
                no_caption=no_caption,
                aesthetic_unscored=aesthetic_unscored,
                min_saturation=min_saturation,
                max_saturation=max_saturation,
                seed=seed,
                generators=gen_list,
                tags=tag_list,
                tag_mode=tag_mode,
                ratings=rating_list,
                checkpoints=cp_list,
                loras=lr_list,
                search_query=search,
                prompt_terms=prompt_list,
                prompt_match_mode=normalized_prompt_match_mode,
                artist=artist,
                sort_by=sort_by,
                limit=fetch_limit,
                offset=scan_offset,
                min_width=min_width,
                max_width=max_width,
                min_height=min_height,
                max_height=max_height,
                aspect_ratio=aspect_ratio,
                min_aesthetic=min_aesthetic,
                max_aesthetic=max_aesthetic,
                date_from=date_from,
                date_to=date_to,
                min_user_rating=min_user_rating,
                brightness_min=brightness_min,
                brightness_max=brightness_max,
                color_temperature=color_temperature,
                brightness_distribution=brightness_distribution,
                exclude_tags=ex_tag_list,
                exclude_generators=ex_gen_list,
                exclude_ratings=ex_rating_list,
                exclude_checkpoints=ex_cp_list,
                exclude_loras=ex_lr_list,
                exclude_prompts=ex_prompt_list,
                exclude_colors=ex_color_list,
                color_hues=color_hue_list,
                exclude_color_hues=ex_color_hue_list,
                collection_id=collection_id,
                scope=scope,
            )
            if not batch:
                break

            live_batch, missing_count = self._filter_and_mark_missing_images(batch)
            total_missing += missing_count
            images.extend(live_batch)
            scan_offset += len(batch)

            if len(images) >= limit + 1 or len(batch) < fetch_limit:
                break

        has_more = len(images) > limit
        if has_more:
            images = images[:limit]

        total = db.get_filtered_image_count(
            folder=folder,
            has_metadata=has_metadata,
            no_caption=no_caption,
            aesthetic_unscored=aesthetic_unscored,
            min_saturation=min_saturation,
            max_saturation=max_saturation,
            seed=seed,
            generators=gen_list,
            tags=tag_list,
            tag_mode=tag_mode,
            ratings=rating_list,
            checkpoints=cp_list,
            loras=lr_list,
            search_query=search,
            prompt_terms=prompt_list,
            prompt_match_mode=normalized_prompt_match_mode,
            artist=artist,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            aspect_ratio=aspect_ratio,
            min_aesthetic=min_aesthetic,
            max_aesthetic=max_aesthetic,
            date_from=date_from,
            date_to=date_to,
            min_user_rating=min_user_rating,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            exclude_tags=ex_tag_list,
            exclude_generators=ex_gen_list,
            exclude_ratings=ex_rating_list,
            exclude_checkpoints=ex_cp_list,
            exclude_loras=ex_lr_list,
            exclude_prompts=ex_prompt_list,
            exclude_colors=ex_color_list,
            color_hues=color_hue_list,
            exclude_color_hues=ex_color_hue_list,
            collection_id=collection_id,
            scope=scope,
        )

        return {
            "images": images,
            "next_cursor": None,
            "next_offset": page_offset + len(images) if has_more else None,
            "has_more": has_more,
            "total": total,
        }

    def get_image_count(
        self,
        *,
        generators: Optional[str] = None,
        tags: Optional[str] = None,
        tag_mode: str = "and",
        ratings: Optional[str] = None,
        checkpoints: Optional[str] = None,
        loras: Optional[str] = None,
        search: Optional[str] = None,
        artist: Optional[str] = None,
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        prompts: Optional[str] = None,
        prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
        aspect_ratio: Optional[str] = None,
        min_aesthetic: Optional[float] = None,
        max_aesthetic: Optional[float] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_user_rating: Optional[int] = None,
        brightness_min: Optional[float] = None,
        brightness_max: Optional[float] = None,
        color_temperature: Optional[str] = None,
        brightness_distribution: Optional[str] = None,
        exclude_tags: Optional[str] = None,
        exclude_generators: Optional[str] = None,
        exclude_ratings: Optional[str] = None,
        exclude_checkpoints: Optional[str] = None,
        exclude_loras: Optional[str] = None,
        exclude_prompts: Optional[str] = None,
        exclude_colors: Optional[str] = None,
        color_hues: Optional[str] = None,  # v3.5.0 dominant-hue include (CSV)
        exclude_color_hues: Optional[str] = None,  # v3.5.0 dominant-hue exclude (CSV)
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
        folder: Optional[str] = None,
        has_metadata: Optional[bool] = None,
        # Aurora Phase 3 gallery filters
        no_caption: Optional[bool] = None,
        aesthetic_unscored: Optional[bool] = None,
        min_saturation: Optional[float] = None,
        max_saturation: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return the exact count of images matching the same filters as GET /api/images.

        Powers the live "Apply · ~N images" filter preview (Aurora Phase 3). Unlike
        the ``total`` field on GET /api/images — which returns a -1 skip sentinel on
        the cursor path for large libraries — this endpoint always runs the count
        query and returns a real total. Sort order and pagination are irrelevant to
        a count, so they are intentionally not accepted.
        """
        self._validate_common_gallery_filters(
            sort_by="newest",
            aspect_ratio=aspect_ratio,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            scope=scope,
        )

        color_temperature = _sanitize_filter_value(color_temperature).lower() if color_temperature else None
        brightness_distribution = _sanitize_filter_value(brightness_distribution).lower() if brightness_distribution else None

        total = db.get_filtered_image_count(
            generators=_sanitize_filter_values(generators),
            tags=_sanitize_filter_values(tags),
            tag_mode=tag_mode,
            ratings=_sanitize_filter_values(ratings),
            checkpoints=_sanitize_filter_values(checkpoints),
            loras=_sanitize_filter_values(loras),
            search_query=_sanitize_filter_value(search) if search else None,
            prompt_terms=_sanitize_filter_values(prompts),
            prompt_match_mode=_coerce_prompt_match_mode(prompt_match_mode),
            artist=_sanitize_filter_value(artist) if artist else None,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            aspect_ratio=aspect_ratio,
            min_aesthetic=min_aesthetic,
            max_aesthetic=max_aesthetic,
            date_from=date_from,
            date_to=date_to,
            min_user_rating=min_user_rating,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            exclude_tags=_sanitize_filter_values(exclude_tags),
            exclude_generators=_sanitize_filter_values(exclude_generators),
            exclude_ratings=_sanitize_filter_values(exclude_ratings),
            exclude_checkpoints=_sanitize_filter_values(exclude_checkpoints),
            exclude_loras=_sanitize_filter_values(exclude_loras),
            exclude_prompts=_sanitize_filter_values(exclude_prompts),
            exclude_colors=_sanitize_filter_values(exclude_colors),
            color_hues=_sanitize_filter_values(color_hues),
            exclude_color_hues=_sanitize_filter_values(exclude_color_hues),
            collection_id=collection_id,
            scope=scope,
            folder=folder,
            has_metadata=has_metadata,
            no_caption=no_caption,
            aesthetic_unscored=aesthetic_unscored,
            min_saturation=min_saturation,
            max_saturation=max_saturation,
            seed=seed,
        )
        return {"total": int(total)}

    def get_library_folders(self) -> Dict[str, Any]:
        """List distinct image directories for the gallery folder tree (v3.3.2 Library Navigation)."""
        return {"folders": db.get_library_folders()}

    def get_library_roots(self) -> Dict[str, Any]:
        """List registered library roots, each with a live indexed-image count (v3.3.2).

        Counts reuse the recursive folder filter so a root reports every image in
        its subtree. Database failures abort the list instead of reporting a false
        zero count.
        """
        roots = db.list_library_roots()
        enriched = []
        for root in roots:
            path = str(root.get("path") or "")
            try:
                count = db.get_filtered_image_count(folder=path)
            except sqlite3.Error as exc:
                database_error = f"{type(exc).__name__}: {exc}"
                message = (
                    f"Could not count indexed images for Library Root '{path}': "
                    f"{database_error}. Restart SD Image Sorter and try again. "
                    "If this continues, open Support Diagnostics and restore a "
                    "compatible database backup before retrying."
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "library_root_count_failed",
                        "message": message,
                        "root_path": path,
                        "database_error": database_error,
                    },
                ) from exc
            runtime_path = indexed_path_for_runtime(path, os.name)
            exists = bool(runtime_path) and os.path.isdir(runtime_path)
            enriched.append({**root, "image_count": count, "exists": exists})
        return {"roots": enriched}

    def get_image_by_id(self, image_id: int) -> Dict[str, Any]:
        """
        Get a single image with its associated tags.

        Args:
            image_id: The unique identifier of the image

        Returns:
            Dict containing 'image' and 'tags' fields

        Raises:
            HTTPException 404: Image not found
        """
        image = db.get_image_by_id(image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        tags = db.get_image_tags(image_id)
        return {"image": image, "tags": tags}

    def patch_image_captions(
        self,
        image_id: int,
        *,
        ai_caption: Optional[str],
        nl_caption: Optional[str],
        set_ai_caption: bool,
        set_nl_caption: bool,
    ) -> Dict[str, Any]:
        """Manually edit ai_caption / nl_caption (FE-3, explicit-clear).

        Raises 400 when neither field was supplied and 404 for unknown ids.
        Returns the stored captions after the write so the client renders
        the authoritative value.
        """
        if not (set_ai_caption or set_nl_caption):
            raise HTTPException(
                status_code=400,
                detail="Provide ai_caption and/or nl_caption",
            )
        updated = db.set_image_captions(
            image_id,
            ai_caption=ai_caption,
            nl_caption=nl_caption,
            set_ai_caption=set_ai_caption,
            set_nl_caption=set_nl_caption,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Image not found")
        image = db.get_image_by_id(image_id) or {}
        return {
            "id": image_id,
            "ai_caption": image.get("ai_caption"),
            "nl_caption": image.get("nl_caption"),
        }
