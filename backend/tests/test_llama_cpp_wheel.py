"""llama-cpp-python is installed from official CPU wheels, never compiled."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import llama_cpp_wheel
import optional_dependencies


@pytest.mark.parametrize(
    ("system", "machine", "supported"),
    (
        ("Windows", "AMD64", True),
        ("Windows", "x86_64", True),
        ("Windows", "ARM64", False),
        ("Linux", "x86_64", True),
        ("Linux", "aarch64", True),
        ("Linux", "arm64", True),
        ("Linux", "i686", False),
        ("Darwin", "arm64", True),
        ("Darwin", "x86_64", False),
    ),
)
def test_cpu_wheel_platform_matrix(system, machine, supported):
    assert llama_cpp_wheel.cpu_wheel_supported(system, machine) is supported


def test_tipo_optional_group_never_installs_llama_cpp_from_pypi():
    """PyPI llama-cpp-python is an sdist. Public machines have no compiler."""
    assert all(
        "llama-cpp-python" not in spec
        for spec in optional_dependencies.OPTIONAL_DEPENDENCY_GROUPS["tipo"]
    )
    assert "llama_cpp" not in optional_dependencies.GROUP_IMPORTS["tipo"]
    assert optional_dependencies.IMPORT_TO_PACKAGE_HINT["llama_cpp"].startswith(
        "llama-cpp-python"
    )


def test_unsupported_platform_refuses_to_compile():
    with pytest.raises(llama_cpp_wheel.UnsupportedLlamaCppWheelError, match="will not compile"):
        llama_cpp_wheel.require_cpu_wheel_platform("Windows", "ARM64")


def test_pip_args_cannot_fall_back_to_a_pypi_sdist():
    args = llama_cpp_wheel.build_pip_install_args()

    assert llama_cpp_wheel.ONLY_BINARY in args
    assert llama_cpp_wheel.CPU_WHEEL_INDEX in args
    extra = args[args.index("--extra-index-url") + 1]
    assert extra == llama_cpp_wheel.CPU_WHEEL_INDEX
    assert llama_cpp_wheel.LLAMA_CPP_SPEC in args
    joined = " ".join(args)
    assert "CMAKE_ARGS" not in joined
    assert "--no-binary" not in joined


def test_install_cpu_wheel_invokes_the_prebuilt_pip_command(monkeypatch):
    recorded = []

    def fake_run(command, **kwargs):
        recorded.append(command)
        return None

    monkeypatch.setattr(llama_cpp_wheel, "require_cpu_wheel_platform", lambda: None)
    llama_cpp_wheel.install_cpu_wheel(runner=fake_run)

    assert len(recorded) == 1
    command = recorded[0]
    assert command[:5] == [
        sys.executable,
        "-m",
        "pip",
        "--disable-pip-version-check",
        "install",
    ]
    assert llama_cpp_wheel.build_pip_install_args() == command[5:]


def test_ensure_tipo_group_installs_llama_cpp_from_the_cpu_index(monkeypatch):
    pip_calls = []
    wheel_calls = []

    monkeypatch.setattr(optional_dependencies, "_needs_install", lambda *_args: True)
    monkeypatch.setattr(optional_dependencies, "_assert_safe_install_target", lambda *_args: None)
    monkeypatch.setattr(
        optional_dependencies,
        "install_packages",
        lambda packages: pip_calls.append(list(packages)) or False,
    )
    monkeypatch.setattr(
        optional_dependencies,
        "_import_optional_package",
        lambda *_args: None,
    )

    def fake_install_cpu_wheel(**_kwargs):
        wheel_calls.append(llama_cpp_wheel.build_pip_install_args())

    monkeypatch.setattr("llama_cpp_wheel.install_cpu_wheel", fake_install_cpu_wheel)
    monkeypatch.setattr("llama_cpp_wheel.require_cpu_wheel_platform", lambda: None)

    result = optional_dependencies.ensure_group("tipo")

    assert pip_calls, "tipo-kgen / torch lock must still go through install_packages"
    assert "tipo-kgen>=0.3.1" in pip_calls[0] or any(
        item.startswith("tipo-kgen") for row in pip_calls for item in row
    )
    assert not any(
        "llama-cpp-python" in item for row in pip_calls for item in row
    ), "llama-cpp-python must not be installed from PyPI (that is an sdist)"
    assert wheel_calls == [llama_cpp_wheel.build_pip_install_args()]
    assert llama_cpp_wheel.LLAMA_CPP_SPEC in result.installed_packages
    assert result.restart_recommended is True


def test_ensure_tipo_group_installs_the_cpu_wheel_before_torch(monkeypatch):
    order = []

    monkeypatch.setattr(optional_dependencies, "_needs_install", lambda *_args: True)
    monkeypatch.setattr(optional_dependencies, "_assert_safe_install_target", lambda *_args: None)
    monkeypatch.setattr(optional_dependencies, "_import_optional_package", lambda *_args: None)
    monkeypatch.setattr(
        optional_dependencies,
        "install_packages",
        lambda packages: order.append("pypi") or False,
    )
    monkeypatch.setattr("llama_cpp_wheel.require_cpu_wheel_platform", lambda: None)

    def fake_install_cpu_wheel(**_kwargs):
        order.append("wheel")

    monkeypatch.setattr("llama_cpp_wheel.install_cpu_wheel", fake_install_cpu_wheel)

    optional_dependencies.ensure_group("tipo")

    assert order == ["wheel", "pypi"], (
        "the CPU wheel must fail closed before the torch download"
    )


def test_ensure_tipo_group_rejects_hosts_without_a_cpu_wheel(monkeypatch):
    monkeypatch.setattr(llama_cpp_wheel.platform, "system", lambda: "Windows")
    monkeypatch.setattr(llama_cpp_wheel.platform, "machine", lambda: "ARM64")

    with pytest.raises(
        optional_dependencies.UnsupportedOptionalDependencyError,
        match="will not compile",
    ):
        optional_dependencies.ensure_group("tipo")
