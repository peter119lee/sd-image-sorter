/**
 * Dataset Maker — queue + import-gallery rendering: virtual renderers, _buildQueueItem, queue view mode, caption filter, _updateCount.
 * Moved VERBATIM from dataset-maker-part2.js L28-104, L318-449,
 * L458-644, L1024-1193. (The _queueItemDecorators registry initializer
 * that lived at part2 L457 now lives in dataset/core.js — lead-approved
 * deviation.)
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    const DATASET_VIRTUAL_THRESHOLD = 800;
    const DATASET_VIRTUAL_BUFFER_ROWS = 3;
    const DATASET_QUEUE_GRID_MIN = 144;
    const DATASET_QUEUE_LIST_HEIGHT = 108;
    const DATASET_IMPORT_GRID_MIN = 150;
    const DATASET_QUEUE_GRID_GAP = 8;
    const DATASET_IMPORT_GRID_GAP = 10;
    const DATASET_MAX_SCROLL_SPACER_PX = 24_000_000;

    function cleanupVirtualRenderer(owner, key) {
        const cleanup = owner[key];
        if (typeof cleanup === 'function') cleanup();
        owner[key] = null;
    }

    function getVirtualMetrics(scroller, rowCount, itemHeight) {
        const viewport = Math.max(1, scroller.clientHeight || 1);
        const totalHeight = Math.max(0, rowCount * itemHeight);
        const spacerHeight = Math.min(totalHeight, DATASET_MAX_SCROLL_SPACER_PX);
        const compressed = totalHeight > spacerHeight;
        if (!compressed) {
            return {
                viewport,
                totalHeight,
                spacerHeight,
                compressed: false,
                virtualTop: Math.max(0, scroller.scrollTop || 0),
                domTopForRow: (row) => row * itemHeight,
            };
        }
        const domScrollable = Math.max(1, spacerHeight - viewport);
        const virtualScrollable = Math.max(1, totalHeight - viewport);
        const ratio = Math.max(0, Math.min(1, (scroller.scrollTop || 0) / domScrollable));
        const virtualTop = ratio * virtualScrollable;
        return {
            viewport,
            totalHeight,
            spacerHeight,
            compressed: true,
            virtualTop,
            domTopForRow: (row) => (scroller.scrollTop || 0) + ((row * itemHeight) - virtualTop),
        };
    }

    function scrollVirtualIndexIntoView(scroller, index, columns, itemHeight) {
        if (!scroller || index < 0 || columns <= 0 || itemHeight <= 0) return;
        const row = Math.floor(index / columns);
        const rowCount = Math.ceil(Math.max(0, index + 1) / columns);
        const knownRows = Number(scroller.dataset.virtualRows || rowCount);
        const totalRows = Math.max(rowCount, knownRows);
        const totalHeight = totalRows * itemHeight;
        const viewport = Math.max(1, scroller.clientHeight || 1);
        const spacerHeight = Math.min(totalHeight, DATASET_MAX_SCROLL_SPACER_PX);
        const targetVirtualTop = row * itemHeight;
        if (totalHeight <= spacerHeight) {
            scroller.scrollTop = targetVirtualTop;
            return;
        }
        const ratio = targetVirtualTop / Math.max(1, totalHeight - viewport);
        scroller.scrollTop = ratio * Math.max(1, spacerHeight - viewport);
    }

    function getMeasuredWidth(el, fallback = 1) {
        const rectWidth = Math.floor(el?.getBoundingClientRect?.().width || 0);
        const clientWidth = Math.floor(el?.clientWidth || 0);
        const parentWidth = Math.floor(el?.parentElement?.getBoundingClientRect?.().width || 0);
        return Math.max(1, rectWidth || clientWidth || parentWidth || fallback || 1);
    }

    function getGridLayout(el, minCellWidth, gapPx) {
        const width = getMeasuredWidth(el, minCellWidth);
        const gap = Math.max(0, Number(gapPx) || 0);
        const columns = Math.max(1, Math.floor((width + gap) / (Math.max(1, minCellWidth) + gap)));
        const cellWidth = Math.max(1, Math.floor((width - (gap * (columns - 1))) / columns));
        return { width, columns, cellWidth, rowStride: cellWidth + gap, gap };
    }

    // ---------- Queue rendering ----------
    DM._renderQueue = function () {
        const list = document.getElementById('dataset-queue-list');
        if (!list) return;
        this._updateCount?.();
        const mode = this._queueViewMode === 'list' ? 'list' : 'grid';
        const renderIds = this._queueIdsForCurrentFilter();
        list.classList.toggle('dataset-queue-grid-mode', mode === 'grid');
        list.classList.toggle('dataset-queue-list-mode', mode === 'list');
        if (renderIds.length > DATASET_VIRTUAL_THRESHOLD) {
            this._renderVirtualQueue(list, mode, renderIds);
            return;
        }
        cleanupVirtualRenderer(this, '_queueVirtualCleanup');
        list.classList.remove('is-virtualized');
        list.style.display = '';
        delete list.dataset.virtualRows;
        if (this.imageIds.length === 0) {
            list.innerHTML = `
                <div class="dataset-empty-state">
                    <span class="dataset-empty-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-package"/></svg></span>
                    <p class="dataset-empty-headline">${this._t('dataset.queueEmptyHeadline', 'No images yet')}</p>
                    <p class="dataset-empty-body">
                        <span class="dataset-empty-arrow">←</span>
                        ${this._t('dataset.queueEmptyBody',
                            'Open the <svg class="icon" aria-hidden="true"><use href="#i-image"/></svg> Gallery tab, click some images, then click "Add from Gallery" above.')}
                    </p>
                </div>
            `;
            this._updateMultiSelectUI();
            return;
        }
        list.innerHTML = '';
        if (renderIds.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'dataset-empty-state';
            empty.textContent = this._t('dataset.queueFilterEmpty', 'No images match this queue filter.');
            list.appendChild(empty);
            this._updateMultiSelectUI();
            return;
        }
        for (const [index, id] of renderIds.entries()) {
            list.appendChild(this._buildQueueItem(id, index));
        }
        this._highlightActiveQueueItem();
        this._applyAuditFilterToQueue?.();
        this._applyCaptionFilter?.();
        this._updateMultiSelectUI();
    };

    DM._scrollActiveQueueItemIntoView = function () {
        const list = document.getElementById('dataset-queue-list');
        if (!list || !list.classList.contains('is-virtualized') || this.activeId == null) return;
        const renderIds = this._queueIdsForCurrentFilter();
        const index = renderIds.indexOf(Number(this.activeId));
        if (index < 0) return;
        const mode = this._queueViewMode === 'list' ? 'list' : 'grid';
        const gridLayout = getGridLayout(list, DATASET_QUEUE_GRID_MIN, DATASET_QUEUE_GRID_GAP);
        const width = gridLayout.width;
        const columns = mode === 'list' ? 1 : gridLayout.columns;
        const cellWidth = mode === 'list' ? width : gridLayout.cellWidth;
        const itemHeight = mode === 'list' ? DATASET_QUEUE_LIST_HEIGHT : gridLayout.rowStride;
        list.dataset.virtualRows = String(Math.ceil(renderIds.length / columns));
        scrollVirtualIndexIntoView(list, index, columns, itemHeight);
    };

    DM._renderVirtualQueue = function (list, mode, renderIds = this._queueIdsForCurrentFilter()) {
        cleanupVirtualRenderer(this, '_queueVirtualCleanup');
        list.innerHTML = '';
        list.classList.add('is-virtualized');
        list.style.display = 'block';

        const spacer = document.createElement('div');
        spacer.className = 'dataset-virtual-spacer dataset-queue-virtual-spacer';
        list.appendChild(spacer);

        let frame = 0;
        const renderVisible = () => {
            if (frame) cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                frame = 0;
                const isList = mode === 'list';
                const gridLayout = getGridLayout(list, DATASET_QUEUE_GRID_MIN, DATASET_QUEUE_GRID_GAP);
                const width = gridLayout.width;
                const columns = isList ? 1 : gridLayout.columns;
                const cellWidth = isList ? width : gridLayout.cellWidth;
                const itemHeight = isList ? DATASET_QUEUE_LIST_HEIGHT : gridLayout.rowStride;
                const rowCount = Math.ceil(renderIds.length / columns);
                const metrics = getVirtualMetrics(list, rowCount, itemHeight);
                list.dataset.virtualRows = String(rowCount);
                spacer.style.height = `${metrics.spacerHeight}px`;
                spacer.style.position = 'relative';
                spacer.innerHTML = '';

                const startRow = Math.max(0, Math.floor(metrics.virtualTop / itemHeight) - DATASET_VIRTUAL_BUFFER_ROWS);
                const visibleRows = Math.ceil(metrics.viewport / itemHeight) + (DATASET_VIRTUAL_BUFFER_ROWS * 2);
                const endRow = Math.min(rowCount, startRow + visibleRows);

                for (let row = startRow; row < endRow; row += 1) {
                    for (let col = 0; col < columns; col += 1) {
                        const index = row * columns + col;
                        if (index >= renderIds.length) break;
                        const node = this._buildQueueItem(renderIds[index], index);
                        node.style.position = 'absolute';
                        node.style.top = `${metrics.domTopForRow(row)}px`;
                        node.style.left = isList ? '0' : `${col * (cellWidth + DATASET_QUEUE_GRID_GAP)}px`;
                        node.style.width = isList ? 'calc(100% - 6px)' : `${Math.max(1, cellWidth)}px`;
                        node.style.height = isList ? `${DATASET_QUEUE_LIST_HEIGHT - 4}px` : `${Math.max(1, cellWidth)}px`;
                        node.style.aspectRatio = 'auto';
                        node.style.boxSizing = 'border-box';
                        spacer.appendChild(node);
                    }
                }
                this._highlightActiveQueueItem();
                this._applyAuditFilterToQueue?.();
                this._updateMultiSelectUI();
            });
        };

        list.addEventListener('scroll', renderVisible, { passive: true });
        const resizeObserver = typeof ResizeObserver !== 'undefined'
            ? new ResizeObserver(renderVisible)
            : null;
        if (resizeObserver) resizeObserver.observe(list);
        this._queueVirtualCleanup = () => {
            if (frame) cancelAnimationFrame(frame);
            list.removeEventListener('scroll', renderVisible);
            if (resizeObserver) resizeObserver.disconnect();
        };
        renderVisible();
    };

    DM._buildQueueItem = function (id, orderIndex = null) {
        const meta = this.meta.get(id) || {};
        const resolvedOrderIndex = Number.isFinite(orderIndex) ? Number(orderIndex) : this.imageIds.indexOf(id);
        const item = document.createElement('div');
        item.setAttribute('role', 'button');
        item.tabIndex = 0;
        item.className = 'dataset-queue-item';
        item.dataset.imageId = String(id);
        if (resolvedOrderIndex >= 0) item.dataset.queueOrder = String(resolvedOrderIndex + 1);

        const selectToggle = document.createElement('button');
        selectToggle.type = 'button';
        selectToggle.className = 'dataset-queue-select-toggle';
        selectToggle.setAttribute('role', 'checkbox');
        selectToggle.setAttribute('aria-checked', this._queueSelection.has(id) ? 'true' : 'false');
        selectToggle.title = this._t('dataset.toggleSelect', 'Select image');
        selectToggle.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            this._toggleQueueSelection(id);
        });

        // v3.2.2 (issue #5 follow-up): caption status badge so the user
        // can see at a glance which images need work.
        //   edited   -> user manually edited the caption (sticky)
        //   tagged   -> backend produced a non-empty caption
        //   untagged -> empty rendered caption + no override; user
        //               should run "Tag all images" or write one
        const cap = this.captionEdits.has(id)
            ? this.captionEdits.get(id)
            : (this.captions.get(id) || '');
        let status = 'untagged';
        if (this.captionEdits.has(id)) status = 'edited';
        else if (String(cap).trim().length > 0) status = 'tagged';
        item.classList.add(`status-${status}`);

        const img = document.createElement('img');
        img.className = 'dataset-queue-thumb';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.alt = '';
        item.classList.add('is-loading');
        img.onload = () => {
            item.classList.remove('is-loading');
        };
        img.onerror = () => {
            item.classList.remove('is-loading');
            img.classList.add('is-missing');
            img.removeAttribute('src');
        };
        img.src = this._thumbSrc(id, 160);
        if (img.complete) item.classList.remove('is-loading');

        const orderBadge = document.createElement('span');
        orderBadge.className = 'dataset-queue-order';
        orderBadge.textContent = resolvedOrderIndex >= 0 ? String(resolvedOrderIndex + 1) : '?';
        orderBadge.title = this._t('dataset.queueOrder', 'Queue order');

        const metaWrap = document.createElement('div');
        metaWrap.className = 'dataset-queue-meta';
        const filename = document.createElement('strong');
        filename.className = 'dataset-queue-filename';
        filename.textContent = meta.filename || `image_${id}`;
        const idLabel = document.createElement('small');
        idLabel.className = 'dataset-queue-id';
        idLabel.textContent = `#${id}`;
        metaWrap.append(filename, idLabel);

        const badge = document.createElement('span');
        badge.className = `dataset-queue-badge dataset-queue-badge-${status}`;
        const badgeMap = {
            untagged: { icon: '⚠️', key: 'dataset.statusUntagged', fallback: 'no caption' },
            tagged:   { icon: '✓',  key: 'dataset.statusTagged',   fallback: 'tagged' },
            edited:   { icon: '✏️', key: 'dataset.statusEdited',   fallback: 'edited' },
        };
        const b = badgeMap[status];
        const badgeIcon = document.createElement('span');
        badgeIcon.setAttribute('aria-hidden', 'true');
        badgeIcon.textContent = b.icon;
        const badgeText = document.createElement('span');
        badgeText.textContent = this._t(b.key, b.fallback);
        badge.append(badgeIcon, badgeText);

        item.append(selectToggle, orderBadge, img, metaWrap, badge);
        item.addEventListener('click', (e) => {
            if (e.shiftKey || e.ctrlKey || e.metaKey) {
                this._handleMultiSelectClick(id, e);
            } else {
                this._queueSelection.clear();
                this._updateMultiSelectUI();
                this._setActive(id);
            }
        });
        item.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            if (e.shiftKey || e.ctrlKey || e.metaKey) this._handleMultiSelectClick(id, e);
            else this._setActive(id);
        });
        for (const decorate of this._queueItemDecorators) decorate.call(this, item, id);
        return item;
    };

    DM._refreshQueueItem = function (id) {
        const list = document.getElementById('dataset-queue-list');
        if (!list) return;
        const existing = list.querySelector(`.dataset-queue-item[data-image-id="${id}"]`);
        if (!existing) return;
        if (list.classList.contains('is-virtualized')) {
            this._renderQueue();
            return;
        }
        const orderIndex = Number(existing.dataset.queueOrder || 0) - 1;
        existing.replaceWith(this._buildQueueItem(id, Number.isFinite(orderIndex) ? orderIndex : null));
        this._highlightActiveQueueItem();
        this._applyAuditFilterToQueue?.();
    };

    DM._highlightActiveQueueItem = function () {
        const list = document.getElementById('dataset-queue-list');
        if (!list) return;
        for (const el of list.querySelectorAll('.dataset-queue-item')) {
            el.classList.toggle('active', Number(el.dataset.imageId) === Number(this.activeId));
        }
    };

    DM._setQueueViewMode = function (mode) {
        this._queueViewMode = mode === 'list' ? 'list' : 'grid';
        try { localStorage.setItem('sd-image-sorter-dataset-queue-mode', this._queueViewMode); } catch {}
        document.querySelectorAll('[data-dataset-queue-mode]').forEach((btn) => {
            const active = btn.getAttribute('data-dataset-queue-mode') === this._queueViewMode;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        this._renderQueue();
    };

    DM._initQueueModeControls = function () {
        document.querySelectorAll('[data-dataset-queue-mode]').forEach((btn) => {
            btn.addEventListener('click', () => this._setQueueViewMode(btn.getAttribute('data-dataset-queue-mode')));
        });
        this._setQueueViewMode(this._queueViewMode || 'grid');
    };

    // ---------- Queue caption filter ----------
    DM._queueCaptionFilter = 'all';

    DM._queueIdsForCurrentFilter = function () {
        const filter = this._queueCaptionFilter || 'all';
        if (filter === 'all') return Array.from(this.imageIds || []);
        return (this.imageIds || []).filter((id) => {
            const caption = (this.captionEdits?.get?.(id) || this.captions?.get?.(id) || '').trim();
            const hasCaption = caption.length > 0;
            return filter === 'tagged' ? hasCaption : !hasCaption;
        });
    };

    DM._initQueueCaptionFilter = function () {
        const sel = document.getElementById('dataset-queue-caption-filter');
        if (!sel) return;
        sel.addEventListener('change', () => {
            this._queueCaptionFilter = sel.value || 'all';
            this._renderQueue();
        });
    };

    DM._applyCaptionFilter = function () {
        const filter = this._queueCaptionFilter || 'all';
        const list = document.getElementById('dataset-queue-list');
        if (list?.classList.contains('is-virtualized')) {
            this._renderQueue();
            return;
        }
        const items = document.querySelectorAll('#dataset-queue-list .dataset-queue-item');
        for (const it of items) {
            if (filter === 'all') {
                it.style.display = '';
                continue;
            }
            const id = Number(it.dataset.imageId || 0);
            const caption = (this.captionEdits?.get?.(id) || this.captions?.get?.(id) || '').trim();
            const hasCaption = caption.length > 0;
            if (filter === 'tagged') it.style.display = hasCaption ? '' : 'none';
            else it.style.display = hasCaption ? 'none' : '';
        }
    };

    DM._updateCount = function () {
        const num = document.getElementById('dataset-count-num');
        if (num) num.textContent = String(this.imageIds.length);
        const clearBtn = document.getElementById('btn-dataset-clear');
        if (clearBtn) clearBtn.hidden = this.imageIds.length === 0;
        this._syncTriggerQuickfillButton?.();
        if (DM._refreshExportPreview) DM._refreshExportPreview();
    };

    DM._renderImportGallery = function () {
        const container = document.getElementById('dataset-import-gallery');
        const grid = document.getElementById('dataset-import-gallery-grid');
        const countEl = document.getElementById('dataset-import-gallery-count');
        if (!container || !grid) return;

        if (this.imageIds.length === 0) {
            container.hidden = true;
            grid.innerHTML = '';
            grid.classList.remove('is-virtualized');
            delete grid.dataset.virtualRows;
            cleanupVirtualRenderer(this, '_importVirtualCleanup');
            this._updateMultiSelectUI();
            return;
        }

        container.hidden = false;
        if (countEl) {
            countEl.textContent = this._t('dataset.importGalleryCount',
                '{count} images imported', { count: this.imageIds.length });
        }

        if (this.imageIds.length > DATASET_VIRTUAL_THRESHOLD) {
            this._renderVirtualImportGallery(grid);
            return;
        }

        cleanupVirtualRenderer(this, '_importVirtualCleanup');
        grid.classList.remove('is-virtualized');
        delete grid.dataset.virtualRows;
        grid.innerHTML = '';
        for (const [index, id] of this.imageIds.entries()) {
            grid.appendChild(this._buildImportThumb(id, index));
        }
        this._updateMultiSelectUI();
    };

    DM._buildImportThumb = function (id, orderIndex = null) {
        const resolvedOrderIndex = Number.isFinite(orderIndex) ? Number(orderIndex) : this.imageIds.indexOf(id);
        const thumb = document.createElement('div');
        thumb.className = 'import-thumb';
        thumb.dataset.imageId = String(id);
        if (resolvedOrderIndex >= 0) thumb.dataset.queueOrder = String(resolvedOrderIndex + 1);
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.decoding = 'async';
        img.alt = '';
        thumb.classList.add('is-loading');
        img.onload = () => {
            thumb.classList.remove('is-loading');
        };
        img.onerror = () => {
            thumb.classList.remove('is-loading');
            img.removeAttribute('src');
            thumb.classList.add('thumb-missing');
        };

        const src = this._thumbSrc(id, 160);
        if (src) img.src = src;
        else thumb.classList.add('preview-pending');
        if (!src || img.complete) thumb.classList.remove('is-loading');

        img.style.width = '100%';
        img.style.height = '100%';
        img.style.objectFit = 'cover';
        thumb.appendChild(img);
        const order = document.createElement('span');
        order.className = 'import-thumb-order';
        order.textContent = resolvedOrderIndex >= 0 ? String(resolvedOrderIndex + 1) : '?';
        order.title = this._t('dataset.queueOrder', 'Queue order');
        thumb.appendChild(order);
        const keep = document.createElement('span');
        keep.className = 'import-thumb-keep';
        keep.textContent = this._t('dataset.keepBadge', 'Keep');
        thumb.appendChild(keep);
        const selected = document.createElement('span');
        selected.className = 'import-thumb-selected';
        selected.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-check\"/></svg>";
        selected.setAttribute('aria-hidden', 'true');
        thumb.appendChild(selected);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'import-thumb-remove';
        remove.textContent = 'x';
        remove.title = this._t('dataset.removeFromDataset', 'Remove');
        remove.addEventListener('click', (event) => {
            event.stopPropagation();
            this._removeImageById(id);
        });
        thumb.appendChild(remove);
        thumb.addEventListener('click', (event) => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) this._handleMultiSelectClick(id, event);
            else this._toggleQueueSelection(id);
        });
        thumb.addEventListener('dblclick', () => {
            this._setActive(id);
            this._setPipelineTab?.('workbench');
        });
        return thumb;
    };

    DM._renderVirtualImportGallery = function (grid) {
        cleanupVirtualRenderer(this, '_importVirtualCleanup');
        grid.innerHTML = '';
        grid.classList.add('is-virtualized');

        const spacer = document.createElement('div');
        spacer.className = 'dataset-virtual-spacer dataset-import-virtual-spacer';
        grid.appendChild(spacer);

        let frame = 0;
        const renderVisible = () => {
            if (frame) cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                frame = 0;
                const gridLayout = getGridLayout(grid, DATASET_IMPORT_GRID_MIN, DATASET_IMPORT_GRID_GAP);
                const columns = gridLayout.columns;
                const cellWidth = gridLayout.cellWidth;
                const itemHeight = gridLayout.rowStride;
                const rowCount = Math.ceil(this.imageIds.length / columns);
                const metrics = getVirtualMetrics(grid, rowCount, itemHeight);
                grid.dataset.virtualRows = String(rowCount);
                spacer.style.height = `${metrics.spacerHeight}px`;
                spacer.style.position = 'relative';
                spacer.innerHTML = '';

                const startRow = Math.max(0, Math.floor(metrics.virtualTop / itemHeight) - DATASET_VIRTUAL_BUFFER_ROWS);
                const visibleRows = Math.ceil(metrics.viewport / itemHeight) + (DATASET_VIRTUAL_BUFFER_ROWS * 2);
                const endRow = Math.min(rowCount, startRow + visibleRows);

                for (let row = startRow; row < endRow; row += 1) {
                    for (let col = 0; col < columns; col += 1) {
                        const index = row * columns + col;
                        if (index >= this.imageIds.length) break;
                        const thumb = this._buildImportThumb(this.imageIds[index], index);
                        thumb.style.position = 'absolute';
                        thumb.style.top = `${metrics.domTopForRow(row)}px`;
                        thumb.style.left = `${col * (cellWidth + DATASET_IMPORT_GRID_GAP)}px`;
                        thumb.style.width = `${Math.max(1, cellWidth)}px`;
                        thumb.style.height = `${Math.max(1, cellWidth)}px`;
                        thumb.style.aspectRatio = 'auto';
                        thumb.style.boxSizing = 'border-box';
                        spacer.appendChild(thumb);
                    }
                }
                this._updateMultiSelectUI();
            });
        };

        grid.addEventListener('scroll', renderVisible, { passive: true });
        const resizeObserver = typeof ResizeObserver !== 'undefined'
            ? new ResizeObserver(renderVisible)
            : null;
        if (resizeObserver) resizeObserver.observe(grid);
        this._importVirtualCleanup = () => {
            if (frame) cancelAnimationFrame(frame);
            grid.removeEventListener('scroll', renderVisible);
            if (resizeObserver) resizeObserver.disconnect();
        };
        renderVisible();
    };
})();
