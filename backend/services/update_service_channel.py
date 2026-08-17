"""Update-channel resolution + SSRF host allowlist for the update service.

Split out of ``services/update_service.py`` (2026-07). The SSRF allowlist block (the host tables
plus _host_from_url / _host_is_github / _host_is_internal /
_is_safe_proxy_prefix / _is_safe_channel_url) moved VERBATIM: the
exact-or-subdomain boundary in _host_is_github and the 172.16-31 numeric
second-octet check in _host_is_internal ARE the security boundary
(tests/test_update_service_pins.py section B); do not simplify them.

The ONLY non-verbatim edits in this module (see the split manifest): reads
of the four facade-patched channel globals -- CONFIG_DIR, UPDATE_API_URL,
UPDATE_WEB_URL, UPDATE_DOWNLOAD_URL_PREFIX -- resolve through _svc() at
call time, because tests patch those names on the facade module object
(services.update_service); a bare re-import here would freeze independent
bindings those patches silently miss. Never-patched names
(GITHUB_LATEST_RELEASE_API_URL) import directly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app_info import GITHUB_LATEST_RELEASE_API_URL


logger = logging.getLogger("services.update_service")  # historical channel preserved (campaign rule)


# --- Update-channel SSRF hardening -------------------------------------------------
# The update channel can be overridden via update-channel.json (proxy-mirror
# feature) so users behind GitHub-blocking networks can still self-update. That
# override is attacker-influenceable if the config file is tampered with, so we
# refuse to fetch from arbitrary hosts: direct channel URLs must point at GitHub,
# and the opt-in proxy-prefix mirror must be https and must NOT resolve to an
# internal / loopback / link-local target. Invalid overrides are ignored and we
# fall back to the built-in GitHub channel instead of crashing.
_GITHUB_HOST_SUFFIXES = (
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    ".githubusercontent.com",
)
_INTERNAL_HOST_SUFFIXES = (
    ".internal",
    ".local",
)
_INTERNAL_HOST_EXACT = (
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
)
_INTERNAL_HOST_PREFIXES = (
    "127.",
    "10.",
    "192.168.",
    "169.254.",
    "0.",
)


def _host_from_url(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").strip().lower()
    except ValueError:
        return ""


def _host_is_github(host: str) -> bool:
    if not host:
        return False
    for suffix in _GITHUB_HOST_SUFFIXES:
        base = suffix.lstrip(".")
        # Require an exact host match or a real subdomain boundary ("." + base).
        # A bare host.endswith(base) would also accept attacker-registrable
        # lookalikes such as "evilgithub.com" (ends with "github.com").
        if host == base or host.endswith("." + base):
            return True
    return False


def _host_is_internal(host: str) -> bool:
    """Reject loopback, RFC1918, link-local, and internal-only hostnames."""
    if not host:
        # An empty / unparseable host is treated as unsafe.
        return True
    bracketless = host.strip("[]")
    if bracketless in _INTERNAL_HOST_EXACT:
        return True
    # IPv6 loopback / link-local (fe80::/10) / unique-local (fc00::/7). Scope
    # these prefixes to actual IPv6 literals so hostnames like "fdn.example.com"
    # are not misclassified as internal.
    if ":" in bracketless:
        if bracketless in {"::1", "::"} or bracketless.startswith(("fe80:", "fc", "fd")):
            return True
    if any(bracketless == suffix.lstrip(".") or bracketless.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES):
        return True
    if any(bracketless.startswith(prefix) for prefix in _INTERNAL_HOST_PREFIXES):
        return True
    # 172.16.0.0 – 172.31.255.255 (private range) needs a numeric second octet check.
    if bracketless.startswith("172."):
        parts = bracketless.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def _is_safe_proxy_prefix(prefix: str) -> bool:
    """A proxy/mirror prefix must be https and must not target an internal host."""
    normalized = str(prefix or "").strip()
    if not normalized.lower().startswith("https://"):
        return False
    return not _host_is_internal(_host_from_url(normalized))


def _is_safe_channel_url(url: str) -> bool:
    """Validate an api_url / web_url channel override.

    Accepts either a direct https GitHub URL, or a proxy-mirror form where a
    GitHub URL is appended after an https proxy prefix (e.g.
    ``https://mirror.example/https://github.com/...``). The proxy host itself
    must not be internal/loopback.
    """
    normalized = str(url or "").strip()
    if not normalized.lower().startswith("https://"):
        return False
    if _host_is_github(_host_from_url(normalized)):
        return True
    # Proxy-mirror form: an embedded https GitHub URL after the proxy prefix.
    embedded_index = normalized.find("https://", len("https://"))
    if embedded_index != -1:
        embedded = normalized[embedded_index:]
        if _host_is_github(_host_from_url(embedded)) and not _host_is_internal(
            _host_from_url(normalized)
        ):
            return True
    return False


def _svc():
    """Resolve facade-patched seams through services.update_service at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy import
    avoids a facade<->submodule load cycle.
    """
    import services.update_service as update_service

    return update_service


class _UpdateChannelMixin:
    """Channel-resolution / SSRF-guard surface of UpdateService (facade-assembled)."""

    def _channel_config_path(self) -> Path:
        return Path(_svc().CONFIG_DIR) / "update-channel.json"

    def _base_channel_state(self) -> dict[str, str]:
        return {
            "api_url": _svc().UPDATE_API_URL,
            "web_url": _svc().UPDATE_WEB_URL,
            "download_url_prefix": _svc().UPDATE_DOWNLOAD_URL_PREFIX,
        }

    def _read_channel_override(self) -> dict[str, Any]:
        path = self._channel_config_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read update channel override: %s", exc)
            return {}
        if not isinstance(payload, dict):
            return {}
        return self._sanitize_channel_override(payload)

    def _sanitize_channel_override(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drop any override URL that fails the SSRF host allowlist.

        Reading the override file is a trust boundary: a tampered config must
        never be able to redirect update fetches at an internal/loopback host
        or a non-GitHub origin. Invalid fields are dropped with a warning so we
        fall back to the built-in GitHub channel instead of crashing.
        """
        sanitized: dict[str, Any] = {}
        if "channel_name" in payload:
            sanitized["channel_name"] = payload.get("channel_name")

        api_url = str(payload.get("api_url") or "").strip()
        if api_url:
            if _is_safe_channel_url(api_url):
                sanitized["api_url"] = api_url
            else:
                logger.warning("Ignoring update channel override api_url (failed SSRF allowlist): %s", api_url)

        web_url = str(payload.get("web_url") or "").strip()
        if web_url:
            # web_url is only ever opened in the user's browser, but keep it on
            # the same allowlist so the displayed channel stays consistent.
            if _is_safe_channel_url(web_url):
                sanitized["web_url"] = web_url
            else:
                logger.warning("Ignoring update channel override web_url (failed SSRF allowlist): %s", web_url)

        if "download_url_prefix" in payload:
            prefix = str(payload.get("download_url_prefix") or "").strip()
            if not prefix:
                # An explicit empty prefix means "no mirror"; keep it as-is.
                sanitized["download_url_prefix"] = prefix
            elif _is_safe_proxy_prefix(prefix):
                sanitized["download_url_prefix"] = prefix
            else:
                logger.warning(
                    "Ignoring update channel override download_url_prefix (failed SSRF allowlist): %s",
                    prefix,
                )
        return sanitized

    def _channel_state(self) -> dict[str, Any]:
        base = self._base_channel_state()
        override = self._read_channel_override()

        api_url = str(base["api_url"] or "").strip()
        web_url = str(base["web_url"] or "").strip()
        download_url_prefix = str(base["download_url_prefix"] or "").strip()

        if "api_url" in override:
            api_url = str(override.get("api_url") or "").strip() or api_url
        if "web_url" in override:
            web_url = str(override.get("web_url") or "").strip() or web_url
        if "download_url_prefix" in override:
            download_url_prefix = str(override.get("download_url_prefix") or "").strip()

        has_override = bool(override)
        is_default_github_channel = api_url.rstrip("/") == GITHUB_LATEST_RELEASE_API_URL.rstrip("/")

        return {
            "channel_name": str(override.get("channel_name") or "").strip() or (
                "GitHub Default" if is_default_github_channel and not has_override else "Custom Channel"
            ),
            "api_url": api_url,
            "web_url": web_url,
            "download_url_prefix": download_url_prefix,
            "has_override": has_override,
            "config_path": str(self._channel_config_path()),
            "is_default_github_channel": is_default_github_channel,
            "base_api_url": base["api_url"],
            "base_web_url": base["web_url"],
            "base_download_url_prefix": base["download_url_prefix"],
        }

    def _is_default_github_channel(self) -> bool:
        return bool(self._channel_state()["is_default_github_channel"])

    def _rewrite_download_url(self, url: str, prefix: str) -> str:
        normalized = str(url or "").strip()
        if not normalized:
            return ""
        if not prefix:
            return normalized
        if normalized.startswith(prefix):
            return normalized
        return f"{prefix}{normalized}"

    def _format_update_error(self, exc: Exception) -> str:
        detail = str(exc).strip() or exc.__class__.__name__
        if self._is_default_github_channel():
            return (
                f"Failed to reach the default GitHub update channel: {detail}. "
                "Your network may not be able to access GitHub directly. "
                "Please check your connection or enable VPN and try again."
            )
        return f"Failed to reach the configured update channel: {detail}"

    def get_channel_settings(self) -> dict[str, Any]:
        channel = self._channel_state()
        return {
            "channel_name": channel["channel_name"],
            "channel_api_url": channel["api_url"],
            "channel_web_url": channel["web_url"],
            "download_url_prefix": channel["download_url_prefix"],
            "has_channel_override": channel["has_override"],
            "channel_config_path": channel["config_path"],
            "is_default_github_channel": channel["is_default_github_channel"],
            "base_channel_api_url": channel["base_api_url"],
            "base_channel_web_url": channel["base_web_url"],
        }

    def save_proxy_channel(self, proxy_prefix: str, *, channel_name: str = "Custom Proxy") -> dict[str, Any]:
        normalized = str(proxy_prefix or "").strip()
        if not normalized:
            return self.reset_channel_settings()
        # SSRF guard: the proxy-mirror prefix must be https and must not point
        # at an internal/loopback target before we persist it as a channel.
        if not normalized.lower().startswith("https://"):
            raise RuntimeError("Update proxy prefix must start with https://")
        if not normalized.endswith(("/", "=", "?", "&")) and "?" not in normalized:
            normalized = normalized + "/"
        if not _is_safe_proxy_prefix(normalized):
            raise RuntimeError(
                "Update proxy prefix must be https and must not target an internal or loopback host"
            )

        base = self._base_channel_state()
        payload = {
            "channel_name": str(channel_name or "Custom Proxy").strip() or "Custom Proxy",
            "api_url": f"{normalized}{base['api_url']}",
            "web_url": f"{normalized}{base['web_url']}",
            "download_url_prefix": normalized,
        }
        # Re-validate the composed URLs so a hostile base channel value cannot
        # smuggle a non-GitHub / internal target past the prefix check.
        if not _is_safe_channel_url(payload["api_url"]):
            raise RuntimeError("Resulting update channel api_url failed the SSRF allowlist")

        path = self._channel_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._clear_cache()
        return self.get_channel_settings()

    def reset_channel_settings(self) -> dict[str, Any]:
        path = self._channel_config_path()
        path.unlink(missing_ok=True)
        self._clear_cache()
        return self.get_channel_settings()
