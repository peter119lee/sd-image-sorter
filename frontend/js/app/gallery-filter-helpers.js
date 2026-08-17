/**
 * app/gallery-filter-helpers.js — app.js decomposition, stage 2 (leaf sections).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js,
 * pre-split lines 828-1133. Classic script: shares ONE global lexical
 * environment with app.js and the other app/ parts; index.html loads
 * every app/ file BEFORE app.js, so these top-level declarations stay
 * visible there. No behavior change intended.
 */
// v3.2.2: detect whether the user has applied any non-default filter so
// the empty-state can show a contextual message ("no matches, try clearing
// your filter") instead of the onboarding card ("no images yet, import a
// folder") which is misleading when the user has a populated library.
function _galleryHasActiveFilter() {
    const f = AppState.filters || {};
    if (f.tags && f.tags.length > 0) return true;
    if (f.checkpoints && f.checkpoints.length > 0) return true;
    if (f.loras && f.loras.length > 0) return true;
    if (f.prompts && f.prompts.length > 0) return true;
    if (f.search && String(f.search).trim().length > 0) return true;
    if (f.artist) return true;
    if (f.folder) return true;
    if (f.hasMetadata != null) return true;
    if (f.minWidth != null || f.maxWidth != null) return true;
    if (f.minHeight != null || f.maxHeight != null) return true;
    if (f.aspectRatio) return true;
    if (f.minAesthetic != null || f.maxAesthetic != null) return true;
    if (f.dateFrom || f.dateTo) return true;
    if (f.minUserRating != null) return true;
    if (f.brightnessMin != null || f.brightnessMax != null) return true;
    if (f.colorTemperature) return true;
    if (f.colorHues?.length || f.excludeColorHues?.length) return true;
    if (f.brightnessDistribution) return true;
    // generators/ratings start as "all selected" - flag only when a strict
    // subset is selected
    if (Array.isArray(f.generators) && Array.isArray(ALL_GENERATORS) &&
        f.generators.length > 0 && f.generators.length < ALL_GENERATORS.length) return true;
    if (Array.isArray(f.ratings) && f.ratings.length > 0 && f.ratings.length < 4) return true;
    return false;
}

function _applyGalleryEmptyStateVariant(emptyState) {
    if (!emptyState) return;
    // v3.3.1 FEAT-COLLECTIONS: when the gallery is scoped to a collection
    // (or Favorites), an empty result means "this collection has no images
    // yet" — NOT "import a folder". Show a friendly, context-aware message
    // instead of the onboarding card.
    const browsingCollection = AppState.filters && AppState.filters.collectionId != null;
    const filterActive = _galleryHasActiveFilter() || browsingCollection;
    emptyState.classList.toggle('empty-state-no-matches', filterActive);
    emptyState.classList.toggle('empty-state-no-library', !filterActive);

    // Look up the title/hint strings; fall back to English if i18n
    // hasn't loaded yet.
    const t = (key, fallback) => (typeof appT === 'function' ? appT(key, fallback) : fallback);

    const titleEl = emptyState.querySelector('h3');
    const hintEl = emptyState.querySelector('p');
    const importBtn = emptyState.querySelector('#empty-state-scan-btn');
    const onboardingSteps = emptyState.querySelector('.onboarding-steps');
    const onboardingTip = emptyState.querySelector('.onboarding-tip');
    const clearFiltersBtn = emptyState.querySelector('#empty-state-clear-filters-btn');

    if (browsingCollection) {
        // Collection / Favorites empty scope.
        const isFavorites = Boolean(window.CollectionsUI?.isFavoritesActive?.());
        const titleKey = isFavorites ? 'collections.favoritesEmpty' : 'collections.emptyImages';
        const titleFallback = isFavorites
            ? 'No favorites yet'
            : 'No images in this collection yet';
        const hintFallback = isFavorites
            ? 'Click the ♥ on any image to add it to Favorites.'
            : 'Right-click an image and choose "Add to collection…" to fill this collection.';
        if (titleEl) {
            titleEl.setAttribute('data-i18n', titleKey);
            titleEl.textContent = t(titleKey, titleFallback);
        }
        if (hintEl) {
            const hintKey = isFavorites ? 'collections.favoritesEmptyHint' : 'collections.emptyImagesHint';
            hintEl.setAttribute('data-i18n', hintKey);
            hintEl.textContent = t(hintKey, hintFallback);
        }
        if (importBtn) importBtn.style.display = 'none';
        if (onboardingSteps) onboardingSteps.style.display = 'none';
        if (onboardingTip) onboardingTip.style.display = 'none';
        if (clearFiltersBtn) clearFiltersBtn.style.display = 'none';
        if (window.I18n && typeof window.I18n.applyToDOM === 'function') {
            try { window.I18n.applyToDOM(emptyState); } catch (_e) {}
        }
        return;
    }

    if (filterActive) {
        if (titleEl) {
            titleEl.setAttribute('data-i18n', 'gallery.noMatchesTitle');
            titleEl.textContent = t('gallery.noMatchesTitle', 'No images match your filters');
        }
        if (hintEl) {
            hintEl.setAttribute('data-i18n', 'gallery.noMatchesHint');
            hintEl.textContent = t('gallery.noMatchesHint',
                'Try removing some filter criteria, clearing your search, or adjusting the prompt/tag conditions.');
        }
        if (importBtn) importBtn.style.display = 'none';
        if (onboardingSteps) onboardingSteps.style.display = 'none';
        if (onboardingTip) onboardingTip.style.display = 'none';
        // Inject a "Clear filters" CTA if not already present
        let cta = clearFiltersBtn;
        if (!cta) {
            const actions = emptyState.querySelector('.empty-actions');
            if (actions) {
                cta = document.createElement('button');
                cta.id = 'empty-state-clear-filters-btn';
                cta.className = 'btn btn-primary';
                const labelSpan = document.createElement('span');
                labelSpan.setAttribute('data-i18n', 'gallery.clearFilters');
                labelSpan.textContent = t('gallery.clearFilters', 'Clear all filters');
                cta.append(document.createTextNode('🧹 '));
                cta.appendChild(labelSpan);
                cta.addEventListener('click', () => {
                    if (typeof window.resetFilters === 'function') {
                        window.resetFilters();
                    } else if (window.FilterStore?.resetFilters) {
                        window.FilterStore.resetFilters();
                    } else {
                        // Last-ditch fallback: full reload
                        location.reload();
                    }
                });
                actions.appendChild(cta);
            }
        } else {
            cta.style.display = '';
        }
    } else {
        if (titleEl) {
            titleEl.setAttribute('data-i18n', 'gallery.noImages');
            titleEl.textContent = t('gallery.noImages', 'No images yet');
        }
        if (hintEl) {
            hintEl.setAttribute('data-i18n', 'gallery.scanPrompt');
            hintEl.textContent = t('gallery.scanPrompt',
                'Import an image folder to start browsing, filtering, and organizing your images');
        }
        if (importBtn) importBtn.style.display = '';
        if (onboardingSteps) onboardingSteps.style.display = '';
        if (onboardingTip) onboardingTip.style.display = '';
        if (clearFiltersBtn) clearFiltersBtn.style.display = 'none';
    }
    // v3.2.2: re-apply i18n to the empty state subtree so the new
    // data-i18n attributes resolve correctly even if applyToDOM has
    // already cached their previous value.
    if (window.I18n && typeof window.I18n.applyToDOM === 'function') {
        try { window.I18n.applyToDOM(emptyState); } catch (_e) {}
    }
}

function clearFilteredSelectionIfFilterChanged(filters = AppState.filters) {
    if (AppState.selectionScope !== 'filtered') return false;
    if (!AppState.selectionToken && (!AppState?.selectedIds || AppState.selectedIds.size === 0)) return false;

    const currentFilterKey = getSelectionFilterCacheKey(filters);
    if (!AppState.selectionFilterKey || AppState.selectionFilterKey === currentFilterKey) {
        return false;
    }

    updateSelectionState((selection) => {
        selection.selectedIds = new Set();
        selection.scope = 'visible';
        selection.filterKey = null;
        selection.selectionToken = null;
        selection.selectionTotal = 0;
    });
    resetSelectionDataCache();

    if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
        Gallery.syncSelectionState();
    }
    if (typeof updateSelectionUI === 'function') updateSelectionUI();
    emitSelectionStateChanged();
    return true;
}

function markGalleryNeedsRefresh({ resetSelectionCache = true } = {}) {
    AppState.galleryNeedsRefresh = true;
    if (resetSelectionCache) {
        resetSelectionDataCache();
    }
}

function commitFilterModalState(filterState) {
    const nextFilters = cloneFilterState(filterState);
    const hasExternalHandler = Boolean(FilterModalController.onApply || FilterModalController.onReset);

    if (!hasExternalHandler) {
        setAppFilters(nextFilters);
        return cloneFilterState(AppState.filters);
    }

    if (FilterModalController.targetState) {
        copyFilterState(FilterModalController.targetState, nextFilters);
        return cloneFilterState(FilterModalController.targetState);
    }

    return nextFilters;
}

// Sort direction pairs: base sort value -> reversed sort value
const SORT_PAIRS = {
    newest: 'oldest',
    name_asc: 'name_desc',
    generator: 'generator_desc',
    prompt_length: 'prompt_length_asc',
    tag_count: 'tag_count_asc',
    rating: 'rating_desc',
    user_rating: 'user_rating_asc',
    character_count: 'character_count_asc',
    file_size: 'file_size_asc',
    aesthetic: 'aesthetic_asc',
    brightness: 'brightness_asc',
    saturation: 'saturation_asc',
    brightness_skew: 'brightness_skew_asc',
};
// Build full bidirectional reverse map
const SORT_REVERSE_MAP = {};
for (const [a, b] of Object.entries(SORT_PAIRS)) {
    SORT_REVERSE_MAP[a] = b;
    SORT_REVERSE_MAP[b] = a;
}
SORT_REVERSE_MAP.random = 'random';

/** Get the base (non-reversed) sort value for dropdown display */
function getBaseSortValue(sortBy) {
    for (const [base, rev] of Object.entries(SORT_PAIRS)) {
        if (sortBy === rev) return base;
    }
    return sortBy;
}

/** Check if the current sort is in reversed direction */
function isSortReversed(sortBy) {
    return Object.values(SORT_PAIRS).includes(sortBy);
}

/** Sync the sort dropdown and reverse button with current AppState.filters.sortBy */
function updateSortReverseButton() {
    const sortBy = AppState.filters.sortBy;
    const reversed = isSortReversed(sortBy);
    const btn = $('#sort-reverse-btn');
    const dropdown = $('#gallery-sort');
    if (btn) {
        btn.classList.toggle('active', reversed);
        btn.setAttribute('aria-pressed', String(reversed));
    }
    if (dropdown) {
        dropdown.value = getBaseSortValue(sortBy);
    }
}

function syncGallerySortLabels() {
    const dropdown = $('#gallery-sort');
    if (!dropdown) return;

    const mappings = {
        newest: ['sort.newest', 'Newest'],
        name_asc: ['sort.nameAsc', 'Name (A-Z)'],
        generator: ['sort.generator', 'Generator'],
        prompt_length: ['sort.promptLength', 'Prompt Length'],
        tag_count: ['sort.tagCount', 'Most Tags'],
        rating: ['sort.rating', 'Rating (NSFW first)'],
        character_count: ['sort.characterCount', 'Characters'],
        file_size: ['sort.fileSize', 'Largest File'],
        aesthetic: ['sort.aesthetic', 'Aesthetic Score'],
        brightness: ['sort.brightness', 'Brightest'],
        saturation: ['sort.saturation', 'Most Saturated'],
        brightness_skew: ['sort.brightnessSkew', 'Brightness Spread'],
        random: ['sort.random', 'Random'],
    };

    Object.entries(mappings).forEach(([value, [key, fallback]]) => {
        const option = dropdown.querySelector(`option[value="${value}"]`);
        if (option) option.textContent = appT(key, fallback);
    });
}

function supportsCursorPagination(sortBy = AppState.filters.sortBy) {
    return sortBy === 'newest' || sortBy === 'oldest';
}

// ============== API Functions ==============

/**
 * Format error messages for user-friendly display
 * @param {number} status - HTTP status code
 * @param {object} errorData - Error response data
 * @returns {string} User-friendly error message
 */
function formatApiError(status, errorData = {}) {
    // A refused AI-runtime lease is the one 409 that carries a structured
    // blocker (scope / label / elapsed seconds). Every verb in API funnels
    // through here, so explaining it once covers censor detect, similarity,
    // aesthetic scoring, artist identification and POST /api/tag/single
    // together - the same single point the backend maps the exception at.
    if (status === 409 && errorData && errorData.type === 'AiRuntimeBusyError') {
        // The refusal is fresher than any poll: let the status badge re-read.
        document.dispatchEvent(new CustomEvent('ai-runtime-busy', { detail: errorData }));
        const explained = window.AiBusy?.explain?.(errorData);
        if (explained) return explained;
    }

    // Use error detail if provided
    if (errorData.detail) return errorData.detail;
    if (errorData.error) return errorData.error;
    if (errorData.message) return errorData.message;

    // Default messages based on status code
    const statusMessages = {
        400: 'Invalid request. Please check your input and try again.',
        401: 'Authentication required. Please refresh the page.',
        403: 'Access denied. You do not have permission for this action.',
        404: 'The requested resource was not found.',
        409: 'This operation conflicts with an existing one. Please wait and try again.',
        422: 'Invalid data provided. Please check your input.',
        429: 'Too many requests. Please wait a moment and try again.',
        500: 'Server error. Please try again later or check the logs.',
        502: 'Server is temporarily unavailable. Please try again.',
        503: 'Service unavailable. The server may be starting up.',
    };

    return statusMessages[status] || `Request failed (${status}). Please try again.`;
}

