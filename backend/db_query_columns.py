"""Column-list / order-SQL constants for image queries (split from db_query.py).

``VALID_SORT_OPTIONS``, the canonical image column field tuples,
``_format_image_column_list`` plus every comma-joined column string derived
from it, the reconnect-candidate columns, and the library-order /
stable-random / default ORDER BY SQL constants moved here verbatim in the
2026-07 db_query split. Everything is immutable and the intra-file
derivation order is preserved: each derived string is built from its source
tuple (or sibling constant) inside this one module. Consumers keep importing
through the ``db_query`` facade; do not import this module directly from
feature code.

Imports only from stdlib to avoid an import cycle with the ``database``
facade.
"""
from typing import Optional, Tuple


VALID_SORT_OPTIONS = {
    "newest", "oldest", "name_asc", "name_desc", "generator", "generator_desc",
    "prompt_length", "prompt_length_asc", "tag_count", "tag_count_asc",
    "rating", "rating_desc", "character_count", "character_count_asc",
    "random", "file_size", "file_size_asc", "aesthetic", "aesthetic_asc",
    # v3.3.2 user star rating (FF-2)
    "user_rating", "user_rating_asc",
    # v3.2.1 color sorts
    "brightness", "brightness_asc",
    "saturation", "saturation_asc",
    "brightness_skew", "brightness_skew_asc",
}

# Canonical column lists for image queries.
# All functions selecting image rows should reference these constants
# so column additions only need to change one place.
_IMAGE_COLUMNS_BASE_FIELDS = (
    "id",
    "path",
    "filename",
    "generator",
    "prompt",
    "negative_prompt",
    "metadata_json",
    "width",
    "height",
    "file_size",
    "checkpoint",
    "checkpoint_normalized",
    "loras",
    "model_hash",
    "is_readable",
    "read_error",
    "source_mtime_ns",
    "source_size",
    "metadata_status",
    "library_order_time",
    "source_file_mtime",
    "created_at",
    "indexed_at",
    "tagged_at",
    "ai_caption",
    "nl_caption",
    # Caption text lifted from a .txt/.json sidecar (migration 042). Neither a
    # generation prompt nor something this app generated, so it is its own
    # field rather than a variant of prompt / ai_caption / nl_caption.
    "sidecar_caption",
    # Which FORMAT that caption is in — tags / natural / mixed / unknown
    # (migration 044). Format is a different axis from the provenance split
    # above and is derived from the text, so it rides along as a marker instead
    # of another text column. Read-only hint for presentation and conversion:
    # nothing may filter or shorten captions by it.
    "sidecar_caption_format",
    "aesthetic_score",
    # v3.3.2 user star rating (FF-2): INTEGER 0-5, NOT NULL DEFAULT 0 (0 = unrated).
    "user_rating",
    # Color analysis (migration 010, v3.2.1). All nullable until backfill.
    "dominant_colors",
    "avg_brightness",
    "color_temperature",
    "color_saturation",
    "brightness_histogram",
    "brightness_skew",
    "brightness_distribution",
)
_IMAGE_COLUMNS_WITH_PROMPT_FIELDS = (
    "id",
    "filename",
    "path",
    "generator",
    "prompt",
    "negative_prompt",
    "width",
    "height",
    "file_size",
    "checkpoint",
    "checkpoint_normalized",
    "loras",
    "model_hash",
    "is_readable",
    "read_error",
    "source_mtime_ns",
    "source_size",
    "metadata_status",
    "library_order_time",
    "source_file_mtime",
    "created_at",
    "tagged_at",
    "aesthetic_score",
    # v3.3.2 user star rating (FF-2): 0-5, 0 = unrated.
    "user_rating",
    # Color summary for gallery list (v3.2.1). Histogram/skew skipped to keep row light.
    "dominant_colors",
    "avg_brightness",
    "color_temperature",
    "color_saturation",
    "brightness_distribution",
)
_IMAGE_COLUMNS_LIGHTWEIGHT_FIELDS = (
    "id",
    "filename",
    "path",
    "generator",
    "width",
    "height",
    "file_size",
    "checkpoint",
    "checkpoint_normalized",
    "loras",
    "model_hash",
    "is_readable",
    "read_error",
    "source_mtime_ns",
    "source_size",
    "metadata_status",
    "library_order_time",
    "source_file_mtime",
    "created_at",
    "tagged_at",
    "aesthetic_score",
    # v3.3.2 user star rating (FF-2): 0-5, 0 = unrated.
    "user_rating",
    # Color summary for gallery list (v3.2.1). Histogram/skew skipped to keep row light.
    "dominant_colors",
    "avg_brightness",
    "color_temperature",
    "color_saturation",
    "brightness_distribution",
)


def _format_image_column_list(columns: Tuple[str, ...], *, alias: Optional[str] = None) -> str:
    """Return a comma-joined image column list, optionally qualified by an alias."""
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{column}" for column in columns)


_IMAGE_COLUMNS_FULL = _format_image_column_list(_IMAGE_COLUMNS_BASE_FIELDS, alias="i")
_IMAGE_COLUMNS_WITH_PROMPT = _format_image_column_list(_IMAGE_COLUMNS_WITH_PROMPT_FIELDS, alias="i")
_IMAGE_COLUMNS_LIGHTWEIGHT = _format_image_column_list(_IMAGE_COLUMNS_LIGHTWEIGHT_FIELDS, alias="i")
_IMAGE_COLUMNS_BARE = _format_image_column_list(_IMAGE_COLUMNS_BASE_FIELDS)

# Minimal columns the missing-file reconnect flow consumes per candidate
# (id/path/filename plus the size + mtime fields used by image_service
# match logic). Kept narrow so large libraries do not load full rows.
_RECONNECT_CANDIDATE_FIELDS = (
    "id",
    "path",
    "filename",
    "file_size",
    "source_size",
    "source_mtime_ns",
    "source_file_mtime",
)
_RECONNECT_CANDIDATE_COLUMNS = _format_image_column_list(_RECONNECT_CANDIDATE_FIELDS)

_LIBRARY_ORDER_SQL_UNQUALIFIED = "COALESCE(library_order_time, created_at)"
_LIBRARY_ORDER_SQL = "COALESCE(i.library_order_time, i.created_at)"
_STABLE_RANDOM_ORDER_SQL = "((i.id * 1103515245 + 12345) & 2147483647)"
# Default ORDER BY clause; also the fallback for unknown sort keys. Defined
# once so the fallback stays a fixed constant rather than a per-call f-string.
_DEFAULT_ORDER_CLAUSE = f"{_LIBRARY_ORDER_SQL} DESC, i.id DESC"
