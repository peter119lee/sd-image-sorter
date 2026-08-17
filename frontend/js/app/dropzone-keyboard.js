/**
 * app/dropzone-keyboard.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 9634-9936 (of 10,152): gallery drop zone + global keyboard shortcuts.
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
// ============== Initialization ==============

// Global keyboard shortcuts for gallery navigation
// ============== Gallery Drag-and-Drop Import ==============

function initGalleryDropZone() {
    const galleryView = document.getElementById('view-gallery');
    if (!galleryView) return;

    let overlay = document.getElementById('gallery-drop-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'gallery-drop-overlay';
        overlay.className = 'gallery-drop-overlay';
        overlay.innerHTML = `
            <div class="gallery-drop-overlay-content">
                <span class="gallery-drop-overlay-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-folder"/></svg></span>
                <span class="gallery-drop-overlay-text">${escapeHtml(appT('gallery.dropToImport', 'Drop folder or images to import'))}</span>
                <span class="gallery-drop-overlay-hint">${escapeHtml(appT('gallery.dropHint', 'Images will be imported from the dropped folder'))}</span>
            </div>`;
        galleryView.appendChild(overlay);
    }

    let dragCounter = 0;

    galleryView.addEventListener('dragenter', (e) => {
        if (AppState.currentView !== 'gallery') return;
        if (!_hasFolderOrImageFiles(e)) return;
        e.preventDefault();
        dragCounter++;
        if (dragCounter === 1) overlay.classList.add('visible');
    });

    galleryView.addEventListener('dragover', (e) => {
        if (AppState.currentView !== 'gallery') return;
        if (!_hasFolderOrImageFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
    });

    galleryView.addEventListener('dragleave', (e) => {
        if (AppState.currentView !== 'gallery') return;
        e.preventDefault();
        dragCounter--;
        if (dragCounter <= 0) {
            dragCounter = 0;
            overlay.classList.remove('visible');
        }
    });

    galleryView.addEventListener('drop', (e) => {
        if (AppState.currentView !== 'gallery') return;
        e.preventDefault();
        dragCounter = 0;
        overlay.classList.remove('visible');
        _handleGalleryDrop(e);
    });
}

function _hasFolderOrImageFiles(e) {
    if (!e.dataTransfer) return false;
    const types = e.dataTransfer.types;
    return types && (types.includes('Files') || types.indexOf('Files') >= 0);
}

// Read a dropped directory's top-level image files via the FileSystemEntry API
// (used because dataTransfer.files is typically empty for folder drops). Returns
// [{name, size}] capped so a huge folder can't stall the resolve request.
function _readTopLevelDroppedImageFiles(dirEntry, cap = 12) {
    return new Promise((resolve) => {
        if (!dirEntry || typeof dirEntry.createReader !== 'function') { resolve([]); return; }
        const IMG = /\.(png|jpe?g|webp|bmp|gif)$/i;
        const reader = dirEntry.createReader();
        const fileEntries = [];
        let settled = false;
        const fail = () => { if (!settled) { settled = true; resolve([]); } };

        const finish = () => {
            const picked = fileEntries.slice(0, cap);
            if (!picked.length) { if (!settled) { settled = true; resolve([]); } return; }
            const out = [];
            let pending = picked.length;
            picked.forEach((fe) => {
                fe.file(
                    (f) => { out.push({ name: f.name, size: f.size || 0 }); if (--pending === 0 && !settled) { settled = true; resolve(out); } },
                    () => { if (--pending === 0 && !settled) { settled = true; resolve(out); } }
                );
            });
        };

        // readEntries returns in batches; keep calling until it yields none.
        const readBatch = () => {
            reader.readEntries((entries) => {
                if (!entries.length || fileEntries.length >= cap) { finish(); return; }
                for (const entry of entries) {
                    if (entry.isFile && IMG.test(entry.name)) fileEntries.push(entry);
                }
                readBatch();
            }, fail);
        };
        readBatch();
    });
}

async function _handleGalleryDrop(e) {
    const items = e.dataTransfer.items;
    const files = e.dataTransfer.files;
    const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/gif']);

    let isFolder = false;
    let folderName = '';
    let dirEntry = null;

    if (items && items.length > 0) {
        for (let i = 0; i < items.length; i++) {
            const entry = items[i].webkitGetAsEntry?.() || items[i].getAsEntry?.();
            if (entry && entry.isDirectory) {
                isFolder = true;
                folderName = entry.name || '';
                dirEntry = entry;
                break;
            }
        }
    }

    if (isFolder) {
        // dataTransfer.files is usually empty for a folder drop in Chrome, so
        // read the folder's own top-level image files via the entry — that gives
        // resolve_drop real filenames to match against the library and locate the
        // on-disk folder (the browser never exposes its absolute path directly).
        let folderFiles = [];
        if (dirEntry) {
            try { folderFiles = await _readTopLevelDroppedImageFiles(dirEntry); } catch (_) { /* ignore */ }
        }
        _handleFolderDrop(folderName, files, folderFiles);
        return;
    }

    if (files && files.length > 0) {
        const imageFiles = Array.from(files).filter(f =>
            IMAGE_TYPES.has(f.type) || /\.(png|jpe?g|webp|bmp|gif)$/i.test(f.name)
        );
        if (imageFiles.length > 0) {
            _handleImageFilesDrop(imageFiles);
            return;
        }
    }

    showModal('scan-modal');
}

async function _handleFolderDrop(folderName, files, folderFiles) {
    const droppedFiles = [];
    // Prefer the folder's own files (read via the directory entry); fall back to
    // the flat dataTransfer.files, which is often empty for folder drops.
    const source = (folderFiles && folderFiles.length) ? folderFiles : Array.from(files || []);
    for (let i = 0; i < Math.min(source.length, 8); i++) {
        const f = source[i];
        if (f && f.name) {
            droppedFiles.push({ name: f.name, size: f.size || 0 });
        }
    }

    try {
        const result = await API.resolveDrop(folderName, droppedFiles);
        if (result?.folder_path) {
            _openScanWithPath(result.folder_path);
            return;
        }
    } catch (_) { /* fallback below */ }

    // Resolution failed. Browsers never expose a dropped folder's absolute path,
    // and we couldn't match its files to a known library folder — so do NOT put
    // the bare folder name in the path field (it would be read as a relative path
    // like "26_05_29" and fail with "Folder does not exist"). Open the scan modal
    // with an empty path and launch the folder browser so the user can locate it.
    showModal('scan-modal');
    const input = document.getElementById('scan-folder-path');
    if (input) {
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    showToast(
        appT('gallery.dropHintBrowse', "Couldn't auto-locate \"{name}\" — your browser can't share a folder's full path. Pick it with Browse to scan it.")
            .replace('{name}', folderName || (droppedFiles[0]?.name) || ''),
        'warning'
    );
    if (input && typeof window.showFolderBrowser === 'function') {
        try { window.showFolderBrowser(input); } catch (_) { /* ignore */ }
    }
}

async function _handleImageFilesDrop(imageFiles) {
    showToast(
        appT('gallery.importingDropped', 'Importing {count} images...')
            .replace('{count}', String(imageFiles.length)),
        'info'
    );
    try {
        const result = await API.importFiles(imageFiles);
        const imported = result?.imported || 0;
        const errors = result?.errors || 0;
        if (imported > 0) {
            showToast(
                appT('gallery.importedDropped', 'Imported {count} images into gallery')
                    .replace('{count}', String(imported))
                    + (errors > 0 ? ` (${errors} failed)` : ''),
                'success'
            );
            await loadStats();
            await loadImages();
        } else {
            showToast(appT('gallery.importDroppedFailed', 'No images could be imported'), 'warning');
        }
    } catch (error) {
        showToast(formatUserError(error, appT('gallery.importDroppedError', 'Failed to import dropped images')), 'error');
    }
}

function _openScanWithPath(folderPath) {
    showModal('scan-modal');
    const input = document.getElementById('scan-folder-path');
    if (input) {
        input.value = folderPath;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    showToast(appT('gallery.dropFolderDetected', 'Folder detected: {path}').replace('{path}', folderPath), 'info');
}

function initGlobalKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Only handle when not in input/textarea
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            return;
        }

        // Only in gallery view
        if (AppState.currentView !== 'gallery') {
            return;
        }

        // G - Toggle grid view
        if (e.key === 'g' || e.key === 'G') {
            e.preventDefault();
            setGalleryViewMode('grid');
            showToast(appT('gallery.viewGrid', 'Grid view'), 'info');
        }
        // L - Toggle large view
        else if (e.key === 'l' || e.key === 'L') {
            e.preventDefault();
            setGalleryViewMode('large');
            showToast(appT('gallery.viewLarge', 'Large view'), 'info');
        }
        // W - Toggle waterfall view
        else if (e.key === 'w' || e.key === 'W') {
            e.preventDefault();
            setGalleryViewMode('waterfall');
            showToast(appT('gallery.viewWaterfall', 'Waterfall view'), 'info');
        }
        // F - Open filter modal
        else if (e.key === 'f' || e.key === 'F') {
            e.preventDefault();
            openFilterModal();
        }
        // R - Random image
        else if (e.key === 'r' || e.key === 'R') {
            e.preventDefault();
            showRandomImage();
        }
        // S - Toggle selection mode
        else if (e.key === 's' || e.key === 'S') {
            e.preventDefault();
            setSelectionMode(!AppState.selectionMode);
            showToast(
                AppState.selectionMode
                    ? appT('gallery.selectionModeOn', 'Selection mode ON')
                    : appT('gallery.selectionModeOff', 'Selection mode OFF'),
                'info'
            );
        }
        // Escape - Clear selection
        else if (e.key === 'Escape') {
            if (getSelectedGalleryCount() > 0) {
                e.preventDefault();
                clearSelectedIds({ scope: 'visible' });
                updateSelectionUI();
                emitSelectionStateChanged();
                if (window.Gallery && typeof Gallery.syncSelectionState === 'function') {
                    Gallery.syncSelectionState();
                }
                showToast(appT('gallery.selectionCleared', 'Selection cleared'), 'info');
            }
        }
        // Delete - Remove from gallery only; permanent disk delete stays behind the explicit dangerous button.
        else if (e.key === 'Delete') {
            if (getSelectedGalleryCount() > 0) {
                e.preventDefault();
                removeSelectedGalleryImages();
            }
        }
    });
}

