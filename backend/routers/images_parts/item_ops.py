"""Per-image operation endpoints: reparse · rating (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 1547-1605 (registration
position 8 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from routers.images import get_image_service, router
from services import entry_stats_service
from services.image_service import ImageService


@router.post(
    "/images/{image_id}/reparse",
    summary="Re-parse image metadata",
    description="""
Re-extract metadata from the image file and update the database.
Useful when the original metadata extraction failed or when the image was modified.

Supports re-parsing for:
- ComfyUI: JSON workflow in PNG text chunks
- NovelAI: JSON in Comment text chunk
- WebUI/Forge: parameters text chunk
- WebP: EXIF and XMP metadata
    """,
    responses={
        200: {"description": "Updated image data", "content": {"application/json": {"example": {"image": {}, "tags": []}}}},
        404: {"description": "Image not found", "content": {"application/json": {"example": {"detail": "Image not found"}}}},
        500: {"description": "Failed to reparse", "content": {"application/json": {"example": {"detail": "Failed to reparse metadata"}}}}
    }
)
async def reparse_image(
    image_id: int,
    service: ImageService = Depends(get_image_service),
):
    """Re-parse metadata for a single image and update the database."""
    return service.reparse_image(image_id)


class SetUserRatingRequest(BaseModel):
    """Body for POST /api/images/{image_id}/rating (v3.3.2 FF-2)."""
    stars: int = Field(..., ge=0, le=5, description="User star rating 0-5 (0 = unrated)")


@router.post(
    "/images/{image_id}/rating",
    summary="Set an image's user star rating",
    description=(
        "Set the explicit user star rating (0-5; 0 = unrated) for one image (v3.3.2). "
        "This is the Eagle-style manual rating, independent of the AI WD14 rating tags "
        "(general/sensitive/questionable/explicit)."
    ),
    responses={
        200: {"description": "Rating updated", "content": {"application/json": {"example": {"image_id": 42, "user_rating": 4, "updated": True}}}},
        404: {"description": "Image not found", "content": {"application/json": {"example": {"detail": "Image not found"}}}},
    },
)
async def set_image_user_rating(
    image_id: int,
    request: SetUserRatingRequest,
    service: ImageService = Depends(get_image_service),
):
    """Set the user star rating (0-5) for a single image."""
    try:
        result = service.set_user_rating(image_id, request.stars)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("updated"):
        raise HTTPException(status_code=404, detail="Image not found")
    entry_stats_service.record_activity(entry_stats_service.KIND_RATED, 1)
    return result
