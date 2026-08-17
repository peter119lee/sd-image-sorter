"""Release fetch, status, download integrity, and restart orchestration.

Split out of ``services/update_service.py`` (2026-07). Moved VERBATIM: the version-key family
(_normalize_version / _version_key / _version_is_newer, semver precedence:
a final release outranks its own pre-release), _safe_version_text (the
literal-dot-dot-survives quirk is pinned AS-IS), the archive member-name
helpers, and _download_asset -- whose guard ORDER (resolve sha256 ->
unsafe-name -> missing-checksum -> post-download checksum/size mismatch)
is pinned by error message; do not reorder.

The ONLY non-verbatim edits in this module (see the split manifest): reads
of the two facade-patched dir globals -- PACKAGE_ROOT and UPDATE_DIR --
resolve through _svc() at call time, because tests patch those names on
the facade module object (services.update_service); a bare re-import here
would freeze independent bindings those patches silently miss.
Never-patched names (APP_VERSION, DATA_DIR, the asset-name templates,
PACKAGE_MANIFEST_RELATIVE_PATH) import directly, and network calls stay
attribute-form ``urllib.request.urlopen(...)`` so the stdlib-module patch
seam keeps working.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from app_info import (
    APP_VERSION,
    LINUX_FULL_ASSET_TEMPLATE,
    PATCH_ASSET_TEMPLATE,
    WINDOWS_FULL_ASSET_TEMPLATE,
)
from config import DATA_DIR
from update_worker import PACKAGE_MANIFEST_RELATIVE_PATH


logger = logging.getLogger("services.update_service")  # historical channel preserved (campaign rule)

_VERSION_SEGMENT_RE = re.compile(r"(\d+|[A-Za-z]+)")
_HTTP_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"sd-image-sorter/{APP_VERSION}",
}
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _normalize_version(text: Optional[str]) -> str:
    return str(text or "").strip().lstrip("vV")


def _version_key(version: str) -> tuple[Any, ...]:
    # Semver-style precedence: a final release outranks its own pre-release
    # ("3.5.0" > "3.5.0-beta.1"), so beta users are offered the stable build.
    # Tokens are type-tagged triples so numeric and alpha tokens never reach
    # a raw int-vs-str comparison (which would raise TypeError for shapes
    # like "3.5.0.1" vs "3.5.0-beta").
    normalized = _normalize_version(version)
    core_text, prerelease_sep, prerelease_text = normalized.partition("-")

    def _tokens(text: str) -> list[tuple[int, int, str]]:
        tokens: list[tuple[int, int, str]] = []
        for segment in re.split(r"[.\-+_]", text):
            for token in _VERSION_SEGMENT_RE.findall(segment):
                if token.isdigit():
                    tokens.append((1, int(token), ""))
                else:
                    tokens.append((0, 0, token.lower()))
        return tokens

    parts = _tokens(core_text)
    if prerelease_sep:
        parts.append((0, 0, ""))  # pre-release marker: sorts below the final-release marker
        parts.extend(_tokens(prerelease_text))
    else:
        parts.append((2, 0, ""))  # final-release marker
    return tuple(parts)


def _version_is_newer(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _safe_version_text(version: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", _normalize_version(version)) or "latest"


def _validate_archive_member_name(name: str) -> None:
    normalized = str(name or "").replace("\\", "/").strip()
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in relative.parts
    ):
        raise RuntimeError(f"Downloaded update contains unsafe archive entry: {name}")


def _package_manifest_member_name(name: str) -> str | None:
    """Return a validated package-manifest archive member name, if this is one."""
    normalized = str(name or "").replace("\\", "/").strip()
    parts = PurePosixPath(normalized).parts
    manifest_parts = PACKAGE_MANIFEST_RELATIVE_PATH.parts

    if parts == manifest_parts:
        return normalized
    if len(parts) == len(manifest_parts) + 1 and parts[1:] == manifest_parts:
        return normalized
    return None


def _load_single_package_manifest(
    *,
    current_manifest: dict[str, Any] | None,
    member_name: str,
    payload: bytes,
) -> dict[str, Any]:
    if current_manifest is not None:
        raise RuntimeError(f"Downloaded update contains multiple package manifests; duplicate near: {member_name}")
    return json.loads(payload.decode("utf-8"))


def _sha256sum(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _svc():
    """Resolve facade-patched seams through services.update_service at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy import
    avoids a facade<->submodule load cycle.
    """
    import services.update_service as update_service

    return update_service


class _UpdateDeliveryMixin:
    """Release-fetch / download / restart surface of UpdateService (facade-assembled)."""

    def _platform_key(self) -> str:
        if sys.platform == "win32":
            return "windows"
        if sys.platform.startswith("linux"):
            return "linux"
        return "unsupported"

    def _read_release_json(self) -> dict[str, Any]:
        channel = self._channel_state()
        req = urllib.request.Request(channel["api_url"], headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Update API returned an unexpected payload")
        return payload

    def _read_release_manifest(self, manifest_url: str) -> dict[str, Any]:
        req = urllib.request.Request(manifest_url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Release checksum manifest returned an unexpected payload")
        return payload

    def _resolve_asset_sha256(self, asset: dict[str, Any]) -> str:
        direct_sha256 = str(asset.get("sha256") or "").strip().lower()
        if direct_sha256:
            return direct_sha256

        manifest_url = str(asset.get("manifest_download_url") or "").strip()
        if not manifest_url:
            return ""

        manifest_payload = self._read_release_manifest(manifest_url)
        manifest_assets = manifest_payload.get("assets") or []
        if not isinstance(manifest_assets, list):
            raise RuntimeError("Release checksum manifest assets payload is invalid")

        target_name = str(asset.get("name") or "").strip()
        for entry in manifest_assets:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "").strip() != target_name:
                continue
            sha256_value = str(entry.get("sha256") or "").strip().lower()
            if sha256_value:
                return sha256_value
            break

        raise RuntimeError(f"Release checksum manifest is missing sha256 for asset: {target_name}")

    def _select_update_asset(self, release: dict[str, Any], latest_version: str) -> Optional[dict[str, Any]]:
        channel = self._channel_state()
        assets = release.get("assets") or []
        if not isinstance(assets, list):
            return None

        platform_key = self._platform_key()
        preferred_names = [
            ("patch", PATCH_ASSET_TEMPLATE.format(version=latest_version)),
        ]
        if platform_key == "windows":
            preferred_names.append(("full", WINDOWS_FULL_ASSET_TEMPLATE.format(version=latest_version)))
        elif platform_key == "linux":
            preferred_names.append(("full", LINUX_FULL_ASSET_TEMPLATE.format(version=latest_version)))
        manifest_name = f"sd-image-sorter-v{latest_version}-release-manifest.json"
        manifest_download_url = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("name") or "") != manifest_name:
                continue
            manifest_download_url = self._rewrite_download_url(
                asset.get("browser_download_url") or "",
                channel["download_url_prefix"],
            )
            break

        for asset_kind, preferred_name in preferred_names:
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")
                if name != preferred_name:
                    continue
                return {
                    "kind": asset_kind,
                    "name": asset.get("name") or "",
                    "size_bytes": int(asset.get("size") or 0),
                    "download_url": self._rewrite_download_url(
                        asset.get("browser_download_url") or "",
                        channel["download_url_prefix"],
                    ),
                    "manifest_download_url": manifest_download_url,
                    "content_type": asset.get("content_type") or "",
                    "updated_at": asset.get("updated_at"),
                }
        return None

    def _build_status(self, release: Optional[dict[str, Any]], *, error: Optional[str] = None) -> dict[str, Any]:
        current_version = APP_VERSION
        channel = self._channel_state()
        status: dict[str, Any] = {
            "updater_enabled": True,
            "package_root": str(_svc().PACKAGE_ROOT),
            "data_root": str(DATA_DIR),
            "update_root": str(_svc().UPDATE_DIR),
            "platform": self._platform_key(),
            "current_version": current_version,
            "latest_version": current_version,
            "has_update": False,
            "release_url": channel["web_url"],
            "release_notes": "",
            "asset": None,
            "error": error,
            "update_unavailable_reason": None,
            "channel_name": channel["channel_name"],
            "channel_api_url": channel["api_url"],
            "channel_web_url": channel["web_url"],
            "channel_download_url_prefix": channel["download_url_prefix"],
            "has_channel_override": channel["has_override"],
            "channel_config_path": channel["config_path"],
            "is_default_github_channel": channel["is_default_github_channel"],
            "base_channel_api_url": channel["base_api_url"],
            "base_channel_web_url": channel["base_web_url"],
            "checked_at": time.time(),
        }

        if not release:
            return status

        latest_version = _normalize_version(
            release.get("tag_name")
            or release.get("name")
            or current_version
        ) or current_version
        asset = self._select_update_asset(release, latest_version)
        newer_than_current = _version_is_newer(latest_version, current_version)
        status.update(
            {
                "latest_version": latest_version,
                "has_update": bool(asset and newer_than_current),
                "release_url": release.get("html_url") or status["release_url"],
                "release_notes": release.get("body") or "",
                "published_at": release.get("published_at"),
                "asset": asset,
                "update_unavailable_reason": (
                    (
                        f"Latest release {latest_version} exists, but this update channel does not provide "
                        "a compatible in-app update package for your platform. "
                        "Please open the release page and download the full package manually."
                    )
                    if newer_than_current and asset is None
                    else None
                ),
            }
        )
        return status

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if (
                not force
                and self._cached_status is not None
                and now - self._checked_at < self._cache_ttl_seconds
            ):
                return dict(self._cached_status)

        try:
            release = self._read_release_json()
            status = self._build_status(release)
        except Exception as exc:
            logger.warning("Failed to check for updates: %s", exc)
            status = self._build_status(None, error=self._format_update_error(exc))

        with self._lock:
            self._cached_status = dict(status)
            self._checked_at = status["checked_at"]

        return status

    def _downloads_dir(self) -> Path:
        path = Path(_svc().UPDATE_DIR) / "downloads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _state_dir(self) -> Path:
        path = Path(_svc().UPDATE_DIR) / "state"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _logs_dir(self) -> Path:
        path = Path(_svc().UPDATE_DIR) / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _download_asset(self, asset: dict[str, Any], version: str) -> Path:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("download_url") or "").strip()
        size_bytes = int(asset.get("size_bytes") or 0)
        expected_sha256 = self._resolve_asset_sha256(asset)
        if not name or not url:
            raise RuntimeError("Release does not include a downloadable update asset")
        # The GitHub asset name is used verbatim as the on-disk archive filename;
        # reject anything that is not a single path component so a hostile name
        # (e.g. "../run.bat" or "a/b") cannot escape the downloads directory.
        if Path(name).name != name or ".." in name or "/" in name or "\\" in name:
            raise RuntimeError(f"Refusing to download update asset with unsafe name: {name}")
        # Integrity is mandatory: without a verified SHA-256 we have no way to
        # know the downloaded archive is the one the maintainer published, so we
        # refuse to apply it rather than silently skipping the check.
        if not expected_sha256:
            raise RuntimeError(
                f"Refusing to apply update asset without a verified SHA-256 checksum: {name}"
            )

        target_dir = self._downloads_dir() / _safe_version_text(version)
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_path = target_dir / name

        if archive_path.exists() and archive_path.stat().st_size > 0:
            size_matches = size_bytes <= 0 or archive_path.stat().st_size == size_bytes
            hash_matches = _sha256sum(archive_path) == expected_sha256
            if size_matches and hash_matches:
                self._validate_archive(archive_path)
                return archive_path

        temp_path = archive_path.with_name(archive_path.name + ".tmp")
        if temp_path.exists():
            temp_path.unlink()
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(req, timeout=60) as response, temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    f"Downloaded update checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
                )
            temp_path.replace(archive_path)
        finally:
            # temp_path.replace() removes the temp file on success, so guard the
            # cleanup against the file already being gone (Windows raises here).
            temp_path.unlink(missing_ok=True)

        if size_bytes > 0 and archive_path.stat().st_size != size_bytes:
            raise RuntimeError(
                f"Downloaded update size mismatch: expected {size_bytes}, got {archive_path.stat().st_size}"
            )
        self._validate_archive(archive_path)
        return archive_path

    def _resolve_launcher_path(self) -> Path:
        launcher_name = str(os.environ.get("SD_IMAGE_SORTER_LAUNCHER") or "").strip()
        candidates = []
        if launcher_name:
            candidates.append(_svc().PACKAGE_ROOT / launcher_name)
        if sys.platform == "win32":
            candidates.extend(
                [
                    _svc().PACKAGE_ROOT / "run-portable.bat",
                    _svc().PACKAGE_ROOT / "run.bat",
                ]
            )
        else:
            candidates.append(_svc().PACKAGE_ROOT / "run.sh")

        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise RuntimeError("Could not locate a launcher script for restarting the app")

    def _pending_manifest_path(self, version: str) -> Path:
        safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", version) or "latest"
        return self._state_dir() / f"pending-update-{safe_version}.json"

    def _write_pending_manifest(
        self,
        *,
        archive_path: Path,
        version: str,
        relaunch: bool,
        current_pid: int | None = None,
    ) -> Path:
        launcher_path = self._resolve_launcher_path()
        timestamp = int(time.time())
        payload = {
            "created_at": timestamp,
            "current_pid": os.getpid() if current_pid is None else int(current_pid),
            "package_root": str(_svc().PACKAGE_ROOT),
            "archive_path": str(archive_path),
            "target_version": version,
            "launcher_path": str(launcher_path),
            "relaunch": bool(relaunch),
            "log_path": str(self._logs_dir() / f"update-{timestamp}.log"),
            "update_root": str(_svc().UPDATE_DIR),
        }
        manifest_path = self._pending_manifest_path(version)
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return manifest_path

    def _worker_command(self, manifest_path: Path) -> list[str]:
        return [
            sys.executable,
            str(_svc().PACKAGE_ROOT / "backend" / "update_worker.py"),
            "--manifest",
            str(manifest_path),
        ]

    def _launch_worker(self, manifest_path: Path) -> None:
        command = self._worker_command(manifest_path)
        kwargs: dict[str, Any] = {
            "cwd": str(_svc().PACKAGE_ROOT),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            creationflags = 0
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            kwargs["creationflags"] = creationflags
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
