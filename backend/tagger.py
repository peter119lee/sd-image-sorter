"""
WD14 Tagger using ONNX Runtime for image tagging.
Supports automatic model download from HuggingFace and local model loading.
"""
import os
import json
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image
from pathlib import Path

# Will be imported lazily
ort = None
hf_hub = None


def _ensure_imports():
    """Lazily import heavy dependencies."""
    global ort, hf_hub
    if ort is None:
        import onnxruntime as ort_module
        ort = ort_module
    if hf_hub is None:
        import huggingface_hub as hf_module
        hf_hub = hf_module


# Model configurations - eva02-large is the best quality model
MODELS = {
    "wd-eva02-large-tagger-v3": {
        "repo_id": "SmilingWolf/wd-eva02-large-tagger-v3",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv"
    },
    "wd-swinv2-tagger-v3": {
        "repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv"
    },
    "wd-convnext-tagger-v3": {
        "repo_id": "SmilingWolf/wd-convnext-tagger-v3",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv"
    },
    "wd-vit-tagger-v3": {
        "repo_id": "SmilingWolf/wd-vit-tagger-v3",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv"
    },
    "wd-vit-large-tagger-v3": {
        "repo_id": "SmilingWolf/wd-vit-large-tagger-v3",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv"
    }
}

# Default to eva02-large for best quality
DEFAULT_MODEL = "wd-eva02-large-tagger-v3"

# Rating categories
RATINGS = ["general", "sensitive", "questionable", "explicit"]


class WD14Tagger:
    """WD14 Tagger for anime-style image tagging using ONNX."""
    
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        model_path: Optional[str] = None,
        tags_path: Optional[str] = None,
        model_dir: Optional[str] = None,
        threshold: float = 0.35,
        character_threshold: float = 0.85,
        use_gpu: bool = True
    ):
        """
        Initialize the tagger.
        
        Args:
            model_name: One of the supported model names (for auto-download)
            model_path: Direct path to .onnx file (overrides model_name)
            tags_path: Direct path to selected_tags.csv (required if model_path is set)
            model_dir: Directory to store/load models. If None, uses cache dir.
            threshold: Confidence threshold for general tags
            character_threshold: Confidence threshold for character tags
            use_gpu: Whether to use GPU acceleration (CUDA) if available
        """
        _ensure_imports()
        
        self.model_name = model_name
        self.model_path = model_path
        self.tags_path = tags_path
        self.model_dir = model_dir or self._get_default_model_dir()
        self.threshold = threshold
        self.character_threshold = character_threshold
        self.use_gpu = use_gpu
        
        self.session = None
        self.tags = []
        self.general_tags = []
        self.character_tags = []
        self.rating_tags = []
        self.rating_indices = {}  # Map rating name to index
        
        self._loaded = False
    
    def _get_default_model_dir(self) -> str:
        """Get default model directory - prefers project folder over user cache."""
        # Priority 1: Project folder (same drive as code)
        project_model_dir = os.path.join(os.path.dirname(__file__), "..", "models", "wd14-tagger")
        project_model_dir = os.path.abspath(project_model_dir)
        
        # Check if it exists OR create it (prefer local storage)
        if os.path.exists(project_model_dir):
            return project_model_dir
        
        # Create project folder for local storage
        try:
            os.makedirs(project_model_dir, exist_ok=True)
            print(f"Created model directory: {project_model_dir}")
            return project_model_dir
        except Exception as e:
            print(f"Could not create project model dir: {e}")
        
        # Fallback: User cache (C: drive)
        cache_dir = os.path.expanduser("~/.cache/wd14-tagger")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir
    
    def _validate_model_file(self, model_path: str) -> bool:
        """
        Validate that an ONNX model file is not corrupted.
        Returns True if valid, False if corrupted or invalid.
        """
        if not os.path.exists(model_path):
            return False
        
        # Check file size - ONNX models should be at least 1MB
        try:
            file_size = os.path.getsize(model_path)
            if file_size < 1024 * 1024:  # Less than 1MB is suspicious
                print(f"Warning: Model file {model_path} is suspiciously small ({file_size} bytes)")
                return False
        except:
            return False
        
        # Try to read the file header to verify it's a valid ONNX file
        try:
            with open(model_path, 'rb') as f:
                header = f.read(4)
                # ONNX files start with specific protobuf bytes
                if len(header) < 4:
                    return False
        except Exception as e:
            print(f"Error reading model file header: {e}")
            return False
        
        return True
    
    def _get_model_paths(self) -> Tuple[str, str]:
        """Get model and tags file paths."""
        # If direct paths are provided, use them
        if self.model_path and os.path.exists(self.model_path):
            if self.tags_path and os.path.exists(self.tags_path):
                return self.model_path, self.tags_path
            # Try to find tags file next to model
            model_dir = os.path.dirname(self.model_path)
            possible_tags = [
                os.path.join(model_dir, "selected_tags.csv"),
                os.path.join(model_dir, "..", "selected_tags.csv"),
            ]
            for tags_path in possible_tags:
                if os.path.exists(tags_path):
                    return self.model_path, tags_path
            raise ValueError(f"Tags file not found. Please provide tags_path for custom model.")
        
        # Otherwise, download from HuggingFace
        return self._download_model()
    
    def _download_model(self) -> Tuple[str, str]:
        """Download model from HuggingFace if not present."""
        if self.model_name not in MODELS:
            raise ValueError(f"Unknown model: {self.model_name}. Available: {list(MODELS.keys())}")
        
        config = MODELS[self.model_name]
        repo_id = config["repo_id"]
        
        model_path = os.path.join(self.model_dir, self.model_name, config["model_file"])
        tags_path = os.path.join(self.model_dir, self.model_name, config["tags_file"])
        
        # Check if model exists and is valid
        needs_download = False
        if not os.path.exists(model_path):
            needs_download = True
        elif not self._validate_model_file(model_path):
            print(f"Model file {model_path} appears corrupted. Re-downloading...")
            needs_download = True
            # Delete corrupted file
            try:
                os.remove(model_path)
            except Exception as e:
                print(f"Warning: Could not delete corrupted model file: {e}")
        
        # Download if needed
        if needs_download:
            print(f"Downloading model {self.model_name}...")
            os.makedirs(os.path.dirname(model_path), exist_ok=True)
            
            try:
                model_path = hf_hub.hf_hub_download(
                    repo_id=repo_id,
                    filename=config["model_file"],
                    local_dir=os.path.join(self.model_dir, self.model_name)
                )
                
                # Validate after download
                if not self._validate_model_file(model_path):
                    raise ValueError(f"Downloaded model file is invalid. Please check your internet connection and try again.")
            except Exception as e:
                print(f"Error downloading model: {e}")
                raise
        
        if not os.path.exists(tags_path):
            print(f"Downloading tags file...")
            tags_path = hf_hub.hf_hub_download(
                repo_id=repo_id,
                filename=config["tags_file"],
                local_dir=os.path.join(self.model_dir, self.model_name)
            )
        
        return model_path, tags_path
    
    def _load_tags(self, tags_path: str):
        """Load tag labels from CSV.
        
        IMPORTANT: The model output index is the ROW NUMBER in the CSV (0-indexed after header),
        NOT the tag_id column value. The tag_id column is just metadata.
        """
        self.tags = []
        self.general_tags = []
        self.character_tags = []
        self.rating_tags = []
        self.rating_indices = {}
        
        with open(tags_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Skip header, use enumeration index as the model output position
        for row_idx, line in enumerate(lines[1:]):
            parts = line.strip().split(",")
            if len(parts) >= 3:
                # row_idx is the actual index into model output (0-indexed)
                tag_name = parts[1]
                category = int(parts[2])
                
                self.tags.append(tag_name)
                
                if category == 0:
                    self.general_tags.append((row_idx, tag_name))
                elif category == 4:
                    self.character_tags.append((row_idx, tag_name))
                elif category == 9:
                    self.rating_tags.append((row_idx, tag_name))
                    # Map rating name to index
                    self.rating_indices[tag_name] = row_idx
    
    def load(self):
        """Load the model and tags."""
        if self._loaded:
            return
        
        model_path, tags_path = self._get_model_paths()
        
        # Load ONNX model with error handling
        print(f"Loading model from {model_path}...")
        
        # Choose providers based on use_gpu setting
        if self.use_gpu:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        else:
            providers = ['CPUExecutionProvider']
        
        available_providers = ort.get_available_providers()
        providers = [p for p in providers if p in available_providers]
        print(f"Using providers: {providers} (GPU {'enabled' if self.use_gpu else 'disabled'})")
        
        # Create session options to prevent CPU overload / BSOD
        # This fixes CLOCK_WATCHDOG_TIMEOUT crashes
        sess_options = ort.SessionOptions()
        
        # Limit threads to prevent CPU overload (use half of available cores, min 1)
        import multiprocessing
        num_threads = max(1, multiprocessing.cpu_count() // 2)
        sess_options.intra_op_num_threads = num_threads
        sess_options.inter_op_num_threads = 1  # Sequential graph execution
        
        # Disable thread spinning to reduce CPU usage (prevents BSOD)
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        
        # Use sequential execution mode (safer for CPU)
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        
        # Optimize for inference
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        print(f"ONNX session using {num_threads} threads (spinning disabled)")
        
        try:
            self.session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
        except Exception as e:
            error_msg = str(e)
            if "INVALID_PROTOBUF" in error_msg or "Protobuf parsing failed" in error_msg:
                # Model file is corrupted
                print(f"ERROR: Model file is corrupted: {model_path}")
                print(f"Attempting to delete and re-download...")
                
                # Try to delete corrupted file
                try:
                    os.remove(model_path)
                    print(f"Deleted corrupted model file.")
                except Exception as del_error:
                    print(f"Could not delete corrupted file: {del_error}")
                
                # Try to re-download
                print("Re-downloading model...")
                model_path, tags_path = self._download_model()
                
                # Try loading again
                try:
                    self.session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
                    print("Successfully loaded model after re-download!")
                except Exception as e2:
                    raise RuntimeError(f"Failed to load model even after re-download. Error: {e2}")
            else:
                # Some other ONNX error
                raise RuntimeError(f"Failed to load ONNX model: {error_msg}")
        
        # Load tags
        self._load_tags(tags_path)
        
        self._loaded = True
        print(f"Model loaded. Using providers: {self.session.get_providers()}")
    
    def _preprocess(self, image: Image.Image) -> np.ndarray:
        """Preprocess image for inference."""
        # Get input size from model
        input_shape = self.session.get_inputs()[0].shape
        size = input_shape[2] if len(input_shape) == 4 else 448
        
        # Resize and pad to square
        image = image.convert("RGB")
        
        # Resize keeping aspect ratio
        old_size = image.size
        ratio = float(size) / max(old_size)
        new_size = tuple([int(x * ratio) for x in old_size])
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        # Pad to square
        new_image = Image.new("RGB", (size, size), (255, 255, 255))
        paste_pos = ((size - new_size[0]) // 2, (size - new_size[1]) // 2)
        new_image.paste(image, paste_pos)
        
        # Convert to numpy
        img_array = np.array(new_image, dtype=np.float32)
        
        # BGR to RGB (if needed) and normalize
        img_array = img_array[:, :, ::-1]  # RGB to BGR for model
        
        # Add batch dimension
        img_array = np.expand_dims(img_array, axis=0)
        
        return img_array
    
    def tag(self, image_path: str) -> Dict[str, Any]:
        """
        Tag a single image.
        
        Returns:
            {
                "general_tags": [{"tag": str, "confidence": float}, ...],
                "character_tags": [{"tag": str, "confidence": float}, ...],
                "rating": str,
                "rating_confidences": {"general": float, "sensitive": float, ...},
                "all_tags": [{"tag": str, "confidence": float}, ...]
            }
        """
        if not self._loaded:
            self.load()
        
        # Load and preprocess image
        image = Image.open(image_path)
        input_data = self._preprocess(image)
        image.close()  # Free memory immediately
        
        # Run inference
        input_name = self.session.get_inputs()[0].name
        output = self.session.run(None, {input_name: input_data})[0]
        
        # Process output
        probs = output[0]
        
        result = {
            "general_tags": [],
            "character_tags": [],
            "rating": "unknown",
            "rating_confidences": {},
            "all_tags": []
        }
        
        # Extract general tags
        for tag_id, tag_name in self.general_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                if conf >= self.threshold:
                    result["general_tags"].append({"tag": tag_name, "confidence": conf})
                    result["all_tags"].append({"tag": tag_name, "confidence": conf})
        
        # Extract character tags
        for tag_id, tag_name in self.character_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                if conf >= self.character_threshold:
                    result["character_tags"].append({"tag": tag_name, "confidence": conf})
                    result["all_tags"].append({"tag": tag_name, "confidence": conf})
        
        # Get ratings with all confidences
        rating_probs = []
        for tag_id, tag_name in self.rating_tags:
            if tag_id < len(probs):
                conf = float(probs[tag_id])
                rating_probs.append((tag_name, conf))
                result["rating_confidences"][tag_name] = conf
        
        if rating_probs:
            # Only add the HIGHEST confidence rating tag to all_tags
            best_rating = max(rating_probs, key=lambda x: x[1])
            result["rating"] = best_rating[0]
            result["all_tags"].append({"tag": best_rating[0], "confidence": best_rating[1]})
        
        # Sort by confidence
        result["general_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["character_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        result["all_tags"].sort(key=lambda x: x["confidence"], reverse=True)
        
        return result
    
    def tag_batch(self, image_paths: List[str]) -> List[Dict[str, Any]]:
        """Tag multiple images with memory management."""
        import gc
        results = []
        for i, path in enumerate(image_paths):
            try:
                results.append(self.tag(path))
            except Exception as e:
                print(f"Error tagging {path}: {e}")
                results.append({
                    "general_tags": [],
                    "character_tags": [],
                    "rating": "unknown",
                    "rating_confidences": {},
                    "all_tags": [],
                    "error": str(e)
                })
            # Garbage collect every 10 images to prevent memory buildup
            if (i + 1) % 10 == 0:
                gc.collect()
        return results


# Singleton instance
_tagger = None
_current_settings = {}

def get_tagger(
    model_name: str = DEFAULT_MODEL,
    model_path: Optional[str] = None,
    tags_path: Optional[str] = None,
    threshold: float = 0.35,
    character_threshold: float = 0.85,
    use_gpu: bool = True,
    force_reload: bool = False
) -> WD14Tagger:
    """Get or create the tagger instance."""
    global _tagger, _current_settings
    
    new_settings = {
        "model_name": model_name,
        "model_path": model_path,
        "tags_path": tags_path,
        "use_gpu": use_gpu
    }
    
    # Reload if settings changed or forced
    if force_reload or _tagger is None or new_settings != _current_settings:
        _tagger = WD14Tagger(
            model_name=model_name,
            model_path=model_path,
            tags_path=tags_path,
            threshold=threshold,
            character_threshold=character_threshold,
            use_gpu=use_gpu
        )
        _current_settings = new_settings
    else:
        # Just update thresholds
        _tagger.threshold = threshold
        _tagger.character_threshold = character_threshold
    
    return _tagger


def get_available_models() -> List[str]:
    """Get list of available model names."""
    return list(MODELS.keys())


def tag_image(image_path: str, threshold: float = 0.35) -> Dict[str, Any]:
    """Convenience function to tag a single image."""
    return get_tagger(threshold=threshold).tag(image_path)
