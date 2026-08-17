/**
 * SD Image Sorter - Image Obfuscation Workspace
 *
 * Clipboard-first workflow:
 * - drag, browse, or paste images into queue
 * - encode/decode into previewable PNG results
 * - copy single result to clipboard when browser allows
 * - keep download as fallback, not the only path
 */

(function () {
    'use strict';

    const ImageObfuscator = {
        _queue: [],
        _processing: false,
        _languageBound: false,
        _eventsBound: false,
        _pasteShortcutBound: false,

        init() {
            const dropZone = document.getElementById('obfuscate-drop-zone');
            const fileInput = document.getElementById('obfuscate-file-input');
            if (!dropZone || !fileInput) return;

            if (this._eventsBound) {
                this._syncCompatModeUI();
                this._renderQueue();
                return;
            }
            this._eventsBound = true;
            this._bindDropZone(dropZone, fileInput);
            this._bindPasteShortcut();
            document.getElementById('obfuscate-compat-mode')?.addEventListener('change', () => {
                this._syncCompatModeUI();
                this._revealAdvancedSettingsForCaveat();
            });

            document.getElementById('obfuscate-btn-encode')?.addEventListener('click', () => this._processAll('encode'));
            document.getElementById('obfuscate-btn-decode')?.addEventListener('click', () => this._processAll('decode'));
            document.getElementById('obfuscate-btn-clear')?.addEventListener('click', () => this._clearQueue());

            // Settings toggle
            document.getElementById('obfuscate-settings-toggle')?.addEventListener('click', () => {
                const panel = document.getElementById('obfuscate-advanced-settings');
                if (panel) panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
            });

            // Preview modal
            document.getElementById('obfuscate-preview-close')?.addEventListener('click', () => this._closePreview());
            document.getElementById('obfuscate-preview-modal')?.querySelector('.modal-backdrop')?.addEventListener('click', () => this._closePreview());
            document.getElementById('obfuscate-preview-copy')?.addEventListener('click', () => this._copyPreviewItem());
            document.getElementById('obfuscate-preview-download')?.addEventListener('click', () => this._downloadPreviewItem());

            // Batch download
            document.getElementById('obfuscate-btn-download-all')?.addEventListener('click', () => this._downloadAllAsZip());

            if (!this._languageBound) {
                document.addEventListener('languageChanged', () => {
                    this._syncCompatModeUI();
                    this._renderQueue();
                });
                this._languageBound = true;
            }

            this._syncCompatModeUI();
            this._renderQueue();
        },

        _t(key, fallback, params) {
            const v = window.I18n?.t?.(key, params); return (v && v !== key) ? v : fallback;
        },

        _getBaseName(item) {
            if (!item) return 'image';
            const edited = String(item.editedName || '').trim();
            if (edited) return edited;
            return String(item.name || 'image').replace(/\.[^.]+$/, '') || 'image';
        },

        _getResultName(item, extension) {
            return `${this._getBaseName(item)}${extension}`;
        },

        _bindDropZone(dropZone, fileInput) {
            const onDragOver = (event) => {
                event.preventDefault();
                dropZone.classList.add('drag-over');
            };
            const onDragLeave = (event) => {
                event.preventDefault();
                dropZone.classList.remove('drag-over');
            };
            const onDrop = (event) => {
                event.preventDefault();
                dropZone.classList.remove('drag-over');
                const files = Array.from(event.dataTransfer?.files || []);
                if (files.length) this._addFiles(files);
            };

            dropZone.addEventListener('dragover', onDragOver);
            dropZone.addEventListener('dragleave', onDragLeave);
            dropZone.addEventListener('drop', onDrop);
            dropZone.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', (event) => {
                const files = Array.from(event.target.files || []);
                if (files.length) this._addFiles(files);
                event.target.value = '';
            });
        },

        _bindPasteShortcut() {
            if (this._pasteShortcutBound) return;
            this._pasteShortcutBound = true;
            document.addEventListener('paste', async (event) => {
                const readerPanel = document.getElementById('reader-tool-panel-obfuscation');
                const readerView = document.getElementById('view-reader');
                if (!readerPanel || !readerView) return;
                const panelActive = readerPanel.classList.contains('active');
                const readerActive = readerView.classList.contains('active');
                if (!panelActive || !readerActive) return;

                const clipboardItems = Array.from(event.clipboardData?.items || []);
                const imageFiles = clipboardItems
                    .filter((item) => item.type.startsWith('image/'))
                    .map((item) => item.getAsFile())
                    .filter(Boolean);

                if (!imageFiles.length) return;
                event.preventDefault();
                this._addFiles(imageFiles);
            });
        },

        _statusLabel(status) {
            const labels = {
                pending: this._t('tools.statusPending', 'Pending'),
                processing: this._t('tools.statusProcessing', 'Processing'),
                done: this._t('tools.statusDone', 'Done'),
                error: this._t('tools.statusError', 'Error'),
            };
            return labels[status] || status;
        },

        _actionLabel(action) {
            const labels = {
                encode: this._t('tools.encode', 'Protect'),
                decode: this._t('tools.decode', 'Restore'),
            };
            return labels[action] || action;
        },

        _compatModeLabel(compatMode) {
            const labels = {
                big_tomato: this._t('tools.compatBigTomatoShort', 'Big Tomato'),
                small_tomato: this._t('tools.compatSmallTomatoShort', 'Small Tomato'),
            };
            return labels[compatMode] || compatMode;
        },

        _getCompatMode() {
            return document.getElementById('obfuscate-compat-mode')?.value || 'big_tomato';
        },

        _syncCompatModeUI() {
            const compatMode = this._getCompatMode();
            const isSmallTomato = compatMode === 'small_tomato';
            const passwordInput = document.getElementById('obfuscate-password');
            const legacyRow = document.getElementById('obfuscate-legacy-row');
            const metadataRow = document.getElementById('obfuscate-metadata-row');
            const legacyInput = document.getElementById('obfuscate-use-legacy-pnginfo');
            const metadataInput = document.getElementById('obfuscate-preserve-metadata');
            const compatHelp = document.getElementById('obfuscate-compat-help');

            if (compatHelp) {
                // The markup carries data-i18n="tools.compatBigTomatoHelp", and
                // ui-refresh's MutationObserver re-applies it on any DOM change
                // inside #app, so the Simple-mode help was silently reverting to
                // the Standard-mode description moments after being set. Claim
                // the dynamic-text lock both i18n.applyToDOM and ui-refresh
                // honour, so the line always describes the selected mode.
                compatHelp.dataset.i18nLocked = '1';
                compatHelp.textContent = isSmallTomato
                    ? this._t(
                        // The old fallback claimed no PNG Info was kept, which stopped
                        // being true once the mode gate came off; the locale pack owns
                        // the shipped wording, this is only the no-i18n fallback.
                        'tools.compatSmallTomatoHelp',
                        'Best when you just want a quick share-safe version. No password.'
                    )
                    : this._t(
                        'tools.compatBigTomatoHelp',
                        'Best when you want password support and to keep image info.'
                    );
            }

            if (passwordInput) {
                passwordInput.disabled = isSmallTomato;
                passwordInput.style.display = isSmallTomato ? 'none' : '';
                passwordInput.placeholder = isSmallTomato
                    ? this._t('tools.passwordNotUsed', 'Not used in Simple mode')
                    : this._t('tools.password', 'Password (optional)');
                // Parity finding (2026-07-07): the reference site breaks on
                // non-4-digit passwords (its parseInt yields NaN) — steer
                // users to the cross-site-safe form via the tooltip.
                passwordInput.title = isSmallTomato
                    ? ''
                    : this._t('tools.bigTomatoPasswordHint', 'Leave empty for a fixed scramble. For decoding on the site too, use a 4-digit numeric password (e.g. 0512) — other formats break on the site side.');
            }

            // Protect and restore both emit a PNG, and a PNG can always carry
            // tEXt, so metadata now survives in Simple mode too (backend commit
            // 8982d08 removed the same gate server-side). Nothing here is
            // disabled by compat mode any more; the one path that genuinely
            // cannot carry it is the Simple-mode .jpg download, and
            // _renderMetadataDownloadNote says so next to the checkbox.
            if (legacyInput) legacyInput.disabled = false;
            if (metadataInput) metadataInput.disabled = false;
            legacyRow?.classList.remove('is-disabled');
            metadataRow?.classList.remove('is-disabled');
            this._renderMetadataDownloadNote(isSmallTomato);
        },

        _renderMetadataDownloadNote(isSmallTomato) {
            const metadataRow = document.getElementById('obfuscate-metadata-row');
            if (!metadataRow) return;

            let note = document.getElementById('obfuscate-metadata-download-note');
            if (!note) {
                note = document.createElement('p');
                note.id = 'obfuscate-metadata-download-note';
                note.className = 'reader-editor-warning';
                metadataRow.insertAdjacentElement('afterend', note);
            }

            note.textContent = this._t(
                'tools.smallTomatoMetadataDownloadNote',
                'Simple mode keeps image info in the protected and restored PNG, but its download is re-encoded to .jpg for site compatibility and JPEG cannot hold PNG Info. Copy or drag the result, or use Standard mode, when the receiver needs the info.'
            );
            note.hidden = !isSmallTomato;
        },

        _revealAdvancedSettingsForCaveat() {
            // The note above lives in Advanced settings, collapsed by default, so
            // picking Simple mode would otherwise hide the download limitation
            // until after the user had already downloaded and shared a copy.
            if (this._getCompatMode() !== 'small_tomato') return;
            const panel = document.getElementById('obfuscate-advanced-settings');
            if (panel && panel.style.display === 'none') panel.style.display = 'flex';
        },

        _addFiles(files) {
            const imageFiles = files.filter((file) => file && String(file.type || '').startsWith('image/'));
            if (!imageFiles.length) {
                window.App?.showToast?.(this._t('tools.noImageFiles', 'No image files found'), 'error');
                return;
            }

            for (const file of imageFiles) {
                this._queue.push({
                    file,
                    name: file.name,
                    status: 'pending',
                    mode: null,
                    compatMode: null,
                    previewUrl: URL.createObjectURL(file),
                    resultBlob: null,
                    resultUrl: '',
                    resultName: '',
                });
            }

            this._renderQueue();
            window.App?.showToast?.(
                this._t('tools.addedImages', `Added ${imageFiles.length} image(s)`, { count: imageFiles.length }),
                'success'
            );
        },

        _renderQueue() {
            const container = document.getElementById('obfuscate-queue');
            const countEl = document.getElementById('obfuscate-count');
            if (!container) return;

            if (countEl) {
                countEl.textContent = this._queue.length
                    ? this._t('tools.imageCount', `${this._queue.length} image(s)`, { count: this._queue.length })
                    : '';
            }

            if (!this._queue.length) {
                container.innerHTML = `<div class="obfuscate-empty">${this._t('tools.noImages', 'Drop images here or click to browse')}</div>`;
                return;
            }

            container.innerHTML = this._queue.map((item, index) => {
                const hasResult = Boolean(item.resultUrl);
                const copyDisabled = hasResult ? '' : 'disabled';
                const downloadDisabled = hasResult ? '' : 'disabled';
                const statusParts = [this._statusLabel(item.status)];
                if (item.mode) statusParts.push(this._actionLabel(item.mode));
                if (item.compatMode) statusParts.push(this._compatModeLabel(item.compatMode));
                const copyTitle = hasResult
                    ? this._t('tools.copyImage', 'Copy image')
                    : this._t('tools.processFirst', 'Process this image first');
                const downloadTitle = hasResult
                    ? this._t('tools.downloadImage', 'Download image')
                    : this._t('tools.processFirst', 'Process this image first');

                const resultThumb = hasResult
                    ? `<span class="obfuscate-thumb-arrow">→</span><img src="${item.resultUrl}" class="obfuscate-thumb result-thumb" alt="result" draggable="true">`
                    : '';

                const displayName = this._escapeHtml(this._getBaseName(item));
                const editTitle = this._t('tools.editFilename', 'Edit filename');

                return `
                    <div class="obfuscate-item ${item.status}" data-index="${index}">
                        <div class="obfuscate-thumb-pair">
                            <img src="${item.previewUrl}" class="obfuscate-thumb" alt="source">
                            ${resultThumb}
                        </div>
                        <div class="obfuscate-item-info">
                            <input type="text" class="obfuscate-item-name-input" data-index="${index}" value="${displayName}" title="${this._escapeHtml(editTitle)}" aria-label="${this._escapeHtml(editTitle)}">
                            <span class="obfuscate-item-status">${this._escapeHtml(statusParts.join(' · '))}</span>
                        </div>
                        <div class="obfuscate-item-actions">
                            <button class="btn-icon obfuscate-copy" data-index="${index}" ${copyDisabled} title="${this._escapeHtml(copyTitle)}" aria-label="${this._escapeHtml(copyTitle)}"><svg class="icon" aria-hidden="true"><use href="#i-clipboard"/></svg></button>
                            <button class="btn-icon obfuscate-download" data-index="${index}" ${downloadDisabled} title="${this._escapeHtml(downloadTitle)}" aria-label="${this._escapeHtml(downloadTitle)}"><svg class="icon" aria-hidden="true"><use href="#i-download"/></svg></button>
                            <button class="btn-icon obfuscate-remove" data-index="${index}" title="${this._escapeHtml(this._t('tools.removeItem', 'Remove'))}" aria-label="${this._escapeHtml(this._t('tools.removeItem', 'Remove'))}"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button>
                        </div>
                    </div>
                `;
            }).join('');

            container.querySelectorAll('.obfuscate-remove').forEach((button) => {
                button.addEventListener('click', (event) => {
                    const index = Number.parseInt(event.currentTarget.dataset.index, 10);
                    this._removeItem(index);
                });
            });

            container.querySelectorAll('.obfuscate-download').forEach((button) => {
                button.addEventListener('click', async (event) => {
                    const index = Number.parseInt(event.currentTarget.dataset.index, 10);
                    await this._downloadItem(index);
                });
            });

            container.querySelectorAll('.obfuscate-copy').forEach((button) => {
                button.addEventListener('click', async (event) => {
                    const index = Number.parseInt(event.currentTarget.dataset.index, 10);
                    await this._copyItem(index);
                });
            });

            // Click thumbnails to open large preview
            container.querySelectorAll('.obfuscate-thumb').forEach((thumb) => {
                thumb.addEventListener('click', (event) => {
                    const src = event.currentTarget.src;
                    const index = Number.parseInt(event.currentTarget.closest('.obfuscate-item')?.dataset?.index, 10);
                    if (src) this._openPreview(src, index);
                });
            });

            // Editable filename
            container.querySelectorAll('.obfuscate-item-name-input').forEach((input) => {
                input.addEventListener('change', (event) => {
                    const index = Number.parseInt(event.currentTarget.dataset.index, 10);
                    const item = this._queue[index];
                    if (item) {
                        const newName = event.currentTarget.value.trim();
                        item.editedName = newName || null;
                        if (item.resultName && newName) {
                            item.resultName = this._getResultName(item, '.png');
                        }
                    }
                });
                // Prevent arrow keys from propagating to gallery navigation
                input.addEventListener('keydown', (event) => event.stopPropagation());
            });

            // Drag result images for direct sharing
            container.querySelectorAll('.result-thumb[draggable="true"]').forEach((thumb) => {
                thumb.addEventListener('dragstart', (event) => {
                    const index = Number.parseInt(thumb.closest('.obfuscate-item')?.dataset?.index, 10);
                    const item = this._queue[index];
                    if (item?.resultBlob) {
                        const name = item.resultName || this._getResultName(item, '.png');
                        const file = new File([item.resultBlob], name, { type: 'image/png' });
                        event.dataTransfer.setData('text/plain', name);
                        event.dataTransfer.items.add(file);
                    }
                });
            });

            // Show/hide batch download bar
            const hasResults = this._queue.some((item) => item.resultUrl);
            const batchBar = document.getElementById('obfuscate-batch-bar');
            if (batchBar) batchBar.style.display = hasResults ? 'flex' : 'none';
        },

        _removeItem(index) {
            const item = this._queue[index];
            if (!item) return;
            if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
            if (item.resultUrl) URL.revokeObjectURL(item.resultUrl);
            this._queue.splice(index, 1);
            this._renderQueue();
        },

        async _copyItem(index) {
            const item = this._queue[index];
            if (!item?.resultBlob) return;

            if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
                window.App?.showToast?.(this._t('tools.copyUnsupported', 'This browser cannot copy images directly. Use Download instead.'), 'warning');
                return;
            }

            try {
                await navigator.clipboard.write([
                    new ClipboardItem({
                        'image/png': item.resultBlob,
                    }),
                ]);
                window.App?.showToast?.(this._t('tools.copySuccess', 'Image copied to clipboard'), 'success');
            } catch (error) {
                window.App?.showToast?.(this._t('tools.copyFailed', 'Failed to copy image'), 'error');
            }
        },

        async _downloadItem(index) {
            const item = this._queue[index];
            if (!item?.resultUrl) return;

            if (item.compatMode === 'small_tomato') {
                try {
                    const jpegBlob = await this._createJpegBlob(item.resultBlob || item.resultUrl);
                    const jpegUrl = URL.createObjectURL(jpegBlob);
                    this._triggerDownload(jpegUrl, this._getResultName(item, '.jpg'));
                    setTimeout(() => URL.revokeObjectURL(jpegUrl), 1000);
                    return;
                } catch (error) {
                    // If browser-side JPEG conversion fails, keep the PNG fallback.
                }
            }

            this._triggerDownload(item.resultUrl, item.resultName || this._getResultName(item, '.png'));
        },

        _triggerDownload(url, filename) {
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        },

        _replaceExtension(filename, nextExtension) {
            const stem = String(filename || 'image').replace(/\.[^.]+$/, '');
            return `${stem}${nextExtension}`;
        },

        async _createJpegBlob(source) {
            const objectUrl = source instanceof Blob ? URL.createObjectURL(source) : String(source || '');
            const shouldRevoke = source instanceof Blob;
            try {
                const image = await new Promise((resolve, reject) => {
                    const element = new Image();
                    element.onload = () => resolve(element);
                    element.onerror = () => reject(new Error('failed to load result image'));
                    element.src = objectUrl;
                });

                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                const context = canvas.getContext('2d');
                if (!context) {
                    throw new Error('missing canvas context');
                }

                context.drawImage(image, 0, 0);
                return await new Promise((resolve, reject) => {
                    canvas.toBlob((blob) => {
                        if (blob) resolve(blob);
                        else reject(new Error('failed to create jpeg blob'));
                    }, 'image/jpeg', 1);
                });
            } finally {
                if (shouldRevoke) {
                    URL.revokeObjectURL(objectUrl);
                }
            }
        },

        _resolveDownloadName(response, itemName, mode) {
            const header = response.headers.get('Content-Disposition') || '';
            const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
            if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);

            const simpleMatch = header.match(/filename="?([^";]+)"?/i);
            if (simpleMatch?.[1]) return simpleMatch[1];

            const stem = itemName.replace(/\.[^.]+$/, '');
            return `${mode === 'encode' ? 'encoded' : 'decoded'}_${stem}.png`;
        },

        _createProcessingFile(item) {
            if (!item?.resultBlob) return item?.file || null;
            const resultName = item.resultName || `${item.name.replace(/\.[^.]+$/, '')}.png`;
            const resultType = item.resultBlob.type || 'image/png';
            return new File([item.resultBlob], resultName, { type: resultType });
        },

        async _processAll(mode) {
            if (!this._queue.length) {
                window.App?.showToast?.(this._t('tools.noQueue', 'No images in queue'), 'error');
                return;
            }
            if (this._processing) return;

            const compatMode = this._getCompatMode();
            const password = document.getElementById('obfuscate-password')?.value || '';
            const preserveMetadata = document.getElementById('obfuscate-preserve-metadata')?.checked ?? true;
            const legacyPngInfo = document.getElementById('obfuscate-use-legacy-pnginfo')?.checked ?? false;
            const progressEl = document.getElementById('obfuscate-progress');

            // Always use client-side engine for pixel scrambling (instant).
            // Only fall back to backend if engine is missing.
            const hasEngine = Boolean(window.ObfuscateEngine);

            this._processing = true;
            let completed = 0;

            try {
                for (const item of this._queue) {
                    item.status = 'processing';
                    item.mode = mode;
                    item.compatMode = compatMode;
                    this._renderQueue();

                    try {
                        // Determine source URL for processing
                        const sourceBlob = item.resultBlob || item.file;

                        if (hasEngine) {
                            const engineFn = mode === 'encode'
                                ? window.ObfuscateEngine.encode
                                : window.ObfuscateEngine.decode;
                            const result = await engineFn(sourceBlob, password, {
                                compatMode,
                                preserveMetadata,
                                legacyPngInfo,
                            });

                            if (item.resultUrl) URL.revokeObjectURL(item.resultUrl);
                            item.resultBlob = result.blob;
                            item.resultUrl = result.url;
                            item.resultName = this._getResultName(item, '.png');
                        } else {
                            // ── Backend fallback (only if engine unavailable) ──
                            const processingFile = this._createProcessingFile(item);
                            if (!processingFile) {
                                throw new Error(this._t('tools.processingFailed', 'Processing failed'));
                            }

                            const formData = new FormData();
                            formData.append('file', processingFile);
                            formData.append('password', password);
                            formData.append('mode', mode);
                            formData.append('preserve_metadata', String(preserveMetadata));
                            formData.append('legacy_pnginfo', String(legacyPngInfo));
                            formData.append('compat_mode', compatMode);

                            const response = await fetch('/api/obfuscate/preview', {
                                method: 'POST',
                                body: formData,
                            });

                            if (!response.ok) {
                                const err = await response.json().catch(() => ({}));
                                throw new Error(err.detail || err.error || this._t('tools.processingFailed', 'Processing failed'));
                            }

                            const blob = await response.blob();
                            if (item.resultUrl) URL.revokeObjectURL(item.resultUrl);
                            item.resultBlob = blob;
                            item.resultUrl = URL.createObjectURL(blob);
                            item.resultName = this._resolveDownloadName(response, item.name, mode);
                        }

                        item.status = 'done';
                        completed += 1;
                    } catch (error) {
                        item.status = 'error';
                        window.App?.showToast?.(String(error.message || this._t('tools.processingFailed', 'Processing failed')), 'error');
                    }

                    this._renderQueue();
                    if (progressEl) {
                        progressEl.textContent = this._t(
                            mode === 'encode' ? 'tools.encodeProgress' : 'tools.decodeProgress',
                            `${completed}/${this._queue.length}`,
                            { current: completed, total: this._queue.length }
                        );
                    }
                }

                window.App?.showToast?.(
                    this._t(
                        mode === 'encode' ? 'tools.encodeSummary' : 'tools.decodeSummary',
                        `${mode === 'encode' ? 'Protected' : 'Restored'} ${completed}/${this._queue.length} images`,
                        { current: completed, total: this._queue.length }
                    ),
                    completed > 0 ? 'success' : 'warning'
                );
            } finally {
                this._processing = false;
            }
        },

        _clearQueue() {
            this._queue.forEach((item) => {
                if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
                if (item.resultUrl) URL.revokeObjectURL(item.resultUrl);
            });
            this._queue = [];
            const progressEl = document.getElementById('obfuscate-progress');
            if (progressEl) progressEl.textContent = '';
            this._renderQueue();
        },

        _escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = String(text || '');
            return div.innerHTML;
        },

        // ── Preview Modal ─────────────────────────────────────────
        _previewIndex: -1,

        _openPreview(src, index) {
            const modal = document.getElementById('obfuscate-preview-modal');
            const img = document.getElementById('obfuscate-preview-img');
            if (!modal || !img) return;
            img.src = src;
            this._previewIndex = Number.isFinite(index) ? index : -1;
            modal.classList.add('visible');
        },

        _closePreview() {
            const modal = document.getElementById('obfuscate-preview-modal');
            if (modal) modal.classList.remove('visible');
        },

        async _copyPreviewItem() {
            if (this._previewIndex >= 0) {
                await this._copyItem(this._previewIndex);
            }
        },

        _downloadPreviewItem() {
            if (this._previewIndex >= 0) {
                this._downloadItem(this._previewIndex);
            }
        },

        // ── Batch Download as ZIP ─────────────────────────────────
        async _downloadAllAsZip() {
            const doneItems = this._queue.filter((item) => item.resultBlob);
            if (!doneItems.length) {
                window.App?.showToast?.(this._t('tools.noResults', 'No processed results to download'), 'warning');
                return;
            }

            try {
                // Simple ZIP creation (store method, no compression — images are already compressed)
                const files = [];
                for (const item of doneItems) {
                    let name = item.resultName || this._getResultName(item, '.png');
                    let blob = item.resultBlob;
                    if (item.compatMode === 'small_tomato') {
                        blob = await this._createJpegBlob(blob);
                        name = this._getResultName(item, '.jpg');
                    }
                    const data = new Uint8Array(await blob.arrayBuffer());
                    files.push({ name, data });
                }

                const zipBlob = this._createZipBlob(files);
                const url = URL.createObjectURL(zipBlob);
                this._triggerDownload(url, `obfuscated_${Date.now()}.zip`);
                setTimeout(() => URL.revokeObjectURL(url), 2000);
                window.App?.showToast?.(this._t('tools.downloadAllSuccess', `Downloaded ${files.length} images as ZIP`), 'success');
            } catch (error) {
                window.App?.showToast?.(this._t('tools.downloadAllFailed', 'Failed to create ZIP'), 'error');
            }
        },

        _createZipBlob(files) {
            // Minimal ZIP implementation (store method, no compression)
            const localHeaders = [];
            const centralHeaders = [];
            let offset = 0;

            for (const file of files) {
                const nameBytes = new TextEncoder().encode(file.name);
                const crc = this._crc32(file.data);
                const size = file.data.length;

                // Local file header
                const local = new Uint8Array(30 + nameBytes.length + size);
                const lv = new DataView(local.buffer);
                lv.setUint32(0, 0x04034b50, true); // signature
                lv.setUint16(4, 20, true); // version needed
                lv.setUint16(6, 0, true); // flags
                lv.setUint16(8, 0, true); // compression (store)
                lv.setUint16(10, 0, true); // mod time
                lv.setUint16(12, 0, true); // mod date
                lv.setUint32(14, crc, true);
                lv.setUint32(18, size, true); // compressed size
                lv.setUint32(22, size, true); // uncompressed size
                lv.setUint16(26, nameBytes.length, true);
                lv.setUint16(28, 0, true); // extra length
                local.set(nameBytes, 30);
                local.set(file.data, 30 + nameBytes.length);
                localHeaders.push(local);

                // Central directory header
                const central = new Uint8Array(46 + nameBytes.length);
                const cv = new DataView(central.buffer);
                cv.setUint32(0, 0x02014b50, true);
                cv.setUint16(4, 20, true); // version made by
                cv.setUint16(6, 20, true); // version needed
                cv.setUint16(8, 0, true); // flags
                cv.setUint16(10, 0, true); // compression
                cv.setUint16(12, 0, true); // mod time
                cv.setUint16(14, 0, true); // mod date
                cv.setUint32(16, crc, true);
                cv.setUint32(20, size, true);
                cv.setUint32(24, size, true);
                cv.setUint16(28, nameBytes.length, true);
                cv.setUint16(30, 0, true); // extra length
                cv.setUint16(32, 0, true); // comment length
                cv.setUint16(34, 0, true); // disk start
                cv.setUint16(36, 0, true); // internal attrs
                cv.setUint32(38, 0, true); // external attrs
                cv.setUint32(42, offset, true); // local header offset
                central.set(nameBytes, 46);
                centralHeaders.push(central);

                offset += local.length;
            }

            const centralSize = centralHeaders.reduce((s, c) => s + c.length, 0);
            const endRecord = new Uint8Array(22);
            const ev = new DataView(endRecord.buffer);
            ev.setUint32(0, 0x06054b50, true);
            ev.setUint16(4, 0, true); // disk number
            ev.setUint16(6, 0, true); // disk with central dir
            ev.setUint16(8, files.length, true);
            ev.setUint16(10, files.length, true);
            ev.setUint32(12, centralSize, true);
            ev.setUint32(16, offset, true);
            ev.setUint16(20, 0, true); // comment length

            return new Blob([...localHeaders, ...centralHeaders, endRecord], { type: 'application/zip' });
        },

        _crc32(data) {
            let crc = 0xFFFFFFFF;
            for (let i = 0; i < data.length; i++) {
                crc ^= data[i];
                for (let j = 0; j < 8; j++) {
                    crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
                }
            }
            return (crc ^ 0xFFFFFFFF) >>> 0;
        },
    };

    window.ImageObfuscator = ImageObfuscator;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => ImageObfuscator.init());
    } else {
        ImageObfuscator.init();
    }
})();
