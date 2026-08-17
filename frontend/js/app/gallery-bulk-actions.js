/**
 * app/gallery-bulk-actions.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 1492-2080 (of 10,152): gallery delete/remove/move-copy + bg progress pollers.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
function getSelectedGalleryExamples(ids, limit = 5) {
    const imageById = new Map((AppState.images || []).map((image) => [Number(image.id), image]));
    return ids
        .slice(0, limit)
        .map((id) => imageById.get(Number(id))?.filename || `Image ${id}`)
        .join(', ');
}

function getSelectedGalleryCount() {
    if (AppState.selectionScope === 'filtered' && AppState.selectionToken) {
        const selectionTotal = Number(AppState.selectionTotal);
        return Number.isFinite(selectionTotal) ? Math.max(0, selectionTotal) : 0;
    }
    return AppState.selectedIds?.size || 0;
}

function getActiveSelectionTokenForActions() {
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

function isFilteredSelectionActiveForCurrentFilters() {
    return AppState.selectionScope === 'filtered'
        && Boolean(AppState.selectionToken)
        && AppState.selectionFilterKey === getSelectionFilterCacheKey(AppState.filters);
}

function clearGallerySelectionAfterBulkAction() {
    updateSelectionState((selection) => {
        selection.selectedIds = new Set();
        selection.scope = 'visible';
        selection.filterKey = null;
        selection.selectionToken = null;
        selection.selectionTotal = 0;
    });
    resetSelectionDataCache();
}

function getSelectedGalleryIds() {
    if (AppState.selectionScope === 'filtered' && AppState.selectionToken) {
        return [];
    }
    return Array.from(AppState.selectedIds)
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0);
}

async function deleteGalleryImagesByIds(imageIds) {
    const selectionToken = getActiveSelectionTokenForActions();
    const ids = normalizeSelectionImageIds(imageIds);
    const count = selectionToken ? getSelectedGalleryCount() : ids.length;

    if (count === 0) {
        showToast(appT('selection.emptyHint', 'Select images, or choose all current filter matches.'), 'info');
        return;
    }

    const examples = selectionToken ? '' : getSelectedGalleryExamples(ids);
    const title = appT('selection.deleteConfirmTitle', 'Move selected image files to Trash?');
    const message = appT(
        'selection.deleteConfirmBody',
        'This moves {count} original file(s) to the operating system Trash / Recycle Bin and removes them from this gallery. Use Remove from Gallery if you only want to clean the index. Examples: {examples}'
    )
        .replace('{count}', count)
        .replace('{examples}', examples || (selectionToken
            ? appT('selection.filteredExamples', 'current filtered selection')
            : ids.slice(0, 5).join(', ')));

    showConfirm(title, message, async () => {
        try {
            let result;
            if (shouldUseBulkJob(selectionToken, ids.length)) {
                // Debt-22 durable path: token-scoped or large selection.
                const envelope = await API.startDeleteBulkJob(ids, { selectionToken });
                const job = await pollBulkJobUntilDone(envelope, 'delete', {
                    show: _showBgDeleteProgress,
                    update: _updateBgDeleteProgress,
                    hide: _hideBgDeleteProgress,
                });
                result = normalizeBulkJobResult(job, 'delete');
            } else {
                await API.startDeleteJob(ids, { selectionToken });
                result = await pollDeleteProgressUntilDone();
            }
            if (result?.status === 'reset') {
                // Stranded job cleared by the user; the reset control already
                // reported it and there is no run result to summarize.
                await loadImages();
                loadStats();
                return;
            }
            if (result?.status === 'error') {
                showToast(
                    formatUserError(null, appT('selection.deleteFailed', 'Failed to move selected image files to Trash')),
                    'error'
                );
                await loadImages();
                loadStats();
                return;
            }
            const failed = Array.isArray(result.failed) ? result.failed : [];
            const failedIds = new Set(failed.map((item) => Number(item.image_id)));

            if (selectionToken) {
                clearGallerySelectionAfterBulkAction();
            } else {
                const deletedIds = ids.filter((id) => !failedIds.has(id));
                mutateSelectedIds((selectedIds) => {
                    deletedIds.forEach((id) => selectedIds.delete(id));
                });
            }

            updateSelectionUI();
            emitSelectionStateChanged();
            if (window.Gallery && typeof window.Gallery.syncSelectionState === 'function') {
                window.Gallery.syncSelectionState();
            }

            await loadImages();
            loadStats();

            if (result?.status === 'cancelled') {
                showToast(
                    appT('selection.deleteCancelled', 'Stopped. Moved {count} file(s) to Trash before cancelling.')
                        .replace('{count}', result.deleted || 0),
                    'info'
                );
                return;
            }

            // Durable jobs report an aggregate error_count (no per-id `failed`
            // list); Phase-1 jobs report `failed`. Prefer whichever is present.
            const failedCount = Number.isFinite(result.error_count) ? result.error_count : failed.length;
            if (failedCount > 0) {
                showToast(
                    appT('selection.deletePartial', 'Moved {deleted} file(s) to Trash. {failed} failed.')
                        .replace('{deleted}', result.deleted || 0)
                        .replace('{failed}', failedCount),
                    'warning'
                );
                return;
            }

            showToast(
                appT('selection.deleteSuccess', 'Moved {count} image file(s) to Trash.')
                    .replace('{count}', result.deleted || 0),
                'success'
            );
        } catch (error) {
            showToast(
                formatUserError(error, appT('selection.deleteFailed', 'Failed to move selected image files to Trash')),
                'error'
            );
        }
    });
}

function deleteSelectedGalleryImages() {
    return deleteGalleryImagesByIds(getSelectedGalleryIds());
}

async function removeGalleryImagesByIds(imageIds) {
    const selectionToken = getActiveSelectionTokenForActions();
    const ids = normalizeSelectionImageIds(imageIds);
    const count = selectionToken ? getSelectedGalleryCount() : ids.length;

    if (count === 0) {
        showToast(appT('selection.emptyHint', 'Select images, or choose all current filter matches.'), 'info');
        return;
    }

    const examples = selectionToken ? '' : getSelectedGalleryExamples(ids);
    const title = appT('selection.removeConfirmTitle', 'Remove selected images from gallery?');
    const message = appT(
        'selection.removeConfirmBody',
        'This removes {count} image record(s) from this gallery only. Files stay on disk and can be re-imported by scanning again. Examples: {examples}'
    )
        .replace('{count}', count)
        .replace('{examples}', examples || (selectionToken
            ? appT('selection.filteredExamples', 'current filtered selection')
            : ids.slice(0, 5).join(', ')));

    showConfirm(title, message, async () => {
        try {
            let result;
            if (shouldUseBulkJob(selectionToken, ids.length)) {
                // Debt-22 durable path: token-scoped or large selection.
                const envelope = await API.startRemoveBulkJob(ids, { selectionToken });
                const job = await pollBulkJobUntilDone(envelope, 'remove', {
                    show: _showBgRemoveProgress,
                    update: _updateBgRemoveProgress,
                    hide: _hideBgRemoveProgress,
                });
                result = normalizeBulkJobResult(job, 'remove');
            } else {
                await API.startRemoveJob(ids, { selectionToken });
                result = await pollRemoveProgressUntilDone();
            }
            if (result?.status === 'reset') {
                // Stranded job cleared by the user; the reset control already
                // reported it and there is no run result to summarize.
                await loadImages();
                await loadStats();
                return;
            }
            if (result?.status === 'error') {
                showToast(
                    formatUserError(null, appT('selection.removeFailed', 'Failed to remove selected images from gallery')),
                    'error'
                );
                await loadImages();
                await loadStats();
                return;
            }
            if (selectionToken) {
                clearGallerySelectionAfterBulkAction();
            } else {
                mutateSelectedIds((selectedIds) => {
                    ids.forEach((id) => selectedIds.delete(id));
                });
                resetSelectionDataCache();
            }
            updateSelectionUI();
            emitSelectionStateChanged();
            if (window.Gallery && typeof window.Gallery.syncSelectionState === 'function') {
                window.Gallery.syncSelectionState();
            }

            await loadImages();
            await loadStats();

            if (result?.status === 'cancelled') {
                showToast(
                    appT('selection.removeCancelled', 'Stopped. Removed {count} image record(s) before cancelling.')
                        .replace('{count}', result?.removed || 0),
                    'info'
                );
                return;
            }

            // Durable jobs report a `missingCount` integer; Phase-1 jobs report
            // a `missing_ids` array. Prefer whichever is present.
            const missingCount = Number.isFinite(result?.missingCount)
                ? result.missingCount
                : (Array.isArray(result?.missing_ids) ? result.missing_ids.length : 0);
            if (missingCount > 0) {
                showToast(
                    appT('selection.removePartial', 'Removed {removed} image record(s). {missing} were already missing from the gallery.')
                        .replace('{removed}', result?.removed || 0)
                        .replace('{missing}', missingCount),
                    'warning'
                );
                return;
            }

            showToast(
                appT('selection.removeSuccess', 'Removed {count} image record(s) from the gallery. Files were not deleted.')
                    .replace('{count}', result?.removed || 0),
                'success'
            );
        } catch (error) {
            showToast(
                formatUserError(error, appT('selection.removeFailed', 'Failed to remove selected images from gallery')),
                'error'
            );
        }
    });
}

function removeSelectedGalleryImages() {
    return removeGalleryImagesByIds(getSelectedGalleryIds());
}

async function moveOrCopyGalleryImages(imageIds, operation = 'move', options = {}) {
    const normalizedOperation = operation === 'copy' ? 'copy' : 'move';
    // v3.2.1 task #34: when the user is in "Select All Filtered" scope, the
    // selection is represented by a token (not by populating
    // AppState.selectedIds). The previous implementation relied solely on the
    // ID list and showed a misleading "select images" toast even when a
    // filtered selection was active. Mirror the delete/remove logic instead.
    const selectionToken = options.source === 'selection'
        ? getActiveSelectionTokenForActions()
        : null;
    const ids = normalizeSelectionImageIds(imageIds);
    const isSingleContext = options.source === 'context' && ids.length === 1;
    const totalCount = selectionToken ? getSelectedGalleryCount() : ids.length;

    if (totalCount === 0) {
        showToast(appT('selection.emptyHint', 'Select images, or choose all current filter matches.'), 'info');
        return;
    }

    const operationLabel = normalizedOperation === 'copy'
        ? appT('selection.copyVerb', 'Copy')
        : appT('selection.moveVerb', 'Move');
    const destination = await showInputModal(
        isSingleContext
            ? (normalizedOperation === 'copy'
                ? appT('gallery.contextCopyPromptTitle', 'Copy image')
                : appT('gallery.contextMovePromptTitle', 'Move image'))
            : appT('selection.destinationPromptTitle', '{operation} selected images')
                .replace('{operation}', operationLabel),
        appT('selection.destinationPromptBody', 'Enter the destination folder path for {count} selected image(s).')
            .replace('{count}', totalCount),
        getRecentFolders()[0] || ''
    );
    if (!destination || !destination.trim()) return;

    const trimmedDestination = destination.trim();
    const confirmTitle = isSingleContext
        ? (normalizedOperation === 'copy'
            ? appT('gallery.contextCopyConfirmTitle', 'Copy this image file?')
            : appT('gallery.contextMoveConfirmTitle', 'Move this image file?'))
        : (normalizedOperation === 'copy'
            ? appT('selection.copyConfirmTitle', 'Copy selected image files?')
            : appT('selection.moveConfirmTitle', 'Move selected image files?'));
    const confirmBody = (isSingleContext
        ? (normalizedOperation === 'copy'
            ? appT('gallery.contextCopyConfirmBody', 'This copies the file to: {destination}. Original stays in place.')
            : appT('gallery.contextMoveConfirmBody', 'This moves the original file to: {destination}'))
        : (normalizedOperation === 'copy'
            ? appT('selection.copyConfirmBody', 'This copies {count} file(s) to: {destination}. Originals stay in place.')
            : appT('selection.moveConfirmBody', 'This moves {count} original file(s) to: {destination}')))
        .replace('{count}', totalCount)
        .replace('{destination}', trimmedDestination);

    showConfirm(confirmTitle, confirmBody, async () => {
        try {
            // v3.3.0 USR-1: drive the move through the background job so the
            // progress bar streams (and the user can see how far the per-file
            // source deletion has advanced) instead of a silent blocking POST.
            const start = await API.startMoveJob(ids, trimmedDestination, normalizedOperation, { selectionToken });
            let finalProgress = start;
            if (start?.status !== 'done') {
                finalProgress = await pollMoveProgressUntilDone();
            }
            if (finalProgress?.status === 'reset') {
                // The user cleared a job stranded in `cancelling`. The reset
                // control already reported the outcome, and there is no run
                // result to summarize; refresh so partially-moved files show
                // their new locations.
                resetSelectionDataCache();
                await loadImages();
                await loadStats();
                return;
            }
            if (finalProgress?.status === 'error') {
                showToast(
                    finalProgress.message || appT('selection.moveCopyFailed', 'Failed to {operation} selected images')
                        .replace('{operation}', operationLabel.toLowerCase()),
                    'error'
                );
                // Files moved before the failure are gone from their source —
                // reload so the gallery doesn't keep showing stale rows.
                resetSelectionDataCache();
                await loadImages();
                await loadStats();
                return;
            }
            const results = Array.isArray(finalProgress?.results) ? finalProgress.results : [];
            const successes = results.filter((item) => item?.success);
            // For filtered selection mode the API expanded the token server-side,
            // so the per-id failure mapping uses results[].id rather than the
            // empty client-side `ids` list.
            const failed = results.length > 0
                ? results.filter((item) => !item?.success)
                : (ids.length > 0 ? ids.map((id) => ({ id, error: 'No result returned' })) : []);

            if (successes.length > 0 && normalizedOperation === 'move') {
                if (selectionToken) {
                    clearGallerySelectionAfterBulkAction();
                } else {
                    const movedIds = new Set(successes.map((item) => Number(item.id)).filter((id) => Number.isFinite(id)));
                    mutateSelectedIds((selectedIds) => {
                        movedIds.forEach((id) => selectedIds.delete(id));
                    });
                }
            }

            addRecentFolder(trimmedDestination);
            resetSelectionDataCache();
            updateSelectionUI();
            emitSelectionStateChanged();
            if (window.Gallery && typeof window.Gallery.syncSelectionState === 'function') {
                window.Gallery.syncSelectionState();
            }

            await loadImages();
            await loadStats();

            if (finalProgress?.status === 'cancelled') {
                showToast(
                    appT('selection.moveCopyCancelled', '{operation} cancelled after {count} image(s).')
                        .replace('{operation}', operationLabel)
                        .replace('{count}', successes.length),
                    'info'
                );
                return;
            }

            if (failed.length > 0) {
                showToast(
                    appT('selection.moveCopyPartial', '{operation} completed for {success} image(s). {failed} failed.')
                        .replace('{operation}', operationLabel)
                        .replace('{success}', successes.length)
                        .replace('{failed}', failed.length),
                    successes.length > 0 ? 'warning' : 'error'
                );
                return;
            }

            showToast(
                appT('selection.moveCopySuccess', '{operation} completed for {count} image(s).')
                    .replace('{operation}', operationLabel)
                    .replace('{count}', successes.length),
                'success'
            );
        } catch (error) {
            _hideBgMoveProgress();
            showToast(
                formatUserError(error, appT('selection.moveCopyFailed', 'Failed to {operation} selected images')
                    .replace('{operation}', operationLabel.toLowerCase())),
                'error'
            );
        }
    });
}

// The three background bars below all poll a job that `cancel` can strand in
// `cancelling` forever. Each keeps its own stall watcher so the recovery button
// appears only on the bar whose job is actually stuck.
const _bgMoveStuckWatcher = createStuckJobWatcher();
const _bgDeleteStuckWatcher = createStuckJobWatcher();
const _bgRemoveStuckWatcher = createStuckJobWatcher();

function _syncBgJobResetButton(buttonSelector, watcher, progress, { endpoint, onCleared }) {
    const button = $(buttonSelector);
    if (!button) return;
    bindStuckJobResetButton(button, { endpoint, onCleared });
    setStuckJobResetVisible(button, isStuckJobStalled(watcher, progress?.status));
}

// v3.3.0 USR-1: floating move/copy progress bar (mirrors bg-scan-progress).
function _showBgMoveProgress() {
    const bar = $('#bg-move-progress');
    if (bar) bar.style.display = 'flex';
}

function _hideBgMoveProgress() {
    const bar = $('#bg-move-progress');
    if (bar) bar.style.display = 'none';
    setStuckJobResetVisible($('#bg-move-reset'), false);
    _bgMoveStuckWatcher.cancellingSince = 0;
}

function _updateBgMoveProgress(progress) {
    const fill = $('#bg-move-progress-fill');
    const textEl = $('#bg-move-progress-text');
    const total = Number(progress?.total || 0);
    const current = Number(progress?.current || 0);
    const isCopy = progress?.operation === 'copy';
    const indeterminate = total <= 0 || progress?.status === 'starting';
    if (fill) {
        const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
        fill.classList.toggle('is-indeterminate', indeterminate);
        fill.style.width = indeterminate ? '' : (pct + '%');
    }
    if (textEl) {
        if (progress?.status === 'cancelling') {
            textEl.textContent = appT('move.cancelling', 'Stopping...');
            return;
        }
        const verb = isCopy
            ? appT('move.copyingVerb', 'Copying')
            : appT('move.movingVerb', 'Moving');
        // Move (not copy) deletes each source as it advances — make that visible
        // so the user knows exactly when originals are gone (USR-1).
        const sourceNote = isCopy ? '' : appT('move.sourceNote', ' · originals removed as each file moves');
        textEl.textContent = `${verb} ${current}/${total}${sourceNote}`;
    }
}

async function pollMoveProgressUntilDone() {
    _showBgMoveProgress();
    const TERMINAL = new Set(['done', 'cancelled', 'error', 'idle']);
    let stuckReset = false;
    try {
        // Loop until the job reports a terminal state. 300ms cadence matches
        // the scan/batch-move pollers.
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const progress = await API.getMoveProgress();
            _updateBgMoveProgress(progress);
            if (TERMINAL.has(progress?.status)) {
                return progress;
            }
            // `cancelling` is not terminal, so a worker that died holding it
            // keeps this loop running forever. Offer the manual reset instead.
            _syncBgJobResetButton('#bg-move-reset', _bgMoveStuckWatcher, progress, {
                endpoint: '/api/move/reset',
                onCleared: () => { stuckReset = true; },
            });
            if (stuckReset) {
                return { status: 'reset', results: [] };
            }
            await new Promise((resolve) => setTimeout(resolve, 300));
        }
    } finally {
        _hideBgMoveProgress();
    }
}

// v3.3.2 Phase-1: floating delete-to-trash progress bar + poller, mirrors the
// move job's _showBgMoveProgress/_updateBgMoveProgress/pollMoveProgressUntilDone.
function _showBgDeleteProgress() {
    const bar = $('#bg-delete-progress');
    if (bar) bar.style.display = 'flex';
}

function _hideBgDeleteProgress() {
    const bar = $('#bg-delete-progress');
    if (bar) bar.style.display = 'none';
    setStuckJobResetVisible($('#bg-delete-reset'), false);
    _bgDeleteStuckWatcher.cancellingSince = 0;
}

function _updateBgDeleteProgress(progress) {
    const fill = $('#bg-delete-progress-fill');
    const textEl = $('#bg-delete-progress-text');
    const total = Number(progress?.total || 0);
    const current = Number(progress?.current || 0);
    const indeterminate = total <= 0 || progress?.status === 'starting';
    if (fill) {
        const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
        fill.classList.toggle('is-indeterminate', indeterminate);
        fill.style.width = indeterminate ? '' : (pct + '%');
    }
    if (textEl) {
        if (progress?.status === 'cancelling') {
            textEl.textContent = appT('delete.cancelling', 'Stopping...');
            return;
        }
        textEl.textContent = `${appT('delete.trashingVerb', 'Moving to Trash')} ${current}/${total}`;
    }
}

async function pollDeleteProgressUntilDone() {
    _showBgDeleteProgress();
    const TERMINAL = new Set(['done', 'cancelled', 'error', 'idle']);
    let stuckReset = false;
    try {
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const progress = await API.getDeleteProgress();
            _updateBgDeleteProgress(progress);
            if (TERMINAL.has(progress?.status)) {
                return progress;
            }
            _syncBgJobResetButton('#bg-delete-reset', _bgDeleteStuckWatcher, progress, {
                endpoint: '/api/images/delete-selected/reset',
                onCleared: () => { stuckReset = true; },
            });
            if (stuckReset) {
                return { status: 'reset', deleted: 0, failed: [] };
            }
            await new Promise((resolve) => setTimeout(resolve, 300));
        }
    } finally {
        _hideBgDeleteProgress();
    }
}

// v3.3.2 Phase-1: floating remove-from-gallery progress bar + poller (mirrors delete).
function _showBgRemoveProgress() {
    const bar = $('#bg-remove-progress');
    if (bar) bar.style.display = 'flex';
}

function _hideBgRemoveProgress() {
    const bar = $('#bg-remove-progress');
    if (bar) bar.style.display = 'none';
    setStuckJobResetVisible($('#bg-remove-reset'), false);
    _bgRemoveStuckWatcher.cancellingSince = 0;
}

function _updateBgRemoveProgress(progress) {
    const fill = $('#bg-remove-progress-fill');
    const textEl = $('#bg-remove-progress-text');
    const total = Number(progress?.total || 0);
    const current = Number(progress?.current || 0);
    const indeterminate = total <= 0 || progress?.status === 'starting';
    if (fill) {
        const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
        fill.classList.toggle('is-indeterminate', indeterminate);
        fill.style.width = indeterminate ? '' : (pct + '%');
    }
    if (textEl) {
        if (progress?.status === 'cancelling') {
            textEl.textContent = appT('remove.cancelling', 'Stopping...');
            return;
        }
        textEl.textContent = `${appT('remove.removingVerb', 'Removing from gallery')} ${current}/${total}`;
    }
}

async function pollRemoveProgressUntilDone() {
    _showBgRemoveProgress();
    const TERMINAL = new Set(['done', 'cancelled', 'error', 'idle']);
    let stuckReset = false;
    try {
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const progress = await API.getRemoveProgress();
            _updateBgRemoveProgress(progress);
            if (TERMINAL.has(progress?.status)) {
                return progress;
            }
            _syncBgJobResetButton('#bg-remove-reset', _bgRemoveStuckWatcher, progress, {
                endpoint: '/api/images/remove-selected/reset',
                onCleared: () => { stuckReset = true; },
            });
            if (stuckReset) {
                return { status: 'reset', removed: 0, missing_ids: [] };
            }
            await new Promise((resolve) => setTimeout(resolve, 300));
        }
    } finally {
        _hideBgRemoveProgress();
    }
}

// v3.3.2 Phase-1: poll the background batch tag-export job until terminal. The
// batch-export modal owns its own progress UI, so this just returns the terminal
// payload (which embeds the full export result under `result`). Coarse progress:
// no per-chunk advance, no mid-run cancel.
async function pollExportProgressUntilDone() {
    const TERMINAL = new Set(['done', 'cancelled', 'error', 'idle']);
    let fetchFailures = 0;
    // eslint-disable-next-line no-constant-condition
    while (true) {
        let progress;
        try {
            progress = await API.getExportProgress();
            fetchFailures = 0;
        } catch (e) {
            // Tolerate transient fetch errors, but stop after 3 consecutive
            // failures instead of polling forever — the caller's catch turns
            // the throw into a visible error toast.
            fetchFailures += 1;
            if (fetchFailures >= 3) throw e;
            await new Promise((resolve) => setTimeout(resolve, 300));
            continue;
        }
        if (TERMINAL.has(progress?.status)) {
            return progress;
        }
        await new Promise((resolve) => setTimeout(resolve, 300));
    }
}

