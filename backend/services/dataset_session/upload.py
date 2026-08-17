"""Upload/extract entry points for the Dataset Maker session service.

Moved from services/dataset_session_service.py (decomposition 2026-07). The async pair
(upload_files_for_dataset / _extract_rar_into_dataset), the soft rarfile
import, and the zip/rar decompression-bomb guards move VERBATIM except two
seam lines:
  * upload_files_for_dataset resolves _get_upload_dir through _svc() — the
    REBIND global pair _UPLOAD_DIR/_get_upload_dir stays homed on the facade
    FILE, where the reader + pin suites patch _UPLOAD_DIR
    (tests/test_dataset_session_service.py:250,272,290,298).
  * _extract_rar_into_dataset resolves _try_import_rarfile through _svc() —
    the reader suite patches it on the facade module object
    (tests/test_dataset_session_service.py:291).
"""
from __future__ import annotations

import io
import logging
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_session.allowlist import _register_session_paths
from services.dataset_session.ids_and_items import _ds_id_for_path, _read_image_metadata

# Same logger channel as the pre-split monolith (report seam: logger verbatim).
logger = logging.getLogger("services.dataset_session_service")


def _svc():
    """Resolve facade-patched seams through services.dataset_session_service at call time.

    The reader suite patches ``_UPLOAD_DIR`` (read back by the facade-homed
    ``_get_upload_dir``) and ``_try_import_rarfile`` on the facade module
    object; bare local calls here would
    make those patches miss. The lazy import avoids a facade<->submodule load
    cycle.
    """
    import services.dataset_session_service as dataset_session_service

    return dataset_session_service

# Decompression-bomb guard for uploaded ZIP/RAR archives.
#
# These are a malware / zip-bomb safeguard, NOT a dataset-size limit: the
# values mirror update_service's bounded-extraction caps
# (_MAX_UPDATE_ARCHIVE_ENTRIES / _MAX_UPDATE_ARCHIVE_UNCOMPRESSED_BYTES) and
# are deliberately generous so they sit far above any legitimate LoRA dataset
# (20k images, 2 GiB uncompressed). A real training set never trips them; a
# crafted bomb that inflates a tiny archive into terabytes does.
_MAX_ARCHIVE_ENTRIES = 20000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024

# ------------------------------ upload-files ------------------------------


def _safe_uploaded_name(name: str, fallback: str = "image") -> str:
    """Return a filename safe to create under the upload directory."""
    leaf = Path(str(name or "")).name
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in leaf).strip(" .")
    return cleaned or fallback


def _append_upload_item(dest: Path, items: List[Dict[str, Any]], *, source_kind: str) -> bool:
    """Read metadata for an uploaded/extracted image and append a session item.

    Items written here have a real on-disk path under the upload directory,
    so the export pipeline's ``beside_image`` mode can write a same-name
    ``.txt`` next to the extracted/uploaded copy. Earlier versions marked
    these as ``cache_only`` which forced the user into a separate output
    folder even though the image lives on disk.
    """
    meta = _read_image_metadata(dest)
    if meta is None:
        dest.unlink(missing_ok=True)
        return False

    width, height, thumb_b64 = meta
    stat = dest.stat()
    abs_path = str(dest.resolve())
    items.append({
        "ds_id": _ds_id_for_path(abs_path),
        "abs_path": abs_path,
        "filename": dest.name,
        "width": width,
        "height": height,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "thumb_b64": thumb_b64,
        "source_kind": source_kind,
        "sidecar_capability": "beside_image",
    })
    return True


@dataclass
class _ArchiveExtractResult:
    skipped: int = 0


def _try_import_rarfile():
    """Soft-import the optional ``rarfile`` dependency.

    Returns the module on success or ``None`` if unavailable. We cannot
    rely on ``rarfile`` being installed because it requires the system
    ``unrar`` (or ``bsdtar`` / ``7z``) binary at runtime, and shipping
    those binaries on every platform is out of scope for this release.
    """
    try:
        import rarfile  # type: ignore
    except ImportError:
        return None
    return rarfile


async def _extract_rar_into_dataset(
    upload_file,
    upload_dir: Path,
    items: List[Dict[str, Any]],
) -> _ArchiveExtractResult:
    """Extract a RAR archive into ``upload_dir`` and append image items.

    Mirrors the ZIP extraction path:
    - Each archive lands in its own subdirectory under ``upload_dir``.
    - Path-traversal entries are skipped.
    - Non-image members are silently skipped.
    - Images that fail metadata read are counted as skipped.

    Raises ``ValueError`` when the ``rarfile`` dependency is missing or
    the system extractor binary cannot be found, with a message that
    points the user at the same workaround as before (extract manually
    or convert to ZIP).
    """
    rarfile = _svc()._try_import_rarfile()
    if rarfile is None:
        raise ValueError(
            "RAR archives need the optional 'rarfile' dependency and a system "
            "'unrar' binary. Install them, or extract the RAR to a folder / "
            "convert it to ZIP, then import again."
        )

    filename = upload_file.filename or "archive.rar"
    archive_dir = upload_dir / f"{_safe_uploaded_name(Path(filename).stem, 'archive')}_{uuid.uuid4().hex[:8]}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    rar_buffer_path = archive_dir / f"_source_{uuid.uuid4().hex[:8]}.rar"
    try:
        if hasattr(upload_file, "file") and upload_file.file is not None:
            upload_file.file.seek(0)
            with rar_buffer_path.open("wb") as out:
                shutil.copyfileobj(upload_file.file, out, length=1024 * 1024)
        else:
            content = await upload_file.read()
            rar_buffer_path.write_bytes(content)

        skipped = 0
        try:
            with rarfile.RarFile(str(rar_buffer_path)) as rf:
                members = rf.infolist()
                if len(members) > _MAX_ARCHIVE_ENTRIES:
                    raise ValueError(
                        "RAR archive contains too many entries "
                        f"(> {_MAX_ARCHIVE_ENTRIES}); refusing to extract a "
                        "possible decompression bomb. / RAR 内含过多文件，已拒绝解压。"
                    )
                total_uncompressed_bytes = 0
                for member in members:
                    if member.isdir():
                        continue
                    total_uncompressed_bytes += int(getattr(member, "file_size", 0) or 0)
                    if total_uncompressed_bytes > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            "RAR archive uncompressed size exceeds the safe "
                            f"limit ({_MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes); "
                            "refusing to extract a possible decompression bomb. "
                            "/ RAR 解压后体积过大，已拒绝解压。"
                        )
                    raw_name = (member.filename or "").replace("\\", "/")
                    if not raw_name:
                        skipped += 1
                        continue
                    posix = PurePosixPath(raw_name)
                    if posix.is_absolute() or ".." in posix.parts:
                        skipped += 1
                        continue
                    suffix = Path(posix.name).suffix.lower()
                    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
                        continue
                    safe_name = _safe_uploaded_name(posix.name, f"image{suffix or '.png'}")
                    dest = archive_dir / safe_name
                    counter = 1
                    while dest.exists():
                        dest = archive_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
                        counter += 1
                    try:
                        with rf.open(member) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out, length=1024 * 1024)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("rar-extract: copy failed for %s: %s", raw_name, exc)
                        skipped += 1
                        continue
                    if not _append_upload_item(dest, items, source_kind="rar_extract"):
                        skipped += 1
        except ValueError:
            # Decompression-bomb guard (and multi-volume / path errors below)
            # must surface as a clear client error, not be swallowed by the
            # broad BadRarFile fallback when rarfile lacks a BadRarFile attr.
            raise
        except getattr(rarfile, "BadRarFile", Exception) as exc:  # noqa: BLE001
            logger.warning("rar-extract: bad archive %s: %s", filename, exc)
            skipped += 1
        except getattr(rarfile, "NeedFirstVolume", Exception):
            raise ValueError(
                "Multi-part RAR archives must be uploaded together starting from "
                "the first volume (.part1.rar). Extract the archive locally and "
                "import the folder instead."
            )
        except FileNotFoundError as exc:
            # rarfile raises this when the underlying unrar binary is missing.
            raise ValueError(
                "RAR extraction needs a system 'unrar' (or 'bsdtar') binary. "
                "Install it, extract the RAR to a folder, or convert it to ZIP."
            ) from exc
    finally:
        rar_buffer_path.unlink(missing_ok=True)

    return _ArchiveExtractResult(skipped=skipped)


async def upload_files_for_dataset(files, *, recursive: bool = True) -> Dict[str, Any]:
    """Save uploaded files to a temp directory and return scan-like items.

    Accepts a list of FastAPI UploadFile objects. Returns the same shape
    as scan_folder_for_dataset so the frontend can use addLocalItems().
    """
    upload_dir = _svc()._get_upload_dir()
    items: List[Dict[str, Any]] = []
    skipped = 0
    truncated = False

    for upload_file in files:
        filename = upload_file.filename or "unknown.png"
        ext = Path(filename).suffix.lower()

        if ext == ".rar":
            extracted = await _extract_rar_into_dataset(upload_file, upload_dir, items)
            skipped += extracted.skipped
            continue

        if ext == ".zip":
            archive_dir = upload_dir / f"{_safe_uploaded_name(Path(filename).stem, 'archive')}_{uuid.uuid4().hex[:8]}"
            archive_dir.mkdir(parents=True, exist_ok=True)
            try:
                if hasattr(upload_file, "file") and upload_file.file is not None:
                    upload_file.file.seek(0)
                    zip_source = upload_file.file
                else:
                    zip_source = io.BytesIO(await upload_file.read())

                with zipfile.ZipFile(zip_source) as zf:
                    members = zf.infolist()
                    if len(members) > _MAX_ARCHIVE_ENTRIES:
                        raise ValueError(
                            "ZIP archive contains too many entries "
                            f"(> {_MAX_ARCHIVE_ENTRIES}); refusing to extract a "
                            "possible decompression bomb. / ZIP 内含过多文件，已拒绝解压。"
                        )
                    total_uncompressed_bytes = 0
                    for member in members:
                        if member.is_dir():
                            continue
                        total_uncompressed_bytes += member.file_size
                        if total_uncompressed_bytes > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                            raise ValueError(
                                "ZIP archive uncompressed size exceeds the safe "
                                f"limit ({_MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes); "
                                "refusing to extract a possible decompression bomb. "
                                "/ ZIP 解压后体积过大，已拒绝解压。"
                            )
                        raw_name = member.filename.replace("\\", "/")
                        posix = PurePosixPath(raw_name)
                        if posix.is_absolute() or ".." in posix.parts:
                            skipped += 1
                            continue
                        if not recursive and len(posix.parts) > 1:
                            continue
                        suffix = Path(posix.name).suffix.lower()
                        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
                            continue
                        safe_name = _safe_uploaded_name(posix.name, f"image{suffix or '.png'}")
                        dest = archive_dir / safe_name
                        counter = 1
                        while dest.exists():
                            dest = archive_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
                            counter += 1
                        with zf.open(member) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out, length=1024 * 1024)
                        if not _append_upload_item(dest, items, source_kind="zip_extract"):
                            skipped += 1
            except zipfile.BadZipFile:
                skipped += 1
            continue

        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            skipped += 1
            continue

        # Write to disk with a unique name to avoid collisions
        safe_filename = _safe_uploaded_name(filename, "image.png")
        dest = upload_dir / safe_filename
        counter = 1
        while dest.exists():
            stem = Path(safe_filename).stem
            dest = upload_dir / f"{stem}_{counter}{ext}"
            counter += 1

        if hasattr(upload_file, "file") and upload_file.file is not None:
            upload_file.file.seek(0)
            with dest.open("wb") as out:
                shutil.copyfileobj(upload_file.file, out, length=1024 * 1024)
        else:
            content = await upload_file.read()
            dest.write_bytes(content)

        if not _append_upload_item(dest, items, source_kind="uploaded_file"):
            skipped += 1

    if not items:
        raise ValueError("No valid image files in the upload.")

    # Trust the uploaded/extracted paths we just wrote to disk so the
    # local-thumbnail endpoint can serve them.
    _register_session_paths(item.get("abs_path") for item in items if item.get("abs_path"))

    return {"items": items, "skipped_unreadable": skipped, "truncated": truncated}
