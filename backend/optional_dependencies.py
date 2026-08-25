"""Optional dependency installation helpers.

Core startup deliberately avoids GB-scale AI stacks. Feature entry points call
these helpers only after the user explicitly asks to prepare that feature.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
from pathlib import Path
import subprocess
import sys
import logging
import platform
from dataclasses import dataclass
from typing import Iterable, Sequence

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from runtime_dependency_check import (
    ORT_DISTRIBUTION_NAMES,
    InstalledDistribution,
    RuntimeDependencyError,
    RuntimeSnapshot,
    active_distribution_requirements,
    capture_named_runtime_snapshot,
    validate_runtime_dependencies,
    validated_onnxruntime_provider,
)


@dataclass(frozen=True)
class DependencyInstallResult:
    installed_packages: tuple[str, ...]
    restart_recommended: bool = False
    restart_reason: str = ""


# Why a restart can be genuinely unavoidable. Anything not covered by these
# three cases is importable straight away, so the feature must keep going in
# this process instead of sending the user back to the launcher.
#
#   LOADED_MODULE  sys.modules already holds the module object. Re-importing
#                  returns the stale one no matter what pip wrote to disk, and
#                  a loaded native extension cannot be swapped in place.
#   DLL_LOCK       Windows refused to overwrite a file another loaded library
#                  is holding, so the install landed only partially.
#   FRESH_PROCESS  The module imports in a clean interpreter but not in this
#                  one (stale package metadata, .pth side effects).
RESTART_REASON_LOADED_MODULE = "replaced_loaded_module"
RESTART_REASON_DLL_LOCK = "windows_dll_lock"
RESTART_REASON_IMPORT_NEEDS_FRESH_PROCESS = "import_needs_fresh_process"

# Importing torch on a cold filesystem cache is slow but not unbounded. The
# probe exists to answer a yes/no question, so cap it rather than letting a
# wedged interpreter hang the Prepare thread forever.
_CLEAN_IMPORT_PROBE_TIMEOUT_SECONDS = 300


class UnsafeDependencyInstallError(RuntimeError):
    """Raised when optional packages would be installed outside the app venv."""


class UnsupportedOptionalDependencyError(RuntimeError):
    """Raised when a dependency group has no security-supported platform build."""


class OptionalDependencyMetadataError(RuntimeError):
    """Raised when an optional package cannot be installed from locked metadata."""


class OptionalDependencyImportError(RuntimeError):
    """Raised when a newly installed optional package cannot be imported."""


_TRITON_PACKAGE = "triton-windows" if sys.platform == "win32" else "triton>=3.0.0"

OPTIONAL_DEPENDENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "clip": ("fastembed>=0.4.0",),
    "aesthetic": ("torch>=2.0.0", "open-clip-torch>=2.24.0"),
    "artist": ("torch>=2.0.0", "transformers>=5.6.0", "timm>=0.9.0", "safetensors>=0.4.0"),
    "lucida": (
        "torch>=2.0.0",
        "torchvision>=0.23.0",
        "transformers>=5.6.0",
        "timm>=0.9.0",
        "safetensors>=0.4.0",
        "huggingface-hub>=0.24.0",
        "kornia==0.8.3",
        "einops>=0.8.0",
    ),
    "nudenet": ("nudenet>=3.0.0",),
    "yolo": ("torch>=2.0.0", "ultralytics>=8.4.0"),
    "sam3": (
        "torch>=2.0.0",
        "transformers>=5.6.0",
        "safetensors>=0.4.0",
        "opencv-python>=4.9.0",
    ),
    "toriigate": ("torch>=2.0.0", "transformers>=5.6.0", "safetensors>=0.4.0"),
    "florence2": (
        "torch>=2.0.0",
        "transformers>=5.6.0",
        "timm>=0.9.0",
        "einops>=0.8.0",
        "safetensors>=0.4.0",
        "huggingface-hub>=0.24.0",
    ),
    "cl-tagger-v2": ("huggingface-hub>=0.24.0",),
    "translation": ("translators==6.0.4",),
    # tipo-kgen imports torch/transformers at module load even for GGUF.
    # llama-cpp-python is installed separately from the official CPU wheel
    # index (never the PyPI sdist).
    "tipo": (
        "torch>=2.0.0",
        "transformers>=5.6.0",
        "huggingface-hub>=0.24.0",
        "tipo-kgen>=0.3.1",
    ),
}

TORCH_DEPENDENCY_GROUPS: frozenset[str] = frozenset(
    {"aesthetic", "artist", "florence2", "lucida", "sam3", "toriigate", "yolo", "tipo"}
)


def _torch_runtime_support_error(
    system: str,
    machine: str,
    macos_version: str,
) -> str | None:
    if system != "Darwin":
        return None
    if machine.lower() != "arm64":
        return (
            "Full AI Torch features are unavailable on Intel Mac because "
            "PyTorch no longer publishes a security-supported Intel Mac wheel. "
            "Core Gallery, metadata, sorting, and ONNX features remain available. "
            "Use Apple Silicon with macOS 14 or newer, Windows, or Linux for "
            "Torch-backed features."
        )

    version_match = re.match(r"^(\d+)", macos_version.strip())
    macos_major = int(version_match.group(1)) if version_match else None
    if macos_major is None or macos_major < 14:
        detected_version = macos_version.strip() or "unknown"
        return (
            "Full AI Torch features require macOS 14 or newer on Apple Silicon "
            "because the security-supported Torch 2.13 wheel targets macOS 14. "
            f"Detected macOS version: {detected_version}. Upgrade macOS or "
            "continue with the core features."
        )
    return None


def _validate_optional_group_platform(group: str) -> None:
    if group not in TORCH_DEPENDENCY_GROUPS:
        return

    system = platform.system()
    if group == "sam3" and system == "Darwin":
        raise UnsupportedOptionalDependencyError(
            "SAM3 is CUDA-only in the current verified product runtime and "
            "is unavailable on macOS. Core Gallery, metadata, sorting, and "
            "ONNX features remain available."
        )

    support_error = _torch_runtime_support_error(
        system,
        platform.machine(),
        platform.mac_ver()[0],
    )
    if support_error is not None:
        raise UnsupportedOptionalDependencyError(support_error)


SOFT_DEPENDENCY_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "artist": (("triton", _TRITON_PACKAGE),),
}


GROUP_IMPORTS: dict[str, tuple[str, ...]] = {
    "clip": ("fastembed",),
    "aesthetic": ("torch", "open_clip"),
    "artist": ("torch", "transformers", "timm", "safetensors"),
    "lucida": (
        "torch",
        "torchvision",
        "transformers",
        "timm",
        "safetensors",
        "huggingface_hub",
        "kornia",
        "einops",
    ),
    "nudenet": ("nudenet",),
    "yolo": ("torch", "ultralytics"),
    "sam3": ("torch", "transformers", "safetensors", "cv2"),
    "toriigate": ("torch", "transformers", "safetensors"),
    "florence2": (
        "torch",
        "transformers",
        "timm",
        "einops",
        "safetensors",
        "huggingface_hub",
    ),
    "cl-tagger-v2": ("huggingface_hub",),
    "translation": ("translators",),
    "tipo": ("torch", "transformers", "huggingface_hub", "kgen"),
}

IMPORT_TO_PACKAGE_HINT: dict[str, str] = {
    "fastembed": "fastembed>=0.4.0",
    "torch": "torch>=2.0.0",
    "open_clip": "open-clip-torch>=2.24.0",
    "transformers": "transformers>=5.6.0",
    "timm": "timm>=0.9.0",
    "safetensors": "safetensors>=0.4.0",
    "nudenet": "nudenet>=3.0.0",
    "ultralytics": "ultralytics>=8.4.0",
    "cv2": "opencv-python>=4.9.0",
    "triton": _TRITON_PACKAGE,
    "translators": "translators==6.0.4",
    "kgen": "tipo-kgen>=0.3.1",
    "llama_cpp": "llama-cpp-python>=0.3.24",
}


_REQUIREMENTS_CACHE: dict[str, str] | None = None


def _normalize_package_name(package_name: str) -> str:
    return package_name.lower().replace("-", "_")


def _requirement_marker_matches(marker_text: str | None) -> bool:
    if not marker_text:
        return True
    try:
        return Marker(marker_text).evaluate()
    except InvalidMarker:
        return False


def _load_requirement_version_map() -> dict[str, str]:
    global _REQUIREMENTS_CACHE
    if _REQUIREMENTS_CACHE is not None:
        return _REQUIREMENTS_CACHE

    mapping: dict[str, str] = {}
    requirements_path = Path(__file__).resolve().parent / "requirements.txt"
    if not requirements_path.exists():
        _REQUIREMENTS_CACHE = mapping
        return mapping

    requirement_line = re.compile(
        r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(==|>=)\s*([^;\s]+)(?:\s*;\s*(.+))?$"
    )
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = requirement_line.match(line)
        if not match:
            continue
        package_name, _operator, version, marker_text = match.groups()
        if not _requirement_marker_matches(marker_text):
            continue
        normalized = _normalize_package_name(package_name)
        mapping[normalized] = f"{package_name}=={version.strip()}"

    _REQUIREMENTS_CACHE = mapping
    return mapping


def _lock_package_spec(package_spec: str) -> str:
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(==|>=)\s*([^;\[]+)", package_spec)
    if not match:
        lock_map = _load_requirement_version_map()
        return lock_map.get(_normalize_package_name(package_spec), package_spec)

    package_name, operator, _required_version = match.groups()
    if operator != ">=":
        return package_spec

    lock_map = _load_requirement_version_map()
    locked = lock_map.get(_normalize_package_name(package_name))
    return locked or package_spec

def missing_imports(module_names: Iterable[str]) -> list[str]:
    return [module_name for module_name in module_names if importlib.util.find_spec(module_name) is None]


def _installed_version_satisfies(package_spec: str) -> bool:
    try:
        requirement = Requirement(package_spec)
    except InvalidRequirement as exc:
        raise ValueError(
            f"Invalid optional dependency requirement {package_spec!r}: {exc}"
        ) from exc

    if not requirement.specifier:
        return True

    try:
        installed_text = importlib.metadata.version(requirement.name)
    except importlib.metadata.PackageNotFoundError:
        return False

    try:
        installed_version = Version(installed_text)
    except InvalidVersion as exc:
        raise RuntimeError(
            f"Package {requirement.name!r} reported invalid installed version "
            f"{installed_text!r} while checking {package_spec!r}. Reinstall the "
            "exact package from the application lock."
        ) from exc

    return requirement.specifier.contains(
        installed_version,
        prereleases=None,
    )

def _needs_install(module_name: str, package_spec: str) -> bool:
    """Check if a module needs installation.

    Beyond find_spec + version check, attempts a real import for packages
    known to fail when installed with --no-deps (missing transitive deps).
    """
    if importlib.util.find_spec(module_name) is None:
        return True
    if not _installed_version_satisfies(package_spec):
        return True
    # Packages that are known to break when installed with --no-deps
    # (their top-level __init__.py imports sub-dependencies immediately).
    _NO_DEPS_FRAGILE = {"fastembed", "nudenet", "ultralytics"}
    if module_name in _NO_DEPS_FRAGILE:
        try:
            __import__(module_name)
        except ImportError:
            return True
        except OSError as exc:
            # torch's cudnn / cuda DLL chain raises OSError (Windows
            # error 127 / 126) when a runtime DLL fails to load. The
            # previous narrow ``except ImportError`` let those bubble
            # up as raw "[WinError 127] cudnn_cnn64_9.dll" errors in
            # the prepare flow. Treat them as "needs reinstall" so the
            # downstream pipeline at least attempts a repair, and log
            # the underlying OS error so the user can find it. If the
            # reinstall doesn't fix it, the problem is system-level
            # (CUDA toolkit / VC++ runtime) and the user should run
            # the dedicated torch-runtime repair tool.
            logging.getLogger(__name__).warning(
                "Optional package %s could not be imported even though it is "
                "installed - DLL load failed with %s. Triggering reinstall.",
                module_name,
                exc,
            )
            return True
    return False


def _running_in_virtualenv() -> bool:
    return bool(getattr(sys, "base_prefix", sys.prefix) != sys.prefix or getattr(sys, "real_prefix", None))


def _running_in_portable_python() -> bool:
    try:
        package_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        portable_python_root = os.path.normcase(os.path.abspath(os.path.join(package_root, "python")))
        executable = os.path.normcase(os.path.abspath(sys.executable))
        return os.path.commonpath([executable, portable_python_root]) == portable_python_root
    except (OSError, ValueError):
        return False


def _allow_system_python_install() -> bool:
    return os.environ.get("SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL") == "1"


def _assert_safe_install_target(packages: Sequence[str]) -> None:
    if not packages or _running_in_virtualenv() or _running_in_portable_python() or _allow_system_python_install():
        return
    package_list = ", ".join(packages)
    raise UnsafeDependencyInstallError(
        "Refusing to install optional Python packages into the system Python environment. "
        "Start SD Image Sorter with run.bat, run-portable.bat, or run.sh so the app-owned Python runtime is used, then click Prepare again. "
        "If you are intentionally managing your own environment, create/activate a virtual environment first "
        "or set SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL=1. "
        f"Packages not installed: {package_list}"
    )


_log = logging.getLogger(__name__)

_WINDOWS_DLL_LOCK_MARKERS = ("WinError 5", "Access is denied", "存取被拒")

_ORT_CONSUMER_IMPORTS: dict[str, str] = {
    "fastembed": "fastembed",
    "nudenet": "nudenet",
}


def _requirement_name(package_spec: str) -> str | None:
    try:
        return canonicalize_name(Requirement(package_spec).name)
    except InvalidRequirement:
        return None


def _installed_distribution(
    snapshot: RuntimeSnapshot,
    package_name: str,
) -> InstalledDistribution:
    canonical_name = canonicalize_name(package_name)
    matches = tuple(
        distribution
        for distribution in snapshot.distributions
        if canonicalize_name(distribution.name) == canonical_name
    )
    if len(matches) != 1:
        raise OptionalDependencyMetadataError(
            f"Expected exactly one installed {package_name!r} distribution after pip "
            f"completed, but found {len(matches)}. Reinstall the feature from the "
            "application lock."
        )
    return matches[0]


def _locked_requirement(
    consumer: InstalledDistribution,
    requirement: Requirement,
) -> str:
    lock_map = _load_requirement_version_map()
    locked_spec = lock_map.get(_normalize_package_name(requirement.name))
    if locked_spec is None:
        raise OptionalDependencyMetadataError(
            f"Installed optional package {consumer.name} {consumer.version} requires "
            f"{requirement}, but {requirement.name!r} has no active exact version in "
            "backend/requirements.txt. Refresh the application dependency lock before "
            "preparing this feature."
        )

    try:
        locked_requirement = Requirement(locked_spec)
    except InvalidRequirement as error:
        raise OptionalDependencyMetadataError(
            f"Application lock entry {locked_spec!r} for {requirement.name!r} is "
            f"invalid: {error}"
        ) from error
    exact_versions = tuple(
        specifier.version
        for specifier in locked_requirement.specifier
        if specifier.operator == "==" and not specifier.version.endswith(".*")
    )
    if len(exact_versions) != 1:
        raise OptionalDependencyMetadataError(
            f"Application lock entry {locked_spec!r} for {requirement.name!r} must "
            "contain one exact non-wildcard version."
        )
    try:
        locked_version = Version(exact_versions[0])
    except InvalidVersion as error:
        raise OptionalDependencyMetadataError(
            f"Application lock entry {locked_spec!r} contains invalid version "
            f"{exact_versions[0]!r}."
        ) from error
    if requirement.specifier and not requirement.specifier.contains(
        locked_version,
        prereleases=None,
    ):
        raise OptionalDependencyMetadataError(
            f"Installed optional package {consumer.name} {consumer.version} requires "
            f"{requirement}, but the application lock selects {locked_spec}. Refresh "
            "the lock with a compatible version before preparing this feature."
        )

    if not requirement.extras:
        return locked_spec
    extras = ",".join(sorted(requirement.extras))
    return f"{locked_requirement.name}[{extras}]=={exact_versions[0]}"


def _validate_ort_requirement(
    consumer: InstalledDistribution,
    requirement: Requirement,
    provider: InstalledDistribution,
    snapshot: RuntimeSnapshot,
) -> None:
    validate_runtime_dependencies(
        RuntimeSnapshot(
            distributions=(
                InstalledDistribution(
                    name=consumer.name,
                    version=consumer.version,
                    requirements=(str(requirement),),
                ),
                InstalledDistribution(
                    name=provider.name,
                    version=provider.version,
                    requirements=(),
                ),
            ),
            onnxruntime_module_version=snapshot.onnxruntime_module_version,
            marker_environment=snapshot.marker_environment,
        )
    )


def _locked_non_ort_dependencies(
    consumer: InstalledDistribution,
    provider: InstalledDistribution,
    snapshot: RuntimeSnapshot,
) -> tuple[str, ...]:
    locked_dependencies: list[str] = []
    for requirement in active_distribution_requirements(
        consumer,
        snapshot.marker_environment,
    ):
        requirement_name = canonicalize_name(requirement.name)
        if requirement_name in ORT_DISTRIBUTION_NAMES:
            _validate_ort_requirement(
                consumer,
                requirement,
                provider,
                snapshot,
            )
            continue
        locked_spec = _locked_requirement(consumer, requirement)
        if locked_spec not in locked_dependencies:
            locked_dependencies.append(locked_spec)
    return tuple(locked_dependencies)


def _install_without_dependencies(
    package_spec: str,
    index_args: Sequence[str],
) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--no-deps",
            *index_args,
            package_spec,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    importlib.invalidate_caches()


def _clean_process_import_command(module_name: str) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"import importlib; importlib.import_module({module_name!r})",
    ]


def _probe_import_in_clean_process(module_name: str) -> bool:
    """Return True when a freshly started interpreter can import the module."""
    try:
        subprocess.run(
            _clean_process_import_command(module_name),
            check=True,
            text=True,
            capture_output=True,
            timeout=_CLEAN_IMPORT_PROBE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def _restart_reason_after_install(
    *,
    modules_to_verify: Sequence[str],
    preloaded_modules: Sequence[str],
    dll_locked: bool,
) -> str:
    """Return why this install needs a fresh interpreter, or "" if it does not.

    Historically every install that touched a single package told the user to
    restart, which also meant Prepare stopped before downloading any model
    weights. Installing a package this process has never imported does not
    need a new interpreter, so ask the runtime instead of assuming.
    """
    if dll_locked:
        return RESTART_REASON_DLL_LOCK
    if preloaded_modules:
        # An in-process import here would happily return the stale module and
        # report success, so do not even try.
        return RESTART_REASON_LOADED_MODULE

    importlib.invalidate_caches()
    for module_name in modules_to_verify:
        try:
            importlib.import_module(module_name)
        except (ImportError, OSError) as error:
            if _probe_import_in_clean_process(module_name):
                return RESTART_REASON_IMPORT_NEEDS_FRESH_PROCESS
            raise OptionalDependencyImportError(
                f"Installed the packages for module {module_name!r}, but neither "
                f"this process nor a clean Python process can import it "
                f"({type(error).__name__}: {error}). The install is incomplete - "
                "check the preceding pip output, then run Prepare again."
            ) from error
    return ""


def _import_optional_package(package_name: str, module_name: str) -> None:
    importlib.invalidate_caches()
    try:
        importlib.import_module(module_name)
    except (ImportError, OSError) as error:
        raise OptionalDependencyImportError(
            f"Installed optional package {package_name!r}, but importing module "
            f"{module_name!r} failed with {type(error).__name__}: {error}. Check the "
            "preceding pip output and reinstall the feature from the application lock."
        ) from error
    probe_code = _clean_process_import_command(module_name)
    try:
        subprocess.run(
            probe_code,
            check=True,
            text=True,
            capture_output=True,
            timeout=_CLEAN_IMPORT_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        output = "\n".join(
            value.strip()
            for value in (error.stdout or "", error.stderr or "")
            if value.strip()
        )
        raise OptionalDependencyImportError(
            f"Installed optional package {package_name!r}, but a clean Python "
            f"process could not import {module_name!r} (exit code "
            f"{error.returncode}). Output: {output or 'no output'}"
        ) from error


def _install_with_resolver(
    packages: Sequence[str],
    index_args: Sequence[str],
) -> bool:
    if not packages:
        return False

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", *index_args, *packages],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "") + (exc.stdout or "")
        if platform.system() == "Windows" and any(m in stderr for m in _WINDOWS_DLL_LOCK_MARKERS):
            _log.warning(
                "pip install hit a locked DLL (another model loaded the same native library). "
                "Retrying with --no-deps to install the pure-Python portion..."
            )
            subprocess.run(
                [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", "--no-deps", *index_args, *packages],
                check=True,
                text=True,
            )
            # --no-deps skips transitive dependencies. Run a second pass that
            # asks pip to resolve and install only the missing sub-dependencies.
            # We use --no-deps on the main package again but explicitly install
            # its requirements via pip's dependency resolver on a dry-run parse.
            try:
                # pip install <pkg> --dry-run would be ideal but isn't available
                # on all pip versions. Instead, just try importing common deps
                # that are known to be needed by our optional groups.
                _KNOWN_TRANSITIVE_DEPS = ["requests", "tqdm", "pillow", "numpy"]
                missing_deps = []
                for dep in _KNOWN_TRANSITIVE_DEPS:
                    try:
                        __import__(dep.replace("-", "_"))
                    except ImportError:
                        missing_deps.append(dep)
                if missing_deps:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", *index_args, *missing_deps],
                        check=False,
                        text=True,
                        capture_output=True,
                    )
            except Exception:
                pass  # best effort
            importlib.invalidate_caches()
            return True
        else:
            raise
    importlib.invalidate_caches()
    return False


def _install_ort_consumers(
    package_specs: Sequence[str],
    provider: InstalledDistribution,
    index_args: Sequence[str],
) -> bool:
    dll_locked = False
    for package_spec in package_specs:
        package_name = _requirement_name(package_spec)
        if package_name is None:
            raise OptionalDependencyMetadataError(
                f"Invalid optional package requirement {package_spec!r}."
            )
        module_name = _ORT_CONSUMER_IMPORTS[package_name]
        _install_without_dependencies(package_spec, index_args)
        snapshot = capture_named_runtime_snapshot(
            (*ORT_DISTRIBUTION_NAMES, package_name)
        )
        current_provider = validated_onnxruntime_provider(snapshot)
        provider_changed = current_provider is None or (
            canonicalize_name(current_provider.name) != canonicalize_name(provider.name)
            or current_provider.version != provider.version
        )
        if provider_changed:
            current_description = (
                f"{current_provider.name} {current_provider.version}"
                if current_provider is not None
                else "none"
            )
            raise RuntimeDependencyError(
                f"ONNX Runtime provider changed while installing {package_spec}: "
                f"expected {provider.name} {provider.version}, found "
                f"{current_description}. Run the application runtime repair before "
                "preparing this feature again."
            )
        consumer = _installed_distribution(snapshot, package_name)
        locked_dependencies = _locked_non_ort_dependencies(
            consumer,
            current_provider,
            snapshot,
        )
        dll_locked = _install_with_resolver(
            locked_dependencies,
            index_args,
        ) or dll_locked
        _import_optional_package(package_name, module_name)
    return dll_locked


def install_packages(packages: Sequence[str]) -> bool:
    """Install packages via pip. Returns True if a DLL-lock fallback was used."""
    if not packages:
        return False
    _assert_safe_install_target(packages)

    # Use the fastest PyPI mirror (cached 30 min) so runtime installs
    # don't crawl on slow paths to pypi.org's Fastly CDN.
    index_args: list[str] = []
    try:
        from config import get_data_dir
        import mirror_selector
        selection = mirror_selector.select_pypi_index(data_dir=get_data_dir())
        if selection and selection.index_url:
            index_args = ["--index-url", selection.index_url, "--extra-index-url", "https://pypi.org/simple"]
    except Exception:
        pass  # fall back to default PyPI

    package_names = tuple(_requirement_name(package) for package in packages)
    ort_consumers = tuple(
        package
        for package, package_name in zip(packages, package_names)
        if package_name in _ORT_CONSUMER_IMPORTS
    )
    if not ort_consumers:
        return _install_with_resolver(packages, index_args)

    snapshot = capture_named_runtime_snapshot(ORT_DISTRIBUTION_NAMES)
    provider = validated_onnxruntime_provider(snapshot)
    non_cpu_provider = provider is not None and canonicalize_name(provider.name) in {
        "onnxruntime-gpu",
        "onnxruntime-directml",
    }
    if not non_cpu_provider:
        return _install_with_resolver(packages, index_args)

    ordinary_packages = tuple(
        package
        for package, package_name in zip(packages, package_names)
        if package_name not in _ORT_CONSUMER_IMPORTS
    )
    dll_locked = _install_with_resolver(ordinary_packages, index_args)
    ort_consumer_dll_locked = _install_ort_consumers(
        ort_consumers,
        provider,
        index_args,
    )
    return dll_locked or ort_consumer_dll_locked


def _ensure_tipo_group() -> DependencyInstallResult:
    """Install the CPU llama.cpp wheel first, then tipo-kgen from PyPI.

    llama-cpp-python on PyPI is an sdist. Compiling it is refused; the official
    extra-index CPU wheels cover every platform this app ships. The wheel is
    installed before torch/tipo-kgen so a missing wheel fails closed without
    a multi-GB download, and so tipo-kgen cannot pull the sdist as a dep.
    """
    from llama_cpp_wheel import (
        LLAMA_CPP_SPEC,
        UnsupportedLlamaCppWheelError,
        install_cpu_wheel,
        require_cpu_wheel_platform,
    )

    try:
        require_cpu_wheel_platform()
    except UnsupportedLlamaCppWheelError as exc:
        raise UnsupportedOptionalDependencyError(str(exc)) from exc

    # Wheel first: fail closed before downloading torch, and so a later
    # tipo-kgen install cannot pull the PyPI llama-cpp-python sdist.
    llama_packages: list[str] = []
    if _needs_install("llama_cpp", LLAMA_CPP_SPEC):
        _assert_safe_install_target((LLAMA_CPP_SPEC,))
        try:
            install_cpu_wheel()
        except subprocess.CalledProcessError as exc:
            output = "\n".join(
                value.strip()
                for value in (exc.stdout or "", exc.stderr or "")
                if value.strip()
            )
            raise OptionalDependencyImportError(
                "Could not install a prebuilt llama-cpp-python CPU wheel "
                f"({LLAMA_CPP_SPEC}). pip output: {output or 'no output'}. "
                "This app does not compile llama.cpp from source."
            ) from exc
        llama_packages.append(LLAMA_CPP_SPEC)
        _import_optional_package("llama-cpp-python", "llama_cpp")

    packages = OPTIONAL_DEPENDENCY_GROUPS["tipo"]
    imports = GROUP_IMPORTS["tipo"]
    packages_to_install: list[str] = []
    modules_to_verify: list[str] = []
    for module_name, package in zip(imports, packages):
        locked_package = _lock_package_spec(package)
        if _needs_install(module_name, locked_package) and locked_package not in packages_to_install:
            packages_to_install.append(locked_package)
            modules_to_verify.append(module_name)
    # llama_cpp is deliberately absent: _import_optional_package above already
    # proved it imports both here and in a clean interpreter.
    preloaded_modules = tuple(name for name in modules_to_verify if name in sys.modules)
    dll_locked = install_packages(packages_to_install)

    installed = tuple(llama_packages + packages_to_install)
    if not installed and not dll_locked:
        return DependencyInstallResult(installed_packages=())

    restart_reason = _restart_reason_after_install(
        modules_to_verify=tuple(modules_to_verify),
        preloaded_modules=preloaded_modules,
        dll_locked=dll_locked,
    )
    return DependencyInstallResult(
        installed_packages=installed,
        restart_recommended=bool(restart_reason),
        restart_reason=restart_reason,
    )


def ensure_imports(module_names: Iterable[str]) -> DependencyInstallResult:
    packages = []
    modules_to_verify = []
    for module_name in module_names:
        package_spec = IMPORT_TO_PACKAGE_HINT.get(module_name, module_name)
        locked_package = _lock_package_spec(package_spec)
        if _needs_install(module_name, locked_package) and locked_package not in packages:
            packages.append(locked_package)
            modules_to_verify.append(module_name)
    preloaded_modules = tuple(name for name in modules_to_verify if name in sys.modules)
    dll_locked = install_packages(packages)
    if not packages and not dll_locked:
        return DependencyInstallResult(installed_packages=())

    restart_reason = _restart_reason_after_install(
        modules_to_verify=tuple(modules_to_verify),
        preloaded_modules=preloaded_modules,
        dll_locked=dll_locked,
    )
    return DependencyInstallResult(
        installed_packages=tuple(packages),
        restart_recommended=bool(restart_reason),
        restart_reason=restart_reason,
    )


def ensure_group(group: str) -> DependencyInstallResult:
    _validate_optional_group_platform(group)
    if group == "tipo":
        return _ensure_tipo_group()
    packages = OPTIONAL_DEPENDENCY_GROUPS.get(group)
    imports = GROUP_IMPORTS.get(group)
    if not packages or imports is None:
        raise ValueError(f"Unknown optional dependency group: {group}")

    packages_to_install = []
    modules_to_verify = []
    for module_name, package in zip(imports, packages):
        locked_package = _lock_package_spec(package)
        if _needs_install(module_name, locked_package) and locked_package not in packages_to_install:
            packages_to_install.append(locked_package)
            modules_to_verify.append(module_name)

    # Captured before pip runs: once a module is in sys.modules, replacing the
    # files under it cannot affect this process.
    preloaded_modules = tuple(name for name in modules_to_verify if name in sys.modules)

    dll_locked = install_packages(packages_to_install)
    if not packages_to_install and not dll_locked:
        return DependencyInstallResult(installed_packages=())

    restart_reason = _restart_reason_after_install(
        modules_to_verify=tuple(modules_to_verify),
        preloaded_modules=preloaded_modules,
        dll_locked=dll_locked,
    )
    return DependencyInstallResult(
        installed_packages=tuple(packages_to_install),
        restart_recommended=bool(restart_reason),
        restart_reason=restart_reason,
    )


import logging as _logging

_dep_logger = _logging.getLogger("sd-image-sorter.deps")


def ensure_group_with_soft_deps(group: str) -> DependencyInstallResult:
    """Install core group deps, then best-effort install soft deps (triton etc.).

    Soft deps failing does NOT block the core install or raise an error.
    """
    result = ensure_group(group)
    soft_entries = SOFT_DEPENDENCY_GROUPS.get(group)
    if not soft_entries:
        return result

    soft_installed: list[str] = []
    soft_restart_reason = ""
    for module_name, package_spec in soft_entries:
        locked_package = _lock_package_spec(package_spec)
        if not _needs_install(module_name, locked_package):
            continue
        preloaded = (module_name,) if module_name in sys.modules else ()
        try:
            dll_locked = install_packages([locked_package])
            soft_installed.append(locked_package)
            soft_restart_reason = soft_restart_reason or _restart_reason_after_install(
                modules_to_verify=(module_name,),
                preloaded_modules=preloaded,
                dll_locked=dll_locked,
            )
        except Exception as exc:
            _dep_logger.warning(
                "Optional package %s could not be installed (non-fatal): %s",
                package_spec,
                exc,
            )

    all_installed = list(result.installed_packages) + soft_installed
    restart_reason = result.restart_reason or soft_restart_reason
    return DependencyInstallResult(
        installed_packages=tuple(all_installed),
        restart_recommended=bool(restart_reason),
        restart_reason=restart_reason,
    )
