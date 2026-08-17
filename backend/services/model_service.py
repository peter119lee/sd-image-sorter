"""Service layer for model inventory and first-run model preparation.

Compatibility FILE facade since the 2026-07 sibling-module split: the download-progress family
(_download_progress + lock + get/_set + _direct_download_file), the
download-scheme guard, the WD14/SAM3 runtime-repair helpers,
_sam3_download_urls, the exception classes, the dependency-result plumbing,
_ensure_artist_runtime_direct, download_privacy_yolo_bundle, ModelService +
the _default_model_service singleton, and EVERY module-level seam binding
stay HERE -- tests monkeypatch services.model_service.<name>, aesthetic.py
lazily imports _direct_download_file from this path, and the
PROJECT_ROOT/parents[2] + repair_torch parents[1] anchors are
__file__-depth-sensitive. Pure helpers live in model_service_helpers, the
inventory branch table in model_service_inventory, and the prepare_model
routing in model_service_prepare; moved bodies resolve facade-bound seams
back through _svc() at call time. The SAM3 setup_steps copy must stay in
THIS file's raw source (test_release_build.py reads it).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import platform
import shutil
import tempfile
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from app_info import APP_VERSION
from config import TAGGER_MODELS, get_artist_model_dir, get_florence2_model_dir, get_sam3_model_dir, get_toriigate_model_dir, get_wd14_model_dir, get_yolo_model_dir
from model_health import (
    get_model_health,
    get_sam3_checkpoint_path,
    get_torch_onnx_runtime_health,
)
from optional_dependencies import DependencyInstallResult, ensure_group, ensure_group_with_soft_deps

# ---------------------------------------------------------------------------
# Facade re-imports (sibling-module split 2026-07). Every moved name is re-imported so the
# historical monkeypatch/import surface (services.model_service.<name>) keeps
# resolving on THIS module object; the moved bodies read patched seams back
# through _svc() at call time. Unused-looking imports are intentional
# re-exports (pyproject per-file F401 ignore).
# ---------------------------------------------------------------------------
from services.model_service_helpers import (
    _artist_checkpoint_url,
    _artist_resolve_url,
    _artist_runtime_url,
    _copy_existing_tree,
    _materialize_existing_file,
    _safe_extract_single_root_zip,
    build_civitai_auth_error,
    build_privacy_yolo_prepare_error,
)
from services.model_service_inventory import _build_inventory
from services.model_service_prepare import _prepare_model


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRIVACY_YOLO_PAGE_URL = "https://civitai.red/models/1736285/and-or-dickvaginatitsanuscum-yolov8-segment-model"
PRIVACY_YOLO_API_URL = "https://civitai.red/api/v1/models/1736285"
PRIVACY_YOLO_DIRECT_URL = (
    "https://civitai.red/api/download/models/1965032?type=Archive&format=Other"
)
SAM3_MODELSCOPE_URL = "https://modelscope.cn/models/facebook/sam3/files"
ARTIST_LSNET_RUNTIME_REVISION = "416d945e65b81ced93f1e762349d790ca92106b1"
ARTIST_LSNET_RUNTIME_ZIP_URL = (
    f"https://github.com/spawner1145/comfyui-lsnet/archive/{ARTIST_LSNET_RUNTIME_REVISION}.zip"
)

_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/124.0.0.0 Safari/537.36 sd-image-sorter/{APP_VERSION}"
    ),
    "Accept": "application/json, */*;q=0.8",
}
_MAX_PRIVACY_YOLO_ZIP_ENTRIES = 512
_MAX_PRIVACY_YOLO_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_ARTIST_RUNTIME_ZIP_ENTRIES = 1024
_MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_model_logger = logging.getLogger(__name__)
_download_progress = {"active": False, "url": "", "downloaded": 0, "total": 0, "filename": ""}
_download_progress_lock = threading.Lock()


class ExternalAuthRequiredError(RuntimeError):
    """Raised when a model download is blocked by an external auth wall."""

    def __init__(self, payload: Dict[str, Any], status_code: int = 409):
        super().__init__(payload.get("message") or payload.get("error") or "External authentication required")
        self.payload = payload
        self.status_code = status_code


class ModelPreparationFailedError(RuntimeError):
    """Raised when a model cannot be prepared automatically for a non-auth reason."""

    def __init__(self, payload: Dict[str, Any], status_code: int = 502):
        super().__init__(payload.get("message") or payload.get("error") or "Model preparation failed")
        self.payload = payload
        self.status_code = status_code


def _with_dependency_result(result: Dict[str, Any], install_result: DependencyInstallResult) -> Dict[str, Any]:
    if not install_result.installed_packages:
        return result
    return {
        **result,
        "installed_packages": list(install_result.installed_packages),
        "restart_recommended": install_result.restart_recommended,
    }


def _repair_wd14_onnxruntime_if_possible() -> Dict[str, Any]:
    # Windows AND Linux: Linux requirements pin the CPU-only onnxruntime, so
    # NVIDIA Linux users need this Prepare-time swap to onnxruntime-gpu just
    # like Windows users need the CUDA-DLL / DirectML fixups.
    if platform.system() not in ("Windows", "Linux"):
        return {"attempted": False, "ok": True, "reason": "unsupported_platform"}

    try:
        from repair_onnxruntime import repair_platform_onnxruntime

        result = repair_platform_onnxruntime(stream_pip=True)
        providers = [str(provider) for provider in (result.get("providers_after_repair") or [])]
        has_gpu_provider = "CUDAExecutionProvider" in providers or "DmlExecutionProvider" in providers
        vendor = str(result.get("gpu_vendor_primary") or "").lower()
        gpu_expected = vendor in {"nvidia", "amd", "intel"}
        return {
            "attempted": True,
            "ok": bool(has_gpu_provider or not gpu_expected),
            "repaired": bool(result.get("repaired")),
            "actions": list(result.get("actions") or []),
            "providers_after_repair": providers,
            "gpu_vendor_primary": result.get("gpu_vendor_primary"),
            "target_runtime": result.get("target_runtime"),
        }
    except Exception as exc:
        _model_logger.warning("WD14 ONNX Runtime GPU repair failed: %s", exc)
        return {"attempted": True, "ok": False, "error": str(exc)}


def _dependency_restart_result(model_id: str, install_result: DependencyInstallResult) -> Optional[Dict[str, Any]]:
    if not install_result.installed_packages:
        return None
    packages = ", ".join(install_result.installed_packages)
    return {
        "status": "needs_restart",
        "model_id": model_id,
        "message": (
            f"Installed Python packages for this feature: {packages}. "
            "Restart SD Image Sorter, then click Prepare again if model files still need downloading."
        ),
        "installed_packages": list(install_result.installed_packages),
        "restart_recommended": True,
    }


_ALLOWED_DOWNLOAD_SCHEMES = ("https", "http")


def _resolve_allowed_download_schemes() -> tuple[str, ...]:
    """Return the schemes urlopen_with_ua should accept on this run.

    Production stays restricted to ``https``/``http``. The Playwright
    end-to-end suite stages model fixtures as local files and points the
    backend at them via ``file://`` URLs in environment variables. Setting
    ``SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS=1`` opts into accepting
    ``file://`` so those fixture-driven flows can run without a real CDN.
    The flag is intentionally namespaced as ``_TEST_`` so operators do not
    enable it in production by accident.
    """
    if os.environ.get("SD_IMAGE_SORTER_TEST_ALLOW_FILE_DOWNLOADS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _ALLOWED_DOWNLOAD_SCHEMES + ("file",)
    return _ALLOWED_DOWNLOAD_SCHEMES


def urlopen_with_ua(url: str, timeout: int = 30):
    """Open a URL with a browser-style User-Agent for CDNs that reject urllib.

    Hardened against env-var-supplied ``file://`` / ``ftp://`` URLs that could
    coerce ``urlopen`` into reading local files or talking to unintended
    services. Scheme is restricted to ``https`` (preferred) or ``http``,
    plus ``file`` only when the explicit test flag opts in.
    """
    from urllib.parse import urlparse

    scheme = (urlparse(url).scheme or "").lower()
    allowed_schemes = _resolve_allowed_download_schemes()
    if scheme not in allowed_schemes:
        raise ValueError(
            f"Refusing to download from scheme {scheme!r}; "
            f"only {allowed_schemes} are allowed."
        )
    req = urllib.request.Request(url, headers=_DOWNLOAD_HEADERS)
    return urllib.request.urlopen(req, timeout=timeout)


def get_download_progress() -> dict:
    with _download_progress_lock:
        return dict(_download_progress)


def _set_download_progress(**updates: Any) -> None:
    with _download_progress_lock:
        _download_progress.update(updates)


def _direct_download_file(url: str, dest: Path, *, timeout: int = 300) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _model_logger.info("Downloading %s → %s", url, dest)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    delay_ms = max(0, int(os.environ.get("SD_IMAGE_SORTER_DOWNLOAD_CHUNK_DELAY_MS", "0") or "0"))
    _set_download_progress(active=True, url=url, downloaded=0, total=0, filename=dest.name)
    try:
        with urlopen_with_ua(url, timeout=timeout) as src, open(tmp, "wb") as dst:
            total = int(src.headers.get("Content-Length") or 0)
            _set_download_progress(total=total)
            downloaded = 0
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                downloaded += len(chunk)
                _set_download_progress(downloaded=downloaded)
                if delay_ms:
                    time.sleep(delay_ms / 1000)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        _set_download_progress(active=False, downloaded=0, total=0)
    return dest


# Files the transformers SAM3 loader needs in the checkpoint directory.
# ``sam3.pt`` is intentionally NOT in this list — it's the legacy 3.45 GB
# pickle from the unmaintained sam3 0.1.3 package; the transformers backend
# loads ``model.safetensors`` directly from this same directory.
_SAM3_DOWNLOAD_FILES = (
    "config.json",
    "model.safetensors",
    "processor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
)


def _sam3_download_urls() -> List[Tuple[str, str]]:
    """Return ``(filename, url)`` pairs for the full transformers SAM3 checkpoint.

    Honours ``SD_IMAGE_SORTER_SAM3_BASE_URL`` for users who want to point the
    downloader at an alternate ModelScope/HF mirror. Defaults to ModelScope's
    facebook/sam3 so China users do not need HF access.
    """
    configured_base = os.environ.get("SD_IMAGE_SORTER_SAM3_BASE_URL", "").strip().rstrip("/")
    base = configured_base or "https://modelscope.cn/models/facebook/sam3/resolve/master"
    return [(name, f"{base}/{name}") for name in _SAM3_DOWNLOAD_FILES]


def _torch_runtime_failure_result(
    error: str,
    returncode: Optional[int],
    stdout: str,
    stderr: str,
) -> Dict[str, Any]:
    return {
        "attempted": True,
        "ok": False,
        "repaired": False,
        "restart_required": False,
        "returncode": returncode,
        "error": error,
        "stdout": stdout,
        "stderr": stderr,
    }


def _repair_torch_runtime_if_possible() -> Dict[str, Any]:
    if platform.system() != "Windows":
        return {"attempted": False, "reason": "non_windows"}
    if os.environ.get("SD_IMAGE_SORTER_SKIP_TORCH_REPAIR", "").strip().lower() in {"1", "true", "yes", "on"}:
        return {"attempted": False, "reason": "disabled"}

    repair_script = Path(__file__).resolve().parents[1] / "repair_torch_runtime.py"
    if not repair_script.exists():
        return {"attempted": False, "reason": "repair_script_missing"}

    _model_logger.info("Checking the shared PyTorch runtime before preparing a GPU model")
    try:
        completed = subprocess.run(
            [sys.executable, str(repair_script), "--auto", "--json"],
            cwd=str(repair_script.parent),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("SD_IMAGE_SORTER_TORCH_REPAIR_TIMEOUT", "3600") or "3600"),
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _torch_runtime_failure_result(
            f"Could not start the PyTorch runtime repair: {type(exc).__name__}: {exc}",
            None,
            "",
            "",
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        return _torch_runtime_failure_result(
            (
                f"PyTorch runtime repair exited with code {completed.returncode}. "
                f"stdout={stdout.strip()!r}; stderr={stderr.strip()!r}"
            ),
            completed.returncode,
            stdout,
            stderr,
        )

    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return _torch_runtime_failure_result(
            (
                "PyTorch runtime repair returned invalid JSON despite exit code 0: "
                f"{type(exc).__name__}: {exc}; stdout={stdout.strip()!r}; "
                f"stderr={stderr.strip()!r}"
            ),
            completed.returncode,
            stdout,
            stderr,
        )

    if not isinstance(payload, dict):
        return _torch_runtime_failure_result(
            f"PyTorch runtime repair returned {type(payload).__name__}; expected a JSON object.",
            completed.returncode,
            stdout,
            stderr,
        )

    torch_version = payload.get("torch_version")
    torch_cuda_build = payload.get("torch_cuda_build")
    torch_cuda_available = payload.get("torch_cuda_available")
    repaired = payload.get("repaired")
    actions = payload.get("actions")
    valid_payload = (
        (torch_version is None or isinstance(torch_version, str))
        and (torch_cuda_build is None or isinstance(torch_cuda_build, str))
        and isinstance(torch_cuda_available, bool)
        and isinstance(repaired, bool)
        and isinstance(actions, list)
        and all(isinstance(action, str) for action in actions)
    )
    if not valid_payload:
        return _torch_runtime_failure_result(
            (
                "PyTorch runtime repair JSON is missing required typed fields: "
                "torch_version, torch_cuda_build, torch_cuda_available, repaired, actions."
            ),
            completed.returncode,
            stdout,
            stderr,
        )

    compatible = (
        isinstance(torch_version, str)
        and isinstance(torch_cuda_build, str)
        and torch_cuda_build.split(".", 1)[0] == "12"
        and torch_cuda_available is True
    )
    if not compatible:
        return {
            **_torch_runtime_failure_result(
                (
                    "PyTorch repair exited with code 0 but left an incompatible GPU runtime: "
                    f"torch={torch_version or 'missing'}, CUDA {torch_cuda_build or 'missing'}, "
                    f"available={torch_cuda_available!r}. Use Model Manager Prepare to "
                    "install the supported cu126 runtime, then restart the app."
                ),
                completed.returncode,
                stdout,
                stderr,
            ),
            "actions": list(actions),
        }

    importlib.invalidate_caches()
    return {
        "attempted": True,
        "ok": True,
        "repaired": repaired,
        "restart_required": repaired,
        "returncode": completed.returncode,
        "torch_version": torch_version,
        "torch_cuda_build": torch_cuda_build,
        "torch_cuda_available": torch_cuda_available,
        "actions": list(actions),
    }


def _repair_sam3_runtime_if_possible() -> Dict[str, Any]:
    return _repair_torch_runtime_if_possible()


def _ensure_artist_runtime_direct() -> str:
    target_dir = Path(get_artist_model_dir()) / "comfyui-lsnet-runtime"
    if (target_dir / "lsnet_model").exists():
        return str(target_dir.resolve())

    if os.environ.get("SD_IMAGE_SORTER_DISABLE_LEGACY_MODEL_COPY") != "1":
        legacy_dir = PROJECT_ROOT / "models" / "artist" / "comfyui-lsnet-runtime"
        if _copy_existing_tree(legacy_dir, target_dir, "lsnet_model"):
            return str(target_dir.resolve())

    with tempfile.TemporaryDirectory(prefix="kaloscope-runtime-") as tmp_dir:
        zip_path = Path(tmp_dir) / "comfyui-lsnet-runtime.zip"
        _direct_download_file(_artist_runtime_url(), zip_path, timeout=300)
        _safe_extract_single_root_zip(
            zip_path,
            target_dir,
            max_entries=_MAX_ARTIST_RUNTIME_ZIP_ENTRIES,
            max_bytes=_MAX_ARTIST_RUNTIME_UNCOMPRESSED_BYTES,
        )
    if not (target_dir / "lsnet_model").exists():
        raise RuntimeError("Downloaded LSNet runtime did not contain lsnet_model.")
    return str(target_dir.resolve())




# MODELS-07: the "essentials" set surfaced first (with a Recommended badge) in
# the Model Manager. These entries provide one usable model for each core
# feature, including natural-language captions and persistent training masks.
# It MUST stay in sync with the entries whose ``recommended`` flag is true in
# routers/models.py; the focused tests compare both sets so they cannot drift.
RECOMMENDED_MODEL_IDS = frozenset({
    "wd14",
    "censor-nudenet",
    "clip",
    "aesthetic",
    "artist",
    "sam3",
    "florence2",
    "lucida",
})


def _sam3_inventory_setup_steps() -> List[str]:
    """SAM3 Model-Manager card setup copy (raw-source contract).

    tests/test_release_build.py::test_model_manager_sam3_setup_copy_matches_lazy_prepare_policy
    reads THIS file's raw source text and asserts these exact strings stay in
    backend/services/model_service.py; the rest of the inventory card table
    lives in services/model_service_inventory.py and fetches this list
    through the facade at call time (so get_sam3_model_dir stays a
    facade-bound monkeypatch seam).
    """
    return [
        "Click Prepare / Download to install SAM3 Python runtime packages if they are missing.",
        "Restart SD Image Sorter if the Prepare result says Python packages were installed.",
        "Click Prepare / Download again to fetch model.safetensors, or place sam3.pt / model.safetensors manually only if the download fails: " + str(Path(get_sam3_model_dir()) / "facebook-sam3-modelscope"),
    ]


class ModelService:
    """Owns model health aggregation, downloads, and preparation side effects."""

    def get_status(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "models": self.build_model_inventory(),
            "health": get_model_health(),
        }

    def build_model_inventory(self) -> List[Dict[str, Any]]:
        health = get_model_health()
        return _build_inventory(health)

    def download_privacy_yolo_bundle(self) -> Dict[str, str]:
        target_dir = Path(get_yolo_model_dir())
        target_dir.mkdir(parents=True, exist_ok=True)

        download_url: Optional[str] = None
        try:
            with urlopen_with_ua(PRIVACY_YOLO_API_URL, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            versions = payload.get("modelVersions") or []
            if versions:
                download_url = versions[0].get("downloadUrl") or None
        except Exception:
            download_url = None
        if not download_url:
            download_url = PRIVACY_YOLO_DIRECT_URL

        civitai_auth_error = build_civitai_auth_error(target_dir)

        with tempfile.TemporaryDirectory(prefix="privacy-yolo-") as tmp_dir:
            zip_path = Path(tmp_dir) / "privacy-yolo.zip"
            response_content_type = ""
            try:
                src_ctx = urlopen_with_ua(download_url, timeout=300)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise ExternalAuthRequiredError(civitai_auth_error) from exc
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        f"Civitai returned HTTP {exc.code} while downloading the archive.",
                    )
                ) from exc
            except urllib.error.URLError as exc:
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        f"Download request failed: {exc.reason or exc}",
                    )
                ) from exc

            try:
                with src_ctx as src, open(zip_path, "wb") as dst:
                    response_content_type = (src.headers.get("Content-Type") or "").lower()
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
            except OSError as exc:
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        f"Failed to store the downloaded archive locally: {exc}",
                    )
                ) from exc

            if "text/html" in response_content_type or not zipfile.is_zipfile(zip_path):
                if "text/html" in response_content_type:
                    raise ExternalAuthRequiredError(civitai_auth_error)
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        "Downloaded file was not a valid zip archive.",
                    )
                )

            try:
                target_root = target_dir.resolve()
                with zipfile.ZipFile(zip_path, "r") as archive:
                    total_uncompressed_bytes = 0
                    members = archive.infolist()
                    if len(members) > _MAX_PRIVACY_YOLO_ZIP_ENTRIES:
                        raise ModelPreparationFailedError(
                            build_privacy_yolo_prepare_error(
                                target_dir,
                                "Archive contained too many files to extract safely.",
                            )
                        )
                    for member in members:
                        normalized_name = str(member.filename or "").replace("\\", "/").strip()
                        relative_name = PurePosixPath(normalized_name)
                        if (
                            not normalized_name
                            or relative_name.is_absolute()
                            or normalized_name[:2].endswith(":")
                            or ".." in relative_name.parts
                        ):
                            raise ModelPreparationFailedError(
                                build_privacy_yolo_prepare_error(
                                    target_dir,
                                    f"Archive contained an unsafe path: {member.filename}",
                                )
                            )
                        member_path = (target_root / relative_name).resolve()
                        try:
                            member_path.relative_to(target_root)
                        except ValueError as exc:
                            raise ModelPreparationFailedError(
                                build_privacy_yolo_prepare_error(
                                    target_dir,
                                    f"Archive contained an unsafe path: {member.filename}",
                                )
                            ) from exc
                        if not member.is_dir():
                            total_uncompressed_bytes += member.file_size
                            if total_uncompressed_bytes > _MAX_PRIVACY_YOLO_UNCOMPRESSED_BYTES:
                                raise ModelPreparationFailedError(
                                    build_privacy_yolo_prepare_error(
                                        target_dir,
                                        "Archive uncompressed size exceeded the safe extraction limit.",
                                    )
                                )
                    archive.extractall(target_root)
            except zipfile.BadZipFile as exc:
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        f"Downloaded archive could not be opened as a zip file: {exc}",
                    )
                ) from exc
            except OSError as exc:
                raise ModelPreparationFailedError(
                    build_privacy_yolo_prepare_error(
                        target_dir,
                        f"Failed to extract the Privacy YOLO archive: {exc}",
                    )
                ) from exc

        default_path = get_model_health()["censor"]["legacy"]["default_model_path"]
        return {
            "model_dir": str(target_dir.resolve()),
            "default_model_path": default_path or "",
        }

    def prepare_model(self, model_id: str, *, source: Optional[str] = None, variant: Optional[str] = None) -> Dict[str, Any]:
        return _prepare_model(self, model_id, source=source, variant=variant)


_default_model_service = ModelService()


def get_model_service() -> ModelService:
    return _default_model_service
