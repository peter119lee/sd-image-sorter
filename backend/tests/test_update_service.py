from __future__ import annotations

import io
import json
import tarfile
import zipfile
import hashlib
from pathlib import Path

import pytest

from app_info import GITHUB_LATEST_RELEASE_API_URL, GITHUB_REPOSITORY_URL
from services.update_service import UpdateService

# The built-in GitHub channel, derived from app_info instead of repeated as a
# literal. Production decides ``is_default_github_channel`` by string-comparing
# the configured api_url against GITHUB_LATEST_RELEASE_API_URL, so a hardcoded
# copy stops being "the default" the moment the owner or repo is renamed -- and
# the test then asserts default-channel behaviour against a URL that is no
# longer the default.
_DEFAULT_API_URL = GITHUB_LATEST_RELEASE_API_URL
_DEFAULT_WEB_URL = f"{GITHUB_REPOSITORY_URL}/releases/latest"


def test_get_status_reports_available_patch_update(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(
        service,
        "_read_release_json",
        lambda: {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/release",
            "body": "notes",
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-app-patch.zip",
                    "size": 123,
                    "browser_download_url": "https://example.com/patch.zip",
                    "content_type": "application/zip",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        },
    )

    status = service.get_status(force=True)

    assert status["has_update"] is True
    assert status["latest_version"] == "9.9.9"
    assert status["asset"]["kind"] == "patch"
    assert status["asset"]["name"] == "sd-image-sorter-v9.9.9-app-patch.zip"
    assert status["channel_api_url"]
    assert status["channel_web_url"]


def test_get_status_prefers_patch_over_full_package(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(service, "_platform_key", lambda: "linux")
    monkeypatch.setattr(
        service,
        "_read_release_json",
        lambda: {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/release",
            "body": "notes",
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-linux.tar.gz",
                    "size": 999,
                    "browser_download_url": "https://example.com/full.tar.gz",
                    "content_type": "application/gzip",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "name": "sd-image-sorter-v9.9.9-app-patch.zip",
                    "size": 123,
                    "browser_download_url": "https://example.com/patch.zip",
                    "content_type": "application/zip",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            ],
        },
    )

    status = service.get_status(force=True)

    assert status["has_update"] is True
    assert status["asset"]["kind"] == "patch"
    assert status["asset"]["name"] == "sd-image-sorter-v9.9.9-app-patch.zip"


def test_get_status_flags_missing_patch_asset(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(
        service,
        "_read_release_json",
        lambda: {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/release",
            "body": "notes",
            "assets": [],
        },
    )

    status = service.get_status(force=True)

    assert status["has_update"] is False
    assert status["latest_version"] == "9.9.9"
    assert status["update_unavailable_reason"]


def test_get_status_falls_back_to_full_package_when_patch_missing(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(service, "_platform_key", lambda: "linux")
    monkeypatch.setattr(
        service,
        "_read_release_json",
        lambda: {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/release",
            "body": "notes",
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-linux.tar.gz",
                    "size": 456,
                    "browser_download_url": "https://example.com/full.tar.gz",
                    "content_type": "application/gzip",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        },
    )

    status = service.get_status(force=True)

    assert status["has_update"] is True
    assert status["latest_version"] == "9.9.9"
    assert status["asset"]["kind"] == "full"
    assert status["asset"]["name"] == "sd-image-sorter-v9.9.9-linux.tar.gz"


def test_read_release_json_uses_configured_update_api_url(monkeypatch):
    service = UpdateService()
    captured: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"tag_name": "v3.1.1", "assets": []}).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr("services.update_service.UPDATE_API_URL", "https://mirror.example/latest.json")
    monkeypatch.setattr("services.update_service.urllib.request.urlopen", fake_urlopen)

    payload = service._read_release_json()

    assert captured["url"] == "https://mirror.example/latest.json"
    assert payload["tag_name"] == "v3.1.1"


def test_select_update_asset_applies_download_proxy_prefix(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr("services.update_service.UPDATE_DOWNLOAD_URL_PREFIX", "https://ghfast.top/")

    asset = service._select_update_asset(
        {
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-app-patch.zip",
                    "size": 321,
                    "browser_download_url": "https://github.com/example/patch.zip",
                }
            ]
        },
        "9.9.9",
    )

    assert asset is not None
    assert asset["download_url"] == "https://ghfast.top/https://github.com/example/patch.zip"


def test_select_update_asset_attaches_release_manifest_url(monkeypatch):
    service = UpdateService()

    asset = service._select_update_asset(
        {
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-app-patch.zip",
                    "size": 321,
                    "browser_download_url": "https://example.com/patch.zip",
                },
                {
                    "name": "sd-image-sorter-v9.9.9-release-manifest.json",
                    "size": 123,
                    "browser_download_url": "https://example.com/manifest.json",
                },
            ]
        },
        "9.9.9",
    )

    assert asset is not None
    assert asset["manifest_download_url"] == "https://example.com/manifest.json"


def test_resolve_asset_sha256_reads_release_manifest(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(
        service,
        "_read_release_manifest",
        lambda url: {
            "assets": [
                {
                    "name": "sd-image-sorter-v9.9.9-app-patch.zip",
                    "sha256": "abc123",
                }
            ]
        },
    )

    sha256_value = service._resolve_asset_sha256(
        {
            "name": "sd-image-sorter-v9.9.9-app-patch.zip",
            "manifest_download_url": "https://example.com/manifest.json",
        }
    )

    assert sha256_value == "abc123"


def test_get_status_default_github_failure_explains_mirror_option(monkeypatch):
    service = UpdateService()

    def raise_timeout():
        raise TimeoutError("timed out")

    monkeypatch.setattr("services.update_service.UPDATE_API_URL", _DEFAULT_API_URL)
    monkeypatch.setattr(service, "_read_release_json", raise_timeout)

    status = service.get_status(force=True)

    assert status["has_update"] is False
    assert "GitHub" in status["error"]
    assert "network" in status["error"].lower()
    assert "VPN" in status["error"]


def test_save_proxy_channel_creates_package_local_override(monkeypatch, tmp_path: Path):
    service = UpdateService()
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("services.update_service.CONFIG_DIR", config_dir)
    monkeypatch.setattr("services.update_service.UPDATE_API_URL", _DEFAULT_API_URL)
    monkeypatch.setattr("services.update_service.UPDATE_WEB_URL", _DEFAULT_WEB_URL)

    result = service.save_proxy_channel("https://ghfast.top")
    config_payload = json.loads((config_dir / "update-channel.json").read_text(encoding="utf-8"))

    assert result["has_channel_override"] is True
    assert result["download_url_prefix"] == "https://ghfast.top/"
    assert result["is_default_github_channel"] is False
    assert config_payload["api_url"] == f"https://ghfast.top/{_DEFAULT_API_URL}"
    assert config_payload["web_url"] == f"https://ghfast.top/{_DEFAULT_WEB_URL}"


def test_reset_channel_settings_removes_override(monkeypatch, tmp_path: Path):
    service = UpdateService()
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    override_path = config_dir / "update-channel.json"
    override_path.write_text(
        json.dumps(
            {
                "channel_name": "Custom Proxy",
                "api_url": f"https://proxy.example/{_DEFAULT_API_URL}",
                "web_url": f"https://proxy.example/{_DEFAULT_WEB_URL}",
                "download_url_prefix": "https://proxy.example/",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("services.update_service.CONFIG_DIR", config_dir)
    monkeypatch.setattr("services.update_service.UPDATE_API_URL", _DEFAULT_API_URL)

    result = service.reset_channel_settings()

    assert override_path.exists() is False
    assert result["has_channel_override"] is False
    assert result["is_default_github_channel"] is True


def test_validate_archive_rejects_zip_path_traversal(tmp_path: Path):
    service = UpdateService()
    archive_path = tmp_path / "patch.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "bad")

    with pytest.raises(RuntimeError, match="unsafe archive entry"):
        service._validate_archive(archive_path)


def test_validate_archive_rejects_tar_path_traversal(tmp_path: Path):
    service = UpdateService()
    archive_path = tmp_path / "patch.tar.gz"
    payload = b"bad"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("..\\escape.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(RuntimeError, match="unsafe archive entry"):
        service._validate_archive(archive_path)


def test_prepare_update_launches_worker(monkeypatch, tmp_path: Path):
    service = UpdateService()
    archive_path = tmp_path / "patch.zip"
    archive_path.write_bytes(b"zip")
    manifest_path = tmp_path / "pending.json"

    monkeypatch.setattr("services.update_service.ensure_directories", lambda: None)
    monkeypatch.setattr(
        service,
        "get_status",
        lambda force=False: {
            "current_version": "3.1.0",
            "latest_version": "3.1.1",
            "has_update": True,
            "asset": {
                "name": "sd-image-sorter-v3.1.1-app-patch.zip",
                "size_bytes": 3,
                "download_url": "https://example.com/patch.zip",
            },
            "error": None,
        },
    )
    monkeypatch.setattr(service, "_download_asset", lambda asset, version: archive_path)
    monkeypatch.setattr(
        service,
        "_write_pending_manifest",
        lambda **kwargs: manifest_path,
    )

    launched: dict[str, Path] = {}
    monkeypatch.setattr(service, "_launch_worker", lambda path: launched.setdefault("path", path))

    result = service.prepare_update(force_check=True, relaunch=True)

    assert result["status"] == "scheduled"
    assert result["downloaded_archive"] == str(archive_path)
    assert launched["path"] == manifest_path


def test_download_asset_rejects_sha256_mismatch(monkeypatch, tmp_path: Path):
    service = UpdateService()
    payload = b"not-a-real-archive"

    class FakeResponse:
        def __enter__(self):
            self._offset = 0
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            if self._offset >= len(payload):
                return b""
            if size < 0:
                size = len(payload) - self._offset
            chunk = payload[self._offset:self._offset + size]
            self._offset += len(chunk)
            return chunk

    monkeypatch.setattr(service, "_downloads_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_validate_archive", lambda path: None)
    monkeypatch.setattr(service, "_resolve_asset_sha256", lambda asset: hashlib.sha256(b"expected").hexdigest())
    monkeypatch.setattr("services.update_service.urllib.request.urlopen", lambda request, timeout=0: FakeResponse())

    asset = {
        "name": "sd-image-sorter-v9.9.9-app-patch.zip",
        "size_bytes": len(payload),
        "download_url": "https://example.com/patch.zip",
    }

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        service._download_asset(asset, "9.9.9")

    final_path = tmp_path / "9.9.9" / "sd-image-sorter-v9.9.9-app-patch.zip"
    assert final_path.exists() is False
    assert final_path.with_name(final_path.name + ".tmp").exists() is False
