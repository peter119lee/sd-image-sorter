"""Tracked sources must not embed live-looking API keys.

A one-off Playwright script committed a real AIHubMix key in 2026-05. GitHub
visitors could read it from main. This pin scans the git index so a later
working-tree-only cleanup cannot hide the same class of leak.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# 20+ chars after sk- is long enough to catch live OpenAI-compatible keys
# (the leaked one was 48) and short enough to ignore documented fixtures
# such as sk-should-not-appear and sk-abcdefgh-secret.
LIVE_API_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")
SKIP_SUFFIXES = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".onnx",
    ".png",
    ".pt",
    ".safetensors",
    ".webp",
    ".woff",
    ".woff2",
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
    )
    return result.stdout


def test_tracked_sources_do_not_embed_live_api_keys():
    leaks: list[str] = []
    for relative in _git("ls-files").splitlines():
        path = REPO_ROOT / relative
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if LIVE_API_KEY.search(text):
            leaks.append(relative.replace("\\", "/"))

    assert not leaks, (
        "tracked files embed a live-looking API key (sk- + 20+ chars). "
        "Use an environment variable. Offending files:\n  " + "\n  ".join(leaks)
    )


def test_optional_live_vlm_script_reads_key_from_env():
    script = (
        REPO_ROOT / "tests" / "e2e" / "round2_real_api.spec.js"
    ).read_text(encoding="utf-8")
    assert "process.env.AIHUBMIX_API_KEY" in script
    assert "AIHUBMIX_API_KEY" in script
    assert "real aihubmix API" not in script.lower()
    assert not LIVE_API_KEY.search(script)
