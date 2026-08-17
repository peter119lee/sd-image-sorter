"""Selection-token endpoints: selection-token · selection-chunk (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 722-801 (registration
position 2 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from fastapi import Depends, Query

from routers.images import (
    SelectionChunkResponse,
    SelectionTokenRequest,
    SelectionTokenResponse,
    get_image_service,
    router,
)
from services.image_service import ImageService


@router.post(
    "/images/selection-token",
    response_model=SelectionTokenResponse,
    summary="Create a filtered selection token",
    description="""
Create a stateless token for the current gallery filter payload.

Newer clients use this before fetching `/api/images/selection-chunk` pages so
large filtered selections do not require one giant ID response. `total_estimate`
is exact for indexed filters and marked as an estimate when prompt post-filtering
may still remove SQL false positives.
    """,
)
async def create_selection_token(
    request: SelectionTokenRequest,
    service: ImageService = Depends(get_image_service),
):
    """Create a chunkable filtered-selection token."""
    return service.create_selection_token(
        generators=request.generators,
        tags=request.tags,
        tag_mode=request.tagMode,
        ratings=request.ratings,
        checkpoints=request.checkpoints,
        loras=request.loras,
        prompts=request.prompts,
        prompt_match_mode=request.promptMatchMode,
        artist=request.artist,
        search=request.search,
        sort_by=request.sortBy,
        min_width=request.minWidth,
        max_width=request.maxWidth,
        min_height=request.minHeight,
        max_height=request.maxHeight,
        aspect_ratio=request.aspectRatio,
        min_aesthetic=request.minAesthetic,
        max_aesthetic=request.maxAesthetic,
        date_from=request.dateFrom,
        date_to=request.dateTo,
        min_user_rating=request.minUserRating,
        brightness_min=request.brightnessMin,
        brightness_max=request.brightnessMax,
        color_temperature=request.colorTemperature,
        brightness_distribution=request.brightnessDistribution,
        excluded_image_ids=request.excludedImageIds,
        exclude_tags=request.excludeTags,
        exclude_generators=request.excludeGenerators,
        exclude_ratings=request.excludeRatings,
        exclude_checkpoints=request.excludeCheckpoints,
        exclude_loras=request.excludeLoras,
        exclude_prompts=request.excludePrompts,
        exclude_colors=request.excludeColors,
        color_hues=request.colorHues,
        exclude_color_hues=request.excludeColorHues,
        collection_id=request.collectionId,
        scope=request.scope,
        folder=request.folder,
        has_metadata=request.hasMetadata,
        no_caption=request.noCaption,
        aesthetic_unscored=request.aestheticUnscored,
        min_saturation=request.minSaturation,
        max_saturation=request.maxSaturation,
        seed=request.seed,
        chunk_size=request.chunkSize,
    )


@router.get(
    "/images/selection-chunk",
    response_model=SelectionChunkResponse,
    summary="Fetch one filtered selection ID chunk",
    description="Fetch one ordered image-ID chunk from a token created by `/api/images/selection-token`.",
)
async def get_selection_chunk(
    selection_token: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=10000),
    service: ImageService = Depends(get_image_service),
):
    """Return one chunk of filtered-result image IDs."""
    return service.get_selection_chunk(selection_token, offset=offset, limit=limit)
