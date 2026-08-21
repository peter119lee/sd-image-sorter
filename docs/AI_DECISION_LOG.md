# AI Decision Log

**Updated:** 2026-05-19
**Purpose:** Preserve deliberate local decisions so future AI agents do not silently undo them.

## How To Use This File

1. Read this before changing UX wording, workflow behavior, data semantics, save behavior, path handling, or major defaults.
2. Add a new entry when a decision is intentional and another AI might otherwise "correct" it later.
3. Do not rewrite old entries just to make the file look clean.
4. If a decision changes, add a new entry and mark the older one as superseded.

## What Belongs Here

This file is for durable repo-local decisions that future AI is likely to misread or undo without help.

Good candidates:

- non-obvious defaults
- save / overwrite / move / copy semantics
- selection / filter / scope semantics
- path or data invariants
- product-specific UX rules that differ from generic convention
- local product assumptions that materially affect implementation choices

## What Does Not Belong Here

Do not use this file for:

- ordinary bug fixes with no lasting semantic rule
- temporary workarounds
- one-off release chores
- raw TODO lists
- current test counts
- speculative ideas not yet reflected in current behavior
- generic advice like "write cleaner code" or "improve performance"

If something is only a suspected future problem, it belongs in `docs/TECHNICAL_DEBT_NOTES.md`, not here.

## Admission Bar

Before adding an ADR here, be able to answer all of these:

1. What exact local behavior or rule is being preserved?
2. Why is another AI likely to "fix" it incorrectly later?
3. What evidence supports it?
4. What kind of future changes are still allowed?

If you cannot answer those questions clearly, do not add the ADR yet.

## Entry Format

Use this structure for future entries:

### ADR-AI-YYYYMMDD-XX: Short Title
- Status: active / superseded / deprecated
- Area: frontend UX / backend workflow / data model / architecture / release / etc.
- Evidence tier: `explicit user instruction` / `Tier 1` / `Tier 1 + Tier 2`
- Decision:
- Why:
- Do not "improve" this by:
- Allowed evolution:
- Evidence:
- Last verified:
- Related files:
- Supersedes:
- Validation:

## Current Records

### ADR-AI-20260521-01: Auto-Separate is a 3-pane workbench, not a vertical form
- Status: active
- Area: frontend UX / sorting workflow / release polish
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The Auto-Separate page (`#view-autosep`) renders as a left/center/right workbench with the Caption Editor design language. The left pane holds Saved Configs, the scope status card, and the active filter summary. The center pane is the preview grid (visual focus, gets the most width). The right pane holds Destination, the Move/Copy radio, the safety checkboxes, and the big Run CTA. The shell caps to viewport so the Run CTA stays visible at the bottom of the action pane on common desktop and laptop sizes. At 1080-1280px the action pane drops to a sticky bottom bar across the full shell width. Below 760px the layout stacks single-column. Every locked element ID from the previous vertical form is preserved verbatim so the existing `autosep.js` (saved configs CRUD, scope intent buttons, preview, execute, polling, settings modal) keeps working without changes. The 3 scope intent buttons (use saved / copy from gallery / resync from gallery) all stay reachable inline; the visual simplification did not collapse them into a single ambiguous control.
- Why:
  The previous single-column form was a long scroll: Saved Configs, then Filter Criteria, then Destination, then Preview, then File Action, then Run. On 1080p screens the user could not see the preview list and the Run CTA at the same time. New users misread the page as a tutorial-style sequential form, while power users had to scroll past the saved configs strip every time. Putting filter setup, preview, and action side by side makes the relationship obvious — pick filters and a destination, watch the preview update, then click Run — and matches the visual language of the same-release Caption Editor workbench.
- Do not "improve" this by:
  Collapsing the 3 scope intent buttons into one button, hiding the saved configs in a modal-only entry, removing the inline filter summary so users must click Edit Filters to see the current scope, dropping the preview pane back to a small inline strip, defaulting Move instead of Copy (Principle #11 still applies), or removing the `confirmBeforeMove=true` checkbox.
- Allowed evolution:
  Add chip-based inline filter editing, a scope-intent dropdown menu (still showing all 3 intents), debounced live-preview on filter changes, a sticky preview thumbnail when scrolling the filter pane, drag-drop reorder of saved configs, or per-image-exclude on the preview thumbnails — provided the 3-pane layout, copy-by-default, scope-card semantics, locked element IDs, and the underlying `/api/batch-move` async progress contract remain intact.
- Evidence:
  `frontend/index.html` `#view-autosep` is a `.autosep-shell` with three `.autosep-pane` regions (filter / preview / action). `frontend/css/vlm.css` defines the grid layout, the responsive 2-column + sticky-bottom-bar fallback for 1080-1280px, and the single-column stack below 760px. The cards inside the filter pane use `flex-shrink: 0` so the flex column does not collapse them under `overflow: hidden`. `frontend/js/autosep.js` is unchanged in this commit because every element ID it touches (`autosep-destination`, `btn-execute-autosep`, `autosep-preview-list`, `autosep-config-select`, `btn-autosep-use-gallery-scope`, `btn-autosep-resync-scope`, `btn-autosep-keep-scope`, `autosep-summary-*`, etc.) was preserved verbatim. `scripts/screenshot-autosep-workbench.js` is the regression helper that asserts 39/39 locked IDs are present, copy is the default radio, `confirmBeforeMove` is checked by default, and 12/12 functional smoke checks pass.
- Last verified:
  2026-05-21 with backend on :8502 + Playwright helper at viewports 1440x900, 1366x768, 1080x720, and 720x900: `PASS: all locked IDs present, copy default + confirm default contracts hold.` Element-level screenshots of the configs card and the filter pane confirm card-head + summary text render correctly.
- Related files:
  `frontend/index.html`
  `frontend/css/vlm.css`
  `frontend/js/autosep.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `scripts/screenshot-autosep-workbench.js`
  `docs/AI_PRINCIPLES.md` (Principle #11 — Auto-Separate default operation = copy)
- Supersedes:
  The earlier vertical-form layout where Saved Configs / Filter Criteria / Destination / Preview / File Action / Run stacked top-to-bottom in a single panel.
- Validation:
  `node --check frontend/js/autosep.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js scripts/screenshot-autosep-workbench.js`; `node scripts/screenshot-autosep-workbench.js` against a backend running on `127.0.0.1:8502` (PASS); element-level screenshots in `.tmp/screenshots/c35-autosep-workbench/` for visual review.

### ADR-AI-20260521-02: Caption Editor opens in a dedicated full-screen modal
- Status: active
- Area: frontend UX / export workflow / release polish
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The same-name `.txt` / LoRA caption export's 3-column workbench (image queue / current caption editor / shared-tag toolbox) can be opened in a dedicated near-fullscreen modal via a new "Open Editor" button next to "🔄 Refresh" in the batch-export preview header. The modal hosts the same workbench markup and shares the same `editedCaptions`, blacklist, and image-override state as the inline preview. While the editor is open, the inline preview pane shows a hint that edits live in the popup. Closing the editor returns the workbench to the inline pane with all temporary edits preserved.
- Why:
  The inline export preview is great for last-minute settings polish, but it lives inside the already-busy batch-export modal, which leaves only ~600px wide for the 3-column workbench. Power users editing LoRA training captions for dozens of images need real horizontal real estate for the textarea and the shared-tag panel. Forcing them to use the inline view turns caption authoring into a constant scroll battle. A dedicated full-screen window solves the space problem without forking the workbench logic.
- Do not "improve" this by:
  Moving caption authoring to a separate route (it must remain part of the export flow), removing the inline workbench (some users only need a quick glance), forking `_renderPreviewWorkbench` into two implementations, or letting the modal lose `editedCaptions` on close.
- Allowed evolution:
  Multi-select editing across rows, autocomplete from a tag dictionary, per-row translation columns, paging beyond the first preview chunk, keyboard shortcuts (Ctrl+Enter to apply current edit and move to next image), or an even larger "exclusive editor" route — provided the queue/current/shared-tag workbench model and the shared edited-caption state are kept intact.
- Evidence:
  `frontend/index.html` adds the `caption-editor-modal` shell + topbar + body with id `caption-editor-list`, and adds the "Open Editor" button to the batch-export preview header. `frontend/js/v321-ui.js` adds `openCaptionEditor()` / `closeCaptionEditor()` and routes `_renderPreviewWorkbench()` and `refreshPreview()` to either the inline or the modal container based on whether the modal is open. `frontend/css/vlm.css` adds `caption-editor-shell` / `caption-editor-topbar` / `caption-editor-body` / `caption-editor-list` styles plus the `.export-preview-workbench--full` modifier that lets the workbench fill the modal height. `scripts/screenshot-caption-editor.js` asserts the 3-pane workbench renders inside both the inline pane and the modal at 1440x900 and 1366x768.
- Last verified:
  2026-05-21 with backend on :8501 + Playwright helper. Both inline and fullscreen modes render queue/editor/tools panels: `PASS: 3-column workbench rendered in both inline and fullscreen modes.`
- Related files:
  `frontend/index.html`
  `frontend/js/v321-ui.js`
  `frontend/css/vlm.css`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `scripts/screenshot-caption-editor.js`
- Supersedes:
  Builds on ADR-AI-20260519-06 (the workbench design itself remains canonical); does not supersede it. This ADR only adds the dedicated full-screen entry point.
- Validation:
  `node --check frontend/js/v321-ui.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js scripts/screenshot-caption-editor.js`; `node scripts/screenshot-caption-editor.js` against a backend on `127.0.0.1:8501` (PASS).

### ADR-AI-20260519-07: VLM setup actions save first and expose live API debugging
- Status: active
- Area: frontend UX / backend workflow / debugging / release polish
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  VLM Settings actions that depend on provider configuration, especially "Fetch Available Models" and "Test Connection", must first save the current form values and then run the requested action. Users must not need to discover the separate footer Save button before those actions work. VLM runs launched from the Tagger Natural Language tab must show visible progress with success count, failed count, token count, current image, and API response state (`waiting`, `responded`, `error`, `cancelling`, `done`). The optional "API Chat" debug view may show recent sanitized request/response events in a chat-like timeline, including system/user prompt, tags, model, image filename, response text, errors, tokens, and latency, but must not expose API keys, service account content, image base64, or raw binary payloads.
- Why:
  The previous VLM settings flow silently used stale saved settings when users clicked Fetch/Test after editing the form, which made a normal workflow look broken. The v3.2.1 Tagger UI also hid VLM progress inside a hidden parent panel, so users could not tell whether the backend was working, waiting on the API, or failing. The debug chat is intentionally user-facing because seeing the exact prompt/response loop is useful for LoRA trainers tuning VLM prompts, but it must stay safe and compact.
- Do not "improve" this by:
  Requiring users to click Save before Fetch/Test, showing only a generic spinner without success/error/API response counts, hiding final VLM run summaries when the run finishes, removing the API Chat entry because it looks "developer-only", or logging/displaying API keys, service-account JSON, data URLs, base64 image content, or full oversized raw payloads.
- Allowed evolution:
  Add retry-by-message, copy prompt/response, token-cost estimates, per-image transcript download, provider-specific timing, richer filtering, or a persistent debug history if the default UI remains compact and all secrets/image bytes stay redacted.
- Evidence:
  `frontend/js/vlm-caption.js` collects current settings and saves them before `/api/vlm/models` and `/api/vlm/test`, immediately polls VLM batch progress, keeps the Tagger VLM progress panel visible for running/error/summary states, and renders the API Chat modal from sanitized debug events. `backend/routers/vlm.py` exposes VLM batch API status counters and `/api/vlm/caption-batch/debug-chat`, records sanitized request/response/error events, and never includes API keys or base64 payloads in debug events. Regression coverage asserts auto-save request ordering, visible success/error/API status progress, and sanitized API Chat content.
- Last verified:
  2026-05-19 with source-server Playwright interaction, backend router tests, and desktop/mobile visual checks.
- Related files:
  `backend/routers/vlm.py`
  `backend/tests/test_routers/test_vlm.py`
  `frontend/index.html`
  `frontend/css/vlm.css`
  `frontend/js/vlm-caption.js`
  `frontend/js/v321-ui.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `tests/e2e/task4_tagger_tabs.spec.js`
- Supersedes:
  The earlier v3.2.1 VLM settings/progress behavior where Fetch/Test used only previously saved settings and VLM progress could be hidden by the Natural Language tab layout.
- Validation:
  `node --check frontend/js/vlm-caption.js frontend/js/v321-ui.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js tests/e2e/task4_tagger_tabs.spec.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_routers/test_vlm.py backend/tests/test_frontend_contract.py::test_v321_modules_read_runtime_selection_store_from_window_app -s`; `BASE_URL=http://127.0.0.1:8504 node tests/e2e/task4_tagger_tabs.spec.js`; desktop screenshots `/tmp/vlm-progress-desktop.png` and `/tmp/vlm-debug-chat-desktop.png`; mobile screenshots `/tmp/vlm-progress-mobile.png` and `/tmp/vlm-debug-chat-mobile.png`.

### ADR-AI-20260519-06: Same-name export preview is a caption workbench, not a passive preview list
- Status: active
- Area: frontend UX / export workflow / release polish
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The Same-name `.txt` export "Live preview" is a dataset caption workbench. It uses a left image queue, a central current-image caption editor, and a right shared-tag/check/cleanup tool panel, following the workflow model of BooruDatasetTagManager: select an image, edit its caption/tags, inspect tags shared across the preview set, check whether captions are empty/duplicate/blacklist-hit heavy, and apply add/remove/cleanup operations to the current caption or all preview captions before export. Preview edits are temporary export data, not automatic saves; nothing is written to source images or the database until the user explicitly exports, copies, or downloads.
- Why:
  A plain preview list does not help users fix LoRA training captions before writing sidecars. Users need to remove boilerplate, add trigger/helper tags, and quickly normalize a batch of similar images while seeing which image each caption belongs to. BooruDatasetTagManager is useful because it treats caption review as an editor with queue/current/common-tag panels, not as a static text dump.
- Do not "improve" this by:
  Reverting the preview to a simple row list or one textarea, hiding per-image edited state, making tag tools depend only on visible button text, limiting the queue to an arbitrary tiny first-five sample when the preview API can render more, implying edits auto-save to the DB/source files, or letting edited preview captions apply only to clipboard/download while sidecar export ignores them.
- Allowed evolution:
  Add multi-select editing, autocomplete from a tag dictionary, translation columns, tag confidence display, paging beyond the first preview chunk, keyboard shortcuts, or richer exact/contains blacklist modes if the queue/current/shared-tag workbench model and export override semantics remain intact.
- Evidence:
  `frontend/js/v321-ui.js` renders the queue/current editor/shared-tag/check/cleanup workbench, previews up to 20 selected images, tracks edited captions, exposes stable tool button hooks, and injects/collects caption overrides for export. `frontend/css/vlm.css` keeps the tool buttons compact enough for desktop and mobile without text overflow. `frontend/js/app.js` passes `image_overrides` through the batch export API helper. `backend/services/tag_export_service.py` writes image overrides verbatim. Playwright coverage checks the workbench panels, current edit, cleanup tools, add-all edit, clipboard path, and blacklist payload alignment.
- Last verified:
  2026-05-19 with source-server Playwright interaction and desktop/mobile screenshot checks.
- Related files:
  `frontend/js/v321-ui.js`
  `frontend/css/vlm.css`
  `frontend/js/app.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `backend/services/tag_export_service.py`
  `backend/tests/test_routers/test_tags.py`
  `tests/e2e/task7_export_unified.spec.js`
- Supersedes:
  The earlier v3.2.1 live-preview implementation that behaved like a passive preview list.
- Validation:
  `node --check frontend/js/v321-ui.js frontend/js/app.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js tests/e2e/task7_export_unified.spec.js`; `BASE_URL=http://127.0.0.1:8504 node tests/e2e/task7_export_unified.spec.js`; `cd backend && python3 -m pytest tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_uses_image_overrides_for_preview_edits tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_caption_blacklist_filters_final_caption_tokens tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_template_uses_root_blacklist -q -s`; desktop screenshot `/tmp/sd-preview-workbench-shots/desktop-caption-workbench-v4.png`; mobile screenshot `/tmp/sd-preview-workbench-shots/mobile-caption-workbench-v4.png`.

### ADR-AI-20260519-05: Export blacklist filters final training captions, not only stored tag rows
- Status: active
- Area: backend workflow / export semantics / frontend UX
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The Same-name `.txt` export blacklist is a final training-caption filter. For training-caption modes (`caption_tags`, `caption_merged`, `nl_caption`, `prompt_nl`) it must remove matching comma-separated tokens from AI captions, original prompts, and stored tags before writing sidecars. For template mode, the same modal-level blacklist must also filter preset defaults and template variables such as `{quality}`, `{safety}`, `{rating}`, `{count}`, `{trigger}`, `{nl_caption}`, `{prompt}`, `{negative}`, `{tags}`, `{tags:filtered}`, and `{append}`. The live preview, clipboard/download combined output, and real sidecar writer must share the same blacklist semantics.
- Why:
  Users use the export blacklist to remove training-caption boilerplate such as `newest`, `highres`, `normal quality`, `score_5`, `safe`, and `1girl`. Those strings can come from preset defaults, prompts, AI captions, rating/count variables, or tag rows. Filtering only stored tag rows makes preview/export misleading and leaves unwanted LoRA captions even when the UI says the blacklist was applied.
- Do not "improve" this by:
  Treating blacklist as tag-table-only cleanup, applying it only in the preview path, applying it only inside template options while ignoring the root `blacklist` payload field, letting preset defaults bypass the filter, or changing Anima preset defaults just to hide the bug. The defaults may remain, but blacklist must be authoritative when the user supplies it.
- Allowed evolution:
  Richer token matching, per-mode blacklist help text, regex mode, or exact/contains switches can be added if they remain explicit and keep preview, clipboard/download, and real sidecar export aligned.
- Evidence:
  `backend/services/tag_export_service.py` filters final caption/prompt tokens for non-template training-caption modes and merges the root request blacklist into template options. `backend/services/export_template_engine.py` filters preset defaults and template variables. `frontend/js/v321-ui.js` passes blacklist into preview and combined output. `frontend/js/app.js` passes template options explicitly for real template sidecar export.
- Last verified:
  2026-05-19 with targeted backend tests, fresh v3.2.1 package API export checks, and Playwright export modal checks.
- Related files:
  `backend/services/tag_export_service.py`
  `backend/services/export_template_engine.py`
  `backend/tests/test_export_template_engine.py`
  `backend/tests/test_routers/test_tags.py`
  `frontend/js/app.js`
  `frontend/js/v321-ui.js`
  `tests/e2e/task7_export_unified.spec.js`
- Supersedes:
  The earlier implicit behavior where the blacklist filtered only tag rows for some export paths.
- Validation:
  `cd backend && python3 -m pytest tests/test_export_template_engine.py tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_can_write_prompt_tag_caption_sidecars tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_caption_sidecars_normalize_multiline_parts_to_one_line tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_caption_blacklist_filters_final_caption_tokens tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_template_uses_root_blacklist tests/test_cache_bust.py -q -s`; rebuilt `sd-image-sorter-v3.2.1-linux.tar.gz`, launched a fresh package on port 8502, inserted five fixture images, verified `/api/tags/export-preview`, `caption_merged` sidecar export, and `template`/Anima sidecar export all removed `newest`, `highres`, `normal quality`, `score_5`, `safe`, and `1girl`; `BASE_URL=http://127.0.0.1:8502 node tests/e2e/task7_export_unified.spec.js`.

### ADR-AI-20260519-04: Tagger modal uses task-specific cards instead of layered selects and duplicate action strips
- Status: active
- Area: frontend UX / release polish
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The AI Auto Tagging modal is a task switcher, not a raw model dropdown. The top-level tabs remain Local Tagger, Natural Language, and Aesthetic Score, but each tab must show only the controls needed for that task. Natural Language uses two in-app source cards for ToriiGate and VLM API; the native `#tag-model-select` stays hidden as the canonical integration value for existing JS/tests, not as a visible user control. VLM must not show a legacy banner plus a separate utility strip plus a footer start button at the same time. Idle progress bars must stay hidden until a real run starts.
- Why:
  Fresh package QA and user review found the previous v3.2.1 Tagger UI confusing: an OS-styled select menu broke the dark modal visually, and Natural Language exposed multiple overlapping concepts at once (tabs, source toggle, model dropdown, VLM banner, VLM utility strip, and footer action). That made the release look unfinished and user-unfriendly even when the backend path worked.
- Do not "improve" this by:
  Re-exposing the native model select in the main Tagger workflow, adding another visible VLM/ToriiGate selector beside the source cards, showing VLM Settings/Caption in both an inline strip and the modal footer, or letting `data-tagger-shows` reveal idle progress containers on tab switch.
- Allowed evolution:
  The card layout, icons, copy, and model metadata can be refined. A future command palette or advanced model drawer is fine if the primary modal still keeps one clear task path and one primary action per active source.
- Evidence:
  `frontend/js/v321-ui.js` renders visible source/model cards while keeping `#tag-model-select` hidden and synchronized. `frontend/css/vlm.css` hides the native select and styles task cards. `frontend/js/ui-refresh.js` delegates Tagger dynamic copy back to v3.2.1 integration so language refresh does not reset Natural Language labels. Playwright task checks assert that duplicate VLM strips, idle progress bars, and local import/export actions stay hidden on the Natural Language tab.
- Last verified:
  2026-05-19 with source-server Playwright screenshots and fresh v3.2.1 package Playwright screenshots.
- Related files:
  `frontend/index.html`
  `frontend/css/vlm.css`
  `frontend/js/v321-ui.js`
  `frontend/js/ui-refresh.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `tests/e2e/task4_tagger_tabs.spec.js`
  `tests/e2e/round2_real_api.spec.js`
- Supersedes:
  The earlier implicit v3.2.1 Tagger layout that exposed a native select and duplicate VLM action surfaces.
- Validation:
  `node --check frontend/js/v321-ui.js frontend/js/ui-refresh.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js tests/e2e/task4_tagger_tabs.spec.js tests/e2e/round2_real_api.spec.js`; `BASE_URL=http://127.0.0.1:8496 node tests/e2e/task4_tagger_tabs.spec.js`; `BASE_URL=http://127.0.0.1:8496 node tests/e2e/task5_setup_routing.spec.js`; rebuilt `sd-image-sorter-v3.2.1-linux.tar.gz`, launched a fresh package on port 8497, and captured `/tmp/sd-v321-fresh-package-design-qa/01-fresh-nl-toriigate.png` plus `/tmp/sd-v321-fresh-package-design-qa/02-fresh-nl-vlm.png`.

### ADR-AI-20260519-03: Successful moves must survive stale missing-file gallery reads
- Status: active
- Area: backend workflow / path handling / data model
- Evidence tier: Tier 1
- Decision:
  Moving an indexed image to a new path is authoritative once the file operation succeeds. If a concurrent Gallery read or thumbnail refresh observes the old path as missing, it must not leave the row unreadable when the current DB row now points to an existing moved file. `update_image_path()` clears stale missing-file unreadable state, and Gallery missing-file filtering re-reads the current row before marking an image missing.
- Why:
  Auto-Separate and batch move run in the background while the frontend may still refresh thumbnails or Gallery rows from a pre-move snapshot. Without this guard, a real moved file can disappear from normal Gallery queries because the stale old path gets persisted as `File not found on disk`.
- Do not "improve" this by:
  Marking missing files from a stale list snapshot without rechecking the current row, or changing `update_image_path()` back to a path-only update that preserves `not found` read errors after a successful move.
- Allowed evolution:
  A stronger shared file-mutation transaction or event model is fine if it preserves the invariant that successful move/copy state cannot be poisoned by stale old-path reads.
- Evidence:
  `backend/database.py::update_image_path` restores rows with missing/not-found read errors after a successful path update. `backend/services/image_service.py::_filter_and_mark_missing_images` re-reads the current row before marking a listed image unreadable.
- Last verified:
  2026-05-19 with targeted backend tests and the Auto-Separate Playwright regression.
- Related files:
  `backend/database.py`
  `backend/services/image_service.py`
  `backend/tests/test_database.py`
  `backend/tests/test_routers/test_images.py`
  `tests/e2e/specs/manual-regression.spec.ts`
- Supersedes:
  None
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests/test_database.py -k "update_image_path" -q`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests/test_routers/test_images.py -k "moved_row_missing or skips_missing_files" -q`; `cd tests/e2e && PW_REUSE_SERVER=0 PW_WEB_SERVER_PORT=19333 npx playwright test specs/manual-regression.spec.ts:1075 --project=chromium --workers=1`.

### ADR-AI-20260519-02: Release packages must ship version-specific release notes
- Status: active
- Area: release packaging / user-facing docs
- Evidence tier: Tier 1
- Decision:
  Public release archives must include `release-notes.md` copied from `docs/RELEASE_NOTES_v<version>.md` during packaging. The build should fail if that version-specific file is missing. `README.md` download and extraction examples must reference the current `APP_VERSION`, not an older artifact name.
- Why:
  Fresh-package QA found the v3.2.1 Linux tarball still shipping a root `release-notes.md` for v3.2.0, and README examples that pointed users at v3.2.0 package names. That directly misleads real users during the exact "download a fresh release package" path.
- Do not "improve" this by:
  Copying a stale root `release-notes.md` into packages, hand-editing package staging output after build, or allowing README examples to drift behind `backend/app_info.py`.
- Allowed evolution:
  The root `release-notes.md` can be removed or generated from the same source, but packages must continue to get version-specific notes and tests must guard that behavior.
- Evidence:
  `scripts/build_release_packages.py::write_release_notes` copies the matching version file into every package staging directory. `backend/tests/test_release_build.py` verifies README package names and the release-notes copy behavior.
- Last verified:
  2026-05-19 with release build tests, package integrity validation, and fresh tarball inspection.
- Related files:
  `scripts/build_release_packages.py`
  `backend/tests/test_release_build.py`
  `README.md`
  `docs/RELEASE_NOTES_v3.2.1.md`
- Supersedes:
  The previous implicit behavior where the root `release-notes.md` was copied as-is.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_release_build.py`; `python3 scripts/lazy_release_qa.py --artifact-root artifacts/release --version 3.2.1 --skip-server`.

### ADR-AI-20260519-01: Prompt filters keep exact mode by default and expose contains mode
- Status: active
- Area: filter semantics / frontend UX / sorting workflow
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Prompt-term filters support two user-selectable match modes. `exact` remains the default and matches normalized prompt tokens, preserving the old guard against accidental substring matches such as `hero` matching `superhero`. `contains` is opt-in and matches substring text inside the normalized full prompt, so a filter like `takamatsu_tomori` includes free-form variants such as `takamatsu_tomori(bang dream!)` and `takamatsu_tomori(bang dream!!!!!its mygo)`.
- Why:
  SD prompt writing is free-form. Character or subject tags are often annotated with parentheses, franchise names, or comments. Exact-only prompt filtering split those variants into separate piles during Auto-Separate and Manual Sort. But switching everything to substring matching would reintroduce overmatching bugs already guarded by exact post-filter tests.
- Do not "improve" this by:
  Making contains the silent default, removing exact post-filtering, treating prompt search mode as the same thing as free-text search, or wiring the mode only in Gallery while leaving Auto-Separate / Manual Sort / selection tokens on exact semantics.
- Allowed evolution:
  Add richer modes such as token-prefix, regex, or per-term modes if they remain explicit in the UI and carry through Gallery, filtered selection, export previews, Auto-Separate, and Manual Sort.
- Evidence:
  Backend query helpers accept `prompt_match_mode` / `promptMatchMode`; Gallery filter state persists `promptMatchMode`; Auto-Separate and Manual Sort copy the same filter contract and pass it to backend batch/sort calls. Regression tests cover exact default behavior, contains-mode parenthesized variants, selection token chunks, export preview paging, and Manual Sort forwarding.
- Last verified:
  2026-05-19 with targeted backend/router tests and frontend JS syntax checks.
- Related files:
  `backend/database.py`
  `backend/routers/images.py`
  `backend/routers/sorting.py`
  `backend/services/image_service.py`
  `backend/services/sorting_service.py`
  `backend/services/tag_export_service.py`
  `frontend/index.html`
  `frontend/js/app.js`
  `frontend/js/autosep.js`
  `frontend/js/manual-sort.js`
  `frontend/js/stores/filter-store.js`
  `frontend/js/modules/utils/filters.js`
- Supersedes:
  None
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_database.py -k "prompt_filter_exact_mode or prompt_filter_contains_mode"`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_routers/test_images.py -k "prompt_contains_mode or token_prompt_contains or respects_prompt_contains_mode"`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_routers/test_sorting.py -k "prompt_match_mode"`; `node --check frontend/js/app.js frontend/js/autosep.js frontend/js/manual-sort.js frontend/js/stores/filter-store.js frontend/js/modules/utils/filters.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`.

### ADR-AI-20260515-03: Gallery header stays single-row on 1920px desktop
- Status: active
- Area: frontend UX / responsive layout
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  On wide desktop viewports such as `1920x1080`, keep the Gallery header controls in a single row without overlap, text clipping, or page-level horizontal overflow. Frequent controls such as image count, Random, generator tabs, sort, and view mode should remain readable. Lower-frequency header actions such as Find Missing, Clear Current Library, and the contextual Guide may become icon-only buttons with preserved `title`/`aria-label`.
- Why:
  With the Gallery sidebar open, a `1920x1080` viewport leaves about 1520px for the Gallery header. Letting `.gallery-header-left` or `.generator-tabs` wrap makes the header look like two bars and wastes vertical workspace. The user explicitly asked for the Gallery bar to be one line at `1920x1080`.
- Do not "improve" this by:
  Re-enabling wrapping for the wide-desktop Gallery header, letting generator tabs dictate the full header width, letting controls overlap, or allowing fixed action buttons such as `Random` to shrink until their text clips.
- Allowed evolution:
  Add an overflow menu, shorter generator labels, a richer tab scroller, or different icon choices if it preserves the one-row wide-desktop header and passes overlap/overflow checks in English and zh-CN.
- Evidence:
  Current CSS gives `.gallery-container` and `.gallery-header` `min-width: 0`, keeps the `min-width: 1800px` Gallery header row non-wrapping, prevents fixed left-side buttons from shrinking, makes lower-frequency header actions icon-only in that wide-row mode, and lets `.generator-tabs` take the remaining space without covering the right-side controls.
- Last verified:
  2026-05-15 with Playwright DOM/screenshot checks at `1920x1080` in English and zh-CN.
- Related files:
  `frontend/css/ui-refresh.css`
  `frontend/js/guide.js`
- Supersedes:
  None
- Validation:
  Playwright local render check on `http://127.0.0.1:18487` confirmed no failures for single-row height, page horizontal scroll, generator/view/Guide overlap, controls outside viewport, non-tabs text overflow, and console errors at `1920x1080` in English and zh-CN. Static checks: `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `node --check frontend/js/guide.js`; `git diff --check -- frontend/css/ui-refresh.css frontend/js/guide.js docs/AI_DECISION_LOG.md`.

### ADR-AI-20260515-02: Contextual action buttons may use compact scope labels when the panel already names the action
- Status: active
- Area: frontend UX / bilingual layout
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Keep the Censor filter apply buttons inside the Filters panel as compact scope labels: `Current`, `Selected`, `All` in English and `当前`, `已选`, `全部` in zh-CN. The surrounding Filters panel and button styling already provide the action context, so each small grid button does not need to repeat "Apply to".
- Why:
  In the three-column Censor side panel, longer bilingual labels such as `Apply to Current` / `应用到当前图片` can wrap and make one row of buttons taller than neighboring controls on common desktop sizes such as `1366x768` and `1280x720`. Compact contextual labels keep the button group aligned without hiding controls or creating uneven heights.
- Do not "improve" this by:
  Expanding these three labels back to full imperative phrases just for explicitness unless the control is moved out of the Filters panel or the layout is redesigned to give each action enough width.
- Allowed evolution:
  Add tooltips, aria labels, or a wider/stacked responsive layout if more explicit wording is needed, as long as English and zh-CN remain visually stable across common desktop and mobile viewports.
- Evidence:
  `#view-censor .censor-filter-apply-group` is a three-column action group inside the Filters card. Current CSS constrains `.censor-apply-filters-btn` and its text span to one line with ellipsis, while i18n uses compact scope labels.
- Last verified:
  2026-05-15 with Playwright layout audit at `1920x1080`, `1600x900`, `1536x864`, `1440x900`, `1366x768`, `1280x720`, and `390x844` in English and zh-CN.
- Related files:
  `frontend/css/censor-v2.css`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
- Supersedes:
  None
- Validation:
  `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `git diff --check -- frontend/js/lang/en.js frontend/js/lang/zh-CN.js frontend/css/censor-v2.css`; Playwright common-resolution button audit on local server `http://127.0.0.1:18487`.

### ADR-AI-20260515-01: Aesthetic scoring belongs with AI tagging controls, not the Gallery toolbar
- Status: active
- Area: frontend UX / bilingual layout
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Keep the low-frequency `Score Aesthetic` action and its unavailable/running/ready status inside the AI Auto Tagging modal as a compact AI utility strip. The Gallery header should keep high-frequency browsing controls such as filters, random image, missing-file recovery, generator tabs, sorting, view modes, and clear-library actions, but should not permanently reserve toolbar space for aesthetic scoring.
- Why:
  Aesthetic scoring is a heavy local-AI preparation/scoring workflow, not a frequent Gallery browsing action. Leaving it in the Gallery top bar made the header noisy, consumed horizontal space on 1366px and smaller desktop layouts, and worsened bilingual button pressure.
- Do not "improve" this by:
  Moving `Score Aesthetic`, `Stop`, or `Aesthetic unavailable` back into the Gallery header just because aesthetic score is a Gallery sort/filter dimension.
- Allowed evolution:
  Add an AI tools popover, model manager shortcut, or clearer readiness guidance if it stays progressively disclosed and does not crowd the Gallery work surface.
- Evidence:
  Current markup places `#btn-score-aesthetic`, `#btn-cancel-aesthetic`, and `#aesthetic-status-chip` under `#tag-modal .tagger-utility-strip`, while the Gallery header no longer contains those controls. Bilingual CSS keeps the strip compact on desktop and mobile.
- Last verified:
  2026-05-15 with Playwright layout audit at `1920x1080`, `1600x900`, `1536x864`, `1440x900`, `1366x768`, `1280x720`, and `390x844` in English and zh-CN.
- Related files:
  `frontend/index.html`
  `frontend/js/app.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `frontend/css/ui-refresh.css`
- Supersedes:
  None
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `git diff --check -- frontend/index.html frontend/js/app.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js frontend/css/ui-refresh.css`; Playwright common-resolution button audit on local server `http://127.0.0.1:18487`.

### ADR-AI-20260514-01: Tagger hardware clamps should preserve real throughput on capable machines
- Status: active
- Area: tagging workflow / hardware safety / performance
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  WD14 ONNX tagger recommendations should use aggressive-but-bounded true batching on capable hardware: high-VRAM GPUs may recommend chunks above 32, heavy WD14-family models should not be capped at 12 on 16-24 GB cards, and high-RAM CPU fallback should not be stuck at tiny queue chunks. Runtime clamps, adaptive GPU backoff, memory-pressure shrink, session refresh, worker isolation, custom-model conservatism, and ToriiGate chunk 1 remain the safety contract.
- Why:
  The product explicitly targets huge libraries, including ~200,000-image collections. The user reported that tagger crashes were primarily from RAM/VRAM not being released after long runs, not from chunk 12 vs larger chunks. Keeping recommended chunks too small wastes capable hardware and can turn normal large-library tagging into multi-hour waiting while the existing adaptive safeguards still protect real OOM conditions.
- Do not "improve" this by:
  Reverting high-end WD14 GPU recommendations back to 12/16/32, treating automatic clamps as a reason to make every run slow, raising custom ONNX defaults without proof, or increasing ToriiGate/VLM chunk size without a separate VLM-specific design.
- Allowed evolution:
  Add smarter per-model benchmarking, learned stable chunk persistence, VRAM-reservation-aware scheduling, explicit "max throughput" presets, or more granular GPU/vendor tables if they keep low-headroom machines protected and keep large-library workflows fast.
- Evidence:
  `hardware_monitor.recommend_tagger_config()` now recommends larger GPU/CPU chunks by hardware capacity while still downshifting when free VRAM is actually tight. `TaggingService` allows WD14 true batches up to 64, while custom ONNX remains capped at 8 and ToriiGate remains fixed at 1. Frontend chunk choices expose 48 and 64 so backend recommendations are selectable.
- Last verified:
  2026-05-14 against current workspace tests for hardware recommendation and tagging runtime planning.
- Related files:
  `backend/hardware_monitor.py`
  `backend/services/tagging_service.py`
  `frontend/js/app.js`
  `frontend/index.html`
  `backend/tests/test_hardware_monitor.py`
  `backend/tests/test_tagging_service.py`
- Supersedes:
  None; refines ADR-AI-20260427-12 and ADR-AI-20260501-02.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_hardware_monitor.py`; targeted runtime-plan tests in `backend/tests/test_tagging_service.py`.

### ADR-AI-20260501-01: Sidebar action panels should not create nested scrollbars when the sidebar can own scrolling
- Status: active
- Area: frontend UX / layout
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Keep sidebar action panels in one obvious scroll flow. In Gallery, the filter sidebar owns overflow and the selection action panel must not add its own scrollbar. In Censor/Edit, the left queue sidebar should keep the fixed queue controls visible and let the queue grid own the only internal list scrollbar.
- Why:
  Nested scrollbars inside small sidebar action panels look broken, waste space, and make users fight the UI even when there is enough room in the parent panel. The queue grid is the exception because it is a real unbounded item list under fixed controls.
- Do not "improve" this by:
  Adding `max-height` plus `overflow-y: auto` to Gallery selection panels, or making both a Censor sidebar and its queue grid scroll at the same time.
- Allowed evolution:
  Redesign sidebars into drawers, accordions, or virtualized lists if the scroll owner remains explicit and the UI avoids competing scrollbars.
- Evidence:
  Current CSS keeps `.selection-panel` visible in the Gallery sidebar flow and keeps `#view-censor .censor-sidebar-v2.left` from scrolling while `.censor-queue-grid` remains the queue list scroller.
- Last verified:
  2026-05-01 against current workspace CSS and user report about unnecessary left-bar scrollbars after opening selection controls.
- Related files:
  `frontend/css/ui-refresh.css`
  `frontend/css/censor-v2.css`
  `frontend/index.html`
- Supersedes:
  None
- Validation:
  CSS diff inspection and `git diff --check`.

### ADR-AI-20260501-02: Heavy local AI work must be resource-gated without blanket slowdowns
- Status: active
- Area: backend runtime / stability / performance
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Route heavyweight local model loading and inference through a shared AI runtime guard, keep memory-heavy algorithms chunked or bounded, and use targeted CPU fallback only when GPU memory is unsafe or after a real GPU OOM. Do not globally slow every workflow just to avoid crashes.
- Why:
  Aesthetic scoring, WD14 tagging, CLIP similarity, YOLO/SAM censoring, ToriiGate, and artist identification can all load large ONNX/Torch/Transformers runtimes. Letting them run concurrently or build unbounded in-memory batches can exhaust RAM/VRAM and make the whole computer freeze. The user explicitly reported aesthetic scoring crashing the computer and called out historical tagger crashes, while also requiring speed to remain important.
- Do not "improve" this by:
  Removing the shared runtime guard because individual features pass tests, restoring whole-batch image preprocessing, restoring full-library embedding matrices for similarity search, disabling GPU by default for all users, or serializing cheap non-AI work behind the heavyweight guard.
- Allowed evolution:
  Replace the coarse guard with a smarter scheduler, priority queue, per-device VRAM reservation, model residency manager, or ANN-backed similarity index if it preserves the same crash-safety contract and keeps fast paths fast.
- Evidence:
  `backend/ai_runtime_guard.py` now owns the shared process/file lock, CUDA headroom check, OOM detection, and cleanup helpers. Aesthetic scoring does GPU headroom checks plus CPU retry after CUDA OOM. Tagger preprocessing streams by runtime chunks. Similarity search scans embeddings in DB chunks with bounded top-k. Censor save operations enforce edit/mask/pixel budgets and crop cached masks to affected boxes. Heavy model paths in tagger, aesthetic, similarity, censor, NudeNet, SAM3, ToriiGate, artist identification, and model-health metadata probing use the guard.
- Last verified:
  2026-05-01 against current workspace code and CI.
- Related files:
  `backend/ai_runtime_guard.py`
  `backend/aesthetic.py`
  `backend/tagger.py`
  `backend/services/tagging_service.py`
  `backend/similarity.py`
  `backend/services/similarity_service.py`
  `backend/censor.py`
  `backend/services/censor_service.py`
  `backend/nudenet_detector.py`
  `backend/model_health.py`
  `backend/sam3_refiner.py`
  `backend/toriigate_tagger.py`
  `backend/artist_identifier.py`
  `backend/tests/test_resource_safety.py`
- Supersedes:
  None
- Validation:
  `python3 -m pytest -s tests/test_resource_safety.py tests/test_tagger.py`; `python3 -m pytest -s tests/test_routers/test_prompts_censor_similarity_artists.py tests/test_tagging_service.py tests/test_hardware_monitor.py`; `python3 scripts/run_ci.py`.

### ADR-AI-20260427-01: Copy mode is a first-class workflow, not a redundant duplicate of move
- Status: active
- Area: sorting workflow / file lifecycle
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  Keep copy as an explicit first-class operation in sorting flows instead of treating move as the only "real" operation.
- Why:
  Some users need to sort into other folders without disturbing originals or their date-based library workflow. Removing or downplaying copy would push the product toward technically simpler behavior that is worse for real usage.
- Do not "improve" this by:
  Removing copy mode, collapsing copy into move, or treating copy as an unnecessary extra because move looks cleaner.
- Allowed evolution:
  Safer defaults, clearer wording, better explanation of when copy should be used, and better post-action refresh behavior.
- Evidence:
  Current sorting flows expose explicit move vs copy behavior; current user feedback explicitly asked for copy support because move-only handling was insufficient; sorting regression fixes and tests now cover this workflow.
- Last verified:
  2026-04-27 against current workspace behavior and sorting regression coverage.
- Related files:
  `backend/services/sorting_service.py`
  `frontend/js/manual-sort.js`
  `frontend/js/autosep.js`
  `backend/tests/test_routers/test_sorting.py`
- Supersedes:
  None
- Validation:
  Covered by sorting tests and recent copy/move regression fixes.

### ADR-AI-20260427-02: Derived state validity depends on pixel content, not container metadata churn
- Status: active
- Area: backend data lifecycle
- Evidence tier: Tier 1
- Decision:
  Use metadata-independent pixel fingerprints (`content_fingerprint`) to decide whether tags, embeddings, AI captions, aesthetic scores, and artist predictions should be preserved or cleared.
- Why:
  Metadata-only rewrites should not destroy expensive derived data, while true pixel changes must invalidate stale derived state. Using file timestamps or container rewrites alone is too blunt and causes real regressions.
- Do not "improve" this by:
  Clearing derived data on every rescan/save/reparse just because file metadata changed, or by treating all same-path rewrites as equivalent.
- Allowed evolution:
  Centralize the policy further, improve docs, add more invariant tests, or improve fingerprinting implementation.
- Evidence:
  Current code now uses `content_fingerprint`; recent regression fixes corrected both over-clearing and under-clearing derived data when files were rewritten.
- Last verified:
  2026-04-27 against current workspace code and regression coverage.
- Related files:
  `backend/database.py`
  `backend/image_manager.py`
  `backend/image_fingerprint.py`
  `backend/services/image_service.py`
  `backend/services/censor_service.py`
- Supersedes:
  Any older implicit "mtime/size alone decides staleness" behavior.
- Validation:
  Database, image manager, images, tags, sorting, and prompts/censor/similarity/artists regression suites.

### ADR-AI-20260427-03: Saving over an indexed file must reconcile the library row immediately
- Status: active
- Area: save / overwrite workflow
- Evidence tier: Tier 1
- Decision:
  If a save target path already belongs to an indexed library item, refresh that indexed row immediately after save.
- Why:
  Otherwise the UI can keep stale metadata or stale derived state even though the file on disk changed. This rule is workflow-critical and must not depend on the feature author remembering it ad hoc.
- Do not "improve" this by:
  Assuming a later scan will clean things up, or by limiting refresh logic to one feature such as Reader only.
- Allowed evolution:
  Centralize overwrite reconciliation into a shared save helper used by all features.
- Evidence:
  The same bug family had to be fixed in more than one feature path, and current code now explicitly reconciles indexed rows after overwrite saves.
- Last verified:
  2026-04-27 against current workspace code and save-flow regression coverage.
- Related files:
  `backend/services/image_service.py`
  `backend/services/censor_service.py`
  `backend/image_manager.py`
  `backend/database.py`
- Supersedes:
  Feature-local assumptions that overwrite save does not need immediate library reconciliation.
- Validation:
  Reader save tests plus Censor Edit overwrite regression tests.

### ADR-AI-20260427-04: "Select Visible" wording is intentional because the behavior is visible-scope, not global-scope
- Status: active
- Area: gallery UX semantics
- Evidence tier: Tier 1
- Decision:
  The gallery bulk-selection button should describe visible-scope behavior, not broad "select all" behavior.
- Why:
  The implementation operates on visible rendered gallery items. Labeling that action as "Select All" misleads users and future contributors about the true scope.
- Do not "improve" this by:
  Renaming it to "Select All" unless the actual selection semantics are changed to match.
- Allowed evolution:
  Make scope even clearer, document visible vs filtered vs full-library selection, or redesign the selection model explicitly.
- Evidence:
  A real UI text regression changed the label while the behavior remained visible-only; current code and markup still reflect visible-scope selection.
- Last verified:
  2026-04-27 against current workspace UI text and selection logic.
- Related files:
  `frontend/index.html`
  `frontend/js/gallery.js`
  `frontend/js/app.js`
  `frontend/js/ui-refresh.js`
- Supersedes:
  The accidental label drift introduced by UI refresh text binding.
- Validation:
  Manual verification plus code inspection of visible-scope selection logic.

### ADR-AI-20260427-19: Gallery selection scope must stay explicit when visible-scope and loaded-scope actions differ
- Status: active
- Area: gallery UX semantics / frontend state
- Evidence tier: Tier 1
- Decision:
  Keep gallery selection scope explicit in the sidebar: visible-scope buttons stay labeled as visible actions, and shift-range selection is treated as loaded-result scope rather than silently pretending it means the whole filtered result set.
- Why:
  The gallery already mixes two real scopes today: DOM-visible bulk actions and loaded-result range selection. If the UI hides that distinction, future pagination or virtualization work will quietly reintroduce wrong-scope bugs.
- Do not "improve" this by:
  Collapsing everything back into generic "selected" wording, renaming visible-scope actions to global-scope wording, or pretending shift-range covers the full filtered result list when it only covers loaded rows.
- Allowed evolution:
  Add a true filtered/all-matching selection mode later, but only if the UI and state contract continue to say exactly which scope each action uses.
- Evidence:
  Current code now routes gallery selection changes through `SelectionStore`, the sidebar shows explicit scope copy, and range selection still operates on `AppState.images` while the visible-action buttons operate on rendered gallery items.
- Last verified:
  2026-04-27 against current workspace selection logic, sidebar copy, and E2E coverage.
- Related files:
  `frontend/js/stores/selection-store.js`
  `frontend/js/gallery.js`
  `frontend/js/app.js`
  `frontend/index.html`
- Supersedes:
  None
- Validation:
  Gallery selection E2E coverage plus manual code inspection of visible vs loaded scope paths.

### ADR-AI-20260427-20: True filtered selection must resolve from the backend result set, not the loaded thumbnail subset
- Status: active
- Area: gallery UX semantics / frontend-backend contract
- Evidence tier: Tier 1
- Decision:
  When Gallery selection scope is `filtered`, the frontend must resolve the ID set through `POST /api/images/selection-ids` and treat it as a distinct contract from `visible` DOM selection or `loaded` shift-range selection.
- Why:
  The loaded gallery page is only a slice of the filtered result set. If filtered selection is inferred from `AppState.images` or pruned against the currently loaded page on every refresh, batch actions silently target the wrong files.
- Do not "improve" this by:
  Pretending filtered selection can be reconstructed from loaded thumbnails, pruning filtered selections against the current page after every reload, or renaming visible-scope actions to broad "select all" wording.
- Allowed evolution:
  Recompute filtered selection automatically when filters change, add a future whole-library scope, or optimize the backend ID-resolution path, as long as filtered selection remains an explicit backend-resolved contract.
- Evidence:
  Current code now exposes `POST /api/images/selection-ids`, `SelectionStore` carries a filtered-selection `filterKey`, the sidebar exposes dedicated "Select All Filtered" and visible-scope actions, and same-filter reloads no longer silently drop off-page IDs.
- Last verified:
  2026-04-28 against current workspace code, targeted backend router tests, and local Playwright browser runs; WSL fallback now prefers a local POSIX Python before a Windows `python.exe`, and missing Chromium shared libraries can be bootstrapped from repo-local runtime packages.
- Related files:
  `backend/routers/images.py`
  `backend/services/image_service.py`
  `backend/tests/test_routers/test_images.py`
  `frontend/js/stores/selection-store.js`
  `frontend/js/app.js`
  `frontend/js/gallery.js`
  `frontend/index.html`
  `tests/e2e/playwright.config.ts`
  `tests/e2e/scripts/run-playwright.mjs`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  None
- Validation:
  Backend router tests for `/api/images/selection-ids` pass; local Playwright browser runs now pass for `should load the main page` and `filtered selection should resolve all matching ids and survive same-filter reloads`.

### ADR-AI-20260428-21: Image time semantics are split, but `created_at` remains a compatibility alias
- Status: active
- Area: data model / sorting semantics
- Evidence tier: Tier 1
- Decision:
  `images.library_order_time` is now the stable gallery ordering key, `images.source_file_mtime` tracks the current file modification time, and `created_at` remains only as a deprecated compatibility alias that mirrors `library_order_time`.
- Why:
  The old single-field model mixed stable library chronology with mutable file time. That made rescans, copies, and future timeline/sort work too easy to misread. The split preserves existing gallery chronology while finally giving file-time semantics their own field.
- Do not "improve" this by:
  Reusing `source_file_mtime` as the default gallery sort key, treating `created_at` like real file creation time again, or dropping the compatibility alias before all callers are migrated.
- Allowed evolution:
  Move more code and docs off `created_at`, eventually remove the alias in a later compatibility-breaking pass, and add explicit UI wording for file-time-based views if the product needs them.
- Evidence:
  The schema now includes `library_order_time` and `source_file_mtime`; default image ordering and cursor pagination now use `library_order_time`; scan/rescan/copy writes preserve `library_order_time` while updating `source_file_mtime`; router/database/image-manager regressions cover the split.
- Last verified:
  2026-04-28 against current workspace code and targeted backend regression coverage.
- Related files:
  `backend/migrations/004_image_time_semantics.py`
  `backend/migrations/_schema_common.py`
  `backend/database.py`
  `backend/image_manager.py`
  `backend/routers/images.py`
  `backend/tests/test_database.py`
  `backend/tests/test_image_manager.py`
  `backend/tests/test_routers/test_images.py`
  `backend/tests/test_routers/test_sorting.py`
- Supersedes:
  The older implicit assumption recorded in debt notes that `created_at` still carried mixed live semantics.
- Validation:
  `backend/tests/test_database.py`, `backend/tests/test_image_manager.py`, `backend/tests/test_routers/test_images.py`, and `backend/tests/test_routers/test_sorting.py` pass with the new split semantics.

### ADR-AI-20260427-05: Truthful UI and runtime reporting is a product rule
- Status: active
- Area: UX semantics / runtime reporting
- Evidence tier: Tier 1
- Decision:
  User-facing status, warnings, and capability descriptions should report real behavior, real limitations, and actual runtime state, even when that is less flattering than a smoother-looking generic message.
- Why:
  Release history repeatedly fixed misleading UX: Reader clipboard metadata safety, tagger runtime fallback visibility, model capability wording, progress detail for skipped/unreadable/failed states, and JPG/WebP metadata honesty. This is not random polish; it is a recurring product stance.
- Do not "improve" this by:
  Hiding fallback state, overclaiming metadata preservation, implying unsupported model capabilities, or collapsing detailed failure states into vague "done/failed" language.
- Allowed evolution:
  Better wording, cleaner presentation, and stronger docs are welcome as long as they stay honest about actual behavior.
- Evidence:
  Current release notes, changelog entries, and code paths repeatedly encode truthful-runtime and truthful-limitation messaging across Reader, Tagger, Similarity, and Censor flows.
- Last verified:
  2026-04-27 against current docs, code, and existing release history.
- Related files:
  `frontend/js/image-reader.js`
  `frontend/js/app.js`
  `frontend/js/censor-edit.js`
  `backend/model_health.py`
  `backend/routers/models.py`
  `backend/routers/images.py`
  `backend/routers/tags.py`
- Supersedes:
  Any "looks successful enough" presentation that hides real limitations or runtime fallback.
- Validation:
  Release notes, progress docs, and multiple regression/E2E validations around truthful runtime/status reporting.

### ADR-AI-20260427-06: Reader clipboard import is intentionally treated as a lossy browser path
- Status: active
- Area: Reader workflow / metadata semantics
- Evidence tier: Tier 1
- Decision:
  Original-file access paths remain the metadata-safe Reader flow. Clipboard/browser paths are intentionally treated as potentially lossy and must warn accordingly.
- Why:
  Browser clipboard handling does not guarantee preservation of SD PNG metadata. Past versions looked like successful parses even when metadata had already been dropped, which misled users.
- Do not "improve" this by:
  Pretending clipboard import is equivalent to original-file loading, suppressing metadata-loss warnings, or reintroducing API/UI wording that implies metadata safety where the browser cannot guarantee it.
- Allowed evolution:
  Better warnings, better follow-up save behavior, or clearer distinction between source-file and browser-upload paths.
- Evidence:
  Current Reader UI, release notes, and architecture docs all distinguish original-file paths from lossy browser clipboard/import paths.
- Last verified:
  2026-04-27 against current Reader code and cited release/docs history.
- Related files:
  `frontend/js/image-reader.js`
  `backend/routers/images.py`
  `backend/services/image_service.py`
  `README.md`
- Supersedes:
  Older UI semantics that made clipboard import look metadata-safe.
- Validation:
  Browser validation and release review notes for Reader clipboard behavior.

### ADR-AI-20260427-07: Unreadable images are quarantined from normal workflows
- Status: active
- Area: library health / workflow gating
- Evidence tier: Tier 1
- Decision:
  Corrupt, truncated, missing, or otherwise unreadable images should be marked as such and excluded from normal scan/sort/tag/similarity flows by default.
- Why:
  Letting broken rows keep participating as if they were healthy creates cascading bugs, fake successes, stale embeddings, and user confusion.
- Do not "improve" this by:
  Silently letting unreadable files flow through normal operations just because the path still exists, or by reusing stale derived data from previously readable states.
- Allowed evolution:
  Better visibility, repair actions, rescan tools, or richer diagnostics for unreadable rows.
- Evidence:
  Current code, release notes, and regression coverage all treat unreadable-image quarantine as deliberate product behavior.
- Last verified:
  2026-04-27 against current workspace code and referenced release/test material.
- Related files:
  `backend/image_manager.py`
  `backend/database.py`
  `backend/services/sorting_service.py`
  `backend/services/tagging_service.py`
  `backend/similarity.py`
- Supersedes:
  Older behavior where existence-on-disk was treated as enough for continued participation.
- Validation:
  Scan, sorting, similarity, and image-manager regression coverage.

### ADR-AI-20260427-08: Large-library workflows must not be weakened by convenience limits
- Status: active
- Area: scale / performance / product direction
- Evidence tier: Tier 1 + Tier 2
- Decision:
  Safety and performance changes must preserve viability for very large SD libraries. Limits should be scoped to one risky request or one risky operation, not used as a shortcut to reduce product capability.
- Why:
  Historical release design explicitly targets power users with 1TB / 100k-image libraries. The repo repeatedly rejected "small arbitrary limits" and "fake fixes" that only make demos look cleaner.
- Do not "improve" this by:
  Quietly capping library usage, skipping metadata extraction to look faster, inflating progress with inaccurate totals, or replacing precomputed fast paths with slow brute-force fallbacks.
- Allowed evolution:
  Streaming, paging, bounded work, early library usability, cache improvements, and scoped safety guards that do not weaken real workflows.
- Evidence:
  Current scan and similarity direction in code/release notes aligns with hardening-spec language that explicitly protects large-library workflows.
- Last verified:
  2026-04-27 against current workspace behavior, release history, and hardening design notes.
- Related files:
  `backend/image_manager.py`
  `backend/services/sorting_service.py`
  `backend/services/similarity_service.py`
  `backend/routers/images.py`
  `frontend/js/app.js`
- Supersedes:
  Any future temptation to solve performance by shrinking supported workflow scale.
- Validation:
  Release-hardening design rules, scan validation, and large-library oriented release notes.

### ADR-AI-20260427-09: Long scans should become usable earlier instead of blocking on total completeness
- Status: active
- Area: scan/import UX
- Evidence tier: Tier 1
- Decision:
  For long scans, the product direction is to expose a usable library earlier and continue remaining metadata work in the background, with clear user-facing status.
- Why:
  Users with large libraries care more about "the app is usable and clearly still working" than about waiting for every last metadata field before anything appears.
- Do not "improve" this by:
  Reverting to all-or-nothing scan blocking, or by faking completeness while hidden work continues without explanation.
- Allowed evolution:
  Better background progress, more honest staging, faster placeholder import, and safer metadata backfill.
- Evidence:
  Current release notes and scan-progress work explicitly frame early library usability and background continuation as intended behavior.
- Last verified:
  2026-04-27 against current release notes, progress docs, and scan code direction.
- Related files:
  `backend/image_manager.py`
  `frontend/js/app.js`
  `backend/services/sorting_service.py`
- Supersedes:
  Older scan behavior that made users wait too long before anything felt available.
- Validation:
  v3.1.0 scan/browser validation and regression notes.

### ADR-AI-20260427-10: Service-layer architecture is intentional; do not dump new business rules back into routers
- Status: active
- Area: backend architecture
- Evidence tier: Tier 1
- Decision:
  The backend's service-layer direction is intentional. Routers should stay as API/domain entry points, while substantial workflow logic belongs in services or shared backend modules.
- Why:
  The project explicitly moved toward service-layer architecture in v2.0.0 and current architecture docs still describe services as the business-logic layer. Reversing that direction makes cross-feature consistency and testing worse.
- Do not "improve" this by:
  Shoving new workflow logic directly into routers just because it is faster in one patch, unless the change is truly trivial glue code.
- Allowed evolution:
  Extract more shared logic out of routers, improve service boundaries, and document invariants more clearly.
- Evidence:
  Current backend structure and historical release docs consistently describe routers as thin entry points over a service layer.
- Last verified:
  2026-04-27 against current repo structure and architecture docs.
- Related files:
  `backend/services/`
  `backend/routers/`
  `docs/architecture.md`
- Supersedes:
  None
- Validation:
  Current repo architecture and historical release documentation.

### ADR-AI-20260427-11: Local-first / local-only is a real product assumption
- Status: active
- Area: product shape / security context / UX expectations
- Evidence tier: Tier 1
- Decision:
  Treat this app as a local-first, local-only tool unless the user explicitly changes product direction.
- Why:
  The README, API docs, and security docs all frame the product around "your images stay on your machine" and local execution without accounts or cloud upload. Many current tradeoffs, warnings, and security assumptions depend on that product shape.
- Do not "improve" this by:
  Sneaking in cloud-centric assumptions, account/login requirements, remote dependency for core workflows, or security/UX changes that only make sense if the app is network-exposed by default.
- Allowed evolution:
  Better documentation, optional remote integrations clearly marked as optional, and explicit product decisions if the app ever grows beyond local-only use.
- Evidence:
  Current product docs and security/architecture docs all treat local-only operation as the default product contract.
- Last verified:
  2026-04-27 against current README, API docs, and security architecture docs.
- Related files:
  `README.md`
  `docs/API.md`
  `docs/SECURITY_ARCHITECTURE.md`
  `backend/main.py`
- Supersedes:
  None
- Validation:
  Current product documentation and architecture/security assumptions.

### ADR-AI-20260427-12: Automatic hardware clamps are the real tagger GPU semantics
- Status: active
- Area: tagging workflow / hardware safety / runtime UX
- Evidence tier: Tier 1
- Decision:
  GPU tagging runs should start directly under automatic hardware safety limits. A separate "are you sure you want GPU?" confirmation step is not part of the intended product behavior.
- Why:
  The repo explicitly removed the old confirmation-modal semantics because safety is now enforced by runtime VRAM/RAM clamps, session refresh, and truthful runtime reporting. Reintroducing a launch confirm would make the UX less truthful without adding real protection.
- Do not "improve" this by:
  Bringing back a GPU start confirmation modal, defaulting `allow_unsafe_acceleration` to true, or hiding target-vs-actual runtime fallback state.
- Allowed evolution:
  Better hardware summaries, clearer runtime fallback wording, more telemetry, and better model-specific guidance.
- Evidence:
  Current Tagger code defaults unsafe acceleration off; release notes and smoke tests explicitly confirm there is no separate GPU confirm modal.
- Last verified:
  2026-04-27 against current tagger code, release notes, and smoke/E2E coverage.
- Related files:
  `frontend/js/app.js`
  `backend/routers/tags.py`
  `backend/services/tagging_service.py`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  Older launch-time GPU confirmation semantics.
- Validation:
  Current smoke/E2E assertions verify GPU start paths do not show the confirm modal and keep unsafe acceleration disabled.

### ADR-AI-20260427-13: Auto-Separate and Manual Sort keep explicit saved scopes; Gallery is not a live binding
- Status: active
- Area: sorting workflow / scope semantics
- Evidence tier: Tier 1 + Tier 2
- Decision:
  Auto-Separate and Manual Sort maintain their own saved filter scopes. Gallery filters may be copied in on first use or explicit resync, but later Gallery changes must not silently retarget these workflows.
- Why:
  These tools can move or copy large sets of files. Silent scope drift after the user has already configured a workflow is dangerous and breaks predictability. The current UI deliberately surfaces saved scope, sync time, mismatch, and resync actions.
- Do not "improve" this by:
  Making these workflows live-bind to Gallery filters, silently overwriting saved scope on each open, or hiding the saved-vs-live mismatch state because it looks simpler.
- Allowed evolution:
  Clearer scope badges, better previews, named scope presets, or better sync controls.
- Evidence:
  Current UI and JS state logic implement saved-vs-resynced scope behavior, and the hardening design explicitly preserves isolated task filter scopes.
- Last verified:
  2026-04-27 against current UI code, scope-state logic, and referenced design/test notes.
- Related files:
  `frontend/index.html`
  `frontend/js/autosep.js`
  `frontend/js/manual-sort.js`
  `tests/e2e/specs/manual-regression.spec.ts`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  Any older assumption that sorting sub-tools should always mirror the current Gallery filters automatically.
- Validation:
  Current UI copy, scope-status logic, and regression/E2E coverage around saved scope and explicit resync behavior.

### ADR-AI-20260427-14: Destructive file actions should confirm before execution; backend conflicts are the fallback, not the primary UX
- Status: active
- Area: destructive workflow safety / overwrite UX
- Evidence tier: Tier 1 + Tier 2
- Decision:
  When the client can predict a destructive or side-effecting action in advance, it should confirm first with the user. Backend `409` / validation responses remain the safety net, not the main confirmation flow.
- Why:
  This repo repeatedly fixed "fail first, then ask" or "just do it" behavior around moving files and overwriting outputs. Predictable destructive actions should disclose action mode, target path, and relevant scope before side effects begin.
- Do not "improve" this by:
  Sending the destructive request first and using the first backend conflict as pseudo-confirmation, auto-running move/copy workflows without confirmation, or hiding target/scope details to keep the modal shorter.
- Allowed evolution:
  Richer previews, better confirmation wording, per-item overwrite choices, or clearer dry-run summaries.
- Evidence:
  Current Reader, Manual Sort, and Auto-Separate flows confirm before destructive actions; release work and tests explicitly checked this.
- Last verified:
  2026-04-27 against current UI code, Reader live tests, sorting regressions, and release-progress notes.
- Related files:
  `frontend/js/manual-sort.js`
  `frontend/js/autosep.js`
  `frontend/js/image-reader.js`
  `tests/e2e/specs/manual-regression.spec.ts`
  `tests/e2e/specs/reader-live.spec.ts`
- Supersedes:
  Older fail-then-confirm UX around same-path overwrite, plus any assumption that file-moving workflows can start without an explicit preflight confirmation.
- Validation:
  Reader live tests assert overwrite confirmation appears before any `409`, and sorting regressions assert move/copy execution shows confirmation modals first.

### ADR-AI-20260427-15: UI should stay desktop-first, compact, bilingual, and feature-retentive
- Status: active
- Area: frontend UX / layout policy / feature surface
- Evidence tier: explicit user instruction
- Decision:
  Optimize the UI primarily for desktop/laptop usage in English and `zh-CN`, keep functional controls more prominent than explanatory text, use progressive disclosure for secondary settings/help, and do not remove existing features without explicit approval.
- Why:
  This product is a computer tool for real workflows, not a mobile-first marketing page. Users need dense but understandable controls, strong bilingual layout behavior, and retained capability. A cleaner-looking UI that hides or deletes power features is the wrong direction for this repo.
- Do not "improve" this by:
  Rebuilding screens around mobile-first assumptions, letting long English labels break layouts, replacing usable controls with oversized explanation blocks, or removing existing features just to simplify a screen.
- Allowed evolution:
  Better grouping, better iconography, more compact dialogs, clearer advanced/basic separation, and safer relocation of secondary controls.
- Evidence:
  Explicit user instruction in the current task on 2026-04-27.
- Last verified:
  2026-04-27 from current task instructions and aligned principle updates.
- Related files:
  `frontend/index.html`
  `frontend/css/styles.css`
  `frontend/css/censor-v2.css`
  `frontend/js/app.js`
  `frontend/js/censor-edit.js`
  `frontend/js/manual-sort.js`
  `frontend/js/autosep.js`
- Supersedes:
  None
- Validation:
  Future UI work should be checked against desktop small-screen behavior, bilingual layout stability, icon accessibility, and feature retention.

### ADR-AI-20260427-16: In-app updates must never manage package-local user data or updater runtime state
- Status: active
- Area: release / updater safety / package-local data
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The in-app updater may replace only release-managed application files. It must never overwrite or delete `data/` or updater runtime folders such as `update/downloads`, `update/logs`, `update/state`, `update/worker`, and `update/backups`.
- Why:
  This product ships as a self-contained package that users keep in one folder. Their database, favorites, downloaded models, caches, thumbnails, and update working state all live inside that package. One-click updates are only acceptable if they preserve that state by design, not by luck.
- Do not "improve" this by:
  Turning the updater into a full reinstall, broadening manifests to include runtime folders, or trusting packaging alone to keep user data safe.
- Allowed evolution:
  Add stronger validation, better logs, more recovery tooling, or richer release-channel support, but keep the rule that runtime state is outside updater ownership.
- Evidence:
  Current update-service/update-worker code, release-pack docs, and updater regression tests explicitly protect package-local runtime and user-data paths.
- Last verified:
  2026-04-27 against current workspace updater code and tests.
- Related files:
  `backend/update_worker.py`
  `backend/services/update_service.py`
  `scripts/build_release_packages.py`
  `docs/RELEASE_PACKS.md`
- Supersedes:
  Any implicit assumption that a full-package fallback may safely overwrite the whole extracted folder tree.
- Validation:
  `backend/tests/test_update_worker.py` protected-path regression coverage plus release-build manifest exclusion tests.

### ADR-AI-20260427-17: Update checks are manual, GitHub-default, and advanced-channel override is opt-in
- Status: active
- Area: update UX / release channel semantics
- Evidence tier: explicit user instruction + Tier 1
- Decision:
  The app should only check for updates when the user clicks the update button. It should not auto-check on startup and should not auto-apply updates without explicit confirmation. The default update channel remains GitHub Releases, while custom channel/proxy settings are an advanced opt-in path for advanced users and fork maintainers.
- Why:
  The intended product behavior is "one-click when I choose" rather than background updater behavior. Ordinary users should not be forced into channel/proxy setup just because GitHub can be blocked in some regions. At the same time, advanced users still need a supported override path.
- Do not "improve" this by:
  Adding startup auto-checks, silent background updates, mandatory proxy/channel setup UI for everyone, or replacing honest GitHub/VPN guidance with a fake built-in default mirror story.
- Allowed evolution:
  Clearer update status UI, better channel diagnostics, better wording around VPN/channel override, and more advanced override options behind an explicitly advanced path.
- Evidence:
  Current frontend only checks when the update buttons are clicked; current README and release-pack docs tell ordinary users to use the update button and enable VPN if GitHub is unreachable; current backend keeps custom channel override available through config instead of forcing it into the normal user flow.
- Last verified:
  2026-04-27 against current frontend update flow, updater service behavior, README, and release-pack docs.
- Related files:
  `frontend/js/app.js`
  `backend/services/update_service.py`
  `backend/routers/updates.py`
  `README.md`
  `docs/RELEASE_PACKS.md`
  `backend/tests/test_update_service.py`
- Supersedes:
  None
- Validation:
  `backend/tests/test_update_service.py`, `backend/tests/test_routers/test_updates.py`, unsafe archive-entry validation tests, and current click-triggered frontend update flow.

### ADR-AI-20260427-18: LoRA library filtering uses exact normalized names, not substring search
- Status: active
- Area: filter semantics / metadata assets
- Evidence tier: Tier 1
- Decision:
  Filtering by selected LoRAs should match the normalized LoRA name exactly after stripping path, extension, and weight syntax. It should not use substring matching such as `%girl%`.
- Why:
  LoRA names are identity-like asset names. Substring matching makes common short names dangerous: `girl` can match `school_girl`, and `detail` can match unrelated `add_detail` variants. The `image_loras` junction table exists to make normalized asset-name matching explicit and indexable.
- Do not "improve" this by:
  Replacing exact `image_loras.lora_name = ?` filtering with broad `LIKE` search for selected LoRA filters. If fuzzy LoRA discovery is needed, add a separate search/discovery mode instead of changing the filter contract.
- Allowed evolution:
  Add aliases, explicit fuzzy search UI, or richer LoRA asset metadata, but keep selected-filter execution exact unless the UI clearly says otherwise.
- Evidence:
  Current `backend/database.py` normalizes LoRA names with `normalize_lora_name()` and filters against `image_loras` by exact lowercase normalized name.
- Last verified:
  2026-04-27 against current database filter implementation and regression tests.
- Related files:
  `backend/database.py`
  `backend/tests/test_database.py`
  `frontend/js/gallery.js`
- Supersedes:
  The previous substring `LIKE` implementation in `_apply_lora_filter()`.
- Validation:
  `backend/tests/test_database.py` covers exact LoRA filters for both stored LoRA arrays and inline `<lora:name:weight>` prompt tags.

### ADR-AI-20260427-20: Stale scan placeholder rows are quarantined on startup instead of silently staying pending
- Status: active
- Area: scan lifecycle / data recovery
- Evidence tier: Tier 1
- Decision:
  Any `images.metadata_status = "pending"` row that survives into a fresh app startup is treated as a stale interrupted-scan placeholder. Startup repair must convert it into a recoverable `error` / unreadable row instead of leaving it in a forever-pending readable state.
- Why:
  Once the process is gone, there is no in-flight metadata worker left that can finish that placeholder. Leaving it pending creates a blind spot for derived-state invalidation and misreports library health. Quarantining it is more truthful, while keeping its recoverable fingerprint/derived data lets a later rescan restore the row safely if the source file did not actually change.
- Do not "improve" this by:
  Auto-marking stale pending rows as `complete`, silently leaving them readable forever, or pretending a later background worker still exists when the scan already died.
- Allowed evolution:
  Add richer repair UI, startup diagnostics, or explicit rescan helpers, but keep the rule that stale pending placeholders are no longer considered healthy rows after restart.
- Evidence:
  Current `database.init_db()` now repairs stale pending rows on startup, and scan logic already reparses non-`complete` rows on the next truthful rescan.
- Last verified:
  2026-04-27 against current startup repair code and regression coverage.
- Related files:
  `backend/database.py`
  `backend/image_manager.py`
  `backend/tests/test_database.py`
- Supersedes:
  The accidental old behavior where interrupted placeholder rows could remain `pending` indefinitely.
- Validation:
  `backend/tests/test_database.py::test_init_quarantines_stale_pending_rows_without_erasing_recoverable_derived_state`

### ADR-AI-20260427-21: Release bootstrap and update downloads should use checksum validation when the repo controls the artifact channel
- Status: active
- Area: release / updater integrity
- Evidence tier: Tier 1
- Decision:
  External runtime/bootstrap downloads used by release packaging must be pinned by SHA-256 and must use immutable artifact URLs when the upstream default URL is mutable; release-built update assets should expose a checksum manifest so the in-app updater can validate archives when that manifest is present. Bootstrap download caches must stay under staging, not the release asset root.
- Why:
  Size-only validation is not enough for bootstrap Python, `get-pip.py`, or shipped update archives. This repo already controls the release-builder output, so it should use that control to make drift and tampering fail loudly instead of silently succeeding.
- Do not "improve" this by:
  Reverting to naked `urlretrieve()` downloads, pinning a mutable `get-pip.py` URL by hash, leaving bootstrap cache files next to publishable release assets, keeping updater validation at size-only when a checksum manifest exists, or treating checksum assets as optional decoration with no enforcement path.
- Allowed evolution:
  Stronger signing, detached signatures, or richer manifest metadata are welcome later, but the baseline checksum guard should stay in place.
- Evidence:
  `scripts/build_release_packages.py` now pins Python embed downloads by versioned Python URL and SHA-256, pins `get-pip.py` to a specific `pypa/get-pip` Git commit plus SHA-256, keeps bootstrap download cache under staging, and emits a release-manifest asset; `backend/services/update_service.py` can consume that manifest to validate downloaded archives.
- Last verified:
  2026-04-28 against current release-builder code, updater code, release-build smoke, and regression coverage.
- Related files:
  `scripts/build_release_packages.py`
  `backend/services/update_service.py`
  `backend/tests/test_release_build.py`
  `backend/tests/test_update_service.py`
- Supersedes:
  The older size-only / trust-the-URL release bootstrap behavior.
- Validation:
  `backend/tests/test_release_build.py` checksum/bootstrap source tests, `backend/tests/test_update_service.py` manifest/checksum download tests, and `scripts/build_release_packages.py` smoke build.

### ADR-AI-20260428-22: Cross-generator checkpoint filters must use `checkpoint_normalized`, while raw `checkpoint` stays display-only
- Status: active
- Area: metadata asset semantics / gallery filters / prompt stats
- Evidence tier: Tier 1
- Decision:
  Image rows now persist both raw `checkpoint` and derived `checkpoint_normalized`. Raw `checkpoint` remains the per-image display value, while gallery filters, analytics facets, free-text checkpoint search, and prompt-stat checkpoint grouping must compare on `checkpoint_normalized`.
- Why:
  Different generators report the same model in incompatible forms: path prefixes, file extensions, and WebUI-style hash suffixes such as `model.safetensors [abc12345]`. Using raw `checkpoint` as both display text and identity key fragments one logical model into multiple silent filter buckets. Splitting display from identity fixes the contract without hiding the original metadata string.
- Do not "improve" this by:
  Going back to raw `checkpoint` equality for filters/facets, dropping the raw field from image payloads, or broadening checkpoint matching into fuzzy substring behavior that makes one model accidentally match another.
- Allowed evolution:
  Add richer checkpoint display labels, alias tables, or generator-specific normalization rules later, but keep the rule that cross-generator filter/search/facet semantics use the normalized key.
- Evidence:
  The schema now includes `images.checkpoint_normalized`; DB writes backfill and maintain it; gallery/image payloads expose both raw and normalized checkpoint fields; analytics and prompt stats group by `checkpoint_normalized`; checkpoint search and filter queries now compare on the normalized field.
- Last verified:
  2026-04-28 against current workspace code plus targeted backend regression coverage.
- Related files:
  `backend/utils/model_names.py`
  `backend/migrations/005_checkpoint_normalization.py`
  `backend/database.py`
  `backend/services/sorting_service.py`
  `backend/routers/prompts.py`
  `backend/routers/images.py`
  `backend/tests/test_database.py`
  `backend/tests/test_routers/test_images.py`
  `backend/tests/test_routers/test_sorting.py`
  `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`
  `frontend/js/app.js`
  `frontend/js/gallery.js`
- Supersedes:
  The old implicit behavior where raw mixed generator checkpoint strings also doubled as the filter/facet identity key.
- Validation:
  `backend/tests/test_database.py`, `backend/tests/test_routers/test_images.py`, `backend/tests/test_routers/test_sorting.py`, and `backend/tests/test_routers/test_prompts_censor_similarity_artists.py` all pass with the new contract.

### ADR-AI-20260428-23: Manual Sort session persistence belongs to package-local runtime state (`data/state`), with legacy-path migration
- Status: active
- Area: runtime state / sorting session persistence
- Evidence tier: Tier 1
- Decision:
  Persist Manual Sort session payloads at `data/state/sort-session.json` by default (`SD_IMAGE_SORTER_SORT_SESSION_FILE` override supported). On startup, still read legacy `backend/sort_session.json` if present, then migrate to the new runtime path.
- Why:
  Session persistence is runtime/user state, not source-code-adjacent app code. Keeping it under `backend/` made release/update ownership blurry and depended on packaging exclusions instead of runtime-boundary contract.
- Do not "improve" this by:
  Moving session persistence back beside backend source files, or dropping legacy-path compatibility immediately.
- Allowed evolution:
  Move to richer session schema/versioning, add multi-session support, or relocate state under a new runtime directory as long as runtime ownership and migration behavior remain explicit.
- Evidence:
  Sorting service now writes to the config-driven runtime state path and keeps compatibility load/migration logic for legacy persisted sessions.
- Last verified:
  2026-04-28 against current sorting service and config behavior.
- Related files:
  `backend/config.py`
  `backend/services/sorting_service.py`
  `backend/update_worker.py`
  `scripts/build_release_packages.py`
- Supersedes:
  The older implicit behavior that persisted session state under `backend/sort_session.json`.
- Validation:
  Targeted sorting-session restore tests and runtime-path verification in current workspace.

### ADR-AI-20260428-24: Release builder and detached updater must share manifest/runtime-protection constants
- Status: active
- Area: release/update operability contract
- Evidence tier: Tier 1
- Decision:
  Packaging and updater flows must reuse the same manifest relative paths and runtime-protected prefix definitions, instead of duplicating string literals in separate modules.
- Why:
  Copy-maintained constants drift silently and only explode during publish/update. The detached worker is the enforcement boundary; the builder should derive its exclusions and manifest naming from that same contract.
- Do not "improve" this by:
  Reintroducing hardcoded `update/package-manifest.json` / `update/installed-manifest.json` string copies in the release builder, or bypassing updater-owned runtime protection prefixes in packaging logic.
- Allowed evolution:
  Move this contract to a dedicated shared module later, as long as builder/updater continue to share one source of truth.
- Evidence:
  `scripts/build_release_packages.py` now imports manifest/runtime-protection constants from `backend/update_worker.py` and uses them for skip and manifest generation paths.
- Last verified:
  2026-04-28 against current release-builder and updater code.
- Related files:
  `scripts/build_release_packages.py`
  `backend/update_worker.py`
  `backend/tests/test_release_build.py`
  `backend/tests/test_update_worker.py`
- Supersedes:
  Prior duplicated manifest/runtime protection literals across builder and updater.
- Validation:
  Release-builder and updater unit tests in current workspace.

### ADR-AI-20260428-25: `/api/images` cursor pagination is an opaque API contract, not an image-ID contract
- Status: active
- Area: API contract / pagination invariants
- Evidence tier: Tier 1
- Decision:
  `GET /api/images` must expose `next_cursor` as an opaque token that clients pass back unchanged. Backend pagination may still accept legacy integer image IDs for backward compatibility, but frontend and external callers must not parse, synthesize, or rely on cursor internals.
- Why:
  Treating `cursor` as a bare image ID made pagination fragile when the anchor row disappeared between requests. The stable contract is the `(sort_value, id)` boundary encoded into the cursor token; that lets newest/oldest pagination continue even after deletes while keeping clients decoupled from storage details.
- Do not "improve" this by:
  Switching frontend code or docs back to integer cursor assumptions, regenerating cursor strings on the client, or downgrading service-layer parsing to `int(cursor)`.
- Allowed evolution:
  Change cursor token schema/version later, provided the token remains opaque to clients and legacy integer acceptance is retired only with an explicit compatibility decision.
- Evidence:
  Shared cursor helpers now live in `backend/utils/pagination_cursor.py`; image service decodes API cursors before calling the DB layer; DB pagination uses stored sort boundaries when available; router/API docs describe the token as opaque; regressions cover malformed tokens, deleted anchor rows, and frontend passthrough behavior.
- Last verified:
  2026-04-28 against current workspace code plus targeted backend and Playwright regression coverage.
- Related files:
  `backend/utils/pagination_cursor.py`
  `backend/services/image_service.py`
  `backend/database.py`
  `backend/routers/images.py`
  `backend/db_repos/repositories/base.py`
  `backend/db_repos/repositories/image_repo.py`
  `docs/API.md`
  `backend/tests/test_database.py`
  `backend/tests/test_api_errors.py`
  `backend/tests/test_routers/test_images.py`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  The older implicit contract where `cursor` / `next_cursor` were treated as raw image IDs.
- Validation:
  Targeted pagination regression tests in `backend/tests/test_database.py`, `backend/tests/test_api_errors.py`, `backend/tests/test_routers/test_images.py`, and `tests/e2e/specs/smoke.spec.ts`.

### ADR-AI-20260428-26: Tag import writes must reuse the shared tag writer contract
- Status: active
- Area: derived-state invariants / tag import semantics
- Evidence tier: Tier 1
- Decision:
  Any feature that writes final tags into the library, including JSON tag import flows, must route through the shared DB tag-writer contract (`db.add_tags()` / `db.add_tags_batch()`) instead of hand-writing tag SQL in the caller.
- Why:
  The shared tag writer owns more than `tags` rows. It also owns `tagged_at`, tag-cache invalidation, and `content_fingerprint` backfill needed to keep derived-state invalidation rules consistent with scan/tag/save flows. When import logic writes tags directly, the system stops having one owner for those invariants.
- Do not "improve" this by:
  Reintroducing per-feature `DELETE FROM tags` / `INSERT` / `UPDATE images SET tagged_at ...` SQL in import/export/service code, or by treating tag import as a harmless special case because it is "just data restore."
- Allowed evolution:
  Move the shared writer behind a repository/service abstraction later, provided tag import still reuses one canonical write path and payload normalization stays outside the DB helper.
- Evidence:
  `TaggingService.import_tags()` now normalizes duplicate tag payloads and delegates the final write to `db.add_tags_batch()` instead of maintaining its own tag/`tagged_at` SQL path.
- Last verified:
  2026-04-28 against current workspace code and targeted backend regression coverage.
- Related files:
  `backend/services/tagging_service.py`
  `backend/database.py`
  `backend/tests/test_tagging_service.py`
  `backend/tests/test_database.py`
  `backend/tests/test_routers/test_tags.py`
- Supersedes:
  The older implicit behavior where import-tag writes were allowed to bypass the shared tag writer as long as the endpoint appeared to work.
- Validation:
  Targeted import-tag regressions in `backend/tests/test_tagging_service.py`, `backend/tests/test_database.py`, and `backend/tests/test_routers/test_tags.py`.

### ADR-AI-20260428-27: Reader overwrite confirmation and gallery refresh must use canonical pre-save library identity
- Status: active
- Area: reader save semantics / indexed-path UX contract
- Evidence tier: Tier 1
- Decision:
  Reader metadata-save overwrite checks must compare the requested output path against the canonical original source path when one exists, not against the upload temp path. After save succeeds, gallery refresh intent must be decided against the pre-save indexed source identity (plus currently loaded gallery image paths), not against the post-save path after Reader retargets itself to the new output file.
- Why:
  Reader uploads can have both a browser temp path and an original indexed library path. The temp path is not the library identity users care about for overwrite confirmation. Separately, once save succeeds Reader intentionally retargets its current source to the saved file; if refresh logic compares against that new path, every save starts looking like an indexed overwrite and causes unnecessary gallery reloads.
- Do not "improve" this by:
  Falling back to temp-upload-path overwrite checks when a canonical original path is known, or by moving the refresh comparison after Reader has already overwritten its source identity with the saved output path.
- Allowed evolution:
  Move this decision into a more explicit Reader save-state model later, as long as overwrite confirmation remains based on canonical original identity and gallery refresh stays limited to real indexed-path overwrites.
- Evidence:
  Reader now uses `_getCanonicalSourcePathForOverwrite()` for client-side overwrite confirmation and `_markGalleryRefreshForIndexedOverwrite(savedOutputPath, previousOriginalSourcePath)` to preserve the pre-save comparison identity while still retargeting Reader to the saved file afterward.
- Last verified:
  2026-04-28 against current workspace code plus targeted Playwright smoke coverage.
- Related files:
  `frontend/js/image-reader.js`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  The older implicit behavior where Reader overwrite checks could compare against temp upload paths and gallery refresh intent could drift with post-save state mutation.
- Validation:
  Targeted Reader save-path smoke coverage in `tests/e2e/specs/smoke.spec.ts`, including indexed overwrite confirmation and save-as-new non-refresh behavior.

### ADR-AI-20260428-28: Feature-local expensive derived-state writes must use the shared derived-state writer
- Status: active
- Area: backend data lifecycle / derived-state invariants
- Evidence tier: Tier 1
- Decision:
  Feature services that write expensive pixel-derived image state (`embedding`, `aesthetic_score`, artist prediction fingerprint advancement) must route image-row writes through `backend/services/derived_state_service.py` instead of embedding their own `UPDATE images ... content_fingerprint ...` SQL.
- Why:
  `content_fingerprint` is the validity boundary for expensive derived state. Letting each feature hand-write the same update silently turns fingerprint advancement into a scattered invariant and makes stale caches easy to bless as current data.
- Do not "improve" this by:
  Reintroducing direct feature-local `UPDATE images SET ... content_fingerprint = COALESCE(...)` SQL in similarity, aesthetic, artist, or future derived-analysis services.
- Allowed evolution:
  Move the helper to a lower DB-owned module later to remove the remaining `database.py` ownership split, provided feature services still call one shared writer boundary.
- Evidence:
  `backend/similarity.py`, `backend/services/aesthetic_service.py`, and `backend/services/artist_service.py` now use `write_image_embeddings()`, `write_image_aesthetic_score()`, `write_image_content_fingerprint(s)()`, or `write_artist_prediction()`; `backend/tests/test_derived_state_contract.py` guards the writer allowlist and preserves metadata-only vs pixel-change invalidation semantics.
- Last verified:
  2026-04-28 against current workspace code and targeted backend contract tests.
- Related files:
  `backend/services/derived_state_service.py`
  `backend/similarity.py`
  `backend/services/aesthetic_service.py`
  `backend/services/artist_service.py`
  `backend/database.py`
  `backend/tests/test_derived_state_contract.py`
- Supersedes:
  The older tolerated pattern where each feature service could advance `content_fingerprint` with local SQL as long as the endpoint worked.
- Validation:
  Targeted derived-state contract tests plus service integration regression coverage in current workspace.

### ADR-AI-20260428-29: Filtered Gallery selection is tied to an exact filter snapshot
- Status: active
- Area: frontend UX / selection-filter contract
- Evidence tier: Tier 1
- Decision:
  A Gallery selection with scope `filtered` is only valid for the filter snapshot recorded in `selectionFilterKey`. If Gallery filters change, stale filtered selection must be cleared and downgraded to `visible` rather than silently applying to a different result set. Gallery filter modal apply/reset must commit through `FilterStore`, not direct `AppState.filters` mutation.
- Why:
  `filtered` means "all backend-resolved results for this exact filter", not "whatever the current sidebar happens to mean later". Keeping old IDs after filter changes makes destructive batch actions semantically wrong even when the UI counter still looks plausible.
- Do not "improve" this by:
  Preserving `filtered` selections across filter-key changes, mutating `AppState.filters` directly from the modal, or inferring filtered selection from currently loaded thumbnails.
- Allowed evolution:
  Add explicit UX prompts to preserve/convert selections later, but the default hidden state transition must not carry stale filtered selections across filter snapshots.
- Evidence:
  `frontend/js/app.js` now clears stale filtered selections from the `FilterStore` subscription / fallback setter and commits filter-modal apply/reset through `commitFilterModalState()`; Playwright regression coverage verifies both behaviors.
- Last verified:
  2026-04-28 against current workspace code and selected Playwright coverage.
- Related files:
  `frontend/js/app.js`
  `frontend/js/stores/filter-store.js`
  `frontend/js/stores/selection-store.js`
  `tests/e2e/specs/manual-regression.spec.ts`
- Supersedes:
  Any older implicit behavior where a filtered selection could survive unrelated filter mutations because the selected ID set was still non-empty.
- Validation:
  `manual-regression.spec.ts` tests for filtered-selection clearing and FilterStore-based modal commits.

### ADR-AI-20260428-30: Downloaded update archives must prove their managed-path contract before apply
- Status: active
- Area: release/update operability contract
- Evidence tier: Tier 1
- Decision:
  A downloaded update archive is not a valid update payload unless it contains `update/package-manifest.json` and that manifest passes strict managed-path validation before the update is staged/applied.
- Why:
  Archive member-name checks alone only prevent traversal. They do not prove which installed files the release claims to own, and they do not prevent a future bad package from managing protected runtime state such as `data/` or updater workspaces.
- Do not "improve" this by:
  Accepting archives without `update/package-manifest.json`, treating manifest validation as worker-only best effort, or letting protected runtime paths appear in a newly downloaded manifest because the builder "should have" filtered them earlier.
- Allowed evolution:
  Add signatures or stronger manifest schema validation later; the minimum contract remains manifest-present plus managed-path validation before file replacement.
- Evidence:
  `UpdateService._validate_archive()` now reads the package manifest from rootless or single-payload-root zip/tar update payloads, rejects archives missing it, rejects multiple real package manifests, ignores fake `badupdate/package-manifest.json` suffix matches, and calls `validate_update_manifest_managed_paths()`; release-builder tests and updater tests cover protected runtime path filtering and rejection.
- Last verified:
  2026-04-28 against current workspace code and release/update contract tests.
- Related files:
  `backend/services/update_service.py`
  `backend/update_worker.py`
  `scripts/build_release_packages.py`
  `backend/tests/test_update_worker.py`
  `backend/tests/test_update_service.py`
  `backend/tests/test_release_build.py`
  `docs/RELEASE_PACKS.md`
- Supersedes:
  The older weaker assumption that a syntactically safe archive was enough to proceed and the worker would catch all package ownership mistakes later.
- Validation:
  Targeted release/update tests plus full CI in current workspace.



### ADR-AI-20260428-31: Prompt and LoRA library facets must use maintained index tables
- Status: active
- Area: database / performance / facet semantics
- Evidence tier: Tier 1
- Decision:
  Prompt and LoRA library/facet counts must be served from maintained normalized index tables (`image_prompt_tokens`, `image_loras`) instead of reparsing every `images.prompt` / `images.loras` row at request time.
- Why:
  Library panels are interactive UI surfaces. Full-table Python regex/JSON scans scale badly on large SD libraries and create a second implementation of prompt/LoRA normalization that can drift from filter semantics.
- Do not "improve" this by:
  Reintroducing `SELECT id, prompt FROM images` / `SELECT id, loras, prompt FROM images` plus request-time tokenization in `TaggingService`, or by adding a new prompt/LoRA facet endpoint that bypasses the maintained indexes.
- Allowed evolution:
  Replace the SQLite tables with a generated statistics table or searchable facet API later, provided scan/reparse/update paths remain the source of truth and request handlers do not reparse the whole image table.
- Evidence:
  Migration `006_prompt_token_index` creates/backfills `image_prompt_tokens`; `database._sync_image_prompt_tokens()` and existing `image_loras` sync refresh indexes on add/reparse; `TaggingService.get_prompts_library()` and `TaggingService.get_loras_library()` now delegate to DB indexed facet helpers.
- Last verified:
  2026-04-28 against current workspace code and targeted database/router tests.
- Related files:
  `backend/migrations/006_prompt_token_index.py`
  `backend/migrations/_schema_common.py`
  `backend/database.py`
  `backend/services/tagging_service.py`
  `backend/routers/tags.py`
  `backend/tests/test_database.py`
  `backend/tests/test_routers/test_tags.py`
- Supersedes:
  The older accepted behavior where opening prompt/LoRA libraries could trigger full-image-table parsing.
- Validation:
  Targeted database migration/index tests and prompt/LoRA router tests.

### ADR-AI-20260428-32: Censor save overwrite requires explicit intent and returns reconcile signals
- Status: active
- Area: UX contract / indexed overwrite lifecycle
- Evidence tier: Tier 1
- Decision:
  Censor save endpoints must default to no overwrite. Replacing an existing output file requires explicit `allow_overwrite=true`, and successful saves that touch an indexed path must return enough signal for the frontend to mark Gallery data stale.
- Why:
  Reader already treats overwrite as destructive intent. Censor silently overwriting files creates inconsistent UX and can leave the Gallery showing stale thumbnails/metadata unless the frontend knows an indexed output was reconciled.
- Do not "improve" this by:
  Defaulting Censor saves back to `allow_overwrite=True`, swallowing 409 conflicts as generic save failures without user-facing overwrite policy, or letting Censor write directly to `AppState.galleryNeedsRefresh` instead of using the app boundary.
- Allowed evolution:
  Add a richer 409-confirm-retry dialog later. The minimum contract remains explicit overwrite intent plus backend reconcile metadata.
- Evidence:
  `CensorSaveRequest`, `CensorSaveDataRequest`, and `CensorSaveOperationsRequest` now include `allow_overwrite`; `CensorService` rejects implicit existing-file overwrites with 409, uses shared save/reconcile, and returns `overwrote_existing`, `overwrote_indexed_path`, `reconciled_image_id`, and `warnings`; Censor UI exposes an overwrite checkbox and calls `window.App.markGalleryNeedsRefresh()` on indexed overwrites.
- Last verified:
  2026-04-28 against targeted backend tests and frontend syntax checks.
- Related files:
  `backend/services/censor_service.py`
  `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`
  `frontend/index.html`
  `frontend/js/censor-edit.js`
  `frontend/js/app.js`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
- Supersedes:
  The older implicit Censor behavior where save-data/save-operations always overwrote target files.
- Validation:
  Targeted Censor save overwrite/reconcile tests and JS syntax checks.

### ADR-AI-20260428-33: Gallery scope-narrowing operations must drop out-of-scope IDs
- Status: active
- Area: frontend selection semantics / destructive action safety
- Evidence tier: Tier 1
- Decision:
  When a Gallery operation narrows selection scope to `visible`, it must not carry IDs from broader or stale scopes such as `filtered` or `loaded`. When a range operation enters `loaded`, it must not carry stale `filtered` IDs.
- Why:
  The scope label drives user trust before destructive actions. If UI says `visible` while `selectedIds` still contains thousands of filtered/off-screen IDs, delete/export/censor actions can operate on a larger set than the user believes.
- Do not "improve" this by:
  Cloning the previous `selectedIds` set unconditionally inside `Gallery.toggleSelection()`, `selectAllVisible()`, `invertVisibleSelection()`, or range-selection code.
- Allowed evolution:
  Add an explicit "add visible to filtered selection" UX later, but implicit scope narrowing must discard out-of-scope IDs.
- Evidence:
  `frontend/js/gallery.js` now uses `selectionBaseForScope()` for visible/loaded transitions, and Playwright coverage verifies that toggling a visible item after a filtered selection drops an off-screen filtered ID.
- Last verified:
  2026-04-28 against frontend syntax checks and selected Playwright coverage in current workspace.
- Related files:
  `frontend/js/gallery.js`
  `tests/e2e/specs/manual-regression.spec.ts`
- Supersedes:
  The older implementation that cloned the whole selected-ID set and then relabeled the selection scope.
- Validation:
  `manual-regression.spec.ts` selection-scope regression plus JS syntax checks.

### ADR-AI-20260428-34: Derived state may only be preserved when content identity is explicitly unchanged
- Status: active
- Area: backend data lifecycle / derived-state invalidation
- Evidence tier: Tier 1
- Decision:
  `preserve_derived_state=True` is not a caller override for stale caches. It may preserve tags, embeddings, captions, scores, and artist predictions only when the row remains readable, metadata status is `complete`, and both old and incoming `content_fingerprint` values are present and equal.
- Why:
  Metadata-only rewrites should not force expensive reprocessing, but unreadable parses, missing fingerprints, or changed pixel fingerprints must invalidate derived state. Otherwise old AI results can be silently blessed as current data after overwrite/reparse failure.
- Do not "improve" this by:
  Treating `preserve_derived_state=True` as unconditional, preserving derived state for unreadable/error rows, or preserving when either content fingerprint is unknown.
- Allowed evolution:
  Move the invalidation decision into a lower repository layer later, provided the same content-identity precondition remains enforced.
- Evidence:
  `backend/database.py` now computes `can_preserve_derived_state` from readable/status/fingerprint equality, and tests cover matching-fingerprint preservation plus unreadable and pixel-changed invalidation.
- Last verified:
  2026-04-28 against targeted database and derived-state regression tests.
- Related files:
  `backend/database.py`
  `backend/tests/test_database.py`
  `backend/tests/test_derived_state_contract.py`
- Supersedes:
  The older implicit behavior where caller intent could preserve derived state even when content identity was unknown or invalid.
- Validation:
  `test_update_image_metadata_preserve_flag_requires_matching_fingerprint`, `test_update_image_metadata_preserve_flag_clears_when_content_changed`, and `test_update_image_metadata_preserve_flag_does_not_keep_unreadable_rows`.

### ADR-AI-20260428-35: Interrupted scans must reconcile pending placeholders in the same runtime
- Status: active
- Area: scan/import/rescan lifecycle
- Evidence tier: Tier 1
- Decision:
  A scan run that is cancelled or fails after quick-import placeholders are written must reconcile its own pending rows before returning: new placeholders are removed, and updated placeholders that never completed metadata backfill are quarantined as unreadable `metadata_status='error'` rows.
- Why:
  Startup repair is not enough. A user can cancel a scan and keep using the same running app; leaving readable `pending` rows in that session makes the library look imported while metadata and derived-state validity are still unknown.
- Do not "improve" this by:
  Relying only on next-startup cleanup, leaving pending rows readable, or reporting placeholder writes as completed metadata updates.
- Allowed evolution:
  Add scan-run IDs or a full import job table later; the current low-risk rule is that interrupted placeholder state must be reconciled before the current process continues normal use.
- Evidence:
  `backend/image_manager.py` tracks per-run placeholder status and metadata completion, deletes new unresolved placeholders, and quarantines unresolved updated placeholders on cancellation/error.
- Last verified:
  2026-04-28 against targeted scan lifecycle tests.
- Related files:
  `backend/image_manager.py`
  `backend/tests/test_image_manager.py`
- Supersedes:
  The older assumption that startup pending-row quarantine was sufficient for all interrupted scans.
- Validation:
  `test_scan_folder_raises_cancelled_when_stop_requested_after_first_progress` and scan count regressions.

### ADR-AI-20260428-36: Obfuscation save uses the same explicit overwrite contract as Reader and Censor
- Status: active
- Area: overwrite UX / indexed-file reconciliation
- Evidence tier: Tier 1
- Decision:
  Obfuscation encode/decode and batch processing default to `allow_overwrite=false`; replacing an existing output requires explicit `allow_overwrite=true`. Successful saves return `warnings`, `overwrote_existing`, `overwrote_indexed_path`, and `reconciled_image_id` so clients can refresh indexed gallery state.
- Why:
  Reader and Censor already require destructive intent. Obfuscation silently overwriting files would keep the most destructive image transformation on a different contract and recreate hidden gallery-staleness failures.
- Do not "improve" this by:
  Passing `allow_overwrite=True` by default, hiding indexed overwrite signals, or treating obfuscation as a special case because it is a utility endpoint.
- Allowed evolution:
  Move existence/preflight details deeper into the shared indexed-file mutation helper later, but every current save-capable feature must remain explicit about overwrite intent.
- Evidence:
  `SingleProcessRequest` and `BatchProcessRequest` now expose `allow_overwrite`, obfuscation rejects existing targets with HTTP 409 by default, and tests cover explicit overwrite plus indexed-state refresh.
- Last verified:
  2026-04-28 against obfuscation router tests.
- Related files:
  `backend/routers/obfuscation.py`
  `backend/obfuscation.py`
  `backend/tests/test_routers/test_obfuscation.py`
- Supersedes:
  The previous obfuscation behavior where `save_and_reconcile(... allow_overwrite=True)` made overwrites implicit.
- Validation:
  `backend/tests/test_routers/test_obfuscation.py`.

### ADR-AI-20260428-37: Manual Sort resume UI must show server-session context, not local setup preferences
- Status: active
- Area: frontend UX / manual-sort session semantics
- Evidence tier: Tier 1
- Decision:
  When `/api/sort/current` reports an unfinished session, the resume banner must display that server session's remaining count, operation mode, and saved folder mapping, and must state that setup preferences may differ from the active saved session.
- Why:
  Manual Sort setup fields are local preferences until a new session starts. Resume uses backend/persisted session state. If the banner only shows a count, users cannot tell whether they are resuming move vs copy or which folders will be used.
- Do not "improve" this by:
  Inferring resume context from `localStorage`, hiding operation/folder state until after resume, or suggesting setup edits will change an existing server session.
- Allowed evolution:
  Make setup read-only while a server session exists or add a richer session-detail panel, provided resume context remains server-owned.
- Evidence:
  `frontend/js/manual-sort.js` renders resume details from the `/api/sort/current` payload, and Playwright smoke coverage asserts mode/folder copy appears in the banner.
- Last verified:
  2026-04-28 against JS syntax and targeted/static tests; Playwright target discovery passed in the current environment.
- Related files:
  `frontend/js/manual-sort.js`
  `frontend/index.html`
  `frontend/js/lang/en.js`
  `frontend/js/lang/zh-CN.js`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  The older count-only resume banner that let `localStorage` setup values visually compete with server session state.
- Validation:
  Manual Sort resume banner smoke coverage in `tests/e2e/specs/smoke.spec.ts`.

### ADR-AI-20260428-38: Large synchronous UX operations need explicit caps before staged backend protocols exist
- Status: active
- Area: performance / large-library UX
- Evidence tier: Tier 1
- Decision:
  Until backend-side selection tokens, streamed exports, and background duplicate jobs exist, synchronous large operations must have guardrails: filtered selection above 10,000 results requires explicit confirmation, export prompt/tag preview loads at most 2,000 selected IDs and truncates huge text, and duplicate search refuses synchronous all-pairs comparison above `DUPLICATE_SYNC_MAX_EMBEDDINGS`.
- Why:
  These are not complete scalability architectures, but they prevent the current UI/backend from silently walking into memory and CPU cliffs on large libraries.
- Do not "improve" this by:
  Removing the confirmations/caps because pagination exists elsewhere, raising thresholds without performance evidence, or pretending the guards replace tokenized selection/export/background duplicate protocols.
- Allowed evolution:
  Replace the guards with server-side selection snapshots, streaming/paged export, searchable facets, and background/ANN duplicate workflows.
- Evidence:
  `frontend/js/app.js` adds filtered-selection confirmation and export preview caps; `backend/similarity.py` counts embeddings before loading them and returns a structured `too_many_embeddings` duplicate-search reason; `frontend/js/similar.js` displays that reason.
- Last verified:
  2026-04-28 against targeted backend and JS syntax tests.
- Related files:
  `frontend/js/app.js`
  `frontend/js/similar.js`
  `backend/similarity.py`
  `backend/services/similarity_service.py`
  `backend/config.py`
  `tests/e2e/specs/smoke.spec.ts`
  `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`
- Supersedes:
  The older behavior where these actions could synchronously materialize very large ID sets, text payloads, or all-pairs embedding matrices without an explicit user/performance boundary.
- Validation:
  Duplicate-search limit regression tests, JS syntax checks, and smoke tests for selection/export guardrails.

### ADR-AI-20260428-39: New migrations must freeze their own data-transform semantics
- Status: active
- Area: schema migration / operability
- Evidence tier: Tier 1
- Decision:
  Numbered migrations must not import mutable runtime business helpers such as `database.extract_prompt_tokens()` for data backfills. If a migration needs transformation logic, it must freeze the version used by that migration or import from a migration-safe frozen helper.
- Why:
  Re-running an old migration during a future fresh install must produce the same schema/data shape that existing upgraded users received. Importing live runtime helpers makes historical migrations change silently when business semantics evolve.
- Do not "improve" this by:
  DRYing migrations against runtime helpers whose behavior can change, or changing a historical migration's transform logic instead of adding a new migration.
- Allowed evolution:
  Introduce a dedicated migration utility module containing versioned frozen helpers.
- Evidence:
  `backend/migrations/006_prompt_token_index.py` now contains `_extract_prompt_tokens_v1()` and no longer imports `database`; `backend/tests/test_migration_contract.py` guards both import isolation and tokenizer examples.
- Last verified:
  2026-04-28 against migration contract tests.
- Related files:
  `backend/migrations/006_prompt_token_index.py`
  `backend/tests/test_migration_contract.py`
- Supersedes:
  The initial v6 migration draft that imported the mutable runtime prompt-token extractor from `database.py`.
- Validation:
  `backend/tests/test_migration_contract.py`.

### ADR-AI-20260428-40: SQLite datetime values are adapted explicitly for Python 3.12+
- Status: active
- Area: dependency/runtime compatibility / database serialization
- Evidence tier: Tier 1
- Decision:
  `backend/database.py` registers an explicit `datetime -> string` SQLite adapter using `datetime.isoformat(sep=" ")` so inserts and updates do not rely on Python's deprecated default sqlite3 datetime adapter.
- Why:
  Python 3.12 emits deprecation warnings for the default adapter. Leaving this implicit turns normal scan/add-image paths into warning noise now and future runtime risk later.
- Do not "improve" this by:
  Removing the adapter because tests pass on one Python version, or changing stored datetime format without a migration and ordering compatibility check.
- Allowed evolution:
  A future schema migration may standardize on UTC or epoch storage, but that must be a staged data migration rather than an incidental adapter change.
- Evidence:
  `backend/database.py` registers `_adapt_datetime_for_sqlite()`, and `backend/tests/test_database.py` treats `DeprecationWarning` as an error around representative `add_image()` / `add_tags()` writes.
- Last verified:
  2026-04-28 against targeted database tests.
- Related files:
  `backend/database.py`
  `backend/tests/test_database.py`
- Supersedes:
  The previous implicit reliance on sqlite3's deprecated default datetime adapter.
- Validation:
  `test_datetime_values_do_not_use_deprecated_sqlite_default_adapter`.

### ADR-AI-20260428-41: Filtered selection uses an immediate stateless chunk protocol, not a durable snapshot
- Status: active
- Area: frontend UX / API contract / performance
- Evidence tier: Tier 1
- Decision:
  Large filtered Gallery selection should prefer `POST /api/images/selection-token` followed by immediate `GET /api/images/selection-chunk` pages. The legacy `POST /api/images/selection-ids` endpoint remains the fallback and remains the required path for `sortBy=random`.
- Why:
  Returning every matching ID in one response recreates a large-library memory cliff. Stateless chunks reduce response size without pretending to be a durable background selection snapshot. Random ordering cannot be offset-paged because each chunk would re-randomize and duplicate or skip images.
- Do not "improve" this by:
  Using the chunk protocol for `random`, treating the token as resumable across scan/import/delete operations, or removing the legacy endpoint before all clients have a fallback.
- Allowed evolution:
  Replace this with a server-side snapshot/cursor protocol if selection becomes a long-running or resumable operation.
- Evidence:
  `backend/routers/images.py` exposes token/chunk response models, `backend/services/image_service.py` rejects random chunk tokens and validates decoded token scalar types as 400s, `backend/database.py` supports exact-match post-filter offset/limit, and `frontend/js/app.js` fetches filtered IDs in chunks with legacy fallback.
- Last verified:
  2026-04-28 against targeted selection router/database tests, JS syntax checks, API docs contract tests, and full `scripts/run_ci.py` validation.
- Related files:
  `backend/routers/images.py`
  `backend/services/image_service.py`
  `backend/database.py`
  `frontend/js/app.js`
  `tests/e2e/specs/smoke.spec.ts`
  `backend/tests/test_routers/test_images.py`
  `backend/tests/test_database.py`
- Supersedes:
  The previous mitigation where filtered selection still relied on one large `/api/images/selection-ids` response after the large-selection confirmation.
- Validation:
  `backend/tests/test_routers/test_images.py::TestSelectionIds`, `backend/tests/test_database.py::TestImageFiltering::test_get_filtered_image_ids_streams_post_filter_batches_with_optional_limit`, and `node --check frontend/js/app.js`.

### ADR-AI-20260428-42: Export preview data can page by selection token
- Status: active
- Area: API contract / frontend performance
- Evidence tier: Tier 1
- Decision:
  `POST /api/images/export-data` accepts either legacy `image_ids` or a `selection_token` with `offset` / `limit`. Filtered Gallery export previews use the token path when the current `filtered` selection still matches its recorded filter key. The export modal's limited-preview copy reports the preview window size, not the number of non-empty prompt/tag rows rendered into the textarea.
- Why:
  Capping the modal preview avoided UI lockups but still required clients to send large explicit ID payloads. Token-page export keeps the preview path aligned with backend filter semantics and avoids another hidden large-payload cliff.
- Do not "improve" this by:
  Sending both `image_ids` and `selection_token`, treating selection tokens as durable snapshots, or using token mode for `sortBy=random`.
- Allowed evolution:
  A future full export/download flow may stream pages server-side, but it should build on an explicit snapshot/job contract rather than pretending this immediate token is resumable.
- Evidence:
  `backend/routers/images.py` validates the mutually exclusive request shapes, `backend/services/image_service.py` resolves token export pages through the same post-filter offset code as selection chunks, and `frontend/js/app.js` chooses token export for current filtered selections while carrying an internal `preview_count` so UX copy cannot confuse returned prompt rows with the selected preview window.
- Last verified:
  2026-04-28 against targeted export router tests, export Playwright regressions, JS syntax checks, and full `scripts/run_ci.py` validation.
- Related files:
  `backend/routers/images.py`
  `backend/services/image_service.py`
  `backend/tests/test_routers/test_images.py`
  `frontend/js/app.js`
  `tests/e2e/specs/smoke.spec.ts`
- Supersedes:
  The previous export modal mitigation where large previews were capped but still always used explicit selected ID payloads.
- Validation:
  `backend/tests/test_routers/test_images.py::TestExportSelectionData`, `tests/e2e/specs/smoke.spec.ts` filtered/export preview regressions, `node --check frontend/js/app.js`, and `python3 scripts/run_ci.py`.

### ADR-AI-20260428-43: Indexed file save preflight is owned by the shared mutation helper
- Status: active
- Area: overwrite semantics / backend data lifecycle
- Evidence tier: Tier 1
- Decision:
  Save-capable features must use `save_and_reconcile_checked()` / `preflight_output_write()` for same-source, existing-file, directory, symlink, overwrite-intent, and indexed-row reconciliation semantics. Reader, Censor, and Obfuscation no longer keep feature-local overwrite preflight helpers.
- Why:
  The old shape duplicated the same destructive-write policy in feature services, making future save features likely to drift and making indexed overwrite refresh depend on caller discipline.
- Do not "improve" this by:
  Reintroducing `_ensure_overwrite_allowed()` in feature modules, checking `output.exists and not allow_overwrite` at the feature layer, or writing bytes before the shared preflight has run.
- Allowed evolution:
  The helper can grow richer return metadata or transactional temp-file writes, but destructive write policy must stay centralized.
- Evidence:
  `backend/services/indexed_file_mutation_service.py` now owns checked preflight and result metadata; Censor and Obfuscation call it directly; contract tests reject feature-local overwrite helpers.
- Last verified:
  2026-04-28 against indexed mutation contract tests and Reader/Censor/Obfuscation router slices.
- Related files:
  `backend/services/indexed_file_mutation_service.py`
  `backend/services/image_service.py`
  `backend/services/censor_service.py`
  `backend/obfuscation.py`
  `backend/tests/test_indexed_file_mutation_contract.py`
- Supersedes:
  The prior partially-centralized state where `save_and_reconcile()` refreshed indexed rows but callers still owned overwrite preflight.
- Validation:
  `backend/tests/test_indexed_file_mutation_contract.py`, `backend/tests/test_routers/test_obfuscation.py`, `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`.

### ADR-AI-20260428-44: `window.App` is sealed; feature modules use named bridges
- Status: active
- Area: frontend state / architecture
- Evidence tier: Tier 1
- Decision:
  `window.App` is created once by `frontend/js/app.js` and sealed immediately. Feature modules must not add or mutate `window.App.*`; Censor exposes its queue bridge via `window.CensorEdit.addToQueue`, while app-level callers use the stable `window.App.addToCensorQueue()` wrapper.
- Why:
  A giant mutable global service locator lets feature modules silently expand API surface and makes load order/state ownership regressions hard to detect.
- Do not "improve" this by:
  Adding feature-local fields back onto `window.App`, or bypassing the wrapper from Gallery/Similar/Artist features.
- Allowed evolution:
  Split `window.App` into narrower module APIs, but keep static tests that prevent feature modules from mutating shared globals.
- Evidence:
  `frontend/js/app.js` calls `Object.seal(window.App)`, `frontend/js/censor-edit.js` registers `window.CensorEdit.addToQueue`, and `backend/tests/test_frontend_contract.py` blocks feature-module `window.App.*` assignments.
- Last verified:
  2026-04-28 against frontend contract tests and JS syntax checks.
- Related files:
  `frontend/js/app.js`
  `frontend/js/censor-edit.js`
  `frontend/js/gallery.js`
  `backend/tests/test_frontend_contract.py`
- Supersedes:
  The previous Censor bridge that mutated `window.App._addToCensorQueue` at runtime.
- Validation:
  `backend/tests/test_frontend_contract.py`, `node --check frontend/js/app.js frontend/js/censor-edit.js frontend/js/gallery.js`.

### ADR-AI-20260428-45: Release manifests declare model artifact policy
- Status: active
- Area: release / update / model assets
- Evidence tier: Tier 1
- Decision:
  Release package manifests include `model_artifact_policy`, explicitly declaring that default app packages do not manage model payload files, that runtime models live under `data/models`, and which model paths are auto-download or optional release assets.
- Why:
  A model-free package can otherwise look complete to update tooling, and an accidental staged model binary could become updater-managed app content.
- Do not "improve" this by:
  Putting model binaries into default app/update manifests without explicit `include_model_payloads=True`, or relying on release notes alone to explain model delivery.
- Allowed evolution:
  Optional model packs may opt into model payload management with separate manifest semantics and artifact smoke tests.
- Evidence:
  `scripts/build_release_packages.py` writes `model_artifact_policy` and excludes non-doc `models/` payloads from default manifests; `backend/tests/test_release_build.py` validates the policy.
- Last verified:
  2026-04-28 against release build tests.
- Related files:
  `scripts/build_release_packages.py`
  `backend/tests/test_release_build.py`
  `docs/RELEASE_PACKS.md`
- Supersedes:
  The previous release manifest shape that tracked app managed paths but did not state model delivery assumptions.
- Validation:
  `backend/tests/test_release_build.py`.

### ADR-AI-20260428-46: Shared requirements must keep platform-specific wheels guarded

- Status: accepted
- Context:
  `run.bat`, `run.sh`, and the portable launcher all install from `backend/requirements.txt`. A Linux-generated lockfile can accidentally include Linux-only CUDA/NVIDIA/Triton wheels without markers, which makes Windows and macOS first-run installation fail before the app starts.
- Decision:
  Keep Linux CUDA/NVIDIA/Triton transitive pins guarded with `sys_platform == "linux"`, keep `uvloop` guarded away from Windows, and keep Linux ONNX Runtime on a Linux-only pin, use macOS-resolvable ONNX Runtime, OpenCV, and PyTorch pins, keep the Windows ONNX Runtime GPU and `triton-windows` pins Windows-only, and pin `triton-windows` to an actually published wheel version. Treat marker loss or an unresolvable platform pin in `backend/requirements.txt` or `backend/requirements-dev.txt` as a release/dev-onboarding blocker.
- Evidence:
  `backend/requirements.txt` and `backend/requirements-dev.txt` now guard `cuda-*`, `nvidia-*`, and `triton`, split ONNX Runtime, OpenCV, and PyTorch pins for Linux/Windows/macOS, and use a published `triton-windows` post-release pin; `backend/tests/test_release_build.py` validates both runtime and dev lock marker policy.
- Related files:
  `backend/requirements.txt`
  `backend/requirements-dev.txt`
  `backend/tests/test_release_build.py`
  `run.bat`
  `run.sh`
  `scripts/build_release_packages.py`
- Validation:
  `backend/tests/test_release_build.py`.

### ADR-AI-20260428-47: Selection tokens are valid only for filtered scope

- Status: accepted
- Context:
  Gallery range/visible/toggle operations rewrite selection scope locally. If stale `selectionToken` or `filterKey` survives after scope narrows to `visible` or `loaded`, later export paths can accidentally see a token that no longer represents the current selection semantics.
- Decision:
  `SelectionStore.cloneState()` owns the invariant: only `filtered` selections may retain `filterKey` and `selectionToken`; `visible` and `loaded` selections always clear both fields. Manual Sort resume banner rendering also treats a missing session as hidden instead of preserving stale banner text.
- Evidence:
  `frontend/js/stores/selection-store.js`
  `frontend/js/app.js`
  `frontend/js/manual-sort.js`
  `backend/tests/test_frontend_contract.py`
- Validation:
  `backend/tests/test_frontend_contract.py`.

### ADR-AI-20260428-48: Gallery batch actions separate filtered scope, index removal, and destructive disk delete

- Status: accepted
- Context:
  User smoke testing found the Gallery selection panel had collapsed important meanings: the primary action looked like visible-only selection, filtered selection was ambiguous, disk deletion was exposed as the obvious selected-file action, and Gallery had no selected move/copy entry point even though the backend already supported `/api/move`.
- Decision:
  Gallery selection must expose separate actions for `Select All Filtered`, `Invert All Filtered`, `Select Visible`, and `Invert Visible`. The safe default cleanup action is `Remove from Gallery`, which deletes only index rows through `/api/images/remove-selected`; the Delete key follows that safe removal semantic. Permanent file deletion remains available only as `Delete Files from Disk...` with explicit destructive copy and `confirm_delete_files=true`. Selected Gallery images must also expose `Move Selected...` and `Copy Selected...` using the shared `/api/move` operation contract.
- Evidence:
  `frontend/index.html`, `frontend/js/app.js`, `frontend/js/ui-refresh.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/routers/images.py`, `backend/services/image_service.py`, `backend/tests/test_routers/test_images.py`, `tests/e2e/specs/smoke.spec.ts`.
- Related invariants:
  Visible selection only covers currently rendered/loaded thumbnails; filtered selection covers every image matching the active Gallery filters. Removing from Gallery never touches disk files, including when invoked by the Delete key. Disk delete must be visually and verbally dangerous.
- Validation:
  Backend route tests pass. Browser smoke coverage for the touched Gallery/Manual Sort flows passes through the project wrapper: `node tests/e2e/scripts/run-playwright.mjs test specs/smoke.spec.ts -g "selection scope summary|filtered selection|gallery batch actions|gallery selected move|manual sort start"`.

### ADR-AI-20260428-49: Manual Sort start cannot silently replace a resumable session

- Status: accepted
- Context:
  User smoke testing confirmed that exiting Manual Sort mid-session and pressing Start after restart discards the saved progress and restarts from the first matching image.
- Decision:
  `/api/sort/start` defaults to preserving an unfinished active session and returns HTTP 409 unless the caller sends `replace_existing=true`. The frontend must not use the primary Start action as a hidden "new session" path when a resumable session exists; it checks `/api/sort/current`, shows saved progress, and resumes by default. Starting from the first matching image is allowed only after the user explicitly discards the saved session first.
- Evidence:
  `backend/routers/sorting.py`, `backend/services/sorting_service.py`, `frontend/js/app.js`, `frontend/js/manual-sort.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_routers/test_sorting.py`, `tests/e2e/specs/smoke.spec.ts`.
- Related invariants:
  Resume is the safe default; replacing saved Manual Sort progress is destructive session behavior and must be opt-in at both API and UI layers.
- Validation:
  `backend/tests/test_routers/test_sorting.py::TestSortSession::test_start_sort_session_requires_explicit_replace_when_unfinished`.

### ADR-AI-20260428-50: Generator family and batch export contracts must expose explicit status

- Status: accepted
- Context:
  User smoke testing reported Forge images appearing under WebUI and tag export feedback being too weak for serious SD workflows. Investigation found Forge detection only trusted limited parameter text, and `/api/tags/export-batch` returned a shape that frontend code could misread.
- Decision:
  WebUI-family parser logic detects Forge from structured generator signals such as PNG `Software`/`Source`, explicit Forge version fields, and Forge-style version strings; it must not scan arbitrary prompt text for the word `forge`. Batch tag export returns explicit `status`, `exported`, `skipped`, numeric `errors`/`error_count`, `error_messages`, and `total` so UI can distinguish success, skipped sidecars, partial success, and failure.
- Evidence:
  `backend/metadata_parser.py`, `backend/services/tagging_service.py`, `backend/tests/test_metadata_parser.py`, `backend/tests/test_routers/test_tags.py`, `frontend/js/app.js`.
- Related invariants:
  `forge` and `webui` are distinct user-facing generator buckets, but prompt words like `forge` or `forged armor` are not generator identity. Batch export errors and skipped files are counts plus messages, not truthy/falsy ambiguous fields.
- Validation:
  `backend/tests/test_metadata_parser.py`, `backend/tests/test_routers/test_tags.py::TestExportTagsBatch::test_export_batch_returns_normalized_frontend_contract_fields`.

### ADR-AI-20260429-51: Pro export, Auto-Separate execution settings, and metadata-resolving counts are explicit UX contracts

- Status: accepted
- Context:
  User smoke testing showed three regressions that shared the same root: important SD workflow semantics were hidden or implicit. Export only exposed weak prompt/tag paths, Auto-Separate hid move/copy safety choices in settings, and quick-import generator tabs could show WebUI/Forge zeroes while metadata was still pending.
- Decision:
  Export payloads and sidecars must expose SD-user content modes explicitly: prompt, negative, prompt+negative, A1111/Forge parameter block, tags, caption+tags, merged caption, and JSON. Sidecar overwrite behavior is an explicit `overwrite_policy`, and `skip` must report skipped files instead of looking like a generic failure. Auto-Separate must show file action mode plus safety toggles on the main panel before the execute button, not only in the settings modal. `/api/stats` must report `metadata_status`, `metadata_pending`, `scan_status`, and `scan_library_ready` so frontend generator tabs can mark provisional counts as resolving while metadata is pending or scan import has not made the library ready.
- Evidence:
  `backend/services/tag_export_service.py`, `backend/services/image_service.py`, `backend/services/sorting_service.py`, `backend/database.py`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/autosep.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `docs/API.md`.
- Related invariants:
  Move/copy is not a preference hidden behind a gear; it changes whether source files move. Quick-import generator counts are not authoritative until metadata pending reaches zero and the scan reports the library ready. Export modes are workflow contracts, not just textarea labels.
- Validation:
  `node --check frontend/js/app.js frontend/js/autosep.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`; targeted stats/frontend contract tests and export router tests.

### ADR-AI-20260429-52: Manual Sort primary Start resumes unfinished sessions

- Status: accepted
- Context:
  User smoke testing showed the earlier safeguard was still not enough: a confirmation to "start new" preserved API safety but left the primary UX path biased toward restarting, which feels like progress loss when the user simply relaunches and presses Start.
- Decision:
  Manual Sort's primary Start action is now a resume-first action when `/api/sort/current` reports an unfinished session. The confirmation copy says to resume instead of starting over; cancelling keeps the resume banner visible. A new first-image session must be reached by discarding the saved session first, not by a normal Start click. Backend `replace_existing=true` remains as an explicit escape hatch for callers that already performed a destructive discard decision.
- Evidence:
  `frontend/js/manual-sort.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/services/sorting_service.py`, `backend/tests/test_frontend_contract.py`, `tests/e2e/specs/smoke.spec.ts`.
- Related invariants:
  Persisted `current_index` is user progress. Resume is the default product semantic; replacement is destructive session state and must be explicit, not merely confirmed in a generic start flow.
- Validation:
  `backend/tests/test_frontend_contract.py::test_manual_sort_start_routes_unfinished_sessions_to_resume`; browser smoke test `manual sort start should resume unfinished session instead of starting over`.

### ADR-AI-20260429-53: Gallery Delete key and scan-time counts must follow safe/provisional semantics

- Status: accepted
- Context:
  Review found two remaining contract holes after the smoke-fix pass: the visible button semantics were safe, but the Delete keyboard shortcut still invoked permanent disk deletion; `/api/stats` exposed scan readiness, but the frontend only treated `metadata_pending` as provisional.
- Decision:
  The Gallery Delete key removes selected rows from the gallery index only, matching `Remove from Gallery`; permanent deletion remains behind the explicit `Delete Files from Disk...` action. Generator tab counts are resolving when metadata is pending or when scan is running/cancelling before `scan_library_ready`; WebUI/Forge zeroes must not be displayed as final in that state.
- Evidence:
  `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_frontend_contract.py`, `tests/e2e/specs/smoke.spec.ts`, `docs/API.md`.
- Related invariants:
  Keyboard shortcuts cannot be more destructive than the primary visible UX. Generator buckets are final only after both import and metadata resolution make the indexed library ready.
- Validation:
  `backend/tests/test_frontend_contract.py::test_gallery_delete_key_removes_from_gallery_not_disk`, `backend/tests/test_frontend_contract.py::test_metadata_resolving_chip_is_driven_by_stats_contract`, browser smoke tests for Gallery remove/delete and stale large-library loads.

### ADR-AI-20260429-54: Background/model lifecycle state belongs in services, not routers

- Status: accepted
- Area: backend architecture / release stability
- Context:
  The full-repo debt audit found two repeated router ownership leaks: Aesthetic/Artist background progress used router-local dictionaries and locks while Sorting/Tagging already exposed service-owned state, and model-manager preparation mixed inventory, external downloads, archive validation, and HTTP response mapping inside `routers/models.py`.
- Decision:
  Routers should translate HTTP contracts and schedule framework background tasks; services own lifecycle state, model inventory, external preparation side effects, and testable domain errors. Aesthetic and Artist progress now lives in their service instances. Model Manager now uses `ModelService`; `routers/models.py` only keeps the request model and error-to-HTTP conversion.
- Why:
  Two lifecycle patterns in one app make future background jobs inconsistent and hard to test. External model preparation also needs unit-testable business logic without importing FastAPI router concerns.
- Do not regress:
  Do not add new router-level job state dictionaries/locks or move model download/prepare branches back into a router just because it is quicker. Do not make service code raise FastAPI `HTTPException` for these paths.
- Allowed evolution:
  Sorting/Tagging compatibility handles can be retired gradually once frontend/API callers are migrated. `ModelService` can be split further by provider if model preparation grows.
- Evidence:
  `backend/services/aesthetic_service.py`, `backend/services/artist_service.py`, `backend/services/model_service.py`, `backend/routers/aesthetic.py`, `backend/routers/artists.py`, `backend/routers/models.py`, `backend/tests/test_release_build.py`, `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`.
- Validation:
  Targeted pytest for model prepare/status errors, artist progress seams, aesthetic score-all seam, and release guard tests.

### ADR-AI-20260429-55: External archives are validated with bounded extraction budgets

- Status: accepted
- Area: release security / model runtime preparation
- Context:
  The audit follow-up found several places that accepted external archives with path checks but no entry-count or uncompressed-size budget: Privacy YOLO model bundles, LSNet artist runtime zips, and self-update patch archives. The update worker also used Windows `os.kill(pid, 0)` process probing semantics that are not safe to rely on for update apply timing.
- Decision:
  Archive intake paths must validate normalized member names, reject traversal/absolute/drive-letter entries, cap entry counts, cap total uncompressed bytes, and only then extract/copy payloads. LSNet runtime downloads are pinned to a specific GitHub commit zip instead of `refs/heads/main.zip`. Windows update-worker PID probing uses a Windows process API helper instead of signal-zero probing. CI now also runs frontend JavaScript syntax checks before browser E2E.
- Why:
  This is a local app, but release and model/runtime preparation consume remote archives. A bad archive should fail as a controlled preparation/update error, not create Zip Slip writes, zip-bomb memory/disk pressure, or half-applied updates.
- Do not regress:
  Do not reintroduce `extractall()` guarded only by string `startswith()`, unbounded `ZipFile`/`tarfile` iteration, moving target branch zip URLs for runtime code, or Windows `os.kill(pid, 0)` as an updater liveness check.
- Allowed evolution:
  The numeric extraction budgets may be tuned if real release/model assets exceed them, but tests should continue to prove unsafe paths, too many entries, and oversized uncompressed payloads are rejected.
- Evidence:
  `backend/artist_identifier.py`, `backend/services/model_service.py`, `backend/services/update_service.py`, `backend/update_worker.py`, `frontend/js/manual-sort.js`, `scripts/run_ci.py`, `backend/tests/test_artist_identifier_runtime.py`, `backend/tests/test_model_service.py`, `backend/tests/test_update_worker.py`, `backend/tests/test_security_check.py`, `backend/tests/test_router_service_boundaries.py`, `backend/tests/test_release_build.py`.
- Validation:
  `python3 -m py_compile backend/update_worker.py backend/artist_identifier.py backend/services/model_service.py backend/services/update_service.py scripts/run_ci.py scripts/security_check.py`; `node --check frontend/js/manual-sort.js`; targeted pytest for update/model/artist/security/boundary/release guard tests.

### ADR-AI-20260429-56: Dynamic UI text must own its i18n binding state

- Status: accepted
- Area: frontend i18n / UI refresh stability
- Context:
  Final E2E validation found dynamic labels being clobbered by the global `ui-refresh.js` / `I18n.applyToDOM()` replay cycle. Auto-Separate changed the execute button title/aria state to Copy but the visible label stayed Move after `ui-refresh` rebuilt the button. Queue Solitaire copied Gallery filters, then the filter summary reverted to the static idle text because the element still carried `data-i18n="queueSolitaire.filterSummaryIdle"`. Manual Sort's resume button also passed the DOM click event as the optional session payload, making the direct Resume path report no saved session.
- Decision:
  Dynamic state-owned UI text must not keep stale static i18n bindings. If a state switch maps to another static translation, update the `data-i18n` key and visible text together. If the text is generated from runtime state, remove the static `data-i18n` binding before writing the generated text. Event listeners that call payload-accepting functions must wrap the call so DOM events are not mistaken for domain payloads.
- Why:
  The global translation observer is useful for static bilingual UI, but it replays translations after DOM child mutations. Dynamic state labels therefore need explicit ownership or E2E will see honest state in aria/title while visible copy silently regresses.
- Do not regress:
  Do not set dynamic text on elements that still carry an unrelated `data-i18n` key. Do not rely on `querySelector('[data-i18n]')` only when `ui-refresh` can rebuild buttons with `.ui-label`. Do not pass event handlers directly to functions whose first parameter is a domain object.
- Evidence:
  `frontend/js/autosep.js`, `frontend/js/manual-sort.js`, `frontend/js/queue-solitaire.js`, `tests/e2e/specs/smoke.spec.ts`, `tests/e2e/specs/manual-regression.spec.ts`.
- Validation:
  Targeted Playwright slice passed for Auto-Separate copy mode and Manual Sort resume paths (`4 passed`); Queue Solitaire regression passed (`1 passed`); full `python3 scripts/run_ci.py` passed with lock freshness, dependency security audit, frontend JS syntax, backend suite (`793 collected`), and Playwright E2E (`113 passed, 2 skipped`).

### ADR-AI-20260429-57: Tested repository seams are not dead code just because services still call `database.py`

- Status: superseded (2026-08-19)
- Superseded by: `backend/db_repos/` removed in `9a4663f`; `backend/tests/test_architecture_contract.py` forbids unadopted reintroduction. The keep-as-partial-seam decision was wrong — nothing in production imported it.
- Area: backend architecture / debt triage
- Context:
  The 2026-04-29 debt audit flagged `backend/db_repos/` as a deletion quick win because production services mostly still import `database.py` directly. Current workspace evidence contradicts that: repository helpers are referenced by pagination/path-equivalence tests and ADR-AI-20260428-25 lists `ImageRepository` cursor contract files as part of the opaque pagination invariant.
- Decision:
  Keep `backend/db_repos/` while it has active tests or recorded API/storage-contract evidence. Treat it as a partially adopted repository seam, not an unused folder. Future cleanup may either migrate more callers into it or retire it only after replacing/removing the tests and superseding the related ADR evidence.
- Why:
  Deleting a tested compatibility seam to reduce visual file count would break regression coverage and erase a documented path-equivalence contract. The real debt is incomplete adoption and duplicated access patterns, not simply file presence.
- Do not regress:
  Do not delete `backend/db_repos/` or remove its tests as a drive-by quick win. Do not add new untested repository wrappers that only mirror `database.py` without owning a contract.
- Allowed evolution:
  A deliberate repository migration can move service callers behind the seam. A deliberate deletion can happen later if repository tests are ported to the canonical DB/service layer and ADR-AI-20260428-25 is superseded.
- Evidence:
  `backend/db_repos/repositories/image_repo.py`, `backend/db_repos/repositories/base.py`, `backend/tests/test_db_repos_image_repo.py`, `backend/database.py`, `docs/AI_DECISION_LOG.md`.
- Validation:
  Static reference check found active imports in `backend/tests/test_db_repos_image_repo.py` and related ADR evidence before the quick-win deletion was rejected.

### ADR-AI-20260429-58: Move/copy consistency uses compensation plus DB transaction, not batch all-or-nothing

- Status: accepted
- Area: file lifecycle / database consistency
- Context:
  The debt audit correctly found that `move_image()` moved files before updating SQLite, and `copy_image()` wrote the copied file before three independent DB writes (`add_image`, tags, derived state). A crash or DB error could leave the file and index disagreeing. Batch move/copy already reports per-image partial failures, so making the whole batch all-or-nothing would be a user-visible semantic change.
- Decision:
  Keep per-image batch semantics, but make each image operation crash-safer. A move that cannot update SQLite after `shutil.move()` attempts to move the file back to the original path and reports whether rollback succeeded. A copy that cannot insert/copy indexed DB state removes the copied file. Copy DB writes now go through one transaction (`add_copied_image_with_state`) so copied row, tags, cached caption/aesthetic/embedding, and artist prediction succeed or roll back together.
- Why:
  SQLite and the filesystem cannot be made truly atomic together without a more invasive journal/recovery design. Compensation closes the common failure window while preserving the existing UI/API expectation that a batch can partially succeed and report per-file errors.
- Do not regress:
  Do not re-split copy state into independent `add_image` / `add_tags` / `copy_image_derived_state` transactions. Do not remove move rollback just because `shutil.move()` already succeeded. Do not turn batch move/copy into all-or-nothing without a new product decision and UX copy.
- Allowed evolution:
  A future recovery journal can cover process crashes between filesystem and DB steps. A future background job protocol can offer explicit transactional batches, but it must be a new API/UX contract rather than a silent behavior change.
- Evidence:
  `backend/image_manager.py`, `backend/database.py`, `backend/tests/test_image_manager.py`, `.plans/sd-image-sorter-release/docs/invariants.md`.
- Validation:
  Failure-injection tests cover DB-failed move rollback, DB-failed copy cleanup, and copied-row transaction rollback; existing move/copy route tests continue to pass.

### ADR-AI-20260429-59: Router service lifecycle uses shared lazy providers

- Status: accepted
- Area: backend router/service lifecycle
- Context:
  The debt audit found repeated router-level `_service = None` / `get_*_service()` / `set_*_service()` boilerplate and a concrete bug risk in `routers/sorting.py`: compatibility binding called `get_sorting_service()` during module import, which could create a default `SortingService` before `main.py` or tests injected the intended instance. `routers/tags.py` had the same eager compatibility pattern. The same pass also confirmed `GALLERY_MAX_LIMIT` was a dead config value because the images router/service already enforce their own request limits.
- Decision:
  Router-owned services should use the shared `ServiceProvider` helper for lazy creation, test injection, clearing, and optional state-binding hooks. Legacy module-level compatibility handles may remain temporarily, but Tagging/Sorting progress/session handles must be backed by lazy `MutableStateProxy` objects until an explicit service is requested or injected. Dead config/env knobs that no runtime caller reads should be removed instead of documented as supported configuration.
- Why:
  Per-router copies of the same lazy-singleton pattern drift quickly, and import-time service construction creates split-brain state and fragile test/application lifecycle order. A shared provider preserves FastAPI dependency injection ergonomics while making service replacement/clearing consistent. Dead config values are worse than no config because users can set them and see no behavior change.
- Do not regress:
  Do not call `get_*_service()` from router module top level to initialize compatibility state. Do not reintroduce ad-hoc `_service = None` provider clones for new routers. Do not reintroduce `SD_IMAGE_SORTER_GALLERY_MAX_LIMIT` unless the images API actually reads it and tests prove the contract.
- Allowed evolution:
  Once legacy router-level state access is gone, compatibility proxies can be removed entirely. `ServiceProvider` may grow only small lifecycle hooks; larger application-scoped service containers should be a deliberate FastAPI lifecycle refactor.
- Evidence:
  `backend/services/service_provider.py`, `backend/routers/aesthetic.py`, `backend/routers/artists.py`, `backend/routers/censor.py`, `backend/routers/images.py`, `backend/routers/prompts.py`, `backend/routers/similarity.py`, `backend/routers/sorting.py`, `backend/routers/tags.py`, `backend/routers/updates.py`, `backend/config.py`, `backend/image_manager.py`, `backend/tests/test_service_provider.py`, `backend/tests/test_routers/test_sorting.py`, `docs/TECHNICAL_DEBT_NOTES.md`.
- Validation:
  Provider/router targeted pytest passed for service provider, state compatibility, sorting compatibility, tags, prompts/censor/similarity/artists, updates, and images (`201 passed`); config/image-manager targeted pytest also passed; full `python3 scripts/run_ci.py` passed afterward (`800 passed, 5 skipped` backend; `113 passed, 2 skipped` Playwright).


### ADR-AI-20260429-60: Frontend listener debt gets local cleanup, not a global lifecycle rewrite

- Status: accepted
- Area: frontend event lifecycle / stability
- Context:
  The debt audit flagged a high add/remove listener imbalance. A full teardown lifecycle for `app.js`, `gallery.js`, and `censor-edit.js` is a Dangerous Refactor, but several isolated leaks were safe to reduce: Gallery preview zoom left document-level mouse handlers alive after the image modal closed, Queue Solitaire toolbar init could stack handlers if the exported initializer was called again, Folder Browser registered the same ready callback twice, and Reader/Obfuscator exposed init methods that could rebind drop/paste/click listeners.
- Decision:
  Apply local, idempotent cleanup only where the owner is clear. Gallery owns `_cleanupZoomHandlers()` and `hideModal('image-modal')` calls it on the canonical close path. Queue Solitaire, Image Reader, and Image Obfuscator use small `_toolbarInitialized` / `_eventsBound` guards. Folder Browser keeps one ready path.
- Why:
  These changes remove concrete duplicate-listener paths without inventing a cross-view lifecycle system or touching Censor canvas re-entry behavior.
- Do not regress:
  Do not re-add document-level Gallery zoom handlers without close cleanup. Do not call exported frontend `init()` functions in a way that stacks DOM listeners. Do not use this local cleanup as justification for a mechanical whole-app listener rewrite.
- Allowed evolution:
  A future frontend architecture pass can replace these local guards with explicit view init/teardown contracts after E2E coverage is strong enough.
- Evidence:
  `frontend/js/gallery.js`, `frontend/js/app.js`, `frontend/js/queue-solitaire.js`, `frontend/js/folder-browser.js`, `frontend/js/image-reader.js`, `frontend/js/image-obfuscate.js`.
- Validation:
  `node --check` passed for the touched frontend files; targeted/full validation should continue to cover Gallery modal close, Queue Solitaire, Reader paste, and Obfuscation workflows.

### ADR-AI-20260429-61: CI release guards use tracked release docs and isolated test databases

- Status: accepted
- Area: CI / release validation / test isolation
- Context:
  GitHub Actions failed on clean Linux, macOS, and Windows checkouts because a release-build test read root `AGENTS.md`, which is ignored and local-only. Linux also exposed `test_save_and_reconcile_checked_reports_target_existence` using the default SQLite path without initializing schema, which passed locally only when an existing developer DB happened to contain `images`.
- Decision:
  Release documentation guards must read tracked release/user-facing files only; local agent instructions are not part of a clean CI checkout or release contract. Database-touching tests must request the `test_db` fixture, even when they only expect an empty library, so schema exists and tests do not depend on developer runtime state.
- Why:
  CI must represent a fresh user/release checkout, not a Codex workspace with ignored helper files and pre-existing `data/images.db`. Tests that pass only because local runtime files exist are false confidence and block cross-platform release gates.
- Do not regress:
  Do not make CI or release tests depend on ignored workspace files such as `AGENTS.md`. Do not call database helpers from tests without either `test_db`, `test_db_with_images`, or an explicit service/test fixture that initializes schema.
- Evidence:
  `backend/tests/test_release_build.py`, `backend/tests/test_indexed_file_mutation_contract.py`, `.gitignore`, `.github/workflows/ci.yml`.
- Validation:
  Targeted pytest for `test_current_install_docs_match_python_312_floor` and `test_save_and_reconcile_checked_reports_target_existence` passes locally and matches the GitHub Actions failure signatures.

### ADR-AI-20260429-62: Playwright CI fixtures must be generated from tracked sources

- Status: accepted
- Area: CI / browser E2E / release validation
- Context:
  GitHub Actions also exposed hidden Playwright prerequisites: `tests/e2e/playwright.config.ts` pointed at ignored `tests/e2e/storage/onboarding-complete.json`, and `reader-live.spec.ts` expected a review dataset generated by an ignored `backend/.tmp/build_review_dataset.py` script. Clean CI checkouts therefore failed before exercising real UI behavior.
- Decision:
  Browser E2E state must be inline or generated from tracked code. The onboarding storage state is now defined in Playwright config, and the multi-generator reader dataset is generated by tracked `scripts/build_review_dataset.py` before `scripts/run_ci.py` starts Playwright.
- Why:
  Browser release gates are useless if they require developer-only artifacts. CI must be able to recreate every fixture from source in a clean checkout, while still keeping generated images out of release archives.
- Do not regress:
  Do not add `storageState` paths or E2E fixture dependencies that point at ignored files unless the CI runner creates them from tracked scripts first.
- Evidence:
  `scripts/build_review_dataset.py`, `scripts/run_ci.py`, `tests/e2e/playwright.config.ts`, `tests/e2e/specs/reader-live.spec.ts`, `backend/tests/test_release_build.py`.
- Validation:
  Release-build tests assert the tracked/generated Playwright input contract so future CI-only fixture drift is caught before GitHub Actions.

### ADR-AI-20260429-63: CI E2E must not depend on private local media fixtures

- Status: accepted
- Area: CI / browser E2E / scan progress contract
- Context:
  The clean Linux CI run passed the previous fixture-generation fixes but still exposed three hidden assumptions: a scan progress test assumed metadata callbacks could only happen after discovery was final, the artist E2E expected ignored `backend/favorites` media to be present and identifiable, the manual Auto-Separate / Manual Sort browser tests expected ignored `.tmp/manual-test` images to already exist, and the tagger runtime E2E assumed no asynchronous model-sync pass would close the advanced `<details>` element after the test opened it.
- Decision:
  Scan completion now emits a terminal metadata progress event with `total_final=true` after all metadata work drains, even if some metadata callbacks were truthfully emitted while discovery was still growing. Artist E2E skips when the clean tracked fixture library and available runtime cannot produce a non-`undefined` prediction. Manual Auto-Separate / Manual Sort E2E now creates its clean PNG fixtures before restoring DB rows. Tagger runtime E2E repeatedly opens the advanced details during the assertion window instead of racing the app's async sync pass.
- Why:
  Release CI must validate shipped behavior from tracked inputs only. Tests may use optional local/private media for stronger coverage, but they cannot fail clean checkouts when that media is absent or when an optional AI runtime produces no useful prediction for synthetic fixtures.
- Do not regress:
  Do not make required CI E2E depend on ignored media under `backend/favorites` or `.tmp`. If a browser test needs optional AI/media behavior, it must either seed tracked fixtures or explicitly skip with a useful reason.
- Evidence:
  `backend/image_manager.py`, `backend/tests/test_image_manager.py`, `tests/e2e/specs/manual-regression.spec.ts`, `tests/e2e/specs/tagger-runtime.spec.ts`.
- Validation:
  Targeted backend scan progress test and full backend suite cover the terminal metadata progress contract; Playwright config listing validates the browser spec syntax in this local WSL environment where Chromium runtime libraries are unavailable.

### ADR-AI-20260429-64: Launcher ONNX Runtime repair must show long pip work

- Status: accepted
- Area: launcher / dependency repair / first-run UX
- Context:
  Windows launchers run `backend/repair_onnxruntime.py --auto` immediately after dependency installation. On NVIDIA machines, that repair may install `onnxruntime-gpu[cuda,cudnn]`, which pulls roughly 1.4 GB of CUDA/cuDNN runtime wheels. Previously those pip calls used captured output, so the console could sit at `Checking Windows ONNX Runtime package state...` with no visible activity, making a healthy first run look frozen.
- Decision:
  Launcher-triggered ONNX Runtime repair must stream pip output and print the concrete repair action before any long install, while machine-readable `--json` mode stays quiet/captured. Hidden multi-minute dependency downloads during startup are not acceptable UX.
- Why:
  This is a local beginner-facing tool. If startup needs to download or reinstall a large runtime, the user must see what is happening and why instead of guessing whether the app crashed.
- Do not regress:
  Do not reintroduce captured-only pip output for `--auto` launcher repair. Do not hide CUDA/cuDNN runtime installation behind a generic readiness check message. Keep `--json` suitable for automation by not streaming progress there.
- Evidence:
  `backend/repair_onnxruntime.py`, `backend/tests/test_repair_onnxruntime.py`, `README.md`, `docs/RELEASE_PACKS.md`.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/test_repair_onnxruntime.py`.

### ADR-AI-20260429-65: Runtime lock includes small resolver-quiet compatibility deps

- Status: accepted
- Area: dependency lock / launcher UX / Windows install
- Context:
  A successful Windows first-run `pip install -r backend/requirements.txt` could still print scary resolver text such as `ERROR: pip's dependency resolver...`, because reused or previously packaged environments had `selenium` / `trio` installed while their small optional runtime dependencies were missing. The app setup completed, but normal users reasonably read that `ERROR` block as a failed install.
- Decision:
  Keep `websocket-client`, `sniffio`, `sortedcontainers`, and Windows-only `cffi`/`pycparser` in the runtime and dev locks. These packages close the observed `selenium` / `trio` resolver warning surface without changing app behavior. Release guard tests must keep them present and keep `cffi`/`pycparser` Windows/PyPy markers aligned with the upstream `trio` requirement.
- Why:
  First-run setup must look trustworthy. A local beginner-facing launcher should not show avoidable pip `ERROR` warnings after a successful dependency install.
- Do not regress:
  Do not remove these as "unused" just because app code does not import them. Do not regenerate the cross-platform lock in a way that drops platform markers for heavy Linux CUDA/Triton wheels or the Windows-only `cffi` closure.
- Evidence:
  `backend/requirements.in`, `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/tests/test_release_build.py`.
- Validation:
  `python3 scripts/check_lockfiles.py`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q tests/test_release_build.py::test_runtime_requirements_keep_platform_specific_wheels_guarded tests/test_release_build.py::test_dev_requirements_keep_platform_specific_wheels_guarded`.

### ADR-AI-20260429-66: Scan responsiveness protects large WSL/Windows libraries without hiding metadata work

- Status: accepted
- Area: scan performance / path semantics / frontend responsiveness
- Context:
  Large user libraries commonly contain 10,000-100,000 images on Windows/WSL-mounted drives. The scan path already used quick placeholder import plus background metadata parsing, but equivalent-path lookups used `LOWER(path)` for Windows/WSL case-insensitive matching without a matching expression index. Benchmarks against a 100,000-row temporary image table showed 200 equivalent-path lookups taking about 0.81s before the expression index and about 0.057s after it. The frontend also risked turning "library ready" into a thumbnail storm by immediately loading the normal gallery page size while metadata parsing was still active.
- Decision:
  Current behavior adds `idx_images_path_lower ON images(LOWER(path))` for fresh databases and migration `007_path_lookup_casefold_index` for existing databases. Scan progress callbacks are throttled to first/progress/error/final events instead of one backend lock update per image. The UI labels this as "Fast import (recommended)" / "快速导入（推荐）" with a short helper that says image info is kept while full bad-file checking is skipped. Scan modal helper/checkbox text should not use arbitrary word breaking because broken mixed-language terms look unpolished. Quick-import metadata parsing is metadata-only: PNG parsing skips non-metadata image payload chunks such as `IDAT`, and quick import does not run full Pillow image-data verification. Full non-quick scans keep image-data validation. Quick-import library-ready refresh loads a small gallery preview page (`SCAN_PREVIEW_PAGE_SIZE = 80`) while metadata continues, and scan completion performs one silent gallery refresh so resolved metadata appears without repeated gallery reloads during scanning.
- Why:
  The user-facing goal is not merely raw throughput; noob users should see that the app is working quickly, the gallery should not look stuck/reloading while a scan is running, and re-scanning already-known large libraries should avoid avoidable full-table path scans. Metadata is still parsed for changed/new files and skipped only when stored source size/mtime prove the file is unchanged. The tradeoff is explicit: quick import optimizes first-use responsiveness and may leave corrupt-but-metadata-readable files to be discovered later by thumbnail/detail loading, while non-quick scan remains the stricter validation path.
- Do not regress:
  Do not remove the `LOWER(path)` index while `_path_query_match_clause()` still emits `LOWER(path) IN (...)`. Do not rename quick import back to wording that sounds like metadata may be missing. Do not make quick import read/verify full PNG image payloads by default. Do not reintroduce per-image UI progress writes for large scans unless the frontend starts consuming per-file streaming updates. Do not refresh the full normal gallery page repeatedly during active quick-import metadata parsing.
- Allowed evolution:
  The preview page size may be tuned with real user hardware data. A future scan-aware thumbnail scheduler may replace the fixed preview limit if it can keep first-use gallery feedback without starving metadata parsing.
- Evidence:
  `backend/migrations/007_path_lookup_casefold_index.py`, `backend/migrations/_schema_common.py`, `backend/image_manager.py`, `backend/metadata_parser.py`, `frontend/js/app.js`, `backend/tests/test_database.py`, `backend/tests/test_image_manager.py`, `backend/tests/test_metadata_parser.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `python3 -m py_compile backend/image_manager.py backend/metadata_parser.py backend/migrations/007_path_lookup_casefold_index.py backend/migrations/_schema_common.py`; `node --check frontend/js/app.js`; targeted scan/database/frontend/metadata-parser pytest; broader `python3 -m pytest -q --capture=no tests/test_metadata_parser.py tests/test_database.py tests/test_image_manager.py tests/test_db_repos_image_repo.py tests/test_frontend_contract.py` passed (`203 passed`). Temporary benchmark confirmed the casefold index query plan uses `idx_images_path_lower`; `/mnt/l` 5000-image quick scan measured about 12.65s first scan and 3.57s unchanged re-scan; quick import parsed metadata `5000/5000`; unchanged re-scan scheduled `0` metadata jobs.

### ADR-AI-20260429-67: Gallery selection panel exposes common actions first

- Status: accepted
- Area: gallery selection UX / destructive action visibility / bilingual layout
- Context:
  Current Gallery selection mode previously exposed many batch buttons in the left sidebar at once: filtered/visible selection tools, invert actions, move/copy, export variants, Censor edit, remove-from-gallery, and delete-from-disk. On desktop sidebars this made selection mode feel noisy and annoying, and long bilingual labels increased the risk of broken button layout.
- Decision:
  Current behavior keeps the common path visible: compact `Visible` / `All Filtered` selection buttons plus `Move`, `Copy`, and `Censor`. Lower-frequency actions (`Invert`, prompt/tag/sidecar export) and destructive or semi-destructive actions (`Remove from Gallery`, `Delete from Disk`) live inside the collapsed `More actions` section. The visible selection copy is intentionally shorter in both English and `zh-CN`; scope text is also shortened and ellipsized in the sidebar. The `More actions` section auto-collapses when selection mode is disabled, the panel is hidden, or there is no active selection.
- Why:
  Beginner users need a calm, obvious path after selecting images, not a wall of buttons. Power-user actions still exist, but they no longer occupy prime space or sit next to common actions by default. Keeping destructive disk delete behind an extra expansion step also matches the product rule that dangerous actions should be harder to misclick than common actions.
- Do not regress:
  Do not put export, invert, remove, and disk-delete actions back into the always-visible selection sidebar without a new UX decision. Do not lengthen the visible selection labels in a way that breaks the desktop sidebar. Keep `Delete from Disk` visually and structurally separated from move/copy/censor.
- Allowed evolution:
  The exact common-action set can change after user testing, but the panel should preserve progressive disclosure: common actions first, advanced/dangerous actions behind an explicit expansion.
- Evidence:
  `frontend/index.html`, `frontend/css/ui-refresh.css`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `tests/e2e/specs/smoke.spec.ts`, `tests/e2e/specs/manual-regression.spec.ts`, `docs/AI_PRINCIPLES.md` principles 5, 6, and 8.
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `python3 -m pytest -q --capture=no tests/test_frontend_contract.py` from `backend/` (`12 passed`); static selection-panel DOM check confirmed 6 visible buttons including `Clear` and 7 collapsed buttons. After populating the local `.tools` Playwright runtime cache, `node tests/e2e/scripts/run-playwright.mjs test specs/smoke.spec.ts -g "context menu|selection actions|selection scope summary|gallery batch actions"` passed the broader touched slice (`4 passed`).

### ADR-AI-20260429-68: Gallery right-click menu is a single-image workflow shortcut, not a destructive cleanup panel

- Status: accepted
- Area: gallery context menu / local workflow UX / destructive action visibility
- Context:
  After the selection sidebar was simplified, the Gallery right-click menu still had too few useful actions for desktop users: it mostly exposed folder/path/filter/censor helpers, while common single-image work like previewing, selecting, moving, copying, reading metadata, or sending the image into Prompt Helper required leaving the immediate image context. At the same time, adding every batch/destructive action would recreate the same overload problem in a different surface.
- Decision:
  Current behavior treats the Gallery image context menu as a single-image quick workflow menu. It exposes `Preview`, `Select Image` / `Deselect Image`, `Move`, `Copy`, `Send to Censor`, `Prompt Helper`, `Read Metadata`, `Filter by Checkpoint` when a checkpoint exists, `Open in Folder`, and `Copy Path`. Single-image move/copy routes through the existing `/api/move` contract via `moveOrCopyGalleryImages(..., { source: 'context' })`, preserving the same confirmation and recent-folder behavior as selected batch move/copy. Permanent disk delete is intentionally not added to the right-click menu.
- Why:
  Desktop users expect right-click to accelerate work on the item under the pointer. These actions are all directly about the clicked image and map to existing app workflows. Permanent deletion remains outside this menu because it is too easy to trigger from a contextual click and the product rule says dangerous actions must be harder to misclick than common actions.
- Do not regress:
  Do not add `Delete from Disk` to the normal Gallery image context menu without a new explicit destructive-action UX decision. Do not fork a separate move/copy backend path for context-menu actions. Do not let the context menu become a second always-full batch sidebar; keep actions grouped and image-scoped.
- Allowed evolution:
  A future advanced settings toggle could expose extra context actions for pro users, but destructive disk deletion should still require stronger friction than an ordinary right-click menu item.
- Evidence:
  `frontend/js/gallery.js`, `frontend/js/app.js`, `frontend/css/styles.css`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_frontend_contract.py`, `tests/e2e/specs/smoke.spec.ts`, `docs/AI_PRINCIPLES.md` principles 6 and 8.
- Validation:
  `node --check frontend/js/gallery.js`; `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `cd backend && python3 -m pytest -q --capture=no tests/test_frontend_contract.py` (`13 passed`). After populating the local `.tools` Playwright runtime cache with `libnspr4`, `libnss3`, and `libasound2t64` `.deb` packages, `node tests/e2e/scripts/run-playwright.mjs test specs/smoke.spec.ts -g "context menu|selection actions|selection scope summary|gallery batch actions"` passed (`4 passed`). `.tools/` is ignored because these local runtime packages are machine cache, not source.

### ADR-AI-20260429-69: PNG quick import must reject structurally truncated files without full pixel decode

- Status: accepted
- Area: scan performance / data quality / quick import semantics
- Context:
  The scan speed work made quick import skip full Pillow image-data validation so large first scans can show the library sooner. A regression test caught that a truncated PNG could be accepted when the parser treated the fast metadata path as optional and fell back to Pillow after structural errors; Pillow can still expose dimensions/metadata for some cut-off PNGs unless `verify()` is run.
- Decision:
  Current behavior keeps quick import metadata-only for PNG pixel data, but the PNG fast path is authoritative for PNG structure. It reads only chunk headers and metadata chunks, skips large image chunks, and requires valid chunk bounds plus an `IEND` chunk. If the fast path detects invalid signature, truncation, or missing `IEND`, the file is reported as unreadable instead of falling back to Pillow and entering the library. Full import can still run Pillow `verify()` when the user disables quick import.
- Why:
  Users need quick import to be fast, but they should not see broken/truncated files silently indexed as valid gallery items. Chunk-structure validation is a cheap middle ground: it preserves the first-scan speed path while catching common file truncation.
- Do not regress:
  Do not reintroduce silent Pillow fallback for PNG fast-path structural errors. Do not make quick import do full pixel decode by default just to catch truncation. Keep the UI wording clear that quick import skips full bad-file validation, not all validation and not metadata parsing.
- Allowed evolution:
  A future parser can add similarly cheap structure checks for other formats or a separate advanced integrity-scan action, but that should be exposed as a deliberate user choice because it costs scan time.
- Evidence:
  `backend/metadata_parser.py`, `backend/image_manager.py`, `backend/tests/test_metadata_parser.py`, `backend/tests/test_routers/test_sorting.py`, `docs/AI_DECISION_LOG.md` ADR-AI-20260429-66.
- Validation:
  Targeted pytest passed for mixed-root bad PNG reporting and PNG fast-path validation: `python3 -m pytest -q --capture=no backend/tests/test_routers/test_sorting.py::TestScan::test_scan_mixed_root_skips_truncated_and_reports_filenames backend/tests/test_metadata_parser.py::TestMetadataParserBase::test_parse_png_text_metadata_uses_fast_path_without_pillow_open backend/tests/test_metadata_parser.py::TestMetadataParserBase::test_parse_png_validation_still_runs_verify_open` (`3 passed`).

### ADR-AI-20260430-70: Launcher dependency install hides platform-marker noise but keeps progress

- Status: accepted
- Area: launcher / dependency install UX / portable release
- Context:
  Windows portable first-run installs from the shared cross-platform `backend/requirements.txt`. Correct platform markers intentionally keep Linux CUDA/Triton and macOS-only wheels out of Windows installs, but pip prints one `Ignoring ... markers ... don't match your environment` line for every skipped dependency. A successful install can therefore show a long wall of irrelevant package-resolution text before the app starts, which looks broken to normal users.
- Decision:
  Launcher dependency installs now run through `backend/launcher_pip.py`, which streams meaningful pip progress while filtering only platform-marker `Ignoring ... don't match your environment` lines. Keep real progress (`Collecting`, `Downloading`, installs), warnings, and errors visible. `run-portable.bat`, generated portable launchers, `run.bat`, and `run.sh` must use this wrapper for first-run requirements installation.
- Why:
  ADR-AI-20260429-64 remains valid: long dependency work must not be hidden. The fix is not silent install; it is readable install. Users should see that setup is doing work, without being forced to parse irrelevant cross-platform marker noise.
- Do not regress:
  Do not route launcher requirements installation back to raw `pip install -r backend/requirements.txt`. Do not use fully quiet pip output for long first-run setup. Do not broaden the filter to hide actual pip warnings/errors or package download/install progress.
- Evidence:
  `backend/launcher_pip.py`, `run-portable.bat`, `run.bat`, `run.sh`, `scripts/build_release_packages.py`, `backend/tests/test_launcher_pip.py`, `backend/tests/test_release_build.py`.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp pytest -q backend/tests/test_launcher_pip.py backend/tests/test_release_build.py::test_write_portable_launcher_uses_clean_crlf_endings`.

### ADR-AI-20260430-71: Scan progress should not show ETA while image discovery is still growing

- Status: accepted
- Area: scan progress UX / desktop scan modal / bilingual layout
- Context:
  During large folder imports, the scan total can keep increasing while the app is still walking the folder. Showing a normal countdown ETA in that phase makes the estimate jump whenever more images are discovered, which looks fake to users. The same scan modal also regressed visually in Chinese: the short `高级选项` advanced-options label could break with only the last character on a new line while unused space remained elsewhere in the summary row.
- Decision:
  Scan import/discovery progress now avoids ETA and uses discovery wording instead: the UI shows how many images have been found and that the folder is still being counted. ETA is only shown after the backend reports a stable total and the UI is displaying the metadata backfill phase. Metadata ETA tracker keys include the metadata total so old rate samples are discarded if the backend discovers a larger metadata denominator. The scan modal's advanced-options label is treated as an unbreakable short label; the hint text is the flexible part of that row.
- Why:
  A beginner-facing local tool should not present unstable math as a precise promise. It is better to be honest that the app is still counting the folder than to show a countdown that changes every time more images appear. Short UI labels in Chinese and English should also look intentional on desktop, not split as single orphan characters.
- Do not regress:
  Do not show `progress.eta` for scan import/discovery while `total_final` is false or while the folder total can still grow. Do not let the scan advanced summary label use arbitrary word breaking. If future scan phases add ETA, gate it on a stable denominator and use separate progress tracker scope keys so old samples do not leak across phases.
- Evidence:
  `frontend/js/app.js`, `frontend/css/ui-refresh.css`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_frontend_contract.py`, `docs/AI_PRINCIPLES.md` principles 4, 5, and 6.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py::test_scan_modal_advanced_summary_does_not_break_chinese_label backend/tests/test_frontend_contract.py::test_scan_progress_eta_is_only_shown_for_stable_metadata_work`; `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`.

### ADR-AI-20260430-72: Gallery batch selection means current filtered result set, not visible DOM thumbnails

- Status: accepted
- Area: gallery selection UX / filter contract / desktop layout
- Context:
  The Gallery selection sidebar exposed `Visible`, `All Filtered`, and a `More actions` hamburger. This matched implementation terms but not the user's mental model: normal users expect “all” to mean all images currently shown by the Gallery filters, including images not yet scrolled into the DOM. A concrete bug also proved the contract was fragile: `All Filtered` could fail when the frontend sent an empty `aspectRatio` string and the backend validated it as an invalid value.
- Decision:
  The primary Gallery selection action is now “select all current filter matches.” The `Visible` button and the `More actions` hamburger are removed from the user-facing panel. Batch controls are grouped as range, common actions, export, and remove/danger sections. Empty or unknown `aspectRatio` values are normalized to no filter in saved frontend filter state, frontend request payloads, and backend token/selection contracts.
- Why:
  A desktop sorting tool should use user-facing range language, not DOM/virtualization jargon. The sidebar should present the workflow in the order users think about it: choose the target set, act on it, export if needed, then use dangerous removal actions only deliberately.
- Do not regress:
  Do not reintroduce `Visible` as a primary Gallery batch button. Do not put export/remove/delete behind a generic hamburger again unless a new explicit UX decision supersedes this one. Do not treat empty or unknown aspect-ratio values from stale frontend state as invalid selection filters.
- Allowed evolution:
  A future advanced/preferences surface can expose visible-page selection for power users, but the default Gallery sidebar should keep “all” tied to the current filtered Gallery result set.
- Evidence:
  Explicit user instruction on 2026-04-30; `frontend/index.html`, `frontend/css/ui-refresh.css`, `frontend/js/app.js`, `backend/services/image_service.py`, `backend/tests/test_frontend_contract.py`, `backend/tests/test_routers/test_images.py`.
- Supersedes:
  ADR-AI-20260429-67's decision to keep advanced actions behind an expansion in the selection sidebar.
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/ui-refresh.js`; `node --check frontend/js/autosep.js`; `node --check frontend/js/manual-sort.js`; `node --check frontend/js/stores/filter-store.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py backend/tests/test_routers/test_images.py::TestSelectionIds backend/tests/test_routers/test_images.py::TestDeleteSelectedImages` (`34 passed`); `node tests/e2e/scripts/run-playwright.mjs test specs/smoke.spec.ts -g "selection scope summary|filtered selection|gallery selected move and copy|export modal|batch sidecar"` (`7 passed`); `python3 scripts/check_lockfiles.py`.

### ADR-AI-20260430-73: Gallery disk removal moves files to OS Trash, never silent permanent unlink

- Status: accepted
- Area: file lifecycle / destructive action UX / cross-platform behavior
- Context:
  The Gallery “Delete from Disk” action permanently unlinked files. The user explicitly rejected this because normal desktop users expect file deletion to go to the computer's Trash / Recycle Bin and because permanent deletion is too harsh for a local image-management tool.
- Decision:
  The user-facing action is now “Move to Trash.” Backend deletion uses `send2trash` to route files through the operating system Trash / Recycle Bin / wastebasket where supported, removes the gallery row only after the trash move succeeds, and reports per-image failures. The backend must not silently fall back to permanent deletion when trash support is unavailable.
- Why:
  Dangerous actions must be harder to regret than common actions. “Remove from Gallery” remains the non-file-destructive cleanup action; “Move to Trash” is still destructive but recoverable through the OS.
- Do not regress:
  Do not use `Path.unlink()` / permanent delete for the Gallery batch disk-removal path. Do not label the action “Delete from Disk” if it is recoverable trash movement. Do not add this destructive action to the normal right-click image context menu without a new explicit UX decision.
- Allowed evolution:
  The response contract can expose richer platform/trash diagnostics, and the UI can add recovery guidance. A future dedicated “permanently delete” action would require explicit user approval, stronger confirmation, and separate API semantics.
- Evidence:
  Explicit user instruction on 2026-04-30; `backend/services/image_service.py`, `backend/routers/images.py`, `backend/requirements.in`, `backend/requirements.txt`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_routers/test_images.py`.
- Validation:
  `python3 -m py_compile backend/services/image_service.py backend/routers/images.py`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py backend/tests/test_routers/test_images.py::TestSelectionIds backend/tests/test_routers/test_images.py::TestDeleteSelectedImages` (`34 passed`); `python3 scripts/check_lockfiles.py`.

### ADR-AI-20260430-74: Export buttons must disclose output shape before users commit

- Status: accepted
- Area: export UX / LoRA training workflow / bilingual copy
- Context:
  The Gallery selection sidebar used short labels `Prompts`, `Tags`, and `Sidecars`. Those labels did not tell users what file/text format would be produced. The user explicitly called out two real workflows: LoRA training users often need one same-name `.txt` caption file per image, while other users need prompt lists separated by blank lines or numbered per image.
- Decision:
  Gallery batch export now exposes two user-facing entry points: `Text / CSV...` for previewable prompt/tag/JSONL/CSV text exports, and `Training .txt files...` for one-file-per-image caption/prompt outputs. The text export modal includes per-format descriptions and adds a numbered prompt-list format. The `.txt` export modal defaults to `caption_merged` because that is the most useful LoRA-dataset starting point, while still allowing prompt-only, tags-only, A1111 parameter block, and JSON outputs.
- Why:
  Export is not one thing. Users should know whether they will get clipboard text, one downloaded text/CSV file, or many same-name `.txt` files before they click the final action.
- Do not regress:
  Do not reduce the Gallery export buttons back to unexplained `Prompts`, `Tags`, or `Sidecars`. Do not remove same-name `.txt` training export from the main batch selection flow. Do not hide output-format details until after the export is already run.
- Allowed evolution:
  Export presets, examples, and pro templates are allowed as long as the first screen still makes output shape clear for beginners.
- Evidence:
  Explicit user instruction on 2026-04-30; `frontend/index.html`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/services/tag_export_service.py`, `backend/tests/test_frontend_contract.py`, `tests/e2e/specs/smoke.spec.ts`.
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/ui-refresh.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py` (`18 passed` inside the combined `34 passed` targeted run); `node tests/e2e/scripts/run-playwright.mjs test specs/smoke.spec.ts -g "selection scope summary|filtered selection|gallery selected move and copy|export modal|batch sidecar"` (`7 passed`).

### ADR-AI-20260430-75: Saved tool filters must be explained without “scope” jargon

- Status: accepted
- Area: Auto-Separate / Manual Sort filter UX / bilingual wording
- Context:
  The saved-filter status text said things like “saved scope,” “synced from Gallery,” and “keep saved scope.” The user explicitly said this was not understandable, even to them.
- Decision:
  User-facing wording now describes the behavior as copying and using a saved set of filters. The key mental model is: the tool uses the filters shown in that tool, and later Gallery filter changes are not copied automatically. Buttons now say “copy current Gallery filters” / “continue using these filters” instead of “use/resync/keep scope,” and implementation fallbacks/HTML defaults avoid showing “saved scope” before i18n loads.
- Why:
  “Scope” is an implementation concept. Users need to know whether the tool will use the current Gallery view or an older saved filter set.
- Do not regress:
  Do not reintroduce `作用域` / “scope” in user-facing saved-filter copy. Do not use “sync” as the main button language when the actual action is copying the current Gallery filters into the tool.
- Allowed evolution:
  A compact visual diff between Gallery filters and tool filters would be useful, as long as the copy still states the current behavior directly.
- Evidence:
  Explicit user instruction on 2026-04-30; `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `frontend/js/autosep.js`, `frontend/js/manual-sort.js`, `frontend/index.html`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/ui-refresh.js`; `node --check frontend/js/autosep.js`; `node --check frontend/js/manual-sort.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py` (`18 passed` inside the combined `34 passed` targeted run).

### ADR-AI-20260430-76: Scan ETA must use a real counted image total and separate metadata totals

- Status: accepted
- Area: scan progress UX / backend progress contract / large-library first-use experience
- Context:
  ADR-AI-20260430-71 removed unstable scan ETA while the folder total was still growing. The user correctly rejected that as incomplete: the product still needs to tell users roughly how long a 10,000-100,000 image import will take near the start of the scan, not simply hide the estimate. The old payload also allowed metadata backfill progress to overwrite the main `processed` / `total` image-import counters, which made scan progress and ETA appear to jump or regress.
- Decision:
  Scans now perform a lightweight count pass over image files before import/metadata work. The count pass emits a `counting` phase, then import begins with `total_final=true` and a real image denominator. Frontend import ETA is allowed once the counted total is known and at least some images have been processed. Metadata backfill has separate `metadata_processed`, `metadata_total`, and `metadata_total_final` fields; metadata ETA is shown only after that metadata denominator is final. Frontend progress trackers use separate scope keys for counted import, growing metadata, and final metadata so old rate samples do not leak across phases.
- Why:
  Users deciding whether to keep the app open need an early, honest estimate. Hiding ETA avoids fake math but does not solve the user problem. Counting filenames first gives a real denominator without parsing image metadata twice, while separating metadata totals prevents background detail work from corrupting the visible import count.
- Do not regress:
  Do not show ETA from a denominator that is still growing. Do not let metadata callbacks overwrite the main image import `processed` / `total` counters. Do not remove the count phase unless a replacement provides an equally real early denominator. If future optimizations avoid the second directory walk, they must preserve the same progress semantics.
- Supersedes:
  Supersedes the part of ADR-AI-20260430-71 that said scan ETA is only shown for stable metadata work. ADR-AI-20260430-71 remains valid for the advanced-options label layout and the rule that ETA must not use a growing denominator.
- Evidence:
  Explicit user instruction on 2026-04-30; `backend/image_manager.py`, `backend/services/sorting_service.py`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_routers/test_sorting.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `python3 -m py_compile backend/image_manager.py backend/services/sorting_service.py`; `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py backend/tests/test_routers/test_sorting.py::TestScan::test_scan_progress_counts_total_before_import_and_keeps_metadata_separate` (`19 passed`).

### ADR-AI-20260430-77: Missing-file repair is user-triggered background path reconnection, not automatic scanning

- Status: accepted
- Area: path identity / missing-file UX / background task behavior / large-library performance
- Context:
  Users may move or delete original image files outside the app. Current library rows store source paths, so an external move makes the old row look missing and a later scan of the new folder can import the file as a new row. The user asked whether a repair flow would cost time, hurt performance, or block work, and then explicitly asked that it run in the background.
- Decision:
  The app now exposes a user-triggered `Find Missing` / `找回图片` flow. The user chooses the folder or drive to search; the backend runs a separate background task with independent progress and cancellation under `/api/images/reconnect-missing/*`. The task only updates SQLite library records when a safe match is found. It does not move, delete, or edit image files. Matching prefers filename + size + modification time, and only reads image pixels for content fingerprint confirmation when an uncertain candidate has a stored fingerprint and the user leaves the safety option enabled. Ambiguous matches and already-indexed target-path conflicts are counted but not auto-applied, so the repair task cannot create duplicate rows for the same found file path.
- Why:
  This matches normal desktop expectations: beginners need a visible repair action when files were moved, but the app must not unexpectedly scan whole disks or mutate files. Large-library users can keep browsing while repair runs, and advanced users can choose a wider search scope when they really do not know where files went.
- Do not regress:
  Do not make missing-file repair run automatically on every app start or gallery refresh. Do not combine it with normal import scanning in a way that makes ordinary folder scans slower. Do not auto-update ambiguous duplicate-name matches or reconnect onto a path that is already represented by a different gallery row. Do not move/delete files as part of this repair path. Do not use “sync” wording if the action is only reconnecting stored source paths.
- Allowed evolution:
  A future UI can add a review list for ambiguous matches, search previously scanned folders first, and provide clearer time estimates for very large drives. A future deeper mode can compute fingerprints more broadly, but it must remain opt-in, cancellable, and honest about disk work.
- Evidence:
  User discussion on 2026-04-30; `backend/database.py`; `backend/services/image_service.py`; `backend/routers/images.py`; `backend/tests/test_reconnect_missing_files.py`; `frontend/index.html`; `frontend/js/app.js`; `frontend/js/folder-browser.js`; `frontend/js/lang/en.js`; `frontend/js/lang/zh-CN.js`.
- Validation:
  `python3 -m py_compile backend/database.py backend/services/image_service.py backend/routers/images.py backend/tests/test_reconnect_missing_files.py`; `node --check frontend/js/app.js frontend/js/folder-browser.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_reconnect_missing_files.py` (`5 passed`); `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py` (`18 passed`); targeted delete/missing-file regression tests (`5 passed`).

### ADR-AI-20260430-78: Export labels name the output shape, not internal feature names

- Status: accepted
- Area: export UX / bilingual terminology / SD-user workflow copy
- Context:
  ADR-AI-20260430-74 fixed the bigger export-shape problem, but the follow-up labels still used mixed wording such as `Prompt Sheet`, `Caption Files`, and old sidecar/export terms. The user explicitly asked for wording that noob users can understand without hiding pro SD terms: `Prompt text`, `Negative prompt`, `Tags`, `LoRA caption file`, `Sidecar caption / same-name .txt`, `Metadata / generation info`, `WD14 auto tagging`, and `SAM3 text segmentation`.
- Decision:
  Gallery export now has two compact entry points: `Combined Export...` / `合并导出...` for previewable one-file or clipboard outputs, and `Same-name .txt...` / `同名 .txt...` for one caption file per image. The export modal labels describe output format directly: `Prompt text`, `Prompt text + filenames`, `Negative prompt`, `Prompt + Negative`, `Tags list`, `Merged caption lines`, `CSV table`, `JSONL`, and `A1111 / Forge block`. The same-name export modal uses `LoRA caption file`, `Prompt text`, `Tags`, `Negative prompt`, `Caption + Tags`, `A1111 / Forge block`, and `JSON`. Explanations live in helper/preview text rather than long button labels. Related technical terms were aligned to `Metadata / Generation Info`, `WD14 Auto Tagging`, and `SAM3 Text Segmentation` in the touched UI surfaces.
- Why:
  Export decisions are about file shape, not feature names. A beginner should know before clicking whether they will get one merged text/table file or many same-name `.txt` files, while pro SD users still need the exact terms they recognize for LoRA training and metadata workflows. Short button labels protect the desktop layout; longer explanations belong in preview/helper text.
- Do not regress:
  Do not rename the two main Gallery export actions back to vague `Prompts`, `Tags`, `Sidecars`, `Prompt Sheet`, or generic `Caption Files` without a new superseding decision. Do not put long explanations inside the sidebar buttons. Do not translate SD terms so aggressively that `Prompt`, `Negative prompt`, `Tags`, `LoRA`, `WD14`, `SAM3`, or `Metadata` become less precise.
- Supersedes:
  Supersedes only the user-facing label names in ADR-AI-20260430-74. ADR-AI-20260430-74 remains valid for the export semantics: output shape must be disclosed before commit, and one-file-per-image `.txt` remains a main batch flow.
- Evidence:
  Explicit user instruction on 2026-04-30; `frontend/index.html`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_frontend_contract.py`, `tests/e2e/specs/smoke.spec.ts`.
- Validation:
  `node --check frontend/js/app.js`; `node --check frontend/js/gallery.js`; `node --check frontend/js/modules/utils/errors.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_frontend_contract.py` (`18 passed`); `npm --prefix tests/e2e test -- smoke.spec.ts -g "export modal|batch sidecar export"` (`3 passed`); `npm --prefix tests/e2e test -- smoke.spec.ts -g "gallery context menu"` (`1 passed`).

### ADR-AI-20260430-79: Scan-driven gallery refresh must not auto-page without user scroll

- Status: accepted
- Area: scan progress UX / gallery refresh performance / large-library browsing
- Context:
  After quick-import library readiness, the frontend refreshes the Gallery so users can start browsing while metadata continues in the background. A regression test caught that returning to Gallery from another view could trigger an initial refresh, an automatic load-more request, and the final scan-complete refresh. For large libraries this creates extra `/api/images` work and makes the UI feel like it is still loading even when the user only expected the first visible page.
- Decision:
  Refreshes caused by scan progress now suppress the immediate automatic load-more check. The Gallery still attaches its pagination listener, so explicit user scrolling can load more images normally. The scan flow may still do one early library-ready refresh and one final completion refresh, but it must not auto-page extra results just because the grid is near the viewport after an automatic scan refresh.
- Why:
  Large-library users need the Gallery to become usable quickly and stay stable during background metadata work. Automatic scan refresh should show the first usable page, not spend extra time fetching additional pages the user did not ask for. This keeps the UI responsive without removing normal infinite scroll.
- Do not regress:
  Do not call `_onGalleryScroll()` immediately after a scan-driven `loadImages()` refresh. Do not remove user-scroll pagination. Do not refresh the Gallery while the user is on another view; mark it for refresh and wait until the user returns.
- Allowed evolution:
  A future implementation can prefetch more pages after the app is idle, but it must be cancellable/coalesced and must not make scan progress or Gallery loading feel stuck.
- Evidence:
  `frontend/js/app.js`; `tests/e2e/specs/scan-gallery-refresh.spec.ts`; failing CI run on 2026-04-30 showed 3 image fetches where the contract allows at most 2.
- Validation:
  `node --check frontend/js/app.js`; `npm --prefix tests/e2e test -- scan-gallery-refresh.spec.ts` (`2 passed`); `python3 scripts/run_ci.py` (`PASSED: compiled lock freshness`, `dependency security audit`, `frontend js syntax`, `backend full suite`, `playwright e2e`; Playwright `115 passed`, `3 skipped`).

### ADR-AI-20260430-80: Desktop modal controls must keep common actions visible and advanced-only settings out of the default path

- Status: accepted
- Area: desktop UI hardening / modal layout / Censor controls / export UX
- Context:
  A Chinese desktop visual audit before real-user testing found several user-facing regressions outside the Gallery-only path: the Censor right sidebar save/queue card could stick over filter sliders, the top brand could wrap on a 1366px desktop width, checkbox marks could collapse because inline checkbox boxes ignored fixed sizing, ordinary modal action bars could cover content on short desktop windows, and the same-name `.txt` export modal forced LoRA-only advanced fields into the beginner path.
- Decision:
  Common actions must remain visible without covering form content or controls. Censor's save/queue card is static rather than sticky, because it shares a narrow sidebar with sliders. The top brand and short checkbox controls are protected from accidental wrapping/collapse on normal desktop widths. Ordinary modal action bars do not use the global sticky behavior on short desktop windows; only explicitly sticky modal surfaces should keep sticky action rows. The same-name `.txt` export modal keeps the normal output choice and export action visible first, while `Prefix / Class Token` and `Tag Blacklist` live under `Advanced options` because they are LoRA caption tuning fields, not required beginner settings.
- Why:
  This project is a desktop local tool, so 1366x768 desktop layout is a real release target. Users should not need to understand every advanced SD training option just to export captions, and controls must not hide each other during normal use. Short labels belong in controls; longer explanations belong in helper text, preview text, or collapsible advanced sections.
- Do not regress:
  Do not make Censor sidebar bottom actions sticky again unless the sidebar layout is redesigned so it cannot cover sliders. Do not put LoRA-only advanced caption options back into the default same-name export path. Do not apply broad sticky modal action behavior to every modal on short desktop windows. Do not allow short Chinese labels such as `高级选项` or the brand text to wrap into orphan characters on normal desktop widths.
- Evidence:
  `frontend/css/ui-refresh.css`, `frontend/index.html`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `tests/e2e/specs/smoke.spec.ts`, `artifacts/ui-audit-zh/zh-ui-audit.json`, `artifacts/ui-audit-target/scan-modal-fixed.png`, `artifacts/ui-audit-target/censor-fixed.png`, `artifacts/ui-audit-target/batch-inspect-1s.png`.
- Related decisions:
  ADR-AI-20260430-78 remains the export wording/format contract. This ADR narrows the desktop layout/default-path behavior around that export flow.
- Validation:
  `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `git diff --check`; `npm --prefix tests/e2e test -- smoke.spec.ts -g "batch sidecar export|export modal"` (`3 passed`); `python3 scripts/lazy_release_qa.py --skip-package --image-count 120 --frontend` (`PASS`).

### ADR-AI-20260430-81: Missing-file reconnect completion reports the selected search scope, not the whole library backlog

- Status: accepted
- Area: missing-file UX / path repair result semantics / large-library clarity
- Context:
  Full Playwright CI found a real user-facing failure in the `Find moved images` flow: reconnecting one moved file through the UI succeeded, but the final progress payload still reported hundreds of unrelated `still_missing` rows from the dirty library. That made the background search look stuck or unsuccessful even though the file in the user-selected folder had been found.
- Decision:
  Missing-file reconnect now keeps two counts separate. `library_missing_total` is the whole library backlog for context, while `missing_total` and `still_missing` describe only missing records that were actually relevant to files seen in the selected search folder. Matches, ambiguous candidates, and already-indexed conflicts all remove those scoped records from `still_missing`, because they were found and need either automatic reconnect, user review, or duplicate cleanup. If the selected folder contains no files that match any missing library row, the frontend shows a warning that this folder did not match the missing records and suggests choosing a wider folder or reconnecting the drive.
- Why:
  Users choose a concrete folder because they are asking “are my moved images here?” The completion message must answer that question, not punish them with unrelated missing records from old tests, removed drives, or other folders. Keeping the whole-library backlog as a separate field preserves diagnostics without making the normal completion result feel false.
- Do not regress:
  Do not compute `still_missing` from every missing row in the database for a folder-scoped reconnect run. Do not show a success-looking “0 found” message when the app checked a folder but found no matching missing records. Do not automatically reconnect ambiguous rows or merge duplicate found paths just to reduce the count.
- Evidence:
  `backend/services/image_service.py`, `backend/tests/test_reconnect_missing_files.py`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `tests/e2e/specs/reconnect-missing.spec.ts`.
- Related decisions:
  Extends ADR-AI-20260430-77. Debt-14 remains open for the larger ambiguous/conflict review UI.
- Validation:
  `python3 -m py_compile backend/services/image_service.py`; `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_reconnect_missing_files.py` (`6 passed`); `npm --prefix tests/e2e test -- reconnect-missing.spec.ts` (`1 passed`).

### ADR-AI-20260502-82: Model setup is asynchronous and stores prepared assets under package data

- Status: accepted
- Area: model manager / first-run downloads / local runtime state / setup UX
- Context:
  Model Manager preparation could freeze the whole frontend because `/api/models/prepare` ran blocking model downloads inside an async FastAPI handler. Kaloscope also downloaded assets into legacy `models/artist/` while health checks looked under `data/models/artist/`, so the UI stayed `Missing` after a successful download. The visible `Downloaded` status was also confusing because it meant files existed but runtime dependencies were still missing.
- Decision:
  `/api/models/prepare` now starts preparation in a background executor and returns immediately with `downloading`; `/api/models/download-progress` is the progress/result source. Artist/Kaloscope setup uses direct HTTP downloads and writes runtime/checkpoint/mapping assets under `get_artist_model_dir()` (`data/models/artist` by default), matching `model_health.py`. Legacy `models/artist` files may be copied only as a local compatibility source, not as the canonical prepared location. The model manager presents only `Ready` or `Missing`; if files exist but runtime dependencies are missing, the card remains `Missing` and the message names the dependency/runtime problem.
- Why:
  A local app must keep the UI responsive while multi-GB model files download. Health checks and setup must share the same canonical data directory, or users cannot trust a completed download. `Downloaded` is an implementation detail, not a user-ready state; users need to know whether the feature works now or what concrete setup is still missing.
- Do not regress:
  Do not call blocking model downloads directly from async request handlers. Do not make `models/artist` the canonical prepared asset location. Do not reintroduce a user-visible `Downloaded` badge/status for model cards. Do not make Kaloscope setup depend on `huggingface_hub` or ModelScope SDK when direct HTTP URLs are sufficient.
- Evidence:
  `backend/routers/models.py`, `backend/services/model_service.py`, `backend/artist_identifier.py`, `backend/model_health.py`, `frontend/js/app.js`, `tests/e2e/playwright.config.ts`, `tests/e2e/specs/model-manager.spec.ts`.
- Validation:
  `python3 -m py_compile backend/model_health.py backend/services/model_service.py backend/artist_identifier.py backend/routers/models.py`; `npm --prefix tests/e2e run test -- specs/model-manager.spec.ts` (`4 passed`); `PYTHONPATH=backend python3 -m pytest -s backend/tests/test_model_service.py -q` (`9 passed`).

### ADR-AI-20260502-83: Portable launcher repairs PyTorch CUDA separately from base requirements

- Status: accepted
- Area: Windows portable packaging / GPU runtime selection / SAM3 readiness
- Context:
  The Windows portable launcher already repaired ONNX Runtime after hardware detection, but `backend/requirements.txt` installed the normal PyPI `torch==2.11.0` package. On Windows that resolved to `torch 2.11.0+cpu`, so SAM3 could have a 3.4 GB checkpoint under `data/models/sam3` while still staying `Missing` because the app's embedded Python had no CUDA Torch and no SAM3 runtime packages. The Model Manager also previously made this worse by reporting the checkpoint as already present without explaining the runtime gap.
- Decision:
  Keep the base requirements portable and cross-machine, then repair machine-specific GPU runtimes in launcher scripts. `repair_onnxruntime.py` remains responsible for ONNX Runtime vendor selection. `repair_torch_runtime.py` now reuses Windows GPU detection, keeps AMD/Intel on standard CPU Torch, and switches NVIDIA systems to a compatible PyTorch CUDA wheel index based on detected driver CUDA capability. It also installs SAM3 runtime packages (`sam3`, `einops`, `hydra-core`, `omegaconf`, `pycocotools`) for NVIDIA systems unless explicitly skipped. Model readiness must continue to distinguish file presence from runtime usability.
- Why:
  CUDA Torch wheels are NVIDIA-specific, large, and served from PyTorch CUDA indexes rather than normal PyPI defaults. Putting a CUDA wheel directly into `requirements.txt` would either break or overburden non-NVIDIA users. The launcher has the hardware context, so it is the correct place to choose CUDA Torch versus standard Torch, just as it already chooses ONNX Runtime GPU versus DirectML.
- Do not regress:
  Do not assume `torch==...` from normal PyPI means CUDA is available on Windows. Do not mark SAM3 `Ready` unless the checkpoint exists, required Python packages import, and `torch.cuda.is_available()` is true. Do not install CUDA Torch for AMD/Intel machines unless the app adds a real supported non-NVIDIA Torch backend. Do not use ModelScope SDK as the required SAM3 checkpoint download path when direct HTTP preparation exists.
- Escape hatches:
  Set `SD_IMAGE_SORTER_SKIP_TORCH_REPAIR=1` to skip Torch repair, `SD_IMAGE_SORTER_SKIP_SAM3_RUNTIME_REPAIR=1` to skip SAM3 package repair, or `SD_IMAGE_SORTER_TORCH_CUDA_INDEX_URL` to force a custom PyTorch wheel index.
- Evidence:
  `backend/repair_torch_runtime.py`, `backend/repair_onnxruntime.py`, `run-portable.bat`, `scripts/build_release_packages.py`, `backend/model_health.py`, `backend/services/model_service.py`, `frontend/js/app.js`, `backend/tests/test_repair_torch_runtime.py`, `backend/tests/test_model_service.py`.
- Validation:
  `TMPDIR=$PWD/.tmp/pytest-tmp PYTHONPATH=backend python3 -m pytest -s backend/tests/test_repair_torch_runtime.py backend/tests/test_model_service.py backend/tests/test_model_health.py backend/tests/test_release_build.py -q` (`41 passed`); `python3 -m py_compile backend/repair_torch_runtime.py backend/repair_onnxruntime.py backend/model_health.py backend/services/model_service.py backend/routers/models.py scripts/build_release_packages.py`; `node --check frontend/js/app.js`; `node --check frontend/js/lang/en.js`; `node --check frontend/js/lang/zh-CN.js`.

### ADR-AI-20260502-84: First launch prepares feature runtime packages before the app opens

- Status: accepted
- Area: first-run setup / customer onboarding / SAM3 readiness / launcher contracts
- Context:
  Treating SAM3 runtime packages as a repair only after a user clicks `Prepare / Download` creates a bad first-run product experience: a customer can install the checkpoint, still see `Missing`, and be told to restart or manually install packages. That is unacceptable for a trial user because the first launch may be the only chance to show the feature working. The previous runtime repair also did not cover `run.bat`, and the portable launcher consistency check did not verify SAM3's transitive runtime imports such as `decord` and `iopath`.
- Decision:
  SAM3 Python runtime packages are now part of the launcher-installed runtime requirements: `sam3`, `einops`, `hydra-core`, `omegaconf`, `pycocotools`, plus locked transitive runtime packages such as `decord` and `iopath`. Windows launchers run PyTorch/SAM3 runtime repair before the backend process starts, so NVIDIA users get CUDA Torch selected before the app opens instead of after a model card click. `run.bat`, `run.sh`, `run-portable.bat`, and generated portable launchers check the full AI runtime import set and reinstall dependencies when it is incomplete. Model health probes Torch through a subprocess when Torch is not already loaded, so status checks do not pollute the long-lived backend process with a CPU Torch import before repair.
- Why:
  Runtime packages are product prerequisites, not optional user chores. Large model checkpoints can remain explicit user downloads, but once a checkpoint is present the feature should be usable immediately on supported hardware. Installing or swapping Torch after the backend has already imported Torch is not reliable, so hardware-specific runtime selection must happen before backend startup.
- Do not regress:
  Do not move SAM3 runtime package installation back to the model-card click path as the normal customer flow. Do not require customers to manually run `pip install` for built-in features. Do not run Torch CUDA repair after the backend starts as the primary path. Do not let `/api/models/status` import Torch into the backend process unless Torch is already loaded by a real feature.
- Allowed evolution:
  Future launchers may add a clearer setup progress UI or cached/offline wheel bundle, but the invariant remains: supported runtime packages are prepared before the browser opens, while multi-GB model checkpoints stay explicit downloads.
- Evidence:
  `backend/requirements.in`, `backend/requirements.txt`, `backend/model_health.py`, `backend/repair_torch_runtime.py`, `run.bat`, `run.sh`, `run-portable.bat`, `scripts/build_release_packages.py`, `backend/services/model_service.py`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `tests/e2e/playwright.config.ts`, `backend/tests/test_release_build.py`, `backend/tests/test_model_health.py`, `backend/tests/test_model_service.py`.
- Validation:
  `bash -n run.sh`; `python3 -m py_compile backend/model_health.py backend/services/model_service.py backend/repair_torch_runtime.py scripts/build_release_packages.py`; `node --check frontend/js/app.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`; `TMPDIR=$PWD/.tmp/pytest-tmp PYTHONPATH=backend python3 -m pytest -s -q backend/tests/test_model_service.py backend/tests/test_model_health.py backend/tests/test_repair_torch_runtime.py backend/tests/test_release_build.py` (`43 passed`); `npm --prefix tests/e2e run test -- specs/model-manager.spec.ts` (`4 passed`).

### ADR-AI-20260502-85: Launchers prepare build tools before runtime requirements

- Status: accepted
- Area: first-run dependency install / Windows portable packaging / embedded Python
- Context:
  A real Windows hand test of `sd-image-sorter-v3.1.0-windows-portable.zip` failed on first launch while installing `antlr4-python3-runtime==4.9.3`, a source-only dependency pulled by Hydra/OmegaConf. Python's embeddable Windows distribution uses a `python3XX._pth` isolated path model, and pip's PEP 517 build-isolation subprocess could not import `setuptools.build_meta` even after reporting build dependency installation complete.
- Decision:
  All launchers bootstrap `setuptools` and `wheel` before installing the runtime lock. The generated Windows portable launcher additionally installs `backend/requirements.txt` with `--no-build-isolation` because embedded Python build isolation is the failing path. The launchers still use `backend/launcher_pip.py` so marker noise stays filtered and users see normal pip progress.
- Why:
  The portable package must work on a clean Windows machine without asking users to install Visual Studio, system Python, or manual build tools. Disabling build isolation for this embedded interpreter is safer than relying on pip's temporary build environments, because the isolated `._pth` behavior can hide build backend packages from source-build subprocesses.
- Do not regress:
  Do not remove the `setuptools wheel` bootstrap from `run.bat`, `run.sh`, or generated `run-portable.bat`. Do not remove `--no-build-isolation` from the generated `run-portable.bat` unless the runtime lock is proven wheel-only on Windows or the project replaces embeddable Python with a distribution that supports pip build isolation reliably. Do not change launchers back to direct `python -m pip install -r backend\requirements.txt`.
- Evidence:
  Windows first-launch failure log from `C:\temp\SDIS 手测\sd-image-sorter-v3.1.0-windows-portable`, `run.bat`, `run.sh`, `scripts/build_release_packages.py`, `backend/tests/test_release_build.py`.
- Validation:
  `python3 -m py_compile scripts/build_release_packages.py backend/launcher_pip.py`; `cd backend && env TMPDIR="$(dirname "$PWD")/.tmp/pytest-tmp" PYTHONPATH=. python3 -m pytest -q -s tests/test_release_build.py` (`21 passed`); rebuilt `sd-image-sorter-v3.1.0-pipfix1-windows-portable.zip` and verified `run-portable.bat` contains the build-tool bootstrap plus `--no-build-isolation`, zip integrity passes, and required portable files are present. `run.bat` and `run.sh` were also hardened to install build tools before runtime requirements while keeping normal pip build isolation enabled for standard venv installs.


### ADR-AI-20260502-86: macOS packages skip SAM3 runtime dependencies

- Status: accepted
- Area: macOS packaging / SAM3 readiness / Python 3.12 dependency support
- Context:
  The Linux/Mac release package originally installed the same full AI runtime dependency lock everywhere. A PyPI wheel audit found `decord==0.6.0`, pulled by `sam3==0.1.3`, has no Python 3.12 macOS wheel and no sdist, so a clean macOS setup would fail before the app starts. This is not just an optional feature failure: one unavailable SAM3 dependency blocks the whole application install.
- Decision:
  The runtime lock marks `sam3`, `decord`, `iopath`, `einops`, `hydra-core`, `omegaconf`, and `pycocotools` as non-macOS dependencies. `run.sh` skips SAM3 runtime imports in its dependency completeness check on Darwin, and model health reports SAM3 as disabled on macOS instead of telling users to repair missing packages.
- Why:
  SAM3 is treated by this app as a CUDA-only feature. Forcing its runtime packages onto macOS gives users a broken first-run experience for a feature that is not ready there anyway. The Mac package should start cleanly and clearly say SAM3 is unavailable, not fail while installing unrelated runtime dependencies.
- Do not regress:
  Do not make `sam3` or `decord` unconditional in `backend/requirements.txt` until the project has verified Python 3.12 macOS wheels or a different supported SAM3 runtime path. Do not put SAM3 imports back into the Darwin branch of `run.sh` startup checks. Do not show macOS users a generic "missing packages" SAM3 message when the intended state is unsupported.
- Evidence:
  PyPI metadata for `sam3==0.1.3` requires `decord>=0.6.0,<0.7.0`; PyPI files for `decord==0.6.0` include Windows/Linux wheels and old macOS cp36-cp38 wheels only, not Python 3.12 macOS wheels. Code evidence: `backend/requirements.in`, `backend/requirements.txt`, `run.sh`, `backend/model_health.py`, `backend/tests/test_release_build.py`, `backend/tests/test_model_health.py`.
- Validation:
  `bash -n run.sh`; `python3 -m py_compile backend/model_health.py scripts/build_release_packages.py backend/launcher_pip.py`; `cd backend && env TMPDIR="$(dirname "$PWD")/.tmp/pytest-tmp" PYTHONPATH=. python3 -m pytest -q -s tests/test_release_build.py tests/test_model_health.py`; rebuilt release packages and verified the Linux/Mac package's `run.sh` has the Darwin SAM3 skip while Windows portable still has the SAM3 bootstrap.


### ADR-AI-20260502-87: Release target is Windows and Linux only

- Status: accepted
- Area: release platform support / packaging / first-run reliability
- Context:
  During first-run package testing, Windows embedded Python exposed a build-isolation failure, and macOS exposed a separate unsupported SAM3 dependency chain (`decord==0.6.0` has no Python 3.12 macOS wheel). The release goal is now to make Windows and Linux reliably start first instead of spending release time on macOS-specific dependency exceptions.
- Decision:
  This release line supports Windows and Linux only. The generated full tar package is named `sd-image-sorter-vX.X.X-linux.tar.gz`; `run.sh` exits early on Darwin with a clear unsupported-platform message. Linux first-run installs CPU PyTorch from the PyTorch CPU wheel index before installing a filtered runtime requirements file that omits direct CUDA/NVIDIA/Triton package pins. Windows portable keeps the embedded-Python `setuptools/wheel` bootstrap plus `--no-build-isolation` path.
- Why:
  A release package that starts reliably on the supported platforms is better than a cross-platform label that fails during dependency installation. Linux CPU-first is the safest baseline: the app can start, WD14/CLIP/censor workflows can run on CPU, and CUDA/SAM3 can remain explicit follow-up work instead of blocking first launch.
- Do not regress:
  Do not publish a `linux-mac` full package name or advertise macOS support until a clean macOS first-run is tested. Do not make Linux first-run install direct `nvidia-*`, `cuda-*`, `triton`, `torch`, or `torchvision` pins from the shared lock before the CPU Torch baseline is installed. Do not remove the Windows portable build-tool bootstrap.
- Evidence:
  `run.sh`, `run.bat`, `scripts/build_release_packages.py`, `backend/app_info.py`, `backend/services/update_service.py`, `backend/tests/test_release_build.py`, `backend/tests/test_update_service.py`, `docs/RELEASE_PACKS.md`, `README.md`.

### ADR-AI-20260503-88: PyTorch CUDA repair verifies reinstalled wheels in a fresh interpreter

- Status: accepted
- Area: Windows first launch / portable runtime repair / network and disk usage truthfulness
- Context:
  A first-start Windows portable log from `C:\temp\SDIS 手测\sd-image-sorter-v3.1.0-windows-portable\log.txt` showed the app ultimately started, but the launcher downloaded ONNX CUDA runtime wheels plus multiple PyTorch CUDA wheels (`cu128`, `cu126`, then fallbacks) before warning that CUDA Torch repair failed. The root issue was not that every wheel was needed: `repair_torch_runtime.py` imported CPU `torch` while probing the existing state, then used pip to replace it with a CUDA wheel in the same Python process. Re-probing through the already-imported module kept reporting `torch.version.cuda` as empty, so the repair loop treated a successful install as failure and downloaded more wheels.
- Decision:
  After pip replaces Torch, CUDA repair must verify the installed wheel from a fresh Python interpreter via `_torch_probe_subprocess()`. The same fresh probe result is used to report final repair state when a CUDA Torch reinstall occurred. README first-start documentation must distinguish required feature runtime downloads from caches and optional model downloads, and must tell users that repeated PyTorch CUDA wheel downloads in one launch indicate an old package or repair failure rather than normal behavior.
- Why:
  Python cannot reliably unload and reload binary Torch modules in the same process after pip replaces them on disk. A subprocess probe is cheaper than another multi-GB wheel download and avoids lying to users with a failed repair warning after a valid install. Disk and traffic warnings also need to be explicit because the app intentionally ships a full local AI workflow rather than deleting features to look small.
- Do not regress:
  Do not verify a post-pip Torch replacement by calling the current process's already-imported `torch` module. Do not add broad CUDA Torch pins to base requirements just to avoid launcher logic; non-NVIDIA machines must not pay that cost. Do not present `data/pip-cache` as required app data: it is safe-to-clean package-download cache, separate from models, database, favorites, and settings.
- Evidence:
  `backend/repair_torch_runtime.py`, `backend/tests/test_repair_torch_runtime.py`, `README.md`, `CHANGELOG.md`, the provided first-start `log.txt` showing repeated CUDA wheel downloads and final app startup.
- Validation:
  `cd backend && TMPDIR=/tmp PYTHONPATH=. python3 -m pytest -q tests/test_repair_torch_runtime.py` (`9 passed`).


### ADR-AI-20260504-89: SAM3 text segmentation must gate on presence_logits, not just per-query score

- Status: accepted
- Area: backend AI inference / privacy-censor accuracy
- Context:
  After switching the SAM3 backend from the unmaintained `sam3==0.1.3` PyPI package to HuggingFace `transformers.Sam3Model`, real anime/SD image testing exposed a serious failure mode the old `argmax(scores)` selection silently inherited. SAM3 emits a per-text-query `presence_logits` "is this concept here at all" signal alongside `pred_masks` and `pred_logits`. Empirically, on the user's `C:\temp\SDIS 手测\OutPut Keep` images, real detections produced `sigmoid(presence_logits)` in `[0.52, 0.74]` while absent prompts ("exposed female genitalia" on a clothed image, "exposed male genitalia" on a female image, etc.) clustered in `[0.001, 0.030]`. The old code ignored `presence_logits`, picked the highest-scoring mask among 200 queries regardless, and consistently returned a whole-body silhouette covering 50–85 % of the canvas — which the censor pipeline then rasterised as a giant box over the entire person. Users described this as "why is it a box and worse than YOLO/NudeNet".
- Decision:
  `backend/sam3_refiner.py::_run_segmentation` gates text prompts on `sigmoid(presence_logits) >= presence_threshold` (default 0.5) BEFORE running `post_process_instance_segmentation`. `detect_privacy_regions(conf_threshold=...)` reuses its existing `conf_threshold` argument as the presence-probability threshold (parallel semantics to NudeNet's score threshold: higher = stricter, lower = more recall). A belt-and-suspenders area cap (`_DEFAULT_MAX_AREA_RATIO = 0.30`) rejects any mask covering more than 30 % of the image even if presence somehow squeaks past. A small score floor (`_DEFAULT_SCORE_FLOOR = 0.05`) drops total noise. The previous `area / image_area < 0.001` floor inside `detect_privacy_regions` is replaced with an absolute `area < 64 px` floor so legitimate small detections (nipples, anus on high-resolution canvases) survive. Box-only prompts (`refine_box`) skip the presence gate because `presence_logits` is text-conditioned.
- Why:
  Score alone does not separate present-vs-absent on this model: real detections range `[0.034, 0.553]` for top-1 score while false positives reach `0.044`, leaving no clean threshold. `presence_logits` cleanly separates the two clusters. The whole-body collapse pattern is a known SAM3 behaviour for absent prompts — the model still has to assign 200 query slots, and on absent text they default to a generic high-coverage query (consistently query #144 in the diagnostic dump). Without this gate the "Pro" SAM3 censor mode actively makes images worse than NudeNet by drawing huge boxes over clothed people. Lowering `score_threshold` to 0.05 instead of 0.3 also matters: real anime detections (`top1 = 0.13–0.34`) were being filtered out, which the user perceived as "most things it cannot detect ever with low confidence threshold".
- Do not regress:
  Do not delete the `presence_threshold` check from `_run_segmentation`. Do not raise `score_threshold` back to 0.3 to "be stricter" — strictness now lives in `presence_threshold`. Do not put the `area / image_area < 0.001` floor back in `detect_privacy_regions`; nipple-size masks on 1024×1536 canvases legitimately measure ~110 px and were being silently dropped. Do not reintroduce a fallback that picks `argmax(scores)` when presence is below threshold — that is the failure mode that produced the giant-box bug.
- Allowed evolution:
  Replace the constant `_DEFAULT_PRESENCE_THRESHOLD = 0.5` with a per-class threshold table if the model's presence calibration differs by anatomy class. Add a small NMS / cross-prompt dedup if SAM3 returns near-identical bboxes for different prompts (observed in the diagnostic dump on one image where breast and buttocks prompts produced overlapping masks). Replace the area cap with a learned size prior. None of those should weaken the absent-prompt false-positive guarantee.
- Evidence:
  Diagnostic run (`C:\temp\SDIS 手测\sam3_diag\diag.py`) against 5 images at `C:\temp\SDIS 手测\OutPut Keep\` and post-fix verification run (`C:\temp\SDIS 手测\sam3_diag\verify.py`). Pre-fix masks at `C:\temp\SDIS 手测\sam3_debug\` showed whole-body silhouettes for absent prompts; post-fix masks at `C:\temp\SDIS 手测\sam3_verify\` show only localised anatomy-shaped masks (max 5.84 % of canvas). Empirical thresholds documented inline in `backend/sam3_refiner.py` near `_DEFAULT_PRESENCE_THRESHOLD`.
- Last verified:
  2026-05-04 against the 5-image real-data sample and full backend pytest suite.
- Related files:
  `backend/sam3_refiner.py`
  `backend/tests/test_sam3_refiner.py`
  `backend/services/censor_service.py`
- Supersedes:
  None
- Validation:
  `cd backend && python -m pytest tests/test_sam3_refiner.py` (12 passed) and full suite `cd backend && python -m pytest tests/` (892 passed, 5 skipped).


### ADR-AI-20260508-89: Custom tagger remains WD14-compatible ONNX only

- Status: superseded by ADR-AI-20260508-93
- Area: tagger runtime / custom model UX / model-specific preprocessing
- Context:
  A user tried to run a locally supplied tagger through `Custom Local Model` and hit ONNX Runtime shape errors: the graph expected NCHW `[batch, 3, 448, 448]`, while the custom path prepared NHWC `[batch, 448, 448, 3]`. The current shipped model catalog also includes Camie, PixAI, and ToriiGate, which are not interchangeable generic WD14 paths: Camie needs JSON metadata and ImageNet/NCHW preprocessing, PixAI needs NCHW `[-1, 1]` preprocessing plus rating fallback, and ToriiGate uses a dedicated VLM backend rather than ONNX Runtime.
- Decision:
  `Custom Local Model` remains a generic WD14-compatible ONNX + CSV entry point, not an offline override for every built-in tagger. The ONNX runtime now infers NHWC vs NCHW from the model input shape so compatible custom ONNX exports with `[batch, 3, H, W]` run correctly. UI/README wording now tells users to choose Camie, PixAI, and ToriiGate from the model list instead of feeding their files through the custom path.
- Why:
  Making custom silently mimic every built-in model would require schema detection for metadata files, preprocessing, output categories, rating semantics, and non-ONNX backends. That would hide risky incompatibility behind a friendly label. Inferring tensor layout fixes the concrete compatible-ONNX bug without pretending custom is a universal model adapter.
- Do not regress:
  Do not route ToriiGate through `WD14Tagger` or accept non-ONNX custom tagger files. Do not allow custom JSON metadata until the request schema and validation explicitly model it. Keep built-in model-specific preprocessing in the named catalog entries unless a real custom metadata schema is added.
- Evidence:
  `backend/tagger.py`, `backend/services/tagging_service.py`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `README.md`, user screenshot showing ONNX Runtime `Got invalid dimensions ... Got: 448 Expected: 3`.
- Validation:
  Added `backend/tests/test_tagger.py::test_custom_onnx_infers_nchw_input_layout` to cover the NCHW custom ONNX case.

### ADR-AI-20260508-90: Tagger thresholds must run only on normalized confidence probabilities

- Status: accepted
- Area: backend AI tagging accuracy / model-specific score semantics
- Context:
  The supported tagger catalog now includes classic WD14 ONNX models, Camie v2, PixAI v0.9, and ToriiGate. They do not all expose scores with the same semantics: WD/PixAI outputs are consumed as probabilities, Camie ONNX emits logits that need sigmoid normalization, and ToriiGate is a VLM caption-to-tags backend rather than a probability-thresholded classifier. Treating logits as confidence values can create impossible `confidence > 1` tags and makes weak/noisy model output look like strong matches, which users correctly perceive as random tagging.
- Decision:
  `WD14Tagger` normalizes model scores before thresholding. Built-in Camie declares `output_activation = sigmoid`; WD/PixAI/custom-compatible ONNX remain probability/identity outputs. Any NaN/Inf or out-of-range probability score is ignored before threshold checks, so invalid logits cannot pass general/character thresholds or win rating selection. PixAI rating fallback derives rating only from already-thresholded returned tags. ToriiGate remains documented as threshold-not-applicable because its confidence values mean "generated tag accepted by parser", not classifier probability.
- Why:
  Threshold sliders are a user promise: lower than threshold means not returned. That promise is only meaningful when scores are bounded confidence probabilities. Model-specific activation belongs in the built-in model config; custom remains WD14-compatible probability ONNX rather than a universal logits adapter. Failing closed on invalid custom scores is safer than emitting random-looking high-confidence tags.
- Do not regress:
  Do not remove Camie's sigmoid activation. Do not treat raw scores outside `[0, 1]` as valid confidence values. Do not make PixAI fallback inspect unreturned low-confidence explicit tags. Do not present ToriiGate's `confidence=1.0` as threshold probability; it is VLM parser output.
- Evidence:
  `backend/config.py`, `backend/tagger.py`, `backend/tests/test_tagger.py`, `backend/tests/test_toriigate_tagger.py`, `README.md`, and current HuggingFace examples for Camie/PixAI model usage.
- Validation:
  `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_tagger.py tests/test_toriigate_tagger.py tests/test_tagging_service.py` (`41 passed`).

### ADR-AI-20260508-91: Launchers may auto-shift only reserved default localhost ports

- Status: accepted
- Area: Windows startup / launcher UX / localhost port semantics
- Context:
  A Windows portable launch on real hardware failed after readiness checks with `WinError 10013` while binding `127.0.0.1:8487`. This failure happens before the browser can use the app and is commonly caused by Windows excluded/reserved TCP port ranges from Hyper-V, WSL, VPN, or security software. It is different from normal `address already in use`: another running SD Image Sorter instance on `8487` should not cause a second backend to silently start against the same package-local database on another port.
- Decision:
  Launchers run `backend/launcher_port.py` before opening the browser. When no explicit `SD_IMAGE_SORTER_PORT` is set and the default port fails with access denied / refused-by-OS semantics, the launcher searches upward from `8488` and exports the selected port before starting `main.py --port ...`. Browser URL and backend bind port must always come from the same selected value. If the user explicitly set `SD_IMAGE_SORTER_PORT`, the launcher fails loudly instead of changing it. If the port is simply already in use, the launcher also fails loudly and tells the user to use the existing tab or close the process.
- Why:
  Beginners should not have to diagnose Windows reserved port ranges to open a local-only app. Auto-shifting the default keeps first launch friendly. Preserving explicit overrides and refusing to auto-shift normal port-in-use cases protects user intent and avoids accidental double-running against one local database.
- Do not regress:
  Do not open the browser before port selection. Do not let `run-portable.bat`, `run.bat`, `run.sh`, or the release package template hardcode a URL that can differ from the backend bind port. Do not silently change an explicit `SD_IMAGE_SORTER_PORT`. Do not treat normal address-in-use as safe auto-fallback.
- Evidence:
  User-provided portable log from 2026-05-08 showing successful readiness checks followed by `ERROR: [Errno 13] ... ('127.0.0.1', 8487): [winerror 10013]` and shutdown. Current files: `backend/launcher_port.py`, `run-portable.bat`, `run.bat`, `run.sh`, `scripts/build_release_packages.py`.
- Validation:
  `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_launcher_port.py tests/test_update_cli.py tests/test_release_build.py tests/test_update_service.py tests/test_update_worker.py` (`79 passed`).

### ADR-AI-20260508-92: Rescue batch files are external safety nets, not alternate launchers

- Status: accepted
- Area: Windows portable UX / update reachability / support tooling
- Context:
  v3.1.1 exposed a bad support trap: the only normal update path lived inside the web UI, but a startup bind failure can prevent users from ever reaching the web UI. Adding external `.bat` files is necessary, but making `fix.bat` an alternate startup path would train normal users to launch through a repair script and would hide the real startup contract.
- Decision:
  `run.bat` and generated `run-portable.bat` own normal startup self-healing, including reserved-default-port detection, selected port propagation to `main.py --port ...`, and opening the browser at the actual selected URL. `update.bat` is an external rescue updater that uses the same package-local data/config/update/cache paths as the launcher, checks the release channel, downloads a verified update archive, writes a pending manifest with `current_pid=0`, applies it via `update_worker.apply_update`, and relaunches through the normal launcher when possible. `update_worker` treats `current_pid <= 0` as an external/no-running-app manifest and applies immediately instead of waiting for a process. `fix.bat` is for rare diagnostics/repair only: it reports version/path, probes the configured/default port with `launcher_port.py --diagnose`, shows Windows excluded TCP ranges when available, runs ONNX Runtime and PyTorch/SAM3 repair scripts, and prints startup readiness. It must not run `main.py` or become the normal way to choose a fallback port.
- Why:
  Normal users should double-click the normal launcher and get into the app. If a Windows reserved port blocks default `8487`, the launcher should silently pick a safe bindable localhost port and open the matching URL. `fix.bat` should stay a support tool users almost never need. `update.bat` provides the missing escape hatch when a blocker prevents access to the in-app updater.
- Do not regress:
  Do not move reserved-port auto-fallback into `fix.bat`. Do not let `fix.bat` start the server, open the browser, or call `main.py`. Do not remove `update.bat` from release-managed files. Do not let `update.bat` use different data/config/update/model directories from `run.bat` / `run-portable.bat`; otherwise updates and diagnostics will operate on the wrong package state. Do not make `update_worker` wait on `current_pid=0`; that value is the external updater's explicit no-app-process sentinel.
- Evidence:
  User correction on 2026-05-08: `fix.bat` should not be “启动进不去时自动换安全端口再启动”; that behavior belongs inside `run`, with the browser auto-opened to the changed URL. Current files: `run.bat`, `scripts/build_release_packages.py`, `backend/launcher_port.py`, `backend/update_cli.py`, `backend/update_worker.py`, `fix.bat`, `update.bat`, `backend/tests/test_release_build.py`, `backend/tests/test_update_cli.py`, `backend/tests/test_update_worker.py`.
- Validation:
  `cd backend && PYTHONPATH=. python3 -m py_compile launcher_port.py update_cli.py services/update_service.py main.py && PYTHONPATH=. python3 -m pytest -q -s tests/test_launcher_port.py tests/test_update_cli.py tests/test_release_build.py tests/test_update_service.py tests/test_update_worker.py` (`79 passed`). Follow-up: `TMPDIR=/mnt/l/Antigravitiy code/sd-image-sorter/.tmp/pytest-tmp PYTHONPATH=. python3 -m pytest -s tests/test_update_worker.py::test_apply_update_with_external_manifest_does_not_wait_for_pid_zero tests/test_update_cli.py -q` (`5 passed`).

### ADR-AI-20260508-93: Custom ONNX tagger is profile-aware for WD14, Camie, and PixAI

- Status: accepted
- Area: tagger runtime / custom model UX / model-specific preprocessing / threshold semantics
- Supersedes:
  ADR-AI-20260508-89. The old “Custom remains WD14-compatible ONNX only” decision was too restrictive and conflicted with the product goal of keeping local workflows inside the app when the backend already has the required model-specific runtime profiles.
- Context:
  A user correctly challenged the earlier boundary that told users to avoid `Custom Local Model` for Camie and PixAI. Camie and PixAI are ONNX taggers; their local files are not unsafe by nature. The unsafe part was treating them as generic WD14 CSV/probability/NHWC models. Current code already has explicit Camie and PixAI configs for metadata format, preprocessing, output activation, thresholds, and rating fallback.
- Decision:
  `Custom Local Model` is a profile-aware ONNX entry point. The UI exposes `Custom Model Type` with WD14-compatible, Camie, and PixAI options. For local custom paths, the backend maps that selected profile to the real built-in config before loading the ONNX file. WD14-compatible uses `selected_tags.csv`; PixAI uses `selected_tags.csv` plus PixAI preprocessing and rating fallback; Camie uses metadata JSON plus NCHW/ImageNet preprocessing and sigmoid score normalization. ToriiGate remains excluded from Custom ONNX because it is not an ONNX tagger and runs through the dedicated VLM/PyTorch backend.
- Why:
  Banning Camie/PixAI from Custom was a lazy stopgap. The correct invariant is not “only WD14”; it is “only run a local model when the app knows its schema.” A profile selector makes the schema explicit, keeps validation strict, and avoids random tags by applying the right metadata parser, normalization, threshold defaults, and rating behavior. The Custom path still starts with conservative runtime chunks because a user-supplied ONNX file can differ from the exact built-in export even when it uses the same profile.
- Do not regress:
  Do not collapse Custom back to “WD14 only.” Do not let Camie run with CSV metadata or identity logits. Do not let PixAI run with JSON metadata or without PixAI preprocessing/rating fallback. Do not route ToriiGate through `WD14Tagger` or accept it as a Custom ONNX profile. Do not send frontend auto-selected batch size as a user override; only user-edited advanced batch size should override the backend's conservative Custom default.
- Evidence:
  Current files: `backend/services/tagging_service.py`, `backend/tagger.py`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `README.md`. Regression coverage: `backend/tests/test_tagging_service.py`, `backend/tests/test_tagger.py`, `backend/tests/test_routers/test_tags.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_tagging_service.py::test_runtime_plan_maps_legacy_custom_model_paths_to_wd14_profile tests/test_tagging_service.py::test_runtime_plan_uses_custom_camie_profile_for_local_onnx tests/test_tagging_service.py::test_runtime_plan_uses_custom_pixai_profile_for_local_onnx tests/test_tagging_service.py::test_custom_camie_profile_rejects_csv_metadata_path tests/test_tagging_service.py::test_custom_pixai_profile_rejects_json_metadata_path tests/test_tagging_service.py::test_custom_toriigate_profile_is_rejected_because_it_is_not_onnx tests/test_tagger.py::test_custom_profile_aliases_resolve_to_real_model_profiles tests/test_tagger.py::test_custom_camie_profile_autodetects_metadata_json_next_to_model` (`8 passed`).


### ADR-AI-20260508-94: Launcher URL host and custom tagger metadata must match the selected runtime profile

- Status: accepted
- Area: startup launcher contract / custom tagger file contract / release safety
- Context:
  Two adjacent regressions were found during autonomous reliability review. First, the new launcher port probe honored `SD_IMAGE_SORTER_HOST`, but `run.bat`, `run.sh`, and the portable launcher template still built browser URLs as `http://localhost:<port>`. If a user intentionally binds another loopback address such as `127.0.0.2` or `::1`, the backend can bind one host while the launcher opens a different host. Second, profile-aware Custom ONNX tagging correctly exposed Camie/PixAI profiles, but `WD14Tagger._get_model_paths()` could still auto-fallback Camie to `selected_tags.csv` when `tags_path` was omitted. That lets a Camie runtime reach the JSON metadata parser with a WD14 CSV file, producing a runtime crash instead of a clear validation error.
- Decision:
  `backend/launcher_port.py` now exports `SD_IMAGE_SORTER_URL_HOST` alongside the selected port. Launchers and generated portable launchers build `APP_URL` from that exported URL host plus the selected port, not from a hardcoded `localhost`. `WD14Tagger._get_model_paths()` is profile-aware for custom local paths: Camie only auto-discovers JSON metadata candidates, WD14/PixAI only auto-discover CSV candidates, and explicitly supplied `tags_path` must use the selected profile's allowed extension.
- Why:
  Port and bind-host are one startup contract; matching only the port is not enough. Custom tagger profiles are one model-schema contract; selecting Camie but silently accepting WD14 CSV breaks the same invariant the profile selector was added to protect. Both fixes fail closed with actionable errors instead of opening the wrong URL or crashing deep in inference setup.
- Do not regress:
  Do not reintroduce hardcoded `http://localhost:<port>` into launchers once a bind host override exists. Do not remove `SD_IMAGE_SORTER_URL_HOST` from launcher output without replacing it with an equivalent host-aware URL mechanism. Do not allow Camie custom paths to fall back to `selected_tags.csv`, and do not allow WD14/PixAI custom paths to fall back to Camie JSON unless their profile metadata rules are explicitly changed and tested.
- Evidence:
  Current files: `backend/launcher_port.py`, `run.bat`, `run.sh`, `scripts/build_release_packages.py`, `backend/tagger.py`, `backend/tests/test_launcher_port.py`, `backend/tests/test_release_build.py`, `backend/tests/test_tagger.py`.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests/test_launcher_port.py backend/tests/test_release_build.py -q` (`35 passed`). `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests/test_tagger.py backend/tests/test_tagging_service.py backend/tests/test_routers/test_tags.py -q` (`86 passed`). `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests --ignore=backend/tests/test_sam3_refiner.py -q` (`902 passed, 1 skipped`). Full `backend/tests` collection without ignore is still blocked in this environment by missing `torch` for `backend/tests/test_sam3_refiner.py`.

### ADR-AI-20260508-95: Custom ONNX metadata is optional but user files are never repair-deleted

- Status: accepted
- Area: custom tagger UX / local file safety / API compatibility
- Context:
  A follow-up code audit found two contract mismatches in the profile-aware Custom ONNX path. The backend could already auto-detect profile-specific tag metadata beside a custom model, but the frontend still blocked start unless `tags_path` was filled in. Separately, `WD14Tagger._create_session()` reused the built-in corrupted-download repair path for every ONNX load, so an invalid user-supplied `model_path` that raised `INVALID_PROTOBUF` could be deleted before failing to re-download a built-in model. A third compatibility edge existed when old clients sent `model_path` with a concrete WD model name instead of `custom` / `wd14`.
- Decision:
  Custom ONNX `tags_path` is optional in the frontend and API contract when the matching metadata file is next to the ONNX model. `WD14Tagger` only auto-discovers metadata that matches the selected profile: WD14/PixAI discover CSV only; Camie discovers JSON only. Explicit `model_path` and explicit `tags_path` are hard contracts: if supplied, they must exist and must not silently fall back to built-in downloads or neighbor-file auto-discovery; `tags_path` without `model_path` is rejected because built-in taggers ignore that field. Explicit `tags_path` must use the selected profile's allowed extension. Corrupted-model auto-delete/re-download now applies only to app-managed built-in downloads, never to an explicit local `model_path`. Legacy WD built-in names paired with `model_path` are normalized to the WD14-compatible custom profile.
- Why:
  The useful UX invariant is “select the right schema, then the app can help find the matching metadata,” not “force every user to paste two paths.” The safety invariant is stronger: local model paths are user-owned files, not cache entries. A repair path that is correct for app-managed HuggingFace downloads is destructive when pointed at a user's custom export.
- Do not regress:
  Do not make `tags_path` mandatory again unless backend auto-discovery is removed at the same time. Do not accept `tags_path` without `model_path`, because that field has no built-in-model effect. Do not let custom Camie consume CSV or custom WD14/PixAI consume JSON. Do not delete, overwrite, or re-download user-supplied `model_path` files during model-load repair. Do not reject legacy custom requests that pass a WD built-in `model_name` with `model_path`; normalize them to WD14-compatible behavior.
- Evidence:
  Current files: `backend/tagger.py`, `backend/services/tagging_service.py`, `frontend/js/app.js`, `frontend/index.html`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `README.md`, `docs/API.md`, `docs/AI_PRINCIPLES.md`. Regression coverage: `backend/tests/test_tagger.py`, `backend/tests/test_tagging_service.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_tagger.py::test_custom_profile_aliases_resolve_to_real_model_profiles tests/test_tagger.py::test_custom_wd14_profile_does_not_follow_mutable_default_model tests/test_tagger.py::test_custom_model_load_error_does_not_delete_user_supplied_file tests/test_tagger.py::test_custom_camie_profile_autodetects_metadata_json_next_to_model tests/test_tagger.py::test_custom_wd14_profile_does_not_autodetect_camie_json tests/test_tagger.py::test_custom_camie_profile_does_not_autodetect_selected_tags_csv tests/test_tagger.py::test_custom_pixai_profile_rejects_direct_json_metadata tests/test_tagger.py::test_custom_camie_profile_does_not_fallback_to_wd14_csv tests/test_tagging_service.py::test_runtime_plan_maps_legacy_custom_model_paths_to_wd14_profile tests/test_tagging_service.py::test_custom_model_path_with_legacy_wd_model_name_stays_wd14_compatible tests/test_tagging_service.py::test_custom_wd14_runtime_plan_ignores_mutable_default_model tests/test_tagging_service.py::test_runtime_plan_uses_custom_camie_profile_for_local_onnx tests/test_tagging_service.py::test_runtime_plan_uses_custom_pixai_profile_for_local_onnx tests/test_frontend_contract.py::test_custom_tagger_profile_ui_and_payload_contract` (`14 passed`).

### ADR-AI-20260508-96: Playwright tagging E2E uses a test-only tagger seam, not live WD14 downloads

- Status: accepted
- Area: release CI / E2E determinism / local AI runtime boundaries
- Context:
  Release CI hung in the real UI scan-then-tag flow after Model Manager tests reset the isolated Playwright model directory. The tag flow then tried to prepare/load the real WD14 ONNX model inside `.tmp/e2e-data-<port>/models`, leaving `/api/tag/progress` in long-running `running` state while polling. That tests network/model availability and Windows file-lock timing more than it tests the product flow.
- Decision:
  Playwright webServer sets `SD_IMAGE_SORTER_E2E_FAKE_TAGGER=1`. When that flag is present, non-ToriiGate tagging workers use a tiny deterministic in-process tagger that still goes through the real scan endpoint, tag start endpoint, progress queue, image readability checks, content fingerprinting, `database.add_tags_batch`, and image detail reads. Production launchers and normal runtime never set this flag. ToriiGate remains excluded from this seam because its behavior is a separate VLM/PyTorch backend.
- Why:
  E2E should prove the app workflow and persistence contract, not depend on downloading/loading 500MB+ HuggingFace assets during every release gate. Real model correctness remains covered by backend tagger/runtime tests and manual/model-manager flows. Keeping the seam behind an explicit `SD_IMAGE_SORTER_E2E_FAKE_TAGGER` environment variable makes the boundary obvious and prevents production drift.
- Do not regress:
  Do not make Playwright release CI depend on live WD14/PixAI/Camie downloads for scan/tag persistence coverage. Do not enable the fake tagger outside test-controlled environments. If the seam is removed, replace it with an equally deterministic local fixture model and keep `/api/tag/progress` from polling indefinitely on missing/slow model assets.
- Evidence:
  Current files: `backend/services/tagging_service.py`, `tests/e2e/playwright.config.ts`, `backend/tests/test_tagging_service.py`, `tests/e2e/specs/manual-regression.spec.ts`.
- Validation:
  `cd backend && TMPDIR=/mnt/l/Antigravitiy code/sd-image-sorter/.tmp/pytest-tmp PYTHONPATH=. python3 -m pytest -q -s tests/test_tagging_service.py::test_e2e_fake_tagger_completes_without_downloading_real_model tests/test_update_worker.py::test_apply_update_with_external_manifest_does_not_wait_for_pid_zero tests/test_update_cli.py` (`6 passed`).

### ADR-AI-20260508-97: Playwright artist E2E uses a test-only identifier seam, and single-image artist inference must not block FastAPI

- Status: accepted
- Area: release CI / E2E determinism / local AI runtime boundaries / API responsiveness
- Context:
  Release CI exposed a shared failure chain after Model Manager tests reset the isolated Playwright model directory and then artist identification tried to load/run the real experimental Kaloscope runtime during a single `/api/artists/identify` probe. The endpoint was declared `async` but called synchronous model preparation and inference inline. While Kaloscope was loading or stuck in a bad fixture/runtime state, unrelated page navigations and model-manager requests timed out behind the same event loop.
- Decision:
  `/api/artists/identify` dispatches `ArtistService.identify_image(...)` through `run_in_threadpool`, matching the existing censor/similarity pattern for heavy local AI work. Playwright webServer also sets `SD_IMAGE_SORTER_E2E_FAKE_ARTIST=1`; when that flag is present, the artist router reports a ready fixture runtime from diagnostics and injects a tiny deterministic identifier that still goes through the real image lookup, path resolution, fingerprinting, derived-state write, API response, and UI polling contracts. Production launchers and normal runtime never set this flag.
- Why:
  A local desktop app must stay responsive while optional AI models prepare. E2E should prove the UI/API/database workflow, not depend on loading the experimental Kaloscope runtime in every release gate. Real model readiness remains covered by Model Manager status/prepare flows and backend model-health/runtime tests.
- Do not regress:
  Do not run artist model loading or inference inline on the FastAPI event loop. Do not make Playwright release CI depend on live Kaloscope inference for artist UI/persistence coverage. Do not enable `SD_IMAGE_SORTER_E2E_FAKE_ARTIST` outside explicit test-controlled environments. If the seam is removed, replace it with an equally deterministic local fixture model and keep unrelated page/API requests responsive during artist model preparation.
- Evidence:
  Current files: `backend/routers/artists.py`, `backend/services/artist_service.py`, `tests/e2e/playwright.config.ts`, `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`, `tests/e2e/specs/manual-regression.spec.ts`.
- Validation:
  `cd backend && TMPDIR=/mnt/l/Antigravitiy code/sd-image-sorter/.tmp/pytest-tmp PYTHONPATH=. python3 -m pytest -q -s tests/test_routers/test_prompts_censor_similarity_artists.py::TestArtistsRouterValidation::test_identify_route_dispatches_model_work_to_threadpool tests/test_routers/test_prompts_censor_similarity_artists.py::TestArtistsRouterValidation::test_e2e_fake_artist_identifier_writes_prediction_without_real_runtime` (`2 passed`).

### ADR-AI-20260509-98: LoRA caption export is one training-caption line, not a vague sidecar dump

- Status: accepted
- Area: export semantics / LoRA training workflow / bilingual UX copy
- Context:
  The user challenged whether `LoRA caption` actually meant Prompt + Tags or something else. Investigation found the UI was vague and the backend did not fully match the labels: `prompt` and `tags` sidecar modes could be polluted by the advanced Prefix / Class Token field, and `caption_merged` could write multiline files when stored AI captions or prompts contained newlines. Sidecar filenames also trusted stored `filename` values more than necessary.
- Decision:
  `LoRA caption file` means one same-name `.txt` per image for training, written as one caption line: optional Class Token / Prefix + AI caption + Prompt + Tags. `Caption + Tags` is also a training-caption mode and may use the Prefix, but it intentionally excludes the original Prompt. Exact export modes (`Prompt text`, `Tags`, `Negative prompt`, `Prompt + Negative`, `A1111 / Forge block`, and `JSON`) ignore Prefix/Class Token and preserve their named data shape. Batch sidecar filenames are sanitized before writing inside the selected output folder.
- Why:
  Export labels are user promises. If a user selects `Prompt text`, prepending a LoRA class token is wrong. If a user selects `Tags`, injecting arbitrary Prefix text is wrong. If a LoRA trainer expects one caption file per image, silently writing multiple lines from metadata newlines is wrong. The UI should explain the formula directly instead of making beginners infer what `LoRA caption` means.
- Do not regress:
  Do not let Prefix/Class Token affect exact Prompt/Tags/Negative/A1111/JSON exports. Do not let `caption_merged` or `caption_tags` output raw multiline caption parts. Do not rename the helper text back to vague `LoRA captions` without showing the formula. Do not build sidecar output paths from unsanitized stored filenames.
- Supersedes:
  Clarifies ADR-AI-20260429-51, ADR-AI-20260430-74, and ADR-AI-20260430-78 for Prefix/Class Token scope and the concrete `LoRA caption` formula.
- Evidence:
  User question on 2026-05-09; current files: `backend/services/tag_export_service.py`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `docs/API.md`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `backend/tests/test_routers/test_tags.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `node --check frontend/js/app.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest -q backend/tests/test_routers/test_tags.py::TestExportTagsBatch backend/tests/test_routers/test_images.py::TestExportSelectionData backend/tests/test_frontend_contract.py` (`34 passed`); `npm --prefix tests/e2e test -- smoke.spec.ts -g "export modal|batch sidecar export"` (`3 passed`).

### ADR-AI-20260509-99: Facet search must query the full indexed library before display limiting

- Status: accepted
- Area: gallery filter UX / tag prompt LoRA checkpoint facet search / power-user scale
- Context:
  The user reported that searching `blue` did not show an existing tag such as `nagisa_(blue_archive)`. The root cause was not matching syntax; the UI searched only a pre-limited client-side facet list (`/api/tags` default 500, library defaults around 1000, prompt/LoRA library defaults around 500/1000). Any lower-frequency tag, prompt token, LoRA, or checkpoint outside that slice was invisible even when it matched the query.
- Decision:
  Facet search for tags, prompt tokens, LoRAs, and checkpoints must search the full indexed database first, then apply any optional display limit to the matched result set. `/api/tags/library`, `/api/prompts/library`, and `/api/loras/library` accept optional `q` and optional `limit`; omitting `limit` returns all matching facet rows. `/api/analytics` accepts optional `facet`, `q`, and `limit` for checkpoint/LoRA/tag facet search. Frontend autocomplete uses small display limits only for the suggestion popup, but the query is sent to backend full-library search. Library modal search and filter modal checkpoint/LoRA search also route through backend facet queries instead of filtering a pre-limited local array.
- Why:
  A search box that cannot find existing values is worse than no search box. Display limits are valid UI rendering controls; they are not valid search scope controls. This follows the repo principle that performance work must not quietly weaken total library size support or shrink the product into a toy.
- Do not regress:
  Do not reintroduce default facet endpoints that return only the top N values and then rely on frontend `.includes()` as the only search path. Do not make autocomplete fetch `/api/tags` or a cached pre-limited prompt/LoRA library and search locally. Do not cap backend `q` search before matching; apply `limit` only after filtering and relevance ordering. Keep small suggestion counts as a display choice only.
- Evidence:
  Current files: `backend/database.py`, `backend/services/tagging_service.py`, `backend/routers/tags.py`, `backend/services/sorting_service.py`, `backend/routers/sorting.py`, `frontend/js/app.js`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `backend/tests/test_routers/test_tags.py`, `backend/tests/test_routers/test_sorting.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  `python3 -m compileall -q backend/database.py backend/services/tagging_service.py backend/routers/tags.py backend/services/sorting_service.py backend/routers/sorting.py`; `node --check frontend/js/app.js`; `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_routers/test_tags.py::TestGetTags tests/test_routers/test_tags.py::TestTagsLibrary tests/test_routers/test_tags.py::TestPromptsLibrary tests/test_routers/test_tags.py::TestLorasLibrary tests/test_routers/test_sorting.py::TestAnalytics tests/test_frontend_contract.py` (`42 passed`).

### ADR-AI-20260509-100: Manual Sort large filter scopes use JSON body, not query-string payloads

- Status: accepted
- Area: manual sort API / large-library filters / power-user scale
- Context:
  The hard-limit audit found another arbitrary limit class after the facet-search fix: Manual Sort still started sessions through `/api/sort/start` query parameters, with long filter scopes encoded as comma-separated URL strings. Large tag, checkpoint, LoRA, or prompt selections could hit query-size or `max_length=1000` validation before the backend ever queried the indexed library.
- Decision:
  Preferred Manual Sort clients send a JSON request body to `POST /api/sort/start`, carrying filter arrays and the folder map directly. The legacy query-string API remains supported for compatibility. The backend service accepts both array values and legacy comma-separated strings, and folder parsing accepts either a JSON object body or the old JSON-encoded query value.
- Why:
  Query strings are a navigation/paging convenience, not a safe transport for large structured filter scopes. Manual Sort is a power-user workflow that may intentionally combine many tags, LoRAs, checkpoints, prompt terms, and destination folders; silently constraining that through URL length turns the product into a toy for large libraries.
- Do not regress:
  Do not move Manual Sort frontend startup back to `this.post(`/api/sort/start?${params}`)` or re-add arbitrary 1000-character query limits for tag/checkpoint/LoRA/prompt scopes. Keep the legacy query path working, but treat JSON body as the primary API contract for rich filter scopes.
- Evidence:
  Current files: `backend/routers/sorting.py`, `backend/services/sorting_service.py`, `frontend/js/app.js`, `backend/tests/test_routers/test_sorting.py`, `backend/tests/test_frontend_contract.py`, `docs/API.md`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `CHANGELOG.md`.
- Validation:
  `python3 -m compileall -q backend/routers/sorting.py backend/services/sorting_service.py`; `node --check frontend/js/app.js`; `cd backend && PYTHONPATH=. python3 -m pytest -q -s tests/test_routers/test_sorting.py::TestSortSession::test_start_sort_session_accepts_json_body_for_large_filter_payloads tests/test_routers/test_sorting.py::TestSortSession::test_start_sort_session tests/test_routers/test_sorting.py::TestSortSession::test_start_sort_session_forwards_search_query tests/test_routers/test_sorting.py::TestSortSession::test_start_sort_session_rejects_invalid_folders_payload tests/test_frontend_contract.py::test_manual_sort_start_uses_json_body_not_query_string_filters` (`5 passed`).

### ADR-AI-20260509-101: Default startup must stay lightweight and metadata persistence must stay compact

- Status: accepted
- Area: startup dependencies / storage footprint / model preparation UX / SQLite migration
- Context:
  A user reported a real storage failure: after scanning images before dumping them, `data/images.db` could become larger than the source images. Code review confirmed the cause: scan persisted raw image metadata chunks (`prompt`, `workflow`, NovelAI `Comment`, A1111 `parameters`, EXIF/XMP payloads) into `images.metadata_json` even though those chunks can be re-read from the source image. The same review found first-run launchers were still installing or repairing large AI stacks by default, which costs storage and internet bandwidth before the user chooses those features.
- Decision:
  Persist only compact metadata summaries in the DB: `_compact` plus `_parsed`. Raw PNG/EXIF/XMP metadata remains in the original image file and is not duplicated into SQLite during scan/reparse. Migration 008 compacts legacy `images.metadata_json` and `collection_items.metadata_json`, and `init_db()` attempts a post-commit `VACUUM` after metadata-compaction migrations so SQLite can release old pages. That `VACUUM` is best-effort: if SQLite reports low disk space or a transient lock after the migration committed, startup must continue and warn rather than rolling back the already-successful upgrade. Launchers now install `backend/requirements-core.txt` by default; full AI dependencies require `SD_IMAGE_SORTER_INSTALL_FULL_AI=1` or explicit Feature Setup / Prepare action. Heavy optional groups are installed only when missing or too old, and the UI tells users to restart when a Prepare action installed Python packages into the live process.
- Why:
  This app is local-first and must be usable on machines with limited disk and bandwidth. Copying recoverable raw metadata into SQLite violates that constraint and provides little value. Heavy AI features still matter, but paying gigabytes on first launch for features the user may not use is bad product behavior.
- Do not regress:
  Do not persist raw `prompt`, `workflow`, `Comment`, `parameters`, EXIF, or XMP blobs into `images.metadata_json` as the normal index shape. Do not remove the post-migration VACUUM attempt unless another file-shrink mechanism replaces it, but also do not make a post-commit VACUUM failure fatal to app startup. Do not make normal launchers install Torch/FastEmbed/NudeNet/Ultralytics/SAM3/Transformers by default again without an explicit product decision. If Prepare installs Python packages while the backend is running, keep a visible restart reminder.
- Evidence:
  Current files: `backend/metadata_storage.py`, `backend/image_manager.py`, `backend/migrations/008_compact_persisted_metadata.py`, `backend/database.py`, `backend/requirements-core.in`, `backend/requirements-core.txt`, `run.bat`, `run.sh`, `scripts/build_release_packages.py`, `backend/optional_dependencies.py`, `backend/services/model_service.py`, `backend/routers/models.py`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_image_manager.py`, `backend/tests/test_migration_contract.py`, `backend/tests/test_optional_dependencies.py`, `backend/tests/test_release_build.py`.
- Validation:
  Targeted pytest covered compact metadata persistence, migration 008 compaction/isolation, best-effort VACUUM failure handling, lightweight core requirements, optional dependency restart hints, model service prepare behavior, model health, and torch runtime repair. `scripts/check_lockfiles.py`, `bash -n run.sh`, and Python compile checks passed.

### ADR-AI-20260509-102: Large scans need bounded work and quiet, actionable console logs

- Status: accepted
- Area: large-library scan stability / console diagnostics / beginner-friendly debugging
- Context:
  A user scanning roughly 80,000 metadata-bearing images reported that the terminal stopped giving useful updates, scans appeared randomly stuck, and launcher text such as `Error output above` did not identify the real failure. Corrupt/truncated image files also produced noisy parser tracebacks such as `Invalid PNG signature`, burying the useful scan state.
- Decision:
  Folder scans keep metadata extraction bounded with a small per-worker backlog and per-file timeout, continue past unreadable files, and expose skipped-file samples through scan progress. Metadata parsing uses isolated worker processes by default (`SD_IMAGE_SORTER_SCAN_METADATA_EXECUTOR=process`) so a stuck native image decoder can be terminated after timeout; `thread` remains available for compatibility/debugging. Normal console output must stay sparse: startup status, scan start configuration, low-frequency scan heartbeat lines while work is still running, scan completion/cancel/failure summaries, and one skipped-file summary when errors occurred. The scan heartbeat defaults to every 15 seconds and is adjustable with `SD_IMAGE_SORTER_SCAN_LOG_HEARTBEAT_SECONDS`; it exists specifically so a stuck scan still reports step, processed/total, metadata progress, pending backlog, error count, current item, idle time, and elapsed time without flooding the terminal. Because terminal/Codex/IDE scrollback is finite, normal startup also writes a small rotating file log to `data/logs/backend.log` by default (`SD_IMAGE_SORTER_LOG_FILE=false` disables it). `/api/scan/progress` exposes `attention_required` / `attention_message` when visible progress is stale, and the scan modal offers localized copy-diagnostics, open-log, copy-log-path, structured step/current/pending/completed details, privacy notice, and stop-import actions backed by `/api/support/diagnostics` and `/api/support/open-log`; diagnostics returns bounded redacted log text and open-log only opens the configured app log path. The background scan bar must also surface stalled-state attention before generic counting text, otherwise users who close the modal still see a frozen-looking import. Uvicorn access logs are off by default and can be re-enabled with `SD_IMAGE_SORTER_ACCESS_LOG=true`; parser tracebacks for known-bad media belong at DEBUG via `SD_IMAGE_SORTER_LOG_LEVEL=DEBUG`.
- Why:
  Large SD libraries routinely contain broken partial downloads, generated sidecars, and old experimental files. Treating every bad image as an ERROR makes the app look broken and gives beginners no useful next step. Conversely, completely silent scans destroy trust at 10k-100k scale. The right behavior is bounded background work plus short, actionable summaries.
- Do not regress:
  Do not emit per-file WARNING/ERROR tracebacks for expected unreadable image metadata. Do not turn HTTP access logs back on by default. Do not replace low-frequency heartbeat diagnostics with per-file logging. Do not remove the rotating support log, `/api/support/diagnostics`, or `/api/support/open-log` without replacing the support workflow for truncated consoles. Do not remove scan start/completion summary logs, the UI stalled-scan warning, structured diagnostics card, copy/open/copy-path actions, stop-import escape hatch, or the UI recent-errors payload. Do not allow metadata worker queues to grow unbounded while importing a large folder. Do not silently fall back to unkillable thread metadata workers as the default production path. If a scan is cancelled or aborts while metadata futures are still pending, cancel those futures and terminate isolated metadata workers instead of only calling executor shutdown. Do not claim scans can never stall: OS directory enumeration, network/cloud drives, antivirus, SQLite locks, and disk I/O can still delay progress outside the metadata worker kill boundary.
- Evidence:
  Current files: `backend/image_manager.py`, `backend/metadata_parser.py`, `backend/services/sorting_service.py`, `backend/main.py`, `backend/config.py`, `frontend/index.html`, `frontend/js/app.js`, `frontend/css/ui-refresh.css`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `run.bat`, `scripts/build_release_packages.py`, `backend/tests/test_image_manager.py`, `backend/tests/test_metadata_parser_errors.py`, `backend/tests/test_routers/test_sorting.py`, `backend/tests/test_main_logging.py`, `backend/tests/test_frontend_contract.py`.
- Validation:
  Targeted pytest covers metadata parser log quietness, scan progress summaries, scan heartbeat logging during a blocking worker, user-visible stalled-scan progress fields, support diagnostics redaction/API/open-log payloads, frontend diagnostics-card contract, config boolean parsing, uvicorn access-log defaults, rotating file-log creation, unreadable-image scan behavior, and release launcher error wording. A real process-worker timeout check terminates a deliberately stuck metadata worker and returns in under 1 second. A 20,000-file process-worker pressure check completed with `errors=0`, `metadata_processed=20000`, `indexed_rows=20000`, and bounded pending backlog. A production-default 80,000-file process-worker pressure check completed with `errors=0`, `metadata_processed=80000`, `indexed_rows=80000`, and `max_pending=15`. A 10,000-file mixed scan pressure check completed with 400 valid metadata PNGs, 9,600 corrupt PNGs, `metadata_processed=10000`, `errors=9600`, and zero WARNING/ERROR log output from bad images. Playwright verified the Chinese scan modal renders the stalled-scan card, preserves dynamic stalled messages across language refreshes, shows structured diagnostics plus stop/copy/open actions, surfaces the localized stalled warning in the background scan bar, copies diagnostics, handles open-log when no OS opener exists, displays redacted paths, and has no page console errors in the checked flow.

### ADR-AI-20260509-103: Regeneratable thumbnail cache must be capped and feature setup must show real first-run cost

- Status: accepted
- Area: storage footprint / first-run UX / optional runtime preparation
- Context:
  After compacting `images.db`, the next unbounded storage risk was `data/thumbnails`: it stores derived WebP thumbnails that can be regenerated and previously had only age-based cleanup. A 100,000-image library can still create enough thumbnails to matter on small disks, and users also need to know which features work immediately versus which ones will download model/runtime packages and may need a restart.
- Decision:
  Thumbnail cache has a configurable persistent cap, defaulting to 500 MB in `data/config/app-settings.json` and overridable by `SD_IMAGE_SORTER_THUMBNAIL_CACHE_MAX_MB`. The cache evicts oldest `.webp` files when over limit; setting the limit to `0` disables persistent thumbnail writes. Feature Setup / Disk Usage exposes the current thumbnail cache usage and limit, lets users save a new MB limit, and immediately trims derived thumbnails if needed. Feature Setup also lists first-run-ready features separately from Prepare/Download features, and ToriiGate is now represented as a prepare-able model card that installs optional runtime packages first, then downloads model files after restart if needed.
- Why:
  Thumbnail files are derived, so eviction does not destroy user data. A finite default protects users with limited storage without cutting gallery browsing. Explicit feature-cost grouping prevents the app from pretending heavyweight AI features are free on first launch and reinforces the restart requirement after runtime package installs.
- Do not regress:
  Do not remove the default thumbnail cache cap or replace it with age-only cleanup. Do not hide the cache limit behind a config-file-only setting. Do not let the cache cleanup endpoint delete originals, models, DB files, favorites, or settings. Do not make ToriiGate silently download gigabytes without a Feature Setup status/prepare path. Keep restart reminders visible when Prepare installed Python packages.
- Evidence:
  Current files: `backend/config.py`, `backend/thumbnail_cache.py`, `backend/services/disk_service.py`, `backend/routers/disk.py`, `backend/services/image_service.py`, `backend/services/model_service.py`, `frontend/js/app.js`, `frontend/css/ui-refresh.css`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `README.md`, `backend/tests/test_config_env.py`, `backend/tests/test_thumbnail_cache.py`, `backend/tests/test_disk_service.py`, `backend/tests/test_model_service.py`, `backend/tests/test_frontend_contract.py`.

### ADR-AI-20260509-104: Old heavyweight Python environments are reclaimed by scheduled launcher rebuild, not live deletion

- Status: accepted
- Area: disk footprint / launcher safety / Feature Setup recovery
- Context:
  Lightweight first launch stops new installs from pulling full AI Python stacks by default, but existing users may already have a large `backend/venv` from older full-runtime installs. Deleting a live Python environment from the running backend is unsafe because the current process, imported modules, and file handles may still depend on it. Opening Disk Usage also exposed another scale risk: recursively measuring large `backend/venv`, model, HF, Torch, pip-cache, or thumbnail trees can make the UI look stuck.
- Decision:
  Feature Setup / Disk Usage exposes the local Python runtime environment and a “rebuild lightweight runtime on next start” action. The backend only writes a marker at `data/state/rebuild-core-venv.json`; it never deletes its own active `backend/venv`. `run.bat` and `run.sh` consume that marker before Python startup, remove only `backend/venv`, clear `backend/.requirements_hash`, then recreate/install the selected dependency mode. Disk status size reporting for potentially huge cache/runtime/model directories uses bounded best-effort scans and reports `size_complete:false` / unknown size instead of blocking the UI.
- Why:
  This gives old users an in-app, understandable path to reclaim space without asking them to manually delete folders and without risking user data. Launcher-time deletion is the safe point because the backend is not running yet. Bounded size scans preserve the Disk Usage UX on large or slow disks; exact byte counts are less important than avoiding another apparent hang.
- Do not regress:
  Do not delete `backend/venv` from the running FastAPI process. Do not let the rebuild action delete `data/`, `images.db`, downloaded models, caches, favorites, settings, or updater state. Do not make Disk Usage recursively scan unbounded runtime/model/cache trees synchronously. Keep the confirmation copy explicit that heavy AI Python packages may need to be prepared again after a lightweight rebuild.
- Evidence:
  Current files: `backend/services/disk_service.py`, `backend/routers/disk.py`, `run.bat`, `run.sh`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `frontend/css/ui-refresh.css`, `docs/API.md`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `README.md`, `backend/tests/test_disk_service.py`, `backend/tests/test_release_build.py`, `backend/tests/test_frontend_contract.py`, `backend/tests/test_thumbnail_cache.py`.
- Validation:
  Targeted tests cover rebuild marker creation without deleting the active venv, bounded runtime-size scans, bounded thumbnail-cache stats, launcher marker consumption strings, Feature Setup runtime/cache contract text, and API docs endpoint drift.

### ADR-AI-20260509-105: Optional AI installs must be safe under frontend and non-launcher entry paths

- Status: accepted
- Area: optional dependencies / frontend validation / scan timeout safety
- Context:
  Full CI and new Playwright coverage showed two non-obvious risks outside the original backend-only storage checks. First, opening Feature Setup from a backend started directly with system Python could make Prepare try to install heavyweight AI packages into the system Python environment. Second, metadata extraction timeout recovery killed stuck workers but then waited during executor shutdown, which can reintroduce the scan hang the timeout was meant to avoid.
- Decision:
  Optional dependency installation now refuses to pip-install packages unless the app is running inside a virtual environment, unless `SD_IMAGE_SORTER_ALLOW_SYSTEM_PIP_INSTALL=1` is explicitly set. The models router converts that refusal into structured Prepare progress with manual recovery steps, and Playwright verifies the user sees an actionable guide instead of a silent backend traceback. Metadata timeout recovery shuts down killed metadata executors with `wait=False` and `cancel_futures=True`, so a stuck parser cannot block scan completion after timeout handling.
- Why:
  Lightweight startup is not enough if a user or test starts `backend/main.py` directly and then clicks Prepare. The app must fail safely rather than polluting system Python or downloading huge packages into an unmanaged environment. Likewise, large or malformed image sets need timeout behavior that actually releases the scan flow.
- Do not regress:
  Do not allow optional AI Prepare to install heavyweight packages into unmanaged system Python by default. Do not accidentally block the app-owned generated portable Python, because Windows portable users must still be able to Prepare features. Do not hide this failure as a generic toast; keep actionable manual steps visible. Do not wait for already-terminated metadata workers during timeout cleanup. Keep frontend E2E coverage for Disk Usage, runtime rebuild, cache-limit tradeoff text, and the system-Python install guard.
- Evidence:
  Current files: `backend/optional_dependencies.py`, `backend/routers/models.py`, `backend/image_manager.py`, `tests/e2e/specs/model-manager.spec.ts`, `tests/e2e/playwright.config.ts`, `backend/tests/test_optional_dependencies.py`, `backend/tests/test_routers/test_prompts_censor_similarity_artists.py`, `backend/tests/test_image_manager.py`, `docs/API.md`.
- Validation:
  Targeted frontend tests cover incomplete large disk scans, saving thumbnail cache limits, scheduled runtime rebuild confirmation, and system-Python Prepare guidance. Full CI passed with backend full suite and Playwright E2E.
### ADR-AI-20260509-106: Runtime rebuild and cache cleanup must target only app-owned paths

- Status: accepted
- Area: disk cleanup safety / portable runtime / launcher rebuild semantics
- Context:
  Follow-up team review of the lightweight update found two real P1 risks. Disk cleanup used `PIP_CACHE_DIR` directly and explicitly allowed `pip_cache` outside `data/`, so a user or shell that pointed `PIP_CACHE_DIR` at a global/home directory could lose unrelated files by clicking cleanup. The Windows portable launcher also did not consume the lightweight runtime-rebuild marker, and optional install safety initially treated generated embedded Python like unmanaged system Python.
- Decision:
  Disk cleanup now ignores external `PIP_CACHE_DIR` and only exposes/cleans the app-owned `data/pip-cache`. Runtime rebuild is described as rebuilding the app-owned Python runtime, not only `backend/venv`: source/Linux launchers still delete only `backend/venv`, while generated `run-portable.bat` consumes the same marker by clearing only embedded Python's pip-installed `Lib/site-packages` and `Scripts` directories, leaving the embedded Python base files and all `data/` content intact. Optional Prepare installs are allowed in app-owned portable Python but still refused for unmanaged system Python.
- Why:
  Lightweight storage fixes are not safe if a cleanup button can delete outside the app, or if the main Windows portable distribution cannot actually shed old heavy packages. Users must be able to recover disk without risking originals, settings, models, `images.db`, or unrelated system/user caches.
- Do not regress:
  Do not trust environment-derived cache paths for deletion unless they are proven app-owned. Do not delete the entire portable `python/` directory during a runtime rebuild; only pip-installed packages/scripts are regeneratable. Do not block generated portable Python from Feature Setup Prepare. Keep launcher failure checks so a locked package directory leaves the marker in place and tells the user to close Python windows before retrying.
- Evidence:
  Current files: `backend/services/disk_service.py`, `backend/optional_dependencies.py`, `backend/routers/models.py`, `scripts/build_release_packages.py`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `README.md`, `docs/API.md`, `backend/tests/test_disk_service.py`, `backend/tests/test_optional_dependencies.py`, `backend/tests/test_release_build.py`, `tests/e2e/specs/model-manager.spec.ts`.
- Validation:
  Targeted tests cover external `PIP_CACHE_DIR` not being cleaned, portable runtime detection/marker payload, portable Python optional Prepare allowance, generated portable marker consumption/failure strings, Linux full-AI hash guard strings, and SAM3 lazy Prepare copy.

### ADR-AI-20260509-107: Unknown-size cache cleanup needs explicit user confirmation

- Status: accepted
- Area: Feature Setup Disk Usage / accessibility / data-safety UX
- Context:
  The lightweight storage review intentionally made directory size scans bounded so old full-AI runtimes and huge cache trees cannot freeze Feature Setup. That creates a second UX risk: a cache entry can be shown as `size_complete:false` / “large or not fully scanned”. Even though the backend deletion path is whitelisted and app-owned, cleaning an unknown-size directory is more surprising than cleaning a known 1 MB cache. The same review found the structured manual setup guide was actionable but did not fully trap keyboard focus.
- Decision:
  Disk Usage now asks for a second confirmation before cleaning any selected safe-to-clean cache whose size was not fully scanned. The confirmation repeats that only selected app-owned caches are emptied and that `images.db`, settings, models, and original images are not deleted. The manual setup guide now traps Tab / Shift+Tab inside the dialog and restores focus to the launching control when closed.
- Why:
  Bounded scans are correct for performance, but incomplete information should slow down destructive-looking actions. The cleanup endpoint remains safe, yet the product should make the “unknown size” condition visible at the moment of action. Keyboard users also need the same safe recovery path as mouse users when manual setup instructions appear.
- Do not regress:
  Do not auto-clean `size_complete:false` cache rows without an additional confirmation. Do not weaken the backend whitelist just because the frontend asks for confirmation. Keep the confirmation copy explicit about app-owned caches and preserved `images.db`, settings, models, and originals. Keep setup-guide focus trapped while open.
- Evidence:
  Current files: `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `tests/e2e/specs/model-manager.spec.ts`, `.plans/sd-image-sorter-release/findings.md`, `.plans/sd-image-sorter-release/progress.md`, `CHANGELOG.md`.
- Validation:
  `node --check frontend/js/app.js`; `TMPDIR=/tmp TEMP=/tmp TMP=/tmp python3 -m pytest backend/tests/test_frontend_contract.py backend/tests/test_disk_service.py backend/tests/test_optional_dependencies.py -q` (`37 passed`); `npm --prefix tests/e2e test -- --grep "disk cleanup asks|prepare system-python"` (`2 passed`).

### ADR-AI-20260509-108: Compact metadata and cache cleanup contracts must be enforced at every write/report boundary

- Status: accepted
- Area: SQLite metadata footprint / copy workflows / disk cleanup safety / optional dependency version contracts
- Context:
  A follow-up audit found that the compact metadata decision was implemented in scan/reparse and migrations, but not at every DB write boundary. Copying an image could inherit an old raw `metadata_json` blob from a legacy row and write it back into `images.db`; direct DB upserts and collection/favorites snapshots had the same caller-dependent risk. The same audit found a UI/backend contract drift in Disk Usage: environment-overridden cache directories (`SD_IMAGE_SORTER_TMP_DIR`, `SD_IMAGE_SORTER_THUMBNAIL_DIR`, `SD_IMAGE_SORTER_CACHE_DIR`) could point outside `data/`, appear in the safe-to-clean list, then be rejected by the cleanup endpoint. Directory-size reporting also followed symlinks, so a symlink inside an app cache could make external bytes look reclaimable even though cleanup only unlinks the symlink. ToriiGate also imports `Qwen3_5ForConditionalGeneration`, so the optional/full dependency floor must match the real import rather than a stale broad Transformers lower bound.
- Decision:
  Persisted metadata compaction is now enforced at the database write boundary for image upserts, metadata updates, copied-image records, and collection snapshots. The canonical DB shape remains `_compact` plus optional `_parsed`; unreadable legacy JSON falls back to an empty compact payload rather than preserving raw blobs during new writes. Migration 009 exists because already-upgraded users with `schema_version=8` will never re-run migration 008, so the raw-only EXIF/XMP rows missed by the first v8 pass need a forward migration. Disk Usage safe cleanup now reports only app-owned `data/tmp`, `data/thumbnails`, `data/cache`, and `data/pip-cache`; external cache/temp/thumbnail environment variables are not one-click cleanup targets, symlinked cache roots are refused, and size scans skip symlinks so external targets are not counted as app-reclaimable bytes. ToriiGate and the full-AI requirements input both require `transformers>=5.6.0` to match the actual Qwen 3.5 class import.
- Why:
  Storage and cleanup promises are product contracts, not implementation hints. If one write path or one environment-variable branch bypasses the contract, old users can still end up with a huge `images.db` or see a scary cleanup UI that appears able to delete outside app-owned data. Optional dependency checks must reflect real imports, otherwise Prepare can say a runtime is ready and then fail on the next import.
- Do not regress:
  Do not rely on scan/reparse callers alone to compact `metadata_json`; DB-owned image and collection write helpers must keep the invariant. Do not add new metadata persistence paths that store raw PNG text chunks, EXIF, XMP, or workflow JSON without routing through the compact helper. Do not mark externally redirected temp/cache/thumbnail directories as safe-to-clean. Do not clean symlinked safe-cache roots, and do not count symlink targets as cache bytes the app can safely reclaim. Do not lower ToriiGate/SAM3 Transformers floors below the version that provides the imported classes, and keep `requirements.in` aligned with `optional_dependencies.py` so future lock refreshes cannot drift backward.
- Evidence:
  Current files: `backend/database.py`, `backend/image_manager.py`, `backend/metadata_storage.py`, `backend/migrations/008_compact_persisted_metadata.py`, `backend/migrations/009_compact_raw_only_metadata.py`, `backend/services/disk_service.py`, `backend/optional_dependencies.py`, `backend/requirements.in`, `frontend/js/app.js`, `frontend/css/ui-refresh.css`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_database.py`, `backend/tests/test_image_manager.py`, `backend/tests/test_migration_contract.py`, `backend/tests/test_disk_service.py`, `backend/tests/test_optional_dependencies.py`, `docs/API.md`, `CHANGELOG.md`.
- Validation:
  Targeted checks should cover DB write-boundary compaction, copied-image compaction, migration 009 for already-v8 databases, app-owned cache reporting, symlink size accounting, and ToriiGate dependency floor alignment.

### ADR-AI-20260511-109: Prompt Lab fixed affixes are part of generated prompt semantics

- Status: accepted
- Area: Prompt Lab UX / prompt generation semantics / preset behavior
- Context:
  Users need Prompt Lab generation to support fixed tags before and after the generated result, for common SD patterns such as always starting with quality tags and ending with resolution/detail tags. The risky version of this feature is two hidden text inputs that only append strings after generation, because users would not know when the fields apply, duplicates would leak into copied prompts, and saved presets would not restore the actual generation setup.
- Decision:
  Prompt Lab Random output now treats fixed beginning and fixed ending tags as part of the generated prompt output contract. Generate/Randomize merges `prependTags`, the generated core prompt, and `appendTags` in that order, removes duplicates using trimmed case-insensitive tag keys with spaces and underscores treated the same, and preserves the first visible spelling/order. The UI explains this as beginner-facing “fixed tags before/after Generate” with “auto dedupe” copy. Saved Prompt Lab presets include `prependTags` and `appendTags`; loading a preset restores those fields. Clearing the builder clears selected slots/output but intentionally keeps the fixed affix fields so users can generate multiple prompts with the same house style.
- Why:
  For SD workflows, these fixed tags are not decorative UI state; they are repeatable prompt-building intent. Prefix tags should win when the generated result repeats the same tag, because users put them there to control ordering. Keeping affixes across Clear supports the common “try another prompt with the same base quality/style wrapper” workflow and avoids making users retype house defaults.
- Do not regress:
  Do not reintroduce duplicate fixed/generated tags in the final Prompt Lab output. Do not save presets without the fixed beginning/end fields. Do not make Clear erase affixes unless the UI gains a separate explicit “clear fixed tags” action. Do not hide this behavior behind unexplained Prepend/Append labels; the UI must keep beginner-readable copy that says the fields are merged on Generate/Randomize and duplicates are removed.
- Evidence:
  Current files: `frontend/index.html`, `frontend/js/prompt-lab.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `frontend/css/ui-refresh.css`, `tests/e2e/specs/smoke.spec.ts`.
- Validation:
  `node --check frontend/js/prompt-lab.js frontend/js/lang/en.js frontend/js/lang/zh-CN.js`; `node ./scripts/run-playwright.mjs test specs/smoke.spec.ts -g "Prompt Lab fixed beginning/end tags"` (`1 passed`).

### ADR-AI-20260512-110: Tagger stays content-only; export owns selected-batch caption edits

- Status: accepted
- Area: Tagger semantics / Prompt Lab UX / batch export UX / optional dependency reproducibility
- Context:
  Prompt-building fixed tags and export-time training-caption edits look similar at first glance, but they serve different user jobs. If prepend/append or class-token behavior is put into the tagger, every tagged image appears to have the same added tags, blurring the line between AI-recognized image content and user-authored caption intent. Users also need to apply prefixes/blacklists only to a selected subset during export.
- Decision:
  Keep the tagger content-only. Do not add Prompt Lab prepend/append or export prefix/blacklist behavior to AI tag detection. Prompt Lab keeps fixed beginning/end tags because it is a prompt builder. Batch Export keeps Prefix / Class Token and blacklist because it is selected-batch post-processing. The export UI must explicitly say it affects only the currently selected Gallery images. Auto Censor model selection now surfaces the actual selected local YOLO file near the detector selector, so users do not have to open Advanced Model Picker just to know what will run. Optional Feature Setup dependency installs now prefer exact versions already pinned in `backend/requirements.txt` before falling back to broad specs, reducing release-time resolver drift without creating separate feature lock files yet.
- Why:
  Users should be able to trust that tagger output means “the model saw this in the image.” Prompt Lab and Export are user-authored transformation layers and should say so. Batch export is the right place for “only these selected images get this class token / blacklist” because selection scope is already explicit there. Censor model labels must reflect the actually-used file to avoid support reports like “why is Wenaka missing?” when the app already selected it. Optional Prepare installs should match the shipped release lock as much as possible so feature setup remains predictable.
- Do not regress:
  Do not add fixed prompt tags, training class tokens, or blacklist filtering to the tagger pipeline. Do not remove the selected-scope copy from Combined Export or Same-name .txt export. Do not hide the active YOLO filename only inside the advanced picker. Do not go back to broad optional package specs when a package is pinned in `backend/requirements.txt`.
- Evidence:
  Current files: `frontend/index.html`, `frontend/js/app.js`, `frontend/js/censor-edit.js`, `frontend/js/prompt-lab.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/optional_dependencies.py`, `backend/tests/test_optional_dependencies.py`, `tests/e2e/specs/smoke.spec.ts`, `CHANGELOG.md`, `release-notes.md`, `docs/RELEASE_NOTES_v3.1.5.md`.
- Validation:
  Targeted checks should cover Prompt Lab affix dedupe, export selected-scope copy, batch export selected-scope copy and payload, visible censor active YOLO filename, optional dependency lock mapping, release build launcher import probe, and full CI before publishing.

### ADR-AI-20260513-111: WD14 model reuse must keep tag thresholds request-scoped

- Status: accepted
- Area: AI tagging / concurrency / model lifecycle
- Context:
  WD14 ONNX models are expensive to load, so the runtime keeps a singleton tagger for the same model/path/backend settings. Previous threshold handling mutated `tagger.threshold` and `tagger.character_threshold` on the shared instance when callers requested different confidence thresholds. That made concurrent or near-concurrent calls race through shared mutable threshold state. Removing the mutation without another request-scoped path made reused single-image/convenience calls silently keep the first loaded thresholds.
- Decision:
  Keep model/session reuse keyed only by heavy runtime settings, but carry general and character thresholds as per-call configuration. `get_tagger()` returns a lightweight configured proxy over the shared loaded `WD14Tagger`; `tag()` / `tag_batch()` accept optional thresholds and pass them into `_process_probs()` for every inference path, including true-batch and fallback single-image loops. Background batch tagging may still force reload for worker isolation, but threshold correctness must not depend on reload.
- Why:
  Thresholds change result interpretation, not the model weights or ONNX session. Reloading a 500MB+ model just to change thresholds is wasteful, while mutating shared threshold fields is unsafe. A request-scoped proxy preserves reuse performance, prevents threshold races, and keeps old convenience semantics like `get_tagger(threshold=x).tag(path)` correct.
- Do not regress:
  Do not put `threshold` or `character_threshold` back into shared mutable singleton state as the primary request mechanism. Do not require model reloads only to change thresholds. If new tagger inference paths are added, they must pass request thresholds to `_process_probs()` or an equivalent request-scoped scorer.
- Evidence:
  Current files: `backend/tagger.py`, `backend/tests/test_tagger.py`.

### ADR-AI-20260513-112: WD14 Prepare owns Windows ONNX GPU runtime repair

- Status: accepted
- Area: Feature Setup / WD14 tagging / Windows runtime packaging
- Context:
  Windows portable lightweight startup intentionally installs `backend/requirements-core.txt`, which uses CPU `onnxruntime` on Windows and skips the heavyweight ONNX GPU repair path. That keeps first launch small, but it means a user can open WD14 tagging, see only `CPUExecutionProvider`, and fall back to CPU even on a GPU machine. Before this decision, the WD14 Model Manager Prepare action only downloaded model files with `use_gpu=False`; it did not repair `onnxruntime-gpu`, `onnxruntime-directml`, CUDA, or cuDNN packages.
- Decision:
  WD14 Prepare / Recheck now runs the Windows ONNX Runtime repair path before importing tagger code, then downloads / validates the requested WD14 model files. Windows repair installs or downgrades ONNX runtime packages to release-pinned specs (for v3.1.6, `onnxruntime-gpu==1.21.0` for NVIDIA and `onnxruntime-directml==1.21.0` for AMD/Intel) instead of letting pip pull a newer incompatible runtime. The DirectML pin lives in `repair_onnxruntime.py` because installing it from `requirements.txt` would conflict with NVIDIA users. Repair must also probe the imported `onnxruntime` module, not just distribution metadata, because a broken environment can report `onnxruntime-gpu==1.21.0` while importing a namespace package with no `get_available_providers`. If repair changes ONNX runtime packages, the Prepare result asks for an app restart before GPU tagging. The Feature Setup copy now lists WD14 under Prepare rather than claiming no extra runtime setup.
- Why:
  Lightweight startup is still correct, but GPU repair must be reachable from the user-visible WD14 preparation flow. Running repair before importing `tagger` avoids locking the old ONNX DLLs in the current process, and pinning the repair install prevents resolver drift such as `onnxruntime-gpu 1.26.0` breaking the shipped app runtime.
- Do not regress:
  Do not move WD14 GPU repair back behind `SD_IMAGE_SORTER_INSTALL_FULL_AI` only. Do not import `tagger` before repair in the WD14 Prepare path. Do not use unpinned `pip install onnxruntime-gpu` / `onnxruntime-directml` in repair code. Do not describe WD14 as fully ready after model-file download on Windows unless the GPU runtime repair path is also available or explicitly marked CPU-only.
- Evidence:
  Current files: `backend/services/model_service.py`, `backend/repair_onnxruntime.py`, `frontend/js/app.js`, `frontend/js/lang/en.js`, `frontend/js/lang/zh-CN.js`, `backend/tests/test_model_service.py`, `backend/tests/test_frontend_contract.py`, `backend/tests/test_repair_onnxruntime.py`, `CHANGELOG.md`, `docs/RELEASE_NOTES_v3.1.6.md`.
- Validation:
  `backend/venv/Scripts/python.exe -m pytest backend/tests/test_model_service.py backend/tests/test_frontend_contract.py backend/tests/test_tagger.py -q` (`71 passed`).

### ADR-AI-20260514-113: Windows ONNX repair is eager only for supported hardware

- Status: accepted
- Area: Windows portable startup / ONNX Runtime / WD14 tagging / public release safety
- Context:
  v3.1.6 release validation found a mismatch between intended and actual ONNX behavior. The generated Windows portable launcher runs `repair_onnxruntime.py --auto` after installing the lightweight core runtime. That is good for supported GPU machines because WD14 CPU fallback can be painfully slow and can push lower-RAM systems into crashes, but the repair script's CPU-only path also targeted `onnxruntime-gpu` when no GPU vendor was detected. That could make CPU-only, VM, RDP, or driver-broken machines pay for an unsupported GPU package during first start.
- Decision:
  Keep Windows portable startup ONNX repair enabled, but gate package swaps on confident hardware detection. CPU-only `onnxruntime` is upgraded to `onnxruntime-gpu==1.21.0` only for detected NVIDIA hardware, upgraded to `onnxruntime-directml==1.21.0` only for detected AMD/Intel hardware, and left as CPU runtime when no supported GPU vendor is detected. The repair result now tracks whether a pip-changing action actually occurred, so a no-op CPU keep does not falsely ask users to restart. WD14 Prepare / Recheck still runs the same repair path for users who reach setup from inside the app.
- Why:
  Public downloads must work beyond the maintainer's machine. Supported GPU users should not discover WD14 through a slow/crashy CPU path, while unsupported hardware should not download heavy or useless GPU runtimes. The launcher has the only reliable pre-import window for ONNX DLL changes, so startup repair is acceptable only when hardware detection justifies it.
- Do not regress:
  Do not install `onnxruntime-gpu` or `onnxruntime-directml` when `gpu_vendor_primary` is missing or unknown. Do not mark a no-op CPU keep as `repaired`. Do not move Windows WD14 repair behind manual environment variables only. Keep release notes explicit that NVIDIA first start can be slow because CUDA/cuDNN packages are large.
- Evidence:
  Current files: `backend/repair_onnxruntime.py`, `backend/tests/test_repair_onnxruntime.py`, `CHANGELOG.md`, `release-notes.md`, `docs/RELEASE_NOTES_v3.1.6.md`, `docs/AI_PRINCIPLES.md`.
- Validation:
  `TMPDIR=/tmp TEMP=/tmp TMP=/tmp PYTHONPATH=backend python3 -m pytest -q backend/tests/test_repair_onnxruntime.py backend/tests/test_model_service.py::test_prepare_wd14_repairs_windows_onnx_runtime backend/tests/test_model_service.py::test_prepare_wd14_warns_when_windows_onnx_repair_fails` (`11 passed`).

### ADR-AI-20260514-114: Image metadata harvesting stays broad but bounded

- Status: accepted
- Area: metadata scanning / JPEG, TIFF, GIF, sidecar compatibility / scan performance
- Context:
  v3.1.6 pre-release testing found that `.jpg` / `.jpeg` images can carry Stable Diffusion prompts and generation parameters. The scan allowlist already included JPEG files, but parser coverage was mostly incidental: ASCII EXIF `UserComment` worked, while common `UNICODE` UserComment payloads and APP1 XMP packets could be missed or decoded as garbage. The broader product goal is to recover as much SD metadata as possible without turning Gallery scan into a slow full-file forensic pass. If unchanged historical JPEG rows stayed marked `metadata_status='complete'`, ordinary rescans would also skip them forever unless users knew to force reparse.
- Decision:
  JPEG/JPG files are treated as first-class SD metadata containers. The parser decodes EXIF UserComment through a shared helper, including ASCII, UNICODE UTF-16 payloads, empty-prefix payloads, and generic UTF-8/UTF-16 text bytes; JPEG APP1 XMP packets are scanned for WebUI/Forge parameter blocks and ComfyUI prompt JSON. TIFF/TIF is now part of the image scan allowlist and can provide EXIF/ImageDescription/XMP metadata. GIF comment metadata is harvested when present. Small same-name sidecars (`image.ext.txt`, `image.txt`, `image.ext.json`, `image.json`, `image.ext.xmp`, `image.xmp`) are loaded only after embedded metadata fails, so exported/training captions can populate prompt fields without making every image pay extra IO. `PARSED_METADATA_VERSION` is bumped, and the unchanged-file scan skip still forces only `.jpg` / `.jpeg` rows parsed by older or missing parser versions to reparse on normal folder scan.
- Why:
  Users should not need to know whether their SD prompt was saved into PNG chunks, EXIF, XMP, GIF comments, TIFF fields, or a training sidecar. The performance contract matters just as much: only metadata/header structures are read, WebP XMP is read by RIFF chunk instead of whole-file loading, sidecars have a 256 KB cap, directory listing cache, and 50k-name huge-directory cache fuse, and limiting automatic version reparsing to JPEG avoids a punishing full metadata reparse of existing PNG/WebP/TIFF libraries just because metadata harvesting got broader.
- Do not regress:
  Keep `.jpg`, `.jpeg`, `.tif`, and `.tiff` in the image scan allowlist. Do not decode EXIF `UNICODE` UserComment as UTF-8. Do not require `force_reparse` for old JPEG rows after parser-version upgrades. Do not make every parser-version bump reparse all unchanged PNG/WebP/TIFF rows unless the upgrade actually affects those formats. Do not read large sidecars or whole WebP files during scan-time metadata extraction. Keep sidecar parsing fallback-only so embedded metadata remains the source of truth when present.
- Evidence:
  Current files: `backend/metadata_parser.py`, `backend/image_manager.py`, `backend/config.py`, `backend/services/image_service.py`, `backend/tests/test_metadata_parser.py`, `backend/tests/test_image_manager.py`, `CHANGELOG.md`, `release-notes.md`.
- Validation:
  `python3 -m py_compile backend/metadata_parser.py backend/image_manager.py backend/config.py backend/services/image_service.py`; `cd backend && python3 -m pytest -s tests/test_metadata_parser.py tests/test_metadata_parser_errors.py tests/test_image_manager.py tests/test_config_env.py -q` (`129 passed`).

## ADR-2026-05-14: Library Health is advisory and read-only

- Status: accepted
- Evidence tier: Tier 1 — implemented in current code and covered by router contract tests
- Context:
  A comparison with `zanllp/infinite-image-browsing` showed a useful product gap: creators need a library-level management surface before file organization, especially for metadata coverage, duplicate filenames, unreadable records, and archive/export risks. Our app already has scan, tagging, sorting, similarity, and export flows, but did not expose these risks in one beginner-friendly place.
- Decision:
  Add a `GET /api/library-health` endpoint and a `Library Health` / `库体检` UI view as a read-only audit. It aggregates existing SQLite records only. It must not move, delete, rewrite, re-import, generate thumbnails, invoke models, or silently repair files.
- Product semantics:
  - `quality_score` is a user-facing health indicator, not durable business data.
  - duplicate-filename warnings mean flat archive/export risk; they do not claim image-content duplication.
  - recommendations are next-action guidance. Users remain in control and must choose re-import, re-parse, tagging, or sorting workflows explicitly.
- Why not automatic cleanup:
  File movement, flattening, deletion, and overwrite behavior are high-risk local workflows. This repo prioritizes predictable beginner-safe UX over clever automatic archive changes.
- Related files:
  - `backend/database.py`
  - `backend/services/sorting_service.py`
  - `backend/routers/sorting.py`
  - `frontend/index.html`
  - `frontend/js/library-health.js`
  - `frontend/css/styles.css`
  - `docs/API.md`
  - `.plans/sd-image-sorter-release/docs/api-contracts.md`

## ADR-2026-05-14: Large filtered selections stay token-scoped

- Status: accepted
- Evidence tier: Tier 1 — implemented in current frontend/backend code and covered by router/resource-safety tests
- Context:
  A 200k-image filtered Gallery selection exposed several crash paths: the browser could resolve every matching ID into one giant array, destructive actions could POST the giant array back to FastAPI/Pydantic, and sidecar export could fetch all images/tags into large maps before writing files. Those behaviors made "Select all filtered results" technically truthful but unsafe at the scale this app explicitly supports.
- Decision:
  Filtered-result selection is represented by an immediate `selection_token` plus a total estimate/count whenever the user chooses all matching results. Frontend state must keep the token scope and must not materialize the full ID set just to delete, remove, or export. `excludedImageIds` is allowed only as a small explicit-exclusion list for inverted filtered selection after manual toggles. Backend delete/remove snapshots token IDs server-side in bounded chunks before mutating the database/files, and sidecar export reads image/tag data chunk-by-chunk while writing output.
- Product semantics:
  - `visible` and `loaded` selection remain frontend scopes.
  - filtered selection means backend filter scope, not the currently loaded thumbnail page.
  - token mode is stateless and immediate; it is not a durable resumable job snapshot.
  - prompt/LoRA matching keeps exact post-filter semantics even when SQL uses token indexes as a prefilter.
- Why:
  The product must remain viable for very large local SD libraries without quietly weakening selection behavior. Token scope preserves user intent while avoiding browser memory spikes, huge JSON payloads, and SQL maps for hundreds of thousands of IDs.
- Do not regress:
  Do not reintroduce `new Set(allFilteredIds)` or full-ID JSON payloads for filtered delete/remove/export. Do not use `excludedImageIds` as an inverted giant include-list. Do not make `selection-token` support `sortBy=random` unless ordering is seed-stable. Do not replace exact prompt-term semantics with substring-only token prefilters.
- Evidence:
  Current files: `frontend/js/app.js`, `frontend/js/gallery.js`, `frontend/js/stores/selection-store.js`, `backend/routers/images.py`, `backend/services/image_service.py`, `backend/services/tag_export_service.py`, `backend/services/tagging_service.py`, `backend/database.py`, `docs/API.md`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `backend/tests/test_routers/test_images.py`, `backend/tests/test_resource_safety.py`.

## ADR-2026-05-14: Large-library dashboard and scan paths are bounded by default

- Status: accepted
- Evidence tier: Tier 1 — implemented in current backend code and tests
- Context:
  For 200k-image libraries, startup dashboard stats and folder scan startup were doing work whose cost scaled with the whole library before the user saw useful progress. `/api/stats` could return every unique tag/checkpoint/LoRA, and scan could walk the folder tree once to count totals before walking it again to import. On network drives or huge prompt/tag vocabularies this made the app feel frozen and could keep large arrays resident in browser/backend memory.
- Decision:
  `/api/stats` is a bounded dashboard summary: counts plus top-N facets only. Full tag/checkpoint/LoRA browsing belongs to searchable/paginated analytics or Library-tab endpoints. Folder scan defaults to single-pass streaming progress; exact up-front totals are not part of the default scan contract and any future precise-ETA mode must be explicit.
- Product semantics:
  - dashboard facet arrays are not exhaustive library dictionaries.
  - scan progress can begin with discovered/imported counters rather than a perfect total.
  - users should see useful work start quickly even when exact ETA is unavailable.
- Why:
  Beginner-friendly UX is not "freeze first, be exact later." Large local libraries need bounded first paint, bounded JSON, and immediate progress more than perfect initial totals.
- Do not regress:
  Do not make `/api/stats` return unbounded tags/checkpoints/LoRAs. Do not add an unconditional pre-count directory pass to scan. Do not make Library facet UIs assume stats arrays are exhaustive. Do not hide the distinction between provisional scan/discovered progress and exact totals.
- Evidence:
  Current files: `backend/services/sorting_service.py`, `backend/image_manager.py`, `backend/routers/sorting.py`, `frontend/js/app.js`, `docs/API.md`, `.plans/sd-image-sorter-release/docs/api-contracts.md`, `backend/tests/test_image_manager.py`, `backend/tests/test_routers/test_sorting.py`.

## ADR-2026-05-14: Windows ONNX runtime repair must not resolve shared dependencies freely

- Status: accepted
- Context: A fresh v3.1.6 Windows portable launch installed `requirements-core.txt` first, then repaired CPU-only `onnxruntime` to `onnxruntime-gpu==1.21.0`. Installing the GPU wheel before removing the CPU wheel let the CPU uninstall damage the shared `onnxruntime` import package, causing a second force reinstall. The GPU install also resolved dependencies freely and upgraded shared pins such as `numpy==1.26.4` to a newer unpinned NumPy.
- Decision: Runtime swaps now uninstall the conflicting ONNX package before installing the target runtime, install target ONNX runtimes with `--no-deps`, and install CUDA/cuDNN extras under a pip-safe constraints file generated from `requirements-core.txt` by stripping extras-only syntax such as `uvicorn[standard]`. This preserves the locked core runtime while still allowing NVIDIA CUDA DLL packages to be added.
- Consequences: First launch on NVIDIA still downloads the large CUDA/cuDNN payload when GPU repair is needed, but it should not reinstall ONNX Runtime twice or drift already-pinned shared dependencies. Future runtime repair paths must keep this no-deps/constraints pattern unless the lock strategy changes.



## ADR-2026-05-16: Alternate-generator metadata detection (Fooocus / reForge / Gemini / gpt-image / Easy Diffusion / InvokeAI / SwarmUI / Draw Things)

- Status: accepted
- Context: Before v3.2.x the parser recognized only ComfyUI / NovelAI / WebUI / Forge. Anything else with usable metadata fell through to a generic `_parse_explicit_saved_metadata` fallback (which only understands plain `prompt`/`negative_prompt` keys) and was tagged `others` with most fields empty. PNGs from Fooocus (capitalised JSON keys in `Comment`), reForge (`Version: ...-reforge` in `parameters`), Easy Diffusion (`negative_prompt` chunk + `use_*_model` keys), InvokeAI (`invokeai_metadata`/`sd-metadata`/`Dream`), SwarmUI (`sui_image_params` JSON), Draw Things (XMP `exif:UserComment`), Gemini (Software=Gemini / nano-banana), and OpenAI gpt-image (Software=OpenAI/ChatGPT/DALL-E) all looked like "others" with no prompt visible.
- Decision: Added dedicated detectors in `backend/metadata_parser.py` (`_maybe_parse_fooocus`, `_maybe_parse_swarmui`, `_maybe_parse_invokeai`, `_maybe_parse_drawthings`, `_maybe_parse_easy_diffusion`) that run BEFORE the generic `_parse_explicit_saved_metadata` fallback so they can claim metadata that would otherwise be flattened to `others`. `_detect_webui_family_generator` now distinguishes `reforge` from vanilla `forge`. A new `_maybe_detect_ai_provider` runs after the Software-tag NAI/ComfyUI checks and surfaces `gemini` / `gpt-image` based on Software/Source/Make/Description fields with case-insensitive regexes (`gemini|imagen|nano-banana|google ai`, `gpt-image|chatgpt|openai|dall-e`). `MetadataParser.GENERATORS` now has 14 entries; `MetadataParser.OTHERS_BUNDLE` records the 9 IDs the gallery groups under the "Others" tab so the frontend can stay in sync.
- Easy Diffusion safeguard: the EasyDiffusion detector REQUIRES at least one of `use_stable_diffusion_model`, `use_lora_model`, `sampler_name`, `num_inference_steps`, `guidance_scale`, etc. before claiming an image. Plain `prompt`+`negative_prompt` JSON sidecars (no Easy-Diffusion-specific markers) intentionally still classify as `others` so we don't hijack arbitrary user-written sidecar files. Test `test_easy_diffusion_does_not_hijack_generic_sidecar` enforces this.
- Frontend: Top-level gallery tab bar stays at 5 primary generators (`comfyui` / `nai` / `webui` / `forge` / `unknown`) plus an `others` bundle tab. Clicking "Others" sets `filters.generators = OTHERS_GENERATOR_BUNDLE` (9 IDs). The Filter Criteria modal now lists all 14 generators as individual checkboxes so users can isolate "only Fooocus" or "only Gemini". Tab badge for "Others" sums `OTHERS_GENERATOR_BUNDLE.reduce(genCounts)` so it matches the gallery once the user clicks.
- Performance: Detector chain adds 0.13–0.30 ms/image worst case (verified with `.tmp/bench_parser.py`, 500 iters per fixture). No fast-path flag added because the cost is in-memory regex/dict access on metadata that was already loaded for the WebUI/NAI/ComfyUI checks; file IO is unchanged.
- Consequences: New generator IDs are stored verbatim in `images.generator` (no enum constraint, no migration needed). Adding another rare generator means: (1) extend `MetadataParser.GENERATORS` and `OTHERS_BUNDLE`; (2) add a `_maybe_parse_*` detector and wire it into the dispatch loop in `_detect_and_parse`; (3) add the value to `frontend/js/app.js::OTHERS_GENERATOR_BUNDLE`, `gallery.js::DEFAULT_GENERATOR_COLORS`, `virtual-gallery.js::GENERATOR_COLORS`, `stores/filter-store.js::DEFAULT_FILTER_GENERATORS`; (4) add an `i18n` key in `lang/en.js` + `lang/zh-CN.js` and a checkbox to `index.html#modal-generator-filters`; (5) update the index-keyed `_setCheckboxTexts('#modal-generator-filters', [...])` list in `ui-refresh.js::_translateGallery` (this list is order-sensitive; adding a row in the middle without updating it causes label/value mismatches in zh-CN — exactly the bug found and fixed during this change).

## ADR-2026-05-16: Icon-only navbar buttons must keep the emoji glyph un-clipped

- Status: accepted
- Context: A long-standing rule in `frontend/css/ui-refresh.css` truncates the LABEL span of nav-action buttons (`.nav-actions .btn span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`) so a longer Chinese / German translation cannot push the navbar layout. For text+icon buttons that worked correctly because `:last-child` was the text span. For icon-only buttons (Setup 🧰, Library 📚, Language 🌐, Update ⬆️, Help ❓) the same selector matched the EMOJI span — its sole child — and clipped it by ~24px. The visual symptom was most obvious in Chinese because nav-tab labels are wider in that locale and the layout was tighter.
- Decision: Scope the truncation rule to non-icon-only buttons (`.nav-actions .btn:not(.btn-icon-only) span:last-child`) and add an explicit "do not clip" rule for `.btn-icon-only span[aria-hidden]`: `overflow: visible; text-overflow: clip; flex: 0 0 auto;`. The truncation rule still protects against the original long-translation overflow on text+icon buttons (Import Images / AI Tag Images).
- Consequences: Future icon-only navbar buttons must use the `.btn-icon-only` class to inherit the un-clipped emoji rule. Adding a new icon-only button without that class would re-introduce the clipping. The Playwright sweep at `.tmp/zh_sweep.js` checks for `scrollWidth > clientWidth` overflow on every button across all 7 views; running it after CSS changes detects regressions immediately (it dropped from 35 incidents/run to 0 after the fix).


## ADR-2026-05-16: Fooocus disambiguation must beat NovelAI Comment block (real-shape lesson)

- Status: accepted, supersedes the synthetic-test assumptions in the alt-generator detection ADR above
- Context: My initial Fooocus tests used capitalised JSON keys (`Prompt`, `Negative Prompt`, `Base Model`) inferred from product docs. Actual upstream lllyasviel/Fooocus output (per `Fooocus/modules/private_logger.py`) writes a `Comment` PNG chunk with **lowercase** keys: `prompt`, `negative_prompt`, `base_model`, `performance`, `sampler`, `steps`, `seed`, `width`, `height`, `version`, `metadata_scheme`. The existing NovelAI block in `_detect_and_parse` only checks `"prompt" in comment_data or "uc" in comment_data`, so every real Fooocus image was being misclassified as `nai` with no negative prompt or checkpoint extracted. The synthetic test fixtures didn't catch this because they used the (non-shipping) capitalised shape. Cross-verified against `L:\Antigravitiy code\clone\sd-prompt-reader\sd_prompt_reader\format\fooocus.py` which reads `data_json.get("prompt")` / `data_json.get("negative_prompt")`.
- Decision: Two changes, both in `backend/metadata_parser.py`:
  - In `_detect_and_parse`, before running the NAI Comment branch, peek at `comment_data` for Fooocus-distinctive sibling keys (`base_model` / `performance` / `metadata_scheme` ∈ {fooocus,a1111} / `version` containing "Fooocus" / `negative_prompt` without `uc`) and delegate to `_maybe_parse_fooocus`. If Fooocus claims it, return immediately.
  - In `_maybe_parse_fooocus`, allow lowercase `prompt`+`negative_prompt` JSON to trigger detection when at least one Fooocus sibling key (`base_model`, `performance`, `sampler`, `steps`, `seed`, `metadata_scheme`, `sharpness`, `guidance_scale`) is also present. Without that sibling-key gate the detector would also catch raw NAI Comments (which carry only `prompt`+`uc`+`v4_prompt`).
- Tests: `test_fooocus_real_lowercase_shape`, `test_fooocus_real_shape_no_scheme_chunk`, and `test_nai_still_wins_when_uc_present` lock the behaviour. Real-image sampling on `L:\Pictures\AAA Reference` (156 random images) shows comfyui 26 / forge 20 / nai 21 / webui 6 / unknown 82 — i.e. NAI still detected at expected rate, no false shift to Fooocus.
- Consequences: When adding another Comment-based generator in future, the Fooocus disambiguator must either be extended or replaced by a generic "alt-Comment" dispatcher. The lesson: **never invent a JSON shape from product copy; always read the upstream emitter source code**. Reference parsers used during this work: `L:\Antigravitiy code\clone\sd-prompt-reader` and `L:\Antigravitiy code\clone\infinite-image-browsing\scripts\iib\parsers`.

## ADR-2026-05-16: C2PA byte-signature fallback for closed AI providers

- Status: accepted, refines the AI-provider detection in the alt-generator ADR
- Context: Plain Software/Make/Description tag detection is unreliable for closed-source AI providers because hosting platforms (Twitter, Discord, Pixiv, ...) often strip the EXIF tag while keeping the C2PA / "Content Credentials" manifest intact. The C2PA manifest is stored in PNG `caBX` chunks (JUMBF) or JPEG APP11 segments, with cleartext UTF-8 strings like `claim_generator_info` containing the provider name. Real-image sampling on `L:\Pictures\AAA Reference\undid` found `0E404B43F9DE4A3C6E642B62E44E4BD5.png` — a stripped ChatGPT image whose only signal was a C2PA manifest with `name: "OpenAI Media Service API"` near a `c2pa` anchor at offset 57.
- Decision: Add `_scan_c2pa_byte_signatures(image_path, file_size)` that opens the file, reads up to 512 KiB, requires at least one C2PA anchor (`c2pa`, `jumbf`, `claim_generator`, `contentcredentials`, `content credentials`) to be present, then matches a known provider marker (`gpt-image`/`chatgpt`/`openai` for OpenAI, `gemini`/`imagen`/`google ai`/`nano-banana`/`deepmind` for Google) inside the same blob. Wired as the LAST fallback in `_maybe_detect_ai_provider` so it only runs when EXIF-tag matching failed.
- Anchor-required guard: prevents false positives where an image's prompt mentions "openai-style" — without the anchor, the byte scan returns None. Tested by `test_c2pa_byte_scan_requires_anchor`.
- Why not c2pa-python? The full library validates cryptographic signatures and parses CBOR — heavy dependency for "label the image so the user can find it". The byte scan covers the user's stated need (find these images in the gallery, not prove provenance). If signature validation becomes a requirement later, drop in `c2pa-python` behind a feature flag and keep the byte-scan as the cheap pre-filter.
- Performance: bounded to 512 KiB of IO per image; only triggered when no other detector matched. Real-image scan of 156 reference images: full parse pipeline avg 4.13 ms / p95 12.2 ms / p99 29.3 ms; max 109 ms is dominated by Pillow opening a 32 MB PNG, not the C2PA scan.
- Tests: `test_c2pa_byte_signature_gpt_image`, `test_c2pa_byte_signature_gemini`, and `test_c2pa_byte_scan_requires_anchor` lock the behaviour. The first two synthesise a fake `caBX` chunk near the file header; the third verifies that prompts mentioning provider names without an anchor stay classified as their actual generator (webui, etc.).


## ADR-2026-05-16: Pixel-watermark detection for Gemini / gpt-image is METADATA-ONLY for now (correction)

- Status: accepted, partially supersedes the "C2PA byte-signature fallback" ADR above (clarifies what we DO NOT detect)
- Context: An earlier draft of these ADRs implied that detecting Google's SynthID invisible pixel watermark would require Google's proprietary model, and similarly that OpenAI's pixel signal was undetectable. After the user pointed at `https://github.com/aloshdenny/reverse-SynthID` (3.8k stars), I re-checked the public landscape:
  - **Gemini SynthID**: a public 90%-accurate detector EXISTS (`aloshdenny/reverse-SynthID`'s `RobustSynthIDExtractor`, FFT spectral analysis, no Google models needed). Other generic watermark detectors also exist: `facebook/watermark-anything` (ICLR 2025, MIT), `prithivMLmods/Watermark-Detection-SigLIP2`. So my original phrasing "needs Google's proprietary model" was wrong.
  - **OpenAI gpt-image**: no public open-source pixel-level detector found (verified via web search on 2026-05-16). C2PA Content Credentials remain the only public provenance signal.
- Decision: Keep current behaviour (metadata-only detection: EXIF Software/Make/Description + 512 KiB C2PA byte-scan with anchor-required guard) as the **default**, and explicitly NOT integrate `reverse-SynthID` in this release. Reasoning:
  - Cost: ~100–300 ms / image vs ~2 ms for the byte-scan. On a 70k-image library this adds hours to a re-scan.
  - License: `reverse-SynthID` is research-only; users would need explicit consent before bundling it.
  - Resolution gate: detector only ships codebooks for 1024×1024 and 1536×2816; arbitrary resolutions degrade. The user library is resolution-mixed.
  - False-positive risk on Stable Diffusion: ~10% inaccuracy on Gemini at supported resolutions, and the spectral patterns can collide with SD-image artifacts on a library that's 99% SD outputs.
  - Marginal value: the C2PA byte-scan already catches every Gemini / gpt-image sample in `L:\Pictures\AAA Reference\undid` we've tested. Pixel-detection only adds value when both EXIF and C2PA are stripped — a small subset.
  - Asymmetry: a Gemini detector exists, but no public OpenAI detector does. Shipping pixel-detection for one provider but not the other gives the user inconsistent confidence levels per generator.
- User-visible disclosure: the image-detail modal now shows an inline note for `gemini` and `gpt-image` images (`modal.aiProviderNote.gemini`, `modal.aiProviderNote.gptImage`) explaining we identified the source via metadata, NOT via the in-pixel watermark. The strings are localised in en + zh-CN. The note is amber-styled (FYI, not error) and sits right under the modal-meta row.
- TODO: tracked as `Debt-23` in `TECHNICAL_DEBT_NOTES.md` with the integration plan (opt-in Setup card, resolution gate, confidence threshold ≥ 0.85, must keep the modal hint visible at least until the detector is on by default). Re-evaluate in 6 months — the watermark-detection landscape is moving fast.
- TODO marker also placed in `backend/metadata_parser.py::MetadataParser._maybe_detect_ai_provider` docstring so anyone touching that function sees the gap and the candidate library before reinventing it.
- Lesson: I overstated "no detector exists" without searching. The user pushed back with a concrete link. Do a real web/GitHub/HuggingFace search before stating a capability is impossible — the field changes fast and 2024–2026 has been a watermarking research explosion.



## ADR-2026-05-16: SAM3 prepare must pin `+cuXXX` torch and drop `--extra-index-url` to survive flaky CUDA-index downloads (release blocker)

- Decision

  `backend/repair_torch_runtime.py:_install_cuda_torch` now pins the explicit local-version label on torch and torchvision (`torch==X.Y.Z+cu128`, etc.) and uses ONLY the cu-specific `--index-url` (no `--extra-index-url`). The numpy ABI constraint (`numpy<2.0`) is installed in a separate up-front pip call from PyPI. New regression test `test_cuda_install_pins_local_version_label_so_pypi_cannot_satisfy` locks the behaviour.

- Why this matters

  In the live fresh-portable verification on 2026-05-16, the user's first SAM3 prepare hit a transient `IncompleteRead` on download.pytorch.org's cu126 wheel (2.2 GB read, 400 MB short), then a DNS lookup miss during the cu121 retry. The pre-fix code combined `--index-url cu121 --extra-index-url https://pypi.org/simple` with a plain `torch==2.12.0` requirement. PyPI publishes the CPU wheel as exactly `2.12.0` (no local-version suffix), so pip happily satisfied the requirement from PyPI and reported success — but `torch.version.cuda` was empty afterwards and SAM3 refused to load with the error "this app's Python has CPU-only PyTorch; SAM3 needs a CUDA-enabled Torch build." The user had no clear path forward and would need to manually pip install CUDA torch — exactly the kind of "real bug masked by a workaround" pattern the user explicitly told us to avoid.

  The fix has three layers:
  1. `+cuXXX` local-version pin makes the requirement unsatisfiable on PyPI (PyPI's `2.12.0` is not `2.12.0+cu128` per PEP 440).
  2. Dropping `--extra-index-url` removes PyPI from the resolver entirely for the torch step. With `--no-deps`, no transitive deps need PyPI either.
  3. `numpy<2.0` is installed in a separate pip call against PyPI. numpy doesn't live on download.pytorch.org, so it has to come from PyPI — but moving it out of the cu-index call keeps that call single-source.

  Result: a transient CUDA-index error now produces a clean "could not find" pip error (which our retry-cascade can catch and try the next CUDA version), instead of a silent CPU install that corrupts SAM3 readiness.

- Sibling work

  This is the CUDA-torch counterpart of the ONNX Runtime "Step 0" fix from earlier the same day (`repair_onnxruntime.py`). Both bugs share the pattern: the repair script returned success after a no-op or wrong-target install, leaving downstream model loading broken with a confusing message. Both are now covered by regression tests.

- Files changed

  `backend/repair_torch_runtime.py`, `backend/tests/test_repair_torch_runtime.py`, `CHANGELOG.md`, `docs/API.md` (separate fix: documented `GET /api/models/bulk-bundle` endpoint that was added with the bulk-download feature but missed the docs sync).

- How to verify

  `python -m pytest backend/tests/test_repair_torch_runtime.py -v`. All 10 tests must pass. The new test asserts both halves of the fix: (a) torch requirement carries `+cuXXX`, and (b) pip args do NOT include `--extra-index-url`.




## ADR-2026-05-16: Default scan back to count-first ("precise_total=True"), keep streaming as opt-out

- Decision

  `image_manager.scan_folder` default for `precise_total` is now `True`. The scanner walks the folder once to discover the total file count, emits ``counting → counted → importing`` progress phases, then runs the import + metadata pipeline with a real `current/total` denominator. Callers that legitimately need to skip the count walk (e.g. network-share scans where the walk itself takes minutes) opt out with `precise_total=False`.

- Why this matters

  In a previous iteration the default was flipped to `False` ("single-pass scan") with the rationale that "Large/network folders should start importing immediately instead of walking the full tree once just to get an ETA." That's correct in the abstract, but it broke the typical-user UX: a 48 K-file library on a local SSD takes ~80 s to scan; the count walk alone takes ~1–2 s. Skipping the count means heartbeats show `processed=14 800/?` for the entire 80 s — there's no ETA, no apparent finish line, the progress bar can't fill, and several seconds of confusion at the start because the user has no idea whether 14 800 is 10 % or 90 % of the work.

  User report (2026-05-16): "Why the gallery scanning changed?? The old design is a double scanning with first very quick to estimate the time and the images numbers, and then it can show the estimate time and the whole number, why this functions changed?"

  The single-pass default optimised for the rare worst case (huge or slow medium) at the cost of the typical-case UX. The trade-off is wrong because:
  1. Walking 48 K entries via `os.scandir` on a local SSD is ~1 s. Even a 1 M-entry library is ~10–20 s. The cost is small.
  2. The metadata-parse phase dominates total scan time by 1–2 orders of magnitude, so the count walk barely shifts overall completion.
  3. With no `total`, the progress UI cannot show ETA, percent complete, or remaining time. That's a real loss of information, not a cosmetic difference.

  For the rare case of multi-million-file network shares where the walk itself is slow, `precise_total=False` is still available as an explicit opt-out. The new default just stops penalising the 99 % to optimise for the 1 %.

- Sibling work

  Coupled with the metadata-parser fix in the same commit: when the PNG fast-path fails with `Invalid PNG signature`, fall through to Pillow's content-sniff path. This is what handles JPEG / WEBP / GIF files saved with `.png` extension — common in real libraries because Civitai, Discord, browsers, etc. rename images during upload/download. Both fixes target the same user complaint about the v3.2.0 scanner UX.

- Files changed

  `backend/image_manager.py`, `backend/metadata_parser.py`, `backend/tests/test_image_manager.py`, `backend/tests/test_metadata_parser.py`, `backend/tests/test_routers/test_sorting.py`.

- How to verify

  `python -m pytest backend/tests/test_image_manager.py -k scan_folder backend/tests/test_metadata_parser.py::TestMetadataParserBase::test_parse_jpeg_with_png_extension_falls_through_to_pillow backend/tests/test_metadata_parser.py::TestMetadataParserBase::test_parse_png_fast_path_still_rejects_genuinely_truncated_png`. All 26 tests must pass.



## ADR-2026-05-16-copy-default: Auto-Separate and Manual Sort default to "copy", not "move"

- Decision

  The Auto-Separate (`autosep.js`) and Manual Sort (`manual-sort.js`) file-action mode defaults to `copy` (non-destructive) for any user without a saved preference. Locations:

  - `frontend/index.html`: the three radio groups (`autosep-operation-mode-main`, `autosep-operation-mode-settings`, `manual-sort-operation`) ship with `value="copy"` carrying the `checked` attribute.
  - `frontend/js/autosep.js`: `DEFAULT_AUTOSEP_SETTINGS.operationMode = 'copy'` and `normalizeAutoSepOperationMode` falls back to `'copy'` for unrecognized values.
  - `frontend/js/manual-sort.js`: the localStorage fallback and `normalizeManualSortOperationMode` both fall back to `'copy'`.
  - `frontend/js/app.js`: `startSortSession`'s `operationMode` parameter default + the request body's `operation_mode` fallback are both `'copy'`.
  - The "Action mode: …" status line and helper text under the manual-sort radios reflect copy as the initial state before JS runs.

  The user can still switch to `move` per session via the radio buttons. Their last choice is persisted to localStorage so power users only flip once.

- Why this matters

  Auto-Separate and Manual Sort can move thousands of files in a single click. A user who clicks "Start" before reading the radio labels — or before noticing the radios at all — could destructively move their entire library into the wrong folders.

  User instruction (2026-05-16): "yes, change to copy as default. then mark those default value to the related document to avoid further another ai to change them without asking or considering."

  The sort tools' purpose is sorting, but the safer-by-default principle for batch destructive operations matters more than the "moves are the point" argument. Power users move once, then their localStorage choice sticks. New users get the safe path until they actively choose otherwise.

- Sibling work

  This decision is logged in the table at the bottom of `docs/AI_PRINCIPLES.md` Principle #11 alongside the other locked defaults. Any future agent attempting to flip these back to `move` will need to satisfy the three-part justification (named-commit + ADR + regression test), per Principle #11.

- Files changed

  `frontend/index.html`, `frontend/js/autosep.js`, `frontend/js/manual-sort.js`, `frontend/js/app.js`, `docs/AI_PRINCIPLES.md` (table updated).

- How to verify

  Manual: open the gallery, expand Auto-Separate, confirm the "Copy and keep originals" radio is selected. Open Manual Sort, confirm same. Behavioural test: ``node .tmp/test_default_operation_mode.js`` (created in the same commit) loads the page in headless Chromium and asserts the checked radio for each of the three groups is the copy variant.


## ADR-2026-05-20: v3.2.1 LoRA export, VLM retry/debug, and model download source semantics

- Status: accepted
- Context:
  A user found that the "LoRA Training Template" export path had not been tested, the export modal was visually cramped, the blacklist did not apply reliably to final export captions, and VLM tagging progress lost useful success/error/API status. The same release also added a Download Source selector, but only dependency/PyTorch mirror paths were consistently covered; model downloads still had separate code paths for WD14, ToriiGate, CLIP/FastEmbed, Aesthetic/OpenCLIP, Artist/Kaloscope, SAM3, Civitai, and GitHub-hosted small files.
- Decision:
  Same-name batch export now has an explicit `Tags + Natural Language caption` mode (`tags_nl`): prefix, filtered tags, then the VLM/natural-language caption, with no original SD prompt. `Prompt + Natural Language caption` remains the mode that includes the original prompt. LoRA Training Template preview must be tested through the real selection-panel entry and real backend-scanned images, not by injecting fake `.gallery-item` DOM.
- VLM UI contract:
  VLM progress/status belongs only to the Natural Language + VLM API source workflow. It must show success count, error count, token count, API status/latency/error, and keep failed image IDs so a Retry Failed button can rerun only those images. The API chat/debug modal is a user-visible inspection tool; it must show sanitized request/response content without leaking API keys.
- Download source contract:
  The Download Source setting is not decorative. HuggingFace-hosted models must use the shared endpoint order from `backend/model_download_sources.py`: `auto` = explicit `HF_ENDPOINT` if any, then official HF, then hf-mirror; `hf-mirror` = hf-mirror first; `modelscope` = use real ModelScope only when a compatible ModelScope repo exists, otherwise hf-mirror first with a clear fallback. This now covers WD14, ToriiGate, Artist/Kaloscope runtime use, CLIP/FastEmbed, and Aesthetic/OpenCLIP. Direct providers that are not HuggingFace-compatible stay direct: Civitai Privacy YOLO, GitHub LSNet runtime / LAION linear head, and SAM3's ModelScope checkpoint path.
- Tests:
  `tests/e2e/task7_export_unified.spec.js` is the release guard for LoRA Training Template and export preview: it creates real PNGs, scans them through `/api/scan`, imports tags + `ai_caption`, opens the modal from the real selected-gallery action, checks `tags_nl`, blacklist/cleanup, Add All overrides, clipboard path, and viewport containment. `tests/e2e/task4_tagger_tabs.spec.js` guards VLM source auto-save, progress/API status, API Chat, and Retry Failed. Backend tests in `test_mirror_selector.py`, `test_model_service.py`, and `test_artist_identifier_runtime.py` lock shared model download-source semantics.
- Do not regress:
  Do not add a new model downloader that reads only raw `HF_ENDPOINT` or hard-codes `huggingface.co` when it is a HuggingFace-hosted model. Do not test export UI by fake DOM injection when the bug class depends on real backend data and selection state. Do not let VLM progress messages follow every tagger tab/source.


## ADR-2026-05-20: Color filters are a full Gallery contract, and VLM debug endpoints are redacted URLs

- Status: accepted
- Context:
  The v3.2.1 release notes advertised color-based Gallery filtering and sorting, but the shipped stack only partially wired it: cursor pagination passed color filters, while offset pagination, selection tokens, selected-result exports, and frontend filter controls did not. The same review found VLM debug chat already hid keys and image bytes but still exposed endpoint userinfo/query/fragment, which can contain bearer tokens on OpenAI-compatible gateways.
- Decision:
  Color filters are first-class Gallery filters, not API-only experimental fields. They must travel through `GET /api/images`, `POST /api/images/selection-token`, `GET /api/images/selection-chunk`, selection-based export/delete/remove paths, saved filter state, and scoped Auto-Separate / Manual Sort filter copies. The frontend must expose brightness range, color temperature, brightness distribution, and color sort options directly in the existing filter/sort surfaces. VLM API Chat may show the endpoint scheme/host/path for debugging, but userinfo, query string, and fragment must be redacted.
- Do not regress:
  Do not add a new Gallery filter field without updating list retrieval, filtered selection token contracts, frontend saved state, summary formatting, and docs together. Do not expose full VLM endpoint URLs in debug events; treat query strings and fragments as secret-bearing.
- Tests:
  Backend tests cover color filters through offset pagination and selection tokens, VLM debug endpoint redaction, corrupt VLM concurrency setting normalization, color-analysis path resolution, and the router/service boundary for export preview. Frontend validation must include a real browser click through the filter modal so the color params are observed on `/api/images`.


## ADR-2026-05-24: Dataset Maker fresh sessions seed Anime LoRA defaults

**Status:** Accepted (v3.2.2).

**Context:** The Dataset Maker tab is the entry point for batch LoRA training-set work. Without any pre-fill, a first-time user sees three empty controls (common tags textarea, underscore-to-space checkbox, naming preset radios) and has to know what each one should be. The most common LoRA failure mode in real user reports is "I forgot to add masterpiece / best_quality" or "my filenames look like 4B2965D015FF06CF.png so the trainer can't sequence them". Setting opinionated defaults avoids that footgun while staying easy to change.

**Decision:** On the very first init (no `sd-image-sorter-dataset-customized` flag in localStorage), the frontend pre-fills:

- `dataset-common-tags` textarea = `masterpiece, best_quality`
- `dataset-underscore-to-space` checkbox = ON (preserve `score_*` prefixes — already wired in the export-template engine)
- naming preset radio = `renumber` (so output is `your_lora_001.png` etc, not random hex stems)
- trigger field placeholder = `your_lora_trigger`

A new "🎌 Apply Anime LoRA defaults" button in Step B re-applies these in one click and clears the customized flag.

**Locked invariants** (per AI_PRINCIPLES.md §11 user-visible defaults):

- The default ONLY applies on the very first init. As soon as the user types in any of the affected fields or picks a different preset, the localStorage flag is set and the defaults stop self-applying.
- The defaults DO NOT touch `image_op` (still `copy`, locked by ADR-2026-05-16-copy-default) or `overwrite_policy`. Those have their own ADRs.
- "🎌 Apply Anime LoRA defaults" is destructive of user-typed values. The button is in the Step B card next to the help text, NOT next to the export confirm so it cannot be misclicked into a destructive overwrite during the actual export click path.

**Regression test:** `backend/tests/test_dataset_anime_defaults.py::test_anime_defaults_button_present_in_dataset_maker_html` asserts the markup carries the button id + i18n key. The localStorage gating is exercised by the frontend e2e in `.tmp/v322-folder-import.js` and friends, which clear the flag before each run; the defaults applying without overriding existing input is a property the regression test pins at the HTML/i18n level.

**What future AI must not do:**

- Do NOT silently change the default common-tags string. If `masterpiece, best_quality` is wrong for a future base model, add a SECOND preset button rather than flipping the default.
- Do NOT make the defaults survive a user's explicit edit. The localStorage flag is the only correct gating signal.
- Do NOT bundle the Anime defaults flip with a "release prep" or "stability hardening" commit; per AI_PRINCIPLES §11 a default flip needs its own commit, this ADR, and a regression test in the same patch.


### ADR-AI-20260524-114: macOS portable bundle deferred

- Status: accepted
- Area: Release packaging / macOS distribution / public download UX
- Context:
  PR #14 (Phase 1) and PR #15 (Phase 2) shipped a Linux portable bundle for x86_64 and aarch64 that bundles `cpython-3.13.13` from `astral-sh/python-build-standalone`. The natural follow-up question is whether to ship a matching `macos-portable-arm64.tar.gz` and `macos-portable-x86_64.tar.gz` so macOS users get the same "extract and run" experience. PBS publishes both Darwin variants (~30-40 MB each) so the build wiring would be cheap; the question is whether the user-facing experience is good enough to justify shipping it.
- Decision:
  Skip macOS portable bundles for v3.2.x. Keep `linux.tar.gz` (source) plus `./run.sh` as the supported macOS path. Document the deferral in `docs/RELEASE_PACKS.md` so users searching for a macOS portable see it as a deliberate "not yet" and not an oversight, and so future contributors do not silently ship an unsigned macOS bundle without revisiting the trade-offs below.
- Why not now:
  1. **Gatekeeper friction without notarization.** Apple's Gatekeeper rejects unsigned downloaded binaries with a `"developer cannot be verified"` warning on first launch. Notarization requires an active [Apple Developer Program](https://developer.apple.com/programs/) subscription (\$99/yr) and per-release signing/upload to Apple's notary service. Without that, every user has to right-click → Open or `xattr -dr com.apple.quarantine sd-image-sorter/` before launching — a much worse first-run experience than the Windows / Linux portable double-click flow. Shipping an unsigned bundle would deliver a worse UX than the existing source-install path for most users, defeating the purpose of "portable means easy".
  2. **macOS users almost always already have Python.** Homebrew, `pyenv`, `asdf`, and `uv` are standard on macOS dev machines. The "no system Python" pain point that drove the Linux portable basically does not exist on macOS — meaning the marginal user benefit of a macOS portable is much smaller than for Linux.
  3. **macOS Intel is fading upstream.** PyTorch dropped macOS Intel (`x86_64-apple-darwin`) wheels after `2.2.2`; PR #12 already pins that legacy version explicitly because the resolver cannot find anything newer. Shipping an Intel macOS portable today freezes users to an upstream-deprecated stack — worse than `./run.sh` against `brew install python@3.13`, which lets the user manage upgrades on their own schedule.
  4. **Apple Silicon already works fine via source.** M1 / M2 / M3 / M4 users get full functionality through `./run.sh`; ONNX Runtime auto-selects the optimal provider. No CUDA on macOS means the heavy GPU paths are not the value-add anyway.
- When to revisit:
  Re-open this decision if any of these becomes true:
  - An Apple Developer Program account becomes available for notarization, removing Gatekeeper friction.
  - A real macOS user reports that `./run.sh` is broken in a way the source path cannot fix.
  - PyTorch ships a renewed macOS Intel wheel line that justifies revisiting the legacy pin.
  - The macOS user share grows enough that the current source-install flow becomes a measurable adoption blocker.
- Do not regress:
  Do not silently add a `macos-portable-*.tar.gz` build target without a fresh ADR re-opening this one. Do not ship an unsigned macOS bundle as the recommended download path in README — if a future PR ships one for advanced users, label it explicitly "unsigned, Gatekeeper bypass required" and keep `./run.sh` as the recommended default. Do not remove the source `./run.sh` macOS-detection path (PR #12) on the assumption that "we have a macOS portable now"; that path stays the supported macOS install for the foreseeable future.
- Evidence:
  Current files: `docs/RELEASE_PACKS.md` (macOS section explaining the deferral + workarounds), `run.sh` (Darwin detection from PR #12), `backend/requirements.in` / `backend/requirements.txt` (per-platform pins for darwin x86_64 from PR #12 / #11), `scripts/build_release_packages.py` (linux-portable wiring that deliberately does not have a Darwin counterpart).
- Validation:
  Doc-level only — no test pins this decision because it is a "do not silently ship X" rule. Future contributors should grep for `macos-portable` in the build script and the workflow YAML before adding any Darwin asset and re-read this ADR.


### ADR-AI-20260715-115: AI runtime upgrades must match published wheels and remain security-clean

- Status: accepted
- Area: dependency security / PyTorch CUDA repair / ONNX hardware detection / macOS source support / reproducible locks
- Context:
  A dependency refresh raised Pillow and setuptools security floors and moved the full AI runtime to Torch 2.13 / torchvision 0.28. Initial CUDA fallback constants reused those versions on indexes where the pair was never published, while the available cu124/cu121 fallback pairs carried many current advisories. The universal dev lock also explicitly omitted setuptools even though CI installs that lock alone. On current Windows NVIDIA drivers, PowerShell CIM can be denied while nvidia-smi still works, and NVIDIA's 610 driver branch reports CUDA UMD Version rather than the older CUDA Version header.
- Decision:
  Pillow stays at or above 12.3.0 and setuptools stays at or above 83.0.0 in core, full, and dev/CI locks. The dev/CI lock deliberately extends the core lock and excludes Torch, CUDA, and other full-AI packages, so Intel Mac contributors and CI can install it; the separate full-AI lock retains the platform boundary below. All compiled locks use uv universal resolution and include fresh source-input hashes. Windows/Linux Torch stays at 2.13.0 with torchvision 0.28.0. Optional Prepare compares installed packages against the exact compiled lock, so an older Torch no longer passes through the loose `torch>=2.0` declaration, including the explicit Torch requirement in the YOLO group. Windows CUDA repair treats only the published clean cu130/cu126 release pair with an exact fresh Torch/torchvision/CUDA probe as ready; it selects one highest compatible index rather than silently falling back to another wheel family. A driver below CUDA 12.6 keeps the existing runtime and receives an actionable driver-upgrade message before any package download. A custom CUDA index URL must end in cu130 or cu126, preserve the real local-version suffix, and remain within the detected driver capability.
  The core/dev locks split macOS ONNX Runtime by Python: 1.19.2 for Python 3.12 and 1.23.2 for Python 3.13. Version 1.23.2 is the newest official release with both x86_64 and arm64 cp313 wheels; allowing an unconstrained newer resolver choice would select an arm64-only wheel and break Intel Mac contributors.
- macOS boundary:
  Core gallery, metadata, sorting, and ONNX features remain available on Intel and Apple Silicon Macs. General Torch-backed optional features require Apple Silicon on macOS 14 or newer, matching the security-supported Torch 2.13 wheel. SAM3 remains a separate CUDA-only product path and fails before checkpoint access or download on every macOS version. Intel Mac and pre-14 Apple Silicon fail before other optional/full-AI installation with an actionable message instead of silently installing Torch 2.2.2 or 2.10.0. This supersedes only the legacy Torch compatibility parts of ADR-AI-20260502-83, ADR-AI-20260502-87, and ADR-AI-20260524-114; ADR-AI-20260502-86's SAM3 CUDA-only boundary, the lightweight-runtime decision, and the no-portable-bundle decision remain in force.
- Hardware detection:
  Windows ONNX detection validates CIM JSON and falls back to the vendor-supported nvidia-smi probe on command failure, empty output, invalid output, or no usable CIM devices. The shared NVIDIA probe is also used on Linux. Torch CUDA capability parsing accepts both CUDA Version and CUDA UMD Version headers, and probe stderr is suppressed while structured diagnostic fields are logged.
- Primary evidence:
  Pillow 12.3.0 security notes: https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html. Setuptools fix: https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c and https://github.com/pypa/setuptools/releases/tag/v83.0.0. Torch release and wheel metadata: https://github.com/pytorch/pytorch/releases/tag/v2.13.0, https://pypi.org/pypi/torch/2.13.0/json, https://pypi.org/pypi/torchvision/0.28.0/json, and the official cu130/cu126/cu124/cu121 indexes under https://download.pytorch.org/whl/. Universal/no-emit semantics: https://docs.astral.sh/uv/reference/cli/#uv-pip-compile--universal and https://docs.astral.sh/uv/reference/cli/#uv-pip-compile--no-emit-package.
  ONNX Runtime wheel metadata: https://pypi.org/pypi/onnxruntime/1.19.2/json and https://pypi.org/pypi/onnxruntime/1.23.2/json. An isolated pip-audit query for onnxruntime 1.23.2 reported no known vulnerabilities.
- Real validation:
  On an RTX 3090, an isolated torch 2.13.0+cu130 / torchvision 0.28.0+cu130 environment passed CUDA matmul, Conv2d, torchvision NMS, and synchronize. The same environment loaded the product's Kaloscope/LSNet checkpoint and returned three mapped artist predictions in 7.656 seconds; a separate real ToriiGate product-class run on an indexed image returned a valid natural-language caption in 15.982 seconds with `error=null`. A read-only probe of the current app venv correctly detected the RTX 3090 and CUDA UMD 13.3, and exposed its older torch 2.9.1+cu128 as the regression case that the exact-lock/repair tests now cover. Focused runtime, Model Service, lock, release, lint, shell, and dependency-security gates pass after the platform policy change.
  uv 0.11.23 also resolved the core-only dev lock for Intel Mac without installation: x86_64-apple-darwin / Python 3.12 selected 85 packages with ONNX Runtime 1.19.2, and Python 3.13 selected 85 packages with ONNX Runtime 1.23.2; neither closure contained Torch, CUDA, NVIDIA, Ultralytics, or Triton packages.
- Do not regress:
  Do not invent a Torch/version pair for a CUDA index without querying the official wheel repository. Do not use a loose direct requirement to decide whether an exact locked runtime is already satisfied. Do not report a CUDA install as successful unless a fresh interpreter reports the exact selected Torch/torchvision/CUDA build and `torch.cuda.is_available() == True`. Do not silently fall back to another CUDA wheel family after a selected index fails. Do not auto-downgrade a clean runtime to advisory-bearing legacy Torch for GPU acceleration. Do not reintroduce full-AI packages into the cross-platform dev/CI lock or omit its setuptools security floor. Do not promise Intel Mac Torch features or macOS SAM3 until upstream and the product path are both verified. Do not treat primitive tensor smoke as sufficient when a real product model and representative input are available.
