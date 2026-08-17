"""Manual Sort undo must put the file back where it came from, under its name.

``_undo_file_operation`` used to hand only the *folder* to ``move_image``, which
derives the filename from the file's current basename and then applies the
forward-move collision suffix. Undoing a move of ``00042.png`` into a folder
that already held a ``00042.png`` therefore restored ``00042_1.png`` — the
original filename destroyed, the library row pointing at the renamed file, and
the undo reported as clean. For a Stable Diffusion library the filename carries
the seed/batch number, so that is real information lost by the undo button.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException

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


def _history_entry(image_id: int, original: Path, moved_to: str) -> dict:
    """The history record Manual Sort builds for a slot move (session.py)."""
    return {
        "action": "move",
        "operation": "move",
        "image_id": image_id,
        "original_path": str(original),
        "original_folder": str(original.parent),
        "new_path": moved_to,
        "copied_image_id": None,
        "folder_key": "a",
    }


class TestManualSortUndoRestoresOriginalPath:
    def test_undo_restores_the_original_filename_after_a_collision_rename(
        self, test_db, tmp_path, svc
    ):
        """The user's file must come back as 00042.png, not 00042_1.png.

        Asserting only that undo "succeeded" would reproduce the bug: the old
        code returned without raising while leaving a renamed file behind.
        """
        library = tmp_path / "library"
        library.mkdir()
        sorted_dir = tmp_path / "sorted"
        sorted_dir.mkdir()

        original = _write_png(library / "00042.png", "red")
        stranger = _write_png(sorted_dir / "00042.png", "blue")
        stranger_bytes = stranger.read_bytes()

        image_id = test_db.add_image(
            path=str(original),
            filename="00042.png",
            generator="unknown",
            metadata_json="{}",
        )

        moved = svc._apply_file_operation(
            operation="move",
            image_id=image_id,
            destination_folder=str(sorted_dir),
            source_path=str(original),
        )
        # Forward-move collision handling is correct and must stay that way.
        assert Path(moved["new_path"]).name == "00042_1.png"
        assert not original.exists()

        svc._undo_file_operation(_history_entry(image_id, original, moved["new_path"]))

        assert original.exists(), "undo must restore the file under its original name"
        assert not (library / "00042_1.png").exists()
        assert not Path(moved["new_path"]).exists()

        row = test_db.get_image_by_id(image_id)
        assert Path(row["path"]).name == "00042.png"
        assert os.path.samefile(row["path"], original)

        # The unrelated file that caused the collision is never touched.
        assert stranger.exists()
        assert stranger.read_bytes() == stranger_bytes

    def test_undo_refuses_when_something_else_now_occupies_the_original_path(
        self, test_db, tmp_path, svc
    ):
        """A genuinely occupied original slot must fail loudly, not silently rename."""
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

        moved = svc._apply_file_operation(
            operation="move",
            image_id=image_id,
            destination_folder=str(sorted_dir),
            source_path=str(original),
        )
        assert Path(moved["new_path"]).name == "00042.png"

        # A different file appears at the original location after the move.
        occupant = _write_png(library / "00042.png", "green")
        occupant_bytes = occupant.read_bytes()

        with pytest.raises(FileOperationError) as excinfo:
            svc._undo_file_operation(
                _history_entry(image_id, original, moved["new_path"])
            )

        message = str(excinfo.value)
        assert "00042.png" in message

        # Nothing moved, nothing renamed, nothing overwritten.
        assert occupant.read_bytes() == occupant_bytes
        assert Path(moved["new_path"]).exists()
        assert not (library / "00042_1.png").exists()
        assert os.path.samefile(
            test_db.get_image_by_id(image_id)["path"], moved["new_path"]
        )

    def test_sort_action_undo_reports_the_occupied_slot_and_stays_retryable(
        self, test_db, tmp_path, svc
    ):
        """The session must tell the user why undo could not run, and keep the entry."""
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
        moved = svc._apply_file_operation(
            operation="move",
            image_id=image_id,
            destination_folder=str(sorted_dir),
            source_path=str(original),
        )
        _write_png(library / "00042.png", "green")

        entry = _history_entry(image_id, original, moved["new_path"])
        svc._sort_session = {
            **svc._build_default_sort_session_state(),
            "active": True,
            "image_ids": [image_id],
            "current_index": 1,
            "folders": {"a": str(sorted_dir)},
            "history": [entry],
        }

        with pytest.raises(HTTPException) as excinfo:
            svc.sort_action("undo")

        assert excinfo.value.status_code == 500
        assert "00042.png" in str(excinfo.value.detail)
        # Rolled back so the user can clear the slot and retry.
        assert svc._sort_session["history"] == [entry]
        assert svc._sort_session["redo_stack"] == []
