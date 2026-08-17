/**
 * manual-sort/collection-slots.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 268-457 (the v3.3.1 Collection Slots block): per-slot
 * collection load/save/queries, the slot / bracket-winner / cull-destination
 * select builders, loadManualSortCollections, refreshManualSortSlotUi and
 * initManualSortSlotControls. Classic script: loads after
 * manual-sort/state-constants.js (base).
 */
// ============== Collection Slots (v3.3.1) ==============

function loadManualSortSlotCollections() {
    const slots = { w: null, a: null, s: null, d: null };
    try {
        const raw = localStorage.getItem(MANUAL_SORT_SLOT_COLLECTIONS_KEY);
        const parsed = raw ? JSON.parse(raw) : null;
        if (parsed && typeof parsed === 'object') {
            MANUAL_SORT_SLOT_KEYS.forEach((key) => {
                const value = Number(parsed[key]);
                slots[key] = Number.isInteger(value) && value > 0 ? value : null;
            });
        }
    } catch (_) {
        // Ignore corrupt saved state; fall back to all-folder slots.
    }
    ManualSortState.collectionSlots = slots;
}

function saveManualSortSlotCollections() {
    localStorage.setItem(
        MANUAL_SORT_SLOT_COLLECTIONS_KEY,
        JSON.stringify(ManualSortState.collectionSlots || {})
    );
}

function isManualSortCollectionSlot(key) {
    const id = ManualSortState.collectionSlots?.[key];
    return Number.isInteger(id) && id > 0;
}

function getManualSortCollectionName(collectionId) {
    const match = (ManualSortState.collectionsCache || []).find((c) => c.id === collectionId);
    return match ? match.name : '';
}

// Build the non-folder ({ key: collectionId }) map to send to the backend.
function getManualSortActiveCollectionSlots() {
    const out = {};
    MANUAL_SORT_SLOT_KEYS.forEach((key) => {
        out[key] = isManualSortCollectionSlot(key) ? ManualSortState.collectionSlots[key] : null;
    });
    return out;
}

function populateManualSortCollectionSelects() {
    const selects = document.querySelectorAll('.slot-collection-select');
    if (!selects.length) return;
    const placeholder = manualSortText('manual.collectChoose', 'Choose a collection…', '选择一个收藏夹…');
    const options = (ManualSortState.collectionsCache || [])
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
        .join('');
    selects.forEach((select) => {
        const key = select.dataset.key;
        select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>${options}`;
        const assigned = ManualSortState.collectionSlots?.[key];
        select.value = isManualSortCollectionSlot(key) ? String(assigned) : '';
    });
}

// v3.3.2 WB-S6: A/B Showdown winner destination selector. '' = don't save,
// 'fav' = Favorites, otherwise a collection id. Persisted across reloads.
function getBracketWinnerDest() {
    try {
        const stored = localStorage.getItem(MANUAL_SORT_BRACKET_WINNER_KEY);
        if (stored != null) return stored;
    } catch (_) { /* ignore */ }
    return '';
}

function populateBracketWinnerSelect() {
    const select = document.getElementById('bracket-winner-collection');
    if (!select) return;
    const none = manualSortText('manual.bracketWinnerNone', "Don't save", '不收藏');
    const fav = manualSortText('manual.bracketWinnerFav', 'Favorites', '收藏');
    const options = (ManualSortState.collectionsCache || [])
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
        .join('');
    select.innerHTML = `<option value="">${escapeHtml(none)}</option>`
        + `<option value="fav">${escapeHtml(fav)}</option>`
        + options;
    // Restore the saved choice if it still exists.
    const saved = getBracketWinnerDest();
    const valid = saved === '' || saved === 'fav'
        || (ManualSortState.collectionsCache || []).some((c) => String(c.id) === String(saved));
    select.value = valid ? saved : '';
}

// v3.3.2 FF-1: 留/汰 cull keep/reject destination selectors. Same value space
// as the bracket winner ('' = don't save, 'fav' = Favorites, else collection
// id). Keep defaults to Favorites; reject defaults to don't-save.
function getCullDest(which) {
    const key = which === 'reject' ? MANUAL_SORT_CULL_REJECT_KEY : MANUAL_SORT_CULL_KEEP_KEY;
    try {
        const stored = localStorage.getItem(key);
        if (stored != null) return stored;
    } catch (_) { /* ignore */ }
    return which === 'keep' ? 'fav' : '';
}

function populateCullDestSelect(which) {
    const select = document.getElementById(which === 'reject' ? 'cull-reject-collection' : 'cull-keep-collection');
    if (!select) return;
    const none = manualSortText('manual.bracketWinnerNone', "Don't save", '不收藏');
    const fav = manualSortText('manual.bracketWinnerFav', 'Favorites', '收藏');
    const options = (ManualSortState.collectionsCache || [])
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
        .join('');
    select.innerHTML = `<option value="">${escapeHtml(none)}</option>`
        + `<option value="fav">${escapeHtml(fav)}</option>`
        + options;
    const saved = getCullDest(which);
    const valid = saved === '' || saved === 'fav'
        || (ManualSortState.collectionsCache || []).some((c) => String(c.id) === String(saved));
    select.value = valid ? saved : (which === 'keep' ? 'fav' : '');
}

function populateCullDestSelects() {
    populateCullDestSelect('keep');
    populateCullDestSelect('reject');
}

async function loadManualSortCollections() {
    try {
        const result = await window.App?.API?.listCollections?.();
        const list = Array.isArray(result?.collections) ? result.collections : [];
        ManualSortState.collectionsCache = list.map((c) => ({ id: Number(c.id), name: String(c.name || '') }));
    } catch (e) {
        ManualSortState.collectionsCache = [];
        if (window.Logger) Logger.warn('Failed to load collections for manual sort:', e);
    }
    // Drop any saved slot id that no longer exists so the UI never shows a
    // stale assignment.
    const validIds = new Set(ManualSortState.collectionsCache.map((c) => c.id));
    MANUAL_SORT_SLOT_KEYS.forEach((key) => {
        if (isManualSortCollectionSlot(key) && !validIds.has(ManualSortState.collectionSlots[key])) {
            ManualSortState.collectionSlots[key] = null;
        }
    });
    populateManualSortCollectionSelects();
    populateBracketWinnerSelect();
    populateCullDestSelects();
    MANUAL_SORT_SLOT_KEYS.forEach(refreshManualSortSlotUi);
}

// Toggle the folder-input vs collection-select for a slot based on its type.
function refreshManualSortSlotUi(key) {
    const slot = document.querySelector(`.slot-target[data-key="${key}"]`);
    if (!slot) return;
    const isCollection = isManualSortCollectionSlot(key);
    const explicitCollectionType = slot.querySelector('input[name="slot-type-' + key + '"][value="collection"]')?.checked;
    const showCollection = isCollection || Boolean(explicitCollectionType);

    const folderTarget = slot.querySelector('.slot-folder-target');
    const collectionSelect = slot.querySelector('.slot-collection-select');
    if (folderTarget) folderTarget.hidden = showCollection;
    if (collectionSelect) collectionSelect.hidden = !showCollection;

    const radios = slot.querySelectorAll(`input[name="slot-type-${key}"]`);
    radios.forEach((radio) => {
        radio.checked = showCollection ? radio.value === 'collection' : radio.value === 'folder';
    });
}

function initManualSortSlotControls() {
    document.querySelectorAll('input[name^="slot-type-"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            const key = radio.dataset.key;
            if (!radio.checked || !key) return;
            if (radio.value === 'folder') {
                ManualSortState.collectionSlots[key] = null;
                saveManualSortSlotCollections();
            }
            refreshManualSortSlotUi(key);
            updateFolderNames();
        });
    });

    document.querySelectorAll('.slot-collection-select').forEach((select) => {
        select.addEventListener('change', () => {
            const key = select.dataset.key;
            const value = Number(select.value);
            ManualSortState.collectionSlots[key] = Number.isInteger(value) && value > 0 ? value : null;
            saveManualSortSlotCollections();
            refreshManualSortSlotUi(key);
            updateFolderNames();
        });
    });
}

