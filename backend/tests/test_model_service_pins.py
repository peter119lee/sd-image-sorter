"""Characterization pins for ``services/model_service.py`` (tier-2 step 0).

These pins lock the *current observable behavior* of the model inventory /
preparation service ahead of a facade-plus-package split. They are the safety
net, not an aspiration: where the code has a quirk or a latent bug it is
pinned AS-IS, never "fixed" here.

Machine-state isolation is the load-bearing constraint. This machine has real
downloaded models under gitignored ``models/`` / ``data/``; these pins must pass
on a CLEAN checkout with zero models present. They therefore NEVER hit the
network, NEVER read a real model file, and NEVER touch ``data/images.db`` — every
path-resolution / existence / download seam is monkeypatched or redirected at
``tmp_path``. The companion suite ``tests/test_model_service.py`` already covers
the WD14/Linux repair paths, the safe-zip download happy/traversal cases, the
SAM3 existing-checkpoint branch, and the toriigate/artist prepare delegations;
this file deliberately pins the surfaces that one does NOT, with no overlap.
"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import model_service


# ---------------------------------------------------------------------------
# Shared fakes / helpers (kept local so these pins do not couple to the sibling
# test module's private fixtures).
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal context-manager response for the ``urlopen_with_ua`` seam."""

    def __init__(self, payload: bytes, *, content_length: int | None = None) -> None:
        self._payload = payload
        length = len(payload) if content_length is None else content_length
        self.headers = {
            "Content-Length": str(length),
            "Content-Type": "application/octet-stream",
        }

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


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _base_health() -> dict:
    """A minimal but structurally-complete ``get_model_health()`` payload.

    ``build_model_inventory`` indexes ``wd14``/``clip``/``artist``/``censor``
    directly (KeyError if absent) and ``.get()``s ``toriigate``/``oppai_oracle``,
    so those two may be omitted. Individual pins mutate the branch they exercise.
    """
    return {
        "wd14": {
            "installed_models": [],
            "model_path": None,
            "default_model": "wd-swinv2-tagger-v3",
        },
        "clip": {
            "available": False,
            "runtime_loaded": False,
            "model_path": None,
            "message": "missing",
        },
        "artist": {
            "available": False,
            "checkpoint_path": None,
            "runtime_path": None,
            "message": "missing",
        },
        "censor": {
            "legacy": {
                "available": False,
                "default_model_path": "",
                "message": "missing",
                "files": [],
            },
            "nudenet": {
                "available": False,
                "model_downloaded": False,
                "model_path": None,
                "message": "missing",
            },
            "sam3": {"available": False, "checkpoint_path": None, "message": "missing"},
        },
    }


@pytest.fixture(autouse=True)
def _restore_download_progress():
    """Snapshot/restore the module-global progress dict.

    ``_download_progress`` is process-global mutable state; pins that drive
    ``_direct_download_file`` mutate it. Restoring keeps this suite from leaking
    state into the sibling suites during the full-backend run.
    """
    snapshot = dict(model_service._download_progress)
    yield
    model_service._download_progress.clear()
    model_service._download_progress.update(snapshot)


# ===========================================================================
# Module constants & path anchors
# ===========================================================================


def test_project_root_anchors_at_repository_root():
    # PROJECT_ROOT is Path(__file__).resolve().parents[2]; it must land on the
    # repo root that actually contains backend/services/model_service.py. This
    # is the split-fragility anchor: moving the module deeper breaks parents[2].
    root = model_service.PROJECT_ROOT
    assert (root / "backend" / "services" / "model_service.py").is_file()


def test_recommended_model_ids_is_frozenset_with_exact_members():
    assert isinstance(model_service.RECOMMENDED_MODEL_IDS, frozenset)
    assert model_service.RECOMMENDED_MODEL_IDS == frozenset(
        {
            "wd14",
            "censor-nudenet",
            "clip",
            "aesthetic",
            "artist",
            "sam3",
            "florence2",
            "lucida",
        }
    )


def test_download_progress_default_shape():
    progress = model_service.get_download_progress()
    assert set(progress) == {"active", "url", "downloaded", "total", "filename"}


# ===========================================================================
# Download-scheme guard: _resolve_allowed_download_schemes / urlopen_with_ua
# ===========================================================================


def test_resolve_allowed_schemes_defaults_to_https_http(monkeypatch):
    monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
    assert model_service._resolve_allowed_download_schemes() == ("https", "http")


def test_resolve_allowed_schemes_adds_file_when_test_flag_set(monkeypatch):
    monkeypatch.setenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "1")
    assert model_service._resolve_allowed_download_schemes() == (
        "https",
        "http",
        "file",
    )


def test_urlopen_with_ua_refuses_file_scheme_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", raising=False)
    local = tmp_path / "payload.bin"
    local.write_bytes(b"data")

    with pytest.raises(ValueError, match="Refusing to download from scheme"):
        model_service.urlopen_with_ua(local.as_uri())


def test_urlopen_with_ua_refuses_ftp_scheme(monkeypatch):
    monkeypatch.setenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "1")
    # Even with the test flag on, only file/https/http are allowed — ftp stays out.
    with pytest.raises(ValueError, match="Refusing to download from scheme 'ftp'"):
        model_service.urlopen_with_ua("ftp://example.test/model.bin")


def test_urlopen_with_ua_allows_file_scheme_with_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "1")
    local = tmp_path / "payload.bin"
    local.write_bytes(b"opened-locally")

    with model_service.urlopen_with_ua(local.as_uri()) as response:
        assert response.read() == b"opened-locally"


# ===========================================================================
# Download-progress state machine: _direct_download_file / _set_download_progress
# ===========================================================================


def test_direct_download_file_writes_content_and_resets_active(monkeypatch, tmp_path):
    dest = tmp_path / "sub" / "weights.bin"
    monkeypatch.setattr(
        model_service,
        "urlopen_with_ua",
        lambda _url, timeout=30: _FakeResponse(b"AB" * 8),
    )

    returned = model_service._direct_download_file("https://cdn.test/weights.bin", dest)

    assert returned == dest
    assert dest.read_bytes() == b"AB" * 8
    # The finally-block resets active/downloaded/total but intentionally leaves
    # url + filename holding the last download's values (pinned AS-IS).
    progress = model_service.get_download_progress()
    assert progress["active"] is False
    assert progress["downloaded"] == 0
    assert progress["total"] == 0
    assert progress["filename"] == "weights.bin"
    assert progress["url"] == "https://cdn.test/weights.bin"


def test_direct_download_file_cleans_tmp_and_resets_on_error(monkeypatch, tmp_path):
    dest = tmp_path / "weights.bin"

    class _Boom(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise OSError("connection reset")

    monkeypatch.setattr(
        model_service, "urlopen_with_ua", lambda _url, timeout=30: _Boom(b"")
    )

    with pytest.raises(OSError, match="connection reset"):
        model_service._direct_download_file("https://cdn.test/weights.bin", dest)

    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".tmp").exists()
    assert model_service.get_download_progress()["active"] is False


# ===========================================================================
# Safe single-root zip extraction: _safe_extract_single_root_zip
# ===========================================================================


def test_safe_extract_single_root_moves_tree_to_target(tmp_path):
    zip_path = tmp_path / "runtime.zip"
    zip_path.write_bytes(_zip_bytes({"lsnet-runtime/lsnet_model/w.bin": b"x"}))
    target = tmp_path / "installed"

    result = model_service._safe_extract_single_root_zip(
        zip_path, target, max_entries=64, max_bytes=1024
    )

    assert result == target
    assert (target / "lsnet_model" / "w.bin").read_bytes() == b"x"


def test_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "evil.zip"
    zip_path.write_bytes(_zip_bytes({"../escape.bin": b"x"}))
    target = tmp_path / "installed"

    with pytest.raises(ValueError, match="unsafe path"):
        model_service._safe_extract_single_root_zip(
            zip_path, target, max_entries=64, max_bytes=1024
        )


def test_safe_extract_requires_exactly_one_root_directory(tmp_path):
    zip_path = tmp_path / "multi.zip"
    zip_path.write_bytes(_zip_bytes({"a/f.bin": b"1", "b/f.bin": b"2"}))
    target = tmp_path / "installed"

    with pytest.raises(ValueError, match="exactly one root directory"):
        model_service._safe_extract_single_root_zip(
            zip_path, target, max_entries=64, max_bytes=1024
        )


# ===========================================================================
# Existing-file materialization: _materialize_existing_file / _copy_existing_tree
# ===========================================================================


def test_materialize_existing_file_links_or_copies_content(tmp_path):
    source = tmp_path / "src.bin"
    source.write_bytes(b"payload")
    dest = tmp_path / "nested" / "dst.bin"

    assert model_service._materialize_existing_file(source, dest) is True
    assert dest.read_bytes() == b"payload"


def test_materialize_existing_file_missing_source_returns_false(tmp_path):
    source = tmp_path / "absent.bin"
    dest = tmp_path / "dst.bin"

    assert model_service._materialize_existing_file(source, dest) is False
    assert not dest.exists()


def test_copy_existing_tree_copies_when_marker_present(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "lsnet_model").mkdir()
    (source / "lsnet_model" / "w.bin").write_bytes(b"x")
    dest = tmp_path / "dst"

    assert model_service._copy_existing_tree(source, dest, "lsnet_model") is True
    assert (dest / "lsnet_model" / "w.bin").read_bytes() == b"x"


def test_copy_existing_tree_returns_false_when_marker_absent(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    dest = tmp_path / "dst"

    assert model_service._copy_existing_tree(source, dest, "lsnet_model") is False
    assert not dest.exists()


# ===========================================================================
# Artist URL resolution + runtime short-circuit
# ===========================================================================


def test_artist_runtime_url_prefers_env_override(monkeypatch):
    monkeypatch.setenv(
        "SD_IMAGE_SORTER_ARTIST_RUNTIME_ZIP_URL", "https://mirror.test/runtime.zip"
    )
    assert model_service._artist_runtime_url() == "https://mirror.test/runtime.zip"


def test_artist_runtime_url_falls_back_to_pinned_revision(monkeypatch):
    monkeypatch.delenv("SD_IMAGE_SORTER_ARTIST_RUNTIME_ZIP_URL", raising=False)
    assert (
        model_service._artist_runtime_url()
        == model_service.ARTIST_LSNET_RUNTIME_ZIP_URL
    )
    assert (
        model_service.ARTIST_LSNET_RUNTIME_REVISION
        in model_service.ARTIST_LSNET_RUNTIME_ZIP_URL
    )


def test_artist_checkpoint_url_routes_class_mapping_to_dedicated_env(monkeypatch):
    monkeypatch.setenv(
        "SD_IMAGE_SORTER_ARTIST_CLASS_MAPPING_URL", "https://mirror.test/map.csv"
    )
    monkeypatch.setenv(
        "SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL", "https://mirror.test/ckpt.pth"
    )

    mapping = model_service._artist_checkpoint_url(
        "repo/id", "class_mapping.csv", hf_base="https://hf.test"
    )
    checkpoint = model_service._artist_checkpoint_url(
        "repo/id", "best_checkpoint.pth", hf_base="https://hf.test"
    )

    assert mapping == "https://mirror.test/map.csv"
    assert checkpoint == "https://mirror.test/ckpt.pth"


def test_artist_checkpoint_url_defaults_to_hf_resolve_path(monkeypatch):
    monkeypatch.delenv("SD_IMAGE_SORTER_ARTIST_CHECKPOINT_URL", raising=False)
    url = model_service._artist_checkpoint_url(
        "owner/model", "best_checkpoint.pth", hf_base="https://hf.test/"
    )
    assert url == "https://hf.test/owner/model/resolve/main/best_checkpoint.pth"


def test_ensure_artist_runtime_direct_shortcircuits_when_already_present(
    monkeypatch, tmp_path
):
    artist_dir = tmp_path / "artist"
    (artist_dir / "comfyui-lsnet-runtime" / "lsnet_model").mkdir(parents=True)
    monkeypatch.setattr(model_service, "get_artist_model_dir", lambda: str(artist_dir))

    def _fail(*_a, **_k):
        raise AssertionError("must not download when runtime already present")

    monkeypatch.setattr(model_service, "_direct_download_file", _fail)

    resolved = model_service._ensure_artist_runtime_direct()
    assert resolved == str((artist_dir / "comfyui-lsnet-runtime").resolve())


# ===========================================================================
# SAM3 download URLs: ModelScope default + env override
# ===========================================================================


def test_sam3_download_urls_default_to_modelscope_master(monkeypatch):
    monkeypatch.delenv("SD_IMAGE_SORTER_SAM3_BASE_URL", raising=False)
    pairs = model_service._sam3_download_urls()
    urls = [url for _, url in pairs]

    assert all(
        url.startswith("https://modelscope.cn/models/facebook/sam3/resolve/master/")
        for url in urls
    )


def test_sam3_download_urls_honour_base_url_override(monkeypatch):
    monkeypatch.setenv("SD_IMAGE_SORTER_SAM3_BASE_URL", "https://mirror.test/sam3/")
    pairs = model_service._sam3_download_urls()

    assert ("config.json", "https://mirror.test/sam3/config.json") in pairs
    assert all(url.startswith("https://mirror.test/sam3/") for _, url in pairs)


# ===========================================================================
# Dependency-result plumbing: _with_dependency_result / _dependency_restart_result
# ===========================================================================


def test_with_dependency_result_returns_input_when_nothing_installed():
    base = {"status": "ok", "model_id": "clip"}
    merged = model_service._with_dependency_result(
        base, model_service.DependencyInstallResult(installed_packages=())
    )
    assert merged is base
    assert "installed_packages" not in merged


def test_with_dependency_result_merges_installed_packages():
    base = {"status": "ok", "model_id": "clip"}
    merged = model_service._with_dependency_result(
        base, model_service.DependencyInstallResult(("fastembed>=0.4.0",), True)
    )
    assert merged is not base
    assert merged["installed_packages"] == ["fastembed>=0.4.0"]
    assert merged["restart_recommended"] is True


def test_dependency_restart_result_is_none_when_nothing_installed():
    assert (
        model_service._dependency_restart_result(
            "clip", model_service.DependencyInstallResult(installed_packages=())
        )
        is None
    )


def test_dependency_restart_result_reports_needs_restart_when_installed():
    result = model_service._dependency_restart_result(
        "toriigate", model_service.DependencyInstallResult(("torch>=2.0.0",), True)
    )
    assert result["status"] == "needs_restart"
    assert result["model_id"] == "toriigate"
    assert result["restart_recommended"] is True
    assert result["installed_packages"] == ["torch>=2.0.0"]
    assert "torch>=2.0.0" in result["message"]


# ===========================================================================
# Error contracts: exceptions + rich-error payload builders
# ===========================================================================


def test_external_auth_required_error_defaults_to_409_and_keeps_payload():
    payload = {"message": "sign in first", "type": "CivitaiLoginRequired"}
    err = model_service.ExternalAuthRequiredError(payload)
    assert err.status_code == 409
    assert err.payload is payload
    assert str(err) == "sign in first"


def test_model_preparation_failed_error_defaults_to_502_and_derives_message():
    # No "message" key → falls back to "error", then to the default string.
    err = model_service.ModelPreparationFailedError({"error": "boom"})
    assert err.status_code == 502
    assert str(err) == "boom"


def test_build_civitai_auth_error_payload_shape(tmp_path):
    payload = model_service.build_civitai_auth_error(tmp_path / "yolo")
    assert payload["type"] == "CivitaiLoginRequired"
    assert payload["model_id"] == "censor-legacy"
    assert payload["provider"] == "Civitai"
    assert payload["external_url"] == model_service.PRIVACY_YOLO_PAGE_URL
    assert payload["target_dir"] == str((tmp_path / "yolo").resolve())
    assert len(payload["manual_steps"]) == 4


def test_build_privacy_yolo_prepare_error_echoes_reason(tmp_path):
    payload = model_service.build_privacy_yolo_prepare_error(
        tmp_path / "yolo", "download timed out"
    )
    assert payload["type"] == "ModelPreparationFailed"
    assert payload["reason"] == "download timed out"
    assert payload["external_url"] == model_service.PRIVACY_YOLO_PAGE_URL


# ===========================================================================
# Runtime-repair branch guards (kept off Windows subprocess / network)
# ===========================================================================


def test_wd14_repair_skips_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(model_service.platform, "system", lambda: "Darwin")
    result = model_service._repair_wd14_onnxruntime_if_possible()
    assert result == {"attempted": False, "ok": True, "reason": "unsupported_platform"}


def test_wd14_repair_reports_ok_when_no_discrete_gpu_expected(monkeypatch):
    # A CPU-only box with no discrete GPU vendor reports ok=True even though the
    # repaired provider list has no CUDA/DML entry — pinned AS-IS, not fixed here.
    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setitem(
        sys.modules,
        "repair_onnxruntime",
        SimpleNamespace(
            repair_platform_onnxruntime=lambda stream_pip=False: {
                "repaired": False,
                "actions": [],
                "providers_after_repair": ["CPUExecutionProvider"],
                "gpu_vendor_primary": None,
                "target_runtime": "onnxruntime",
            }
        ),
    )

    result = model_service._repair_wd14_onnxruntime_if_possible()
    assert result["attempted"] is True
    assert result["ok"] is True
    assert result["providers_after_repair"] == ["CPUExecutionProvider"]


def test_sam3_repair_skips_on_non_windows(monkeypatch):
    monkeypatch.setattr(model_service.platform, "system", lambda: "Linux")
    assert model_service._repair_sam3_runtime_if_possible() == {
        "attempted": False,
        "reason": "non_windows",
    }


def test_sam3_repair_skips_when_disabled_flag_set(monkeypatch):
    monkeypatch.setattr(model_service.platform, "system", lambda: "Windows")
    monkeypatch.setenv("SD_IMAGE_SORTER_SKIP_TORCH_REPAIR", "1")
    assert model_service._repair_sam3_runtime_if_possible() == {
        "attempted": False,
        "reason": "disabled",
    }


# ===========================================================================
# build_model_inventory: message-key branch table
# ===========================================================================


def test_inventory_clip_loaded_branch_uses_loaded_message_key(monkeypatch):
    health = _base_health()
    health["clip"] = {
        "available": False,
        "runtime_loaded": True,
        "text_runtime_loaded": True,
        "model_path": None,
        "message": "missing",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    clip = next(item for item in inventory if item["id"] == "clip")

    assert clip["available"] is True
    assert clip["message_key"] == "models.clip.loaded"


def test_inventory_artist_no_source_branch(monkeypatch):
    health = _base_health()
    health["artist"] = {
        "available": False,
        "checkpoint_path": None,
        "runtime_path": None,
        "has_download_source": False,
        "message": "no source",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    artist = next(item for item in inventory if item["id"] == "artist")

    assert artist["message_key"] == "models.artist.noSource"
    assert artist["download_supported"] is False


def test_inventory_censor_legacy_ready_with_general_files(monkeypatch):
    health = _base_health()
    health["censor"]["legacy"] = {
        "available": True,
        "default_model_path": "/models/yolo/privacy.pt",
        "message": "ready",
        "files": [
            {"recommended_for_censor": True},
            {"recommended_for_censor": False},
        ],
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    legacy = next(item for item in inventory if item["id"] == "censor-legacy")

    assert legacy["message_key"] == "models.censorLegacy.readyPrivacyWithGeneral"


def test_inventory_nudenet_installed_but_not_downloaded_branch(monkeypatch):
    health = _base_health()
    health["censor"]["nudenet"] = {
        "available": True,
        "model_downloaded": False,
        "model_path": "/models/nudenet",
        "message": "installed",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    nudenet = next(item for item in inventory if item["id"] == "censor-nudenet")

    assert nudenet["message_key"] == "models.censorNudenet.installed"
    assert nudenet["available"] is True
    assert nudenet["runtime_available"] is True
    assert nudenet["status"] == "missing"
    assert nudenet["status_label"] == "Missing"
    assert nudenet["path"] == "/models/nudenet"


def test_bulk_bundle_counts_nudenet_weights_when_runtime_only(monkeypatch):
    health = _base_health()
    health["censor"]["nudenet"] = {
        "available": True,
        "model_downloaded": False,
        "model_path": "/models/nudenet",
        "message": "installed",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    from routers.models import BULK_MODEL_BUNDLE, get_bulk_bundle

    payload = asyncio.run(get_bulk_bundle(service=model_service.ModelService()))
    nudenet_spec = next(item for item in BULK_MODEL_BUNDLE if item["id"] == "censor-nudenet")
    nudenet_item = next(item for item in payload["items"] if item["id"] == "censor-nudenet")

    assert nudenet_item["status"] == "missing"
    assert payload["recommended_pending_total_bytes"] >= nudenet_spec["size_bytes"]


def test_inventory_sam3_cpu_torch_branch(monkeypatch):
    health = _base_health()
    health["censor"]["sam3"] = {
        "available": False,
        "checkpoint_path": "/models/sam3/model.safetensors",
        "torch_cuda_build": None,
        "cuda_available": False,
        "message": "cpu torch",
    }
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    sam3 = next(item for item in inventory if item["id"] == "sam3")

    assert sam3["message_key"] == "models.sam3.cpuTorch"


def test_inventory_wd14_surfaces_default_variant_from_health(monkeypatch):
    health = _base_health()
    health["wd14"]["default_model"] = "wd-swinv2-tagger-v3"
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    inventory = model_service.ModelService().build_model_inventory()
    wd14 = next(item for item in inventory if item["id"] == "wd14")

    assert wd14["default_variant"] == "wd-swinv2-tagger-v3"


def test_inventory_includes_oppai_oracle_as_non_recommended(monkeypatch):
    monkeypatch.setattr(model_service, "get_model_health", lambda: _base_health())

    inventory = model_service.ModelService().build_model_inventory()
    oppai = next(item for item in inventory if item["id"] == "oppai-oracle")

    assert oppai["recommended"] is False
    assert oppai["group"] == "Tagging"


def test_get_status_wraps_inventory_and_health(monkeypatch):
    health = _base_health()
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)

    status = model_service.ModelService().get_status()

    assert status["status"] == "ok"
    assert status["health"] is health
    assert [item["id"] for item in status["models"]] == [
        "wd14",
        "toriigate",
        "florence2",
        "oppai-oracle",
        "cl-tagger-v2",
        "tipo",
        "clip",
        "aesthetic",
        "artist",
        "lucida",
        "censor-legacy",
        "censor-nudenet",
        "sam3",
    ]


# ===========================================================================
# prepare_model routing table (network + heavy imports stubbed)
# ===========================================================================


def test_prepare_model_normalizes_id_and_skips_repair_off_windows_linux(monkeypatch):
    # "  WD14 " must normalize to wd14; on macOS the ONNX repair is a no-op, so
    # the happy path returns a plain ok result with no restart hint.
    monkeypatch.setattr(model_service.platform, "system", lambda: "Darwin")

    class _FakeTagger:
        def __init__(self, model_name, use_gpu=False):
            self.model_name = model_name

        def _get_model_paths(self):
            return ("/m/model.onnx", "/m/tags.csv")

    monkeypatch.setitem(
        sys.modules,
        "tagger",
        SimpleNamespace(DEFAULT_MODEL="wd-swinv2-tagger-v3", WD14Tagger=_FakeTagger),
    )

    result = model_service.ModelService().prepare_model("  WD14 ")

    assert result["status"] == "ok"
    assert result["model_id"] == "wd14"
    assert result["runtime_repair"]["attempted"] is False
    assert "restart_recommended" not in result


def test_prepare_oppai_oracle_returns_ready(monkeypatch):
    class _FakeOppai:
        def __init__(self, model_name, model_dir, use_gpu=False):
            self.model_name = model_name

        def _get_model_paths(self):
            return ("/m/oppai.onnx", "/m/oppai_tags.csv")

    monkeypatch.setitem(
        sys.modules,
        "oppai_oracle_tagger",
        SimpleNamespace(
            OppaiOracleTagger=_FakeOppai, DEFAULT_MODEL="oppai-oracle-v1.1"
        ),
    )
    import config

    monkeypatch.setattr(
        config, "get_oppai_oracle_model_dir", lambda: "/m/oppai", raising=False
    )

    result = model_service.ModelService().prepare_model("oppai-oracle")

    assert result["status"] == "ok"
    assert result["model_id"] == "oppai-oracle"
    assert result["paths"] == {
        "model_path": "/m/oppai.onnx",
        "tags_path": "/m/oppai_tags.csv",
    }


def test_prepare_censor_nudenet_loads_detector_and_reports_ready(monkeypatch):
    health = _base_health()
    health["censor"]["nudenet"]["model_path"] = "/models/nudenet/320n.onnx"
    load_calls = []

    class _FakeDetector:
        def load(self):
            load_calls.append(True)

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)
    monkeypatch.setitem(
        sys.modules,
        "nudenet_detector",
        SimpleNamespace(get_nudenet_detector=lambda: _FakeDetector()),
    )

    result = model_service.ModelService().prepare_model("censor-nudenet")

    assert load_calls == [True]
    assert result["status"] == "ok"
    assert result["paths"] == {"model_path": "/models/nudenet/320n.onnx"}


def test_prepare_censor_legacy_wraps_download_bundle(monkeypatch):
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(
        model_service.ModelService,
        "download_privacy_yolo_bundle",
        lambda self: {
            "model_dir": "/models/yolo",
            "default_model_path": "/models/yolo/privacy.pt",
        },
    )

    result = model_service.ModelService().prepare_model("censor-legacy")

    assert result["status"] == "ok"
    assert result["message"] == "Privacy YOLO files were downloaded from Civitai."
    assert result["paths"]["default_model_path"] == "/models/yolo/privacy.pt"


def test_prepare_aesthetic_head_present_but_runtime_unavailable(monkeypatch, tmp_path):
    head_dir = tmp_path / "aesthetic"
    head_dir.mkdir()
    (head_dir / "sa_0_4_vit_l_14_linear.pth").write_bytes(b"head")

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )

    def _fail_download(*_a, **_k):
        raise AssertionError("head already present; must not re-download")

    monkeypatch.setattr(model_service, "_direct_download_file", _fail_download)
    monkeypatch.setitem(
        sys.modules,
        "aesthetic",
        SimpleNamespace(
            reset_availability_cache=lambda: None,
            _ensure_loaded=lambda: None,
            _get_models_dir=lambda: head_dir,
            get_aesthetic_backbone_path=lambda: None,
            is_available=lambda: False,
            is_fully_ready=lambda: False,
        ),
    )

    result = model_service.ModelService().prepare_model("aesthetic")

    assert result["status"] == "needs_runtime"
    assert result["ready"] is False
    assert "could not be loaded" in result["message"]


def test_prepare_clip_initializes_vision_and_text_towers(monkeypatch):
    calls: list[str] = []
    similarity_module = SimpleNamespace(
        ensure_clip_model_ready=lambda: calls.append("prepare") or "/models/vision",
        get_clip_text_local_model_path=lambda: "/models/text",
    )
    monkeypatch.setitem(sys.modules, "similarity", similarity_module)
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult(installed_packages=()),
    )

    result = model_service.ModelService().prepare_model("clip")

    assert calls == ["prepare"]
    assert result["status"] == "ok"
    assert result["paths"] == {
        "vision_model_path": "/models/vision",
        "text_model_path": "/models/text",
    }


def test_prepare_sam3_fresh_download_skips_existing_and_assembles(
    monkeypatch, tmp_path
):
    sam3_root = tmp_path / "sam3"
    checkpoint_dir = sam3_root / "facebook-sam3-modelscope"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "model.safetensors").write_bytes(b"weights")  # already present

    ready = _base_health()
    ready["censor"]["sam3"] = {
        "available": True,
        "checkpoint_path": str(checkpoint_dir),
        "missing_dependencies": [],
        "missing_dependency_packages": [],
        "message": "ready",
    }
    downloaded = []

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(model_service, "get_sam3_model_dir", lambda: str(sam3_root))
    monkeypatch.setattr(model_service, "get_model_health", lambda: ready)
    # checkpoint_before is None (fresh install), refreshed path resolves after downloads.
    checkpoint_states = iter([None, str(checkpoint_dir)])
    monkeypatch.setattr(
        model_service, "get_sam3_checkpoint_path", lambda: next(checkpoint_states)
    )
    monkeypatch.setattr(
        model_service,
        "_sam3_download_urls",
        lambda: [("model.safetensors", "file://skip"), ("config.json", "file://cfg")],
    )

    def _fake_download(url, dest, *, timeout=300):
        downloaded.append(Path(dest).name)
        Path(dest).write_bytes(b"{}")
        return dest

    monkeypatch.setattr(model_service, "_direct_download_file", _fake_download)

    result = model_service.ModelService().prepare_model("sam3")

    # model.safetensors already on disk with size>0 → skipped; only config fetched.
    assert downloaded == ["config.json"]
    assert result["status"] == "ok"
    assert result["ready"] is True


def test_prepare_sam3_fresh_download_raises_when_checkpoint_incomplete(
    monkeypatch, tmp_path
):
    sam3_root = tmp_path / "sam3"

    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setattr(model_service, "get_sam3_model_dir", lambda: str(sam3_root))
    monkeypatch.setattr(model_service, "get_model_health", lambda: _base_health())
    monkeypatch.setattr(
        model_service, "get_sam3_checkpoint_path", lambda: None
    )  # never resolves
    monkeypatch.setattr(
        model_service, "_sam3_download_urls", lambda: [("config.json", "file://cfg")]
    )
    monkeypatch.setattr(
        model_service,
        "_direct_download_file",
        lambda url, dest, *, timeout=300: Path(dest).write_bytes(b"{}") or dest,
    )

    with pytest.raises(RuntimeError, match="Could not assemble SAM3 checkpoint"):
        model_service.ModelService().prepare_model("sam3")


def test_prepare_unknown_model_id_after_normalization_raises_valueerror():
    with pytest.raises(ValueError, match="cannot be prepared"):
        model_service.ModelService().prepare_model("  Nonsense-Model ")


def test_get_model_service_returns_process_singleton():
    first = model_service.get_model_service()
    second = model_service.get_model_service()
    assert first is second
    assert first is model_service._default_model_service
