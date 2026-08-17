"""VLM result persistence -- the vocab-gated caption/tag store writers.

Decomposed from routers/vlm.py (2026-07): a verbatim slice of the pre-split
lines 546-633. Import
routers.vlm (the facade), NOT this module -- the facade re-imports
_persist_vlm_result/_persist_tags BY REFERENCE, so their callers
(caption_single, _run_batch) resolve them as facade globals and
monkeypatches on the facade keep biting (tests/test_vlm_router_pins.py
census; tests/test_vlm_tag_gate.py drives the real bodies through
routers.vlm). The vocabulary gate stays a LAZY in-function import from
services.vlm_tag_gate (patched THERE by the reader net).
"""
from __future__ import annotations

import logging
from typing import List

from routers.vlm_models import (
    VLMResultPersistenceError,
    _PersistedVLMTagRow,
    _VLMPersistenceStore,
)

# The moved bodies keep logging under the pre-split logger name so existing
# log filtering/handler config is unchanged (tags_bulk_journal_ops precedent).
logger = logging.getLogger("routers.vlm")


def _persist_vlm_result(
    db: _VLMPersistenceStore,
    image_id: int,
    caption: str,
    vlm_tags: List[str],
) -> int:
    """Atomically persist a VLM caption and gated tags for one image.

    VLM tags pass through the vocabulary gate (services.vlm_tag_gate) first:
    hallucinated non-vocabulary tags and rating words are dropped so they never
    become permanent library tags. Surviving tags (normalized) are appended to
    the existing tags; existing tags keep their confidence, new VLM tags use
    confidence=0.85 (a manual-tier marker). Returns the number of tags the gate
    dropped so callers can surface "N invalid tags dropped".
    """
    from services.vlm_tag_gate import filter_vlm_tags

    try:
        accepted, dropped = filter_vlm_tags(vlm_tags) if vlm_tags else ([], 0)
        if dropped:
            logger.info(
                "VLM tag gate dropped invalid tags",
                extra={"dropped_count": dropped, "image_id": image_id},
            )
        if not caption and not accepted:
            return dropped
        existing = db.get_image_tags(image_id) or []
        existing_lower = {
            tag_row["tag"].lower()
            for tag_row in existing
            if tag_row["tag"]
        }
        # ``accepted`` is already lowercase_with_underscores, so a direct
        # membership test against the lowercased existing set is correct.
        new_tags = [tag for tag in accepted if tag not in existing_lower]
        if not caption and not new_tags:
            return dropped
        # Full-list merge (replace_scope stays "all"): existing rows keep
        # their provenance columns; VLM additions are marked source='vlm' so
        # a later pipeline re-tag may replace them but never the user's rows.
        merged: List[_PersistedVLMTagRow] = [
            {
                "tag": tag_row["tag"],
                "confidence": (
                    float(tag_row["confidence"])
                    if tag_row["confidence"] is not None
                    else 1.0
                ),
                "source": tag_row["source"],
                "category": tag_row["category"],
            }
            for tag_row in existing
            if tag_row["tag"]
        ] + [
            {
                "tag": tag,
                "confidence": 0.85,
                "source": "vlm",
                "category": None,
            }
            for tag in new_tags
        ]
        db.add_tags_batch(
            [{
                "image_id": image_id,
                "tags": merged,
                "ai_caption": caption or None,
                "nl_caption": caption or None,
            }],
            default_source=None,
            replace_scope="all",
        )
    except Exception as exc:
        raise VLMResultPersistenceError(
            f"VLM result persistence failed for image_id={image_id}: {exc}"
        ) from exc
    return dropped


def _persist_tags(
    db: _VLMPersistenceStore,
    image_id: int,
    vlm_tags: List[str],
) -> int:
    """Persist gated VLM tags without suppressing database failures."""
    if not vlm_tags:
        return 0
    return _persist_vlm_result(db, image_id, "", vlm_tags)
