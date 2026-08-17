"""File-serving + misc endpoints: save-edited · image-file · image-thumbnail · image-preview-by-path · thumbnail-cache/* · open-folder · parse-image (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 1608-1863 (registration
position 9 of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
# ``sys`` / ``subprocess`` are read as module attributes by open_folder so the
# reader test's patches on the shared module singletons
# (images_router.sys.platform / images_router.subprocess.Popen) keep landing.
import subprocess
import sys

from fastapi import Depends, HTTPException, Query, UploadFile, File

# parse_uploaded_image reads the four upload constants through the facade
# module object AT CALL TIME (not by value at import) so
# monkeypatch.setattr(images_router, "PARSE_IMAGE_UPLOAD_MAX_BYTES", ...)
# in tests/test_routers/test_images.py keeps biting after the split.
import routers.images as _images_facade
from routers.images import (
    OpenFolderRequest,
    SaveEditedMetadataRequest,
    SaveEditedMetadataResponse,
    get_image_service,
    router,
)
from services.image_service import ImageService
from utils.path_validation import PathValidationError


@router.post(
    "/image-metadata/save-edited",
    response_model=SaveEditedMetadataResponse,
    summary="Save an image copy with edited metadata",
    description="""
Save a copy of an image to a caller-selected path after editing common SD metadata fields.

This endpoint is used by the Single Image Reader metadata editor. It defaults to
save-as-new behavior and returns format-specific warnings where metadata support
is limited (notably JPEG / some WebP viewers).
    """,
    responses={
        200: {
            "description": "Edited image saved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "output_path": "/path/to/image.metadata-edited.png",
                        "format": "png",
                        "warnings": [],
                    }
                }
            }
        },
        400: {"description": "Invalid path, format, or metadata payload"},
        409: {"description": "Output path already exists and overwrite was not confirmed"},
    },
)
async def save_edited_image_metadata(
    request: SaveEditedMetadataRequest,
    service: ImageService = Depends(get_image_service),
):
    """Save a new image with edited metadata."""
    try:
        return service.save_image_with_edited_metadata(
            source_path=request.source_path,
            output_path=request.output_path,
            image_format=request.format,
            metadata=request.metadata,
            allow_overwrite=request.allow_overwrite,
            quality=request.quality,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PathValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        # v3.2.2: previously bubbled up as 500 "UnhandledException" when
        # users tried to save into a system-protected directory like
        # C:\Windows\System32\. That looks like a server crash; return a
        # 403 with the OS-provided reason instead.
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied writing to output path: {exc}",
        ) from exc
    except OSError as exc:
        # Catch the OS-level errors (read-only path, ENOSPC, ENOENT on
        # the parent directory, network drive timeout) and surface them
        # as 400 with the underlying message rather than a generic 500.
        raise HTTPException(
            status_code=400,
            detail=f"Cannot write to output path: {exc}",
        ) from exc


@router.get(
    "/image-file/{image_id}",
    summary="Get image file",
    description="Serve the actual image file for display or download.",
    responses={
        200: {"description": "Image file binary data"},
        404: {"description": "Image not found or file missing", "content": {"application/json": {"example": {"detail": "Image not found"}}}}
    }
)
async def get_image_file(
    image_id: int,
    service: ImageService = Depends(get_image_service),
):
    """Serve the actual image file."""
    return service.get_image_file(image_id)


@router.get(
    "/image-thumbnail/{image_id}",
    summary="Get image thumbnail",
    description="""
Get a cached thumbnail of the image.

Thumbnails are cached in backend/thumbnails/ using WebP format for optimal
compression. Cache invalidation is based on source file modification time.

Supported cache sizes: 256, 384, 512 (requested sizes are normalized to nearest).
Custom sizes between 1-4096 are generated on-demand but not cached.
    """,
    responses={
        200: {
            "description": "Thumbnail image (WebP format)",
            "headers": {
                "Cache-Control": {"description": "Cache duration", "example": "public, max-age=86400"},
                "X-Thumbnail-Cache": {"description": "Cache status", "example": "HIT"}
            }
        },
        404: {"description": "Image not found", "content": {"application/json": {"example": {"detail": "Image not found"}}}},
        500: {"description": "Failed to generate thumbnail", "content": {"application/json": {"example": {"detail": "Failed to generate thumbnail"}}}}
    }
)
async def get_image_thumbnail(
    image_id: int,
    size: int = Query(default=256, ge=1, le=4096, description="Thumbnail max dimension in pixels (1-4096)"),
    service: ImageService = Depends(get_image_service),
):
    """Get a thumbnail of the image with persistent disk caching."""
    return await service.get_image_thumbnail(image_id, size)


@router.get(
    "/image-preview-by-path",
    summary="Get a thumbnail for a file by absolute path",
    description="""
Roadmap-C missing-file repair. Serve a WebP thumbnail for a found-but-unlinked
image file addressed by absolute path (the id-based thumbnail endpoint can't
reach a file that is not yet an indexed image).

The path is validated before any read: directory traversal (`..`) is rejected,
the file must exist, and it must be an allowed image type. Size is clamped to
1..1024. Returns 404 JSON for an invalid, missing, or non-image path.
    """,
    responses={
        200: {"description": "Thumbnail image (WebP)"},
        404: {"description": "Invalid, missing, or non-image path",
              "content": {"application/json": {"example": {"detail": "File does not exist"}}}},
    },
)
async def get_image_preview_by_path(
    path: str = Query(..., min_length=1, max_length=4096, description="Absolute path to the image file."),
    size: int = Query(default=256, ge=1, le=1024, description="Thumbnail max dimension in pixels (1-1024)."),
    service: ImageService = Depends(get_image_service),
):
    """Serve a WebP thumbnail for a validated file path (repair-review preview)."""
    return await service.get_image_preview_by_path(path, size)


@router.get("/thumbnail-cache/stats")
async def get_thumbnail_cache_statistics(
    service: ImageService = Depends(get_image_service),
):
    """Get thumbnail cache statistics."""
    return service.get_thumbnail_cache_stats()


@router.post("/thumbnail-cache/clear")
async def clear_thumbnail_cache(
    service: ImageService = Depends(get_image_service),
):
    """Clear all cached thumbnails."""
    return service.clear_thumbnail_cache()


@router.post("/thumbnail-cache/cleanup")
async def cleanup_thumbnail_cache(
    max_age_days: int = Query(default=30, ge=1, le=365),
    service: ImageService = Depends(get_image_service),
):
    """Remove cached thumbnails older than max_age_days."""
    return service.cleanup_thumbnail_cache(max_age_days)


@router.post(
    "/open-folder",
    summary="Open image in file explorer",
    description="""
Open the containing folder of an image in the OS file explorer, with the file selected.

Supports Windows (explorer), Linux (xdg-open), and macOS (open -R).
    """,
    responses={
        200: {
            "description": "Folder opened successfully",
            "content": {"application/json": {"example": {"success": True, "path": "/path/to/image.png"}}}
        },
        404: {
            "description": "Image not found or file missing",
            "content": {"application/json": {"example": {"detail": "Image not found"}}}
        },
        500: {
            "description": "Failed to open folder",
            "content": {"application/json": {"example": {"detail": "Failed to open folder: ..."}}}
        }
    }
)
async def open_folder(
    body: OpenFolderRequest,
    service: ImageService = Depends(get_image_service),
):
    """Open the containing folder of an image in the OS file explorer."""
    if body.image_id is None:
        raise HTTPException(status_code=400, detail="image_id is required")

    return service.open_image_folder(
        body.image_id,
        platform=sys.platform,
        popen=subprocess.Popen,
    )


@router.post(
    "/parse-image",
    summary="Parse uploaded image metadata",
    description="""
Accept an image file upload and return parsed SD metadata without saving to the database.

Useful for inspecting metadata of images that are not yet in the library.
Returns generator type, prompt, negative prompt, checkpoint, LoRAs, generation
parameters, image dimensions, and file size.
    """,
    responses={
        200: {
            "description": "Parsed metadata",
            "content": {
                "application/json": {
                    "example": {
                        "generator": "comfyui",
                        "prompt": "1girl, solo, masterpiece",
                        "negative_prompt": "lowres, bad anatomy",
                        "checkpoint": "sd_xl_base_1.0.safetensors",
                        "loras": ["detail_tweaker"],
                        "width": 1024,
                        "height": 1536,
                        "file_size": 2048576,
                        "metadata": {}
                    }
                }
            }
        },
        400: {
            "description": "No file uploaded",
            "content": {"application/json": {"example": {"detail": "No file uploaded"}}}
        },
        500: {
            "description": "Failed to parse image",
            "content": {"application/json": {"example": {"detail": "Failed to parse image metadata: ..."}}}
        }
    }
)
async def parse_uploaded_image(
    file: UploadFile = File(...),
    service: ImageService = Depends(get_image_service),
):
    """Parse metadata from an uploaded image file without saving to database."""
    return await service.parse_uploaded_image(
        file,
        temp_dir=_images_facade.READER_UPLOAD_TEMP_DIR,
        temp_ttl_seconds=_images_facade.READER_UPLOAD_TTL_SECONDS,
        max_bytes=_images_facade.PARSE_IMAGE_UPLOAD_MAX_BYTES,
        chunk_size=_images_facade.PARSE_IMAGE_UPLOAD_CHUNK_SIZE,
    )
