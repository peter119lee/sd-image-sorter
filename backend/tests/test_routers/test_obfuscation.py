"""
Tests for obfuscation router preview behavior.

Focuses on the publish-critical preview endpoint used by the new UI.
"""
import io
import sys
import tempfile
import base64
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _create_png_with_metadata(path: Path, prompt_text: str = "test metadata") -> None:
    pnginfo = PngInfo()
    pnginfo.add_text("prompt", prompt_text)
    Image.new("RGB", (24, 24), color="orange").save(path, pnginfo=pnginfo)


class TestObfuscationPreview:
    def test_preview_preserve_metadata_can_be_disabled(self, test_client, tmp_path, monkeypatch):
        from routers import obfuscation as obfuscation_router

        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "hidden prompt")

        created_paths = []
        real_named_tempfile = tempfile.NamedTemporaryFile

        def fake_named_tempfile(*args, **kwargs):
            kwargs["dir"] = tmp_path
            handle = real_named_tempfile(*args, **kwargs)
            created_paths.append(Path(handle.name))
            return handle

        monkeypatch.setattr(obfuscation_router.tempfile, "NamedTemporaryFile", fake_named_tempfile)

        with open(source_path, "rb") as handle:
            response = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode", "preserve_metadata": "false"},
            )

        assert response.status_code == 200
        result = Image.open(io.BytesIO(response.content))
        assert "prompt" not in result.info
        assert len(created_paths) == 1
        assert not created_paths[0].exists()

    def test_preview_preserve_metadata_can_be_enabled(self, test_client, tmp_path):
        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "visible prompt")

        with open(source_path, "rb") as handle:
            response = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode", "preserve_metadata": "true"},
            )

        assert response.status_code == 200
        result = Image.open(io.BytesIO(response.content))
        assert "prompt" in result.info

    def test_preview_legacy_pnginfo_decode_restores_original_text(self, test_client, tmp_path):
        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "legacy prompt text")

        with open(source_path, "rb") as handle:
            encoded = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode", "preserve_metadata": "true", "legacy_pnginfo": "true", "password": "1201"},
            )

        assert encoded.status_code == 200

        encoded_bytes = encoded.content
        encoded_image = Image.open(io.BytesIO(encoded_bytes))
        assert encoded_image.size == (24, 25)

        decode_payload = io.BytesIO(encoded_bytes)
        with decode_payload:
            decoded = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("encoded.png", decode_payload, "image/png")},
                data={"mode": "decode", "preserve_metadata": "true", "legacy_pnginfo": "true", "password": "1201"},
            )

        assert decoded.status_code == 200
        decoded_image = Image.open(io.BytesIO(decoded.content))
        assert decoded_image.size == (24, 24)
        assert decoded_image.info.get("prompt") == "legacy prompt text"

    def test_preview_small_tomato_roundtrip_restores_pixels_and_metadata(self, test_client, tmp_path):
        # Audit F5: this case previously asserted that small_tomato DROPS the
        # prompt ("prompt" not in ...), pinning a silent data loss - the user
        # protected an image to share it, restored it later, and their
        # generation parameters were gone. The output container is a PNG in
        # both directions and a PNG can always carry tEXt, so the loss was a
        # gate, not a format limit. The pixel round-trip assertions below are
        # unchanged; only the metadata expectation is inverted.
        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "small tomato prompt")

        with Image.open(source_path) as original:
            original_rgba = original.convert("RGBA").tobytes()

        with open(source_path, "rb") as handle:
            encoded = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode", "preserve_metadata": "true", "compat_mode": "small_tomato"},
            )

        assert encoded.status_code == 200
        encoded_image = Image.open(io.BytesIO(encoded.content))
        assert encoded_image.size == (24, 24)
        assert encoded_image.info.get("prompt") not in (None, "small tomato prompt"), (
            "the shared copy must carry the prompt in its encrypted form"
        )

        decode_payload = io.BytesIO(encoded.content)
        with decode_payload:
            decoded = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("encoded.png", decode_payload, "image/png")},
                data={"mode": "decode", "preserve_metadata": "true", "compat_mode": "small_tomato"},
            )

        assert decoded.status_code == 200
        decoded_image = Image.open(io.BytesIO(decoded.content))
        assert decoded_image.size == (24, 24)
        assert decoded_image.info.get("prompt") == "small tomato prompt"
        assert decoded_image.convert("RGBA").tobytes() == original_rgba

    def test_preview_rejects_unknown_compat_mode(self, test_client, tmp_path):
        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "bad compat mode")

        with open(source_path, "rb") as handle:
            response = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode", "compat_mode": "mystery_tomato"},
            )

        assert response.status_code == 400
        body = response.json()
        error_msg = body.get("detail") or body.get("error") or ""
        assert "Unsupported compat mode" in error_msg

    def test_preview_rejects_oversized_upload_bytes(self, test_client, tmp_path, monkeypatch):
        from routers import obfuscation as obfuscation_router

        source_path = tmp_path / "source.png"
        _create_png_with_metadata(source_path, "byte limit")
        monkeypatch.setattr(obfuscation_router, "MAX_OBFUSCATE_SOURCE_BYTES", 32)

        with open(source_path, "rb") as handle:
            response = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode"},
            )

        assert response.status_code == 413
        body = response.json()
        error_msg = body.get("detail") or body.get("error") or ""
        assert "too large" in error_msg.lower()

    def test_preview_rejects_oversized_pixel_dimensions(self, test_client, tmp_path, monkeypatch):
        import obfuscation as obfuscation_module

        source_path = tmp_path / "source.png"
        Image.new("RGB", (40, 40), color="orange").save(source_path)
        monkeypatch.setattr(obfuscation_module, "MAX_OBFUSCATE_SOURCE_PIXELS", 1000)

        with open(source_path, "rb") as handle:
            response = test_client.post(
                "/api/obfuscate/preview",
                files={"file": ("source.png", handle, "image/png")},
                data={"mode": "encode"},
            )

        assert response.status_code == 413
        body = response.json()
        error_msg = body.get("detail") or body.get("error") or ""
        assert "too large" in error_msg.lower()


class TestObfuscationIndexedOverwrite:
    def test_encode_rejects_existing_output_without_allow_overwrite(self, test_client, tmp_path):
        source_path = tmp_path / "obfuscate-source.png"
        output_path = tmp_path / "obfuscate-output.png"
        _create_png_with_metadata(source_path, "source")
        _create_png_with_metadata(output_path, "existing")

        response = test_client.post(
            "/api/obfuscate/encode",
            json={
                "image_path": str(source_path),
                "output_path": str(output_path),
                "password": "1201",
                "preserve_metadata": False,
            },
        )

        assert response.status_code == 409
        body = response.json()
        error_msg = body.get("detail") or body.get("error") or ""
        assert "already exists" in error_msg.lower()

    def test_encode_overwrite_refreshes_indexed_state(self, test_client, tmp_path):
        from image_fingerprint import compute_image_content_fingerprint

        source_path = tmp_path / "obfuscate-indexed-source.png"
        _create_png_with_metadata(source_path, "indexed prompt")

        image_id = test_client.test_db.add_image(
            path=str(source_path),
            filename=source_path.name,
            metadata_json="{}",
        )

        original_fingerprint = compute_image_content_fingerprint(str(source_path))
        test_client.test_db.add_tags(
            image_id,
            [{"tag": "stale_tag", "confidence": 0.95}],
            content_fingerprint=original_fingerprint,
        )
        with test_client.test_db.get_db() as conn:
            conn.execute(
                """
                UPDATE images
                SET ai_caption = ?, aesthetic_score = ?, embedding = ?, content_fingerprint = ?
                WHERE id = ?
                """,
                ("stale caption", 0.99, base64.b64decode("qrvM"), original_fingerprint, image_id),
            )

        response = test_client.post(
            "/api/obfuscate/encode",
            json={
                "image_path": str(source_path),
                "output_path": str(source_path),
                "password": "1201",
                "preserve_metadata": False,
                "allow_overwrite": True,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["warnings"] == []
        assert payload["overwrote_existing"] is True
        assert payload["overwrote_indexed_path"] is True
        assert payload["reconciled_image_id"] == image_id

        refreshed_fingerprint = compute_image_content_fingerprint(str(source_path))

        with test_client.test_db.get_db() as conn:
            row = conn.execute(
                "SELECT ai_caption, aesthetic_score, embedding, content_fingerprint FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
            tag_count = conn.execute(
                "SELECT COUNT(*) FROM tags WHERE image_id = ?",
                (image_id,),
            ).fetchone()[0]

        assert tag_count == 0
        assert row["ai_caption"] is None
        assert row["aesthetic_score"] is None
        assert row["embedding"] is None
        assert row["content_fingerprint"] == refreshed_fingerprint
        assert refreshed_fingerprint != original_fingerprint
