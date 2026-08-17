"""Real-image contracts for legacy Censor preview and save output integrity."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from services.image_metadata_writer import JPEG_ALPHA_WARNING


def _create_rgba_image(path: Path, format_name: str) -> None:
    pixels: list[tuple[int, int, int, int]] = [
        (255, 220, 30, 255) if 4 <= x < 12 and 4 <= y < 12 else (0, 0, 0, 0)
        for y in range(16)
        for x in range(16)
    ]
    image = Image.new("RGBA", (16, 16))
    image.putdata(pixels)
    if format_name == "WEBP":
        image.save(path, format=format_name, lossless=True)
        return
    image.save(path, format=format_name)


def _add_image(db: ModuleType, path: Path) -> int:
    return int(
        db.add_image(
            path=str(path),
            filename=path.name,
            metadata_json="{}",
        )
    )


def test_preview_preserves_rgba_pixels_in_png_data_url(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "preview-alpha.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)

    response = test_client.post(
        "/api/censor/preview",
        json={
            "image_id": image_id,
            "regions": [[4, 4, 12, 12]],
            "style": "solid",
        },
    )

    assert response.status_code == 200, response.text
    prefix, payload = response.json()["preview"].split(",", 1)
    assert prefix == "data:image/png;base64"
    with Image.open(BytesIO(base64.b64decode(payload))) as preview:
        preview.load()
        assert preview.format == "PNG"
        assert preview.mode == "RGBA"
        assert preview.getpixel((0, 0)) == (0, 0, 0, 0)
        assert preview.getpixel((8, 8)) == (0, 0, 0, 255)


def test_preview_skips_reversed_solid_region(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "preview-reversed.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)

    response = test_client.post(
        "/api/censor/preview",
        json={
            "image_id": image_id,
            "regions": [[12, 12, 4, 4]],
            "style": "solid",
        },
    )

    assert response.status_code == 200, response.text
    _, payload = response.json()["preview"].split(",", 1)
    with Image.open(BytesIO(base64.b64decode(payload))) as preview:
        preview.load()
        with Image.open(source_path) as source:
            source.load()
            assert preview.tobytes() == source.tobytes()


def test_preview_skips_reversed_sticker_region(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "preview-reversed-sticker.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)

    response = test_client.post(
        "/api/censor/preview",
        json={
            "image_id": image_id,
            "regions": [[12, 12, 4, 4]],
            "style": "sticker",
        },
    )

    assert response.status_code == 200, response.text
    _, payload = response.json()["preview"].split(",", 1)
    with Image.open(BytesIO(base64.b64decode(payload))) as preview:
        preview.load()
        with Image.open(source_path) as source:
            source.load()
            assert preview.tobytes() == source.tobytes()


@pytest.mark.parametrize(
    ("extension", "format_name"),
    [("png", "PNG"), ("webp", "WEBP")],
)
def test_save_preserves_rgba_and_matches_filename_format(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
    extension: str,
    format_name: str,
) -> None:
    source_path = tmp_path / f"save-alpha.{extension}"
    _create_rgba_image(source_path, format_name)
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / f"out-{extension}"

    response = test_client.post(
        "/api/censor/save",
        json={
            "image_id": image_id,
            "regions": [[4, 4, 12, 12]],
            "style": "solid",
            "output_folder": str(output_folder),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["filename"] == f"save-alpha_censored.{extension}"
    output_path = Path(result["output_path"])
    with Image.open(output_path) as saved:
        saved.load()
        assert saved.format == format_name
        assert "A" in saved.getbands()
        assert saved.getpixel((0, 0))[3] == 0
        assert max(saved.getpixel((8, 8))[:3]) < 20
        assert saved.getpixel((8, 8))[3] == 255


def test_save_jpeg_matches_filename_and_decoded_format(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "save-opaque.jpg"
    Image.new("RGB", (16, 16), (255, 220, 30)).save(source_path, format="JPEG")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / "out-jpeg"

    response = test_client.post(
        "/api/censor/save",
        json={
            "image_id": image_id,
            "regions": [[4, 4, 12, 12]],
            "style": "solid",
            "output_folder": str(output_folder),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["filename"] == "save-opaque_censored.jpg"
    with Image.open(result["output_path"]) as saved:
        saved.load()
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"
        assert max(saved.getpixel((8, 8))) < 20


def test_save_unsupported_source_format_uses_png_extension_and_bytes(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "fallback.bmp"
    Image.new("RGB", (16, 16), (255, 220, 30)).save(source_path, format="BMP")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / "out-bmp"

    response = test_client.post(
        "/api/censor/save",
        json={
            "image_id": image_id,
            "regions": [[4, 4, 12, 12]],
            "style": "solid",
            "output_folder": str(output_folder),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["filename"] == "fallback_censored.png"
    with Image.open(result["output_path"]) as saved:
        assert saved.format == "PNG"


def test_canvas_save_flattens_rgba_to_white_jpeg_and_warns(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "canvas-alpha.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / "canvas-out"
    image_data = base64.b64encode(source_path.read_bytes()).decode("ascii")

    response = test_client.post(
        "/api/censor/save-data",
        json={
            "image_data": f"data:image/png;base64,{image_data}",
            "filename": "canvas-alpha.jpg",
            "output_folder": str(output_folder),
            "metadata_option": "strip",
            "output_format": "jpg",
            "original_image_id": image_id,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    with Image.open(result["output_path"]) as saved:
        saved.load()
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"
        assert min(saved.getpixel((0, 0))) >= 245
        center = saved.getpixel((8, 8))
        assert center[0] > 220
        assert center[1] > 180
    assert result["warnings"] == [JPEG_ALPHA_WARNING]


@pytest.mark.parametrize(
    ("extension", "format_name"),
    [("png", "PNG"), ("webp", "WEBP")],
)
def test_canvas_save_preserves_alpha_for_transparent_output(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
    extension: str,
    format_name: str,
) -> None:
    source_path = tmp_path / f"canvas-alpha-{extension}.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / f"canvas-{extension}-out"
    image_data = base64.b64encode(source_path.read_bytes()).decode("ascii")

    response = test_client.post(
        "/api/censor/save-data",
        json={
            "image_data": f"data:image/png;base64,{image_data}",
            "filename": f"canvas-alpha.{extension}",
            "output_folder": str(output_folder),
            "metadata_option": "strip",
            "output_format": extension,
            "original_image_id": image_id,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    with Image.open(result["output_path"]) as saved:
        saved.load()
        assert saved.format == format_name
        assert "A" in saved.getbands()
        assert saved.getpixel((0, 0))[3] == 0
        assert saved.getpixel((8, 8))[3] == 255
    assert result["warnings"] == []


def test_failed_encode_leaves_the_overwritten_original_byte_identical(
    tmp_path: Path,
) -> None:
    """A save that dies inside the encoder must not consume the old file.

    20000px exceeds WebP's 16383 dimension limit, so Pillow raises *after* it
    has opened the destination. Writing straight at the destination left the
    user's original at 0 bytes with no backup and no undo.
    """
    from services.censor_service import CensorService

    target = tmp_path / "precious-original.webp"
    _create_rgba_image(target, "WEBP")
    original_bytes = target.read_bytes()
    assert original_bytes

    with pytest.raises((ValueError, OSError)):
        CensorService._save_image_with_format(
            Image.new("RGB", (20000, 8), color="red"),
            str(target),
            "webp",
            {},
        )

    assert target.read_bytes() == original_bytes
    assert [entry.name for entry in tmp_path.iterdir()] == [target.name]


def test_canvas_save_failure_keeps_the_confirmed_overwrite_target_intact(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    """The user confirmed an overwrite, the encode failed, and the app said
    "Save data failed" — the file they overwrote must still be there."""
    output_folder = tmp_path / "canvas-overwrite-out"
    output_folder.mkdir()
    target = output_folder / "precious.webp"
    _create_rgba_image(target, "WEBP")
    original_bytes = target.read_bytes()

    oversized = BytesIO()
    Image.new("RGB", (20000, 8), color="red").save(oversized, format="PNG")
    image_data = base64.b64encode(oversized.getvalue()).decode("ascii")

    response = test_client.post(
        "/api/censor/save-data",
        json={
            "image_data": f"data:image/png;base64,{image_data}",
            "filename": "precious.webp",
            "output_folder": str(output_folder),
            "metadata_option": "strip",
            "output_format": "webp",
            "allow_overwrite": True,
        },
    )

    assert response.status_code == 500, response.text
    assert target.read_bytes() == original_bytes
    assert [entry.name for entry in output_folder.iterdir()] == [target.name]


def test_operation_save_flattens_rgba_to_white_jpeg_and_warns(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "operations-alpha.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / "operations-out"

    response = test_client.post(
        "/api/censor/save-operations",
        json={
            "original_image_id": image_id,
            "operations": [
                {
                    "kind": "stroke",
                    "tool": "pen",
                    "brush_size": 4,
                    "pen_color": "#ff0000",
                    "pen_opacity": 1,
                    "points": [{"x": 8, "y": 8}],
                }
            ],
            "filename": "operations-alpha.jpg",
            "output_folder": str(output_folder),
            "metadata_option": "strip",
            "output_format": "jpg",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    with Image.open(result["output_path"]) as saved:
        saved.load()
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"
        assert min(saved.getpixel((0, 0))) >= 245
        center = saved.getpixel((8, 8))
        assert center[0] > 200
        assert center[1] < 80
    assert result["warnings"] == [JPEG_ALPHA_WARNING]


@pytest.mark.parametrize(
    ("extension", "format_name"),
    [("png", "PNG"), ("webp", "WEBP")],
)
def test_operation_save_preserves_alpha_for_transparent_output(
    test_client: TestClient,
    test_db: ModuleType,
    tmp_path: Path,
    extension: str,
    format_name: str,
) -> None:
    source_path = tmp_path / f"operations-alpha-{extension}.png"
    _create_rgba_image(source_path, "PNG")
    image_id = _add_image(test_db, source_path)
    output_folder = tmp_path / f"operations-{extension}-out"

    response = test_client.post(
        "/api/censor/save-operations",
        json={
            "original_image_id": image_id,
            "operations": [
                {
                    "kind": "stroke",
                    "tool": "pen",
                    "brush_size": 4,
                    "pen_color": "#ff0000",
                    "pen_opacity": 1,
                    "points": [{"x": 8, "y": 8}],
                }
            ],
            "filename": f"operations-alpha.{extension}",
            "output_folder": str(output_folder),
            "metadata_option": "strip",
            "output_format": extension,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    with Image.open(result["output_path"]) as saved:
        saved.load()
        assert saved.format == format_name
        assert "A" in saved.getbands()
        assert saved.getpixel((0, 0))[3] == 0
        assert saved.getpixel((8, 8))[3] == 255
    assert result["warnings"] == []
