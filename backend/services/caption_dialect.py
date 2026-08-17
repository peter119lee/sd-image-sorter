"""Does a caption's FORMAT match what the consumer about to use it needs?

``caption_format`` answers "what format is this text in?". This module answers
the product question layered on top: "given what this text is about to be used
for, does that need the user's attention?" It is the join that was missing —
``dataset_project_models`` has stored a ``target_model`` since migration 033 and
nothing ever compared it with the caption dialect, so a ``krea2`` project could
export Booru tag captions and an ``anima`` project could export prose.

Hard invariant, inherited from the marker slice
==============================================
**Nothing here may discard, truncate or refuse text.** Every function returns a
label, a bool, an advisory record, or ``None``. Detection is good but not
perfect (measured 99.96% agreement with an independent oracle over the owner's
5,242 real sidecars), so when it is wrong the only cost must be an unnecessary
notice — never a lost or edited caption.

Which targets get an opinion, and why only two
==============================================
``CAPTION_DIALECT_TARGET_MODELS`` deliberately covers two of the four values
``target_model`` accepts:

* ``krea2`` -> ``natural``. Krea's own documentation recommends
  natural-language prompts and Krea 2 trains predominantly on long NL captions,
  and ``AGENTS.md`` makes it a binding rule: "Treat Krea 2 as a
  natural-language-first target. Do not reintroduce Booru-only prompt
  assumptions into its workflow."
* ``anima`` -> ``tags``. The NoobAI/booru family prescribes rigid tag order plus
  quality and era tokens that are meaningless outside booru captioning.

``sdxl`` and ``flux`` are **left un-opinionated on purpose**, and so is the empty
"not chosen" value. SDXL was trained on alt-text prose yet essentially all
community LoRA work for it uses tag captions, and no first-party source
prescribes a caption format for FLUX LoRA training. Guessing for them would
manufacture warnings the evidence does not support. Adding a target here
requires first-party evidence, not a plausible inference.

The dialect is not binary
=========================
Illustrious v1.1+ trains on hybrid NL+tag captions and TIPO is deliberately
bilingual, so ``mixed`` is never reported as a mismatch with a conversion
direction. It gets its own "needs attention" code and no direction. ``unknown``
gets no advisory at all: there is text and the classifier declined to place it,
and telling a user to convert text nobody could classify is worse than silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from caption_format import (
    CAPTION_FORMAT_MIXED,
    CAPTION_FORMAT_NATURAL,
    CAPTION_FORMAT_TAGS,
    caption_format_for_storage,
    detect_caption_format,
    looks_like_garbage_tag,
)


CAPTION_DIALECT_TAGS = CAPTION_FORMAT_TAGS
CAPTION_DIALECT_NATURAL = CAPTION_FORMAT_NATURAL

# Target model -> the caption dialect it is documented to want. Read the module
# docstring before adding an entry; an unevidenced guess here becomes a warning
# on every export.
CAPTION_DIALECT_TARGET_MODELS: dict[str, str] = {
    "krea2": CAPTION_DIALECT_NATURAL,
    "anima": CAPTION_DIALECT_TAGS,
}

_CONVERSION_FOR = {
    (CAPTION_FORMAT_TAGS, CAPTION_DIALECT_NATURAL): "tags_to_natural",
    (CAPTION_FORMAT_NATURAL, CAPTION_DIALECT_TAGS): "natural_to_tags",
}

_DIALECT_LABEL = {
    CAPTION_DIALECT_TAGS: "Booru tag captions",
    CAPTION_DIALECT_NATURAL: "natural-language captions",
}
_FORMAT_LABEL = {
    CAPTION_FORMAT_TAGS: "a Booru tag list",
    CAPTION_FORMAT_NATURAL: "natural-language prose",
    CAPTION_FORMAT_MIXED: "both tags and prose",
}

CODE_DIALECT_MISMATCH = "caption_dialect_mismatch"
CODE_DIALECT_PARTIAL = "caption_dialect_partial"
CODE_NL_OVER_TAG_SOURCE = "nl_compose_over_tag_source"
CODE_NL_SOURCE_PARTLY_TAGS = "nl_compose_source_partly_tags"

# Composition modes that put text into a prose slot. ``booru`` (and an absent
# entry) keeps the tag caption, so nothing there can contradict a tag source.
_PROSE_COMPOSITION_MODES = frozenset({"nl", "both"})


@dataclass(frozen=True)
class CaptionDialectAdvisory:
    """One thing that needs attention. Advisory only — it never blocks a write."""

    code: str
    caption_format: str
    message: str
    action: str
    expected_dialect: Optional[str] = None
    target_model: str = ""
    convert: Optional[str] = None


def dialect_for_target_model(target_model: object) -> Optional[str]:
    """The caption dialect ``target_model`` is documented to want, or ``None``."""
    return CAPTION_DIALECT_TARGET_MODELS.get(str(target_model or "").strip().lower())


def resolved_caption_format(
    text: object,
    stored_format: object = None,
) -> Optional[str]:
    """The format of ``text``: the stored marker when it has one, else derived.

    Callers on the Dataset Maker session path read the ``.txt`` fresh from disk
    and have no database row, so they pass ``text`` only. Callers holding an
    image row pass the ``sidecar_caption_format`` column as well; a row written
    before migration 044 has ``NULL`` there while still having text, which is
    why the derivation is the fallback rather than the exception.

    Returns ``None`` only when there is no text at all.
    """
    marker = str(stored_format or "").strip().lower()
    if marker in {
        CAPTION_FORMAT_TAGS,
        CAPTION_FORMAT_NATURAL,
        CAPTION_FORMAT_MIXED,
    }:
        return marker
    return caption_format_for_storage(text)


def caption_reads_as_prose(
    text: object,
    stored_format: object = None,
) -> bool:
    """Whether ``text`` is confidently natural-language prose.

    Used to decide whether an existing sidecar caption may stand in for a
    natural-language caption. ``mixed`` and ``unknown`` deliberately answer
    ``False``: a slot that asks for prose should only be filled from text the
    classifier positively placed as prose.
    """
    return resolved_caption_format(text, stored_format) == CAPTION_FORMAT_NATURAL


def caption_dialect_advisory(
    target_model: object,
    caption_format: object,
) -> Optional[CaptionDialectAdvisory]:
    """Whether this caption format needs attention for this target model.

    ``None`` means "nothing to say", which covers four different situations that
    must not be conflated: the target has no evidenced dialect, there is no
    caption text (``None``), the classifier declined to place the text
    (``unknown``), and the format already agrees with the target.
    """
    dialect = dialect_for_target_model(target_model)
    if dialect is None:
        return None
    fmt = str(caption_format or "").strip().lower()
    model = str(target_model or "").strip().lower()
    wants = _DIALECT_LABEL[dialect]

    convert = _CONVERSION_FOR.get((fmt, dialect))
    if convert is not None:
        return CaptionDialectAdvisory(
            code=CODE_DIALECT_MISMATCH,
            caption_format=fmt,
            expected_dialect=dialect,
            target_model=model,
            convert=convert,
            message=(
                f"This caption is {_FORMAT_LABEL[fmt]}, but the project targets "
                f"{model}, which wants {wants}."
            ),
            action=(
                "Convert this caption, or re-caption the image with the "
                "natural-language captioner, before exporting."
                if convert == "tags_to_natural"
                else "Convert this caption, or re-tag the image, before exporting."
            ),
        )

    if fmt == CAPTION_FORMAT_MIXED:
        return CaptionDialectAdvisory(
            code=CODE_DIALECT_PARTIAL,
            caption_format=fmt,
            expected_dialect=dialect,
            target_model=model,
            convert=None,
            message=(
                f"This caption contains {_FORMAT_LABEL[fmt]}, and the project "
                f"targets {model}, which wants {wants}. Hybrid captions are "
                "deliberate for some trainers, so no conversion is assumed."
            ),
            action="Review this caption and decide whether the mix is intended.",
        )

    return None


def nl_compose_advisory(
    composition_mode: object,
    caption_format: object,
) -> Optional[CaptionDialectAdvisory]:
    """Whether a prose composition slot is about to receive tag text.

    The per-image composition mode comes from the export request alone, so
    asking for ``nl`` over a tag-list source used to emit a tag dump into a
    prose slot with nothing said about it. The text is still emitted in full —
    substituting or dropping it would be the worse failure — but the user is now
    told, because "these 40 captions are tag lists, not prose" is actionable and
    a silent tag dump is not.
    """
    if str(composition_mode or "").strip().lower() not in _PROSE_COMPOSITION_MODES:
        return None
    fmt = str(caption_format or "").strip().lower()
    if fmt == CAPTION_FORMAT_TAGS:
        return CaptionDialectAdvisory(
            code=CODE_NL_OVER_TAG_SOURCE,
            caption_format=fmt,
            message=(
                "Natural-language composition was requested, but the caption "
                "source is a Booru tag list, so the prose slot receives tags."
            ),
            action=(
                "Switch this image back to tag captions, or generate a "
                "natural-language caption for it, then export again."
            ),
        )
    if fmt == CAPTION_FORMAT_MIXED:
        return CaptionDialectAdvisory(
            code=CODE_NL_SOURCE_PARTLY_TAGS,
            caption_format=fmt,
            message=(
                "Natural-language composition was requested, and the caption "
                "source mixes tags with prose, so part of the prose slot "
                "receives tags."
            ),
            action="Review this caption's natural-language text before exporting.",
        )
    return None


def is_prose_segment(value: object) -> bool:
    """Whether one comma segment is positively a prose clause.

    Used to keep prose out of a prompt's ``{tags}`` slot. Both primitives are
    required because each has a blind spot that would delete real tags:
    ``looks_like_garbage_tag`` rejects ``?``, ``!?`` and ``^_^``, which are
    genuine Danbooru tags present in the owner's library, while
    ``detect_caption_format`` reads a long multi-word tag such as
    ``kamiyama high school uniform (project sekai)`` as prose on its own.
    Requiring both to agree keeps every real tag and still rejects a sentence.
    """
    text = str(value or "")
    if not text.strip():
        return False
    return looks_like_garbage_tag(text) and (
        detect_caption_format(text) == CAPTION_FORMAT_NATURAL
    )


def tag_slot_candidates(values: Iterable[object]) -> list[str]:
    """The subset of ``values`` that may be interpolated into a ``{tags}`` slot.

    Every ``user_prompt_with_tags`` preset tells the model that the interpolated
    text is danbooru-style tags, so a prose clause in there is a false statement
    about the input rather than merely a weak hint. Nothing the user owns is
    lost: this filters a grounding hint assembled in memory, not stored text.
    """
    return [
        str(value)
        for value in values
        if str(value or "").strip() and not is_prose_segment(value)
    ]
