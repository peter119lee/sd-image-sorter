/**
 * Model Manager opener and bulk download flows.
 * Classic script sharing the app global lexical environment.
 */
async function openModelManager(initialTab) {
    // Remove first-run pulse indicator once user has found the button
    const setupBtn = $('#btn-open-model-manager');
    if (setupBtn && setupBtn.classList.contains('setup-pulse')) {
        setupBtn.classList.remove('setup-pulse');
        localStorage.setItem('sd-image-sorter-setup-clicked', '1');
    }
    const summaryEl = $('#model-manager-summary');
    const gridEl = $('#model-manager-grid');
    if (summaryEl) {
        summaryEl.innerHTML = `<div class="model-manager-stat"><strong>${escapeHtml(appT('models.loadingTitle', 'Checking'))}</strong><span>${escapeHtml(appT('models.loadingBody', 'Checking what is ready on this computer...'))}</span></div>`;
    }
    if (gridEl) gridEl.innerHTML = '';
    syncSettingsControls();
    showModal('model-manager-modal');
    // v3.5.0: the modal is tabbed (rule 6). Openers can land on a specific
    // section; the settings gear resets to the first tab.
    if (window.SettingsTabs && typeof window.SettingsTabs.activate === 'function') {
        window.SettingsTabs.activate(typeof initialTab === 'string' ? initialTab : 'general');
    }

    // Disk usage loads independently so a slow model probe doesn't block it.
    loadDiskUsage();

    // Lazily initialize Dataset Audit only when the user expands it. Its data
    // call is heavier than disk usage, so we do not want it to fire on every
    // Setup open.
    bindDatasetAuditLazyInit();

    try {
        const result = await API.getModelStatus();
        renderModelManager(result.models || []);
    } catch (error) {
        if (summaryEl) {
            summaryEl.innerHTML = `<div class="model-manager-stat"><strong>${escapeHtml(appT('models.failedTitle', 'Load failed'))}</strong><span>${escapeHtml(error.message || appT('models.failedBody', 'Could not read local feature status right now.'))}</span></div>`;
        }
    }

    // Wire the "Download all" button. Idempotent — re-binding on each
    // openModelManager() call is fine because the previous handler was
    // removed when the DOM survived (the button is static markup).
    const bulkBtn = $('#btn-bulk-download-models');
    if (bulkBtn && !bulkBtn.dataset.bulkBound) {
        bulkBtn.dataset.bulkBound = '1';
        bulkBtn.addEventListener('click', () => {
            promptBulkDownloadModels().catch((err) => {
                console.error('Bulk download flow failed', err);
                showToast(formatUserError(err, appT('models.bulkFailed', 'Bulk download failed')), 'error');
            });
        });
    }
}

function _formatBulkBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(0)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function _parseModelPrepareStart(payload, requestedModelId) {
    if (typeof requestedModelId !== 'string' || !requestedModelId.trim()) {
        throw new TypeError('requestedModelId must be a non-empty string');
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new TypeError(`Model prepare response for '${requestedModelId}' must be an object`);
    }
    const status = typeof payload.status === 'string' ? payload.status.trim() : '';
    const activeModelId = typeof payload.model_id === 'string' ? payload.model_id.trim() : '';
    if (!status || !activeModelId) {
        throw new TypeError(
            `Model prepare response for '${requestedModelId}' must include status and model_id`,
        );
    }
    return { status, activeModelId };
}

function _modelPrepareConflictMessage(requestedModelId, activeModelId) {
    return appT(
        'models.prepareConflict',
        'Cannot prepare {requested}: {active} is already being prepared. Wait for it to finish, then try again.',
        { requested: requestedModelId, active: activeModelId },
    );
}

function _bulkModelGuidance(item, prepareResult) {
    if (!prepareResult || typeof prepareResult !== 'object' || Array.isArray(prepareResult)) {
        return null;
    }
    const manualSteps = Array.isArray(prepareResult.manual_steps)
        ? prepareResult.manual_steps.filter((step) => typeof step === 'string' && step.trim())
        : [];
    const resultUrl = typeof prepareResult.external_url === 'string'
        ? prepareResult.external_url.trim()
        : '';
    const itemUrl = typeof item?.auth_url === 'string' ? item.auth_url.trim() : '';
    const externalUrl = /^https:\/\//i.test(resultUrl)
        ? resultUrl
        : /^https:\/\//i.test(itemUrl)
            ? itemUrl
            : '';
    if (manualSteps.length === 0 && !externalUrl) {
        return null;
    }
    return {
        provider: typeof prepareResult.provider === 'string' ? prepareResult.provider : '',
        message: typeof prepareResult.message === 'string'
            ? prepareResult.message
            : appT('models.prepareFailed', 'Model setup failed'),
        target_dir: typeof prepareResult.target_dir === 'string' ? prepareResult.target_dir : '',
        external_url: externalUrl,
        manual_steps: manualSteps,
    };
}

function _bulkFailureSummary(failures) {
    return failures
        .map((failure) => `${failure.id}: ${failure.message}`)
        .join('; ');
}

async function promptBulkDownloadModels() {
    let bundle;
    try {
        bundle = await API.getModelBulkBundle();
    } catch (err) {
        showToast(formatUserError(err, appT('models.bulkFetchFailed', 'Could not load the bulk download list. Please restart the app and try again.')), 'error');
        return;
    }

    const rawItems = Array.isArray(bundle?.items) ? bundle.items : [];
    const items = rawItems.filter((item) => item && typeof item.id === 'string' && item.id.trim());
    const pendingItems = items.filter((item) => item.status !== 'ready' && item.download_supported !== false);
    if (pendingItems.length === 0) {
        showToast(items.length
            ? appT('models.bulkAllReady', 'All recommended models are already downloaded.')
            : appT('models.bulkEmpty', 'No models are configured for bulk download.'), 'success');
        return;
    }

    // Older backend responses treated every missing item as recommended. New
    // responses carry default_selected so gated optional models stay off.
    const defaultSelectedIds = new Set(
        pendingItems
            .filter((item) => item.default_selected === true
                || (item.default_selected == null && item.recommended !== false && !item.gated_download && !item.requires_auth))
            .map((item) => item.id),
    );
    const featureLabel = (item) => {
        const labels = {
            tagging: appT('models.bulkFeatureTagging', 'Tagging'),
            censor: appT('models.bulkFeatureCensor', 'Censor'),
            similarity: appT('models.bulkFeatureSimilarity', 'Similarity'),
            scoring: appT('models.bulkFeatureScoring', 'Scoring'),
            artist_id: appT('models.bulkFeatureArtist', 'Artist ID'),
            segmentation: appT('models.bulkFeatureSegmentation', 'Segmentation'),
            natural_language_caption: appT('models.bulkFeatureCaption', 'Natural-language captions'),
            training_masks: appT('models.bulkFeatureMasks', 'Training masks'),
        };
        return labels[item.feature_key] || item.group || appT('models.groupFallback', 'Feature');
    };

    const listHtml = items.map((item) => {
        const isReady = item.status === 'ready';
        const selectable = !isReady && item.download_supported !== false;
        const selected = selectable && defaultSelectedIds.has(item.id);
        const cls = isReady ? 'is-ready' : 'is-pending';
        const pillText = isReady
            ? appT('models.bulkAlreadyReady', 'already ready')
            : item.gated_download || item.requires_auth
                ? appT('models.bulkAuthRequired', 'HF authorization required')
                : appT('models.bulkWillDownload', 'will download');
        const authUrl = typeof item.auth_url === 'string' && /^https:\/\//i.test(item.auth_url)
            ? `<a class="bulk-download-auth-link" href="${escapeHtml(item.auth_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(appT('models.bulkAuthLink', 'Open authorization page'))}</a>`
            : '';
        const authNote = item.gated_download || item.requires_auth
            ? `<span class="bulk-download-auth-note">${escapeHtml(appT('models.bulkAuthNote', 'Accept the official Hugging Face terms first.'))} ${authUrl}</span>`
            : '';
        return `
            <div class="bulk-download-row ${cls}${selectable ? '' : ' is-disabled'}" data-model-id="${escapeHtml(item.id)}">
                <label class="bulk-download-select">
                    <input type="checkbox" class="bulk-download-checkbox" data-model-id="${escapeHtml(item.id)}" data-testid="bulk-download-select-${escapeHtml(item.id)}"${selected ? ' checked' : ''}${selectable ? '' : ' disabled'}>
                    <span class="bulk-download-name"><strong>${escapeHtml(item.label || item.name || item.id)}</strong><small>${escapeHtml(featureLabel(item))}</small>${authNote}</span>
                </label>
                <span class="bulk-download-pill">${escapeHtml(pillText)}</span>
                <span class="bulk-download-size">~${escapeHtml(_formatBulkBytes(item.size_bytes))}</span>
            </div>
        `;
    }).join('');

    const excludedItems = Array.isArray(bundle.excluded) ? bundle.excluded : [];
    const excludedHtml = excludedItems.length ? `
        <p class="model-card-hint" style="margin-top:8px;">
            ${escapeHtml(appT('models.bulkExcludedNote', 'Skipped:'))} ${
                excludedItems.map((item) => escapeHtml(item.id)).join(', ')
            }
        </p>
    ` : '';
    const bodyHtml = `
        <p>${escapeHtml(appT(
            'models.bulkConfirmIntro',
            'Select the models to download. Recommended models are selected for you; optional models stay off until you choose them.',
        ))}</p>
        <div class="bulk-download-selection-controls" role="group" aria-label="${escapeHtml(appT('models.bulkSelectionLabel', 'Bulk model selection'))}">
            <button type="button" class="btn btn-ghost btn-small" id="bulk-select-recommended">${escapeHtml(appT('models.bulkSelectRecommended', 'Select recommended'))}</button>
            <button type="button" class="btn btn-ghost btn-small" id="bulk-select-all">${escapeHtml(appT('models.bulkSelectAll', 'Select all'))}</button>
            <button type="button" class="btn btn-ghost btn-small" id="bulk-clear-selection">${escapeHtml(appT('models.bulkClearSelection', 'Clear'))}</button>
        </div>
        <div class="bulk-download-list" role="list">${listHtml}</div>
        <div class="bulk-download-summary" id="bulk-download-selection-summary"></div>
        ${excludedHtml}
        <p class="model-card-hint" style="margin-top:10px;">${escapeHtml(appT(
            'models.bulkConfirmNote',
            'Sizes are estimates. Python packages may be installed before model files; restart the app when the result says so, then resume the remaining selections.',
        ))}</p>
    `;

    const getSelectedItems = () => {
        const selectedIds = new Set(
            Array.from(document.querySelectorAll('#confirm-message .bulk-download-checkbox:checked'))
                .map((input) => input.dataset.modelId)
                .filter(Boolean),
        );
        return pendingItems.filter((item) => selectedIds.has(item.id));
    };
    let selectedItems = [];
    const updateSelectionUi = () => {
        const selected = getSelectedItems();
        selectedItems = selected;
        const totalBytes = selected.reduce((total, item) => total + (Number(item.size_bytes) || 0), 0);
        const summary = document.getElementById('bulk-download-selection-summary');
        if (summary) {
            summary.textContent = appT(
                'models.bulkSelectionSummary',
                '{count} model(s) selected · estimated download {size}',
                { count: selected.length, size: _formatBulkBytes(totalBytes) },
            );
        }
        const okButton = document.getElementById('btn-confirm-ok');
        if (okButton) {
            okButton.disabled = selected.length === 0;
            okButton.textContent = appT('models.bulkConfirmOk', 'Download {count} model(s) (~{size})', {
                count: selected.length,
                size: _formatBulkBytes(totalBytes),
            });
        }
    };

    showConfirm(
        appT('models.bulkConfirmTitle', 'Choose models to download'),
        '',
        async () => {
            unlockDynamicI18nText('#btn-confirm-ok', 'modal.yes', 'Yes, proceed');
            const selected = selectedItems.slice();
            if (selected.length === 0) {
                showToast(appT('models.bulkNoSelection', 'Select at least one missing model.'), 'warning');
                return;
            }
            await runBulkDownload(selected);
        },
        () => {
            unlockDynamicI18nText('#btn-confirm-ok', 'modal.yes', 'Yes, proceed');
            const messageEl = document.getElementById('confirm-message');
            if (messageEl) {
                messageEl.style.maxHeight = '';
                messageEl.style.overflowY = '';
                messageEl.style.textAlign = '';
            }
        },
    );

    const messageEl = document.getElementById('confirm-message');
    if (messageEl) {
        // All dynamic registry values are escaped before entering this sink.
        messageEl.innerHTML = bodyHtml;
        messageEl.style.maxHeight = '60vh';
        messageEl.style.overflowY = 'auto';
        messageEl.style.textAlign = 'left';
        messageEl.querySelectorAll('.bulk-download-checkbox').forEach((input) => {
            input.addEventListener('change', updateSelectionUi);
        });
        messageEl.querySelector('#bulk-select-recommended')?.addEventListener('click', () => {
            messageEl.querySelectorAll('.bulk-download-checkbox').forEach((input) => {
                input.checked = defaultSelectedIds.has(input.dataset.modelId);
            });
            updateSelectionUi();
        });
        messageEl.querySelector('#bulk-select-all')?.addEventListener('click', () => {
            messageEl.querySelectorAll('.bulk-download-checkbox:not(:disabled)').forEach((input) => {
                input.checked = true;
            });
            updateSelectionUi();
        });
        messageEl.querySelector('#bulk-clear-selection')?.addEventListener('click', () => {
            messageEl.querySelectorAll('.bulk-download-checkbox').forEach((input) => {
                input.checked = false;
            });
            updateSelectionUi();
        });
    }
    lockDynamicI18nText('#btn-confirm-ok', 'modal.yes');
    updateSelectionUi();
}

async function runBulkDownload(items) {
    const button = $('#btn-bulk-download-models');
    const originalLabel = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
    }

    const total = items.length;
    let completed = 0;
    const failures = [];
    let needsRestart = false;

    // Pulse the Setup button so user knows something is running even if modal is closed
    const setupBtn = $('#btn-open-model-manager');
    if (setupBtn) setupBtn.classList.add('setup-pulse');

    // Show a persistent progress banner inside the model manager modal
    const gridEl = $('#model-manager-grid');
    let banner = document.getElementById('bulk-download-progress-banner');
    if (!banner && gridEl && gridEl.parentElement) {
        banner = document.createElement('div');
        banner.id = 'bulk-download-progress-banner';
        banner.style.cssText = 'padding:12px 16px;margin-bottom:12px;border-radius:8px;background:var(--bg-elevated);border:1px solid var(--accent-primary);font-size:13px;';
        gridEl.parentElement.insertBefore(banner, gridEl);
    }
    const updateBanner = (text) => { if (banner) banner.textContent = text; };

    for (const [itemIndex, item] of items.entries()) {
        updateBanner(appT('models.bulkProgress', 'Downloading {index}/{total}: {name}', { index: completed + 1, total, name: item.name || item.id }));
        if (button) {
            button.innerHTML = `<span aria-hidden="true">⏳</span> <span>${escapeHtml(appT(
                'models.bulkProgress',
                'Downloading {index}/{total}: {name}',
                { index: completed + 1, total, name: item.name || item.id }
            ))}</span>`;
        }

        let prepareStart;
        try {
            const prepareResponse = await API.prepareModel(item.id, {
                variant: item.variant || null,
            });
            prepareStart = _parseModelPrepareStart(prepareResponse, item.id);
        } catch (err) {
            failures.push({ id: item.id, message: err?.message || String(err) });
            completed += 1;
            continue;
        }
        if (prepareStart.activeModelId !== item.id) {
            const message = _modelPrepareConflictMessage(item.id, prepareStart.activeModelId);
            const blockedItems = items.slice(itemIndex);
            failures.push(...blockedItems.map((blockedItem) => ({
                id: blockedItem.id,
                message,
            })));
            completed += blockedItems.length;
            break;
        }

        // Poll progress until this model finishes (or another one starts).
        // Re-uses the existing /api/models/download-progress endpoint that
        // the per-card prepare buttons drive.
        let finished = false;
        let safetyTicks = 0;
        let pollErrorStreak = 0;
        const maxPollErrorStreak = 3;
        while (!finished) {
            await new Promise(r => setTimeout(r, 1500));
            safetyTicks += 1;
            // Hard guard: 1 hour absolute cap per model so the loop can
            // never deadlock if the backend never reports `prepare_result`.
            if (safetyTicks > 2400) {
                failures.push({ id: item.id, message: 'timeout waiting for prepare_result' });
                break;
            }
            try {
                const p = await API.get('/api/models/download-progress');
                pollErrorStreak = 0;
                const pr = p?.prepare_result;
                if (pr && !pr.active && pr.model_id === item.id && pr.status) {
                    finished = true;
                    if (pr.restart_recommended || pr.status === 'needs_restart') {
                        needsRestart = true;
                        break;
                    }
                    if (pr.status !== 'done' && pr.status !== 'ready' && pr.status !== 'warning') {
                        failures.push({
                            id: item.id,
                            message: pr.message || pr.error || pr.status,
                            guidance: _bulkModelGuidance(item, pr),
                        });
                    }
                    break;
                }
                if (button && p?.active && p.total > 0) {
                    const pct = Math.round((p.downloaded / p.total) * 100);
                    const detail = appT('models.bulkProgressDetail', '{index}/{total}: {name} {pct}%', { index: completed + 1, total, name: item.name || item.id, pct });
                    updateBanner(detail);
                    button.innerHTML = `<span aria-hidden="true">⏳</span> <span>${escapeHtml(detail)}</span>`;
                }
            } catch (err) {
                pollErrorStreak += 1;
                if (pollErrorStreak < maxPollErrorStreak) {
                    updateBanner(appT(
                        'models.bulkPollRetry',
                        'Status check failed for {name}; retrying ({attempt}/{limit})...',
                        {
                            name: item.name || item.id,
                            attempt: pollErrorStreak,
                            limit: maxPollErrorStreak,
                        },
                    ));
                    continue;
                }
                const message = appT(
                    'models.bulkPollFailed',
                    'Status checks failed {count} times for {name}. Check the starter console, then reopen Model Manager to resume.',
                    {
                        count: pollErrorStreak,
                        name: item.name || item.id,
                    },
                );
                failures.push({ id: item.id, message, guidance: null });
                finished = true;
                showToast(message, 'error');
            }
        }
        completed += 1;
        if (needsRestart) {
            showToast(appT('models.bulkNeedsRestart', 'Restart required — close and reopen the app, then resume the remaining selections.'), 'warning');
            break;
        }
        // Notify per-model completion so user knows progress even if modal is closed
        if (failures.length === 0 || failures[failures.length - 1]?.id !== item.id) {
            showToast(appT('models.bulkItemDone', '✓ {name} ({index}/{total})', { name: item.name || item.id, index: completed, total }), 'success');
        }
    }

    // Stop the pulse indicator
    if (setupBtn) setupBtn.classList.remove('setup-pulse');

    // Refresh model status to reflect the new "ready" rows.
    try {
        const refreshed = await API.getModelStatus();
        renderModelManager(refreshed.models || []);
    } catch (err) {
        // Non-fatal — the user can re-open the modal.
    }

    if (button) {
        button.disabled = false;
        button.innerHTML = originalLabel
            || `<span aria-hidden="true">⬇️</span> <span>${escapeHtml(appT('models.bulkDownload', 'Download selected models'))}</span>`;
    }

    const actionableFailure = failures.find((failure) => failure.guidance);
    if (actionableFailure) {
        showModelSetupGuide(actionableFailure.guidance);
    }

    const failureSummary = _bulkFailureSummary(failures);

    // Update banner with final result
    if (banner) {
        if (needsRestart) {
            banner.style.borderColor = 'var(--color-warning, var(--accent))';
            banner.style.background = 'rgba(245, 158, 11, 0.1)';
            // Plain-text fallback only: this string goes through escapeHtml, so
            // any markup here would be shown to the user as literal source.
            banner.innerHTML = `<strong>${escapeHtml(appT('models.bulkNeedsRestart', 'Restart required'))}</strong><br>${escapeHtml(appT('models.bulkRestartExplain', 'A feature installed Python packages. Close and restart the app, then reopen the model selector to continue the remaining downloads.'))}`;
        } else if (failures.length === 0) {
            banner.style.borderColor = 'var(--color-success, #4A9D69)';
            banner.style.background = 'rgba(34, 197, 94, 0.1)';
            banner.textContent = appT('models.bulkDoneAll', 'All {count} model(s) downloaded successfully.', { count: total });
            setTimeout(() => { if (banner.parentNode) banner.remove(); }, 10000);
        } else {
            banner.style.borderColor = 'var(--color-danger, var(--danger))';
            banner.textContent = appT('models.bulkDoneMixed', 'Downloaded {ok}/{total}. Failed: {failed}.', { ok: total - failures.length, total, failed: failureSummary });
        }
    }

    if (failures.length === 0 && !needsRestart) {
        showToast(appT('models.bulkDoneAll', 'All {count} model(s) downloaded successfully.', { count: total }), 'success');
    } else if (needsRestart) {
        showToast(appT('models.bulkNeedsRestart', '⚠️ Restart required — close and reopen the app, then click Download again.'), 'warning');
    } else {
        const okCount = total - failures.length;
        showToast(appT(
            'models.bulkDoneMixed',
            'Downloaded {ok}/{total}. Failed: {failed}. Open each model card to retry the failed ones.',
            { ok: okCount, total, failed: failureSummary }
        ), 'warning');
    }
}
