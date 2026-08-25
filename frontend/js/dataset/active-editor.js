/**
 * Dataset Maker — active-image editor: _setActive dispatch + gallery branch, full-res bar, remove/revert, empty editor, zoom, caption diff, workbench keyboard.
 * Moved VERBATIM from dataset-maker-part2.js L1-27, L115-317,
 * L1194-1216, L1438-1457, L1538-1598. (The _activeChangedHooks registry
 * initializer that lived at part2 L114 now lives in dataset/core.js —
 * lead-approved deviation.)
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
/**
 * Dataset Maker - Part 2 (active image, caption rendering, export flow,
 * modals). Loaded by dataset-maker.js as a sibling so each file stays
 * within easy reading length.
 *
 * Adds methods to ``window.DatasetMaker``.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    // ---------- Active image + caption editor ----------
    DM._zoomLevel = 1;
    DM._queueViewMode = (() => {
        try { return localStorage.getItem('sd-image-sorter-dataset-queue-mode') || 'grid'; }
        catch { return 'grid'; }
    })();

    DM._thumbSrc = function (id, size = 128) {
        const numericId = Number(id);
        const meta = this.meta.get(numericId) || {};
        if (this.isLocalId && this.isLocalId(numericId)) {
            return meta.thumb_b64 ? `data:image/jpeg;base64,${meta.thumb_b64}` : '';
        }
        return `/api/image-thumbnail/${numericId}?size=${size}`;
    };

    DM._setActive = function (imageId) {
        const id = Number(imageId);
        if (this.isLocalId?.(id) && typeof this._setActiveLocal === 'function') {
            // Local-source items (negative ids) render via the branch in
            // dataset-maker-local-import.js. NOTE: the local branch never
            // flushed pending caption edits, refreshed the split view, or
            // updated the caption diff — gallery-only hooks below guard on
            // isLocalId themselves to preserve that.
            this._setActiveLocal(id);
        } else {
            this._setActiveGallery(id);
        }
        for (const hook of this._activeChangedHooks) hook.call(this, id);
    };

    DM._setActiveGallery = function (id) {
        if (Number(this.activeId) !== id) this._flushPendingCaptionEdit?.();
        if (!this.imageIds.includes(id)) return;
        this.activeId = id;
        const meta = this.meta.get(id) || {};
        const filename = meta.filename || `#${id}`;

        const img = document.getElementById('dataset-editor-image');
        const empty = document.getElementById('dataset-editor-empty');
        const ta = document.getElementById('dataset-editor-textarea');
        const actions = document.getElementById('dataset-editor-actions');
        const filenameEl = document.getElementById('dataset-editor-filename');
        const zoomBar = document.getElementById('dataset-zoom-toolbar');

        if (img) {
            img.src = this._fullResMode ? `/api/image-file/${id}` : this._thumbSrc(id, 1024);
            img.alt = filename;
            img.hidden = false;
            let usedFallback = false;
            img.onerror = () => {
                if (!usedFallback) {
                    usedFallback = true;
                    img.src = this._thumbSrc(id, 1024);
                    return;
                }
                img.removeAttribute('src');
                img.hidden = true;
                if (empty) empty.hidden = false;
            };
        }
        if (empty) empty.hidden = true;
        if (filenameEl) filenameEl.textContent = filename;
        if (zoomBar) zoomBar.hidden = false;

        // Reset zoom on image change
        this._zoomLevel = 1;
        this._applyZoom();

        const caption = this.captionEdits.has(id)
            ? this.captionEdits.get(id)
            : (this.captions.get(id) || '');
        if (ta) {
            ta.value = caption;
            ta.hidden = false;
        }
        if (actions) actions.hidden = false;

        this._highlightActiveQueueItem();
        this._scrollActiveQueueItemIntoView?.();
        this._renderTagPills?.();
        this._ensureFullResButtons();
    };

    DM._fullResMode = false;

    DM._ensureFullResButtons = function () {
        const wrap = document.getElementById('dataset-editor-image-wrap');
        if (!wrap || wrap.querySelector('.dataset-fullres-bar')) return;
        const bar = document.createElement('div');
        bar.className = 'dataset-fullres-bar';
        const btnOne = document.createElement('button');
        btnOne.type = 'button';
        btnOne.className = 'btn btn-ghost btn-small dataset-fullres-btn';
        btnOne.textContent = '\uD83D\uDD0D';
        btnOne.title = this._t('dataset.fullResCurrent', 'Load full resolution (current)');
        btnOne.addEventListener('click', () => this._loadFullResCurrent());
        const btnAll = document.createElement('button');
        btnAll.type = 'button';
        btnAll.className = 'btn btn-ghost btn-small dataset-fullres-btn';
        btnAll.textContent = '\uD83D\uDD0D\u2726';
        btnAll.title = this._t('dataset.fullResAll', 'Full resolution mode (all)');
        btnAll.addEventListener('click', () => this._toggleFullResAll());
        bar.appendChild(btnOne);
        bar.appendChild(btnAll);
        wrap.appendChild(bar);
    };

    DM._loadFullResCurrent = function () {
        const img = document.getElementById('dataset-editor-image');
        if (!img || this.activeId == null) return;
        const id = Number(this.activeId);
        img.src = this.isLocalId?.(id) ? this._thumbSrc(id, 2048) : `/api/image-file/${id}`;
    };

    DM._toggleFullResAll = function () {
        this._fullResMode = !this._fullResMode;
        const btn = document.querySelector('.dataset-fullres-bar .dataset-fullres-btn:last-child');
        if (btn) btn.classList.toggle('active', this._fullResMode);
        if (this.activeId != null) {
            const img = document.getElementById('dataset-editor-image');
            if (img) {
                img.src = this._fullResMode
                    ? `/api/image-file/${this.activeId}`
                    : this._thumbSrc(this.activeId, 1024);
            }
        }
        this._toast(
            this._fullResMode
                ? this._t('dataset.fullResOnToast', 'Full resolution mode ON')
                : this._t('dataset.fullResOffToast', 'Full resolution mode OFF (thumbnails)'),
            'info', 2000
        );
    };

    DM._stepActive = function (delta) {
        if (this.activeId == null || this.imageIds.length === 0) return;
        const idx = this.imageIds.indexOf(Number(this.activeId));
        if (idx < 0) return;
        const next = (idx + delta + this.imageIds.length) % this.imageIds.length;
        this._setActive(this.imageIds[next]);
    };

    DM._removeActive = function () {
        if (this.activeId == null) return;
        this._removeImageById(Number(this.activeId), { confirm: true });
        this._saveSession();
    };

    DM._reviewUndoStack = [];

    DM._dropImageForReview = function (imageId) {
        const id = Number(imageId);
        const idx = this.imageIds.indexOf(id);
        if (idx < 0) return;
        const snapshot = {
            id,
            index: idx,
            caption: this.captions.get(id),
            captionEdit: this.captionEdits.get(id),
            nlCaption: this.nlCaptions?.get?.(id),
            nlEdit: this.nlEdits?.get?.(id),
            captionType: this.captionType?.get?.(id),
            localPath: this.localItemPaths?.get?.(id),
            localDsId: this.localItemDsIds?.get?.(id),
        };
        this._removeImageById(id, { confirm: false });
        this._reviewUndoStack.push(snapshot);
        this._saveSession();
        this._toast?.(
            this._t('dataset.droppedForReview', 'Dropped from dataset (Z to undo)'),
            'info',
            1800
        );
    };

    DM._undoReviewDrop = function () {
        const snapshot = this._reviewUndoStack.pop();
        if (!snapshot) {
            this._toast?.(
                this._t('dataset.nothingToUndoDrop', 'Nothing to undo'),
                'info',
                1400
            );
            return;
        }
        const insertAt = Math.min(snapshot.index, this.imageIds.length);
        this.imageIds.splice(insertAt, 0, snapshot.id);
        if (snapshot.caption != null) this.captions.set(snapshot.id, snapshot.caption);
        if (snapshot.captionEdit != null) this.captionEdits.set(snapshot.id, snapshot.captionEdit);
        if (snapshot.nlCaption != null) this.nlCaptions?.set?.(snapshot.id, snapshot.nlCaption);
        if (snapshot.nlEdit != null) this.nlEdits?.set?.(snapshot.id, snapshot.nlEdit);
        if (snapshot.captionType != null) this.captionType?.set?.(snapshot.id, snapshot.captionType);
        if (snapshot.localPath != null) this.localItemPaths?.set?.(snapshot.id, snapshot.localPath);
        if (snapshot.localDsId != null) this.localItemDsIds?.set?.(snapshot.id, snapshot.localDsId);
        this._renderQueue();
        this._updateCount();
        this._updateExportEnabled();
        this._setActive(snapshot.id);
        this._saveSession();
        this._toast?.(
            this._t('dataset.undidReviewDrop', 'Restored last dropped image'),
            'info',
            1800
        );
    };

    DM._removeImageById = function (imageId, options = {}) {
        const id = Number(imageId);
        if (!this.imageIds.includes(id)) return;
        if (options.confirm) {
            const msg = this._t('dataset.confirmRemove', 'Remove this image from the dataset?');
            if (!window.confirm(msg)) return;
        }
        this._supersedeCaptionFetch?.();
        const idx = this.imageIds.indexOf(id);
        this.imageIds.splice(idx, 1);
        this.captions.delete(id);
        if (typeof this._deleteCaptionEditForDatasetRemoval === 'function') {
            this._deleteCaptionEditForDatasetRemoval(id);
        } else {
            this.captionEdits.delete(id);
        }
        this._undoStacks?.delete?.(id);
        this._queueSelection.delete(id);
        // NL/type maps too — local ids are deterministic (sha1 of path), so a
        // leaked entry resurfaces on re-import (2026-07 pin-sweep finding #2).
        this.nlCaptions?.delete?.(id);
        this.nlEdits?.delete?.(id);
        this.captionType?.delete?.(id);
        if (this.localItemPaths && this.isLocalId && this.isLocalId(id)) {
            this.localItemPaths.delete(id);
            this.localItemDsIds?.delete?.(id);
        }
        const wasActive = Number(this.activeId) === id;
        if (wasActive) this.activeId = null;
        this._renderQueue();
        this._renderImportGallery();
        this._updateCount();
        this._updateExportEnabled();
        this._updateMultiSelectUI();
        if (this.imageIds.length === 0) {
            this._renderEmptyEditor();
        } else if (wasActive) {
            this._setActive(this.imageIds[Math.min(idx, this.imageIds.length - 1)]);
        }
        this._syncSourceCapabilityStatus?.();
        this._syncOutputModeUi?.();
        this._saveSession?.();
        // Notify derived caches (confidence pills, vocab cache) that an
        // image left the dataset so they don't serve stale entries for
        // an id that may be re-added later with different tags.
        window.dispatchEvent(new CustomEvent('dataset:changed', { detail: { removed: id } }));
    };

    DM._revertActiveCaption = function () {
        if (this.activeId == null) return;
        const id = Number(this.activeId);
        this.captionEdits.delete(id);
        const ta = document.getElementById('dataset-editor-textarea');
        if (ta) ta.value = this.captions.get(id) || '';
        this._refreshQueueItem(id);
        this._saveSession?.();
    };

    DM._renderEmptyEditor = function () {
        const img = document.getElementById('dataset-editor-image');
        const empty = document.getElementById('dataset-editor-empty');
        const ta = document.getElementById('dataset-editor-textarea');
        const actions = document.getElementById('dataset-editor-actions');
        const filenameEl = document.getElementById('dataset-editor-filename');
        if (img) img.hidden = true;
        if (empty) empty.hidden = false;
        const emptyText = empty?.querySelector?.('.dataset-editor-empty-text');
        if (emptyText) {
            emptyText.textContent = this._t('dataset.editorEmpty',
                'Pick an image from the queue on the left to edit its caption.');
        }
        if (ta) ta.hidden = true;
        if (actions) actions.hidden = true;
        if (filenameEl) filenameEl.textContent = '';
    };

    // ---------- Zoom controls ----------
    DM._applyZoom = function () {
        const img = document.getElementById('dataset-editor-image');
        const label = document.getElementById('dataset-zoom-label');
        if (img) img.style.transform = `scale(${this._zoomLevel})`;
        if (label) label.textContent = Math.round(this._zoomLevel * 100) + '%';
    };

    DM._zoomIn = function () {
        this._zoomLevel = Math.min(this._zoomLevel + 0.25, 5);
        this._applyZoom();
    };

    DM._zoomOut = function () {
        this._zoomLevel = Math.max(this._zoomLevel - 0.25, 0.25);
        this._applyZoom();
    };

    DM._zoomReset = function () {
        this._zoomLevel = 1;
        this._applyZoom();
    };

    // ---------- Zoom event bindings ----------
    (function initZoomBindings() {
        document.getElementById('btn-dataset-zoom-in')
            ?.addEventListener('click', () => DM._zoomIn());
        document.getElementById('btn-dataset-zoom-out')
            ?.addEventListener('click', () => DM._zoomOut());
        document.getElementById('btn-dataset-zoom-reset')
            ?.addEventListener('click', () => DM._zoomReset());

        const wrap = document.getElementById('dataset-editor-image-wrap');
        if (wrap) {
            wrap.addEventListener('wheel', (e) => {
                if (!e.ctrlKey) return;
                e.preventDefault();
                if (e.deltaY < 0) DM._zoomIn();
                else DM._zoomOut();
            }, { passive: false });
        }
    })();

    // ---------- Caption diff indicator ----------
    DM._updateCaptionDiff = function (id) {
        const el = document.getElementById('dataset-caption-diff');
        if (!el) return;
        if (!this.captionEdits.has(id)) {
            el.hidden = true;
            return;
        }
        const original = (this.captions.get(id) || '').split(', ').filter(Boolean);
        const edited = (this.captionEdits.get(id) || '').split(', ').filter(Boolean);
        const origSet = new Set(original);
        const editSet = new Set(edited);
        const added = edited.filter(t => !origSet.has(t)).length;
        const removed = original.filter(t => !editSet.has(t)).length;
        if (added === 0 && removed === 0) {
            el.hidden = true;
            return;
        }
        const parts = [];
        if (added > 0) parts.push(`<span class="dataset-diff-added">+${added} tag${added > 1 ? 's' : ''}</span>`);
        if (removed > 0) parts.push(`<span class="dataset-diff-removed">-${removed} tag${removed > 1 ? 's' : ''}</span>`);
        el.innerHTML = parts.join(', ');
        el.hidden = false;
    };

    // Update the caption diff on image change (former _setActive wrapper).
    // Gallery-only: the local-import branch never updated the diff.
    DM._activeChangedHooks.push(function (id) {
        if (this.isLocalId?.(id)) return;
        if (this.activeId != null) this._updateCaptionDiff(Number(this.activeId));
    });

    // ---------- Keyboard shortcuts for workbench ----------
    document.addEventListener('keydown', function (e) {
        const view = document.getElementById('view-dataset');
        if (!view || view.hidden) return;
        const maker = view.querySelector('.dataset-maker');
        if (!maker || maker.dataset.activeTab !== 'workbench') return;
        const tag = document.activeElement?.tagName?.toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

        switch (e.key) {
            case 'a':
            case 'A':
            case 'ArrowLeft':
                e.preventDefault();
                DM._stepActive(-1);
                break;
            case 'd':
            case 'D':
            case 'ArrowRight':
                e.preventDefault();
                DM._stepActive(1);
                break;
            case 'Delete':
                e.preventDefault();
                DM._removeActive();
                break;
            case 'x':
            case 'X':
                e.preventDefault();
                if (DM.activeId != null) DM._dropImageForReview(DM.activeId);
                break;
            case 'z':
            case 'Z':
                if (e.ctrlKey || e.metaKey) break;
                e.preventDefault();
                DM._undoReviewDrop();
                break;
        }
    });
})();
