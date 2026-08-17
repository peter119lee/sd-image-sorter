/**
 * app/selection.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 1017-1367 (of 10,152): selection mode/scope system + gallery preview/prompt-filter.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
function syncSelectionModeButton() {
    const toggleBtn = $('#btn-toggle-select');
    if (!toggleBtn) return;

    const iconEl = toggleBtn.querySelector('span:first-child');
    const labelEl = toggleBtn.querySelector('span:last-child');
    const isSelecting = Boolean(AppState.selectionMode);
    const _d = window.I18n?.t?.('selection.doneSelecting');
    const doneLabel = (_d && _d !== 'selection.doneSelecting') ? _d : 'Done Selecting';
    const _s = window.I18n?.t?.('gallery.selectImages');
    const idleLabel = (_s && _s !== 'gallery.selectImages') ? _s : 'Select Images';

    toggleBtn.classList.toggle('active', isSelecting);
    toggleBtn.classList.toggle('selection-active', isSelecting);
    toggleBtn.setAttribute('aria-pressed', String(isSelecting));
    toggleBtn.setAttribute('data-state', isSelecting ? 'selecting' : 'idle');
    toggleBtn.setAttribute(
        'aria-label',
        isSelecting ? 'Exit image selection mode' : 'Enable image selection mode'
    );

    if (iconEl) {
        iconEl.innerHTML = isSelecting ? "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-sparkle\"/></svg>" : "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-check\"/></svg>";
    }

    if (labelEl) {
        labelEl.textContent = isSelecting ? doneLabel : idleLabel;
    }
}

function emitSelectionStateChanged() {
    const selectedCount = getSelectedGalleryCount();
    const detail = {
        selectionMode: Boolean(AppState.selectionMode),
        selectedCount,
        selectionScope: AppState.selectionScope || 'visible',
    };
    window.dispatchEvent(new CustomEvent('selection-state-changed', { detail }));
    document.dispatchEvent(new CustomEvent('selection-state-changed', { detail }));
}

function getSelectionScopeSummaryText(scope = AppState.selectionScope || 'visible') {
    if (scope === 'loaded') {
        return appT('selection.scopeLoaded', 'Selected from loaded gallery items');
    }
    if (scope === 'filtered') {
        if (isFilteredSelectionTokenRefreshPending()) {
            return appT('selection.scopeFilteredUpdating', 'Updating filtered selection...');
        }
        return AppState.selectionToken
            ? appT('selection.scopeFiltered', 'Selected all current filter matches')
            : appT('selection.scopeFilteredExplicit', 'Selected current filter matches');
    }
    return appT('selection.scopeVisible', 'Selected manually from Gallery');
}

function collapseSelectionMoreActions() {
    // Selection actions are now always visible in sections; kept as a no-op for older callers.
}

function normalizeSelectionImageIds(rawIds) {
    return Array.isArray(rawIds)
        ? rawIds
            .map((id) => Number(id))
            .filter((id) => Number.isFinite(id) && id > 0)
        : [];
}

let filteredSelectionRefreshSequence = 0;
let filteredSelectionRefreshState = null;

function isFilteredSelectionTokenRefreshPending() {
    const pending = filteredSelectionRefreshState;
    if (!pending) return false;
    return AppState.selectionScope === 'filtered'
        && Boolean(AppState.selectionToken)
        && AppState.selectionFilterKey === pending.filterKey
        && AppState.selectionToken === pending.sourceToken
        && getSelectionFilterCacheKey(AppState.filters) === pending.filterKey;
}

function invalidateFilteredSelectionTokenRefresh() {
    filteredSelectionRefreshSequence += 1;
    filteredSelectionRefreshState = null;
}

function syncFilteredSelectionPresentation() {
    resetSelectionDataCache();
    if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
        Gallery.syncSelectionState();
    } else {
        updateSelectionUI();
    }
    emitSelectionStateChanged();
}

function captureConfirmedFilteredSelection() {
    return {
        selectedIds: new Set(AppState.selectedIds || []),
        selectionToken: String(AppState.selectionToken || ''),
        selectionTotal: Math.max(0, Number(AppState.selectionTotal || 0) || 0),
    };
}

async function updateFilteredSelectionExclusions(rawExcludedIds) {
    if (!isFilteredSelectionActiveForCurrentFilters()) {
        throw new Error('Filtered selection is no longer active for the current Gallery filters');
    }

    const excludedIds = new Set(normalizeSelectionImageIds(Array.from(rawExcludedIds || [])));
    const filterPayload = buildSelectionFilterRequest();
    const filterKey = JSON.stringify(filterPayload);
    const previousPending = isFilteredSelectionTokenRefreshPending()
        ? filteredSelectionRefreshState
        : null;
    const confirmed = previousPending
        ? {
            selectedIds: new Set(previousPending.confirmed.selectedIds),
            selectionToken: previousPending.confirmed.selectionToken,
            selectionTotal: previousPending.confirmed.selectionTotal,
        }
        : captureConfirmedFilteredSelection();
    const baseTotal = confirmed.selectionTotal + confirmed.selectedIds.size;
    const requestId = filteredSelectionRefreshSequence + 1;
    filteredSelectionRefreshSequence = requestId;
    filteredSelectionRefreshState = {
        requestId,
        filterKey,
        sourceToken: confirmed.selectionToken,
        confirmed,
    };

    updateSelectionState((selection) => {
        selection.selectedIds = new Set(excludedIds);
        selection.selectionTotal = Math.max(0, baseTotal - excludedIds.size);
    });
    syncFilteredSelectionPresentation();

    try {
        const result = await API.createSelectionToken(
            filterPayload,
            FILTERED_SELECTION_CHUNK_SIZE,
            { excludedImageIds: Array.from(excludedIds) }
        );
        const refreshedToken = typeof result?.selection_token === 'string'
            ? result.selection_token.trim()
            : '';
        const refreshedTotal = Number(result?.total_estimate);
        if (!refreshedToken) {
            throw new TypeError('Selection token refresh response was missing selection_token');
        }
        if (!Number.isFinite(refreshedTotal) || refreshedTotal < 0) {
            throw new TypeError(
                `Selection token refresh returned invalid total_estimate: ${String(result?.total_estimate)}`
            );
        }

        const pending = filteredSelectionRefreshState;
        if (!pending || pending.requestId !== requestId) return false;
        const stateStillMatches = AppState.selectionScope === 'filtered'
            && AppState.selectionFilterKey === filterKey
            && AppState.selectionToken === pending.sourceToken
            && getSelectionFilterCacheKey(AppState.filters) === filterKey;
        if (!stateStillMatches) {
            filteredSelectionRefreshState = null;
            return false;
        }

        updateSelectionState((selection) => {
            selection.selectedIds = new Set(excludedIds);
            selection.selectionToken = refreshedToken;
            selection.selectionTotal = refreshedTotal;
        });
        filteredSelectionRefreshState = null;
        syncFilteredSelectionPresentation();
        return true;
    } catch (error) {
        const pending = filteredSelectionRefreshState;
        if (!pending || pending.requestId !== requestId) return false;
        const stateStillMatches = AppState.selectionScope === 'filtered'
            && AppState.selectionFilterKey === filterKey
            && AppState.selectionToken === pending.sourceToken
            && getSelectionFilterCacheKey(AppState.filters) === filterKey;
        filteredSelectionRefreshState = null;
        if (!stateStillMatches) return false;

        updateSelectionState((selection) => {
            selection.selectedIds = new Set(confirmed.selectedIds);
            selection.selectionToken = confirmed.selectionToken;
            selection.selectionTotal = confirmed.selectionTotal;
        });
        syncFilteredSelectionPresentation();
        showToast(
            formatUserError(
                error,
                appT(
                    'selection.exclusionRefreshFailed',
                    'Could not update the filtered selection. The previous selection was restored.'
                )
            ),
            'error'
        );
        return false;
    }
}

function confirmLargeFilteredSelection(total) {
    const normalizedTotal = Number(total || 0);
    if (normalizedTotal <= FILTERED_SELECTION_CONFIRM_THRESHOLD) {
        return true;
    }

    const confirmMessage = appT(
        'selection.largeFilteredConfirm',
        'This will select {count} filtered images using a compact selection token. Continue?',
        { count: normalizedTotal }
    );
    return window.confirm(confirmMessage);
}

function shouldFallbackToSelectionIds(error) {
    return [404, 405, 501].includes(Number(error?.apiStatus));
}

async function resolveFilteredSelectionIdsViaChunks(filterPayload, options = {}) {
    const tokenPayload = await API.createSelectionToken(filterPayload, FILTERED_SELECTION_CHUNK_SIZE, options);
    const selectionToken = tokenPayload?.selection_token;
    if (!selectionToken) {
        throw new Error('Selection token response was missing a token');
    }

    const totalEstimate = Number(tokenPayload?.total_estimate || 0);
    if (!confirmLargeFilteredSelection(totalEstimate)) {
        return { cancelled: true, imageIds: [] };
    }

    return {
        cancelled: false,
        imageIds: [],
        selectionToken,
        total: totalEstimate,
        exactTotal: tokenPayload?.exact_total !== false,
    };
}

async function resolveFilteredSelectionIdsViaLegacyEndpoint(filterPayload) {
    const result = await API.getSelectionIds(filterPayload);
    const imageIds = normalizeSelectionImageIds(result?.image_ids);
    const total = Number(result?.total || imageIds.length || 0);
    if (!confirmLargeFilteredSelection(total)) {
        return { cancelled: true, imageIds: [] };
    }
    return { cancelled: false, imageIds, selectionToken: null, total };
}

async function resolveFilteredSelectionIds(filterPayload, options = {}) {
    try {
        return await resolveFilteredSelectionIdsViaChunks(filterPayload, options);
    } catch (error) {
        if (!shouldFallbackToSelectionIds(error)) {
            throw error;
        }
        return resolveFilteredSelectionIdsViaLegacyEndpoint(filterPayload);
    }
}

async function selectAllFilteredResults() {
    const selectFilteredBtn = $('#btn-select-all');
    if (selectFilteredBtn) {
        selectFilteredBtn.disabled = true;
    }

    try {
        const filterPayload = buildSelectionFilterRequest();
        const filterKey = JSON.stringify(filterPayload);
        const result = await resolveFilteredSelectionIds(filterPayload);
        if (result.cancelled) {
            updateSelectionUI();
            return;
        }

        if (getSelectionFilterCacheKey(AppState.filters) !== filterKey) {
            updateSelectionUI();
            return;
        }

        invalidateFilteredSelectionTokenRefresh();

        updateSelectionState((selection) => {
            selection.selectedIds = result.selectionToken ? new Set() : new Set(result.imageIds);
            selection.scope = 'filtered';
            selection.filterKey = filterKey;
            selection.selectionToken = result.selectionToken || null;
            selection.selectionTotal = Number(result.total || result.imageIds?.length || 0);
        });

        if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
            Gallery.syncSelectionState();
        }
        updateSelectionUI();
        emitSelectionStateChanged();
    } catch (error) {
        showToast(
            formatUserError(error, appT('selection.selectFilteredFailed', 'Failed to select all current filter matches')),
            'error'
        );
        updateSelectionUI();
    }
}

async function invertAllFilteredResults() {
    if (isFilteredSelectionTokenRefreshPending()) {
        showToast(
            appT('selection.scopeFilteredUpdating', 'Updating filtered selection...'),
            'info'
        );
        return;
    }
    const invertFilteredBtn = $('#btn-invert-selection-filtered');
    if (invertFilteredBtn) {
        invertFilteredBtn.disabled = true;
    }

    try {
        const filterPayload = buildSelectionFilterRequest();
        const filterKey = JSON.stringify(filterPayload);
        const excludedImageIds = AppState.selectionScope === 'filtered' && AppState.selectionToken
            ? Array.from(AppState.selectedIds || [])
            : getSelectedGalleryIds();
        const result = await resolveFilteredSelectionIds(filterPayload, { excludedImageIds });
        if (result.cancelled) {
            updateSelectionUI();
            return;
        }

        if (getSelectionFilterCacheKey(AppState.filters) !== filterKey) {
            updateSelectionUI();
            return;
        }

        const currentSelected = new Set(AppState.selectedIds || []);
        const hasActiveFilteredToken = AppState.selectionScope === 'filtered'
            && Boolean(AppState.selectionToken)
            && isFilteredSelectionActiveForCurrentFilters();

        invalidateFilteredSelectionTokenRefresh();

        updateSelectionState((selection) => {
            if (hasActiveFilteredToken) {
                if (currentSelected.size === 0) {
                    selection.selectedIds = new Set();
                    selection.scope = 'visible';
                    selection.filterKey = null;
                    selection.selectionToken = null;
                    selection.selectionTotal = 0;
                    return;
                }

                selection.selectedIds = new Set(currentSelected);
                selection.scope = 'filtered';
                selection.filterKey = filterKey;
                selection.selectionToken = null;
                selection.selectionTotal = currentSelected.size;
                return;
            }

            if (result.selectionToken) {
                selection.selectedIds = currentSelected;
                selection.scope = 'filtered';
                selection.filterKey = filterKey;
                selection.selectionToken = result.selectionToken;
                selection.selectionTotal = Number(result.total || 0);
                return;
            }

            const nextSelected = new Set(
                result.imageIds.filter((imageId) => !currentSelected.has(imageId))
            );
            selection.selectedIds = nextSelected;
            selection.scope = 'filtered';
            selection.filterKey = filterKey;
            selection.selectionToken = null;
            selection.selectionTotal = nextSelected.size;
        });

        if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
            Gallery.syncSelectionState();
        }
        resetSelectionDataCache();
        updateSelectionUI();
        emitSelectionStateChanged();
    } catch (error) {
        showToast(
            formatUserError(error, appT('selection.invertFilteredFailed', 'Failed to invert current filter matches')),
            'error'
        );
        updateSelectionUI();
    }
}

function setSelectionMode(enabled, options = {}) {
    const { clearSelectionWhenDisabled = true } = options;
    const nextMode = Boolean(enabled);
    updateSelectionState((selection) => {
        selection.selectionMode = nextMode;
        if (!nextMode && clearSelectionWhenDisabled) {
            selection.selectedIds = new Set();
            selection.scope = 'visible';
            selection.filterKey = null;
            selection.selectionToken = null;
            selection.selectionTotal = 0;
        }
    });

    if (!nextMode) {
        invalidateFilteredSelectionTokenRefresh();
        collapseSelectionMoreActions();
    }

    if (!nextMode && clearSelectionWhenDisabled && window.Gallery) {
        window.Gallery.lastSelectedIndex = null;
    }

    updateSelectionUI();
    emitSelectionStateChanged();

    if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
        Gallery.syncSelectionState();
    }
}

function openGalleryPreview(imageId) {
    switchView('gallery');
    requestAnimationFrame(() => {
        if (window.Gallery && typeof window.Gallery.openPreview === 'function') {
            window.Gallery.openPreview(imageId);
        }
    });
}

function applyPromptFilter(prompt) {
    const value = String(prompt ?? '').trim();
    if (!value) return false;

    updateAppFilters((filters) => {
        filters.prompts = [value];
    });

    if (typeof renderModalActivePrompts === 'function') {
        renderModalActivePrompts();
    }
    updateFilterSummary();
    if (typeof window.updateAutoSepSummary === 'function') window.updateAutoSepSummary();
    if (typeof window.invalidateAutoSepPreview === 'function') window.invalidateAutoSepPreview();
    if (typeof window.updateManualSortFilterSummary === 'function') window.updateManualSortFilterSummary();
    syncGenTabsWithFilters();
    switchView('gallery');
    loadImages();
    return true;
}

function resetViewScrollPosition() {
    const mainContent = document.getElementById('main-content');
    document.documentElement.style.overflowAnchor = 'none';
    document.body.style.overflowAnchor = 'none';
    if (mainContent) {
        mainContent.style.overflowAnchor = 'none';
        mainContent.scrollTop = 0;
    }
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    try {
        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    } catch (_error) {
        window.scrollTo(0, 0);
    }
}

let viewScrollResetToken = 0;

function hasViewScrollMoved() {
    const mainContent = document.getElementById('main-content');
    return Math.max(
        window.pageYOffset || 0,
        document.documentElement.scrollTop || 0,
        document.body.scrollTop || 0,
        mainContent?.scrollTop || 0,
    ) > 4;
}

function scheduleViewScrollReset() {
    // Gallery Comfort may be deliberately restoring a saved browse position on
    // this very view switch. This reset fires at 0/rAF/50/160/320/700ms, so it
    // used to stomp the restore six times over and "continue where you left
    // off" could never work. Honour an in-progress restore and skip.
    const restoring = () => Boolean(
        window.GalleryComfort && typeof window.GalleryComfort.isRestoring === 'function'
        && window.GalleryComfort.isRestoring(),
    );
    const token = ++viewScrollResetToken;
    let resetApplied = false;
    if (restoring()) return;
    const resetUnlessRestoring = () => {
        if (token !== viewScrollResetToken || restoring()) return;
        if (resetApplied && hasViewScrollMoved()) {
            viewScrollResetToken += 1;
            return;
        }
        resetViewScrollPosition();
        resetApplied = true;
    };
    resetUnlessRestoring();
    requestAnimationFrame(() => {
        resetUnlessRestoring();
        requestAnimationFrame(resetUnlessRestoring);
    });
    setTimeout(resetUnlessRestoring, 50);
    setTimeout(resetUnlessRestoring, 160);
    setTimeout(resetUnlessRestoring, 320);
    setTimeout(resetUnlessRestoring, 700);
}

