/**
 * similar/render.js — similar.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/similar.js pre-cut
 * lines 1103-1363 (of 1,517): renderSearchResults, renderDuplicateResults
 * (the '// ===== Rendering =====' section comment travels with them),
 * loadMoreSearchResults, loadMoreDuplicateResults, _previewImage,
 * _sendToEdit, _openInReader, _openInBuild, _addToDataset (its FLOW-04
 * doc comment travels with it), _addToCollection, _renderSearchResult,
 * _renderDuplicatePair — result cards, duplicate pairs + the gallery/
 * reader/censor/dataset/collection/build handoffs. Classic non-strict
 * script: joins the ONE unsealed window.SimilarImages object declared in
 * similar/core.js, which loads FIRST; boot.js publishes initSimilar LAST.
 */
Object.assign(window.SimilarImages, {
    // ============== Rendering ==============

    renderSearchResults() {
        const container = document.getElementById('similar-results');
        const loadMoreBtn = document.getElementById('btn-similar-load-more');
        if (!container) return;

        if (this.searchResults.length === 0) {
            container.innerHTML = `<div class="empty-state">${escapeHtml(this.searchEmptyMessage)}</div>`;
            if (loadMoreBtn) loadMoreBtn.style.display = 'none';
            return;
        }

        const { API } = window.App;
        const getThumbnailUrl = (id) => API?.getThumbnailUrl?.(id) ?? `/api/image-thumbnail/${id}?size=256`;
        const fragment = document.createDocumentFragment();

        this.searchResults.forEach((result) => {
            fragment.appendChild(this._renderSearchResult(result, getThumbnailUrl));
        });

        container.replaceChildren(fragment);

        container.querySelectorAll('.similar-result').forEach(el => {
            el.addEventListener('click', () => {
                const id = parseInt(el.dataset.id, 10);
                this._previewImage(id);
            });
        });

        container.querySelectorAll('.similar-action-btn').forEach(btn => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id, 10);
                if (!id) return;
                if (action === 'preview') this._previewImage(id);
                if (action === 'reader') this._openInReader(id, btn.dataset.filename || '');
                if (action === 'edit') this._sendToEdit(id);
                if (action === 'dataset') this._addToDataset(id);
                if (action === 'collection') this._addToCollection(id);
                if (action === 'build') this._openInBuild(id);
            });
        });

        if (loadMoreBtn) {
            loadMoreBtn.style.display = this.searchHasMore ? 'inline-flex' : 'none';
        }
    },

    renderDuplicateResults() {
        const container = document.getElementById('similar-duplicates');
        const loadMoreBtn = document.getElementById('btn-similar-duplicates-more');
        if (!container) return;

        if (this.duplicateResults.length === 0) {
            container.innerHTML = `<div class="empty-state">${escapeHtml(this.duplicateEmptyMessage)}</div>`;
            if (loadMoreBtn) loadMoreBtn.style.display = 'none';
            return;
        }

        const { API } = window.App;
        const getThumbnailUrl = (id) => API?.getThumbnailUrl?.(id) ?? `/api/image-thumbnail/${id}?size=256`;
        const fragment = document.createDocumentFragment();

        this.duplicateResults.forEach((pair) => {
            fragment.appendChild(this._renderDuplicatePair(pair, getThumbnailUrl));
        });

        container.replaceChildren(fragment);

        container.querySelectorAll('.dup-image').forEach(el => {
            el.addEventListener('click', () => {
                const id = parseInt(el.dataset.id, 10);
                this._previewImage(id);
            });
        });

        container.querySelectorAll('.similar-action-btn').forEach(btn => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id, 10);
                if (!id) return;
                if (action === 'preview') this._previewImage(id);
                if (action === 'reader') this._openInReader(id, btn.dataset.filename || '');
                if (action === 'edit') this._sendToEdit(id);
                if (action === 'build') this._openInBuild(id);
            });
        });

        if (loadMoreBtn) {
            loadMoreBtn.style.display = this.duplicateHasMore ? 'inline-flex' : 'none';
        }
    },

    async loadMoreSearchResults() {
        if (this.currentSearchMode === 'id' && this.currentSearchId) {
            await this.searchByImage(this.currentSearchId, { append: true });
            return;
        }
        if (this.currentSearchMode === 'upload' && this.currentSearchFile) {
            await this.searchByUpload(this.currentSearchFile, { append: true });
            return;
        }
        if (this.currentSearchMode === 'text' && this.currentSearchText) {
            await this.searchByText(this.currentSearchText, { append: true });
        }
    },

    async loadMoreDuplicateResults() {
        await this.findDuplicates({ append: true });
    },

    _previewImage(id) {
        if (window.App && typeof window.App.openGalleryPreview === 'function') {
            window.App.openGalleryPreview(id);
        } else if (window.Gallery) {
            window.Gallery.openPreview(id);
        }
    },

    _sendToEdit(id) {
        if (window.App?.addToCensorQueue) {
            window.App.addToCensorQueue([id]);
        }
    },

    _openInReader(id, filename = '') {
        window.App?.openReaderFromImage?.(id, filename);
    },

    _openInBuild(id) {
        window.App?.openPromptBuildFromImage?.(id);
    },

    // FLOW-04: Similar results used to dead-end at Preview/Reader/Edit/Build —
    // you couldn't pull a found image into a training set or collection without
    // leaving for the Gallery. These reuse the same handoff targets the gallery
    // context menu / preview modal use.
    _addToDataset(id) {
        if (typeof window.App?.addToDatasetMaker === 'function') {
            window.App.addToDatasetMaker([id], { switchView: true, showToast: true });
        } else {
            window.App?.showToast?.(this._t('selection.sendToDatasetMakerUnavailable', 'Dataset Maker module not loaded yet — try again in a moment.'), 'error');
        }
    },

    _addToCollection(id) {
        window.CollectionsUI?.openAddToCollectionPicker?.([id]);
    },

    _renderSearchResult(result, getThumbnailUrl) {
        const card = document.createElement('div');
        card.className = 'similar-result';
        card.dataset.id = String(result.id);

        const thumb = document.createElement('div');
        thumb.className = 'similar-thumb';

        const img = document.createElement('img');
        img.src = getThumbnailUrl(result.id);
        img.alt = result.filename || '';
        img.loading = 'lazy';
        thumb.appendChild(img);

        const info = document.createElement('div');
        info.className = 'similar-info';

        const score = document.createElement('span');
        score.className = 'similar-score';
        score.textContent = `${(result.similarity * 100).toFixed(1)}%`;

        const name = document.createElement('span');
        name.className = 'similar-name';
        name.title = result.filename || '';
        name.textContent = result.filename || this._t('similar.itemUnknown', 'Unknown');

        const actions = document.createElement('div');
        actions.className = 'similar-actions';
        actions.innerHTML = `
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="preview" data-id="${result.id}"><svg class="icon" aria-hidden="true"><use href="#i-eye"/></svg> ${this._t('similar.preview', 'Preview')}</button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="reader" data-id="${result.id}" data-filename="${escapeHtml(result.filename || '')}"><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg> ${this._t('similar.reader', 'Reader')}</button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="edit" data-id="${result.id}"><svg class="icon" aria-hidden="true"><use href="#i-grid"/></svg> ${this._t('similar.edit', 'Edit')}</button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="dataset" data-id="${result.id}"><svg class="icon" aria-hidden="true"><use href="#i-package"/></svg> ${this._t('similar.dataset', 'Dataset')}</button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="collection" data-id="${result.id}"><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg> ${this._t('similar.collection', 'Collection')}</button>
            <button class="btn btn-secondary btn-small similar-action-btn" data-action="build" data-id="${result.id}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg> ${this._t('similar.build', 'Build')}</button>
        `;

        info.append(score, name, actions);
        card.append(thumb, info);
        return card;
    },

    _renderDuplicatePair(pair, getThumbnailUrl) {
        const wrapper = document.createElement('div');
        wrapper.className = 'duplicate-pair';

        // Backend returns {image_a: {id, filename}, image_b: {id, filename}, similarity}
        // Legacy shape: {id1, filename1, id2, filename2, similarity}
        const id1 = pair.id1 ?? pair.image_a?.id;
        const id2 = pair.id2 ?? pair.image_b?.id;
        const filename1 = pair.filename1 ?? pair.image_a?.filename ?? '';
        const filename2 = pair.filename2 ?? pair.image_b?.filename ?? '';
        const similarity = pair.similarity ?? 0;

        const first = document.createElement('div');
        first.className = 'dup-image';
        if (id1 != null) first.dataset.id = String(id1);

        const firstImg = document.createElement('img');
        firstImg.src = id1 != null ? getThumbnailUrl(id1) : '';
        firstImg.alt = '';
        firstImg.loading = 'lazy';

        const firstName = document.createElement('span');
        firstName.className = 'dup-name';
        firstName.textContent = filename1;

        const firstActions = document.createElement('div');
        firstActions.className = 'similar-actions';
        firstActions.innerHTML = `
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="preview" data-id="${id1}" title="${this._t('similar.preview', 'Preview')}"><svg class="icon" aria-hidden="true"><use href="#i-eye"/></svg></button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="reader" data-id="${id1}" data-filename="${escapeHtml(filename1 || '')}" title="${this._t('similar.reader', 'Reader')}"><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg></button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="edit" data-id="${id1}" title="${this._t('similar.edit', 'Edit')}"><svg class="icon" aria-hidden="true"><use href="#i-grid"/></svg></button>
            <button class="btn btn-secondary btn-small similar-action-btn" data-action="build" data-id="${id1}" title="${this._t('similar.build', 'Build')}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg></button>
        `;

        first.append(firstImg, firstName, firstActions);

        const score = document.createElement('div');
        score.className = 'dup-score';
        score.textContent = `${(similarity * 100).toFixed(1)}%`;

        const second = document.createElement('div');
        second.className = 'dup-image';
        if (id2 != null) second.dataset.id = String(id2);

        const secondImg = document.createElement('img');
        secondImg.src = id2 != null ? getThumbnailUrl(id2) : '';
        secondImg.alt = '';
        secondImg.loading = 'lazy';

        const secondName = document.createElement('span');
        secondName.className = 'dup-name';
        secondName.textContent = filename2;

        const secondActions = document.createElement('div');
        secondActions.className = 'similar-actions';
        secondActions.innerHTML = `
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="preview" data-id="${id2}" title="${this._t('similar.preview', 'Preview')}"><svg class="icon" aria-hidden="true"><use href="#i-eye"/></svg></button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="reader" data-id="${id2}" data-filename="${escapeHtml(filename2 || '')}" title="${this._t('similar.reader', 'Reader')}"><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg></button>
            <button class="btn btn-ghost btn-small similar-action-btn" data-action="edit" data-id="${id2}" title="${this._t('similar.edit', 'Edit')}"><svg class="icon" aria-hidden="true"><use href="#i-grid"/></svg></button>
            <button class="btn btn-secondary btn-small similar-action-btn" data-action="build" data-id="${id2}" title="${this._t('similar.build', 'Build')}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg></button>
        `;

        second.append(secondImg, secondName, secondActions);
        wrapper.append(first, score, second);
        return wrapper;
    },

});
