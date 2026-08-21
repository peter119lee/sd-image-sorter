/**
 * similar/embedding.js — similar.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/similar.js pre-cut
 * lines 279-313 + 376-502 + 632-762 (of 1,517): setEmbeddingUiState,
 * resetEmbeddingUi, formatIssueSummary, waitForEmbeddingStatusReady,
 * renderEmbeddingProgress, resumeEmbeddingProgress, startEmbedding and
 * pollEmbedProgress (the '// ===== Embedding =====' section comment
 * travels with startEmbedding) — the embedding-index build bar + polling.
 * Classic non-strict script: joins the ONE unsealed window.SimilarImages
 * object declared in similar/core.js, which loads FIRST; boot.js publishes
 * initSimilar LAST.
 */
Object.assign(window.SimilarImages, {
    /**
     * Label-only refresh for the embed-row primary. Split out of
     * setEmbeddingUiState because the button is rendered before
     * /api/similarity/stats answers: with embedded still 0 it read
     * "Build Similarity Index" forever, even once 49/4883 were indexed.
     * Never calls back into refreshWorkflowStatus (recursion).
     */
    syncEmbedButtonLabel(isRunning) {
        const btnEmbed = document.getElementById('btn-similar-embed');
        if (!btnEmbed) return;
        const { embedded, pending } = this.getEmbeddingStats();
        const hasExistingIndex = embedded > 0;
        // The button only stays visible while pending > 0 (refreshContentVisibility),
        // i.e. the click CONTINUES an unfinished index — "Rebuild" was a lie
        // that also collided with the workflow card's own start CTA.
        const continuesPartialIndex = hasExistingIndex && pending > 0;

        btnEmbed.textContent = isRunning
            ? this._t('similar.indexingNow', 'Indexing...')
            : this._t(
                continuesPartialIndex ? 'similar.continueIndexing'
                    : hasExistingIndex ? 'similar.rebuildIndex' : 'similar.generateEmbed',
                continuesPartialIndex ? 'Continue Indexing'
                    : hasExistingIndex ? 'Rebuild Index' : 'Generate Embeddings'
            );
    },

    setEmbeddingUiState(isRunning, label = null) {
        const btnEmbed = document.getElementById('btn-similar-embed');
        if (!btnEmbed) return;

        btnEmbed.disabled = isRunning;
        if (label) {
            btnEmbed.textContent = label;
        } else {
            this.syncEmbedButtonLabel(isRunning);
        }
        this.updateActionAvailability();
        this.refreshWorkflowStatus();
    },

    resetEmbeddingUi({ hideProgress = true, progressMessage = '' } = {}) {
        const progressBar = document.getElementById('similar-embed-progress');
        const progressFill = document.getElementById('similar-embed-fill');
        const progressText = document.getElementById('similar-embed-text');

        this.setEmbeddingUiState(false);
        if (progressFill) {
            progressFill.style.width = '0%';
        }
        if (progressText) {
            progressText.textContent = progressMessage;
        }
        if (progressBar && hideProgress) {
            progressBar.style.display = 'none';
        }
        this.updateActionAvailability();
        this.refreshWorkflowStatus();
    },

    formatIssueSummary(progress = {}) {
        const issues = Array.isArray(progress.recent_issues) ? progress.recent_issues : [];
        if (!issues.length) return '';
        return issues
            .slice(-3)
            .map((issue) => {
                const idPart = issue.image_id ? `#${issue.image_id} ` : '';
                const reasonPart = issue.reason ? ` (${issue.reason})` : '';
                return `${issue.kind}: ${idPart}${issue.filename}${reasonPart}`;
            })
            .join(' | ');
    },

    async waitForEmbeddingStatusReady(timeoutMs = 10000) {
        const startedAt = Date.now();
        while (this.isCheckingEmbeddingStatus && (Date.now() - startedAt) < timeoutMs) {
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        return !this.isCheckingEmbeddingStatus;
    },

    renderEmbeddingProgress(progress = {}) {
        const progressBar = document.getElementById('similar-embed-progress');
        const progressFill = document.getElementById('similar-embed-fill');
        const progressText = document.getElementById('similar-embed-text');
        const total = Number(progress.total || 0);
        const processed = Number(progress.processed || progress.current || 0);
        const hasBreakdown = ['embedded', 'skipped', 'unreadable', 'failed'].some((key) => progress[key] != null);
        const embedded = Number(progress.embedded || 0);
        const errors = Number(progress.errors || 0);
        const skipped = Number(progress.skipped || 0);
        const unreadable = Number(progress.unreadable || 0);
        const failed = Number(progress.failed || 0);
        const completed = total > 0 ? Math.min(total, hasBreakdown ? processed : (processed + errors)) : 0;
        const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
        const issueSummary = this.formatIssueSummary(progress);
        const issueSuffix = issueSummary
            ? (window.I18n?.getLang?.() === 'zh-CN' ? `（${issueSummary}）` : ` (${issueSummary})`)
            : '';

        if (progressBar) {
            progressBar.style.display = 'block';
        }
        if (progressFill) {
            progressFill.style.width = `${percent}%`;
        }
        if (progressText) {
            if (!this.embedProgressTracker) {
                this.embedProgressTracker = window.App.createProgressTracker();
            }

            if (total > 0) {
                progressText.textContent = window.App.buildProgressText({
                    progress,
                    completed,
                    total,
                    tracker: this.embedProgressTracker,
                    defaultMessage: errors > 0
                        ? (hasBreakdown
                            ? this._t(
                                'similar.embedProgressBreakdown',
                                '{embedded} embedded, {skipped} skipped, {unreadable} unreadable, {failed} failed{issues}',
                                { embedded, skipped, unreadable, failed, issues: issueSuffix }
                            )
                            : this._t(
                                'similar.embedProgressSimple',
                                '{processed} embedded, {errors} failed',
                                { processed, errors }
                            ))
                        : this._t(
                            'similar.embedProgressCount',
                            '{count} embedded',
                            { count: hasBreakdown ? embedded : processed }
                        ),
                    primaryLabel: this._t('similar.embedProgressPrimary', 'Embedding')
                });
            } else if (progress.running) {
                progressText.textContent = this._t('similar.embedPreparing', 'Preparing embeddings...');
            } else {
                progressText.textContent = this._t('similar.embedNoPending', 'No pending images to index');
            }
        }
    },

    async resumeEmbeddingProgress({ optimistic = true } = {}) {
        if (this.isEmbedding || this.isCheckingEmbeddingStatus) return;

        this.isCheckingEmbeddingStatus = true;
        if (optimistic) {
            this.setEmbeddingUiState(true, this._t('similar.embedCheckingStatus', 'Checking status...'));
        }

        try {
            const progress = await window.App.API.get('/api/similarity/progress');
            this.embedProgress = progress;

            if (!progress?.running) {
                this.isEmbedding = false;
                this.setEmbeddingUiState(false);
                if (this.embedProgressTracker) {
                    window.App.resetProgressTracker(this.embedProgressTracker);
                }
                this.refreshWorkflowStatus();
                return;
            }

            if (!this.embedProgressTracker) {
                this.embedProgressTracker = window.App.createProgressTracker();
            } else {
                window.App.resetProgressTracker(this.embedProgressTracker);
            }
            this.isEmbedding = true;
            this.setEmbeddingUiState(true);
            this.renderEmbeddingProgress(progress);
            this.pollEmbedProgress();
            this.refreshWorkflowStatus();
        } catch (e) {
            this.setEmbeddingUiState(false);
            Logger.warn('Failed to resume similarity embedding progress:', e);
            this.refreshWorkflowStatus();
        } finally {
            this.isCheckingEmbeddingStatus = false;
            this.updateActionAvailability();
            this.refreshWorkflowStatus();
        }
    },

    // ============== Embedding ==============

    async startEmbedding() {
        if (this.isEmbedding) return;

        const { showToast } = window.App;
        if (typeof window.ensureFeatureModel === 'function') {
            const ensured = await window.ensureFeatureModel('clip', {
                label: this._t('similar.clipModelName', 'CLIP similarity'),
                sizeHint: '~580 MB',
                confirmBytes: 0,
            });
            if (!ensured.ok) return;
        }
        this.isEmbedding = true;
        this.dismissFirstUseCard();
        this.embedProgressTracker = window.App.createProgressTracker();
        window.App.resetProgressTracker(this.embedProgressTracker);

        this.setEmbeddingUiState(true);
        this.renderEmbeddingProgress({ running: true, total: 0, processed: 0, errors: 0 });
        this.refreshWorkflowStatus();

        try {
            const result = await window.App.API.post('/api/similarity/embed');
            this.embedProgress = result?.progress || this.embedProgress;
            if (result?.progress) {
                this.renderEmbeddingProgress(result.progress);
            }

            if (result?.status === 'already_running') {
                showToast(this._t('similar.embedAlreadyRunning', 'Embedding is already running in the background'), 'info');
            } else {
                showToast(this._t('similar.embedStarted', 'Embedding started in background'), 'info');
            }
            this.pollEmbedProgress();
        } catch (e) {
            showToast(formatUserError(e, this._t('similar.failedToStart', 'Failed to start similarity processing')), 'error');
            this.isEmbedding = false;
            this.resetEmbeddingUi();
            this.refreshWorkflowStatus();
        }
    },

    async pollEmbedProgress() {
        const progressBar = document.getElementById('similar-embed-progress');

        try {
            const result = await window.App.API.get('/api/similarity/progress');
            this.embedProgress = result;
            this.renderEmbeddingProgress(result);

            if (result.running) {
                setTimeout(() => this.pollEmbedProgress(), 1000);
            } else {
                this.isEmbedding = false;
                this.setEmbeddingUiState(false);

                const total = Number(result.total || 0);
                const processed = Number(result.processed || result.current || 0);
                const hasBreakdown = ['embedded', 'skipped', 'unreadable', 'failed'].some((key) => result[key] != null);
                const embedded = Number(result.embedded || 0);
                const errors = Number(result.errors || 0);
                const skipped = Number(result.skipped || 0);
                const unreadable = Number(result.unreadable || 0);
                const failed = Number(result.failed || 0);
                const completed = total > 0 ? Math.min(total, hasBreakdown ? processed : (processed + errors)) : 0;
                const issueSummary = this.formatIssueSummary(result);
                const issueSuffix = issueSummary
                    ? (window.I18n?.getLang?.() === 'zh-CN' ? `（${issueSummary}）` : ` (${issueSummary})`)
                    : '';
                const extra = errors > 0
                    ? (hasBreakdown
                        ? (window.I18n?.getLang?.() === 'zh-CN'
                            ? `，已跳过 ${skipped} 张，不可读 ${unreadable} 张，失败 ${failed} 张${issueSuffix}`
                            : `, ${skipped} skipped, ${unreadable} unreadable, ${failed} failed${issueSuffix}`)
                        : (window.I18n?.getLang?.() === 'zh-CN'
                            ? `，失败 ${errors} 张`
                            : `, ${errors} failed`))
                    : '';
                const finalMessage = total > 0
                    ? this._t(
                        'similar.embedProgressFinal',
                        '{completed}/{total} images ({count} embedded{extra})',
                        {
                            completed,
                            total,
                            count: hasBreakdown ? embedded : processed,
                            extra,
                        }
                    )
                    : this._t('similar.embedNoPending', 'No pending images to index');

                this.resetEmbeddingUi({ hideProgress: false, progressMessage: finalMessage });
                if (this.embedProgressTracker) {
                    window.App.resetProgressTracker(this.embedProgressTracker);
                }
                if (progressBar) {
                    setTimeout(() => { progressBar.style.display = 'none'; }, 2000);
                }
                this.loadStats();
                if (total === 0) {
                    window.App.showToast(this._t('similar.embedNoPending', 'No pending images to index'), 'info');
                } else if (errors > 0) {
                    window.App.showToast(
                        hasBreakdown
                            ? this._t(
                                'similar.embedFinishedBreakdown',
                                'Indexing finished: {embedded} embedded, {skipped} skipped, {unreadable} unreadable, {failed} failed{issues}',
                                { embedded, skipped, unreadable, failed, issues: issueSuffix }
                            )
                            : this._t(
                                'similar.embedFinishedSimple',
                                'Indexing finished: {processed} embedded, {errors} failed',
                                { processed, errors }
                            ),
                        'warning',
                    );
                } else {
                    window.App.showToast(
                        this._t('similar.embedComplete', 'Indexing complete: {count} images embedded', {
                            count: hasBreakdown ? embedded : processed,
                        }),
                        'success'
                    );
                }
                this.refreshWorkflowStatus();
            }
        } catch (e) {
            this.isEmbedding = false;
            this.resetEmbeddingUi();
            if (this.embedProgressTracker) {
                window.App.resetProgressTracker(this.embedProgressTracker);
            }
            window.App.showToast(formatUserError(e, this._t('similar.refreshProgressFailed', 'Failed to refresh embedding progress')), 'error');
            this.refreshWorkflowStatus();
        }
    },

});
