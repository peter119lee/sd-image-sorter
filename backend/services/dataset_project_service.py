"""Business operations for durable Dataset Maker projects."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import db_dataset_projects as project_db
from caption_format import caption_format_for_storage
from config import ALLOWED_IMAGE_EXTENSIONS
from services.caption_dialect import caption_dialect_advisory
from services.dataset_session.allowlist import (
    _register_thumbnail_paths,
    project_save_authorization_status,
)
from services.dataset_project_models import (
    DatasetProjectCreateRequest,
    DatasetProjectDeleteResponse,
    DatasetProjectLibraryItemRequest,
    DatasetProjectListResponse,
    DatasetProjectLocalItemRequest,
    DatasetProjectLocalItemResponse,
    DatasetProjectResponse,
    DatasetProjectRevisionRequest,
    DatasetProjectUpdateRequest,
    DatasetProjectSettingsV1,
)
from services.dataset_sidecar import (
    MAX_DATASET_SIDECAR_BYTES,
    read_dataset_sidecar,
)
from utils.path_validation import normalize_user_path
from utils.source_paths import indexed_image_path_match_key


DatasetProjectError = project_db.DatasetProjectError
DatasetProjectImageNotFoundError = project_db.DatasetProjectImageNotFoundError
DatasetProjectNameConflictError = project_db.DatasetProjectNameConflictError
DatasetProjectNotFoundError = project_db.DatasetProjectNotFoundError
DatasetProjectRevisionConflictError = project_db.DatasetProjectRevisionConflictError
DatasetProjectSourceValidationError = project_db.DatasetProjectSourceValidationError
DatasetProjectStateConflictError = project_db.DatasetProjectStateConflictError


class DatasetProjectSourceIdentityConflictError(project_db.DatasetProjectError):
    def __init__(self, project_id: int | None, path: str, reason: str):
        self.project_id = project_id
        self.path = path
        self.reason = reason
        super().__init__(
            f"Dataset project local source {path!r} identity conflict: {reason}"
        )


def _name_key(name: str) -> str:
    return name.casefold()


def _capture_local_source(
    item: DatasetProjectLocalItemRequest,
    trusted_sources: dict[str, tuple[int, str, str, str]],
    project_id: int | None,
) -> project_db.DatasetProjectLocalItemInput:
    requested_path = item.path
    try:
        candidate = Path(normalize_user_path(requested_path))
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise DatasetProjectSourceValidationError(
            requested_path,
            "the file does not exist",
        ) from error
    except (OSError, ValueError) as error:
        raise DatasetProjectSourceValidationError(
            requested_path,
            f"the path cannot be resolved: {error}",
        ) from error

    if resolved.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise DatasetProjectSourceValidationError(
            requested_path,
            f"extension {resolved.suffix.lower()!r} is not a supported image type",
        )
    try:
        source_stat = os.stat(resolved)
    except OSError as error:
        raise DatasetProjectSourceValidationError(
            requested_path,
            f"the file cannot be inspected: {error}",
        ) from error
    if not stat.S_ISREG(source_stat.st_mode):
        raise DatasetProjectSourceValidationError(
            requested_path,
            "the path is not a regular file",
        )

    canonical_path = str(resolved)
    path_key = indexed_image_path_match_key(canonical_path)
    captured_identity = (
        source_stat.st_size,
        str(source_stat.st_mtime_ns),
        str(source_stat.st_dev),
        str(source_stat.st_ino),
    )
    saved_identity = trusted_sources.get(path_key)
    authorization_status = project_save_authorization_status(
        canonical_path,
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_dev,
        source_stat.st_ino,
    )
    identity_is_authorized = (
        saved_identity == captured_identity
        or authorization_status == "authorized"
    )
    if not identity_is_authorized and (
        saved_identity is not None or authorization_status == "changed"
    ):
        raise DatasetProjectSourceIdentityConflictError(
            project_id,
            canonical_path,
            (
                "the file identity changed after it was imported or saved; "
                "explicitly import the current file before saving"
            ),
        )
    if not identity_is_authorized:
        raise DatasetProjectSourceValidationError(
            requested_path,
            (
                "the file was not imported by the active Dataset Maker session "
                "and does not match this project's saved identity"
            ),
        )
    return {
        "item_type": "local",
        "path": canonical_path,
        "path_key": path_key,
        "size": captured_identity[0],
        "mtime_ns": captured_identity[1],
        "device": captured_identity[2],
        "inode": captured_identity[3],
    }


def _capture_project_items(
    request: DatasetProjectCreateRequest | DatasetProjectUpdateRequest,
    trusted_sources: dict[str, tuple[int, str, str, str]],
    project_id: int | None,
) -> list[project_db.DatasetProjectItemInput]:
    captured: list[project_db.DatasetProjectItemInput] = []
    local_path_keys: set[str] = set()
    for item in request.items:
        if isinstance(item, DatasetProjectLibraryItemRequest):
            captured.append({"item_type": "library", "image_id": item.image_id})
            continue
        local_source = _capture_local_source(item, trusted_sources, project_id)
        path_key = local_source["path_key"]
        if path_key in local_path_keys:
            raise DatasetProjectSourceValidationError(
                local_source["path"],
                "the canonical path appears more than once in the project",
            )
        local_path_keys.add(path_key)
        captured.append(local_source)
    return captured


def _trusted_local_sources(
    record: project_db.DatasetProjectRecord,
) -> dict[str, tuple[int, str, str, str]]:
    return {
        indexed_image_path_match_key(item["path"]): (
            item["size"],
            item["mtime_ns"],
            item["device"],
            item["inode"],
        )
        for item in record["items"]
        if item["item_type"] == "local"
    }


def _read_project_sidecar(path: str) -> str | None:
    try:
        return read_dataset_sidecar(path, MAX_DATASET_SIDECAR_BYTES)
    except ValueError as error:
        raise DatasetProjectSourceValidationError(
            path,
            f"the caption sidecar is invalid: {error}",
        ) from error


def _captured_sidecar_snapshot(
    items: list[project_db.DatasetProjectItemInput],
) -> dict[str, str | None]:
    return {
        item["path"]: _read_project_sidecar(item["path"])
        for item in items
        if item["item_type"] == "local"
    }


def _record_sidecar_snapshot(
    record: project_db.DatasetProjectRecord,
) -> dict[str, str | None]:
    return {
        item["path"]: _read_project_sidecar(item["path"])
        for item in record["items"]
        if item["item_type"] == "local"
        and item["source_status"] == "available"
    }


def _caption_dialect_fields(
    caption: str | None,
    target_model: str,
) -> dict[str, object]:
    """Label the caption's format and say whether it suits the project's target.

    A local item has no database row, so the format is derived from the ``.txt``
    text just read from disk rather than from ``images.sidecar_caption_format``.
    This is the join that ``target_model`` never had: a ``krea2`` project whose
    sources are Booru tag lists is now flagged per item instead of silently
    exporting tag captions to a natural-language-first target. The caption text
    in the same item is returned in full regardless of what the label says.
    """
    caption_format = caption_format_for_storage(caption)
    advisory = caption_dialect_advisory(target_model, caption_format)
    return {
        "sidecar_caption_format": caption_format,
        "caption_dialect": None if advisory is None else {
            "code": advisory.code,
            "target_model": advisory.target_model,
            "expected_dialect": advisory.expected_dialect,
            "caption_format": advisory.caption_format,
            "convert": advisory.convert,
            "message": advisory.message,
            "action": advisory.action,
        },
    }


def _project_response(
    record: project_db.DatasetProjectRecord,
    sidecar_snapshot: dict[str, str | None] | None,
) -> DatasetProjectResponse:
    settings = DatasetProjectSettingsV1.model_validate_json(
        record["settings_json"],
        strict=True,
    )
    captions = (
        _record_sidecar_snapshot(record)
        if sidecar_snapshot is None
        else sidecar_snapshot
    )
    response_items = []
    for item in record["items"]:
        if item["item_type"] != "local":
            response_items.append(item)
            continue
        caption = (
            captions[item["path"]]
            if item["source_status"] == "available"
            else None
        )
        response_items.append({
            **item,
            "sidecar_caption": caption,
            **_caption_dialect_fields(caption, settings.target_model),
        })
    response = DatasetProjectResponse.model_validate(
        {
            "id": record["id"],
            "name": record["name"],
            "revision": record["revision"],
            "archived_at": record["archived_at"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "missing_image_ids": record["missing_image_ids"],
            "items": response_items,
            "settings": settings,
        },
        strict=True,
    )
    available_paths = [
        item.path
        for item in response.items
        if isinstance(item, DatasetProjectLocalItemResponse)
        and item.source_status == "available"
    ]
    _register_thumbnail_paths(available_paths)
    return response


def list_active_dataset_projects() -> DatasetProjectListResponse:
    return DatasetProjectListResponse.model_validate(
        {"projects": project_db.list_dataset_project_records(False)},
        strict=True,
    )


def list_archived_dataset_projects() -> DatasetProjectListResponse:
    return DatasetProjectListResponse.model_validate(
        {"projects": project_db.list_dataset_project_records(True)},
        strict=True,
    )


def get_dataset_project(project_id: int) -> DatasetProjectResponse:
    return _project_response(
        project_db.get_dataset_project_record(project_id),
        None,
    )


def create_dataset_project(
    request: DatasetProjectCreateRequest,
) -> DatasetProjectResponse:
    captured_items = _capture_project_items(request, {}, None)
    sidecar_snapshot = _captured_sidecar_snapshot(captured_items)
    record = project_db.create_dataset_project_record(
        request.name,
        _name_key(request.name),
        captured_items,
        request.settings.model_dump_json(),
    )
    return _project_response(record, sidecar_snapshot)


def update_dataset_project(
    project_id: int,
    request: DatasetProjectUpdateRequest,
) -> DatasetProjectResponse:
    current_record = project_db.require_dataset_project_revision(
        project_id,
        request.expected_revision,
    )
    captured_items = _capture_project_items(
        request,
        _trusted_local_sources(current_record),
        project_id,
    )
    sidecar_snapshot = _captured_sidecar_snapshot(captured_items)
    record = project_db.update_dataset_project_record(
        project_id,
        request.expected_revision,
        request.name,
        _name_key(request.name),
        captured_items,
        request.settings.model_dump_json(),
    )
    return _project_response(record, sidecar_snapshot)


def archive_dataset_project(
    project_id: int,
    request: DatasetProjectRevisionRequest,
) -> DatasetProjectResponse:
    current_record = project_db.require_dataset_project_revision(
        project_id,
        request.expected_revision,
    )
    sidecar_snapshot = _record_sidecar_snapshot(current_record)
    record = project_db.archive_dataset_project_record(
        project_id,
        request.expected_revision,
    )
    return _project_response(record, sidecar_snapshot)


def restore_dataset_project(
    project_id: int,
    request: DatasetProjectRevisionRequest,
) -> DatasetProjectResponse:
    current_record = project_db.require_dataset_project_revision(
        project_id,
        request.expected_revision,
    )
    sidecar_snapshot = _record_sidecar_snapshot(current_record)
    record = project_db.restore_dataset_project_record(
        project_id,
        request.expected_revision,
    )
    return _project_response(record, sidecar_snapshot)


def delete_dataset_project(
    project_id: int,
    request: DatasetProjectRevisionRequest,
) -> DatasetProjectDeleteResponse:
    project_db.delete_dataset_project_record(
        project_id,
        request.expected_revision,
    )
    return DatasetProjectDeleteResponse(deleted=True, project_id=project_id)
