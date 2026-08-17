/**
 * manual-sort/bracket.js — manual-sort.js decomposition.
 * Extracted VERBATIM (byte-identical) from frontend/js/manual-sort.js,
 * pre-split lines 2042-2611 (the entire A/B Showdown block, v3.3.2 WB-S3):
 * startBracketSorting, fighter meta/diff rendering, the synchronized
 * pixel-peep zoom, payload render, performBracketAction, winner routing,
 * finishBracketSorting and the bracket key map. Classic script: loads after
 * manual-sort/state-constants.js (base).
 */
// ============== A/B Showdown (bracket) — v3.3.2 WB-S3 ==============

// Folder-free start path for A/B Showdown. Mirrors startSorting's filter
// building but skips destination folders (bracket is non-destructive culling)
// and the move/copy confirmation.
async function startBracketSorting() {
    const { $, API, showToast } = window.App;

    // Resume any unfinished session in its own mode rather than clobbering it.
    try {
        const existing = await API.getCurrentSortImage();
        const hasActive = existing && !existing.done && (existing.image || existing.champion);
        if (hasActive) {
            if (existing.mode === 'bracket') {
                ManualSortState.startTime = Date.now();
                ManualSortState.history = [];
                ManualSortState.actionTimestamps = [];
                activateSortingUi('bracket');
                applyCurrentSortPayload(existing);
                showToast(manualSortText('manual.bracketResumed', 'Resumed your A/B Showdown.', '已恢复 A/B 擂台。'), 'info');
            } else {
                await confirmCrossModeSavedSession(existing, 'bracket');
            }
            return;
        }
    } catch (error) {
        if (window.Logger) Logger.warn('Failed to check existing session before bracket start:', error);
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
            generators,
            tags,
            ratings,
            {}, // no destination folders for bracket
            checkpoints,
            loras,
            prompts,
            dimensions,
            search,
            { min: f.minAesthetic, max: f.maxAesthetic },
            'copy', // operation mode is irrelevant; bracket does not move files
            f.artist,
            false,
            f.promptMatchMode,
            f.tagMode,
            {
                tags: f.excludeTags?.length > 0 ? f.excludeTags : null,
                generators: f.excludeGenerators?.length > 0 ? f.excludeGenerators : null,
                ratings: f.excludeRatings?.length > 0 ? f.excludeRatings : null,
                checkpoints: f.excludeCheckpoints?.length > 0 ? f.excludeCheckpoints : null,
                loras: f.excludeLoras?.length > 0 ? f.excludeLoras : null,
            },
            null, // collection slots
            'bracket',
            buildManualSortScopeFilters(f),
        );

        const totalImages = Number(result?.total_images ?? 0);
        if (totalImages === 0) {
            showToast(manualSortText('manual.noImages', 'No images match Manual Sort filters', '没有图片匹配手动排序筛选'), 'error');
            return;
        }
        if (totalImages < 2) {
            showToast(manualSortText('manual.bracketNeedTwo', 'A/B Showdown needs at least 2 images to compare.', 'A/B 擂台至少需要 2 张图片才能比较。'), 'error');
            return;
        }

        // Fresh session bookkeeping.
        ManualSortState.startTime = Date.now();
        ManualSortState.history = [];
        ManualSortState.images = [];
        ManualSortState.currentImage = null;
        ManualSortState.currentTags = [];
        ManualSortState.actionTimestamps = [];
        ManualSortState.sortedCount = 0;
        ManualSortState.skippedCount = 0;
        ManualSortState.combo = 0;
        ManualSortState.bracketStreak = 1;
        ManualSortState.bracketLastChampIndex = 0;

        activateSortingUi('bracket');
        setBracketZoomActive(false);
        await loadCurrentImage();
    } catch (error) {
        rollbackSortingUi();
        Logger.error('Failed to start A/B Showdown:', error);
        showToast(formatUserError(error, manualSortText('manual.bracketStartFailed', 'Failed to start A/B Showdown', '开始 A/B 擂台失败')), 'error');
    }
}

function bracketImageName(image) {
    if (!image) return '';
    if (image.filename) return image.filename;
    if (image.path) return String(image.path).split(/[\\/]/).pop();
    return image.id ? `#${image.id}` : '';
}

function renderBracketFighterName(selector, image) {
    const { $ } = window.App;
    const el = $(selector);
    if (!el) return;
    el.textContent = bracketImageName(image);
}

// v3.3.2 WB-S4: per-fighter generation-param chips — the SD judging context
// Eagle/Billfish can't show. Reuses Gallery's synchronous metadata parser so
// sampler/cfg/steps/seed come from the same source as the detail view.
function bracketMetaChipsHtml(image) {
    if (!image) return '';
    let gp = {};
    try {
        const parsed = window.Gallery && typeof window.Gallery._extractParsedData === 'function'
            ? window.Gallery._extractParsedData(image)
            : null;
        gp = (parsed && parsed.generation_params) || {};
    } catch (_) { gp = {}; }

    const chips = [];
    const chip = (text) => { if (text != null && String(text).trim() !== '') chips.push(`<span class="bchip">${escapeHtml(String(text))}</span>`); };
    const labeled = (label, val) => { if (val != null && String(val).trim() !== '') chips.push(`<span class="bchip"><b>${escapeHtml(label)}</b> ${escapeHtml(String(val))}</span>`); };

    if (gp.sampler) chip(gp.sampler);
    labeled('CFG', gp.cfg_scale);
    labeled('Steps', gp.steps);
    labeled('Seed', gp.seed);
    if (image.checkpoint) {
        const ckpt = String(image.checkpoint).split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|pth)$/i, '');
        chip(ckpt);
    }
    if (image.width && image.height) chip(`${image.width}×${image.height}`);
    if (image.aesthetic_score != null && image.aesthetic_score !== '') {
        const score = Number(image.aesthetic_score);
        if (Number.isFinite(score)) labeled('★', score.toFixed(1));
    }
    return chips.join('');
}

function renderBracketMeta(selector, image) {
    const { $ } = window.App;
    const el = $(selector);
    if (!el) return;
    el.innerHTML = bracketMetaChipsHtml(image);
}

// Normalize a sampler/scheduler label so the SAME choice from different
// generators isn't reported as a difference: A1111 "DPM++ 2M" vs ComfyUI
// "dpmpp_2m" vs NAI "k_euler_ancestral". Display still uses the raw value;
// only the same/diff decision is normalized. (Some A1111 versions fold the
// scheduler into the sampler name, so cross-generator matching is best-effort.)
function normalizeSamplerForCompare(value) {
    if (value == null) return null;
    let s = String(value).toLowerCase().trim();
    if (!s) return null;
    s = s.replace(/\+\+/g, 'pp');        // dpm++ -> dpmpp
    s = s.replace(/ancestral/g, 'a');    // "euler ancestral" -> "euler a"
    s = s.replace(/[\s_]+/g, '');         // collapse spaces / underscores
    s = s.replace(/^k(?=euler|dpmpp|dpm|heun|lms)/, ''); // NAI "k_euler" -> "euler"
    return s || null;
}

// v3.3.2 WB-S5: comparable generation params for the metadata-diff strip.
// Ordered so the strip reads the way 炼丹 users scan params.
function bracketComparableParams(image) {
    let gp = {};
    try {
        const parsed = window.Gallery && typeof window.Gallery._extractParsedData === 'function'
            ? window.Gallery._extractParsedData(image)
            : null;
        gp = (parsed && parsed.generation_params) || {};
    } catch (_) { gp = {}; }

    const ckpt = image && image.checkpoint
        ? String(image.checkpoint).split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|pth)$/i, '')
        : null;
    const norm = (v) => (v == null || String(v).trim() === '' ? null : String(v).trim());

    // Scheduler is stored under a different key per generator: ComfyUI
    // "scheduler", A1111/WebUI "schedule_type", NovelAI "noise_schedule".
    const sched = norm(gp.scheduler != null ? gp.scheduler
        : (gp.schedule_type != null ? gp.schedule_type : gp.noise_schedule));
    const sampler = norm(gp.sampler);
    // genParam flags the true SD generation params (vs structural model/size) so
    // the strip can tell "params match" apart from "no SD metadata at all".
    return [
        { key: 'Sampler', value: sampler, cmp: normalizeSamplerForCompare(sampler), genParam: true },
        { key: 'CFG', value: norm(gp.cfg_scale), genParam: true },
        { key: 'Steps', value: norm(gp.steps), genParam: true },
        { key: 'Seed', value: norm(gp.seed), genParam: true },
        { key: 'Scheduler', value: sched, cmp: normalizeSamplerForCompare(sched), genParam: true },
        { key: 'Clip skip', value: norm(gp.clip_skip), genParam: true },
        { key: 'Denoise', value: norm(gp.denoising_strength != null ? gp.denoising_strength : gp.denoise), genParam: true },
        { key: 'Model', value: norm(ckpt), genParam: false },
        { key: 'Size', value: (image && image.width && image.height) ? `${image.width}×${image.height}` : null, genParam: false },
    ];
}

// Render the only-show-differences strip between champion (A) and challenger (B).
function renderBracketDiff(champImage, challImage) {
    const { $ } = window.App;
    const strip = $('#bracket-diff');
    if (!strip) return;

    if (!champImage || !challImage) {
        strip.hidden = true;
        strip.innerHTML = '';
        return;
    }

    const a = bracketComparableParams(champImage);
    const b = bracketComparableParams(challImage);
    const bByKey = {};
    b.forEach((p) => { bByKey[p.key] = p; });

    const diffs = [];
    const sames = [];
    let comparableGenParams = 0; // SD generation params present on either side
    a.forEach((p) => {
        const bp = bByKey[p.key] || {};
        const av = p.value;
        const bv = bp.value;
        if (av == null && bv == null) return;        // neither side has this field
        if (p.genParam) comparableGenParams += 1;
        const ac = p.cmp != null ? p.cmp : av;       // normalized compare key (sampler/scheduler)
        const bc = bp.cmp != null ? bp.cmp : bv;
        if (ac === bc) { sames.push(p.key); return; }
        diffs.push({ key: p.key, a: av == null ? '—' : av, b: bv == null ? '—' : bv });
    });

    const parts = [`<span class="bd-label">${escapeHtml(manualSortText('manual.bracketDiffLabel', 'Differences only', '只显示差异'))}</span>`];
    if (diffs.length > 0) {
        diffs.forEach((d) => {
            parts.push(
                `<span class="bd-chip"><b>${escapeHtml(d.key)}</b>`
                + `<span class="bd-a">${escapeHtml(d.a)}</span>`
                + `<span class="bd-arrow">→</span>`
                + `<span class="bd-b">${escapeHtml(d.b)}</span></span>`
            );
        });
    } else if (comparableGenParams === 0) {
        // Neither image carries SD generation metadata (e.g. un-parsed images);
        // claiming "same params" would be misleading, so be honest instead.
        parts.push(`<span class="bd-none">${escapeHtml(manualSortText('manual.bracketDiffNoMeta', 'No SD generation metadata to compare', '没有可对比的 SD 生成参数'))}</span>`);
    } else {
        parts.push(`<span class="bd-none">${escapeHtml(manualSortText('manual.bracketDiffNone', 'Same generation params', '生成参数相同'))}</span>`);
    }
    if (sames.length > 0) {
        parts.push(
            `<span class="bd-same">${escapeHtml(formatManualSortText('manual.bracketDiffSame', 'same: {keys}', '相同: {keys}', { keys: sames.join(' · ') }))}</span>`
        );
    }

    strip.innerHTML = parts.join('');
    strip.hidden = false;
}

// v3.3.2 WB-S5: synchronized pixel-peep zoom. Moving over either fighter zooms
// BOTH images to the same PICTURE point (corrected for object-fit letterboxing)
// so fine detail compares 1:1 even when the two images differ in aspect ratio.
const BRACKET_ZOOM_SCALE = 2.6;

// The rendered (letterboxed) rect of an object-fit:contain image inside a box
// of bw×bh — used to map between cursor/box space and picture space.
function containedImageRect(naturalW, naturalH, bw, bh) {
    if (!naturalW || !naturalH || !bw || !bh) return null;
    const scale = Math.min(bw / naturalW, bh / naturalH);
    const w = naturalW * scale;
    const h = naturalH * scale;
    return { left: (bw - w) / 2, top: (bh - h) / 2, width: w, height: h };
}

function setBracketZoomActive(active) {
    const { $ } = window.App;
    ManualSortState.bracketZoom = !!active;
    const btn = $('#bracket-btn-zoom');
    if (btn) btn.setAttribute('aria-pressed', String(!!active));
    const duel = document.querySelector('.bracket-duel');
    if (duel) duel.classList.toggle('zooming', !!active);
    if (!active) applyBracketZoom(null, null);
}

// normX/normY are PICTURE-space coordinates in [0,1] (where in the actual image
// content the cursor points). Each fighter maps that picture point back to its
// OWN box-relative transform-origin, correcting for object-fit:contain
// letterboxing, so both images zoom to the same picture coordinate.
function applyBracketZoom(normX, normY) {
    const { $ } = window.App;
    const imgs = [$('#bracket-champion-image'), $('#bracket-challenger-image')];
    imgs.forEach((img) => {
        if (!img) return;
        if (normX == null || normY == null) {
            img.style.transform = '';
            img.style.transformOrigin = '';
            return;
        }
        const rect = img.getBoundingClientRect();
        const r = containedImageRect(img.naturalWidth, img.naturalHeight, rect.width, rect.height);
        let oxPct = normX * 100;
        let oyPct = normY * 100;
        if (r && rect.width && rect.height) {
            oxPct = ((r.left + normX * r.width) / rect.width) * 100;
            oyPct = ((r.top + normY * r.height) / rect.height) * 100;
        }
        img.style.transformOrigin = `${oxPct.toFixed(2)}% ${oyPct.toFixed(2)}%`;
        img.style.transform = `scale(${BRACKET_ZOOM_SCALE})`;
    });
}

function handleBracketZoomMove(e) {
    if (!ManualSortState.bracketZoom) return;
    const fighter = e.currentTarget;
    const img = fighter.tagName === 'IMG' ? fighter : fighter.querySelector('img');
    const rect = (img || fighter).getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    // Cursor position within the image element box.
    const bx = e.clientX - rect.left;
    const by = e.clientY - rect.top;
    // Convert to picture-space via the hovered image's letterboxed rect, so the
    // focal point is "where in the picture" rather than "where in the box".
    const r = img ? containedImageRect(img.naturalWidth, img.naturalHeight, rect.width, rect.height) : null;
    let normX;
    let normY;
    if (r && r.width && r.height) {
        normX = Math.min(1, Math.max(0, (bx - r.left) / r.width));
        normY = Math.min(1, Math.max(0, (by - r.top) / r.height));
    } else {
        normX = Math.min(1, Math.max(0, bx / rect.width));
        normY = Math.min(1, Math.max(0, by / rect.height));
    }
    applyBracketZoom(normX, normY);
}

// v3.3.2 WB-S4: brief highlight on the chosen fighter for tactile feedback.
function flashBracketPick(action) {
    const { $ } = window.App;
    const id = action === 'champion' ? '#bracket-champion'
        : action === 'challenger' ? '#bracket-challenger'
        : null;
    if (!id) return;
    const el = $(id);
    if (!el) return;
    el.classList.remove('is-picked');
    void el.offsetWidth; // force reflow so the animation restarts
    el.classList.add('is-picked');
    setTimeout(() => el.classList.remove('is-picked'), 240);
}

function updateBracketProgress(result) {
    const { $ } = window.App;
    const total = Number(result?.total ?? ManualSortState.total ?? 0);
    const comparisonsTotal = Number(result?.comparisons_total ?? Math.max(0, total - 1));
    const challengerIndex = Number(result?.challenger_index ?? result?.index ?? 0);
    const decided = Math.max(0, Math.min(challengerIndex - 1, comparisonsTotal));
    const pct = comparisonsTotal > 0 ? (decided / comparisonsTotal) * 100 : 0;

    const fill = $('#bracket-progress-fill');
    if (fill) fill.style.width = `${pct}%`;
    const text = $('#bracket-progress-text');
    if (text) text.textContent = `${decided} / ${comparisonsTotal}`;

    // v3.3.2 WB-S4: champion win-streak (only once the champ has held ≥2 rounds).
    const streakEl = $('#bracket-streak');
    if (streakEl) {
        const streak = Number(ManualSortState.bracketStreak || 0);
        streakEl.textContent = streak >= 2
            ? formatManualSortI18n('manual.bracketStreak', 'Streak ×{n}', { n: streak })
            : '';
    }
}

// Renders the current champion/challenger pair. Returns false when the bracket
// is finished (so callers mirror applyCurrentSortPayload's contract).
function applyBracketPayload(result, options = {}) {
    const { $, API } = window.App;
    ManualSortState.mode = 'bracket';

    updateHistoryControlState(result || {});

    if (result?.done) {
        finishBracketSorting(result);
        return false;
    }

    const champion = result?.champion?.image || null;
    const challenger = result?.challenger?.image || null;
    ManualSortState.currentImage = challenger;
    ManualSortState.index = Number(result?.challenger_index ?? result?.index ?? 0);
    ManualSortState.total = Number(result?.total ?? 0);

    // v3.3.2 WB-S4: champion win-streak. Same champion index across loads means
    // the champ held the crown another round.
    const champIdx = Number(result?.champion_index ?? 0);
    if (ManualSortState.bracketLastChampIndex === champIdx) {
        ManualSortState.bracketStreak = (ManualSortState.bracketStreak || 1) + 1;
    } else {
        ManualSortState.bracketStreak = 1;
    }
    ManualSortState.bracketLastChampIndex = champIdx;

    const cacheSuffix = options.cacheBust ? `?t=${Date.now()}` : '';
    const champImg = $('#bracket-champion-image');
    if (champImg) champImg.src = champion?.id ? API.getImageUrl(champion.id) + cacheSuffix : '';
    const challImg = $('#bracket-challenger-image');
    if (challImg) challImg.src = challenger?.id ? API.getImageUrl(challenger.id) + cacheSuffix : '';

    renderBracketFighterName('#bracket-champion-name', champion);
    renderBracketFighterName('#bracket-challenger-name', challenger);
    renderBracketMeta('#bracket-champion-meta', champion);
    renderBracketMeta('#bracket-challenger-meta', challenger);
    renderBracketDiff(champion, challenger);
    // Each new pair starts un-zoomed; a mouse move re-applies if zoom is on.
    applyBracketZoom(null, null);
    updateBracketProgress(result);

    const undoBtn = $('#bracket-btn-undo');
    if (undoBtn) undoBtn.disabled = !result?.undo_available;
    const redoBtn = $('#bracket-btn-redo');
    if (redoBtn) redoBtn.disabled = !result?.redo_available;

    return true;
}

async function performBracketAction(action, fast = false) {
    const { API, showToast } = window.App;
    if (!ManualSortState.active || ManualSortState.mode !== 'bracket') return;

    const isHistory = action === 'undo' || action === 'redo';
    if (!isHistory) {
        if (ManualSortState.isProcessing) { flashManualSortBusy(); return; }
        if (isManualSortInCooldown()) { flashManualSortBusy(); return; }
    }
    ManualSortState.isProcessing = true;

    try {
        // v3.3.2 WB-S4: directional pick sfx (left/right pitch) + brief highlight.
        if (action === 'skip') {
            window.AudioManager?.play('skip');
        } else if (isHistory) {
            window.AudioManager?.play('undo');
        } else {
            window.AudioManager?.play('move', action === 'champion' ? 'a' : 'd');
        }
        flashBracketPick(action);

        const result = await API.sortAction(action);
        if (result?.error) {
            updateHistoryControlState(result);
            showToast(result.error, 'error');
            return;
        }

        if (!isHistory) {
            ManualSortState.actionTimestamps.push(Date.now());
            const cutoff = Date.now() - 30000;
            ManualSortState.actionTimestamps = ManualSortState.actionTimestamps.filter(t => t > cutoff);
        }

        // A bracket action returns only status flags/indices, never the next
        // pair — reload fresh so the new champion/challenger render.
        await loadCurrentImage();
    } catch (error) {
        Logger.error('Bracket action failed:', error);
        showToast(manualSortText('manual.bracketActionFailed', 'Action failed', '操作失败'), 'error');
    } finally {
        ManualSortState.isProcessing = false;
        ManualSortState.lastActionCompletedAt = Date.now();
    }
}

// v3.3.2 WB-S6: route the showdown winner to its chosen destination by
// reference (non-destructive). Returns a display label for the toast, or null
// when nothing was saved. '' = don't save, 'fav' = Favorites, else collection id.
async function collectBracketWinner(winnerImage) {
    const { API, showToast } = window.App;
    const winnerId = winnerImage && winnerImage.id;
    if (!winnerId) return null;
    const dest = getBracketWinnerDest();
    if (!dest) return null;

    try {
        if (dest === 'fav') {
            await API.setFavorite(winnerId, true);
            return manualSortText('manual.bracketWinnerFav', 'Favorites', '收藏');
        }
        const collectionId = Number(dest);
        if (!Number.isInteger(collectionId) || collectionId <= 0) return null;
        await API.setCollectionMembership(collectionId, winnerId, true);
        const match = (ManualSortState.collectionsCache || []).find((c) => c.id === collectionId);
        return match ? match.name : `#${collectionId}`;
    } catch (error) {
        Logger.error('Failed to save showdown winner:', error);
        showToast(manualSortText('manual.bracketWinnerFailed', 'Failed to save the winner', '保存冠军失败'), 'error');
        return null;
    }
}

async function finishBracketSorting(result) {
    const { $, showToast } = window.App;

    ManualSortState.active = false;
    ManualSortState.undoAvailable = false;
    ManualSortState.redoAvailable = false;
    document.removeEventListener('keydown', handleSortKeypress);
    updateHistoryControlState({ undo_available: false, redo_available: false });

    window.AudioManager?.play('finish');
    setBracketZoomActive(false);

    const winner = result?.winner?.image || result?.champion?.image || null;
    const winnerName = bracketImageName(winner);

    hideSortInterfaces();
    $('#sort-setup').style.display = 'block';

    const destLabel = await collectBracketWinner(winner);

    if (winnerName && destLabel) {
        showToast(
            formatManualSortI18n('manual.bracketWinnerSaved', 'Winner {name} → {dest}', { name: winnerName, dest: destLabel }),
            'success'
        );
    } else if (winnerName) {
        showToast(
            formatManualSortI18n('manual.bracketWinner', 'Showdown complete — winner: {name}', { name: winnerName }),
            'success'
        );
    } else {
        showToast(manualSortText('manual.bracketComplete', 'Showdown complete.', '擂台结束。'), 'success');
    }

    window.App.API.delete('/api/sort/session').catch(e => {
        if (window.Logger) Logger.warn('Failed to clean up bracket session:', e);
    });

    if (window.App && window.App.loadImages) {
        window.App.loadImages();
    }
}

function handleBracketKeypress(e) {
    const key = e.key;
    let action = null;
    if (key === 'ArrowLeft' || key === 'a' || key === 'A') action = 'champion';
    else if (key === 'ArrowRight' || key === 'd' || key === 'D') action = 'challenger';
    else if (key === ' ' || key === 'ArrowUp' || key === 'w' || key === 'W') action = 'skip';
    else if (key === 'z' || key === 'Z') action = 'undo';
    else if (key === 'y' || key === 'Y') action = 'redo';
    else if (key === 'Escape') { e.preventDefault(); exitSorting(); return; }

    if (!action) return;
    e.preventDefault();
    performBracketAction(action, Boolean(e.repeat));
}

