"""Torch/runtime probing helpers (split from model_health.py, 2026-07).

_module_available / _module_installed / _probe_loaded_torch_runtime /
_probe_torch_runtime moved here verbatim. Nothing here runs at import time: the 45s-timeout torch subprocess is
forked only when _probe_torch_runtime is CALLED (and torch is not already in
sys.modules), so importing this module stays light. _probe_torch_runtime
resolves _probe_loaded_torch_runtime back through _svc() at call time so
monkeypatches on the facade module keep affecting behavior; sys / warnings /
importlib are process-global singletons, so direct stdlib imports here
observe the same patches (e.g. model_health.sys.platform).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import warnings
from typing import Any, Dict


def _svc():
    """Resolve facade-patched seams through model_health at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy
    import avoids a facade<->sibling load cycle.
    """
    import model_health

    return model_health


def _module_available(module_name: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _module_installed(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _probe_loaded_torch_runtime() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "torch_version": None,
        "torch_cuda_build": None,
        "torch_cuda_available": False,
        "torch_probe_error": None,
        "torch_probe_source": "current-process",
    }
    try:
        torch = sys.modules.get("torch")
        if torch is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                torch = importlib.import_module("torch")
        result["torch_version"] = getattr(torch, "__version__", None)
        result["torch_cuda_build"] = getattr(getattr(torch, "version", None), "cuda", None)
        result["torch_cuda_available"] = bool(torch.cuda.is_available())
    except Exception as exc:
        result["torch_probe_error"] = str(exc)
    return result


def _probe_torch_runtime() -> Dict[str, Any]:
    if "torch" in sys.modules:
        return _svc()._probe_loaded_torch_runtime()

    code = r'''
import json
from importlib import metadata
result = {
    "torch_version": None,
    "torch_cuda_build": None,
    "torch_cuda_available": False,
    "torch_probe_error": None,
    "torch_probe_source": "subprocess",
}
try:
    result["torch_version"] = metadata.version("torch")
except Exception:
    pass
try:
    import torch
    result["torch_version"] = getattr(torch, "__version__", result["torch_version"])
    result["torch_cuda_build"] = getattr(getattr(torch, "version", None), "cuda", None)
    result["torch_cuda_available"] = bool(torch.cuda.is_available())
except Exception as exc:
    result["torch_probe_error"] = str(exc)
print(json.dumps(result))
'''.strip()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except Exception as exc:
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "torch_probe_error": str(exc),
            "torch_probe_source": "subprocess",
        }

    if completed.returncode != 0:
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "torch_probe_error": (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip(),
            "torch_probe_source": "subprocess",
        }

    try:
        parsed = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
    except Exception as exc:
        return {
            "torch_version": None,
            "torch_cuda_build": None,
            "torch_cuda_available": False,
            "torch_probe_error": f"Could not parse torch probe output: {exc}",
            "torch_probe_source": "subprocess",
        }

    return {
        "torch_version": parsed.get("torch_version"),
        "torch_cuda_build": parsed.get("torch_cuda_build"),
        "torch_cuda_available": bool(parsed.get("torch_cuda_available")),
        "torch_probe_error": parsed.get("torch_probe_error"),
        "torch_probe_source": parsed.get("torch_probe_source") or "subprocess",
    }
