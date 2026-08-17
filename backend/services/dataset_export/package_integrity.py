"""Streaming inventory and integrity verification for trainer export packages."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, ValidationError

from config import ALLOWED_IMAGE_EXTENSIONS
from services.dataset_export._constants import (
    EXPORT_MANIFEST_FILENAME,
    PACKAGE_HASH_CHUNK_SIZE,
    PACKAGE_INVENTORY_FILENAME,
    PACKAGE_LOCK_FILENAME,
    PACKAGE_MANIFEST_SCHEMA,
    PACKAGE_MANIFEST_VERSION,
)
from services.dataset_export.anima_contract import (
    AnimaTrainerContractError,
    get_anima_trainer_contract,
    validate_anima_artifact_completeness,
    validate_anima_package_options,
    validate_anima_toml_text,
)
from services.dataset_export.kohya_contract import (
    KohyaTrainerContractError,
    get_kohya_trainer_contract,
    validate_kohya_package_options,
    validate_kohya_toml_text,
)
from services.dataset_export.models import (
    DatasetExportRequest,
    DatasetPackageAnnotationSnapshot,
    DatasetPackageFrozenDraftAnnotation,
    DatasetPackageRevisionAnnotation,
    DatasetPackageArtifact,
    DatasetPackageCounts,
    DatasetPackageInventoryRecord,
    DatasetPackageInventorySummary,
    DatasetPackageManifest,
    DatasetPackageOptions,
    DatasetPackageSourceIdentity,
    DatasetPackageTrainer,
    DatasetPackageVerificationIssue,
    DatasetPackageVerificationRequest,
    DatasetPackageVerificationResponse,
)
from services.dataset_export.annotations import AnnotationProvenance
from utils.atomic_staging import create_staging_sibling, publish_staging_file
from utils.path_validation import normalize_user_path, validate_folder_path


class PackageIntegrityError(RuntimeError):
    """Raised when a package artifact cannot be recorded or published safely."""


class PackageOwnershipError(PackageIntegrityError):
    """Raised when an export target is not owned by a recognized package run."""


class PackageLockError(PackageIntegrityError):
    """Raised when another Package v2 writer owns the output root."""


class PackageFileLock:
    """Cross-platform non-blocking advisory lock kept for one package run."""

    def __init__(self, output_folder: Path) -> None:
        self._path = output_folder / PACKAGE_LOCK_FILENAME
        self._handle: Optional[IO[bytes]] = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise PackageLockError(f"Package lock is already held: path={self._path}")
        try:
            existing = self._path.lstat()
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise PackageLockError(
                "Package lock target could not be inspected: "
                f"path={self._path}, error_type={type(exc).__name__}, error={exc}"
            ) from exc
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise PackageLockError(
                "Package lock target must be a regular non-symlink file: "
                f"path={self._path}"
            )
        descriptor: Optional[int] = None
        handle: Optional[IO[bytes]] = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._path, flags, 0o600)
            handle = os.fdopen(descriptor, "r+b")
            descriptor = None
            opened = os.fstat(handle.fileno())
            linked = self._path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(linked.st_mode)
                or not os.path.samestat(opened, linked)
            ):
                raise PackageLockError(
                    "Package lock target must be a regular non-symlink file: "
                    f"path={self._path}"
                )
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                )
            linked_after_lock = self._path.lstat()
            if (
                not stat.S_ISREG(linked_after_lock.st_mode)
                or not os.path.samestat(opened, linked_after_lock)
            ):
                raise PackageLockError(
                    "Package lock target changed while it was being acquired: "
                    f"path={self._path}"
                )
        except PackageLockError:
            if descriptor is not None:
                os.close(descriptor)
            if handle is not None:
                handle.close()
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            if handle is not None:
                handle.close()
            raise PackageLockError(
                "Package output root is already locked by another writer: "
                f"path={self._path}, error_type={type(exc).__name__}, error={exc}"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        unlock_error: Optional[OSError] = None
        close_error: Optional[OSError] = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    handle.fileno(),
                    fcntl.LOCK_UN,  # type: ignore[attr-defined]
                )
        except OSError as exc:
            unlock_error = exc
        try:
            handle.close()
        except OSError as exc:
            close_error = exc
        if unlock_error is not None or close_error is not None:
            raise PackageIntegrityError(
                "Package lock could not be released cleanly: "
                f"path={self._path}, unlock_error={unlock_error}, "
                f"close_error={close_error}"
            )


class _LegacyManifestCounts(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    total: int
    exported: int
    skipped: int
    failed: int


class _LegacyOwnedManifest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    manifest_version: Literal[1]
    generated_at: float
    generated_at_iso: str
    status: str
    counts: _LegacyManifestCounts


def package_requested(request: DatasetExportRequest) -> bool:
    return str(request.trainer_config).strip().lower() in {
        "kohya_toml",
        "anima_lora_toml",
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(PACKAGE_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise PackageIntegrityError(
            "Package artifact could not be hashed: "
            f"path={path}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    return digest.hexdigest()


def _read_owned_manifest(path: Path) -> Optional[DatasetPackageManifest]:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PackageOwnershipError(
            "Package manifest target could not be inspected: "
            f"path={path}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    if not stat.S_ISREG(entry.st_mode):
        raise PackageOwnershipError(
            f"Package manifest target is not a regular file: path={path}"
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackageOwnershipError(
            "Package manifest could not be inspected safely: "
            f"path={path}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    try:
        return DatasetPackageManifest.model_validate_json(content)
    except ValidationError:
        try:
            _LegacyOwnedManifest.model_validate_json(content)
        except ValidationError as exc:
            raise PackageOwnershipError(
                "Existing export_manifest.json is not a recognized SD Image Sorter "
                f"manifest; move or rename it before exporting: path={path}"
            ) from exc
        return None


def preflight_package_targets(
    output_folder: Optional[Path],
    request: DatasetExportRequest,
) -> None:
    """Reject unknown manifest or inventory targets before export mutation."""
    if output_folder is None or not package_requested(request) or not output_folder.exists():
        return
    mask_export = str(request.mask_export).strip().lower()
    mask_path = output_folder / "mask"
    if mask_export != "none" and (
        mask_path.is_symlink()
        or (mask_path.exists() and not mask_path.is_dir())
    ):
        raise PackageOwnershipError(
            "Package mask directory must be a regular directory inside the "
            f"output root: path={mask_path}"
        )
    manifest_path = output_folder / EXPORT_MANIFEST_FILENAME
    inventory_path = output_folder / PACKAGE_INVENTORY_FILENAME
    parsed_manifest = _read_owned_manifest(manifest_path)
    try:
        inventory_entry = inventory_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PackageOwnershipError(
            "Package inventory target could not be inspected: "
            f"path={inventory_path}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    if not stat.S_ISREG(inventory_entry.st_mode):
        raise PackageOwnershipError(
            f"Package inventory target is not a regular file: path={inventory_path}"
        )
    if parsed_manifest is None:
        raise PackageOwnershipError(
            "Existing export_inventory.jsonl has no recognized Package v2 owner; "
            f"move or rename it before exporting: path={inventory_path}"
        )
    # A building/incomplete manifest may accompany an inventory published just
    # before a crash. Its strict v2 producer identity is enough for a rerun to
    # replace that recovery artifact safely.


def _atomic_write_text(target: Path, content: str) -> None:
    """Stage package metadata beside its target, then publish it over the target.

    Staging and publishing both go through ``utils.atomic_staging`` rather than
    ``tempfile`` plus a bare ``os.replace``. ``mkstemp`` read an unwritable
    destination folder's refusal as a name collision and retried it up to
    ``tempfile.TMP_MAX`` — measured at 2,147,483,647 on this interpreter, not the
    10,000 the docs imply — so aiming a package export at a folder it cannot
    write to hung instead of reporting the refusal; ``os.replace`` publishes a
    new inode, which would sever a hard link on the destination.
    """
    encoded = content.encode("utf-8")
    descriptor: Optional[int] = None
    temporary: Optional[Path] = None
    write_error: Optional[OSError] = None
    cleanup_error: Optional[OSError] = None
    try:
        temporary, descriptor = create_staging_sibling(target)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            written = handle.write(encoded)
            if written != len(encoded):
                raise OSError(
                    f"short write: expected={len(encoded)}, written={written}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        if target.is_symlink():
            target.unlink()
        publish_staging_file(temporary, target)
        temporary = None
    except OSError as exc:
        write_error = exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_error = exc
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = exc
    if write_error is not None or cleanup_error is not None:
        primary = write_error if write_error is not None else cleanup_error
        raise PackageIntegrityError(
            "Package metadata could not be written atomically: "
            f"target={target}, error_type={type(primary).__name__}, "
            f"error={primary}, cleanup_error={cleanup_error}"
        ) from primary


def _prepare_package_parent(
    package_root: Path,
    target: Path,
) -> None:
    try:
        relative_target = target.relative_to(package_root)
    except ValueError as exc:
        raise PackageIntegrityError(
            "Package target is outside the output root: "
            f"root={package_root}, target={target}"
        ) from exc
    if not relative_target.parts or any(
        part in {"", ".", ".."}
        for part in relative_target.parts
    ):
        raise PackageIntegrityError(
            "Package target has an invalid relative path: "
            f"root={package_root}, target={target}"
        )
    try:
        resolved_root = package_root.resolve(strict=True)
    except OSError as exc:
        raise PackageIntegrityError(
            "Package output root could not be resolved: "
            f"root={package_root}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    current = package_root
    for part in relative_target.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise PackageIntegrityError(
                "Package target parent must not be a symlink: "
                f"root={package_root}, parent={current}, target={target}"
            )
        try:
            current.mkdir()
        except FileExistsError:
            if not current.is_dir() or current.is_symlink():
                raise PackageIntegrityError(
                    "Package target parent must be a regular directory: "
                    f"root={package_root}, parent={current}, target={target}"
                )
        except OSError as exc:
            raise PackageIntegrityError(
                "Package target parent could not be created: "
                f"root={package_root}, parent={current}, target={target}, "
                f"error_type={type(exc).__name__}, error={exc}"
            ) from exc
        if current.is_symlink() or not current.is_dir():
            raise PackageIntegrityError(
                "Package target parent must be a regular directory: "
                f"root={package_root}, parent={current}, target={target}"
            )
    try:
        resolved_parent = target.parent.resolve(strict=True)
        resolved_parent.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise PackageIntegrityError(
            "Package target parent escaped the output root: "
            f"root={package_root}, parent={target.parent}, target={target}"
        ) from exc


def copy_package_file_atomic(
    source: Path,
    target: Path,
    package_root: Path,
) -> None:
    """Copy through a unique sibling temp so a target symlink is never followed.

    This is the one package writer that lands on a file the user may already
    own: ``overwrite_policy="overwrite"`` republishes over images already in the
    chosen folder, and hardlinking a large image set is a real space-saving
    practice. So publishing goes through ``utils.atomic_staging``, which keeps a
    linked destination's other names pointing at the newly published bytes
    instead of leaving them on the pre-export copy. Staging goes through the same
    module because ``tempfile`` retried an unwritable folder up to
    ``tempfile.TMP_MAX`` times instead of reporting its refusal.
    """
    _prepare_package_parent(package_root, target)
    descriptor: Optional[int] = None
    temporary: Optional[Path] = None
    copy_error: Optional[OSError] = None
    cleanup_error: Optional[OSError] = None
    try:
        temporary, descriptor = create_staging_sibling(target)
        os.close(descriptor)
        descriptor = None
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        if target.is_symlink():
            target.unlink()
        publish_staging_file(temporary, target)
        temporary = None
    except OSError as exc:
        copy_error = exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_error = exc
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = exc
    if copy_error is not None or cleanup_error is not None:
        primary = copy_error if copy_error is not None else cleanup_error
        raise PackageIntegrityError(
            "Package file could not be copied atomically: "
            f"source={source}, target={target}, error_type={type(primary).__name__}, "
            f"error={primary}, cleanup_error={cleanup_error}"
        ) from primary


def write_package_text_atomic(
    target: Path,
    content: str,
    package_root: Path,
) -> None:
    _prepare_package_parent(package_root, target)
    _atomic_write_text(target, content)


def read_package_manifest(output_folder: Path) -> DatasetPackageManifest:
    manifest_path = output_folder / EXPORT_MANIFEST_FILENAME
    parsed = _read_owned_manifest(manifest_path)
    if parsed is None:
        raise PackageIntegrityError(
            f"Package v2 manifest is missing: path={manifest_path}"
        )
    return parsed


def _require_active_building_run(output_folder: Path, run_id: str) -> None:
    current = read_package_manifest(output_folder)
    if current.run_id != run_id or current.package_status != "building":
        raise PackageOwnershipError(
            "The active package run changed before finalize: "
            f"expected_run_id={run_id}, observed_run_id={current.run_id}, "
            f"observed_status={current.package_status}"
        )


def publish_package_manifest(
    output_folder: Path,
    manifest: DatasetPackageManifest,
    *,
    expected_active_run_id: Optional[str],
) -> str:
    if expected_active_run_id is not None:
        _require_active_building_run(output_folder, expected_active_run_id)
        if manifest.run_id != expected_active_run_id:
            raise PackageOwnershipError(
                "Package final manifest run id does not match its active run: "
                f"expected={expected_active_run_id}, received={manifest.run_id}"
            )
    target = output_folder / EXPORT_MANIFEST_FILENAME
    _atomic_write_text(target, manifest.model_dump_json(indent=2))
    return str(target)


def _retire_owned_package_manifest(output_folder: Path) -> Optional[Path]:
    target = output_folder / EXPORT_MANIFEST_FILENAME
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PackageOwnershipError(
            "Package manifest target could not be inspected before retirement: "
            f"path={target}, error_type={type(exc).__name__}, error={exc}"
        ) from exc
    _read_owned_manifest(target)
    retired = output_folder / (
        f"{EXPORT_MANIFEST_FILENAME}.retired.{uuid.uuid4().hex}.json"
    )
    try:
        os.replace(str(target), str(retired))
    except OSError as exc:
        raise PackageIntegrityError(
            "Existing Package v2 certificate could not be retired before a new run: "
            f"source={target}, retired={retired}, "
            f"error_type={type(exc).__name__}, error={exc}"
        ) from exc
    return retired


def _publish_aborted_package_manifest(
    output_folder: Path,
    manifest: DatasetPackageManifest,
    expected_run_id: str,
) -> str:
    current = read_package_manifest(output_folder)
    if (
        current.run_id != expected_run_id
        or current.package_status not in {"building", "complete", "incomplete"}
    ):
        raise PackageOwnershipError(
            "The package run changed before abort publication: "
            f"expected_run_id={expected_run_id}, observed_run_id={current.run_id}, "
            f"observed_status={current.package_status}"
        )
    if manifest.run_id != expected_run_id or manifest.package_status != "incomplete":
        raise PackageOwnershipError(
            "The aborted package manifest does not match its active run: "
            f"expected_run_id={expected_run_id}, manifest_run_id={manifest.run_id}, "
            f"manifest_status={manifest.package_status}"
        )
    target = output_folder / EXPORT_MANIFEST_FILENAME
    _atomic_write_text(target, manifest.model_dump_json(indent=2))
    return str(target)


def _trainer_for_request(request: DatasetExportRequest) -> DatasetPackageTrainer:
    mode = str(request.trainer_config).strip().lower()
    if mode == "kohya_toml":
        contract = get_kohya_trainer_contract()
        return DatasetPackageTrainer(
            id=contract.id,
            wire_value=contract.wire_value,
            contract_version=contract.contract_version,
            upstream_repository=contract.upstream.repository,
            upstream_tag=contract.upstream.tag,
            upstream_commit=contract.upstream.commit,
        )
    if mode == "anima_lora_toml":
        contract = get_anima_trainer_contract()
        return DatasetPackageTrainer(
            id=contract.id,
            wire_value=contract.wire_value,
            contract_version=contract.contract_version,
            upstream_repository=contract.upstream.repository,
            upstream_tag=contract.upstream.tag,
            upstream_commit=contract.upstream.commit,
        )
    raise PackageIntegrityError(
        f"Package v2 requires a verified trainer_config; received={request.trainer_config!r}"
    )


def _current_trainer_snapshot(wire_value: str) -> DatasetPackageTrainer:
    if wire_value == "kohya_toml":
        contract = get_kohya_trainer_contract()
    elif wire_value == "anima_lora_toml":
        contract = get_anima_trainer_contract()
    else:
        raise PackageIntegrityError(
            f"Unknown trainer wire value in Package v2 manifest: wire_value={wire_value!r}"
        )
    return DatasetPackageTrainer(
        id=contract.id,
        wire_value=contract.wire_value,
        contract_version=contract.contract_version,
        upstream_repository=contract.upstream.repository,
        upstream_tag=contract.upstream.tag,
        upstream_commit=contract.upstream.commit,
    )


def _options_for_request(
    request: DatasetExportRequest,
    caption_extension: str,
) -> DatasetPackageOptions:
    return DatasetPackageOptions(
        content_mode=str(request.content_mode).strip().lower(),
        caption_extension=caption_extension,
        mask_export=str(request.mask_export).strip().lower(),
        naming_pattern=request.naming_pattern,
        image_op="copy",
        overwrite_policy=request.overwrite_policy,
        trainer_repeats=request.trainer_repeats,
        trainer_batch=request.trainer_batch,
        trainer_resolution=request.trainer_resolution,
        trainer_keep_tokens=request.trainer_keep_tokens,
        trigger_sha256=_sha256_text(request.trigger),
    )


def _initial_package_manifest(
    request: DatasetExportRequest,
    requested_total: int,
    caption_extension: str,
    run_id: str,
    started_at: str,
    package_status: Literal["building", "incomplete"],
    errors: Tuple[str, ...],
) -> DatasetPackageManifest:
    return DatasetPackageManifest(
        schema=PACKAGE_MANIFEST_SCHEMA,
        manifest_version=PACKAGE_MANIFEST_VERSION,
        producer="SD Image Sorter",
        run_id=run_id,
        package_status=package_status,
        started_at=started_at,
        finished_at=None,
        trainer=_trainer_for_request(request),
        options=_options_for_request(request, caption_extension),
        readiness=None,
        counts=DatasetPackageCounts(
            requested=requested_total,
            processed=0,
            exported=0,
            skipped=0,
            failed=0,
            masks_written=0,
            masks_missing=0,
            inventory_records=0,
        ),
        inventory=None,
        package_artifacts=(),
        errors=errors,
    )


class PackageInventoryWriter:
    """Job-owned append-only connector for the uncapped JSONL inventory."""

    def __init__(self, output_folder: Path, run_id: str) -> None:
        self._output_folder = output_folder
        self._run_id = run_id
        self._record_count = 0
        self._digest = hashlib.sha256()
        self._closed = False
        descriptor: Optional[int] = None
        temporary_path: Optional[Path] = None
        try:
            # ``utils.atomic_staging``, not ``tempfile``: staging into a folder
            # the process cannot write to used to retry the refusal up to
            # ``tempfile.TMP_MAX`` times, so an unwritable output folder hung the
            # export here — before a single row was written — instead of
            # reporting the refusal. The run id is no longer part of the staging
            # name because ``PackageFileLock`` already admits one run per folder,
            # and the bounded name search steps over a file a killed run left.
            temporary_path, descriptor = create_staging_sibling(
                output_folder / PACKAGE_INVENTORY_FILENAME
            )
            self._handle: IO[bytes] = os.fdopen(descriptor, "wb")
            descriptor = None
            self._temporary_path = temporary_path
        except (OSError, ValueError) as exc:
            cleanup_errors: list[str] = []
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            raise PackageIntegrityError(
                "Package inventory staging file could not be created: "
                f"folder={output_folder}, run_id={run_id}, "
                f"error_type={type(exc).__name__}, error={exc}, "
                f"cleanup_errors={cleanup_errors}"
            ) from exc

    def append(self, record: DatasetPackageInventoryRecord) -> None:
        if self._closed:
            raise PackageIntegrityError(
                f"Package inventory is already closed: run_id={self._run_id}"
            )
        encoded = (record.model_dump_json() + "\n").encode("utf-8")
        try:
            written = self._handle.write(encoded)
        except OSError as exc:
            raise PackageIntegrityError(
                "Package inventory record could not be written: "
                f"run_id={self._run_id}, index={record.index}, "
                f"error_type={type(exc).__name__}, error={exc}"
            ) from exc
        if written != len(encoded):
            raise PackageIntegrityError(
                "Package inventory record was only partially written: "
                f"run_id={self._run_id}, index={record.index}, "
                f"expected={len(encoded)}, written={written}"
            )
        self._digest.update(encoded)
        self._record_count += 1

    def finalize(self) -> DatasetPackageInventorySummary:
        if self._closed:
            raise PackageIntegrityError(
                f"Package inventory is already closed: run_id={self._run_id}"
            )
        _require_active_building_run(self._output_folder, self._run_id)
        target = self._output_folder / PACKAGE_INVENTORY_FILENAME
        write_error: Optional[OSError] = None
        close_error: Optional[OSError] = None
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except OSError as exc:
            write_error = exc
        try:
            self._handle.close()
        except OSError as exc:
            close_error = exc
        self._closed = True
        if write_error is not None or close_error is not None:
            cleanup_error: Optional[OSError] = None
            try:
                self._temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = exc
            primary = write_error if write_error is not None else close_error
            raise PackageIntegrityError(
                "Package inventory could not be flushed safely: "
                f"target={target}, run_id={self._run_id}, error={primary}, "
                f"close_error={close_error}, cleanup_error={cleanup_error}"
            ) from primary
        try:
            publish_staging_file(self._temporary_path, target)
            byte_size = target.stat().st_size
        except OSError as exc:
            replace_cleanup_error: Optional[OSError] = None
            try:
                self._temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                replace_cleanup_error = cleanup_exc
            raise PackageIntegrityError(
                "Package inventory could not be finalized atomically: "
                f"target={target}, run_id={self._run_id}, "
                f"error_type={type(exc).__name__}, error={exc}, "
                f"cleanup_error={replace_cleanup_error}"
            ) from exc
        return DatasetPackageInventorySummary(
            path=PACKAGE_INVENTORY_FILENAME,
            byte_size=byte_size,
            sha256=self._digest.hexdigest(),
            record_count=self._record_count,
        )

    def abort(self) -> None:
        cleanup_errors: list[str] = []
        if not self._closed:
            try:
                self._handle.close()
            except OSError as exc:
                cleanup_errors.append(f"close_error={exc}")
            self._closed = True
        try:
            self._temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"unlink_error={exc}")
        if cleanup_errors:
            raise PackageIntegrityError(
                "Package inventory abort cleanup failed: "
                f"run_id={self._run_id}, errors={' | '.join(cleanup_errors)}"
            )

@dataclass(frozen=True)
class DatasetPackageBuild:
    output_folder: Path
    run_id: str
    started_at: str
    trainer: DatasetPackageTrainer
    options: DatasetPackageOptions
    inventory_writer: PackageInventoryWriter
    package_lock: PackageFileLock


def begin_dataset_package(
    output_folder: Path,
    request: DatasetExportRequest,
    requested_total: int,
    caption_extension: str,
) -> DatasetPackageBuild:
    preflight_package_targets(output_folder, request)
    package_lock = PackageFileLock(output_folder)
    package_lock.acquire()
    try:
        preflight_package_targets(output_folder, request)
    except Exception as exc:
        try:
            package_lock.release()
        except PackageIntegrityError as release_exc:
            raise PackageIntegrityError(
                f"{exc}; package_lock_release_error={release_exc}"
            ) from exc
        raise
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    building = _initial_package_manifest(
        request,
        requested_total,
        caption_extension,
        run_id,
        started_at,
        "building",
        (),
    )
    try:
        _retire_owned_package_manifest(output_folder)
        publish_package_manifest(
            output_folder,
            building,
            expected_active_run_id=None,
        )
        writer = PackageInventoryWriter(output_folder, run_id)
    except PackageIntegrityError as exc:
        failed = building.model_copy(
            update={
                "package_status": "incomplete",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "errors": ("Package inventory staging could not be created",),
            }
        )
        cleanup_errors: list[str] = []
        try:
            publish_package_manifest(
                output_folder,
                failed,
                expected_active_run_id=run_id,
            )
        except PackageIntegrityError as cleanup_exc:
            cleanup_errors.append(str(cleanup_exc))
        try:
            package_lock.release()
        except PackageIntegrityError as cleanup_exc:
            cleanup_errors.append(str(cleanup_exc))
        if cleanup_errors:
            raise PackageIntegrityError(
                f"{exc}; cleanup_errors={' | '.join(cleanup_errors)}"
            ) from exc
        raise
    return DatasetPackageBuild(
        output_folder=output_folder,
        run_id=run_id,
        started_at=started_at,
        trainer=building.trainer,
        options=building.options,
        inventory_writer=writer,
        package_lock=package_lock,
    )


def publish_pending_dataset_package(
    output_folder: Path,
    request: DatasetExportRequest,
    requested_total: int,
    caption_extension: str,
    run_id: str,
) -> str:
    preflight_package_targets(output_folder, request)
    package_lock = PackageFileLock(output_folder)
    package_lock.acquire()
    primary_error: Optional[Exception] = None
    try:
        preflight_package_targets(output_folder, request)
        pending = _initial_package_manifest(
            request,
            requested_total,
            caption_extension,
            run_id,
            datetime.now(timezone.utc).isoformat(),
            "incomplete",
            ("Package export is queued and has not completed",),
        )
        _retire_owned_package_manifest(output_folder)
        return publish_package_manifest(
            output_folder,
            pending,
            expected_active_run_id=None,
        )
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        try:
            package_lock.release()
        except PackageIntegrityError as release_exc:
            if primary_error is None:
                raise
            primary_error.add_note(f"package_lock_release_error={release_exc}")


def resume_pending_dataset_package(
    output_folder: Path,
    request: DatasetExportRequest,
    requested_total: int,
    caption_extension: str,
    run_id: str,
) -> DatasetPackageBuild:
    preflight_package_targets(output_folder, request)
    package_lock = PackageFileLock(output_folder)
    package_lock.acquire()
    building: Optional[DatasetPackageManifest] = None
    building_published = False
    try:
        preflight_package_targets(output_folder, request)
        current = read_package_manifest(output_folder)
        expected = _initial_package_manifest(
            request,
            requested_total,
            caption_extension,
            run_id,
            current.started_at,
            "incomplete",
            current.errors,
        )
        if (
            current.run_id != run_id
            or current.package_status != "incomplete"
            or current.trainer != expected.trainer
            or current.options != expected.options
            or current.counts.requested != requested_total
        ):
            raise PackageOwnershipError(
                "The queued Package v2 certificate changed before worker start: "
                f"expected_run_id={run_id}, observed_run_id={current.run_id}, "
                f"observed_status={current.package_status}"
            )
        building = current.model_copy(update={
            "package_status": "building",
            "finished_at": None,
            "errors": (),
        })
        publish_package_manifest(
            output_folder,
            building,
            expected_active_run_id=None,
        )
        building_published = True
        writer = PackageInventoryWriter(output_folder, run_id)
    except Exception as exc:
        cleanup_errors: list[str] = []
        if building is not None and building_published:
            failed = building.model_copy(update={
                "package_status": "incomplete",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "errors": (str(exc),),
            })
            try:
                _publish_aborted_package_manifest(
                    output_folder,
                    failed,
                    run_id,
                )
            except PackageIntegrityError as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        try:
            package_lock.release()
        except PackageIntegrityError as cleanup_exc:
            cleanup_errors.append(str(cleanup_exc))
        if cleanup_errors:
            raise PackageIntegrityError(
                f"{exc}; cleanup_errors={' | '.join(cleanup_errors)}"
            ) from exc
        raise
    return DatasetPackageBuild(
        output_folder=output_folder,
        run_id=run_id,
        started_at=building.started_at,
        trainer=building.trainer,
        options=building.options,
        inventory_writer=writer,
        package_lock=package_lock,
    )


def _relative_artifact(
    output_folder: Path,
    role: Literal["image", "caption", "mask", "trainer_config"],
    path: Path,
) -> DatasetPackageArtifact:
    try:
        resolved_root = output_folder.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        relative = resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise PackageIntegrityError(
            "Package artifact is missing or outside the output folder: "
            f"role={role}, path={path}, output_folder={output_folder}"
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise PackageIntegrityError(
            f"Package artifact is not a regular file: role={role}, path={path}"
        )
    stat_result = path.stat()
    return DatasetPackageArtifact(
        role=role,
        path=relative.as_posix(),
        required=True,
        byte_size=stat_result.st_size,
        sha256=_hash_file(path),
    )


def build_inventory_record(
    output_folder: Path,
    index: int,
    image_id: int,
    source_path: str,
    disposition: Literal["exported", "skipped", "failed"],
    reason: Optional[str],
    image_path: Optional[Path],
    caption_path: Optional[Path],
    mask_path: Optional[Path],
    expected_caption_sha256: Optional[str],
    annotation_provenance: Optional[AnnotationProvenance],
) -> DatasetPackageInventoryRecord:
    source = Path(source_path) if source_path else None
    source_stat: Optional[os.stat_result] = None
    source_hash: Optional[str] = None
    if source is not None:
        try:
            if source.is_file():
                source_stat = source.stat()
                source_hash = _hash_file(source)
        except OSError:
            source_stat = None
            source_hash = None
    outputs = []
    if image_path is not None:
        outputs.append(_relative_artifact(output_folder, "image", image_path))
    if caption_path is not None:
        outputs.append(_relative_artifact(output_folder, "caption", caption_path))
    if mask_path is not None:
        outputs.append(_relative_artifact(output_folder, "mask", mask_path))
    caption_artifact = next(
        (artifact for artifact in outputs if artifact.role == "caption"),
        None,
    )
    image_artifact = next(
        (artifact for artifact in outputs if artifact.role == "image"),
        None,
    )
    if caption_artifact is not None and (
        expected_caption_sha256 is None
        or caption_artifact.sha256 != expected_caption_sha256
    ):
        raise PackageIntegrityError(
            "Exported caption artifact does not match rendered content: "
            f"output={caption_artifact.path}, "
            f"expected_sha256={expected_caption_sha256}, "
            f"observed_sha256={caption_artifact.sha256}"
        )
    if image_artifact is not None and (
        source_hash is None
        or source_stat is None
        or image_artifact.sha256 != source_hash
        or image_artifact.byte_size != source_stat.st_size
    ):
        raise PackageIntegrityError(
            "Exported image does not match its source snapshot: "
            f"source={source_path}, output={image_artifact.path}"
        )
    annotation = None
    if caption_artifact is not None and expected_caption_sha256 is not None:
        if annotation_provenance is None:
            annotation = DatasetPackageAnnotationSnapshot(
                kind="legacy_snapshot",
                revision_id=None,
                content_sha256=expected_caption_sha256,
            )
        elif annotation_provenance["kind"] == "revision_ref":
            revision_id = annotation_provenance["revision_id"]
            if revision_id is None:
                raise PackageIntegrityError(
                    "revision_ref annotation provenance is missing revision_id"
                )
            annotation = DatasetPackageRevisionAnnotation(
                kind="revision_ref",
                revision_id=revision_id,
                content_sha256=annotation_provenance["content_sha256"],
                rendered_caption_sha256=expected_caption_sha256,
                source=annotation_provenance["source"],
                author_class=annotation_provenance["author_class"],
                provider=annotation_provenance["provider"],
                model=annotation_provenance["model"],
                restored_from_revision_id=annotation_provenance[
                    "restored_from_revision_id"
                ],
            )
        else:
            annotation = DatasetPackageFrozenDraftAnnotation(
                kind="frozen_draft",
                content_sha256=annotation_provenance["content_sha256"],
                rendered_caption_sha256=expected_caption_sha256,
            )
    return DatasetPackageInventoryRecord(
        index=index,
        source=DatasetPackageSourceIdentity(
            image_id=max(0, image_id),
            filename=(source.name if source is not None and source.name else f"image-{image_id}"),
            path_sha256=_sha256_text(source_path),
            byte_size=source_stat.st_size if source_stat is not None else None,
            mtime_ns=source_stat.st_mtime_ns if source_stat is not None else None,
            sha256=source_hash,
        ),
        disposition=disposition,
        reason=reason,
        annotation=annotation,
        outputs=tuple(outputs),
    )


def build_package_artifact(
    output_folder: Path,
    role: Literal["trainer_config"],
    path: Path,
) -> DatasetPackageArtifact:
    return _relative_artifact(output_folder, role, path)


def _completion_inventory_errors(
    output_folder: Path,
    inventory: DatasetPackageInventorySummary,
    package_artifacts: Tuple[DatasetPackageArtifact, ...],
    options: DatasetPackageOptions,
    trainer: DatasetPackageTrainer,
    expected_exported: int,
    expected_skipped: int,
    expected_failed: int,
    expected_masks_written: int,
) -> Tuple[str, ...]:
    inspection = _inspect_package_inventory(
        output_folder,
        inventory,
        package_artifacts,
        options.mask_export,
        options.caption_extension,
        trainer.wire_value,
    )
    errors = [
        (
            "Package root contains an unlisted trainable artifact: "
            f"path={issue.path}"
            if issue.code == "unlisted_trainable_artifact"
            else (
                f"Package integrity validation failed: code={issue.code}, "
                f"path={issue.path}, expected={issue.expected}, "
                f"observed={issue.observed}"
            )
        )
        for issue in inspection.issues
    ]
    options_issue = _trainer_options_contract_issue(trainer, options)
    if options_issue is not None:
        errors.append(
            "Package trainer options validation failed: "
            f"code={options_issue.code}, observed={options_issue.observed}"
        )
    config_issue = _trainer_config_contract_issue(
        output_folder,
        trainer,
        options,
        package_artifacts,
        expected_exported,
        expected_masks_written,
    )
    if config_issue is not None:
        errors.append(
            "Package trainer config validation failed: "
            f"code={config_issue.code}, path={config_issue.path}, "
            f"observed={config_issue.observed}"
        )
    if (
        inspection.exported_records != expected_exported
        or inspection.skipped_records != expected_skipped
        or inspection.failed_records != expected_failed
    ):
        errors.append(
            "Package inventory disposition counts do not match the export result: "
            f"expected_exported={expected_exported}, "
            f"observed_exported={inspection.exported_records}, "
            f"expected_skipped={expected_skipped}, "
            f"observed_skipped={inspection.skipped_records}, "
            f"expected_failed={expected_failed}, "
            f"observed_failed={inspection.failed_records}"
        )
    return tuple(errors)


def _mask_counts_match_export(
    mask_export: str,
    exported: int,
    masks_written: int,
    masks_missing: int,
) -> bool:
    if masks_missing != 0:
        return False
    if mask_export == "none":
        return masks_written == 0
    return masks_written == exported


def _finalize_dataset_package_locked(
    build: DatasetPackageBuild,
    requested: int,
    processed: int,
    exported: int,
    skipped: int,
    failed: int,
    masks_written: int,
    masks_missing: int,
    trainer_config_path: Optional[str],
    cancelled: bool,
    errors: Tuple[str, ...],
) -> Tuple[Literal["complete", "incomplete"], str]:
    inventory = build.inventory_writer.finalize()
    package_artifacts: Tuple[DatasetPackageArtifact, ...] = ()
    final_errors = list(errors)
    if trainer_config_path is not None:
        try:
            package_artifacts = (
                build_package_artifact(
                    build.output_folder,
                    "trainer_config",
                    Path(trainer_config_path),
                ),
            )
        except PackageIntegrityError as exc:
            final_errors.append(str(exc))
    final_errors.extend(
        _completion_inventory_errors(
            build.output_folder,
            inventory,
            package_artifacts,
            build.options,
            build.trainer,
            exported,
            skipped,
            failed,
            masks_written,
        )
    )
    complete = (
        not cancelled
        and exported == processed == requested
        and exported > 0
        and skipped == 0
        and failed == 0
        and _mask_counts_match_export(
            build.options.mask_export,
            exported,
            masks_written,
            masks_missing,
        )
        and len(package_artifacts) == 1
        and inventory.record_count == processed
        and not final_errors
    )
    package_status: Literal["complete", "incomplete"] = (
        "complete" if complete else "incomplete"
    )
    manifest = DatasetPackageManifest(
        schema=PACKAGE_MANIFEST_SCHEMA,
        manifest_version=PACKAGE_MANIFEST_VERSION,
        producer="SD Image Sorter",
        run_id=build.run_id,
        package_status=package_status,
        started_at=build.started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        trainer=build.trainer,
        options=build.options,
        readiness=None,
        counts=DatasetPackageCounts(
            requested=requested,
            processed=processed,
            exported=exported,
            skipped=skipped,
            failed=failed,
            masks_written=masks_written,
            masks_missing=masks_missing,
            inventory_records=inventory.record_count,
        ),
        inventory=inventory,
        package_artifacts=package_artifacts,
        errors=tuple(final_errors[:50]),
    )
    manifest_path = publish_package_manifest(
        build.output_folder,
        manifest,
        expected_active_run_id=build.run_id,
    )
    return package_status, manifest_path


def abort_dataset_package(build: DatasetPackageBuild, reason: str) -> None:
    cleanup_errors: list[str] = []
    try:
        build.inventory_writer.abort()
    except PackageIntegrityError as exc:
        cleanup_errors.append(str(exc))
    try:
        current = read_package_manifest(build.output_folder)
        if (
            current.run_id == build.run_id
            and current.package_status in {"building", "complete", "incomplete"}
        ):
            incomplete = current.model_copy(update={
                "package_status": "incomplete",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "errors": (*current.errors, reason),
            })
            _publish_aborted_package_manifest(
                build.output_folder,
                incomplete,
                build.run_id,
            )
    except PackageIntegrityError as exc:
        cleanup_errors.append(str(exc))
    try:
        build.package_lock.release()
    except PackageIntegrityError as exc:
        cleanup_errors.append(str(exc))
    if cleanup_errors:
        raise PackageIntegrityError(
            "Package build abort did not clean up safely: "
            f"run_id={build.run_id}, errors={' | '.join(cleanup_errors)}"
        )


def finalize_dataset_package(
    build: DatasetPackageBuild,
    requested: int,
    processed: int,
    exported: int,
    skipped: int,
    failed: int,
    masks_written: int,
    masks_missing: int,
    trainer_config_path: Optional[str],
    cancelled: bool,
    errors: Tuple[str, ...],
) -> Tuple[Literal["complete", "incomplete"], str]:
    try:
        result = _finalize_dataset_package_locked(
            build,
            requested,
            processed,
            exported,
            skipped,
            failed,
            masks_written,
            masks_missing,
            trainer_config_path,
            cancelled,
            errors,
        )
        build.package_lock.release()
    except Exception as exc:
        try:
            abort_dataset_package(build, f"Package finalization failed: {exc}")
        except PackageIntegrityError as abort_exc:
            raise PackageIntegrityError(
                f"{exc}; package_abort_cleanup_error={abort_exc}"
            ) from exc
        raise
    return result


def _canonical_package_relative_path(relative_path: str) -> Optional[str]:
    if not relative_path or "\\" in relative_path:
        return None
    candidate = PurePosixPath(relative_path)
    canonical = candidate.as_posix()
    if (
        candidate.is_absolute()
        or canonical != relative_path
        or canonical == "."
        or any(part in {".", ".."} for part in candidate.parts)
    ):
        return None
    return canonical


def _package_relative_identity(relative_path: str) -> Optional[str]:
    canonical = _canonical_package_relative_path(relative_path)
    if canonical is None:
        return None
    return os.path.normcase(canonical)


def _safe_manifest_artifact_path(output_folder: Path, relative_path: str) -> Optional[Path]:
    canonical = _canonical_package_relative_path(relative_path)
    if canonical is None:
        return None
    try:
        root_entry = output_folder.lstat()
        if not stat.S_ISDIR(root_entry.st_mode):
            return None
        current = output_folder
        parts = PurePosixPath(canonical).parts
        for index, part in enumerate(parts):
            current = current / part
            entry = current.lstat()
            if stat.S_ISLNK(entry.st_mode):
                return None
            is_leaf = index == len(parts) - 1
            if not is_leaf and not stat.S_ISDIR(entry.st_mode):
                return None
            if is_leaf and not stat.S_ISREG(entry.st_mode):
                return None
        resolved_root = output_folder.resolve(strict=True)
        resolved = current.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return resolved


def _verification_issue(
    code: str,
    path: Optional[str],
    expected: str,
    observed: str,
) -> DatasetPackageVerificationIssue:
    return DatasetPackageVerificationIssue(
        code=code,
        path=path,
        expected=expected,
        observed=observed,
    )


def _verify_artifact(
    output_folder: Path,
    artifact: DatasetPackageArtifact,
) -> Optional[DatasetPackageVerificationIssue]:
    canonical = _canonical_package_relative_path(artifact.path)
    if canonical is None:
        return _verification_issue(
            "artifact_path_invalid",
            artifact.path,
            "canonical POSIX relative package path",
            "non-canonical or unsafe path",
        )
    unresolved = output_folder.joinpath(*PurePosixPath(canonical).parts)
    if unresolved.is_symlink():
        return _verification_issue(
            "artifact_type_invalid",
            artifact.path,
            "regular non-symlink file",
            "symlink",
        )
    path = _safe_manifest_artifact_path(output_folder, artifact.path)
    if path is None:
        return _verification_issue(
            "artifact_path_invalid",
            artifact.path,
            "a regular file inside the package root",
            "missing, escaped, or unreadable",
        )
    if path.is_symlink() or not path.is_file():
        return _verification_issue(
            "artifact_type_invalid",
            artifact.path,
            "regular non-symlink file",
            "non-regular file or symlink",
        )
    try:
        observed_size = path.stat().st_size
    except OSError as exc:
        return _verification_issue(
            "artifact_read_failed",
            artifact.path,
            "readable regular file",
            f"error_type={type(exc).__name__}, error={exc}",
        )
    if observed_size != artifact.byte_size:
        return _verification_issue(
            "artifact_size_mismatch",
            artifact.path,
            str(artifact.byte_size),
            str(observed_size),
        )
    try:
        observed_hash = _hash_file(path)
    except PackageIntegrityError as exc:
        return _verification_issue(
            "artifact_read_failed",
            artifact.path,
            "readable regular file",
            str(exc),
        )
    if observed_hash != artifact.sha256:
        return _verification_issue(
            "artifact_hash_mismatch",
            artifact.path,
            artifact.sha256,
            observed_hash,
        )
    return None


def _normalized_contract_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _trainer_options_contract_issue(
    trainer: DatasetPackageTrainer,
    options: DatasetPackageOptions,
) -> Optional[DatasetPackageVerificationIssue]:
    try:
        if trainer.wire_value == "anima_lora_toml":
            validate_anima_package_options(options)
        else:
            validate_kohya_package_options(options)
    except (AnimaTrainerContractError, KohyaTrainerContractError) as exc:
        return _verification_issue(
            "trainer_options_contract_mismatch",
            EXPORT_MANIFEST_FILENAME,
            "manifest trainer options accepted by the pinned trainer contract",
            f"error_type={type(exc).__name__}, error={exc}",
        )
    return None


def _trainer_config_contract_issue(
    output_folder: Path,
    trainer: DatasetPackageTrainer,
    options: DatasetPackageOptions,
    package_artifacts: Tuple[DatasetPackageArtifact, ...],
    expected_exported: int,
    expected_masks_written: int,
) -> Optional[DatasetPackageVerificationIssue]:
    config_artifacts = tuple(
        artifact
        for artifact in package_artifacts
        if artifact.role == "trainer_config"
    )
    if len(config_artifacts) != 1:
        return None
    artifact = config_artifacts[0]
    if artifact.path != "dataset_config.toml":
        return _verification_issue(
            "trainer_config_contract_mismatch",
            artifact.path,
            "dataset_config.toml at the package root",
            artifact.path,
        )
    path = _safe_manifest_artifact_path(output_folder, artifact.path)
    if path is None or path.is_symlink() or not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        root_identity = _normalized_contract_path(output_folder)
        if trainer.wire_value == "anima_lora_toml":
            anima_options = validate_anima_toml_text(content)
            expected_mask_dir = (
                output_folder / "mask"
                if options.mask_export == "anima_lora"
                else None
            )
            matches = (
                _normalized_contract_path(anima_options.image_dir) == root_identity
                and anima_options.caption_extension == options.caption_extension
                and anima_options.num_repeats == options.trainer_repeats
                and anima_options.batch_size == options.trainer_batch
                and (
                    None
                    if anima_options.mask_dir is None
                    else _normalized_contract_path(anima_options.mask_dir)
                )
                == (
                    None
                    if expected_mask_dir is None
                    else _normalized_contract_path(expected_mask_dir)
                )
            )
            completeness = validate_anima_artifact_completeness(anima_options)
            matches = (
                matches
                and completeness.image_count == expected_exported
                and completeness.caption_count == expected_exported
                and completeness.mask_count == expected_masks_written
            )
        else:
            kohya_options = validate_kohya_toml_text(content)
            expected_conditioning_dir = (
                output_folder / "mask"
                if options.mask_export == "kohya"
                else None
            )
            matches = (
                _normalized_contract_path(kohya_options.image_dir) == root_identity
                and kohya_options.caption_extension == options.caption_extension
                and kohya_options.num_repeats == options.trainer_repeats
                and kohya_options.batch_size == options.trainer_batch
                and kohya_options.resolution == options.trainer_resolution
                and kohya_options.keep_tokens == options.trainer_keep_tokens
                and (
                    kohya_options.class_tokens == ""
                    if expected_conditioning_dir is not None
                    else _sha256_text(kohya_options.class_tokens)
                    == options.trigger_sha256
                )
                and (
                    None
                    if kohya_options.conditioning_data_dir is None
                    else _normalized_contract_path(kohya_options.conditioning_data_dir)
                )
                == (
                    None
                    if expected_conditioning_dir is None
                    else _normalized_contract_path(expected_conditioning_dir)
                )
            )
    except (
        OSError,
        UnicodeError,
        AnimaTrainerContractError,
        KohyaTrainerContractError,
    ) as exc:
        return _verification_issue(
            "trainer_config_contract_mismatch",
            artifact.path,
            "strict pinned trainer config matching manifest options and package root",
            f"error_type={type(exc).__name__}, error={exc}",
        )
    if matches:
        return None
    return _verification_issue(
        "trainer_config_contract_mismatch",
        artifact.path,
        "strict pinned trainer config matching manifest options and package root",
        "parsed trainer options differ from Package v2 manifest",
    )


@dataclass(frozen=True)
class _InventoryInspection:
    issues: Tuple[DatasetPackageVerificationIssue, ...]
    checked_records: int
    checked_artifacts: int
    exported_records: int
    skipped_records: int
    failed_records: int
    listed_paths: frozenset[str]


def _artifact_layout_issue(
    record: DatasetPackageInventoryRecord,
    record_path: str,
    caption_extension: str,
    mask_export: str,
    trainer_wire_value: str,
) -> Optional[DatasetPackageVerificationIssue]:
    if record.disposition != "exported":
        return None
    artifacts = {artifact.role: artifact for artifact in record.outputs}
    image_artifact = artifacts.get("image")
    caption_artifact = artifacts.get("caption")
    if image_artifact is None or caption_artifact is None:
        return None
    image_value = _canonical_package_relative_path(image_artifact.path)
    caption_value = _canonical_package_relative_path(caption_artifact.path)
    if image_value is None or caption_value is None:
        return None
    image_path = PurePosixPath(image_value)
    caption_path = PurePosixPath(caption_value)
    image_extensions = {
        str(extension).lower()
        for extension in ALLOWED_IMAGE_EXTENSIONS
    }
    if trainer_wire_value == "anima_lora_toml":
        contract = get_anima_trainer_contract()
        allowed_caption_extensions = set(contract.capabilities.caption_extensions)
    else:
        contract = get_kohya_trainer_contract()
        allowed_caption_extensions = set(contract.capabilities.caption_extensions)
    if (
        image_path.suffix.lower() not in image_extensions
        or caption_extension not in allowed_caption_extensions
        or caption_path.suffix != caption_extension
        or image_path.parent != caption_path.parent
        or image_path.stem != caption_path.stem
        or (
            trainer_wire_value == "anima_lora_toml"
            and image_path.parent != PurePosixPath(".")
        )
    ):
        return _verification_issue(
            "artifact_layout_invalid",
            record_path,
            "supported image plus same-parent/same-stem trainer caption",
            f"image={image_artifact.path},caption={caption_artifact.path}",
        )
    if mask_export == "none":
        return None
    mask_artifact = artifacts.get("mask")
    if mask_artifact is None:
        return None
    mask_value = _canonical_package_relative_path(mask_artifact.path)
    if mask_value is None:
        return None
    if trainer_wire_value == "anima_lora_toml" and mask_export == "anima_lora":
        anima_contract = get_anima_trainer_contract()
        expected_mask = (
            PurePosixPath(anima_contract.generated_artifacts.mask_directory)
            / image_path.parent
            / f"{image_path.stem}{anima_contract.capabilities.loss_mask_suffix}"
        )
    elif trainer_wire_value == "kohya_toml" and mask_export == "kohya":
        kohya_contract = get_kohya_trainer_contract()
        expected_mask = (
            PurePosixPath(kohya_contract.generated_artifacts.conditioning_directory)
            / f"{image_path.stem}.png"
        )
    else:
        return _verification_issue(
            "artifact_layout_invalid",
            record_path,
            "mask mode compatible with pinned trainer",
            f"trainer={trainer_wire_value},mask_export={mask_export}",
        )
    if mask_value != expected_mask.as_posix():
        return _verification_issue(
            "artifact_layout_invalid",
            mask_artifact.path,
            expected_mask.as_posix(),
            mask_value,
        )
    return None


def _inspect_package_inventory(
    output_folder: Path,
    inventory: DatasetPackageInventorySummary,
    package_artifacts: Tuple[DatasetPackageArtifact, ...],
    mask_export: str,
    caption_extension: str,
    trainer_wire_value: str,
) -> _InventoryInspection:
    issues: list[DatasetPackageVerificationIssue] = []
    listed_paths: set[str] = set()
    checked_records = 0
    checked_artifacts = 0
    exported_records = 0
    skipped_records = 0
    failed_records = 0

    for artifact in package_artifacts:
        checked_artifacts += 1
        identity = _package_relative_identity(artifact.path)
        if identity is not None and identity in listed_paths:
            issues.append(_verification_issue(
                "artifact_path_duplicate",
                artifact.path,
                "unique relative artifact path",
                "duplicate",
            ))
        if identity is not None:
            listed_paths.add(identity)
        issue = _verify_artifact(output_folder, artifact)
        if issue is not None:
            issues.append(issue)

    inventory_path = _safe_manifest_artifact_path(output_folder, inventory.path)
    if inventory_path is None or inventory_path.is_symlink() or not inventory_path.is_file():
        issues.append(_verification_issue(
            "inventory_path_invalid",
            inventory.path,
            "a regular inventory file inside the package root",
            "missing, escaped, or non-regular",
        ))
        return _InventoryInspection(
            issues=tuple(issues),
            checked_records=0,
            checked_artifacts=checked_artifacts,
            exported_records=0,
            skipped_records=0,
            failed_records=0,
            listed_paths=frozenset(listed_paths),
        )

    try:
        observed_size = inventory_path.stat().st_size
        observed_hash = _hash_file(inventory_path)
    except (OSError, PackageIntegrityError) as exc:
        issues.append(_verification_issue(
            "inventory_read_failed",
            inventory.path,
            "readable inventory file",
            f"error_type={type(exc).__name__}, error={exc}",
        ))
        return _InventoryInspection(
            issues=tuple(issues),
            checked_records=0,
            checked_artifacts=checked_artifacts,
            exported_records=0,
            skipped_records=0,
            failed_records=0,
            listed_paths=frozenset(listed_paths),
        )
    if observed_size != inventory.byte_size:
        issues.append(_verification_issue(
            "inventory_size_mismatch",
            inventory.path,
            str(inventory.byte_size),
            str(observed_size),
        ))
    if observed_hash != inventory.sha256:
        issues.append(_verification_issue(
            "inventory_hash_mismatch",
            inventory.path,
            inventory.sha256,
            observed_hash,
        ))

    try:
        with inventory_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = DatasetPackageInventoryRecord.model_validate_json(line)
                except ValidationError as exc:
                    issues.append(_verification_issue(
                        "inventory_record_invalid",
                        f"{inventory.path}:{line_number}",
                        "strict inventory record",
                        str(exc),
                    ))
                    continue
                checked_records += 1
                if record.index != checked_records:
                    issues.append(_verification_issue(
                        "inventory_index_invalid",
                        f"{inventory.path}:{line_number}",
                        str(checked_records),
                        str(record.index),
                    ))
                if record.disposition == "exported":
                    exported_records += 1
                elif record.disposition == "skipped":
                    skipped_records += 1
                else:
                    failed_records += 1

                role_names = tuple(artifact.role for artifact in record.outputs)
                role_set = set(role_names)
                if len(role_names) != len(role_set):
                    issues.append(_verification_issue(
                        "artifact_role_multiplicity_invalid",
                        f"{inventory.path}:{line_number}",
                        "at most one artifact per role",
                        ",".join(role_names),
                    ))
                expected_roles = {"image", "caption"}
                if mask_export != "none":
                    expected_roles.add("mask")
                if record.disposition == "exported" and role_set != expected_roles:
                    issues.append(_verification_issue(
                        "artifact_roles_mismatch",
                        f"{inventory.path}:{line_number}",
                        ",".join(sorted(expected_roles)),
                        ",".join(sorted(role_set)),
                    ))
                if record.disposition != "exported" and record.outputs:
                    issues.append(_verification_issue(
                        "non_exported_record_has_artifacts",
                        f"{inventory.path}:{line_number}",
                        "no output artifacts",
                        str(len(record.outputs)),
                    ))
                caption_artifact = next(
                    (artifact for artifact in record.outputs if artifact.role == "caption"),
                    None,
                )
                image_artifact = next(
                    (artifact for artifact in record.outputs if artifact.role == "image"),
                    None,
                )
                layout_issue = _artifact_layout_issue(
                    record,
                    f"{inventory.path}:{line_number}",
                    caption_extension,
                    mask_export,
                    trainer_wire_value,
                )
                if layout_issue is not None:
                    issues.append(layout_issue)
                rendered_annotation_sha256 = (
                    record.annotation.content_sha256
                    if isinstance(record.annotation, DatasetPackageAnnotationSnapshot)
                    else (
                        record.annotation.rendered_caption_sha256
                        if record.annotation is not None
                        else None
                    )
                )
                if record.disposition == "exported" and (
                    record.annotation is None
                    or caption_artifact is None
                    or rendered_annotation_sha256 != caption_artifact.sha256
                ):
                    issues.append(_verification_issue(
                        "annotation_hash_mismatch",
                        f"{inventory.path}:{line_number}",
                        "caption artifact SHA-256",
                        "missing or different annotation hash",
                    ))
                if record.disposition == "exported" and (
                    image_artifact is None
                    or record.source.sha256 is None
                    or record.source.byte_size is None
                    or image_artifact.sha256 != record.source.sha256
                    or image_artifact.byte_size != record.source.byte_size
                ):
                    issues.append(_verification_issue(
                        "source_image_hash_mismatch",
                        f"{inventory.path}:{line_number}",
                        "exported image hash and size equal the source snapshot",
                        "missing or different source identity",
                    ))
                for artifact in record.outputs:
                    checked_artifacts += 1
                    identity = _package_relative_identity(artifact.path)
                    if identity is not None and identity in listed_paths:
                        issues.append(_verification_issue(
                            "artifact_path_duplicate",
                            artifact.path,
                            "unique relative artifact path",
                            "duplicate",
                        ))
                    if identity is not None:
                        listed_paths.add(identity)
                    issue = _verify_artifact(output_folder, artifact)
                    if issue is not None:
                        issues.append(issue)
    except (OSError, UnicodeError) as exc:
        issues.append(_verification_issue(
            "inventory_read_failed",
            inventory.path,
            "readable UTF-8 JSONL",
            f"error_type={type(exc).__name__}, error={exc}",
        ))
    if checked_records != inventory.record_count:
        issues.append(_verification_issue(
            "inventory_record_count_mismatch",
            inventory.path,
            str(inventory.record_count),
            str(checked_records),
        ))
    for unlisted_path in _find_unlisted_trainable_paths(output_folder, listed_paths):
        issues.append(_verification_issue(
            "unlisted_trainable_artifact",
            unlisted_path,
            "every trainable file listed in the package inventory",
            "file is present but unlisted",
        ))
    return _InventoryInspection(
        issues=tuple(issues),
        checked_records=checked_records,
        checked_artifacts=checked_artifacts,
        exported_records=exported_records,
        skipped_records=skipped_records,
        failed_records=failed_records,
        listed_paths=frozenset(listed_paths),
    )


def _find_unlisted_trainable_paths(
    output_folder: Path,
    listed_paths: set[str],
) -> Tuple[str, ...]:
    image_extensions = {
        str(extension).lower()
        for extension in ALLOWED_IMAGE_EXTENSIONS
    }
    unlisted = []
    for candidate in output_folder.rglob("*"):
        relative = candidate.relative_to(output_folder).as_posix()
        identity = _package_relative_identity(relative)
        is_trainable = (
            candidate.suffix.lower() in image_extensions
            or candidate.suffix.lower() == ".txt"
            or candidate.name == "dataset_config.toml"
        )
        if not is_trainable:
            continue
        if candidate.is_symlink() and identity not in listed_paths:
            unlisted.append(relative)
            continue
        if candidate.is_file() and identity not in listed_paths:
            unlisted.append(relative)
    return tuple(sorted(unlisted))


def verify_dataset_package(
    request: DatasetPackageVerificationRequest,
) -> DatasetPackageVerificationResponse:
    normalized = normalize_user_path(request.output_folder)
    is_valid, error = validate_folder_path(normalized, allow_create=False)
    if not is_valid:
        raise PackageIntegrityError(
            error or f"Package output folder is invalid: path={request.output_folder!r}"
    )
    output_folder = Path(normalized)
    package_lock = PackageFileLock(output_folder)
    try:
        package_lock.acquire()
    except PackageLockError as exc:
        return DatasetPackageVerificationResponse(
            status="invalid",
            valid=False,
            run_id=None,
            checked_records=0,
            checked_artifacts=0,
            issues=(
                _verification_issue(
                    "package_locked",
                    PACKAGE_LOCK_FILENAME,
                    "an unlocked package root for a stable verification snapshot",
                    str(exc),
                ),
            ),
        )
    try:
        return _verify_dataset_package_locked(request, output_folder)
    finally:
        package_lock.release()


def _verify_dataset_package_locked(
    request: DatasetPackageVerificationRequest,
    output_folder: Path,
) -> DatasetPackageVerificationResponse:
    manifest_path = output_folder / EXPORT_MANIFEST_FILENAME
    if manifest_path.is_symlink() or (
        manifest_path.exists() and not manifest_path.is_file()
    ):
        return DatasetPackageVerificationResponse(
            status="invalid",
            valid=False,
            run_id=None,
            checked_records=0,
            checked_artifacts=0,
            issues=(
                _verification_issue(
                    "manifest_type_invalid",
                    EXPORT_MANIFEST_FILENAME,
                    "regular non-symlink file",
                    "symlink or non-regular file",
                ),
            ),
        )
    if not manifest_path.exists():
        return DatasetPackageVerificationResponse(
            status="missing",
            valid=False,
            run_id=None,
            checked_records=0,
            checked_artifacts=0,
            issues=(
                _verification_issue(
                    "manifest_missing",
                    EXPORT_MANIFEST_FILENAME,
                    "Package v2 manifest",
                    "missing",
                ),
            ),
        )
    try:
        manifest = read_package_manifest(output_folder)
    except PackageIntegrityError as exc:
        return DatasetPackageVerificationResponse(
            status="invalid",
            valid=False,
            run_id=None,
            checked_records=0,
            checked_artifacts=0,
            issues=(
                _verification_issue(
                    "manifest_invalid",
                    EXPORT_MANIFEST_FILENAME,
                    "strict Package v2 manifest",
                    str(exc),
                ),
            ),
        )
    issues: list[DatasetPackageVerificationIssue] = []
    if manifest.run_id != request.expected_run_id:
        issues.append(_verification_issue(
            "run_id_mismatch",
            EXPORT_MANIFEST_FILENAME,
            request.expected_run_id,
            manifest.run_id,
        ))
    expected_trainer = _current_trainer_snapshot(manifest.trainer.wire_value)
    if manifest.trainer != expected_trainer:
        issues.append(_verification_issue(
            "trainer_contract_mismatch",
            EXPORT_MANIFEST_FILENAME,
            expected_trainer.model_dump_json(),
            manifest.trainer.model_dump_json(),
        ))
    if manifest.package_status == "complete" and (
        manifest.counts.exported <= 0
        or manifest.counts.exported != manifest.counts.processed
        or manifest.counts.processed != manifest.counts.requested
        or manifest.counts.skipped != 0
        or manifest.counts.failed != 0
        or not _mask_counts_match_export(
            manifest.options.mask_export,
            manifest.counts.exported,
            manifest.counts.masks_written,
            manifest.counts.masks_missing,
        )
        or bool(manifest.errors)
    ):
        issues.append(_verification_issue(
            "complete_state_invalid",
            EXPORT_MANIFEST_FILENAME,
            "complete package has exported data, no failures, and no errors",
            manifest.counts.model_dump_json() + f",errors={len(manifest.errors)}",
        ))
    checked_records = 0
    checked_artifacts = 0
    exported_records = 0
    skipped_records = 0
    failed_records = 0
    inventory = manifest.inventory
    if inventory is None:
        issues.append(_verification_issue(
            "inventory_missing",
            PACKAGE_INVENTORY_FILENAME,
            "a finalized inventory reference",
            "manifest has no inventory",
        ))
    else:
        inspection = _inspect_package_inventory(
            output_folder,
            inventory,
            manifest.package_artifacts,
            manifest.options.mask_export,
            manifest.options.caption_extension,
            manifest.trainer.wire_value,
        )
        issues.extend(inspection.issues)
        checked_records = inspection.checked_records
        checked_artifacts = inspection.checked_artifacts
        exported_records = inspection.exported_records
        skipped_records = inspection.skipped_records
        failed_records = inspection.failed_records
    if len(manifest.package_artifacts) != 1 or any(
        artifact.role != "trainer_config"
        for artifact in manifest.package_artifacts
    ):
        issues.append(_verification_issue(
            "trainer_config_artifact_mismatch",
            None,
            "exactly one trainer_config artifact",
            str(len(manifest.package_artifacts)),
        ))
    options_issue = _trainer_options_contract_issue(
        manifest.trainer,
        manifest.options,
    )
    if options_issue is not None:
        issues.append(options_issue)
    config_issue = _trainer_config_contract_issue(
        output_folder,
        manifest.trainer,
        manifest.options,
        manifest.package_artifacts,
        manifest.counts.exported,
        manifest.counts.masks_written,
    )
    if config_issue is not None:
        issues.append(config_issue)
    if (
        exported_records != manifest.counts.exported
        or skipped_records != manifest.counts.skipped
        or failed_records != manifest.counts.failed
        or checked_records != manifest.counts.inventory_records
        or checked_records != manifest.counts.processed
    ):
        issues.append(_verification_issue(
            "manifest_counts_mismatch",
            EXPORT_MANIFEST_FILENAME,
            (
                f"exported={manifest.counts.exported},skipped={manifest.counts.skipped},"
                f"failed={manifest.counts.failed},records={manifest.counts.inventory_records},"
                f"processed={manifest.counts.processed}"
            ),
            (
                f"exported={exported_records},skipped={skipped_records},"
                f"failed={failed_records},records={checked_records}"
            ),
        ))
    if manifest.package_status != "complete":
        return DatasetPackageVerificationResponse(
            status="invalid" if issues else "incomplete",
            valid=False,
            run_id=manifest.run_id,
            checked_records=checked_records,
            checked_artifacts=checked_artifacts,
            issues=tuple(issues),
        )
    return DatasetPackageVerificationResponse(
        status="invalid" if issues else "complete",
        valid=not issues,
        run_id=manifest.run_id,
        checked_records=checked_records,
        checked_artifacts=checked_artifacts,
        issues=tuple(issues),
    )


__all__ = [
    "DatasetPackageBuild",
    "PackageFileLock",
    "PackageIntegrityError",
    "PackageInventoryWriter",
    "PackageLockError",
    "PackageOwnershipError",
    "abort_dataset_package",
    "begin_dataset_package",
    "build_inventory_record",
    "finalize_dataset_package",
    "package_requested",
    "preflight_package_targets",
    "publish_pending_dataset_package",
    "publish_package_manifest",
    "read_package_manifest",
    "resume_pending_dataset_package",
    "verify_dataset_package",
]
