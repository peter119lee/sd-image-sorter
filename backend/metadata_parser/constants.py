# =============================================================================
# metadata_parser.constants - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 28-44, 48-206.
# Module constants + the MetadataParser class vocabulary (ParserVocabularyMixin).
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
from typing import Optional, Dict, Tuple, Set

PARSED_METADATA_VERSION = 8
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_CHUNK_BYTES = 64 * 1024 * 1024       # 64 MB – generous cap for any single PNG chunk
_MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024    # 64 MB – cap for zlib-decompressed text data
_MAX_PNG_STEALTH_PROBE_BYTES = 64 * 1024 * 1024
JPEG_SIGNATURE = b"\xff\xd8"
_MAX_JPEG_SEGMENT_BYTES = 64 * 1024 * 1024
_MAX_XMP_CHUNK_BYTES = 8 * 1024 * 1024
_MAX_SIDECAR_BYTES = 256 * 1024
_MAX_SIDECAR_DIRECTORY_CACHE_ENTRIES = 4096
_MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES = 50_000
SIDECAR_EXTENSIONS = (".txt", ".json", ".xmp")
# Metadata key a sidecar loader uses for plain caption text (a Danbooru-style
# tag list or a written description). Deliberately NOT "prompt": no generator
# claimed this text as the prompt it rendered from, so it must not reach the
# SD-prompt field. Only sidecar loaders ever emit this key.
SIDECAR_CAPTION_METADATA_KEY = "sidecar_caption"
# Result key carrying the DETECTED FORMAT of that caption text (tags / natural /
# mixed / unknown; see caption_format). A different axis from the provenance the
# key above encodes: provenance cannot be derived from the text, format can.
# Never a metadata key — nothing in a file claims this; the parser derives it.
SIDECAR_CAPTION_FORMAT_RESULT_KEY = "sidecar_caption_format"
_sidecar_directory_cache: Dict[str, Tuple[Tuple[int, int], Optional[Set[str]]]] = {}
WEBP_SIGNATURE = b"RIFF"
WEBP_FOURCC = b"WEBP"
_MAX_WEBP_CHUNK_BYTES = 64 * 1024 * 1024



class ParserVocabularyMixin:
    """Class-level vocabulary of MetadataParser (generator ids, ComfyUI node
    type sets, bridge keys, raw-retention caps). Constants only, no methods;
    resolved through MRO by every other mixin via self.<CONSTANT>."""

    GENERATORS = {
        "comfyui": "ComfyUI",
        "nai": "NovelAI",
        "webui": "WebUI",
        "forge": "Forge",
        "reforge": "reForge",
        "fooocus": "Fooocus",
        "easy-diffusion": "Easy Diffusion",
        "invokeai": "InvokeAI",
        "swarmui": "SwarmUI",
        "drawthings": "Draw Things",
        "gemini": "Gemini",
        "gpt-image": "gpt-image",
        "unknown": "Unknown",
        "others": "Others",
    }

    # Generator IDs that are bundled under the gallery "Others" tab.
    # Top tab bar stays small (comfyui/nai/webui/forge/unknown) per product
    # decision; rare generators show up here with their actual generator
    # name preserved so users can still filter on them in the modal.
    OTHERS_BUNDLE = (
        "others",
        "fooocus",
        "reforge",
        "easy-diffusion",
        "invokeai",
        "swarmui",
        "drawthings",
        "gemini",
        "gpt-image",
    )

    # Node class_types that contain text prompts in ComfyUI
    COMFYUI_TEXT_NODE_TYPES = {
        # Standard CLIP text encoders
        "CLIPTextEncode",
        "CLIPTextEncodeSDXL",
        "CLIPTextEncodeSD3",
        "CLIPTextEncodeFlux",
        "CLIPTextEncodeHunyuanDiT",
        # Custom/community text encoders
        "NewBieCLIPTextEncode",
        "NewBieCLIPTextEncodeBasic",
        "BNK_CLIPTextEncodeAdvanced",
        "CLIPTextEncodeA1111",
        # Conditioning nodes
        "ConditioningCombine",
        "ConditioningConcat",
        "ConditioningSetArea",
    }

    # Node types that hold string constants (prompt fragments)
    COMFYUI_STRING_NODE_TYPES = {
        "StringConstantMultiline",
        "StringConstant",
        "String",
        "Text",
        "TextMultiline",
        "TextBox",
        "ShowText",
        "Note",
        "PrimitiveNode",
    }

    # Node types that load checkpoints
    COMFYUI_CHECKPOINT_NODE_TYPES = {
        "CheckpointLoaderSimple",
        "CheckPointLoaderSimple",
        "CheckpointLoader",
        "CheckpointLoaderNF4",
        "UNETLoader",
        "DiffusionModelLoader",
        "DiffusionModelLoaderKJ",
    }

    # Node types that load LoRAs
    COMFYUI_LORA_NODE_TYPES = {
        "LoraLoader",
        "LoraLoaderModelOnly",
        "LoRALoader",
        "LoraLoaderBlockWeight",
    }

    # Multi-LoRA loader node types (rgthree-style: lora_1, lora_2, ...)
    COMFYUI_MULTI_LORA_NODE_TYPES = {
        "Power Lora Loader (rgthree)",
        "CR LoRA Stack",
        "Efficient Loader",
    }

    # Node types that are KSamplers (have positive/negative inputs)
    COMFYUI_SAMPLER_NODE_TYPES = {
        "KSampler",
        "KSamplerAdvanced",
        "KSamplerSelect",
        "SamplerCustom",
        "SamplerCustomAdvanced",
    }

    # Image-typed link inputs used to bridge across runtime VLM/inference
    # nodes (e.g. QwenTE_ImageInfer) whose own text output is generated at
    # RUNTIME and therefore not recoverable from the serialized graph.
    # Instruction/system inputs (e.g. "提示词", "系统提示词", "system") are
    # deliberately NOT in this list and are never followed on that path.
    COMFYUI_IMAGE_BRIDGE_KEYS = ("图片", "image", "images", "img")

    # Non-semantic link channels the conditioning bridge must never follow:
    # model/clip/vae/latent/mask plumbing cannot carry prompt text, and
    # walking them would traverse the whole graph for nothing. Image keys
    # stay with the VLM image bridge above; instruction/system inputs stay
    # excluded per the image-bridge rationale.
    COMFYUI_COND_BRIDGE_EXCLUDE_KEYS = (
        "model", "clip", "vae", "latent", "latent_image", "samples",
        "mask", "pixels", "sigmas", "sampler", "noise", "guider",
        "clip_vision", "control_net", "controlnet", "提示词", "系统提示词",
        "system", "system_prompt",
    )

    COMFYUI_MODEL_FILE_EXTENSIONS = (
        ".safetensors",
        ".ckpt",
        ".pt",
        ".pth",
        ".bin",
        ".onnx",
    )

    # Metadata L3 (raw retention): when parsing fails to produce a positive
    # prompt, the original text chunks are preserved (gzipped) in the DB so a
    # future parser can re-parse the image without the file. Caps keep
    # pathological workflows (embedded base64 images etc.) out of the DB.
    RAW_METADATA_CHUNK_CAP = 2 * 1024 * 1024
    RAW_METADATA_TOTAL_CAP = 4 * 1024 * 1024

    COMFYUI_MODEL_KEY_TYPES = {
        "ckpt_name": "checkpoint",
        "checkpoint_name": "checkpoint",
        "checkpoint": "checkpoint",
        "unet_name": "unet",
        "diffusion_model": "diffusion_model",
        "diffusion_model_name": "diffusion_model",
        "model_name": "model",
        "base_model": "model",
        "lora_name": "lora",
        "vae_name": "vae",
        "clip_name": "clip",
        "clip_name1": "clip",
        "clip_name2": "clip",
        "yolo_model": "yolo",
        "yolo_model_name": "yolo",
        "detector_model": "yolo",
        "detector_model_name": "yolo",
        "bbox_model_name": "yolo",
        "segm_model_name": "yolo",
        "ultralytics_model": "yolo",
        "ultralytics_model_name": "yolo",
    }

