"""TIPO Model Center registration: health probe + inventory card.

TIPO (``services/tipo_service.py``) shipped with an on-demand GGUF download
into ``DATA_DIR/models/tipo`` but no Model Center presence, so the owner could
not see whether it was installed, and a half-written weight file was
indistinguishable from no weight file at all. These tests pin the three states
the card must tell apart -- missing / broken / ready -- and the inventory entry
that renders them.

Nothing here downloads weights: a GGUF is simulated by its 4-byte magic
(``0x47 0x47 0x55 0x46``, ggml GGUF spec) plus padding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import model_health
from services import model_service, tipo_service

GGUF_MAGIC = b"GGUF"


def _weight_file(model_dir: Path, variant: str) -> Path:
    """Mirror the on-disk name kgen's ``download_gguf`` produces."""
    spec = tipo_service.MODEL_SPECS[variant]
    return model_dir / f"{spec.repo.split('/')[-1]}_{spec.filename}"


def _install(model_dir: Path, variant: str) -> Path:
    target = _weight_file(model_dir, variant)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(GGUF_MAGIC + b"\x03\x00\x00\x00" + b"\x00" * 4096)
    return target


@pytest.fixture
def tipo_dir(tmp_path, monkeypatch) -> Path:
    """Point TIPO's weight home at an empty scratch directory."""
    model_dir = tmp_path / "tipo-models"
    model_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tipo_service, "tipo_model_dir_path", lambda: model_dir)
    return model_dir


def _probe(runtime_ready: bool, monkeypatch) -> dict:
    monkeypatch.setattr(
        tipo_service,
        "_missing_runtime_modules",
        lambda: [] if runtime_ready else ["llama_cpp", "kgen"],
    )
    return tipo_service.probe_tipo_installation()


# ---------------------------------------------------------------------------
# Health probe: missing vs broken vs ready
# ---------------------------------------------------------------------------


def test_probe_reports_missing_when_no_weight_file_exists(tipo_dir, monkeypatch):
    probe = _probe(runtime_ready=True, monkeypatch=monkeypatch)

    assert probe["weight_state"] == "missing"
    assert probe["available"] is False
    assert probe["installed_variants"] == []
    assert probe["broken_variants"] == []
    assert probe["model_dir"] == str(tipo_dir.resolve())


def test_probe_distinguishes_a_broken_install_from_a_missing_one(tipo_dir, monkeypatch):
    """A truncated download must not read as "never downloaded"."""
    missing_message = _probe(runtime_ready=True, monkeypatch=monkeypatch)["message"]

    target = _weight_file(tipo_dir, "200m-ft")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"<!DOCTYPE html><html>rate limited</html>")

    probe = _probe(runtime_ready=True, monkeypatch=monkeypatch)

    assert probe["weight_state"] == "broken"
    assert probe["available"] is False
    assert probe["broken_variants"] == ["200m-ft"]
    assert probe["installed_variants"] == []
    assert "200m-ft" in probe["message"]
    assert probe["message"] != missing_message


def test_probe_treats_a_zero_byte_weight_file_as_broken(tipo_dir, monkeypatch):
    target = _weight_file(tipo_dir, "100m")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")

    probe = _probe(runtime_ready=True, monkeypatch=monkeypatch)

    assert probe["weight_state"] == "broken"
    assert probe["broken_variants"] == ["100m"]


def test_probe_reports_ready_only_when_weights_and_runtime_are_both_present(
    tipo_dir, monkeypatch
):
    _install(tipo_dir, "200m-ft")

    ready = _probe(runtime_ready=True, monkeypatch=monkeypatch)
    assert ready["weight_state"] == "ready"
    assert ready["installed_variants"] == ["200m-ft"]
    assert ready["available"] is True
    assert ready["missing_dependencies"] == []

    without_runtime = _probe(runtime_ready=False, monkeypatch=monkeypatch)
    assert without_runtime["weight_state"] == "ready"
    assert without_runtime["available"] is False
    assert without_runtime["missing_dependencies"] == ["llama_cpp", "kgen"]
    assert "llama_cpp" in without_runtime["message"]


def test_probe_prefers_a_ready_variant_over_a_broken_sibling(tipo_dir, monkeypatch):
    _install(tipo_dir, "200m-ft")
    broken = _weight_file(tipo_dir, "100m")
    broken.write_bytes(b"partial")

    probe = _probe(runtime_ready=True, monkeypatch=monkeypatch)

    assert probe["weight_state"] == "ready"
    assert probe["installed_variants"] == ["200m-ft"]
    assert probe["broken_variants"] == ["100m"]
    assert probe["available"] is True


def test_probe_never_creates_the_weight_directory(tmp_path, monkeypatch):
    """A read-only status check must not write into DATA_DIR."""
    absent = tmp_path / "never-created"
    monkeypatch.setattr(tipo_service, "tipo_model_dir_path", lambda: absent)
    monkeypatch.setattr(tipo_service, "_missing_runtime_modules", lambda: [])

    probe = tipo_service.probe_tipo_installation()

    assert probe["weight_state"] == "missing"
    assert not absent.exists()


def test_get_model_health_exposes_the_tipo_probe(tipo_dir, monkeypatch):
    _install(tipo_dir, "200m-ft")
    monkeypatch.setattr(tipo_service, "_missing_runtime_modules", lambda: [])

    health = model_health.get_model_health()

    assert health["tipo"]["weight_state"] == "ready"
    assert health["tipo"]["installed_variants"] == ["200m-ft"]
    assert health["tipo"]["default_variant"] == "v2.1"


# ---------------------------------------------------------------------------
# Inventory card
# ---------------------------------------------------------------------------


def _base_health() -> dict:
    """Minimal health contract shared with test_florence2_registration."""
    return {
        "wd14": {
            "installed_models": [],
            "model_path": None,
            "default_model": "wd-swinv2-tagger-v3",
        },
        "toriigate": {"available": False, "model_dir": "/m/toriigate", "message": "x"},
        "oppai_oracle": {"available": False, "model_dir": "/m/oppai", "message": "x"},
        "florence2": {
            "available": False,
            "checkpoint_path": None,
            "expected_path": "/m/florence2",
            "missing_dependencies": [],
            "message": "x",
        },
        "clip": {
            "available": False,
            "runtime_loaded": False,
            "model_path": None,
            "message": "x",
        },
        "artist": {
            "available": False,
            "checkpoint_path": None,
            "runtime_path": None,
            "message": "x",
        },
        "lucida": {
            "available": False,
            "checkpoint_path": None,
            "expected_path": "/m/lucida",
            "missing_dependencies": [],
            "message": "x",
        },
        "censor": {
            "legacy": {
                "available": False,
                "default_model_path": "",
                "message": "x",
                "files": [],
            },
            "nudenet": {
                "available": False,
                "model_downloaded": False,
                "model_path": None,
                "message": "x",
            },
            "sam3": {"available": False, "checkpoint_path": None, "message": "x"},
        },
    }


def _tipo_card(tipo_health: dict | None, monkeypatch) -> dict:
    health = _base_health()
    if tipo_health is not None:
        health["tipo"] = tipo_health
    monkeypatch.setattr(model_service, "get_model_health", lambda: health)
    inventory = model_service.ModelService().build_model_inventory()
    return next(item for item in inventory if item["id"] == "tipo")


def test_inventory_registers_a_tipo_card_the_owner_can_find(monkeypatch):
    card = _tipo_card(
        {
            "available": True,
            "weight_state": "ready",
            "installed_variants": ["v2.1"],
            "broken_variants": [],
            "missing_dependencies": [],
            "model_dir": "/m/tipo",
            "default_variant": "v2.1",
            "message": "TIPO is ready.",
        },
        monkeypatch,
    )

    assert card["name"] == "TIPO Prompt Expansion"
    assert card["status"] == "ready"
    assert card["available"] is True
    assert card["path"] == "/m/tipo"
    assert card["message_key"] == "models.tipo.ready"
    assert card["installed_variants"] == ["v2.1"]
    assert card["default_variant"] == "v2.1"
    assert card["selectable_variants"] == tipo_service.selectable_tipo_variants()


def test_inventory_tipo_card_reports_a_broken_install_distinctly(monkeypatch):
    broken = _tipo_card(
        {
            "available": False,
            "weight_state": "broken",
            "installed_variants": [],
            "broken_variants": ["v2.1"],
            "missing_dependencies": [],
            "model_dir": "/m/tipo",
            "default_variant": "v2.1",
            "message": "broken",
        },
        monkeypatch,
    )
    missing = _tipo_card(
        {
            "available": False,
            "weight_state": "missing",
            "installed_variants": [],
            "broken_variants": [],
            "missing_dependencies": [],
            "model_dir": "/m/tipo",
            "default_variant": "v2.1",
            "message": "missing",
        },
        monkeypatch,
    )

    assert broken["message_key"] == "models.tipo.broken"
    assert missing["message_key"] == "models.tipo.missing"
    assert broken["message_key"] != missing["message_key"]
    assert broken["message_params"]["variants"] == "v2.1"
    assert broken["status"] == "missing"


def test_inventory_tipo_card_reports_the_missing_opt_in_runtime(monkeypatch):
    card = _tipo_card(
        {
            "available": False,
            "weight_state": "missing",
            "installed_variants": [],
            "broken_variants": [],
            "missing_dependencies": ["llama_cpp", "kgen"],
            "model_dir": "/m/tipo",
            "default_variant": "v2.1",
            "message": "deps",
        },
        monkeypatch,
    )

    assert card["message_key"] == "models.tipo.missingDeps"
    assert card["message_params"]["deps"] == "llama_cpp, kgen"
    assert tipo_service.PIP_INSTALL_HINT in " ".join(card["setup_steps"])


def test_inventory_tipo_card_offers_prepare_and_is_not_recommended(monkeypatch):
    card = _tipo_card(
        {
            "available": False,
            "weight_state": "missing",
            "installed_variants": [],
            "broken_variants": [],
            "missing_dependencies": [],
            "model_dir": "/m/tipo",
            "default_variant": "v2.1",
            "message": "missing",
        },
        monkeypatch,
    )

    assert card["download_supported"] is True
    assert card["recommended"] is False
    assert "tipo" not in model_service.RECOMMENDED_MODEL_IDS
    assert any("Prepare" in step for step in card["setup_steps"])
    assert "only-binary=llama-cpp-python" in " ".join(card["setup_steps"])


def test_prepare_tipo_installs_the_runtime_group_and_does_not_fetch_weights(
    monkeypatch, tipo_dir
):
    recorded = []

    def fake_ensure_group(group):
        recorded.append(group)
        return model_service.DependencyInstallResult(installed_packages=())

    monkeypatch.setattr(model_service, "ensure_group", fake_ensure_group)
    result = model_service.ModelService().prepare_model("tipo")

    assert recorded == ["tipo"]
    assert result["status"] == "ok"
    assert result["model_id"] == "tipo"
    assert "prebuilt" in result["message"].lower()
    assert not list(tipo_dir.glob("*.gguf"))


def test_inventory_survives_a_health_dict_without_a_tipo_key(monkeypatch):
    """Partial/mocked health dicts must not crash the whole inventory."""
    card = _tipo_card(None, monkeypatch)

    assert card["available"] is False
    assert card["message_key"] == "models.tipo.missing"
    assert card["default_variant"] == "v2.1"
    assert tipo_service.WEIGHT_SIZE_HINT in " ".join(card["setup_steps"])


def test_v21_revision_is_a_commit_pin():
    spec = tipo_service.MODEL_SPECS["v2.1"]
    assert spec.repo == "KBlueLeaf/TIPO-v2.1-1B-A200M"
    assert spec.hf_filename == "gguf/TIPO-v2.1-1B-A200M-Q8_0.gguf"
    assert spec.revision == tipo_service.V21_REVISION
    assert len(spec.revision) == 40
    int(spec.revision, 16)
    assert spec.revision == spec.revision.lower()
    assert tipo_service.DEFAULT_MODEL_KEY == "v2.1"
    assert tipo_service.selectable_tipo_variants() == [
        {"id": "v2.1", "size_hint": tipo_service.WEIGHT_SIZE_HINT},
        {"id": "200m-ft", "size_hint": tipo_service.LIGHT_WEIGHT_SIZE_HINT},
    ]
    assert "100m" not in {item["id"] for item in tipo_service.selectable_tipo_variants()}


def test_v21_download_requests_the_pinned_commit(tipo_dir, monkeypatch):
    """A pin nobody passes to the downloader is decoration, not a pin."""
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        nested = Path(kwargs["local_dir"]) / kwargs["filename"]
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_bytes(GGUF_MAGIC + b"\x00" * 16)
        return str(nested)

    monkeypatch.setattr(tipo_service, "_hf_hub_download", fake_download)
    spec = tipo_service.MODEL_SPECS["v2.1"]
    path = tipo_service._download_weight(spec, tipo_dir)

    assert path == tipo_service.tipo_weight_path("v2.1", tipo_dir)
    assert path.is_file()
    assert not (tipo_dir / "gguf" / spec.filename).exists()
    assert calls == [
        {
            "repo_id": "KBlueLeaf/TIPO-v2.1-1B-A200M",
            "filename": "gguf/TIPO-v2.1-1B-A200M-Q8_0.gguf",
            "repo_type": "model",
            "local_dir": str(tipo_dir),
            "revision": tipo_service.V21_REVISION,
        }
    ]


def test_download_is_skipped_when_the_weight_file_already_exists(tipo_dir, monkeypatch):
    spec = tipo_service.MODEL_SPECS["v2.1"]
    target = tipo_service.tipo_weight_path("v2.1", tipo_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(GGUF_MAGIC + b"\x00" * 16)

    def _must_not_run(**_kwargs):
        raise AssertionError("existing GGUF must not be fetched again")

    monkeypatch.setattr(tipo_service, "_hf_hub_download", _must_not_run)
    assert tipo_service._download_weight(spec, tipo_dir) == target
