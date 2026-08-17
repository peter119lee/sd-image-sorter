"""
SQLite database for storing image metadata and tags.

This module provides direct function-based database access for backward compatibility.
For new code, consider using the repository pattern from db_repos:

    from db_repos import ImageRepository, TagRepository, CollectionRepository
    from db_repos import ImageFilters

    # Example usage:
    image_repo = ImageRepository()
    images = image_repo.find_all(filters=ImageFilters(tags=["portrait"]), limit=50)
    image = image_repo.find_by_id(123)

    # Dependency injection with FastAPI:
    def get_image_repo() -> ImageRepository:
        return ImageRepository()

See backend/db_repos/repositories/ for the repository implementations.
"""
import sqlite3
import os
import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union, Iterator
from contextlib import contextmanager
import time
import threading

from config import (
    DATABASE_PATH,
    FAVORITES_COLLECTION_SLUG,
    FAVORITES_COLLECTION_NAME,
    FAVORITES_FOLDER_PATH,
)
from utils.source_paths import (
    build_indexed_folder_scope_query_patterns,
    build_indexed_image_lookup_candidates,
    indexed_image_path_match_key,
    is_indexed_image_path_in_folder_scope,
    is_case_insensitive_indexed_path,
    normalize_indexed_image_path,
)
from utils.model_names import (
    checkpoint_identity_key,
    normalize_checkpoint_name as _normalize_checkpoint_name,
)
from utils.pagination_cursor import encode_image_cursor_from_image
from metadata_storage import compact_existing_metadata_json, compact_metadata_json

import db_core
from db_core import (
    PROMPT_MATCH_MODE_EXACT,
    PROMPT_MATCH_MODE_CONTAINS,
    VALID_PROMPT_MATCH_MODES,
    _adapt_datetime_for_sqlite,
    _tags_cache_lock,
    _TAGS_CACHE_TTL,
    _generators_cache_lock,
    _invalidate_facet_caches,
    _invalidate_tags_cache,
    SCHEMA_VERSION_ROW_ID,
    STALE_PENDING_METADATA_READ_ERROR,
    get_db,
)
from db_helpers import (
    _normalize_indexed_image_path,
    _path_query_match_clause,
    _folder_scope_query_match_clause,
    _ensure_content_fingerprint_value,
    normalize_prompt_token,
    normalize_prompt_match_mode,
    escape_like_pattern,
    normalize_lora_name,
    normalize_checkpoint_name,
    extract_prompt_tokens,
    extract_lora_names,
    _serialize_loras,
    _deserialize_loras,
    _normalize_source_fingerprint,
    _normalize_content_fingerprint,
    _row_value,
    _json_safe_db_value,
    _row_to_dict,
    _rows_to_dicts,
    _has_source_fingerprint,
    _is_source_fingerprint_changed,
    _has_derived_state,
    _should_clear_derived_state,
)
from db_query import (
    VALID_SORT_OPTIONS,
    _IMAGE_COLUMNS_BASE_FIELDS,
    _IMAGE_COLUMNS_WITH_PROMPT_FIELDS,
    _IMAGE_COLUMNS_LIGHTWEIGHT_FIELDS,
    _format_image_column_list,
    _IMAGE_COLUMNS_FULL,
    _IMAGE_COLUMNS_WITH_PROMPT,
    _IMAGE_COLUMNS_LIGHTWEIGHT,
    _IMAGE_COLUMNS_BARE,
    _RECONNECT_CANDIDATE_FIELDS,
    _RECONNECT_CANDIDATE_COLUMNS,
    _LIBRARY_ORDER_SQL_UNQUALIFIED,
    _LIBRARY_ORDER_SQL,
    _STABLE_RANDOM_ORDER_SQL,
    _DEFAULT_ORDER_CLAUSE,
    _build_base_query,
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
    _apply_search_filter,
    _apply_prompt_terms_filter,
    _apply_dimension_filters,
    _apply_aesthetic_filter,
    _apply_color_filter,
    _apply_artist_filter,
    _normalize_filter_id_list,
    _apply_id_list_filter,
    _apply_image_ids_filter,
    _apply_excluded_image_ids_filter,
    _apply_readable_filter,
    _get_order_clause,
    _supports_cursor_sort,
    _fetch_post_filtered_page,
    _fetch_post_filtered_ids,
    _matches_exact_post_filters,
    _post_filter_results,
)
from db_schema import (
    UnsupportedDatabaseSchemaVersionError,
    _ensure_schema_version_table,
    _get_schema_version,
    _set_schema_version,
    validate_database_schema_version,
    _run_post_migration_vacuum,
    _recover_stale_pending_metadata_rows,
    init_db,
)
from db_images_write import (
    _clear_image_derived_state,
    _sync_image_loras,
    _sync_image_prompt_tokens,
    add_image,
    _get_existing_images_by_paths,
    _compact_persisted_metadata_json,
    _upsert_image_record,
    add_images_batch,
    get_image_scan_state_by_paths,
    reconnect_image_source_path,
    _mark_image_tagged,
    delete_images_by_ids,
    delete_images_by_paths,
    mark_pending_images_metadata_error,
    _copy_image_derived_state,
    copy_image_derived_state,
    set_image_captions,
    update_image_caption,
    set_user_rating,
    update_image_colors,
    update_image_path,
    update_image_metadata,
    update_reparsed_prompt_fields,
    update_reparsed_sidecar_caption,
    mark_image_unreadable,
    mark_image_unreadable_by_path,
    mark_image_readable,
    delete_image,
)
from db_collections import (
    get_collection_by_slug,
    get_collection_item,
    add_collection_item,
    remove_collection_item,
    get_favorite_source_ids,
    get_favorites_count,
    # v3.3.0 FEAT-COLLECTIONS
    get_favorites_collection_id,
    is_favorited,
    set_favorite,
    list_collections,
    create_collection,
    rename_collection,
    delete_collection,
    collection_exists,
    set_collection_membership,
    set_collection_membership_bulk,
    get_collection_image_ids,
)
from db_library_roots import (
    add_library_root,
    record_library_root_scan,
    list_library_roots,
    get_library_root,
    remove_library_root,
    set_library_root_enabled,
    touch_library_root_scanned,
)
from db_gallery_session import (
    clear_gallery_session,
    add_gallery_session_image_ids,
    add_gallery_session_paths,
    get_gallery_session_image_ids,
)
from db_dataset_projects import (
    list_dataset_project_records,
    get_dataset_project_record,
    require_dataset_project_revision,
    create_dataset_project_record,
    update_dataset_project_record,
    archive_dataset_project_record,
    restore_dataset_project_record,
    delete_dataset_project_record,
)
from db_annotation_revisions import (
    ANNOTATION_KIND_TRAINING_CAPTION,
    AnnotationRevisionError,
    AnnotationContentValidationError,
    AnnotationProjectNotFoundError,
    AnnotationProjectRevisionConflictError,
    AnnotationProjectStateConflictError,
    AnnotationSubjectNotInProjectError,
    AnnotationSubjectNotFoundError,
    AnnotationSubjectProjectConflictError,
    AnnotationSubjectIdentityConflictError,
    AnnotationHeadConflictError,
    AnnotationRevisionNotFoundError,
    AnnotationRevisionSubjectConflictError,
    create_project_library_training_caption_revision,
    create_project_local_training_caption_revision,
    restore_project_training_caption_revision,
    select_project_training_caption_head,
    get_project_training_caption_head,
    validate_project_training_caption_subject,
    resolve_project_library_training_caption_head,
    resolve_project_local_training_caption_head,
    list_project_training_caption_heads,
    list_project_training_caption_revisions,
)
from db_reconnect_reviews import (
    add_reconnect_review,
    delete_pending_reconnect_reviews,
    prune_resolved_reconnect_reviews,
    count_pending_reconnect_reviews,
    list_reconnect_reviews,
    get_reconnect_review,
    resolve_reconnect_review,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_RESOLVED,
    REVIEW_STATUS_CONFLICT,
)
from db_tags import (
    add_tags,
    add_tags_batch,
    tag_update_transaction,
    get_image_tags,
    get_image_tags_map,
    get_tag_writer_provenance_map,
    get_image_ids_already_tagged,
    get_all_tags,
    _facet_search_rank_params,
    _facet_search_rank_sql,
    _append_optional_limit,
    search_tags,
    _query_indexed_facet,
    get_all_prompt_tokens,
    get_all_loras,
    get_all_generators,
)
from db_tag_scores import (
    replace_scores_in_cursor,
    get_tag_model_audit,
    get_scores_for_images,
    list_score_models,
    find_coverage_gaps,
    get_tag_score_stats,
    purge_tag_scores,
)
from db_facets import (
    get_metadata_status_counts,
    _library_health_percent,
    get_library_health_report,
    _build_library_health_recommendations,
    get_all_checkpoints,
    count_checkpoints,
)
from db_images_read import (
    get_images_in_folder_scope,
    count_prompt_coverage_in_folder_scope,
    get_library_folders,
    get_missing_image_reconnect_candidates,
    get_images,
    get_filtered_image_count,
    get_filtered_image_ids,
    get_images_paginated,
    _get_filtered_count,
    get_image_by_id,
    get_images_missing_color_data,
    count_images_missing_color_data,
    get_image_by_path,
    get_images_by_ids,
    get_untagged_images,
    get_all_image_ids,
    get_untagged_image_ids,
    count_all_image_ids,
    count_untagged_image_ids,
    iter_all_image_id_chunks,
    iter_untagged_image_id_chunks,
    get_image_count,
)


logger = logging.getLogger(__name__)


# Connection state stays on the ``database`` module so the test suite can keep
# monkeypatching ``database.DATABASE_PATH`` / ``database._pragmas_initialized``.
# The concrete factory below is injected into db_core so every db_* module
# shares it via ``db_core.get_db``/``db_core.get_connection`` without importing
# ``database`` (which would create an import cycle).
_pragmas_initialized: set = set()
_pragmas_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory and performance optimizations."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout=30000")  # v3.3.2: wait up to 30s on lock (large-library scan/tag vs browse contention)
        # WAL mode and other persistent PRAGMAs only need to be set once per database path
        db_path = os.path.abspath(DATABASE_PATH)
        if db_path not in _pragmas_initialized:
            with _pragmas_lock:
                if db_path not in _pragmas_initialized:
                    validate_database_schema_version(conn)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
                    _pragmas_initialized.add(db_path)
        return conn
    except Exception:
        conn.close()
        raise


db_core.set_connection_provider(get_connection)


def iter_filtered_image_id_chunks(
    *,
    chunk_size: int = 2000,
    query_page_size: Optional[int] = None,
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
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_user_rating: Optional[int] = None,
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
    folder: Optional[str] = None,
    has_metadata: Optional[bool] = None,
    # Aurora Phase 3 gallery filters
    no_caption: Optional[bool] = None,
    aesthetic_unscored: Optional[bool] = None,
    min_saturation: Optional[float] = None,
    max_saturation: Optional[float] = None,
    seed: Optional[int] = None,
) -> Iterator[List[int]]:
    """Yield filtered image IDs in bounded consumer chunks.

    Kept in the ``database`` facade (not moved to ``db_images_read``) so the
    in-module ``get_filtered_image_ids`` reference resolves to the re-exported,
    monkeypatchable ``database`` global. Tests patch ``database.get_filtered_image_ids``
    and expect this generator to honor that patch.

    ``query_page_size`` can exceed ``chunk_size`` so read-only consumers avoid
    repeating an expensive filtered query for every small write chunk. Only one
    query page and one consumer chunk are retained at a time. Every supported
    deterministic order ends with ``i.id``, so adjacent JOIN duplicates can be
    removed across page boundaries without retaining a full seen-ID set.
    """
    normalized_chunk_size = max(1, int(chunk_size or 2000))
    normalized_query_page_size = max(
        1,
        int(query_page_size or normalized_chunk_size),
    )
    consumer_chunk: List[int] = []
    last_image_id: Optional[int] = None
    offset = 0
    while True:
        page = get_filtered_image_ids(
            generators=generators,
            tags=tags,
            tag_mode=tag_mode,
            ratings=ratings,
            checkpoints=checkpoints,
            loras=loras,
            search_query=search_query,
            sort_by=sort_by,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            prompt_terms=prompt_terms,
            prompt_match_mode=prompt_match_mode,
            aspect_ratio=aspect_ratio,
            artist=artist,
            image_ids=image_ids,
            excluded_image_ids=excluded_image_ids,
            min_aesthetic=min_aesthetic,
            max_aesthetic=max_aesthetic,
            date_from=date_from,
            date_to=date_to,
            min_user_rating=min_user_rating,
            include_unreadable=include_unreadable,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            color_temperature=color_temperature,
            brightness_distribution=brightness_distribution,
            exclude_tags=exclude_tags,
            exclude_generators=exclude_generators,
            exclude_ratings=exclude_ratings,
            exclude_checkpoints=exclude_checkpoints,
            exclude_loras=exclude_loras,
            exclude_prompts=exclude_prompts,
            exclude_colors=exclude_colors,
            color_hues=color_hues,
            exclude_color_hues=exclude_color_hues,
            collection_id=collection_id,
            scope=scope,
            folder=folder,
            has_metadata=has_metadata,
            no_caption=no_caption,
            aesthetic_unscored=aesthetic_unscored,
            min_saturation=min_saturation,
            max_saturation=max_saturation,
            seed=seed,
            fetch_chunk_size=normalized_query_page_size,
            offset=offset,
            limit=normalized_query_page_size,
        )
        if not page:
            break
        for raw_image_id in page:
            image_id = int(raw_image_id)
            if image_id == last_image_id:
                continue
            last_image_id = image_id
            consumer_chunk.append(image_id)
            if len(consumer_chunk) >= normalized_chunk_size:
                yield consumer_chunk
                consumer_chunk = []
        if len(page) < normalized_query_page_size:
            break
        offset += len(page)
    if consumer_chunk:
        yield consumer_chunk


def iter_id_snapshot_chunks(
    id_chunks: Iterator[List[int]],
    *,
    chunk_size: int = 500,
) -> Iterator[List[int]]:
    """Materialize ``id_chunks`` into a temp-file snapshot before yielding.

    ``iter_filtered_image_id_chunks`` re-runs its filtered query with an
    advancing offset between chunks. When the consumer mutates rows the filter
    matches (bulk-removing tag X from a tag-X scope, smart-tag/VLM writes
    against a tag-filtered selection token, ...), every committed chunk
    shrinks the matching set while the offset advances, silently skipping
    about half the images. Draining the source iterator into a temp file
    BEFORE yielding the first chunk pins the worklist to the pre-mutation
    state without holding 100k+ IDs in memory (mirrors the snapshot pattern
    used by ``SortingService._write_id_snapshot`` and
    ``ImageService._iter_selection_token_snapshot_chunks``). The temp file is
    removed when iteration finishes, errors out, or the consumer abandons the
    generator.
    """
    normalized_chunk_size = max(1, int(chunk_size or 500))
    snapshot_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            snapshot_path = handle.name
            for id_chunk in id_chunks:
                for image_id in id_chunk:
                    handle.write(f"{int(image_id)}\n")

        batch: List[int] = []
        with open(snapshot_path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    image_id = int(line.strip())
                except ValueError:
                    continue
                batch.append(image_id)
                if len(batch) >= normalized_chunk_size:
                    yield batch
                    batch = []
        if batch:
            yield batch
    finally:
        if snapshot_path:
            try:
                os.unlink(snapshot_path)
            except OSError:
                logger.debug("Failed to remove ID snapshot temp file: %s", snapshot_path)


# NOTE: init_db() is called by the lifespan handler in main.py.
# Do not call it at module import time to avoid side effects.
