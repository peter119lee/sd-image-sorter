/**
 * manual-sort/slot-start.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 1503-2041: the slot (WASD) start path — resume-instead and
 * cross-mode confirms, startSorting (copy/move confirm + session start +
 * minimap preview fetch), folder-name/input helpers, history-control state,
 * hide/activate/rollback stage UI, and the applyCurrentSortPayload dispatcher.
 * Classic script: loads after manual-sort/state-constants.js (base).
 */
// ============== Start Sorting ==============

async function confirmResumeSavedSessionFromStart(savedSession) {
    const body = formatManualSortI18n(
        'manual.resumeInsteadBody',
        'An unfinished Manual Sort session is saved at image {index}/{total} with {remaining} remaining. Resume it instead of starting over. To start from the first matching image, discard the saved session first.',
        {
            index: Number(savedSession.index ?? savedSession.challenger_index ?? 0) + 1,
            total: Number(savedSession.total || 0),
            remaining: Number(savedSession.remaining || 0),
        }
    );

    return new Promise(resolve => {
        window.App.showConfirm(
            manualSortText('manual.resumeInsteadTitle', 'Resume saved Manual Sort session?', '恢复已保存的手动排序会话？'),
            body,
            async () => {
                await resumeSavedSession(savedSession);
                resolve(true);
            },
            () => {
                renderManualSortResumeBanner(savedSession, { visible: true });
                resolve(false);
            }
        );
    });
}

// A saved session exists in a DIFFERENT mode than the one the user just asked
// to start. Never silently resume the old one (that ignored the mode choice
// and — because the direction keys alias WASD — could move files the user
// thought they were only comparing). Ask explicitly: OK discards the old
// session and starts the requested mode fresh; Cancel keeps the old session
// and surfaces the resume banner so it can be continued from there.
async function confirmCrossModeSavedSession(savedSession, requestedMode) {
    const { API, showToast } = window.App;
    const savedMode = MANUAL_SORT_MODES.has(savedSession.mode) ? savedSession.mode : 'slot';
    const body = formatManualSortText(
        'manual.crossModeBody',
        'You chose {new}, but an unfinished {old} session is saved (image {index}/{total}, {remaining} left). Discard it and start a new {new} session? Choose Cancel to keep the {old} session and resume it from the banner instead.',
        '你选择了「{new}」，但有一个未完成的「{old}」会话（第 {index}/{total} 张，还剩 {remaining}）。丢弃它并开始新的「{new}」吗？点「取消」可保留「{old}」会话，稍后从横幅恢复。',
        {
            new: getManualSortModeLabel(requestedMode),
            old: getManualSortModeLabel(savedMode),
            index: Number(savedSession.index ?? savedSession.challenger_index ?? 0) + 1,
            total: Number(savedSession.total || 0),
            remaining: Number(savedSession.remaining || 0),
        }
    );

    return new Promise(resolve => {
        window.App.showConfirm(
            manualSortText('manual.crossModeTitle', 'Finish the saved session first', '先处理未完成的排序会话'),
            body,
            async () => {
                try {
                    await API.delete('/api/sort/session');
                } catch (error) {
                    if (window.Logger) Logger.warn('Failed to discard cross-mode session:', error);
                    showToast(
                        formatUserError(error, manualSortText('manual.discardSessionFailed', 'Failed to discard saved session', '丢弃已保存会话失败')),
                        'error'
                    );
                    resolve(true); // handled (aborted); caller must not start
                    return;
                }
                renderManualSortResumeBanner(null, { visible: false });
                // Re-enter the requested start path. The saved session is gone,
                // so this run proceeds straight to a fresh start.
                if (requestedMode === 'bracket') await startBracketSorting();
                else if (requestedMode === 'cull') await startCullSorting();
                else await startSorting();
                resolve(true);
            },
            () => {
                renderManualSortResumeBanner(savedSession, { visible: true });
                resolve(true); // handled (kept); caller must not start
            }
        );
    });
}

async function startSorting() {
    // v3.3.2 WB-S3: A/B Showdown uses a separate, folder-free start path so the
    // slot (WASD) flow below stays exactly as it was.
    if (getManualSortSelectedMode() === 'bracket') {
        return startBracketSorting();
    }
    // v3.3.2 FF-1: 留/汰 cull is also folder-free and non-destructive.
    if (getManualSortSelectedMode() === 'cull') {
        return startCullSorting();
    }

    const { $, $$, API, showToast } = window.App;
    const operationMode = getManualSortOperationMode();
    const operationLabel = getManualSortOperationLabel(operationMode);

    try {
        const savedSession = await API.getCurrentSortImage();
        if (savedSession && !savedSession.done && (savedSession.image || savedSession.champion)) {
            const savedMode = MANUAL_SORT_MODES.has(savedSession.mode) ? savedSession.mode : 'slot';
            if (savedMode === 'slot') {
                await confirmResumeSavedSessionFromStart(savedSession);
            } else {
                await confirmCrossModeSavedSession(savedSession, 'slot');
            }
            return;
        }
    } catch (error) {
        if (window.Logger) Logger.warn('Failed to check existing sort session before start:', error);
    }

    // Collect folder paths (folder-typed slots only).
    const folders = {};
    $$('.folder-path-input').forEach(input => {
        const key = input.dataset.key;
        if (input.value.trim() && !isManualSortCollectionSlot(key)) {
            folders[key] = input.value.trim();
        }
    });

    // v3.3.1: collection-typed slots ({ key: collectionId }).
    const collectionSlots = getManualSortActiveCollectionSlots();
    const hasCollectionSlot = MANUAL_SORT_SLOT_KEYS.some((key) => isManualSortCollectionSlot(key));

    // Validate at least one destination (folder OR collection).
    if (Object.keys(folders).length === 0 && !hasCollectionSlot) {
        showToast(manualSortText('manual.configureFolder', 'Please configure at least one destination folder', '请至少配置一个目标文件夹'), 'error');
        return;
    }

    const replaceExisting = false;

    // Confirmation dialog before starting (files will be moved/copied)
    const scopeStatus = getManualSortScopeStatus();
    const scopeLine = scopeStatus.lastSyncedLabel && scopeStatus.matchesGallery
        ? formatManualSortI18n('scope.executeSynced', 'Using saved {tool} filters copied from Gallery at {time}', {
            tool: getManualSortToolLabel(),
            time: scopeStatus.lastSyncedLabel,
        })
        : formatManualSortI18n('scope.executeSaved', 'Using saved {tool} filters', {
            tool: getManualSortToolLabel(),
        });
    const confirmMessage = window.I18n?.getLang?.() === 'zh-CN'
        ? formatManualSortI18n(
            operationMode === 'copy' ? 'manual.startSortingConfirmCopy' : 'manual.startSortingConfirmMove',
            operationMode === 'copy'
                ? '开始排序后，图片会被复制到对应文件夹，原图保持不动。\n\n操作模式：{mode}\n{scope}\n确定开始吗？'
                : '开始排序后，图片将被移动到对应文件夹。\n\n操作模式：{mode}\n{scope}\n确定开始吗？',
            { scope: scopeLine, mode: operationLabel }
        )
        : formatManualSortI18n(
            operationMode === 'copy' ? 'manual.startSortingConfirmCopy' : 'manual.startSortingConfirmMove',
            operationMode === 'copy'
                ? 'Starting a sort session will copy images to the configured folders and keep the originals in place.\n\nAction mode: {mode}\n{scope}\nAre you sure?'
                : 'Starting a sort session will move images to the configured folders.\n\nAction mode: {mode}\n{scope}\nAre you sure?',
            { scope: scopeLine, mode: operationLabel }
        );
    const confirmed = await new Promise(resolve => {
        window.App.showConfirm(
            manualSortText('manual.startSortingTitle', 'Start Sorting', '确认开始排序'),
            confirmMessage,
            () => resolve(true),
            () => resolve(false)
        );
    });
    if (!confirmed) return;

    ManualSortState.folders = folders;
    setManualSortOperationMode(operationMode, { persist: true, updateUi: true });

    // Save destination folders for quick access later
    Object.keys(folders).forEach(key => {
        const path = folders[key];
        localStorage.setItem(`sort-folder-${key}`, path);
        if (window.App && window.App.addRecentFolder) {
            window.App.addRecentFolder(path);
        }
    });

    // Manual Sort keeps its own filter state so queue/sort work does not pollute Gallery.
    const f = buildManualSortFilterContract(getManualSortFilters());
    const generators = f.generators?.length > 0 ? f.generators : null;
    const ratings = f.ratings?.length > 0 ? f.ratings : null;
    const tags = f.tags?.length > 0 ? f.tags : null;
    const checkpoints = f.checkpoints?.length > 0 ? f.checkpoints : null;
    const loras = f.loras?.length > 0 ? f.loras : null;
    const prompts = f.prompts?.length > 0 ? f.prompts : null;
    const search = f.search?.trim() || null;
    const dimensions = {
        minWidth: f.minWidth,
        maxWidth: f.maxWidth,
        minHeight: f.minHeight,
        maxHeight: f.maxHeight,
        aspectRatio: f.aspectRatio
    };

    try {
        // Set folders + collection slots on server
        await API.setSortFolders(folders, collectionSlots);

        // Start session with unified filters including prompts and dimensions
        const result = await API.startSortSession(
            generators,
            tags,
            ratings,
            folders,
            checkpoints,
            loras,
            prompts,
            dimensions,
            search,
            {
                min: f.minAesthetic,
                max: f.maxAesthetic,
            },
            operationMode,
            f.artist,
            replaceExisting,
            f.promptMatchMode,
            f.tagMode,
            {
                tags: f.excludeTags?.length > 0 ? f.excludeTags : null,
                generators: f.excludeGenerators?.length > 0 ? f.excludeGenerators : null,
                ratings: f.excludeRatings?.length > 0 ? f.excludeRatings : null,
                checkpoints: f.excludeCheckpoints?.length > 0 ? f.excludeCheckpoints : null,
                loras: f.excludeLoras?.length > 0 ? f.excludeLoras : null,
            },
            collectionSlots,
            'slot',
            buildManualSortScopeFilters(f),
        );

        if (result.total_images === 0) {
            showToast(
                manualSortText('manual.noImages', 'No images match Manual Sort filters', '没有图片匹配手动排序筛选'),
                'error'
            );
            return;
        }

        // Fetch images for gallery preview with paginated requests.
        const previewImages = [];
        let previewCursor = null;

        while (previewImages.length < result.total_images && previewImages.length < MAX_MINIMAP_IMAGES) {
            const remainingPreviewSlots = Math.min(
                result.total_images - previewImages.length,
                MAX_MINIMAP_IMAGES - previewImages.length
            );
            const imagesResult = await API.getImages({
                generators: generators,
                tags: tags,
                tagMode: f.tagMode,
                ratings: ratings,
                checkpoints: checkpoints,
                loras: loras,
                prompts: prompts,
                promptMatchMode: f.promptMatchMode,
                artist: f.artist,
                search: search,
                minWidth: f.minWidth,
                maxWidth: f.maxWidth,
                minHeight: f.minHeight,
                maxHeight: f.maxHeight,
                aspectRatio: f.aspectRatio,
                minAesthetic: f.minAesthetic,
                maxAesthetic: f.maxAesthetic,
                excludeTags: f.excludeTags?.length > 0 ? f.excludeTags : null,
                excludeGenerators: f.excludeGenerators?.length > 0 ? f.excludeGenerators : null,
                excludeRatings: f.excludeRatings?.length > 0 ? f.excludeRatings : null,
                excludeCheckpoints: f.excludeCheckpoints?.length > 0 ? f.excludeCheckpoints : null,
                excludeLoras: f.excludeLoras?.length > 0 ? f.excludeLoras : null,
                // v3.3.x gallery-scope parity: the minimap preview must show
                // the same set the session will actually iterate.
                ...buildManualSortScopeFilters(f),
                limit: remainingPreviewSlots,
                cursor: previewCursor
            });

            if (!imagesResult?.images?.length) {
                break;
            }

            previewImages.push(...imagesResult.images.slice(0, remainingPreviewSlots));

            if (!imagesResult.has_more || !imagesResult.next_cursor) {
                break;
            }

            previewCursor = imagesResult.next_cursor;
        }

        ManualSortState.total = result.total_images;
        ManualSortState.index = 0;
        ManualSortState.combo = 0;
        ManualSortState.history = [];
        ManualSortState.images = previewImages;
        ManualSortState.sortedCount = 0;
        ManualSortState.skippedCount = 0;
        ManualSortState.undoAvailable = false;
        ManualSortState.redoAvailable = false;
        ManualSortState.startTime = Date.now();
        ManualSortState.actionTimestamps = [];
        ManualSortState.currentImage = null;
        ManualSortState.currentTags = [];

        // Update folder names in UI
        updateFolderNames();

        activateSortingUi();

        try {
            await loadCurrentImage();
        } catch (error) {
            rollbackSortingUi();
            throw error;
        }

        // Play start sound
        window.AudioManager?.play('start');

    } catch (error) {
        showToast(formatUserError(error, manualSortText('manual.startFailed', 'Failed to start sorting', '开始排序失败')), "error");
    }
}

function updateFolderNames() {
    const { $ } = window.App;

    Object.keys(DEFAULT_FOLDER_LABELS).forEach((key) => {
        const nameEl = $(`#folder-name-${key}`);
        if (!nameEl) return;

        // v3.3.1: collection-typed slots show the collection name + a hint that
        // the action adds by reference (the file is not moved).
        if (isManualSortCollectionSlot(key)) {
            const name = getManualSortCollectionName(ManualSortState.collectionSlots[key]);
            const label = name || manualSortText('manual.collectSlotFallback', 'Collection', '收藏夹');
            nameEl.textContent = `${label}`;
            nameEl.title = formatManualSortI18n(
                'manual.collectHint',
                'Adds to “{name}” by reference — the file is not moved.',
                { name: label }
            );
            return;
        }

        nameEl.title = '';
        const path = ManualSortState.folders[key];
        if (path) {
            const parts = path.split(/[/\\]/);
            nameEl.textContent = parts[parts.length - 1] || path;
        } else {
            nameEl.textContent = DEFAULT_FOLDER_LABELS[key] || key.toUpperCase();
        }
    });
}

function restoreFolderInputs() {
    document.querySelectorAll('.folder-path-input').forEach(input => {
        const key = input.dataset.key;
        input.value = key ? (ManualSortState.folders[key] || '') : '';
    });
    updateFolderNames();
}

function syncPreviewImages(imageIds = [], currentImage = null) {
    if (!Array.isArray(imageIds) || imageIds.length === 0) {
        ManualSortState.images = [];
        return;
    }

    const existingById = new Map((ManualSortState.images || []).map(image => [image.id, image]));
    ManualSortState.images = imageIds.map(id => existingById.get(id) || { id });

    if (currentImage?.id) {
        const currentIndex = imageIds.indexOf(currentImage.id);
        if (currentIndex >= 0) {
            ManualSortState.images[currentIndex] = currentImage;
        }
    }
}

function updateHistoryControlState(state = {}) {
    if (typeof state.undo_available === 'boolean') {
        ManualSortState.undoAvailable = state.undo_available;
    }
    if (typeof state.redo_available === 'boolean') {
        ManualSortState.redoAvailable = state.redo_available;
    }

    document.querySelectorAll('[data-action="undo"]').forEach(btn => {
        const disabled = !ManualSortState.active || !ManualSortState.undoAvailable;
        btn.disabled = disabled;
        btn.setAttribute('aria-disabled', String(disabled));
    });

    document.querySelectorAll('[data-action="redo"]').forEach(btn => {
        const disabled = !ManualSortState.active || !ManualSortState.redoAvailable;
        btn.disabled = disabled;
        btn.setAttribute('aria-disabled', String(disabled));
    });
}

// v3.3.2 WB-S3: hide both the slot and bracket interfaces (used by
// finish/exit/rollback so neither lingers when returning to setup).
function hideSortInterfaces() {
    const { $ } = window.App;
    const slot = $('#sort-interface');
    if (slot) slot.style.display = 'none';
    const bracket = $('#sort-bracket-interface');
    if (bracket) bracket.style.display = 'none';
    const cull = $('#sort-cull-interface');
    if (cull) cull.style.display = 'none';
    // Leaving any stage always restores the app chrome (focus mode is a
    // stage-only affordance; the saved preference survives for next time).
    clearManualSortZen();
}

function activateSortingUi(mode = 'slot') {
    const { $ } = window.App;
    ManualSortState.active = true;
    ManualSortState.mode = MANUAL_SORT_MODES.has(mode) ? mode : 'slot';
    document.removeEventListener('keydown', handleSortKeypress);
    document.addEventListener('keydown', handleSortKeypress);
    $('#sort-setup').style.display = 'none';
    hideSortInterfaces();
    if (ManualSortState.mode === 'bracket') {
        $('#sort-bracket-interface').style.display = 'flex';
    } else if (ManualSortState.mode === 'cull') {
        $('#sort-cull-interface').style.display = 'flex';
    } else {
        $('#sort-interface').style.display = 'flex';
        // Restore the chosen focus state for the WASD stage (the toggle lives
        // in this HUD). hideSortInterfaces() above already cleared it.
        applyManualSortZen(getManualSortZenPref(), { persist: false });
    }
    // Re-sync the HUD mute button — the global Settings toggle may have changed
    // AudioManager since this button was last painted.
    syncSortMuteButton();
    updateHistoryControlState();
}

function rollbackSortingUi() {
    const { $ } = window.App;
    ManualSortState.active = false;
    document.removeEventListener('keydown', handleSortKeypress);
    hideSortInterfaces();
    $('#sort-setup').style.display = 'block';
    updateHistoryControlState({ undo_available: false, redo_available: false });
}

function applyCurrentSortPayload(result, options = {}) {
    // v3.3.2 WB-S3: bracket sessions render the A/B interface instead of the
    // single-image slot view. Keeps the slot path below byte-identical.
    if (result?.mode === 'bracket') {
        return applyBracketPayload(result, options);
    }
    if (result?.mode === 'cull') {
        return applyCullPayload(result, options);
    }

    const { $, API } = window.App;
    const { cacheBust = false } = options;

    if (result?.operation_mode) {
        setManualSortOperationMode(result.operation_mode, { persist: true, updateUi: true });
    }

    if (result?.folders && typeof result.folders === 'object') {
        ManualSortState.folders = { ...ManualSortState.folders, ...result.folders };
        restoreFolderInputs();
    }

    // v3.3.1: adopt the session's per-slot collection assignments so a resumed
    // session keeps its collection-typed slots (and the legend/labels match).
    if (result?.collection_slots && typeof result.collection_slots === 'object') {
        MANUAL_SORT_SLOT_KEYS.forEach((key) => {
            const value = Number(result.collection_slots[key]);
            ManualSortState.collectionSlots[key] = Number.isInteger(value) && value > 0 ? value : null;
        });
        saveManualSortSlotCollections();
        populateManualSortCollectionSelects();
        MANUAL_SORT_SLOT_KEYS.forEach(refreshManualSortSlotUi);
        updateFolderNames();
    }

    if (Array.isArray(result?.image_ids)) {
        syncPreviewImages(result.image_ids, result.image || null);
    }

    if (Number.isFinite(result?.sorted_count)) {
        ManualSortState.sortedCount = result.sorted_count;
    }
    if (Number.isFinite(result?.skipped_count)) {
        ManualSortState.skippedCount = result.skipped_count;
    }

    updateHistoryControlState(result || {});

    if (result?.done) {
        finishSorting();
        return false;
    }

    ManualSortState.currentImage = result.image;
    ManualSortState.currentTags = result.tags || [];
    ManualSortState.index = result.index;
    ManualSortState.total = result.total;

    if (ManualSortState.currentImage?.id && ManualSortState.images?.length > ManualSortState.index) {
        ManualSortState.images[ManualSortState.index] = ManualSortState.currentImage;
    }

    const imgWrapper = $('.current-image-wrapper');
    imgWrapper.classList.remove('fly-up', 'fly-down', 'fly-left', 'fly-right', 'skip');
    imgWrapper.classList.add('slide-in');

    const img = $('#current-image');
    const cacheSuffix = cacheBust ? `?t=${Date.now()}` : '';
    img.src = ManualSortState.currentImage?.id ? API.getImageUrl(ManualSortState.currentImage.id) + cacheSuffix : '';

    const tagsEl = $('#current-image-tags');
    const topTags = ManualSortState.currentTags.slice(0, 5);
    tagsEl.innerHTML = topTags
        .map(t => `<span class="image-tag">${escapeHtml(t.tag)}</span>`)
        .join('');

    updateProgress();

    setTimeout(() => {
        imgWrapper.classList.remove('slide-in');
    }, 300);

    return true;
}

