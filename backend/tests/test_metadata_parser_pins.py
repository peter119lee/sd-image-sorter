"""Characterization pins for metadata_parser BEFORE decomposition.

The dedicated suites (test_metadata_parser.py, _errors.py, _comfyui_enhanced.py,
_webp_fast.py, test_civitai_integration.py, test_metadata_parser_params.py) already
exercise the per-generator parse paths in depth (~176 tests, 75% line coverage).
This file does NOT re-test those. It locks the *cross-module contract* that a
`metadata_parser` -> `metadata/` package split could silently break, plus a few
load-bearing behaviors that no existing test asserts:

  * the public import surface consumers depend on (image_manager, similarity,
    services/*, tagging worker) and the module globals tests monkeypatch;
  * the `get_parser()` singleton identity;
  * the exact `_parsed` key set + version stamp that image_manager and the
    frontend read;
  * the Metadata-L3 "raw <-> no-prompt" invariant AT THE PARSER BOUNDARY
    (parse() emits `raw_metadata_text` iff it produced no positive prompt) and
    the `_capture_raw_metadata_text` envelope shape + size caps that
    image_manager compresses into `raw_metadata_gz`;
  * `verify_image_readable`, a public entry point (used by similarity,
    sorting_service, tagging worker) whose real body is UNCOVERED by the
    dedicated suites.

Everything here pins CURRENT behavior AS-IS (characterization), including quirks.
Values were confirmed empirically against the live module on 2026-07-13.
"""

import json
import sys
from pathlib import Path

import pytest

# Match the existing suite's import shape: consumers and tests use BOTH
# `import metadata_parser as <module>` (to monkeypatch module globals) and
# `from metadata_parser import <name>`. Both must keep working post-split.
sys.path.insert(0, str(Path(__file__).parent.parent))

import metadata_parser as metadata_parser_module
from metadata_storage import compact_metadata_dict
from metadata_parser import (
    PARSED_METADATA_VERSION,
    MetadataParser,
    get_parser,
    parse_image,
    verify_image_readable,
)


# --------------------------------------------------------------------------
# Fixtures (same construction style as test_metadata_parser.py)
# --------------------------------------------------------------------------
def _write_png(path: Path, text_chunks: dict, size=(64, 64)) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for key, value in text_chunks.items():
        info.add_text(key, value)
    Image.new("RGB", size, color="white").save(path, pnginfo=info)
    return path


_WEBUI_PARAMS = (
    "masterpiece\nNegative prompt: lowres\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, "
    "Size: 320x240, Model: demo.safetensors"
)

_COMFYUI_PROMPT = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
    "2": {
        "class_type": "KSampler",
        "inputs": {"positive": ["1", 0], "negative": ["1", 0]},
    },
}


# --------------------------------------------------------------------------
# Group A — public import surface + monkeypatch seams (decomposition guard)
# --------------------------------------------------------------------------
def test_public_api_names_importable_from_top_module():
    """These five names are imported by production consumers and tests.

    image_manager: `from metadata_parser import PARSED_METADATA_VERSION, parse_image`
    similarity / sorting_service / tagging.worker / image_service:
        `from metadata_parser import verify_image_readable`
    metadata_repair_service / test suites: `from metadata_parser import MetadataParser`
    A package split must keep all of them re-exported from the `metadata_parser`
    top-level (e.g. via `metadata_parser/__init__.py`).
    """
    for name in (
        "parse_image",
        "verify_image_readable",
        "get_parser",
        "MetadataParser",
        "PARSED_METADATA_VERSION",
    ):
        assert hasattr(metadata_parser_module, name), f"missing public symbol: {name}"


def test_module_global_patch_seams_present():
    """Existing tests monkeypatch these module globals on the top module.

    test_metadata_parser.py patches `metadata_parser_module.Image.open` and
    `metadata_parser_module._MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES`;
    test_metadata_parser_errors.py patches
    `metadata_parser_module._MAX_DECOMPRESSED_BYTES`. If the split moves these
    off the top module, those suites break silently at patch time — pin the
    seam here so the contract is explicit for whoever does the decomposition.
    """
    for name in (
        "Image",  # `from PIL import Image` at module scope; patched as .Image.open
        "_MAX_DECOMPRESSED_BYTES",
        "_MAX_SIDECAR_DIRECTORY_CACHE_FILENAMES",
        "_MAX_XMP_CHUNK_BYTES",
    ):
        assert hasattr(metadata_parser_module, name), f"missing patch seam: {name}"


def test_parsed_metadata_version_is_current_value():
    """image_manager gates re-parse on `stored_version < PARSED_METADATA_VERSION`
    (image_manager.py:256). Pin the current wire value so a bump is a deliberate,
    visible change rather than an accident of the module move."""
    assert PARSED_METADATA_VERSION == 8
    assert isinstance(PARSED_METADATA_VERSION, int)


# --------------------------------------------------------------------------
# Group B — singleton contract
# --------------------------------------------------------------------------
def test_get_parser_returns_process_singleton():
    """get_parser() memoizes a single MetadataParser (CLAUDE.md 'Singleton
    models'). parse_image() delegates to it. Decomposition must preserve the
    module-level `_parser` cache, not construct per-call."""
    first = get_parser()
    second = get_parser()
    assert first is second
    assert isinstance(first, MetadataParser)


def test_parse_image_delegates_to_singleton(tmp_path, monkeypatch):
    """parse_image(path, ...) == get_parser().parse(path, ...): it must route
    through the singleton, forwarding validate_image_data."""
    sentinel = {"generator": "sentinel", "prompt": None}
    seen = {}

    def fake_parse(path, validate_image_data=False):
        seen["path"] = path
        seen["validate"] = validate_image_data
        return sentinel

    monkeypatch.setattr(get_parser(), "parse", fake_parse)
    out = parse_image("some/path.png", validate_image_data=True)
    assert out is sentinel
    assert seen == {"path": "some/path.png", "validate": True}


# --------------------------------------------------------------------------
# Group C — parse() output contract (top-level shape + _parsed block)
# --------------------------------------------------------------------------
def test_parse_result_top_level_keys(tmp_path):
    """parse() always returns this exact top-level key set (plus optional
    `raw_metadata_text` / `civitai_resources`). The frontend and image_manager
    read these positionally; a rename during the split would break ingestion."""
    result = parse_image(str(_write_png(tmp_path / "plain.png", {})))
    for key in (
        "generator",
        "prompt",
        "negative_prompt",
        "checkpoint",
        "loras",
        "metadata",
        "width",
        "height",
        "file_size",
        "parse_error",
    ):
        assert key in result, f"missing top-level key: {key}"


def test_parsed_block_exact_key_set(tmp_path):
    """metadata['_parsed'] carries EXACTLY these 8 keys on every parse.

    This is the structured payload the frontend reads (generation_params,
    is_img2img, character_prompts, prompt_nodes, model_assets, civitai_resources)
    and image_manager reads (version). Pin the full set so an accidental
    add/drop during decomposition is caught.
    """
    result = parse_image(
        str(_write_png(tmp_path / "webui.png", {"parameters": _WEBUI_PARAMS}))
    )
    parsed = result["metadata"]["_parsed"]
    assert set(parsed.keys()) == {
        "version",
        "generation_params",
        "is_img2img",
        "img2img_info",
        "character_prompts",
        "prompt_nodes",
        "model_assets",
        "civitai_resources",
        "sidecar_fallback",
    }


def test_parsed_block_version_matches_constant(tmp_path):
    """`_parsed['version']` is stamped from PARSED_METADATA_VERSION."""
    result = parse_image(
        str(_write_png(tmp_path / "webui.png", {"parameters": _WEBUI_PARAMS}))
    )
    assert result["metadata"]["_parsed"]["version"] == PARSED_METADATA_VERSION


def test_sidecar_fallback_marker_is_compact_and_does_not_persist_raw_data(tmp_path):
    from PIL import Image

    image_path = tmp_path / "compact-sidecar.jpg"
    sidecar_path = tmp_path / "compact-sidecar.jpg.txt"
    Image.new("RGB", (64, 64), color="white").save(image_path, "JPEG")
    sidecar_path.write_text("private sidecar prompt", encoding="utf-8")

    result = parse_image(str(image_path))
    compact = compact_metadata_dict(result["metadata"])

    expected = {
        "schema_version": 1,
        "evaluated": True,
        "evidence": [
            {
                "carrier": "txt",
                "basename": "compact-sidecar.jpg.txt",
                "method": "sidecar_fallback",
                "confidence": "high",
                "parser_version": PARSED_METADATA_VERSION,
                # Plain sidecar text is caption text, not the generation
                # prompt (migration 042) — the provenance names what it filled.
                "fields": ["sidecar_caption"],
            }
        ],
    }
    assert compact["_parsed"]["sidecar_fallback"] == expected
    serialized = json.dumps(compact, ensure_ascii=False)
    assert "private sidecar prompt" not in serialized
    assert str(tmp_path) not in serialized


# --------------------------------------------------------------------------
# Group D — Metadata-L3 raw<->no-prompt invariant at the PARSER boundary
# --------------------------------------------------------------------------
def test_raw_metadata_text_captured_when_no_positive_prompt(tmp_path):
    """When parse() finds no positive prompt but the file DOES carry string
    chunks, it emits `raw_metadata_text`: a JSON envelope mapping every string
    chunk name to its text. image_manager compresses this into `raw_metadata_gz`
    for the 'Re-parse failed images' job (Metadata L3)."""
    png = _write_png(
        tmp_path / "noprompt.png",
        {"Description": "just some freeform text", "Comment": "hello"},
    )
    result = parse_image(str(png))
    assert result["generator"] == "unknown"
    assert not result["prompt"]
    assert "raw_metadata_text" in result
    envelope = json.loads(result["raw_metadata_text"])
    assert envelope == {"Description": "just some freeform text", "Comment": "hello"}


@pytest.mark.parametrize(
    "chunks",
    [
        {"parameters": _WEBUI_PARAMS},
        {"prompt": json.dumps(_COMFYUI_PROMPT)},
    ],
    ids=["webui", "comfyui"],
)
def test_raw_metadata_text_absent_when_positive_prompt_parsed(tmp_path, chunks):
    """The other half of the invariant: once a positive prompt is recovered,
    parse() does NOT attach `raw_metadata_text` (image_manager then clears any
    stored raw envelope). Matches the DB-side raw_metadata_gz CASE in
    test_derived_state_contract.py."""
    png = _write_png(tmp_path / "withprompt.png", chunks)
    result = parse_image(str(png))
    assert result["prompt"] and result["prompt"].strip()
    assert "raw_metadata_text" not in result


def test_raw_metadata_text_absent_when_no_chunks(tmp_path):
    """No string chunks at all -> nothing to retain -> no raw_metadata_text."""
    result = parse_image(str(_write_png(tmp_path / "bare.png", {})))
    assert "raw_metadata_text" not in result


# --------------------------------------------------------------------------
# Group E — _capture_raw_metadata_text envelope shape + caps (quirks)
# --------------------------------------------------------------------------
def test_capture_raw_skips_oversized_chunk_but_keeps_rest():
    """A single chunk larger than RAW_METADATA_CHUNK_CAP is dropped; smaller
    chunks in the same envelope survive. The cap uses strict `>`, so a chunk
    exactly AT the cap is KEPT (quirk pinned as-is)."""
    parser = get_parser()
    oversized = "x" * (parser.RAW_METADATA_CHUNK_CAP + 1)
    at_cap = "q" * parser.RAW_METADATA_CHUNK_CAP
    envelope = json.loads(
        parser._capture_raw_metadata_text(
            {"big": oversized, "keep": "short", "edge": at_cap}
        )
    )
    assert "big" not in envelope
    assert envelope["keep"] == "short"
    assert envelope["edge"] == at_cap  # == cap is not > cap, so retained


def test_capture_raw_returns_none_over_total_cap():
    """Chunks each under the per-chunk cap but summing over RAW_METADATA_TOTAL_CAP
    abort the whole capture (return None) rather than truncating."""
    parser = get_parser()
    chunk = "z" * (parser.RAW_METADATA_CHUNK_CAP - 100)  # under per-chunk cap, kept
    envelope = {"a": chunk, "b": chunk, "c": chunk}  # ~3 * 2MB > 4MB total
    assert parser._capture_raw_metadata_text(envelope) is None


def test_capture_raw_skips_non_string_and_blank_values():
    """Only non-blank string values are retained; non-str values and
    whitespace-only strings are skipped. An envelope that ends up empty
    returns None."""
    parser = get_parser()
    assert json.loads(parser._capture_raw_metadata_text({"n": 123, "s": "ok"})) == {
        "s": "ok"
    }
    assert parser._capture_raw_metadata_text({"blank": "   "}) is None
    assert parser._capture_raw_metadata_text({}) is None


# --------------------------------------------------------------------------
# Group F — verify_image_readable (public entry point, body UNCOVERED)
# --------------------------------------------------------------------------
def test_verify_image_readable_good_png(tmp_path):
    """A decodable image returns exactly (True, None)."""
    from PIL import Image

    good = tmp_path / "good.png"
    Image.new("RGB", (8, 8), color="white").save(good)
    assert verify_image_readable(str(good)) == (True, None)


def test_verify_image_readable_truncated(tmp_path):
    """A truncated file returns (False, <non-empty error string>). Pillow's
    default (LOAD_TRUNCATED_IMAGES unset) raises on the truncated load."""
    from PIL import Image

    good = tmp_path / "good.png"
    Image.new("RGB", (8, 8), color="white").save(good)
    truncated = tmp_path / "truncated.png"
    raw = good.read_bytes()
    truncated.write_bytes(raw[: len(raw) // 2])

    ok, err = verify_image_readable(str(truncated))
    assert ok is False
    assert isinstance(err, str) and err


def test_verify_image_readable_missing_file(tmp_path):
    """A path that does not exist returns (False, <error string>), never raises."""
    ok, err = verify_image_readable(str(tmp_path / "nope.png"))
    assert ok is False
    assert isinstance(err, str) and err
