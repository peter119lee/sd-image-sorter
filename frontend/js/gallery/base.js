/**
 * gallery/base.js — gallery.js decomposition (family base; MUST LOAD FIRST).
 * Moved BYTE-IDENTICAL from frontend/js/gallery.js pre-cut lines 1-198 +
 * 4698-4708 (of 4,708): the 4 module-private helpers, the two shared consts
 * (global lexical environment — TDZ is why base loads first), the Gallery
 * data-prop object literal, the window.Gallery / window.GALLERY_VIRTUAL_CONFIG
 * publish, and the _bindLanguageUpdates DOMContentLoaded boot.
 */
/**
 * SD Image Sorter - Gallery Module
 * Handles image grid display, preview modal, multi-selection and drag-and-drop
 * Supports virtual scrolling for large image collections (500+ images)
 */

function getGalleryAppContext() {
    const app = window.App || {};
    const appState = app.AppState || {
        images: [],
        filters: {},
        selectedIds: new Set(),
        selectionMode: false,
        selectionScope: 'visible',
        selectionFilterKey: null,
        selectionToken: null,
        selectionTotal: 0,
        viewMode: 'grid'
    };
    const cloneSelectionState = app.cloneSelectionState || ((selectionState) => ({
        selectionMode: Boolean(selectionState?.selectionMode),
        selectedIds: new Set(Array.from(selectionState?.selectedIds || [])),
        scope: selectionState?.scope || selectionState?.selectionScope || 'visible',
        filterKey: selectionState?.filterKey || selectionState?.selectionFilterKey || null,
        selectionToken: selectionState?.selectionToken || null,
        selectionTotal: Number(selectionState?.selectionTotal || 0) || 0,
    }));
    const setSelectionState = app.setSelectionState || ((nextSelection) => {
        const nextState = cloneSelectionState(nextSelection);
        appState.selectionMode = nextState.selectionMode;
        appState.selectedIds = nextState.selectedIds;
        appState.selectionScope = nextState.scope;
        appState.selectionFilterKey = nextState.filterKey || null;
        appState.selectionToken = nextState.selectionToken || null;
        appState.selectionTotal = Number(nextState.selectionTotal || 0) || 0;
        return nextState;
    });
    const updateSelectionState = app.updateSelectionState || ((updater) => {
        const draft = cloneSelectionState({
            selectionMode: appState.selectionMode,
            selectedIds: appState.selectedIds,
            scope: appState.selectionScope,
            filterKey: appState.selectionFilterKey,
            selectionToken: appState.selectionToken,
            selectionTotal: appState.selectionTotal,
        });
        const nextState = typeof updater === 'function'
            ? (updater(draft) ?? draft)
            : updater;
        return setSelectionState(nextState);
    });
    return {
        $: app.$ || ((selector) => document.querySelector(selector)),
        API: app.API || window.API,
        AppState: appState,
        updateSelectionState,
        updateSelectionUI: app.updateSelectionUI || window.updateSelectionUI,
        showModal: app.showModal || window.showModal,
        formatSize: app.formatSize || window.formatSize,
        showToast: app.showToast || window.showToast,
        getSelectedGalleryCount: app.getSelectedGalleryCount,
        isFilteredSelectionActiveForCurrentFilters: app.isFilteredSelectionActiveForCurrentFilters
    };
}

function getRequiredGalleryAPI() {
    const { API } = getGalleryAppContext();
    if (!API) {
        throw new Error('App API is not ready yet');
    }
    return API;
}

function selectionBaseForScope(selection, nextScope, { additive = true } = {}) {
    if (!additive) return new Set();

    const currentScope = selection?.scope || selection?.selectionScope || 'visible';
    if (currentScope === nextScope) {
        return new Set(selection?.selectedIds || []);
    }

    if (nextScope === 'visible' || currentScope === 'filtered') {
        return new Set();
    }

    return new Set(selection?.selectedIds || []);
}

function isGalleryImageSelected(AppState, imageId) {
    const app = window.App || {};
    const numericId = Number(imageId);
    const idIsExcluded = AppState.selectedIds.has(numericId) || AppState.selectedIds.has(String(imageId));
    if (AppState.selectionScope === 'filtered' && AppState.selectionToken) {
        const tokenStillValid = typeof app.isFilteredSelectionActiveForCurrentFilters === 'function'
            ? app.isFilteredSelectionActiveForCurrentFilters()
            : true;
        return tokenStillValid && !idIsExcluded;
    }
    return idIsExcluded;
}

/**
 * Gallery Virtual Scrolling Configuration
 */
const GALLERY_VIRTUAL_CONFIG = {
    bufferSize: 3,            // Rows rendered above and below the viewport
    threshold: 96,            // Minimum items to enable virtual scrolling
    estimatedItemHeight: 200, // Estimated height for grid mode
    rowGap: 16,               // Gap between rows
    columnGap: 16,            // Gap between columns
    aspectRatio: {
        grid: 1,
        large: 0.84
    },
    progressiveRender: {
        initialCount: {
            grid: 24,
            large: 10
        },
        batchCount: {
            grid: 36,
            large: 12
        }
    },
    largeThumb: {
        initialSize: 384,
        finalSize: 512,
        visibleMargin: 320
    },
    minColumnWidth: {
        grid: 200,
        large: 340,
        waterfall: 280
    },
    waterfall: {
        columnWidth: 280,
        minHeight: 180,
        maxHeight: 600,
        estimatedHeight: 350
    }
};

const DEFAULT_GENERATOR_COLORS = {
    comfyui: '#456852',
    nai: '#685445',
    webui: '#455368',
    forge: '#504568',
    reforge: '#574568',
    fooocus: '#684556',
    'easy-diffusion': '#456864',
    invokeai: '#455d68',
    swarmui: '#686145',
    drawthings: '#684558',
    gemini: '#685e45',
    'gpt-image': '#45685d',
    others: '#455468',
    unknown: '#575759'
};

const Gallery = {
    images: [],
    loading: false,
    lastSelectedIndex: null,
    _languageBound: false,
    _analysisBound: false,
    _modalAnalysisRunning: new Set(),
    lazyObserver: null,
    currentPreviewIndex: -1,
    currentPreviewRequestId: 0,
    showAllTags: false,
    // v3.3.0 FEAT-COLLECTIONS: source-image ids currently in Favorites.
    // Hydrated from /api/collections/favorites/ids on load; kept in sync as
    // the user toggles hearts so re-renders show the correct state.
    favoriteIds: new Set(),
    // Default expand primary metadata so users don't see "empty" collapsed
    // headers (QA P3-12). Secondary / verbose blocks stay collapsed.
    modalSectionState: {
        prompt: true,
        negative: true,
        params: true,
        modelAssets: true,
        loras: true,
        nodes: false,
        color: true,
    },
    _histogramMode: 'rgb',

    // Virtual scrolling state
    virtualList: null,
    useVirtualScroll: false,
    // Owner FB-3: one thumbnail-size px shared by the toolbar slider, the
    // [ / ] shortcuts and every layout path. Lazily hydrated from the same
    // localStorage key the slider block in app.js persists.
    _thumbnailSizePx: null,
    pendingRenderFrame: null,
    renderSessionId: 0,
    largeUpgradeQueue: new Set(),
    largeUpgradeTaskId: null,
    anchorRestoreToken: 0,

};

// Export configuration for external use
window.GALLERY_VIRTUAL_CONFIG = GALLERY_VIRTUAL_CONFIG;
window.Gallery = Gallery;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => Gallery._bindLanguageUpdates());
} else {
    Gallery._bindLanguageUpdates();
}
