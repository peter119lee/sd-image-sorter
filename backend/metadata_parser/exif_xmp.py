# =============================================================================
# metadata_parser.exif_xmp - metadata_parser decomposition stages 1+2 (2026-07-13).
# Extracted VERBATIM from backend/metadata_parser.py @ c06d374 (4,912 lines).
# Source line ranges (original file): 4551-4590, 4601-4646, 4656-4695, 4706-4796.
# Mixin: EXIF / XMP / GIF / TIFF SD-metadata extraction (pure format parsing).
# self.* calls and class-constant lookups resolve via MRO exactly as before.
# Patched seams (Image / open / _MAX_* / _sidecar_directory_cache): the readers
# live in metadata_parser/_runtime.py behind the package get/set proxy in
# __init__.py (stage 3); see tests/test_metadata_parser_pins.py.
import json
import logging
import re
from typing import Dict, Any
from PIL import Image
from .constants import _MAX_XMP_CHUNK_BYTES

logger = logging.getLogger(__name__)

class ExifXmpMixin:
    """EXIF / XMP / GIF / TIFF SD-metadata extraction (pure format parsing)."""

    def _extract_exif(self, img: Image.Image) -> dict:
        """Extract top-level EXIF data from image."""
        try:
            return self._extract_exif_mapping(img.getexif())
        except Exception as e:
            logger.debug("Error extracting EXIF: %s", e)
        return {}

    def _extract_exif_mapping(self, exif: Any) -> dict:
        """Extract top-level EXIF tags from an Exif mapping object."""
        metadata = {}
        try:
            if exif:
                from PIL import ExifTags
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag_name == "XPComment":
                        decoded = self._decode_exif_user_comment(value)
                        if decoded:
                            metadata["XPComment"] = decoded
                            if "Comment" not in metadata:
                                metadata["Comment"] = decoded
                        continue
                    if tag_name in {"ImageDescription", "Software", "Model", "Make"} and isinstance(value, bytes):
                        decoded = self._decode_exif_user_comment(value)
                        if decoded:
                            metadata[tag_name] = decoded
                        continue
                    if isinstance(value, bytes):
                        try:
                            metadata[tag_name] = value.decode('utf-8', errors='replace')
                        except Exception as e:
                            logger.debug("Failed to decode EXIF tag %s: %s", tag_name, e)
                            metadata[tag_name] = str(value)
                    else:
                        metadata[tag_name] = value
        except Exception as e:
            logger.debug("Error extracting EXIF: %s", e)
        return metadata

    def _extract_exif_ifd(self, img: Image.Image) -> dict:
        """
        Extract EXIF IFD (sub-directory) data, specifically UserComment.
        NovelAI V4+ stores prompt data here for WebP images.
        """
        try:
            return self._extract_exif_ifd_mapping(img.getexif())
        except Exception:
            return {}

    def _extract_exif_ifd_mapping(self, exif: Any) -> dict:
        """
        Extract EXIF IFD (sub-directory) data, specifically UserComment.
        NovelAI V4+ stores prompt data here for WebP images.
        """
        metadata: Dict[str, Any] = {}
        try:
            if not exif:
                return metadata

            # Get the Exif IFD (tag 0x8769)
            ifd = exif.get_ifd(0x8769)
            if ifd:
                from PIL import ExifTags
                for tag_id, value in ifd.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

                    # Special handling for UserComment (tag 37510 / 0x9286)
                    if tag_id == 37510:
                        metadata["UserComment"] = value  # Keep raw bytes for parsing
                        decoded = self._decode_exif_user_comment(value)
                        if decoded:
                            metadata["UserCommentText"] = decoded
                    elif isinstance(value, bytes):
                        try:
                            metadata[tag_name] = value.decode('utf-8', errors='replace')
                        except Exception as e:
                            logger.debug("Failed to decode EXIF IFD tag %s: %s", tag_name, e)
                            metadata[tag_name] = str(value)
                    else:
                        metadata[tag_name] = value
        except Exception:
            # Non-critical, some images don't have EXIF IFD
            pass
        return metadata

    def _extract_jpeg_sd_metadata(self, img: Image.Image) -> dict:
        """Extract SD metadata from JPEG EXIF fields."""
        try:
            return self._extract_sd_metadata_from_exif(img.getexif())
        except Exception as e:
            logger.debug("Error extracting JPEG SD metadata: %s", e)
            return {}

    def _extract_gif_comment_metadata(self, img: Image.Image) -> dict:
        """Extract SD metadata from lightweight GIF comment fields."""
        comment = getattr(img, "info", {}).get("comment")
        if comment is None:
            return {}
        text = self._decode_exif_user_comment(comment)
        if not text:
            return {}
        if "Steps:" in text and "Sampler:" in text:
            return {"Comment": text, "parameters": text}
        return {"Comment": text, "prompt": text}

    def _extract_tiff_xmp(self, img: Image.Image) -> dict:
        """Extract XMP packet text from TIFF tag 700 when present."""
        try:
            tag_v2 = getattr(img, "tag_v2", None)
            if not tag_v2:
                return {}
            xmp_value = tag_v2.get(700)
            if xmp_value is None:
                return {}
            if isinstance(xmp_value, bytes):
                xmp_text = xmp_value[:_MAX_XMP_CHUNK_BYTES].decode("utf-8", errors="replace")
            elif isinstance(xmp_value, str):
                xmp_text = xmp_value[:_MAX_XMP_CHUNK_BYTES]
            else:
                xmp_text = str(xmp_value)[:_MAX_XMP_CHUNK_BYTES]
            return self._extract_xmp_sd_metadata(xmp_text) if xmp_text.strip() else {}
        except Exception as e:
            logger.debug("Error extracting TIFF XMP: %s", e)
            return {}

    def _extract_sd_metadata_from_exif(self, exif: Any) -> dict:
        """Extract SD metadata from an Exif mapping object."""
        metadata: Dict[str, Any] = {}
        try:
            if not exif:
                return metadata

            # Check ImageDescription (tag 0x010E)
            img_desc = exif.get(0x010E)
            if isinstance(img_desc, bytes):
                img_desc = self._decode_exif_user_comment(img_desc)
            if img_desc and isinstance(img_desc, str):
                if "ImageDescription" not in metadata:
                    metadata["ImageDescription"] = img_desc

            # Check for parameters in ImageDescription
            if img_desc and "Steps:" in str(img_desc) and "Sampler:" in str(img_desc):
                metadata["parameters"] = str(img_desc)

            # Check UserComment in Exif IFD for ComfyUI / WebUI params
            ifd = exif.get_ifd(0x8769)
            if ifd:
                uc = ifd.get(0x9286)  # UserComment
                if uc:
                    text = self._decode_exif_user_comment(uc)
                    if text and text.strip():
                        text = text.strip()
                        metadata.setdefault("UserCommentText", text)
                        if text.startswith('{'):
                            try:
                                obj = json.loads(text)
                                if isinstance(obj, dict) and any(
                                    isinstance(v, dict) and "class_type" in v
                                    for v in obj.values()
                                ):
                                    metadata["prompt"] = text
                            except (json.JSONDecodeError, ValueError):
                                pass
                        if "prompt" not in metadata and "Steps:" in text and "Sampler:" in text:
                            metadata["parameters"] = text
        except Exception as e:
            logger.debug("Error extracting JPEG SD metadata: %s", e)
        return metadata

    def _extract_xmp_sd_metadata(self, xmp_text: str) -> dict:
        """Extract SD prompt metadata from a decoded XMP packet."""
        metadata: Dict[str, Any] = {"xmp": xmp_text}

        parameter_patterns = (
            r"<[^>]*(?:parameters|Parameter|UserComment)[^>]*>(.*?)</[^>]+>",
            r"(?:sd:)?parameters=[\"'](.*?)[\"']",
        )
        for pattern in parameter_patterns:
            match = re.search(pattern, xmp_text, re.DOTALL | re.IGNORECASE)
            if match:
                metadata["parameters"] = self._decode_xmp_text_value(match.group(1))
                break
        if "parameters" not in metadata and "Steps:" in xmp_text and "Sampler:" in xmp_text:
            metadata["parameters"] = self._decode_xmp_text_value(xmp_text)

        prompt_patterns = (
            r"<[^>]*(?:prompt|Prompt)[^>]*>(.*?)</[^>]+>",
            r"(?:sd:)?prompt=[\"'](.*?)[\"']",
        )
        for pattern in prompt_patterns:
            match = re.search(pattern, xmp_text, re.DOTALL | re.IGNORECASE)
            if not match:
                continue
            prompt_value = self._decode_xmp_text_value(match.group(1))
            if prompt_value.strip().startswith("{"):
                metadata["prompt"] = prompt_value
                break

        if "prompt" not in metadata and "prompt" in xmp_text.lower():
            json_start = xmp_text.find('{')
            if json_start != -1:
                json_end = xmp_text.rfind('}')
                if json_end > json_start:
                    potential_json = xmp_text[json_start:json_end + 1]
                    try:
                        json.loads(potential_json)
                        metadata["prompt"] = potential_json
                    except json.JSONDecodeError as e:
                        logger.debug('Failed to parse XMP prompt JSON: %s', e)

        return metadata

    def _decode_xmp_text_value(self, value: str) -> str:
        """Decode a text value copied out of XMP XML/attribute content."""
        from html import unescape

        return unescape(value).strip()

