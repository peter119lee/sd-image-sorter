"""Obfuscate -> restore must not silently eat the user's generation data (audit F5).

Privacy Tools is sold as a reversible round trip: protect an image before you
share it, restore it afterwards. The pixels always came back byte-exact, but the
prompt/seed/sampler only survived one combination - PNG source in big_tomato
mode. A JPEG or WebP source lost them because chunk harvesting only understood
the PNG signature, and small_tomato dropped them by design. With "allow
overwrite" on, that loss is permanent.

The output container is a PNG in every one of these cases, and a PNG can always
carry tEXt, so no combination here genuinely cannot hold the metadata. These
tests assert the thing the user cares about: after protect-then-restore, the
prompt is still there and identical.
"""

from __future__ import annotations

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import obfuscation as obf

A1111_PARAMETERS = (
    "a girl standing in the rain, masterpiece, best quality\n"
    "Negative prompt: lowres, bad anatomy\n"
    "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 123456789, "
    "Size: 512x768, Model: someModel"
)
EXPECTED_PROMPT = "a girl standing in the rain, masterpiece, best quality"


def _write_png_source(path):
    info = PngInfo()
    info.add_text("parameters", A1111_PARAMETERS)
    Image.new("RGB", (24, 24), color="orange").save(path, pnginfo=info)


def _write_exif_source(path, image_format):
    """Write the A1111 EXIF UserComment shape, using Pillow only.

    piexif happens to be installed here but is not a declared dependency, so
    this fixture stays on Pillow's own Exif writer.
    """
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9286] = b"UNICODE\x00" + A1111_PARAMETERS.encode("utf-16-be")
    Image.new("RGB", (24, 24), color="orange").save(path, format=image_format, exif=exif)


SOURCES = {
    "png": (_write_png_source, ".png"),
    "jpeg": (lambda path: _write_exif_source(path, "JPEG"), ".jpg"),
    "webp": (lambda path: _write_exif_source(path, "WEBP"), ".webp"),
}


def _roundtrip(tmp_path, source_kind, compat_mode, password=""):
    writer, suffix = SOURCES[source_kind]
    source = tmp_path / f"source-{source_kind}-{compat_mode}{suffix}"
    protected = tmp_path / f"protected-{source_kind}-{compat_mode}.png"
    restored = tmp_path / f"restored-{source_kind}-{compat_mode}.png"
    writer(source)

    encode_result = obf.encode_image(
        str(source), str(protected), password, compat_mode=compat_mode
    )
    decode_result = obf.decode_image(
        str(protected), str(restored), password, compat_mode=compat_mode
    )
    return source, protected, restored, encode_result, decode_result


@pytest.mark.parametrize("source_kind", ["png", "jpeg", "webp"])
@pytest.mark.parametrize("compat_mode", [obf.BIG_TOMATO_MODE, obf.SMALL_TOMATO_MODE])
class TestPromptSurvivesTheRoundTrip:
    def test_restored_image_still_carries_the_original_prompt(
        self, tmp_path, test_db, source_kind, compat_mode
    ):
        source, _protected, restored, encode_result, _decode = _roundtrip(
            tmp_path, source_kind, compat_mode
        )

        with Image.open(restored) as image:
            recovered = image.info.get("parameters")

        assert recovered == A1111_PARAMETERS, (
            f"{source_kind} + {compat_mode}: the generation parameters did not "
            "survive protect -> restore"
        )
        assert encode_result["metadata_preserved"] is True

    def test_product_parser_reads_the_same_prompt_back(
        self, tmp_path, test_db, source_kind, compat_mode
    ):
        import metadata_parser

        source, _protected, restored, _encode, _decode = _roundtrip(
            tmp_path, source_kind, compat_mode
        )

        before = metadata_parser.parse_image(str(source))
        after = metadata_parser.parse_image(str(restored))

        assert before["prompt"] == EXPECTED_PROMPT, "fixture did not produce a parseable source"
        assert after["prompt"] == before["prompt"]
        assert after["negative_prompt"] == before["negative_prompt"]

    def test_protected_copy_does_not_leak_the_prompt_in_clear_text(
        self, tmp_path, test_db, source_kind, compat_mode
    ):
        """The whole point is sharing the protected file, so the carried text
        must be the encrypted form, not the readable prompt."""
        _source, protected, _restored, _encode, _decode = _roundtrip(
            tmp_path, source_kind, compat_mode
        )

        with Image.open(protected) as image:
            carried = image.info.get("parameters")

        assert carried, "nothing was carried, so nothing can be restored"
        assert carried != A1111_PARAMETERS
        assert EXPECTED_PROMPT not in carried


class TestOptOutStillWorks:
    def test_preserve_metadata_false_still_strips_everything(self, tmp_path, test_db):
        source = tmp_path / "opt-out.png"
        protected = tmp_path / "opt-out-protected.png"
        _write_png_source(source)

        result = obf.encode_image(
            str(source), str(protected), "1201", preserve_metadata=False
        )

        with Image.open(protected) as image:
            assert "parameters" not in image.info
        assert result["metadata_preserved"] is False

    def test_source_without_metadata_reports_nothing_carried(self, tmp_path, test_db):
        source = tmp_path / "bare.png"
        protected = tmp_path / "bare-protected.png"
        Image.new("RGB", (16, 16), color="grey").save(source)

        result = obf.encode_image(str(source), str(protected), "1201")

        assert result["metadata_preserved"] is True
        assert result["metadata_chunks_carried"] == 0
