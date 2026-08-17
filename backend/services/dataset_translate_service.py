"""Dataset Maker translation service.

Extracted from ``routers/dataset.py`` in v3.4.5 — the translation
subsystem (cache, provider alias maps, seven no-key web provider
clients, VLM bridge, and the auto-chain fallback) previously lived
inside the router module, which made the router ~780 lines longer
than its job (translate HTTP <-> service) warrants.

Public surface:
  * ``DatasetTranslateRequest`` — the pydantic request model (kept here
    so the router imports it from the service, matching the other
    dataset request models).
  * ``translate_dataset_texts(payload, texts)`` — the entry point the
    route handler calls. Returns the response dict on success and
    raises ``fastapi.HTTPException`` on provider failure (the
    domain-exception migration is intentionally deferred — see
    TECHNICAL_DEBT_NOTES Debt-07).

Behaviour is byte-for-byte identical to the pre-extraction router
implementation; this commit only moves code.
"""
from __future__ import annotations

# Split (2026-07) into four sibling modules + this facade, every moved line
# VERBATIM (contract: tests/test_dataset_translate_pins.py, 41 pins +
# tests/test_dataset_translate.py, 11):
#
#   * dataset_translate_models    — DatasetTranslateRequest, alias maps, chains
#   * dataset_translate_cache     — the _TRANSLATION_CACHE rebind-seam HOME
#                                   (cache + lock + 50k cap + path/load/save/key)
#   * dataset_translate_parsing   — _parse_translation_output + lang/tag helpers
#   * dataset_translate_providers — the no-key/keyed provider clients (httpx seam)
#
# The four cache-aware orchestrators (_translate_external_uncached /
# _translate_external_with_cache / _translate_tag_texts_smart /
# _translate_external_texts) STAY DEFINED HERE: tests monkeypatch them on this
# module object and the next function up the chain must see the patch through
# a plain global lookup — a by-reference re-export cannot provide that.
#
# The three cache-STATE seams (_TRANSLATION_CACHE, _TRANSLATION_CACHE_MAX_ITEMS,
# DEFAULT_CACHE_DIR) are NOT re-imported by value; the module-class swap at the
# bottom live-forwards get/set on this module to dataset_translate_cache, so a
# facade-side monkeypatch and a cache-side ``global`` rebind stay one storage
# location (the #1 way this split could silently break).

import json
import sys
import types
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from services import dataset_translate_cache as _cache_home
from services.dataset_translate_cache import (
    _TRANSLATION_CACHE_LOCK,
    _load_translation_cache,
    _save_translation_cache,
    _translation_cache_key,
    _translation_cache_path,
)
from services.dataset_translate_models import (
    DatasetTranslateRequest,
    _DIRECT_TRANSLATION_PROVIDER_ALIASES,
    _GLOBAL_FREE_PROVIDER_CHAIN,
    _MAINLAND_FREE_PROVIDER_CHAIN,
    _TRANSLATORS_PROVIDER_ALIASES,
)
from services.dataset_translate_parsing import (
    _looks_like_tag_list,
    _parse_translation_output,
    _split_caption_tags,
    _translate_mode_for_texts,
    _translation_lang_code,
    _translation_source_lang,
)
from services.dataset_translate_providers import (
    _translate_baidu_sug_free,
    _translate_bing_keyed,
    _translate_custom_external,
    _translate_google_free,
    _translate_mymemory_free,
    _translate_translators_web,
)


class _DatasetTranslateFacade(types.ModuleType):
    """Live-forward the three cache-state seams to ``dataset_translate_cache``.

    Tests monkeypatch ``_TRANSLATION_CACHE`` / ``DEFAULT_CACHE_DIR`` /
    ``_TRANSLATION_CACHE_MAX_ITEMS`` on THIS module object while the bodies
    that read them (``_load/_save_translation_cache``,
    ``_translation_cache_path``) live in the cache module. Property
    forwarding keeps a single storage location, so a facade-side patch and
    the cache module's own ``global`` rebind always observe the same value.
    """

    @property
    def _TRANSLATION_CACHE(self):
        return _cache_home._TRANSLATION_CACHE

    @_TRANSLATION_CACHE.setter
    def _TRANSLATION_CACHE(self, value):
        _cache_home._TRANSLATION_CACHE = value

    @property
    def _TRANSLATION_CACHE_MAX_ITEMS(self):
        return _cache_home._TRANSLATION_CACHE_MAX_ITEMS

    @_TRANSLATION_CACHE_MAX_ITEMS.setter
    def _TRANSLATION_CACHE_MAX_ITEMS(self, value):
        _cache_home._TRANSLATION_CACHE_MAX_ITEMS = value

    @property
    def DEFAULT_CACHE_DIR(self):
        return _cache_home.DEFAULT_CACHE_DIR

    @DEFAULT_CACHE_DIR.setter
    def DEFAULT_CACHE_DIR(self, value):
        _cache_home.DEFAULT_CACHE_DIR = value


async def _translate_external_uncached(
    provider: str,
    texts: List[str],
    *,
    source_lang: str,
    target_lang: str,
    mode: str,
) -> Dict[str, Any]:
    provider = str(provider or "").strip().lower()
    direct_provider = _DIRECT_TRANSLATION_PROVIDER_ALIASES.get(provider)
    if direct_provider == "google":
        translations = await _translate_google_free(texts, source_lang=source_lang, target_lang=target_lang)
        provider_name = "google"
    elif direct_provider == "mymemory":
        translations = await _translate_mymemory_free(texts, source_lang=source_lang, target_lang=target_lang)
        provider_name = "mymemory"
    elif direct_provider == "baidu":
        translations = await _translate_baidu_sug_free(texts, mode=mode)
        provider_name = "baidu"
    elif provider in _TRANSLATORS_PROVIDER_ALIASES:
        translations = await _translate_translators_web(
            provider,
            texts,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        provider_name = _TRANSLATORS_PROVIDER_ALIASES.get(provider, provider)
    elif provider == "bing":
        translations = await _translate_bing_keyed(texts, source_lang=source_lang, target_lang=target_lang)
        provider_name = "bing"
    elif provider == "custom":
        translations = await _translate_custom_external(
            texts,
            source_lang=source_lang,
            target_lang=target_lang,
            mode=mode,
        )
        provider_name = "custom"
    else:
        raise RuntimeError(f"Unknown external translation provider: {provider}")
    return {
        "translations": (translations + [""] * len(texts))[:len(texts)],
        "provider": provider_name,
    }


async def _translate_external_with_cache(
    provider: str,
    texts: List[str],
    *,
    source_lang: str,
    target_lang: str,
    mode: str,
) -> Dict[str, Any]:
    cache = _load_translation_cache()
    keys = [
        _translation_cache_key(provider, source_lang=source_lang, target_lang=target_lang, mode=mode, text=text)
        for text in texts
    ]
    out: List[Optional[str]] = [cache.get(key) for key in keys]
    missing_index_by_text: Dict[str, List[int]] = {}
    for idx, value in enumerate(out):
        if value is None:
            missing_index_by_text.setdefault(texts[idx], []).append(idx)
    if missing_index_by_text:
        missing_texts = list(missing_index_by_text.keys())
        translated = await _translate_external_uncached(
            provider,
            missing_texts,
            source_lang=source_lang,
            target_lang=target_lang,
            mode=mode,
        )
        provider_name = str(translated.get("provider") or provider)
        changed = False
        for text, translation in zip(missing_texts, translated.get("translations") or []):
            for idx in missing_index_by_text.get(text, []):
                value = str(translation or "").strip()
                out[idx] = value
                if value:
                    cache[keys[idx]] = value
                    changed = True
        if changed:
            _save_translation_cache()
    else:
        provider_name = provider
    return {
        "translations": [str(item or "") for item in out],
        "provider": provider_name,
        "cache_hits": len(texts) - len(missing_index_by_text),
        "cache_misses": len(missing_index_by_text),
    }


async def _translate_tag_texts_smart(
    provider: str,
    texts: List[str],
    *,
    source_lang: str,
    target_lang: str,
) -> Dict[str, Any]:
    token_order: List[str] = []
    seen_tokens: set[str] = set()
    tokenized: List[List[str]] = []
    for text in texts:
        tokens = _split_caption_tags(text)
        tokenized.append(tokens)
        for token in tokens:
            key = token.lower()
            if key in seen_tokens:
                continue
            seen_tokens.add(key)
            token_order.append(token)
    if not token_order:
        return {"translations": [""] * len(texts), "provider": provider, "cache_hits": 0, "cache_misses": 0, "unique_terms": 0}
    translated = await _translate_external_with_cache(
        provider,
        token_order,
        source_lang=source_lang,
        target_lang=target_lang,
        mode="tag",
    )
    if not any(str(item or "").strip() for item in translated.get("translations") or []):
        raise RuntimeError(f"{provider} returned empty tag translations")
    by_lower = {
        source.lower(): target
        for source, target in zip(token_order, translated.get("translations") or [])
    }
    return {
        "translations": [
            ", ".join(by_lower.get(token.lower(), token) or token for token in tokens)
            for tokens in tokenized
        ],
        "provider": translated.get("provider") or provider,
        "cache_hits": translated.get("cache_hits", 0),
        "cache_misses": translated.get("cache_misses", 0),
        "unique_terms": len(token_order),
    }


async def _translate_external_texts(payload: DatasetTranslateRequest, texts: List[str]) -> Dict[str, Any]:
    provider = str(payload.external_provider or "auto").strip().lower()
    mode = _translate_mode_for_texts(payload.mode, texts)
    source_lang = str(payload.source_lang or "en").strip() or "en"
    target_lang = str(payload.target_lang or "zh-CN").strip() or "zh-CN"
    attempts = [provider]
    if provider in {"", "auto", "free", "auto_global"}:
        attempts = _GLOBAL_FREE_PROVIDER_CHAIN
    elif provider in {"auto_cn", "mainland", "china", "physton"}:
        attempts = _MAINLAND_FREE_PROVIDER_CHAIN

    errors: List[str] = []
    for name in attempts:
        try:
            if mode == "tags":
                translated = await _translate_tag_texts_smart(
                    name,
                    texts,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            else:
                translated = await _translate_external_with_cache(
                    name,
                    texts,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    mode=mode,
                )
                if any(texts) and not any(str(item or "").strip() for item in translated.get("translations") or []):
                    raise RuntimeError(f"{name} returned empty translations")
            return {
                "translations": (translated.get("translations", []) + [""] * len(texts))[:len(texts)],
                "provider_mode": "external",
                "provider": translated.get("provider") or name,
                "target_lang": target_lang,
                "source_lang": source_lang,
                "mode": mode,
                "cache_hits": translated.get("cache_hits", 0),
                "cache_misses": translated.get("cache_misses", 0),
                "unique_terms": translated.get("unique_terms", len(texts)),
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            if provider not in {"", "auto", "free", "auto_global", "auto_cn", "mainland", "china", "physton"}:
                break

    raise HTTPException(status_code=502, detail={
        "error": "; ".join(errors) or "External translation failed",
        "error_type": "external_provider_error",
        "provider": provider or "auto",
    })


# ------------------------------ public entry point ------------------------------

async def translate_dataset_texts(payload: DatasetTranslateRequest, texts: List[str]) -> Dict[str, Any]:
    """Translate Dataset Maker texts via the configured VLM or free web providers.

    Response contract:
    - Always: ``translations`` — list aligned 1:1 with ``payload.texts``.
    - VLM mode adds ``provider_mode='vlm'``, ``provider``, ``model``,
      ``tokens_used``.
    - External mode adds ``provider_mode='external'``, ``provider`` (the one
      that actually succeeded), ``source_lang``, ``target_lang``, ``mode``,
      ``cache_hits``, ``cache_misses``, ``unique_terms``.
    - Errors: 400 when VLM mode has no endpoint configured; 502 with
      ``{error, error_type, provider}`` detail when the provider (or every
      provider in an auto chain) fails or returns empty output.
    """
    if not texts:
        return {"translations": [], "provider_mode": payload.provider_mode}

    provider_mode = str(payload.provider_mode or "vlm").strip().lower()
    if provider_mode != "vlm":
        return await _translate_external_texts(payload, texts)

    from routers.vlm import _build_config
    from vlm_providers import get_provider

    config = _build_config()
    if not config.endpoint and not config.use_vertex:
        raise HTTPException(status_code=400, detail="No VLM endpoint configured for translation")

    mode = "tags" if str(payload.mode or "").lower() == "tags" else "caption"
    custom_prompt = str(payload.prompt or "").strip()
    system_prompt = (
        "You are a precise translation engine for Stable Diffusion dataset captions. "
        "Translate to Chinese. Preserve tag separators, order, counts, names, model tokens, "
        "and technical terms when translation would be harmful. Return only valid JSON."
    )
    instruction = custom_prompt or (
        "Translate each item to Simplified Chinese for human review. "
        "Return a JSON array of strings with exactly the same length as the input. "
        "For tag lists, keep comma-separated structure and translate understandable tag meanings; "
        "do not invent, remove, or reorder tags."
    )
    prompt = (
        f"Target language: {payload.target_lang}\n"
        f"Input mode: {mode}\n"
        f"{instruction}\n\n"
        f"Input JSON array:\n{json.dumps(texts, ensure_ascii=False)}"
    )
    provider = get_provider(config)
    result = await provider.generate_text(prompt, system_prompt=system_prompt, max_tokens=4096, temperature=0.1)
    if result.error:
        raise HTTPException(status_code=502, detail={
            "error": result.error,
            "error_type": result.error_type or "provider_error",
            "provider": config.provider,
            "model": config.model,
        })

    translations = _parse_translation_output(result.caption or result.raw_text, len(texts))
    if not any(str(item or "").strip() for item in translations):
        raise HTTPException(status_code=502, detail={
            "error": "Translation provider returned an empty translation",
            "error_type": "empty_response",
            "provider": config.provider,
            "model": config.model,
        })
    return {
        "translations": translations,
        "provider_mode": provider_mode,
        "provider": config.provider,
        "model": result.model or config.model,
        "tokens_used": result.tokens_used,
    }


sys.modules[__name__].__class__ = _DatasetTranslateFacade
