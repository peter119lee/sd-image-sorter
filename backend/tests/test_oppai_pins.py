"""Characterization pins for oppai_oracle_tagger.py (OppaiOracle V1.1 ONNX) — TIER-2 step 0.

These lock the module-boundary contracts that a tagger/toriigate-style facade +
sibling-module split must preserve VERBATIM, WITHOUT loading a real ONNX session,
importing onnxruntime, or downloading anything. Everything heavy is stubbed:

  * ``oppai_oracle_tagger.ort`` / ``hf_hub`` are the lazy-import globals; pins
    inject fake sys.modules so no real ``onnxruntime`` / ``huggingface_hub``
    import runs.
  * pure scoring / preprocessing / loader / resolution helpers are exercised
    through ``OppaiOracleTagger.__new__`` (bypasses ``__init__`` ->
    ``_ensure_imports``), mirroring the tagger/toriigate pins ``_bare`` helper.

Companion to the existing readers test_oppai_oracle_tagger.py (24 tests: preprocess
math, tag loader, process_probs threshold/clip, registry, singleton reuse, GPU OOM
backoff) and test_oppai_oracle_alias.py (7 tests: alias normalization). This is the
UNIT twin that protects the SEAMS a decomposition would move. Net-new focus
(reader-silent): module/__all__/import-contract identity, the ``_get_preprocess_
executor`` family + the line-770 deep-read seam, ort/hf lazy-import wiring, the
singleton rebuild matrix, the ``config.TAG_SCORES_*`` tag_scores seam, and the
loader / file-resolution / session-helper contracts.

SEAM NOTES a split MUST honor:
  * ``tests/test_model_service_pins.py`` replaces sys.modules['oppai_oracle_tagger']
    with a SimpleNamespace exposing ONLY ``OppaiOracleTagger`` + ``DEFAULT_MODEL``,
    then does ``from oppai_oracle_tagger import OppaiOracleTagger, DEFAULT_MODEL``.
    Both MUST stay top-level attrs a facade re-exports.
  * ``tests/test_oppai_oracle_alias.py`` does ``importlib.import_module(
    "oppai_oracle_tagger")`` and reads private ``_normalize_oppai_model_alias`` +
    ``DEFAULT_MODEL`` on it — the module must stay a real importable FILE.
  * ``_preprocess_paths`` resolves the MODULE-LEVEL ``_get_preprocess_executor``
    (line 770). A split moving the class to a submodule must re-resolve it at call
    time on the facade (see tagger_preprocess.py ``_svc()`` playbook), or a bare
    read would create a second, unpatched executor family.
"""

from __future__ import annotations

import csv
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import numpy as np
import pytest
from PIL import Image

import oppai_oracle_tagger as oppai
from oppai_oracle_tagger import OppaiOracleTagger


# ---------------------------------------------------------------------------
# Machine-state isolation (tagger_pins precedent 0edbb81): snapshot + restore
# every mutable module global so a pin can freely rebind them (or call
# get_oppai_oracle_tagger, which mutates the singleton) without bleeding into
# sibling suites on a shared interpreter. Any executor a pin creates is shut
# down so no oppai-preprocess threads leak.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_oppai_module_state():
    saved = {
        "ort": oppai.ort,
        "hf_hub": oppai.hf_hub,
        "_tagger_singleton": oppai._tagger_singleton,
        "_singleton_settings": dict(oppai._singleton_settings),
        "_preprocess_executor": oppai._preprocess_executor,
    }
    try:
        yield
    finally:
        created = oppai._preprocess_executor
        if (
            created is not None
            and created is not saved["_preprocess_executor"]
            and hasattr(created, "shutdown")
        ):
            created.shutdown(wait=False)
        oppai.ort = saved["ort"]
        oppai.hf_hub = saved["hf_hub"]
        oppai._tagger_singleton = saved["_tagger_singleton"]
        oppai._singleton_settings = saved["_singleton_settings"]
        oppai._preprocess_executor = saved["_preprocess_executor"]


# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------
def _bare(**attrs: Any) -> OppaiOracleTagger:
    """Build an OppaiOracleTagger via __new__ (skips __init__ -> _ensure_imports).

    Defaults mirror the real constructor's post-init attribute set so any pure
    method (loader / process_probs / resolution / session helpers) can run
    without a real ONNX import or model file.
    """
    tagger = OppaiOracleTagger.__new__(OppaiOracleTagger)
    tagger.model_name = "oppai-oracle-v1.1"
    tagger.model_path = None
    tagger.tags_path = None
    tagger.model_dir = "/fake/models/oppai"
    tagger.threshold = oppai.DEFAULT_THRESHOLD
    tagger.character_threshold = 1.0
    tagger.use_gpu = False
    tagger.session = None
    tagger.tags = []
    tagger.general_tags = []
    tagger.character_tags = []
    tagger.rating_tags = []
    tagger.rating_indices = {}
    tagger._loaded = False
    tagger._resolved_model_path = None
    tagger._resolved_tags_path = None
    tagger._target = 448
    tagger._pad_color = (114, 114, 114)
    tagger._supports_true_batch = True
    tagger._session_refresh_interval = 0
    tagger._images_since_session_create = 0
    for key, value in attrs.items():
        setattr(tagger, key, value)
    return tagger


def _write_oppai_csv(tmp_path: Path) -> Path:
    """Tiny stand-in selected_tags.csv with the real shape: header + PAD/UNK +
    3 general rows + 4 rating rows (tag ids aligned to column position)."""
    csv_path = tmp_path / "selected_tags.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag_id", "name", "category"])
        writer.writerow(["0", "<PAD>", "0"])
        writer.writerow(["1", "<UNK>", "0"])
        writer.writerow(["2", "1girl", "0"])
        writer.writerow(["3", "long_hair", "0"])
        writer.writerow(["4", "smile", "0"])
        writer.writerow(["5", "rating:general", "0"])
        writer.writerow(["6", "rating:sensitive", "0"])
        writer.writerow(["7", "rating:questionable", "0"])
        writer.writerow(["8", "rating:explicit", "0"])
    return csv_path


def _loaded_bare(tmp_path: Path, **attrs: Any) -> OppaiOracleTagger:
    """A _bare tagger with the minimal tag table loaded (general + rating splits)."""
    tagger = _bare(**attrs)
    tagger._load_tags(str(_write_oppai_csv(tmp_path)))
    return tagger


def _tiny_png(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    Image.new("RGB", (10, 12), (5, 5, 5)).save(path)
    return str(path)


class _InlineFuture:
    """Stand-in Future: runs the callable eagerly, replays value/exception on .result()."""

    def __init__(self, fn, args):
        self._exc: Any = None
        self._value: Any = None
        try:
            self._value = fn(*args)
        except Exception as exc:  # noqa: BLE001 - mirror executor per-image isolation
            self._exc = exc

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._value


class _RecordingExecutor:
    """Stand-in ThreadPoolExecutor that records submit() calls (runs inline)."""

    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, fn, *args):
        self.submit_calls += 1
        return _InlineFuture(fn, args)


class _RecordingOppai:
    """Stand-in for OppaiOracleTagger used to pin get_oppai_oracle_tagger branching
    WITHOUT running the real __init__ (which imports onnxruntime + touches the FS)."""

    instances: List["_RecordingOppai"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.model_name = kwargs.get("model_name")
        self.threshold = kwargs.get("threshold")
        self.character_threshold = kwargs.get("character_threshold")
        _RecordingOppai.instances.append(self)


@pytest.fixture
def recording_oppai(monkeypatch):
    """Prime a clean singleton slot patched to the recording fake class."""
    _RecordingOppai.instances = []
    monkeypatch.setattr(oppai, "OppaiOracleTagger", _RecordingOppai)
    oppai._tagger_singleton = None
    oppai._singleton_settings = {}
    return _RecordingOppai


# ===========================================================================
# 1. Module identity + import contract (facade-critical) — reader-silent
# ===========================================================================
def test_module_is_a_real_importable_file_with_private_alias_seam():
    """test_oppai_oracle_alias.py imports the module by name and reads private
    _normalize_oppai_model_alias + DEFAULT_MODEL off it. A package split must keep
    ``oppai_oracle_tagger`` a real importable module carrying these attrs."""
    mod = importlib.import_module("oppai_oracle_tagger")
    assert mod is oppai
    assert Path(mod.__file__).name == "oppai_oracle_tagger.py"
    assert callable(mod._normalize_oppai_model_alias)
    assert mod.DEFAULT_MODEL == "oppai-oracle-v1.1"


def test_all_exports_are_truthful_and_frozen():
    """Every __all__ name resolves, and the public export set is exactly the six
    the readers/importers depend on (letterbox/preprocess free fns included)."""
    for name in oppai.__all__:
        assert hasattr(oppai, name), f"__all__ advertises {name!r} but module lacks it"
    assert set(oppai.__all__) == {
        "DEFAULT_MODEL",
        "DEFAULT_THRESHOLD",
        "OppaiOracleTagger",
        "get_oppai_oracle_tagger",
        "letterbox_to_square",
        "preprocess_image",
    }


def test_top_level_import_contract_matches_product_importers():
    """Mirrors worker.py / model_service_prepare.py / smart_tag/tagging.py: the
    class + getter + DEFAULT_MODEL must import straight off the top-level module."""
    from oppai_oracle_tagger import (  # noqa: F401 - importability IS the pin
        DEFAULT_MODEL,
        DEFAULT_THRESHOLD,
        OppaiOracleTagger as _Cls,
        get_oppai_oracle_tagger,
        letterbox_to_square,
        preprocess_image,
    )

    assert DEFAULT_MODEL == "oppai-oracle-v1.1"
    assert _Cls is OppaiOracleTagger


def test_sys_modules_replacement_reader_contract():
    """test_model_service_pins.py swaps sys.modules['oppai_oracle_tagger'] for a
    SimpleNamespace exposing ONLY OppaiOracleTagger + DEFAULT_MODEL. Those two
    names MUST remain top-level module attrs a facade re-exports."""
    assert hasattr(oppai, "OppaiOracleTagger")
    assert hasattr(oppai, "DEFAULT_MODEL")


def test_module_constants_have_pinned_values():
    assert oppai.DEFAULT_MODEL == "oppai-oracle-v1.1"
    assert oppai.DEFAULT_THRESHOLD == pytest.approx(0.7927, abs=1e-4)
    assert oppai.PAD_TAG_INDEX == 0
    assert oppai.UNK_TAG_INDEX == 1
    assert oppai.RATING_TAG_PREFIX == "rating:"


def test_preprocess_max_workers_is_bounded():
    assert oppai._PREPROCESS_MAX_WORKERS == min(8, (os.cpu_count() or 4))
    assert 1 <= oppai._PREPROCESS_MAX_WORKERS <= 8


# ===========================================================================
# 2. Preprocess-executor family + line-770 deep-read seam (headline split seam)
# ===========================================================================
def test_get_preprocess_executor_lazily_creates_and_caches():
    """First call builds a ThreadPoolExecutor into the module global and caches
    it; a second call returns the same instance."""
    oppai._preprocess_executor = None  # fixture restores + shuts down
    first = oppai._get_preprocess_executor()
    second = oppai._get_preprocess_executor()
    assert isinstance(first, ThreadPoolExecutor)
    assert first is second
    assert oppai._preprocess_executor is first


def test_get_preprocess_executor_reads_current_module_global():
    """SEAM: _get_preprocess_executor consults the CURRENT module global (not a
    captured local). Proven by planting a sentinel — it is returned verbatim,
    without building a real pool. A split moving this fn must keep the same
    global binding patchable."""
    sentinel = object()
    oppai._preprocess_executor = sentinel  # fixture restores (sentinel has no shutdown)
    assert oppai._get_preprocess_executor() is sentinel


def test_preprocess_paths_multi_resolves_module_level_executor(monkeypatch, tmp_path):
    """The line-770 patch surface: a >1-path preprocess resolves the MODULE-LEVEL
    ``_get_preprocess_executor`` and submits one job per path. A facade split must
    keep this symbol resolvable on the module the tests patch (tagger_preprocess
    ``_svc()`` playbook)."""
    recorder = _RecordingExecutor()
    monkeypatch.setattr(oppai, "_get_preprocess_executor", lambda: recorder)
    tagger = _bare(_target=8)
    paths = [_tiny_png(tmp_path, "a.png"), _tiny_png(tmp_path, "b.png")]

    prepared = tagger._preprocess_paths(paths)

    assert recorder.submit_calls == 2
    assert all(isinstance(item, tuple) and len(item) == 2 for item in prepared)


def test_preprocess_paths_single_is_serial_and_never_touches_executor(
    monkeypatch, tmp_path
):
    """A single-path chunk stays serial: the executor must NOT be resolved at all
    (pins the ``len(paths) <= 1`` fast path)."""
    monkeypatch.setattr(
        oppai,
        "_get_preprocess_executor",
        lambda: (_ for _ in ()).throw(
            AssertionError("must not use executor for one image")
        ),
    )
    tagger = _bare(_target=8)

    prepared = tagger._preprocess_paths([_tiny_png(tmp_path, "solo.png")])

    assert len(prepared) == 1
    assert isinstance(prepared[0], tuple) and len(prepared[0]) == 2


def test_preprocess_paths_isolates_bad_path_and_preserves_order(tmp_path):
    """A missing image in the middle is isolated as an Exception at its index while
    readable neighbours preprocess, in order (parallel branch, real executor)."""
    good = _tiny_png(tmp_path, "g.png")
    paths = [good, str(tmp_path / "missing.png"), good]
    tagger = _bare(_target=8)

    prepared = tagger._preprocess_paths(paths)

    assert isinstance(prepared[0], tuple)
    assert isinstance(prepared[1], Exception)
    assert isinstance(prepared[2], tuple)


# ===========================================================================
# 3. Free-function resolution seams (module-level letterbox / preprocess_image)
# ===========================================================================
def test_preprocess_image_resolves_module_level_letterbox(monkeypatch):
    """SEAM: preprocess_image calls the MODULE-LEVEL letterbox_to_square. A split
    moving the letterbox helper must keep preprocess_image resolving it on the
    same module. Proven by spying the module symbol."""
    calls: List[Any] = []
    real = oppai.letterbox_to_square

    def _spy(image, *, target, pad_color):
        calls.append((target, pad_color))
        return real(image, target=target, pad_color=pad_color)

    monkeypatch.setattr(oppai, "letterbox_to_square", _spy)
    oppai.preprocess_image(Image.new("RGB", (10, 12), (5, 5, 5)), target=16)

    assert calls == [(16, (114, 114, 114))]


def test_preprocess_paths_resolves_module_level_preprocess_image(monkeypatch, tmp_path):
    """SEAM: the per-image ``_one`` closure calls the MODULE-LEVEL preprocess_image
    (not a bound copy). Proven by swapping the module symbol for a sentinel fn and
    observing _preprocess_paths return its output."""
    seen: List[int] = []

    def _fake(image, *, target, pad_color):
        seen.append(target)
        return ("PV", "PM")

    monkeypatch.setattr(oppai, "preprocess_image", _fake)
    tagger = _bare(_target=8)

    prepared = tagger._preprocess_paths([_tiny_png(tmp_path, "a.png")])

    assert prepared == [("PV", "PM")]
    assert seen == [8]


# ===========================================================================
# 4. Lazy-import family (ort / hf_hub) — facade-critical
# ===========================================================================
def test_ensure_imports_is_noop_when_both_globals_bound(monkeypatch):
    ort_sentinel, hub_sentinel = object(), object()
    monkeypatch.setattr(oppai, "ort", ort_sentinel)
    monkeypatch.setattr(oppai, "hf_hub", hub_sentinel)

    oppai._ensure_imports()

    assert oppai.ort is ort_sentinel
    assert oppai.hf_hub is hub_sentinel


def test_ensure_imports_binds_ort_and_hf_from_their_modules(monkeypatch):
    """Import wiring seam: ort<-onnxruntime, hf_hub<-huggingface_hub, and the
    runtime_env prep runs first. Proven by injecting fake modules (no real
    onnxruntime import) and observing the globals get bound to them."""
    prep_called = {"n": 0}
    fake_ort = SimpleNamespace(preload_dlls=lambda: None)
    fake_hub = object()
    fake_runtime_env = SimpleNamespace(
        prepare_onnxruntime_environment=lambda: prep_called.__setitem__("n", 1)
    )
    monkeypatch.setitem(sys.modules, "runtime_env", fake_runtime_env)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setattr(oppai, "ort", None)
    monkeypatch.setattr(oppai, "hf_hub", None)

    oppai._ensure_imports()

    assert oppai.ort is fake_ort
    assert oppai.hf_hub is fake_hub
    assert prep_called["n"] == 1


# ===========================================================================
# 5. Singleton lifecycle — get_oppai_oracle_tagger rebuild matrix
# ===========================================================================
def test_singleton_reuses_instance_when_settings_unchanged(recording_oppai):
    first = oppai.get_oppai_oracle_tagger(use_gpu=False)
    second = oppai.get_oppai_oracle_tagger(use_gpu=False)
    assert first is second
    assert len(recording_oppai.instances) == 1


def test_singleton_rebuilds_on_use_gpu_change(recording_oppai):
    first = oppai.get_oppai_oracle_tagger(use_gpu=False)
    switched = oppai.get_oppai_oracle_tagger(use_gpu=True)
    assert switched is not first
    assert len(recording_oppai.instances) == 2


def test_singleton_rebuilds_on_model_path_change(recording_oppai):
    first = oppai.get_oppai_oracle_tagger(use_gpu=False)
    switched = oppai.get_oppai_oracle_tagger(
        use_gpu=False, model_path="/custom/model.onnx"
    )
    assert switched is not first


def test_singleton_force_reload_rebuilds(recording_oppai):
    first = oppai.get_oppai_oracle_tagger(use_gpu=False)
    reloaded = oppai.get_oppai_oracle_tagger(use_gpu=False, force_reload=True)
    assert reloaded is not first
    assert len(recording_oppai.instances) == 2


def test_singleton_canonicalizes_alias_before_settings_compare(recording_oppai):
    """SEAM: the family id ``oppai-oracle`` and the versioned key resolve to the
    SAME cached settings (canonicalized before the equality check), so no rebuild."""
    first = oppai.get_oppai_oracle_tagger(model_name="oppai-oracle", use_gpu=False)
    same = oppai.get_oppai_oracle_tagger(model_name="oppai-oracle-v1.1", use_gpu=False)
    assert same is first
    assert len(recording_oppai.instances) == 1
    assert first.model_name == "oppai-oracle-v1.1"


def test_singleton_thresholds_update_in_place_without_rebuild(recording_oppai):
    """threshold + character_threshold are live-tunable: an otherwise-unchanged
    call reuses the instance and mutates both floats (never rebuilds weights)."""
    first = oppai.get_oppai_oracle_tagger(
        use_gpu=False, threshold=0.5, character_threshold=0.6
    )
    same = oppai.get_oppai_oracle_tagger(
        use_gpu=False, threshold=0.8, character_threshold=0.9
    )
    assert same is first
    assert len(recording_oppai.instances) == 1
    assert first.threshold == pytest.approx(0.8)
    assert first.character_threshold == pytest.approx(0.9)


# ===========================================================================
# 6. process_probs / result-shape + tag_scores config seam (reader-silent)
# ===========================================================================
def test_build_empty_result_shape_and_optional_error_key():
    tagger = _bare()
    empty = tagger._build_empty_result()
    assert set(empty) == {
        "general_tags",
        "character_tags",
        "rating",
        "rating_confidences",
        "all_tags",
    }
    assert empty["rating"] == "unknown"
    assert empty["general_tags"] == [] and empty["all_tags"] == []
    with_error = tagger._build_empty_result("boom")
    assert with_error["error"] == "boom"


def test_process_probs_rating_is_selected_below_general_threshold(tmp_path):
    """Ratings are NEVER gated by the general threshold: the top rating wins even
    when every score is below it, and character_tags stays empty."""
    tagger = _loaded_bare(tmp_path, threshold=0.9)
    probs = np.zeros(9, dtype=np.float32)
    probs[5] = 0.10  # rating:general
    probs[7] = 0.30  # rating:questionable (highest, still below general threshold)

    result = tagger._process_probs(probs)

    assert result["rating"] == "questionable"
    assert result["rating_confidences"]["questionable"] == pytest.approx(0.30)
    assert result["general_tags"] == []
    assert result["character_tags"] == []
    assert any(item["tag"] == "questionable" for item in result["all_tags"])


def test_process_probs_honors_per_call_threshold_override(tmp_path):
    """A per-call ``threshold`` overrides self.threshold for the general split."""
    tagger = _loaded_bare(tmp_path, threshold=0.5)
    probs = np.zeros(9, dtype=np.float32)
    probs[2] = 0.80  # 1girl
    probs[4] = 0.60  # smile
    probs[7] = 0.95  # rating:questionable

    strict = tagger._process_probs(probs, threshold=0.9)
    assert [item["tag"] for item in strict["general_tags"]] == []

    loose = tagger._process_probs(probs, threshold=0.5)
    assert {item["tag"] for item in loose["general_tags"]} == {"1girl", "smile"}


def test_process_probs_character_threshold_param_is_accepted_and_ignored(tmp_path):
    """OppaiOracle has no character category: character_threshold is bookkeeping
    only — passing any value must not change the output shape."""
    tagger = _loaded_bare(tmp_path, threshold=0.5)
    probs = np.zeros(9, dtype=np.float32)
    probs[2] = 0.99

    baseline = tagger._process_probs(probs)
    with_char = tagger._process_probs(probs, character_threshold=0.01)

    assert with_char["general_tags"] == baseline["general_tags"]
    assert with_char["character_tags"] == []


def test_process_probs_collects_below_threshold_scores_when_enabled(
    tmp_path, monkeypatch
):
    """BE-1 seam: with TAG_SCORES_ENABLED, every score >= floor is collected for the
    tag_scores table (virtual re-threshold), including tags BELOW the display
    threshold, tagged with their category. Reads the module-level ``config``."""
    monkeypatch.setattr(oppai.config, "TAG_SCORES_ENABLED", True)
    monkeypatch.setattr(oppai.config, "TAG_SCORES_FLOOR", 0.1)
    tagger = _loaded_bare(tmp_path, threshold=0.5)
    probs = np.zeros(9, dtype=np.float32)
    probs[2] = 0.99  # 1girl -> displayed + scored
    probs[3] = 0.30  # long_hair -> below display threshold but >= floor -> scored only
    probs[7] = 0.85  # rating:questionable -> scored, category rating

    result = tagger._process_probs(probs)
    scored = {entry["tag"]: entry for entry in result["tag_scores"]}

    assert scored["long_hair"]["category"] == "general"
    assert scored["1girl"]["category"] == "general"
    assert scored["questionable"]["category"] == "rating"
    assert "long_hair" not in {item["tag"] for item in result["general_tags"]}


def test_process_probs_omits_tag_scores_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(oppai.config, "TAG_SCORES_ENABLED", False)
    tagger = _loaded_bare(tmp_path, threshold=0.5)
    probs = np.zeros(9, dtype=np.float32)
    probs[2] = 0.99

    result = tagger._process_probs(probs)

    assert "tag_scores" not in result


# ===========================================================================
# 7. Tag-table loader edge cases (reader-silent)
# ===========================================================================
def test_load_tags_raises_on_empty_file(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty tag file"):
        _bare()._load_tags(str(empty))


def test_load_tags_raises_on_unexpected_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected OppaiOracle tag header"):
        _bare()._load_tags(str(bad))


def test_load_tags_skips_malformed_rows(tmp_path):
    messy = tmp_path / "messy.csv"
    messy.write_text(
        "tag_id,name,category\nnotint,bad,0\n2,1girl,0\n\n",
        encoding="utf-8",
        newline="",
    )
    tagger = _bare()
    tagger._load_tags(str(messy))
    assert [name for _, name in tagger.general_tags] == ["1girl"]


def test_load_tags_is_idempotent(tmp_path):
    csv_path = _write_oppai_csv(tmp_path)
    tagger = _bare()
    tagger._load_tags(str(csv_path))
    first = list(tagger.general_tags)
    tagger._load_tags(str(csv_path))
    assert tagger.general_tags == first  # reset each call, not appended


# ===========================================================================
# 8. File-resolution + error contracts (reader-silent, no network)
# ===========================================================================
def test_model_config_is_empty_for_unknown_model():
    assert _bare(model_name="bogus-model")._model_config() == {}


def test_download_model_raises_for_unknown_model():
    """Unknown model name short-circuits with the explicit error BEFORE any
    network/FS work (empty config guard)."""
    tagger = _bare(model_name="bogus-model")
    with pytest.raises(ValueError, match="Unknown OppaiOracle model"):
        tagger._download_model()


def test_get_model_paths_missing_custom_model_raises(tmp_path):
    tagger = _bare(model_path=str(tmp_path / "nope.onnx"), tags_path=None)
    with pytest.raises(FileNotFoundError):
        tagger._get_model_paths()


def test_get_model_paths_uses_sibling_selected_tags_csv(tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    sibling = tmp_path / "selected_tags.csv"
    sibling.write_text("tag_id,name,category\n", encoding="utf-8")
    tagger = _bare(model_path=str(model), tags_path=None)

    resolved_model, resolved_tags = tagger._get_model_paths()

    assert resolved_model == str(model)
    assert resolved_tags == str(sibling)


def test_get_model_paths_custom_model_without_tags_raises(tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    tagger = _bare(model_path=str(model), tags_path=None)
    with pytest.raises(ValueError, match="selected_tags.csv"):
        tagger._get_model_paths()


def test_expected_local_paths_uses_canonical_repo_subfolder_layout(tmp_path):
    """Layout contract: <model_dir>/<model_name>/<repo_subfolder>/<file>."""
    tagger = _bare(model_name="oppai-oracle-v1.1", model_dir=str(tmp_path))
    model_path, tags_path = tagger._expected_local_paths()
    tail = os.path.join("oppai-oracle-v1.1", "V1.1_onnx")
    assert model_path.endswith(os.path.join(tail, "model.onnx"))
    assert tags_path.endswith(os.path.join(tail, "selected_tags.csv"))


def test_validate_model_file_size_gate(tmp_path):
    tagger = _bare()
    assert tagger._validate_model_file(str(tmp_path / "absent.onnx")) is False
    small = tmp_path / "small.onnx"
    small.write_bytes(b"x")
    assert tagger._validate_model_file(str(small)) is False
    big = tmp_path / "big.onnx"
    big.write_bytes(b"0" * (1024 * 1024 + 1))
    assert tagger._validate_model_file(str(big)) is True


# ===========================================================================
# 9. Session helpers (reader-silent) — refresh cadence + provider probe
# ===========================================================================
def test_set_session_refresh_interval_clamps_negative_to_zero():
    tagger = _bare()
    tagger.set_session_refresh_interval(-5)
    assert tagger._session_refresh_interval == 0
    tagger.set_session_refresh_interval(7)
    assert tagger._session_refresh_interval == 7


def test_maybe_refresh_session_reloads_once_interval_crossed(monkeypatch):
    tagger = _bare(
        _session_refresh_interval=2, _images_since_session_create=0, _loaded=True
    )
    reloads = {"n": 0}

    def _fake_load():
        reloads["n"] += 1
        tagger._loaded = True

    monkeypatch.setattr(tagger, "load", _fake_load)

    tagger._maybe_refresh_session(1)  # running total 1 < 2 -> no reload
    assert reloads["n"] == 0
    tagger._maybe_refresh_session(1)  # running total 2 >= 2 -> reload + reset
    assert reloads["n"] == 1
    assert tagger._images_since_session_create == 0


def test_maybe_refresh_session_disabled_when_interval_zero(monkeypatch):
    tagger = _bare(_session_refresh_interval=0, _images_since_session_create=0)
    monkeypatch.setattr(
        tagger,
        "load",
        lambda: (_ for _ in ()).throw(AssertionError("must not reload when disabled")),
    )
    tagger._maybe_refresh_session(1000)  # never reloads


def test_session_uses_gpu_provider_matrix():
    tagger = _bare(session=None)
    assert tagger._session_uses_gpu() is False
    tagger.session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
    assert tagger._session_uses_gpu() is False
    tagger.session = SimpleNamespace(
        get_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    assert tagger._session_uses_gpu() is True
    tagger.session = SimpleNamespace(get_providers=lambda: ["DmlExecutionProvider"])
    assert tagger._session_uses_gpu() is True
