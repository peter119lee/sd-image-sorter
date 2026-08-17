"""Native Florence-2 Base runtime contracts with fake collaborators only."""
from __future__ import annotations

import sys
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import florence2_captioner
from model_download_sources import hf_error_metadata


def _write_complete_checkpoint(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for filename in florence2_captioner.FLORENCE2_REQUIRED_FILES:
        (path / filename).write_bytes(b"fixture")


def test_huggingface_error_metadata_redacts_bearer_and_token_values():
    metadata = hf_error_metadata(
        RuntimeError("Authorization: Bearer secret-a token=secret-b access_token=secret-c")
    )

    assert "secret-a" not in metadata["error"]
    assert "secret-b" not in metadata["error"]
    assert "secret-c" not in metadata["error"]
    assert metadata["gated"] is True


def test_prepare_checkpoint_uses_only_the_pinned_snapshot(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    calls: list[dict[str, object]] = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        target = Path(str(kwargs["local_dir"]))
        _write_complete_checkpoint(target)
        return str(target)

    model_dir = tmp_path / "florence2"
    monkeypatch.setattr(
        florence2_captioner,
        "get_florence2_model_dir",
        lambda: str(model_dir),
    )
    monkeypatch.setattr(
        florence2_captioner,
        "get_hf_endpoint_order",
        lambda model_name: ["https://huggingface.co"],
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    checkpoint = florence2_captioner.prepare_checkpoint()

    assert checkpoint == str(model_dir.resolve())
    assert calls == [
        {
            "repo_id": "florence-community/Florence-2-base",
            "revision": "00921df66db728a9ceb750f5eca43e5c203a2051",
            "local_dir": str(model_dir),
            "allow_patterns": list(florence2_captioner.FLORENCE2_REQUIRED_FILES),
            "endpoint": "https://huggingface.co",
        }
    ]
    records = [
        record
        for record in caplog.records
        if record.message == "Model artifact validation"
    ]
    assert {record.artifact_file for record in records} == set(
        florence2_captioner.FLORENCE2_REQUIRED_FILES
    )
    assert all(record.status == "file_ready" for record in records)


def test_checkpoint_requires_every_nonempty_runtime_file(monkeypatch, tmp_path):
    model_dir = tmp_path / "florence2"
    model_dir.mkdir()
    monkeypatch.setattr(
        florence2_captioner,
        "get_florence2_model_dir",
        lambda: str(model_dir),
    )

    for filename in florence2_captioner.FLORENCE2_REQUIRED_FILES[:-1]:
        (model_dir / filename).write_bytes(b"fixture")
    assert florence2_captioner.get_checkpoint_path() is None

    last_file = model_dir / florence2_captioner.FLORENCE2_REQUIRED_FILES[-1]
    last_file.write_bytes(b"")
    assert florence2_captioner.get_checkpoint_path() is None

    last_file.write_bytes(b"fixture")
    assert florence2_captioner.get_checkpoint_path() == str(model_dir.resolve())


def test_prepare_checkpoint_reports_gated_huggingface_access(monkeypatch, tmp_path, caplog):
    class _Response:
        status_code = 401

    class _GatedError(RuntimeError):
        response = _Response()

    def snapshot_download(**_kwargs):
        raise _GatedError("401 Client Error: gated repository")

    monkeypatch.setattr(
        florence2_captioner,
        "get_florence2_model_dir",
        lambda: str(tmp_path / "florence2"),
    )
    monkeypatch.setattr(
        florence2_captioner,
        "get_hf_endpoint_order",
        lambda model_name: ["https://huggingface.co"],
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    with pytest.raises(florence2_captioner.Florence2UnavailableError) as error:
        florence2_captioner.prepare_checkpoint()

    message = str(error.value).lower()
    assert "401" in message
    assert "gated" in message
    assert "token" in message or "accept" in message
    assert any(record.__dict__.get("status_code") == 401 for record in caplog.records)


def test_caption_loads_native_runtime_and_generates_inside_runtime_guard(
    monkeypatch,
    tmp_path,
):
    checkpoint = tmp_path / "florence2"
    _write_complete_checkpoint(checkpoint)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (4, 3), color=(20, 40, 60)).save(image_path)
    events: list[tuple[object, ...]] = []
    guard_labels: list[str] = []
    guard_active = False

    class FakeBatch(dict):
        def to(self, device):
            events.append(("inputs.to", device, guard_active))
            return self

    class FakeProcessor:
        def __call__(self, *, text, images, return_tensors):
            events.append(("processor", text, images.size, return_tensors))
            return FakeBatch(input_ids="ids", pixel_values="pixels")

        def batch_decode(self, generated_ids, *, skip_special_tokens):
            events.append(("decode", generated_ids, skip_special_tokens, guard_active))
            return ["decoded Florence output"]

        def post_process_generation(self, text, *, task, image_size):
            events.append(("post_process", text, task, image_size))
            return {task: "A detailed natural-language description."}

    class FakeModel:
        def to(self, device):
            events.append(("model.to", device, guard_active))
            return self

        def eval(self):
            events.append(("model.eval", guard_active))
            return self

        def generate(self, **kwargs):
            events.append(("generate", kwargs, guard_active))
            return ["generated ids"]

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(path, **kwargs):
            events.append(("model.from_pretrained", path, kwargs, guard_active))
            return FakeModel()

    class FakeAutoProcessor:
        @staticmethod
        def from_pretrained(path, **kwargs):
            events.append(("processor.from_pretrained", path, kwargs, guard_active))
            return FakeProcessor()

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
        events.append(("inference.enter", guard_active))
        yield

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        inference_mode=inference_mode,
    )
    fake_transformers = SimpleNamespace(
        AutoModelForImageTextToText=FakeAutoModel,
        AutoProcessor=FakeAutoProcessor,
    )
    monkeypatch.setattr(
        florence2_captioner,
        "get_checkpoint_path",
        lambda: str(checkpoint),
    )
    monkeypatch.setattr(florence2_captioner, "exclusive_ai_runtime", guard)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    florence2_captioner._runtime_by_device.clear()

    caption = florence2_captioner.caption_image(str(image_path), use_gpu=False)

    assert caption == "A detailed natural-language description."
    assert guard_labels == ["florence2-load", "florence2-inference"]
    expected_loader_kwargs = {
        "trust_remote_code": False,
        "local_files_only": True,
        "use_safetensors": True,
    }
    assert (
        "model.from_pretrained",
        str(checkpoint),
        expected_loader_kwargs,
        True,
    ) in events
    assert (
        "processor.from_pretrained",
        str(checkpoint),
        {"trust_remote_code": False, "local_files_only": True},
        True,
    ) in events
    generate_event = next(event for event in events if event[0] == "generate")
    assert generate_event[1] == {
        "input_ids": "ids",
        "pixel_values": "pixels",
        "max_new_tokens": 1024,
        "do_sample": False,
        "num_beams": 3,
    }
    assert generate_event[2] is True
    assert ("inputs.to", "cpu", True) in events


def test_gpu_request_fails_without_silent_cpu_fallback(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with pytest.raises(
        florence2_captioner.Florence2CudaError,
        match="CUDA.*not available.*Model Manager",
    ):
        florence2_captioner._resolve_device(use_gpu=True)


def test_invalid_post_processed_caption_fails_explicitly(monkeypatch, tmp_path):
    image_path = tmp_path / "source.png"
    Image.new("RGB", (2, 2)).save(image_path)

    class FakeBatch(dict):
        def to(self, device):
            return self

    class FakeProcessor:
        def __call__(self, *, text, images, return_tensors):
            return FakeBatch(input_ids="ids", pixel_values="pixels")

        def batch_decode(self, generated_ids, *, skip_special_tokens):
            return ["decoded"]

        def post_process_generation(self, text, *, task, image_size):
            return {task: "   "}

    class FakeModel:
        def generate(self, **kwargs):
            return ["generated ids"]

    @contextmanager
    def inference_mode():
        yield

    @contextmanager
    def guard(label, **_kwargs):
        yield

    monkeypatch.setattr(
        florence2_captioner,
        "_load_runtime",
        lambda device: (FakeModel(), FakeProcessor()),
    )
    monkeypatch.setattr(florence2_captioner, "exclusive_ai_runtime", guard)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            inference_mode=inference_mode,
        ),
    )

    with pytest.raises(
        florence2_captioner.Florence2InferenceError,
        match="non-empty natural-language caption",
    ):
        florence2_captioner.caption_image(str(image_path), use_gpu=False)
