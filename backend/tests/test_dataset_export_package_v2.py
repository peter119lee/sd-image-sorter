"""Integration contracts for verified Dataset Export Package v2."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Mapping, Sequence

import pytest
from PIL import Image


pytestmark = pytest.mark.usefixtures("authorize_legacy_dataset_exports")

import services.dataset_export_service as export_service
import services.dataset_export.engine as export_engine


def _make_source(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (24, 24), color=color).save(path)
    return path.resolve()


def _anima_payload(sources: list[Path], output_folder: Path) -> dict[str, object]:
    return {
        "image_paths": [str(source) for source in sources],
        "output_folder": str(output_folder),
        "naming_pattern": "train_{index:03d}",
        "image_op": "copy",
        "overwrite_policy": "unique",
        "content_mode": "tags",
        "image_overrides": {
            str(source): f"subject, sample_{index}"
            for index, source in enumerate(sources, start=1)
        },
        "trainer_config": "anima_lora_toml",
        "mask_export": "none",
    }


def _read_inventory(output_folder: Path) -> list[dict[str, object]]:
    inventory_path = output_folder / "export_inventory.jsonl"
    return [
        json.loads(line)
        for line in inventory_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rewrite_package_models(
    output_folder: Path,
    records: Sequence[object],
    manifest_updates: Mapping[str, object],
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    typed_records = [
        record
        if isinstance(record, DatasetPackageInventoryRecord)
        else DatasetPackageInventoryRecord.model_validate_json(json.dumps(record))
        for record in records
    ]
    inventory_content = "".join(
        f"{record.model_dump_json()}\n"
        for record in typed_records
    )
    inventory_path = output_folder / "export_inventory.jsonl"
    inventory_path.write_text(inventory_content, encoding="utf-8", newline="")
    manifest = read_package_manifest(output_folder)
    assert manifest.inventory is not None
    inventory = manifest.inventory.model_copy(update={
        "byte_size": len(inventory_content.encode("utf-8")),
        "sha256": hashlib.sha256(inventory_content.encode("utf-8")).hexdigest(),
        "record_count": len(typed_records),
    })
    updated = manifest.model_copy(update={
        "inventory": inventory,
        **dict(manifest_updates),
    })
    (output_folder / "export_manifest.json").write_text(
        updated.model_dump_json(),
        encoding="utf-8",
    )


def _verify_package(test_client, output_folder: Path, run_id: str) -> dict[str, object]:
    response = test_client.post(
        "/api/dataset/package-verifications",
        json={"output_folder": str(output_folder), "expected_run_id": run_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_anima_export_publishes_complete_v2_package_and_verifies(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (10, 20, 30))
    output_folder = tmp_path / "package"

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["package_status"] == "complete"
    assert len(body["package_run_id"]) == 32
    assert body["package_manifest_path"] == str(
        output_folder / "export_manifest.json"
    )

    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "sd-image-sorter.dataset-package"
    assert manifest["manifest_version"] == 2
    assert manifest["run_id"] == body["package_run_id"]
    assert manifest["package_status"] == "complete"
    assert manifest["trainer"]["id"] == "anima_lora"
    assert manifest["trainer"]["upstream_commit"] == (
        "13eaf97a3903405baa939d7cb4a524f8f3e11303"
    )

    records = _read_inventory(output_folder)
    assert len(records) == 1
    assert records[0]["disposition"] == "exported"
    outputs = records[0]["outputs"]
    assert {artifact["role"] for artifact in outputs} == {"image", "caption"}
    for artifact in outputs:
        artifact_path = output_folder / artifact["path"]
        assert artifact_path.stat().st_size == artifact["byte_size"]
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]

    verification = test_client.post(
        "/api/dataset/package-verifications",
        json={
            "output_folder": str(output_folder),
            "expected_run_id": body["package_run_id"],
        },
    )
    assert verification.status_code == 200, verification.text
    verified = verification.json()
    assert verified["status"] == "complete"
    assert verified["valid"] is True
    assert verified["checked_records"] == 1
    assert verified["checked_artifacts"] == 3
    assert verified["issues"] == []


def test_package_inventory_is_not_truncated_with_api_items(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources = [
        _make_source(tmp_path / f"source-{index}.png", (index, 30, 60))
        for index in range(1, 4)
    ]
    output_folder = tmp_path / "package"
    monkeypatch.setattr(export_service, "DATASET_EXPORT_RESPONSE_ITEM_LIMIT", 1)

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload(sources, output_folder),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items_truncated"] is True
    assert len(body["items"]) == 1
    assert body["package_status"] == "complete"
    assert len(_read_inventory(output_folder)) == 3


def test_unknown_manifest_blocks_before_config_or_image_mutation(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (20, 30, 40))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    manifest_path = output_folder / "export_manifest.json"
    manifest_path.write_text('{"belongs_to":"user"}', encoding="utf-8")
    config_path = output_folder / "dataset_config.toml"
    config_path.write_text("# Generated by SD Image Sorter\nold = true\n", encoding="utf-8")

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 409, response.text
    assert manifest_path.read_text(encoding="utf-8") == '{"belongs_to":"user"}'
    assert config_path.read_text(encoding="utf-8") == (
        "# Generated by SD Image Sorter\nold = true\n"
    )
    assert list(output_folder.glob("train_*.png")) == []


def test_unowned_inventory_blocks_before_image_mutation(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (25, 35, 45))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    inventory_path = output_folder / "export_inventory.jsonl"
    inventory_path.write_text('{"belongs_to":"user"}\n', encoding="utf-8")

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 409, response.text
    assert inventory_path.read_text(encoding="utf-8") == '{"belongs_to":"user"}\n'
    assert list(output_folder.glob("train_*.png")) == []


@pytest.mark.parametrize(
    "target_name",
    ["export_manifest.json", "export_inventory.jsonl"],
)
def test_broken_package_metadata_symlink_blocks_before_mutation(
    test_client,
    tmp_path: Path,
    target_name: str,
) -> None:
    source = _make_source(tmp_path / "source.png", (25, 36, 46))
    source_content = source.read_bytes()
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    target = output_folder / target_name
    os.symlink(tmp_path / f"missing-{target_name}", target)

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 409, response.text
    assert target.is_symlink()
    assert source.read_bytes() == source_content
    assert list(output_folder.glob("train_*")) == []
    assert (output_folder / "dataset_config.toml").exists() is False


def test_kohya_package_rejects_unowned_trainer_config_before_mutation(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (26, 36, 46))
    source_content = source.read_bytes()
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    config_path = output_folder / "dataset_config.toml"
    user_config = b"# User-maintained Kohya config\n[[datasets]]\n"
    config_path.write_bytes(user_config)
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = "kohya_toml"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 409, response.text
    assert config_path.read_bytes() == user_config
    assert source.read_bytes() == source_content
    assert list(output_folder.glob("train_*")) == []
    assert (output_folder / "export_manifest.json").exists() is False
    assert (output_folder / "export_inventory.jsonl").exists() is False


def test_trainer_package_rejects_move_without_touching_source(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (30, 40, 50))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["image_op"] = "move"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 400, response.text
    assert "image_op='copy'" in response.text
    assert source.exists()
    assert output_folder.exists() is False


def test_unlisted_trainable_file_prevents_complete_package(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (35, 45, 55))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    _make_source(output_folder / "unlisted.png", (1, 2, 3))

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "partial"
    assert body["package_status"] == "incomplete"
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["package_status"] == "incomplete"
    assert any("unlisted trainable artifact" in error for error in manifest["errors"])


def test_copy_content_drift_prevents_complete_package(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import engine

    source = _make_source(tmp_path / "source.png", (37, 47, 57))
    output_folder = tmp_path / "package"
    real_copy = engine.shutil.copy2

    def corrupt_copy(source_path: str, target_path: str) -> str:
        copied = real_copy(source_path, target_path)
        Path(target_path).write_bytes(b"corrupted after copy")
        return str(copied)

    monkeypatch.setattr(engine.shutil, "copy2", corrupt_copy)

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "partial"
    assert body["package_status"] == "incomplete"
    assert any(
        "does not match its source snapshot" in error
        for error in body["error_messages"]
    )


def test_caption_writer_drift_prevents_complete_package(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import engine

    source = _make_source(tmp_path / "source.png", (38, 48, 58))
    output_folder = tmp_path / "package"
    real_write = engine.write_package_text_atomic

    def write_corrupted_caption(
        target: Path,
        content: str,
        package_root: Path,
    ) -> None:
        assert content == "expected caption"
        real_write(target, "corrupted caption", package_root)

    monkeypatch.setattr(
        engine,
        "write_package_text_atomic",
        write_corrupted_caption,
    )
    payload = _anima_payload([source], output_folder)
    payload["image_overrides"] = {str(source): "expected caption"}

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["package_status"] == "incomplete"
    assert any(
        "caption artifact does not match rendered content" in error
        for error in body["error_messages"]
    )
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["package_status"] == "incomplete"


def test_package_verifier_reports_tampered_caption(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (40, 50, 60))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    caption = next(output_folder.glob("train_*.txt"))
    original_caption = caption.read_text(encoding="utf-8")
    caption.write_text(original_caption[::-1], encoding="utf-8")

    response = test_client.post(
        "/api/dataset/package-verifications",
        json={
            "output_folder": str(output_folder),
            "expected_run_id": exported["package_run_id"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "invalid"
    assert body["valid"] is False
    assert any(
        issue["code"] == "artifact_hash_mismatch"
        and issue["path"] == caption.name
        for issue in body["issues"]
    )


def test_package_verifier_reports_artifact_size_mismatch(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (45, 55, 65))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    caption = next(output_folder.glob("train_*.txt"))
    caption.write_text("longer tampered caption", encoding="utf-8")

    response = test_client.post(
        "/api/dataset/package-verifications",
        json={
            "output_folder": str(output_folder),
            "expected_run_id": exported["package_run_id"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "invalid"
    assert body["valid"] is False
    assert any(
        issue["code"] == "artifact_size_mismatch"
        and issue["path"] == caption.name
        for issue in body["issues"]
    )


@pytest.mark.parametrize(
    "trainer_config",
    ["kohya_toml", "anima_lora_toml"],
)
def test_verifier_rejects_trainer_config_root_contract_mismatch(
    test_client,
    tmp_path: Path,
    trainer_config: str,
) -> None:
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (46, 56, 66))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = trainer_config
    exported = test_client.post("/api/dataset/export", json=payload).json()
    config_path = output_folder / "dataset_config.toml"
    content = config_path.read_text(encoding="utf-8")
    root_literal = str(output_folder).replace("\\", "/")
    wrong_root = str(tmp_path / "other-root").replace("\\", "/")
    assert root_literal in content
    tampered = content.replace(root_literal, wrong_root)
    config_path.write_text(tampered, encoding="utf-8", newline="")
    encoded = tampered.encode("utf-8")
    manifest = read_package_manifest(output_folder)
    package_artifacts = tuple(
        artifact.model_copy(update={
            "byte_size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        })
        if artifact.role == "trainer_config"
        else artifact
        for artifact in manifest.package_artifacts
    )
    updated = manifest.model_copy(update={"package_artifacts": package_artifacts})
    (output_folder / "export_manifest.json").write_text(
        updated.model_dump_json(),
        encoding="utf-8",
        newline="",
    )

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "trainer_config_contract_mismatch"
        for issue in verified["issues"]
    ), verified


@pytest.mark.parametrize("trainer_config", ["kohya_toml", "anima_lora_toml"])
def test_verifier_rejects_trainer_config_artifact_at_noncanonical_path(
    test_client,
    tmp_path: Path,
    trainer_config: str,
) -> None:
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (46, 57, 68))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = trainer_config
    exported = test_client.post("/api/dataset/export", json=payload).json()
    config_path = output_folder / "dataset_config.toml"
    renamed_path = output_folder / "renamed-config.toml"
    config_path.replace(renamed_path)
    manifest = read_package_manifest(output_folder)
    package_artifacts = tuple(
        artifact.model_copy(update={"path": renamed_path.name})
        if artifact.role == "trainer_config"
        else artifact
        for artifact in manifest.package_artifacts
    )
    updated = manifest.model_copy(update={"package_artifacts": package_artifacts})
    (output_folder / "export_manifest.json").write_text(
        updated.model_dump_json(),
        encoding="utf-8",
        newline="",
    )

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "trainer_config_contract_mismatch"
        for issue in verified["issues"]
    ), verified


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [("trainer_resolution", 2048), ("trainer_keep_tokens", 1)],
)
def test_verifier_rejects_manifest_options_outside_anima_contract(
    test_client,
    tmp_path: Path,
    option_name: str,
    option_value: int,
) -> None:
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (47, 57, 67))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    manifest = read_package_manifest(output_folder)
    tampered_options = manifest.options.model_copy(
        update={option_name: option_value}
    )
    _rewrite_package_models(
        output_folder,
        _read_inventory(output_folder),
        {"options": tampered_options},
    )

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "trainer_options_contract_mismatch"
        for issue in verified["issues"]
    ), verified


def test_verifier_rejects_anima_pair_moved_below_training_root(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (48, 58, 68))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    record = DatasetPackageInventoryRecord.model_validate_json(
        json.dumps(_read_inventory(output_folder)[0])
    )
    nested = output_folder / "nested"
    nested.mkdir()
    moved_outputs = []
    for artifact in record.outputs:
        moved_path = nested / Path(artifact.path).name
        (output_folder / artifact.path).replace(moved_path)
        moved_outputs.append(artifact.model_copy(update={
            "path": moved_path.relative_to(output_folder).as_posix(),
        }))
    record = record.model_copy(update={"outputs": tuple(moved_outputs)})
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, [record], {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_layout_invalid"
        for issue in verified["issues"]
    ), verified


def test_blocked_rerun_preserves_old_complete_manifest(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (50, 60, 70))
    output_folder = tmp_path / "package"
    first = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    missing = tmp_path / "missing.png"

    second_response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([missing], output_folder),
    )

    assert second_response.status_code == 409, second_response.text
    assert second_response.json()["code"] == "readiness_blocked"
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["run_id"] == first["package_run_id"]
    assert manifest["package_status"] == "complete"

    old_verification = test_client.post(
        "/api/dataset/package-verifications",
        json={
            "output_folder": str(output_folder),
            "expected_run_id": first["package_run_id"],
        },
    ).json()
    assert old_verification["valid"] is True
    assert old_verification["issues"] == []


def test_cancelled_trainer_export_never_publishes_complete_package(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (60, 70, 80))
    output_folder = tmp_path / "package"
    request = export_service.DatasetExportRequest.model_validate(
        _anima_payload([source], output_folder)
    )
    cancel_event = threading.Event()
    cancel_event.set()

    response = export_engine.export_dataset(request, cancel_event=cancel_event)

    assert response.status == "cancelled"
    assert response.package_status == "incomplete"
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["package_status"] == "incomplete"


def test_running_cancel_during_config_generation_prevents_complete_package(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.bulk_job_service import (
        JOB_KIND_DATASET_EXPORT,
        get_bulk_job_service,
    )
    from services.dataset_export import engine

    source = _make_source(tmp_path / "source.png", (61, 71, 81))
    output_folder = tmp_path / "package"
    config_started = threading.Event()
    release_config = threading.Event()
    real_write_config = engine._write_anima_dataset_config

    def blocked_write_config(*args, **kwargs) -> str:
        config_started.set()
        if not release_config.wait(timeout=3.0):
            raise RuntimeError("Timed out waiting to release trainer config writer")
        return real_write_config(*args, **kwargs)

    monkeypatch.setattr(
        engine,
        "_write_anima_dataset_config",
        blocked_write_config,
    )
    start_responses: list[object] = []

    def start_export() -> None:
        start_responses.append(test_client.post(
            "/api/dataset/export/start",
            json=_anima_payload([source], output_folder),
        ))

    start_thread = threading.Thread(target=start_export)
    start_thread.start()
    assert config_started.wait(timeout=3.0), "worker never reached config writer"
    active_jobs = [
        job
        for job in get_bulk_job_service().list_jobs(active_only=True)
        if job["kind"] == JOB_KIND_DATASET_EXPORT
    ]
    assert len(active_jobs) == 1
    job_id = active_jobs[0]["job_id"]

    cancelled = test_client.post(f"/api/bulk-jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["message"] == "Cancellation requested"
    release_config.set()
    start_thread.join(timeout=4.0)
    assert start_thread.is_alive() is False
    assert start_responses and start_responses[0].status_code == 200

    job = test_client.get(f"/api/bulk-jobs/{job_id}").json()
    assert job["status"] == "cancelled"
    assert job["result"]["status"] == "cancelled"
    assert job["result"]["package_status"] == "incomplete"
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["package_status"] == "incomplete"
    verified = _verify_package(
        test_client,
        output_folder,
        job["result"]["package_run_id"],
    )
    assert verified["valid"] is False
    assert verified["status"] in {"incomplete", "invalid"}


def test_cancel_after_complete_manifest_publication_keeps_done_package(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.bulk_job_service import (
        JOB_KIND_DATASET_EXPORT,
        get_bulk_job_service,
    )
    from services.dataset_export import engine

    source = _make_source(tmp_path / "source.png", (62, 72, 82))
    output_folder = tmp_path / "package"
    manifest_published = threading.Event()
    release_finalize = threading.Event()
    real_finalize = engine.finalize_dataset_package

    def finalize_then_block(*args, **kwargs):
        result = real_finalize(*args, **kwargs)
        manifest = json.loads(
            (output_folder / "export_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["package_status"] == "complete"
        manifest_published.set()
        if not release_finalize.wait(timeout=3.0):
            raise RuntimeError("Timed out waiting to release finalized package")
        return result

    monkeypatch.setattr(engine, "finalize_dataset_package", finalize_then_block)
    start_responses: list[object] = []

    def start_export() -> None:
        start_responses.append(test_client.post(
            "/api/dataset/export/start",
            json=_anima_payload([source], output_folder),
        ))

    start_thread = threading.Thread(target=start_export)
    start_thread.start()
    assert manifest_published.wait(timeout=3.0), "complete manifest was not published"
    active_jobs = [
        job
        for job in get_bulk_job_service().list_jobs(active_only=True)
        if job["kind"] == JOB_KIND_DATASET_EXPORT
    ]
    assert len(active_jobs) == 1
    job_id = active_jobs[0]["job_id"]

    cancelled = test_client.post(f"/api/bulk-jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["message"] == "Completion already started"
    release_finalize.set()
    start_thread.join(timeout=4.0)
    assert start_thread.is_alive() is False
    assert start_responses and start_responses[0].status_code == 200

    job = test_client.get(f"/api/bulk-jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["result"]["status"] == "ok"
    assert job["result"]["package_status"] == "complete"
    verified = _verify_package(
        test_client,
        output_folder,
        job["result"]["package_run_id"],
    )
    assert verified["valid"] is True
    assert verified["status"] == "complete"


def test_non_trainer_export_keeps_legacy_v1_manifest(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (70, 80, 90))
    output_folder = tmp_path / "legacy"
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = "none"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["package_status"] == "not_requested"
    assert body["package_run_id"] is None
    assert body["package_manifest_path"] is None
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["manifest_version"] == 1
    assert "schema" not in manifest
    assert (output_folder / "export_inventory.jsonl").exists() is False


def test_finalizer_refuses_to_overwrite_another_run_manifest(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.package_integrity import (
        PackageOwnershipError,
        publish_package_manifest,
        read_package_manifest,
    )

    source = _make_source(tmp_path / "source.png", (80, 90, 100))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    original = read_package_manifest(output_folder)
    other_run_id = "b" * 32
    competing = original.model_copy(
        update={"run_id": other_run_id, "package_status": "building"}
    )
    manifest_path = output_folder / "export_manifest.json"
    manifest_path.write_text(
        competing.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(PackageOwnershipError, match="active package run changed"):
        publish_package_manifest(
            output_folder,
            original,
            expected_active_run_id=exported["package_run_id"],
        )

    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current["run_id"] == other_run_id
    assert current["package_status"] == "building"


def test_manifest_replace_failure_preserves_building_manifest_and_cleans_temp(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity

    source = _make_source(tmp_path / "source.png", (85, 95, 105))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    complete = package_integrity.read_package_manifest(output_folder)
    building = complete.model_copy(update={"package_status": "building"})
    manifest_path = output_folder / "export_manifest.json"
    manifest_path.write_text(building.model_dump_json(), encoding="utf-8")
    real_replace = package_integrity.os.replace

    def fail_manifest_replace(source_path: str, target_path: str) -> None:
        if Path(target_path) == manifest_path:
            raise OSError("injected manifest replace failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(package_integrity.os, "replace", fail_manifest_replace)

    with pytest.raises(
        package_integrity.PackageIntegrityError,
        match="injected manifest replace failure",
    ):
        package_integrity.publish_package_manifest(
            output_folder,
            complete,
            expected_active_run_id=exported["package_run_id"],
        )

    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current["run_id"] == exported["package_run_id"]
    assert current["package_status"] == "building"
    # Any dot-prefixed sibling of the manifest, whatever the staging name: pinning
    # the old ``.<name>.<random>.tmp`` shape only pinned tempfile's random name.
    assert list(output_folder.glob(".export_manifest.json*")) == []


@pytest.mark.parametrize("entrypoint", ["pending", "begin"])
def test_new_run_manifest_failure_retires_previous_complete_certificate(
    test_client,
    tmp_path: Path,
    monkeypatch,
    entrypoint: str,
) -> None:
    from services.dataset_export import package_integrity
    from services.dataset_export.models import DatasetExportRequest

    source = _make_source(tmp_path / "source.png", (86, 96, 106))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    first = test_client.post("/api/dataset/export", json=payload).json()
    request = DatasetExportRequest.model_validate(payload)
    manifest_path = output_folder / "export_manifest.json"
    real_replace = package_integrity.os.replace

    def fail_canonical_manifest_replace(
        source_path: str,
        target_path: str,
    ) -> None:
        if Path(target_path) == manifest_path:
            raise OSError("injected new run manifest replace failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(
        package_integrity.os,
        "replace",
        fail_canonical_manifest_replace,
    )
    with pytest.raises(
        package_integrity.PackageIntegrityError,
        match="injected new run manifest replace failure",
    ):
        if entrypoint == "pending":
            package_integrity.publish_pending_dataset_package(
                output_folder,
                request,
                1,
                ".txt",
                "d" * 32,
            )
        else:
            package_integrity.begin_dataset_package(
                output_folder,
                request,
                1,
                ".txt",
            )

    verified = _verify_package(
        test_client,
        output_folder,
        first["package_run_id"],
    )
    assert verified["valid"] is False
    assert verified["status"] in {"invalid", "missing"}
    retired_manifests = []
    for candidate in output_folder.iterdir():
        if candidate == manifest_path or not candidate.is_file():
            continue
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if document.get("run_id") == first["package_run_id"]:
            retired_manifests.append(candidate)
    assert retired_manifests


def test_verifier_rejects_symlink_leaf_even_when_target_is_internal(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (90, 100, 110))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    caption = next(output_folder.glob("train_*.txt"))
    internal_target = output_folder / "internal-caption-copy.bin"
    caption.replace(internal_target)
    os.symlink(internal_target, caption)

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_type_invalid"
        and issue["path"] == caption.name
        for issue in verified["issues"]
    )


def test_verifier_rejects_symlinked_manifest(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (90, 101, 111))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    manifest_path = output_folder / "export_manifest.json"
    external_manifest = tmp_path / "external-manifest.json"
    manifest_path.replace(external_manifest)
    os.symlink(external_manifest, manifest_path)

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "manifest_type_invalid"
        for issue in verified["issues"]
    )


def test_verifier_rejects_unlisted_trainable_symlink(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (90, 102, 112))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    rogue_path = output_folder / "rogue.png"
    os.symlink(source, rogue_path)

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "unlisted_trainable_artifact"
        and issue["path"] == rogue_path.name
        for issue in verified["issues"]
    )


@pytest.mark.parametrize(
    "trainer_updates",
    [
        {"upstream_commit": "0" * 40},
        {"id": "kohya_sd_scripts", "wire_value": "anima_lora_toml"},
    ],
)
def test_verifier_rejects_trainer_contract_drift(
    test_client,
    tmp_path: Path,
    trainer_updates: dict[str, str],
) -> None:
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (91, 101, 111))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    manifest = read_package_manifest(output_folder)
    records = _read_inventory(output_folder)
    bad_trainer = manifest.trainer.model_copy(update=trainer_updates)
    _rewrite_package_models(output_folder, records, {"trainer": bad_trainer})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "trainer_contract_mismatch"
        for issue in verified["issues"]
    )


@pytest.mark.parametrize(
    "contradiction",
    ["skipped", "masks_written", "masks_missing", "errors"],
)
def test_verifier_rejects_complete_manifest_with_failure_state(
    test_client,
    tmp_path: Path,
    contradiction: str,
) -> None:
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (92, 102, 112))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    manifest = read_package_manifest(output_folder)
    records = _read_inventory(output_folder)
    updates: dict[str, object] = {}
    if contradiction == "errors":
        updates["errors"] = ("injected terminal error",)
    else:
        updates["counts"] = manifest.counts.model_copy(
            update={contradiction: 1}
        )
    _rewrite_package_models(output_folder, records, updates)

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "complete_state_invalid"
        for issue in verified["issues"]
    )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_role", "duplicate_index", "non_contiguous_index", "source_hash"],
)
def test_verifier_rejects_inventory_semantic_tampering(
    test_client,
    tmp_path: Path,
    mutation: str,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    sources = [
        _make_source(tmp_path / f"source-{index}.png", (index, 110, 120))
        for index in (1, 2)
    ]
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload(sources, output_folder),
    ).json()
    records = [
        DatasetPackageInventoryRecord.model_validate_json(json.dumps(record))
        for record in _read_inventory(output_folder)
    ]
    if mutation == "duplicate_role":
        duplicate = records[0].outputs[0].model_copy(
            update={"path": "duplicate-image-role.bin"}
        )
        (output_folder / "duplicate-image-role.bin").write_bytes(
            (output_folder / records[0].outputs[0].path).read_bytes()
        )
        records[0] = records[0].model_copy(
            update={"outputs": (*records[0].outputs, duplicate)}
        )
    elif mutation == "duplicate_index":
        records[1] = records[1].model_copy(update={"index": records[0].index})
    elif mutation == "non_contiguous_index":
        records[1] = records[1].model_copy(update={"index": 3})
    else:
        records[0] = records[0].model_copy(update={
            "source": records[0].source.model_copy(update={"sha256": "0" * 64})
        })
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, records, {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] in {
            "artifact_role_multiplicity_invalid",
            "inventory_index_invalid",
            "source_image_hash_mismatch",
        }
        for issue in verified["issues"]
    )


def test_verifier_rejects_noncanonical_alias_of_existing_artifacts(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    sources = [
        _make_source(tmp_path / f"source-{index}.png", (35, 45, 55))
        for index in (1, 2)
    ]
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload(sources, output_folder),
    ).json()
    records = [
        DatasetPackageInventoryRecord.model_validate_json(json.dumps(record))
        for record in _read_inventory(output_folder)
    ]
    first_outputs = {
        artifact.role: artifact
        for artifact in records[0].outputs
    }
    second_paths = tuple(
        output_folder / artifact.path
        for artifact in records[1].outputs
    )
    aliased_outputs = tuple(
        first_outputs[role].model_copy(
            update={"path": f"./{first_outputs[role].path}"}
        )
        for role in ("image", "caption")
    )
    first_caption = first_outputs["caption"]
    assert records[1].annotation is not None
    records[1] = records[1].model_copy(update={
        "annotation": records[1].annotation.model_copy(
            update={"content_sha256": first_caption.sha256}
        ),
        "outputs": aliased_outputs,
    })
    for second_path in second_paths:
        second_path.unlink()
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, records, {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_path_invalid"
        and str(issue["path"]).startswith("./")
        for issue in verified["issues"]
    ), verified


def test_verifier_rejects_artifact_path_through_parent_symlink_alias(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (45, 55, 65))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = "kohya_toml"
    exported = test_client.post("/api/dataset/export", json=payload).json()
    first = DatasetPackageInventoryRecord.model_validate_json(
        json.dumps(_read_inventory(output_folder)[0])
    )
    alias = output_folder / "alias"
    os.symlink(output_folder, alias, target_is_directory=True)
    second = first.model_copy(update={
        "index": 2,
        "outputs": tuple(
            artifact.model_copy(update={"path": f"alias/{artifact.path}"})
            for artifact in first.outputs
        ),
    })
    manifest = read_package_manifest(output_folder)
    counts = manifest.counts.model_copy(update={
        "requested": 2,
        "processed": 2,
        "exported": 2,
        "inventory_records": 2,
    })
    _rewrite_package_models(output_folder, [first, second], {"counts": counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_path_invalid"
        and str(issue["path"]).startswith("alias/")
        for issue in verified["issues"]
    ), verified


def test_verifier_holds_package_lock_for_stable_snapshot(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity
    from services.dataset_export.models import (
        DatasetExportRequest,
        DatasetPackageVerificationRequest,
    )

    source = _make_source(tmp_path / "source.png", (46, 56, 66))
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    exported = test_client.post("/api/dataset/export", json=payload).json()
    inspection_started = threading.Event()
    release_inspection = threading.Event()
    real_inspect = package_integrity._inspect_package_inventory

    def inspect_after_release(*args, **kwargs):
        inspection_started.set()
        if not release_inspection.wait(timeout=3.0):
            raise RuntimeError("Timed out waiting to release package verification")
        return real_inspect(*args, **kwargs)

    monkeypatch.setattr(
        package_integrity,
        "_inspect_package_inventory",
        inspect_after_release,
    )
    verification_results: list[object] = []

    def verify() -> None:
        verification_results.append(package_integrity.verify_dataset_package(
            DatasetPackageVerificationRequest(
                output_folder=str(output_folder),
                expected_run_id=exported["package_run_id"],
            )
        ))

    verifier_thread = threading.Thread(target=verify)
    verifier_thread.start()
    assert inspection_started.wait(timeout=3.0), "verifier did not inspect inventory"
    writer_errors: list[Exception] = []
    writer_builds: list[object] = []
    request = DatasetExportRequest.model_validate(payload)
    try:
        writer_builds.append(package_integrity.begin_dataset_package(
            output_folder,
            request,
            1,
            ".txt",
        ))
    except package_integrity.PackageLockError as exc:
        writer_errors.append(exc)
    finally:
        if writer_builds:
            package_integrity.abort_dataset_package(
                writer_builds[0],
                "Concurrent writer should not have acquired the package lock",
            )
        release_inspection.set()
        verifier_thread.join(timeout=4.0)

    assert verifier_thread.is_alive() is False
    assert len(writer_errors) == 1
    assert writer_builds == []
    assert len(verification_results) == 1
    verification = verification_results[0]
    assert verification.valid is True
    assert verification.status == "complete"
    probe = package_integrity.PackageFileLock(output_folder)
    probe.acquire()
    probe.release()


def test_verifier_reports_package_lock_conflict_as_typed_result(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.package_integrity import PackageFileLock

    source = _make_source(tmp_path / "source.png", (47, 57, 67))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    package_lock = PackageFileLock(output_folder)
    package_lock.acquire()
    try:
        response = test_client.post(
            "/api/dataset/package-verifications",
            json={
                "output_folder": str(output_folder),
                "expected_run_id": exported["package_run_id"],
            },
        )
    finally:
        package_lock.release()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "invalid"
    assert body["valid"] is False
    assert any(
        issue["code"] == "package_locked"
        for issue in body["issues"]
    ), body


def test_verifier_rejects_unsupported_training_artifact_extensions(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (56, 66, 76))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    record = DatasetPackageInventoryRecord.model_validate_json(
        json.dumps(_read_inventory(output_folder)[0])
    )
    renamed_paths = {
        "image": "train_001.bin",
        "caption": "train_001.caption",
    }
    renamed_outputs = []
    for artifact in record.outputs:
        renamed_path = renamed_paths[artifact.role]
        (output_folder / artifact.path).replace(output_folder / renamed_path)
        renamed_outputs.append(
            artifact.model_copy(update={"path": renamed_path})
        )
    record = record.model_copy(update={"outputs": tuple(renamed_outputs)})
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, [record], {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_layout_invalid"
        for issue in verified["issues"]
    ), verified


def test_verifier_rejects_image_caption_stem_mismatch(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (57, 67, 77))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    record = DatasetPackageInventoryRecord.model_validate_json(
        json.dumps(_read_inventory(output_folder)[0])
    )
    renamed_outputs = []
    for artifact in record.outputs:
        if artifact.role != "caption":
            renamed_outputs.append(artifact)
            continue
        renamed_path = "different-stem.txt"
        (output_folder / artifact.path).replace(output_folder / renamed_path)
        renamed_outputs.append(
            artifact.model_copy(update={"path": renamed_path})
        )
    record = record.model_copy(update={"outputs": tuple(renamed_outputs)})
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, [record], {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_layout_invalid"
        for issue in verified["issues"]
    ), verified


@pytest.mark.parametrize("tamper_target", ["artifact", "inventory"])
def test_finalize_rechecks_published_inventory_and_artifacts(
    tmp_path: Path,
    monkeypatch,
    tamper_target: str,
) -> None:
    from services.dataset_export.models import DatasetExportRequest
    from services.dataset_export.package_integrity import (
        PackageInventoryWriter,
        begin_dataset_package,
        build_inventory_record,
        finalize_dataset_package,
        read_package_manifest,
    )

    output_folder = tmp_path / "package"
    output_folder.mkdir()
    source = _make_source(tmp_path / "source.png", (93, 103, 113))
    image_path = output_folder / "train_001.png"
    shutil.copy2(source, image_path)
    caption_path = output_folder / "train_001.txt"
    caption_path.write_text("subject", encoding="utf-8")
    config_path = output_folder / "dataset_config.toml"
    config_path.write_text("# Generated by SD Image Sorter\n", encoding="utf-8")
    request = DatasetExportRequest.model_validate(
        _anima_payload([source], output_folder)
    )
    build = begin_dataset_package(output_folder, request, 1, ".txt")
    build.inventory_writer.append(build_inventory_record(
        output_folder,
        1,
        0,
        str(source),
        "exported",
        None,
        image_path,
        caption_path,
        None,
        hashlib.sha256(caption_path.read_bytes()).hexdigest(),
        None,
    ))
    real_finalize = PackageInventoryWriter.finalize

    def finalize_then_tamper(writer: PackageInventoryWriter):
        summary = real_finalize(writer)
        if tamper_target == "artifact":
            original = caption_path.read_text(encoding="utf-8")
            caption_path.write_text(original[::-1], encoding="utf-8")
        else:
            (output_folder / "export_inventory.jsonl").open(
                "a", encoding="utf-8"
            ).write("\n")
        return summary

    monkeypatch.setattr(PackageInventoryWriter, "finalize", finalize_then_tamper)

    package_status, _manifest_path = finalize_dataset_package(
        build,
        1,
        1,
        1,
        0,
        0,
        0,
        0,
        str(config_path),
        False,
        (),
    )

    assert package_status == "incomplete"
    assert read_package_manifest(output_folder).package_status == "incomplete"


def test_queued_trainer_cancel_preserves_previous_complete_certificate(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    import services.dataset_export_service as export_service_module

    source = _make_source(tmp_path / "source.png", (94, 104, 114))
    output_folder = tmp_path / "package"
    first = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    held_tasks: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def hold_background_task(self, func, *args, **kwargs) -> None:
        held_tasks.append((func, args, kwargs))

    monkeypatch.setattr(
        export_service_module.BackgroundTasks,
        "add_task",
        hold_background_task,
    )
    started = test_client.post(
        "/api/dataset/export/start",
        json=_anima_payload([source], output_folder),
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    cancelled = test_client.post(f"/api/bulk-jobs/{job_id}/cancel").json()

    assert cancelled["status"] == "cancelled"
    assert cancelled["result"]["package_status"] == "incomplete"
    current = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert current["package_status"] == "complete"
    assert current["run_id"] == first["package_run_id"]
    assert held_tasks


def test_trainer_image_copy_replaces_symlink_without_touching_target(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (95, 105, 115))
    external = tmp_path / "external.bin"
    external.write_bytes(b"must remain unchanged")
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    image_target = output_folder / "train_001.png"
    os.symlink(external, image_target)
    payload = _anima_payload([source], output_folder)
    payload["overwrite_policy"] = "overwrite"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    assert external.read_bytes() == b"must remain unchanged"
    assert image_target.is_symlink() is False
    assert response.json()["package_status"] == "complete"


def test_trainer_mask_copy_replaces_symlink_without_touching_target(
    test_client,
    test_db,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services import mask_service

    source = _make_source(tmp_path / "source.png", (96, 106, 116))
    image_id = test_db.add_image(str(source), source.name)
    mask_dir = tmp_path / "stored-masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", mask_dir)
    mask_dir.mkdir()
    Image.new("L", (24, 24), color=255).save(mask_dir / f"{image_id}.png")
    external = tmp_path / "external-mask.bin"
    external.write_bytes(b"mask target must remain unchanged")
    output_folder = tmp_path / "package"
    (output_folder / "mask").mkdir(parents=True)
    mask_target = output_folder / "mask" / "train_001_mask.png"
    os.symlink(external, mask_target)
    payload = _anima_payload([source], output_folder)
    payload["image_paths"] = []
    payload["image_ids"] = [image_id]
    payload["image_overrides"] = {str(image_id): "subject"}
    payload["mask_export"] = "anima_lora"
    payload["overwrite_policy"] = "overwrite"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    assert external.read_bytes() == b"mask target must remain unchanged"
    assert mask_target.is_symlink() is False
    assert response.json()["package_status"] == "complete"


@pytest.mark.parametrize(
    ("trainer_config", "mask_export", "mask_name"),
    [
        ("kohya_toml", "kohya", "train_001.png"),
        ("anima_lora_toml", "anima_lora", "train_001_mask.png"),
    ],
)
def test_trainer_package_unique_does_not_overwrite_unowned_mask(
    test_client,
    test_db,
    tmp_path: Path,
    monkeypatch,
    trainer_config: str,
    mask_export: str,
    mask_name: str,
) -> None:
    from services import mask_service

    source = _make_source(tmp_path / "source.png", (96, 111, 121))
    source_content = source.read_bytes()
    image_id = test_db.add_image(str(source), source.name)
    stored_masks = tmp_path / "stored-masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", stored_masks)
    stored_masks.mkdir()
    Image.new("L", (24, 24), color=255).save(stored_masks / f"{image_id}.png")
    output_folder = tmp_path / "package"
    mask_folder = output_folder / "mask"
    mask_folder.mkdir(parents=True)
    mask_target = mask_folder / mask_name
    sentinel = b"user-maintained mask"
    mask_target.write_bytes(sentinel)
    payload = _anima_payload([source], output_folder)
    payload["image_paths"] = []
    payload["image_ids"] = [image_id]
    payload["image_overrides"] = {str(image_id): "subject"}
    payload["trainer_config"] = trainer_config
    payload["mask_export"] = mask_export
    payload["overwrite_policy"] = "unique"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["package_status"] == "incomplete"
    assert mask_target.read_bytes() == sentinel
    assert source.read_bytes() == source_content
    assert list(output_folder.glob("train_*")) == []
    manifest = json.loads(
        (output_folder / "export_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["package_status"] == "incomplete"


def test_kohya_masked_package_with_trigger_completes_and_verifies(
    test_client,
    test_db,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services import mask_service

    source = _make_source(tmp_path / "source.png", (97, 112, 122))
    image_id = test_db.add_image(str(source), source.name)
    stored_masks = tmp_path / "stored-masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", stored_masks)
    stored_masks.mkdir()
    Image.new("L", (24, 24), color=255).save(stored_masks / f"{image_id}.png")
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["image_paths"] = []
    payload["image_ids"] = [image_id]
    payload["image_overrides"] = {str(image_id): "hero_trigger, subject"}
    payload["trigger"] = "hero_trigger"
    payload["trainer_config"] = "kohya_toml"
    payload["mask_export"] = "kohya"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["package_status"] == "complete"
    config = (output_folder / "dataset_config.toml").read_text(encoding="utf-8")
    assert "conditioning_data_dir" in config
    assert "class_tokens =" not in config
    verified = _verify_package(
        test_client,
        output_folder,
        body["package_run_id"],
    )
    assert verified["status"] == "complete"
    assert verified["valid"] is True


@pytest.mark.parametrize(
    ("trainer_config", "mask_export"),
    [("kohya_toml", "kohya"), ("anima_lora_toml", "anima_lora")],
)
def test_verifier_rejects_requested_mask_at_wrong_layout(
    test_client,
    test_db,
    tmp_path: Path,
    monkeypatch,
    trainer_config: str,
    mask_export: str,
) -> None:
    from services import mask_service
    from services.dataset_export.models import DatasetPackageInventoryRecord
    from services.dataset_export.package_integrity import read_package_manifest

    source = _make_source(tmp_path / "source.png", (98, 113, 123))
    image_id = test_db.add_image(str(source), source.name)
    stored_masks = tmp_path / "stored-masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", stored_masks)
    stored_masks.mkdir()
    Image.new("L", (24, 24), color=255).save(stored_masks / f"{image_id}.png")
    output_folder = tmp_path / "package"
    payload = _anima_payload([source], output_folder)
    payload["image_paths"] = []
    payload["image_ids"] = [image_id]
    payload["image_overrides"] = {str(image_id): "subject"}
    payload["trainer_config"] = trainer_config
    payload["mask_export"] = mask_export
    payload["overwrite_policy"] = "overwrite"
    exported = test_client.post("/api/dataset/export", json=payload).json()
    record = DatasetPackageInventoryRecord.model_validate_json(
        json.dumps(_read_inventory(output_folder)[0])
    )
    moved_outputs = []
    for artifact in record.outputs:
        if artifact.role != "mask":
            moved_outputs.append(artifact)
            continue
        wrong_path = output_folder / "mask" / "wrong-mask-name.png"
        (output_folder / artifact.path).replace(wrong_path)
        moved_outputs.append(artifact.model_copy(update={
            "path": wrong_path.relative_to(output_folder).as_posix(),
        }))
    record = record.model_copy(update={"outputs": tuple(moved_outputs)})
    manifest = read_package_manifest(output_folder)
    _rewrite_package_models(output_folder, [record], {"counts": manifest.counts})

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_layout_invalid"
        for issue in verified["issues"]
    ), verified


@pytest.mark.parametrize(
    ("trainer_config", "mask_export"),
    [
        ("kohya_toml", "kohya"),
        ("anima_lora_toml", "anima_lora"),
    ],
)
def test_trainer_mask_directory_symlink_blocks_before_writes(
    test_client,
    test_db,
    tmp_path: Path,
    monkeypatch,
    trainer_config: str,
    mask_export: str,
) -> None:
    from services import mask_service

    source = _make_source(tmp_path / "source.png", (96, 106, 116))
    image_id = test_db.add_image(str(source), source.name)
    stored_masks = tmp_path / "stored-masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", stored_masks)
    stored_masks.mkdir()
    Image.new("L", (24, 24), color=255).save(stored_masks / f"{image_id}.png")
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    external = tmp_path / "external-mask-root"
    external.mkdir()
    os.symlink(external, output_folder / "mask", target_is_directory=True)
    payload = _anima_payload([source], output_folder)
    payload["image_paths"] = []
    payload["image_ids"] = [image_id]
    payload["image_overrides"] = {str(image_id): "subject"}
    payload["trainer_config"] = trainer_config
    payload["mask_export"] = mask_export

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 409, response.text
    assert "mask directory" in response.json()["error"]
    assert list(external.iterdir()) == []
    assert list(output_folder.glob("train_*")) == []


def test_package_copy_rejects_symlink_parent_without_external_write(
    tmp_path: Path,
) -> None:
    from services.dataset_export.package_integrity import (
        PackageIntegrityError,
        copy_package_file_atomic,
    )

    source = _make_source(tmp_path / "source.png", (96, 109, 119))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    external = tmp_path / "external-mask-root"
    external.mkdir()
    os.symlink(external, output_folder / "mask", target_is_directory=True)
    target = output_folder / "mask" / "copied.png"

    with pytest.raises(PackageIntegrityError, match="parent"):
        copy_package_file_atomic(source, target, output_folder)

    assert (external / target.name).exists() is False


def test_kohya_config_copy_replaces_symlink_without_touching_target(
    test_client,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source.png", (96, 107, 117))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    external = tmp_path / "external-config.toml"
    external_content = b"external config must remain unchanged"
    external.write_bytes(external_content)
    config_target = output_folder / "dataset_config.toml"
    os.symlink(external, config_target)
    payload = _anima_payload([source], output_folder)
    payload["trainer_config"] = "kohya_toml"

    response = test_client.post("/api/dataset/export", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["package_status"] == "complete"
    assert external.read_bytes() == external_content
    assert config_target.is_symlink() is False


def test_resume_writer_init_failure_publishes_incomplete_and_releases_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity
    from services.dataset_export.models import DatasetExportRequest

    source = _make_source(tmp_path / "source.png", (96, 108, 118))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    request = DatasetExportRequest.model_validate(
        _anima_payload([source], output_folder)
    )
    run_id = "c" * 32
    package_integrity.publish_pending_dataset_package(
        output_folder,
        request,
        1,
        ".txt",
        run_id,
    )

    def fail_writer_init(_output_folder: Path, _run_id: str) -> None:
        raise package_integrity.PackageIntegrityError(
            "injected inventory writer init failure"
        )

    monkeypatch.setattr(
        package_integrity,
        "PackageInventoryWriter",
        fail_writer_init,
    )

    with pytest.raises(
        package_integrity.PackageIntegrityError,
        match="injected inventory writer init failure",
    ):
        package_integrity.resume_pending_dataset_package(
            output_folder,
            request,
            1,
            ".txt",
            run_id,
        )

    manifest = package_integrity.read_package_manifest(output_folder)
    assert manifest.package_status == "incomplete"
    assert any("writer init failure" in error for error in manifest.errors)
    probe = package_integrity.PackageFileLock(output_folder)
    probe.acquire()
    probe.release()


def test_package_file_lock_is_nonblocking_and_released_on_abort(
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetExportRequest
    from services.dataset_export.package_integrity import (
        PackageLockError,
        abort_dataset_package,
        begin_dataset_package,
    )

    source = _make_source(tmp_path / "source.png", (97, 107, 117))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    request = DatasetExportRequest.model_validate(
        _anima_payload([source], output_folder)
    )
    first = begin_dataset_package(output_folder, request, 1, ".txt")

    with pytest.raises(PackageLockError, match="already locked"):
        begin_dataset_package(output_folder, request, 1, ".txt")

    abort_dataset_package(first, "test abort")
    second = begin_dataset_package(output_folder, request, 1, ".txt")
    abort_dataset_package(second, "test cleanup")
    assert (output_folder / ".sd-image-sorter-package.lock").exists()


def test_package_file_lock_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    from services.dataset_export.package_integrity import (
        PackageFileLock,
        PackageLockError,
    )

    output_folder = tmp_path / "package"
    output_folder.mkdir()
    external = tmp_path / "external-lock-target"
    external.write_bytes(b"")
    os.symlink(external, output_folder / ".sd-image-sorter-package.lock")
    package_lock = PackageFileLock(output_folder)

    try:
        with pytest.raises(PackageLockError, match="regular non-symlink file"):
            package_lock.acquire()
    finally:
        package_lock.release()

    assert external.read_bytes() == b""


def test_concurrent_package_writer_returns_conflict(
    test_client,
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetExportRequest
    from services.dataset_export.package_integrity import (
        abort_dataset_package,
        begin_dataset_package,
    )

    source = _make_source(tmp_path / "source.png", (101, 111, 121))
    output_folder = tmp_path / "package"
    output_folder.mkdir()
    payload = _anima_payload([source], output_folder)
    request = DatasetExportRequest.model_validate(payload)
    active = begin_dataset_package(output_folder, request, 1, ".txt")

    try:
        response = test_client.post("/api/dataset/export", json=payload)
    finally:
        abort_dataset_package(active, "test cleanup")

    assert response.status_code == 409, response.text
    assert "already locked" in response.json()["error"]


def test_unexpected_export_failure_aborts_package_and_releases_lock(
    tmp_path: Path,
) -> None:
    from services.dataset_export.models import DatasetExportRequest
    from services.dataset_export.package_integrity import (
        PackageFileLock,
        read_package_manifest,
    )
    from services.dataset_export.engine import export_dataset

    source = _make_source(tmp_path / "source.png", (102, 112, 122))
    output_folder = tmp_path / "package"
    request = DatasetExportRequest.model_validate(
        _anima_payload([source], output_folder)
    )

    def fail_after_package_begin(update: dict[str, object]) -> None:
        if str(update.get("message") or "").startswith("Exporting 0/"):
            raise RuntimeError("injected execution failure")

    with pytest.raises(RuntimeError, match="injected execution failure"):
        export_dataset(request, progress_callback=fail_after_package_begin)

    manifest = read_package_manifest(output_folder)
    assert manifest.package_status == "incomplete"
    assert any("injected execution failure" in error for error in manifest.errors)
    probe = PackageFileLock(output_folder)
    probe.acquire()
    probe.release()


def test_lock_release_failure_cannot_leave_complete_certificate(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity

    source = _make_source(tmp_path / "source.png", (103, 113, 123))
    output_folder = tmp_path / "package"
    real_release = package_integrity.PackageFileLock.release
    failure_injected = False

    def release_then_fail(package_lock) -> None:
        nonlocal failure_injected
        real_release(package_lock)
        if not failure_injected:
            failure_injected = True
            raise package_integrity.PackageIntegrityError(
                "injected lock release failure"
            )

    monkeypatch.setattr(
        package_integrity.PackageFileLock,
        "release",
        release_then_fail,
    )

    response = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["package_status"] == "incomplete"
    manifest = package_integrity.read_package_manifest(output_folder)
    assert manifest.package_status == "incomplete"
    verified = _verify_package(test_client, output_folder, body["package_run_id"])
    assert verified["valid"] is False
    assert verified["status"] == "incomplete"


def test_verifier_converts_hash_io_failure_to_typed_invalid_issue(
    test_client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity

    source = _make_source(tmp_path / "source.png", (98, 108, 118))
    output_folder = tmp_path / "package"
    exported = test_client.post(
        "/api/dataset/export",
        json=_anima_payload([source], output_folder),
    ).json()
    real_hash = package_integrity._hash_file

    def remove_caption_before_hash(path: Path) -> str:
        if path.suffix == ".txt":
            path.unlink()
        return real_hash(path)

    monkeypatch.setattr(package_integrity, "_hash_file", remove_caption_before_hash)

    verified = _verify_package(
        test_client,
        output_folder,
        exported["package_run_id"],
    )

    assert verified["status"] == "invalid"
    assert any(
        issue["code"] == "artifact_read_failed"
        for issue in verified["issues"]
    )


def test_atomic_manifest_error_includes_cleanup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from services.dataset_export import package_integrity

    target = tmp_path / "export_manifest.json"
    real_unlink = Path.unlink

    def fail_replace(source_path: str, target_path: str) -> None:
        raise OSError("injected replace failure")

    def fail_temp_cleanup(path: Path, *args, **kwargs) -> None:
        if path.name.startswith(".export_manifest.json."):
            raise OSError("injected cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(package_integrity.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)

    with pytest.raises(package_integrity.PackageIntegrityError) as exc_info:
        package_integrity._atomic_write_text(target, "{}")

    message = str(exc_info.value)
    assert "injected replace failure" in message
    assert "cleanup_error" in message
    assert "injected cleanup failure" in message
