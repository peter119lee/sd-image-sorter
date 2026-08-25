"""After scan, gallery search/filter/detail must use the new parser payload.

Parser-only wins are not enough: the user-visible gallery is the scanned
SQLite row (compact ``metadata_json``, ``prompt`` tokens, checkpoint, generator).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from db_core import PROMPT_MATCH_MODE_CONTAINS, PROMPT_MATCH_MODE_EXACT
from image_manager import scan_folder
from metadata_parser import PARSED_METADATA_VERSION

V5_SLOT_TOKEN = "zxqv5charslot"


def _write_png(path: Path, chunks: dict[str, str]) -> Path:
    info = PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    Image.new("RGB", (64, 64), color="white").save(path, pnginfo=info)
    return path


def _v5_comment() -> str:
    return json.dumps(
        {
            "prompt": "n::",
            "uc": "lowres",
            "steps": 28,
            "sampler": "k_euler_ancestral",
            "scale": 5.0,
            "seed": 1,
            "width": 64,
            "height": 64,
            "params_version": 4,
            "v4_prompt": {
                "caption": {
                    "base_caption": "n::",
                    "char_captions": [
                        {
                            "char_caption": f"{V5_SLOT_TOKEN}, silver hair",
                            "centers": [{"x": 0.5, "y": 0.5}],
                        }
                    ],
                }
            },
            "characterPrompts": [
                {
                    "prompt": f"{V5_SLOT_TOKEN}, silver hair",
                    "uc": "extra fingers",
                    "center": {"x": 0.5, "y": 0.5},
                }
            ],
        }
    )


@pytest.fixture
def scanned_library(test_db, tmp_path: Path):
    folder = tmp_path / "lib"
    folder.mkdir()
    _write_png(
        folder / "nai_v5.png",
        {
            "Software": "NovelAI",
            "Source": "nai-diffusion-5-full",
            "Description": "n::",
            "Comment": _v5_comment(),
        },
    )
    _write_png(
        folder / "settings_only.png",
        {
            "parameters": (
                "\nClip skip: 1, Model hash: BD43B7CFFE, Model: anima-base-v1.0, "
                'Hashes: {"model":"BD43B7CFFE"}, Version: ComfyUI'
            )
        },
    )
    scan_folder(str(folder), recursive=False)
    return folder


def _row_by_name(filename: str) -> dict:
    matches = [row for row in db.get_images(include_unreadable=True) if row["filename"] == filename]
    assert matches, f"missing scanned row {filename}"
    return db.get_image_by_id(matches[0]["id"])


def test_scan_persists_v5_character_slots_for_gallery_detail(scanned_library):
    row = _row_by_name("nai_v5.png")
    parsed = json.loads(row["metadata_json"])["_parsed"]
    assert row["generator"] == "nai"
    assert parsed["version"] == PARSED_METADATA_VERSION
    assert parsed["character_prompts"][0]["prompt"] == f"{V5_SLOT_TOKEN}, silver hair"
    assert V5_SLOT_TOKEN in (parsed.get("character_prompt_text") or "")
    assert V5_SLOT_TOKEN not in (row.get("prompt") or "")


def test_gallery_search_and_prompt_filter_use_character_slots(scanned_library):
    searched = db.get_images(search_query=V5_SLOT_TOKEN)
    assert [row["filename"] for row in searched] == ["nai_v5.png"]

    exact = db.get_images(
        prompt_terms=[V5_SLOT_TOKEN],
        prompt_match_mode=PROMPT_MATCH_MODE_EXACT,
    )
    assert [row["filename"] for row in exact] == ["nai_v5.png"]

    contains = db.get_images(
        prompt_terms=["zxqv5char"],
        prompt_match_mode=PROMPT_MATCH_MODE_CONTAINS,
    )
    assert [row["filename"] for row in contains] == ["nai_v5.png"]

    ids = db.get_filtered_image_ids(
        prompt_terms=[V5_SLOT_TOKEN],
        prompt_match_mode=PROMPT_MATCH_MODE_EXACT,
    )
    row = _row_by_name("nai_v5.png")
    assert ids == [row["id"]]


def test_settings_only_comfyui_scan_is_filterable_without_invented_prompt(scanned_library):
    row = _row_by_name("settings_only.png")
    assert row["generator"] == "comfyui"
    assert row["checkpoint"] == "anima-base-v1.0"
    assert not (row.get("prompt") or "").strip()
    assert "Clip skip" not in (row.get("prompt") or "")

    matched = db.get_images(checkpoints=["anima-base-v1.0"])
    assert [item["filename"] for item in matched] == ["settings_only.png"]

    searched = db.get_images(search_query="anima-base-v1.0")
    assert [item["filename"] for item in searched] == ["settings_only.png"]
