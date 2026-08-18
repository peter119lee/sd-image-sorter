"""Tagging worker process: the multiprocessing spawn target and its helpers.

Moved verbatim from services/tagging_service.py (decomposition 2026-07).
_tagging_worker_main is resolved by module+qualname when the spawn context
starts a child process, so this module path must stay importable from a
child process.
"""

import gc
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from config import DEFAULT_TAGGER_MODEL, TAGGER_MODELS
from image_fingerprint import compute_image_content_fingerprint
from metadata_parser import verify_image_readable
from services import entry_stats_service
from utils.reported_cause import describe_readability_failure
from services.tagging.filters import (
    _apply_pre_tag_filters,
    _build_last_run_stats,
    _iter_rescaling_batches,
    _iter_rescaling_chunk_source,
)
from services.tagging.progress import _build_tag_progress_state
from services.tagging.request import TagRequest, resolve_request_thresholds
from services.tagging.runtime_plan import _format_runtime_adjustment_message
from tag_writer_provenance import (
    build_wd14_writer_provenance,
    require_image_content_fingerprint,
    require_unchanged_wd14_loaded_model,
    replace_wd14_runtime_providers,
)
from utils.source_paths import resolve_existing_indexed_image_path

# NOTE(decomposition): keep the historical logger channel so log routing
# and output stay byte-identical after the services/tagging split.
logger = logging.getLogger("services.tagging_service")

# NOTE(decomposition): this file moved one directory deeper than the old
# services/tagging_service.py. resolve_existing_indexed_image_path derives
# the backend root from dirname(dirname(backend_file)), so keep pointing at
# the original facade path — otherwise the relative indexed-image path
# candidates would silently change.
_BACKEND_FILE = str(Path(__file__).resolve().parents[1] / "tagging_service.py")


def _runtime_provider_chain(tagger: Any) -> List[str]:
    session = getattr(tagger, "session", None)
    get_providers = getattr(session, "get_providers", None)
    if not callable(get_providers):
        return []
    return [str(value) for value in get_providers()]


def _source_file_identity(image_path: str) -> tuple[int, int, int, int]:
    source_stat = os.stat(image_path)
    return (
        int(source_stat.st_dev),
        int(source_stat.st_ino),
        int(source_stat.st_size),
        int(source_stat.st_mtime_ns),
    )


def _prepare_wd14_source_evidence(
    image_path: str,
) -> tuple[str, tuple[int, int, int, int]]:
    identity_before = _source_file_identity(image_path)
    content_fingerprint = require_image_content_fingerprint(
        compute_image_content_fingerprint(image_path)
    )
    identity_after = _source_file_identity(image_path)
    if identity_before != identity_after:
        raise RuntimeError(
            "source image changed while its WD14 content fingerprint was computed"
        )
    return content_fingerprint, identity_after


def _require_unchanged_wd14_source(
    image_path: str,
    expected_identity: tuple[int, int, int, int],
    expected_content_fingerprint: str,
) -> None:
    current_fingerprint, current_identity = _prepare_wd14_source_evidence(image_path)
    if (
        current_identity != expected_identity
        or not hmac.compare_digest(
            current_fingerprint,
            expected_content_fingerprint,
        )
    ):
        raise RuntimeError(
            "source image changed while WD14 inference was running; tags were not published"
        )


class _E2ETaggingStub:
    """Small deterministic tagger for Playwright full-flow tests only."""

    use_gpu = False

    def load(self) -> None:
        return None

    def set_session_refresh_interval(self, _interval: int) -> None:
        return None

    def tag_batch(
        self,
        image_paths: List[str],
        *,
        preferred_batch_size: int = 1,
        min_batch_size: int = 1,
        return_runtime_info: bool = False,
    ) -> Any:
        results = []
        for image_path in image_paths:
            stem = (
                Path(image_path).stem.replace("_", " ").replace("-", " ").strip()
                or "image"
            )
            all_tags = [
                {"tag": "e2e_fixture", "confidence": 0.99, "category": "general"},
                {"tag": stem, "confidence": 0.88, "category": "general"},
                {"tag": "general", "confidence": 0.99, "category": "rating"},
            ]
            result = {
                "all_tags": all_tags,
                "general_tags": all_tags[:2],
                "character_tags": [],
                "rating": {"tag": "general", "confidence": 0.99},
                "error": None,
            }
            # BE-1: mirror the real _process_probs contract so e2e runs
            # exercise the tag_scores write seam end-to-end, including one
            # sub-threshold score the rethreshold/coverage-gap endpoints can
            # surface.
            if config.TAG_SCORES_ENABLED:
                result["tag_scores"] = [
                    {"tag": "e2e_fixture", "score": 0.99, "category": "general"},
                    {"tag": stem, "score": 0.88, "category": "general"},
                    {"tag": "e2e_low_conf", "score": 0.18, "category": "general"},
                    {"tag": "general", "score": 0.99, "category": "rating"},
                ]
            results.append(result)
        runtime_info = {
            "requested_batch_size": preferred_batch_size,
            "effective_batch_size": max(
                min_batch_size, min(preferred_batch_size, max(1, len(image_paths)))
            ),
            "fallbacks": [],
        }
        if return_runtime_info:
            return results, runtime_info
        return results


def _e2e_tagger_getter(**_kwargs: Any) -> _E2ETaggingStub:
    return _E2ETaggingStub()


def _tagging_worker_main(
    runtime_plan_payload: Dict[str, Any],
    progress_queue: Any,
    cancel_event: Any,
) -> None:
    """Run the heavy tagger work in a child process so GPU/provider crashes do not kill the API."""
    import database as worker_db
    from tagger import get_tagger
    from toriigate_tagger import get_toriigate_tagger
    from oppai_oracle_tagger import get_oppai_oracle_tagger
    from cl_tagger_v2 import get_cl_tagger_v2_tagger

    request = TagRequest.model_validate(runtime_plan_payload.get("request", {}))
    effective_model_name = (
        runtime_plan_payload.get("model_name")
        or (request.model_name or DEFAULT_TAGGER_MODEL).strip()
    )
    model_config = TAGGER_MODELS.get(effective_model_name, {})
    runtime_backend = str(model_config.get("runtime_backend", "wd14")).lower()
    writer_family = str(model_config.get("writer_family", "")).strip().lower()
    effective_use_gpu = bool(
        runtime_plan_payload.get("effective_use_gpu", request.use_gpu)
    )
    startup_notice = str(runtime_plan_payload.get("startup_notice", "") or "")
    batch_size = max(1, int(runtime_plan_payload.get("fetch_batch_size", 100)))
    commit_interval = max(1, int(runtime_plan_payload.get("commit_interval", 50)))
    gc_interval = max(1, int(runtime_plan_payload.get("gc_interval", 50)))
    cpu_pause_seconds = max(
        0.0, float(runtime_plan_payload.get("cpu_pause_seconds", 0.0))
    )
    session_refresh_interval = max(
        0, int(runtime_plan_payload.get("session_refresh_interval", 0))
    )
    total_processed = 0
    total_tagged = 0
    total_errors = 0
    total = 0
    gpu_fallback_announced = False
    tagging_start_time = 0.0
    runtime_backend_target = "gpu" if effective_use_gpu else "cpu"
    # Don't claim an actual backend until the tagger is actually loaded. ONNX Runtime
    # silently falls back to CPU when CUDA init fails (no exception), so reading
    # session.get_providers() post-load is the only honest source of truth. Leaving
    # this blank during the load phase makes the UI show "GPU Target" (from checkbox)
    # instead of lying with "GPU target -> GPU actual" before the session exists.
    runtime_backend_actual = ""
    runtime_backend_reason = ""
    memory_pressure_warning = ""

    def infer_runtime_reason() -> str:
        try:
            from hardware_monitor import get_system_info

            system_info = get_system_info()
        except Exception:
            system_info = {}

        if runtime_backend == "toriigate":
            if not system_info.get("torch_cuda_available"):
                return "CUDA is unavailable or this build only has the CPU PyTorch runtime."
            return "ToriiGate failed to stay on CUDA and fell back to CPU."

        providers = [
            str(item).lower() for item in (system_info.get("onnx_providers") or [])
        ]
        if not any(
            provider in providers
            for provider in [
                "cudaexecutionprovider",
                "dmlexecutionprovider",
                "tensorrtexecutionprovider",
            ]
        ):
            return "The ONNX runtime has no GPU provider on this machine."
        return "The GPU provider failed, so the run continued on CPU."

    def send(
        status: str,
        message: str,
        current: Optional[int] = None,
        total_override: Optional[int] = None,
        last_run_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        progress_queue.put(
            _build_tag_progress_state(
                status=status,
                current=total_processed if current is None else current,
                total=total if total_override is None else total_override,
                tagged=total_tagged,
                errors=total_errors,
                message=message,
                runtime_backend_target=runtime_backend_target,
                runtime_backend_actual=runtime_backend_actual,
                runtime_backend_reason=runtime_backend_reason,
                memory_pressure_warning=memory_pressure_warning,
                last_run_stats=last_run_stats,
            )
        )

    def send_with_eta(base_message: str) -> None:
        """Send a progress message with ETA calculation when in tagging phase."""
        if tagging_start_time > 0 and total_processed > 0 and total > 0:
            elapsed = time.time() - tagging_start_time
            rate = total_processed / max(elapsed, 0.001)
            remaining = total - total_processed
            eta_seconds = remaining / max(rate, 0.001)
            if eta_seconds < 60:
                eta_str = f"{int(eta_seconds)}s"
            elif eta_seconds < 3600:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = (
                    f"{int(eta_seconds // 3600)}h {int((eta_seconds % 3600) // 60)}m"
                )
            send("running", f"{base_message} (ETA: {eta_str})")
        else:
            send("running", base_message)

    try:
        if cancel_event.is_set():
            send("cancelled", "Tagging cancelled before processing images")
            return

        if request.model_path:
            send("running", "Loading custom model...")
        elif runtime_backend == "toriigate":
            # ToriiGate first-use pulls Qwen2.5-VL (~5 GB). Detect the empty
            # cache and surface a clear size warning BEFORE the download starts
            # so users on slow / metered links know what is about to happen.
            try:
                from config import get_toriigate_model_dir

                cache_root = Path(get_toriigate_model_dir()) / effective_model_name
                already_cached = (cache_root / "config.json").exists()
            except Exception:
                already_cached = False
            if not already_cached:
                send(
                    "running",
                    "First-time ToriiGate download: ~5 GB from HuggingFace. "
                    "This runs once; keep the app open until it completes.",
                )
            else:
                send(
                    "running",
                    f"Loading ToriiGate on {'GPU' if effective_use_gpu else 'CPU'}...",
                )
        elif runtime_backend == "oppai-oracle":
            # OppaiOracle V1.1 ONNX is ~947 MB. Surface a clear size warning
            # BEFORE the download starts so users on slow / metered links
            # know what is about to happen.
            try:
                from config import get_oppai_oracle_model_dir

                cache_root = Path(get_oppai_oracle_model_dir()) / effective_model_name
                already_cached = (
                    any(cache_root.rglob("model.onnx"))
                    if cache_root.exists()
                    else False
                )
            except Exception:
                already_cached = False
            if not already_cached:
                send(
                    "running",
                    "First-time OppaiOracle download: ~947 MB from HuggingFace. "
                    "This runs once; keep the app open until it completes.",
                )
            else:
                send(
                    "running",
                    f"Loading OppaiOracle on {'GPU' if effective_use_gpu else 'CPU'}...",
                )
        elif runtime_backend == "cl-tagger-v2":
            try:
                from config import get_cl_tagger_v2_model_dir

                cache_root = (
                    Path(get_cl_tagger_v2_model_dir())
                    / effective_model_name
                )
                already_cached = any(cache_root.rglob("model.onnx")) if cache_root.exists() else False
            except OSError:
                already_cached = False
            if not already_cached:
                send(
                    "running",
                    "First-time CL Tagger v2 download: the gated checkpoint will be fetched from official Hugging Face after authorization.",
                )
            else:
                send(
                    "running",
                    f"Loading CL Tagger v2 on {'GPU' if effective_use_gpu else 'CPU'}...",
                )
        elif effective_use_gpu:
            send("running", "Loading model on GPU...")
        else:
            send("running", "Loading model on CPU...")

        if (
            os.environ.get("SD_IMAGE_SORTER_E2E_FAKE_TAGGER") == "1"
            and runtime_backend != "toriigate"
        ):
            tagger_getter = _e2e_tagger_getter
        else:
            if runtime_backend == "toriigate":
                tagger_getter = get_toriigate_tagger
            elif runtime_backend == "oppai-oracle":
                tagger_getter = get_oppai_oracle_tagger
            elif runtime_backend == "cl-tagger-v2":
                tagger_getter = get_cl_tagger_v2_tagger
            else:
                tagger_getter = get_tagger
        effective_threshold, effective_character_threshold = resolve_request_thresholds(
            effective_model_name, request.threshold, request.character_threshold
        )
        tagger = tagger_getter(
            model_name=effective_model_name,
            model_path=request.model_path,
            tags_path=request.tags_path,
            threshold=effective_threshold,
            character_threshold=effective_character_threshold,
            use_gpu=effective_use_gpu,
            force_reload=True,
        )

        # Eagerly load the model so progress transitions from "loading" to "tagging"
        if hasattr(tagger, "load"):
            from ai_runtime_guard import exclusive_ai_runtime

            with exclusive_ai_runtime(f"tagger-load:{effective_model_name}"):
                tagger.load()

        runtime_providers = _runtime_provider_chain(tagger)
        is_e2e_stub = isinstance(tagger, _E2ETaggingStub)
        resolved_model_path = getattr(tagger, "_resolved_model_path", None)
        loaded_model_file_identity = getattr(
            tagger,
            "_loaded_model_file_identity",
            None,
        )
        loaded_model_file_sha256 = getattr(
            tagger,
            "_loaded_model_file_sha256",
            None,
        )
        writer_provenance = (
            None
            if is_e2e_stub
            else build_wd14_writer_provenance(
                effective_model_name=effective_model_name,
                requested_model_path=request.model_path,
                model_config=model_config,
                resolved_model_path=resolved_model_path,
                runtime_providers=runtime_providers,
                loaded_model_file_identity=loaded_model_file_identity,
                loaded_model_file_sha256=loaded_model_file_sha256,
            )
        )
        if writer_family == "wd14" and not is_e2e_stub and writer_provenance is None:
            raise RuntimeError(
                "WD14 tagging cannot continue without complete writer provenance"
            )
        if hasattr(tagger, "set_session_refresh_interval"):
            tagger.set_session_refresh_interval(session_refresh_interval)

        runtime_backend_actual = "gpu" if getattr(tagger, "use_gpu", False) else "cpu"
        if runtime_backend_actual == "gpu":
            runtime_backend_reason = "The runtime loaded successfully on GPU."
        elif effective_use_gpu:
            runtime_backend_reason = infer_runtime_reason()
        else:
            runtime_backend_reason = "CPU mode was requested for this run."

        if startup_notice:
            send("running", startup_notice)

        if effective_use_gpu and not getattr(tagger, "use_gpu", False):
            gpu_fallback_announced = True
            send(
                "running",
                f"GPU load failed. Continuing on CPU instead. Reason: {runtime_backend_reason}",
            )

        if cancel_event.is_set():
            send("cancelled", "Tagging cancelled before processing images")
            return

        send("running", "Collecting image list...")
        if request.image_ids:
            # Avoid N+1: per-id `get_image_by_id` becomes one round-trip per
            # image and would block "Collecting image list..." for minutes
            # on a 5M-id request before the first image is even tagged.
            # `get_images_by_ids` already chunks IN(...) at 500.
            existing_ids = set(
                worker_db.get_images_by_ids(list(request.image_ids)).keys()
            )
            all_ids = [img_id for img_id in request.image_ids if img_id in existing_ids]
            id_batches = _iter_rescaling_batches(all_ids, lambda: batch_size)
            total = len(all_ids)
        elif request.retag_all:
            total = worker_db.count_all_image_ids()
            id_batches = _iter_rescaling_chunk_source(
                worker_db.iter_all_image_id_chunks(max(batch_size, 1000)),
                lambda: batch_size,
            )
        else:
            total = worker_db.count_untagged_image_ids()
            id_batches = _iter_rescaling_chunk_source(
                worker_db.iter_untagged_image_id_chunks(max(batch_size, 1000)),
                lambda: batch_size,
            )

        send(
            "running",
            f"Model loaded. Tagging {total} images...",
            current=0,
            total_override=total,
        )
        tagging_start_time = time.time()
        tags_batch: List[Dict[str, Any]] = []
        # v3.2.2 T-power-PR2 (H): accumulate top-tag frequency for the
        # post-completion stats modal. Counter is process-local; no IPC
        # cost — flushed once into the terminal progress send.
        from collections import Counter as _Counter

        top_tags_counter: _Counter = _Counter()

        # Use _iter_rescaling_batches so memory-pressure reductions to batch_size
        # take effect on the very next iteration. A plain range(0, total, batch_size)
        # would capture the step at creation and skip images whenever the chunk
        # shrank mid-run.
        for batch_start, batch_ids in id_batches:
            if cancel_event.is_set():
                break

            batch_images_map = worker_db.get_images_by_ids(batch_ids)
            batch_images = [img for img in batch_images_map.values() if img]

            existing_images: List[Dict[str, Any]] = []
            batch_paths: List[str] = []

            for img in batch_images:
                image_path = img["path"]
                resolved_path = resolve_existing_indexed_image_path(
                    image_path, backend_file=_BACKEND_FILE
                )
                image_name = os.path.basename(resolved_path or image_path)
                if not resolved_path:
                    total_errors += 1
                    total_processed += 1
                    worker_db.mark_image_unreadable(img["id"], "File not found")
                    logger.error("Image file missing during tagging: %s", image_path)
                    send_with_eta(
                        f"Skipped unreadable image: {image_name} (File not found)",
                    )
                    continue

                readable, read_error = verify_image_readable(resolved_path)
                if not readable:
                    total_errors += 1
                    total_processed += 1
                    cause = describe_readability_failure(read_error)
                    worker_db.mark_image_unreadable(img["id"], cause)
                    logger.warning(
                        "Skipping unreadable image during tagging: %s (%s)",
                        image_path,
                        read_error,
                    )
                    send_with_eta(
                        f"Skipped unreadable image: {image_name} ({cause})",
                    )
                    continue

                source_evidence: Dict[str, Any] = {}
                if writer_provenance is not None:
                    try:
                        content_fingerprint, source_identity = (
                            _prepare_wd14_source_evidence(resolved_path)
                        )
                    except Exception as error:
                        total_errors += 1
                        total_processed += 1
                        logger.error(
                            "Skipping image without stable WD14 source evidence: %s (%s)",
                            image_path,
                            error,
                        )
                        send_with_eta(
                            f"Skipped changed or unverifiable image: {image_name} ({error})",
                        )
                        continue
                    source_evidence = {
                        "_content_fingerprint": content_fingerprint,
                        "_source_identity": source_identity,
                    }

                existing_images.append(
                    {
                        **img,
                        "_resolved_path": resolved_path,
                        **source_evidence,
                    }
                )
                batch_paths.append(resolved_path)

            if existing_images:
                processed_in_batch = 0
                try:
                    # Elastic batch sizing: check memory and reduce batch if under pressure
                    try:
                        from hardware_monitor import check_memory_pressure

                        pressure = check_memory_pressure()
                        if pressure.get("should_restart_session"):
                            tagger._recreate_session()
                            memory_pressure_warning = (
                                "VRAM pressure forced a runtime session refresh."
                            )
                            send("running", memory_pressure_warning)
                        ram_avail = pressure.get("ram_available_gb")
                        ram_total = pressure.get("ram_total_gb")
                        ram_pct = pressure.get("ram_percent_used")
                        if pressure.get("should_pause"):
                            logger.warning(
                                "Memory pressure critical (RAM: %.1f/%.1f GB, %.0f%% used). Pausing 2s and reducing batch.",
                                ram_avail or 0,
                                ram_total or 0,
                                ram_pct or 0,
                            )
                            if ram_avail is not None and ram_total is not None:
                                memory_pressure_warning = (
                                    f"Memory pressure is critical "
                                    f"({ram_avail:.1f} of {ram_total:.1f} GB RAM free, {ram_pct:.0f}% used). "
                                    f"Pausing briefly and reducing chunk size."
                                )
                            else:
                                memory_pressure_warning = "Memory pressure is critical. Pausing briefly and reducing chunk size."
                            send("running", memory_pressure_warning)
                            time.sleep(2)
                            gc.collect()
                            batch_size = max(1, batch_size // 2)
                        elif ram_pct is not None and ram_pct >= 90.0 and batch_size > 2:
                            reduced = max(2, batch_size // 2)
                            logger.info(
                                "High RAM usage (%.0f%% used, %.1f/%.1f GB free). Reducing batch size %d -> %d.",
                                ram_pct,
                                ram_avail or 0,
                                ram_total or 0,
                                batch_size,
                                reduced,
                            )
                            if ram_avail is not None and ram_total is not None:
                                memory_pressure_warning = (
                                    f"High RAM usage ({ram_pct:.0f}% used, {ram_avail:.1f} of {ram_total:.1f} GB free). "
                                    f"Reducing chunk size to {reduced}."
                                )
                            else:
                                memory_pressure_warning = f"High RAM usage detected. Reducing chunk size to {reduced}."
                            send("running", memory_pressure_warning)
                            batch_size = reduced
                    except Exception:
                        pass  # hardware_monitor not available

                    # Show which images are being tagged
                    first_name = os.path.basename(batch_paths[0])
                    if len(existing_images) > 1:
                        last_name = os.path.basename(batch_paths[-1])
                        send(
                            "running",
                            f"Tagging {total_processed + 1}-{total_processed + len(existing_images)}/{total}: {first_name} ... {last_name}",
                        )
                    else:
                        send(
                            "running",
                            f"Tagging {total_processed + 1}/{total}: {first_name}",
                        )
                    providers_before_inference = (
                        _runtime_provider_chain(tagger)
                        if writer_provenance is not None
                        else []
                    )
                    batch_results, runtime_info = tagger.tag_batch(
                        batch_paths,
                        preferred_batch_size=batch_size,
                        min_batch_size=1,
                        return_runtime_info=True,
                    )
                    providers_after_inference = (
                        _runtime_provider_chain(tagger)
                        if writer_provenance is not None
                        else []
                    )

                    if (
                        writer_provenance is not None
                        and (
                            runtime_info.get("used_cpu_fallback")
                            or providers_before_inference
                            != providers_after_inference
                        )
                    ):
                        fallback_runtime_info = runtime_info
                        retry_providers_before = providers_after_inference
                        batch_results, retry_runtime_info = tagger.tag_batch(
                            batch_paths,
                            preferred_batch_size=batch_size,
                            min_batch_size=1,
                            return_runtime_info=True,
                        )
                        retry_providers_after = _runtime_provider_chain(tagger)
                        if (
                            retry_runtime_info.get("used_cpu_fallback")
                            or retry_providers_before != retry_providers_after
                        ):
                            raise RuntimeError(
                                "WD14 runtime provider chain changed again while "
                                "re-running a fallback batch; no tags were published"
                            )
                        runtime_info = dict(retry_runtime_info)
                        runtime_info["used_cpu_fallback"] = True
                        runtime_info["attempted_gpu_backoff"] = bool(
                            fallback_runtime_info.get("attempted_gpu_backoff")
                            or retry_runtime_info.get("attempted_gpu_backoff")
                        )
                        runtime_info["backoff_steps"] = [
                            *(fallback_runtime_info.get("backoff_steps") or []),
                            *(retry_runtime_info.get("backoff_steps") or []),
                        ]
                        providers_after_inference = retry_providers_after

                    batch_writer_provenance = None
                    if writer_provenance is not None:
                        require_unchanged_wd14_loaded_model(
                            writer_provenance,
                            getattr(tagger, "_resolved_model_path", None),
                            getattr(tagger, "_loaded_model_file_identity", None),
                            getattr(tagger, "_loaded_model_file_sha256", None),
                        )
                        batch_writer_provenance = replace_wd14_runtime_providers(
                            writer_provenance,
                            providers_after_inference,
                        )

                    runtime_adjustment_message = _format_runtime_adjustment_message(
                        runtime_info
                    )
                    if runtime_adjustment_message:
                        send(
                            "running", f"Adaptive runtime: {runtime_adjustment_message}"
                        )

                    if runtime_info.get("used_cpu_fallback"):
                        runtime_backend_actual = "cpu"
                        runtime_backend_reason = (
                            "GPU inference failed, so the run continued on CPU."
                        )

                    if (
                        effective_use_gpu
                        and not gpu_fallback_announced
                        and not getattr(tagger, "use_gpu", False)
                    ):
                        gpu_fallback_announced = True
                        runtime_backend_actual = "cpu"
                        runtime_backend_reason = (
                            "GPU inference failed, so the run continued on CPU."
                        )
                        send(
                            "running",
                            f"GPU inference failed. Continuing on CPU... Reason: {runtime_backend_reason}",
                        )

                    for img, result in zip(existing_images, batch_results):
                        if cancel_event.is_set():
                            break

                        result_error = result.get("error")
                        resolved_path = img.get("_resolved_path") or img["path"]
                        if result_error is None and writer_provenance is not None:
                            try:
                                _require_unchanged_wd14_source(
                                    resolved_path,
                                    img["_source_identity"],
                                    img["_content_fingerprint"],
                                )
                            except Exception as error:
                                result_error = str(error)

                        if result_error:
                            total_errors += 1
                            logger.error(
                                "Error tagging %s: %s",
                                resolved_path,
                                result_error,
                            )
                        else:
                            content_fingerprint = img.get("_content_fingerprint")
                            if writer_provenance is None:
                                try:
                                    content_fingerprint = (
                                        compute_image_content_fingerprint(resolved_path)
                                    )
                                except Exception as exc:
                                    logger.warning(
                                        "Could not compute content fingerprint for %s: %s",
                                        resolved_path,
                                        exc,
                                    )

                            # Apply pre-tag filters (T-power-PR1):
                            # 1. pre_tag_blacklist: drop any tag whose name (case-insensitive,
                            #    underscores normalized) matches one of the blacklist entries.
                            # 2. max_tags_per_image: keep top N tags by confidence (0 = unlimited).
                            #
                            # We touch the result dict in place so existing callers that read
                            # `general_tags` / `character_tags` / `all_tags` see the same view.
                            filtered_tags = _apply_pre_tag_filters(
                                result["all_tags"],
                                blacklist=request.pre_tag_blacklist,
                                max_tags=request.max_tags_per_image,
                            )
                            # H: feed counter for post-run stats modal.
                            for t in filtered_tags or []:
                                if isinstance(t, dict):
                                    name = str(t.get("tag") or "").strip()
                                else:
                                    name = str(t or "").strip()
                                if name:
                                    top_tags_counter[name] += 1
                            entry = {
                                "image_id": img["id"],
                                "tags": filtered_tags,
                                "content_fingerprint": content_fingerprint,
                                "writer_provenance": (
                                    batch_writer_provenance.model_dump(mode="python")
                                    if batch_writer_provenance is not None
                                    and filtered_tags
                                    else None
                                ),
                            }
                            raw_scores = result.get("tag_scores")
                            if raw_scores:
                                # BE-1: the raw (pre-filter) score distribution
                                # rides the same transaction as the tag rows.
                                # Blacklist / max_tags shape only the ROWS —
                                # scores stay the audit truth of what the model
                                # actually saw.
                                entry["tag_scores"] = {
                                    "model": effective_model_name,
                                    "scores": raw_scores,
                                }
                            tags_batch.append(entry)
                            total_tagged += 1

                        total_processed += 1
                        processed_in_batch += 1
                        current_filename = os.path.basename(
                            img.get("_resolved_path") or img["path"]
                        )
                        send_with_eta(
                            f"{total_processed}/{total} ({total_tagged} tagged{f', {total_errors} failed' if total_errors else ''}) - {current_filename}",
                        )

                        if len(tags_batch) >= commit_interval:
                            worker_db.add_tags_batch(
                                tags_batch,
                                default_source="tagger",
                                replace_scope="pipeline",
                            )
                            tags_batch = []

                        if total_processed % gc_interval == 0:
                            gc.collect()
                            if cpu_pause_seconds > 0:
                                time.sleep(cpu_pause_seconds)
                except Exception as error:
                    logger.error(
                        "Error tagging batch starting at %s: %s", batch_start, error
                    )
                    remaining = len(existing_images) - processed_in_batch
                    total_errors += remaining
                    total_processed += remaining
                    send(
                        "running",
                        f"Processed {total_processed}/{total} ({total_tagged} tagged, {total_errors} failed)",
                    )

            if tags_batch:
                worker_db.add_tags_batch(
                    tags_batch, default_source="tagger", replace_scope="pipeline"
                )
                tags_batch = []

            del batch_images
            gc.collect()
            if cpu_pause_seconds > 0:
                time.sleep(cpu_pause_seconds)

        if cancel_event.is_set():
            send(
                "cancelled",
                f"Tagging cancelled. Processed {total_processed}/{total} images.",
                last_run_stats=_build_last_run_stats(
                    tagging_start_time,
                    total_processed,
                    total_tagged,
                    total_errors,
                    top_tags_counter,
                ),
            )
            return

        entry_stats_service.record_activity(
            entry_stats_service.KIND_TAGGED, total_processed
        )
        send(
            "done",
            f"Completed! Processed {total_processed} images: {total_tagged} tagged"
            + (f", {total_errors} failed." if total_errors else "."),
            last_run_stats=_build_last_run_stats(
                tagging_start_time,
                total_processed,
                total_tagged,
                total_errors,
                top_tags_counter,
            ),
        )
    except Exception as error:
        send("error", f"Error: {error}")
