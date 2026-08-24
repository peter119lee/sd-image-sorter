"""Model-inventory branch table (split from services/model_service.py, 2026-07).

_build_inventory is ModelService.build_model_inventory's body moved here: the facade method fetches
``health = get_model_health()`` (facade-bound seam) and delegates; every
remaining facade-family read (PROJECT_ROOT, TAGGER_MODELS, the config dir
getters, PRIVACY_YOLO_PAGE_URL, SAM3_MODELSCOPE_URL, RECOMMENDED_MODEL_IDS)
resolves through _svc() at call time so monkeypatches on the facade module
keep affecting behavior. The SAM3 card's setup_steps copy stays in the
facade (_sam3_inventory_setup_steps) because tests/test_release_build.py
asserts those literal strings in backend/services/model_service.py's raw
source text.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List


def _svc():
    """Resolve facade-patched seams through services.model_service at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy import
    avoids a facade<->submodule load cycle.
    """
    import services.model_service as model_service

    return model_service


def _tipo_pip_hint() -> str:
    """The exact opt-in install command, read from the module that owns it."""
    from services.tipo_service import PIP_INSTALL_HINT

    return PIP_INSTALL_HINT


def _tipo_weight_size() -> str:
    """User-facing size of the default TIPO GGUF, owned by tipo_service."""
    from services.tipo_service import WEIGHT_SIZE_HINT

    return WEIGHT_SIZE_HINT


def _tipo_light_weight_size() -> str:
    from services.tipo_service import LIGHT_WEIGHT_SIZE_HINT

    return LIGHT_WEIGHT_SIZE_HINT


def _tipo_selectable_variants() -> list:
    from services.tipo_service import selectable_tipo_variants

    return selectable_tipo_variants()


def _tipo_default_variant() -> str:
    from services.tipo_service import DEFAULT_MODEL_KEY

    return DEFAULT_MODEL_KEY


def _build_inventory(health: Dict[str, Any]) -> List[Dict[str, Any]]:
    censor = health["censor"]
    artist = health["artist"]
    lucida = health.get("lucida", {})
    florence2 = health.get("florence2", {})
    cl_tagger_v2 = health.get("cl_tagger_v2", {})
    installed_wd14 = [item["name"] for item in health["wd14"]["installed_models"] if item["available"]]
    wd14_default_ready = bool(health["wd14"].get("available"))
    wd14_primary_path = None
    if wd14_default_ready and health["wd14"].get("model_path"):
        wd14_primary_path = health["wd14"]["model_path"]
    elif installed_wd14:
        first_variant = installed_wd14[0]
        wd14_primary_path = str(
            (Path(_svc().get_wd14_model_dir()) / first_variant / _svc().TAGGER_MODELS[first_variant]["model_file"]).resolve()
        )

    aesthetic_available = False
    aesthetic_message = "Aesthetic predictor dependencies are not installed"
    aesthetic_head_path = str(_svc().PROJECT_ROOT / "models" / "aesthetic" / "sa_0_4_vit_l_14_linear.pth")
    aesthetic_head_exists = (
        Path(aesthetic_head_path).is_file()
        and Path(aesthetic_head_path).stat().st_size > 0
    )
    aesthetic_runtime_ready = (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("open_clip") is not None
    )
    aesthetic_backbone_path = None
    aesthetic_loaded = False
    try:
        from aesthetic import get_aesthetic_backbone_path, is_predictor_loaded

        resolved_backbone = get_aesthetic_backbone_path()
        aesthetic_backbone_path = (
            str(resolved_backbone) if resolved_backbone is not None else None
        )
        aesthetic_loaded = is_predictor_loaded()
    except (ImportError, OSError):
        aesthetic_backbone_path = None
        aesthetic_loaded = False
    aesthetic_available = bool(
        aesthetic_head_exists
        and aesthetic_runtime_ready
        and (aesthetic_loaded or aesthetic_backbone_path)
    )
    if aesthetic_available:
        aesthetic_message = "Aesthetic predictor is ready (CLIP + linear head)."
    elif aesthetic_head_exists:
        aesthetic_message = (
            "Aesthetic linear head is present, but the CLIP backbone or runtime is missing. "
            "Run Prepare / Download (~1.7 GB CLIP ViT-L/14 + head). "
            "Scoring may still fetch missing files from Hugging Face."
        )

    def with_status(*, is_ready: bool, is_downloaded: bool) -> Dict[str, str]:
        if is_ready:
            return {"status": "ready", "status_label": "Ready"}
        return {"status": "missing", "status_label": "Missing"}

    # -- WD14 --
    if wd14_default_ready:
        wd14_message_key = "models.wd14.readyCount"
        wd14_message = f"{len(installed_wd14)} WD14 variant(s) are ready."
        wd14_message_params = {"count": len(installed_wd14)}
    else:
        wd14_message_key = "models.wd14.missing"
        wd14_message = "Default WD14 files (wd-swinv2-tagger-v3) are missing. Run Prepare / Download."
        wd14_message_params = {}

    # -- ToriiGate --
    toriigate = health.get("toriigate", {})
    toriigate_available = bool(toriigate.get("available"))
    toriigate_dir = toriigate.get("model_dir") or str(Path(_svc().get_toriigate_model_dir()) / "toriigate-0.5")

    if florence2.get("available"):
        florence2_message_key = "models.florence2.ready"
    elif florence2.get("missing_dependencies"):
        florence2_message_key = "models.florence2.missingDeps"
    else:
        florence2_message_key = "models.florence2.missing"

    # -- OppaiOracle --
    oppai_oracle = health.get("oppai_oracle", {})
    oppai_oracle_available = bool(oppai_oracle.get("available"))
    oppai_oracle_dir = oppai_oracle.get("model_dir") or ""

    # -- CLIP --
    clip_health = health["clip"]
    clip_runtime_loaded = clip_health.get("runtime_loaded", False)
    clip_text_runtime_loaded = clip_health.get("text_runtime_loaded", False)
    clip_available = bool(
        clip_health.get(
            "feature_ready",
            clip_health["available"] or clip_runtime_loaded,
        )
        or (clip_runtime_loaded and clip_text_runtime_loaded)
    )
    if clip_runtime_loaded and clip_text_runtime_loaded and not clip_health.get("feature_ready"):
        clip_message_key = "models.clip.loaded"
        clip_message = "CLIP vision and text models are loaded and ready."
    elif clip_health.get("feature_ready", clip_health["available"]):
        clip_message_key = "models.clip.ready"
        clip_message = clip_health["message"]
    elif clip_health.get("model_downloaded") and not clip_health.get("text_model_downloaded"):
        clip_message_key = "models.clip.missingText"
        clip_message = clip_health["message"]
    elif clip_health["model_path"]:
        clip_message_key = "models.clip.missingRuntime"
        clip_message = clip_health["message"]
    else:
        clip_message_key = "models.clip.missingModel"
        clip_message = clip_health["message"]

    # -- Aesthetic --
    if aesthetic_available:
        aesthetic_msg_key = "models.aesthetic.ready"
    elif aesthetic_head_exists:
        aesthetic_msg_key = "models.aesthetic.headOnly"
    else:
        aesthetic_msg_key = "models.aesthetic.missing"

    # -- Artist --
    if artist["available"]:
        artist_message_key = "models.artist.ready"
    elif not artist.get("checkpoint_path") and not artist.get("has_download_source"):
        artist_message_key = "models.artist.noSource"
    else:
        artist_message_key = "models.artist.missing"

    if lucida.get("available"):
        lucida_message_key = "models.lucida.ready"
    elif lucida.get("missing_dependencies"):
        lucida_message_key = "models.lucida.missingDeps"
    else:
        lucida_message_key = "models.lucida.missing"

    if cl_tagger_v2.get("available"):
        cl_tagger_v2_message_key = "models.clTaggerV2.ready"
    elif cl_tagger_v2.get("missing_dependencies"):
        cl_tagger_v2_message_key = "models.clTaggerV2.missingDeps"
    else:
        cl_tagger_v2_message_key = "models.clTaggerV2.missing"

    # -- TIPO (prompt expansion) --
    # Ordered so the owner can tell the three states apart at a glance: a
    # half-written GGUF ("broken") must never render as "not downloaded yet",
    # which is the whole reason this card exists.
    tipo = health.get("tipo", {})
    tipo_available = bool(tipo.get("available"))
    tipo_broken_variants = list(tipo.get("broken_variants") or [])
    tipo_missing_deps = list(tipo.get("missing_dependencies") or [])
    if tipo_available:
        tipo_message_key = "models.tipo.ready"
    elif tipo.get("weight_state") == "broken":
        tipo_message_key = "models.tipo.broken"
    elif tipo_missing_deps:
        tipo_message_key = "models.tipo.missingDeps"
    else:
        tipo_message_key = "models.tipo.missing"

    # -- Censor Legacy --
    legacy = censor["legacy"]
    privacy_yolo_files = [f for f in legacy.get("files", []) if f.get("recommended_for_censor")]
    general_yolo_files = [f for f in legacy.get("files", []) if not f.get("recommended_for_censor")]
    if legacy["available"] and privacy_yolo_files:
        if general_yolo_files:
            censor_legacy_key = "models.censorLegacy.readyPrivacyWithGeneral"
        else:
            censor_legacy_key = "models.censorLegacy.readyPrivacy"
    elif legacy["available"]:
        censor_legacy_key = "models.censorLegacy.readyNonPrivacy"
    else:
        censor_legacy_key = "models.censorLegacy.missing"

    # -- NudeNet --
    nudenet = censor["nudenet"]
    if nudenet["available"] and nudenet.get("model_downloaded"):
        nudenet_key = "models.censorNudenet.ready"
    elif nudenet["available"]:
        nudenet_key = "models.censorNudenet.installed"
    else:
        nudenet_key = "models.censorNudenet.missing"

    # -- SAM3 --
    sam3 = censor["sam3"]
    sam3_missing_packages = sam3.get("missing_dependency_packages") or sam3.get("missing_dependencies") or []
    sam3_message_params = {"deps": ", ".join(sam3_missing_packages)}
    if sam3["available"]:
        sam3_key = "models.sam3.ready"
    elif sam3["checkpoint_path"] and sam3_missing_packages and sam3.get("torch_version") and sam3.get("torch_cuda_build") is None:
        sam3_key = "models.sam3.missingDepsCpuTorch"
    elif sam3["checkpoint_path"] and sam3_missing_packages:
        sam3_key = "models.sam3.missingDeps"
    elif sam3["checkpoint_path"]:
        if sam3.get("torch_cuda_build") is None:
            sam3_key = "models.sam3.cpuTorch"
        elif not sam3.get("cuda_available"):
            sam3_key = "models.sam3.noCuda"
        else:
            sam3_key = "models.sam3.missing"
    else:
        sam3_key = "models.sam3.missing"

    inventory = [
        {
            "id": "wd14",
            "name": "WD14 Tagger",
            "group": "Tagging",
            "group_key": "models.group.tagging",
            "available": wd14_default_ready,
            **with_status(is_ready=wd14_default_ready, is_downloaded=bool(installed_wd14) or wd14_default_ready),
            "message": wd14_message,
            "message_key": wd14_message_key,
            "message_params": wd14_message_params,
            "path": health["wd14"]["model_path"] or wd14_primary_path,
            "download_supported": True,
            "variants": [item["name"] for item in health["wd14"]["installed_models"]],
            # The variant list follows TAGGER_MODELS insertion order (eva02
            # is first), but the recommended default is swinv2. Surface the
            # default so the card's <select> pre-selects it and one-click
            # Prepare downloads the recommended model, not the heavy eva02.
            # .get(): production health always sets default_model, but a
            # partial/mocked health dict must not crash the whole inventory.
            "default_variant": health["wd14"].get("default_model"),
            "installed_variants": installed_wd14,
            "setup_steps": [
                "Click Prepare / Download to download the selected WD14 model files if missing.",
                "On Windows, the same action also repairs ONNX GPU packages so CUDA/DirectML can appear.",
                "Restart SD Image Sorter if the Prepare result says ONNX Runtime was repaired.",
            ],
        },
        {
            "id": "toriigate",
            "name": "ToriiGate 0.5",
            "group": "Captioning",
            "group_key": "models.group.captioning",
            "available": toriigate_available,
            **with_status(
                is_ready=toriigate_available,
                is_downloaded=bool(Path(toriigate_dir).joinpath("config.json").exists()),
            ),
            "message": toriigate.get("message") or "ToriiGate captioner files are not downloaded yet. Run Prepare / Download (~9.6 GB BF16).",
            "message_key": "models.toriigate.ready" if toriigate_available else "models.toriigate.missing",
            "path": toriigate_dir,
            "download_supported": True,
            "setup_steps": [
                "Click Prepare / Download to install the PyTorch/Transformers runtime if missing.",
                "Restart SD Image Sorter if the Prepare result says Python packages were installed.",
                "Click Prepare / Download again to download the ToriiGate captioner files (~9.6 GB BF16) if they are not present.",
            ],
        },
        {
            "id": "florence2",
            "name": "Florence-2 Base",
            "group": "Captioning",
            "group_key": "models.group.captioning",
            "available": bool(florence2.get("available")),
            **with_status(
                is_ready=bool(florence2.get("available")),
                is_downloaded=bool(florence2.get("checkpoint_path")),
            ),
            "message": (
                florence2.get("message")
                or "Florence-2 Base setup is incomplete."
            ),
            "message_key": florence2_message_key,
            "message_params": {
                "deps": ", ".join(florence2.get("missing_dependencies") or []),
            },
            "path": (
                florence2.get("checkpoint_path")
                or florence2.get("expected_path", "")
            ),
            "download_supported": True,
            "default_variant": "base",
            "default_model": "florence-community/Florence-2-base",
            "recommended": True,
            "note": (
                "Native Transformers local natural-language captioner. It is not a "
                "booru tagger and does not participate in tag voting."
            ),
            "setup_steps": [
                "Click Prepare / Download to install the Florence-2 runtime if needed.",
                "Restart SD Image Sorter if Python packages were installed.",
                "Click Prepare / Download again to fetch the commit-pinned model.",
            ],
            "external_links": [
                {
                    "label": "HuggingFace",
                    "url": (
                        "https://huggingface.co/"
                        "florence-community/Florence-2-base"
                    ),
                },
            ],
        },
        {
            "id": "oppai-oracle",
            "name": "OppaiOracle V1.1",
            "group": "Tagging",
            "group_key": "models.group.tagging",
            "available": oppai_oracle_available,
            **with_status(
                is_ready=oppai_oracle_available,
                is_downloaded=oppai_oracle_available,
            ),
            "message": oppai_oracle.get("message") or "OppaiOracle V1.1 (~947 MB ONNX) is not downloaded yet.",
            "message_key": "models.oppaiOracle.ready" if oppai_oracle_available else "models.oppaiOracle.missing",
            "path": oppai_oracle_dir,
            "download_supported": True,
            "setup_steps": [
                "Click Prepare / Download to fetch the OppaiOracle V1.1 ONNX bundle (~947 MB) from HuggingFace.",
                "No additional Python packages are required; ONNX Runtime is already installed.",
                "Once ready, OppaiOracle V1.1 will appear in the tagger model dropdown.",
            ],
        },
        {
            "id": "cl-tagger-v2",
            "name": "CL Tagger v2",
            "group": "Tagging",
            "group_key": "models.group.tagging",
            "available": bool(cl_tagger_v2.get("available")),
            **with_status(
                is_ready=bool(cl_tagger_v2.get("available")),
                is_downloaded=bool(cl_tagger_v2.get("checkpoint_path")),
            ),
            "message": (
                cl_tagger_v2.get("message")
                or "CL Tagger v2 setup is incomplete."
            ),
            "message_key": cl_tagger_v2_message_key,
            "message_params": {
                "deps": ", ".join(cl_tagger_v2.get("missing_dependencies") or []),
            },
            "path": (
                cl_tagger_v2.get("checkpoint_path")
                or cl_tagger_v2.get("expected_path", "")
            ),
            "download_supported": True,
            "default_variant": "v2_00",
            "default_model": "cella110n/cl_tagger_v2:v2_00",
            "gated_download": True,
            "requires_auth": True,
            "note": (
                "Gated model. The portable package contains no weights; Prepare / Download "
                "fetches the user-authorized checkpoint only from the official Hugging Face repository."
            ),
            "setup_steps": [
                "Accept the cl_tagger_v2 model terms on the official Hugging Face page and configure a token if the repository requests one.",
                "Click Prepare / Download to install the small runtime group and fetch the pinned v2_00 files.",
                "Restart SD Image Sorter if Python packages were installed, then run Prepare / Download again if the first attempt requested it.",
            ],
            "external_links": [
                {
                    "label": "HuggingFace",
                    "url": "https://huggingface.co/cella110n/cl_tagger_v2",
                },
            ],
        },
        {
            "id": "tipo",
            "name": "TIPO Prompt Expansion",
            "group": "Tagging",
            "group_key": "models.group.tagging",
            "available": tipo_available,
            **with_status(
                is_ready=tipo_available,
                is_downloaded=bool(tipo.get("installed_variants")),
            ),
            "message": tipo.get("message") or "TIPO prompt expansion is not set up yet.",
            "message_key": tipo_message_key,
            "message_params": {
                "deps": ", ".join(tipo_missing_deps),
                "variants": ", ".join(tipo_broken_variants),
            },
            "path": tipo.get("model_dir") or "",
            "download_supported": True,
            "default_variant": tipo.get("default_variant") or _tipo_default_variant(),
            "installed_variants": list(tipo.get("installed_variants") or []),
            "selectable_variants": _tipo_selectable_variants(),
            "note": (
                "Expands a danbooru TAG LIST, not natural-language prose. "
                "Proposals are vocabulary-gated and never auto-applied. "
                "v2.1 and the older v2 1B model are the same RAM class (~1–2 GB); "
                "200m-ft is the lighter CPU option. No dedicated GPU is required."
            ),
            "setup_steps": [
                "Click Prepare / Download. The app installs a prebuilt CPU llama.cpp wheel "
                "(official extra-index, no C compiler) plus tipo-kgen. "
                f"Manual equivalent: {_tipo_pip_hint()}",
                "Restart SD Image Sorter if it asks, then click Prepare again.",
                f"Choose v2.1 (~{_tipo_weight_size()}) or the lighter 200m-ft "
                f"(~{_tipo_light_weight_size()}) in Reverse Prompt / Dataset Maker; "
                "the selected GGUF downloads on first Suggest.",
                "If this card says the files are unreadable, delete them from that path and run it again.",
            ],
            "external_links": [
                {
                    "label": "KGen / TIPO",
                    "url": "https://github.com/KohakuBlueleaf/KGen",
                },
            ],
        },
        {
            "id": "clip",
            "name": "CLIP Similarity",
            "group": "Search",
            "group_key": "models.group.search",
            "available": clip_available,
            **with_status(
                is_ready=bool(clip_available),
                is_downloaded=bool(clip_health["model_path"] or clip_runtime_loaded),
            ),
            "message": clip_message,
            "message_key": clip_message_key,
            "path": clip_health["model_path"] or clip_health.get("expected_path", ""),
            "text_path": clip_health.get("text_model_path") or clip_health.get("expected_text_path", ""),
            "download_supported": True,
            "setup_steps": [
                "Click Prepare to install fastembed Python package (restart required after install).",
                "Click Prepare again after restart to download and verify both CLIP ViT-B/32 ONNX towers (~600 MB total).",
                "Vision requires model.onnx + config.json + preprocessor_config.json in " + clip_health.get("expected_path", "data/models/clip/Qdrant-clip-ViT-B-32-vision"),
                "Text queries require model.onnx + config.json + tokenizer.json + tokenizer_config.json + special_tokens_map.json in " + clip_health.get("expected_text_path", "data/models/clip/Qdrant-clip-ViT-B-32-text"),
            ],
        },
        {
            "id": "aesthetic",
            "name": "Aesthetic Predictor",
            "group": "Scoring",
            "group_key": "models.group.scoring",
            "available": aesthetic_available,
            **with_status(is_ready=aesthetic_available, is_downloaded=aesthetic_head_exists),
            "message": aesthetic_message,
            "message_key": aesthetic_msg_key,
            "path": aesthetic_head_path if aesthetic_head_exists else None,
            "download_supported": True,
            "note": "Uses CLIP ViT-L/14 + the LAION linear head. Prepare / Download validates both before reporting ready.",
            "backbone_path": aesthetic_backbone_path,
        },
        {
            "id": "artist",
            "name": "Artist ID / Kaloscope",
            "group": "Artist ID",
            "group_key": "models.group.artistId",
            "available": artist["available"],
            **with_status(
                is_ready=bool(artist["available"]),
                is_downloaded=bool(artist["checkpoint_path"] or artist["runtime_path"]),
            ),
            "message": artist["message"],
            "message_key": artist_message_key,
            "path": artist["checkpoint_path"] or artist.get("expected_path", ""),
            "download_supported": bool(artist.get("has_download_source", True)),
            "sources": [
                s for s in ["auto", "huggingface", "modelscope"]
                if s == "auto"
                or (s == "huggingface" and artist.get("huggingface_available"))
                or (s == "modelscope" and artist.get("modelscope_available"))
            ],
            "runtime_path": artist["runtime_path"],
            "setup_steps": [
                "Click Prepare to install torch/transformers/timm Python packages (restart required).",
                "Click Prepare again after restart to download Kaloscope 2.0 model (~2.8 GB).",
                "Source: HuggingFace (heathcliff01/Kaloscope2.0) or ModelScope (Heathcliff02/Kaloscope-2.0) — pick via the Download Source selector above.",
                "Manual: put best_checkpoint.pth in " + str(Path(_svc().get_artist_model_dir()) / "kaloscope2.0" / "448-90.13"),
                "Manual: put class_mapping.csv in " + str(Path(_svc().get_artist_model_dir()) / "kaloscope2.0"),
                "Manual: the LSNet runtime (lsnet_model/) goes in " + str(Path(_svc().get_artist_model_dir()) / "comfyui-lsnet-runtime"),
            ],
        },
        {
            "id": "lucida",
            "name": "Lucida",
            "group": "Training Masks",
            "group_key": "models.group.trainingMasks",
            "available": bool(lucida.get("available")),
            **with_status(
                is_ready=bool(lucida.get("available")),
                is_downloaded=bool(lucida.get("checkpoint_path")),
            ),
            "message": lucida.get("message") or "Lucida setup is incomplete.",
            "message_key": lucida_message_key,
            "message_params": {
                "deps": ", ".join(lucida.get("missing_dependencies") or []),
            },
            "path": lucida.get("checkpoint_path") or lucida.get("expected_path", ""),
            "download_supported": True,
            "default_variant": "pinned",
            "default_model": "egeorcun/lucida",
            "recommended": True,
            "note": (
                "MIT-licensed weights. Training data includes research-only datasets; "
                "commercial users should make their own assessment."
            ),
            "setup_steps": [
                "Click Prepare / Download to install the Lucida runtime if needed.",
                "Restart SD Image Sorter if Python packages were installed.",
                "Click Prepare / Download again to fetch the commit-pinned Lucida checkpoint (~885 MB).",
            ],
            "external_links": [
                {
                    "label": "HuggingFace",
                    "url": "https://huggingface.co/egeorcun/lucida",
                },
                {
                    "label": "GitHub",
                    "url": "https://github.com/egeorcun/lucida",
                },
            ],
        },
        {
            "id": "censor-legacy",
            "name": "Privacy YOLO",
            "group": "Censor",
            "group_key": "models.group.censor",
            "available": legacy["available"],
            **with_status(
                is_ready=bool(legacy["available"]),
                is_downloaded=bool(legacy["default_model_path"]),
            ),
            "message": legacy["message"],
            "message_key": censor_legacy_key,
            "path": legacy["default_model_path"] or legacy.get("expected_path", ""),
            "download_supported": True,
            "external_links": [
                {
                    "label": "Civitai",
                    "url": _svc().PRIVACY_YOLO_PAGE_URL,
                }
            ],
            "setup_steps": [
                "Click Prepare to auto-download the recommended privacy YOLO model.",
                "If auto-download fails (Civitai login wall), download manually from the Civitai link above.",
                "Place the .pt file in " + str(Path(_svc().get_yolo_model_dir())),
            ],
        },
        {
            "id": "censor-nudenet",
            "name": "NudeNet v3",
            "group": "Censor",
            "group_key": "models.group.censor",
            # ``available`` is the runtime-installation signal exposed by the
            # health contract.  A detector is not usable until its 320n.onnx
            # weights are also present, so bulk-download readiness must use
            # the complete artifact state instead of the runtime alone.
            "available": nudenet["available"],
            "runtime_available": bool(nudenet["available"]),
            **with_status(
                is_ready=bool(nudenet["available"] and nudenet.get("model_downloaded")),
                is_downloaded=bool(nudenet.get("model_downloaded")),
            ),
            "message": nudenet["message"],
            "message_key": nudenet_key,
            "path": nudenet["model_path"],
            "download_supported": True,
        },
        {
            "id": "sam3",
            "name": "SAM 3",
            "group": "Censor",
            "group_key": "models.group.censor",
            "available": sam3["available"],
            **with_status(
                is_ready=bool(sam3["available"]),
                is_downloaded=bool(sam3["checkpoint_path"]),
            ),
            "message": sam3["message"],
            "message_key": sam3_key,
            "message_params": sam3_message_params,
            "path": sam3["checkpoint_path"] or sam3.get("expected_path", ""),
            "download_supported": True,
            "setup_steps": _svc()._sam3_inventory_setup_steps(),
            "external_links": [
                {
                    "label": "ModelScope",
                    "url": _svc().SAM3_MODELSCOPE_URL,
                }
            ],
        },
    ]
    # MODELS-07: flag the essentials so the Model Manager can render them
    # first with a Recommended badge. Optional/advanced models (ToriiGate,
    # OppaiOracle, Wenaka Privacy YOLO) fall into the "additional" section.
    for entry in inventory:
        entry["recommended"] = entry["id"] in _svc().RECOMMENDED_MODEL_IDS
    return inventory
