"""Generic prompt-likeness scoring for metadata extraction (v3.5.0 L2).

The precise ComfyUI graph tracer (L1) understands node semantics; this module
deliberately does NOT. It answers one question for any string found anywhere
in a workflow: "does this text read like a prompt?" — so brand-new custom
node packs are caught without teaching the parser their shapes.

Two independent signals, the better one wins:

- Vocabulary: the share of comma tokens that are known danbooru tags
  (reusing the 140k vocabulary bundled for tag autocomplete). A string whose
  tokens are half booru tags IS a prompt, whatever node held it.
- Structure: natural-language prompts ("a watercolor of a fox, soft light")
  hit few booru tags, so comma-separated multi-word shape scores on its own.

Everything fails open: with no vocabulary available, structure alone decides.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Candidates below the floor are never accepted as prompts.
PROMPT_SCORE_FLOOR = 0.35
# Strings shorter than this cannot carry enough signal to judge.
MIN_CANDIDATE_LENGTH = 12
# Bonus when the string came out of a text-encoder-ish node.
TEXT_NODE_BONUS = 0.15

_WEIGHT_SYNTAX_RE = re.compile(r"[()\[\]{}]|:\d+(?:\.\d+)?")
_LORA_TAG_RE = re.compile(r"<[^<>]*>")
_FILE_EXT_RE = re.compile(
    r"\.(safetensors|ckpt|pt|pth|bin|onnx|png|jpe?g|webp|gif|mp4|json|ya?ml|txt|csv)$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

# Input keys whose values are configuration, not prose — skipped at harvest.
NON_PROMPT_KEYS = frozenset({
    "separator", "delimiter", "sampler_name", "scheduler", "ckpt_name",
    "unet_name", "vae_name", "lora_name", "model_name", "control_net_name",
    "filename_prefix", "filename", "font", "font_name", "preset",
    "upscale_method", "method", "mode", "device", "output_format",
    "extension", "path", "directory", "folder", "custom_layer_filter",
})

# Node class_types whose strings are never generation prompts (tagger dumps,
# loaders). ShowText that is *fed by* a tagger is skipped separately.
HARVEST_SKIP_CLASS_MARKERS = (
    "WD14Tagger",
    "CLIPLoader",
    "VAELoader",
    "CheckpointLoader",
    "UNETLoader",
    "DiffusionModelLoader",
)

_BUS_NODE_TYPES = frozenset({"GetNode", "Get", "SetNode", "Set", "Reroute"})


def _source_chain_has_tagger(
    nodes: Dict[str, dict],
    ref: Any,
    seen: Optional[set] = None,
) -> bool:
    """True when a ShowText (or similar) is fed by a Tagger through Get/Set buses."""
    if seen is None:
        seen = set()
    if not isinstance(ref, (list, tuple)) or not ref:
        return False
    node_id = str(ref[0])
    if node_id in seen:
        return False
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return False
    seen.add(node_id)
    class_type = str(node.get("class_type") or "")
    if "Tagger" in class_type:
        return True
    if class_type not in _BUS_NODE_TYPES and "Reroute" not in class_type:
        return False
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    for value in inputs.values():
        if _source_chain_has_tagger(nodes, value, seen):
            return True
    return False

_NEGATIVE_INDICATORS = (
    "worst quality", "low quality", "bad quality", "lowres",
    "bad anatomy", "worst hands", "bad hands", "deformed", "blurry",
    "low_resolution", "medium_resolution", "low_score",
    "pixelated", "compression artifacts", "jpeg artifacts",
    "bad_anatomy", "worst_hands", "extra fingers", "fewer fingers",
    "extra digits", "missing fingers", "watermark", "signature",
    "easynegative", "negativexl", "badhandv4", "bad-hands",
    "worst_quality", "low_quality",
)


def _vocab_index() -> Optional[Dict[str, int]]:
    try:
        from services.tag_suggest_service import get_vocab_tag_index

        return get_vocab_tag_index()
    except Exception as exc:  # pragma: no cover - defensive import guard
        logger.debug("prompt scorer running without vocabulary: %s", exc)
        return None


def tokenize_prompt_text(text: str) -> List[str]:
    """Split a candidate into normalized comma tokens (weight syntax removed)."""
    cleaned = _LORA_TAG_RE.sub(" ", str(text or ""))
    cleaned = _WEIGHT_SYNTAX_RE.sub(" ", cleaned)
    tokens: List[str] = []
    for part in re.split(r"[,，、;；]+", cleaned):
        token = re.sub(r"\s+", " ", part).strip().lower()
        if token:
            tokens.append(token)
    return tokens


def _vocab_hit_ratio(tokens: Sequence[str], vocab: Optional[Dict[str, int]]) -> float:
    if not tokens or not vocab:
        return 0.0
    hits = 0
    for token in tokens:
        underscored = token.replace(" ", "_")
        if underscored in vocab or token in vocab:
            hits += 1
    return hits / len(tokens)


def _structure_score(text: str, tokens: Sequence[str]) -> float:
    """Shape-only score for tag lists AND natural-language prompts.

    Comma-token count is the booru/A1111 signal. Word count is the Flux/Krea/
    Midjourney signal: a paragraph with no commas is still a prompt. The
    60-char average-token cap only applies when there are *multiple* comma
    segments — applying it to a single long sentence used to reject every
    prose prompt over 60 characters.
    """
    words = str(text).split()
    word_count = len(words)
    token_count = len(tokens)
    if token_count >= 5:
        base = 0.5
    elif token_count >= 3:
        base = 0.42
    elif word_count >= 8:
        base = 0.48
    elif word_count >= 6:
        base = 0.42
    else:
        return 0.1
    if token_count >= 2:
        lengths = [len(t) for t in tokens]
        average = sum(lengths) / len(lengths)
        if not 2 <= average <= 80:
            return 0.15
    return min(0.55, base + min(0.05, max(token_count, word_count // 4) * 0.005))


def looks_like_non_prompt_value(text: str) -> bool:
    """Values that are clearly configuration: paths, URLs, model files, JSON."""
    stripped = str(text or "").strip().lstrip("\x00").strip()
    if stripped.startswith("ASCII") or stripped.startswith("UNICODE"):
        stripped = stripped.split("\x00", 1)[-1] if "\x00" in stripped else stripped[6:].lstrip()
        stripped = stripped.lstrip("\x00 ").strip()
    if len(stripped) < MIN_CANDIDATE_LENGTH:
        return True
    if _URL_RE.match(stripped):
        return True
    if _FILE_EXT_RE.search(stripped.split(",")[-1].strip()) or _FILE_EXT_RE.search(stripped):
        return True
    if ("\\" in stripped or "/" in stripped) and "," not in stripped:
        return True
    first = stripped.lstrip()[:1]
    if first in ("{", "["):
        return True
    if re.fullmatch(r"[\d\s.,:x×-]+", stripped):
        return True
    # kjnodes Set/Get bus titles and other UI labels, not generation text.
    if "【" in stripped and "】" in stripped and not re.search(r"[,，、]", stripped):
        return True
    return False


def score_prompt_likeness(text: str, vocab: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Score how much a string reads like an SD prompt (0..1).

    Booru tag lists score via vocabulary. Natural-language prompts (Flux,
    Krea, Midjourney, SD3 T5) score via sentence structure and the shared
    caption-format classifier — format is a keep signal, never a discard
    (see caption_format invariant).
    """
    tokens = tokenize_prompt_text(text)
    if vocab is None:
        vocab = _vocab_index()
    hit_ratio = _vocab_hit_ratio(tokens, vocab)
    vocab_score = 0.6 + 0.4 * hit_ratio if hit_ratio >= 0.4 else hit_ratio
    structure = _structure_score(text, tokens)
    return {
        "score": max(vocab_score, structure),
        "vocab_hit_ratio": hit_ratio,
        "token_count": len(tokens),
        "vocab_available": vocab is not None,
    }


def is_negative_prompt_text(text: str) -> bool:
    """3+ classic negative-quality indicators → negative prompt."""
    lower = str(text or "").lower()
    matches = sum(1 for indicator in _NEGATIVE_INDICATORS if indicator in lower)
    return matches >= 3


def _widget_strings_for_harvest(value: Any) -> List[str]:
    """Flatten ShowText-style nested string widgets; skip JSON/token stacks."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped[:1] in "{[":
            return []
        return [stripped]
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(item, dict) for item in value):
            return []
        texts: List[str] = []
        for item in value:
            texts.extend(_widget_strings_for_harvest(item))
        return texts
    return []


def harvest_prompt_candidates(nodes: Dict[str, dict],
                              text_node_types: Iterable[str]) -> List[Dict[str, Any]]:
    """Collect every plausible prompt string from inputs AND widgets.

    No node-type knowledge required — that is the point. `text_node_types`
    only adds a small prior bonus for encoder-ish nodes. Custom prompt UIs
    that store text only in ``widgets_values`` (unknown class names) are
    still harvested.
    """
    type_markers = tuple(text_node_types or ())
    vocab = _vocab_index()
    candidates: List[Dict[str, Any]] = []
    seen_texts: set = set()

    def push(text: str, node_id: str, class_type: str, key: str, is_text_node: bool) -> None:
        stripped = text.strip()
        if not stripped or stripped in seen_texts:
            return
        if str(key).lower() in NON_PROMPT_KEYS:
            return
        if looks_like_non_prompt_value(stripped):
            return
        result = score_prompt_likeness(stripped, vocab)
        score = result["score"] + (TEXT_NODE_BONUS if is_text_node else 0.0)
        seen_texts.add(stripped)
        candidates.append({
            "text": stripped,
            "score": round(min(1.0, score), 4),
            "node_id": str(node_id),
            "class_type": class_type,
            "key": str(key),
            "vocab_hit_ratio": result["vocab_hit_ratio"],
        })

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or node.get("type") or "")
        if any(marker in class_type for marker in HARVEST_SKIP_CLASS_MARKERS):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if "ShowText" in class_type:
            ref = inputs.get("text") or inputs.get("string")
            if _source_chain_has_tagger(nodes, ref):
                continue
        is_text_node = any(marker in class_type for marker in type_markers)
        for key, value in inputs.items():
            if isinstance(value, str):
                push(value, node_id, class_type, key, is_text_node)
        if class_type in _BUS_NODE_TYPES:
            continue
        for index, text in enumerate(_widget_strings_for_harvest(node.get("widgets_values"))):
            push(text, node_id, class_type, f"widgets_values[{index}]", is_text_node)
    return candidates


def _dedupe_substrings(ordered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop candidates fully contained in an already-kept longer one."""
    kept: List[Dict[str, Any]] = []
    for candidate in sorted(ordered, key=lambda c: len(c["text"]), reverse=True):
        normalized = re.sub(r"\s+", " ", candidate["text"].lower())
        if any(normalized in re.sub(r"\s+", " ", other["text"].lower()) for other in kept):
            continue
        kept.append(candidate)
    return kept


def pick_positive_negative(candidates: List[Dict[str, Any]],
                           floor: float = PROMPT_SCORE_FLOOR,
                           ) -> Tuple[Optional[str], Optional[str]]:
    """Choose the best positive and negative from harvested candidates."""
    eligible = [c for c in candidates if c["score"] >= floor]
    if not eligible:
        return (None, None)
    unique = _dedupe_substrings(eligible)
    negatives = [c for c in unique if is_negative_prompt_text(c["text"])]
    positives = [c for c in unique if not is_negative_prompt_text(c["text"])]

    def best(pool: List[Dict[str, Any]]) -> Optional[str]:
        if not pool:
            return None
        top = max(candidate["score"] for candidate in pool)
        near = [candidate for candidate in pool if candidate["score"] >= top - 0.15]
        near.sort(key=lambda c: (len(c["text"]), c["score"]), reverse=True)
        return near[0]["text"]

    return (best(positives), best(negatives))
