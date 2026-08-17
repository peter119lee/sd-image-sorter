/**
 * Dataset Maker — gallery import: _importFromGallery, addImageIds, selection-token resolve, duplicate-filename warning, _clearAll, preview-updated flash.
 * Moved VERBATIM from dataset-maker.js L556-787.
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;
    // The members below are VERBATIM object-literal members lifted from
    // dataset-maker.js's original `const DM = {...}` literal. Object.assign
    // attaches them to the same window.DatasetMaker instance; call-time
    // `this` binding (DM.method() -> this === DM) is identical.
    Object.assign(DM, {

        // ---- Import from Gallery ----
        async _importFromGallery() {
            const ids = await this._resolveGallerySelectionIds();
            if (!ids || ids.length === 0) {
                // P0 fix from issue #5 follow-up noob review: silent failure
                // here is the #1 confusion point. Take the user there
                // explicitly + show a sticky toast that survives the nav.
                this._toast(this._t('dataset.gallerySelectionEmpty',
                    'Open the Gallery tab and select images first, then come back here and click this button again.'),
                    'warning', 8000);
                // Switch to the Gallery tab so the user sees the next step
                // without having to figure out where to click.
                const galleryTab = document.getElementById('nav-tab-gallery');
                if (galleryTab) {
                    setTimeout(() => galleryTab.click(), 600);
                }
                return;
            }
            await this.addImageIds(ids, { showToast: true });
        },

        /**
         * Public helper for external modules (gallery selection toolbar,
         * Tag modal, color analysis, future bridges) to push image IDs
         * into the Dataset Maker queue. Handles dedup, lazy meta fetch,
         * caption fetch, queue render, optional toast.
         *
         * Returns the number of NEW images actually added (after dedup).
         *
         * @param {Array<number|string>} ids
         * @param {{showToast?: boolean, switchView?: boolean}} options
         */
        async addImageIds(ids, options = {}) {
            const showToast = options.showToast !== false;
            const switchView = options.switchView !== false;

            const before = this.imageIds.length;
            const seen = new Set(this.imageIds);
            const newOnes = [];
            for (const id of (ids || [])) {
                const n = Number(id);
                if (!Number.isFinite(n) || n <= 0) continue;
                if (!seen.has(n)) {
                    this.imageIds.push(n);
                    seen.add(n);
                    newOnes.push(n);
                }
            }

            // Pull width/height from the gallery's already-loaded records
            // when possible to avoid per-image API calls. Same shape as
            // _importFromGallery's pre-flight enrichment.
            try {
                const galleryRecords = (window.AppState && window.AppState.images) || [];
                const byId = new Map();
                for (const rec of galleryRecords) {
                    if (rec && rec.id != null) byId.set(Number(rec.id), rec);
                }
                for (const id of newOnes) {
                    const rec = byId.get(Number(id));
                    if (!rec) continue;
                    const existing = this.meta.get(Number(id)) || {};
                    this.meta.set(Number(id), {
                        ...existing,
                        source: existing.source || 'gallery',
                        source_kind: existing.source_kind || 'gallery',
                        sidecar_capability: existing.sidecar_capability || 'beside_image',
                        filename: existing.filename || rec.filename || '',
                        thumbnail_path: existing.thumbnail_path || rec.thumbnail_path || '',
                        width: Number(rec.width || 0),
                        height: Number(rec.height || 0),
                        file_size: Number(rec.file_size || 0),
                    });
                }
            } catch { /* gallery state shape might shift; non-fatal */ }

            await this._fetchMissingMeta();
            await this._fetchMissingCaptions();
            for (const id of newOnes) {
                const existing = this.meta.get(Number(id)) || {};
                this.meta.set(Number(id), {
                    ...existing,
                    source: existing.source || 'gallery',
                    source_kind: existing.source_kind || 'gallery',
                    sidecar_capability: existing.sidecar_capability || 'beside_image',
                });
            }
            this._renderQueue();
            this._updateCount();
            this._updateExportEnabled();
            this._syncSourceCapabilityStatus?.();
            if (this.activeId == null && this.imageIds.length) {
                this._setActive(this.imageIds[0]);
            }

            const added = this.imageIds.length - before;

            if (switchView && added > 0 && typeof window.switchView === 'function') {
                try { window.switchView('dataset'); } catch (_e) { /* ignore */ }
            }

            // Ensure we stay on the import tab so the user sees the result
            if (added > 0) {
                this._setPipelineTab('import');
                this._renderImportGallery();
            }

            if (showToast) {
                if (added > 0) {
                    this._toast(this._t('dataset.gallerySelectionAdded',
                        'Added {count} images from Gallery selection',
                        { count: added }), 'success');
                } else {
                    this._toast(this._t('dataset.gallerySelectionAlreadyAdded',
                        'Those images are already in the Dataset Maker queue.'),
                        'info');
                }
            }
            this._checkDuplicateFilenames();
            this._saveSession();
            return added;
        },

        _checkDuplicateFilenames() {
            const stems = new Map();
            for (const id of this.imageIds) {
                const meta = this.meta.get(id) || {};
                const fn = (meta.filename || '').replace(/\.[^.]+$/, '').toLowerCase();
                if (!fn) continue;
                if (!stems.has(fn)) stems.set(fn, 0);
                stems.set(fn, stems.get(fn) + 1);
            }
            let dupCount = 0;
            for (const count of stems.values()) {
                if (count > 1) dupCount += count;
            }
            if (dupCount > 0) {
                this._toast(this._t('dataset.duplicateWarning',
                    'Found {count} images with similar filenames. You may want to review for duplicates.',
                    { count: dupCount }), 'warning', 6000);
            }
        },

        _getGallerySelectedIds() {
            const selectedIds = window.AppFilterAccess?.getSelectedImageIds?.();
            return Array.isArray(selectedIds)
                ? selectedIds
                    .map((id) => Number(id))
                    .filter((id) => Number.isFinite(id) && id > 0)
                : [];
        },

        async _resolveGallerySelectionIds() {
            const explicit = this._getGallerySelectedIds();
            if (explicit.length > 0) return explicit;

            const app = window.App || {};
            const token = window.AppFilterAccess?.getActiveSelectionToken?.() || null;
            const api = app.API;
            if (!token || !api || typeof api.getSelectionChunk !== 'function') {
                return [];
            }

            const out = [];
            let offset = 0;
            let hasMore = true;
            const chunkSize = 10000;
            while (hasMore) {
                const chunk = await api.getSelectionChunk(token, {
                    offset,
                    limit: chunkSize,
                });
                const ids = Array.isArray(chunk?.image_ids) ? chunk.image_ids : [];
                out.push(...ids.map(Number).filter((id) => Number.isFinite(id) && id > 0));
                hasMore = Boolean(chunk?.has_more);
                offset = Number(chunk?.next_offset || 0);
                if (!offset && hasMore) break;
            }
            return out;
        },

        _clearAll() {
            const count = this.imageIds.length;
            const title = this._t('dataset.confirmClearTitle', 'Clear Dataset Maker');
            const msg = this._t('dataset.confirmClear',
                'Remove all {count} images from the Dataset Maker queue? Original files and Gallery records will not be deleted.',
                { count });
            const doClear = () => {
                const managedTrigger = String(this._quickfilledTrigger || '').trim();
                try {
                    if (managedTrigger) {
                        this._saveManagedTriggerForLocalIds?.(
                            this.imageIds,
                            managedTrigger,
                            null,
                        );
                    }
                } catch (error) {
                    const reason = error instanceof Error ? error.message : String(error);
                    this._toast(this._t(
                        'dataset.clearPersistenceFailed',
                        'Clear was cancelled because local caption ownership could not be saved: {reason} Free browser storage and try again.',
                        { reason },
                    ), 'error', 8000);
                    return;
                }
                this._discardPendingDatasetEdits?.();
                this.imageIds = [];
                this.captions.clear();
                this.captionEdits.clear();
                this._undoStacks.clear();
                this._queueSelection.clear();
                // meta + NL/type maps too — clearing used to leak them, and
                // deterministic local ids resurface leaked entries on
                // re-import (2026-07 pin-sweep finding #2).
                this.meta?.clear?.();
                this.nlCaptions?.clear?.();
                this.nlEdits?.clear?.();
                this.captionType?.clear?.();
                this.activeId = null;
                this._clearLocalDatasetState?.();
                this._removeDatasetSession?.(this._activeProject || null);
                this._saveSession();
                this._renderQueue();
                this._renderImportGallery?.();
                this._renderEmptyEditor();
                this._updateCount();
                this._updateExportEnabled();
                this._syncSourceCapabilityStatus?.();
                // Notify derived caches (confidence pills, future
                // vocab/tag-zh caches) that the dataset membership
                // changed so they don't serve stale entries.
                window.dispatchEvent(new CustomEvent('dataset:changed'));
            };
            if (count === 0) {
                doClear();
                return;
            }
            if (window.App?.showConfirm) {
                window.App.showConfirm(title, msg, doClear);
                return;
            }
            if (window.confirm(msg)) doClear();
        },

        // v3.4.4 fix #4: tiny "✓ preview updated" cue so users see the
        // debounced export-preview re-render fire after editing a
        // prefix/template/blacklist field. No-op if the cue element
        // isn't present.
        _flashPreviewUpdated() {
            const list = document.getElementById('dataset-export-preview-list');
            if (!list) return;
            list.classList.remove('dm-preview-just-updated');
            // reflow to restart the CSS animation
            void list.offsetWidth;
            list.classList.add('dm-preview-just-updated');
        },
    });
})();
