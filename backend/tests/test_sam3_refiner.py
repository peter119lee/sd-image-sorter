"""
Unit tests for ``sam3_refiner`` gating logic.

The gating layer is what protects users from SAM3's whole-body false-positive
collapse on absent prompts (the historical "box censor" bug). These tests
verify the three gates in isolation, without loading a real checkpoint or
requiring CUDA.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

torch = pytest.importorskip("torch", reason="sam3_refiner gating tests need torch; CI installs core deps only")
from PIL import Image

import sam3_refiner
from sam3_refiner import (
    _DEFAULT_PRESENCE_THRESHOLD,
    _best_mask,
    _presence_prob,
    SAM3Refiner,
)


# ---------- _presence_prob ----------------------------------------------------


def test_presence_prob_returns_max_sigmoid():
    out = SimpleNamespace(presence_logits=torch.tensor([[2.0, -3.0, 0.5]]))

    assert _presence_prob(out) == pytest.approx(torch.sigmoid(torch.tensor(2.0)).item())


def test_presence_prob_handles_missing_attribute():
    assert _presence_prob(SimpleNamespace()) is None


def test_presence_prob_handles_empty_tensor():
    assert _presence_prob(SimpleNamespace(presence_logits=torch.empty(0))) is None


# ---------- _best_mask --------------------------------------------------------


def test_best_mask_picks_highest_score_above_threshold():
    masks = torch.zeros((3, 4, 4), dtype=torch.bool)
    masks[1, 1:3, 1:3] = True
    results = [{"scores": torch.tensor([0.1, 0.7, 0.4]), "masks": masks}]

    mask = _best_mask(results, score_threshold=0.5)

    assert mask is not None
    assert int(mask.sum()) == 4


def test_best_mask_returns_none_when_all_below_threshold():
    masks = torch.zeros((2, 4, 4), dtype=torch.bool)
    results = [{"scores": torch.tensor([0.1, 0.2]), "masks": masks}]

    assert _best_mask(results, score_threshold=0.5) is None


def test_best_mask_thresholds_floating_masks_at_half():
    masks = torch.full((1, 4, 4), 0.6)
    results = [{"scores": torch.tensor([0.9]), "masks": masks}]

    mask = _best_mask(results)

    assert mask is not None
    assert mask.shape == (4, 4)
    assert int(mask.sum()) == 16


# ---------- _run_segmentation gates ------------------------------------------


@pytest.fixture
def fake_refiner(monkeypatch):
    """Build a SAM3Refiner whose model + processor are fully mocked.

    The gates we test live entirely in ``_run_segmentation`` and do not need
    any real SAM3 internals — only the shape of the output matters.
    """
    refiner = SAM3Refiner(checkpoint_path=None)

    def fake_load(*_args, **_kwargs):
        refiner._model = MagicMock(name="Sam3Model")
        refiner._processor = MagicMock(name="Sam3Processor")
        processed_inputs = MagicMock()
        processed_inputs.to.return_value = {}
        refiner._processor.return_value = processed_inputs
        return refiner._model, refiner._processor

    monkeypatch.setattr(refiner, "load", fake_load)
    monkeypatch.setattr(sam3_refiner, "_check_sam3_available", lambda: True)
    monkeypatch.setattr(sam3_refiner, "_sam3_device", "cpu", raising=False)
    monkeypatch.setattr(
        sam3_refiner, "exclusive_ai_runtime", lambda *_a, **_k: contextlib.nullcontext()
    )

    refiner.load()
    return refiner


def _processed_results(scores, masks):
    return [{"scores": scores, "masks": masks}]


def test_run_segmentation_gates_below_presence_threshold(fake_refiner):
    """Absent-prompt case: presence_logits sigmoid ~0.02 should be refused."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[-4.0]]),  # sigmoid ~ 0.018
    )
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.01]),
        torch.ones((1, 8, 8), dtype=torch.bool),
    )

    image = Image.new("RGB", (8, 8))
    result = fake_refiner._run_segmentation(image, text="exposed female genitalia")

    assert result is None
    fake_refiner._processor.post_process_instance_segmentation.assert_not_called()


def test_run_segmentation_passes_above_presence_threshold(fake_refiner):
    """Real-detection case: presence_logits sigmoid ~0.73 plus a real mask."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[1.0]]),  # sigmoid ~ 0.73
    )
    masks = torch.zeros((1, 8, 8), dtype=torch.bool)
    masks[0, 2:5, 2:5] = True  # 9 / 64 ≈ 14 % of canvas (well under 30 % cap)
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.5]), masks
    )

    image = Image.new("RGB", (8, 8))
    result = fake_refiner._run_segmentation(image, text="exposed female breast")

    assert result is not None
    assert int(result.sum()) == 9


def test_run_segmentation_rejects_whole_body_collapse(fake_refiner):
    """Belt-and-suspenders: even if presence sneaks past, an oversized mask
    (the 50–85 % whole-body silhouette) should be vetoed by the area cap."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[2.0]]),  # presence passes
    )
    big = torch.ones((1, 8, 8), dtype=torch.bool)  # 100 % of canvas
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.6]), big
    )

    image = Image.new("RGB", (8, 8))
    result = fake_refiner._run_segmentation(image, text="exposed buttocks")

    assert result is None


def test_run_segmentation_skips_presence_gate_for_box_only_prompt(fake_refiner):
    """Box prompts (no text) shouldn't be gated by presence_logits, which is
    a text-conditioned signal. ``refine_box`` callers depend on this."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[-5.0]]),  # would fail presence if applied
    )
    masks = torch.zeros((1, 8, 8), dtype=torch.bool)
    masks[0, 1:3, 1:3] = True
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.5]), masks
    )

    image = Image.new("RGB", (8, 8))
    result = fake_refiner._run_segmentation(image, box=[1.0, 1.0, 4.0, 4.0])

    assert result is not None
    assert int(result.sum()) == 4


# ---------- detect_privacy_regions defaults ----------------------------------


def test_detect_privacy_regions_default_threshold_uses_presence_gate(fake_refiner):
    """Default ``conf_threshold`` should match the empirically-chosen
    presence threshold so callers that don't pass an arg get sane behavior."""
    import inspect

    sig = inspect.signature(fake_refiner.detect_privacy_regions)
    assert sig.parameters["conf_threshold"].default == _DEFAULT_PRESENCE_THRESHOLD


def test_detect_privacy_regions_drops_undersized_pixel_area(fake_refiner):
    """Even if a prompt passes presence + score, masks under the absolute
    pixel floor (64 px, set to drop single-pixel artefacts) are filtered."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[2.0]]),
    )
    tiny = torch.zeros((1, 32, 32), dtype=torch.bool)
    tiny[0, 0, 0] = True  # 1 px
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.5]), tiny
    )

    detections = fake_refiner.detect_privacy_regions(
        Image.new("RGB", (32, 32)), prompts=[{"prompt": "x", "class": "x"}]
    )

    assert detections == []


# ---------- _check_sam3_available CUDA re-probe (transient-outage recovery) ---


def test_check_sam3_available_reprobes_cuda_each_call(monkeypatch):
    """A transient CUDA outage must NOT be cached permanently.

    Regression guard: previously a single ``torch.cuda.is_available() == False``
    at process start (GPU contention) was cached forever, hard-disabling SAM3
    for every later request. Now the import result is cached but CUDA is
    re-evaluated on every call.
    """
    # Simulate "transformers SAM3 import already succeeded" -> cached True.
    monkeypatch.setattr(sam3_refiner, "_sam3_available", True, raising=False)

    calls = {"n": 0}

    def flaky_cuda():
        calls["n"] += 1
        return calls["n"] > 1  # unavailable on the first call, available after

    monkeypatch.setattr(torch.cuda, "is_available", flaky_cuda)

    assert sam3_refiner._check_sam3_available() is False  # transient outage
    assert sam3_refiner._check_sam3_available() is True   # recovered, not sticky
    assert sam3_refiner._check_sam3_available() is True


def test_check_sam3_available_caches_import_failure(monkeypatch):
    """A real ImportError (SAM3 truly not installed) stays cached and never
    even probes CUDA -- the import cannot start succeeding mid-process."""
    monkeypatch.setattr(sam3_refiner, "_sam3_available", False, raising=False)

    def explode():
        raise AssertionError("CUDA must not be probed when the import failed")

    monkeypatch.setattr(torch.cuda, "is_available", explode)

    assert sam3_refiner._check_sam3_available() is False


# ---------- refine_box confidence threading (sam3_confidence slider) ---------


def test_refine_box_threads_confidence_into_both_gates(fake_refiner, monkeypatch):
    """The slider value must reach _run_segmentation as BOTH the mask-score
    floor and the text-conditioned presence gate (the box-only path simply
    ignores presence inside _run_segmentation)."""
    captured = {}

    def spy(image, text=None, box=None, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(fake_refiner, "_run_segmentation", spy)

    fake_refiner.refine_box(
        Image.new("RGB", (8, 8)), [1, 1, 4, 4], confidence_threshold=0.7
    )

    assert captured["score_threshold"] == pytest.approx(0.7)
    assert captured["presence_threshold"] == pytest.approx(0.7)


def test_refine_box_without_confidence_preserves_default_gates(fake_refiner, monkeypatch):
    """No confidence (legacy callers) -> no gate kwargs, so _run_segmentation
    keeps its historical defaults (score floor 0.05, presence 0.5)."""
    captured = {}

    def spy(image, text=None, box=None, **kwargs):
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(fake_refiner, "_run_segmentation", spy)

    fake_refiner.refine_box(Image.new("RGB", (8, 8)), [1, 1, 4, 4])

    assert "score_threshold" not in captured["kwargs"]
    assert "presence_threshold" not in captured["kwargs"]
    # refine_box is what batch_refine_mask loops over, so it must stay in the
    # normal AI-runtime lane; test_ai_runtime_priority_lanes.py proves the
    # resulting admission order.
    assert captured["kwargs"]["priority"] == sam3_refiner.PRIORITY_NORMAL


def test_refine_box_confidence_is_clamped_and_score_floored(fake_refiner, monkeypatch):
    """Slider at 0 still rejects raw noise (score floor); out-of-range values
    are clamped to [0, 1]."""
    captured = {}

    def spy(image, text=None, box=None, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return None

    monkeypatch.setattr(fake_refiner, "_run_segmentation", spy)
    image = Image.new("RGB", (8, 8))

    fake_refiner.refine_box(image, [1, 1, 4, 4], confidence_threshold=0.0)
    assert captured["score_threshold"] == sam3_refiner._DEFAULT_SCORE_FLOOR
    assert captured["presence_threshold"] == 0.0

    fake_refiner.refine_box(image, [1, 1, 4, 4], confidence_threshold=5.0)
    assert captured["score_threshold"] == 1.0
    assert captured["presence_threshold"] == 1.0


def test_refine_box_confidence_gates_low_score_masks(fake_refiner):
    """Semantic gate: a 0.3-score mask passes with no confidence (back-compat)
    but is refused once the slider demands 0.5 -- the previously-dead knob now
    rejects low-confidence refinements."""
    fake_refiner._model.return_value = SimpleNamespace(
        presence_logits=torch.tensor([[-5.0]]),  # irrelevant for box-only prompts
    )
    masks = torch.zeros((1, 8, 8), dtype=torch.bool)
    masks[0, 1:3, 1:3] = True
    fake_refiner._processor.post_process_instance_segmentation.return_value = _processed_results(
        torch.tensor([0.3]), masks
    )

    image = Image.new("RGB", (8, 8))

    assert fake_refiner.refine_box(image, [1, 1, 4, 4]) is not None
    assert fake_refiner.refine_box(image, [1, 1, 4, 4], confidence_threshold=0.5) is None


# ---------- segment_by_text decoupled presence threshold ---------------------


def test_segment_by_text_uses_decoupled_default_threshold(fake_refiner, monkeypatch):
    """Explicit user text defaults to the looser text gate, NOT the strict 0.5
    auto-detect gate, so deliberately-typed prompts aren't silently rejected."""
    captured = {}

    def spy(image, text=None, box=None, presence_threshold=None, **_kw):
        captured["presence_threshold"] = presence_threshold
        return None

    monkeypatch.setattr(fake_refiner, "_run_segmentation", spy)

    fake_refiner.segment_by_text(Image.new("RGB", (8, 8)), "exposed female breast")

    assert captured["presence_threshold"] == sam3_refiner._DEFAULT_TEXT_PRESENCE_THRESHOLD
    assert captured["presence_threshold"] < _DEFAULT_PRESENCE_THRESHOLD


def test_segment_by_text_respects_explicit_threshold_override(fake_refiner, monkeypatch):
    """A caller-supplied threshold overrides the default and is clamped to [0, 1]."""
    captured = {}

    def spy(image, text=None, box=None, presence_threshold=None, **_kw):
        captured["presence_threshold"] = presence_threshold
        return None

    monkeypatch.setattr(fake_refiner, "_run_segmentation", spy)

    fake_refiner.segment_by_text(Image.new("RGB", (8, 8)), "x", presence_threshold=0.8)
    assert captured["presence_threshold"] == 0.8

    fake_refiner.segment_by_text(Image.new("RGB", (8, 8)), "x", presence_threshold=5.0)
    assert captured["presence_threshold"] == 1.0
