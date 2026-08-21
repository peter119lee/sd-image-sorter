"""
Tag categorization rules and built-in mappings for SD Image Sorter.

Provides automatic tag categorization, tag sets (outfit groups),
and exclusion rules for intelligent prompt generation.
"""

import csv
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

# Data tables extracted to the tag_vocab/ package (2026-07 split). Every name
# is re-imported here because the 8 production importers and the test suite
# bind them via `from tag_rules import <NAME>` and patch them on this module
# (tests/test_tag_rules_pins.py, tests/test_tag_training_filters.py).
# The logic below stays in THIS file on purpose:
# - _iter_tagger_selected_tag_csvs derives the repo root from __file__, so
#   its defining module must sit directly under backend/ (pinned).
# - The lazy vocab caches are written via `global` by their loaders and read
#   by categorize_tag, so caches + loaders + readers must share one module
#   for the monkeypatch.setattr(tag_rules, ...) seams to stay effective.
from tag_vocab.builtin_rules import (
    BUILTIN_EXCLUSION_RULES,
    BUILTIN_TAG_SETS,
    WEIGHTED_GROUPS,
)
from tag_vocab.detail_keywords import (
    ACTION_DETAIL_KEYWORDS,
    BACKGROUND_OBJECT_KEYWORDS,
    CHARACTER_DETAIL_KEYWORDS,
    EXPRESSION_DETAIL_KEYWORDS,
    META_DETAIL_KEYWORDS,
    NSFW_BODY_KEYWORDS,
    OUTFIT_DETAIL_KEYWORDS,
)
from tag_vocab.detail_tokens import (
    ACTION_DETAIL_TOKENS,
    BACKGROUND_DETAIL_TOKENS,
    BODY_DETAIL_TOKENS,
    CHARACTER_ENTITY_TOKENS,
    EFFECT_KEYWORDS,
    EXPRESSION_DETAIL_TOKENS,
    HAIRSTYLE_KEYWORDS,
    META_STRUCTURE_TOKENS,
    OBJECT_PROP_KEYWORDS,
    OUTFIT_DETAIL_TOKENS,
    POSE_DETAIL_TOKENS,
    STYLE_DETAIL_TOKENS,
)
from tag_vocab.exact_sets import (
    ACTION_TAGS,
    ANGLE_TAGS,
    BACKGROUND_TAGS,
    BODY_TAGS,
    EXPRESSION_TAGS,
    META_TAGS,
    POSE_TAGS,
    QUALITY_TAGS,
    RATING_TAGS,
    STYLE_TAGS,
    SUBJECT_COUNT_TAGS,
)
from tag_vocab.franchises import _FRANCHISE_PATTERN, _FRANCHISE_SUFFIXES
from tag_vocab.outfit import (
    OUTFIT_KEYWORDS,
    _GARMENT_VETO_KEYWORDS,
    _GARMENT_VETO_TOKENS,
)

logger = logging.getLogger(__name__)

# ============================================================
# WD14 Character Tag Cache
# ============================================================
# Loaded lazily from selected_tags.csv files (category 4 = character)

_wd14_character_tags: Optional[Set[str]] = None
_booru_tag_categories: Optional[Dict[str, str]] = None
_storyaura_character_tags: Optional[Set[str]] = None

_BOORU_CATEGORY_BY_ID = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "meta",
    9: "rating",
}


def _iter_tagger_selected_tag_csvs() -> List[Path]:
    """Find local tagger selected_tags.csv files that can provide vocab metadata."""
    repo_root = Path(__file__).resolve().parent.parent
    roots: List[Path] = []
    try:
        from config import get_oppai_oracle_model_dir, get_wd14_model_dir

        roots.extend([Path(get_wd14_model_dir()), Path(get_oppai_oracle_model_dir())])
    except Exception:
        pass

    roots.extend(
        [
            repo_root / "data" / "models" / "wd14-tagger",
            repo_root / "models" / "wd14-tagger",
            repo_root / "data" / "models" / "oppai-oracle",
        ]
    )

    seen_roots: Set[Path] = set()
    csv_paths: List[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen_roots or not root.exists():
            continue
        seen_roots.add(resolved)
        csv_paths.extend(sorted(root.rglob("selected_tags.csv")))
    return csv_paths


def _load_wd14_character_tags() -> Set[str]:
    """Load character tag names from all available WD14 selected_tags.csv files."""
    global _wd14_character_tags
    if _wd14_character_tags is not None:
        return _wd14_character_tags

    tags: Set[str] = set()
    for csv_path in _iter_tagger_selected_tag_csvs():
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("category") == "4":
                        tag_name = row.get("name", "").strip()
                        if tag_name:
                            tags.add(tag_name.lower().replace(" ", "_"))
        except Exception as exc:
            logger.debug("Failed to load character tags from %s: %s", csv_path, exc)

    logger.info("Loaded %d character tags from WD14 model files", len(tags))
    _wd14_character_tags = tags
    return tags


def _load_storyaura_character_tags() -> Set[str]:
    """Popular character names from the bundled StoryAura catalog.

    Lets Dataset Maker / export classify character tags before any WD14
    model is downloaded. Isolated tests patch this cache to an empty set.
    """
    global _storyaura_character_tags
    if _storyaura_character_tags is not None:
        return _storyaura_character_tags
    try:
        from danbooru_catalog import get_character_tag_set

        tags = set(get_character_tag_set())
    except Exception as exc:
        logger.debug("StoryAura character names unavailable: %s", exc)
        tags = set()
    _storyaura_character_tags = tags
    return tags


def _load_booru_tag_categories() -> Dict[str, str]:
    """Load tagger vocab category metadata from available booru-style tag files."""
    global _booru_tag_categories
    if _booru_tag_categories is not None:
        return _booru_tag_categories

    categories: Dict[str, str] = {}
    for csv_path in _iter_tagger_selected_tag_csvs():
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tag_name = row.get("name", "").strip()
                    if not tag_name:
                        continue
                    try:
                        category_id = int(row.get("category", ""))
                    except (TypeError, ValueError):
                        continue
                    category = _BOORU_CATEGORY_BY_ID.get(category_id)
                    if category:
                        categories[tag_name.lower().replace(" ", "_")] = category
        except Exception as exc:
            logger.debug(
                "Failed to load booru tag categories from %s: %s", csv_path, exc
            )

    _booru_tag_categories = categories
    return categories


def _fallback_known_booru_general_category(
    tag_lower: str, clean_tokens: Set[str]
) -> str:
    """Return a non-unknown bucket for known tagger vocab category-0 tags."""
    if tag_lower.startswith(
        ("bad_", "worst_", "low_", "poor_")
    ) or clean_tokens.intersection({"error", "mistake", "quality", "noise", "jpeg"}):
        return "quality"
    if clean_tokens.intersection(
        {"style", "palette", "sepia", "grain", "contrast", "symmetry", "perspective"}
    ):
        return "style"
    if clean_tokens.intersection(
        {
            "smile",
            "angry",
            "shy",
            "sad",
            "happy",
            "annoyed",
            "nervous",
            "screaming",
            "thinking",
        }
    ):
        return "expression"
    if clean_tokens.intersection(
        {
            "standing",
            "sitting",
            "kneeling",
            "lying",
            "reclining",
            "leaning",
            "walking",
            "running",
            "turning",
            "handstand",
        }
    ):
        return "pose"
    if clean_tokens.intersection(
        {
            "holding",
            "taking",
            "recording",
            "punching",
            "kicking",
            "pulling",
            "control",
            "waking",
            "drying",
        }
    ):
        return "action"
    if clean_tokens.intersection(
        {
            "skin",
            "arm",
            "arms",
            "leg",
            "legs",
            "hand",
            "hands",
            "head",
            "hair",
            "eye",
            "eyes",
            "bone",
            "wing",
            "wings",
            "tail",
            "uterus",
            "prosthesis",
        }
    ):
        return "body"
    if clean_tokens.intersection(
        {
            "shirt",
            "dress",
            "skirt",
            "shoe",
            "shoes",
            "hat",
            "fedora",
            "cloth",
            "clothing",
            "cutout",
            "neckwear",
            "sleeve",
            "strap",
            "pin",
        }
    ):
        return "outfit"
    if clean_tokens.intersection(
        {
            "room",
            "sky",
            "pool",
            "water",
            "waves",
            "building",
            "tower",
            "house",
            "vase",
            "laptop",
            "switch",
            "board",
            "poster",
            "bamboo",
        }
    ):
        return "background"
    if clean_tokens.intersection(
        {
            "girl",
            "boy",
            "man",
            "woman",
            "bride",
            "alien",
            "zombie",
            "knight",
            "husband",
            "wife",
        }
    ):
        return "character"
    return "meta"


def categorize_tag(tag: str) -> str:
    """
    Categorize a single tag into a semantic category.

    Returns one of: character, artist, outfit, pose, body, expression,
    background, action, style, quality, meta, rating, angle, unknown
    """
    tag_lower = tag.lower().replace(" ", "_")
    clean_tag = tag_lower.replace("_(", "(").replace(")_", ")")
    clean_tokens = {
        token.strip("()[]{}'\"")
        for token in re.split(r"[_\-\s]+", tag_lower)
        if token and token.strip("()[]{}'\"")
    }

    # Exact set lookups first (fast)
    if tag_lower in RATING_TAGS:
        return "rating"
    # Danbooru colon metatags survive the space->underscore normalization with the
    # colon intact, so the set lookups above miss them. They are unambiguous:
    # "rating:safe"/"rating:explicit" are ratings; "score:8" and Pony-style
    # "score_9"/"score_8_up" are quality boosters. (Can't misclassify: a real tag
    # like "scoreboard" has no underscore/colon right after "score".)
    if tag_lower.startswith("rating:"):
        return "rating"
    if tag_lower.startswith("score:") or tag_lower.startswith("score_"):
        return "quality"
    if tag_lower in QUALITY_TAGS:
        return "quality"
    if tag_lower in SUBJECT_COUNT_TAGS or re.match(
        r"^\d+\+?(girls|boys|others)$", tag_lower
    ):
        return "character"
    if tag_lower.startswith("multiple_") and clean_tokens.intersection(
        {"girls", "boys", "others"}
    ):
        return "character"
    if tag_lower in META_TAGS:
        return "meta"
    if tag_lower in EXPRESSION_TAGS:
        return "expression"
    if tag_lower in POSE_TAGS:
        return "pose"
    if tag_lower in ANGLE_TAGS:
        return "angle"
    if tag_lower in BODY_TAGS:
        return "body"
    if tag_lower in ACTION_TAGS:
        return "action"
    if tag_lower in BACKGROUND_TAGS:
        return "background"
    if tag_lower in STYLE_TAGS:
        return "style"

    # Artist detection (prompt conventions: SDXL "artist:name" /
    # "(artist_name:weight)", and Anima-style "@name" style triggers).
    if tag_lower.startswith("artist:") or tag_lower.startswith("artist_"):
        return "artist"
    if tag_lower.startswith("@") and len(tag_lower) > 1:
        return "artist"

    booru_categories = _load_booru_tag_categories()
    booru_category = booru_categories.get(tag_lower)
    if booru_category == "rating":
        return "rating"
    if booru_category == "artist":
        return "artist"
    if booru_category in {"character", "copyright"}:
        return "character"
    if booru_category == "meta":
        return "meta"

    # WD14-based character detection (loaded from selected_tags.csv) — checked
    # early because substring-based keyword matching below can false-positive
    # on character names (e.g. "hat" matching "hatsune_miku").
    wd14_chars = _load_wd14_character_tags()
    if wd14_chars and tag_lower in wd14_chars:
        return "character"

    storyaura_chars = _load_storyaura_character_tags()
    if storyaura_chars and tag_lower in storyaura_chars:
        return "character"

    # Franchise-suffix heuristic: name_(franchise) → character
    if _FRANCHISE_PATTERN.match(clean_tag):
        return "character"

    paren_match = re.search(r"\(([^)]+)\)$", tag_lower)
    if paren_match:
        franchise = paren_match.group(1).replace(" ", "_")
        if franchise in _FRANCHISE_SUFFIXES:
            return "character"

    if (
        re.match(r"^(year|era)_\d{4}$", tag_lower)
        or re.fullmatch(r"(?:18|19|20)\d{2}", tag_lower)
        or clean_tokens.intersection(
            {"year", "version", "resolution", "filesize", "ratio"}
        )
    ):
        return "meta"

    # Only "_shot" (cowboy_shot, wide_shot) is an angle signal; a bare "shot"
    # suffix misrouted anime_screenshot/screenshot here (v3.3.4 regression).
    if (
        tag_lower.endswith("_focus")
        or tag_lower.endswith("_view")
        or tag_lower.endswith("_shot")
        or tag_lower.startswith(("from_", "pov_"))
    ):
        return "angle"
    if tag_lower in {"straight-on", "straight_on"}:
        return "angle"

    if tag_lower.endswith("_background") or tag_lower.endswith(" background"):
        return "background"

    if clean_tokens.intersection(META_STRUCTURE_TOKENS) or re.fullmatch(
        r"\d+koma", tag_lower
    ):
        return "meta"

    if (
        tag_lower.endswith("_style")
        or tag_lower.endswith("style")
        or "lineart" in tag_lower
        or "render" in tag_lower
    ):
        return "style"
    if (
        clean_tokens.intersection(STYLE_DETAIL_TOKENS)
        or tag_lower.endswith("_(style)")
        or tag_lower.endswith("_(medium)")
        or tag_lower.endswith(
            "_coloring"
        )  # anime_coloring, western_coloring → rendering style
        or "style_parody" in tag_lower  # "drawn in another artist's style"
    ):
        return "style"
    if tag_lower in {"?", "..."} or re.fullmatch(
        r":[a-z<>()]+|[\W_]+|[\^;:xdoop_<>\-]+", tag_lower
    ):
        return "expression"

    if clean_tokens.intersection(
        {
            "smile",
            "blush",
            "wink",
            "grin",
            "laughing",
            "crying",
            "expressionless",
            "seductive",
            "embarrassed",
            "surprised",
            "mouth",
            "looking",
        }
    ):
        return "expression"
    if clean_tokens.intersection(EXPRESSION_DETAIL_TOKENS):
        return "expression"

    if clean_tokens.intersection(
        {
            "standing",
            "sitting",
            "kneeling",
            "lying",
            "leaning",
            "pose",
            "stretching",
            "jumping",
            "walking",
            "running",
            "hugging",
            "dancing",
            "squatting",
            "crouching",
            "floating",
            "tilt",
            "bent",
            "crossed",
        }
    ):
        return "pose"
    if (
        tag_lower.startswith(
            ("hand_on_", "hands_on_", "own_hands_", "clenched_hand", "facing_")
        )
        or tag_lower.endswith(("_pull", "_raised"))
        or tag_lower == "v"
        or tag_lower.endswith("_v")
        or clean_tokens.intersection(
            {
                "hands",
                "hand",
                "arms",
                "arm",
                "legs",
                "leg",
                "knees",
                "knee",
                "fingers",
                "finger",
            }
        )
        and clean_tokens.intersection(
            {
                "up",
                "together",
                "outstretched",
                "clenched",
                "bent",
                "crossed",
                "apart",
                "side",
                "behind",
                "back",
                "raised",
                "interlocked",
                "spread",
                "v",
            }
        )
    ):
        return "pose"
    if tag_lower == "top-down_bottom-up":
        return "pose"
    if clean_tokens.intersection(POSE_DETAIL_TOKENS) and clean_tokens.intersection(
        {
            "arms",
            "arm",
            "hands",
            "hand",
            "legs",
            "leg",
            "head",
            "viewer",
            "glance",
            "floor",
            "contrapposto",
            "wielding",
            "wield",
        }
    ):
        return "pose"

    if clean_tokens.intersection(
        {
            "outdoors",
            "indoors",
            "beach",
            "ocean",
            "sea",
            "sky",
            "forest",
            "night",
            "day",
            "sunset",
            "sunrise",
            "room",
            "bedroom",
            "bathroom",
            "classroom",
            "city",
            "street",
            "park",
            "garden",
            "field",
            "building",
            "bush",
            "leaf",
            "leaves",
            "tree",
            "road",
            "wall",
            "scenery",
            "nature",
            "blossoms",
            "crescent",
        }
    ):
        return "background"

    if clean_tokens.intersection(
        {
            "hair",
            "eyes",
            "breasts",
            "chest",
            "thighs",
            "legs",
            "skin",
            "ears",
            "tail",
            "tails",
            "wings",
            "horns",
            "horn",
            "halo",
            "fangs",
            "teeth",
            "navel",
            "belly",
            "feet",
            "armpits",
            "eyebrows",
            "pupils",
            "sclera",
            "pectorals",
            "mark",
            "nose",
            "claws",
            "feathers",
            "beard",
            "curvy",
            "bangs",
            "torso",
            "tentacles",
            "toned",
        }
    ):
        return "body"
    if clean_tokens.intersection(BODY_DETAIL_TOKENS):
        return "body"
    if tag_lower.endswith(
        ("_twintails", "_drills", "_tail", "_tails", "_eye", "_eyes", "_sclera")
    ):
        return "body"

    if clean_tokens.intersection(
        {
            "holding",
            "grabbing",
            "touching",
            "kissing",
            "licking",
            "biting",
            "reading",
            "writing",
            "drinking",
            "eating",
            "swimming",
            "kiss",
        }
    ):
        return "action"
    if clean_tokens.intersection(ACTION_DETAIL_TOKENS):
        return "action"

    if any(keyword in tag_lower for keyword in NSFW_BODY_KEYWORDS):
        return "body"

    if any(keyword in tag_lower for keyword in OUTFIT_DETAIL_KEYWORDS):
        return "outfit"
    if clean_tokens.intersection(OUTFIT_DETAIL_TOKENS):
        return "outfit"
    if tag_lower.endswith(("_print", "_trim")):
        return "outfit"

    # Generic-object background classification (v3.3.4 regression fix): these
    # sets hold generic nouns (tank, pencil, key, shell, egg, moon, winter,
    # summer, ...), so they must run AFTER the body/action/outfit checks above
    # and must not fire on garment compounds (tank_top, pencil_skirt,
    # winter_coat) — those fall through to the OUTFIT_KEYWORDS loop below.
    has_garment_signal = any(
        keyword in tag_lower for keyword in _GARMENT_VETO_KEYWORDS
    ) or clean_tokens.intersection(_GARMENT_VETO_TOKENS)
    if not has_garment_signal:
        if clean_tokens.intersection(BACKGROUND_DETAIL_TOKENS):
            return "background"
        if any(keyword in tag_lower for keyword in BACKGROUND_OBJECT_KEYWORDS):
            return "background"

    if any(keyword in tag_lower for keyword in ACTION_DETAIL_KEYWORDS):
        return "action"

    if any(keyword in tag_lower for keyword in EXPRESSION_DETAIL_KEYWORDS):
        return "expression"

    if any(keyword in tag_lower for keyword in META_DETAIL_KEYWORDS):
        return "meta"

    if any(keyword in tag_lower for keyword in CHARACTER_DETAIL_KEYWORDS):
        return "character"
    if (
        clean_tokens.intersection(CHARACTER_ENTITY_TOKENS)
        or tag_lower in {"aged_down", "aged_up", "genderswap"}
        or tag_lower.startswith("genderswap")
        or re.search(r"(girl|boy|woman|man|male|female)s?$", tag_lower)
    ):
        return "character"

    if paren_match and clean_tokens.intersection({"creature", "character", "pokemon"}):
        return "character"

    # Outfit detection via keyword matching
    for keyword in OUTFIT_KEYWORDS:
        if keyword in tag_lower:
            return "outfit"

    # Hair/eye color tags that might not be in the BODY set
    if "_hair" in tag_lower or "_eyes" in tag_lower:
        return "body"

    # Hairstyle and body-detail patterns
    if any(keyword in tag_lower for keyword in HAIRSTYLE_KEYWORDS):
        return "body"

    # Object/prop keywords → background
    if any(keyword in tag_lower for keyword in OBJECT_PROP_KEYWORDS):
        return "background"

    # Effect/rendering keywords → style
    if any(keyword in tag_lower for keyword in EFFECT_KEYWORDS):
        return "style"

    # Meta heuristic: tags about image structure/annotations
    if (
        tag_lower.startswith("no_")
        or tag_lower.startswith("non-")
        or tag_lower.endswith("_request")
    ):
        return "meta"

    if booru_category == "general":
        return _fallback_known_booru_general_category(tag_lower, clean_tokens)

    return "unknown"


def categorize_tags_batch(tags: List[str]) -> Dict[str, str]:
    """Categorize multiple tags at once. Returns {tag: category}."""
    return {tag: categorize_tag(tag) for tag in tags}


def get_exclusion_targets(active_tags: Set[str], rules: List[dict]) -> Set[str]:
    """
    Given a set of active tags and exclusion rules,
    return the set of tags that should be excluded.
    """
    excluded = set()

    for rule in rules:
        conditions = rule.get("conditions", [])
        targets = rule.get("targets", [])

        # Check if all conditions are met
        conditions_met = True
        for cond in conditions:
            cond_tag = cond["tag"].lower().replace(" ", "_")
            cond_type = cond.get("type", "present")

            tag_present = any(
                cond_tag in t.lower().replace(" ", "_") for t in active_tags
            )

            if cond_type == "present" and not tag_present:
                conditions_met = False
                break
            elif cond_type == "absent" and tag_present:
                conditions_met = False
                break

        if conditions_met:
            for target in targets:
                if "tag" in target and target["tag"]:
                    excluded.add(target["tag"].lower().replace(" ", "_"))
                if "category" in target and target.get("category"):
                    # Category-level exclusion would need the categorize_tag function
                    # For now, individual tag targets are sufficient
                    pass

    return excluded
