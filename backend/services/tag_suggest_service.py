"""Unified tag suggestion service (v3.5.0).

Backs ``GET /api/tags/suggest`` — the shared type-ahead source for every
tag-typing surface (Dataset Maker caption editor, image-detail tag editor,
mass tag editor, caption-editor export preview).

Merges up to three sources per query:

1. **Library tags** — the user's own ``tags`` table, frequency-ranked.
   Always available; the only source when no vocabulary file is present.
2. **Bundled danbooru vocabulary** — ``backend/assets/danbooru_tags.csv``
   (MIT-licensed export from DominikDoom/a1111-sd-webui-tagcomplete):
   ~140k tags sorted by post count, with danbooru category codes and
   alias lists. Aliases participate in matching (typing "boobs" suggests
   "breasts").
3. **Chinese / Japanese aliases** — bundled MIT table derived from
   StoryAura/Danbooru-Dataset-csv (``backend/assets/danbooru_zh.csv``).
   A user drop-in at ``<DATA_DIR>/danbooru_zh.csv`` still wins, so
   upgrades never overwrite a personal translation file. Accepted shape:
   CSV where column 1 is the tag and column 2 a comma-joined list of CJK
   aliases (header auto-detected). CJK queries fuzzy-match those aliases
   and suggestions carry a ``zh`` display string.

The vocabulary loads lazily on first use (~0.5 s for 140k rows) and is
cached for the process lifetime. Matching is a linear scan over the
count-sorted rows with early exit, so buckets come out popularity-ranked
for free. Popular character tags missing from the 140k dump are merged
from the StoryAura character catalog so CJK hits like 初音未来 resolve.
"""

import csv
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Rows: (tag, count, category_code, match_blob) — match_blob is the
# lowercase tag plus aliases joined by commas, scanned with substring
# matching. Queries never contain commas (commas end the token in every
# attached input), so matches cannot straddle alias boundaries.
_VocabRow = Tuple[str, int, int, str]

_LOCK = threading.Lock()
_VOCAB: Optional[List[_VocabRow]] = None
_ZH_DISPLAY: Optional[Dict[str, str]] = None  # tag -> first zh alias
_ZH_BLOBS: Optional[List[Tuple[str, str, int, int]]] = None  # zh_lower, tag, count, code
_VOCAB_INDEX: Optional[Dict[str, int]] = None  # tag -> vocab list index

_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")

# Mirrors the mapping categorize_tag() applies to booru category names.
_BOORU_CODE_TO_APP_CATEGORY = {
    1: "artist",
    3: "character",  # copyright — closest app bucket, same as categorize_tag
    4: "character",
    5: "meta",
    9: "rating",
}

MAX_LIMIT = 50
DEFAULT_LIMIT = 20


def _danbooru_csv_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "danbooru_tags.csv"


def _zh_csv_paths() -> List[Path]:
    paths: List[Path] = []
    try:
        import config

        paths.append(Path(config.DATA_DIR) / "danbooru_zh.csv")
    except Exception:  # pragma: no cover - config always importable in app
        pass
    paths.append(Path(__file__).resolve().parent.parent / "assets" / "danbooru_zh.csv")
    return paths


def reset_cache() -> None:
    """Testing hook: drop the loaded vocabulary so paths can be re-resolved."""
    global _VOCAB, _ZH_DISPLAY, _ZH_BLOBS, _VOCAB_INDEX
    with _LOCK:
        _VOCAB = None
        _ZH_DISPLAY = None
        _ZH_BLOBS = None
        _VOCAB_INDEX = None


def _normalize_tag(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def _load_danbooru_vocab() -> List[_VocabRow]:
    path = _danbooru_csv_path()
    rows: List[_VocabRow] = []
    if not path.is_file():
        logger.info("Danbooru vocabulary not found at %s; suggest falls back to library tags only", path)
        return rows
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for parts in csv.reader(f):
                if len(parts) < 3:
                    continue
                tag = _normalize_tag(parts[0])
                if not tag:
                    continue
                try:
                    code = int(parts[1])
                except (TypeError, ValueError):
                    code = 0
                try:
                    count = int(parts[2])
                except (TypeError, ValueError):
                    count = 0
                aliases = parts[3].strip().lower() if len(parts) > 3 else ""
                blob = tag if not aliases else f"{tag},{aliases}"
                rows.append((tag, count, code, blob))
    except Exception as exc:
        logger.warning("Failed to load danbooru vocabulary from %s: %s", path, exc)
        return []
    logger.info("Loaded %d danbooru vocabulary tags from %s", len(rows), path)
    return rows


def _zh_count_and_code(tag: str, vocab: List[_VocabRow], vocab_index: Dict[str, int]) -> Tuple[int, int]:
    idx = vocab_index.get(tag)
    if idx is not None:
        _name, count, code, _blob = vocab[idx]
        return int(count), int(code)
    try:
        from danbooru_catalog import get_character

        character = get_character(tag)
    except Exception:
        character = None
    if character:
        return int(character.get("post_count") or 0), 4
    return 0, 0


def _load_zh_translations(
    vocab: List[_VocabRow], vocab_index: Dict[str, int]
) -> Tuple[Dict[str, str], List[Tuple[str, str, int, int]]]:
    display: Dict[str, str] = {}
    blobs: List[Tuple[str, str, int, int]] = []
    for path in _zh_csv_paths():
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                # Header-less two-column files are fine too: treat a first
                # row whose second cell contains CJK as data, not header.
                if header and len(header) >= 2 and _CJK_RE.search(header[1] or ""):
                    f.seek(0)
                    reader = csv.reader(f)
                for parts in reader:
                    if len(parts) < 2:
                        continue
                    tag = _normalize_tag(parts[0])
                    zh_all = (parts[1] or "").strip()
                    if not tag or not zh_all:
                        continue
                    display[tag] = zh_all.split(",")[0].strip() or zh_all
                    count, code = _zh_count_and_code(tag, vocab, vocab_index)
                    blobs.append((zh_all.lower(), tag, count, code))
        except Exception as exc:
            logger.warning("Failed to load zh tag translations from %s: %s", path, exc)
            continue
        blobs.sort(key=lambda item: item[2], reverse=True)
        logger.info("Loaded %d zh tag translations from %s", len(display), path)
        break
    return display, blobs


def _ensure_loaded() -> None:
    global _VOCAB, _ZH_DISPLAY, _ZH_BLOBS, _VOCAB_INDEX
    if _VOCAB is not None:
        return
    with _LOCK:
        if _VOCAB is not None:
            return
        vocab = _load_danbooru_vocab()
        index = {row[0]: i for i, row in enumerate(vocab)}
        extras = []
        if vocab:
            try:
                from danbooru_catalog import extra_vocab_rows

                extras = extra_vocab_rows(set(index))
            except Exception as exc:
                logger.debug("character catalog extras unavailable: %s", exc)
                extras = []
        if extras:
            vocab = list(vocab)
            vocab.extend(extras)
            vocab.sort(key=lambda row: row[1], reverse=True)
            index = {row[0]: i for i, row in enumerate(vocab)}
            logger.info("Merged %d extra StoryAura character tags into vocabulary", len(extras))
        display, blobs = _load_zh_translations(vocab, index)
        # Publish fully-built structures last so readers never see partials.
        _VOCAB_INDEX = index
        _ZH_DISPLAY = display
        _ZH_BLOBS = blobs
        _VOCAB = vocab


def get_vocab_tag_index() -> Optional[Dict[str, int]]:
    """Expose the loaded tag→index map for membership tests.

    Used by prompt_text_scorer to judge whether an arbitrary string from a
    ComfyUI graph "reads like a prompt" (v3.5.0 metadata L2 fallback).
    Returns None when the bundled vocabulary is unavailable — callers must
    fail open to structure-only heuristics.
    """
    try:
        _ensure_loaded()
    except Exception as exc:  # pragma: no cover - defensive: parser must not die
        logger.warning("danbooru vocab unavailable for prompt scoring: %s", exc)
        return None
    return _VOCAB_INDEX


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query_library_tags(q_norm: str, limit: int) -> List[Tuple[str, int]]:
    """Frequency-ranked tags from the user's library matching the query."""
    import database

    sql = (
        "SELECT tag, COUNT(*) AS cnt FROM tags "
        "GROUP BY tag "
        "ORDER BY cnt DESC, tag ASC"
    )
    params: Tuple[Any, ...] = ()
    if q_norm:
        sql = (
            "SELECT tag, COUNT(*) AS cnt FROM tags "
            "WHERE REPLACE(LOWER(tag), ' ', '_') LIKE ? ESCAPE '\\' "
            "GROUP BY tag "
            "ORDER BY cnt DESC, tag ASC"
        )
        params = (f"%{_escape_like(q_norm)}%",)
    try:
        conn = database.get_connection()
        try:
            cur = conn.execute(sql + " LIMIT ?", params + (limit,))
            return [(str(row[0]), int(row[1])) for row in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Library tag lookup failed for suggest: %s", exc)
        return []


def _category_for(tag: str, code: int = 0) -> str:
    mapped = _BOORU_CODE_TO_APP_CATEGORY.get(code)
    if mapped:
        return mapped
    try:
        from tag_rules import categorize_tag

        return categorize_tag(tag)
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _entry(tag: str, count: int, source: str, code: int = 0) -> Dict[str, Any]:
    zh = (_ZH_DISPLAY or {}).get(tag)
    return {
        "tag": tag,
        "count": count,
        "source": source,
        "category": _category_for(tag, code),
        "zh": zh,
    }


def _scan_danbooru(q_norm: str, limit: int, seen: set) -> List[Dict[str, Any]]:
    """Popularity-ranked exact/prefix/contains buckets from the vocabulary."""
    vocab = _VOCAB or []
    exact: List[Dict[str, Any]] = []
    exact_idx = (_VOCAB_INDEX or {}).get(q_norm)
    if exact_idx is not None and q_norm not in seen:
        tag, count, code, _ = vocab[exact_idx]
        exact.append(_entry(tag, count, "danbooru", code))
    prefix: List[Dict[str, Any]] = []
    contains: List[Dict[str, Any]] = []
    for tag, count, code, blob in vocab:
        if tag in seen or tag == q_norm:
            continue
        if blob.startswith(q_norm) or f",{q_norm}" in blob:
            # startswith on the blob == prefix of the canonical tag; an
            # alias hit right after a comma also reads as a strong match.
            if len(prefix) < limit:
                prefix.append(_entry(tag, count, "danbooru", code))
        elif q_norm in blob:
            if len(contains) < limit:
                contains.append(_entry(tag, count, "danbooru", code))
        if len(prefix) >= limit and len(contains) >= limit:
            break
    return exact + prefix + contains


def _scan_zh(q_lower: str, limit: int, seen: set) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for zh_blob, tag, count, code in _ZH_BLOBS or []:
        if q_lower in zh_blob:
            if tag in seen:
                continue
            seen.add(tag)
            out.append(_entry(tag, count, "danbooru", code))
            if len(out) >= limit:
                break
    return out


def get_tag_info(tag: str) -> Dict[str, Any]:
    """Everything the app knows about ONE tag — the learn-while-tagging
    popover (competitive roadmap #6; Persona C): canonical category,
    danbooru popularity + aliases + zh display from the bundled vocab
    (alias hits, including CJK, resolve to their canonical tag),
    character copyright/parent from the StoryAura catalog, implication
    edges both ways from the bundled/drop-in table, and the live library
    count. Read-only; unknown tags still return the category heuristic +
    library count so hand-rolled tags are not a dead end."""
    _ensure_loaded()
    q_norm = _normalize_tag(tag)
    info: Dict[str, Any] = {
        "tag": q_norm,
        "canonical": q_norm,
        "found_in_vocab": False,
        "category": None,
        "danbooru_count": 0,
        "aliases": [],
        "zh": None,
        "copyright": None,
        "parent_tag": None,
        "implies": [],
        "implied_by": [],
        "library_count": 0,
    }
    if not q_norm:
        return info

    vocab = _VOCAB or []
    index = _VOCAB_INDEX or {}
    idx = index.get(q_norm)
    if idx is None:
        # Alias hit: comma-boundary scan over the blobs (tiny cost, one-off).
        needle = f",{q_norm}"
        for row_idx, (_tag, _count, _code, blob) in enumerate(vocab):
            if blob.endswith(needle) or f"{needle}," in blob:
                idx = row_idx
                break
    canonical = q_norm
    code = 0
    if idx is None:
        q_lower = q_norm.lower()
        padded = f",{q_lower},"
        for zh_blob, zh_tag, _count, _code in _ZH_BLOBS or []:
            if padded in f",{zh_blob},":
                idx = index.get(zh_tag)
                canonical = zh_tag
                break
    if idx is not None:
        canonical, count, code, blob = vocab[idx]
        info["canonical"] = canonical
        info["found_in_vocab"] = True
        info["danbooru_count"] = int(count)
        parts = blob.split(",")
        info["aliases"] = [alias for alias in parts[1:] if alias]
        info["zh"] = (_ZH_DISPLAY or {}).get(canonical)
    elif canonical != q_norm:
        info["canonical"] = canonical
        info["found_in_vocab"] = True
        info["zh"] = (_ZH_DISPLAY or {}).get(canonical)
        count, code = _zh_count_and_code(canonical, vocab, index)
        info["danbooru_count"] = int(count)
    info["category"] = _category_for(canonical, code)

    try:
        from danbooru_catalog import get_character

        character = get_character(canonical)
    except Exception:
        character = None
    if character:
        copyright_ = str(character.get("copyright") or "").strip()
        parent = str(character.get("parent_tag") or "").strip()
        info["copyright"] = copyright_ or None
        info["parent_tag"] = parent or None
        if not info["found_in_vocab"]:
            info["found_in_vocab"] = True
            info["canonical"] = canonical
            info["danbooru_count"] = int(character.get("post_count") or 0)
            info["category"] = "character"
            info["zh"] = info["zh"] or (_ZH_DISPLAY or {}).get(canonical)

    zh_display = info["zh"] or (_ZH_DISPLAY or {}).get(canonical)
    info["zh"] = zh_display
    if zh_display and zh_display not in info["aliases"]:
        info["aliases"] = list(info["aliases"]) + [zh_display]

    try:
        from services.tag_training_filters import _implication_table

        table = _implication_table()
        # _implication_key folds underscores to spaces — match that form.
        key = canonical.replace("_", " ")
        # Table values are space-folded; the API speaks underscore form
        # like every other tag surface in the app.
        info["implies"] = sorted(p.replace(" ", "_") for p in table.get(key, set()))
        info["implied_by"] = sorted(
            child.replace(" ", "_")
            for child, parents in table.items()
            if key in parents
        )
    except Exception:  # pragma: no cover - popover data is best-effort
        logger.debug("implication lookup failed for %s", canonical, exc_info=True)

    try:
        import database as db

        counts = {row["tag"].lower().replace("_", " "): row["count"] for row in db.get_all_tags()}
        info["library_count"] = int(
            counts.get(canonical) or counts.get(q_norm) or 0
        )
    except Exception:  # pragma: no cover
        logger.debug("library count lookup failed for %s", canonical, exc_info=True)
    return info


def suggest(q: str = "", limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Return merged, ranked tag suggestions for a partial token."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    _ensure_loaded()

    raw = (q or "").strip()
    q_norm = _normalize_tag(raw)

    library = _query_library_tags(q_norm, limit)
    lib_exact: List[Dict[str, Any]] = []
    lib_prefix: List[Dict[str, Any]] = []
    lib_contains: List[Dict[str, Any]] = []
    seen: set = set()
    for tag, count in library:
        tag_norm = _normalize_tag(tag)
        seen.add(tag_norm)
        item = _entry(tag_norm, count, "library")
        if not q_norm or tag_norm == q_norm:
            (lib_exact if q_norm else lib_prefix).append(item)
        elif tag_norm.startswith(q_norm):
            lib_prefix.append(item)
        else:
            lib_contains.append(item)

    dan: List[Dict[str, Any]] = []
    if q_norm:
        if _CJK_RE.search(raw):
            dan = _scan_zh(raw.lower(), limit, seen)
        else:
            dan = _scan_danbooru(q_norm, limit, seen)

    dan_exact = [d for d in dan if d["tag"] == q_norm]
    dan_rest = [d for d in dan if d["tag"] != q_norm]
    merged = lib_exact + dan_exact + lib_prefix + lib_contains + dan_rest

    return {
        "suggestions": merged[:limit],
        "danbooru_loaded": bool(_VOCAB),
        "zh_loaded": bool(_ZH_BLOBS),
    }
