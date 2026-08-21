"""
Tag endpoints for SD Image Sorter.
Handles tag retrieval, tagging operations, import/export.

Refactored to use Service Layer pattern with dependency injection.
"""
from typing import Optional, Dict, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse

from services.service_provider import ServiceProvider
from services.state_compat import MutableStateProxy
from services.tagging_service import (
    TaggingService,
    TagRequest,
    TagImportRequest,
    BatchTagExportRequest,
    CombinedTagExportRequest,
    ExportPreviewRequest,
)
from services.tagging_pipeline_service import (
    TaggingPipelineService,
    get_tagging_pipeline_service,
)
from services.tag_export_service import combined_export_path
from services.trait_pruning_service import TraitCandidatesRequest
from services.dataset_consistency_service import ConsistencyReportRequest
from services.single_image_tag_service import SingleImageTagRequest
from services.tipo_service import TipoSuggestRequest
from services.tag_score_service import (
    CoverageGapsRequest,
    RethresholdRequest,
    ScorePurgeRequest,
    TagAuditRequest,
)


router = APIRouter(prefix="/api", tags=["tags"])

# Service instance - will be set via dependency injection
tag_progress: Any = None


def _bind_tagging_compat_state(service: TaggingService) -> None:
    """Keep legacy router-level progress handles pointed at the service-owned state."""
    global tag_progress
    tag_progress = service.get_progress_proxy()


def _bind_lazy_tagging_compat_state() -> None:
    """Expose legacy router-level progress without creating TaggingService at import time."""
    global tag_progress
    tag_progress = MutableStateProxy(
        lambda: get_tagging_service().get_progress(),
        lambda state: get_tagging_service().set_progress(state),
    )


_tagging_service_provider = ServiceProvider(TaggingService, on_set=_bind_tagging_compat_state)


def get_tagging_service() -> TaggingService:
    """Dependency injection for TaggingService."""
    return _tagging_service_provider.get()


def set_tagging_service(service: Optional[TaggingService]) -> None:
    """Set or clear the tagging service instance."""
    _tagging_service_provider.set(service)
    if service is None:
        _bind_lazy_tagging_compat_state()


def set_tagger_getter(tagger_getter: "Callable[..., Any]") -> None:
    """Set the tagger getter function from main module."""
    service = get_tagging_service()
    service.set_tagger_getter(tagger_getter)


def get_tag_progress_state() -> Dict[str, Any]:
    """Get the current tag progress state (for backwards compatibility)."""
    return get_tagging_service().get_progress()


def set_tag_progress_state(state: Dict[str, Any]) -> None:
    """Set the tag progress state (for backwards compatibility)."""
    get_tagging_service().set_progress(state)


_bind_lazy_tagging_compat_state()


@router.get(
    "/tags",
    summary="Get all tags",
    description="Retrieve all unique tags from the database with their occurrence counts.",
    responses={
        200: {
            "description": "List of tags with counts",
            "content": {
                "application/json": {
                    "example": {
                        "tags": [
                            {"tag": "1girl", "count": 1523},
                            {"tag": "solo", "count": 1204},
                            {"tag": "long_hair", "count": 892}
                        ]
                    }
                }
            }
        }
    }
)
def get_all_tags(
    limit: Optional[int] = Query(default=None, ge=1, description="Optional display limit; omitted returns all tags"),
    service: TaggingService = Depends(get_tagging_service),
):
    """Get all unique tags with occurrence counts."""
    return service.get_all_tags(limit)


@router.get(
    "/generators",
    summary="Get all generators",
    description="Retrieve all detected generators (ComfyUI, NAI, WebUI, Forge) with image counts.",
    responses={
        200: {
            "description": "List of generators with counts",
            "content": {
                "application/json": {
                    "example": {
                        "generators": [
                            {"generator": "comfyui", "count": 500},
                            {"generator": "nai", "count": 300},
                            {"generator": "webui", "count": 200}
                        ]
                    }
                }
            }
        }
    }
)
def get_generators(
    service: TaggingService = Depends(get_tagging_service),
):
    """Get all generators with image counts."""
    return service.get_generators()


@router.get(
    "/tags/library",
    summary="Get tags library",
    description="Get tags library with frequency counts and sorting options.",
    responses={
        200: {
            "description": "Tags library with sorting",
            "content": {
                "application/json": {
                    "example": {
                        "tags": [
                            {"tag": "1girl", "count": 1523},
                            {"tag": "solo", "count": 1204}
                        ],
                        "total": 5678,
                        "sort": "frequency"
                    }
                }
            }
        }
    }
)
def get_tags_library(
    sort_by: str = Query(default="frequency", description="Sort order: 'frequency' or 'alphabetical'"),
    limit: Optional[int] = Query(default=None, ge=1, description="Optional display limit; omitted returns all matching tags"),
    q: Optional[str] = Query(default=None, description="Search all tags with normalized substring matching"),
    service: TaggingService = Depends(get_tagging_service),
):
    """Get tags library with frequency and sorting options."""
    return service.get_tags_library(sort_by=sort_by, limit=limit, search_query=q)


@router.get(
    "/tags/suggest",
    summary="Suggest tags for type-ahead",
    description="""
Unified tag suggestions for autocomplete inputs. Merges the user's own
library tags (frequency-ranked) with the bundled danbooru vocabulary
(popularity-ranked, alias-aware). CJK queries match the bundled MIT
Chinese/Japanese alias table (StoryAura), or a user drop-in that overrides
it.
    """,
    responses={
        200: {
            "description": "Ranked suggestions",
            "content": {
                "application/json": {
                    "example": {
                        "suggestions": [
                            {"tag": "long_hair", "count": 1523, "source": "library", "category": "body", "zh": None},
                            {"tag": "long_sleeves", "count": 2102140, "source": "danbooru", "category": "outfit", "zh": None},
                        ],
                        "danbooru_loaded": True,
                        "zh_loaded": True,
                    }
                }
            }
        }
    }
)
def suggest_tags(
    q: str = Query(default="", description="Partial tag token; CJK matches Chinese aliases when available"),
    limit: int = Query(default=20, ge=1, le=50, description="Maximum suggestions to return"),
):
    """Type-ahead tag suggestions across library + danbooru vocabulary."""
    from services import tag_suggest_service

    return tag_suggest_service.suggest(q=q, limit=limit)


@router.post("/tags/consistency/report")
async def consistency_report(request: ConsistencyReportRequest):
    """Pre-training dataset health check (BE-5'): trigger hygiene, composition
    balance, tag-set consistency. Read-only: findings describe explicit
    Dataset Maker actions instead of mutating anything here."""
    from services.dataset_consistency_service import build_consistency_report

    if not request.image_ids and not request.selection_token:
        raise HTTPException(400, "Provide image_ids or selection_token")
    return build_consistency_report(request)


@router.post(
    "/tags/trait-candidates",
    summary="Compute character-trait pruning candidates",
    description="""
P1-17 (character LoRA practice): frequency-ranked innate-trait tags (hair /
eyes / skin / body families) across the selected images, so the export UI can
offer a reviewable "absorb traits into the trigger" checklist. Selected tags
feed the ordinary export blacklist — nothing is ever removed silently.
    """,
    responses={
        200: {
            "description": "Trait candidates ranked by frequency",
            "content": {
                "application/json": {
                    "example": {
                        "total_images": 25,
                        "candidates": [
                            {"tag": "silver_hair", "family": "hair", "count": 24, "ratio": 0.96},
                            {"tag": "red_eyes", "family": "eyes", "count": 23, "ratio": 0.92},
                        ],
                    }
                }
            }
        }
    },
)
def trait_candidates(request: TraitCandidatesRequest):
    """Frequency-ranked innate-trait tags for the trait-pruning checklist."""
    from services.trait_pruning_service import compute_trait_candidates

    return compute_trait_candidates(request)


@router.get("/tags/info")
def tag_info(
    tag: str = Query(..., min_length=1, max_length=256, description="Tag or alias to look up"),
):
    """Learn-while-tagging popover data: category, danbooru popularity,
    aliases (alias input resolves to its canonical tag), zh display,
    character copyright/parent when known, implication edges both ways,
    and the live library count."""
    from services import tag_suggest_service

    return tag_suggest_service.get_tag_info(tag)


@router.post("/tags/rethreshold")
def rethreshold_tags(request: RethresholdRequest):
    """BE-1 virtual re-threshold: rewrite tag rows from stored tag_scores at
    new thresholds with ZERO inference. ``model`` names a tagger with stored
    scores, or "consensus" to fuse every stored model. ``dry_run`` (default)
    only reports the diff. Property-tested: equals re-running inference at
    the same thresholds (for thresholds >= the storage floor)."""
    import config as app_config
    from services import tag_score_service
    from services.tagging_service import resolve_request_thresholds

    ids = tag_score_service.resolve_scope_ids(
        request.image_ids, request.selection_token
    )
    if not ids:
        raise HTTPException(400, "Provide image_ids or selection_token")

    is_consensus = request.model.strip().lower() == "consensus"
    if is_consensus:
        if request.threshold is None:
            raise HTTPException(
                400, "Consensus mode needs an explicit threshold (no single model default applies)."
            )
        threshold = float(request.threshold)
        character_threshold = float(
            request.character_threshold
            if request.character_threshold is not None
            else request.threshold
        )
    else:
        threshold, character_threshold = resolve_request_thresholds(
            request.model, request.threshold, request.character_threshold
        )

    floor = float(app_config.TAG_SCORES_FLOOR)
    if threshold < floor or character_threshold < floor:
        raise HTTPException(
            400,
            f"Stored scores are floored at {floor:.2f}; thresholds below that "
            "cannot be reproduced from the score table. Re-tag with a lower "
            "SD_IMAGE_SORTER_TAG_SCORES_FLOOR instead.",
        )

    if is_consensus:
        return tag_score_service.rethreshold_consensus_images(
            ids,
            threshold,
            character_threshold,
            consensus_min=request.consensus_min,
            dry_run=request.dry_run,
            pre_tag_blacklist=request.pre_tag_blacklist,
            max_tags_per_image=request.max_tags_per_image,
        )
    return tag_score_service.rethreshold_images(
        ids,
        request.model,
        threshold,
        character_threshold,
        dry_run=request.dry_run,
        pre_tag_blacklist=request.pre_tag_blacklist,
        max_tags_per_image=request.max_tags_per_image,
    )


@router.post("/tags/coverage-gaps")
def coverage_gaps(request: CoverageGapsRequest):
    """BE-1 coverage completion (N2): images whose stored score for ``tag``
    sits just under the threshold but carry no such tag row — "should
    probably have it, doesn't". Feeds the Separation Console's find-missing
    button; confirmed adds go through the normal bulk-add path as manual."""
    from services import tag_score_service

    return tag_score_service.find_gaps_for_request(request)


@router.post("/tags/suggest-upsample")
def suggest_upsample_tags(request: TipoSuggestRequest):
    """TIPO tag-upsampling assist (roadmap #8, v1): propose danbooru tags
    the WD14-family taggers have no label for. Opt-in dependency pair
    (tipo-kgen + llama-cpp-python, CPU GGUF); missing install returns 400
    with the exact pip hint. Read-only — proposals are vocab-gated and the
    frontend applies confirmed picks itself (never auto-applied)."""
    from services import tipo_service

    try:
        return tipo_service.suggest_upsample(request)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except tipo_service.TipoError as exc:
        raise HTTPException(400, str(exc))


@router.post("/tags/scores/tag-audit")
def tag_model_audit(request: TagAuditRequest):
    """BE-1-UI per-model audit: which models scored this tag in the scope,
    at what confidence spread — the "who said that?" view for dubious tags."""
    from services import tag_score_service

    return tag_score_service.tag_model_audit(request)


@router.get("/tags/scores/stats")
def tag_score_stats():
    """Storage report for the tag_scores table (rows, models, size hint) —
    the visible-cost side of the default-on score persistence."""
    from services import tag_score_service

    return tag_score_service.get_stats()


@router.post("/tags/scores/purge")
def purge_tag_scores(request: ScorePurgeRequest):
    """Drop stored scores — all of them, or one model's slice. The escape
    hatch for reclaiming disk on huge libraries."""
    from services import tag_score_service

    removed = tag_score_service.purge(request.model)
    return {"removed": removed, "model": request.model}


@router.get("/prompts/library")
def get_prompts_library(
    limit: Optional[int] = Query(default=None, ge=1, description="Optional display limit; omitted returns all matching prompt tokens"),
    q: Optional[str] = Query(default=None, description="Search all prompt tokens with normalized substring matching"),
    service: TaggingService = Depends(get_tagging_service),
):
    """Get unique prompt tokens from images with frequency counts."""
    return service.get_prompts_library(limit=limit, search_query=q)


@router.get(
    "/loras/library",
    summary="Get LoRAs library",
    description="""
Get unique LoRAs from the normalized `image_loras` index with frequency counts.

The index is maintained from both the `loras` JSON array and `<lora:name:weight>` prompt patterns during scan/reparse. Names are normalized by stripping weight notation and file extensions.
    """,
    responses={
        200: {
            "description": "LoRAs library",
            "content": {
                "application/json": {
                    "example": {
                        "loras": [
                            {"lora": "detail_tweaker", "count": 234},
                            {"lora": "add_detail", "count": 189}
                        ],
                        "total": 45
                    }
                }
            }
        }
    }
)
def get_loras_library(
    limit: Optional[int] = Query(default=None, ge=1, description="Optional display limit; omitted returns all matching LoRAs"),
    q: Optional[str] = Query(default=None, description="Search all LoRAs with normalized substring matching"),
    service: TaggingService = Depends(get_tagging_service),
):
    """Get unique LoRAs from images with frequency counts."""
    return service.get_loras_library(limit=limit, search_query=q)


@router.get(
    "/checkpoints/library",
    summary="Get Checkpoints library",
    description="Get unique normalized checkpoints with frequency counts (v3.3.0 FEAT-CHECKPOINT-TAB).",
)
def get_checkpoints_library(
    limit: Optional[int] = Query(default=None, ge=1, description="Optional display limit; omitted returns all matching checkpoints"),
    q: Optional[str] = Query(default=None, description="Search all checkpoints with normalized substring matching"),
    service: TaggingService = Depends(get_tagging_service),
):
    """Get unique checkpoints from images with frequency counts."""
    return service.get_checkpoints_library(limit=limit, search_query=q)


@router.get("/tags/export")
async def export_tags(
    service: TaggingService = Depends(get_tagging_service),
):
    """Export all image tags as JSON for backup/transfer."""
    return service.export_tags()


@router.post("/tags/import")
async def import_tags(
    request: TagImportRequest,
    service: TaggingService = Depends(get_tagging_service),
):
    """Import tags from exported JSON data."""
    return service.import_tags(request)


@router.post("/tag/start")
@router.post("/tag")
async def start_tagging(
    request: TagRequest,
    background_tasks: BackgroundTasks,
    service: TaggingService = Depends(get_tagging_service),
    pipeline: TaggingPipelineService = Depends(get_tagging_pipeline_service),
):
    """Start tagging images with WD14 tagger."""
    return pipeline.start_gallery_tagging(request, background_tasks, legacy_service=service)


@router.post(
    "/tag/single",
    summary="Tag one arbitrary image file",
    description=(
        "Run the WD14 tagger against ONE file and return its tags inline. "
        "Takes a filesystem path, not an ``images.id``: the file may be "
        "un-indexed, and no database row is read, created or updated "
        "(``stored`` is always false). Accepts the ``source_temp_path`` that "
        "``POST /api/parse-image`` returns for a retained upload, so a "
        "drop-one-image page needs no second upload route. Unlike "
        "``POST /api/tag`` this is synchronous — there is no job to poll."
    ),
    responses={
        400: {"description": "Unsafe or unsupported image path"},
        404: {"description": "The file does not exist"},
        422: {"description": "The file exists but could not be tagged"},
        503: {"description": "The tagger runtime could not run"},
    },
)
def tag_single_image(request: SingleImageTagRequest):
    """Tag one file on disk without indexing it."""
    from services import single_image_tag_service

    return single_image_tag_service.tag_single_image(request)


@router.get("/tagger/models")
async def get_tagger_models(
    service: TaggingService = Depends(get_tagging_service),
):
    """Get available WD14 tagger models."""
    return service.get_tagger_models()


@router.get("/tag/progress")
async def get_tag_progress(
    service: TaggingService = Depends(get_tagging_service),
    pipeline: TaggingPipelineService = Depends(get_tagging_pipeline_service),
):
    """Get current tagging progress."""
    return pipeline.get_gallery_progress(legacy_service=service)


@router.post("/tag/cancel")
async def cancel_tagging(
    service: TaggingService = Depends(get_tagging_service),
    pipeline: TaggingPipelineService = Depends(get_tagging_pipeline_service),
):
    """Request cancellation of the current tagging task."""
    return pipeline.cancel_gallery_tagging(legacy_service=service)


@router.post("/tag/reset")
async def reset_tag_progress(
    service: TaggingService = Depends(get_tagging_service),
):
    """Manually reset a stuck tagging task back to idle."""
    return service.reset_progress()


@router.get(
    "/tags/pipeline-queue",
    summary="Get the AI-job pipeline queue snapshot",
    description=(
        "Read-only snapshot of the shared AI-job FIFO queue across all kinds "
        "(gallery tagging / smart-tag / VLM / aesthetic). Returns "
        "``{total_queued, queued:[{queue_id, kind, position, enqueued_at}], "
        "last_start_error}``. Powers a live queue indicator (Aurora Phase 3). "
        "No side effects."
    ),
    responses={
        200: {
            "description": "Pipeline queue snapshot",
            "content": {
                "application/json": {
                    "example": {
                        "total_queued": 2,
                        "queued": [
                            {"queue_id": "q1", "kind": "gallery", "position": 1, "enqueued_at": 1717430000.0},
                            {"queue_id": "q2", "kind": "smart", "position": 2, "enqueued_at": 1717430005.0},
                        ],
                        "last_start_error": None,
                    }
                }
            },
        }
    },
)
async def get_pipeline_queue(
    pipeline: TaggingPipelineService = Depends(get_tagging_pipeline_service),
):
    """Return the shared AI-job pipeline queue snapshot (all kinds)."""
    return pipeline.queue_snapshot()


@router.post("/tags/export-batch")
async def export_tags_batch(
    request: BatchTagExportRequest,
    background_tasks: BackgroundTasks,
    service: TaggingService = Depends(get_tagging_service),
):
    """Export tags for each image to individual .txt files.

    Debt-22: with ``background: true`` this returns a durable bulk-job envelope
    (poll GET /api/bulk-jobs/{job_id}, cancel POST /api/bulk-jobs/{job_id}/cancel)
    with per-image progress and mid-run cancel. Without it, the export runs
    synchronously and the response is unchanged.
    """
    if request.background:
        return service.start_export_bulk_job(request, background_tasks)
    return service.export_tags_batch(request)


@router.post("/tags/export-batch/start")
async def start_export_tags_batch_job(
    request: BatchTagExportRequest,
    background_tasks: BackgroundTasks,
    service: TaggingService = Depends(get_tagging_service),
):
    """v3.3.2 Phase-1: start batch tag export as a background job (coarse progress,
    no mid-run cancel) so large exports don't freeze the request."""
    return service.start_export_tags_batch_job(request, background_tasks)


@router.get("/tags/export-batch/progress")
async def get_export_tags_batch_progress(
    service: TaggingService = Depends(get_tagging_service),
):
    """Get current batch tag-export job progress (embeds the export result when done)."""
    return service.get_export_progress()


@router.post("/tags/export-batch/reset")
async def reset_export_tags_batch_progress(
    service: TaggingService = Depends(get_tagging_service),
):
    """Reset a stuck batch tag-export job."""
    return service.reset_export_progress()


@router.post("/tags/export-combined")
def export_tags_combined(
    request: CombinedTagExportRequest,
    service: TaggingService = Depends(get_tagging_service),
):
    """Render selected captions into one server-side downloadable text file."""
    return service.export_tags_combined(request)


@router.get("/tags/export-combined/download/{token}")
async def download_combined_export(token: str):
    """Download a server-rendered combined export without building a JS Blob."""
    path = combined_export_path(token)
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=f"sd-image-sorter-combined-{token[:8]}.txt",
    )


@router.post("/tags/fix-ratings")
async def fix_rating_tags(
    service: TaggingService = Depends(get_tagging_service),
):
    """Clean up duplicate rating tags in existing database."""
    return service.fix_rating_tags()



# === v3.2.1: Export Template Engine endpoints ===


@router.post("/tags/export-preview")
async def export_preview(
    request: ExportPreviewRequest,
    service: TaggingService = Depends(get_tagging_service),
):
    """Render export captions for a small set of images using the template engine.

    Used by the export modal's live preview to show what the final captions
    will look like before committing to a batch export.
    """
    return service.export_preview(request)


@router.get("/tags/export-presets")
async def export_presets():
    """Return list of LoRA training presets for the export modal."""
    from services.export_template_engine import list_presets, TEMPLATE_VARIABLES
    return {
        "presets": list_presets(),
        "variables": TEMPLATE_VARIABLES,
    }
