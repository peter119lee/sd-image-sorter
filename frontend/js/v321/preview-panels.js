/**
 * v321/preview-panels.js - v321-ui.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/v321-ui.js pre-cut lines 2442-2995
 * (of 3,164): (C) two-box preview editor, tools/diagnostics/cleanup panels, thumb.
 * Classic script: joins the ONE unsealed window.V321Integration object
 * declared in v321/base.js (loads FIRST); v321/boot.js registers the
 * DOMContentLoaded init LAST; index.html lists the family in original
 * line order.
 */
Object.assign(window.V321Integration, {

    _buildPreviewEditor() {
        const item = this._getPreviewItem();
        const panel = document.createElement('div');
        panel.className = 'export-preview-editor';
        if (!item) return panel;
        const id = Number(item.image_id);
        const caption = this._getRenderedCaption(id);

        const top = document.createElement('div');
        top.className = 'export-preview-current';
        top.appendChild(this._createPreviewThumb(item, 220));

        const meta = document.createElement('div');
        meta.className = 'export-preview-current-meta';
        const edited = this.editedCaptions.has(id) || this.editedNl.has(id);
        meta.innerHTML = `
            <span>${this._i18n('batchExport.previewCurrent', 'Current image')}</span>
            <strong></strong>
            <small>#${id}</small>
        `;
        meta.querySelector('strong').textContent = item.filename || '';
        if (edited) {
            const badge = document.createElement('em');
            badge.className = 'export-preview-edited-badge';
            badge.textContent = this._i18n('batchExport.previewEditedWillExport', 'Edited for export');
            meta.appendChild(badge);
        }
        top.appendChild(meta);

        const helper = document.createElement('p');
        helper.className = 'export-preview-editor-helper';
        helper.textContent = this._i18n(
            'batchExport.previewWorkbenchHelper',
            'Edit this caption here. Queue items marked Edited are used only when you export, copy, or download.'
        );

        // Aurora #25c: live "what the export writes" text — created before the
        // textareas so both input handlers can refresh it without a rerender.
        const willExportText = document.createElement('span');
        willExportText.className = 'export-preview-willexport-text';
        const refreshWillExport = () => { willExportText.textContent = this._getExportedCaption(id); };

        const textarea = document.createElement('textarea');
        textarea.className = 'export-preview-textarea export-preview-main-textarea';
        if (this.editedCaptions.has(id)) textarea.classList.add('edited');
        textarea.dataset.imageId = String(id);
        textarea.value = caption;
        textarea.addEventListener('input', () => {
            this._setPreviewCaption(id, textarea.value);
            textarea.classList.toggle('edited', this.editedCaptions.has(id));
            refreshWillExport();
        });
        textarea.addEventListener('blur', () => this._renderPreviewWorkbench());
        window.CaptionAutocomplete?.attach?.(textarea);

        const chips = document.createElement('div');
        chips.className = 'export-preview-token-list';
        const captionTokens = this._splitCaptionTokens(caption);
        for (const token of captionTokens) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'export-preview-token';
            chip.title = this._i18n('batchExport.removeFromCurrent', 'Remove from current');
            chip.textContent = `${token} ×`;
            this._applyTokenCategoryClass(chip, token);
            chip.addEventListener('click', () => {
                this._applyTokenToCaption(id, token, 'remove');
                this._renderPreviewWorkbench();
            });
            chips.appendChild(chip);
        }
        this._recolorTokensWhenCategorized(captionTokens);

        // Aurora #25c: per-image caption type (booru | both | nl) + NL box —
        // consolidated with the Dataset Maker two-box editor. CaptionCore owns
        // the semantics; the backend composes identically at export time.
        const ctype = this._getCaptionType(id);
        const showNl = ctype === 'nl' || ctype === 'both';

        const captype = document.createElement('div');
        captype.className = 'export-preview-captype';
        const captypeRow = document.createElement('div');
        captypeRow.className = 'export-preview-captype-row';
        const captypeLabel = document.createElement('span');
        captypeLabel.className = 'export-preview-captype-label';
        captypeLabel.textContent = this._i18n('dataset.captionTypeLabel', 'This image:');
        const seg = document.createElement('div');
        seg.className = 'export-preview-captype-seg';
        seg.setAttribute('role', 'radiogroup');
        seg.setAttribute('aria-label', captypeLabel.textContent);
        for (const value of ['booru', 'both', 'nl']) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'export-preview-captype-btn';
            b.dataset.captionType = value;
            b.textContent = this._captionTypeDisplayLabel(value);
            const on = value === ctype;
            b.classList.toggle('is-active', on);
            b.setAttribute('role', 'radio');
            b.setAttribute('aria-checked', on ? 'true' : 'false');
            b.addEventListener('click', () => {
                this._setCaptionType(id, value);
                this._renderPreviewWorkbench();
            });
            seg.appendChild(b);
        }
        const bulk = document.createElement('div');
        bulk.className = 'export-preview-captype-bulk';
        const loadedCount = this._loadedPreviewIds().length;
        const applyAllBtn = document.createElement('button');
        applyAllBtn.type = 'button';
        applyAllBtn.className = 'btn btn-small btn-ghost';
        applyAllBtn.textContent = this._i18n('batchExport.captypeApplyLoaded', 'Set loaded {count} to this type', { count: loadedCount });
        applyAllBtn.addEventListener('click', () => this._applyCaptionTypeToLoaded(this._getCaptionType(id)));
        const autoBtn = document.createElement('button');
        autoBtn.type = 'button';
        autoBtn.className = 'btn btn-small btn-ghost';
        autoBtn.textContent = this._i18n('batchExport.captypeAutoLoaded', 'Auto-assign (loaded {count})', { count: loadedCount });
        autoBtn.addEventListener('click', () => this._autoAssignTypesLoaded());
        bulk.append(applyAllBtn, autoBtn);
        captypeRow.append(captypeLabel, seg, bulk);
        const captypeHint = document.createElement('small');
        captypeHint.className = 'export-preview-captype-hint';
        captypeHint.textContent = this._i18n('batchExport.captypeHint',
            'Booru = tags only · Both = tags + sentence · NL = sentence only (applies in template/tags modes)');
        captype.append(captypeRow, captypeHint);

        const nlWrap = document.createElement('div');
        nlWrap.className = 'export-preview-nl';
        nlWrap.hidden = !showNl;
        const nlLabel = document.createElement('span');
        nlLabel.className = 'export-preview-nl-label';
        nlLabel.textContent = this._i18n('batchExport.nlBoxLabel', 'Natural-language caption (NL)');
        const nlBox = document.createElement('textarea');
        nlBox.className = 'export-preview-textarea export-preview-nl-textarea';
        if (this.editedNl.has(id)) nlBox.classList.add('edited');
        nlBox.value = this._getNlText(id);
        let nlTimer = null;
        nlBox.addEventListener('input', () => {
            if (nlTimer) clearTimeout(nlTimer);
            nlTimer = setTimeout(() => {
                nlTimer = null;
                this._setNlEdit(id, nlBox.value);
                nlBox.classList.toggle('edited', this.editedNl.has(id));
                refreshWillExport();
            }, 200);
        });
        nlBox.addEventListener('blur', () => {
            // Flush a pending debounce BEFORE the rerender, otherwise a fast
            // type -> blur re-renders the box from state that's 200ms behind.
            if (nlTimer) {
                clearTimeout(nlTimer);
                nlTimer = null;
                this._setNlEdit(id, nlBox.value);
            }
            this._renderPreviewWorkbench();
        });
        nlWrap.append(nlLabel, nlBox);

        const willExport = document.createElement('div');
        willExport.className = 'export-preview-willexport';
        willExport.hidden = !showNl;
        const willExportLabel = document.createElement('strong');
        willExportLabel.textContent = this._i18n('batchExport.willExportPreview', 'Will export:');
        refreshWillExport();
        willExport.append(willExportLabel, willExportText);

        const actions = document.createElement('div');
        actions.className = 'export-preview-editor-actions';
        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'btn btn-small btn-ghost';
        reset.textContent = this._i18n('batchExport.resetCurrentPreview', 'Reset current');
        reset.addEventListener('click', () => this._resetPreviewCaption(id));
        const resetAll = document.createElement('button');
        resetAll.type = 'button';
        resetAll.className = 'btn btn-small btn-ghost';
        resetAll.textContent = this._i18n('batchExport.resetAllPreview', 'Reset all');
        resetAll.addEventListener('click', () => this._resetAllPreviewCaptions());
        actions.append(reset, resetAll);

        panel.append(top, helper, textarea, chips, captype, nlWrap, willExport, actions);
        return panel;
    },

    _buildPreviewTools() {
        const panel = document.createElement('div');
        panel.className = 'export-preview-tools';

        const common = this._getCommonPreviewTokens();
        const head = document.createElement('div');
        head.className = 'export-preview-panel-head';
        head.innerHTML = `<strong>${this._i18n('batchExport.commonTags', 'Common tags')}</strong><span>${common.length}</span>`;

        const helper = document.createElement('p');
        helper.className = 'export-preview-tools-helper';
        helper.textContent = this._i18n(
            'batchExport.commonTagsHelper',
            'Tags shared by preview images. Click a tag to add it to the current caption.'
        );

        const commonList = document.createElement('div');
        commonList.className = 'export-preview-common-tags';
        for (const item of common) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'export-preview-common-tag';
            chip.title = this._i18n('batchExport.addToCurrent', 'Add to current');
            chip.innerHTML = `<span></span><small>${item.count}</small>`;
            chip.querySelector('span').textContent = item.token;
            this._applyTokenCategoryClass(chip, item.token);
            chip.addEventListener('click', () => {
                const active = this._getPreviewItem();
                if (!active) return;
                this._applyTokenToCaption(active.image_id, item.token, 'add');
                this._renderPreviewWorkbench();
            });
            commonList.appendChild(chip);
        }
        this._recolorTokensWhenCategorized(common.map((item) => item.token));
        if (!common.length) {
            const empty = document.createElement('p');
            empty.className = 'export-preview-empty-tools';
            empty.textContent = this._i18n('batchExport.noCommonTags', 'No tags');
            commonList.appendChild(empty);
        }

        const diagnostics = this._buildPreviewDiagnostics();
        const cleanup = this._buildPreviewCleanupTools();

        const form = document.createElement('div');
        form.className = 'export-preview-tag-form';

        // Position toggle as inline icon buttons
        const posPrepend = document.createElement('button');
        posPrepend.type = 'button';
        posPrepend.className = 'btn btn-small btn-ghost active';
        posPrepend.textContent = '↑';
        posPrepend.title = this._i18n('batchExport.positionFront', 'Front');
        posPrepend.dataset.pos = 'prepend';
        const posAppend = document.createElement('button');
        posAppend.type = 'button';
        posAppend.className = 'btn btn-small btn-ghost';
        posAppend.textContent = '↓';
        posAppend.title = this._i18n('batchExport.positionBack', 'Back');
        posAppend.dataset.pos = 'append';
        const getPosition = () => posAppend.classList.contains('active') ? 'append' : 'prepend';
        posPrepend.addEventListener('click', () => { posPrepend.classList.add('active'); posAppend.classList.remove('active'); });
        posAppend.addEventListener('click', () => { posAppend.classList.add('active'); posPrepend.classList.remove('active'); });

        const inputRow = document.createElement('div');
        inputRow.className = 'export-preview-tag-input-row';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'input-field';
        input.id = 'export-preview-tag-input';
        input.placeholder = this._i18n('batchExport.tagToolPlaceholder', 'tag to add or remove');
        input.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            const active = this._getPreviewItem();
            if (!active) return;
            const tags = input.value.split(',').map(t => t.trim()).filter(Boolean);
            for (const tag of tags) this._applyTokenToCaption(active.image_id, tag, 'add', getPosition());
            input.value = '';
            this._renderPreviewWorkbench();
        });
        inputRow.append(posPrepend, posAppend, input);

        const addCurrent = this._toolButton('batchExport.addToCurrent', 'Add', () => {
            const active = this._getPreviewItem();
            if (!active) return;
            const tags = input.value.split(',').map(t => t.trim()).filter(Boolean);
            for (const tag of tags) this._applyTokenToCaption(active.image_id, tag, 'add', getPosition());
            input.value = '';
            this._renderPreviewWorkbench();
        });
        const removeCurrent = this._toolButton('batchExport.removeFromCurrent', 'Remove', () => {
            const active = this._getPreviewItem();
            if (!active) return;
            const tags = input.value.split(',').map(t => t.trim()).filter(Boolean);
            for (const tag of tags) this._applyTokenToCaption(active.image_id, tag, 'remove');
            input.value = '';
            this._renderPreviewWorkbench();
        });
        const addAll = this._toolButton('batchExport.addToAllPreview', '+All images', async () => {
            const tags = input.value.split(',').map(t => t.trim()).filter(Boolean);
            if (!tags.length) return;
            const count = this._queueActionCount();
            if (!confirm(this._i18n('batchExport.confirmAddAll', `Add "${tags.join(', ')}" to all ${count} images?`, { tags: tags.join(', '), count }))) return;
            for (const tag of tags) await this._applyTokenToAll(tag, 'add', getPosition());
            input.value = '';
        });
        const removeAll = this._toolButton('batchExport.removeFromAllPreview', '-All images', async () => {
            const tags = input.value.split(',').map(t => t.trim()).filter(Boolean);
            if (!tags.length) return;
            const count = this._queueActionCount();
            if (!confirm(this._i18n('batchExport.confirmRemoveAll', `Remove "${tags.join(', ')}" from all ${count} images?`, { tags: tags.join(', '), count }))) return;
            for (const tag of tags) await this._applyTokenToAll(tag, 'remove');
            input.value = '';
        });
        form.append(inputRow, addCurrent, removeCurrent, addAll, removeAll);

        // Aurora Phase 3 (#25c): the health-check strip is always visible so
        // dataset problems (empty / blacklist / duplicate / over-length /
        // missing trigger) surface without a click. Only the heavier Cleanup
        // tools stay behind a disclosure.
        const cleanupTools = document.createElement('details');
        cleanupTools.className = 'export-preview-tools-disclosure';
        const cleanupSummary = document.createElement('summary');
        const cleanupLabel = this._i18n('batchExport.previewCleanupTools', 'Cleanup');
        const metrics = this._getPreviewDiagnostics();
        const cleanupSummaryLabel = document.createElement('span');
        cleanupSummaryLabel.textContent = cleanupLabel;
        const cleanupSummaryCount = document.createElement('small');
        cleanupSummaryCount.textContent = `${metrics.edited}/${metrics.total}`;
        cleanupSummary.append(cleanupSummaryLabel, cleanupSummaryCount);
        cleanupTools.append(cleanupSummary, cleanup);

        panel.append(head, helper, commonList, form, diagnostics, cleanupTools);
        return panel;
    },

    _toolButton(key, fallback, handler, options = {}) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = options.className || 'btn btn-small btn-secondary';
        btn.dataset.i18nKey = key;
        if (options.tool) btn.dataset.previewTool = options.tool;
        btn.textContent = this._i18n(key, fallback);
        btn.addEventListener('click', handler);
        return btn;
    },

    _buildPreviewDiagnostics() {
        const metrics = this._getPreviewDiagnostics();
        const section = document.createElement('div');
        section.className = 'export-preview-checks';
        const title = document.createElement('strong');
        title.textContent = this._i18n('batchExport.previewChecks', 'Checks');
        const grid = document.createElement('div');
        grid.className = 'export-preview-stat-grid';
        const rows = [
            ['batchExport.previewEditedCount', 'Edited', `${metrics.edited}/${metrics.total}`, metrics.edited > 0],
            ['batchExport.previewEmptyCount', 'Empty', String(metrics.empty), metrics.empty > 0],
            ['batchExport.previewBlockedCount', 'Blacklist hits', String(metrics.blockedHits), metrics.blockedHits > 0],
            ['batchExport.previewDuplicateCount', 'Duplicates', String(metrics.duplicateHits), metrics.duplicateHits > 0],
            ['batchExport.previewMaxTokens', 'Max tokens', String(metrics.maxTokens), metrics.maxTokens > 75],
        ];
        // Missing-trigger check only appears when a LoRA trigger word is set.
        if (metrics.hasTrigger) {
            rows.push(['batchExport.previewMissingTrigger', 'Missing trigger', String(metrics.missingTrigger), metrics.missingTrigger > 0]);
        }
        for (const [key, label, value, warn] of rows) {
            const stat = document.createElement('div');
            stat.className = 'export-preview-stat';
            if (warn) stat.classList.add('warn');
            stat.innerHTML = `<span></span><strong></strong>`;
            stat.querySelector('span').textContent = this._i18n(key, label);
            stat.querySelector('strong').textContent = value;
            grid.appendChild(stat);
        }
        section.append(title, grid);
        return section;
    },

    _buildPreviewCleanupTools() {
        const section = document.createElement('div');
        section.className = 'export-preview-cleanup';
        const title = document.createElement('strong');
        title.textContent = this._i18n('batchExport.previewCleanupTools', 'Cleanup');
        const grid = document.createElement('div');
        grid.className = 'export-preview-cleanup-grid';
        const activeId = () => this._getPreviewItem()?.image_id;
        const addRow = (labelKey, labelFallback, currentKey, currentFallback, currentTool, currentHandler, allKey, allFallback, allTool, allHandler) => {
            const row = document.createElement('div');
            row.className = 'export-preview-cleanup-row';
            const label = document.createElement('span');
            label.textContent = this._i18n(labelKey, labelFallback);
            row.append(
                label,
                this._toolButton(currentKey, currentFallback, currentHandler, {
                    className: 'btn btn-small btn-ghost',
                    tool: currentTool,
                }),
                this._toolButton(allKey, allFallback, allHandler, {
                    className: 'btn btn-small btn-ghost',
                    tool: allTool,
                }),
            );
            grid.appendChild(row);
        };
        addRow('batchExport.cleanupDedupeLabel', 'Dedupe', 'batchExport.cleanupCurrent', 'Current', 'dedupe-current', () => {
            const id = activeId();
            if (!id) return;
            this._cleanupPreviewCaption(id, { dedupe: true });
            this._renderPreviewWorkbench();
        }, 'batchExport.cleanupAllImages', 'All images', 'dedupe-all', async () => {
            const count = this._queueActionCount();
            if (!confirm(this._i18n('batchExport.confirmCleanupAll', `Remove duplicate tags from all ${count} images?`, { count }))) return;
            await this._cleanupAllPreviewCaptions({ dedupe: true });
        });
        addRow('batchExport.cleanupBlacklistLabel', 'Blacklist', 'batchExport.cleanupCurrent', 'Current', 'blacklist-current', () => {
            const id = activeId();
            if (!id) return;
            this._cleanupPreviewCaption(id, { blacklist: true, dedupe: true });
            this._renderPreviewWorkbench();
        }, 'batchExport.cleanupAllImages', 'All images', 'blacklist-all', async () => {
            const count = this._queueActionCount();
            const blacklist = this._getBlacklistTokens();
            const preview = blacklist.length ? blacklist.slice(0, 10).join(', ') + (blacklist.length > 10 ? '...' : '') : '(empty)';
            if (!confirm(this._i18n('batchExport.confirmBlacklistAll', `Remove blacklisted tags [${preview}] from all ${count} images?`, { preview, count }))) return;
            await this._cleanupAllPreviewCaptions({ blacklist: true, dedupe: true });
        });
        // Pencil icon for editing blacklist inline, appended to the Blacklist row
        const blacklistRow = grid.lastElementChild;
        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'btn btn-small btn-ghost';
        editBtn.title = this._i18n('batchExport.editBlacklist', 'Edit blacklist...');
        editBtn.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-edit\"/></svg>";
        editBtn.addEventListener('click', () => {
            let existing = grid.querySelector('.inline-blacklist-editor');
            if (existing) { existing.remove(); return; }
            const editor = document.createElement('div');
            editor.className = 'inline-blacklist-editor';
            editor.style.cssText = 'margin-top:8px; display:flex; flex-direction:column; gap:6px; grid-column:1/-1;';
            const hint = document.createElement('small');
            hint.style.color = 'var(--text-muted)';
            hint.textContent = this._i18n('batchExport.blacklistInlineHint', 'Comma-separated tags to exclude from export:');
            const textarea = document.createElement('textarea');
            textarea.className = 'input-field';
            textarea.rows = 3;
            textarea.style.fontSize = '12px';
            const mainTextarea = document.getElementById('batch-export-blacklist');
            textarea.value = mainTextarea?.value || '';
            textarea.addEventListener('input', () => { if (mainTextarea) mainTextarea.value = textarea.value; });
            editor.append(hint, textarea);
            blacklistRow.after(editor);
            textarea.focus();
        });
        blacklistRow.appendChild(editBtn);
        addRow('batchExport.cleanupBoilerplateLabel', 'Quality/rating', 'batchExport.cleanupCurrent', 'Current', 'boilerplate-current', () => {
            const id = activeId();
            if (!id) return;
            this._cleanupPreviewCaption(id, { boilerplate: true, dedupe: true });
            this._renderPreviewWorkbench();
        }, 'batchExport.cleanupAllImages', 'All images', 'boilerplate-all', async () => {
            const count = this._queueActionCount();
            const boilerplate = this._getLoraBoilerplateTokens().slice(0, 8).join(', ') + '...';
            if (!confirm(this._i18n('batchExport.confirmBoilerplateAll', `Remove quality/rating tags [${boilerplate}] from all ${count} images?`, { boilerplate, count }))) return;
            await this._cleanupAllPreviewCaptions({ boilerplate: true, dedupe: true });
        });
        // Category batch removal row
        const catRow = document.createElement('div');
        catRow.className = 'export-preview-cleanup-row';
        const catLabel = document.createElement('span');
        catLabel.textContent = this._i18n('batchExport.cleanupCategoryLabel', 'Category');
        const catSelect = document.createElement('select');
        catSelect.className = 'input-field';
        catSelect.style.cssText = 'flex:1; font-size:12px; padding:2px 6px;';
        for (const opt of ['character', 'copyright', 'meta']) {
            const o = document.createElement('option');
            o.value = opt;
            o.textContent = opt.charAt(0).toUpperCase() + opt.slice(1);
            catSelect.appendChild(o);
        }
        const catBtn = this._toolButton('batchExport.cleanupCategoryRemoveAll', 'Remove All', async () => {
            await this._removeTagsByCategory(catSelect.value);
        }, { className: 'btn btn-small btn-ghost' });
        catRow.append(catLabel, catSelect, catBtn);
        grid.appendChild(catRow);
        section.append(title, grid);
        return section;
    },

    _getPreviewDiagnostics() {
        const blacklist = new Set(this._getBlacklistTokens().map((token) => this._normalizeCaptionToken(token)));
        // Cross-reference the Dataset Maker LoRA trigger word. Missing-trigger
        // is only a meaningful check when the user actually set one.
        const triggerRaw = (document.getElementById('dataset-trigger')?.value || '').trim();
        const triggerKey = triggerRaw ? this._normalizeCaptionToken(triggerRaw) : '';
        let empty = 0;
        let blockedHits = 0;
        let duplicateHits = 0;
        let maxTokens = 0;
        let missingTrigger = 0;
        // Use previewResults for diagnostics (only covers loaded items)
        for (const item of this.previewResults) {
            // #25c: measure the COMPOSED final caption (type + NL + transforms)
            // so the checks strip reflects what the export will actually write.
            const tokens = this._splitCaptionTokens(this._getExportedCaption(item.image_id));
            if (!tokens.length) empty += 1;
            maxTokens = Math.max(maxTokens, tokens.length);
            const seen = new Set();
            for (const token of tokens) {
                const key = this._normalizeCaptionToken(token);
                if (blacklist.has(key)) blockedHits += 1;
                if (seen.has(key)) duplicateHits += 1;
                seen.add(key);
            }
            // Only flag non-empty captions that forgot the trigger — empty
            // captions are already surfaced by the 'empty' metric.
            if (triggerKey && tokens.length && !seen.has(triggerKey)) missingTrigger += 1;
        }
        return {
            total: this.queueTotalCount || this.queueImageIds.length || this.previewResults.length,
            edited: new Set([
                ...Array.from(this.editedCaptions.keys()).map(Number),
                ...Array.from(this.editedNl.keys()).map(Number),
            ]).size,
            empty,
            blockedHits,
            duplicateHits,
            maxTokens,
            hasTrigger: !!triggerKey,
            missingTrigger,
        };
    },

    _getCommonPreviewTokens() {
        const counts = new Map();
        for (const item of this.previewResults) {
            const seen = new Set();
            for (const token of this._splitCaptionTokens(this._getRenderedCaption(item.image_id))) {
                const key = token.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                const current = counts.get(key) || { token, count: 0 };
                current.count += 1;
                counts.set(key, current);
            }
        }
        const total = Math.max(1, this.previewResults.length);
        return Array.from(counts.values())
            .filter((item) => item.count > 1 || total === 1)
            .sort((a, b) => b.count - a.count || a.token.localeCompare(b.token));
    },

    _createPreviewThumb(item, size) {
        const img = document.createElement('img');
        img.className = 'export-preview-thumb';
        img.alt = item.filename || `Image ${item.image_id}`;
        img.src = window.API?.getThumbnailUrl?.(item.image_id, size) || `/api/image-thumbnail/${item.image_id}?size=${size}`;
        img.loading = 'lazy';
        img.onerror = () => {
            img.removeAttribute('src');
            img.style.background = 'linear-gradient(135deg, #1f2937 0%, #111827 100%)';
            img.alt = 'image';
        };
        return img;
    },

});
