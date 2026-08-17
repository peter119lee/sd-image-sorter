"""
Image obfuscation endpoints for SD Image Sorter.

Supports:
- reference-compatible preview encode/decode
- optional legacy PNG Info text encryption
- clipboard-friendly PNG result bytes
"""
import logging
import os
import tempfile
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from obfuscation import (
    BIG_TOMATO_MODE,
    MAX_OBFUSCATE_SOURCE_BYTES,
    ImageTooLargeError,
    ObfuscationOverwriteConflictError,
    batch_process,
    decode_image,
    decode_image_bytes,
    encode_image,
    encode_image_bytes,
    extract_source_text_chunks_from_bytes,
    normalize_compat_mode,
)
from utils.path_validation import (
    ALLOWED_IMAGE_EXTENSIONS,
    sanitize_filename,
    validate_file_path,
    validate_folder_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/obfuscate", tags=["obfuscation"])
PREVIEW_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


def _safe_unlink(path: Optional[str]) -> None:
    """Best-effort cleanup for temporary preview files."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


async def _read_preview_upload(file: UploadFile) -> bytes:
    """Read preview uploads incrementally so oversized files fail before full buffering."""
    chunks = []
    total = 0

    while True:
        chunk = await file.read(PREVIEW_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_OBFUSCATE_SOURCE_BYTES:
            raise ImageTooLargeError(
                f"Image file too large (max {MAX_OBFUSCATE_SOURCE_BYTES // (1024 * 1024)}MB)"
            )
        chunks.append(chunk)

    return b"".join(chunks)


def _validate_source_image(path: str) -> str:
    """Reject bad paths, missing files, traversal, and unsupported extensions."""
    is_valid, error = validate_file_path(path, allowed_extensions=ALLOWED_IMAGE_EXTENSIONS)
    if not is_valid:
        status = 404 if error == "File does not exist" else 400
        raise HTTPException(status_code=status, detail=error or "Invalid source image path")
    return path


def _validate_output_path(path: str) -> str:
    """Validate an output path's parent directory (file does not need to exist yet)."""
    if not path or not isinstance(path, str):
        raise HTTPException(status_code=400, detail="Output path cannot be empty")
    parent = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    is_valid, error = validate_folder_path(parent, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid output directory")
    filename = os.path.basename(path)
    if not filename or sanitize_filename(filename) in ("", "unnamed") and filename not in ("unnamed",):
        raise HTTPException(status_code=400, detail="Invalid output filename")
    return path


def _validate_output_folder(path: str) -> str:
    """Validate a batch output folder (created if missing)."""
    is_valid, error = validate_folder_path(path, allow_create=True)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error or "Invalid output folder")
    return path


class SingleProcessRequest(BaseModel):
    image_path: str = Field(..., description="Path to the image file")
    output_path: str = Field(..., description="Path to save the processed image")
    password: str = Field(default="", description="Password for scrambling")
    preserve_metadata: bool = Field(default=True, description="Preserve SD metadata")
    compat_mode: str = Field(default=BIG_TOMATO_MODE, description="Compatibility mode: big_tomato or small_tomato")
    allow_overwrite: bool = Field(default=False, description="Allow replacing an existing output file")


class BatchProcessRequest(BaseModel):
    # Background-task pipeline: each path is processed sequentially so the
    # only risk from a large list is the request payload itself. Internal
    # operations don't fan out into bulk SQL, so the ceiling only caps
    # payload memory. 5,000,000 matches the rest of the batch endpoints and
    # covers any realistic personal library; 50k was still rejecting real
    # users with larger collections.
    image_paths: List[str] = Field(..., min_length=1, max_length=5_000_000, description="List of image file paths")
    output_folder: str = Field(..., description="Destination folder")
    password: str = Field(default="", description="Password for scrambling")
    mode: str = Field(default="encode", description="'encode' or 'decode'")
    preserve_metadata: bool = Field(default=True, description="Preserve SD metadata")
    suffix: str = Field(default="", description="Suffix for output filenames")
    legacy_pnginfo: bool = Field(default=False, description="Use legacy PNG Info text algorithm")
    compat_mode: str = Field(default=BIG_TOMATO_MODE, description="Compatibility mode: big_tomato or small_tomato")
    allow_overwrite: bool = Field(default=False, description="Allow replacing existing output files")


@router.post("/encode")
async def encode_single(request: SingleProcessRequest):
    """Encode (scramble) a single image."""
    image_path = _validate_source_image(request.image_path)
    output_path = _validate_output_path(request.output_path)

    try:
        compat_mode = normalize_compat_mode(request.compat_mode)
        result = encode_image(
            image_path,
            output_path,
            request.password,
            request.preserve_metadata,
            compat_mode=compat_mode,
            allow_overwrite=request.allow_overwrite,
        )
        return result
    except ImageTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ObfuscationOverwriteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Encode failed")
        raise HTTPException(
            status_code=500,
            detail="Encode failed. / 加密失败。",
        )


@router.post("/decode")
async def decode_single(request: SingleProcessRequest):
    """Decode (unscramble) a single image."""
    image_path = _validate_source_image(request.image_path)
    output_path = _validate_output_path(request.output_path)

    try:
        compat_mode = normalize_compat_mode(request.compat_mode)
        result = decode_image(
            image_path,
            output_path,
            request.password,
            request.preserve_metadata,
            compat_mode=compat_mode,
            allow_overwrite=request.allow_overwrite,
        )
        return result
    except ImageTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ObfuscationOverwriteConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Decode failed")
        raise HTTPException(
            status_code=500,
            detail="Decode failed. / 解密失败。",
        )


@router.post("/batch")
async def batch(request: BatchProcessRequest):
    """Batch encode or decode multiple images."""
    if request.mode not in ("encode", "decode"):
        raise HTTPException(status_code=400, detail="mode must be 'encode' or 'decode'")

    for source in request.image_paths:
        _validate_source_image(source)

    output_folder = _validate_output_folder(request.output_folder)

    try:
        compat_mode = normalize_compat_mode(request.compat_mode)
        result = batch_process(
            request.image_paths,
            output_folder,
            request.password,
            request.mode,
            request.preserve_metadata,
            request.suffix,
            legacy_pnginfo=request.legacy_pnginfo,
            compat_mode=compat_mode,
            allow_overwrite=request.allow_overwrite,
        )
        return result
    except ImageTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Batch %s failed: %s", request.mode, e)
        raise HTTPException(status_code=500, detail=f"Batch failed: {str(e)}")


@router.post("/preview")
async def preview_process(
    file: UploadFile = File(...),
    password: str = Form(default=""),
    mode: str = Form(default="encode"),
    preserve_metadata: bool = Form(default=True),
    legacy_pnginfo: bool = Form(default=False),
    compat_mode: str = Form(default=BIG_TOMATO_MODE),
):
    """Process image and return a PNG result for preview / download / clipboard copy."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    if mode not in ("encode", "decode"):
        raise HTTPException(status_code=400, detail="mode must be 'encode' or 'decode'")

    stem = os.path.splitext(os.path.basename(file.filename))[0] or "image"
    tmp_out_path = None
    try:
        normalized_mode = normalize_compat_mode(compat_mode)
        content = await _read_preview_upload(file)
        text_chunks = extract_source_text_chunks_from_bytes(content) if preserve_metadata else []

        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_out:
            tmp_out_path = tmp_out.name

        process_fn = encode_image_bytes if mode == "encode" else decode_image_bytes
        result_bytes = process_fn(
            content,
            password or "",
            text_chunks=text_chunks,
            preserve_metadata=preserve_metadata,
            legacy_pnginfo=legacy_pnginfo,
            compat_mode=normalized_mode,
        )
        with open(tmp_out_path, "wb") as handle:
            handle.write(result_bytes)

        return FileResponse(
            tmp_out_path,
            media_type="image/png",
            filename=f"{'encoded' if mode == 'encode' else 'decoded'}_{stem}.png",
            background=BackgroundTask(_safe_unlink, tmp_out_path),
        )
    except ImageTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UnidentifiedImageError:
        # Caller uploaded something that isn't a recognizable image (zip, HTML,
        # empty file). v3.2.2: previously surfaced as 500 with the BytesIO
        # object's address leaked into the response body. Treat as a client
        # error with a sanitized message.
        raise HTTPException(status_code=400, detail="Upload is not a recognizable image file (PNG / JPEG / WebP).")
    except Image.DecompressionBombError as exc:
        # Pixel count exceeds Pillow's MAX_IMAGE_PIXELS guard (decompression
        # bomb). DecompressionBombError subclasses Exception, NOT OSError, so it
        # must be caught explicitly or it falls through to the generic 500. The
        # oversized upload is the caller's fault, so 413 is correct.
        logger.warning("Obfuscate preview rejected oversized image (decompression bomb): %s", exc)
        raise HTTPException(status_code=413, detail="Image is too large to process safely.")
    except (OSError, IOError) as exc:
        # Truncated PNG, corrupt header, or otherwise unreadable bytes. The
        # caller's input is at fault, not the server, so 400 is correct.
        logger.warning("Obfuscate preview rejected unreadable upload: %s", exc)
        raise HTTPException(status_code=400, detail="Image file is corrupt or truncated.")
    except Exception as e:
        # Genuine server-side failure - log it but keep the wire response
        # generic so we don't leak Python repr / object addresses.
        logger.exception("Obfuscate preview failed: %s", e)
        raise HTTPException(status_code=500, detail="Preview processing failed.")
