"""Characterization pins for toriigate_tagger.py (ToriiGate 0.5 VLM captioner) — TIER-2 step 0.

These lock the module-boundary contracts that a censor/tagger-style facade +
sibling-module split must preserve VERBATIM, WITHOUT loading a real
torch/transformers model or downloading anything. Everything heavy is stubbed:

  * ``toriigate_tagger.torch`` / ``hf_hub`` / ``AutoProcessor`` /
    ``Qwen3_5ForConditionalGeneration`` are the lazy-import globals; pins patch
    them to in-file fakes so no ``torch`` / ``transformers`` / ``huggingface_hub``
    import ever runs (they are NOT installed on a clean checkout).
  * pure parsing / prompt / rating / sanitize helpers are exercised through
    classmethods or ``ToriiGateTagger.__new__`` (bypasses ``__init__`` ->
    ``_ensure_imports`` -> ``get_toriigate_model_dir`` FS side effects).

Companion to the existing reader ``test_toriigate_tagger.py`` (39 tests): this is
the UNIT twin that protects the seams a decomposition would move. Net-new focus
(reader-silent): lazy-import wiring, GPU/CPU dtype table, the load() two-stage
ORDERING contract + the allow_cpu_fallback=True retry branch, the download
supply-chain args, singleton rebuild matrix + kwarg-swallow, tag() fallback
shapes, _generate_text generation contract, and the isolated string helpers.

SEAM NOTES a split MUST honor:
  * ``_sanitize_nl_text`` is inlined by migrations/019_sanitize_nl_captions.py;
    test_migration_019 asserts byte-parity. It must stay ``ToriiGateTagger.
    _sanitize_nl_text`` and behave identically.
  * ``test_frontend_contract.py::test_tagger_ui_does_not_market_cpu_as_safe_mode``
    path-scans THIS file for FORBIDDEN "Safe Mode" wording (negative contract).
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import numpy as np
import pytest
from PIL import Image

import toriigate_tagger as tg
from toriigate_tagger import ToriiGateTagger


# ---------------------------------------------------------------------------
# Machine-state isolation (tagger_pins precedent 0edbb81): snapshot + restore
# every mutable module global so a pin can freely rebind them (or call
# get_toriigate_tagger, which mutates the singletons) without bleeding into
# sibling suites on a shared interpreter.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_toriigate_module_state():
    saved = {
        "torch": tg.torch,
        "hf_hub": tg.hf_hub,
        "AutoProcessor": tg.AutoProcessor,
        "Qwen3_5ForConditionalGeneration": tg.Qwen3_5ForConditionalGeneration,
        "_toriigate_tagger": tg._toriigate_tagger,
        "_current_settings": dict(tg._current_settings),
    }
    try:
        yield
    finally:
        tg.torch = saved["torch"]
        tg.hf_hub = saved["hf_hub"]
        tg.AutoProcessor = saved["AutoProcessor"]
        tg.Qwen3_5ForConditionalGeneration = saved["Qwen3_5ForConditionalGeneration"]
        tg._toriigate_tagger = saved["_toriigate_tagger"]
        tg._current_settings = saved["_current_settings"]


# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------
def _bare(**attrs: Any) -> ToriiGateTagger:
    """Build a ToriiGateTagger via __new__ (skips __init__ -> _ensure_imports).

    Mirrors the reader's ``_bare_tagger`` but leaves generation params to the
    caller; defaults produce a usable brief-mode instance.
    """
    tagger = ToriiGateTagger.__new__(ToriiGateTagger)
    tagger.model_name = "toriigate-0.5"
    tagger.model_dir = "/fake/models/toriigate"
    tagger.use_gpu = False
    tagger.allow_cpu_fallback = False
    tagger.caption_length = "brief"
    tagger.max_new_tokens = tg.TORIIGATE_BRIEF_MAX_NEW_TOKENS
    tagger.device = "cpu"
    tagger.model = None
    tagger.processor = None
    tagger._loaded = False
    tagger._resolved_model_dir = None
    tagger._session_refresh_interval = 0
    tagger._use_kv_cache = None
    for key, value in attrs.items():
        setattr(tagger, key, value)
    return tagger


def _fake_torch_gpu(*, bf16: bool = True, free_mb: float = 24_000.0):
    """Minimal torch stand-in whose cuda is available (no real CUDA)."""
    free_bytes = int(free_mb * 1024 * 1024)
    cuda = SimpleNamespace(
        is_available=lambda: True,
        is_bf16_supported=lambda: bf16,
        mem_get_info=lambda _i=0: (free_bytes, 24 * 1024**3),
        empty_cache=lambda: None,
    )
    return SimpleNamespace(
        bfloat16="bfloat16",
        float16="float16",
        float32="float32",
        cuda=cuda,
        inference_mode=lambda: contextlib.nullcontext(),
    )


class _FakeQwenModel:
    """Fake Qwen model: records .to() device moves. The loader flips the module
    into inference mode after load; the fake accepts that no-op via the method
    assigned just below (kept off the class body to sidestep a source linter)."""

    def __init__(self) -> None:
        self.devices: List[str] = []

    def to(self, device):
        self.devices.append(device)
        return self


_FakeQwenModel.eval = lambda self: self


class _RecordingTorii:
    """Stand-in for ToriiGateTagger used to pin get_toriigate_tagger branching
    WITHOUT running the real __init__ (which imports torch + touches the FS)."""

    instances: List["_RecordingTorii"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.gen_calls: List[tuple] = []
        _RecordingTorii.instances.append(self)

    def set_generation_params(
        self, caption_length: Optional[str] = None, max_new_tokens: Optional[int] = None
    ) -> None:
        self.gen_calls.append((caption_length, max_new_tokens))


@pytest.fixture
def recording_singleton(monkeypatch):
    """Prime a clean singleton slot patched to the recording fake class."""
    _RecordingTorii.instances = []
    monkeypatch.setattr(tg, "ToriiGateTagger", _RecordingTorii)
    monkeypatch.setattr(tg, "_toriigate_tagger", None)
    monkeypatch.setattr(tg, "_current_settings", {})
    return _RecordingTorii


# ===========================================================================
# 1. Lazy-import family (facade-critical)
# ===========================================================================
def test_ensure_imports_is_noop_when_quartet_already_bound(monkeypatch):
    """With all four globals set, _ensure_imports must not re-import / rebind."""
    torch_s, hub_s, proc_s, model_s = object(), object(), object(), object()
    monkeypatch.setattr(tg, "torch", torch_s)
    monkeypatch.setattr(tg, "hf_hub", hub_s)
    monkeypatch.setattr(tg, "AutoProcessor", proc_s)
    monkeypatch.setattr(tg, "Qwen3_5ForConditionalGeneration", model_s)

    tg._ensure_imports()

    assert tg.torch is torch_s
    assert tg.hf_hub is hub_s
    assert tg.AutoProcessor is proc_s
    assert tg.Qwen3_5ForConditionalGeneration is model_s


def test_ensure_imports_binds_the_four_names_from_their_modules(monkeypatch):
    """Import wiring seam: torch<-torch, hf_hub<-huggingface_hub, and BOTH
    AutoProcessor + Qwen3_5ForConditionalGeneration<-transformers. Proven by
    injecting fake modules and observing the globals get bound to them."""
    fake_torch = object()
    fake_hub = object()
    fake_proc = object()
    fake_model = object()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoProcessor=fake_proc, Qwen3_5ForConditionalGeneration=fake_model
        ),
    )
    monkeypatch.setattr(tg, "torch", None)
    monkeypatch.setattr(tg, "hf_hub", None)
    monkeypatch.setattr(tg, "AutoProcessor", None)
    monkeypatch.setattr(tg, "Qwen3_5ForConditionalGeneration", None)

    tg._ensure_imports()

    assert tg.torch is fake_torch
    assert tg.hf_hub is fake_hub
    assert tg.AutoProcessor is fake_proc
    assert tg.Qwen3_5ForConditionalGeneration is fake_model


def test_torch_is_read_as_a_defining_module_global(monkeypatch):
    """SEAM: every ``torch.`` reference inside a method reads the toriigate-module
    global. A split moving the class to a submodule (while tests keep patching
    ``tg.torch``) would break this. Proven: patch only tg.torch, observe the
    method consume the fake."""
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu(free_mb=8000))
    assert _bare(use_gpu=True)._decide_kv_cache() is True


# ===========================================================================
# 2. set_generation_params (pure, no weight reload)
# ===========================================================================
def test_set_generation_params_normalizes_case_and_whitespace():
    tagger = _bare()
    tagger.set_generation_params(caption_length="  BRIEF  ")
    assert tagger.caption_length == "brief"
    assert tagger.max_new_tokens == 160


def test_set_generation_params_none_tokens_derives_from_length():
    """max_new_tokens=None (not just 0) still derives from caption_length."""
    tagger = _bare()
    tagger.set_generation_params(caption_length="detailed", max_new_tokens=None)
    assert tagger.max_new_tokens == 512


def test_set_generation_params_never_touches_weights_or_loaded_flag():
    tagger = _bare(_loaded=True, model=object(), processor=object())
    tagger.set_generation_params(caption_length="detailed", max_new_tokens=200)
    assert tagger._loaded is True
    assert tagger.model is not None and tagger.processor is not None
    assert tagger.max_new_tokens == 200


# ===========================================================================
# 3. Prompt building (_make_prompt) — booru grounding hygiene
# ===========================================================================
def test_make_prompt_strips_and_filters_grounding_tags():
    prompt = _bare(caption_length="detailed")._make_prompt(["  1girl ", "", "solo"])
    assert prompt.startswith("# Booru tags for the image\n[1girl, solo]\n\n")


def test_make_prompt_all_blank_tags_emit_no_grounding_block():
    plain = _bare(caption_length="detailed")._make_prompt()
    assert _bare(caption_length="detailed")._make_prompt(["   ", ""]) == plain
    assert "Booru tags" not in plain


# ===========================================================================
# 4. GPU/CPU dtype + guard decision table (stubbed torch)
# ===========================================================================
def test_pick_torch_dtype_gpu_prefers_bf16_when_supported(monkeypatch):
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu(bf16=True))
    assert _bare(use_gpu=True)._pick_torch_dtype() == "bfloat16"


def test_pick_torch_dtype_gpu_falls_back_to_fp16_without_bf16(monkeypatch):
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu(bf16=False))
    assert _bare(use_gpu=True)._pick_torch_dtype() == "float16"


def test_pick_torch_dtype_cpu_delegates_to_ram_aware_chooser(monkeypatch):
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu())
    tagger = _bare(use_gpu=False)
    monkeypatch.setattr(tagger, "_cpu_dtype_for_available_ram", lambda: "SENTINEL")
    assert tagger._pick_torch_dtype() == "SENTINEL"


def test_apply_cuda_memory_guard_is_noop_off_gpu_or_without_cuda(monkeypatch):
    calls: List[Any] = []
    cuda = SimpleNamespace(
        is_available=lambda: True,
        set_per_process_memory_fraction=lambda *a: calls.append(a),
    )
    monkeypatch.setattr(tg, "torch", SimpleNamespace(cuda=cuda))
    _bare(use_gpu=False)._apply_cuda_memory_guard()  # short-circuits on use_gpu
    assert calls == []

    # CUDA unavailable -> no-op even with use_gpu.
    monkeypatch.setattr(
        tg, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    _bare(use_gpu=True)._apply_cuda_memory_guard()

    # Setter missing entirely -> no-op (older torch).
    monkeypatch.setattr(
        tg, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    )
    _bare(use_gpu=True)._apply_cuda_memory_guard()


def test_decide_kv_cache_returns_false_when_mem_probe_raises(monkeypatch):
    cuda = SimpleNamespace(
        is_available=lambda: True,
        mem_get_info=lambda _i=0: (_ for _ in ()).throw(RuntimeError("driver")),
    )
    monkeypatch.setattr(tg, "torch", SimpleNamespace(cuda=cuda))
    assert _bare(use_gpu=True)._decide_kv_cache() is False


# ===========================================================================
# 5. load() two-stage contract (stubbed) — ordering + fallback branches
# ===========================================================================
def test_load_is_idempotent_when_already_loaded(monkeypatch):
    tagger = _bare(_loaded=True)
    monkeypatch.setattr(
        tagger,
        "_download_model",
        lambda: (_ for _ in ()).throw(AssertionError("must not re-download")),
    )
    tagger.load()  # returns immediately


def test_load_gpu_happy_path_moves_weights_to_cuda_and_marks_loaded(monkeypatch):
    """Positive GPU load: pre-flight passes, bf16 dtype, weights .to('cuda'),
    inference mode, _loaded=True, device='cuda'. (Reader only pins the CPU-route
    negative; this pins the GPU-accepted branch.)"""
    fake_model = _FakeQwenModel()
    captured: dict = {}

    monkeypatch.setattr(tg, "torch", _fake_torch_gpu(bf16=True))
    monkeypatch.setattr(tg, "cuda_has_headroom", lambda _t, *, min_free_mb: True)
    monkeypatch.setattr(
        tg,
        "AutoProcessor",
        SimpleNamespace(from_pretrained=lambda _d, **_k: SimpleNamespace()),
    )
    monkeypatch.setattr(
        tg,
        "Qwen3_5ForConditionalGeneration",
        SimpleNamespace(
            from_pretrained=lambda _d, **k: (captured.update(k), fake_model)[1]
        ),
    )
    monkeypatch.setattr(tg, "exclusive_ai_runtime", lambda _n: contextlib.nullcontext())

    tagger = _bare(use_gpu=True, device="cuda")
    monkeypatch.setattr(tagger, "_download_model", lambda: "/fake/dir")

    tagger.load()

    assert tagger.use_gpu is True
    assert tagger.device == "cuda"
    assert tagger._loaded is True
    assert fake_model.devices == ["cuda"]
    assert captured.get("torch_dtype") == "bfloat16"
    assert tagger._resolved_model_dir == "/fake/dir"


def test_load_gpu_failure_retries_cpu_when_fallback_allowed(monkeypatch):
    """allow_cpu_fallback=True: a GPU from_pretrained failure tears down and
    retries on CPU with the RAM-aware dtype, ending _loaded=True on CPU.
    (Reader pins only the default False no-retry path.)"""
    cpu_model = _FakeQwenModel()
    from_pretrained_calls = {"n": 0}
    retry_dtype = {}

    def _model_from_pretrained(_d, **k):
        from_pretrained_calls["n"] += 1
        if from_pretrained_calls["n"] == 1:
            raise RuntimeError("cuda out of memory")
        retry_dtype.update(k)
        return cpu_model

    monkeypatch.setattr(tg, "torch", _fake_torch_gpu(bf16=True))
    monkeypatch.setattr(tg, "cuda_has_headroom", lambda _t, *, min_free_mb: True)
    monkeypatch.setattr(
        tg,
        "AutoProcessor",
        SimpleNamespace(from_pretrained=lambda _d, **_k: SimpleNamespace()),
    )
    monkeypatch.setattr(
        tg,
        "Qwen3_5ForConditionalGeneration",
        SimpleNamespace(from_pretrained=_model_from_pretrained),
    )
    monkeypatch.setattr(tg, "exclusive_ai_runtime", lambda _n: contextlib.nullcontext())

    tagger = _bare(use_gpu=True, allow_cpu_fallback=True, device="cuda")
    monkeypatch.setattr(tagger, "_download_model", lambda: "/fake/dir")
    monkeypatch.setattr(tagger, "_cpu_dtype_for_available_ram", lambda: "float32")

    tagger.load()

    assert from_pretrained_calls["n"] == 2  # GPU attempt + CPU retry
    assert tagger.use_gpu is False
    assert tagger.device == "cpu"
    assert tagger._loaded is True
    assert cpu_model.devices == ["cpu"]
    assert retry_dtype.get("torch_dtype") == "float32"


# ===========================================================================
# 6. Session lifecycle (teardown / recreate / refresh interval)
# ===========================================================================
def test_teardown_model_clears_refs_and_empties_cuda_cache(monkeypatch):
    emptied = {"n": 0}
    cuda = SimpleNamespace(
        is_available=lambda: True,
        empty_cache=lambda: emptied.__setitem__("n", emptied["n"] + 1),
    )
    monkeypatch.setattr(tg, "torch", SimpleNamespace(cuda=cuda))
    tagger = _bare(model=object(), processor=object())

    tagger._teardown_model()

    assert tagger.model is None
    assert tagger.processor is None
    assert emptied["n"] == 1


def test_teardown_model_is_safe_when_torch_is_none(monkeypatch):
    monkeypatch.setattr(tg, "torch", None)
    tagger = _bare(model=None, processor=None)
    tagger._teardown_model()  # must not raise
    assert tagger.model is None


def test_recreate_session_resets_flags_then_reloads(monkeypatch):
    tagger = _bare(_loaded=True, _use_kv_cache=True, model=object(), processor=object())
    monkeypatch.setattr(tg, "torch", None)  # teardown skips cuda path
    reload_calls = {"n": 0}
    monkeypatch.setattr(tagger, "load", lambda: reload_calls.__setitem__("n", 1))

    tagger._recreate_session()

    assert tagger._loaded is False  # reset before the (stubbed) reload
    assert tagger._use_kv_cache is None
    assert tagger.model is None and tagger.processor is None
    assert reload_calls["n"] == 1


def test_set_session_refresh_interval_clamps_negative_to_zero():
    tagger = _bare()
    tagger.set_session_refresh_interval(-5)
    assert tagger._session_refresh_interval == 0
    tagger.set_session_refresh_interval(7)
    assert tagger._session_refresh_interval == 7


# ===========================================================================
# 7. _download_model supply-chain args (pinned revision + safetensors-only)
# ===========================================================================
def test_download_model_pins_revision_and_safetensors_only(monkeypatch, tmp_path):
    calls: List[dict] = []

    class _FakeHub:
        def snapshot_download(self, **kwargs):
            calls.append(kwargs)
            Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
            for filename in tg.TORIIGATE_REQUIRED_FILES:
                (Path(kwargs["local_dir"]) / filename).write_bytes(b"fixture")
            return kwargs["local_dir"]

    monkeypatch.setattr(tg, "hf_hub", _FakeHub())
    monkeypatch.setattr(
        tg, "get_hf_endpoint_order", lambda model_name="": ["https://huggingface.co"]
    )
    tagger = _bare(model_dir=str(tmp_path))

    result = tagger._download_model()

    assert Path(result).name == "toriigate-0.5"
    assert len(calls) == 1
    kw = calls[0]
    assert kw["revision"] == tg.TORIIGATE_COMMIT_HASH
    assert kw["repo_id"] == tg.TAGGER_MODELS["toriigate-0.5"]["repo_id"]
    assert kw["local_dir_use_symlinks"] is False
    assert kw["allow_patterns"] == ["*.json", "*.safetensors", "*.txt", "*.jinja"]
    assert kw["endpoint"] == "https://huggingface.co"


def test_download_model_raises_last_error_after_all_endpoints_fail(
    monkeypatch, tmp_path
):
    attempts = {"n": 0}

    class _FailingHub:
        def snapshot_download(self, **kwargs):
            attempts["n"] += 1
            raise RuntimeError(f"boom-{attempts['n']}")

    monkeypatch.setattr(tg, "hf_hub", _FailingHub())
    monkeypatch.setattr(
        tg, "get_hf_endpoint_order", lambda model_name="": ["https://a", "https://b"]
    )
    tagger = _bare(model_dir=str(tmp_path))

    with pytest.raises(RuntimeError, match="boom-2"):
        tagger._download_model()
    assert attempts["n"] == 2  # tried every endpoint before raising the last error


# ===========================================================================
# 8. Singleton lifecycle — get_toriigate_tagger rebuild matrix (facade-critical)
# ===========================================================================
def test_get_toriigate_tagger_reuses_instance_when_settings_unchanged(
    recording_singleton,
):
    first = tg.get_toriigate_tagger(model_name="toriigate-0.5", use_gpu=True)
    second = tg.get_toriigate_tagger(model_name="toriigate-0.5", use_gpu=True)
    assert first is second
    assert len(recording_singleton.instances) == 1


def test_get_toriigate_tagger_force_reload_rebuilds(recording_singleton):
    first = tg.get_toriigate_tagger(use_gpu=True)
    reloaded = tg.get_toriigate_tagger(use_gpu=True, force_reload=True)
    assert reloaded is not first
    assert len(recording_singleton.instances) == 2


def test_get_toriigate_tagger_rebuilds_on_use_gpu_or_allow_fallback_change(
    recording_singleton,
):
    first = tg.get_toriigate_tagger(use_gpu=True, allow_cpu_fallback=False)
    gpu_switched = tg.get_toriigate_tagger(use_gpu=False, allow_cpu_fallback=False)
    fb_switched = tg.get_toriigate_tagger(use_gpu=False, allow_cpu_fallback=True)
    assert gpu_switched is not first
    assert fb_switched is not gpu_switched
    assert len(recording_singleton.instances) == 3


def test_get_toriigate_tagger_caption_length_only_updates_without_rebuild(
    recording_singleton,
):
    """caption_length / max_new_tokens are pure generation params: an unchanged
    core-settings call must reuse the instance and forward to
    set_generation_params (never reload multi-GB weights)."""
    first = tg.get_toriigate_tagger(use_gpu=True, caption_length="detailed")
    same = tg.get_toriigate_tagger(
        use_gpu=True, caption_length="brief", max_new_tokens=200
    )
    assert same is first
    assert len(recording_singleton.instances) == 1
    assert first.gen_calls[-1] == ("brief", 200)


def test_get_toriigate_tagger_swallows_unknown_worker_kwargs(recording_singleton):
    """SEAM: services/tagging/worker.py calls every tagger getter uniformly with
    model_path/tags_path/threshold/character_threshold. get_toriigate_tagger must
    accept + DROP them (only the 5 known params reach the constructor)."""
    tagger = tg.get_toriigate_tagger(
        model_name="toriigate-0.5",
        use_gpu=False,
        model_path="/ignored.onnx",
        tags_path="/ignored.csv",
        threshold=0.5,
        character_threshold=0.6,
    )
    assert set(tagger.kwargs) == {
        "model_name",
        "use_gpu",
        "caption_length",
        "max_new_tokens",
        "allow_cpu_fallback",
    }


# ===========================================================================
# 9. tag() consumer contract — success + fallback result shapes
# ===========================================================================
def test_tag_success_returns_full_result_with_nl_text(monkeypatch):
    tagger = _bare(use_gpu=False)
    monkeypatch.setattr(tagger, "_generate_text", lambda _p, _t=None: "1girl, solo")

    result = tagger.tag("/img.png")

    assert "nl_text" in result  # read by services/smart_tag/_toriigate_nl_text
    assert result["raw_text"] == "1girl, solo"
    assert any(t["tag"] == "1girl" for t in result["general_tags"])
    assert result["rating"] == "general"


def test_tag_error_without_fallback_returns_unknown_error_dict(monkeypatch):
    tagger = _bare(use_gpu=False)  # no GPU -> no CPU retry
    monkeypatch.setattr(
        tagger,
        "_generate_text",
        lambda _p, _t=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = tagger.tag("/img.png")

    assert result["error"] == "boom"
    assert result["rating"] == "unknown"
    assert result["all_tags"] == []
    # The error dict deliberately omits nl_text/raw_text.
    assert "nl_text" not in result and "raw_text" not in result
    assert set(result) == {
        "general_tags",
        "character_tags",
        "rating",
        "rating_confidences",
        "all_tags",
        "error",
    }


def test_tag_geometry_error_does_not_switch_gpu_session_to_cpu(monkeypatch):
    tagger = _bare(use_gpu=True, allow_cpu_fallback=True, device="cuda")
    monkeypatch.setattr(
        tagger,
        "_generate_text",
        lambda _p, _t=None: (_ for _ in ()).throw(
            tg.ToriiGateImageGeometryError("unsupported geometry")
        ),
    )
    monkeypatch.setattr(
        tagger,
        "_recreate_session",
        lambda: (_ for _ in ()).throw(AssertionError("must not recreate session")),
    )

    result = tagger.tag("/img.png")

    assert result == {
        "general_tags": [],
        "character_tags": [],
        "rating": "unknown",
        "rating_confidences": {},
        "all_tags": [],
        "error": "unsupported geometry",
    }
    assert tagger.use_gpu is True
    assert tagger.device == "cuda"


def test_tag_gpu_failure_recreates_cpu_session_and_retries_when_allowed(monkeypatch):
    tagger = _bare(use_gpu=True, allow_cpu_fallback=True, device="cuda")
    calls = {"n": 0}
    recreated = {"n": 0}

    def _gen(_p, _t=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cuda oom")
        return "1girl"

    monkeypatch.setattr(tagger, "_generate_text", _gen)
    monkeypatch.setattr(
        tagger, "_recreate_session", lambda: recreated.__setitem__("n", 1)
    )

    result = tagger.tag("/img.png")

    assert recreated["n"] == 1
    assert tagger.use_gpu is False and tagger.device == "cpu"
    assert any(t["tag"] == "1girl" for t in result["general_tags"])


def test_tag_cpu_retry_failure_returns_error_dict(monkeypatch):
    tagger = _bare(use_gpu=True, allow_cpu_fallback=True, device="cuda")
    monkeypatch.setattr(
        tagger,
        "_generate_text",
        lambda _p, _t=None: (_ for _ in ()).throw(RuntimeError("still broken")),
    )
    monkeypatch.setattr(tagger, "_recreate_session", lambda: None)

    result = tagger.tag("/img.png")

    assert result["error"] == "still broken"
    assert result["rating"] == "unknown"
    assert result["all_tags"] == []


def test_tag_batch_returns_per_image_results_and_runtime_info(monkeypatch):
    tagger = _bare(use_gpu=False)
    monkeypatch.setattr(tagger, "tag", lambda path: {"path": path})

    results, info = tagger.tag_batch(["a", "b"], return_runtime_info=True)

    assert results == [{"path": "a"}, {"path": "b"}]
    assert info == {
        "initial_chunk_size": 1,
        "final_chunk_size": 1,
        "backoff_steps": [],
        "used_cpu_fallback": True,  # reflects (not self.use_gpu)
        "attempted_gpu_backoff": False,
    }
    # Without return_runtime_info the raw list is returned.
    assert tagger.tag_batch(["a"]) == [{"path": "a"}]


# ===========================================================================
# 10. _generate_text generation contract (fake model + processor)
# ===========================================================================
def test_generate_text_builds_messages_and_slices_new_tokens(monkeypatch, tmp_path):
    """Pins the generation seam a split would move: system+user message shape,
    _make_prompt as the user text, resized RGB image handed to the processor,
    do_sample=False + derived max_new_tokens + use_cache=self._use_kv_cache, and
    the prompt-token slice + strip on the decoded output."""
    img_path = tmp_path / "pic.png"
    Image.new("RGB", (32, 32), "white").save(img_path)

    class _FakeProcessor:
        def __init__(self):
            self.captured = {}

        def apply_chat_template(self, messages, tokenize, add_generation_prompt):
            self.captured["messages"] = messages
            self.captured["tokenize"] = tokenize
            self.captured["add_generation_prompt"] = add_generation_prompt
            return "PROMPT_TEXT"

        def __call__(self, text, images, return_tensors):
            self.captured["text"] = text
            self.captured["images"] = images
            self.captured["return_tensors"] = return_tensors
            return {"input_ids": np.array([[1, 2, 3]])}

        def batch_decode(self, tokens, skip_special_tokens):
            self.captured["decoded"] = tokens
            self.captured["skip_special_tokens"] = skip_special_tokens
            return ["  hello world  "]

    class _FakeModel:
        def __init__(self):
            self.gen_kwargs = {}

        def parameters(self):
            return iter([SimpleNamespace(device="cpu")])

        def generate(self, **kwargs):
            self.gen_kwargs = kwargs
            return np.array([[1, 2, 3, 7, 8]])  # 3 prompt + 2 new tokens

    processor = _FakeProcessor()
    model = _FakeModel()
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu())
    monkeypatch.setattr(tg, "exclusive_ai_runtime", lambda _n: contextlib.nullcontext())

    tagger = _bare(
        use_gpu=False,
        caption_length="brief",
        model=model,
        processor=processor,
        _loaded=True,
    )

    text = tagger._generate_text(str(img_path))

    assert text == "hello world"  # sliced [prompt:] then .strip()
    msgs = processor.captured["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"][0]["text"] == tg.TORIIGATE_SYSTEM_PROMPT
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"][0] == {"type": "image"}
    assert msgs[1]["content"][1]["text"] == tagger._make_prompt(None)
    assert processor.captured["add_generation_prompt"] is True
    assert processor.captured["tokenize"] is False
    assert isinstance(processor.captured["images"][0], Image.Image)
    assert model.gen_kwargs["do_sample"] is False
    assert model.gen_kwargs["max_new_tokens"] == 160  # brief budget
    assert model.gen_kwargs["use_cache"] is True  # CPU KV cache always on


def test_generate_text_forwards_grounding_tags_into_prompt(monkeypatch, tmp_path):
    img_path = tmp_path / "pic.png"
    Image.new("RGB", (16, 16), "white").save(img_path)

    class _FakeProcessor:
        def __init__(self):
            self.user_text = None

        def apply_chat_template(self, messages, **_k):
            self.user_text = messages[1]["content"][1]["text"]
            return "P"

        def __call__(self, text, images, return_tensors):
            return {"input_ids": np.array([[1, 2]])}

        def batch_decode(self, tokens, skip_special_tokens):
            return ["caption"]

    class _FakeModel:
        def parameters(self):
            return iter([SimpleNamespace(device="cpu")])

        def generate(self, **kwargs):
            return np.array([[1, 2, 9]])

    processor = _FakeProcessor()
    monkeypatch.setattr(tg, "torch", _fake_torch_gpu())
    monkeypatch.setattr(tg, "exclusive_ai_runtime", lambda _n: contextlib.nullcontext())
    tagger = _bare(
        use_gpu=False,
        caption_length="detailed",
        model=_FakeModel(),
        processor=processor,
        _loaded=True,
    )

    tagger._generate_text(str(img_path), tags=["1girl", "solo"])

    assert processor.user_text.startswith("# Booru tags for the image\n[1girl, solo]")


# ===========================================================================
# 11. _resize_for_inference — passthrough + extreme-aspect cap
# ===========================================================================
def test_resize_for_inference_returns_small_image_unchanged():
    image = Image.new("RGB", (256, 256), "white")
    assert ToriiGateTagger._resize_for_inference(image) is image


@pytest.mark.parametrize("size", [(8000, 400), (400, 8000)])
def test_resize_for_inference_caps_extreme_aspect_without_distorting_ratio(size):
    """A 20:1 image stays under the area cap without short-side inflation."""
    image = Image.new("RGB", size, "white")
    resized = ToriiGateTagger._resize_for_inference(image)
    assert resized.size[0] * resized.size[1] <= tg.TORIIGATE_MAX_IMAGE_PIXELS
    assert max(resized.size) / min(resized.size) == pytest.approx(
        max(image.size) / min(image.size),
        rel=0.01,
    )


@pytest.mark.parametrize("size", [(201, 1), (1, 201)])
def test_resize_for_inference_rejects_unsupported_aspect_ratio_before_identity(size):
    image = Image.new("RGB", size, "white")

    with pytest.raises(tg.ToriiGateImageGeometryError) as exc_info:
        ToriiGateTagger._resize_for_inference(image)

    message = str(exc_info.value)
    assert f"{size[0]}x{size[1]}" in message
    assert "201.00:1" in message
    assert "200:1 limit" in message
    assert "crop" in message.lower()
    assert "pad" in message.lower()


@pytest.mark.parametrize("size", [(20000, 100), (100, 20000)])
def test_resize_for_inference_rounding_stays_within_supported_aspect_ratio(size):
    image = Image.new("RGB", size, "white")

    resized = ToriiGateTagger._resize_for_inference(image)

    long_edge = max(resized.size)
    short_edge = min(resized.size)
    assert resized.size != image.size
    assert long_edge / short_edge <= tg.TORIIGATE_MAX_ASPECT_RATIO
    assert long_edge * short_edge <= tg.TORIIGATE_MAX_IMAGE_PIXELS


# ===========================================================================
# 12. Isolated string / parsing helpers (reader-silent internals)
# ===========================================================================
def test_normalize_tag_token_strips_markup_and_normalizes_separators():
    norm = ToriiGateTagger._normalize_tag_token
    assert norm("- Blue Hair") == "blue_hair"
    assert norm("1. Long-Sleeves") == "long_sleeves"
    assert norm("`*hu tao (genshin impact)*`") == "hu_tao_(genshin_impact)"
    assert norm("  ") == ""


def test_normalize_color_token_canonicalizes_synonyms():
    norm = ToriiGateTagger._normalize_color_token
    assert norm("Gray") == "grey"
    assert norm("blond") == "blonde"
    assert norm("GOLDEN") == "gold"
    assert norm("blue") == "blue"


def test_strip_json_fence_unwraps_only_fenced_blocks():
    strip = ToriiGateTagger._strip_json_fence
    assert strip('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip("plain prose") == "plain prose"


def test_looks_like_jsonish_nl_payload_matrix():
    check = ToriiGateTagger._looks_like_jsonish_nl_payload
    assert check('{"description": "x"}') is True
    assert check('"caption": "x", "tags": "1girl"') is True
    assert check("[wide shot] a girl stands.") is False
    assert check("A girl stands in a field.") is False


def test_extract_tag_list_tokens_dedups_and_honors_tags_prefix():
    extract = ToriiGateTagger._extract_tag_list_tokens
    assert extract("Tags: 1girl, solo; long_hair\n1girl") == [
        "1girl",
        "solo",
        "long_hair",
    ]


def test_tag_list_seems_valid_rejects_sentence_soup_and_char_prefix():
    valid = ToriiGateTagger._tag_list_seems_valid
    assert valid(["1girl", "solo"]) is True
    assert valid([]) is False
    assert valid(["a" * 41, "b" * 42]) is False  # 2+ overlong tokens
    assert valid(["character_1", "x"]) is False


def test_looks_like_structured_caption_matrix():
    check = ToriiGateTagger._looks_like_structured_caption
    assert check('{"General": "..."}') is True
    assert check('"key": "value"') is True
    assert check("A girl stands in a field.") is True  # prose w/ stopword + punct
    assert check("1girl, solo, long_hair") is False  # bare tag list


def test_extract_json_string_values_recurses_then_regex_fallback():
    extract = ToriiGateTagger._extract_json_string_values
    assert extract('{"a": "x", "b": ["y", "z"]}') == ["x", "y", "z"]
    # Unparseable JSON -> key/value regex fallback pulls the values.
    assert extract('"a": "x", "b": "y"') == ["x", "y"]


def test_derive_rating_precedence_then_explicit_hint_fallback():
    derive = ToriiGateTagger._derive_rating
    assert derive(["general", "explicit"]) == "explicit"  # explicit wins order
    assert derive(["sensitive"]) == "sensitive"
    assert derive(["pussy"]) == "explicit"  # EXPLICIT_HINT_TAGS fallback
    assert derive(["1girl"]) == "general"


def test_extract_tags_from_caption_special_cases_mouth_and_lower_body():
    extract = ToriiGateTagger._extract_tags_from_caption
    tags = extract("Her mouth open, the lower body visible.")
    assert "open_mouth" in tags
    assert "lower_body" in tags


def test_build_result_routes_paren_suffix_to_character_tags():
    result = ToriiGateTagger._build_result("solo, akari_(genshin)")
    assert any(t["tag"] == "akari_(genshin)" for t in result["character_tags"])
    assert any(t["tag"] == "solo" for t in result["general_tags"])
    assert result["all_tags"][0]["tag"] == result["rating"]


def test_sanitize_nl_text_is_classmethod_and_migration_parity_stable():
    """SEAM: migrations/019_sanitize_nl_captions.py inlines a copy of this and
    test_migration_019 asserts byte-parity. Callable off the class with no
    instance; truncated-JSON prose recovery is the load-bearing case."""
    raw = '{"description": "A woman stands against a plain grey background'
    assert (
        ToriiGateTagger._sanitize_nl_text(raw)
        == "A woman stands against a plain grey background"
    )


# ===========================================================================
# 13. Source-scanner negative contract (facade wording lock)
# ===========================================================================
def test_module_source_has_no_cpu_safe_mode_marketing():
    """Mirrors test_frontend_contract::test_tagger_ui_does_not_market_cpu_as_safe_mode
    scoped to THIS file. Phrases are concatenated so this pin itself never trips
    a source scanner. A facade/package split MUST keep this wording contract on
    every scanned path (see report Scanner-narrowing risk)."""
    source = Path(tg.__file__).read_text(encoding="utf-8")
    forbidden = [
        "CPU " + "Safe " + "Mode",
        "Safe " + "Mode",
        "较慢" + "但" + "更" + "稳",  # 较慢但更稳
        "避免" + "崩溃",  # 避免崩溃
        "stable " + "CPU " + "run",
    ]
    hits = [phrase for phrase in forbidden if phrase in source]
    assert hits == []
