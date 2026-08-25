"""Current product docs and chrome must not advertise a retired or fake state.

These pins exist because stale comments (glassmorphism, experimental Style Finder,
keep-db_repos) were treated as the live product and misled both users and agents.
"""

from __future__ import annotations

from pathlib import Path

import artist_identifier as ai
from routers.artists import IdentifyResponse
from tagger_models import TAGGER_MODELS


ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_current_product_docs_do_not_claim_glassmorphism():
    for relative in ("AGENTS.md", "CLAUDE.md", "README.md"):
        text = _read(relative).lower()
        assert "glassmorphism" not in text, f"{relative} still claims glassmorphism"
    architecture = _read("docs", "architecture.md")
    assert "not glassmorphism" in architecture.lower()
    assert "Experimental artist identification" not in architecture


def test_identify_api_does_not_mark_style_finder_experimental():
    assert "experimental" not in IdentifyResponse.model_fields
    assert not (ROOT / "backend" / "artist" / "default_artists.py").exists()
    assert "DEFAULT_ARTISTS" not in (ROOT / "backend" / "artist_identifier.py").read_text(
        encoding="utf-8"
    )


def test_debt_notes_do_not_tell_agents_to_keep_deleted_db_repos():
    notes = _read("docs", "TECHNICAL_DEBT_NOTES.md")
    assert "deletion of `backend/db_repos/` was rejected" not in notes
    assert "Keep `backend/db_repos/`" not in notes
    assert not (ROOT / "backend" / "db_repos").exists()


def test_manual_sort_does_not_advertise_modes_that_do_not_exist():
    html = _read("frontend", "index.html")
    assert "sort-mode-soon" not in html
    assert "manual.modeMoreSoon" not in html
    en = _read("frontend", "js", "lang", "en.js")
    zh = _read("frontend", "js", "lang", "zh-CN.js")
    assert "manual.modeMoreSoon" not in en
    assert "manual.modeMoreSoon" not in zh


def test_marketing_tagger_count_matches_the_catalog():
    tagger_count = sum(
        1 for cfg in TAGGER_MODELS.values() if not cfg.get("captioner_only")
    )
    readme = _read("README.md")
    why = _read("docs", "WHY_CHOOSE_US.md")
    assert "7 models" not in readme
    assert "7 个模型" not in readme
    assert "7 models" not in why
    assert "10 local tagger" not in readme
    assert "10 个本地打标" not in readme
    assert "10 local tagger" not in why
    assert f"{tagger_count} local tagger" in why
    assert f"{tagger_count} 个本地打标" in readme
    assert f"{tagger_count} local tagger" in readme
    assert "ToriiGate captioner" in why
    assert "ToriiGate 描述器" in readme


def test_artist_load_failure_does_not_lock_a_placeholder_model():
    ident = ai.ArtistIdentifier()
    ident._mark_load_failed("no weights")
    assert ident._model is None


def test_marketing_copy_matches_shipped_template_and_vlm_facts():
    readme = _read("README.md")
    why = _read("docs", "WHY_CHOOSE_US.md")
    agents = _read("AGENTS.md")
    claude = _read("CLAUDE.md")
    architecture = _read("docs", "architecture.md")

    assert "实验性画师" not in readme
    assert "14 个模板变量" not in readme
    assert "14 variables" not in readme
    assert "14 variables" not in why
    assert "17 个模板变量" in readme
    assert "17 variables" in readme
    assert "17 variables" in why
    assert "5 providers" not in readme
    assert "5 providers" not in why
    assert "Portable single-file" not in readme
    assert "Portable single-file" not in why
    assert "单文件便携" not in readme
    assert "| **Prompt Lab** |" not in readme
    assert "| **Prompt Lab** |" not in why
    assert "Models are loaded lazily on first use:" not in architecture
    assert "~65 MB" not in _read("backend", "config.py")
    assert "~65 MB" not in _read("backend", "similarity.py")
    assert "~65 MB" not in _read("docs", "API.md")
    assert ".tab-badge-experimental" not in _read("frontend", "css", "styles.css")
    assert "100% local, zero cloud upload" not in why
    assert "docs/screenshots/gallery_hero.png" not in readme
    assert "facebookresearch/sam2" not in readme
    assert "heathcliff01" not in readme.lower()

    assert "SQLite database defaults to `data/images.db`" in agents
    assert "SQLite database defaults to `data/images.db`" in claude
    assert "Gallery pagination is cursor-based" in agents
    assert "Gallery pagination is cursor-based" in claude
    assert "localhost_only_middleware` in `backend/app_security.py`" in agents
    assert "connection pooling" not in architecture.lower()
    assert "tags (id, image_id, tag, confidence, source, category)" in architecture

    security = _read("backend", "app_security.py")
    assert 'allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]' in security
    assert "entry.multiLibrarySoon" not in _read("frontend", "js", "lang", "en.js")
    assert "entry.multiLibrarySoon" not in _read("frontend", "js", "lang", "zh-CN.js")
    assert "images never leave this PC" not in _read("frontend", "js", "lang", "en.js")
    assert "图不出这台电脑" not in _read("frontend", "js", "lang", "zh-CN.js")
    assert "The 4 tabs in the modal" not in _read("frontend", "js", "lang", "en.js")
    assert "4 个 tab" not in _read("frontend", "js", "lang", "zh-CN.js")
    assert "named as somebody else" not in _read("frontend", "index.html")
    shortcuts = _read("frontend", "js", "modules", "components", "keyboard-shortcuts.js")
    assert "1-7" not in shortcuts
    assert "Switch tabs quickly" not in shortcuts


def test_first_use_downloads_the_clicked_feature_with_progress():
    html = _read("frontend", "index.html")
    api_at = html.find("js/app/api-features.js")
    restart_at = html.find("js/app/model-restart.js")
    ensure_at = html.find("js/app/ensure-model.js")
    tagging_at = html.find("js/app/tagging-flow.js")
    assert api_at != -1 and restart_at != -1 and ensure_at != -1 and tagging_at != -1
    assert api_at < restart_at < ensure_at < tagging_at

    helper = _read("frontend", "js", "app", "ensure-model.js")
    assert "FEATURE_INSTALL_CONFIRM_BYTES" in helper
    assert "1024 * 1024 * 1024" in helper
    assert "function ensureFeatureModel" in helper
    assert "/api/models/download-progress" in helper
    assert "prepareSpecForTagger" in helper

    assert "ensureFeatureModel" in _read("frontend", "js", "app", "tagging-flow.js")
    assert "ensureFeatureModel" in _read("frontend", "js", "similar", "embedding.js")
    assert "ensureFeatureModel" in _read("frontend", "js", "app", "stats-aesthetic.js")
    assert "ensureFeatureModel" in _read("frontend", "js", "artist", "identify.js")
    assert "ensureFeatureModel" in _read("frontend", "js", "smart-tag", "run.js")
    detect = _read("frontend", "js", "censor", "detect.js")
    assert "ensureFeatureModel" in detect
    assert "censor-nudenet" in detect
    assert "It may look stuck" not in _read("frontend", "js", "lang", "en.js")
    assert "censor.nudenetFirstUseDownload" in _read("frontend", "js", "lang", "zh-CN.js")
    assert "第一次使用 NudeNet 会下载模型" in _read("frontend", "js", "lang", "zh-CN.js")
    assert "btnEmbed.disabled = !modelReady" not in _read("frontend", "js", "similar", "status.js")
    assert "Model-health owns the CTA" not in _read("frontend", "js", "similar", "status.js")
    assert "!isAvailable" not in _read("frontend", "js", "artist", "diagnostics.js")
    assert "startButton.disabled = true" not in _read("frontend", "js", "app", "stats-aesthetic.js")

    architecture = _read("docs", "architecture.md")
    assert "progress overlay" in architecture.lower()
    assert "1 GB" in architecture

    identifier = _read("backend", "artist_identifier.py")
    assert "or identifier._model == \"placeholder\"" not in identifier
    assert "self._model == \"placeholder\"" in identifier
