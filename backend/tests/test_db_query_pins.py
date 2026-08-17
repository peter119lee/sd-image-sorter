"""Characterization pins for ``db_query`` (god-file split, step 0).

``db_query.py`` is the pure SQL-construction layer under the image read path:
column-list constants, the base-query builder, every ``_apply_*`` filter, the
order-clause / cursor-support resolvers, and the post-filter helpers. It opens
no connection of its own (the ``_fetch_*`` scanners take a live conn in).

The defining contract here is an **import fan-in by reference**: three fresh
siblings and the ``database`` facade origin-import db_query's helpers *by name*
and call them verbatim — db_images_query pulls 38 names, db_images_paginate 36,
db_images_lookup 2, and database.py 43. These pins lock:

* the identity re-export union (``consumer.X is db_query.X`` for every imported
  name across all four namespaces) so a later tiling split cannot silently drop,
  rename, or wrapper a name,
* the immutable SQL/column constants consumed at import time,
* the ``(conditions, params)`` in/out contract of every ``_apply_*`` family
  (clause text + bound-param order + in-place-mutation identity), which a split
  must not reorder or rename,
* base-query / group-by / order-clause / cursor-support shapes,
* the lazy ``color_analyzer`` import inside the dominant-hue filter,
* the pure post-filter (exact prompt/LORA) matchers.

Everything is pure-function testing (no DB) except where a filter is exercised
end-to-end; those use the shared temp-file ``test_db`` fixture. No real
``data/images.db`` is touched. Complements ``test_db_images_read_pins.py``
(reader-level behavior) and ``test_database.py`` (integration filters); these
pins deliberately target the construction helpers and the export surface.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Match the sibling test module's import bootstrap.
sys.path.insert(0, str(Path(__file__).parent.parent))

import db_query
import database  # noqa: F401  (import injects the connection provider + facade)
import db_images_query
import db_images_paginate
import db_images_lookup

from db_query import (
    VALID_SORT_OPTIONS,
    _format_image_column_list,
    _build_base_query,
    _group_by_clause,
    _get_order_clause,
    _supports_cursor_sort,
    _apply_generator_filter,
    _apply_rating_filter,
    _apply_checkpoint_filter,
    _apply_lora_filter,
    _apply_search_filter,
    _apply_dimension_filters,
    _apply_aesthetic_filter,
    _apply_saturation_filter,
    _apply_seed_filter,
    _apply_user_rating_filter,
    _apply_metadata_presence_filter,
    _apply_no_caption_filter,
    _apply_color_filter,
    _apply_color_hues_filter,
    _apply_exclude_prompts_filter,
    _apply_exclude_colors_filter,
    _apply_exclude_ratings_filter,
    _apply_tag_filter,
    _apply_artist_filter,
    _apply_collection_filter,
    _apply_image_ids_filter,
    _apply_excluded_image_ids_filter,
    _apply_readable_filter,
    _apply_date_filter,
    _matches_exact_post_filters,
    _post_filter_results,
    _fetch_post_filtered_page,
    _LIBRARY_ORDER_SQL_UNQUALIFIED,
    _LIBRARY_ORDER_SQL,
    _STABLE_RANDOM_ORDER_SQL,
    _DEFAULT_ORDER_CLAUSE,
    _RECONNECT_CANDIDATE_COLUMNS,
    _IMAGE_COLUMNS_FULL,
    _IMAGE_COLUMNS_BARE,
)
from db_core import PROMPT_MATCH_MODE_EXACT, PROMPT_MATCH_MODE_CONTAINS
from db_helpers import _favorite_image_ids_query


# ===========================================================================
# The exact by-reference import surface each consumer pulls from db_query.
# These lists ARE the re-export contract: a split of db_query must keep every
# one of these names resolvable AND identical (same object) on the consumer.
# Kept verbatim in sync with the module headers as of the 2026-07 read split.
# ===========================================================================

_DB_IMAGES_QUERY_NAMES = [
    "_IMAGE_COLUMNS_FULL",
    "_IMAGE_COLUMNS_WITH_PROMPT",
    "_IMAGE_COLUMNS_LIGHTWEIGHT",
    "_apply_date_filter",
    "_build_base_query",
    "_group_by_clause",
    "_apply_tag_filter",
    "_apply_generator_filter",
    "_apply_rating_filter",
    "_apply_checkpoint_filter",
    "_apply_lora_filter",
    "_apply_exclude_tags_filter",
    "_apply_exclude_generators_filter",
    "_apply_exclude_ratings_filter",
    "_apply_exclude_checkpoints_filter",
    "_apply_exclude_loras_filter",
    "_apply_exclude_prompts_filter",
    "_apply_exclude_colors_filter",
    "_apply_color_hues_filter",
    "_apply_search_filter",
    "_apply_prompt_terms_filter",
    "_apply_dimension_filters",
    "_apply_aesthetic_filter",
    "_apply_saturation_filter",
    "_apply_no_caption_filter",
    "_apply_seed_filter",
    "_apply_user_rating_filter",
    "_apply_color_filter",
    "_apply_artist_filter",
    "_apply_image_ids_filter",
    "_apply_excluded_image_ids_filter",
    "_apply_collection_filter",
    "_apply_folder_filter",
    "_apply_metadata_presence_filter",
    "_apply_readable_filter",
    "_get_order_clause",
    "_fetch_post_filtered_page",
    "_fetch_post_filtered_ids",
]

_DB_IMAGES_PAGINATE_NAMES = [
    "_IMAGE_COLUMNS_FULL",
    "_IMAGE_COLUMNS_LIGHTWEIGHT",
    "_LIBRARY_ORDER_SQL_UNQUALIFIED",
    "_apply_date_filter",
    "_build_base_query",
    "_group_by_clause",
    "_apply_tag_filter",
    "_apply_generator_filter",
    "_apply_rating_filter",
    "_apply_checkpoint_filter",
    "_apply_lora_filter",
    "_apply_exclude_tags_filter",
    "_apply_exclude_generators_filter",
    "_apply_exclude_ratings_filter",
    "_apply_exclude_checkpoints_filter",
    "_apply_exclude_loras_filter",
    "_apply_exclude_prompts_filter",
    "_apply_exclude_colors_filter",
    "_apply_color_hues_filter",
    "_apply_search_filter",
    "_apply_prompt_terms_filter",
    "_apply_dimension_filters",
    "_apply_aesthetic_filter",
    "_apply_saturation_filter",
    "_apply_no_caption_filter",
    "_apply_seed_filter",
    "_apply_user_rating_filter",
    "_apply_color_filter",
    "_apply_artist_filter",
    "_apply_collection_filter",
    "_apply_folder_filter",
    "_apply_metadata_presence_filter",
    "_apply_readable_filter",
    "_get_order_clause",
    "_supports_cursor_sort",
    "_fetch_post_filtered_page",
]

_DB_IMAGES_LOOKUP_NAMES = ["_IMAGE_COLUMNS_BARE", "_RECONNECT_CANDIDATE_COLUMNS"]

_DATABASE_FACADE_NAMES = [
    "VALID_SORT_OPTIONS",
    "_IMAGE_COLUMNS_BASE_FIELDS",
    "_IMAGE_COLUMNS_WITH_PROMPT_FIELDS",
    "_IMAGE_COLUMNS_LIGHTWEIGHT_FIELDS",
    "_format_image_column_list",
    "_IMAGE_COLUMNS_FULL",
    "_IMAGE_COLUMNS_WITH_PROMPT",
    "_IMAGE_COLUMNS_LIGHTWEIGHT",
    "_IMAGE_COLUMNS_BARE",
    "_RECONNECT_CANDIDATE_FIELDS",
    "_RECONNECT_CANDIDATE_COLUMNS",
    "_LIBRARY_ORDER_SQL_UNQUALIFIED",
    "_LIBRARY_ORDER_SQL",
    "_STABLE_RANDOM_ORDER_SQL",
    "_DEFAULT_ORDER_CLAUSE",
    "_build_base_query",
    "_apply_tag_filter",
    "_apply_generator_filter",
    "_apply_rating_filter",
    "_apply_checkpoint_filter",
    "_apply_lora_filter",
    "_apply_exclude_tags_filter",
    "_apply_exclude_generators_filter",
    "_apply_exclude_ratings_filter",
    "_apply_exclude_checkpoints_filter",
    "_apply_exclude_loras_filter",
    "_apply_search_filter",
    "_apply_prompt_terms_filter",
    "_apply_dimension_filters",
    "_apply_aesthetic_filter",
    "_apply_color_filter",
    "_apply_artist_filter",
    "_normalize_filter_id_list",
    "_apply_id_list_filter",
    "_apply_image_ids_filter",
    "_apply_excluded_image_ids_filter",
    "_apply_readable_filter",
    "_get_order_clause",
    "_supports_cursor_sort",
    "_fetch_post_filtered_page",
    "_fetch_post_filtered_ids",
    "_matches_exact_post_filters",
    "_post_filter_results",
]

# The union is the full set db_query MUST keep exporting after any split.
_REQUIRED_EXPORT_UNION = sorted(
    set(_DB_IMAGES_QUERY_NAMES)
    | set(_DB_IMAGES_PAGINATE_NAMES)
    | set(_DB_IMAGES_LOOKUP_NAMES)
    | set(_DATABASE_FACADE_NAMES)
)


# ===========================================================================
# Import fan-in / re-export identity (the defining contract)
# ===========================================================================


class TestReExportIdentityUnion:
    def test_db_query_exposes_every_required_export(self):
        """db_query must own every name any consumer imports by reference."""
        for name in _REQUIRED_EXPORT_UNION:
            assert hasattr(db_query, name), f"db_query lost required export {name}"

    def test_db_images_query_bindings_are_identical_objects(self):
        for name in _DB_IMAGES_QUERY_NAMES:
            assert getattr(db_images_query, name) is getattr(db_query, name), (
                f"{name} diverged between db_images_query and db_query"
            )

    def test_db_images_paginate_bindings_are_identical_objects(self):
        for name in _DB_IMAGES_PAGINATE_NAMES:
            assert getattr(db_images_paginate, name) is getattr(db_query, name), (
                f"{name} diverged between db_images_paginate and db_query"
            )

    def test_db_images_lookup_bindings_are_identical_objects(self):
        for name in _DB_IMAGES_LOOKUP_NAMES:
            assert getattr(db_images_lookup, name) is getattr(db_query, name), (
                f"{name} diverged between db_images_lookup and db_query"
            )

    def test_database_facade_bindings_are_identical_objects(self):
        for name in _DATABASE_FACADE_NAMES:
            assert getattr(database, name) is getattr(db_query, name), (
                f"{name} diverged between database facade and db_query"
            )


# ===========================================================================
# Immutable SQL / column constants (consumed at import time by value)
# ===========================================================================


class TestSqlConstants:
    def test_library_order_expressions(self):
        assert (
            _LIBRARY_ORDER_SQL_UNQUALIFIED == "COALESCE(library_order_time, created_at)"
        )
        assert _LIBRARY_ORDER_SQL == "COALESCE(i.library_order_time, i.created_at)"

    def test_stable_random_and_default_order_clause(self):
        assert _STABLE_RANDOM_ORDER_SQL == "((i.id * 1103515245 + 12345) & 2147483647)"
        # The default clause is the newest ordering and the unknown-key fallback.
        assert (
            _DEFAULT_ORDER_CLAUSE
            == "COALESCE(i.library_order_time, i.created_at) DESC, i.id DESC"
        )

    def test_reconnect_candidate_columns_are_bare_and_narrow(self):
        assert _RECONNECT_CANDIDATE_COLUMNS == (
            "id, path, filename, file_size, source_size, source_mtime_ns, source_file_mtime"
        )

    def test_full_columns_are_i_aliased_and_bare_columns_are_not(self):
        assert _IMAGE_COLUMNS_FULL.startswith("i.id, i.path, i.filename, i.generator,")
        assert _IMAGE_COLUMNS_BARE.startswith("id, path, filename, generator,")
        # Same field set, differing only by the alias prefix.
        assert _IMAGE_COLUMNS_FULL == ", ".join(
            "i." + c for c in _IMAGE_COLUMNS_BARE.split(", ")
        )

    def test_valid_sort_options_is_the_frozen_27_key_set(self):
        assert VALID_SORT_OPTIONS == {
            "newest",
            "oldest",
            "name_asc",
            "name_desc",
            "generator",
            "generator_desc",
            "prompt_length",
            "prompt_length_asc",
            "tag_count",
            "tag_count_asc",
            "rating",
            "rating_desc",
            "character_count",
            "character_count_asc",
            "random",
            "file_size",
            "file_size_asc",
            "aesthetic",
            "aesthetic_asc",
            "user_rating",
            "user_rating_asc",
            "brightness",
            "brightness_asc",
            "saturation",
            "saturation_asc",
            "brightness_skew",
            "brightness_skew_asc",
        }


class TestFormatColumnList:
    def test_bare_and_aliased_join_shapes(self):
        cols = ("id", "path", "width")
        assert _format_image_column_list(cols) == "id, path, width"
        assert _format_image_column_list(cols, alias="i") == "i.id, i.path, i.width"


# ===========================================================================
# Base query / group-by / order clause / cursor support
# ===========================================================================


class TestBaseQueryShapes:
    def test_plain_sort_is_simple_select_from_images(self):
        assert _build_base_query("newest", "COLS") == "SELECT COLS FROM images i"

    def test_unknown_sort_falls_back_to_plain_newest_shape(self):
        assert _build_base_query("not-a-sort", "COLS") == "SELECT COLS FROM images i"

    def test_tag_count_uses_left_join_count_distinct(self):
        q = _build_base_query("tag_count", "COLS")
        assert q.startswith("SELECT COLS")
        assert "LEFT JOIN tags _agg_tag ON _agg_tag.image_id = i.id" in q
        assert "COUNT(DISTINCT _agg_tag.id) as tag_count" in q

    def test_character_count_uses_category_case_left_join(self):
        q = _build_base_query("character_count", "COLS")
        assert "LEFT JOIN tags _agg_char ON _agg_char.image_id = i.id" in q
        assert "LOWER(TRIM(COALESCE(_agg_char.category, ''))) = 'character'" in q
        assert "_agg_char.category IS NULL" in q
        assert "_agg_char.tag LIKE '%(%)'" in q
        assert "_agg_char.tag NOT LIKE '%()'" in q
        assert "as char_count" in q

    def test_character_count_uses_category_and_legacy_suffix_semantics(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE images (id INTEGER PRIMARY KEY);
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY,
                image_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                category TEXT
            );
            INSERT INTO images (id) VALUES (1), (2), (3);
            INSERT INTO tags (id, image_id, tag, category) VALUES
                (1, 1, 'alice', ' Character '),
                (2, 1, 'character_counter_request', 'general'),
                (3, 2, 'hatsune_miku_(vocaloid)', NULL),
                (4, 2, 'character_counter_request', NULL),
                (5, 2, 'miku(vocaloid)', NULL),
                (6, 3, 'character_counter_request', 'general');
            """
        )

        query = (
            _build_base_query("character_count", "i.id")
            + _group_by_clause("character_count")
            + " ORDER BY "
            + _get_order_clause("character_count")
        )
        rows = conn.execute(query).fetchall()

        assert rows == [(2, 2), (1, 1), (3, 0)]

    def test_rating_sort_reads_denormalized_ai_rating_with_distinct(self):
        q = _build_base_query("rating", "COLS")
        assert q.startswith("SELECT DISTINCT COLS")
        assert "CASE i.ai_rating" in q
        assert "as rating_order" in q
        assert q.rstrip().endswith("FROM images i")

    def test_group_by_only_for_aggregate_sorts(self):
        assert _group_by_clause("tag_count") == " GROUP BY i.id"
        assert _group_by_clause("character_count_asc") == " GROUP BY i.id"
        # rating uses SELECT DISTINCT, not GROUP BY.
        assert _group_by_clause("rating") == ""
        assert _group_by_clause("newest") == ""


class TestOrderClauseAndCursorSupport:
    def test_known_order_clauses(self):
        assert _get_order_clause("newest") == _DEFAULT_ORDER_CLAUSE
        assert (
            _get_order_clause("oldest")
            == "COALESCE(i.library_order_time, i.created_at) ASC, i.id ASC"
        )
        assert _get_order_clause("name_asc") == "i.filename ASC, i.id ASC"
        assert (
            _get_order_clause("random")
            == "((i.id * 1103515245 + 12345) & 2147483647) ASC, i.id ASC"
        )
        assert (
            _get_order_clause("aesthetic")
            == "COALESCE(i.aesthetic_score, 0) DESC, i.id DESC"
        )

    def test_unknown_sort_key_returns_default_clause(self):
        assert _get_order_clause("bogus") == _DEFAULT_ORDER_CLAUSE

    def test_every_valid_sort_has_a_dedicated_order_clause(self):
        # No valid sort silently collapses onto the default except "newest".
        for sort_by in VALID_SORT_OPTIONS:
            clause = _get_order_clause(sort_by)
            assert clause
            if sort_by != "newest":
                assert clause != _DEFAULT_ORDER_CLAUSE, sort_by

    def test_cursor_support_is_limited_to_newest_and_oldest(self):
        assert _supports_cursor_sort("newest") is True
        assert _supports_cursor_sort("oldest") is True
        for sort_by in (
            "name_asc",
            "random",
            "tag_count",
            "rating",
            "aesthetic",
            "bogus",
        ):
            assert _supports_cursor_sort(sort_by) is False


# ===========================================================================
# (conditions, params) filter contracts — clause text + param order + identity
# ===========================================================================


class TestFilterMutationIdentity:
    def test_condition_helpers_mutate_in_place_and_return_the_same_lists(self):
        """Callers rely on the passed conditions/params being extended in place;
        the helpers must return those very objects, not fresh copies."""
        conditions, params = [], []
        out_c, out_p = _apply_generator_filter(conditions, params, ["webui"])
        assert out_c is conditions
        assert out_p is params

    def test_falsy_input_is_a_noop_leaving_lists_untouched(self):
        conditions, params = ["seed"], [1]
        out_c, out_p = _apply_generator_filter(conditions, params, None)
        assert out_c == ["seed"] and out_p == [1]
        out_c2, out_p2 = _apply_generator_filter(conditions, params, [])
        assert out_c2 == ["seed"] and out_p2 == [1]


class TestListFilterClauses:
    def test_generator_filter_in_clause_and_param_order(self):
        c, p = _apply_generator_filter([], [], ["webui", "comfyui"])
        assert c == ["i.generator IN (?,?)"]
        assert p == ["webui", "comfyui"]

    def test_rating_filter_all_selected_is_noop_subset_adds_clause(self):
        noop_c, noop_p = _apply_rating_filter(
            [], [], ["general", "sensitive", "questionable", "explicit"]
        )
        assert noop_c == [] and noop_p == []

        c, p = _apply_rating_filter([], [], ["explicit"])
        assert len(c) == 1
        assert "i.ai_rating IN (?)" in c[0]
        assert "i.tagged_at IS NULL" in c[0]
        assert p == ["explicit"]

    def test_checkpoint_filter_normalizes_and_dedups_by_identity(self):
        c, p = _apply_checkpoint_filter(
            [], [], ["Model.safetensors", "model.ckpt", "Other.safetensors"]
        )
        assert c == ["i.checkpoint_normalized COLLATE NOCASE IN (?,?)"]
        # "model.ckpt" collapses onto "Model" (same identity key) and is dropped.
        assert p == ["Model", "Other"]

    def test_lora_filter_ors_exists_probes_with_normalized_names(self):
        c, p = _apply_lora_filter([], [], ["MyLora.safetensors", "Other:0.8"])
        assert len(c) == 1
        assert c[0].count("EXISTS (SELECT 1 FROM image_loras il") == 2
        assert " OR " in c[0]
        assert p == ["mylora", "other"]

    def test_search_filter_binds_filename_then_checkpoint_then_tokens(self):
        c, p = _apply_search_filter([], [], "dragon")
        assert len(c) == 1
        assert c[0].startswith(
            "(LOWER(i.filename) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(i.checkpoint_normalized, '')) LIKE ? ESCAPE '\\'"
        )
        assert c[0].endswith(")")
        assert "image_prompt_tokens" in c[0]
        # Param order is load-bearing: filename pattern, checkpoint pattern, then
        # one token pattern per extracted prompt token.
        assert p[0] == "%dragon%"
        assert p[1] == "%dragon%"
        assert len(p) >= 3 and all(x == "%dragon%" for x in p)


class TestRangeAndScalarFilters:
    def test_dimension_filter_param_order_and_aspect_clause(self):
        c, p = _apply_dimension_filters([], [], 100, 2000, 200, 3000, "landscape")
        assert c[:4] == [
            "i.width >= ?",
            "i.width <= ?",
            "i.height >= ?",
            "i.height <= ?",
        ]
        assert p == [100, 2000, 200, 3000]
        assert "CAST(i.width AS FLOAT) / i.height > 1.1" in c[4]

    def test_aesthetic_unscored_takes_precedence_over_range(self):
        c, p = _apply_aesthetic_filter([], [], 5.0, 9.0, aesthetic_unscored=True)
        assert c == ["i.aesthetic_score IS NULL"]
        assert p == []  # range is ignored entirely under unscored

        c2, p2 = _apply_aesthetic_filter([], [], 5.0, None)
        assert c2 == ["i.aesthetic_score IS NOT NULL AND i.aesthetic_score >= ?"]
        assert p2 == [5.0]

    def test_saturation_range_guards_null_and_casts_float(self):
        c, p = _apply_saturation_filter([], [], 0.25, 0.75)
        assert c == [
            "i.color_saturation IS NOT NULL AND i.color_saturation >= ?",
            "i.color_saturation IS NOT NULL AND i.color_saturation <= ?",
        ]
        assert p == [0.25, 0.75]
        assert all(isinstance(x, float) for x in p)

    def test_seed_filter_binds_seed_twice_and_guards_non_int(self):
        c, p = _apply_seed_filter([], [], 4242)
        assert len(c) == 1
        assert "json_valid(i.metadata_json)" in c[0]
        assert "generation_params.seed" in c[0]
        assert "generation_params.noise_seed" in c[0]
        assert p == [4242, 4242]

        assert _apply_seed_filter([], [], None) == ([], [])
        assert _apply_seed_filter([], [], "not-an-int") == ([], [])

    def test_user_rating_threshold_only_binds_when_positive(self):
        c, p = _apply_user_rating_filter([], [], 3)
        assert c == ["i.user_rating >= ?"] and p == [3]
        assert _apply_user_rating_filter([], [], 0) == ([], [])
        assert _apply_user_rating_filter([], [], None) == ([], [])

    def test_no_caption_filter_is_param_free_column_predicate(self):
        c, p = _apply_no_caption_filter([], [], True)
        assert c == [
            "(COALESCE(i.ai_caption, '') = '' AND COALESCE(i.nl_caption, '') = '')"
        ]
        assert p == []
        assert _apply_no_caption_filter([], [], None) == ([], [])

    def test_metadata_presence_filter_true_false_none(self):
        true_c, true_p = _apply_metadata_presence_filter([], [], True)
        false_c, _ = _apply_metadata_presence_filter([], [], False)
        assert true_p == []
        assert false_c[0] == "NOT " + true_c[0]
        assert _apply_metadata_presence_filter([], [], None) == ([], [])


class TestColorFilters:
    def test_color_filter_accepts_valid_values_and_ignores_junk(self):
        c, p = _apply_color_filter(
            [],
            [],
            brightness_min=0.1,
            brightness_max=0.9,
            color_temperature="WARM",
            brightness_distribution="not-a-shape",
        )
        assert "i.avg_brightness IS NOT NULL AND i.avg_brightness >= ?" in c
        assert "i.avg_brightness IS NOT NULL AND i.avg_brightness <= ?" in c
        assert "i.color_temperature = ?" in c
        # invalid distribution contributes no clause / no param
        assert "i.brightness_distribution = ?" not in c
        assert p == [0.1, 0.9, "warm"]

    def test_color_hues_filter_lazy_imports_vocab_and_wraps_tags(self):
        # include: OR of comma-wrapped LIKE patterns; exclude: per-hue NOT LIKE
        # with a NULL-survives guard. Values outside DOMINANT_COLOR_TAGS ignored.
        c, p = _apply_color_hues_filter(
            [], [], color_hues=["red", "notacolor"], exclude_color_hues=["blue"]
        )
        assert c[0] == "(i.dominant_color_tags LIKE ?)"
        assert (
            c[1]
            == "(i.dominant_color_tags IS NULL OR i.dominant_color_tags NOT LIKE ?)"
        )
        assert p == ["%,red,%", "%,blue,%"]

    def test_exclude_colors_keeps_null_rows_and_ignores_invalid(self):
        c, p = _apply_exclude_colors_filter([], [], ["warm", "chartreuse"])
        assert c == ["(i.color_temperature IS NULL OR i.color_temperature NOT IN (?))"]
        assert p == ["warm"]


class TestExcludeFilters:
    # Both exclude-prompt pins gained a sidecar_caption arm: migration 042 moved
    # .txt text into that column and _apply_prompt_terms_filter matches it, so
    # the negation has to reject it or the same row satisfies "contains X" and
    # "excludes X" at once. Behavioural coverage of the resulting file set lives
    # in test_prompt_filter_sidecar_caption.py; these two stay because they pin
    # the whole-token boundary, which SQL alone enforces on the exclude side.
    def test_exclude_prompts_exact_is_token_level_not_like(self):
        c, p = _apply_exclude_prompts_filter([], [], ["cat"], PROMPT_MATCH_MODE_EXACT)
        assert len(c) == 1
        # The prompt arm still compares whole index tokens with `=`, never LIKE.
        assert (
            "NOT EXISTS (SELECT 1 FROM image_prompt_tokens ipt "
            "WHERE ipt.image_id = i.id AND ipt.token = ?)"
        ) in c[0]
        # The caption arm carries the same boundary by wrapping the comma-split
        # segments, so "cat" cannot reach "catgirl".
        assert "i.sidecar_caption" in c[0]
        assert p == ["cat", "%,cat,%"]

    def test_exclude_prompts_contains_uses_normalized_not_like(self):
        c, p = _apply_exclude_prompts_filter(
            [], [], ["cat"], PROMPT_MATCH_MODE_CONTAINS
        )
        assert c == [
            "(LOWER(REPLACE(COALESCE(i.prompt, ''), '_', ' ')) NOT LIKE ? ESCAPE '\\'"
            " AND LOWER(REPLACE(COALESCE(i.sidecar_caption, ''), '_', ' ')) "
            "NOT LIKE ? ESCAPE '\\')"
        ]
        assert p == ["%cat%", "%cat%"]

    def test_exclude_ratings_keeps_unrated_via_is_null_arm(self):
        c, p = _apply_exclude_ratings_filter([], [], ["Explicit"])
        assert c == ["(i.ai_rating IS NULL OR LOWER(i.ai_rating) NOT IN (?))"]
        assert p == ["explicit"]


class TestQueryMutatingFilters:
    def test_tag_filter_and_mode_appends_one_join_per_tag(self):
        q, p = _apply_tag_filter("SELECT x FROM images i", ["a", "b"], [], "and")
        assert q == (
            "SELECT x FROM images i"
            " INNER JOIN tags t0 ON i.id = t0.image_id AND t0.tag = ?"
            " INNER JOIN tags t1 ON i.id = t1.image_id AND t1.tag = ?"
        )
        assert p == ["a", "b"]

    def test_tag_filter_or_mode_uses_single_in_join(self):
        q, p = _apply_tag_filter("SELECT x FROM images i", ["a", "b"], [], "or")
        assert q == (
            "SELECT x FROM images i"
            " INNER JOIN tags _tor ON i.id = _tor.image_id AND _tor.tag IN (?,?)"
        )
        assert p == ["a", "b"]

    def test_artist_filter_injects_distinct_and_join(self):
        q, c, p = _apply_artist_filter("SELECT x FROM images i", [], [], "rembrandt")
        assert q == (
            "SELECT DISTINCT x FROM images i"
            " INNER JOIN artist_predictions ap ON i.id = ap.image_id"
        )
        assert c == ["ap.artist = ?"]
        assert p == ["rembrandt"]


class TestCollectionAndIdFilters:
    def test_collection_filter_builds_union_subquery_with_two_params(self):
        c, p = _apply_collection_filter([], [], 5)
        assert c == [
            "i.id IN ("
            "SELECT ci.source_image_id FROM collection_items ci WHERE ci.collection_id = ? "
            "UNION "
            f"SELECT favorite_images.id FROM ({_favorite_image_ids_query()}) favorite_images "
            "JOIN collections c ON c.id = ? AND c.slug = 'favorites'"
            ")"
        ]
        assert p == [5, 5]

    def test_collection_filter_noop_for_none_and_non_positive(self):
        assert _apply_collection_filter([], [], None) == ([], [])
        assert _apply_collection_filter([], [], 0) == ([], [])
        assert _apply_collection_filter([], [], -3) == ([], [])

    def test_include_ids_empty_but_present_forces_impossible_match(self):
        # An explicit empty include list must match nothing (0 = 1), not silently
        # widen to "all rows"; None stays a no-op.
        c_empty, _ = _apply_image_ids_filter([], [], [])
        assert c_empty == ["0 = 1"]
        assert _apply_image_ids_filter([], [], None) == ([], [])

    def test_include_ids_dedup_and_drop_non_positive(self):
        c, p = _apply_image_ids_filter([], [], [3, 3, -1, 0, 5, "x"])
        assert c == ["i.id IN (?,?)"]
        assert p == [3, 5]

    def test_exclude_ids_empty_is_noop_and_uses_not_in(self):
        assert _apply_excluded_image_ids_filter([], [], []) == ([], [])
        c, p = _apply_excluded_image_ids_filter([], [], [7, 9])
        assert c == ["i.id NOT IN (?,?)"]
        assert p == [7, 9]

    def test_readable_filter_default_excludes_unreadable_override_is_noop(self):
        c, p = _apply_readable_filter([], [])
        assert c == ["COALESCE(i.is_readable, 1) = 1"]
        assert _apply_readable_filter([], [], include_unreadable=True) == ([], [])


class TestDateFilterShape:
    def test_date_filter_half_open_upper_bound_and_inplace_identity(self):
        conditions, params = [], []
        out_c, out_p = _apply_date_filter(
            conditions, params, "2026-01-10", "2026-01-20"
        )
        assert out_c is conditions and out_p is params
        assert conditions == [
            "COALESCE(i.library_order_time, i.created_at) >= ?",
            "COALESCE(i.library_order_time, i.created_at) < date(?, '+1 day')",
        ]
        assert params == ["2026-01-10", "2026-01-20"]


# ===========================================================================
# Pure post-filter (exact prompt/LORA) matchers
# ===========================================================================


class TestPostFilterMatchers:
    def test_exact_prompt_match_is_whole_token_not_substring(self):
        # exact mode: "cat" must NOT match the prompt "catgirl" (token-level)...
        assert not _matches_exact_post_filters(
            "catgirl", None, ["cat"], [], prompt_match_mode=PROMPT_MATCH_MODE_EXACT
        )
        # ...but contains mode treats it as a substring and DOES match.
        assert _matches_exact_post_filters(
            "catgirl", None, ["cat"], [], prompt_match_mode=PROMPT_MATCH_MODE_CONTAINS
        )
        # exact match of the exact token passes.
        assert _matches_exact_post_filters(
            "cat, dog", None, ["cat"], [], prompt_match_mode=PROMPT_MATCH_MODE_EXACT
        )

    def test_lora_match_requires_any_named_lora_present(self):
        assert _matches_exact_post_filters(None, '["mylora"]', [], ["mylora"])
        assert not _matches_exact_post_filters(None, '["mylora"]', [], ["other"])

    def test_post_filter_results_passthrough_slices_when_no_filters(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert _post_filter_results(rows, None, None, 1, 1) == [{"id": 2}]
        # limit 0 means "to the end" from the offset.
        assert _post_filter_results(rows, None, None, 1, 0) == [{"id": 2}, {"id": 3}]

    def test_post_filter_results_applies_exact_prompt_matching(self):
        rows = [
            {"id": 1, "prompt": "cat", "loras": None},
            {"id": 2, "prompt": "dog", "loras": None},
        ]
        out = _post_filter_results(rows, ["cat"], None, 0, 0)
        assert [r["id"] for r in out] == [1]

    def test_fetch_post_filtered_page_rejects_negative_limit(self):
        class _FakeConn:
            def cursor(self):
                return object()

        with pytest.raises(ValueError, match="limit must be >= 0"):
            _fetch_post_filtered_page(
                _FakeConn(), "SELECT 1", [], "i.id", None, None, limit=-1
            )
