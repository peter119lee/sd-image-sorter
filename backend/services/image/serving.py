"""File/thumbnail serving, metadata re-parse and edited-metadata save,
folder opening, Reader upload parsing, and thumbnail-cache admin.

Methods moved verbatim from services/image_service.py (decomposition 2026-07)
except the lines listed in the split manifest: the two indexed-path healing
calls pass the facade’s _BACKEND_FILE (utils/source_paths derives
backend_root from dirname(dirname(backend_file)); this mixin’s own
__file__ is one level too deep — the trap the
test_backend_file_argument_resolves_to_the_backend_root pin locks). The
bare-import seams (resolve_existing_indexed_image_path,
reparse_image_metadata, save_and_reconcile_checked, verify_image_readable,
parse_image, get_thumbnail_async) resolve through the facade so module-object
monkeypatches on services.image_service keep landing.
"""

import io
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

import database as db
from services.image_metadata_writer import (
    JPEG_LIMITATION_WARNING,
    WEBP_LIMITATION_WARNING,
    build_exif_bytes,
    build_pnginfo,
    build_sd_parameters_text,
    normalize_edited_metadata,
    prepare_image_for_save,
)
from thumbnail_cache import (
    generate_placeholder_thumbnail,
    clear_cache as clear_thumbnail_cache,
    cleanup_old_cache,
    enforce_cache_size_limit,
    get_cache_stats,
    SUPPORTED_SIZES,
)
from utils.path_validation import (
    ALLOWED_IMAGE_EXTENSIONS,
    PathValidationError,
    validate_file_path,
    validate_image_output_path,
)

# NOTE(decomposition): keep the historical logger channel so log routing and
# output stay byte-identical after the package split.
logger = logging.getLogger("services.image_service")


def _svc():
    """Resolve facade-owned seams/constants through services.image_service at call time.

    Tests patch module attributes on the facade; a ``from`` import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.image_service as image_service

    return image_service


def resolve_existing_indexed_image_path(*args, **kwargs):
    """Facade-seam proxy (the pin suite patches services.image_service.resolve_existing_indexed_image_path)."""
    return _svc().resolve_existing_indexed_image_path(*args, **kwargs)


def reparse_image_metadata(*args, **kwargs):
    """Facade-seam proxy (latent seam)."""
    return _svc().reparse_image_metadata(*args, **kwargs)


def save_and_reconcile_checked(*args, **kwargs):
    """Facade-seam proxy (latent seam)."""
    return _svc().save_and_reconcile_checked(*args, **kwargs)


def verify_image_readable(*args, **kwargs):
    """Facade-seam proxy (latent seam)."""
    return _svc().verify_image_readable(*args, **kwargs)


def parse_image(*args, **kwargs):
    """Facade-seam proxy (latent seam)."""
    return _svc().parse_image(*args, **kwargs)


def get_thumbnail_async(*args, **kwargs):
    """Facade-seam proxy (latent seam; returns the facade coroutine, awaited at the call site)."""
    return _svc().get_thumbnail_async(*args, **kwargs)


def _cleanup_stale_reader_uploads(temp_dir: Path, ttl_seconds: int) -> None:
    """Best-effort cleanup for temporary Reader uploads kept for follow-up save actions."""
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc).timestamp() - ttl_seconds
        for candidate in temp_dir.iterdir():
            try:
                if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except OSError:
                continue
    except OSError:
        logger.debug("Failed to prepare Reader temp directory", exc_info=True)


def _allocate_reader_upload_path(temp_dir: Path, filename: str) -> Path:
    suffix = Path(filename or "").suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
        suffix = ".png"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir / f"{uuid.uuid4().hex}{suffix}"


class ServingMixin:
    """File/thumbnail serving and metadata-save slice of ImageService (assembled in services/image_service.py)."""

    def resolve_image_source_path(self, image_id: int, primary_path: str) -> str:
        """
        Resolve the best available image source path.

        Args:
            image_id: Image ID for error messages
            primary_path: Primary path from database

        Returns:
            Resolved absolute path to the image file

        Raises:
            HTTPException 404: Image file not found on disk
        """
        resolved_path = resolve_existing_indexed_image_path(primary_path, backend_file=_svc()._BACKEND_FILE)
        if resolved_path:
            return resolved_path

        raise HTTPException(status_code=404, detail="Image file not found on disk")

    def reparse_image(self, image_id: int) -> Dict[str, Any]:
        """
        Re-parse metadata for a single image and update the database.

        Args:
            image_id: The unique identifier of the image

        Returns:
            Updated image data

        Raises:
            HTTPException 404: Image not found
            HTTPException 500: Failed to parse metadata
        """
        image = db.get_image_by_id(image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        source_path = self.resolve_image_source_path(image_id, image["path"])

        try:
            reparse_image_metadata(image_id, source_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to reparse metadata") from exc

        return self.get_image_by_id(image_id)

    def save_image_with_edited_metadata(
        self,
        source_path: str,
        output_path: str,
        image_format: str,
        metadata: Optional[Dict[str, Any]],
        allow_overwrite: bool = False,
        quality: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save a copy of an image with edited SD metadata."""
        is_valid, error = validate_file_path(source_path, ALLOWED_IMAGE_EXTENSIONS)
        if not is_valid:
            raise PathValidationError(error or "Invalid source image path")

        source = Path(source_path).resolve()
        output = validate_image_output_path(output_path, allow_overwrite=allow_overwrite)

        normalized_output_format = output.extension.lstrip(".").lower()
        if normalized_output_format == "jpeg":
            normalized_output_format = "jpg"

        requested_format = str(image_format or normalized_output_format).strip().lower()
        if requested_format == "jpeg":
            requested_format = "jpg"
        if requested_format not in {"png", "webp", "jpg"}:
            raise ValueError("Unsupported output format")
        if requested_format != normalized_output_format:
            raise ValueError("Output path extension does not match the selected format")

        if quality is not None and (quality < 1 or quality > 100):
            raise ValueError("Quality must be between 1 and 100")

        normalized_metadata = normalize_edited_metadata(metadata)
        parameters_text = build_sd_parameters_text(normalized_metadata)
        warnings: List[str] = []

        pil_format = "PNG"
        if requested_format == "webp":
            pil_format = "WEBP"
            warnings.append(WEBP_LIMITATION_WARNING)
        elif requested_format == "jpg":
            pil_format = "JPEG"
            warnings.append(JPEG_LIMITATION_WARNING)

        def _write_edited_image(final_output_path: str, _overwrite_requested: bool) -> None:
            with Image.open(source) as image:
                save_image = prepare_image_for_save(image, pil_format, warnings)
                save_kwargs: Dict[str, Any] = {}
                icc_profile = image.info.get("icc_profile")
                if icc_profile:
                    save_kwargs["icc_profile"] = icc_profile

                if pil_format == "PNG":
                    save_kwargs["pnginfo"] = build_pnginfo(normalized_metadata, parameters_text)
                else:
                    exif_bytes = build_exif_bytes(image, parameters_text)
                    if exif_bytes:
                        save_kwargs["exif"] = exif_bytes
                    save_kwargs["quality"] = int(quality if quality is not None else (92 if pil_format == "JPEG" else 95))

                try:
                    save_image.save(final_output_path, format=pil_format, **save_kwargs)
                finally:
                    save_image.close()

        write_result = save_and_reconcile_checked(
            str(output.path),
            _write_edited_image,
            allow_overwrite=allow_overwrite,
            source_path=str(source),
            preserve_derived_state=(source == output.path),
            backend_file=_svc()._BACKEND_FILE,
        )
        warnings.extend(write_result.warnings)

        return {
            "output_path": str(output.path),
            "format": requested_format,
            "warnings": warnings,
        }

    def open_image_folder(
        self,
        image_id: int,
        *,
        platform: str,
        popen: Callable[[List[str]], Any] = subprocess.Popen,
    ) -> Dict[str, Any]:
        """Open the containing folder of an indexed image in the OS file explorer."""
        image = db.get_image_by_id(image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        try:
            file_path = self.resolve_image_source_path(image_id, image.get("path", ""))
            normalized_path = os.path.normpath(file_path)

            if platform == "win32":
                popen(["explorer", "/select,", normalized_path])
            elif platform == "darwin":
                popen(["open", "-R", normalized_path])
            else:
                popen(["xdg-open", os.path.dirname(normalized_path)])

            return {"success": True, "path": normalized_path}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to open folder for image %s: %s", image_id, exc)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to open folder: {exc}",
            ) from exc

    async def parse_uploaded_image(
        self,
        file: UploadFile,
        *,
        temp_dir: Path,
        temp_ttl_seconds: int,
        max_bytes: int,
        chunk_size: int,
    ) -> Dict[str, Any]:
        """Parse uploaded image metadata without persisting the image in the library DB."""
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded")

        tmp_path: Optional[Path] = None
        cleanup_tmp = True

        try:
            _cleanup_stale_reader_uploads(temp_dir, temp_ttl_seconds)
            tmp_path = _allocate_reader_upload_path(temp_dir, file.filename)

            with open(tmp_path, "wb") as tmp_handle:
                total_bytes = 0
                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Uploaded image is too large to parse (max 64MB)",
                        )
                    tmp_handle.write(chunk)

            readable, read_error = await run_in_threadpool(verify_image_readable, str(tmp_path))
            if not readable:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid or unreadable image file: {read_error or 'image decode failed'}",
                )

            result = await run_in_threadpool(parse_image, str(tmp_path))
            if result.get("parse_error") or result.get("width", 0) <= 0 or result.get("height", 0) <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to parse image metadata: {result.get('parse_error') or 'image metadata could not be read'}",
                )

            result["source_temp_path"] = str(tmp_path.resolve())
            cleanup_tmp = False
            return result
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to parse uploaded image %s: %s", getattr(file, "filename", None), exc)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse image metadata: {exc}",
            ) from exc
        finally:
            await file.close()
            if cleanup_tmp and tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError as cleanup_exc:
                    logger.warning(
                        "Failed to remove temp upload file %s: %s",
                        tmp_path,
                        cleanup_exc,
                    )

    def get_image_file(self, image_id: int) -> FileResponse:
        """
        Serve the actual image file.

        Args:
            image_id: The unique identifier of the image

        Returns:
            FileResponse with the image binary data

        Raises:
            HTTPException 404: Image not found or file missing
        """
        image = db.get_image_by_id(image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        file_path = self.resolve_image_source_path(image_id, image["path"])
        filename = image.get("filename") or os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
        }
        return FileResponse(
            file_path,
            media_type=media_types.get(ext),
            filename=filename,
            content_disposition_type="inline",
        )

    async def get_image_thumbnail(
        self,
        image_id: int,
        size: int = 256
    ) -> StreamingResponse:
        """
        Get a thumbnail of the image with persistent disk caching.

        Args:
            image_id: The unique identifier of the image
            size: Maximum thumbnail dimension

        Returns:
            StreamingResponse with WebP image data

        Raises:
            HTTPException 404: Image not found
            HTTPException 500: Failed to generate thumbnail
        """
        image = db.get_image_by_id(image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        source_path = self.resolve_image_source_path(image_id, image["path"])

        try:
            if os.path.islink(source_path):
                raise HTTPException(status_code=404, detail="Image file not found on disk")
            thumbnail_bytes, last_modified, cache_hit = await get_thumbnail_async(source_path, size)
            media_type = "image/webp"
            max_age = 86400 if cache_hit else 3600

            return StreamingResponse(
                io.BytesIO(thumbnail_bytes),
                media_type=media_type,
                headers={
                    "Cache-Control": f"public, max-age={max_age}",
                    "Last-Modified": format_datetime(last_modified, usegmt=True),
                    "X-Thumbnail-Cache": "HIT" if cache_hit else "MISS",
                },
            )
        except (UnidentifiedImageError, OSError):
            placeholder_bytes = generate_placeholder_thumbnail(size)
            return StreamingResponse(
                io.BytesIO(placeholder_bytes),
                media_type="image/webp",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Thumbnail-Cache": "MISS",
                    "X-Thumbnail-Placeholder": "UNREADABLE",
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to generate thumbnail") from exc

    def get_thumbnail_cache_stats(self) -> Dict[str, Any]:
        """Get thumbnail cache statistics."""
        stats = get_cache_stats()
        return {
            "cache_stats": stats,
            "supported_sizes": list(SUPPORTED_SIZES),
        }

    def clear_thumbnail_cache(self) -> Dict[str, int]:
        """Clear all cached thumbnails."""
        count = clear_thumbnail_cache()
        return {"deleted_count": count}

    def cleanup_thumbnail_cache(self, max_age_days: int = 30) -> Dict[str, Any]:
        """Remove cached thumbnails older than max_age_days, then enforce size cap."""
        count = cleanup_old_cache(max_age_days)
        size_cleanup = enforce_cache_size_limit(force=True)
        return {"deleted_count": count, "max_age_days": max_age_days, "size_cleanup": size_cleanup}
