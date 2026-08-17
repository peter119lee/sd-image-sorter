"""Stage a file beside its destination, then publish it over that destination.

Why this exists instead of ``tempfile``
=======================================
``tempfile.NamedTemporaryFile(dir=target.parent)`` / ``tempfile.mkstemp(dir=...)``
**cannot** be used to stage a write. On Windows ``mkstemp`` treats a
``PermissionError`` as "that random name is already taken" and retries up to
``TMP_MAX`` — measured at 2,147,483,647 on the shipped interpreter, not the
10,000 the docs imply, so the retry is effectively unbounded — because
``os.access`` only inspects the read-only attribute and reports an ACL-protected
folder such as ``C:\\Windows\\System32`` as writable. Staging into a folder the
process cannot write to therefore never returns — measured stalling a test for
over 30 minutes without finishing — so a clean error turns into a hang, the one
failure the user cannot even diagnose.

``O_CREAT | O_EXCL`` surfaces the real error on the first attempt. The
deterministic ``.<name>.tmp<suffix>`` name matches
``tag_export.sidecars._write_sidecar_atomically``, which is why that writer never
had the hazard, and a short bounded search still steps over a staging file
abandoned by a killed process.

Two other copies of this rule exist and are deliberately left alone:
``tag_export.sidecars`` writes text through a single fixed name (no search
needed) and ``services.image_metadata_writer._create_staging_file`` is bound to
its own descriptor-based encode path. Fold them in here when those files are
free; do not add a fourth copy.

Publishing
==========
``os.replace`` points the destination name at a brand-new inode, which silently
severs a hard link: measured here, an in-place save keeps ``st_nlink == 2`` and
``samefile`` while ``os.replace`` drops the count to 1 and leaves the other name
holding the pre-write bytes. :func:`publish_staging_file` therefore
rename-publishes only while the destination is its file's only name, and
otherwise overwrites in place behind an fsync'd ``.bak`` with restore-on-failure
— the rule established in ``dd11296``.

Accepted project-wide trade: ``os.replace`` needs ``FILE_SHARE_DELETE`` on any
concurrent handle and the Windows CRT does not grant it, so publishing can fail
where a bare write sometimes succeeded. It fails with the user's original file
intact, which is the correct direction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


# Enough to step over a staging file left behind by a killed process, few enough
# that an unwritable or exhausted folder is reported rather than retried.
STAGING_NAME_ATTEMPTS = 8

_COPY_BLOCK_BYTES = 1024 * 1024


def create_staging_sibling(target: Path) -> Tuple[Path, int]:
    """Create an exclusive staging file beside ``target`` and return it open.

    Returns ``(staging_path, file_descriptor)``; the caller owns the descriptor.
    Raises the operating system's real error — ``PermissionError`` for a folder
    that refuses writes — on the first refusal instead of retrying it.
    """
    suffix = target.suffix or ".tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    last_error: Optional[OSError] = None

    for attempt in range(STAGING_NAME_ATTEMPTS):
        marker = ".tmp" if attempt == 0 else f".tmp{attempt}"
        candidate = target.with_name(f".{target.name}{marker}{suffix}")
        try:
            return candidate, os.open(candidate, flags)
        except FileExistsError as exc:
            last_error = exc
            continue

    raise last_error if last_error is not None else OSError(
        f"Could not create a staging file beside {target}"
    )


def discard_staging_file(path: Path) -> None:
    """Drop a staging or backup file whose contents are no longer needed."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def destination_has_other_links(target: Path) -> bool:
    """Return whether other directory entries point at this destination's file."""
    try:
        return os.stat(target).st_nlink > 1
    except OSError:
        return False


def _copy_file_contents(source: Path, destination: Path) -> None:
    """Stream one file onto another, leaving the result durable on disk."""
    with open(source, "rb") as reader, open(destination, "wb") as writer:
        while True:
            block = reader.read(_COPY_BLOCK_BYTES)
            if not block:
                break
            writer.write(block)
        writer.flush()
        try:
            os.fsync(writer.fileno())
        except OSError:
            pass


def _overwrite_in_place(destination: Path, source: Path) -> None:
    """Replace a file's contents without replacing the file itself."""
    with open(source, "rb") as reader, open(destination, "r+b") as writer:
        written = 0
        while True:
            block = reader.read(_COPY_BLOCK_BYTES)
            if not block:
                break
            writer.write(block)
            written += len(block)
        writer.truncate(written)
        writer.flush()
        try:
            os.fsync(writer.fileno())
        except OSError:
            pass


def _publish_preserving_links(staging: Path, target: Path) -> None:
    """Update a hardlinked destination in place, behind an fsync'd backup.

    Rename-publishing here would hand this name a private new inode and leave
    every alias holding the pre-write bytes, so the bytes have to go through the
    shared file. That is recovery rather than atomicity: a hard kill mid-write
    can still leave a partial file, but the previous contents survive beside it
    as ``.<name>.bak`` instead of being gone.
    """
    backup_path = target.with_name(f".{target.name}.bak")
    _copy_file_contents(target, backup_path)
    try:
        _overwrite_in_place(target, staging)
    except BaseException:
        try:
            _overwrite_in_place(target, backup_path)
        except OSError as restore_error:
            # Keep the backup: it is now the only complete copy of the file.
            raise OSError(
                f"Writing {target.name} failed and the previous file could not be "
                f"restored; it is kept at {backup_path}: {restore_error}"
            ) from restore_error
        discard_staging_file(backup_path)
        raise
    discard_staging_file(backup_path)


def publish_staging_file(staging: Path, target: Path) -> None:
    """Publish ``staging`` as ``target`` without severing ``target``'s hard links.

    ``staging`` no longer exists once this returns successfully. On failure the
    destination is left exactly as it was and ``staging`` is left for the caller
    to discard.
    """
    if destination_has_other_links(target):
        _publish_preserving_links(staging, target)
        discard_staging_file(staging)
        return
    os.replace(str(staging), str(target))
