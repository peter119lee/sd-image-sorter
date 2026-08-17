"""
SD Image Sorter - Censor Module
YOLOv8 ONNX-based detection and censoring for sensitive content.

Requires a YOLOv8 ONNX model trained to detect body parts.
Recommended model: https://civitai.com/models/1736285
"""

import json
import threading
import logging
import os
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
from typing import List, Dict, Tuple, Optional

from config import (
    CENSOR_DEFAULT_CLASSES,
    CENSOR_CONFIDENCE_THRESHOLD,
    CENSOR_IOU_THRESHOLD,
    YOLO_INPUT_SIZE,
    CENSOR_DEFAULT_BLOCK_SIZE,
    CENSOR_DEFAULT_BLUR_RADIUS,
)
from ai_runtime_guard import PRIORITY_NORMAL, exclusive_ai_runtime

logger = logging.getLogger(__name__)

# Lazy import: onnxruntime is loaded on first use (see CensorDetector.load)
ort = None


# Shared canonical class name mapping used by both the detector and model_health
_CLASS_NAME_ALIASES = {
    "breast": "breasts",
    "breasts": "breasts",
    "boob": "breasts",
    "boobs": "breasts",
    "tits": "breasts",
    "tit": "breasts",
    "vagina": "pussy",
    "vulva": "pussy",
    "pussy": "pussy",
    "labia": "pussy",
    "penis": "dick",
    "dick": "dick",
    "cock": "dick",
    "cum": "cum",
    "semen": "cum",
    "anus": "anus",
    "butthole": "anus",
}


def canonicalize_class_name(class_name: str) -> str:
    """Normalize a YOLO class name to a canonical form.

    This is the single source of truth for class name aliasing.
    Used by CensorDetector and model_health.
    """
    normalized = str(class_name or "").strip().lower().replace("_", " ").replace("-", " ")
    collapsed = normalized.replace(" ", "")
    return _CLASS_NAME_ALIASES.get(collapsed, normalized)


def _ensure_ort():
    """Lazily import onnxruntime."""
    global ort
    if ort is None:
        from runtime_env import prepare_onnxruntime_environment

        prepare_onnxruntime_environment()
        import onnxruntime as ort_module  # type: ignore
        ort = ort_module
        preload = getattr(ort, "preload_dlls", None)
        if callable(preload):
            try:
                preload()
            except Exception as exc:
                logger.debug("onnxruntime.preload_dlls() was not usable: %s", exc)


_cv2 = None


def _try_import_cv2():
    """Lazily import OpenCV (Apache-2.0). Used only to turn decoded YOLOv8-seg
    masks into polygon contours; absence degrades gracefully to box geometry."""
    global _cv2
    if _cv2 is None:
        try:
            import cv2 as _cv2_module  # type: ignore
            _cv2 = _cv2_module
        except Exception:
            _cv2 = False
    return _cv2 or None


class CensorDetector:
    """YOLOv8 ONNX detector for sensitive body parts."""

    def __init__(self, model_path: Optional[str] = None, classes: Optional[List[str]] = None):
        self.model_path = model_path
        self.session = None
        self.runtime = None
        self.runtime_backend = None
        self.supports_masks = False
        self._onnx_segmentation = False
        self.requested_classes = list(classes) if classes else None
        self.raw_classes = list(classes) if classes else list(CENSOR_DEFAULT_CLASSES)
        self.classes = list(classes) if classes else list(CENSOR_DEFAULT_CLASSES)
        self.input_size = YOLO_INPUT_SIZE

    @staticmethod
    def _canonicalize_class_name(class_name: str) -> str:
        return canonicalize_class_name(class_name)

    def _set_classes(self, class_names: List[str]):
        cleaned = [str(name).strip() for name in class_names if str(name).strip()]
        if not cleaned:
            cleaned = list(self.requested_classes) if self.requested_classes else list(CENSOR_DEFAULT_CLASSES)
        self.raw_classes = cleaned
        self.classes = [self._canonicalize_class_name(name) for name in cleaned]

    @staticmethod
    def _names_from_mapping(mapping) -> List[str]:
        if isinstance(mapping, dict):
            ordered = []
            for key in sorted(mapping.keys(), key=lambda item: int(item) if str(item).isdigit() else str(item)):
                ordered.append(str(mapping[key]))
            return ordered
        if isinstance(mapping, list):
            return [str(item) for item in mapping]
        return []

    def _load_onnx_metadata(self, session):
        try:
            metadata = session.get_modelmeta().custom_metadata_map or {}
            raw_names = metadata.get("names")
            if not raw_names:
                return
            try:
                parsed = json.loads(raw_names) if isinstance(raw_names, str) else raw_names
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid class name format in model metadata, using fallback")
                parsed = raw_names if not isinstance(raw_names, str) else None
            names = self._names_from_mapping(parsed)
            if names:
                self._set_classes(names)
        except Exception as exc:
            logger.debug("Could not parse ONNX metadata names for %s: %s", self.model_path, exc)

    def _supports_lightweight_onnx(self, session) -> bool:
        outputs = session.get_outputs()
        if not outputs:
            return False

        output_shape = outputs[0].shape
        if len(output_shape) != 3:
            return False

        channel_dim = output_shape[1]
        if not isinstance(channel_dim, int):
            return False

        expected_channels = 4 + len(self.classes)
        if len(outputs) > 1:
            expected_channels += 32
        return channel_dim == expected_channels

    @staticmethod
    def _onnx_has_segmentation_outputs(session) -> bool:
        try:
            return len(session.get_outputs()) > 1
        except Exception:
            return False

    def _load_with_ultralytics(self, model_path: str):
        os.environ["YOLO_AUTOINSTALL"] = "false"
        from ultralytics import YOLO

        logger.info("Loading model with Ultralytics runtime: %s", model_path)
        with exclusive_ai_runtime("censor-ultralytics-load"):
            model = YOLO(model_path)
        self.runtime = model
        self.session = model
        self.runtime_backend = "ultralytics"
        self.supports_masks = "seg" in os.path.basename(model_path).lower()
        self.input_size = YOLO_INPUT_SIZE

        names = self._names_from_mapping(getattr(model, "names", {}))
        if names:
            self._set_classes(names)

    @staticmethod
    def _lookup_runtime_name(names, class_id: int) -> str:
        if isinstance(names, dict):
            return str(names.get(class_id, names.get(str(class_id), f"class_{class_id}")))
        if isinstance(names, list) and 0 <= class_id < len(names):
            return str(names[class_id])
        return f"class_{class_id}"

    def _detect_with_ultralytics(
        self,
        image_source,
        conf_threshold: float,
        priority: int = PRIORITY_NORMAL,
    ) -> List[Dict]:
        """Shared ultralytics inference for detect() and detect_from_image().

        ``priority`` comes from the public method so the lane follows the CALLER,
        not this helper: pinning it here would apply one lane to every entry
        point regardless of whether a user is waiting on the result.
        """
        if self.runtime is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        device = "cpu"
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                device = 0
        except Exception as exc:
            logger.debug("torch/CUDA not available for censor: %s", exc)
            device = "cpu"

        with exclusive_ai_runtime("censor-ultralytics-inference", priority=priority):
            results = self.runtime.predict(image_source, conf=conf_threshold, device=device, verbose=False)
        if not results:
            return []

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        detections = []
        names = getattr(result, "names", getattr(self.runtime, "names", {}))
        masks = getattr(result, "masks", None)
        polygons = getattr(masks, "xy", None) if masks is not None else None
        for index in range(len(boxes)):
            class_id = int(boxes.cls[index].item())
            confidence = float(boxes.conf[index].item())
            x1, y1, x2, y2 = [int(round(value)) for value in boxes.xyxy[index].tolist()]
            class_name = self._canonicalize_class_name(self._lookup_runtime_name(names, class_id))
            detection = {
                "class": class_name,
                "class_id": class_id,
                "confidence": confidence,
                "box": [x1, y1, x2, y2],
            }

            if polygons is not None and index < len(polygons):
                polygon = polygons[index].tolist()
                if polygon:
                    detection["polygon"] = [
                        [float(point[0]), float(point[1])]
                        for point in polygon
                        if isinstance(point, (list, tuple)) and len(point) >= 2
                    ]

            detections.append(detection)

        return detections
        
    def load(self, model_path: Optional[str] = None):
        """Load the selected YOLO model with the most compatible runtime."""
        _ensure_ort()

        if model_path:
            self.model_path = model_path

        if not self.model_path or not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found: {self.model_path}")

        extension = os.path.splitext(self.model_path)[1].lower()
        if extension in {".pt", ".pth"}:
            try:
                self._load_with_ultralytics(self.model_path)
                logger.info("Censor detector loaded via Ultralytics: %s", os.path.basename(self.model_path))
                logger.info("Classes: %d", len(self.classes))
                return
            except ImportError as exc:
                raise RuntimeError(
                    "Cannot load a PyTorch YOLO model because 'ultralytics' is not installed.\n\n"
                    "Install ultralytics or switch to an ONNX model in models/yolo."
                ) from exc
            except Exception as exc:
                logger.error("Error loading PyTorch YOLO model: %s", exc)
                raise RuntimeError(f"Failed to load PyTorch YOLO model: {exc}") from exc

        try:
            # Create ONNX Runtime session
            # Note: We assign to a temp variable first to ensure full success before setting self.session
            logger.info("Initializing ONNX session for: %s", self.model_path)
            available_providers = ort.get_available_providers()
            providers = [provider for provider in ['CUDAExecutionProvider', 'CPUExecutionProvider'] if provider in available_providers]
            if not providers:
                providers = ['CPUExecutionProvider']
            sess_options = ort.SessionOptions()
            # Adaptive thread count mirroring tagger._build_session_options:
            # GPU sessions stay lean (the GPU does the work, CPU just feeds it),
            # CPU sessions scale with available cores instead of a fixed cap so
            # large-batch censoring isn't throttled on high-core machines.
            import multiprocessing
            cpu_count = max(1, multiprocessing.cpu_count())
            gpu_enabled = 'CUDAExecutionProvider' in providers
            num_threads = 2 if gpu_enabled else min(cpu_count, max(2, cpu_count // 2))
            sess_options.intra_op_num_threads = num_threads
            sess_options.inter_op_num_threads = max(1, num_threads // 2)
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            sess_options.enable_cpu_mem_arena = 'CUDAExecutionProvider' not in providers
            sess_options.enable_mem_pattern = 'CUDAExecutionProvider' not in providers
            with exclusive_ai_runtime("censor-onnx-load"):
                session = ort.InferenceSession(self.model_path, sess_options=sess_options, providers=providers)
            
            # Get input details
            input_info = session.get_inputs()[0]
            self.input_name = input_info.name
            
            # Update input size from model if available
            if len(input_info.shape) == 4:
                _, _, h, w = input_info.shape
                if isinstance(h, int) and isinstance(w, int):
                    self.input_size = (w, h)

            self._load_onnx_metadata(session)

            has_seg = self._onnx_has_segmentation_outputs(session)
            lightweight_ok = self._supports_lightweight_onnx(session)
            # Native seg-mask decoding needs OpenCV (Apache-2.0) to trace contours.
            native_seg_ok = has_seg and lightweight_ok and _try_import_cv2() is not None

            # Only reach for Ultralytics (AGPL) when we genuinely cannot parse the
            # model ourselves: a seg model whose masks we can't decode natively, or
            # any output layout the lightweight parser does not understand. When we
            # CAN decode seg masks natively we stay on ONNX Runtime (lighter, faster,
            # no AGPL dependency) and still emit pixel-accurate polygons.
            needs_ultralytics = (has_seg and not native_seg_ok) or (not lightweight_ok)
            if needs_ultralytics:
                reason = (
                    "exposes segmentation outputs we cannot decode natively"
                    if (has_seg and not native_seg_ok)
                    else "uses an output layout the lightweight parser does not support"
                )
                try:
                    logger.info(
                        "Model %s %s. Trying Ultralytics runtime so masks are preserved.",
                        os.path.basename(self.model_path), reason,
                    )
                    self._load_with_ultralytics(self.model_path)
                    logger.info("Censor detector loaded via Ultralytics: %s", os.path.basename(self.model_path))
                    logger.info("Classes: %d", len(self.classes))
                    return
                except ImportError:
                    if not lightweight_ok:
                        raise
                    logger.warning(
                        "Ultralytics is unavailable; continuing with the lightweight ONNX parser "
                        "(box geometry only, segmentation masks not preserved)."
                    )
                except Exception as exc:
                    if not lightweight_ok:
                        raise
                    logger.warning(
                        "Could not switch %s to Ultralytics runtime (%s). Continuing with the lightweight ONNX parser.",
                        os.path.basename(self.model_path), exc,
                    )

            self.session = session
            self.runtime = None
            self.runtime_backend = "onnxruntime"
            self._onnx_segmentation = native_seg_ok
            self.supports_masks = native_seg_ok
            logger.info(
                "Censor detector loaded (ONNX%s): %s",
                ", native segmentation" if native_seg_ok else "",
                os.path.basename(self.model_path),
            )
            logger.info("Input size: %s, Classes: %d", self.input_size, len(self.classes))
            
        except Exception as e:
            logger.error("Error loading ONNX model: %s", e)
            self.session = None  # Ensure it's None on failure
            self.runtime = None
            self.runtime_backend = None
            
            # Provide helpful error message
            error_msg = str(e)
            if "Protobuf" in error_msg or "INVALID_PROTOBUF" in error_msg:
                raise RuntimeError(
                    "ONNX model file appears to be corrupted or invalid.\n\n"
                    "If this is a .pt file, it cannot be loaded as ONNX directly. "
                    "The automatic conversion requires 'ultralytics' to be installed:\n"
                    "  pip install ultralytics\n\n"
                    "If this is an .onnx file, it may be corrupted. Try re-exporting it."
                )
            raise e
        
    def preprocess(self, image: Image.Image) -> Tuple[np.ndarray, Tuple[float, float], Tuple[int, int]]:
        """Preprocess image for YOLOv8 inference."""
        original_size = image.size  # (width, height)
        
        # Resize with letterboxing to maintain aspect ratio
        img_w, img_h = original_size
        target_w, target_h = self.input_size
        
        scale = min(target_w / img_w, target_h / img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        
        # Resize
        resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Create padded image
        padded = Image.new('RGB', self.input_size, (114, 114, 114))
        pad_x = (target_w - new_w) // 2
        pad_y = (target_h - new_h) // 2
        padded.paste(resized, (pad_x, pad_y))
        
        # Convert to numpy and normalize
        img_array = np.array(padded, dtype=np.float32) / 255.0
        
        # HWC to CHW, add batch dimension
        img_array = img_array.transpose(2, 0, 1)
        img_array = np.expand_dims(img_array, axis=0)
        
        # Return scale info for postprocessing
        scale_info = (scale, scale)
        pad_info = (pad_x, pad_y)
        
        return img_array, scale_info, pad_info
    
    def postprocess(
        self,
        outputs: np.ndarray,
        original_size: Tuple[int, int],
        scale_info: Tuple[float, float],
        pad_info: Tuple[int, int],
        conf_threshold: float = CENSOR_CONFIDENCE_THRESHOLD,
        iou_threshold: float = CENSOR_IOU_THRESHOLD,
        proto: Optional[np.ndarray] = None,
    ) -> List[Dict]:
        """Postprocess YOLOv8 outputs to detection boxes.

        When ``proto`` (the YOLOv8-seg mask-prototype output) is provided, each
        kept detection also gets a pixel-accurate ``polygon`` traced from its
        instance mask, so censoring follows the actual shape instead of a box.
        """
        predictions = np.squeeze(outputs).T
        
        # Extract boxes (x_center, y_center, width, height)
        boxes = predictions[:, :4]
        
        # Handle segmentation models (channels > 4 + num_classes)
        num_classes = len(self.classes)
        
        if predictions.shape[1] > 4 + num_classes:
            # Segmentation model detected: Only use class columns, ignore mask coeffs
            scores = predictions[:, 4:4+num_classes]
        else:
            scores = predictions[:, 4:]
        
        # Get max class score and class id for each box
        class_ids = np.argmax(scores, axis=1)
        confidences = np.max(scores, axis=1)
        
        # Filter by confidence
        mask = confidences >= conf_threshold
        boxes = boxes[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        # Capture the 32 mask coefficients (seg models) for the surviving boxes,
        # aligned with ``boxes`` so NMS-kept indices line up with their masks.
        seg_coeffs = None
        if proto is not None and predictions.shape[1] > 4 + num_classes:
            seg_coeffs = predictions[:, 4 + num_classes:][mask]

        if len(boxes) == 0:
            return []
        
        # Convert from center to corner format
        x_center, y_center, width, height = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = x_center - width / 2
        y1 = y_center - height / 2
        x2 = x_center + width / 2
        y2 = y_center + height / 2

        # Keep a copy in model-input (letterboxed) coordinates for seg-mask cropping,
        # before we unscale the display boxes back to original-image coordinates.
        x1_in, y1_in, x2_in, y2_in = x1.copy(), y1.copy(), x2.copy(), y2.copy()

        # Unscale
        scale_x, scale_y = scale_info
        pad_x, pad_y = pad_info
        
        x1 = (x1 - pad_x) / scale_x
        y1 = (y1 - pad_y) / scale_y
        x2 = (x2 - pad_x) / scale_x
        y2 = (y2 - pad_y) / scale_y
        
        # Clip
        orig_w, orig_h = original_size
        x1 = np.clip(x1, 0, orig_w)
        y1 = np.clip(y1, 0, orig_h)
        x2 = np.clip(x2, 0, orig_w)
        y2 = np.clip(y2, 0, orig_h)
        
        # NMS
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)
        indices = self._nms(boxes_xyxy, confidences, iou_threshold)
        
        detections = []
        for i in indices:
            class_id = int(class_ids[i])
            class_name = self.classes[class_id] if class_id < len(self.classes) else f"class_{class_id}"

            detection = {
                "class": class_name,
                "class_id": class_id,
                "confidence": float(confidences[i]),
                "box": [int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])],
            }

            if seg_coeffs is not None:
                polygon = self._decode_seg_polygon(
                    seg_coeffs[i],
                    proto,
                    (x1_in[i], y1_in[i], x2_in[i], y2_in[i]),
                    scale_info,
                    pad_info,
                    original_size,
                )
                if polygon:
                    detection["polygon"] = polygon

            detections.append(detection)

        return detections

    def _decode_seg_polygon(self, coeff, proto, box_input_xyxy, scale_info, pad_info, original_size):
        """Assemble one YOLOv8-seg instance mask (prototype masks · coefficients),
        then trace it to a polygon in ORIGINAL-image coordinates.

        ``proto``  : ndarray [num_proto, ph, pw] mask prototypes (model-input space).
        ``coeff``  : ndarray [num_proto] mask coefficients for this detection.
        ``box_input_xyxy`` : detection box in model-input (letterboxed) coordinates,
                     used to crop the mask so neighbouring instances don't bleed in.
        Returns a list of [x, y] points, or None (no contour / OpenCV unavailable).
        """
        cv2 = _try_import_cv2()
        if cv2 is None or proto is None:
            return None
        try:
            proto_arr = np.asarray(proto, dtype=np.float32)
            if proto_arr.ndim == 4:
                proto_arr = proto_arr[0]
            num_proto, ph, pw = proto_arr.shape
            sigmoid = 1.0 / (1.0 + np.exp(-(coeff.astype(np.float32) @ proto_arr.reshape(num_proto, -1))))
            mask = sigmoid.reshape(ph, pw)

            in_w, in_h = self.input_size
            sx, sy = pw / float(in_w), ph / float(in_h)
            bx1, by1, bx2, by2 = box_input_xyxy
            cx1 = int(np.clip(np.floor(bx1 * sx), 0, pw))
            cx2 = int(np.clip(np.ceil(bx2 * sx), 0, pw))
            cy1 = int(np.clip(np.floor(by1 * sy), 0, ph))
            cy2 = int(np.clip(np.ceil(by2 * sy), 0, ph))
            if cx2 <= cx1 or cy2 <= cy1:
                return None
            cropped = np.zeros_like(mask)
            cropped[cy1:cy2, cx1:cx2] = mask[cy1:cy2, cx1:cx2]

            binary = (cropped >= 0.5).astype(np.uint8)
            if int(binary.sum()) == 0:
                return None
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            contour = max(contours, key=cv2.contourArea)
            epsilon = 0.01 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
            if len(approx) < 3:
                return None

            # Map proto-space -> model-input space -> original image (reverse letterbox).
            up_x, up_y = in_w / float(pw), in_h / float(ph)
            scale_x, scale_y = scale_info
            pad_x, pad_y = pad_info
            orig_w, orig_h = original_size
            polygon = []
            for px, py in approx:
                ix, iy = float(px) * up_x, float(py) * up_y
                ox = float(np.clip((ix - pad_x) / scale_x, 0, orig_w))
                oy = float(np.clip((iy - pad_y) / scale_y, 0, orig_h))
                polygon.append([ox, oy])
            return polygon if len(polygon) >= 3 else None
        except Exception as exc:
            logger.debug("Seg-mask polygon decode failed: %s", exc)
            return None
    
    def _nms(self, boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
        """Non-maximum suppression."""
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        
        order = scores.argsort()[::-1]
        keep = []
        
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            if order.size == 1:
                break
            
            # Compute IoU
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)  # Added epsilon to prevent div/0
            
            # Keep boxes with IoU below threshold
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        
        return keep
    
    def detect(
        self,
        image_path: str,
        conf_threshold: float = CENSOR_CONFIDENCE_THRESHOLD,
        priority: int = PRIORITY_NORMAL,
    ) -> List[Dict]:
        """Run detection on an image file.

        ``priority`` defaults to the normal lane; the single-image detect service
        raises it to ``PRIORITY_INTERACTIVE``. Any bulk caller added later gets
        the normal lane by simply not asking, which is the behaviour that keeps
        the interactive lane meaningful.
        """
        if self.session is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if self.runtime_backend == "ultralytics":
            return self._detect_with_ultralytics(image_path, conf_threshold, priority)

        # Load and preprocess image
        with Image.open(image_path) as source_image:
            image = source_image.convert('RGB')
        original_size = image.size

        img_array, scale_info, pad_info = self.preprocess(image)

        # Run inference
        with exclusive_ai_runtime("censor-onnx-inference", priority=priority):
            outputs = self.session.run(None, {self.input_name: img_array})

        # Postprocess
        proto = outputs[1] if (self._onnx_segmentation and len(outputs) > 1) else None
        detections = self.postprocess(
            outputs[0],
            original_size,
            scale_info,
            pad_info,
            conf_threshold,
            proto=proto,
        )

        return detections

    def detect_from_image(
        self,
        image: Image.Image,
        conf_threshold: float = CENSOR_CONFIDENCE_THRESHOLD,
        priority: int = PRIORITY_NORMAL,
    ) -> List[Dict]:
        """Run detection on a PIL Image."""
        if self.session is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if self.runtime_backend == "ultralytics":
            return self._detect_with_ultralytics(image, conf_threshold, priority)
        
        original_size = image.size
        img_array, scale_info, pad_info = self.preprocess(image)
        
        with exclusive_ai_runtime("censor-onnx-inference", priority=priority):
            outputs = self.session.run(None, {self.input_name: img_array})
        
        proto = outputs[1] if (self._onnx_segmentation and len(outputs) > 1) else None
        detections = self.postprocess(
            outputs[0],
            original_size,
            scale_info,
            pad_info,
            conf_threshold,
            proto=proto,
        )
        
        return detections


# The Pillow censoring transforms were split out to censor_transforms.py
# (2026-07). `Censor` is re-exported BY REFERENCE so `censor.Censor` stays
# the SAME class object: services/censor/output_io.py lazily does
# `from censor import Censor`, and tests string-patch
# "censor.Censor.apply_censoring" -- class-attribute patches propagate only
# through a shared object.
from censor_transforms import Censor


# Global detector instance (lazy loaded)
_detector: Optional[CensorDetector] = None
_detector_lock = threading.Lock()


def _detector_requires_reload(
    detector: Optional[CensorDetector],
    model_path: Optional[str],
) -> bool:
    """Return whether the requested detector must be constructed and loaded."""
    if detector is None:
        return True
    if not model_path:
        return False
    return detector.model_path != model_path or detector.session is None


def get_detector(model_path: Optional[str] = None) -> CensorDetector:
    """Get or create the global detector instance."""
    global _detector

    current_detector = _detector
    if current_detector is not None and not _detector_requires_reload(
        current_detector,
        model_path,
    ):
        return current_detector

    with _detector_lock:
        current_detector = _detector
        if current_detector is not None and not _detector_requires_reload(
            current_detector,
            model_path,
        ):
            return current_detector

        detector = CensorDetector(model_path)
        if model_path:
            # Publish only after load succeeds so a failed replacement cannot
            # displace the previous healthy detector.
            detector.load()
        _detector = detector
        return detector
