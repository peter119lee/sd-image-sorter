"""Smart Tag job registry + pipeline orchestration (worker-thread body).

Owns the job registry (_jobs / _active_job_id / _jobs_lock) together with
EVERY function that reads or rebinds those module globals (get_job /
get_active_job / cancel_active_job / _prune_finished_jobs_locked /
_run_pipeline / start_smart_tag_job) - global-statement rebinding of
_active_job_id means they must share one namespace. Also owns the windowed
single-tagger pipeline, the two-phase ToriiGate pipeline, and the
multi-tagger consensus orchestration inside _run_pipeline. The two pure
stage helpers (_terminal_job_outcome and the legacy per-image
_process_one_image) live in pipeline_stages.py and are re-imported here
so the facade owner map and the direct-import test path keep resolving
them on this module.

Monkeypatch seams: the test suites patch collaborator names (e.g.
_resolve_tagger, _apply_memory_pressure, _request_total,
_load_toriigate_for_phase2) on services.smart_tag_service; the facade
forwards those writes into THIS module's namespace because the consuming
call sites live here (see the facade docstring).

Split verbatim out of services/smart_tag_service.py.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.smart_tag.caption_phase import _build_caption_phase, _run_caption_phase
from services.smart_tag.consensus import compute_consensus_tags
from services.smart_tag.jobs import (
    SmartTagJobState,
    _fail_missing_source,
    _record_job_error,
)
from services.smart_tag.pipeline_stages import (
    _process_one_image as _process_one_image,  # re-export: facade + test surface
    _terminal_job_outcome,
)
from services.smart_tag.request import SmartTagRequest, _coerce_request, _request_total
from services.smart_tag.results import (
    _booru_partial_from_tag_result,
    _close_caption_results,
)
from services.smart_tag.sources import _iter_request_source_chunks, _iter_windows
from services.smart_tag.tagging import (
    SMART_TAG_TAG_BATCH_SIZE,
    _apply_memory_pressure,
    _load_florence2_for_phase2,
    _load_toriigate_for_phase2,
    _recommended_tag_batch_size,
    _release_booru_sessions,
    _resolve_tagger,
    _resolve_tagger_by_model,
    _tag_batch_with_thresholds,
)

# Shared logger: keep the historical channel name so log capture, filtering,
# and support-log diagnostics behave exactly as before the decomposition.
logger = logging.getLogger("services.smart_tag_service")


# Job hygiene: finished (completed/warning/failed/cancelled) jobs and their on-disk
# caption-results files used to accumulate for the life of the process. Keep
# only this many finished jobs; older ones are pruned when a new job starts.
SMART_TAG_FINISHED_JOBS_KEPT = 5


# How many images flow through one booru->VLM window in the single-tagger
# pipeline. Bounds peak memory (only this many per-image tag partials are held
# at once — important for very large libraries) and keeps the progress bar
# moving, while still giving the GPU a full batch and the VLM up to
# `concurrent_requests` parallel calls per window.
SMART_TAG_PIPELINE_WINDOW = 64
LOCAL_CAPTION_MODES = frozenset({"toriigate", "florence2"})


_jobs_lock = threading.Lock()
_jobs: Dict[str, SmartTagJobState] = {}
_active_job_id: Optional[str] = None


def _new_job_id() -> str:
    return uuid.uuid4().hex


def get_job(job_id: str) -> Optional[SmartTagJobState]:
    with _jobs_lock:
        return _jobs.get(job_id)


def get_active_job() -> Optional[SmartTagJobState]:
    with _jobs_lock:
        if _active_job_id is None:
            return None
        return _jobs.get(_active_job_id)


def cancel_active_job() -> Optional[SmartTagJobState]:
    """Mark the running job (if any) as cancel-requested. Returns the job.

    Cancel semantics: the pipeline loop checks ``cancel_requested`` before
    processing each new image. Already-processed images have their tags
    persisted via ``_persist_result`` (which commits per-image through
    ``add_tags_batch``), so cancellation never rolls back completed work.
    """
    with _jobs_lock:
        if _active_job_id is None:
            return None
        job = _jobs.get(_active_job_id)
        if job is None:
            return None
        job.cancel_requested = True
        job.message = "Cancellation requested..."
        return job


_TERMINAL_JOB_STATUSES = {"completed", "warning", "failed", "cancelled"}


def _prune_finished_jobs_locked(keep: int = SMART_TAG_FINISHED_JOBS_KEPT) -> List[str]:
    """Drop all but the newest ``keep`` finished jobs from the registry.

    Caller must hold ``_jobs_lock``. Returns the caption-results file paths
    of the evicted jobs so the caller can delete them outside the lock.
    """
    finished = [
        job for job in _jobs.values()
        if job.status in _TERMINAL_JOB_STATUSES and job.job_id != _active_job_id
    ]
    overflow = len(finished) - max(0, int(keep))
    if overflow <= 0:
        return []
    finished.sort(key=lambda job: job.finished_at or job.started_at)
    evicted_paths: List[str] = []
    for job in finished[:overflow]:
        _jobs.pop(job.job_id, None)
        if job.caption_results_path:
            evicted_paths.append(job.caption_results_path)
    return evicted_paths


def _delete_caption_result_files(paths: List[str]) -> None:
    """Best-effort cleanup of pruned jobs' data/smart-tag-results/*.jsonl files."""
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete smart-tag results file %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def _run_windowed_pipeline(
    job: SmartTagJobState,
    req: SmartTagRequest,
    *,
    tagger,
    vlm_provider,
    nl_tagger,
) -> None:
    """Single-tagger Smart Tag pipeline: GPU-batched booru tagging + concurrent
    VLM captioning, streamed in bounded windows.

    Replaces the old one-image-at-a-time loop, which tagged a single image per
    GPU call and ran the VLM with ``asyncio.run`` per image — so the GPU sat
    mostly idle and ``config.concurrent_requests`` was never used. Each image is
    persisted as soon as its caption is assembled, so cancellation never loses
    completed work. Sets the terminal job status itself.
    """
    ctx = _build_caption_phase(req, vlm_provider, nl_tagger)
    # Hardware-aware booru batch size (mirrors the bulk tagging worker): an
    # 8GB-VRAM laptop starts at a size that fits instead of a fixed 64.
    booru_batch_size = (
        _recommended_tag_batch_size(
            getattr(tagger, "model_name", "") or req.tagger_model or "", req.use_gpu
        )
        if (req.enable_wd14 and tagger is not None)
        else SMART_TAG_TAG_BATCH_SIZE
    )
    job.stage = "vlm" if ctx.nl_active else ("tagging" if req.enable_wd14 else "")
    job.phase_completion = 0.0
    if ctx.use_vlm:
        job.message = f"Smart-tagging {job.total} image(s) (VLM x{ctx.worker_count})..."
    else:
        job.message = f"Smart-tagging {job.total} image(s)..."

    for window in _iter_windows(req, SMART_TAG_PIPELINE_WINDOW, job):
        if job.cancel_requested:
            break
        valid = [(sk, iid, p) for (sk, iid, p) in window if p]
        for source_key, _iid, path in window:
            if not path:
                _fail_missing_source(job, source_key)
        if not valid:
            continue

        # ---- Booru phase: ONE GPU-batched call for the whole window. ----
        if req.enable_wd14 and tagger is not None:
            booru_batch_size = _apply_memory_pressure(job, tagger, booru_batch_size)
            job.message = f"Tagging {len(valid)} image(s) on GPU..."
            raw_results = _tag_batch_with_thresholds(
                tagger,
                [path for (_sk, _iid, path) in valid],
                general_threshold=req.general_threshold,
                character_threshold=req.character_threshold,
                copyright_threshold=req.copyright_threshold,
                preferred_batch_size=booru_batch_size,
            )
        else:
            raw_results = [{} for _ in valid]

        partials = [
            _booru_partial_from_tag_result(
                raw or {}, req, score_model=getattr(tagger, "model_name", None)
            )
            for raw in raw_results
        ]
        items = [
            (valid[i][0], valid[i][1], valid[i][2], partials[i])
            for i in range(len(valid))
        ]
        # _run_caption_phase persists each image as its caption completes, so a
        # mid-window cancel keeps finished work and just stops issuing new calls.
        _run_caption_phase(job, req, items, ctx)

    job.status, job.message = _terminal_job_outcome(job, req)


# How many failed model names to spell out before summarising the rest, so a
# ten-tagger run cannot turn the job message into a wall of stack text.
_TAGGER_LOAD_ERROR_NAMES_IN_MESSAGE = 3


def _describe_tagger_load_failures(failures: List[Tuple[str, str]]) -> str:
    """Render "model: reason" for the first few failed loads."""
    shown = failures[:_TAGGER_LOAD_ERROR_NAMES_IN_MESSAGE]
    detail = "; ".join(f"{model}: {reason}" for model, reason in shown)
    remaining = len(failures) - len(shown)
    if remaining > 0:
        detail = f"{detail}; and {remaining} more"
    return detail


def _persist_booru_only(
    job: SmartTagJobState,
    req: "SmartTagRequest",
    pending_items: List[Tuple[str, int, str, Dict[str, Any]]],
) -> None:
    """Persist tag-only captions for items whose NL phase could not run, so a
    failed local-captioner load doesn't throw away a completed booru pass."""
    ctx = _build_caption_phase(req, None, None)
    for win_start in range(0, len(pending_items), SMART_TAG_PIPELINE_WINDOW):
        _run_caption_phase(
            job,
            req,
            pending_items[win_start:win_start + SMART_TAG_PIPELINE_WINDOW],
            ctx,
        )


def _local_captioner_label(req: "SmartTagRequest") -> str:
    if req.natural_language_mode == "toriigate":
        return "ToriiGate"
    if req.natural_language_mode == "florence2":
        return "Florence-2 Base"
    raise ValueError(
        "Local caption pipeline requires natural_language_mode 'toriigate' "
        f"or 'florence2'; received {req.natural_language_mode!r}."
    )


def _load_local_captioner_for_phase2(
    job: SmartTagJobState,
    req: "SmartTagRequest",
):
    if req.natural_language_mode == "toriigate":
        return _load_toriigate_for_phase2(job, req)
    if req.natural_language_mode == "florence2":
        return _load_florence2_for_phase2(job, req)
    raise ValueError(
        "Unsupported local caption mode: "
        f"{req.natural_language_mode!r}."
    )


def _run_two_phase_local_captioner_pipeline(
    job: SmartTagJobState,
    req: "SmartTagRequest",
    *,
    tagger,
) -> None:
    """Two-phase Smart Tag for a local natural-language captioner.

    Phase 1 tags EVERY window with the booru tagger, then the booru session is
    fully released; only then is the selected local captioner loaded for phase
    2. The cloud-VLM mode remains interleaved because it is only an HTTP client.
    Sets the terminal job status itself.
    """
    captioner_label = _local_captioner_label(req)
    booru_batch_size = (
        _recommended_tag_batch_size(
            getattr(tagger, "model_name", "") or req.tagger_model or "", req.use_gpu
        )
        if (req.enable_wd14 and tagger is not None)
        else SMART_TAG_TAG_BATCH_SIZE
    )
    job.stage = "tagging" if req.enable_wd14 else "vlm"
    job.phase_completion = 0.0
    job.message = f"Smart-tagging {job.total} image(s) (phase 1/2: booru tags)..."

    pending_items: List[Tuple[str, int, str, Dict[str, Any]]] = []
    total = max(1, int(job.total or 0))
    for window in _iter_windows(req, SMART_TAG_PIPELINE_WINDOW, job):
        if job.cancel_requested:
            break
        valid = [(sk, iid, p) for (sk, iid, p) in window if p]
        for source_key, _iid, path in window:
            if not path:
                _fail_missing_source(job, source_key)
        if not valid:
            continue

        if req.enable_wd14 and tagger is not None:
            booru_batch_size = _apply_memory_pressure(job, tagger, booru_batch_size)
            raw_results = _tag_batch_with_thresholds(
                tagger,
                [path for (_sk, _iid, path) in valid],
                general_threshold=req.general_threshold,
                character_threshold=req.character_threshold,
                copyright_threshold=req.copyright_threshold,
                preferred_batch_size=booru_batch_size,
            )
        else:
            raw_results = [{} for _ in valid]

        partials = [
            _booru_partial_from_tag_result(
                raw or {}, req, score_model=getattr(tagger, "model_name", None)
            )
            for raw in raw_results
        ]
        pending_items.extend(
            (valid[i][0], valid[i][1], valid[i][2], partials[i])
            for i in range(len(valid))
        )
        job.phase_completion = min(1.0, len(pending_items) / total)
        job.message = f"Phase 1/2: tagged {len(pending_items)}/{total} image(s)..."

    if job.cancel_requested:
        job.status = "cancelled"
        job.message = "Cancelled by user."
        return

    # Residency handoff: booru session out before the local captioner loads.
    if tagger is not None:
        _release_booru_sessions([tagger])

    job.stage = "vlm"
    job.processed = job.skipped
    job.phase_completion = 0.0
    try:
        nl_tagger = _load_local_captioner_for_phase2(job, req)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s load failed after the booru phase: %s", captioner_label, exc)
        _persist_booru_only(job, req, pending_items)
        job.status = "failed"
        job.message = (
            f"Booru tags were saved, but the {captioner_label} caption phase could not "
            f"start: {exc}"
        )
        return

    ctx = _build_caption_phase(req, None, nl_tagger)
    job.message = (
        f"Phase 2/2: captioning {len(pending_items)} image(s) "
        f"with {captioner_label}..."
    )
    for win_start in range(0, len(pending_items), SMART_TAG_PIPELINE_WINDOW):
        if job.cancel_requested:
            break
        _run_caption_phase(
            job,
            req,
            pending_items[win_start:win_start + SMART_TAG_PIPELINE_WINDOW],
            ctx,
        )

    job.status, job.message = _terminal_job_outcome(job, req)


def _run_two_phase_toriigate_pipeline(
    job: SmartTagJobState,
    req: "SmartTagRequest",
    *,
    tagger,
) -> None:
    """Compatibility wrapper for the historical ToriiGate test seam."""
    if req.natural_language_mode != "toriigate":
        raise ValueError("ToriiGate pipeline requires natural_language_mode='toriigate'.")
    _run_two_phase_local_captioner_pipeline(job, req, tagger=tagger)


def _run_pipeline(job: SmartTagJobState, req: SmartTagRequest) -> None:
    """Body of the worker thread - drives the pipeline and updates job state."""
    global _active_job_id
    job.status = "running"
    job.message = "Resolving images..."

    try:
        # Inside the try so a failure (e.g. a selection token that no longer
        # decodes) lands in the failed-state handler below instead of wedging
        # the active-job slot until restart.
        job.total = _request_total(req)
        if job.total <= 0:
            job.status = "failed"
            job.message = "No matching images found."
            return

        # Lazy provider construction so importing this module never triggers
        # heavy ONNX / VLM SDK loads.
        tagger = None
        vlm_provider = None
        nl_tagger = None
        if req.enable_wd14:
            if req.taggers:
                job.message = "Local booru taggers will run one at a time..."
            else:
                job.message = "Loading local booru tagger..."
                tagger = _resolve_tagger(req)
                if hasattr(tagger, "load"):
                    tagger.load()
        if req.enable_vlm:
            if req.natural_language_mode in LOCAL_CAPTION_MODES:
                # v3.4.3 two-phase contract: ToriiGate (~9.6 GB) loads only
                # AFTER the booru phase finishes and its session is released —
                # never alongside WD14. See _run_two_phase_toriigate_pipeline /
                # _load_toriigate_for_phase2. nl_tagger stays None here.
                captioner_label = _local_captioner_label(req)
                job.message = (
                    f"{captioner_label} will load after the booru tagging phase..."
                )
            else:
                job.message = "Loading VLM provider..."
                try:
                    from routers.vlm import _build_config as _build_vlm_config
                    from vlm_providers import get_provider as _get_vlm_provider

                    vlm_config = _build_vlm_config()
                    # Fix B2: _coerce_request already rejected the
                    # (enable_vlm=True, nl_mode='vlm', empty endpoint) case
                    # with a 400 ValueError. If we reach here without an
                    # endpoint, configuration drifted between request
                    # validation and worker start (e.g. user wiped VLM
                    # settings while the request was in flight) — fail loud
                    # rather than silently downgrading to booru-only.
                    if not (vlm_config.endpoint or vlm_config.api_key):
                        raise RuntimeError(
                            "VLM endpoint/api_key disappeared between request "
                            "validation and worker start. Re-check VLM Settings."
                        )
                    vlm_provider = _get_vlm_provider(vlm_config)
                except Exception as exc:
                    if not req.enable_wd14 or req.caption_profile is not None:
                        raise
                    logger.warning("VLM provider not available, continuing without it: %s", exc)
                    vlm_provider = None

        # ---- Multi-tagger mode: process ALL images per-tagger to avoid
        # model reload thrashing. Each tagger is loaded once, tags every
        # image, then the next tagger is loaded. Results are fused after
        # all taggers finish.
        if req.enable_wd14 and req.taggers:
            job.stage = "tagging"
            # Collect all source items first so we can iterate them per-tagger.
            all_sources: List[Tuple[str, int, str]] = []
            for source_chunk in _iter_request_source_chunks(req, job):
                all_sources.extend(source_chunk)
            # Fix M1: keep ``total`` as the image count throughout the job so
            # "Cancelled at N/M" reads as N/M images. ``phase_completion``
            # tracks 0.0->1.0 within the active phase for a smooth bar.
            # skip_existing drops stay counted: total includes them and
            # ``processed`` starts at the skipped count so N/M reaches M.
            job.total = len(all_sources) + job.skipped
            job.processed = job.skipped
            job.phase_completion = 0.0

            # per_image_outputs[i] = list of per-tagger output dicts for image i
            per_image_outputs: List[List[Dict[str, Any]]] = [[] for _ in all_sources]
            tagger_count = len(req.taggers)
            total_tagger_steps = max(1, len(all_sources) * tagger_count)
            used_taggers: List[Any] = []
            # (model, reason) for every tagger whose weights would not load.
            # A model that never loaded contributes no tags, so the run must
            # not present its empty output as a success.
            tagger_load_failures: List[Tuple[str, str]] = []

            for tagger_idx, entry in enumerate(req.taggers):
                if job.cancel_requested:
                    break
                model_name = str(entry.get("model") or "").strip()
                if not model_name:
                    continue
                weight = float(entry.get("weight") or 1.0)
                gen_th = float(entry.get("general_threshold") or req.general_threshold)
                char_th = float(entry.get("character_threshold") or req.character_threshold)
                copy_th = float(entry.get("copyright_threshold") or req.copyright_threshold or gen_th)

                job.message = f"Loading tagger {tagger_idx + 1}/{tagger_count}: {model_name}..."
                try:
                    one_tagger = _resolve_tagger_by_model(
                        model_name,
                        general_threshold=gen_th,
                        character_threshold=char_th,
                        copyright_threshold=copy_th,
                        use_gpu=req.use_gpu,
                    )
                    if hasattr(one_tagger, "load"):
                        one_tagger.load()
                    used_taggers.append(one_tagger)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("smart-tag: failed to load tagger %s: %s", model_name, exc)
                    tagger_load_failures.append((model_name, str(exc)))
                    _record_job_error(
                        job,
                        f"tagger:{model_name}",
                        f"Could not load tagger {model_name}: {exc}",
                    )
                    continue

                tagger_batch_size = _recommended_tag_batch_size(
                    getattr(one_tagger, "model_name", "") or model_name, req.use_gpu
                )

                # GPU-batch this tagger's pass over the images in windows (the
                # old per-image loop barely used the GPU). _tag_batch_with_thresholds
                # self-tunes / falls back per image, so one bad image can't sink
                # the whole window.
                for win_start in range(0, len(all_sources), SMART_TAG_PIPELINE_WINDOW):
                    if job.cancel_requested:
                        break
                    window = all_sources[win_start:win_start + SMART_TAG_PIPELINE_WINDOW]
                    local_valid = [i for i, (_sk, _iid, p) in enumerate(window) if p]
                    if local_valid:
                        tagger_batch_size = _apply_memory_pressure(job, one_tagger, tagger_batch_size)
                        batch_out = _tag_batch_with_thresholds(
                            one_tagger,
                            [window[i][2] for i in local_valid],
                            general_threshold=gen_th,
                            character_threshold=char_th,
                            copyright_threshold=copy_th,
                            preferred_batch_size=tagger_batch_size,
                        )
                        for local_i, out in zip(local_valid, batch_out):
                            out = out or {}
                            per_image_outputs[win_start + local_i].append({
                                "model": model_name,
                                "weight": weight,
                                "general_tags": out.get("general_tags") or [],
                                "copyright_tags": out.get("copyright_tags") or [],
                                "character_tags": out.get("character_tags") or [],
                                "rating": out.get("rating"),
                                # BE-1: raw floored scores ride along so every
                                # model's distribution is persisted, not just
                                # the fused verdicts.
                                "tag_scores": out.get("tag_scores") or [],
                            })
                    # Fix M1: image-count semantics for processed/total; smooth
                    # sub-phase progress through phase_completion (0.0-1.0).
                    images_done = min(len(all_sources), win_start + len(window))
                    steps_done = images_done + (tagger_idx * len(all_sources))
                    job.phase_completion = min(1.0, steps_done / total_tagger_steps)
                    job.processed = job.skipped + min(len(all_sources), steps_done // tagger_count)
                    job.message = f"Tagging ({model_name}) {images_done}/{len(all_sources)}"

            if tagger_load_failures and not used_taggers and not job.cancel_requested:
                # Not one requested model loaded, so the booru phase produced
                # nothing for any image. Fail the way the single-tagger path
                # already does — there _resolve_tagger()/load() raises into the
                # _run_pipeline handler — instead of persisting empty tag sets
                # and counting each one as a success.
                raise RuntimeError(
                    f"Could not load any of the {tagger_count} selected tagger "
                    "model(s), so no tags were written. "
                    f"{_describe_tagger_load_failures(tagger_load_failures)}"
                )

            if job.cancel_requested:
                job.status = "cancelled"
                job.message = "Cancelled by user."
            else:
                # Consensus + concurrent VLM. Build each image's fused tag
                # partial, then run the SAME concurrent caption phase the
                # single-tagger path uses, so the multi-tagger mode also gets
                # VLM concurrency (config.concurrent_requests) instead of the
                # old one-image-at-a-time asyncio.run.
                pending_items: List[Tuple[str, int, str, Dict[str, Any]]] = []
                for img_idx, (source_key, image_id, path) in enumerate(all_sources):
                    if not path:
                        _fail_missing_source(job, source_key)
                        continue
                    fused = compute_consensus_tags(
                        per_image_outputs[img_idx] or [],
                        consensus_min=req.consensus_min,
                        skip_categories=req.consensus_skip_categories,
                    )
                    fused["tag_score_sets"] = [
                        {"model": o.get("model"), "scores": o.get("tag_scores")}
                        for o in (per_image_outputs[img_idx] or [])
                        if o.get("model") and o.get("tag_scores")
                    ]
                    partial = _booru_partial_from_tag_result(fused, req)
                    pending_items.append((source_key, image_id, path, partial))

                # Two-phase residency contract (v3.4.3): the booru sessions go
                # out before ToriiGate loads; a failed load still persists the
                # finished booru pass instead of discarding it.
                if req.enable_vlm and req.natural_language_mode in LOCAL_CAPTION_MODES:
                    _release_booru_sessions(used_taggers)
                    job.stage = "vlm"
                    job.processed = job.skipped
                    job.phase_completion = 0.0
                    try:
                        nl_tagger = _load_local_captioner_for_phase2(job, req)
                    except Exception as exc:  # noqa: BLE001
                        captioner_label = _local_captioner_label(req)
                        logger.error(
                            "%s load failed after the booru phase: %s",
                            captioner_label,
                            exc,
                        )
                        _persist_booru_only(job, req, pending_items)
                        job.status = "failed"
                        job.message = (
                            f"Booru tags were saved, but the {captioner_label} caption "
                            f"phase could not start: {exc}"
                        )
                        return

                ctx = _build_caption_phase(req, vlm_provider, nl_tagger)
                job.stage = "vlm" if ctx.nl_active else "tagging"
                # Fix M1: total stays at the image count; processed resets so
                # the second phase counts images cleanly (skipped images stay
                # counted so N/M still reaches M).
                job.processed = job.skipped
                job.phase_completion = 0.0
                job.message = "Running consensus + VLM..." if ctx.nl_active else "Running consensus..."

                for win_start in range(0, len(pending_items), SMART_TAG_PIPELINE_WINDOW):
                    if job.cancel_requested:
                        break
                    _run_caption_phase(
                        job,
                        req,
                        pending_items[win_start:win_start + SMART_TAG_PIPELINE_WINDOW],
                        ctx,
                    )

                # Some models loaded and some did not: the images were tagged,
                # but by fewer models than the user selected, so the consensus
                # is not the one they asked for. Downgrade the clean success.
                degraded_reason = ""
                if tagger_load_failures:
                    degraded_reason = (
                        f"{len(tagger_load_failures)} of {tagger_count} selected "
                        "tagger model(s) could not be loaded and contributed no "
                        f"tags: {_describe_tagger_load_failures(tagger_load_failures)}"
                    )
                job.status, job.message = _terminal_job_outcome(
                    job, req, degraded_reason=degraded_reason
                )
        elif req.enable_vlm and req.natural_language_mode == "toriigate":
            # Single-tagger + local ToriiGate: two-phase (tag all → release
            # booru session → load ToriiGate → caption all) so the two heavy
            # models are never resident together. Sets terminal status itself.
            _run_two_phase_toriigate_pipeline(job, req, tagger=tagger)
        elif req.enable_vlm and req.natural_language_mode == "florence2":
            _run_two_phase_local_captioner_pipeline(job, req, tagger=tagger)
        else:
            # Single-tagger / no-tagger path: GPU-batched booru tagging +
            # concurrent VLM captioning (see _run_windowed_pipeline). It sets
            # the terminal job status itself: completed, warning, failed, or
            # cancelled.
            _run_windowed_pipeline(
                job,
                req,
                tagger=tagger,
                vlm_provider=vlm_provider,
                nl_tagger=nl_tagger,
            )
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.message = f"Smart Tag failed: {exc}"
        logger.exception("smart-tag pipeline failed")
    finally:
        _close_caption_results(job)
        job.finished_at = time.time()
        with _jobs_lock:
            if _active_job_id == job.job_id:
                _active_job_id = None


def start_smart_tag_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Public entry point used by the router.

    Validates the payload, registers a job, and starts the pipeline in a
    daemon worker thread. Returns the initial job snapshot.
    """
    global _active_job_id
    req = _coerce_request(payload)

    with _jobs_lock:
        if _active_job_id is not None:
            existing = _jobs.get(_active_job_id)
            if existing and existing.status in ("queued", "running"):
                raise RuntimeError(
                    "Another Smart Tag job is already running. "
                    "Cancel it first or wait for it to finish."
                )
        # Job hygiene: evict old finished jobs (and remember their on-disk
        # caption-results files) now that a new job is starting.
        stale_result_files = _prune_finished_jobs_locked()
        job = SmartTagJobState(
            job_id=_new_job_id(),
            settings={
                "image_count": len(req.image_ids),
                "selection_count": int(req.selection_count or 0),
                "path_count": len(req.image_paths),
                "dataset_scan_count": int(req.dataset_scan_count or 0),
                "has_selection_token": bool(req.selection_token),
                "has_dataset_scan_token": bool(req.dataset_scan_token),
                "training_purpose": req.training_purpose,
                "trigger_word": req.trigger_word,
                "merge_strategy": req.merge_strategy,
                "auto_strip_noise": req.auto_strip_noise,
                "skip_existing": req.skip_existing,
                "enable_wd14": req.enable_wd14,
                "enable_vlm": req.enable_vlm,
                "tagger_model": req.tagger_model,
                "taggers": list(req.taggers),
                "consensus_min": req.consensus_min,
                "natural_language_mode": req.natural_language_mode,
                "caption_profile": (
                    req.caption_profile.value
                    if req.caption_profile is not None
                    else None
                ),
                "general_threshold": req.general_threshold,
                "character_threshold": req.character_threshold,
                "copyright_threshold": req.copyright_threshold,
            },
        )
        _jobs[job.job_id] = job
        _active_job_id = job.job_id

    # File deletion happens outside _jobs_lock so a slow disk cannot stall
    # progress polls; these jsonl files are no longer referenced by any job.
    _delete_caption_result_files(stale_result_files)

    threading.Thread(
        target=_run_pipeline,
        args=(job, req),
        name=f"smart-tag-{job.job_id[:8]}",
        daemon=True,
    ).start()
    return job.snapshot()
