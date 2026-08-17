"""Caption-to-tag parsing + sanitize mixin for ToriiGateTagger (split 2026-07).

Code moved verbatim from toriigate_tagger.py: the pure parsing half of the class (_strip_reasoning through
_build_result plus _resize_for_inference) and the caption tables it reads
(RATING_TAGS / EXPLICIT_HINT_TAGS / CAPTION_*). The ONLY non-verbatim edits:
_resize_for_inference resolves TORIIGATE_MAX_IMAGE_PIXELS (5 read sites),
TORIIGATE_MAX_ASPECT_RATIO (4 read sites) and ToriiGateImageGeometryError
(the raise) through _svc() at call time -- all three stay DEFINED on the
facade (suites read/patch them as toriigate_tagger.<name>, and the raised
exception must BE the facade class). _strip_reasoning keeps its lazy
in-function vlm_providers import.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from PIL import Image


def _svc():
    """Resolve the facade-owned geometry limits + error class at call time.

    The suites read/patch ``toriigate_tagger.TORIIGATE_MAX_IMAGE_PIXELS`` /
    ``TORIIGATE_MAX_ASPECT_RATIO`` / ``ToriiGateImageGeometryError`` on the
    facade module object; a from-import here would freeze an independent
    binding. The lazy import avoids a facade<->mixin load cycle.
    """
    import toriigate_tagger

    return toriigate_tagger


RATING_TAGS = {"general", "sensitive", "questionable", "explicit"}
EXPLICIT_HINT_TAGS = {
    "pussy",
    "penis",
    "dick",
    "anus",
    "cum",
    "sex",
    "nude",
    "nipples",
}

CAPTION_COUNT_PATTERNS = (
    (re.compile(r"\b(?:a|an|one|single)\s+(?:young\s+)?(?:girl|woman)\b"), "1girl"),
    (re.compile(r"\b(?:a|an|one|single)\s+(?:young\s+)?boy\b"), "1boy"),
    (re.compile(r"\b(?:girl|woman)\s+with\b"), "1girl"),
    (re.compile(r"\bboy\s+with\b"), "1boy"),
    (re.compile(r"\b(?:two|2)\s+girls\b"), "2girls"),
    (re.compile(r"\b(?:two|2)\s+boys\b"), "2boys"),
)
CAPTION_ATTRIBUTE_PATTERNS = (
    (
        re.compile(
            r"\b(black|blue|brown|green|grey|gray|orange|pink|purple|red|silver|white|yellow|blond|blonde)\s+hair\b"
        ),
        "{}_hair",
    ),
    (
        re.compile(
            r"\b(black|blue|brown|green|grey|gray|orange|pink|purple|red|silver|white|yellow|gold|golden)\s+eyes\b"
        ),
        "{}_eyes",
    ),
    (
        re.compile(
            r"\b(black|blue|brown|green|grey|gray|orange|pink|purple|red|silver|white|yellow)\s+blazer\b"
        ),
        "{}_blazer",
    ),
    (
        re.compile(
            r"\b(black|blue|brown|green|grey|gray|orange|pink|purple|red|silver|white|yellow)\s+shirt\b"
        ),
        "{}_shirt",
    ),
    (
        re.compile(
            r"\b(black|blue|brown|green|grey|gray|orange|pink|purple|red|silver|white|yellow)\s+bow\s+tie\b"
        ),
        "{}_bowtie",
    ),
)
CAPTION_PHRASE_TAGS = (
    ("school uniform", ("school_uniform",)),
    ("blazer", ("blazer",)),
    ("white shirt", ("white_shirt", "shirt")),
    ("shirt", ("shirt",)),
    ("red bow tie", ("red_bowtie", "bowtie")),
    ("bow tie", ("bowtie",)),
    ("monitor", ("monitor",)),
    ("screen", ("screen",)),
    ("viewfinder", ("viewfinder",)),
    ("recording", ("recording",)),
    ("rec indicator", ("recording",)),
    ("security camera", ("security_camera",)),
    ("tear-streaked", ("tears",)),
    ("tears", ("tears",)),
    ("crying", ("crying",)),
    ("distressed", ("distressed",)),
    ("fear", ("fear",)),
    ("restrained", ("restrained",)),
    ("cuffed", ("handcuffs", "restrained")),
    ("bound", ("bound",)),
    ("nude", ("nude",)),
    ("breasts", ("breasts",)),
    ("breast", ("breasts",)),
    ("nipples", ("nipples",)),
    ("nipple", ("nipples",)),
    ("buttocks", ("buttocks",)),
    ("anus", ("anus",)),
    ("vulva", ("pussy",)),
    ("labia", ("pussy",)),
    ("vaginal", ("pussy",)),
    ("genitalia", ("pussy",)),
    ("spread wide", ("spread_legs",)),
    ("legs spread", ("spread_legs",)),
    ("against the wall", ("against_wall",)),
    ("through the wall", ("against_wall",)),
)


class _CaptionParsingMixin:
    """Pure caption/tag parsing half of ToriiGateTagger (no module-global state)."""

    @staticmethod
    def _strip_reasoning(text: str) -> str:
        # Delegates to the shared VLM helper so the <think>-stripping behaviour
        # stays identical to the OpenAI-compatible provider (audit P2-9). Lazy
        # import keeps toriigate_tagger free of an import-time vlm_providers dep.
        from vlm_providers.base import strip_reasoning

        return strip_reasoning(text)

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        fence = re.match(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", text.strip(), re.DOTALL)
        return fence.group(1).strip() if fence else text.strip()

    @classmethod
    def _looks_like_jsonish_nl_payload(cls, text: str) -> bool:
        """True for JSON-shaped NL payloads; false for ordinary prose.

        The old guard treated any caption containing ``"key": "value"`` as
        JSON-ish, which can corrupt normal prose. Only whole-response JSON
        objects/arrays, fenced JSON, or top-level caption/tag key-value payloads
        should be sanitized.
        """
        cleaned = cls._strip_json_fence(cls._strip_reasoning(text))
        if not cleaned:
            return False
        if re.match(r'^\{\s*"(?:[^"\\]|\\.)+"\s*:', cleaned):
            return True
        if re.match(r'^\[\s*(?:\{|"|\])', cleaned):
            return True
        return bool(
            re.match(
                r'^"(?:description|caption|nl|nl_caption|natural_language|text|summary|tags|tag)"\s*:',
                cleaned,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _normalize_color_token(value: str) -> str:
        token = str(value or "").strip().lower()
        if token == "gray":
            return "grey"
        if token == "blond":
            return "blonde"
        if token == "golden":
            return "gold"
        return token

    @classmethod
    def _normalize_tag_token(cls, value: str) -> str:
        token = str(value or "").strip().lower()
        token = token.replace("`", "").replace("*", "").replace("•", "")
        token = re.sub(r"^\s*(?:[-•*]+|\d+\)|\d+\.)\s*", "", token)
        token = token.replace(" ", "_")
        token = token.replace("-", "_")
        token = re.sub(r"_+", "_", token)
        token = re.sub(r"[^a-z0-9_(),]+", "", token)
        token = token.strip("_, ")
        return token

    @classmethod
    def _extract_tag_list_tokens(cls, cleaned: str) -> List[str]:
        lowered = cleaned.lower()
        if "tags:" in lowered:
            cleaned = cleaned[lowered.index("tags:") + len("tags:") :]
        cleaned = cleaned.replace("\r", "\n").replace(";", ",")
        parts = re.split(r"[\n,]+", cleaned)
        tags: List[str] = []
        seen = set()
        for part in parts:
            token = cls._normalize_tag_token(part)
            if not token or token in seen:
                continue
            seen.add(token)
            tags.append(token)
        return tags

    @staticmethod
    def _looks_like_structured_caption(cleaned: str) -> bool:
        stripped = cleaned.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return True
        if re.search(r'"[^"]+"\s*:\s*"', stripped):
            return True
        if re.search(r"[.!?]", stripped) and re.search(
            r"\b(the|a|an|with|and|is|are|this|that|there)\b",
            stripped.lower(),
        ):
            return True
        return False

    @staticmethod
    def _tag_list_seems_valid(tags: List[str]) -> bool:
        if not tags:
            return False
        if sum(1 for tag in tags if len(tag) > 40) >= 2:
            return False
        if any("character_" in tag for tag in tags):
            return False
        return True

    @classmethod
    def _extract_json_string_values(cls, cleaned: str) -> List[str]:
        try:
            parsed = json.loads(cleaned)
        except Exception:
            parsed = None

        def collect(node: Any) -> List[str]:
            values: List[str] = []
            if isinstance(node, str):
                values.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    values.extend(collect(value))
            elif isinstance(node, list):
                for value in node:
                    values.extend(collect(value))
            return values

        if parsed is not None:
            return collect(parsed)

        return re.findall(r'"[^"]+"\s*:\s*"([^"]+)"', cleaned)

    # JSON keys that hold the prose caption, in preference order. Keys that
    # hold tag lists / metadata are deliberately excluded so a tags-only JSON
    # answer yields an empty NL caption instead of duplicating booru tags.
    _NL_CAPTION_JSON_KEYS = (
        "description",
        "caption",
        "nl",
        "nl_caption",
        "natural_language",
        "text",
        "summary",
    )
    _NL_NON_CAPTION_JSON_KEYS = {"tags", "tag", "rating", "score", "characters"}

    @classmethod
    def _sanitize_nl_text(cls, text: str) -> str:
        """Extract a plain-prose caption from raw model output.

        ToriiGate is fine-tuned heavily on JSON answers and often returns
        ``{"description": ..., "tags": ...}`` even when asked for prose, and
        the JSON is frequently truncated mid-string by the max_new_tokens
        cap. Handles complete JSON, truncated JSON, and plain sentences.
        """
        cleaned = cls._strip_json_fence(cls._strip_reasoning(text))
        if not cls._looks_like_jsonish_nl_payload(cleaned):
            return cleaned

        # Complete JSON: parse and pull the caption-like key.
        try:
            parsed = json.loads(cleaned)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for key in cls._NL_CAPTION_JSON_KEYS:
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            candidates = [
                value
                for key, value in parsed.items()
                if isinstance(value, str)
                and value.strip()
                and str(key).lower() not in cls._NL_NON_CAPTION_JSON_KEYS
            ]
            return max(candidates, key=len).strip() if candidates else ""
        if isinstance(parsed, list):
            strings = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
            return " ".join(strings)

        # Truncated JSON: pull the (possibly unterminated) caption value.
        match = re.search(
            r'"(?:description|caption|nl|nl_caption|natural_language|text|summary)"\s*:\s*"((?:[^"\\]|\\.)*)',
            cleaned,
        )
        if match:
            value = match.group(1).rstrip("\\")
            try:
                value = json.loads(f'"{value}"')
            except Exception:
                value = value.replace('\\"', '"').replace("\\n", " ")
            return value.strip()

        # Key/value soup without a caption key: longest non-tag value wins.
        pairs = re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)
        candidates = [
            value
            for key, value in pairs
            if value.strip() and key.lower() not in cls._NL_NON_CAPTION_JSON_KEYS
        ]
        if candidates:
            return max(candidates, key=len).strip()
        if pairs:
            return ""

        # JSON-looking but unparseable and pairless: strip the wrapper crud.
        return cleaned.strip('{}[]"\n\t ,')

    @classmethod
    def _extract_tags_from_caption(cls, cleaned: str) -> List[str]:
        caption_chunks = cls._extract_json_string_values(cleaned)
        caption_text = " ".join(caption_chunks) if caption_chunks else cleaned
        lowered = caption_text.lower()

        tags: List[str] = []
        seen = set()

        def append_tag(value: str) -> None:
            token = cls._normalize_tag_token(value)
            if not token or token in seen:
                return
            seen.add(token)
            tags.append(token)

        for pattern, tag in CAPTION_COUNT_PATTERNS:
            if pattern.search(lowered):
                append_tag(tag)

        for pattern, template in CAPTION_ATTRIBUTE_PATTERNS:
            for match in pattern.findall(lowered):
                append_tag(template.format(cls._normalize_color_token(match)))

        for phrase, mapped_tags in CAPTION_PHRASE_TAGS:
            if phrase in lowered:
                for mapped in mapped_tags:
                    append_tag(mapped)

        if "mouth open" in lowered or "open mouth" in lowered:
            append_tag("open_mouth")
        if "lower body" in lowered:
            append_tag("lower_body")

        return tags

    @classmethod
    def _extract_tags(cls, text: str) -> List[str]:
        cleaned = cls._strip_reasoning(text)

        if cls._looks_like_structured_caption(cleaned):
            caption_tags = cls._extract_tags_from_caption(cleaned)
            if caption_tags:
                return caption_tags

        tag_list_tags = cls._extract_tag_list_tokens(cleaned)
        if cls._tag_list_seems_valid(tag_list_tags):
            return tag_list_tags

        caption_tags = cls._extract_tags_from_caption(cleaned)
        if caption_tags:
            return caption_tags
        return tag_list_tags

    @classmethod
    def _derive_rating(cls, tags: List[str]) -> str:
        for rating in ("explicit", "questionable", "sensitive", "general"):
            if rating in tags:
                return rating
        if any(tag in EXPLICIT_HINT_TAGS for tag in tags):
            return "explicit"
        return "general"

    @classmethod
    def _build_result(cls, text: str) -> Dict[str, Any]:
        tags = cls._extract_tags(text)
        rating = cls._derive_rating(tags)
        normalized_tags = [tag for tag in tags if tag not in RATING_TAGS]
        general_tags = []
        character_tags = []

        for tag in normalized_tags:
            target = character_tags if re.search(r"_\([^)]*\)$", tag) else general_tags
            target.append({"tag": tag, "confidence": 1.0})

        all_tags = [{"tag": rating, "confidence": 1.0}]
        all_tags.extend(general_tags)
        all_tags.extend(character_tags)

        return {
            "general_tags": general_tags,
            "character_tags": character_tags,
            "rating": rating,
            "rating_confidences": {rating: 1.0},
            "all_tags": all_tags,
            "raw_text": text,
            "nl_text": cls._sanitize_nl_text(text),
        }

    @staticmethod
    def _resize_for_inference(image: Image.Image) -> Image.Image:
        width, height = image.size
        long_edge = max(width, height)
        short_edge = min(width, height)
        aspect_ratio = long_edge / float(short_edge)
        if aspect_ratio > _svc().TORIIGATE_MAX_ASPECT_RATIO:
            raise _svc().ToriiGateImageGeometryError(
                f"ToriiGate image {width}x{height} has aspect ratio "
                f"{aspect_ratio:.2f}:1, which exceeds the supported "
                f"{_svc().TORIIGATE_MAX_ASPECT_RATIO:.0f}:1 limit. Crop the long edge "
                "or pad the short edge before tagging."
            )

        pixels = width * height
        if pixels <= _svc().TORIIGATE_MAX_IMAGE_PIXELS:
            return image

        scale = (_svc().TORIIGATE_MAX_IMAGE_PIXELS / float(pixels)) ** 0.5
        resized_width = max(1, int(width * scale))
        resized_height = max(1, int(height * scale))
        if resized_width >= resized_height:
            resized_width = min(
                resized_width,
                int(_svc().TORIIGATE_MAX_ASPECT_RATIO * resized_height),
            )
        else:
            resized_height = min(
                resized_height,
                int(_svc().TORIIGATE_MAX_ASPECT_RATIO * resized_width),
            )
        if resized_width * resized_height > _svc().TORIIGATE_MAX_IMAGE_PIXELS:
            if resized_width >= resized_height:
                resized_width = max(
                    1,
                    _svc().TORIIGATE_MAX_IMAGE_PIXELS // resized_height,
                )
            else:
                resized_height = max(
                    1,
                    _svc().TORIIGATE_MAX_IMAGE_PIXELS // resized_width,
                )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        return image.resize((resized_width, resized_height), resampling)
