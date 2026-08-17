"""
Tests for sorting router endpoints.

Tests:
- POST /api/scan - Folder scanning
- GET /api/scan/progress - Scan progress
- POST /api/move - Image moving
- POST /api/batch-move - Batch move by filters
- POST /api/sort/* - Manual sort session

Priority: CRITICAL (file operations)
"""
import asyncio
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import BackgroundTasks

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _create_sort_image(tmp_path: Path, filename: str) -> Path:
    from PIL import Image

    image_path = tmp_path / filename
    Image.new("RGB", (64, 64), color="white").save(image_path)
    return image_path


@pytest.fixture
def isolated_sorting_service(tmp_path):
    """Use a fresh sorting service instance so progress state does not leak across tests.

    Also redirects ``db.DATABASE_PATH`` to a throwaway DB **unless** a DB-isolating
    fixture (e.g. ``test_db``) has already pointed it at a test path. This keeps
    scan side-effects — notably the v3.3.2 auto-register-library-root hook, which
    records a Library Root on scan completion — from leaking rows into the
    real ``data/images.db``. Uses the same ``test_``/``tmp`` path heuristic as
    ``test_tagging_service``'s production-DB guard.
    """
    import database as db
    from routers.sorting import set_sorting_service
    from services.sorting_service import SortingService

    original_path = db.DATABASE_PATH
    patched = False
    lowered = str(original_path).lower()
    if "test_" not in lowered and "tmp" not in lowered:
        db.DATABASE_PATH = str(tmp_path / "isolated_sorting.db")
        db.init_db()
        patched = True

    service = SortingService()
    set_sorting_service(service)
    try:
        yield service
    finally:
        set_sorting_service(SortingService())
        if patched:
            db.DATABASE_PATH = original_path


class TestRouterCompatibilityState:
    """Tests for legacy router compatibility shims delegating to service-owned state."""

    def test_lazy_compat_state_does_not_create_service_until_used(self):
        from routers import sorting as sorting_router
        from services.sorting_service import SortingService

        original_service = sorting_router._sorting_service_provider._instance
        try:
            sorting_router.set_sorting_service(None)
            sorting_router._bind_lazy_sorting_compat_state()

            assert sorting_router._sorting_service_provider._instance is None
            assert sorting_router.scan_progress.copy()["status"] == "idle"
            assert isinstance(sorting_router._sorting_service_provider._instance, SortingService)
        finally:
            sorting_router.set_sorting_service(original_service)

    def test_scan_progress_compat_helpers_delegate_to_service(self, isolated_sorting_service):
        from routers import sorting as sorting_router

        sorting_router.set_scan_progress_state({
            "status": "running",
            "current": 3,
            "total": 9,
            "message": "Scanning...",
        })

        assert isolated_sorting_service.get_scan_progress()["status"] == "running"
        assert sorting_router.scan_progress.copy()["current"] == 3

        sorting_router.scan_progress["message"] = "Compat update"

        assert isolated_sorting_service.get_scan_progress()["message"] == "Compat update"
        assert sorting_router.get_scan_progress_state()["message"] == "Compat update"

    def test_sort_session_compat_helpers_delegate_to_service(self, isolated_sorting_service):
        from routers import sorting as sorting_router

        sorting_router.set_sort_session({
            "active": True,
            "image_ids": [11, 22],
            "current_index": 0,
            "folders": {"a": "/tmp/sorted"},
            "operation_mode": "move",
            "history": [],
            "redo_stack": [],
        })

        assert isolated_sorting_service.get_sort_session()["image_ids"] == [11, 22]
        assert sorting_router.sort_session.copy()["current_index"] == 0

        sorting_router.sort_session["current_index"] = 1

        assert isolated_sorting_service.get_sort_session()["current_index"] == 1
        assert sorting_router.get_sort_session()["current_index"] == 1


class TestValidatePath:
    """Tests for POST /api/validate-path endpoint."""

    def test_validate_existing_path(self, test_client, tmp_path: Path):
        """Validating existing path should return valid."""
        response = test_client.post(
            "/api/validate-path",
            json={"path": str(tmp_path)}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["error"] is None

    def test_validate_nonexistent_path(self, test_client):
        """Validating nonexistent path should return invalid."""
        response = test_client.post(
            "/api/validate-path",
            json={"path": "/nonexistent/path/12345"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["error"] is not None

    def test_validate_empty_path(self, test_client):
        """Validating empty path should return invalid."""
        response = test_client.post(
            "/api/validate-path",
            json={"path": ""}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_validate_null_byte_in_path(self, test_client):
        """Null bytes in path should be rejected."""
        response = test_client.post(
            "/api/validate-path",
            json={"path": "/path/with\x00null"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False


class TestSystemInfo:
    """Tests for GET /api/system-info endpoint."""

    def test_get_system_info_returns_recommendations_by_model(self, test_client):
        with patch("hardware_monitor.get_system_info", return_value={
            "total_ram_gb": 32,
            "available_ram_gb": 24,
            "gpu_name": "Test GPU",
            "gpu_vram_total_mb": 16384,
            "gpu_vram_available_mb": 12000,
            "torch_cuda_available": True,
            "onnx_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        }), patch("hardware_monitor.recommend_tagger_config", side_effect=lambda system_info, model_name, use_gpu: {
            "model_name": model_name,
            "use_gpu": use_gpu,
            "recommended_batch_size": 4 if use_gpu else 2,
            "recommended_use_gpu": use_gpu,
            "recommended_session_refresh_interval": 180 if use_gpu else 0,
            "risk_level": "low" if use_gpu else "medium",
        }):
            response = test_client.get("/api/system-info")

        assert response.status_code == 200
        data = response.json()
        assert data["system_info"]["gpu_name"] == "Test GPU"
        assert "recommendation" in data
        assert "recommendations_by_model" in data
        assert "wd-swinv2-tagger-v3" in data["recommendations_by_model"]
        assert "custom" in data["recommendations_by_model"]
        assert data["recommendations_by_model"]["custom"]["gpu"]["use_gpu"] is True
        assert data["recommendations_by_model"]["custom"]["cpu"]["use_gpu"] is False


class TestScan:
    """Tests for POST /api/scan endpoint."""

    def test_scan_nonexistent_folder(self, test_client):
        """Scanning nonexistent folder should return 400."""
        response = test_client.post(
            "/api/scan",
            json={"folder_path": "/nonexistent/folder/12345"}
        )

        assert response.status_code == 400

    def test_scan_valid_folder(self, test_client, tmp_path: Path):
        """Scanning valid folder should start background task."""
        from PIL import Image

        # Create test images
        for i in range(3):
            img = Image.new("RGB", (100, 100), color="red")
            img.save(tmp_path / f"test_{i}.png")

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["source"] == "manual"

    def test_scan_start_response_filters_internal_fields(
        self,
        test_client,
        tmp_path: Path,
        monkeypatch,
    ):
        from routers.sorting import get_sorting_service

        service = get_sorting_service()

        def fake_start_scan(request, background_tasks, source):
            return {
                "status": "started",
                "message": "started",
                "run_id": 14,
                "source": source,
                "internal_only": "must not cross the API boundary",
            }

        monkeypatch.setattr(service, "start_scan", fake_start_scan)

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "started",
            "message": "started",
            "run_id": 14,
            "source": "manual",
        }

    def test_scan_progress_response_filters_internal_fields(self, test_client):
        from routers.sorting import get_sorting_service

        service = get_sorting_service()
        progress = service._build_default_scan_progress_state()
        progress.update({
            "run_id": 15,
            "source": "manual",
            "status": "done",
            "step": "done",
            "internal_only": "must not cross the API boundary",
        })
        service.set_scan_progress(progress)

        response = test_client.get("/api/scan/progress")

        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == 15
        assert payload["source"] == "manual"
        assert "internal_only" not in payload

    def test_auto_refresh_idle_response_uses_strict_public_shape(self, test_client):
        response = test_client.post("/api/library/auto-refresh", json={})

        assert response.status_code == 200
        assert response.json() == {
            "status": "idle",
            "reason": "no_enabled_roots",
        }

    def test_auto_refresh_started_response_includes_nested_identity(
        self,
        test_client,
        tmp_path: Path,
        monkeypatch,
    ):
        import database as db
        from routers.sorting import get_sorting_service

        db.add_library_root(str(tmp_path))
        service = get_sorting_service()

        def fake_start_registered_root_scan(
            request,
            background_tasks,
            source,
            *,
            root_record_path,
            root_id,
        ):
            return {
                "status": "started",
                "message": "started",
                "run_id": 16,
                "source": source,
                "internal_only": "filtered",
            }

        monkeypatch.setattr(
            service,
            "start_registered_root_scan",
            fake_start_registered_root_scan,
        )

        response = test_client.post("/api/library/auto-refresh", json={})

        assert response.status_code == 200
        assert response.json() == {
            "status": "started",
            "root": str(tmp_path).replace("\\", "/"),
            "scan": {
                "status": "started",
                "message": "started",
                "run_id": 16,
                "source": "library_auto_refresh",
            },
        }

    def test_scan_progress_after_start(self, test_client, tmp_path: Path):
        """After starting scan, progress should be queryable."""
        from PIL import Image

        # Create test image
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(tmp_path / "progress_test.png")

        # Start scan
        test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False}
        )

        # Check progress
        response = test_client.get("/api/scan/progress")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "current" in data
        assert "total" in data

    def test_scan_progress_counts_total_before_import_and_keeps_metadata_separate(self, test_client, tmp_path: Path):
        """Scan progress should count files first and not let metadata progress overwrite import totals."""
        from PIL import Image
        from image_manager import scan_folder

        for i in range(3):
            Image.new("RGB", (32, 32), color="blue").save(tmp_path / f"counted_{i}.png")

        events = []

        def progress_callback(current, total, filename, details=None):
            events.append({
                "current": current,
                "total": total,
                "filename": filename,
                "details": details or {},
            })

        result = scan_folder(
            str(tmp_path),
            recursive=False,
            progress_callback=progress_callback,
            quick_import=True,
        )

        phases = [event["details"].get("phase") for event in events]
        # Default scan now does count-first → counted → importing so the
        # heartbeat shows a real ``current/total`` from the first import
        # event. (See ``image_manager.scan_folder`` ``precise_total``
        # default in tests/test_image_manager.py.)
        assert "counting" in phases
        assert "counted" in phases
        assert "importing" in phases

        import_events = [event for event in events if event["details"].get("phase") == "importing"]
        assert import_events
        # First importing event sees the precise count as the total.
        assert import_events[0]["total"] == 3
        assert import_events[0]["details"]["import_total"] == 3
        assert import_events[0]["details"]["total_final"] is True
        assert result["total"] == 3
        assert result["total_final"] is True

        metadata_events = [event for event in events if event["details"].get("phase") == "metadata"]
        assert metadata_events
        assert all(event["details"]["import_total"] == 3 for event in metadata_events)
        assert metadata_events[-1]["details"]["metadata_total_final"] is True
        assert result["counted"] == 3
        assert result["total"] == 3
        assert result["metadata_total_final"] is True

    def test_scan_progress_surfaces_actionable_scan_errors(
        self,
        test_client,
        tmp_path: Path,
        monkeypatch,
    ):
        from exceptions import ScanError
        from services import sorting_service as sorting_service_module

        def fail_scan(*_args, **_kwargs):
            raise ScanError("Cannot read scan root: simulated access denial")

        monkeypatch.setattr(sorting_service_module, "scan_folder", fail_scan)

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": True},
        )

        assert response.status_code == 200
        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "error"
        assert "simulated access denial" in progress["message"]
        assert "internal error" not in progress["message"].lower()

    def test_scan_reports_library_root_persistence_failure_instead_of_done(
        self,
        test_client,
        tmp_path: Path,
        caplog,
    ):
        import logging

        import database as db

        with db.get_db() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_library_root_scan
                BEFORE INSERT ON library_roots
                BEGIN
                    SELECT RAISE(ABORT, 'library root storage unavailable');
                END
                """
            )

        with caplog.at_level(logging.INFO, logger="services.sorting_service"):
            response = test_client.post(
                "/api/scan",
                json={"folder_path": str(tmp_path), "recursive": False},
            )

        assert response.status_code == 200
        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "error"
        assert "image indexing completed" in progress["message"].lower()
        assert "library root storage unavailable" in progress["message"]
        assert str(tmp_path) in progress["message"]
        assert "scan this folder again" in progress["message"].lower()
        assert db.list_library_roots() == []
        messages = [record.getMessage() for record in caplog.records]
        assert any("Scan failed:" in message for message in messages)
        assert not any("Scan completed:" in message for message in messages)

    def test_scan_rejects_a_linked_or_junction_root_before_background_start(
        self,
        test_client,
        tmp_path: Path,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "services.sorting.scan.is_directory_symlink_or_junction",
            lambda _path: True,
        )

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": True},
        )

        assert response.status_code == 400
        assert "Windows junction" in response.json()["error"]
        assert test_client.get("/api/scan/progress").json()["status"] == "idle"

    def test_scan_skips_unreadable_images(self, test_client, tmp_path: Path):
        """Unreadable image files should count as errors and not be inserted."""
        import database as db
        from PIL import Image

        valid_path = tmp_path / "valid.png"
        Image.new("RGB", (64, 64), color="green").save(valid_path)
        (tmp_path / "broken.png").write_bytes(b"not-a-real-png")

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False}
        )

        assert response.status_code == 200

        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "done"
        assert progress["errors"] == 1
        assert progress["new"] == 1
        assert db.get_image_count() == 1



    def test_scan_logs_heartbeat_when_worker_makes_no_progress(self, tmp_path: Path, isolated_sorting_service, monkeypatch, caplog):
        """If a scan stalls inside a blocking function, the console should still show low-frequency state.

        This test used to rely on a ``time.sleep(0.05)`` inside the mocked
        ``scan_folder`` and bet that the heartbeat thread (waiting on a
        10 ms interval) would fire at least once during that window. On
        macOS CI runners the OS scheduler routinely exceeds 50 ms of
        latency for short ``threading.Event.wait`` calls, so the bet
        lost about 1 in 30 runs and produced a flaky CI failure even
        though the production heartbeat code was fine.

        The replacement is deterministic: we attach a logging handler
        that flips a ``threading.Event`` the moment a real
        "Scan heartbeat:" message is emitted, then have the mocked
        ``scan_folder`` block on that event with a generous 5 s
        timeout. Two outcomes are possible:

        * The heartbeat thread fires (production behaviour): the event
          flips, ``scan_folder`` returns, and the assertion confirms a
          heartbeat record landed in caplog. **No timing race.**
        * The heartbeat never fires (real regression): the wait times
          out, ``scan_folder`` returns anyway, and the assertion fails
          loudly — surfacing the actual bug instead of hiding it
          behind an unrelated sleep budget.
        """
        import logging
        import threading
        from fastapi import BackgroundTasks
        from services import sorting_service as sorting_service_module
        from services.sorting_service import ScanRequest

        monkeypatch.setattr(sorting_service_module, "SCAN_LOG_HEARTBEAT_SECONDS", 0.01)

        heartbeat_seen = threading.Event()

        class _HeartbeatDetector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    if "Scan heartbeat:" in record.getMessage():
                        heartbeat_seen.set()
                except Exception:  # noqa: BLE001 — defensive; never fail in a logging handler
                    pass

        detector = _HeartbeatDetector(level=logging.INFO)
        hb_logger = logging.getLogger("services.sorting_service")
        hb_logger.addHandler(detector)

        def slow_scan_folder(*_args, **_kwargs):
            # Block until the heartbeat thread actually emits at least
            # one log line, OR the safety budget expires. The 5 s budget
            # is ~500x the configured heartbeat interval, so any normal
            # scheduler will let the heartbeat through long before we
            # time out.
            heartbeat_seen.wait(timeout=5.0)
            return {
                "total": 0,
                "counted": 0,
                "total_final": True,
                "import_complete": True,
                "errors": 0,
                "new": 0,
                "updated": 0,
                "removed": 0,
                "library_ready": True,
                "metadata_processed": 0,
                "metadata_total": 0,
                "metadata_total_final": True,
                "recent_errors": [],
            }

        monkeypatch.setattr(sorting_service_module, "scan_folder", slow_scan_folder)
        background_tasks = BackgroundTasks()

        try:
            with caplog.at_level(logging.INFO, logger="services.sorting_service"):
                isolated_sorting_service.start_scan(
                    ScanRequest(folder_path=str(tmp_path), recursive=False),
                    background_tasks,
                    "manual",
                )
                background_tasks.tasks[0].func()
        finally:
            hb_logger.removeHandler(detector)

        assert heartbeat_seen.is_set(), (
            "Scan heartbeat thread never logged within the 5 s safety budget. "
            "If this fails consistently, the production heartbeat loop in "
            "sorting_service.run_scan is broken; check that "
            "SCAN_LOG_HEARTBEAT_SECONDS is honoured and that the status "
            "publish-to-running flip happens before heartbeat_thread.start()."
        )
        assert any("Scan heartbeat:" in record.getMessage() for record in caplog.records), (
            "Heartbeat detector saw a message but caplog did not. This usually "
            "means caplog.set_level dropped the propagation; double-check the "
            "logger name and handler configuration."
        )

    def test_scan_logs_start_summary_and_bad_file_samples(self, test_client, tmp_path: Path, caplog):
        """Console logs should be sparse but useful for debugging large scans."""
        import logging
        from PIL import Image

        Image.new("RGB", (64, 64), color="green").save(tmp_path / "good.png")
        (tmp_path / "broken.png").write_bytes(b"not-a-real-png")

        with caplog.at_level(logging.INFO, logger="services.sorting_service"):
            response = test_client.post(
                "/api/scan",
                json={"folder_path": str(tmp_path), "recursive": False},
            )

        assert response.status_code == 200
        messages = [record.getMessage() for record in caplog.records]
        assert any("Scan started:" in message and str(tmp_path) in message for message in messages)
        assert any("Scan completed:" in message and "errors=1" in message for message in messages)
        assert any("Scan completed with 1 issue(s)" in message and "broken.png" in message for message in messages)
        assert not any(
            "Invalid PNG signature" in record.getMessage() and record.levelno >= logging.ERROR
            for record in caplog.records
        )

    def test_scan_mixed_root_keeps_good_files_and_reports_corrupt_and_truncated_names(self, test_client, tmp_path: Path):
        """Mixed scan roots should finish, index good files, and name bad files in progress."""
        import database as db
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        metadata = PngInfo()
        metadata.add_text(
            "parameters",
            "masterpiece\nNegative prompt: lowres\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 64x64, Model: demo.safetensors",
        )

        good_path = tmp_path / "good.png"
        Image.new("RGB", (64, 64), color="green").save(good_path, pnginfo=metadata)

        truncated_path = tmp_path / "truncated.png"
        Image.new("RGB", (64, 64), color="blue").save(truncated_path, pnginfo=metadata)
        truncated_bytes = truncated_path.read_bytes()
        truncated_path.write_bytes(truncated_bytes[: len(truncated_bytes) // 2])

        corrupt_path = tmp_path / "corrupt.png"
        corrupt_path.write_bytes(b"not-a-real-png")

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False}
        )

        assert response.status_code == 200

        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "done"
        assert progress["new"] == 1
        assert progress["errors"] == 2
        assert "Issues:" in progress["message"]
        assert "truncated.png" in progress["message"]
        assert "corrupt.png" in progress["message"]
        assert [entry["filename"] for entry in progress["recent_errors"]] == ["corrupt.png", "truncated.png"]

        images = db.get_images(limit=10)
        assert [image["filename"] for image in images] == ["good.png"]

        sort_response = test_client.post("/api/sort/start")
        assert sort_response.status_code == 200
        assert sort_response.json()["total_images"] == 1

    def test_scan_mixed_root_skips_truncated_and_reports_filenames(self, test_client, tmp_path: Path):
        """Mixed scan roots should keep good files and report corrupt/truncated filenames."""
        import database as db
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        good_path = tmp_path / "good.png"
        metadata = PngInfo()
        metadata.add_text(
            "parameters",
            "masterpiece\nNegative prompt: lowres\nSteps: 20, Sampler: Euler, Model: demo-checkpoint",
        )
        Image.new("RGB", (64, 64), color="green").save(good_path, pnginfo=metadata)

        truncated_path = tmp_path / "truncated.png"
        truncated_path.write_bytes(good_path.read_bytes()[:-24])
        (tmp_path / "corrupt.png").write_bytes(b"not-a-real-png")

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False}
        )

        assert response.status_code == 200

        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "done"
        assert progress["errors"] == 2
        assert progress["new"] == 1
        assert "corrupt.png" in progress["message"]
        assert "truncated.png" in progress["message"]
        assert [img["filename"] for img in db.get_images(limit=20)] == ["good.png"]

    def test_scan_keeps_corrupt_exif_webp_and_surfaces_library_health_error(
        self,
        test_client,
        tmp_path: Path,
    ):
        from PIL import Image

        image_path = tmp_path / "corrupt-exif.webp"
        Image.new("RGB", (32, 24), color="navy").save(
            image_path,
            format="WEBP",
            exif=b"not-valid-tiff-exif",
        )
        with Image.open(image_path) as stored_image:
            assert stored_image.size == (32, 24)
            stored_image.verify()

        response = test_client.post(
            "/api/scan",
            json={"folder_path": str(tmp_path), "recursive": False},
        )

        assert response.status_code == 200
        images = test_client.test_db.get_images(limit=10)
        assert len(images) == 1
        stored = images[0]
        assert stored["filename"] == image_path.name
        assert stored["is_readable"] == 1
        assert stored["width"] == 32
        assert stored["height"] == 24
        assert stored["metadata_status"] == "error"
        assert stored["read_error"].startswith(
            "WebP EXIF chunk could not be parsed:"
        )

        health_response = test_client.get("/api/library-health?sample_limit=3")
        assert health_response.status_code == 200
        health = health_response.json()
        assert health["issue_counts"]["metadata_error"] == 1
        sample = next(
            item for item in health["issue_samples"] if item["id"] == stored["id"]
        )
        assert sample["read_error"] == stored["read_error"]

    def test_scan_reset(self, test_client):
        """Resetting scan progress should work."""
        response = test_client.post("/api/scan/reset")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_scan_reset_restores_canonical_progress_fields(self, isolated_sorting_service):
        """Reset should not return a partial idle state that breaks frontend progress reads."""
        isolated_sorting_service._scan_progress = {
            "status": "error",
            "step": "error",
            "current": 4,
            "processed": 4,
            "total": 10,
            "message": "stuck",
        }
        isolated_sorting_service._scan_worker_thread = None

        result = isolated_sorting_service.reset_scan_progress()
        progress = isolated_sorting_service.get_scan_progress()

        assert result["status"] == "reset"
        assert progress["status"] == "idle"
        assert progress["message"] == "Reset by user"
        for key in ["counted", "import_complete", "metadata_total_final", "total_final", "quick_import"]:
            assert key in progress

    def test_scan_reset_accepts_terminal_state_while_worker_finishes(self, isolated_sorting_service):
        """A published terminal result must be resettable without client retries."""

        class FinishingWorker:
            def is_alive(self):
                return True

        isolated_sorting_service._scan_progress = {
            **isolated_sorting_service._build_default_scan_progress_state(),
            "run_id": 21,
            "source": "manual",
            "status": "done",
            "step": "done",
        }
        isolated_sorting_service._scan_worker_thread = FinishingWorker()

        result = isolated_sorting_service.reset_scan_progress()

        assert result["status"] == "reset"
        assert isolated_sorting_service.get_scan_progress()["status"] == "idle"

    def test_terminal_acknowledgement_cannot_clear_a_newer_run(self, test_client, tmp_path: Path):
        from routers.sorting import get_sorting_service
        from services.sorting_models import SCAN_SOURCE_MANUAL, ScanRequest

        service = get_sorting_service()
        service._scan_run_id = 30
        service._scan_progress = {
            **service._build_default_scan_progress_state(),
            "run_id": 30,
            "source": SCAN_SOURCE_MANUAL,
            "status": "done",
            "step": "done",
        }
        stale_identity = {"run_id": 30, "source": SCAN_SOURCE_MANUAL}
        new_start = service.start_scan(
            ScanRequest(folder_path=str(tmp_path)),
            BackgroundTasks(),
            SCAN_SOURCE_MANUAL,
        )

        response = test_client.post("/api/scan/acknowledge", json=stale_identity)

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == "scan_identity_mismatch"
        assert payload["error"] == payload["message"]
        assert payload["type"] == "HTTPException"
        assert "detail" not in payload
        progress = service.get_scan_progress()
        assert progress["run_id"] == new_start["run_id"]
        assert progress["source"] == SCAN_SOURCE_MANUAL
        assert progress["status"] == "starting"

    def test_terminal_acknowledgement_atomically_clears_the_observed_manual_run(self, test_client):
        from routers.sorting import get_sorting_service
        from services.sorting_models import SCAN_SOURCE_MANUAL

        service = get_sorting_service()
        service._scan_progress = {
            **service._build_default_scan_progress_state(),
            "run_id": 32,
            "source": SCAN_SOURCE_MANUAL,
            "status": "done",
            "step": "done",
        }

        response = test_client.post(
            "/api/scan/acknowledge",
            json={"run_id": 32, "source": SCAN_SOURCE_MANUAL},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "acknowledged",
            "run_id": 32,
            "source": SCAN_SOURCE_MANUAL,
        }
        progress = service.get_scan_progress()
        assert progress["run_id"] == 0
        assert progress["source"] is None
        assert progress["status"] == "idle"

    def test_terminal_acknowledgement_rejects_the_observed_active_run(self, test_client):
        from routers.sorting import get_sorting_service
        from services.sorting_models import SCAN_SOURCE_MANUAL

        service = get_sorting_service()
        service._scan_progress = {
            **service._build_default_scan_progress_state(),
            "run_id": 33,
            "source": SCAN_SOURCE_MANUAL,
            "status": "running",
            "step": "scanning",
        }

        response = test_client.post(
            "/api/scan/acknowledge",
            json={"run_id": 33, "source": SCAN_SOURCE_MANUAL},
        )

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == "scan_not_terminal"
        assert payload["error"] == payload["message"]
        assert payload["type"] == "HTTPException"
        assert "detail" not in payload
        progress = service.get_scan_progress()
        assert progress["run_id"] == 33
        assert progress["status"] == "running"

    @pytest.mark.parametrize(
        "payload",
        [
            {"run_id": "34", "source": "manual"},
            {"run_id": 0, "source": "manual"},
            {"run_id": 34, "source": "library_rescan"},
        ],
    )
    def test_terminal_acknowledgement_requires_strict_manual_identity(self, test_client, payload):
        response = test_client.post("/api/scan/acknowledge", json=payload)

        assert response.status_code == 400

    def test_cancel_scan_cannot_cancel_a_newer_run(self, test_client, tmp_path: Path):
        from routers.sorting import get_sorting_service
        from services.sorting_models import SCAN_SOURCE_MANUAL, ScanRequest

        service = get_sorting_service()
        service._scan_run_id = 40
        service._scan_progress = {
            **service._build_default_scan_progress_state(),
            "run_id": 40,
            "source": SCAN_SOURCE_MANUAL,
            "status": "done",
            "step": "done",
        }
        stale_identity = {"run_id": 40, "source": SCAN_SOURCE_MANUAL}
        new_start = service.start_scan(
            ScanRequest(folder_path=str(tmp_path)),
            BackgroundTasks(),
            SCAN_SOURCE_MANUAL,
        )

        response = test_client.post("/api/scan/cancel", json=stale_identity)

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == "scan_identity_mismatch"
        assert "detail" not in payload
        progress = service.get_scan_progress()
        assert progress["run_id"] == new_start["run_id"]
        assert progress["source"] == SCAN_SOURCE_MANUAL
        assert progress["status"] == "starting"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"run_id": "41", "source": "manual"},
            {"run_id": 0, "source": "manual"},
            {"run_id": 41, "source": "unsupported"},
        ],
    )
    def test_cancel_scan_requires_strict_identity(self, test_client, payload):
        response = test_client.post("/api/scan/cancel", json=payload)

        assert response.status_code == 400

    def test_force_reset_invalidates_a_queued_starting_worker(
        self,
        isolated_sorting_service,
        tmp_path: Path,
        monkeypatch,
    ):
        from services import sorting_service as sorting_service_module
        from services.sorting_models import SCAN_SOURCE_MANUAL, ScanRequest

        scan_calls = 0

        def fake_scan_folder(*args, **kwargs):
            nonlocal scan_calls
            scan_calls += 1
            return {
                "total": 0,
                "counted": 0,
                "total_final": True,
                "import_complete": True,
                "errors": 0,
                "new": 0,
                "updated": 0,
                "removed": 0,
                "library_ready": True,
                "metadata_processed": 0,
                "metadata_total": 0,
                "metadata_total_final": True,
                "recent_errors": [],
            }

        monkeypatch.setattr(sorting_service_module, "scan_folder", fake_scan_folder)
        background_tasks = BackgroundTasks()
        start = isolated_sorting_service.start_scan(
            ScanRequest(folder_path=str(tmp_path)),
            background_tasks,
            SCAN_SOURCE_MANUAL,
        )
        queued_cancel_event = isolated_sorting_service._scan_cancel_event

        reset = isolated_sorting_service.reset_scan_progress()
        background_tasks.tasks[0].func()

        assert reset["status"] == "reset"
        assert queued_cancel_event is not None and queued_cancel_event.is_set()
        assert isolated_sorting_service._scan_run_id > start["run_id"]
        assert isolated_sorting_service.get_scan_progress()["status"] == "idle"
        assert scan_calls == 0

    def test_scan_cleanup_missing_removes_stale_entries_in_scope(self, test_client, tmp_path: Path):
        """Folder sync should remove indexed rows whose files no longer exist under the scanned scope."""
        import database as db
        from PIL import Image

        valid_path = tmp_path / "valid.png"
        Image.new("RGB", (64, 64), color="green").save(valid_path)
        missing_path = tmp_path / "missing.png"

        db.add_image(
            path=str(valid_path),
            filename=valid_path.name,
            metadata_json="{}",
            width=64,
            height=64,
            file_size=valid_path.stat().st_size,
            created_at=datetime.fromtimestamp(valid_path.stat().st_mtime),
        )
        db.add_image(
            path=str(missing_path),
            filename=missing_path.name,
            metadata_json="{}",
            width=64,
            height=64,
            file_size=123,
            created_at=datetime.now(),
        )

        response = test_client.post(
            "/api/scan",
            json={
                "folder_path": str(tmp_path),
                "recursive": False,
                "cleanup_missing": True,
            }
        )

        assert response.status_code == 200

        progress = test_client.get("/api/scan/progress").json()
        assert progress["status"] == "done"
        assert progress["removed"] == 1
        assert "removed" in progress["message"].lower()
        assert [img["filename"] for img in db.get_images(limit=20, include_unreadable=True)] == ["valid.png"]

    def test_cancel_scan_marks_idle_worker_as_cancelled(self, isolated_sorting_service):
        """Cancel should flip the shared scan state to cancelled when no live worker remains."""
        import threading
        from services.sorting_models import ScanCancelRequest
        from services.sorting_service import ScanRequest

        bg = BackgroundTasks()
        start = isolated_sorting_service.start_scan(
            ScanRequest(folder_path=os.getcwd(), recursive=False),
            bg,
            "manual",
        )

        isolated_sorting_service._scan_progress = {
            **isolated_sorting_service._build_default_scan_progress_state(),
            "run_id": start["run_id"],
            "source": start["source"],
            "status": "running",
            "step": "scanning",
            "current": 3,
            "processed": 3,
            "total": 10,
            "errors": 1,
            "new": 2,
            "updated": 0,
            "message": "Processing files...",
            "current_item": "demo.png",
            "started_at": 1.0,
            "updated_at": 2.0,
        }
        isolated_sorting_service._scan_cancel_event = threading.Event()
        isolated_sorting_service._scan_worker_thread = None

        result = isolated_sorting_service.cancel_scan(ScanCancelRequest(
            run_id=start["run_id"],
            source=start["source"],
        ))
        progress = isolated_sorting_service.get_scan_progress()

        assert result["status"] == "cancelled"
        assert progress["status"] == "cancelled"
        assert progress["current"] == 3
        assert "cancelled" in progress["message"].lower()

    def test_cancel_scan_sets_cancelling_when_worker_is_alive(self, isolated_sorting_service):
        """Cancel should request cooperative stop and leave the run in cancelling until the worker exits."""
        import threading
        from services.sorting_models import ScanCancelRequest

        class AliveThread:
            def is_alive(self):
                return True

        isolated_sorting_service._scan_progress = {
            **isolated_sorting_service._build_default_scan_progress_state(),
            "run_id": 1,
            "source": "manual",
            "status": "running",
            "step": "scanning",
            "current": 4,
            "processed": 4,
            "total": 12,
            "errors": 0,
            "new": 4,
            "updated": 0,
            "message": "Processing files...",
            "current_item": "demo.png",
            "started_at": 1.0,
            "updated_at": 2.0,
        }
        isolated_sorting_service._scan_cancel_event = threading.Event()
        isolated_sorting_service._scan_worker_thread = AliveThread()
        isolated_sorting_service._scan_run_id = 1

        result = isolated_sorting_service.cancel_scan(ScanCancelRequest(
            run_id=1,
            source="manual",
        ))
        progress = isolated_sorting_service.get_scan_progress()

        assert result["status"] == "cancelling"
        assert isolated_sorting_service._scan_cancel_event.is_set() is True
        assert progress["status"] == "cancelling"

    def test_cancel_scan_invalidates_a_queued_starting_worker(
        self,
        isolated_sorting_service,
        tmp_path: Path,
        monkeypatch,
    ):
        from services import sorting_service as sorting_service_module
        from services.sorting_models import SCAN_SOURCE_MANUAL, ScanCancelRequest, ScanRequest

        scan_calls = 0

        def fake_scan_folder(*args, **kwargs):
            nonlocal scan_calls
            scan_calls += 1
            raise AssertionError("A cancelled queued scan must not start")

        monkeypatch.setattr(sorting_service_module, "scan_folder", fake_scan_folder)
        background_tasks = BackgroundTasks()
        start = isolated_sorting_service.start_scan(
            ScanRequest(folder_path=str(tmp_path)),
            background_tasks,
            SCAN_SOURCE_MANUAL,
        )
        queued_cancel_event = isolated_sorting_service._scan_cancel_event

        result = isolated_sorting_service.cancel_scan(ScanCancelRequest(
            run_id=start["run_id"],
            source=start["source"],
        ))
        progress = isolated_sorting_service.get_scan_progress()
        background_tasks.tasks[0].func()

        assert result["status"] == "cancelled"
        assert queued_cancel_event is not None and queued_cancel_event.is_set()
        assert progress["run_id"] == start["run_id"]
        assert progress["source"] == SCAN_SOURCE_MANUAL
        assert progress["status"] == "cancelled"
        assert isolated_sorting_service._scan_run_id > start["run_id"]
        assert scan_calls == 0


class TestMove:
    """Tests for POST /api/move endpoint."""

    def test_move_to_nonexistent_folder(self, test_client, test_db, tmp_path: Path):
        """Moving to nonexistent folder - path validation allows creation."""
        import database as db

        # Add image to database (even though file doesn't exist)
        image_id = db.add_image(
            path="/test/move_test.png",
            filename="move_test.png",
        )

        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [image_id],
                "destination_folder": str(tmp_path / "created-destination")
            }
        )

        # The service allows creating destination folders (allow_create=True),
        # but the move will fail because image file doesn't exist
        # The service returns 200 with success=False in results
        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["success"] is False

    def test_move_to_uncreatable_folder_returns_400(self, test_client, test_db, tmp_path: Path):
        """Moving to a path that validates but cannot be created should return 400."""
        import database as db

        image_id = db.add_image(
            path="/test/move_test.png",
            filename="move_test.png",
        )

        with patch("services.sorting_service.os.makedirs", side_effect=OSError("read-only filesystem")):
            response = test_client.post(
                "/api/move",
                json={
                    "image_ids": [image_id],
                    "destination_folder": str(tmp_path / "blocked-destination")
                }
            )

        assert response.status_code == 400
        assert "Could not create destination folder" in response.text

    def test_move_empty_list(self, test_client, tmp_path: Path):
        """Moving empty image list should fail validation."""
        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [],
                "destination_folder": str(tmp_path)
            }
        )

        # Should fail validation since min_length=1 for image_ids
        # Returns 400 for Pydantic validation in this version
        assert response.status_code in [400, 422]

    def test_move_nonexistent_image(self, test_client, tmp_path: Path):
        """Moving nonexistent image should return error in results."""
        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [999999],
                "destination_folder": str(tmp_path)
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["success"] is False

    def test_move_image_file(self, test_client, test_db, tmp_path: Path):
        """Moving actual image file should work."""
        import database as db
        from PIL import Image

        # Create source image
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        img_path = source_dir / "move_me.png"
        img = Image.new("RGB", (100, 100), color="green")
        img.save(img_path)

        image_id = db.add_image(
            path=str(img_path),
            filename="move_me.png",
        )

        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [image_id],
                "destination_folder": str(dest_dir)
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["success"] is True

        # Verify file was moved
        assert not img_path.exists()
        assert (dest_dir / "move_me.png").exists()

    def test_copy_image_file_keeps_source_and_does_not_index_copy(self, test_client, test_db, tmp_path: Path):
        """Copy mode preserves the source and writes a file-only copy.

        v3.5.0 owner decision: copies are NOT indexed — copy-based sorting
        used to show every sorted image twice in the gallery. The copied
        file only enters the library if its folder is scanned later.
        """
        import database as db
        from PIL import Image

        source_dir = tmp_path / "copy_source"
        source_dir.mkdir()
        dest_dir = tmp_path / "copy_dest"
        dest_dir.mkdir()

        img_path = source_dir / "copy_me.png"
        Image.new("RGB", (96, 96), color="purple").save(img_path)

        image_id = db.add_image(
            path=str(img_path),
            filename=img_path.name,
            generator="unknown",
            prompt="copy flow",
            metadata_json="{}",
            created_at=datetime(2024, 1, 2, 3, 4, 5),
        )
        with db.get_db() as conn:
            rows_before = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]

        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [image_id],
                "destination_folder": str(dest_dir),
                "operation": "copy",
            }
        )

        assert response.status_code == 200
        payload = response.json()["results"][0]
        assert payload["success"] is True
        assert payload["operation"] == "copy"
        assert payload["new_image_id"] is None
        assert img_path.exists()
        copied_path = dest_dir / "copy_me.png"
        assert copied_path.exists()

        original_row = db.get_image_by_id(image_id)
        assert original_row["path"] == str(img_path)
        assert db.get_image_by_path(str(copied_path)) is None
        with db.get_db() as conn:
            rows_after = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        assert rows_after == rows_before

    def test_move_rejects_unreadable_image_even_if_file_exists(self, test_client, test_db, tmp_path: Path):
        """A truncated image should not be moved just because the file still exists on disk."""
        import database as db
        from PIL import Image

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        seed_path = source_dir / "seed.png"
        Image.new("RGB", (64, 64), color="blue").save(seed_path)

        truncated_path = source_dir / "truncated.png"
        truncated_path.write_bytes(seed_path.read_bytes()[:-24])

        image_id = db.add_image(
            path=str(truncated_path),
            filename=truncated_path.name,
            metadata_json="{}",
        )

        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [image_id],
                "destination_folder": str(dest_dir),
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["success"] is False
        assert "Truncated" in data["results"][0]["error"]
        assert truncated_path.exists()
        assert not (dest_dir / truncated_path.name).exists()

        row = db.get_image_by_id(image_id)
        assert row["is_readable"] == 0
        assert "Truncated" in (row["read_error"] or "")


class TestMoveJob:
    """v3.3.0 USR-1: background move/copy job (progress + cancel)."""

    def test_move_job_completes_and_embeds_results(self, isolated_sorting_service, test_db, tmp_path: Path):
        import database as db
        from PIL import Image
        from services.sorting_service import MoveRequest

        source_dir = tmp_path / "src"
        source_dir.mkdir()
        dest_dir = tmp_path / "dst"
        dest_dir.mkdir()

        img_path = source_dir / "job_move.png"
        Image.new("RGB", (48, 48), color="orange").save(img_path)
        image_id = db.add_image(path=str(img_path), filename="job_move.png")

        background_tasks = BackgroundTasks()
        start = isolated_sorting_service.start_move_job(
            MoveRequest(image_ids=[image_id], destination_folder=str(dest_dir)),
            background_tasks,
        )
        assert start["status"] == "started"
        assert start["total"] == 1

        # Run the background worker synchronously.
        background_tasks.tasks[0].func()

        progress = isolated_sorting_service.get_move_progress()
        assert progress["status"] == "done"
        assert progress["moved"] == 1
        assert progress["current"] == 1
        assert progress["total"] == 1
        assert len(progress["results"]) == 1
        assert progress["results"][0]["success"] is True

        # Source moved, destination populated.
        assert not img_path.exists()
        assert (dest_dir / "job_move.png").exists()

    def test_move_job_records_failure_for_missing_row(self, isolated_sorting_service, test_db, tmp_path: Path):
        from services.sorting_service import MoveRequest

        background_tasks = BackgroundTasks()
        result = isolated_sorting_service.start_move_job(
            MoveRequest(image_ids=[999999], destination_folder=str(tmp_path / "dst")),
            background_tasks,
        )
        # The id resolves to no rows, but the request still has a concrete id
        # list of length 1, so the worker runs and records a failure result.
        assert result["status"] == "started"
        background_tasks.tasks[0].func()
        progress = isolated_sorting_service.get_move_progress()
        assert progress["status"] == "done"
        assert progress["moved"] == 0
        assert progress["errors"] == 1

    def test_move_job_rejects_concurrent_start(self, isolated_sorting_service, tmp_path: Path):
        from fastapi import HTTPException
        from services.sorting_service import MoveRequest

        # Force the progress into a running state to simulate an in-flight job.
        isolated_sorting_service._move_progress["status"] = "running"
        with pytest.raises(HTTPException) as exc:
            isolated_sorting_service.start_move_job(
                MoveRequest(image_ids=[1], destination_folder=str(tmp_path / "dst")),
                BackgroundTasks(),
            )
        assert exc.value.status_code == 409

    def test_move_progress_endpoint_returns_idle_initially(self, test_client, isolated_sorting_service):
        response = test_client.get("/api/move/progress")
        assert response.status_code == 200
        assert response.json()["status"] == "idle"

    def test_cancel_move_when_idle_is_noop(self, isolated_sorting_service):
        result = isolated_sorting_service.cancel_move()
        assert result["status"] == "idle"


class TestBatchMove:
    """Tests for POST /api/batch-move endpoint."""

    def test_batch_move_no_matches(self, test_client, tmp_path: Path):
        """Batch move with no matching images should return message."""
        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["nonexistent_generator"],
                "destination_folder": str(tmp_path)
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_batch_move_with_filters(self, test_client, test_db_with_images, tmp_path: Path):
        """Batch move with generator filter should work."""
        dest_dir = tmp_path / "batch_dest"
        dest_dir.mkdir()

        # Note: This test won't actually move files since they don't exist on disk
        # But it should still process the filter logic

        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["unknown"],  # Use a generator that exists in test data
                "destination_folder": str(dest_dir)
            }
        )

        # Should succeed, may return count 0 if no files to move
        assert response.status_code == 200
        data = response.json()
        assert "count" in data

    def test_batch_move_forwards_search_query(self, test_client, tmp_path: Path):
        """Batch move should forward free-text search to the filtering layer."""
        with patch("services.sorting_service.db.get_filtered_image_count", return_value=0) as mock_count:
            response = test_client.post(
                "/api/batch-move",
                json={
                    "search": "manual_test_autosep_token_20260405",
                    "destination_folder": str(tmp_path)
                }
            )

        assert response.status_code == 200
        kwargs = mock_count.call_args.kwargs
        assert kwargs["search_query"] == "manual_test_autosep_token_20260405"

    def test_batch_move_forwards_artist_filter(self, test_client, tmp_path: Path):
        """Batch move should pass the normalized artist filter into the counting query."""
        with patch("services.sorting_service.db.get_filtered_image_count", return_value=0) as mock_count:
            response = test_client.post(
                "/api/batch-move",
                json={
                    "artist": "  artist_batch_move_20260428  ",
                    "destination_folder": str(tmp_path)
                }
            )

        assert response.status_code == 200
        kwargs = mock_count.call_args.kwargs
        assert kwargs["artist"] == "artist_batch_move_20260428"

    def test_batch_move_forwards_tag_mode_and_exclude_filters(self, test_client, tmp_path: Path):
        """Auto-Separate batch move must preserve Gallery copied AND/OR and exclude filters."""
        with patch("services.sorting_service.db.get_filtered_image_count", return_value=0) as mock_count:
            response = test_client.post(
                "/api/batch-move",
                json={
                    "tags": ["solo", "smile"],
                    "tag_mode": "or",
                    "exclude_tags": ["bad_anatomy"],
                    "exclude_generators": ["unknown"],
                    "exclude_ratings": ["explicit"],
                    "exclude_checkpoints": ["bad.ckpt"],
                    "exclude_loras": ["bad_lora"],
                    "destination_folder": str(tmp_path),
                },
            )

        assert response.status_code == 200
        kwargs = mock_count.call_args.kwargs
        assert kwargs["tags"] == ["solo", "smile"]
        assert kwargs["tag_mode"] == "or"
        assert kwargs["exclude_tags"] == ["bad_anatomy"]
        assert kwargs["exclude_generators"] == ["unknown"]
        assert kwargs["exclude_ratings"] == ["explicit"]
        assert kwargs["exclude_checkpoints"] == ["bad.ckpt"]
        assert kwargs["exclude_loras"] == ["bad_lora"]

    def test_batch_move_forwards_tag_mode_and_exclude_filters_to_snapshot_iterator(self, tmp_path: Path, isolated_sorting_service, monkeypatch):
        """The background batch snapshot must use the same AND/OR and exclude filters as the count."""
        from services import sorting_service as sorting_service_module
        from services.sorting_service import BatchMoveRequest

        background_tasks = BackgroundTasks()
        captured_kwargs = {}

        monkeypatch.setattr(sorting_service_module.db, "get_filtered_image_count", lambda **_kwargs: 1)

        def fake_iter_filtered_image_id_chunks(**kwargs):
            captured_kwargs.update(kwargs)
            yield []

        monkeypatch.setattr(sorting_service_module.db, "iter_filtered_image_id_chunks", fake_iter_filtered_image_id_chunks)

        isolated_sorting_service.batch_move_images(
            BatchMoveRequest(
                destination_folder=str(tmp_path / "dest"),
                tags=["solo", "smile"],
                tag_mode="or",
                exclude_tags=["bad_anatomy"],
                exclude_generators=["unknown"],
                exclude_ratings=["explicit"],
                exclude_checkpoints=["bad.ckpt"],
                exclude_loras=["bad_lora"],
            ),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        assert captured_kwargs["tags"] == ["solo", "smile"]
        assert captured_kwargs["tag_mode"] == "or"
        assert captured_kwargs["exclude_tags"] == ["bad_anatomy"]
        assert captured_kwargs["exclude_generators"] == ["unknown"]
        assert captured_kwargs["exclude_ratings"] == ["explicit"]
        assert captured_kwargs["exclude_checkpoints"] == ["bad.ckpt"]
        assert captured_kwargs["exclude_loras"] == ["bad_lora"]

    def test_batch_move_invalid_destination(self, test_client):
        """Batch move to invalid destination - path validation allows creation."""
        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["comfyui"],
                "destination_folder": "/invalid/destination/12345"
            }
        )

        # The service allows creating destination folders (allow_create=True)
        # So it returns 200 with count=0 (no matching images to move)
        assert response.status_code == 200
        data = response.json()
        # Either no images match or they are moved
        assert "count" in data or "message" in data

    def test_batch_move_allows_large_match_counts_when_background_chunking_is_available(self, test_client, tmp_path: Path):
        """Large batch moves should now start and stream through image ID chunks instead of hard-failing at 5000."""
        with patch("services.sorting_service.db.get_filtered_image_count", return_value=5001), \
             patch("services.sorting_service.db.get_filtered_image_ids", return_value=[1, 2, 3]):
            response = test_client.post(
                "/api/batch-move",
                json={
                    "generators": ["unknown"],
                    "destination_folder": str(tmp_path),
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["total"] == 5001

    def test_batch_move_rejects_second_start_while_running(self, test_client, tmp_path: Path, isolated_sorting_service):
        """Starting another batch move while one is already running should fail with 409."""
        isolated_sorting_service._batch_move_progress = {
            "status": "running",
            "current": 1,
            "total": 5,
            "message": "Moving images...",
            "errors": 0,
            "moved": 1,
        }

        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["unknown"],
                "destination_folder": str(tmp_path)
            }
        )

        assert response.status_code == 409
        data = response.json()
        assert (data.get("detail") or data.get("error")) == "Batch move already in progress"

    def test_batch_move_reset_does_not_clear_running_job(self, test_client, isolated_sorting_service):
        """Reset should not stomp over a live batch move task."""
        isolated_sorting_service._batch_move_progress = {
            "status": "running",
            "current": 2,
            "total": 7,
            "message": "Moving images...",
            "errors": 1,
            "moved": 1,
        }

        response = test_client.post("/api/batch-move/reset")

        assert response.status_code == 409
        data = response.json()
        assert (data.get("detail") or data.get("error")) == "Cannot reset batch move while it is still running"
        assert isolated_sorting_service.get_batch_move_progress()["status"] == "running"
        assert isolated_sorting_service.get_batch_move_progress()["current"] == 2

    def test_batch_copy_completion_message_uses_copy_word(self, tmp_path: Path, isolated_sorting_service, monkeypatch):
        """Copy mode progress must not tell users files were moved."""
        from services import sorting_service as sorting_service_module
        from services.sorting_service import BatchMoveRequest

        background_tasks = BackgroundTasks()
        source_path = tmp_path / "source.png"
        source_path.write_bytes(b"image")

        monkeypatch.setattr(sorting_service_module.db, "get_filtered_image_count", lambda **_kwargs: 1)
        monkeypatch.setattr(sorting_service_module.db, "get_filtered_image_ids", lambda **_kwargs: [1])
        monkeypatch.setattr(
            sorting_service_module.db,
            "get_images_by_ids",
            lambda _ids: {1: {"id": 1, "filename": "source.png", "path": str(source_path)}},
        )
        # Per-image readability now happens inside the worker loop (it
        # used to be batched up front in ``_filter_readable_image_ids``).
        # The fixture writes a placeholder byte string instead of a real
        # PNG, so stub the verifier to accept the file.
        monkeypatch.setattr(sorting_service_module, "verify_image_readable", lambda _path: (True, None))
        monkeypatch.setattr(isolated_sorting_service, "_filter_readable_image_ids", lambda ids: (ids, []))
        monkeypatch.setattr(isolated_sorting_service, "_resolve_image_path", lambda _path: str(source_path))
        monkeypatch.setattr(isolated_sorting_service, "_apply_file_operation", lambda **_kwargs: None)

        isolated_sorting_service.batch_move_images(
            BatchMoveRequest(
                destination_folder=str(tmp_path / "dest"),
                operation="copy",
                # v3.2.2 safety guard: BatchMoveRequest now requires at
                # least one filter so an unfiltered request can't move the
                # whole library by accident. Pass a tiny generators filter
                # that matches everything in this fixture so the validator
                # is happy without changing the count semantics.
                generators=["forge", "comfyui", "nai", "webui", "unknown", "others"],
            ),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        progress = isolated_sorting_service.get_batch_move_progress()
        assert progress["status"] == "done"
        assert "Copied 1 images" in progress["message"]
        assert "Moved" not in progress["message"]


class TestSortSession:
    """Tests for manual sort session endpoints."""

    def test_start_sort_session(self, test_client, test_db_with_images):
        """Starting sort session should work."""
        response = test_client.post(
            "/api/sort/start?generators=unknown"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert "total_images" in data


    def test_start_sort_session_requires_explicit_replace_when_unfinished(self, test_client, test_db, tmp_path: Path):
        """Starting Manual Sort should not silently discard a resumable session."""
        db = test_client.test_db
        first_path = _create_sort_image(tmp_path, "replace_guard_1.png")
        second_path = _create_sort_image(tmp_path, "replace_guard_2.png")
        db.add_image(path=str(first_path), filename=first_path.name, generator="unknown", metadata_json="{}")
        db.add_image(path=str(second_path), filename=second_path.name, generator="unknown", metadata_json="{}")

        test_client.delete("/api/sort/session")
        start_response = test_client.post("/api/sort/start?generators=unknown")
        assert start_response.status_code == 200

        skip_response = test_client.post("/api/sort/action?action=skip")
        assert skip_response.status_code == 200
        assert test_client.get("/api/sort/current").json()["index"] == 1

        blocked_response = test_client.post("/api/sort/start?generators=unknown")
        assert blocked_response.status_code == 409
        blocked_payload = blocked_response.json()
        blocked_error = blocked_payload.get("detail") or blocked_payload.get("error") or ""
        assert "unfinished manual sort session" in blocked_error.lower()
        assert test_client.get("/api/sort/current").json()["index"] == 1

        replace_response = test_client.post("/api/sort/start?generators=unknown&replace_existing=true")
        assert replace_response.status_code == 200
        assert test_client.get("/api/sort/current").json()["index"] == 0

    def test_start_sort_empty_results(self, test_client, test_db):
        """Starting sort session with no matches should work."""
        response = test_client.post(
            "/api/sort/start?generators=nonexistent"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_images"] == 0

    def test_start_sort_session_forwards_search_query(self, test_client):
        """Manual sort should pass the free-text search filter into the ID query."""
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start?search=manual_test_autosep_token_20260405"
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["search_query"] == "manual_test_autosep_token_20260405"

    def test_start_sort_session_forwards_prompt_match_mode_from_json_body(self, test_client, tmp_path: Path):
        """Manual Sort should use the same prompt match mode as the filter modal."""
        folder = tmp_path / "manual-sort-prompt-mode"
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start",
                json={
                    "prompts": ["takamatsu_tomori"],
                    "prompt_match_mode": "contains",
                    "folders": {"w": str(folder)},
                    "operation_mode": "copy",
                },
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["prompt_terms"] == ["takamatsu_tomori"]
        assert kwargs["prompt_match_mode"] == "contains"

    def test_start_sort_session_forwards_artist_filter(self, test_client):
        """Manual sort should pass the normalized artist filter into the ID query."""
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start?artist=%20artist_sort_session_20260428%20"
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["artist"] == "artist_sort_session_20260428"

    def test_start_sort_session_forwards_tag_mode_and_exclude_filters_from_json_body(self, test_client, tmp_path: Path):
        """Manual Sort JSON starts must preserve Gallery copied AND/OR and exclude filters."""
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start",
                json={
                    "tags": ["solo", "smile"],
                    "tag_mode": "or",
                    "exclude_tags": ["bad_anatomy"],
                    "exclude_generators": ["unknown"],
                    "exclude_ratings": ["explicit"],
                    "exclude_checkpoints": ["bad.ckpt"],
                    "exclude_loras": ["bad_lora"],
                    "folders": {"w": str(tmp_path / "manual-dest")},
                    "operation_mode": "copy",
                },
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["tags"] == ["solo", "smile"]
        assert kwargs["tag_mode"] == "or"
        assert kwargs["exclude_tags"] == ["bad_anatomy"]
        assert kwargs["exclude_generators"] == ["unknown"]
        assert kwargs["exclude_ratings"] == ["explicit"]
        assert kwargs["exclude_checkpoints"] == ["bad.ckpt"]
        assert kwargs["exclude_loras"] == ["bad_lora"]

    def test_start_sort_session_forwards_tag_mode_and_exclude_filters_from_query(self, test_client):
        """Legacy Manual Sort query starts must preserve AND/OR and exclude filters."""
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start"
                "?tags=solo,smile"
                "&tag_mode=or"
                "&exclude_tags=bad_anatomy"
                "&exclude_generators=unknown"
                "&exclude_ratings=explicit"
                "&exclude_checkpoints=bad.ckpt"
                "&exclude_loras=bad_lora"
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["tags"] == ["solo", "smile"]
        assert kwargs["tag_mode"] == "or"
        assert kwargs["exclude_tags"] == ["bad_anatomy"]
        assert kwargs["exclude_generators"] == ["unknown"]
        assert kwargs["exclude_ratings"] == ["explicit"]
        assert kwargs["exclude_checkpoints"] == ["bad.ckpt"]
        assert kwargs["exclude_loras"] == ["bad_lora"]

    def test_start_sort_session_accepts_json_body_for_large_filter_payloads(self, test_client, tmp_path: Path):
        """Manual Sort should not force large tag/LoRA/checkpoint scopes through query-string limits."""
        long_tag = "manual_sort_long_tag_" + ("x" * 1400)
        long_lora = "manual_sort_long_lora_" + ("y" * 1400)
        long_prompt = "manual sort prompt " + ("z" * 1400)
        folder = tmp_path / "manual-sort-long-filter-dest"

        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start",
                json={
                    "tags": [long_tag],
                    "loras": [long_lora],
                    "prompts": [long_prompt],
                    "folders": {"w": str(folder)},
                    "operation_mode": "copy",
                },
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        assert kwargs["tags"] == [long_tag]
        assert kwargs["loras"] == [long_lora]
        assert kwargs["prompt_terms"] == [long_prompt]
        assert response.json()["operation_mode"] == "copy"

    def test_start_sort_session_rejects_invalid_folders_payload(self, test_client):
        """Bad folders JSON should fail instead of silently becoming an empty config."""
        response = test_client.post(
            "/api/sort/start?folders=%7Bnot-json"
        )

        assert response.status_code == 400
        data = response.json()
        assert (data.get("detail") or data.get("error")) == "Invalid folders payload"

    def test_start_sort_session_rejects_non_object_folders_payload(self, test_client):
        """Folders payload must be a JSON object, not a list or scalar."""
        response = test_client.post(
            "/api/sort/start?folders=%5B%22a%22%5D"
        )

        assert response.status_code == 400
        data = response.json()
        assert (data.get("detail") or data.get("error")) == "Invalid folders payload"

    def test_get_current_without_session(self, test_client):
        """Getting current sort image without active session should return an empty-state payload."""
        # Clear any existing session first
        test_client.delete("/api/sort/session")

        response = test_client.get("/api/sort/current")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["done"] is True
        assert data["image"] is None
        assert data["total"] == 0

    def test_get_current_sort_image(self, test_client, test_db_with_images):
        """Getting current sort image should work during session."""
        # Start session
        test_client.post("/api/sort/start?generators=unknown")

        response = test_client.get("/api/sort/current")

        assert response.status_code == 200
        data = response.json()
        assert "image" in data or "done" in data

    def test_get_current_sort_image_reports_history_counts(self, test_client, tmp_path: Path):
        """Current sort payload should expose restored move/skip counts for resumed sessions."""
        db = test_client.test_db
        first_path = _create_sort_image(tmp_path, "resume_skip.png")
        db.add_image(
            path=str(first_path),
            filename="resume_skip.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=first_path.stat().st_size,
            metadata_json="{}",
        )
        second_path = _create_sort_image(tmp_path, "resume_skip_2.png")
        db.add_image(
            path=str(second_path),
            filename="resume_skip_2.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=second_path.stat().st_size,
            metadata_json="{}",
        )

        test_client.delete("/api/sort/session")
        test_client.post("/api/sort/start?generators=unknown")
        test_client.post("/api/sort/action?action=skip")

        response = test_client.get("/api/sort/current")

        assert response.status_code == 200
        data = response.json()
        assert data["sorted_count"] == 0
        assert data["skipped_count"] == 1

    def test_start_sort_session_defers_unreadable_detection_to_lazy_verify(self, test_client, tmp_path: Path):
        """Manual sort should start quickly without bulk-verifying every image.

        The sync start endpoint used to run a full PIL decode on every candidate
        image, which blocked the event loop for minutes on large libraries. We
        now rely on (a) the scan-time ``is_readable`` flag already filtering the
        DB query, and (b) the lazy per-image verification inside
        ``get_current_sort_image`` to skip any stragglers at playback time.
        """
        db = test_client.test_db
        good_path = _create_sort_image(tmp_path, "manual_good.png")
        truncated_source = _create_sort_image(tmp_path, "manual_source.png")
        truncated_path = tmp_path / "manual_bad.png"
        truncated_path.write_bytes(truncated_source.read_bytes()[:-24])

        good_id = db.add_image(
            path=str(good_path),
            filename="manual_good.png",
            generator="unknown",
            width=64,
            height=64,
            file_size=good_path.stat().st_size,
            metadata_json="{}",
        )
        bad_id = db.add_image(
            path=str(truncated_path),
            filename="manual_bad.png",
            generator="unknown",
            width=64,
            height=64,
            file_size=truncated_path.stat().st_size,
            metadata_json="{}",
        )

        response = test_client.post("/api/sort/start?generators=unknown")

        assert response.status_code == 200
        data = response.json()
        assert data["total_images"] == 2
        assert data["skipped_unreadable"] == []
        assert data["current"]["id"] in {good_id, bad_id}

    def test_get_current_sort_image_exposes_resume_metadata(self, test_client, isolated_sorting_service, tmp_path: Path):
        """Resume payload should include stable image id order plus undo/redo availability for the frontend."""
        db = test_client.test_db
        first_path = _create_sort_image(tmp_path, "resume_meta_1.png")
        first_id = db.add_image(
            path=str(first_path),
            filename="resume_meta_1.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=first_path.stat().st_size,
            metadata_json="{}",
        )
        second_path = _create_sort_image(tmp_path, "resume_meta_2.png")
        second_id = db.add_image(
            path=str(second_path),
            filename="resume_meta_2.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=second_path.stat().st_size,
            metadata_json="{}",
        )

        isolated_sorting_service.set_sort_session({
            "active": True,
            "image_ids": [first_id, second_id],
            "current_index": 1,
            "folders": {"a": "/tmp/sorted"},
            "history": [{"action": "skip", "image_id": first_id}],
            "redo_stack": [{"action": "move", "image_id": second_id, "folder_key": "a"}],
        })

        response = test_client.get("/api/sort/current")

        assert response.status_code == 200
        data = response.json()
        assert data["image_ids"] == [first_id, second_id]
        assert data["folders"] == {"a": "/tmp/sorted"}
        assert data["undo_available"] is True
        assert data["redo_available"] is True

    def test_sort_action_without_session(self, test_client):
        """Sort action without active session should fail."""
        # Clear session
        test_client.delete("/api/sort/session")

        response = test_client.post("/api/sort/action?action=skip")

        assert response.status_code == 400

    def test_sort_skip_action(self, test_client, test_db_with_images):
        """Skip action should advance to next image."""
        # Start session
        test_client.post("/api/sort/start?generators=unknown")

        response = test_client.post("/api/sort/action?action=skip")

        assert response.status_code == 200

    def test_sort_undo_without_history(self, test_client, test_db_with_images):
        """Undo without history should return appropriate message."""
        # Start fresh session
        test_client.delete("/api/sort/session")
        test_client.post("/api/sort/start?generators=unknown")

        response = test_client.post("/api/sort/action?action=undo")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_history"

    def test_sort_undo_returns_folder_key_for_redo(self, test_client, tmp_path: Path):
        """Undo should return the undone folder key so the frontend can rebuild redo state after resume."""
        from PIL import Image

        image_path = tmp_path / "undo_move.png"
        Image.new("RGB", (64, 64), color="white").save(image_path)
        destination = tmp_path / "sorted"
        destination.mkdir()

        db = test_client.test_db
        image_id = db.add_image(
            path=str(image_path),
            filename="undo_move.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=image_path.stat().st_size,
            metadata_json="{}",
        )

        test_client.delete("/api/sort/session")
        test_client.post("/api/sort/start?generators=unknown")
        test_client.post("/api/sort/set-folders", json={"folders": {"a": str(destination)}})

        move_response = test_client.post("/api/sort/action?action=move&folder_key=a")
        assert move_response.status_code == 200

        undo_response = test_client.post("/api/sort/action?action=undo")

        assert undo_response.status_code == 200
        data = undo_response.json()
        assert data["status"] == "undone"
        assert data["undone_action"] == "move"
        assert data["folder_key"] == "a"
        assert data["sorted_count"] == 0
        assert data["skipped_count"] == 0
        assert data["redo_available"] is True

    def test_sort_copy_undo_and_redo_keep_original_file_intact(self, test_client, tmp_path: Path):
        """Manual sort copy mode should undo by removing the copied file while keeping the source."""
        from PIL import Image

        source_dir = tmp_path / "copy_sort_source"
        source_dir.mkdir()
        destination = tmp_path / "copy_sort_dest"
        destination.mkdir()

        image_path = source_dir / "copy_sort.png"
        Image.new("RGB", (80, 80), color="orange").save(image_path)

        db = test_client.test_db
        db.add_image(
            path=str(image_path),
            filename=image_path.name,
            generator="unknown",
            prompt="copy me",
            metadata_json="{}",
        )

        test_client.delete("/api/sort/session")
        start_response = test_client.post("/api/sort/start?generators=unknown&operation_mode=copy")
        assert start_response.status_code == 200
        assert start_response.json()["operation_mode"] == "copy"

        test_client.post("/api/sort/set-folders", json={"folders": {"a": str(destination)}})

        copy_response = test_client.post("/api/sort/action?action=move&folder_key=a")
        assert copy_response.status_code == 200
        copy_payload = copy_response.json()
        assert copy_payload["done"] is True
        assert copy_payload["operation_mode"] == "copy"
        assert image_path.exists()
        copied_path = destination / image_path.name
        assert copied_path.exists()
        # v3.5.0: copies are file-only — the library row count never changes.
        assert db.get_image_count() == 1

        undo_response = test_client.post("/api/sort/action?action=undo")
        assert undo_response.status_code == 200
        undo_payload = undo_response.json()
        assert undo_payload["status"] == "undone"
        assert undo_payload["operation_mode"] == "copy"
        assert image_path.exists()
        assert not copied_path.exists()
        assert db.get_image_count() == 1

        redo_response = test_client.post("/api/sort/action?action=redo")
        assert redo_response.status_code == 200
        redo_payload = redo_response.json()
        assert redo_payload["status"] == "redone"
        assert redo_payload["operation_mode"] == "copy"
        assert image_path.exists()
        assert copied_path.exists()
        assert db.get_image_count() == 1

    def test_sort_collect_adds_membership_without_moving_file(self, test_client, test_db, tmp_path: Path):
        """v3.3.1: a collection-typed slot adds the image to the collection by
        reference (file stays put) and advances; undo removes membership and
        steps back; redo re-adds membership."""
        from PIL import Image

        source_dir = tmp_path / "collect_source"
        source_dir.mkdir()
        image_path = source_dir / "collect_me.png"
        Image.new("RGB", (48, 48), color="purple").save(image_path)

        db = test_client.test_db
        image_id = db.add_image(
            path=str(image_path),
            filename=image_path.name,
            generator="unknown",
            prompt="collect me",
            metadata_json="{}",
        )

        # Create a target collection.
        created = test_client.post("/api/collections", json={"name": "Collect Slot"})
        assert created.status_code == 200
        collection_id = created.json()["id"]

        # Start a session, then assign slot 'w' to the collection.
        test_client.delete("/api/sort/session")
        start_response = test_client.post("/api/sort/start?generators=unknown")
        assert start_response.status_code == 200

        set_response = test_client.post(
            "/api/sort/set-folders",
            json={"folders": {}, "collection_slots": {"w": collection_id}},
        )
        assert set_response.status_code == 200
        assert set_response.json()["collection_slots"]["w"] == collection_id

        # Collect: image added by reference, file NOT moved, cursor advanced.
        collect_response = test_client.post("/api/sort/action?action=collect&folder_key=w")
        assert collect_response.status_code == 200
        collect_payload = collect_response.json()
        assert collect_payload.get("done") is True or collect_payload.get("image") is not None
        assert collect_payload["collected_count"] == 1
        assert image_path.exists()  # file stays put
        assert db.get_image_count() == 1  # no copy row created
        assert image_id in db.get_collection_image_ids(collection_id)

        # Undo: membership removed, cursor steps back.
        undo_response = test_client.post("/api/sort/action?action=undo")
        assert undo_response.status_code == 200
        undo_payload = undo_response.json()
        assert undo_payload["status"] == "undone"
        assert undo_payload["undone_action"] == "collect"
        assert undo_payload["folder_key"] == "w"
        assert undo_payload["collected_count"] == 0
        assert image_path.exists()
        assert image_id not in db.get_collection_image_ids(collection_id)

        # Redo: membership re-added.
        redo_response = test_client.post("/api/sort/action?action=redo")
        assert redo_response.status_code == 200
        redo_payload = redo_response.json()
        assert redo_payload["status"] == "redone"
        assert redo_payload["redone_action"] == "collect"
        assert redo_payload["collected_count"] == 1
        assert image_path.exists()
        assert image_id in db.get_collection_image_ids(collection_id)

    def test_sort_collect_requires_assigned_slot(self, test_client, test_db_with_images):
        """Pressing a slot that is not assigned to a collection returns a clear
        error and does not advance/raise."""
        test_client.delete("/api/sort/session")
        test_client.post("/api/sort/start?generators=unknown")

        response = test_client.post("/api/sort/action?action=collect&folder_key=w")
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "collection" in data["error"].lower()

    def test_set_sort_folders_rejects_unknown_collection(self, test_client, tmp_path: Path):
        """Assigning a non-existent collection id to a slot is a 400."""
        response = test_client.post(
            "/api/sort/set-folders",
            json={"folders": {}, "collection_slots": {"w": 999999}},
        )
        assert response.status_code == 400

    def test_sort_redo_replays_persisted_skip(self, test_client, isolated_sorting_service, tmp_path: Path):
        """Redo should be driven by backend session state so it survives resume/reload."""
        db = test_client.test_db
        first_path = _create_sort_image(tmp_path, "redo_skip_1.png")
        first_id = db.add_image(
            path=str(first_path),
            filename="redo_skip_1.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=first_path.stat().st_size,
            metadata_json="{}",
        )
        second_path = _create_sort_image(tmp_path, "redo_skip_2.png")
        second_id = db.add_image(
            path=str(second_path),
            filename="redo_skip_2.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=second_path.stat().st_size,
            metadata_json="{}",
        )

        isolated_sorting_service.set_sort_session({
            "active": True,
            "image_ids": [first_id, second_id],
            "current_index": 0,
            "folders": {},
            "history": [],
            "redo_stack": [{"action": "skip", "image_id": first_id}],
        })

        response = test_client.post("/api/sort/action?action=redo")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "redone"
        assert data["redone_action"] == "skip"
        assert data["image"]["id"] == second_id
        assert data["skipped_count"] == 1
        assert data["undo_available"] is True
        assert data["redo_available"] is False

    def test_load_session_from_disk_rebases_current_index_and_keeps_valid_redo(self, test_client, tmp_path: Path, monkeypatch):
        """Restore should drop missing image ids but keep current_index/history/redo aligned with the surviving order."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

        db = test_client.test_db
        first_id = db.add_image(
            path="/tmp/restore_rebase_1.png",
            filename="restore_rebase_1.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )
        second_id = db.add_image(
            path="/tmp/restore_rebase_2.png",
            filename="restore_rebase_2.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )
        third_id = db.add_image(
            path="/tmp/restore_rebase_3.png",
            filename="restore_rebase_3.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )

        with db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM images WHERE id = ?", (first_id,))

        session_path = tmp_path / "sort_session.json"
        session_path.write_text(json.dumps({
            "session_schema_version": SORT_SESSION_SCHEMA_VERSION,
            "active": True,
            "image_ids": [first_id, second_id, third_id],
            "current_index": 2,
            "folders": {"a": "/tmp/sorted"},
            "history": [
                {"action": "skip", "image_id": first_id},
                {"action": "move", "image_id": second_id, "folder_key": "a", "original_path": "/tmp/original.png", "new_path": "/tmp/new.png"},
            ],
            "redo_stack": [
                {"action": "skip", "image_id": third_id},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))
        service = SortingService()

        service.load_session_from_disk()
        restored = service.get_sort_session()

        assert restored["image_ids"] == [second_id, third_id]
        assert restored["current_index"] == 1
        assert [entry["image_id"] for entry in restored["history"]] == [second_id]
        assert [entry["image_id"] for entry in restored["redo_stack"]] == [third_id]

        persisted = json.loads(session_path.read_text(encoding="utf-8"))
        assert persisted["session_schema_version"] == SORT_SESSION_SCHEMA_VERSION
        assert persisted["current_index"] == 1
        assert persisted["image_ids"] == [second_id, third_id]

    def test_load_session_from_disk_discards_history_past_restored_cursor(self, test_client, tmp_path: Path, monkeypatch):
        """Corrupt persisted history should not be allowed to push the restored cursor past the saved current index."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

        db = test_client.test_db
        first_id = db.add_image(
            path="/tmp/restore_cursor_1.png",
            filename="restore_cursor_1.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )
        second_id = db.add_image(
            path="/tmp/restore_cursor_2.png",
            filename="restore_cursor_2.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )

        session_path = tmp_path / "sort_session_cursor.json"
        session_path.write_text(json.dumps({
            "active": True,
            "image_ids": [first_id, second_id],
            "current_index": 1,
            "folders": {},
            "history": [
                {"action": "skip", "image_id": first_id},
                {"action": "skip", "image_id": second_id},
            ],
            "redo_stack": [],
        }), encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))
        service = SortingService()

        service.load_session_from_disk()
        restored = service.get_sort_session()
        persisted = json.loads(session_path.read_text(encoding="utf-8"))

        assert restored["current_index"] == 1
        assert [entry["image_id"] for entry in restored["history"]] == [first_id]
        assert persisted["session_schema_version"] == SORT_SESSION_SCHEMA_VERSION

    def test_load_session_from_disk_discards_unknown_newer_schema_version(self, test_client, tmp_path: Path, monkeypatch):
        """A persisted session from a newer schema version should be discarded instead of half-restored."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

        image_id = test_client.test_db.add_image(
            path="/tmp/restore_future_version.png",
            filename="restore_future_version.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )

        session_path = tmp_path / "sort_session_future.json"
        session_path.write_text(json.dumps({
            "session_schema_version": SORT_SESSION_SCHEMA_VERSION + 1,
            "active": True,
            "image_ids": [image_id],
            "current_index": 0,
            "folders": {"a": "/tmp/sorted"},
            "operation_mode": "move",
            "history": [],
            "redo_stack": [],
        }), encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))
        service = SortingService()

        service.load_session_from_disk()
        restored = service.get_sort_session()

        assert restored["active"] is False
        assert restored["image_ids"] == []
        assert session_path.exists() is False

    def test_load_session_from_disk_migrates_legacy_file_to_preferred_state_dir(self, test_client, tmp_path: Path, monkeypatch):
        """Loading from the legacy backend-local file should rewrite the session into the preferred state path."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

        image_id = test_client.test_db.add_image(
            path="/tmp/restore_legacy_file.png",
            filename="restore_legacy_file.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )

        preferred_path = tmp_path / "data" / "state" / "sort-session.json"
        legacy_path = tmp_path / "backend" / "sort_session.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "session_schema_version": SORT_SESSION_SCHEMA_VERSION,
            "active": True,
            "image_ids": [image_id],
            "current_index": 0,
            "folders": {"a": "/tmp/sorted"},
            "operation_mode": "move",
            "history": [],
            "redo_stack": [],
        }), encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(preferred_path))
        monkeypatch.setattr(sorting_module, "LEGACY_SESSION_FILE", str(legacy_path))
        service = SortingService()

        service.load_session_from_disk()
        restored = service.get_sort_session()

        assert restored["active"] is True
        assert restored["image_ids"] == [image_id]
        assert preferred_path.exists() is True
        assert legacy_path.exists() is False

        persisted = json.loads(preferred_path.read_text(encoding="utf-8"))
        assert persisted["session_schema_version"] == SORT_SESSION_SCHEMA_VERSION
        assert persisted["image_ids"] == [image_id]

    def test_load_session_from_disk_falls_back_to_valid_legacy_when_preferred_is_invalid(self, test_client, tmp_path: Path, monkeypatch):
        """A corrupt preferred session file should not block restore from a valid legacy payload."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

        image_id = test_client.test_db.add_image(
            path="/tmp/restore_legacy_fallback.png",
            filename="restore_legacy_fallback.png",
            generator="unknown",
            prompt=None,
            negative_prompt=None,
            checkpoint=None,
            loras=[],
            width=64,
            height=64,
            file_size=1,
            metadata_json="{}",
        )

        preferred_path = tmp_path / "data" / "state" / "sort-session.json"
        legacy_path = tmp_path / "backend" / "sort_session.json"
        preferred_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        preferred_path.write_text("{not-json", encoding="utf-8")
        legacy_path.write_text(json.dumps({
            "session_schema_version": SORT_SESSION_SCHEMA_VERSION,
            "active": True,
            "image_ids": [image_id],
            "current_index": 0,
            "folders": {"a": "/tmp/sorted"},
            "operation_mode": "move",
            "history": [],
            "redo_stack": [],
        }), encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(preferred_path))
        monkeypatch.setattr(sorting_module, "LEGACY_SESSION_FILE", str(legacy_path))
        service = SortingService()

        service.load_session_from_disk()
        restored = service.get_sort_session()

        assert restored["active"] is True
        assert restored["image_ids"] == [image_id]
        assert preferred_path.exists() is True
        assert legacy_path.exists() is False

        persisted = json.loads(preferred_path.read_text(encoding="utf-8"))
        assert persisted["session_schema_version"] == SORT_SESSION_SCHEMA_VERSION
        assert persisted["image_ids"] == [image_id]

    def test_clear_sort_session_removes_preferred_and_legacy_session_files(self, tmp_path: Path, monkeypatch):
        """Clearing a session should remove both the preferred state file and any leftover legacy file."""
        from services import sorting_service as sorting_module
        from services.sorting_service import SortingService

        preferred_path = tmp_path / "data" / "state" / "sort-session.json"
        legacy_path = tmp_path / "backend" / "sort_session.json"
        preferred_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        preferred_path.write_text("{}", encoding="utf-8")
        legacy_path.write_text("{}", encoding="utf-8")

        monkeypatch.setattr(sorting_module, "SESSION_FILE", str(preferred_path))
        monkeypatch.setattr(sorting_module, "LEGACY_SESSION_FILE", str(legacy_path))
        service = SortingService()

        result = service.clear_sort_session()

        assert result["status"] == "ok"
        assert preferred_path.exists() is False
        assert legacy_path.exists() is False

    def test_set_sort_folders(self, test_client, tmp_path: Path):
        """Setting sort folders should work."""
        response = test_client.post(
            "/api/sort/set-folders",
            json={"folders": {"a": str(tmp_path)}}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_set_sort_folders_invalid_path(self, test_client):
        """Setting sort folders with non-existent path - service allows creation."""
        response = test_client.post(
            "/api/sort/set-folders",
            json={"folders": {"a": "/invalid/path/12345"}}
        )

        # Service allows creating directories (allow_create=True)
        # Returns 200 with success or 400 for truly invalid paths
        assert response.status_code in [200, 400]

    def test_get_sort_folders(self, test_client):
        """Getting sort folders should work."""
        response = test_client.get("/api/sort/folders")

        assert response.status_code == 200
        data = response.json()
        assert "folders" in data

    def test_clear_sort_session(self, test_client):
        """Clearing sort session should work."""
        response = test_client.delete("/api/sort/session")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestClearGallery:
    """Tests for DELETE /api/clear-gallery endpoint."""

    def test_clear_gallery(self, test_client, test_db_with_images):
        """Clearing gallery should remove all images."""
        response = test_client.delete("/api/clear-gallery")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify gallery is empty
        response = test_client.get("/api/images")
        assert response.json()["total"] == 0


class TestAnalytics:
    """Tests for GET /api/analytics endpoint."""

    def test_get_analytics(self, test_client, test_db_with_images):
        """Getting analytics should return stats."""
        response = test_client.get("/api/analytics")

        assert response.status_code == 200
        data = response.json()
        assert "checkpoints" in data
        assert "loras" in data
        assert "top_tags" in data

    def test_get_analytics_groups_checkpoint_variants_by_normalized_name(self, test_client):
        test_client.test_db.add_image(
            path="/tmp/analytics_cp_a.png",
            filename="analytics_cp_a.png",
            checkpoint="ponyXLV6.safetensors [abcd1234]",
            metadata_json="{}",
        )
        test_client.test_db.add_image(
            path="/tmp/analytics_cp_b.png",
            filename="analytics_cp_b.png",
            checkpoint="ponyXLV6.safetensors",
            metadata_json="{}",
        )

        response = test_client.get("/api/analytics")

        assert response.status_code == 200
        data = response.json()
        assert data["checkpoints"][0]["checkpoint"] == "ponyXLV6"
        assert data["checkpoints"][0]["checkpoint_normalized"] == "ponyXLV6"
        assert data["checkpoints"][0]["count"] == 2

    def test_analytics_checkpoint_query_searches_full_table_before_limit(self, test_client):
        for index in range(30):
            test_client.test_db.add_image(
                path=f"/tmp/analytics_cp_filler_{index}.png",
                filename=f"analytics_cp_filler_{index}.png",
                checkpoint=f"zz_filler_model_{index:02d}.safetensors",
                metadata_json="{}",
            )
        test_client.test_db.add_image(
            path="/tmp/analytics_cp_blue.png",
            filename="analytics_cp_blue.png",
            checkpoint="nagisa_blue_archive.safetensors",
            metadata_json="{}",
        )

        response = test_client.get("/api/analytics?facet=checkpoints&q=blue&limit=5")

        assert response.status_code == 200
        data = response.json()
        assert [checkpoint["checkpoint"] for checkpoint in data["checkpoints"]] == ["nagisa_blue_archive"]

    def test_analytics_lora_query_searches_full_index_before_limit(self, test_client):
        for index in range(30):
            test_client.test_db.add_image(
                path=f"/tmp/analytics_lora_filler_{index}.png",
                filename=f"analytics_lora_filler_{index}.png",
                loras=[f"zz_filler_lora_{index:02d}"],
                metadata_json="{}",
            )
        test_client.test_db.add_image(
            path="/tmp/analytics_lora_blue.png",
            filename="analytics_lora_blue.png",
            loras=["nagisa_blue_archive"],
            metadata_json="{}",
        )

        response = test_client.get("/api/analytics?facet=loras&q=blue&limit=5")

        assert response.status_code == 200
        data = response.json()
        assert [lora["lora"] for lora in data["loras"]] == ["nagisa_blue_archive"]

    def test_get_stats(self, test_client, test_db_with_images):
        """Getting stats should return summary."""
        test_client.test_db.add_image(
            path="/tmp/stats_pending_metadata.png",
            filename="stats_pending_metadata.png",
            generator="unknown",
            metadata_status="pending",
        )

        response = test_client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total_images" in data
        assert "generators" in data
        assert data["metadata_status"]["pending"] >= 1
        assert data["metadata_pending"] >= 1
        assert data["metadata_resolving"] is True
        assert "metadata_status" in data
        assert "metadata_pending" in data

    def test_get_stats_reports_pending_metadata_counts(self, test_client):
        """Stats should tell the frontend when generator counts are still resolving."""
        db = test_client.test_db
        db.add_image(
            path="/tmp/stats_pending_metadata.png",
            filename="stats_pending_metadata.png",
            generator="unknown",
            metadata_json="{}",
            metadata_status="pending",
        )
        db.add_image(
            path="/tmp/stats_complete_metadata.png",
            filename="stats_complete_metadata.png",
            generator="forge",
            metadata_json="{}",
            metadata_status="complete",
        )

        response = test_client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["metadata_status"]["pending"] == 1
        assert data["metadata_status"]["complete"] >= 1
        assert data["metadata_pending"] == 1
        assert data["metadata_resolving"] is True
        assert data["total_images"] >= 2


class TestExportTagsBatch:
    """Tests for POST /api/tags/export-batch endpoint."""

    def test_export_tags_empty_list(self, test_client, tmp_path: Path):
        """Exporting empty tag list should fail validation (min_length=1)."""
        response = test_client.post(
            "/api/tags/export-batch",
            json={
                "image_ids": [],
                "output_folder": str(tmp_path)
            }
        )

        # Should fail validation since min_length=1 for image_ids
        assert response.status_code in [400, 422]

    def test_export_tags_nonexistent_image(self, test_client, tmp_path: Path):
        """Exporting tags for nonexistent image should handle gracefully."""
        response = test_client.post(
            "/api/tags/export-batch",
            json={
                "image_ids": [999999],
                "output_folder": str(tmp_path)
            }
        )

        assert response.status_code == 200
        # Should have error for the nonexistent image
        data = response.json()
        assert data["exported"] == 0

    def test_export_tags_invalid_folder(self, test_client):
        """Exporting to invalid folder - service allows creation."""
        response = test_client.post(
            "/api/tags/export-batch",
            json={
                "image_ids": [1],
                "output_folder": "/invalid/path/12345"
            }
        )

        # Service allows creating output directories (allow_create=True)
        # Returns 200 with exported=0 or 400 for truly invalid paths
        assert response.status_code in [200, 400, 422]

    def test_export_tags_with_blacklist(self, test_client, test_db, tmp_path: Path):
        """Exporting tags with blacklist should filter tags."""
        import database as db
        from PIL import Image

        # Create image
        img_path = tmp_path / "export_test.png"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(img_path)

        image_id = db.add_image(path=str(img_path), filename="export_test.png")
        db.add_tags(image_id, [
            {"tag": "keep_tag", "confidence": 0.9},
            {"tag": "remove_tag", "confidence": 0.9},
        ])

        output_dir = tmp_path / "tags_output"
        output_dir.mkdir()

        response = test_client.post(
            "/api/tags/export-batch",
            json={
                "image_ids": [image_id],
                "output_folder": str(output_dir),
                "blacklist": ["remove_tag"]
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exported"] == 1

        # Check the file content
        txt_file = output_dir / "export_test.txt"
        assert txt_file.exists()
        content = txt_file.read_text()
        assert "keep tag" in content
        assert "remove_tag" not in content


class TestSecurity:
    """Security tests for sorting endpoints."""

    def test_path_traversal_in_scan(self, test_client):
        """Path traversal in scan path should be blocked."""
        response = test_client.post(
            "/api/scan",
            json={"folder_path": "../../../etc"}
        )

        assert response.status_code == 400

    def test_path_traversal_in_move(self, test_client, test_db):
        """Path traversal in move destination should be blocked."""
        import database as db

        image_id = db.add_image(path="/test/image.png", filename="image.png")

        response = test_client.post(
            "/api/move",
            json={
                "image_ids": [image_id],
                "destination_folder": "../../../tmp"
            }
        )

        assert response.status_code == 400

    def test_sql_injection_in_generator_filter(self, test_client, test_db):
        """SQL injection in generator filter should be handled."""
        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["'; DROP TABLE images; --"],
                "destination_folder": "/tmp"
            }
        )

        # Should not crash, either succeeds with no matches or rejects
        assert response.status_code in [200, 400]


class TestImportUploadedFilesSecurity:
    """Regression tests for the path-traversal hardening in import_uploaded_files.

    Background: a previous version of the endpoint passed the browser-supplied
    ``UploadFile.filename`` directly into ``import_dir / filename``. A malicious
    or buggy client could submit ``"../../etc/passwd.png"`` and write outside
    ``import_dir``. The fix reduces the filename to its basename and rejects
    anything that resolves outside the import directory.
    """

    @pytest.fixture
    def fake_upload(self):
        """Build a minimal stand-in for FastAPI's UploadFile."""
        class _FakeUpload:
            def __init__(self, filename: str, content: bytes = b"\x89PNG\r\n\x1a\n"):
                self.filename = filename
                self._content = content

            async def read(self) -> bytes:
                return self._content

        return _FakeUpload

    def test_rejects_parent_directory_traversal(self, tmp_path, fake_upload, monkeypatch):
        from services.sorting_service import SortingService

        monkeypatch.setattr("services.sorting_service.parse_metadata_job", lambda job: {
            "record": {"path": job["path"], "filename": job["filename"]},
            "error": None,
        })
        monkeypatch.setattr("services.sorting_service.add_images_batch", lambda records, return_statuses=False: {"statuses": {}, "ids": {}})
        monkeypatch.setattr("config.DATA_DIR", str(tmp_path / "data"))

        service = SortingService()
        uploads = [
            fake_upload("../../escape.png"),
            fake_upload("..\\..\\windows-escape.png"),
            fake_upload("/abs/path/escape.png"),
        ]
        asyncio.run(service.import_uploaded_files(uploads))

        # All saved files must land inside import_dir as plain basenames.
        import_dir = (tmp_path / "data" / "imports").resolve()
        for child in import_dir.iterdir():
            child.resolve().relative_to(import_dir)  # raises if it escaped

    def test_rejects_dotfile_only_filename(self, tmp_path, fake_upload, monkeypatch):
        from services.sorting_service import SortingService

        monkeypatch.setattr("services.sorting_service.parse_metadata_job", lambda job: {
            "record": {"path": job["path"], "filename": job["filename"]},
            "error": None,
        })
        monkeypatch.setattr("services.sorting_service.add_images_batch", lambda records, return_statuses=False: {"statuses": {}, "ids": {}})
        monkeypatch.setattr("config.DATA_DIR", str(tmp_path / "data"))

        service = SortingService()
        # ".png" on its own (suffix matches) but the basename is empty/dotfile —
        # the service should fall back to a numbered "upload_N.png" name.
        uploads = [fake_upload(".png")]
        asyncio.run(service.import_uploaded_files(uploads))

        import_dir = (tmp_path / "data" / "imports").resolve()
        names = [p.name for p in import_dir.iterdir()]
        assert all(not n.startswith(".") for n in names), names

    def test_skips_files_with_disallowed_extension(self, tmp_path, fake_upload, monkeypatch):
        from services.sorting_service import SortingService

        monkeypatch.setattr("services.sorting_service.parse_metadata_job", lambda job: {
            "record": {"path": job["path"], "filename": job["filename"]},
            "error": None,
        })
        monkeypatch.setattr("services.sorting_service.add_images_batch", lambda records, return_statuses=False: {"statuses": {}, "ids": {}})
        monkeypatch.setattr("config.DATA_DIR", str(tmp_path / "data"))

        service = SortingService()
        uploads = [fake_upload("evil.exe"), fake_upload("safe.png")]
        result = asyncio.run(service.import_uploaded_files(uploads))

        import_dir = (tmp_path / "data" / "imports").resolve()
        saved = list(import_dir.iterdir()) if import_dir.exists() else []
        assert len(saved) == 1
        assert saved[0].suffix.lower() == ".png"
        assert result["total"] == 1


class TestResolveDropSecurity:
    """Folder names from the browser must not escape common image roots."""

    def test_rejects_traversal_in_folder_name(self, isolated_sorting_service):
        # ``../../Windows`` etc. must not match any of the common roots even if
        # such a path coincidentally exists on disk.
        assert isolated_sorting_service.resolve_drop("../etc", [], None) == {"folder_path": ""}
        assert isolated_sorting_service.resolve_drop("..\\Windows", [], None) == {"folder_path": ""}

    def test_rejects_separator_in_folder_name(self, isolated_sorting_service):
        assert isolated_sorting_service.resolve_drop("foo/bar", [], None) == {"folder_path": ""}
        assert isolated_sorting_service.resolve_drop("C:\\Windows", [], None) == {"folder_path": ""}

    def test_like_wildcards_are_escaped(self, isolated_sorting_service, test_db):
        import database as db

        # ``%`` is a SQL LIKE wildcard. Folder-segment matching must treat it
        # literally so submitting ``%`` does not match every row.
        # Depends on ``test_db`` so the SQL query has a real ``images`` table to
        # run against — earlier traversal/separator tests short-circuit before
        # the query, but ``%`` is a normal character that passes path
        # validation and reaches the SQL layer.
        db.add_image(path="/library/ordinary/image.png", filename="image.png")
        result = isolated_sorting_service.resolve_drop("%", [], None)
        assert result["folder_path"] == ""


class TestResolveDropMatching:
    """resolve_drop maps dropped filenames back to their indexed folder so a
    folder drag fills the scan path instead of failing with a bare folder name."""

    @pytest.mark.parametrize(
        (
            "stored_path",
            "folder_name",
            "windows_folder",
            "posix_folder",
        ),
        [
            (
                r"C:\Library\Drop\image.png",
                "Drop",
                r"C:\Library\Drop",
                "/mnt/c/Library/Drop",
            ),
            (
                "/mnt/c/Library/Ä/image.png",
                "ä",
                r"C:\Library\Ä",
                "/mnt/c/Library/Ä",
            ),
            (
                r"\\Server\Share\Drop\image.png",
                "Drop",
                r"\\Server\Share\Drop",
                r"\\Server\Share\Drop",
            ),
            (
                r"\\Server\Share\image.png",
                "Share",
                "\\\\Server\\Share\\",
                "\\\\Server\\Share\\",
            ),
        ],
    )
    def test_matches_indexed_folder_name_across_path_representations(
        self,
        isolated_sorting_service,
        test_db,
        monkeypatch,
        stored_path,
        folder_name,
        windows_folder,
        posix_folder,
    ):
        import database as db

        monkeypatch.setattr(isolated_sorting_service, "_common_image_roots", lambda: [])
        db.add_image(path=stored_path, filename="image.png")

        result = isolated_sorting_service.resolve_drop(folder_name, [], dropped_files=[])

        expected = windows_folder if os.name == "nt" else posix_folder
        assert result == {"folder_path": expected}

    def test_filename_match_returns_cross_runtime_parent(
        self,
        isolated_sorting_service,
        test_db,
    ):
        import database as db

        if os.name == "nt":
            stored_path = "/mnt/c/Library/Drop/image.png"
            expected_parent = r"C:\Library\Drop"
        else:
            stored_path = r"C:\Library\Drop\image.png"
            expected_parent = "/mnt/c/Library/Drop"

        db.add_image(
            path=stored_path,
            filename="image.png",
            file_size=1234,
            metadata_json="{}",
        )

        result = isolated_sorting_service.resolve_drop(
            "Drop",
            [],
            dropped_files=[{"name": "image.png", "size": 1234}],
        )

        assert result == {"folder_path": expected_parent}

    def test_posix_folder_name_match_remains_case_sensitive(
        self,
        isolated_sorting_service,
        test_db,
        monkeypatch,
    ):
        import database as db

        monkeypatch.setattr(isolated_sorting_service, "_common_image_roots", lambda: [])
        db.add_image(path="/library/drop/image.png", filename="image.png")

        result = isolated_sorting_service.resolve_drop("Drop", [], dropped_files=[])

        assert result == {"folder_path": ""}

    def test_matches_indexed_folder_by_filename_and_size(self, isolated_sorting_service, test_db):
        import database as db

        db.add_image(path="/lib/26_05_29/a.png", filename="a.png", file_size=111111, metadata_json="{}")
        db.add_image(path="/lib/26_05_29/b.png", filename="b.png", file_size=222222, metadata_json="{}")

        # Read the stored parent back so the assertion survives any path
        # normalization add_image applies on the way in.
        conn = db.get_connection()
        row = conn.cursor().execute(
            "SELECT path FROM images WHERE filename = 'a.png'"
        ).fetchone()
        stored_path = row[0] if isinstance(row, (tuple, list)) else row["path"]
        expected_parent = str(Path(stored_path).parent)

        dropped = [
            {"name": "a.png", "size": 111111},
            {"name": "b.png", "size": 222222},
        ]
        result = isolated_sorting_service.resolve_drop("26_05_29", [], dropped_files=dropped)
        assert result["folder_path"] == expected_parent

    def test_unmatched_drop_returns_empty_path(self, isolated_sorting_service, test_db):
        # Neither the files nor the folder name exist in the library, so the path
        # stays empty and the frontend opens the folder browser instead of
        # writing a bare, unscannable folder name into the path field.
        result = isolated_sorting_service.resolve_drop(
            "folder_not_in_library_zzz",
            [],
            dropped_files=[{"name": "ghost_xyz.png", "size": 7}],
        )
        assert result["folder_path"] == ""


def test_scan_progress_marks_user_visible_stalled_warning(isolated_sorting_service, monkeypatch):
    monkeypatch.setattr("services.sorting_service.SCAN_UI_STALLED_SECONDS", 10.0)
    isolated_sorting_service.set_scan_progress({
        "status": "running",
        "step": "metadata",
        "current": 42,
        "processed": 42,
        "total": 100,
        "metadata_processed": 40,
        "metadata_total": 100,
        "metadata_pending": 8,
        "message": "Reading metadata...",
        "current_item": "slow.png",
        "started_at": 100.0,
        "updated_at": 110.0,
    })
    monkeypatch.setattr("services.sorting_service.time.time", lambda: 125.0)

    progress = isolated_sorting_service.get_scan_progress()

    assert progress["attention_required"] is True
    assert progress["stalled_seconds"] == 15
    assert "metadata" in progress["attention_message"].lower()
    assert progress["diagnostics_available"] is True


def test_scan_progress_does_not_mark_stalled_when_recently_updated(isolated_sorting_service, monkeypatch):
    monkeypatch.setattr("services.sorting_service.SCAN_UI_STALLED_SECONDS", 10.0)
    isolated_sorting_service.set_scan_progress({
        "status": "running",
        "step": "importing",
        "current": 10,
        "processed": 10,
        "total": 100,
        "message": "Importing...",
        "started_at": 100.0,
        "updated_at": 123.0,
    })
    monkeypatch.setattr("services.sorting_service.time.time", lambda: 125.0)

    progress = isolated_sorting_service.get_scan_progress()

    assert progress["attention_required"] is False
    assert progress["stalled_seconds"] == 2
    assert progress["diagnostics_available"] is True

def test_library_health_reports_metadata_and_archive_signals(test_client, tmp_path):
    from PIL import Image

    image_a = tmp_path / "same.png"
    image_b_dir = tmp_path / "nested"
    image_b_dir.mkdir()
    image_b = image_b_dir / "same.png"
    image_c = tmp_path / "broken.png"
    Image.new("RGB", (32, 32), "white").save(image_a)
    Image.new("RGB", (64, 32), "black").save(image_b)
    Image.new("RGB", (16, 16), "red").save(image_c)

    db = test_client.test_db
    db.add_image(
        path=str(image_a),
        filename=image_a.name,
        generator="comfyui",
        prompt="1girl, solo",
        checkpoint="model.safetensors",
        width=32,
        height=32,
        file_size=image_a.stat().st_size,
        metadata_json="{}",
    )
    db.add_image(
        path=str(image_b),
        filename=image_b.name,
        generator="unknown",
        prompt="",
        checkpoint=None,
        width=64,
        height=32,
        file_size=image_b.stat().st_size,
        metadata_json="{}",
    )
    broken_id = db.add_image(
        path=str(image_c),
        filename=image_c.name,
        generator="webui",
        prompt="bad metadata",
        checkpoint="broken.safetensors",
        width=16,
        height=16,
        file_size=image_c.stat().st_size,
        metadata_json="{}",
    )
    db.mark_image_unreadable(broken_id, "decode failed")

    response = test_client.get("/api/library-health?sample_limit=3")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_images"] == 3
    assert data["issue_counts"]["missing_prompt"] == 1
    assert data["issue_counts"]["unreadable"] == 1
    assert data["duplicate_filenames"]["images"] == 2
    assert data["duplicate_filenames"]["samples"][0]["filename"] == "same.png"
    assert data["recommendations"]
    assert 0 <= data["summary"]["quality_score"] <= 100


# ---------------------------------------------------------------------------
# v3.3.2 Sort & Cull Workbench — WB-S1: unified session `mode` field.
# "slot" is the default and reproduces the original WASD slot-sort exactly;
# new modes register in VALID_SORT_MODES in later slices. These pin the
# backward-compatible contract (no schema-version bump): default slot, unknown
# mode rejected at start / coerced to slot on load, mode persists + round-trips.
# ---------------------------------------------------------------------------

def _add_wb_image(db, name):
    return db.add_image(
        path=f"/tmp/{name}",
        filename=name,
        generator="unknown",
        prompt=None,
        negative_prompt=None,
        checkpoint=None,
        loras=[],
        width=64,
        height=64,
        file_size=1,
        metadata_json="{}",
    )


def test_start_sort_session_defaults_to_slot_mode(test_client):
    db = test_client.test_db
    _add_wb_image(db, "wb_slot_default.png")

    response = test_client.post("/api/sort/start")
    assert response.status_code == 200
    assert response.json()["mode"] == "slot"

    current = test_client.get("/api/sort/current")
    assert current.status_code == 200
    assert current.json()["mode"] == "slot"


def test_start_sort_session_rejects_unknown_mode(test_client):
    db = test_client.test_db
    _add_wb_image(db, "wb_bad_mode.png")

    response = test_client.post("/api/sort/start?mode=nonsense")
    assert response.status_code == 400


def test_persisted_sort_session_round_trips_mode(tmp_path, monkeypatch):
    from services import sorting_service as sorting_module
    from services.sorting_service import SortingService

    session_path = tmp_path / "sort_session.json"
    monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))

    service = SortingService()
    service.set_sort_session({
        "active": True,
        "mode": "slot",
        "image_ids": [1, 2, 3],
        "current_index": 0,
        "folders": {"a": "/tmp/sorted"},
        "history": [],
        "redo_stack": [],
    })
    service._save_session_to_disk()

    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted["mode"] == "slot"


def test_load_legacy_session_without_mode_defaults_to_slot(test_client, tmp_path, monkeypatch):
    from services import sorting_service as sorting_module
    from services.sorting_service import SORT_SESSION_SCHEMA_VERSION, SortingService

    db = test_client.test_db
    img_id = _add_wb_image(db, "wb_legacy_mode.png")

    session_path = tmp_path / "sort_session.json"
    # A pre-v3.3.2 persisted session has NO "mode" key.
    session_path.write_text(json.dumps({
        "session_schema_version": SORT_SESSION_SCHEMA_VERSION,
        "active": True,
        "image_ids": [img_id],
        "current_index": 0,
        "folders": {},
        "history": [],
        "redo_stack": [],
    }), encoding="utf-8")
    monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))

    service = SortingService()
    service.load_session_from_disk()
    assert service.get_sort_session()["mode"] == "slot"


def test_coerce_unknown_mode_falls_back_to_slot():
    from services.sorting_service import SortingService

    service = SortingService()
    coerced = service._coerce_sort_session_state(
        {"active": True, "mode": "garbage", "image_ids": []}
    )
    assert coerced["mode"] == "slot"


def test_persisted_session_write_survives_a_crash_mid_dump(tmp_path: Path, monkeypatch):
    """A kill during the after-every-keypress save must not eat the undo history.

    ``open("w")`` truncated the live session file before the first byte of the
    new payload was written, so any interruption left an unparseable file.
    """
    from services import sorting_session_store as store

    session_path = tmp_path / "sort-session.json"
    payload = {
        "session_schema_version": store.SORT_SESSION_SCHEMA_VERSION,
        "active": True,
        "history": [{"image_id": 7, "original_path": "/library/00042.png"}],
    }
    store.write_persisted_session(session_path, payload)
    original_bytes = session_path.read_bytes()

    def die_mid_dump(_data, handle, **_kwargs):
        handle.write('{"session_schema_version": 1, "active": true, "hist')
        raise OSError("simulated power loss")

    monkeypatch.setattr(store.json, "dump", die_mid_dump)

    with pytest.raises(OSError):
        store.write_persisted_session(session_path, payload)

    assert session_path.read_bytes() == original_bytes
    assert store.read_persisted_session(session_path)["history"] == payload["history"]
    assert [entry.name for entry in tmp_path.iterdir()] == [session_path.name]


def test_unreadable_session_file_is_preserved_and_reported(tmp_path: Path, monkeypatch, caplog):
    """An unparseable session file must not be silently dropped and re-failed.

    Before: the generic handler logged a warning, kept the empty default
    session, and left the broken file in place so every later start failed the
    same way. The undo stack that could reverse the sort was simply gone.
    """
    import logging

    from services import sorting_service as sorting_module
    from services.sorting_service import SortingService

    session_path = tmp_path / "sort-session.json"
    truncated = '{"session_schema_version": 1, "active": true, "history": [{"image_id'
    session_path.write_text(truncated, encoding="utf-8")

    monkeypatch.setattr(sorting_module, "SESSION_FILE", str(session_path))
    monkeypatch.setattr(sorting_module, "LEGACY_SESSION_FILE", str(session_path))
    service = SortingService()

    with caplog.at_level(logging.ERROR):
        service.load_session_from_disk()

    assert service.get_sort_session()["active"] is False
    # The broken file is out of the load path, so the next start is clean...
    assert not session_path.exists()
    # ...but the bytes are kept instead of thrown away.
    quarantined = list(tmp_path.glob("sort-session.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == truncated
    assert any(
        record.levelno >= logging.ERROR and "could not be restored" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# v3.3.2 Sort & Cull Workbench — WB-S2: A/B "King-of-Hill" bracket mode.
# A champion stays; each remaining candidate challenges it; N-1 comparisons →
# one winner. Pure in-memory pointer logic (no file moves). Tests drive the
# service directly with real on-disk images (bracket get_current verifies
# readability), reading image order from the session so they don't depend on
# the DB's id ordering.
# ---------------------------------------------------------------------------

def _make_bracket_images(db, tmp_path, count):
    from PIL import Image

    ids = []
    for i in range(count):
        path = tmp_path / f"wb_brk_{i}.png"
        Image.new("RGB", (16, 16), color=(i * 30 % 255, 40, 60)).save(path)
        ids.append(
            db.add_image(
                path=str(path),
                filename=path.name,
                generator="unknown",
                prompt=None,
                negative_prompt=None,
                checkpoint=None,
                loras=[],
                width=16,
                height=16,
                file_size=1,
                metadata_json="{}",
            )
        )
    return ids


def _bracket_service(tmp_path, monkeypatch):
    from services import sorting_service as sorting_module
    from services.sorting_service import SortingService

    monkeypatch.setattr(sorting_module, "SESSION_FILE", str(tmp_path / "sort_session.json"))
    return SortingService()


def test_bracket_start_initializes_champion_and_challenger(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)

    result = service.start_sort_session(mode="bracket")
    assert result["mode"] == "bracket"

    cur = service.get_current_sort_image()
    order = cur["image_ids"]
    assert cur["mode"] == "bracket"
    assert cur["done"] is False
    assert cur["total"] == 3
    assert cur["champion"]["image"]["id"] == order[0]
    assert cur["challenger"]["image"]["id"] == order[1]


def test_bracket_keep_champion_advances_challenger(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("champion")
    cur = service.get_current_sort_image()
    assert cur["champion"]["image"]["id"] == order[0]   # champion kept
    assert cur["challenger"]["image"]["id"] == order[2]  # next challenger


def test_bracket_promote_challenger_changes_champion(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    cur = service.get_current_sort_image()
    assert cur["champion"]["image"]["id"] == order[1]   # challenger promoted
    assert cur["challenger"]["image"]["id"] == order[2]


def test_bracket_completes_with_single_winner(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("champion")  # order[0] beats order[1]
    service.sort_action("champion")  # order[0] beats order[2]
    done = service.get_current_sort_image()
    assert done["done"] is True
    assert done["winner"]["image"]["id"] == order[0]


def test_bracket_single_candidate_auto_wins(test_db, tmp_path, monkeypatch):
    ids = _make_bracket_images(test_db, tmp_path, 1)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")

    done = service.get_current_sort_image()
    assert done["done"] is True
    assert done["winner"]["image"]["id"] == ids[0]


def test_bracket_undo_restores_previous_pair(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")          # champion → order[1]
    service.sort_action("undo")
    cur = service.get_current_sort_image()
    assert cur["champion"]["image"]["id"] == order[0]   # restored
    assert cur["challenger"]["image"]["id"] == order[1]


def test_bracket_redo_reapplies_choice(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    service.sort_action("undo")
    service.sort_action("redo")
    cur = service.get_current_sort_image()
    assert cur["champion"]["image"]["id"] == order[1]
    assert cur["challenger"]["image"]["id"] == order[2]


def test_bracket_restart_preserves_mode_progress_and_redo(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 4)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    service.sort_action("champion")
    service.sort_action("undo")

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["champion_index"] == 1
    assert restored_state["current_index"] == 2
    assert len(restored_state["history"]) == 1
    assert len(restored_state["redo_stack"]) == 1
    assert current["mode"] == "bracket"
    assert current["champion"]["image"]["id"] == order[1]
    assert current["challenger"]["image"]["id"] == order[2]
    assert current["redo_available"] is True

    restored_service.sort_action("redo")
    after_redo = restored_service.get_current_sort_image()
    assert after_redo["champion"]["image"]["id"] == order[1]
    assert after_redo["challenger"]["image"]["id"] == order[3]


def test_bracket_restart_rebases_actions_after_database_row_disappears(
    test_db, tmp_path, monkeypatch
):
    _make_bracket_images(test_db, tmp_path, 5)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    service.sort_action("champion")
    service.sort_action("challenger")
    service.sort_action("undo")

    with test_db.get_db() as connection:
        connection.execute("DELETE FROM images WHERE id = ?", (order[2],))

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["image_ids"] == [order[0], order[1], order[3], order[4]]
    assert restored_state["champion_index"] == 1
    assert restored_state["current_index"] == 2
    assert restored_state["history"] == [
        {
            "action": "challenger",
            "mode": "bracket",
            "prev_champion_index": 0,
            "prev_challenger_index": 1,
        }
    ]
    assert restored_state["redo_stack"] == [
        {
            "action": "challenger",
            "mode": "bracket",
            "prev_champion_index": 1,
            "prev_challenger_index": 2,
        }
    ]
    assert current["champion"]["image"]["id"] == order[1]
    assert current["challenger"]["image"]["id"] == order[3]

    restored_service.sort_action("redo")
    after_redo = restored_service.get_current_sort_image()
    assert after_redo["champion"]["image"]["id"] == order[3]
    assert after_redo["challenger"]["image"]["id"] == order[4]


def test_bracket_restart_preserves_progress_after_lazy_unreadable_skip(
    test_db, tmp_path, monkeypatch, caplog
):
    _make_bracket_images(test_db, tmp_path, 4)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("champion")
    unreadable_path = Path(test_db.get_image_by_id(order[2])["path"])
    unreadable_path.unlink()
    after_skip = service.get_current_sort_image()
    assert after_skip["champion"]["image"]["id"] == order[0]
    assert after_skip["challenger"]["image"]["id"] == order[3]

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["champion_index"] == 0
    assert len(restored_state["history"]) == 1
    assert restored_state["redo_stack"] == []
    assert current["champion"]["image"]["id"] == order[0]
    assert current["challenger"]["image"]["id"] == order[3]
    restart_records = [
        record for record in caplog.records
        if record.getMessage() == "Restarting invalid persisted bracket session"
    ]
    assert restart_records == []


def test_bracket_restart_discards_disconnected_redo_after_database_row_disappears(
    test_db, tmp_path, monkeypatch, caplog
):
    _make_bracket_images(test_db, tmp_path, 6)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    service.sort_action("challenger")
    service.sort_action("champion")
    service.sort_action("undo")
    service.sort_action("undo")
    service.sort_action("undo")

    with test_db.get_db() as connection:
        connection.execute("DELETE FROM images WHERE id = ?", (order[1],))

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["image_ids"] == [
        order[0], order[2], order[3], order[4], order[5]
    ]
    assert restored_state["champion_index"] == 0
    assert restored_state["current_index"] == 1
    assert restored_state["history"] == []
    assert restored_state["redo_stack"] == []
    assert current["champion"]["image"]["id"] == order[0]
    assert current["challenger"]["image"]["id"] == order[2]
    assert current["redo_available"] is False
    restart_records = [
        record for record in caplog.records
        if record.getMessage() == "Restarting invalid persisted bracket session"
    ]
    assert len(restart_records) == 1
    assert restart_records[0].reason == "disconnected_action_chain"


def test_bracket_restart_discards_disconnected_history_when_champion_survives(
    test_db, tmp_path, monkeypatch, caplog
):
    _make_bracket_images(test_db, tmp_path, 6)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    service.sort_action("challenger")
    service.sort_action("champion")

    with test_db.get_db() as connection:
        connection.execute("DELETE FROM images WHERE id = ?", (order[1],))

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["image_ids"] == [
        order[0], order[2], order[3], order[4], order[5]
    ]
    assert restored_state["champion_index"] == 0
    assert restored_state["current_index"] == 1
    assert restored_state["history"] == []
    assert restored_state["redo_stack"] == []
    assert current["champion"]["image"]["id"] == order[0]
    assert current["challenger"]["image"]["id"] == order[2]
    restart_records = [
        record for record in caplog.records
        if record.getMessage() == "Restarting invalid persisted bracket session"
    ]
    assert len(restart_records) == 1
    assert restart_records[0].reason == "disconnected_action_chain"


def test_bracket_restart_restarts_explicitly_when_saved_champion_disappears(
    test_db, tmp_path, monkeypatch, caplog
):
    _make_bracket_images(test_db, tmp_path, 4)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("challenger")
    with test_db.get_db() as connection:
        connection.execute("DELETE FROM images WHERE id = ?", (order[1],))

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "bracket"
    assert restored_state["image_ids"] == [order[0], order[2], order[3]]
    assert restored_state["champion_index"] == 0
    assert restored_state["current_index"] == 1
    assert restored_state["history"] == []
    assert restored_state["redo_stack"] == []
    assert current["champion"]["image"]["id"] == order[0]
    assert current["challenger"]["image"]["id"] == order[2]
    restart_records = [
        record for record in caplog.records
        if record.getMessage() == "Restarting invalid persisted bracket session"
    ]
    assert len(restart_records) == 1
    assert restart_records[0].reason == "missing_champion_or_invalid_cursor"


def test_bracket_rejects_invalid_action(test_db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    _make_bracket_images(test_db, tmp_path, 2)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="bracket")

    with pytest.raises(HTTPException) as exc_info:
        service.sort_action("move")  # a slot action is invalid in bracket mode
    assert exc_info.value.status_code == 400


def test_bracket_start_and_action_via_api(test_client, tmp_path):
    from PIL import Image

    db = test_client.test_db
    for i in range(3):
        path = tmp_path / f"wb_api_{i}.png"
        Image.new("RGB", (16, 16), color=(i * 30 % 255, 10, 20)).save(path)
        db.add_image(
            path=str(path), filename=path.name, generator="unknown", prompt=None,
            negative_prompt=None, checkpoint=None, loras=[], width=16, height=16,
            file_size=1, metadata_json="{}",
        )

    start = test_client.post("/api/sort/start?mode=bracket")
    assert start.status_code == 200
    assert start.json()["mode"] == "bracket"

    current = test_client.get("/api/sort/current")
    assert current.status_code == 200
    body = current.json()
    assert body["mode"] == "bracket"
    assert body["champion"] is not None and body["challenger"] is not None

    action = test_client.post("/api/sort/action?action=champion")
    assert action.status_code == 200
    assert action.json()["mode"] == "bracket"


# ---------------------------------------------------------------------------
# v3.3.2 Sort & Cull Workbench — FF-1: 留/汰 Keep-Reject rapid cull mode.
# One image at a time; keep/reject/skip with undo/redo. Non-destructive — keep
# and reject only record the decision + advance the cursor (the frontend routes
# kept→Collection / rejected→opt-in target at finish). Tests drive the service
# directly with real on-disk images (cull get_current verifies readability),
# reading image order from the session so they don't depend on DB id ordering.
# Reuses _make_bracket_images / _bracket_service (generic image+service helpers).
# ---------------------------------------------------------------------------

def test_cull_start_initializes_first_image(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)

    result = service.start_sort_session(mode="cull")
    assert result["mode"] == "cull"

    cur = service.get_current_sort_image()
    order = cur["image_ids"]
    assert cur["mode"] == "cull"
    assert cur["done"] is False
    assert cur["total"] == 3
    assert cur["index"] == 0
    assert cur["image"]["image"]["id"] == order[0]
    assert cur["kept"] == 0 and cur["rejected"] == 0


def test_cull_keep_advances_and_counts(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    res = service.sort_action("keep")
    assert res["decision"] == "keep" and res["image_id"] == order[0]
    cur = service.get_current_sort_image()
    assert cur["index"] == 1
    assert cur["image"]["image"]["id"] == order[1]
    assert cur["kept"] == 1 and cur["rejected"] == 0


def test_cull_reject_advances_and_counts(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    res = service.sort_action("reject")
    assert res["decision"] == "reject" and res["image_id"] == order[0]
    cur = service.get_current_sort_image()
    assert cur["index"] == 1 and cur["rejected"] == 1 and cur["kept"] == 0


def test_cull_skip_advances_without_counting(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")

    service.sort_action("skip")
    cur = service.get_current_sort_image()
    assert cur["index"] == 1 and cur["kept"] == 0 and cur["rejected"] == 0


def test_cull_completes_after_last_image(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 2)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")

    service.sort_action("keep")
    service.sort_action("reject")
    done = service.get_current_sort_image()
    assert done["done"] is True
    assert done["image"] is None
    assert done["kept"] == 1 and done["rejected"] == 1


def test_cull_undo_restores_cursor_and_tally(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("keep")
    service.sort_action("undo")
    cur = service.get_current_sort_image()
    assert cur["index"] == 0
    assert cur["image"]["image"]["id"] == order[0]
    assert cur["kept"] == 0


def test_cull_redo_reapplies_decision(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("keep")
    service.sort_action("undo")
    service.sort_action("redo")
    cur = service.get_current_sort_image()
    assert cur["index"] == 1
    assert cur["image"]["image"]["id"] == order[1]
    assert cur["kept"] == 1


def test_cull_restart_preserves_mode_decisions_and_redo(test_db, tmp_path, monkeypatch):
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("keep")
    service.sort_action("reject")
    service.sort_action("undo")

    restored_service = _bracket_service(tmp_path, monkeypatch)
    restored_service.load_session_from_disk()
    restored_state = restored_service.get_sort_session()
    current = restored_service.get_current_sort_image()

    assert restored_state["mode"] == "cull"
    assert restored_state["current_index"] == 1
    assert len(restored_state["history"]) == 1
    assert len(restored_state["redo_stack"]) == 1
    assert current["mode"] == "cull"
    assert current["image"]["image"]["id"] == order[1]
    assert current["decisions"] == {str(order[0]): "keep"}
    assert current["kept"] == 1
    assert current["rejected"] == 0
    assert current["redo_available"] is True

    restored_service.sort_action("redo")
    after_redo = restored_service.get_current_sort_image()
    assert after_redo["image"]["image"]["id"] == order[2]
    assert after_redo["decisions"] == {
        str(order[0]): "keep",
        str(order[1]): "reject",
    }


def test_cull_rejects_invalid_action(test_db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    _make_bracket_images(test_db, tmp_path, 2)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")

    with pytest.raises(HTTPException) as exc_info:
        service.sort_action("champion")  # a bracket action is invalid in cull mode
    assert exc_info.value.status_code == 400


def test_cull_get_current_exposes_decisions_for_resume(test_db, tmp_path, monkeypatch):
    # The cull payload must expose a per-image keep/reject decision map so a
    # browser reload can rebuild its client-side decisions and still route them
    # at finish. Regression: decisions made before a reload were silently dropped.
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("keep")    # order[0] -> keep
    service.sort_action("reject")  # order[1] -> reject

    cur = service.get_current_sort_image()
    assert cur["decisions"] == {str(order[0]): "keep", str(order[1]): "reject"}

    # Skip is not a decision and must not appear; the done payload keeps the map.
    service.sort_action("skip")    # order[2] -> skip
    done = service.get_current_sort_image()
    assert done["done"] is True
    assert done["decisions"] == {str(order[0]): "keep", str(order[1]): "reject"}


def test_cull_decisions_reflect_undo(test_db, tmp_path, monkeypatch):
    # Undo removes the decision from the map so a resumed session doesn't
    # re-route a reverted keep/reject.
    _make_bracket_images(test_db, tmp_path, 3)
    service = _bracket_service(tmp_path, monkeypatch)
    service.start_sort_session(mode="cull")
    order = service.get_current_sort_image()["image_ids"]

    service.sort_action("keep")
    assert service.get_current_sort_image()["decisions"] == {str(order[0]): "keep"}
    service.sort_action("undo")
    assert service.get_current_sort_image()["decisions"] == {}


def test_cull_start_and_action_via_api(test_client, tmp_path):
    from PIL import Image

    db = test_client.test_db
    for i in range(3):
        path = tmp_path / f"wb_cull_api_{i}.png"
        Image.new("RGB", (16, 16), color=(i * 30 % 255, 10, 20)).save(path)
        db.add_image(
            path=str(path), filename=path.name, generator="unknown", prompt=None,
            negative_prompt=None, checkpoint=None, loras=[], width=16, height=16,
            file_size=1, metadata_json="{}",
        )

    start = test_client.post("/api/sort/start?mode=cull")
    assert start.status_code == 200
    assert start.json()["mode"] == "cull"

    current = test_client.get("/api/sort/current")
    assert current.status_code == 200
    body = current.json()
    assert body["mode"] == "cull"
    assert body["image"] is not None and body["kept"] == 0

    action = test_client.post("/api/sort/action?action=keep")
    assert action.status_code == 200
    payload = action.json()
    assert payload["mode"] == "cull" and payload["decision"] == "keep"


# ============================================================================
# v3.3.x gallery-scope parity (regression for the filter→scope data-loss bug:
# min_user_rating / collection_id / folder / has_metadata / exclude_prompts /
# exclude_colors / brightness fields were silently dropped on the sorting
# path, so batch move and manual sort operated on a WIDER set than the
# gallery displayed).
# ============================================================================

_V33X_SCOPE_PAYLOAD = {
    "min_user_rating": 3,
    "brightness_min": 10.5,
    "brightness_max": 200.0,
    "color_temperature": "warm",
    "brightness_distribution": "balanced",
    "exclude_prompts": ["bad hands"],
    "exclude_colors": ["cool"],
    "collection_id": 7,
    "folder": "D:/library/keepers",
    "has_metadata": True,
}


class TestSortingScopeFilterParity:
    """Every v3.3.x gallery-scope filter must reach the count/iter/session queries."""

    def test_batch_move_forwards_v33x_scope_filters_to_count(self, test_client, tmp_path: Path):
        with patch("services.sorting_service.db.get_filtered_image_count", return_value=0) as mock_count:
            response = test_client.post(
                "/api/batch-move",
                json={**_V33X_SCOPE_PAYLOAD, "destination_folder": str(tmp_path)},
            )

        assert response.status_code == 200
        kwargs = mock_count.call_args.kwargs
        for key, expected in _V33X_SCOPE_PAYLOAD.items():
            assert kwargs[key] == expected, f"batch-move count query dropped {key}"

    def test_batch_move_forwards_v33x_scope_filters_to_snapshot_iterator(self, tmp_path: Path, isolated_sorting_service, monkeypatch):
        from services import sorting_service as sorting_service_module
        from services.sorting_service import BatchMoveRequest

        background_tasks = BackgroundTasks()
        captured_kwargs = {}

        monkeypatch.setattr(sorting_service_module.db, "get_filtered_image_count", lambda **_kwargs: 1)

        def fake_iter_filtered_image_id_chunks(**kwargs):
            captured_kwargs.update(kwargs)
            yield []

        monkeypatch.setattr(sorting_service_module.db, "iter_filtered_image_id_chunks", fake_iter_filtered_image_id_chunks)

        isolated_sorting_service.batch_move_images(
            BatchMoveRequest(destination_folder=str(tmp_path / "dest"), **_V33X_SCOPE_PAYLOAD),
            background_tasks,
        )
        background_tasks.tasks[0].func()

        for key, expected in _V33X_SCOPE_PAYLOAD.items():
            assert captured_kwargs[key] == expected, f"batch-move snapshot query dropped {key}"

    def test_batch_move_scope_only_filters_satisfy_safety_guard(self):
        """A collection-only (or folder/★-rating-only) scope is a legitimate filter."""
        from services.sorting_service import BatchMoveRequest

        for kwargs in (
            {"collection_id": 12},
            {"folder": "D:/library/keepers"},
            {"min_user_rating": 4},
            {"exclude_prompts": ["bad hands"]},
            {"exclude_colors": ["cool"]},
            {"has_metadata": False},
        ):
            parsed = BatchMoveRequest(destination_folder="X:/dest", **kwargs)
            for field, value in kwargs.items():
                assert getattr(parsed, field) == value

    def test_batch_move_zero_star_rating_alone_does_not_unlock_whole_library(self):
        """min_user_rating=0 is a no-op at the DB layer ("show all"), so it must
        not satisfy the catastrophic-foot-gun guard by itself."""
        from pydantic import ValidationError
        from services.sorting_service import BatchMoveRequest

        with pytest.raises(ValidationError):
            BatchMoveRequest(destination_folder="X:/dest", min_user_rating=0)

    def test_start_sort_session_forwards_v33x_scope_filters_from_json_body(self, test_client, tmp_path: Path):
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post(
                "/api/sort/start",
                json={
                    **_V33X_SCOPE_PAYLOAD,
                    "folders": {"w": str(tmp_path / "slot-w")},
                    "operation_mode": "copy",
                },
            )

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        for key, expected in _V33X_SCOPE_PAYLOAD.items():
            assert kwargs[key] == expected, f"manual-sort session query dropped {key}"

    def test_start_sort_session_legacy_query_path_defaults_scope_fields_to_none(self, test_client):
        """Backward compatibility: legacy query-string starts behave exactly as before."""
        with patch("services.sorting_service.db.get_filtered_image_ids", return_value=[]) as mock_ids:
            response = test_client.post("/api/sort/start?generators=unknown")

        assert response.status_code == 200
        kwargs = mock_ids.call_args.kwargs
        for key in _V33X_SCOPE_PAYLOAD:
            assert kwargs[key] is None, f"legacy path unexpectedly set {key}"

    def test_batch_move_min_user_rating_and_exclude_prompts_constrain_real_count(self, test_client, tmp_path: Path):
        """End-to-end against a real DB: ★≥N + exclude-prompts narrow the matched
        set exactly like the gallery shows (previously the whole generator scope
        would have been moved)."""
        db = test_client.test_db
        prompts = ["sunny meadow landscape", "bad hands close-up", "sunny beach panorama"]
        image_ids = []
        for index, prompt in enumerate(prompts):
            image_ids.append(db.add_image(
                path=f"/test/scope-parity/img_{index}.png",
                filename=f"scope_parity_{index}.png",
                generator="webui",
                prompt=prompt,
                negative_prompt=None,
                checkpoint=None,
                loras=[],
                width=64,
                height=64,
                file_size=1,
                metadata_json="{}",
            ))

        assert db.set_user_rating(image_ids[0], 4)
        assert db.set_user_rating(image_ids[1], 5)
        # image_ids[2] stays unrated (0 stars)

        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["webui"],
                "min_user_rating": 4,
                "exclude_prompts": ["bad hands"],
                "prompt_match_mode": "contains",
                "destination_folder": str(tmp_path / "scope-dest"),
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Of the 3 webui images only img_0 is rated >= 4 AND free of "bad hands".
        assert data.get("total", data.get("count")) == 1

    def test_start_sort_session_min_user_rating_constrains_real_session_set(self, test_client, tmp_path: Path):
        """End-to-end against a real DB: the WASD session set honors the ★≥N scope."""
        from PIL import Image

        db = test_client.test_db
        image_ids = []
        for index in range(3):
            image_path = tmp_path / f"scope_session_{index}.png"
            Image.new("RGB", (16, 16), color=(index * 40 % 255, 30, 60)).save(image_path)
            image_ids.append(db.add_image(
                path=str(image_path),
                filename=image_path.name,
                generator="unknown",
                prompt=None,
                negative_prompt=None,
                checkpoint=None,
                loras=[],
                width=16,
                height=16,
                file_size=1,
                metadata_json="{}",
            ))

        assert db.set_user_rating(image_ids[1], 5)

        response = test_client.post(
            "/api/sort/start",
            json={
                "generators": ["unknown"],
                "min_user_rating": 5,
                "folders": {"w": str(tmp_path / "five-star-dest")},
                "operation_mode": "copy",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_images"] == 1
        assert data["current"]["id"] == image_ids[1]


class TestBatchMoveCancellingBusyGuard:
    """A batch move in 'cancelling' state is still busy (worker draining)."""

    def test_batch_move_rejects_second_start_while_cancelling(self, test_client, tmp_path: Path, isolated_sorting_service):
        isolated_sorting_service._batch_move_progress = {
            "status": "cancelling",
            "current": 3,
            "total": 9,
            "message": "Cancelling...",
            "errors": 0,
            "moved": 3,
        }

        response = test_client.post(
            "/api/batch-move",
            json={
                "generators": ["unknown"],
                "destination_folder": str(tmp_path),
            },
        )

        assert response.status_code == 409
        data = response.json()
        assert (data.get("detail") or data.get("error")) == "Batch move already in progress"
