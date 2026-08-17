"""Output-path safety, preview/save endpoints, base64 decode, metadata copy.

Methods moved from services/censor_service.py (decomposition 2026-07). Legacy preview/save preserve alpha
and encode truthful formats. The manifest lines in save_data
and _decode_base64_image resolve MAX_SAVE_DATA_PIXELS / MAX_SAVE_DATA_BYTES
through _svc() at call time (patched on the facade module object); the three
backend_file= call-sites here (_resolve_source_image_path, save, save_data)
pass the facade _BACKEND_FILE because utils/source_paths and
indexed_file_mutation_service derive backend_root =
dirname(dirname(abspath(backend_file))) and this module sits one level too
deep; and _resolve_source_image_path resolves the CensorService class through
_svc() so class-attribute patches keep landing. SAFETY INVARIANT kept intact:
save() writes only after Censor.apply_censoring returns and emits no file on
failure (never-fallback-to-uncensored). Overwrite preflight stays owned by
indexed_file_mutation_service (source-text contract).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from PIL import Image, PngImagePlugin

import database as db
from services.image_metadata_writer import prepare_image_for_save
from services.indexed_file_mutation_service import save_and_reconcile_checked
from utils.atomic_staging import (
    create_staging_sibling,
    discard_staging_file,
    publish_staging_file,
)
from utils.source_paths import resolve_existing_indexed_image_path

if TYPE_CHECKING:  # annotation-only; never imported at runtime (no facade cycle)
    from services.censor_service import CensorApplyRequest, CensorSaveDataRequest, CensorSaveRequest

logger = logging.getLogger("services.censor_service")


CensorOutputFormat = Literal['png', 'jpg', 'webp']


def _combine_save_warnings(writer_warnings: List[str], reconcile_warnings: List[str]) -> List[str]:
    '''Return stable, de-duplicated warnings from image conversion and reconciliation.'''
    return list(dict.fromkeys([*writer_warnings, *reconcile_warnings]))


def _image_has_alpha(image: Image.Image) -> bool:
    '''Return whether decoding the image as RGB would discard transparency.'''
    return 'A' in image.getbands() or 'transparency' in image.info


def _normalize_censor_source(image: Image.Image) -> Image.Image:
    '''Return a detached RGB/RGBA raster suitable for censor operations.'''
    target_mode = 'RGBA' if _image_has_alpha(image) else 'RGB'
    return image.convert(target_mode)


def _save_pillow_image_atomically(
    image: Image.Image,
    output_path: str,
    image_format: str,
    save_kwargs: Dict[str, object],
) -> None:
    """Encode to a sibling staging file, then publish it over the destination.

    ``Image.save`` opens the destination with ``w+b`` and only removes the file
    on failure when it created it, so encoding straight at the destination left
    an overwritten original truncated to 0 bytes with no backup and no undo.

    Staging goes through ``utils.atomic_staging`` rather than ``tempfile``: on
    Windows ``mkstemp`` reads a ``PermissionError`` as a name collision and
    retries it up to ``TMP_MAX`` — 2,147,483,647 on the shipped interpreter, not
    the 10,000 the docs imply — so aiming this writer at a folder it cannot write
    to used to hang instead of failing. Publishing goes through the same
    module because ``os.replace`` severs a hard link — over a linked destination
    the alias would keep the UNcensored image, which is the opposite of what
    this feature exists to do.
    """
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging, descriptor = create_staging_sibling(target)
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        discard_staging_file(staging)
        raise
    try:
        with handle:
            image.save(handle, format=image_format, **save_kwargs)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        publish_staging_file(staging, target)
    except BaseException:
        discard_staging_file(staging)
        raise


def _resolve_censor_output_format(source_format: Optional[str]) -> tuple[CensorOutputFormat, str]:
    '''Map the decoded source format to a truthful supported output format.'''
    normalized = str(source_format or '').upper()
    if normalized == 'JPEG':
        return 'jpg', '.jpg'
    if normalized == 'WEBP':
        return 'webp', '.webp'
    return 'png', '.png'


def _svc():
    """Resolve facade-owned seams/constants through services.censor_service at call time.

    Tests patch module attributes on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.censor_service as censor_service

    return censor_service


def _paths_match_runtime_case(candidate: Path, resolved: Path) -> bool:
    """Treat Windows case normalization as the same path for symlink checks."""
    return os.path.normcase(str(candidate)) == os.path.normcase(str(resolved))


class _OutputMixin:
    """Output/save/metadata slice of CensorService (assembled in services/censor_service.py)."""

    @staticmethod
    def _ensure_safe_existing_file(path: str, *, allowed_extensions: Optional[set] = None) -> str:
        """Validate an existing file path and reject symlinks."""
        if not path:
            raise HTTPException(status_code=404, detail="File not found")

        candidate = Path(os.path.abspath(path))
        resolved_candidate = candidate.resolve(strict=False)
        if candidate.is_symlink() or not _paths_match_runtime_case(candidate, resolved_candidate):
            raise HTTPException(status_code=400, detail="Symlink paths are not allowed")

        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        if allowed_extensions and candidate.suffix.lower() not in allowed_extensions:
            raise HTTPException(status_code=400, detail="File type is not allowed")

        return str(candidate)

    @staticmethod
    def _resolve_source_image_path(
        path: str,
        *,
        image_id: Optional[int] = None,
        action_label: str = "This action",
    ) -> str:
        """Resolve an indexed image path using the same fallback rules as image serving."""
        normalized = str(path or "").strip()
        if not normalized:
            raise HTTPException(
                status_code=404,
                detail="The library entry does not contain a source image path.",
            )

        resolved_path = resolve_existing_indexed_image_path(normalized, backend_file=_svc()._BACKEND_FILE)
        if resolved_path:
            return _svc().CensorService._ensure_safe_existing_file(resolved_path)

        image_prefix = f"Image {image_id}" if image_id else "This image"
        raise HTTPException(
            status_code=404,
            detail=(
                f"{image_prefix} source file is missing on disk. {action_label} needs the original file, "
                f"but the library still points to: {normalized}. The file was probably moved, deleted, "
                "or its drive/folder is not available. Reconnect it and rescan that folder."
            ),
        )

    @staticmethod
    def _ensure_safe_output_directory(path: str) -> str:
        """Validate an output directory and reject symlinks."""
        candidate = Path(os.path.abspath(path))
        resolved_candidate = candidate.resolve(strict=False)
        if candidate.is_symlink() or not _paths_match_runtime_case(candidate, resolved_candidate):
            raise HTTPException(status_code=400, detail="Symlink paths are not allowed")
        return str(candidate)


    @staticmethod
    def _sanitize_suffix(suffix: str) -> str:
        """Convert user-provided suffix into a safe filename fragment."""
        allowed = ''.join(ch for ch in str(suffix or '') if ch.isalnum() or ch in {'_', '-'})
        if not allowed:
            return '_censored'
        if not allowed.startswith(('_', '-')):
            allowed = f'_{allowed}'
        return allowed[:64]

    @staticmethod
    def _normalize_output_format(output_format: str) -> str:
        """Normalize output format to a strict allowlist."""
        normalized = str(output_format or '').strip().lower()
        if normalized not in {'png', 'jpg', 'jpeg', 'webp'}:
            raise HTTPException(status_code=400, detail='Unsupported output format')
        return normalized

    @staticmethod
    def _ensure_output_path(output_folder: str, filename: str) -> str:
        """Build a final output path that cannot escape the target directory."""
        target_dir = Path(output_folder).resolve()
        output_path = (target_dir / filename).resolve()
        if output_path.parent != target_dir:
            raise HTTPException(status_code=400, detail='Invalid output path')
        return str(output_path)

    @staticmethod
    def _output_validation_error(message: str) -> HTTPException:
        return HTTPException(status_code=400, detail=message)

    @staticmethod
    def _output_conflict_error(message: str) -> HTTPException:
        return HTTPException(status_code=409, detail=message)

    @staticmethod
    def _save_response(output_path: str, filename: str, *, warnings: Optional[List[str]] = None, target_existed: bool = False) -> Dict[str, Any]:
        indexed_output = db.get_image_by_path(output_path)
        return {
            "status": "ok",
            "output_path": output_path,
            "filename": filename,
            "warnings": warnings or [],
            "overwrote_existing": bool(target_existed),
            "overwrote_indexed_path": bool(indexed_output),
            "reconciled_image_id": int(indexed_output["id"]) if indexed_output else None,
        }

    def preview(self, request: CensorApplyRequest) -> Dict[str, str]:
        """Apply censoring and return base64 preview image."""
        from censor import Censor

        image_data = db.get_image_by_id(request.image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image_data["path"],
            image_id=request.image_id,
            action_label="Preview",
        )

        # Validate sticker_path to prevent arbitrary file read
        if request.sticker_path:
            from utils.path_validation import validate_file_path
            is_valid, error = validate_file_path(request.sticker_path, allowed_extensions={'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'})
            if not is_valid:
                raise HTTPException(status_code=400, detail=error or "Invalid sticker path")

        try:
            with Image.open(image_path) as src:
                image = _normalize_censor_source(src)
            regions = [(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in request.regions]

            censored = Censor.apply_censoring(
                image,
                regions,
                style=request.style,
                block_size=request.block_size,
                blur_radius=request.blur_radius,
                sticker_path=request.sticker_path
            )

            buffer = BytesIO()
            if _image_has_alpha(censored):
                censored.save(buffer, format="PNG")
                preview_mime = "image/png"
            else:
                censored.save(buffer, format="JPEG", quality=90)
                preview_mime = "image/jpeg"
            buffer.seek(0)
            b64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

            return {
                "status": "ok",
                "preview": f"data:{preview_mime};base64,{b64_image}"
            }
        except Exception:
            raise HTTPException(status_code=500, detail="Preview failed")

    def save(self, request: CensorSaveRequest) -> Dict[str, Any]:
        """Apply censoring and save to output folder."""
        from censor import Censor
        from utils.path_validation import validate_folder_path, validate_file_path

        image_data = db.get_image_by_id(request.image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image_data["path"],
            image_id=request.image_id,
            action_label="Saving",
        )

        # Validate sticker_path to prevent arbitrary file read
        if request.sticker_path:
            is_valid_sticker, sticker_error = validate_file_path(request.sticker_path, allowed_extensions={'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'})
            if not is_valid_sticker:
                raise HTTPException(status_code=400, detail=sticker_error or "Invalid sticker path")

        is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid output folder")

        output_folder = self._ensure_safe_output_directory(request.output_folder)

        try:
            os.makedirs(output_folder, exist_ok=True)

            with Image.open(image_path) as src:
                source_format = src.format
                image = _normalize_censor_source(src)
            regions = [(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in request.regions]

            censored = Censor.apply_censoring(
                image,
                regions,
                style=request.style,
                block_size=request.block_size,
                blur_radius=request.blur_radius,
                sticker_path=request.sticker_path
            )

            base_name = os.path.splitext(image_data["filename"])[0]
            safe_suffix = self._sanitize_suffix(request.filename_suffix)
            output_format, ext = _resolve_censor_output_format(source_format)
            output_filename = f"{base_name}{safe_suffix}{ext}"
            output_path = self._ensure_output_path(output_folder, output_filename)

            def _write_censored_image(final_output_path: str, _overwrite_requested: bool) -> List[str]:
                return self._save_image_with_format(censored, final_output_path, output_format, {})

            write_result = save_and_reconcile_checked(
                output_path,
                _write_censored_image,
                allow_overwrite=request.allow_overwrite,
                backend_file=_svc()._BACKEND_FILE,
                validation_error_factory=self._output_validation_error,
                conflict_error_factory=self._output_conflict_error,
            )

            return self._save_response(
                output_path,
                output_filename,
                warnings=_combine_save_warnings(write_result.writer_result, write_result.warnings),
                target_existed=write_result.target_existed,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Censor save failed")
            raise HTTPException(status_code=500, detail="Save failed")

    def save_data(self, request: CensorSaveDataRequest) -> Dict[str, Any]:
        """Save base64 image data directly to disk."""
        from utils.path_validation import validate_folder_path, sanitize_filename

        is_valid, error = validate_folder_path(request.output_folder, allow_create=True)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error or "Invalid output folder")

        output_folder = self._ensure_safe_output_directory(request.output_folder)

        try:
            os.makedirs(output_folder, exist_ok=True)

            image_bytes, _ = self._decode_base64_image(request.image_data)
            image: Image.Image = Image.open(BytesIO(image_bytes))
            width, height = image.size
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail="Invalid image data")
            if width * height > _svc().MAX_SAVE_DATA_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Image dimensions are too large for canvas save (max 40 megapixels)",
                )

            safe_filename = sanitize_filename(request.filename)
            base_name = os.path.splitext(safe_filename)[0]
            output_format = self._normalize_output_format(request.output_format)
            ext = f".{output_format}"
            output_filename = f"{base_name}{ext}"
            output_path = self._ensure_output_path(output_folder, output_filename)

            save_kwargs: Dict[str, object]
            if request.metadata_option == "strip":
                image = self._strip_all_metadata(image)
                save_kwargs = {}
            else:
                save_kwargs = self._prepare_metadata_for_save(
                    image,
                    request.original_image_id,
                    request.metadata_option,
                    output_format
                )

            def _write_canvas_save(final_output_path: str, _overwrite_requested: bool) -> List[str]:
                return self._save_image_with_format(image, final_output_path, output_format, save_kwargs)

            write_result = save_and_reconcile_checked(
                output_path,
                _write_canvas_save,
                allow_overwrite=request.allow_overwrite,
                backend_file=_svc()._BACKEND_FILE,
                validation_error_factory=self._output_validation_error,
                conflict_error_factory=self._output_conflict_error,
            )

            return self._save_response(
                output_path,
                output_filename,
                warnings=_combine_save_warnings(write_result.writer_result, write_result.warnings),
                target_existed=write_result.target_existed,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Censor canvas save failed")
            raise HTTPException(status_code=500, detail="Save data failed")

    @staticmethod
    def _decode_base64_image(image_data: str) -> tuple:
        """Decode base64 image data into image bytes."""
        if ',' in image_data:
            _, data = image_data.split(',', 1)
        else:
            data = image_data
        try:
            image_bytes = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid image data") from exc
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Invalid image data")
        if len(image_bytes) > _svc().MAX_SAVE_DATA_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Canvas image data is too large to save (max 40MB)",
            )
        return image_bytes, data

    @staticmethod
    def _strip_all_metadata(image: Image.Image) -> Image.Image:
        """Strip all metadata by creating a clean copy."""
        clean_image = Image.new(image.mode, image.size)
        pixel_data_getter = getattr(image, "get_flattened_data", image.getdata)
        clean_image.putdata(list(pixel_data_getter()))
        return clean_image

    @staticmethod
    def _png_text_to_exif(original_img: Image.Image) -> Optional[bytes]:
        """Convert PNG text chunks to EXIF UserComment for non-PNG formats.

        Priority: 'parameters' (WebUI/A1111), then 'prompt' (ComfyUI).
        """
        text_chunks = {}
        for key, value in original_img.info.items():
            if isinstance(key, str) and isinstance(value, str) and key not in {
                'exif', 'icc_profile', 'dpi', 'interlace', 'gamma', 'chromaticity',
            }:
                text_chunks[key] = value

        if not text_chunks:
            return None

        comment = text_chunks.get('parameters') or text_chunks.get('prompt', '')
        if not comment:
            return None

        try:
            import struct
            ascii_prefix = b'ASCII\x00\x00\x00'
            encoded = comment.encode('utf-8', errors='replace')
            user_comment = ascii_prefix + encoded

            # Build a minimal EXIF with UserComment (tag 0x9286) in Exif IFD
            # Header: "Exif\0\0" + TIFF header (little-endian)
            bo = b'II'  # little-endian
            tiff_header = bo + struct.pack('<H', 42) + struct.pack('<I', 8)

            # IFD0: one entry pointing to Exif IFD
            ifd0_count = struct.pack('<H', 1)
            # Tag 0x8769 (ExifIFD), type LONG(4), count 1, value = offset to Exif IFD
            exif_ifd_offset_placeholder = 8 + 2 + 12 + 4  # after IFD0
            ifd0_entry = struct.pack('<HHI', 0x8769, 4, 1) + struct.pack('<I', exif_ifd_offset_placeholder)
            ifd0_next = struct.pack('<I', 0)

            # Exif IFD: one entry for UserComment
            exif_ifd_count = struct.pack('<H', 1)
            uc_data_offset = exif_ifd_offset_placeholder + 2 + 12 + 4
            exif_ifd_entry = struct.pack('<HHI', 0x9286, 7, len(user_comment)) + struct.pack('<I', uc_data_offset)
            exif_ifd_next = struct.pack('<I', 0)

            tiff_body = (ifd0_count + ifd0_entry + ifd0_next +
                         exif_ifd_count + exif_ifd_entry + exif_ifd_next +
                         user_comment)

            exif_bytes = b'Exif\x00\x00' + tiff_header + tiff_body
            return exif_bytes
        except Exception as e:
            logger.warning("Failed to build EXIF from PNG text chunks: %s", e)
            return None

    @staticmethod
    def _copy_png_text_metadata(original_img: Image.Image) -> Optional[PngImagePlugin.PngInfo]:
        """Copy PNG text metadata chunks from original image."""
        pnginfo = PngImagePlugin.PngInfo()
        has_text = False

        skip_keys = {'exif', 'icc_profile', 'dpi', 'interlace', 'gamma', 'chromaticity'}

        for key, value in original_img.info.items():
            if not isinstance(key, str):
                continue
            if key in skip_keys:
                continue

            if isinstance(value, str):
                try:
                    pnginfo.add_text(key, value)
                    has_text = True
                except Exception as e:
                    logger.warning("Could not add text chunk %s: %s", key, e)
            elif isinstance(value, bytes):
                for encoding in ['utf-8', 'latin-1']:
                    try:
                        decoded = value.decode(encoding)
                        pnginfo.add_text(key, decoded)
                        has_text = True
                        break
                    except (UnicodeDecodeError, AttributeError):
                        continue

        return pnginfo if has_text else None

    def _prepare_metadata_for_save(
        self,
        image: Image.Image,
        original_image_id: Optional[int],
        metadata_option: str,
        output_format: str
    ) -> Dict[str, object]:
        """Prepare save kwargs with metadata based on options."""
        save_kwargs: Dict[str, object] = {}

        if metadata_option == "strip":
            return save_kwargs

        if metadata_option not in {"keep", "minimal"} or not original_image_id:
            return save_kwargs

        original_image_data = db.get_image_by_id(original_image_id)
        if not original_image_data:
            return save_kwargs

        try:
            original_source_path = self._resolve_source_image_path(
                original_image_data["path"],
                image_id=original_image_id,
                action_label="Metadata copy",
            )
            # `.info` is read multiple times below, and `_png_text_to_exif`
            # / `_copy_png_text_metadata` both walk PNG text chunks on the
            # still-open file object. Wrap the whole reader block so the
            # OS handle is released deterministically afterwards (matters
            # on Windows: a leaked handle blocks subsequent move/delete of
            # the source file).
            with Image.open(original_source_path) as original_img:
                if 'icc_profile' in original_img.info:
                    save_kwargs['icc_profile'] = original_img.info['icc_profile']

                if 'dpi' in original_img.info:
                    save_kwargs['dpi'] = original_img.info['dpi']

                if metadata_option == "keep" and 'exif' in original_img.info:
                    save_kwargs['exif'] = original_img.info['exif']

                if metadata_option == "keep" and output_format == 'png':
                    pnginfo = self._copy_png_text_metadata(original_img)
                    if pnginfo:
                        save_kwargs['pnginfo'] = pnginfo

                # For non-PNG outputs, convert PNG text chunks to EXIF so SD
                # metadata survives the format change.  Many SD tools read
                # the "parameters" key from EXIF UserComment.
                if metadata_option == "keep" and output_format != 'png' and 'exif' not in save_kwargs:
                    exif_bytes = self._png_text_to_exif(original_img)
                    if exif_bytes:
                        save_kwargs['exif'] = exif_bytes

        except Exception as e:
            logger.warning("Could not copy metadata from original: %s", e)

        return save_kwargs

    @staticmethod
    def _save_image_with_format(
        image: Image.Image,
        output_path: str,
        output_format: str,
        save_kwargs: Dict[str, object]
    ) -> List[str]:
        """Save image in the specified format."""
        warnings: List[str] = []
        if output_format == 'webp':
            webp_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile']}
            _save_pillow_image_atomically(image, output_path, 'WEBP', {'quality': 95, **webp_kwargs})
        elif output_format in ['jpg', 'jpeg']:
            image = prepare_image_for_save(image, 'JPEG', warnings)
            jpeg_kwargs = {k: v for k, v in save_kwargs.items() if k in ['exif', 'icc_profile', 'dpi']}
            _save_pillow_image_atomically(image, output_path, 'JPEG', {'quality': 95, **jpeg_kwargs})
        else:
            png_kwargs = {k: v for k, v in save_kwargs.items() if k in ['pnginfo', 'dpi', 'icc_profile']}
            _save_pillow_image_atomically(image, output_path, 'PNG', png_kwargs)
        return warnings
