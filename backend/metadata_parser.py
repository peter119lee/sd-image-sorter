"""
Metadata parser for Stable Diffusion generated images.
Detects generator type and extracts prompt information.
"""
import json
import re
from typing import Optional, Dict, Any, Tuple, List
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import os


class MetadataParser:
    """Parse metadata from SD-generated images to detect source and extract prompts."""
    
    GENERATORS = {
        "comfyui": "ComfyUI",
        "nai": "NovelAI", 
        "webui": "WebUI",
        "forge": "Forge",
        "unknown": "Unknown"
    }
    
    def parse(self, image_path: str) -> Dict[str, Any]:
        """
        Parse image metadata and return structured data.
        
        Returns:
            {
                "generator": str,  # comfyui, nai, webui, forge, unknown
                "prompt": str or None,
                "negative_prompt": str or None,
                "checkpoint": str or None,
                "loras": list of str,
                "metadata": dict,  # Full raw metadata
                "width": int,
                "height": int,
                "file_size": int
            }
        """
        result = {
            "generator": "unknown",
            "prompt": None,
            "negative_prompt": None,
            "checkpoint": None,
            "loras": [],
            "metadata": {},
            "width": 0,
            "height": 0,
            "file_size": 0
        }
        
        try:
            result["file_size"] = os.path.getsize(image_path)
            
            with Image.open(image_path) as img:
                result["width"] = img.width
                result["height"] = img.height
                
                # Get all metadata
                metadata = {}
                if hasattr(img, 'info'):
                    metadata = dict(img.info)
                
                # Check for WebP EXIF/XMP
                if img.format == 'WEBP':
                    exif_data = self._extract_exif(img)
                    metadata.update(exif_data)
                    
                    # Extract XMP for WebP (common for ComfyUI/Stable Diffusion)
                    xmp_data = self._extract_webp_xmp(image_path)
                    metadata.update(xmp_data)
                
                result["metadata"] = self._serialize_metadata(metadata)
                
                # Detect generator and extract prompts, checkpoint, loras
                generator, prompt, neg_prompt, checkpoint, loras = self._detect_and_parse(metadata)
                result["generator"] = generator
                result["prompt"] = prompt
                result["negative_prompt"] = neg_prompt
                result["checkpoint"] = checkpoint
                result["loras"] = loras
                
        except Exception as e:
            print(f"Error parsing {image_path}: {e}")
        
        return result
    
    def _serialize_metadata(self, metadata: dict) -> dict:
        """Serialize metadata to JSON-safe format."""
        result = {}
        for key, value in metadata.items():
            try:
                # Try to serialize, skip if not possible
                json.dumps({key: value})
                result[key] = value
            except (TypeError, ValueError):
                # Convert bytes to string
                if isinstance(value, bytes):
                    try:
                        result[key] = value.decode('utf-8', errors='replace')
                    except:
                        result[key] = str(value)
                else:
                    result[key] = str(value)
        return result
    
    def _detect_and_parse(self, metadata: dict) -> Tuple[str, Optional[str], Optional[str], Optional[str], List[str]]:
        """
        Detect generator type and extract prompts, checkpoint, and loras.
        Returns: (generator, prompt, negative_prompt, checkpoint, loras)
        """
        checkpoint = None
        loras = []

        # Check for ComfyUI - has "prompt" key with JSON workflow
        if "prompt" in metadata:
            try:
                prompt_data = metadata["prompt"]
                if isinstance(prompt_data, str):
                    prompt_data = json.loads(prompt_data)
                if isinstance(prompt_data, dict):
                    # ComfyUI stores workflow as dict
                    # Look for positive/negative prompts, checkpoint, and loras in nodes
                    pos, neg, cp, lr = self._extract_comfyui_data(prompt_data)
                    if pos or "workflow" in metadata:
                        return ("comfyui", pos, neg, cp, lr)
            except json.JSONDecodeError:
                pass
        
        # Check for ComfyUI workflow key
        if "workflow" in metadata:
            try:
                workflow = metadata["workflow"]
                if isinstance(workflow, str):
                    workflow = json.loads(workflow)
                pos, neg, cp, lr = self._extract_comfyui_data(metadata.get("prompt", {}))
                return ("comfyui", pos, neg, cp, lr)
            except:
                return ("comfyui", None, None, None, [])
        
        # Check for NovelAI - has "Comment" with specific format
        if "Comment" in metadata:
            try:
                comment = metadata["Comment"]
                if isinstance(comment, str):
                    comment_data = json.loads(comment)
                    if "prompt" in comment_data or "uc" in comment_data:
                        prompt = comment_data.get("prompt", "")
                        neg = comment_data.get("uc", "")
                        # NAI doesn't easily expose checkpoint/lora in Comment the same way
                        return ("nai", prompt, neg, None, [])
            except json.JSONDecodeError:
                pass
        
        # Check for NovelAI Description field
        if "Description" in metadata:
            desc = metadata["Description"]
            if "NovelAI" in str(desc) or "nai" in str(desc).lower():
                return ("nai", desc, None, None, [])
        
        # Check for WebUI/Forge - has "parameters" text chunk
        if "parameters" in metadata:
            params = metadata["parameters"]
            prompt, neg, cp, lr = self._parse_webui_parameters(params)
            
            # Detect Forge vs base WebUI
            generator = "webui"
            if "forge" in params.lower() or "Forge" in params:
                generator = "forge"
            
            return (generator, prompt, neg, cp, lr)
        
        # Check for A1111 format in other fields
        for key in ["Parameters", "Comment", "UserComment", "parameters"]:
            if key in metadata:
                params = str(metadata[key])
                # Remove common EXIF prefix for UserComment if present
                if params.startswith("UNICODE") or params.startswith("ASCII"):
                    params = params[7:].strip("\0 ")
                
                if "Steps:" in params and "Sampler:" in params:
                    prompt, neg, cp, lr = self._parse_webui_parameters(params)
                    generator = "forge" if "forge" in params.lower() else "webui"
                    return (generator, prompt, neg, cp, lr)
        
        # Check Software tag
        if "Software" in metadata:
            software = str(metadata["Software"]).lower()
            if "novelai" in software:
                return ("nai", None, None, None, [])
            if "comfyui" in software:
                return ("comfyui", None, None, None, [])
        
        return ("unknown", None, None, None, [])
    
    def _extract_comfyui_data(self, prompt_data: Any) -> Tuple[Optional[str], Optional[str], Optional[str], List[str]]:
        """Extract positive/negative prompts, checkpoint, and loras from ComfyUI workflow."""
        if not isinstance(prompt_data, dict):
            try:
                prompt_data = json.loads(prompt_data) if isinstance(prompt_data, str) else {}
            except:
                return (None, None, None, [])
        
        positive = []
        negative = []
        checkpoint = None
        loras = []
        
        # Look through nodes
        for node_id, node in prompt_data.items():
            if not isinstance(node, dict):
                continue
            
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})
            
            # Common prompt node types
            if "CLIPTextEncode" in class_type:
                text = inputs.get("text", "")
                if text and isinstance(text, str):
                    if not positive:
                        positive.append(text)
                    else:
                        negative.append(text)
            
            # Checkpoint loaders
            if "CheckpointLoader" in class_type or class_type == "CheckPointLoaderSimple":
                cp = inputs.get("ckpt_name", "")
                if cp:
                    checkpoint = cp
            
            # Lora loaders
            if "LoraLoader" in class_type:
                lr = inputs.get("lora_name", "")
                if lr:
                    loras.append(lr)
            
            # KSampler nodes might have prompt connections
            if "positive" in inputs and isinstance(inputs["positive"], str):
                positive.append(inputs["positive"])
            if "negative" in inputs and isinstance(inputs["negative"], str):
                negative.append(inputs["negative"])
        
        pos_text = " ".join(positive) if positive else None
        neg_text = " ".join(negative) if negative else None
        
        return (pos_text, neg_text, checkpoint, loras)
    
    def _parse_webui_parameters(self, params: str) -> Tuple[Optional[str], Optional[str], Optional[str], List[str]]:
        """Parse WebUI/Forge parameters format including checkpoint and loras."""
        if not params:
            return (None, None, None, [])
        
        prompt = None
        negative = None
        checkpoint = None
        loras = []
        
        # Extract Lora from prompt: <lora:name:weight>
        lora_matches = re.findall(r"<lora:([^:]+):[^>]+>", params)
        if lora_matches:
            loras = list(set(lora_matches))
        
        # Extract Checkpoint from parameters (usually "Model: [name]")
        model_match = re.search(r"Model:\s*([^,]+)", params)
        if model_match:
            checkpoint = model_match.group(1).strip()
        
        # WebUI format: prompt\nNegative prompt: neg\nSteps: X, ...
        lines = params.split("\n")
        
        # Find where negative prompt starts
        neg_start = -1
        for i, line in enumerate(lines):
            if line.startswith("Negative prompt:"):
                neg_start = i
                break
        
        # Find where parameters start
        param_start = -1
        for i, line in enumerate(lines):
            if re.match(r"^Steps:\s*\d+", line):
                param_start = i
                break
        
        # Extract positive prompt
        if neg_start > 0:
            prompt = "\n".join(lines[:neg_start]).strip()
        elif param_start > 0:
            prompt = "\n".join(lines[:param_start]).strip()
        else:
            prompt = params  # Just use everything
        
        # Extract negative prompt
        if neg_start >= 0:
            neg_end = param_start if param_start > neg_start else len(lines)
            neg_lines = lines[neg_start:neg_end]
            if neg_lines:
                neg_lines[0] = neg_lines[0].replace("Negative prompt:", "").strip()
                negative = "\n".join(neg_lines).strip()
        
        return (prompt, negative, checkpoint, loras)

    def _extract_exif(self, img: Image.Image) -> dict:
        """Extract EXIF data from image."""
        metadata = {}
        try:
            exif = img.getexif()
            if exif:
                from PIL import ExifTags
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, tag_id)
                    if isinstance(value, bytes):
                        try:
                            metadata[tag_name] = value.decode('utf-8', errors='replace')
                        except:
                            metadata[tag_name] = str(value)
                    else:
                        metadata[tag_name] = value
        except Exception as e:
            print(f"Error extracting exif: {e}")
        return metadata

    def _extract_webp_xmp(self, image_path: str) -> dict:
        """
        Extract XMP metadata from a WebP file manually by parsing chunks.
        WebP is a RIFF container, so we look for the 'XMP ' chunk.
        """
        metadata = {}
        try:
            with open(image_path, 'rb') as f:
                data = f.read()
                
                # Search for XMP chunk
                # In RIFF containers, chunks are 4-byte ID + 4-byte size
                xmp_pos = data.find(b'XMP ')
                if xmp_pos != -1:
                    # Size is 4 bytes after ID
                    size = int.from_bytes(data[xmp_pos+4:xmp_pos+8], 'little')
                    xmp_content = data[xmp_pos+8:xmp_pos+8+size]
                    
                    # Try to decode
                    try:
                        # For SD metadata, we often find it as a string inside the XMP
                        # ComfyUI/WebUI sometimes wrap it in XML, but we can try to find the prompt keys
                        decoded_xmp = xmp_content.decode('utf-8', errors='replace')
                        metadata["xmp"] = decoded_xmp
                        
                        # Peek inside for common keys to help _detect_and_parse
                        # Some versions of SD tools store the raw parameters string in XMP
                        if "parameters" not in metadata and "parameters" in decoded_xmp:
                            # Try to extract parameters if it looks like WebUI format
                            match = re.search(r'parameters>(.*?)</', decoded_xmp, re.DOTALL)
                            if match:
                                metadata["parameters"] = match.group(1).strip()
                            else:
                                # Fallback: just use the whole thing if it contains SD-like info
                                if "Steps:" in decoded_xmp:
                                    metadata["parameters"] = decoded_xmp
                        
                        # ComfyUI specific: often just raw JSON in XMP or a specific property
                        if "prompt" not in metadata and "prompt" in decoded_xmp:
                            # See if it's a JSON block (use simpler pattern, (?R) not supported)
                            # Look for opening brace and try to find matching close
                            json_start = decoded_xmp.find('{')
                            if json_start != -1:
                                # Simple approach: find the last closing brace
                                json_end = decoded_xmp.rfind('}')
                                if json_end > json_start:
                                    potential_json = decoded_xmp[json_start:json_end+1]
                                    try:
                                        json.loads(potential_json)
                                        metadata["prompt"] = potential_json
                                    except json.JSONDecodeError:
                                        pass

                    except Exception as inner_e:
                        # XMP decoding errors are non-critical, suppress verbose output
                        pass
                        
        except Exception as e:
            print(f"Error extracting webp xmp: {e}")
            
        return metadata


# Singleton instance
_parser = None

def get_parser() -> MetadataParser:
    """Get the singleton parser instance."""
    global _parser
    if _parser is None:
        _parser = MetadataParser()
    return _parser


def parse_image(image_path: str) -> Dict[str, Any]:
    """Convenience function to parse a single image."""
    return get_parser().parse(image_path)
