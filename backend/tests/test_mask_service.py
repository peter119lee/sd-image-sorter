"""Phase 4 mask editor: mask CRUD/status/auto endpoints + export integration.

Masks are grayscale PNGs keyed by gallery image id (white = train, black =
ignore); absence means "train the whole image" and must never fail anything.
Export naming contract: OneTrainer = ``<stem>-masklabel.png`` beside the
exported image; kohya = ``mask/<stem>.png`` under the output folder.
"""
from __future__ import annotations

import base64
import io as _io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from services import mask_service


pytestmark = pytest.mark.usefixtures("authorize_legacy_dataset_exports")


def _data_url(mode="L", size=(32, 32), color=255):
    image = Image.new(mode, size, color=color)
    buffer = _io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.fixture
def masks_dir(tmp_path, monkeypatch):
    target = tmp_path / "masks"
    monkeypatch.setattr(mask_service, "MASKS_DIR", target)
    return target


@pytest.fixture
def staged_image(test_db, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = src / "mask_subject.png"
    Image.new("RGB", (32, 32), color=(120, 60, 30)).save(path)
    image_id = db.add_image(path=str(path), filename="mask_subject.png")
    db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])
    return image_id, path


class TestMaskCrud:
    def test_put_get_delete_roundtrip(self, test_client, staged_image, masks_dir):
        image_id, _ = staged_image
        assert test_client.get(f"/api/masks/{image_id}").status_code == 404

        saved = test_client.put(f"/api/masks/{image_id}", json={"data_url": _data_url()})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["saved"] is True and body["width"] == 32

        fetched = test_client.get(f"/api/masks/{image_id}")
        assert fetched.status_code == 200
        assert fetched.headers["content-type"] == "image/png"
        mask = Image.open(_io.BytesIO(fetched.content))
        assert mask.mode == "L", "masks are stored as grayscale L"

        removed = test_client.delete(f"/api/masks/{image_id}")
        assert removed.json()["removed"] is True
        assert test_client.get(f"/api/masks/{image_id}").status_code == 404

    def test_rgba_input_converts_to_grayscale(self, test_client, staged_image, masks_dir):
        image_id, _ = staged_image
        response = test_client.put(
            f"/api/masks/{image_id}", json={"data_url": _data_url(mode="RGBA", color=(255, 0, 0, 255))}
        )
        assert response.status_code == 200
        mask = Image.open(mask_service.mask_path(image_id))
        assert mask.mode == "L"

    def test_unknown_image_404(self, test_client, masks_dir):
        response = test_client.put("/api/masks/999999", json={"data_url": _data_url()})
        assert response.status_code == 404

    def test_garbage_payload_400(self, test_client, staged_image, masks_dir):
        image_id, _ = staged_image
        response = test_client.put(
            f"/api/masks/{image_id}",
            json={"data_url": "data:image/png;base64," + "A" * 64},
        )
        assert response.status_code == 400

    def test_status_endpoint(self, test_client, staged_image, masks_dir):
        image_id, _ = staged_image
        test_client.put(f"/api/masks/{image_id}", json={"data_url": _data_url()})
        response = test_client.post("/api/masks/status", json={"image_ids": [image_id, 424242]})
        assert response.status_code == 200
        masks = response.json()["masks"]
        assert masks[str(image_id)] is True
        assert masks["424242"] is False


class TestAutoMask:
    def test_missing_rembg_yields_actionable_400(self, test_client, staged_image, masks_dir, monkeypatch):
        image_id, _ = staged_image
        # Force the opt-in dependency to be absent even if the dev env has it.
        monkeypatch.setitem(sys.modules, "rembg", None)
        response = test_client.post(f"/api/masks/{image_id}/auto", json={"method": "rembg"})
        assert response.status_code == 400
        assert "pip install rembg" in response.json()["error"]

    def test_unknown_method_400(self, test_client, staged_image, masks_dir):
        image_id, _ = staged_image
        response = test_client.post(f"/api/masks/{image_id}/auto", json={"method": "clipseg"})
        assert response.status_code == 400

    def test_lucida_returns_soft_preview_without_saving(
        self, test_client, staged_image, masks_dir, monkeypatch
    ):
        image_id, _ = staged_image
        calls = []

        def generate_subject_mask(image, use_gpu):
            calls.append({"size": image.size, "use_gpu": use_gpu})
            return Image.new("L", image.size, color=96)

        monkeypatch.setattr(mask_service.config, "LUCIDA_USE_GPU", False, raising=False)
        monkeypatch.setitem(
            sys.modules,
            "lucida_matting",
            SimpleNamespace(
                LucidaError=RuntimeError,
                generate_subject_mask=generate_subject_mask,
            ),
        )

        response = test_client.post(f"/api/masks/{image_id}/auto", json={"method": "lucida"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method"] == "lucida"
        assert body["saved"] is False
        assert calls == [{"size": (32, 32), "use_gpu": False}]
        payload = base64.b64decode(body["data_url"].split(",", 1)[1])
        preview = Image.open(_io.BytesIO(payload))
        assert preview.mode == "L"
        assert preview.getpixel((0, 0)) == 96
        assert not mask_service.has_mask(image_id)

    def test_unprepared_lucida_yields_actionable_400(
        self, test_client, staged_image, masks_dir, monkeypatch
    ):
        image_id, _ = staged_image

        class _LucidaUnavailable(RuntimeError):
            pass

        def generate_subject_mask(_image, use_gpu):
            del use_gpu
            raise _LucidaUnavailable(
                "Lucida model files are missing. Run Prepare / Download."
            )

        monkeypatch.setitem(
            sys.modules,
            "lucida_matting",
            SimpleNamespace(
                LucidaError=_LucidaUnavailable,
                generate_subject_mask=generate_subject_mask,
            ),
        )

        response = test_client.post(f"/api/masks/{image_id}/auto", json={"method": "lucida"})

        assert response.status_code == 400
        assert "Prepare / Download" in response.json()["error"]

    def test_auto_returns_preview_without_saving(self, test_client, staged_image, masks_dir, monkeypatch):
        """Stub rembg to prove the wiring: alpha channel becomes the L mask,
        returned as a data URL, and NOTHING is persisted."""
        image_id, _ = staged_image

        class _FakeRembg:
            @staticmethod
            def remove(image):
                rgba = image.convert("RGBA")
                alpha = Image.new("L", rgba.size, color=200)
                rgba.putalpha(alpha)
                return rgba

        monkeypatch.setitem(sys.modules, "rembg", _FakeRembg)
        response = test_client.post(f"/api/masks/{image_id}/auto", json={"method": "rembg"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["saved"] is False
        assert body["data_url"].startswith("data:image/png;base64,")
        assert not mask_service.has_mask(image_id), "auto must not persist"


class TestMaskExport:
    def _stage_three(self, tmp_path):
        src = tmp_path / "exp-src"
        src.mkdir()
        ids = []
        for index in range(1, 4):
            path = src / f"subject_{index:03d}.png"
            Image.new("RGB", (32, 32), color=(index * 40, 80, 120)).save(path)
            image_id = db.add_image(path=str(path), filename=path.name)
            db.add_tags(image_id, [{"tag": "1girl", "confidence": 0.9}])
            ids.append(image_id)
        return ids

    def test_onetrainer_masks_beside_exports_and_missing_counted(
        self, test_client, test_db, tmp_path, masks_dir
    ):
        ids = self._stage_three(tmp_path)
        # Masks for the first two only — the third must count as missing.
        for image_id in ids[:2]:
            test_client.put(f"/api/masks/{image_id}", json={"data_url": _data_url()})

        out = tmp_path / "out-ot"
        out.mkdir()
        response = test_client.post("/api/dataset/export", json={
            "image_ids": ids,
            "output_folder": str(out),
            "naming_pattern": "{filename}",
            "mask_export": "onetrainer",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        assert body["masks_written"] == 2
        assert body["masks_missing"] == 1
        assert (out / "subject_001-masklabel.png").exists()
        assert (out / "subject_002-masklabel.png").exists()
        assert not (out / "subject_003-masklabel.png").exists()
        mask = Image.open(out / "subject_001-masklabel.png")
        assert mask.mode == "L"

    def test_kohya_masks_in_conditioning_folder_follow_renames(
        self, test_client, test_db, tmp_path, masks_dir
    ):
        ids = self._stage_three(tmp_path)
        for image_id in ids:
            test_client.put(f"/api/masks/{image_id}", json={"data_url": _data_url()})

        out = tmp_path / "out-kohya"
        out.mkdir()
        response = test_client.post("/api/dataset/export", json={
            "image_ids": ids,
            "output_folder": str(out),
            "naming_pattern": "{trigger}_{index:03d}",
            "trigger": "mychar",
            "mask_export": "kohya",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["masks_written"] == 3
        # Mask stems must follow the RENAMED image stems, not the sources.
        for index in range(1, 4):
            assert (out / f"mychar_{index:03d}.png").exists()
            assert (out / "mask" / f"mychar_{index:03d}.png").exists()

    def test_path_source_requested_mask_is_counted_missing(self, tmp_path, masks_dir):
        from services.dataset_export.engine import export_dataset
        from services.dataset_export.models import DatasetExportRequest

        source = tmp_path / "local-source.png"
        Image.new("RGB", (32, 32), color=(120, 60, 30)).save(source)
        out = tmp_path / "out-local-mask"
        request = DatasetExportRequest(
            image_paths=[str(source)],
            output_folder=str(out),
            naming_pattern="{filename}",
            image_overrides={str(source.resolve()): "subject"},
            mask_export="kohya",
        )

        result = export_dataset(request)

        assert result.status == "ok"
        assert result.masks_written == 0
        assert result.masks_missing == 1

    def test_mask_export_none_writes_nothing(self, test_client, test_db, tmp_path, masks_dir):
        ids = self._stage_three(tmp_path)
        test_client.put(f"/api/masks/{ids[0]}", json={"data_url": _data_url()})
        out = tmp_path / "out-none"
        out.mkdir()
        response = test_client.post("/api/dataset/export", json={
            "image_ids": ids,
            "output_folder": str(out),
            "naming_pattern": "{filename}",
        })
        body = response.json()
        assert body["masks_written"] == 0 and body["masks_missing"] == 0
        assert not list(out.glob("*masklabel*")) and not (out / "mask").exists()

    def test_invalid_mask_export_400(self, test_client, test_db, tmp_path):
        ids = self._stage_three(tmp_path)
        response = test_client.post("/api/dataset/export", json={
            "image_ids": ids,
            "output_folder": str(tmp_path / "x"),
            "mask_export": "diffusers",
        })
        assert response.status_code == 400


class TestMaskAutoBatch:
    def test_lucida_batch_saves_and_skips_existing(
        self, test_client, staged_image, masks_dir, monkeypatch
    ):
        image_id, _ = staged_image
        calls = []

        def generate_subject_mask(image, use_gpu):
            del use_gpu
            calls.append(image.size)
            return Image.new("L", image.size, color=77)

        monkeypatch.setattr(mask_service.config, "LUCIDA_USE_GPU", False, raising=False)
        monkeypatch.setitem(
            sys.modules,
            "lucida_matting",
            SimpleNamespace(
                LucidaError=RuntimeError,
                generate_subject_mask=generate_subject_mask,
            ),
        )

        started = test_client.post(
            "/api/masks/auto-batch",
            json={"image_ids": [image_id], "method": "lucida", "overwrite": False},
        )
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        body = None
        for _ in range(40):
            body = test_client.get(f"/api/bulk-jobs/{job_id}").json()
            if body.get("status") in {"done", "error", "cancelled"}:
                break
            import time
            time.sleep(0.05)
        assert body is not None
        assert body["status"] == "done", body
        result = body.get("result") or {}
        assert result.get("saved") == 1
        assert result.get("skipped") == 0
        assert mask_service.has_mask(image_id)
        assert calls == [(32, 32)]

        started_again = test_client.post(
            "/api/masks/auto-batch",
            json={"image_ids": [image_id], "method": "lucida", "overwrite": False},
        )
        job_id = started_again.json()["job_id"]
        for _ in range(40):
            body = test_client.get(f"/api/bulk-jobs/{job_id}").json()
            if body.get("status") in {"done", "error", "cancelled"}:
                break
            import time
            time.sleep(0.05)
        assert body["status"] == "done", body
        assert (body.get("result") or {}).get("skipped") == 1
        assert (body.get("result") or {}).get("saved") == 0
        assert calls == [(32, 32)]

    def test_auto_batch_rejects_unknown_method(self, test_client, staged_image):
        image_id, _ = staged_image
        response = test_client.post(
            "/api/masks/auto-batch",
            json={"image_ids": [image_id], "method": "nope"},
        )
        assert response.status_code == 400
