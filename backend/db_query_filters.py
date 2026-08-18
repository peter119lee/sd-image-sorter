"""Include-side ``_apply_*`` filter builders for image queries (split from db_query.py).

The tag/generator/rating/checkpoint/LoRA list filters, search / folder /
prompt-terms matching, the dimension / aesthetic / date / saturation /
no-caption / seed / user-rating / metadata-presence / color / color-hues
scalar filters, the artist join, the id-list core
(``_normalize_filter_id_list`` / ``_apply_id_list_filter`` and both id-list
wrappers), the collection subquery, and the readable guard moved here
verbatim in the 2026-07 db_query split (the seven ``_apply_exclude_*``
filters live in ``db_query_filters_exclude``). The helpers mutate the
caller-supplied ``conditions``/``params`` lists in place and return those
same objects; ``_apply_color_hues_filter`` keeps its function-scoped lazy
``color_analyzer`` import (hoisting it would create a
``db_query -> color_analyzer`` import-time edge). Consumers keep importing
through the ``db_query`` facade; do not import this module directly from
feature code.

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
    extract_prompt_tokens,
    _favorite_image_ids_query,
    _folder_scope_query_match_clause,
)


def _apply_tag_filter(query: str, tags: Optional[List[str]], params: List[Any],
                      tag_mode: str = "and") -> tuple:
    """Apply tag filtering with JOINs (AND logic) or subquery (OR logic).

    Args:
        query: Current query string
        tags: List of tags to filter by
        params: Current parameter list
        tag_mode: 'and' (image must have ALL tags) or 'or' (image must have ANY tag)

    Returns:
        Tuple of (modified query, modified params)
    """
    if not tags:
        return query, params

    if tag_mode == "or":
        placeholders = ",".join("?" * len(tags))
        query += f" INNER JOIN tags _tor ON i.id = _tor.image_id AND _tor.tag IN ({placeholders})"
        params.extend(tags)
    else:
        for i, tag in enumerate(tags):
            alias = f"t{i}"
            query += f" INNER JOIN tags {alias} ON i.id = {alias}.image_id AND {alias}.tag = ?"
            params.append(tag)

    return query, params


def _apply_generator_filter(conditions: List[str], params: List[Any],
                            generators: Optional[List[str]]) -> tuple:
    """Apply generator filtering (OR logic).

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        generators: List of generators to filter by

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not generators:
        return conditions, params

    placeholders = ",".join("?" * len(generators))
    conditions.append(f"i.generator IN ({placeholders})")
    params.extend(generators)

    return conditions, params


def _apply_rating_filter(conditions: List[str], params: List[Any],
                         ratings: Optional[List[str]]) -> tuple:
    """Apply rating filtering (OR logic with untagged fallback).

    When all 4 ratings are selected, don't filter at all (show everything).
    When some ratings are selected, show images with those rating tags OR untagged images.

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        ratings: List of ratings to filter by

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not ratings:
        return conditions, params

    all_ratings = {'general', 'sensitive', 'questionable', 'explicit'}
    selected_ratings = set(ratings)

    # Only apply filter if not all ratings are selected
    if selected_ratings == all_ratings:
        return conditions, params

    rating_placeholders = ",".join("?" * len(ratings))
    # Image's rating verdict is one of the selected ratings OR the image was
    # never tagged (untagged fallback, unchanged). BE-3: ai_rating is the
    # denormalized winner column (migration 026) — no tags-table probe.
    conditions.append(f"""(
        i.ai_rating IN ({rating_placeholders})
        OR i.tagged_at IS NULL
    )""")
    params.extend(ratings)

    return conditions, params


def _apply_checkpoint_filter(conditions: List[str], params: List[Any],
                             checkpoints: Optional[List[str]]) -> tuple:
    """Apply checkpoint filtering (OR logic).

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        checkpoints: List of checkpoints to filter by

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not checkpoints:
        return conditions, params

    normalized_checkpoints: List[str] = []
    seen: set[str] = set()
    for checkpoint in checkpoints:
        normalized = normalize_checkpoint_name(checkpoint)
        identity = checkpoint_identity_key(normalized)
        if not normalized or identity in seen:
            continue
        seen.add(identity)
        normalized_checkpoints.append(normalized)

    if not normalized_checkpoints:
        return conditions, params

    placeholders = ",".join("?" * len(normalized_checkpoints))
    conditions.append(f"i.checkpoint_normalized COLLATE NOCASE IN ({placeholders})")
    params.extend(normalized_checkpoints)

    return conditions, params


def _apply_lora_filter(conditions: List[str], params: List[Any],
                       loras: Optional[List[str]]) -> tuple:
    """Apply LoRA filtering (OR logic - image has ANY of the selected loras).

    Uses the image_loras junction table for efficient indexed lookups
    instead of LIKE scans on TEXT columns.

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        loras: List of loras to filter by

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not loras:
        return conditions, params

    lora_conditions = []
    for lora in loras:
        lora_normalized = normalize_lora_name(lora)
        lora_conditions.append(
            "EXISTS (SELECT 1 FROM image_loras il WHERE il.image_id = i.id AND LOWER(il.lora_name) = ?)"
        )
        params.append(lora_normalized)

    conditions.append(f"({' OR '.join(lora_conditions)})")

    return conditions, params


# The two columns that hold "the words describing this image", normalized the
# same way (lowercase, underscore folded to space) so a Danbooru-style
# ``silver_hair`` and a typed ``silver hair`` are the same term.
#
# Migration 042 split them by PROVENANCE — ``prompt`` is what an SD generator
# was given, ``sidecar_caption`` is what somebody else wrote in a ``.txt``
# beside the file — not by meaning. Every text filter therefore has to look at
# both or it silently answers a narrower question than the user asked. They are
# named constants because ``21fd5e8`` extended one caller (the search box) and
# missed the other (prompt terms, which is what Auto-Separate moves on); a
# shared expression makes the next such divergence a compile-time-visible edit
# rather than a silent behavioral one.
_PROMPT_NORMALIZED_SQL = "LOWER(REPLACE(COALESCE(i.prompt, ''), '_', ' '))"
_SIDECAR_CAPTION_NORMALIZED_SQL = (
    "LOWER(REPLACE(COALESCE(i.sidecar_caption, ''), '_', ' '))"
)


def _apply_search_filter(conditions: List[str], params: List[Any],
                         search_query: Optional[str]) -> tuple:
    """Apply free-text search over filename, checkpoint, prompt and caption.

    Normalizes: lowercase and replace underscore with space.

    Sidecar captions (migration 042) live in their own column precisely so
    they are NOT counted as generation prompts, but the user still needs to
    find those images by the words in them — so the search box matches both
    ``prompt`` (through the token index) and ``sidecar_caption`` (a
    normalized substring match, the same shape ``_apply_prompt_terms_filter``
    uses in "contains" mode). Underscore folding matters here: sidecar
    captions are usually Danbooru tags like ``silver_hair``.

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        search_query: Search term to look for

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not search_query:
        return conditions, params

    raw_search = str(search_query or "").strip()
    normalized_search = normalize_prompt_token(raw_search)
    checkpoint_search = checkpoint_identity_key(raw_search) or raw_search.lower()
    prompt_tokens = sorted(extract_prompt_tokens(raw_search) or [])
    if not prompt_tokens and normalized_search:
        prompt_tokens = [normalized_search]

    token_conditions: List[str] = []
    token_params: List[Any] = []
    for token in prompt_tokens[:8]:
        token_like = f"%{escape_like_pattern(token)}%"
        token_conditions.append(
            "EXISTS (SELECT 1 FROM image_prompt_tokens ipt "
            "WHERE ipt.image_id = i.id AND ipt.token LIKE ? ESCAPE '\\')"
        )
        token_params.append(token_like)

    prompt_clause = " OR ".join(token_conditions)
    if prompt_clause:
        prompt_clause = f" OR ({prompt_clause})"

    caption_clause = ""
    caption_params: List[Any] = []
    if normalized_search:
        caption_clause = f" OR {_SIDECAR_CAPTION_NORMALIZED_SQL} LIKE ? ESCAPE '\\'"
        caption_params.append(f"%{escape_like_pattern(normalized_search)}%")

    conditions.append(
        "("
        "LOWER(i.filename) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(i.checkpoint_normalized, '')) LIKE ? ESCAPE '\\'"
        f"{prompt_clause}"
        f"{caption_clause}"
        ")"
    )
    params.extend(
        [
            f"%{escape_like_pattern(raw_search.lower())}%",
            f"%{escape_like_pattern(checkpoint_search)}%",
        ]
    )
    params.extend(token_params)
    params.extend(caption_params)

    return conditions, params


def _apply_folder_filter(conditions: List[str], params: List[Any],
                         folder: Optional[str]) -> tuple:
    """Scope results to images whose indexed path is within a folder subtree.

    v3.3.2 Library Navigation. Reuses the casefold/path-normalization-aware
    folder-scope clause used by the reconnect-missing-files reads, qualified to
    the ``i`` alias used by the gallery list/count queries. Matching is
    recursive (the folder itself and everything beneath it).

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        folder: Folder path to scope to (None/blank is a no-op)

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not folder or not str(folder).strip():
        return conditions, params

    clause, clause_params = _folder_scope_query_match_clause(str(folder), column="i.path")
    if clause:
        conditions.append(f"({clause})")
        params.extend(clause_params)

    return conditions, params


def _apply_prompt_terms_filter(conditions: List[str], params: List[Any],
                               prompt_terms: Optional[List[str]],
                               prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT) -> tuple:
    """Apply multi-prompt filter (AND logic - image text must contain ALL terms).

    Each term must appear in the image's ``prompt`` OR in its
    ``sidecar_caption`` (migration 042). The two columns are split by who wrote
    the text, not by what it means, and this filter is a question about
    meaning: a rule written as "prompt contains silver_hair" is the user asking
    for images of silver hair, and the same image answers that question whether
    its tags arrived embedded in the PNG or in a ``.txt`` next to it.

    Matching both is also the only reading that keeps existing rules stable.
    The scan upsert writes ``prompt = ?`` unconditionally, so a rescan
    relocates sidecar-derived text out of ``prompt``; a prompt-only filter
    would then move strictly fewer files than the same saved Auto-Separate rule
    moved yesterday, with nothing telling the user it had narrowed. This is the
    selection criterion for batch move, so that is a wrong file set, not just a
    shorter list.

    Mode semantics are unchanged and apply identically to both columns:

    * ``contains`` — normalized substring, resolved entirely in SQL.
    * ``exact`` — whole comma-delimited token. The SQL below is only a
      deliberately BROAD pre-filter for this mode (a token-index ``LIKE`` for
      the prompt, a substring ``LIKE`` for the caption);
      ``_matches_exact_post_filters`` then re-checks both columns with
      ``extract_prompt_tokens`` and is what actually decides. A pre-filter may
      over-match, never under-match, or rows vanish before the post-filter can
      rescue them.

    ``sidecar_caption_format`` is deliberately NOT consulted. Exact mode
    already self-selects tag-shaped text (a prose caption has no comma-split
    tokens to match) and contains mode is a substring search where finding
    words inside prose is the point — so a format gate would add no precision,
    while making results depend on a column that is NULL for every row written
    before migration 044.

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        prompt_terms: List of prompt terms to filter by
        prompt_match_mode: ``exact`` (whole token) or ``contains`` (substring)

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if not prompt_terms:
        return conditions, params

    match_mode = normalize_prompt_match_mode(prompt_match_mode)
    for term in prompt_terms:
        normalized_term = normalize_prompt_token(term)
        if not normalized_term:
            continue
        like_pattern = f"%{escape_like_pattern(normalized_term)}%"
        if match_mode == PROMPT_MATCH_MODE_CONTAINS:
            conditions.append(
                f"({_PROMPT_NORMALIZED_SQL} LIKE ? ESCAPE '\\'"
                f" OR {_SIDECAR_CAPTION_NORMALIZED_SQL} LIKE ? ESCAPE '\\')"
            )
        else:
            conditions.append(
                "(EXISTS (SELECT 1 FROM image_prompt_tokens ipt "
                "WHERE ipt.image_id = i.id AND ipt.token LIKE ? ESCAPE '\\')"
                f" OR {_SIDECAR_CAPTION_NORMALIZED_SQL} LIKE ? ESCAPE '\\')"
            )
        params.append(like_pattern)
        params.append(like_pattern)

    return conditions, params


def _apply_dimension_filters(conditions: List[str], params: List[Any],
                             min_width: Optional[int], max_width: Optional[int],
                             min_height: Optional[int], max_height: Optional[int],
                             aspect_ratio: Optional[str]) -> tuple:
    """Apply dimension and aspect ratio filters.

    Args:
        conditions: Current WHERE conditions list
        params: Current parameter list
        min_width, max_width: Width range constraints
        min_height, max_height: Height range constraints
        aspect_ratio: One of 'square', 'landscape', 'portrait'

    Returns:
        Tuple of (modified conditions, modified params)
    """
    if min_width:
        conditions.append("i.width >= ?")
        params.append(min_width)
    if max_width:
        conditions.append("i.width <= ?")
        params.append(max_width)
    if min_height:
        conditions.append("i.height >= ?")
        params.append(min_height)
    if max_height:
        conditions.append("i.height <= ?")
        params.append(max_height)

    # Aspect ratio filter
    if aspect_ratio:
        if aspect_ratio == 'square':
            conditions.append("i.height > 0 AND ABS(CAST(i.width AS FLOAT) / i.height - 1.0) < 0.1")
        elif aspect_ratio == 'landscape':
            conditions.append("i.height > 0 AND CAST(i.width AS FLOAT) / i.height > 1.1")
        elif aspect_ratio == 'portrait':
            conditions.append("i.height > 0 AND CAST(i.width AS FLOAT) / i.height < 0.9")

    return conditions, params


def _apply_aesthetic_filter(conditions: List[str], params: List[Any],
                            min_aesthetic: Optional[float],
                            max_aesthetic: Optional[float],
                            aesthetic_unscored: Optional[bool] = None) -> tuple:
    """Apply aesthetic score range filters.

    ``aesthetic_unscored`` takes precedence: when truthy it matches only rows
    that have not been aesthetic-scored yet (``aesthetic_score IS NULL``) and
    the min/max range is ignored entirely, so "unscored" and a numeric range
    can never contradict each other in the same query.
    """
    if aesthetic_unscored:
        conditions.append("i.aesthetic_score IS NULL")
        return conditions, params
    if min_aesthetic is not None:
        conditions.append("i.aesthetic_score IS NOT NULL AND i.aesthetic_score >= ?")
        params.append(min_aesthetic)
    if max_aesthetic is not None:
        conditions.append("i.aesthetic_score IS NOT NULL AND i.aesthetic_score <= ?")
        params.append(max_aesthetic)
    return conditions, params


def _apply_date_filter(conditions: List[str], params: List[Any],
                       date_from: Optional[str],
                       date_to: Optional[str]) -> tuple:
    """File-time day-range filter (YYYY-MM-DD, both bounds inclusive).

    Filters on COALESCE(library_order_time, created_at) — the same
    expression the newest/oldest sort key uses, i.e. the file's first-seen
    mtime (stable across rescans). Values are 'YYYY-MM-DD HH:MM:SS' strings,
    so lexicographic comparison against the day prefix is exact; the upper
    bound is half-open on the NEXT day via SQLite date(?, '+1 day') so the
    whole end day is included.
    """
    if date_from:
        conditions.append("COALESCE(i.library_order_time, i.created_at) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append(
            "COALESCE(i.library_order_time, i.created_at) < date(?, '+1 day')"
        )
        params.append(date_to)
    return conditions, params


def _apply_saturation_filter(conditions: List[str], params: List[Any],
                             min_saturation: Optional[float],
                             max_saturation: Optional[float]) -> tuple:
    """Apply a color-saturation range filter (Aurora Phase 3 gallery filter).

    Ranges over ``i.color_saturation`` — the same column the ``saturation`` /
    ``saturation_asc`` gallery sorts use. Only rows that have been color-analyzed
    (non-null saturation) can match, mirroring the brightness range in
    :func:`_apply_color_filter`, so unanalyzed rows are excluded rather than
    treated as 0.
    """
    if min_saturation is not None:
        conditions.append("i.color_saturation IS NOT NULL AND i.color_saturation >= ?")
        params.append(float(min_saturation))
    if max_saturation is not None:
        conditions.append("i.color_saturation IS NOT NULL AND i.color_saturation <= ?")
        params.append(float(max_saturation))
    return conditions, params


def _apply_no_caption_filter(conditions: List[str], params: List[Any],
                             no_caption: Optional[bool]) -> tuple:
    """Restrict to images that carry no caption of either kind (Aurora Phase 3).

    "No caption" means both the WD14/smart-tag ``ai_caption`` and the VLM
    natural-language ``nl_caption`` are empty/NULL. ``None`` (or falsy) is a
    no-op. No parameters are bound — the predicate is a pure column expression —
    so this composes with every other filter and both pagination strategies.
    """
    if not no_caption:
        return conditions, params
    conditions.append("(COALESCE(i.ai_caption, '') = '' AND COALESCE(i.nl_caption, '') = '')")
    return conditions, params


def _apply_seed_filter(conditions: List[str], params: List[Any],
                       seed: Optional[int]) -> tuple:
    """Match images generated with a specific seed (Aurora Phase 3).

    The seed is not a column; it lives inside ``metadata_json`` at the parsed
    path ``$._parsed.generation_params.seed`` for every generator (WebUI / NAI /
    Forge / ComfyUI all funnel through ``_parsed.generation_params`` — see
    metadata_parser.py). ComfyUI KSampler graphs additionally record
    ``noise_seed``, so both verified paths are OR'd. ``metadata_json`` is
    persisted as compact valid JSON, but legacy rows can hold non-JSON text, so
    the extraction is guarded by ``json_valid`` (SQLite's ``json_extract`` raises
    on malformed input); guarded rows simply do not match instead of crashing
    the query. The extracted value is CAST to INTEGER so a stringified seed
    still compares equal to the integer bind.
    """
    if seed is None:
        return conditions, params
    try:
        seed_value = int(seed)
    except (TypeError, ValueError):
        return conditions, params
    conditions.append(
        "(json_valid(i.metadata_json) AND ("
        "CAST(json_extract(i.metadata_json, '$._parsed.generation_params.seed') AS INTEGER) = ? "
        "OR CAST(json_extract(i.metadata_json, '$._parsed.generation_params.noise_seed') AS INTEGER) = ?"
        "))"
    )
    params.append(seed_value)
    params.append(seed_value)
    return conditions, params


def _apply_user_rating_filter(conditions: List[str], params: List[Any],
                              min_user_rating: Optional[int]) -> tuple:
    """Apply the gallery "★≥N" user-rating filter (v3.3.2, FF-2).

    ``user_rating`` is NOT NULL DEFAULT 0, so no NULL guard is needed. A
    ``min_user_rating`` of None or 0 is a no-op that keeps unrated images in;
    only a value >= 1 narrows results to images rated at least that many stars.
    """
    if min_user_rating is not None and int(min_user_rating) > 0:
        conditions.append("i.user_rating >= ?")
        params.append(int(min_user_rating))
    return conditions, params


# An image "has SD metadata" when it was recognized as a generation with
# readable parameters: either a known generator (not unknown/blank) OR a
# non-empty positive prompt. metadata_status is NOT used — it tracks the parse
# *pipeline* state (complete/pending/error), which is uniformly "complete" for
# already-scanned libraries and so makes a useless gallery filter.
#
# The generator half is the negation of db_helpers.NO_GENERATOR_RECORDED_SQL and
# has to stay spelled the same, TRIM included, or a whitespace-only generator
# reads as a named tool here and as no generator there.
_HAS_METADATA_CLAUSE = (
    "((LOWER(TRIM(COALESCE(i.generator, ''))) NOT IN ('', 'unknown')) "
    "OR (COALESCE(TRIM(i.prompt), '') <> ''))"
)


def _apply_metadata_presence_filter(conditions: List[str], params: List[Any],
                                    has_metadata: Optional[bool]) -> tuple:
    """Apply the gallery "has SD generation parameters" filter (v3.3.2 small-opt).

    ``has_metadata`` of None is a no-op (show everything). True narrows to images
    that carry SD metadata; False narrows to images that carry none (e.g. plain
    PNGs scanned into the library). No parameters are bound — the predicate is a
    pure column expression — so this composes with every other filter and both
    pagination strategies.
    """
    if has_metadata is None:
        return conditions, params
    if has_metadata:
        conditions.append(_HAS_METADATA_CLAUSE)
    else:
        conditions.append(f"NOT {_HAS_METADATA_CLAUSE}")
    return conditions, params


def _apply_color_filter(conditions: List[str], params: List[Any],
                        brightness_min: Optional[float] = None,
                        brightness_max: Optional[float] = None,
                        color_temperature: Optional[str] = None,
                        brightness_distribution: Optional[str] = None) -> tuple:
    """Apply v3.2.1 color-based filters (brightness range, temperature, distribution shape)."""
    if brightness_min is not None:
        conditions.append("i.avg_brightness IS NOT NULL AND i.avg_brightness >= ?")
        params.append(float(brightness_min))
    if brightness_max is not None:
        conditions.append("i.avg_brightness IS NOT NULL AND i.avg_brightness <= ?")
        params.append(float(brightness_max))
    if color_temperature:
        valid = {"warm", "cool", "neutral"}
        if color_temperature.lower() in valid:
            conditions.append("i.color_temperature = ?")
            params.append(color_temperature.lower())
    if brightness_distribution:
        valid_dist = {"left_heavy", "right_heavy", "middle_heavy", "edge_heavy", "balanced"}
        if brightness_distribution.lower() in valid_dist:
            conditions.append("i.brightness_distribution = ?")
            params.append(brightness_distribution.lower())
    return conditions, params


def _apply_color_hues_filter(conditions: List[str], params: List[Any],
                             color_hues: Optional[List[str]] = None,
                             exclude_color_hues: Optional[List[str]] = None) -> tuple:
    """v3.5.0 dominant-hue filter over the comma-wrapped dominant_color_tags.

    ``color_hues`` matches images whose dominant colors include ANY of the
    requested hues (OR semantics, like the other list filters).
    ``exclude_color_hues`` rejects images containing ANY of them; rows with
    NULL/empty tags are NOT excluded — they merely lack the attribute,
    mirroring _apply_exclude_colors_filter.
    Values outside color_analyzer.DOMINANT_COLOR_TAGS are ignored.
    """
    from color_analyzer import DOMINANT_COLOR_TAGS

    valid = set(DOMINANT_COLOR_TAGS)
    if color_hues:
        wanted = [h.lower() for h in color_hues if h and h.lower() in valid]
        if wanted:
            ors = " OR ".join(["i.dominant_color_tags LIKE ?"] * len(wanted))
            conditions.append(f"({ors})")
            params.extend([f"%,{h},%" for h in wanted])
    if exclude_color_hues:
        banned = [h.lower() for h in exclude_color_hues if h and h.lower() in valid]
        for h in banned:
            conditions.append(
                "(i.dominant_color_tags IS NULL OR i.dominant_color_tags NOT LIKE ?)"
            )
            params.append(f"%,{h},%")
    return conditions, params


def _apply_artist_filter(query: str, conditions: List[str], params: List[Any],
                         artist: Optional[str]) -> tuple:
    """Apply artist filter by joining with artist_predictions table.

    Args:
        query: Current query string
        conditions: Current WHERE conditions list
        params: Current parameter list
        artist: Artist name to filter by

    Returns:
        Tuple of (modified query, modified conditions, modified params)
    """
    if not artist:
        return query, conditions, params

    if "SELECT DISTINCT" not in query:
        query = query.replace("SELECT ", "SELECT DISTINCT ", 1)
    query += " INNER JOIN artist_predictions ap ON i.id = ap.image_id"
    conditions.append("ap.artist = ?")
    params.append(artist)

    return query, conditions, params


def _normalize_filter_id_list(values: Optional[List[int]]) -> List[int]:
    if values is None:
        return []

    normalized: List[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        normalized.append(image_id)
    return normalized


def _apply_id_list_filter(
    conditions: List[str],
    params: List[Any],
    image_ids: Optional[List[int]],
    *,
    include: bool,
) -> tuple:
    normalized_ids = _normalize_filter_id_list(image_ids)
    if not normalized_ids:
        if include and image_ids is not None:
            conditions.append("0 = 1")
        return conditions, params

    placeholders = ",".join("?" * len(normalized_ids))
    operator = "IN" if include else "NOT IN"
    conditions.append(f"i.id {operator} ({placeholders})")
    params.extend(normalized_ids)
    return conditions, params


def _apply_image_ids_filter(conditions: List[str], params: List[Any],
                            image_ids: Optional[List[int]]) -> tuple:
    """Apply image ID include filtering."""
    return _apply_id_list_filter(conditions, params, image_ids, include=True)


def _apply_excluded_image_ids_filter(conditions: List[str], params: List[Any],
                                     excluded_image_ids: Optional[List[int]]) -> tuple:
    """Apply image ID exclusion filtering."""
    return _apply_id_list_filter(conditions, params, excluded_image_ids, include=False)


def _apply_collection_filter(conditions: List[str], params: List[Any],
                             collection_id: Optional[int]) -> tuple:
    """Restrict results to images belonging to a collection (v3.3.1).

    Membership is a reference row in ``collection_items`` (see db_collections.py),
    so this composes with every other filter at the SQL level and stays correct
    under cursor pagination. ``None`` (or a non-positive id) is a no-op, leaving
    the gallery's normal unfiltered listing untouched.
    """
    if collection_id is None:
        return conditions, params
    try:
        cid = int(collection_id)
    except (TypeError, ValueError):
        return conditions, params
    if cid <= 0:
        return conditions, params
    # Regular collections resolve via their collection_items snapshot. Favorites
    # are path-anchored (rescan-proof) in favorite_paths, so the second branch
    # only yields rows when `cid` is the Favorites collection (gated by its slug).
    conditions.append(
        "i.id IN ("
        "SELECT ci.source_image_id FROM collection_items ci WHERE ci.collection_id = ? "
        "UNION "
        f"SELECT favorite_images.id FROM ({_favorite_image_ids_query()}) favorite_images "
        "JOIN collections c ON c.id = ? AND c.slug = 'favorites'"
        ")"
    )
    params.append(cid)
    params.append(cid)
    return conditions, params


def _apply_library_workspace_filter(
    conditions: List[str],
    params: List[Any],
    library_id: Optional[str] = None,
) -> tuple:
    """Always pin image queries to the active long-lived library workspace.

    Prevents cross-library leaks when multi-library is enabled. Defaults to
    ``main`` so pre-migration rows and missing headers stay correct.
    """
    try:
        from library_context import get_current_library_id, normalize_library_id

        lid = normalize_library_id(library_id if library_id is not None else get_current_library_id())
    except Exception:
        lid = str(library_id or "main").strip() or "main"
    conditions.append("COALESCE(i.library_id, 'main') = ?")
    params.append(lid)
    return conditions, params


def _apply_gallery_scope_filter(
    conditions: List[str],
    params: List[Any],
    scope: Optional[str],
) -> tuple:
    """Legacy process-session scope + mandatory long-lived library pin.

    ``current_session`` membership is retained for internal/compat callers but
    is no longer a user-facing gallery world. Library workspace filter always
    runs so multi-library isolation cannot be skipped.
    """
    conditions, params = _apply_library_workspace_filter(conditions, params)
    if scope is None or scope == "library":
        return conditions, params
    if scope == "current_session":
        conditions.append(
            "EXISTS ("
            "SELECT 1 FROM gallery_session_images gsi "
            "WHERE gsi.image_id = i.id"
            ")"
        )
        return conditions, params
    raise ValueError("scope must be 'current_session' or 'library'")


def _apply_readable_filter(
    conditions: List[str],
    params: List[Any],
    include_unreadable: bool = False,
) -> tuple:
    """Exclude unreadable images from normal library workflows by default."""
    if include_unreadable:
        return conditions, params

    conditions.append("COALESCE(i.is_readable, 1) = 1")
    return conditions, params
