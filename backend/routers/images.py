"""
Image endpoints for SD Image Sorter.
Handles image retrieval, filtering, and file serving.
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

import database as db

router = APIRouter(prefix="/api", tags=["images"])


@router.get("/images")
async def get_images(
    generators: Optional[str] = None,
    tags: Optional[str] = None,
    ratings: Optional[str] = None,
    checkpoints: Optional[str] = None,
    loras: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query(default="newest", description="Sort by: newest, oldest, name_asc, name_desc, generator, prompt_length, tag_count, rating, character_count, random, file_size"),
    limit: int = Query(default=0, description="0 = no limit, returns all images"),
    offset: int = 0,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    prompts: Optional[str] = None,  # Comma-separated prompt terms (AND logic)
    aspect_ratio: Optional[str] = None  # 'square', 'landscape', 'portrait'
):
    """
    Get images with optional filters.
    - generators: Comma-separated list of generators (comfyui, nai, webui, forge)
    - tags: Comma-separated list of tags (AND logic)
    - ratings: Comma-separated ratings (general, sensitive, questionable, explicit)
    - search: Search in prompts
    - sort_by: Sorting method
    - limit: 0 for all images
    - min_width, max_width, min_height, max_height: Dimension filters
    - aspect_ratio: 'square', 'landscape', or 'portrait'
    """
    gen_list = generators.split(",") if generators else None
    tag_list = tags.split(",") if tags else None
    rating_list = ratings.split(",") if ratings else None
    cp_list = checkpoints.split(",") if checkpoints else None
    lr_list = loras.split(",") if loras else None
    prompt_list = prompts.split(",") if prompts else None
    
    # Use very high limit when 0 (all images)
    actual_limit = limit if limit > 0 else 999999
    
    images = db.get_images(
        generators=gen_list,
        tags=tag_list,
        ratings=rating_list,
        checkpoints=cp_list,
        loras=lr_list,
        search_query=search,
        prompt_terms=prompt_list,
        sort_by=sort_by,
        limit=actual_limit,
        offset=offset,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        aspect_ratio=aspect_ratio
    )
    
    return {"images": images, "count": len(images)}


@router.get("/images/{image_id}")
async def get_image(image_id: int):
    """Get a single image with its tags."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    tags = db.get_image_tags(image_id)
    return {"image": image, "tags": tags}


@router.get("/image-file/{image_id}")
async def get_image_file(image_id: int):
    """Serve the actual image file."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    return FileResponse(image["path"])


@router.get("/image-thumbnail/{image_id}")
async def get_image_thumbnail(image_id: int, size: int = 256):
    """Get a thumbnail of the image (returns full image for now, frontend can resize)."""
    image = db.get_image_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    return FileResponse(image["path"])
