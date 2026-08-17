/**
 * app/library-modelselect.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 6412-6883 (of 10,152): model-select modal + tags library.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
// ============== UI Components ==============

function normalizeCheckpointFilterValue(value) {
    let text = String(value || '').trim();
    if (!text) return '';
    text = text.replace(/\\/g, '/').split('/').pop().trim();
    text = text.replace(/\s+\[[0-9a-fA-F]{4,}\]\s*$/, '').trim();
    text = text.replace(/\.(safetensors|ckpt|pt|pth|bin|onnx)$/i, '').trim();
    return text;
}

function getCheckpointOptionValue(item) {
    return normalizeCheckpointFilterValue(item?.checkpoint_normalized || item?.checkpoint || item);
}

function openModelSelect(type) {
    AppState.modalSelection.type = type;
    AppState.modalSelection.search = '';
    const currentSelection = AppState.filters[`${type}s`] || [];
    AppState.modalSelection.tempSelected = new Set(
        type === 'checkpoint'
            ? currentSelection.map(normalizeCheckpointFilterValue).filter(Boolean)
            : currentSelection
    );

    $('#model-select-title').textContent = type === 'checkpoint'
        ? appT('modelSelect.checkpointsTitle', 'Select Models')
        : appT('modelSelect.lorasTitle', 'Select LoRAs');
    $('#model-select-search').value = '';

    renderModelSelectList();
    showModal('model-select-modal');
}

function renderModelSelectList() {
    const { type, tempSelected, search } = AppState.modalSelection;
    const items = type === 'checkpoint' ? AppState.analytics.checkpoints : AppState.analytics.loras;
    const list = $('#model-select-list');

    if (!items || items.length === 0) {
        list.innerHTML = `<div class="filter-empty" style="text-align: center; padding: 20px; color: var(--text-muted);">${escapeHtml(appT('modelSelect.empty', 'No models found'))}</div>`;
        return;
    }

    const filtered = items.filter(item => {
        const value = type === 'checkpoint' ? getCheckpointOptionValue(item) : item.lora;
        const label = type === 'checkpoint' ? (item.checkpoint || value) : item.lora;
        return String(label || value || '').toLowerCase().includes(search);
    });

    list.innerHTML = filtered.map(item => {
        const value = type === 'checkpoint' ? getCheckpointOptionValue(item) : item.lora;
        const label = type === 'checkpoint' ? (item.checkpoint || value) : item.lora;
        const isSelected = tempSelected.has(value);
        const safeValue = escapeHtml(value);
        const safeLabel = escapeHtml(label);

        return `
            <div class="model-select-item ${isSelected ? 'selected' : ''}" data-value="${safeValue}">
                <div class="checkbox-custom" style="background: ${isSelected ? 'var(--accent-primary)' : 'transparent'}; border-color: ${isSelected ? 'var(--accent-primary)' : 'var(--border-color)'}">
                    ${isSelected ? '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg>' : ''}
                </div>
                <div class="item-text" title="${safeLabel}">${safeLabel}</div>
                <div class="item-count">${item.count}</div>
            </div>
        `;
    }).join('');

    // Add click handlers
    list.querySelectorAll('.model-select-item').forEach(el => {
        el.addEventListener('click', () => {
            const val = el.dataset.value;
            if (tempSelected.has(val)) {
                tempSelected.delete(val);
            } else {
                tempSelected.add(val);
            }
            renderModelSelectList();
        });
    });
}

function confirmModelSelection() {
    const { type, tempSelected } = AppState.modalSelection;
    const selected = Array.from(tempSelected);
    updateAppFilters((filters) => {
        // B2: model-select used to update the store without reloading the
        // gallery, so "selected base model" looked applied in the summary
        // but results never narrowed.
        if (type === 'checkpoint' && typeof normalizeCheckpointFilterValue === 'function') {
            filters.checkpoints = selected.map(normalizeCheckpointFilterValue).filter(Boolean);
        } else {
            filters[`${type}s`] = selected;
        }
    });

    updateModelSelectionSummaries();
    if (typeof updateFilterSummary === 'function') updateFilterSummary();
    hideModal('model-select-modal');
    if (typeof loadImages === 'function') loadImages();
}

function updateModelSelectionSummaries() {
    const cpCount = AppState.filters.checkpoints?.length || 0;
    const lrCount = AppState.filters.loras?.length || 0;

    // These elements may not exist in compact sidebar - use optional chaining
    const cpSummary = $('#selection-summary-checkpoints');
    const loraSummary = $('#selection-summary-loras');

    if (cpSummary) {
        cpSummary.textContent = cpCount === 0 ? 'No checkpoints selected' :
            (cpCount === 1 ? AppState.filters.checkpoints[0] : `${cpCount} checkpoints selected`);
    }

    if (loraSummary) {
        loraSummary.textContent = lrCount === 0 ? 'No Loras selected' :
            (lrCount === 1 ? AppState.filters.loras[0] : `${lrCount} Loras selected`);
    }
}

function updateCollapsibleFilterUI(type, items) {
    // Legacy support, now using summaries
    updateModelSelectionSummaries();
}

// ============== Tags & Prompts Library ==============

const LIBRARY_DISPLAY_LIMIT = 500;

const libraryData = {
    currentTab: 'tags',
    tags: [],
    prompts: [],
    loras: [],
    filterState: null,
    returnFilterOptions: null,
    optionData: null,
    searchRequestId: 0,
};

function openTagsLibrary(options = {}) {
    libraryData.filterState = options.filterState || null;
    libraryData.returnFilterOptions = options.returnFilterOptions || null;
    libraryData.optionData = options.optionData || null;
    const searchInput = $('#library-search');
    if (searchInput) {
        searchInput.value = '';
    }
    showModal('tags-library-modal');
    loadLibraryContent();
}

function finishTagsLibraryInteraction() {
    const returnFilterOptions = libraryData.returnFilterOptions;
    hideModal('tags-library-modal');
    libraryData.filterState = null;
    libraryData.returnFilterOptions = null;
    libraryData.optionData = null;

    if (returnFilterOptions) {
        openFilterModal(returnFilterOptions);
    }
}

function switchLibraryTab(tab) {
    libraryData.currentTab = tab;
    const searchInput = $('#library-search');
    if (searchInput) {
        searchInput.value = '';
    }
    // Update tab button active states
    const tagsTab = $('#library-tab-tags');
    const promptsTab = $('#library-tab-prompts');
    const lorasTab = $('#library-tab-loras');
    const checkpointsTab = $('#library-tab-checkpoints');
    if (tagsTab) {
        tagsTab.classList.toggle('active', tab === 'tags');
        tagsTab.classList.toggle('btn-secondary', tab === 'tags');
        tagsTab.classList.toggle('btn-ghost', tab !== 'tags');
    }
    if (promptsTab) {
        promptsTab.classList.toggle('active', tab === 'prompts');
        promptsTab.classList.toggle('btn-secondary', tab === 'prompts');
        promptsTab.classList.toggle('btn-ghost', tab !== 'prompts');
    }
    if (lorasTab) {
        lorasTab.classList.toggle('active', tab === 'loras');
        lorasTab.classList.toggle('btn-secondary', tab === 'loras');
        lorasTab.classList.toggle('btn-ghost', tab !== 'loras');
    }
    if (checkpointsTab) {
        checkpointsTab.classList.toggle('active', tab === 'checkpoints');
        checkpointsTab.classList.toggle('btn-secondary', tab === 'checkpoints');
        checkpointsTab.classList.toggle('btn-ghost', tab !== 'checkpoints');
    }
    loadLibraryContent();
}

async function fetchLibraryFacet(tab, { sortBy = 'frequency', query = '', optionData = null } = {}) {
    const normalizedQuery = String(query || '').trim();
    if (optionData && !normalizedQuery) {
        if (tab === 'tags' && optionData.tags?.length) {
            return { items: optionData.tags.slice(0, LIBRARY_DISPLAY_LIMIT), total: optionData.tags.length };
        }
        if (tab === 'loras' && optionData.loras?.length) {
            return { items: optionData.loras.slice(0, LIBRARY_DISPLAY_LIMIT), total: optionData.loras.length };
        }
        if (tab === 'prompts' && optionData.prompts?.length) {
            return { items: optionData.prompts.slice(0, LIBRARY_DISPLAY_LIMIT), total: optionData.prompts.length };
        }
    }

    if (tab === 'tags') {
        const result = await API.getTagsLibrary(sortBy, {
            limit: LIBRARY_DISPLAY_LIMIT,
            query: normalizedQuery || null,
        });
        return { items: result.tags || [], total: result.total || 0 };
    }
    if (tab === 'loras') {
        const result = await API.getLorasLibrary({
            limit: LIBRARY_DISPLAY_LIMIT,
            query: normalizedQuery || null,
        });
        return { items: result.loras || [], total: result.total || 0 };
    }
    if (tab === 'checkpoints') {
        const result = await API.getCheckpointsLibrary({
            limit: LIBRARY_DISPLAY_LIMIT,
            query: normalizedQuery || null,
        });
        return { items: result.checkpoints || [], total: result.total || 0 };
    }
    const result = await API.getPromptsLibrary({
        limit: LIBRARY_DISPLAY_LIMIT,
        query: normalizedQuery || null,
    });
    return { items: result.prompts || [], total: result.total || 0 };
}

function renderLibraryFacet(tab, items) {
    if (tab === 'tags') {
        libraryData.tags = items;
        renderLibraryTags(items);
    } else if (tab === 'loras') {
        libraryData.loras = items;
        renderLibraryLoras(items);
    } else if (tab === 'checkpoints') {
        libraryData.checkpoints = items;
        renderLibraryCheckpoints(items);
    } else {
        libraryData.prompts = items;
        renderLibraryPrompts(items);
    }
}

function setLibraryStatsText(tab, shownCount, totalCount) {
    const statsText = $('#library-stats-text');
    if (!statsText) return;
    // Defensive: this field is JS-owned; make sure no stray data-i18n can let a
    // later applyToDOM reset the count back to "Loading...".
    statsText.removeAttribute('data-i18n');
    const t = (key, params, fallback) => {
        const translated = window.I18n?.t?.(key, params);
        return translated && translated !== key ? translated : (fallback || key);
    };
    const isLimited = shownCount < totalCount;
    const params = { shown: shownCount, total: totalCount, count: totalCount };
    if (tab === 'tags') {
        statsText.textContent = isLimited
            ? t('library.tagsShown', params, `Showing ${shownCount} of ${totalCount} unique tags`)
            : t('library.tagsFound', params, `${totalCount} unique tags found`);
    } else if (tab === 'loras') {
        statsText.textContent = isLimited
            ? t('library.lorasShown', params, `Showing ${shownCount} of ${totalCount} unique LoRAs`)
            : t('library.lorasFound', params, `${totalCount} unique LoRAs found`);
    } else if (tab === 'checkpoints') {
        statsText.textContent = isLimited
            ? t('library.checkpointsShown', params, `Showing ${shownCount} of ${totalCount} unique checkpoints`)
            : t('library.checkpointsFound', params, `${totalCount} unique checkpoints found`);
    } else {
        statsText.textContent = isLimited
            ? t('library.promptsShown', params, `Showing ${shownCount} of ${totalCount} unique prompts`)
            : t('library.promptsFound', params, `${totalCount} unique prompts found`);
    }
}

function renderLibraryLoadError(tab, error) {
    const content = $('#library-content');
    const statsText = $('#library-stats-text');
    const fallbackMessages = {
        tags: appT('library.loadTagsFailed', 'Failed to load tag library'),
        prompts: appT('library.loadPromptsFailed', 'Failed to load prompt library'),
        loras: appT('library.loadLorasFailed', 'Failed to load LoRA library'),
        checkpoints: appT('library.loadCheckpointsFailed', 'Failed to load checkpoint library'),
    };
    const fallbackMessage = fallbackMessages[tab] || fallbackMessages.tags;
    const message = formatUserError(error, fallbackMessage);
    content.innerHTML = `
        <div class="library-status library-status-error">
            <strong>${escapeHtml(fallbackMessage)}</strong>
            <p>${escapeHtml(message)}</p>
        </div>
    `;
    if (statsText) {
        statsText.textContent = message;
    }
}

async function loadLibraryContent() {
    const content = $('#library-content');
    const statsText = $('#library-stats-text');
    const sortBy = $('#library-sort')?.value || 'frequency';
    const currentTab = libraryData.currentTab;
    const requestId = ++libraryData.searchRequestId;
    const t = (key, params, fallback) => {
        const translated = window.I18n?.t?.(key, params);
        return translated && translated !== key ? translated : (fallback || key);
    };
    const loadingLabels = {
        tags: t('library.loadingTags', null, 'Loading tag library…'),
        prompts: t('library.loadingPrompts', null, 'Loading prompt library…'),
        loras: t('library.loadingLoras', null, 'Loading LoRA library…'),
        checkpoints: t('library.loadingCheckpoints', null, 'Loading checkpoint library…')
    };
    const loadingLabel = loadingLabels[currentTab] || loadingLabels.tags;

    content.innerHTML = `
        <div class="library-status">
            <div class="spinner" aria-hidden="true"></div>
            <p>${loadingLabel}</p>
        </div>
    `;
    if (statsText) {
        statsText.textContent = window.I18n?.t?.('library.loading') || loadingLabel;
    }

    try {
        const result = await fetchLibraryFacet(currentTab, {
            sortBy,
            optionData: libraryData.optionData,
        });
        if (requestId !== libraryData.searchRequestId || currentTab !== libraryData.currentTab) return;
        renderLibraryFacet(currentTab, result.items);
        setLibraryStatsText(currentTab, result.items.length, result.total);
    } catch (error) {
        if (requestId !== libraryData.searchRequestId || currentTab !== libraryData.currentTab) return;
        renderLibraryLoadError(currentTab, error);
        Logger.error('Library load error:', error);
    }
}

function renderLibraryTags(tags) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    if (!tags || tags.length === 0) {
        content.innerHTML = '<p class="empty-state-text" style="width:100%;text-align:center;padding:32px;color:var(--text-muted)">' + escapeHtml(appT('library.tagsEmpty', 'No tags found. Scan a folder and run Tag Images first.')) + '</p>';
        return;
    }
    const addHint = escapeHtml(appT('library.clickToAddFilter', 'Click to add as filter'));
    content.innerHTML = tags.map(t => `
        <div class="library-tag" data-tag="${escapeHtml(t.tag)}" title="${addHint}">
            <span class="tag-name">${escapeHtml(t.tag)}</span>
            <span class="tag-count">${t.count}</span>
        </div>
    `).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const tag = el.dataset.tag;
            const filterState = libraryData.filterState || AppState.filters;
            if (!filterState.tags.includes(tag)) {
                filterState.tags = [...filterState.tags, tag];
                if (!libraryData.returnFilterOptions) {
                    updateFilterSummary();
                    loadImages();
                }
                finishTagsLibraryInteraction();
                showToast(appT('library.addedFilter', 'Added "{value}" to filters').replace('{value}', tag), 'success');
            }
        });
    });
}

function renderLibraryPrompts(prompts) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    if (!prompts || prompts.length === 0) {
        content.innerHTML = '<p class="empty-state-text" style="width:100%;text-align:center;padding:32px;color:var(--text-muted)">' + escapeHtml(appT('library.promptsEmpty', 'No prompts yet. Import images with prompt info first.')) + '</p>';
        return;
    }
    const addHint = escapeHtml(appT('library.clickToAddFilter', 'Click to add as filter'));
    content.innerHTML = prompts.map(p => `
        <div class="library-tag" data-prompt="${escapeHtml(p.prompt)}" title="${addHint}">
            <span class="tag-name">${escapeHtml(p.prompt)}</span>
            <span class="tag-count">${p.count}</span>
        </div>
    `).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const prompt = el.dataset.prompt;
            const filterState = libraryData.filterState || AppState.filters;
            if (!filterState.prompts.includes(prompt)) {
                filterState.prompts = [...filterState.prompts, prompt];
                if (!libraryData.returnFilterOptions) {
                    updateFilterSummary();
                    loadImages();
                }
                finishTagsLibraryInteraction();
                showToast(appT('library.addedFilter', 'Added "{value}" to filters').replace('{value}', prompt), 'success');
            }
        });
    });
}

function renderLibraryLoras(loras) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    if (!loras || loras.length === 0) {
        content.innerHTML = '<p class="empty-state-text" style="width:100%;text-align:center;padding:32px;color:var(--text-muted)">' + escapeHtml(appT('library.lorasEmpty', 'No LoRA info yet. Import images with LoRA info first.')) + '</p>';
        return;
    }
    const addHint = escapeHtml(appT('library.clickToAddFilter', 'Click to add as filter'));
    content.innerHTML = loras.map(l => `
        <div class="library-tag" data-lora="${escapeHtml(l.lora)}" title="${addHint}">
            <span class="tag-name">${escapeHtml(l.lora)}</span>
            <span class="tag-count">${l.count}</span>
        </div>
    `).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const lora = el.dataset.lora;
            const filterState = libraryData.filterState || AppState.filters;
            const currentLoras = filterState.loras || [];
            if (!currentLoras.includes(lora)) {
                filterState.loras = [...currentLoras, lora];
                if (!libraryData.returnFilterOptions) {
                    updateFilterSummary();
                    loadImages();
                }
                finishTagsLibraryInteraction();
                showToast(appT('library.addedFilter', 'Added "{value}" to filters').replace('{value}', lora), 'success');
            }
        });
    });
}

// v3.3.0 FEAT-CHECKPOINT-TAB: render the Checkpoints library tab (mirrors LoRAs).
function renderLibraryCheckpoints(checkpoints) {
    const content = $('#library-content');
    content.style.flexDirection = 'row';
    if (!checkpoints || checkpoints.length === 0) {
        content.innerHTML = '<p class="empty-state-text" style="width:100%;text-align:center;padding:32px;color:var(--text-muted)">' + escapeHtml(appT('library.checkpointsEmpty', 'No checkpoint info yet. Import images with checkpoint info first.')) + '</p>';
        return;
    }
    const addHint = escapeHtml(appT('library.clickToAddFilter', 'Click to add as filter'));
    content.innerHTML = checkpoints.map(c => {
        const value = c.checkpoint_normalized || c.checkpoint || '';
        const display = c.checkpoint || value;
        return `
        <div class="library-tag" data-checkpoint="${escapeHtml(value)}" title="${addHint}">
            <span class="tag-name">${escapeHtml(display)}</span>
            <span class="tag-count">${c.count}</span>
        </div>`;
    }).join('');

    content.querySelectorAll('.library-tag').forEach(el => {
        el.addEventListener('click', () => {
            const checkpoint = normalizeCheckpointFilterValue
                ? normalizeCheckpointFilterValue(el.dataset.checkpoint)
                : el.dataset.checkpoint;
            const filterState = libraryData.filterState || AppState.filters;
            const currentCheckpoints = filterState.checkpoints || [];
            if (checkpoint && !currentCheckpoints.includes(checkpoint)) {
                filterState.checkpoints = [...currentCheckpoints, checkpoint];
                if (!libraryData.returnFilterOptions) {
                    updateFilterSummary();
                    loadImages();
                }
                finishTagsLibraryInteraction();
                showToast(appT('library.addedFilter', 'Added "{value}" to filters').replace('{value}', el.dataset.checkpoint), 'success');
            }
        });
    });
}

function isLibrarySearchContextCurrent(context) {
    return context.requestId === libraryData.searchRequestId
        && context.tab === libraryData.currentTab
        && context.query === ($('#library-search')?.value || '')
        && context.sortBy === ($('#library-sort')?.value || 'frequency')
        && context.optionData === libraryData.optionData;
}

const runLibrarySearch = debounce(async (context) => {
    if (!isLibrarySearchContextCurrent(context)) return;

    try {
        const result = await fetchLibraryFacet(context.tab, {
            sortBy: context.sortBy,
            query: context.query,
            optionData: context.optionData,
        });
        if (!isLibrarySearchContextCurrent(context)) return;
        renderLibraryFacet(context.tab, result.items);
        setLibraryStatsText(context.tab, result.items.length, result.total);
    } catch (error) {
        if (!isLibrarySearchContextCurrent(context)) return;
        renderLibraryLoadError(context.tab, error);
        Logger.error('Library search error:', error);
    }
}, 200);

function filterLibraryContent() {
    runLibrarySearch({
        requestId: ++libraryData.searchRequestId,
        query: $('#library-search')?.value || '',
        tab: libraryData.currentTab,
        sortBy: $('#library-sort')?.value || 'frequency',
        optionData: libraryData.optionData,
    });
}

// ============== Modal Tag/Prompt Autocomplete ==============

// searchModalTags and searchModalPrompts are defined in the Filter Modal section below
// (single definition, using API facet searches instead of pre-limited local caches)

// renderModalActiveTags and renderModalActivePrompts are defined in the Filter Modal section below

