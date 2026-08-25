"""Image listing / filtered count / filtered IDs (split from db_images_read.py).

``get_images``, ``get_filtered_image_count``, and ``get_filtered_image_ids``
moved here verbatim in the 2026-07 db_images_read split. Consumers keep
importing through the ``database`` facade (which re-exports these via the
``db_images_read`` file facade); do not import this module directly from
feature code.

Imports only from db_core / db_helpers / db_query / stdlib to avoid an import
cycle with the ``database`` facade.
"""
from typing import Optional, List, Dict, Any

from db_core import (
    PROMPT_MATCH_MODE_EXACT,
    get_db,
)
from db_helpers import (
    normalize_prompt_match_mode,
    _rows_to_dicts,
)
from db_query import (
    _IMAGE_COLUMNS_FULL,
    _IMAGE_COLUMNS_WITH_PROMPT,
    _IMAGE_COLUMNS_LIGHTWEIGHT,
    _apply_date_filter,
    _build_base_query,
    _group_by_clause,
    _apply_tag_filter,
    _apply_generator_filter,
    _apply_rating_filter,
    _apply_checkpoint_filter,
    _apply_lora_filter,
    _apply_exclude_tags_filter,
    _apply_exclude_generators_filter,
    _apply_exclude_ratings_filter,
    _apply_exclude_checkpoints_filter,
    _apply_exclude_loras_filter,
    _apply_exclude_prompts_filter,
    _apply_exclude_colors_filter,
    _apply_color_hues_filter,
    _apply_search_filter,
    _apply_prompt_terms_filter,
    _apply_dimension_filters,
    _apply_aesthetic_filter,
    _apply_saturation_filter,
    _apply_no_caption_filter,
    _apply_seed_filter,
    _apply_user_rating_filter,
    _apply_color_filter,
    _apply_artist_filter,
    _apply_image_ids_filter,
    _apply_excluded_image_ids_filter,
    _apply_collection_filter,
    _apply_gallery_scope_filter,
    _apply_folder_filter,
    _apply_metadata_presence_filter,
    _apply_readable_filter,
    _get_order_clause,
    _fetch_post_filtered_page,
    _fetch_post_filtered_ids,
)


def get_images(
    generators: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    tag_mode: str = "and",
    ratings: Optional[List[str]] = None,
    checkpoints: Optional[List[str]] = None,
    loras: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    sort_by: str = "newest",
    limit: int = 100,
    offset: int = 0,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    prompt_terms: Optional[List[str]] = None,  # Multi-prompt filter (AND logic)
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    aspect_ratio: Optional[str] = None,  # 'square', 'landscape', 'portrait'
    artist: Optional[str] = None,  # Artist filter
    image_ids: Optional[List[int]] = None,
    excluded_image_ids: Optional[List[int]] = None,
    min_aesthetic: Optional[float] = None,
    max_aesthetic: Optional[float] = None,
    date_from: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    date_to: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    min_user_rating: Optional[int] = None,  # v3.3.2 FF-2: gallery "★≥N" filter
    include_unreadable: bool = False,
    # v3.2.1 color filters
    brightness_min: Optional[float] = None,
    brightness_max: Optional[float] = None,
    color_temperature: Optional[str] = None,
    brightness_distribution: Optional[str] = None,
    # v3.2.2 per-item exclude filters
    exclude_tags: Optional[List[str]] = None,
    exclude_generators: Optional[List[str]] = None,
    exclude_ratings: Optional[List[str]] = None,
    exclude_checkpoints: Optional[List[str]] = None,
    exclude_loras: Optional[List[str]] = None,
    exclude_prompts: Optional[List[str]] = None,
    exclude_colors: Optional[List[str]] = None,
    color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue include
    exclude_color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue exclude
    collection_id: Optional[int] = None,
    scope: Optional[str] = None,
    folder: Optional[str] = None,  # v3.3.2 Library Navigation: recursive folder-subtree scope
    has_metadata: Optional[bool] = None,  # v3.3.2 small-opt: "has SD generation parameters" filter
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = None,
    aesthetic_unscored: Optional[bool] = None,
    min_saturation: Optional[float] = None,
    max_saturation: Optional[float] = None,
    seed: Optional[int] = None,
) ->List[Dict[str, Any]]:
    """
    Get images with optional filters.

    .. deprecated::
        Use get_images_paginated() for better performance with large datasets.
        OFFSET pagination becomes slow for large offsets as SQLite must scan
        all preceding rows. Cursor-based pagination in get_images_paginated()
        uses indexed lookups for constant-time page fetching.

    Args:
        generators: Filter by generator type (OR logic)
        tags: Filter by tags (AND logic - image must have ALL tags)
        ratings: Filter by rating tags (OR logic - image must have ANY rating OR be untagged)
        checkpoints: Filter by checkpoint names (OR logic)
        loras: Filter by lora names (AND logic - image must have ALL loras)
        search_query: Search in prompt text
        artist: Filter by artist name (from artist_predictions table)
        sort_by: Sorting method (newest, oldest, name_asc, name_desc, generator, generator_desc, prompt_length, prompt_length_asc, tag_count, tag_count_asc, rating, rating_desc, character_count, character_count_asc, random, file_size, file_size_asc)
        min_width, max_width, min_height, max_height: Dimension filters
        aspect_ratio: Filter by aspect ratio ('square', 'landscape', 'portrait')

    Returns:
        List of image dictionaries matching the filters.
    """
    if image_ids is not None and len(image_ids) == 0:
        return []

    with get_db() as conn:
        cursor = conn.cursor()

        # Determine if post-filtering is needed (for exact matching)
        normalized_prompt_match_mode = normalize_prompt_match_mode(prompt_match_mode)
        needs_prompt_post_filter = bool(prompt_terms) and normalized_prompt_match_mode == PROMPT_MATCH_MODE_EXACT
        needs_post_filter = needs_prompt_post_filter or bool(loras)
        # Include prompt fields when searching or post-filtering
        needs_prompt_fields = bool(search_query) or needs_post_filter
        if needs_post_filter:
            select_cols = _IMAGE_COLUMNS_FULL
        elif needs_prompt_fields:
            select_cols = _IMAGE_COLUMNS_WITH_PROMPT
        else:
            select_cols = _IMAGE_COLUMNS_LIGHTWEIGHT

        # Build base query with sorting subqueries
        query = _build_base_query(sort_by, select_cols)

        # Initialize conditions and params
        conditions: List[str] = []
        params: List[Any] = []

        # Apply tag filter (JOIN)
        query, params = _apply_tag_filter(query, tags, params, tag_mode)

        # Apply image ID include/exclude filters
        conditions, params = _apply_image_ids_filter(conditions, params, image_ids)
        conditions, params = _apply_excluded_image_ids_filter(conditions, params, excluded_image_ids)

        # Exclude unreadable images from normal library results (unless include_unreadable=True)
        conditions, params = _apply_readable_filter(conditions, params, include_unreadable)

        # Apply generator filter
        conditions, params = _apply_generator_filter(conditions, params, generators)

        # Apply rating filter
        conditions, params = _apply_rating_filter(conditions, params, ratings)

        # Apply checkpoint filter
        conditions, params = _apply_checkpoint_filter(conditions, params, checkpoints)

        # Apply lora filter (SQL-level)
        conditions, params = _apply_lora_filter(conditions, params, loras)

        # Apply search filter
        conditions, params = _apply_search_filter(conditions, params, search_query)

        # Apply prompt terms filter
        conditions, params = _apply_prompt_terms_filter(
            conditions,
            params,
            prompt_terms,
            normalized_prompt_match_mode,
        )

        # Apply dimension filters
        conditions, params = _apply_dimension_filters(
            conditions, params,
            min_width, max_width, min_height, max_height, aspect_ratio
        )

        # Apply aesthetic score filters
        conditions, params = _apply_aesthetic_filter(
            conditions, params, min_aesthetic, max_aesthetic, aesthetic_unscored
        )
        conditions, params = _apply_date_filter(
            conditions, params, date_from, date_to
        )
        conditions, params = _apply_user_rating_filter(
            conditions, params, min_user_rating
        )
        # Aurora Phase 3 gallery filters (saturation range, caption presence, seed)
        conditions, params = _apply_saturation_filter(
            conditions, params, min_saturation, max_saturation
        )
        conditions, params = _apply_no_caption_filter(conditions, params, no_caption)
        conditions, params = _apply_seed_filter(conditions, params, seed)

        # Apply v3.2.1 color filters
        conditions, params = _apply_color_filter(
            conditions, params,
            brightness_min, brightness_max, color_temperature, brightness_distribution,
        )

        # Apply v3.2.2 per-item exclude filters
        conditions, params = _apply_exclude_tags_filter(conditions, params, exclude_tags)
        conditions, params = _apply_exclude_generators_filter(conditions, params, exclude_generators)
        conditions, params = _apply_exclude_ratings_filter(conditions, params, exclude_ratings)
        conditions, params = _apply_exclude_checkpoints_filter(conditions, params, exclude_checkpoints)
        conditions, params = _apply_exclude_loras_filter(conditions, params, exclude_loras)
        conditions, params = _apply_exclude_prompts_filter(conditions, params, exclude_prompts, prompt_match_mode)
        conditions, params = _apply_exclude_colors_filter(conditions, params, exclude_colors)
        conditions, params = _apply_color_hues_filter(conditions, params, color_hues, exclude_color_hues)
        conditions, params = _apply_collection_filter(conditions, params, collection_id)
        conditions, params = _apply_gallery_scope_filter(conditions, params, scope)

        # Apply artist filter (JOIN)
        query, conditions, params = _apply_artist_filter(query, conditions, params, artist)

        # Apply folder-subtree scope (v3.3.2 Library Navigation)
        conditions, params = _apply_folder_filter(conditions, params, folder)
        # Apply "has SD generation parameters" scope (v3.3.2 small-opt)
        conditions, params = _apply_metadata_presence_filter(conditions, params, has_metadata)

        # Build WHERE clause
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Aggregate sorts (tag_count/character_count) need GROUP BY after WHERE.
        query += _group_by_clause(sort_by)

        # Get order clause and append to query
        order_clause = _get_order_clause(sort_by)

        if needs_post_filter:
            results = _fetch_post_filtered_page(
                conn,
                query,
                params,
                order_clause,
                prompt_terms,
                loras,
                prompt_match_mode=normalized_prompt_match_mode,
                post_offset=offset,
                limit=limit,
            )
        else:
            query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = _rows_to_dicts(rows)

        return results


def get_filtered_image_count(
    generators: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    tag_mode: str = "and",
    ratings: Optional[List[str]] = None,
    checkpoints: Optional[List[str]] = None,
    loras: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    prompt_terms: Optional[List[str]] = None,
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    aspect_ratio: Optional[str] = None,
    artist: Optional[str] = None,
    image_ids: Optional[List[int]] = None,
    excluded_image_ids: Optional[List[int]] = None,
    min_aesthetic: Optional[float] = None,
    max_aesthetic: Optional[float] = None,
    date_from: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    date_to: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    min_user_rating: Optional[int] = None,  # v3.3.2 FF-2: gallery "★≥N" filter
    include_unreadable: bool = False,
    # v3.2.1 color filters
    brightness_min: Optional[float] = None,
    brightness_max: Optional[float] = None,
    color_temperature: Optional[str] = None,
    brightness_distribution: Optional[str] = None,
    # v3.2.2 per-item exclude filters
    exclude_tags: Optional[List[str]] = None,
    exclude_generators: Optional[List[str]] = None,
    exclude_ratings: Optional[List[str]] = None,
    exclude_checkpoints: Optional[List[str]] = None,
    exclude_loras: Optional[List[str]] = None,
    exclude_prompts: Optional[List[str]] = None,
    exclude_colors: Optional[List[str]] = None,
    color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue include
    exclude_color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue exclude
    collection_id: Optional[int] = None,
    scope: Optional[str] = None,
    folder: Optional[str] = None,  # v3.3.2 Library Navigation: recursive folder-subtree scope
    has_metadata: Optional[bool] = None,  # v3.3.2 small-opt: "has SD generation parameters" filter
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = None,
    aesthetic_unscored: Optional[bool] = None,
    min_saturation: Optional[float] = None,
    max_saturation: Optional[float] = None,
    seed: Optional[int] = None,
) ->int:
    """Get count of images matching filters without loading image data.

    Memory-efficient: Only returns a count, doesn't load any image rows.
    For filters requiring post-filtering (prompt_terms, loras), this returns
    an approximate count based on SQL-level filtering.

    Args:
        Same filters as get_images()

    Returns:
        Number of matching images
    """
    if image_ids is not None and len(image_ids) == 0:
        return 0

    with get_db() as conn:
        cursor = conn.cursor()

        # Build count query
        query = "SELECT COUNT(DISTINCT i.id) FROM images i"

        conditions: List[str] = []
        params: List[Any] = []

        # Apply tag filter (JOIN)
        if tags:
            if tag_mode == "or":
                placeholders = ",".join("?" * len(tags))
                query += f" INNER JOIN tags _tor ON i.id = _tor.image_id AND _tor.tag IN ({placeholders})"
                params.extend(tags)
            else:
                for i, tag in enumerate(tags):
                    alias = f"t{i}"
                    query += f" INNER JOIN tags {alias} ON i.id = {alias}.image_id AND {alias}.tag = ?"
                    params.append(tag)


        # Apply image ID include/exclude filters
        conditions, params = _apply_image_ids_filter(conditions, params, image_ids)
        conditions, params = _apply_excluded_image_ids_filter(conditions, params, excluded_image_ids)

        # Exclude unreadable images from normal library results (unless include_unreadable=True)
        conditions, params = _apply_readable_filter(conditions, params, include_unreadable)

        # Apply generator filter
        conditions, params = _apply_generator_filter(conditions, params, generators)

        # Apply rating filter
        conditions, params = _apply_rating_filter(conditions, params, ratings)

        # Apply checkpoint filter
        conditions, params = _apply_checkpoint_filter(conditions, params, checkpoints)

        # Apply lora filter (SQL-level)
        conditions, params = _apply_lora_filter(conditions, params, loras)

        # Apply search filter
        conditions, params = _apply_search_filter(conditions, params, search_query)

        # Apply prompt terms filter
        conditions, params = _apply_prompt_terms_filter(
            conditions,
            params,
            prompt_terms,
            prompt_match_mode,
        )

        # Apply dimension filters
        conditions, params = _apply_dimension_filters(
            conditions, params,
            min_width, max_width, min_height, max_height, aspect_ratio
        )

        conditions, params = _apply_aesthetic_filter(
            conditions, params, min_aesthetic, max_aesthetic, aesthetic_unscored
        )
        conditions, params = _apply_date_filter(
            conditions, params, date_from, date_to
        )
        conditions, params = _apply_user_rating_filter(
            conditions, params, min_user_rating
        )
        # Aurora Phase 3 gallery filters (saturation range, caption presence, seed)
        conditions, params = _apply_saturation_filter(
            conditions, params, min_saturation, max_saturation
        )
        conditions, params = _apply_no_caption_filter(conditions, params, no_caption)
        conditions, params = _apply_seed_filter(conditions, params, seed)

        # Apply v3.2.1 color filters
        conditions, params = _apply_color_filter(
            conditions, params,
            brightness_min, brightness_max, color_temperature, brightness_distribution,
        )

        # Apply v3.2.2 per-item exclude filters
        conditions, params = _apply_exclude_tags_filter(conditions, params, exclude_tags)
        conditions, params = _apply_exclude_generators_filter(conditions, params, exclude_generators)
        conditions, params = _apply_exclude_ratings_filter(conditions, params, exclude_ratings)
        conditions, params = _apply_exclude_checkpoints_filter(conditions, params, exclude_checkpoints)
        conditions, params = _apply_exclude_loras_filter(conditions, params, exclude_loras)
        conditions, params = _apply_exclude_prompts_filter(conditions, params, exclude_prompts, prompt_match_mode)
        conditions, params = _apply_exclude_colors_filter(conditions, params, exclude_colors)
        conditions, params = _apply_color_hues_filter(conditions, params, color_hues, exclude_color_hues)
        conditions, params = _apply_collection_filter(conditions, params, collection_id)
        conditions, params = _apply_gallery_scope_filter(conditions, params, scope)

        # Apply artist filter (JOIN)
        query, conditions, params = _apply_artist_filter(query, conditions, params, artist)

        # Apply folder-subtree scope (v3.3.2 Library Navigation)
        conditions, params = _apply_folder_filter(conditions, params, folder)
        # Apply "has SD generation parameters" scope (v3.3.2 small-opt)
        conditions, params = _apply_metadata_presence_filter(conditions, params, has_metadata)

        # Build WHERE clause
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        cursor.execute(query, params)
        return cursor.fetchone()[0]


def get_filtered_image_ids(
    generators: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    tag_mode: str = "and",
    ratings: Optional[List[str]] = None,
    checkpoints: Optional[List[str]] = None,
    loras: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    sort_by: str = "newest",
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    prompt_terms: Optional[List[str]] = None,
    prompt_match_mode: str = PROMPT_MATCH_MODE_EXACT,
    aspect_ratio: Optional[str] = None,
    artist: Optional[str] = None,
    image_ids: Optional[List[int]] = None,
    excluded_image_ids: Optional[List[int]] = None,
    min_aesthetic: Optional[float] = None,
    max_aesthetic: Optional[float] = None,
    date_from: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    date_to: Optional[str] = None,  # inclusive YYYY-MM-DD (file time)
    min_user_rating: Optional[int] = None,  # v3.3.2 FF-2: gallery "★≥N" filter
    include_unreadable: bool = False,
    fetch_chunk_size: int = 5000,
    max_results: Optional[int] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    # v3.2.1 color filters
    brightness_min: Optional[float] = None,
    brightness_max: Optional[float] = None,
    color_temperature: Optional[str] = None,
    brightness_distribution: Optional[str] = None,
    # v3.2.2 per-item exclude filters
    exclude_tags: Optional[List[str]] = None,
    exclude_generators: Optional[List[str]] = None,
    exclude_ratings: Optional[List[str]] = None,
    exclude_checkpoints: Optional[List[str]] = None,
    exclude_loras: Optional[List[str]] = None,
    exclude_prompts: Optional[List[str]] = None,
    exclude_colors: Optional[List[str]] = None,
    color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue include
    exclude_color_hues: Optional[List[str]] = None,  # v3.5.0 dominant-hue exclude
    collection_id: Optional[int] = None,
    scope: Optional[str] = None,
    folder: Optional[str] = None,  # v3.3.2 Library Navigation: recursive folder-subtree scope
    has_metadata: Optional[bool] = None,  # v3.3.2 small-opt: "has SD generation parameters" filter
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = None,
    aesthetic_unscored: Optional[bool] = None,
    min_saturation: Optional[float] = None,
    max_saturation: Optional[float] = None,
    seed: Optional[int] = None,
) ->List[int]:
    """Get list of image IDs matching filters without loading full image data.

    Memory-efficient: Returns only IDs, not full image dictionaries.
    Used by sort session to minimize memory footprint.

    Args:
        Same filters as get_images()

    Returns:
        List of image IDs matching the filters
    """
    if image_ids is not None and len(image_ids) == 0:
        return []
    normalized_offset = max(0, int(offset or 0))
    if max_results is not None and max_results <= 0:
        return []
    if limit is not None and limit <= 0:
        return []

    result_limit = limit if limit is not None else max_results

    with get_db() as conn:
        cursor = conn.cursor()

        normalized_prompt_match_mode = normalize_prompt_match_mode(prompt_match_mode)
        needs_prompt_post_filter = bool(prompt_terms) and normalized_prompt_match_mode == PROMPT_MATCH_MODE_EXACT
        needs_post_filter = needs_prompt_post_filter or bool(loras)
        # sidecar_caption and compact metadata_json ride along because
        # _matches_exact_post_filters reads caption text and NAI character
        # slots as extra prompt-term sources; without them the post-filter
        # would drop rows whose text a rescan relocated, and this is the id
        # set batch move and delete-selected act on.
        select_cols = (
            "i.id, i.prompt, i.sidecar_caption, i.loras, i.metadata_json"
            if needs_post_filter
            else "i.id"
        )
        query = _build_base_query(sort_by, select_cols)

        conditions: List[str] = []
        params: List[Any] = []

        # Apply tag filter (JOIN)
        query, params = _apply_tag_filter(query, tags, params, tag_mode)

        # Apply image ID include/exclude filters
        conditions, params = _apply_image_ids_filter(conditions, params, image_ids)
        conditions, params = _apply_excluded_image_ids_filter(conditions, params, excluded_image_ids)

        # Exclude unreadable images from normal library results (unless include_unreadable=True)
        conditions, params = _apply_readable_filter(conditions, params, include_unreadable)

        # Apply generator filter
        conditions, params = _apply_generator_filter(conditions, params, generators)

        # Apply rating filter
        conditions, params = _apply_rating_filter(conditions, params, ratings)

        # Apply checkpoint filter
        conditions, params = _apply_checkpoint_filter(conditions, params, checkpoints)

        # Apply lora filter (SQL-level)
        conditions, params = _apply_lora_filter(conditions, params, loras)

        # Apply search filter
        conditions, params = _apply_search_filter(conditions, params, search_query)

        # Apply prompt terms filter
        conditions, params = _apply_prompt_terms_filter(
            conditions,
            params,
            prompt_terms,
            normalized_prompt_match_mode,
        )

        # Apply dimension filters
        conditions, params = _apply_dimension_filters(
            conditions, params,
            min_width, max_width, min_height, max_height, aspect_ratio
        )

        conditions, params = _apply_aesthetic_filter(
            conditions, params, min_aesthetic, max_aesthetic, aesthetic_unscored
        )
        conditions, params = _apply_date_filter(
            conditions, params, date_from, date_to
        )
        conditions, params = _apply_user_rating_filter(
            conditions, params, min_user_rating
        )
        # Aurora Phase 3 gallery filters (saturation range, caption presence, seed)
        conditions, params = _apply_saturation_filter(
            conditions, params, min_saturation, max_saturation
        )
        conditions, params = _apply_no_caption_filter(conditions, params, no_caption)
        conditions, params = _apply_seed_filter(conditions, params, seed)

        # Apply v3.2.1 color filters
        conditions, params = _apply_color_filter(
            conditions, params,
            brightness_min, brightness_max, color_temperature, brightness_distribution,
        )

        # Apply v3.2.2 per-item exclude filters
        conditions, params = _apply_exclude_tags_filter(conditions, params, exclude_tags)
        conditions, params = _apply_exclude_generators_filter(conditions, params, exclude_generators)
        conditions, params = _apply_exclude_ratings_filter(conditions, params, exclude_ratings)
        conditions, params = _apply_exclude_checkpoints_filter(conditions, params, exclude_checkpoints)
        conditions, params = _apply_exclude_loras_filter(conditions, params, exclude_loras)
        conditions, params = _apply_exclude_prompts_filter(conditions, params, exclude_prompts, prompt_match_mode)
        conditions, params = _apply_exclude_colors_filter(conditions, params, exclude_colors)
        conditions, params = _apply_color_hues_filter(conditions, params, color_hues, exclude_color_hues)
        conditions, params = _apply_collection_filter(conditions, params, collection_id)
        conditions, params = _apply_gallery_scope_filter(conditions, params, scope)

        # Apply artist filter (JOIN)
        query, conditions, params = _apply_artist_filter(query, conditions, params, artist)

        # Apply folder-subtree scope (v3.3.2 Library Navigation)
        conditions, params = _apply_folder_filter(conditions, params, folder)
        # Apply "has SD generation parameters" scope (v3.3.2 small-opt)
        conditions, params = _apply_metadata_presence_filter(conditions, params, has_metadata)

        # Build WHERE clause
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Aggregate sorts (tag_count/character_count) need GROUP BY after WHERE.
        query += _group_by_clause(sort_by)

        # Get order clause
        order_clause = _get_order_clause(sort_by)

        if needs_post_filter:
            return _fetch_post_filtered_ids(
                conn,
                query,
                params,
                order_clause,
                prompt_terms,
                loras,
                prompt_match_mode=normalized_prompt_match_mode,
                post_offset=normalized_offset,
                limit=result_limit,
                fetch_size=fetch_chunk_size,
            )

        query += f" ORDER BY {order_clause}"

        if result_limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([result_limit, normalized_offset])
        elif normalized_offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(normalized_offset)

        cursor.execute(query, params)

        ids: List[int] = []
        chunk_size = max(1, int(fetch_chunk_size))
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            ids.extend(int(row["id"]) for row in rows)
            if result_limit is not None and len(ids) >= result_limit:
                return ids[:result_limit]

        return ids
