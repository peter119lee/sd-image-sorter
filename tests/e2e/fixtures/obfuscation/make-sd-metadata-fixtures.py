"""Regenerate the SD-metadata source fixtures used by obfuscation-metadata-roundtrip.spec.ts.

The client obfuscation engine has to harvest the generation parameters out of
whatever container the user dropped in. Hand-assembling a JPEG APP1 segment or a
VP8X WebP in the spec would only prove the engine can read bytes the spec itself
invented, so the fixtures are written by Pillow instead - the same writer, and
the same tag shapes, as backend/tests/test_obfuscation_metadata_roundtrip.py.
That makes a client-side pass real evidence that the two paths agree.

Run from the repo root:
    backend\\venv\\Scripts\\python.exe tests\\e2e\\fixtures\\obfuscation\\make-sd-metadata-fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

# Kept character-for-character in sync with the spec's A1111_PARAMETERS and with
# backend/tests/test_obfuscation_metadata_roundtrip.py.
A1111_PARAMETERS = (
    "a girl standing in the rain, masterpiece, best quality\n"
    "Negative prompt: lowres, bad anatomy\n"
    "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 123456789, "
    "Size: 512x768, Model: someModel"
)

SIZE = 24


def _base_image() -> Image.Image:
    """A deterministic non-uniform pattern.

    A flat colour would let a broken pixel scramble pass unnoticed, so the spec
    also compares restored pixels against the decoded source.
    """
    image = Image.new("RGB", (SIZE, SIZE))
    image.putdata(
        [
            ((x * 11) % 256, (y * 7) % 256, (x * y) % 256)
            for y in range(SIZE)
            for x in range(SIZE)
        ]
    )
    return image


def main() -> None:
    out_dir = Path(__file__).resolve().parent

    info = PngInfo()
    info.add_text("parameters", A1111_PARAMETERS)
    _base_image().save(out_dir / "sd-metadata-source.png", pnginfo=info)

    for suffix, image_format in ((".jpg", "JPEG"), (".webp", "WEBP")):
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9286] = b"UNICODE\x00" + A1111_PARAMETERS.encode("utf-16-be")
        _base_image().save(out_dir / f"sd-metadata-source{suffix}", format=image_format, exif=exif)

    # A source with no generation data at all, for the "nothing to carry" path.
    _base_image().save(out_dir / "no-metadata-source.png")

    for path in sorted(out_dir.glob("*-source.*")):
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
