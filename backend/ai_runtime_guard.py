"""Shared guardrails for heavy local AI runtimes.

The app can load several large models from different routes. Running them at the
same time is the common crash pattern: each individual job looks valid, but their
combined RAM/VRAM pressure can freeze or crash the machine. This module provides
a process-local and cross-process exclusive lease for model load/inference work.
"""
from __future__ import annotations

import errno
import heapq
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from config import get_temp_dir


logger = logging.getLogger(__name__)

AI_RUNTIME_LOCK_DISABLED = os.environ.get(
    "SD_IMAGE_SORTER_DISABLE_AI_RUNTIME_LOCK",
    "false",
).lower() in {"1", "true", "yes"}

# v3.3.0 PERF-2: tiered AI runtime scheduler.
#
# Two tiers replace the old single global lock:
#   - "vram" (DEFAULT): mutually exclusive across threads AND processes (in-process
#     priority gate + cross-process file lock). This is the original crash-prevention
#     behavior — loading/running several large models at once is the common
#     freeze/crash pattern, so VRAM work stays serialized. Existing callers that
#     pass no tier keep EXACTLY the previous semantics (zero behavior change).
#   - "cpu": a bounded concurrent pool for genuinely CPU-only work, so two CPU
#     jobs (or a CPU job and a VRAM job) can run at once instead of being
#     serialized behind the single global lock. Opt-in via tier="cpu".
#
# Reentrancy is preserved per tier so nested leases on the same thread do not
# deadlock (the VRAM gate tracks owner+depth; the CPU tier uses thread-local depth).
TIER_VRAM = "vram"
TIER_CPU = "cpu"
_VALID_TIERS = {TIER_VRAM, TIER_CPU}

# v3.3.2 Phase 1: priority + timeout + per-job VRAM estimate at the VRAM seam.
#
# Priority is an admission order for the exclusive VRAM tier ONLY (the CPU tier
# is a concurrent pool, so ordering there is meaningless). LOWER number = higher
# priority (a min-heap pops the smallest). Same-priority waiters stay FIFO via a
# monotonic sequence tiebreak, so equal-priority callers keep arrival order —
# strictly fairer than the previous plain RLock. The DEFAULT is NORMAL, which
# reproduces the prior fully-serialized behavior for every existing caller.
PRIORITY_INTERACTIVE = 0  # user is staring at the result (preview, single op, search)
PRIORITY_NORMAL = 50  # default — unchanged behavior for existing callers
PRIORITY_BATCH = 100  # background bulk work that should yield to everything else

# A held lease older than this is flagged ``stuck`` in the status snapshot. This
# is a diagnostic hint only (surfaced in the /api/system/ai-jobs badge); it does
# NOT cancel or cap anything.
_AI_JOB_STUCK_AFTER_SECONDS = max(
    1, int(os.environ.get("SD_IMAGE_SORTER_AI_JOB_STUCK_SECONDS", "180") or 180)
)

# Why a lease was refused. These are NOT interchangeable: "someone is working"
# and "the lock is held by something that is not the process that claimed it"
# need different answers from the user, so callers can branch on them.
REASON_BUSY = "busy"
REASON_STALE_LOCK = "stale_lock_holder_gone"


class AiRuntimeBusyError(RuntimeError):
    """Raised when a lease cannot be acquired before its wait bound expires.

    Carries enough structure for a caller to name the blocker instead of
    guessing: ``reason`` (see ``REASON_*``), ``blocker`` (the same
    ``label`` / ``elapsed_seconds`` vocabulary ``get_ai_jobs_snapshot`` already
    publishes through ``GET /api/system/ai-jobs``, plus ``pid`` when the holder
    is another process), and the wait budget that was spent.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str = REASON_BUSY,
        blocker: Optional[Dict[str, Any]] = None,
        waited_seconds: Optional[float] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.blocker = blocker or None
        self.waited_seconds = waited_seconds
        self.timeout_seconds = timeout_seconds


def _default_lock_wait_seconds() -> float:
    """How long the CROSS-PROCESS file lock may be waited for.

    The gallery AI Tag job runs in a spawned child process, so the in-process
    priority gate cannot order it against the server at all — only this file
    lock serializes them, and one batch holds it for a whole chunk (100 images
    on GPU, 12 on CPU). The bound therefore has to be long enough to cover a
    chunk or the wait is useless, and short enough that a caller eventually
    gets an answer instead of hanging forever.

    Default = ``_AI_JOB_STUCK_AFTER_SECONDS``. That constant is this module's
    own already-published definition of "a lease this old is abnormal" (it
    drives the ``stuck`` flag in ``GET /api/system/ai-jobs``), so reusing it
    means we never refuse a holder the app itself still considers to be working
    normally, and the two numbers cannot drift into contradicting each other.
    """
    raw = os.environ.get("SD_IMAGE_SORTER_AI_LOCK_WAIT_SECONDS", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            logger.debug(
                "Invalid SD_IMAGE_SORTER_AI_LOCK_WAIT_SECONDS=%r; using default", raw
            )
        else:
            if value > 0:
                return value
    return float(_AI_JOB_STUCK_AFTER_SECONDS)


_LOCK_WAIT_SECONDS = _default_lock_wait_seconds()

# Poll cadence for the non-blocking lock retry. Starts tight so a short lease
# hands over promptly, then backs off so a long batch chunk costs few syscalls.
_LOCK_POLL_MIN_SECONDS = 0.02
_LOCK_POLL_MAX_SECONDS = 0.25

# Byte 0 is the locked range and never carries information; the holder
# descriptor starts at byte 1 so a WAITER can still read it. Measured on
# Windows: a process that does not hold the lock gets EACCES reading byte 0 but
# reads byte 1 onwards fine, which is the only reason naming the blocker works.
_LOCK_BYTE_LENGTH = 1
_LOCK_DESCRIPTOR_MAX_BYTES = 4096

# "The byte range is already locked" as reported by each platform's
# non-blocking lock call. Measured on Windows: msvcrt LK_NBLCK raises EACCES
# (not EDEADLK — that one is specific to LK_LOCK giving up after 10 retries,
# and is kept here only so the legacy blocking mode would still read as
# contention). Anything outside this set is a real failure and must propagate.
_LOCK_CONTENTION_ERRNOS = frozenset(
    {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}
)


def _default_cpu_pool_size() -> int:
    raw = os.environ.get("SD_IMAGE_SORTER_AI_CPU_POOL", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.debug("Invalid SD_IMAGE_SORTER_AI_CPU_POOL=%r; using default", raw)
    # Leave headroom for the rest of the app; never below 1.
    return max(1, (os.cpu_count() or 2) - 1)


class _VramGate:
    """Fair, reentrant, priority-ordered in-process gate for the VRAM tier.

    Replaces a plain ``RLock`` so that higher-priority (interactive) jobs win the
    exclusive runtime ahead of queued bulk jobs, while same-priority waiters stay
    FIFO. With every caller at ``PRIORITY_NORMAL`` and ``timeout=None`` this
    behaves like the previous lock: fully serialized and reentrant per thread.
    The cross-process file lock (below) is layered on top by ``AiRuntimeLease``.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._owner: Optional[int] = None  # thread ident holding the gate, or None
        self._depth = 0  # reentrant depth for the owner
        self._heap: List[tuple] = []  # waiters: (priority, seq)
        self._seq = 0

    def acquire(self, priority: int, timeout: Optional[float]) -> bool:
        """Acquire the gate. Returns True if this was a nested (reentrant) entry.

        Raises ``AiRuntimeBusyError`` if ``timeout`` elapses before admission.
        """
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return True
            self._seq += 1
            ticket = (int(priority), self._seq)
            heapq.heappush(self._heap, ticket)
            deadline = None if timeout is None else time.monotonic() + timeout
            try:
                while self._owner is not None or self._heap[0] != ticket:
                    if deadline is None:
                        self._cond.wait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise AiRuntimeBusyError(
                                f"Timed out after {timeout:.1f}s waiting for the "
                                "exclusive AI runtime"
                            )
                        self._cond.wait(remaining)
            except BaseException:
                self._discard_locked(ticket)
                self._cond.notify_all()
                raise
            heapq.heappop(self._heap)  # remove our (head) ticket
            self._owner = me
            self._depth = 1
            return False

    def _discard_locked(self, ticket: tuple) -> None:
        try:
            self._heap.remove(ticket)
            heapq.heapify(self._heap)
        except ValueError:
            pass

    def release(self) -> None:
        """Release one level of the gate; frees the resource at the outermost."""
        me = threading.get_ident()
        with self._cond:
            if self._owner != me:
                return
            self._depth -= 1
            if self._depth <= 0:
                self._depth = 0
                self._owner = None
                self._cond.notify_all()


_vram_gate = _VramGate()

# CPU tier concurrency pool + per-thread reentrancy depth.
_CPU_POOL_SIZE = _default_cpu_pool_size()
_cpu_semaphore = threading.BoundedSemaphore(_CPU_POOL_SIZE)
_cpu_thread_local = threading.local()

# Active-job registry for the optional /api/system/ai-jobs status badge.
_jobs_lock = threading.Lock()
_active_jobs: Dict[int, Dict[str, Any]] = {}
_job_seq = 0


def _register_job(
    label: str,
    tier: str,
    priority: int = PRIORITY_NORMAL,
    vram_mb: Optional[int] = None,
) -> int:
    global _job_seq
    with _jobs_lock:
        _job_seq += 1
        job_id = _job_seq
        _active_jobs[job_id] = {
            "label": label,
            "tier": tier,
            "priority": int(priority),
            "vram_mb": vram_mb,
            "started_at": time.time(),
        }
        return job_id


def _unregister_job(job_id: int) -> None:
    with _jobs_lock:
        _active_jobs.pop(job_id, None)


def get_ai_jobs_snapshot() -> Dict[str, Any]:
    """Return a snapshot of in-flight AI runtime leases for a status badge."""
    now = time.time()
    with _jobs_lock:
        jobs: List[Dict[str, Any]] = [
            {
                "label": info["label"],
                "tier": info["tier"],
                "priority": info.get("priority", PRIORITY_NORMAL),
                "estimated_vram_mb": info.get("vram_mb"),
                "elapsed_seconds": round(max(0.0, now - info["started_at"]), 1),
                "stuck": (now - info["started_at"]) >= _AI_JOB_STUCK_AFTER_SECONDS,
            }
            for info in _active_jobs.values()
        ]
    jobs.sort(key=lambda j: j["elapsed_seconds"], reverse=True)
    vram = sum(1 for j in jobs if j["tier"] == TIER_VRAM)
    cpu = sum(1 for j in jobs if j["tier"] == TIER_CPU)
    vram_estimated_mb = sum(
        j["estimated_vram_mb"]
        for j in jobs
        if j["tier"] == TIER_VRAM and isinstance(j["estimated_vram_mb"], (int, float))
    )
    return {
        "active": len(jobs),
        "vram_active": vram,
        "cpu_active": cpu,
        "cpu_pool_size": _CPU_POOL_SIZE,
        "vram_estimated_mb": vram_estimated_mb,
        "stuck_after_seconds": _AI_JOB_STUCK_AFTER_SECONDS,
        "jobs": jobs,
    }



class AiRuntimeLease:
    """Exclusive (VRAM) or bounded-concurrent (CPU) lease for model work."""

    def __init__(
        self,
        label: str,
        tier: str = TIER_VRAM,
        *,
        priority: int = PRIORITY_NORMAL,
        timeout: Optional[float] = None,
        vram_mb: Optional[int] = None,
    ) -> None:
        self.label = str(label or "ai-runtime")
        self.tier = tier if tier in _VALID_TIERS else TIER_VRAM
        self._priority = int(priority)
        self._timeout = timeout
        self._vram_mb = vram_mb
        self._handle: Optional[BinaryIO] = None
        self._acquired = False
        self._nested = False
        self._job_id: Optional[int] = None

    def acquire(self) -> "AiRuntimeLease":
        if self._acquired:
            return self
        if self.tier == TIER_CPU:
            return self._acquire_cpu()
        return self._acquire_vram()

    def _acquire_cpu(self) -> "AiRuntimeLease":
        # Per-thread reentrancy: a nested CPU lease on the same thread must not
        # consume a second semaphore slot (would deadlock at pool size 1).
        depth = getattr(_cpu_thread_local, "depth", 0)
        if depth > 0:
            _cpu_thread_local.depth = depth + 1
            self._nested = True
            self._acquired = True
            return self
        started = time.monotonic()
        if self._timeout is None:
            _cpu_semaphore.acquire()
        elif not _cpu_semaphore.acquire(timeout=self._timeout):
            exc = AiRuntimeBusyError(
                f"Timed out after {self._timeout:.1f}s waiting for a CPU AI runtime slot"
            )
            self._enrich_busy_error(exc, TIER_CPU, time.monotonic() - started)
            raise exc
        _cpu_thread_local.depth = 1
        self._acquired = True
        self._job_id = _register_job(self.label, TIER_CPU, self._priority, self._vram_mb)
        logger.debug("Acquired AI runtime lease (cpu): %s", self.label)
        return self

    def _acquire_vram(self) -> "AiRuntimeLease":
        started = time.monotonic()
        # An explicit timeout is a budget for the WHOLE admission, so the gate
        # and the file lock share one deadline rather than each getting a full
        # timeout and doubling the caller's worst case.
        deadline = None if self._timeout is None else started + self._timeout

        # Win the in-process priority gate first (this is the serialization +
        # priority + timeout seam). Reentrant on the same thread.
        try:
            nested = _vram_gate.acquire(self._priority, self._timeout)
        except AiRuntimeBusyError as exc:
            self._enrich_busy_error(exc, TIER_VRAM, time.monotonic() - started)
            raise
        if nested:
            self._nested = True
            self._acquired = True
            return self

        # First (outermost) acquisition for this thread: take the cross-process
        # file lock unless globally disabled. Roll the gate back on any failure
        # so a raise here never wedges every other waiter.
        try:
            if not AI_RUNTIME_LOCK_DISABLED:
                self._acquire_cross_process_lock(started, deadline)
        except BaseException:
            _vram_gate.release()
            raise

        self._acquired = True
        self._job_id = _register_job(self.label, TIER_VRAM, self._priority, self._vram_mb)
        logger.debug("Acquired AI runtime lease (vram): %s", self.label)
        return self

    def _acquire_cross_process_lock(
        self, started: float, deadline: Optional[float]
    ) -> None:
        """Take ``<temp>/ai-runtime.lock``, waiting a bounded amount of time.

        Without a caller-supplied timeout the wait is bounded by
        ``_LOCK_WAIT_SECONDS`` rather than being unbounded: the holder is
        another PROCESS, so unlike the in-process gate we cannot see it make
        progress, and blocking a request forever is not an answer.
        """
        lock_path = Path(get_temp_dir()) / "ai-runtime.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_deadline = (
            started + _LOCK_WAIT_SECONDS if deadline is None else deadline
        )
        handle = lock_path.open("a+b")
        try:
            if not _lock_file(handle, lock_deadline):
                waited = time.monotonic() - started
                reason, blocker = _describe_cross_process_blocker(lock_path)
                logger.warning(
                    "AI runtime lease %r gave up after %.1fs (%s): %s",
                    self.label,
                    waited,
                    reason,
                    blocker,
                )
                raise AiRuntimeBusyError(
                    _busy_message(reason, blocker, waited),
                    reason=reason,
                    blocker=blocker,
                    waited_seconds=round(waited, 1),
                    timeout_seconds=lock_deadline - started,
                )
            _write_lock_descriptor(handle, self.label)
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def _enrich_busy_error(
        self, exc: AiRuntimeBusyError, tier: str, waited: float
    ) -> None:
        """Attach the in-process blocker to a gate/pool timeout.

        Done here rather than inside the gate so the gate never has to reach
        for the job registry while holding its own condition variable.
        """
        if exc.blocker is None:
            exc.blocker = _describe_local_blocker(tier)
        if exc.waited_seconds is None:
            exc.waited_seconds = round(waited, 1)
        if exc.timeout_seconds is None:
            exc.timeout_seconds = self._timeout
        if exc.blocker is not None:
            exc.args = (_busy_message(exc.reason, exc.blocker, waited),)

    def release(self) -> None:
        if not self._acquired:
            return
        if self.tier == TIER_CPU:
            self._release_cpu()
        else:
            self._release_vram()

    def _release_cpu(self) -> None:
        try:
            depth = getattr(_cpu_thread_local, "depth", 1)
            _cpu_thread_local.depth = max(0, depth - 1)
            if self._nested:
                self._nested = False
            else:
                _cpu_semaphore.release()
        finally:
            self._acquired = False
            if self._job_id is not None:
                _unregister_job(self._job_id)
                self._job_id = None
            logger.debug("Released AI runtime lease (cpu): %s", self.label)

    def _release_vram(self) -> None:
        try:
            # Drop the cross-process file lock only on the outermost lease; a
            # nested lease never took one.
            if not self._nested and self._handle is not None:
                try:
                    self._handle.seek(0)
                    self._handle.truncate()
                    self._handle.flush()
                    _unlock_file(self._handle)
                finally:
                    self._handle.close()
                    self._handle = None
        finally:
            self._nested = False
            self._acquired = False
            if self._job_id is not None:
                _unregister_job(self._job_id)
                self._job_id = None
            _vram_gate.release()
            logger.debug("Released AI runtime lease (vram): %s", self.label)

    def __enter__(self) -> "AiRuntimeLease":
        return self.acquire()

    def __exit__(self, *_args) -> bool:
        self.release()
        return False


def acquire_ai_runtime(
    label: str,
    tier: str = TIER_VRAM,
    *,
    priority: int = PRIORITY_NORMAL,
    timeout: Optional[float] = None,
    vram_mb: Optional[int] = None,
) -> AiRuntimeLease:
    """Acquire and return a heavy-runtime lease (default tier = exclusive VRAM).

    ``priority`` orders VRAM-tier admission (lower = sooner; see ``PRIORITY_*``).
    ``timeout`` (seconds) raises ``AiRuntimeBusyError`` if admission is not won in
    time; ``None`` (default) waits indefinitely, as before. ``vram_mb`` is an
    optional estimate surfaced in the status snapshot.
    """
    return AiRuntimeLease(
        label, tier, priority=priority, timeout=timeout, vram_mb=vram_mb
    ).acquire()


def exclusive_ai_runtime(
    label: str,
    tier: str = TIER_VRAM,
    *,
    priority: int = PRIORITY_NORMAL,
    timeout: Optional[float] = None,
    vram_mb: Optional[int] = None,
) -> AiRuntimeLease:
    """Context manager for heavy-runtime work (default tier = exclusive VRAM).

    See ``acquire_ai_runtime`` for ``priority`` / ``timeout`` / ``vram_mb``. With
    the defaults this is byte-for-byte the previous fully-serialized behavior.
    """
    return AiRuntimeLease(
        label, tier, priority=priority, timeout=timeout, vram_mb=vram_mb
    )


def _ensure_lock_byte(handle: BinaryIO) -> None:
    """Guarantee byte 0 exists so there is a range to lock.

    The handle is opened append-mode, so this write lands at EOF — which is
    byte 0 precisely when the file is empty. Skipped otherwise, because
    appending once per acquisition would grow the file without bound.
    """
    if os.fstat(handle.fileno()).st_size >= _LOCK_BYTE_LENGTH:
        return
    handle.write(b"\0")
    handle.flush()


def _try_lock_file(handle: BinaryIO) -> bool:
    """One NON-BLOCKING attempt. True if taken, False if someone else holds it.

    Any error that is not "already locked" propagates: a read-only volume or a
    bad descriptor is a broken environment, not a busy runtime, and must not be
    reported to the user as "wait your turn".
    """
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTE_LENGTH)
        except OSError as exc:
            if exc.errno in _LOCK_CONTENTION_ERRNOS:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _LOCK_CONTENTION_ERRNOS:
            return False
        raise
    return True


def _lock_file(handle: BinaryIO, deadline: float) -> bool:
    """Wait for the cross-process lock until ``deadline``. False if it expires.

    Replaces a single blocking call on both platforms. Windows
    ``msvcrt.locking(LK_LOCK)`` retried ten times at one-second intervals and
    then RAISED ``OSError(EDEADLK)`` — measured at 9.1s, far under one batch
    chunk — so an unrelated AI feature failed instead of queueing. POSIX
    ``flock(LOCK_EX)`` had the opposite flaw: it waited forever, with no way to
    answer a caller that supplied a timeout. One bounded poll loop fixes both.
    """
    _ensure_lock_byte(handle)
    interval = _LOCK_POLL_MIN_SECONDS
    while True:
        if _try_lock_file(handle):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))
        interval = min(interval * 2, _LOCK_POLL_MAX_SECONDS)


def _write_lock_descriptor(handle: BinaryIO, label: str) -> None:
    """Publish who holds the lock, at byte 1, where a waiter can read it."""
    payload = json.dumps(
        {"pid": os.getpid(), "label": label, "started_at": time.time()},
        ensure_ascii=True,
    ).encode("utf-8", errors="replace")[:_LOCK_DESCRIPTOR_MAX_BYTES]
    handle.truncate(_LOCK_BYTE_LENGTH)  # drop any previous holder's descriptor
    handle.write(payload)  # append-mode: lands at byte 1
    handle.flush()
    os.fsync(handle.fileno())


def _read_lock_descriptor(lock_path: Path) -> Optional[Dict[str, Any]]:
    """Read the current holder's descriptor, or None if it cannot be trusted.

    A descriptor written by an older build (which started at byte 0) parses as
    garbage here and correctly degrades to None rather than to a wrong name.
    """
    try:
        with open(lock_path, "rb") as handle:
            handle.seek(_LOCK_BYTE_LENGTH)
            raw = handle.read(_LOCK_DESCRIPTOR_MAX_BYTES)
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _process_is_alive(pid: Any) -> Optional[bool]:
    """True / False / None (undecidable) for whether ``pid`` is still running.

    Deliberately NOT ``os.kill(pid, 0)`` on Windows: CPython maps ``os.kill``
    there to ``TerminateProcess``, so the "probe" would kill the process it is
    asking about.
    """
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_value <= 0:
        return None

    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_timeout = 0x00000102
            error_access_denied = 5
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint32,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.OpenProcess(synchronize, 0, pid_value)
            if not handle:
                # Access denied means it exists but is out of reach; anything
                # else (invalid parameter) means there is no such process.
                return ctypes.get_last_error() == error_access_denied
            try:
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - a liveness hint must never break a lease
            logger.debug("Windows process liveness probe failed", exc_info=True)
            return None

    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _describe_local_blocker(tier: str) -> Optional[Dict[str, Any]]:
    """Name the in-process lease holding ``tier``, from the /api/system/ai-jobs
    snapshot — the source that already publishes label/elapsed/stuck."""
    for job in get_ai_jobs_snapshot()["jobs"]:  # already longest-running first
        if job["tier"] == tier:
            return {
                "scope": "thread",
                "label": job["label"],
                "priority": job["priority"],
                "elapsed_seconds": job["elapsed_seconds"],
                "stuck": job["stuck"],
            }
    return None


def _describe_cross_process_blocker(
    lock_path: Path,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Classify who is holding the cross-process lock.

    Three outcomes, kept apart on purpose:

    * a live holder that named itself -> BUSY, and we can say what it is;
    * a holder that did not name itself (descriptor missing, truncated, or
      written by an older build) -> BUSY, but say so generically rather than
      inventing a name;
    * the lock is held while the pid in the descriptor is verifiably GONE ->
      not ordinary contention. Both Windows ``LockFile`` and POSIX ``flock``
      are released by the OS when the owner dies, so a dead owner cannot be
      what is blocking us; something else is, and "wait for it" would be
      wrong advice.
    """
    descriptor = _read_lock_descriptor(lock_path)
    if not descriptor:
        return REASON_BUSY, None

    started_at = descriptor.get("started_at")
    elapsed: Optional[float] = None
    if isinstance(started_at, (int, float)):
        elapsed = round(max(0.0, time.time() - float(started_at)), 1)

    pid = descriptor.get("pid")
    alive = _process_is_alive(pid)
    blocker = {
        "scope": "process",
        "pid": pid if isinstance(pid, int) else None,
        "label": str(descriptor.get("label") or "") or None,
        "elapsed_seconds": elapsed,
        "holder_alive": alive,
    }
    if alive is False:
        return REASON_STALE_LOCK, blocker
    return REASON_BUSY, blocker


def _busy_message(reason: str, blocker: Optional[Dict[str, Any]], waited: float) -> str:
    """One short, path-free sentence naming the blocker.

    Kept under the 180-character ceiling that ``frontend/js/modules/utils/
    errors.js`` uses to discard messages, and free of file paths for the same
    reason — otherwise the only actionable part is thrown away before display.
    """
    label = (blocker or {}).get("label") or "Another AI job"
    elapsed = (blocker or {}).get("elapsed_seconds")
    running = f" (running {elapsed:.0f}s)" if isinstance(elapsed, (int, float)) else ""
    if reason == REASON_STALE_LOCK:
        return (
            f"The AI runtime is locked, but the job that claimed it ({label}) is no "
            f"longer running. Restart the app to clear the lock."
        )
    if blocker is None:
        return (
            f"Another process is using the AI runtime. Waited {waited:.0f}s. "
            f"Try again once it finishes."
        )
    return (
        f"{label} is using the AI runtime{running}. Waited {waited:.0f}s. "
        f"Try again when it finishes, or cancel it."
    )


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTE_LENGTH)
        except OSError:
            logger.debug("AI runtime Windows file unlock failed", exc_info=True)
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.debug("AI runtime POSIX file unlock failed", exc_info=True)


def cuda_has_headroom(torch_module, *, min_free_mb: int) -> bool:
    """Return True when CUDA exists and has enough free VRAM for another model."""
    try:
        if not torch_module.cuda.is_available():
            return False
        mem_get_info = getattr(torch_module.cuda, "mem_get_info", None)
        if not callable(mem_get_info):
            return True
        free_bytes, _total_bytes = mem_get_info(0)
        return (free_bytes / (1024 ** 2)) >= min_free_mb
    except Exception as exc:
        logger.debug("CUDA headroom check failed; allowing runtime to decide: %s", exc)
        return True


def clear_torch_cuda_cache(torch_module=None) -> None:
    """Best-effort CUDA cache release without importing torch unless needed."""
    try:
        if torch_module is None:
            import torch as torch_module  # type: ignore
        if torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
    except Exception:
        logger.debug("CUDA cache clear failed", exc_info=True)


def looks_like_cuda_oom(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "cuda out of memory",
            "cublas_status_alloc_failed",
            "cudnn_status_alloc_failed",
            "failed to allocate memory",
            "out of memory",
        )
    )
