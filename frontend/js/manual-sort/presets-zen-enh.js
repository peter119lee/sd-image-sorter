/**
 * manual-sort/presets-zen-enh.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 635-948 + 1120-1207 (Aurora Phase 3 Slice 2 sort
 * enhancements): setup-visibility guard, the live scoped image count, focus
 * (zen) mode, the HUD mute-button sync, named full-config presets,
 * maybeAdoptManualSortFiltersFromGallery, folder summaries and the resume
 * banner renderer. Classic script: loads after manual-sort/state-constants.js.
 */
// ============== Sort enhancements (Aurora Phase 3 Slice 2) ==============
//
// Three additive setup-screen conveniences: a live scoped image count above the
// Start button, a focus (zen) mode toggle on the sorting stage, and named
// full-config presets. All are localStorage-backed and never touch the server
// session shape.

// --- Live scoped image count ---------------------------------------------
// Only fetch when the manual-sort setup is actually on screen; the summary hook
// fires on init/languageChange even while the view is hidden.
function isManualSortSetupVisible() {
    const sortingView = document.getElementById('view-sorting');
    if (sortingView && !sortingView.classList.contains('active')) return false;
    const manualView = document.getElementById('view-manual');
    if (!manualView || manualView.style.display === 'none') return false;
    const setup = document.getElementById('sort-setup');
    if (!setup || setup.style.display === 'none') return false;
    return !ManualSortState.active;
}

let _manualSortScopeCountAbort = null;
let _manualSortScopeCountSeq = 0;

// Count how many images the current Manual Sort filters would pull in and show
// it above the Start button. Reuses the Gallery count endpoint + shared
// buildFilterQueryParams so the number matches what a session would enqueue.
// Best-effort: on any failure the row shows an unavailable note rather than a
// stale/false count. The `≈` framing keeps it honest (filters + moves race).
async function refreshManualSortScopeCount() {
    const wrap = document.getElementById('sort-scope-count');
    const text = document.getElementById('sort-scope-count-text');
    if (!wrap || !text) return;

    // Don't spend a request while the setup is off-screen.
    if (!isManualSortSetupVisible()) {
        wrap.hidden = true;
        return;
    }

    const API = window.App?.API;
    if (!API || typeof API.buildFilterQueryParams !== 'function') {
        wrap.hidden = true;
        return;
    }

    let params;
    try {
        params = API.buildFilterQueryParams(buildManualSortFilterContract(getManualSortFilters()));
    } catch (_) {
        wrap.hidden = true;
        return;
    }

    // Reflect a counting state immediately so a slow fetch never looks frozen.
    wrap.hidden = false;
    wrap.classList.add('is-counting');
    wrap.classList.remove('is-failed');
    text.textContent = manualSortText('manual.scopeCountCounting', 'Counting images…', '正在统计图片…');

    const seq = ++_manualSortScopeCountSeq;
    try {
        if (_manualSortScopeCountAbort) _manualSortScopeCountAbort.abort();
        _manualSortScopeCountAbort = new AbortController();
        const resp = await fetch(`/api/images/count?${params}`, { signal: _manualSortScopeCountAbort.signal });
        if (seq !== _manualSortScopeCountSeq) return; // a newer refresh superseded us
        if (!resp.ok) throw new Error(`count ${resp.status}`);
        const data = await resp.json();
        const total = Number(data?.total);
        wrap.classList.remove('is-counting');
        if (Number.isFinite(total) && total >= 0) {
            text.textContent = formatManualSortI18n('manual.scopeCount', '≈{count} images in scope', {
                count: total.toLocaleString(),
            });
        } else {
            // total < 0 is the count-skipped sentinel for very large libraries;
            // hide rather than print a nonsense number.
            wrap.hidden = true;
        }
    } catch (e) {
        if (e?.name === 'AbortError' || seq !== _manualSortScopeCountSeq) return;
        wrap.classList.remove('is-counting');
        wrap.classList.add('is-failed');
        text.textContent = manualSortText('manual.scopeCountFailed', 'Count unavailable', '无法统计数量');
    }
}

// --- Focus (zen) mode -----------------------------------------------------
function getManualSortZenPref() {
    try { return localStorage.getItem(MANUAL_SORT_ZEN_KEY) === '1'; } catch (_) { return false; }
}

// Collapse the app chrome for a distraction-free WASD stage. A single
// html.sort-zen class does the work in CSS (--nav-height:0 + hidden top bar);
// the preference persists so the next session remembers it.
function applyManualSortZen(on, { persist = true } = {}) {
    const enabled = !!on;
    document.documentElement.classList.toggle('sort-zen', enabled);
    if (persist) {
        try { localStorage.setItem(MANUAL_SORT_ZEN_KEY, enabled ? '1' : '0'); } catch (_) { /* ignore */ }
    }
    const btn = document.getElementById('btn-sort-zen');
    if (btn) {
        btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        btn.classList.toggle('is-active', enabled);
        btn.title = enabled
            ? manualSortText('manual.zenExit', 'Exit focus mode', '退出专注模式')
            : manualSortText('manual.zenEnter', 'Focus mode', '专注模式');
    }
}

// Visual-only strip used when leaving the stage — never clears the saved
// preference, so re-entering a session restores the chosen focus state.
function clearManualSortZen() {
    document.documentElement.classList.remove('sort-zen');
}

// Repaint the on-stage mute button from the live AudioManager state. Module-level
// so it can re-sync on stage entry — the global Settings sound toggle mutates
// AudioManager without notifying this button, which would otherwise show a stale
// icon/aria-pressed until its own next click.
function syncSortMuteButton() {
    const muteBtn = document.getElementById('btn-sort-mute');
    const muteIcon = document.getElementById('sort-mute-icon');
    if (!muteBtn) return;
    // Read the real boolean: the sort blip defaults OFF, and if audio.js never
    // loaded nothing can play, so "muted" is the honest paint in both cases.
    const on = window.AudioManager?.enabled === true;
    if (muteIcon) muteIcon.innerHTML = on ? "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-volume\"/></svg>" : "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-volume-off\"/></svg>";
    muteBtn.setAttribute('aria-pressed', on ? 'false' : 'true');
    muteBtn.title = on
        ? manualSortText('manual.muteSounds', 'Mute sort sounds', '静音排序音效')
        : manualSortText('manual.unmuteSounds', 'Unmute sort sounds', '取消静音');
}

// --- Named full-config presets -------------------------------------------
function getManualSortPresets() {
    try {
        const parsed = JSON.parse(localStorage.getItem(MANUAL_SORT_PRESETS_KEY) || '[]');
        return Array.isArray(parsed) ? parsed.filter((p) => p && typeof p.name === 'string') : [];
    } catch (_) {
        return [];
    }
}

function saveManualSortPresets(list) {
    try { localStorage.setItem(MANUAL_SORT_PRESETS_KEY, JSON.stringify(list)); } catch (_) { /* ignore */ }
}

// Snapshot everything the setup screen holds. Folder paths read straight from
// the DOM inputs (the source of truth for folder-typed slots); slots/filters/
// mode/action come from state.
function captureManualSortPreset(name) {
    const folders = {};
    document.querySelectorAll('.folder-path-input').forEach((input) => {
        const key = input.dataset.key;
        const value = (input.value || '').trim();
        if (key && value) folders[key] = value;
    });
    return {
        name,
        mode: getManualSortSelectedMode(),
        operationMode: getManualSortOperationMode(),
        filters: serializeManualSortFilters(getManualSortFilters()),
        collectionSlots: { ...(ManualSortState.collectionSlots || {}) },
        folders,
    };
}

// Restore a captured preset into the live setup and repaint every derived UI.
function applyManualSortPreset(preset) {
    if (!preset) return;

    setManualSortFilters(preset.filters || {});
    markManualSortScopeCustomized();

    // Collection slots — drop ids that no longer exist so the UI can't show a
    // ghost assignment.
    const validIds = new Set((ManualSortState.collectionsCache || []).map((c) => c.id));
    const slots = {};
    MANUAL_SORT_SLOT_KEYS.forEach((key) => {
        const id = preset.collectionSlots?.[key];
        slots[key] = Number.isInteger(id) && id > 0 && validIds.has(id) ? id : null;
    });
    ManualSortState.collectionSlots = slots;
    saveManualSortSlotCollections();

    // Folder inputs + persisted per-slot folder paths.
    document.querySelectorAll('.folder-path-input').forEach((input) => {
        const key = input.dataset.key;
        const value = preset.folders?.[key] || '';
        input.value = value;
        ManualSortState.folders[key] = value;
        try { localStorage.setItem(`sort-folder-${key}`, value); } catch (_) { /* ignore */ }
    });

    setManualSortSelectedMode(preset.mode || 'slot');
    setManualSortOperationMode(preset.operationMode || 'copy', { persist: true, updateUi: true });

    // Reset each slot's folder/collection radio to match the preset BEFORE
    // refreshing the slot UI. refreshManualSortSlotUi reads the LIVE radio state
    // (explicitCollectionType), so a slot still checked "collection" from a
    // previous preset would otherwise hide the restored folder path behind an
    // empty collection dropdown.
    MANUAL_SORT_SLOT_KEYS.forEach((key) => {
        const isCollection = Number.isInteger(slots[key]) && slots[key] > 0;
        document.querySelectorAll(`input[name="slot-type-${key}"]`).forEach((radio) => {
            radio.checked = isCollection ? radio.value === 'collection' : radio.value === 'folder';
        });
    });

    populateManualSortCollectionSelects();
    MANUAL_SORT_SLOT_KEYS.forEach(refreshManualSortSlotUi);
    if (typeof updateFolderNames === 'function') updateFolderNames();
    updateManualSortFilterSummary();
}

// Rebuild the preset <select> with DOM methods (the security hook blocks
// innerHTML). Keeps the current selection if it still exists.
function populateManualSortPresetSelect() {
    const select = document.getElementById('sort-preset-select');
    if (!select) return;
    const presets = getManualSortPresets();
    const prev = select.value;
    while (select.firstChild) select.removeChild(select.firstChild);

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = presets.length
        ? manualSortText('manual.presetChoose', 'Choose a preset…', '选择预设…')
        : manualSortText('manual.presetNone', 'No saved presets', '暂无预设');
    select.appendChild(placeholder);

    presets.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
    });

    if (prev && presets.some((p) => p.name === prev)) select.value = prev;
    const del = document.getElementById('btn-sort-preset-delete');
    if (del) del.disabled = presets.length === 0;
}

async function handleManualSortPresetSave() {
    const raw = await window.App.showInputModal(
        manualSortText('manual.presetSaveTitle', 'Save Sort Preset', '保存排序预设'),
        manualSortText(
            'manual.presetSavePrompt',
            'Name this preset. It stores the folders/collections, slot layout, mode, action, and filters currently set here.',
            '给这套预设取个名字。它会记住当前的文件夹/收藏夹、槽位布局、模式、动作和筛选。'
        ),
        ''
    );
    if (raw == null) return; // cancelled
    const name = raw.trim();
    if (!name) {
        window.App.showToast(manualSortText('manual.presetNameRequired', 'Enter a preset name', '请输入预设名称'), 'error');
        return;
    }

    const presets = getManualSortPresets();
    const existingIdx = presets.findIndex((p) => p.name === name);
    if (existingIdx >= 0) {
        const ok = await new Promise((resolve) => {
            window.App.showConfirm(
                manualSortText('manual.presetOverwriteTitle', 'Replace Preset', '覆盖预设'),
                formatManualSortI18n('manual.presetOverwrite', 'A preset named "{name}" already exists. Replace it?', { name }),
                () => resolve(true),
                () => resolve(false)
            );
        });
        if (!ok) return;
        presets[existingIdx] = captureManualSortPreset(name);
    } else {
        presets.push(captureManualSortPreset(name));
    }
    saveManualSortPresets(presets);
    populateManualSortPresetSelect();
    const select = document.getElementById('sort-preset-select');
    if (select) select.value = name;
    window.App.showToast(formatManualSortI18n('manual.presetSaved', 'Preset "{name}" saved', { name }), 'success');
}

function handleManualSortPresetLoad() {
    const name = document.getElementById('sort-preset-select')?.value || '';
    if (!name) {
        window.App.showToast(manualSortText('manual.presetPickFirst', 'Choose a preset to load first', '请先选择要载入的预设'), 'info');
        return;
    }
    const preset = getManualSortPresets().find((p) => p.name === name);
    if (!preset) return;
    applyManualSortPreset(preset);
    window.App.showToast(formatManualSortI18n('manual.presetLoaded', 'Preset "{name}" loaded', { name }), 'success');
}

async function handleManualSortPresetDelete() {
    const name = document.getElementById('sort-preset-select')?.value || '';
    if (!name) {
        window.App.showToast(manualSortText('manual.presetPickFirst', 'Choose a preset to load first', '请先选择要载入的预设'), 'info');
        return;
    }
    const ok = await new Promise((resolve) => {
        window.App.showConfirm(
            manualSortText('manual.presetDeleteTitle', 'Delete Preset', '删除预设'),
            formatManualSortI18n('manual.presetDeleteConfirm', 'Delete the preset "{name}"? This cannot be undone.', { name }),
            () => resolve(true),
            () => resolve(false)
        );
    });
    if (!ok) return;
    saveManualSortPresets(getManualSortPresets().filter((p) => p.name !== name));
    populateManualSortPresetSelect();
    window.App.showToast(formatManualSortI18n('manual.presetDeleted', 'Preset "{name}" deleted', { name }), 'success');
}

function maybeAdoptManualSortFiltersFromGallery() {
    if (ManualSortState.hasSavedFilterState || ManualSortState.inheritedCurrentGalleryFilters) {
        return false;
    }

    syncManualSortFiltersFromGallery({ toastKey: null });
    return true;
}

function summarizeManualSortFolders(folders = {}) {
    const entries = Object.entries(folders || {}).filter(([, value]) => typeof value === 'string' && value.trim());
    if (entries.length === 0) {
        return manualSortText('manual.resumeFoldersEmpty', 'No destination folders saved yet', '还没有保存目标文件夹');
    }
    return entries
        .map(([key, value]) => `${String(key).toUpperCase()}: ${value}`)
        .join(' · ');
}

function renderManualSortResumeBanner(session, { visible = true } = {}) {
    const banner = document.querySelector('#sort-resume-banner');
    if (!banner) return;

    if (!visible) {
        ManualSortState.resumeBannerSessionSnapshot = null;
        banner.style.display = 'none';
        return;
    }

    if (!session) {
        ManualSortState.resumeBannerSessionSnapshot = null;
        banner.style.display = 'none';
        return;
    }

    banner.style.display = 'flex';
    const mode = MANUAL_SORT_MODES.has(session?.mode) ? session.mode : 'slot';
    const remaining = Number(session?.remaining || 0);
    ManualSortState.resumeBannerSessionSnapshot = {
        mode,
        remaining,
        // Resumed sessions keep whatever mode they were started with;
        // default for a brand-new session is 'copy' (Principle #11).
        operation_mode: session?.operation_mode || 'copy',
        folders: { ...(session?.folders || {}) },
    };

    const countEl = banner.querySelector('.resume-count');
    if (countEl) {
        if (mode === 'bracket') {
            countEl.textContent = formatManualSortText('manual.resumeBracketRemaining', '{count} comparisons left', '还剩 {count} 场对决', { count: remaining });
        } else if (mode === 'cull') {
            countEl.textContent = formatManualSortText('manual.resumeCullRemaining', '{count} images left to judge', '还有 {count} 张待筛选', { count: remaining });
        } else {
            countEl.textContent = formatManualSortI18n('manual.imagesRemaining', '{count} images remaining', { count: remaining });
        }
    }

    // Action mode + destination folders only apply to the slot (WASD) flow.
    // Bracket and cull are folder-free and non-destructive, so hide those lines
    // instead of showing meaningless "folders: none" / copy-mode text.
    const slotOnly = mode === 'slot';
    const operationEl = banner.querySelector('.resume-operation');
    if (operationEl) {
        operationEl.style.display = slotOnly ? '' : 'none';
        if (slotOnly) {
            const modeLabel = getManualSortOperationLabel(session?.operation_mode || 'copy');
            operationEl.textContent = formatManualSortI18n(
                'manual.resumeOperationMode',
                'Saved session action mode: {mode}',
                { mode: modeLabel }
            );
        }
    }

    const foldersEl = banner.querySelector('.resume-folders');
    if (foldersEl) {
        foldersEl.style.display = slotOnly ? '' : 'none';
        if (slotOnly) {
            foldersEl.textContent = formatManualSortI18n(
                'manual.resumeFolderSummary',
                'Saved session folders: {summary}',
                { summary: summarizeManualSortFolders(session?.folders || {}) }
            );
        }
    }
}

