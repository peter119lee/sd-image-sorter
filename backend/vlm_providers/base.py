"""Base class and data types for VLM providers."""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image

# The segment-shape rules ("is this comma segment a tag or leaked prose?") moved
# to ``caption_format`` in 2026-08 so this parser and the sidecar caption-format
# classifier share ONE implementation instead of two that drift. Rebound under
# the historical private names because this module's readers,
# ``tests/test_vlm_tag_parser.py`` and the migration-012 parity test all resolve
# them as ``vlm_providers.base.<name>`` and must get the same objects, not copies.
from caption_format import (
    PROSE_SUFFIX_CHARS as _PROSE_SUFFIX_CHARS,
    looks_like_garbage_tag as _looks_like_garbage_tag,
)

try:  # pragma: no cover - import shim
    from launcher_port import is_loopback_host
except Exception:  # pragma: no cover - launcher_port is stdlib-only; this is defensive
    def is_loopback_host(host: Optional[str]) -> bool:
        if not host:
            return False
        return host.strip().lower() in {"localhost", "127.0.0.1", "::1", "[::1]"}

logger = logging.getLogger(__name__)


def _redact_url(url: str) -> str:
    """Redact secrets in a URL for safe logging / display.

    Keeps the scheme + host (so a misconfiguration is still diagnosable) but
    strips any embedded ``user:pass@`` credentials and the query string, which
    can carry API keys (e.g. Gemini's ``?key=...``). Empty input passes through.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = host
    if parts.username:
        # Never echo embedded credentials back.
        netloc = f"***@{host}"
    query = "***" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def _url_is_secure_or_loopback(url: str) -> bool:
    """Return True when sending an API key over ``url`` is acceptable.

    https is always fine. Plain http is only acceptable for loopback hosts
    (local VLM servers such as Ollama / llama.cpp on 127.0.0.1, ::1 or
    localhost). An empty URL means the provider will use its own https default,
    so it is treated as safe.
    """
    if not url:
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme in ("https", "wss"):
        return True
    if scheme in ("http", "ws"):
        return is_loopback_host(parts.hostname)
    # Unknown / relative scheme: be conservative and treat as unsafe only when
    # it explicitly looks like cleartext; otherwise let the provider default win.
    return True


_ALLOWED_REQUEST_SCHEMES = frozenset({"http", "https"})


def assert_safe_request_url(url: str) -> None:
    """Reject endpoint URLs whose scheme is not http/https before connecting.

    A small SSRF hardening for the user-configured VLM endpoint (SEC-4): it
    blocks classic non-http request vectors — ``file://``, ``gopher://``,
    ``ftp://``, ``data:`` — that are never valid for an OpenAI-compatible API
    and that an attacker-influenced config could otherwise smuggle in.

    It deliberately does **not** block private, loopback, or link-local
    addresses. Local VLM servers (Ollama, LM Studio, llama.cpp on ``127.0.0.1``)
    are a first-class, documented use case, and the existing key-withholding
    guard in :class:`VLMConfig` already keeps secrets off cleartext non-loopback
    transports. Blocking internal IPs here would cap a core feature, not harden
    it, so DNS-rebinding/private-IP filtering is intentionally out of scope.

    Raises:
        ProviderError: when the scheme is unsupported or the host is missing.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ProviderError(f"Invalid endpoint URL: {exc}", error_type="config", retryable=False)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_REQUEST_SCHEMES:
        raise ProviderError(
            f"Unsupported endpoint scheme '{parts.scheme or ''}'. Use http:// or https://.",
            error_type="config",
            retryable=False,
        )
    if not parts.hostname:
        raise ProviderError(
            "Endpoint URL is missing a host. Set a full http(s):// endpoint.",
            error_type="config",
            retryable=False,
        )


# Output format constants
OUTPUT_FORMAT_NL = "nl_caption"
OUTPUT_FORMAT_TAGS = "danbooru_tags"
OUTPUT_FORMAT_BOTH = "both"
VALID_OUTPUT_FORMATS = {OUTPUT_FORMAT_NL, OUTPUT_FORMAT_TAGS, OUTPUT_FORMAT_BOTH}


class ProviderError(Exception):
    """Raised when a VLM provider encounters an error."""

    def __init__(self, message: str, error_type: str = "unknown", retryable: bool = False):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


@dataclass
class VLMConfig:
    """Configuration for a VLM provider instance."""

    provider: str = "openai_compat"
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    max_retries: int = 3
    retry_delay_seconds: float = 2.0
    timeout_seconds: float = 60.0
    concurrent_requests: int = 2
    system_prompt: str = ""
    user_prompt: str = ""
    user_prompt_with_tags: str = ""  # Used when image already has danbooru tags
    include_tags_as_context: bool = True
    max_image_size: int = 1024
    nsfw_retry_prompt: str = ""

    # v3.2.1 additions
    output_format: str = OUTPUT_FORMAT_NL  # nl_caption | danbooru_tags | both
    # v3.4.3: caption generation parameters, previously hardcoded per provider
    # (1024 / 0.3). The analyze path keeps its own fixed 2048 / 0.1.
    caption_max_tokens: int = 1024
    caption_temperature: float = 0.3
    http_proxy: str = ""    # HTTP proxy URL (e.g., http://proxy:8080)
    https_proxy: str = ""   # HTTPS proxy URL
    socks_proxy: str = ""   # SOCKS proxy URL (e.g., socks5://localhost:1080)
    # Vertex AI specific (only used when provider=='gemini' and using Vertex)
    use_vertex: bool = False
    vertex_project: str = ""
    vertex_location: str = "us-central1"
    service_account_json: str = ""  # JSON content or path to file

    def __post_init__(self) -> None:
        """Refuse to transmit the API key over an insecure transport.

        Local VLM servers legitimately use plain ``http://`` on a loopback
        host, so those are allowed untouched. But if an API key is configured
        and the endpoint (or a key-carrying proxy) is non-loopback ``http://``,
        sending the key would leak it in cleartext. We strip the key in that
        case rather than transmit it — the request will then fail auth loudly
        instead of leaking the secret silently. This is a security guard, not a
        feature limit: switching the endpoint to https (or pointing at a
        loopback host) restores the key.
        """
        if not self.api_key:
            return

        insecure_targets: List[str] = []
        if not _url_is_secure_or_loopback(self.endpoint):
            insecure_targets.append(self.endpoint)
        # A cleartext proxy can observe the key even if the endpoint is https.
        for proxy_url in (self.http_proxy, self.https_proxy, self.socks_proxy):
            if proxy_url and not _url_is_secure_or_loopback(proxy_url):
                insecure_targets.append(proxy_url)

        if insecure_targets:
            logger.warning(
                "VLM API key withheld: refusing to send it over a non-loopback "
                "cleartext (http) transport %s. Use https or a loopback host.",
                [_redact_url(u) for u in insecure_targets],
            )
            self.api_key = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": _redact_url(self.endpoint),
            "api_key": "***" if self.api_key else "",
            "model": self.model,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "timeout_seconds": self.timeout_seconds,
            "concurrent_requests": self.concurrent_requests,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "user_prompt_with_tags": self.user_prompt_with_tags,
            "include_tags_as_context": self.include_tags_as_context,
            "max_image_size": self.max_image_size,
            "nsfw_retry_prompt": self.nsfw_retry_prompt,
            "output_format": self.output_format,
            "http_proxy": _redact_url(self.http_proxy),
            "https_proxy": _redact_url(self.https_proxy),
            "socks_proxy": _redact_url(self.socks_proxy),
            "use_vertex": self.use_vertex,
            "vertex_project": self.vertex_project,
            "vertex_location": self.vertex_location,
            "service_account_json": "***" if self.service_account_json else "",
        }

    def get_proxies(self) -> Optional[Dict[str, str]]:
        """Build proxies dict for httpx based on config. Returns None if no proxies."""
        proxies: Dict[str, str] = {}
        if self.socks_proxy:
            # SOCKS proxies apply to both http and https
            proxies["http://"] = self.socks_proxy
            proxies["https://"] = self.socks_proxy
        else:
            if self.http_proxy:
                proxies["http://"] = self.http_proxy
            if self.https_proxy:
                proxies["https://"] = self.https_proxy
        return proxies or None


@dataclass
class VLMResult:
    """Result from a VLM captioning request."""

    caption: str = ""           # Natural language caption
    tags: List[str] = field(default_factory=list)  # Parsed danbooru-style tags
    tokens_used: int = 0
    error: Optional[str] = None
    error_type: Optional[str] = None
    retries_used: int = 0
    model: str = ""
    raw_text: str = ""          # Original raw output for debugging
    truncated: bool = False     # True when the provider cut output at the token cap


def encode_image_base64(image_path: str, max_size: int = 1024) -> str:
    """Load and resize image, return base64-encoded JPEG."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            img = img.resize(
                (int(w * scale), int(h * scale)),
                getattr(Image, "Resampling", Image).LANCZOS,
            )
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _socks_proxy_missing_error(exc: Exception) -> "ProviderError":
    """Build the actionable error for a SOCKS proxy without the socksio backend.

    httpx raises a bare ``ImportError`` ("install httpx[socks]") when a SOCKS
    proxy is requested but the optional ``socksio`` package is absent. We turn
    that into a config-level :class:`ProviderError` so the request surfaces a
    clear, fixable message instead of crashing — and so we never silently fall
    back to a direct connection, which would leak the connection the user
    explicitly wanted tunnelled through SOCKS.
    """
    return ProviderError(
        "A SOCKS proxy is configured but the 'socksio' package is not installed. "
        "Install it with: pip install \"httpx[socks]\"  — or switch to an HTTP/HTTPS "
        "proxy in VLM Settings.",
        error_type="config",
        retryable=False,
    )


def make_async_client(config: VLMConfig, timeout: Optional[float] = None) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient with proxy support derived from config.

    HTTP and HTTPS proxies work with stock httpx. SOCKS proxies additionally
    require the ``socksio`` package (the ``httpx[socks]`` extra); when it is
    missing we raise a clear :class:`ProviderError` (see
    :func:`_socks_proxy_missing_error`) rather than letting httpx's raw
    ImportError crash the request. A malformed (non-SOCKS) proxy config falls
    back to a direct connection so a typo never hard-fails captioning.
    """
    actual_timeout = timeout if timeout is not None else config.timeout_seconds
    proxies = config.get_proxies()
    kwargs: Dict[str, Any] = {"timeout": actual_timeout}
    if proxies:
        # httpx 0.28+ uses 'proxy' singular; older accepts 'proxies' dict.
        # Try modern API first.
        try:
            # Pick the appropriate proxy: if all schemes use the same proxy, use 'proxy'
            unique_proxies = set(proxies.values())
            if len(unique_proxies) == 1:
                kwargs["proxy"] = next(iter(unique_proxies))
            else:
                # Different proxies for http vs https - use mounts
                from httpx import AsyncHTTPTransport
                mounts = {scheme: AsyncHTTPTransport(proxy=url) for scheme, url in proxies.items()}
                kwargs["mounts"] = mounts
        except ImportError as e:
            # AsyncHTTPTransport(proxy=socks://...) with no socksio backend.
            raise _socks_proxy_missing_error(e) from e
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to apply proxy config (falling back to direct): {e}")
            kwargs.pop("proxy", None)
            kwargs.pop("mounts", None)
    try:
        return httpx.AsyncClient(**kwargs)
    except ImportError as e:
        # The single-proxy ``proxy=`` path builds its transport here, so a
        # missing socksio surfaces as ImportError at construction time.
        if config.socks_proxy:
            raise _socks_proxy_missing_error(e) from e
        logger.warning(f"Proxy client init failed ({e}); falling back to direct connection.")
        kwargs.pop("proxy", None)
        kwargs.pop("mounts", None)
        return httpx.AsyncClient(timeout=actual_timeout)
    except (TypeError, ValueError) as e:
        # Proxy kwarg shape rejected by this httpx version - retry without proxy
        logger.warning(f"Proxy unsupported ({e}); falling back to direct connection.")
        kwargs.pop("proxy", None)
        kwargs.pop("mounts", None)
        return httpx.AsyncClient(timeout=actual_timeout)


def detect_provider(endpoint: str) -> str:
    """Auto-detect provider type from endpoint URL pattern.

    Returns one of: 'anthropic', 'gemini', 'openai_compat'.
    Defaults to 'openai_compat' for unknown endpoints (covers Ollama, vLLM, OpenRouter, etc.).
    """
    if not endpoint:
        return "openai_compat"
    lower = endpoint.lower()
    if "anthropic.com" in lower or "/v1/messages" in lower:
        return "anthropic"
    if "googleapis.com" in lower or "generativelanguage" in lower or "aiplatform" in lower:
        return "gemini"
    return "openai_compat"


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Strip ``<think>...</think>`` reasoning blocks from raw model output.

    Reasoning models (DeepSeek-R1 distills, QwQ, some Qwen builds) wrap their
    chain-of-thought in ``<think>`` tags before the real answer. Left in, that
    CoT leaks into captions and danbooru tags. Handles a complete
    ``<think>..</think>`` block and the common truncated case where only the
    closing ``</think>`` survived (keep everything after it). Shared by the
    OpenAI-compatible provider and the ToriiGate tagger so the behaviour cannot
    drift between the two.
    """
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[-1]
    return cleaned.strip()


class VLMProvider:
    """Abstract base for VLM providers."""

    name: str = "base"

    def __init__(self, config: VLMConfig):
        self.config = config

    async def caption_image(
        self,
        image_path: str,
        *,
        tags: Optional[List[str]] = None,
    ) -> VLMResult:
        """Generate a natural language caption (or tags) for an image.

        Result depends on config.output_format:
        - nl_caption: VLMResult.caption populated, tags empty
        - danbooru_tags: VLMResult.tags populated, caption may be empty
        - both: both populated (parsed from hybrid output)
        """
        raise NotImplementedError

    async def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.1,
    ) -> VLMResult:
        """Generate text without an image.

        Dataset Maker uses this for translation. Providers share the same
        endpoint/auth settings as VLM captioning but receive text-only chat
        payloads, so translation failures never touch image-caption state.
        """
        raise NotImplementedError

    async def test_connection(self) -> Dict[str, Any]:
        """Test if the provider is reachable. Returns status dict."""
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        """Fetch available models from the provider. May return empty list."""
        return []

    def build_user_message(self, tags: Optional[List[str]] = None) -> str:
        """Build the user prompt, optionally including tags as context."""
        tag_str = ", ".join(tags) if tags else ""

        # If tags exist and we have a dedicated with-tags prompt, use it
        if tags and self.config.include_tags_as_context and self.config.user_prompt_with_tags:
            prompt = self.config.user_prompt_with_tags
            prompt = prompt.replace("{tags}", tag_str)
            return prompt.strip()

        # Fallback: use the regular user_prompt
        prompt = self.config.user_prompt
        if tags and self.config.include_tags_as_context:
            if "{tags}" in prompt:
                prompt = prompt.replace("{tags}", tag_str)
            else:
                prompt += f"\n\nThe following danbooru-style tags describe this image:\n{tag_str}"
        else:
            prompt = prompt.replace("{tags}", "")

        return prompt.strip()

    def parse_output(self, raw_text: str) -> VLMResult:
        """Parse raw VLM text output into VLMResult based on output_format.

        Supports:
        - nl_caption: returns text as caption
        - danbooru_tags: parses comma/newline-separated tags
        - both: parses <NL>...</NL><TAGS>...</TAGS> hybrid format
        """
        result = VLMResult(raw_text=raw_text)
        text = raw_text.strip()

        if self.config.output_format == OUTPUT_FORMAT_NL:
            # Defensive: JSON-tuned models (and presets that pick nl_caption)
            # sometimes answer with {"description": ..., "tags": ...} anyway —
            # extract the prose instead of leaking raw JSON into captions.
            jsonish_caption = _extract_caption_from_jsonish(text)
            result.caption = text if jsonish_caption is None else jsonish_caption
            return result

        if self.config.output_format == OUTPUT_FORMAT_TAGS:
            result.tags = _parse_tag_list(text)
            return result

        if self.config.output_format == OUTPUT_FORMAT_BOTH:
            nl_part, tags_part = _parse_hybrid_output(text)
            # If no structured format was found (the model ignored the JSON /
            # marker contract), fall back to a shape-based split so a weak
            # model's "tags, tags. Prose." blob doesn't dump booru tags into the
            # natural-language caption (the exact leak point 1 reported).
            if not nl_part and not tags_part:
                nl_part, tags_part = _heuristic_split_tags_prose(text)
            result.caption = nl_part
            result.tags = _parse_tag_list(tags_part) if tags_part else []
            # Last resort: nothing parseable -> keep the whole text as caption.
            if not result.caption and not result.tags:
                result.caption = text
            return result

        # Unknown format - treat as caption
        result.caption = text
        return result




def _parse_tag_list(text: str) -> List[str]:
    """Parse comma- or newline-separated tag list. Drops empty/whitespace-only tags.

    Filters obvious VLM-generated prose / markdown / LaTeX so the user's tag
    library doesn't end up storing chain-of-thought as searchable tags.
    """
    if not text:
        return []
    # Replace common delimiters with comma
    normalized = text.replace("\n", ",").replace(";", ",")
    raw_tags = [t.strip() for t in normalized.split(",")]
    # Filter empty, garbage, sentence-shaped, or excessively long tags
    return [t for t in raw_tags if t and not _looks_like_garbage_tag(t)]


def _strip_json_fence(text: str) -> str:
    """Remove a whole-response markdown code fence, if present."""
    import re as _re

    candidate = (text or "").strip()
    fence = _re.match(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", candidate, _re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return candidate


def _looks_like_jsonish_payload(text: str) -> bool:
    """Return True only for JSON-shaped model payloads, not ordinary prose.

    Captions such as ``[wide shot] ...`` or text containing
    ``"label": "value"`` must pass through unchanged. We only parse when the
    whole response starts like an object, a JSON array, a fenced JSON block, or
    a top-level caption/tag key without braces.
    """
    import re as _re

    candidate = _strip_json_fence(text)
    if not candidate:
        return False
    if candidate.startswith("{"):
        return True
    if _re.match(r'^\[\s*(?:\{|"|\])', candidate):
        return True
    return bool(
        _re.match(
            r'^"(?:description|caption|nl|nl_caption|natural_language|text|summary|tags|tag)"\s*:',
            candidate,
            _re.IGNORECASE,
        )
    )


def _try_parse_json_hybrid(text: str) -> Optional[tuple[str, str]]:
    """Extract ``(nl, tags)`` from a JSON object the model may have emitted.

    Handles ```json fenced blocks and leading/trailing prose by locating the
    outermost ``{...}``. Recognises description/caption/nl keys for the prose
    and a ``tags`` key (list or comma string). Returns ``None`` when no usable
    JSON object is present so the caller can try the next strategy.
    """
    import json as _json

    candidate = _strip_json_fence(text)

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = _json.loads(candidate[start:end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    nl = ""
    for key in ("description", "caption", "nl", "nl_caption", "natural_language"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            nl = value.strip()
            break

    tags_value = obj.get("tags")
    if isinstance(tags_value, list):
        tags_str = ", ".join(str(t).strip() for t in tags_value if str(t).strip())
    elif isinstance(tags_value, str):
        tags_str = tags_value.strip()
    else:
        tags_str = ""

    if nl or tags_str:
        return nl, tags_str
    return None


def _extract_caption_from_jsonish(text: str) -> Optional[str]:
    """Pull prose out of a JSON-shaped answer, or ``None`` if not JSON-shaped.

    nl_caption-mode models occasionally ignore the prose instruction and emit
    ``{"description": ..., "tags": ...}`` — sometimes truncated mid-string by
    the token cap. Returns the extracted caption (``""`` when the JSON held no
    prose, e.g. a tags-only object — booru tags come from the tagger, never
    from here) or ``None`` when the text does not look like JSON at all.
    """
    import json as _json
    import re as _re

    candidate = _strip_json_fence(text)
    if not _looks_like_jsonish_payload(candidate):
        return None

    parsed = _try_parse_json_hybrid(candidate)
    if parsed is not None:
        return parsed[0]

    try:
        parsed_json = _json.loads(candidate)
    except Exception:
        parsed_json = None
    if isinstance(parsed_json, list):
        strings = [
            item.strip()
            for item in parsed_json
            if isinstance(item, str) and item.strip()
        ]
        return " ".join(strings)

    # Truncated JSON: pull the (possibly unterminated) caption value.
    match = _re.search(
        r'"(?:description|caption|nl|nl_caption|natural_language|text|summary)"'
        r'\s*:\s*"((?:[^"\\]|\\.)*)',
        candidate,
    )
    if match:
        value = match.group(1).rstrip("\\")
        try:
            value = _json.loads(f'"{value}"')
        except Exception:
            value = value.replace('\\"', '"').replace("\\n", " ")
        return value.strip()

    # JSON-shaped but nothing prose-like to salvage (e.g. truncated tags-only
    # object): an empty caption beats leaking raw JSON into training data.
    return ""


def _heuristic_split_tags_prose(text: str) -> tuple[str, str]:
    """Best-effort split of an unstructured ``tags ... prose`` blob.

    Used only when a model ignored the requested JSON / marker contract. The
    goal is to keep a comma-separated booru-tag run out of the natural-language
    caption, not perfect recovery. Conservative: when no clear tag run is found
    it returns ``("", "")`` so the caller keeps the whole text as the caption
    and never invents tags from prose.
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""

    # Strategy 1: line-structured output (tags on their own line(s), prose on
    # others). A "tag line" has >=3 comma parts, no terminal sentence
    # punctuation, and a majority of tag-shaped parts.
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= 2:
        tag_lines: List[str] = []
        prose_lines: List[str] = []
        for line in lines:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            is_tag_line = (
                len(parts) >= 3
                and line[-1] not in _PROSE_SUFFIX_CHARS
                and sum(0 if _looks_like_garbage_tag(p) else 1 for p in parts) >= max(2, int(len(parts) * 0.6))
            )
            (tag_lines if is_tag_line else prose_lines).append(line)
        tags_str = ", ".join(tag_lines)
        prose = " ".join(prose_lines).strip()
        if tags_str and prose:
            return prose, tags_str

    # Strategy 2: single line "tag, tag, tag, ..., Prose sentence." — take the
    # leading run of tag-shaped comma segments; the first prose-shaped segment
    # starts the natural-language remainder.
    parts = [p.strip() for p in raw.replace("\n", " ").split(",")]
    parts = [p for p in parts if p]
    tag_run: List[str] = []
    for idx, part in enumerate(parts):
        if _looks_like_garbage_tag(part) or (part and part[-1] in _PROSE_SUFFIX_CHARS):
            if len(tag_run) >= 3:
                prose = ", ".join(parts[idx:]).strip()
                if prose:
                    return prose, ", ".join(tag_run)
            break
        tag_run.append(part)
    else:
        # Every comma segment looked like a tag (no prose region) — route the
        # whole blob to tags rather than dumping a tag list into the caption.
        if len(tag_run) >= 3:
            return "", ", ".join(tag_run)

    return "", ""


def _parse_hybrid_output(text: str) -> tuple[str, str]:
    """Parse hybrid (NL + tags) output, robust to weak models.

    Tries, in order: a JSON object (``{"description": .., "tags": ..}``);
    XML-style ``<NL>../<TAGS>..`` markers; ``Description:`` / ``Tags:`` section
    headers. Returns ``(nl_text, tags_text)``; either may be empty when not
    found. The purely shape-based fallback for marker-less output lives in
    ``_heuristic_split_tags_prose`` and is applied by the caller.
    """
    import re

    json_result = _try_parse_json_hybrid(text)
    if json_result is not None:
        return json_result

    nl_match = re.search(r"<NL>(.*?)</NL>", text, re.DOTALL | re.IGNORECASE)
    tags_match = re.search(r"<TAGS>(.*?)</TAGS>", text, re.DOTALL | re.IGNORECASE)
    nl_text = nl_match.group(1).strip() if nl_match else ""
    tags_text = tags_match.group(1).strip() if tags_match else ""
    if nl_text or tags_text:
        return nl_text, tags_text

    # Fallback: "Description: ...\nTags: ..." style section headers.
    desc_match = re.search(r"(?:description|caption)[:：]\s*(.+?)(?:\n\s*tags?[:：]|\Z)", text, re.IGNORECASE | re.DOTALL)
    tag_match = re.search(r"tags?[:：]\s*(.+?)\Z", text, re.IGNORECASE | re.DOTALL)
    if desc_match or tag_match:
        return (
            desc_match.group(1).strip() if desc_match else "",
            tag_match.group(1).strip() if tag_match else "",
        )

    return "", ""
