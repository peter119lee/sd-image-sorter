"""Characterization pins for ``services.update_service.UpdateService``.

Step-0 pins-first safety net. These lock the CURRENT behavior of the in-app
updater so a later verbatim split / refactor cannot silently change it. They
are intentionally behavioral (not aspirational): where the current code has a
quirk, it is pinned AS-IS and called out in the module docstring below.

SCOPE / NON-DUPLICATION
-----------------------
``_validate_archive`` (zip/tar traversal, zip-bomb, entry cap, missing/double
manifest, protected-path rejection) is already heavily pinned by
``test_update_archive_validation.py`` and ``test_update_worker.py`` and by two
cases in ``test_update_service.py``. This suite does NOT re-pin archive
validation. It targets the previously-uncovered surfaces, with the security
invariants front and center:

  * SSRF host allowlist (``_host_is_github`` / ``_host_is_internal`` /
    ``_is_safe_channel_url`` / ``_is_safe_proxy_prefix``) — the largest
    untested attack surface.
  * Channel-override trust boundary (``_read_channel_override`` /
    ``_sanitize_channel_override`` / ``save_proxy_channel``).
  * Download integrity guards (unsafe asset name, mandatory SHA-256).
  * Release JSON/manifest payload contracts + the SOP ``release.body``
    passthrough (CLAUDE.md release-notes 200-char rule).
  * Restart orchestration (launcher env branch, worker command, pending
    manifest shape).

DORMANT BEHAVIOR PINNED AS-IS:
  * ``_version_is_newer("3.5.0", "3.5.0-beta.1")`` is True: a final release
    outranks its own pre-release (fixed semver precedence) because ``_version_key``
    yields a shorter tuple that sorts first. This is pinned, not fixed.
  * ``_safe_version_text`` does not strip a literal ``..`` (only path
    separators are collapsed); pinned AS-IS. Harmless because no separator
    remains so it cannot traverse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import services.update_service as us
from services.update_service import UpdateService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _point_channel_at_tmp(monkeypatch, tmp_path: Path) -> Path:
    """Redirect CONFIG_DIR + the GitHub-default channel URLs at a hermetic tmp.

    Ensures ``_channel_state`` reads no real override file and reports the
    built-in GitHub channel as the default.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(us, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(us, "UPDATE_API_URL", us.GITHUB_LATEST_RELEASE_API_URL)
    monkeypatch.setattr(
        us,
        "UPDATE_WEB_URL",
        "https://github.com/peter119lee/sd-image-sorter/releases/latest",
    )
    monkeypatch.setattr(us, "UPDATE_DOWNLOAD_URL_PREFIX", "")
    return config_dir


# ===========================================================================
# Section A — version normalization + comparison (pure helpers)
# ===========================================================================


def test_normalize_version_strips_v_prefix_and_whitespace_and_none():
    assert us._normalize_version("  vV3.5.0  ") == "3.5.0"
    assert us._normalize_version("V3.5.0") == "3.5.0"
    assert us._normalize_version(None) == ""


def test_version_key_orders_versions_semver_style():
    # Ordering pins (the key's internal shape is deliberately not pinned):
    # numeric core ordering, prerelease bumps, and type-safety for mixed
    # numeric/alpha shapes that used to risk int-vs-str TypeError.
    assert us._version_key("3.5.0") > us._version_key("3.4.9")
    assert us._version_key("3.5.0-beta.2") > us._version_key("3.5.0-beta.1")
    assert us._version_key("1a") == us._version_key("1A")
    assert us._version_is_newer("3.5.0.1", "3.5.0-beta") is True


def test_version_is_newer_true_for_patch_and_prerelease_bump():
    assert us._version_is_newer("3.6.0", "3.5.0") is True
    assert us._version_is_newer("3.5.0-beta.2", "3.5.0-beta.1") is True
    assert us._version_is_newer("3.5.0", "3.6.0") is False


def test_version_is_newer_offers_stable_to_its_own_prerelease_users():
    """Regression pin for the beta-cannot-upgrade-to-stable trap.

    A final release now outranks its own pre-release (semver precedence),
    so users on 3.5.0-beta.1 are offered 3.5.0 when it ships. The reverse
    direction stays false.
    """
    assert us._version_is_newer("3.5.0", "3.5.0-beta.1") is True
    assert us._version_is_newer("3.5.0-beta.1", "3.5.0") is False
    assert us._version_is_newer("3.5.0", "3.5.0") is False


def test_safe_version_text_sanitizes_and_defaults_to_latest():
    assert us._safe_version_text("") == "latest"
    # Path separators collapse to '-', but a literal '..' survives (harmless:
    # no separator remains so it cannot traverse). Pinned AS-IS.
    assert us._safe_version_text("3.5.0/../x") == "3.5.0-..-x"


# ===========================================================================
# Section B — SSRF host allowlist (SECURITY-CRITICAL)
# ===========================================================================


def test_host_from_url_lowercases_hostname():
    assert us._host_from_url("https://API.GitHub.com/x") == "api.github.com"
    assert us._host_from_url("not a url") == ""


def test_host_is_github_accepts_real_hosts_and_rejects_lookalike():
    assert us._host_is_github("api.github.com") is True
    assert us._host_is_github("objects.githubusercontent.com") is True
    assert us._host_is_github("github.com") is True
    # SECURITY: an attacker-registrable lookalike that merely ends with the
    # allowlisted suffix must be rejected (no bare endswith match).
    assert us._host_is_github("evilgithub.com") is False
    assert us._host_is_github("") is False


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("10.0.0.5", True),
        ("192.168.1.1", True),
        ("169.254.1.1", True),
        ("0.0.0.0", True),
        ("172.16.0.1", True),  # low edge of RFC1918 172.16/12
        ("172.31.255.1", True),  # high edge
        ("172.15.0.1", False),  # just below the private range
        ("172.32.0.1", False),  # just above the private range
        ("localhost", True),
        ("::1", True),
        ("fe80::1", True),
        ("service.internal", True),
        ("printer.local", True),
        ("example.com", False),
        ("api.github.com", False),
        ("", True),  # unparseable/empty host is treated as unsafe
    ],
)
def test_host_is_internal_classifies_loopback_rfc1918_and_linklocal(host, expected):
    assert us._host_is_internal(host) is expected


def test_is_safe_proxy_prefix_requires_https_and_non_internal_host():
    assert us._is_safe_proxy_prefix("https://ghfast.top/") is True
    assert us._is_safe_proxy_prefix("http://ghfast.top/") is False
    assert us._is_safe_proxy_prefix("https://127.0.0.1/") is False


def test_is_safe_channel_url_accepts_direct_github_and_proxy_mirror():
    assert us._is_safe_channel_url("https://api.github.com/x") is True
    # Proxy-mirror form: an embedded https GitHub URL after a public proxy host.
    assert (
        us._is_safe_channel_url("https://mirror.example/https://github.com/x") is True
    )


def test_is_safe_channel_url_rejects_internal_proxy_and_non_github():
    # SECURITY: proxy prefix that resolves to loopback must be refused even
    # though it embeds a valid GitHub URL.
    assert us._is_safe_channel_url("https://127.0.0.1/https://github.com/x") is False
    assert us._is_safe_channel_url("https://evil.com/x") is False
    assert us._is_safe_channel_url("http://api.github.com/x") is False


# ===========================================================================
# Section C — channel override trust boundary (SECURITY)
# ===========================================================================


def test_read_channel_override_returns_empty_on_malformed_json(
    monkeypatch, tmp_path: Path
):
    config_dir = _point_channel_at_tmp(monkeypatch, tmp_path)
    (config_dir / "update-channel.json").write_text("{not json", encoding="utf-8")
    service = UpdateService()
    assert service._read_channel_override() == {}


def test_read_channel_override_returns_empty_on_non_dict_payload(
    monkeypatch, tmp_path: Path
):
    config_dir = _point_channel_at_tmp(monkeypatch, tmp_path)
    (config_dir / "update-channel.json").write_text("[1, 2, 3]", encoding="utf-8")
    service = UpdateService()
    assert service._read_channel_override() == {}


def test_sanitize_channel_override_drops_unsafe_urls_and_keeps_safe(
    monkeypatch, tmp_path: Path
):
    config_dir = _point_channel_at_tmp(monkeypatch, tmp_path)
    (config_dir / "update-channel.json").write_text(
        json.dumps(
            {
                "channel_name": "X",
                "api_url": "https://evil.com/x",  # dropped
                "web_url": "https://mirror.example/https://github.com/y",  # kept
                "download_url_prefix": "https://127.0.0.1/",  # dropped
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService()
    sanitized = service._read_channel_override()
    assert "api_url" not in sanitized
    assert "download_url_prefix" not in sanitized
    assert sanitized["web_url"] == "https://mirror.example/https://github.com/y"
    assert sanitized["channel_name"] == "X"


def test_channel_state_defaults_to_github_when_no_override(monkeypatch, tmp_path: Path):
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    state = service._channel_state()
    assert state["is_default_github_channel"] is True
    assert state["has_override"] is False
    assert state["channel_name"] == "GitHub Default"


def test_save_proxy_channel_rejects_non_https_prefix(monkeypatch, tmp_path: Path):
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    with pytest.raises(RuntimeError, match="https"):
        service.save_proxy_channel("http://ghfast.top")


def test_save_proxy_channel_rejects_internal_host_prefix(monkeypatch, tmp_path: Path):
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    with pytest.raises(RuntimeError, match="internal or loopback"):
        service.save_proxy_channel("https://127.0.0.1")


# ===========================================================================
# Section D — download URL rewrite
# ===========================================================================


def test_rewrite_download_url_prefix_semantics():
    service = UpdateService()
    # Empty prefix => passthrough.
    assert (
        service._rewrite_download_url("https://github.com/x", "")
        == "https://github.com/x"
    )
    # Prefix is prepended.
    assert (
        service._rewrite_download_url("https://github.com/x", "https://ghfast.top/")
        == "https://ghfast.top/https://github.com/x"
    )
    # Idempotent: an already-prefixed URL is not double-wrapped.
    assert (
        service._rewrite_download_url(
            "https://ghfast.top/https://github.com/x", "https://ghfast.top/"
        )
        == "https://ghfast.top/https://github.com/x"
    )
    # Empty URL stays empty.
    assert service._rewrite_download_url("", "https://ghfast.top/") == ""


# ===========================================================================
# Section E — download integrity guards (SECURITY)
# ===========================================================================


def test_download_asset_rejects_unsafe_archive_name(monkeypatch, tmp_path: Path):
    """A GitHub asset name is used verbatim as the on-disk filename; a name that
    is not a single path component must be refused before any download."""
    service = UpdateService()
    monkeypatch.setattr(service, "_downloads_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="unsafe name"):
        service._download_asset(
            {
                "name": "../run.bat",
                "download_url": "https://example.com/a",
                "size_bytes": 1,
            },
            "9.9.9",
        )


def test_download_asset_refuses_asset_without_verified_sha256(
    monkeypatch, tmp_path: Path
):
    """Integrity is mandatory: without a resolvable SHA-256 the archive is
    refused rather than applied unverified."""
    service = UpdateService()
    monkeypatch.setattr(service, "_downloads_dir", lambda: tmp_path)
    # Asset carries no sha256 and no manifest_download_url => resolves to "".
    with pytest.raises(RuntimeError, match="verified SHA-256"):
        service._download_asset(
            {
                "name": "patch.zip",
                "download_url": "https://example.com/a",
                "size_bytes": 1,
            },
            "9.9.9",
        )


# ===========================================================================
# Section F — release JSON / manifest payload contracts
# ===========================================================================


def _fake_urlopen(payload_bytes: bytes):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload_bytes

    def _open(request, timeout=0):
        return _Resp()

    return _open


def test_read_release_json_rejects_non_dict_payload(monkeypatch, tmp_path: Path):
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    monkeypatch.setattr(us.urllib.request, "urlopen", _fake_urlopen(b"[1, 2, 3]"))
    with pytest.raises(RuntimeError, match="unexpected payload"):
        service._read_release_json()


def test_read_release_manifest_rejects_non_dict_payload(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(us.urllib.request, "urlopen", _fake_urlopen(b"[1, 2, 3]"))
    with pytest.raises(RuntimeError, match="unexpected payload"):
        service._read_release_manifest("https://example.com/manifest.json")


def test_resolve_asset_sha256_returns_direct_value_lowercased():
    service = UpdateService()
    assert (
        service._resolve_asset_sha256({"name": "a.zip", "sha256": "ABC123"}) == "abc123"
    )


def test_resolve_asset_sha256_empty_when_no_sha_and_no_manifest():
    service = UpdateService()
    assert service._resolve_asset_sha256({"name": "a.zip"}) == ""


def test_resolve_asset_sha256_raises_when_manifest_missing_entry(monkeypatch):
    service = UpdateService()
    monkeypatch.setattr(
        service,
        "_read_release_manifest",
        lambda url: {"assets": [{"name": "other.zip", "sha256": "deadbeef"}]},
    )
    with pytest.raises(RuntimeError, match="missing sha256"):
        service._resolve_asset_sha256(
            {"name": "a.zip", "manifest_download_url": "https://example.com/m.json"}
        )


# ===========================================================================
# Section G — status envelope, SOP body passthrough, caching
# ===========================================================================


def test_build_status_passes_release_body_verbatim(monkeypatch, tmp_path: Path):
    """SOP contract (CLAUDE.md): the in-app popup shows the first 200 chars of
    ``release_notes``, which must be the raw ``release.body`` GitHub returns."""
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    status = service._build_status(
        {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/release",
            "body": "## v9.9.9 raw body verbatim",
            "assets": [],
        }
    )
    assert status["release_notes"] == "## v9.9.9 raw body verbatim"
    assert status["latest_version"] == "9.9.9"
    # Envelope always carries the updater/channel scaffolding.
    for key in ("updater_enabled", "channel_name", "current_version", "checked_at"):
        assert key in status


def test_get_status_caches_within_ttl_and_force_refetches(monkeypatch, tmp_path: Path):
    _point_channel_at_tmp(monkeypatch, tmp_path)
    service = UpdateService()
    calls = {"n": 0}

    def fake_read():
        calls["n"] += 1
        return {"tag_name": "v9.9.9", "body": "b", "assets": []}

    monkeypatch.setattr(service, "_read_release_json", fake_read)

    service.get_status(force=True)  # fetch #1
    service.get_status(force=False)  # served from cache, no fetch
    assert calls["n"] == 1
    service.get_status(force=True)  # force bypasses cache => fetch #2
    assert calls["n"] == 2


def test_get_status_swallows_fetch_error_for_custom_channel(
    monkeypatch, tmp_path: Path
):
    """A non-default channel failure surfaces the configured-channel message and
    never raises out of get_status."""
    config_dir = _point_channel_at_tmp(monkeypatch, tmp_path)
    # Make the channel a (safe) custom proxy so is_default_github_channel=False.
    (config_dir / "update-channel.json").write_text(
        json.dumps(
            {
                "channel_name": "Custom Proxy",
                "api_url": "https://mirror.example/https://api.github.com/x",
                "web_url": "https://mirror.example/https://github.com/y",
                "download_url_prefix": "https://mirror.example/",
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService()

    def boom():
        raise TimeoutError("timed out")

    monkeypatch.setattr(service, "_read_release_json", boom)
    status = service.get_status(force=True)
    assert status["has_update"] is False
    assert "configured update channel" in status["error"]


# ===========================================================================
# Section H — restart orchestration
# ===========================================================================


def test_resolve_launcher_path_honors_env_launcher(monkeypatch, tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "my-custom-launch.sh").write_text("x", encoding="utf-8")
    monkeypatch.setattr(us, "PACKAGE_ROOT", pkg)
    monkeypatch.setenv("SD_IMAGE_SORTER_LAUNCHER", "my-custom-launch.sh")
    service = UpdateService()
    assert service._resolve_launcher_path() == pkg / "my-custom-launch.sh"


def test_resolve_launcher_path_raises_when_none_found(monkeypatch, tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    monkeypatch.setattr(us, "PACKAGE_ROOT", pkg)
    monkeypatch.delenv("SD_IMAGE_SORTER_LAUNCHER", raising=False)
    service = UpdateService()
    with pytest.raises(RuntimeError, match="launcher"):
        service._resolve_launcher_path()


def test_worker_command_targets_update_worker_with_manifest_flag(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(us, "PACKAGE_ROOT", tmp_path)
    service = UpdateService()
    manifest_path = tmp_path / "state" / "pending.json"
    command = service._worker_command(manifest_path)
    assert command[0] == us.sys.executable
    assert command[1].endswith("update_worker.py")
    assert command[-2:] == ["--manifest", str(manifest_path)]


def test_pending_manifest_path_sanitizes_version_into_filename(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(us, "UPDATE_DIR", tmp_path / "update")
    service = UpdateService()
    path = service._pending_manifest_path("9.9.9/../x")
    # Same sanitization used elsewhere: separators collapse to '-'.
    assert path.name == "pending-update-9.9.9-..-x.json"
    assert path.parent == tmp_path / "update" / "state"


def test_write_pending_manifest_records_archive_launcher_and_pid(
    monkeypatch, tmp_path: Path
):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    monkeypatch.setattr(us, "PACKAGE_ROOT", pkg)
    monkeypatch.setattr(us, "UPDATE_DIR", tmp_path / "update")
    service = UpdateService()
    launcher = pkg / "run.sh"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(service, "_resolve_launcher_path", lambda: launcher)

    archive_path = tmp_path / "downloads" / "sd-image-sorter-v9.9.9-app-patch.zip"
    manifest_path = service._write_pending_manifest(
        archive_path=archive_path,
        version="9.9.9",
        relaunch=False,
        current_pid=424242,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["archive_path"] == str(archive_path)
    assert payload["target_version"] == "9.9.9"
    assert payload["launcher_path"] == str(launcher)
    assert payload["relaunch"] is False
    assert payload["current_pid"] == 424242
    assert payload["package_root"] == str(pkg)
