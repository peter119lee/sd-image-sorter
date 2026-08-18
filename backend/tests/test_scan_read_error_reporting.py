"""A scan-marked unreadable row must not carry the file's folder into the UI.

Commit 01789f9 taught the Auto-Separate / move path to store a user-facing
cause (single line, filename instead of the absolute path, length-capped)
because ``frontend/js/modules/utils/errors.js`` discards any message that
still names a drive. The scan path kept storing ``str(exc)`` verbatim, and
Library Health prints ``sample.read_error`` with no formatter, so a row
marked unreadable by a scan could put the owner's folder into the attention
list.

The stored cause has to be the same shape the move path already writes. The
attention list also has to survive rows that were stored before that, because
``formatUserError`` would delete the whole sentence rather than shorten it.
"""

from __future__ import annotations

import re
from pathlib import Path

import database as db
from image_manager import _parse_metadata_job, scan_folder
from metadata_parser import verify_image_readable
from PIL import Image

from tests.test_library_health_attention_reasons import _panel_reasons
from tests.test_move_failure_reporting import _assert_the_frontend_can_show


def _undecodable_png(folder: Path, name: str = "00042.png") -> Path:
    """A file whose name says PNG but whose bytes Pillow will not decode.

    The decoder's answer quotes the absolute path. That is the one shape this
    pin exists to refuse — a truncated-but-still-openable PNG can fail with
    ``Truncated File Read`` and never mention a folder, which would let a
    leak pass by accident.
    """
    folder.mkdir(parents=True, exist_ok=True)
    broken = folder / name
    broken.write_bytes(b"truncated image data")
    return broken


def test_scan_of_an_undecodable_file_stores_a_cause_without_a_drive_path(
    test_db, tmp_path: Path
) -> None:
    """The scan worker, the DB row, and the attention-list copy all stay path-free.

    A brand-new corrupt file is dropped from the library (the scan reports it
    and moves on). An already-indexed file that later fails to decode is the
    row Library Health actually lists, so this seeds that row first, then
    scans the folder the way a rescan of a damaged file would.
    """
    library = tmp_path / "library"
    library.mkdir()
    broken = library / "00042.png"
    Image.new("RGB", (16, 16), color="green").save(broken)
    scan_folder(str(library), recursive=False, metadata_workers=1)

    readable = db.get_image_by_path(str(broken))
    assert readable is not None and int(readable["is_readable"] or 0) == 1, (
        "the first scan did not index the good file, so the rescan would not "
        "be marking an existing row"
    )

    broken.write_bytes(b"truncated image data")
    readable_now, decoder_answer = verify_image_readable(str(broken))
    assert readable_now is False and decoder_answer, (
        "the decoder accepted the file, so this test proves nothing"
    )

    scan_folder(str(library), recursive=False, metadata_workers=1)
    stored = db.get_image_by_path(str(broken))
    assert stored is not None
    assert int(stored["is_readable"] or 0) == 0
    cause = stored["read_error"] or ""

    decoder_words = re.findall(
        r"[A-Za-z]{3,}", re.sub(r"'[^']*'|\"[^\"]*\"", " ", decoder_answer)
    )
    assert decoder_words, "the decoder said nothing but a path"
    for word in decoder_words:
        assert word in cause, f"{word!r} was lost from {cause!r}"
    assert "00042.png" in cause, "the path was deleted rather than shortened"
    assert str(library) not in cause
    _assert_the_frontend_can_show(cause)

    report = db.get_library_health_report(sample_limit=8)
    sample = next(row for row in report["issue_samples"] if int(row["id"]) == int(stored["id"]))
    printed = _panel_reasons([sample])[0]
    assert re.search(r"[A-Za-z]:\\", printed) is None, f"drive path reached the list: {printed!r}"
    assert str(library) not in printed


def test_parse_job_record_does_not_keep_the_folder_either(tmp_path: Path) -> None:
    """The metadata worker is the scan write site; it must normalise before upsert."""
    broken = _undecodable_png(tmp_path / "library")
    result = _parse_metadata_job({"path": str(broken), "filename": broken.name})
    cause = result["record"]["read_error"]
    assert result["record"]["is_readable"] is False
    assert broken.name in cause
    assert str(broken.parent) not in cause
    _assert_the_frontend_can_show(cause)


def test_attention_list_still_names_the_file_when_the_stored_cause_has_a_folder(
    test_db, tmp_path: Path
) -> None:
    """Old scan rows already have the raw path. formatUserError would wipe them.

    A one-line display sanitiser has to keep the decoder's sentence and the
    filename, and drop only the directory, so a library that has not been
    re-scanned stops leaking without turning the reason into a blank.
    """
    library = tmp_path / "library"
    library.mkdir()
    raw = (
        "cannot identify image file "
        r"'L:\OwnersLibrary\kept.png'"
    )
    image_id = int(
        db.add_image(
            path=str(library / "kept.png"),
            filename="kept.png",
            generator="unknown",
            metadata_json="{}",
        )
    )
    with db.get_db() as conn:
        conn.execute(
            "UPDATE images SET is_readable = 0, read_error = ?, metadata_status = 'error' "
            "WHERE id = ?",
            (raw, image_id),
        )

    report = db.get_library_health_report(sample_limit=8)
    sample = next(row for row in report["issue_samples"] if int(row["id"]) == image_id)
    assert sample["read_error"] == raw, (
        "this pins display of an already-stored value; if the payload rewrote "
        "it, the sanitiser would no longer be what this test is watching"
    )
    printed = _panel_reasons([sample])[0]
    assert "kept.png" in printed
    assert re.search(r"[A-Za-z]:\\", printed) is None, f"drive path reached the list: {printed!r}"
    assert "OwnersLibrary" not in printed
    assert "cannot identify" in printed.lower()
