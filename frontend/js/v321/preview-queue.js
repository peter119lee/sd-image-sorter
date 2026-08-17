/**
 * v321/preview-queue.js - v321-ui.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/v321-ui.js pre-cut lines 2134-2441
 * (of 3,164): (C) virtualized preview queue render + metadata fetch + coloring.
 * Classic script: joins the ONE unsealed window.V321Integration object
 * declared in v321/base.js (loads FIRST); v321/boot.js registers the
 * DOMContentLoaded init LAST; index.html lists the family in original
 * line order.
 */
Object.assign(window.V321Integration, {

    _buildPreviewQueue() {
        const queue = document.createElement('div');
        queue.className = 'export-preview-queue';

        const head = document.createElement('div');
        head.className = 'export-preview-panel-head';
        const total = this.queueTotalCount || this.queueImageIds.length || this.previewResults.length;
        head.innerHTML = `<strong>${this._i18n('batchExport.previewQueue', 'Images')}</strong><span class="export-queue-count${total > 1000 ? ' export-queue-count--warn' : ''}">${total} images</span>`;
        queue.appendChild(head);

        const body = document.createElement('div');
        body.className = 'export-preview-queue-list';

        const ids = this.queueImageIds.length ? this.queueImageIds : this.previewResults.map(r => Number(r.image_id));
        const totalCount = this.queueTotalCount || ids.length;
        const ITEM_HEIGHT = 60;
        const MAX_SCROLL_SPACER_PX = 4_000_000;
        const MAX_RENDER_VIEWPORT_PX = 1200;

        const getMetrics = () => {
            const viewport = Math.max(1, Math.min(body.clientHeight || 400, MAX_RENDER_VIEWPORT_PX));
            const totalHeight = Math.max(0, totalCount * ITEM_HEIGHT);
            const spacerHeight = Math.min(totalHeight, MAX_SCROLL_SPACER_PX);
            if (totalHeight <= spacerHeight) {
                const virtualTop = Math.max(0, body.scrollTop || 0);
                return {
                    viewport,
                    spacerHeight,
                    virtualTop,
                    domTopForIndex: (index) => index * ITEM_HEIGHT,
                };
            }
            const domScrollable = Math.max(1, spacerHeight - viewport);
            const virtualScrollable = Math.max(1, totalHeight - viewport);
            const ratio = Math.max(0, Math.min(1, (body.scrollTop || 0) / domScrollable));
            const virtualTop = ratio * virtualScrollable;
            return {
                viewport,
                spacerHeight,
                virtualTop,
                domTopForIndex: (index) => (body.scrollTop || 0) + ((index * ITEM_HEIGHT) - virtualTop),
            };
        };

        // Virtual scroll: only render visible items
        const spacer = document.createElement('div');
        spacer.style.height = `${Math.min(Math.max(0, totalCount * ITEM_HEIGHT), MAX_SCROLL_SPACER_PX)}px`;
        spacer.style.position = 'relative';

        const renderVisible = () => {
            const metrics = getMetrics();
            spacer.style.height = `${metrics.spacerHeight}px`;
            const startIdx = Math.max(0, Math.floor(metrics.virtualTop / ITEM_HEIGHT));
            const endIdx = Math.min(startIdx + Math.ceil(metrics.viewport / ITEM_HEIGHT) + 2, totalCount);

            spacer.innerHTML = '';
            const visibleIds = [];
            for (let i = startIdx; i < endIdx; i++) {
                const imageId = this.queueSelectionToken ? this.queueIdByIndex.get(i) : ids[i];
                const btn = imageId
                    ? this._buildQueueItem(imageId, i)
                    : this._buildQueuePlaceholder(i);
                btn.style.position = 'absolute';
                btn.style.top = `${metrics.domTopForIndex(i)}px`;
                btn.style.left = '0';
                btn.style.right = '0';
                btn.style.height = `${ITEM_HEIGHT}px`;
                spacer.appendChild(btn);
                if (imageId) visibleIds.push(imageId);
            }

            // Prefetch metadata for visible items that are missing. Do not
            // re-render after a no-op fetch; otherwise large virtual queues can
            // spin in a microtask render loop.
            const missingVisibleIds = visibleIds.filter(id => !this.previewMetadata.has(Number(id)));
            if (missingVisibleIds.length) {
                this._fetchQueueMetadata(missingVisibleIds).then((changed) => {
                    if (changed) renderVisible();
                });
            }
            if (this.queueSelectionToken) {
                let needsIds = false;
                for (let i = startIdx; i < endIdx; i += 1) {
                    if (!this.queueIdByIndex.has(i)) {
                        needsIds = true;
                        break;
                    }
                }
                if (!needsIds) return;
                this._fetchQueueIdsWindow(startIdx, endIdx - startIdx).then((loaded) => {
                    if (loaded.length) renderVisible();
                });
            }
        };

        body.addEventListener('scroll', renderVisible);
        body.appendChild(spacer);
        queue.appendChild(body);

        requestAnimationFrame(renderVisible);
        this._queueScrollContainer = body;
        this._queueRenderVisible = renderVisible;
        return queue;
    },

    _buildQueuePlaceholder(index) {
        const item = document.createElement('div');
        item.className = 'export-preview-queue-item is-loading';
        item.innerHTML = `<span class="export-preview-queue-copy"><span>#${index + 1}</span><strong>Loading...</strong><small></small></span>`;
        return item;
    },

    _buildQueueItem(imageId, index) {
        const id = Number(imageId);
        const meta = this.previewMetadata.get(id);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'export-preview-queue-item';
        if (id === Number(this.activePreviewImageId)) btn.classList.add('active');
        if (this.editedCaptions.has(id) || this.editedNl.has(id)) btn.classList.add('edited');
        btn.dataset.imageId = String(id);

        if (meta) {
            const img = this._createPreviewThumb({ image_id: id, filename: meta.filename }, 96);
            const copy = document.createElement('span');
            copy.className = 'export-preview-queue-copy';
            copy.innerHTML = `<span></span><strong></strong><small></small>`;
            copy.querySelector('span').textContent = `#${id}`;
            copy.querySelector('strong').textContent = meta.filename || '';
            copy.querySelector('small').textContent = (this.editedCaptions.has(id) || this.editedNl.has(id))
                ? this._i18n('batchExport.previewEdited', 'Edited')
                : this._i18n('batchExport.previewGenerated', 'Generated');
            btn.append(img, copy);
        } else {
            const placeholder = document.createElement('span');
            placeholder.className = 'export-preview-queue-copy';
            placeholder.innerHTML = `<span>#${id}</span><strong>Loading…</strong><small></small>`;
            btn.appendChild(placeholder);
        }

        // Aurora Phase 3 (#25c): caption-type chip + missing-trigger flag
        // (both no-ops for unloaded captions / default states).
        this._decorateQueueItemType(btn, id);
        this._decorateQueueItemTrigger(btn, id);

        btn.addEventListener('click', () => {
            this.activePreviewImageId = id;
            this.activePreviewIndex = Number(index || 0);
            this._onQueueItemClick(id);
        });
        return btn;
    },

    /** Append the caption-type chip (B+N / NL) when this image will export the
     *  NL sentence — same chip language as the Dataset Maker queue. */
    _decorateQueueItemType(btn, id) {
        // Chip = export effect, not just the stored setting: in content modes
        // where the compose is gated off, showing B+N/NL would over-promise.
        if (!this._composeEligible()) return;
        const ctype = this._getCaptionType(id);
        if (ctype !== 'nl' && ctype !== 'both') return;
        const chip = document.createElement('span');
        chip.className = `export-preview-queue-captype export-preview-queue-captype-${ctype}`;
        chip.textContent = ctype === 'both'
            ? this._i18n('dataset.captionTypeChipBoth', 'B+N')
            : this._i18n('dataset.captionTypeChipNl', 'NL');
        chip.title = ctype === 'both'
            ? this._i18n('dataset.captionTypeBothTip', 'Exports tags, then the sentence')
            : this._i18n('dataset.captionTypeNlTip', 'Exports the sentence only');
        btn.appendChild(chip);
    },

    /** Append a small ⚑ badge when this queue item's loaded caption is missing
     *  the Dataset Maker trigger word. Lazy/unloaded captions get no badge. */
    _decorateQueueItemTrigger(btn, id) {
        const triggerRaw = (document.getElementById('dataset-trigger')?.value || '').trim();
        if (!triggerRaw) return;
        if (!this.editedCaptions.has(id) && !this.previewCache.has(id)) return;
        const tokens = this._splitCaptionTokens(this._getExportedCaption(id));
        if (!tokens.length) return;
        const triggerKey = this._normalizeCaptionToken(triggerRaw);
        if (tokens.some((t) => this._normalizeCaptionToken(t) === triggerKey)) return;
        const badge = document.createElement('span');
        badge.className = 'export-preview-queue-trigger-warn';
        badge.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-flag\"/></svg>";
        badge.title = this._i18n('batchExport.previewMissingTriggerHint', 'Missing trigger word');
        badge.setAttribute('aria-label', badge.title);
        btn.appendChild(badge);
    },

    async _onQueueItemClick(imageId) {
        const id = Number(imageId);
        if (this.queueIndexById.has(id)) {
            this.activePreviewIndex = this.queueIndexById.get(id);
        }
        // Ensure metadata is available
        if (!this.previewMetadata.has(id)) {
            await this._fetchQueueMetadata([id]);
        }
        // Fetch caption if not cached
        const contentMode = document.getElementById('batch-export-content-mode')?.value;
        const opts = this._previewOptionsForContentMode(contentMode);
        await this._fetchCaptionForImage(id, opts);
        this._renderPreviewWorkbench();
    },

    async _fetchCaptionForImage(imageId, opts) {
        const id = Number(imageId);
        if (this.previewCache.has(id)) return this.previewCache.get(id);
        if (!opts) {
            const contentMode = document.getElementById('batch-export-content-mode')?.value;
            opts = this._previewOptionsForContentMode(contentMode);
        }
        try {
            const r = await fetch('/api/tags/export-preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_ids: [id], ...opts }),
            });
            if (!r.ok) return '';
            const data = await r.json();
            for (const item of (data.results || [])) {
                this.previewCache.set(item.image_id, item.rendered || '');
                if (item.filename && !this.previewMetadata.has(item.image_id)) {
                    this.previewMetadata.set(item.image_id, { filename: item.filename, thumbnail_path: item.thumbnail_path || '' });
                }
                this._seedNlFromPreviewItem(item);
            }
            return this.previewCache.get(id) || '';
        } catch (e) {
            console.warn('_fetchCaptionForImage failed', e);
            return '';
        }
    },

    async _fetchQueueMetadata(imageIds) {
        if (!imageIds.length) return false;
        // Filter out already-cached IDs
        const needed = imageIds
            .map(id => Number(id))
            .filter(id => Number.isFinite(id) && id > 0)
            .filter(id => !this.previewMetadata.has(id) && !this._queueMetadataInFlight.has(id));
        if (!needed.length) return false;
        needed.forEach(id => this._queueMetadataInFlight.add(id));
        let changed = false;
        // Use export-preview to get metadata (filename) — it's the only endpoint
        // guaranteed to return filename without triggering individual detail requests
        const contentMode = document.getElementById('batch-export-content-mode')?.value;
        const opts = this._previewOptionsForContentMode(contentMode);
        try {
            const batch = needed.slice(0, 50);
            const r = await fetch('/api/tags/export-preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_ids: batch, ...opts }),
            });
            if (r.ok) {
                const data = await r.json();
                for (const item of (data.results || [])) {
                    const itemId = Number(item.image_id);
                    if (!Number.isFinite(itemId) || itemId <= 0) continue;
                    if (!this.previewMetadata.has(itemId)) changed = true;
                    this.previewMetadata.set(itemId, { filename: item.filename || '', thumbnail_path: item.thumbnail_path || '' });
                    if (item.rendered && !this.previewCache.has(itemId)) {
                        this.previewCache.set(itemId, item.rendered);
                    }
                    this._seedNlFromPreviewItem(item);
                }
            }
        } catch (e) {
            // Graceful fallback
        } finally {
            needed.forEach(id => this._queueMetadataInFlight.delete(id));
        }
        // Set placeholder for any still-missing
        for (const id of needed) {
            if (!this.previewMetadata.has(id)) {
                this.previewMetadata.set(id, { filename: `Image ${id}`, thumbnail_path: '' });
                changed = true;
            }
        }
        return changed;
    },

    // Danbooru category coloring for caption-editor chips. Delegates to the
    // Dataset Maker's category cache (backend /api/prompts/categorize) so the
    // editor and the dataset pills always agree; degrades to uncolored chips
    // when that module is unavailable.
    _applyTokenCategoryClass(chip, token) {
        const dm = window.DatasetMaker;
        if (!dm || typeof dm._classifyTagCategory !== 'function') return;
        const category = String(dm._classifyTagCategory(token) || 'unknown');
        chip.classList.add(`dataset-tag-pill-category-${category}`);
    },

    _recolorTokensWhenCategorized(tokens) {
        const dm = window.DatasetMaker;
        if (!dm || typeof dm._ensureTagCategories !== 'function') return;
        if (!Array.isArray(tokens) || !tokens.length) return;
        Promise.resolve(dm._ensureTagCategories(tokens))
            .then((gained) => {
                // Re-render once the backend categories land; _ensureTagCategories
                // returns false on cache hits, so this cannot loop.
                if (gained) this._renderPreviewWorkbench();
            })
            .catch(() => {});
    },
});
