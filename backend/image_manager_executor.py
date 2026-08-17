"""Metadata-executor lifecycle helpers for image_manager scans.

Moved verbatim from image_manager.py (decomposition 2026-07, stage 2) except the manifested facade-resolved
read-sites (marked ``# decomposition:``). The tunable constants
(SCAN_METADATA_EXECUTOR_MODE, SCAN_METADATA_BACKLOG_PER_WORKER,
SCAN_METADATA_MIN_BACKLOG, SCAN_DB_BATCH_SIZE) and the executor classes
(ThreadPoolExecutor / ProcessPoolExecutor) are read through the facade
module object at call time because the existing suite monkeypatches them
on ``image_manager`` — a ``from`` import here would freeze
independent bindings those patches silently miss (the sorting/scan.py
``image_manager_module.CONST`` precedent)."""

import logging
from collections.abc import MutableMapping
from typing import Any, Dict, Iterator, List

# NOTE(decomposition): keep the historical logger channel so log routing
# and output stay identical to the pre-split single-file module.
logger = logging.getLogger("image_manager")


def _facade():
    """Resolve patched seam names / tunable constants through the facade at call time.

    Lazy import: the facade imports this module at its own import time, so a
    module-level ``import image_manager`` here could observe a partially
    initialized module depending on import order."""
    import image_manager

    return image_manager


def _metadata_executor_mode() -> str:
    """Return the configured metadata worker isolation mode."""
    SCAN_METADATA_EXECUTOR_MODE = _facade().SCAN_METADATA_EXECUTOR_MODE  # decomposition: patched on image_manager
    if SCAN_METADATA_EXECUTOR_MODE in {"process", "processes", "isolated"}:
        return "process"
    if SCAN_METADATA_EXECUTOR_MODE in {"thread", "threads", "legacy"}:
        return "thread"
    logger.warning(
        "Unknown SD_IMAGE_SORTER_SCAN_METADATA_EXECUTOR=%r; using process isolation",
        SCAN_METADATA_EXECUTOR_MODE,
    )
    return "process"


def _create_metadata_executor(worker_count: int) -> Any:
    """Create the metadata worker executor. Process mode lets timeouts kill stuck C extensions."""
    worker_count = max(1, int(worker_count or 1))
    if _metadata_executor_mode() == "process":
        return _facade().ProcessPoolExecutor(max_workers=worker_count)  # decomposition: facade-resolved
    return _facade().ThreadPoolExecutor(max_workers=worker_count)  # decomposition: facade-resolved (patched on image_manager)


def _terminate_metadata_executor_workers(executor: Any) -> bool:
    """Best-effort terminate metadata workers after a hard timeout."""
    terminate_workers = getattr(executor, "terminate_workers", None)
    if callable(terminate_workers):
        terminate_workers()
        return True

    processes = getattr(executor, "_processes", None)
    if isinstance(processes, MutableMapping):
        terminated = False
        for process in list(processes.values()):
            if process is None:
                continue
            try:
                if process.is_alive():
                    process.terminate()
                    terminated = True
            except Exception as exc:
                logger.debug("Failed to terminate metadata worker process: %s", exc)

        for process in list(processes.values()):
            if process is None:
                continue
            try:
                process.join(timeout=1.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)
            except Exception as exc:
                logger.debug("Failed to join terminated metadata worker process: %s", exc)
        return terminated

    return False


def _shutdown_metadata_executor(executor: Any, *, wait_for_workers: bool = False) -> None:
    try:
        executor.shutdown(wait=wait_for_workers, cancel_futures=True)
    except TypeError:
        executor.shutdown(wait=wait_for_workers)
    except Exception as exc:
        logger.debug("Failed to shut down metadata executor cleanly: %s", exc)


def _metadata_job_for_retry(job: Dict[str, Any]) -> Dict[str, Any]:
    retry_job = dict(job)
    retry_job.pop("submitted_at", None)
    return retry_job


def _chunked(items: Iterator[Any], size: int) -> Iterator[List[Any]]:
    """Yield fixed-size batches from an iterator without buffering the full stream."""
    batch: List[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _metadata_backlog_limit(worker_count: int) -> int:
    """Return a bounded metadata queue size for large-library scans."""
    facade = _facade()  # decomposition: tunables are patched on image_manager
    per_worker_limit = max(1, int(worker_count or 1)) * max(1, int(facade.SCAN_METADATA_BACKLOG_PER_WORKER))
    return max(1, min(facade.SCAN_DB_BATCH_SIZE, max(facade.SCAN_METADATA_MIN_BACKLOG, per_worker_limit)))
