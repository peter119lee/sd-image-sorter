"""
Frontend contract tests that guard shared state boundaries.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


FORBIDDEN_APPSTATE_ASSIGN_RE = re.compile(
    r"(?:window\.)?(?:App\.)?AppState\.[A-Za-z_][A-Za-z0-9_]*\s*=",
    re.MULTILINE,
)
FORBIDDEN_WINDOW_APP_ASSIGN_RE = re.compile(
    r"window\.App\.[A-Za-z_$][A-Za-z0-9_$]*\s*=(?!=)",
    re.MULTILINE,
)

ALLOWED_DIRECT_WRITER_FILES = {"app.js", "gallery.js"}
SKIPPED_DIRS = {"__pycache__", "node_modules"}
LANGUAGE_KEY_RE = re.compile(
    r'''^\s*(?P<quote>['"])(?P<key>[^'"]+)(?P=quote)\s*:''',
    re.MULTILINE,
)


def _iter_frontend_js_files(frontend_root: Path):
    for root, dirs, files in os.walk(frontend_root):
        dirs[:] = [name for name in dirs if name not in SKIPPED_DIRS]
        root_path = Path(root)
        for filename in files:
            if filename.endswith(".js"):
                yield root_path / filename


def _language_pack_keys(source: str) -> list[str]:
    return [match.group("key") for match in LANGUAGE_KEY_RE.finditer(source)]


def _javascript_object_body(source: str, variable_name: str) -> str:
    match = re.search(
        rf"\bvar {re.escape(variable_name)} = \{{(?P<body>.*?)^\s*\}};",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"JavaScript object {variable_name!r} is missing"
    return match.group("body")


def test_language_packs_have_unique_matching_keys():
    repo_root = Path(__file__).resolve().parents[2]
    language_sources = {
        "en": (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8"),
        "zh-CN": (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(encoding="utf-8"),
    }
    keys_by_language = {}
    duplicates_by_language = {}

    for language, source in language_sources.items():
        keys = _language_pack_keys(source)
        counts = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        keys_by_language[language] = set(keys)
        if duplicates:
            duplicates_by_language[language] = duplicates

    assert not duplicates_by_language
    assert keys_by_language["en"] == keys_by_language["zh-CN"]


def test_guide_translation_supplements_do_not_override_main_language_packs():
    repo_root = Path(__file__).resolve().parents[2]
    main_sources = {
        "en": (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8"),
        "zh-CN": (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(encoding="utf-8"),
    }
    guide_source = (repo_root / "frontend" / "js" / "guide-translations.js").read_text(encoding="utf-8")
    guide_bodies = {
        "en": _javascript_object_body(guide_source, "en"),
        "zh-CN": _javascript_object_body(guide_source, "zhCN"),
    }
    guide_keys = {
        language: set(_language_pack_keys(source))
        for language, source in guide_bodies.items()
    }
    overlaps = {
        language: sorted(set(_language_pack_keys(main_sources[language])) & keys)
        for language, keys in guide_keys.items()
    }

    assert guide_keys["en"] == guide_keys["zh-CN"]
    assert not any(overlaps.values()), overlaps


def _dataset_family_source(repo_root: Path) -> str:
    # The dataset-maker JS god-file family (dataset-maker.js + part2/part3/
    # pipeline/local-import/cleanups) was decomposed VERBATIM into the
    # frontend/js/dataset/ module family; contract pins now assert against the
    # family concatenation so each literal is found in whichever new module
    # hosts it (same adaptation style as the censor / smart_tag splits).
    dataset_dir = repo_root / "frontend" / "js" / "dataset"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(dataset_dir.glob("*.js"))
    )
    assert source, "frontend/js/dataset/*.js family is missing"
    return source


def _app_family_source(repo_root: Path) -> str:
    # The app.js god-file is being decomposed VERBATIM into the
    # frontend/js/app/ module family (staged split; same adaptation style as
    # the censor / dataset-maker splits) while a slim, still-servable
    # frontend/js/app.js stays behind (test_cache_bust GETs /static/js/app.js).
    # Contract pins assert against the family concatenation so each literal is
    # found in whichever file hosts it. Until the split lands the family is
    # exactly app.js, so every assertion is byte-for-byte unchanged.
    app_js = repo_root / "frontend" / "js" / "app.js"
    assert app_js.is_file(), "frontend/js/app.js must remain a real file"
    app_source = app_js.read_text(encoding="utf-8")
    assert app_source, "frontend/js/app.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "app"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([app_source] + family_parts)


def _is_app_family_file(file_path: Path, frontend_root: Path) -> bool:
    # frontend/js/app/*.js modules are split out of app.js verbatim, so they
    # inherit app.js's shared-state writer allowances in the walker tests.
    return file_path.relative_to(frontend_root).as_posix().startswith("app/")


def _gallery_family_source(repo_root: Path) -> str:
    # The gallery.js god-object (`const Gallery = {...}`) is decomposed VERBATIM
    # into the frontend/js/gallery/ module family (Object.assign mixins over the
    # shared base object; same adaptation style as the censor / dataset-maker /
    # app.js splits) while a slim, still-servable frontend/js/gallery.js stays
    # behind (the index.html ordering assertion below and the release QA gate
    # both reference /static/js/gallery.js). Contract pins assert against the
    # family concatenation so each literal is found in whichever file hosts it.
    # Until the split lands the family is exactly gallery.js, so every
    # assertion is byte-for-byte unchanged.
    gallery_js = repo_root / "frontend" / "js" / "gallery.js"
    assert gallery_js.is_file(), "frontend/js/gallery.js must remain a real file"
    gallery_source = gallery_js.read_text(encoding="utf-8")
    assert gallery_source, "frontend/js/gallery.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "gallery"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([gallery_source] + family_parts)


def _is_gallery_family_file(file_path: Path, frontend_root: Path) -> bool:
    # frontend/js/gallery/*.js modules are split out of gallery.js verbatim, so
    # they inherit gallery.js's AppState walker allowance. gallery.js has no
    # real AppState writes -- its `AppState.viewMode ===` comparisons merely
    # false-positive FORBIDDEN_APPSTATE_ASSIGN_RE on the first `=` of `===`.
    return file_path.relative_to(frontend_root).as_posix().startswith("gallery/")


def _manual_sort_family_source(repo_root: Path) -> str:
    # The manual-sort.js god-file is being decomposed VERBATIM into the
    # frontend/js/manual-sort/ module family (static script tags in index.html,
    # base first; same adaptation style as the censor / dataset-maker / app.js /
    # gallery.js splits) while a slim, still-servable frontend/js/manual-sort.js
    # stays behind (index.html's script tag and the release packages reference
    # it). Contract pins assert against the family concatenation so each
    # literal is found in whichever file hosts it. Until the split lands the
    # family is exactly manual-sort.js, so every assertion is byte-for-byte
    # unchanged.
    manual_sort_js = repo_root / "frontend" / "js" / "manual-sort.js"
    assert manual_sort_js.is_file(), (
        "frontend/js/manual-sort.js must remain a real file"
    )
    manual_sort_source = manual_sort_js.read_text(encoding="utf-8")
    assert manual_sort_source, "frontend/js/manual-sort.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "manual-sort"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([manual_sort_source] + family_parts)


def _autosep_family_source(repo_root: Path) -> str:
    # The autosep.js god-file is being decomposed VERBATIM into the
    # frontend/js/autosep/ module family (static script tags in index.html,
    # state-constants base first; same adaptation style as the censor /
    # dataset-maker / app.js / gallery.js / manual-sort splits) while a slim,
    # still-servable frontend/js/autosep.js stays behind (index.html's script
    # tag and the release packages reference it). Contract pins assert against
    # the family concatenation so each literal is found in whichever file
    # hosts it. Until the split lands the family is exactly autosep.js, so
    # every assertion is byte-for-byte unchanged.
    autosep_js = repo_root / "frontend" / "js" / "autosep.js"
    assert autosep_js.is_file(), "frontend/js/autosep.js must remain a real file"
    autosep_source = autosep_js.read_text(encoding="utf-8")
    assert autosep_source, "frontend/js/autosep.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "autosep"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([autosep_source] + family_parts)


def _smart_tag_family_source(repo_root: Path) -> str:
    # The smart-tag.js god-file (single strict IIFE, one window.SmartTag
    # publish) is decomposed VERBATIM into the frontend/js/smart-tag/ module
    # family (static script tags in index.html, one shared classic-script
    # global lexical environment; state.js first, boot.js last — same
    # adaptation style as the censor / autosep / manual-sort splits) while a
    # slim, still-servable frontend/js/smart-tag.js stays behind (index.html's
    # script tag and the release packages reference it). Contract pins assert
    # against the family concatenation so each literal is found in whichever
    # file hosts it (form.js: vlm_grounding/toriigate_grounding and the
    # image_paths/selection_token/dataset_scan_token payload lines; run.js:
    # /api/smart-tag/results; taggers.js: model?.default_threshold).
    smart_tag_js = repo_root / "frontend" / "js" / "smart-tag.js"
    assert smart_tag_js.is_file(), "frontend/js/smart-tag.js must remain a real file"
    smart_tag_source = smart_tag_js.read_text(encoding="utf-8")
    assert smart_tag_source, "frontend/js/smart-tag.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "smart-tag"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([smart_tag_source] + family_parts)


def _v321_family_source(repo_root: Path) -> str:
    # The v321-ui.js god-object (`const V321Integration = {...}`) is decomposed
    # VERBATIM into the frontend/js/v321/ module family (Object.assign mixins
    # over the shared base object declared in v321/base.js; same adaptation
    # style as the censor / dataset-maker / app.js / gallery.js / manual-sort
    # splits) while a slim, still-servable frontend/js/v321-ui.js stays behind
    # (index.html's script tag and the release packages reference it).
    # Contract pins assert against the family concatenation so each literal is
    # found in whichever file hosts it. Until the split lands the family is
    # exactly v321-ui.js, so every assertion is byte-for-byte unchanged.
    v321_js = repo_root / "frontend" / "js" / "v321-ui.js"
    assert v321_js.is_file(), "frontend/js/v321-ui.js must remain a real file"
    v321_source = v321_js.read_text(encoding="utf-8")
    assert v321_source, "frontend/js/v321-ui.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "v321"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([v321_source] + family_parts)


def _prompt_lab_family_source(repo_root: Path) -> str:
    # The prompt-lab.js god-object (`const PromptLab = {...}`) is decomposed
    # VERBATIM into the frontend/js/prompt-lab/ module family (Object.assign
    # mixins over the shared base object declared in prompt-lab/base.js; same
    # adaptation style as the censor / dataset-maker / app.js / gallery.js /
    # manual-sort / v321 splits) while a slim, still-servable
    # frontend/js/prompt-lab.js stays behind (index.html's script tag and the
    # release packages reference it). Contract pins assert against the family
    # concatenation so each literal is found in whichever file hosts it. Until
    # the split lands the family is exactly prompt-lab.js, so every assertion
    # is byte-for-byte unchanged.
    prompt_lab_js = repo_root / "frontend" / "js" / "prompt-lab.js"
    assert prompt_lab_js.is_file(), (
        "frontend/js/prompt-lab.js must remain a real file"
    )
    prompt_lab_source = prompt_lab_js.read_text(encoding="utf-8")
    assert prompt_lab_source, "frontend/js/prompt-lab.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "prompt-lab"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([prompt_lab_source] + family_parts)


def _reader_family_source(repo_root: Path) -> str:
    # The image-reader.js god-object (`const ImageReader = {...}`) is decomposed
    # VERBATIM into the frontend/js/image-reader/ module family (Object.assign
    # mixins over the shared base object declared in image-reader/core.js; same
    # adaptation style as the censor / dataset-maker / app.js / gallery.js /
    # manual-sort / v321 / prompt-lab splits) while a slim, still-servable
    # frontend/js/image-reader.js stays behind (index.html's script tag and the
    # release packages reference it). Contract pins assert against the family
    # concatenation so each literal is found in whichever file hosts it.
    reader_js = repo_root / "frontend" / "js" / "image-reader.js"
    assert reader_js.is_file(), "frontend/js/image-reader.js must remain a real file"
    reader_source = reader_js.read_text(encoding="utf-8")
    assert reader_source, "frontend/js/image-reader.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "image-reader"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([reader_source] + family_parts)


def _vlm_caption_family_source(repo_root: Path) -> str:
    # The vlm-caption.js god-object (`const VLMCaption = {...}`) is decomposed
    # VERBATIM into the frontend/js/vlm-caption/ module family (Object.assign
    # mixins over the shared base object declared in vlm-caption/core.js; same
    # adaptation style as the censor / dataset-maker / app.js / gallery.js /
    # manual-sort / v321 / prompt-lab / image-reader splits) while a slim,
    # still-servable frontend/js/vlm-caption.js stays behind (index.html's
    # script tag and the release packages reference it). Contract pins assert
    # against the family concatenation so each literal is found in whichever
    # file hosts it.
    vlm_js = repo_root / "frontend" / "js" / "vlm-caption.js"
    assert vlm_js.is_file(), "frontend/js/vlm-caption.js must remain a real file"
    vlm_source = vlm_js.read_text(encoding="utf-8")
    assert vlm_source, "frontend/js/vlm-caption.js must not be empty"
    family_dir = repo_root / "frontend" / "js" / "vlm-caption"
    family_parts = [
        path.read_text(encoding="utf-8") for path in sorted(family_dir.glob("*.js"))
    ]
    return "\n".join([vlm_source] + family_parts)


def test_frontend_feature_modules_do_not_directly_assign_appstate():
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend" / "js"
    violations: list[str] = []

    for file_path in sorted(_iter_frontend_js_files(frontend_root)):
        # App/gallery-family skip: app/*.js and gallery/*.js are split out of
        # app.js / gallery.js and keep those files' AppState direct-writer
        # allowances (censor/dataset-split precedent).
        if (
            file_path.name in ALLOWED_DIRECT_WRITER_FILES
            or _is_app_family_file(file_path, frontend_root)
            or _is_gallery_family_file(file_path, frontend_root)
        ):
            continue
        source = file_path.read_text(encoding="utf-8")
        for match in FORBIDDEN_APPSTATE_ASSIGN_RE.finditer(source):
            line_number = source.count("\n", 0, match.start()) + 1
            relative_path = file_path.relative_to(repo_root).as_posix()
            violations.append(f"{relative_path}:{line_number}: {match.group(0)}")

    assert not violations, (
        "Feature modules must not directly assign `AppState.*`.\n"
        "Use narrow app APIs (for example `markGalleryNeedsRefresh`) instead.\n"
        + "\n".join(violations)
    )


def test_frontend_feature_modules_do_not_mutate_window_app_namespace():
    repo_root = Path(__file__).resolve().parents[2]
    frontend_root = repo_root / "frontend" / "js"
    violations: list[str] = []

    for file_path in sorted(_iter_frontend_js_files(frontend_root)):
        # App-family skip: app/*.js is split out of app.js and keeps its
        # window.App writer allowance (censor/dataset-split precedent).
        if file_path.name == "app.js" or _is_app_family_file(file_path, frontend_root):
            continue
        source = file_path.read_text(encoding="utf-8")
        for match in FORBIDDEN_WINDOW_APP_ASSIGN_RE.finditer(source):
            line_number = source.count("\n", 0, match.start()) + 1
            relative_path = file_path.relative_to(repo_root).as_posix()
            violations.append(f"{relative_path}:{line_number}: {match.group(0)}")

    assert not violations, (
        "Feature modules must not mutate `window.App.*`; use named module bridges or narrow app APIs.\n"
        + "\n".join(violations)
    )


def test_window_app_context_is_sealed_after_creation():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "window.App = buildAppContext();" in source
    assert "Object.seal(window.App);" in source


def test_load_images_options_are_passed_as_second_argument():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "loadImages({" not in source
    assert "loadImages(false, {" in source


def test_cancelled_gallery_load_marks_refresh_intent():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "function cancelGalleryImageLoad()" in source
    assert "hadPendingGalleryLoad" in source
    assert "AppState.galleryNeedsRefresh = true;" in source


def test_selection_store_clears_filtered_token_for_non_filtered_scopes():
    if shutil.which("node") is None:
        return

    repo_root = Path(__file__).resolve().parents[2]
    script = f"""
const fs = require('fs');
global.window = {{}};
const source = fs.readFileSync({str(repo_root / "frontend" / "js" / "stores" / "selection-store.js")!r}, 'utf8');
eval(source);
const visibleState = window.SelectionStore.cloneState({{
  selectionMode: true,
  selectedIds: [1, 2],
  scope: 'visible',
  filterKey: 'stale-filter',
  selectionToken: 'stale-token',
}});
if (visibleState.filterKey !== null || visibleState.selectionToken !== null) {{
  throw new Error('visible selection retained filtered token state');
}}
const loadedState = window.SelectionStore.cloneState({{
  selectionMode: true,
  selectedIds: [1, 2],
  scope: 'loaded',
  filterKey: 'stale-filter',
  selectionToken: 'stale-token',
}});
if (loadedState.filterKey !== null || loadedState.selectionToken !== null) {{
  throw new Error('loaded selection retained filtered token state');
}}
const filteredState = window.SelectionStore.cloneState({{
  selectionMode: true,
  selectedIds: [1, 2],
  scope: 'filtered',
  filterKey: 'active-filter',
  selectionToken: 'active-token',
}});
if (filteredState.filterKey !== 'active-filter' || filteredState.selectionToken !== 'active-token') {{
  throw new Error('filtered selection lost active token state');
}}
"""
    subprocess.run(["node", "-e", script], check=True)


def test_v321_modules_read_runtime_selection_store_from_window_app():
    repo_root = Path(__file__).resolve().parents[2]
    # v321-family read: v321-ui.js is split into v321/*.js (gallery precedent).
    v321_source = _v321_family_source(repo_root)
    # vlm-family read: vlm-caption.js is split into vlm-caption/*.js
    # (gallery precedent).
    vlm_source = _vlm_caption_family_source(repo_root)

    combined_source = v321_source + "\n" + vlm_source
    assert "window.SelectionStore?.getSelectedIds" not in combined_source
    assert "window.SelectionStore.getSelectedIds" not in combined_source
    assert "window.App?.SelectionStore?.getState?.()" in combined_source
    assert "/api/tags/export-combined" in v321_source
    assert "_buildCombinedExportPayload" in v321_source


def test_dataset_maker_large_queues_use_virtualized_rendering():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)

    assert "DATASET_VIRTUAL_THRESHOLD" in source
    assert "_renderVirtualQueue" in source
    assert "_renderVirtualImportGallery" in source
    assert "dataset-virtual-spacer" in source


def test_dataset_folder_import_has_paged_large_folder_controls():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    source = _dataset_family_source(repo_root)
    part2_source = source
    pipeline_source = source
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    app_source = _app_family_source(repo_root)
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert 'id="btn-dataset-folder-import-more"' in html
    assert "_folderScanToken" in source
    assert "const FOLDER_SCAN_PAGE_SIZE = 5000;" in source
    assert "include_thumbnails: false" in source
    assert "/api/dataset/local-thumbnail" in source
    assert "encodeURIComponent(path)" in source
    assert "LARGE_BROWSER_DROP_WARNING_FILES" in source
    assert ".slice(0, LARGE_BROWSER_DROP_WARNING_FILES)" not in source
    assert "scan_token" in source
    assert "dataset_scan_tokens" in source
    assert "manifest_items" not in source
    assert "folderImportAddedManifest" in source
    assert "_markLocalManifestExcluded?.(id)" in part2_source
    assert "exportPreviewManifestNote" in pipeline_source
    assert "confirmSummaryManifestPreview" not in zh_source
    assert "dataset.importGalleryManifestCount" in zh_source
    assert "Object.entries(params)" in app_source


def test_dataset_folder_import_append_keeps_current_tab_and_shows_busy_state():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)
    css = (repo_root / "frontend" / "css" / "dataset-pipeline.css").read_text(
        encoding="utf-8"
    )

    assert "const focusImportTab = options.focusImportTab === true;" in source
    assert "focusImportTab: !append" in source
    assert "this._setFolderImportBusy(true);" in source
    assert "this._setFolderImportBusy(false);" in source
    assert ".dataset-folder-import-status-row.is-loading::before" in css
    assert "@keyframes dataset-spin" in css


def test_dataset_audit_results_have_next_step_actions():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)
    css = (repo_root / "frontend" / "css" / "dataset-pipeline.css").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "dataset-audit-next-steps" in source
    assert "selectAuditMatches" in source
    assert "removeAuditMatches" in source
    assert "dataset.auditBadgeMissing" in source
    assert "dataset.auditNextTruncated" in source
    assert "item_limit: 50000" in source
    assert "dataset.auditActionWorkbench" in zh_source
    assert ".dataset-audit-next-steps" in css
    assert ".dataset-audit-next-warning" in css


def test_dataset_browser_uploads_reuse_folder_import_busy_spinner():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )

    assert "dataset.uploadImporting" in source
    assert "DM._setFolderImportBusy?.(true);" in source
    assert "DM._setFolderImportBusy?.(false);" in source
    assert "'dataset.uploadImporting'" in en_source


def test_gallery_order_badge_moves_away_from_selection_circle():
    repo_root = Path(__file__).resolve().parents[2]
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )

    assert ".gallery-grid.selection-mode .gallery-item-order" in css
    assert "left: 42px;" in css
    assert ".gallery-grid.selection-mode .gallery-item::before" in css
    assert "z-index: 6;" in css


def test_dataset_local_ids_use_safe_52_bit_hash_slice():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)

    assert ".slice(0, 13)" in source
    assert "Number.MAX_SAFE_INTEGER" in source


def test_full_selection_workflows_do_not_fallback_to_gallery_dom():
    repo_root = Path(__file__).resolve().parents[2]
    checked = {
        # v321-family read: v321-ui.js is split into v321/*.js (gallery
        # precedent); no v321 family file may scrape gallery DOM either.
        "frontend/js/v321-ui.js family": _v321_family_source(repo_root),
        # vlm-family read: vlm-caption.js is split into vlm-caption/*.js
        # (gallery precedent); no vlm family file may scrape gallery DOM either.
        "frontend/js/vlm-caption.js family": _vlm_caption_family_source(repo_root),
        "frontend/js/mass-tag-editor.js": (
            repo_root / "frontend" / "js" / "mass-tag-editor.js"
        ).read_text(encoding="utf-8"),
    }

    violations = [
        path
        for path, source in checked.items()
        if ".gallery-item[data-id]" in source or "gallery-item[data-id]" in source
    ]

    assert not violations, (
        "Full-selection workflows must use SelectionStore/selection-token resolvers, "
        "not currently rendered gallery DOM nodes: " + ", ".join(violations)
    )


def test_selection_filter_payload_preserves_full_gallery_scope():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    # NOTE(split): first-match regexes; each builder fn must stay unique family-wide.
    source = _app_family_source(repo_root)

    selection_match = re.search(
        r"function buildSelectionFilterRequest\(.*?\) \{\n(?P<body>.*?)\n\}",
        source,
        re.DOTALL,
    )
    assert selection_match is not None
    selection_body = selection_match.group("body")

    advanced_match = re.search(
        r"function buildAdvancedFilterContract\(.*?\) \{\n(?P<body>.*?)\n\}",
        source,
        re.DOTALL,
    )
    assert advanced_match is not None
    advanced_body = advanced_match.group("body")

    for field in (
        "minUserRating",
        "excludePrompts",
        "excludeColors",
        "collectionId",
        "folder",
        "hasMetadata",
    ):
        assert f"{field}:" in selection_body
        assert f"{field}:" in advanced_body


def test_batch_caption_export_requires_selection_not_loaded_gallery_fallback():
    repo_root = Path(__file__).resolve().parents[2]
    # v321-family read: v321-ui.js is split into v321/*.js (gallery precedent).
    # _resolveSelectionImageIds is runtime-dead but its body IS this pinned
    # no-loaded-gallery-fallback invariant (lead sign-off: KEEP verbatim); the
    # assertions below follow it into whichever family file hosts it
    # (v321/preview-core.js after the split).
    source = _v321_family_source(repo_root)

    assert "allowLoadedFallback = false" in source
    assert "const source = await this._loadQueueSource();" in source
    assert (
        "return allowLoadedFallback ? this._getLoadedGalleryImageIds(normalizedCap) : [];"
        in source
    )
    assert "return this._getLoadedGalleryImageIds(1000000);" not in source


def test_mass_tag_entry_is_visible_on_desktop_and_mobile():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )
    source = (repo_root / "frontend" / "js" / "mass-tag-editor.js").read_text(
        encoding="utf-8"
    )

    assert 'id="mobile-btn-mass-tag-editor"' in html
    assert 'document.getElementById("mobile-btn-mass-tag-editor")' in source
    assert ".nav-actions #btn-mass-tag-editor" not in css


def test_mass_tag_does_not_expand_selection_tokens_in_browser():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend" / "js" / "mass-tag-editor.js").read_text(
        encoding="utf-8"
    )

    assert "resolveScopePayload" in source
    assert "selection_token" in source
    assert "/api/images/selection-chunk" not in source
    assert "getSelectionChunk" not in source
    assert "resolveSelectedImageIds" not in source


def test_native_checkbox_radio_are_not_forced_to_button_size():
    repo_root = Path(__file__).resolve().parents[2]
    css = (repo_root / "frontend" / "css" / "styles.css").read_text(encoding="utf-8")

    start = css.index("/* Ensure interactive elements have proper touch targets */")
    end = css.index("/* View buttons in gallery */", start)
    body = css[start:end]
    touch_rule = body.split("}", 1)[0]
    assert 'input[type="checkbox"],' not in touch_rule
    assert 'input[type="radio"],' not in touch_rule
    assert "accent-color: var(--accent-primary);" in css


def test_app_filter_access_exposes_selection_token_resolver():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "resolveSelectedImageIds" in source
    assert "getActiveSelectionToken" in source
    assert "getSelectionChunk(token" in source


def test_censor_filtered_selection_uses_token_backed_queue_window():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    # NOTE(split): first-match regex; keep the whole #btn-send-to-censor handler in
    # ONE family file so the non-greedy match cannot bridge file boundaries.
    app_source = _app_family_source(repo_root)
    # censor-edit.js was decomposed VERBATIM into the frontend/js/censor/
    # module family (god-file redesign). Concatenate the whole family so this
    # contract keeps pinning the token-backed queue seams no matter which part
    # hosts them (same adaptation style as the smart_tag_service split).
    censor_dir = repo_root / "frontend" / "js" / "censor"
    censor_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(censor_dir.glob("*.js"))
    )
    assert censor_source, "frontend/js/censor/*.js family is missing"

    send_block = re.search(
        r"\$\('#btn-send-to-censor'\).*?addEventListener\('click', async \(e\) => \{(?P<body>.*?)\n    \}\);",
        app_source,
        re.DOTALL,
    )
    assert send_block is not None
    body = send_block.group("body")

    assert "selectionToken: token" in body
    assert "visibleImageIds:" in body
    assert "API.getSelectionChunk" not in body
    assert "while (!done)" not in body

    assert "CENSOR_TOKEN_QUEUE_WINDOW_SIZE" in censor_source
    assert "tokenQueueSource" in censor_source
    assert "loadSelectionDataByToken" in censor_source
    assert "processCensorBatchItems" in censor_source
    assert "selection_token" not in body


def test_vlm_caption_uses_selection_token_without_resolving_full_id_list():
    repo_root = Path(__file__).resolve().parents[2]
    # vlm-family read: vlm-caption.js is split into vlm-caption/*.js
    # (gallery precedent).
    source = _vlm_caption_family_source(repo_root)

    assert "getActiveSelectionToken" in source
    assert "selection_token" in source
    assert "resolveSelectedImageIds" not in source
    assert "JSON.stringify(batchTarget.payload)" in source


def test_mass_tag_editor_reuses_gallery_filter_contract_for_filter_scope():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend" / "js" / "mass-tag-editor.js").read_text(
        encoding="utf-8"
    )

    assert "window.App.buildSelectionFilterRequest(filters)" in source
    assert "tagMode" in source
    assert "excludeTags" in source
    assert "excludeGenerators" in source
    assert "excludeRatings" in source
    assert "excludeCheckpoints" in source
    assert "excludeLoras" in source


def test_dataset_caption_refresh_batches_without_silent_500_cap():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)

    assert "const batchSize = 500;" in source
    assert "targetIds.slice(i, i + batchSize)" in source
    assert "image_ids: ids.slice(0, 500)" not in source


def test_dataset_export_uses_background_progress_job():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    part3 = _dataset_family_source(repo_root)
    local_import = part3

    assert "/api/dataset/export/start" in part3
    assert "/api/bulk-jobs/${encodeURIComponent(jobId)}" in part3
    assert "/api/bulk-jobs/${encodeURIComponent(jobId)}/cancel" in part3
    assert "/api/dataset/export/progress" not in part3
    assert "/api/dataset/export/cancel" not in part3
    assert "sd-image-sorter-dataset-export-job" in part3
    assert "sessionStorage.setItem" in part3
    assert "sessionStorage.getItem" in part3
    assert "sessionStorage.removeItem" in part3
    assert 'id="dataset-export-progress-text"' in html
    assert 'id="btn-dataset-export-cancel"' in html
    assert "DM._buildExportPayload" in local_import
    assert "/api/dataset/export'" not in part3
    assert "/api/dataset/export'" not in local_import


def test_dataset_maker_guards_session_preview_and_heavy_audit_ux():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    dataset_js = _dataset_family_source(repo_root)
    local_import = dataset_js
    part2 = dataset_js
    pipeline = dataset_js
    onboarding = (
        repo_root / "frontend" / "js" / "modules" / "components" / "onboarding.js"
    ).read_text(encoding="utf-8")
    css = (repo_root / "frontend" / "css" / "dataset-pipeline.css").read_text(
        encoding="utf-8"
    )
    maker_css = (repo_root / "frontend" / "css" / "dataset-maker.css").read_text(
        encoding="utf-8"
    )

    assert 'id="dataset-audit-check-phash" checked' not in html
    assert ".dataset-audit-modal-card" in css
    assert "dataset-export-action-bar" in html
    assert "#view-dataset .dataset-export-action-bar" in maker_css
    assert "position: sticky" in maker_css
    assert "top: -12px" in maker_css
    assert "max-height: none" in maker_css
    assert "#view-dataset .dataset-export-preview-list" in maker_css
    assert "overflow: visible" in maker_css
    assert "_flushPendingCaptionEdit" in dataset_js
    assert "const value = ta.value;" in dataset_js
    assert "_serializeLocalDatasetState" in local_import
    assert "_restoreLocalSession" in local_import
    assert "let previewRequestSeq" in pipeline
    assert "previewAbortController.abort()" in pipeline
    assert "renderPreviewError" in pipeline
    assert "Fall through to the old lightweight filename-only preview" not in pipeline
    assert "_queueIdsForCurrentFilter" in part2
    assert "list.classList.contains('is-virtualized')" in part2
    assert "document.elementFromPoint" in onboarding
    assert "navTarget.click()" in onboarding
    # QA P3-4: the tour's auto-start (and its view guards) is formally retired;
    # the tour must only be reachable programmatically via the guide's 🎓 Tour.
    assert "AUTO_START_ENABLED" not in onboarding
    assert "markHasSeenImages" not in onboarding
    assert "cleanupResidualTourUi();" in onboarding


def test_dataset_new_i18n_keys_are_translated():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    en = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8")
    zh = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="(dataset\.[^"]+)"', html))
    keys |= {
        "dataset.exportPreviewLoading",
        "dataset.exportPreviewFailed",
        "dataset.translationEmpty",
        "dataset.queueFilterEmpty",
        "dataset.sessionRestorePartial",
        "dataset.confirmClearTitle",
        "dataset.keepBadge",
        "dataset.dedupeNoSelection",
        "dataset.dedupeDone",
        "dataset.auditPhashUnavailable",
        "dataset.auditPhashChecked",
        "dataset.auditPhashUnavailableShort",
        "dataset.auditPhashCheckedShort",
        "dataset.exportPreviewLoadedOnlyEdit",
    }
    missing = [
        key for key in sorted(keys) if f"'{key}'" not in en or f"'{key}'" not in zh
    ]

    assert not missing


def test_dataset_maker_ui_removes_confusing_external_tool_copy():
    repo_root = Path(__file__).resolve().parents[2]
    checked_files = [
        repo_root / "frontend" / "index.html",
        repo_root / "frontend" / "js" / "lang" / "en.js",
        repo_root / "frontend" / "js" / "lang" / "zh-CN.js",
    ]

    violations = [
        file_path.relative_to(repo_root).as_posix()
        for file_path in checked_files
        if "LoraHub" in file_path.read_text(encoding="utf-8")
    ]

    assert not violations, (
        "Dataset Maker UI must not mention external tools: " + ", ".join(violations)
    )


def test_dataset_export_tab_does_not_show_workbench_find_replace():
    repo_root = Path(__file__).resolve().parents[2]
    css = (repo_root / "frontend" / "css" / "dataset-pipeline.css").read_text(
        encoding="utf-8"
    )

    assert '[data-active-tab="export"] #dataset-step-findreplace' in css


def test_dataset_folder_and_output_browse_buttons_are_real_click_buttons():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    dataset_js = _dataset_family_source(repo_root)
    local_import = dataset_js

    assert (
        'type="button" class="btn btn-ghost btn-small" id="btn-dataset-folder-import-browse"'
        in html
    )
    assert (
        'type="button" class="btn btn-ghost btn-small" id="btn-dataset-browse-output"'
        in html
    )
    assert "btn-dataset-folder-import-browse" in local_import
    # PR #18 (issue 4): browse button is a toggle — mousedown opens or closes
    # the folder browser depending on whether it's already showing.
    assert "addEventListener('mousedown'" in local_import
    assert "window.showFolderBrowser(pathInput)" in local_import
    assert "btn-dataset-browse-output" in dataset_js
    assert "showFolderBrowser(input)" in dataset_js


def test_dataset_maker_sidecar_export_limits_are_visible_before_caption_work():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    local_import = _dataset_family_source(repo_root)
    en = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(encoding="utf-8")
    zh = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert 'id="dataset-sidecar-import-notice"' in html
    assert 'id="dataset-sidecar-source-status"' in html
    assert "Gallery" in html and "folder path" in html
    # v3.2.2: drag/drop folders, ZIP, and RAR also support beside_image
    # by writing the .txt next to the imported copy in the upload dir.
    assert "next to the imported copy" in html
    assert "rarfile" in html and "unrar" in html
    assert "dataset.sidecarNoticeTitle" in en and "dataset.sidecarNoticeTitle" in zh
    assert "dataset.sidecarSourceStatus" in en and "dataset.sidecarSourceStatus" in zh
    # RAR + ZIP both flow through the server-side upload route; the
    # legacy ``unsupportedRarFiles`` client-side helper was removed (it
    # always returned []), so only the server-side handling remains.
    assert "ARCHIVE_EXTS" in local_import
    assert "upload-files" in local_import


def test_dataset_maker_step2_owns_caption_formatting_and_translation_settings():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    dataset_js = _dataset_family_source(repo_root)

    setup_start = html.index('id="dataset-step-setup"')
    caption_start = html.index('data-i18n="dataset.cardCaptionTitle"')
    find_replace_start = html.index('id="dataset-step-findreplace"', caption_start)
    setup_block = html[setup_start:caption_start]
    caption_block = html[caption_start:find_replace_start]
    export_start = html.index('id="dataset-step-export"')
    export_end = html.index('id="dataset-export-preview"', export_start)
    export_block = html[export_start:export_end]

    for marker in [
        'id="dataset-trigger"',
        'id="dataset-lora-type"',
        'id="dataset-common-tags"',
        'id="btn-dataset-quickfill-trigger"',
        'id="btn-dataset-quickfill-quality"',
    ]:
        assert marker in setup_block
        assert marker not in caption_block
        assert marker not in export_block

    for marker in [
        'id="dataset-export-prefix"',
        'id="dataset-template-options"',
        'id="dataset-template-override"',
        'id="btn-dataset-clear-prefix"',
        'id="btn-dataset-reset-template"',
        'id="btn-dataset-refresh-zh-aid"',
        'id="dataset-replace-rules"',
        'id="dataset-max-tags"',
        'id="dataset-translation-options"',
        'id="dataset-translation-provider-mode"',
        'id="dataset-translation-external-provider"',
        'id="dataset-translation-prompt"',
    ]:
        assert marker in caption_block
        assert marker not in export_block

    assert 'id="dataset-naming-pattern"' in export_block
    assert 'id="dataset-naming-pattern"' not in setup_block
    assert 'id="dataset-naming-pattern"' not in caption_block
    assert 'id="dataset-export-content-mode"' in html
    assert 'type="hidden" id="dataset-export-content-mode"' in html
    assert 'data-i18n="dataset.exportContentMode"' not in export_block
    assert 'data-i18n="dataset.namingLegend"' not in export_block
    assert "btn-dataset-clear-prefix" in dataset_js
    assert "btn-dataset-reset-template" in dataset_js
    assert "btn-dataset-refresh-zh-aid" in dataset_js


def test_smart_tag_has_visible_booru_to_captioner_grounding_control():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    smart_tag_js = _smart_tag_family_source(repo_root)

    assert 'id="smart-tag-vlm-grounding"' in html
    assert 'data-i18n="smartTag.vlmGrounding"' in html
    assert "vlm_grounding" in smart_tag_js
    assert "toriigate_grounding" in smart_tag_js


def test_dataset_export_tab_is_export_only_with_output_mode_payload():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    part3 = _dataset_family_source(repo_root)
    local_import = (
        repo_root / "frontend" / "js" / "dataset" / "local-import.js"
    ).read_text(encoding="utf-8")
    pipeline = part3

    assert 'name="dataset-output-mode"' in html
    assert 'value="folder"' in html
    assert 'value="beside_image"' in html
    assert 'id="dataset-beside-image-warning"' in html
    assert "data-export-folder-only" in html
    assert "DM._outputMode" in part3
    # FE-1 2b: _buildExportPayload has ONE implementation, hosted in
    # dataset/local-import.js (it reads local-source state); the former part3
    # copy was dead code and stays removed — the family-wide occurrence count
    # of the payload's output_mode line must remain exactly 1, in local-import.
    assert part3.count("output_mode: outputMode") == 1
    assert "output_mode: outputMode" in local_import
    assert "_sidecarCapabilityStats" in part3
    assert "_exportDisabledReason" in part3
    assert "!this._exportDisabledReason?.()" in local_import
    assert "{ ...this, imageIds:" not in local_import
    assert "_syncOutputModeUi" in part3
    assert "output_mode" in pipeline


def test_dataset_audit_is_modal_not_inline_details():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    pipeline = _dataset_family_source(repo_root)
    css = (repo_root / "frontend" / "css" / "dataset-pipeline.css").read_text(
        encoding="utf-8"
    )

    assert '<details class="dataset-audit-inline"' not in html
    assert 'id="dataset-audit-modal"' in html
    assert "dataset-audit-modal-card" in html
    assert "DM._showAuditModal" in pipeline
    assert "DM._hideAuditModal" in pipeline
    assert "panel.open = true" not in pipeline
    assert ".dataset-audit-modal-card" in css
    assert ".dataset-audit-inline:not([open]) .dataset-audit-body" not in css


def test_dataset_global_caption_scope_and_tag_categories_are_available():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    part2 = _dataset_family_source(repo_root)
    part3 = part2
    css = (repo_root / "frontend" / "css" / "dataset-maker.css").read_text(
        encoding="utf-8"
    )

    assert 'id="dataset-caption-scope"' in html
    assert 'id="dataset-dedupe-scope"' not in html
    assert "_captionScopeIds" in part3
    assert "dataset-caption-scope" in part3
    assert "dataset-dedupe-scope" not in part3
    assert "_classifyTagCategory" in part2
    assert "dataset-tag-pill-category-" in part2
    # Every tag must resolve to a real danbooru group + color: the frontend pulls
    # authoritative categories from the backend 14-class classifier and caches them
    # so pills recolor away from the local first-paint guess.
    assert "_ensureTagCategories" in part2
    assert "/api/prompts/categorize" in part2
    # The 14 backend categories (tag_rules.categorize_tag) each need a pill color.
    for category in [
        "quality",
        "meta",
        "rating",
        "character",
        "body",
        "outfit",
        "expression",
        "pose",
        "action",
        "angle",
        "background",
        "style",
        "artist",
        "unknown",
    ]:
        assert f"dataset-tag-pill-category-{category}" in css


def test_dataset_custom_dropdown_does_not_close_when_its_own_list_scrolls():
    repo_root = Path(__file__).resolve().parents[2]
    pipeline = _dataset_family_source(repo_root)

    # The custom dropdown registers its outside-interaction listeners ONCE
    # (shared across every wrapped select) instead of per-select, which
    # previously leaked 3 permanent listeners + a body-appended node on
    # every init. The scroll handler must still keep an open list open
    # when the user scrolls INSIDE it.
    assert "ensureSharedListeners" in pipeline
    assert "SHARED_LISTENERS_INSTALLED" in pipeline
    assert "list.contains(target)" in pipeline
    # The old per-select leak pattern must be gone.
    assert "function handleOutsideScroll(e)" not in pipeline
    assert "document.addEventListener('click', closeList)" not in pipeline


def test_gallery_send_to_dataset_maker_button_tracks_selection_state():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    # NOTE(split): first-match regex on `const buttonIds = [` (count==1 today); a
    # second declaration earlier in the family concat would repoint the pin.
    app_source = _app_family_source(repo_root)

    assert "'btn-send-selection-to-dataset-maker'" in app_source
    button_block = re.search(
        r"const buttonIds = \[(?P<body>.*?)\];", app_source, re.DOTALL
    )
    assert button_block is not None
    assert "'btn-send-selection-to-dataset-maker'" in button_block.group("body")


def test_dataset_init_syncs_current_naming_preset_ui():
    repo_root = Path(__file__).resolve().parents[2]
    # init() lives in dataset/core.js after the split; the ordering assertion
    # below is meaningful only within that single file.
    dataset_js = (repo_root / "frontend" / "js" / "dataset" / "core.js").read_text(
        encoding="utf-8"
    )

    assert "this._onPresetChange?.();" in dataset_js
    assert dataset_js.index("this._onPresetChange?.();") < dataset_js.index(
        "this._updateNamingPreview();"
    )


def test_dataset_project_store_load_order_and_stable_controls():
    repo_root = Path(__file__).resolve().parents[2]
    core = (repo_root / "frontend" / "js" / "dataset" / "core.js").read_text(
        encoding="utf-8"
    )
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    project_store = (
        repo_root / "frontend" / "js" / "dataset" / "project-store.js"
    ).read_text(encoding="utf-8")
    project_settings = (
        repo_root / "frontend" / "js" / "dataset" / "project-settings.js"
    ).read_text(encoding="utf-8")

    gallery_index = core.index("/static/js/dataset/gallery-import.js")
    settings_index = core.index("/static/js/dataset/project-settings.js")
    project_index = core.index("/static/js/dataset/project-store.js")
    events_index = core.index("/static/js/dataset/events.js")
    caption_split_index = core.index("/static/js/dataset-maker-caption-split.js")
    annotation_index = core.index("/static/js/dataset/annotation-ledger.js")
    assert gallery_index < settings_index < project_index < events_index
    assert caption_split_index < annotation_index
    assert "this._initProjectStore?.();" in core
    assert "this._initProjectSettingsPersistence?.();" in core
    for test_id in (
        "dataset-project-toolbar",
        "dataset-project-selector",
        "dataset-project-save-as",
        "dataset-project-save",
        "dataset-project-menu",
        "dataset-project-missing",
        "dataset-annotation-status",
        "dataset-save-caption-version",
        "dataset-caption-history",
    ):
        assert f'data-testid="{test_id}"' in html
    assert "expected_revision: active.revision" in project_store
    assert "settings: settingsSnapshot.settings" in project_store
    assert "settings: active.settings" in project_store
    assert "_prepareProjectSettingsRestore" in project_store
    assert "settings_version: SETTINGS_VERSION" in project_settings
    assert "dataset_project_revision_conflict" in project_store


def test_dataset_vocab_uses_explicit_actions_not_hidden_click_cycle():
    repo_root = Path(__file__).resolve().parents[2]
    source = _dataset_family_source(repo_root)
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "function cycleTag" not in source
    assert "dataset-vocab-action" in source
    assert "dataset.vocabAddCommon" in source
    assert "Third click clears" not in html


def test_smart_tag_supports_path_source_dataset_items():
    repo_root = Path(__file__).resolve().parents[2]
    frontend = _smart_tag_family_source(repo_root)
    router = (repo_root / "backend" / "routers" / "smart_tag.py").read_text(
        encoding="utf-8"
    )
    # smart_tag_service.py was decomposed into the services/smart_tag/ package
    # (facade + submodules); the service-side contract strings live in the union.
    service = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [repo_root / "backend" / "services" / "smart_tag_service.py"]
        + sorted((repo_root / "backend" / "services" / "smart_tag").glob("*.py"))
    )

    assert "image_paths: sources.imagePaths" in frontend
    assert "selection_token: sources.selectionToken" in frontend
    assert "dataset_scan_token: sources.datasetScanToken" in frontend
    assert "/api/smart-tag/results" in frontend
    assert "selection_token: Optional[str]" in router
    assert "dataset_scan_token: Optional[str]" in router
    assert "image_paths: List[str]" in router
    assert "get_caption_results_page" in service
    assert "caption_result_count" in service


def test_smart_tag_uses_model_specific_tagger_defaults():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    frontend = _smart_tag_family_source(repo_root)
    # smart_tag_service.py was decomposed into the services/smart_tag/ package
    # (facade + submodules); the service-side contract strings live in the union.
    service = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [repo_root / "backend" / "services" / "smart_tag_service.py"]
        + sorted((repo_root / "backend" / "services" / "smart_tag").glob("*.py"))
    )
    # tagging_service.py was likewise decomposed into the services/tagging/
    # package; the tagger-side contract strings live in the union.
    tagger_service = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [repo_root / "backend" / "services" / "tagging_service.py"]
        + sorted((repo_root / "backend" / "services" / "tagging").glob("*.py"))
    )
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert 'id="smart-tag-max-tags"' in html
    assert 'data-i18n="smartTag.maxTags"' in html
    assert "smartTag.maxTags" in en_source
    assert "smartTag.maxTags" in zh_source
    assert "model?.default_threshold" in frontend
    assert "model?.default_character_threshold" in frontend
    assert "model?.default_copyright_threshold" in frontend
    assert "model?.default_max_tags_per_image" in frontend
    assert "function maxTagsInputWasTouched()" in frontend
    assert "return toFiniteMaxTags(input?.value, 0);" in frontend
    assert "getPayloadThresholdsForModel(model, sharedThresholds)" in frontend
    assert (
        "const maxTagsPerImage = getPayloadMaxTagsForModels(uniqueTaggers)" in frontend
    )
    assert "max_tags_per_image: maxTagsPerImage" in frontend
    assert "default_copyright_threshold" in tagger_service
    assert "default_max_tags_per_image" in tagger_service
    assert "def _tagger_defaults(model_name: str)" in service
    assert "multi_max_tag_defaults" in service


def test_manual_sort_resume_failure_does_not_render_null_visible_banner():
    repo_root = Path(__file__).resolve().parents[2]
    # Manual-sort-family read: manual-sort.js is being split into
    # manual-sort/*.js (censor/dataset/app/gallery precedent).
    source = _manual_sort_family_source(repo_root)

    assert "renderManualSortResumeBanner(null, { visible: true })" not in source
    assert "previousResumeSnapshot" in source


def test_tagger_ui_does_not_market_cpu_as_safe_mode():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    checked_sources = [("frontend/js/app.js family", _app_family_source(repo_root))]
    checked_files = [
        repo_root / "frontend" / "js" / "lang" / "en.js",
        repo_root / "frontend" / "js" / "lang" / "zh-CN.js",
        repo_root / "backend" / "services" / "tagging_service.py",
        *sorted((repo_root / "backend" / "services" / "tagging").glob("*.py")),
        repo_root / "backend" / "tagger.py",
        repo_root / "backend" / "toriigate_tagger.py",
    ]
    forbidden_phrases = [
        "CPU " + "Safe " + "Mode",
        "Safe " + "Mode",
        "较慢" + "但" + "更" + "稳",
        "避免" + "崩溃",
        "stable " + "CPU " + "run",
    ]
    violations: list[str] = []

    for file_path in checked_files:
        checked_sources.append(
            (
                file_path.relative_to(repo_root).as_posix(),
                file_path.read_text(encoding="utf-8"),
            )
        )

    for label, source in checked_sources:
        for phrase in forbidden_phrases:
            if phrase in source:
                violations.append(f"{label}: contains {phrase!r}")

    assert not violations, (
        "Tagger UI/runtime wording must not market CPU as safer.\n"
        + "\n".join(violations)
    )


def test_manual_sort_start_uses_json_body_not_query_string_filters():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "async startSortSession(" in source
    assert "return this.post('/api/sort/start', {" in source
    assert "params.set('tags', tags.join(','))" not in source
    assert "this.post(`/api/sort/start?${params}`)" not in source


def test_gallery_load_finally_clears_only_active_sequence():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "let _activeImageLoadSequence = 0;" in source
    assert "const isActiveLoad = _activeImageLoadSequence === loadSequence;" in source
    assert "RequestManager.complete(IMAGE_LOAD_KEY, controller);" in source


def test_autosep_critical_action_settings_are_visible_on_main_panel():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # Autosep-family read: autosep.js is being split into autosep/*.js.
    # NOTE(split): the operation-radio handler literal below lives inside
    # initAutoSeparate (autosep/init.js after the split).
    source = _autosep_family_source(repo_root)

    assert "autosep-action-settings" in html
    assert 'name="autosep-operation-mode-main"' in html
    assert 'data-autosep-setting="confirmBeforeMove"' in html
    assert 'data-autosep-setting="rememberDestination"' in html
    assert "setAutoSepOperationMode(input.value, { persist: true })" in source


def test_metadata_resolving_chip_is_driven_by_stats_contract():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "metadata-status-chip" in html
    assert "stats.metadata_pending" in source
    assert "stats.scan_status" in source
    assert "stats.scan_library_ready" in source
    assert (
        "const countsResolving = metadataPending > 0 || (scanRunning && !scanLibraryReady);"
        in source
    )
    assert "gallery.metadataResolving" in source
    assert "gallery.scanResolving" in source
    assert (
        "countEl.textContent = countsResolving && count === 0 ? '…' : String(count);"
        in source
    )


def test_filter_facet_search_uses_backend_queries_not_prelimited_local_cache():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "const FACET_SUGGESTION_LIMIT = 24;" in source
    assert "API.getTagsLibrary('frequency', {" in source
    assert "API.getPromptsLibrary({" in source
    assert "API.getAnalyticsFacet(facet, {" in source
    assert "selectedCheckpointValues.forEach((checkpointValue)" in source
    assert "(filterState.loras || []).forEach((lora)" in source
    assert "tagsLibraryCache" not in source
    assert "promptsLibraryCache" not in source
    assert (
        "return this.get(`/api/tags/library?sort_by=${sortBy}&limit=${limit}`);"
        not in source
    )
    assert "return this.get(`/api/prompts/library?limit=${limit}`);" not in source
    assert "return this.get(`/api/loras/library?limit=${limit}`);" not in source


def test_gallery_delete_key_removes_from_gallery_not_disk():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    # NOTE(split): first-match regex on the Delete-key branch (count==1 today); the
    # body terminator pins an 8-space indent - move the block verbatim.
    source = _app_family_source(repo_root)

    match = re.search(
        r"else if \(e\.key === 'Delete'\) \{(?P<body>.*?)\n        \}",
        source,
        re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    assert "removeSelectedGalleryImages();" in body
    assert "deleteSelectedGalleryImages();" not in body


def test_manual_sort_start_routes_unfinished_sessions_to_resume():
    repo_root = Path(__file__).resolve().parents[2]
    # Manual-sort-family read: manual-sort.js is being split into
    # manual-sort/*.js (censor/dataset/app/gallery precedent).
    source = _manual_sort_family_source(repo_root)

    assert "confirmResumeSavedSessionFromStart(savedSession)" in source
    assert "resumeSavedSession(savedSession)" in source
    assert "discard the saved session first" in source
    assert "replaceExisting = false" in source


def test_gallery_context_menu_has_workflow_actions_and_trash_is_explicit():
    repo_root = Path(__file__).resolve().parents[2]
    # Gallery-family read: the pinned literals live in gallery/context-menu.js
    # after the split (family == gallery.js until then).
    gallery_source = _gallery_family_source(repo_root)
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    app_source = _app_family_source(repo_root)
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    # Terminator bounds the body on the `_positionContextMenu(` sibling that
    # directly follows `_showContextMenu` (both live in gallery/context-menu.js
    # after the split). The previous terminator (`},\n\n    // Cleanup`) also
    # required the destroy() block to be adjacent in the SAME object literal --
    # an adjacency the Object.assign family split breaks (destroy moved to
    # gallery/lifecycle-a11y.js). Call sites use `this._positionContextMenu(`
    # at deeper indentation, so this cannot terminate early inside the body.
    match = re.search(
        r"_showContextMenu\(e, image\) \{(?P<body>.*?)"
        r"\n    \},\n\n    _positionContextMenu\(",
        gallery_source,
        re.DOTALL,
    )
    assert match is not None
    body = match.group("body")

    expected_keys = [
        "gallery.contextPreview",
        "gallery.contextSelectImage",
        "gallery.contextMoveImage",
        "gallery.contextCopyImage",
        "gallery.contextSendToCensor",
        "gallery.contextFindSimilar",
        "gallery.contextPromptHelper",
        "gallery.contextReadMetadata",
        "gallery.contextFilterCheckpoint",
        "gallery.contextOpenFolder",
        "gallery.contextCopyPath",
        "gallery.contextRemoveFromGallery",
        "gallery.contextMoveToTrash",
        "gallery.contextApplyToSelected",
    ]
    for key in expected_keys:
        assert key in body
        assert key in en_source
        assert key in zh_source

    assert (
        "const actionImageIds = isSelected && selectedImageIds.length > 1 ? selectedImageIds : [imageId];"
        in body
    )
    assert (
        "app.moveOrCopyGalleryImages?.(actionImageIds, 'move', { source: 'context' })"
        in body
    )
    assert (
        "app.moveOrCopyGalleryImages?.(actionImageIds, 'copy', { source: 'context' })"
        in body
    )
    assert "openPromptBuildFromImage?.(image.id)" in body
    assert "openReaderFromImage?.(image.id" in body
    assert "app.removeGalleryImagesByIds?.(actionImageIds)" in body
    assert "app.deleteGalleryImagesByIds?.(actionImageIds)" in body
    assert "contextDelete" not in body
    assert "Delete from Disk" not in body

    assert "moveOrCopyGalleryImages" in app_source
    assert "deleteGalleryImagesByIds" in app_source
    assert "removeGalleryImagesByIds" in app_source
    assert "operating system Trash / Recycle Bin" in app_source
    assert "emitSelectionStateChanged" in app_source


def test_gallery_single_color_action_patches_frontend_color_fields():
    repo_root = Path(__file__).resolve().parents[2]
    # Gallery-family read: the color-patch pin lives in gallery/modal-analysis.js
    # after the split (family == gallery.js until then).
    gallery_source = _gallery_family_source(repo_root)

    assert "_buildColorAnalysisPatch" in gallery_source
    for field in [
        "dominant_colors",
        "avg_brightness",
        "color_temperature",
        "color_saturation",
        "brightness_distribution",
    ]:
        assert f"'{field}'" in gallery_source

    assert (
        "this._patchImageState(id, { color_data: result.color_data });"
        not in gallery_source
    )
    assert "this._patchImageState(id, colorPatch);" in gallery_source


def test_queue_manager_gallery_filters_use_backend_selection_contract():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend" / "js" / "queue-solitaire.js").read_text(
        encoding="utf-8"
    )

    assert "resolveGalleryFilterMatches" in source
    assert "api.createSelectionToken(filters" in source
    assert "api.getSelectionChunk(selectionToken" in source
    assert "selection_token" in source
    assert "queueSet.has(normalized)" in source
    assert "if (fromGallery)" in source


def test_gallery_selection_panel_is_desktop_user_facing_not_visible_dom_jargon():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)
    ui_refresh = (repo_root / "frontend" / "js" / "ui-refresh.js").read_text(
        encoding="utf-8"
    )
    filter_store = (
        repo_root / "frontend" / "js" / "stores" / "filter-store.js"
    ).read_text(encoding="utf-8")
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "btn-select-visible" not in html
    assert "selection-panel-more" not in html
    assert "selection.selectVisible" not in ui_refresh
    assert "selection.invertVisible" not in ui_refresh
    assert "selection.moreActions" not in en_source
    assert "selection.selectVisible" not in zh_source
    assert "selection-panel-section" in html
    assert "selection.selectAllFilteredHelp" in html
    assert "选择当前筛选全部" in zh_source
    assert (
        "作用域"
        not in zh_source[
            zh_source.find("'scope.useGallery'") : zh_source.find(
                "// ========================", zh_source.find("'scope.useGallery'")
            )
        ]
    )
    assert (
        "const VALID_ASPECT_RATIO_FILTERS = new Set(['square', 'landscape', 'portrait']);"
        in source
    )
    assert (
        "aspectRatio: normalizeAspectRatioFilter(source.aspectRatio) || null" in source
    )
    assert "normalizeAspectRatioFilter(filters.aspectRatio)" in source
    assert "normalizeAspectRatioFilter(dimensions?.aspectRatio)" in source
    assert "const savedFilters = cloneFilterState(loadSavedFilterState());" in source
    assert "['square', 'landscape', 'portrait'].includes" in filter_store
    assert ".selection-panel-section" in css


def test_gallery_setup_button_lives_in_nav_not_floating():
    """Setup button should be in the nav-actions bar, not a floating FAB."""
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )

    assert 'id="btn-open-model-manager"' in html
    assert "gallery-model-manager-fab" not in html
    assert "gallery-model-manager-fab" not in css


def test_export_ui_explains_output_formats_before_action():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "export-format-description" in html
    assert "batch-export-content-description" in html
    assert 'value="prompt_numbered"' in html
    assert 'data-i18n="export.groupAdvanced"' in html
    assert 'data-i18n="batchExport.groupAdvanced"' in html
    assert "function getExportFormatDescription" in source
    assert "function getBatchExportContentDescription" in source
    assert "export.descPromptNumbered" in en_source
    assert "export.descPromptNumbered" in zh_source
    assert "Combined Export..." in en_source
    assert "合并导出..." in zh_source
    assert "Training captions..." in en_source
    assert "训练 caption..." in zh_source
    assert "训练 caption" in zh_source
    assert "可选 Class Token + AI caption + Prompt + Tags" in zh_source
    assert "训练用 .txt" in zh_source
    assert "Prompt Sheet..." not in en_source
    assert "Caption Files..." not in en_source


def test_scan_modal_advanced_summary_does_not_break_chinese_label():
    repo_root = Path(__file__).resolve().parents[2]
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )

    assert (
        '#scan-modal .guided-advanced-summary > [data-i18n="scan.advancedSummary"]'
        in css
    )
    assert "white-space: nowrap;" in css
    assert "word-break: keep-all;" in css
    assert "#scan-modal .guided-advanced-hint" in css
    assert "grid-template-columns: max-content minmax(0, 1fr) max-content;" in css
    assert "text-overflow: ellipsis;" in css
    assert "min-width: 0;" in css


def test_scan_progress_eta_uses_real_counted_totals_and_separate_metadata_totals():
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "function getScanProgressMetrics(progress)" in source
    assert "const totalFinal = progress?.total_final === true;" in source
    assert (
        "const metadataTotalFinal = progress?.metadata_total_final === true;" in source
    )
    assert (
        "const showingMetadata = progress?.step === 'metadata' && importComplete;"
        in source
    )
    assert "const showEta = showingMetadata" in source
    assert "scan-import:${totalFinal ? total : 'counting'}" in source
    assert "scan-metadata:${metadataTotalFinal ? metadataTotal : 'growing'}" in source
    assert "progress.countingImages" in source
    assert "progress.detailsStillCounting" in source
    assert "Counting images... {count} found" in en_source
    assert "checking the final detail count" in en_source
    assert "正在统计图片... 已找到 {count} 张" in zh_source
    assert "正在确认详细信息总数" in zh_source


def test_queue_solitaire_escapes_file_and_section_values_before_inner_html():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend" / "js" / "queue-solitaire.js").read_text(
        encoding="utf-8"
    )

    assert "function escapeQueueHtml" in source
    assert 'value="${escapeQueueHtml(section.name)}"' in source
    assert (
        '<option value="${escapeQueueHtml(s.id)}">${escapeQueueHtml(s.name)}' in source
    )
    assert (
        "escapeQueueHtml(item?.outputFilename || item?.originalFilename || state.previewId)"
        in source
    )
    assert (
        "${item?.outputFilename || item?.originalFilename || state.previewId}"
        not in source
    )


def test_custom_tagger_profile_ui_and_payload_contract():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert 'id="tag-custom-profile-select"' in html
    assert 'value="wd14"' in html
    assert 'value="camie-tagger-v2"' in html
    assert 'value="pixai-tagger-v0.9"' in html
    assert "custom_profile: options.customProfile || null" in source
    assert "options.customProfile = customProfile;" in source
    assert "options.modelName = customProfile;" in source
    assert "if (tagsPath)" in source
    assert "options.tagsPath = tagsPath;" in source
    assert "showToast(appT('tag.tagsMetadataRequired'" not in source
    assert (
        "if (applyModelDefaults && meta && (!isCustom || effectiveModelForUi !== 'custom'))"
        in source
    )
    assert "batchSelect?.dataset.userChosen === '1'" in source
    assert "modal.tagCustomProfile" in en_source
    assert "modal.tagCustomProfile" in zh_source
    assert "tagger.customCamieHelp" in en_source
    assert "tagger.customCamieHelp" in zh_source
    assert "tag.tagsMetadataRequired" in en_source
    assert "tag.tagsMetadataRequired" in zh_source
    assert "Optional if the file sits next to the model" in en_source
    assert "如果文件就在模型旁边可不填" in zh_source


def test_feature_setup_explains_lightweight_startup_and_cache_limit():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)

    assert "model-manager-summary" in html
    assert "renderFeatureAvailabilityNotice" in source
    assert "features.prepare.wd14" in source
    assert "features.ready.wd14" not in source
    assert "thumbnail-cache-limit-input" in source
    assert "saveDiskSettings" in source
    assert "requestCoreRuntimeRebuild" in source
    assert "disk.thumbnailTradeoffHint" in source
    assert "/api/disk/runtime/rebuild-core" in source
    assert "models.restartAfterInstallWithPackages" in source


def test_scan_stalled_diagnostics_are_visible_and_copyable_from_frontend():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    source = _app_family_source(repo_root)
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "scan-diagnostics-card" in html
    assert "btn-copy-scan-diagnostics" in html
    assert "btn-open-scan-log" in html
    assert "btn-copy-scan-log-path" in html
    assert "btn-stop-scan-from-diagnostics" in html
    assert "scan-diagnostics-meta" in html
    assert 'data-i18n-aria="scan.diagnosticsMetaLabel"' in html
    assert "scan-storage-hint" in html
    assert 'id="scan-diagnostics-message" data-i18n' not in html
    assert "messageEl.removeAttribute('data-i18n')" in source
    assert "stepEl.removeAttribute('data-i18n')" in source
    assert "currentEl.removeAttribute('data-i18n')" in source
    assert "updateScanDiagnosticsCard(progress)" in source
    assert "_scanLastProgress" in source
    assert "_updateBgScanProgress(_scanLastProgress)" in source
    assert "progress?.attention_required" in source
    assert "API.getSupportDiagnostics(200)" in source
    assert "API.getSupportDiagnostics(1)" in source
    assert "payload?.log_file_path_redacted" in source
    assert "rememberScanLogPath" in source
    assert "progress?.attention_required" in source
    assert "buildScanAttentionMessage(progress" in source
    assert "SCAN_DIAGNOSTICS_HOLD_MS" in source
    assert "pathEl.title = result.path_redacted" in source
    assert "result.message || appT('scan.openLogUnavailable'" not in source
    assert "scan.backgroundStalledDetailed" in source
    assert "copyScanDiagnostics" in source
    assert "openScanLogFile" in source
    assert "copyScanLogPath" in source
    assert "/api/support/diagnostics" in source
    assert "/api/support/open-log" in source
    assert ".scan-diagnostics-card" in css
    for key in [
        "scan.diagnosticsTitle",
        "scan.backgroundStalledDetailed",
        "scan.copyLogPath",
        "scan.logPathCopied",
        "scan.logPathCopyFailed",
        "scan.copyLogPathUnavailable",
        "scan.diagnosticsMetaLabel",
    ]:
        assert key in en_source
        assert key in zh_source


def test_tag_category_copy_and_promptlab_board_are_wired():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    # Gallery-family read: contextCopyTagCategory/TagCategoryCopy.showMenu live
    # in gallery/context-menu.js after the split; index.html must still list
    # the retained /static/js/gallery.js AFTER tag-category-copy.js.
    gallery_source = _gallery_family_source(repo_root)
    # Reader-family read: image-reader.js is decomposed into image-reader/*.js
    # (gallery precedent); _copyPromptCategory/_renderReaderCategoryTags live
    # in image-reader/category-tags.js after the split.
    reader_source = _reader_family_source(repo_root)
    # Prompt-lab-family read: prompt-lab.js is decomposed into prompt-lab/*.js
    # (v321/gallery precedent); each pinned literal lives in whichever family
    # file hosts it.
    promptlab_source = _prompt_lab_family_source(repo_root)
    copy_source = (repo_root / "frontend" / "js" / "tag-category-copy.js").read_text(
        encoding="utf-8"
    )
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    app_source = _app_family_source(repo_root)
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (repo_root / "frontend" / "js" / "lang" / "zh-CN.js").read_text(
        encoding="utf-8"
    )

    assert "/static/js/tag-category-copy.js" in html
    assert html.index("/static/js/tag-category-copy.js") < html.index(
        "/static/js/gallery.js"
    )
    assert 'id="btn-copy-tags-category"' in html
    assert 'id="reader-copy-prompt-category"' in html
    assert 'id="reader-category-tags-section"' in html
    assert 'id="promptlab-category-board-modal"' in html
    assert 'id="btn-promptlab-category-board"' in html
    assert 'id="pl-build-category-workbench"' in html
    assert 'id="pl-build-use-checked"' in html
    assert 'id="pl-build-copy-caption"' in html
    assert 'id="pl-build-clean-prompt"' in html
    assert 'id="pl-build-drop-quality"' in html
    assert 'id="pl-build-space-tags"' in html
    assert 'id="pl-build-reorder"' in html

    assert "window.TagCategoryCopy" in copy_source
    assert "/api/prompts/categorize" in copy_source
    assert "CORE_BOARD_GROUPS" in copy_source
    assert "PURPOSE_PRESETS" in copy_source
    assert "buildPurposePrompt" in copy_source
    assert "CATEGORY_ALIASES" in copy_source
    assert "normalizeCategoryName" in copy_source
    assert "findGalleryByTags" in copy_source
    assert "gallery.contextCopyTagCategory" in gallery_source
    assert "TagCategoryCopy.showMenu" in gallery_source
    assert "_copyPromptCategory" in reader_source
    assert "_renderReaderCategoryTags" in reader_source
    assert "openCategoryBoard" in promptlab_source
    assert "submitCategoryBoard" in promptlab_source
    assert "_renderBuildCategoryWorkbench" in promptlab_source
    assert "_useCheckedBuildCategories" in promptlab_source
    assert "_cleanBuildPrompt" in promptlab_source
    assert "/api/prompts/recategorize" in promptlab_source
    assert "applyTagFiltersFromExternal" in app_source
    assert ".tag-category-copy-menu" in css
    assert ".tag-category-copy-purpose" in css
    assert ".reader-category-tags" in css
    assert ".promptlab-build-category-workbench" in css
    assert ".promptlab-category-board-columns" in css

    for key in [
        "tagCategory.copyOptions",
        "tagCategory.purposePrompts",
        "tagCategory.findSimilarByCategory",
        "tagCategory.trainingCaption",
        "tagCategory.appearance",
        "tagCategory.clothing",
        "tagCategory.pose",
        "tagCategory.scenery",
        "tagCategory.unclassified",
        "promptlab.categoryBoardTitle",
        "promptlab.submitCategoryBoard",
        "promptlab.imagePromptRecipe",
        "promptlab.useCheckedCategories",
        "promptlab.copyTrainingCaption",
        "promptlab.cleanPrompt",
        "reader.categoryTags",
        "gallery.contextCopyTagCategory",
    ]:
        assert key in en_source
        assert key in zh_source


def test_sorting_payloads_carry_v33x_gallery_scope_filters():
    """Regression: Auto-Separate batch-move and Manual Sort session starts must
    send the v3.3.x gallery-scope fields (collection/folder/star-rating/
    exclude-prompts/colors/brightness). Before this fix the serializers and
    API payload builders silently dropped them, so "Copy from Gallery" moved
    or sorted a WIDER set than the gallery displayed."""
    repo_root = Path(__file__).resolve().parents[2]
    # App-family read: app.js is being split into app/*.js (censor/dataset precedent).
    # NOTE(split): count >= 2 sums across the family; BOTH wire builders (batchMove
    # + startSortSession) must keep every scope key - do not DRY them in the split.
    app_source = _app_family_source(repo_root)
    # Autosep-family read: autosep.js is being split into autosep/*.js.
    # NOTE(split): each scope key below recurs across serialize.js
    # (serializeAutoSepFilters), preview.js (signature + query builder), and
    # move-progress.js (the batchMove scope bundle) - do not DRY them.
    autosep_source = _autosep_family_source(repo_root)
    autosep_preview_source = (
        repo_root / "frontend" / "js" / "autosep" / "preview.js"
    ).read_text(encoding="utf-8")
    autosep_move_source = (
        repo_root / "frontend" / "js" / "autosep" / "move-progress.js"
    ).read_text(encoding="utf-8")
    # Manual-sort-family read: manual-sort.js is being split into
    # manual-sort/*.js. NOTE(split): count >= 4 sums across the family
    # (slot-start x2 incl. the minimap preview, bracket, cull) - do not DRY
    # the scope bundle across the split.
    manual_sort_source = _manual_sort_family_source(repo_root)

    # API.batchMove AND API.startSortSession must put every scope field on the
    # wire (snake_case payload keys, hence count >= 2 across the two builders).
    for wire_key in (
        "exclude_prompts:",
        "exclude_colors:",
        "min_user_rating:",
        "brightness_min:",
        "brightness_max:",
        "color_temperature:",
        "brightness_distribution:",
        "collection_id:",
        "scope:",
        "has_metadata:",
    ):
        assert app_source.count(wire_key) >= 2, (
            f"app.js payload builders miss {wire_key}"
        )

    # Auto-Separate's serializer keeps the fields when copying gallery filters,
    # so the saved scope, the preview query, and the executed move all match.
    for field in (
        "excludePrompts:",
        "excludeColors:",
        "minUserRating:",
        "brightnessMin:",
        "brightnessMax:",
        "colorTemperature:",
        "brightnessDistribution:",
        "collectionId:",
        "scope:",
        "hasMetadata:",
    ):
        assert field in autosep_source, f"serializeAutoSepFilters misses {field}"

    assert "scope: 'library'" in autosep_preview_source
    assert "scope: contract.scope," in autosep_move_source

    # Manual Sort routes the same scope bundle through every start path
    # (slot/bracket/cull) and the minimap preview query.
    assert "function buildManualSortScopeFilters" in manual_sort_source
    assert manual_sort_source.count("buildManualSortScopeFilters(f)") >= 4
    assert manual_sort_source.count("scope: contract.scope || 'library',") >= 2


def test_frontend_control_audit_script_reports_inventory():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_frontend_controls.py"

    result = subprocess.run(
        [sys.executable, str(script), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    summary = report["summary"]

    assert summary["source"] == "frontend/index.html"
    assert summary["buttons"] >= 400
    assert summary["total_controls"] >= summary["buttons"]
    for category in (
        "referenced-by-id",
        "referenced-by-data",
        "delegate-only",
        "static-only",
        "needs-runtime-check",
    ):
        assert category in summary["categories"]
    assert "deletion recommendations" in " ".join(report["notes"])


def test_frontend_control_audit_keeps_known_delegated_controls_out_of_static_only_bucket():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_frontend_controls.py"

    result = subprocess.run(
        [sys.executable, str(script), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    controls_by_id = {
        control["id"]: control for control in report["controls"] if control.get("id")
    }

    known_delegated = [
        "reader-tool-tab-reader",
        "reader-tool-tab-obfuscation",
        "dataset-tab-import",
        "dataset-tab-workbench",
        "dataset-tab-export",
        "btn-dataset-queue-grid",
        "btn-dataset-queue-list",
        "btn-filter-vivid",
    ]
    for control_id in known_delegated:
        assert control_id in controls_by_id
        control = controls_by_id[control_id]
        assert control["category"] not in {"static-only", "needs-runtime-check"}, (
            control
        )
        assert control["evidence"], control


def test_dataset_trainer_selector_replaces_legacy_kohya_checkbox_and_loads_once():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    core = (repo_root / "frontend" / "js" / "dataset" / "core.js").read_text(
        encoding="utf-8"
    )
    local_import = (
        repo_root / "frontend" / "js" / "dataset" / "local-import.js"
    ).read_text(encoding="utf-8")
    trainer_selector = (
        repo_root / "frontend" / "js" / "dataset" / "trainer-selector.js"
    ).read_text(encoding="utf-8")

    assert 'id="dataset-trainer-package"' in html
    assert 'data-testid="dataset-trainer-contract-retry"' in html
    assert 'id="dataset-trainer-resolution"' in html
    assert 'data-testid="dataset-bucket-resize-enabled"' in html
    assert 'data-testid="dataset-bucket-resize-subject-aware"' in html
    assert 'id="dataset-trainer-toml"' not in html
    assert core.count("/static/js/dataset/trainer-selector.js") == 1
    assert core.index("/static/js/dataset/output-naming.js") < core.index(
        "/static/js/dataset/trainer-selector.js"
    )
    assert core.index("/static/js/dataset/trainer-selector.js") < core.index(
        "/static/js/dataset/readiness.js"
    )
    assert "this._initTrainerSelector?.();" in core
    assert "this._trainerExportFields()" in local_import
    assert "this._bucketResizeExportSettings()" in local_import
    assert "trainer_resolution" in trainer_selector
    assert "GENERIC_BUCKET_BOUNDS" in trainer_selector


def test_review_cockpit_loads_typed_issue_queue_without_replacing_tag_tools():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    core = (
        repo_root / "frontend" / "js" / "modules" / "separation-console" / "core.js"
    ).read_text(encoding="utf-8")
    issues = (
        repo_root / "frontend" / "js" / "modules" / "separation-console" / "issues.js"
    ).read_text(encoding="utf-8")
    css = (repo_root / "frontend" / "css" / "ui-refresh.css").read_text(
        encoding="utf-8"
    )
    en_source = (repo_root / "frontend" / "js" / "lang" / "en.js").read_text(
        encoding="utf-8"
    )
    zh_source = (
        repo_root / "frontend" / "js" / "lang" / "zh-CN.js"
    ).read_text(encoding="utf-8")

    assert 'data-testid="review-cockpit-tab-issues"' in html
    assert 'data-testid="review-cockpit-filter-sidecar-fallback"' in html
    assert 'id="sepcon-tags-panel"' in html
    assert 'id="sepcon-issue-filter"' in html
    assert html.count("/static/js/modules/separation-console/issues.js") == 1
    assert html.index("/static/js/modules/separation-console/issues.js") < html.index(
        "/static/js/modules/separation-console/boot.js"
    )
    assert "this._initReviewIssues(details);" in core
    assert "'/api/dataset/review-queue'" in issues
    assert "reviewParseResponse" in issues
    assert "schema_version: REVIEW_SCHEMA_VERSION" in issues
    assert "value_en" in issues
    assert "value_zh" in issues
    assert "provider_states must contain each provider exactly once" in issues
    assert "'sidecar_metadata_dependency'" in issues
    assert "sidecar_fallback: ['sidecar_metadata_dependency']" in issues
    assert ".sepcon-issue-rows" in css
    assert ".sepcon-view-tabs" in css
    for key in (
        "dataset.reviewCockpitTitle",
        "dataset.reviewViewsAria",
        "dataset.reviewIssuesTab",
        "dataset.reviewRefreshAria",
        "dataset.reviewPagesAria",
        "dataset.reviewIssueFilter",
        "dataset.reviewIssueSidecarFallback",
        "dataset.reviewIncludeDuplicates",
        "dataset.reviewEmpty",
    ):
        assert key in en_source
        assert key in zh_source


# ``i18n.applyToDOM`` assigns ``el.textContent = t(key)``. Any element that
# carries data-i18n and also holds an inline sprite therefore loses the sprite
# the first time translations are applied. The house pattern keeps the icon as a
# sibling and puts the label in its own span (see #export-title-text).
class _IconInsideI18nScanner(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[dict] = []
        self.offenders: list[tuple[str, int, str]] = []

    def _open(self, tag: str, attrs) -> dict:
        mapping = dict(attrs)
        return {
            "tag": tag,
            "key": mapping.get("data-i18n") or mapping.get("data-i18n-html"),
            "line": self.getpos()[0],
            "id": mapping.get("id", ""),
        }

    def handle_starttag(self, tag, attrs):
        frame = self._open(tag, attrs)
        if tag == "svg":
            for ancestor in self._stack:
                if ancestor["key"]:
                    self.offenders.append(
                        (ancestor["key"], ancestor["line"], ancestor["id"] or ancestor["tag"])
                    )
                    break
        if tag not in self.VOID:
            self._stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.VOID or tag == "svg":
            return
        if self._stack and self._stack[-1]["tag"] == tag:
            self._stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                return


def test_no_data_i18n_element_wraps_a_sprite_icon():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")

    scanner = _IconInsideI18nScanner()
    scanner.feed(html)

    assert scanner.offenders == [], (
        "these elements carry data-i18n and an inline <svg>, so applyToDOM will "
        "delete the icon; move the label into a child span instead: "
        + ", ".join(f"{key} @ line {line} ({where})" for key, line, where in scanner.offenders)
    )


def test_locale_values_carry_no_emoji():
    """Graphite replaced emoji with the sprite sheet; locale values must follow.

    A locale value that still holds an emoji does not merely look dated - it is
    what ``applyToDOM`` writes over the sprite, so the emoji and the icon are the
    same defect. Text-presentation notation the product actually uses (the star
    rating, and the check/cross that mirror the English DO/DON'T rule list) is
    not emoji and stays.
    """
    repo_root = Path(__file__).resolve().parents[2]
    allowed = {"\u2605", "\u2713", "\u2715"}
    emoji_re = re.compile(
        "["
        "\U0001f000-\U0001faff"
        "\u2600-\u27bf"
        "\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55"
        "\u2139\u2194-\u21aa\u2328\u23e9-\u23fa"
        "\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe"
        "\ufe0f"
        "]"
    )
    value_re = re.compile(
        r"""^\s*(?:'[^']+'|"[^"]+")\s*:\s*(?P<value>.+?),?\s*$""", re.MULTILINE
    )

    offenders: list[str] = []
    for name in ("en.js", "zh-CN.js"):
        source = (repo_root / "frontend" / "js" / "lang" / name).read_text(
            encoding="utf-8"
        )
        for line_number, line in enumerate(source.splitlines(), start=1):
            match = value_re.match(line)
            if match is None:
                continue
            found = {
                char
                for char in match.group("value")
                if emoji_re.match(char) and char not in allowed
            }
            if found:
                offenders.append(f"{name}:{line_number} {''.join(sorted(found))}")

    assert offenders == [], "emoji left in locale values: " + "; ".join(offenders)


def test_icon_bearing_headings_are_translated_through_their_label_span():
    """ui-refresh must not write textContent onto a heading that holds a sprite.

    ``_setText`` is a second writer, so retargeting only ``applyToDOM`` is not
    enough - the 120ms/500ms re-applies and the MutationObserver would delete the
    icon again.
    """
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    ui_refresh = (repo_root / "frontend" / "js" / "ui-refresh.js").read_text(
        encoding="utf-8"
    )

    for span_id, key in (
        ("export-title-text", "modal.exportPrompts"),
        ("analytics-title-text", "modal.analytics"),
        ("batch-export-title-text", "batchExport.title"),
    ):
        assert f'id="{span_id}" data-i18n="{key}"' in html
        assert f"this._setText('#{span_id}', '{key}');" in ui_refresh

    # These headings are icon + label-span pairs already, so applyToDOM owns
    # them; a _setText on the heading itself is what erased their icons.
    for selector in (
        "#analytics-modal h3",
        "#batch-export-modal h3",
        "#rename-modal h3",
        "#save-options-modal h3",
        "#view-artist .control-section h3",
        "#view-artist .results-header h3",
        "#view-artist .artist-details h3",
    ):
        assert f"'{selector}'" not in ui_refresh, (
            f"ui-refresh still writes textContent to {selector}, which holds a sprite icon"
        )


def _locale_pack_sources(repo_root: Path) -> dict[str, str]:
    return {
        name: (repo_root / "frontend" / "js" / "lang" / name).read_text(encoding="utf-8")
        for name in ("en.js", "zh-CN.js")
    }


def test_small_tomato_download_note_is_translated_in_both_locales():
    """image-obfuscate.js references the key, so a missing entry ships English to zh.

    Two earlier attempts skipped this because a careless insert trips
    ``test_language_packs_have_unique_matching_keys``; the key still has to
    exist exactly once in each pack.
    """
    repo_root = Path(__file__).resolve().parents[2]
    key = "tools.smallTomatoMetadataDownloadNote"

    obfuscate = (repo_root / "frontend" / "js" / "image-obfuscate.js").read_text(
        encoding="utf-8"
    )
    assert f"'{key}'" in obfuscate

    for name, source in _locale_pack_sources(repo_root).items():
        occurrences = len(re.findall(rf"^\s*'{re.escape(key)}'\s*:", source, re.MULTILINE))
        assert occurrences == 1, f"{name} declares {key} {occurrences} times, expected 1"


def test_image_obfuscate_chrome_carries_no_emoji():
    """Graphite bans emoji in application chrome; the sprite sheet replaces them."""
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend" / "js" / "image-obfuscate.js").read_text(
        encoding="utf-8"
    )
    emoji_re = re.compile(
        "["
        "\U0001f000-\U0001faff"
        "\u2600-\u27bf"
        "\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55"
        "\u2139\u2194-\u21aa\u2328\u23e9-\u23fa"
        "\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe"
        "\ufe0f"
        "]"
    )
    offenders = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(source.splitlines(), start=1)
        if emoji_re.search(line)
    ]
    assert offenders == [], "emoji left in image-obfuscate.js: " + "; ".join(offenders)


def test_text_recovery_button_is_gated_on_missing_text_not_missing_prompt():
    """``missing_prompt`` never reaches zero for non-SD images.

    Gating the button on it leaves a permanently visible control that recovers
    nothing; ``missing_text`` is the counter the job actually moves.
    """
    repo_root = Path(__file__).resolve().parents[2]
    health = (repo_root / "frontend" / "js" / "library-health.js").read_text(
        encoding="utf-8"
    )

    visibility = re.search(
        r"function updateReparseVisibility\(\)\s*\{(?P<body>.*?)\n    \}",
        health,
        re.DOTALL,
    )
    assert visibility is not None
    assert "missing_text" in visibility.group("body")
    assert "missing_prompt" not in visibility.group("body")

    finish = re.search(
        r"function finishReparse\(job\)\s*\{(?P<body>.*?)\n    \}", health, re.DOTALL
    )
    assert finish is not None
    assert "captions_recovered" in finish.group("body"), (
        "a run that recovers thousands of captions must not report them as zero"
    )


def test_text_recovery_control_is_labelled_for_text_not_only_prompts():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    packs = _locale_pack_sources(repo_root)

    assert 'id="metadata-reparse-label" data-i18n="health.reparse">Recover Missing Text<' in html

    english = re.search(r"^\s*'health\.reparse'\s*:\s*'(?P<value>[^']*)'", packs["en.js"], re.MULTILINE)
    assert english is not None
    assert english.group("value") == "Recover Missing Text"

    done = re.search(r"^\s*'health\.reparseDone'\s*:\s*'(?P<value>[^']*)'", packs["en.js"], re.MULTILINE)
    assert done is not None
    assert "{captions}" in done.group("value"), (
        "the completion toast must report recovered sidecar captions, not only prompts"
    )


def test_modal_shows_a_recovered_sidecar_caption_apart_from_the_generation_prompt():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    sections = (repo_root / "frontend" / "js" / "gallery" / "modal-sections.js").read_text(
        encoding="utf-8"
    )

    assert 'id="modal-sidecar-caption-section"' in html
    assert 'id="modal-sidecar-caption-text"' in html
    assert 'data-i18n="modal.sidecarCaption"' in html

    prompt_view = re.search(
        r"_applyModalPromptView\(promptView\)\s*\{(?P<body>.*?)\n    \},",
        sections,
        re.DOTALL,
    )
    assert prompt_view is not None
    assert "_applyModalSidecarCaption" in prompt_view.group("body"), (
        "the recovered caption has to render next to the prompt block"
    )

    renderer = re.search(
        r"_applyModalSidecarCaption\(image\)\s*\{(?P<body>.*?)\n    \},",
        sections,
        re.DOTALL,
    )
    assert renderer is not None
    assert "sidecar_caption" in renderer.group("body")
    assert "modal-sidecar-caption-section" in renderer.group("body")

    for pack_name, source in _locale_pack_sources(repo_root).items():
        for key in ("modal.sidecarCaption", "modal.sidecarCaptionHelp"):
            assert re.search(rf"^\s*'{re.escape(key)}'\s*:", source, re.MULTILINE), (
                f"{pack_name} is missing {key}"
            )


def _artist_family_source(repo_root: Path) -> str:
    artist_dir = repo_root / "frontend" / "js" / "artist"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(artist_dir.glob("*.js"))
    )


def test_artist_ui_consumes_the_tiered_confidence_contract():
    """`artist` is "undefined" unless the tier is high; the UI must read the tier.

    Reading only ``artist`` is exactly the bug that puts the literal string
    "undefined" on screen for the two lower tiers.
    """
    repo_root = Path(__file__).resolve().parents[2]
    family = _artist_family_source(repo_root)

    for field in (
        "confidence_level",
        "candidate_artist",
        "advisory",
        "out_of_vocabulary_likely",
        "low_confidence_artist_counts",
        "confident_count",
        "low_confidence_count",
    ):
        assert field in family, f"no artist module reads {field}"

    assert "describeArtistResult" in family, (
        "the three tiers need one shared formatter so every caller agrees"
    )


def test_single_image_identify_toast_routes_through_the_tier_formatter():
    """The modal button is the other caller of POST /api/artists/identify."""
    repo_root = Path(__file__).resolve().parents[2]
    analysis = (
        repo_root / "frontend" / "js" / "gallery" / "modal-analysis.js"
    ).read_text(encoding="utf-8")

    assert "'/api/artists/identify'" in analysis
    assert "describeArtistResult" in analysis
    assert "|| 'undefined'" not in analysis, (
        "the modal toast must not fall back to the backend's undefined sentinel"
    )


def test_artist_view_offers_a_vocabulary_lookup_and_an_honest_threshold_label():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    family = _artist_family_source(repo_root)
    packs = _locale_pack_sources(repo_root)

    # "Is my artist supported?" — only 37% of this owner's artists exist in the
    # model's vocabulary and nothing in the product said so.
    assert 'id="artist-vocabulary-input"' in html
    assert 'id="btn-artist-vocabulary-check"' in html
    assert 'id="artist-vocabulary-result"' in html
    assert "/api/artists/vocabulary" in family

    # The slider can only tighten now, so the old "below this = undefined"
    # helper describes behaviour the backend no longer has.
    helper = re.search(
        r"^\s*'artist\.belowThreshold'\s*:\s*'(?P<value>[^']*)'", packs["en.js"], re.MULTILINE
    )
    assert helper is not None
    assert "undefined" not in helper.group("value").lower()
    assert "0.20" in helper.group("value"), (
        "the helper must name the confident threshold the backend actually uses"
    )

    # Confident / unconfirmed / no-match are three separate buckets.
    for key in (
        "artist.confidentMatches",
        "artist.unconfirmed",
        "artist.noMatch",
        "artist.vocabularyTitle",
        "artist.vocabularyCheck",
        "artist.candidateBadge",
    ):
        for pack_name, source in packs.items():
            assert re.search(rf"^\s*'{re.escape(key)}'\s*:", source, re.MULTILINE), (
                f"{pack_name} is missing {key}"
            )


def test_artist_copy_no_longer_tells_users_to_lower_the_threshold():
    """Lowering the slider stopped producing names when tiering landed.

    Advice that cannot work is worse than none: it sends the owner back through
    an 80k-image run for a result the backend will still refuse to assert.
    """
    repo_root = Path(__file__).resolve().parents[2]
    packs = _locale_pack_sources(repo_root)
    family = _artist_family_source(repo_root)

    assert "suggestedLow" not in family, (
        "the suggested 0.02-0.08 band was advice for the pre-tiering pipeline"
    )
    assert "Lower the threshold" not in family

    for pack_name, source in packs.items():
        for key in ("artist.startStep2Title", "artist.startStep2Text"):
            match = re.search(
                rf"^\s*'{re.escape(key)}'\s*:\s*'(?P<value>[^']*)'", source, re.MULTILINE
            )
            assert match is not None, f"{pack_name} is missing {key}"
            assert "0.02" not in match.group("value"), (
                f"{pack_name}:{key} still recommends the obsolete low-threshold band"
            )


# Every job whose cancel publishes a non-terminal ``cancelling`` state that only
# its worker can clear, paired with the endpoint that clears it when the worker
# is gone. Tag export is deliberately absent: it has no ``cancelling`` state, so
# the only way it strands is stuck at ``running`` -- exactly what its reset
# refuses with 409 -- and a button that can only ever be refused is not a
# recovery path. See .plans/sd-image-sorter-release/decisions.md.
STUCK_JOB_RESET_ENDPOINTS = (
    "/api/move/reset",
    "/api/batch-move/reset",
    "/api/images/delete-selected/reset",
    "/api/images/remove-selected/reset",
)


def test_every_stuck_job_reset_endpoint_is_reachable_from_the_ui():
    """A reset nothing calls is not a fix — it is a restart with extra steps.

    All four endpoints existed and worked for months while no frontend module
    called any of them, so a stuck job still meant restarting the app. This
    fails if one is orphaned again.
    """
    repo_root = Path(__file__).resolve().parents[2]
    frontend_js = repo_root / "frontend" / "js"
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in _iter_frontend_js_files(frontend_js)
    }

    for endpoint in STUCK_JOB_RESET_ENDPOINTS:
        callers = [
            path.name for path, source in sources.items() if f"'{endpoint}'" in source
        ]
        assert callers, f"no frontend module calls {endpoint}"

    # The recovery control must stay manual. Auto-calling it would hide the real
    # failure and could race a worker that is still finishing — which is the one
    # thing the endpoint's 409 exists to refuse.
    helper = (frontend_js / "app" / "stuck-job-reset.js").read_text(encoding="utf-8")
    assert "addEventListener('click'" in helper, (
        "the reset must be user-initiated, never fired by the poller"
    )
    assert "apiStatus) === 409" in helper, (
        "409 has to be told apart from a real failure, or a correct refusal "
        "renders as a broken feature"
    )


def test_stuck_job_reset_controls_carry_a_hidden_guard():
    """`.btn`-family display rules outrank the UA `[hidden]` rule.

    The reset buttons start hidden and are revealed only after a stall, so a
    missing guard would put them on screen permanently — the same defect that
    kept ``#btn-metadata-reparse`` visible with nothing left to recover.
    """
    repo_root = Path(__file__).resolve().parents[2]
    tokens = (repo_root / "frontend" / "css" / "tokens.css").read_text(encoding="utf-8")
    for selector in (".bg-tag-reset-btn[hidden]", ".btn-reset-operation[hidden]"):
        assert selector in tokens, f"{selector} has no display:none guard"


def test_manual_sort_undo_failure_reports_the_backend_reason():
    """"Failed to undo" alone reads as "undo is broken".

    The service answers with an actionable sentence (which file is in the way
    and what to do about it); the catch used to throw it away.
    """
    repo_root = Path(__file__).resolve().parents[2]
    slot_actions = (
        repo_root / "frontend" / "js" / "manual-sort" / "slot-actions.js"
    ).read_text(encoding="utf-8")
    packs = _locale_pack_sources(repo_root)

    assert "manual.undoFailedWithReason" in slot_actions, (
        "undo must mirror the move/skip paths and pass {reason} through"
    )
    for pack_name, source in packs.items():
        match = re.search(
            r"^\s*'manual\.undoFailedWithReason'\s*:\s*'(?P<value>[^']*)'",
            source,
            re.MULTILINE,
        )
        assert match is not None, f"{pack_name} is missing manual.undoFailedWithReason"
        assert "{reason}" in match.group("value"), (
            f"{pack_name}:manual.undoFailedWithReason drops the reason placeholder"
        )


def test_autosep_error_panel_uses_the_shared_error_formatter():
    """It was the one error surface printing the backend string raw.

    ``services/sorting/batch_move.py`` normalizes these causes (single line,
    basename only, capped at 140 chars) specifically so they survive the
    jargon/length filter in ``modules/utils/errors.js`` intact.
    """
    repo_root = Path(__file__).resolve().parents[2]
    move_progress = (
        repo_root / "frontend" / "js" / "autosep" / "move-progress.js"
    ).read_text(encoding="utf-8")

    formatter = re.search(
        r"function formatAutosepMoveError\(entry\)\s*\{(?P<body>.*?)\n\}",
        move_progress,
        re.DOTALL,
    )
    assert formatter is not None
    assert "formatUserError(error)" in formatter.group("body"), (
        "the Auto-Separate error panel must route through the shared formatter"
    )


def _prompt_lab_stats_source(repo_root: Path) -> str:
    return (repo_root / "frontend" / "js" / "prompt-lab" / "stats.js").read_text(
        encoding="utf-8"
    )


def test_prompt_lab_renders_every_empty_reason_the_backend_can_send():
    """A reason the renderer does not know degrades to "no data yet".

    That is honest but useless, and it happens silently - so the vocabulary the
    two sides share is pinned here rather than left to a rename to break. The
    values come from ``prompt_service.py``'s own constants, so renaming one
    without teaching the renderer fails this test instead of the user's panel.
    """
    repo_root = Path(__file__).resolve().parents[2]
    service = (repo_root / "backend" / "services" / "prompt_service.py").read_text(
        encoding="utf-8"
    )
    stats = _prompt_lab_stats_source(repo_root)

    reasons = re.findall(r'^REASON_[A-Z_]+ = "([a-z_]+)"', service, re.MULTILINE)
    actions = re.findall(r'^ACTION_[A-Z_]+ = "([a-z_]+)"', service, re.MULTILINE)
    assert len(reasons) == 4, f"expected four empty reasons, found {reasons}"
    assert actions == ["scan_generated_images_folder"], actions

    for value in reasons + actions:
        assert f"'{value}'" in stats, f"prompt-lab/stats.js cannot render {value!r}"

    for field in (
        "top_checkpoints_empty_reason",
        "checkpoint_score_leaders_empty_reason",
        "checkpoint_recipes_empty_reason",
        "checkpoint_empty_action",
        "checkpoint_coverage",
    ):
        assert field in stats, f"prompt-lab/stats.js ignores {field}"


def test_prompt_lab_no_longer_advises_importing_prompt_metadata():
    """The old empty-panel line was advice, and for some libraries a dead end.

    "Checkpoint patterns will appear here after you import more prompt metadata"
    cannot succeed when the images never carried metadata to import. The key
    stays in the packs (they are append-only); what must not come back is the
    renderer reaching for it.
    """
    repo_root = Path(__file__).resolve().parents[2]
    stats = _prompt_lab_stats_source(repo_root)
    assert "promptlab.noCheckpointsYet" not in stats, (
        "the empty checkpoint panel must render the backend's reason, not the "
        "old blanket advice"
    )


def test_prompt_lab_empty_reason_strings_exist_with_their_placeholders():
    """Two of the four facts are only useful with the numbers behind them."""
    repo_root = Path(__file__).resolve().parents[2]
    packs = _locale_pack_sources(repo_root)
    required = {
        "promptlab.emptyNoCheckpointMetadata": (),
        "promptlab.emptyCheckpointOnlyMissingFiles": ("{count}",),
        "promptlab.emptyNoScoredImages": (),
        "promptlab.emptyNotEnoughScoredPerCheckpoint": ("{min}", "{scored}"),
        "promptlab.emptyScanGeneratedImages": (),
        "promptlab.noCheckpointDataYet": (),
        "promptlab.avgCaptionLen": (),
        "promptlab.captionFromSidecars": ("{sample}",),
        "promptlab.captionNoneYet": (),
        "promptlab.captionNotTracked": (),
    }
    for pack_name, source in packs.items():
        for key, placeholders in required.items():
            match = re.search(
                rf"^\s*'{re.escape(key)}'\s*:\s*'(?P<value>[^']*)'",
                source,
                re.MULTILINE,
            )
            assert match is not None, f"{pack_name} is missing {key}"
            for placeholder in placeholders:
                assert placeholder in match.group("value"), (
                    f"{pack_name}:{key} drops {placeholder}"
                )


def test_the_caption_statistic_note_is_not_reset_by_the_i18n_pass():
    """``i18n.applyToDOM`` rewrites every ``[data-i18n]`` element's text.

    The caption note is written by ``loadStats`` from the payload, so carrying a
    static key would mean the language pass silently replaced the measured state
    with a fixed sentence - the class of defect the ``i18nLocked`` flag exists
    for. Keeping the attribute off the element is the simpler guarantee.
    """
    repo_root = Path(__file__).resolve().parents[2]
    markup = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"<[^>]*id=\"pl-avg-caption-note\"[^>]*>", markup)
    assert match is not None, "the caption note element is missing"
    assert "data-i18n" not in match.group(0), (
        "pl-avg-caption-note is written from the payload; a data-i18n key would "
        "be re-applied over it on the next language pass"
    )


def test_every_model_card_message_key_is_translated_in_both_locales():
    """A model card's status line is the only place these states are ever said.

    ``model-manager-render.js`` renders ``appT(message_key, message)``, so a key
    the packs do not define silently falls back to the backend's raw English
    string - in the Chinese UI too. The four ``models.tipo.*`` keys shipped that
    way: the card chose between "ready", "unreadable GGUF", "runtime packages
    missing" and "not downloaded yet", and every one of them printed English.
    """
    repo_root = Path(__file__).resolve().parents[2]
    inventory = (
        repo_root / "backend" / "services" / "model_service_inventory.py"
    ).read_text(encoding="utf-8")
    packs = _locale_pack_sources(repo_root)
    pack_keys = {
        name: set(_language_pack_keys(source)) for name, source in packs.items()
    }

    emitted = sorted(set(re.findall(r'"(models\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+)"', inventory)))
    assert emitted, "no model card message keys found - has the inventory moved?"

    missing = {
        name: sorted(key for key in emitted if key not in keys)
        for name, keys in pack_keys.items()
    }
    assert not any(missing.values()), f"untranslated model card message keys: {missing}"


def test_tipo_card_states_keep_the_lists_the_backend_passes():
    """The broken and missing-runtime states are only useful with their lists.

    ``message_params`` carries the joined broken variants and missing packages;
    a translation that drops the placeholder turns "delete these two files"
    into an instruction with nothing to act on.
    """
    repo_root = Path(__file__).resolve().parents[2]
    packs = _locale_pack_sources(repo_root)
    required = {
        "models.tipo.broken": "{variants}",
        "models.tipo.missingDeps": "{deps}",
    }
    for pack_name, source in packs.items():
        for key, placeholder in required.items():
            match = re.search(
                rf"^\s*'{re.escape(key)}'\s*:\s*'(?P<value>[^']*)'",
                source,
                re.MULTILINE,
            )
            assert match is not None, f"{pack_name} is missing {key}"
            assert placeholder in match.group("value"), (
                f"{pack_name}:{key} drops the {placeholder} the backend passes"
            )
