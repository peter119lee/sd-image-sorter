"""Query-param count endpoint: GET /images/count (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 940-1069 (registration
position 4 (static, must precede {image_id}) of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from typing import Optional

from fastapi import Depends, Query

from routers.images import PROMPT_MATCH_MODE_EXACT, get_image_service, router
from services.image_service import ImageService


@router.get(
    "/images/count",
    summary="Count images matching filters",
    description="""
Return the exact number of images matching the same filter parameters as
`GET /api/images`. Powers the live "Apply · ~N images" filter preview
(Aurora Phase 3).

Unlike the `total` field on `GET /api/images` — which returns a `-1` skip
sentinel on the cursor path for very large libraries — this endpoint always
runs the count query and returns a real total. Sort order and pagination
parameters are irrelevant to a count and are not accepted.

Declared above `GET /api/images/{image_id}` so the dynamic-id route does not
shadow it.
    """,
    responses={
        200: {
            "description": "Exact match count",
            "content": {"application/json": {"example": {"total": 4213}}},
        }
    },
)
async def count_images(
    generators: Optional[str] = Query(default=None, description="Comma-separated generators to filter."),
    generator: Optional[str] = Query(default=None, description="Singular alias for generators.", deprecated=True),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags (AND by default)."),
    tag: Optional[str] = Query(default=None, description="Singular alias for tags.", deprecated=True),
    tag_mode: str = Query(default="and", pattern="^(and|or)$", description="Tag matching mode."),
    ratings: Optional[str] = Query(default=None, description="Comma-separated ratings."),
    rating: Optional[str] = Query(default=None, description="Singular alias for ratings.", deprecated=True),
    checkpoints: Optional[str] = Query(default=None, description="Comma-separated checkpoints."),
    checkpoint: Optional[str] = Query(default=None, description="Singular alias for checkpoints.", deprecated=True),
    loras: Optional[str] = Query(default=None, description="Comma-separated LoRAs."),
    lora: Optional[str] = Query(default=None, description="Singular alias for loras.", deprecated=True),
    search: Optional[str] = Query(default=None, max_length=1000, description="Free-text search in prompts."),
    artist: Optional[str] = Query(default=None, max_length=500, description="Filter by artist name."),
    min_width: Optional[int] = Query(default=None, ge=1, le=100000),
    max_width: Optional[int] = Query(default=None, ge=1, le=100000),
    min_height: Optional[int] = Query(default=None, ge=1, le=100000),
    max_height: Optional[int] = Query(default=None, ge=1, le=100000),
    prompts: Optional[str] = Query(default=None, max_length=1000, description="Comma-separated prompt terms (AND)."),
    prompt_match_mode: str = Query(default=PROMPT_MATCH_MODE_EXACT, pattern="^(exact|contains)$"),
    aspect_ratio: Optional[str] = Query(default=None, description="square, landscape, or portrait."),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    min_aesthetic: Optional[float] = Query(default=None, ge=0, le=10),
    max_aesthetic: Optional[float] = Query(default=None, ge=0, le=10),
    min_user_rating: Optional[int] = Query(default=None, ge=0, le=5),
    brightness_min: Optional[float] = Query(default=None, ge=0, le=255),
    brightness_max: Optional[float] = Query(default=None, ge=0, le=255),
    color_temperature: Optional[str] = Query(default=None, description="warm | cool | neutral."),
    brightness_distribution: Optional[str] = Query(default=None),
    exclude_tags: Optional[str] = Query(default=None, description="Comma-separated tags to exclude."),
    exclude_generators: Optional[str] = Query(default=None),
    exclude_ratings: Optional[str] = Query(default=None),
    exclude_checkpoints: Optional[str] = Query(default=None),
    exclude_loras: Optional[str] = Query(default=None),
    exclude_prompts: Optional[str] = Query(default=None),
    exclude_colors: Optional[str] = Query(default=None),
    color_hues: Optional[str] = Query(default=None),
    exclude_color_hues: Optional[str] = Query(default=None),
    collection_id: Optional[int] = Query(default=None, ge=1),
    scope: Optional[str] = Query(default=None, pattern="^(current_session|library)$"),
    folder: Optional[str] = Query(default=None, max_length=4096),
    has_metadata: Optional[bool] = Query(default=None),
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = Query(default=None, description="Only images with no AI and no NL caption."),
    aesthetic_unscored: Optional[bool] = Query(default=None, description="Only images with no aesthetic score. Takes precedence over min/max_aesthetic."),
    min_saturation: Optional[float] = Query(default=None, ge=0, le=255),
    max_saturation: Optional[float] = Query(default=None, ge=0, le=255),
    seed: Optional[int] = Query(default=None, description="Match images generated with this exact seed."),
    service: ImageService = Depends(get_image_service),
):
    """Return the exact number of images matching the given filters."""
    # Accept singular aliases exactly as GET /api/images does.
    def _merge(plural: Optional[str], singular: Optional[str]) -> Optional[str]:
        if not singular:
            return plural
        combined = (plural + "," + singular) if plural else singular
        seen, parts = set(), []
        for tok in combined.split(","):
            t = tok.strip()
            if t and t not in seen:
                seen.add(t)
                parts.append(t)
        return ",".join(parts) if parts else None

    return service.get_image_count(
        generators=_merge(generators, generator),
        tags=_merge(tags, tag),
        tag_mode=tag_mode,
        ratings=_merge(ratings, rating),
        checkpoints=_merge(checkpoints, checkpoint),
        loras=_merge(loras, lora),
        search=search,
        artist=artist,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        prompts=prompts,
        prompt_match_mode=prompt_match_mode,
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
        scope=scope,
        folder=folder,
        has_metadata=has_metadata,
        no_caption=no_caption,
        aesthetic_unscored=aesthetic_unscored,
        min_saturation=min_saturation,
        max_saturation=max_saturation,
        seed=seed,
    )
