"""Lucida subject-matting runtime contracts."""
from __future__ import annotations

import sys
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import lucida_matting


def test_soft_alpha_to_mask_preserves_fractional_values_and_source_size():
    alpha = np.asarray(
        [
            [0.0, 0.25],
            [0.5, 1.0],
        ],
        dtype=np.float32,
    )

    mask = lucida_matting.soft_alpha_to_mask(alpha, source_size=(2, 2))

    assert mask.mode == "L"
    assert mask.size == (2, 2)
    assert [mask.getpixel((x, y)) for y in range(2) for x in range(2)] == [0, 64, 128, 255]


def test_prepare_checkpoint_uses_pinned_revision(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        target.mkdir(parents=True, exist_ok=True)
        for filename in lucida_matting.LUCIDA_REQUIRED_FILES:
            (target / filename).write_bytes(b"fixture")
        return str(target)

    monkeypatch.setattr(lucida_matting, "get_lucida_model_dir", lambda: str(tmp_path / "lucida"))
    monkeypatch.setattr(
        lucida_matting,
        "get_hf_endpoint_order",
        lambda model_name: ["https://huggingface.co"],
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    checkpoint = lucida_matting.prepare_checkpoint()

    assert checkpoint == str((tmp_path / "lucida").resolve())
    assert calls == [
        {
            "repo_id": lucida_matting.LUCIDA_MODEL_ID,
            "revision": lucida_matting.LUCIDA_REVISION,
            "local_dir": str(tmp_path / "lucida"),
            "allow_patterns": list(lucida_matting.LUCIDA_REQUIRED_FILES),
            "endpoint": "https://huggingface.co",
        }
    ]
    records = [
        record
        for record in caplog.records
        if record.message == "Model artifact validation"
    ]
    assert {record.artifact_file for record in records} == set(
        lucida_matting.LUCIDA_REQUIRED_FILES
    )
    assert all(record.status == "file_ready" for record in records)


def test_prepare_checkpoint_reports_gated_huggingface_access(monkeypatch, tmp_path, caplog):
    class _Response:
        status_code = 403

    class _GatedError(RuntimeError):
        response = _Response()

    def snapshot_download(**_kwargs):
        raise _GatedError("403 Forbidden: gated repository")

    monkeypatch.setattr(lucida_matting, "get_lucida_model_dir", lambda: str(tmp_path / "lucida"))
    monkeypatch.setattr(
        lucida_matting,
        "get_hf_endpoint_order",
        lambda model_name: ["https://huggingface.co"],
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    with pytest.raises(lucida_matting.LucidaUnavailableError) as error:
        lucida_matting.prepare_checkpoint()

    message = str(error.value).lower()
    assert "403" in message
    assert "gated" in message
    assert "token" in message or "accept" in message
    assert any(record.__dict__.get("status_code") == 403 for record in caplog.records)


def test_remote_code_import_error_is_actionable(monkeypatch, tmp_path):
    checkpoint = tmp_path / "lucida"
    checkpoint.mkdir()
    for filename in lucida_matting.LUCIDA_REQUIRED_FILES:
        (checkpoint / filename).write_bytes(b"fixture")

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise ImportError("No module named 'kornia'")

    monkeypatch.setattr(lucida_matting, "get_checkpoint_path", lambda: str(checkpoint))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoModelForImageSegmentation=FakeAutoModel),
    )
    lucida_matting._model_by_device.clear()

    with pytest.raises(lucida_matting.LucidaUnavailableError, match="kornia"):
        lucida_matting._load_model("cpu")


def test_model_load_uses_guard_and_local_only_checkpoint(monkeypatch, tmp_path):
    checkpoint = tmp_path / "lucida"
    checkpoint.mkdir()
    for filename in lucida_matting.LUCIDA_REQUIRED_FILES:
        (checkpoint / filename).write_bytes(b"fixture")
    calls = []
    guard_labels = []

    class FakeModel:
        def to(self, device):
            calls.append(("to", device))
            return self

        def eval(self):
            calls.append(("eval",))
            return self

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append(("from_pretrained", path, kwargs))
            return FakeModel()

    @contextmanager
    def guard(label, **_kwargs):
        guard_labels.append(label)
        yield

    monkeypatch.setattr(lucida_matting, "get_checkpoint_path", lambda: str(checkpoint))
    monkeypatch.setattr(lucida_matting, "exclusive_ai_runtime", guard)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoModelForImageSegmentation=FakeAutoModel),
    )
    lucida_matting._model_by_device.clear()

    lucida_matting._load_model("cpu")

    assert guard_labels == ["lucida-load"]
    assert calls[0] == (
        "from_pretrained",
        str(checkpoint),
        {
            "trust_remote_code": True,
            "local_files_only": True,
            "use_safetensors": True,
        },
    )
    assert calls[1:] == [("to", "cpu"), ("eval",)]


def test_inference_keeps_device_transfer_inside_guard(monkeypatch):
    events = []
    guard_labels = []
    guard_active = False

    class FakeTensor:
        def unsqueeze(self, dim):
            events.append(("unsqueeze", guard_active))
            return self

        def to(self, device):
            events.append(("to", device, guard_active))
            return self

        def sigmoid(self):
            events.append(("sigmoid", guard_active))
            return self

        def __getitem__(self, key):
            events.append(("getitem", key, guard_active))
            return self

        def detach(self):
            events.append(("detach", guard_active))
            return self

        def cpu(self):
            events.append(("cpu", guard_active))
            return self

        def float(self):
            events.append(("float", guard_active))
            return self

        def numpy(self):
            events.append(("numpy", guard_active))
            return np.asarray([[0.5]], dtype=np.float32)

    class FakeModel:
        def __call__(self, tensor):
            events.append(("model", guard_active))
            return [tensor]

    class FakeTransforms:
        def Compose(self, transforms):
            return lambda image: FakeTensor()

        def Resize(self, size):
            return object()

        def ToTensor(self):
            return object()

        def Normalize(self, *, mean, std):
            return object()

    @contextmanager
    def guard(label, **_kwargs):
        nonlocal guard_active
        guard_labels.append(label)
        previous = guard_active
        guard_active = True
        try:
            yield
        finally:
            guard_active = previous

    @contextmanager
    def inference_mode():
        yield

    monkeypatch.setattr(lucida_matting, "exclusive_ai_runtime", guard)
    monkeypatch.setattr(lucida_matting, "_load_model", lambda device: FakeModel())

    mask = lucida_matting._generate_on_device(
        Image.new("RGB", (3, 2)),
        "cuda",
        SimpleNamespace(inference_mode=inference_mode),
        FakeTransforms(),
    )

    assert mask.size == (3, 2)
    assert guard_labels == ["lucida-inference"]
    assert ("to", "cuda", True) in events
    assert ("model", True) in events
    assert ("sigmoid", True) in events
    assert ("detach", True) in events
    assert ("cpu", True) in events
    assert ("float", True) in events
    assert ("numpy", True) in events


def test_cuda_failure_retries_generation_on_cpu(monkeypatch):
    attempts = []
    guard_labels = []
    cleanup_guard_active = False
    cache_events = []

    def empty_cache():
        cache_events.append(("empty_cache", cleanup_guard_active))

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=empty_cache)
    )
    fake_torchvision = SimpleNamespace(transforms=SimpleNamespace())

    def generate_on_device(source, device, torch_module, transforms_module):
        attempts.append(device)
        if device == "cuda":
            raise lucida_matting.LucidaCudaError("CUDA out of memory")
        return Image.new("L", source.size, color=127)

    @contextmanager
    def guard(label, **_kwargs):
        nonlocal cleanup_guard_active
        guard_labels.append(label)
        previous = cleanup_guard_active
        cleanup_guard_active = True
        try:
            yield
        finally:
            cleanup_guard_active = previous

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchvision", fake_torchvision)
    monkeypatch.setattr(lucida_matting, "_generate_on_device", generate_on_device, raising=False)
    monkeypatch.setattr(lucida_matting, "exclusive_ai_runtime", guard)
    lucida_matting._model_by_device["cuda"] = SimpleNamespace()

    mask = lucida_matting.generate_subject_mask(Image.new("RGB", (7, 5)), use_gpu=True)

    assert attempts == ["cuda", "cpu"]
    assert guard_labels == ["lucida-cleanup"]
    assert cache_events == [("empty_cache", True)]
    assert "cuda" not in lucida_matting._model_by_device
    assert mask.mode == "L"
    assert mask.size == (7, 5)
