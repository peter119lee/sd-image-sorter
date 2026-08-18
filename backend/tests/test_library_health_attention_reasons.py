"""The attention list may only accuse a row of a defect the audit counts.

Background
==========
``issue_samples`` is "Files Needing Attention": the panel's one per-row list, and
the only place the audit points at an individual image. Its membership and its
ordering are built from :data:`db_facets.SAMPLE_REASON_LADDER`, so a row is
listed for a defect ``issue_counts`` publishes and the reason shown against it is
the first such defect it matches.

The panel used to decide that reason itself, from two rules the backend had
already rejected:

* any row with an empty ``prompt`` was called **Missing prompt**, though
  ``missing_text`` deliberately spares a row whose text is in
  ``sidecar_caption``; and
* any row with an empty ``checkpoint_normalized`` was called **Missing
  checkpoint**, though ``sd_missing_checkpoint`` only counts rows some generator
  actually claimed.

So the two shapes below — a caption-only row listed for being untagged, and a
prompted row listed for recording generation data against no generator — were
each shown a defect the audit refuses to count. ``get_library_health_report``
ships ``sidecar_caption`` and ``generator`` precisely so a consumer need not
guess; this test drives the real payload through the real panel code.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import database as db

DANBOORU_CAPTION = "1girl, solo, silver hair, looking at viewer"
SD_PROMPT = "masterpiece, best quality, 1girl"

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed(
    folder: Path,
    name: str,
    *,
    prompt: Optional[str] = None,
    caption: Optional[str] = None,
    generator: str = "unknown",
    checkpoint: Optional[str] = None,
    tagged: bool = True,
) -> int:
    image_id = int(
        db.add_image(
            path=str(folder / name),
            filename=name,
            generator=generator,
            prompt=prompt,
            checkpoint=checkpoint,
            width=64,
            height=64,
            file_size=1024,
            metadata_json="{}",
        )
    )
    with db.get_db() as conn:
        conn.execute(
            "UPDATE images SET prompt = ?, sidecar_caption = ? WHERE id = ?",
            (prompt, caption, image_id),
        )
        if tagged:
            conn.execute(
                "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
                (image_id,),
            )
    return image_id


def _panel_reason_source() -> str:
    """The shipped ladder and the function that walks it, as one runnable block.

    Sliced out of the module rather than re-typed, so this test exercises the
    code the browser loads instead of a copy that can quietly stop matching it.
    """
    source = (REPO_ROOT / "frontend" / "js" / "library-health.js").read_text(
        encoding="utf-8"
    )
    start = source.find("var UNATTRIBUTED_GENERATORS")
    assert start != -1, "library-health.js no longer declares UNATTRIBUTED_GENERATORS"
    end = re.search(
        r"^    function sampleReason\(.*?^    \}$", source, re.MULTILINE | re.DOTALL
    )
    assert end is not None, "library-health.js no longer declares sampleReason()"
    assert end.end() > start, "sampleReason no longer follows the ladder it walks"
    return source[start : end.end()]


def _panel_reasons(samples: List[Dict[str, Any]]) -> List[str]:
    """Run the panel's own ``sampleReason`` over these rows and return its output.

    ``t`` is shimmed to return the translation key, so the assertions name the
    reason that was chosen rather than one language's wording of it.
    """
    if shutil.which("node") is None:
        pytest.skip("node is required to execute the shipped panel code")
    script = (
        "function t(key, fallback) { return key; }\n"
        f"{_panel_reason_source()}\n"
        f"const samples = {json.dumps(samples)};\n"
        "console.log(JSON.stringify(samples.map(sampleReason)));\n"
    )
    result = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


@pytest.fixture
def mixed_library(test_db, tmp_path: Path) -> Dict[str, Any]:
    """One row per reason the ladder can pick, including the two it used to get wrong."""
    folder = tmp_path / "library"
    folder.mkdir()
    return {
        # Listed for being untagged. Its text is a sidecar caption, so
        # missing_text excludes it and no generator claimed it, so
        # sd_missing_checkpoint excludes it too.
        "caption_only_untagged": _seed(
            folder, "caption-only-untagged.png", caption=DANBOORU_CAPTION, tagged=False
        ),
        # Listed for unattributed_sd_metadata: it records a prompt against no
        # generator, which today's parser cannot write, so the attribution is
        # stale. Its empty checkpoint is not a defect — nothing claimed it.
        "prompted_no_generator": _seed(
            folder, "prompted-no-generator.png", prompt=SD_PROMPT
        ),
        # The row a missing checkpoint really is a defect for.
        "generated_no_checkpoint": _seed(
            folder, "generated-no-checkpoint.png", prompt=SD_PROMPT, generator="webui"
        ),
        # Neither prompt nor caption: the row Recover Missing Text can move.
        "textless": _seed(
            folder, "textless.png", generator="others", checkpoint="anythingV5"
        ),
    }


def test_the_attention_list_describes_each_row_by_the_defect_that_listed_it(
    mixed_library: Dict[str, Any],
) -> None:
    report = db.get_library_health_report(sample_limit=20)
    samples = {int(row["id"]): row for row in report["issue_samples"]}

    # The two shapes this test exists for have to be on the list before anything
    # is asserted about how they are described; a sample list filled only with
    # unreadable and pending rows would pass any wording check by accident.
    for name in ("caption_only_untagged", "prompted_no_generator"):
        assert mixed_library[name] in samples, (
            f"{name} never reached issue_samples, so this test would be asserting "
            "about a row the panel never draws"
        )

    ordered_ids = [int(row["id"]) for row in report["issue_samples"]]
    reasons = dict(zip(ordered_ids, _panel_reasons(report["issue_samples"])))

    assert reasons[mixed_library["caption_only_untagged"]] == "health.reason.untagged", (
        "a caption-only row is on the list for being untagged; calling it a "
        "missing prompt accuses it of a defect issue_counts.missing_text excludes"
    )
    assert (
        reasons[mixed_library["prompted_no_generator"]]
        == "health.reason.unattributedSdMetadata"
    ), (
        "a row with no generator is on the list for its stale attribution; "
        "calling it a missing checkpoint accuses it of a defect "
        "issue_counts.sd_missing_checkpoint excludes"
    )
    assert (
        reasons[mixed_library["generated_no_checkpoint"]]
        == "health.reason.missingCheckpoint"
    ), "a generator did claim this row, so its empty checkpoint is a real gap"
    assert reasons[mixed_library["textless"]] == "health.reason.missingText"


def test_every_listed_row_is_ranked_by_a_defect_the_vocabulary_declares(
    mixed_library: Dict[str, Any],
) -> None:
    """No row may reach the panel's fallback, and none may be listed unranked.

    The sample query's WHERE is the disjunction of the same predicates its
    ranking uses, so "listed but nameable by nothing" is unreachable by
    construction — this pins that construction rather than trusting it.
    """
    report = db.get_library_health_report(sample_limit=20)
    samples = report["issue_samples"]
    assert samples, "nothing was listed, so this guard checks nothing"
    assert not any(row["read_error"] for row in samples), (
        "these fixtures seed no unreadable row, so every reason below has to be "
        "a ranked label rather than a row's own error text"
    )

    reasons = _panel_reasons(samples)
    assert "health.reason.unnamed" not in reasons, (
        "a listed row fell through every rank: the panel cannot see the column "
        f"that listed it. Reasons drawn: {reasons}"
    )
    unlabelled = [reason for reason in reasons if not reason.startswith("health.reason.")]
    assert not unlabelled, f"a listed row was described by raw text: {unlabelled}"
