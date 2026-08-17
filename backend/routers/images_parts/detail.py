"""Single-image endpoints: GET /images/{image_id} · PATCH caption (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 1072-1151 (registration
position 5 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from typing import Optional

from fastapi import Depends, Path as FastAPIPath
from pydantic import BaseModel, Field

from routers.images import get_image_service, router
from services.image_service import ImageService


@router.get(
    "/images/{image_id}",
    summary="Get a single image",
    description="Retrieve detailed information about a specific image including all associated tags.",
    responses={
        200: {
            "description": "Image details with tags",
            "content": {
                "application/json": {
                    "example": {
                        "image": {
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
                            "rating": "general"
                        },
                        "tags": [
                            {"tag": "1girl", "confidence": 0.95},
                            {"tag": "solo", "confidence": 0.92}
                        ]
                    }
                }
            }
        },
        404: {"description": "Image not found", "content": {"application/json": {"example": {"detail": "Image not found"}}}}
    }
)
async def get_image(
    image_id: int = FastAPIPath(..., ge=1, le=2_147_483_647, description="Image ID (must fit in signed 32-bit int)"),
    service: ImageService = Depends(get_image_service),
):
    """Get a single image with its associated tags."""
    return service.get_image_by_id(image_id)


class ImageCaptionPatchRequest(BaseModel):
    """PATCH body for manual caption edits (FE-3).

    Explicit-clear semantics: a field is written IFF its key is present in
    the request JSON (``model_fields_set``), so an empty-string nl_caption
    clears NL while leaving ``ai_caption`` untouched.
    """

    ai_caption: Optional[str] = Field(default=None, max_length=20000)
    nl_caption: Optional[str] = Field(default=None, max_length=20000)


@router.patch(
    "/images/{image_id}/caption",
    summary="Manually edit ai_caption / nl_caption",
    description="""
Write the composed display caption (`ai_caption`) and/or the pure
natural-language caption (`nl_caption`) for one image.

A field is written only when its key is present in the request body, so a
client can clear one caption (send an empty string) without touching the
other. Returns the stored captions after the write.
    """,
)
async def patch_image_caption(
    request: ImageCaptionPatchRequest,
    image_id: int = FastAPIPath(..., ge=1, le=2_147_483_647, description="Image ID (must fit in signed 32-bit int)"),
    service: ImageService = Depends(get_image_service),
):
    """Manually edit captions with explicit-clear semantics."""
    fields = request.model_fields_set & {"ai_caption", "nl_caption"}
    return service.patch_image_captions(
        image_id,
        ai_caption=request.ai_caption,
        nl_caption=request.nl_caption,
        set_ai_caption="ai_caption" in fields,
        set_nl_caption="nl_caption" in fields,
    )
