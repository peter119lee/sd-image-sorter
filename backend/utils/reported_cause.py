"""User-facing causes for a specific file that failed.

Anything reported as the reason a scan, move, or readability check refused a
file has to pass through here first. ``frontend/js/modules/utils/errors.js``
replaces any message carrying a drive-qualified path, or running past its
length ceiling, with a canned "please try again" sentence — so an
un-normalized cause is not merely untidy, it is deleted before the user sees
it.
"""

from __future__ import annotations

import re
from typing import Optional

# Short enough that the shared frontend formatter forwards the sentence
# instead of collapsing it at its 180-character ceiling.
_MAX_REPORTED_CAUSE_LENGTH = 140

_QUOTED_PATH_PATTERN = re.compile(r"(['\"])(?P<path>[^'\"]*[\\/][^'\"]*)\1")
_DRIVE_OR_UNC_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s'\"]*")
_POSIX_PATH_PATTERN = re.compile(r"(?<![\w~])/(?:[^\s'\"/]+/)+[^\s'\"]*")


def _path_leaf(value: str) -> str:
    """Return the final component of a path written with either separator."""
    parts = [part for part in re.split(r"[\\/]+", value) if part]
    return parts[-1] if parts else value


def _shorten_paths(text: str) -> str:
    """Replace filesystem paths in an error message with their filename.

    The cause of a failed read or move is worth showing the user; the absolute
    path it happened at is not.
    """
    shortened = _QUOTED_PATH_PATTERN.sub(
        lambda match: f"{match.group(1)}{_path_leaf(match.group('path'))}{match.group(1)}",
        text,
    )
    shortened = _DRIVE_OR_UNC_PATH_PATTERN.sub(
        lambda match: _path_leaf(match.group(0)), shortened
    )
    return _POSIX_PATH_PATTERN.sub(lambda match: _path_leaf(match.group(0)), shortened)


def normalize_reported_cause(text: str) -> str:
    """Render an already-worded cause as one line a user can be shown."""
    cause = " ".join(_shorten_paths(str(text or "")).split())
    if len(cause) > _MAX_REPORTED_CAUSE_LENGTH:
        cause = cause[:_MAX_REPORTED_CAUSE_LENGTH].rstrip() + "..."
    return cause


def describe_readability_failure(read_error: Optional[str]) -> str:
    """Name why an image could not be decoded, in words a user can be shown.

    Pillow answers with ``cannot identify image file '<absolute path>'``.
    Reporting that verbatim is the one shape errors.js throws away.
    """
    return normalize_reported_cause(read_error) or "Unreadable image"
