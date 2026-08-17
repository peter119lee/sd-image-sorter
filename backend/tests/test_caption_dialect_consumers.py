"""The five consumers that must know whether caption text is tags or prose.

`images.sidecar_caption_format` (migration 044) records the format; these tests
cover the consumers that act on it. Every test asserts what the user is told or
given — never internal state alone — and every one of them also asserts that the
caption text itself is still stored and returned in full, because the hard
invariant inherited from the marker slice is that a format label may never
discard, truncate or refuse text.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

import services.dataset_export.captions as dataset_captions
import services.tag_export.captions as tag_export_captions
from caption_format import detect_caption_format
from services.caption_dialect import (
    CAPTION_DIALECT_TARGET_MODELS,
    caption_dialect_advisory,
    nl_compose_advisory,
)
from services.dataset_export.models import DatasetReadinessRequest
from services.dataset_export.readiness import run_dataset_readiness
from services.dataset_session.ids_and_items import (
    _manifest_item_for_path,
    _session_item_for_path,
)
from services.dataset_session.allowlist import _register_session_paths
from tests.test_dataset_projects import _default_project_settings


TAG_LIST_CAPTION = "1girl, solo, long hair, looking at viewer, school uniform"
PROSE_CAPTION = (
    "A young woman stands in the middle of a sunlit classroom, and she is "
    "looking straight at the camera while the afternoon light falls across "
    "her shoulders from the window on the left."
)
MIXED_CAPTION = f"1girl, solo, long hair, school uniform. {PROSE_CAPTION}"
# One glued blob with no word separation: too long to be a tag, too wordless to
# be prose. This is the 'unknown' state — there IS text, and the classifier
# declines to guess.
UNPLACEABLE_CAPTION = "deadbeef" * 16


def _sanity_check_fixture_formats() -> None:
    assert detect_caption_format(TAG_LIST_CAPTION) == "tags"
    assert detect_caption_format(PROSE_CAPTION) == "natural"
    assert detect_caption_format(MIXED_CAPTION) == "mixed"
    assert detect_caption_format(UNPLACEABLE_CAPTION) == "unknown"


def test_fixture_captions_really_have_the_formats_these_tests_assume() -> None:
    _sanity_check_fixture_formats()


# ------------------------------------------------------------------ #
# Consumer 1 — target model versus caption dialect
# ------------------------------------------------------------------ #


def _project_with_local_sidecar(
    test_client,
    tmp_path: Path,
    name: str,
    target_model: str,
    caption: str | None,
):
    image_path = tmp_path / f"{name}.png"
    Image.new("RGB", (8, 8)).save(image_path)
    if caption is not None:
        image_path.with_suffix(".txt").write_text(caption, encoding="utf-8")
    settings = deepcopy(_default_project_settings())
    settings["target_model"] = target_model
    _register_session_paths([str(image_path)])
    response = test_client.post(
        "/api/dataset/projects",
        json={
            "name": name,
            "items": [{"item_type": "local", "path": str(image_path)}],
            "settings": settings,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["items"][0]


def test_krea2_project_flags_a_tag_list_caption_for_conversion(
    test_client,
    tmp_path: Path,
) -> None:
    item = _project_with_local_sidecar(
        test_client,
        tmp_path,
        "krea2-tags",
        "krea2",
        TAG_LIST_CAPTION,
    )

    assert item["sidecar_caption_format"] == "tags"
    advisory = item["caption_dialect"]
    assert advisory is not None, (
        "a krea2 project must not silently accept a Booru tag caption"
    )
    assert advisory["code"] == "caption_dialect_mismatch"
    assert advisory["expected_dialect"] == "natural"
    assert advisory["convert"] == "tags_to_natural"
    assert "krea2" in advisory["message"]
    # The text is still returned in full.
    assert item["sidecar_caption"] == TAG_LIST_CAPTION


def test_anima_project_flags_a_prose_caption_for_the_converse_conversion(
    test_client,
    tmp_path: Path,
) -> None:
    item = _project_with_local_sidecar(
        test_client,
        tmp_path,
        "anima-prose",
        "anima",
        PROSE_CAPTION,
    )

    assert item["sidecar_caption_format"] == "natural"
    advisory = item["caption_dialect"]
    assert advisory is not None
    assert advisory["code"] == "caption_dialect_mismatch"
    assert advisory["expected_dialect"] == "tags"
    assert advisory["convert"] == "natural_to_tags"
    assert item["sidecar_caption"] == PROSE_CAPTION


def test_a_matching_dialect_produces_no_advisory(
    test_client,
    tmp_path: Path,
) -> None:
    krea2 = _project_with_local_sidecar(
        test_client, tmp_path, "krea2-prose", "krea2", PROSE_CAPTION
    )
    anima = _project_with_local_sidecar(
        test_client, tmp_path, "anima-tags", "anima", TAG_LIST_CAPTION
    )

    assert krea2["sidecar_caption_format"] == "natural"
    assert krea2["caption_dialect"] is None
    assert anima["sidecar_caption_format"] == "tags"
    assert anima["caption_dialect"] is None


def test_a_mixed_caption_needs_attention_without_claiming_a_direction(
    test_client,
    tmp_path: Path,
) -> None:
    item = _project_with_local_sidecar(
        test_client, tmp_path, "krea2-mixed", "krea2", MIXED_CAPTION
    )

    assert item["sidecar_caption_format"] == "mixed"
    advisory = item["caption_dialect"]
    assert advisory is not None
    assert advisory["code"] == "caption_dialect_partial"
    # A hybrid NL+tag caption is legitimate for some trainers, so no conversion
    # direction may be asserted.
    assert advisory["convert"] is None
    assert item["sidecar_caption"] == MIXED_CAPTION


def test_unknown_format_is_never_reported_as_a_dialect_mismatch(
    test_client,
    tmp_path: Path,
) -> None:
    item = _project_with_local_sidecar(
        test_client, tmp_path, "krea2-unknown", "krea2", UNPLACEABLE_CAPTION
    )

    assert item["sidecar_caption_format"] == "unknown"
    assert item["caption_dialect"] is None, (
        "'unknown' means the classifier declined to guess; claiming a mismatch "
        "would push the user to convert text that may already be correct"
    )
    assert item["sidecar_caption"] == UNPLACEABLE_CAPTION


def test_absent_sidecar_has_a_null_format_and_no_advisory(
    test_client,
    tmp_path: Path,
) -> None:
    item = _project_with_local_sidecar(
        test_client, tmp_path, "krea2-none", "krea2", None
    )

    assert item["sidecar_caption"] is None
    assert item["sidecar_caption_format"] is None
    assert item["caption_dialect"] is None


@pytest.mark.parametrize("target_model", ["", "sdxl", "flux"])
def test_targets_without_dialect_evidence_stay_un_opinionated(
    test_client,
    tmp_path: Path,
    target_model: str,
) -> None:
    assert target_model not in CAPTION_DIALECT_TARGET_MODELS
    for index, caption in enumerate((TAG_LIST_CAPTION, PROSE_CAPTION, MIXED_CAPTION)):
        item = _project_with_local_sidecar(
            test_client,
            tmp_path,
            f"neutral-{target_model or 'unset'}-{index}",
            target_model,
            caption,
        )
        # The format is still reported — only the opinion is withheld.
        assert item["sidecar_caption_format"] == detect_caption_format(caption)
        assert item["caption_dialect"] is None
        assert item["sidecar_caption"] == caption


def test_advisory_policy_covers_only_the_two_evidenced_targets() -> None:
    assert CAPTION_DIALECT_TARGET_MODELS == {"krea2": "natural", "anima": "tags"}
    for target_model in ("", "sdxl", "flux", "unheard-of"):
        for caption_format in ("tags", "natural", "mixed", "unknown", None):
            assert caption_dialect_advisory(target_model, caption_format) is None


# ------------------------------------------------------------------ #
# Consumer 1b — readiness sees the caption the export would actually write
# ------------------------------------------------------------------ #


def _readiness_report(request: DatasetReadinessRequest):
    return run_dataset_readiness(
        request,
        readiness_report_id="readiness-dialect-test",
        progress_callback=lambda _processed, _total, _message: None,
        cancellation_requested=lambda: False,
    )


def _krea2_project_id_and_revision(test_client, tmp_path: Path, image_path: Path):
    settings = deepcopy(_default_project_settings())
    settings["target_model"] = "krea2"
    _register_session_paths([str(image_path)])
    created = test_client.post(
        "/api/dataset/projects",
        json={
            "name": f"krea2-readiness-{image_path.stem}",
            "items": [{"item_type": "local", "path": str(image_path)}],
            "settings": settings,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    return int(body["id"]), int(body["revision"])


def _frozen_tag_caption(booru_caption: str) -> dict[str, object]:
    return {
        "kind": "frozen_draft",
        "content": {
            "content_version": 1,
            "booru_caption": booru_caption,
            "nl_caption": "",
            "caption_type": "booru",
        },
    }


def test_readiness_warns_that_a_krea2_project_would_export_tag_captions(
    test_client,
    tmp_path: Path,
) -> None:
    source = tmp_path / "krea2-readiness.png"
    Image.new("RGB", (8, 8)).save(source)
    source.with_suffix(".txt").write_text(TAG_LIST_CAPTION, encoding="utf-8")
    project_id, revision = _krea2_project_id_and_revision(test_client, tmp_path, source)

    report = _readiness_report(DatasetReadinessRequest(
        image_paths=[str(source)],
        output_folder=str(tmp_path / "krea2-readiness-out"),
        naming_pattern="{filename}",
        content_mode="tags",
        overwrite_policy="unique",
        dataset_project_id=project_id,
        dataset_project_revision=revision,
        annotation_selections={
            str(source.resolve()): _frozen_tag_caption(TAG_LIST_CAPTION),
        },
    ))

    dialect_issues = [
        issue for issue in report.issues
        if issue.code == "caption_dialect_mismatch"
    ]
    assert dialect_issues, (
        "readiness must tell the user that a krea2 project is about to write "
        f"Booru tag captions; got codes {[i.code for i in report.issues]}"
    )
    issue = dialect_issues[0]
    # It informs, it never blocks: nothing may refuse the user's own text.
    assert issue.severity == "warning"
    assert report.summary.blocker_count == 0
    assert report.summary.status == "warnings"
    assert "krea2" in issue.message
    assert issue.action


def test_readiness_stays_quiet_when_a_krea2_project_carries_prose_captions(
    test_client,
    tmp_path: Path,
) -> None:
    source = tmp_path / "krea2-prose-readiness.png"
    Image.new("RGB", (8, 8)).save(source)
    project_id, revision = _krea2_project_id_and_revision(test_client, tmp_path, source)

    report = _readiness_report(DatasetReadinessRequest(
        image_paths=[str(source)],
        output_folder=str(tmp_path / "krea2-prose-readiness-out"),
        naming_pattern="{filename}",
        content_mode="tags",
        overwrite_policy="unique",
        dataset_project_id=project_id,
        dataset_project_revision=revision,
        annotation_selections={
            str(source.resolve()): _frozen_tag_caption(PROSE_CAPTION),
        },
    ))

    assert [issue.code for issue in report.issues if "dialect" in issue.code] == []
    assert report.summary.status == "ready"


def test_a_warned_caption_is_still_exported_in_full(
    test_client,
    tmp_path: Path,
    authorize_legacy_dataset_exports,
) -> None:
    """The warning must cost the user a notice, never a character of caption."""
    source = tmp_path / "warned-export.png"
    Image.new("RGB", (8, 8)).save(source)
    resolved = str(source.resolve())
    output = tmp_path / "warned-export-out"

    response = test_client.post("/api/dataset/export", json={
        "image_paths": [str(source)],
        "output_folder": str(output),
        "naming_pattern": "{filename}",
        "content_mode": "tags",
        "overwrite_policy": "unique",
        "image_types": {resolved: "nl"},
        "image_nl_overrides": {resolved: TAG_LIST_CAPTION},
    })

    assert response.status_code == 200, response.text
    assert response.json()["exported"] == 1
    written = (output / "warned-export.txt").read_text(encoding="utf-8")
    assert written == TAG_LIST_CAPTION


def test_export_preview_shows_the_dialect_problem_beside_the_caption(
    test_client,
    tmp_path: Path,
) -> None:
    """The preview is the WYSIWYG surface, so it must carry the notice too."""
    source = tmp_path / "krea2-preview.png"
    Image.new("RGB", (8, 8)).save(source)
    source.with_suffix(".txt").write_text(TAG_LIST_CAPTION, encoding="utf-8")
    project_id, revision = _krea2_project_id_and_revision(test_client, tmp_path, source)

    response = test_client.post("/api/dataset/export-preview", json={
        "image_paths": [str(source)],
        "content_mode": "tags",
        "limit": 10,
        "dataset_project_id": project_id,
        "dataset_project_revision": revision,
        "annotation_selections": {
            str(source.resolve()): _frozen_tag_caption(TAG_LIST_CAPTION),
        },
    })

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["caption"] == TAG_LIST_CAPTION
    assert item["caption_format"] == "tags"
    assert [advisory["code"] for advisory in item["caption_advisories"]] == [
        "caption_dialect_mismatch",
    ]
    assert item["caption_advisories"][0]["convert"] == "tags_to_natural"


def test_export_preview_says_nothing_when_there_is_nothing_to_say(
    test_client,
    tmp_path: Path,
) -> None:
    source = tmp_path / "plain-preview.png"
    Image.new("RGB", (8, 8)).save(source)
    source.with_suffix(".txt").write_text(TAG_LIST_CAPTION, encoding="utf-8")

    response = test_client.post("/api/dataset/export-preview", json={
        "image_paths": [str(source)],
        "content_mode": "tags",
        "limit": 10,
    })

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["caption_advisories"] == []
    assert item["caption_format"] == "tags"


def test_readiness_stays_silent_for_a_project_without_a_dialect_opinion(
    test_client,
    tmp_path: Path,
) -> None:
    source = tmp_path / "neutral-readiness.png"
    Image.new("RGB", (8, 8)).save(source)
    source.with_suffix(".txt").write_text(TAG_LIST_CAPTION, encoding="utf-8")

    report = _readiness_report(DatasetReadinessRequest(
        image_paths=[str(source)],
        output_folder=str(tmp_path / "neutral-readiness-out"),
        naming_pattern="{filename}",
        content_mode="tags",
        overwrite_policy="unique",
    ))

    assert [issue.code for issue in report.issues if "dialect" in issue.code] == []
    assert report.summary.blocker_count == 0


# ------------------------------------------------------------------ #
# Consumer 2 — NL composition mode over a tag-list source
# ------------------------------------------------------------------ #


def _dataset_compose(record: dict[str, object], caption_type: str, sink=None) -> str:
    return dataset_captions._compose_nl_caption(
        "1girl, solo",
        record,
        5,
        "/x/y.png",
        content_mode="tags",
        types_int={5: caption_type},
        types_path={},
        nl_overrides_int={},
        nl_overrides_path={},
        advisories=sink,
    )


def test_nl_composition_over_a_tag_list_source_is_reported_not_silent() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": TAG_LIST_CAPTION}, "nl", sink)

    assert sink, (
        "asking for prose and receiving a tag dump must be reported; a silent "
        "substitution gives the user wrong output they cannot see"
    )
    advisory = sink[0]
    assert advisory.code == "nl_compose_over_tag_source"
    assert advisory.caption_format == "tags"
    # Nothing is discarded or trimmed: the caption is emitted in full.
    assert out == TAG_LIST_CAPTION


def test_nl_composition_over_a_prose_source_reports_nothing() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": PROSE_CAPTION}, "nl", sink)

    assert sink == []
    assert out == PROSE_CAPTION


def test_nl_composition_over_an_unplaceable_source_is_not_called_a_mismatch() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": UNPLACEABLE_CAPTION}, "nl", sink)

    assert sink == [], (
        "'unknown' is not evidence of a tag dump; warning here would train the "
        "user to ignore the warning"
    )
    assert out == UNPLACEABLE_CAPTION


def test_nl_composition_over_an_empty_source_reports_nothing() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": "", "ai_caption": ""}, "nl", sink)

    assert sink == []
    assert out == "1girl, solo"


def test_both_mode_over_a_tag_list_source_is_reported_too() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": TAG_LIST_CAPTION}, "both", sink)

    assert [advisory.code for advisory in sink] == ["nl_compose_over_tag_source"]
    assert out == f"1girl, solo, {TAG_LIST_CAPTION}"


def test_mixed_nl_source_is_reported_as_partly_tags() -> None:
    sink: list[object] = []
    out = _dataset_compose({"nl_caption": MIXED_CAPTION}, "nl", sink)

    assert [advisory.code for advisory in sink] == ["nl_compose_source_partly_tags"]
    assert out == " ".join(MIXED_CAPTION.split())


def test_compose_advisory_needs_a_composition_mode_that_asks_for_prose() -> None:
    for caption_type in ("booru", "", "tags"):
        assert nl_compose_advisory(caption_type, "tags") is None


def test_readiness_warns_when_nl_composition_runs_over_a_tag_list_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "nl-over-tags.png"
    Image.new("RGB", (8, 8)).save(source)
    source.with_suffix(".txt").write_text(TAG_LIST_CAPTION, encoding="utf-8")
    resolved = str(source.resolve())

    report = _readiness_report(DatasetReadinessRequest(
        image_paths=[str(source)],
        output_folder=str(tmp_path / "nl-over-tags-out"),
        naming_pattern="{filename}",
        content_mode="tags",
        overwrite_policy="unique",
        image_types={resolved: "nl"},
        image_nl_overrides={resolved: TAG_LIST_CAPTION},
    ))

    codes = [issue.code for issue in report.issues]
    assert "nl_compose_over_tag_source" in codes, codes
    assert report.summary.blocker_count == 0
    assert report.summary.status == "warnings"


# ------------------------------------------------------------------ #
# Consumer 3 — the session path has no database column to read
# ------------------------------------------------------------------ #


def _write_source(tmp_path: Path, name: str, caption: str | None) -> Path:
    path = tmp_path / name
    Image.new("RGB", (8, 8)).save(path)
    if caption is not None:
        path.with_suffix(".txt").write_text(caption, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        (TAG_LIST_CAPTION, "tags"),
        (PROSE_CAPTION, "natural"),
        (MIXED_CAPTION, "mixed"),
        (UNPLACEABLE_CAPTION, "unknown"),
        ("", None),
        (None, None),
    ],
)
def test_session_items_derive_the_format_from_the_text_read_off_disk(
    tmp_path: Path,
    caption: str | None,
    expected: str | None,
) -> None:
    path = _write_source(tmp_path, "session.png", caption)

    manifest_item = _manifest_item_for_path(str(path), 0)
    session_item = _session_item_for_path(path, scan_index=0)

    assert session_item is not None
    for item in (manifest_item, session_item):
        assert item["sidecar_caption_format"] == expected
        # The caption text still arrives whole; the marker rides beside it.
        assert item["sidecar_caption"] == caption


def test_session_item_flags_a_mixed_caption_for_the_editor(tmp_path: Path) -> None:
    path = _write_source(tmp_path, "mixed-session.png", MIXED_CAPTION)

    item = _session_item_for_path(path, scan_index=0)

    assert item is not None
    assert item["sidecar_caption_format"] == "mixed"
    assert item["sidecar_caption"] == MIXED_CAPTION


def test_folder_scan_reports_the_caption_format_per_item(
    test_client,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "scan-formats"
    folder.mkdir()
    _write_source(folder, "tagged.png", TAG_LIST_CAPTION)
    _write_source(folder, "prose.png", PROSE_CAPTION)

    response = test_client.post("/api/dataset/folder-scan", json={
        "folder_path": str(folder),
        "recursive": False,
        "limit": 10,
    })

    assert response.status_code == 200, response.text
    items = {item["filename"]: item for item in response.json()["items"]}
    assert items["tagged.png"]["sidecar_caption_format"] == "tags"
    assert items["tagged.png"]["sidecar_caption"] == TAG_LIST_CAPTION
    assert items["prose.png"]["sidecar_caption_format"] == "natural"
    assert items["prose.png"]["sidecar_caption"] == PROSE_CAPTION


# ------------------------------------------------------------------ #
# Consumer 5 — the NL twin sidecar should use a prose sidecar it already has
# ------------------------------------------------------------------ #


def test_nl_twin_prefers_a_prose_sidecar_over_the_booru_fallback() -> None:
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "",
            "sidecar_caption": PROSE_CAPTION,
            "sidecar_caption_format": "natural",
            "ai_caption": TAG_LIST_CAPTION,
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={},
    )

    assert out == " ".join(PROSE_CAPTION.split())


def test_nl_twin_ignores_a_tag_list_sidecar_and_keeps_the_old_fallback() -> None:
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "",
            "sidecar_caption": TAG_LIST_CAPTION,
            "sidecar_caption_format": "tags",
            "ai_caption": "a stored fused caption",
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={},
    )

    assert out == "a stored fused caption"


def test_nl_twin_still_prefers_the_dedicated_nl_column() -> None:
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "the dedicated sentence",
            "sidecar_caption": PROSE_CAPTION,
            "sidecar_caption_format": "natural",
            "ai_caption": "",
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={},
    )

    assert out == "the dedicated sentence"


def test_nl_twin_derives_the_format_when_the_marker_is_absent() -> None:
    """A row written before migration 044 has no marker but still has text."""
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "",
            "sidecar_caption": PROSE_CAPTION,
            "sidecar_caption_format": None,
            "ai_caption": TAG_LIST_CAPTION,
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={},
    )

    assert out == " ".join(PROSE_CAPTION.split())


def test_nl_twin_does_not_promote_a_sidecar_the_classifier_cannot_place() -> None:
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "",
            "sidecar_caption": UNPLACEABLE_CAPTION,
            "sidecar_caption_format": "unknown",
            "ai_caption": "a stored fused caption",
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={},
    )

    assert out == "a stored fused caption"


def test_an_explicit_empty_nl_override_still_suppresses_everything() -> None:
    out = tag_export_captions._compose_nl_for_image(
        "1girl, solo",
        {
            "nl_caption": "stored",
            "sidecar_caption": PROSE_CAPTION,
            "sidecar_caption_format": "natural",
            "ai_caption": "",
        },
        7,
        content_mode="tags",
        image_types={7: "nl"},
        nl_overrides={7: ""},
    )

    assert out == "1girl, solo"
