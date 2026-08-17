"""Integration coverage for persistent named Dataset Maker projects."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import db_dataset_projects as project_db
from services.dataset_export.models import (
    DatasetExportPreviewRequest,
    DatasetExportRequest,
    DatasetReadinessRequest,
)
from services.dataset_project_models import DatasetProjectSettingsV1
from services.dataset_session.allowlist import (
    _register_session_paths,
    _session_path_cache,
    is_path_in_dataset_session,
)
from services.tagging.request import ExportPreviewRequest as TagExportPreviewRequest


def _default_project_settings() -> dict[str, object]:
    return {
        "settings_version": 1,
        "target_model": "",
        "caption_render": {
            "trigger": "",
            "common_tags": [],
            "blacklist": [],
            "normalize_tag_underscores": True,
            "content_mode": "template",
            "prefix": "",
            "template": {
                "template_override": "{trigger}, {tags:filtered}, {append}",
                "replace_rules": {},
                "max_tags": 0,
            },
        },
        "naming": {
            "preset": "keep",
            "custom_pattern": "{trigger}_{index:03d}",
        },
        "output": {
            "mode": "folder",
            "folder": "",
            "image_op": "copy",
            "overwrite_policy": "unique",
        },
        "trainer": {
            "config": "none",
            "contract_version": None,
            "mask_export": "none",
            "repeats": 10,
            "batch": 2,
            "resolution": 1024,
            "keep_tokens": 0,
        },
        "subject_crop": {
            "enabled": False,
            "alpha_threshold": 1,
            "padding_percent": 0,
            "background_mode": "keep_background",
            "solid_color": "#000000",
        },
        "bucket_resize": {
            "enabled": False,
            "subject_aware": False,
            "alpha_threshold": 128,
        },
        "watermark_removal": {
            "enabled": False,
            "method": "telea",
            "radius": 3,
            "padding_percent": 0,
            "regions": [],
        },
        "planning": {"epochs": 10},
    }


def _project_settings_with(
    path: tuple[str, ...],
    value: object,
) -> dict[str, object]:
    settings = deepcopy(_default_project_settings())
    cursor = settings
    for key in path[:-1]:
        nested = cursor[key]
        if not isinstance(nested, dict):
            raise TypeError(f"Project settings path {path!r} crosses non-object {key!r}")
        cursor = nested
    cursor[path[-1]] = value
    return settings


def _project_settings_with_trainer(
    config: str,
    contract_version: str | None,
    mask_export: str,
    resolution: int,
    keep_tokens: int,
) -> dict[str, object]:
    settings = deepcopy(_default_project_settings())
    settings["trainer"] = {
        **settings["trainer"],
        "config": config,
        "contract_version": contract_version,
        "mask_export": mask_export,
        "resolution": resolution,
        "keep_tokens": keep_tokens,
    }
    return settings


def _add_image(db, tmp_path: Path, filename: str) -> int:
    path = tmp_path / filename
    path.write_bytes(b"dataset-project-fixture")
    return int(db.add_image(path=str(path), filename=filename))


def _create_project(test_client, name: str, image_ids: list[int]):
    return test_client.post(
        "/api/dataset/projects",
        json={
            "name": name,
            "items": [_library_item(image_id) for image_id in image_ids],
            "settings": _default_project_settings(),
        },
    )


def _library_item(image_id: int) -> dict[str, object]:
    return {"item_type": "library", "image_id": image_id}


def _local_item(path: Path) -> dict[str, object]:
    return {"item_type": "local", "path": str(path)}


def _expected_ds_id(path: Path) -> str:
    digest = hashlib.sha1(
        str(path.resolve()).encode("utf-8", errors="replace")
    ).hexdigest()
    return f"ds:{digest[:16]}"


def _create_mixed_project(
    test_client,
    name: str,
    items: list[dict[str, object]],
):
    _register_session_paths(
        str(item["path"])
        for item in items
        if item.get("item_type") == "local" and isinstance(item.get("path"), str)
    )
    return test_client.post(
        "/api/dataset/projects",
        json={
            "name": name,
            "items": items,
            "settings": _default_project_settings(),
        },
    )


def test_create_and_read_project_preserves_strict_settings(test_client):
    settings = _default_project_settings()
    settings["target_model"] = "krea2"
    settings["caption_render"] = {
        **settings["caption_render"],
        "trigger": "subject_v1",
        "common_tags": ["masterpiece", "best_quality", "masterpiece"],
    }
    settings["caption_render"]["template"]["replace_rules"] = {"watermark": ""}
    settings["trainer"] = {
        "config": "anima_lora_toml",
        "contract_version": "1.0.0",
        "mask_export": "anima_lora",
        "repeats": 12,
        "batch": 4,
        "resolution": 1024,
        "keep_tokens": 0,
    }
    settings["subject_crop"] = {
        "enabled": True,
        "alpha_threshold": 24,
        "padding_percent": 15,
        "background_mode": "solid_color",
        "solid_color": "#123ABC",
    }

    created = test_client.post(
        "/api/dataset/projects",
        json={"name": "Strict settings", "items": [], "settings": settings},
    )

    assert created.status_code == 201
    assert created.json()["settings"] == settings
    fetched = test_client.get(f"/api/dataset/projects/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["settings"] == settings


def test_project_preserves_enabled_bucket_preprocessing_for_generic_export(test_client):
    settings = _default_project_settings()
    settings["trainer"] = {
        **settings["trainer"],
        "resolution": 512,
    }
    settings["bucket_resize"] = {
        "enabled": True,
        "subject_aware": True,
        "alpha_threshold": 160,
    }

    created = test_client.post(
        "/api/dataset/projects",
        json={"name": "Bucket preprocessing", "items": [], "settings": settings},
    )

    assert created.status_code == 201
    assert created.json()["settings"] == settings
    fetched = test_client.get(f"/api/dataset/projects/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["settings"] == settings


def test_project_preserves_enabled_watermark_removal_for_generic_export(test_client):
    settings = _default_project_settings()
    settings["watermark_removal"] = {
        "enabled": True,
        "method": "ns",
        "radius": 4,
        "padding_percent": 2,
        "regions": [{"x": 7000, "y": 8000, "width": 2500, "height": 1500}],
    }

    created = test_client.post(
        "/api/dataset/projects",
        json={"name": "Watermark cleanup", "items": [], "settings": settings},
    )

    assert created.status_code == 201
    assert created.json()["settings"] == settings
    fetched = test_client.get(f"/api/dataset/projects/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["settings"] == settings


@pytest.mark.parametrize(
    ("config", "contract_version", "mask_export"),
    (
        ("none", None, "none"),
        ("none", None, "onetrainer"),
        ("none", None, "kohya"),
        ("kohya_toml", "1.0.0", "none"),
        ("kohya_toml", "1.0.0", "kohya"),
        ("anima_lora_toml", "1.0.0", "none"),
        ("anima_lora_toml", "1.0.0", "anima_lora"),
    ),
)
def test_create_accepts_verified_trainer_compatibility(
    test_client,
    config,
    contract_version,
    mask_export,
):
    settings = _project_settings_with_trainer(
        config,
        contract_version,
        mask_export,
        1024,
        0,
    )

    response = test_client.post(
        "/api/dataset/projects",
        json={"name": "Compatible trainer", "items": [], "settings": settings},
    )

    assert response.status_code == 201


@pytest.mark.parametrize(
    "settings",
    (
        _project_settings_with_trainer("none", None, "anima_lora", 1024, 0),
        _project_settings_with_trainer("none", None, "none", 768, 0),
        _project_settings_with_trainer("none", None, "none", 1024, 1),
        _project_settings_with_trainer(
            "kohya_toml", "1.0.0", "onetrainer", 1024, 0
        ),
        _project_settings_with_trainer(
            "kohya_toml", "1.0.0", "anima_lora", 1024, 0
        ),
        _project_settings_with_trainer(
            "anima_lora_toml", "1.0.0", "onetrainer", 1024, 0
        ),
        _project_settings_with_trainer(
            "anima_lora_toml", "1.0.0", "kohya", 1024, 0
        ),
        _project_settings_with_trainer(
            "anima_lora_toml", "1.0.0", "none", 768, 0
        ),
        _project_settings_with_trainer(
            "anima_lora_toml", "1.0.0", "none", 1024, 1
        ),
    ),
)
def test_create_rejects_incompatible_trainer_settings(test_client, settings):
    response = test_client.post(
        "/api/dataset/projects",
        json={"name": "Incompatible trainer", "items": [], "settings": settings},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "settings",
    (
        {**_default_project_settings(), "unexpected": True},
        _project_settings_with(("settings_version",), True),
        _project_settings_with(("target_model",), "unsupported"),
        _project_settings_with(("caption_render", "trigger"), "x" * 101),
        _project_settings_with(("caption_render", "trigger"), "___"),
        _project_settings_with(("caption_render", "common_tags"), [" untrimmed"]),
        _project_settings_with(("caption_render", "blacklist"), [" "]),
        _project_settings_with(("caption_render", "common_tags"), ["x" * 501]),
        _project_settings_with(
            ("caption_render", "common_tags"),
            [f"tag-{index}" for index in range(1001)],
        ),
        _project_settings_with(
            ("caption_render", "template", "replace_rules"),
            {" untrimmed": "replacement"},
        ),
        _project_settings_with(
            ("caption_render", "template", "replace_rules"),
            {"tag": " replacement"},
        ),
        _project_settings_with(("caption_render", "template", "max_tags"), 201),
        _project_settings_with(("output", "mode"), "unsupported"),
        _project_settings_with(("trainer", "repeats"), True),
        _project_settings_with(("trainer", "batch"), 65),
        _project_settings_with(("planning", "epochs"), 0),
        _project_settings_with(("trainer", "contract_version"), "1.0.0"),
        _project_settings_with(("trainer", "config"), "kohya_toml"),
        {
            **_project_settings_with(("trainer", "config"), "kohya_toml"),
            "trainer": {
                **_default_project_settings()["trainer"],
                "config": "kohya_toml",
                "contract_version": "not-semver",
            },
        },
        {
            **_project_settings_with(("trainer", "config"), "kohya_toml"),
            "trainer": {
                **_default_project_settings()["trainer"],
                "config": "kohya_toml",
                "contract_version": "1.0.0",
            },
            "output": {
                **_default_project_settings()["output"],
                "mode": "beside_image",
            },
        },
        {
            **_project_settings_with(("trainer", "config"), "kohya_toml"),
            "trainer": {
                **_default_project_settings()["trainer"],
                "config": "kohya_toml",
                "contract_version": "1.0.0",
            },
            "output": {
                **_default_project_settings()["output"],
                "image_op": "move",
            },
        },
    ),
)
def test_create_rejects_invalid_project_settings(test_client, settings):
    response = test_client.post(
        "/api/dataset/projects",
        json={"name": "Invalid settings", "items": [], "settings": settings},
    )

    assert response.status_code == 400


def test_caption_tag_list_limits_match_project_preview_readiness_and_export() -> None:
    tags = [f"tag-{index}" for index in range(1000)]
    settings = _project_settings_with(("caption_render", "blacklist"), tags)

    project = DatasetProjectSettingsV1.model_validate(settings, strict=True)
    preview = DatasetExportPreviewRequest(blacklist=tags, common_tags=tags)
    readiness = DatasetReadinessRequest(blacklist=tags, common_tags=tags)
    export = DatasetExportRequest(blacklist=tags, common_tags=tags)

    assert len(project.caption_render.blacklist) == 1000
    assert len(preview.blacklist) == 1000
    assert len(readiness.blacklist) == 1000
    assert len(export.blacklist) == 1000

    too_many_tags = [*tags, "tag-1000"]
    with pytest.raises(ValidationError):
        DatasetExportPreviewRequest(blacklist=too_many_tags)
    with pytest.raises(ValidationError):
        DatasetReadinessRequest(blacklist=too_many_tags)
    with pytest.raises(ValidationError):
        DatasetExportRequest(blacklist=too_many_tags)


@pytest.mark.parametrize(
    "invalid_trigger",
    (
        "___",
        "Bad,Trigger",
        "Bad\nTrigger",
        "Bad Trigger",
        "Bad\tTrigger",
        "Bad\u00a0Trigger",
        "Bad\u3000Trigger",
        "Bad\u0085Trigger",
        "Bad\ufeffTrigger",
    ),
)
def test_trigger_contract_rejects_invalid_single_tokens(
    invalid_trigger: str,
) -> None:
    settings = _project_settings_with(
        ("caption_render", "trigger"),
        invalid_trigger,
    )

    with pytest.raises(ValidationError):
        DatasetProjectSettingsV1.model_validate(settings, strict=True)
    with pytest.raises(ValidationError):
        DatasetExportPreviewRequest(trigger=invalid_trigger)
    with pytest.raises(ValidationError):
        DatasetReadinessRequest(trigger=invalid_trigger)
    with pytest.raises(ValidationError):
        DatasetExportRequest(trigger=invalid_trigger)
    with pytest.raises(ValidationError):
        TagExportPreviewRequest(trigger=invalid_trigger)


@pytest.mark.parametrize(
    "template_options",
    (
        {"trigger": "___"},
        {"trigger": "x" * 101},
        {"blacklist": [f"tag-{index}" for index in range(1001)]},
    ),
)
@pytest.mark.parametrize(
    "request_model",
    (DatasetExportPreviewRequest, DatasetReadinessRequest, DatasetExportRequest),
)
def test_nested_template_options_cannot_bypass_caption_contracts(
    request_model,
    template_options: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        request_model(template_options=template_options)


@pytest.mark.parametrize(
    "request_model",
    (DatasetExportPreviewRequest, DatasetReadinessRequest, DatasetExportRequest),
)
def test_nested_template_contract_rejects_conflicting_duplicate_fields(
    request_model,
) -> None:
    with pytest.raises(ValidationError):
        request_model(
            trigger="top_level_trigger",
            blacklist=["top_level_block"],
            template_options={
                "trigger": "nested_trigger",
                "blacklist": ["nested_block"],
            },
        )


@pytest.mark.parametrize("edge_whitespace", (" ", "\u0085", "\ufeff"))
@pytest.mark.parametrize(
    "request_model",
    (DatasetExportPreviewRequest, DatasetReadinessRequest, DatasetExportRequest),
)
def test_trigger_contract_canonicalizes_surrounding_whitespace(
    request_model,
    edge_whitespace: str,
) -> None:
    raw_trigger = f"{edge_whitespace}{edge_whitespace}Hero_Token{edge_whitespace}"
    request = request_model(
        trigger=raw_trigger,
        template_options={"trigger": "Hero_Token"},
    )

    assert request.trigger == "Hero_Token"
    assert request.template_options is not None
    assert request.template_options.trigger == "Hero_Token"

    settings = _project_settings_with(
        ("caption_render", "trigger"),
        raw_trigger,
    )
    project = DatasetProjectSettingsV1.model_validate(settings, strict=True)
    legacy_preview = TagExportPreviewRequest(trigger=raw_trigger)
    assert project.caption_render.trigger == "Hero_Token"
    assert legacy_preview.trigger == "Hero_Token"


def test_create_requires_project_settings(test_client):
    response = test_client.post(
        "/api/dataset/projects",
        json={"name": "Missing settings", "items": []},
    )

    assert response.status_code == 400


def test_create_and_read_project_preserves_order(test_client, tmp_path: Path):
    db = test_client.test_db
    first_id = _add_image(db, tmp_path, "first.png")
    second_id = _add_image(db, tmp_path, "second.png")

    created = _create_project(test_client, "Portrait review", [second_id, first_id])

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Portrait review"
    assert body["revision"] == 1
    assert body["archived_at"] is None
    assert body["missing_image_ids"] == []
    assert body["items"] == [
        {
            "position": 0,
            "item_type": "library",
            "source_image_id": second_id,
            "image_id": second_id,
            "missing": False,
        },
        {
            "position": 1,
            "item_type": "library",
            "source_image_id": first_id,
            "image_id": first_id,
            "missing": False,
        },
    ]

    fetched = test_client.get(f"/api/dataset/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    listed = test_client.get("/api/dataset/projects")
    assert listed.status_code == 200
    assert listed.json()["projects"] == [
        {
            "id": body["id"],
            "name": "Portrait review",
            "revision": 1,
            "archived_at": None,
            "created_at": body["created_at"],
            "updated_at": body["updated_at"],
            "item_count": 2,
            "missing_image_count": 0,
        }
    ]


def test_project_survives_database_reinitialization(test_client, tmp_path: Path):
    db = test_client.test_db
    image_id = _add_image(db, tmp_path, "durable.png")
    created = _create_project(test_client, "Durable project", [image_id]).json()

    db._pragmas_initialized = set()
    db.init_db()

    fetched = test_client.get(f"/api/dataset/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["items"][0]["image_id"] == image_id
    assert fetched.json()["settings"] == _default_project_settings()


def test_mixed_project_persists_server_derived_local_identity_across_restart(
    test_client,
    tmp_path: Path,
):
    db = test_client.test_db
    library_id = _add_image(db, tmp_path, "mixed-library.png")
    local_path = tmp_path / "mixed-local.png"
    local_path.write_bytes(b"persistent-local-source")
    local_path.with_suffix(".txt").write_text(
        "1girl, red_hair",
        encoding="utf-8",
    )
    (tmp_path / "nested").mkdir()
    requested_local_path = tmp_path / "nested" / ".." / local_path.name
    expected_stat = local_path.stat()

    created = _create_mixed_project(
        test_client,
        "Mixed durable project",
        [_local_item(requested_local_path), _library_item(library_id)],
    )

    assert created.status_code == 201
    body = created.json()
    assert body["items"] == [
        {
            "position": 0,
            "item_type": "local",
            "ds_id": _expected_ds_id(local_path),
            "path": str(local_path.resolve()),
            "size": expected_stat.st_size,
            "mtime_ns": str(expected_stat.st_mtime_ns),
            "device": str(expected_stat.st_dev),
            "inode": str(expected_stat.st_ino),
            "source_status": "available",
            "sidecar_caption": "1girl, red_hair",
            # Format marker + target-model advisory travel with every local item.
            # This project's target_model is "" (unset), which has no evidenced
            # caption dialect, so the advisory is deliberately null here.
            "sidecar_caption_format": "tags",
            "caption_dialect": None,
        },
        {
            "position": 1,
            "item_type": "library",
            "source_image_id": library_id,
            "image_id": library_id,
            "missing": False,
        },
    ]

    db._pragmas_initialized = set()
    db.init_db()

    fetched = test_client.get(f"/api/dataset/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["items"] == body["items"]


def test_invalid_sidecar_cannot_create_or_update_a_project(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "atomic-write.png"
    local_path.write_bytes(b"atomic-project-source")
    local_path.with_suffix(".txt").write_text("1girl", encoding="utf-8")
    created = _create_mixed_project(
        test_client,
        "Atomic sidecar update",
        [_local_item(local_path)],
    )
    assert created.status_code == 201
    project = created.json()

    local_path.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
    blocked_update = test_client.put(
        f"/api/dataset/projects/{project['id']}",
        json={
            "name": "Must not persist",
            "items": [_local_item(local_path)],
            "expected_revision": project["revision"],
            "settings": _default_project_settings(),
        },
    )
    assert blocked_update.status_code == 400

    local_path.with_suffix(".txt").unlink()
    unchanged = test_client.get(f"/api/dataset/projects/{project['id']}").json()
    assert unchanged["name"] == "Atomic sidecar update"
    assert unchanged["revision"] == project["revision"]

    invalid_path = tmp_path / "atomic-create.png"
    invalid_path.write_bytes(b"atomic-create-source")
    invalid_path.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
    blocked_create = _create_mixed_project(
        test_client,
        "Must not be created",
        [_local_item(invalid_path)],
    )
    assert blocked_create.status_code == 400
    assert all(
        item["name"] != "Must not be created"
        for item in test_client.get("/api/dataset/projects").json()["projects"]
    )


def test_invalid_sidecar_cannot_archive_or_restore_a_project(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "atomic-state.png"
    local_path.write_bytes(b"atomic-state-source")
    local_path.with_suffix(".txt").write_text("1girl", encoding="utf-8")
    created = _create_mixed_project(
        test_client,
        "Atomic sidecar state",
        [_local_item(local_path)],
    ).json()

    local_path.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
    blocked_archive = test_client.post(
        f"/api/dataset/projects/{created['id']}/archive",
        json={"expected_revision": created["revision"]},
    )
    assert blocked_archive.status_code == 400

    local_path.with_suffix(".txt").write_text("1girl", encoding="utf-8")
    unchanged = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert unchanged["archived_at"] is None
    assert unchanged["revision"] == created["revision"]
    archived = test_client.post(
        f"/api/dataset/projects/{created['id']}/archive",
        json={"expected_revision": created["revision"]},
    ).json()

    local_path.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
    blocked_restore = test_client.post(
        f"/api/dataset/projects/{created['id']}/restore",
        json={"expected_revision": archived["revision"]},
    )
    assert blocked_restore.status_code == 400

    local_path.with_suffix(".txt").unlink()
    still_archived = test_client.get(
        f"/api/dataset/projects/{created['id']}"
    ).json()
    assert still_archived["archived_at"] == archived["archived_at"]
    assert still_archived["revision"] == archived["revision"]


def test_reading_project_reauthorizes_available_local_thumbnail_path(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "reauthorized.png"
    local_path.write_bytes(b"reauthorized-local-source")
    created = _create_mixed_project(
        test_client,
        "Reauthorized source",
        [_local_item(local_path)],
    ).json()
    _session_path_cache.clear()
    assert is_path_in_dataset_session(str(local_path)) is False

    fetched = test_client.get(f"/api/dataset/projects/{created['id']}")

    assert fetched.status_code == 200
    assert is_path_in_dataset_session(str(local_path)) is True


def test_create_canonicalizes_an_explicitly_imported_absolute_alias(
    test_client,
    tmp_path: Path,
):
    nested = tmp_path / "nested"
    nested.mkdir()
    local_path = tmp_path / "canonical-source.png"
    local_path.write_bytes(b"canonical-local-source")
    alias_path = nested / ".." / local_path.name
    _register_session_paths([str(alias_path)])

    response = test_client.post(
        "/api/dataset/projects",
        json={
            "name": "Canonical alias",
            "items": [_local_item(alias_path)],
            "settings": _default_project_settings(),
        },
    )

    assert response.status_code == 201
    assert response.json()["items"][0]["path"] == str(local_path.resolve())
    assert response.json()["items"][0]["source_status"] == "available"


def test_local_project_source_reports_missing_and_same_path_replacement_without_rebinding(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "replace-me.png"
    local_path.write_bytes(b"original-local-source")
    created = _create_mixed_project(
        test_client,
        "Identity guarded",
        [_local_item(local_path)],
    ).json()
    stored_item = created["items"][0]

    local_path.unlink()
    missing = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert missing["items"][0] == {
        **stored_item,
        "source_status": "missing",
    }

    local_path.write_bytes(b"replacement-local-source-with-different-identity")
    changed = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert changed["items"][0] == {
        **stored_item,
        "source_status": "changed",
    }
    assert changed["revision"] == created["revision"]


def test_local_project_source_reports_in_place_modification_as_changed(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "modify-me.png"
    local_path.write_bytes(b"before")
    created = _create_mixed_project(
        test_client,
        "Modified source",
        [_local_item(local_path)],
    ).json()

    local_path.write_bytes(b"after-with-a-different-size")

    fetched = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert fetched["items"][0]["source_status"] == "changed"
    assert fetched["items"][0]["size"] == created["items"][0]["size"]


def test_project_get_cannot_reauthorize_changed_source_for_save(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "identity-bound-import.png"
    local_path.write_bytes(b"identity-before-import")
    created = _create_mixed_project(
        test_client,
        "Identity-bound import",
        [_local_item(local_path)],
    ).json()

    local_path.write_bytes(b"identity-after-replacement-with-new-size")
    fetched = test_client.get(f"/api/dataset/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["items"][0]["source_status"] == "changed"
    assert is_path_in_dataset_session(str(local_path)) is True

    blocked = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Must explicitly reimport",
            "items": [_local_item(local_path)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "dataset_project_local_source_identity_conflict"
    assert blocked.json()["project_id"] == created["id"]
    assert blocked.json()["path"] == str(local_path.resolve())

    _register_session_paths([str(local_path)])
    rebound = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Explicitly reimported",
            "items": [_local_item(local_path)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )

    assert rebound.status_code == 200
    assert rebound.json()["revision"] == 2
    assert rebound.json()["items"][0]["source_status"] == "available"


def test_manifest_pagination_cannot_reauthorize_an_unhydrated_replacement(
    test_client,
    tmp_path: Path,
):
    first_path = tmp_path / "001.png"
    second_path = tmp_path / "002.png"
    first_path.write_bytes(b"first-manifest-source")
    second_path.write_bytes(b"second-manifest-source-before")
    initial = test_client.post(
        "/api/dataset/folder-scan",
        json={
            "folder_path": str(tmp_path),
            "limit": 1,
            "include_thumbnails": False,
        },
    )
    assert initial.status_code == 200
    assert initial.json()["has_more"] is True
    hydrated_path = Path(initial.json()["items"][0]["abs_path"])
    unhydrated_path = second_path if hydrated_path == first_path else first_path

    unhydrated_path.write_bytes(b"manifest-source-after-with-new-size")
    paged = test_client.post(
        "/api/dataset/folder-scan",
        json={
            "scan_token": initial.json()["scan_token"],
            "offset": 1,
            "limit": 1,
            "include_thumbnails": False,
        },
    )
    assert paged.status_code == 200
    assert paged.json()["items"][0]["abs_path"] == str(unhydrated_path.resolve())

    blocked = test_client.post(
        "/api/dataset/projects",
        json={
            "name": "Manifest replacement must be reimported",
            "items": [_local_item(unhydrated_path)],
            "settings": _default_project_settings(),
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "dataset_project_local_source_identity_conflict"


@pytest.mark.parametrize(
    "item_factory",
    (
        lambda tmp_path: {"item_type": "local", "path": "relative.png"},
        lambda tmp_path: _local_item(tmp_path / "missing.png"),
        lambda tmp_path: _local_item(tmp_path),
        lambda tmp_path: _local_item(tmp_path / "not-an-image.txt"),
        lambda tmp_path: {
            "item_type": "local",
            "path": str(tmp_path / "strict.png"),
            "size": 1,
        },
        lambda tmp_path: {"item_type": "local", "path": True},
        lambda tmp_path: {"item_type": "unknown", "path": str(tmp_path / "strict.png")},
    ),
)
def test_create_rejects_invalid_local_source_contract(
    test_client,
    tmp_path: Path,
    item_factory,
):
    (tmp_path / "strict.png").write_bytes(b"strict-local-source")
    (tmp_path / "not-an-image.txt").write_bytes(b"not-an-image")

    response = _create_mixed_project(
        test_client,
        "Strict local source",
        [item_factory(tmp_path)],
    )

    assert response.status_code == 400


def test_create_rejects_duplicate_canonical_local_paths(test_client, tmp_path: Path):
    local_path = tmp_path / "duplicate.png"
    local_path.write_bytes(b"duplicate-local-source")

    response = _create_mixed_project(
        test_client,
        "No duplicate sources",
        [_local_item(local_path), _local_item(local_path)],
    )

    assert response.status_code == 400


def test_create_rejects_unimported_absolute_local_path(test_client, tmp_path: Path):
    local_path = tmp_path / "not-imported.png"
    local_path.write_bytes(b"untrusted-local-source")
    _session_path_cache.clear()

    response = test_client.post(
        "/api/dataset/projects",
        json={
            "name": "Not imported",
            "items": [_local_item(local_path)],
            "settings": _default_project_settings(),
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "dataset_project_local_source_invalid"
    assert "active Dataset Maker session" in response.json()["reason"]


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    (
        ({"name": "Duplicate", "items": [_library_item(1), _library_item(1)]}, 400),
        ({"name": "Negative", "items": [{"item_type": "library", "image_id": -1}]}, 400),
        ({"name": "Boolean", "items": [{"item_type": "library", "image_id": True}]}, 400),
        ({"name": "Unknown", "items": [_library_item(999999)]}, 404),
        ({"name": "Extra", "items": [], "unexpected": True}, 400),
        ({"name": "Legacy", "image_ids": []}, 400),
    ),
)
def test_create_rejects_invalid_image_contract(
    test_client,
    payload: dict[str, object],
    expected_status: int,
):
    response = test_client.post(
        "/api/dataset/projects",
        json={**payload, "settings": _default_project_settings()},
    )
    assert response.status_code == expected_status


def test_update_requires_current_revision_and_never_overwrites(test_client, tmp_path: Path):
    db = test_client.test_db
    first_id = _add_image(db, tmp_path, "revision-first.png")
    second_id = _add_image(db, tmp_path, "revision-second.png")
    created = _create_project(test_client, "Revisioned", [first_id]).json()

    updated = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Revisioned current",
            "items": [_library_item(second_id), _library_item(first_id)],
            "expected_revision": 1,
            "settings": {
                **_default_project_settings(),
                "target_model": "flux",
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    stale = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Stale overwrite",
            "items": [_library_item(first_id)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )
    assert stale.status_code == 409
    stale_body = stale.json()
    assert {
        key: stale_body[key]
        for key in (
            "code",
            "message",
            "project_id",
            "expected_revision",
            "current_revision",
        )
    } == {
        "code": "dataset_project_revision_conflict",
        "message": "Dataset project changed since it was loaded. Reload it before saving.",
        "project_id": created["id"],
        "expected_revision": 1,
        "current_revision": 2,
    }

    current = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert current["name"] == "Revisioned current"
    assert current["settings"]["target_model"] == "flux"
    assert [item["source_image_id"] for item in current["items"]] == [
        second_id,
        first_id,
    ]


def test_update_rolls_back_name_settings_and_items_together(
    test_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db = test_client.test_db
    image_id = _add_image(db, tmp_path, "atomic-settings.png")
    created = _create_project(test_client, "Atomic before", [image_id]).json()
    changed_settings = {
        **_default_project_settings(),
        "target_model": "anima",
    }

    def fail_item_replace(
        conn,
        project_id: int,
        items: list[project_db.DatasetProjectItemInput],
    ) -> None:
        raise RuntimeError(
            f"forced item replacement failure for project_id={project_id}, "
            f"item_count={len(items)}, connection={conn!r}"
        )

    monkeypatch.setattr(project_db, "_replace_project_items", fail_item_replace)
    with pytest.raises(RuntimeError, match="forced item replacement failure"):
        project_db.update_dataset_project_record(
            created["id"],
            created["revision"],
            "Atomic after",
            "atomic after",
            [{"item_type": "library", "image_id": image_id}],
            json.dumps(changed_settings),
        )

    current = test_client.get(f"/api/dataset/projects/{created['id']}").json()
    assert current["name"] == "Atomic before"
    assert current["revision"] == 1
    assert current["settings"] == _default_project_settings()
    assert current["items"] == created["items"]


def test_stale_local_update_returns_revision_conflict_before_source_validation(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "stale-local.png"
    local_path.write_bytes(b"stale-local-source")
    created = _create_mixed_project(
        test_client,
        "Stale local",
        [_local_item(local_path)],
    ).json()
    current = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Stale local current",
            "items": [_local_item(local_path)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )
    assert current.status_code == 200
    assert current.json()["revision"] == 2
    local_path.unlink()

    stale = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Must not validate missing source",
            "items": [_local_item(local_path)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "dataset_project_revision_conflict"
    assert stale.json()["current_revision"] == 2


def test_update_accepts_expired_session_path_only_when_saved_identity_still_matches(
    test_client,
    tmp_path: Path,
):
    local_path = tmp_path / "expired-session.png"
    local_path.write_bytes(b"unchanged-local-source")
    created = _create_mixed_project(
        test_client,
        "Expired session",
        [_local_item(local_path)],
    ).json()
    _session_path_cache.clear()

    unchanged = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Expired session",
            "items": [_local_item(local_path)],
            "expected_revision": 1,
            "settings": _default_project_settings(),
        },
    )

    assert unchanged.status_code == 200
    assert unchanged.json()["revision"] == 2

    _session_path_cache.clear()
    local_path.write_bytes(b"changed-without-an-explicit-reimport")
    changed = test_client.put(
        f"/api/dataset/projects/{created['id']}",
        json={
            "name": "Must not rebind",
            "items": [_local_item(local_path)],
            "expected_revision": 2,
            "settings": _default_project_settings(),
        },
    )

    assert changed.status_code == 409
    assert changed.json()["code"] == "dataset_project_local_source_identity_conflict"
    assert "explicitly import the current file" in changed.json()["reason"]


def test_archive_and_restore_move_project_between_lists(test_client):
    created = _create_project(test_client, "Archive me", []).json()
    active_summary = test_client.get("/api/dataset/projects").json()["projects"]
    assert active_summary[0]["item_count"] == 0
    assert active_summary[0]["missing_image_count"] == 0

    archived = test_client.post(
        f"/api/dataset/projects/{created['id']}/archive",
        json={"expected_revision": 1},
    )
    assert archived.status_code == 200
    assert archived.json()["revision"] == 2
    assert archived.json()["settings"] == created["settings"]
    assert archived.json()["archived_at"] is not None
    assert test_client.get("/api/dataset/projects").json()["projects"] == []
    assert [
        project["id"]
        for project in test_client.get("/api/dataset/projects/archived").json()["projects"]
    ] == [created["id"]]

    restored = test_client.post(
        f"/api/dataset/projects/{created['id']}/restore",
        json={"expected_revision": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 3
    assert restored.json()["archived_at"] is None
    assert restored.json()["settings"] == created["settings"]


def test_restore_rejects_active_name_conflict(test_client):
    archived_project = _create_project(test_client, "Shared name", []).json()
    archived = test_client.post(
        f"/api/dataset/projects/{archived_project['id']}/archive",
        json={"expected_revision": 1},
    ).json()
    _create_project(test_client, "shared NAME", [])

    restored = test_client.post(
        f"/api/dataset/projects/{archived_project['id']}/restore",
        json={"expected_revision": archived["revision"]},
    )

    assert restored.status_code == 409
    assert restored.json()["code"] == "dataset_project_name_conflict"


def test_deleted_library_image_remains_as_missing_source_without_revision_change(
    test_client,
    tmp_path: Path,
):
    db = test_client.test_db
    image_id = _add_image(db, tmp_path, "missing.png")
    created = _create_project(test_client, "Missing source", [image_id]).json()

    with db.get_db() as conn:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))

    fetched = test_client.get(f"/api/dataset/projects/{created['id']}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["revision"] == 1
    assert body["missing_image_ids"] == [image_id]
    assert body["items"] == [
        {
            "position": 0,
            "item_type": "library",
            "source_image_id": image_id,
            "image_id": None,
            "missing": True,
        }
    ]


def test_deleting_project_never_deletes_library_images(test_client, tmp_path: Path):
    db = test_client.test_db
    image_id = _add_image(db, tmp_path, "keep.png")
    created = _create_project(test_client, "Disposable project", [image_id]).json()

    deleted = test_client.request(
        "DELETE",
        f"/api/dataset/projects/{created['id']}",
        json={"expected_revision": 1},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "project_id": created["id"]}
    assert db.get_image_by_id(image_id) is not None
    assert test_client.get(f"/api/dataset/projects/{created['id']}").status_code == 404


def test_deleting_project_never_deletes_local_files(test_client, tmp_path: Path):
    local_path = tmp_path / "keep-local.png"
    local_contents = b"keep-local-source"
    local_path.write_bytes(local_contents)
    created = _create_mixed_project(
        test_client,
        "Disposable local project",
        [_local_item(local_path)],
    ).json()

    deleted = test_client.request(
        "DELETE",
        f"/api/dataset/projects/{created['id']}",
        json={"expected_revision": 1},
    )

    assert deleted.status_code == 200
    assert local_path.read_bytes() == local_contents
    assert test_client.get(f"/api/dataset/projects/{created['id']}").status_code == 404


def test_active_names_use_unicode_casefold_uniqueness(test_client):
    assert _create_project(test_client, "Straße", []).status_code == 201

    conflict = _create_project(test_client, "STRASSE", [])

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "dataset_project_name_conflict"


@pytest.mark.parametrize(
    "payload",
    (
        {"name": "Strict", "items": [], "expected_revision": True},
        {"name": "Strict", "items": [], "expected_revision": 1, "extra": 1},
    ),
)
def test_update_rejects_non_strict_or_extra_fields(test_client, payload: dict[str, object]):
    created = _create_project(test_client, "Strict", []).json()
    response = test_client.put(f"/api/dataset/projects/{created['id']}", json=payload)
    assert response.status_code == 400
