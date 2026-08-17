"""The AI runtime lease must QUEUE across processes, not fail.

The gallery AI Tag job runs in a spawned child process, so the server's
in-process priority heap and the child's are different objects and cannot
order each other at all -- only the ``<temp>/ai-runtime.lock`` file lock
serializes them. On Windows that lock used ``msvcrt.locking(LK_LOCK)``, which
retries ten times at one-second intervals and then RAISES
``OSError(EDEADLK)``; measured at 9.1s, well inside one batch chunk (100
images on GPU, 12 on CPU). So during a gallery tag job, Censor detect,
similarity search, aesthetic scoring, artist identification and
``POST /api/tag/single`` failed intermittently instead of waiting their turn.

A single-process test cannot see any of this -- the in-process heap already
waits correctly -- so the decisive tests here spawn a REAL second process.
Smart Tag's thread-based contention is included as the control: it already
waited correctly and must keep doing so.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ai_runtime_guard as g  # noqa: E402
import config  # noqa: E402

BACKEND_DIR = str(Path(__file__).parent.parent)

# msvcrt LK_LOCK gave up at a measured 9.1s. A waiter that gets past this many
# seconds and still succeeds is doing something the old code could not do.
OLD_BLOCKING_FAILURE_SECONDS = 10.0

# Long enough that the old implementation is guaranteed to have raised.
HOLDER_SECONDS = 12.0

# Holders that the test kills itself once it has proven its point.
LONG_HOLD_SECONDS = 60.0


# A holder that takes the lease through the real public API. It reports its
# OWN pid because ``Popen.pid`` is not it: the Windows venv ``python.exe`` is a
# launcher that runs the base interpreter in a further child, so the pid the
# lease records and the pid the test spawned are different numbers.
_LEASE_HOLDER = """
import os, sys, time
sys.path.insert(0, os.environ["SD_TEST_BACKEND"])
import ai_runtime_guard as g
with g.exclusive_ai_runtime(os.environ["SD_TEST_LABEL"]):
    print("HELD %d" % os.getpid(), flush=True)
    time.sleep(float(os.environ["SD_TEST_HOLD"]))
print("RELEASED", flush=True)
"""

# A holder that takes the raw OS lock and writes whatever descriptor the test
# asks for. Uses only msvcrt/fcntl, never the module under test, so the
# on-disk byte layout is pinned independently of the implementation.
_RAW_HOLDER = r"""
import os, sys, time
handle = open(os.environ["SD_TEST_LOCK_PATH"], "a+b")
if os.fstat(handle.fileno()).st_size < 1:
    handle.write(b"\0")
    handle.flush()
if os.name == "nt":
    import msvcrt
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
handle.truncate(1)
payload = os.environ.get("SD_TEST_DESCRIPTOR", "")
if payload:
    handle.write(payload.encode("utf-8"))
handle.flush()
os.fsync(handle.fileno())
print("HELD %d" % os.getpid(), flush=True)
time.sleep(float(os.environ["SD_TEST_HOLD"]))
"""


def _os_scratch_root() -> Path:
    """A scratch root that is NOT inside ``data/``.

    Importing the backend redirects ``tempfile`` and TEMP/TMP/TMPDIR into
    ``data/tmp`` (``config.configure_runtime_temp_env``). That redirect is
    intended behaviour and is not touched here -- but these tests spawn real
    processes that take a real cross-process lock, and doing that inside the
    owner's ``data/`` tree would collide with other fixtures living there.
    """
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) / "Temp" if local_app_data else Path(tempfile.gettempdir())
    else:
        root = Path("/tmp")
    root = root.resolve()
    data_dir = Path(config.DATA_DIR).resolve()
    assert root.is_dir(), f"no usable OS temp root at {root}"
    assert root != data_dir and data_dir not in root.parents, (
        f"refusing to run cross-process lock tests inside the data directory: {root}"
    )
    return root


@pytest.fixture
def lock_dir(monkeypatch):
    """Point the lease at a private scratch directory and clean it up."""
    assert not g.AI_RUNTIME_LOCK_DISABLED, (
        "SD_IMAGE_SORTER_DISABLE_AI_RUNTIME_LOCK is set; these tests exercise "
        "the cross-process lock and would silently prove nothing"
    )
    root = _os_scratch_root()
    scratch = Path(tempfile.mkdtemp(prefix="sd-ai-lease-", dir=str(root)))
    monkeypatch.setattr(g, "get_temp_dir", lambda: str(scratch))
    try:
        yield scratch
    finally:
        assert scratch.parent == root  # only ever remove what mkdtemp made
        shutil.rmtree(scratch, ignore_errors=True)


class _Holder:
    """A child process holding the cross-process lock."""

    def __init__(self, process: subprocess.Popen, pid: int):
        self.process = process
        self.pid = pid  # the interpreter's own pid, not necessarily Popen.pid

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()
            self.process.wait(timeout=15)


def _start_holder(source: str, env_extra: dict, lock_dir: Path) -> _Holder:
    env = dict(os.environ)
    env["SD_TEST_BACKEND"] = BACKEND_DIR
    env["SD_IMAGE_SORTER_TMP_DIR"] = str(lock_dir)
    env.pop("SD_IMAGE_SORTER_DISABLE_AI_RUNTIME_LOCK", None)
    env.update({key: str(value) for key, value in env_extra.items()})
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = process.stdout.readline().strip()
    parts = line.split()
    if len(parts) != 2 or parts[0] != "HELD":
        holder = _Holder(process, process.pid)
        holder.stop()
        raise AssertionError(
            f"holder process did not take the lock (said {line!r}); "
            f"stderr: {process.stderr.read()[:600]}"
        )
    return _Holder(process, int(parts[1]))


def _assert_gate_is_clean() -> None:
    """A refused lease must not leave the in-process gate wedged."""
    assert g.get_ai_jobs_snapshot()["active"] == 0
    assert len(g._vram_gate._heap) == 0
    assert g._vram_gate._owner is None


def _dead_pid() -> int:
    """A pid that has certainly exited, confirmed rather than assumed."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        stdout=subprocess.PIPE,
        text=True,
    )
    pid = int(process.communicate(timeout=60)[0].strip())
    process.wait(timeout=60)
    deadline = time.monotonic() + 15
    while g._process_is_alive(pid) is not False and time.monotonic() < deadline:
        time.sleep(0.05)
    assert g._process_is_alive(pid) is False, f"pid {pid} did not exit"
    return pid


# --------------------------------------------------------------------------
# The defect: a lease behind another PROCESS must wait, then succeed.
# --------------------------------------------------------------------------


def test_interactive_lease_waits_out_a_batch_in_another_process_then_acquires(lock_dir):
    """The whole defect, reproduced across two processes.

    Before the fix this raised ``OSError: [Errno 36] Resource deadlock
    avoided`` after 9.1s. Asserting only "no OSError escaped" would be too
    weak -- the point is that the second acquirer WAITS and then gets the
    runtime, so the elapsed wait is asserted too.
    """
    holder = _start_holder(
        _LEASE_HOLDER,
        {"SD_TEST_LABEL": "gallery-tag", "SD_TEST_HOLD": HOLDER_SECONDS},
        lock_dir,
    )
    try:
        started = time.monotonic()
        with g.exclusive_ai_runtime("tag-single", priority=g.PRIORITY_INTERACTIVE):
            waited = time.monotonic() - started
            assert g.get_ai_jobs_snapshot()["vram_active"] == 1
    finally:
        holder.stop()

    assert waited > OLD_BLOCKING_FAILURE_SECONDS, (
        f"acquired after only {waited:.1f}s -- the holder was still running, so "
        "this did not exercise real contention"
    )
    assert waited < HOLDER_SECONDS + 10.0, f"waited far too long ({waited:.1f}s)"
    _assert_gate_is_clean()


def test_lock_descriptor_is_readable_by_a_waiter_while_the_lock_is_held(lock_dir):
    """Naming the blocker depends on this exact on-disk layout.

    Byte 0 is the locked range and is unreadable to anyone else (measured:
    EACCES on Windows), so the holder descriptor must live at byte 1 onwards.
    """
    holder = _start_holder(
        _LEASE_HOLDER,
        {"SD_TEST_LABEL": "smart-tag", "SD_TEST_HOLD": LONG_HOLD_SECONDS},
        lock_dir,
    )
    try:
        with open(lock_dir / "ai-runtime.lock", "rb") as handle:
            handle.seek(1)
            descriptor = json.loads(handle.read(4096).decode("utf-8"))
    finally:
        holder.stop()

    assert descriptor["pid"] == holder.pid
    assert descriptor["label"] == "smart-tag"
    assert isinstance(descriptor["started_at"], (int, float))


# --------------------------------------------------------------------------
# Exhaustion: a bounded wait that expires must say who is holding the runtime.
# --------------------------------------------------------------------------


def test_exhausted_wait_raises_busy_error_naming_the_holding_job(lock_dir, monkeypatch):
    monkeypatch.setattr(g, "_LOCK_WAIT_SECONDS", 1.0)
    holder = _start_holder(
        _LEASE_HOLDER,
        {"SD_TEST_LABEL": "gallery-tag", "SD_TEST_HOLD": LONG_HOLD_SECONDS},
        lock_dir,
    )
    try:
        with pytest.raises(g.AiRuntimeBusyError) as caught:
            with g.exclusive_ai_runtime("censor-detect"):
                pass
    finally:
        holder.stop()

    error = caught.value
    assert not isinstance(error, OSError), "callers cannot interpret a raw OSError"
    assert error.reason == g.REASON_BUSY
    assert error.blocker["scope"] == "process"
    assert error.blocker["pid"] == holder.pid
    assert error.blocker["label"] == "gallery-tag"
    assert error.blocker["holder_alive"] is True
    assert isinstance(error.blocker["elapsed_seconds"], (int, float))
    assert error.waited_seconds >= 1.0
    assert "gallery-tag" in str(error)
    _assert_gate_is_clean()


def test_exhausted_wait_distinguishes_a_lock_whose_owner_is_gone(lock_dir, monkeypatch):
    """A dead owner is NOT ordinary contention and must not say "wait".

    Both Windows ``LockFile`` and POSIX ``flock`` are released by the OS when
    the owning process dies, so a lock that is still held while its recorded
    owner is gone means something other than the claimed job is holding it.
    """
    monkeypatch.setattr(g, "_LOCK_WAIT_SECONDS", 1.0)
    descriptor = json.dumps(
        {"pid": _dead_pid(), "label": "gallery-tag", "started_at": time.time()}
    )
    holder = _start_holder(
        _RAW_HOLDER,
        {
            "SD_TEST_LOCK_PATH": str(lock_dir / "ai-runtime.lock"),
            "SD_TEST_DESCRIPTOR": descriptor,
            "SD_TEST_HOLD": LONG_HOLD_SECONDS,
        },
        lock_dir,
    )
    try:
        with pytest.raises(g.AiRuntimeBusyError) as caught:
            with g.exclusive_ai_runtime("similarity-search"):
                pass
    finally:
        holder.stop()

    error = caught.value
    assert error.reason == g.REASON_STALE_LOCK
    assert error.blocker["holder_alive"] is False
    assert "restart" in str(error).lower()
    _assert_gate_is_clean()


def test_exhausted_wait_without_a_descriptor_does_not_invent_a_blocker(
    lock_dir, monkeypatch
):
    """An unnamed holder is reported as unnamed, not as a guess."""
    monkeypatch.setattr(g, "_LOCK_WAIT_SECONDS", 1.0)
    holder = _start_holder(
        _RAW_HOLDER,
        {
            "SD_TEST_LOCK_PATH": str(lock_dir / "ai-runtime.lock"),
            "SD_TEST_DESCRIPTOR": "",
            "SD_TEST_HOLD": LONG_HOLD_SECONDS,
        },
        lock_dir,
    )
    try:
        with pytest.raises(g.AiRuntimeBusyError) as caught:
            with g.exclusive_ai_runtime("aesthetic-score"):
                pass
    finally:
        holder.stop()

    error = caught.value
    assert error.reason == g.REASON_BUSY
    assert error.blocker is None
    assert "another process" in str(error).lower()
    _assert_gate_is_clean()


def test_explicit_timeout_bounds_the_whole_admission_not_each_stage(
    lock_dir, monkeypatch
):
    """``timeout=`` is one budget shared by the gate and the file lock."""
    monkeypatch.setattr(g, "_LOCK_WAIT_SECONDS", LONG_HOLD_SECONDS)
    holder = _start_holder(
        _LEASE_HOLDER,
        {"SD_TEST_LABEL": "gallery-tag", "SD_TEST_HOLD": LONG_HOLD_SECONDS},
        lock_dir,
    )
    try:
        started = time.monotonic()
        with pytest.raises(g.AiRuntimeBusyError):
            with g.exclusive_ai_runtime("artist-identify", timeout=0.5):
                pass
        elapsed = time.monotonic() - started
    finally:
        holder.stop()

    assert elapsed < 5.0, (
        f"timeout=0.5 waited {elapsed:.1f}s -- the caller's budget was ignored "
        "in favour of the default cross-process bound"
    )
    _assert_gate_is_clean()


def test_lease_still_acquires_immediately_when_nothing_else_holds_the_lock(lock_dir):
    started = time.monotonic()
    with g.exclusive_ai_runtime("uncontended"):
        assert g.get_ai_jobs_snapshot()["vram_active"] == 1
    assert time.monotonic() - started < 5.0
    _assert_gate_is_clean()


# --------------------------------------------------------------------------
# Control: Smart Tag runs in a THREAD, so its contention was already correct.
# --------------------------------------------------------------------------


def test_thread_contention_still_waits_indefinitely_and_then_succeeds(lock_dir):
    """Smart Tag's shape. This always worked; it must keep working."""
    order = []
    order_lock = threading.Lock()
    holding = threading.Event()

    def batch():
        with g.exclusive_ai_runtime("smart-tag", priority=g.PRIORITY_BATCH):
            holding.set()
            time.sleep(1.5)
            with order_lock:
                order.append("smart-tag")

    worker = threading.Thread(target=batch)
    worker.start()
    assert holding.wait(10)

    started = time.monotonic()
    with g.exclusive_ai_runtime("tag-single", priority=g.PRIORITY_INTERACTIVE):
        waited = time.monotonic() - started
        with order_lock:
            order.append("tag-single")
    worker.join(20)

    assert order == ["smart-tag", "tag-single"], "the interactive lease jumped a held lease"
    assert waited >= 0.5, "the interactive lease did not actually wait for the batch"
    _assert_gate_is_clean()


def test_in_process_timeout_names_the_thread_that_is_blocking(lock_dir):
    holding = threading.Event()
    release = threading.Event()

    def batch():
        with g.exclusive_ai_runtime("smart-tag", priority=g.PRIORITY_BATCH):
            holding.set()
            release.wait(20)

    worker = threading.Thread(target=batch)
    worker.start()
    try:
        assert holding.wait(10)
        with pytest.raises(g.AiRuntimeBusyError) as caught:
            with g.exclusive_ai_runtime("tag-single", timeout=0.2):
                pass
    finally:
        release.set()
        worker.join(20)

    error = caught.value
    assert error.reason == g.REASON_BUSY
    assert error.blocker["scope"] == "thread"
    assert error.blocker["label"] == "smart-tag"
    assert error.blocker["priority"] == g.PRIORITY_BATCH
    assert "smart-tag" in str(error)
    _assert_gate_is_clean()
