"""
Censor endpoints for SD Image Sorter.
Handles NSFW detection, censoring preview and save operations.
"""
import os
import base64
import traceback
from typing import Optional, List
from io import BytesIO

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from PIL import Image, PngImagePlugin

import database as db

router = APIRouter(prefix="/api/censor", tags=["censor"])


# Pydantic models for this router
class CensorDetectRequest(BaseModel):
    image_id: int
    model_path: str
    confidence_threshold: float = 0.5


class CensorApplyRequest(BaseModel):
    image_id: int
    regions: List[List[int]]
    style: str = "mosaic"
    block_size: int = 16
    blur_radius: int = 20
    sticker_path: Optional[str] = None


class CensorSaveRequest(BaseModel):
    image_id: int
    regions: List[List[int]]
    style: str = "mosaic"
    block_size: int = 16
    blur_radius: int = 20
    sticker_path: Optional[str] = None
    output_folder: str
    filename_suffix: str = "_censored"


class CensorSaveDataRequest(BaseModel):
    """Request to save base64 image data directly."""
    image_data: str
    filename: str
    output_folder: str
    metadata_option: str = "keep"
    output_format: str = "png"  # 'png' or 'webp'
    original_image_id: Optional[int] = None


# Lazy-loaded detector
_censor_detector = None


@router.post("/detect")
async def censor_detect(request: CensorDetectRequest):
    """
    Run detection on an image to find regions to censor.
    Returns list of detected regions with class names and confidence.
    """
    global _censor_detector
    
    from censor import CensorDetector
    from utils.path_validation import validate_file_path, ALLOWED_MODEL_EXTENSIONS
    
    image = db.get_image_by_id(request.image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    is_valid, error = validate_file_path(request.model_path, ALLOWED_MODEL_EXTENSIONS)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or f"Invalid model path: {request.model_path}")
    
    try:
        if _censor_detector is None or _censor_detector.model_path != request.model_path or _censor_detector.session is None:
            if _censor_detector is not None and _censor_detector.session is None:
                print("Censor detector exists but session is None, re-loading...")
            
            print(f"Loading censor model: {request.model_path}")
            _censor_detector = CensorDetector(request.model_path)
            _censor_detector.load()
            print("Model loaded successfully")
        
        print(f"Running detection on: {image['path']}")
        detections = _censor_detector.detect(image["path"], request.confidence_threshold)
        print(f"Found {len(detections)} detections")
        
        return {
            "status": "ok",
            "image_id": request.image_id,
            "detections": detections
        }
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Detection error:\n{error_trace}")
        
        msg = str(e)
        if "Protobuf" in msg:
            msg = "Model format error. If using a .pt file, ensure 'ultralytics' is installed. If using .onnx, the file may be corrupted."
            
        raise HTTPException(status_code=500, detail=f"Detection failed: {msg}")


@router.post("/preview")
async def censor_preview(request: CensorApplyRequest):
    """Apply censoring and return base64 preview image."""
    from censor import Censor
    
    image_data = db.get_image_by_id(request.image_id)
    if not image_data:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image_data["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    try:
        image = Image.open(image_data["path"]).convert('RGB')
        regions = [tuple(r) for r in request.regions]
        
        censored = Censor.apply_censoring(
            image,
            regions,
            style=request.style,
            block_size=request.block_size,
            blur_radius=request.blur_radius,
            sticker_path=request.sticker_path
        )
        
        buffer = BytesIO()
        censored.save(buffer, format='JPEG', quality=90)
        buffer.seek(0)
        b64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return {
            "status": "ok",
            "preview": f"data:image/jpeg;base64,{b64_image}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")


@router.post("/save")
async def censor_save(request: CensorSaveRequest):
    """Apply censoring and save to output folder."""
    from censor import Censor
    from utils.path_validation import validate_folder_path
    
    image_data = db.get_image_by_id(request.image_id)
    if not image_data:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not os.path.exists(image_data["path"]):
        raise HTTPException(status_code=404, detail="Image file not found on disk")
    
    is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid output folder")
    
    try:
        os.makedirs(request.output_folder, exist_ok=True)
        
        image = Image.open(image_data["path"]).convert('RGB')
        regions = [tuple(r) for r in request.regions]
        
        censored = Censor.apply_censoring(
            image,
            regions,
            style=request.style,
            block_size=request.block_size,
            blur_radius=request.blur_radius,
            sticker_path=request.sticker_path
        )
        
        base_name = os.path.splitext(image_data["filename"])[0]
        ext = os.path.splitext(image_data["filename"])[1] or ".png"
        output_filename = f"{base_name}{request.filename_suffix}{ext}"
        output_path = os.path.join(request.output_folder, output_filename)
        
        if ext.lower() in ['.jpg', '.jpeg']:
            censored.save(output_path, format='JPEG', quality=95)
        else:
            censored.save(output_path, format='PNG')
        
        return {
            "status": "ok",
            "output_path": output_path,
            "filename": output_filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")


@router.post("/save-data")
async def censor_save_data(request: CensorSaveDataRequest):
    """
    Save base64 image data directly to disk.
    Used for saving canvas-edited images.
    Supports metadata handling: 'keep' preserves original metadata, 'wash' strips all metadata.
    """
    from utils.path_validation import validate_folder_path, sanitize_filename
    
    is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid output folder")
    
    try:
        os.makedirs(request.output_folder, exist_ok=True)
        
        if ',' in request.image_data:
            header, data = request.image_data.split(',', 1)
        else:
            data = request.image_data
        
        image_bytes = base64.b64decode(data)
        image = Image.open(BytesIO(image_bytes))
        
        safe_filename = sanitize_filename(request.filename)
        base_name = os.path.splitext(safe_filename)[0]
        # Use explicit output_format for extension instead of filename extension
        ext = f".{request.output_format.lower()}"
        output_filename = f"{base_name}{ext}"
        output_path = os.path.join(request.output_folder, output_filename)
        
        save_kwargs = {}
        output_format = request.output_format.lower()
        
        # Handle metadata based on option
        if request.metadata_option == "strip":
            # Strip all metadata by creating a clean copy of just the pixel data
            # This ensures no metadata from the canvas PNG is preserved
            clean_image = Image.new(image.mode, image.size)
            clean_image.putdata(list(image.getdata()))
            image = clean_image
            # save_kwargs stays empty - no metadata will be added
            
        elif request.metadata_option == "keep" and request.original_image_id:
            # Keep metadata from original image
            original_image_data = db.get_image_by_id(request.original_image_id)
            if original_image_data and os.path.exists(original_image_data["path"]):
                try:
                    original_path = original_image_data["path"]
                    original_img = Image.open(original_path)
                    
                    # Get EXIF data if available
                    if 'exif' in original_img.info:
                        save_kwargs['exif'] = original_img.info['exif']
                    
                    # Copy ICC profile if present
                    if 'icc_profile' in original_img.info:
                        save_kwargs['icc_profile'] = original_img.info['icc_profile']
                    
                    # Copy DPI if present
                    if 'dpi' in original_img.info:
                        save_kwargs['dpi'] = original_img.info['dpi']
                    
                    # For PNG output, copy ALL text metadata chunks
                    if output_format == 'png':
                        pnginfo = PngImagePlugin.PngInfo()
                        has_text = False
                        
                        # PNG text chunks are common keys for SD images:
                        # - 'parameters' (WebUI/Forge)
                        # - 'prompt' and 'workflow' (ComfyUI)
                        # - 'Comment' (NovelAI)
                        # - 'Description', 'Software', etc.
                        
                        for key, value in original_img.info.items():
                            # Skip binary data that shouldn't be in text chunks
                            if key in ['exif', 'icc_profile', 'dpi', 'interlace', 'gamma', 'chromaticity']:
                                continue
                            
                            if isinstance(value, str):
                                try:
                                    pnginfo.add_text(key, value)
                                    has_text = True
                                except Exception as e:
                                    print(f"Could not add text chunk {key}: {e}")
                            elif isinstance(value, bytes):
                                # Some metadata is stored as bytes, try to decode
                                try:
                                    decoded = value.decode('utf-8')
                                    pnginfo.add_text(key, decoded)
                                    has_text = True
                                except (UnicodeDecodeError, AttributeError):
                                    # If it can't be decoded, try latin-1
                                    try:
                                        decoded = value.decode('latin-1')
                                        pnginfo.add_text(key, decoded)
                                        has_text = True
                                    except:
                                        pass
                        
                        if has_text:
                            save_kwargs['pnginfo'] = pnginfo
                            
                except Exception as e:
                    print(f"Warning: Could not copy metadata from original: {e}")
        
        
        # Save based on explicit output_format parameter
        if output_format == 'webp':
            if image.mode == 'RGBA':
                # WebP supports RGBA
                pass
            webp_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile']}
            image.save(output_path, format='WEBP', quality=95, **webp_kwargs)
        elif output_format in ['jpg', 'jpeg']:
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            jpeg_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile', 'dpi']}
            image.save(output_path, format='JPEG', quality=95, **jpeg_kwargs)
        else:
            # Default to PNG
            if request.metadata_option == "strip":
                image.save(output_path, format='PNG')
            else:
                png_kwargs = {k: v for k, v in save_kwargs.items() if k in ['pnginfo', 'dpi']}
                image.save(output_path, format='PNG', **png_kwargs)
        
        return {
            "status": "ok",
            "output_path": output_path,
            "filename": output_filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
