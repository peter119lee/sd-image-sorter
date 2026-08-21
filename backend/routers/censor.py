"""
Censor endpoints for SD Image Sorter.
Handles NSFW detection, censoring preview and save operations.

Supports multiple detection backends:
- Legacy YOLOv8 ONNX (wenaka model)
- NudeNet v3 (NSFW-specific body part detection)
- SAM3 mask refinement (pixel-precise segmentation)

Refactored to use Service Layer pattern with dependency injection.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from services import entry_stats_service
from services.service_provider import ServiceProvider
from services.censor_service import (
    CensorService,
    CensorDetectRequest,
    MaskRefineRequest,
    BatchMaskRefineRequest,
    TextSegmentRequest,
    CensorApplyRequest,
    CensorSaveRequest,
    CensorSaveDataRequest,
    CensorSaveOperationsRequest,
    RemoveBackgroundRequest,
)


router = APIRouter(prefix="/api/censor", tags=["censor"])

# Service instance - will be set via dependency injection
_censor_service_provider = ServiceProvider(CensorService)


get_censor_service = _censor_service_provider.get
set_censor_service = _censor_service_provider.set


@router.post(
    "/detect",
    summary="Detect regions to censor",
    description="""
Run detection on an image to find regions that may need censoring.

**Supported detection backends:**
- `"legacy"`: Original YOLOv8 ONNX model (requires model_path)
- `"nudenet"`: NudeNet v3 body part detection (recommended for NSFW)
- `"both"`: Run both detectors and merge results

**NudeNet mapped labels include:**
- `breasts`, `buttocks`, `pussy`, `dick`, `anus`, `belly`, `feet`, `armpits`
- covered variants such as `breasts_covered`, `buttocks_covered`

Use `exposed_only=true` to detect only exposed (not covered) parts.
    """,
    responses={
        200: {
            "description": "Detection results",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "image_id": 1,
                        "model_type": "nudenet",
                        "detections": [
                            {
                                "box": [100, 200, 300, 400],
                                "label": "breasts",
                                "confidence": 0.89,
                                "source": "nudenet"
                            }
                        ]
                    }
                }
            }
        },
        400: {"description": "Invalid model path for legacy mode"},
        404: {"description": "Image not found"},
        500: {"description": "Detection failed"}
    }
)
async def censor_detect(
    request: CensorDetectRequest,
    service: CensorService = Depends(get_censor_service),
):
    """Run detection on an image to find regions to censor."""
    return await run_in_threadpool(service.detect, request)


@router.post(
    "/preview",
    summary="Preview censored image",
    description="""
Apply censoring to specified regions and return a base64-encoded preview image.
Opaque previews are JPEG data URLs. Sources with transparency are PNG data URLs so alpha is preserved;
the declared MIME type always matches the encoded bytes.

**Censoring styles:**
- `mosaic`: Pixelate the region with adjustable block size
- `blur`: Gaussian blur with adjustable radius
- `solid`: Fill with solid color
- `sticker`: Overlay a sticker image

Use this endpoint to preview before saving with `/api/censor/save`.
    """,
    responses={
        200: {
            "description": "Preview image",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "preview": "data:image/png;base64,iVBORw0KGgo..."
                    }
                }
            }
        },
        404: {"description": "Image not found"},
        500: {"description": "Preview failed"}
    }
)
async def censor_preview(
    request: CensorApplyRequest,
    service: CensorService = Depends(get_censor_service),
):
    """Apply censoring and return base64 preview image."""
    return await run_in_threadpool(service.preview, request)


@router.post("/save")
async def censor_save(
    request: CensorSaveRequest,
    service: CensorService = Depends(get_censor_service),
):
    """Apply censoring and save to output folder."""
    result = await run_in_threadpool(service.save, request)
    entry_stats_service.record_activity(entry_stats_service.KIND_CENSORED, 1)
    return result


@router.post("/save-data")
async def censor_save_data(
    request: CensorSaveDataRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Save base64 image data directly to disk.
    Used for saving canvas-edited images.
    Supports metadata handling: 'keep' preserves original metadata, 'strip' removes all metadata.
    """
    result = await run_in_threadpool(service.save_data, request)
    entry_stats_service.record_activity(entry_stats_service.KIND_CENSORED, 1)
    return result


@router.post("/save-operations")
async def censor_save_operations(
    request: CensorSaveOperationsRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Save a non-destructive edit operation list on top of the original image.
    Used by the large-image proxy editor so the browser does not need to upload
    a full rasterized canvas snapshot.
    """
    result = await run_in_threadpool(service.save_operations, request)
    entry_stats_service.record_activity(entry_stats_service.KIND_CENSORED, 1)
    return result


@router.post("/refine-mask")
async def refine_mask(
    request: MaskRefineRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Refine a bounding box into a pixel-precise segmentation mask using SAM3.

    Takes a detection bounding box and returns a refined binary mask
    that follows the actual contours of the detected region.
    Returns HTTP 503 if SAM3 is not prepared. There is no silent fallback
    to another detector.
    """
    return await run_in_threadpool(service.refine_mask, request)


@router.post("/batch-refine-mask")
async def batch_refine_mask(
    request: BatchMaskRefineRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Run SAM3 mask refinement on multiple images/boxes sequentially.

    Processes each item one-by-one through SAM3 (heavy model) but
    presents as a single batch operation. Returns results and errors
    for each item.
    """
    return await run_in_threadpool(service.batch_refine_mask, request)


@router.post("/segment-text")
async def segment_text(
    request: TextSegmentRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Segment objects by text description using SAM3's open-vocabulary feature.

    Allows users to describe what they want to censor in natural language,
    e.g. "exposed breasts", "person's face", "tattoo on arm".
    """
    return await run_in_threadpool(service.segment_text, request)


@router.post("/remove-background")
async def remove_background(
    request: RemoveBackgroundRequest,
    service: CensorService = Depends(get_censor_service),
):
    """
    Remove background using SAM3 foreground detection.

    Detects the main subject in the image and removes the background,
    allowing user choice of transparent, white, or black fill.

    **Fill modes:**
    - `transparent`: PNG with alpha channel (recommended)
    - `white`: White background fill
    - `black`: Black background fill

    **Edge threshold:**
    Controls SAM3 detection sensitivity (0.0-1.0).
    Higher = stricter detection, lower = more permissive.

    Returns a base64-encoded preview image.
    """
    return await run_in_threadpool(service.remove_background, request)


@router.get("/mask-cache/{mask_ref}")
async def get_cached_mask_preview(
    mask_ref: str,
    width: Optional[int] = Query(default=None, ge=1, le=8192),
    height: Optional[int] = Query(default=None, ge=1, le=8192),
    service: CensorService = Depends(get_censor_service),
):
    """
    Fetch a cached SAM3 mask crop by reference.

    Large-image flows use this to avoid shipping giant base64 masks through the
    browser. Optional width/height resize is intended for proxy-canvas previews.
    """
    return await run_in_threadpool(service.get_cached_mask_preview, mask_ref, width, height)


@router.get("/models")
async def list_models(
    service: CensorService = Depends(get_censor_service),
):
    """
    List available detection backends and their status.

    Returns which detection models are installed and ready to use,
    helping the frontend show appropriate options.
    """
    return service.list_models()
