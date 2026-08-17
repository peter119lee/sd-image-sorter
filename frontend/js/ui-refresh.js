(function () {
    'use strict';

    var UIRefresh = {
        _observer: null,
        _applyScheduled: false,
        _applying: false,
        _observerSuspended: false,
        _observerResumeHandle: null,

        _t: function (key, params, fallback) {
            if (window.I18n && typeof window.I18n.t === 'function') {
                var translated = window.I18n.t(key, params);
                if (translated && translated !== key) {
                    return translated;
                }
            }
            return fallback || key;
        },

        _escape: function (value) {
            return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },

        _setText: function (selector, key, fallback) {
            var el = document.querySelector(selector);
            if (!el) return;
            if (el.dataset && el.dataset.i18nLocked === '1') return;
            el.textContent = this._t(key, null, fallback);
        },

        _setStaticText: function (selector, key, fallback) {
            var el = document.querySelector(selector);
            if (!el || !el.hasAttribute('data-i18n')) return;
            el.textContent = this._t(key, null, fallback);
        },

        _setTextAll: function (selector, keys) {
            var nodes = document.querySelectorAll(selector);
            for (var i = 0; i < nodes.length && i < keys.length; i++) {
                if (nodes[i].dataset && nodes[i].dataset.i18nLocked === '1') continue;
                nodes[i].textContent = this._t(keys[i]);
            }
        },

        _setAttr: function (selector, attr, key, fallback) {
            var el = document.querySelector(selector);
            if (!el) return;
            el.setAttribute(attr, this._t(key, null, fallback));
        },

        _setPlaceholder: function (selector, key) {
            var el = document.querySelector(selector);
            if (!el) return;
            el.placeholder = this._t(key);
        },

        /**
         * Render a button icon.
         *
         * An "i-*" value is a Graphite sprite symbol and is emitted as real
         * SVG markup. Anything else keeps the old escaped-text behaviour, so
         * callers outside this file cannot inject HTML through it.
         *
         * This used to escape unconditionally, which meant every language
         * switch rewrote the button contents and replaced the sprite icons
         * with the emoji that were hard-coded at the call sites.
         */
        _iconHtml: function (icon) {
            if (!icon) return '';
            if (/^i-[a-z0-9-]+$/.test(icon)) {
                return '<svg class="icon" aria-hidden="true"><use href="#' + icon + '"/></svg>';
            }
            return '<span aria-hidden="true">' + this._escape(icon) + '</span>';
        },

        _setButton: function (selector, key, icon, titleKey) {
            var el = document.querySelector(selector);
            if (!el) return;
            // Respect the dynamic-text lock used by callers that need to
            // override a button's label at runtime (e.g. the bulk-download
            // confirmation OK button shows "Download N model(s) (~X GB)"
            // and must NOT be reset to the static "modal.yes" key while
            // the dialog is open).
            if (el.dataset && el.dataset.i18nLocked === '1') return;
            var label = this._escape(this._t(key));
            el.innerHTML = this._iconHtml(icon) + '<span class="ui-label">' + label + '</span>';
            if (titleKey) {
                var text = this._t(titleKey);
                if (el.dataset.dynamicTitle !== 'true') {
                    el.title = text;
                }
                el.setAttribute('aria-label', text);
            }
        },

        _setCountButton: function (selector, key) {
            var button = document.querySelector(selector);
            if (!button) return;
            var count = button.querySelector('.gen-count');
            var countHtml = '';
            if (count) {
                countHtml = '<span class="gen-count" id="' + count.id + '">' + this._escape(count.textContent) + '</span>';
            }
            button.innerHTML = this._escape(this._t(key)) + ' ' + countHtml;
        },

        _setOptionText: function (selectSelector, optionMap) {
            var select = document.querySelector(selectSelector);
            if (!select) return;
            Object.keys(optionMap).forEach(function (value) {
                var option = select.querySelector('option[value="' + value + '"]');
                if (option) {
                    option.textContent = UIRefresh._t(optionMap[value]);
                }
            });
        },

        _setCheckboxTexts: function (selector, keys) {
            var nodes = document.querySelectorAll(selector + ' .checkbox-text');
            for (var i = 0; i < nodes.length && i < keys.length; i++) {
                nodes[i].textContent = this._t(keys[i]);
            }
        },

        _setSummaryStrongs: function (selector, keys) {
            var nodes = document.querySelectorAll(selector + ' strong');
            for (var i = 0; i < nodes.length && i < keys.length; i++) {
                nodes[i].textContent = this._t(keys[i]) + ':';
            }
        },

        _setViewToggle: function (selector, key) {
            var button = document.querySelector(selector);
            if (!button) return;
            button.textContent = this._t(key);
        },

        _setToggleHeader: function (selector, key) {
            var el = document.querySelector(selector);
            if (!el) return;

            // Label-span structure (same pattern as the reader sections):
            // only the label carries text, so the collapse icon survives.
            var label = el.querySelector('.section-toggle-label');
            if (label) {
                label.textContent = this._t(key);
                return;
            }

            var icon = el.querySelector('.collapse-icon');
            if (!icon) {
                el.textContent = this._t(key);
                return;
            }

            el.innerHTML = this._escape(this._t(key)) + ' <span class="collapse-icon">' + this._escape(icon.textContent || '▼') + '</span>';
        },

        _translateGallery: function () {
            this._setCountButton('#generator-tabs .gen-tab[data-gen="all"]', 'generator.all');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="nai"]', 'generator.nai');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="comfyui"]', 'generator.comfyui');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="forge"]', 'generator.forge');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="webui"]', 'generator.webui');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="unknown"]', 'generator.unknown');
            this._setCountButton('#generator-tabs .gen-tab[data-gen="others"]', 'generator.others');

            this._setOptionText('#gallery-sort', {
                newest: 'sort.newest',
                oldest: 'sort.oldest',
                name_asc: 'sort.nameAsc',
                name_desc: 'sort.nameDesc',
                generator: 'sort.generator',
                prompt_length: 'sort.promptLength',
                tag_count: 'sort.tagCount',
                rating: 'sort.rating',
                character_count: 'sort.characterCount',
                file_size: 'sort.fileSize',
                file_size_asc: 'sort.fileSizeAsc',
                aesthetic: 'sort.aesthetic',
                brightness: 'sort.brightness',
                saturation: 'sort.saturation',
                brightness_skew: 'sort.brightnessSkew',
                random: 'sort.random'
            });

            // v3.2.2: ``_applyGalleryEmptyStateVariant`` (in app.js) flips the
            // empty state between two variants — the original "no images
            // yet" onboarding card, and a new "no images match your
            // filter" message. Respect the variant class instead of
            // hard-coding the no-images keys, otherwise this method
            // overwrites the variant-specific copy on every refresh.
            var emptyState = document.getElementById('gallery-empty-state');
            if (emptyState && emptyState.classList.contains('empty-state-no-matches')) {
                this._setText('#gallery-empty-state h3', 'gallery.noMatchesTitle');
                this._setText('#gallery-empty-state p', 'gallery.noMatchesHint');
            } else {
                this._setText('#gallery-empty-state h3', 'gallery.noImages');
                this._setText('#gallery-empty-state p', 'gallery.scanPrompt');
            }
            this._setButton('#empty-state-scan-btn', 'action.scan', 'i-folder', 'action.scan');
            // v3.2.2: also localize the new "Clear all filters" button
            this._setButton('#empty-state-clear-filters-btn', 'gallery.clearFilters', 'i-broom', 'gallery.clearFilters');
            this._setText('#load-more-btn', 'gallery.loadMore');
            this._setText('#gallery-loading span', 'gallery.loading');
            this._setSummaryStrongs('#autosep-filter-summary', [
                'summary.generators',
                'summary.tags',
                'summary.ratings',
                'summary.checkpoints',
                'summary.loras',
                'summary.prompts',
                'summary.search',
                'summary.dimensions'
            ]);
            this._setSummaryStrongs('#manual-sort-filter-summary', [
                'summary.generators',
                'summary.tags',
                'summary.ratings',
                'summary.checkpoints',
                'summary.loras',
                'summary.prompts',
                'summary.search',
                'summary.dimensions'
            ]);
            // Gallery filter-summary labels are translated declaratively via
            // their own `data-i18n` attributes (see index.html #filter-summary).
            // The previous positional `_setTextAll` over `.summary-label`
            // misaligned once the Colors row was inserted, so the data-i18n
            // path is now the single source of truth for these labels.
        },

        _translateAutoSeparate: function () {
            this._setText('#view-autosep .panel-title', 'autosep.title');
            this._setText('#view-autosep .panel-description', 'autosep.description');
            this._setText('#view-autosep .filter-header-compact h4', 'filter.criteria');
            this._setButton('#btn-autosep-filters', 'gallery.editFilters', 'i-search', 'gallery.editFilters');
            this._setText('#autosep-scope-note', 'autosep.scopeNote');
            this._setText('#view-autosep .filter-section:nth-of-type(2) h4', 'autosep.destination');
            this._setButton('#btn-browse-destination', 'common.browse', null, 'common.browse');
            this._setPlaceholder('#autosep-destination', 'modal.folderPath');
            this._setText('#view-autosep .preview-section h4', 'autosep.preview');
            this._setText('#autosep-preview .stat-label', 'common.images', 'images');
            this._setText('#autosep-preview-list .autosep-preview-empty', 'autosep.previewEmpty');
            this._setText('#view-autosep .autosep-preview-hint', 'autosep.previewHint');
            this._setButton('#btn-preview-autosep', 'autosep.previewBtn', null, 'autosep.previewBtn');
            this._setButton('#btn-execute-autosep', 'autosep.moveBtn', 'i-folder', 'autosep.moveBtn');
            window.updateAutoSepActionUi?.();
        },

        _translateManual: function () {
            this._setText('#manual-sort-mobile-warning h3', 'manual.keyboardRequired');
            this._setText('#manual-sort-mobile-warning p', 'manual.keyboardMsg');
            this._setButton('#return-to-gallery-btn', 'manual.returnToGallery');
            this._setText('#view-manual .setup-title', 'manual.title');
            this._setText('#view-manual .setup-description', 'manual.description');
            this._setText('#manual-sort-scope-note', 'manual.scopeNote');
            this._setText('#view-manual .space-indicator span', 'manual.skip');
            this._setText('#view-manual .filter-header-compact h4', 'filter.imagesToSort');
            this._setButton('#btn-manual-sort-filters', 'gallery.editFilters', 'i-search', 'gallery.editFilters');
            // v3.3.2 WB-S3: the start button reflects the selected Workbench mode
            // (A/B Showdown vs slot sort). Read the persisted mode so the
            // MutationObserver-driven re-apply keeps the right label.
            var startSortKey = 'manual.startSorting';
            try {
                var sortMode = localStorage.getItem('manual_sort_mode_v1');
                if (sortMode === 'bracket') startSortKey = 'manual.startShowdown';
                else if (sortMode === 'cull') startSortKey = 'manual.startCulling';
            } catch (e) { /* localStorage may be unavailable */ }
            this._setButton('#btn-start-sorting', startSortKey, 'i-dice', startSortKey);
            this._setText('#gallery-preview-bar .minimap-label', 'manual.minimap');
            this._setTextAll('.minimap-legend .legend-item', ['manual.current', 'manual.sorted', 'manual.pending']);
            this._setTextAll('.progress-stat-label', ['manual.sorted', 'manual.skipped', 'manual.progress', 'manual.remaining', 'manual.speed']);
            this._setText('.progress-hint', 'manual.progressHint');
        },

        _translateSimilar: function () {
            this._setText('#view-similar .similar-header h3', 'similar.title');
            // The embed-row primary is state-derived (build / continue / rebuild /
            // indexing…), so this re-apply must NOT hard-set it back to "build" —
            // that is what pinned it to 建立相似索引 even with a partial index.
            // Let SimilarImages relabel it; fall back only before it is live.
            var similar = window.SimilarImages;
            if (similar && typeof similar.syncEmbedButtonLabel === 'function') {
                var running = Boolean(similar.isEmbedding
                    || similar.isCheckingEmbeddingStatus
                    || (similar.embedProgress && similar.embedProgress.running));
                similar.syncEmbedButtonLabel(running);
            } else {
                this._setButton('#btn-similar-embed', 'similar.generateEmbed');
            }
            this._setText('#view-similar .similar-tab[data-target="panel-similar-search"]', 'similar.search');
            this._setText('#view-similar .similar-tab[data-target="panel-similar-duplicates"]', 'similar.duplicates');
            this._setPlaceholder('#similar-search-id', 'similar.searchById');
            this._setButton('#btn-similar-search', 'similar.searchById');
            this._setButton('#btn-similar-upload', 'similar.upload');
            this._setStaticText('#similar-results .empty-state', 'similar.searchEmpty');
            this._setText('#panel-similar-duplicates label', 'similar.threshold');
            this._setButton('#btn-similar-duplicates', 'similar.findDuplicates');
            this._setStaticText('#similar-duplicates .empty-state', 'similar.duplicatesEmpty');
        },

        _translatePromptLab: function () {
            this._setText('#view-promptlab .promptlab-browser-header h4', 'promptlab.categories');
            this._setPlaceholder('#promptlab-search', 'promptlab.searchTags');
            this._setText('#view-promptlab .tagset-selector label', 'promptlab.tagSet');
            this._setOptionText('#promptlab-set-select', { '': 'promptlab.selectTagSet' });
            this._setButton('#btn-promptlab-apply-tagset', 'promptlab.applyTagSet');
            this._setText('#view-promptlab .promptlab-admin-panel .guided-advanced-summary > span:first-child', 'promptlab.manageData');
            this._setText('#view-promptlab .promptlab-admin-panel .guided-advanced-hint', 'promptlab.manageDataHint');
            this._setTextAll('#view-promptlab .promptlab-admin-section h5', ['promptlab.recategorizeTitle', 'promptlab.customTagSets', 'promptlab.customExclusions']);
            this._setPlaceholder('#promptlab-recat-tag', 'promptlab.recategorizeTagPlaceholder');
            this._setPlaceholder('#promptlab-recat-category', 'promptlab.recategorizeCategoryPlaceholder');
            this._setButton('#btn-promptlab-recategorize', 'common.save');
            this._setText('#view-promptlab .promptlab-builder-header h4', 'promptlab.slots');
            this._setButton('#btn-promptlab-random', 'promptlab.randomize', 'i-dice', 'promptlab.randomize');
            this._setButton('#btn-promptlab-clear', 'promptlab.clear', 'i-trash', 'promptlab.clear');
            this._setText('#view-promptlab .promptlab-output-header h4', 'promptlab.output');
            this._setButton('#btn-promptlab-use-gallery', 'promptlab.findInGallery', 'i-search', 'promptlab.findInGallery');
            this._setButton('#btn-promptlab-generate', 'promptlab.generate');
            this._setButton('#btn-promptlab-copy', 'promptlab.copy', 'i-clipboard', 'promptlab.copy');
            this._setButton('#btn-promptlab-validate', 'promptlab.validate', 'i-check', 'promptlab.validate');
            this._setPlaceholder('#promptlab-output', 'promptlab.outputPlaceholder');
            this._setText('#view-promptlab .promptlab-presets-header h5', 'promptlab.presets');
            this._setButton('#btn-promptlab-save-preset', 'promptlab.savePreset', 'i-save', 'promptlab.savePreset');
            this._setStaticText('#promptlab-categories .empty-state', 'promptlab.loadingCategories');
            this._setStaticText('#promptlab-slots .empty-state', 'promptlab.loadingSlots');
            this._setStaticText('#promptlab-presets .preset-empty', 'promptlab.noPresets');
        },

        _translateArtist: function () {
            // Five disjoint buckets, in DOM order. loadStats() rebuilds these
            // cards from the same keys, so this re-apply must stay in step or
            // the observer relabels the wrong bucket a frame later.
            this._setTextAll('#artist-stats .stat-label', ['artist.totalImages', 'artist.confidentMatches', 'artist.unconfirmed', 'artist.noMatch', 'artist.artistsFound']);
            // The section headings are NOT set here. Each <h3> holds a sprite icon
            // plus its own `data-i18n` label span, so applyToDOM already
            // translates them; writing the heading's textContent would delete
            // the icon on every re-apply.
            this._setTextAll('#view-artist .control-section label:not(.artist-threshold-label)', ['artist.modelSource', 'artist.localModelPath']);
            this._setText('.artist-threshold-label [data-i18n="artist.confidenceThreshold"]', 'artist.confidenceThreshold');
            this._setOptionText('#artist-model-source', {
                huggingface: 'artist.huggingface',
                modelscope: 'artist.modelscope',
                local: 'artist.localModel'
            });
            this._setPlaceholder('#artist-model-path', 'artist.localModelPath');
            this._setText('#view-artist .control-section .helper-text', 'artist.belowThreshold');
            this._setButton('#btn-identify-all', 'artist.identifyAll', 'i-palette', 'artist.identifyAll');
            this._setButton('#btn-identify-selected', 'artist.identifySelected', 'i-target', 'artist.identifySelected');
            this._setButton('#btn-refresh-artist-stats', 'artist.refreshStats', 'i-refresh', 'artist.refreshStats');
            this._setButton('#btn-clear-artist-data', 'artist.clearPredictions', 'i-trash', 'artist.clearPredictions');
            this._setViewToggle('#view-artist .toggle-btn[data-view="grid"]', 'artist.grid');
            this._setViewToggle('#view-artist .toggle-btn[data-view="list"]', 'artist.list');
            this._setText('#artist-results-grid .empty-state p', 'artist.noArtists');
            this._setText('#artist-results-grid .empty-hint', 'artist.noArtistsHint');
            this._setText('#artist-detail-content .detail-placeholder', 'artist.selectArtist');
        },

        _translateImageModal: function () {
            this._setButton('#modal-prev-image', 'modal.prev', 'i-arrow-left', 'modal.prev');
            this._setButton('#modal-next-image', 'modal.next', 'i-arrow-right', 'modal.next');
            this._setButton('#btn-copy-prompt', 'modal.copyPrompt');
            this._setButton('#btn-copy-negative', 'modal.copyNegative');
            this._setButton('#btn-copy-tags', 'modal.copyTags');
            this._setButton('#btn-copy-params', 'modal.copyParams');
            this._setButton('#btn-copy-all', 'modal.copyAll');
            this._setButton('#btn-reparse-metadata', 'modal.reparse', 'i-refresh', 'modal.reparse');
            this._setTextAll('.modal-meta strong', ['modal.generator', 'modal.size', 'modal.checkpoint']);
            this._setText('#modal-loading-state', 'modal.loadingDetails');
            this._setText('#modal-img2img-badge', 'modal.img2img');
            this._setToggleHeader('#modal-loras-section h4', 'modal.loras');
            this._setToggleHeader('.modal-prompt h4', 'modal.prompt');
            this._setToggleHeader('#modal-negative-section h4', 'modal.negativePrompt');
            this._setText('#modal-characters-section h4', 'modal.characterPrompts');
            this._setToggleHeader('#modal-params-section h4', 'modal.genParams');
            this._setText('#modal-img2img-section h4', 'modal.img2imgDetails');
            this._setToggleHeader('#modal-nodes-section h4', 'modal.promptNodes');
            this._setToggleHeader('#modal-color-distribution h4', 'modal.colorDistribution');
            this._setText('.modal-tags-header h4', 'modal.tags');
        },

        _translateModalForms: function () {
            this._setText('#scan-modal-title', 'modal.scanFolder');
            this._setText('#scan-folder-path-label', 'modal.folderPath');
            this._setPlaceholder('#scan-folder-path', 'modal.folderPath');
            this._setText('#scan-recursive-label', 'modal.includeSubfolders');
            this._setText('#scan-quick-import-label', 'scan.quickImportLabel');
            this._setText('#scan-force-reparse-label', 'scan.forceReparseLabel');
            this._setText('#scan-cleanup-missing-label', 'scan.cleanupMissingLabel');
            this._setText('#scan-auto-tag-label', 'scan.autoTagLabel');
            var scanCancelBtn = document.querySelector('#btn-cancel-scan');
            if (!scanCancelBtn || scanCancelBtn.dataset.liveLabel !== '1') {
                this._setButton('#btn-cancel-scan', 'modal.cancel');
            }
            this._setButton('#btn-start-scan', 'modal.startScan');
            // Do NOT reset #scan-progress-text here while a scan is live.
            // The app removes data-i18n during active scans so progress polling
            // can own this field without MutationObserver/i18n clobbering it.
            this._setStaticText('#scan-progress-text', 'modal.scanStarting');

            this._setText('#tag-modal-title', 'modal.tagTitle');
            this._setText('#tag-modal .modal-description', 'modal.tagDescription');
            if (window.V321Integration && typeof window.V321Integration.syncVisibleTaggerCopy === 'function') {
                window.V321Integration.syncVisibleTaggerCopy();
            } else {
                this._setText('#tag-model-select-label', 'modal.tagModel');
            }
            this._setOptionText('#tag-model-select', {
                custom: 'modal.tagCustomModel'
            });
            this._setText('#custom-model-group label', 'modal.tagCustomModelPath');
            this._setPlaceholder('#tag-model-path', 'modal.tagCustomModelPath');
            this._setText('#custom-model-group .helper-text', 'modal.tagCustomModelPathHelper');
            this._setText('#custom-tags-group label', 'modal.tagTagsCsvPath');
            this._setPlaceholder('#tag-tags-path', 'modal.tagTagsCsvPath');
            this._setText('#custom-tags-group .helper-text', 'modal.tagTagsCsvHelper');
            this._setCheckboxTexts('#tag-modal', ['modal.tagRetagAll', 'modal.tagUseGpu']);
            // Do NOT reset #tag-gpu-help here. Runtime state in app.js owns this dynamic copy.
            // MutationObserver refreshes must not clobber CPU/GPU runtime fallback semantics.
            // Do NOT reset #tag-progress-text here while tagging is live.
            // The app removes data-i18n during active runs so progress polling
            // can own this field without MutationObserver/i18n clobbering it.
            this._setStaticText('#tag-progress-text', 'modal.tagLoadingModel');
            this._setButton('#btn-export-tags', 'modal.tagExport', 'i-upload', 'modal.tagExport');
            this._setButton('#btn-import-tags', 'modal.tagImport', 'i-download', 'modal.tagImport');
            this._setButton('#btn-cancel-tag', 'modal.tagCancel');
            this._setButton('#btn-start-tag', 'modal.tagStart');

            // The label span, not the <h3>: the heading also holds the sprite icon.
            this._setText('#analytics-title-text', 'modal.analytics');
            this._setTextAll('#analytics-modal h4', ['modal.topCheckpoints', 'modal.topLoras', 'modal.topTags']);

            this._setText('#export-title-text', 'modal.exportPrompts');
            this._setButton('#btn-export-tags-alt', 'modal.exportTagsAlt');
            this._setButton('#btn-copy-export', 'modal.copyToClipboard');

            this._setText('#confirm-title', 'modal.confirm');
            this._setText('#confirm-message', 'modal.confirmAction');
            this._setButton('#btn-confirm-cancel', 'modal.cancel');
            this._setButton('#btn-confirm-ok', 'modal.yes');

            this._setText('#input-modal-title', 'modal.enterValue');
            this._setButton('#btn-input-cancel', 'modal.cancel');
            this._setButton('#btn-input-ok', 'modal.ok');
        },

        _translateLibraryAndExport: function () {
            this._setText('#batch-export-title-text', 'batchExport.title');
            this._setText('#batch-export-folder + .helper-text', 'batchExport.outputFolderHelper');
            this._setText('label[for="batch-export-content-mode"]', 'batchExport.contentMode');
            this._setText('#batch-export-content-mode + .helper-text', 'batchExport.contentModeHelper');
            this._setText('label[for="batch-export-overwrite"]', 'batchExport.overwritePolicy');
            this._setText('#batch-export-prefix + .helper-text', 'batchExport.tagPrefixHelper');
            this._setText('#batch-export-blacklist + .helper-text', 'batchExport.tagBlacklistHelper');
            this._setPlaceholder('#batch-export-folder', 'batchExport.outputFolder');
            this._setPlaceholder('#batch-export-prefix', 'batchExport.tagPrefix');
            this._setPlaceholder('#batch-export-blacklist', 'batchExport.tagBlacklist');
            this._setText('#batch-export-progress-text', 'batchExport.exporting');
            this._setButton('#btn-cancel-batch-export', 'batchExport.cancel');
            this._setButton('#btn-start-batch-export', 'batchExport.exportFiles');

            // #rename-modal-title and the Save-Options heading are icon + label-span
            // pairs translated by applyToDOM; writing the <h3> would drop the icon.
            this._setText('#rename-modal .modal-description', 'rename.description');
            this._setText('#rename-modal .checkbox-text', 'rename.useOriginal');
            this._setText('#rename-modal .helper-text', 'rename.useOriginalHelper');
            this._setTextAll('#rename-modal .form-group label:not(.checkbox-label)', ['rename.baseName', 'rename.startingNumber', 'rename.preview']);
            this._setTextAll('#rename-modal .form-group .helper-text', ['rename.useOriginalHelper', 'rename.baseNameHelper', 'rename.startingNumberHelper']);
            this._setPlaceholder('#rename-base', 'rename.baseName');
            this._setText('.preview-hint', 'rename.andSoOn');
            this._setButton('#btn-cancel-rename', 'rename.cancel');
            this._setButton('#btn-apply-rename', 'rename.apply');

            this._setText('#save-options-modal .modal-description', 'save.description');
            this._setTextAll('#save-options-modal label', ['save.outputFolder', 'save.metadataHandling', 'save.outputFormat']);
            this._setPlaceholder('#save-output-folder', 'save.outputFolder');
            this._setTextAll('#save-options-modal .helper-text', ['save.outputFolderHelper', 'save.metadataHelper', 'save.formatHelper']);
            this._setOptionText('#save-metadata-option', {
                strip: 'save.metadataStrip',
                keep: 'save.metadataKeep',
                minimal: 'save.metadataMinimal'
            });
            this._setOptionText('#save-format-option', {
                png: 'save.formatPng',
                webp: 'save.formatWebp'
            });
            this._setButton('#btn-cancel-save-options', 'save.cancel');
            this._setButton('#btn-confirm-save-options', 'save.saveAll', 'i-save', 'save.saveAll');

            this._setText('#model-select-title', 'modelSelect.title');
            this._setPlaceholder('#model-select-search', 'modelSelect.search');
            this._setButton('#btn-cancel-model-select', 'modelSelect.cancel');
            this._setButton('#btn-confirm-model-select', 'modelSelect.apply');

            this._setText('#tags-library-modal h3', 'library.title');
            this._setText('#tags-library-modal .modal-description', 'library.description');
            this._setButton('#library-tab-tags', 'library.tags', 'i-tag', 'library.tags');
            this._setButton('#library-tab-prompts', 'library.prompts', 'i-edit', 'library.prompts');
            this._setButton('#library-tab-loras', 'library.loras', 'i-layers', 'library.loras');
            this._setOptionText('#library-sort', {
                frequency: 'library.sortFrequency',
                alphabetical: 'library.sortAlpha'
            });
            this._setPlaceholder('#library-search', 'library.search');
            // #library-stats-text is JS-owned (loadLibraryContent/setLibraryStatsText):
            // forcing it back to "Loading..." on every languageChanged wiped the
            // rendered count. Leave it to the library loader.
            this._setButton('#btn-close-tags-library-2', 'library.close');
        },

        _translateSelectionAndFilters: function () {
            this._setButton('#btn-open-library-from-filter', 'filter.browseLibrary', 'i-book', 'filter.browseLibrary');
            this._setText('#filter-modal-title', 'filter.filterImages');
            this._setText('#generator-filters-heading', 'filter.generators');
            // Order MUST match index.html #modal-generator-filters checkbox order.
            this._setCheckboxTexts('#modal-generator-filters', [
                'generator.comfyui',
                'generator.nai',
                'generator.webui',
                'generator.forge',
                'generator.reforge',
                'generator.fooocus',
                'generator.invokeai',
                'generator.swarmui',
                'generator.easyDiffusion',
                'generator.drawthings',
                'generator.gemini',
                'generator.gptImage',
                'generator.unknown',
                'generator.others'
            ]);
            this._setText('#dimensions-heading', 'filter.dimensions');
            this._setPlaceholder('#filter-min-width', 'filter.widthMin');
            this._setPlaceholder('#filter-max-width', 'filter.widthMax');
            this._setPlaceholder('#filter-min-height', 'filter.heightMin');
            this._setPlaceholder('#filter-max-height', 'filter.heightMax');
            this._setButton('#btn-reset-filters', 'filter.reset');
            this._setButton('#btn-apply-modal-filters', 'filter.apply');
            this._setText('#filter-modal .filter-column:nth-of-type(2) .filter-section:nth-of-type(1) h4', 'filter.tags');
            this._setText('#filter-modal .filter-column:nth-of-type(2) .filter-section:nth-of-type(2) h4', 'filter.promptSearch');
            this._setText('#filter-modal .filter-column:nth-of-type(2) .filter-section:nth-of-type(3) h4', 'filter.checkpoints');
            this._setText('#filter-modal .filter-column:nth-of-type(2) .filter-section:nth-of-type(4) h4', 'filter.loras');
            this._setPlaceholder('#modal-tag-search', 'filter.searchTags');
            this._setPlaceholder('#modal-prompt-search', 'filter.searchPrompts');
            this._setPlaceholder('#modal-checkpoint-search', 'filter.searchCheckpoints');
            this._setPlaceholder('#modal-lora-search', 'filter.searchLoras');

            this._setButton('#btn-select-all', 'selection.selectAllFiltered', 'i-check', 'selection.selectAllFiltered');
            this._setButton('#btn-invert-selection-filtered', 'selection.invertAllFiltered', 'i-refresh', 'selection.invertAllFiltered');
            this._setButton('#btn-move-selected', 'selection.moveSelected', 'i-folder', 'selection.moveSelected');
            this._setButton('#btn-copy-selected', 'selection.copySelected', 'i-file', 'selection.copySelected');
            this._setButton('#btn-send-to-censor', 'selection.censorEdit', 'i-grid', 'gallery.contextSendToCensor');
            this._setButton('#btn-remove-selected-gallery', 'selection.removeFromGallery', 'i-broom', 'selection.removeFromGallery');
            this._setButton('#btn-delete-selected-files', 'selection.deleteSelectedFiles', 'i-trash', 'selection.deleteSelectedFiles');
            this._setButton('#btn-clear-selection', 'selection.deselectAll');
        },

        _translateCommonState: function () {
            this._setText('#global-loading-msg', 'common.loading');
            this._setAttr('#btn-help', 'title', 'guide.tooltip');
            this._setAttr('#btn-help', 'aria-label', 'guide.tooltip');
            this._setAttr('#mobile-btn-language', 'title', 'lang.switchTooltip');
            this._setAttr('#mobile-btn-language', 'aria-label', 'lang.switchLabel');
            this._setAttr('#btn-language-toggle', 'title', 'lang.switchTooltip');
            this._setAttr('#btn-language-toggle', 'aria-label', 'lang.switchLabel');
        },

        _pauseObserver: function () {
            this._observerSuspended = true;
            if (this._observerResumeHandle != null) {
                cancelAnimationFrame(this._observerResumeHandle);
                this._observerResumeHandle = null;
            }
        },

        _resumeObserverSoon: function () {
            var self = this;
            if (this._observerResumeHandle != null) {
                cancelAnimationFrame(this._observerResumeHandle);
            }
            this._observerResumeHandle = requestAnimationFrame(function () {
                self._observerSuspended = false;
                self._observerResumeHandle = null;
            });
        },

        applyTranslations: function () {
            if (this._applying) return;
            this._applying = true;
            this._pauseObserver();

            try {
                if (window.I18n && typeof window.I18n.applyToDOM === 'function') {
                    window.I18n.applyToDOM();
                }

                this._translateGallery();
                this._translateAutoSeparate();
                this._translateManual();
                this._translateSimilar();
                this._translatePromptLab();
                this._translateArtist();
                this._translateImageModal();
                this._translateModalForms();
                this._translateLibraryAndExport();
                this._translateSelectionAndFilters();
                this._translateCommonState();
            } finally {
                this._applying = false;
                this._resumeObserverSoon();
            }
        },

        scheduleApply: function () {
            if (this._applyScheduled) return;
            this._applyScheduled = true;

            var self = this;
            requestAnimationFrame(function () {
                self._applyScheduled = false;
                self.applyTranslations();
            });
        },

        updateLanguageButtons: function () {
            var buttons = document.querySelectorAll('#btn-language-toggle, #mobile-btn-language');
            for (var i = 0; i < buttons.length; i++) {
                var button = buttons[i];
                if (!button.classList.contains('btn-icon-only')) {
                    var label = button.querySelector('span:last-child');
                    if (label) {
                        label.textContent = this._t('lang.toggle');
                    }
                }
                button.title = this._t('lang.switchTooltip') || this._t('lang.switchTitle');
                button.setAttribute('aria-label', this._t('lang.switchLabel'));
            }
        },

        initLanguageButtons: function () {
            var self = this;
            function bind(button) {
                if (!button || button.dataset.langBound === 'true') return;
                button.dataset.langBound = 'true';
                button.addEventListener('click', function () {
                    if (!window.I18n || typeof window.I18n.toggle !== 'function') return;
                    window.I18n.toggle();
                    if (button.id === 'mobile-btn-language' && typeof window.closeMobileMenu === 'function') {
                        window.closeMobileMenu();
                    }
                });
            }

            bind(document.getElementById('btn-language-toggle'));
            bind(document.getElementById('mobile-btn-language'));
            self.updateLanguageButtons();
        },

        observeChanges: function () {
            if (this._observer || !window.MutationObserver) return;
            var self = this;
            var root = document.getElementById('app');
            if (!root) return;

            this._observer = new MutationObserver(function () {
                if (!self._applying && !self._observerSuspended) {
                    self.scheduleApply();
                }
            });

            this._observer.observe(root, {
                childList: true,
                subtree: true,
                attributes: false
            });
        },

        init: function () {
            if (window.I18n && typeof window.I18n.init === 'function' && !window.I18n._initialized) {
                window.I18n.init();
            }

            document.documentElement.lang = window.I18n?.getLang?.() === 'zh-CN' ? 'zh-CN' : 'en';
            this.initLanguageButtons();
            this.applyTranslations();
            this.observeChanges();

            var self = this;
            document.addEventListener('languageChanged', function () {
                document.documentElement.lang = window.I18n?.getLang?.() === 'zh-CN' ? 'zh-CN' : 'en';
                self.updateLanguageButtons();
                self.scheduleApply();
            });

            setTimeout(function () { self.scheduleApply(); }, 120);
            setTimeout(function () { self.scheduleApply(); }, 500);
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            UIRefresh.init();
        });
    } else {
        UIRefresh.init();
    }

    window.UIRefresh = UIRefresh;
})();
