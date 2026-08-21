"""Mode switches must keep a stable geometry when the selected option changes."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CSS_DIR = ROOT / "frontend" / "css"

# Selected-state rules for compact mode / segmented controls. Color and fill
# may change; font-weight and translateY must not, or the group reflows.
_STABLE_SELECTED = (
    ".sort-mode-btn.is-active",
    ".pub-variant-btn.active",
    ".export-preview-captype-btn.is-active",
    ".sorting-sub-tab.active",
    '.bracket-insp-btn[aria-pressed="true"]',
    ".hero-mode-btn.active",
    ".gallery-scope-btn.is-active",
    ".aspect-quick-btn.is-active",
    ".dataset-caption-type-btn.is-active",
    ".vlm-segmented-btn.active",
    ".mass-tag-tab.active",
    ".tagger-tab.active",
    ".censor-tab.is-active",
    ".sepcon-view-tab.is-active",
    ".segmented-option input:checked + span",
)

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]+)\}", re.S)
_GEOMETRIC = re.compile(
    r"(?:^|;)\s*(?:font-weight|transform|letter-spacing)\s*:",
    re.I,
)


def _all_css() -> str:
    parts = []
    for path in sorted(CSS_DIR.glob("*.css")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _rule_bodies(css: str, selector: str) -> list[str]:
    needle = re.sub(r"\s+", " ", selector).strip()
    bodies = []
    for raw_sel, body in _RULE_RE.findall(css):
        compact = re.sub(r"\s+", " ", raw_sel)
        for piece in compact.split(","):
            if needle in piece.strip():
                bodies.append(body)
    return bodies


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


def test_mode_switches_do_not_restyle_geometry_when_selected():
    css = _all_css()
    missing = []
    offenders = []
    for selector in _STABLE_SELECTED:
        bodies = _rule_bodies(css, selector)
        if not bodies:
            missing.append(selector)
            continue
        for body in bodies:
            if _GEOMETRIC.search(body):
                offenders.append(f"{selector}: {body.strip()}")
    assert not missing, f"missing selected-state rules: {missing}"
    assert not offenders, "selected-state geometry restyle:\n" + "\n".join(offenders)


def test_mode_switch_idle_weight_is_constant():
    css = _all_css()
    for selector, weight in (
        (".sort-mode-btn", "600"),
        (".pub-variant-btn", "600"),
        (".export-preview-captype-btn", "600"),
        (".bracket-insp-btn", "600"),
        (".hero-mode-switch", None),
        (".sort-mode-switch", None),
    ):
        bodies = _rule_bodies(css, selector)
        assert bodies, f"missing rule for {selector}"
        joined = "\n".join(bodies)
        if weight:
            assert f"font-weight: {weight}" in joined, selector
        if selector.endswith("-switch"):
            assert "flex-wrap: nowrap" in joined, selector
