"""Request models and validation constants for sorting endpoints."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, TypedDict, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from constants import VALID_ASPECT_RATIOS


DIMENSION_MIN = 1
DIMENSION_MAX = 100000
PATH_MAX_LENGTH = 4096
FOLDER_KEY_MAX_LENGTH = 100
SEARCH_MAX_LENGTH = 1000
VALID_SORT_ACTIONS = ["move", "skip", "undo", "redo", "collect"]
# v3.3.2 Sort & Cull Workbench: the manual-sort session is becoming mode-aware.
# "slot" is the original WASD slot-sort and stays the default, so every existing
# caller and persisted session keeps identical behavior. New modes (e.g. the A/B
# "bracket" King-of-Hill) are added in later slices and registered here.
SORT_MODE_SLOT = "slot"
# v3.3.2 WB-S2: A/B "King-of-Hill" bracket mode. A champion stays on screen and
# each remaining candidate challenges it; after N-1 comparisons a single winner
# remains. Pure in-memory pointer logic -- no file moves (winner handling is a
# later slice). Reuses the session's current_index as the challenger pointer and
# adds champion_index.
SORT_MODE_BRACKET = "bracket"
# v3.3.2 FF-1: 留/汰 Keep-Reject rapid cull. One image at a time; keep or reject
# (or skip), with undo/redo. Non-destructive -- decisions are recorded in the
# session history and the frontend routes kept→Collection / rejected→opt-in
# target at finish (mirrors the bracket winner routing). Reuses current_index as
# the single cursor; no champion pointer, no file moves.
SORT_MODE_CULL = "cull"
SORT_MODE_DEFAULT = SORT_MODE_SLOT
VALID_SORT_MODES = [SORT_MODE_SLOT, SORT_MODE_BRACKET, SORT_MODE_CULL]
# Bracket actions: pick the champion (A keeps the crown), promote the challenger
# (B wins), skip (no preference -- champion stays), plus undo/redo.
VALID_BRACKET_ACTIONS = ["champion", "challenger", "skip", "undo", "redo"]
# Cull actions: keep (loved), reject (cut), skip (decide later), plus undo/redo.
VALID_CULL_ACTIONS = ["keep", "reject", "skip", "undo", "redo"]
VALID_FILE_OPERATIONS = ["move", "copy"]
VALID_PROMPT_MATCH_MODES = {"exact", "contains"}
SCAN_SOURCE_MANUAL = "manual"
SCAN_SOURCE_LIBRARY_AUTO_REFRESH = "library_auto_refresh"
SCAN_SOURCE_LIBRARY_RESCAN = "library_rescan"
VALID_SCAN_SOURCES = frozenset({
    SCAN_SOURCE_MANUAL,
    SCAN_SOURCE_LIBRARY_AUTO_REFRESH,
    SCAN_SOURCE_LIBRARY_RESCAN,
})
BACKGROUND_SCAN_SOURCES = frozenset({
    SCAN_SOURCE_LIBRARY_AUTO_REFRESH,
    SCAN_SOURCE_LIBRARY_RESCAN,
})
SCAN_ACTIVE_STATUSES = frozenset({"starting", "running", "cancelling"})
SCAN_TERMINAL_STATUSES = frozenset({"done", "error", "cancelled"})
ScanSource = Literal[
    "manual",
    "library_auto_refresh",
    "library_rescan",
]
ScanStatus = Literal[
    "idle",
    "starting",
    "running",
    "cancelling",
    "done",
    "cancelled",
    "error",
]


class ScanStartResult(TypedDict):
    """Stable identity returned by every accepted scan start."""

    status: Literal["started"]
    message: str
    run_id: int
    source: ScanSource


class ScanStartResponse(BaseModel):
    """Public scan-start response with a stable run identity."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["started"]
    message: str
    run_id: int = Field(gt=0)
    source: ScanSource


class ScanAcknowledgeResult(TypedDict):
    """Identity of a manual terminal result cleared by the client."""

    status: Literal["acknowledged"]
    run_id: int
    source: Literal["manual"]


class ScanAcknowledgeRequest(BaseModel):
    """Strict identity required to clear a manual terminal result."""

    model_config = ConfigDict(extra="ignore", strict=True)

    run_id: int = Field(gt=0)
    source: Literal["manual"]


class ScanAcknowledgeResponse(BaseModel):
    """Public confirmation that one manual terminal result was cleared."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["acknowledged"]
    run_id: int = Field(gt=0)
    source: Literal["manual"]


class ScanCancelResult(TypedDict):
    """Identity and state returned by a cooperative scan cancellation."""

    status: Literal["cancelling", "cancelled"]
    message: str
    run_id: int
    source: ScanSource


class ScanCancelRequest(BaseModel):
    """Strict identity required to cancel the currently observed scan."""

    model_config = ConfigDict(extra="ignore", strict=True)

    run_id: int = Field(gt=0)
    source: ScanSource


class ScanCancelResponse(BaseModel):
    """Public cancellation response bound to one scan identity."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["cancelling", "cancelled"]
    message: str
    run_id: int = Field(gt=0)
    source: ScanSource


class ScanRecentErrorResponse(BaseModel):
    """Bounded unreadable-file detail exposed by scan progress."""

    model_config = ConfigDict(extra="ignore", strict=True)

    filename: str
    error: str
    kind: str


class ScanProgressResponse(BaseModel):
    """Public scan-progress state with identity validation."""

    model_config = ConfigDict(extra="ignore", strict=True)

    run_id: int = Field(ge=0)
    source: Optional[ScanSource]
    status: ScanStatus
    step: str
    current: int
    processed: int
    total: int
    counted: int
    total_final: bool
    import_complete: bool
    errors: int
    new: int
    updated: int
    skipped_other_library: int = 0
    skipped_other_library_paths: List[str] = Field(default_factory=list)
    removed: int
    library_ready: bool
    quick_import: bool
    metadata_processed: int
    metadata_total: int
    metadata_total_final: bool
    metadata_pending: int
    # Prompt coverage for the scanned scope, filled in when the scan finishes.
    # ``None`` means "not measured yet" and must stay distinguishable from a
    # measured zero, so a scan that could not check coverage never reads as a
    # scan that found nothing missing.
    metadata_prompt_total: Optional[int] = None
    metadata_missing_prompt: Optional[int] = None
    # Recoverable shortfall: no prompt AND no sidecar caption. Distinct from
    # metadata_missing_prompt, which since migration 042 counts every downloaded
    # image whose text lives in a .txt beside it — those need no repair.
    metadata_missing_text: Optional[int] = None
    message: str
    current_item: Optional[str]
    started_at: Optional[float]
    updated_at: Optional[float]
    attention_required: bool
    attention_message: str
    stalled_seconds: int
    diagnostics_available: bool
    diagnostics_endpoint: str
    recent_errors: List[ScanRecentErrorResponse]

    @model_validator(mode="after")
    def validate_run_identity(self) -> "ScanProgressResponse":
        if self.status == "idle":
            if self.run_id != 0 or self.source is not None:
                raise ValueError("idle scan progress must use run_id=0 and source=null")
            return self
        if self.run_id <= 0 or self.source is None:
            raise ValueError("non-idle scan progress requires a positive run_id and source")
        return self


class LibraryAutoRefreshStartedResponse(BaseModel):
    """Accepted idle library refresh."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["started"]
    root: str
    scan: ScanStartResponse


class LibraryAutoRefreshExpectedSkipResponse(BaseModel):
    """Expected idle library refresh skip."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["skipped"]
    reason: Literal["scan_in_progress", "manual_completion_pending"]


class LibraryAutoRefreshFailedResponse(BaseModel):
    """Actionable idle library refresh start failure."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["skipped"]
    reason: Literal["scan_start_failed"]
    detail: str
    status_code: int


class LibraryAutoRefreshIdleResponse(BaseModel):
    """Idle library refresh with no enabled roots."""

    model_config = ConfigDict(extra="ignore", strict=True)

    status: Literal["idle"]
    reason: Literal["no_enabled_roots"]


LibraryAutoRefreshResponse = Union[
    LibraryAutoRefreshStartedResponse,
    LibraryAutoRefreshExpectedSkipResponse,
    LibraryAutoRefreshFailedResponse,
    LibraryAutoRefreshIdleResponse,
]


class ScanRequest(BaseModel):
    """Request model for folder scanning."""

    folder_path: str = Field(..., max_length=PATH_MAX_LENGTH)
    recursive: bool = True
    force_reparse: bool = False
    cleanup_missing: bool = False
    quick_import: bool = True

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path_length(cls, v: str) -> str:
        if len(v) > PATH_MAX_LENGTH:
            raise ValueError(f"folder_path must be at most {PATH_MAX_LENGTH} characters")
        return v


class ValidatePathRequest(BaseModel):
    """Request model for path validation."""

    path: str = Field(..., max_length=PATH_MAX_LENGTH)

    @field_validator("path")
    @classmethod
    def validate_path_length(cls, v: str) -> str:
        if len(v) > PATH_MAX_LENGTH:
            raise ValueError(f"path must be at most {PATH_MAX_LENGTH} characters")
        return v


class MoveRequest(BaseModel):
    """Request model for image move operations.

    v3.2.1: Accepts EITHER `image_ids` (explicit list) OR `selection_token`
    (filtered scope). The token form lets the UI pass "Select All Filtered"
    without first expanding tens of thousands of IDs client-side.
    """

    # Per-image work is sequential and the inner DB read uses
    # ``db.get_images_by_ids`` which already chunks IN(...) at 500. The
    # ceiling only caps request payload memory; 5M covers any realistic
    # personal library (the previous 50k ceiling rejected real users).
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=5_000_000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    destination_folder: str = Field(..., max_length=PATH_MAX_LENGTH)
    operation: str = Field(default="move")

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, v: str) -> str:
        if v not in VALID_FILE_OPERATIONS:
            raise ValueError(f"operation must be one of: {', '.join(VALID_FILE_OPERATIONS)}")
        return v

    @model_validator(mode="after")
    def require_ids_or_selection_token(self) -> "MoveRequest":
        if self.image_ids is None and not self.selection_token:
            raise ValueError("Either image_ids or selection_token is required")
        if self.image_ids is not None and self.selection_token:
            raise ValueError("Provide either image_ids or selection_token, not both")
        return self


class SortFilterRequest(BaseModel):
    """Shared filter model for bulk sort-style operations."""

    generators: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    tag_mode: str = Field(default="and")
    ratings: Optional[List[str]] = None
    checkpoints: Optional[List[str]] = None
    loras: Optional[List[str]] = None
    prompts: Optional[List[str]] = None
    prompt_match_mode: str = Field(default="exact")
    artist: Optional[str] = Field(default=None, max_length=500)
    search: Optional[str] = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    min_width: Optional[int] = Field(default=None, ge=DIMENSION_MIN, le=DIMENSION_MAX)
    max_width: Optional[int] = Field(default=None, ge=DIMENSION_MIN, le=DIMENSION_MAX)
    min_height: Optional[int] = Field(default=None, ge=DIMENSION_MIN, le=DIMENSION_MAX)
    max_height: Optional[int] = Field(default=None, ge=DIMENSION_MIN, le=DIMENSION_MAX)
    aspect_ratio: Optional[str] = None
    min_aesthetic: Optional[float] = Field(default=None, ge=0, le=10)
    max_aesthetic: Optional[float] = Field(default=None, ge=0, le=10)
    # v3.2.2 per-item exclude filters
    exclude_tags: Optional[List[str]] = Field(default=None)
    exclude_generators: Optional[List[str]] = Field(default=None)
    exclude_ratings: Optional[List[str]] = Field(default=None)
    exclude_checkpoints: Optional[List[str]] = Field(default=None)
    exclude_loras: Optional[List[str]] = Field(default=None)
    # v3.3.x gallery-scope parity (matches /api/images and the selection-token
    # filter contract). These were silently dropped on the sorting path before,
    # so "Copy from Gallery" widened the moved/sorted set beyond what the
    # gallery displayed (collection/folder/star-rating/exclude scopes lost).
    min_user_rating: Optional[int] = Field(default=None, ge=0, le=5)
    # v3.2.1 brightness/color filters
    brightness_min: Optional[float] = Field(default=None, ge=0, le=255)
    brightness_max: Optional[float] = Field(default=None, ge=0, le=255)
    color_temperature: Optional[str] = Field(default=None, max_length=16)
    brightness_distribution: Optional[str] = Field(default=None, max_length=32)
    # v3.3.0 exclude filters
    exclude_prompts: Optional[List[str]] = Field(default=None)
    exclude_colors: Optional[List[str]] = Field(default=None)
    color_hues: Optional[List[str]] = Field(default=None)  # v3.5.0 dominant-hue include
    exclude_color_hues: Optional[List[str]] = Field(default=None)  # v3.5.0 dominant-hue exclude
    # v3.3.1 collection scope
    collection_id: Optional[int] = Field(default=None, ge=1)
    scope: Optional[str] = Field(default=None, pattern="^(current_session|library)$")
    # v3.3.2 Library Navigation: recursive folder-subtree scope
    folder: Optional[str] = Field(default=None, max_length=PATH_MAX_LENGTH)
    # v3.3.2: "has SD generation parameters" scope (True/False; None = all)
    has_metadata: Optional[bool] = None

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio_field(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in VALID_ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio must be one of: {', '.join(VALID_ASPECT_RATIOS)}")
        return v

    @field_validator("prompt_match_mode")
    @classmethod
    def validate_prompt_match_mode(cls, v: str) -> str:
        normalized = str(v or "exact").strip().lower()
        if normalized not in VALID_PROMPT_MATCH_MODES:
            raise ValueError("prompt_match_mode must be exact or contains")
        return normalized

    @field_validator("tag_mode")
    @classmethod
    def validate_tag_mode(cls, v: str) -> str:
        normalized = str(v or "and").strip().lower()
        if normalized not in {"and", "or"}:
            raise ValueError("tag_mode must be and or or")
        return normalized

    @field_validator("max_width")
    @classmethod
    def validate_max_width(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None and info.data.get("min_width") is not None and v < info.data["min_width"]:
            raise ValueError("max_width cannot be less than min_width")
        return v

    @field_validator("max_height")
    @classmethod
    def validate_max_height(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None and info.data.get("min_height") is not None and v < info.data["min_height"]:
            raise ValueError("max_height cannot be less than min_height")
        return v

    @field_validator("max_aesthetic")
    @classmethod
    def validate_max_aesthetic(cls, v: Optional[float], info) -> Optional[float]:
        if v is not None and info.data.get("min_aesthetic") is not None and v < info.data["min_aesthetic"]:
            raise ValueError("max_aesthetic cannot be less than min_aesthetic")
        return v


VALID_BATCH_MOVE_SPLIT_BY = frozenset({"", "none", "generator", "checkpoint", "rating"})


class BatchMoveRequest(SortFilterRequest):
    """Request model for batch move operations."""

    destination_folder: str = Field(..., max_length=PATH_MAX_LENGTH)
    operation: str = Field(default="move")
    # B3-②: optional subfolder split under destination_folder.
    # "" / "none" = flat; generator | checkpoint | rating = one child folder per value.
    split_by: Optional[str] = Field(default=None, max_length=32)

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, v: str) -> str:
        if v not in VALID_FILE_OPERATIONS:
            raise ValueError(f"operation must be one of: {', '.join(VALID_FILE_OPERATIONS)}")
        return v

    @field_validator("split_by")
    @classmethod
    def validate_split_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = str(v).strip().lower()
        if normalized not in VALID_BATCH_MOVE_SPLIT_BY:
            raise ValueError(
                "split_by must be one of: none, generator, checkpoint, rating"
            )
        if normalized in {"", "none"}:
            return None
        return normalized

    @model_validator(mode="after")
    def require_at_least_one_filter(self) -> "BatchMoveRequest":
        """Refuse whole-library moves unless the caller supplies a real filter."""

        # SortFilterRequest fields that, if any of them is set, indicate the
        # caller actually intended a filter-scoped move.
        filter_fields = (
            self.generators,
            self.tags,
            self.ratings,
            self.checkpoints,
            self.loras,
            self.prompts,
            self.exclude_tags,
            self.exclude_generators,
            self.exclude_ratings,
            self.exclude_checkpoints,
            self.exclude_loras,
            self.exclude_prompts,
            self.exclude_colors,
            self.artist,
            self.search,
            self.min_width,
            self.max_width,
            self.min_height,
            self.max_height,
            self.aspect_ratio,
            self.min_aesthetic,
            self.max_aesthetic,
            # v3.3.x scope fields. min_user_rating is only a real filter when
            # >= 1 (0/None means "show all", mirroring the DB layer), so a
            # bare min_user_rating=0 must NOT unlock a whole-library move.
            self.min_user_rating if (self.min_user_rating or 0) > 0 else None,
            self.brightness_min,
            self.brightness_max,
            self.color_temperature,
            self.brightness_distribution,
            self.collection_id,
            self.folder,
            self.has_metadata,
        )
        # ``self.aspect_ratio`` is acceptable as a filter even though it has
        # only 3 valid values (square / landscape / portrait); ``ratings``
        # similarly. A list with at least one entry is the signal.
        any_set = False
        for value in filter_fields:
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)) and len(value) == 0:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            any_set = True
            break
        if not any_set:
            raise ValueError(
                "batch-move requires at least one filter (generators, tags, "
                "ratings, checkpoints, loras, prompts, artist, search, "
                "min/max dimensions, aspect_ratio, or aesthetic range). "
                "Refusing to move every image in the library by default. "
                "If you really want to move every image, use /api/move with "
                "an explicit selection_token covering the whole library."
            )
        return self


class ManualSortStartRequest(SortFilterRequest):
    """Request model for starting manual sort without query-string size limits."""

    folders: Optional[Dict[str, str]] = None
    # v3.3.1: optional per-slot collection ids ({key: collection_id|None}).
    collection_slots: Optional[Dict[str, Optional[int]]] = None
    operation_mode: str = Field(default="move", max_length=16)
    replace_existing: bool = False
    # v3.3.2 Workbench: culling/sorting mode ("slot" = WASD slot-sort, default).
    mode: str = Field(default=SORT_MODE_DEFAULT, max_length=16)


class FolderConfig(BaseModel):
    """Request model for folder configuration.

    v3.3.1: ``collection_slots`` is an optional per-slot collection mapping
    (``{key: collection_id|None}``). When omitted, existing folder behavior is
    untouched; when present, a slot with a collection id becomes
    "collection-typed" and its key adds the current image to that collection by
    reference instead of moving the file.
    """

    folders: Dict[str, str] = Field(...)
    collection_slots: Optional[Dict[str, Optional[int]]] = Field(default=None)

    @field_validator("folders")
    @classmethod
    def validate_folders(cls, v: Dict[str, str]) -> Dict[str, str]:
        for key, path in v.items():
            if len(key) > FOLDER_KEY_MAX_LENGTH:
                raise ValueError(f'Folder key "{key}" exceeds max length of {FOLDER_KEY_MAX_LENGTH}')
            if path and len(path) > PATH_MAX_LENGTH:
                raise ValueError(f'Path for key "{key}" exceeds max length of {PATH_MAX_LENGTH}')
        return v

    @field_validator("collection_slots")
    @classmethod
    def validate_collection_slots(cls, v: Optional[Dict[str, Optional[int]]]) -> Optional[Dict[str, Optional[int]]]:
        if v is None:
            return v
        for key in v:
            if len(key) > FOLDER_KEY_MAX_LENGTH:
                raise ValueError(f'Collection slot key "{key}" exceeds max length of {FOLDER_KEY_MAX_LENGTH}')
        return v


class BrowseFolderRequest(BaseModel):
    """Request model for folder browsing."""

    path: str = Field(default="", max_length=PATH_MAX_LENGTH)


__all__ = [
    "BatchMoveRequest",
    "BrowseFolderRequest",
    "DIMENSION_MAX",
    "DIMENSION_MIN",
    "FOLDER_KEY_MAX_LENGTH",
    "FolderConfig",
    "ManualSortStartRequest",
    "MoveRequest",
    "PATH_MAX_LENGTH",
    "SEARCH_MAX_LENGTH",
    "SORT_MODE_BRACKET",
    "SORT_MODE_CULL",
    "SORT_MODE_DEFAULT",
    "SORT_MODE_SLOT",
    "ScanRequest",
    "SortFilterRequest",
    "VALID_BRACKET_ACTIONS",
    "VALID_CULL_ACTIONS",
    "VALID_FILE_OPERATIONS",
    "VALID_PROMPT_MATCH_MODES",
    "VALID_SORT_ACTIONS",
    "VALID_SORT_MODES",
    "ValidatePathRequest",
]
