"""Gallery listing endpoints: GET /images · /folders · /library-roots (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 287-719 (registration
position 1 of 9).
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
    "/images",
    summary="Get images with optional filters",
    description="""
Retrieve a list of images from the database with comprehensive filtering options.
Uses cursor-based pagination for efficient large dataset handling.

All filter parameters support comma-separated values. Tag filters use AND logic
(all tags must be present). Generator/rating filters use OR logic.

**Example requests:**
- `GET /api/images?generators=comfyui,nai&limit=100` - Get ComfyUI and NAI images
- `GET /api/images?tags=1girl,solo&ratings=general,sensitive` - Get safe solo girl images
- `GET /api/images?search=landscape&sort_by=random` - Random images with 'landscape' in prompt
- `GET /api/images?min_width=1920&aspect_ratio=landscape` - High-res landscape images

**Pagination:**
Use the `cursor` parameter with the `next_cursor` value from the previous response to get the next page.
Treat `next_cursor` as an opaque token and pass it back unchanged.
    """,
    responses={
        200: {
            "description": "List of images matching filters",
            "content": {
                "application/json": {
                    "example": {
                        "images": [
                            {
                                "id": 1,
                                "filename": "image_001.png",
                                "path": "/path/to/image_001.png",
                                "generator": "comfyui",
                                "prompt": "1girl, solo, masterpiece",
                                "negative_prompt": "lowres, bad anatomy",
                                "checkpoint": "sd_xl_base_1.0.safetensors",
                                "checkpoint_normalized": "sd_xl_base_1.0",
                                "width": 1024,
                                "height": 1536,
                                "rating": "general",
                                "library_order_time": "2024-01-15T10:30:00Z",
                                "source_file_mtime": "2024-02-01T08:45:12Z",
                                "created_at": "2024-01-15T10:30:00Z"
                            }
                        ],
                        "next_cursor": "eyJpZCI6MSwic29ydF92YWx1ZSI6IjIwMjQtMDEtMTVUMTA6MzA6MDBaIiwidiI6MX0",
                        "has_more": True,
                        "total": 500
                    }
                }
            }
        },
        400: {
            "description": "Invalid filter parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid sort_by value. Must be one of: newest, oldest, ..."}
                }
            }
        }
    }
)
async def get_images(
    generators: Optional[str] = Query(
        default=None,
        description="Comma-separated list of generators to filter. Options: comfyui, nai, webui, forge",
        examples=["comfyui,nai"],
    ),
    generator: Optional[str] = Query(
        default=None,
        description=(
            "Singular alias for ``generators``. v3.2.2+ accepts both because users "
            "(and the OpenAPI examples) reach for the natural singular form first; "
            "previously ``?generator=nai`` was silently ignored and returned the "
            "entire unfiltered library."
        ),
        examples=["nai"],
        deprecated=True,
    ),
    tags: Optional[str] = Query(
        default=None,
        description="Comma-separated list of tags (AND logic by default - all tags must be present)",
        examples=["1girl,solo,long_hair"],
    ),
    tag: Optional[str] = Query(
        default=None,
        description="Singular alias for ``tags`` (v3.2.2+).",
        examples=["1girl"],
        deprecated=True,
    ),
    tag_mode: str = Query(
        default="and",
        description="Tag matching mode: 'and' (image must have ALL tags) or 'or' (image must have ANY tag)",
        pattern="^(and|or)$",
        examples=["and"],
    ),
    ratings: Optional[str] = Query(
        default=None,
        description="Comma-separated content ratings. Options: general, sensitive, questionable, explicit",
        examples=["general,sensitive"],
    ),
    rating: Optional[str] = Query(
        default=None,
        description="Singular alias for ``ratings`` (v3.2.2+).",
        examples=["general"],
        deprecated=True,
    ),
    checkpoints: Optional[str] = Query(
        default=None,
        description="Comma-separated checkpoint/model names",
        examples=["sd_xl_base_1.0,animagine_xl"],
    ),
    checkpoint: Optional[str] = Query(
        default=None,
        description="Singular alias for ``checkpoints`` (v3.2.2+).",
        examples=["animagine_xl"],
        deprecated=True,
    ),
    loras: Optional[str] = Query(
        default=None,
        description="Comma-separated LoRA names",
        examples=["detail_tweaker,add_detail"],
    ),
    lora: Optional[str] = Query(
        default=None,
        description="Singular alias for ``loras`` (v3.2.2+).",
        examples=["detail_tweaker"],
        deprecated=True,
    ),
    search: Optional[str] = Query(
        default=None,
        max_length=1000,
        description="Free-text search in image prompts",
        examples=["landscape"],
    ),
    artist: Optional[str] = Query(
        default=None,
        max_length=500,
        description="Filter by artist name",
        examples=["greg_rutkowski"],
    ),
    sort_by: str = Query(
        default="newest",
        description="Sort order: newest, oldest, name_asc, name_desc, generator, generator_desc, prompt_length, prompt_length_asc, tag_count, tag_count_asc, rating, rating_desc, character_count, character_count_asc, random, file_size, file_size_asc, aesthetic, aesthetic_asc, user_rating, user_rating_asc",
        examples=["newest"],
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Number of images to return (1-1000)",
        examples=[100],
    ),
    cursor: Optional[str] = Query(
        default=None,
        description="Opaque cursor from the previous page's next_cursor value. Pass it back unchanged.",
        examples=["eyJpZCI6NDIsInNvcnRfdmFsdWUiOiIyMDI0LTAxLTE1VDEwOjMwOjAwWiIsInYiOjF9"],
    ),
    offset: Optional[int] = Query(
        default=None,
        ge=0,
        le=100_000_000,
        description=(
            "Offset for fallback pagination when the selected sort does not support cursor pagination. "
            "Must be non-negative; large offsets are slow at library scale (prefer cursor pagination)."
        ),
        examples=[200],
    ),
    min_width: Optional[int] = Query(
        default=None,
        ge=1,
        le=100000,
        description="Minimum image width in pixels",
        examples=[1024],
    ),
    max_width: Optional[int] = Query(
        default=None,
        ge=1,
        le=100000,
        description="Maximum image width in pixels",
        examples=[2048],
    ),
    min_height: Optional[int] = Query(
        default=None,
        ge=1,
        le=100000,
        description="Minimum image height in pixels",
        examples=[1024],
    ),
    max_height: Optional[int] = Query(
        default=None,
        ge=1,
        le=100000,
        description="Maximum image height in pixels",
        examples=[2048],
    ),
    prompts: Optional[str] = Query(
        default=None,
        max_length=1000,
        description="Comma-separated prompt terms (AND logic)",
        examples=["masterpiece,best_quality"],
    ),
    prompt_match_mode: str = Query(
        default=PROMPT_MATCH_MODE_EXACT,
        description="Prompt term matching mode: exact token matching or contains substring matching",
        pattern="^(exact|contains)$",
        examples=["exact"],
    ),
    aspect_ratio: Optional[str] = Query(
        default=None,
        description="Filter by aspect ratio: square, landscape, portrait",
        examples=["landscape"],
    ),
    date_from: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Only images whose file time is on/after this day (YYYY-MM-DD, inclusive).",
    ),
    date_to: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Only images whose file time is on/before this day (YYYY-MM-DD, inclusive).",
    ),
    min_aesthetic: Optional[float] = Query(
        default=None,
        ge=0,
        le=10,
        description="Minimum aesthetic score (0-10)",
    ),
    max_aesthetic: Optional[float] = Query(
        default=None,
        ge=0,
        le=10,
        description="Maximum aesthetic score (0-10)",
    ),
    min_user_rating: Optional[int] = Query(
        default=None,
        ge=0,
        le=5,
        description="Minimum user star rating 0-5 (the gallery ★≥N filter); 0 or None shows all (v3.3.2).",
    ),
    # v3.2.1 color filters
    brightness_min: Optional[float] = Query(
        default=None,
        ge=0,
        le=255,
        description="Minimum average brightness (0-255). Set after running /api/colors/analyze.",
    ),
    brightness_max: Optional[float] = Query(
        default=None,
        ge=0,
        le=255,
        description="Maximum average brightness (0-255).",
    ),
    color_temperature: Optional[str] = Query(
        default=None,
        description="Filter by color temperature: warm | cool | neutral",
    ),
    brightness_distribution: Optional[str] = Query(
        default=None,
        description="Filter by brightness distribution shape: left_heavy | right_heavy | middle_heavy | edge_heavy | balanced",
    ),
    # v3.2.2 per-item exclude filters
    exclude_tags: Optional[str] = Query(
        default=None,
        description="Comma-separated tags to exclude (images with ANY of these tags are hidden)",
    ),
    exclude_generators: Optional[str] = Query(
        default=None,
        description="Comma-separated generators to exclude",
    ),
    exclude_ratings: Optional[str] = Query(
        default=None,
        description="Comma-separated ratings to exclude",
    ),
    exclude_checkpoints: Optional[str] = Query(
        default=None,
        description="Comma-separated checkpoints to exclude",
    ),
    exclude_loras: Optional[str] = Query(
        default=None,
        description="Comma-separated LoRAs to exclude",
    ),
    exclude_prompts: Optional[str] = Query(
        default=None,
        description="Comma-separated prompt terms to exclude (v3.3.0)",
    ),
    exclude_colors: Optional[str] = Query(
        default=None,
        description="Comma-separated color temperatures to exclude: warm/cool/neutral (v3.3.0)",
    ),
    color_hues: Optional[str] = Query(
        default=None,
        description="Comma-separated dominant hues to require (ANY match): red/orange/yellow/green/cyan/blue/purple/pink/brown/white/black/gray (v3.5.0)",
    ),
    exclude_color_hues: Optional[str] = Query(
        default=None,
        description="Comma-separated dominant hues to exclude (v3.5.0)",
    ),
    collection_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="Restrict results to images in this collection (v3.3.1). Composes with all other filters.",
    ),
    scope: Optional[str] = Query(
        default=None,
        pattern="^(current_session|library)$",
        description="Gallery scope: current_session or library.",
    ),
    folder: Optional[str] = Query(
        default=None,
        max_length=4096,
        description="Restrict results to images whose indexed path is within this folder subtree (recursive). v3.3.2 Library Navigation.",
    ),
    has_metadata: Optional[bool] = Query(
        default=None,
        description="Restrict to images that carry SD generation parameters (true) or carry none (false). Omit for all. v3.3.2 small-opt.",
    ),
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = Query(
        default=None,
        description="When true, only images with neither an AI caption nor an NL caption (both empty). Aurora Phase 3.",
    ),
    aesthetic_unscored: Optional[bool] = Query(
        default=None,
        description="When true, only images with no aesthetic score. Takes precedence over min/max_aesthetic. Aurora Phase 3.",
    ),
    min_saturation: Optional[float] = Query(
        default=None,
        ge=0,
        le=255,
        description="Minimum color saturation (0-255). Requires color analysis. Aurora Phase 3.",
    ),
    max_saturation: Optional[float] = Query(
        default=None,
        ge=0,
        le=255,
        description="Maximum color saturation (0-255). Requires color analysis. Aurora Phase 3.",
    ),
    seed: Optional[int] = Query(
        default=None,
        description="Match images generated with this exact seed (read from metadata_json). Aurora Phase 3.",
    ),
    service: ImageService = Depends(get_image_service),
):
    """Retrieve images with optional filtering using cursor-based pagination."""
    # v3.2.2: accept singular forms (``generator``, ``tag``, ``rating``,
    # ``checkpoint``, ``lora``) as aliases for the plural query params so
    # ``?generator=nai`` etc. no longer silently return the entire library.
    # Combine plural + singular, dedupe, comma-join.
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

    generators = _merge(generators, generator)
    tags = _merge(tags, tag)
    ratings = _merge(ratings, rating)
    checkpoints = _merge(checkpoints, checkpoint)
    loras = _merge(loras, lora)

    return service.get_images(
        generators=generators,
        tags=tags,
        tag_mode=tag_mode,
        ratings=ratings,
        checkpoints=checkpoints,
        loras=loras,
        search=search,
        artist=artist,
        sort_by=sort_by,
        limit=limit,
        cursor=cursor,
        offset=offset,
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


@router.get(
    "/folders",
    summary="List image directories for the gallery folder tree",
    description="v3.3.2 Library Navigation: distinct directories that contain images, forward-slash normalized and sorted. The frontend builds a nested tree by splitting on '/'.",
)
async def list_library_folders(
    service: ImageService = Depends(get_image_service),
):
    """Return distinct image directories for the gallery folder tree."""
    return service.get_library_folders()


@router.get(
    "/library-roots",
    summary="List registered library roots",
    description="v3.3.2 Library Navigation: folders the user added as image sources, each with a live indexed-image count. Roots are auto-registered when a folder is scanned and persist independently of the images currently under them.",
)
async def list_library_roots(
    service: ImageService = Depends(get_image_service),
):
    """Return registered library roots with per-root image counts."""
    return service.get_library_roots()
