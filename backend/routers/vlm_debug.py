"""Pure debug/redaction/coercion helpers for the VLM captioning router.

Decomposed from routers/vlm.py (2026-07): a verbatim slice of the pre-split
lines 205-250, 271-295 and 326-357. Import routers.vlm (the facade), NOT this module -- the facade
re-imports every helper BY REFERENCE, so callers (_append_debug_chat_event,
_build_config, _run_batch) resolve them as facade globals and monkeypatches
on the facade keep biting. Everything here is stateless: no module global
is mutated and no debug-chat ring state lives here (that stays on the
facade with the _debug_chat_next_id rebind seam).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit

from vlm_providers import VLMConfig

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact_debug_text(value: Any, limit: int = 5000) -> str:
    text = "" if value is None else str(value)
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text


def _redact_debug_endpoint(value: Any) -> str:
    """Hide endpoint credentials and query tokens before exposing debug events."""
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return _redact_debug_text(endpoint.split("?", 1)[0], 500)
    if not parsed.scheme or not parsed.netloc:
        return _redact_debug_text(endpoint.split("?", 1)[0], 500)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    redacted_query = "..." if parsed.query else ""
    redacted = urlunsplit((parsed.scheme, host, parsed.path, redacted_query, ""))
    return _redact_debug_text(redacted, 500)


def _coerce_int_setting(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    return max(minimum, min(maximum, coerced))


def _coerce_float_setting(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        coerced = default
    return max(minimum, min(maximum, coerced))


def _build_debug_request_event(
    *,
    image_id: int,
    image_name: str,
    config: VLMConfig,
    provider_name: str,
    tags: List[str],
    user_message: str,
) -> Dict[str, Any]:
    return {
        "phase": "request",
        "image_id": image_id,
        "image_name": image_name,
        "provider": provider_name,
        "model": config.model,
        "output_format": config.output_format,
        "endpoint": _redact_debug_endpoint(config.endpoint),
        "system_prompt": _redact_debug_text(config.system_prompt),
        "user_prompt": _redact_debug_text(user_message),
        "tags": tags[:120],
        "tags_count": len(tags),
        "note": "Image bytes are sent to the API but hidden here; API keys and base64 payloads are never shown.",
    }




def _normalize_openai_endpoint(url: str) -> str:
    """Auto-append ``/v1`` for OpenAI-compatible endpoints missing the version path.

    A common new-user mistake is to paste ``https://aihubmix.com`` (or any
    other OpenAI gateway) into the VLM endpoint field without the ``/v1``
    suffix. The provider then builds ``https://aihubmix.com/chat/completions``
    which the gateway's CDN often answers with an XML 401 "AuthenticationRequired"
    from the underlying object storage instead of a useful API error.

    We only touch URLs whose path is empty or ``/``. URLs that already have
    any non-trivial path (e.g. ``/v1``, ``/openai/v1``, ``/api/proxy``) are
    left alone — the user explicitly chose them.
    """
    if not url:
        return url
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return cleaned
    try:
        from urllib.parse import urlparse

        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            return cleaned  # not a parseable URL; do not mangle
        path = parsed.path or ""
        if path in ("", "/"):
            return cleaned + "/v1"
        return cleaned
    except Exception:
        return cleaned
