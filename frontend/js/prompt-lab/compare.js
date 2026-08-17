/**
 * prompt-lab/compare.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 2049-2105 (of 2,485):
 * populateImageSelectors and runCompare (/api/prompts/compare A/B diff).
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    populateImageSelectors() {
        const images = this._getPromptLabImages();
        const options = images.map(img =>
            `<option value="${img.id}">${escapeHtml(img.filename)}${img.aesthetic_score != null ? ' <svg class="icon" aria-hidden="true"><use href="#i-star"/></svg>' + img.aesthetic_score.toFixed(1) : ''}</option>`
        ).join('');
        const defaultOpt = `<option value="">${this._t('promptlab.selectImage', 'Select image...')}</option>`;
        ['pl-compare-a', 'pl-compare-b'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = defaultOpt + options;
        });
        this._renderImagePreviewCard('pl-compare-a-preview', document.getElementById('pl-compare-a')?.value || '', 'promptlab.comparePreviewEmpty', 'Choose an image to preview it here.');
        this._renderImagePreviewCard('pl-compare-b-preview', document.getElementById('pl-compare-b')?.value || '', 'promptlab.comparePreviewEmpty', 'Choose an image to preview it here.');
        if (!this.imageCatalogLoaded) {
            this._ensureImageCatalog().then(() => this.populateImageSelectors()).catch(() => {});
        }
    },

    async runCompare() {
        const idA = document.getElementById('pl-compare-a')?.value;
        const idB = document.getElementById('pl-compare-b')?.value;
        if (!idA || !idB) {
            window.App?.showToast?.(this._t('promptlab.selectTwoImages', 'Select two images to compare'), 'warning');
            return;
        }
        try {
            const result = await window.App.API.get(`/api/prompts/compare?id_a=${idA}&id_b=${idB}`);
            const container = document.getElementById('pl-compare-result');
            if (!container) return;
            const renderTags = (tags, cls) => tags.map(t => `<span class="promptlab-diff-tag ${cls}">${escapeHtml(t)}</span>`).join('');
            const commonTokens = Array.isArray(result.prompt_common) ? result.prompt_common : [];
            container.innerHTML = `
                <div class="promptlab-diff-card" style="grid-column:1/-1;">
                    <h5 style="color:#A2D3B4;">${this._t('promptlab.commonTokens', 'Common')} (${result.prompt_common.length})</h5>
                    <div class="promptlab-diff-tags">${renderTags(result.prompt_common, 'common') || `<span style="color:var(--text-muted)">${this._t('promptlab.none', 'None')}</span>`}</div>
                    <div class="promptlab-action-buttons" style="margin-top:10px;">
                        <button class="btn btn-primary btn-small" data-action="build-common" data-tokens="${escapeHtml(commonTokens.join('|'))}">${this._t('promptlab.buildFromCommon', 'Build from Common')}</button>
                    </div>
                </div>
                <div class="promptlab-diff-card">
                    <h5 style="color:#E7CAA9;">${this._t('promptlab.onlyInImage', 'Only in {name}', { name: result.image_a.filename }).replace('{name}', escapeHtml(result.image_a.filename))} (${result.prompt_only_a.length})</h5>
                    <div class="promptlab-diff-tags">${renderTags(result.prompt_only_a, 'only-a') || `<span style="color:var(--text-muted)">${this._t('promptlab.none', 'None')}</span>`}</div>
                    <div class="promptlab-action-buttons" style="margin-top:10px;">
                        <button class="btn btn-secondary btn-small" data-action="build-image" data-image-id="${result.image_a.id}">${this._t('promptlab.openImageABuild', 'Open Image A in Build')}</button>
                    </div>
                </div>
                <div class="promptlab-diff-card">
                    <h5 style="color:var(--warning);">${this._t('promptlab.onlyInImage', 'Only in {name}', { name: result.image_b.filename }).replace('{name}', escapeHtml(result.image_b.filename))} (${result.prompt_only_b.length})</h5>
                    <div class="promptlab-diff-tags">${renderTags(result.prompt_only_b, 'only-b') || `<span style="color:var(--text-muted)">${this._t('promptlab.none', 'None')}</span>`}</div>
                    <div class="promptlab-action-buttons" style="margin-top:10px;">
                        <button class="btn btn-secondary btn-small" data-action="build-image" data-image-id="${result.image_b.id}">${this._t('promptlab.openImageBBuild', 'Open Image B in Build')}</button>
                    </div>
                </div>`;
        } catch (e) {
            window.App?.showToast?.(`${this._t('promptlab.compareFailed', 'Compare failed')}: ${e.message || e}`, 'error');
        }
    },

});
