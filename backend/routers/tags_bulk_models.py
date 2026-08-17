"""Request models + warning types for the bulk tag router.

Decomposed from routers/tags_bulk.py (2026-07): a verbatim slice of the
pre-split lines 38, 40-56, 129-267 and 308-311. Import routers.tags_bulk (the
facade), NOT this module -- the facade re-imports every name here BY
REFERENCE so the FastAPI endpoint annotations, the pins suite and every
historical ``routers.tags_bulk.<name>`` read resolve to the SAME objects
(tests/test_tags_bulk_pins.py TestPatchSurfaceCensus).

DORMANT BUG KEPT AS-IS: ``BulkTagFilterContract.tagMode`` carries a
field-level ``pattern="^(and|or)$"`` that 422-rejects ``"OR"`` before the
model_validator's ``.lower()`` can normalize it, while the sibling
``promptMatchMode`` (no field pattern) normalizes ``"CONTAINS"``; pinned by
test_tagmode_uppercase_rejected_while_prompt_mode_normalizes.
"""
from __future__ import annotations

from typing import List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

import database as db
from services.tag_export_service import (
    PROMPT_MATCH_MODE_CONTAINS,
    PROMPT_MATCH_MODE_EXACT,
)

BULK_TAG_MAX_IMAGE_IDS = 1_000_000
VALID_PROMPT_MATCH_MODES = {PROMPT_MATCH_MODE_EXACT, PROMPT_MATCH_MODE_CONTAINS}

BulkWarningCode = Literal[
    "undo_journal_truncated",
    "undo_journal_persistence_failed",
]


class BulkOperationWarning(TypedDict):
    code: BulkWarningCode
    message: str


class JournalApiResult(TypedDict):
    op_id: Optional[str]
    undo_available: bool
    warnings: List[BulkOperationWarning]


# ====================================================================
# Request models
# ====================================================================

class BulkTagFilterContract(BaseModel):
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
    excludedImageIds: List[int] = Field(default_factory=list, max_length=10000)
    excludeTags: List[str] = Field(default_factory=list)
    excludeGenerators: List[str] = Field(default_factory=list)
    excludeRatings: List[str] = Field(default_factory=list)
    excludeCheckpoints: List[str] = Field(default_factory=list)
    excludeLoras: List[str] = Field(default_factory=list)
    excludePrompts: List[str] = Field(default_factory=list)
    excludeColors: List[str] = Field(default_factory=list)
    colorHues: List[str] = Field(default_factory=list)  # v3.5.0
    excludeColorHues: List[str] = Field(default_factory=list)  # v3.5.0
    collectionId: Optional[int] = Field(default=None, ge=1)
    folder: Optional[str] = Field(default=None, max_length=4096)
    hasMetadata: Optional[bool] = None

    @model_validator(mode="after")
    def normalize_contract(self) -> "BulkTagFilterContract":
        prompt_mode = str(self.promptMatchMode or PROMPT_MATCH_MODE_EXACT).strip().lower()
        if prompt_mode not in VALID_PROMPT_MATCH_MODES:
            raise ValueError("promptMatchMode must be exact or contains")
        self.promptMatchMode = prompt_mode
        self.tagMode = "or" if str(self.tagMode or "and").strip().lower() == "or" else "and"

        sort_by = str(self.sortBy or "newest").strip()
        if sort_by not in db.VALID_SORT_OPTIONS:
            raise ValueError("Invalid sortBy value")
        if sort_by == "random":
            raise ValueError("random sort cannot use bulk tag filter scope")
        self.sortBy = sort_by

        if self.aspectRatio == "":
            self.aspectRatio = None
        return self


class BulkTagScopeRequest(BaseModel):
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=BULK_TAG_MAX_IMAGE_IDS)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    filters: Optional[BulkTagFilterContract] = None

    @field_validator("image_ids")
    @classmethod
    def dedupe_explicit_image_ids(cls, image_ids: Optional[List[int]]) -> Optional[List[int]]:
        if image_ids is None:
            return None
        return list(dict.fromkeys(image_ids))

    @model_validator(mode="after")
    def require_one_scope(self) -> "BulkTagScopeRequest":
        scope_count = sum([
            self.image_ids is not None,
            bool(self.selection_token),
            self.filters is not None,
        ])
        if scope_count == 0:
            raise ValueError("One of image_ids, selection_token, or filters is required")
        if scope_count > 1:
            raise ValueError("Provide only one of image_ids, selection_token, or filters")
        return self


class FindReplaceRequest(BulkTagScopeRequest):
    find: str
    replace: str
    case_sensitive: bool = False
    # QW-3: opt-in regex mode. ``find`` becomes a whole-tag fullmatch
    # pattern; ``replace`` may use backrefs (\\1). Literal whole-tag
    # equality stays the safe default.
    regex: bool = False
    dry_run: bool = False


class BulkAddRequest(BulkTagScopeRequest):
    tags: List[str] = Field(min_length=1, max_length=200)
    confidence: float = 0.85
    dry_run: bool = False

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: List[str]) -> List[str]:
        normalized: List[str] = []
        seen: set[str] = set()
        for raw_tag in tags:
            tag = raw_tag.strip()
            tag_key = tag.lower()
            if not tag or tag_key in seen:
                continue
            seen.add(tag_key)
            normalized.append(tag)
        if not normalized:
            raise ValueError("tags list cannot be empty")
        return normalized


class BulkRemoveRequest(BulkTagScopeRequest):
    tags: List[str] = Field(min_length=1, max_length=200)
    case_sensitive: bool = False
    dry_run: bool = False


class CleanupRequest(BulkTagScopeRequest):
    # v3.2.2: confidence is normalized to [0.0, 1.0]. Out-of-range
    # values (e.g. 1.5) used to silently mean "remove all tags",
    # which is destructive when dry_run=False. Negative values were
    # silent no-ops. Bound them so the caller has to be explicit.
    min_confidence: float = Field(default=0.20, ge=0.0, le=1.0)
    dedupe: bool = True
    dry_run: bool = False



class BulkUndoRequest(BaseModel):
    force: bool = False
