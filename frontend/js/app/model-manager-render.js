/**
 * Model Manager status and preparation card rendering.
 * Classic script sharing the app global lexical environment.
 */
function renderModelManager(models = []) {
    const summaryEl = $('#model-manager-summary');
    const gridEl = $('#model-manager-grid');
    if (!summaryEl || !gridEl) return;

    const readyCount = models.filter(model => model.status === 'ready').length;
    const missingCount = models.filter(model => model.status === 'missing').length;

    summaryEl.innerHTML = `
        <div class="model-manager-stat">
            <strong>${readyCount}</strong>
            <span>${escapeHtml(appT('models.ready', 'Ready now'))}</span>
        </div>
        <div class="model-manager-stat">
            <strong>${missingCount}</strong>
            <span>${escapeHtml(appT('models.missing', 'Need attention'))}</span>
        </div>
        <div class="model-manager-stat">
            <strong>${models.length}</strong>
            <span>${escapeHtml(appT('models.total', 'Tracked runtimes'))}</span>
        </div>
    `;

    renderFeatureAvailabilityNotice();

    API.getMirror().then((mirrorData) => {
        const current = mirrorData?.mirror || 'auto';
        // Labels are i18n-driven so the dropdown is not English-only in the
        // zh-CN UI. The ModelScope label is deliberately honest: only the
        // Artist/Kaloscope and SAM3 downloaders actually reach modelscope.cn;
        // every other model (WD14, ToriiGate, OppaiOracle, CLIP, Aesthetic)
        // is HuggingFace-only and uses hf-mirror under this setting.
        const labels = {
            auto: appT('models.mirror.auto', 'Auto (HuggingFace → hf-mirror fallback)'),
            'hf-mirror': appT('models.mirror.hfMirror', 'hf-mirror.com (HF mirror)'),
            modelscope: appT('models.mirror.modelscope', 'ModelScope (Artist & SAM3 only; others use hf-mirror)'),
        };
        let mirrorRow = document.getElementById('model-mirror-row');
        if (!mirrorRow) {
            mirrorRow = document.createElement('div');
            mirrorRow.id = 'model-mirror-row';
            mirrorRow.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;padding:10px 14px;margin-bottom:12px;background:rgba(255,255,255,0.03);border:1px solid rgba(var(--accent-rgb), 0.08);border-radius:12px;';
            gridEl.parentElement.insertBefore(mirrorRow, gridEl);
        }
        const opts = (mirrorData?.options || ['auto', 'hf-mirror', 'modelscope']).map(
            o => `<option value="${escapeHtml(o)}"${o === current ? ' selected' : ''}>${escapeHtml(labels[o] || o)}</option>`
        ).join('');
        const mirrorHint = appT(
            'models.mirror.hint',
            'ModelScope (modelscope.cn) is only used for Artist / Kaloscope and SAM 3. Other models always download from HuggingFace or its hf-mirror.'
        );
        mirrorRow.innerHTML = `
            <label style="font-size:13px;font-weight:600;color:var(--text-secondary);white-space:nowrap;">${escapeHtml(appT('models.mirrorLabel', 'Download Source'))}</label>
            <select class="input-field" id="model-mirror-select" style="flex:1;min-width:220px;font-size:12px;padding:6px 8px;">${opts}</select>
            <div style="flex-basis:100%;font-size:11px;line-height:1.5;color:var(--text-tertiary,#989898);">${escapeHtml(mirrorHint)}</div>
        `;
        document.getElementById('model-mirror-select')?.addEventListener('change', async (e) => {
            try {
                await API.setMirror(e.target.value);
                showToast(appT('models.mirrorSaved', 'Download source saved: {mirror}').replace('{mirror}', labels[e.target.value] || e.target.value), 'success');
            } catch (err) {
                showToast(formatUserError(err, 'Failed to save'), 'error');
            }
        });
    }).catch(() => {});

    const renderModelCard = (model) => {
        const safeId = escapeHtml(model.id);
        const status = model.status || (model.available ? 'ready' : 'missing');
        const statusClass = status === 'ready' ? 'is-ready' : 'is-missing';
        const statusLabel = status === 'ready'
            ? appT('models.readyBadge', 'Ready')
            : appT('models.missingBadge', 'Missing');
        const sourceOptions = Array.isArray(model.sources) ? model.sources.map((source) => `
            <option value="${escapeHtml(source)}">${escapeHtml(source)}</option>
        `).join('') : '';
        // Pre-select the backend-recommended default variant (e.g. wd-swinv2)
        // so the card's Prepare downloads the recommended model, not whichever
        // variant happens to be first in the list (eva02-large is heavy/opt-in).
        const defaultVariant = model.default_variant || '';
        const variantOptions = Array.isArray(model.variants) ? model.variants.map((variant) => `
            <option value="${escapeHtml(variant)}"${variant === defaultVariant ? ' selected' : ''}>${escapeHtml(variant)}</option>
        `).join('') : '';
        const installedVariants = Array.isArray(model.installed_variants) && model.installed_variants.length
            ? `<div class="model-card-hint">${escapeHtml(appT('models.installedVariants', 'Installed variants'))}: ${escapeHtml(model.installed_variants.join(', '))}</div>`
            : '';
        const externalLinks = Array.isArray(model.external_links) ? model.external_links.map((link) => {
            // Defense in depth: only allow http(s) URLs in the model registry. Block javascript:, data:,
            // file:, vbscript: and other surprising schemes even though the registry is backend-controlled.
            const rawUrl = String(link.url || '');
            const safeUrl = /^https?:\/\//i.test(rawUrl) ? rawUrl : '#';
            return `
            <a class="btn btn-ghost btn-small" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label || appT('models.openSource', 'Open source'))}</a>
        `;
        }).join('') : '';

        return `
            <article class="model-card ${statusClass}${model.recommended ? ' is-recommended' : ''}" data-model-id="${safeId}">
                <div class="model-card-header">
                    <div>
                        <div class="model-card-group">${escapeHtml(model.group_key ? appT(model.group_key, model.group || appT('models.groupFallback', 'Feature')) : (model.group || appT('models.groupFallback', 'Feature')))}${model.recommended ? ` <span class="model-card-badge" title="${escapeHtml(appT('models.recommendedTooltip', 'Included in “Download all recommended models”'))}">${escapeHtml(appT('models.recommended', 'Recommended'))}</span>` : ''}</div>
                        <div class="model-card-title">${escapeHtml(model.name || model.id)}</div>
                    </div>
                    <span class="model-card-status ${statusClass}">${escapeHtml(statusLabel)}</span>
                </div>
                <div class="model-card-message">${escapeHtml(model.message_key ? appT(model.message_key, model.message || '', model.message_params || {}) : (model.message || ''))}</div>
                ${model.path ? `<div class="model-card-path">${escapeHtml(appT('models.path', 'Current path'))}:<code>${escapeHtml(model.path)}</code></div>` : ''}
                ${model.runtime_path ? `<div class="model-card-path">${escapeHtml(appT('models.runtimePath', 'Runtime files'))}:<code>${escapeHtml(model.runtime_path)}</code></div>` : ''}
                ${installedVariants}
                ${sourceOptions ? `
                    <label class="model-card-hint">
                        ${escapeHtml(appT('models.source', 'Source'))}
                        <select class="input-field model-source-select" data-model-id="${safeId}">${sourceOptions}</select>
                    </label>
                ` : ''}
                ${variantOptions ? `
                    <label class="model-card-hint">
                        ${escapeHtml(appT('models.variant', 'Variant'))}
                        <select class="input-field model-variant-select" data-model-id="${safeId}">${variantOptions}</select>
                    </label>
                ` : ''}
                ${Array.isArray(model.setup_steps) && model.setup_steps.length && status !== 'ready' ? `
                    <details class="model-card-setup-steps">
                        <summary>${escapeHtml(appT('models.setupSteps', 'Manual setup steps'))}</summary>
                        <div class="model-card-hint">${model.setup_steps.map((s, i) => `<div>${i + 1}. <code>${escapeHtml(s)}</code></div>`).join('')}</div>
                    </details>
                ` : ''}
                <div class="model-card-actions">
                    ${model.download_supported ? `<button class="btn btn-primary btn-prepare-model" data-model-id="${safeId}">${escapeHtml(status === 'ready' ? appT('models.repair', 'Recheck / Repair') : appT('models.prepare', 'Prepare / Download'))}</button>` : ''}
                    ${!model.download_supported && status !== 'ready' ? `<span class="model-card-hint">${escapeHtml(appT('models.noAutoDownload', 'Automatic download not available — follow manual steps above'))}</span>` : ''}
                    ${externalLinks}
                </div>
            </article>
        `;
    };

    // MODELS-07: essentials-first. Recommended models render in a leading
    // "Essentials" section; optional/advanced ones (ToriiGate, OppaiOracle,
    // Wenaka Privacy YOLO) drop into an "Additional" section so a new user is
    // not faced with a flat, undifferentiated wall of model cards.
    const sectionHeading = (key, fallback) =>
        `<div class="model-manager-section" role="presentation">${escapeHtml(appT(key, fallback))}</div>`;
    const recommendedModels = models.filter((model) => model.recommended);
    const optionalModels = models.filter((model) => !model.recommended);
    gridEl.innerHTML = [
        recommendedModels.length
            ? sectionHeading('models.essentials', 'Essentials · recommended for everyone') + recommendedModels.map(renderModelCard).join('')
            : '',
        optionalModels.length
            ? sectionHeading('models.optionalSection', 'Additional & advanced models') + optionalModels.map(renderModelCard).join('')
            : '',
    ].join('');

    const withRestartReminder = (message, prepareResult) => {
        if (!prepareResult?.restart_recommended) return message;
        const packages = Array.isArray(prepareResult.installed_packages)
            ? prepareResult.installed_packages.join(', ')
            : '';
        const reminder = packages
            ? appT('models.restartAfterInstallWithPackages', 'Installed Python packages: {packages}. Restart the app before using this feature.', { packages })
            : appT('models.restartAfterInstall', 'Restart the app before using this feature.');
        return message ? `${message} ${reminder}` : reminder;
    };

    gridEl.querySelectorAll('.btn-prepare-model').forEach((button) => {
        button.addEventListener('click', async () => {
            const modelId = button.dataset.modelId;
            const source = gridEl.querySelector(`.model-source-select[data-model-id="${CSS.escape(modelId)}"]`)?.value || null;
            const variant = gridEl.querySelector(`.model-variant-select[data-model-id="${CSS.escape(modelId)}"]`)?.value || null;
            const originalLabel = button.textContent;
            button.disabled = true;
            button.textContent = appT('models.working', 'Working...');
            let prepareResponse;
            try {
                prepareResponse = await API.prepareModel(modelId, { source, variant });
            } catch (error) {
                showToast(formatUserError(error, appT('models.prepareFailed', 'Model setup failed')), 'error');
                button.disabled = false;
                button.textContent = originalLabel;
                return;
            }
            let prepareStart;
            try {
                prepareStart = _parseModelPrepareStart(prepareResponse, modelId);
            } catch (_error) {
                showToast(appT(
                    'models.invalidPrepareResponse',
                    'Model setup returned an invalid response. Expected an object with non-empty status and model_id. Restart the app and try again.',
                ), 'error');
                button.disabled = false;
                button.textContent = originalLabel;
                return;
            }
            if (prepareStart.activeModelId !== modelId) {
                showToast(_modelPrepareConflictMessage(modelId, prepareStart.activeModelId), 'warning');
                button.disabled = false;
                button.textContent = originalLabel;
                return;
            }

            let finished = false;
            let pollErrorStreak = 0;
            const MAX_POLL_ERROR_STREAK = 8; // ~6s of consecutive poll failures before giving up
            // Stall detection is progress-based, not time-capped: a 5GB model
            // legitimately downloads for far longer than any fixed cutoff. Only
            // warn (informationally, polling continues) after this long with no
            // change in downloaded bytes.
            const STALL_WARNING_MS = 3 * 60 * 1000;
            let lastProgressSignature = null;
            let lastProgressAt = Date.now();
            let stallWarned = false;
            let runningInBackground = false;
            let backgroundPollWarningShown = false;

            // Preparation can include non-interruptible pip/HuggingFace work.
            // Keep polling after the modal closes instead of claiming it stopped.
            let backgroundButton = button.parentElement.querySelector('[data-action="background-model-prepare"]');
            if (!backgroundButton) {
                backgroundButton = document.createElement('button');
                backgroundButton.type = 'button';
                backgroundButton.className = 'btn btn-ghost btn-small';
                backgroundButton.dataset.action = 'background-model-prepare';
                backgroundButton.textContent = appT('models.continueInBackground', 'Run in background');
                button.parentElement.insertBefore(backgroundButton, button.nextSibling);
            }
            backgroundButton.style.display = '';
            backgroundButton.onclick = () => {
                runningInBackground = true;
                backgroundButton.style.display = 'none';
                hideModal('model-manager-modal');
                showToast(
                    appT(
                        'models.continuingInBackground',
                        'Model setup continues in background. You will be notified when it finishes.',
                    ),
                    'info',
                );
            };

            const pollProgress = async () => {
                try {
                    const p = await API.get('/api/models/download-progress');
                    pollErrorStreak = 0; // a successful read clears the transient-failure streak
                    backgroundPollWarningShown = false;
                    const progressSignature = p?.active ? `${p.filename || ''}:${p.downloaded || 0}` : null;
                    if (progressSignature !== lastProgressSignature) {
                        lastProgressSignature = progressSignature;
                        lastProgressAt = Date.now();
                        stallWarned = false;
                    } else if (!stallWarned && Date.now() - lastProgressAt > STALL_WARNING_MS) {
                        // Informational only — keep polling; large downloads can
                        // pause on slow mirrors and resume on their own.
                        stallWarned = true;
                        showToast(appT('models.downloadStalled', 'Download may have stalled. Check your network connection and try again.'), 'warning');
                    }
                    if (p?.active && p.total > 0) {
                        const pct = Math.round((p.downloaded / p.total) * 100);
                        const mb = (p.downloaded / 1048576).toFixed(0);
                        const totalMb = (p.total / 1048576).toFixed(0);
                        button.textContent = `${p.filename || 'Downloading'}: ${mb}/${totalMb} MB (${pct}%)`;
                    } else if (p?.active) {
                        const mb = (p.downloaded / 1048576).toFixed(0);
                        button.textContent = `${p.filename || 'Downloading'}: ${mb} MB...`;
                    }
                    const pr = p?.prepare_result;
                    if (pr && !pr.active && pr.model_id === modelId && pr.status) {
                        finished = true;
                        backgroundButton.style.display = 'none';
                        if (pr.status === 'done') {
                            showToast(withRestartReminder(pr.message || appT('models.readyToast', '{model} is ready.', { model: modelId }), pr), pr.restart_recommended ? 'warning' : 'success');
                            const refreshed = await API.getModelStatus();
                            renderModelManager(refreshed.models || []);
                            document.dispatchEvent(new CustomEvent('model-status-changed', { detail: { modelId } }));
                            return;
                        }
                        if (pr.status === 'warning') {
                            showToast(withRestartReminder(pr.message || appT('models.needsRuntimeToast', 'Model files are present, but runtime setup is incomplete.'), pr), 'warning');
                            const refreshed = await API.getModelStatus();
                            renderModelManager(refreshed.models || []);
                            document.dispatchEvent(new CustomEvent('model-status-changed', { detail: { modelId } }));
                            return;
                        }
                        if (pr.status === 'error') {
                            // If the backend returned structured guidance
                            // (Civitai login wall on Privacy YOLO, archive
                            // verification failure, etc.), surface it as
                            // an actionable dialog instead of swallowing
                            // the recovery path into a toast.
                            const hasGuidance = Array.isArray(pr.manual_steps) && pr.manual_steps.length > 0;
                            if (hasGuidance) {
                                showModelSetupGuide(pr);
                            } else {
                                showToast(pr.message || appT('models.prepareFailed', 'Model setup failed'), 'error');
                            }
                            try {
                                const refreshed = await API.getModelStatus();
                                renderModelManager(refreshed.models || []);
                            } catch (_refreshErr) {
                                button.disabled = false;
                                button.textContent = originalLabel;
                            }
                            return;
                        }
                    }
                } catch (_pollErr) {
                    // A foreground card must eventually recover its controls.
                    // Background mode has no blocked control, so slow its checks
                    // after a failure streak and keep watching for completion.
                    pollErrorStreak++;
                    if (pollErrorStreak >= MAX_POLL_ERROR_STREAK && !finished) {
                        if (runningInBackground) {
                            if (!backgroundPollWarningShown) {
                                backgroundPollWarningShown = true;
                                showToast(appT(
                                    'models.backgroundStatusUnavailable',
                                    'Model setup is still running, but status checks are failing. We will keep checking in the background.',
                                ), 'warning');
                            }
                        } else {
                            finished = true;
                            backgroundButton.style.display = 'none';
                            showToast(appT('models.downloadStalled', 'Download may have stalled. Check your network connection and try again.'), 'warning');
                            button.disabled = false;
                            button.textContent = originalLabel;
                            return;
                        }
                    }
                }
                if (!finished) {
                    const nextPollDelay = runningInBackground && pollErrorStreak >= MAX_POLL_ERROR_STREAK
                        ? 5000
                        : 800;
                    setTimeout(pollProgress, nextPollDelay);
                }
            };
            pollProgress();
        });
    });
}
