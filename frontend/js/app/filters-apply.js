/**
 * app/filters-apply.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 6976-7443 (of 10,152): filter apply/clear, tag chips, filter modal open/render.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
// ============== Filters ==============

function updateFiltersFromUI() {
    // Get generators
    const generators = [];
    $$('#modal-generator-filters input[type="checkbox"]:checked').forEach(cb => {
        generators.push(cb.value);
    });
    updateAppFilters((filters) => {
        filters.generators = generators;
    });

    // Get ratings
    const ratings = [];
    $$('#modal-rating-filters input[type="checkbox"]:checked').forEach(cb => {
        ratings.push(cb.value);
    });
    updateAppFilters((filters) => {
        filters.ratings = ratings;
    });
}

function applyFilters() {
    updateFiltersFromUI();
    loadImages();
}

function clearFilters() {
    $$('#modal-generator-filters input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    $$('#modal-rating-filters input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    updateAppFilters((filters) => {
        filters.generators = [...ALL_GENERATORS];
        filters.ratings = ['general', 'sensitive', 'questionable', 'explicit'];
        filters.tags = [];
        filters.search = '';
    });
    const freeTextSearch = $('#modal-free-text-search');
    if (freeTextSearch) freeTextSearch.value = '';
    const activeTags = $('#active-tags');
    if (activeTags) activeTags.innerHTML = '';
    loadImages();
}

function addTagFilter(tag) {
    if (!AppState.filters.tags.includes(tag)) {
        updateAppFilters((filters) => {
            filters.tags = [...filters.tags, tag];
        });
        renderActiveTagFilters();
    }
}

function applyTagFiltersFromExternal(tags, options = {}) {
    const cleanTags = Array.from(new Set((tags || [])
        .map((tag) => String(tag || '').trim())
        .filter(Boolean)));
    if (cleanTags.length === 0) {
        showToast(appT('tagCategory.noneFound', 'No tags found for that category.'), 'warning');
        return false;
    }

    const replaceTags = options.replaceTags === true;
    const nextMode = options.tagMode === 'or' ? 'or' : 'and';
    updateAppFilters((filters) => {
        const currentTags = Array.isArray(filters.tags) ? filters.tags : [];
        filters.tags = replaceTags
            ? cleanTags
            : Array.from(new Set([...currentTags, ...cleanTags]));
        filters.tagMode = nextMode;
        filters.cursor = null;
        filters.offset = 0;
    });
    renderActiveTagFilters();
    updateFilterSummary();
    switchView('gallery');
    loadImages();

    const label = String(options.label || '').trim();
    const message = label
        ? appT('tagCategory.findAppliedNamed', 'Showing images matching {category} tags', { category: label }).replace('{category}', label)
        : appT('tagCategory.findApplied', 'Showing images matching those tags');
    showToast(message, 'success');
    return true;
}

function removeTagFilter(tag) {
    updateAppFilters((filters) => {
        filters.tags = filters.tags.filter(t => t !== tag);
    });
    renderActiveTagFilters();
}

function renderActiveTagFilters() {
    const container = $('#active-tags');
    if (!container) return;
    container.innerHTML = '';

    AppState.filters.tags.forEach(tag => {
        const tagEl = document.createElement('span');
        tagEl.className = 'active-tag';
        tagEl.appendChild(document.createTextNode(`${tag} `));

        const removeEl = document.createElement('span');
        removeEl.className = 'remove-tag';
        removeEl.dataset.tag = tag;
        removeEl.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-close\"/></svg>";
        removeEl.addEventListener('click', () => removeTagFilter(tag));

        tagEl.appendChild(removeEl);
        container.appendChild(tagEl);
    });
}

// ============== Unified Filter Modal ==============

/**
 * Map a focusField key (sidebar / chip deep-link) to a selector inside
 * #filter-modal. Used so "模型" / checkpoint chips open the modal already
 * scrolled to the Base model (checkpoint) list — not the generators block.
 */
const FILTER_MODAL_FOCUS_TARGETS = Object.freeze({
    generators: '#modal-generator-filters',
    ratings: '#modal-rating-filters',
    tags: '#modal-tag-search',
    prompts: '#modal-prompt-search',
    search: '#modal-free-text-search',
    checkpoints: '#modal-checkpoint-list',
    // Aliases users / plans use for the same control
    checkpoint: '#modal-checkpoint-list',
    model: '#modal-checkpoint-list',
    'base-model': '#modal-checkpoint-list',
    baseModel: '#modal-checkpoint-list',
    meta: '#modal-checkpoint-list',
    loras: '#modal-lora-list',
    lora: '#modal-lora-list',
    colors: 'input[name="color-hue"]',
    dimensions: '#filter-min-width',
    hasMetadata: 'input[name="has-metadata"]',
    metadata: 'input[name="has-metadata"]',
    aesthetic: '#filter-aesthetic-min',
});

/**
 * Scroll/highlight a filter-modal section after open. Safe no-op when the
 * field is unknown or the modal is closed.
 */
function focusFilterModalField(focusField) {
    if (!focusField) return;
    const key = String(focusField).trim();
    const selector = FILTER_MODAL_FOCUS_TARGETS[key] || FILTER_MODAL_FOCUS_TARGETS[key.toLowerCase()];
    if (!selector) return;
    const modal = document.getElementById('filter-modal');
    if (!modal) return;
    const target = modal.querySelector(selector);
    if (!target) return;
    const section = target.closest('.filter-section, .filter-panel') || target;
    try {
        section.scrollIntoView({ block: 'center', behavior: 'smooth' });
    } catch (_e) {
        section.scrollIntoView();
    }
    section.classList.add('filter-focus-target');
    section.setAttribute('data-filter-focused', '1');
    if (typeof target.focus === 'function') {
        try { target.focus({ preventScroll: true }); } catch (_e) { /* non-focusable ok */ }
    }
    window.setTimeout(() => {
        section.classList.remove('filter-focus-target');
        // Keep a durable marker for tests / a11y until the next open.
    }, 1800);
}

async function openFilterModal(options = {}) {
    const targetState = options.filterState || AppState.filters;
    FilterModalController.mode = options.mode || 'gallery';
    FilterModalController.targetState = targetState;
    FilterModalController.workingState = cloneFilterState(targetState);
    FilterModalController.onApply = typeof options.onApply === 'function' ? options.onApply : null;
    FilterModalController.onReset = typeof options.onReset === 'function' ? options.onReset : null;
    FilterModalController.titleText = options.titleText || null;
    FilterModalController.applyButtonText = options.applyButtonText || null;
    FilterModalController.resetButtonText = options.resetButtonText || null;
    FilterModalController.optionData = options.optionData || null;
    FilterModalController.focusField = options.focusField || null;

    // Show skeleton while loading
    if (window.SkeletonFilterModal) {
        window.SkeletonFilterModal.show('filter-modal');
    }

    // Sync modal state with current AppState
    const filterState = getFilterModalState();
    const titleEl = $('#filter-modal-title');
    if (titleEl && FilterModalController.titleText) {
        titleEl.textContent = FilterModalController.titleText;
    } else if (titleEl) {
        titleEl.textContent = appT('filter.filterImages', 'Filter Images');
    }
    const applyButton = $('#btn-apply-modal-filters');
    const resetButton = $('#btn-reset-filters');
    if (applyButton) {
        applyButton.textContent = FilterModalController.applyButtonText || appT('filter.apply', 'Apply Filters');
    }
    if (resetButton) {
        resetButton.textContent = FilterModalController.resetButtonText || appT('filter.reset', 'Reset All');
    }
    // v3.5.0 audit: the footer note used to always talk about "gallery
    // results" even when the modal was opened from Manual Sort / Auto-Separate.
    const footerNote = document.querySelector('#filter-modal .filter-modal-footer-note');
    if (footerNote) {
        const footerByMode = {
            'manual-sort': ['filter.footerHintManual', 'Applies only to the images Manual Sort will go through — Gallery filters stay unchanged.'],
            'auto-separate': ['filter.footerHintAutosep', 'Applies only to what Auto-Separate will match — Gallery filters stay unchanged.'],
            'queue-profile': ['filter.footerHintQueue', 'Applies only to the censor queue selection — Gallery filters stay unchanged.'],
            'queue-solitaire': ['filter.footerHintQueue', 'Applies only to the censor queue selection — Gallery filters stay unchanged.'],
        };
        const footerEntry = footerByMode[FilterModalController.mode]
            || ['filter.footerHint', 'Apply filters to refresh the gallery results. Press Enter inside tag or prompt search to add multiple values separated by commas.'];
        footerNote.setAttribute('data-i18n', footerEntry[0]);
        footerNote.textContent = appT(footerEntry[0], footerEntry[1]);
    }
    $$('#modal-generator-filters input').forEach(cb => {
        cb.checked = filterState.generators.includes(cb.value);
    });
    $$('#modal-rating-filters input').forEach(cb => {
        cb.checked = filterState.ratings.includes(cb.value);
    });
    const minWidthInput = $('#filter-min-width');
    const maxWidthInput = $('#filter-max-width');
    const minHeightInput = $('#filter-min-height');
    const maxHeightInput = $('#filter-max-height');
    if (minWidthInput) minWidthInput.value = filterState.minWidth ?? '';
    if (maxWidthInput) maxWidthInput.value = filterState.maxWidth ?? '';
    if (minHeightInput) minHeightInput.value = filterState.minHeight ?? '';
    if (maxHeightInput) maxHeightInput.value = filterState.maxHeight ?? '';
    $$('input[name="aspect-ratio"]').forEach(radio => {
        radio.checked = radio.value === (filterState.aspectRatio || '');
    });
    // v3.3.2 small-opt: "has SD generation parameters" tri-state radios
    $$('input[name="has-metadata"]').forEach(radio => {
        const cur = filterState.hasMetadata;
        const curValue = cur === true ? 'true' : (cur === false ? 'false' : '');
        radio.checked = radio.value === curValue;
    });
    // File-time date range filter
    const dateFromInput = $('#filter-date-from');
    const dateToInput = $('#filter-date-to');
    if (dateFromInput) dateFromInput.value = filterState.dateFrom ?? '';
    if (dateToInput) dateToInput.value = filterState.dateTo ?? '';
    // Aesthetic score filter
    const minAestheticInput = $('#filter-aesthetic-min');
    const maxAestheticInput = $('#filter-aesthetic-max');
    if (minAestheticInput) minAestheticInput.value = filterState.minAesthetic ?? '';
    if (maxAestheticInput) maxAestheticInput.value = filterState.maxAesthetic ?? '';
    const minUserRatingInput = $('#filter-user-rating-min');
    if (minUserRatingInput) minUserRatingInput.value = filterState.minUserRating ?? '';
    const brightnessMinInput = $('#filter-brightness-min');
    const brightnessMaxInput = $('#filter-brightness-max');
    if (brightnessMinInput) brightnessMinInput.value = filterState.brightnessMin ?? '';
    if (brightnessMaxInput) brightnessMaxInput.value = filterState.brightnessMax ?? '';
    // Aurora Phase 3 (24d): saturation range + Unscored aesthetic tier state
    const saturationMinInput = $('#filter-saturation-min');
    const saturationMaxInput = $('#filter-saturation-max');
    if (saturationMinInput) saturationMinInput.value = filterState.minSaturation ?? '';
    if (saturationMaxInput) saturationMaxInput.value = filterState.maxSaturation ?? '';
    // Reset the Apply label to its translatable state on every open; the
    // count preview re-locks it as soon as a fresh count arrives.
    const applyModalButton = $('#btn-apply-modal-filters');
    if (applyModalButton) {
        delete applyModalButton.dataset.i18nLocked;
        applyModalButton.textContent = appT('filter.apply', 'Apply Filters');
    }
    const aestheticQuickGroup = $('.aesthetic-quick-filters');
    if (aestheticQuickGroup) {
        aestheticQuickGroup.dataset.unscored = filterState.aestheticUnscored === true ? '1' : '';
    }
    $$('.aesthetic-quick').forEach(btn => {
        btn.classList.toggle('is-active', btn.dataset.unscored === '1' && filterState.aestheticUnscored === true);
    });
    $$('input[name="color-temperature"]').forEach(radio => {
        radio.checked = radio.value === (filterState.colorTemperature || '');
    });
    $$('input[name="color-hue"]').forEach(cb => {
        cb.checked = (filterState.colorHues || []).includes(cb.value);
    });
    $$('input[name="brightness-distribution"]').forEach(radio => {
        radio.checked = radio.value === (filterState.brightnessDistribution || '');
    });
    $$('input[name="prompt-match-mode"]').forEach(radio => {
        radio.checked = radio.value === normalizePromptMatchMode(filterState.promptMatchMode);
    });
    $$('input[name="tag-match-mode"]').forEach(radio => {
        radio.checked = radio.value === (filterState.tagMode || 'and');
    });
    // Don't prefill prompt search bar with AppState.filters.search —
    // the prompt search is for adding prompt filters, not for text search
    $('#modal-prompt-search').value = '';
    const freeTextSearch = $('#modal-free-text-search');
    if (freeTextSearch) freeTextSearch.value = filterState.search || '';
    const modalTagSearch = $('#modal-tag-search');
    const modalTagSuggestions = $('#modal-tag-suggestions');
    const modalPromptSuggestions = $('#modal-prompt-suggestions');
    if (modalTagSearch) modalTagSearch.value = '';
    if (modalTagSuggestions) {
        modalTagSuggestions.innerHTML = '';
        modalTagSuggestions.classList.remove('visible');
    }
    if (modalPromptSuggestions) {
        modalPromptSuggestions.innerHTML = '';
        modalPromptSuggestions.classList.remove('visible');
    }

    // Show active tags and prompts
    renderModalActiveTags();
    renderModalActivePrompts();

    // FIX 2026-06-12: render the saved filter presets every time the modal
    // opens (renderFilterPresets previously had no caller besides itself).
    renderFilterPresets();

    // Load checkpoints and loras into modal lists
    await loadModalFilterLists();
    updateFilterModalSummary();

    // Hide skeleton after loading
    if (window.SkeletonFilterModal) {
        window.SkeletonFilterModal.hide('filter-modal');
    }

    showModal('filter-modal');

    // Deep-link: sidebar / chip asked for a specific block (e.g. checkpoints).
    // Wait a frame so layout is final after showModal + list render.
    const focusField = options.focusField || FilterModalController.focusField;
    if (focusField) {
        requestAnimationFrame(() => {
            requestAnimationFrame(() => focusFilterModalField(focusField));
        });
    }

    // v3.2.1 task #26: notify modules that the filter modal was just opened so
    // ColorBackfill (and future addons) can refresh their inline banners.
    try { window.dispatchEvent(new CustomEvent('filterModalOpened')); } catch (_e) {}
}

function renderModalActiveTags() {
    const container = $('#modal-active-tags');
    if (!container) return;
    container.innerHTML = '';

    const filterState = getFilterModalState();

    // Render included tags
    filterState.tags.forEach(tag => {
        const tagEl = document.createElement('span');
        tagEl.className = 'active-tag';
        tagEl.title = 'Click to exclude; click again to remove';
        tagEl.appendChild(document.createTextNode(`${tag} `));

        const removeEl = document.createElement('span');
        removeEl.className = 'remove-modal-tag';
        removeEl.dataset.tag = tag;
        removeEl.textContent = '\u00d7';
        removeEl.addEventListener('click', (e) => {
            e.stopPropagation();
            filterState.tags = filterState.tags.filter(t => t !== tag);
            renderModalActiveTags();
        });

        tagEl.addEventListener('click', () => {
            // Cycle: include -> exclude
            filterState.tags = filterState.tags.filter(t => t !== tag);
            if (!filterState.excludeTags) filterState.excludeTags = [];
            if (!filterState.excludeTags.includes(tag)) filterState.excludeTags.push(tag);
            renderModalActiveTags();
        });

        tagEl.appendChild(removeEl);
        container.appendChild(tagEl);
    });

    // Render excluded tags
    (filterState.excludeTags || []).forEach(tag => {
        const tagEl = document.createElement('span');
        tagEl.className = 'active-tag active-tag-exclude';
        tagEl.title = 'Click to remove exclusion';
        tagEl.appendChild(document.createTextNode(`${tag} `));

        const removeEl = document.createElement('span');
        removeEl.className = 'remove-modal-tag';
        removeEl.dataset.tag = tag;
        removeEl.textContent = '\u00d7';
        removeEl.addEventListener('click', (e) => {
            e.stopPropagation();
            filterState.excludeTags = (filterState.excludeTags || []).filter(t => t !== tag);
            renderModalActiveTags();
        });

        tagEl.addEventListener('click', () => {
            // Cycle: exclude -> remove
            filterState.excludeTags = (filterState.excludeTags || []).filter(t => t !== tag);
            renderModalActiveTags();
        });

        tagEl.appendChild(removeEl);
        container.appendChild(tagEl);
    });

    updateFilterModalSummary();
}

function renderModalActivePrompts() {
    let container = document.getElementById('modal-active-prompts');
    if (!container) {
        const promptSearch = document.getElementById('modal-prompt-search');
        if (promptSearch) {
            container = document.createElement('div');
            container.id = 'modal-active-prompts';
            container.className = 'active-tags';
            container.style.marginTop = '8px';
            promptSearch.parentNode.insertBefore(container, promptSearch.nextSibling);
        } else {
            return;
        }
    }

    container.innerHTML = '';
    const filterState = getFilterModalState();
    filterState.prompts.forEach(prompt => {
        const promptEl = document.createElement('span');
        promptEl.className = 'active-tag';
        promptEl.title = appT('filter.clickToExcludeHint', 'Click to exclude; click again to remove');
        promptEl.appendChild(document.createTextNode(`${prompt} `));

        const removeEl = document.createElement('span');
        removeEl.className = 'remove-modal-prompt';
        removeEl.dataset.prompt = prompt;
        removeEl.textContent = '×';
        removeEl.addEventListener('click', (e) => {
            e.stopPropagation();
            filterState.prompts = filterState.prompts.filter(p => p !== prompt);
            renderModalActivePrompts();
        });

        // v3.3.0 FEAT-EXCLUDE-EXTRA: cycle include -> exclude.
        promptEl.addEventListener('click', () => {
            filterState.prompts = filterState.prompts.filter(p => p !== prompt);
            if (!filterState.excludePrompts) filterState.excludePrompts = [];
            if (!filterState.excludePrompts.includes(prompt)) filterState.excludePrompts.push(prompt);
            renderModalActivePrompts();
        });

        promptEl.appendChild(removeEl);
        container.appendChild(promptEl);
    });

    // v3.3.0 FEAT-EXCLUDE-EXTRA: render excluded prompts (cycle exclude -> remove).
    (filterState.excludePrompts || []).forEach(prompt => {
        const promptEl = document.createElement('span');
        promptEl.className = 'active-tag active-tag-exclude';
        promptEl.title = appT('filter.clickToRemoveExclusion', 'Click to remove exclusion');
        promptEl.appendChild(document.createTextNode(`${prompt} `));

        const removeEl = document.createElement('span');
        removeEl.className = 'remove-modal-prompt';
        removeEl.dataset.prompt = prompt;
        removeEl.textContent = '×';
        removeEl.addEventListener('click', (e) => {
            e.stopPropagation();
            filterState.excludePrompts = (filterState.excludePrompts || []).filter(p => p !== prompt);
            renderModalActivePrompts();
        });

        promptEl.addEventListener('click', () => {
            filterState.excludePrompts = (filterState.excludePrompts || []).filter(p => p !== prompt);
            renderModalActivePrompts();
        });

        promptEl.appendChild(removeEl);
        container.appendChild(promptEl);
    });

    updateFilterModalSummary();
}

// ============== Model Manager ==============

function renderFeatureAvailabilityNotice() {
    const summaryEl = $('#model-manager-summary');
    const gridEl = $('#model-manager-grid');
    if (!summaryEl || !gridEl) return;

    let noticeEl = document.getElementById('feature-availability-notice');
    if (!noticeEl) {
        noticeEl = document.createElement('section');
        noticeEl.id = 'feature-availability-notice';
        noticeEl.className = 'feature-availability-notice';
        gridEl.parentElement.insertBefore(noticeEl, gridEl);
    }

    const readyItems = [
        appT('features.ready.gallery', 'Import / scan folders, browse gallery, read SD metadata'),
        appT('features.ready.filters', 'Filter, search, batch select, auto-separate, and WASD manual sort'),
        appT('features.ready.prompts', 'Prompt Helper, tag library, export sidecar .txt / .json files'),
        appT('features.ready.censorManual', 'Manual censor editor tools: brush, pen, eraser, clone, preview/save'),
        appT('features.ready.colorAnalysis', 'Color analysis: dominant colors, brightness, saturation, color temperature'),
        appT('features.ready.loraExport', 'LoRA training export: Caption Editor, template presets, batch export'),
    ];
    const prepareItems = [
        appT('features.prepare.wd14', 'WD14 / ONNX tagging: first use downloads the selected model and shows install progress; Windows GPU runtime is repaired when needed'),
        appT('features.prepare.clip', 'CLIP similarity / duplicate search: first use installs fastembed and downloads the vision+text pair (~580 MB) with progress'),
        appT('features.prepare.aesthetic', 'Aesthetic scoring: first use installs torch + open-clip and downloads CLIP / scoring head files (~1.7 GB; confirm first)'),
        appT('features.prepare.artist', 'Artist ID: first use installs torch / transformers / timm / safetensors / triton and downloads Kaloscope (~2.8 GB; confirm first)'),
        appT('features.prepare.censorAi', 'AI censor detectors: first use of NudeNet installs its runtime and model. Privacy YOLO and SAM3 stay opt-in.'),
        appT('features.prepare.toriigate', 'ToriiGate local captioner: first use installs PyTorch / Transformers and downloads about 9.6 GB BF16 weights after you confirm'),
        appT('features.prepare.florence2', 'Florence-2 Base local captioning: first use installs the local runtime and downloads the pinned model with progress'),
        appT('features.prepare.vlm', 'VLM natural language captioning: requires API keys (OpenAI/Anthropic/Gemini) or local Ollama'),
    ];

    noticeEl.innerHTML = `
        <div class="feature-availability-card is-ready">
            <strong>${escapeHtml(appT('features.readyTitle', 'Ready after first run.bat'))}</strong>
            <ul>${readyItems.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
        </div>
        <div class="feature-availability-card is-prepare">
            <strong>${escapeHtml(appT('features.prepareTitle', 'Downloads on first use'))}</strong>
            <p>${escapeHtml(appT('features.prepareRestartNote', 'If install adds Python packages, restart the app before using that feature. The UI will warn you when this happens.'))}</p>
            <ul>${prepareItems.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
        </div>
    `;
}

