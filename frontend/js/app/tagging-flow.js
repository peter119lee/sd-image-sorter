/**
 * app/tagging-flow.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 5094-5794 (of 10,152): shared scan/tag progress state + tagging start/poll/resume.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
// ============== Tagging ==============

let _tagProgressTimer = null;
let _tagPollingActive = false;
// Guards the window between a startTagging() click and the backend ack.
// _tagPollingActive only flips true AFTER the await, so without this a
// double-click would fire two concurrent start requests.
let _tagStartInFlight = false;
let _tagMinimizedToBackground = false;
// v3.4.1 AI job queue: true while our gallery-tag start sits in the unified
// pipeline queue (backend returned {"status":"queued"}). _tagQueuedSince
// timestamps the enqueue so a stale pipeline_queue.last_start_error from an
// older run is never mistaken for ours.
let _tagQueuedWaiting = false;
let _tagQueuedSince = 0;
let _tagLastProgressPercent = 0;
let _tagLastProgressText = '';
let _tagLastCurrent = 0;
let _tagLastTotal = 0;
let _scanProgressTracker = createProgressTracker();
let _scanBackgroundProgressTracker = createProgressTracker();
let _scanLastProgress = null;
let _scanLastLogPath = '';
let _scanLastLogPathRedacted = '';
const SCAN_DIAGNOSTICS_HOLD_MS = 10000;
let _scanDiagnosticsHoldUntil = 0;
let _scanLibraryReadyHandled = false;
let _scanLastAutoRefreshAt = 0;
// v3.5.0 audit: on tiny libraries library_ready fires <1s after the start
// toast — two stacked info toasts read as spam. Track when the start toast
// was shown so the ready toast can be skipped if it would stack.
let _scanStartToastAt = 0;
// v3.4.3: folder of the most recent scan, for the "Create collection" CTA —
// lets users keep each imported dataset separated without manual selection.
let _scanLastFolderPath = '';
// Each scan poller owns one backend run identity. Overlapping response timing
// cannot cancel another run's consumer; a changed identity is re-dispatched by
// source and duplicate pollers for the same run are coalesced.
const _scanPollersByIdentity = new Map();
let _reconnectPollTimer = null;
let _tagProgressTracker = createProgressTracker();
let _tagProgressMonitorDegraded = false;
const TAG_PROGRESS_RETRY_LIMIT = 3;
const TAG_PROGRESS_RETRY_DELAY_MS = 1000;
const TAG_PROGRESS_DEGRADED_POLL_MS = 5000;

function clearTagProgressTimer() {
    if (_tagProgressTimer) {
        clearTimeout(_tagProgressTimer);
        _tagProgressTimer = null;
    }
}

function scheduleTagProgressPoll(delay = 500, retryCount = 0) {
    clearTagProgressTimer();
    _tagProgressTimer = setTimeout(() => pollTagProgress(retryCount), delay);
}

function resetTagUiProgressState() {
    _tagMinimizedToBackground = false;
    _tagProgressMonitorDegraded = false;
    _tagLastProgressPercent = 0;
    _tagLastProgressText = '';
    _tagLastCurrent = 0;
    _tagLastTotal = 0;
    resetProgressTracker(_tagProgressTracker);
}

function minimizeTaggingToBackground() {
    if (!_tagPollingActive) {
        hideModal('tag-modal');
        _hideBgTagProgress();
        return;
    }

    _tagMinimizedToBackground = true;
    hideModal('tag-modal');
    _updateBgTagProgress(
        _tagLastProgressPercent,
        _tagLastProgressText || appT('tagger.progressPreparing', 'Preparing tagger...'),
        'running'
    );
    showToast(appT('tagger.minimizedToBackground', 'Tagging continues in the background. Use the progress bar to stop or check details.'), 'info');
}

const TAG_CANCELLATION_TERMINAL_STATUSES = new Set([
    'idle',
    'done',
    'cancelled',
    'error',
    'queue_cleared',
]);

function parseTagCancellationResponse(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('Tag cancel response must be an object.');
    }

    if (typeof value.status !== 'string' || !value.status.trim()) {
        throw new TypeError('Tag cancel response field "status" must be a non-empty string.');
    }
    if (!Number.isInteger(value.removed_queued) || value.removed_queued < 0) {
        throw new TypeError('Tag cancel response field "removed_queued" must be a non-negative integer.');
    }
    if (typeof value.message !== 'string') {
        throw new TypeError('Tag cancel response field "message" must be a string.');
    }

    return {
        status: value.status.trim(),
        removedQueued: value.removed_queued,
        message: value.message,
    };
}

function isQueuedTagCancellationComplete(result) {
    return result.removedQueued > 0 && TAG_CANCELLATION_TERMINAL_STATUSES.has(result.status);
}

function finishCancelledTagging(message) {
    window.__liveTagProgress = null;
    _tagPollingActive = false;
    _tagStartInFlight = false;
    _tagQueuedWaiting = false;
    _tagQueuedSince = 0;
    clearTagProgressTimer();
    _hideBgTagProgress();
    showToast(message || appT('tagger.progressCancelled', 'Tagging cancelled'), 'info');
    $('#tag-progress-container').style.display = 'none';
    unlockLiveProgressText('#tag-progress-text', 'modal.tagLoadingModel', 'Loading model...');
    setTaggingUiState(false);
    resetTagUiProgressState();
    syncTaggerModelUi();
}

async function requestStopTagging() {
    if (!_tagPollingActive) {
        hideModal('tag-modal');
        _hideBgTagProgress();
        return;
    }

    try {
        const cancelResult = parseTagCancellationResponse(await API.cancelTagging());
        if (isQueuedTagCancellationComplete(cancelResult)) {
            finishCancelledTagging(appT('tagger.progressCancelled', 'Tagging cancelled'));
            return;
        }
        _tagMinimizedToBackground = true;
        _updateBgTagProgress(
            _tagLastProgressPercent,
            appT('tagger.progressCancelling', 'Cancelling... {current}/{total}')
                .replace('{current}', String(_tagLastCurrent))
                .replace('{total}', String(_tagLastTotal)),
            'cancelling'
        );
        showToast(appT('tagger.cancellingAfterCurrent', 'Cancelling after current image...'), 'info');
    } catch (err) {
        showToast(formatUserError(err, 'Failed to cancel'), 'error');
    }
}

function setTaggingUiState(isRunning) {
    const startBtn = $('#btn-start-tag');
    const cancelBtn = $('#btn-cancel-tag');
    const modelSelect = $('#tag-model-select');
    const thresholdInput = $('#tag-threshold');
    const characterThresholdInput = $('#tag-character-threshold');
    const retagAll = $('#tag-retag-all');
    const useGpu = $('#tag-use-gpu');
    const customProfile = $('#tag-custom-profile-select');
    const modelPath = $('#tag-model-path');
    const tagsPath = $('#tag-tags-path');
    const exportBtn = $('#btn-export-tags-json');
    const importBtn = $('#btn-import-tags');

    if (startBtn) {
        if (isRunning) {
            startBtn.dataset.i18nLocked = '1';
        } else {
            delete startBtn.dataset.i18nLocked;
        }
        startBtn.setAttribute('data-i18n', isRunning ? 'tag.running' : 'modal.tagStart');
        startBtn.disabled = isRunning;
        startBtn.textContent = isRunning
            ? appT('tag.running', 'Tagging...')
            : appT('modal.tagStart', 'Start Tagging');
    }

    if (cancelBtn) {
        if (isRunning) {
            cancelBtn.dataset.i18nLocked = '1';
        } else {
            delete cancelBtn.dataset.i18nLocked;
        }
        cancelBtn.setAttribute('data-i18n', isRunning ? 'tagger.runInBackground' : 'modal.tagCancel');
        cancelBtn.disabled = false;
        cancelBtn.textContent = isRunning
            ? appT('tagger.runInBackground', 'Run in Background')
            : appT('modal.tagCancel', 'Cancel');
    }

    [modelSelect, thresholdInput, characterThresholdInput, retagAll, useGpu, customProfile, modelPath, tagsPath, exportBtn, importBtn].forEach((element) => {
        if (element) {
            element.disabled = isRunning;
        }
    });

    if (!isRunning) {
        syncTaggerModelUi({ applyModelDefaults: false });
    }
}

async function loadTaggerModels() {
    const select = $('#tag-model-select');
    if (!select) return;

    try {
        const result = await API.getTaggerModels();
        // Captioner-only entries (ToriiGate) never belong in the gallery
        // tagger dropdown — measured unusable as a tagger; caption via
        // Smart Tag's natural-language stage instead.
        const models = (Array.isArray(result.models) ? result.models : [])
            .filter((model) => !model?.captioner_only);
        const defaultModel = normalizeTaggerModelName(result.default, 'wd-swinv2-tagger-v3');
        const currentValue = normalizeTaggerModelName(select.value, defaultModel);
        _taggerModelCatalog = models;
        _taggerModelCatalogMap = new Map(
            models
                .filter((model) => model?.name)
                .map((model) => [normalizeTaggerModelName(model.name, defaultModel), model])
        );

        const options = models.map((model) => {
            const name = model.name || model.path || 'unknown-model';
            const bestFor = model.best_for ? ` - ${model.best_for}` : '';
            const recommended = model.recommended ? ' (Recommended)' : '';
            const disabled = model.disabled ? ' (Unavailable)' : '';
            const disabledAttr = model.disabled ? ' disabled aria-disabled="true"' : '';
            const title = model.disabled && model.disabled_reason
                ? `${name}${bestFor} - ${model.disabled_reason}`
                : `${name}${bestFor}`;
            return `<option value="${escapeHtml(name)}" title="${escapeHtml(title)}"${disabledAttr}>${escapeHtml(name)}${recommended}${disabled}</option>`;
        });
        options.push('<option value="custom">Custom Local Model...</option>');
        // v3.2.1: add VLM (Natural Language) backend as a primary tagger choice
        const _vg = window.I18n?.t?.('modal.tagGroupVlm');
        const vlmGroupLabel = (_vg && _vg !== 'modal.tagGroupVlm') ? _vg : 'VLM Captioning (Natural Language)';
        const _vm = window.I18n?.t?.('modal.tagModelVlm');
        const vlmOptionLabel = (_vm && _vm !== 'modal.tagModelVlm') ? _vm : '🧠 VLM (Cloud API or Local Ollama)';
        options.push(
            `<optgroup label="${escapeHtml(vlmGroupLabel)}">` +
            `<option value="vlm">${escapeHtml(vlmOptionLabel)}</option>` +
            '</optgroup>'
        );

        select.innerHTML = options.join('');
        const savedDefaults = AppPreferences.getTaggerDefaults();
        const savedModel = getAvailableTaggerOptionValue(savedDefaults?.modelName);
        const currentModel = getAvailableTaggerOptionValue(currentValue);
        const fallbackModel = getAvailableTaggerOptionValue(defaultModel) || select.querySelector('option:not([disabled])')?.value || defaultModel;
        select.value = savedModel || currentModel || fallbackModel;
        _suppressTaggerPreferencePersistence = true;
        try {
            select.dispatchEvent(new Event('change'));
            applyStoredTaggerDefaults({ defaults: savedDefaults });
        } finally {
            _suppressTaggerPreferencePersistence = false;
        }
        syncSettingsPreferenceStatus();
    } catch (error) {
        Logger.warn('Failed to load tagger models list:', error);
        syncTaggerModelUi({ applyModelDefaults: false });
    }
}

async function exportTagLibraryJson() {
    try {
        const data = await API.exportAllTags();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        a.href = url;
        a.download = `sd-image-sorter-tags-${stamp}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(appT('tag.tagsExported', 'Tags exported'), 'success');
    } catch (error) {
        showToast(formatUserError(error, appT('tag.exportFailed', 'Failed to export tags')), 'error');
    }
}

async function startTagging() {
    const t = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
    // In-flight guard: block a second start request while the first is still
    // awaiting the backend ack (before _tagPollingActive flips true).
    if (_tagStartInFlight || _tagPollingActive) {
        return;
    }
    if (!hasLoadedTaggerSystemInfo() && typeof loadSystemInfo === 'function') {
        await loadSystemInfo();
    }
    const threshold = parseFloat($('#tag-threshold')?.value) || 0.35;
    const characterThreshold = parseFloat($('#tag-character-threshold')?.value) || 0.85;
    const modelSelectRaw = $('#tag-model-select')?.value || '';
    const modelSelect = normalizeTaggerModelName(
        modelSelectRaw,
        'wd-swinv2-tagger-v3'
    );
    const isCustomModel = modelSelectRaw === 'custom';
    const modelMeta = getTaggerModelMeta(modelSelect);
    if (!isCustomModel && modelMeta?.disabled) {
        showToast(modelMeta.disabled_reason || appT('tag.modelUnavailable', 'This tagger model is not available in the current build.'), 'warning');
        return;
    }
    const useGpuCheckbox = $('#tag-use-gpu');
    const gpuLocked = isGpuLockedTaggerModel(modelSelect, { isCustom: isCustomModel });

    const options = {
        threshold,
        characterThreshold,
        allowUnsafeAcceleration: false
    };

    // Handle custom model
    if (isCustomModel) {
        const modelPath = $('#tag-model-path')?.value?.trim() || '';
        const tagsPath = $('#tag-tags-path')?.value?.trim() || '';
        const customProfile = getCustomTaggerProfile();

        if (!modelPath) {
            showToast(appT('tag.modelPathRequired', 'Please enter a model path'), 'error');
            return;
        }

        options.modelPath = modelPath;
        if (tagsPath) {
            options.tagsPath = tagsPath;
        }
        options.customProfile = customProfile;
        options.modelName = customProfile;
    } else {
        options.modelName = modelSelect;
    }

    options.retagAll = $('#tag-retag-all').checked;
    options.useGpu = useGpuCheckbox?.checked ?? true;

    // v3.2.2 T-power-PR1: pre-tag filters from the optional <details>.
    // Blacklist is split on commas + newlines, trimmed, deduped.
    const blacklistRaw = (document.getElementById('tag-pre-blacklist')?.value || '').trim();
    if (blacklistRaw) {
        const seen = new Set();
        const blacklist = [];
        for (const token of blacklistRaw.split(/[,\n]+/)) {
            const t = token.trim();
            if (!t) continue;
            const k = t.toLowerCase();
            if (seen.has(k)) continue;
            seen.add(k);
            blacklist.push(t);
        }
        if (blacklist.length) options.preTagBlacklist = blacklist;
    }
    const maxTagsRaw = (document.getElementById('tag-max-tags-per-image')?.value || '').trim();
    if (maxTagsRaw) {
        const n = parseInt(maxTagsRaw, 10);
        if (Number.isFinite(n) && n > 0) options.maxTagsPerImage = n;
    }

    // Advanced runtime chunk size now maps to the backend's true WD14 batch size
    // where the selected model supports dynamic batching.
    const batchSelect = document.getElementById('tagger-batch-size');
    const effectiveModelForBatch = getEffectiveTaggerModelForUi(modelSelect, { isCustom: isCustomModel });
    if (isToriiGateTaggerModel(effectiveModelForBatch, { isCustom: false })) {
        options.batchSize = 1;
    } else if (batchSelect?.dataset.userChosen === '1') {
        const recommendedBatchSize = getRecommendedTaggerChunkSize(effectiveModelForBatch, {
            isCustom: isCustomModel && effectiveModelForBatch === 'custom',
            useGpu: options.useGpu,
        });
        options.batchSize = Math.min(128, parseInt(batchSelect.value, 10) || recommendedBatchSize);
    }

    if (gpuLocked) {
        options.useGpu = false;
        options.allowUnsafeAcceleration = false;
        if (useGpuCheckbox) {
            useGpuCheckbox.checked = false;
        }
        syncTaggerModelUi({ applyModelDefaults: false });
    }

    persistTaggerDefaultsFromDom();

    // Aurora Phase 3: the batch action bar's Tag button scopes this run to the
    // Gallery selection (explicit id selections only; token selections keep
    // the whole-library semantics of this modal).
    const scopedTagIds = window.GalleryToolbar?.consumeTagSelectionIds?.();
    if (scopedTagIds && scopedTagIds.length) {
        options.imageIds = scopedTagIds;
    }

    try {
        _tagStartInFlight = true;
        const startResp = await API.startTagging(options);
        // v3.4.1 AI job queue: another AI job is running, so the backend
        // queued this one (200 + status:"queued") instead of failing with
        // 409. The normal poll loop below renders the queued state and
        // hands over to the running state automatically.
        const isQueued = !!(startResp && startResp.status === 'queued' && startResp.pipeline_queued === true);
        if (isQueued) {
            _tagQueuedWaiting = true;
            _tagQueuedSince = Date.now();
            showToast(startResp.duplicate
                ? appT('aiQueue.duplicateToast', 'An identical job is already queued')
                : appT('aiQueue.queuedToast', 'Queued — starts automatically after the current AI job finishes'), 'info');
        } else {
            _tagQueuedWaiting = false;
        }

        // Scoped run accepted — disarm so the NEXT run isn't silently scoped.
        if (scopedTagIds && scopedTagIds.length) {
            window.GalleryToolbar?.disarmTagSelection?.();
        }

        _tagPollingActive = true;
        _tagStartInFlight = false;
        resetTagUiProgressState();
        clearTagProgressTimer();

        $('#tag-progress-container').style.display = 'block';
        lockLiveProgressText('#tag-progress-text');
        $('#tag-progress-fill').style.width = '0%';
        _tagLastProgressPercent = 0;
        if (isQueued) {
            _tagLastProgressText = appT('aiQueue.queuedProgress', 'Queued #{position} — waiting for the current AI job to finish')
                .replace('{position}', String(startResp.queue_position || 1));
        } else {
            _tagLastProgressText = gpuLocked
                ? t('tag.preparingMaxQuality', 'Preparing Max Quality model on CPU...')
                : (options.useGpu
                    ? t('tag.preparingGpu', 'Preparing model on GPU...')
                    : t('tag.preparingCpu', 'Preparing model on CPU...'));
        }
        $('#tag-progress-text').textContent = _tagLastProgressText;
        setTaggingUiState(true);

        pollTagProgress();
    } catch (error) {
        _tagPollingActive = false;
        _tagStartInFlight = false;
        clearTagProgressTimer();
        showToast(formatUserError(error, appT('tag.startFailed', 'Failed to start tagging')), 'error');
    }
}

async function pollTagProgress(retryCount = 0) {
    if (!_tagPollingActive) return;

    try {
        const progress = await API.getTagProgress();
        // A queue-only cancellation can finish while this request is in flight.
        // Do not let that stale response restore queued state or re-arm a timer.
        if (!_tagPollingActive) return;
        _tagProgressMonitorDegraded = false;
        window.__liveTagProgress = progress;
        syncTaggerModelUi();

        // v3.4.1 AI job queue: while our start sits in the pipeline queue the
        // legacy status is still idle/done from a previous run. Render the
        // queued state and keep polling; when the dispatcher starts the job
        // the status flips to running and the normal flow takes over.
        const _queueInfo = progress.pipeline_queue || null;
        const _queuedEntries = (_queueInfo && Array.isArray(_queueInfo.queued)) ? _queueInfo.queued : [];
        if (!['running', 'cancelling'].includes(progress.status)) {
            if (_queuedEntries.length > 0) {
                _tagQueuedWaiting = true;
                const queuedText = appT('aiQueue.queuedProgress', 'Queued #{position} — waiting for the current AI job to finish')
                    .replace('{position}', String(_queuedEntries[0].position || 1));
                const queuedFill = $('#tag-progress-fill');
                if (queuedFill) {
                    queuedFill.classList.add('is-indeterminate');
                    queuedFill.style.width = '';
                }
                $('#tag-progress-text').textContent = queuedText;
                _tagLastProgressText = queuedText;
                _updateBgTagProgress(0, queuedText, 'running');
                scheduleTagProgressPoll(1000);
                return;
            }
            if (_tagQueuedWaiting) {
                // Our queued entry left the queue. Either it just started
                // (status flips running on a later poll) or it failed to
                // start — the coordinator records that per kind and clears
                // it again on the next successful start.
                _tagQueuedWaiting = false;
                const startError = _queueInfo && _queueInfo.last_start_error;
                const startErrorAt = startError ? Date.parse(startError.at || '') : NaN;
                if (startError && Number.isFinite(startErrorAt) && startErrorAt >= (_tagQueuedSince - 2000)) {
                    window.__liveTagProgress = null;
                    _tagPollingActive = false;
                    clearTagProgressTimer();
                    _hideBgTagProgress();
                    showToast(appT('aiQueue.startFailed', 'Queued job failed to start: {error}')
                        .replace('{error}', String(startError.error || '')), 'error');
                    $('#tag-progress-container').style.display = 'none';
                    unlockLiveProgressText('#tag-progress-text', 'modal.tagLoadingModel', 'Loading model...');
                    setTaggingUiState(false);
                    resetTagUiProgressState();
                    syncTaggerModelUi();
                    return;
                }
            }
        } else {
            _tagQueuedWaiting = false;
        }

        // UI-03: Improved progress display with ETA
        const current = (progress.processed ?? progress.current ?? 0);
        const total = progress.total || 0;
        const percent = total > 0 ? (current / total) * 100 : 0;
        _tagLastCurrent = current;
        _tagLastTotal = total;
        const tagged = Number(progress.tagged || 0);
        const errors = Number(progress.errors || 0);

        const fillEl = $('#tag-progress-fill');
        // No real percent yet means we are still importing modules / downloading the
        // VLM / loading the ONNX session. Switch the bar to an indeterminate "still
        // working" animation so users can see activity instead of a stuck 0%.
        const isIndeterminate = progress.status === 'running' && (total === 0 || current === 0);
        if (fillEl) {
            fillEl.classList.toggle('is-indeterminate', isIndeterminate);
            fillEl.style.width = isIndeterminate ? '' : (percent + '%');
        }

        const remaining = total > 0 ? Math.max(0, total - current) : 0;
        const extraParts = [];
        if (total > 0) extraParts.push(appT('progress.left', '{count} left').replace('{count}', remaining));
        if (tagged > 0) extraParts.push(appT('progress.taggedCount', '{count} tagged').replace('{count}', tagged));
        if (errors > 0) extraParts.push(appT('progress.failedCount', '{count} failed').replace('{count}', errors));

        let progressText = buildOperationProgressText({
            completed: current,
            total,
            tracker: _tagProgressTracker,
            primaryLabel: appT('tagger.progressLabel', 'Tagging'),
            extraParts,
            detail: progress.current_item || progress.message || appT('tagger.progressPreparing', 'Preparing tagger...'),
            defaultMessage: appT('tagger.progressPreparing', 'Preparing tagger...'),
        });

        if (progress.status === 'cancelling') {
            progressText = progress.message || appT('tagger.progressCancelling', 'Cancelling... {current}/{total}')
                .replace('{current}', current)
                .replace('{total}', Math.max(total, current));
        }

        $('#tag-progress-text').textContent = progressText;
        _tagLastProgressPercent = percent;
        _tagLastProgressText = progressText;

        // Update background progress bar (always, even if modal is closed)
        _updateBgTagProgress(percent, progressText, progress.status);

        if (progress.status === 'done') {
            window.__liveTagProgress = null;
            _tagPollingActive = false;
            clearTagProgressTimer();
            _hideBgTagProgress();
            _showCompletionFlash();
            // FLOW-06: persistent next-step CTA in place of the success toast.
            const _taggedCount = Number(progress.completed ?? progress.processed ?? 0);
            if (errors > 0) {
                showToast(progress.message, 'warning');
            } else {
                showPipelineNextStep({
                    icon: 'i-tag',
                    title: _taggedCount > 0
                        ? appT('flow.tagDoneTitle', 'Tagged {count} images — what next?').replace('{count}', String(_taggedCount))
                        : appT('flow.tagDoneTitleZero', 'Tagging complete — what next?'),
                    actions: [
                        { icon: '🗂️', label: appT('nav.sorting', 'Organize'), action: 'view:sorting' },
                        { icon: '📦', label: appT('nav.dataset', 'Dataset'), action: 'view:dataset' },
                    ],
                });
            }
            hideModal('tag-modal');
            $('#tag-progress-container').style.display = 'none';
            unlockLiveProgressText('#tag-progress-text', 'modal.tagLoadingModel', 'Loading model...');
            setTaggingUiState(false);
            resetTagUiProgressState();
            syncTaggerModelUi();
            loadImages();
            loadStats();
            // v3.2.2 T-power-PR2 (H): pop the post-tag stats modal once
            // when the worker emits last_run_stats on the terminal send.
            if (progress.last_run_stats && typeof window.TagStatsModal === 'object'
                && typeof window.TagStatsModal.show === 'function') {
                try { window.TagStatsModal.show(progress.last_run_stats); } catch (_e) { /* */ }
            }
            // v3.2.2 T-power-PR3 (G): two-layer completion notification
            // (favicon+title blink always; Notification API if user opted in).
            if (typeof window.TagCompleteNotify === 'object'
                && typeof window.TagCompleteNotify.fireOnDone === 'function') {
                try {
                    window.TagCompleteNotify.fireOnDone(
                        progress.message || 'Tagging complete',
                        errors > 0 ? 'warning' : 'success',
                    );
                } catch (_e) { /* */ }
            }
            // v3.2.1: dispatch a hookable event so other modules (gallery
            // sub-views, prompt-lab, etc.) can react to fresh tags without
            // needing to know about the polling internals here.
            try {
                document.dispatchEvent(new CustomEvent('taggingCompleted', {
                    detail: {
                        completed: progress?.completed || 0,
                        errors: errors || 0,
                        message: progress?.message || '',
                    },
                }));
            } catch (_e) {
                /* event dispatch is best-effort */
            }
        } else if (progress.status === 'cancelled') {
            finishCancelledTagging(progress.message || appT('tagger.progressCancelled', 'Tagging cancelled'));
        } else if (progress.status === 'running') {
            scheduleTagProgressPoll(500);
        } else if (progress.status === 'cancelling') {
            scheduleTagProgressPoll(300);
        } else if (progress.status === 'error') {
            window.__liveTagProgress = null;
            _tagPollingActive = false;
            clearTagProgressTimer();
            _hideBgTagProgress();
            showToast(progress.message, 'error');
            $('#tag-progress-container').style.display = 'none';
            unlockLiveProgressText('#tag-progress-text', 'modal.tagLoadingModel', 'Loading model...');
            setTaggingUiState(false);
            resetTagUiProgressState();
            syncTaggerModelUi();
        } else {
            scheduleTagProgressPoll(500);
        }
    } catch (error) {
        if (!_tagPollingActive) return;
        // Progress transport failure does not prove that the backend job ended.
        // Keep the running UI locked and continue observing at a lower cadence.
        if (retryCount < TAG_PROGRESS_RETRY_LIMIT) {
            Logger.warn('Tag progress poll failed, retrying:', error);
            scheduleTagProgressPoll(TAG_PROGRESS_RETRY_DELAY_MS, retryCount + 1);
            return;
        }

        Logger.warn('Tag progress monitoring is temporarily unavailable:', error);
        const monitorText = appT(
            'tagger.progressMonitorUnavailable',
            'Tagging status is temporarily unavailable. The task may still be queued or running; monitoring will keep trying.'
        );
        if (!_tagProgressMonitorDegraded) {
            _tagProgressMonitorDegraded = true;
            showToast(monitorText, 'warning');
        }
        $('#tag-progress-text').textContent = monitorText;
        _tagLastProgressText = monitorText;
        const lastKnownStatus = window.__liveTagProgress?.status;
        const monitorStatus = lastKnownStatus === 'cancelling'
            ? 'cancelling'
            : (_tagQueuedWaiting ? 'queued' : 'running');
        _updateBgTagProgress(_tagLastProgressPercent, monitorText, monitorStatus);
        scheduleTagProgressPoll(TAG_PROGRESS_DEGRADED_POLL_MS, retryCount + 1);
    }
}

// ============== Background Tagging Progress Bar ==============

function _updateBgTagProgress(percent, text, status) {
    const bar = $('#bg-tag-progress');
    if (!bar) return;
    if (!_tagPollingActive || ['idle', 'done', 'cancelled', 'error'].includes(status)) {
        bar.style.display = 'none';
        return;
    }
    const tagModal = $('#tag-modal');
    const modalOpen = tagModal && tagModal.classList.contains('visible');
    const shouldShow = _tagMinimizedToBackground || !modalOpen;
    bar.style.display = shouldShow ? 'flex' : 'none';
    const fill = $('#bg-tag-progress-fill');
    const textEl = $('#bg-tag-progress-text');
    const isIndeterminate = ['running', 'cancelling', 'queued'].includes(status) && (!percent || percent === 0);
    if (fill) {
        fill.classList.toggle('is-indeterminate', isIndeterminate);
        fill.style.width = isIndeterminate ? '' : (percent + '%');
    }
    if (textEl) textEl.textContent = text;
}

function _hideBgTagProgress() {
    const bar = $('#bg-tag-progress');
    if (bar) bar.style.display = 'none';
}

function _showCompletionFlash() {
    const flash = document.createElement('div');
    flash.style.cssText = 'position:fixed;inset:0;background:rgba(var(--success-rgb), 0.08);pointer-events:none;z-index:9999;';
    document.body.appendChild(flash);
    setTimeout(() => flash.remove(), 700);
}

function _initBgTagProgressButtons() {
    const cancelBtn = $('#bg-tag-cancel');
    const openBtn = $('#bg-tag-open');
    if (cancelBtn) {
        // Use onclick (single handler) instead of addEventListener: this
        // initializer can run more than once, and stacking listeners would
        // fire multiple stop-tagging requests on a single click.
        cancelBtn.onclick = async () => {
            await requestStopTagging();
        };
    }
    if (openBtn) {
        openBtn.onclick = () => {
            _tagMinimizedToBackground = false;
            showModal('tag-modal');
        };
    }
}

async function resumeTaggingProgress() {
    try {
        const progress = await API.getTagProgress();
        // v3.4.1 AI job queue: an F5 while our start is still queued must
        // re-attach the poller too, otherwise the queued job would start
        // later with no visible progress and no way to cancel it.
        const queuedEntries = progress?.pipeline_queue?.queued || [];
        const isLive = ['running', 'cancelling'].includes(progress?.status);
        if (!isLive && queuedEntries.length === 0) {
            _hideBgTagProgress();
            return;
        }
        if (!isLive && queuedEntries.length > 0) {
            _tagQueuedWaiting = true;
            _tagQueuedSince = Date.now();
        }

        _tagPollingActive = true;
        _tagMinimizedToBackground = !($('#tag-modal')?.classList.contains('visible'));
        clearTagProgressTimer();
        $('#tag-progress-container').style.display = 'block';
        lockLiveProgressText('#tag-progress-text');
        _tagLastProgressPercent = 0;
        _tagLastProgressText = progress.message || appT('tagger.progressResuming', 'Resuming tagging progress...');
        $('#tag-progress-text').textContent = _tagLastProgressText;
        setTaggingUiState(true);
        // Show background progress bar (tag modal may not be open)
        const current = progress.processed || progress.current || 0;
        const total = progress.total || 0;
        const percent = total > 0 ? (current / total) * 100 : 0;
        _tagLastProgressPercent = percent;
        _updateBgTagProgress(percent, _tagLastProgressText, progress.status);
        pollTagProgress();
    } catch (error) {
        Logger.warn('Failed to resume tagging progress:', error);
    }
}

