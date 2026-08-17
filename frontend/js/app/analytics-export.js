/**
 * app/analytics-export.js — app.js decomposition, stage 2 (leaf sections).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js,
 * pre-split lines 10234-10996. Classic script: shares ONE global lexical
 * environment with app.js and the other app/ parts; index.html loads
 * every app/ file BEFORE app.js, so these top-level declarations stay
 * visible there. No behavior change intended.
 */
// AbortController for confirm modal to prevent listener accumulation
let _confirmAbort = null;

function showConfirm(title, message, onOk, onCancel) {
    lockDynamicI18nText('#confirm-title', 'modal.confirm');
    lockDynamicI18nText('#confirm-message', 'modal.confirmAction');
    $('#confirm-title').textContent = title || appT('modal.confirm', 'Are you sure?');
    $('#confirm-message').textContent = message || appT('modal.confirmAction', 'This action cannot be undone.');

    // Abort previous confirm listeners
    if (_confirmAbort) _confirmAbort.abort();
    _confirmAbort = new AbortController();
    const signal = _confirmAbort.signal;

    const okBtn = $('#btn-confirm-ok');
    okBtn.addEventListener('click', () => {
        hideModal('confirm-modal');
        if (onOk) onOk();
    }, { signal });

    // Handle cancel callback if provided
    const cancelBtn = $('#btn-confirm-cancel');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            hideModal('confirm-modal');
            if (onCancel) onCancel();
        }, { signal });
    }

    showModal('confirm-modal');
}

function showRandomImage() {
    if (AppState.images.length === 0) {
        showToast(appT('gallery.noImagesAvailable', 'No images available'), 'info');
        return;
    }

    const randomIndex = Math.floor(Math.random() * AppState.images.length);
    const randomImage = AppState.images[randomIndex];

    if (window.Gallery) {
        Gallery.openPreview(randomImage.id);
    }
}

async function showAnalytics() {
    try {
        // Stats are already updated via loadStats regularly, but we can refresh
        await loadStats();
        const data = AppState.analytics;

        $('#analytics-checkpoints').innerHTML = data.checkpoints.length ?
            data.checkpoints.map(c => `
                <div class="analytics-item clickable" data-type="checkpoint" data-value="${escapeHtml(getCheckpointOptionValue(c))}">
                    <span class="item-name">${escapeHtml(c.checkpoint || getCheckpointOptionValue(c))}</span>
                    <span class="item-count">${c.count}</span>
                </div>
            `).join('') : `<p>${escapeHtml(appT('analytics.noCheckpoints', 'No checkpoints found'))}</p>`;

        $('#analytics-loras').innerHTML = data.loras.length ?
            data.loras.map(l => `
                <div class="analytics-item clickable" data-type="lora" data-value="${escapeHtml(l.lora)}">
                    <span class="item-name">${escapeHtml(l.lora)}</span>
                    <span class="item-count">${l.count}</span>
                </div>
            `).join('') : `<p>${escapeHtml(appT('analytics.noLoras', 'No LoRAs found'))}</p>`;

        $('#analytics-tags').innerHTML = data.top_tags.length ?
            data.top_tags.map(t => `
                <div class="analytics-item clickable" data-type="tag" data-value="${escapeHtml(t.tag)}">
                    <span class="item-name">${escapeHtml(t.tag)}</span>
                    <span class="item-count">${t.count}</span>
                </div>
            `).join('') : `<p>${escapeHtml(appT('analytics.noTags', 'No tags found'))}</p>`;

        // Add click handlers to all analytics items
        $$('#analytics-modal .analytics-item.clickable').forEach(el => {
            el.addEventListener('click', () => {
                const type = el.dataset.type;
                const value = el.dataset.value;
                applyAnalyticsFilter(type, value);
            });
        });

        showModal('analytics-modal');
    } catch (e) {
        showToast(formatUserError(e, appT('analytics.loadFailed', 'Failed to load analytics')), 'error');
    }
}

function applyAnalyticsFilter(type, value) {
    if (type === 'checkpoint') {
        updateAppFilters((filters) => {
            filters.checkpoints = [value];
        });
        updateModelSelectionSummaries();
    } else if (type === 'lora') {
        updateAppFilters((filters) => {
            filters.loras = [value];
        });
        updateModelSelectionSummaries();
    } else if (type === 'tag') {
        if (!AppState.filters.tags.includes(value)) {
            updateAppFilters((filters) => {
                filters.tags = [...filters.tags, value];
            });
            renderActiveTagFilters();
        }
    }
    hideModal('analytics-modal');
    loadImages();
    showToast(appT('filter.appliedValue', 'Filter applied: {value}', { value }), 'success');
}


let _currentExportModalData = null;
let _currentExportFormat = 'prompt';

function getExportFormatLabel(format) {
    const labels = {
        prompt: appT('export.formatPrompt', 'Prompt text'),
        prompt_numbered: appT('export.formatPromptNumbered', 'Prompt text + filenames'),
        negative: appT('export.formatNegative', 'Negative prompt'),
        prompt_negative: appT('export.formatPromptNegative', 'Prompt + Negative'),
        a1111: appT('export.formatA1111', 'A1111 / Forge block'),
        tags: appT('export.formatTags', 'Tags list'),
        caption_tags: appT('export.formatCaptionTags', 'Caption + Tags lines'),
        caption_merged: appT('export.formatCaptionMerged', 'Merged caption lines'),
        jsonl: appT('export.formatJsonl', 'JSONL'),
        csv: appT('export.formatCsv', 'CSV table'),
    };
    return labels[format] || labels.prompt;
}

function getExportFormatDescription(format) {
    const descriptions = {
        prompt: appT('export.descPrompt', 'One .txt: each image Prompt is separated by a blank line.'),
        prompt_numbered: appT('export.descPromptNumbered', 'One .txt: filename title + Prompt for each image.'),
        negative: appT('export.descNegative', 'One .txt: Negative prompt only, separated by blank lines.'),
        prompt_negative: appT('export.descPromptNegative', 'One .txt: Prompt plus Negative prompt for each image.'),
        a1111: appT('export.descA1111', 'One .txt: WebUI/A1111-style parameter blocks for regeneration.'),
        tags: appT('export.descTags', 'One .txt: merged unique Tags from all selected images.'),
        caption_tags: appT('export.descCaptionTags', 'One .txt: one AI caption + Tags line per image.'),
        caption_merged: appT('export.descCaptionMerged', 'One .txt: one merged caption line per image, built from AI caption, Prompt, and Tags.'),
        jsonl: appT('export.descJsonl', 'One .jsonl: one JSON object per image for scripts and dataset tools.'),
        csv: appT('export.descCsv', 'One .csv table: filename, Prompt, Tags, model, and size columns.'),
    };
    return descriptions[format] || descriptions.prompt;
}

function updateExportModalTitle(format) {
    // Target the label span, not the <h3>: the heading also holds the sprite icon.
    const title = $('#export-title-text');
    if (!title) return;
    title.dataset.i18nLocked = '1';
    title.textContent = getExportFormatLabel(format);
}

function getBatchExportContentDescription(mode) {
    const descriptions = {
        caption_merged: appT('batchExport.descCaptionMerged', 'Writes one same-name .txt per image for LoRA training: optional Class Token + AI caption + Prompt + Tags, merged into one line.'),
        prompt: appT('batchExport.descPrompt', 'Writes one same-name .txt per image containing only Prompt text.'),
        tags: appT('batchExport.descTags', 'Writes one same-name .txt per image containing only Tags. The Class Token field is ignored.'),
        negative: appT('batchExport.descNegative', 'Writes one same-name .txt per image containing only Negative prompt.'),
        prompt_negative: appT('batchExport.descPromptNegative', 'Writes one same-name .txt per image with Prompt plus Negative prompt.'),
        a1111: appT('batchExport.descA1111', 'Writes one same-name .txt per image in A1111 / Forge parameter-block format for regeneration.'),
        caption_tags: appT('batchExport.descCaptionTags', 'Writes one same-name .txt per image with optional Class Token + AI caption + Tags, without the original Prompt.'),
        tags_nl: appT('batchExport.descTagsNl', 'Writes one same-name .txt per image with optional Class Token + Tags + Natural Language caption, without the original Prompt.'),
        nl_caption: appT('batchExport.descNlCaption', 'Writes one same-name .txt per image containing only the Natural Language caption.'),
        prompt_nl: appT('batchExport.descPromptNl', 'Writes one same-name .txt per image with Prompt plus Natural Language caption.'),
        json: appT('batchExport.descJson', 'Writes one same-name .json per image with Prompt, Tags, model, size, and generation parameters.'),
    };
    return descriptions[mode] || descriptions.caption_merged;
}

function updateExportFormatDescription(format) {
    const description = $('#export-format-description');
    if (description) {
        description.textContent = getExportFormatDescription(format || $('#export-format')?.value || _currentExportFormat || 'prompt');
    }
}

function updateBatchExportContentDescription(mode) {
    const description = $('#batch-export-content-description');
    if (description) {
        description.textContent = getBatchExportContentDescription(mode || $('#batch-export-content-mode')?.value || 'caption_merged');
    }
}

function normalizeExportTextPart(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
}

function uniqueExportParts(parts) {
    const seen = new Set();
    const output = [];
    parts.forEach((part) => {
        const value = normalizeExportTextPart(part).replace(/^,+|,+$/g, '').trim();
        if (!value) return;
        const key = value.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        output.push(value);
    });
    return output;
}

function buildExportGenerationParams(image = {}) {
    const params = image.generation_params && typeof image.generation_params === 'object'
        ? { ...image.generation_params }
        : {};
    if (!params.model && image.checkpoint) params.model = image.checkpoint;
    if (!params.size && image.width && image.height) params.size = `${image.width}x${image.height}`;
    return params;
}

function buildA1111ExportBlock(image = {}) {
    const prompt = String(image.prompt || '').trim();
    const negative = String(image.negative_prompt || '').trim();
    const params = buildExportGenerationParams(image);
    const lines = [];
    if (prompt) lines.push(prompt);
    if (negative) lines.push(`Negative prompt: ${negative}`);

    const order = [
        ['steps', 'Steps'],
        ['sampler', 'Sampler'],
        ['schedule_type', 'Schedule type'],
        ['cfg_scale', 'CFG scale'],
        ['seed', 'Seed'],
        ['size', 'Size'],
        ['model', 'Model'],
        ['model_hash', 'Model hash'],
        ['clip_skip', 'Clip skip'],
        ['denoising_strength', 'Denoising strength'],
        ['loras', 'LoRAs'],
    ];
    const emitted = new Set();
    const parts = [];
    order.forEach(([key, label]) => {
        const value = params[key];
        if (value == null || value === '') return;
        emitted.add(key);
        parts.push(`${label}: ${value}`);
    });
    Object.keys(params).sort().forEach((key) => {
        if (emitted.has(key)) return;
        const value = params[key];
        if (value == null || value === '') return;
        const label = key.split('_').map(part => part ? part.charAt(0).toUpperCase() + part.slice(1) : part).join(' ');
        parts.push(`${label}: ${value}`);
    });
    if (parts.length) lines.push(parts.join(', '));
    return lines.join('\n').trim();
}

function buildExportRecord(image = {}) {
    return {
        id: image.id,
        filename: image.filename || '',
        generator: image.generator || null,
        prompt: image.prompt || '',
        negative_prompt: image.negative_prompt || '',
        ai_caption: image.ai_caption || '',
        tags: Array.isArray(image.tags) ? image.tags : [],
        checkpoint: image.checkpoint || null,
        width: image.width || null,
        height: image.height || null,
        aesthetic_score: image.aesthetic_score ?? null,
        generation_params: buildExportGenerationParams(image),
    };
}

function escapeCsvField(value) {
    const text = value == null ? '' : String(value);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function buildExportText(exportData, format) {
    const images = Array.isArray(exportData?.images) ? exportData.images : [];
    let text = '';

    if (format === 'prompt') {
        text = images.map(image => String(image.prompt || '').trim()).filter(Boolean).join('\n\n');
    } else if (format === 'prompt_numbered') {
        text = images.map((image, index) => {
            const prompt = String(image.prompt || '').trim();
            if (!prompt) return '';
            const filename = String(image.filename || `Image ${image.id || index + 1}`).trim();
            return `${index + 1}. ${filename}\n${prompt}`;
        }).filter(Boolean).join('\n\n');
    } else if (format === 'negative') {
        text = images.map(image => String(image.negative_prompt || '').trim()).filter(Boolean).join('\n\n');
    } else if (format === 'prompt_negative') {
        text = images.map((image) => {
            const prompt = String(image.prompt || '').trim();
            const negative = String(image.negative_prompt || '').trim();
            return [prompt, negative ? `Negative prompt: ${negative}` : ''].filter(Boolean).join('\n');
        }).filter(Boolean).join('\n\n');
    } else if (format === 'a1111') {
        text = images.map(buildA1111ExportBlock).filter(Boolean).join('\n\n');
    } else if (format === 'tags') {
        const allTags = new Set();
        images.forEach((image) => (image.tags || []).forEach(tag => allTags.add(tag)));
        text = Array.from(allTags).sort().join(', ');
    } else if (format === 'caption_tags') {
        text = images.map((image) => uniqueExportParts([image.ai_caption, ...(image.tags || [])]).join(', ')).filter(Boolean).join('\n');
    } else if (format === 'caption_merged') {
        text = images.map((image) => uniqueExportParts([image.ai_caption, image.prompt, ...(image.tags || [])]).join(', ')).filter(Boolean).join('\n');
    } else if (format === 'jsonl') {
        text = images.map(image => JSON.stringify(buildExportRecord(image))).join('\n');
    } else if (format === 'csv') {
        const header = ['id', 'filename', 'generator', 'prompt', 'negative_prompt', 'ai_caption', 'tags', 'checkpoint', 'width', 'height'];
        const rows = images.map((image) => {
            const record = buildExportRecord(image);
            const values = [
                record.id,
                record.filename,
                record.generator,
                record.prompt,
                record.negative_prompt,
                record.ai_caption,
                record.tags.join(', '),
                record.checkpoint,
                record.width,
                record.height,
            ];
            return values.map(escapeCsvField).join(',');
        });
        text = [header.join(','), ...rows].join('\n');
    }

    if (!text) {
        text = appT('export.noDataForFormat', 'No exportable data for this format in the selected preview.');
    }

    const totalSelected = Number(exportData?.total || images.length);
    const previewWindowSize = Number(exportData?.preview_count ?? exportData?.count ?? images.length);
    const previewCount = Math.min(totalSelected, previewWindowSize);
    const previewOnly = Boolean(exportData?.has_more) || totalSelected > previewCount;

    if (text.length > EXPORT_PREVIEW_MAX_CHARS) {
        text = `${text.slice(0, EXPORT_PREVIEW_MAX_CHARS)}\n\n${appT('export.previewTextTruncated', '[Preview truncated to keep the app responsive]')}`;
    }
    if (previewOnly) {
        text = `${text}\n\n${appT(
            'export.previewLimited',
            'Preview only shows the first {preview} of {total} selected images. Use "Same-name .txt" when you need one complete caption file per image.',
            { preview: previewCount, total: totalSelected }
        )}`;
    }
    return text;
}

function setExportModalMode(mode) {
    const exportAltBtn = $('#btn-export-tags-alt');
    if (!exportAltBtn) return;

    const normalizedMode = mode === 'tags' ? 'tags' : 'prompts';
    exportAltBtn.dataset.exportView = normalizedMode;
    exportAltBtn.innerHTML = normalizedMode === 'prompts'
        ? `🏷️ ${appT('export.tagsInstead', 'Show Tags')}`
        : `📤 ${appT('export.promptsInstead', 'Show Prompt Text')}`;
}

function renderExportModalText(format = null) {
    const selectedFormat = format || $('#export-format')?.value || _currentExportFormat || 'prompt';
    _currentExportFormat = selectedFormat;
    const select = $('#export-format');
    if (select && select.value !== selectedFormat) select.value = selectedFormat;

    updateExportModalTitle(selectedFormat);
    setExportModalMode(selectedFormat === 'tags' ? 'tags' : 'prompts');
    updateExportFormatDescription(selectedFormat);

    const textArea = $('#export-text');
    if (!textArea || !_currentExportModalData) return;
    textArea.value = buildExportText(_currentExportModalData, selectedFormat);
}

async function showExportModalWithFormat(format = 'prompt') {
    const selectedCount = getSelectedGalleryCount();
    if (selectedCount === 0) return;

    _currentExportModalData = null;
    _currentExportFormat = format;
    $('#export-count').textContent = appT('export.selectedCount', 'This export includes only {count} selected images.', {
        count: selectedCount,
    });
    const select = $('#export-format');
    if (select) select.value = format;
    setExportModalMode(format === 'tags' ? 'tags' : 'prompts');
    updateExportModalTitle(format);
    updateExportFormatDescription(format);
    const textArea = $('#export-text');
    textArea.value = format === 'tags'
        ? appT('export.loadingTags', 'Loading tags...')
        : appT('export.loadingPrompts', 'Loading prompts...');

    showModal('export-modal');

    try {
        const ids = getSelectedGalleryIds();
        _currentExportModalData = await loadSelectionPreviewData(ids, EXPORT_PREVIEW_MAX_IMAGES);
        renderExportModalText(format);
    } catch (e) {
        textArea.value = appT('export.errorLoadingData', 'Error loading export data: {message}', {
            message: e.message,
        });
    }
}

async function showExportModal() {
    return showExportModalWithFormat('prompt');
}

async function showExportTagsModal() {
    return showExportModalWithFormat('tags');
}

function getExportFileExtension(format) {
    if (format === 'jsonl') return 'jsonl';
    if (format === 'csv') return 'csv';
    return 'txt';
}

function downloadCurrentExportText() {
    const text = $('#export-text')?.value || '';
    const format = $('#export-format')?.value || _currentExportFormat || 'prompt';
    const extension = getExportFileExtension(format);
    const filename = `sd-image-sorter-${format}-${new Date().toISOString().slice(0, 10)}.${extension}`;
    const blob = new Blob([text], { type: extension === 'csv' ? 'text/csv;charset=utf-8' : 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}


function showBatchExportModal() {
    const selectedCount = getSelectedGalleryCount();
    if (selectedCount === 0) {
        showToast(appT('export.selectImagesFirst', 'Please select images first'), 'error');
        return;
    }

    $('#batch-export-count').textContent = appT('batchExport.selectedCount', 'This batch export includes only {count} selected images.', {
        count: selectedCount,
    });
    const contentModeSelect = $('#batch-export-content-mode');
    if (contentModeSelect && !contentModeSelect.value) {
        contentModeSelect.value = 'caption_merged';
    }
    updateBatchExportContentDescription(contentModeSelect?.value || 'caption_merged');
    syncBatchExportOutputModeUi();
    $('#batch-export-progress').style.display = 'none';
    $('#btn-start-batch-export').disabled = false;
    showModal('batch-export-modal');
}

function syncBatchExportOutputModeUi() {
    // The "Save next to each image" option ignores Output Folder, so disable
    // the input + helper text when that mode is selected. Disabled inputs
    // visually reinforce that the field is not used and skip required-field
    // validation when the user clicks Export.
    const folderRadio = document.querySelector('input[name="batch-export-output-mode"][value="folder"]');
    const folderGroup = $('#batch-export-folder-group');
    const folderInput = $('#batch-export-folder');
    const isFolderMode = !!(folderRadio && folderRadio.checked);
    if (folderInput) {
        folderInput.disabled = !isFolderMode;
    }
    if (folderGroup) {
        folderGroup.classList.toggle('is-disabled', !isFolderMode);
    }
}

function getExportDataCacheKey(imageIds) {
    return imageIds.map((id) => String(id)).join(',');
}

function getTokenExportDataCacheKey(selectionToken, offset, limit) {
    return `token:${selectionToken}:${offset}:${limit}`;
}

function getActiveSelectionExportToken() {
    if (AppState.selectionScope !== 'filtered' || !AppState.selectionToken) {
        return null;
    }
    if (isFilteredSelectionTokenRefreshPending()) {
        return null;
    }
    if (getSelectedGalleryCount() === 0) {
        return null;
    }
    if (AppState.selectionFilterKey !== getSelectionFilterCacheKey(AppState.filters)) {
        return null;
    }
    return AppState.selectionToken;
}

function resetSelectionDataCache() {
    AppState.selectionDataCache = {
        key: null,
        data: null
    };
}

function buildSelectionDataPayload(imageIds, data) {
    const normalizedIds = imageIds
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value) && value > 0);
    const requestedIdSet = new Set(normalizedIds);
    const cachedImages = new Map(
        (AppState.images || [])
            .filter((image) => requestedIdSet.has(Number(image?.id)))
            .map((image) => [Number(image.id), image])
    );
    const fetchedImages = new Map(
        (Array.isArray(data?.images) ? data.images : [])
            .map((image) => [Number(image.id), image])
    );
    const images = [];
    const resolvedIds = new Set();

    normalizedIds.forEach((id) => {
        const cached = cachedImages.get(id) || null;
        const fetched = fetchedImages.get(id) || null;
        if (!cached && !fetched) {
            return;
        }

        images.push({
            ...(cached || {}),
            ...(fetched || {}),
            id,
            prompt: fetched?.prompt ?? cached?.prompt ?? '',
            tags: Array.isArray(fetched?.tags)
                ? fetched.tags
                : (Array.isArray(cached?.tags) ? cached.tags : []),
        });
        resolvedIds.add(id);
    });

    const missingFromApi = Array.isArray(data?.missing_ids)
        ? data.missing_ids
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value > 0)
        : [];
    const missingIds = Array.from(new Set([
        ...missingFromApi,
        ...normalizedIds.filter((id) => !resolvedIds.has(id)),
    ]));

    return { images, missing_ids: missingIds };
}

async function loadSelectionData(imageIds) {
    const cacheKey = getExportDataCacheKey(imageIds);
    if (AppState.selectionDataCache.key === cacheKey && AppState.selectionDataCache.data) {
        return AppState.selectionDataCache.data;
    }

    const data = buildSelectionDataPayload(imageIds, await API.getSelectionData(imageIds));
    AppState.selectionDataCache = {
        key: cacheKey,
        data
    };
    return data;
}

async function loadSelectionDataByToken(selectionToken, { offset = 0, limit = EXPORT_PREVIEW_MAX_IMAGES } = {}) {
    const normalizedOffset = Math.max(0, Number(offset) || 0);
    const normalizedLimit = Math.max(1, Math.min(Number(limit) || EXPORT_PREVIEW_MAX_IMAGES, 10000));
    const cacheKey = getTokenExportDataCacheKey(selectionToken, normalizedOffset, normalizedLimit);
    if (AppState.selectionDataCache.key === cacheKey && AppState.selectionDataCache.data) {
        return AppState.selectionDataCache.data;
    }

    const response = await API.getSelectionDataByToken(selectionToken, {
        offset: normalizedOffset,
        limit: normalizedLimit,
    });
    const responseIds = Array.isArray(response?.images)
        ? response.images.map((image) => Number(image?.id)).filter((id) => Number.isFinite(id) && id > 0)
        : [];
    const data = {
        ...buildSelectionDataPayload(responseIds, response),
        count: Number(response?.count ?? responseIds.length),
        preview_count: Number(response?.count ?? responseIds.length),
        total: Number(response?.total ?? responseIds.length),
        offset: Number(response?.offset ?? normalizedOffset),
        limit: Number(response?.limit ?? normalizedLimit),
        next_offset: response?.next_offset ?? null,
        has_more: Boolean(response?.has_more),
        source: response?.source || 'selection_token',
        exact_total: response?.exact_total !== false,
    };
    AppState.selectionDataCache = {
        key: cacheKey,
        data
    };
    return data;
}

async function loadSelectionPreviewData(ids, limit = EXPORT_PREVIEW_MAX_IMAGES) {
    const selectionToken = getActiveSelectionExportToken();
    if (selectionToken) {
        return loadSelectionDataByToken(selectionToken, { offset: 0, limit });
    }

    const previewIds = ids.slice(0, limit);
    const data = await loadSelectionData(previewIds);
    return {
        ...data,
        count: data.images.length,
        preview_count: previewIds.length,
        total: ids.length,
        offset: 0,
        limit: previewIds.length,
        has_more: ids.length > previewIds.length,
        source: 'image_ids',
        exact_total: true,
    };
}


async function executeBatchExport() {
    const outputModeRadio = document.querySelector('input[name="batch-export-output-mode"]:checked');
    const outputMode = outputModeRadio?.value === 'folder' ? 'folder' : 'beside_image';
    const outputFolder = $('#batch-export-folder')?.value?.trim() || '';
    if (outputMode === 'folder' && !outputFolder) {
        showToast(appT('export.outputFolderRequired', 'Please enter an output folder'), 'error');
        return;
    }

    const prefix = $('#batch-export-prefix')?.value || '';
    const blacklistText = $('#batch-export-blacklist')?.value || '';
    const blacklist = blacklistText ? blacklistText.split(',').map(t => t.trim()).filter(t => t) : [];
    const contentMode = $('#batch-export-content-mode')?.value || 'caption_merged';
    const overwritePolicy = $('#batch-export-overwrite')?.value || 'unique';

    const selectionToken = getActiveSelectionTokenForActions();
    const imageIds = selectionToken ? [] : getSelectedGalleryIds();

    // Show progress
    const progressEl = $('#batch-export-progress');
    const progressFill = $('#batch-export-progress-fill');
    const progressText = $('#batch-export-progress-text');
    const startBtn = $('#btn-start-batch-export');
    if (progressEl) progressEl.style.display = 'block';
    if (progressFill) progressFill.style.width = '0%';
    if (progressText) progressText.textContent = appT('export.inProgress', 'Exporting...');
    if (startBtn) startBtn.disabled = true;

    try {
        const templateOptions = contentMode === 'template' && window.V321Integration?.collectTemplateOptions
            ? window.V321Integration.collectTemplateOptions()
            : null;
        const imageOverrides = window.V321Integration?.collectEditedCaptionOverrides
            ? window.V321Integration.collectEditedCaptionOverrides()
            : null;
        const captionTransforms = window.V321Integration?.collectCaptionTransforms
            ? window.V321Integration.collectCaptionTransforms()
            : null;
        // Aurora #25c: per-image caption types + edited NL sentences, collected
        // explicitly here (replacing the old window.fetch monkey-patch) so both
        // export paths below carry them in the payload.
        const imageTypes = window.V321Integration?.collectCaptionTypes
            ? window.V321Integration.collectCaptionTypes()
            : null;
        const imageNlOverrides = window.V321Integration?.collectNlOverrides
            ? window.V321Integration.collectNlOverrides()
            : null;
        const normalizeTagUnderscores = $('#batch-export-normalize-underscores')
            ? !!$('#batch-export-normalize-underscores').checked
            : undefined;
        // P0-3 split export: only tag-only modes may carry the NL twin (the
        // backend 400s otherwise), so the checkbox is ignored for NL modes.
        const nlSidecar = ($('#batch-export-nl-sidecar')?.checked === true)
            && (contentMode === 'tags' || contentMode === 'template');
        // P2-19 / P2-18: training-purpose filter + implication dedup, same
        // controls the live preview reads so the export matches what was shown.
        const trainingPurpose = $('#batch-export-training-purpose')?.value || '';
        const dedupeImplications = $('#batch-export-dedupe-implications')?.checked === true;
        const exportOptions = {
            selectionToken,
            outputMode,
            templateOptions,
            imageOverrides,
            captionTransforms,
            imageTypes,
            imageNlOverrides,
            normalizeTagUnderscores,
            nlSidecar,
            trainingPurpose,
            dedupeImplications,
        };
        let result;
        if (shouldUseBulkJob(selectionToken, imageIds.length)) {
            // Debt-22 durable path: per-image progress + real mid-run cancel.
            const envelope = await API.startExportBulkJob(imageIds, outputFolder, blacklist, prefix, contentMode, overwritePolicy, exportOptions);
            const job = await pollBulkJobUntilDone(envelope, 'export', {
                show: _showBatchExportCancel,
                update: _updateBatchExportJobProgress,
                hide: _hideBatchExportCancel,
            });
            result = (job && job.result) ? job.result : (job || {});
        } else {
            await API.startExportJob(imageIds, outputFolder, blacklist, prefix, contentMode, overwritePolicy, exportOptions);
            // v3.3.2 Phase-1: poll the background job instead of blocking the
            // request. The terminal payload embeds the export result under
            // `result`, so the mapping below is unchanged.
            const finalProgress = await pollExportProgressUntilDone();
            result = (finalProgress && finalProgress.result) ? finalProgress.result : (finalProgress || {});
        }

        $('#batch-export-progress-fill').style.width = '100%';

        const exported = Number(result?.exported || 0);
        const skipped = Number(result?.skipped || 0);
        const errorCount = Number(result?.error_count ?? result?.errors ?? 0);
        const errorMessages = Array.isArray(result?.error_messages) ? result.error_messages : [];

        if ((result.status === 'ok' || errorCount === 0) && exported > 0 && skipped === 0) {
            showToast(appT('export.success', 'Exported {count} tag files successfully.', {
                count: exported,
            }), 'success');
            hideModal('batch-export-modal');
        } else if (result.status === 'partial' || exported > 0 || skipped > 0) {
            const baseMessage = exported > 0
                ? appT('batchExport.partialSuccess', 'Exported {count} file(s). {failed} failed.')
                    .replace('{count}', exported)
                    .replace('{failed}', errorCount)
                : appT('batchExport.noFilesWritten', 'No .txt / .json files were written.');
            const skippedMessage = skipped > 0
                ? ` ${appT('batchExport.skippedExisting', 'Skipped {skipped} existing file(s).').replace('{skipped}', skipped)}`
                : '';
            showToast(`${baseMessage}${skippedMessage}`.trim(), errorCount > 0 || skipped > 0 ? 'warning' : 'success');
            hideModal('batch-export-modal');
        } else {
            showToast(appT('export.failedReason', 'Export failed: {reason}', {
                reason: errorMessages.join(', ') || appT('common.unknownError', 'Unknown error'),
            }), 'error');
        }

        _showExportValidationWarnings(result?.validation);
    } catch (e) {
        showToast(formatUserError(e, appT('export.failed', 'Export failed')), "error");
    } finally {
        $('#batch-export-progress').style.display = 'none';
        $('#btn-start-batch-export').disabled = false;
    }
}

// Trainer-consumability report from the backend export validator: pairing,
// single-line, trigger presence, rating consistency, emptiness. Only speaks
// up when something is actually wrong with the written caption files.
function _showExportValidationWarnings(validation) {
    const warnings = Array.isArray(validation?.warnings) ? validation.warnings : [];
    if (!warnings.length) return;
    const parts = warnings.map((w) => {
        const label = appT(`exportValidation.${w.code}`, w.message || w.code);
        const example = Array.isArray(w.examples) && w.examples.length ? ` (${w.examples[0]}…)` : '';
        return `${label} ×${w.count}${example}`;
    });
    showToast(
        `${appT('exportValidation.title', 'Training-data check')}: ${parts.join(' · ')}`,
        'warning',
        { duration: 12000 },
    );
}

