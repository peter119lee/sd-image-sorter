"""The seven ``_apply_exclude_*`` filter builders (split from db_query.py).

``_apply_exclude_tags_filter`` through ``_apply_exclude_colors_filter``
moved here verbatim in the 2026-07 db_query split (the include-side filters
and the id-list core live in ``db_query_filters``). Like their include
counterparts they mutate the caller-supplied ``conditions``/``params`` lists
in place and return those same objects. Consumers keep importing through the
``db_query`` facade; do not import this module directly from feature code.

Imports only from db_core / db_helpers / utils / stdlib to avoid an import
cycle with the ``database`` facade.
"""
from typing import Optional, List, Any

from utils.model_names import checkpoint_identity_key
from db_core import (
    PROMPT_MATCH_MODE_EXACT,
    PROMPT_MATCH_MODE_CONTAINS,
)
from db_helpers import (
    normalize_prompt_token,
    normalize_prompt_match_mode,
    escape_like_pattern,
    normalize_lora_name,
    normalize_checkpoint_name,
)

_CHARACTER_PROMPT_TEXT_NORMALIZED_SQL = (
    "LOWER(REPLACE(COALESCE(json_extract(i.metadata_json, "
    "'$._parsed.character_prompt_text'), ''), '_', ' '))"
)


def _apply_exclude_tags_filter(conditions: List[str], params: List[Any],
                               exclude_tags: Optional[List[str]]) -> tuple:
    """Exclude images that have ANY of the specified tags."""
    if not exclude_tags:
        return conditions, params
    placeholders = ",".join("?" * len(exclude_tags))
    # NOT EXISTS (instead of NOT IN) so the engine can use an index on
    # LOWER(tag) (idx_tags_lower_tag) instead of full-scanning tags. tags.image_id
    # is NOT NULL, so NOT EXISTS and the old NOT IN exclude exactly the same rows.
    conditions.append(
        f"NOT EXISTS (SELECT 1 FROM tags _ex_tag WHERE _ex_tag.image_id = i.id "
        f"AND LOWER(_ex_tag.tag) IN ({placeholders}))"
    )
    params.extend([t.lower() for t in exclude_tags])
    return conditions, params


def _apply_exclude_generators_filter(conditions: List[str], params: List[Any],
                                     exclude_generators: Optional[List[str]]) -> tuple:
    """Exclude images matching any of the specified generators."""
    if not exclude_generators:
        return conditions, params
    placeholders = ",".join("?" * len(exclude_generators))
    conditions.append(f"LOWER(i.generator) NOT IN ({placeholders})")
    params.extend([g.lower() for g in exclude_generators])
    return conditions, params


def _apply_exclude_ratings_filter(conditions: List[str], params: List[Any],
                                  exclude_ratings: Optional[List[str]]) -> tuple:
    """Exclude images that have ANY of the specified rating tags."""
    if not exclude_ratings:
        return conditions, params
    placeholders = ",".join("?" * len(exclude_ratings))
    # BE-3: excludes by the denormalized images.ai_rating verdict (migration
    # 026). The IS NULL arm keeps unrated images visible — NOT IN alone would
    # drop them because NULL NOT IN (...) evaluates to NULL.
    conditions.append(
        f"(i.ai_rating IS NULL OR LOWER(i.ai_rating) NOT IN ({placeholders}))"
    )
    params.extend([r.lower() for r in exclude_ratings])
    return conditions, params


def _apply_exclude_checkpoints_filter(conditions: List[str], params: List[Any],
                                      exclude_checkpoints: Optional[List[str]]) -> tuple:
    """Exclude images matching any of the specified checkpoints."""
    if not exclude_checkpoints:
        return conditions, params
    normalized = []
    seen: set = set()
    for cp in exclude_checkpoints:
        n = normalize_checkpoint_name(cp)
        identity = checkpoint_identity_key(n)
        if not n or identity in seen:
            continue
        seen.add(identity)
        normalized.append(n)
    if not normalized:
        return conditions, params
    placeholders = ",".join("?" * len(normalized))
    conditions.append(f"i.checkpoint_normalized COLLATE NOCASE NOT IN ({placeholders})")
    params.extend(normalized)
    return conditions, params


def _apply_exclude_loras_filter(conditions: List[str], params: List[Any],
                                exclude_loras: Optional[List[str]]) -> tuple:
    """Exclude images that have ANY of the specified LoRAs."""
    if not exclude_loras:
        return conditions, params
    for lora in exclude_loras:
        lora_normalized = normalize_lora_name(lora)
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM image_loras il WHERE il.image_id = i.id AND LOWER(il.lora_name) = ?)"
        )
        params.append(lora_normalized)
    return conditions, params


# Whole-comma-segment view of the sidecar caption, for the exclude filter's
# exact mode. The include side can afford a broad LIKE because
# _matches_exact_post_filters re-checks tokens in Python; excludes have no
# post-pass, so the token boundary has to live in the SQL (see the v3.4.0 note
# below). Wrapping the text in commas and collapsing the spaces around each
# separator makes "%,term,%" match exactly one comma-delimited segment, which
# is the same split extract_prompt_tokens performs. There is no equivalent of
# image_prompt_tokens for captions — indexing them was rejected deliberately
# (see update_reparsed_sidecar_caption), so the boundary is expressed inline.
_SIDECAR_CAPTION_SEGMENTS_SQL = (
    "',' || REPLACE(REPLACE(TRIM("
    "LOWER(REPLACE(COALESCE(i.sidecar_caption, ''), '_', ' '))"
    "), ' ,', ','), ', ', ',') || ','"
)


def _apply_exclude_prompts_filter(conditions: List[str], params: List[Any],
                                  exclude_prompts: Optional[List[str]],
                                  prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT) -> tuple:
    """Exclude images whose text matches ANY of the specified terms.

    v3.3.0 FEAT-EXCLUDE-EXTRA: the negation of _apply_prompt_terms_filter, and
    it has to stay the exact negation. That filter reads ``prompt`` and
    ``sidecar_caption`` (migration 042); if this one read only ``prompt`` the
    pair would contradict itself — the same row could satisfy "contains X" and
    "excludes X" at once — and the contradiction resolves in the dangerous
    direction, letting a batch move carry off a file the user explicitly ruled
    out.

    'contains' mode does a normalized substring NOT LIKE over both columns;
    'exact' mode excludes any image with a matching whole token in either.
    """
    if not exclude_prompts:
        return conditions, params
    match_mode = normalize_prompt_match_mode(prompt_match_mode)
    for term in exclude_prompts:
        normalized_term = normalize_prompt_token(term)
        if not normalized_term:
            continue
        if match_mode == PROMPT_MATCH_MODE_CONTAINS:
            conditions.append(
                "(LOWER(REPLACE(COALESCE(i.prompt, ''), '_', ' ')) NOT LIKE ? ESCAPE '\\'"
                " AND LOWER(REPLACE(COALESCE(i.sidecar_caption, ''), '_', ' ')) "
                "NOT LIKE ? ESCAPE '\\'"
                f" AND {_CHARACTER_PROMPT_TEXT_NORMALIZED_SQL} NOT LIKE ? ESCAPE '\\')"
            )
            like_pattern = f"%{escape_like_pattern(normalized_term)}%"
            params.extend([like_pattern, like_pattern, like_pattern])
        else:
            # v3.4.0 FIX: exact mode must compare whole normalized tokens.
            # The include filter uses a broad LIKE pre-filter because it is
            # corrected by an exact post-filter; excludes have no post-pass,
            # so a LIKE here permanently over-excluded (excluding "cat" also
            # hid "catgirl"/"scattered"). image_prompt_tokens stores tokens
            # already normalized via normalize_prompt_token, matching
            # normalized_term above; the caption arm reproduces that boundary
            # with the comma-wrapped segment view.
            conditions.append(
                "(NOT EXISTS (SELECT 1 FROM image_prompt_tokens ipt "
                "WHERE ipt.image_id = i.id AND ipt.token = ?)"
                f" AND {_SIDECAR_CAPTION_SEGMENTS_SQL} NOT LIKE ? ESCAPE '\\')"
            )
            # The term is collapsed the same way the column expression is, so a
            # multi-word term ("looking at viewer") still lines up with one
            # segment of the wrapped text.
            segment_term = normalized_term.replace(" ,", ",").replace(", ", ",")
            params.append(normalized_term)
            params.append(f"%,{escape_like_pattern(segment_term)},%")
    return conditions, params


def _apply_exclude_colors_filter(conditions: List[str], params: List[Any],
                                 exclude_colors: Optional[List[str]]) -> tuple:
    """Exclude images whose color_temperature is ANY of the specified values.

    v3.3.0 FEAT-EXCLUDE-EXTRA: the negation of the color_temperature side of
    _apply_color_filter. Values are warm/cool/neutral (others are ignored).
    Images with NULL color_temperature are NOT excluded (they simply lack the
    attribute, mirroring how the include filter only matches non-null rows).
    """
    if not exclude_colors:
        return conditions, params
    valid = {"warm", "cool", "neutral"}
    normalized = [c.lower() for c in exclude_colors if c and c.lower() in valid]
    if not normalized:
        return conditions, params
    placeholders = ",".join("?" * len(normalized))
    conditions.append(
        f"(i.color_temperature IS NULL OR i.color_temperature NOT IN ({placeholders}))"
    )
    params.extend(normalized)
    return conditions, params
