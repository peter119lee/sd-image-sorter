"""Character trait-pruning candidates (P1-17, owner-approved 2026-07-07).

Community character-LoRA practice: prune the character's INNATE trait tags
(hair, eyes, skin, body markers) so the trigger word absorbs the identity and
the model doesn't tie it to individually-tagged features. The audit mandated
a reviewable checklist, never silent deletion — this service only COMPUTES
candidates; the user picks which ones feed the export blacklist.

Two signals:
- a stay-list heuristic keeps candidates to innate-trait families (clothing /
  pose / composition tags are never offered);
- a learned intersection keeps only tags present in >= ``min_ratio`` of the
  selected images, so one-off traits ("wet hair" in a single shot) drop out.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

import db_tags

TRAIT_CANDIDATE_LIMIT_DEFAULT = 60

# Tags that END with these but are transient states or actions, not traits.
_HAIR_EXCLUSIONS = {
    "adjusting_hair", "hand_in_hair", "hand_in_own_hair", "playing_with_own_hair",
    "wet_hair", "hair_ornament", "hair_flower", "hair_ribbon", "hair_bow",
    "hair_bobbles", "hairband", "hairclip", "hair_tie", "hair_scrunchie",
}
_EYE_EXCLUSIONS = {"closed_eyes", "half-closed_eyes", "rolling_eyes", "crossed_eyes"}
_SKIN_EXCLUSIONS = {"shiny_skin"}

_HAIRSTYLE_TAGS = {
    "twintails", "ponytail", "braid", "ahoge", "bangs", "blunt_bangs",
    "parted_bangs", "swept_bangs", "asymmetrical_bangs", "hair_between_eyes",
    "hair_over_one_eye", "hair_intakes", "sidelocks", "hime_cut", "bob_cut",
    "pixie_cut", "hair_bun", "double_bun", "single_hair_bun", "low_twintails",
    "short_twintails", "side_ponytail", "high_ponytail", "low_ponytail",
    "braided_ponytail", "twin_braids", "side_braid", "single_braid",
    "french_braid", "crown_braid", "half_updo", "hair_flaps",
}
_EYE_TAGS = {"heterochromia", "tsurime", "tareme", "long_eyelashes", "thick_eyebrows"}
_SKIN_TAGS = {"dark-skinned_female", "dark-skinned_male", "tan", "tanlines"}
_BODY_TAGS = {
    "flat_chest", "small_breasts", "medium_breasts", "large_breasts",
    "huge_breasts", "gigantic_breasts", "animal_ears", "animal_ear_fluff",
    "tail", "horns", "halo", "fang", "fangs", "skin_fang", "mole", "freckles",
    "scar", "muscular", "muscular_female", "muscular_male", "abs",
    "thick_thighs", "wide_hips", "petite", "elf", "dark_elf", "forehead",
}
_BODY_SUFFIXES = ("_ears", "_tail", "_horns", "_horn", "_wings")
_BODY_PREFIXES = ("mole_", "scar_")


def classify_trait_family(tag: str) -> Optional[str]:
    """Return the trait family for an innate-trait tag, or None for non-traits."""
    normalized = str(tag or "").strip().lower().replace(" ", "_")
    if not normalized:
        return None

    if normalized in _HAIR_EXCLUSIONS or normalized in _EYE_EXCLUSIONS or normalized in _SKIN_EXCLUSIONS:
        return None
    if normalized.endswith("_hair") or normalized in _HAIRSTYLE_TAGS:
        return "hair"
    if normalized.endswith("_eyes") or normalized.endswith("_pupils") or normalized in _EYE_TAGS:
        return "eyes"
    if normalized.endswith("_skin") or normalized in _SKIN_TAGS:
        return "skin"
    if (
        normalized in _BODY_TAGS
        or normalized.endswith(_BODY_SUFFIXES)
        or normalized.startswith(_BODY_PREFIXES)
        or normalized == "wings"
    ):
        return "body"
    return None


class TraitCandidatesRequest(BaseModel):
    """Request model for POST /api/tags/trait-candidates."""

    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=20000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    min_ratio: float = Field(default=0.6, ge=0.05, le=1.0)
    limit: int = Field(default=TRAIT_CANDIDATE_LIMIT_DEFAULT, ge=1, le=200)

    @model_validator(mode="after")
    def require_ids_or_selection_token(self):
        if self.image_ids is None and not self.selection_token:
            raise ValueError("Either image_ids or selection_token is required")
        if self.image_ids is not None and self.selection_token:
            raise ValueError("Provide either image_ids or selection_token, not both")
        return self


def _resolve_image_ids(request: TraitCandidatesRequest) -> List[int]:
    if request.image_ids is not None:
        seen = set()
        ids: List[int] = []
        for value in request.image_ids:
            image_id = int(value)
            if image_id not in seen:
                seen.add(image_id)
                ids.append(image_id)
        return ids
    from services.tag_export_service import iter_selection_token_id_chunks
    ids = []
    for chunk in iter_selection_token_id_chunks(request.selection_token or ""):
        ids.extend(int(v) for v in chunk)
    return ids


def compute_trait_candidates(request: TraitCandidatesRequest) -> Dict[str, Any]:
    """Frequency-ranked innate-trait tags across the selected images."""
    image_ids = _resolve_image_ids(request)
    total_images = len(image_ids)
    if not total_images:
        return {"total_images": 0, "candidates": []}

    counts: Dict[str, Dict[str, Any]] = {}
    tags_map = db_tags.get_image_tags_map(image_ids)
    for image_id in image_ids:
        seen_this_image = set()
        for row in tags_map.get(image_id, []) or []:
            category = str(row.get("category") or "").strip().lower()
            if category not in ("", "general"):
                continue
            tag = str(row.get("tag") or "").strip()
            key = tag.lower().replace(" ", "_")
            if not key or key in seen_this_image:
                continue
            family = classify_trait_family(key)
            if not family:
                continue
            seen_this_image.add(key)
            entry = counts.setdefault(key, {"tag": tag, "family": family, "count": 0})
            entry["count"] += 1

    min_count = max(1, int(request.min_ratio * total_images + 0.5))
    candidates = [
        {
            "tag": entry["tag"],
            "family": entry["family"],
            "count": entry["count"],
            "ratio": round(entry["count"] / total_images, 3),
        }
        for entry in counts.values()
        if entry["count"] >= min_count
    ]
    candidates.sort(key=lambda item: (-item["count"], item["tag"]))
    return {"total_images": total_images, "candidates": candidates[: request.limit]}
