"""One place that answers "is this caption text tags, prose, or both?".

Why this module exists
======================
Caption text in this app is split between columns by **provenance** — who wrote
it: ``prompt`` (the generator), ``ai_caption`` (this app's tagger),
``nl_caption`` (this app's VLM), ``sidecar_caption`` (a ``.txt``/``.json`` file
somebody else wrote). Provenance cannot be recovered from the text, so it has to
be structural.

Danbooru-tags-versus-prose is a *different* axis: **format**. It *can* be
derived from the text, so it is recorded as one small marker
(``images.sidecar_caption_format``, migration 044) rather than by splitting the
text across more columns. Mixing the two axes into the column layout is what
would make the schema messy.

Hard invariant
==============
**A format label may never be used to discard, truncate or refuse text.** Every
public function here returns a label, a bool, or ``None`` — never text. There is
deliberately no "clean the caption" helper: if one existed, a caller could store
the cleaned version instead of the original and the user's text would be edited
behind their back. When detection is wrong nothing is lost, because the original
string is still stored in full and the marker only decides how it is presented
or converted.

Where the rules came from
=========================
``looks_like_garbage_tag`` and its three tables are the segment-shape rules that
already lived in ``vlm_providers/base.py``, built from real VLM output damage
(markdown headers, LaTeX, chain-of-thought sentences leaking into a tag list).
They moved here so Smart Tag's VLM parser and the sidecar classifier share one
implementation instead of drifting apart; ``vlm_providers.base`` imports the same
objects back under their historical private names.

The whole-text thresholds below are derived from what the existing
implementations already found to work plus the owner's real library (5,242
sidecar files, read-only):

* the 60% "majority of comma segments look like tags" rule is
  ``_heuristic_split_tags_prose``'s Strategy 1 rule, unchanged;
* mean words per comma segment is 1.72 at the median and 2.24 at p99 across the
  owner's tag-list sidecars, so 2.5 separates tag lists from prose clauses with
  headroom;
* real Danbooru tags are multi-word and space-separated ("looking at viewer",
  "kamiyama high school uniform (project sekai)"), which is why a longer mean is
  still accepted when every segment is tag-shaped;
* ``?``, ``!``, ``!!``, ``^_^`` and ``@_@`` are real tags in that library, so
  sentence punctuation alone never proves prose.
"""
from __future__ import annotations

import re


# --------------------------------------------------------------------------
# Segment-shape rules (moved from vlm_providers/base.py, behaviour unchanged)
# --------------------------------------------------------------------------

MARKDOWN_PREFIXES = ("#", "*", "-", "+", ">", "•", "·", "—", "–", "·")
PROSE_SUFFIX_CHARS = frozenset({".", "!", "?", "。", "！", "？"})
FORBIDDEN_SUBSTRINGS = ("$$", "```", "://", "|", "<sub>", "<sup>")


def looks_like_garbage_tag(tag: str) -> bool:
    """Reject markdown headers, prose, LaTeX, sentence fragments and other VLM noise.

    Real-world VLM responses (especially Gemma / Qwen / GPT) regularly leak
    chain-of-thought into the danbooru-tags output: ``### 1. Address the …``,
    ``*   **Character Design:** the character has long``,
    ``$$x = \\frac{-3y \\pm \\sqrt{(3y)^2 - 4(1)(y^2 - 1)}}{2}$$``,
    ``Are you looking for information on the character`` etc. The previous
    parser only checked length 2 ≤ len ≤ 100 so all of those became tags
    and polluted the user's library. This filter rejects them by shape.
    """
    if not tag:
        return True
    if not (2 <= len(tag) <= 100):
        return True

    stripped = tag.strip()
    if not stripped:
        return True

    # Markdown markers / numbered lists / blockquotes
    if stripped[0] in MARKDOWN_PREFIXES:
        return True
    # Numbered list "1." "1)" "(1)"
    if len(stripped) >= 2 and stripped[0].isdigit():
        # match leading digits then "." or ")" then space
        idx = 0
        while idx < len(stripped) and stripped[idx].isdigit():
            idx += 1
        if idx < len(stripped) and stripped[idx] in (".", ")"):
            tail = stripped[idx + 1 :].lstrip()
            # If after the number there's prose (multiple words / capitalized
            # English sentence), reject. Pure danbooru tags don't start with
            # leading numbers like "1. solo" — and even if they did, the
            # rejection is safer than letting "1. Address the issue" through.
            if tail and (" " in tail or any(c.isupper() for c in tail[:1])):
                return True

    lowered = stripped.lower()
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in lowered:
            return True

    # Prose markers: a real danbooru tag like "long_hair, smile, 1girl" never
    # ends with sentence punctuation, never contains `: ` (colon + space) or
    # `; ` mid-string. Reject such cases.
    if stripped[-1] in PROSE_SUFFIX_CHARS:
        return True
    if ": " in stripped or "; " in stripped:
        return True
    # Multiple inner quotes usually mean prose ("Cyphotes, "blue hair, ...).
    if stripped.count('"') >= 2 or stripped.count("'") >= 3:
        return True

    # Tags rarely contain more than ~6 spaces. Multi-word natural language
    # phrases like "*   **Character Design:** The character has long" easily
    # cross that boundary. Allow short multi-word artist / character names
    # ("hatsune miku", "blue archive") but reject sentence-shaped strings.
    if stripped.count(" ") >= 6:
        return True

    # Sentence-case English with 4+ spaces is almost always prose
    # ("This image features a highly detailed").
    if stripped.count(" ") >= 4 and stripped[:1].isupper() and stripped[1:2].islower():
        return True

    # Lone leading quote without a matching closing quote is typically a
    # broken VLM fragment ("\"Cyphotes", "\" \"standing").
    if stripped[0] in ('"', "'") and stripped.count(stripped[0]) == 1:
        return True

    return False


# --------------------------------------------------------------------------
# Whole-text format classification
# --------------------------------------------------------------------------

CAPTION_FORMAT_TAGS = "tags"
CAPTION_FORMAT_NATURAL = "natural"
CAPTION_FORMAT_MIXED = "mixed"
CAPTION_FORMAT_UNKNOWN = "unknown"
CAPTION_FORMATS = frozenset(
    {
        CAPTION_FORMAT_TAGS,
        CAPTION_FORMAT_NATURAL,
        CAPTION_FORMAT_MIXED,
        CAPTION_FORMAT_UNKNOWN,
    }
)

# A chunk ends at sentence punctuation or a line break. Splitting first is what
# lets "1girl, solo. She is standing in a field." be recognised as genuinely
# mixed instead of averaged into one wrong answer.
_CHUNK_BOUNDARY_RE = re.compile(r"([.!?。！？]+|[\r\n]+)")
# Tag separators, matching what the two existing tag splitters already accept
# (they both fold ";" into ","). Ideographic commas are included because this is
# a bilingual product.
_PART_SPLIT_RE = re.compile(r"[,;，、]+")
# Latin words plus single CJK ideographs/kana, which carry no spaces.
_WORD_RE = re.compile(r"[0-9A-Za-z']+|[\u3040-\u30ff\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# Closed-class English words. Extends the stopword probe ToriiGate's structured
# caption check already used (the|a|an|with|and|is|are|this|that|there).
_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
        "for", "from", "had", "has", "have", "he", "her", "hers", "his", "in",
        "into", "is", "it", "its", "of", "on", "onto", "or", "she", "that",
        "the", "their", "them", "there", "these", "they", "this", "those",
        "to", "was", "were", "which", "while", "who", "with", "you", "your",
    }
)

# A tag list's comma segments are short. Measured on the owner's 5,242 real
# sidecars: median 1.72 words per segment, p99 2.24.
_TAG_MEAN_WORDS = 2.5
# Relaxed ceiling for a list where EVERY segment is tag-shaped — real Danbooru
# tags run to five words ("kamiyama high school uniform (project sekai)").
_TAG_MEAN_WORDS_WHEN_ALL_TAG_SHAPED = 4.0
_MIN_PARTS_FOR_RELAXED_MEAN = 4
# A clause needs this many words before its function words mean anything; below
# it, "looking at viewer" would read as prose.
_MIN_PROSE_WORDS = 6
_MIN_TERMINATED_PROSE_WORDS = 3
# Segments this long are clauses, not tags, even without a known function word
# (this is what recognises CJK prose, which has no spaces to count).
_PROSE_MEAN_WORDS = 4.0
_MAX_TAG_WORDS_IN_LONE_SEGMENT = 3


def _chunks(text: str) -> list[tuple[str, bool]]:
    """Split into (chunk, ended_with_sentence_punctuation) pairs."""
    pieces = _CHUNK_BOUNDARY_RE.split(text)
    chunks: list[tuple[str, bool]] = []
    for index in range(0, len(pieces), 2):
        body = pieces[index]
        if not body.strip():
            continue
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        terminated = bool(separator) and separator[0] not in "\r\n"
        chunks.append((body, terminated))
    return chunks


# Internal chunk verdicts. A *weak* tag verdict is enough to call a whole file
# ``tags`` but NOT enough to declare a file ``mixed``: measured on the owner's
# library, every chunk that only reached the relaxed ceiling inside a prose file
# was a prose clause ("She wears a white, off-shoulder, frilled top with black
# accents, and a short black skirt" — four comma clauses, mean 4.0, each
# individually tag-shaped). Genuine mixed files always carry a dense tag run.
_TAGS_STRONG = "tags-strong"
_TAGS_WEAK = "tags-weak"


def _classify_chunk(chunk: str, terminated: bool) -> str | None:
    """Verdict for one chunk: strong/weak tags, natural, or ``None`` when unclear."""
    parts = [part.strip() for part in _PART_SPLIT_RE.split(chunk) if part.strip()]
    if not parts:
        return None
    words = _WORD_RE.findall(chunk)
    word_count = len(words)
    if not word_count:
        return None
    mean_words = word_count / len(parts)
    tag_shaped = [not looks_like_garbage_tag(part) for part in parts]
    tag_shaped_count = sum(tag_shaped)
    has_function_word = any(word.lower() in _FUNCTION_WORDS for word in words)

    # Tag list first: a run of booru tags routinely contains function words
    # ("looking at viewer", "hand on own hip") and would otherwise read as prose.
    if len(parts) >= 2:
        majority_tag_shaped = tag_shaped_count >= max(2, int(len(parts) * 0.6))
        if majority_tag_shaped and mean_words <= _TAG_MEAN_WORDS:
            return _TAGS_STRONG
        if (
            len(parts) >= _MIN_PARTS_FOR_RELAXED_MEAN
            and all(tag_shaped)
            and mean_words <= _TAG_MEAN_WORDS_WHEN_ALL_TAG_SHAPED
        ):
            return _TAGS_WEAK
    elif tag_shaped[0] and (
        "_" in parts[0]
        or (word_count <= _MAX_TAG_WORDS_IN_LONE_SEGMENT and not has_function_word)
    ):
        return _TAGS_STRONG

    if has_function_word and word_count >= _MIN_PROSE_WORDS:
        return CAPTION_FORMAT_NATURAL
    # A short sentence still counts when it was actually terminated — but a lone
    # tag-shaped segment never does. "hands on own cheeks" sits between the
    # "!?" and "?" tags in a real tag list and is not a sentence.
    if (
        has_function_word
        and terminated
        and word_count >= _MIN_TERMINATED_PROSE_WORDS
        and not (len(parts) == 1 and tag_shaped[0])
    ):
        return CAPTION_FORMAT_NATURAL
    # Long segments in text that is genuinely split into words. The
    # word-separation check keeps a glued blob (base64, a hash) out of "natural".
    if (
        word_count >= _MIN_PROSE_WORDS
        and mean_words >= _PROSE_MEAN_WORDS
        and (" " in chunk or _CJK_RE.search(chunk))
    ):
        return CAPTION_FORMAT_NATURAL

    return None


def detect_caption_format(text: object) -> str:
    """Classify caption text as ``tags``, ``natural``, ``mixed`` or ``unknown``.

    Never raises and never returns text. ``unknown`` is the honest answer for
    empty input, non-strings, and anything the rules above cannot place — a
    wrong confident label is worse than admitting ignorance, because a
    downstream converter would then act on it.
    """
    if not isinstance(text, str) or not text.strip():
        return CAPTION_FORMAT_UNKNOWN

    verdicts = set()
    for chunk, terminated in _chunks(text):
        verdict = _classify_chunk(chunk, terminated)
        if verdict is not None:
            verdicts.add(verdict)

    has_prose = CAPTION_FORMAT_NATURAL in verdicts
    # Only a dense tag run can turn a file that also contains prose into
    # "mixed"; a merely tag-shaped run of prose clauses cannot.
    if has_prose and _TAGS_STRONG in verdicts:
        return CAPTION_FORMAT_MIXED
    if has_prose:
        return CAPTION_FORMAT_NATURAL
    if verdicts & {_TAGS_STRONG, _TAGS_WEAK}:
        return CAPTION_FORMAT_TAGS
    return CAPTION_FORMAT_UNKNOWN


def caption_format_for_storage(text: object) -> str | None:
    """Marker to store next to ``text``, or ``None`` when there is no text.

    ``NULL`` and ``'unknown'`` mean different things and both are useful:
    ``NULL`` is "this row has no sidecar caption (or predates migration 044)",
    ``'unknown'`` is "there IS text and the classifier could not place it".
    Callers pass the exact string they are about to write, so the marker and the
    text cannot disagree.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return detect_caption_format(text)
