"""Florence-2 Smart Tag and Model Manager registration contracts."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import model_service
from services.smart_tag.request import _coerce_request


def _base_health() -> dict[str, object]:
    return {
        "wd14": {
            "installed_models": [],
            "model_path": None,
            "default_model": "wd-swinv2-tagger-v3",
        },
        "toriigate": {
            "available": False,
            "model_dir": "/models/toriigate/toriigate-0.5",
            "message": "missing",
        },
        "oppai_oracle": {
            "available": False,
            "model_dir": "/models/oppai-oracle",
            "message": "missing",
        },
        "florence2": {
            "available": False,
            "checkpoint_path": None,
            "expected_path": "/models/florence2",
            "missing_dependencies": ["transformers"],
            "message": "Florence-2 setup is incomplete.",
        },
        "clip": {
            "available": False,
            "runtime_loaded": False,
            "model_path": None,
            "message": "missing",
        },
        "artist": {
            "available": False,
            "checkpoint_path": None,
            "runtime_path": None,
            "message": "missing",
        },
        "lucida": {
            "available": False,
            "checkpoint_path": None,
            "expected_path": "/models/lucida",
            "missing_dependencies": [],
            "message": "missing",
        },
        "censor": {
            "legacy": {
                "available": False,
                "default_model_path": "",
                "message": "missing",
                "files": [],
            },
            "nudenet": {
                "available": False,
                "model_downloaded": False,
                "model_path": None,
                "message": "missing",
            },
            "sam3": {
                "available": False,
                "checkpoint_path": None,
                "message": "missing",
            },
        },
    }


def test_request_accepts_florence2_without_loading_remote_vlm_config(monkeypatch):
    def reject_vlm_config():
        raise AssertionError("Remote VLM config must not load for Florence-2 mode")

    monkeypatch.setitem(
        sys.modules,
        "routers.vlm",
        SimpleNamespace(_build_config=reject_vlm_config),
    )

    request = _coerce_request(
        {
            "image_ids": [1],
            "enable_vlm": True,
            "natural_language_mode": "florence2",
        }
    )

    assert request.natural_language_mode == "florence2"


def test_inventory_exposes_florence2_as_optional_local_captioner(monkeypatch):
    monkeypatch.setattr(model_service, "get_model_health", _base_health)

    inventory = model_service.ModelService().build_model_inventory()
    florence = next(item for item in inventory if item["id"] == "florence2")

    assert florence["group"] == "Captioning"
    assert florence["recommended"] is True
    assert florence["default_variant"] == "base"
    assert florence["default_model"] == "florence-community/Florence-2-base"
    assert florence["download_supported"] is True
    assert florence["message_key"] == "models.florence2.missingDeps"


def test_prepare_florence2_uses_dedicated_pinned_checkpoint_flow(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        model_service,
        "ensure_group",
        lambda group: calls.append(group)
        or model_service.DependencyInstallResult((), False),
    )
    monkeypatch.setitem(
        sys.modules,
        "florence2_captioner",
        SimpleNamespace(prepare_checkpoint=lambda: "C:/models/florence2"),
    )

    result = model_service.ModelService().prepare_model("florence2")

    assert calls == ["florence2"]
    assert result == {
        "status": "ok",
        "model_id": "florence2",
        "message": "Florence-2 Base runtime and pinned model files are ready.",
        "paths": {"checkpoint_path": "C:/models/florence2"},
    }


def test_florence2_ui_copy_exists_without_booru_registry_entry():
    repo_root = Path(__file__).resolve().parents[2]
    index_html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    en = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8")
    zh = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(encoding="utf-8")
    tagger_models = (repo_root / "backend" / "tagger_models.py").read_text(encoding="utf-8")

    assert '<option value="florence2"' in index_html
    for key in (
        "smartTag.nlSourceFlorence2",
        "models.florence2.ready",
        "models.florence2.missingDeps",
        "models.florence2.missing",
    ):
        assert f"'{key}':" in en
        assert f"'{key}':" in zh
    assert '"florence2"' not in tagger_models
