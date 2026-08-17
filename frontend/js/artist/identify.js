/**
 * artist/identify.js — artist-ident.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/artist-ident.js
 * pre-cut lines 509-591 + 764-1080 (of 1,171): _getIdentifyModelConfig
 * (a gallery/modal-analysis.js seam — underscore name but PUBLIC),
 * _getIdentifyPayload, _buildCompletionToast, the "Identification"
 * section comment, updateProgressUi, resumeBatchProgress, identifyAll,
 * pollProgress, identifySelected and clearAllData (the batch identify
 * pipeline + progress bar). Classic non-strict script: joins the ONE
 * unsealed window.ArtistIdent object declared in artist/core.js, which
 * loads FIRST; artist/boot.js runs the DOMContentLoaded tail LAST.
 */
Object.assign(window.ArtistIdent, {
    _getIdentifyModelConfig() {
        const modelSourceEl = document.getElementById('artist-model-source');
        const modelPathEl = document.getElementById('artist-model-path');
        const useGpuEl = document.getElementById('artist-use-gpu');
        const modelSource = String(modelSourceEl?.value || 'huggingface').trim() || 'huggingface';
        const modelPath = String(modelPathEl?.value || '').trim();

        if (modelSource === 'local' && !modelPath) {
            throw new Error('Local model path is required when using the local model source');
        }

        return {
            model_source: modelSource,
            model_path: modelSource === 'local' ? modelPath : null,
            // Default checked = use GPU (matches backend ARTIST_USE_GPU default).
            // Unchecked forces CPU for GPU stacks that freeze under CUDA load.
            use_gpu: useGpuEl ? !!useGpuEl.checked : null,
        };
    },

    _getIdentifyPayload(imageIds) {
        return {
            image_ids: imageIds,
            threshold: this.getThresholdValue(),
            top_k: 5,
            ...this._getIdentifyModelConfig(),
        };
    },

    _buildCompletionToast(progress, requestedCount = 0) {
        const results = Array.isArray(progress?.results) ? progress.results : [];
        const errors = Number(progress?.errors || 0);
        const tiers = results.map((result) => this.describeArtistResult(result).level);
        const confident = tiers.filter((level) => level === 'high').length;
        const unconfirmed = tiers.filter((level) => level === 'low').length;
        const noMatch = tiers.filter((level) => level === 'none').length;
        const targetCount = requestedCount || Number(progress?.total || 0) || results.length;

        // Whole-batch crash: the backend reports step='error' + message
        // WITHOUT bumping the per-image error count, so check it before the
        // count-based paths or a crashed run shows a success toast.
        if (String(progress?.step || '') === 'error') {
            return {
                level: 'error',
                message: String(progress?.message || '').trim()
                    || this.tText('Artist identification failed.', '画师识别失败。'),
            };
        }

        if (errors > 0) {
            return {
                level: 'warning',
                message: this.tText(
                    `Artist identification finished with ${errors} error(s).`,
                    `画师识别完成，但有 ${errors} 个错误。`
                ),
            };
        }

        // No confident match in the whole run is worth flagging, but the cause
        // is almost never the slider: 97% of the bottom tier is an artist the
        // model simply cannot name. Telling the user to lower the threshold and
        // rerun (the pre-tiering advice) costs another full pass for nothing.
        if (results.length > 0 && confident === 0) {
            return {
                level: 'warning',
                message: this.tKey(
                    'artist.completionNoConfident',
                    'Finished {count} image(s) with no confident match: {unconfirmed} unconfirmed candidate(s), {none} with no match. Those artists are probably not in this model\u2019s vocabulary.',
                    '已完成 {count} 张，但没有高置信度匹配：{unconfirmed} 个低置信度候选，{none} 个没有匹配。这些画师大概率不在此模型的词表里。',
                    { count: results.length, unconfirmed, none: noMatch }
                ),
            };
        }

        if (results.length > 0) {
            return {
                level: 'success',
                message: this.tKey(
                    'artist.completionTiered',
                    'Identified {count} image(s): {confident} confident, {unconfirmed} unconfirmed, {none} no match.',
                    '已完成 {count} 张：{confident} 个高置信度，{unconfirmed} 个低置信度候选，{none} 个没有匹配。',
                    { count: results.length, confident, unconfirmed, none: noMatch }
                ),
            };
        }

        if (targetCount > 0) {
            return {
                level: 'success',
                message: this.tKey(
                    'artist.completionCounted',
                    'Identified {count} image(s).',
                    '已完成 {count} 张图片的画师识别。',
                    { count: targetCount }
                ),
            };
        }

        return {
            level: 'success',
            message: this.tText('Artist identification complete!', '画师识别完成。'),
        };
    },


    // ============== Identification ==============

    updateProgressUi(progress = {}) {
        const progressContainer = document.getElementById('artist-progress-container');
        const progressFill = document.getElementById('artist-progress-fill');
        const progressText = document.getElementById('artist-progress-text');
        const total = Number(progress.total || 0);
        const processed = Number(progress.processed || 0);
        const errors = Number(progress.errors || 0);
        const completed = total > 0 ? Math.min(total, processed + errors) : 0;
        const percent = total > 0 ? Math.round(completed / total * 100) : 0;

        if (progressContainer) progressContainer.style.display = 'block';
        if (progressFill) progressFill.style.width = `${percent}%`;
        if (progressText) {
            if (!this.progressTracker) {
                this.progressTracker = window.App.createProgressTracker();
            }

            const progressLabel = total > 0
                ? window.App.buildProgressText({
                    progress,
                    completed,
                    total,
                    tracker: this.progressTracker,
                    defaultMessage: `${processed} identified${errors > 0 ? `, ${errors} error(s)` : ''}`,
                    primaryLabel: 'Artist ID'
                })
                : (progress.message || 'Preparing artist identification...');
            const currentItem = progress.current_item ? ` · ${progress.current_item}` : '';
            progressText.textContent = `${progressLabel}${currentItem}`;
        }
    },

    async resumeBatchProgress() {
        if (this.isIdentifying) return;

        try {
            const progress = await window.App.API.get('/api/artists/batch-progress');
            if (!progress?.running) {
                return;
            }

            this.progressTracker = window.App.createProgressTracker();
            window.App.resetProgressTracker(this.progressTracker);
            this.isIdentifying = true;
            this.refreshAvailabilityState();
            this.updateProgressUi(progress);

            const finalProgress = await this.pollProgress();
            await this.loadStats();
            const completion = this._buildCompletionToast(finalProgress);
            window.App.showToast(completion.message, completion.level);
        } catch (e) {
            Logger.warn('Failed to resume artist identification progress:', e);
        } finally {
            if (!document.getElementById('artist-progress-container')) {
                return;
            }

            this.isIdentifying = false;
            this.refreshAvailabilityState();
            if (this.progressTracker) {
                window.App.resetProgressTracker(this.progressTracker);
            }
            document.getElementById('artist-progress-container').style.display = 'none';
        }
    },

    async identifyAll() {
        if (this.isIdentifying) return;

        const { showToast, hideGlobalLoading } = window.App;
        const progressContainer = document.getElementById('artist-progress-container');
        const progressFill = document.getElementById('artist-progress-fill');
        const progressText = document.getElementById('artist-progress-text');

        if (this.diagnostics && this.diagnostics.available === false) {
            showToast(this.tText('Finish setup first, then start identification.', '请先完成准备，再开始识别。'), 'warning');
            return;
        }

        this.isIdentifying = true;
        this.dismissFirstUseCard();
        this.refreshAvailabilityState();
        this.progressTracker = window.App.createProgressTracker();
        window.App.resetProgressTracker(this.progressTracker);

        // Show progress bar immediately instead of a blocking overlay
        if (progressContainer) progressContainer.style.display = 'block';
        if (progressFill) progressFill.style.width = '0%';
        if (progressText) progressText.textContent = this.tText(
            'Collecting image list...', '正在收集图片列表...'
        );

        let handedOffToExistingTask = false;

        try {
            const pageSize = 1000;
            let cursor = null;
            let imageIds = [];

            while (true) {
                const query = new URLSearchParams({ limit: String(pageSize) });
                if (cursor) query.set('cursor', cursor);
                const imagesResult = await window.App.API.get(`/api/images?${query.toString()}`);
                imageIds = imageIds.concat((imagesResult.images || []).map(img => img.id));

                if (progressText) {
                    progressText.textContent = this.tText(
                        `Collected ${imageIds.length} images...`,
                        `已收集 ${imageIds.length} 张图片...`
                    );
                }

                if (!imagesResult.has_more || !imagesResult.next_cursor) {
                    break;
                }
                cursor = imagesResult.next_cursor;
            }

            if (imageIds.length === 0) {
                showToast(this.tKey('artist.noImagesToIdentify', 'No images to identify', '没有可识别的图片'), 'warning');
                return;
            }

            if (progressText) {
                progressText.textContent = this.tText(
                    `Starting identification of ${imageIds.length} images...`,
                    `正在启动 ${imageIds.length} 张图片的识别...`
                );
            }

            // Start batch identification
            await window.App.API.post('/api/artists/identify-batch', this._getIdentifyPayload(imageIds));

            // Poll progress
            const progress = await this.pollProgress();
            await this.loadStats();
            const completion = this._buildCompletionToast(progress, imageIds.length);
            showToast(completion.message, completion.level);

        } catch (e) {
            if (/already in progress/i.test(String(e?.message || ''))) {
                handedOffToExistingTask = true;
                showToast(this.tText(
                    'Artist identification is already running in the background',
                    '画师识别已经在后台运行中'
                ), 'info');
                this.isIdentifying = false;
                await this.resumeBatchProgress();
            } else {
                showToast(formatUserError(e, this.tKey('artist.identificationFailed', 'Artist identification failed', '画师识别失败')), "error");
            }
        } finally {
            if (!handedOffToExistingTask) {
                this.isIdentifying = false;
                this.refreshAvailabilityState();
                if (this.progressTracker) {
                    window.App.resetProgressTracker(this.progressTracker);
                }
                if (progressContainer) progressContainer.style.display = 'none';
            }
            hideGlobalLoading();
        }
    },

    async pollProgress() {
        const progressFill = document.getElementById('artist-progress-fill');
        const progressText = document.getElementById('artist-progress-text');
        let lastProgress = null;

        while (this.isIdentifying) {
            try {
                const progress = await window.App.API.get('/api/artists/batch-progress');
                lastProgress = progress;

                if (progress.total > 0) {
                    const processed = Number(progress.processed || 0);
                    const errors = Number(progress.errors || 0);
                    const completed = Math.min(progress.total, processed + errors);
                    const percent = Math.round(completed / progress.total * 100);
                    if (progressFill) progressFill.style.width = `${percent}%`;
                    if (progressText) {
                        if (processed === 0 && progress.step === 'loading_runtime') {
                            progressText.textContent = progress.message || this.tKey('artist.loadingModel', 'Loading artist model...', '正在载入画师模型...');
                        } else {
                            const progressLabel = window.App.buildProgressText({
                                progress,
                                completed,
                                total: Number(progress.total || 0),
                                tracker: this.progressTracker,
                                defaultMessage: errors > 0
                                    ? this.tKey('artist.progressDefault', '{processed} identified, {errors} failed', '已识别 {processed} 张，失败 {errors} 张', {
                                        processed,
                                        errors,
                                    })
                                    : this.tKey('artist.progressDefault', '{processed} identified, {errors} failed', '已识别 {processed} 张，失败 {errors} 张', {
                                        processed,
                                        errors: 0,
                                    }),
                                primaryLabel: this.tKey('artist.progressPrimary', 'Artist ID', '画师识别')
                            });
                            const currentItem = progress.current_item ? ` · ${progress.current_item}` : '';
                            progressText.textContent = `${progressLabel}${currentItem}`;
                        }
                    }
                }

                if (!progress.running) {
                    return progress;
                }

                await new Promise(resolve => setTimeout(resolve, 1000));
            } catch (e) {
                Logger.error('Progress poll error:', e);
                throw e;
            }
        }

        return lastProgress;
    },

    async identifySelected() {
        const { showToast } = window.App;
        const progressContainer = document.getElementById('artist-progress-container');
        const progressFill = document.getElementById('artist-progress-fill');
        const progressText = document.getElementById('artist-progress-text');
        const selectedImageIds = this._getExplicitGallerySelectionIds();

        if (selectedImageIds.length === 0) {
            showToast(this.tText('No images selected', '没有选中图片'), 'warning');
            return;
        }

        if (this.isIdentifying) {
            showToast(this.tText('Identification already in progress', '识别任务已在进行中'), 'warning');
            return;
        }

        if (this.diagnostics && this.diagnostics.available === false) {
            showToast(this.tText('Finish setup first, then start identification.', '请先完成准备，再开始识别。'), 'warning');
            return;
        }

        this.isIdentifying = true;
        this.dismissFirstUseCard();
        this.refreshAvailabilityState();
        this.progressTracker = window.App.createProgressTracker();
        window.App.resetProgressTracker(this.progressTracker);

        let handedOffToExistingTask = false;

        try {
            if (progressContainer) progressContainer.style.display = 'block';
            if (progressFill) progressFill.style.width = '0%';
            if (progressText) {
                progressText.textContent = this.tKey(
                    'artist.identifyingSelected',
                    'Identifying {count} selected image(s)...',
                    '正在识别 {count} 张已选图片...',
                    { count: selectedImageIds.length }
                );
            }

            await window.App.API.post(
                '/api/artists/identify-batch',
                this._getIdentifyPayload(selectedImageIds),
            );

            const progress = await this.pollProgress();
            await this.loadStats();
            const completion = this._buildCompletionToast(progress, selectedImageIds.length);
            showToast(completion.message, completion.level);

        } catch (e) {
            if (/already in progress/i.test(String(e?.message || ''))) {
                handedOffToExistingTask = true;
                showToast(this.tText(
                    'Artist identification is already running in the background',
                    '画师识别已经在后台运行中'
                ), 'info');
                this.isIdentifying = false;
                await this.resumeBatchProgress();
            } else {
                showToast(formatUserError(e, this.tKey('artist.identificationFailed', 'Artist identification failed', '画师识别失败')), "error");
            }
        } finally {
            if (!handedOffToExistingTask) {
                this.isIdentifying = false;
                this.refreshAvailabilityState();
                if (this.progressTracker) {
                    window.App.resetProgressTracker(this.progressTracker);
                }
                if (progressContainer) progressContainer.style.display = 'none';
            }
        }
    },


    async clearAllData() {
        const { showToast, showConfirm, API } = window.App;

        showConfirm(
            this.tKey('artist.clearConfirmTitle', 'Clear Artist Predictions', '清空画师识别结果'),
            this.tKey('artist.clearConfirmMessage', 'Clear all artist predictions? This cannot be undone.', '要清空全部画师识别结果吗？此操作无法撤销。'),
            async () => {
                try {
                    await API.delete('/api/artists/clear');
                    showToast(this.tText('All predictions cleared', '已清除所有预测'), 'success');
                    this.loadStats();
                } catch (e) {
                    showToast(formatUserError(e, this.tKey('artist.clearDataFailed', 'Failed to clear data', '清空数据失败')), "error");
                }
            }
        );
    },

});
