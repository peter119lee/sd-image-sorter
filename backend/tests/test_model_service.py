from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import optional_dependencies

from services import model_service


class FakeResponse:
    def __init__(self, payload: bytes, *, content_type: str = "application/octet-stream") -> None:
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._payload = self._payload, b""
            return data
        data, self._payload = self._payload[:size], self._payload[size:]
        return data


def _zip_payload(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _fake_health(default_model_path: str = "") -> dict:
    return {
        "wd14": {"installed_models": [], "model_path": None},
        "toriigate": {"available": False, "model_name": "toriigate-0.5", "model_dir": "/models/toriigate/toriigate-0.5", "message": "missing"},
        "clip": {"available": False, "runtime_loaded": False, "model_path": None, "message": "missing"},
        "artist": {"available": False, "checkpoint_path": None, "runtime_path": None, "message": "missing"},
        "censor": {
            "legacy": {"available": False, "default_model_path": default_model_path, "message": "missing"},
            "nudenet": {"available": False, "model_downloaded": False, "model_path": None, "message": "missing"},
            "sam3": {"available": False, "checkpoint_path": None, "message": "missing"},
        },
    }


def test_model_inventory_is_built_without_router_imports(monkeypatch):
    monkeypatch.setattr(model_service, "get_model_health", lambda: _fake_health())
    monkeypatch.setitem(sys.modules, "aesthetic", SimpleNamespace(is_available=lambda: False))

    inventory = model_service.ModelService().build_model_inventory()

    model_ids = {item["id"] for item in inventory}
    assert {"wd14", "toriigate", "clip", "aesthetic", "artist", "censor-legacy", "censor-nudenet", "sam3"}.issubset(model_ids)
    assert all("status" in item and "download_supported" in item for item in inventory)


def test_model_inventory_flags_recommended_essentials(monkeypatch):
    # MODELS-07: every inventory entry carries a `recommended` flag so the
    # Model Manager can render essentials first; it must match the curated set.
    monkeypatch.setattr(model_service, "get_model_health", lambda: _fake_health())
    monkeypatch.setitem(sys.modules, "aesthetic", SimpleNamespace(is_available=lambda: False))

    inventory = model_service.ModelService().build_model_inventory()

    assert all("recommended" in item for item in inventory)
    for item in inventory:
        assert item["recommended"] == (item["id"] in model_service.RECOMMENDED_MODEL_IDS)
    recommended_ids = {item["id"] for item in inventory if item["recommended"]}
    assert {
        "wd14",
        "censor-nudenet",
        "clip",
        "aesthetic",
        "artist",
        "florence2",
        "lucida",
    } == recommended_ids
    # Optional/advanced models must NOT be flagged as essentials.
    assert not any(item["recommended"] for item in inventory if item["id"] in {"toriigate", "oppai-oracle", "censor-legacy", "sam3"})


def test_recommended_ids_match_bulk_bundle():
    # MODELS-07 sync guard: the "essentials" set surfaced in the Model Manager
    # must stay identical to the "Download all recommended models" bundle so the
    # two cannot silently drift.
    from routers.models import BULK_MODEL_BUNDLE

    bundle_ids = {
        item["id"] for item in BULK_MODEL_BUNDLE if item.get("recommended")
    }
    assert bundle_ids == set(model_service.RECOMMENDED_MODEL_IDS)
    assert any(item["id"] == "cl-tagger-v2" and not item["recommended"] for item in BULK_MODEL_BUNDLE)


def test_prepare_wd14_repairs_windows_onnx_runtime(monkeypatch):
    repair_calls = []

    class FakeWD14Tagger:
        def __init__(self, model_name, use_gpu=False):
            self.model_name = model_name
            self.use_gpu = use_gpu

        def _get_model_paths(self):
            return ("C:/models/wd14/model.onnx", "C:/models/wd14/selected_tags.csv")

    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "tagger", SimpleNamespace(DEFAULT_MODEL="wd-swinv2-tagger-v3", WD14Tagger=FakeWD14Tagger))
    monkeypatch.setitem(
        sys.modules,
        "repair_onnxruntime",
        SimpleNamespace(
            repair_platform_onnxruntime=lambda stream_pip=False: repair_calls.append(stream_pip) or {
                "repaired": True,
                "actions": ["Installed onnxruntime-gpu CUDA runtime"],
                "providers_after_repair": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                "gpu_vendor_primary": "nvidia",
                "target_runtime": "onnxruntime-gpu",
            }
        ),
    )

    result = model_service.ModelService().prepare_model("wd14", variant="wd-swinv2-tagger-v3")

    assert repair_calls == [True]
    assert result["status"] == "ok"
    assert result["restart_recommended"] is True
    assert result["runtime_repair"]["ok"] is True
    assert result["runtime_repair"]["providers_after_repair"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_prepare_wd14_repairs_linux_onnx_runtime(monkeypatch):
    """Linux Prepare must run the same ONNX GPU repair path as Windows — the
    Linux requirements pin CPU-only onnxruntime, so this Prepare-time swap is
    the lightweight/portable user's only route to GPU WD14 tagging."""
    repair_calls = []

    class FakeWD14Tagger:
        def __init__(self, model_name, use_gpu=False):
            self.model_name = model_name
            self.use_gpu = use_gpu

        def _get_model_paths(self):
            return ("/models/wd14/model.onnx", "/models/wd14/selected_tags.csv")

    monkeypatch.setattr(model_service.platform, "system", lambda: "Linux")
    monkeypatch.setitem(sys.modules, "tagger", SimpleNamespace(DEFAULT_MODEL="wd-swinv2-tagger-v3", WD14Tagger=FakeWD14Tagger))
    monkeypatch.setitem(
        sys.modules,
        "repair_onnxruntime",
        SimpleNamespace(
            repair_platform_onnxruntime=lambda stream_pip=False: repair_calls.append(stream_pip) or {
                "repaired": True,
                "actions": ["Installing onnxruntime-gpu[cuda,cudnn]==1.21.0"],
                "providers_after_repair": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                "gpu_vendor_primary": "nvidia",
                "target_runtime": "onnxruntime-gpu",
            }
        ),
    )

    result = model_service.ModelService().prepare_model("wd14", variant="wd-swinv2-tagger-v3")

    assert repair_calls == [True]
    assert result["status"] == "ok"
    assert result["runtime_repair"]["ok"] is True
    assert result["runtime_repair"]["providers_after_repair"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_prepare_wd14_warns_when_windows_onnx_repair_fails(monkeypatch):
    class FakeWD14Tagger:
        def __init__(self, model_name, use_gpu=False):
            self.model_name = model_name
            self.use_gpu = use_gpu

        def _get_model_paths(self):
            return ("C:/models/wd14/model.onnx", "C:/models/wd14/selected_tags.csv")

    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "tagger", SimpleNamespace(DEFAULT_MODEL="wd-swinv2-tagger-v3", WD14Tagger=FakeWD14Tagger))
    monkeypatch.setitem(
        sys.modules,
        "repair_onnxruntime",
        SimpleNamespace(
            repair_platform_onnxruntime=lambda stream_pip=False: {
                "repaired": False,
                "actions": ["CPU-only runtime remained installed"],
                "providers_after_repair": ["CPUExecutionProvider"],
                "gpu_vendor_primary": "nvidia",
                "target_runtime": "onnxruntime-gpu",
            }
        ),
    )

    result = model_service.ModelService().prepare_model("wd14")

    assert result["status"] == "warning"
    assert result["runtime_repair"]["ok"] is False
    assert "may stay on CPU" in result["message"]


def test_download_privacy_yolo_bundle_extracts_safe_zip(monkeypatch, tmp_path):
    target_dir = tmp_path / "models" / "yolo"
    zip_payload = _zip_payload({"nested/model.onnx": b"onnx"})
    responses = [
        FakeResponse(json.dumps({"modelVersions": [{"downloadUrl": "https://example.test/model.zip"}]}).encode("utf-8"), content_type="application/json"),
        FakeResponse(zip_payload, content_type="application/zip"),
    ]

    monkeypatch.setattr(model_service, "get_yolo_model_dir", lambda: str(target_dir))
    monkeypatch.setattr(model_service, "urlopen_with_ua", lambda _url, timeout=30: responses.pop(0))
    monkeypatch.setattr(model_service, "get_model_health", lambda: _fake_health(str(target_dir / "nested" / "model.onnx")))

    result = model_service.ModelService().download_privacy_yolo_bundle()

    assert (target_dir / "nested" / "model.onnx").read_bytes() == b"onnx"
    assert result["model_dir"] == str(target_dir.resolve())
    assert result["default_model_path"].endswith("model.onnx")


@pytest.mark.parametrize("member_name", ["../escape.onnx", "..\\escape.onnx", "/tmp/escape.onnx", "C:/escape.onnx"])
def test_download_privacy_yolo_bundle_rejects_zip_path_traversal(monkeypatch, tmp_path, member_name):
    target_dir = tmp_path / "models" / "yolo"
    zip_payload = _zip_payload({member_name: b"bad"})
    responses = [
        FakeResponse(b"{}", content_type="application/json"),
        FakeResponse(zip_payload, content_type="application/zip"),
    ]

    monkeypatch.setattr(model_service, "get_yolo_model_dir", lambda: str(target_dir))
    monkeypatch.setattr(model_service, "urlopen_with_ua", lambda _url, timeout=30: responses.pop(0))

    with pytest.raises(model_service.ModelPreparationFailedError) as exc_info:
        model_service.ModelService().download_privacy_yolo_bundle()

    assert exc_info.value.payload["type"] == "ModelPreparationFailed"
    assert "unsafe path" in exc_info.value.payload["reason"]
    assert not (tmp_path / "models" / "escape.onnx").exists()


def test_download_privacy_yolo_bundle_rejects_oversized_zip(monkeypatch, tmp_path):
    target_dir = tmp_path / "models" / "yolo"
    zip_payload = _zip_payload({"nested/model.onnx": b"12345"})
    responses = [
        FakeResponse(b"{}", content_type="application/json"),
        FakeResponse(zip_payload, content_type="application/zip"),
    ]

    monkeypatch.setattr(model_service, "_MAX_PRIVACY_YOLO_UNCOMPRESSED_BYTES", 4)
    monkeypatch.setattr(model_service, "get_yolo_model_dir", lambda: str(target_dir))
    monkeypatch.setattr(model_service, "urlopen_with_ua", lambda _url, timeout=30: responses.pop(0))

    with pytest.raises(model_service.ModelPreparationFailedError) as exc_info:
        model_service.ModelService().download_privacy_yolo_bundle()

    assert "uncompressed size exceeded" in exc_info.value.payload["reason"]
    assert not (target_dir / "nested" / "model.onnx").exists()


def test_download_privacy_yolo_bundle_returns_auth_payload_for_html_login(monkeypatch, tmp_path):
    target_dir = tmp_path / "models" / "yolo"
    responses = [
        FakeResponse(b"{}", content_type="application/json"),
        FakeResponse(b"<html>login</html>", content_type="text/html"),
    ]

    monkeypatch.setattr(model_service, "get_yolo_model_dir", lambda: str(target_dir))
    monkeypatch.setattr(model_service, "urlopen_with_ua", lambda _url, timeout=30: responses.pop(0))

    with pytest.raises(model_service.ExternalAuthRequiredError) as exc_info:
        model_service.ModelService().download_privacy_yolo_bundle()

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload["type"] == "CivitaiLoginRequired"
    assert exc_info.value.payload["external_url"] == model_service.PRIVACY_YOLO_PAGE_URL




def test_prepare_model_returns_restart_hint_when_optional_dependencies_installed(monkeypatch):
    installed_groups = []

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: installed_groups.append(group) or model_service.DependencyInstallResult(("fastembed>=0.4.0",), True),
    )
    monkeypatch.setitem(
        sys.modules,
        "similarity",
        SimpleNamespace(ensure_clip_model_ready=lambda: "/models/clip/model.onnx"),
    )

    result = model_service.ModelService().prepare_model("clip")

    assert installed_groups == ["clip"]
    assert result["restart_recommended"] is True
    assert result["installed_packages"] == ["fastembed>=0.4.0"]

def test_prepare_model_unknown_id_is_domain_error():
    with pytest.raises(ValueError, match="cannot be prepared"):
        model_service.ModelService().prepare_model("not-a-model")


def test_prepare_sam3_rejects_macos_before_checkpoint_or_download(monkeypatch):
    ensure_calls = []

    monkeypatch.setattr(model_service.platform, "system", lambda: "Darwin")

    def reject_sam3(group):
        ensure_calls.append(group)
        raise optional_dependencies.UnsupportedOptionalDependencyError(
            "SAM3 is CUDA-only and unavailable on macOS."
        )

    monkeypatch.setattr(model_service, "ensure_group", reject_sam3)
    monkeypatch.setattr(
        model_service,
        "get_sam3_checkpoint_path",
        lambda: pytest.fail("SAM3 platform validation must run before checkpoint access."),
    )

    with pytest.raises(
        optional_dependencies.UnsupportedOptionalDependencyError,
        match="CUDA-only",
    ):
        model_service.ModelService().prepare_model("sam3")

    assert ensure_calls == ["sam3"]


def test_prepare_sam3_existing_checkpoint_reports_runtime_gap(monkeypatch):
    checkpoint = "/models/sam3/facebook-sam3-modelscope/model.safetensors"
    repair_calls: list[bool] = []
    health = _fake_health()
    health["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": checkpoint,
        "missing_dependencies": ["transformers", "safetensors"],
        "missing_dependency_packages": ["transformers", "safetensors"],
        "cuda_available": False,
        "torch_cuda_build": None,
        "message": "SAM3 checkpoint is installed, but SAM3 is not ready: missing Python packages: transformers, safetensors; this app's Python has CPU-only PyTorch; SAM3 needs a CUDA-enabled Torch build.",
    }
    monkeypatch.setattr(model_service, "get_sam3_checkpoint_path", lambda: checkpoint)
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)
    monkeypatch.setattr(model_service, "ensure_group", lambda group: model_service.DependencyInstallResult((), False))
    monkeypatch.setattr(
        model_service,
        "_repair_sam3_runtime_if_possible",
        lambda: repair_calls.append(True) or {"attempted": True, "ok": False},
    )

    result = model_service.ModelService().prepare_model("sam3")

    assert result["status"] == "needs_runtime"
    assert result["ready"] is False
    assert result["paths"]["checkpoint_path"] == checkpoint
    assert result["missing_dependency_packages"] == ["transformers", "safetensors"]
    assert "checkpoint is installed" in result["message"]
    assert "CPU-only PyTorch" in result["message"]
    assert result["runtime_repair"] == {"attempted": True, "ok": False}
    assert repair_calls == [True]


def test_sam3_default_download_urls_do_not_fallback_to_sam2_checkpoint(monkeypatch):
    """SAM3 download URLs must (a) cover the full transformers checkpoint
    (weights + config + tokenizer files) and (b) never silently fall back
    to a SAM2 mirror — pulling SAM2 .pt and saving it as SAM3 safetensors
    has historically corrupted user installs."""
    monkeypatch.delenv("SD_IMAGE_SORTER_SAM3_BASE_URL", raising=False)
    monkeypatch.delenv("SD_IMAGE_SORTER_SAM3_URLS", raising=False)

    pairs = model_service._sam3_download_urls()

    assert pairs
    assert all(isinstance(p, tuple) and len(p) == 2 for p in pairs)
    filenames = {name for name, _ in pairs}
    urls = [url for _, url in pairs]
    assert all("sam2" not in url.lower() for url in urls)
    assert "model.safetensors" in filenames
    assert "config.json" in filenames
    assert "tokenizer.json" in filenames


def test_prepare_sam3_existing_checkpoint_requires_restart_when_runtime_changed(monkeypatch):
    checkpoint = "/models/sam3/facebook-sam3-modelscope/model.safetensors"
    before = _fake_health()
    before["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": checkpoint,
        "missing_dependencies": ["sam3"],
        "missing_dependency_packages": ["sam3"],
        "cuda_available": False,
        "torch_cuda_build": None,
        "message": "SAM3 checkpoint is installed, but runtime is incomplete.",
    }
    health_calls = []
    repair_calls = []

    monkeypatch.setattr(model_service, "get_sam3_checkpoint_path", lambda: checkpoint)
    monkeypatch.setattr(
        model_service,
        "get_model_health",
        lambda: health_calls.append(True) or before,
    )
    monkeypatch.setattr(model_service, "ensure_group", lambda group: model_service.DependencyInstallResult((), False))
    monkeypatch.setattr(
        model_service,
        "_repair_sam3_runtime_if_possible",
        lambda: repair_calls.append(True)
        or {
            "attempted": True,
            "ok": True,
            "repaired": True,
            "restart_required": True,
        },
    )

    result = model_service.ModelService().prepare_model("sam3")

    assert health_calls == [True]
    assert repair_calls == [True]
    assert result["status"] == "needs_restart"
    assert result["ready"] is False
    assert result["restart_recommended"] is True
    assert result["runtime_repair"] == {
        "attempted": True,
        "ok": True,
        "repaired": True,
        "restart_required": True,
    }


def test_torch_runtime_repair_parses_json_and_requires_restart(monkeypatch):
    calls = []
    payload = {
        "platform": "Windows",
        "torch_version": "2.13.0+cu126",
        "torch_cuda_build": "12.6",
        "torch_cuda_available": True,
        "repaired": True,
        "actions": ["Installed cu126"],
    }

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(model_service.subprocess, "run", fake_run)

    result = model_service._repair_torch_runtime_if_possible()

    assert result["ok"] is True
    assert result["repaired"] is True
    assert result["restart_required"] is True
    command, kwargs = calls[0]
    assert command[-2:] == ["--auto", "--json"]
    assert kwargs["capture_output"] is True


def test_torch_runtime_repair_rejects_exit_zero_incompatible_state(monkeypatch):
    payload = {
        "platform": "Windows",
        "torch_version": "2.13.0+cu130",
        "torch_cuda_build": "13.0",
        "torch_cuda_available": True,
        "repaired": False,
        "actions": ["No repair needed"],
    }
    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        model_service.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = model_service._repair_torch_runtime_if_possible()

    assert result["ok"] is False
    assert result["returncode"] == 0
    assert "CUDA 13.0" in result["error"]
    assert "cu126" in result["error"]


def test_prepare_sam3_runtime_change_stops_for_restart(monkeypatch):
    checkpoint = "/models/sam3/facebook-sam3-modelscope/model.safetensors"
    health = _fake_health()
    health["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": checkpoint,
        "missing_dependencies": [],
        "missing_dependency_packages": [],
        "cuda_available": True,
        "torch_cuda_build": "13.0",
        "runtime_compatible": False,
        "message": "PyTorch CUDA 13.0 is incompatible. Run Prepare, then restart.",
    }
    monkeypatch.setattr(model_service, "get_sam3_checkpoint_path", lambda: checkpoint)
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "_repair_sam3_runtime_if_possible",
        lambda: {
            "attempted": True,
            "ok": True,
            "repaired": True,
            "restart_required": True,
        },
    )

    result = model_service.ModelService().prepare_model("sam3")

    assert result["status"] == "needs_restart"
    assert result["ready"] is False
    assert result["restart_recommended"] is True


def test_prepare_sam3_refreshes_health_when_repair_changed_nothing(monkeypatch):
    checkpoint = "/models/sam3/facebook-sam3-modelscope/model.safetensors"
    before = _fake_health()
    before["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": checkpoint,
        "missing_dependencies": [],
        "missing_dependency_packages": [],
        "cuda_available": False,
        "torch_cuda_build": "12.6",
        "runtime_compatible": True,
        "message": "CUDA probe was not ready.",
    }
    after = _fake_health()
    after["censor"]["sam3"] = {
        **before["censor"]["sam3"],
        "available": True,
        "cuda_available": True,
        "message": "SAM3 checkpoint and runtime dependencies are ready.",
    }
    health_results = iter((before, after))

    monkeypatch.setattr(model_service, "get_sam3_checkpoint_path", lambda: checkpoint)
    monkeypatch.setattr(model_service, "get_model_health", lambda: next(health_results))
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "_repair_sam3_runtime_if_possible",
        lambda: {
            "attempted": True,
            "ok": True,
            "repaired": False,
            "restart_required": False,
        },
    )

    result = model_service.ModelService().prepare_model("sam3")

    assert result["status"] == "ok"
    assert result["ready"] is True
    assert "restart_recommended" not in result
    assert result["runtime_repair"]["repaired"] is False


def test_model_inventory_explains_sam3_checkpoint_with_missing_runtime(monkeypatch):
    checkpoint = "/models/sam3/facebook-sam3-modelscope/model.safetensors"
    health = _fake_health()
    health["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": checkpoint,
        "missing_dependencies": ["transformers", "safetensors"],
        "missing_dependency_packages": ["transformers", "safetensors"],
        "cuda_available": False,
        "torch_version": "2.11.0+cpu",
        "torch_cuda_build": None,
        "message": "SAM3 checkpoint is installed, but runtime is incomplete.",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)
    monkeypatch.setitem(sys.modules, "aesthetic", SimpleNamespace(is_available=lambda: False))

    inventory = model_service.ModelService().build_model_inventory()
    sam3 = next(model for model in inventory if model["id"] == "sam3")

    assert sam3["status"] == "missing"
    assert sam3["message_key"] == "models.sam3.missingDepsCpuTorch"
    assert sam3["message_params"] == {"deps": "transformers, safetensors"}
    assert sam3["path"] == checkpoint


def test_prepare_router_marks_runtime_gap_as_warning(monkeypatch):
    from routers import models as models_router

    class FakeService:
        def prepare_model(self, model_id, source=None, variant=None):
            return {"status": "needs_runtime", "message": "runtime missing"}

    with models_router._prepare_lock:
        models_router._prepare_result.update(active=True, model_id="sam3", status="downloading", message="", error="")

    models_router._run_prepare_blocking(FakeService(), "sam3", None, None)

    with models_router._prepare_lock:
        assert models_router._prepare_result["active"] is False
        assert models_router._prepare_result["status"] == "warning"
        assert models_router._prepare_result["message"] == "runtime missing"


def test_prepare_router_logs_compact_external_auth_guidance(caplog):
    from routers import models as models_router

    payload = {
        "type": "ExternalAuthRequired",
        "provider": "Hugging Face",
        "message": "request-id-heavy gated failure",
        "manual_steps": ["Accept terms", "Configure token"],
        "target_dir": "C:/models/cl-tagger-v2",
        "external_url": "https://huggingface.co/cella110n/cl_tagger_v2",
    }

    class FakeService:
        def prepare_model(self, model_id, source=None, variant=None):
            raise model_service.ExternalAuthRequiredError(payload)

    with caplog.at_level("WARNING", logger=models_router.__name__):
        models_router._run_prepare_blocking(
            FakeService(),
            "cl-tagger-v2",
            None,
            None,
        )

    record = next(
        record
        for record in caplog.records
        if record.message.startswith("[MODEL] prepare_failed")
    )
    assert record.starter_console_message == (
        "[MODEL] prepare_failed model_id=cl-tagger-v2 "
        "error_type=ExternalAuthRequired provider=Hugging Face "
        "action=follow Model Manager recovery steps and retry"
    )


def test_prepare_toriigate_returns_restart_hint_when_runtime_installed(monkeypatch):
    installed_groups = []

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: installed_groups.append(group) or model_service.DependencyInstallResult(("torch>=2.0.0",), True),
    )

    result = model_service.ModelService().prepare_model("toriigate")

    assert installed_groups == ["toriigate"]
    assert result["status"] == "needs_restart"
    assert result["restart_recommended"] is True


def test_prepare_toriigate_repairs_incompatible_runtime_before_model_import(
    monkeypatch,
):
    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": True,
            "runtime_compatible": False,
            "runtime_compatibility_error": "PyTorch CUDA 13.0 is incompatible.",
        },
        raising=False,
    )
    monkeypatch.setattr(
        model_service,
        "_repair_torch_runtime_if_possible",
        lambda: {
            "attempted": True,
            "ok": True,
            "repaired": True,
            "restart_required": True,
        },
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "toriigate_tagger",
        SimpleNamespace(
            ToriiGateTagger=lambda *args, **kwargs: pytest.fail(
                "ToriiGate must not import or initialize before restart."
            )
        ),
    )

    result = model_service.ModelService().prepare_model("toriigate")

    assert result["status"] == "needs_restart"
    assert result["restart_recommended"] is True
    assert result["runtime_repair"]["repaired"] is True


def test_prepare_toriigate_continues_when_repair_changed_nothing(
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "toriigate"
    tagger_calls = []

    class FakeToriiGateTagger:
        def __init__(self, model_name, model_dir, use_gpu):
            tagger_calls.append((model_name, model_dir, use_gpu))
            self.model_dir = model_dir

        def _download_model(self):
            return str(Path(self.model_dir) / "toriigate-0.5")

    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": True,
            "runtime_compatible": False,
            "runtime_compatibility_error": "Stale incompatible probe.",
        },
    )
    monkeypatch.setattr(
        model_service,
        "_repair_torch_runtime_if_possible",
        lambda: {
            "attempted": True,
            "ok": True,
            "repaired": False,
            "restart_required": False,
        },
    )
    monkeypatch.setattr(
        model_service,
        "get_toriigate_model_dir",
        lambda: str(model_dir),
    )
    monkeypatch.setitem(
        sys.modules,
        "toriigate_tagger",
        SimpleNamespace(ToriiGateTagger=FakeToriiGateTagger),
    )

    result = model_service.ModelService().prepare_model("toriigate")

    assert result["status"] == "ok"
    assert "restart_recommended" not in result
    assert tagger_calls == [("toriigate-0.5", str(model_dir), False)]


def test_prepare_toriigate_downloads_after_runtime_exists(monkeypatch, tmp_path):
    installed_groups = []
    fake_tagger_calls = []
    model_dir = tmp_path / "toriigate"

    class FakeToriiGateTagger:
        def __init__(self, model_name="toriigate-0.5", model_dir=None, use_gpu=False):
            fake_tagger_calls.append((model_name, model_dir, use_gpu))

        def _download_model(self):
            target = model_dir / "toriigate-0.5"
            target.mkdir(parents=True)
            (target / "config.json").write_text("{}", encoding="utf-8")
            (target / "model.safetensors").write_bytes(b"model")
            return str(target)

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: installed_groups.append(group) or model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": True,
            "runtime_compatible": True,
            "runtime_compatibility_error": None,
        },
    )
    monkeypatch.setattr(model_service, "get_toriigate_model_dir", lambda: str(model_dir))
    monkeypatch.setitem(sys.modules, "toriigate_tagger", SimpleNamespace(ToriiGateTagger=FakeToriiGateTagger))

    result = model_service.ModelService().prepare_model("toriigate")

    assert installed_groups == ["toriigate"]
    assert fake_tagger_calls == [("toriigate-0.5", str(model_dir), False)]
    assert result["status"] == "ok"
    assert Path(result["paths"]["model_dir"]).name == "toriigate-0.5"


def test_prepare_toriigate_keeps_non_windows_cpu_download_path(
    monkeypatch,
    tmp_path,
):
    model_dir = tmp_path / "toriigate"
    repair_calls = []

    class FakeToriiGateTagger:
        def __init__(self, model_name, model_dir, use_gpu):
            self.model_dir = model_dir

        def _download_model(self):
            return str(Path(self.model_dir) / "toriigate-0.5")

    monkeypatch.setattr(model_service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service,
        "get_torch_onnx_runtime_health",
        lambda: {
            "torch_cuda_available": False,
            "runtime_compatible": True,
            "runtime_compatibility_error": None,
        },
    )
    monkeypatch.setattr(
        model_service,
        "_repair_torch_runtime_if_possible",
        lambda: repair_calls.append(True),
    )
    monkeypatch.setattr(
        model_service,
        "get_toriigate_model_dir",
        lambda: str(model_dir),
    )
    monkeypatch.setitem(
        sys.modules,
        "toriigate_tagger",
        SimpleNamespace(ToriiGateTagger=FakeToriiGateTagger),
    )

    result = model_service.ModelService().prepare_model("toriigate")

    assert result["status"] == "ok"
    assert repair_calls == []
    assert Path(result["paths"]["model_dir"]).name == "toriigate-0.5"


def test_prepare_artist_delegates_to_runtime_artist_asset_preparer(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    checkpoint = tmp_path / "kaloscope2.0" / "448-90.13" / "best_checkpoint.pth"
    mapping = tmp_path / "kaloscope2.0" / "class_mapping.csv"
    runtime.mkdir(parents=True)
    checkpoint.parent.mkdir(parents=True)
    mapping.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"ckpt")
    mapping.write_text("class\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(
        model_service,
        "ensure_group_with_soft_deps",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    import artist_identifier

    monkeypatch.setattr(
        artist_identifier,
        "prepare_artist_assets",
        lambda source="auto": calls.append(source) or {
            "runtime_path": str(runtime),
            "checkpoint_path": str(checkpoint),
            "class_mapping_path": str(mapping),
            "source": source,
        },
    )

    result = model_service.ModelService().prepare_model("artist", source="modelscope")

    assert calls == ["modelscope"]
    assert result["status"] == "ok"
    assert result["paths"]["checkpoint_path"] == str(checkpoint.resolve())


def test_toriigate_download_uses_shared_hf_endpoint_order(monkeypatch, tmp_path):
    import toriigate_tagger

    calls = []

    class FakeHub:
        def snapshot_download(self, **kwargs):
            calls.append(kwargs)
            Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
            for filename in toriigate_tagger.TORIIGATE_REQUIRED_FILES:
                (Path(kwargs["local_dir"]) / filename).write_bytes(b"fixture")
            return kwargs["local_dir"]

    monkeypatch.setattr(toriigate_tagger, "hf_hub", FakeHub())
    monkeypatch.setattr(
        toriigate_tagger,
        "get_hf_endpoint_order",
        lambda model_name="": ["https://hf-mirror.com", "https://huggingface.co"],
    )

    tagger = toriigate_tagger.ToriiGateTagger.__new__(toriigate_tagger.ToriiGateTagger)
    tagger.model_name = "toriigate-0.5"
    tagger.model_dir = str(tmp_path)

    result = tagger._download_model()

    assert Path(result).name == "toriigate-0.5"
    assert calls
    assert calls[0]["endpoint"] == "https://hf-mirror.com"
