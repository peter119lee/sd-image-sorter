"""Pure Smart Tag pipeline-stage helpers (split out of pipeline.py, 2026-07).

Owns the two stage helpers that touch NEITHER the job registry globals
(_jobs / _active_job_id / _jobs_lock) NOR any facade-patched collaborator:

  * _terminal_job_outcome - pure (job, req) -> (status, message) decision;
  * _process_one_image    - the legacy per-image pipeline (single + consensus).

Seam contract (why exactly these two moved): the patch census over the
smart_tag suites shows every monkeypatched collaborator
(_resolve_tagger, _resolve_tagger_by_model, _request_total,
_recommended_tag_batch_size, _apply_memory_pressure,
_load_toriigate_for_phase2, _persist_result, _append_caption_result, ...)
is consumed only by the functions that STAYED in pipeline.py, where the
services.smart_tag_service facade's owner map lands those writes. The
bodies here consume only never-patched collaborators, imported verbatim
from their defining modules below.

pipeline.py re-imports both names so (a) the facade keeps resolving
smart_tag_service._process_one_image / _terminal_job_outcome through the
pipeline namespace exactly as before, and (b) the direct test import
``from services.smart_tag.pipeline import _terminal_job_outcome`` keeps
working. New code should import from this module directly.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from services.smart_tag.consensus import compute_consensus_tags
from services.smart_tag.jobs import SmartTagJobState, _completion_message
from services.smart_tag.prompts import (
    _vlm_context_tags_for,
    build_vlm_prompt,
    filter_tags_by_training_purpose,
)
from services.smart_tag.request import SmartTagRequest
from services.smart_tag.results import (
    _assemble_result_dict,
    _booru_partial_from_tag_result,
)
from services.smart_tag.tagging import (
    _tag_image_with_thresholds,
    _toriigate_nl_text,
)

# Shared logger: keep the historical channel name so log capture, filtering,
# and support-log diagnostics behave exactly as before the decomposition.
logger = logging.getLogger("services.smart_tag_service")


def _terminal_job_outcome(
    job: SmartTagJobState,
    req: SmartTagRequest,
    *,
    degraded_reason: str = "",
) -> Tuple[str, str]:
    """Return the terminal status and user-facing message without mutating state.

    ``degraded_reason`` describes work the run was asked to do but could not
    (currently: a selected tagger model that would not load). Every image may
    still have been persisted, so this is not a per-image failure — but it
    must stop the run from reporting an unqualified success.
    """
    if job.cancel_requested:
        return "cancelled", "Cancelled by user."
    if job.status != "running":
        return job.status, job.message
    if job.succeeded == 0 and job.failed > 0:
        last_error = ""
        for error in reversed(job.errors):
            last_error = error.get("error", "").strip()
            if last_error:
                break
        if req.caption_profile is not None:
            if not last_error:
                raise RuntimeError(
                    "Caption profile job recorded failures without error details: "
                    f"job_id={job.job_id!r}, failed={job.failed}."
                )
            return (
                "failed",
                f"Caption profile {req.caption_profile.value!r} failed for all "
                f"{job.failed} image(s). Provider error: {last_error}",
            )
        detail = f" Last error: {last_error}" if last_error else ""
        return (
            "failed",
            f"Smart Tag failed for all {job.failed} image(s).{detail}",
        )
    suffix = f" {degraded_reason}" if degraded_reason else ""
    if job.failed > 0 or degraded_reason:
        return "warning", f"Completed with warning. {_completion_message(job)}{suffix}"
    return "completed", _completion_message(job)


def _process_one_image(
    *,
    image_path: str,
    image_id: int,
    req: SmartTagRequest,
    tagger,
    vlm_provider,
    nl_tagger=None,
    precomputed_tagger_outputs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the full per-image pipeline. Returns a dict with caption + tags.

    When ``precomputed_tagger_outputs`` is provided (multi-tagger mode),
    the tagging phase is skipped and the pre-collected outputs are fused
    directly. This avoids reloading models per-image.
    """
    # ------- Stage 1: WD14 / OppaiOracle local tagging --------------------
    if precomputed_tagger_outputs is not None:
        # Multi-tagger consensus from pre-collected per-tagger results.
        raw = compute_consensus_tags(
            precomputed_tagger_outputs,
            consensus_min=req.consensus_min,
            skip_categories=req.consensus_skip_categories,
        )
        raw["tag_score_sets"] = [
            {"model": o.get("model"), "scores": o.get("tag_scores")}
            for o in precomputed_tagger_outputs
            if o.get("model") and o.get("tag_scores")
        ]
    elif req.enable_wd14 and tagger is not None:
        raw = _tag_image_with_thresholds(
            tagger,
            image_path,
            general_threshold=req.general_threshold,
            character_threshold=req.character_threshold,
            copyright_threshold=req.copyright_threshold,
        )
    else:
        raw = {}

    partial = _booru_partial_from_tag_result(
        raw, req, score_model=getattr(tagger, "model_name", None)
    )
    general_names = partial["general_names"]
    copyright_names = partial["copyright_names"]
    character_names = partial["character_names"]

    # ------- Stage 2: VLM caption --------------------------------------
    nl_text = ""
    if req.enable_vlm and req.natural_language_mode == "toriigate" and nl_tagger is not None:
        nl_text = _toriigate_nl_text(
            nl_tagger,
            image_path,
            image_id,
            tags=_vlm_context_tags_for(
                partial,
                req.toriigate_grounding,
                req.training_purpose,
                req.trigger_word,
            ),
        )
    elif req.enable_vlm and vlm_provider is not None:
        # Filter tags by training purpose (LoRA training best practices)
        vlm_context_tags = filter_tags_by_training_purpose(
            req.training_purpose,
            general_names,
            copyright_names,
            character_names,
            req.trigger_word,
        )
        include_tags_as_context = bool(
            getattr(getattr(vlm_provider, "config", None), "include_tags_as_context", True)
        ) and req.vlm_grounding
        prompt = build_vlm_prompt(
            req.training_purpose,
            vlm_context_tags,
            include_tags=include_tags_as_context,
            suppressed_traits=req.suppressed_traits,
        )
        try:
            import asyncio

            async def _call() -> str:
                config = vlm_provider.config
                original_user_prompt = getattr(config, "user_prompt", "")
                original_with_tags = getattr(config, "user_prompt_with_tags", "")
                try:
                    config.user_prompt = prompt
                    config.user_prompt_with_tags = prompt
                    vlm_result = await vlm_provider.caption_image(
                        image_path,
                        tags=vlm_context_tags if include_tags_as_context and vlm_context_tags else None,
                    )
                    return (vlm_result.caption or "").strip()
                finally:
                    config.user_prompt = original_user_prompt
                    config.user_prompt_with_tags = original_with_tags

            nl_text = asyncio.run(_call())
        except Exception as exc:  # noqa: BLE001
            logger.warning("VLM caption failed for image %s: %s", image_id, exc)
            nl_text = ""

    # ------- Stage 3: Caption assembly ---------------------------------
    return _assemble_result_dict(partial, nl_text, image_id, req)
