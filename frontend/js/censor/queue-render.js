/**
 * Censor Editor - queue rendering (split VERBATIM from censor-edit.js; god-file decomposition).
 * Queue strip + queue-manager modal rendering, selection sync, drag-and-drop reorder handlers.
 * Shared top-level bindings (CensorState, ...) are declared in censor/state.js;
 * classic-script global lexical scoping keeps them single instances across parts.
 * Load order is pinned in index.html - see censor/state.js for the full note.
 */
function createCensorBatchOutcomeBadge(item, layoutClass) {
    if (typeof layoutClass !== 'string' || !layoutClass.trim()) {
        throw new TypeError('A non-empty layout class is required for a censor batch outcome badge.');
    }

    const presentation = getCensorBatchOutcomePresentation(item);
    if (!presentation) return null;

    const badge = document.createElement(presentation.isFailure ? 'button' : 'span');
    badge.className = `censor-batch-outcome-badge ${layoutClass} is-${presentation.code}`;
    badge.dataset.testid = 'censor-batch-outcome-badge';
    badge.dataset.imageId = String(item.id);
    badge.dataset.status = presentation.code;
    badge.textContent = presentation.label;

    if (presentation.isFailure) {
        badge.type = 'button';
        badge.setAttribute('aria-label', presentation.failureAriaLabel);
        badge.title = presentation.failureTooltip;
        badge.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            showCensorBatchFailureReason(item);
        });
        badge.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.stopPropagation();
        });
    }

    return badge;
}

function renderQueue() {
    // Every queue mutation funnels through a re-render, so this is the single
    // chokepoint that keeps the persisted copy in sync (QA P3-11).
    persistCensorQueue();

    const list = document.getElementById('censor-queue-list');
    if (!list) return;

    const validIds = new Set(CensorState.queue.map(item => item.id));
    CensorState.selectedItems = new Set(
        [...CensorState.selectedItems].filter(id => validIds.has(id))
    );
    if (CensorState.activeId && !validIds.has(CensorState.activeId)) {
        CensorState.activeId = null;
    }
    if (CensorState.pendingActiveId && !validIds.has(CensorState.pendingActiveId)) {
        CensorState.pendingActiveId = null;
    }

    // Handle empty state
    if (CensorState.queue.length === 0) {
        CensorState.pendingActiveId = null;
        list.innerHTML = `
            <div class="queue-empty-state-v2">
                <span class="empty-icon"><svg class="icon" aria-hidden="true"><use href="#i-image"/></svg></span>
                <p>${escapeHtml(censorT('censor.noImages', null, 'No images selected'))}</p>
                <small>${escapeHtml(censorT('censor.selectFromGallery', null, 'Select from Gallery'))}</small>
            </div>
        `;
        updateQueueSelection();
        updateUndoRedoButtons();
        return;
    }

    // Get existing thumbnails
    const existingThumbs = list.querySelectorAll('.queue-thumb-v2');
    const existingIds = new Set([...existingThumbs].map(t => t.dataset.id));
    const queueIds = new Set(CensorState.queue.map(item => item.id.toString()));

    // Remove thumbnails not in queue anymore
    existingThumbs.forEach(thumb => {
        if (!queueIds.has(thumb.dataset.id)) {
            const shell = thumb.closest('.queue-thumb-shell-v2');
            if (shell) shell.remove();
            else thumb.remove();
        }
    });

    // Clear empty state if present
    const emptyState = list.querySelector('.queue-empty-state-v2');
    if (emptyState) emptyState.remove();

    // Update or create thumbnails
    CensorState.queue.forEach((item, index) => {
        const itemIdStr = item.id.toString();
        let img = list.querySelector(`.queue-thumb-v2[data-id="${itemIdStr}"]`);
        let shell = img?.closest('.queue-thumb-shell-v2') || null;

        if (!img) {
            // Create new thumbnail
            shell = document.createElement('div');
            shell.className = 'queue-thumb-shell-v2';
            shell.dataset.imageId = itemIdStr;
            img = document.createElement('img');
            img.className = 'queue-thumb-v2';
            img.draggable = true;
            img.setAttribute('role', 'button');
            img.setAttribute('tabindex', '0');
            img.setAttribute('aria-selected', 'false');
            img.setAttribute('aria-pressed', 'false');
            img.dataset.id = itemIdStr;

            // Click to load with multi-select support
            const syncSelectedState = () => {
                const isSelected = CensorState.selectedItems.has(item.id);
                img.classList.toggle('selected', isSelected);
                img.setAttribute('aria-selected', String(isSelected));
                img.setAttribute('aria-pressed', String(isSelected));
            };

            img.addEventListener('click', (e) => {
                const clickedIndex = parseInt(img.dataset.index, 10);
                const clickedId = item.id;

                if (e.ctrlKey || e.metaKey) {
                    // Ctrl+click: toggle selection
                    if (CensorState.selectedItems.has(clickedId)) {
                        CensorState.selectedItems.delete(clickedId);
                    } else {
                        CensorState.selectedItems.add(clickedId);
                    }
                    CensorState.lastSelectedIndex = clickedIndex;
                } else if (e.shiftKey && CensorState.lastSelectedIndex >= 0) {
                    // Shift+click: range selection
                    const start = Math.min(CensorState.lastSelectedIndex, clickedIndex);
                    const end = Math.max(CensorState.lastSelectedIndex, clickedIndex);
                    CensorState.selectedItems.clear();
                    for (let i = start; i <= end; i++) {
                        if (CensorState.queue[i]) {
                            CensorState.selectedItems.add(CensorState.queue[i].id);
                        }
                    }
                } else {
                    // Normal click: select single and load
                    CensorState.selectedItems.clear();
                    CensorState.selectedItems.add(clickedId);
                    CensorState.lastSelectedIndex = clickedIndex;
                    loadCanvasImage(item.id);
                }

                updateQueueSelection();
            });

            syncSelectedState();

            // DnD Events
            img.addEventListener('dragstart', handleDragStart);
            img.addEventListener('dragend', handleDragEnd);
            img.addEventListener('dragover', handleDragOver);
            img.addEventListener('drop', handleDrop);
            img.addEventListener('dragenter', (e) => e.target.classList.add('drag-over'));
            img.addEventListener('dragleave', (e) => e.target.classList.remove('drag-over'));

            shell.appendChild(img);
            list.appendChild(shell);
        } else if (!shell) {
            shell = document.createElement('div');
            shell.className = 'queue-thumb-shell-v2';
            shell.dataset.imageId = itemIdStr;
            img.replaceWith(shell);
            shell.appendChild(img);
        }

        // Always append to maintain order (appendChild moves existing node to end).
        list.appendChild(shell);

        // Update properties (always update these - they may have changed)
        img.dataset.index = index;
        const baseTitle = item.outputFilename || '';
        img.title = item.batchError ? `${baseTitle}\n⚠ ${item.batchError}` : baseTitle;

        // Only update src if it changed (prevents reload flash)
        const newSrc = getCensorItemPreviewSrc(item);
        if (img.src !== newSrc) {
            img.src = newSrc;
        }

        // Update classes
        const isActive = item.id === getFocusedCensorImageId();
        const isProcessed = item.isProcessed;
        const isSelected = CensorState.selectedItems.has(item.id);
        const batchFailed = item.batchStatus === 'failed';
        const batchRefined = item.batchStatus === 'refined';
        img.classList.toggle('active', isActive);
        img.classList.toggle('processed', isProcessed);
        img.classList.toggle('selected', isSelected);
        img.classList.toggle('batch-error', batchFailed);
        img.classList.toggle('batch-refined', batchRefined && !isProcessed);
        img.setAttribute('aria-selected', String(isSelected));
        img.setAttribute('aria-pressed', String(isSelected));

        shell.querySelector('.censor-batch-outcome-main')?.remove();
        const outcomeBadge = createCensorBatchOutcomeBadge(item, 'censor-batch-outcome-main');
        if (outcomeBadge) shell.appendChild(outcomeBadge);
    });

    renderTokenQueueLoadMoreControl(list);
    updateQueueSelection();
    updateUndoRedoButtons();
}

function initDragAndDrop() {
    // Basic setup handled in renderQueue listeners
}

function getQueueManagerItems() {
    const search = String(CensorState.queueManagerSearch || '').trim().toLowerCase();
    return CensorState.queue.filter((item) => {
        if (CensorState.queueManagerShowSelectedOnly && !CensorState.selectedItems.has(item.id)) {
            return false;
        }
        if (!search) return true;
        const haystack = `${item.outputFilename || ''} ${item.originalFilename || ''}`.toLowerCase();
        return haystack.includes(search);
    });
}

function openQueueManager() {
    // Use the new Solitaire queue manager if available
    if (window.QueueSolitaire) {
        window.QueueSolitaire.open();
        return;
    }
    // Fallback to old modal
    CensorState.queueManagerSearch = '';
    CensorState.queueManagerShowSelectedOnly = false;
    const searchInput = document.getElementById('queue-manager-search');
    const selectedToggle = document.getElementById('queue-manager-show-selected');
    if (searchInput) searchInput.value = '';
    if (selectedToggle) selectedToggle.checked = false;
    renderQueueManager();
    if (typeof showModal === 'function') {
        showModal('queue-manager-modal');
    } else {
        document.getElementById('queue-manager-modal')?.classList.add('visible');
    }
    setTimeout(() => searchInput?.focus(), 140);
}

function closeQueueManager() {
    if (typeof hideModal === 'function') {
        hideModal('queue-manager-modal');
    } else {
        document.getElementById('queue-manager-modal')?.classList.remove('visible');
    }
}

function formatQueueManagerSummary(visibleCount) {
    return censorT(
        'censor.queueManagerSummary',
        {
            selected: CensorState.selectedItems.size,
            visible: visibleCount,
            total: CensorState.queue.length,
        },
        '{selected} selected • {visible}/{total} visible • drag rows or use the move bar below'
    );
}

function getQueueManagerThumbnailSrc(item) {
    const api = window.App?.API;
    if (item?.previewDataUrl) return item.previewDataUrl;
    if (item?.currentDataUrl) return item.currentDataUrl;
    if (item?.id && typeof api?.getThumbnailUrl === 'function') {
        return api.getThumbnailUrl(item.id, 320);
    }
    return item?.originalUrl || '';
}

function getQueueManagerStatusBadges(item) {
    const badges = [];
    if (item.id === getFocusedCensorImageId()) {
        badges.push(`<span class="queue-manager-badge is-active">${escapeHtml(censorT('common.current', null, 'Current'))}</span>`);
    }
    if (item.isProcessed) {
        badges.push(`<span class="queue-manager-badge is-processed">${escapeHtml(censorT('common.processed', null, 'Processed'))}</span>`);
    }
    return badges.join('');
}

function renderQueueManagerSelectionStrip(items = []) {
    const strip = document.getElementById('queue-manager-selection-strip');
    const countEl = document.getElementById('queue-manager-selection-count');
    const selectedItems = Array.isArray(items) ? items : [];

    if (countEl) {
        countEl.textContent = selectedItems.length > 0
            ? censorT('censor.queueSelectionSummary', { count: selectedItems.length }, '{count} selected')
            : censorT('censor.queueNoSelection', null, 'No selection');
        countEl.classList.toggle('is-empty', selectedItems.length === 0);
    }

    if (!strip) return;

    if (!selectedItems.length) {
        strip.innerHTML = `
            <div class="queue-manager-selection-empty">
                ${escapeHtml(censorT('censor.queueSelectionHelp', null, 'Pick one or more thumbnails to enable batch moves.'))}
            </div>
        `;
        return;
    }

    strip.innerHTML = selectedItems.map((item) => {
        const thumbSrc = escapeHtml(getQueueManagerThumbnailSrc(item));
        const label = escapeHtml(item.outputFilename || item.originalFilename || `Image ${item.id}`);
        return `
            <button class="queue-manager-selection-chip" type="button" data-id="${item.id}" title="${label}">
                <img class="queue-manager-selection-chip-thumb" src="${thumbSrc}" alt="${label}" loading="lazy" decoding="async">
                <span class="queue-manager-selection-chip-label">${label}</span>
            </button>
        `;
    }).join('');

    strip.querySelectorAll('.queue-manager-selection-chip[data-id]').forEach((chip) => {
        chip.addEventListener('click', () => {
            const itemId = Number.parseInt(chip.dataset.id, 10);
            scrollQueueItemIntoView(itemId);
        });
    });
}

function renderQueueManager() {
    const list = document.getElementById('queue-manager-list');
    const summary = document.getElementById('queue-manager-summary');
    const positionInput = document.getElementById('queue-manager-position');
    const countEl = document.getElementById('queue-manager-selection-count');
    if (!list || !summary) return;

    const items = getQueueManagerItems();
    summary.textContent = formatQueueManagerSummary(items.length);

    if (countEl) {
        const count = CensorState.selectedItems.size;
        countEl.textContent = count > 0
            ? censorT('censor.queueSelectionSummary', { count }, '{count} selected')
            : censorT('censor.queueSelectionZero', null, '0 selected');
    }

    if (positionInput && CensorState.selectedItems.size > 0) {
        const firstSelectedIndex = CensorState.queue.findIndex((item) => CensorState.selectedItems.has(item.id));
        if (firstSelectedIndex >= 0) {
            positionInput.value = String(firstSelectedIndex + 1);
        }
    }

    if (!items.length) {
        list.innerHTML = `<div class="queue-manager-empty">${escapeHtml(censorT('censor.queueManagerEmpty', null, 'No queue items match the current filter.'))}</div>`;
        return;
    }

    list.innerHTML = items.map((item) => {
        const index = CensorState.queue.findIndex((entry) => entry.id === item.id);
        const isActive = item.id === getFocusedCensorImageId();
        const isSelected = CensorState.selectedItems.has(item.id);
        const isProcessed = item.processed || item.saved;
        const classes = [
            'queue-manager-grid-item',
            isActive ? 'is-active' : '',
            isSelected ? 'is-selected' : '',
            isProcessed ? 'is-processed' : '',
        ].filter(Boolean).join(' ');
        const badgeClass = isActive ? 'is-active' : isProcessed ? 'is-processed' : '';
        const displayName = item.outputFilename || item.originalFilename || `Image ${item.id}`;
        return `
            <div class="${classes}" data-id="${item.id}" data-index="${index}" draggable="true" title="${escapeHtml(displayName)}">
                <img class="queue-manager-grid-thumb" src="${escapeHtml(getQueueManagerThumbnailSrc(item))}" alt="${escapeHtml(displayName)}" loading="lazy" decoding="async">
                <span class="queue-manager-grid-index">${index + 1}</span>
                ${badgeClass ? `<span class="queue-manager-grid-badge ${badgeClass}"></span>` : ''}
                <div class="queue-manager-grid-label" title="Double-click to rename">${escapeHtml(displayName)}</div>
            </div>
        `;
    }).join('');

    list.querySelectorAll('.queue-manager-grid-item').forEach((item) => {
        item.addEventListener('click', (event) => {
            const clickedId = Number.parseInt(item.dataset.id, 10);
            const clickedIndex = Number.parseInt(item.dataset.index, 10);

            if (event.ctrlKey || event.metaKey) {
                if (CensorState.selectedItems.has(clickedId)) {
                    CensorState.selectedItems.delete(clickedId);
                } else {
                    CensorState.selectedItems.add(clickedId);
                }
                CensorState.lastSelectedIndex = clickedIndex;
            } else if (event.shiftKey && CensorState.lastSelectedIndex >= 0) {
                const start = Math.min(CensorState.lastSelectedIndex, clickedIndex);
                const end = Math.max(CensorState.lastSelectedIndex, clickedIndex);
                CensorState.selectedItems.clear();
                for (let i = start; i <= end; i++) {
                    if (CensorState.queue[i]) {
                        CensorState.selectedItems.add(CensorState.queue[i].id);
                    }
                }
            } else {
                CensorState.selectedItems.clear();
                CensorState.selectedItems.add(clickedId);
                CensorState.lastSelectedIndex = clickedIndex;
            }

            updateQueueSelection();
        });

        item.addEventListener('dblclick', async (event) => {
            const label = item.querySelector('.queue-manager-grid-label');
            if (label && event.target === label) {
                // Enable inline rename
                label.contentEditable = 'true';
                label.focus();
                const range = document.createRange();
                range.selectNodeContents(label);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                const finishRename = () => {
                    label.contentEditable = 'false';
                    const clickedId = Number.parseInt(item.dataset.id, 10);
                    const queueItem = CensorState.queue.find((entry) => entry.id === clickedId);
                    if (queueItem) {
                        const newName = label.textContent.trim();
                        if (newName) queueItem.outputFilename = newName;
                    }
                    label.removeEventListener('blur', finishRename);
                    label.removeEventListener('keydown', handleKey);
                };
                const handleKey = (e) => {
                    if (e.key === 'Enter') { e.preventDefault(); label.blur(); }
                    if (e.key === 'Escape') { label.contentEditable = 'false'; }
                };
                label.addEventListener('blur', finishRename);
                label.addEventListener('keydown', handleKey);
                return;
            }
            // Double-click on thumb loads into editor
            const clickedId = Number.parseInt(item.dataset.id, 10);
            const queueItem = CensorState.queue.find((entry) => entry.id === clickedId);
            await loadCanvasImage(clickedId);
            closeQueueManager();
            window.App.showToast(
                censorT(
                    'censor.queueManagerLoaded',
                    { filename: queueItem?.outputFilename || queueItem?.originalFilename || String(clickedId) },
                    'Loaded {filename} into the editor.'
                ),
                'success'
            );
        });

        item.addEventListener('dragstart', handleQueueManagerDragStart);
        item.addEventListener('dragend', handleQueueManagerDragEnd);
        item.addEventListener('dragover', (event) => {
            event.preventDefault();
            item.classList.add('drag-over');
        });
        item.addEventListener('dragleave', () => item.classList.remove('drag-over'));
        item.addEventListener('drop', handleQueueManagerDrop);
    });
}

function handleQueueManagerDragStart(e) {
    const draggedId = this.dataset.id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedId);
    this.classList.add('dragging');
}

function handleQueueManagerDragEnd() {
    this.classList.remove('dragging');
    document.querySelectorAll('.queue-manager-grid-item').forEach((el) => el.classList.remove('drag-over'));
}

function handleQueueManagerDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    const draggedId = Number.parseInt(e.dataTransfer.getData('text/plain'), 10);
    const targetId = Number.parseInt(this.dataset.id, 10);
    this.classList.remove('drag-over');
    reorderQueueByDraggedTarget(draggedId, targetId);
}

function updateQueueSelection() {
    // Update visual selection state on all queue thumbnails
    document.querySelectorAll('.queue-thumb-v2').forEach(img => {
        // IDs can be string or number — normalize to string for comparison
        const itemIdStr = img.dataset.id;
        const isSelected = [...CensorState.selectedItems].some(id => id.toString() === itemIdStr);
        img.classList.toggle('selected', isSelected);
        img.setAttribute('aria-selected', String(isSelected));
        img.setAttribute('aria-pressed', String(isSelected));
    });

    // Update selection count indicator if it exists
    const countEl = document.getElementById('queue-selection-count');
    if (countEl) {
        const count = CensorState.selectedItems.size;
        countEl.textContent = count > 0
            ? censorT('censor.queueSelectionSummary', { count }, '{count} selected')
            : '';
        countEl.style.display = count > 0 ? 'inline-flex' : 'none';
    }

    updateQueueActionState();
    renderQueueManager();
}

function updateQueueActionState() {
    const hasQueue = CensorState.queue.length > 0;
    const hasSelection = CensorState.selectedItems.size > 0;
    [
        'btn-queue-move-top',
        'btn-queue-move-up',
        'btn-queue-move-down',
        'btn-queue-move-bottom',
        'btn-queue-manager-move-top',
        'btn-queue-manager-move-up',
        'btn-queue-manager-move-down',
        'btn-queue-manager-move-bottom',
        'btn-queue-manager-move-position',
    ].forEach(id => {
        const button = document.getElementById(id);
        if (!button) return;
        button.disabled = !hasQueue || !hasSelection;
    });
}

let draggedItemIndex = null;
const QUEUE_DRAG_SCROLL_EDGE_PX = 64;
const QUEUE_DRAG_SCROLL_STEP_PX = 28;

function handleDragStart(e) {
    draggedItemIndex = parseInt(this.dataset.index, 10);
    e.dataTransfer.effectAllowed = 'move';
    // Use the ID as the data to ensure we identify the right item even if index changes
    e.dataTransfer.setData('text/plain', this.dataset.id);
    this.classList.add('dragging');
    // Set dragging opacity
    setTimeout(() => { this.style.opacity = '0.5'; }, 0);
}

function handleDragEnd(e) {
    this.style.opacity = '1';
    this.classList.remove('dragging');
    // Clean up all drag-over states
    document.querySelectorAll('.queue-thumb-v2').forEach(el => {
        el.classList.remove('drag-over');
    });
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    maybeAutoScrollQueue(e.clientY);
    return false;
}

function maybeAutoScrollQueue(clientY) {
    const queueList = document.getElementById('censor-queue-list');
    if (!queueList) return;

    const rect = queueList.getBoundingClientRect();
    if (!rect.height) return;

    const distanceFromTop = clientY - rect.top;
    const distanceFromBottom = rect.bottom - clientY;

    if (distanceFromTop >= 0 && distanceFromTop < QUEUE_DRAG_SCROLL_EDGE_PX) {
        const intensity = 1 - (distanceFromTop / QUEUE_DRAG_SCROLL_EDGE_PX);
        queueList.scrollTop -= Math.max(10, Math.round(QUEUE_DRAG_SCROLL_STEP_PX * intensity));
    } else if (distanceFromBottom >= 0 && distanceFromBottom < QUEUE_DRAG_SCROLL_EDGE_PX) {
        const intensity = 1 - (distanceFromBottom / QUEUE_DRAG_SCROLL_EDGE_PX);
        queueList.scrollTop += Math.max(10, Math.round(QUEUE_DRAG_SCROLL_STEP_PX * intensity));
    }
}

function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();
    const targetItem = e.target.closest('.queue-thumb-v2');
    if (!targetItem) return false;

    const draggedId = e.dataTransfer.getData('text/plain');
    const targetId = Number.parseInt(targetItem.dataset.id, 10);
    reorderQueueByDraggedTarget(Number.parseInt(draggedId, 10), targetId);

    document.querySelectorAll('.queue-thumb-v2').forEach(el => {
        el.classList.remove('dragging', 'drag-over');
    });
    return false;
}

