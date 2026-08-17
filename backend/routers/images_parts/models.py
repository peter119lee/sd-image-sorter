"""Request/response models for the images router (decomposed from routers/images.py).

Verbatim slice of pre-split routers/images.py lines 40-281 (registration
position 0 (no routes) of 9).
Registers on the ONE shared ``router`` defined in routers/images.py. Import
routers.images (the facade), NOT this module: the facade's import sequence
IS the route registration order (single-segment static GET routes must
register before ``GET /api/images/{image_id}`` or they 422-shadow).
"""
from typing import Optional, Any, List

from pydantic import BaseModel, Field, model_validator

from routers.images import PROMPT_MATCH_MODE_EXACT, VALID_PROMPT_MATCH_MODES


class DeleteSelectedImagesRequest(BaseModel):
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=5_000_000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    confirm_delete_files: bool = False
    # Debt-22 opt-in: when true the sync endpoint instead starts a durable-id
    # background job (BulkJobService) and returns the job envelope. Small
    # selections omit it and keep the unchanged synchronous behavior.
    background: bool = False

    @model_validator(mode="after")
    def require_ids_or_selection_token(self):
        if self.image_ids is None and not self.selection_token:
            raise ValueError("Either image_ids or selection_token is required")
        if self.image_ids is not None and self.selection_token:
            raise ValueError("Provide either image_ids or selection_token, not both")
        return self


class RemoveSelectedImagesRequest(BaseModel):
    # Per-image work is sequential; only the request payload memory matters.
    # Internal SQLite IN(...) lookups already chunk at 500. 5M covers any
    # realistic personal library; the previous 50k ceiling was rejecting
    # real users with larger collections.
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=5_000_000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    # Debt-22 opt-in: see DeleteSelectedImagesRequest.background.
    background: bool = False

    @model_validator(mode="after")
    def require_ids_or_selection_token(self):
        if self.image_ids is None and not self.selection_token:
            raise ValueError("Either image_ids or selection_token is required")
        if self.image_ids is not None and self.selection_token:
            raise ValueError("Provide either image_ids or selection_token, not both")
        return self


class ReconnectMissingFilesRequest(BaseModel):
    search_folder: str = Field(..., min_length=1, max_length=4096)
    recursive: bool = True
    verify_uncertain: bool = True


class RepairConfirmRequest(BaseModel):
    """Body for POST /api/images/repair-confirm (Roadmap-C missing-file repair)."""
    review_id: int = Field(..., ge=1)
    action: str = Field(..., pattern="^(pick|merge|skip)$")
    # Required for pick/merge; ignored for skip. Validated against the review's
    # candidate ids in the service layer.
    chosen_image_id: Optional[int] = Field(default=None, ge=1)


class ExportSelectionRequest(BaseModel):
    # Same rationale as RemoveSelectedImagesRequest: sequential per-image
    # work + chunked SQL means the ceiling only caps payload memory.
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=5_000_000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=10000)

    @model_validator(mode="after")
    def require_ids_or_selection_token(self):
        if self.image_ids is None and not self.selection_token:
            raise ValueError("Either image_ids or selection_token is required")
        if self.image_ids is not None and self.selection_token:
            raise ValueError("Provide either image_ids or selection_token, not both")
        return self


class SelectionIdsRequest(BaseModel):
    generators: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    tagMode: str = Field(default="and", pattern="^(and|or)$")
    ratings: List[str] = Field(default_factory=list)
    checkpoints: List[str] = Field(default_factory=list)
    loras: List[str] = Field(default_factory=list)
    prompts: List[str] = Field(default_factory=list)
    promptMatchMode: str = PROMPT_MATCH_MODE_EXACT
    artist: Optional[str] = None
    search: str = ""
    folder: Optional[str] = Field(default=None, max_length=4096)
    hasMetadata: Optional[bool] = Field(default=None)
    sortBy: str = "newest"
    minWidth: Optional[int] = Field(default=None, ge=1, le=100000)
    maxWidth: Optional[int] = Field(default=None, ge=1, le=100000)
    minHeight: Optional[int] = Field(default=None, ge=1, le=100000)
    maxHeight: Optional[int] = Field(default=None, ge=1, le=100000)
    aspectRatio: Optional[str] = None
    minAesthetic: Optional[float] = Field(default=None, ge=0, le=10)
    maxAesthetic: Optional[float] = Field(default=None, ge=0, le=10)
    minUserRating: Optional[int] = Field(default=None, ge=0, le=5)
    brightnessMin: Optional[float] = Field(default=None, ge=0, le=255)
    brightnessMax: Optional[float] = Field(default=None, ge=0, le=255)
    colorTemperature: Optional[str] = Field(default=None, pattern="^(warm|cool|neutral)$")
    brightnessDistribution: Optional[str] = Field(
        default=None,
        pattern="^(left_heavy|right_heavy|middle_heavy|edge_heavy|balanced)$",
    )
    # v3.2.2 per-item exclude filters
    excludeTags: List[str] = Field(default_factory=list)
    excludeGenerators: List[str] = Field(default_factory=list)
    excludeRatings: List[str] = Field(default_factory=list)
    excludeCheckpoints: List[str] = Field(default_factory=list)
    excludeLoras: List[str] = Field(default_factory=list)
    excludePrompts: List[str] = Field(default_factory=list)
    excludeColors: List[str] = Field(default_factory=list)
    colorHues: List[str] = Field(default_factory=list)  # v3.5.0 dominant-hue include
    excludeColorHues: List[str] = Field(default_factory=list)  # v3.5.0 dominant-hue exclude
    collectionId: Optional[int] = Field(default=None, ge=1)
    scope: Optional[str] = Field(default=None, pattern="^(current_session|library)$")
    # Aurora Phase 3 gallery filters (compose with selection tokens)
    noCaption: Optional[bool] = Field(default=None)
    aestheticUnscored: Optional[bool] = Field(default=None)
    minSaturation: Optional[float] = Field(default=None, ge=0, le=255)
    maxSaturation: Optional[float] = Field(default=None, ge=0, le=255)
    seed: Optional[int] = Field(default=None)
    # File-time day range, YYYY-MM-DD inclusive (timeline-eval memo §4)
    dateFrom: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def validate_prompt_match_mode(self):
        normalized = str(self.promptMatchMode or PROMPT_MATCH_MODE_EXACT).strip().lower()
        if normalized not in VALID_PROMPT_MATCH_MODES:
            raise ValueError("promptMatchMode must be exact or contains")
        self.promptMatchMode = normalized
        self.tagMode = "or" if str(self.tagMode or "and").strip().lower() == "or" else "and"
        return self


class SelectionTokenRequest(SelectionIdsRequest):
    chunkSize: int = Field(default=2000, ge=1, le=10000)
    excludedImageIds: List[int] = Field(default_factory=list, max_length=10000)


class SelectionIdsResponse(BaseModel):
    image_ids: List[int] = Field(default_factory=list)
    total: int = 0


class SelectionTokenResponse(BaseModel):
    selection_token: str
    total_estimate: int = 0
    exact_total: bool = True
    chunk_size: int = 2000


class SelectionChunkResponse(BaseModel):
    image_ids: List[int] = Field(default_factory=list)
    offset: int = 0
    limit: int = 2000
    next_offset: Optional[int] = None
    has_more: bool = False


class FilteredImageCountResponse(BaseModel):
    """Response for POST /api/images/count (Smart Folders live counts)."""
    count: int = 0
    exact: bool = True


class ExportSelectionImage(BaseModel):
    id: int
    filename: str = ""
    generator: Optional[str] = None
    prompt: str = ""
    negative_prompt: str = ""
    checkpoint: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    aesthetic_score: Optional[float] = None
    ai_caption: str = ""
    generation_params: dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class ExportSelectionResponse(BaseModel):
    images: List[ExportSelectionImage] = Field(default_factory=list)
    missing_ids: List[int] = Field(default_factory=list)
    count: int = 0
    total: int = 0
    offset: int = 0
    limit: int = 0
    next_offset: Optional[int] = None
    has_more: bool = False
    source: str = "image_ids"
    exact_total: bool = True


class DeleteSelectedImagesResponse(BaseModel):
    deleted: int
    failed: List[dict[str, Any]]
    permanent_delete: bool = False
    trash_used: bool = True


class RemoveSelectedImagesResponse(BaseModel):
    removed: int
    missing_ids: List[int] = Field(default_factory=list)
    permanent_delete: bool = False


class BulkJobEnvelopeResponse(BaseModel):
    """Envelope returned when a bulk endpoint is invoked with ``background: true``.

    The client polls GET /api/bulk-jobs/{id} for progress and the final
    ``result``. This is a distinct response shape from the synchronous
    delete/remove responses, hence the Union response models below (Debt-22).
    """
    id: str
    job_id: str
    kind: str
    status: str
    total: int = 0
    processed: int = 0
    error_count: int = 0
    error_samples: List[str] = Field(default_factory=list)
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    operation: Optional[str] = None


class SaveEditedMetadataRequest(BaseModel):
    source_path: str = Field(..., min_length=1, description="Source image path (must be non-empty)")
    output_path: str = Field(..., min_length=1, description="Output image path (must be non-empty)")
    format: str = Field(default="png", min_length=1, description="Output format. Empty strings are rejected to avoid silent fallthrough to whatever default the writer picks; the caller must explicitly choose png/webp/jpg.")
    quality: Optional[int] = Field(default=None, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    allow_overwrite: bool = False


class SaveEditedMetadataResponse(BaseModel):
    output_path: str
    format: str
    warnings: List[str] = Field(default_factory=list)


class OpenFolderRequest(BaseModel):
    image_id: Optional[int] = None

