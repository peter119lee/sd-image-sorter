"""Synthetic Pillow and export contracts for mask-driven subject cropping."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

import database as db
from services import mask_service
from services.dataset_crop_service import apply_subject_crop, compute_subject_crop_box
from services.dataset_export.artifacts import _validate_export_request_read_only
from services.dataset_export.engine import export_dataset
from services.dataset_export.models import (
    DatasetExportPreviewRequest,
    DatasetExportRequest,
    DatasetReadinessRequest,
)
from services.dataset_export.readiness import run_dataset_readiness


SUBJECT_CROP_DISABLED = {
    "enabled": False,
    "alpha_threshold": 1,
    "padding_percent": 0,
    "background_mode": "keep_background",
    "solid_color": "#000000",
}


def _crop_settings(
    background_mode: str,
    *,
    alpha_threshold: int = 1,
    padding_percent: int = 0,
    solid_color: str = "#000000",
) -> dict[str, object]:
    return {
        "enabled": True,
        "alpha_threshold": alpha_threshold,
        "padding_percent": padding_percent,
        "background_mode": background_mode,
        "solid_color": solid_color,
    }


def _stage_library_image(
    tmp_path: Path,
    *,
    suffix: str = ".png",
) -> tuple[int, Path]:
    source_dir = tmp_path / "source"
    source_dir.mkdir(exist_ok=True)
    source = source_dir / f"subject{suffix}"
    image = Image.new("RGB", (10, 8))
    image.putdata([
        (x * 20, y * 25, 100)
        for y in range(image.height)
        for x in range(image.width)
    ])
    image.save(source)
    image_id = int(db.add_image(path=str(source), filename=source.name))
    db.add_tags(image_id, [{"tag": "subject", "confidence": 0.99}])
    return image_id, source


def _write_mask(
    masks_dir: Path,
    image_id: int,
    *,
    size: tuple[int, int] = (10, 8),
    fill: int = 0,
) -> Path:
    masks_dir.mkdir(parents=True, exist_ok=True)
    path = masks_dir / f"{image_id}.png"
    mask = Image.new("L", size, color=fill)
    mask.save(path)
    return path


def test_compute_subject_crop_box_threshold_padding_and_clamp() -> None:
    mask = Image.new("L", (10, 8), color=0)
    mask.paste(127, (2, 1, 8, 7))
    mask.paste(200, (3, 2, 7, 6))

    assert compute_subject_crop_box(
        mask,
        alpha_threshold=128,
        padding_percent=50,
    ) == (1, 0, 9, 8)


def test_apply_subject_crop_preserves_soft_mask_in_all_background_modes() -> None:
    source = Image.new("RGB", (3, 1), color=(100, 0, 0))
    mask = Image.new("L", (3, 1))
    mask.putdata([1, 128, 255])

    keep_image, keep_mask, keep_box = apply_subject_crop(
        source,
        mask,
        alpha_threshold=1,
        padding_percent=0,
        background_mode="keep_background",
        solid_color="#000000",
    )
    transparent_image, transparent_mask, transparent_box = apply_subject_crop(
        source,
        mask,
        alpha_threshold=1,
        padding_percent=0,
        background_mode="transparent_rgba",
        solid_color="#000000",
    )
    solid_image, solid_mask, solid_box = apply_subject_crop(
        source,
        mask,
        alpha_threshold=1,
        padding_percent=0,
        background_mode="solid_color",
        solid_color="#000000",
    )

    assert keep_box == transparent_box == solid_box == (0, 0, 3, 1)
    assert list(keep_mask.get_flattened_data()) == [1, 128, 255]
    assert list(transparent_mask.get_flattened_data()) == [1, 128, 255]
    assert list(solid_mask.get_flattened_data()) == [1, 128, 255]
    assert keep_image.mode == "RGB"
    assert list(keep_image.get_flattened_data()) == [(100, 0, 0)] * 3
    assert transparent_image.mode == "RGBA"
    assert list(transparent_image.getchannel("A").get_flattened_data()) == [1, 128, 255]
    assert solid_image.mode == "RGB"
    assert [pixel[0] for pixel in solid_image.get_flattened_data()] == [0, 50, 100]


def test_solid_background_respects_existing_source_alpha() -> None:
    source = Image.new("RGBA", (1, 1), color=(255, 0, 0, 0))
    mask = Image.new("L", (1, 1), color=255)

    solid_image, _, _ = apply_subject_crop(
        source,
        mask,
        alpha_threshold=1,
        padding_percent=0,
        background_mode="solid_color",
        solid_color="#0000FF",
    )

    assert solid_image.getpixel((0, 0)) == (0, 0, 255)


def test_default_export_is_byte_for_byte_copy_without_opening_pillow(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    source_bytes = source.read_bytes()
    source_stat = source.stat()
    output = tmp_path / "default-copy"
    monkeypatch.setattr(
        Image,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled subject crop must not open Pillow")
        ),
    )

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        image_overrides={str(image_id): "caption stays exact"},
    ))

    exported = output / source.name
    assert result.status == "ok"
    assert exported.read_bytes() == source_bytes
    assert source.read_bytes() == source_bytes
    assert source.stat().st_mtime_ns == source_stat.st_mtime_ns


def test_subject_crop_export_aligns_image_mask_and_preserves_caption_and_source(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    stored_mask = _write_mask(masks_dir, image_id)
    with Image.open(stored_mask) as opened:
        mask = opened.copy()
    mask.paste(96, (2, 1, 8, 7))
    mask.putpixel((4, 3), 48)
    mask.save(stored_mask)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    source_stat = source.stat()
    output = tmp_path / "cropped"

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        image_overrides={str(image_id): "exact caption, unchanged"},
        mask_export="kohya",
        subject_crop=_crop_settings(
            "keep_background",
            alpha_threshold=32,
            padding_percent=0,
        ),
    ))

    exported_image = output / source.name
    exported_mask = output / "mask" / source.name
    with Image.open(exported_image) as image_result, Image.open(exported_mask) as mask_result:
        assert image_result.size == mask_result.size == (6, 6)
        assert mask_result.mode == "L"
        assert 48 in list(mask_result.get_flattened_data())
        assert 96 in list(mask_result.get_flattened_data())
    assert result.status == "ok"
    assert result.masks_written == 1
    assert (output / "subject.txt").read_text(encoding="utf-8") == "exact caption, unchanged"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert source.stat().st_mtime_ns == source_stat.st_mtime_ns


@pytest.mark.parametrize(
    ("mask_state", "expected_error"),
    (
        ("missing", "stored training mask is missing"),
        ("empty_file", "stored training mask is empty"),
        ("size_mismatch", "mask size"),
        ("empty_subject", "no subject pixels"),
    ),
)
def test_subject_crop_rejects_invalid_mask_before_writing_image_or_caption(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mask_state: str,
    expected_error: str,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    if mask_state == "empty_file":
        masks_dir.mkdir()
        (masks_dir / f"{image_id}.png").write_bytes(b"")
    elif mask_state == "size_mismatch":
        _write_mask(masks_dir, image_id, size=(4, 4), fill=255)
    elif mask_state == "empty_subject":
        _write_mask(masks_dir, image_id, fill=10)
    output = tmp_path / f"invalid-{mask_state}"

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        image_overrides={str(image_id): "must not be written"},
        mask_export="onetrainer",
        subject_crop=_crop_settings(
            "keep_background",
            alpha_threshold=20,
        ),
    ))

    assert result.status == "failed"
    assert result.error_count == 1
    assert expected_error in result.error_messages[0]
    assert not (output / source.name).exists()
    assert not (output / "subject.txt").exists()
    assert not (output / "mask" / source.name).exists()


@pytest.mark.parametrize(
    ("request_updates", "expected_error"),
    (
        ({"image_paths": ["C:/local/source.png"], "image_ids": []}, "library image_ids"),
        ({"image_ids": [0]}, "positive library image_id"),
        ({"image_op": "move"}, "image_op='copy'"),
        ({"output_mode": "beside_image", "output_folder": ""}, "output_mode='folder'"),
        ({"dataset_scan_tokens": [{"scan_token": "a" * 32}]}, "dataset_scan_tokens"),
        ({"mask_export": "none"}, "mask_export"),
        ({"trainer_config": "kohya_toml"}, "trainer_config='none'"),
    ),
)
def test_subject_crop_rejects_unsupported_export_shapes(
    tmp_path: Path,
    request_updates: dict[str, object],
    expected_error: str,
) -> None:
    payload: dict[str, object] = {
        "image_ids": [1],
        "output_folder": str(tmp_path / "out"),
        "mask_export": "kohya",
        "subject_crop": _crop_settings("keep_background"),
    }
    payload.update(request_updates)
    request = DatasetExportRequest.model_validate(payload)

    with pytest.raises(HTTPException, match=expected_error):
        _validate_export_request_read_only(request)


def test_subject_crop_rejects_transparent_jpeg_before_outputs(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, source = _stage_library_image(tmp_path, suffix=".jpg")
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "transparent-jpeg"

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        image_overrides={str(image_id): "must not be written"},
        mask_export="onetrainer",
        subject_crop=_crop_settings("transparent_rgba"),
    ))

    assert result.status == "failed"
    assert "transparent_rgba cannot be exported to JPEG" in result.error_messages[0]
    assert not (output / source.name).exists()
    assert not (output / "subject.txt").exists()


def test_subject_crop_request_and_preview_defaults_are_backward_compatible() -> None:
    export_request = DatasetExportRequest(
        image_ids=[1],
        output_folder="C:/output",
    )
    preview_request = DatasetExportPreviewRequest(image_ids=[1])

    assert export_request.subject_crop.model_dump() == SUBJECT_CROP_DISABLED
    assert preview_request.subject_crop.model_dump() == SUBJECT_CROP_DISABLED
    with pytest.raises(ValidationError):
        DatasetExportRequest(
            image_ids=[1],
            output_folder="C:/output",
            subject_crop={**SUBJECT_CROP_DISABLED, "alpha_threshold": 0},
        )


def test_subject_crop_readiness_blocks_an_empty_subject_mask(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, _ = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=0)
    request = DatasetReadinessRequest(
        image_ids=[image_id],
        output_folder=str(tmp_path / "readiness-output"),
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background", alpha_threshold=1),
    )

    report = run_dataset_readiness(
        request,
        readiness_report_id="subject-crop-readiness",
        progress_callback=lambda _processed, _total, _message: None,
        cancellation_requested=lambda: False,
    )

    assert report.summary.status == "blocked"
    assert any(issue.code == "subject_crop_mask_invalid" for issue in report.issues)


@pytest.mark.parametrize("alias_kind", ("same_path", "hardlink"))
def test_subject_crop_never_replaces_its_source_or_a_filesystem_alias(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    naming_pattern = "{filename}"
    if alias_kind == "hardlink":
        alias = source.parent / "source-alias.png"
        os.link(source, alias)
        naming_pattern = "source-alias"

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(source.parent),
        naming_pattern=naming_pattern,
        overwrite_policy="overwrite",
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "failed"
    assert result.exported == 0
    assert "same file as its source" in result.error_messages[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_plain_mask_copy_never_replaces_its_source(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(source.parent),
        naming_pattern="{filename}",
        overwrite_policy="overwrite",
        mask_export="kohya",
    ))

    assert result.status == "failed"
    assert result.exported == 0
    assert "same file as its source" in result.error_messages[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_readiness_blocks_plain_copy_source_destination_alias(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)

    report = run_dataset_readiness(
        DatasetExportRequest(
            image_ids=[image_id],
            output_folder=str(source.parent),
            naming_pattern="{filename}",
            overwrite_policy="overwrite",
            mask_export="kohya",
        ),
        readiness_report_id="plain-copy-alias-readiness",
        progress_callback=lambda _processed, _total, _message: None,
        cancellation_requested=lambda: False,
    )

    assert report.summary.status == "blocked"
    assert any(
        issue.code == "source_destination_alias"
        for issue in report.issues
    )


def test_mask_export_move_is_rejected_before_writing(tmp_path: Path) -> None:
    with pytest.raises(HTTPException, match="mask_export.*image_op='copy'"):
        _validate_export_request_read_only(DatasetExportRequest(
            image_ids=[1],
            output_folder=str(tmp_path / "output"),
            image_op="move",
            mask_export="kohya",
        ))


def test_subject_crop_unique_renames_the_whole_pair_when_mask_target_exists(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "unique-mask"
    existing_mask = output / "mask" / "subject.png"
    existing_mask.parent.mkdir(parents=True)
    existing_mask.write_bytes(b"manual-mask")

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        overwrite_policy="unique",
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "ok"
    assert existing_mask.read_bytes() == b"manual-mask"
    assert (output / "subject_1.png").exists()
    assert (output / "subject_1.txt").exists()
    assert (output / "mask" / "subject_1.png").exists()


def test_subject_crop_skip_skips_the_whole_pair_when_mask_target_exists(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "skip-mask"
    existing_mask = output / "mask" / "subject.png"
    existing_mask.parent.mkdir(parents=True)
    existing_mask.write_bytes(b"manual-mask")

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        overwrite_policy="skip",
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "ok"
    assert result.exported == 0
    assert result.skipped == 1
    assert existing_mask.read_bytes() == b"manual-mask"
    assert not (output / "subject.png").exists()
    assert not (output / "subject.txt").exists()


def test_subject_crop_mask_write_failure_is_not_reported_as_exported(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.dataset_export import engine

    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "mask-write-failure"
    original_write = engine._write_pillow_image_atomic

    def fail_mask_write(image: Image.Image, target: Path) -> None:
        if target.parent.name == "mask":
            raise OSError("forced mask write failure")
        original_write(image, target)

    monkeypatch.setattr(engine, "_write_pillow_image_atomic", fail_mask_write)

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "failed"
    assert result.exported == 0
    assert result.error_count == 1
    assert "forced mask write failure" in result.error_messages[0]
    assert result.items[0].error is not None
    assert not (output / "subject.png").exists()
    assert not (output / "subject.txt").exists()
    assert not (output / "mask" / "subject.png").exists()


def test_plain_mask_copy_failure_rolls_back_image_and_caption(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.dataset_export import engine

    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    stored_mask = _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "plain-mask-copy-failure"
    original_copy = engine.shutil.copy2

    def fail_mask_copy(source: str | Path, target: str | Path) -> str:
        if Path(source) == stored_mask:
            raise OSError("forced plain mask copy failure")
        return str(original_copy(source, target))

    monkeypatch.setattr(engine.shutil, "copy2", fail_mask_copy)

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        mask_export="kohya",
    ))

    assert result.status == "failed"
    assert result.exported == 0
    assert result.error_count == 1
    assert "forced plain mask copy failure" in result.error_messages[0]
    assert not (output / "subject.png").exists()
    assert not (output / "subject.txt").exists()
    assert not (output / "mask" / "subject.png").exists()


def test_subject_crop_publish_failure_restores_overwritten_row(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.dataset_export import engine

    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "overwrite-rollback"
    output.mkdir()
    existing_image = output / "subject.png"
    existing_caption = output / "subject.txt"
    existing_mask = output / "mask" / "subject.png"
    existing_mask.parent.mkdir()
    Image.new("RGB", (2, 2), color=(1, 2, 3)).save(existing_image)
    existing_caption.write_text("existing caption", encoding="utf-8")
    Image.new("L", (2, 2), color=77).save(existing_mask)
    original_bytes = {
        path: path.read_bytes()
        for path in (existing_image, existing_caption, existing_mask)
    }
    original_replace = engine.os.replace
    failed = False

    def fail_mask_publish(source: str, target: str) -> None:
        nonlocal failed
        if Path(target) == existing_mask and not failed:
            failed = True
            raise OSError("forced mask publish failure")
        original_replace(source, target)

    monkeypatch.setattr(engine.os, "replace", fail_mask_publish)

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        overwrite_policy="overwrite",
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "failed"
    for path, content in original_bytes.items():
        assert path.read_bytes() == content


def test_published_row_backup_cleanup_failure_is_a_structured_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.dataset_export import engine

    target = tmp_path / "subject.txt"
    staged = tmp_path / "staged.txt"
    target.write_bytes(b"old")
    staged.write_bytes(b"new")
    original_unlink = Path.unlink

    def fail_old_backup_cleanup(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.exists() and path.read_bytes() == b"old" and path != target:
            raise OSError("forced backup cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_old_backup_cleanup)

    with caplog.at_level("WARNING", logger=engine.__name__):
        warnings = engine._publish_staged_row([(staged, target)])

    assert target.read_bytes() == b"new"
    assert len(warnings) == 1
    assert warnings[0].code == "backup_cleanup_failed"
    assert warnings[0].backup_path
    assert warnings[0].error_type == "OSError"
    assert warnings[0].error == "forced backup cleanup failure"
    warning = next(
        record
        for record in caplog.records
        if record.message == "Dataset row backup cleanup failed"
    )
    assert warning.backup_path
    assert warning.error_type == "OSError"
    assert warning.error == "forced backup cleanup failure"


def test_backup_cleanup_warning_is_returned_by_the_export(
    test_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id, _source = _stage_library_image(tmp_path)
    masks_dir = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", masks_dir)
    _write_mask(masks_dir, image_id, fill=255)
    output = tmp_path / "cleanup-warning"
    output.mkdir()
    existing_image = output / "subject.png"
    existing_caption = output / "subject.txt"
    existing_mask = output / "mask" / "subject.png"
    existing_mask.parent.mkdir()
    Image.new("RGB", (2, 2), color=(1, 2, 3)).save(existing_image)
    existing_caption.write_text("old caption", encoding="utf-8")
    Image.new("L", (2, 2), color=77).save(existing_mask)
    original_unlink = Path.unlink

    def fail_caption_backup_cleanup(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        # Identified by what makes it a backup — a dot-prefixed sibling of the
        # caption target still holding the PREVIOUS caption. The former
        # ``.subject.txt.row-`` infix was only ``tempfile``'s random-name
        # prefix; row staging moved to utils.atomic_staging's deterministic
        # ``.tmp`` names when tempfile's Windows retry storm was removed, so
        # that infix was a value-of-the-moment and never the contract.
        if (
            path.suffix == ".txt"
            and path.name.startswith(f".{existing_caption.name}")
            and path.exists()
            and path.read_text(encoding="utf-8") == "old caption"
        ):
            raise OSError("forced caption backup cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_caption_backup_cleanup)

    result = export_dataset(DatasetExportRequest(
        image_ids=[image_id],
        output_folder=str(output),
        overwrite_policy="overwrite",
        mask_export="kohya",
        subject_crop=_crop_settings("keep_background"),
    ))

    assert result.status == "ok"
    assert result.exported == 1
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "backup_cleanup_failed"
    assert result.warnings[0].error == "forced caption backup cleanup failure"
    assert Path(result.warnings[0].backup_path).read_text(encoding="utf-8") == "old caption"
