"""Characterization pins for ``model_health.py`` (tier-2 step 0).

These pins lock the *current observable behavior* of the unified model
discovery / readiness module ahead of a facade-plus-siblings split. They are a
safety net, not an aspiration: where the code has a quirk or a latent bug it is
pinned AS-IS, never "fixed" here.

Machine-state isolation is the load-bearing constraint. This machine has real
downloaded models under gitignored ``models/`` / ``data/``; these pins must pass
on a CLEAN checkout with zero models present. They therefore NEVER read a real
model file, NEVER import torch in-process, NEVER download, and NEVER touch
``data/images.db`` — every path-root / existence / import / torch-probe seam is
monkeypatched or redirected at ``tmp_path``.

No overlap with the sibling suites is intentional:
  * ``tests/test_model_health.py`` already covers wenaka/generic-yolov8 profile
    inference, the SAM3 no-torch-in-parent probe, the macOS-unsupported branch,
    the GPU-ready startup tagger, the onnxruntime-conflict report line, and the
    CLIP/artist/SAM3 manual-placement detection happy paths.
  * ``tests/test_similarity_pins.py`` Group E pins the None/object() live read of
    ``similarity._embed_model`` by ``_clip_model_loaded`` from similarity's side;
    here we pin only the exception-swallowing + sys.modules-rebinding contract
    from model_health's side (the module that owns the seam).
  * ``tests/test_model_service.py`` / ``test_censor_service_pins.py`` /
    ``test_routers/*`` monkeypatch the from-imported ``get_model_health`` /
    ``get_default_legacy_model_path`` / ``get_sam3_checkpoint_path`` names on the
    downstream module objects; here we pin only the top-level identity those
    patch surfaces depend on.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import model_health  # noqa: E402

# ---------------------------------------------------------------------------
# Hermetic wiring for the full get_model_health() aggregate. Redirects every
# path-root getter (from-imported from config INTO model_health) at empty
# tmp_path dirs, and stubs the torch-probe / module-installed / clip-loaded
# seams so the result is deterministic on ANY machine regardless of which real
# models happen to be on disk.
# ---------------------------------------------------------------------------

_MODEL_DIR_GETTERS = (
    "get_wd14_model_dir",
    "get_clip_model_dir",
    "get_yolo_model_dir",
    "get_nudenet_model_dir",
    "get_toriigate_model_dir",
    "get_florence2_model_dir",
    "get_oppai_oracle_model_dir",
    "get_cl_tagger_v2_model_dir",
    "get_sam3_model_dir",
    "get_artist_model_dir",
    "get_lucida_model_dir",
)


def _empty_torch_probe() -> dict:
    return {
        "torch_version": None,
        "torch_cuda_build": None,
        "torch_cuda_available": False,
        "torch_probe_error": None,
        "torch_probe_source": "subprocess",
    }


def _wire_clean_state(
    monkeypatch,
    tmp_path,
    *,
    torch_probe=None,
    module_installed=None,
    clip_loaded=False,
):
    """Point every model root at an empty tmp dir; stub torch/import/clip seams.

    Returns the tmp root so individual pins can plant fixture files under the
    specific model dir they exercise.
    """
    for name in _MODEL_DIR_GETTERS:
        target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(model_health, name, (lambda p: (lambda: str(p)))(target))

    monkeypatch.setattr(
        model_health, "_probe_torch_runtime", torch_probe or _empty_torch_probe
    )
    monkeypatch.setattr(
        model_health,
        "_module_installed",
        module_installed or (lambda _name: False),
    )
    monkeypatch.setattr(model_health, "_clip_model_loaded", lambda: clip_loaded)
    monkeypatch.setattr(model_health, "ARTIST_LSNET_CODE_PATH", "")
    # Neutralize the __file__-anchored repo legacy-artist probe so a developer's
    # real models/artist/ checkout cannot shadow the empty tmp roots.
    monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY", "1")
    # TIPO resolves its weight home from DATA_DIR, not a model_health getter, so
    # its own documented override is what keeps the zero-model state real once
    # a developer has actually downloaded the GGUF.
    tipo_root = tmp_path / "tipo"
    tipo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SD_IMAGE_SORTER_TIPO_DIR", str(tipo_root))
    return tmp_path


# ===========================================================================
# Section 1 — Module constants, sys.path side effect, statefulness verdict
# ===========================================================================


def test_sam3_required_modules_tuple_is_exact():
    # The (import_name, package_name) census that drives SAM3 missing-dep
    # reporting. Order matters: get_model_health iterates it and special-cases
    # the leading torch entry.
    assert model_health.SAM3_REQUIRED_MODULES == (
        ("torch", "torch"),
        ("transformers", "transformers"),
        ("safetensors", "safetensors"),
        ("cv2", "opencv-python"),
    )


def test_sam3_import_to_package_maps_cv2_to_opencv_python():
    # The only non-identity mapping: import "cv2" installs package "opencv-python".
    assert model_health.SAM3_IMPORT_TO_PACKAGE["cv2"] == "opencv-python"
    assert model_health.SAM3_IMPORT_TO_PACKAGE["torch"] == "torch"


def test_backend_dir_is_on_sys_path_import_side_effect():
    # Import-time side effect: the module prepends its own backend dir to
    # sys.path so bare `from config import ...` resolves for launchers.
    assert model_health._BACKEND_DIR in sys.path
    assert Path(model_health._BACKEND_DIR, "model_health.py").is_file()


def test_get_model_health_returns_a_fresh_dict_each_call(monkeypatch, tmp_path):
    # Statefulness verdict: model_health is STATELESS — no module-level result
    # cache. Two calls return equal-but-distinct dicts (mutating one must not
    # leak into the next). Downstream callers rely on being able to dict()-copy
    # and mutate (routers/artists.py does `dict(get_model_health()["artist"])`).
    _wire_clean_state(monkeypatch, tmp_path)
    first = model_health.get_model_health()
    second = model_health.get_model_health()
    assert first == second
    assert first is not second
    assert first["censor"] is not second["censor"]


# ===========================================================================
# Section 2 — from-import identity seams (the split's load-bearing contract)
#
# Five downstream modules do `from model_health import <name>` at module top,
# freezing the reference at import time. Their test suites monkeypatch those
# names on the DOWNSTREAM module object. Any future split MUST keep these names
# top-level on model_health with the SAME function identity, or both the live
# call graph and every downstream monkeypatch seam silently break.
# ===========================================================================


def test_model_service_binds_get_model_health_and_sam3_checkpoint_identity():
    from services import model_service

    assert model_service.get_model_health is model_health.get_model_health
    assert (
        model_service.get_sam3_checkpoint_path is model_health.get_sam3_checkpoint_path
    )


def test_censor_service_binds_health_and_legacy_path_identity():
    from services import censor_service

    assert censor_service.get_model_health is model_health.get_model_health
    assert (
        censor_service.get_default_legacy_model_path
        is model_health.get_default_legacy_model_path
    )


def test_similarity_binds_get_clip_local_model_path_identity():
    import similarity

    assert (
        similarity.get_clip_local_model_path is model_health.get_clip_local_model_path
    )


def test_similarity_service_binds_get_model_health_identity():
    from services import similarity_service

    assert similarity_service.get_model_health is model_health.get_model_health


def test_artists_router_binds_get_model_health_identity():
    from routers import artists

    assert artists.get_model_health is model_health.get_model_health


def test_public_names_stay_top_level_module_attributes():
    # The exported surface downstream code from-imports or getattr()s. If a
    # split moves any of these into a sibling, model_health must re-export it
    # under the same name (mirrors the modelsvc/censorsvc facade pattern).
    for name in (
        "get_model_health",
        "get_sam3_checkpoint_path",
        "get_default_legacy_model_path",
        "get_clip_local_model_path",
        "get_artist_checkpoint_path",
        "get_artist_class_mapping_path",
        "get_startup_readiness",
        "format_model_health_report",
        "format_startup_readiness_report",
    ):
        assert callable(getattr(model_health, name)), name


# ===========================================================================
# Section 3 — _clip_model_loaded cross-module read of similarity._embed_model
#
# Complements test_similarity_pins.py Group E (None/object live read). Here we
# pin the two behaviors that suite does NOT: (a) the bare-except swallow when
# the similarity import/attr access fails, and (b) that the read binds to the
# CURRENT similarity module object in sys.modules (a from-import inside the
# function body, re-resolved every call).
# ===========================================================================


def test_clip_model_loaded_swallows_import_failure_and_returns_false(monkeypatch):
    # A similarity module object lacking _embed_model makes
    # `from similarity import _embed_model` raise ImportError; the bare except
    # turns that into a quiet False rather than propagating.
    monkeypatch.setitem(sys.modules, "similarity", types.SimpleNamespace())
    assert model_health._clip_model_loaded() is False


def test_clip_model_loaded_reads_current_similarity_module(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "similarity", types.SimpleNamespace(_embed_model=None)
    )
    assert model_health._clip_model_loaded() is False

    monkeypatch.setitem(
        sys.modules, "similarity", types.SimpleNamespace(_embed_model=object())
    )
    assert model_health._clip_model_loaded() is True


# ===========================================================================
# Section 4 — SAM3 pure helpers
# ===========================================================================


def test_sam3_supported_is_false_only_on_darwin(monkeypatch):
    monkeypatch.setattr(model_health.sys, "platform", "darwin")
    assert model_health._sam3_supported_on_platform() is False
    monkeypatch.setattr(model_health.sys, "platform", "win32")
    assert model_health._sam3_supported_on_platform() is True
    monkeypatch.setattr(model_health.sys, "platform", "linux")
    assert model_health._sam3_supported_on_platform() is True


def test_sam3_missing_dependency_packages_maps_and_dedups():
    packages = model_health._sam3_missing_dependency_packages(
        ["cv2", "torch", "cv2", "transformers"]
    )
    # cv2 -> opencv-python, order preserved, duplicates collapsed.
    assert packages == ["opencv-python", "torch", "transformers"]


def test_sam3_message_unsupported_platform_wins_over_everything():
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path="/models/sam3",
        missing_packages=[],
        cuda_available=True,
        uses_cpu_only_torch=False,
        supported_on_platform=False,
    )
    assert "disabled on macOS" in msg


def test_sam3_message_missing_checkpoint_with_missing_packages():
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path=None,
        missing_packages=["torch", "transformers"],
        cuda_available=False,
        uses_cpu_only_torch=False,
    )
    assert msg == (
        "SAM3 checkpoint is missing, and runtime packages are not installed: "
        "torch, transformers."
    )


def test_sam3_message_missing_checkpoint_no_missing_packages_asks_for_download():
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path=None,
        missing_packages=[],
        cuda_available=True,
        uses_cpu_only_torch=False,
    )
    assert msg.startswith("SAM3 checkpoint is missing. Download it via Prepare")
    assert "facebook-sam3-modelscope" in msg


def test_sam3_message_checkpoint_present_but_cpu_only_torch():
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path="/models/sam3",
        missing_packages=[],
        cuda_available=False,
        uses_cpu_only_torch=True,
    )
    assert "CPU-only PyTorch" in msg
    assert msg.startswith("SAM3 checkpoint is installed, but SAM3 is not ready:")


def test_sam3_message_checkpoint_present_cuda_missing_not_cpu_only():
    # uses_cpu_only_torch False + cuda False -> the "CUDA is not available" arm
    # (elif), NOT the CPU-only-Torch arm.
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path="/models/sam3",
        missing_packages=[],
        cuda_available=False,
        uses_cpu_only_torch=False,
    )
    assert "CUDA is not available" in msg
    assert "CPU-only PyTorch" not in msg


def test_sam3_message_all_ready():
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path="/models/sam3",
        missing_packages=[],
        cuda_available=True,
        uses_cpu_only_torch=False,
    )
    assert msg == "SAM3 checkpoint and runtime dependencies are ready."


def test_sam3_message_combines_missing_packages_and_cpu_only_torch():
    # Both problems accumulate, packages first then the torch note, joined "; ".
    msg = model_health._format_sam3_readiness_message(
        checkpoint_path="/models/sam3",
        missing_packages=["safetensors"],
        cuda_available=False,
        uses_cpu_only_torch=True,
    )
    assert "missing Python packages: safetensors" in msg
    assert "CPU-only PyTorch" in msg
    assert msg.index("missing Python packages") < msg.index("CPU-only PyTorch")


# ===========================================================================
# Section 5 — YOLO class-mapping / profile / capability pure helpers
# ===========================================================================


def test_parse_class_mapping_orders_dict_by_int_key():
    assert model_health._parse_class_mapping({"1": "b", "0": "a", "2": "c"}) == [
        "a",
        "b",
        "c",
    ]


def test_parse_class_mapping_accepts_json_string_and_list():
    assert model_health._parse_class_mapping('["x", "y"]') == ["x", "y"]
    assert model_health._parse_class_mapping(["p", "q"]) == ["p", "q"]


def test_parse_class_mapping_invalid_input_returns_empty_list():
    assert model_health._parse_class_mapping("not json") == []
    assert model_health._parse_class_mapping(42) == []


def test_infer_profile_privacy_from_canonical_class_names():
    # A privacy body-part class name (canonical) forces the privacy-censor
    # profile regardless of filename.
    profile = model_health._infer_yolo_model_profile(["breasts"], "random-model.onnx")
    assert profile["id"] == "privacy-censor"
    assert profile["recommended_for_censor"] is True


def test_infer_profile_yolo26_filename_is_general_object():
    profile = model_health._infer_yolo_model_profile([], "yolo26s-seg.onnx")
    assert profile["id"] == "general-object"
    assert profile["recommended_for_censor"] is False


def test_infer_profile_unknown_when_no_signal():
    profile = model_health._infer_yolo_model_profile([], "mystery.onnx")
    assert profile["id"] == "unknown"
    assert profile["recommended_for_censor"] is False


def test_build_capabilities_privacy_seg_vs_box_output_mode():
    seg = model_health._build_yolo_capabilities("privacy-censor", "wenaka-seg.onnx", [])
    box = model_health._build_yolo_capabilities("privacy-censor", "wenaka.onnx", [])
    assert seg["supports_mask_output"] is True
    assert seg["output_mode_label"] == "Privacy-part segmentation masks"
    assert box["supports_mask_output"] is False
    assert box["output_mode_label"] == "Fast box-first censoring"
    # Empty class list falls back to the "5 built-in privacy classes" label.
    assert "5 built-in privacy classes" in seg["class_scope_label"]


def test_build_capabilities_general_object_reports_family_and_pro_level():
    caps26 = model_health._build_yolo_capabilities(
        "general-object", "yolo26s-seg.onnx", []
    )
    caps8 = model_health._build_yolo_capabilities(
        "general-object", "yolov8s-seg.onnx", []
    )
    assert "YOLO26" in caps26["plain_english"]
    assert "YOLOv8" in caps8["plain_english"]
    assert caps26["recommended_user_level"] == "pro"
    assert caps26["supports_text_prompt"] is False


def test_build_capabilities_unknown_profile_shape():
    caps = model_health._build_yolo_capabilities("unknown", "x.onnx", [])
    assert caps["class_scope"] == "unknown"
    assert caps["supports_mask_output"] is False
    assert caps["recommended_user_level"] == "pro"


# ===========================================================================
# Section 6 — Path-resolution functions (hermetic against empty/planted dirs)
# ===========================================================================


def test_clip_local_model_path_returns_none_when_root_empty(monkeypatch, tmp_path):
    clip_root = tmp_path / "clip"
    clip_root.mkdir()
    monkeypatch.setattr(model_health, "get_clip_model_dir", lambda: str(clip_root))
    assert model_health.get_clip_local_model_path() is None


def test_clip_local_model_path_recursive_fallback_skips_dot_and_tmp_dirs(
    monkeypatch, tmp_path
):
    clip_root = tmp_path / "clip"
    # A hidden dir holding model.onnx must be skipped by the recursive fallback.
    hidden = clip_root / ".cache"
    hidden.mkdir(parents=True)
    (hidden / "model.onnx").write_bytes(b"onnx")
    # A real nested dir at depth 2 should be the one returned.
    real = clip_root / "vendor" / "clip-weights"
    real.mkdir(parents=True)
    (real / "model.onnx").write_bytes(b"onnx")
    (real / "config.json").write_text("{}", encoding="utf-8")
    (real / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(model_health, "get_clip_model_dir", lambda: str(clip_root))

    found = model_health.get_clip_local_model_path()
    assert found is not None
    assert not Path(found).name.startswith(".")


def test_default_legacy_model_path_prefers_wenaka_over_generic(monkeypatch, tmp_path):
    yolo_root = tmp_path / "yolo"
    yolo_root.mkdir()
    (yolo_root / "yolov8s-seg.onnx").write_bytes(b"a")
    (yolo_root / "wenaka_yolov8s-seg.onnx").write_bytes(b"b")
    monkeypatch.setattr(model_health, "get_yolo_model_dir", lambda: str(yolo_root))

    resolved = model_health.get_default_legacy_model_path()
    assert Path(resolved).name == "wenaka_yolov8s-seg.onnx"


def test_default_legacy_model_path_globs_any_weight_when_no_preferred(
    monkeypatch, tmp_path
):
    yolo_root = tmp_path / "yolo"
    yolo_root.mkdir()
    (yolo_root / "custom-detector.pt").write_bytes(b"a")
    monkeypatch.setattr(model_health, "get_yolo_model_dir", lambda: str(yolo_root))

    resolved = model_health.get_default_legacy_model_path()
    assert Path(resolved).name == "custom-detector.pt"


def test_default_legacy_model_path_none_when_empty(monkeypatch, tmp_path):
    yolo_root = tmp_path / "yolo"
    yolo_root.mkdir()
    monkeypatch.setattr(model_health, "get_yolo_model_dir", lambda: str(yolo_root))
    assert model_health.get_default_legacy_model_path() is None


def test_sam3_checkpoint_requires_all_transformers_companions(monkeypatch, tmp_path):
    sam3_root = tmp_path / "sam3"
    canonical = sam3_root / "facebook-sam3-modelscope"
    canonical.mkdir(parents=True)
    # Only config.json — incomplete, must NOT resolve.
    (canonical / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(model_health, "get_sam3_model_dir", lambda: str(sam3_root))
    assert model_health.get_sam3_checkpoint_path() is None

    # Add the weights and processor/tokenizer companions — now it resolves.
    for filename in model_health.SAM3_CHECKPOINT_REQUIRED_FILES:
        (canonical / filename).write_bytes(b"w")
    resolved = model_health.get_sam3_checkpoint_path()
    assert resolved is not None
    assert Path(resolved).name == "facebook-sam3-modelscope"


def test_artist_class_mapping_found_one_level_above_checkpoint(monkeypatch, tmp_path):
    # HF ships class_mapping.csv at the kaloscope dir root while the checkpoint
    # lives under 448-90.13/; the resolver must look one level up.
    from config import ARTIST_KALOSCOPE_CHECKPOINT, ARTIST_KALOSCOPE_CLASS_MAPPING

    artist_root = tmp_path / "artist"
    kalo = artist_root / "kaloscope2.0"
    checkpoint = kalo / ARTIST_KALOSCOPE_CHECKPOINT
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"ckpt")
    mapping_basename = Path(ARTIST_KALOSCOPE_CLASS_MAPPING.replace("\\", "/")).name
    (kalo / mapping_basename).write_text("class\n", encoding="utf-8")
    monkeypatch.setattr(model_health, "get_artist_model_dir", lambda: str(artist_root))

    resolved = model_health.get_artist_class_mapping_path()
    assert resolved is not None
    assert Path(resolved).name == mapping_basename


def test_find_kaloscope_dir_prefers_canonical_lowercase(monkeypatch, tmp_path):
    from config import ARTIST_KALOSCOPE_CHECKPOINT

    artist_root = tmp_path / "artist"
    canonical = artist_root / "kaloscope2.0" / ARTIST_KALOSCOPE_CHECKPOINT
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"ckpt")

    resolved = model_health._find_kaloscope_dir(artist_root)
    assert resolved == canonical.parent


def test_find_kaloscope_dir_none_when_root_absent(tmp_path):
    assert model_health._find_kaloscope_dir(tmp_path / "does-not-exist") is None


def test_resolve_artist_runtime_accepts_model_marker(monkeypatch, tmp_path):
    # The resolver accepts EITHER a "lsnet_model" OR a bare "model" subdir as
    # the runtime marker; pin the less-obvious "model" acceptance.
    artist_root = tmp_path / "artist"
    runtime = artist_root / "comfyui-lsnet-runtime"
    (runtime / "model").mkdir(parents=True)
    monkeypatch.setattr(model_health, "get_artist_model_dir", lambda: str(artist_root))
    monkeypatch.setattr(model_health, "ARTIST_LSNET_CODE_PATH", "")
    monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY", "1")

    resolved = model_health._resolve_artist_runtime_path()
    assert resolved is not None
    assert Path(resolved).name == "comfyui-lsnet-runtime"


def test_resolve_artist_runtime_honors_configured_code_path(monkeypatch, tmp_path):
    configured = tmp_path / "custom-lsnet"
    (configured / "lsnet_model").mkdir(parents=True)
    monkeypatch.setattr(model_health, "ARTIST_LSNET_CODE_PATH", str(configured))
    # Point the data-dir root elsewhere/empty so only the configured path matches.
    empty_artist = tmp_path / "empty-artist"
    empty_artist.mkdir()
    monkeypatch.setattr(model_health, "get_artist_model_dir", lambda: str(empty_artist))
    monkeypatch.setenv("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY", "1")

    resolved = model_health._resolve_artist_runtime_path()
    assert resolved == str(configured.resolve())


def test_list_model_files_filters_by_extension_and_rounds_size(monkeypatch, tmp_path):
    directory = tmp_path / "weights"
    directory.mkdir()
    (directory / "keep.onnx").write_bytes(b"x" * (1024 * 1024))  # 1.0 MB
    (directory / "skip.txt").write_text("nope", encoding="utf-8")

    files = model_health._list_model_files(directory, [".onnx"])
    assert [f["name"] for f in files] == ["keep.onnx"]
    assert files[0]["size_mb"] == 1.0
    assert set(files[0]) == {"name", "path", "size_mb"}


def test_list_model_files_missing_directory_returns_empty(tmp_path):
    assert model_health._list_model_files(tmp_path / "absent", [".onnx"]) == []


def test_list_yolo_model_files_describes_planted_file(monkeypatch, tmp_path):
    # _describe_yolo_model needs a real file (stat) but must NOT load ONNX; stub
    # the class-name reader so no runtime is touched.
    yolo_root = tmp_path / "yolo"
    yolo_root.mkdir()
    (yolo_root / "yolo26s-seg.onnx").write_bytes(b"x" * 2048)
    monkeypatch.setattr(model_health, "_load_yolo_class_names", lambda _p: [])

    files = model_health._list_yolo_model_files(yolo_root)
    assert len(files) == 1
    described = files[0]
    assert described["name"] == "yolo26s-seg.onnx"
    assert described["format"] == "onnx"
    assert described["profile"] == "general-object"
    assert described["recommended_for_censor"] is False
    assert "capabilities" in described


# ===========================================================================
# Section 7 — get_model_health() aggregate shape (zero-model clean state)
#
# The aggregate dict is the load-bearing contract: model_service inventory,
# censor_service, similarity_service, artists router, and the launcher reports
# all index specific keys. Pin the exact section key sets so a split cannot
# drop or rename a field downstream consumers read.
# ===========================================================================


def test_health_top_level_and_censor_key_sets(monkeypatch, tmp_path):
    _wire_clean_state(monkeypatch, tmp_path)
    health = model_health.get_model_health()

    assert set(health) == {
        "wd14",
        "toriigate",
        "florence2",
        "tipo",
        "oppai_oracle",
        "cl_tagger_v2",
        "clip",
        "lucida",
        "censor",
        "artist",
    }
    assert set(health["censor"]) == {"legacy", "nudenet", "sam3"}


def test_health_all_subsystems_unavailable_in_zero_model_state(monkeypatch, tmp_path):
    _wire_clean_state(monkeypatch, tmp_path)
    health = model_health.get_model_health()

    assert health["wd14"]["available"] is False
    assert health["toriigate"]["available"] is False
    assert health["florence2"]["available"] is False
    assert health["tipo"]["available"] is False
    assert health["tipo"]["weight_state"] == "missing"
    assert health["oppai_oracle"]["available"] is False
    assert health["cl_tagger_v2"]["available"] is False
    assert health["clip"]["available"] is False
    assert health["lucida"]["available"] is False
    assert health["censor"]["legacy"]["available"] is False
    assert health["censor"]["nudenet"]["available"] is False
    assert health["censor"]["sam3"]["available"] is False
    assert health["artist"]["available"] is False


def test_health_wd14_installed_models_lists_wd14_family_only(
    monkeypatch, tmp_path
):
    from config import TAGGER_MODELS

    _wire_clean_state(monkeypatch, tmp_path)
    wd14 = model_health.get_model_health()["wd14"]
    wd14_names = {
        name
        for name, config in TAGGER_MODELS.items()
        if str(config.get("writer_family") or "").strip().lower() == "wd14"
    }

    assert isinstance(wd14["installed_models"], list)
    assert {entry["name"] for entry in wd14["installed_models"]} == wd14_names
    assert len(wd14["installed_models"]) == len(wd14_names)
    assert len(wd14["installed_models"]) < len(TAGGER_MODELS)
    for entry in wd14["installed_models"]:
        assert set(entry) == {"name", "available"}
        assert entry["available"] is False  # zero-model state


def test_health_sam3_subshape_and_torch_probe_passthrough(monkeypatch, tmp_path):
    probe = {
        "torch_version": "2.11.0+cpu",
        "torch_cuda_build": None,
        "torch_cuda_available": False,
        "torch_probe_error": None,
        "torch_probe_source": "current-process",
    }
    _wire_clean_state(monkeypatch, tmp_path, torch_probe=lambda: probe)
    sam3 = model_health.get_model_health()["censor"]["sam3"]

    assert set(sam3) == {
        "available",
        "checkpoint_path",
        "expected_path",
        "missing_dependencies",
        "missing_dependency_packages",
        "cuda_available",
        "torch_version",
        "torch_cuda_build",
        "torch_probe_error",
        "torch_probe_source",
        "runtime_compatible",
        "runtime_compatibility_error",
        "message",
        "runtime_note",
        "capabilities",
    }
    # Torch-probe fields flow straight through onto the sam3 block.
    assert sam3["torch_version"] == "2.11.0+cpu"
    assert sam3["torch_probe_source"] == "current-process"
    assert sam3["cuda_available"] is False
    assert sam3["runtime_compatible"] is True
    assert sam3["runtime_compatibility_error"] is None


def test_health_sam3_available_requires_all_four_gates(monkeypatch, tmp_path):
    # available = supported AND checkpoint AND no-missing-deps AND cuda. With a
    # checkpoint present + all deps installed but CUDA unavailable, it stays
    # False — the GPU gate is load-bearing (pinned AS-IS).
    sam3_root = tmp_path / "sam3g"
    canonical = sam3_root / "facebook-sam3-modelscope"
    canonical.mkdir(parents=True)
    for filename in model_health.SAM3_CHECKPOINT_REQUIRED_FILES:
        (canonical / filename).write_bytes(b"w")

    _wire_clean_state(
        monkeypatch,
        tmp_path,
        torch_probe=lambda: {
            "torch_version": "2.11.0+cu128",
            "torch_cuda_build": "12.8",
            "torch_cuda_available": False,  # the failing gate
            "torch_probe_error": None,
            "torch_probe_source": "subprocess",
        },
        module_installed=lambda _name: True,  # all deps "installed"
    )
    monkeypatch.setattr(model_health, "get_sam3_model_dir", lambda: str(sam3_root))

    sam3 = model_health.get_model_health()["censor"]["sam3"]
    assert sam3["checkpoint_path"] is not None
    assert sam3["missing_dependencies"] == []
    assert sam3["cuda_available"] is False
    assert sam3["available"] is False


def test_health_clip_message_branches_on_runtime_and_download(monkeypatch, tmp_path):
    # No files + no fastembed -> "missing" branch.
    _wire_clean_state(monkeypatch, tmp_path, module_installed=lambda _n: False)
    clip = model_health.get_model_health()["clip"]
    assert clip["available"] is False
    assert clip["model_downloaded"] is False
    assert "missing" in clip["message"].lower()


def test_health_clip_downloaded_but_runtime_missing_message(monkeypatch, tmp_path):
    _wire_clean_state(monkeypatch, tmp_path, module_installed=lambda _n: False)
    # Model files present, but fastembed absent -> "downloaded, runtime missing".
    monkeypatch.setattr(
        model_health, "get_clip_local_model_path", lambda: "/models/clip/x"
    )
    monkeypatch.setattr(
        model_health,
        "get_clip_text_local_model_path",
        lambda: "/models/clip/text",
    )
    clip = model_health.get_model_health()["clip"]
    assert clip["model_downloaded"] is True
    assert clip["runtime_available"] is False
    assert clip["available"] is False
    assert "FastEmbed runtime is missing" in clip["message"]


def test_health_artist_available_requires_runtime_checkpoint_mapping_and_deps(
    monkeypatch, tmp_path
):
    _wire_clean_state(monkeypatch, tmp_path, module_installed=lambda _n: True)
    # Provide runtime + checkpoint + mapping; with all deps installed, available.
    monkeypatch.setattr(
        model_health, "_resolve_artist_runtime_path", lambda: "/runtime"
    )
    monkeypatch.setattr(model_health, "get_artist_checkpoint_path", lambda: "/ckpt.pth")
    monkeypatch.setattr(
        model_health, "get_artist_class_mapping_path", lambda: "/map.csv"
    )
    artist = model_health.get_model_health()["artist"]
    assert artist["missing_dependencies"] == []
    assert artist["available"] is True

    # Drop the class mapping -> available flips False even with runtime+ckpt.
    monkeypatch.setattr(model_health, "get_artist_class_mapping_path", lambda: None)
    artist2 = model_health.get_model_health()["artist"]
    assert artist2["available"] is False


def test_health_legacy_censor_flags_yolo_family(monkeypatch, tmp_path):
    yolo_root = tmp_path / "yolo"
    yolo_root.mkdir()
    (yolo_root / "yolo26s-seg.onnx").write_bytes(b"x" * 1024)
    _wire_clean_state(monkeypatch, tmp_path)
    monkeypatch.setattr(model_health, "get_yolo_model_dir", lambda: str(yolo_root))
    monkeypatch.setattr(model_health, "_load_yolo_class_names", lambda _p: [])

    legacy = model_health.get_model_health()["censor"]["legacy"]
    assert legacy["has_yolo26"] is True
    assert legacy["has_yolov8s"] is False
    assert legacy["general_model_count"] == 1
    assert legacy["privacy_model_count"] == 0
    # A general-only install is "available" (a model exists) but flagged as not
    # a privacy detector.
    assert legacy["available"] is True
    assert "does not look like a privacy-part detector" in legacy["message"]


# ===========================================================================
# Section 8 — Report / readiness formatters
# ===========================================================================


def test_format_model_health_report_renders_ok_warn_markers():
    health = {
        "wd14": {"available": True, "default_model": "wd-swinv2-tagger-v3"},
        "toriigate": {"available": False, "message": "not downloaded"},
        "clip": {"available": True, "message": "ready"},
        "censor": {
            "legacy": {
                "available": True,
                "default_model_path": "/m/wenaka.onnx",
                "message": "Privacy-part YOLO model ready.",
                "privacy_model_count": 1,
                "general_model_count": 0,
            },
            "nudenet": {"available": False, "message": "not installed"},
            "sam3": {
                "available": False,
                "message": "missing",
                "missing_dependencies": ["torch"],
            },
        },
        "artist": {
            "available": False,
            "message": "missing",
            "missing_dependencies": ["timm"],
            "runtime_path": None,
        },
    }
    report = model_health.format_model_health_report(health)

    assert report.splitlines()[0] == "Model Readiness"
    assert "[OK] WD14 default (wd-swinv2-tagger-v3): ready" in report
    assert "[WARN] ToriiGate: not downloaded" in report
    assert "[OK] CLIP similarity: ready" in report
    assert "Default: /m/wenaka.onnx" in report
    assert "Missing: torch" in report  # sam3 missing line
    assert "Missing: timm" in report  # artist missing line


def test_startup_readiness_cpu_fallback_and_partial_censor(monkeypatch):
    readiness = model_health.get_startup_readiness(
        health={
            "wd14": {"available": True},
            "clip": {"available": False, "message": "clip setup needed"},
            "censor": {
                "legacy": {"available": False},
                "nudenet": {"available": False},
                "sam3": {"available": False, "message": "sam3 missing"},
            },
            "artist": {"available": True, "message": "artist ready"},
        },
        system_info={
            "gpu_name": None,
            "total_ram_gb": 16,
            "gpu_vram_total_mb": None,
            "onnx_providers": ["CPUExecutionProvider"],
            "onnxruntime_conflict": False,
        },
        recommendation={
            "recommended_batch_size": 8,
            "recommended_use_gpu": False,
            "message": "Running on CPU.",
        },
    )
    features = readiness["features"]
    # WD14 present but GPU not recommended -> CPU-fallback warn.
    assert features["tagger"]["level"] == "warn"
    assert "CPU fallback" in features["tagger"]["headline"]
    # Neither legacy nor nudenet -> censor partial.
    assert features["censor"]["level"] == "warn"
    assert "partial" in features["censor"]["headline"]
    # Similarity mirrors the clip message.
    assert features["similarity"]["detail"] == "clip setup needed"
    # Artist ready path.
    assert features["artist"]["level"] == "ready"
    # No GPU name but RAM present -> summary lists just the RAM part (the
    # "No dedicated GPU detected" fallback only fires when NO hardware parts
    # exist at all).
    assert readiness["hardware"]["summary"] == "16GB RAM"
    assert readiness["hardware"]["providers"] == ["CPU"]


def test_startup_readiness_no_hardware_parts_uses_fallback_summary():
    readiness = model_health.get_startup_readiness(
        health={
            "wd14": {"available": False},
            "clip": {"available": False, "message": "m"},
            "censor": {
                "legacy": {"available": False},
                "nudenet": {"available": False},
                "sam3": {"available": False, "message": "m"},
            },
            "artist": {"available": False, "message": "m"},
        },
        system_info={
            "gpu_name": None,
            "total_ram_gb": None,
            "gpu_vram_total_mb": None,
            "onnx_providers": [],
            "onnxruntime_conflict": False,
        },
        recommendation={
            "recommended_batch_size": 8,
            "recommended_use_gpu": False,
            "message": "",
        },
    )
    # With no gpu_name / ram / vram, hardware_parts is empty -> fallback string.
    assert readiness["hardware"]["summary"] == "No dedicated GPU detected"


def test_format_startup_readiness_report_lists_features_and_note():
    report = model_health.format_startup_readiness_report(
        readiness={
            "hardware": {
                "summary": "RTX 4090 · 64GB RAM",
                "providers": ["CUDA", "CPU"],
                "onnxruntime_conflict": False,
                "recommendation_message": "GPU ready.",
            },
            "features": {
                "tagger": {
                    "level": "ready",
                    "headline": "WD14 tagging: GPU ready",
                    "detail": "chunk 32",
                },
                "similarity": {
                    "level": "ready",
                    "headline": "Similar search: ready",
                    "detail": "CLIP ok",
                },
                "censor": {
                    "level": "warn",
                    "headline": "Censor tools: partial",
                    "detail": "none ready",
                },
                "artist": {
                    "level": "warn",
                    "headline": "Artist ID: setup needed",
                    "detail": "missing",
                },
                "sam3": {
                    "level": "warn",
                    "headline": "SAM3 Pro masks: setup needed",
                    "detail": "missing",
                },
            },
        }
    )
    lines = report.splitlines()
    assert lines[0] == "Startup Readiness"
    assert "Hardware: RTX 4090 · 64GB RAM" in report
    assert "Providers: CUDA, CPU" in report
    assert "[OK] WD14 tagging: GPU ready" in report
    assert "[WARN] Censor tools: partial" in report
    assert "Runtime note: GPU ready." in report
