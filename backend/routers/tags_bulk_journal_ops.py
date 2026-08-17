"""Op-state singletons + journal/transaction helpers for the bulk tag router.

Decomposed from routers/tags_bulk.py (2026-07): a verbatim slice of the
pre-split lines 57-128, 455-483 and 500-681. Import routers.tags_bulk (the
facade), NOT this module -- the facade re-imports every name here BY
REFERENCE, so ``_op_lock`` / ``_op_run_lock`` / ``_op_state`` stay single
shared objects on both modules (tests/test_tags_bulk_pins.py
TestLockStatefulness; the overlap-409 reader acquires
``tags_bulk._op_run_lock`` while ``_run_exclusive`` gates on the object
defined HERE -- same Lock). ``_op_state`` has exactly ONE copy: this one.
NONE of the names defined here is monkeypatched by name on the facade -- the
patched trio (BULK_TAG_ID_CHUNK_SIZE / _preserve_row / _estimate_scope_total)
and ALL its callers stay in routers/tags_bulk.py.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, NoReturn

from fastapi import HTTPException

import database as db
from services import tag_bulk_journal
from routers.tags_bulk_models import (
    BulkOperationWarning,
    BulkTagScopeRequest,
    JournalApiResult,
)

# The moved bodies keep logging under the pre-split logger name so existing
# log filtering/handler config is unchanged (same pattern as
# oppai_oracle_loader.py -> "oppai_oracle_tagger").
logger = logging.getLogger("routers.tags_bulk")


_op_lock = threading.Lock()
_op_run_lock = threading.Lock()
_op_state: Dict[str, Any] = {
    "running": False,
    "operation": "",
    "total": 0,
    "completed": 0,
    "errors": [],
}


def _begin_op(name: str, total: int) -> None:
    """Mark a bulk op as started and reset progress counters.

    The separate run lock rejects overlapping operations before this
    state is reset. This lock only keeps counter mutations atomic for
    polling clients.
    """
    with _op_lock:
        _op_state.update({
            "running": True,
            "operation": name,
            "total": int(total),
            "completed": 0,
            "errors": [],
        })


def _bump_op_progress(delta: int = 1) -> None:
    with _op_lock:
        _op_state["completed"] = int(_op_state.get("completed") or 0) + int(delta)


def _record_op_error(image_id: int, message: str) -> None:
    with _op_lock:
        errors = _op_state.setdefault("errors", [])
        if len(errors) < 50:
            errors.append({"image_id": image_id, "error": message})


def _end_op() -> None:
    with _op_lock:
        _op_state["running"] = False


def _record_scope_estimate_failure(operation: str, detail: str) -> None:
    """Replace stale progress with the latest pre-mutation scope failure."""
    with _op_lock:
        _op_state.update({
            "running": False,
            "operation": operation,
            "total": 0,
            "completed": 0,
            "errors": [{"image_id": 0, "error": detail}],
        })


def _run_exclusive(operation: str, handler, request) -> Dict[str, Any]:
    """Run one bulk operation at a time to avoid read-modify-write tag loss."""
    if not _op_run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another bulk tag operation is already running. Wait for it to finish.",
        )
    try:
        return handler(request)
    finally:
        _op_run_lock.release()




def _scope_source(request: BulkTagScopeRequest) -> str:
    if request.image_ids is not None:
        return "image_ids"
    if request.selection_token:
        return "selection_token"
    return "filters"


def _confidence_from_row(row: Dict[str, Any]) -> float:
    """Return numeric confidence without treating a valid zero as missing."""
    raw_confidence: object = row.get("confidence")
    if raw_confidence is None:
        return 1.0
    if isinstance(raw_confidence, bool) or not isinstance(
        raw_confidence,
        (int, float, str),
    ):
        raise TypeError(
            "Tag confidence must be numeric; received "
            f"{type(raw_confidence).__name__}."
        )
    try:
        return float(raw_confidence)
    except ValueError as exc:
        raise ValueError(
            f"Tag confidence must be numeric; received {raw_confidence!r}."
        ) from exc


def _row_from_tuple(row) -> Dict[str, Any]:
    """(tag, confidence, source, category) from _dedupe_tag_rows -> row dict."""
    tag, confidence, source, category = row
    return {"tag": tag, "confidence": confidence, "source": source, "category": category}


def _create_journal_buffer() -> tag_bulk_journal.JournalBuffer:
    return tag_bulk_journal.create_journal_buffer(
        tag_bulk_journal.MAX_JOURNAL_IMAGES,
        tag_bulk_journal.MAX_JOURNAL_SERIALIZED_BYTES,
    )


def _append_journal_entry(
    journal: tag_bulk_journal.JournalBuffer,
    image_id: int,
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
) -> None:
    tag_bulk_journal.append_journal_entry(
        journal,
        image_id,
        before_rows,
        after_rows,
    )


def _record_journal_if_applied(
    request: BulkTagScopeRequest,
    operation: str,
    params: Dict[str, Any],
    journal: tag_bulk_journal.JournalBuffer,
    images_affected: int,
) -> JournalApiResult:
    """Journal a committed operation and expose lost undo as a warning."""
    if request.dry_run or images_affected <= 0:
        return {"op_id": None, "undo_available": False, "warnings": []}
    try:
        record_result = tag_bulk_journal.record_op(
            operation=operation,
            scope_source=_scope_source(request),
            params=params,
            journal=journal,
            images_affected=images_affected,
        )
        if record_result.truncated:
            if record_result.truncation_reason == "serialized_byte_limit":
                limit_description = (
                    f"the {journal.max_serialized_bytes}-byte serialized-data limit"
                )
            else:
                limit_description = f"the {journal.max_images}-image limit"
            warning: BulkOperationWarning = {
                "code": "undo_journal_truncated",
                "message": (
                    "Tags were applied, but undo is unavailable because the journal "
                    f"exceeded {limit_description}."
                ),
            }
            return {
                "op_id": record_result.op_id,
                "undo_available": False,
                "warnings": [warning],
            }
        return {
            "op_id": record_result.op_id,
            "undo_available": True,
            "warnings": [],
        }
    except Exception as exc:
        logger.warning(
            "bulk undo journal record failed",
            extra={
                "operation": operation,
                "images_affected": images_affected,
                "error_type": type(exc).__name__,
            },
            exc_info=exc,
        )
        warning = {
            "code": "undo_journal_persistence_failed",
            "message": (
                "Tags were applied, but undo is unavailable because the journal "
                f"could not be saved. Cause: {type(exc).__name__}: {exc}"
            ),
        }
        return {"op_id": None, "undo_available": False, "warnings": [warning]}


def _commit_tag_updates(
    write_updates: Callable[[List[Dict[str, Any]]], None],
    updates: List[Dict[str, Any]],
) -> None:
    if not updates:
        return
    image_ids = [int(item["image_id"]) for item in updates]
    first_image_id = image_ids[0]
    try:
        write_updates(updates)
    except Exception as exc:
        detail = (
            f"Bulk tag update failed for {len(image_ids)} image(s) beginning with "
            f"image_id={first_image_id}; all changes were rolled back. Cause: "
            f"{type(exc).__name__}: {exc}"
        )
        _record_op_error(first_image_id, detail)
        logger.exception(
            "bulk tag transaction write failed",
            extra={
                "image_count": len(image_ids),
                "first_image_id": first_image_id,
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=500, detail=detail) from exc


def _raise_bulk_preparation_error(
    operation: str,
    image_id: int,
    dry_run: bool,
    exc: Exception,
) -> NoReturn:
    """Abort a logical bulk operation when one image cannot be prepared."""
    outcome = (
        "no changes were applied"
        if dry_run
        else "all changes were rolled back"
    )
    detail = (
        f"Bulk tag {operation} failed while preparing image_id={image_id}; "
        f"{outcome}. Cause: {type(exc).__name__}: {exc}"
    )
    _record_op_error(image_id, detail)
    logger.exception(
        "bulk tag operation preparation failed",
        extra={
            "operation": operation,
            "image_id": image_id,
            "dry_run": dry_run,
            "error_type": type(exc).__name__,
        },
    )
    raise HTTPException(status_code=500, detail=detail) from exc


@contextmanager
def _bulk_tag_transaction(
    operation: str,
    dry_run: bool,
) -> Iterator[Callable[[List[Dict[str, Any]]], None]]:
    """Expose transaction lifecycle failures as actionable API errors."""
    try:
        with db.tag_update_transaction(
            default_source=None,
            replace_scope="all",
        ) as write_updates:
            yield write_updates
    except HTTPException:
        raise
    except Exception as exc:
        outcome = (
            "no changes were applied"
            if dry_run
            else "all changes were rolled back"
        )
        detail = (
            f"Bulk tag {operation} transaction failed; {outcome}. "
            f"Cause: {type(exc).__name__}: {exc}"
        )
        _record_op_error(0, detail)
        logger.exception(
            "bulk tag transaction failed",
            extra={
                "operation": operation,
                "dry_run": dry_run,
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=500, detail=detail) from exc
