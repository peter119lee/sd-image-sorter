"""Selection payload endpoints: export-data · selection-ids · POST count (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 1154-1306 (registration
position 6 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from fastapi import Depends

from routers.images import (
    ExportSelectionRequest,
    ExportSelectionResponse,
    FilteredImageCountResponse,
    SelectionIdsRequest,
    SelectionIdsResponse,
    get_image_service,
    router,
)
from services.image_service import ImageService


@router.post(
    "/images/export-data",
    response_model=ExportSelectionResponse,
    summary="Get prompt and tag export data for selected images",
    description="""
Return prompt text and tags for a selected image batch.

Legacy clients may pass explicit `image_ids`. Newer large-selection clients may
pass `selection_token`, `offset`, and `limit` to page export preview data without
sending a giant ID payload. Missing explicit IDs are reported in `missing_ids`
instead of failing the whole export.
    """,
)
async def export_selection_data(
    request: ExportSelectionRequest,
    service: ImageService = Depends(get_image_service),
):
    """Get export-ready prompt and tag data for selected images or a token chunk."""
    if request.selection_token:
        return service.get_export_selection_data_for_token(
            request.selection_token,
            offset=request.offset,
            limit=request.limit,
        )
    return service.get_export_selection_data(request.image_ids or [])


@router.post(
    "/images/selection-ids",
    response_model=SelectionIdsResponse,
    summary="Resolve all image IDs for the current filtered result set",
    description="""
Return the full ordered ID set for the current gallery filter payload.

This is used for truthful filtered-result selection. Unlike visible or loaded
selection, this endpoint resolves the full matching result set in backend sort
order, not just the thumbnails currently mounted in the DOM.
    """,
)
async def get_selection_ids(
    request: SelectionIdsRequest,
    service: ImageService = Depends(get_image_service),
):
    """Return the full filtered-result ID set for selection flows."""
    return service.get_filtered_selection_ids(
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
        folder=request.folder,
        has_metadata=request.hasMetadata,
        no_caption=request.noCaption,
        aesthetic_unscored=request.aestheticUnscored,
        min_saturation=request.minSaturation,
        max_saturation=request.maxSaturation,
        seed=request.seed,
    )


@router.post(
    "/images/count",
    response_model=FilteredImageCountResponse,
    summary="Count images matching a gallery filter payload",
    description="""
Count the images matching a gallery filter payload without returning rows or IDs.

Smart Folders v1: the gallery sidebar pins saved filter presets and shows a live
image count per pin. The request body is the same filter payload as
`/api/images/selection-ids`. `exact=false` mirrors the selection-token
`exact_total` semantics: prompt terms in `exact` match mode are post-filtered
after SQL, so the count can over-report for those payloads.
    """,
)
async def count_filtered_images(
    request: SelectionIdsRequest,
    service: ImageService = Depends(get_image_service),
):
    """Return the number of images matching the gallery filter payload."""
    return service.count_filtered_images(
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
        folder=request.folder,
        has_metadata=request.hasMetadata,
        no_caption=request.noCaption,
        aesthetic_unscored=request.aestheticUnscored,
        min_saturation=request.minSaturation,
        max_saturation=request.maxSaturation,
        seed=request.seed,
    )
