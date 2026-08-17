"""Request models + persistence-store types for the VLM captioning router.

Decomposed from routers/vlm.py (2026-07): a verbatim slice of the pre-split
lines 34-70, 407-410, 435-461, 491-495, 639-645, 751-771, 1183-1186 and
1225-1228. Import routers.vlm
(the facade), NOT this module -- the facade re-imports every name here BY
REFERENCE so the FastAPI endpoint annotations, the pins suite and every
historical ``routers.vlm.<name>`` read resolve to the SAME class objects
(tests/test_vlm_router_pins.py TestImportSeamCensus).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, TypedDict

from pydantic import BaseModel, Field, model_validator


class _StoredVLMTagRow(TypedDict):
    tag: str
    confidence: Optional[float]
    source: Optional[str]
    category: Optional[str]


class _PersistedVLMTagRow(TypedDict):
    tag: str
    confidence: float
    source: Optional[str]
    category: Optional[str]


class _VLMImageUpdate(TypedDict):
    image_id: int
    tags: List[_PersistedVLMTagRow]
    ai_caption: Optional[str]
    nl_caption: Optional[str]


class _VLMPersistenceStore(Protocol):
    def get_image_tags(self, image_id: int) -> List[_StoredVLMTagRow]: ...

    def add_tags_batch(
        self,
        image_tags_list: List[_VLMImageUpdate],
        *,
        default_source: Optional[str],
        replace_scope: str,
    ) -> None: ...


class VLMResultPersistenceError(RuntimeError):
    """Raised when a generated VLM result cannot be saved atomically."""


class DetectProviderRequest(BaseModel):
    endpoint: str


class ProbeConcurrencyRequest(BaseModel):
    """Probe the configured VLM endpoint for the highest stable concurrent load.

    Uses the lightweight provider ``test_connection`` call (typically GET /models)
    fired N-at-a-time, ramping from 1 to ``max_level``. The last fully-successful
    level is the recommended ``concurrent_requests`` value.
    """

    max_level: int = Field(default=8, ge=1, le=16)
    apply: bool = False  # when True, write recommended value into vlm-settings.json


class SaveSettingsRequest(BaseModel):
    provider: Optional[str] = None
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    max_retries: Optional[int] = Field(default=None, ge=0, le=10)
    retry_delay_seconds: Optional[float] = Field(default=None, ge=0, le=60)
    timeout_seconds: Optional[float] = Field(default=None, ge=1, le=600)
    concurrent_requests: Optional[int] = Field(default=None, ge=1, le=16)
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    user_prompt_with_tags: Optional[str] = None
    include_tags_as_context: Optional[bool] = None
    max_image_size: Optional[int] = Field(default=None, ge=128, le=4096)
    nsfw_retry_prompt: Optional[str] = None
    output_format: Optional[str] = None
    caption_max_tokens: Optional[int] = Field(default=None, ge=64, le=8192)
    caption_temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    socks_proxy: Optional[str] = None
    use_vertex: Optional[bool] = None
    vertex_project: Optional[str] = None
    vertex_location: Optional[str] = None
    service_account_json: Optional[str] = None


class CaptionSingleRequest(BaseModel):
    image_id: int
    tags: Optional[List[str]] = None


@dataclass(frozen=True)
class _BatchImageSource:
    source_type: str
    total: int
    iter_chunks: Callable[[], Iterator[List[int]]]




class BatchCaptionRequest(BaseModel):
    image_ids: Optional[List[int]] = Field(default=None, max_length=1_000_000)
    selection_token: Optional[str] = Field(default=None, min_length=1)
    filters: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def require_one_image_source(self):
        source_count = sum([
            self.image_ids is not None,
            bool(self.selection_token),
            self.filters is not None,
        ])
        if source_count == 0:
            raise ValueError("Either image_ids, selection_token, or filters is required")
        if source_count > 1:
            raise ValueError("Provide only one of image_ids, selection_token, or filters")
        return self


class PullModelRequest(BaseModel):
    model: str




class DeleteModelRequest(BaseModel):
    model: str
