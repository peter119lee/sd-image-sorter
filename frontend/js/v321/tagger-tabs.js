/**
 * v321/tagger-tabs.js - v321-ui.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/v321-ui.js pre-cut lines 100-512
 * (of 3,164): (A) tagger tab switching, NL sub-source, setTaggerTab/applyTaggerTab.
 * Classic script: joins the ONE unsealed window.V321Integration object
 * declared in v321/base.js (loads FIRST); v321/boot.js registers the
 * DOMContentLoaded init LAST; index.html lists the family in original
 * line order.
 */
Object.assign(window.V321Integration, {

    // ====================================================================
    // (A) Tagger 3-tab redesign — Local / Natural Language / Aesthetic
    //     Replaces the old single-dropdown VLM-mode-banner approach.
    // ====================================================================

    /** Currently selected tab id ('smart' | 'local' | 'nl' | 'aesthetic' | 'color').
     *  The global AI-Tag button opens on 'local' (the familiar WD14 config). The
     *  Aurora Phase 3 (#25b) 智能一趟 (Smart Tag) landing is scoped to the Gallery
     *  [打标] selection flow, which forces the 'smart' tab on open — see
     *  gallery-toolbar wireTagSelected. Smart stays the prominent first tab. */
    activeTaggerTab: 'local',
    localModelPickerOpen: false,

    bindTaggerBackendSwitch() {
        const tabsRow = document.querySelector('#tag-modal .tagger-tabs');
        const select = document.getElementById('tag-model-select');
        if (!tabsRow || !select) return;

        const tabButtons = Array.from(tabsRow.querySelectorAll('.tagger-tab[data-tagger-tab]'));

        // -- Tab click handler --
        for (const btn of tabButtons) {
            btn.addEventListener('click', () => {
                const tab = btn.dataset.taggerTab;
                if (!tab) return;
                this.setTaggerTab(tab);
            });
        }

        // The dropdown is rebuilt asynchronously by app.js loadTaggerModels()
        // when the modal opens. Re-apply tab visibility every time options
        // change so ToriiGate / VLM filtering stays correct.
        const observer = new MutationObserver(() => {
            this.applyTaggerTab(this.activeTaggerTab, { silent: true });
        });
        observer.observe(select, { childList: true });

        const tagModal = document.getElementById('tag-modal');
        if (tagModal) {
            const modalObserver = new MutationObserver(() => {
                if (tagModal.classList.contains('visible') && this.activeTaggerTab === 'aesthetic') {
                    void this._refreshAestheticTab();
                }
            });
            modalObserver.observe(tagModal, { attributes: true, attributeFilter: ['class'] });
        }

        select.addEventListener('change', () => {
            if (this.activeTaggerTab === 'local') {
                this.localModelPickerOpen = false;
            }
            this.renderTaggerModelChoices();
        });

        document.getElementById('tagger-model-current')?.addEventListener('click', () => {
            if (this.activeTaggerTab !== 'local') return;
            this.localModelPickerOpen = !this.localModelPickerOpen;
            this.renderTaggerModelChoices();
        });

        // The "Open VLM Settings" button used by the Natural Language flow.
        const openVlmSettings = () => {
            if (typeof window.App?.openVlmSettings === 'function') {
                window.App.openVlmSettings();
            } else {
                document.getElementById('btn-vlm-settings')?.click();
            }
        };
        document.getElementById('btn-vlm-banner-settings')?.addEventListener('click', openVlmSettings);
        document.getElementById('btn-vlm-banner-settings-legacy')?.addEventListener('click', openVlmSettings);

        // Setup CTAs — open Model Manager (Task 5 will scroll + highlight).
        this._openTaggerSetup = (highlightId, toastKey) => {
            const closeTagger = () => {
                if (typeof window.hideModal === 'function') {
                    window.hideModal('tag-modal');
                } else {
                    document.getElementById('tag-modal')?.classList.remove('visible');
                }
            };
            const showSetup = () => {
                if (typeof window.openModelManager === 'function') {
                    window.openModelManager();
                } else {
                    document.getElementById('btn-open-model-manager')?.click();
                }
            };
            closeTagger();
            // Defer so the close animation finishes before the next modal opens.
            setTimeout(() => {
                showSetup();
                if (highlightId) {
                    this._highlightModelCard(highlightId);
                }
                if (toastKey && typeof window.showToast === 'function') {
                    const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
                    window.showToast(i18n(toastKey, ''), 'info');
                }
            }, 60);
        };
        document.getElementById('btn-tagger-aesthetic-setup')?.addEventListener('click', () => {
            this._openTaggerSetup('aesthetic', 'tagger.routedFromTagger');
        });

        // Aesthetic tab Start / Cancel proxy to the legacy global buttons while
        // the shared task state keeps both control surfaces synchronized.
        document.getElementById('btn-tagger-aesthetic-start')?.addEventListener('click', (event) => {
            const startButton = event.currentTarget;
            if (startButton.disabled) return;
            document.getElementById('btn-score-aesthetic')?.click();
        });
        document.getElementById('btn-tagger-aesthetic-cancel')?.addEventListener('click', () => {
            document.getElementById('btn-cancel-aesthetic')?.click();
        });
        document.getElementById('btn-cancel-aesthetic-tab')?.addEventListener('click', () => {
            if (typeof window.hideModal === 'function') {
                window.hideModal('tag-modal');
            }
        });

        // Aurora Phase 3 (#25b): wire the 智能一趟 launch CTA once.
        this._bindSmartTabOnce();

        // Initial state — the global tagger opens on the local WD14 config.
        // #25b's 智能一趟 landing is applied per-open by the Gallery [打标] flow
        // (gallery-toolbar wireTagSelected → setTaggerTab('smart')), not globally.
        this.setTaggerTab('local');
    },

    /** Wire the Smart Tag launch panel's CTA (idempotent). Opens the full Smart
     *  Tag workspace, forwarding the armed Gallery selection scope if present. */
    _bindSmartTabOnce() {
        if (this._smartTabBound) return;
        this._smartTabBound = true;
        const goBtn = document.getElementById('btn-tagger-smart-go');
        if (!goBtn) return;
        goBtn.addEventListener('click', () => {
            const armed = window.GalleryToolbar?.consumeTagSelectionIds?.() || null;
            if (typeof window.hideModal === 'function') {
                try { window.hideModal('tag-modal'); } catch (_e) {}
            }
            // Defer so the tagger modal's close animation finishes first.
            setTimeout(() => {
                if (armed && armed.length && typeof window.SmartTag?.openScoped === 'function') {
                    window.SmartTag.openScoped({ imageIds: armed });
                } else if (typeof window.SmartTag?.open === 'function') {
                    window.SmartTag.open();
                }
            }, 120);
        });
    },

    /** Refresh the 智能一趟 launch panel's scope line from the armed Gallery
     *  selection (non-consuming peek) or fall back to whole-library wording. */
    _refreshSmartTab() {
        const scopeEl = document.getElementById('tagger-smart-scope');
        if (!scopeEl) return;
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        const armed = window.GalleryToolbar?.consumeTagSelectionIds?.() || null;
        if (armed && armed.length) {
            scopeEl.textContent = i18n('tagger.smartScopeSelected', 'Scoped to the {count} images selected in Gallery.')
                .replace('{count}', String(armed.length));
        } else {
            scopeEl.textContent = i18n('tagger.smartScopeAll', 'Runs on the Gallery selection, filtered scope, or Dataset Maker queue you set up next.');
        }
    },

    /** Switch the active tab. Updates dropdown filter, panel visibility, status text. */
    setTaggerTab(tab) {
        if (!['smart', 'local', 'nl', 'aesthetic', 'color'].includes(tab)) {
            tab = 'smart';
        }
        this.activeTaggerTab = tab;
        this.vlmActive = (tab === 'nl');
        if (tab !== 'local') {
            this.localModelPickerOpen = false;
        }

        // Tab button active state.
        const tabsRow = document.querySelector('#tag-modal .tagger-tabs');
        if (tabsRow) {
            for (const b of tabsRow.querySelectorAll('.tagger-tab')) {
                const isActive = b.dataset.taggerTab === tab;
                b.classList.toggle('active', isActive);
                b.setAttribute('aria-selected', isActive ? 'true' : 'false');
            }
        }

        // Tab description line below the tab row.
        const desc = document.getElementById('tagger-tab-description');
        const modalDesc = document.querySelector('#tag-modal .modal-description');
        if (desc) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const map = {
                smart: 'tagger.tabSmartDesc',
                local: 'tagger.tabLocalDesc',
                nl: 'tagger.tabNlDesc',
                aesthetic: 'tagger.tabAestheticDesc',
                color: 'tagger.tabColorDesc',
            };
            desc.setAttribute('data-i18n', map[tab]);
            desc.textContent = i18n(map[tab], '');
        }
        if (modalDesc) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const map = {
                smart: ['tagger.modalDescSmart', 'One guided pass: booru tags, optional caption, cleanup, and trigger word — recommended.'],
                local: ['modal.tagDescription', 'Pick a supported tagger model and generate tags for images.'],
                nl: ['tagger.modalDescNl', 'Choose a natural-language backend and caption selected images.'],
                aesthetic: ['tagger.modalDescAesthetic', 'Score selected images with the local aesthetic model.'],
                color: ['tagger.modalDescColor', 'Run local color analysis to enable color sorts and filters.'],
            };
            const [key, fallback] = map[tab] || map.smart;
            modalDesc.setAttribute('data-i18n', key);
            modalDesc.textContent = i18n(key, fallback);
        }
        const modelLabel = document.getElementById('tag-model-select-label');
        if (modelLabel) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const key = tab === 'nl' ? 'tagger.nlSourceLabel' : 'modal.tagModel';
            modelLabel.setAttribute('data-i18n', key);
            modelLabel.textContent = i18n(key, tab === 'nl' ? 'Natural language source' : 'Model');
        }

        this.applyTaggerTab(tab);

        if (tab === 'smart') {
            this._refreshSmartTab();
        }
        if (tab === 'nl') {
            // Bind radio listeners (idempotent) + apply current selection.
            this._bindNlSubToggleOnce();
            this._applyNlSubSource();
            this.refreshVLMBannerStatus();
        }
        if (tab === 'aesthetic') {
            this._refreshAestheticTab();
        }
        if (tab === 'color') {
            this._refreshColorTab();
        }
        try { window.VLMCaption?.syncWorkflowVisibility?.(); } catch (_e) {}
    },

    /** Wire the ToriiGate / VLM API radio toggle inside the Natural Language tab. */
    _bindNlSubToggleOnce() {
        if (this._nlSubToggleBound) return;
        this._nlSubToggleBound = true;
        const radios = document.querySelectorAll('input[name="tagger-nl-source"]');
        for (const r of radios) {
            r.addEventListener('change', () => this._applyNlSubSource());
        }
    },

    /** Sync NL tab content to the chosen sub-source.
     *  ToriiGate: hide VLM banner + utility strip, set dropdown to toriigate-0.5.
     *  VLM API:   hide ToriiGate setup card, set dropdown to vlm.
     */
    _applyNlSubSource() {
        const checked = document.querySelector('input[name="tagger-nl-source"]:checked');
        const source = checked?.value || 'toriigate';
        const select = document.getElementById('tag-model-select');

        const toriiCard = document.getElementById('tagger-nl-toriigate-card');
        const vlmBanner = document.getElementById('vlm-mode-banner');
        const vlmStrip = document.querySelector('#tag-modal .tagger-utility-strip');

        if (source === 'toriigate') {
            if (toriiCard) toriiCard.style.display = '';
            if (vlmBanner) vlmBanner.style.display = 'none';
            if (vlmStrip) vlmStrip.style.display = 'none';
            if (select) {
                const torii = Array.from(select.querySelectorAll('option'))
                    .find((o) => (o.value || '').toLowerCase().includes('toriigate'));
                if (torii) {
                    select.value = torii.value;
                    select.dispatchEvent(new Event('change'));
                }
            }
        } else {
            if (toriiCard) toriiCard.style.display = 'none';
            if (vlmBanner) vlmBanner.style.display = '';
            if (vlmStrip) vlmStrip.style.display = '';
            if (select) {
                const vlmOpt = select.querySelector('option[value="vlm"]');
                if (vlmOpt) {
                    select.value = 'vlm';
                    select.dispatchEvent(new Event('change'));
                }
            }
        }
        this._syncNlWorkflow(source);
        this.renderTaggerModelChoices();
        try { window.VLMCaption?.syncWorkflowVisibility?.(); } catch (_e) {}
    },

    _syncNlWorkflow(source) {
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        const title = document.getElementById('tagger-nl-workflow-title');
        const hint = document.getElementById('tagger-nl-workflow-hint');
        const vlmStatus = document.getElementById('vlm-banner-current');
        const startBtn = document.getElementById('btn-start-tag');
        const toriiCard = document.getElementById('tagger-nl-toriigate-card');
        const vlmBanner = document.getElementById('vlm-mode-banner');
        const vlmStrip = document.querySelector('#tag-modal .tagger-utility-strip');

        if (source === 'vlm') {
            if (toriiCard) toriiCard.style.display = 'none';
            if (vlmBanner) vlmBanner.style.display = 'none';
            if (vlmStrip) vlmStrip.style.display = 'none';
            if (title) {
                title.setAttribute('data-i18n', 'tagger.nlVlmApiTitle');
                title.textContent = i18n('tagger.nlVlmApiTitle', 'VLM API (Cloud / Ollama / OpenRouter)');
            }
            if (hint) {
                hint.setAttribute('data-i18n', 'tagger.nlVlmApiHint');
                hint.textContent = i18n('tagger.nlVlmApiHint', 'Send images to a remote VLM endpoint. Configure provider + model in VLM Settings.');
            }
            if (vlmStatus) vlmStatus.style.display = '';
            if (startBtn && !startBtn.disabled) {
                startBtn.dataset.i18nLocked = '1';
                startBtn.textContent = i18n('vlm.utilityStart', 'Caption');
            }
        } else {
            if (toriiCard) toriiCard.style.display = '';
            if (vlmBanner) vlmBanner.style.display = 'none';
            if (vlmStrip) vlmStrip.style.display = 'none';
            if (title) {
                title.setAttribute('data-i18n', 'tagger.nlToriiTitle');
                title.textContent = i18n('tagger.nlToriiTitle', 'ToriiGate (local model)');
            }
            if (hint) {
                hint.setAttribute('data-i18n', 'tagger.nlToriiHint');
                hint.textContent = i18n('tagger.nlToriiHint', 'Heavy local captioner. Needs a one-time ~9.6 GB BF16 download from Model Manager.');
            }
            if (vlmStatus) vlmStatus.style.display = 'none';
            if (startBtn && !startBtn.disabled) {
                startBtn.dataset.i18nLocked = '1';
                startBtn.textContent = i18n('modal.tagStart', 'Start Tagging');
            }
        }
    },

    /** Show/hide tab-keyed sections + filter the model dropdown to the right entries. */
    applyTaggerTab(tab, _opts = {}) {
        const modal = document.getElementById('tag-modal');
        if (!modal) return;
        const modelSelector = document.querySelector('#tag-modal .tagger-model-selector');
        if (modelSelector) {
            modelSelector.dataset.activeTab = tab;
        }
        this._syncModalActionsForTab(tab);

        // Toggle every element with data-tagger-shows.
        // The attribute is a space-separated list of tab ids ("local nl"), and
        // an element is visible only if that list contains the active tab.
        const candidates = modal.querySelectorAll('[data-tagger-shows]');
        for (const el of candidates) {
            const shows = (el.dataset.taggerShows || '').split(/\s+/).filter(Boolean);
            const visible = shows.includes(tab);
            el.style.display = visible ? '' : 'none';
        }
        try { window.VLMCaption?.syncWorkflowVisibility?.(); } catch (_e) {}

        // Filter the dropdown options based on tab.
        const select = document.getElementById('tag-model-select');
        if (select) {
            for (const opt of select.querySelectorAll('option')) {
                const value = (opt.value || '').toLowerCase();
                const isVlm = value === 'vlm';
                const isTorii = value.includes('toriigate');
                let allowed;
                if (tab === 'local') {
                    allowed = !isVlm && !isTorii;
                } else if (tab === 'nl') {
                    allowed = isVlm || isTorii;
                } else {
                    allowed = false;
                }
                opt.hidden = !allowed;
                opt.disabled = opt.disabled && !opt.dataset.allowedByTab;
            }
            // Hide empty optgroups whose options are all hidden.
            for (const og of select.querySelectorAll('optgroup')) {
                const visibleChildren = Array.from(og.querySelectorAll('option'))
                    .filter((o) => !o.hidden);
                og.hidden = visibleChildren.length === 0;
            }
            // If the current value is filtered out, fall back to the first
            // allowed value so the select.value stays meaningful.
            const currentOpt = select.querySelector(`option[value="${CSS.escape(select.value || '')}"]`);
            if (!currentOpt || currentOpt.hidden) {
                const firstAllowed = Array.from(select.querySelectorAll('option'))
                    .find((o) => !o.hidden && !o.disabled);
                if (firstAllowed) {
                    select.value = firstAllowed.value;
                    select.dispatchEvent(new Event('change'));
                }
            }
        }
        this.renderTaggerModelChoices();
    },

    _syncModalActionsForTab(tab) {
        const localActions = document.querySelectorAll('#tag-modal .tagger-local-action');
        for (const action of localActions) {
            action.style.display = tab === 'local' ? '' : 'none';
        }
        const startBtn = document.getElementById('btn-start-tag');
        if (startBtn && !startBtn.disabled) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            if (tab === 'nl') {
                startBtn.dataset.i18nLocked = '1';
                const source = document.querySelector('input[name="tagger-nl-source"]:checked')?.value || 'toriigate';
                startBtn.textContent = source === 'vlm'
                    ? i18n('vlm.utilityStart', 'Caption')
                    : i18n('modal.tagStart', 'Start Tagging');
            } else {
                delete startBtn.dataset.i18nLocked;
                startBtn.textContent = i18n('modal.tagStart', 'Start Tagging');
            }
        }
    },
});
