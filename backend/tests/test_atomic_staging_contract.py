"""Staging contracts for the two Pillow writers that publish beside a destination.

Two hazards, both measured on Windows and both closed by
``utils.atomic_staging``:

1. ``tempfile.mkstemp`` treats a ``PermissionError`` as "that random name is
   already taken" and retries up to ``tempfile.TMP_MAX`` — measured at
   2,147,483,647 on this interpreter, not the 10,000 the docs imply — because
   ``os.access`` only inspects the read-only attribute and reports an
   ACL-protected folder as writable. Staging an atomic write into a folder the
   process cannot write to therefore never returned. The tests below reproduce
   the mechanism by refusing every create in one directory and asserting the
   ATTEMPT COUNT, which bounds the run instead of waiting out 10,000 real
   syscalls.
2. ``os.replace`` publishes a new inode, which severs a hard link and leaves
   every other name for that file holding the pre-write bytes (dd11296).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List

import pytest
from PIL import Image

from services.censor.output_io import _save_pillow_image_atomically
from utils import atomic_staging


class _RetryStorm(AssertionError):
    """The staging path retried a create the folder had already refused.

    Deliberately not an ``OSError``: ``tempfile._mkstemp_inner`` swallows
    ``PermissionError`` and ``FileExistsError`` to retry up to
    ``tempfile.TMP_MAX`` times, so only an unrelated exception type can cut the
    storm short.
    Cutting it short is what keeps this suite bounded — against a real folder
    each refused create costs a filesystem round trip, which is exactly why the
    unbounded version stalled for over 30 minutes.
    """


def _refuse_creates_in(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    error: OSError,
    *,
    alarm_after: int = atomic_staging.STAGING_NAME_ATTEMPTS + 2,
) -> List[str]:
    """Make every file creation inside ``directory`` fail, and record attempts.

    Patched on the ``os`` module itself rather than on one importer, because
    ``tempfile`` does ``import os as _os`` and therefore shares the object:
    a patch scoped to a single module would not reach the buggy staging path
    and the test would pass for the wrong reason. Every other path is delegated
    to the real ``os.open`` so pytest, Pillow, and the temp tree keep working.
    """
    real_open = os.open
    attempts: List[str] = []
    guarded = os.path.normcase(str(directory))

    def guarded_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not isinstance(path, int):
            try:
                text = os.fspath(path)
            except TypeError:
                text = ""
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            if text and os.path.normcase(os.path.dirname(text)) == guarded:
                attempts.append(text)
                if len(attempts) >= alarm_after:
                    raise _RetryStorm(
                        f"staging retried a refused create {len(attempts)} times "
                        f"in {directory}; an unwritable destination folder must "
                        "surface the operating system's error on the first "
                        "attempt instead of stalling the write"
                    )
                raise error
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    return attempts


class TestStagingNeverRetriesAnUnwritableFolder:
    def test_a_permission_error_is_reported_on_the_first_attempt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(PermissionError):
            atomic_staging.create_staging_sibling(locked / "target.png")

        assert len(attempts) == 1, (
            f"a permission error was retried {len(attempts)} times; an unwritable "
            "destination folder must fail immediately, not stall the write"
        )

    def test_the_name_search_is_bounded_and_never_reuses_a_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        locked = tmp_path / "taken"
        locked.mkdir()
        attempts = _refuse_creates_in(
            monkeypatch, locked, FileExistsError(17, "File exists")
        )

        with pytest.raises(FileExistsError):
            atomic_staging.create_staging_sibling(locked / "target.png")

        assert len(attempts) == atomic_staging.STAGING_NAME_ATTEMPTS
        assert len(set(attempts)) == len(attempts), "the search reused a name"

    def test_the_search_steps_over_an_abandoned_staging_file(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "row.png"
        first_path, first_fd = atomic_staging.create_staging_sibling(target)
        os.close(first_fd)

        second_path, second_fd = atomic_staging.create_staging_sibling(target)
        os.close(second_fd)

        assert second_path != first_path
        assert second_path.suffix == target.suffix
        assert first_path.exists() and second_path.exists()

    def test_the_censor_writer_reports_an_unwritable_output_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        locked = tmp_path / "censor-out"
        locked.mkdir()
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )
        image = Image.new("RGB", (8, 8), color=(11, 22, 33))

        with pytest.raises(PermissionError):
            _save_pillow_image_atomically(
                image, str(locked / "censored.png"), "PNG", {}
            )

        assert len(attempts) == 1, (
            f"the censor save retried an unwritable folder {len(attempts)} times; "
            "the request would never return"
        )

    def test_the_dataset_row_writer_reports_an_unwritable_output_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import engine

        locked = tmp_path / "dataset-out"
        locked.mkdir()
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )
        image = Image.new("RGB", (8, 8), color=(44, 55, 66))

        with pytest.raises(PermissionError):
            engine._write_transformed_row_atomic(
                image,
                locked / "row.png",
                "1girl, solo",
                locked / "row.txt",
                None,
                None,
            )

        assert len(attempts) == 1, (
            f"the dataset row writer retried an unwritable folder {len(attempts)} "
            "times; the export job would never finish"
        )

    def test_the_trainer_config_invalidation_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The first thing a trainer-config export writes into the user's folder.

        It runs before the first row is staged, so a refusal here hung the export
        before reaching the row writer the previous test covers.
        """
        from fastapi import HTTPException

        from services.dataset_export.artifacts import _invalidate_existing_anima_config

        locked = tmp_path / "trainer-out"
        locked.mkdir()
        existing = locked / "dataset_config.toml"
        kept = "# Generated by SD Image Sorter for anima_lora v1.14.2.hotfix.\n"
        existing.write_text(kept, encoding="utf-8")
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(HTTPException) as raised:
            _invalidate_existing_anima_config(locked, "anima_lora_toml")

        assert len(attempts) == 1, (
            f"invalidating the stale trainer config retried an unwritable folder "
            f"{len(attempts)} times; the export would never start"
        )
        assert raised.value.status_code == 409
        assert "PermissionError" in str(raised.value.detail)
        assert existing.read_text(encoding="utf-8") == kept

    def test_the_package_metadata_writer_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import package_integrity

        locked = tmp_path / "package-out"
        locked.mkdir()
        target = locked / "export_manifest.json"
        kept = '{"package_status": "complete"}'
        target.write_text(kept, encoding="utf-8")
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(package_integrity.PackageIntegrityError) as raised:
            package_integrity._atomic_write_text(
                target, '{"package_status": "building"}'
            )

        assert len(attempts) == 1, (
            f"the package manifest writer retried an unwritable folder "
            f"{len(attempts)} times; the export job would never finish"
        )
        assert "PermissionError" in str(raised.value)
        assert target.read_text(encoding="utf-8") == kept

    def test_the_package_file_copy_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import package_integrity

        locked = tmp_path / "package-copy"
        locked.mkdir()
        source = tmp_path / "source.txt"
        source.write_bytes(b"bytes the export wanted to publish")
        target = locked / "row.txt"
        kept = b"bytes already in the user's folder"
        target.write_bytes(kept)
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(package_integrity.PackageIntegrityError) as raised:
            package_integrity.copy_package_file_atomic(source, target, locked)

        assert len(attempts) == 1, (
            f"the package file copy retried an unwritable folder {len(attempts)} "
            "times; the export job would never finish"
        )
        assert "PermissionError" in str(raised.value)
        assert target.read_bytes() == kept

    def test_the_package_inventory_writer_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import package_integrity

        locked = tmp_path / "inventory-out"
        locked.mkdir()
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(package_integrity.PackageIntegrityError) as raised:
            package_integrity.PackageInventoryWriter(locked, "0" * 32)

        assert len(attempts) == 1, (
            f"the package inventory writer retried an unwritable folder "
            f"{len(attempts)} times; the export job would never start"
        )
        assert "PermissionError" in str(raised.value)

    def test_the_kohya_config_writer_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import kohya_contract

        locked = tmp_path / "kohya-out"
        locked.mkdir()
        target = locked / "dataset_config.toml"
        kept = "# previous config the user still has\n"
        target.write_text(kept, encoding="utf-8")
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(kohya_contract.KohyaTrainerContractError) as raised:
            kohya_contract._write_kohya_config_atomically(
                target, "[general]\nenable_bucket = true\n"
            )

        assert len(attempts) == 1, (
            f"the Kohya config writer retried an unwritable folder "
            f"{len(attempts)} times; the export would never finish"
        )
        assert "PermissionError" in str(raised.value)
        assert target.read_text(encoding="utf-8") == kept

    def test_the_anima_config_writer_reports_an_unwritable_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import anima_contract

        locked = tmp_path / "anima-out"
        locked.mkdir()
        target = locked / "dataset_config.toml"
        kept = "# previous config the user still has\n"
        target.write_text(kept, encoding="utf-8")
        attempts = _refuse_creates_in(
            monkeypatch, locked, PermissionError(13, "Permission denied")
        )

        with pytest.raises(anima_contract.AnimaTrainerContractError) as raised:
            anima_contract._write_anima_config_atomically(
                target, "[general]\nenable_bucket = true\n"
            )

        assert len(attempts) == 1, (
            f"the Anima config writer retried an unwritable folder "
            f"{len(attempts)} times; the export would never finish"
        )
        assert "PermissionError" in str(raised.value)
        assert target.read_text(encoding="utf-8") == kept


class TestPublishKeepsHardLinks:
    def test_saving_over_a_hardlinked_destination_keeps_the_link(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "shared.png"
        Image.new("RGB", (8, 8), color=(1, 2, 3)).save(target)
        alias = tmp_path / "shared-alias.png"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")

        _save_pillow_image_atomically(
            Image.new("RGB", (8, 8), color=(250, 240, 230)), str(target), "PNG", {}
        )

        assert os.path.samefile(target, alias), (
            "the censor save severed the user's hard link; the other name kept the "
            "uncensored image"
        )
        with Image.open(alias) as published:
            assert published.getpixel((0, 0)) == (250, 240, 230)
        assert not list(tmp_path.glob(".*.bak")), "a backup was left behind"

    def test_a_failed_in_place_update_restores_the_previous_image(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "restore.png"
        Image.new("RGB", (8, 8), color=(9, 8, 7)).save(target)
        alias = tmp_path / "restore-alias.png"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")
        original_bytes = target.read_bytes()

        real_overwrite = atomic_staging._overwrite_in_place
        calls: List[Path] = []

        def failing_overwrite(destination: Path, source: Path) -> None:
            calls.append(source)
            if len(calls) == 1:
                raise OSError(5, "simulated device failure")
            real_overwrite(destination, source)

        monkeypatch.setattr(atomic_staging, "_overwrite_in_place", failing_overwrite)

        with pytest.raises(OSError):
            _save_pillow_image_atomically(
                Image.new("RGB", (8, 8), color=(200, 100, 50)),
                str(target),
                "PNG",
                {},
            )

        assert target.read_bytes() == original_bytes
        assert alias.read_bytes() == original_bytes
        assert os.path.samefile(target, alias)
        assert not list(tmp_path.glob(".*.bak")), (
            "the restored image left its backup behind"
        )
        assert not list(tmp_path.glob(".restore.png.tmp*")), (
            "the failed save left its staging file behind"
        )

    def test_copying_a_package_file_over_a_hardlinked_target_keeps_the_link(
        self, tmp_path: Path
    ) -> None:
        """The package copier is the one site here that meets a real user file.

        ``overwrite_policy="overwrite"`` republishes over images already in the
        chosen folder, and hardlinking a large image set is a real space-saving
        practice, so a rename-publish here would leave the alias on the
        pre-export bytes.
        """
        from services.dataset_export import package_integrity

        root = tmp_path / "package"
        root.mkdir()
        target = root / "row.png"
        target.write_bytes(b"bytes from the previous export")
        alias = tmp_path / "row-alias.png"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")
        source = tmp_path / "source.png"
        published = b"bytes this export is publishing"
        source.write_bytes(published)

        package_integrity.copy_package_file_atomic(source, target, root)

        assert os.stat(target).st_nlink == 2, (
            "the package copy severed the user's hard link"
        )
        assert os.path.samefile(target, alias)
        assert target.read_bytes() == published
        assert alias.read_bytes() == published
        assert not list(root.glob(".*.bak")), "a backup was left behind"
        assert not list(root.glob(".row.png.tmp*")), "a staging file was left behind"

    def test_a_failed_package_copy_restores_the_previous_bytes_on_both_names(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from services.dataset_export import package_integrity

        root = tmp_path / "package"
        root.mkdir()
        target = root / "row.png"
        original = b"bytes from the previous export"
        target.write_bytes(original)
        alias = tmp_path / "row-alias.png"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")
        source = tmp_path / "source.png"
        source.write_bytes(b"bytes this export failed to publish")

        real_overwrite = atomic_staging._overwrite_in_place
        calls: List[Path] = []

        def failing_overwrite(destination: Path, staged: Path) -> None:
            calls.append(staged)
            if len(calls) == 1:
                raise OSError(5, "simulated device failure")
            real_overwrite(destination, staged)

        monkeypatch.setattr(atomic_staging, "_overwrite_in_place", failing_overwrite)

        with pytest.raises(package_integrity.PackageIntegrityError):
            package_integrity.copy_package_file_atomic(source, target, root)

        assert target.read_bytes() == original
        assert alias.read_bytes() == original
        assert os.stat(target).st_nlink == 2
        assert os.path.samefile(target, alias)
        assert not list(root.glob(".*.bak")), (
            "the restored file left its backup behind"
        )
        assert not list(root.glob(".row.png.tmp*")), (
            "the failed copy left its staging file behind"
        )

    def test_writing_a_trainer_config_over_a_hardlinked_target_keeps_the_link(
        self, tmp_path: Path
    ) -> None:
        from services.dataset_export import kohya_contract

        target = tmp_path / "dataset_config.toml"
        target.write_text("# previous config\n", encoding="utf-8")
        alias = tmp_path / "shared_dataset_config.toml"
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")
        content = "[general]\nenable_bucket = true\n"

        kohya_contract._write_kohya_config_atomically(target, content)

        assert os.stat(target).st_nlink == 2, (
            "the trainer config writer severed the user's hard link"
        )
        assert os.path.samefile(target, alias)
        assert alias.read_text(encoding="utf-8") == content
        assert not list(tmp_path.glob(".*.bak")), "a backup was left behind"
        assert not list(tmp_path.glob(".dataset_config.toml.tmp*")), (
            "a staging file was left behind"
        )
