"""Module constants shared by the services/image/ mixin family.

Moved verbatim from services/image_service.py (decomposition 2026-07).
services/image_service.py re-imports every name so each stays a facade module
attribute (tests read image_service.SELECTION_IDS_MAX_RESPONSE /
SELECTION_TOKEN_VERSION / VALID_SORT_OPTIONS). Mixin BODY reads resolve through the facade at call time
(_svc().NAME) so facade-attribute patches keep landing; only def-time
signature defaults (DEFAULT_PAGE_SIZE / PROMPT_MATCH_MODE_EXACT /
SELECTION_TOKEN_DEFAULT_CHUNK) import from here directly — binding
semantics identical because defaults freeze at def time either way.
"""

# Validation constants
DIMENSION_MIN = 1
DIMENSION_MAX = 100000
LIMIT_MAX = 1000
OFFSET_MAX = 10000000
SEARCH_MAX_LENGTH = 1000
DEFAULT_PAGE_SIZE = 100
SELECTION_IDS_FETCH_CHUNK = 2000
SELECTION_IDS_MAX_RESPONSE = 100000
SELECTION_TOKEN_DEFAULT_CHUNK = 2000
SELECTION_TOKEN_MAX_CHUNK = 10000
SELECTION_TOKEN_MAX_EXCLUDED_IDS = 10000
SELECTION_TOKEN_VERSION = 2
# v3.3.2 Phase-1: chunk size for the background delete-to-trash job's batched
# DB reads (matches the literal 500 the synchronous delete path already used and
# SortingService.BATCH_MOVE_FETCH_CHUNK in spirit).
DELETE_FETCH_CHUNK = 500
PROMPT_MATCH_MODE_EXACT = "exact"
PROMPT_MATCH_MODE_CONTAINS = "contains"
VALID_PROMPT_MATCH_MODES = {PROMPT_MATCH_MODE_EXACT, PROMPT_MATCH_MODE_CONTAINS}
VALID_COLOR_TEMPERATURES = {"warm", "cool", "neutral"}
VALID_BRIGHTNESS_DISTRIBUTIONS = {"left_heavy", "right_heavy", "middle_heavy", "edge_heavy", "balanced"}
SELECTION_TOKEN_RANDOM_SORT_ERROR = (
    "random sort cannot use the chunked selection token protocol; use selection-ids or a snapshot protocol"
)
RECONNECT_PROGRESS_EVERY_N_FILES = 100
RECONNECT_PROGRESS_MIN_INTERVAL_SECONDS = 0.5
RECONNECT_MTIME_TOLERANCE_NS = 2_000_000_000
# Roadmap-C missing-file repair review: cap persisted pending review rows per
# run so a pathological library (tens of thousands of same-name/size ambiguous
# files) can't bloat the table; resolved history is separately pruned to 500.
RECONNECT_REVIEW_MAX_PENDING_PER_RUN = 2000
RECONNECT_REVIEW_RESOLVED_HISTORY_KEEP = 500


# Valid sort options and aspect ratios
VALID_SORT_OPTIONS = [
    "newest", "oldest", "name_asc", "name_desc", "generator", "generator_desc",
    "prompt_length", "prompt_length_asc", "tag_count", "tag_count_asc",
    "rating", "rating_desc", "character_count", "character_count_asc",
    "aesthetic", "aesthetic_asc",
    # v3.3.2 user star rating (FF-2)
    "user_rating", "user_rating_asc",
    "random", "file_size", "file_size_asc",
    # v3.2.1 color sorts
    "brightness", "brightness_asc",
    "saturation", "saturation_asc",
    "brightness_skew", "brightness_skew_asc",
]
