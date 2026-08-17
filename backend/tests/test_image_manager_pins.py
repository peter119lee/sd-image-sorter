"""Characterization pins for image_manager.py (TIER-2 step 0).

image_manager is a STATELESS bag of free functions (no class, no instance
state, and — verified — no ``global`` writers; the only module state is
read-only constants). These pins lock the *uncovered* load-bearing seams the
existing test_image_manager.py suite does not reach — the two 0%-covered public
functions ``reparse_image_metadata`` (imported directly by image_service /
indexed_file_mutation_service / metadata_repair_service) and ``batch_move``
(the router move path), the shared ``_prepare_destination_path`` unique-name
allocator, the ``_parse_metadata_job`` error taxonomy, the process-kill
executor-termination fallback (the 80k+ bounded-worker safety), the pure
metadata/fingerprint helpers, and the record builders — plus the SAFETY
invariants the decomposition must preserve:

  * rescan preserves ``library_order_time`` even when pixels change
    (the date-filter foundation);
  * an error / timeout record NEVER drops the row (full key set, status="error");
  * ``_metadata_backlog_limit`` stays bounded by SCAN_DB_BATCH_SIZE;
  * ``_cleanup_missing_scope_entries`` resolves indexed rows through
    ``image_manager.__file__`` (the backend-root/project-root math in
    utils/source_paths.py is anchored on this module's nesting depth — a split
    that moves this caller shifts both roots one level up).

No existing file is modified. Uses the tmp-DB ``test_db`` fixture and real PNGs;
never touches data/images.db. conftest forces the scan executor to "thread".
"""

import gzip
import json
from collections.abc import MutableMapping
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

import database as db
import image_manager
from image_manager import (
    PARSED_METADATA_VERSION,
    SCAN_DB_BATCH_SIZE,
    _build_metadata_error_record,
    _build_metadata_success_record,
    _build_placeholder_record,
    _chunked,
    _cleanup_missing_scope_entries,
    _compress_raw_metadata_text,
    _deserialize_loras,
    _is_unchanged_scan_hit,
    _metadata_backlog_limit,
    _metadata_executor_mode,
    _metadata_job_for_retry,
    _needs_metadata_parser_upgrade,
    _parse_metadata_job,
    _prepare_destination_path,
    _should_compute_content_fingerprint,
    _source_fingerprint_matches,
    _stored_parsed_metadata_version,
    _terminate_metadata_executor_workers,
    batch_move,
    get_folder_stats,
    parse_metadata_job,
    reparse_image_metadata,
)
from exceptions import FileOperationError, ScanCancelledError


def _png(path: Path, color: str = "white", size=(64, 64)) -> Path:
    Image.new("RGB", size, color=color).save(path)
    return path


def _webp(path: Path, exif: bytes | None) -> Path:
    image = Image.new("RGB", (32, 24), color="navy")
    if exif is None:
        image.save(path, format="WEBP")
    else:
        image.save(path, format="WEBP", exif=exif)
    with Image.open(path) as stored:
        stored.verify()
    return path


# ---------------------------------------------------------------------------
# Pure helpers (no DB, no filesystem)
# ---------------------------------------------------------------------------
class TestPureHelpers:
    def test_deserialize_loras_covers_every_arm(self):
        assert _deserialize_loras(None) is None
        assert _deserialize_loras(["a", "b"]) == ["a", "b"]
        assert _deserialize_loras('["x", "y"]') == ["x", "y"]
        assert _deserialize_loras("not-json") is None
        assert _deserialize_loras('{"not": "a list"}') is None
        assert _deserialize_loras(12345) is None

    def test_metadata_backlog_limit_is_bounded_by_batch_size(self):
        # 80k+ safety: the queue never grows past SCAN_DB_BATCH_SIZE regardless
        # of worker count. sorting/scan.py reads this exact helper for its
        # startup log — the values are a cross-module contract.
        assert _metadata_backlog_limit(1) == 16  # min-backlog floor
        assert _metadata_backlog_limit(8) == 32  # per-worker * 4
        assert _metadata_backlog_limit(64) == SCAN_DB_BATCH_SIZE  # capped at 200
        assert _metadata_backlog_limit(0) == 16  # falsy -> treated as 1

    def test_metadata_job_for_retry_drops_submitted_at_without_mutating(self):
        job = {"path": "p", "filename": "f", "submitted_at": 123.0}
        retry = _metadata_job_for_retry(job)
        assert "submitted_at" not in retry
        assert retry["path"] == "p" and retry["filename"] == "f"
        # original untouched (immutability of the source job)
        assert job["submitted_at"] == 123.0

    def test_chunked_yields_fixed_batches_plus_partial_tail(self):
        batches = list(_chunked(iter(range(7)), 3))
        assert batches == [[0, 1, 2], [3, 4, 5], [6]]
        assert list(_chunked(iter([]), 3)) == []

    def test_metadata_executor_mode_maps_aliases(self, monkeypatch):
        monkeypatch.setattr(image_manager, "SCAN_METADATA_EXECUTOR_MODE", "process")
        assert _metadata_executor_mode() == "process"
        monkeypatch.setattr(image_manager, "SCAN_METADATA_EXECUTOR_MODE", "threads")
        assert _metadata_executor_mode() == "thread"
        monkeypatch.setattr(image_manager, "SCAN_METADATA_EXECUTOR_MODE", "banana")
        assert _metadata_executor_mode() == "process"  # unknown -> process isolation

    def test_stored_parsed_metadata_version_covers_shapes(self):
        assert _stored_parsed_metadata_version(None) is None
        assert (
            _stored_parsed_metadata_version(
                {"metadata_json": json.dumps({"_parsed": {"version": 5}})}
            )
            == 5
        )
        # bytes payload is decoded before JSON parsing
        assert (
            _stored_parsed_metadata_version(
                {"metadata_json": json.dumps({"_parsed": {"version": 6}}).encode()}
            )
            == 6
        )
        # dict payload (already-decoded)
        assert (
            _stored_parsed_metadata_version(
                {"metadata_json": {"_parsed": {"version": 4}}}
            )
            == 4
        )
        assert _stored_parsed_metadata_version({"metadata_json": "not json"}) is None
        assert (
            _stored_parsed_metadata_version({"metadata_json": json.dumps({})}) is None
        )
        assert (
            _stored_parsed_metadata_version(
                {"metadata_json": json.dumps({"_parsed": {"version": "x"}})}
            )
            is None
        )

    def test_needs_metadata_parser_upgrade_revisits_every_indexed_format(self):
        """Any indexed format parsed below the current version must be revisited.

        Regression: the required-version map pinned JPEG at a literal 7 and
        listed no other extension, so once PARSED_METADATA_VERSION reached 8
        a JPEG or WebP row indexed by the v7 parser became permanently
        unreachable — every later parser improvement, including the
        format-independent caption-sidecar fallback that makes those files'
        prompts readable, could never be applied to it. 2,682 rows in the
        owner's library were frozen promptless this way.
        """
        stale = json.dumps({"_parsed": {"version": PARSED_METADATA_VERSION - 1}})
        current = json.dumps({"_parsed": {"version": PARSED_METADATA_VERSION}})
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
            assert (
                _needs_metadata_parser_upgrade(
                    {"path": f"a{ext}", "metadata_json": stale}
                )
                is True
            ), f"{ext} parsed by an older parser must be re-parsed"
            assert (
                _needs_metadata_parser_upgrade(
                    {"path": f"a{ext}", "metadata_json": current}
                )
                is False
            ), f"{ext} already at the current parser version must not churn"

        # A row with no recorded parser version predates the stamp -> upgrade.
        assert (
            _needs_metadata_parser_upgrade({"path": "a.jpg", "metadata_json": "{}"})
            is True
        )
        # Files the scanner never indexes stay exempt.
        assert (
            _needs_metadata_parser_upgrade({"path": "a.txt", "metadata_json": stale})
            is False
        )

    def test_compress_raw_metadata_text_roundtrips_and_fails_soft(self):
        blob = _compress_raw_metadata_text('{"prompt": "hi"}')
        assert isinstance(blob, bytes)
        assert json.loads(gzip.decompress(blob).decode("utf-8")) == {"prompt": "hi"}
        # unusable input never raises -> None (optional retention feature)
        assert _compress_raw_metadata_text("") is None
        assert _compress_raw_metadata_text("   ") is None
        assert _compress_raw_metadata_text(None) is None
        assert _compress_raw_metadata_text(12345) is None


# ---------------------------------------------------------------------------
# Fingerprint / unchanged-hit gates (need a real file stat)
# ---------------------------------------------------------------------------
class TestFingerprintGates:
    def _existing_from(self, path: Path, **overrides):
        st = path.stat()
        base = {
            "is_readable": 1,
            "metadata_status": "complete",
            "content_fingerprint": "fp",
            "source_mtime_ns": st.st_mtime_ns,
            "source_size": st.st_size,
            "path": str(path),
            "metadata_json": json.dumps({
                "_parsed": {"version": PARSED_METADATA_VERSION}
            }),
        }
        base.update(overrides)
        return base, st

    def test_source_fingerprint_matches(self, tmp_path):
        p = _png(tmp_path / "fp.png")
        existing, st = self._existing_from(p)
        assert _source_fingerprint_matches(existing, st) is True
        assert (
            _source_fingerprint_matches({**existing, "source_size": 999999}, st)
            is False
        )
        assert _source_fingerprint_matches(None, st) is False
        assert _source_fingerprint_matches({"source_mtime_ns": "x"}, st) is False

    def test_is_unchanged_scan_hit_gate(self, tmp_path):
        p = _png(tmp_path / "hit.png")
        existing, st = self._existing_from(p)
        assert _is_unchanged_scan_hit(existing, st) is True
        # pending metadata disqualifies
        assert (
            _is_unchanged_scan_hit({**existing, "metadata_status": "pending"}, st)
            is False
        )
        # unreadable disqualifies
        assert _is_unchanged_scan_hit({**existing, "is_readable": 0}, st) is False
        # derived state present but no content fingerprint -> needs backfill -> not a hit
        needs_backfill = {
            **existing,
            "content_fingerprint": None,
            "tagged_at": "2026-01-01",
        }
        assert _is_unchanged_scan_hit(needs_backfill, st) is False

    def test_should_compute_content_fingerprint(self, tmp_path):
        assert _should_compute_content_fingerprint(None) is False
        assert (
            _should_compute_content_fingerprint({"content_fingerprint": "fp"}) is True
        )
        # derived state without fingerprint -> must backfill
        assert (
            _should_compute_content_fingerprint(
                {"content_fingerprint": None, "ai_caption": "cap"}
            )
            is True
        )
        assert (
            _should_compute_content_fingerprint({"content_fingerprint": None}) is False
        )


# ---------------------------------------------------------------------------
# Record builders — DB-write contract + SAFETY invariants
# ---------------------------------------------------------------------------
class TestRecordBuilders:
    def test_placeholder_preserves_library_order_time_even_when_pixels_change(
        self, tmp_path
    ):
        """[SAFETY] library_order_time is the date-filter foundation: it must
        survive a rescan even when the fingerprint no longer matches (pixels
        changed), so first-seen ordering never jumps to the new mtime."""
        p = _png(tmp_path / "order.png")
        st = p.stat()
        first_seen = datetime(2020, 1, 1, 0, 0, 0)
        existing = {
            "is_readable": 1,
            "library_order_time": first_seen,
            "created_at": first_seen,
            # fingerprint that will NOT match the real file -> pixels "changed"
            "source_mtime_ns": st.st_mtime_ns + 999,
            "source_size": st.st_size + 999,
            "content_fingerprint": "stale",
        }
        record = _build_placeholder_record(str(p), p.name, st, existing)
        assert record["library_order_time"] == first_seen
        assert record["created_at"] == first_seen
        # but the source file mtime tracks the *current* file
        assert record["source_file_mtime"] == datetime.fromtimestamp(st.st_mtime)
        assert record["metadata_status"] == "pending"

    def test_placeholder_unknown_record_when_no_existing_row(self, tmp_path):
        p = _png(tmp_path / "fresh.png")
        st = p.stat()
        record = _build_placeholder_record(str(p), p.name, st, None)
        assert record["generator"] == "unknown"
        assert record["loras"] == []
        assert record["is_readable"] is True
        # brand-new row seeds library_order_time from the file mtime
        assert record["library_order_time"] == datetime.fromtimestamp(st.st_mtime)

    def test_metadata_success_record_shape_and_raw_compression(self, tmp_path):
        p = _png(tmp_path / "ok.png")
        st = p.stat()
        metadata = {
            "generator": "comfyui",
            "prompt": "1girl",
            "negative_prompt": "lowres",
            "width": 64,
            "height": 64,
            "checkpoint": "model.safetensors",
            "loras": ["lora_a"],
            "metadata": {"_parsed": {"generation_params": {"model_hash": "abc123"}}},
            "raw_metadata_text": '{"prompt": "raw"}',
        }
        rec = _build_metadata_success_record(
            str(p), p.name, st, metadata, content_fingerprint="cf"
        )
        assert rec["metadata_status"] == "complete"
        assert rec["is_readable"] is True
        assert rec["model_hash"] == "abc123"
        assert rec["content_fingerprint"] == "cf"
        assert rec["source_mtime_ns"] == st.st_mtime_ns
        # raw envelope is gzipped into raw_metadata_gz (L3 retention)
        assert json.loads(gzip.decompress(rec["raw_metadata_gz"]).decode()) == {
            "prompt": "raw"
        }

    def test_metadata_success_record_preserves_nonfatal_metadata_error(self, tmp_path):
        p = _png(tmp_path / "metadata-warning.png")
        metadata_error = "WebP EXIF chunk could not be parsed: invalid TIFF header"
        metadata = {
            "generator": "unknown",
            "prompt": None,
            "negative_prompt": None,
            "width": 32,
            "height": 24,
            "checkpoint": None,
            "loras": [],
            "metadata": {},
            "metadata_error": metadata_error,
        }

        record = _build_metadata_success_record(
            str(p),
            p.name,
            p.stat(),
            metadata,
            content_fingerprint=None,
        )

        assert record["is_readable"] is True
        assert record["width"] == 32
        assert record["height"] == 24
        assert record["metadata_status"] == "error"
        assert record["read_error"] == metadata_error

    def test_metadata_error_record_never_drops_the_row(self):
        """[SAFETY] timeout/permission/OS failures still produce a COMPLETE row
        (status='error', is_readable=False) — the file is never silently
        dropped from the library. Null stat (file vanished) is tolerated."""
        rec = _build_metadata_error_record("/x/gone.png", "gone.png", None, "timed out")
        assert rec["metadata_status"] == "error"
        assert rec["is_readable"] is False
        assert rec["read_error"] == "timed out"
        assert rec["source_mtime_ns"] is None and rec["source_size"] is None
        # full row shape is preserved (a superset of the add_images_batch keys)
        for key in (
            "path",
            "filename",
            "generator",
            "prompt",
            "metadata_json",
            "loras",
        ):
            assert key in rec


# ---------------------------------------------------------------------------
# _parse_metadata_job error taxonomy + public wrapper
# ---------------------------------------------------------------------------
class TestParseMetadataJob:
    def test_missing_file_is_os_error_record(self):
        result = _parse_metadata_job(
            {"path": "/does/not/exist.png", "filename": "x.png"}
        )
        assert result["error"]["kind"] == "os_error"
        assert result["generator"] is None
        assert result["record"]["metadata_status"] == "error"

    def test_no_dimensions_is_unreadable(self, tmp_path, monkeypatch):
        p = _png(tmp_path / "nodims.png")
        monkeypatch.setattr(
            image_manager,
            "parse_image",
            lambda *a, **k: {"parse_error": None, "width": 0, "height": 0},
        )
        result = _parse_metadata_job({"path": str(p), "filename": p.name})
        assert result["error"]["kind"] == "unreadable"
        assert "no dimensions" in result["error"]["error"].lower()

    def test_parse_error_passthrough(self, tmp_path, monkeypatch):
        p = _png(tmp_path / "boom.png")
        monkeypatch.setattr(
            image_manager,
            "parse_image",
            lambda *a, **k: {"parse_error": "corrupt chunk"},
        )
        result = _parse_metadata_job({"path": str(p), "filename": p.name})
        assert result["error"]["kind"] == "unreadable"
        assert result["error"]["error"] == "corrupt chunk"

    def test_public_wrapper_delegates_to_underscored(self):
        # parse_metadata_job is the cross-module-safe name used by
        # SortingService.import_uploaded_files; it must mirror _parse_metadata_job.
        job = {"path": "/nope.png", "filename": "nope.png"}
        assert (
            parse_metadata_job(job)["error"]["kind"]
            == _parse_metadata_job(job)["error"]["kind"]
        )


# ---------------------------------------------------------------------------
# reparse_image_metadata — imported by 3 services, was 0% covered
# ---------------------------------------------------------------------------
class TestReparseImageMetadata:
    def test_success_maps_fields_and_marks_complete(self, tmp_path, monkeypatch):
        p = _png(tmp_path / "reparse-ok.png")
        captured = {}
        monkeypatch.setattr(
            image_manager,
            "parse_image",
            lambda *a, **k: {
                "parse_error": None,
                "generator": "webui",
                "prompt": "a cat",
                "negative_prompt": "dog",
                "width": 64,
                "height": 64,
                "file_size": 111,
                "checkpoint": "ck.safetensors",
                "loras": [],
                "metadata": {},
            },
        )
        monkeypatch.setattr(
            image_manager, "update_image_metadata", lambda **kw: captured.update(kw)
        )
        out = reparse_image_metadata(42, str(p))
        assert out["generator"] == "webui"
        assert captured["generator"] == "webui"
        assert captured["prompt"] == "a cat"
        assert captured["metadata_status"] == "complete"
        assert captured["is_readable"] is True
        assert captured["preserve_derived_state"] is False
        assert (
            captured["content_fingerprint"] is not None
        )  # computed from the real file

    def test_parse_error_marks_error_and_threads_preserve_flag(
        self, tmp_path, monkeypatch
    ):
        p = _png(tmp_path / "reparse-bad.png")
        captured = {}
        monkeypatch.setattr(
            image_manager, "parse_image", lambda *a, **k: {"parse_error": "unreadable"}
        )
        monkeypatch.setattr(
            image_manager, "update_image_metadata", lambda **kw: captured.update(kw)
        )
        reparse_image_metadata(7, str(p), preserve_derived_state=True)
        assert captured["metadata_status"] == "error"
        assert captured["is_readable"] is False
        assert captured["read_error"] == "unreadable"
        # the caller's preserve_derived_state choice reaches the DB layer on both arms
        assert captured["preserve_derived_state"] is True

    def test_corrupt_webp_exif_remains_readable_after_reparse(self, tmp_path, test_db):
        image_path = _webp(tmp_path / "reparse-corrupt-exif.webp", b"not-valid-tiff-exif")
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            generator="unknown",
            width=32,
            height=24,
            file_size=image_path.stat().st_size,
            metadata_json="{}",
        )

        result = reparse_image_metadata(image_id, str(image_path), preserve_derived_state=False)
        stored = db.get_image_by_id(image_id)

        assert result["parse_error"] is None
        assert result["metadata_error"].startswith(
            "WebP EXIF chunk could not be parsed:"
        )
        assert stored is not None
        assert stored["is_readable"] == 1
        assert stored["width"] == 32
        assert stored["height"] == 24
        assert stored["metadata_status"] == "error"
        assert stored["read_error"] == result["metadata_error"]


# ---------------------------------------------------------------------------
# _prepare_destination_path + batch_move
# ---------------------------------------------------------------------------
class TestMoveCopyDestination:
    def test_prepare_destination_unique_increments_on_conflict(self, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        _png(dest / "a.png")  # occupy the target name
        src = _png(
            tmp_path / "src-a.png"
        )  # different source, same basename? no -> rename src
        src2 = tmp_path / "a.png"
        _png(src2)
        _, new_path = _prepare_destination_path(str(src2), str(dest), "move")
        assert Path(new_path).name == "a_1.png"

    def test_prepare_destination_copy_forces_increment_even_for_same_path(
        self, tmp_path
    ):
        dest = tmp_path / "dst2"
        dest.mkdir()
        existing = _png(dest / "same.png")
        # image_path == the existing target, but a copy must not clobber it
        _, new_path = _prepare_destination_path(str(existing), str(dest), "copy")
        assert Path(new_path).name == "same_1.png"

    def test_batch_move_moves_all_and_reports_new_paths(self, test_db, tmp_path):
        src = tmp_path / "bm-src"
        dest = tmp_path / "bm-dest"
        src.mkdir()
        dest.mkdir()
        ids, paths = [], []
        for i in range(2):
            p = _png(src / f"m{i}.png")
            ids.append(db.add_image(path=str(p), filename=p.name))
            paths.append(str(p))
        result = batch_move(ids, paths, str(dest))
        assert result["total"] == 2
        assert result["moved"] == 2
        assert result["errors"] == 0
        assert len(result["new_paths"]) == 2
        for i in range(2):
            assert (dest / f"m{i}.png").exists()
            assert not (src / f"m{i}.png").exists()

    def test_batch_move_counts_errors_for_missing_source(self, test_db, tmp_path):
        src = tmp_path / "bm2-src"
        dest = tmp_path / "bm2-dest"
        src.mkdir()
        dest.mkdir()
        good = _png(src / "good.png")
        good_id = db.add_image(path=str(good), filename=good.name)
        missing = str(src / "ghost.png")  # never created -> move raises
        result = batch_move([good_id, 999], [str(good), missing], str(dest))
        assert result["moved"] == 1
        assert result["errors"] == 1
        assert result["new_paths"] == [str(dest / "good.png")]

    def test_batch_move_invokes_progress_callback_per_item(self, test_db, tmp_path):
        src = tmp_path / "bm3-src"
        dest = tmp_path / "bm3-dest"
        src.mkdir()
        dest.mkdir()
        p = _png(src / "cb.png")
        pid = db.add_image(path=str(p), filename=p.name)
        seen = []
        batch_move(
            [pid],
            [str(p)],
            str(dest),
            progress_callback=lambda c, t, f: seen.append((c, t, f)),
        )
        assert seen == [(1, 1, "cb.png")]


# ---------------------------------------------------------------------------
# Executor termination fallback — the 80k+ stuck-worker kill path
# ---------------------------------------------------------------------------
class TestExecutorTermination:
    def test_uses_terminate_workers_hook_when_available(self):
        class HookExecutor:
            def __init__(self):
                self.calls = 0

            def terminate_workers(self):
                self.calls += 1

        ex = HookExecutor()
        assert _terminate_metadata_executor_workers(ex) is True
        assert ex.calls == 1

    def test_falls_back_to_processes_terminate_and_join(self):
        class FakeProc:
            def __init__(self):
                self._alive = True
                self.terminated = False
                self.joined = False

            def is_alive(self):
                return self._alive

            def terminate(self):
                self.terminated = True
                self._alive = False

            def join(self, timeout=None):
                self.joined = True

        procs = {0: FakeProc(), 1: FakeProc()}

        class ProcExecutor:
            _processes = procs  # a MutableMapping, no terminate_workers hook

        assert isinstance(ProcExecutor._processes, MutableMapping)
        assert _terminate_metadata_executor_workers(ProcExecutor()) is True
        assert all(p.terminated and p.joined for p in procs.values())

    def test_returns_false_without_any_kill_hook(self):
        class Plain:
            pass

        assert _terminate_metadata_executor_workers(Plain()) is False


# ---------------------------------------------------------------------------
# get_folder_stats
# ---------------------------------------------------------------------------
class TestFolderStats:
    def test_counts_images_by_extension_and_ignores_non_images(self, tmp_path):
        _png(tmp_path / "a.png")
        _png(tmp_path / "b.png")
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
        stats = get_folder_stats(str(tmp_path))
        assert stats["total_files"] == 2
        assert stats["by_extension"] == {".png": 2}
        assert stats["total_size"] > 0

    def test_max_files_bounds_the_walk(self, tmp_path):
        for i in range(5):
            _png(tmp_path / f"img{i}.png")
        # islice caps how many rglob entries are even visited
        bounded = get_folder_stats(str(tmp_path), max_files=1)
        assert bounded["total_files"] <= 1


# ---------------------------------------------------------------------------
# _cleanup_missing_scope_entries — the __file__ anchor seam (decomposition trap)
# ---------------------------------------------------------------------------
class TestCleanupMissingScopeSeam:
    def test_resolution_is_anchored_on_image_manager_dunder_file(
        self, test_db, tmp_path, monkeypatch
    ):
        """The relative-path resolution in utils/source_paths.py derives
        backend_root/project_root from ``dirname(dirname(abspath(backend_file)))``.
        image_manager passes its own ``__file__`` — so the module's nesting depth
        is load-bearing. Pin that the anchor is exactly image_manager.__file__;
        a split that relocates this caller shifts both roots and this fires."""
        p = _png(tmp_path / "scope.png")
        db.add_image(path=str(p), filename=p.name)
        captured = {}

        def fake_resolve(candidate, *, backend_file):
            captured["backend_file"] = backend_file
            return candidate  # pretend it still exists -> not removed

        monkeypatch.setattr(
            image_manager, "resolve_indexed_image_path_for_cleanup", fake_resolve
        )
        removed = _cleanup_missing_scope_entries(str(tmp_path), True)
        assert captured["backend_file"] == image_manager.__file__
        assert removed == 0  # resolvable row is kept

    def test_stop_requested_raises_scan_cancelled(self, test_db, tmp_path):
        p = _png(tmp_path / "cancel-scope.png")
        db.add_image(path=str(p), filename=p.name)
        with pytest.raises(ScanCancelledError):
            _cleanup_missing_scope_entries(
                str(tmp_path), True, stop_requested=lambda: True
            )


# ---------------------------------------------------------------------------
# move_image error surface (FileOperationError wrapping)
# ---------------------------------------------------------------------------
class TestMoveImageErrors:
    def test_move_missing_source_raises_file_operation_error(self, test_db, tmp_path):
        dest = tmp_path / "mv-dest"
        dest.mkdir()
        with pytest.raises(FileOperationError):
            image_manager.move_image(1, str(dest), str(tmp_path / "nope.png"))
