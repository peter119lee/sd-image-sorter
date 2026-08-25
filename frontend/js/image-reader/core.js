/**
 * image-reader/core.js — image-reader.js decomposition (family base; MUST LOAD FIRST).
 * Moved BYTE-IDENTICAL from frontend/js/image-reader.js pre-cut lines 1-6 +
 * 8-208 + 214-239 + 961-966 + 1650-1743 (of 1,749): the file header, the
 * IIFE's 'use strict' (kept as a file-level directive here — and added to
 * every other family file — so the moved code keeps its strict semantics),
 * `const ImageReader = {` + the state fields, init, the workspace tool tabs
 * (_bindWorkspaceTabs/_switchWorkspaceTool), _t, _formatLabel,
 * _updateFormatButton, _syncStaticLabels, _isReaderToolActive, _clear,
 * _setText/_escapeHtml/_formatSize, the object-literal `};` closer and the
 * window.ImageReader publish. Declares the ONE unsealed object every other
 * image-reader/*.js file Object.assign()s onto — this file must load before
 * the rest of the family; image-reader/boot.js invokes init() LAST. Only the
 * IIFE wrapper lines (pre-cut 7 `(function () {` and 1749 `})();`) were
 * dropped.
 */
/**
 * SD Image Sorter - Image Reader Tab
 * Drag & drop image to instantly view metadata without scanning to library.
 * Supports drag-replace (drop new image anytime), shows all hashes, LoRAs, and model info.
 */

    'use strict';

    const ImageReader = {
        _currentImage: null,
        _currentResult: null,
        _currentSourcePath: '',
        _currentOriginalSourcePath: '',
        _currentLibraryImageId: null,
        _currentReaderTags: [],
        _promptFormat: 'original', // 'original' | 'sd' | 'nai'
        _histogramMode: 'rgb',
        _languageBound: false,
        _currentSourceKind: 'file',
        _awaitingClipboardPaste: false,
        _eventsBound: false,
        _lastSuggestedOutputPath: '',
        _metadataEditorStorageKey: 'reader_metadata_editor_last_dir_v1',
        _collapsedState: {
            prompt: true,
            negative: false,
            characters: true,
            params: false,
            modelAssets: false,
            loras: false,
            hashes: false,
            categoryTags: true,
            editor: false,
        },

        init() {
            const dropZone = document.getElementById('reader-drop-zone');
            const fileInput = document.getElementById('reader-file-input');
            const container = document.getElementById('view-reader');
            if (!dropZone) return;

            if (this._eventsBound) {
                this._syncStaticLabels();
                return;
            }
            this._eventsBound = true;
            this._bindWorkspaceTabs();

            // Drop zone handlers
            this._setupDropZone(dropZone, fileInput);

            // Allow drag-replace: the ENTIRE reader view accepts drops
            if (container) {
                container.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                });
                container.addEventListener('drop', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const files = e.dataTransfer?.files;
                    if (files?.length > 0) {
                        this._handleFile(files[0], { sourceKind: 'file' });
                    }
                });
            }

            // Copy buttons
            document.getElementById('reader-copy-prompt')?.addEventListener('click', () => this._copy('prompt'));
            document.getElementById('reader-copy-prompt-category')?.addEventListener('click', (event) => this._copyPromptCategory(event));
            document.getElementById('reader-copy-negative')?.addEventListener('click', () => this._copy('negative'));
            document.getElementById('reader-copy-params')?.addEventListener('click', () => this._copy('params'));
            document.getElementById('reader-copy-all')?.addEventListener('click', () => {
                this._copy('all');
                this._closeCopyMenu();
            });
            document.getElementById('reader-copy-sd')?.addEventListener('click', () => {
                this._copy('sd');
                this._closeCopyMenu();
            });
            document.getElementById('reader-clear')?.addEventListener('click', () => this._clear());
            document.getElementById('reader-toggle-format')?.addEventListener('click', () => this._toggleFormat());
            document.addEventListener('click', (event) => {
                const menu = document.getElementById('reader-copy-menu');
                if (menu?.hasAttribute('open') && !menu.contains(event.target)) {
                    menu.removeAttribute('open');
                }
            });

            // Paste button — stop propagation so clicking it doesn't also open the file picker
            const pasteBtn = document.getElementById('reader-paste-btn');
            if (pasteBtn) {
                pasteBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    this._handlePaste();
                });
            }

            // Global Ctrl+V listener — only acts when the reader view is active
            document.addEventListener('paste', (e) => {
                const readerView = document.getElementById('view-reader');
                if (!readerView || readerView.style.display === 'none') return;
                if (!this._isReaderToolActive()) return;

                const target = e.target;
                const tag = (target?.tagName || '').toLowerCase();
                if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;

                const items = e.clipboardData?.items;
                if (!items) {
                    if (this._awaitingClipboardPaste) {
                        this._setClipboardPasteState(false);
                        window.App?.showToast?.(this._t('reader.pasteNoImage', 'No image found in clipboard'), 'error');
                    }
                    return;
                }
                for (const item of items) {
                    if (item.kind === 'file' && item.type.startsWith('image/')) {
                        const file = item.getAsFile();
                        if (file) {
                            e.preventDefault();
                            const sourceKind = this._awaitingClipboardPaste ? 'clipboard-button' : 'clipboard-shortcut';
                            this._setClipboardPasteState(false);
                            this._handleFile(file, { sourceKind });
                            return;
                        }
                    }
                }

                if (this._awaitingClipboardPaste) {
                    e.preventDefault();
                    this._setClipboardPasteState(false);
                    window.App?.showToast?.(this._t('reader.pasteNoImage', 'No image found in clipboard'), 'error');
                }
            });
            document.querySelectorAll('[data-reader-histogram-mode]').forEach((button) => {
                button.addEventListener('click', () => {
                    this._histogramMode = button.dataset.readerHistogramMode || 'rgb';
                    document.querySelectorAll('[data-reader-histogram-mode]').forEach((node) => {
                        node.classList.toggle('active', node === button);
                    });
                    this._renderReaderColorDistribution();
                });
            });
            this._bindSectionToggles();
            this._bindMetadataEditor();

            this._syncStaticLabels();

            if (!this._languageBound) {
                document.addEventListener('languageChanged', () => {
                    this._syncStaticLabels();
                    if (this._currentResult) {
                        this._renderResult(this._currentResult, this._currentImage?.name || '', { resetFormat: false });
                        this._updateMetadataEditorFormatWarning();
                        this._updateMetadataEditorOutputHint();
                    }
                });
                this._languageBound = true;
            }
        },

        _bindWorkspaceTabs() {
            document.querySelectorAll('#view-reader .reader-tool-tab').forEach((tab) => {
                tab.addEventListener('click', () => {
                    const tool = tab.dataset.readerTool;
                    if (!tool) return;
                    this._switchWorkspaceTool(tool);
                });
            });
        },

        _switchWorkspaceTool(tool) {
            document.querySelectorAll('#view-reader .reader-tool-tab').forEach((tab) => {
                const active = tab.dataset.readerTool === tool;
                tab.classList.toggle('active', active);
                tab.setAttribute('aria-selected', String(active));
            });

            document.querySelectorAll('#view-reader .reader-tool-panel').forEach((panel) => {
                const active = panel.id === `reader-tool-panel-${tool}`;
                panel.classList.toggle('active', active);
                panel.hidden = !active;
            });

            // Title follows the active tool (mirrors the settings-modal title).
            // Swap BOTH data-i18n and textContent so I18n.applyToDOM keeps the
            // tool-appropriate heading on languageChanged instead of reverting
            // to the metadata title while the 隐私处理 tool is active.
            const titleEl = document.querySelector('#view-reader .reader-tools-title');
            const subEl = document.querySelector('#view-reader .reader-tools-subtitle');
            const titleKey = tool === 'obfuscation' ? 'reader.workspaceTitleObfuscation' : 'reader.workspaceTitle';
            const subKey = tool === 'obfuscation' ? 'reader.workspaceSubtitleObfuscation' : 'reader.workspaceSubtitle';
            if (titleEl) {
                titleEl.setAttribute('data-i18n', titleKey);
                titleEl.textContent = this._t(titleKey, titleEl.textContent);
            }
            if (subEl) {
                subEl.setAttribute('data-i18n', subKey);
                subEl.textContent = this._t(subKey, subEl.textContent);
            }
        },

        _t(key, fallback, params) {
            const v = window.I18n?.t?.(key, params); return (v && v !== key) ? v : fallback;
        },

        _formatLabel(format) {
            const labels = {
                original: this._t('reader.formatOriginal', 'Original'),
                sd: this._t('reader.formatSd', 'SD / A1111'),
                nai: this._t('reader.formatNai', 'NAI'),
            };
            return this._t('reader.formatLabel', `View: ${labels[format] || labels.original}`, {
                format: labels[format] || labels.original,
            });
        },

        _updateFormatButton() {
            const btn = document.getElementById('reader-toggle-format');
            if (btn) {
                btn.textContent = this._formatLabel(this._promptFormat);
            }
        },

        _syncStaticLabels() {
            this._updateFormatButton();
            const copySdBtn = document.getElementById('reader-copy-sd');
            if (copySdBtn) {
                copySdBtn.textContent = this._t('reader.copySd', 'Copy as SD Text');
            }
        },

        _isReaderToolActive() {
            const readerPanel = document.getElementById('reader-tool-panel-reader');
            if (!readerPanel) return true; // no tool tabs yet, assume reader is active
            return readerPanel.classList.contains('active');
        },

        _clear() {
            this._currentImage = null;
            this._currentResult = null;
            this._currentSourcePath = '';
            this._currentOriginalSourcePath = '';
            this._currentLibraryImageId = null;
            this._currentReaderTags = [];
            this._currentSourceKind = 'file';
            this._setClipboardPasteState(false);
            this._lastSuggestedOutputPath = '';

            const preview = document.getElementById('reader-image-preview');
            const dropZone = document.getElementById('reader-drop-zone');
            const resultPanel = document.getElementById('reader-result-panel');
            const statusEl = document.getElementById('reader-status');

            if (preview) {
                if (preview._blobUrl) URL.revokeObjectURL(preview._blobUrl);
                preview.src = '';
                preview.style.display = 'none';
            }
            if (dropZone) dropZone.style.display = '';
            if (resultPanel) resultPanel.style.display = 'none';
            // Back to the empty state: drop the has-image flag so the drop zone
            // re-centres and the (now empty) metadata column stops reserving space.
            const containerClear = (dropZone || preview)?.closest('.reader-container');
            if (containerClear) containerClear.classList.remove('reader-has-image');
            if (statusEl) {
                statusEl.textContent = '';
                statusEl.style.display = 'none';
            }
            const colorSection = document.getElementById('reader-color-section');
            const metadataEditor = document.getElementById('reader-metadata-editor');
            const metadataEditorBody = document.getElementById('reader-editor-body');
            const metadataWarning = document.getElementById('reader-edit-format-warning');
            const quickFacts = document.getElementById('reader-quick-facts');
            if (colorSection) colorSection.style.display = 'none';
            const modelAssetsSection = document.getElementById('reader-model-assets-section');
            const modelAssets = document.getElementById('reader-model-assets');
            if (modelAssetsSection) modelAssetsSection.style.display = 'none';
            if (modelAssets) modelAssets.innerHTML = '';
            if (quickFacts) {
                quickFacts.innerHTML = '';
                quickFacts.hidden = true;
            }
            if (metadataEditor) metadataEditor.hidden = true;
            if (metadataEditorBody) metadataEditorBody.style.display = 'none';
            if (metadataWarning) {
                metadataWarning.textContent = '';
                metadataWarning.hidden = true;
            }
            [
                'reader-edit-prompt',
                'reader-edit-negative',
                'reader-edit-seed',
                'reader-edit-model',
                'reader-edit-sampler',
                'reader-edit-steps',
                'reader-edit-cfg',
                'reader-edit-size',
                'reader-edit-loras',
                'reader-edit-output-path',
            ].forEach((id) => {
                const input = document.getElementById(id);
                if (input) input.value = '';
            });
            const formatSelect = document.getElementById('reader-edit-format');
            if (formatSelect) formatSelect.value = 'png';
            this._updateFormatButton();
        },

        _setText(id, text) {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        },

        _escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        },

        _formatSize(bytes) {
            if (!bytes) return '';
            const units = ['B', 'KB', 'MB', 'GB'];
            let i = 0;
            let size = bytes;
            while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
            return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
        }
    };

    window.ImageReader = ImageReader;

