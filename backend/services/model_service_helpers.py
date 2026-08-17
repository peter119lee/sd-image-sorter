"""Pure helpers for the model service (split from services/model_service.py, 2026-07).

Functions moved verbatim from services/model_service.py: the safe single-root zip extractor,
the existing-file/tree materializers, the artist URL builders, and the two
Civitai rich-error payload builders. The ONLY non-verbatim edits (see the
split manifest): reads of facade-bound names (ARTIST_LSNET_RUNTIME_ZIP_URL,
PRIVACY_YOLO_PAGE_URL, _artist_resolve_url) resolve through _svc() at call
time, because tests patch and read those names on the facade module object
-- a bare re-import here would freeze independent bindings those patches
silently miss.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict


def _svc():
    """Resolve facade-patched seams through services.model_service at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy import
    avoids a facade<->submodule load cycle.
    """
    import services.model_service as model_service

    return model_service


def _safe_extract_single_root_zip(zip_path: Path, target_dir: Path, *, max_entries: int, max_bytes: int) -> Path:
    with tempfile.TemporaryDirectory(prefix=f"{target_dir.name}-extract-") as tmp_dir:
        extract_dir = Path(tmp_dir) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_root = extract_dir.resolve()
        total_uncompressed_bytes = 0
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise ValueError("Zip contains too many entries to extract safely")
            for member in members:
                normalized_name = str(member.filename or "").replace("\\", "/").strip()
                relative_name = PurePosixPath(normalized_name)
                if (
                    not normalized_name
                    or relative_name.is_absolute()
                    or normalized_name[:2].endswith(":")
                    or ".." in relative_name.parts
                ):
                    raise ValueError(f"Zip contains an unsafe path: {member.filename}")
                member_path = (extract_root / relative_name).resolve()
                try:
                    member_path.relative_to(extract_root)
                except ValueError as exc:
                    raise ValueError(f"Zip contains an unsafe path: {member.filename}") from exc
                if not member.is_dir():
                    total_uncompressed_bytes += member.file_size
                    if total_uncompressed_bytes > max_bytes:
                        raise ValueError("Zip uncompressed size exceeds the safe extraction limit")
            archive.extractall(extract_root)

        extracted_roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(extracted_roots) != 1:
            raise ValueError("Zip must contain exactly one root directory")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(extracted_roots[0]), str(target_dir))
    return target_dir


def _artist_runtime_url() -> str:
    return os.environ.get("SD_IMAGE_SORTER_ARTIST_RUNTIME_ZIP_URL") or _svc().ARTIST_LSNET_RUNTIME_ZIP_URL


def _artist_resolve_url(repo_id: str, filename: str, *, hf_base: str) -> str:
    return f"{hf_base.rstrip('/')}/{repo_id}/resolve/main/{filename}"


def _artist_checkpoint_url(repo_id: str, filename: str, *, hf_base: str) -> str:
    if filename == "class_mapping.csv":
        configured = os.environ.get("SD_IMAGE_SORTER_ARTIST_CLASS_MAPPING_URL")
    else:
        configured = os.environ.get("SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL")
    return configured or _svc()._artist_resolve_url(repo_id, filename, hf_base=hf_base)


def _materialize_existing_file(source: Path, dest: Path) -> bool:
    if not source.exists() or dest.exists():
        return dest.exists()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)
    return True


def _copy_existing_tree(source: Path, dest: Path, marker_name: str) -> bool:
    if not (source / marker_name).exists():
        return False
    if (dest / marker_name).exists():
        return True
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return True


def build_civitai_auth_error(target_dir: Path) -> Dict[str, Any]:
    target_dir_resolved = str(target_dir.resolve())
    return {
        "error": "Civitai login required for the Privacy YOLO download.",
        "type": "CivitaiLoginRequired",
        "message": (
            "Privacy YOLO cannot be downloaded automatically because Civitai now requires a signed-in browser session."
        ),
        "provider": "Civitai",
        "model_id": "censor-legacy",
        "manual_steps": [
            f"Open {_svc().PRIVACY_YOLO_PAGE_URL} in a browser and sign in to Civitai.",
            "Download the Privacy YOLO archive (.zip) from the model page.",
            f"Extract the archive into {target_dir_resolved}.",
            "Restart SD Image Sorter or reopen the Models panel so the files are detected.",
        ],
        "target_dir": target_dir_resolved,
        "external_url": _svc().PRIVACY_YOLO_PAGE_URL,
    }


def build_privacy_yolo_prepare_error(target_dir: Path, reason: str) -> Dict[str, Any]:
    target_dir_resolved = str(target_dir.resolve())
    return {
        "error": "Privacy YOLO preparation failed.",
        "type": "ModelPreparationFailed",
        "message": (
            "Privacy YOLO could not be prepared automatically because the download or archive verification failed."
        ),
        "provider": "Civitai",
        "model_id": "censor-legacy",
        "reason": reason,
        "manual_steps": [
            f"Open {_svc().PRIVACY_YOLO_PAGE_URL} in a browser and sign in to Civitai.",
            "Download the Privacy YOLO archive (.zip) from the model page.",
            f"Extract the archive into {target_dir_resolved}.",
            "Restart SD Image Sorter or reopen the Models panel so the files are detected.",
        ],
        "target_dir": target_dir_resolved,
        "external_url": _svc().PRIVACY_YOLO_PAGE_URL,
    }
