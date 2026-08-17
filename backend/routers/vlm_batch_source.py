"""Batch image-source builders for the VLM captioning router.

Decomposed from routers/vlm.py (2026-07): a verbatim slice of the pre-split
lines 634-638 and 646-750.
Import routers.vlm (the facade), NOT this module -- the facade re-imports
every name BY REFERENCE, so _start_claimed_caption_batch resolves
_build_batch_image_source as a facade global and monkeypatches on the
facade keep biting. The count_selection_token_ids /
iter_selection_token_id_chunks seam stays a LAZY in-function import from
services.tag_export_service (the reader net patches them THERE; neither
name may become module-level here or on the facade --
test_selection_token_helpers_live_on_tag_export_service_not_vlm).
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from fastapi import HTTPException

from routers.vlm_models import BatchCaptionRequest, _BatchImageSource


_BATCH_ID_CHUNK_SIZE = 500


def _iter_image_id_chunks(image_ids: List[int], chunk_size: int = _BATCH_ID_CHUNK_SIZE) -> Iterator[List[int]]:
    normalized_chunk_size = max(1, int(chunk_size or _BATCH_ID_CHUNK_SIZE))
    for index in range(0, len(image_ids), normalized_chunk_size):
        yield image_ids[index:index + normalized_chunk_size]


def _filters_to_selection_kwargs(filters: Dict[str, Any]) -> Dict[str, Any]:
    def pick(camel: str, snake: Optional[str] = None, default: Any = None) -> Any:
        if camel in filters:
            return filters.get(camel)
        if snake and snake in filters:
            return filters.get(snake)
        return default

    return {
        "generators": pick("generators"),
        "tags": pick("tags"),
        "tag_mode": pick("tagMode", "tag_mode", "and"),
        "ratings": pick("ratings"),
        "checkpoints": pick("checkpoints"),
        "loras": pick("loras"),
        "prompts": pick("prompts"),
        "prompt_match_mode": pick("promptMatchMode", "prompt_match_mode", "exact"),
        "artist": pick("artist"),
        "search": pick("search"),
        "sort_by": pick("sortBy", "sort_by", "newest"),
        "min_width": pick("minWidth", "min_width"),
        "max_width": pick("maxWidth", "max_width"),
        "min_height": pick("minHeight", "min_height"),
        "max_height": pick("maxHeight", "max_height"),
        "aspect_ratio": pick("aspectRatio", "aspect_ratio"),
        "min_aesthetic": pick("minAesthetic", "min_aesthetic"),
        "max_aesthetic": pick("maxAesthetic", "max_aesthetic"),
        "min_user_rating": pick("minUserRating", "min_user_rating"),
        "brightness_min": pick("brightnessMin", "brightness_min"),
        "brightness_max": pick("brightnessMax", "brightness_max"),
        "color_temperature": pick("colorTemperature", "color_temperature"),
        "brightness_distribution": pick("brightnessDistribution", "brightness_distribution"),
        "excluded_image_ids": pick("excludedImageIds", "excluded_image_ids"),
        "exclude_tags": pick("excludeTags", "exclude_tags"),
        "exclude_generators": pick("excludeGenerators", "exclude_generators"),
        "exclude_ratings": pick("excludeRatings", "exclude_ratings"),
        "exclude_checkpoints": pick("excludeCheckpoints", "exclude_checkpoints"),
        "exclude_loras": pick("excludeLoras", "exclude_loras"),
        "exclude_prompts": pick("excludePrompts", "exclude_prompts"),
        "exclude_colors": pick("excludeColors", "exclude_colors"),
        "color_hues": pick("colorHues", "color_hues"),
        "exclude_color_hues": pick("excludeColorHues", "exclude_color_hues"),
        "collection_id": pick("collectionId", "collection_id"),
        "folder": pick("folder"),
        "has_metadata": pick("hasMetadata", "has_metadata"),
    }


def _create_selection_token_from_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(filters, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")

    from services.image_service import ImageService

    return ImageService().create_selection_token(
        **_filters_to_selection_kwargs(filters),
        chunk_size=_BATCH_ID_CHUNK_SIZE,
    )


def _build_batch_image_source(request: "BatchCaptionRequest") -> _BatchImageSource:
    if request.image_ids is not None:
        image_ids = list(request.image_ids or [])
        return _BatchImageSource(
            source_type="image_ids",
            total=len(image_ids),
            iter_chunks=lambda: _iter_image_id_chunks(image_ids),
        )

    if request.selection_token:
        from services.tag_export_service import count_selection_token_ids, iter_selection_token_id_chunks

        selection_token = request.selection_token
        total = count_selection_token_ids(selection_token)
        # snapshot=True: workers persist captions AND merged VLM tags while
        # the producer is still iterating. A token filtering on tags/excludeTags
        # the batch rewrites would otherwise skip images mid-run.
        return _BatchImageSource(
            source_type="selection_token",
            total=total,
            iter_chunks=lambda: iter_selection_token_id_chunks(
                selection_token, chunk_size=_BATCH_ID_CHUNK_SIZE, snapshot=True
            ),
        )

    token_payload = _create_selection_token_from_filters(request.filters or {})
    selection_token = token_payload["selection_token"]
    total = int(token_payload.get("total_estimate") or 0)

    from services.tag_export_service import iter_selection_token_id_chunks

    return _BatchImageSource(
        source_type="filters",
        total=total,
        # snapshot=True for the same self-mutation reason as the token branch.
        iter_chunks=lambda: iter_selection_token_id_chunks(
            selection_token, chunk_size=_BATCH_ID_CHUNK_SIZE, snapshot=True
        ),
    )
