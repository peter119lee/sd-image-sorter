/**
 * gallery/modal-analysis.js — gallery.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/gallery.js pre-cut
 * lines 376-594 (of 4,708): modal pipeline handoff + per-image analysis actions (color patch pin).
 * Classic script: joins the ONE unsealed window.Gallery object declared in
 * gallery/base.js, which loads FIRST (the shared consts + module helpers
 * live there); index.html lists the family in original line order.
 */
Object.assign(window.Gallery, {
    // FLOW-03: route the currently-previewed image into the next pipeline step.
    // Reuses the exact functions the right-click context menu calls, so behavior
    // stays consistent. View-switching handoffs close the modal first; "collection"
    // overlays a picker so the modal is left underneath.
    _handleModalHandoff(action) {
        const app = window.App || {};
        const id = this._currentPreviewId;
        if (!id) return;
        const image = this.images.find((im) => Number(im.id) === Number(id))
            || app.AppState?.images?.find((im) => Number(im.id) === Number(id));
        const filename = (image && image.filename) || '';
        const closeModal = () => {
            const fn = app.closeModal || window.closeModal || window.hideModal;
            if (typeof fn === 'function') fn('image-modal');
        };
        switch (action) {
            case 'censor':
                if (typeof app.addToCensorQueue === 'function') app.addToCensorQueue([id]);
                else app.showToast?.(app.appT?.('gallery.contextSendToCensorFailed', 'Failed to send image to Edit') || 'Failed', 'error');
                closeModal();
                break;
            case 'similar':
                app.openSimilarFromImage?.(id);
                closeModal();
                break;
            case 'dataset':
                if (typeof app.addToDatasetMaker === 'function') {
                    app.addToDatasetMaker([id], { switchView: true, showToast: true })
                        .then((ok) => { if (ok) closeModal(); });
                } else {
                    app.showToast?.(app.appT?.('selection.sendToDatasetMakerUnavailable', 'Dataset Maker module not loaded yet — try again in a moment.') || 'Dataset Maker not loaded yet', 'error');
                }
                break;
            case 'collection':
                // Picker overlays on top; keep the image modal underneath so the
                // user returns to the image after choosing a collection.
                window.CollectionsUI?.openAddToCollectionPicker?.([id]);
                break;
            case 'prompt':
                app.openPromptBuildFromImage?.(id);
                closeModal();
                break;
            case 'reader':
                app.openReaderFromImage?.(id, filename);
                closeModal();
                break;
            default:
                break;
        }
    },

    _patchImageState(imageId, patch = {}) {
        const id = Number(imageId);
        const apply = (list) => {
            if (!Array.isArray(list)) return;
            const image = list.find((item) => Number(item?.id) === id);
            if (image) Object.assign(image, patch);
        };
        apply(this.images);
        apply(window.App?.AppState?.images);
        if (this._lastModalImage && Number(this._lastModalImage.id) === id) {
            Object.assign(this._lastModalImage, patch);
        }
    },

    _buildColorAnalysisPatch(colorData = {}) {
        const patch = {};
        [
            'dominant_colors',
            'avg_brightness',
            'color_temperature',
            'color_saturation',
            'brightness_distribution',
            'brightness_histogram',
            'brightness_skew',
        ].forEach((field) => {
            if (Object.prototype.hasOwnProperty.call(colorData, field)) {
                patch[field] = colorData[field];
            }
        });
        return patch;
    },

    _modalAnalysisActions: new Set(['aesthetic', 'color', 'artist', 'caption']),

    _syncModalAnalysisButtons() {
        const anyBusy = this._modalAnalysisRunning.size > 0;
        document.querySelectorAll('[data-modal-analysis]').forEach((button) => {
            const action = button.dataset.modalAnalysis;
            const busy = anyBusy;
            const actionBusy = this._modalAnalysisRunning.has(action);
            button.disabled = anyBusy;
            button.classList.toggle('is-busy', busy);
            button.classList.toggle('is-running-action', actionBusy);
            button.setAttribute('aria-busy', busy ? 'true' : 'false');
            button.setAttribute('aria-disabled', anyBusy ? 'true' : 'false');
        });
    },

    _setModalAnalysisBusy(action, busy) {
        if (busy) this._modalAnalysisRunning.add(action);
        else this._modalAnalysisRunning.delete(action);
        this._syncModalAnalysisButtons();
    },

    async _refreshCurrentPreviewDetails(imageId) {
        if (Number(this._currentPreviewId) !== Number(imageId)) return;
        const api = getRequiredGalleryAPI();
        const result = await api.getImage(imageId);
        if (Number(this._currentPreviewId) !== Number(imageId)) return;
        this._hydratePreview(result.image, result.tags);
    },

    _getArtistSinglePayload(imageId) {
        const artist = window.ArtistIdent;
        const threshold = Number(artist?.getThresholdValue?.());
        let modelConfig = {};
        if (artist && typeof artist._getIdentifyModelConfig === 'function') {
            modelConfig = artist._getIdentifyModelConfig();
        } else {
            const modelSource = String(document.getElementById('artist-model-source')?.value || 'huggingface').trim() || 'huggingface';
            const modelPath = String(document.getElementById('artist-model-path')?.value || '').trim();
            modelConfig = {
                model_source: modelSource,
                model_path: modelSource === 'local' ? modelPath : null,
                use_gpu: document.getElementById('artist-use-gpu') ? !!document.getElementById('artist-use-gpu').checked : null,
            };
        }
        return {
            image_id: Number(imageId),
            threshold: Number.isFinite(threshold) ? threshold : 0.03,
            top_k: 5,
            ...modelConfig,
        };
    },

    async _handleModalAnalysis(action) {
        const app = window.App || {};
        const api = getRequiredGalleryAPI();
        const showToast = app.showToast || window.showToast;
        const id = Number(this._currentPreviewId);
        if (!this._modalAnalysisActions.has(action) || !Number.isFinite(id) || this._modalAnalysisRunning.size > 0) return;

        this._setModalAnalysisBusy(action, true);
        try {
            if (action === 'aesthetic') {
                const result = await api.post(`/api/aesthetic/score/${id}`);
                const score = Number(result?.aesthetic_score);
                if (Number.isFinite(score)) {
                    this._patchImageState(id, { aesthetic_score: score });
                    if (Number(this._currentPreviewId) === id && this._lastModalImage && this._lastParsedData) {
                        this._renderModalSections(this._lastModalImage, this._lastParsedData);
                    }
                }
                await app.refreshAestheticStatus?.();
                const scoreText = Number.isFinite(score) ? score.toFixed(2) : '-';
                showToast?.(
                    this._t('modal.scoreThisDone', { score: scoreText }, 'Aesthetic score updated: {score}').replace('{score}', scoreText),
                    'success'
                );
                return;
            }

            if (action === 'color') {
                const result = await api.post(`/api/colors/analyze-single/${id}`);
                if (result?.color_data) {
                    const colorPatch = this._buildColorAnalysisPatch(result.color_data);
                    if (Object.keys(colorPatch).length > 0) {
                        this._patchImageState(id, colorPatch);
                    }
                }
                await window.ColorBackfill?.refreshProgress?.();
                showToast?.(this._t('modal.colorsThisDone', null, 'Color analysis updated'), 'success');
                return;
            }

            if (action === 'artist') {
                const result = await api.post('/api/artists/identify', this._getArtistSinglePayload(id));
                await window.ArtistIdent?.loadStats?.();
                // `artist` is the sentinel "undefined" unless confidence_level
                // is "high", so the toast is built from the shared tier
                // formatter instead of the raw field. Reading the field was
                // what put the literal word "undefined" on screen.
                const described = window.ArtistIdent.describeArtistResult(result);
                showToast?.(
                    described.level === 'high'
                        ? described.headline
                        : `${described.headline} — ${described.advisory}`,
                    described.level === 'high' ? 'success' : 'info'
                );
                return;
            }

            if (action === 'caption') {
                const tags = (this._lastModalTags || [])
                    .map((tag) => tag?.tag)
                    .filter(Boolean);
                const result = await api.post('/api/vlm/caption', { image_id: id, tags });
                if (result?.error) {
                    throw new Error(result.error);
                }
                if (result?.caption) {
                    this._patchImageState(id, { ai_caption: result.caption, nl_caption: result.caption });
                    if (Number(this._currentPreviewId) === id && this._lastModalImage) {
                        this._renderModalCaption(this._lastModalImage);
                    }
                }
                await this._refreshCurrentPreviewDetails(id);
                await app.loadImages?.();
                showToast?.(this._t('modal.captionThisDone', null, 'Caption updated'), 'success');
            }
        } catch (error) {
            showToast?.(
                formatUserError(error, this._t('modal.analysisThisFailed', null, 'This image analysis failed')),
                'error'
            );
        } finally {
            this._setModalAnalysisBusy(action, false);
        }
    },

});
