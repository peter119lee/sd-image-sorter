/**
 * app/stats-aesthetic.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 5795-6083 (of 10,152): loadStats + aesthetic scoring status/poll/start.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
// ============== Stats ==============

async function loadStats() {
    try {
        const stats = await API.getStats();

        // Update generator counts in tabs
        let totalCount = 0;
        const genCounts = {};
        stats.generators.forEach(gen => {
            genCounts[gen.generator] = gen.count;
            totalCount += gen.count;

            // Legacy checkbox count update
            const countEl = $(`.checkbox-count[data-generator="${gen.generator}"]`);
            if (countEl) {
                countEl.textContent = gen.count;
            }
        });

        const metadataPending = Number(stats.metadata_pending || stats.metadata_status?.pending || stats.metadata_status_counts?.pending || 0);
        const scanStatus = String(stats.scan_status || '').toLowerCase();
        const scanRunning = scanStatus === 'running' || scanStatus === 'cancelling';
        const scanLibraryReady = stats.scan_library_ready === true;
        const countsResolving = metadataPending > 0 || (scanRunning && !scanLibraryReady);
        const reportedTotal = Number.isFinite(Number(stats.total_images))
            ? Number(stats.total_images)
            : totalCount;

        // Update generator tab counts
        const countAll = $('#count-all');
        if (countAll) countAll.textContent = reportedTotal;
        // The image-count label renders "shown / library total" off this very
        // number, and stats can land after the gallery load — re-render it so
        // the pair never disagrees just because of arrival order.
        if (typeof applyGalleryCountLabel === 'function' && !AppState.isLoading) {
            applyGalleryCountLabel();
        }

        ['nai', 'comfyui', 'forge', 'webui', 'unknown'].forEach(gen => {
            const countEl = $(`#count-${gen}`);
            if (countEl) {
                const count = genCounts[gen] || 0;
                countEl.textContent = countsResolving && count === 0 ? '…' : String(count);
                countEl.title = countsResolving
                    ? appT('gallery.metadataResolvingTitle', 'Generator counts are still resolving while metadata is being read or scan import is still running.')
                    : '';
            }
        });

        // The "Others" tab bundles every uncommon generator (Fooocus,
        // reForge, Gemini, gpt-image, ...) — its count must reflect the
        // sum so the badge matches the gallery once the user clicks it.
        const othersCount = OTHERS_GENERATOR_BUNDLE.reduce(
            (sum, gen) => sum + (genCounts[gen] || 0),
            0
        );
        const countOthersEl = $('#count-others');
        const othersTab = $('.gen-tab[data-gen="others"]');
        const activeOtherGenerators = OTHERS_GENERATOR_BUNDLE
            .filter((gen) => (genCounts[gen] || 0) > 0)
            .map((gen) => `${formatGeneratorLabel(gen)} (${genCounts[gen]})`);
        const othersHint = activeOtherGenerators.length > 0
            ? appT('generator.othersActiveHint', 'Grouped generators: {generators}', {
                generators: activeOtherGenerators.join(', '),
            }).replace('{generators}', activeOtherGenerators.join(', '))
            : appT('generator.othersHint', 'Groups uncommon generators');
        if (countOthersEl) {
            countOthersEl.textContent = countsResolving && othersCount === 0 ? '…' : String(othersCount);
            countOthersEl.title = countsResolving
                ? appT('gallery.metadataResolvingTitle', 'Generator counts are still resolving while metadata is being read or scan import is still running.')
                : othersHint;
        }
        if (othersTab) othersTab.title = othersHint;
        syncGeneratorRailOverflow();

        const metadataChip = $('#metadata-status-chip');
        if (metadataChip) {
            if (countsResolving) {
                metadataChip.textContent = metadataPending > 0
                    ? appT('gallery.metadataResolving', 'Reading image info: {count} pending')
                        .replace('{count}', String(metadataPending))
                    : appT('gallery.scanResolving', 'Scanning library: generator counts are not final yet');
                metadataChip.title = appT('gallery.metadataResolvingTitle', 'Generator counts are still resolving while metadata is being read or scan import is still running.');
                metadataChip.hidden = false;
            } else {
                metadataChip.textContent = '';
                metadataChip.title = '';
                metadataChip.hidden = true;
            }
        }

        // Populate version badge
        if (stats.app_version) {
            const vBadge = document.getElementById('brand-version');
            if (vBadge) vBadge.textContent = 'v' + stats.app_version;
            AppState.appVersion = stats.app_version;
            AppState.githubUrl = stats.github_url || '';
        }

        // Store analytics for later use
        AppState.analytics = {
            checkpoints: stats.checkpoints || [],
            loras: stats.loras || [],
            top_tags: stats.top_tags || [],
            generatorCounts: genCounts,
            totalImages: reportedTotal,
            metadataPending,
            metadataStatus: stats.metadata_status || stats.metadata_status_counts || {},
            countsResolving,
            scanStatus,
            scanLibraryReady
        };

        // Update model filters summary UI
        updateModelSelectionSummaries();

    } catch (error) {
        Logger.error('Failed to load stats:', error);
    }
}

let _aestheticStatus = { available: false, message: '' };
let _aestheticStatusGeneration = 0;
let _aestheticProgressTimer = null;
let _aestheticProgressGeneration = 0;
let _aestheticStartRequestPending = false;
let _aestheticUiState = {
    running: false,
    starting: false,
    completed: 0,
    total: 0,
};

function clearAestheticProgressTimer() {
    _aestheticProgressGeneration += 1;
    if (_aestheticProgressTimer) {
        clearTimeout(_aestheticProgressTimer);
        _aestheticProgressTimer = null;
    }
}

function normalizeAestheticUiState(state) {
    return {
        running: Boolean(state?.running),
        starting: Boolean(state?.starting),
        completed: Number(state?.completed || 0),
        total: Number(state?.total || 0),
    };
}

function renderAestheticUi(state) {
    const startButtons = [
        $('#btn-score-aesthetic'),
        $('#btn-tagger-aesthetic-start'),
    ].filter(Boolean);
    const cancelButtons = [
        $('#btn-cancel-aesthetic'),
        $('#btn-tagger-aesthetic-cancel'),
    ].filter(Boolean);
    const chip = $('#aesthetic-status-chip');
    if (startButtons.length === 0) return;

    const t = (key, fallback, params) => {
        const translated = window.I18n?.t?.(key, params);
        return translated && translated !== key ? translated : (fallback || key);
    };
    const busy = state.starting || state.running;

    for (const cancelButton of cancelButtons) {
        cancelButton.style.display = state.running ? '' : 'none';
        cancelButton.disabled = !state.running;
    }

    if (!_aestheticStatus.available && !busy) {
        const installTitle = t('gallery.aestheticInstallOnUse', 'First use downloads aesthetic scoring (about 1.7 GB) and shows progress.');
        for (const startButton of startButtons) {
            startButton.disabled = false;
            startButton.title = installTitle;
            startButton.setAttribute('aria-label', installTitle);
        }
        for (const cancelButton of cancelButtons) {
            cancelButton.style.display = 'none';
            cancelButton.disabled = true;
        }
        if (chip) {
            chip.style.display = 'inline-flex';
            chip.className = 'tagger-aesthetic-status is-warning';
            chip.textContent = t('gallery.aestheticInstallShort', 'Downloads on first use');
            chip.title = installTitle;
        }
        return;
    }

    const startTitle = busy
        ? t('gallery.aestheticRunning', 'Scoring aesthetics...')
        : t('gallery.scoreAesthetic', 'Score Aesthetic');
    for (const startButton of startButtons) {
        startButton.disabled = busy;
        startButton.title = startTitle;
        startButton.setAttribute('aria-label', startTitle);
    }

    if (busy && chip) {
        chip.style.display = 'inline-flex';
        chip.className = 'tagger-aesthetic-status is-info';
        chip.textContent = t('gallery.aestheticProgress', '{completed}/{total} scored', {
            completed: state.completed,
            total: Math.max(state.total, state.completed),
        });
        chip.title = chip.textContent;
    } else if (chip) {
        chip.style.display = 'inline-flex';
        chip.className = 'tagger-aesthetic-status is-safe';
        chip.textContent = t('gallery.aestheticReady', 'Aesthetic ready');
        chip.title = chip.textContent;
    }
}

function updateAestheticUi(nextState) {
    _aestheticUiState = normalizeAestheticUiState(nextState);
    renderAestheticUi(_aestheticUiState);
}

function refreshAestheticUi() {
    renderAestheticUi(_aestheticUiState);
}

async function readAestheticStatus() {
    try {
        const status = await API.getAestheticStatus();
        return {
            available: Boolean(status?.available),
            message: status?.message || '',
            scored_count: Number(status?.scored_count || 0),
        };
    } catch (error) {
        return {
            available: false,
            message: formatUserError(error, appT('gallery.aestheticStatusFailed', 'Could not check aesthetic scoring status')),
            scored_count: 0,
        };
    }
}

function publishAestheticStatus(status) {
    _aestheticStatus = { ...status };
    refreshAestheticUi();

    // Update sort dropdown option availability
    const sortDropdown = $('#gallery-sort');
    if (sortDropdown) {
        const aestheticOption = sortDropdown.querySelector('option[value="aesthetic"]');
        if (aestheticOption) {
            if (!_aestheticStatus.available && _aestheticStatus.scored_count === 0) {
                aestheticOption.disabled = true;
                aestheticOption.textContent = appT('sort.aestheticDisabled', 'Aesthetic Score (unavailable)');
            } else if (_aestheticStatus.scored_count === 0) {
                aestheticOption.disabled = false;
                aestheticOption.textContent = appT('sort.aestheticNoScores', 'Aesthetic Score (no scores yet - score from AI Tag)');
            } else {
                aestheticOption.disabled = false;
                aestheticOption.textContent = appT('sort.aesthetic', 'Aesthetic Score') +
                    ` (${_aestheticStatus.scored_count} scored)`;
            }
        }
    }
    return { ..._aestheticStatus };
}

async function refreshAestheticStatus() {
    _aestheticStatusGeneration += 1;
    const requestGeneration = _aestheticStatusGeneration;
    const status = await readAestheticStatus();
    if (requestGeneration !== _aestheticStatusGeneration) {
        return { ..._aestheticStatus };
    }
    return publishAestheticStatus(status);
}

async function refreshAestheticTaskState() {
    clearAestheticProgressTimer();
    const progressGeneration = _aestheticProgressGeneration;
    _aestheticStatusGeneration += 1;
    const statusGeneration = _aestheticStatusGeneration;
    const nextStatus = await readAestheticStatus();
    if (
        progressGeneration !== _aestheticProgressGeneration
        || statusGeneration !== _aestheticStatusGeneration
    ) {
        return { status: { ..._aestheticStatus }, progress: null };
    }
    const status = publishAestheticStatus(nextStatus);
    if (!status.available) {
        return { status, progress: null };
    }

    try {
        const rawProgress = await API.getAestheticProgress();
        if (progressGeneration !== _aestheticProgressGeneration) {
            return { status, progress: null };
        }
        const running = Boolean(rawProgress?.running);
        const progress = {
            running,
            starting: !running && _aestheticUiState.starting,
            completed: Number(rawProgress?.completed || 0),
            total: Number(rawProgress?.total || 0),
        };
        updateAestheticUi(progress);
        if (progress.running) {
            _aestheticProgressTimer = setTimeout(pollAestheticProgress, 1200);
        }
        return { status, progress };
    } catch (error) {
        if (progressGeneration !== _aestheticProgressGeneration) {
            return { status, progress: null };
        }
        _aestheticStatus = {
            available: false,
            message: formatUserError(error, appT('gallery.aestheticProgressFailed', 'Failed to read aesthetic progress')),
            scored_count: status.scored_count,
        };
        updateAestheticUi({ running: false, starting: false, completed: 0, total: 0 });
        return { status: { ..._aestheticStatus }, progress: null };
    }
}

async function pollAestheticProgress() {
    clearAestheticProgressTimer();
    const requestGeneration = _aestheticProgressGeneration;
    try {
        const progress = await API.getAestheticProgress();
        if (requestGeneration !== _aestheticProgressGeneration) return;
        const running = Boolean(progress?.running);
        const completed = Number(progress?.completed || 0);
        const total = Number(progress?.total || 0);

        updateAestheticUi({ running, starting: false, completed, total });

        if (running) {
            _aestheticProgressTimer = setTimeout(pollAestheticProgress, 1200);
            return;
        }

        // The backend writes progress.error when the whole batch crashed
        // (model load / CUDA failure). Surface that instead of the success
        // toast the run would otherwise fake.
        const batchError = String(progress?.error || '').trim();
        if (batchError) {
            showToast(
                appT('gallery.aestheticFailed', 'Aesthetic scoring failed: {error}').replace('{error}', batchError),
                'error'
            );
            if (completed > 0) {
                // Partial scores may have landed before the crash.
                await loadImages();
                await loadStats();
            }
            return;
        }

        if (total > 0) {
            const errors = Number(progress?.errors || 0);
            showToast(
                errors > 0
                    ? appT('gallery.aestheticCompletedWarn', 'Aesthetic scoring finished with {errors} errors.').replace('{errors}', errors)
                    : appT('gallery.aestheticCompleted', 'Aesthetic scoring completed.'),
                errors > 0 ? 'warning' : 'success'
            );
            await loadImages();
            await loadStats();
        }
    } catch (error) {
        if (requestGeneration !== _aestheticProgressGeneration) return;
        updateAestheticUi({ running: false, starting: false, completed: 0, total: 0 });
        showToast(formatUserError(error, appT('gallery.aestheticProgressFailed', 'Failed to read aesthetic progress')), 'error');
    }
}

async function startAestheticScoring(force = false) {
    if (_aestheticStartRequestPending) return;
    if (typeof window.ensureFeatureModel === 'function') {
        const ensured = await window.ensureFeatureModel('aesthetic', {
            label: appT('gallery.aestheticModelName', 'Aesthetic scoring'),
            sizeHint: '~1.7 GB',
            confirmBytes: 1.7 * 1024 * 1024 * 1024,
        });
        if (!ensured.ok) return;
        await refreshAestheticStatus();
    }
    if (!_aestheticStatus.available) {
        updateAestheticUi({ running: false, starting: false, completed: 0, total: 0 });
        showToast(_aestheticStatus.message || appT('gallery.aestheticUnavailable', 'Aesthetic scoring is unavailable'), 'warning');
        return;
    }

    _aestheticStartRequestPending = true;
    updateAestheticUi({ running: false, starting: true, completed: 0, total: 0 });
    let result;
    try {
        result = await API.startAestheticScoring(force);
    } catch (error) {
        updateAestheticUi({ running: false, starting: false, completed: 0, total: 0 });
        showToast(formatUserError(error, appT('gallery.aestheticStartFailed', 'Failed to start aesthetic scoring')), 'error');
        return;
    } finally {
        _aestheticStartRequestPending = false;
    }

    const status = String(result?.status || 'started');
    const total = Number(result?.total || 0);
    if (status === 'started' && total === 0) {
        updateAestheticUi({ running: false, starting: false, completed: 0, total: 0 });
        showToast(appT('gallery.aestheticNothingToScore', 'All current images already have aesthetic scores.'), 'info');
        return;
    }
    if (status === 'started' || status === 'already_running') {
        updateAestheticUi({ running: true, starting: false, completed: 0, total });
        if (status === 'started') {
            showToast(appT('gallery.aestheticStarted', 'Aesthetic scoring started in the background.'), 'info');
        }
        await pollAestheticProgress();
    }
}

