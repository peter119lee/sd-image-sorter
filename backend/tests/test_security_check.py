from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_security_check():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_pip_audit_scans_full_tree_with_ignore_vuln_allowlist(monkeypatch):
    security_check = _load_security_check()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(security_check.subprocess, "run", fake_run)

    assert security_check.run_pip_audit("backend/requirements.txt", "/tmp/python") == 0

    expected_command = [
        "/tmp/python",
        "-m",
        "pip_audit",
        "-r",
        "backend/requirements.txt",
        "--progress-spinner",
        "off",
    ]
    for vuln_id in security_check.IGNORED_VULN_IDS:
        expected_command.extend(["--ignore-vuln", vuln_id])

    assert calls == [expected_command]
    # The full resolved tree must be audited, so --no-deps must NOT be present.
    assert "--no-deps" not in calls[0]


def test_ensure_pip_audit_runner_reuses_current_interpreter_when_available(monkeypatch):
    security_check = _load_security_check()
    monkeypatch.setattr(security_check, "_python_has_pip_audit", lambda python: True)

    assert security_check.ensure_pip_audit_runner() == security_check.sys.executable


def test_ensure_pip_audit_runner_uses_existing_temp_venv_without_global_install(monkeypatch, tmp_path: Path):
    security_check = _load_security_check()
    venv_python = tmp_path / "pip-audit-venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("python", encoding="utf-8")

    def fake_has_pip_audit(python: str) -> bool:
        return Path(python) == venv_python

    monkeypatch.setattr(security_check, "_python_has_pip_audit", fake_has_pip_audit)
    monkeypatch.setattr(security_check.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(security_check, "_venv_python", lambda _venv_dir: venv_python)
    monkeypatch.setattr(security_check, "_create_pip_audit_venv", lambda _venv_dir: (_ for _ in ()).throw(AssertionError("should reuse existing venv")))
    monkeypatch.setattr(security_check, "install_pip_audit", lambda _python: (_ for _ in ()).throw(AssertionError("should not install globally")))

    assert security_check.ensure_pip_audit_runner() == str(venv_python)


def test_create_pip_audit_venv_falls_back_to_without_pip_system_site_packages(monkeypatch, tmp_path: Path):
    security_check = _load_security_check()
    calls: list[list[str]] = []
    venv_python = tmp_path / "venv" / "bin" / "python"

    class FailingEnvBuilder:
        def __init__(self, *, with_pip: bool):
            assert with_pip is True

        def create(self, _venv_dir: Path) -> None:
            raise RuntimeError("ensurepip unavailable")

    def fake_check_call(command: list[str]) -> None:
        calls.append(command)
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("python", encoding="utf-8")

    monkeypatch.setattr(security_check.venv, "EnvBuilder", FailingEnvBuilder)
    monkeypatch.setattr(security_check.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(security_check, "_venv_python", lambda _venv_dir: venv_python)

    assert security_check._create_pip_audit_venv(tmp_path / "venv") is True
    assert calls == [[
        security_check.sys.executable,
        "-m",
        "venv",
        "--without-pip",
        "--system-site-packages",
        str(tmp_path / "venv"),
    ]]


def test_main_runs_secret_scan_and_skips_pip_audit_on_a_leak(monkeypatch):
    security_check = _load_security_check()
    monkeypatch.setattr(
        security_check,
        "find_live_secrets",
        lambda _root: ["tests/e2e/round2_real_api.spec.js"],
    )
    monkeypatch.setattr(
        security_check,
        "ensure_pip_audit_runner",
        lambda: (_ for _ in ()).throw(AssertionError("must not install pip-audit after a leak")),
    )
    monkeypatch.setattr(
        security_check,
        "run_pip_audit",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not audit after a leak")),
    )

    with pytest.raises(SystemExit) as exited:
        security_check.main()
    assert exited.value.code == 1


def test_find_live_secrets_flags_github_and_hf_tokens(tmp_path: Path, monkeypatch):
    security_check = _load_security_check()
    planted = tmp_path / "leaked.env"
    github_token = "ghp_" + ("a" * 36)
    hf_token = "hf_" + ("b" * 24)
    planted.write_text(
        f"GITHUB_TOKEN={github_token}\nHF_TOKEN={hf_token}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(security_check, "_tracked_text_files", lambda _root: [planted])

    leaks = security_check.find_live_secrets(tmp_path)
    assert any("ghp_" in item for item in leaks)
    assert any("hf_" in item for item in leaks)
