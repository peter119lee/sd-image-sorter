"""Model-path / YOLO / Kaloscope resolution (split from model_health.py, 2026-07).

The YOLO describe chain (_list_model_files, _parse_class_mapping,
_load_yolo_class_names, _infer_yolo_model_profile, _build_yolo_capabilities,
_describe_yolo_model, _list_yolo_model_files), the CLIP / legacy-YOLO / SAM3
checkpoint locators, and the Kaloscope/LSNet artist resolvers moved here
verbatim. Every facade-family read --
the config dir getters and constants (get_clip_model_dir, get_yolo_model_dir,
get_sam3_model_dir, get_artist_model_dir, CLIP_MODEL_NAME,
ARTIST_KALOSCOPE_CHECKPOINT, ARTIST_KALOSCOPE_CLASS_MAPPING,
ARTIST_LSNET_CODE_PATH), exclusive_ai_runtime, _canonicalize_yolo_class_name,
_module_installed, the intra-family calls, and the __file__ repo-legacy
anchor in _resolve_artist_runtime_path -- resolves through _svc() at call
time so monkeypatches on the facade module (including model_health.__file__,
patched by tests/test_artist_diagnostics_legacy_path.py) keep affecting
behavior. _svc().__file__ also keeps the legacy-artist anchor pointing at
backend/model_health.py itself, so Path(...).parent.parent stays the repo
root exactly as before the split. Lazy in-function imports (runtime_env,
onnxruntime, ultralytics) stay in-function; os.environ and pathlib.Path are
process-global singletons, so direct stdlib imports here observe the same
patches (e.g. model_health.Path.resolve).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from model_download_sources import is_nonempty_model_file, missing_model_artifacts


CLIP_VISION_REQUIRED_FILES = (
    "config.json",
    "model.onnx",
    "preprocessor_config.json",
)
CLIP_TEXT_REQUIRED_FILES = (
    "config.json",
    "model.onnx",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _svc():
    """Resolve facade-patched seams through model_health at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy
    import avoids a facade<->sibling load cycle.
    """
    import model_health

    return model_health


def _list_model_files(directory: Path, extensions: Iterable[str]) -> List[Dict[str, Any]]:
    if not directory.exists():
        return []

    allowed = {ext.lower() for ext in extensions}
    files = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed:
            continue
        files.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            }
        )
    return files


def _parse_class_mapping(raw_names: Any) -> List[str]:
    if isinstance(raw_names, str):
        try:
            raw_names = json.loads(raw_names)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw_names, dict):
        ordered = []
        for key in sorted(raw_names.keys(), key=lambda item: int(item) if str(item).isdigit() else str(item)):
            ordered.append(str(raw_names[key]))
        return ordered
    if isinstance(raw_names, list):
        return [str(item) for item in raw_names]
    return []


def _load_yolo_class_names(model_path: Path) -> List[str]:
    candidates = []
    if model_path.suffix.lower() == ".onnx":
        candidates.append(model_path)
    else:
        onnx_candidate = model_path.with_suffix(".onnx")
        if onnx_candidate.exists():
            candidates.append(onnx_candidate)

    for candidate in candidates:
        try:
            from runtime_env import prepare_onnxruntime_environment

            prepare_onnxruntime_environment()
            import onnxruntime as ort

            with _svc().exclusive_ai_runtime("model-health-onnx-metadata"):
                session = ort.InferenceSession(str(candidate), providers=["CPUExecutionProvider"])
                metadata = session.get_modelmeta().custom_metadata_map or {}
            raw_names = metadata.get("names")
            if not raw_names:
                continue
            return _svc()._parse_class_mapping(raw_names)
        except Exception:
            continue

    if model_path.suffix.lower() in {".pt", ".pth"} and _svc()._module_installed("ultralytics"):
        try:
            from ultralytics import YOLO

            with _svc().exclusive_ai_runtime("model-health-ultralytics-metadata"):
                return _svc()._parse_class_mapping(getattr(YOLO(str(model_path)), "names", {}))
        except Exception:
            return []

    return []


def _infer_yolo_model_profile(class_names: List[str], filename: str) -> Dict[str, Any]:
    canonical = [_svc()._canonicalize_yolo_class_name(name) for name in class_names]
    canonical_set = {name.replace(" ", "") for name in canonical}
    privacy_keywords = {"anus", "cum", "dick", "breasts", "pussy"}
    filename_lower = filename.lower()

    if privacy_keywords.intersection(canonical_set):
        return {
            "id": "privacy-censor",
            "label": "Privacy-part detector",
            "recommended_for_censor": True,
            "message": "Specialized for privacy-part detection and censor workflows.",
        }

    if "wenaka" in filename_lower:
        return {
            "id": "privacy-censor",
            "label": "Privacy-part detector",
            "recommended_for_censor": True,
            "message": "Wenaka is treated as a privacy-part detector even when ONNX metadata is incomplete.",
        }

    if "yolo26" in filename_lower or "yolov8" in filename_lower:
        return {
            "id": "general-object",
            "label": "General object segmentation",
            "recommended_for_censor": False,
            "message": "This is a general COCO-style object model. It is useful for compatibility tests, not for privacy-part censoring.",
        }

    return {
        "id": "unknown",
        "label": "Unknown model type",
        "recommended_for_censor": False,
        "message": "Model classes could not be identified automatically.",
    }


def _build_yolo_capabilities(profile_id: str, filename: str, class_names: List[str]) -> Dict[str, Any]:
    filename_lower = filename.lower()
    class_count = len(class_names)
    supports_mask_output = "seg" in filename_lower

    if profile_id == "privacy-censor":
        return {
            "class_scope": "fixed-privacy",
            "class_scope_label": f"{class_count or 5} built-in privacy classes",
            "input_mode_label": "Fixed privacy-part labels",
            "output_mode_label": "Privacy-part segmentation masks" if supports_mask_output else "Fast box-first censoring",
            "supports_text_prompt": False,
            "supports_mask_output": supports_mask_output,
            "recommended_user_level": "normal",
            "best_for": "Quick privacy-part censoring",
            "plain_english": (
                "Best for normal users who want quick privacy-part auto-detection. "
                + (
                    "When the runtime preserves segmentation outputs, auto-censor can use the model masks directly. "
                    if supports_mask_output
                    else ""
                )
                + "This route does not understand arbitrary text prompts."
            ),
        }

    if profile_id == "general-object":
        model_family = "YOLO26" if "yolo26" in filename_lower else "YOLOv8"
        return {
            "class_scope": "fixed-coco",
            "class_scope_label": f"{class_count or 80} built-in object classes",
            "input_mode_label": "Fixed built-in object classes",
            "output_mode_label": "General object segmentation tests",
            "supports_text_prompt": False,
            "supports_mask_output": True,
            "recommended_user_level": "pro",
            "best_for": "Advanced compatibility checks and non-privacy object tests",
            "plain_english": (
                f"The current local {model_family} file is a fixed-class COCO model. "
                "It is useful for general segmentation tests, but it is not an open-text prompt detector."
            ),
        }

    return {
        "class_scope": "unknown",
        "class_scope_label": "Unknown class scope",
        "input_mode_label": "Unknown",
        "output_mode_label": "Unknown",
        "supports_text_prompt": False,
        "supports_mask_output": False,
        "recommended_user_level": "pro",
        "best_for": "Manual inspection required",
        "plain_english": "The app could not determine what this model is good at automatically.",
    }


def _describe_yolo_model(model_path: Path) -> Dict[str, Any]:
    class_names = _svc()._load_yolo_class_names(model_path)
    profile = _svc()._infer_yolo_model_profile(class_names, model_path.name)
    canonical_names = [_svc()._canonicalize_yolo_class_name(name) for name in class_names]
    preview = canonical_names[:8]
    capabilities = _svc()._build_yolo_capabilities(profile["id"], model_path.name, canonical_names)

    return {
        "name": model_path.name,
        "path": str(model_path.resolve()),
        "size_mb": round(model_path.stat().st_size / (1024 * 1024), 1),
        "format": model_path.suffix.lower().lstrip("."),
        "class_count": len(class_names),
        "classes_preview": preview,
        "profile": profile["id"],
        "profile_label": profile["label"],
        "recommended_for_censor": profile["recommended_for_censor"],
        "message": profile["message"],
        "capabilities": capabilities,
    }


def _list_yolo_model_files(directory: Path) -> List[Dict[str, Any]]:
    if not directory.exists():
        return []

    files = []
    for path in sorted(directory.glob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".onnx", ".pt", ".pth", ".safetensors"}:
            continue
        if not is_nonempty_model_file(path):
            continue
        files.append(_svc()._describe_yolo_model(path))
    return files


def _get_fastembed_local_model_path(
    model_name: str,
    required_files: tuple[str, ...],
) -> Optional[str]:
    """Return a complete local FastEmbed model directory if present.

    Checks the canonical slug path first, then the huggingface_hub cache
    layout, then falls back to a deeper recursive scan. FastEmbed delegates
    downloads to huggingface_hub, which stores models as
    ``models--{org}--{repo}/snapshots/{hash}/model.onnx`` (double dash, three
    levels deep) — a layout the old two-level glob never reached, so a user
    who let the first-run download complete still saw "CLIP missing".
    """
    clip_root = Path(_svc().get_clip_model_dir())

    # 1) Canonical slug path (most common when we stage the model ourselves).
    repo_slug = model_name.replace("/", "-").replace("\\", "-")
    candidate = clip_root / repo_slug
    if not missing_model_artifacts(candidate, required_files):
        return str(candidate.resolve())

    # 2) huggingface_hub cache layout: models--{org}--{repo}/snapshots/{hash}/.
    #    Prefer the snapshot dir that actually contains model.onnx.
    hub_dir_name = "models--" + model_name.replace("/", "--").replace("\\", "--")
    hub_snapshots = clip_root / hub_dir_name / "snapshots"
    if hub_snapshots.is_dir():
        for snapshot in sorted(hub_snapshots.iterdir(), reverse=True):
            if not missing_model_artifacts(snapshot, required_files):
                return str(snapshot.resolve())

    # 3) Recursive fallback for any other FastEmbed/HF cache nesting. Bounded to
    #    a few levels so a huge clip_root can't trigger an unbounded walk.
    for depth_pattern in ("*/model.onnx", "*/*/model.onnx", "*/*/*/model.onnx", "*/*/*/*/model.onnx"):
        matches = sorted(clip_root.glob(depth_pattern))
        for match in matches:
            model_dir = match.parent
            # Skip obvious temp/cache directories
            if model_dir.name.startswith(".") or model_dir.name == "tmp":
                continue
            if is_nonempty_model_file(match) and not missing_model_artifacts(
                model_dir,
                required_files,
            ):
                return str(model_dir.resolve())

    return None


def get_default_legacy_model_path() -> Optional[str]:
    """Return the best local legacy YOLO model path for censor detection."""
    yolo_root = Path(_svc().get_yolo_model_dir())
    preferred_names = [
        "wenaka_yolov8s-seg.onnx",
        "wenaka_yolov8s-seg.pt",
        "yolo26s-seg.onnx",
        "yolo26s-seg.pt",
        "yolov8s-seg.onnx",
        "yolov8s-seg.pt",
    ]

    for name in preferred_names:
        candidate = yolo_root / name
        if is_nonempty_model_file(candidate):
            return str(candidate.resolve())

    for suffix in (".onnx", ".pt", ".pth"):
        matches = sorted(
            path for path in yolo_root.glob(f"*{suffix}") if is_nonempty_model_file(path)
        )
        if matches:
            return str(matches[0].resolve())
    return None


def get_sam3_checkpoint_path() -> Optional[str]:
    """Return the directory containing a complete transformers SAM3 checkpoint.

    The transformers ``Sam3Model.from_pretrained`` loader needs a directory
    holding ``config.json`` + ``model.safetensors`` + tokenizer files, so
    this returns the directory path (not a single weight file path).

    Covers the canonical download dirs, then the huggingface_hub cache layout
    (``models--facebook--sam3/snapshots/{hash}/``) and any nested placement, so
    a user who downloaded SAM3 via transformers/HF tooling — not just our
    direct ModelScope fetch — is still detected.
    """
    sam3_root = Path(_svc().get_sam3_model_dir())
    required_files = tuple(_svc().SAM3_CHECKPOINT_REQUIRED_FILES)
    candidate_dirs = [
        sam3_root / "facebook-sam3-modelscope",
        sam3_root / "facebook-sam3",
        sam3_root,
    ]
    for candidate in candidate_dirs:
        if not missing_model_artifacts(candidate, required_files):
            return str(candidate.resolve())

    # huggingface_hub cache layout: models--facebook--sam3/snapshots/{hash}/.
    for hub_dir in sorted(sam3_root.glob("models--*--*")):
        snapshots = hub_dir / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if not missing_model_artifacts(snapshot, required_files):
                return str(snapshot.resolve())

    # Recursive fallback: any dir under sam3_root holding both required files.
    if sam3_root.is_dir():
        for config_file in sorted(sam3_root.rglob("config.json")):
            checkpoint_dir = config_file.parent
            if not missing_model_artifacts(checkpoint_dir, required_files):
                return str(checkpoint_dir.resolve())
    return None


def get_clip_local_model_path() -> Optional[str]:
    """Return the complete local FastEmbed CLIP vision directory, if present."""
    return _get_fastembed_local_model_path(
        _svc().CLIP_MODEL_NAME,
        CLIP_VISION_REQUIRED_FILES,
    )


def get_clip_text_local_model_path() -> Optional[str]:
    """Return the complete local FastEmbed CLIP text directory, if present."""
    return _get_fastembed_local_model_path(
        _svc().CLIP_TEXT_MODEL_NAME,
        CLIP_TEXT_REQUIRED_FILES,
    )


def get_lucida_checkpoint_path() -> Optional[str]:
    """Return the complete local Lucida remote-code checkpoint directory."""
    lucida_root = Path(_svc().get_lucida_model_dir())
    required_files = tuple(_svc().LUCIDA_REQUIRED_FILES)
    try:
        is_complete = not missing_model_artifacts(lucida_root, required_files)
    except OSError:
        is_complete = False
    if is_complete:
        return str(lucida_root.resolve())
    return None


def get_florence2_checkpoint_path() -> Optional[str]:
    """Return the complete local Florence-2 remote-code checkpoint directory."""
    model_dir = Path(_svc().get_florence2_model_dir())
    required_files = tuple(_svc().FLORENCE2_REQUIRED_FILES)
    try:
        is_complete = not missing_model_artifacts(model_dir, required_files)
    except OSError:
        is_complete = False
    if is_complete:
        return str(model_dir.resolve())
    return None


def get_cl_tagger_v2_checkpoint_path() -> Optional[str]:
    """Return the complete local CL Tagger v2 checkpoint directory."""
    from cl_tagger_v2 import CL_TAGGER_V2_REQUIRED_FILES

    model_root = Path(_svc().get_cl_tagger_v2_model_dir()) / "cl-tagger-v2"
    try:
        is_complete = not missing_model_artifacts(model_root, CL_TAGGER_V2_REQUIRED_FILES)
    except OSError:
        is_complete = False
    if is_complete:
        return str(model_root.resolve())
    return None


def _find_kaloscope_dir(artist_root: Path) -> Optional[Path]:
    """Find the directory holding the Kaloscope checkpoint, case-insensitively.

    A ``git clone`` of the model repo creates a mixed-case ``Kaloscope2.0/``
    directory; the hardcoded lowercase ``kaloscope2.0`` path misses it on
    case-sensitive Linux filesystems. Prefer the canonical lowercase dir
    (fast path on Windows/macOS), then any case-insensitive kaloscope* match,
    then any directory anywhere under the artist root that actually contains
    the checkpoint basename. Returns the directory that directly contains the
    checkpoint (i.e. the ``.../448-90.13`` level) or ``None``.
    """
    if not artist_root.exists():
        return None

    checkpoint_basename = Path(_svc().ARTIST_KALOSCOPE_CHECKPOINT.replace("\\", "/")).name

    # 1) Canonical lowercase layout.
    canonical = artist_root / "kaloscope2.0" / _svc().ARTIST_KALOSCOPE_CHECKPOINT
    if is_nonempty_model_file(canonical):
        return canonical.parent

    # 2) Case-insensitive kaloscope* directory, checkpoint at the HF subpath.
    for child in sorted(artist_root.iterdir()):
        if not child.is_dir():
            continue
        normalized = child.name.lower().replace("-", "").replace("_", "").replace(".", "")
        if normalized.startswith("kaloscope"):
            checkpoint = child / _svc().ARTIST_KALOSCOPE_CHECKPOINT
            if is_nonempty_model_file(checkpoint):
                return checkpoint.parent

    # 3) Recursive search by basename (any manual placement depth).
    for checkpoint in sorted(artist_root.rglob(checkpoint_basename)):
        if is_nonempty_model_file(checkpoint):
            return checkpoint.parent
    return None


def _resolve_artist_runtime_path() -> Optional[str]:
    """Locate an LSNet runtime checkout on disk.

    Mirrors ``artist_identifier._resolve_lsnet_runtime_path`` so the
    ``/api/artists/diagnostics`` endpoint and the actual identifier agree
    on whether the runtime is available. Older installs commonly have the
    runtime under ``<repo>/models/artist/comfyui-lsnet-runtime/`` (legacy
    path); that location must be probed here too, otherwise the
    diagnostics permanently reports ``available: false`` even though the
    identifier loads and runs successfully.
    """
    candidates: List[Path] = []
    if _svc().ARTIST_LSNET_CODE_PATH:
        candidates.append(Path(_svc().ARTIST_LSNET_CODE_PATH).expanduser())

    artist_root = Path(_svc().get_artist_model_dir())
    candidates.extend(
        [
            artist_root / "comfyui-lsnet-runtime",
            artist_root / "comfyui-lsnet",
            artist_root / "lsnet-test",
        ]
    )
    # Legacy paths (pre-data/models/ migration). Keep parity with
    # artist_identifier._resolve_lsnet_runtime_path so diagnostics don't drift
    # from runtime behaviour. Skipped when the env opts out of legacy model
    # locations (the hermetic E2E harness sets
    # SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY=1) so a developer's real
    # models/artist/ checkout can't shadow the data-dir runtime. Production
    # never sets the flag, so legacy installs keep resolving exactly as before.
    if os.environ.get("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY") != "1":
        project_root = Path(_svc().__file__).resolve().parent.parent
        candidates.extend(
            [
                project_root / "models" / "artist" / "comfyui-lsnet",
                project_root / "models" / "artist" / "lsnet-test",
                project_root / "models" / "artist" / "comfyui-lsnet-runtime",
                project_root / "third_party" / "comfyui-lsnet",
                project_root / "third_party" / "lsnet-test",
            ]
        )

    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.exists():
            continue
        if (resolved / "lsnet_model").exists() or (resolved / "model").exists():
            return str(resolved)
    return None


def get_artist_checkpoint_path() -> Optional[str]:
    artist_root = Path(_svc().get_artist_model_dir())
    checkpoint_basename = Path(_svc().ARTIST_KALOSCOPE_CHECKPOINT.replace("\\", "/")).name
    checkpoint_dir = _svc()._find_kaloscope_dir(artist_root)
    if checkpoint_dir is None:
        return None
    candidate = checkpoint_dir / checkpoint_basename
    if is_nonempty_model_file(candidate):
        return str(candidate.resolve())
    return None


def get_artist_class_mapping_path() -> Optional[str]:
    artist_root = Path(_svc().get_artist_model_dir())
    mapping_basename = Path(_svc().ARTIST_KALOSCOPE_CLASS_MAPPING.replace("\\", "/")).name
    checkpoint_dir = _svc()._find_kaloscope_dir(artist_root)
    if checkpoint_dir is not None:
        # The mapping sits next to the checkpoint, or one level up (HF ships it
        # at the kaloscope dir root, with the checkpoint under 448-90.13/).
        for mapping_candidate in (
            checkpoint_dir / mapping_basename,
            checkpoint_dir.parent / mapping_basename,
        ):
            if is_nonempty_model_file(mapping_candidate):
                return str(mapping_candidate.resolve())
    # Last resort: any class_mapping.csv under the artist root.
    if artist_root.is_dir():
        for match in sorted(artist_root.rglob(mapping_basename)):
            if is_nonempty_model_file(match):
                return str(match.resolve())
    return None
