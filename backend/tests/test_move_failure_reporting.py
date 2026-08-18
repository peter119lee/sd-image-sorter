"""A failed move must tell the user *why* it failed.

Gallery move and Manual Sort collapsed every cause into "Failed to move image",
so a permission denial, a disconnected drive, a full disk and a file locked by
another program were indistinguishable — even though the precise reason was
already in the exception and already in the backend log. Batch move in the same
package has always passed the real text through, so the two move buttons gave
the user completely different diagnostic quality for the identical failure.

The reported cause is deliberately path-free: the shared frontend formatter
(``frontend/js/modules/utils/errors.js``) discards any message carrying a
drive-qualified path or running past 180 characters, which would throw the
diagnosis away all over again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import BackgroundTasks

import services.sorting_service as ss
from exceptions import FileOperationError


def _write_png(path: Path, color: str) -> Path:
    from PIL import Image

    Image.new("RGB", (16, 16), color=color).save(path)
    return path


def _assert_the_frontend_can_show(cause: str) -> None:
    """The four rules frontend/js/modules/utils/errors.js applies to a message.

    Break any one of them and ``formatUserError`` drops the sentence and
    substitutes "An unexpected error occurred. Please try again.", which is
    worse than useless on a per-file failure list: it tells the user to retry
    something that will fail identically every time.
    """
    assert cause, "an empty cause reaches the user as the canned fallback"
    assert re.search(r"[A-Za-z]:\\", cause) is None, f"drive path survived: {cause!r}"
    assert "\n" not in cause, f"multi-line cause: {cause!r}"
    assert len(cause) < 180, f"cause is {len(cause)} characters: {cause!r}"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A fresh SortingService with its persisted-session files redirected."""
    monkeypatch.setattr(ss, "SESSION_FILE", str(tmp_path / "session.json"), raising=False)
    monkeypatch.setattr(ss, "LEGACY_SESSION_FILE", str(tmp_path / "legacy.json"), raising=False)
    return ss.SortingService()


class TestMoveFailureReporting:
    def test_gallery_move_failure_reports_the_real_cause(self, tmp_path, svc, monkeypatch):
        def _denied(*_args, **_kwargs):
            raise FileOperationError(
                "Permission denied: [WinError 5] Access is denied: "
                r"'L:\library\00042.png'",
                path=r"L:\library\00042.png",
                operation="move",
            )

        monkeypatch.setattr(ss, "move_image", _denied)
        monkeypatch.setattr(ss, "verify_image_readable", lambda _path: (True, None))
        monkeypatch.setattr(svc, "_resolve_image_path", lambda path: path or None)

        result = svc._move_one_image(
            7,
            {"path": r"L:\library\00042.png", "filename": "00042.png"},
            "move",
            str(tmp_path),
        )

        assert result["success"] is False
        assert "Permission denied" in result["error"]
        assert "00042.png" in result["error"]
        assert r"L:\library" not in result["error"]
        assert len(result["error"]) < 180

    def test_gallery_move_failure_distinguishes_a_full_disk(self, tmp_path, svc, monkeypatch):
        def _no_space(*_args, **_kwargs):
            raise FileOperationError(
                "Failed to move file: [Errno 28] No space left on device",
                path=r"L:\library\00042.png",
                operation="move",
            )

        monkeypatch.setattr(ss, "move_image", _no_space)
        monkeypatch.setattr(ss, "verify_image_readable", lambda _path: (True, None))
        monkeypatch.setattr(svc, "_resolve_image_path", lambda path: path or None)

        result = svc._move_one_image(
            7,
            {"path": r"L:\library\00042.png", "filename": "00042.png"},
            "move",
            str(tmp_path),
        )

        assert "No space left on device" in result["error"]

    def test_manual_sort_move_failure_reports_the_real_cause(
        self, test_db, tmp_path, svc, monkeypatch
    ):
        library = tmp_path / "library"
        library.mkdir()
        sorted_dir = tmp_path / "sorted"
        sorted_dir.mkdir()
        original = _write_png(library / "00042.png", "red")

        image_id = test_db.add_image(
            path=str(original),
            filename="00042.png",
            generator="unknown",
            metadata_json="{}",
        )

        def _locked(*_args, **_kwargs):
            raise FileOperationError(
                "Failed to move file: [WinError 32] The process cannot access the "
                "file because it is being used by another process",
                path=str(original),
                operation="move",
            )

        monkeypatch.setattr(ss, "move_image", _locked)
        svc._sort_session = {
            **svc._build_default_sort_session_state(),
            "active": True,
            "image_ids": [image_id],
            "current_index": 0,
            "folders": {"a": str(sorted_dir)},
        }

        result = svc.sort_action("move", "a")

        assert "another process" in result["error"]
        assert str(library) not in result["error"]


class TestBatchMoveFailureReporting:
    """Auto-Separate is the third move surface and needs the same treatment.

    It never lost the cause the way the other two did, but it reported
    ``str(exc)`` verbatim: the FileOperationError preamble duplicated, the
    message spread over the lines the OS put in it, and every absolute path
    intact. That is precisely the shape the frontend formatter throws away.
    """

    def test_batch_move_failure_reports_a_cause_the_frontend_can_show(
        self, test_db, tmp_path, svc, monkeypatch
    ):
        library = tmp_path / "library"
        library.mkdir()
        original = _write_png(library / "00042.png", "blue")
        destination = tmp_path / "keepers"

        test_db.add_image(
            path=str(original),
            filename="00042.png",
            generator="unknown",
            metadata_json="{}",
        )

        def _denied(**_kwargs):
            raise FileOperationError(
                "Permission denied: [WinError 5] Access is denied:\n"
                f"'{original}' -> '{destination / '00042.png'}'",
                path=str(original),
                operation="move",
            )

        monkeypatch.setattr(svc, "_apply_file_operation", _denied)

        background_tasks = BackgroundTasks()
        svc.batch_move_images(
            ss.BatchMoveRequest(
                destination_folder=str(destination),
                generators=["unknown"],
            ),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        progress = svc.get_batch_move_progress()
        assert progress["errors"] == 1
        reported = progress["recent_errors"][0]["error"]

        assert "Permission denied" in reported
        assert "00042.png" in reported
        assert str(library) not in reported
        _assert_the_frontend_can_show(reported)

    def test_an_undecodable_file_says_so_instead_of_reading_as_try_again(
        self, test_db, tmp_path, svc
    ):
        """The one branch that skipped the contract the other branches honour.

        A truncated file never reaches ``_apply_file_operation``: the readability
        check refuses it first, and that branch appended Pillow's ``str(exc)``
        verbatim — ``cannot identify image file '<absolute path>'``. errors.js
        discards any message carrying a drive path, so the user was told to try
        again on a file that cannot be decoded and never will be, and the one
        sentence that said so was thrown away.

        The property is that the decoder's own explanation reaches the user;
        which words Pillow chooses is not this test's business, so they are read
        back from the decoder rather than spelled out here.
        """
        library = tmp_path / "library"
        library.mkdir()
        broken = _write_png(library / "00042.png", "green")
        broken.write_bytes(b"truncated image data")

        test_db.add_image(
            path=str(broken),
            filename="00042.png",
            generator="unknown",
            metadata_json="{}",
        )

        readable, decoder_answer = ss.verify_image_readable(str(broken))
        assert readable is False and decoder_answer, (
            "the decoder accepted the file, so this test proves nothing"
        )

        background_tasks = BackgroundTasks()
        svc.batch_move_images(
            ss.BatchMoveRequest(
                destination_folder=str(tmp_path / "keepers"),
                generators=["unknown"],
            ),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        progress = svc.get_batch_move_progress()
        assert progress["errors"] == 1
        reported = progress["recent_errors"][0]["error"]

        # Every word the decoder used outside the path it quoted has to
        # survive; only the path itself is allowed to shrink.
        decoder_words = re.findall(
            r"[A-Za-z]{3,}", re.sub(r"'[^']*'|\"[^\"]*\"", " ", decoder_answer)
        )
        assert decoder_words, "the decoder said nothing but a path"
        for word in decoder_words:
            assert word in reported, f"{word!r} was lost from {reported!r}"

        assert "00042.png" in reported, "the path was deleted rather than shortened"
        assert str(library) not in reported
        _assert_the_frontend_can_show(reported)

    def test_every_reported_cause_is_renderable_whatever_failed(
        self, test_db, tmp_path, svc, monkeypatch
    ):
        """One run, three different failures, one contract over the whole list.

        Fixing the branch that leaked a drive path is not the same as making it
        impossible for the next branch to leak one, and this list is the only
        thing the Auto-Separate panel has to show. Every entry it carries has to
        be renderable, whichever way the image failed.
        """
        library = tmp_path / "library"
        library.mkdir()
        undecodable = _write_png(library / "undecodable.png", "green")
        undecodable.write_bytes(b"truncated image data")
        refused = _write_png(library / "refused.png", "blue")

        for image_path in (undecodable, refused):
            test_db.add_image(
                path=str(image_path),
                filename=image_path.name,
                generator="unknown",
                metadata_json="{}",
            )
        vanished_id = test_db.add_image(
            path=str(library / "vanished.png"),
            filename="vanished.png",
            generator="unknown",
            metadata_json="{}",
        )
        (library / "vanished.png").unlink(missing_ok=True)

        def _denied(**_kwargs):
            raise FileOperationError(
                "Permission denied: [WinError 5] Access is denied:\n"
                f"'{refused}' -> '{tmp_path / 'keepers' / refused.name}'",
                path=str(refused),
                operation="move",
            )

        monkeypatch.setattr(svc, "_apply_file_operation", _denied)

        background_tasks = BackgroundTasks()
        svc.batch_move_images(
            ss.BatchMoveRequest(
                destination_folder=str(tmp_path / "keepers"),
                generators=["unknown"],
            ),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        progress = svc.get_batch_move_progress()
        assert progress["errors"] == 3, (
            "the run did not produce the three different failures this pins"
        )
        assert vanished_id > 0
        for entry in progress["recent_errors"]:
            _assert_the_frontend_can_show(entry["error"])
