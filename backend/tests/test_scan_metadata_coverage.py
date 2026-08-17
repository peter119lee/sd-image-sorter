"""A scan must report how much of the folder it actually got metadata for.

Background
==========
Scanning a folder of 6,842 images left 5,121 of them with no prompt, and the
scan still finished with "Done! N images indexed." The metadata-coverage ratio
existed only in a log line the user never reads, so three quarters of the
library was silently promptless while the scan reported a clean success.

The completion payload now carries the denominator alongside the shortfall,
and the summary says so once the shortfall is a large fraction of the folder.
"""
from __future__ import annotations

import time
from pathlib import Path

WEBUI_PARAMS = (
    "a cat sitting on a windowsill\n"
    "Negative prompt: blurry\n"
    "Steps: 28, Sampler: DPM++ 2M, CFG scale: 7, Seed: 12345, "
    "Size: 64x64, Model: someModel"
)


def _build_sandbox(tmp_path: Path, *, with_prompt: int, without_prompt: int) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    sandbox = tmp_path / "coverage-sandbox"
    sandbox.mkdir()
    for i in range(with_prompt):
        info = PngInfo()
        info.add_text("parameters", WEBUI_PARAMS)
        Image.new("RGB", (64, 64), color=(10, 20, 30)).save(
            sandbox / f"has-prompt-{i}.png", pnginfo=info
        )
    for i in range(without_prompt):
        Image.new("RGB", (64, 64), color=(90, 90, 90)).save(
            sandbox / f"no-prompt-{i}.png"
        )
    return sandbox


def _scan_and_wait(test_client, folder: Path) -> dict:
    test_client.post("/api/scan/reset")
    started = test_client.post(
        "/api/scan", json={"folder_path": str(folder), "recursive": False}
    )
    assert started.status_code == 200, started.text
    for _ in range(120):
        progress = test_client.get("/api/scan/progress").json()
        if progress.get("status") in {"done", "error", "cancelled"}:
            return progress
        time.sleep(0.25)
    raise AssertionError(f"scan did not finish; last progress={progress}")


def test_scan_completion_reports_the_prompt_shortfall(test_client, test_db, tmp_path):
    """8 of 10 images carry no prompt: the user must be told, not just "Done"."""
    sandbox = _build_sandbox(tmp_path, with_prompt=2, without_prompt=8)

    progress = _scan_and_wait(test_client, sandbox)

    assert progress["status"] == "done"
    # The denominator has to travel with the shortfall or "8 missing" is
    # unreadable — 8 out of 10 and 8 out of 8,000 are different situations.
    assert progress["metadata_prompt_total"] == 10
    assert progress["metadata_missing_prompt"] == 8
    # And the summary the user actually reads must not stop at "Done!".
    assert "8" in progress["message"]
    assert "prompt" in progress["message"].lower()


def test_scan_completion_stays_quiet_when_metadata_coverage_is_good(
    test_client, test_db, tmp_path
):
    """A clean folder must not be nagged; the counters still travel."""
    sandbox = _build_sandbox(tmp_path, with_prompt=10, without_prompt=0)

    progress = _scan_and_wait(test_client, sandbox)

    assert progress["status"] == "done"
    assert progress["metadata_prompt_total"] == 10
    assert progress["metadata_missing_prompt"] == 0
    assert "no prompt" not in progress["message"].lower()
