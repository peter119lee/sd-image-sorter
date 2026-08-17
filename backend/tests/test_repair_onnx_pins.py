"""Characterization pins for ``repair_onnxruntime.py`` (tier-2 step 0).

Companion to ``tests/test_repair_onnxruntime.py`` (24 tests, ~71 monkeypatch
hits) which exercises the Windows/Linux repair *flows* end-to-end but never
touches: the real ``get_install_state`` computation, the pure detection /
version-resolution / constraint-sanitization helpers, the ``main()`` argument
+ exit-code contract, the platform-dispatch guards, or the load-bearing
architectural invariants (stdlib-only imports, the launcher filename contract,
the ``_detect_gpu_vendor`` cross-module seam consumed by
``repair_torch_runtime``).

These are CHARACTERIZATION pins: they lock in what the code does *today* so a
later verbatim split / refactor cannot silently change behaviour. Where a pin
documents a dormant edge it pins the current behaviour AS-IS and says so in
the test docstring — the pin is not an assertion that the behaviour is
desirable.

Hermetic: every subprocess / pip / metadata / probe seam is stubbed; no real
installs, no real network, no real nvidia-smi. Only temp files created here are
unlinked here.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

import repair_onnxruntime


REPO_ROOT = Path(repair_onnxruntime.__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Group A — version resolution + install-spec assembly
# ---------------------------------------------------------------------------


def test_locked_runtime_version_reads_requirements_pin():
    """``_locked_runtime_version`` parses the real requirements.txt pin.

    onnxruntime-gpu is pinned there; onnxruntime-directml is intentionally
    absent (it is selected dynamically for AMD/Intel Windows and would
    conflict with NVIDIA users if pinned in the shared lockfile — see the
    module's DEFAULT_RUNTIME_VERSION_BY_DIST comment). A dist that is not a
    requirement at all resolves to None.
    """
    gpu = repair_onnxruntime._locked_runtime_version("onnxruntime-gpu")
    assert gpu is not None
    assert re.match(r"^\d+\.\d+", gpu), gpu

    # Dynamic-selection package + a definitely-absent name both miss the lock.
    assert repair_onnxruntime._locked_runtime_version("onnxruntime-directml") is None
    assert repair_onnxruntime._locked_runtime_version("totally-not-a-real-pkg") is None


def test_default_runtime_version_by_dist_pins_directml():
    """The in-code fallback map is the sole version source for directml."""
    assert repair_onnxruntime.DEFAULT_RUNTIME_VERSION_BY_DIST == {
        "onnxruntime-directml": "1.21.0",
    }


def test_release_runtime_version_precedence(monkeypatch):
    """locked pin > DEFAULT map > installed_version fallback > None."""
    # 1. locked pin wins over everything.
    monkeypatch.setattr(
        repair_onnxruntime, "_locked_runtime_version", lambda name: "5.5.5"
    )
    assert (
        repair_onnxruntime._release_runtime_version("onnxruntime-directml", "9.9.9")
        == "5.5.5"
    )

    # 2. no lock -> DEFAULT map (directml) wins over installed_version.
    monkeypatch.setattr(
        repair_onnxruntime, "_locked_runtime_version", lambda name: None
    )
    assert (
        repair_onnxruntime._release_runtime_version("onnxruntime-directml", "9.9.9")
        == "1.21.0"
    )

    # 3. no lock, not in DEFAULT map -> installed_version fallback.
    assert repair_onnxruntime._release_runtime_version("some-pkg", "9.9.9") == "9.9.9"

    # 4. nothing anywhere -> None.
    assert repair_onnxruntime._release_runtime_version("some-pkg", None) is None


def test_runtime_install_spec_formats_name_extras_version(monkeypatch):
    """``_runtime_install_spec`` assembles ``name[extras]==version``.

    Version resolution is delegated to ``_release_runtime_version``; this pin
    isolates the string-assembly contract by stubbing that seam.
    """
    monkeypatch.setattr(
        repair_onnxruntime,
        "_release_runtime_version",
        lambda name, installed=None: "1.21.0",
    )
    assert (
        repair_onnxruntime._runtime_install_spec("onnxruntime-gpu", extras="cuda,cudnn")
        == "onnxruntime-gpu[cuda,cudnn]==1.21.0"
    )
    assert (
        repair_onnxruntime._runtime_install_spec("onnxruntime-directml")
        == "onnxruntime-directml==1.21.0"
    )

    # When no version resolves, the spec is the bare (optionally extra'd) name.
    monkeypatch.setattr(
        repair_onnxruntime,
        "_release_runtime_version",
        lambda name, installed=None: None,
    )
    assert (
        repair_onnxruntime._runtime_install_spec("onnxruntime-gpu") == "onnxruntime-gpu"
    )
    assert (
        repair_onnxruntime._runtime_install_spec("onnxruntime-gpu", extras="cuda")
        == "onnxruntime-gpu[cuda]"
    )


# ---------------------------------------------------------------------------
# Group B — constraint sanitization (fully hermetic; path/string args)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("numpy==1.26.4", "numpy==1.26.4"),
        ("uvicorn[standard]==0.46.0", "uvicorn==0.46.0"),  # extras stripped
        (
            'cffi==2.0.0 ; sys_platform == "win32"',
            'cffi==2.0.0 ; sys_platform == "win32"',
        ),
        ("# generated by pip-compile", None),
        ("-r requirements-other.txt", None),
        ("example @ https://example.invalid/example.whl", None),
        ("unpinned-package", None),  # no ==version
        ("", None),
        ("   ", None),
    ],
)
def test_sanitize_constraint_line_edge_cases(line, expected):
    assert repair_onnxruntime._sanitize_constraint_line(line) == expected


def test_pinned_requirement_regex_groups():
    """The regex captures name + ==version + optional marker; extras uncaptured."""
    m = repair_onnxruntime._PINNED_REQUIREMENT_RE.match("numpy==1.26.4")
    assert m is not None
    assert m.groups() == ("numpy", "==1.26.4", None)

    m = repair_onnxruntime._PINNED_REQUIREMENT_RE.match(
        'cffi==2.0.0 ; sys_platform == "win32"'
    )
    assert m is not None
    assert m.group(1) == "cffi"
    assert m.group(2) == "==2.0.0"
    assert m.group(3).strip() == '; sys_platform == "win32"'

    # Bare name with no pin does not match at all.
    assert repair_onnxruntime._PINNED_REQUIREMENT_RE.match("numpy") is None


def test_write_sanitized_constraints_roundtrip(tmp_path):
    reqs = tmp_path / "requirements-core.txt"
    reqs.write_text(
        "\n".join(
            [
                "# header",
                "numpy==1.26.4",
                "uvicorn[standard]==0.46.0",
                "-r other.txt",
            ]
        ),
        encoding="utf-8",
    )
    out = repair_onnxruntime._write_sanitized_constraints(reqs)
    assert out is not None
    try:
        text = out.read_text(encoding="utf-8")
        assert "numpy==1.26.4" in text
        assert "uvicorn==0.46.0" in text
        assert "-r other.txt" not in text
        assert text.endswith("\n")
    finally:
        out.unlink(missing_ok=True)


def test_write_sanitized_constraints_returns_none_when_no_valid_lines(tmp_path):
    reqs = tmp_path / "requirements-core.txt"
    reqs.write_text("# only comments\n-r other.txt\n", encoding="utf-8")
    assert repair_onnxruntime._write_sanitized_constraints(reqs) is None


def test_write_sanitized_constraints_returns_none_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    assert repair_onnxruntime._write_sanitized_constraints(missing) is None


def test_core_requirements_constraint_args_uses_constraint_flag():
    """Against the real checked-in requirements-core.txt the helper yields a
    ``["--constraint", <path>]`` pair pointing at a readable temp file."""
    args = repair_onnxruntime._core_requirements_constraint_args()
    assert len(args) == 2
    assert args[0] == "--constraint"
    constraint_path = Path(args[1])
    try:
        assert constraint_path.exists()
        assert constraint_path.read_text(encoding="utf-8").strip() != ""
    finally:
        constraint_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Group C — GPU detection pure helpers
# ---------------------------------------------------------------------------


def test_empty_gpu_detection_shape():
    assert repair_onnxruntime._empty_gpu_detection() == {
        "vendors": [],
        "primary": None,
        "devices": [],
    }


@pytest.mark.parametrize(
    "name, vendor",
    [
        ("NVIDIA GeForce RTX 4090", "nvidia"),
        ("AMD Radeon RX 7900", "amd"),
        ("Radeon Graphics", "amd"),  # 'radeon' alone maps to amd
        ("Intel Arc A770", "intel"),
        ("Some Weird Accelerator", "unknown"),
        ("nvidia geforce (lowercase)", "nvidia"),  # case-insensitive
    ],
)
def test_vendor_from_device_name_mapping(name, vendor):
    assert repair_onnxruntime._vendor_from_device_name(name) == vendor


def test_primary_vendor_preference_order():
    # nvidia > amd > intel regardless of list order.
    assert repair_onnxruntime._primary_vendor(["intel", "amd", "nvidia"]) == "nvidia"
    assert repair_onnxruntime._primary_vendor(["intel", "amd"]) == "amd"
    assert repair_onnxruntime._primary_vendor(["intel"]) == "intel"
    # No preferred vendor present -> first element.
    assert repair_onnxruntime._primary_vendor(["unknown"]) == "unknown"
    # Empty -> None.
    assert repair_onnxruntime._primary_vendor([]) is None


def test_target_runtime_for_vendor_selection():
    """Runtime-selection contract: AMD/Intel -> DirectML; everything else
    (NVIDIA, unknown, None) -> onnxruntime-gpu.

    NOTE: the ORT *provider-alias* validation from a8d87fd lives in
    optional_dependencies.py / runtime_dependency_check.py, NOT here. This
    module only selects the pip DISTRIBUTION by vendor; provider names
    (CUDA/Dml/CPUExecutionProvider) surface only as ``_probe_ort_providers``
    return values.
    """
    assert (
        repair_onnxruntime._target_runtime_for_vendor("intel") == "onnxruntime-directml"
    )
    assert (
        repair_onnxruntime._target_runtime_for_vendor("amd") == "onnxruntime-directml"
    )
    assert repair_onnxruntime._target_runtime_for_vendor("nvidia") == "onnxruntime-gpu"
    assert repair_onnxruntime._target_runtime_for_vendor("unknown") == "onnxruntime-gpu"
    assert repair_onnxruntime._target_runtime_for_vendor(None) == "onnxruntime-gpu"


def test_parse_windows_cim_output_single_and_list():
    single = repair_onnxruntime._parse_windows_cim_output(
        '{"Name":"NVIDIA GeForce RTX 4090"}'
    )
    assert single == {
        "devices": [{"name": "NVIDIA GeForce RTX 4090", "vendor": "nvidia"}],
        "vendors": ["nvidia"],
        "primary": "nvidia",
    }

    multi = repair_onnxruntime._parse_windows_cim_output(
        '[{"Name":"NVIDIA RTX"},{"Name":"Intel Arc"}]'
    )
    assert [d["vendor"] for d in multi["devices"]] == ["nvidia", "intel"]
    assert multi["vendors"] == ["nvidia", "intel"]
    assert multi["primary"] == "nvidia"


def test_parse_windows_cim_output_filters_virtual_and_basic_render():
    result = repair_onnxruntime._parse_windows_cim_output(
        '[{"Name":"Virtual Display Adapter"},'
        '{"Name":"Microsoft Basic Render Driver"},'
        '{"Name":"Intel Arc"}]'
    )
    assert result["devices"] == [{"name": "Intel Arc", "vendor": "intel"}]


def test_parse_windows_cim_output_keeps_nvidia_virtual():
    """A 'virtual' name that also contains 'nvidia' (vGPU / GRID) is kept."""
    result = repair_onnxruntime._parse_windows_cim_output(
        '[{"Name":"NVIDIA Virtual GPU"}]'
    )
    assert result["devices"] == [{"name": "NVIDIA Virtual GPU", "vendor": "nvidia"}]


def test_parse_windows_cim_output_dedups_vendors():
    result = repair_onnxruntime._parse_windows_cim_output(
        '[{"Name":"NVIDIA A"},{"Name":"NVIDIA B"}]'
    )
    assert len(result["devices"]) == 2
    assert result["vendors"] == ["nvidia"]


@pytest.mark.parametrize(
    "raw, exc",
    [
        ("123", TypeError),  # top-level scalar
        ('"a string"', TypeError),  # top-level scalar string
        ("[1, 2]", TypeError),  # row is not an object
        ("[{}]", ValueError),  # row missing Name
        ('[{"Name":"   "}]', ValueError),  # blank Name
        ('[{"Name": 5}]', ValueError),  # non-string Name
    ],
)
def test_parse_windows_cim_output_raises_on_bad_shapes(raw, exc):
    with pytest.raises(exc):
        repair_onnxruntime._parse_windows_cim_output(raw)


def test_detect_nvidia_gpu_empty_output_returns_empty(monkeypatch):
    """nvidia-smi returning only whitespace -> empty detection (not a crash)."""
    monkeypatch.setattr(
        repair_onnxruntime.subprocess, "check_output", lambda *a, **k: "  \n \n"
    )
    assert (
        repair_onnxruntime._detect_nvidia_gpu()
        == repair_onnxruntime._empty_gpu_detection()
    )


def test_log_detection_warning_message_branches(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger=repair_onnxruntime.logger.name):
        repair_onnxruntime._log_detection_warning(
            probe="windows-cim", reason="command_failed", error=RuntimeError("boom")
        )
        repair_onnxruntime._log_detection_warning(
            probe="nvidia-smi", reason="empty_output", error=None
        )
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Windows CIM GPU detection failed" in m for m in messages)
    assert any(
        "NVIDIA driver CLI GPU detection was inconclusive" in m for m in messages
    )


# ---------------------------------------------------------------------------
# Group D — real get_install_state assembly (never exercised by the flow suite)
# ---------------------------------------------------------------------------


def test_get_install_state_key_set_and_conflict(monkeypatch):
    monkeypatch.setattr(repair_onnxruntime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        repair_onnxruntime,
        "_detect_gpu_vendor",
        lambda: {
            "vendors": ["nvidia"],
            "primary": "nvidia",
            "devices": [{"name": "RTX", "vendor": "nvidia"}],
        },
    )

    # Only the CPU package present -> no conflict.
    monkeypatch.setattr(
        repair_onnxruntime,
        "_version",
        lambda d: "1.20.1" if d == "onnxruntime" else None,
    )
    state = repair_onnxruntime.get_install_state()
    assert set(state) == {
        "platform",
        "python",
        "onnxruntime_version",
        "onnxruntime_gpu_version",
        "onnxruntime_directml_version",
        "has_conflict",
        "has_gpu_package",
        "has_dml_package",
        "gpu_vendor_primary",
        "gpu_vendors_detected",
        "gpu_devices",
    }
    assert state["has_conflict"] is False
    assert state["has_gpu_package"] is False
    assert state["has_dml_package"] is False
    assert state["gpu_vendor_primary"] == "nvidia"

    # All three variants installed at once -> has_conflict True (sum > 1).
    monkeypatch.setattr(repair_onnxruntime, "_version", lambda d: "1.0.0")
    conflicted = repair_onnxruntime.get_install_state()
    assert conflicted["has_conflict"] is True
    assert conflicted["has_gpu_package"] is True
    assert conflicted["has_dml_package"] is True


# ---------------------------------------------------------------------------
# Group E — platform dispatch + per-OS guards
# ---------------------------------------------------------------------------


def test_repair_platform_routes_windows(monkeypatch):
    sentinel = {"platform": "Windows", "repaired": True, "actions": ["win path"]}
    monkeypatch.setattr(repair_onnxruntime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        repair_onnxruntime,
        "repair_windows_onnxruntime",
        lambda *, stream_pip=False: sentinel,
    )
    assert repair_onnxruntime.repair_platform_onnxruntime(stream_pip=True) is sentinel


def test_repair_platform_darwin_reports_no_repair(monkeypatch):
    monkeypatch.setattr(repair_onnxruntime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        repair_onnxruntime, "get_install_state", lambda: {"platform": "Darwin"}
    )
    result = repair_onnxruntime.repair_platform_onnxruntime()
    assert result["repaired"] is False
    assert result["actions"] == ["No repair needed on this platform"]


def test_repair_windows_guard_returns_empty_on_non_windows(monkeypatch):
    monkeypatch.setattr(
        repair_onnxruntime,
        "get_install_state",
        lambda: {"platform": "Linux"},
    )
    # No _run_pip / _version stub needed: the guard must return before touching them.
    result = repair_onnxruntime.repair_windows_onnxruntime()
    assert result["repaired"] is False
    assert result["actions"] == []


def test_repair_linux_guard_returns_empty_on_non_linux(monkeypatch):
    monkeypatch.setattr(
        repair_onnxruntime,
        "get_install_state",
        lambda: {"platform": "Windows"},
    )
    result = repair_onnxruntime.repair_linux_onnxruntime()
    assert result["repaired"] is False
    assert result["actions"] == []


# ---------------------------------------------------------------------------
# Group F — main() argument + exit-code contract
# ---------------------------------------------------------------------------


def _full_state(**overrides):
    state = {
        "platform": "Windows",
        "python": sys.executable,
        "onnxruntime_version": "1.20.1",
        "onnxruntime_gpu_version": None,
        "onnxruntime_directml_version": None,
        "has_conflict": False,
        "has_gpu_package": False,
        "has_dml_package": False,
        "gpu_vendor_primary": "nvidia",
        "gpu_vendors_detected": ["nvidia"],
        "gpu_devices": [{"name": "RTX", "vendor": "nvidia"}],
    }
    state.update(overrides)
    return state


def test_main_without_auto_does_not_repair(monkeypatch, capsys):
    repair_calls = []
    monkeypatch.setattr(repair_onnxruntime.sys, "argv", ["repair_onnxruntime.py"])
    monkeypatch.setattr(repair_onnxruntime, "get_install_state", lambda: _full_state())
    monkeypatch.setattr(
        repair_onnxruntime,
        "repair_platform_onnxruntime",
        lambda *, stream_pip=False: repair_calls.append(stream_pip) or {},
    )

    rc = repair_onnxruntime.main()

    assert rc == 0
    assert repair_calls == []  # no --auto -> repair never invoked
    out = capsys.readouterr().out
    assert "No repair needed" in out


def test_main_auto_invokes_platform_repair_streaming(monkeypatch, capsys):
    repair_calls = []
    monkeypatch.setattr(
        repair_onnxruntime.sys, "argv", ["repair_onnxruntime.py", "--auto"]
    )
    monkeypatch.setattr(repair_onnxruntime, "get_install_state", lambda: _full_state())

    def fake_repair(*, stream_pip=False):
        repair_calls.append(stream_pip)
        return _full_state(
            repaired=True, actions=["did a thing"], providers_after_repair=[]
        )

    monkeypatch.setattr(repair_onnxruntime, "repair_platform_onnxruntime", fake_repair)

    rc = repair_onnxruntime.main()

    assert rc == 0
    # Human-readable mode (no --json) => stream_pip=True.
    assert repair_calls == [True]


def test_main_auto_json_disables_stream_and_prints_json(monkeypatch, capsys):
    repair_calls = []
    monkeypatch.setattr(
        repair_onnxruntime.sys, "argv", ["repair_onnxruntime.py", "--auto", "--json"]
    )
    monkeypatch.setattr(repair_onnxruntime, "get_install_state", lambda: _full_state())

    def fake_repair(*, stream_pip=False):
        repair_calls.append(stream_pip)
        return _full_state(
            repaired=True, actions=["did a thing"], target_runtime="onnxruntime-gpu"
        )

    monkeypatch.setattr(repair_onnxruntime, "repair_platform_onnxruntime", fake_repair)

    rc = repair_onnxruntime.main()

    assert rc == 0
    # --json => stream_pip=False (machine-readable, no streamed pip chatter).
    assert repair_calls == [False]
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["repaired"] is True
    assert parsed["target_runtime"] == "onnxruntime-gpu"


def test_module_entrypoint_raises_systemexit_of_main():
    """The ``__main__`` guard forwards ``main()``'s int return through
    ``SystemExit`` so the process exit code is the function's return value."""
    source = Path(repair_onnxruntime.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source


# ---------------------------------------------------------------------------
# Group G — architectural invariants (the load-bearing pins)
# ---------------------------------------------------------------------------


def test_module_has_no_non_stdlib_top_level_imports():
    """The 'dependency of last resort' contract: repair_onnxruntime.py runs in a
    broken-onnxruntime / embedded-Python environment, so every top-level import
    MUST resolve to the standard library. A split that introduces a third-party
    (or non-stdlib sibling that itself pulls one) import would break the repair
    exactly when it is needed most.
    """
    tree = ast.parse(Path(repair_onnxruntime.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Only module-level absolute imports count; relative sibling imports
            # (level > 0) would themselves have to be stdlib-only anyway.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])

    non_stdlib = roots - set(sys.stdlib_module_names)
    assert non_stdlib == set(), (
        f"repair_onnxruntime.py gained non-stdlib top-level import(s): {sorted(non_stdlib)}. "
        "This module must stay stdlib-only (it runs when onnxruntime is broken)."
    )


def test_detect_gpu_vendor_importable_for_torch_sibling():
    """``repair_torch_runtime`` does ``from repair_onnxruntime import
    _detect_gpu_vendor``. Any future split MUST keep that exact symbol
    importable from the ``repair_onnxruntime`` namespace (facade re-export),
    or the Torch repair silently falls back to its empty detector stub and
    NVIDIA users never get CUDA torch.
    """
    from repair_onnxruntime import _detect_gpu_vendor  # noqa: F401

    assert callable(_detect_gpu_vendor)
    assert _detect_gpu_vendor.__module__ == "repair_onnxruntime"


def test_launchers_invoke_repair_by_filename():
    """run.bat / run.sh / build_release_packages.py all invoke the repair by the
    literal filename ``repair_onnxruntime.py --auto``. The module must therefore
    remain a top-level executable FILE with that exact basename; a rename or a
    move-into-package would break the launcher contract (and
    test_release_build.py's generated-launcher assertions).
    """
    assert Path(repair_onnxruntime.__file__).name == "repair_onnxruntime.py"

    for rel in ("run.bat", "run.sh", "scripts/build_release_packages.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "repair_onnxruntime.py --auto" in text, (
            f"{rel} no longer invokes 'repair_onnxruntime.py --auto' by filename"
        )
