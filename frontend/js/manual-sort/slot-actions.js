/**
 * manual-sort/slot-actions.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 2958-3124 + 3192-3532: resumeSavedSession, loadCurrentImage,
 * progress + minimap rendering, the cooldown/busy guards, performMove /
 * performSkip / undoLastAction, the combo system (incl. its consts) and
 * finishSorting / exitSorting. Classic script: loads after
 * manual-sort/state-constants.js (base).
 */
async function resumeSavedSession(prefetchedSession = null) {
    const { $, API, showToast } = window.App;
    const previousResumeSnapshot = ManualSortState.resumeBannerSessionSnapshot
        ? {
            mode: ManualSortState.resumeBannerSessionSnapshot.mode,
            remaining: ManualSortState.resumeBannerSessionSnapshot.remaining,
            operation_mode: ManualSortState.resumeBannerSessionSnapshot.operation_mode,
            folders: { ...(ManualSortState.resumeBannerSessionSnapshot.folders || {}) },
        }
        : null;

    try {
        const session = prefetchedSession || await API.getCurrentSortImage();

        if (!session || session.done || !(session.image || session.champion)) {
            renderManualSortResumeBanner(null, { visible: false });
            showToast(manualSortText('manual.noSavedSession', 'No saved sorting session to resume', '没有可恢复的已保存排序会话'), 'info');
            return;
        }

        ManualSortState.folders = session.folders || {};
        ManualSortState.startTime = Date.now();
        ManualSortState.combo = 0;
        ManualSortState.lastActionTime = 0;
        ManualSortState.history = [];
        ManualSortState.images = [];
        ManualSortState.currentImage = null;
        ManualSortState.currentTags = [];
        ManualSortState.actionTimestamps = [];
        ManualSortState.sortedCount = 0;
        ManualSortState.skippedCount = 0;
        ManualSortState.undoAvailable = false;
        ManualSortState.redoAvailable = false;
        setManualSortOperationMode(session.operation_mode || ManualSortState.operationMode, { persist: true, updateUi: true });

        if (!Object.keys(ManualSortState.folders).length) {
            const folderResult = await API.get('/api/sort/folders');
            ManualSortState.folders = folderResult?.folders || {};
        }

        restoreFolderInputs();
        const resumeMode = MANUAL_SORT_MODES.has(session.mode) ? session.mode : 'slot';
        // Cull decisions are rebuilt from the server payload in applyCullPayload
        // (server history is the source of truth), so keep/reject choices made
        // before the reload are still routed at finish. Reset the bracket streak
        // so a resumed showdown counts the champion's run from this load.
        ManualSortState.cullDecisions = new Map();
        ManualSortState.bracketStreak = 1;
        ManualSortState.bracketLastChampIndex = null;
        activateSortingUi(resumeMode);
        applyCurrentSortPayload(session);

        renderManualSortResumeBanner(null, { visible: false });
    } catch (error) {
        rollbackSortingUi();
        renderManualSortResumeBanner(previousResumeSnapshot, { visible: Boolean(previousResumeSnapshot) });
        Logger.error('Failed to resume saved session:', error);
        showToast(formatUserError(error, manualSortText('manual.resumeFailed', 'Failed to resume saved session', '恢复已保存会话失败')), 'error');
    }
}

// ============== Load Current Image ==============

async function loadCurrentImage(prefetchedResult = null) {
    const { API } = window.App;

    try {
        const result = prefetchedResult || await API.getCurrentSortImage();
        applyCurrentSortPayload(result, { cacheBust: !!prefetchedResult });
    } catch (error) {
        Logger.error('Failed to load current image:', error);
        throw error;
    }
}

function updateProgress() {
    const { $ } = window.App;
    const percent = ManualSortState.total > 0
        ? (ManualSortState.index / ManualSortState.total) * 100
        : 0;

    $('#sort-progress-fill').style.width = percent + '%';
    $('#sort-progress-text').textContent = `${ManualSortState.index} / ${ManualSortState.total}`;

    // Enhanced progress stats
    const percentEl = $('#sort-percent');
    if (percentEl) percentEl.textContent = Math.round(percent) + '%';

    const sortedEl = $('#sort-sorted-count');
    if (sortedEl) sortedEl.textContent = ManualSortState.sortedCount;

    const skippedEl = $('#sort-skipped-count');
    if (skippedEl) skippedEl.textContent = ManualSortState.skippedCount;

    const remainingEl = $('#sort-remaining-count');
    if (remainingEl) remainingEl.textContent = Math.max(0, ManualSortState.total - ManualSortState.index);

    // Throughput: images per minute over a rolling 10-second window (张/分).
    const speedEl = $('#sort-speed');
    if (speedEl) {
        const now = Date.now();
        const recentActions = ManualSortState.actionTimestamps.filter(t => now - t < 10000);
        const perMinute = recentActions.length > 1
            ? Math.round((recentActions.length / ((now - recentActions[0]) / 1000)) * 60)
            : 0;
        speedEl.textContent = formatManualSortI18n('manual.imagesPerMinute', '{speed}/min', { speed: perMinute });
    }

    // Segmented progress bar
    const sortedFill = $('#sort-progress-sorted');
    const skippedFill = $('#sort-progress-skipped');
    if (sortedFill && skippedFill && ManualSortState.total > 0) {
        const sortedPct = (ManualSortState.sortedCount / ManualSortState.total) * 100;
        const skippedPct = (ManualSortState.skippedCount / ManualSortState.total) * 100;
        sortedFill.style.width = sortedPct + '%';
        skippedFill.style.width = skippedPct + '%';
    }

    // Minimap position
    const minimapPos = $('#minimap-position');
    if (minimapPos) minimapPos.textContent = `${ManualSortState.index + 1}/${ManualSortState.total}`;

    // Also update gallery preview
    updateGalleryPreview();
}

function updateGalleryPreview() {
    const { $, API } = window.App;
    const container = $('#preview-scroll');
    if (!container) return;

    // Get surrounding images (5 before, current, 10 after)
    const startIdx = Math.max(0, ManualSortState.index - 5);
    const endIdx = Math.min(ManualSortState.images?.length || 0, ManualSortState.index + 11);

    if (!ManualSortState.images || ManualSortState.images.length === 0) {
        container.innerHTML = `<span style="color: var(--text-muted); font-size: 12px;">${manualSortText('manual.noImagesLoaded', 'No images loaded', '还没有载入图片')}</span>`;
        return;
    }

    const thumbsHTML = [];
    for (let i = startIdx; i < endIdx; i++) {
        const img = ManualSortState.images[i];
        if (!img) continue;

        let className = 'preview-thumb';
        if (i === ManualSortState.index) {
            className += ' current';
        } else if (i < ManualSortState.index) {
            className += ' processed';
        }

        thumbsHTML.push(`
            <div class="${className}" data-index="${i}" title="${escapeHtml(formatManualSortI18n('manual.previewImageTitle', 'Image {index}', { index: i + 1 }))}">
                <img src="${API?.getThumbnailUrl?.(img.id) ?? `/api/image-thumbnail/${img.id}?size=256`}" alt="" loading="lazy">
            </div>
        `);
    }

    container.innerHTML = thumbsHTML.join('');

    // Scroll to keep current image centered
    const currentThumb = container.querySelector('.current');
    if (currentThumb) {
        currentThumb.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
}

// v3.3.0 USR-4: cooldown + visible busy feedback for manual sort.
function isManualSortInCooldown() {
    const cd = Number(ManualSortState.actionCooldownMs) || 0;
    if (cd <= 0) return false;
    return (Date.now() - (ManualSortState.lastActionCompletedAt || 0)) < cd;
}

// A dropped press (busy or in cooldown) is otherwise invisible. Flash the
// current image wrapper briefly so the user can tell the press was ignored.
function flashManualSortBusy() {
    const wrapper = window.App?.$?.('.current-image-wrapper');
    if (!wrapper) return;
    wrapper.classList.remove('sort-busy-flash');
    // Force reflow so re-adding the class restarts the animation.
    void wrapper.offsetWidth;
    wrapper.classList.add('sort-busy-flash');
    setTimeout(() => wrapper.classList.remove('sort-busy-flash'), 220);
}

async function performMove(folderKey, fast = false) {
    const { $, API, showToast } = window.App;
    // v3.3.1: a collection-typed slot adds the image to a collection by
    // reference ("collect") instead of moving the file.
    const isCollect = isManualSortCollectionSlot(folderKey);
    const operationVerb = isCollect
        ? manualSortText('manual.actionVerbCollect', 'add', '收藏')
        : getManualSortOperationVerb();

    // Prevent race condition from rapid keypresses
    if (ManualSortState.isProcessing) {
        flashManualSortBusy();
        return;
    }
    // v3.3.0 USR-4: optional cooldown (opt-in). Ignore presses fired within
    // the window after the previous action completed.
    if (isManualSortInCooldown()) {
        flashManualSortBusy();
        return;
    }
    ManualSortState.isProcessing = true;

    try {
        // Check the slot is configured (folder path OR collection assignment).
        if (!isCollect && !ManualSortState.folders[folderKey]) {
            showToast(formatManualSortI18n('manual.folderNotConfigured', 'Folder {key} is not configured', {
                key: folderKey.toUpperCase(),
            }), 'error');
            return;
        }

        // Animate folder highlight
        const folderEl = $(`.sort-folder[data-key="${folderKey}"]`);
        folderEl?.classList.add('active');
        setTimeout(() => folderEl?.classList.remove('active'), fast ? 120 : 300);

        // Animate image flying away (skipped on key auto-repeat)
        const direction = DIRECTION_MAP[folderKey];
        const imgWrapper = $('.current-image-wrapper');
        if (!fast) {
            imgWrapper.classList.add(`fly-${direction}`);
        }

        // Play sound
        window.AudioManager?.play('move', folderKey);

        // Wait for animation. On long-press auto-repeat we skip the wait so
        // each action only blocks on the API roundtrip.
        if (!fast) {
            await sleep(300);
        }

        // Send action to server: 'collect' (by reference) or 'move' (file op).
        const result = await API.sortAction(isCollect ? 'collect' : 'move', folderKey);

        if (result.error) {
            updateHistoryControlState(result);
            showToast(
                formatManualSortI18n('manual.operationFailedWithReason', 'Failed to {operation} image: {reason}', {
                    operation: operationVerb,
                    reason: result.error,
                }),
                'error'
            );
            return;
        }

        // Update combo/stats only after a successful action.
        updateCombo();
        ManualSortState.actionTimestamps.push(Date.now());
        const cutoff = Date.now() - 30000;
        ManualSortState.actionTimestamps = ManualSortState.actionTimestamps.filter(t => t > cutoff);

        if (isCollect) {
            const name = getManualSortCollectionName(ManualSortState.collectionSlots[folderKey]) || folderKey.toUpperCase();
            showToast(
                formatManualSortI18n('manual.collectedToast', 'Added to “{name}” (original kept in place)', { name }),
                'success'
            );
        }

        await loadCurrentImage(result);

    } catch (error) {
        Logger.error(`Failed to ${operationVerb} image:`, error);
        showToast(
            formatManualSortI18n('manual.operationFailed', 'Failed to {operation} image', {
                operation: operationVerb,
            }),
            'error'
        );
    } finally {
        ManualSortState.isProcessing = false;
        ManualSortState.lastActionCompletedAt = Date.now();
    }
}

async function performSkip(fast = false) {
    const { $, API, showToast } = window.App;

    // Prevent race condition from rapid keypresses
    if (ManualSortState.isProcessing) {
        flashManualSortBusy();
        return;
    }
    // v3.3.0 USR-4: optional cooldown (opt-in).
    if (isManualSortInCooldown()) {
        flashManualSortBusy();
        return;
    }
    ManualSortState.isProcessing = true;

    try {
        // Animate skip (skipped on key auto-repeat)
        const imgWrapper = $('.current-image-wrapper');
        if (!fast) {
            imgWrapper.classList.add('skip');
        }

        // Play skip sound
        window.AudioManager?.play('skip');

        // Reset combo
        ManualSortState.combo = 0;
        updateComboDisplay();

        if (!fast) {
            await sleep(300);
        }

        const result = await API.sortAction('skip');
        if (result.error) {
            updateHistoryControlState(result);
            showToast(
                formatManualSortI18n('manual.skipFailedWithReason', 'Failed to skip image: {reason}', {
                    reason: result.error,
                }),
                'error'
            );
            return;
        }

        ManualSortState.actionTimestamps.push(Date.now());
        const cutoff = Date.now() - 30000;
        ManualSortState.actionTimestamps = ManualSortState.actionTimestamps.filter(t => t > cutoff);

        await loadCurrentImage(result);

    } catch (error) {
        Logger.error('Failed to skip:', error);
        showToast(manualSortText('manual.skipFailed', 'Failed to skip image', '跳过图片失败'), 'error');
    } finally {
        ManualSortState.isProcessing = false;
        ManualSortState.lastActionCompletedAt = Date.now();
    }
}

// The backend refuses an undo it cannot perform and says exactly why — e.g.
// "another file named '00042.png' is already in 'library'. Move or rename it,
// then undo again." That arrives as the HTTPException detail, wrapped in the
// service's own "Could not undo last action:" preamble. Strip the preamble so
// the toast does not state the failure twice; fall back to the whole message
// if the wording ever changes, because a redundant sentence still beats losing
// the reason.
function describeUndoFailure(error) {
    const raw = typeof error?.message === 'string' ? error.message.trim() : '';
    if (!raw) return '';
    const match = /^could not undo last action:\s*(\S[\s\S]*)$/i.exec(raw);
    return (match ? match[1] : raw).trim();
}

async function undoLastAction() {
    const { $, API, showToast } = window.App;

    // Play undo sound
    window.AudioManager?.play('undo');

    // Reset combo
    ManualSortState.combo = 0;
    updateComboDisplay();

    try {
        const result = await API.sortAction('undo');

        // Check if there was nothing to undo
        if (result.status === 'no_history') {
            updateHistoryControlState(result);
            showToast(manualSortText('manual.undoEmpty', 'Nothing to undo', '没有可撤销的操作'), 'info');
            return;
        }

        await loadCurrentImage(result);
        showToast(manualSortText('manual.undoSuccess', 'Undid last action', '已撤销上一步'), 'info');
    } catch (error) {
        Logger.error('Failed to undo:', error);
        const reason = describeUndoFailure(error);
        showToast(
            reason
                ? formatManualSortI18n('manual.undoFailedWithReason', 'Failed to undo: {reason}', { reason })
                : manualSortText('manual.undoFailed', 'Failed to undo', '撤销失败'),
            'error'
        );
    }
}

// ============== Combo System ==============

const COMBO_WINDOW_MS = 2000;
const COMBO_SOUND_MILESTONE = 5;

function updateCombo() {
    const now = Date.now();
    const timeSinceLast = now - ManualSortState.lastActionTime;

    if (timeSinceLast < COMBO_WINDOW_MS) {
        ManualSortState.combo++;
    } else {
        ManualSortState.combo = 1;
    }

    ManualSortState.lastActionTime = now;
    updateComboDisplay();

    // Play combo sound at milestones
    if (ManualSortState.combo % COMBO_SOUND_MILESTONE === 0 && ManualSortState.combo > 0) {
        window.AudioManager?.play('combo');
    }
}

function updateComboDisplay() {
    const { $ } = window.App;
    const comboEl = $('#combo-display');
    if (!comboEl) return;

    const comboNum = comboEl.querySelector('.combo-number');
    if (!comboNum) return;

    if (ManualSortState.combo >= 3) {
        comboEl.classList.add('visible');
        comboNum.textContent = ManualSortState.combo;

        // Pulse animation
        comboNum.style.transform = 'scale(1.2)';
        setTimeout(() => {
            comboNum.style.transform = 'scale(1)';
        }, 100);
    } else {
        comboEl.classList.remove('visible');
    }
}

// ============== Finish/Exit ==============

function finishSorting() {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    ManualSortState.history = [];
    ManualSortState.undoAvailable = false;
    ManualSortState.redoAvailable = false;
    document.removeEventListener('keydown', handleSortKeypress);
    updateHistoryControlState({ undo_available: false, redo_available: false });

    // Play finish sound
    window.AudioManager?.play('finish');

    // Calculate session stats
    const elapsed = ManualSortState.startTime
        ? Math.round((Date.now() - ManualSortState.startTime) / 1000)
        : 0;
    const minutes = Math.floor(elapsed / 60);
    const seconds = elapsed % 60;
    const timeStr = minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;

    showToast(
        formatManualSortI18n('manual.finishSummary', 'Sorting complete. {sorted} sorted, {skipped} skipped in {time}.', {
            sorted: ManualSortState.sortedCount,
            skipped: ManualSortState.skippedCount,
            time: timeStr,
        }),
        'success'
    );

    // FLOW-07: persistent next-step CTA after finishing a manual sort, so the
    // session no longer dead-ends (the summary toast above still reports the
    // stats). Reuses window.App.showPipelineNextStep.
    window.App?.showPipelineNextStep?.({
        icon: 'i-folders',
        title: formatManualSortI18n('flow.sortDoneTitle', 'Sorting done — what next?'),
        actions: [
            { icon: '🔳', label: formatManualSortI18n('nav.censor', 'Censor Edit'), action: 'view:censor' },
            { icon: '📦', label: formatManualSortI18n('nav.dataset', 'Dataset'), action: 'view:dataset' },
            { icon: '🖼️', label: formatManualSortI18n('nav.gallery', 'Gallery'), action: 'view:gallery' },
        ],
    });

    // Return to setup
    hideSortInterfaces();
    $('#sort-setup').style.display = 'block';

    window.App.API.delete('/api/sort/session').catch(e => {
        if (window.Logger) Logger.warn('Failed to clean up sort session:', e);
    });

    // Refresh gallery
    if (window.App && window.App.loadImages) {
        window.App.loadImages();
    }
}

function exitSorting() {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    ManualSortState.undoAvailable = false;
    ManualSortState.redoAvailable = false;
    document.removeEventListener('keydown', handleSortKeypress);
    updateHistoryControlState({ undo_available: false, redo_available: false });

    hideSortInterfaces();
    $('#sort-setup').style.display = 'block';

    const remaining = Math.max(0, ManualSortState.total - ManualSortState.index);
    if (remaining > 0) {
        renderManualSortResumeBanner(
            {
                mode: ManualSortState.mode,
                remaining,
                operation_mode: getManualSortOperationMode(),
                folders: ManualSortState.folders || {},
            },
            { visible: true }
        );
    }

    showToast(manualSortText('manual.sortingPaused', 'Sorting paused. You can resume later.', '排序已暂停，稍后可以继续。'), 'info');

    // Refresh gallery to show updated image locations
    if (window.App && window.App.loadImages) {
        window.App.loadImages();
    }
}

