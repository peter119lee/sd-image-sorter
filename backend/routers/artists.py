"""
Artist Identification API Router for SD Image Sorter.

Endpoints for identifying artist/style in images using LSNet-style classification.
"""
import os
import logging
import threading
from pathlib import Path
from typing import List, Optional, Dict
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator

from artist_identifier import (
    get_artist_identifier,
    ARTIST_CONFIDENT_THRESHOLD,
    ARTIST_THRESHOLD_DEFAULT,
)
from config import ARTIST_HF_MODEL_ID, ARTIST_MODELSCOPE_MODEL_ID
from exceptions import ImageNotFoundError, OperationInProgressError, ServiceError, ValidationError
from model_health import get_model_health
from services.artist_service import ArtistService
from services.service_provider import ServiceProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artists", tags=["artists"])
# Hard ceiling for one /identify-batch request. Background-task model: each
# image is processed sequentially by the worker, so the server-side memory
# footprint of ``image_ids`` itself is the only thing we cap. Internal
# SQLite IN(...) lookups are already chunked at 500 ids in
# ``database.get_images_by_ids``, so larger ceilings do not change the
# database access pattern. 5,000,000 covers any realistic personal library
# (50k was still rejecting real users with bigger collections).
ARTIST_BATCH_IMAGE_LIMIT = 5_000_000

# Held across the is-running check and start_batch_progress in
# /identify-batch so two concurrent starts cannot both pass the check.
_batch_start_lock = threading.Lock()


# ============== Request/Response Models ==============

class ArtistModelConfig(BaseModel):
    model_source: str = Field("huggingface", pattern="^(huggingface|modelscope|local)$")
    model_path: Optional[str] = None
    # None -> use the ARTIST_USE_GPU config default. False forces CPU for users
    # whose GPU stack freezes under CUDA load (e.g. NVIDIA + Wayland).
    use_gpu: Optional[bool] = None

    @field_validator("model_path", mode="before")
    @classmethod
    def normalize_model_path(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @model_validator(mode="after")
    def validate_local_model_path(self):
        if self.model_source == "local":
            if not self.model_path:
                raise ValueError("Local model path is required when model_source is 'local'")

            normalized_path = Path(os.path.expanduser(self.model_path)).resolve()
            if not normalized_path.is_file():
                raise ValueError("Local model file not found")
            self.model_path = str(normalized_path)
        return self


class IdentifyRequest(ArtistModelConfig):
    image_id: int = Field(..., ge=1)
    threshold: float = Field(ARTIST_THRESHOLD_DEFAULT, ge=0.0, le=1.0)
    top_k: int = Field(5, ge=1, le=20)


class IdentifyBatchRequest(ArtistModelConfig):
    image_ids: List[int] = Field(..., min_length=1, max_length=ARTIST_BATCH_IMAGE_LIMIT)
    threshold: float = Field(ARTIST_THRESHOLD_DEFAULT, ge=0.0, le=1.0)
    top_k: int = Field(5, ge=1, le=20)


class IdentifyResponse(BaseModel):
    image_id: int
    # Only ever a real name when confidence_level == "high". Every other tier
    # reports "undefined" here and carries the guess in candidate_artist, so a
    # caller that reads only `artist` can never mistake a guess for a fact.
    artist: str
    confidence: float
    confidence_level: str = "none"
    candidate_artist: Optional[str] = None
    out_of_vocabulary_likely: bool = False
    vocabulary_size: int = 0
    advisory: str = ""
    top_predictions: List[dict]
    model_loaded: bool


class BatchProgress(BaseModel):
    running: bool
    total: int
    processed: int
    errors: int
    results: List[dict]
    step: Optional[str] = None
    message: Optional[str] = None
    current_item: Optional[str] = None
    started_at: Optional[float] = None
    updated_at: Optional[float] = None


class ModelInfo(BaseModel):
    name: str
    source: str
    available: bool
    artist_count: int


class StatsResponse(BaseModel):
    total_images: int
    identified_images: int
    undefined_count: int
    # undefined_count + low_confidence_count + confident_count == identified_images.
    # Only confident_count rows are presented as identified artists; the rest are
    # unconfirmed guesses (many of them written before the tiering existed).
    confident_count: int = 0
    low_confidence_count: int = 0
    confident_threshold: float = ARTIST_CONFIDENT_THRESHOLD
    artist_counts: dict
    artist_stats: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    low_confidence_artist_counts: Dict[str, int] = Field(default_factory=dict)


class VocabularyResponse(BaseModel):
    vocabulary_size: int
    vocabulary_loaded: bool
    known: Dict[str, bool] = Field(default_factory=dict)


class ArtistImageResponse(BaseModel):
    image_id: int
    filename: str
    artist: str
    confidence: float
    confidence_percent: float
    confidence_level: str = "none"
    path: str


class ArtistImageListResponse(BaseModel):
    artist: str
    total: int
    limit: int
    offset: int
    has_more: bool
    images: List[ArtistImageResponse]


def _configure_artist_service(service: ArtistService) -> None:
    if os.environ.get("SD_IMAGE_SORTER_E2E_FAKE_ARTIST") == "1":
        from services.artist_service import _e2e_artist_identifier_getter

        service.set_identifier_getter(_e2e_artist_identifier_getter)
    else:
        service.set_identifier_getter(get_artist_identifier)


_artist_service_provider = ServiceProvider(
    lambda: ArtistService(identifier_getter=get_artist_identifier),
    on_set=_configure_artist_service,
)


def get_artist_service() -> ArtistService:
    service = _artist_service_provider.get()
    _configure_artist_service(service)
    return service


set_artist_service = _artist_service_provider.set


# ============== Endpoints ==============

@router.post(
    "/identify",
    response_model=IdentifyResponse,
    summary="Identify artist of single image",
    description="""
Identify the artist or art style of a single image using the Kaloscope classifier.
Returns "undefined" when the result is not in the high confidence tier.
    """,
    responses={
        200: {
            "description": "Artist identification result",
            "content": {
                "application/json": {
                    "example": {
                        "image_id": 1,
                        "artist": "greg_rutkowski",
                        "confidence": 0.78,
                        "confidence_level": "high",
                        "candidate_artist": "greg_rutkowski",
                        "out_of_vocabulary_likely": False,
                        "vocabulary_size": 39261,
                        "advisory": "Confident match. ...",
                        "top_predictions": [
                            {"artist": "greg_rutkowski", "confidence": 0.78},
                            {"artist": "alphonse_mucha", "confidence": 0.45}
                        ],
                        "model_loaded": True
                    }
                }
            }
        },
        404: {"description": "Image not found or file missing"}
    }
)
async def identify_artist(
    request: IdentifyRequest,
    service: ArtistService = Depends(get_artist_service),
):
    """
    Identify the artist/style of a single image.

    Uses a classification model to predict the most likely artist
    based on visual style. Results are stored in the database.

    Args:
        request: IdentifyRequest with:
            - image_id: ID of image to analyze
            - threshold: Extra confidence floor; tightens only (cannot assert below 0.20)
            - top_k: Number of top predictions to return (default 5)

    Returns:
        IdentifyResponse with:
        - image_id: The analyzed image ID
        - artist: Predicted artist name or "undefined"
        - confidence: Confidence score for top prediction
        - top_predictions: List of top-k predictions with scores
        - model_loaded: Whether model was loaded successfully

    Raises:
        HTTPException 404: Image not found or file missing on disk
    """
    try:
        result = await run_in_threadpool(
            service.identify_image,
            image_id=request.image_id,
            threshold=request.threshold,
            top_k=request.top_k,
            model_source=request.model_source,
            model_path=request.model_path,
            use_gpu=request.use_gpu,
        )
    except ImageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ServiceError as exc:
        raise HTTPException(status_code=503, detail=exc.message)
    return IdentifyResponse(**result)


@router.post(
    "/identify-batch",
    summary="Batch artist identification",
    description="""
Start batch artist identification for multiple images.

Runs in background. Poll progress with `/api/artists/batch-progress` endpoint.
Results are stored in the artist_predictions table.
    """,
    responses={
        200: {
            "description": "Batch started",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Batch identification started",
                        "total": 100
                    }
                }
            }
        },
        409: {"description": "Batch already running"}
    }
)
async def identify_batch(
    request: IdentifyBatchRequest,
    background_tasks: BackgroundTasks,
    service: ArtistService = Depends(get_artist_service),
):
    """
    Start batch artist identification for multiple images.

    Processes images in the background. Poll progress via the
    /api/artists/batch-progress endpoint.

    Args:
        request: IdentifyBatchRequest with:
            - image_ids: List of image IDs to identify
            - threshold: Minimum confidence threshold
            - top_k: Number of predictions per image
        background_tasks: FastAPI background tasks

    Returns:
        Dict with:
        - message: Status message
        - total: Number of images to process

    Note:
        Only one batch can run at a time.
    """
    with _batch_start_lock:
        # Check-and-start under one lock so two concurrent requests cannot
        # both observe "not running" and start twice (same single-lock start
        # pattern as routers/colors.py start_analysis).
        if service.is_batch_running():
            raise HTTPException(status_code=409, detail="Batch identification already in progress")

        service.start_batch_progress(total=len(request.image_ids))

    # Start background task
    background_tasks.add_task(
        _run_batch_identification,
        request.image_ids,
        request.threshold,
        request.top_k,
        request.model_source,
        request.model_path,
        request.use_gpu,
    )

    return {
        "message": "Batch identification started",
        "total": len(request.image_ids),
    }


def _run_batch_identification(
    image_ids: List[int],
    threshold: float,
    top_k: int,
    model_source: str = "huggingface",
    model_path: Optional[str] = None,
    use_gpu: Optional[bool] = None,
):
    """Background task for batch identification with optimized batch operations."""
    service = get_artist_service()
    try:
        result = service.run_batch_identification(
            image_ids=image_ids,
            threshold=threshold,
            top_k=top_k,
            model_source=model_source,
            model_path=model_path,
            use_gpu=use_gpu,
            progress_callback=service.apply_batch_progress_update,
        )
        service.finish_batch_progress_done(result)
    except Exception as exc:
        logger.error("Artist batch job failed: %s", exc)
        service.finish_batch_progress_error(exc)


@router.post("/batch-cancel")
async def cancel_batch(service: ArtistService = Depends(get_artist_service)):
    """Cancel the running artist batch identification."""
    cancelled = service.request_cancel()
    return {"status": "cancelled" if cancelled else "not_running"}


@router.get("/batch-progress", response_model=BatchProgress)
async def get_batch_progress(service: ArtistService = Depends(get_artist_service)):
    """Get the current batch identification progress."""
    return BatchProgress(**service.get_batch_progress())


@router.get("/models", response_model=List[ModelInfo])
async def list_models():
    """List available artist identification models."""
    health = get_model_health()["artist"]
    models = []

    models.append(ModelInfo(
        name=f"{ARTIST_HF_MODEL_ID} (HuggingFace)",
        source="huggingface",
        available=health["available"],
        artist_count=0,  # class count is GET /api/artists/vocabulary, not this listing
    ))

    models.append(ModelInfo(
        name=(f"{ARTIST_MODELSCOPE_MODEL_ID} (ModelScope)" if ARTIST_MODELSCOPE_MODEL_ID else "Custom ModelScope mirror"),
        source="modelscope",
        available=bool(health["runtime_path"] and health["missing_dependencies"] == []),
        artist_count=0,
    ))

    # Check for local models
    local_models_dir = os.path.join(os.path.dirname(__file__), "..", "..", "models", "artist")
    if os.path.exists(local_models_dir):
        try:
            for f in os.listdir(local_models_dir):
                if f.endswith(('.onnx', '.pt', '.pth', '.bin', '.safetensors')):
                    models.append(ModelInfo(
                        name=f,
                        source="local",
                        available=True,
                        artist_count=0,
                    ))
        except PermissionError:
            logger.warning(f"Permission denied when accessing local models directory: {local_models_dir}")

    return models


@router.get("/diagnostics")
async def get_artist_diagnostics():
    """Return user-friendly runtime diagnostics for the artist feature.

    Reports the richer of two signals:
      * Static health from model_health (Kaloscope runtime + checkpoint present).
      * Live singleton state — if the identifier has already loaded a model
        (HF fallback / ModelScope fallback / local), treat the feature as
        available even when the Kaloscope files are missing.
    """
    if os.environ.get("SD_IMAGE_SORTER_E2E_FAKE_ARTIST") == "1":
        return {
            "status": "ok",
            "available": True,
            "message": "Artist E2E fixture runtime is ready.",
            "runtime_loaded": True,
            "runtime_backend": "e2e-fixture",
            "runtime_path": None,
            "checkpoint_path": None,
            "class_mapping_path": None,
            "missing_dependencies": [],
            "runtime_note": None,
        }

    artist = dict(get_model_health()["artist"])
    live_loaded = False
    live_backend: Optional[str] = None
    live_error: Optional[str] = None
    try:
        identifier = get_artist_identifier()
        live_loaded = bool(identifier._has_live_model())
        live_backend = getattr(identifier, "_backend", None)
        live_error = getattr(identifier, "_load_error", None)
    except Exception:  # identifier import/init must not crash diagnostics
        pass

    if live_loaded:
        artist["available"] = True
        artist["message"] = (
            f"Artist identifier is loaded ({live_backend or 'fallback'})."
        )
    artist["runtime_loaded"] = live_loaded
    artist["runtime_backend"] = live_backend
    if live_error and not artist.get("available"):
        artist["runtime_error"] = live_error
    return {
        "status": "ok",
        **artist,
    }


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get artist identification statistics."""
    service = get_artist_service()
    return StatsResponse(
        **service.get_stats(),
    )


@router.get("/images/{artist_name}", response_model=ArtistImageListResponse)
async def get_artist_images(
    artist_name: str,
    limit: int = Query(default=120, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: ArtistService = Depends(get_artist_service),
):
    """Return images identified for a specific artist ordered by confidence."""
    try:
        return ArtistImageListResponse(**service.get_artist_images(
            artist_name=artist_name,
            limit=limit,
            offset=offset,
        ))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


@router.get("/list")
async def list_artists(
    service: ArtistService = Depends(get_artist_service),
):
    """Get the list of known artists."""
    return service.list_artists()


@router.get(
    "/vocabulary",
    response_model=VocabularyResponse,
    summary="Check whether artists exist in the model's answer set",
    description="""
Report the size of the loaded model's artist vocabulary, and for each supplied
`name`, whether that artist is in it.

An artist that is not in the vocabulary can never be predicted, so every
identification run over their images will name somebody else. Checking here is
strictly more informative than reading the nearest wrong match.

`vocabulary_loaded` is false when no real label source is loaded yet (run an
identification first); in that case `known` is empty.
    """,
)
async def lookup_artist_vocabulary(
    name: List[str] = Query(default_factory=list),
    service: ArtistService = Depends(get_artist_service),
):
    """Answer "can this model ever name these artists?"."""
    return VocabularyResponse(**service.lookup_vocabulary(name))


@router.delete("/clear")
async def clear_predictions(
    service: ArtistService = Depends(get_artist_service),
):
    """Clear all artist predictions."""
    try:
        return service.clear_predictions()
    except OperationInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{exc.message}; wait for it to finish before clearing predictions"
            ),
        ) from exc
