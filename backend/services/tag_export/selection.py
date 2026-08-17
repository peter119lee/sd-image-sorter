"""Selection-token decode + filtered-ID fan-out (split from services/tag_export_service.py).

Moved verbatim. Import through
services.tag_export_service: routers/vlm lazy-imports
``count_selection_token_ids`` from the facade at call time and
tests/test_routers/test_vlm.py monkeypatches it THERE, so external callers
must keep resolving these names on the facade module object.

``EXPORT_DB_CHUNK_SIZE`` lives HERE (not in sidecars.py, where the original
split map placed it) because it is a def-time default argument of
``_iter_id_list_chunks`` / ``iter_selection_token_id_chunks`` below and
sidecars.py already imports from this module — defining it in sidecars
would create a selection<->sidecars import cycle.

Seams kept verbatim:
  * ``import database as db`` — callers patch tag_export_service.db.<fn>,
    which mutates the SHARED database module object, so the patch is
    visible here too. Never switch to ``from database import <fn>``.
  * The lazy in-function import of services.image_service (it top-level
    imports ``extract_generation_params`` back from the facade).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, List

from fastapi import HTTPException

import database as db


EXPORT_DB_CHUNK_SIZE = 500
EXPORT_FILTER_QUERY_PAGE_SIZE = 10_000
PROMPT_MATCH_MODE_EXACT = "exact"
PROMPT_MATCH_MODE_CONTAINS = "contains"


def _normalize_export_image_ids(image_ids: Iterable[Any]) -> List[int]:
    normalized_ids: List[int] = []
    seen_ids: set[int] = set()
    for raw_image_id in image_ids or []:
        try:
            image_id = int(raw_image_id)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        normalized_ids.append(image_id)
    return normalized_ids


def _iter_id_list_chunks(image_ids: Iterable[Any], chunk_size: int = EXPORT_DB_CHUNK_SIZE) -> Iterator[List[int]]:
    normalized_chunk_size = max(1, int(chunk_size or EXPORT_DB_CHUNK_SIZE))
    chunk: List[int] = []
    seen_ids: set[int] = set()
    for raw_image_id in image_ids or []:
        try:
            image_id = int(raw_image_id)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        chunk.append(image_id)
        if len(chunk) >= normalized_chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _decode_selection_token(selection_token: str) -> Dict[str, Any]:
    # Reuse ImageService's validating decoder (lazy import: image_service
    # imports this module at top level). Beyond the version/dict checks it
    # type-checks list fields and coerces numeric filters, so a malformed
    # token ({"minUserRating": "abc"}) fails here with HTTP 400 instead of a
    # ValueError-driven 500 deep inside the SQL builders.
    from services.image_service import ImageService

    filters = ImageService()._decode_selection_token(selection_token)
    if (filters.get("sortBy") or "newest") == "random":
        raise HTTPException(status_code=400, detail="random sort cannot use selection-token export")
    return filters


def iter_selection_token_id_chunks(
    selection_token: str,
    chunk_size: int = EXPORT_DB_CHUNK_SIZE,
    *,
    snapshot: bool = False,
) -> Iterator[List[int]]:
    """Yield the token's matching image IDs in chunks.

    ``snapshot=True`` materializes all matching IDs to a temp file BEFORE the
    first chunk is yielded. Callers that mutate tags/captions the token's
    filters can reference (bulk tag ops, smart-tag, VLM caption batches) MUST
    pass it, otherwise the underlying offset pagination skips images as the
    matching set shrinks between committed chunks. Read-only consumers
    (exports) can keep the default streaming behavior.
    """
    filters = _decode_selection_token(selection_token)
    id_chunks = _iter_decoded_filter_id_chunks(filters, chunk_size)
    if snapshot:
        yield from db.iter_id_snapshot_chunks(id_chunks, chunk_size=chunk_size)
    else:
        yield from id_chunks


def _iter_decoded_filter_id_chunks(filters: Dict[str, Any], chunk_size: int) -> Iterator[List[int]]:
    yield from db.iter_filtered_image_id_chunks(
        chunk_size=chunk_size,
        query_page_size=EXPORT_FILTER_QUERY_PAGE_SIZE,
        generators=filters.get("generators") or None,
        tags=filters.get("tags") or None,
        tag_mode=filters.get("tagMode") or filters.get("tag_mode") or "and",
        ratings=filters.get("ratings") or None,
        checkpoints=filters.get("checkpoints") or None,
        loras=filters.get("loras") or None,
        search_query=filters.get("search") or None,
        sort_by=filters.get("sortBy") or "newest",
        min_width=filters.get("minWidth"),
        max_width=filters.get("maxWidth"),
        min_height=filters.get("minHeight"),
        max_height=filters.get("maxHeight"),
        prompt_terms=filters.get("prompts") or None,
        prompt_match_mode=filters.get("promptMatchMode") or filters.get("prompt_match_mode") or PROMPT_MATCH_MODE_EXACT,
        aspect_ratio=filters.get("aspectRatio"),
        artist=filters.get("artist"),
        min_aesthetic=filters.get("minAesthetic"),
        max_aesthetic=filters.get("maxAesthetic"),
        min_user_rating=filters.get("minUserRating") or filters.get("min_user_rating"),
        excluded_image_ids=filters.get("excludedImageIds") or None,
        brightness_min=filters.get("brightnessMin"),
        brightness_max=filters.get("brightnessMax"),
        color_temperature=filters.get("colorTemperature"),
        brightness_distribution=filters.get("brightnessDistribution"),
        exclude_tags=filters.get("excludeTags") or None,
        exclude_generators=filters.get("excludeGenerators") or None,
        exclude_ratings=filters.get("excludeRatings") or None,
        exclude_checkpoints=filters.get("excludeCheckpoints") or None,
        exclude_loras=filters.get("excludeLoras") or None,
        exclude_prompts=filters.get("excludePrompts") or None,
        exclude_colors=filters.get("excludeColors") or None,
        color_hues=filters.get("colorHues") or None,
        exclude_color_hues=filters.get("excludeColorHues") or None,
        collection_id=filters.get("collectionId") or filters.get("collection_id"),
        folder=filters.get("folder"),
        has_metadata=filters.get("hasMetadata"),
    )


def count_selection_token_ids(selection_token: str) -> int:
    filters = _decode_selection_token(selection_token)
    return db.get_filtered_image_count(
        generators=filters.get("generators") or None,
        tags=filters.get("tags") or None,
        tag_mode=filters.get("tagMode") or filters.get("tag_mode") or "and",
        ratings=filters.get("ratings") or None,
        checkpoints=filters.get("checkpoints") or None,
        loras=filters.get("loras") or None,
        search_query=filters.get("search") or None,
        min_width=filters.get("minWidth"),
        max_width=filters.get("maxWidth"),
        min_height=filters.get("minHeight"),
        max_height=filters.get("maxHeight"),
        prompt_terms=filters.get("prompts") or None,
        prompt_match_mode=filters.get("promptMatchMode") or filters.get("prompt_match_mode") or PROMPT_MATCH_MODE_EXACT,
        aspect_ratio=filters.get("aspectRatio"),
        artist=filters.get("artist"),
        min_aesthetic=filters.get("minAesthetic"),
        max_aesthetic=filters.get("maxAesthetic"),
        min_user_rating=filters.get("minUserRating") or filters.get("min_user_rating"),
        excluded_image_ids=filters.get("excludedImageIds") or None,
        brightness_min=filters.get("brightnessMin"),
        brightness_max=filters.get("brightnessMax"),
        color_temperature=filters.get("colorTemperature"),
        brightness_distribution=filters.get("brightnessDistribution"),
        exclude_tags=filters.get("excludeTags") or None,
        exclude_generators=filters.get("excludeGenerators") or None,
        exclude_ratings=filters.get("excludeRatings") or None,
        exclude_checkpoints=filters.get("excludeCheckpoints") or None,
        exclude_loras=filters.get("excludeLoras") or None,
        exclude_prompts=filters.get("excludePrompts") or None,
        exclude_colors=filters.get("excludeColors") or None,
        color_hues=filters.get("colorHues") or None,
        exclude_color_hues=filters.get("excludeColorHues") or None,
        collection_id=filters.get("collectionId") or filters.get("collection_id"),
        folder=filters.get("folder"),
        has_metadata=filters.get("hasMetadata"),
    )
