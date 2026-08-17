"""Strict readers for Dataset Maker caption sidecars."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from caption_format import (
    CAPTION_FORMAT_MIXED,
    CAPTION_FORMAT_NATURAL,
    detect_caption_format,
)


MAX_DATASET_SIDECAR_BYTES = 1024 * 1024

# Formats whose text is NOT a list of tags, so nothing in it may be enumerated as
# one. See :func:`dataset_sidecar_caption_rows`.
_NOT_A_TAG_LIST = frozenset({CAPTION_FORMAT_NATURAL, CAPTION_FORMAT_MIXED})


def read_dataset_sidecar(image_path: str, max_bytes: int) -> Optional[str]:
    """Read a same-stem UTF-8 caption, preserving absent versus empty."""
    sidecar_path = Path(image_path).with_suffix(".txt")
    try:
        if not sidecar_path.exists():
            return None
        if not sidecar_path.is_file():
            raise ValueError(
                f"Caption sidecar is not a file: path={sidecar_path}"
            )
        size = sidecar_path.stat().st_size
        if size > max_bytes:
            raise ValueError(
                "Caption sidecar is too large: "
                f"path={sidecar_path}, size={size}, max_bytes={max_bytes}"
            )
        with sidecar_path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(
            f"Caption sidecar could not be read: path={sidecar_path}, error={exc}"
        ) from exc

    if len(payload) > max_bytes:
        raise ValueError(
            "Caption sidecar is too large: "
            f"path={sidecar_path}, size={len(payload)}, max_bytes={max_bytes}"
        )
    try:
        return payload.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Caption sidecar must be UTF-8: path={sidecar_path}, error={exc}"
        ) from exc


def dataset_sidecar_tag_rows(caption: str) -> list[dict[str, str]]:
    """Convert a Booru sidecar into tag rows for the export renderer."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in caption.splitlines():
        for raw_tag in raw_line.split(","):
            tag = raw_tag.strip()
            key = tag.lower()
            if not tag or key in seen:
                continue
            seen.add(key)
            rows.append({"tag": tag})
    return rows


def dataset_sidecar_caption_rows(caption: str) -> list[dict[str, str]]:
    """Rows for the export renderer, enumerated only when the caption IS tags.

    The export template's tag slots are a list: the pipeline behind them filters
    each entry against the blacklist, cuts the list to ``max_tags``, and buckets
    entries into ``{characters}`` / ``{general}``. Feeding prose into that list
    made every comma clause a pseudo-tag, so a caption limit truncated a sentence
    mid-way and a line break came out as ``", "`` — the caption stopped saying
    what the user's file says.

    So only a caption the classifier confidently reads as a tag list is split:

    * ``tags`` — split on commas and line breaks, as before. A ``.txt`` holding
      one tag per line is a real convention and still becomes separate tags.
    * ``natural`` / ``mixed`` — ONE row holding the caption verbatim. The template
      still wraps it with ``{trigger}`` and ``{append}``, so a project's own
      template keeps working, but nothing can slice the prose. ``mixed`` is not
      being relabelled as prose: it is simply not a tag list either, and its two
      halves cannot be separated without inventing a splitter that would have to
      re-derive the sentence punctuation ``caption_format`` drops — the one thing
      that could lose text.
    * ``unknown`` — split, i.e. unchanged. ``unknown`` means "there is text and
      the classifier declined to place it", and acting on a non-verdict would be
      a guess; this is the same reason ``unknown`` raises no dialect advisory.
      Measured over the owner's 5,242 real sidecars the distribution is 4,985
      ``tags`` / 208 ``mixed`` / 49 ``natural`` and **zero** ``unknown``, so this
      branch is an edge state where the status quo is the honest default.

    The format is derived from ``caption`` itself rather than read from a stored
    ``images.sidecar_caption_format``: this text was read from disk moments ago
    and a marker derived earlier can describe a caption that has since been
    edited. Deriving here means the label and the text can never disagree, and a
    NULL marker on a row predating migration 044 cannot make the render wrong.

    No format label ever removes, shortens or refuses text — a misclassification
    can only change how the caption is wrapped, never whether it survives.
    """
    if detect_caption_format(caption) in _NOT_A_TAG_LIST:
        return [{"tag": caption}] if caption.strip() else []
    return dataset_sidecar_tag_rows(caption)
