"""Contracts of the sidecar change detector (migration 043).

The scan used to compare only ``(image mtime, image size)``, so a ``.txt``
caption written or edited after indexing was invisible forever. These tests pin
the three properties the re-read gate depends on:

* the fingerprint moves for every sidecar shape ``metadata_parser`` would read,
  and for both of its naming forms;
* "no sidecar" is a definite answer (``''``), distinct from "could not look"
  (``None``), because only the latter must never trigger a re-read or clear
  stored caption text;
* an un-fingerprinted legacy row - which is every one of the owner's 6,842 rows
  right after the migration - is only re-read when a sidecar is actually there.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from image_manager_gates import _sidecar_fingerprint_changed
from sidecar_fingerprint import (
    NO_SIDECAR_FINGERPRINT,
    compute_sidecar_fingerprint,
    _sidecar_candidate_paths,
)


def _image(tmp_path: Path, name: str = "shot.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"not really a png, never opened here")
    return path


class TestComputeSidecarFingerprint:
    def test_no_sidecar_is_a_definite_empty_answer(self, tmp_path: Path):
        assert compute_sidecar_fingerprint(str(_image(tmp_path))) == NO_SIDECAR_FINGERPRINT

    def test_a_missing_path_is_unknown_not_empty(self):
        assert compute_sidecar_fingerprint("") is None

    @pytest.mark.parametrize("sidecar_name", ["shot.txt", "shot.png.txt", "shot.json", "shot.xmp"])
    def test_every_shape_the_parser_reads_moves_the_fingerprint(
        self, tmp_path: Path, sidecar_name: str
    ):
        image = _image(tmp_path)
        bare = compute_sidecar_fingerprint(str(image))

        (tmp_path / sidecar_name).write_text("1girl, solo", encoding="utf-8")

        assert compute_sidecar_fingerprint(str(image)) != bare

    def test_editing_the_text_moves_the_fingerprint(self, tmp_path: Path):
        image = _image(tmp_path)
        sidecar = tmp_path / "shot.txt"
        sidecar.write_text("1girl, solo", encoding="utf-8")
        before = compute_sidecar_fingerprint(str(image))

        sidecar.write_text("1boy, armor, holding sword", encoding="utf-8")

        assert compute_sidecar_fingerprint(str(image)) != before

    def test_touching_only_the_image_does_not_move_it(self, tmp_path: Path):
        """The fingerprint answers about sidecars; the image has its own."""
        image = _image(tmp_path)
        (tmp_path / "shot.txt").write_text("1girl, solo", encoding="utf-8")
        before = compute_sidecar_fingerprint(str(image))

        os.utime(image, (1_600_000_000, 1_600_000_000))

        assert compute_sidecar_fingerprint(str(image)) == before

    def test_deleting_the_sidecar_returns_to_the_empty_answer(self, tmp_path: Path):
        image = _image(tmp_path)
        sidecar = tmp_path / "shot.txt"
        sidecar.write_text("1girl, solo", encoding="utf-8")
        assert compute_sidecar_fingerprint(str(image)) != NO_SIDECAR_FINGERPRINT

        sidecar.unlink()

        assert compute_sidecar_fingerprint(str(image)) == NO_SIDECAR_FINGERPRINT

    def test_a_directory_named_like_a_sidecar_is_not_one(self, tmp_path: Path):
        image = _image(tmp_path)
        (tmp_path / "shot.txt").mkdir()

        assert compute_sidecar_fingerprint(str(image)) == NO_SIDECAR_FINGERPRINT

    def test_an_unstattable_candidate_answers_unknown(self, tmp_path: Path, monkeypatch):
        """Unknown, never "none": "none" would clear stored caption text."""
        import sidecar_fingerprint as module

        image = _image(tmp_path)

        def _denied(path, *args, **kwargs):
            raise PermissionError("access denied")

        monkeypatch.setattr(module.os, "stat", _denied)

        assert compute_sidecar_fingerprint(str(image)) is None

    def test_both_naming_forms_are_questioned(self, tmp_path: Path):
        candidates = [
            os.path.basename(path)
            for path in _sidecar_candidate_paths(str(tmp_path / "shot.png"))
        ]

        assert "shot.png.txt" in candidates
        assert "shot.txt" in candidates
        assert "shot.json" in candidates
        assert "shot.xmp" in candidates


class TestSidecarFingerprintChangedGate:
    def test_unknown_never_forces_a_reread(self):
        assert _sidecar_fingerprint_changed({"sidecar_fingerprint": "abc"}, None) is False
        assert _sidecar_fingerprint_changed({"sidecar_fingerprint": None}, None) is False

    def test_a_legacy_row_with_a_sidecar_now_is_reread_once(self):
        assert _sidecar_fingerprint_changed({"sidecar_fingerprint": None}, "abc") is True

    def test_a_legacy_row_with_no_sidecar_is_left_alone(self):
        """Otherwise upgrading re-parses an entire library for nothing."""
        assert (
            _sidecar_fingerprint_changed(
                {"sidecar_fingerprint": None}, NO_SIDECAR_FINGERPRINT
            )
            is False
        )

    def test_a_matching_fingerprint_is_unchanged(self):
        assert _sidecar_fingerprint_changed({"sidecar_fingerprint": "abc"}, "abc") is False

    def test_a_removed_sidecar_counts_as_changed(self):
        assert (
            _sidecar_fingerprint_changed(
                {"sidecar_fingerprint": "abc"}, NO_SIDECAR_FINGERPRINT
            )
            is True
        )

    def test_a_row_that_settled_on_no_sidecar_stays_settled(self):
        assert (
            _sidecar_fingerprint_changed(
                {"sidecar_fingerprint": NO_SIDECAR_FINGERPRINT}, NO_SIDECAR_FINGERPRINT
            )
            is False
        )
