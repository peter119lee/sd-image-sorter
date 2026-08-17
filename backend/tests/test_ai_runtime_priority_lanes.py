"""Admission-order tests for the interactive AI priority lane at its call sites.

Every assertion here is an ADMISSION ORDER outcome on the real ``_VramGate``,
never "the call site passed a keyword argument". The two are not equivalent:

* a signature check passes while the lane is still entirely broken, and
* a signature check also passes for the specific WRONG fix this slice had to
  avoid -- pinning ``PRIORITY_INTERACTIVE`` on a leaf that the batch loop also
  calls, which hands the batch interactive priority and inverts the whole point
  of the lane.

The race harness always enqueues the waiter that must LOSE first, so plain
FIFO arrival order favours it. Any other admission order is therefore caused by
a declared priority rather than by timing, and the "shared leaf" tests below
fail under both the un-threaded status quo and the naive leaf-pinned fix.

Scope, stated honestly: the lane orders THREADS. The in-process heap is the
only layer that has a queue -- the cross-process file lock deliberately has
none, so waiters there race a poll loop. These tests therefore disable the file
lock and stay on the layer under test. Nothing here asserts a cross-process
ordering, because the guard does not provide one.

No test in this module reads or writes ``data/``: the file lock (whose path
resolves under ``config.get_temp_dir()``) is disabled, and the one scratch PNG
lives in a ``mkdtemp`` directory under the real OS temp root.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import ai_runtime_guard as g


# ---------------------------------------------------------------------------
# Scratch image, kept off data/
# ---------------------------------------------------------------------------

def _real_temp_root() -> str:
    """The OS temp root, not the ``data/tmp`` redirect the backend installs.

    Importing the backend points ``tempfile`` at ``data/tmp`` via
    ``config.configure_runtime_temp_env``. That is intended and must not be
    changed, but this module has no reason to write inside the owner's data
    directory, so the scratch dir is resolved independently.
    """
    for key in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
        base = os.environ.get(key)
        if not base:
            continue
        candidate = Path(base) / "Temp" if key == "LOCALAPPDATA" else Path(base)
        if candidate.is_dir():
            return str(candidate)
    return tempfile.gettempdir()


@pytest.fixture(scope="module")
def scratch_png() -> str:
    """A tiny real PNG the production paths can actually open.

    Torn down file-by-file with ``unlink`` + ``rmdir``: never a recursive force
    delete against a variable-built path.
    """
    root = _real_temp_root()
    scratch = Path(tempfile.mkdtemp(prefix="sdis-ai-lane-", dir=root))
    png = scratch / "lane-probe.png"
    Image.new("RGB", (16, 16), (90, 110, 130)).save(png)
    try:
        yield str(png)
    finally:
        png.unlink(missing_ok=True)
        if scratch.is_dir() and scratch.parent == Path(root):
            os.rmdir(scratch)


# ---------------------------------------------------------------------------
# Race harness
# ---------------------------------------------------------------------------

class _Race:
    """Collects the order in which racing threads were admitted to the gate."""

    def __init__(self) -> None:
        self.order: List[str] = []
        self.failures: List[str] = []
        self._lock = threading.Lock()
        self._names = threading.local()

    def bind(self, name: str) -> None:
        self._names.value = name

    def note(self) -> None:
        name = getattr(self._names, "value", None)
        if name is None:
            return
        with self._lock:
            self.order.append(name)

    def fail(self, detail: str) -> None:
        with self._lock:
            self.failures.append(detail)


_ACTIVE_RACE: Optional[_Race] = None


def note_admission() -> None:
    """Called by each stub standing in for model work, from INSIDE the lease."""
    if _ACTIVE_RACE is not None:
        _ACTIVE_RACE.note()


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def admission_order(
    *waiters: Tuple[str, Callable[[], None]],
    timeout: float = 20.0,
) -> List[str]:
    """Queue each ``(name, driver)`` IN THE GIVEN ORDER behind a held lease.

    The holder takes a plain ``PRIORITY_NORMAL`` lease and keeps it until every
    waiter is parked on the gate, so arrival order favours the earlier entries.
    Whatever order comes back is the priority lane's doing, not the scheduler's.
    """
    global _ACTIVE_RACE
    race = _Race()
    _ACTIVE_RACE = race
    threads: List[threading.Thread] = []
    gate = g._vram_gate

    def make(name: str, driver: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            race.bind(name)
            try:
                driver()
            except BaseException as exc:  # noqa: BLE001 - reported, never hidden
                race.fail(f"{name} raised {exc!r}")

        return run

    try:
        with g.exclusive_ai_runtime("race-holder", priority=g.PRIORITY_NORMAL):
            for index, (name, driver) in enumerate(waiters, start=1):
                thread = threading.Thread(target=make(name, driver), daemon=True)
                thread.start()
                threads.append(thread)
                assert _wait_until(
                    lambda expected=index: len(gate._heap) >= expected
                ), (
                    f"waiter {name!r} never reached the AI runtime gate"
                    f" (failures so far: {race.failures})"
                )
        for thread in threads:
            thread.join(timeout)
    finally:
        _ACTIVE_RACE = None

    assert not race.failures, f"racing waiters failed: {race.failures}"
    assert not [t for t in threads if t.is_alive()], "a racing waiter never finished"
    return list(race.order)


def probe(priority: int) -> Callable[[], None]:
    """A control waiter that declares ``priority`` and nothing else."""

    def driver() -> None:
        with g.exclusive_ai_runtime("lane-probe", priority=priority):
            note_admission()

    return driver


@pytest.fixture(autouse=True)
def in_process_gate_only(monkeypatch):
    """Isolate the in-process priority gate; keep the file lock out of ``data/``."""
    monkeypatch.setattr(g, "AI_RUNTIME_LOCK_DISABLED", True)
    yield
    assert not g._vram_gate._heap, "a test leaked a waiter onto the gate"
    assert g.get_ai_jobs_snapshot()["active"] == 0, "a test leaked a held lease"


# ---------------------------------------------------------------------------
# Leaf lanes: interactive search vs the batch embedding indexer
# ---------------------------------------------------------------------------

class _FakeEmbedder:
    """Stands in for a loaded FastEmbed model; notes admission from inside."""

    def embed(self, items):
        note_admission()
        return [np.zeros(512, dtype=np.float32) for _ in list(items)]


def test_interactive_text_search_is_admitted_before_the_batch_embedding_indexer(
    monkeypatch, scratch_png
):
    """A semantic search must not queue behind the CLIP library indexer.

    ``embed_text`` is only ever reached by ``search_by_text``; ``embed_image_file``
    is only ever reached by ``SimilarityIndex.embed_batch``, the background
    whole-library indexer. Nothing else calls either, so both lanes are safe to
    declare at the leaf.
    """
    import similarity

    model = _FakeEmbedder()
    monkeypatch.setattr(similarity, "_get_text_embed_model", lambda: model)

    def batch_indexer() -> None:
        similarity.embed_image_file(scratch_png, model=model)

    def text_search() -> None:
        similarity.embed_text("a girl standing in the rain")

    order = admission_order(
        ("batch-embed", batch_indexer),
        ("text-search", text_search),
    )
    assert order == ["text-search", "batch-embed"]


# ---------------------------------------------------------------------------
# The inversion hazard: one leaf, an interactive caller AND a batch caller
# ---------------------------------------------------------------------------

def _stub_wd14_tagger():
    """A real ``WD14Tagger`` with only its model work replaced.

    The lease in ``_InferenceFlowMixin.tag`` is the line under test, so it must
    stay real; everything it wraps (session, preprocessing, scoring) is stubbed.
    """
    from tagger import WD14Tagger

    tagger = object.__new__(WD14Tagger)
    tagger._loaded = True
    tagger.model_name = "lane-test-model"
    tagger._preprocess = lambda image: np.zeros((3, 8, 8), dtype=np.float32)

    def _run_inference(_input_data):
        note_admission()
        return np.zeros((1, 4), dtype=np.float32)

    tagger._run_inference = _run_inference
    tagger._process_probs = lambda _probs, **_kwargs: {
        "general_tags": [],
        "character_tags": [],
        "copyright_tags": [],
        "rating": "general",
        "rating_confidences": {},
        "all_tags": [],
    }
    tagger._finalize_processed_images = lambda _count: None
    return tagger


def test_smart_tag_booru_phase_does_not_take_the_interactive_lane(
    monkeypatch, scratch_png
):
    """``POST /api/tag/single`` must beat Smart Tag's WD14 phase at the SAME leaf.

    Both callers reach ``_InferenceFlowMixin.tag``:
    ``single_image_tag_service.tag_single_image`` (one file, user waiting) and
    ``services/smart_tag/tagging._tag_image_with_thresholds`` (a job over many
    files). Pinning the lane inside ``tag`` would give Smart Tag's whole caption
    run interactive priority, so the priority has to arrive as an argument.

    Fails under the un-threaded status quo AND under a leaf pin: in both of
    those the two callers declare the same priority, so the batch waiter --
    queued first -- wins on FIFO.
    """
    from services import single_image_tag_service
    from services.smart_tag import tagging as smart_tag_tagging

    tagger = _stub_wd14_tagger()
    monkeypatch.setattr(single_image_tag_service, "_load_tagger", lambda **_k: tagger)

    def smart_tag_phase() -> None:
        smart_tag_tagging._tag_image_with_thresholds(
            tagger,
            scratch_png,
            general_threshold=0.35,
            character_threshold=0.85,
            copyright_threshold=0.35,
        )

    def single_image_endpoint() -> None:
        single_image_tag_service.tag_single_image(
            single_image_tag_service.SingleImageTagRequest(image_path=scratch_png)
        )

    order = admission_order(
        ("smart-tag-phase", smart_tag_phase),
        ("single-image-tag", single_image_endpoint),
    )
    assert order == ["single-image-tag", "smart-tag-phase"]


def test_batch_mask_refinement_does_not_take_the_interactive_lane(scratch_png):
    """SAM3 text segmentation must beat ``POST /api/censor/batch-refine-mask``.

    ``_run_segmentation`` is private and shared four ways. ``segment_by_text``
    and ``detect_privacy_regions`` are only ever driven by a single-image
    request, but ``refine_box`` is the method
    ``services/censor/sam3_ops.batch_refine_mask`` calls once per item in a
    sequential loop -- so the lane cannot be pinned inside ``_run_segmentation``.
    """
    from sam3_refiner import SAM3Refiner

    class _Inputs(dict):
        def to(self, _device):
            return self

    class _Processor:
        def __call__(self, **_kwargs):
            return _Inputs()

        def post_process_instance_segmentation(self, *_args, **_kwargs):
            return []

    class _Model:
        def __call__(self, **_kwargs):
            note_admission()
            return object()  # no presence_logits -> no early presence return

    refiner = SAM3Refiner()
    refiner._processor = _Processor()
    refiner._model = _Model()

    with Image.open(scratch_png) as handle:
        image = handle.convert("RGB")

    def batch_refine() -> None:
        refiner.refine_box(image, [0, 0, 8, 8])

    def text_segment() -> None:
        refiner.segment_by_text(image, "a hat")

    order = admission_order(
        ("batch-refine", batch_refine),
        ("text-segment", text_segment),
    )
    assert order == ["text-segment", "batch-refine"]


class _FakeAestheticService:
    """Minimal stand-in that just invokes whatever ``predict_score`` it is given.

    The point under test is which callable each ROUTER hands to the service, so
    the service body is irrelevant and the real ``aesthetic.predict_score`` (with
    its real lease) has to be the thing that runs.
    """

    def __init__(self, image_path: str) -> None:
        self._image_path = image_path

    def score_single_image(self, *, image_id, predict_score):
        predict_score(self._image_path)
        return {"image_id": image_id, "aesthetic_score": 5.5}

    def score_batch(self, *, force, predict_score, progress_callback=None):
        predict_score(self._image_path)

    def apply_scoring_progress_update(self, _update):
        return None

    def finish_scoring_progress(self, error=None):
        return None


def test_aesthetic_batch_scoring_does_not_take_the_interactive_lane(
    monkeypatch, scratch_png
):
    """Scoring one image must beat the running score-all job at the same leaf.

    ``routers/aesthetic.score_single_image`` and ``routers/aesthetic._score_batch``
    both import the very same ``aesthetic.predict_score``.
    """
    import aesthetic
    from routers import aesthetic as aesthetic_router

    monkeypatch.setattr(aesthetic, "_ensure_loaded", lambda *_a, **_k: None)

    def _predict_score_loaded(_path):
        note_admission()
        return 5.5

    monkeypatch.setattr(aesthetic, "_predict_score_loaded", _predict_score_loaded)

    service = _FakeAestheticService(scratch_png)
    monkeypatch.setattr(aesthetic_router, "get_aesthetic_service", lambda: service)

    def score_all_job() -> None:
        aesthetic_router._score_batch(False)

    def score_one_image() -> None:
        aesthetic_router.score_single_image(image_id=1, service=service)

    order = admission_order(
        ("score-all-job", score_all_job),
        ("score-one-image", score_one_image),
    )
    assert order == ["score-one-image", "score-all-job"]


def _stub_artist_identifier():
    """A real ``ArtistIdentifier`` with only its ONNX session replaced."""
    from artist_identifier import ArtistIdentifier

    class _Input:
        name = "pixel_values"

    class _Session:
        def get_inputs(self):
            return [_Input()]

        def run(self, _outputs, _feed):
            note_admission()
            return [np.array([[3.0, 1.0]], dtype=np.float32)]

    identifier = object.__new__(ArtistIdentifier)
    identifier._session = _Session()
    identifier._model = object()
    identifier._backend = "onnx"
    identifier._has_class_mapping = True
    identifier._artist_lookup = None
    identifier.artists = ["artist_alpha", "artist_beta"]
    identifier.threshold = 0.2
    identifier.load = lambda: None
    return identifier


def test_artist_batch_identification_does_not_take_the_interactive_lane(
    monkeypatch, scratch_png
):
    """Identifying one image must beat the artist batch job at the same leaf.

    ``ArtistService.identify_image`` and ``ArtistService.run_batch_identification``
    both call ``identify_with_threshold``, which reaches the same three inference
    leases (``_run_onnx`` / ``_run_kaloscope`` / ``_run_torch_classifier``).
    """
    import services.artist_service as artist_service_module
    from services.artist_service import ArtistService

    identifier = _stub_artist_identifier()
    service = ArtistService(identifier_getter=lambda **_k: identifier)

    monkeypatch.setattr(service, "_get_image_path", lambda *_a, **_k: scratch_png)
    monkeypatch.setattr(
        service, "_prepare_source_fingerprint", lambda *_a, **_k: "fingerprint"
    )
    monkeypatch.setattr(service, "_verify_source_fingerprint", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_store_prediction", lambda *_a, **_k: True)
    monkeypatch.setattr(
        artist_service_module,
        "resolve_existing_indexed_image_path",
        lambda *_a, **_k: scratch_png,
    )

    class _Cursor:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return [(1, scratch_png)]

    class _Conn:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(artist_service_module.db, "get_db", lambda: _Conn())
    # Let the batch loop finish cleanly; a swallowed publish error would still
    # record admission, but it would leave an unrelated failure in the log and
    # obscure why this test passes or fails.
    monkeypatch.setattr(
        artist_service_module, "write_artist_predictions", lambda *_a, **_k: [1]
    )

    def batch_identification() -> None:
        service.run_batch_identification(image_ids=[1], threshold=0.2, top_k=2)

    def identify_one_image() -> None:
        service.identify_image(image_id=1, threshold=0.2, top_k=2)

    order = admission_order(
        ("artist-batch", batch_identification),
        ("artist-single", identify_one_image),
    )
    assert order == ["artist-single", "artist-batch"]


# ---------------------------------------------------------------------------
# Censor / NudeNet: no batch caller today, so the guard is the DEFAULT
# ---------------------------------------------------------------------------

def _stub_ultralytics_detector():
    """A real ``CensorDetector`` on the ultralytics path with a stub runtime."""
    from censor import CensorDetector

    class _Runtime:
        names = {0: "face"}

        def predict(self, *_args, **_kwargs):
            note_admission()
            return []  # falsy -> detect() returns [] without post-processing

    detector = CensorDetector(model_path="lane-test-seg.pt")
    detector.runtime = _Runtime()
    detector.session = detector.runtime
    detector.runtime_backend = "ultralytics"
    return detector


def test_censor_detection_defaults_to_normal_so_a_future_batch_cannot_invert(
    scratch_png,
):
    """``CensorDetector.detect`` must not be interactive unless asked.

    ``detect`` and ``detect_from_image`` share ``_detect_with_ultralytics``, and
    only ``services/censor/detection`` (one image, user waiting) passes the
    interactive lane. A caller that says nothing -- which is what any future
    batch loop would look like -- has to land in the normal lane.
    """
    detector = _stub_ultralytics_detector()

    def default_caller() -> None:
        detector.detect(scratch_png, 0.5)

    order = admission_order(
        ("normal-probe", probe(g.PRIORITY_NORMAL)),
        ("censor-default", default_caller),
    )
    # Queued second at the same priority, so FIFO must keep it second.
    assert order == ["normal-probe", "censor-default"]


def test_censor_detection_takes_the_interactive_lane_when_the_caller_asks(
    scratch_png,
):
    """The same leaf, driven the way the single-image detect service drives it."""
    detector = _stub_ultralytics_detector()

    def interactive_caller() -> None:
        detector.detect(scratch_png, 0.5, priority=g.PRIORITY_INTERACTIVE)

    order = admission_order(
        ("normal-probe", probe(g.PRIORITY_NORMAL)),
        ("censor-interactive", interactive_caller),
    )
    assert order == ["censor-interactive", "normal-probe"]


def test_nudenet_detection_defaults_to_normal_so_a_future_batch_cannot_invert(
    monkeypatch, scratch_png
):
    """``NudeNetDetector.detect`` must not be interactive unless asked."""
    import nudenet_detector

    class _Detector:
        def detect(self, _input):
            note_admission()
            return []

    wrapper = nudenet_detector.NudeNetDetector()
    wrapper._detector = _Detector()

    def default_caller() -> None:
        wrapper.detect(scratch_png, conf_threshold=0.5)

    order = admission_order(
        ("normal-probe", probe(g.PRIORITY_NORMAL)),
        ("nudenet-default", default_caller),
    )
    assert order == ["normal-probe", "nudenet-default"]


def test_nudenet_detection_takes_the_interactive_lane_when_the_caller_asks(
    scratch_png,
):
    """The same leaf, driven the way the single-image detect service drives it."""
    import nudenet_detector

    class _Detector:
        def detect(self, _input):
            note_admission()
            return []

    wrapper = nudenet_detector.NudeNetDetector()
    wrapper._detector = _Detector()

    def interactive_caller() -> None:
        wrapper.detect(scratch_png, conf_threshold=0.5, priority=g.PRIORITY_INTERACTIVE)

    order = admission_order(
        ("normal-probe", probe(g.PRIORITY_NORMAL)),
        ("nudenet-interactive", interactive_caller),
    )
    assert order == ["nudenet-interactive", "normal-probe"]


# ---------------------------------------------------------------------------
# Model loads stay NORMAL, deliberately
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_ultralytics(monkeypatch):
    """Install a fake ``ultralytics`` so a real load path loads no weights."""
    module = types.ModuleType("ultralytics")

    class _YOLO:
        def __init__(self, _path):
            note_admission()
            self.names = {0: "face"}

    module.YOLO = _YOLO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    return module


def _model_load_driver():
    from censor import CensorDetector

    def driver() -> None:
        CensorDetector(model_path="lane-test-seg.pt")._load_with_ultralytics(
            "lane-test-seg.pt"
        )

    return driver


def test_a_model_load_outranks_a_batch_job_that_queued_first(
    fake_ultralytics, monkeypatch, scratch_png
):
    """Every model LOAD stays in the normal lane, so bulk work cannot delay it.

    This is why loads were deliberately left at ``PRIORITY_NORMAL`` instead of
    being swept into the batch lane along with the inference they serve: a load
    declared ``PRIORITY_BATCH`` would sit behind every queued batch chunk.
    """
    import similarity

    model = _FakeEmbedder()

    def batch_indexer() -> None:
        similarity.embed_image_file(scratch_png, model=model)

    order = admission_order(
        ("batch-embed", batch_indexer),
        ("model-load", _model_load_driver()),
    )
    assert order == ["model-load", "batch-embed"]


def test_queued_interactive_work_does_not_indefinitely_postpone_a_model_load(
    fake_ultralytics, scratch_png
):
    """A model load queued last still gets in, and still beats the batch chunk.

    Deliberately NOT claiming loads always go first -- interactive work that is
    already queued is admitted ahead of a load, by design. What is pinned is
    that the wait is bounded by the work already in the queue (the load is
    admitted, not skipped) and that a load is never demoted below bulk work.
    """
    import similarity

    model = _FakeEmbedder()

    def batch_indexer() -> None:
        similarity.embed_image_file(scratch_png, model=model)

    order = admission_order(
        ("batch-embed", batch_indexer),
        ("interactive-1", probe(g.PRIORITY_INTERACTIVE)),
        ("interactive-2", probe(g.PRIORITY_INTERACTIVE)),
        ("model-load", _model_load_driver()),
    )
    assert order == ["interactive-1", "interactive-2", "model-load", "batch-embed"]
