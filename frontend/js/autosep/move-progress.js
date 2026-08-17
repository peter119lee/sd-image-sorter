/**
 * autosep/move-progress.js — autosep.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/autosep.js, pre-split
 * lines 1470-1981: the self-contained move/copy progress cluster around its
 * own two module lets (autosepMoveController/autosepMoveTracker) —
 * show/hide/renderErrors/update/poll/resume — plus
 * executeAutoSeparateWithProgress and its window publish (each publish
 * stays in the file that declares its function). Classic script: loads
 * after autosep/state-constants.js (base).
 */


// ============== Enhanced Execute with Progress ==============

// State for move operation
let autosepMoveController = null;
let autosepMoveTracker = null;
// Cancel here publishes `cancelling` and leaves the terminal write to the
// worker, so a dead worker strands this panel — and every later Auto-Separate
// run — until the app restarts. This tracks how long it has been stranded.
let autosepMoveStuckWatcher = createStuckJobWatcher();

function showAutosepMoveProgress(total) {
    // The Auto-Separate markup uses .autosep-pane-action-body / -preview-body
    // panes; the old `.preview-section` selector never matched any element, so
    // this whole function early-returned and the move/copy progress bar (and
    // its preview-pane status) never rendered. Prefer the action pane (where
    // the Run CTA lives), fall back to the preview pane, then the legacy class.
    const container = document.querySelector('.autosep-pane-action-body')
        || document.querySelector('.autosep-pane-preview-body')
        || document.querySelector('.preview-section');
    if (!container) return;
    const operationMode = getAutoSepOperationMode();

    const cancelLabel = tKey('autosep.cancel', 'Cancel', '取消');
    const hideLabel = tKey('autosep.hide', 'Hide', '隐藏');
    const resetLabel = stuckJobResetLabel();

    // Check if progress element already exists
    let progressEl = document.getElementById('autosep-move-progress');
    if (!progressEl) {
        progressEl = document.createElement('div');
        progressEl.id = 'autosep-move-progress';
        progressEl.className = 'autosep-move-progress';
        // Two-button layout: Cancel actually stops the backend worker via
        // /api/batch-move/cancel; Hide only dismisses this UI block. The
        // previous single-button layout was labelled "Hide" but had id
        // `btn-cancel-autosep-move`, which misled users into thinking
        // dismissing the panel cancelled the underlying batch.
        progressEl.innerHTML = `
            <div class="progress-bar">
                <div class="progress-fill" id="autosep-move-fill" style="width: 0%"></div>
            </div>
            <div class="progress-text" id="autosep-move-text">Moving images...</div>
            <div class="autosep-move-errors" id="autosep-move-errors" role="alert" aria-live="polite" style="display: none;"></div>
            <div class="operation-controls">
                <button class="btn-cancel-operation" id="btn-cancel-autosep-move">${window.escapeHtml(cancelLabel)}</button>
                <button class="btn-cancel-operation" id="btn-hide-autosep-move">${window.escapeHtml(hideLabel)}</button>
                <button class="btn-reset-operation" id="btn-reset-autosep-move" type="button" hidden>${window.escapeHtml(resetLabel)}</button>
            </div>
        `;
        container.appendChild(progressEl);
    } else {
        // Re-localize labels in case language changed since last time.
        const existingCancelBtn = document.getElementById('btn-cancel-autosep-move');
        const existingHideBtn = document.getElementById('btn-hide-autosep-move');
        const existingResetBtn = document.getElementById('btn-reset-autosep-move');
        if (existingCancelBtn) existingCancelBtn.textContent = cancelLabel;
        if (existingHideBtn) existingHideBtn.textContent = hideLabel;
        if (existingResetBtn) existingResetBtn.textContent = resetLabel;
    }

    progressEl.classList.add('visible');
    // The progress block sits at the bottom of the preview pane and is often
    // below the fold, so users thought there was no progress bar at all. Bring
    // it into view when a move/copy starts.
    try { progressEl.scrollIntoView({ block: 'nearest' }); } catch (_) { /* older browsers */ }
    autosepMoveTracker = window.App?.createProgressTracker?.() || null;
    if (autosepMoveTracker && typeof window.App?.resetProgressTracker === 'function') {
        window.App.resetProgressTracker(autosepMoveTracker);
    }
    document.getElementById('autosep-move-fill').style.width = '0%';
    document.getElementById('autosep-move-text').textContent = operationMode === 'copy'
        ? tKey('autosep.preparingCopy', `Preparing to copy ${total} images in the background...`, `准备在后台复制 ${total} 张图片...`)
        : tKey('autosep.preparingMove', `Preparing to move ${total} images in the background...`, `准备在后台移动 ${total} 张图片...`);
    renderAutosepMoveErrors([]);

    const cancelBtn = document.getElementById('btn-cancel-autosep-move');
    if (cancelBtn) {
        cancelBtn.disabled = false;
        cancelBtn.textContent = cancelLabel;
        cancelBtn.onclick = async () => {
            cancelBtn.disabled = true;
            cancelBtn.textContent = tKey('autosep.cancelling', 'Cancelling...', '正在取消…');
            try {
                await window.App.API.post('/api/batch-move/cancel', {});
            } catch (error) {
                cancelBtn.disabled = false;
                cancelBtn.textContent = cancelLabel;
                window.App.showToast(
                    tKey('autosep.cancelFailed', 'Failed to request cancellation', '取消请求失败'),
                    'error'
                );
            }
        };
    }
    const hideBtn = document.getElementById('btn-hide-autosep-move');
    if (hideBtn) {
        hideBtn.onclick = () => {
            // Hide only dismisses the panel. The backend worker keeps
            // running; resumeAutosepMoveProgress() will re-attach if the
            // user navigates back while the worker is still active.
            hideAutosepMoveProgress();
        };
    }
    const resetBtn = document.getElementById('btn-reset-autosep-move');
    setStuckJobResetVisible(resetBtn, false);
    autosepMoveStuckWatcher = createStuckJobWatcher();
    bindStuckJobResetButton(resetBtn, {
        endpoint: '/api/batch-move/reset',
        onCleared: () => {
            // The job is gone, so the panel has nothing left to report: fold it
            // away and let the user start a new run.
            if (autosepMoveController) autosepMoveController.active = false;
            hideAutosepMoveProgress();
        },
    });
}

function hideAutosepMoveProgress() {
    const progressEl = document.getElementById('autosep-move-progress');
    if (progressEl) {
        progressEl.classList.remove('visible');
    }
    setStuckJobResetVisible(document.getElementById('btn-reset-autosep-move'), false);
    autosepMoveStuckWatcher = createStuckJobWatcher();
    if (autosepMoveTracker && typeof window.App?.resetProgressTracker === 'function') {
        window.App.resetProgressTracker(autosepMoveTracker);
    }
    autosepMoveTracker = null;
    autosepMoveController = null;
}

function formatAutosepMoveError(entry) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        throw new TypeError('Auto-Separate error detail must be an object');
    }

    const filename = typeof entry.filename === 'string' ? entry.filename.trim() : '';
    const error = typeof entry.error === 'string' ? entry.error.trim() : '';
    if (!filename || !error) {
        throw new TypeError('Auto-Separate error detail requires non-empty filename and error fields');
    }

    // This panel used to be the one error surface that printed the backend
    // string raw, so it missed the shared localization and message mapping
    // every other surface gets. No context label: the panel already sits under
    // the move it belongs to, and the filename below is the subject.
    // services/sorting/batch_move.py normalizes these causes (single line,
    // basename only, capped at 140 chars) precisely so they survive the
    // jargon/length filter in modules/utils/errors.js intact.
    return `${filename}: ${formatUserError(error)}`;
}

function preserveAutosepMoveErrorDetails() {
    const cancelBtn = document.getElementById('btn-cancel-autosep-move');
    if (cancelBtn) cancelBtn.disabled = true;

    const hideBtn = document.getElementById('btn-hide-autosep-move');
    if (hideBtn) hideBtn.textContent = tKey('common.close', 'Close', '关闭');
}

function renderAutosepMoveErrors(errors) {
    const errorsEl = document.getElementById('autosep-move-errors');
    if (!errorsEl) return;
    if (!Array.isArray(errors)) {
        throw new TypeError('Auto-Separate error details must be an array');
    }

    const normalizedErrors = errors.map(formatAutosepMoveError);
    const renderedSignature = JSON.stringify(normalizedErrors);
    if (errorsEl.dataset.autosepMoveErrorSignature === renderedSignature) {
        return;
    }
    errorsEl.dataset.autosepMoveErrorSignature = renderedSignature;

    if (!normalizedErrors.length) {
        errorsEl.style.display = 'none';
        errorsEl.innerHTML = '';
        return;
    }

    errorsEl.style.display = 'block';
    errorsEl.innerHTML = normalizedErrors
        .map((entry) => `<div class="autosep-move-error-item">${window.escapeHtml(entry)}</div>`)
        .join('');
}

function updateAutosepMoveProgress(progress, fallbackTotal) {
    const fillEl = document.getElementById('autosep-move-fill');
    const textEl = document.getElementById('autosep-move-text');
    const errors = Number(progress.errors || 0);
    if (errors > 0 && !Array.isArray(progress.recent_errors)) {
        throw new TypeError('Auto-Separate progress reports errors but recent_errors is not an array');
    }
    if (errors > 0 && progress.recent_errors.length === 0) {
        throw new TypeError('Auto-Separate progress reports errors but recent_errors is empty');
    }
    
    if (fillEl && textEl) {
        const current = Number(progress.current || 0);
        const total = Number(progress.total || fallbackTotal || 0);
        const moved = Number(progress.moved || 0);
        const operationMode = normalizeAutoSepOperationMode(progress.operation || getAutoSepOperationMode());
        const percent = total > 0 ? Math.round((current / total) * 100) : 0;
        fillEl.style.width = percent + '%';
        if (typeof window.App?.buildProgressText === 'function') {
            const movedLabel = getAutoSepCompletedLabel(operationMode, moved);
            const errorLabel = errors > 0
                ? _formatAutoSepI18n('autosep.progressErrors', '{count} failed', { count: errors })
                : '';
            textEl.textContent = window.App.buildProgressText({
                progress,
                completed: current,
                total,
                tracker: autosepMoveTracker,
                defaultMessage: errorLabel ? `${movedLabel} • ${errorLabel}` : movedLabel,
                primaryLabel: tKey('autosep.title', 'Auto-Separate', '自动分类')
            });
        } else {
            const details = [getAutoSepCompletedLabel(operationMode, moved)];
            if (errors > 0) {
                details.push(_formatAutoSepI18n('autosep.progressErrors', '{count} failed', { count: errors }));
            }
            textEl.textContent = _formatAutoSepI18n(
                'autosep.progressSummary',
                'Processed {current}/{total} images ({details})',
                {
                    current,
                    total,
                    details: details.join(' • '),
                }
            );
        }
    }

    renderAutosepMoveErrors(Array.isArray(progress.recent_errors) ? progress.recent_errors : []);
}

async function pollAutosepMoveProgress(expectedTotal, destination) {
    if (autosepMoveController?.active) return;

    const controller = { active: true, destination };
    autosepMoveController = controller;
    const destinationLabel = destination
        ? _formatAutoSepI18n('autosep.destinationSuffix', ' to {path}', { path: destination })
        : '';

    // The backend job may not have flipped idle->running yet on the first
    // poll(s); tolerate a short grace window (~2s at 250ms/poll) before
    // concluding it never started, so the progress bar doesn't flash-and-vanish.
    const AUTOSEP_IDLE_GRACE_POLLS = 8;
    let idlePolls = 0;

    try {
        while (autosepMoveController === controller && controller.active) {
            const progress = await window.App.API.get('/api/batch-move/progress');
            updateAutosepMoveProgress(progress, expectedTotal);

            if (progress.status === 'idle') {
                idlePolls += 1;
                if (idlePolls >= AUTOSEP_IDLE_GRACE_POLLS) {
                    hideAutosepMoveProgress();
                    window.App.showToast(
                        _formatAutoSepI18n(
                            'autosep.moveStoppedNoProgress',
                            'Batch move stopped before any progress was reported'
                        ),
                        'error'
                    );
                    break;
                }
            } else {
                idlePolls = 0;
            }

            // `cancelling` is not a terminal status, so a worker that died
            // holding it keeps this loop and this panel alive forever, and
            // every later batch move is refused with 409. Offer the reset once
            // the state has genuinely stopped moving.
            setStuckJobResetVisible(
                document.getElementById('btn-reset-autosep-move'),
                isStuckJobStalled(autosepMoveStuckWatcher, progress.status)
            );

            if (progress.status === 'done') {
                setTimeout(() => {
                    const movedCount = Number(progress.moved || 0);
                    const errorCount = Number(progress.errors || 0);
                    const operationMode = normalizeAutoSepOperationMode(progress.operation || getAutoSepOperationMode());
                    if (errorCount > 0) {
                        preserveAutosepMoveErrorDetails();
                    } else {
                        hideAutosepMoveProgress();
                    }

                    if (movedCount > 0 && errorCount > 0) {
                        window.App.showToast(
                            _formatAutoSepI18n(
                                operationMode === 'copy' ? 'autosep.copyPartial' : 'autosep.movePartial',
                                operationMode === 'copy' ? 'Copied {count} images{destination}. {errors} failed.' : 'Moved {count} images{destination}. {errors} failed.',
                                {
                                count: movedCount,
                                destination: destinationLabel,
                                errors: errorCount,
                                }
                            ),
                            'warning'
                        );
                    } else if (movedCount > 0) {
                        window.App.showToast(
                            _formatAutoSepI18n(
                                operationMode === 'copy' ? 'autosep.copySuccess' : 'autosep.moveSuccess',
                                operationMode === 'copy' ? 'Copied {count} images{destination}' : 'Moved {count} images{destination}',
                                {
                                count: movedCount,
                                destination: destinationLabel,
                                }
                            ),
                            'success'
                        );
                    } else if (errorCount > 0) {
                        window.App.showToast(
                            progress.message || _formatAutoSepI18n(
                                operationMode === 'copy' ? 'autosep.copyNoneFailed' : 'autosep.moveNoneFailed',
                                operationMode === 'copy' ? 'No images were copied. {errors} failed.' : 'No images were moved. {errors} failed.',
                                {
                                errors: errorCount,
                                }
                            ),
                            'error'
                        );
                    } else {
                        window.App.showToast(
                            progress.message || _formatAutoSepI18n(
                                operationMode === 'copy' ? 'autosep.copyNone' : 'autosep.moveNone',
                                operationMode === 'copy' ? 'No images were copied' : 'No images were moved'
                            ),
                            'error'
                        );
                    }

                    if (movedCount > 0) {
                        AutoSepState.matchCount = 0;
                        AutoSepState.previewImages = [];
                        AutoSepState.previewSignature = null;
                        _resetAutoSepOverflowState(null);
                        document.querySelector('#autosep-preview .stat-number').textContent = '0';
                        renderAutoSepPreviewList([], 0);

                        if (window.App && window.App.loadImages) {
                            window.App.loadImages();
                        }
                        // FLOW-07: persistent next-step CTA after a successful separate,
                        // so sorting no longer dead-ends (the toast above still confirms
                        // the destination). Reuses window.App.showPipelineNextStep.
                        window.App?.showPipelineNextStep?.({
                            icon: 'i-folders',
                            title: _formatAutoSepI18n('flow.sortDoneTitle', 'Sorting done — what next?'),
                            actions: [
                                { icon: '🔳', label: _formatAutoSepI18n('nav.censor', 'Censor Edit'), action: 'view:censor' },
                                { icon: '📦', label: _formatAutoSepI18n('nav.dataset', 'Dataset'), action: 'view:dataset' },
                                { icon: '🖼️', label: _formatAutoSepI18n('nav.gallery', 'Gallery'), action: 'view:gallery' },
                            ],
                        });
                    }
                }, 300);
                break;
            }

            if (progress.status === 'cancelled') {
                hideAutosepMoveProgress();
                const movedCount = Number(progress.moved || 0);
                const errorCount = Number(progress.errors || 0);
                const operationMode = normalizeAutoSepOperationMode(progress.operation || getAutoSepOperationMode());
                window.App.showToast(
                    progress.message || _formatAutoSepI18n(
                        operationMode === 'copy' ? 'autosep.copyCancelled' : 'autosep.moveCancelled',
                        operationMode === 'copy'
                            ? 'Copy cancelled. {count} images copied so far.'
                            : 'Move cancelled. {count} images moved so far.',
                        { count: movedCount }
                    ),
                    errorCount > 0 ? 'warning' : 'info'
                );
                // Refresh the gallery so the partially-moved files reflect
                // their new on-disk locations. Skip the refresh when nothing
                // was committed — there's nothing to update.
                if (movedCount > 0 && window.App && window.App.loadImages) {
                    window.App.loadImages();
                }
                break;
            }

            if (progress.status === 'error') {
                hideAutosepMoveProgress();
                const operationMode = normalizeAutoSepOperationMode(progress.operation || getAutoSepOperationMode());
                window.App.showToast(
                    progress.message || _formatAutoSepI18n(
                        operationMode === 'copy' ? 'autosep.copyFailed' : 'autosep.moveFailed',
                        operationMode === 'copy' ? 'Failed to copy images' : 'Failed to move images'
                    ),
                    'error'
                );
                break;
            }

            await new Promise(resolve => setTimeout(resolve, 250));
        }
    } finally {
        if (autosepMoveController === controller) {
            controller.active = false;
        }
    }
}

async function resumeAutosepMoveProgress() {
    try {
        const progress = await window.App.API.get('/api/batch-move/progress');
        if (progress?.status !== 'running') {
            return;
        }

        const expectedTotal = Number(progress.total || 0);
        showAutosepMoveProgress(expectedTotal);
        updateAutosepMoveProgress(progress, expectedTotal);
        pollAutosepMoveProgress(expectedTotal, '');
    } catch (error) {
        Logger.warn('Failed to resume auto-separate move progress:', error);
    }
}

// Enhanced execute with progress tracking
async function executeAutoSeparateWithProgress() {
    const { $, API, showToast, showConfirm } = window.App;

    const destEl = $('#autosep-destination');
    const destination = destEl ? destEl.value.trim() : '';
    const operationMode = getAutoSepOperationMode();
    const operationLabel = getAutoSepOperationLabel(operationMode);

    if (!destination) {
        showToast(tKey('autosep.noDestination', 'Please enter a destination folder', '请指定目标文件夹'), 'error');
        return;
    }

    const filters = getAutoSepFilters();
    const currentSignature = getAutoSepFilterSignature(filters);
    if (AutoSepState.previewSignature !== currentSignature) {
        showToast(
            _formatAutoSepI18n(
                operationMode === 'copy' ? 'autosep.previewBeforeCopy' : 'autosep.previewBeforeMove',
                operationMode === 'copy'
                    ? 'Please preview the current filter results before copying images'
                    : 'Please preview the current filter results before moving images'
            ),
            'info'
        );
        await updateAutoSepPreview();
        return;
    }

    if (AutoSepState.matchCount === 0) {
        showToast(
            tKey('autosep.noMatchingImages', 'No images match Auto-Separate filters', '没有图片匹配自动分类筛选'),
            'error'
        );
        return;
    }

    const total = AutoSepState.matchCount;
    const scopeStatus = getAutoSepScopeStatus();
    const scopeLine = scopeStatus.lastSyncedLabel && scopeStatus.matchesGallery
        ? _formatAutoSepI18n('scope.executeSynced', 'Using saved {tool} filters copied from Gallery at {time}', {
            tool: getAutoSepToolLabel(),
            time: scopeStatus.lastSyncedLabel,
        })
        : _formatAutoSepI18n('scope.executeSaved', 'Using saved {tool} filters', {
            tool: getAutoSepToolLabel(),
        });

    const executeMove = async () => {
            showAutosepMoveProgress(total);

            try {
                const contract = buildAutoSepFilterContract(filters);
                const dimensions = {
                    minWidth: contract.minWidth,
                    maxWidth: contract.maxWidth,
                    minHeight: contract.minHeight,
                    maxHeight: contract.maxHeight,
                    aspectRatio: contract.aspectRatio
                };

                const splitByEl = document.getElementById('autosep-split-by');
                const splitBy = (splitByEl?.value || '').trim() || null;

                const startResult = await API.batchMove(
                    contract.generators?.length > 0 ? contract.generators : null,
                    contract.tags?.length > 0 ? contract.tags : null,
                    contract.ratings?.length < 4 ? contract.ratings : null,
                    destination,
                    contract.checkpoints?.length > 0 ? contract.checkpoints : null,
                    contract.loras?.length > 0 ? contract.loras : null,
                    contract.prompts?.length > 0 ? contract.prompts : null,
                    dimensions,
                    contract.search?.trim() || null,
                    {
                        min: contract.minAesthetic,
                        max: contract.maxAesthetic,
                    },
                    operationMode,
                    contract.artist,
                    contract.promptMatchMode,
                    contract.tagMode,
                    {
                        tags: contract.excludeTags?.length > 0 ? contract.excludeTags : null,
                        generators: contract.excludeGenerators?.length > 0 ? contract.excludeGenerators : null,
                        ratings: contract.excludeRatings?.length > 0 ? contract.excludeRatings : null,
                        checkpoints: contract.excludeCheckpoints?.length > 0 ? contract.excludeCheckpoints : null,
                        loras: contract.excludeLoras?.length > 0 ? contract.excludeLoras : null,
                    },
                    // v3.3.x gallery-scope parity: collection/folder/star-rating/
                    // exclude-prompts/colors/brightness must constrain the move
                    // exactly like they constrained the previewed gallery view.
                    {
                        excludePrompts: contract.excludePrompts,
                        excludeColors: contract.excludeColors,
                        minUserRating: contract.minUserRating,
                        brightnessMin: contract.brightnessMin,
                        brightnessMax: contract.brightnessMax,
                        colorTemperature: contract.colorTemperature,
                        brightnessDistribution: contract.brightnessDistribution,
                        collectionId: contract.collectionId,
                        scope: contract.scope,
                        folder: contract.folder,
                        hasMetadata: contract.hasMetadata,
                    },
                    splitBy,
                );

                if (startResult?.error) {
                    throw new Error(startResult.message || startResult.error);
                }
                if (startResult?.status !== 'started') {
                    throw new Error(
                        startResult?.message || _formatAutoSepI18n(
                            operationMode === 'copy' ? 'autosep.copyFailed' : 'autosep.moveFailed',
                            operationMode === 'copy' ? 'Failed to copy images' : 'Failed to move images'
                        )
                    );
                }

                const expectedTotal = startResult.total || total;
                updateAutosepMoveProgress({ current: 0, total: expectedTotal, moved: 0, errors: 0 }, expectedTotal);
                await pollAutosepMoveProgress(expectedTotal, destination);

            } catch (error) {
                hideAutosepMoveProgress();
                showToast(
                    formatUserError(
                        error,
                        _formatAutoSepI18n(
                            operationMode === 'copy' ? 'autosep.copyFailed' : 'autosep.moveFailed',
                            operationMode === 'copy' ? 'Failed to copy images' : 'Failed to move images'
                        )
                    ),
                    "error"
                );
            }
    };

    if (AutoSepState.settings.confirmBeforeMove) {
        const allWarn = AutoSepState.allImagesMode
            ? (window.I18n?.getLang?.() === 'zh-CN'
                ? '\n⚠️ 当前没有设置任何筛选条件，将操作图库中的全部图片！\n'
                : '\n⚠️ No filters are set — this will affect ALL images in the library!\n')
            : '';
        showConfirm(
            tKey('autosep.confirmExecuteTitle', 'Confirm Auto-Separate', '确认自动分类'),
            window.I18n?.getLang?.() === 'zh-CN'
                ? `要把 ${total} 张匹配图片${operationMode === 'copy' ? '复制到' : '移动到'}：\n${destination}${allWarn}\n操作模式：${operationLabel}\n${scopeLine}\n继续前先确认上方预览列表。`
                : `${operationMode === 'copy' ? 'Copy' : 'Move'} ${total} matching images to:\n${destination}${allWarn}\nAction mode: ${operationLabel}\n${scopeLine}\nReview the preview list above before continuing.`,
            executeMove
        );
        return;
    }

    await executeMove();
}

// Replace the original function
window.executeAutoSeparateWithProgress = executeAutoSeparateWithProgress;
