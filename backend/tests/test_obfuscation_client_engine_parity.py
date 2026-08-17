"""The client obfuscation engine and backend/obfuscation.py must agree (audit F5).

8982d08 made backend source harvesting container-aware, but the Privacy Tools
queue runs window.ObfuscateEngine by default, so the fix only reached the
file-path endpoints and the preview fallback. The client engine was then brought
in line, and a user must not get a different result depending on whether their
file happened to go through the queue or the backend.

This is the backend half of a two-sided pin, following the same shape as the
existing reference-site parity pair (test_obfuscation_reference_parity.py +
tests/e2e/specs/obfuscation-parity.spec.ts):

  * both halves read the SAME committed fixture bytes in
    tests/e2e/fixtures/obfuscation/, written by Pillow with the tag shapes real
    SD tools produce (see make-sd-metadata-fixtures.py there);
  * this file pins what backend harvesting returns for those bytes;
  * obfuscation-metadata-roundtrip.spec.ts pins that the browser engine's
    extractSourceTextChunksFromBytes returns exactly the same pairs.

Harvesting is the only stage that differed. Pixel scrambling and both text
crypto algorithms are already pinned byte-exact across the two implementations by
the reference-parity pair, so identical harvested chunks means the whole
protect/restore path agrees for every container and compat mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import obfuscation as obf

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "e2e" / "fixtures" / "obfuscation"
SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "e2e" / "specs" / "obfuscation-metadata-roundtrip.spec.ts"
)

# The literal the fixtures embed, split the way the spec builds it so a drift on
# either side is caught by test_spec_and_backend_agree_on_the_expected_literal.
A1111_PARAMETER_LINES = (
    "a girl standing in the rain, masterpiece, best quality",
    "Negative prompt: lowres, bad anatomy",
    "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 123456789, Size: 512x768, Model: someModel",
)
A1111_PARAMETERS = "\n".join(A1111_PARAMETER_LINES)
EXPECTED_PROMPT = A1111_PARAMETER_LINES[0]

# Source container -> the (key, value) pairs harvesting must produce. The client
# spec asserts this same table.
EXPECTED_HARVEST = {
    "sd-metadata-source.png": [("parameters", A1111_PARAMETERS)],
    "sd-metadata-source.jpg": [("parameters", A1111_PARAMETERS)],
    "sd-metadata-source.webp": [("parameters", A1111_PARAMETERS)],
    "no-metadata-source.png": [],
}

SD_SOURCES = [name for name in EXPECTED_HARVEST if name.startswith("sd-")]


@pytest.mark.parametrize("fixture_name", sorted(EXPECTED_HARVEST))
def test_backend_harvest_matches_the_table_the_client_spec_pins(fixture_name):
    """PNG tEXt, JPEG EXIF and WebP EXIF must all land on the same chunk key."""
    data = (FIXTURE_DIR / fixture_name).read_bytes()

    assert obf.extract_source_text_chunks_from_bytes(data) == EXPECTED_HARVEST[fixture_name]


@pytest.mark.parametrize("fixture_name", sorted(SD_SOURCES))
@pytest.mark.parametrize("compat_mode", [obf.BIG_TOMATO_MODE, obf.SMALL_TOMATO_MODE])
def test_backend_round_trip_on_the_shared_fixtures_restores_the_prompt(
    tmp_path, test_db, fixture_name, compat_mode
):
    """The same user-facing claim the client spec makes, on the same bytes."""
    source = tmp_path / fixture_name
    source.write_bytes((FIXTURE_DIR / fixture_name).read_bytes())
    protected = tmp_path / f"protected-{compat_mode}.png"
    restored = tmp_path / f"restored-{compat_mode}.png"
    password = "0512" if compat_mode == obf.BIG_TOMATO_MODE else ""

    obf.encode_image(str(source), str(protected), password, compat_mode=compat_mode)
    obf.decode_image(str(protected), str(restored), password, compat_mode=compat_mode)

    with Image.open(protected) as image:
        carried = image.info.get("parameters")
    with Image.open(restored) as image:
        recovered = image.info.get("parameters")

    assert recovered == A1111_PARAMETERS
    # The protected copy is the one that gets shared, so it must carry the
    # encrypted form, never the readable prompt.
    assert carried and carried != A1111_PARAMETERS
    assert EXPECTED_PROMPT not in carried


def test_spec_and_backend_agree_on_the_expected_literal():
    """Guard the one thing a shared fixture cannot guard: the expected string.

    If either side edits the parameter block without the other, harvesting could
    still 'pass' on both sides while asserting different text.
    """
    spec_source = SPEC_PATH.read_text(encoding="utf-8")

    for line in A1111_PARAMETER_LINES:
        assert f"'{line}'" in spec_source, f"the client spec no longer pins: {line}"


def test_fixtures_are_the_containers_they_claim_to_be():
    """A regenerated fixture that silently lost its EXIF would make both halves
    agree on nothing at all, so pin the container and the tag location too."""
    with Image.open(FIXTURE_DIR / "sd-metadata-source.png") as image:
        assert image.format == "PNG"
        assert image.info.get("parameters") == A1111_PARAMETERS

    for fixture_name, expected_format in (
        ("sd-metadata-source.jpg", "JPEG"),
        ("sd-metadata-source.webp", "WEBP"),
    ):
        with Image.open(FIXTURE_DIR / fixture_name) as image:
            assert image.format == expected_format
            # The prompt must live in EXIF UserComment, not in a PNG text chunk:
            # reading PNG chunks only is exactly what used to drop it.
            assert "parameters" not in image.info
            assert image.getexif().get_ifd(0x8769).get(0x9286)

        raw = (FIXTURE_DIR / fixture_name).read_bytes()
        assert obf.extract_png_text_chunks_from_bytes(raw) == []
