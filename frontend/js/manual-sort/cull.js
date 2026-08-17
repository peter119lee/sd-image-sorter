/**
 * manual-sort/cull.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 2612-2957 (the entire 留/汰 Keep-Reject block, v3.3.2 FF-1):
 * startCullSorting, payload render, progress/tally, the client-side decision
 * map, performCullAction, decision routing, finishCullSorting and the cull key
 * map. Classic script: loads after manual-sort/state-constants.js (base).
 */
// ============== 留/汰 Keep-Reject cull — v3.3.2 FF-1 ==============

// Folder-free, non-destructive start path (mirrors startBracketSorting). One
// image at a time; keep/reject decisions are tracked client-side and routed to
// the chosen collections at finish.
async function startCullSorting() {
    const { API, showToast } = window.App;

    try {
        const existing = await API.getCurrentSortImage();
        const hasActive = existing && !existing.done && (existing.image || existing.champion);
        if (hasActive) {
            if (existing.mode === 'cull') {
                ManualSortState.startTime = Date.now();
                ManualSortState.history = [];
                ManualSortState.actionTimestamps = [];
                ManualSortState.cullDecisions = new Map();
                activateSortingUi('cull');
                applyCurrentSortPayload(existing);
                showToast(manualSortText('manual.cullResumed', 'Resumed your Keep/Reject session.', '已恢复留/汰整理。'), 'info');
            } else {
                await confirmCrossModeSavedSession(existing, 'cull');
            }
            return;
        }
    } catch (error) {
        if (window.Logger) Logger.warn('Failed to check existing session before cull start:', error);
    }

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
        aspectRatio: f.aspectRatio,
    };

    try {
        const result = await API.startSortSession(
            generators, tags, ratings,
            {}, // no destination folders for cull
            checkpoints, loras, prompts, dimensions, search,
            { min: f.minAesthetic, max: f.maxAesthetic },
            'copy', // operation mode irrelevant; cull does not move files
            f.artist, false, f.promptMatchMode, f.tagMode,
            {
                tags: f.excludeTags?.length > 0 ? f.excludeTags : null,
                generators: f.excludeGenerators?.length > 0 ? f.excludeGenerators : null,
                ratings: f.excludeRatings?.length > 0 ? f.excludeRatings : null,
                checkpoints: f.excludeCheckpoints?.length > 0 ? f.excludeCheckpoints : null,
                loras: f.excludeLoras?.length > 0 ? f.excludeLoras : null,
            },
            null, // collection slots
            'cull',
            buildManualSortScopeFilters(f),
        );

        const totalImages = Number(result?.total_images ?? 0);
        if (totalImages === 0) {
            showToast(manualSortText('manual.noImages', 'No images match Manual Sort filters', '没有图片匹配手动排序筛选'), 'error');
            return;
        }

        ManualSortState.startTime = Date.now();
        ManualSortState.history = [];
        ManualSortState.images = [];
        ManualSortState.currentImage = null;
        ManualSortState.currentTags = [];
        ManualSortState.actionTimestamps = [];
        ManualSortState.sortedCount = 0;
        ManualSortState.skippedCount = 0;
        ManualSortState.combo = 0;
        ManualSortState.cullDecisions = new Map();

        activateSortingUi('cull');
        await loadCurrentImage();
    } catch (error) {
        rollbackSortingUi();
        Logger.error('Failed to start Keep/Reject cull:', error);
        showToast(formatUserError(error, manualSortText('manual.cullStartFailed', 'Failed to start Keep/Reject', '开始留/汰失败')), 'error');
    }
}

// Render the single judged image. Returns false when the session finished (so
// callers mirror applyCurrentSortPayload's contract).
function applyCullPayload(result, options = {}) {
    const { $, API } = window.App;
    ManualSortState.mode = 'cull';

    updateHistoryControlState(result || {});

    // v3.3.2 fix: rebuild the decision map from the server payload (history is
    // the source of truth) so a resumed session re-routes keep/reject choices
    // made before a reload — not just those made in the current page load.
    if (result && result.decisions && typeof result.decisions === 'object') {
        const rebuilt = new Map();
        for (const [id, decision] of Object.entries(result.decisions)) {
            const n = Number(id);
            if (Number.isInteger(n) && n > 0 && (decision === 'keep' || decision === 'reject')) {
                rebuilt.set(n, decision);
            }
        }
        ManualSortState.cullDecisions = rebuilt;
    }

    if (result?.done) {
        finishCullSorting(result);
        return false;
    }

    const image = result?.image?.image || null;
    ManualSortState.currentImage = image;
    ManualSortState.currentTags = result?.image?.tags || [];
    ManualSortState.index = Number(result?.index ?? 0);
    ManualSortState.total = Number(result?.total ?? 0);

    const cacheSuffix = options.cacheBust ? `?t=${Date.now()}` : '';
    const img = $('#cull-image');
    if (img) img.src = image?.id ? API.getImageUrl(image.id) + cacheSuffix : '';

    renderBracketFighterName('#cull-name', image);
    renderBracketMeta('#cull-meta', image);
    updateCullProgress(result);

    const undoBtn = $('#cull-btn-undo');
    if (undoBtn) undoBtn.disabled = !result?.undo_available;
    const redoBtn = $('#cull-btn-redo');
    if (redoBtn) redoBtn.disabled = !result?.redo_available;

    return true;
}

function updateCullProgress(result) {
    const { $ } = window.App;
    const total = Number(result?.total ?? ManualSortState.total ?? 0);
    const index = Number(result?.index ?? ManualSortState.index ?? 0);
    const kept = Number(result?.kept ?? 0);
    const rejected = Number(result?.rejected ?? 0);

    const fill = $('#cull-progress-fill');
    if (fill) fill.style.width = total ? `${Math.min(100, (index / total) * 100)}%` : '0%';
    const text = $('#cull-progress-text');
    if (text) text.textContent = `${Math.min(index + 1, total)} / ${total}`;
    // Write only the count spans: the sibling glyph and screen-reader word are
    // the non-colour keep/reject distinction and must survive every update.
    const keepTally = $('#cull-tally-keep-count');
    if (keepTally) keepTally.textContent = String(kept);
    const rejTally = $('#cull-tally-reject-count');
    if (rejTally) rejTally.textContent = String(rejected);
}

// Brief keep/reject/skip stamp animation on the card.
function flashCullStamp(action) {
    const { $ } = window.App;
    const card = $('#cull-card');
    if (!card) return;
    card.classList.remove('cull-flash-keep', 'cull-flash-reject', 'cull-flash-skip');
    const cls = action === 'keep' ? 'cull-flash-keep'
        : action === 'reject' ? 'cull-flash-reject'
        : action === 'skip' ? 'cull-flash-skip' : null;
    if (!cls) return;
    void card.offsetWidth; // reflow so the animation restarts each time
    card.classList.add(cls);
    setTimeout(() => card.classList.remove(cls), 360);
}

// Maintain the client-side decision map from the server's action response so
// finish can route kept→keep dest / rejected→reject dest. Forward keep/reject
// set the decision; skip clears it; undo reverts the affected image; redo
// re-applies the entry's decision. (image_id + decision come back per action.)
function applyCullDecisionFromResult(action, result) {
    if (!ManualSortState.cullDecisions) ManualSortState.cullDecisions = new Map();
    const map = ManualSortState.cullDecisions;
    const id = Number(result?.image_id);
    if (!Number.isInteger(id) || id <= 0) return;
    if (action === 'keep' || action === 'reject') {
        map.set(id, action);
    } else if (action === 'skip' || action === 'undo') {
        map.delete(id);
    } else if (action === 'redo') {
        const decision = result?.decision;
        if (decision === 'keep' || decision === 'reject') map.set(id, decision);
        else map.delete(id);
    }
}

async function performCullAction(action, fast = false) {
    const { API, showToast } = window.App;
    if (!ManualSortState.active || ManualSortState.mode !== 'cull') return;

    const isHistory = action === 'undo' || action === 'redo';
    if (!isHistory) {
        if (ManualSortState.isProcessing) { flashManualSortBusy(); return; }
        if (isManualSortInCooldown()) { flashManualSortBusy(); return; }
    }
    ManualSortState.isProcessing = true;

    try {
        if (action === 'keep') window.AudioManager?.play('move', 'd');
        else if (action === 'reject') window.AudioManager?.play('move', 'a');
        else if (action === 'skip') window.AudioManager?.play('skip');
        else window.AudioManager?.play('undo');
        flashCullStamp(action);

        const result = await API.sortAction(action);
        if (result?.error) {
            updateHistoryControlState(result);
            showToast(result.error, 'error');
            return;
        }

        applyCullDecisionFromResult(action, result);

        if (!isHistory) {
            ManualSortState.actionTimestamps.push(Date.now());
            const cutoff = Date.now() - 30000;
            ManualSortState.actionTimestamps = ManualSortState.actionTimestamps.filter(t => t > cutoff);
        }

        // A cull action returns only status flags — reload fresh so the next
        // image (and tally) render.
        await loadCurrentImage();
    } catch (error) {
        Logger.error('Cull action failed:', error);
        showToast(manualSortText('manual.cullActionFailed', 'Action failed', '操作失败'), 'error');
    } finally {
        ManualSortState.isProcessing = false;
        ManualSortState.lastActionCompletedAt = Date.now();
    }
}

// Route the tracked decisions to their destinations by reference (non-destructive).
async function collectCullDecisions() {
    const { API } = window.App;
    const map = ManualSortState.cullDecisions || new Map();
    const keepDest = getCullDest('keep');
    const rejectDest = getCullDest('reject');

    let attempted = 0;
    let failed = 0;

    // Returns false only when a real write throws, so finishCullSorting can
    // report honestly instead of always showing a green success toast even
    // when every keep/reject failed (e.g. the destination collection was
    // deleted mid-session). A falsy/invalid dest is "nothing to write", not a
    // failure.
    const route = async (id, dest) => {
        if (!dest) return true;
        const cid = dest === 'fav' ? null : Number(dest);
        if (dest !== 'fav' && (!Number.isInteger(cid) || cid <= 0)) return true;
        attempted += 1;
        try {
            if (dest === 'fav') await API.setFavorite(id, true);
            else await API.setCollectionMembership(cid, id, true);
            return true;
        } catch (e) {
            failed += 1;
            if (window.Logger) Logger.error('Failed to route cull decision:', e);
            return false;
        }
    };

    for (const [id, decision] of map.entries()) {
        if (decision === 'keep') await route(id, keepDest);
        else if (decision === 'reject') await route(id, rejectDest);
    }
    return { attempted, failed };
}

async function finishCullSorting(result) {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    ManualSortState.undoAvailable = false;
    ManualSortState.redoAvailable = false;
    document.removeEventListener('keydown', handleSortKeypress);
    updateHistoryControlState({ undo_available: false, redo_available: false });

    window.AudioManager?.play('finish');

    hideSortInterfaces();
    $('#sort-setup').style.display = 'block';

    const map = ManualSortState.cullDecisions || new Map();
    let keptCount = 0;
    let rejectedCount = 0;
    for (const decision of map.values()) {
        if (decision === 'keep') keptCount += 1;
        else if (decision === 'reject') rejectedCount += 1;
    }

    const routeStats = await collectCullDecisions();

    if (routeStats.failed > 0) {
        showToast(
            formatManualSortText(
                'manual.cullCompletePartial',
                'Cull done — kept {kept}, rejected {rejected}, but {failed} could not be saved to your collections/favorites.',
                '整理完成 — 保留 {kept}、剔除 {rejected}，但有 {failed} 张未能写入收藏夹/收藏。',
                { kept: keptCount, rejected: rejectedCount, failed: routeStats.failed }
            ),
            'warning'
        );
    } else {
        showToast(
            formatManualSortI18n(
                'manual.cullComplete',
                'Cull complete — kept {kept}, rejected {rejected}.',
                { kept: keptCount, rejected: rejectedCount }
            ),
            'success'
        );
    }

    window.App.API.delete('/api/sort/session').catch(e => {
        if (window.Logger) Logger.warn('Failed to clean up cull session:', e);
    });

    ManualSortState.cullDecisions = new Map();

    if (window.App && window.App.loadImages) {
        window.App.loadImages();
    }
}

function handleCullKeypress(e) {
    const key = e.key;
    let action = null;
    if (key === 'ArrowRight' || key === 'd' || key === 'D' || key === 'k' || key === 'K') action = 'keep';
    else if (key === 'ArrowLeft' || key === 'a' || key === 'A' || key === 'x' || key === 'X') action = 'reject';
    else if (key === ' ' || key === 'ArrowUp' || key === 'w' || key === 'W' || key === 'ArrowDown' || key === 's' || key === 'S') action = 'skip';
    else if (key === 'z' || key === 'Z') action = 'undo';
    else if (key === 'y' || key === 'Y') action = 'redo';
    else if (key === 'Escape') { e.preventDefault(); exitSorting(); return; }

    if (!action) return;
    e.preventDefault();
    performCullAction(action, Boolean(e.repeat));
}

