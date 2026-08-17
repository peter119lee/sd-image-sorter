/**
 * app/filter-modal-data.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 8717-9478 (of 10,152): filter modal lists/preview/search/apply + filter presets.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
async function loadModalFilterLists() {
    const cpList = $('#modal-checkpoint-list');
    const loraList = $('#modal-lora-list');
    const filterState = getFilterModalState();
    const optionData = FilterModalController.optionData;
    const t = (key, params, fallback) => {
        const translated = window.I18n?.t?.(key, params);
        return translated && translated !== key ? translated : (fallback || key);
    };

    // Show skeleton while loading
    if (window.Skeleton) {
        const skeletonHTML = `
            <div class="skeleton skeleton-filter-option"></div>
            <div class="skeleton skeleton-filter-option"></div>
            <div class="skeleton skeleton-filter-option"></div>
            <div class="skeleton skeleton-filter-option"></div>
            <div class="skeleton skeleton-filter-option"></div>
        `;
        if (cpList) cpList.innerHTML = skeletonHTML;
        if (loraList) loraList.innerHTML = skeletonHTML;
    }

    try {
        const data = optionData || AppState.analytics || await API.getStats();

        renderCheckpointFilterList(data.checkpoints || [], t);
        renderLoraFilterList(data.loras || [], t);

        updateFilterModalSummary();
    } catch (e) {
        Logger.error('Failed to load filter lists:', e);
        // Show error state in lists
        if (cpList) cpList.innerHTML = `<div class="filter-empty-state">${escapeHtml(t('filter.failedLoadCheckpoints', null, 'Failed to load checkpoints.'))}</div>`;
        if (loraList) loraList.innerHTML = `<div class="filter-empty-state">${escapeHtml(t('filter.failedLoadLoras', null, 'Failed to load LoRAs.'))}</div>`;
        updateFilterModalSummary();
    }
}

function renderCheckpointFilterList(checkpoints, t = appT) {
    const cpList = $('#modal-checkpoint-list');
    if (!cpList) return;
    const filterState = getFilterModalState();
    const selectedCheckpointValues = new Set(
        (filterState.checkpoints || []).map(normalizeCheckpointFilterValue).filter(Boolean)
    );
    const normalizedItems = [...(checkpoints || [])];
    const presentValues = new Set(normalizedItems.map(getCheckpointOptionValue).filter(Boolean));
    selectedCheckpointValues.forEach((checkpointValue) => {
        if (!presentValues.has(checkpointValue)) {
            normalizedItems.push({
                checkpoint: checkpointValue,
                checkpoint_normalized: checkpointValue,
                count: '✓',
            });
        }
    });
    cpList.innerHTML = normalizedItems.length > 0 ? normalizedItems.map(cp => `
        <label class="checkbox-label">
            <input type="checkbox" value="${escapeHtml(getCheckpointOptionValue(cp))}" ${selectedCheckpointValues.has(getCheckpointOptionValue(cp)) ? 'checked' : ''}>
            <span class="checkbox-custom"></span>
            <span class="checkbox-text">${escapeHtml(cp.checkpoint || getCheckpointOptionValue(cp))}</span>
            <span class="checkbox-count">${cp.count}</span>
        </label>
    `).join('') : `<div class="filter-empty-state">${escapeHtml(t('filter.noCheckpoints', null, 'No checkpoints found yet.'))}</div>`;
}

function renderLoraFilterList(loras, t = appT) {
    const loraList = $('#modal-lora-list');
    if (!loraList) return;
    const filterState = getFilterModalState();
    const normalizedItems = [...(loras || [])];
    const presentValues = new Set(normalizedItems.map(l => l.lora).filter(Boolean));
    (filterState.loras || []).forEach((lora) => {
        if (lora && !presentValues.has(lora)) {
            normalizedItems.push({ lora, count: '✓' });
        }
    });
    loraList.innerHTML = normalizedItems.length > 0 ? normalizedItems.map(l => `
        <label class="checkbox-label">
            <input type="checkbox" value="${escapeHtml(l.lora)}" ${filterState.loras?.includes(l.lora) ? 'checked' : ''}>
            <span class="checkbox-custom"></span>
            <span class="checkbox-text">${escapeHtml(l.lora)}</span>
            <span class="checkbox-count">${l.count}</span>
        </label>
    `).join('') : `<div class="filter-empty-state">${escapeHtml(t('filter.noLoras', null, 'No LoRAs found yet.'))}</div>`;
}

// ---- Aurora Phase 3 (24d): live hit-count preview on the Apply button ----
// Debounced: every modal change reads the WOULD-BE state via
// readFilterModalDomInto and asks GET /api/images/count how many images
// match, so "Apply" shows the outcome before committing. Degrades silently
// (plain "Apply Filters" label) when the endpoint is unavailable.
let _filterCountPreviewTimer = null;
let _filterCountPreviewAbort = null;

function scheduleFilterCountPreview() {
    if (_filterCountPreviewTimer) clearTimeout(_filterCountPreviewTimer);
    _filterCountPreviewTimer = setTimeout(() => {
        _filterCountPreviewTimer = null;
        runFilterCountPreview();
    }, 600);
}

async function runFilterCountPreview() {
    const applyButton = $('#btn-apply-modal-filters');
    const modal = document.getElementById('filter-modal');
    if (!applyButton || !modal || !modal.classList.contains('visible')) return;

    const candidate = readFilterModalDomInto(cloneFilterState(getFilterModalState()));
    const params = API.buildFilterQueryParams(candidate);
    try {
        if (_filterCountPreviewAbort) _filterCountPreviewAbort.abort();
        _filterCountPreviewAbort = new AbortController();
        const resp = await (window.apiFetch || fetch)(`/api/images/count?${params}`, {
            signal: _filterCountPreviewAbort.signal,
            headers: (window.libraryFetchHeaders || ((h) => h))({ Accept: 'application/json' }),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const total = Number(data?.total);
        if (Number.isFinite(total) && total >= 0) {
            // Lock the label first: I18n.applyToDOM and ui-refresh's
            // _setButton both re-translate this button (data-i18n +
            // explicit _setButton call), and the observer fires on our own
            // write — without the lock the count is clobbered one frame later.
            applyButton.dataset.i18nLocked = '1';
            applyButton.textContent = appT('filter.applyWithCount', 'Apply · ~{count} images')
                .replace('{count}', total.toLocaleString());
        }
    } catch (e) { /* aborted / offline — keep the last label */ }
}

function updateFilterModalSummary() {
    const selectionSummary = $('#filter-modal-selection-summary');
    const summaryHint = $('#filter-modal-summary-hint');
    const filterState = getFilterModalState();
    scheduleFilterCountPreview();
    const t = (key, params, fallback) => {
        const translated = window.I18n?.t?.(key, params);
        return translated && translated !== key ? translated : (fallback || key);
    };

    const countChecked = (selector, fallback = 0) => {
        const matches = $$(selector);
        return matches.length > 0 ? matches.length : fallback;
    };
    const setCount = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    };

    const generatorTotal = Math.max(1, $$('#modal-generator-filters input').length || 5);
    const ratingTotal = Math.max(1, $$('#modal-rating-filters input').length || 4);
    const generatorCount = countChecked('#modal-generator-filters input:checked', filterState.generators?.length || generatorTotal);
    const ratingCount = countChecked('#modal-rating-filters input:checked', filterState.ratings?.length || ratingTotal);
    const checkpointCount = countChecked('#modal-checkpoint-list input:checked', filterState.checkpoints?.length || 0);
    const loraCount = countChecked('#modal-lora-list input:checked', filterState.loras?.length || 0);
    const tagCount = filterState.tags?.length || 0;
    const promptCount = filterState.prompts?.length || 0;
    const minWidth = parseInt($('#filter-min-width')?.value, 10) || null;
    const maxWidth = parseInt($('#filter-max-width')?.value, 10) || null;
    const minHeight = parseInt($('#filter-min-height')?.value, 10) || null;
    const maxHeight = parseInt($('#filter-max-height')?.value, 10) || null;
    const aspectRatio = $('input[name="aspect-ratio"]:checked')?.value || '';
    const hasMetadataChoice = $('input[name="has-metadata"]:checked')?.value || '';
    const dimensionCount = [minWidth, maxWidth, minHeight, maxHeight].filter(Boolean).length + (aspectRatio ? 1 : 0) + (hasMetadataChoice ? 1 : 0);
    const brightnessMin = parseFloat($('#filter-brightness-min')?.value) || null;
    const brightnessMax = parseFloat($('#filter-brightness-max')?.value) || null;
    const colorTemperature = $('input[name="color-temperature"]:checked')?.value || '';
    const brightnessDistribution = $('input[name="brightness-distribution"]:checked')?.value || '';
    const colorHueCount = $$('input[name="color-hue"]:checked').length;
    const colorCount = [brightnessMin, brightnessMax].filter(Boolean).length + (colorTemperature ? 1 : 0) + (brightnessDistribution ? 1 : 0) + (colorHueCount ? 1 : 0);

    setCount('filter-modal-count-generators', `${generatorCount}/${generatorTotal}`);
    setCount('filter-modal-count-ratings', `${ratingCount}/${ratingTotal}`);
    setCount('filter-modal-count-tags', String(tagCount));
    setCount('filter-modal-count-prompts', String(promptCount));
    setCount('filter-modal-count-checkpoints', String(checkpointCount));
    setCount('filter-modal-count-loras', String(loraCount));
    setCount('filter-modal-count-dimensions', dimensionCount > 0 ? String(dimensionCount) : t('filter.any', null, 'Any'));
    setCount('filter-modal-count-colors', colorCount > 0 ? String(colorCount) : t('filter.any', null, 'Any'));

    // Aesthetic stat
    const aestheticMin = filterState.minAesthetic;
    const aestheticMax = filterState.maxAesthetic;
    const aestheticLabel = (aestheticMin || aestheticMax)
        ? `${aestheticMin ?? '0'} - ${aestheticMax ?? '10'}`
        : t('filter.any', null, 'Any');
    setCount('filter-modal-count-aesthetic', aestheticLabel);

    const activeGroupCount = [
        generatorCount !== generatorTotal,
        ratingCount !== ratingTotal,
        tagCount > 0,
        promptCount > 0,
        checkpointCount > 0,
        loraCount > 0,
        dimensionCount > 0,
        colorCount > 0
    ].filter(Boolean).length;

    if (selectionSummary) {
        selectionSummary.textContent = activeGroupCount > 0
            ? t('filter.summaryReady', { count: activeGroupCount }, `${activeGroupCount} filter groups are active.`)
            : t('filter.summaryIdle', null, 'No extra limits selected yet. Apply now to keep the current gallery scope.');
    }

    if (summaryHint) {
        summaryHint.textContent = activeGroupCount > 0
            ? t('filter.summaryHintActive', null, 'Tip: start broad, then add tags or prompts before tightening size, checkpoint, or LoRA filters.')
            : t('filter.summaryHintIdle', null, 'Tip: use tags, prompts, or dimensions when you want a smaller and more targeted result list.');
    }
}

// searchModalTags - debounced wrapper for tag autocomplete in filter modal
const _debouncedTagSearch = debounce(async (query) => {
    const suggestionsEl = $('#modal-tag-suggestions');
    if (!query || query.length < 2) {
        suggestionsEl.innerHTML = '';
        suggestionsEl.classList.remove('visible');
        return;
    }

    try {
        const normalizedQuery = query.toLowerCase().replace(/_/g, ' ');
        if (FilterModalController.optionData?.tags) {
            const filtered = FilterModalController.optionData.tags
                .filter(t => t.tag.toLowerCase().replace(/_/g, ' ').includes(normalizedQuery))
                .slice(0, FACET_SUGGESTION_LIMIT);

            if (filtered.length > 0) {
                suggestionsEl.innerHTML = filtered.map(t => `
                    <div class="tag-suggestion" data-tag="${escapeHtml(t.tag)}">
                        ${escapeHtml(t.tag)} <span style="color: var(--text-muted)">(${t.count})</span>
                    </div>
                `).join('');

                suggestionsEl.classList.add('visible');
                suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
                    el.addEventListener('click', () => {
                        const filterState = getFilterModalState();
                        if (!filterState.tags.includes(el.dataset.tag)) {
                            filterState.tags = [...filterState.tags, el.dataset.tag];
                            renderModalActiveTags();
                        }
                        $('#modal-tag-search').value = '';
                        suggestionsEl.innerHTML = '';
                        suggestionsEl.classList.remove('visible');
                    });
                });
                return;
            }
        }

        const result = await API.getTagsLibrary('frequency', {
            query,
            limit: FACET_SUGGESTION_LIMIT,
        });
        const filtered = result.tags || [];

        suggestionsEl.innerHTML = filtered.map(t => `
            <div class="tag-suggestion" data-tag="${escapeHtml(t.tag)}">
                ${escapeHtml(t.tag)} <span style="color: var(--text-muted)">(${t.count})</span>
            </div>
        `).join('');

        if (filtered.length > 0) {
            suggestionsEl.classList.add('visible');
        } else {
            suggestionsEl.classList.remove('visible');
        }

        suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
            el.addEventListener('click', () => {
                const filterState = getFilterModalState();
                if (!filterState.tags.includes(el.dataset.tag)) {
                    filterState.tags = [...filterState.tags, el.dataset.tag];
                    renderModalActiveTags();
                }
                $('#modal-tag-search').value = '';
                suggestionsEl.innerHTML = '';
                suggestionsEl.classList.remove('visible');
            });
        });
    } catch (e) {
        // Tag search failed silently - non-critical autocomplete
    }
}, 250);

function searchModalTags(query) {
    _debouncedTagSearch(query);
}

// searchModalPrompts - debounced wrapper for prompt autocomplete in filter modal
const _debouncedPromptSearch = debounce(async (query) => {
    const suggestionsEl = $('#modal-prompt-suggestions');
    if (!suggestionsEl) return;

    if (!query || query.length < 2) {
        suggestionsEl.innerHTML = '';
        suggestionsEl.classList.remove('visible');
        return;
    }

    try {
        if (FilterModalController.optionData?.prompts) {
            const filtered = FilterModalController.optionData.prompts
                .filter(p => p.prompt.toLowerCase().includes(query.toLowerCase().replace(/_/g, ' ')))
                .slice(0, FACET_SUGGESTION_LIMIT);

            if (filtered.length > 0) {
                suggestionsEl.innerHTML = filtered.map(p => `
                    <div class="tag-suggestion" data-prompt="${escapeHtml(p.prompt)}">
                        ${escapeHtml(p.prompt)} <span style="color: var(--text-muted)">(${p.count})</span>
                    </div>
                `).join('');

                suggestionsEl.classList.add('visible');
                suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
                    el.addEventListener('click', () => {
                        const prompt = el.dataset.prompt;
                        const filterState = getFilterModalState();
                        const normalize = s => s.toLowerCase().replace(/_/g, ' ').trim();
                        const normalizedExisting = filterState.prompts.map(normalize);
                        if (!normalizedExisting.includes(normalize(prompt))) {
                            filterState.prompts = [...filterState.prompts, prompt];
                            renderModalActivePrompts();
                        }
                        $('#modal-prompt-search').value = '';
                        suggestionsEl.innerHTML = '';
                        suggestionsEl.classList.remove('visible');
                    });
                });
                return;
            }
        }

        const result = await API.getPromptsLibrary({
            query,
            limit: FACET_SUGGESTION_LIMIT,
        });
        const filtered = result.prompts || [];

        suggestionsEl.innerHTML = filtered.map(p => `
            <div class="tag-suggestion" data-prompt="${escapeHtml(p.prompt)}">
                ${escapeHtml(p.prompt)} <span style="color: var(--text-muted)">(${p.count})</span>
            </div>
        `).join('');

        if (filtered.length > 0) {
            suggestionsEl.classList.add('visible');
        } else {
            suggestionsEl.classList.remove('visible');
        }

        suggestionsEl.querySelectorAll('.tag-suggestion').forEach(el => {
            el.addEventListener('click', () => {
                const prompt = el.dataset.prompt;
                const filterState = getFilterModalState();
                const normalize = s => s.toLowerCase().replace(/_/g, ' ').trim();
                const normalizedExisting = filterState.prompts.map(normalize);
                if (!normalizedExisting.includes(normalize(prompt))) {
                    filterState.prompts = [...filterState.prompts, prompt];
                    renderModalActivePrompts();
                }
                $('#modal-prompt-search').value = '';
                suggestionsEl.innerHTML = '';
                suggestionsEl.classList.remove('visible');
            });
        });
    } catch (e) {
        // Prompt search failed silently - non-critical autocomplete
    }
}, 250);

function searchModalPrompts(query) {
    _debouncedPromptSearch(query);
}

/**
 * Collect checked values from a filter checkbox list, treating "every option
 * checked" (with no active search narrowing the list) as NO restriction — an
 * empty array. Sending the explicit full list instead pushes an IN(...) into the
 * query that silently drops every image whose checkpoint is NULL or that used
 * zero LoRAs, which made "select all" return far fewer images than expected
 * (v3.3.2 bug). Mirrors how the rating/generator filters collapse "all" to none.
 */
function _collectAllAwareCheckboxValues(listId, searchId) {
    const list = document.getElementById(listId);
    if (!list) return [];
    const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
    const checked = boxes.filter((cb) => cb.checked).map((cb) => cb.value);
    const searchEl = searchId ? document.getElementById(searchId) : null;
    const searchActive = Boolean(searchEl && searchEl.value.trim());
    // Collapse "every option selected" to no-restriction only when there are
    // multiple options. A single checked box is always intentional (B2: small
    // libraries / one base model looked "selected" but never narrowed results).
    // Search-active lists still keep the explicit checked subset.
    if (!searchActive && boxes.length > 1 && checked.length === boxes.length) {
        return [];
    }
    return checked;
}

// Reads every filter-modal control into ``filterState``. Shared by the Apply
// path and the live hit-count preview (Aurora Phase 3, 24d) so the predicted
// count is computed from EXACTLY what Apply would commit.
function readFilterModalDomInto(filterState) {
    // Get generators
    const generators = [];
    $$('#modal-generator-filters input:checked').forEach(cb => generators.push(cb.value));
    filterState.generators = generators;

    // Get ratings
    const ratings = [];
    $$('#modal-rating-filters input:checked').forEach(cb => ratings.push(cb.value));
    filterState.ratings = ratings;

    // Get checkpoints / loras. "Select all" must mean NO restriction (match
    // every image), not an explicit IN(...) list that would drop NULL-checkpoint
    // / zero-LoRA images. See _collectAllAwareCheckboxValues.
    // Normalize checkpoint values so display names with .safetensors / hashes
    // match backend checkpoint_normalized (B2: selected base model not applied).
    const rawCheckpoints = _collectAllAwareCheckboxValues('modal-checkpoint-list', 'modal-checkpoint-search');
    filterState.checkpoints = typeof normalizeCheckpointFilterValue === 'function'
        ? [...new Set(rawCheckpoints.map(normalizeCheckpointFilterValue).filter(Boolean))]
        : rawCheckpoints;
    filterState.loras = _collectAllAwareCheckboxValues('modal-lora-list', 'modal-lora-search');

    // Prompts: don't use prompt search bar as text search — prompts array is built via Enter key
    // But read the free-text search field for filename/prompt text search
    const freeTextSearch = $('#modal-free-text-search');
    filterState.search = freeTextSearch ? freeTextSearch.value.trim() : '';
    const promptMatchRadio = $('input[name="prompt-match-mode"]:checked');
    filterState.promptMatchMode = normalizePromptMatchMode(promptMatchRadio?.value);
    const tagModeRadio = $('input[name="tag-match-mode"]:checked');
    filterState.tagMode = tagModeRadio?.value || 'and';

    // Get dimension filters
    filterState.minWidth = parseInt($('#filter-min-width')?.value, 10) || null;
    filterState.maxWidth = parseInt($('#filter-max-width')?.value, 10) || null;
    filterState.minHeight = parseInt($('#filter-min-height')?.value, 10) || null;
    filterState.maxHeight = parseInt($('#filter-max-height')?.value, 10) || null;

    // Get aspect ratio
    const aspectRadio = $('input[name="aspect-ratio"]:checked');
    filterState.aspectRatio = aspectRadio ? aspectRadio.value : '';

    // v3.3.2 small-opt: "has SD generation parameters" tri-state ('' = all)
    const hasMetaRadio = $('input[name="has-metadata"]:checked');
    const hasMetaValue = hasMetaRadio ? hasMetaRadio.value : '';
    filterState.hasMetadata = hasMetaValue === 'true' ? true : (hasMetaValue === 'false' ? false : null);

    // File-time date range (native date inputs emit YYYY-MM-DD or '')
    filterState.dateFrom = $('#filter-date-from')?.value || null;
    filterState.dateTo = $('#filter-date-to')?.value || null;

    // Get aesthetic score range
    filterState.minAesthetic = parseFloat($('#filter-aesthetic-min')?.value) || null;
    filterState.maxAesthetic = parseFloat($('#filter-aesthetic-max')?.value) || null;

    // Aurora Phase 3 (24d): the Unscored tier = aesthetic_score IS NULL. It is
    // mutually exclusive with a scored range.
    const aestheticGroup = $('.aesthetic-quick-filters');
    filterState.aestheticUnscored = aestheticGroup?.dataset.unscored === '1' ? true : null;
    if (filterState.aestheticUnscored) {
        filterState.minAesthetic = null;
        filterState.maxAesthetic = null;
    }

    // v3.3.3 WIRING-01: minimum user star rating (1-5; '' = any).
    filterState.minUserRating = parseInt($('#filter-user-rating-min')?.value, 10) || null;

    const colorTemperatureRadio = $('input[name="color-temperature"]:checked');
    const brightnessDistributionRadio = $('input[name="brightness-distribution"]:checked');
    filterState.brightnessMin = parseFloat($('#filter-brightness-min')?.value) || null;
    filterState.brightnessMax = parseFloat($('#filter-brightness-max')?.value) || null;
    filterState.colorTemperature = colorTemperatureRadio ? colorTemperatureRadio.value : '';
    filterState.colorHues = Array.from($$('input[name="color-hue"]:checked'), cb => cb.value);
    filterState.brightnessDistribution = brightnessDistributionRadio ? brightnessDistributionRadio.value : '';

    // Aurora Phase 3 (24d): saturation range (0-255, needs color analysis).
    const saturationMinRaw = parseFloat($('#filter-saturation-min')?.value);
    const saturationMaxRaw = parseFloat($('#filter-saturation-max')?.value);
    filterState.minSaturation = Number.isFinite(saturationMinRaw) ? saturationMinRaw : null;
    filterState.maxSaturation = Number.isFinite(saturationMaxRaw) ? saturationMaxRaw : null;

    return filterState;
}

function applyModalFilters() {
    const filterState = getFilterModalState();
    readFilterModalDomInto(filterState);
    const promptSearch = $('#modal-prompt-search');
    if (promptSearch) promptSearch.value = '';

    const committedFilters = commitFilterModalState(filterState);

    hideModal('filter-modal');

    if (FilterModalController.onApply) {
        FilterModalController.onApply(cloneFilterState(committedFilters));
        showToast(appT('filter.appliedToast', 'Filters applied'), 'success');
        resetFilterModalController();
        return;
    }

    // Update all filter summaries (gallery sidebar + view-specific)
    updateFilterSummary();
    if (typeof window.updateAutoSepSummary === 'function') window.updateAutoSepSummary();
    if (typeof window.invalidateAutoSepPreview === 'function') window.invalidateAutoSepPreview();
    if (typeof window.updateManualSortFilterSummary === 'function') window.updateManualSortFilterSummary();

    syncGenTabsWithFilters();
    loadImages();
    showToast(appT('filter.appliedToast', 'Filters applied'), 'success');
    resetFilterModalController();
}

// Sync generator tab active state with current filter state
function syncGenTabsWithFilters() {
    const gens = AppState.filters.generators;
    $$('.gen-tab').forEach(t => {
        if (gens.length === ALL_GENERATORS.length) {
            t.classList.toggle('active', t.dataset.gen === 'all');
        } else if (
            gens.length === OTHERS_GENERATOR_BUNDLE.length
            && OTHERS_GENERATOR_BUNDLE.every((gen) => gens.includes(gen))
        ) {
            t.classList.toggle('active', t.dataset.gen === 'others');
        } else if (gens.length === 1) {
            t.classList.toggle('active', t.dataset.gen === gens[0]);
        } else {
            t.classList.remove('active');
        }
        t.setAttribute('aria-selected', t.classList.contains('active') ? 'true' : 'false');
    });
    document.querySelector('.gen-tab.active')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    syncGeneratorRailOverflow();
}

// Reflect the current FilterStore aspectRatio on the gallery-header
// quick-toggle (FE-7). Reads the single source of truth so the toggle stays
// correct no matter how aspectRatio changed (toggle click, filter modal,
// preset load, clear filters). Safe no-op when the toggle is not in the DOM.
function syncAspectToggleWithFilters() {
    const buttons = $$('.aspect-quick-btn[data-aspect]');
    if (!buttons.length) return;
    const current = normalizeAspectRatioFilter(AppState.filters.aspectRatio);
    buttons.forEach(btn => {
        const isActive = normalizeAspectRatioFilter(btn.dataset.aspect) === current;
        btn.classList.toggle('is-active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
}

function resetAllFilters() {
    const filterState = getFilterModalState();
    copyFilterState(filterState, createDefaultFilterState());

    // Reset modal checkboxes
    $$('#modal-generator-filters input').forEach(cb => cb.checked = true);
    $$('#modal-rating-filters input').forEach(cb => cb.checked = true);
    $$('#modal-checkpoint-list input').forEach(cb => cb.checked = false);
    $$('#modal-lora-list input').forEach(cb => cb.checked = false);
    const modalPromptSearch = $('#modal-prompt-search');
    if (modalPromptSearch) modalPromptSearch.value = '';
    filterState.promptMatchMode = 'exact';
    $$('input[name="prompt-match-mode"]').forEach(radio => {
        radio.checked = radio.value === 'exact';
    });
    const freeTextSearch = $('#modal-free-text-search');
    if (freeTextSearch) freeTextSearch.value = '';
    // Reset aesthetic inputs
    const minAeInput = $('#filter-aesthetic-min');
    const maxAeInput = $('#filter-aesthetic-max');
    if (minAeInput) minAeInput.value = '';
    if (maxAeInput) maxAeInput.value = '';
    const minUserRatingInputReset = $('#filter-user-rating-min');
    if (minUserRatingInputReset) minUserRatingInputReset.value = '';
    const brightnessMinInput = $('#filter-brightness-min');
    const brightnessMaxInput = $('#filter-brightness-max');
    if (brightnessMinInput) brightnessMinInput.value = '';
    if (brightnessMaxInput) brightnessMaxInput.value = '';
    $$('input[name="color-temperature"]').forEach(r => r.checked = r.value === '');
    $$('input[name="color-hue"]').forEach(cb => cb.checked = false);
    $$('input[name="brightness-distribution"]').forEach(r => r.checked = r.value === '');
    renderModalActiveTags();
    renderModalActivePrompts();

    // Reset dimension filters
    const filterMinWidth = $('#filter-min-width');
    const filterMaxWidth = $('#filter-max-width');
    const filterMinHeight = $('#filter-min-height');
    const filterMaxHeight = $('#filter-max-height');
    if (filterMinWidth) filterMinWidth.value = '';
    if (filterMaxWidth) filterMaxWidth.value = '';
    if (filterMinHeight) filterMinHeight.value = '';
    if (filterMaxHeight) filterMaxHeight.value = '';
    $$('input[name="aspect-ratio"]').forEach(r => r.checked = r.value === '');
    $$('input[name="has-metadata"]').forEach(r => r.checked = r.value === '');
    updateSortReverseButton();
    updateFilterModalSummary();

    // Hide artist filter row
    const artistRow = $('#artist-filter-row');
    if (artistRow) artistRow.style.display = 'none';

    const committedFilters = commitFilterModalState(filterState);
    hideModal('filter-modal');

    // Clearing all filters also drops any folder scope — re-sync the folder tree
    // so its highlight and "Folder: …" chip don't linger after a reset.
    if (window.FolderTreeUI?.isScoped?.()) window.FolderTreeUI.refresh();

    if (FilterModalController.onReset) {
        FilterModalController.onReset(cloneFilterState(committedFilters));
        showToast(appT('filter.clearedToast', 'Filters cleared'), 'success');
        resetFilterModalController();
        return;
    }

    updateFilterSummary();
    if (typeof window.updateAutoSepSummary === 'function') window.updateAutoSepSummary();
    if (typeof window.invalidateAutoSepPreview === 'function') window.invalidateAutoSepPreview();
    if (typeof window.updateManualSortFilterSummary === 'function') window.updateManualSortFilterSummary();

    syncGenTabsWithFilters();
    loadImages();
    showToast(appT('filter.clearedToast', 'Filters cleared'), 'success');
    resetFilterModalController();
}

// ============== Filter Presets ==============

const FILTER_PRESETS_KEY = 'sd-image-sorter-filter-presets';

function getFilterPresets() {
    try {
        const saved = localStorage.getItem(FILTER_PRESETS_KEY);
        return saved ? JSON.parse(saved) : {};
    } catch (e) {
        return {};
    }
}

function saveFilterPreset(name) {
    if (!name || !name.trim()) {
        showToast(appT('filter.presetNameRequired', 'Please enter a preset name'), 'error');
        return false;
    }

    const presets = getFilterPresets();
    presets[name.trim()] = {
        ...cloneFilterState(AppState.filters),
        promptMatchMode: normalizePromptMatchMode(AppState.filters.promptMatchMode),
    };

    try {
        localStorage.setItem(FILTER_PRESETS_KEY, JSON.stringify(presets));
        showToast(appT('filter.presetSaved', 'Preset "{name}" saved', { name }), 'success');
        // Smart Folders v1: a re-saved pinned preset changed its filter state,
        // so its sidebar entry needs a fresh count.
        window.SmartFoldersUI?.refresh?.();
        return true;
    } catch (e) {
        showToast(appT('filter.presetSaveFailed', 'Failed to save preset'), 'error');
        return false;
    }
}

function loadFilterPreset(name) {
    const presets = getFilterPresets();
    const preset = presets[name];

    if (!preset) {
        showToast(appT('filter.presetMissing', 'Preset "{name}" not found', { name }), 'error');
        return false;
    }

    // Apply preset via shared filter setter so FilterStore stays in sync.
    setAppFilters({
        ...AppState.filters,
        ...preset,
    });

    updateFilterSummary();
    syncGenTabsWithFilters();

    // Update modal checkboxes to match
    $$('#modal-generator-filters input').forEach(cb => {
        cb.checked = AppState.filters.generators.includes(cb.value);
    });
    $$('#modal-rating-filters input').forEach(cb => {
        cb.checked = AppState.filters.ratings.includes(cb.value);
    });

    closeFilterModal();
    loadImages();
    showToast(appT('filter.presetLoaded', 'Preset "{name}" loaded', { name }), 'success');
    return true;
}

function deleteFilterPreset(name) {
    const presets = getFilterPresets();
    if (presets[name]) {
        delete presets[name];
        localStorage.setItem(FILTER_PRESETS_KEY, JSON.stringify(presets));
        showToast(appT('filter.presetDeleted', 'Preset "{name}" deleted', { name }), 'success');
        // Smart Folders v1: drop the orphaned pin so the sidebar never shows
        // an entry whose preset no longer exists.
        window.SmartFoldersUI?.handlePresetDeleted?.(name);
        return true;
    }
    return false;
}

function renderFilterPresets() {
    const container = $('#filter-presets-list');
    if (!container) return;

    const presets = getFilterPresets();
    const presetNames = Object.keys(presets);

    if (presetNames.length === 0) {
        container.innerHTML = `<div class="presets-empty">${escapeHtml(appT('filter.presetsEmpty', 'No saved presets'))}</div>`;
        return;
    }

    // Smart Folders v1: per-preset pin toggle (pinned = shows as a sidebar
    // "smart folder" with a live count). Rendered only when the module is
    // loaded so the presets bar is unchanged if it's ever absent.
    const smartFolders = window.SmartFoldersUI;
    container.innerHTML = presetNames.map(name => {
        const safeName = escapeHtml(name);
        const isPinned = Boolean(smartFolders?.isPinned?.(name));
        const pinButton = smartFolders ? `
                <button class="btn-small preset-pin${isPinned ? ' is-pinned' : ''}" data-preset-action="pin" data-preset-name="${safeName}"
                    aria-pressed="${isPinned ? 'true' : 'false'}"
                    title="${escapeHtml(isPinned ? appT('filter.unpinPreset', 'Unpin from sidebar') : appT('filter.pinPreset', 'Pin to sidebar as a smart folder'))}"><svg class="icon" aria-hidden="true"><use href="#i-pin"/></svg></button>` : '';
        return `
        <div class="preset-item">
            <span class="preset-name">${safeName}</span>
            <div class="preset-actions">${pinButton}
                <button class="btn-small" data-preset-action="load" data-preset-name="${safeName}">${escapeHtml(appT('filter.loadPreset', 'Load'))}</button>
                <button class="btn-small btn-danger" data-preset-action="delete" data-preset-name="${safeName}">×</button>
            </div>
        </div>
    `;}).join('');

    container.querySelectorAll('[data-preset-action]').forEach(button => {
        button.addEventListener('click', () => {
            const { presetAction, presetName } = button.dataset;
            if (presetAction === 'load') {
                loadFilterPreset(presetName);
            } else if (presetAction === 'pin') {
                window.SmartFoldersUI?.togglePin?.(presetName);
                renderFilterPresets();
            } else if (presetAction === 'delete' && deleteFilterPreset(presetName)) {
                renderFilterPresets();
            }
        });
    });
}

// Make preset functions globally accessible
window.saveFilterPreset = saveFilterPreset;
window.loadFilterPreset = loadFilterPreset;
window.deleteFilterPreset = deleteFilterPreset;
window.renderFilterPresets = renderFilterPresets;

