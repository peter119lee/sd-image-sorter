"""prepare_model routing table (split from services/model_service.py, 2026-07).

_prepare_model is ModelService.prepare_model's body moved here. Lazy in-function imports (tagger,
toriigate_tagger, oppai_oracle_tagger, similarity, artist_identifier,
nudenet_detector, aesthetic, config) stay in-function -- they re-resolve per
call, so sys.modules stubs keep working and startup stays light. Every
facade-family read (platform, ensure_group / ensure_group_with_soft_deps,
DependencyInstallResult, get_model_health, get_sam3_checkpoint_path, the
dir getters, _direct_download_file, _sam3_download_urls, the repair helpers,
_with_dependency_result, _dependency_restart_result, SAM3_MODELSCOPE_URL)
resolves through _svc() at call time so monkeypatches on the facade module
keep affecting behavior; ``self.download_privacy_yolo_bundle()`` became
``service.download_privacy_yolo_bundle()`` so ModelService class-attribute
patches keep working. The logger hardcodes the historical
"services.model_service" channel.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from model_download_sources import log_model_artifact_status


def _svc():
    """Resolve facade-patched seams through services.model_service at call time.

    Tests monkeypatch seam names on the facade module object; a ``from``
    import here would freeze an independent binding those patches silently
    miss. The lazy import
    avoids a facade<->submodule load cycle.
    """
    import services.model_service as model_service

    return model_service


_model_logger = logging.getLogger("services.model_service")


def _prepare_model(service: Any, model_id: str, *, source: Optional[str] = None, variant: Optional[str] = None) -> Dict[str, Any]:
    normalized_model_id = model_id.strip().lower()

    if normalized_model_id == "wd14":
        runtime_repair = _svc()._repair_wd14_onnxruntime_if_possible()

        from tagger import DEFAULT_MODEL, WD14Tagger

        model_name = variant or DEFAULT_MODEL
        tagger = WD14Tagger(model_name=model_name, use_gpu=False)
        model_path, tags_path = tagger._get_model_paths()
        result = {
            "status": "ok",
            "model_id": normalized_model_id,
            "message": f"WD14 model '{model_name}' is ready.",
            "paths": {"model_path": model_path, "tags_path": tags_path},
            "runtime_repair": runtime_repair,
        }
        if runtime_repair.get("attempted") and not runtime_repair.get("ok"):
            result["status"] = "warning"
            result["message"] = (
                f"WD14 model '{model_name}' is ready, but ONNX GPU runtime repair did not finish. "
                "Tagging may stay on CPU until the runtime is repaired."
            )
        elif runtime_repair.get("repaired"):
            result["restart_recommended"] = True
            result["message"] = (
                f"WD14 model '{model_name}' is ready. ONNX GPU runtime was repaired; "
                "restart the app before using GPU tagging."
            )
        return result


    if normalized_model_id == "toriigate":
        dependency_result = _svc().ensure_group("toriigate")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result

        runtime_health = _svc().get_torch_onnx_runtime_health()
        windows_runtime_incompatible = (
            _svc().platform.system() == "Windows"
            and runtime_health.get("runtime_compatible") is not True
        )
        if windows_runtime_incompatible:
            runtime_repair = _svc()._repair_torch_runtime_if_possible()
            if runtime_repair.get("ok") is not True:
                message = (
                    runtime_repair.get("error")
                    or runtime_health.get("runtime_compatibility_error")
                    or (
                        "ToriiGate CUDA runtime is not ready. Open Model Manager, run Prepare, "
                        "then restart the app."
                    )
                )
                return _svc()._with_dependency_result({
                    "status": "needs_runtime",
                    "model_id": normalized_model_id,
                    "ready": False,
                    "message": message,
                    "runtime_repair": runtime_repair,
                }, dependency_result)
            if runtime_repair.get("restart_required") is True:
                return _svc()._with_dependency_result({
                    "status": "needs_restart",
                    "model_id": normalized_model_id,
                    "ready": False,
                    "restart_recommended": True,
                    "message": (
                        "ToriiGate's supported cu126 runtime is prepared. Restart the app, "
                        "then run Prepare again to download or verify the model files."
                    ),
                    "runtime_repair": runtime_repair,
                }, dependency_result)

        from toriigate_tagger import ToriiGateTagger

        model_dir = _svc().get_toriigate_model_dir()
        tagger = ToriiGateTagger(model_name="toriigate-0.5", model_dir=model_dir, use_gpu=False)
        resolved_dir = tagger._download_model()
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": "ToriiGate runtime and model files are ready.",
            "paths": {"model_dir": resolved_dir},
        }, dependency_result)

    if normalized_model_id == "florence2":
        dependency_result = _svc().ensure_group("florence2")
        restart_result = _svc()._dependency_restart_result(
            normalized_model_id,
            dependency_result,
        )
        if restart_result:
            return restart_result

        from florence2_captioner import prepare_checkpoint

        checkpoint_path = prepare_checkpoint()
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": (
                "Florence-2 Base runtime and pinned model files are ready."
            ),
            "paths": {"checkpoint_path": checkpoint_path},
        }, dependency_result)

    if normalized_model_id == "oppai-oracle":
        # OppaiOracle V1.1 ONNX (~947 MB) is downloaded by the dedicated
        # OppaiOracleTagger class. No extra Python dependencies are needed
        # beyond what WD14 / ONNX Runtime already require, so we do not
        # ensure_group() here — the tagger uses huggingface_hub which is
        # already part of the lightweight core.
        from oppai_oracle_tagger import OppaiOracleTagger, DEFAULT_MODEL as OPPAI_DEFAULT
        from config import get_oppai_oracle_model_dir

        target_variant = (variant or OPPAI_DEFAULT).strip() or OPPAI_DEFAULT
        tagger = OppaiOracleTagger(
            model_name=target_variant,
            model_dir=get_oppai_oracle_model_dir(),
            use_gpu=False,
        )
        model_path, tags_path = tagger._get_model_paths()
        return {
            "status": "ok",
            "model_id": normalized_model_id,
            "message": f"OppaiOracle '{target_variant}' is ready.",
            "paths": {"model_path": model_path, "tags_path": tags_path},
        }

    if normalized_model_id == "clip":
        dependency_result = _svc().ensure_group("clip")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result
        from similarity import ensure_clip_model_ready, get_clip_text_local_model_path

        model_path = ensure_clip_model_ready()
        text_model_path = get_clip_text_local_model_path()
        if not text_model_path:
            raise RuntimeError(
                "CLIP text-query model is incomplete after Prepare / Download."
            )
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": "CLIP vision and text-query models are ready.",
            "paths": {
                "vision_model_path": model_path,
                "text_model_path": text_model_path,
            },
        }, dependency_result)

    if normalized_model_id == "artist":
        dependency_result = _svc().ensure_group_with_soft_deps("artist")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result
        from artist_identifier import prepare_artist_assets

        preferred_source = source or "auto"
        prepared = prepare_artist_assets(preferred_source)

        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": f"Artist checkpoint is ready via {prepared.get('source', preferred_source)}.",
            "paths": {
                "runtime_path": str(Path(prepared["runtime_path"]).resolve()),
                "checkpoint_path": str(Path(prepared["checkpoint_path"]).resolve()),
                "class_mapping_path": str(Path(prepared["class_mapping_path"]).resolve()),
            },
        }, dependency_result)

    if normalized_model_id == "lucida":
        dependency_result = _svc().ensure_group("lucida")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result

        from lucida_matting import prepare_checkpoint

        checkpoint_path = prepare_checkpoint()
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": "Lucida runtime and pinned model files are ready.",
            "paths": {"checkpoint_path": checkpoint_path},
        }, dependency_result)

    if normalized_model_id == "cl-tagger-v2":
        dependency_result = _svc().ensure_group("cl-tagger-v2")
        restart_result = _svc()._dependency_restart_result(
            normalized_model_id,
            dependency_result,
        )
        if restart_result:
            return restart_result

        import config
        from cl_tagger_v2 import CLTaggerV2AuthRequiredError, prepare_checkpoint

        try:
            checkpoint_path = prepare_checkpoint()
        except CLTaggerV2AuthRequiredError as exc:
            target_dir = (
                Path(config.get_cl_tagger_v2_model_dir()) / "cl-tagger-v2"
            )
            raise _svc().ExternalAuthRequiredError({
                "type": "ExternalAuthRequired",
                "provider": "Hugging Face",
                "message": str(exc),
                "manual_steps": [
                    "Open the official CL Tagger v2 Hugging Face page and accept the model terms.",
                    "Sign in with the Hugging Face account that has access to the gated repository.",
                    "Configure a Hugging Face token for this user account if the Hub requests authentication.",
                    "Return to Model Manager and retry Prepare / Download for CL Tagger v2.",
                ],
                "target_dir": str(target_dir.resolve()),
                "external_url": "https://huggingface.co/cella110n/cl_tagger_v2",
            }) from exc
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": (
                "CL Tagger v2 runtime and pinned files are ready. "
                "The gated checkpoint was fetched from official Hugging Face."
            ),
            "paths": {"checkpoint_path": checkpoint_path},
        }, dependency_result)

    if normalized_model_id == "censor-nudenet":
        dependency_result = _svc().ensure_group("nudenet")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result
        from nudenet_detector import get_nudenet_detector

        detector = get_nudenet_detector()
        detector.load()
        refreshed = _svc().get_model_health()["censor"]["nudenet"]
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": "NudeNet runtime is ready.",
            "paths": {"model_path": refreshed["model_path"]},
        }, dependency_result)

    if normalized_model_id == "censor-legacy":
        # Keep first launch light, but preserve the existing .pt YOLO path
        # once the user explicitly prepares the legacy censor model.
        dependency_result = _svc().ensure_group("yolo")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result
        downloaded = service.download_privacy_yolo_bundle()
        return _svc()._with_dependency_result({
            "status": "ok",
            "model_id": normalized_model_id,
            "message": "Privacy YOLO files were downloaded from Civitai.",
            "paths": downloaded,
        }, dependency_result)

    if normalized_model_id == "sam3":
        dependency_result = _svc().ensure_group("sam3")
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result

        def sam3_prepare_result(checkpoint_path: Optional[str]) -> Dict[str, Any]:
            health = _svc().get_model_health()["censor"]["sam3"]
            is_ready = bool(health.get("available"))
            return {
                "status": "ok" if is_ready else "needs_runtime",
                "model_id": normalized_model_id,
                "ready": is_ready,
                "message": health.get("message") or (
                    "SAM3 is ready." if is_ready else "SAM3 checkpoint is installed, but runtime setup is incomplete."
                ),
                "paths": {"checkpoint_path": checkpoint_path},
                "missing_dependencies": health.get("missing_dependencies") or [],
                "missing_dependency_packages": health.get("missing_dependency_packages") or [],
                "cuda_available": health.get("cuda_available"),
                "torch_cuda_build": health.get("torch_cuda_build"),
                "runtime_compatible": health.get("runtime_compatible"),
            }

        def repair_sam3_result(
            result: Dict[str, Any],
            checkpoint_path: str,
        ) -> Dict[str, Any]:
            runtime_repair = _svc()._repair_sam3_runtime_if_possible()
            if runtime_repair.get("ok") is not True:
                return _svc()._with_dependency_result({
                    **result,
                    "runtime_repair": runtime_repair,
                }, dependency_result)
            if runtime_repair.get("restart_required") is True:
                return _svc()._with_dependency_result({
                    **result,
                    "status": "needs_restart",
                    "ready": False,
                    "restart_recommended": True,
                    "message": (
                        "SAM3's supported cu126 runtime is prepared. Restart the app before "
                        "using SAM3 so the backend cannot reuse stale Torch DLLs."
                    ),
                    "runtime_repair": runtime_repair,
                }, dependency_result)
            return _svc()._with_dependency_result({
                **sam3_prepare_result(checkpoint_path),
                "runtime_repair": runtime_repair,
            }, dependency_result)

        checkpoint_before = _svc().get_sam3_checkpoint_path()
        if checkpoint_before:
            result = sam3_prepare_result(checkpoint_before)
            if not result.get("ready"):
                return repair_sam3_result(result, checkpoint_before)
            return _svc()._with_dependency_result(result, dependency_result)

        sam3_dir = Path(_svc().get_sam3_model_dir()) / "facebook-sam3-modelscope"
        sam3_dir.mkdir(parents=True, exist_ok=True)
        # Idempotent file-by-file fetch: skip files already on disk so users
        # who already have the giant model.safetensors don't redownload it
        # just to backfill the small config / tokenizer files.
        errors: List[str] = []
        download_pairs = _svc()._sam3_download_urls()
        for filename, url in download_pairs:
            dest = sam3_dir / filename
            if dest.exists() and dest.stat().st_size > 0:
                continue
            try:
                _svc()._direct_download_file(url, dest, timeout=900)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")
                _model_logger.warning(
                    "SAM3 file download failed: %s -> %s: %s", url, dest, exc
                )

        endpoint = "unknown"
        if download_pairs:
            parsed_endpoint = urlsplit(download_pairs[0][1])
            endpoint = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}".rstrip("/")
        missing = log_model_artifact_status(
            _model_logger,
            model_id="facebook/sam3",
            revision=None,
            endpoint=endpoint,
            model_dir=sam3_dir,
            required_files=_svc()._SAM3_DOWNLOAD_FILES,
        )

        refreshed_path = _svc().get_sam3_checkpoint_path()
        if not refreshed_path:
            detail = "; ".join(errors) if errors else "no completed downloads"
            if missing:
                detail += "; missing artifacts: " + ", ".join(missing)
            raise RuntimeError(
                f"Could not assemble SAM3 checkpoint ({detail}). "
                f"You can manually download files from {_svc().SAM3_MODELSCOPE_URL} and place them in {sam3_dir}"
            )
        result = sam3_prepare_result(refreshed_path)
        if not result.get("ready"):
            return repair_sam3_result(result, refreshed_path)
        return _svc()._with_dependency_result(result, dependency_result)

    if normalized_model_id == "aesthetic":
        dependency_result = _svc().ensure_group("aesthetic")
        # If ensure_group() actually installed torch / open_clip, the
        # cached "torch is missing" answer in aesthetic.is_available
        # would otherwise stick for the rest of this process. Reset
        # the cache before the next is_available() call below so the
        # post-install flow correctly reports "ready" instead of
        # echoing the pre-install state, and the frontend's next
        # /api/aesthetic/status poll re-runs the import check.
        try:
            from aesthetic import reset_availability_cache
            reset_availability_cache()
        except ImportError:
            # aesthetic.py imports torch lazily inside is_available, so
            # this import should never fail; defend against an aborted
            # partial install just in case.
            pass
        restart_result = _svc()._dependency_restart_result(normalized_model_id, dependency_result)
        if restart_result:
            return restart_result
        from aesthetic import (
            _ensure_loaded,
            _get_models_dir,
            get_aesthetic_backbone_path,
            is_available,
            is_fully_ready,
        )

        head_path = _get_models_dir() / "sa_0_4_vit_l_14_linear.pth"
        if not head_path.exists():
            url = "https://github.com/LAION-AI/aesthetic-predictor/raw/main/sa_0_4_vit_l_14_linear.pth"
            _svc()._direct_download_file(url, head_path, timeout=120)

        if is_available():
            _ensure_loaded()
            backbone_path = get_aesthetic_backbone_path()
            if not is_fully_ready():
                raise RuntimeError(
                    "Aesthetic predictor loaded without a verifiable local CLIP backbone. "
                    "Retry Prepare / Download."
                )
            return _svc()._with_dependency_result({
                "status": "ok",
                "model_id": normalized_model_id,
                "message": "Aesthetic predictor CLIP backbone and linear head are ready.",
                "paths": {
                    "head_path": str(head_path),
                    "backbone_path": str(backbone_path or "in-memory"),
                },
            }, dependency_result)
        return _svc()._with_dependency_result({
            "status": "needs_runtime",
            "model_id": normalized_model_id,
            "ready": False,
            "message": (
                "Aesthetic linear head is present, but the CLIP runtime could not be loaded. "
                "Restart SD Image Sorter if dependencies were just installed, then run "
                "Prepare / Download again."
            ),
            "paths": {"head_path": str(head_path)},
        }, dependency_result)

    raise ValueError(f"Model '{model_id}' cannot be prepared from the UI yet.")
