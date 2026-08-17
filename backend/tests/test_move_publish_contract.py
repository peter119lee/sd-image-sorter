"""Contract tests for how a move publishes its destination file.

``dd3c79f`` replaced ``shutil.move`` with ``_move_file_atomically`` so a
cross-volume move could not leave half-written bytes under the final filename.
The fallback it reused stages through ``tempfile.mkstemp``, and on Windows
``mkstemp`` reads a ``PermissionError`` as "that random name is already taken"
and retries it up to ``tempfile.TMP_MAX`` — 2,147,483,647 on this interpreter,
not the 10,000 the docs imply — because ``os.access`` only inspects the
read-only attribute and calls an ACL-denied folder writable. Any rename failure
entered that fallback, so a move into a folder the user cannot write stopped
answering at all instead of naming the reason. ``shutil.move`` had failed fast.

These tests pin the three properties that fixes it: only a genuine cross-volume
rename may fall back to a copy, the staging search is bounded, and the staged
copy is flushed and published without severing a hard link.

Every test that faces an unwritable folder caps the number of create attempts
inside it, so a regression fails the run in milliseconds instead of hanging it.
"""

import contextlib
import errno
import os
import subprocess
import sys
from pathlib import Path

import database as db
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from exceptions import FileOperationError  # noqa: E402
import image_manager  # noqa: E402
from utils.atomic_staging import STAGING_NAME_ATTEMPTS  # noqa: E402


# Far above any legitimate bounded search, far below a number that takes real
# time to reach. A staging search that passes this is retrying, not reporting.
OPEN_ATTEMPT_CAP = 24


class StagingRetryStorm(BaseException):
    """Deliberately not an ``OSError``: a retry loop must not swallow this."""


def _require_the_folder_lies_about_being_writable(directory: Path) -> None:
    """Confirm the premise the defect rests on, or skip instead of pretending.

    The retry storm only happens while ``os.access`` reports the folder as
    writable; that is what sends ``mkstemp`` around its loop again. If the deny
    did not take effect for this account there is nothing to reproduce, and a
    test that quietly passed there would be the "guard that stopped checking"
    failure this project has already been bitten by.
    """
    probe = directory / ".writability-probe.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(probe, flags)
    except PermissionError:
        assert os.access(directory, os.W_OK), (
            "os.access no longer calls an ACL-denied folder writable, so "
            "tempfile.mkstemp would stop retrying there. Re-check the hazard "
            "before trusting this test to cover it."
        )
        return
    os.close(descriptor)
    probe.unlink(missing_ok=True)
    pytest.skip("This account can still create files in the deny-write folder")


@contextlib.contextmanager
def _write_denied_directory(path: Path):
    """Yield a real folder this account may not create files in."""
    if os.name != "nt":
        pytest.skip("The mkstemp retry storm is a Windows ACL behavior")
    path.mkdir(parents=True, exist_ok=True)
    account = os.environ.get("USERNAME") or ""
    if not account:
        pytest.skip("Cannot deny write access without an account name")

    denied = subprocess.run(
        ["icacls", str(path), "/deny", f"{account}:(W,AD,WD)"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    try:
        if denied.returncode != 0:
            pytest.skip(f"Could not deny write access to a scratch folder: {denied.stderr.strip()}")
        _require_the_folder_lies_about_being_writable(path)
        yield path
    finally:
        # Always hand the permission back: pytest prunes old tmp_path trees and
        # cannot delete a folder it may not write.
        subprocess.run(
            ["icacls", str(path), "/remove:d", account],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )


@contextlib.contextmanager
def _capped_create_attempts(directory: Path, cap: int = OPEN_ATTEMPT_CAP):
    """Count create attempts inside one folder and abort a retry storm."""
    real_open = os.open
    folder = os.path.abspath(str(directory))
    attempts: list[str] = []

    def counting_open(path, flags, *args, **kwargs):
        inside = False
        try:
            candidate = os.fspath(path)
            if isinstance(candidate, str):
                inside = os.path.dirname(os.path.abspath(candidate)) == folder
        except TypeError:
            inside = False
        if inside:
            attempts.append(candidate)
            if len(attempts) > cap:
                raise StagingRetryStorm(
                    f"{len(attempts)} create attempts inside {directory.name} for the "
                    "same refusal: the staging search is retrying instead of reporting"
                )
        return real_open(path, flags, *args, **kwargs)

    os.open = counting_open
    try:
        yield attempts
    finally:
        os.open = real_open


@contextlib.contextmanager
def _rename_reports_a_different_volume(source_path: Path):
    """Make one file's rename fail the way a real cross-volume rename does.

    A second writable volume is not guaranteed on a test machine, so the errno
    the operating system actually raises is injected at the ``os.replace`` seam
    instead. That errno was verified here by moving a file from ``L:`` to
    ``C:``: ``OSError`` errno 18 (``EXDEV``), ``WinError`` 17. Only the file
    under test is diverted, so the staging publish's own rename still runs for
    real.
    """
    real_rename = os.rename
    real_replace = os.replace
    wanted = os.path.abspath(str(source_path))

    def _is_the_file_under_test(candidate) -> bool:
        return os.path.abspath(str(candidate)) == wanted

    def cross_volume_rename(src, dst, *args, **kwargs):
        if _is_the_file_under_test(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    def cross_volume_replace(src, dst, *args, **kwargs):
        if _is_the_file_under_test(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *args, **kwargs)

    os.rename = cross_volume_rename
    os.replace = cross_volume_replace
    try:
        yield
    finally:
        os.rename = real_rename
        os.replace = real_replace


def _write_image(path: Path, color: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=color).save(path)
    return path.read_bytes()


def test_move_into_an_unwritable_folder_reports_permission_instead_of_hanging(
    test_db, tmp_path: Path
):
    """The user must get the reason, not a job that never finishes.

    An ACL-denied destination makes ``os.replace`` raise ``PermissionError``
    (errno 13, verified on this machine), which is not a cross-volume rename and
    must never be retried as a staged copy.
    """
    source_path = tmp_path / "denied-source" / "denied-move.png"
    source_bytes = _write_image(source_path, "teal")
    image_id = db.add_image(path=str(source_path), filename=source_path.name)

    with _write_denied_directory(tmp_path / "denied-destination") as destination_dir:
        with _capped_create_attempts(destination_dir) as attempts:
            with pytest.raises(FileOperationError) as failure:
                image_manager.move_image(image_id, str(destination_dir), str(source_path))

    assert "Permission denied" in str(failure.value)
    assert attempts == [], (
        "A refusal that is not a cross-volume rename must not be staged as a copy: "
        f"{len(attempts)} create attempts were made inside the unwritable folder"
    )
    assert source_path.read_bytes() == source_bytes
    assert db.get_image_by_id(image_id)["path"] == str(source_path)


def test_copy_into_an_unwritable_folder_reports_permission_instead_of_hanging(
    test_db, tmp_path: Path
):
    """A copy has no rename to narrow, so only the bounded staging saves it.

    ``copy_image`` goes straight to the staging path that the move only reaches
    across volumes, which is why restricting the move's fallback is not on its
    own enough: this caller hung on the same loop and is fixed only by bounding
    the search.
    """
    source_path = tmp_path / "denied-copy-source" / "denied-copy.png"
    source_bytes = _write_image(source_path, "sienna")
    image_id = db.add_image(path=str(source_path), filename=source_path.name)

    with _write_denied_directory(tmp_path / "denied-copy-destination") as destination_dir:
        with _capped_create_attempts(destination_dir) as attempts:
            with pytest.raises(FileOperationError) as failure:
                image_manager.copy_image(image_id, str(destination_dir), str(source_path))

    assert "Permission denied" in str(failure.value)
    assert 0 < len(attempts) <= STAGING_NAME_ATTEMPTS, (
        "The staging search must stop at its own bound; it made "
        f"{len(attempts)} attempts against a bound of {STAGING_NAME_ATTEMPTS}"
    )
    assert source_path.read_bytes() == source_bytes


def test_cross_volume_move_into_an_unwritable_folder_reports_on_the_first_refusal(
    tmp_path: Path
):
    """Even the fallback itself must report a refusal rather than retry it.

    This is the second, independent layer: a cross-volume rename whose
    destination folder also refuses writes does legitimately reach the staging
    search, and that search has to stop at its own bound.
    """
    source_path = tmp_path / "xdev-denied-source" / "xdev-denied.png"
    source_bytes = _write_image(source_path, "olive")

    with _write_denied_directory(tmp_path / "xdev-denied-destination") as destination_dir:
        destination_path = destination_dir / source_path.name
        with _rename_reports_a_different_volume(source_path):
            with _capped_create_attempts(destination_dir) as attempts:
                with pytest.raises(PermissionError):
                    image_manager._move_file_atomically(
                        str(source_path), str(destination_path)
                    )

    assert 0 < len(attempts) <= STAGING_NAME_ATTEMPTS, (
        "The staging search must stop at its own bound; it made "
        f"{len(attempts)} attempts against a bound of {STAGING_NAME_ATTEMPTS}"
    )
    assert source_path.read_bytes() == source_bytes


def test_cross_volume_move_still_publishes_the_complete_file(test_db, tmp_path: Path):
    """Narrowing the fallback must not break the case the fallback exists for."""
    source_path = tmp_path / "xdev-source" / "xdev-move.png"
    source_bytes = _write_image(source_path, "navy")
    source_mtime_ns = os.stat(source_path).st_mtime_ns
    destination_dir = tmp_path / "xdev-destination"
    destination_dir.mkdir()
    image_id = db.add_image(path=str(source_path), filename=source_path.name)

    with _rename_reports_a_different_volume(source_path):
        new_path = image_manager.move_image(image_id, str(destination_dir), str(source_path))

    published = Path(new_path)
    assert published.read_bytes() == source_bytes
    assert not source_path.exists()
    assert [entry.name for entry in destination_dir.iterdir()] == [published.name], (
        "The staged copy must leave no sibling behind"
    )
    assert os.stat(published).st_mtime_ns == source_mtime_ns, (
        "A moved file keeps its timestamp, or every moved image looks changed to "
        "the next scan's (mtime, size) fingerprint"
    )
    assert db.get_image_by_id(image_id)["path"] == str(published)


def test_cross_volume_move_flushes_the_copy_before_the_destination_name_appears(
    tmp_path: Path
):
    """The published name must not be the first thing to reach the disk.

    ``dd3c79f`` says it exists to stop "half-written bytes sitting under the
    final filename". Without an fsync the copied bytes can still be only in the
    page cache when the name is published, so a power loss leaves exactly that
    file. Durability cannot be observed without cutting power, so what is
    asserted here is the ordering that makes it possible: the staged file is
    flushed, and only afterwards does the destination name exist.
    """
    source_path = tmp_path / "fsync-source" / "fsync-move.png"
    _write_image(source_path, "purple")
    destination_dir = tmp_path / "fsync-destination"
    destination_dir.mkdir()
    destination_path = destination_dir / source_path.name

    folder = os.path.abspath(str(destination_dir))
    real_open = os.open
    real_fsync = os.fsync
    real_replace = os.replace
    descriptor_paths: dict[int, str] = {}
    events: list[tuple[str, str]] = []

    def recording_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        try:
            candidate = os.fspath(path)
        except TypeError:
            return descriptor
        if isinstance(candidate, str) and os.path.dirname(os.path.abspath(candidate)) == folder:
            descriptor_paths[descriptor] = os.path.abspath(candidate)
        return descriptor

    def recording_fsync(descriptor):
        staged = descriptor_paths.get(descriptor)
        if staged is not None:
            events.append(("fsync", staged))
        return real_fsync(descriptor)

    def recording_replace(src, dst, *args, **kwargs):
        if os.path.abspath(str(src)) == os.path.abspath(str(source_path)):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        events.append(("publish", os.path.abspath(str(dst))))
        return real_replace(src, dst, *args, **kwargs)

    os.open = recording_open
    os.fsync = recording_fsync
    os.replace = recording_replace
    try:
        image_manager._move_file_atomically(str(source_path), str(destination_path))
    finally:
        os.open = real_open
        os.fsync = real_fsync
        os.replace = real_replace

    published = os.path.abspath(str(destination_path))
    staged_flushes = [
        index
        for index, (kind, path) in enumerate(events)
        if kind == "fsync" and path != published
    ]
    publishes = [index for index, (kind, path) in enumerate(events) if kind == "publish" and path == published]

    assert staged_flushes, (
        "The staged copy was never flushed, so the destination name can appear "
        f"over bytes that are still only in the page cache. Observed: {events}"
    )
    assert publishes, f"The destination was never published by rename. Observed: {events}"
    assert min(staged_flushes) < min(publishes), (
        f"The flush must precede the publish. Observed: {events}"
    )
    assert destination_path.read_bytes() == Path(published).read_bytes()


def test_move_over_a_hardlinked_destination_keeps_the_link(tmp_path: Path):
    """A publish must not hand the destination a private new inode.

    ``dd11296`` established the rule for every writer that can land on a file
    the user owns: rename-publish only while the destination is its file's only
    name. ``os.replace`` over a linked destination leaves every alias holding
    the pre-move bytes, so the user sees one name updated and the other silently
    stale.
    """
    source_path = tmp_path / "link-source" / "link-move.png"
    source_bytes = _write_image(source_path, "green")
    destination_dir = tmp_path / "link-destination"
    destination_path = destination_dir / "link-move.png"
    _write_image(destination_path, "red")
    alias_path = destination_dir / "alias-of-destination.png"
    try:
        os.link(destination_path, alias_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"This filesystem cannot create hard links: {exc}")
    assert os.stat(destination_path).st_nlink == 2

    with _rename_reports_a_different_volume(source_path):
        image_manager._move_file_atomically(str(source_path), str(destination_path))

    assert destination_path.read_bytes() == source_bytes
    assert os.stat(destination_path).st_nlink == 2, (
        "Publishing severed the user's hard link, so the alias kept the old image"
    )
    assert os.path.samefile(destination_path, alias_path)
    assert alias_path.read_bytes() == source_bytes
    assert not source_path.exists()
    assert sorted(entry.name for entry in destination_dir.iterdir()) == [
        alias_path.name,
        destination_path.name,
    ], "The staging file and its backup must both be gone"
