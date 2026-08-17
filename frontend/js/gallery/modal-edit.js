/**
 * gallery/modal-edit.js — gallery.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/gallery.js pre-cut
 * lines 3227-3533 (of 4,708): inline tag edit (FLOW-02) + caption edit (FE-3).
 * Classic script: joins the ONE unsealed window.Gallery object declared in
 * gallery/base.js, which loads FIRST (the shared consts + module helpers
 * live there); index.html lists the family in original line order.
 */
Object.assign(window.Gallery, {
    // ============== FLOW-02: inline tag editing in the preview modal ==============
    // Tags could only be edited in Dataset / Mass Tag — never where you actually
    // look at the image. The ✎ Edit toggle turns the read-only tag list into
    // removable chips + an add box; Save diffs against the original and commits
    // through the single-image scope of the bulk add/remove endpoints. AI rating
    // tags (general/sensitive/...) stay read-only — they're model output, not
    // user vocabulary.
    _ratingTagNames() {
        return ['general', 'sensitive', 'questionable', 'explicit'];
    },

    _bindTagEditOnce() {
        if (this._tagEditBound) return;
        this._tagEditBound = true;
        document.querySelector('#btn-edit-modal-tags')?.addEventListener('click', () => {
            if (this._tagsEditMode) this._exitTagEdit(); else this._enterTagEdit();
        });
        document.querySelector('#btn-cancel-modal-tags')?.addEventListener('click', () => this._exitTagEdit());
        document.querySelector('#btn-save-modal-tags')?.addEventListener('click', () => this._saveModalTags());
        const input = document.querySelector('#modal-tags-add-input');
        input?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                // When the tag autocomplete dropdown is open, Enter belongs
                // to the suggestion accept (listener order is not reliable
                // between same-node handlers, so check state instead).
                if (window.CaptionAutocomplete?.isOpen?.()) return;
                e.preventDefault();
                this._addTagFromInput();
            }
        });
    },

    _enterTagEdit() {
        const ratings = this._ratingTagNames();
        const otherTags = (this._lastModalTags || []).filter(t => !ratings.includes(t.tag));
        this._tagEditWorking = otherTags.map(t => t.tag);
        this._tagEditOriginal = [...this._tagEditWorking];
        this._tagsEditMode = true;
        const list = document.querySelector('#modal-tags-list');
        const editor = document.querySelector('#modal-tags-editor');
        if (list) { list.hidden = true; list.style.display = 'none'; }
        if (editor) editor.hidden = false;
        const editBtn = document.querySelector('#btn-edit-modal-tags');
        if (editBtn) editBtn.classList.add('active');
        this._renderTagEditChips();
        const input = document.querySelector('#modal-tags-add-input');
        if (input) { input.value = ''; input.focus(); }
    },

    _exitTagEdit() {
        this._tagsEditMode = false;
        const list = document.querySelector('#modal-tags-list');
        const editor = document.querySelector('#modal-tags-editor');
        if (editor) editor.hidden = true;
        if (list) { list.hidden = false; list.style.display = ''; }
        const editBtn = document.querySelector('#btn-edit-modal-tags');
        if (editBtn) editBtn.classList.remove('active');
    },

    _renderTagEditChips() {
        const wrap = document.querySelector('#modal-tags-edit-chips');
        if (!wrap) return;
        wrap.innerHTML = (this._tagEditWorking || []).map((tag, i) =>
            `<span class="tag tag-editable">${this._escapeHtml(tag)}<button type="button" class="tag-remove" data-idx="${i}" aria-label="Remove tag">×</button></span>`
        ).join('');
        wrap.querySelectorAll('.tag-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = Number(btn.dataset.idx);
                if (Number.isInteger(idx)) {
                    this._tagEditWorking.splice(idx, 1);
                    this._renderTagEditChips();
                }
            });
        });
    },

    _addTagFromInput() {
        const input = document.querySelector('#modal-tags-add-input');
        if (!input) return;
        const raw = (input.value || '').trim();
        if (!raw) return;
        // Accept comma-separated input; normalize to lowercase to match WD14 vocab.
        raw.split(',').map(s => s.trim().toLowerCase()).filter(Boolean).forEach(tag => {
            if (!this._tagEditWorking.includes(tag)) this._tagEditWorking.push(tag);
        });
        input.value = '';
        this._renderTagEditChips();
        input.focus();
    },

    async _saveModalTags() {
        const id = Number(this._currentPreviewId);
        if (!id) { this._exitTagEdit(); return; }
        // Fold any text still sitting in the add box into the working set first.
        this._addTagFromInput();
        const original = new Set(this._tagEditOriginal || []);
        const working = new Set(this._tagEditWorking || []);
        const added = [...working].filter(t => !original.has(t));
        const removed = [...original].filter(t => !working.has(t));
        if (added.length === 0 && removed.length === 0) { this._exitTagEdit(); return; }

        const app = window.App || {};
        const api = app.API;
        const saveBtn = document.querySelector('#btn-save-modal-tags');
        if (saveBtn) saveBtn.disabled = true;
        try {
            // FE-2s: both bulk endpoints journal applied ops server-side —
            // collect the op ids so the success toast can offer real undo.
            const opIds = [];
            if (added.length) {
                const result = await api.post('/api/tags/bulk/add', { image_ids: [id], tags: added, dry_run: false });
                if (result?.op_id && result?.undo_available) opIds.push(result.op_id);
            }
            if (removed.length) {
                const result = await api.post('/api/tags/bulk/remove', { image_ids: [id], tags: removed, dry_run: false });
                if (result?.op_id && result?.undo_available) opIds.push(result.op_id);
            }
            const toastOptions = opIds.length === 0 ? undefined : {
                duration: 10000,
                actionLabel: this._t('modal.undo', null, 'Undo'),
                onAction: () => this._undoModalTagOps(id, opIds)
            };
            app.showToast?.(this._t('modal.tagsSaved', null, 'Tags updated'), 'success', toastOptions);
            this._exitTagEdit();
            await this._reloadModalTags(id);
            app.loadImages?.();
        } catch (e) {
            app.showToast?.(this._t('modal.tagsSaveFailed', null, 'Failed to update tags'), 'error');
        } finally {
            if (saveBtn) saveBtn.disabled = false;
        }
    },

    async _undoModalTagOps(id, opIds) {
        const app = window.App || {};
        const api = app.API;
        try {
            // Undo newest-first so each restore sees the state it recorded.
            for (const opId of [...opIds].reverse()) {
                await api.post(`/api/tags/bulk/undo/${encodeURIComponent(opId)}`, {});
            }
            app.showToast?.(this._t('modal.tagsRestored', null, 'Tags restored'), 'success');
            await this._reloadModalTags(id);
            app.loadImages?.();
        } catch (e) {
            app.showToast?.(this._t('modal.tagsSaveFailed', null, 'Failed to update tags'), 'error');
        }
    },

    async _reloadModalTags(id) {
        const api = window.App?.API;
        if (!api?.getImage) return;
        try {
            const result = await api.getImage(Number(id));
            if (result?.tags) {
                this._lastModalTags = result.tags;
                this._renderModalTags(result.tags);
            }
        } catch (_e) {
            /* best-effort refresh */
        }
    },

    _serializeGenerationParams(image, parsedData) {
        const rawParamText = this._extractRawParameterText(image);
        if (rawParamText) {
            return rawParamText;
        }

        return this._buildGenerationParamEntries(image, parsedData)
            .map(({ label, value }) => `${label}: ${value}`)
            .join(', ');
    },

    _renderModalCaption(image) {
        const section = document.querySelector('#modal-caption-section');
        const textEl = document.querySelector('#modal-caption-text');
        if (!section || !textEl) return;

        this._bindCaptionEditOnce();
        this._exitCaptionEdit();
        // FE-3: the section stays visible even when empty — the ✎ Edit
        // button is the entry point for adding a caption from scratch.
        section.style.display = '';

        const caption = (image?.ai_caption || '').trim();
        if (caption) {
            textEl.textContent = caption;
            textEl.classList.remove('modal-caption-empty');
        } else {
            textEl.textContent = this._t('modal.noCaption', null,'No caption yet — use Edit to add one');
            textEl.classList.add('modal-caption-empty');
        }

        const nlView = document.querySelector('#modal-nl-caption-view');
        const nlText = document.querySelector('#modal-nl-caption-text');
        if (nlView && nlText) {
            const nl = (image?.nl_caption || '').trim();
            if (nl && nl !== caption) {
                nlText.textContent = nl;
                nlView.style.display = '';
            } else {
                nlView.style.display = 'none';
                nlText.textContent = '';
            }
        }
    },

    // ============== FE-3: manual caption editing in the preview modal ==============
    // Captions were read-only here — fixing one meant a round-trip through the
    // Dataset Maker. ✎ Edit swaps the read view for two textareas (display
    // caption + pure NL) and saves through PATCH /api/images/{id}/caption,
    // which only writes the fields present in the body (explicit-clear).
    _bindCaptionEditOnce() {
        if (this._captionEditBound) return;
        this._captionEditBound = true;
        document.querySelector('#btn-edit-caption')?.addEventListener('click', () => {
            if (this._captionEditMode) this._exitCaptionEdit(); else this._enterCaptionEdit();
        });
        document.querySelector('#btn-cancel-caption')?.addEventListener('click', () => this._exitCaptionEdit());
        document.querySelector('#btn-save-caption')?.addEventListener('click', () => this._saveModalCaption());
    },

    _enterCaptionEdit() {
        const image = this._lastModalImage;
        if (!image) return;
        this._captionEditMode = true;
        const aiBox = document.querySelector('#modal-caption-edit-ai');
        const nlBox = document.querySelector('#modal-caption-edit-nl');
        if (aiBox) {
            aiBox.value = image.ai_caption || '';
            window.CaptionAutocomplete?.attach?.(aiBox);
        }
        if (nlBox) nlBox.value = image.nl_caption || '';
        const view = document.querySelector('#modal-caption-view');
        const editor = document.querySelector('#modal-caption-editor');
        if (view) view.hidden = true;
        if (editor) editor.hidden = false;
        document.querySelector('#btn-edit-caption')?.classList.add('active');
        aiBox?.focus();
    },

    _exitCaptionEdit() {
        this._captionEditMode = false;
        const view = document.querySelector('#modal-caption-view');
        const editor = document.querySelector('#modal-caption-editor');
        if (editor) editor.hidden = true;
        if (view) view.hidden = false;
        document.querySelector('#btn-edit-caption')?.classList.remove('active');
    },

    async _saveModalCaption() {
        const id = Number(this._currentPreviewId);
        const image = this._lastModalImage;
        if (!id || !image) { this._exitCaptionEdit(); return; }

        const nextAi = (document.querySelector('#modal-caption-edit-ai')?.value ?? '').trim();
        const nextNl = (document.querySelector('#modal-caption-edit-nl')?.value ?? '').trim();
        const body = {};
        if (nextAi !== (image.ai_caption || '').trim()) body.ai_caption = nextAi;
        if (nextNl !== (image.nl_caption || '').trim()) body.nl_caption = nextNl;
        if (Object.keys(body).length === 0) { this._exitCaptionEdit(); return; }

        const app = window.App || {};
        const backup = { ai_caption: image.ai_caption || '', nl_caption: image.nl_caption || '' };
        const saveBtn = document.querySelector('#btn-save-caption');
        if (saveBtn) saveBtn.disabled = true;
        try {
            const updated = await app.API.patch(`/api/images/${id}/caption`, body);
            image.ai_caption = updated?.ai_caption ?? nextAi;
            image.nl_caption = updated?.nl_caption ?? nextNl;
            this._renderModalCaption(image);
            app.showToast?.(
                this._t('modal.captionSaved', null, 'Caption updated'),
                'success',
                {
                    duration: 10000,
                    actionLabel: this._t('modal.undo', null, 'Undo'),
                    onAction: () => this._undoCaptionEdit(id, backup)
                }
            );
        } catch (e) {
            app.showToast?.(this._t('modal.captionSaveFailed', null, 'Failed to save caption'), 'error');
        } finally {
            if (saveBtn) saveBtn.disabled = false;
        }
    },

    async _undoCaptionEdit(id, backup) {
        const app = window.App || {};
        try {
            const updated = await app.API.patch(`/api/images/${id}/caption`, {
                ai_caption: backup.ai_caption,
                nl_caption: backup.nl_caption
            });
            const image = this._lastModalImage;
            if (image && Number(this._currentPreviewId) === id) {
                image.ai_caption = updated?.ai_caption ?? backup.ai_caption;
                image.nl_caption = updated?.nl_caption ?? backup.nl_caption;
                this._renderModalCaption(image);
            }
            app.showToast?.(this._t('modal.captionRestored', null, 'Caption restored'), 'success');
        } catch (e) {
            app.showToast?.(this._t('modal.captionSaveFailed', null, 'Failed to save caption'), 'error');
        }
    },

});
