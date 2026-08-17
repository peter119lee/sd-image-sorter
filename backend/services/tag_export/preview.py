"""Export preview rendering (split from services/tag_export_service.py).

Moved verbatim. Import through
services.tag_export_service. ``import database as db`` keeps the shared
patch seam (tag_export_service.db.<fn> mutates the SHARED database module
object); the lazy in-function imports (services.export_template_engine,
services.tag_training_filters) stay verbatim.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

import database as db
from services.tag_export.captions import (
    VALID_CONTENT_MODES,
    apply_caption_transforms,
    build_sidecar_content,
)
from services.tag_export.selection import _normalize_export_image_ids


def render_export_preview(request: Any) -> Dict[str, Any]:
    """Render template-engine previews for a small image set without writing sidecars."""
    image_ids = _normalize_export_image_ids(getattr(request, "image_ids", []) or [])
    if len(image_ids) > 500:
        raise HTTPException(status_code=400, detail="Preview limited to 500 images at a time")

    from services.export_template_engine import build_export_caption

    # P1-7 preview unification: any real content mode previews through
    # build_sidecar_content — the exact engine the export writes with — so the
    # preview can never drift from the sidecar. Only the template designer
    # (content_mode absent or "template") goes through build_export_caption.
    content_mode = str(getattr(request, "content_mode", None) or "").strip().lower() or None
    use_native_mode = content_mode is not None and content_mode != "template"
    if use_native_mode and content_mode not in VALID_CONTENT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid content_mode: {content_mode}")
    caption_transforms = getattr(request, "caption_transforms", None) or {}

    images_map = db.get_images_by_ids(image_ids)
    tags_map = db.get_image_tags_map(image_ids)
    results: List[Dict[str, Any]] = []

    # P2-19 / P2-18: apply the training filters to the rows BEFORE either
    # preview branch so the template path (which calls build_export_caption
    # directly) matches the export engine's output. Row filtering is
    # idempotent, so the native branch below stays in sync too.
    preview_training_purpose = str(getattr(request, "training_purpose", "") or "")
    preview_dedupe = bool(getattr(request, "dedupe_implications", False))
    preview_trigger = str(getattr(request, "trigger", "") or getattr(request, "prefix", "") or "")

    for image_id in image_ids:
        image = images_map.get(image_id)
        if not image:
            results.append({"image_id": image_id, "error": "not_found", "rendered": ""})
            continue

        preview_rows = tags_map.get(image_id, []) or []
        if preview_training_purpose or preview_dedupe:
            from services.tag_training_filters import apply_training_filters
            preview_rows = apply_training_filters(
                preview_rows,
                training_purpose=preview_training_purpose,
                trigger_word=preview_trigger,
                dedupe_implications=preview_dedupe,
            )

        try:
            if use_native_mode:
                rendered = build_sidecar_content(
                    image,
                    preview_rows,
                    content_mode=str(content_mode),
                    blacklist=set(getattr(request, "blacklist", []) or []),
                    prefix=str(getattr(request, "prefix", "") or ""),
                    normalize_tag_underscores=getattr(request, "normalize_tag_underscores", None),
                )
            else:
                rendered = build_export_caption(
                image,
                preview_rows,
                preset_id=getattr(request, "preset_id", "custom"),
                template_override=getattr(request, "template_override", None),
                trigger=getattr(request, "trigger", ""),
                blacklist=getattr(request, "blacklist", []) or [],
                replace_rules=getattr(request, "replace_rules", {}) or {},
                max_tags=int(getattr(request, "max_tags", 0) or 0),
                append=getattr(request, "append", []) or [],
                quality_override=getattr(request, "quality_override", None),
                safety_override=getattr(request, "safety_override", None),
                rating_override=getattr(request, "rating_override", None),
                underscore_to_space_override=getattr(request, "underscore_to_space_override", None),
                preserve_underscore_prefixes_override=getattr(request, "preserve_underscore_prefixes_override", None),
            )
        except Exception as exc:
            results.append({"image_id": image_id, "error": str(exc), "rendered": ""})
            continue

        rendered = apply_caption_transforms(rendered, caption_transforms)

        # SEP-2: any blacklisted term still present in the FINAL rendered
        # text leaked back in through prose (NL caption / template append) —
        # exactly the failure that undoes trait absorption. Surfaced per
        # image so the preview UI can flag it before export.
        from services.tag_training_filters import scan_text_for_blacklisted_terms
        blacklist_leaks = scan_text_for_blacklisted_terms(
            rendered, getattr(request, "blacklist", []) or []
        )

        results.append({
            "image_id": image_id,
            "filename": image.get("filename") or "",
            "thumbnail_path": image.get("path") or "",
            "rendered": rendered,
            # Surface the raw natural-language caption (VLM / Smart Tag output)
            # alongside the template-rendered string. The Dataset Maker editor
            # renders a booru-tags template that omits {nl_caption}, so without
            # this the VLM caption was visible in the gallery (which reads
            # ai_caption directly) but invisible in the caption editor. The
            # frontend uses this to seed the editor after a Smart Tag run.
            "ai_caption": str(image.get("ai_caption") or ""),
            # Pure natural-language caption (point 1/2): lets the editor's NL
            # box show / edit the sentence separately from the booru-tags box.
            "nl_caption": str(image.get("nl_caption") or ""),
            "blacklist_leaks": blacklist_leaks,
            "error": None,
        })

    return {"results": results}
