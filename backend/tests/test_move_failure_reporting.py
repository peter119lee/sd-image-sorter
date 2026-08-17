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

from pathlib import Path

import pytest

import services.sorting_service as ss
from exceptions import FileOperationError


def _write_png(path: Path, color: str) -> Path:
    from PIL import Image

    Image.new("RGB", (16, 16), color=color).save(path)
    return path


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
