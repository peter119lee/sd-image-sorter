"""
Similarity search router.

Endpoints for image embedding, similarity search, and duplicate detection.

Refactored to use Service Layer pattern with dependency injection.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query, BackgroundTasks
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services.service_provider import ServiceProvider
from services.similarity_service import SimilarityService


router = APIRouter(prefix="/api/similarity", tags=["similarity"])

# Service instance - will be set via dependency injection
_similarity_service_provider = ServiceProvider(SimilarityService)


get_similarity_service = _similarity_service_provider.get
set_similarity_service = _similarity_service_provider.set


class TextSearchRequest(BaseModel):
    """Body schema for POST /api/similarity/search-text (semantic search)."""

    query: str = Field(..., min_length=1, max_length=512)
    limit: int = Field(default=100, ge=1, le=1000)
    # Cross-modal CLIP cosine runs ~0.2-0.35 for matches — default to pure
    # top-k ranking instead of the image-search 0.5 cutoff.
    threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    offset: int = Field(default=0, ge=0)
    collection_id: Optional[int] = Field(default=None, ge=1)
    scope: Optional[str] = Field(default=None, pattern="^(current_session|library)$")


class EmbedRequest(BaseModel):
    """Body schema for POST /api/similarity/embed.

    Without this schema FastAPI treated ``image_ids`` as a query parameter
    on a POST handler with a plain ``list`` annotation, so a JSON body of
    ``{"image_ids": [1, 2, 3]}`` was silently ignored and the worker
    embedded the entire library instead of the requested subset.
    """

    image_ids: Optional[List[int]] = Field(
        default=None,
        max_length=5_000_000,
        description=(
            "Optional list of image IDs to embed. If omitted (or null), all "
            "images without embeddings are processed."
        ),
    )


@router.post(
    "/embed",
    summary="Start image embedding",
    description="""
Start generating CLIP embeddings for images (runs in background).

Embeddings are used for similarity search and duplicate detection.
If `image_ids` is provided, only those images are embedded.
Otherwise, all images without embeddings are processed.

Poll `/api/similarity/progress` to track embedding status.
    """,
    responses={
        200: {
            "description": "Embedding started",
            "content": {
                "application/json": {
                    "example": {"status": "started", "message": "Embedding started in background"}
                }
            }
        }
    }
)
async def embed_images(
    background_tasks: BackgroundTasks,
    request: Optional[EmbedRequest] = None,
    service: SimilarityService = Depends(get_similarity_service),
):
    """Start embedding images in the background.

    Body is optional - call with no body to embed every image without an
    embedding. Pass ``{"image_ids": [...]}`` to scope the run to a subset.
    """
    image_ids = request.image_ids if request else None
    return await run_in_threadpool(service.embed_images, background_tasks, image_ids)


@router.post(
    "/search-text",
    summary="Semantic search by natural-language text",
    description="""
Rank library images against a natural-language description using the CLIP
text tower paired with the image-embedding model (same ViT-B/32 checkpoint,
same 512-dim space). Requires images to be embedded first (POST /embed).

Cross-modal similarity scores run far lower than image-image scores —
matching pairs typically land around 0.2-0.35 — so results are ranked
top-k by default (threshold 0.0).
    """,
)
async def search_text(
    request: TextSearchRequest,
    service: SimilarityService = Depends(get_similarity_service),
):
    """Semantic text-to-image search over stored CLIP embeddings."""
    if request.scope is None:
        return await service.search_by_text(
            request.query,
            request.limit,
            request.threshold,
            request.offset,
            request.collection_id,
        )
    return await service.search_by_text(
        request.query,
        request.limit,
        request.threshold,
        request.offset,
        request.collection_id,
        request.scope,
    )


@router.post("/cancel")
async def cancel_embedding(
    service: SimilarityService = Depends(get_similarity_service),
):
    """Cancel the running embedding batch."""
    cancelled = service.cancel_embedding()
    return {"status": "cancelled" if cancelled else "not_running"}


@router.get("/progress")
async def get_embed_progress(
    service: SimilarityService = Depends(get_similarity_service),
):
    """Get current embedding progress."""
    return service.get_embed_progress()


@router.get(
    "/search/{image_id}",
    summary="Find similar images",
    description="""
Find images similar to a specific image using CLIP embeddings.

Returns images ranked by cosine similarity score. Higher scores indicate
greater visual/semantic similarity.

**Threshold recommendations:**
- `0.95+`: Near-duplicates
- `0.80-0.95`: Very similar images
- `0.60-0.80`: Somewhat similar
- `0.50-0.60`: Loosely related
    """,
    responses={
        200: {
            "description": "Similar images found",
            "content": {
                "application/json": {
                    "example": {
                        "query_image_id": 1,
                        "results": [
                            {"id": 42, "similarity": 0.95, "filename": "similar_image.png"}
                        ],
                        "count": 1
                    }
                }
            }
        }
    }
)
async def search_similar(
    image_id: int,
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum results (1-1000)"),
    offset: int = Query(default=0, ge=0, description="Number of ranked results to skip for pagination"),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0, description="Minimum similarity threshold (0.0-1.0)"),
    collection_id: Optional[int] = Query(default=None, ge=1, description="Restrict similar results to this collection's members (v3.3.1)"),
    scope: Optional[str] = Query(default=None, pattern="^(current_session|library)$"),
    service: SimilarityService = Depends(get_similarity_service),
):
    """Find images similar to a given image ID."""
    if scope is None:
        return await run_in_threadpool(
            service.search_similar, image_id, limit, threshold, offset, collection_id
        )
    return await run_in_threadpool(
        service.search_similar, image_id, limit, threshold, offset, collection_id, scope
    )


@router.post(
    "/search-upload",
    summary="Find similar images by upload",
    description="""
Find images similar to an uploaded image file.

Generates a CLIP embedding for the uploaded image on-the-fly
and searches the database for similar images.
    """,
    responses={
        200: {
            "description": "Similar images found",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {"id": 42, "similarity": 0.89, "filename": "similar_image.png"}
                        ],
                        "count": 1
                    }
                }
            }
        },
        400: {"description": "Empty file uploaded"}
    }
)
async def search_by_upload(
    file: UploadFile = File(..., description="Image file to search for similar images"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum results (1-1000)"),
    offset: int = Query(default=0, ge=0, description="Number of ranked results to skip for pagination"),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0, description="Minimum similarity threshold"),
    collection_id: Optional[int] = Query(default=None, ge=1, description="Restrict similar results to this collection's members (v3.3.1)"),
    scope: Optional[str] = Query(default=None, pattern="^(current_session|library)$"),
    service: SimilarityService = Depends(get_similarity_service),
):
    """Find images similar to an uploaded image."""
    if scope is None:
        return await service.search_by_upload(file, limit, threshold, offset, collection_id)
    return await service.search_by_upload(file, limit, threshold, offset, collection_id, scope)


@router.get(
    "/duplicates",
    summary="Find duplicate images",
    description="""
Find near-duplicate image pairs in the database.

Compares all embedded images against each other and returns pairs
with similarity above the threshold. Useful for identifying
duplicate or near-duplicate images.

**Recommended thresholds:**
- `0.98`: Exact or near-exact duplicates
- `0.95`: Very similar (minor edits, crops)
- `0.90`: Similar (same scene, different shot)
    """,
    responses={
        200: {
            "description": "Duplicate pairs found",
            "content": {
                "application/json": {
                    "example": {
                        "duplicates": [
                            {
                                "image_a": {"id": 1, "filename": "image_001.png"},
                                "image_b": {"id": 2, "filename": "image_002.png"},
                                "similarity": 0.98,
                            }
                        ],
                        "count": 1,
                        "threshold": 0.95
                    }
                }
            }
        }
    }
)
async def find_duplicates(
    threshold: float = Query(default=0.95, ge=0.5, le=1.0, description="Similarity threshold (0.5-1.0)"),
    limit: int = Query(default=500, ge=1, le=5000, description="Maximum pairs to return (1-5000)"),
    offset: int = Query(default=0, ge=0, description="Number of ranked duplicate pairs to skip for pagination"),
    service: SimilarityService = Depends(get_similarity_service),
):
    """Find near-duplicate image pairs above similarity threshold."""
    return await run_in_threadpool(service.find_duplicates, threshold, limit, offset)


@router.get(
    "/compare",
    summary="Compare two images",
    description="Compute the CLIP cosine similarity (0.0-1.0) between two stored, embedded images.",
)
async def compare_images(
    id_a: int = Query(..., ge=1, description="First image ID"),
    id_b: int = Query(..., ge=1, description="Second image ID"),
    service: SimilarityService = Depends(get_similarity_service),
):
    """Compare two images' CLIP similarity side by side (read-only)."""
    return await run_in_threadpool(service.compare_pair, id_a, id_b)


@router.get(
    "/near/{image_id}",
    summary="Nearest images to one image",
    description="""
Return the top-K most similar images to a given image (highest cosine first, no
threshold). Uses the optional hnswlib ANN index when it is installed and
`SD_SIMILARITY_DISABLE_ANN` is not set; otherwise ranks exactly.
    """,
)
async def near_images(
    image_id: int,
    limit: int = Query(default=24, ge=1, le=200, description="Maximum nearest images (1-200)"),
    collection_id: Optional[int] = Query(default=None, ge=1, description="Restrict to a collection's members"),
    scope: Optional[str] = Query(default=None, pattern="^(current_session|library)$"),
    service: SimilarityService = Depends(get_similarity_service),
):
    """Top-K nearest images to a given image ID."""
    if scope is None:
        return await run_in_threadpool(service.find_near, image_id, limit, collection_id)
    return await run_in_threadpool(service.find_near, image_id, limit, collection_id, scope)


@router.get("/stats")
async def embedding_stats(
    service: SimilarityService = Depends(get_similarity_service),
):
    """Get statistics about embeddings."""
    return service.get_stats()


@router.get("/model-status")
async def embedding_model_status(
    service: SimilarityService = Depends(get_similarity_service),
):
    """Get local CLIP model readiness details for the frontend."""
    return service.get_model_status()
