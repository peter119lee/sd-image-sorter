"""
Update service for package-local self-updates.
"""

from __future__ import annotations

# Split (2026-07) into two sibling mixin modules + this facade, every moved
# line VERBATIM except the documented _svc() seam reads (contract:
# tests/test_update_service_pins.py 49 pins + the update family suites):
#
#   * update_service_channel  -- SSRF host allowlist + channel resolution
#                                (_UpdateChannelMixin)
#   * update_service_delivery -- version keys, release fetch/status, download
#                                integrity, restart orchestration
#                                (_UpdateDeliveryMixin)
#
# Seam layout (do not "clean up"):
#   * The verbatim header import block below stays: tests monkeypatch module
#     globals BY STRING PATH on this module
#     (services.update_service.UPDATE_API_URL / CONFIG_DIR / UPDATE_DIR /
#     PACKAGE_ROOT / UPDATE_WEB_URL / UPDATE_DOWNLOAD_URL_PREFIX /
#     ensure_directories) and read the urllib / sys module singletons through
#     this namespace (us.urllib.request.urlopen, us.sys.executable). Moved
#     mixin bodies resolve exactly those patched names back through _svc()
#     at call time.
#   * _MAX_UPDATE_ARCHIVE_ENTRIES / _MAX_UPDATE_ARCHIVE_UNCOMPRESSED_BYTES
#     stay DEFINED here AND _validate_archive stays DEFINED here (verbatim):
#     tests/test_update_worker.py patches the caps on this module object and
#     the validator must see the patch through a plain global lookup
#     (co-homing rule -- a by-reference re-export cannot provide that).
#   * prepare_update stays DEFINED here for the same reason:
#     tests/test_update_service.py patches
#     services.update_service.ensure_directories and prepare_update reads it
#     as a plain global.
#   * Everything else is re-imported below BY REFERENCE so every historical
#     services.update_service.<name> keeps resolving for the ~30 tests that
#     import module privates from here.

import json
import hashlib
import logging
import os
import re
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from urllib.parse import urlsplit

from app_info import (
    APP_VERSION,
    GITHUB_LATEST_RELEASE_API_URL,
    LINUX_FULL_ASSET_TEMPLATE,
    PATCH_ASSET_TEMPLATE,
    WINDOWS_FULL_ASSET_TEMPLATE,
)
from config import (
    CONFIG_DIR,
    DATA_DIR,
    PACKAGE_ROOT,
    UPDATE_API_URL,
    UPDATE_DIR,
    UPDATE_DOWNLOAD_URL_PREFIX,
    UPDATE_WEB_URL,
    ensure_directories,
)
from update_worker import PACKAGE_MANIFEST_RELATIVE_PATH, validate_update_manifest_managed_paths

from services.update_service_channel import (
    _GITHUB_HOST_SUFFIXES,
    _INTERNAL_HOST_EXACT,
    _INTERNAL_HOST_PREFIXES,
    _INTERNAL_HOST_SUFFIXES,
    _UpdateChannelMixin,
    _host_from_url,
    _host_is_github,
    _host_is_internal,
    _is_safe_channel_url,
    _is_safe_proxy_prefix,
)
from services.update_service_delivery import (
    _DOWNLOAD_CHUNK_SIZE,
    _HTTP_HEADERS,
    _VERSION_SEGMENT_RE,
    _UpdateDeliveryMixin,
    _load_single_package_manifest,
    _normalize_version,
    _package_manifest_member_name,
    _safe_version_text,
    _sha256sum,
    _validate_archive_member_name,
    _version_is_newer,
    _version_key,
)


logger = logging.getLogger(__name__)

_MAX_UPDATE_ARCHIVE_ENTRIES = 20000
_MAX_UPDATE_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class UpdateService(_UpdateChannelMixin, _UpdateDeliveryMixin):
    """Check for, download, and stage package-local application updates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_status: Optional[dict[str, Any]] = None
        self._checked_at = 0.0
        self._cache_ttl_seconds = 15 * 60

    def _clear_cache(self) -> None:
        with self._lock:
            self._cached_status = None
            self._checked_at = 0.0

    def _validate_archive(self, archive_path: Path) -> None:
        if archive_path.suffix.lower() == ".zip":
            if not zipfile.is_zipfile(archive_path):
                raise RuntimeError(f"Downloaded patch is not a valid zip archive: {archive_path.name}")
            package_manifest: dict[str, Any] | None = None
            with zipfile.ZipFile(archive_path, "r") as archive:
                total_uncompressed_bytes = 0
                members = archive.infolist()
                if len(members) > _MAX_UPDATE_ARCHIVE_ENTRIES:
                    raise RuntimeError("Downloaded update archive contains too many entries")
                for member in members:
                    _validate_archive_member_name(member.filename)
                    if not member.is_dir():
                        total_uncompressed_bytes += member.file_size
                        if total_uncompressed_bytes > _MAX_UPDATE_ARCHIVE_UNCOMPRESSED_BYTES:
                            raise RuntimeError("Downloaded update archive uncompressed size exceeds the safe limit")
                    manifest_member_name = _package_manifest_member_name(member.filename)
                    if manifest_member_name is not None:
                        package_manifest = _load_single_package_manifest(
                            current_manifest=package_manifest,
                            member_name=manifest_member_name,
                            payload=archive.read(member),
                        )
                bad_member = archive.testzip()
            if bad_member is not None:
                raise RuntimeError(f"Downloaded patch is corrupted near: {bad_member}")
            if package_manifest is None:
                raise RuntimeError("Downloaded update is missing update/package-manifest.json")
            validate_update_manifest_managed_paths(package_manifest)
            return

        if archive_path.name.lower().endswith((".tar.gz", ".tgz")):
            if not tarfile.is_tarfile(archive_path):
                raise RuntimeError(f"Downloaded patch is not a valid tar archive: {archive_path.name}")
            package_manifest = None
            with tarfile.open(archive_path, "r:gz") as archive:
                total_uncompressed_bytes = 0
                members = archive.getmembers()
                if len(members) > _MAX_UPDATE_ARCHIVE_ENTRIES:
                    raise RuntimeError("Downloaded update archive contains too many entries")
                for member in members:
                    _validate_archive_member_name(member.name)
                    if not (member.isfile() or member.isdir()):
                        raise RuntimeError(f"Downloaded update contains unsupported archive entry: {member.name}")
                    if member.isfile():
                        total_uncompressed_bytes += member.size
                        if total_uncompressed_bytes > _MAX_UPDATE_ARCHIVE_UNCOMPRESSED_BYTES:
                            raise RuntimeError("Downloaded update archive uncompressed size exceeds the safe limit")
                    manifest_member_name = _package_manifest_member_name(member.name)
                    if member.isfile() and manifest_member_name is not None:
                        manifest_file = archive.extractfile(member)
                        if manifest_file is not None:
                            package_manifest = _load_single_package_manifest(
                                current_manifest=package_manifest,
                                member_name=manifest_member_name,
                                payload=manifest_file.read(),
                            )
            if package_manifest is None:
                raise RuntimeError("Downloaded update is missing update/package-manifest.json")
            validate_update_manifest_managed_paths(package_manifest)
            return

        raise RuntimeError(f"Unsupported update archive type: {archive_path.name}")

    def prepare_update(self, *, force_check: bool = False, relaunch: bool = True) -> dict[str, Any]:
        ensure_directories()
        status = self.get_status(force=force_check)
        if status.get("error"):
            raise RuntimeError(str(status["error"]))
        if status.get("update_unavailable_reason"):
            raise RuntimeError(str(status["update_unavailable_reason"]))
        if not status.get("has_update"):
            return {**status, "status": "up_to_date"}
        asset = status.get("asset") or {}
        latest_version = str(status.get("latest_version") or APP_VERSION)
        archive_path = self._download_asset(asset, latest_version)
        manifest_path = self._write_pending_manifest(
            archive_path=archive_path,
            version=latest_version,
            relaunch=relaunch,
        )
        self._launch_worker(manifest_path)
        return {
            **status,
            "status": "scheduled",
            "downloaded_archive": str(archive_path),
            "pending_manifest": str(manifest_path),
            "restart_required": True,
        }
