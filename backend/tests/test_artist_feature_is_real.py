"""Style Finder is a real identification feature, not a labelled toy.

The owner rejected two covers that had been treated as acceptable:

* ``experimental: true`` on every identify response, with a comment that the
  identifier uses a hardcoded sample list instead of a trained model.
* ``_model = "placeholder"`` after a load failure, which made ``load()`` a
  no-op so a missing checkpoint could never be retried in the same process.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

import artist_identifier as ai
from routers.artists import IdentifyResponse


REPO_ROOT = Path(__file__).resolve().parents[2]


def _tiny_png(path: Path) -> str:
    Image.new("RGB", (8, 8), color="red").save(path)
    return str(path)


def test_sample_artist_list_file_is_gone():
    assert not (REPO_ROOT / "backend" / "artist" / "default_artists.py").exists()


def test_identify_payload_does_not_mark_the_feature_experimental():
    assert "experimental" not in IdentifyResponse.model_fields


def test_style_finder_chrome_does_not_call_the_feature_experimental():
    html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    artist_more = re.search(
        r'<button[^>]*id="nav-tools-artist"[^>]*>.*?</button>',
        html,
        flags=re.DOTALL,
    )
    assert artist_more is not None
    assert "nav.experimental" not in artist_more.group(0)

    catalog = (REPO_ROOT / "frontend" / "js" / "modules" / "entry-catalog.js").read_text(
        encoding="utf-8"
    )
    assert "goView('artist')" in catalog
    artist_entry = re.search(
        r"\{[^{}]*goView\('artist'\)[^{}]*\}",
        catalog,
        flags=re.DOTALL,
    )
    assert artist_entry is not None
    assert "experimental" not in artist_entry.group(0).lower()

    en = (REPO_ROOT / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8")
    zh = (REPO_ROOT / "frontend" / "js" / "lang" / "zh-CN.js").read_text(encoding="utf-8")
    en_catalog = re.search(r"'catalog\.artist':\s*'([^']*)'", en)
    zh_catalog = re.search(r"'catalog\.artist':\s*'([^']*)'", zh)
    assert en_catalog is not None and zh_catalog is not None
    assert "experimental" not in en_catalog.group(1).lower()
    assert "实验" not in zh_catalog.group(1)


def test_huggingface_load_failure_does_not_lock_placeholder(monkeypatch):
    ident = ai.ArtistIdentifier(model_source="huggingface")
    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise RuntimeError("checkpoint missing")

    monkeypatch.setattr(ai, "prepare_artist_assets", boom)

    ident.load()
    assert ident._model is None
    assert ident._model != "placeholder"
    assert "checkpoint missing" in str(ident._load_error or "")

    ident.load()
    assert calls["n"] == 2, "a failed load must be retryable, not frozen as placeholder"


def test_identify_without_a_loaded_model_does_not_invent_artist_names(tmp_path, monkeypatch):
    image_path = _tiny_png(tmp_path / "probe.png")
    ident = ai.ArtistIdentifier()
    monkeypatch.setattr(ident, "load", lambda: None)
    ident._model = None
    ident._load_error = "Kaloscope is not installed"

    result = ident.identify(image_path)

    assert result["artist"] == "undefined"
    assert result["model_loaded"] is False
    assert result["top_predictions"] == []
    assert "error" in result
    assert "makoto_shinkai" not in str(result).lower()
    assert "wlop" not in str(result).lower()
