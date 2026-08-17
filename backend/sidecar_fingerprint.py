"""Fingerprint the caption sidecars sitting next to an image.

The scan's change detector was ``(image mtime, image size)`` alone. Writing or
editing a ``.txt`` next to an already-indexed image changes neither, so the row
stayed an "unchanged hit" and its caption text was never read again — not by
that scan, not by any later one. Measured on the owner's library: all 5,242
rows whose file still exists have a ``.txt`` beside them, and 5,214 of those
sidecars are newer than the image they describe.

This module answers "did any sidecar for this image change?" by fingerprinting
the (name, mtime_ns, size) of every sidecar candidate that currently exists.
``metadata_parser`` looks for two naming forms per extension
(``image.png.txt`` and ``image.txt``) and skips symlinks; both rules are
mirrored here so the fingerprint changes exactly when the parser's input would.

Deliberately uncached: the only cache key available without carrying per-run
state is the directory mtime, and NTFS does not bump that when a file's
contents are edited in place — a cache keyed on it would miss precisely the
edited-sidecar case this exists to catch. Measured cost of the uncached form on
the owner's library: 124us per image (0.65s for 5,242 files, ~10s projected for
80k), against a scan that opens and parses every changed image.
"""
from __future__ import annotations

import hashlib
import os
import stat as stat_module
from typing import List, Optional, Tuple

from metadata_parser import SIDECAR_EXTENSIONS

# "This image has no sidecar", stored as a definite value rather than NULL so a
# row without sidecars settles instead of looking un-fingerprinted forever.
NO_SIDECAR_FINGERPRINT = ""

# 128 bits of a sha256, which is only ever compared for equality. Keeps the
# column at a fixed 32 characters instead of storing filenames on every row.
_FINGERPRINT_HEX_CHARS = 32


def _sidecar_candidate_paths(image_path: str) -> List[str]:
    """Every sidecar path metadata_parser would look at, in a stable order."""
    base, _ = os.path.splitext(image_path)
    candidates: List[str] = []
    seen = set()
    for extension in SIDECAR_EXTENSIONS:
        for candidate in (f"{image_path}{extension}", f"{base}{extension}"):
            key = os.path.normcase(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def compute_sidecar_fingerprint(image_path: str) -> Optional[str]:
    """Digest the sidecars next to ``image_path``.

    Returns ``NO_SIDECAR_FINGERPRINT`` when the image has no sidecar, a hex
    digest when it has at least one, and ``None`` when the filesystem could not
    be questioned — which callers must read as "unknown", never as "none",
    because treating it as none would clear stored caption text.
    """
    if not image_path:
        return None

    observed: List[Tuple[str, int, int]] = []
    for candidate in _sidecar_candidate_paths(image_path):
        try:
            # follow_symlinks=False: a symlinked sidecar is never read by
            # metadata_parser._load_one_sidecar, so it must not move the
            # fingerprint either.
            stat_result = os.stat(candidate, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            # A candidate we cannot stat makes the whole answer untrustworthy.
            return None
        if not stat_module.S_ISREG(stat_result.st_mode):
            continue
        observed.append(
            (
                os.path.basename(candidate).lower(),
                int(stat_result.st_mtime_ns),
                int(stat_result.st_size),
            )
        )

    if not observed:
        return NO_SIDECAR_FINGERPRINT

    canonical = "|".join(
        f"{name}:{mtime_ns}:{size}" for name, mtime_ns, size in sorted(observed)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_CHARS]
