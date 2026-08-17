"""Filtered-selection: the canonical filter contract, the selection-token
wire format (encode/decode + legacy snake_case compat), chunked ID retrieval,
export-selection pages, and the cross-service snapshot generator
_iter_selection_token_snapshot_chunks that services/sorting/move.py
instantiates ImageService() to reach.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: facade-owned selection/token
constants (SELECTION_TOKEN_VERSION / SELECTION_IDS_MAX_RESPONSE /
SELECTION_TOKEN_RANDOM_SORT_ERROR / chunk caps / PROMPT_MATCH_MODE_*) resolve
through _svc() at call time — tests read and patch them on the facade.
Coercer/sanitizer call sites stay verbatim via services.image._filters.
"""

import base64
import binascii
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

import database as db
from services.image._constants import (
    PROMPT_MATCH_MODE_EXACT,
    SELECTION_TOKEN_DEFAULT_CHUNK,
)
from services.image._filters import (
    _coerce_optional_bool_filter,
    _coerce_optional_date_filter,
    _coerce_optional_float_filter,
    _coerce_optional_int_filter,
    _coerce_optional_string_filter,
    _coerce_prompt_match_mode,
    _coerce_selection_id_list,
    _coerce_tag_mode,
    _invalid_selection_token,
    _sanitize_filter_value,
    _sanitize_filter_values,
)
from services.tag_export_service import extract_generation_params

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


class SelectionMixin:
    """Filtered-selection token/ids slice of ImageService (assembled in services/image_service.py)."""

    def _iter_selection_token_snapshot_chunks(self, selection_token: str, *, chunk_size: int = 500):
        """Snapshot token IDs to a temp file before mutating matching rows."""
        contract = self._decode_selection_token(selection_token)
        if contract["sortBy"] == "random":
            raise HTTPException(status_code=400, detail=_svc().SELECTION_TOKEN_RANDOM_SORT_ERROR)

        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                temp_path = handle.name
                for batch_ids in db.iter_filtered_image_id_chunks(
                    chunk_size=chunk_size,
                    generators=contract["generators"],
                    tags=contract["tags"],
                    tag_mode=contract.get("tagMode", "and"),
                    ratings=contract["ratings"],
                    checkpoints=contract["checkpoints"],
                    loras=contract["loras"],
                    search_query=contract["search"] or None,
                    sort_by=contract["sortBy"],
                    min_width=contract["minWidth"],
                    max_width=contract["maxWidth"],
                    min_height=contract["minHeight"],
                    max_height=contract["maxHeight"],
                    prompt_terms=contract["prompts"],
                    prompt_match_mode=contract["promptMatchMode"],
                    aspect_ratio=contract["aspectRatio"],
                    artist=contract["artist"],
                    min_aesthetic=contract["minAesthetic"],
                    max_aesthetic=contract["maxAesthetic"],
                    date_from=contract.get("dateFrom"),
                    date_to=contract.get("dateTo"),
                    min_user_rating=contract["minUserRating"],
                    brightness_min=contract["brightnessMin"],
                    brightness_max=contract["brightnessMax"],
                    color_temperature=contract["colorTemperature"],
                    brightness_distribution=contract["brightnessDistribution"],
                    exclude_tags=contract.get("excludeTags"),
                    exclude_generators=contract.get("excludeGenerators"),
                    exclude_ratings=contract.get("excludeRatings"),
                    exclude_checkpoints=contract.get("excludeCheckpoints"),
                    exclude_loras=contract.get("excludeLoras"),
                    exclude_prompts=contract.get("excludePrompts"),
                    exclude_colors=contract.get("excludeColors"),
                    color_hues=contract.get("colorHues"),
                    exclude_color_hues=contract.get("excludeColorHues"),
                    collection_id=contract.get("collectionId"),
                    scope=contract.get("scope"),
                    folder=contract.get("folder"),
                    has_metadata=contract.get("hasMetadata"),
                    no_caption=contract.get("noCaption"),
                    aesthetic_unscored=contract.get("aestheticUnscored"),
                    min_saturation=contract.get("minSaturation"),
                    max_saturation=contract.get("maxSaturation"),
                    seed=contract.get("seed"),
                ):
                    for image_id in batch_ids:
                        handle.write(f"{int(image_id)}\n")

            batch: List[int] = []
            with open(temp_path, "r", encoding="utf-8") as handle:
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
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    logger.debug("Failed to remove selection snapshot temp file: %s", temp_path)

    def _build_selection_filter_contract(
        self,
        *,
        generators: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        ratings: Optional[List[str]] = None,
        checkpoints: Optional[List[str]] = None,
        loras: Optional[List[str]] = None,
        prompts: Optional[List[str]] = None,
        tag_mode: str = "and",
        prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
        artist: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "newest",
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        min_aesthetic: Optional[float] = None,
        max_aesthetic: Optional[float] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_user_rating: Optional[int] = None,
        excluded_image_ids: Optional[List[int]] = None,
        # v3.2.1 color filters
        brightness_min: Optional[float] = None,
        brightness_max: Optional[float] = None,
        color_temperature: Optional[str] = None,
        brightness_distribution: Optional[str] = None,
        # v3.2.2 per-item exclude filters
        exclude_tags: Optional[List[str]] = None,
        exclude_generators: Optional[List[str]] = None,
        exclude_ratings: Optional[List[str]] = None,
        exclude_checkpoints: Optional[List[str]] = None,
        exclude_loras: Optional[List[str]] = None,
        exclude_prompts: Optional[List[str]] = None,
        exclude_colors: Optional[List[str]] = None,
        color_hues: Optional[List[str]] = None,
        exclude_color_hues: Optional[List[str]] = None,
        collection_id: Optional[int] = None,
        scope: Optional[str] = None,
        folder: Optional[str] = None,  # v3.3.2 Library Navigation
        has_metadata: Optional[bool] = None,  # v3.3.2 small-opt: "has SD generation parameters" filter
        # Aurora Phase 3 gallery filters
        no_caption: Optional[bool] = None,
        aesthetic_unscored: Optional[bool] = None,
        min_saturation: Optional[float] = None,
        max_saturation: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build the canonical filter contract encoded into selection tokens."""
        sort_by = _coerce_optional_string_filter(sort_by, "sortBy") or "newest"
        artist = _coerce_optional_string_filter(artist, "artist")
        search = _coerce_optional_string_filter(search, "search")
        aspect_ratio = _coerce_optional_string_filter(aspect_ratio, "aspectRatio")
        min_width = _coerce_optional_int_filter(min_width, "minWidth")
        max_width = _coerce_optional_int_filter(max_width, "maxWidth")
        min_height = _coerce_optional_int_filter(min_height, "minHeight")
        max_height = _coerce_optional_int_filter(max_height, "maxHeight")
        min_aesthetic = _coerce_optional_float_filter(min_aesthetic, "minAesthetic")
        max_aesthetic = _coerce_optional_float_filter(max_aesthetic, "maxAesthetic")
        date_from = _coerce_optional_date_filter(date_from, "dateFrom")
        date_to = _coerce_optional_date_filter(date_to, "dateTo")
        min_user_rating = _coerce_optional_int_filter(min_user_rating, "minUserRating")
        brightness_min = _coerce_optional_float_filter(brightness_min, "brightnessMin")
        brightness_max = _coerce_optional_float_filter(brightness_max, "brightnessMax")
        color_temperature = _coerce_optional_string_filter(color_temperature, "colorTemperature")
        color_temperature = color_temperature.lower() if color_temperature else None
        brightness_distribution = _coerce_optional_string_filter(brightness_distribution, "brightnessDistribution")
        brightness_distribution = brightness_distribution.lower() if brightness_distribution else None
        collection_id = _coerce_optional_int_filter(collection_id, "collectionId")
        scope = _coerce_optional_string_filter(scope, "scope")
        if scope not in (None, "current_session", "library"):
            raise HTTPException(status_code=400, detail="scope must be current_session or library")
        tag_mode = _coerce_tag_mode(tag_mode)
        prompt_match_mode = _coerce_prompt_match_mode(prompt_match_mode)
        excluded_image_ids = _coerce_selection_id_list(
            excluded_image_ids,
            "excludedImageIds",
            max_length=_svc().SELECTION_TOKEN_MAX_EXCLUDED_IDS,
        )

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
        )
        return {
            "generators": _sanitize_filter_values(generators) or [],
            "tags": _sanitize_filter_values(tags) or [],
            "tagMode": tag_mode,
            "ratings": _sanitize_filter_values(ratings) or [],
            "checkpoints": _sanitize_filter_values(checkpoints) or [],
            "loras": _sanitize_filter_values(loras) or [],
            "prompts": _sanitize_filter_values(prompts) or [],
            "promptMatchMode": prompt_match_mode,
            "artist": _sanitize_filter_value(artist) if artist else None,
            "search": _sanitize_filter_value(search) if search else "",
            "sortBy": sort_by or "newest",
            "minWidth": min_width,
            "maxWidth": max_width,
            "minHeight": min_height,
            "maxHeight": max_height,
            "aspectRatio": aspect_ratio,
            "minAesthetic": min_aesthetic,
            "maxAesthetic": max_aesthetic,
            "minUserRating": min_user_rating,
            "brightnessMin": brightness_min,
            "brightnessMax": brightness_max,
            "colorTemperature": color_temperature,
            "brightnessDistribution": brightness_distribution,
            "excludedImageIds": excluded_image_ids,
            "excludeTags": _sanitize_filter_values(exclude_tags) or [],
            "excludeGenerators": _sanitize_filter_values(exclude_generators) or [],
            "excludeRatings": _sanitize_filter_values(exclude_ratings) or [],
            "excludeCheckpoints": _sanitize_filter_values(exclude_checkpoints) or [],
            "excludeLoras": _sanitize_filter_values(exclude_loras) or [],
            "excludePrompts": _sanitize_filter_values(exclude_prompts) or [],
            "excludeColors": _sanitize_filter_values(exclude_colors) or [],
            "colorHues": _sanitize_filter_values(color_hues) or [],
            "excludeColorHues": _sanitize_filter_values(exclude_color_hues) or [],
            "collectionId": collection_id,
            "scope": scope,
            "folder": _coerce_optional_string_filter(folder, "folder"),
            "hasMetadata": _coerce_optional_bool_filter(has_metadata, "hasMetadata"),
            # Aurora Phase 3 gallery filters
            "noCaption": _coerce_optional_bool_filter(no_caption, "noCaption"),
            "aestheticUnscored": _coerce_optional_bool_filter(aesthetic_unscored, "aestheticUnscored"),
            "minSaturation": _coerce_optional_float_filter(min_saturation, "minSaturation"),
            "maxSaturation": _coerce_optional_float_filter(max_saturation, "maxSaturation"),
            "seed": _coerce_optional_int_filter(seed, "seed"),
            # File-time day range (timeline-eval memo 2026-07-12): filters on
            # first-seen mtime, deliberately NOT called "generation date".
            "dateFrom": date_from,
            "dateTo": date_to,
        }

    def _selection_ids_from_contract(
        self,
        contract: Dict[str, Any],
        *,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> List[int]:
        return db.get_filtered_image_ids(
            generators=contract["generators"],
            tags=contract["tags"],
            tag_mode=contract.get("tagMode", "and"),
            ratings=contract["ratings"],
            checkpoints=contract["checkpoints"],
            loras=contract["loras"],
            search_query=contract["search"] or None,
            sort_by=contract["sortBy"],
            min_width=contract["minWidth"],
            max_width=contract["maxWidth"],
            min_height=contract["minHeight"],
            max_height=contract["maxHeight"],
            prompt_terms=contract["prompts"],
            prompt_match_mode=contract["promptMatchMode"],
            aspect_ratio=contract["aspectRatio"],
            artist=contract["artist"],
            min_aesthetic=contract["minAesthetic"],
            max_aesthetic=contract["maxAesthetic"],
            date_from=contract.get("dateFrom"),
            date_to=contract.get("dateTo"),
            min_user_rating=contract["minUserRating"],
            brightness_min=contract["brightnessMin"],
            brightness_max=contract["brightnessMax"],
            color_temperature=contract["colorTemperature"],
            brightness_distribution=contract["brightnessDistribution"],
            excluded_image_ids=contract.get("excludedImageIds"),
            exclude_tags=contract.get("excludeTags"),
            exclude_generators=contract.get("excludeGenerators"),
            exclude_ratings=contract.get("excludeRatings"),
            exclude_checkpoints=contract.get("excludeCheckpoints"),
            exclude_loras=contract.get("excludeLoras"),
            exclude_prompts=contract.get("excludePrompts"),
            exclude_colors=contract.get("excludeColors"),
            color_hues=contract.get("colorHues"),
            exclude_color_hues=contract.get("excludeColorHues"),
            collection_id=contract.get("collectionId"),
            scope=contract.get("scope"),
            folder=contract.get("folder"),
            has_metadata=contract.get("hasMetadata"),
            no_caption=contract.get("noCaption"),
            aesthetic_unscored=contract.get("aestheticUnscored"),
            min_saturation=contract.get("minSaturation"),
            max_saturation=contract.get("maxSaturation"),
            seed=contract.get("seed"),
            fetch_chunk_size=_svc().SELECTION_IDS_FETCH_CHUNK,
            offset=offset,
            limit=limit,
        )

    def _selection_total_estimate(self, contract: Dict[str, Any]) -> int:
        return db.get_filtered_image_count(
            generators=contract["generators"],
            tags=contract["tags"],
            tag_mode=contract.get("tagMode", "and"),
            ratings=contract["ratings"],
            checkpoints=contract["checkpoints"],
            loras=contract["loras"],
            search_query=contract["search"] or None,
            min_width=contract["minWidth"],
            max_width=contract["maxWidth"],
            min_height=contract["minHeight"],
            max_height=contract["maxHeight"],
            prompt_terms=contract["prompts"],
            prompt_match_mode=contract["promptMatchMode"],
            aspect_ratio=contract["aspectRatio"],
            artist=contract["artist"],
            min_aesthetic=contract["minAesthetic"],
            max_aesthetic=contract["maxAesthetic"],
            date_from=contract.get("dateFrom"),
            date_to=contract.get("dateTo"),
            min_user_rating=contract["minUserRating"],
            brightness_min=contract["brightnessMin"],
            brightness_max=contract["brightnessMax"],
            color_temperature=contract["colorTemperature"],
            brightness_distribution=contract["brightnessDistribution"],
            excluded_image_ids=contract.get("excludedImageIds"),
            exclude_tags=contract.get("excludeTags"),
            exclude_generators=contract.get("excludeGenerators"),
            exclude_ratings=contract.get("excludeRatings"),
            exclude_checkpoints=contract.get("excludeCheckpoints"),
            exclude_loras=contract.get("excludeLoras"),
            exclude_prompts=contract.get("excludePrompts"),
            exclude_colors=contract.get("excludeColors"),
            color_hues=contract.get("colorHues"),
            exclude_color_hues=contract.get("excludeColorHues"),
            collection_id=contract.get("collectionId"),
            scope=contract.get("scope"),
            folder=contract.get("folder"),
            has_metadata=contract.get("hasMetadata"),
            no_caption=contract.get("noCaption"),
            aesthetic_unscored=contract.get("aestheticUnscored"),
            min_saturation=contract.get("minSaturation"),
            max_saturation=contract.get("maxSaturation"),
            seed=contract.get("seed"),
        )

    def _encode_selection_token(self, contract: Dict[str, Any]) -> str:
        payload = {
            "v": _svc().SELECTION_TOKEN_VERSION,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "filters": contract,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _decode_selection_token(self, selection_token: str) -> Dict[str, Any]:
        try:
            padded = selection_token + "=" * (-len(selection_token) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid selection token")

        if not isinstance(payload, dict) or payload.get("v") != _svc().SELECTION_TOKEN_VERSION:
            raise HTTPException(status_code=400, detail="Invalid selection token")
        filters = payload.get("filters")
        if not isinstance(filters, dict):
            raise HTTPException(status_code=400, detail="Invalid selection token")
        for list_field in (
            "generators",
            "tags",
            "ratings",
            "checkpoints",
            "loras",
            "prompts",
            "excludeTags",
            "excludeGenerators",
            "excludeRatings",
            "excludeCheckpoints",
            "excludeLoras",
            "excludePrompts",
            "excludeColors",
        ):
            value = filters.get(list_field)
            if value is not None and not isinstance(value, list):
                raise _invalid_selection_token()

        try:
            return self._build_selection_filter_contract(
                generators=filters.get("generators"),
                tags=filters.get("tags"),
                tag_mode=filters.get("tagMode") or filters.get("tag_mode") or "and",
                ratings=filters.get("ratings"),
                checkpoints=filters.get("checkpoints"),
                loras=filters.get("loras"),
                prompts=filters.get("prompts"),
                prompt_match_mode=filters.get("promptMatchMode") or filters.get("prompt_match_mode") or _svc().PROMPT_MATCH_MODE_EXACT,
                artist=filters.get("artist"),
                search=filters.get("search"),
                sort_by=filters.get("sortBy") or "newest",
                min_width=filters.get("minWidth"),
                max_width=filters.get("maxWidth"),
                min_height=filters.get("minHeight"),
                max_height=filters.get("maxHeight"),
                aspect_ratio=filters.get("aspectRatio"),
                min_aesthetic=filters.get("minAesthetic"),
                max_aesthetic=filters.get("maxAesthetic"),
                min_user_rating=filters.get("minUserRating") or filters.get("min_user_rating"),
                brightness_min=filters.get("brightnessMin"),
                brightness_max=filters.get("brightnessMax"),
                color_temperature=filters.get("colorTemperature"),
                brightness_distribution=filters.get("brightnessDistribution"),
                excluded_image_ids=filters.get("excludedImageIds"),
                exclude_tags=filters.get("excludeTags"),
                exclude_generators=filters.get("excludeGenerators"),
                exclude_ratings=filters.get("excludeRatings"),
                exclude_checkpoints=filters.get("excludeCheckpoints"),
                exclude_loras=filters.get("excludeLoras"),
                exclude_prompts=filters.get("excludePrompts"),
                exclude_colors=filters.get("excludeColors"),
                color_hues=filters.get("colorHues"),
                exclude_color_hues=filters.get("excludeColorHues"),
                collection_id=filters.get("collectionId") or filters.get("collection_id"),
                scope=filters.get("scope"),
                folder=filters.get("folder"),
                has_metadata=filters.get("hasMetadata"),
                no_caption=filters.get("noCaption"),
                aesthetic_unscored=filters.get("aestheticUnscored"),
                min_saturation=filters.get("minSaturation"),
                max_saturation=filters.get("maxSaturation"),
                seed=filters.get("seed"),
            )
        except HTTPException:
            raise
        except (TypeError, ValueError):
            raise _invalid_selection_token()

    def create_selection_token(
        self,
        *,
        chunk_size: int = SELECTION_TOKEN_DEFAULT_CHUNK,
        **filters: Any,
    ) -> Dict[str, Any]:
        """Create a stateless filtered-selection token for chunked ID retrieval."""
        contract = self._build_selection_filter_contract(**filters)
        if contract["sortBy"] == "random":
            raise HTTPException(status_code=400, detail=_svc().SELECTION_TOKEN_RANDOM_SORT_ERROR)
        normalized_chunk = max(1, min(int(chunk_size or _svc().SELECTION_TOKEN_DEFAULT_CHUNK), _svc().SELECTION_TOKEN_MAX_CHUNK))
        exact_total = not bool(contract["prompts"]) or contract["promptMatchMode"] == _svc().PROMPT_MATCH_MODE_CONTAINS
        return {
            "selection_token": self._encode_selection_token(contract),
            "total_estimate": self._selection_total_estimate(contract),
            "exact_total": exact_total,
            "chunk_size": normalized_chunk,
        }

    def count_filtered_images(self, **filters: Any) -> Dict[str, Any]:
        """Count images matching the gallery filter payload without fetching rows.

        Smart Folders v1: pinned sidebar presets poll this for live counts, so
        it reuses the exact selection filter contract (the same payload as
        selection-ids / selection-token) and the COUNT path those endpoints
        already use. ``exact`` mirrors ``create_selection_token``'s
        ``exact_total``: prompt terms in ``exact`` match mode are post-filtered
        after the SQL prefilter, so their COUNT can over-report.
        """
        contract = self._build_selection_filter_contract(**filters)
        exact = not bool(contract["prompts"]) or contract["promptMatchMode"] == _svc().PROMPT_MATCH_MODE_CONTAINS
        return {
            "count": int(self._selection_total_estimate(contract)),
            "exact": exact,
        }

    def get_selection_chunk(self, selection_token: str, *, offset: int = 0, limit: int = SELECTION_TOKEN_DEFAULT_CHUNK) -> Dict[str, Any]:
        """Resolve one ordered chunk of image IDs from a selection token."""
        contract = self._decode_selection_token(selection_token)
        if contract["sortBy"] == "random":
            raise HTTPException(status_code=400, detail=_svc().SELECTION_TOKEN_RANDOM_SORT_ERROR)
        normalized_offset = max(0, int(offset or 0))
        normalized_limit = max(1, min(int(limit or _svc().SELECTION_TOKEN_DEFAULT_CHUNK), _svc().SELECTION_TOKEN_MAX_CHUNK))
        ids = self._selection_ids_from_contract(
            contract,
            offset=normalized_offset,
            limit=normalized_limit + 1,
        )
        image_ids = ids[:normalized_limit]
        has_more = len(ids) > normalized_limit
        return {
            "image_ids": image_ids,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "next_offset": normalized_offset + len(image_ids) if has_more else None,
            "has_more": has_more,
        }

    def get_filtered_selection_ids(
        self,
        *,
        generators: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        tag_mode: str = "and",
        ratings: Optional[List[str]] = None,
        checkpoints: Optional[List[str]] = None,
        loras: Optional[List[str]] = None,
        prompts: Optional[List[str]] = None,
        prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
        artist: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "newest",
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
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
        exclude_tags: Optional[List[str]] = None,
        exclude_generators: Optional[List[str]] = None,
        exclude_ratings: Optional[List[str]] = None,
        exclude_checkpoints: Optional[List[str]] = None,
        exclude_loras: Optional[List[str]] = None,
        exclude_prompts: Optional[List[str]] = None,
        exclude_colors: Optional[List[str]] = None,
        color_hues: Optional[List[str]] = None,
        exclude_color_hues: Optional[List[str]] = None,
        collection_id: Optional[int] = None,
        folder: Optional[str] = None,
        has_metadata: Optional[bool] = None,
        # Aurora Phase 3 gallery filters
        no_caption: Optional[bool] = None,
        aesthetic_unscored: Optional[bool] = None,
        min_saturation: Optional[float] = None,
        max_saturation: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Resolve the full filtered-result ID set in current gallery sort order."""
        contract = self._build_selection_filter_contract(
            generators=generators,
            tags=tags,
            tag_mode=tag_mode,
            ratings=ratings,
            checkpoints=checkpoints,
            loras=loras,
            prompts=prompts,
            prompt_match_mode=prompt_match_mode,
            artist=artist,
            search=search,
            sort_by=sort_by,
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
            exclude_tags=exclude_tags,
            exclude_generators=exclude_generators,
            exclude_ratings=exclude_ratings,
            exclude_checkpoints=exclude_checkpoints,
            exclude_loras=exclude_loras,
            exclude_prompts=exclude_prompts,
            exclude_colors=exclude_colors,
            color_hues=color_hues,
            exclude_color_hues=exclude_color_hues,
            collection_id=collection_id,
            folder=folder,
            has_metadata=has_metadata,
            no_caption=no_caption,
            aesthetic_unscored=aesthetic_unscored,
            min_saturation=min_saturation,
            max_saturation=max_saturation,
            seed=seed,
        )
        image_ids = self._selection_ids_from_contract(
            contract,
            limit=_svc().SELECTION_IDS_MAX_RESPONSE + 1,
        )
        if len(image_ids) > _svc().SELECTION_IDS_MAX_RESPONSE:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"selection-ids is limited to {_svc().SELECTION_IDS_MAX_RESPONSE} IDs. "
                    "Use selection-token and selection-chunk for larger filtered selections."
                ),
            )
        return {
            "image_ids": image_ids,
            "total": len(image_ids),
        }

    def get_export_selection_data(
        self,
        image_ids: List[int],
        *,
        source: str = "image_ids",
        total: Optional[int] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        has_more: bool = False,
        next_offset: Optional[int] = None,
        exact_total: bool = True,
    ) -> Dict[str, Any]:
        """Return prompt and tag export data for multiple images in one request."""
        images_map = db.get_images_by_ids(image_ids)
        tags_map = db.get_image_tags_map(image_ids)

        export_images: List[Dict[str, Any]] = []
        missing_ids: List[int] = []

        for image_id in image_ids:
            image = images_map.get(image_id)
            if not image:
                missing_ids.append(image_id)
                continue

            export_images.append(
                {
                    "id": image_id,
                    "filename": image.get("filename") or "",
                    "generator": image.get("generator"),
                    "prompt": image.get("prompt") or "",
                    "negative_prompt": image.get("negative_prompt") or "",
                    "checkpoint": image.get("checkpoint"),
                    "width": image.get("width"),
                    "height": image.get("height"),
                    "aesthetic_score": image.get("aesthetic_score"),
                    "ai_caption": image.get("ai_caption") or "",
                    "generation_params": extract_generation_params(image),
                    "tags": [tag["tag"] for tag in tags_map.get(image_id, [])],
                }
            )

        normalized_limit = int(limit if limit is not None else len(image_ids))
        return {
            "images": export_images,
            "missing_ids": missing_ids,
            "count": len(export_images),
            "total": int(total if total is not None else len(image_ids)),
            "offset": max(0, int(offset or 0)),
            "limit": max(0, normalized_limit),
            "next_offset": next_offset,
            "has_more": bool(has_more),
            "source": source,
            "exact_total": bool(exact_total),
        }

    def get_export_selection_data_for_token(
        self,
        selection_token: str,
        *,
        offset: int = 0,
        limit: int = SELECTION_TOKEN_DEFAULT_CHUNK,
    ) -> Dict[str, Any]:
        """Return one export-data page from a filtered selection token."""
        contract = self._decode_selection_token(selection_token)
        if contract["sortBy"] == "random":
            raise HTTPException(status_code=400, detail=_svc().SELECTION_TOKEN_RANDOM_SORT_ERROR)

        normalized_offset = max(0, int(offset or 0))
        normalized_limit = max(1, min(int(limit or _svc().SELECTION_TOKEN_DEFAULT_CHUNK), _svc().SELECTION_TOKEN_MAX_CHUNK))
        ids = self._selection_ids_from_contract(
            contract,
            offset=normalized_offset,
            limit=normalized_limit + 1,
        )
        image_ids = ids[:normalized_limit]
        has_more = len(ids) > normalized_limit
        return self.get_export_selection_data(
            image_ids,
            source="selection_token",
            total=self._selection_total_estimate(contract),
            offset=normalized_offset,
            limit=normalized_limit,
            has_more=has_more,
            next_offset=normalized_offset + len(image_ids) if has_more else None,
            exact_total=not bool(contract["prompts"]) or contract["promptMatchMode"] == _svc().PROMPT_MATCH_MODE_CONTAINS,
        )
