"""DB-backed characterization pins for tagging_service (god-file redesign, step 0).

Companion to test_tagging_pins.py / test_tagging_pins_service.py. This file
pins the seams that need a real (isolated) SQLite database:

  * _tagging_worker_main — run IN-PROCESS with the deterministic
    _E2ETaggingStub (SD_IMAGE_SORTER_E2E_FAKE_TAGGER=1), never a real ONNX
    model: cancel-before-processing, unreadable/missing-file accounting,
    nonexistent-id filtering, pre-write blacklist/max_tags shaping, tag_scores
    riding the same transaction, untagged-vs-retag_all source selection,
    provenance (source='tagger' + replace_scope='pipeline' keeps manual rows),
    terminal last_run_stats.
  * export_tags — colon-in-tag round trip, untagged exclusion, empty-tag rows.
  * import_tags — filename fallback matching; _normalize_import_tags matrix.
  * fix_rating_tags — dedup semantics + the category-blind name matching QUIRK.
  * start_export_bulk_job — Debt-22 envelope, result mapping, progress/cancel
    wiring, selection-token snapshotting (export engine faked; the engine
    itself is pinned in test_routers/test_tags.py + tag-export suites).

Behaviors marked "QUIRK" are pinned as-is on purpose: if a refactor changes
them, that must be a conscious decision, not an accident.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import database as db  # noqa: E402
import services.tagging_service as tsvc  # noqa: E402
from services.tagging_service import (  # noqa: E402
    BatchTagExportRequest,
    TagImportRequest,
    TaggingService,
)

RATING_NAMES = {"general", "sensitive", "questionable", "explicit"}


class _RecorderQueue:
    """Captures worker progress payloads without multiprocessing IPC."""

    def __init__(self) -> None:
        self.messages = []

    def put(self, item) -> None:
        self.messages.append(item)


class _OrderingQueue(_RecorderQueue):
    def __init__(self, events: list[object]) -> None:
        super().__init__()
        self.events = events

    def put(self, item: dict[str, object]) -> None:
        if item.get("status") in {"done", "error", "cancelled"}:
            self.events.append(item["status"])
        super().put(item)


class _StaticEvent:
    def __init__(self, is_set: bool = False) -> None:
        self._is_set = is_set

    def is_set(self) -> bool:
        return self._is_set


def _assert_isolated_db() -> None:
    """Refuse to run the worker against anything but the test_db fixture."""
    assert "test_" in db.DATABASE_PATH or "tmp" in db.DATABASE_PATH, (
        f"test_db fixture did not patch DATABASE_PATH; got {db.DATABASE_PATH!r}. "
        "Refusing to run the tagging worker against a possibly-real DB."
    )


def _add_image(tmp_path: Path, name: str) -> int:
    image_path = tmp_path / name
    Image.new("RGB", (16, 16), color=(120, 80, 40)).save(image_path)
    return db.add_image(str(image_path), image_path.name, metadata_json="{}")


def _payload(image_ids=None, *, retag_all: bool = False, **request_overrides):
    request = {
        "model_name": "wd-swinv2-tagger-v3",
        "image_ids": image_ids,
        "retag_all": retag_all,
        "use_gpu": False,
    }
    request.update(request_overrides)
    return {
        "request": request,
        "model_name": "wd-swinv2-tagger-v3",
        "effective_use_gpu": False,
        "fetch_batch_size": 4,
    }


def _run_worker(payload, *, cancelled: bool = False):
    _assert_isolated_db()
    recorder = _RecorderQueue()
    tsvc._tagging_worker_main(payload, recorder, _StaticEvent(cancelled))
    return recorder.messages


@pytest.fixture
def fake_tagger_env(test_db, monkeypatch):
    """Isolated DB + deterministic stub tagger + readable-image bypass."""
    monkeypatch.setenv("SD_IMAGE_SORTER_E2E_FAKE_TAGGER", "1")
    monkeypatch.setattr(tsvc, "verify_image_readable", lambda path: (True, None))
    return test_db


# ===========================================================================
# _tagging_worker_main — orchestration pins.
# ===========================================================================


def test_worker_cancel_before_processing_writes_nothing(
    fake_tagger_env, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "cancel_early.png")

    messages = _run_worker(_payload([image_id]), cancelled=True)

    terminal = messages[-1]
    assert terminal["status"] == "cancelled"
    assert terminal["message"] == "Tagging cancelled before processing images"
    # QUIRK: the pre-processing cancel carries no last_run_stats (the stats
    # snapshot only rides cancellations that reached the tagging loop).
    assert "last_run_stats" not in terminal
    assert db.get_image_tags(image_id) == []


def test_worker_records_activity_before_success_terminal(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "activity_before_done.png")
    events: list[object] = []
    recorder = _OrderingQueue(events)
    monkeypatch.setattr(
        tsvc.entry_stats_service,
        "record_activity",
        lambda kind, count: events.append((kind, count)),
    )

    tsvc._tagging_worker_main(_payload([image_id]), recorder, _StaticEvent(False))

    assert events == [(tsvc.entry_stats_service.KIND_TAGGED, 1), "done"]
    assert recorder.messages[-1]["status"] == "done"


def test_worker_missing_file_marks_unreadable_and_counts_error(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "gone_missing.png")
    monkeypatch.setattr(
        tsvc, "resolve_existing_indexed_image_path", lambda *args, **kwargs: None
    )

    messages = _run_worker(_payload([image_id]))

    terminal = messages[-1]
    assert terminal["status"] == "done"
    assert terminal["errors"] == 1
    assert terminal["tagged"] == 0
    assert terminal["message"] == "Completed! Processed 1 images: 0 tagged, 1 failed."
    assert any(
        "Skipped unreadable image: gone_missing.png (File not found)" in m["message"]
        for m in messages
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT is_readable, read_error FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    assert row["is_readable"] == 0
    assert row["read_error"] == "File not found"


def test_worker_unreadable_image_records_reader_reason(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "truncated.png")
    monkeypatch.setattr(
        tsvc, "verify_image_readable", lambda path: (False, "truncated file")
    )

    messages = _run_worker(_payload([image_id]))

    assert messages[-1]["status"] == "done"
    assert messages[-1]["errors"] == 1
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT read_error FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    assert row["read_error"] == "truncated file"


def test_worker_unreadable_progress_does_not_show_a_drive_path(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    """The ETA toast interpolates the same cause the row stores; neither may name a folder."""
    image_id = _add_image(tmp_path, "broken.png")
    pillow = r"cannot identify image file 'L:\OwnersLibrary\broken.png'"
    monkeypatch.setattr(
        tsvc, "verify_image_readable", lambda path: (False, pillow)
    )

    messages = _run_worker(_payload([image_id]))

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT read_error FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    stored = row["read_error"] or ""
    assert "broken.png" in stored
    assert re.search(r"[A-Za-z]:\\", stored) is None, stored

    skipped = [m["message"] for m in messages if "Skipped unreadable image" in (m.get("message") or "")]
    assert skipped, "the worker did not emit the unreadable progress toast"
    for message in skipped:
        assert "broken.png" in message
        assert re.search(r"[A-Za-z]:\\", message) is None, message
        assert "OwnersLibrary" not in message


def test_worker_filters_nonexistent_image_ids_from_total(
    fake_tagger_env, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "real_one.png")

    messages = _run_worker(_payload([image_id, 999_999]))

    loaded = next(m for m in messages if m["message"].startswith("Model loaded"))
    assert loaded["total"] == 1
    terminal = messages[-1]
    assert terminal["status"] == "done"
    assert terminal["current"] == 1
    assert terminal["tagged"] == 1
    assert terminal["message"] == "Completed! Processed 1 images: 1 tagged."


def test_worker_pre_tag_blacklist_drops_rows_before_write(
    fake_tagger_env, tmp_path: Path
) -> None:
    image_id = _add_image(tmp_path, "black_pin.png")

    messages = _run_worker(_payload([image_id], pre_tag_blacklist=["e2e_fixture"]))

    tags = {t["tag"] for t in db.get_image_tags(image_id)}
    assert tags == {"black pin", "general"}
    # The stats counter sees the FILTERED rows, so the blacklisted tag never
    # reaches the post-run modal either.
    top_tags = {row["tag"] for row in messages[-1]["last_run_stats"]["top_tags"]}
    assert "e2e_fixture" not in top_tags
    assert "black pin" in top_tags


def test_worker_max_tags_trim_keeps_rating_row(fake_tagger_env, tmp_path: Path) -> None:
    """max_tags_per_image caps CONTENT rows by confidence; the rating verdict
    row is exempt (BE-3) so the image never reads as unrated downstream."""
    image_id = _add_image(tmp_path, "trim_me.png")

    _run_worker(_payload([image_id], max_tags_per_image=1))

    rows = db.get_image_tags(image_id)
    by_tag = {row["tag"]: row for row in rows}
    # e2e_fixture (0.99) outranks the stem tag (0.88); rating row survives.
    assert set(by_tag) == {"e2e_fixture", "general"}
    assert by_tag["general"]["category"] == "rating"


def test_worker_writes_tag_scores_with_model_in_same_transaction(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    """BE-1: raw (pre-filter) score rows ride the tag write, keyed by the
    effective model name, INCLUDING the sub-threshold row thresholding
    removed from the tag rows."""
    monkeypatch.setattr(config, "TAG_SCORES_ENABLED", True)
    image_id = _add_image(tmp_path, "scored.png")

    _run_worker(_payload([image_id]))

    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT model, tag, score FROM tag_scores WHERE image_id = ? ORDER BY tag",
            (image_id,),
        ).fetchall()
    scores = {row["tag"]: (row["model"], row["score"]) for row in rows}
    assert scores["e2e_low_conf"] == ("wd-swinv2-tagger-v3", 0.18)
    assert "e2e_fixture" in scores
    # The sub-threshold row exists ONLY in tag_scores, not as a tag row.
    assert "e2e_low_conf" not in {t["tag"] for t in db.get_image_tags(image_id)}


def test_worker_blacklist_does_not_censor_tag_scores(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    """Blacklist / max_tags shape only the ROWS — the score audit trail keeps
    what the model actually saw."""
    monkeypatch.setattr(config, "TAG_SCORES_ENABLED", True)
    image_id = _add_image(tmp_path, "audit_truth.png")

    _run_worker(_payload([image_id], pre_tag_blacklist=["e2e_fixture"]))

    assert "e2e_fixture" not in {t["tag"] for t in db.get_image_tags(image_id)}
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT tag FROM tag_scores WHERE image_id = ?", (image_id,)
        ).fetchall()
    assert "e2e_fixture" in {row["tag"] for row in rows}


def test_worker_omits_tag_scores_when_disabled(
    fake_tagger_env, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "TAG_SCORES_ENABLED", False)
    image_id = _add_image(tmp_path, "unscored.png")

    _run_worker(_payload([image_id]))

    assert db.get_image_tags(image_id) != []
    with db.get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM tag_scores WHERE image_id = ?", (image_id,)
        ).fetchone()[0]
    assert count == 0


def test_worker_default_path_processes_only_untagged_images(
    fake_tagger_env, tmp_path: Path
) -> None:
    tagged_id = _add_image(tmp_path, "already_tagged.png")
    untagged_id = _add_image(tmp_path, "still_fresh.png")
    db.add_tags(tagged_id, [{"tag": "old_row", "confidence": 0.5}])
    assert db.count_untagged_image_ids() == 1

    messages = _run_worker(_payload(None, retag_all=False))

    terminal = messages[-1]
    assert terminal["total"] == 1
    assert terminal["tagged"] == 1
    assert {t["tag"] for t in db.get_image_tags(tagged_id)} == {"old_row"}
    assert "e2e_fixture" in {t["tag"] for t in db.get_image_tags(untagged_id)}


def test_worker_retag_all_revisits_already_tagged_images(
    fake_tagger_env, tmp_path: Path
) -> None:
    tagged_id = _add_image(tmp_path, "retag_me.png")
    untagged_id = _add_image(tmp_path, "new_too.png")
    db.add_tags(tagged_id, [{"tag": "old_row", "confidence": 0.5}])

    messages = _run_worker(_payload(None, retag_all=True))

    terminal = messages[-1]
    assert terminal["total"] == 2
    assert terminal["tagged"] == 2
    assert "e2e_fixture" in {t["tag"] for t in db.get_image_tags(tagged_id)}
    assert "e2e_fixture" in {t["tag"] for t in db.get_image_tags(untagged_id)}


def test_worker_pipeline_replace_scope_preserves_manual_rows(
    fake_tagger_env, tmp_path: Path
) -> None:
    """Provenance contract (migration 024 era): worker writes go through
    add_tags_batch(default_source='tagger', replace_scope='pipeline'), so a
    re-tag replaces earlier PIPELINE rows but never user-managed manual rows."""
    image_id = _add_image(tmp_path, "keep_manual.png")
    db.add_tags_batch(
        [{"image_id": image_id, "tags": [{"tag": "my_manual", "confidence": 1.0}]}],
        default_source="manual",
    )
    # Seed the stale pipeline row with pipeline scope so the seeding itself
    # does not wipe the manual row (replace_scope defaults to "all").
    db.add_tags_batch(
        [{"image_id": image_id, "tags": [{"tag": "old_pipeline", "confidence": 0.6}]}],
        default_source="tagger",
        replace_scope="pipeline",
    )

    _run_worker(_payload([image_id]))

    rows = {row["tag"]: row for row in db.get_image_tags(image_id)}
    assert "my_manual" in rows
    assert rows["my_manual"]["source"] == "manual"
    assert "old_pipeline" not in rows
    assert rows["e2e_fixture"]["source"] == "tagger"


def test_worker_terminal_done_carries_last_run_stats_snapshot(
    fake_tagger_env, tmp_path: Path
) -> None:
    for name in ("stats_a.png", "stats_b.png"):
        _add_image(tmp_path, name)

    messages = _run_worker(_payload(None, retag_all=True))

    stats = messages[-1]["last_run_stats"]
    assert set(stats.keys()) == {
        "elapsed_seconds",
        "total_processed",
        "total_tagged",
        "total_errors",
        "avg_tags_per_image",
        "top_tags",
    }
    assert stats["total_processed"] == 2
    assert stats["total_tagged"] == 2
    assert stats["total_errors"] == 0
    # QUIRK (also pinned in test_tag_last_run_stats): despite its name,
    # avg_tags_per_image is total_tagged / total_processed — the tagged-image
    # RATIO (2/2 here), not the tag-row count per image.
    assert stats["avg_tags_per_image"] == 1.0
    top = {row["tag"]: row["count"] for row in stats["top_tags"]}
    assert top["e2e_fixture"] == 2
    # Only the terminal payload carries the stats key.
    assert all("last_run_stats" not in m for m in messages[:-1])


# ===========================================================================
# export_tags — backup JSON shape.
# ===========================================================================


def test_export_tags_round_trips_colon_tags_and_skips_untagged(
    test_db, tmp_path: Path
) -> None:
    tagged_id = _add_image(tmp_path, "colon_tag.png")
    _add_image(tmp_path, "never_tagged.png")
    db.add_tags(tagged_id, [{"tag": "ratio:16:9", "confidence": 0.75}])

    exported = TaggingService().export_tags()

    assert exported["version"] == "1.0"
    assert exported["count"] == 1
    row = exported["images"][0]
    assert row["filename"] == "colon_tag.png"
    # rsplit(':', 1) keeps colons INSIDE the tag name intact.
    assert row["tags"] == [{"tag": "ratio:16:9", "confidence": 0.75}]


def test_export_tags_includes_tagged_image_with_zero_tag_rows(
    test_db, tmp_path: Path
) -> None:
    """tagged_at (not tag rows) is the export predicate: a tagged image whose
    rows were all deleted still exports, with an empty tags list."""
    image_id = _add_image(tmp_path, "rows_deleted.png")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE images SET tagged_at = CURRENT_TIMESTAMP WHERE id = ?", (image_id,)
        )
        conn.commit()

    exported = TaggingService().export_tags()
    assert exported["count"] == 1
    assert exported["images"][0]["tags"] == []


# ===========================================================================
# import_tags — filename fallback + _normalize_import_tags matrix.
# ===========================================================================


def test_import_tags_falls_back_to_filename_match_when_path_unknown(
    test_db, tmp_path: Path
) -> None:
    image_path = tmp_path / "fallback_name.png"
    Image.new("RGB", (16, 16), color="white").save(image_path)
    image_id = db.add_image(str(image_path), image_path.name, metadata_json="{}")

    result = TaggingService().import_tags(
        TagImportRequest(
            images=[
                {
                    "path": "/machine-b/other-root/fallback_name.png",
                    "filename": "fallback_name.png",
                    "tags": [{"tag": "found_by_name", "confidence": 0.8}],
                }
            ],
            overwrite=False,
        )
    )

    assert result == {"imported": 1, "skipped": 0}
    assert {t["tag"] for t in db.get_image_tags(image_id)} == {"found_by_name"}


def test_normalize_import_tags_matrix() -> None:
    normalize = TaggingService._normalize_import_tags
    assert normalize(None) == []
    assert normalize(["not-a-dict", 42]) == []
    assert normalize([{"tag": "   "}]) == []
    # Confidence coercion: numeric strings parse, junk/None fall back to 0.5.
    assert normalize([{"tag": "a", "confidence": "0.7"}]) == [
        {"tag": "a", "confidence": 0.7}
    ]
    assert normalize([{"tag": "a", "confidence": "junk"}]) == [
        {"tag": "a", "confidence": 0.5}
    ]
    assert normalize([{"tag": "a", "confidence": None}]) == [
        {"tag": "a", "confidence": 0.5}
    ]
    assert normalize([{"tag": "a"}]) == [{"tag": "a", "confidence": 0.5}]
    # Duplicate tags dedupe with LAST-write-wins confidence.
    assert normalize(
        [{"tag": "a", "confidence": 0.2}, {"tag": "a", "confidence": 0.9}]
    ) == [{"tag": "a", "confidence": 0.9}]
    # Names are stripped before dedup.
    assert normalize([{"tag": "  b  ", "confidence": 0.3}]) == [
        {"tag": "b", "confidence": 0.3}
    ]


# ===========================================================================
# fix_rating_tags.
# ===========================================================================


def _insert_tag_row(image_id: int, tag: str, confidence: float, category=None) -> None:
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO tags (image_id, tag, confidence, category) VALUES (?, ?, ?, ?)",
            (image_id, tag, confidence, category),
        )
        conn.commit()


def test_fix_rating_tags_keeps_highest_confidence_and_counts_images(test_db) -> None:
    image_a = db.add_image(path="/test/fix_a.png", filename="fix_a.png")
    image_b = db.add_image(path="/test/fix_b.png", filename="fix_b.png")
    image_c = db.add_image(path="/test/fix_c.png", filename="fix_c.png")
    _insert_tag_row(image_a, "general", 0.9)
    _insert_tag_row(image_a, "sensitive", 0.7)
    _insert_tag_row(image_a, "explicit", 0.2)
    _insert_tag_row(image_b, "questionable", 0.8)
    _insert_tag_row(image_b, "explicit", 0.6)
    _insert_tag_row(image_c, "general", 0.9)  # single rating: untouched

    result = TaggingService().fix_rating_tags()

    # images_fixed counts IMAGES, not deleted rows (3 rows were removed).
    assert result == {
        "status": "ok",
        "images_fixed": 2,
        "message": "Cleaned up rating tags for 2 images",
    }
    assert {
        t["tag"] for t in db.get_image_tags(image_a) if t["tag"] in RATING_NAMES
    } == {"general"}
    assert {
        t["tag"] for t in db.get_image_tags(image_b) if t["tag"] in RATING_NAMES
    } == {"questionable"}
    assert {t["tag"] for t in db.get_image_tags(image_c)} == {"general"}


def test_quirk_fix_rating_tags_matches_names_regardless_of_category(test_db) -> None:
    """QUIRK: the cleanup matches on tag NAME only — a content tag that
    happens to be named 'sensitive' (category general) is treated as a
    competing rating row and deleted when a higher-confidence rating exists."""
    image_id = db.add_image(path="/test/fix_quirk.png", filename="fix_quirk.png")
    _insert_tag_row(image_id, "general", 0.99, category="rating")
    _insert_tag_row(image_id, "sensitive", 0.7, category="general")
    _insert_tag_row(image_id, "1girl", 0.9, category="general")

    result = TaggingService().fix_rating_tags()

    assert result["images_fixed"] == 1
    remaining = {t["tag"] for t in db.get_image_tags(image_id)}
    assert remaining == {"general", "1girl"}


# ===========================================================================
# start_export_bulk_job (Debt-22) — envelope + wiring, export engine faked.
# ===========================================================================


def test_start_export_bulk_job_envelope_and_result_mapping(
    test_db, monkeypatch
) -> None:
    captured = {}

    def fake_export(
        request, id_chunks=None, total=None, progress_callback=None, cancel_check=None
    ):
        captured["id_chunks"] = id_chunks
        captured["total"] = total
        captured["cancelled_seen"] = cancel_check()
        progress_callback({"processed": 2, "total": total})
        return {
            "exported": 2,
            "skipped": 1,
            "error_count": 0,
            "error_messages": [],
            "total": total,
            "content_mode": "tags",
            "overwrite_policy": "unique",
            "output_mode": "folder",
            "nl_sidecars_written": 0,
            "validation": None,
        }

    monkeypatch.setattr(tsvc, "export_tags_batch_request", fake_export)
    service = TaggingService()
    background_tasks = BackgroundTasks()

    envelope = service.start_export_bulk_job(
        BatchTagExportRequest(image_ids=[1, 2, 3], output_folder="unused"),
        background_tasks,
    )

    assert envelope["operation"] == "export"
    assert envelope["status"] == "queued"
    assert envelope["total"] == 3
    job_id = envelope["job_id"]
    assert len(background_tasks.tasks) == 1

    # Execute the queued FastAPI background task synchronously.
    task = background_tasks.tasks[0]
    task.func(*task.args, **task.kwargs)

    assert captured["id_chunks"] is None  # explicit ids: no token iterator
    assert captured["total"] == 3
    assert captured["cancelled_seen"] is False

    from services.bulk_job_service import get_bulk_job_service

    job = get_bulk_job_service().get_job(job_id)
    assert job["status"] == "done"
    assert job["processed"] == 2  # streamed through the progress callback
    # skipped>0 with no errors maps to the 'partial' status contract.
    assert job["result"]["status"] == "partial"
    assert job["result"]["exported"] == 2
    assert job["result"]["skipped"] == 1
    assert job["result"]["output_mode"] == "folder"


def test_start_export_bulk_job_snapshots_selection_token_chunks(
    test_db, monkeypatch
) -> None:
    """Token selections are snapshotted server-side (snapshot=True) so the
    export cannot chase a live, mutating selection."""
    captured = {}

    def fake_iter(token, snapshot=False):
        captured["token"] = token
        captured["snapshot"] = snapshot
        return iter([[7, 8]])

    def fake_export(
        request, id_chunks=None, total=None, progress_callback=None, cancel_check=None
    ):
        captured["chunks"] = list(id_chunks)
        return {"exported": 2, "skipped": 0, "error_count": 0, "total": total}

    monkeypatch.setattr(tsvc, "count_selection_token_ids", lambda token: 2)
    monkeypatch.setattr(tsvc, "iter_selection_token_id_chunks", fake_iter)
    monkeypatch.setattr(tsvc, "export_tags_batch_request", fake_export)

    service = TaggingService()
    background_tasks = BackgroundTasks()
    envelope = service.start_export_bulk_job(
        BatchTagExportRequest(selection_token="tok-1", output_folder="unused"),
        background_tasks,
    )
    assert envelope["total"] == 2

    task = background_tasks.tasks[0]
    task.func(*task.args, **task.kwargs)

    assert captured["token"] == "tok-1"
    assert captured["snapshot"] is True
    assert captured["chunks"] == [[7, 8]]

    from services.bulk_job_service import get_bulk_job_service

    job = get_bulk_job_service().get_job(envelope["job_id"])
    assert job["status"] == "done"
    assert job["result"]["status"] == "ok"
