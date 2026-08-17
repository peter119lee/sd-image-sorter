"""VLM prompt presets + training-purpose tag filtering for Smart Tag.

Owns PROMPT_PRESETS, build_vlm_prompt (incl. the SEP-2 trait-suppression
block), filter_tags_by_training_purpose, and _vlm_context_tags_for. Also
re-exports the training-purpose vocabulary from services.tag_training_filters
for the callers that import it from the Smart Tag namespace.

Split verbatim out of services/smart_tag_service.py.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.caption_dialect import tag_slot_candidates
from services.smart_tag.consensus import filter_noise_tags
from tag_rules import categorize_tag


# Shared logger: keep the historical channel name so log capture, filtering and
# support-log diagnostics behave exactly as before the decomposition.
logger = logging.getLogger("services.smart_tag_service")


# ---------------------------------------------------------------------------
# VLM prompt presets (training-purpose specific)
#
# Each prompt instructs the VLM what to describe and what to omit so the
# resulting natural-language sentence pairs cleanly with WD14 tags inside a
# LoRA training caption. The wording below is original to this project; the
# instructional content reflects standard LoRA-training advice (style ->
# rendering only, character -> describe what varies, etc.) which is public
# domain industry practice.
# ---------------------------------------------------------------------------

PROMPT_PRESETS: Dict[str, str] = {
    "style": (
        "Task: produce the natural-language portion of a LoRA training "
        "caption that targets STYLE. The target style should be carried by the "
        "training image and trigger, not repeated as caption vocabulary.\n\n"
        "Output 2-3 plain English sentences that cover:\n"
        "  - visible subject, pose or action, clothing, objects, and setting\n"
        "  - composition and framing (close portrait, full body, low angle, dynamic crop)\n"
        "  - scene lighting only when it is situational rather than a rendering style\n\n"
        "Do not name or paraphrase the rendering medium, artist, linework, brushwork, "
        "palette, or target style. No leading trigger word, headers, or praise.\n\n"
        "WD14 tags for grounding (do not parrot them back literally): {tags}"
    ),
    "character": (
        "Task: produce the natural-language portion of a LoRA training "
        "caption that targets a CHARACTER. The character's fixed identity is "
        "learned from the trained weights, so duplicating it in captions hurts "
        "training. Write only about what changes across images.\n\n"
        "Output 2-3 plain English sentences focused on:\n"
        "  - pose, action, and facial expression of the moment\n"
        "  - position and orientation within the frame\n"
        "  - background, setting, time of day\n"
        "  - shot framing (close-up, full body, over the shoulder, from behind)\n"
        "  - lighting and overall mood\n\n"
        "Do not describe: hair color, eye color, hair style or length, the "
        "character's signature outfit, or any other fixed identity feature. "
        "No leading trigger word, no headers, no labels.\n\n"
        "WD14 tags for grounding: {tags}"
    ),
    "general": (
        "Task: write 2-3 plain English sentences describing this image for use as "
        "the natural-language portion of a LoRA training caption. Cover the visible "
        "subject, the pose or action, clothing, background, lighting, and overall "
        "composition. No headers, no labels, no trigger word.\n\n"
        "WD14 tags for grounding: {tags}"
    ),
    "concept": (
        "Task: write 2-3 plain English sentences for a CONCEPT LoRA caption. "
        "Describe the visible subject, action, setting, composition, and lighting "
        "that vary around the target concept. Do not guess a hidden class name or "
        "invent a concept label. No headers, no labels, no trigger word.\n\n"
        "WD14 tags for grounding: {tags}"
    ),
}

# Allowed values for the API. style / character / general / concept cover the
# common LoRA training intents. The vocabulary moved to
# services.tag_training_filters (2026-07-07, P2-19) so the export engine
# shares it — re-exported here because callers across the codebase import
# these names from this module.
from services.tag_training_filters import (  # noqa: E402
    format_trait_suppression_block,  # noqa: F401 — SEP-2 prompt suppression
    TRAINING_PURPOSE_ALIASES,  # noqa: F401 — re-exported for existing importers
    normalize_training_purpose,  # noqa: F401 — re-exported for existing importers
)


def build_vlm_prompt(
    training_purpose: str,
    wd14_tags: List[str],
    *,
    include_tags: bool = True,
    suppressed_traits: Optional[List[str]] = None,
) -> str:
    """Render the per-image VLM prompt for the given training purpose.

    The WD14 tag list is filtered for noise BEFORE being substituted into
    the template so the VLM never sees ``masterpiece, score_9, anime`` and
    parrots them back into the natural-language sentences.

    SEP-2: ``suppressed_traits`` (the dataset's pruned intrinsic features)
    appends a "never mention these" block — the generic preset wording can
    say "no hair color", but only the caller knows THIS character's tail /
    heterochromia / signature accessory.
    """
    canonical = normalize_training_purpose(training_purpose)
    template = PROMPT_PRESETS.get(canonical) or PROMPT_PRESETS["general"]
    if include_tags:
        cleaned, _stripped = filter_noise_tags(wd14_tags)
    else:
        cleaned = []
    prompt = template.replace("{tags}", ", ".join(cleaned))
    suppression = format_trait_suppression_block(suppressed_traits)
    if suppression:
        prompt = f"{prompt}\n\n{suppression}"
    return prompt


def filter_tags_by_training_purpose(
    training_purpose: str,
    general_tags: List[str],
    copyright_tags: List[str],
    character_tags: List[str],
    trigger_word: str = "",
) -> List[str]:
    """Return the caption tags that can be filtered without guessing targets.

    Style mode removes tags classified as style/artist so the target style is not
    named in every caption. Character mode removes detected character names only
    when a trigger word is present to carry that identity. Other modes preserve
    context because the app cannot infer which detected tag is the user's target.
    """
    canonical = normalize_training_purpose(training_purpose)
    all_tags = list(general_tags) + list(copyright_tags) + list(character_tags)

    if canonical == "style":
        filtered_general = [
            tag for tag in general_tags
            if categorize_tag(tag) not in {"style", "artist"}
        ]
        return filtered_general + list(copyright_tags) + list(character_tags)
    if canonical == "character" and str(trigger_word or "").strip():
        return list(general_tags) + list(copyright_tags)
    return all_tags


def _vlm_context_tags_for(
    partial: Dict[str, Any],
    include_tags_as_context: bool,
    training_purpose: str = "general",
    trigger_word: str = "",
) -> Optional[List[str]]:
    """Build the noise-filtered tag list passed to ``provider.caption_image``.

    Replicates ``build_vlm_prompt``'s always-on noise filter so the concurrent
    pipeline produces the same VLM context the serial path did, then lets the
    provider's own ``build_user_message`` substitute it into the (job-constant)
    purpose template — which is why no per-image config mutation is needed and
    the concurrent calls can't race on shared prompt state.
    """
    if not include_tags_as_context:
        return None
    names = filter_tags_by_training_purpose(
        training_purpose,
        partial.get("general_names") or [],
        partial.get("copyright_names") or [],
        partial.get("character_names") or [],
        trigger_word,
    )
    filtered, _stripped = filter_noise_tags(names)
    grounded = tag_slot_candidates(filtered)
    if len(grounded) != len(filtered):
        # Every ``user_prompt_with_tags`` variant states that the interpolated
        # text is danbooru-style tags, and the Krea 2 profile says so twice, so a
        # prose clause in that slot is a false statement about the input rather
        # than a weak hint. This drops only positively prose-shaped candidates
        # from an in-memory grounding list; no stored caption is touched.
        logger.warning(
            "smart-tag: withheld %d prose grounding candidate(s) from the "
            "prompt's tags slot",
            len(filtered) - len(grounded),
        )
    return grounded or None
