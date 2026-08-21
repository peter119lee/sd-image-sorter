"""Public marketing copy must not claim a monopoly or erase real competitors."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_public_positioning_does_not_claim_to_be_the_only_ai_art_manager():
    for relative in ("README.md", "docs/WHY_CHOOSE_US.md"):
        text = _read(relative)
        assert "唯一为 AI 画师打造" not in text, f"{relative} still claims uniqueness"
        assert "The Only Image Manager Built for AI Artists" not in text, (
            f"{relative} still claims uniqueness"
        )


def test_comparison_table_does_not_mark_allusion_sd_metadata_as_absent():
    readme = _read("README.md")
    why = _read("docs", "WHY_CHOOSE_US.md")
    assert "| **SD 元数据** | 原生支持 ComfyUI/NAI/WebUI/Forge | ❌ |" not in readme
    assert "| **SD Metadata** | Native ComfyUI/NAI/WebUI/Forge | ❌ |" not in readme
    assert "| **SD Metadata** | Native ComfyUI/NAI/WebUI/Forge | ❌ |" not in why
    assert "PNG Parameters" in readme
    assert "PNG Parameters" in why


def test_public_docs_acknowledge_eagle_and_billfish():
    for relative in ("README.md", "docs/WHY_CHOOSE_US.md"):
        text = _read(relative)
        assert "Eagle" in text, f"{relative} pretends Eagle is not in the market"
        assert "Billfish" in text, f"{relative} pretends Billfish is not in the market"


def test_why_choose_us_does_not_say_allusion_has_no_ai_or_sd_awareness():
    why = _read("docs", "WHY_CHOOSE_US.md")
    assert "Treat SD images as generic files with arbitrary tags" not in why
    assert "No awareness of AI generation context or workflows" not in why
    assert "- No AI features\n" not in why
    assert "None of these are SD-specific" not in why
