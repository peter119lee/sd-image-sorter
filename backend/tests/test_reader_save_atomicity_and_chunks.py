"""Reader metadata save: crash safety, embedded-chunk preservation, animation.

Three defects this pins, all reproduced before the fix:

R1 (P0) ``services/image/serving.py`` ``_write_edited_image`` handed the final
    destination straight to ``Image.save``, which opens it ``w+b`` and therefore
    truncates it before the first new byte exists. An interrupted overwrite left
    the user's own image as undecodable garbage with no backup. Every other
    user-file writer here stages a temp sibling and publishes with
    ``os.replace`` (``dataset_export.engine._write_pillow_image_atomic``).

R2 (P0) ``services/image_metadata_writer.build_pnginfo`` built a fresh
    ``PngInfo`` from the edited fields only, so editing one field destroyed the
    ComfyUI workflow, the NovelAI ``Comment`` block and every third-party chunk.

R4 (P2) The save dropped every frame but the current one, silently turning an
    animation into a still.

Fault-injection note: patch the ``Image.SAVE`` / ``Image.SAVE_ALL`` **registry
entry**, never ``PngImagePlugin._save``. The registries captured the original
function object at import, so a module-attribute patch injects nothing and the
bug looks absent. Same class of trap as ``shutil.copy2`` routing through
``_winapi.CopyFile2`` on Windows CPython 3.12.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageSequence
from PIL.PngImagePlugin import PngInfo

import metadata_parser


COMFY_API_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 42, "steps": 20, "cfg": 7.0, "positive": ["6", 0], "negative": ["7", 0]},
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "anima_v3.safetensors"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "ORIGINAL comfy positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "ORIGINAL comfy negative"}},
}
COMFY_UI_GRAPH = {"last_node_id": 9, "nodes": [{"id": 3, "type": "KSampler"}], "links": []}
NAI_COMMENT = {
    "prompt": "ORIGINAL nai positive",
    "uc": "ORIGINAL nai negative",
    "steps": 28,
    "sampler": "k_euler_ancestral",
    "seed": 111222333,
}
WEBUI_PARAMETERS = (
    "ORIGINAL webui prompt\n"
    "Negative prompt: ORIGINAL webui negative\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 42, Size: 32x32, Model: anima_v3"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_png(path: Path, chunks: dict[str, str], *, size: tuple[int, int] = (32, 32)) -> Path:
    image = Image.new("RGB", size, (10, 20, 30))
    info = PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    image.save(path, pnginfo=info)
    return path


def _animation_frames(count: int, *, mode: str = "RGB") -> list[Image.Image]:
    frames = []
    for index in range(count):
        frame = Image.new("RGB", (32, 32), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        draw.rectangle([index * 5, index * 5, index * 5 + 9, index * 5 + 9], fill=(255, 40 * index, 10))
        if mode == "P":
            frame = frame.convert("P", palette=Image.Palette.ADAPTIVE)
        frames.append(frame)
    return frames


def _write_animation(path: Path, count: int, *, mode: str = "RGB", pnginfo: PngInfo | None = None) -> Path:
    frames = _animation_frames(count, mode=mode)
    extra = {"pnginfo": pnginfo} if pnginfo is not None else {}
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=120, loop=0, **extra)
    return path


def _frame_count(path: Path) -> int:
    with Image.open(path) as image:
        return getattr(image, "n_frames", 1)


def _text_chunks(path: Path) -> dict[str, str]:
    with Image.open(path) as image:
        return {key: value for key, value in image.info.items() if isinstance(value, str)}


def _reparse_chunk_through_app_parser(tmp_path: Path, name: str, chunks: dict[str, str]) -> dict:
    """Re-parse harvested chunks in isolation with the app's own parser.

    A preserved chunk is only worth anything if the app can still READ it. The
    saved file's own parse is dominated by the edited ``parameters`` block, so
    the surviving generation record is carried into a probe PNG and parsed
    there — proving it is still a machine-readable workflow, not just a string
    that happens to sit in the file.
    """
    probe = _write_png(tmp_path / name, chunks)
    return metadata_parser.parse_image(str(probe))


def _inject_encode_failure(monkeypatch, pil_format: str = "PNG", *, animated: bool = False) -> None:
    """Make Pillow fail part-way through encoding.

    Patches the SAVE registry entry, which is what ``Image.save`` dispatches
    through. ``monkeypatch.setitem`` restores it after the test.
    """
    Image.init()
    registry = Image.SAVE_ALL if animated else Image.SAVE
    assert pil_format in registry, f"{pil_format} missing from {'SAVE_ALL' if animated else 'SAVE'}"

    def exploding_encoder(image, fp, filename, **kwargs):
        fp.write(b"\x89PNG\r\n\x1a\n" + b"truncated-garbage" * 64)
        raise OSError(28, "No space left on device")

    monkeypatch.setitem(registry, pil_format, exploding_encoder)


def _post_save(test_client, source: Path, output: Path, fmt: str, metadata: dict, *, overwrite: bool):
    return test_client.post(
        "/api/image-metadata/save-edited",
        json={
            "source_path": str(source),
            "output_path": str(output),
            "format": fmt,
            "metadata": metadata,
            "allow_overwrite": overwrite,
        },
    )


# ---------------------------------------------------------------------------
# R1 — a failed save must not destroy the file it was overwriting
# ---------------------------------------------------------------------------

class TestFailedSaveLeavesTheOriginalIntact:
    def test_interrupted_overwrite_leaves_the_original_byte_identical(
        self, test_client, tmp_path, monkeypatch
    ):
        source = _write_png(
            tmp_path / "victim.png",
            {"parameters": WEBUI_PARAMETERS, "workflow": json.dumps(COMFY_UI_GRAPH)},
        )
        original_bytes = source.read_bytes()

        _inject_encode_failure(monkeypatch)
        response = _post_save(
            test_client, source, source, "png", {"prompt": "edited prompt"}, overwrite=True
        )

        assert response.status_code == 400, response.text
        assert source.exists(), "the user's image was removed entirely"
        assert source.read_bytes() == original_bytes, (
            "a failed save rewrote the user's original image; it must be untouched"
        )
        # Still a real image, and still the SAME image, per the app's own parser.
        reparsed = metadata_parser.parse_image(str(source))
        assert reparsed.get("parse_error") in (None, ""), reparsed.get("parse_error")
        assert reparsed["prompt"] == "ORIGINAL webui prompt"

    def test_interrupted_overwrite_leaves_no_staging_files_behind(
        self, test_client, tmp_path, monkeypatch
    ):
        folder = tmp_path / "library"
        folder.mkdir()
        source = _write_png(folder / "victim.png", {"parameters": WEBUI_PARAMETERS})

        _inject_encode_failure(monkeypatch)
        _post_save(test_client, source, source, "png", {"prompt": "edited"}, overwrite=True)

        assert sorted(entry.name for entry in folder.iterdir()) == ["victim.png"], (
            "a temp sibling or backup was abandoned in the user's own image folder"
        )

    def test_interrupted_overwrite_of_a_hardlinked_image_keeps_both_names_readable(
        self, test_client, tmp_path, monkeypatch
    ):
        source = _write_png(tmp_path / "linked.png", {"parameters": WEBUI_PARAMETERS})
        alias = tmp_path / "linked-alias.png"
        try:
            os.link(source, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")

        original_bytes = source.read_bytes()
        _inject_encode_failure(monkeypatch)
        _post_save(test_client, source, source, "png", {"prompt": "edited"}, overwrite=True)

        assert source.read_bytes() == original_bytes
        assert alias.read_bytes() == original_bytes
        assert metadata_parser.parse_image(str(alias))["prompt"] == "ORIGINAL webui prompt"

    def test_successful_overwrite_of_a_hardlinked_image_keeps_the_link(
        self, test_client, tmp_path
    ):
        source = _write_png(tmp_path / "shared.png", {"parameters": WEBUI_PARAMETERS})
        alias = tmp_path / "shared-alias.png"
        try:
            os.link(source, alias)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"filesystem cannot create hard links: {exc}")

        response = _post_save(
            test_client, source, source, "png", {"prompt": "edited prompt", "steps": 20},
            overwrite=True,
        )
        assert response.status_code == 200, response.text

        # Publishing a new inode would leave the alias holding the stale image.
        assert os.path.samefile(source, alias), (
            "the save severed the user's hard link; the other name kept stale metadata"
        )
        assert metadata_parser.parse_image(str(alias))["prompt"] == "edited prompt"


class TestStagingCannotHangOnAnUnwritableFolder:
    """Staging must report an unwritable folder, not retry until the app stalls.

    ``tempfile.mkstemp`` treats a Windows ``PermissionError`` as "that random
    name is taken" and retries up to ``TMP_MAX`` (10,000) times, because
    ``os.access`` reports an ACL-protected folder as writable. Staging through
    it turned a clean 403 into a request that never returned.
    """

    def test_a_permission_error_is_not_retried(self, monkeypatch, tmp_path):
        from services import image_metadata_writer

        attempts = []

        def refusing_open(path, flags, *args, **kwargs):
            attempts.append(path)
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(image_metadata_writer.os, "open", refusing_open)
        with pytest.raises(PermissionError):
            image_metadata_writer._create_staging_file(tmp_path / "target.png")

        assert len(attempts) == 1, (
            f"a permission error was retried {len(attempts)} times; an unwritable "
            "destination folder must fail immediately, not stall the save"
        )

    def test_the_staging_name_search_is_bounded(self, monkeypatch, tmp_path):
        from services import image_metadata_writer

        attempts = []

        def always_taken(path, flags, *args, **kwargs):
            attempts.append(path)
            raise FileExistsError(17, "File exists")

        monkeypatch.setattr(image_metadata_writer.os, "open", always_taken)
        with pytest.raises(FileExistsError):
            image_metadata_writer._create_staging_file(tmp_path / "target.png")

        assert len(attempts) == image_metadata_writer.STAGING_NAME_ATTEMPTS
        assert len(set(attempts)) == len(attempts), "the search reused a name"

    def test_an_unwritable_destination_still_fails_fast_end_to_end(
        self, test_client, tmp_path
    ):
        import platform
        import time

        source = _write_png(tmp_path / "src.png", {"parameters": WEBUI_PARAMETERS})
        unwritable = (
            "C:\\Windows\\System32\\readerfix-guard.png"
            if platform.system() == "Windows"
            else "/proc/readerfix-guard.png"
        )

        started = time.monotonic()
        response = _post_save(
            test_client, source, Path(unwritable), "png", {"prompt": "edited"},
            overwrite=True,
        )
        elapsed = time.monotonic() - started

        assert response.status_code in (400, 403), response.text
        assert elapsed < 60, (
            f"the save took {elapsed:.0f}s to reject an unwritable folder; "
            "staging is retrying instead of reporting the error"
        )


# ---------------------------------------------------------------------------
# R2 — embedded chunks the user did not edit must survive
# ---------------------------------------------------------------------------

class TestEmbeddedChunkPreservation:
    def test_comfyui_workflow_survives_an_edit_to_an_unrelated_field(
        self, test_client, tmp_path
    ):
        source = _write_png(
            tmp_path / "comfy.png",
            {
                "prompt": json.dumps(COMFY_API_GRAPH),
                "workflow": json.dumps(COMFY_UI_GRAPH),
            },
        )
        output = tmp_path / "comfy-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"seed": "999"}, overwrite=False
        )
        assert response.status_code == 200, response.text

        saved_chunks = _text_chunks(output)
        assert "prompt" in saved_chunks, "the ComfyUI API workflow was destroyed"
        assert "workflow" in saved_chunks, "the ComfyUI UI workflow was destroyed"
        assert json.loads(saved_chunks["prompt"]) == COMFY_API_GRAPH
        assert json.loads(saved_chunks["workflow"]) == COMFY_UI_GRAPH

        # The surviving graph is still readable BY THE APP, not merely present.
        recovered = _reparse_chunk_through_app_parser(
            tmp_path, "probe-comfy.png", {"prompt": saved_chunks["prompt"]}
        )
        assert recovered["generator"] == "comfyui"
        assert recovered["prompt"] == "ORIGINAL comfy positive"
        assert recovered["checkpoint"] == "anima_v3.safetensors"

        # ...and the user is told the kept record still holds the old values.
        assert any("workflow" in warning for warning in response.json()["warnings"]), (
            "preserving a stale generation record must be disclosed, not silent"
        )

    def test_novelai_comment_and_third_party_chunks_survive(self, test_client, tmp_path):
        source = _write_png(
            tmp_path / "nai.png",
            {
                "Comment": json.dumps(NAI_COMMENT),
                "Software": "NovelAI",
                "MyCustomChunk": "third-party payload",
                "Description": "a description someone wrote",
            },
        )
        output = tmp_path / "nai-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"prompt": "edited prompt", "steps": 28},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        saved_chunks = _text_chunks(output)
        assert "Comment" in saved_chunks, "the NovelAI generation record was destroyed"
        assert saved_chunks["MyCustomChunk"] == "third-party payload"
        assert saved_chunks["Description"] == "a description someone wrote"

        recovered = _reparse_chunk_through_app_parser(
            tmp_path,
            "probe-nai.png",
            {"Comment": saved_chunks["Comment"], "Software": "NovelAI"},
        )
        assert recovered["generator"] == "nai"
        assert recovered["prompt"] == "ORIGINAL nai positive"

    def test_the_edit_still_wins_when_a_generation_record_is_preserved(
        self, test_client, tmp_path
    ):
        """Preserving must not hide the user's edit behind the old record."""
        source = _write_png(
            tmp_path / "both.png",
            {"prompt": json.dumps(COMFY_API_GRAPH), "workflow": json.dumps(COMFY_UI_GRAPH)},
        )
        output = tmp_path / "both-edited.png"

        response = _post_save(
            test_client,
            source,
            output,
            "png",
            {"prompt": "EDITED prompt", "negative_prompt": "EDITED negative", "steps": 20,
             "sampler": "Euler a"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        reparsed = metadata_parser.parse_image(str(output))
        assert reparsed["prompt"] == "EDITED prompt"
        assert reparsed["negative_prompt"] == "EDITED negative"

    def test_a_cleared_field_is_not_resurrected_from_the_source(self, test_client, tmp_path):
        """A stale plain-text chunk must lose to the editor, unlike a workflow."""
        source = _write_png(
            tmp_path / "stale.png",
            {"parameters": WEBUI_PARAMETERS, "prompt": "STALE plain prompt text"},
        )
        output = tmp_path / "stale-edited.png"

        response = _post_save(
            test_client, source, output, "png",
            {"negative_prompt": "only a negative now", "steps": 20, "sampler": "Euler a"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        saved_chunks = _text_chunks(output)
        assert saved_chunks.get("prompt") != "STALE plain prompt text", (
            "a cleared prompt was resurrected from the source's stale prompt chunk"
        )
        assert "STALE plain prompt text" not in (
            metadata_parser.parse_image(str(output)).get("prompt") or ""
        )

    def test_in_place_overwrite_also_preserves_chunks(self, test_client, tmp_path):
        source = _write_png(
            tmp_path / "inplace.png",
            {"parameters": WEBUI_PARAMETERS, "workflow": json.dumps(COMFY_UI_GRAPH)},
        )

        response = _post_save(
            test_client, source, source, "png", {"prompt": "edited", "steps": 20},
            overwrite=True,
        )
        assert response.status_code == 200, response.text
        assert json.loads(_text_chunks(source)["workflow"]) == COMFY_UI_GRAPH

    def test_saving_to_jpeg_names_the_chunks_it_cannot_carry(self, test_client, tmp_path):
        source = _write_png(
            tmp_path / "rich.png",
            {
                "prompt": json.dumps(COMFY_API_GRAPH),
                "workflow": json.dumps(COMFY_UI_GRAPH),
                "MyCustomChunk": "third-party payload",
            },
        )
        output = tmp_path / "rich.jpg"

        response = _post_save(
            test_client, source, output, "jpg", {"prompt": "edited"}, overwrite=False
        )
        assert response.status_code == 200, response.text

        warnings = " ".join(response.json()["warnings"])
        assert "workflow" in warnings, (
            "JPEG cannot carry the workflow and the user must be told which blocks were lost"
        )
        assert "MyCustomChunk" in warnings

    def test_a_source_without_extra_chunks_still_warns_about_nothing(
        self, test_client, tmp_path
    ):
        """No preserved record, no invented warning (the existing PNG contract)."""
        source = tmp_path / "plain.png"
        Image.new("RGB", (16, 16), "white").save(source)
        output = tmp_path / "plain-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"prompt": "cat"}, overwrite=False
        )
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []

    @pytest.mark.parametrize("container,extension", [("WEBP", "webp"), ("JPEG", "jpg")])
    def test_a_workflow_in_a_non_png_source_survives(
        self, test_client, tmp_path, container, extension
    ):
        """A JPEG/WebP keeps its graph in EXIF, so the harvest must be container-aware.

        Reading only PNG text chunks is how the workflow went missing for these
        formats; the same re-keying obfuscation does is what recovers it.
        """
        source = tmp_path / f"comfy-source.{extension}"
        image = Image.new("RGB", (32, 32), (4, 5, 6))
        exif = image.getexif()
        exif.get_ifd(0x8769)[0x9286] = b"UNICODE\x00" + json.dumps(
            COMFY_API_GRAPH
        ).encode("utf-16-le")
        image.save(source, format=container, exif=exif.tobytes())
        output = tmp_path / f"comfy-{extension}-edited.png"

        response = _post_save(
            test_client, source, output, "png",
            {"prompt": "EDITED prompt", "steps": 20, "sampler": "Euler a"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        saved_prompt_chunk = _text_chunks(output).get("prompt", "")
        assert saved_prompt_chunk.lstrip().startswith("{"), (
            f"the {container} source's workflow is gone from the saved file; its prompt "
            f"chunk holds {saved_prompt_chunk[:60]!r} instead of the graph"
        )
        assert json.loads(saved_prompt_chunk) == COMFY_API_GRAPH

        recovered = _reparse_chunk_through_app_parser(
            tmp_path, f"probe-{extension}.png", {"prompt": saved_prompt_chunk}
        )
        assert recovered["generator"] == "comfyui"
        assert recovered["prompt"] == "ORIGINAL comfy positive"

        # The edit itself still governs the saved file.
        assert metadata_parser.parse_image(str(output))["prompt"] == "EDITED prompt"

    def test_a_non_png_source_saved_as_webp_keeps_its_workflow_and_is_not_warned_about(
        self, test_client, tmp_path
    ):
        """No crying wolf: EXIF survives a WebP re-save, so nothing is 'lost'."""
        source = tmp_path / "graph-source.webp"
        image = Image.new("RGB", (32, 32), (4, 5, 6))
        exif = image.getexif()
        exif.get_ifd(0x8769)[0x9286] = b"UNICODE\x00" + json.dumps(
            COMFY_API_GRAPH
        ).encode("utf-16-le")
        image.save(source, format="WEBP", exif=exif.tobytes())
        output = tmp_path / "graph-source-edited.webp"

        response = _post_save(
            test_client, source, output, "webp",
            {"prompt": "EDITED prompt", "steps": 20, "sampler": "Euler a"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        with Image.open(output) as saved:
            carried = saved.getexif().get_ifd(0x8769).get(0x9286)
        assert carried is not None, "the workflow really was dropped"

        warnings = " ".join(response.json()["warnings"])
        assert "cannot store" not in warnings, (
            f"warned about a loss that did not happen: {warnings!r}"
        )

    def test_settings_the_editor_cannot_rebuild_are_reported_not_dropped_silently(
        self, test_client, tmp_path
    ):
        """The editor owns ``parameters``; what it cannot carry must be named."""
        rich_parameters = (
            "a prompt\nNegative prompt: a negative\n"
            "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 42, Size: 32x32, "
            "Model: anima_v3, Model hash: abc123, Clip skip: 2"
        )
        source = _write_png(tmp_path / "rich-params.png", {"parameters": rich_parameters})
        output = tmp_path / "rich-params-edited.png"

        response = _post_save(
            test_client, source, output, "png",
            {"prompt": "EDITED", "negative_prompt": "a negative", "steps": 20,
             "sampler": "Euler a", "cfg_scale": 7, "seed": 42, "size": "32x32",
             "model": "anima_v3"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text

        warnings = " ".join(response.json()["warnings"])
        assert "Model hash" in warnings and "Clip skip" in warnings, (
            "settings the rebuilt parameter block loses must be named, not dropped silently"
        )
        # ...and only the genuinely missing ones are named.
        assert "Sampler" not in warnings


# ---------------------------------------------------------------------------
# R4 — an animated image must keep its frames, or the save must be refused
# ---------------------------------------------------------------------------

class TestAnimationIsNotSilentlyDiscarded:
    def test_animated_webp_keeps_every_frame_as_webp(self, test_client, tmp_path):
        source = _write_animation(tmp_path / "anim.webp", 4)
        assert _frame_count(source) == 4
        output = tmp_path / "anim-edited.webp"

        response = _post_save(
            test_client, source, output, "webp",
            {"prompt": "edited", "steps": 20, "sampler": "Euler a"},
            overwrite=False,
        )
        assert response.status_code == 200, response.text
        assert _frame_count(output) == 4, "the animation was flattened to a still"
        assert metadata_parser.parse_image(str(output))["prompt"] == "edited"

    def test_animated_webp_keeps_every_frame_as_apng(self, test_client, tmp_path):
        source = _write_animation(tmp_path / "anim2.webp", 4)
        output = tmp_path / "anim2-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"prompt": "edited", "steps": 20},
            overwrite=False,
        )
        assert response.status_code == 200, response.text
        assert _frame_count(output) == 4
        assert metadata_parser.parse_image(str(output))["prompt"] == "edited"

    def test_animated_palette_source_keeps_its_frames(self, test_client, tmp_path):
        """Palette frames crash Pillow's APNG encoder unless normalized first."""
        source = _write_animation(tmp_path / "anim.gif", 4, mode="P")
        assert _frame_count(source) == 4
        output = tmp_path / "anim-gif-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"prompt": "edited"}, overwrite=False
        )
        assert response.status_code == 200, response.text
        assert _frame_count(output) == 4

    def test_animated_in_place_overwrite_keeps_its_frames(self, test_client, tmp_path):
        source = _write_animation(tmp_path / "anim-inplace.webp", 3)

        response = _post_save(
            test_client, source, source, "webp", {"prompt": "edited"}, overwrite=True
        )
        assert response.status_code == 200, response.text
        assert _frame_count(source) == 3

    def test_saving_an_animation_as_jpeg_warns_that_the_animation_is_lost(
        self, test_client, tmp_path
    ):
        source = _write_animation(tmp_path / "anim3.webp", 4)
        output = tmp_path / "anim3.jpg"

        response = _post_save(
            test_client, source, output, "jpg", {"prompt": "edited"}, overwrite=False
        )
        assert response.status_code == 200, response.text
        warnings = " ".join(response.json()["warnings"]).lower()
        assert "animation" in warnings, (
            "flattening an animation into a JPEG must be disclosed, not silent"
        )
        assert _frame_count(output) == 1

    def test_an_oversized_animation_is_refused_rather_than_flattened(
        self, test_client, tmp_path, monkeypatch
    ):
        from services import image_metadata_writer

        monkeypatch.setattr(image_metadata_writer, "MAX_ANIMATION_TOTAL_PIXELS", 1024)
        source = _write_animation(tmp_path / "big.webp", 4)
        original_bytes = source.read_bytes()

        response = _post_save(
            test_client, source, source, "webp", {"prompt": "edited"}, overwrite=True
        )
        assert response.status_code == 400, response.text
        assert "animation" in response.text.lower()
        assert source.read_bytes() == original_bytes, (
            "the refusal must leave the animation exactly as it was"
        )

    def test_a_still_image_is_unaffected_by_the_animation_path(self, test_client, tmp_path):
        source = _write_png(tmp_path / "still.png", {"parameters": WEBUI_PARAMETERS})
        output = tmp_path / "still-edited.png"

        response = _post_save(
            test_client, source, output, "png", {"prompt": "edited", "steps": 20},
            overwrite=False,
        )
        assert response.status_code == 200, response.text
        assert _frame_count(output) == 1
        assert metadata_parser.parse_image(str(output))["prompt"] == "edited"
        with Image.open(output) as image:
            assert list(ImageSequence.Iterator(image)).__len__() == 1
