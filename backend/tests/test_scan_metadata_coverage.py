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


def test_scan_does_not_send_the_user_after_text_that_is_already_there(
    test_client, test_db, tmp_path
):
    """A folder of sidecar-captioned images is not a metadata problem.

    This is the owner's folder: 8 images with no SD parameters at all, each with
    a ``.txt`` beside it. Migration 042 stores that text in
    ``images.sidecar_caption`` and leaves ``prompt`` empty, so every one of them
    counts as "no prompt" — yet "Recover Missing Text" has nothing left to find
    for any of them. Naming that action here is a report of a problem that does
    not exist plus an instruction that cannot succeed.
    """
    sandbox = _build_sandbox(tmp_path, with_prompt=2, without_prompt=8)
    for path in sorted(sandbox.glob("no-prompt-*.png")):
        path.with_suffix(".txt").write_text(
            "1girl, solo, silver hair, looking at viewer", encoding="utf-8"
        )

    progress = _scan_and_wait(test_client, sandbox)

    assert progress["status"] == "done"
    # The summary the user reads must not send them after text that is there.
    assert "recover missing text" not in progress["message"].lower()
    # The rows really are promptless — that statistic still travels...
    assert progress["metadata_prompt_total"] == 10
    assert progress["metadata_missing_prompt"] == 8
    # ...and their text really did land in the caption column.
    assert progress["metadata_missing_text"] == 0


def test_scan_still_calls_out_a_folder_with_no_text_at_all(
    test_client, test_db, tmp_path
):
    """The advisory must survive: 8 of 10 images with neither prompt nor caption."""
    sandbox = _build_sandbox(tmp_path, with_prompt=2, without_prompt=8)

    progress = _scan_and_wait(test_client, sandbox)

    assert progress["status"] == "done"
    assert progress["metadata_missing_text"] == 8
    assert "recover missing text" in progress["message"].lower()
    assert "8" in progress["message"]
