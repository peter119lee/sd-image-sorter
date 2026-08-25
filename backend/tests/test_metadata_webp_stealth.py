"""WebP (and JPEG/Pillow) signed stealth carriers — NovelAI alpha LSB and A1111 RGB."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from PIL import Image

from metadata_parser import parse_image


_WEBUI_PARAMETERS = (
    "signed carrier prompt\n"
    "Negative prompt: lowres\n"
    "Steps: 24, Sampler: Euler a, CFG scale: 7, Seed: 42, "
    "Size: 32x160, Model: carrier-model.safetensors"
)

_REAL_NAI_STEALTH_WEBP = Path(
    r"L:\Pictures\AAA Reference\undid\138886896_p3.webp"
)


def _bytes_to_bits(value: bytes) -> list[int]:
    return [
        (byte >> shift) & 1
        for byte in value
        for shift in range(7, -1, -1)
    ]


def _embed_stealth_pixels(
    image: Image.Image,
    signature: bytes,
    payload: bytes,
    declared_bit_length: int,
) -> Image.Image:
    width, height = image.size
    header = signature + declared_bit_length.to_bytes(4, "big")
    bits = _bytes_to_bits(header + payload)
    channels_per_pixel = 1 if signature.startswith(b"stealth_png") else 3
    if len(bits) > width * height * channels_per_pixel:
        raise ValueError("Test carrier does not fit in the fixture image")

    pixels = image.load()
    bit_index = 0
    for x in range(width):
        for y in range(height):
            values = list(pixels[x, y])
            channel_indexes = (3,) if channels_per_pixel == 1 else (0, 1, 2)
            for channel_index in channel_indexes:
                if bit_index >= len(bits):
                    break
                values[channel_index] = (values[channel_index] & ~1) | bits[bit_index]
                bit_index += 1
            pixels[x, y] = tuple(values)
            if bit_index >= len(bits):
                break
        if bit_index >= len(bits):
            break
    return image


def _write_stealth_webp(
    path: Path,
    signature: bytes,
    payload: bytes,
    declared_bit_length: int,
    mode: str,
) -> None:
    base_pixel = (100, 120, 140, 254) if mode == "RGBA" else (100, 120, 140)
    image = Image.new(mode, (32, 160), color=base_pixel)
    _embed_stealth_pixels(image, signature, payload, declared_bit_length)
    image.save(path, format="WEBP", lossless=True, exact=True)


def test_signed_stealth_novelai_json_webp_uses_existing_detector(tmp_path: Path) -> None:
    image_path = tmp_path / "novelai-stealth.webp"
    metadata = {
        "Description": "signed NovelAI webp prompt",
        "Software": "NovelAI",
        "Source": "NovelAI Diffusion V5 0ADF9AB7",
        "Comment": json.dumps({
            "prompt": "signed NovelAI webp prompt",
            "uc": "bad anatomy",
            "steps": 28,
            "sampler": "k_euler",
        }),
    }
    payload = gzip.compress(json.dumps(metadata).encode("utf-8"))
    _write_stealth_webp(
        image_path,
        b"stealth_pngcomp",
        payload,
        len(payload) * 8,
        "RGBA",
    )

    result = parse_image(str(image_path))

    assert result["parse_error"] is None
    assert result["metadata_error"] is None
    assert result["generator"] == "nai"
    assert result["prompt"] == "signed NovelAI webp prompt"
    assert result["negative_prompt"] == "bad anatomy"
    assert result["checkpoint"] == "NovelAI Diffusion V5 0ADF9AB7"


@pytest.mark.parametrize(
    ("signature", "mode", "compressed"),
    [
        (b"stealth_pnginfo", "RGBA", False),
        (b"stealth_pngcomp", "RGBA", True),
        (b"stealth_rgbinfo", "RGB", False),
        (b"stealth_rgbcomp", "RGB", True),
    ],
)
def test_signed_stealth_webui_webp_carriers(
    tmp_path: Path,
    signature: bytes,
    mode: str,
    compressed: bool,
) -> None:
    image_path = tmp_path / f"{signature.decode('ascii')}.webp"
    plain_payload = _WEBUI_PARAMETERS.encode("utf-8")
    payload = gzip.compress(plain_payload) if compressed else plain_payload
    _write_stealth_webp(
        image_path,
        signature,
        payload,
        len(payload) * 8,
        mode,
    )

    result = parse_image(str(image_path))

    assert result["parse_error"] is None
    assert result["metadata_error"] is None
    assert result["generator"] == "webui"
    assert result["prompt"] == "signed carrier prompt"
    assert result["negative_prompt"] == "lowres"
    assert result["checkpoint"] == "carrier-model.safetensors"


def test_corrupt_recognized_stealth_webp_is_nonfatal(tmp_path: Path) -> None:
    image_path = tmp_path / "corrupt-stealth.webp"
    payload = b"not a gzip stream"
    _write_stealth_webp(
        image_path,
        b"stealth_pngcomp",
        payload,
        len(payload) * 8,
        "RGBA",
    )

    result = parse_image(str(image_path))

    assert result["parse_error"] is None
    assert result["generator"] == "unknown"
    assert result["metadata_error"].startswith(
        "PNG Stealth metadata could not be parsed: invalid gzip payload"
    )


def test_unsigned_webp_without_text_does_not_invent_prompt(tmp_path: Path) -> None:
    image_path = tmp_path / "plain.webp"
    Image.new("RGBA", (32, 24), color=(12, 34, 56, 255)).save(
        image_path, format="WEBP", lossless=True, exact=True
    )

    result = parse_image(str(image_path))

    assert result["parse_error"] is None
    assert result["metadata_error"] is None
    assert result["generator"] == "unknown"
    assert result["prompt"] is None


@pytest.mark.skipif(
    not _REAL_NAI_STEALTH_WEBP.is_file(),
    reason="local NovelAI stealth WebP sample is not present",
)
def test_real_novelai_stealth_webp_from_undid() -> None:
    result = parse_image(str(_REAL_NAI_STEALTH_WEBP))

    assert result["parse_error"] is None
    assert result["generator"] == "nai"
    assert result["prompt"]
    assert "masterpiece" in result["prompt"].lower()
    assert "quasarcake" in result["prompt"].lower()
