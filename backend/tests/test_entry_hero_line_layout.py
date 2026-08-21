"""Entry cover-mode controls must not jump when caption copy changes."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_hero_line_uses_stable_three_column_slots():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "css" / "entry.css").read_text(encoding="utf-8")

    assert 'class="hero-credit-slot"' in html
    assert 'id="entry-hero-mode-switch"' in html
    assert 'class="hero-local-note"' in html
    credit_at = html.find('class="hero-credit-slot"')
    switch_at = html.find('id="entry-hero-mode-switch"')
    note_at = html.find('class="hero-local-note"')
    assert credit_at < switch_at < note_at

    assert '"credit credit"' in css
    assert '"switch note"' in css
    assert "grid-area: switch" in css
    assert "#entry-hero-credit" in css
    assert "text-overflow: ellipsis" in css
