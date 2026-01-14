"""
SD Image Sorter - Censor Module
YOLOv8 ONNX-based detection and censoring for sensitive content.

Requires a YOLOv8 ONNX model trained to detect body parts.
Recommended model: https://civitai.com/models/1736285
"""

import os
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
from typing import List, Dict, Tuple, Optional
import onnxruntime as ort


class CensorDetector:
    """YOLOv8 ONNX detector for sensitive body parts."""
    
    # Class names matching the Wenaka YOLO model (wenaka_yolov8s-seg.pt)
    # Reference: https://github.com/Wenaka2004/auto-censor
    # Class IDs: 0=anus, 1=cum, 2=dick, 3=breasts, 4=pussy
    DEFAULT_CLASSES = [
        "anus",     # 0
        "cum",      # 1
        "dick",     # 2
        "breasts",  # 3
        "pussy",    # 4
    ]
    
    def __init__(self, model_path: str = None, classes: List[str] = None):
        self.model_path = model_path
        self.session = None
        self.classes = classes or self.DEFAULT_CLASSES
        self.input_size = (640, 640)  # Standard YOLOv8 input size
        
    def load(self, model_path: str = None):
        """Load the ONNX model, converting from .pt if necessary."""
        if model_path:
            self.model_path = model_path
            
        if not self.model_path or not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found: {self.model_path}")
        
        # Handle .pt files by auto-converting to ONNX
        if self.model_path.lower().endswith('.pt') or self.model_path.lower().endswith('.pth'):
            try:
                print(f"Detected PyTorch model: {self.model_path}")
                
                # Check if already converted (cache)
                base_path = os.path.splitext(self.model_path)[0] + ".onnx"
                if os.path.exists(base_path):
                    print(f"Found cached ONNX model: {base_path}")
                    self.model_path = base_path
                else:
                    print("Attempting to convert to ONNX using ultralytics...")
                    try:
                        from ultralytics import YOLO
                    except ImportError:
                        raise RuntimeError(
                            "Cannot load .pt model: 'ultralytics' package not installed.\n\n"
                            "To fix this issue, install ultralytics:\n"
                            "  pip install ultralytics\n\n"
                            "Alternatively, export your model to ONNX format first:\n"
                            "  from ultralytics import YOLO\n"
                            "  model = YOLO('your_model.pt')\n"
                            "  model.export(format='onnx')\n"
                        )
                    
                    # Load and export
                    model = YOLO(self.model_path)
                    # Export returns the path to the exported file
                    exported_path = model.export(format='onnx')
                    
                    if exported_path and isinstance(exported_path, str):
                        self.model_path = exported_path
                        print(f"Model converted successfully: {self.model_path}")
                    else:
                        # Fallback if export returns something else or fails silently
                        if os.path.exists(base_path):
                            self.model_path = base_path
                            print(f"Using exported ONNX model: {self.model_path}")
                        else:
                            raise RuntimeError("Export failed or returned invalid path")
                        
            except ImportError as e:
                # Already handled above with better message
                raise e
            except Exception as e:
                print(f"Error converting .pt model: {e}")
                raise RuntimeError(
                    f"Failed to convert PyTorch model to ONNX.\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Please ensure:\n"
                    f"1. The model file is valid and not corrupted\n"
                    f"2. You have 'ultralytics' installed: pip install ultralytics\n"
                    f"3. Or manually export the model to .onnx format"
                )

        try:
            # Create ONNX Runtime session
            # Note: We assign to a temp variable first to ensure full success before setting self.session
            print(f"Initializing ONNX session for: {self.model_path}")
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            session = ort.InferenceSession(self.model_path, providers=providers)
            
            # Get input details
            input_info = session.get_inputs()[0]
            self.input_name = input_info.name
            
            # Update input size from model if available
            if len(input_info.shape) == 4:
                _, _, h, w = input_info.shape
                if isinstance(h, int) and isinstance(w, int):
                    self.input_size = (w, h)
            
            self.session = session
            print(f"Censor detector loaded: {os.path.basename(self.model_path)}")
            print(f"Input size: {self.input_size}, Classes: {len(self.classes)}")
            
        except Exception as e:
            print(f"Error loading ONNX model: {str(e)}")
            self.session = None  # Ensure it's None on failure
            
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
        conf_threshold: float = 0.60,
        iou_threshold: float = 0.45
    ) -> List[Dict]:
        """Postprocess YOLOv8 outputs to detection boxes."""
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
        
        if len(boxes) == 0:
            return []
        
        # Convert from center to corner format
        x_center, y_center, width, height = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = x_center - width / 2
        y1 = y_center - height / 2
        x2 = x_center + width / 2
        y2 = y_center + height / 2
        
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
            
            detections.append({
                "class": class_name,
                "class_id": class_id,
                "confidence": float(confidences[i]),
                "box": [int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])]
            })
        
        return detections
    
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
    
    def detect(self, image_path: str, conf_threshold: float = 0.6) -> List[Dict]:
        """Run detection on an image file."""
        if self.session is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        
        # Load and preprocess image
        image = Image.open(image_path).convert('RGB')
        original_size = image.size
        
        img_array, scale_info, pad_info = self.preprocess(image)
        
        # Run inference
        outputs = self.session.run(None, {self.input_name: img_array})
        
        # Postprocess
        detections = self.postprocess(
            outputs[0], 
            original_size,
            scale_info, 
            pad_info,
            conf_threshold
        )
        
        return detections
    
    def detect_from_image(self, image: Image.Image, conf_threshold: float = 0.6) -> List[Dict]:
        """Run detection on a PIL Image."""
        if self.session is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        
        original_size = image.size
        img_array, scale_info, pad_info = self.preprocess(image)
        
        outputs = self.session.run(None, {self.input_name: img_array})
        
        detections = self.postprocess(
            outputs[0],
            original_size,
            scale_info,
            pad_info,
            conf_threshold
        )
        
        return detections


class Censor:
    """Image censoring utilities."""
    
    @staticmethod
    def apply_mosaic(
        image: Image.Image, 
        regions: List[Tuple[int, int, int, int]], 
        block_size: int = 16
    ) -> Image.Image:
        """Apply mosaic/pixelation to regions."""
        result = image.copy()
        
        for x1, y1, x2, y2 in regions:
            # Ensure valid coordinates
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(image.width, x2), min(image.height, y2)
            
            if x2 <= x1 or y2 <= y1:
                continue
            
            # Extract region
            region = result.crop((x1, y1, x2, y2))
            
            # Pixelate: resize down then up
            w, h = region.size
            small_w = max(1, w // block_size)
            small_h = max(1, h // block_size)
            
            small = region.resize((small_w, small_h), Image.Resampling.NEAREST)
            pixelated = small.resize((w, h), Image.Resampling.NEAREST)
            
            # Paste back
            result.paste(pixelated, (x1, y1))
        
        return result
    
    @staticmethod
    def apply_bar(
        image: Image.Image,
        regions: List[Tuple[int, int, int, int]],
        color: Tuple[int, int, int] = (0, 0, 0)
    ) -> Image.Image:
        """Apply solid color bar to regions."""
        result = image.copy()
        draw = ImageDraw.Draw(result)
        
        for x1, y1, x2, y2 in regions:
            draw.rectangle([x1, y1, x2, y2], fill=color)
        
        return result
    
    @staticmethod
    def apply_blur(
        image: Image.Image,
        regions: List[Tuple[int, int, int, int]],
        blur_radius: int = 20
    ) -> Image.Image:
        """Apply gaussian blur to regions."""
        result = image.copy()
        
        for x1, y1, x2, y2 in regions:
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(image.width, x2), min(image.height, y2)
            
            if x2 <= x1 or y2 <= y1:
                continue
            
            region = result.crop((x1, y1, x2, y2))
            blurred = region.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            result.paste(blurred, (x1, y1))
        
        return result
    
    @staticmethod
    def apply_sticker(
        image: Image.Image,
        regions: List[Tuple[int, int, int, int]],
        sticker_path: str = None,
        sticker_emoji: str = "⭐"
    ) -> Image.Image:
        """Apply sticker overlay to regions."""
        result = image.copy()
        
        if sticker_path and os.path.exists(sticker_path):
            sticker = Image.open(sticker_path).convert('RGBA')
        else:
            # Create simple emoji-style sticker
            sticker = None
        
        for x1, y1, x2, y2 in regions:
            w, h = x2 - x1, y2 - y1
            
            if sticker:
                # Resize sticker to fit region
                resized = sticker.resize((w, h), Image.Resampling.LANCZOS)
                result.paste(resized, (x1, y1), resized)
            else:
                # Draw simple star/circle overlay
                draw = ImageDraw.Draw(result)
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                radius = min(w, h) // 2
                draw.ellipse(
                    [center_x - radius, center_y - radius, 
                     center_x + radius, center_y + radius],
                    fill=(255, 215, 0)  # Gold color
                )
        
        return result
    
    @staticmethod
    def apply_censoring(
        image: Image.Image,
        regions: List[Tuple[int, int, int, int]],
        style: str = "mosaic",
        **kwargs
    ) -> Image.Image:
        """Apply censoring with specified style."""
        if style == "mosaic":
            block_size = kwargs.get("block_size", 16)
            return Censor.apply_mosaic(image, regions, block_size)
        elif style == "black_bar":
            return Censor.apply_bar(image, regions, (0, 0, 0))
        elif style == "white_bar":
            return Censor.apply_bar(image, regions, (255, 255, 255))
        elif style == "blur":
            blur_radius = kwargs.get("blur_radius", 20)
            return Censor.apply_blur(image, regions, blur_radius)
        elif style == "sticker":
            sticker_path = kwargs.get("sticker_path")
            return Censor.apply_sticker(image, regions, sticker_path)
        else:
            raise ValueError(f"Unknown censor style: {style}")


# Global detector instance (lazy loaded)
_detector: Optional[CensorDetector] = None


def get_detector(model_path: str = None) -> CensorDetector:
    """Get or create the global detector instance."""
    global _detector
    
    if _detector is None or (model_path and _detector.model_path != model_path):
        _detector = CensorDetector(model_path)
        if model_path:
            _detector.load()
    
    return _detector
