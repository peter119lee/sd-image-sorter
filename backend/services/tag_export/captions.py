"""Caption/sidecar content rendering core (split from services/tag_export_service.py).

Moved verbatim. THIS module is the
single ORIGIN of the identity-shared objects ``NL_COMPOSE_MODES`` /
``VALID_CONTENT_MODES`` and of the re-exported callables
``build_sidecar_content`` / ``compose_caption_with_nl`` /
``apply_caption_transforms``: services.tag_export_service re-binds them by
reference, and services.dataset_export_service plus the
services/dataset_export/ submodules origin-import the SAME objects through
that facade — so each must be defined exactly once here and never
re-declared (`is`-identity pins in tests/test_tag_export_pins.py and
tests/test_dataset_export_pins.py trip on clones).

The lazy in-function imports (services.export_template_engine, tag_rules,
services.tag_training_filters) are origin-module seams and stay verbatim.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException


PARAMETER_EXPORT_ORDER = [
    ("steps", "Steps"),
    ("sampler", "Sampler"),
    ("schedule_type", "Schedule type"),
    ("cfg_scale", "CFG scale"),
    ("seed", "Seed"),
    ("size", "Size"),
    ("model", "Model"),
    ("model_hash", "Model hash"),
    ("clip_skip", "Clip skip"),
    ("denoising_strength", "Denoising strength"),
    ("loras", "LoRAs"),
]

VALID_CONTENT_MODES = {
    "tags",
    "prompt",
    "negative",
    "prompt_negative",
    "a1111",
    "caption_tags",
    "caption_merged",
    "json",
    # v3.2.1 additions
    "nl_caption",      # Pure natural language caption (ai_caption only)
    "tags_nl",         # Tags + natural language caption, without original prompt
    "prompt_nl",       # Original prompt + NL caption
    "template",        # Uses export_template_engine with preset/template options
}


def extract_generation_params(image: Dict[str, Any]) -> Dict[str, Any]:
    """Extract normalized generation parameters from a stored image row."""
    metadata = image.get("metadata") if isinstance(image.get("metadata"), dict) else None
    if metadata is None:
        raw_metadata = image.get("metadata_json")
        if isinstance(raw_metadata, str) and raw_metadata.strip():
            try:
                metadata = json.loads(raw_metadata)
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
        else:
            metadata = {}

    parsed = metadata.get("_parsed") if isinstance(metadata, dict) else {}
    params = parsed.get("generation_params") if isinstance(parsed, dict) else {}
    normalized = dict(params) if isinstance(params, dict) else {}

    if not normalized.get("model") and image.get("checkpoint"):
        normalized["model"] = image.get("checkpoint")
    if not normalized.get("model_hash") and image.get("model_hash"):
        normalized["model_hash"] = image.get("model_hash")
    if not normalized.get("size") and image.get("width") and image.get("height"):
        normalized["size"] = f"{image.get('width')}x{image.get('height')}"
    if not normalized.get("loras") and image.get("loras"):
        loras = image.get("loras")
        if isinstance(loras, str):
            try:
                loaded = json.loads(loras)
                if isinstance(loaded, list):
                    loras = loaded
            except (TypeError, ValueError, json.JSONDecodeError):
                loras = [part.strip() for part in loras.split(",") if part.strip()]
        if isinstance(loras, list) and loras:
            normalized["loras"] = ", ".join(str(item) for item in loras if str(item).strip())

    return normalized


def build_a1111_parameters_text(image: Dict[str, Any]) -> str:
    """Build a Stable Diffusion WebUI/A1111-style prompt block."""
    prompt = str(image.get("prompt") or "").strip()
    negative_prompt = str(image.get("negative_prompt") or "").strip()
    generation_params = extract_generation_params(image)

    lines: List[str] = []
    if prompt:
        lines.append(prompt)
    if negative_prompt:
        lines.append(f"Negative prompt: {negative_prompt}")

    emitted = set()
    parts: List[str] = []
    for key, label in PARAMETER_EXPORT_ORDER:
        value = generation_params.get(key)
        if value is None or value == "":
            continue
        emitted.add(key)
        parts.append(f"{label}: {value}")

    for key in sorted(k for k in generation_params.keys() if k not in emitted):
        value = generation_params.get(key)
        if value is None or value == "":
            continue
        label = " ".join(part.capitalize() for part in str(key).split("_"))
        parts.append(f"{label}: {value}")

    if parts:
        lines.append(", ".join(parts))

    return "\n".join(lines).strip()


def _filter_tags(tags: List[Dict[str, Any]], blacklist: set[str]) -> List[str]:
    return [
        str(tag.get("tag") or "").strip()
        for tag in tags
        if str(tag.get("tag") or "").strip()
        and str(tag.get("tag") or "").strip().lower() not in blacklist
    ]


# Default underscore-preservation prefixes for the LoRA-friendly export path.
# Re-exported from ``export_template_engine`` so the same convention applies
# whether you run the basic ``tags`` mode or the template engine.
LORA_PRESERVE_UNDERSCORE_PREFIXES = ["score_"]


# Content modes that emit danbooru-style tag tokens. Underscore-to-space
# normalization defaults to ON for these so LoRA trainers receive
# ``multiple girls`` (with ``score_5`` preserved) instead of
# ``multiple_girls``. Modes producing free-form text (prompt, NL caption,
# A1111 parameter blocks) are left untouched because users may have written
# deliberate underscores into their original prompts.
DANBOORU_TAG_CONTENT_MODES = {
    "tags",
    "caption_tags",
    "caption_merged",
    "tags_nl",
}


def _maybe_normalize_underscores(
    tags: List[str],
    *,
    normalize: bool,
    preserve_prefixes: Optional[List[str]] = None,
) -> List[str]:
    """Apply LoRA-friendly underscore-to-space conversion to a list of tags."""
    if not normalize:
        return tags
    from services.export_template_engine import normalize_lora_tag
    prefixes = list(preserve_prefixes) if preserve_prefixes is not None else LORA_PRESERVE_UNDERSCORE_PREFIXES
    return [normalize_lora_tag(t, prefixes) for t in tags]


def _resolve_underscore_normalization(
    content_mode: str,
    normalize_tag_underscores: Optional[bool],
) -> bool:
    """Pick the effective underscore normalization flag for ``content_mode``.

    ``normalize_tag_underscores`` is the request override (``True``, ``False``
    or ``None`` for default). When ``None`` we apply normalization for every
    danbooru-tag content mode (the LoRA-trainer expectation) and skip it for
    NL / prompt / a1111 / json modes. ``template`` mode is also skipped here
    because the template engine performs its own per-preset normalization
    using the same underlying utility.
    """
    if normalize_tag_underscores is True:
        return True
    if normalize_tag_underscores is False:
        return False
    return str(content_mode or "").strip().lower() in DANBOORU_TAG_CONTENT_MODES


def _join_caption_parts(parts: List[str]) -> str:
    seen = set()
    output: List[str] = []
    for part in parts:
        normalized = " ".join(str(part or "").split()).strip(",")
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return ", ".join(output)


def _split_caption_transform_tokens(value: str) -> List[str]:
    return [
        " ".join(part.split()).strip(" ,")
        for part in str(value or "").replace("\n", ",").split(",")
        if " ".join(part.split()).strip(" ,")
    ]


def _normalize_caption_transform_token(value: str) -> str:
    return " ".join(str(value or "").replace("_", " ").split()).strip().lower()


def _coerce_transform_token_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    out: List[str] = []
    seen: set[str] = set()
    for raw in values:
        token = " ".join(str(raw or "").split()).strip(" ,")
        if not token:
            continue
        key = _normalize_caption_transform_token(token)
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def apply_caption_transforms(content: str, transforms: Optional[Dict[str, Any]]) -> str:
    """Apply token-level caption transforms without loading captions in the UI.

    The v321 caption editor can now say "add/remove from all selected images"
    as a compact rule. Export code applies that rule per image while streaming
    chunks from either explicit IDs or a selection token.
    """
    if not isinstance(transforms, dict) or not transforms:
        return str(content or "")

    prepend = _coerce_transform_token_list(transforms.get("prepend") or transforms.get("add_prepend"))
    append = _coerce_transform_token_list(transforms.get("append") or transforms.get("add_append"))
    remove = _coerce_transform_token_list(transforms.get("remove") or transforms.get("remove_tokens"))
    remove_categories = {
        str(category or "").strip().lower()
        for category in _coerce_transform_token_list(
            transforms.get("remove_categories") or transforms.get("removeCategories")
        )
        if str(category or "").strip()
    }
    dedupe = bool(transforms.get("dedupe") or prepend or append or remove or remove_categories)

    if not prepend and not append and not remove and not remove_categories and not dedupe:
        return str(content or "")

    tokens = _split_caption_transform_tokens(str(content or ""))
    remove_keys = {_normalize_caption_transform_token(token) for token in remove}
    if remove_keys:
        tokens = [token for token in tokens if _normalize_caption_transform_token(token) not in remove_keys]
    if remove_categories:
        try:
            from tag_rules import categorize_tag
            tokens = [
                token
                for token in tokens
                if str(categorize_tag(token) or "").strip().lower() not in remove_categories
            ]
        except Exception:
            # Category cleanup is a convenience layer. Exact add/remove
            # transforms must continue to work even if the categorizer is
            # unavailable in a packaged build.
            pass

    merged = [*prepend, *tokens, *append]
    if dedupe:
        output: List[str] = []
        seen: set[str] = set()
        for token in merged:
            key = _normalize_caption_transform_token(token)
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(token)
        merged = output

    return ", ".join(merged)


# Content modes whose rendered caption is booru-tags / template only — the
# per-image NL compose (caption editor's Booru/NL/Both control) layers the
# natural-language sentence on top of these. NL-aware modes (tags_nl,
# nl_caption, prompt_nl, caption_*) already emit the sentence globally, so
# compose is skipped for them to avoid doubling it. Shared with
# dataset_export_service — both export engines must gate identically.
NL_COMPOSE_MODES = {"template", "tags"}


def compose_caption_with_nl(rendered: str, caption_type: str, nl_text: str) -> str:
    """Join rule for the per-image Booru/NL/Both caption type.

    Single source of truth for both export engines; the frontend
    ``CaptionCore.compose`` mirrors this exactly — change them together.
    Returns ``rendered`` verbatim for any type other than "nl"/"both".

    The NL sentence is whitespace-flattened (P1-7): stored ai_caption text can
    span multiple lines, and kohya-style trainers read caption line 1 only, so
    a multi-line sentence would silently truncate the training caption.
    """
    ctype = str(caption_type or "").strip().lower()
    if ctype not in ("nl", "both"):
        return rendered
    booru = str(rendered or "").strip()
    text = " ".join(str(nl_text or "").split())
    if ctype == "nl":
        return text or booru
    # 'both' — tags first, then the sentence (matches the tags_nl mode order).
    if booru and text:
        return f"{booru}, {text}"
    return booru or text


def _coerce_int_str_map(raw: Optional[Dict[Any, Any]]) -> Dict[int, str]:
    """Normalise a JSON object keyed by image id into ``{int: str}``."""
    result: Dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                result[int(key)] = str(value or "")
            except (TypeError, ValueError):
                continue
    return result


def _image_nl_source_text(image: Dict[str, Any], image_id: int, nl_overrides: Dict[int, str]) -> str:
    """Resolve one image's natural-language caption text.

    Editor override first (an explicit empty string intentionally suppresses
    the stored sentence), then the stored pure NL, then the fused ai_caption
    for rows tagged before the nl_caption split existed.
    """
    if image_id in nl_overrides:
        return str(nl_overrides[image_id] or "")
    return str(image.get("nl_caption") or image.get("ai_caption") or "")


def _compose_nl_for_image(
    rendered: str,
    image: Dict[str, Any],
    image_id: int,
    *,
    content_mode: str,
    image_types: Dict[int, str],
    nl_overrides: Dict[int, str],
) -> str:
    """Apply the per-image caption type to an already-rendered caption.

    An explicit empty-string entry in ``nl_overrides`` intentionally
    suppresses the stored sentence (mirrors dataset_export_service).
    """
    caption_type = str(image_types.get(image_id, "") or "").strip().lower()
    if caption_type not in ("nl", "both"):
        return rendered
    if str(content_mode or "").strip().lower() not in NL_COMPOSE_MODES:
        return rendered
    nl_text = _image_nl_source_text(image, image_id, nl_overrides)
    return compose_caption_with_nl(rendered, caption_type, nl_text)


def _build_nl_sidecar_content(nl_text: str, trigger: str) -> str:
    """Build the split-export NL twin's content: single line, trigger first.

    kohya-style trainers read caption line 1 only, so the sentence is
    whitespace-flattened. The trigger is prepended unless already present
    (case-insensitive), because each sidecar must stand alone as a caption.
    """
    flattened = " ".join(str(nl_text or "").split())
    trigger_clean = str(trigger or "").strip().strip(",")
    if not trigger_clean:
        return flattened
    if trigger_clean.lower() in flattened.lower():
        return flattened
    return f"{trigger_clean}, {flattened}" if flattened else trigger_clean


def _filter_text_caption_tokens(value: str, blacklist: set[str]) -> List[str]:
    blocked = {" ".join(str(tag or "").split()).strip().lower() for tag in blacklist if str(tag or "").strip()}
    if not blocked:
        normalized = " ".join(str(value or "").split()).strip(",")
        return [normalized] if normalized else []

    output: List[str] = []
    for token in str(value or "").replace("\n", " ").split(","):
        normalized = " ".join(token.split()).strip(",")
        if not normalized:
            continue
        if normalized.lower() in blocked:
            continue
        output.append(normalized)
    return output


def _merge_template_blacklist_options(template_options: Optional[Dict[str, Any]], blacklist: set[str]) -> Dict[str, Any]:
    """Keep the export-modal blacklist authoritative for template sidecars too."""
    opts = dict(template_options or {})
    merged: List[str] = []
    seen: set[str] = set()
    sources = [opts.get("blacklist") or [], blacklist or set()]
    for source in sources:
        if isinstance(source, (str, bytes)):
            items = [source]
        else:
            items = source
        for raw_item in items:
            item = str(raw_item or "").strip()
            if not item:
                continue
            key = " ".join(item.split()).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    opts["blacklist"] = merged
    return opts


def build_sidecar_content(
    image: Dict[str, Any],
    tags: List[Dict[str, Any]],
    *,
    content_mode: str = "tags",
    blacklist: Optional[set[str]] = None,
    prefix: str = "",
    template_options: Optional[Dict[str, Any]] = None,
    normalize_tag_underscores: Optional[bool] = None,
    training_purpose: str = "",
    dedupe_implications: bool = False,
) -> str:
    """Build export content for one image according to a Pro SD workflow mode.

    For content_mode='template', template_options is required and may contain:
      preset_id, template_override, trigger, blacklist, replace_rules, max_tags,
      append, quality_override, safety_override, rating_override.

    ``normalize_tag_underscores`` controls whether danbooru-tag content modes
    (``tags``, ``caption_tags``, ``caption_merged``, ``tags_nl``) emit
    LoRA-friendly captions with underscores converted to spaces (``score_*``
    is always preserved). The default (``None``) follows the per-mode policy:
    tag modes normalize, free-form text modes (prompt, NL, a1111, json) do
    not. Pass ``False`` explicitly to keep underscores in tag modes; pass
    ``True`` to force normalization in modes that do not normalize by default
    (rarely useful — most callers should leave this at ``None``).
    """
    mode = str(content_mode or "tags").strip().lower()
    if mode not in VALID_CONTENT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid content_mode: {content_mode}")

    # P2-19 / P2-18 (2026-07-07): purpose filtering + implication dedup happen
    # on the tag ROWS before any mode dispatch, so every content mode —
    # including 'template', which receives the raw rows below — inherits them
    # and the export preview stays WYSIWYG with the written sidecars.
    if str(training_purpose or "").strip() or dedupe_implications:
        from services.tag_training_filters import apply_training_filters
        trigger_word = str((template_options or {}).get("trigger") or prefix or "")
        tags = apply_training_filters(
            tags,
            training_purpose=training_purpose,
            trigger_word=trigger_word,
            dedupe_implications=dedupe_implications,
        )

    blacklist = blacklist or set()
    filtered_tags = _filter_tags(tags, blacklist)
    prompt = str(image.get("prompt") or "").strip()
    negative_prompt = str(image.get("negative_prompt") or "").strip()
    caption = str(image.get("ai_caption") or "").strip()
    # Pure natural-language caption (point 1): prefer the dedicated nl_caption
    # column; fall back to the composed ai_caption for images tagged before the
    # split existed. The NL-oriented modes use this so booru tags don't leak in.
    nl_caption_text = str(image.get("nl_caption") or "").strip() or caption
    prefix = str(prefix or "").strip()

    # LoRA-friendly underscore normalization for danbooru-tag content modes.
    # Applied AFTER blacklist filtering (so the blacklist still works against
    # raw tag identifiers like ``multiple_girls``) but BEFORE the join, so
    # downstream consumers see ``multiple girls`` while ``score_5`` /
    # ``score_9_up`` survive intact.
    underscore_apply = _resolve_underscore_normalization(mode, normalize_tag_underscores)
    filtered_tags = _maybe_normalize_underscores(filtered_tags, normalize=underscore_apply)

    if mode == "tags":
        return _join_caption_parts(filtered_tags)
    if mode == "prompt":
        return prompt
    if mode == "negative":
        return negative_prompt
    if mode == "prompt_negative":
        return "\n".join(part for part in [prompt, f"Negative prompt: {negative_prompt}" if negative_prompt else ""] if part)
    if mode == "a1111":
        return build_a1111_parameters_text(image)
    if mode == "caption_tags":
        return _join_caption_parts([prefix, *_filter_text_caption_tokens(caption, blacklist), *filtered_tags])
    if mode == "caption_merged":
        return _join_caption_parts([
            prefix,
            *_filter_text_caption_tokens(caption, blacklist),
            *_filter_text_caption_tokens(prompt, blacklist),
            *filtered_tags,
        ])
    if mode == "nl_caption":
        # Pure natural language caption only
        return _join_caption_parts([prefix, *_filter_text_caption_tokens(nl_caption_text, blacklist)])
    if mode == "tags_nl":
        # Training-caption mode: local tags first, then natural-language caption; original prompt is excluded.
        return _join_caption_parts([prefix, *filtered_tags, *_filter_text_caption_tokens(nl_caption_text, blacklist)])
    if mode == "prompt_nl":
        # Original prompt + NL caption (separated by newline for clarity)
        parts = []
        if prefix:
            parts.append(prefix)
        parts.extend(_filter_text_caption_tokens(prompt, blacklist))
        parts.extend(_filter_text_caption_tokens(nl_caption_text, blacklist))
        return "\n".join(parts) if len(parts) > 1 else (parts[0] if parts else "")
    if mode == "template":
        # Use the export template engine
        from services.export_template_engine import build_export_caption
        opts = _merge_template_blacklist_options(template_options, blacklist)
        # Forward the underscore checkbox override so sidecar export matches preview
        if normalize_tag_underscores is False and "underscore_to_space_override" not in opts:
            opts["underscore_to_space_override"] = False
            opts.setdefault("preserve_underscore_prefixes_override", ["score_"])
        elif normalize_tag_underscores is True and "underscore_to_space_override" not in opts:
            opts["underscore_to_space_override"] = True
            opts.setdefault("preserve_underscore_prefixes_override", ["score_"])
        return build_export_caption(image, tags, **opts)
    if mode == "json":
        payload = {
            "id": image.get("id"),
            "filename": image.get("filename") or "",
            "generator": image.get("generator"),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "ai_caption": caption,
            "tags": filtered_tags,
            "checkpoint": image.get("checkpoint"),
            "width": image.get("width"),
            "height": image.get("height"),
            "generation_params": extract_generation_params(image),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    return ", ".join(filtered_tags)
