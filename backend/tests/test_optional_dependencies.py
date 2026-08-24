from __future__ import annotations

import importlib.machinery
import importlib.metadata as importlib_metadata
import platform
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

import optional_dependencies


def test_requirement_lock_map_uses_release_pins():
    optional_dependencies._REQUIREMENTS_CACHE = None

    lock_map = optional_dependencies._load_requirement_version_map()

    # The universal lock keeps the security-supported Torch pair current.
    # Intel Mac and pre-14 Apple Silicon are rejected before optional install.
    # OpenCV retains its separate legacy macOS wheel compatibility markers.
    expected_torch = "torch==2.13.0"
    expected_opencv = (
        "opencv-python==4.10.0.84"
        if sys.platform == "darwin" and platform.machine() == "arm64"
        else "opencv-python==4.9.0.80"
        if sys.platform == "darwin"
        else "opencv-python==4.11.0.86"
    )

    assert lock_map["transformers"] == "transformers==5.6.2"
    assert lock_map["fastembed"] == "fastembed==0.8.0"
    assert lock_map["torch"] == expected_torch
    assert lock_map["opencv_python"] == expected_opencv
    assert optional_dependencies._lock_package_spec("transformers>=5.6.0") == "transformers==5.6.2"
    assert optional_dependencies._lock_package_spec("torch>=2.0.0") == expected_torch


def test_torch_lock_excludes_vulnerable_macos_legacy_pins():
    requirements_path = Path(__file__).resolve().parents[1] / "requirements.in"
    requirements_text = requirements_path.read_text(encoding="utf-8")

    assert "torch==2.2.2" not in requirements_text
    assert "torch==2.10.0" not in requirements_text
    assert "torchvision==0.17.2" not in requirements_text
    assert "torchvision==0.25.0" not in requirements_text
    assert (
        'torch==2.13.0; sys_platform != "darwin" or platform_machine == "arm64"'
        in requirements_text
    )


@pytest.mark.parametrize(
    "group",
    ("aesthetic", "artist", "sam3", "toriigate", "yolo", "tipo"),
)
def test_torch_group_rejects_intel_macos_before_install(monkeypatch, group):
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(optional_dependencies.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        optional_dependencies,
        "_needs_install",
        lambda module_name, package_spec: False,
    )

    with pytest.raises(
        optional_dependencies.UnsupportedOptionalDependencyError,
        match="Intel Mac|CUDA-only",
    ):
        optional_dependencies.ensure_group(group)


def test_torch_group_rejects_pre_14_macos_before_install(monkeypatch):
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(optional_dependencies.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        optional_dependencies.platform,
        "mac_ver",
        lambda: ("13.6.9", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(
        optional_dependencies,
        "_needs_install",
        lambda module_name, package_spec: False,
    )

    with pytest.raises(
        optional_dependencies.UnsupportedOptionalDependencyError,
        match="macOS 14",
    ):
        optional_dependencies.ensure_group("toriigate")


def test_torch_group_allows_macos_14_apple_silicon(monkeypatch):
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(optional_dependencies.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        optional_dependencies.platform,
        "mac_ver",
        lambda: ("14.7.1", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(
        optional_dependencies,
        "_needs_install",
        lambda module_name, package_spec: False,
    )

    result = optional_dependencies.ensure_group("aesthetic")

    assert result == optional_dependencies.DependencyInstallResult(
        installed_packages=(),
    )

def test_sam3_group_rejects_macos_14_apple_silicon(monkeypatch):
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(optional_dependencies.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        optional_dependencies.platform,
        "mac_ver",
        lambda: ("14.7.1", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(
        optional_dependencies,
        "_needs_install",
        lambda module_name, package_spec: False,
    )

    with pytest.raises(
        optional_dependencies.UnsupportedOptionalDependencyError,
        match="CUDA-only",
    ):
        optional_dependencies.ensure_group("sam3")


def _fake_install(installed_list):
    """Return a mock install_packages that records calls and returns False (no DLL lock)."""
    def _mock(packages):
        installed_list.extend(packages)
        return False
    return _mock


def _release_locked_version(package_name: str) -> str:
    normalized = optional_dependencies._normalize_package_name(package_name)
    locked_spec = optional_dependencies._load_requirement_version_map()[normalized]
    operator, separator, version = locked_spec.partition("==")
    if not separator or not operator or not version:
        raise AssertionError(f"Expected an exact release lock for {package_name}: {locked_spec}")
    return version

@pytest.mark.parametrize(
    ("installed_version", "package_spec", "expected"),
    (
        ("2.13.0+cu130", "torch==2.13.0", True),
        ("2.13.0.post1", "torch==2.13.0", False),
        ("2.13.0rc1", "torch==2.13.0", False),
        ("2.13.1", "torch>=2.13.0", True),
    ),
)
def test_installed_version_satisfies_uses_pep440(
    monkeypatch,
    installed_version,
    package_spec,
    expected,
):
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package_name: installed_version,
    )

    assert optional_dependencies._installed_version_satisfies(package_spec) is expected


def test_installed_version_satisfies_rejects_invalid_installed_version(monkeypatch):
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package_name: "not-a-version",
    )

    with pytest.raises(RuntimeError, match="invalid installed version"):
        optional_dependencies._installed_version_satisfies("torch==2.13.0")



def test_ensure_group_installs_missing_or_too_old_packages(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Windows")
    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package: "5.5.0" if package == "transformers" else _release_locked_version(package),
    )
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("sam3")

    assert installed == ["transformers==5.6.2"]
    assert result.installed_packages == ("transformers==5.6.2",)
    assert result.restart_recommended is True

def test_ensure_group_upgrades_torch_to_release_lock(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package: {
            "torch": "2.10.0",
            "open-clip-torch": "3.3.0",
        }[package],
    )
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("aesthetic")

    assert installed == ["torch==2.13.0"]
    assert result.installed_packages == ("torch==2.13.0",)

def test_yolo_group_upgrades_transitive_torch_to_release_lock(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Windows")
    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setitem(sys.modules, "ultralytics", type(sys)("ultralytics"))
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package: (
            "2.10.0"
            if package == "torch"
            else _release_locked_version(package)
        ),
    )
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("yolo")

    assert installed == ["torch==2.13.0"]
    assert result.installed_packages == ("torch==2.13.0",)




def test_toriigate_requires_transformers_version_with_qwen35_support(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        optional_dependencies.importlib.metadata,
        "version",
        lambda package: "5.5.0" if package == "transformers" else _release_locked_version(package),
    )
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("toriigate")

    assert installed == ["transformers==5.6.2"]
    assert result.installed_packages == ("transformers==5.6.2",)
    assert result.restart_recommended is True


def test_ensure_group_skips_already_satisfied_packages(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setattr(optional_dependencies.importlib.metadata, "version", _release_locked_version)
    monkeypatch.setattr(optional_dependencies.platform, "system", lambda: "Windows")
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("aesthetic")

    assert installed == []
    assert result.installed_packages == ()
    assert result.restart_recommended is False


def test_translation_group_installs_translators_runtime(monkeypatch):
    installed = []

    monkeypatch.setattr(optional_dependencies.importlib.util, "find_spec", lambda module: None if module == "translators" else object())
    monkeypatch.setattr(optional_dependencies, "install_packages", _fake_install(installed))

    result = optional_dependencies.ensure_group("translation")

    assert installed == ["translators==6.0.4"]
    assert result.installed_packages == ("translators==6.0.4",)
    assert result.restart_recommended is True


def test_install_packages_refuses_system_python_without_opt_in(monkeypatch):
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: False)
    monkeypatch.delenv("SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL", raising=False)

    calls = []
    monkeypatch.setattr(optional_dependencies.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    try:
        optional_dependencies.install_packages(["torch>=2.0.0"])
    except optional_dependencies.UnsafeDependencyInstallError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected UnsafeDependencyInstallError")

    assert "system Python environment" in message
    assert "run-portable.bat" in message
    assert "torch>=2.0.0" in message
    assert calls == []


def test_install_packages_allows_virtualenv(monkeypatch):
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: True)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(optional_dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(optional_dependencies.importlib, "invalidate_caches", lambda: None)

    optional_dependencies.install_packages(["fastembed>=0.4.0"])

    assert calls
    assert "fastembed>=0.4.0" in calls[0][0]

def test_install_packages_allows_portable_python(monkeypatch, tmp_path):
    package_root = tmp_path / "app"
    backend_dir = package_root / "backend"
    backend_dir.mkdir(parents=True)
    portable_python = package_root / "python" / "python.exe"
    portable_python.parent.mkdir(parents=True)
    portable_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(optional_dependencies, "__file__", str(backend_dir / "optional_dependencies.py"))
    monkeypatch.setattr(optional_dependencies.sys, "executable", str(portable_python))
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: False)
    monkeypatch.delenv("SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL", raising=False)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(optional_dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(optional_dependencies.importlib, "invalidate_caches", lambda: None)

    optional_dependencies.install_packages(["fastembed>=0.4.0"])

    assert calls
    assert "fastembed>=0.4.0" in calls[0][0]


def _write_test_distribution(
    site_packages: Path,
    name: str,
    version: str,
    requirements: tuple[str, ...],
) -> None:
    dist_info_name = canonicalize_name(name).replace("-", "_")
    dist_info = site_packages / f"{dist_info_name}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    metadata_lines = [
        "Metadata-Version: 2.3",
        f"Name: {name}",
        f"Version: {version}",
        *(f"Requires-Dist: {requirement}" for requirement in requirements),
        "",
    ]
    (dist_info / "METADATA").write_text("\n".join(metadata_lines), encoding="utf-8")


def _load_test_distributions(site_packages: Path) -> tuple[importlib_metadata.Distribution, ...]:
    return tuple(importlib_metadata.distributions(path=[str(site_packages)]))


def _patch_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
    distributions: tuple[importlib_metadata.Distribution, ...],
) -> None:
    distributions_by_name = {
        canonicalize_name(distribution.metadata["Name"]): distribution
        for distribution in distributions
    }

    def get_distribution(package_name: str) -> importlib_metadata.Distribution:
        distribution = distributions_by_name.get(canonicalize_name(package_name))
        if distribution is None:
            raise importlib_metadata.PackageNotFoundError(package_name)
        return distribution

    packages_to_distributions: dict[str, list[str]] = {}
    for distribution in distributions:
        distribution_name = distribution.metadata["Name"]
        module_name = (
            "onnxruntime"
            if canonicalize_name(distribution_name).startswith("onnxruntime")
            else canonicalize_name(distribution_name).replace("-", "_")
        )
        packages_to_distributions.setdefault(module_name, []).append(distribution_name)

    def iter_distributions(
        **kwargs: str,
    ) -> Iterator[importlib_metadata.Distribution]:
        requested_name = kwargs.get("name")
        if requested_name is None:
            return iter(distributions)
        canonical_name = canonicalize_name(requested_name)
        return iter(
            distribution
            for distribution in distributions
            if canonicalize_name(distribution.metadata["Name"]) == canonical_name
        )

    monkeypatch.setattr(importlib_metadata, "distributions", iter_distributions)
    monkeypatch.setattr(importlib_metadata, "distribution", get_distribution)
    monkeypatch.setattr(
        importlib_metadata,
        "metadata",
        lambda package_name: get_distribution(package_name).metadata,
    )
    monkeypatch.setattr(
        importlib_metadata,
        "requires",
        lambda package_name: get_distribution(package_name).requires,
    )
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        lambda package_name: get_distribution(package_name).version,
    )
    monkeypatch.setattr(
        importlib_metadata,
        "packages_distributions",
        lambda: packages_to_distributions,
    )


def _record_pip_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(optional_dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(optional_dependencies.importlib, "invalidate_caches", lambda: None)
    return calls


def _command_requirements(command: tuple[str, ...]) -> tuple[str, ...]:
    if "install" not in command:
        return ()
    install_index = command.index("install")
    requirements: list[str] = []
    for token in command[install_index + 1 :]:
        try:
            Requirement(token)
        except InvalidRequirement:
            continue
        requirements.append(token)
    return tuple(requirements)


@pytest.mark.parametrize(
    (
        "package_spec",
        "module_name",
        "provider_name",
        "metadata_requirements",
        "expected_lock_names",
        "inactive_lock_name",
    ),
    (
        (
            "fastembed==0.8.0",
            "fastembed",
            "onnxruntime-gpu",
            (
                "numpy>=1.26; python_version >= '3'",
                "onnxruntime>=1.17.0,!=1.20.0",
                "pillow>=10.3.0,<13.0",
                "tokenizers>=0.15,<1.0",
                "requests>=2.31; python_version < '3'",
            ),
            ("numpy", "pillow", "tokenizers"),
            "requests",
        ),
        (
            "nudenet==3.4.2",
            "nudenet",
            "onnxruntime-directml",
            (
                "numpy",
                "onnxruntime",
                "opencv-python-headless",
                "tqdm; python_version < '3'",
            ),
            ("numpy", "opencv-python-headless"),
            "tqdm",
        ),
    ),
    ids=("fastembed-gpu", "nudenet-directml"),
)
def test_ort_consumers_preserve_existing_provider_and_install_locked_metadata_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    package_spec: str,
    module_name: str,
    provider_name: str,
    metadata_requirements: tuple[str, ...],
    expected_lock_names: tuple[str, ...],
    inactive_lock_name: str,
):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    package_version = package_spec.split("==", 1)[1]
    provider_version = "1.21.0"
    _write_test_distribution(
        site_packages,
        module_name,
        package_version,
        metadata_requirements,
    )
    _write_test_distribution(site_packages, provider_name, provider_version, ())
    unrelated_metadata = site_packages / "unrelated_broken-1.0.0.dist-info"
    unrelated_metadata.mkdir()
    (unrelated_metadata / "METADATA").write_text(
        "Metadata-Version: 2.3\nName: unrelated-broken\n",
        encoding="utf-8",
    )
    module_path = site_packages / module_name
    module_path.mkdir()
    (module_path / "__init__.py").write_text(
        "METADATA_INSTALL_IMPORT_SUCCEEDED = True\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(site_packages))
    _patch_distribution_metadata(
        monkeypatch,
        _load_test_distributions(site_packages),
    )
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: True)
    optional_dependencies._REQUIREMENTS_CACHE = None
    calls = _record_pip_calls(monkeypatch)

    onnxruntime_module = types.ModuleType("onnxruntime")
    onnxruntime_module.__version__ = provider_version
    onnxruntime_module.__spec__ = importlib.machinery.ModuleSpec(
        "onnxruntime",
        loader=None,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime_module)
    previous_module = sys.modules.pop(module_name, None)
    try:
        optional_dependencies.install_packages([package_spec])

        assert calls
        main_command = calls[0]
        assert "--no-deps" in main_command
        assert _command_requirements(main_command) == (package_spec,)

        lock_map = optional_dependencies._load_requirement_version_map()
        expected_locked_requirements = {
            lock_map[optional_dependencies._normalize_package_name(package_name)]
            for package_name in expected_lock_names
        }
        installed_dependency_requirements = {
            requirement
            for command in calls[1:]
            for requirement in _command_requirements(command)
        }
        assert installed_dependency_requirements == expected_locked_requirements
        assert lock_map[optional_dependencies._normalize_package_name(inactive_lock_name)] not in (
            installed_dependency_requirements
        )
        for command in calls:
            for requirement_text in _command_requirements(command):
                requirement_name = canonicalize_name(Requirement(requirement_text).name)
                assert requirement_name != "onnxruntime"
        assert module_name in sys.modules
        assert sys.modules[module_name].METADATA_INSTALL_IMPORT_SUCCEEDED is True
    finally:
        sys.modules.pop(module_name, None)
        if previous_module is not None:
            sys.modules[module_name] = previous_module


@pytest.mark.parametrize(
    "cpu_provider_name",
    (None, "onnxruntime"),
    ids=("no-provider", "cpu-provider"),
)
def test_ort_consumers_keep_normal_resolver_without_gpu_or_directml_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cpu_provider_name: str | None,
):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    if cpu_provider_name is not None:
        _write_test_distribution(site_packages, cpu_provider_name, "1.21.0", ())
        # The provider-vs-module version consistency check compares this
        # fabricated metadata against the IMPORTED onnxruntime module.
        # Inject a matching fake module (the reject-test pattern below)
        # instead of leaning on whatever real onnxruntime the machine
        # has - on core-deps CI the real one is a different version and
        # this test would fail for the wrong reason.
        onnxruntime_module = types.ModuleType("onnxruntime")
        onnxruntime_module.__version__ = "1.21.0"
        onnxruntime_module.__spec__ = importlib.machinery.ModuleSpec(
            "onnxruntime",
            loader=None,
        )
        monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime_module)
    _patch_distribution_metadata(
        monkeypatch,
        _load_test_distributions(site_packages),
    )
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: True)
    calls = _record_pip_calls(monkeypatch)

    optional_dependencies.install_packages(["fastembed==0.8.0"])

    assert len(calls) == 1
    assert "--no-deps" not in calls[0]
    assert _command_requirements(calls[0]) == ("fastembed==0.8.0",)


def test_ort_consumer_rejects_provider_below_installed_metadata_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    _write_test_distribution(
        site_packages,
        "fastembed",
        "0.8.0",
        ("onnxruntime>=1.22",),
    )
    _write_test_distribution(
        site_packages,
        "onnxruntime-gpu",
        "1.21.0",
        (),
    )
    module_path = site_packages / "fastembed"
    module_path.mkdir()
    (module_path / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(site_packages))
    _patch_distribution_metadata(
        monkeypatch,
        _load_test_distributions(site_packages),
    )
    monkeypatch.setattr(optional_dependencies, "_running_in_virtualenv", lambda: True)
    calls = _record_pip_calls(monkeypatch)

    onnxruntime_module = types.ModuleType("onnxruntime")
    onnxruntime_module.__version__ = "1.21.0"
    onnxruntime_module.__spec__ = importlib.machinery.ModuleSpec(
        "onnxruntime",
        loader=None,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime_module)

    with pytest.raises(optional_dependencies.RuntimeDependencyError) as error:
        optional_dependencies.install_packages(["fastembed==0.8.0"])

    message = str(error.value)
    assert "onnxruntime>=1.22" in message
    assert "1.21.0" in message
    assert len(calls) == 1
    assert "--no-deps" in calls[0]


def test_import_verification_probes_clean_process_when_module_is_cached(
    monkeypatch: pytest.MonkeyPatch,
):
    cached_module = types.ModuleType("fastembed")
    cached_module.__spec__ = importlib.machinery.ModuleSpec(
        "fastembed",
        loader=None,
    )
    monkeypatch.setitem(sys.modules, "fastembed", cached_module)
    calls = _record_pip_calls(monkeypatch)

    optional_dependencies._import_optional_package("fastembed", "fastembed")

    assert len(calls) == 1
    command = calls[0]
    assert command[:2] == (sys.executable, "-c")
    assert "importlib.import_module('fastembed')" in command[2]
