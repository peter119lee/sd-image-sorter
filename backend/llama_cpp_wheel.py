"""Install llama-cpp-python from the official prebuilt CPU extra-index.

PyPI publishes llama-cpp-python as an sdist that compiles llama.cpp. That is
not a public-release path: Windows portable users have no MSVC, and Linux
portable users are not required to have a compiler. The first-party CPU
wheel index ships ``py3-none`` wheels (any CPython 3, including Windows
portable 3.12.8 and Linux portable 3.13) for the platforms this app ships:

* Windows amd64
* Linux x86_64 and aarch64 (manylinux2014)
* macOS arm64

Intel Mac, 32-bit Windows, Windows ARM, and other tags have no CPU wheel.
Those platforms fail closed instead of attempting a source build.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from collections.abc import Callable

CPU_WHEEL_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
LLAMA_CPP_SPEC = "llama-cpp-python>=0.3.24"
ONLY_BINARY = "--only-binary=llama-cpp-python"


class UnsupportedLlamaCppWheelError(RuntimeError):
    """No prebuilt CPU wheel exists for this OS/CPU; compiling is refused."""


def cpu_wheel_supported(
    system: str | None = None,
    machine: str | None = None,
) -> bool:
    """Whether the official CPU extra-index has a wheel for this host."""
    system = system or platform.system()
    machine = (machine or platform.machine() or "").lower().replace("-", "_")
    if system == "Windows":
        return machine in {"amd64", "x86_64"}
    if system == "Linux":
        return machine in {"x86_64", "amd64", "aarch64", "arm64"}
    if system == "Darwin":
        return machine == "arm64"
    return False


def require_cpu_wheel_platform(
    system: str | None = None,
    machine: str | None = None,
) -> None:
    system = system or platform.system()
    machine = machine or platform.machine() or "unknown"
    if cpu_wheel_supported(system, machine):
        return
    raise UnsupportedLlamaCppWheelError(
        "TIPO needs a prebuilt llama.cpp CPU wheel. None is published for "
        f"{system} {machine}. Supported hosts: Windows x86_64, Linux x86_64, "
        "Linux aarch64, and macOS Apple Silicon. This app will not compile "
        "llama-cpp-python from source."
    )


def build_pip_install_args() -> list[str]:
    """Flags that install a wheel and cannot fall back to a source build."""
    return [
        ONLY_BINARY,
        "--extra-index-url",
        CPU_WHEEL_INDEX,
        LLAMA_CPP_SPEC,
    ]


def install_cpu_wheel(*, runner: Callable[..., object] | None = None) -> None:
    """pip-install the CPU wheel into the current interpreter.

    ``runner`` is the subprocess.run seam tests patch; production passes
    nothing and uses ``subprocess.run``.
    """
    require_cpu_wheel_platform()
    run = runner or subprocess.run
    command = [
        sys.executable,
        "-m",
        "pip",
        "--disable-pip-version-check",
        "install",
        *build_pip_install_args(),
    ]
    run(command, check=True, text=True, capture_output=True)
