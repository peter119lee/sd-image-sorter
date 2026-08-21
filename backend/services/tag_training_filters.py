"""Training-oriented tag filters shared by the export engine and Smart Tag.

Closes two v3.5.0 tagger-audit features (owner-approved 2026-07-07):

- P2-19 — purpose-aware filtering in the EXPORT engine. Smart Tag already
  filtered by training purpose at tagging time; a user who plain-tags and then
  exports got no purpose semantics. The row-level filter here is applied by
  ``build_sidecar_content`` so every content mode (including template)
  inherits it, and the preview endpoint stays WYSIWYG with the export.
- P2-18 — implication dedup. A curated danbooru implication table plus
  StoryAura parent_tag edges collapse redundant parents (``cat_ears``
  present → drop ``animal_ears``) behind an explicit toggle.
  ``data/danbooru_implications.csv`` is a drop-in extension point, same
  convention as ``data/danbooru_zh.csv``.

Both filters are opt-in — silent tag removal violates the owner's
no-silent-limits principle.
"""

from __future__ import annotations

import re

import csv
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tag_rules import categorize_tag

logger = logging.getLogger(__name__)

# Canonical purpose keys — moved here from smart_tag_service (which now
# imports these) so the tagging pipeline and the export engine share ONE
# vocabulary and cannot drift. nsfw is a routing-only alias of general.
TRAINING_PURPOSE_ALIASES: Dict[str, str] = {
    "style": "style",
    "style_lora": "style",
    "art": "style",
    "art_style": "style",
    "artist_style": "style",
    "character": "character",
    "character_lora": "character",
    "char": "character",
    "general": "general",
    "concept": "concept",
    "concept_lora": "concept",
    "nsfw": "general",  # Same prompt, flagged differently in routing
    "nsfw_lora": "general",
}


def normalize_training_purpose(value: Optional[str]) -> str:
    """Map a user-provided training purpose to a canonical preset key."""
    if not value:
        return "general"
    key = str(value).strip().lower().replace("-", "_")
    return TRAINING_PURPOSE_ALIASES.get(key, "general")


def _effective_category(row: Dict[str, Any]) -> str:
    stored = str(row.get("category") or "").strip().lower()
    if stored:
        return stored
    # Legacy rows predate migration 024 and carry no category — classify on
    # the fly with the same rules the migration used.
    return categorize_tag(str(row.get("tag") or ""))


# Categories that name the *style itself* (an art style, medium, era, or an
# artist) — what a style LoRA's trigger must absorb, so they are stripped.
_STYLE_FAMILY = {"style", "artist"}


def _is_style_family_row(row: Dict[str, Any]) -> bool:
    """True when a row names the style itself and so must be dropped for a style LoRA.

    Why not a hardcoded word list (the thing this replaces): a blacklist can never
    enumerate every style/era/medium tag. But boorus/WD14 dump these descriptors —
    ``1990s_(style)``, ``retro_artstyle``, ``anime_coloring``, ``*_(medium)`` — into
    the coarse ``general`` bucket, so the *stored* category alone misses them. The
    pattern-based ``categorize_tag`` recognises them by SHAPE (``*_(style)`` /
    ``*_(medium)`` / ``*style`` / ``*_coloring`` / ``@artist`` / the STYLE set), which
    generalises to tags no list would contain. We only let the classifier *upgrade* a
    ``general``/empty row — never override a real character/copyright/meta call — so a
    count like ``1girl`` (semantically "character") or a body tag is left untouched.
    """
    stored = str(row.get("category") or "").strip().lower()
    if stored in _STYLE_FAMILY:
        return True
    if stored in ("", "general"):
        return categorize_tag(str(row.get("tag") or "")) in _STYLE_FAMILY
    return False


def filter_tag_rows_by_training_purpose(
    rows: List[Dict[str, Any]],
    training_purpose: str,
    trigger_word: str = "",
) -> List[Dict[str, Any]]:
    """Row-level twin of Smart Tag's ``filter_tags_by_training_purpose``.

    Style mode removes style/medium/era/artist tags — detected by category AND by
    the semantic classifier's shape rules, not a word list — so the target style is
    never named in a caption and the trigger carries it. Character mode removes
    detected character names only when a trigger word is present to carry that
    identity. General/concept pass everything through — the app cannot guess which
    tag is the user's target.
    """
    canonical = normalize_training_purpose(training_purpose)

    if canonical == "style":
        return [row for row in rows if not _is_style_family_row(row)]
    if canonical == "character" and str(trigger_word or "").strip():
        return [row for row in rows if _effective_category(row) != "character"]
    return list(rows)


# ---------------------------------------------------------------------------
# Implication dedup (P2-18)
# ---------------------------------------------------------------------------

_BUNDLED_IMPLICATIONS = (
    Path(__file__).resolve().parents[1] / "assets" / "danbooru_implications.csv"
)
_BUNDLED_IMPLICATIONS_EXT = (
    Path(__file__).resolve().parents[1] / "assets" / "danbooru_implications_ext.csv"
)
_implication_lock = threading.Lock()
_implication_cache: Optional[Dict[str, set]] = None


def _implication_key(tag: str) -> str:
    return " ".join(str(tag or "").replace("_", " ").split()).strip().lower()


def _dropin_implications_path() -> Path:
    from config import DATA_DIR

    return Path(DATA_DIR) / "danbooru_implications.csv"


def _read_implication_csv(path: Path, table: Dict[str, set]) -> int:
    added = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for record in csv.reader(handle):
                if len(record) < 2:
                    continue
                child = _implication_key(record[0])
                parent = _implication_key(record[1])
                if not child or not parent or child == parent or child.startswith("#"):
                    continue
                table.setdefault(child, set()).add(parent)
                added += 1
    except FileNotFoundError:
        return 0
    except Exception as exc:
        logger.warning("Failed reading implication table %s: %s", path, exc)
        return 0
    return added


def _implication_table() -> Dict[str, set]:
    global _implication_cache
    if _implication_cache is not None:
        return _implication_cache
    with _implication_lock:
        if _implication_cache is not None:
            return _implication_cache
        table: Dict[str, set] = {}
        bundled = _read_implication_csv(_BUNDLED_IMPLICATIONS, table)
        bundled_ext = _read_implication_csv(_BUNDLED_IMPLICATIONS_EXT, table)
        dropin = _read_implication_csv(_dropin_implications_path(), table)
        if dropin:
            logger.info("Loaded %d drop-in tag implications from data/", dropin)
        logger.debug(
            "Implication table ready: %d bundled + %d ext + %d drop-in",
            bundled,
            bundled_ext,
            dropin,
        )
        _implication_cache = table
        return table


def reset_implication_cache_for_tests() -> None:
    global _implication_cache
    with _implication_lock:
        _implication_cache = None


def _transitive_parents(child_key: str, table: Dict[str, set]) -> set:
    seen: set = set()
    frontier = list(table.get(child_key, ()))
    while frontier:
        parent = frontier.pop()
        if parent in seen:
            continue
        seen.add(parent)
        frontier.extend(table.get(parent, ()))
    return seen


def collapse_implications(tags: Sequence[str]) -> List[str]:
    """Drop tags implied by a more specific tag that is also present.

    ``cat_ears`` + ``animal_ears`` → keep ``cat_ears`` only. Transitive:
    ``school_swimsuit`` present drops both ``one-piece_swimsuit`` and
    ``swimsuit``. Order and original spelling of the kept tags are preserved.
    """
    table = _implication_table()
    if not table:
        return list(tags)

    present = {_implication_key(tag) for tag in tags if str(tag or "").strip()}
    implied: set = set()
    for key in present:
        implied.update(_transitive_parents(key, table))

    return [tag for tag in tags if _implication_key(tag) not in implied]


def collapse_implication_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = _implication_table()
    if not table:
        return list(rows)

    present = {_implication_key(str(row.get("tag") or "")) for row in rows}
    implied: set = set()
    for key in present:
        implied.update(_transitive_parents(key, table))

    return [
        row
        for row in rows
        if _implication_key(str(row.get("tag") or "")) not in implied
    ]


def apply_training_filters(
    rows: List[Dict[str, Any]],
    *,
    training_purpose: str = "",
    trigger_word: str = "",
    dedupe_implications: bool = False,
) -> List[Dict[str, Any]]:
    """Single seam used by ``build_sidecar_content`` — purpose first (category
    semantics), then implication collapse on what remains."""
    result = rows
    if str(training_purpose or "").strip():
        result = filter_tag_rows_by_training_purpose(
            result, training_purpose, trigger_word
        )
    if dedupe_implications:
        result = collapse_implication_rows(result)
    return result if result is not rows else list(rows)


# ---------------------------------------------------------------------------
# SEP-2: NL leak control.
#
# The blacklist / trait pruner strips tag ROWS so intrinsic character
# features get absorbed by the trigger word — but a VLM prose sentence can
# re-introduce the same feature ("a girl with silver hair" right after
# silver_hair was pruned), silently undoing the separation. Two shared
# helpers close that hole from both ends:
#   * format_trait_suppression_block -> injected into VLM prompts so the
#     model is told THIS dataset's suppressed features up front.
#   * scan_text_for_blacklisted_terms -> post-hoc scan of the final rendered
#     caption so the export preview can flag any term that leaked through.
# ---------------------------------------------------------------------------


def format_trait_suppression_block(traits) -> str:
    """Render the dataset-specific "never mention these" VLM prompt block."""
    phrases = []
    seen = set()
    for trait in traits or []:
        phrase = str(trait or "").strip().lower().replace("_", " ")
        if phrase and phrase not in seen:
            seen.add(phrase)
            phrases.append(phrase)
    if not phrases:
        return ""
    listed = ", ".join(phrases[:80])
    return (
        "Additionally, NEVER mention any of these features in your sentences "
        "- they are fixed identity carried by the trained trigger word: "
        f"{listed}."
    )


def scan_text_for_blacklisted_terms(text: str, blacklist) -> List[str]:
    """Return blacklisted terms that still appear in rendered caption text.

    Word-bounded, case-insensitive; underscore/space variants fold together
    so the tag spelling ``silver_hair`` matches the prose "silver hair".
    """
    if not text or not blacklist:
        return []
    lowered = str(text).lower()
    leaks: List[str] = []
    seen = set()
    for term in blacklist:
        base = str(term or "").strip().lower()
        if not base or base in seen:
            continue
        seen.add(base)
        phrase = base.replace("_", " ").strip()
        if not phrase:
            continue
        pattern = r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"[\s_]+") + r"(?!\w)"
        if re.search(pattern, lowered):
            leaks.append(base)
    return leaks
