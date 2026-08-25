# =============================================================================
# metadata_parser.novelai - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 2397-2596.
# Mixin: NovelAI Comment / UserComment parsing.
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import logging
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger(__name__)

class NovelAIMixin:
    """NovelAI Comment / UserComment parsing."""

    def _parse_nai_usercomment(self, usercomment: Any, metadata: dict) -> Optional[Tuple[str, Optional[str], Optional[str], Optional[str], List[str]]]:
        """Legacy wrapper — delegates to extended version for backward compat."""
        result = self._parse_nai_usercomment_extended(usercomment, metadata)
        if result:
            return ("nai", result.get("prompt"), result.get("negative_prompt"), None, [])
        return None

    def _parse_nai_usercomment_extended(self, usercomment: Any, metadata: dict) -> Optional[Dict[str, Any]]:
        """
        Parse NovelAI V4+ EXIF UserComment and return extended dict.
        Extracts prompt, negative, generation params, character prompts, img2img info.
        """
        try:
            text = None
            if isinstance(usercomment, bytes):
                if usercomment.startswith(b'ASCII\x00\x00\x00'):
                    text = usercomment[8:].decode('utf-8', errors='replace')
                elif usercomment.startswith(b'UNICODE\x00'):
                    text = usercomment[8:].decode('utf-16', errors='replace')
                else:
                    text = usercomment.decode('utf-8', errors='replace')
            elif isinstance(usercomment, str):
                text = self._peel_exif_charset_prefix(usercomment)

            if not text:
                return None

            json_start = text.find('{')
            if json_start < 0:
                return None

            data = json.loads(text[json_start:])
            if not isinstance(data, dict):
                return None

            software = data.get("Software", str(metadata.get("Software", "")))
            is_nai = "novelai" in str(software).lower()
            has_nai_keys = "Description" in data or "Source" in data or "Generation time" in data
            # WebP EXIF UserComment is often the inner Comment JSON
            # (`prompt`/`uc`/`v4_prompt`), not the PNG wrapper object.
            looks_like_comment_body = (
                "uc" in data
                or "v4_prompt" in data
                or "v4_negative_prompt" in data
                or "characterPrompts" in data
            ) and "negative_prompt" not in data

            if not is_nai and not has_nai_keys and not looks_like_comment_body:
                return None

            result: Dict[str, Any] = {
                "generator": "nai",
                "prompt": self._flatten_text_value(data.get("Description", None)),
                "negative_prompt": None,
                "checkpoint": self._extract_metadata_model_identifier(metadata) or self._extract_metadata_model_identifier(data),
                "generation_params": None,
                "character_prompts": None,
                "is_img2img": False,
                "img2img_info": None,
            }

            nested_comment = data.get("Comment")
            if isinstance(nested_comment, str) and nested_comment.strip():
                comment = nested_comment
            elif looks_like_comment_body:
                comment = json.dumps(data)
            else:
                comment = ""
            if isinstance(comment, str) and comment:
                try:
                    comment_data = json.loads(comment)
                    if isinstance(comment_data, dict):
                        if "prompt" in comment_data and not result["prompt"]:
                            result["prompt"] = self._flatten_text_value(comment_data["prompt"])
                        result["negative_prompt"] = self._flatten_text_value(comment_data.get("uc", None))

                        # V4/V4.5/V5 still serialize as v4_prompt (params_version 4).
                        if "v4_prompt" in comment_data:
                            v4_prompt = comment_data["v4_prompt"]
                            if not result["prompt"]:
                                result["prompt"] = self._flatten_nai_structured_prompt(v4_prompt)
                            char_prompts = self._extract_nai_character_prompts(comment_data)
                            if char_prompts:
                                result["character_prompts"] = char_prompts

                        if "v4_negative_prompt" in comment_data:
                            v4_neg = comment_data["v4_negative_prompt"]
                            if not result["negative_prompt"]:
                                result["negative_prompt"] = self._flatten_nai_structured_prompt(v4_neg)

                        # Generation params
                        result["generation_params"] = self._extract_nai_gen_params(comment_data)

                        # img2img detection
                        strength = comment_data.get("strength")
                        noise = comment_data.get("noise")
                        if strength is not None and float(strength) < 1.0:
                            result["is_img2img"] = True
                            result["img2img_info"] = {
                                "denoising_strength": float(strength),
                                "noise": float(noise) if noise is not None else None,
                                "source": "img2img",
                            }
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.debug("Failed to parse NAI UserComment Comment JSON: %s", e)

            if result["prompt"] or result["negative_prompt"]:
                return result

        except Exception as e:
            logger.debug('Failed to parse NAI UserComment: %s', e)

        return None

    def _extract_nai_gen_params(self, comment_data: dict) -> Optional[Dict[str, Any]]:
        """Extract structured generation parameters from NAI Comment JSON."""
        if not isinstance(comment_data, dict):
            return None

        params = {}
        key_map = {
            "steps": "steps",
            "sampler": "sampler",
            "seed": "seed",
            "strength": "strength",
            "noise": "noise",
            "scale": "cfg_scale",
            "cfg_rescale": "cfg_rescale",
            "sm": "sm",
            "sm_dyn": "sm_dyn",
            "dynamic_thresholding": "dynamic_thresholding",
            "noise_schedule": "noise_schedule",
            "legacy_v3_extend": "legacy_v3_extend",
            "uncond_scale": "uncond_scale",
            "skip_cfg_above_sigma": "skip_cfg_above_sigma",
            "ucPreset": "uc_preset",
            "qualityToggle": "quality_toggle",
            "params_version": "params_version",
            "use_coords": "use_coords",
            "use_order": "use_order",
        }

        for src_key, dst_key in key_map.items():
            if src_key in comment_data:
                val = comment_data[src_key]
                params[dst_key] = val

        # Extract resolution from request_type or width/height
        if "width" in comment_data and "height" in comment_data:
            params["size"] = f"{comment_data['width']}x{comment_data['height']}"

        # Keep legacy helpers available for callers that expect them
        if "qualityToggle" in comment_data and "quality_toggle" not in params:
            params["quality_toggle"] = comment_data["qualityToggle"]
        if "ucPreset" in comment_data and "uc_preset" not in params:
            params["uc_preset"] = comment_data["ucPreset"]
        if "params_version" in comment_data and "params_version" not in params:
            params["params_version"] = comment_data["params_version"]
        if "use_coords" in comment_data and "use_coords" not in params:
            params["use_coords"] = comment_data["use_coords"]
        if "use_order" in comment_data and "use_order" not in params:
            params["use_order"] = comment_data["use_order"]

        return params if params else None

    def _flatten_nai_structured_prompt(self, blob: Any) -> Optional[str]:
        """Flatten V4/V4.5/V5 ``v4_prompt`` / ``v4_negative_prompt`` JSON.

        Official NovelAI Diffusion V5 (2026-08-21) still writes the V4
        request shape: ``caption.base_caption`` plus ``char_captions``.
        There is no first-party ``v5_prompt`` key in
        ``NovelAI/novelai-image-metadata``.
        """
        if isinstance(blob, dict):
            caption = blob.get("caption")
            if isinstance(caption, dict) and caption.get("base_caption"):
                return self._flatten_text_value(caption.get("base_caption"))
            return self._flatten_text_value(
                blob.get("prompt") or blob.get("caption") or blob
            )
        return self._flatten_text_value(blob)

    def _extract_nai_character_prompts(self, comment_data: dict) -> Optional[List[Dict[str, Any]]]:
        """Extract NAI V4/V4.5/V5 character prompts from Comment JSON.

        Live request bodies (V5 still uses ``params_version`` 4) store
        characters as top-level ``characterPrompts`` and as
        ``v4_prompt.caption.char_captions``. V5 allows up to 22 slots.
        Some exporters also nest ``v4_prompt.character_prompts``.
        """
        if not isinstance(comment_data, dict):
            return None

        by_index: Dict[int, Dict[str, Any]] = {}

        def ensure(index: int) -> Dict[str, Any]:
            slot = by_index.get(index)
            if slot is None:
                slot = {"index": index, "prompt": "", "negative_prompt": ""}
                by_index[index] = slot
            return slot

        def as_text(value: Any) -> str:
            if isinstance(value, dict):
                return self._flatten_text_value(value) or ""
            if isinstance(value, str):
                return value.strip()
            return str(value).strip() if value else ""

        structured = comment_data.get("v4_prompt")
        if isinstance(structured, dict):
            nested = structured.get("character_prompts")
            if isinstance(nested, list):
                for index, char in enumerate(nested):
                    if not isinstance(char, dict):
                        continue
                    slot = ensure(index)
                    prompt_val = as_text(char.get("prompt", ""))
                    negative_val = as_text(char.get("ucPrompt", char.get("uc", "")))
                    if prompt_val:
                        slot["prompt"] = prompt_val
                    if negative_val:
                        slot["negative_prompt"] = negative_val
                    center = char.get("center")
                    if isinstance(center, dict):
                        slot["center"] = {
                            "x": center.get("x", 0.5),
                            "y": center.get("y", 0.5),
                        }

            caption = structured.get("caption")
            if isinstance(caption, dict):
                char_captions = caption.get("char_captions")
                if isinstance(char_captions, list):
                    for index, item in enumerate(char_captions):
                        if not isinstance(item, dict):
                            continue
                        slot = ensure(index)
                        text = as_text(item.get("char_caption") or item.get("prompt"))
                        if text and not slot["prompt"]:
                            slot["prompt"] = text
                        centers = item.get("centers")
                        if (
                            isinstance(centers, list)
                            and centers
                            and isinstance(centers[0], dict)
                            and "center" not in slot
                        ):
                            slot["center"] = {
                                "x": centers[0].get("x", 0.5),
                                "y": centers[0].get("y", 0.5),
                            }

        v4_neg = comment_data.get("v4_negative_prompt")
        if isinstance(v4_neg, dict):
            caption = v4_neg.get("caption") if isinstance(v4_neg.get("caption"), dict) else {}
            char_captions = caption.get("char_captions") if isinstance(caption, dict) else None
            if isinstance(char_captions, list):
                for index, item in enumerate(char_captions):
                    if not isinstance(item, dict):
                        continue
                    slot = ensure(index)
                    text = as_text(item.get("char_caption") or item.get("uc"))
                    if text and not slot["negative_prompt"]:
                        slot["negative_prompt"] = text

        top = comment_data.get("characterPrompts") or comment_data.get("character_prompts")
        if isinstance(top, list):
            for index, char in enumerate(top):
                if not isinstance(char, dict):
                    continue
                slot = ensure(index)
                prompt_val = as_text(char.get("prompt") or char.get("char_caption"))
                negative_val = as_text(char.get("uc") or char.get("ucPrompt"))
                if prompt_val:
                    slot["prompt"] = prompt_val
                if negative_val:
                    slot["negative_prompt"] = negative_val
                center = char.get("center")
                if isinstance(center, dict):
                    slot["center"] = {
                        "x": center.get("x", 0.5),
                        "y": center.get("y", 0.5),
                    }

        characters = [
            by_index[index]
            for index in sorted(by_index)
            if by_index[index].get("prompt") or by_index[index].get("negative_prompt")
        ]
        return characters or None

