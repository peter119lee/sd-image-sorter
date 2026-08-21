"""Tracked sources must not embed live-looking API keys.

A one-off Playwright script committed a real AIHubMix key in 2026-05. GitHub
visitors could read it from main. This pin scans the git index so a later
working-tree-only cleanup cannot hide the same class of leak.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_API_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")


def _load_security_check():
    script_path = REPO_ROOT / "scripts" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_for_secrets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_sources_do_not_embed_live_api_keys():
    leaks = _load_security_check().find_live_secrets(REPO_ROOT)
    assert not leaks, (
        "tracked files embed a live-looking secret. Use an environment variable. "
        "Offending files:\n  " + "\n  ".join(leaks)
    )


def test_optional_live_vlm_script_reads_key_from_env():
    script = (
        REPO_ROOT / "tests" / "e2e" / "round2_real_api.spec.js"
    ).read_text(encoding="utf-8")
    assert "process.env.AIHUBMIX_API_KEY" in script
    assert "AIHUBMIX_API_KEY" in script
    assert "real aihubmix API" not in script.lower()
    assert not LIVE_API_KEY.search(script)
