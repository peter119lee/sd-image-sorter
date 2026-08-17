"""Detection dispatch, target-family filtering, model listing, and error mapping.

Methods moved verbatim from services/censor_service.py (decomposition 2026-07) except the manifest lines: detect()
and list_models() resolve get_model_health, and _resolve_legacy_model_path
resolves get_default_legacy_model_path, through _svc() at call time. Both names
are patched on the facade module object across the reader suites, so a bare
re-import here would make those patches silently miss. The lazy in-method
imports (censor.get_detector, nudenet_detector, sam3_refiner, config) are
patched on their ORIGIN modules and move freely. Legacy YOLO publication stays
inside censor.get_detector so concurrent service calls only receive a fully
loaded detector.
"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypedDict

from fastapi import HTTPException
from PIL import Image

import database as db
from ai_runtime_guard import PRIORITY_INTERACTIVE

if TYPE_CHECKING:  # annotation-only; never imported at runtime (no facade cycle)
    from services.censor_service import CensorDetectRequest

logger = logging.getLogger("services.censor_service")


class DetectionBackendFailure(TypedDict):
    """Structured failure from one combined-detection backend."""

    backend: str
    status_code: int
    detail: str


def _svc():
    """Resolve facade-owned seams/constants through services.censor_service at call time.

    Tests patch module attributes on the facade; a from-import here would
    freeze an independent binding those patches silently miss. The lazy import avoids a facade<->mixin load cycle.
    """
    import services.censor_service as censor_service

    return censor_service


class _DetectionMixin:
    """Detection/model slice of CensorService (assembled in services/censor_service.py)."""

    @staticmethod
    def _normalize_target_family(label: str) -> str:
        normalized = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
        collapsed = normalized.replace("_", "")

        _BUTTOCKS_ALIASES = {
            "buttocks", "butt", "ass", "buttock",
            "buttocksexposed", "buttockscovered", "buttexposed",
        }
        _BREASTS_ALIASES = {
            "breasts", "breast", "malebreasts", "femalebreasts",
            "malebreast", "femalebreast", "boob", "boobs", "tits", "tit",
            "exposedbreasts", "coveredbreasts",
            "femalebreastexposed", "femalebreastcovered",
            "malebreastexposed", "malebreastcovered",
            "breastexposed", "breastcovered",
            "breastsexposed", "breastscovered",
        }
        _PUSSY_ALIASES = {
            "pussy", "vagina", "vulva", "labia",
            "femalegenitalia", "exposedgenitalia", "coveredgenitalia",
            "femalegenitaliaexposed", "femalegenitaliacovered",
            "pussyexposed",
        }
        _DICK_ALIASES = {
            "dick", "penis", "cock",
            "malegenitalia", "exposedpenis",
            "malegenitaliaexposed", "penisexposed",
        }
        _ANUS_ALIASES = {"anus", "butthole", "anusexposed", "anuscovered"}
        _CUM_ALIASES = {"cum", "semen"}

        if collapsed in _BUTTOCKS_ALIASES:
            return "buttocks"
        if collapsed in _BREASTS_ALIASES:
            return "breasts"
        if collapsed in _PUSSY_ALIASES:
            return "pussy"
        if collapsed in _DICK_ALIASES:
            return "dick"
        if collapsed in _ANUS_ALIASES:
            return "anus"
        if collapsed in _CUM_ALIASES:
            return "cum"
        return normalized

    @classmethod
    def _filter_detections_by_targets(
        cls,
        detections: List[Dict[str, Any]],
        target_classes: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        if target_classes is None:
            return detections

        normalized_targets = {
            cls._normalize_target_family(target)
            for target in target_classes
            if str(target or "").strip()
        }
        if not normalized_targets:
            return []

        filtered = []
        for detection in detections:
            detection_family = cls._normalize_target_family(detection.get("class", ""))
            if detection_family in normalized_targets:
                filtered.append(detection)
        return filtered

    @staticmethod
    def _has_polygon_geometry(detection: Dict[str, Any]) -> bool:
        if detection.get("mask") is not None:
            return True
        polygon = detection.get("polygon")
        if not isinstance(polygon, list):
            return False
        points = [
            point for point in polygon
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        return len(points) >= 3

    @staticmethod
    def _detection_error_to_http(exc: Exception) -> HTTPException:
        """Map an unexpected detection failure to a categorized, actionable HTTP error.

        Distinguishes the three causes users can actually act on instead of a
        cause-free 500 "Detection failed":
          1. model file missing / unreadable / unloadable  -> 503
          2. required dependency missing (onnxruntime / nudenet / opencv) -> 503
          3. image unreadable / corrupt / unsupported       -> 422
        Anything else keeps a 500 but now echoes the real error text (no longer
        cause-free). Reuses the detectors' own RuntimeError messages (e.g.
        nudenet's "nudenet not installed. Run: pip install nudenet"), so the
        actionable "pip install X" survives to the client.
        """
        from PIL import UnidentifiedImageError

        message = str(exc) or exc.__class__.__name__
        lowered = message.lower()

        # (3) Image unreadable / corrupt — most specific type first
        # (UnidentifiedImageError subclasses OSError, so check it before models).
        if isinstance(exc, UnidentifiedImageError) or any(
            hint in lowered
            for hint in ("cannot identify image", "broken data stream", "image file is truncated")
        ):
            return HTTPException(
                status_code=422,
                detail=(
                    "The image could not be read for detection; it may be corrupt "
                    f"or an unsupported format. ({message}) / "
                    f"无法读取该图片进行检测，图片可能已损坏或格式不受支持。（{message}）"
                ),
            )

        # (2) Required Python dependency missing.
        if isinstance(exc, ImportError) or any(
            hint in lowered
            for hint in ("not installed", "pip install", "no module named", "modulenotfounderror")
        ):
            return HTTPException(
                status_code=503,
                detail=(
                    "A required detection dependency is missing. Install it and "
                    f"restart the app, then retry. ({message}) / "
                    f"缺少检测所需的依赖，请安装后重启应用再重试。（{message}）"
                ),
            )

        # (1) Model file missing / unreadable / could not be loaded.
        if isinstance(exc, FileNotFoundError) or any(
            hint in lowered
            for hint in ("model file not found", "model not loaded", "failed to load")
        ):
            return HTTPException(
                status_code=503,
                detail=(
                    "The censor model is missing or could not be loaded. Download "
                    f"or select a detection model, then retry. ({message}) / "
                    f"打码模型缺失或无法加载，请先下载或选择检测模型后重试。（{message}）"
                ),
            )

        # Fallback: still a 500, but no longer cause-free — surface the real error.
        return HTTPException(
            status_code=500,
            detail=f"Detection failed: {message} / 检测失败：{message}",
        )

    @classmethod
    def _describe_detection_backend_failure(
        cls,
        backend: str,
        exc: Exception,
    ) -> DetectionBackendFailure:
        """Normalize one detector failure without hiding its actionable cause."""
        mapped = exc if isinstance(exc, HTTPException) else cls._detection_error_to_http(exc)
        return {
            "backend": backend,
            "status_code": int(mapped.status_code),
            "detail": str(mapped.detail),
        }

    @staticmethod
    def _combined_detection_failure_to_http(
        image_id: int,
        failures: List[DetectionBackendFailure],
    ) -> HTTPException:
        """Return one actionable error when no combined detector completed."""
        status_codes = {failure["status_code"] for failure in failures}
        status_code = next(iter(status_codes)) if len(status_codes) == 1 else 500
        reasons = "; ".join(
            f'{failure["backend"]}: {failure["detail"]}'
            for failure in failures
        )
        return HTTPException(
            status_code=status_code,
            detail=(
                f"Both detection engines failed for image_id={image_id}. {reasons}"
            ),
        )

    @staticmethod
    def _combined_detection_warning(
        failure: DetectionBackendFailure,
        successful_backends: List[str],
    ) -> str:
        """Explain that combined detection completed with only one backend."""
        successful_label = ", ".join(successful_backends)
        return (
            f'{failure["backend"]} detection failed; combined mode used '
            f'{successful_label} only. {failure["detail"]}'
        )

    def detect(self, request: CensorDetectRequest) -> Dict[str, Any]:
        """
        Run detection on an image to find regions to censor.

        Supports multiple detection backends:
        - Legacy YOLOv8 ONNX: General segmentation model
        - NudeNet v3: Specialized NSFW body part detection
        - Both: Combine results from both detectors

        Every backend here is asked for at ``PRIORITY_INTERACTIVE``: this is one
        image with the user watching the censor canvas. The detectors themselves
        default to the normal lane, so the lane is declared once, here, by the
        caller that knows the context.
        """
        image = db.get_image_by_id(request.image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        image_path = self._resolve_source_image_path(
            image["path"],
            image_id=request.image_id,
            action_label="Auto Censor",
        )

        try:
            model_type = request.model_type
            detection_warnings: List[str] = []

            if model_type == "sam3":
                from sam3_refiner import get_sam3_refiner
                sam3_health = _svc().get_model_health()["censor"]["sam3"]
                if not sam3_health["available"]:
                    raise HTTPException(status_code=503, detail=sam3_health.get("message", "SAM3 is not available"))
                refiner = get_sam3_refiner()
                custom_prompts = None
                if request.text_prompts:
                    custom_prompts = [
                        {"prompt": p.strip(), "class": p.strip()}
                        for p in request.text_prompts if p.strip()
                    ]
                try:
                    with Image.open(image_path) as img:
                        detections = refiner.detect_privacy_regions(
                            img,
                            conf_threshold=request.confidence_threshold,
                            prompts=custom_prompts,
                            priority=PRIORITY_INTERACTIVE,
                        )
                except RuntimeError as exc:
                    # SAM3 load / CUDA failures surface the real reason (503),
                    # rather than being masked as a generic 500 "Detection failed".
                    raise HTTPException(status_code=503, detail=str(exc)) from exc

            elif model_type == "nudenet":
                from nudenet_detector import get_nudenet_detector
                detector = get_nudenet_detector()
                try:
                    detections = detector.detect(
                        image_path,
                        conf_threshold=request.confidence_threshold,
                        exposed_only=request.exposed_only,
                        priority=PRIORITY_INTERACTIVE,
                    )
                except RuntimeError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

            elif model_type == "both":
                all_detections: List[Dict[str, Any]] = []
                successful_backends: List[str] = []
                backend_failures: List[DetectionBackendFailure] = []

                try:
                    from nudenet_detector import get_nudenet_detector
                    nn_det = get_nudenet_detector()
                    nn_results = nn_det.detect(
                        image_path,
                        conf_threshold=request.confidence_threshold,
                        exposed_only=request.exposed_only,
                        priority=PRIORITY_INTERACTIVE,
                    )
                    all_detections.extend({**detection, "source": "nudenet"} for detection in nn_results)
                    successful_backends.append("NudeNet")
                except Exception as exc:
                    failure = self._describe_detection_backend_failure("NudeNet", exc)
                    backend_failures.append(failure)
                    logger.warning(
                        "Combined detection backend failed",
                        extra={
                            "backend": failure["backend"],
                            "status_code": failure["status_code"],
                            "image_id": request.image_id,
                            "detail": failure["detail"],
                        },
                    )

                try:
                    from censor import get_detector
                    from config import PROJECT_ROOT

                    legacy_model_path = self._resolve_legacy_model_path(
                        request.model_path,
                        allowed_base=str(PROJECT_ROOT / "models"),
                    )
                    if legacy_model_path:
                        detector = get_detector(legacy_model_path)
                        legacy_results = detector.detect(
                            image_path,
                            request.confidence_threshold,
                            priority=PRIORITY_INTERACTIVE,
                        )
                        all_detections.extend({**detection, "source": "legacy"} for detection in legacy_results)
                        successful_backends.append("Legacy YOLO")
                except Exception as exc:
                    failure = self._describe_detection_backend_failure("Legacy YOLO", exc)
                    backend_failures.append(failure)
                    logger.warning(
                        "Combined detection backend failed",
                        extra={
                            "backend": failure["backend"],
                            "status_code": failure["status_code"],
                            "image_id": request.image_id,
                            "detail": failure["detail"],
                        },
                    )

                if not successful_backends:
                    raise self._combined_detection_failure_to_http(
                        request.image_id,
                        backend_failures,
                    )

                detection_warnings = [
                    self._combined_detection_warning(failure, successful_backends)
                    for failure in backend_failures
                ]

                detections = all_detections

            else:
                from censor import get_detector
                from config import PROJECT_ROOT

                legacy_model_path = self._resolve_legacy_model_path(
                    request.model_path,
                    allowed_base=str(PROJECT_ROOT / "models"),
                )

                detector = get_detector(legacy_model_path)
                detections = detector.detect(
                    image_path,
                    request.confidence_threshold,
                    priority=PRIORITY_INTERACTIVE,
                )

            filtered_detections = self._filter_detections_by_targets(detections, request.target_classes)

            polygon_count = sum(1 for d in filtered_detections if self._has_polygon_geometry(d))
            with Image.open(image_path) as image_for_mask:
                combined_mask_payload = self._build_combined_mask_payload(
                    image_for_mask.size,
                    filtered_detections,
                    include_boxes=polygon_count != len(filtered_detections),
                )

            clean_detections = []
            for d in filtered_detections:
                clean = {k: v for k, v in d.items() if k != "mask"}
                if not self._has_polygon_geometry(clean):
                    clean.pop("polygon", None)
                clean_detections.append(clean)
            if not filtered_detections:
                geometry_mode = "none"
            elif polygon_count == len(filtered_detections):
                geometry_mode = "mask"
            elif polygon_count > 0:
                geometry_mode = "mixed"
            else:
                geometry_mode = "box"

            return {
                "status": "ok",
                "image_id": request.image_id,
                "model_type": model_type,
                "detections": clean_detections,
                "selected_target_classes": request.target_classes or [],
                "combined_mask": combined_mask_payload["combined_mask"],
                "combined_mask_ref": combined_mask_payload["combined_mask_ref"],
                "combined_mask_bounds": combined_mask_payload["combined_mask_bounds"],
                "image_width": combined_mask_payload["image_width"],
                "image_height": combined_mask_payload["image_height"],
                "geometry_mode": geometry_mode,
                "warnings": detection_warnings,
            }
        except HTTPException:
            raise
        except Exception as exc:
            error_trace = traceback.format_exc()
            logger.error("Detection error:\n%s", error_trace)
            raise self._detection_error_to_http(exc) from exc

    def list_models(self) -> Dict[str, Any]:
        """List available detection backends and their status."""
        health = _svc().get_model_health()["censor"]
        legacy = health["legacy"]
        nudenet = health["nudenet"]
        sam3 = health["sam3"]

        models = [
            {
                "id": "legacy",
                "name": "Legacy YOLO",
                "description": "Uses the built-in local YOLO model from models/yolo. The recommended file is the privacy-part detector; generic YOLO26/YOLOv8 files are listed for compatibility tests only.",
                "available": legacy["available"],
                "requires_model_path": False,
                "recommended": legacy["available"] and legacy.get("privacy_model_count", 0) > 0,
                "default_model_path": legacy["default_model_path"],
                "message": legacy["message"],
                "files": legacy["files"],
                "has_yolo26": legacy["has_yolo26"],
                "has_yolov8s": legacy["has_yolov8s"],
                "privacy_model_count": legacy.get("privacy_model_count", 0),
                "general_model_count": legacy.get("general_model_count", 0),
                "simple_user_advice": legacy.get("simple_user_advice"),
                "advanced_user_advice": legacy.get("advanced_user_advice"),
                "capabilities": {
                    "input_mode_label": "Fixed built-in model classes",
                    "output_mode_label": "Model-dependent legacy detection",
                    "supports_text_prompt": False,
                    "supports_mask_output": any(
                        bool((file_info.get("capabilities") or {}).get("supports_mask_output"))
                        for file_info in legacy.get("files", [])
                    ),
                },
            },
            {
                "id": "nudenet",
                "name": "NudeNet v3",
                "description": "Recommended for NSFW region detection. No manual model path required.",
                "available": nudenet["available"],
                # The runtime can be installed while the ONNX weights are not yet
                # on disk - the nudenet library downloads them on the first detect
                # call (a ~2 min blocking fetch). Expose this so the UI can warn
                # before a cold run instead of looking frozen.
                "model_downloaded": bool(nudenet.get("model_downloaded")),
                "requires_model_path": False,
                "recommended": nudenet["available"],
                "message": nudenet["message"],
                "model_path": nudenet["model_path"],
                "capabilities": nudenet.get("capabilities", {}),
            },
            {
                "id": "sam3",
                "name": "SAM 3",
                "description": "Used after detection to refine masks or segment by text prompt.",
                "available": sam3["available"],
                "requires_model_path": False,
                "recommended": sam3["available"],
                "message": sam3["message"],
                "checkpoint_path": sam3["checkpoint_path"],
                "missing_dependencies": sam3["missing_dependencies"],
                "capabilities": sam3.get("capabilities", {}),
            },
        ]

        return {
            "status": "ok",
            "models": models,
            "recommended_backend": (
                "both"
                if nudenet["available"] and legacy["available"] and legacy.get("privacy_model_count", 0) > 0
                else ("nudenet" if nudenet["available"] else ("legacy" if legacy["available"] else None))
            ),
        }

    @staticmethod
    def _resolve_legacy_model_path(requested_path: str, *, allowed_base: str) -> str:
        """Pick a safe legacy YOLO path, falling back to the built-in default."""
        from utils.path_validation import ALLOWED_MODEL_EXTENSIONS, validate_file_path

        normalized = str(requested_path or "").strip()
        if normalized:
            is_valid, error = validate_file_path(
                normalized,
                ALLOWED_MODEL_EXTENSIONS,
                allowed_base=allowed_base,
            )
            if not is_valid:
                raise HTTPException(status_code=400, detail=error or "Invalid model path")
            return str(Path(os.path.abspath(normalized)))

        default_model_path = _svc().get_default_legacy_model_path()
        if default_model_path:
            return default_model_path

        raise HTTPException(
            status_code=503,
            detail="No local legacy YOLO model was found in models/yolo. Download one there or switch to NudeNet.",
        )
