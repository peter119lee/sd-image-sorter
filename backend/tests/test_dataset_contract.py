"""Full-shape contract tests for the four core Dataset Maker endpoints.

Existing dataset tests assert individual fields (``body["status"]``,
``body["items"][0]["caption"]``, …) but never pin the COMPLETE response
schema. That left room for silent drift — e.g. docs/API.md claimed the
export-preview response used ``rows`` when the real field is ``items``.

These tests assert the full set of top-level keys + item sub-keys for:

  * POST /api/dataset/export
  * POST /api/dataset/export-preview
  * POST /api/dataset/audit
  * POST /api/dataset/folder-scan

If a future change adds/removes/renames a field, the matching test fails
here instead of leaking into the frontend as a runtime bug.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from services.dataset_export.models import DatasetExportRequest


pytestmark = pytest.mark.usefixtures("authorize_legacy_dataset_exports")


# ----------------------------- shared fixtures -----------------------------

@pytest.fixture
def contract_images(test_db, tmp_path: Path):
    """Two DB-backed images with tags, plus the output folder."""
    import database as db

    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    image_ids = []
    for name in ("alpha.png", "beta.png"):
        path = src / name
        Image.new("RGB", (32, 32), color=(50, 100, 150)).save(path)
        image_id = db.add_image(path=str(path), filename=name)
        db.add_tags(image_id, [
            {"tag": "1girl", "confidence": 0.9},
            {"tag": "solo", "confidence": 0.8},
        ])
        image_ids.append(image_id)
    return {"image_ids": image_ids, "out": out, "src": src}


# ----------------------------- export -----------------------------

EXPORT_RESPONSE_TOP_KEYS = {
    "status", "exported", "skipped", "error_count", "output_folder",
    "output_mode", "items", "total_items", "items_truncated", "error_messages",
    "warnings",
    # Phase 4 masked training: mask sidecar counters (masks_missing is
    # informational — no mask means "train the whole image", never a failure).
    "masks_written", "masks_missing",
    # Trainer handoff (roadmap #2): path of the generated kohya
    # dataset_config.toml, null unless trainer_config="kohya_toml".
    "trainer_config_path",
    "package_status", "package_run_id", "package_manifest_path",
}
EXPORT_ITEM_KEYS = {
    "image_id", "src_image_path", "dst_image_path", "dst_caption_path",
    "skipped_reason", "error",
}


def test_export_proof_fields_are_strict_optional_hex_contract() -> None:
    request = DatasetExportRequest.model_validate({})
    assert request.readiness_report_id is None
    assert request.readiness_input_fingerprint is None
    with pytest.raises(ValidationError):
        DatasetExportRequest.model_validate({"readiness_report_id": "not-a-report"})
    with pytest.raises(ValidationError):
        DatasetExportRequest.model_validate({
            "readiness_input_fingerprint": "A" * 64,
        })


def test_export_response_shape_is_pinned(test_client, contract_images):
    out = contract_images["out"]
    resp = test_client.post("/api/dataset/export", json={
        "image_ids": contract_images["image_ids"],
        "output_folder": str(out),
        "naming_pattern": "{filename}",
        "image_op": "copy",
        "overwrite_policy": "unique",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level keys: exactly the contract set, no more, no less.
    assert set(body.keys()) == EXPORT_RESPONSE_TOP_KEYS, (
        f"export response top-level keys drifted: {set(body.keys())}"
    )
    assert body["status"] in {"ok", "partial", "failed", "cancelled"}
    assert isinstance(body["exported"], int)
    assert isinstance(body["skipped"], int)
    assert isinstance(body["error_count"], int)
    assert isinstance(body["output_folder"], str)
    assert isinstance(body["output_mode"], str)
    assert isinstance(body["items"], list)
    assert isinstance(body["total_items"], int)
    assert isinstance(body["items_truncated"], bool)
    assert isinstance(body["error_messages"], list)

    # Every item carries exactly the contract sub-keys.
    for item in body["items"]:
        assert set(item.keys()) == EXPORT_ITEM_KEYS, (
            f"export item keys drifted: {set(item.keys())}"
        )
        assert isinstance(item["image_id"], int)
        # src_image_path / dst_image_path / dst_caption_path are str | None
        for path_field in ("src_image_path", "dst_image_path", "dst_caption_path"):
            assert item[path_field] is None or isinstance(item[path_field], str)
        assert item["skipped_reason"] is None or isinstance(item["skipped_reason"], str)
        assert item["error"] is None or isinstance(item["error"], str)


# ----------------------------- export-preview -----------------------------

PREVIEW_RESPONSE_TOP_KEYS = {
    "total", "returned", "items_truncated", "content_mode",
    "output_mode", "sidecar_extension", "items",
}
PREVIEW_ITEM_KEYS = {
    "index", "image_id", "abs_path", "filename", "thumbnail_url",
    "output_image_name", "output_caption_name", "output_image_path",
    "output_caption_path", "caption", "ai_caption", "nl_caption",
    # Format of the caption above, plus anything about it that needs attention
    # for this project's target model or the requested composition mode. Both
    # are advisory; ``caption`` is unaffected by them.
    "caption_format", "caption_advisories",
    "skipped_reason", "error",
}


def test_export_preview_response_shape_is_pinned(test_client, contract_images):
    out = contract_images["out"]
    resp = test_client.post("/api/dataset/export-preview", json={
        "image_ids": contract_images["image_ids"],
        "output_folder": str(out),
        "naming_pattern": "preview_{index:03d}",
        "content_mode": "tags",
        "limit": 10,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == PREVIEW_RESPONSE_TOP_KEYS, (
        f"export-preview top-level keys drifted: {set(body.keys())}"
    )
    # The field is ``items``, NOT ``rows`` — this is the documented drift
    # the contract test exists to prevent.
    assert "rows" not in body
    assert "skipped" not in body
    assert "error_count" not in body

    assert isinstance(body["total"], int)
    assert isinstance(body["returned"], int)
    assert isinstance(body["items_truncated"], bool)
    assert isinstance(body["content_mode"], str)
    assert isinstance(body["output_mode"], str)
    assert isinstance(body["sidecar_extension"], str)
    assert isinstance(body["items"], list)
    assert body["returned"] == len(body["items"])

    for item in body["items"]:
        assert set(item.keys()) == PREVIEW_ITEM_KEYS, (
            f"export-preview item keys drifted: {set(item.keys())}"
        )
        assert isinstance(item["index"], int)
        assert isinstance(item["image_id"], int)
        assert isinstance(item["filename"], str)
        assert isinstance(item["output_image_name"], str)
        assert isinstance(item["output_caption_name"], str)
        assert isinstance(item["caption"], str)
        assert isinstance(item["ai_caption"], str)
        assert isinstance(item["nl_caption"], str)


# ----------------------------- audit -----------------------------

AUDIT_SUMMARY_KEYS = {
    "total", "low_quality_count", "duplicate_pairs", "untagged_count",
    "small_count", "missing_count", "avg_aesthetic",
    "near_duplicate_check_limited", "near_duplicate_checked",
    "near_duplicate_attempted", "near_duplicate_hashes",
    "near_duplicate_failed", "near_duplicate_unavailable_count",
    "near_duplicate_error",
}
AUDIT_ITEM_KEYS = {
    "image_id", "abs_path", "filename", "width", "height",
    "tag_count", "aesthetic_score", "phash_hex", "flags",
}
AUDIT_TOP_KEYS = {"summary", "items", "items_truncated", "items_returned", "duplicate_groups"}


def test_audit_response_shape_is_pinned(test_client, contract_images):
    resp = test_client.post("/api/dataset/audit", json={
        "image_ids": contract_images["image_ids"],
        "dim_min": 16,
        "enable_aesthetic": False,
        "enable_phash": False,
        "enable_untagged": True,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == AUDIT_TOP_KEYS, (
        f"audit top-level keys drifted: {set(body.keys())}"
    )
    summary = body["summary"]
    assert set(summary.keys()) == AUDIT_SUMMARY_KEYS, (
        f"audit summary keys drifted: {set(summary.keys())}"
    )
    assert isinstance(summary["total"], int)
    assert isinstance(summary["low_quality_count"], int)
    assert isinstance(summary["duplicate_pairs"], int)
    assert isinstance(summary["untagged_count"], int)
    assert isinstance(summary["small_count"], int)
    assert isinstance(summary["missing_count"], int)
    assert summary["avg_aesthetic"] is None or isinstance(summary["avg_aesthetic"], (int, float))
    assert isinstance(summary["near_duplicate_check_limited"], bool)
    assert isinstance(summary["near_duplicate_checked"], bool)
    assert isinstance(summary["near_duplicate_attempted"], int)
    assert isinstance(summary["near_duplicate_hashes"], int)
    assert isinstance(summary["near_duplicate_failed"], int)
    assert isinstance(summary["near_duplicate_unavailable_count"], int)
    assert summary["near_duplicate_error"] is None or isinstance(summary["near_duplicate_error"], str)

    assert isinstance(body["items"], list)
    assert isinstance(body["items_truncated"], bool)
    assert isinstance(body["items_returned"], int)
    assert body["items_returned"] == len(body["items"])

    for item in body["items"]:
        assert set(item.keys()) == AUDIT_ITEM_KEYS, (
            f"audit item keys drifted: {set(item.keys())}"
        )
        assert isinstance(item["image_id"], int)
        assert isinstance(item["abs_path"], str)
        assert isinstance(item["filename"], str)
        assert item["width"] is None or isinstance(item["width"], int)
        assert item["height"] is None or isinstance(item["height"], int)
        assert isinstance(item["tag_count"], int)
        assert item["aesthetic_score"] is None or isinstance(item["aesthetic_score"], (int, float))
        assert item["phash_hex"] is None or isinstance(item["phash_hex"], str)
        assert isinstance(item["flags"], list)

    assert isinstance(body["duplicate_groups"], list)
    for group in body["duplicate_groups"]:
        assert set(group.keys()) == {"phash_hex", "image_ids", "abs_paths"}, (
            f"duplicate_group keys drifted: {set(group.keys())}"
        )
        assert isinstance(group["phash_hex"], str)
        assert isinstance(group["image_ids"], list)
        assert isinstance(group["abs_paths"], list)


# ----------------------------- folder-scan -----------------------------

FOLDER_SCAN_TOP_KEYS = {
    "folder_path", "items", "total_files_seen", "skipped_unreadable",
    "truncated", "scan_token", "offset", "next_offset", "has_more", "page_size",
}
FOLDER_SCAN_ITEM_KEYS = {
    "ds_id", "abs_path", "filename", "width", "height",
    "mtime", "size", "thumb_b64", "scan_index",
    "source_kind", "sidecar_capability", "sidecar_caption",
    # Format of sidecar_caption (tags/natural/mixed/unknown, or null when there
    # is no caption). Derived from the text read off disk because this path has
    # no images row to read migration 044's column from.
    "sidecar_caption_format",
}


def test_folder_scan_response_shape_is_pinned(test_client, tmp_path: Path):
    folder = tmp_path / "scanme"
    folder.mkdir()
    for name in ("a.png", "b.png"):
        Image.new("RGB", (16, 16)).save(folder / name)

    resp = test_client.post("/api/dataset/folder-scan", json={
        "folder_path": str(folder),
        "recursive": False,
        "limit": 10,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == FOLDER_SCAN_TOP_KEYS, (
        f"folder-scan top-level keys drifted: {set(body.keys())}"
    )
    # Paging contract fields the frontend depends on for "load more".
    assert isinstance(body["scan_token"], str)
    assert isinstance(body["offset"], int)
    assert body["next_offset"] is None or isinstance(body["next_offset"], int)
    assert isinstance(body["has_more"], bool)
    assert isinstance(body["page_size"], int)
    assert isinstance(body["items"], list)
    assert isinstance(body["total_files_seen"], int)
    assert isinstance(body["skipped_unreadable"], int)
    assert isinstance(body["truncated"], bool)

    for item in body["items"]:
        assert set(item.keys()) == FOLDER_SCAN_ITEM_KEYS, (
            f"folder-scan item keys drifted: {set(item.keys())}"
        )
        assert isinstance(item["ds_id"], str)
        assert isinstance(item["abs_path"], str)
        assert isinstance(item["filename"], str)
        assert item["width"] is None or isinstance(item["width"], int)
        assert item["height"] is None or isinstance(item["height"], int)
        assert isinstance(item["mtime"], (int, float))
        assert isinstance(item["size"], int)
        assert isinstance(item["thumb_b64"], str)
        assert item["scan_index"] is None or isinstance(item["scan_index"], int)
        assert isinstance(item["source_kind"], str)
        assert isinstance(item["sidecar_capability"], str)
        assert item["sidecar_caption"] is None or isinstance(item["sidecar_caption"], str)
        assert item["sidecar_caption_format"] in (
            None, "tags", "natural", "mixed", "unknown",
        )
