"""Write-through persistence + restore surface of the tagging pipeline.

Split out of ``services/tagging_pipeline_service.py`` (2026-07). ``_TaggingPipelinePersistenceMixin``
is assembled into ``TaggingPipelineService`` by the facade; the serialize
helpers (_serialize_payload / _deserialize_payload / _serialize_queue_entry)
moved here with their only callers.

The ONLY non-verbatim edits in this module (see the split manifest): reads
of facade module-scope names -- ``_start_lock`` (the ONE shared
cross-service start lock), the KIND_* constants, ``_utc_now_iso``,
``_fingerprint`` and the ``_QueuedPipelineJob`` dataclass -- resolve
through ``_svc()`` at call time, so the shared-lock identity and the
facade monkeypatch surface are
preserved with a single definition each and no facade<->submodule load
cycle. Behavior invariants stay verbatim: best-effort persistence NEVER
raises into a queue mutation, and the function-local lazy
``from services.tagging_service import TagRequest`` import stays inside
``_deserialize_payload``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from services import ai_job_queue_store

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from services.tagging_pipeline_service import _QueuedPipelineJob


logger = logging.getLogger("services.tagging_pipeline_service")  # historical channel preserved (campaign rule)


def _svc():
    """Resolve facade module-scope seams through services.tagging_pipeline_service at call time.

    ``_start_lock`` must stay ONE object at the facade module scope; resolving it -- and the
    KIND_* constants, _utc_now_iso, _fingerprint, _QueuedPipelineJob --
    lazily keeps a single definition on the facade module (the historical
    patch surface) and avoids a facade<->submodule load cycle.
    """
    import services.tagging_pipeline_service as tagging_pipeline_service

    return tagging_pipeline_service


def _serialize_payload(payload: Any) -> Any:
    """Reduce a queue entry's payload to JSON-serializable request data.

    Gallery payloads are ``TagRequest`` pydantic models; Smart Tag / VLM
    payloads are already plain dicts.
    """
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload


def _deserialize_payload(kind: str, data: Dict[str, Any]) -> Any:
    """Rebuild an entry payload from persisted request data.

    Raises when the persisted gallery payload no longer validates so the
    caller can skip the entry instead of restoring a malformed job.
    """
    if kind == _svc().KIND_GALLERY:
        from services.tagging_service import TagRequest

        return TagRequest(**data)
    return dict(data)


def _serialize_queue_entry(entry: "_QueuedPipelineJob", *, running: bool) -> Dict[str, Any]:
    """On-disk form of a queue entry (request-shaped data only)."""
    return {
        "queue_id": entry.queue_id,
        "kind": entry.kind,
        "payload": _serialize_payload(entry.payload),
        "enqueued_at": entry.enqueued_at,
        "running": bool(running),
    }


class _TaggingPipelinePersistenceMixin:
    """Persistence (write-through + restore) surface of TaggingPipelineService (facade-assembled)."""
    # ------------------------------------------------------------------
    # Persistence (write-through + restore)
    # ------------------------------------------------------------------

    def _persist_state_locked(self) -> None:
        """Write the running entry (if any) + the queue through to disk.

        Caller holds ``_start_lock``. Best-effort: a persistence failure must
        never surface into the queue mutation that triggered it.
        """
        try:
            entries: List[Dict[str, Any]] = []
            if self._running_entry is not None:
                entries.append(_serialize_queue_entry(self._running_entry, running=True))
            entries.extend(_serialize_queue_entry(item, running=False) for item in self._queue)
            ai_job_queue_store.write_queue_state(entries)
        except Exception:  # noqa: BLE001 — persistence must never break a queue mutation
            logger.exception("Failed to persist AI job queue state")

    def _restore_persisted_queue(self) -> None:
        """Load the persisted queue on startup (best-effort).

        The RUNNING-at-shutdown entry is persisted first and restored as a
        queued entry at HEAD (it cannot resume mid-flight). Entries that fail
        validation are skipped; a corrupt/unreadable file yields an empty
        queue. When anything restores and auto-dispatch is on, the dispatcher
        is started so the queue resumes draining.
        """
        try:
            raw_entries = ai_job_queue_store.read_queue_state()
        except Exception:  # noqa: BLE001 — a bad file must never block construction
            logger.exception("Failed to read the persisted AI job queue; starting empty")
            return
        if not raw_entries:
            return

        restored: List[_QueuedPipelineJob] = []
        max_seq = 0
        for data in raw_entries:
            parsed = self._deserialize_persisted_entry(data)
            if parsed is None:
                continue
            entry, seq = parsed
            # Same consecutive-duplicate collapse as _enqueue_locked. Because
            # the running entry is first, a running job identical to the first
            # still-queued job merges here.
            if restored and entry.fingerprint and restored[-1].fingerprint == entry.fingerprint:
                continue
            restored.append(entry)
            max_seq = max(max_seq, seq)

        if not restored:
            return

        with _svc()._start_lock:
            self._queue = restored
            self._queue_seq = max(self._queue_seq, max_seq)
            # Persist the normalized (running-collapsed-into-queued) form so the
            # file matches the in-memory queue immediately after restore.
            self._persist_state_locked()
            self._ensure_dispatcher_locked()
        logger.info(
            "Restored %d persisted AI job queue entr%s",
            len(restored),
            "y" if len(restored) == 1 else "ies",
        )

    def _deserialize_persisted_entry(self, data: Any) -> Optional[Tuple["_QueuedPipelineJob", int]]:
        """Rebuild one queue entry from persisted data, or None if invalid."""
        try:
            if not isinstance(data, dict):
                raise ValueError("entry is not an object")
            kind = data.get("kind")
            if kind not in (_svc().KIND_GALLERY, _svc().KIND_SMART, _svc().KIND_VLM):
                raise ValueError(f"unknown kind {kind!r}")
            raw_payload = data.get("payload")
            if not isinstance(raw_payload, dict):
                raise ValueError("payload is not an object")
            payload = _deserialize_payload(kind, raw_payload)

            queue_id = str(data.get("queue_id") or "").strip()
            if queue_id.startswith("q") and queue_id[1:].isdigit():
                seq = int(queue_id[1:])
            else:
                self._queue_seq += 1
                queue_id = f"q{self._queue_seq}"
                seq = self._queue_seq

            enqueued_at = str(data.get("enqueued_at") or "").strip() or _svc()._utc_now_iso()
            entry = _svc()._QueuedPipelineJob(
                queue_id=queue_id,
                kind=kind,
                payload=payload,
                legacy_service=None,
                loop=None,
                fingerprint=_svc()._fingerprint(kind, payload),
                enqueued_at=enqueued_at,
            )
            return entry, seq
        except Exception as exc:  # noqa: BLE001 — one bad entry never blocks restore
            logger.warning("Skipping invalid persisted AI job queue entry: %s", exc)
            return None

