/**
 * v321/tagger-picker.js - v321-ui.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/v321-ui.js pre-cut lines 513-760 + 774-959
 * (of 3,164): (A) model-choice cards, aesthetic/color panels, VLM banner, submit.
 * Classic script: joins the ONE unsealed window.V321Integration object
 * declared in v321/base.js (loads FIRST); v321/boot.js registers the
 * DOMContentLoaded init LAST; index.html lists the family in original
 * line order.
 */
Object.assign(window.V321Integration, {

    /** Render the visible tagger model/source selector as dark in-app cards.
     *  The native select remains in the DOM as the canonical value owner for
     *  app.js, folder-browser.js, and existing E2E tests, but users no longer
     *  interact with an OS-themed dropdown that breaks the modal styling.
     */
    renderTaggerModelChoices() {
        const select = document.getElementById('tag-model-select');
        const list = document.getElementById('tag-model-choice-list');
        if (!select || !list) return;

        const activeTab = this.activeTaggerTab || 'local';
        const options = Array.from(select.querySelectorAll('option'))
            .filter((opt) => !opt.hidden && !opt.closest('optgroup[hidden]'));
        const currentValue = select.value || '';
        const localClosed = activeTab === 'local' && !this.localModelPickerOpen;
        list.hidden = localClosed;
        list.setAttribute('aria-hidden', localClosed ? 'true' : 'false');

        list.innerHTML = options.map((opt) => {
            const value = opt.value || '';
            const selected = value === currentValue;
            const disabled = opt.disabled;
            const title = this._getModelChoiceTitle(opt);
            const meta = this._getModelChoiceMeta(value, opt, activeTab);
            const badge = this._getModelChoiceBadge(value, opt, activeTab);
            const icon = this._getModelChoiceIcon(value, activeTab);
            const actionHtml = this._getModelChoiceActionHtml(value, activeTab, selected, disabled);
            return `
                <div
                    class="tagger-model-choice${selected ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}"
                    role="radio"
                    tabindex="${disabled ? '-1' : '0'}"
                    aria-checked="${selected ? 'true' : 'false'}"
                    aria-disabled="${disabled ? 'true' : 'false'}"
                    data-model-value="${this._escapeAttr(value)}"
                >
                    <span class="tagger-model-choice-icon" aria-hidden="true">${this._escapeHtml(icon)}</span>
                    <span class="tagger-model-choice-copy">
                        <span class="tagger-model-choice-title">${this._escapeHtml(title)}</span>
                        ${badge ? `<span class="tagger-model-choice-badge">${this._escapeHtml(badge)}</span>` : ''}
                        ${meta ? `<span class="tagger-model-choice-meta">${this._escapeHtml(meta)}</span>` : ''}
                        ${actionHtml}
                    </span>
                </div>
            `;
        }).join('');

        for (const btn of list.querySelectorAll('.tagger-model-choice')) {
            const activate = () => {
                const value = btn.dataset.modelValue || '';
                const option = Array.from(select.querySelectorAll('option'))
                    .find((opt) => opt.value === value);
                if (!option || option.disabled) return;

                select.value = value;
                this._syncNlRadioFromModelValue(value);
                select.dispatchEvent(new Event('change'));
                if (activeTab === 'nl') {
                    const source = value === 'vlm' ? 'vlm' : 'toriigate';
                    this._syncNlWorkflow(source);
                    this.refreshVLMBannerStatus();
                    try { window.VLMCaption?.syncWorkflowVisibility?.(); } catch (_e) {}
                }
            };
            btn.addEventListener('click', (event) => {
                if (event.target?.closest?.('.tagger-model-choice-actions')) return;
                activate();
            });
            btn.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                activate();
            });
        }
        list.querySelector('#btn-tagger-toriigate-setup')?.addEventListener('click', (event) => {
            event.stopPropagation();
            this._openTaggerSetup?.('toriigate', 'tagger.routedToriigate');
        });
        list.querySelector('#btn-vlm-banner-settings')?.addEventListener('click', (event) => {
            event.stopPropagation();
            if (typeof window.App?.openVlmSettings === 'function') {
                window.App.openVlmSettings();
            } else {
                document.getElementById('btn-vlm-settings')?.click();
            }
        });
        this._syncCurrentVisibleCopy();
    },

    _syncCurrentVisibleCopy() {
        const tab = this.activeTaggerTab || 'local';
        const modalDesc = document.querySelector('#tag-modal .modal-description');
        if (modalDesc) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const map = {
                local: ['modal.tagDescription', 'Pick a supported tagger model and generate tags for images.'],
                nl: ['tagger.modalDescNl', 'Choose a natural-language backend and caption selected images.'],
                aesthetic: ['tagger.modalDescAesthetic', 'Score selected images with the local aesthetic model.'],
            };
            const [key, fallback] = map[tab] || map.local;
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
        this._syncLocalModelSummary();
        if (tab === 'nl') {
            const source = document.querySelector('input[name="tagger-nl-source"]:checked')?.value || 'toriigate';
            this._syncNlWorkflow(source);
        }
        this._syncModalActionsForTab(tab);
    },

    _syncLocalModelSummary() {
        const current = document.getElementById('tagger-model-current');
        const titleEl = document.getElementById('tagger-model-current-title');
        const metaEl = document.getElementById('tagger-model-current-meta');
        const actionEl = current?.querySelector('.tagger-model-current-action');
        const select = document.getElementById('tag-model-select');
        if (!current || !select) return;

        const isLocal = this.activeTaggerTab === 'local';
        current.hidden = !isLocal;
        current.setAttribute('aria-hidden', isLocal ? 'false' : 'true');
        current.setAttribute('aria-expanded', this.localModelPickerOpen ? 'true' : 'false');
        current.classList.toggle('is-open', this.localModelPickerOpen);
        if (!isLocal) return;

        const opt = select.selectedOptions?.[0] || select.querySelector(`option[value="${CSS.escape(select.value || '')}"]`);
        if (titleEl) {
            titleEl.textContent = opt ? this._getModelChoiceTitle(opt) : (select.value || '');
        }
        if (metaEl) {
            metaEl.textContent = opt ? this._getModelChoiceMeta(opt.value || '', opt, 'local') : '';
        }
        if (actionEl) {
            const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            actionEl.textContent = this.localModelPickerOpen
                ? i18n('tagger.hideModelList', 'Hide list')
                : i18n('tagger.changeModel', 'Change model');
        }
    },

    syncVisibleTaggerCopy() {
        this._syncCurrentVisibleCopy();
        this.renderTaggerModelChoices();
    },

    _syncNlRadioFromModelValue(value) {
        if (this.activeTaggerTab !== 'nl') return;
        const source = value === 'vlm' ? 'vlm' : 'toriigate';
        const radio = document.querySelector(`input[name="tagger-nl-source"][value="${source}"]`);
        if (radio) radio.checked = true;
    },

    _getModelChoiceTitle(opt) {
        const value = opt?.value || '';
        if (value === 'vlm') {
            const _v1 = window.I18n?.t?.('tagger.nlVlmApiTitle');
            return (_v1 && _v1 !== 'tagger.nlVlmApiTitle') ? _v1 : 'VLM API (Cloud / Ollama / OpenRouter)';
        }
        if (value.toLowerCase().includes('toriigate')) {
            const _v2 = window.I18n?.t?.('tagger.nlToriiTitle');
            return (_v2 && _v2 !== 'tagger.nlToriiTitle') ? _v2 : 'ToriiGate (local model)';
        }
        return (opt?.textContent || value || 'Model')
            .replace(/\s+\(Recommended\)\s*$/i, '')
            .replace(/\s+\(Unavailable\)\s*$/i, '')
            .trim();
    },

    _getModelChoiceMeta(value, opt, activeTab) {
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        const lower = String(value || '').toLowerCase();
        if (value === 'vlm') {
            return i18n('tagger.nlVlmApiHint', 'Send images to a remote VLM endpoint. Configure provider + model in VLM Settings.');
        }
        if (lower.includes('toriigate')) {
            return i18n('tagger.nlToriiHint', 'Heavy local VLM. Needs a one-time ~5 GB model download from the Setup page.');
        }
        if (activeTab === 'local' && value === 'custom') {
            return i18n('tagger.customSubtitle', 'Custom local ONNX tagger with a selectable model profile.');
        }
        const meta = window.getTaggerModelMetaForV321?.(value) || null;
        if (meta?.description || meta?.summary || meta?.best_for) {
            const parts = [];
            if (meta.description || meta.summary) parts.push(meta.description || meta.summary);
            if (meta.best_for) parts.push(meta.best_for);
            return parts.join(' · ');
        }
        return opt?.title || '';
    },

    _getModelChoiceBadge(value, opt, activeTab) {
        const text = opt?.textContent || '';
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        if (opt?.disabled) return i18n('tagger.chipCatalogOnly', 'Catalog Only');
        if (value === 'vlm') return i18n('tagger.tierVlm', 'VLM');
        if (String(value || '').toLowerCase().includes('toriigate')) return i18n('tagger.chipPytorchCuda', 'PyTorch CUDA');
        if (text.includes('(Recommended)')) return i18n('tagger.badgeRecommended', 'Recommended');
        if (activeTab === 'local' && value === 'custom') return i18n('tagger.customBadge', 'Custom');
        return '';
    },

    _getModelChoiceIcon(value, activeTab) {
        const lower = String(value || '').toLowerCase();
        if (value === 'vlm') return '☁';
        if (lower.includes('toriigate')) return '◎';
        if (value === 'custom') return '⚙';
        return activeTab === 'nl' ? '◇' : '✓';
    },

    _getModelChoiceActionHtml(value, activeTab, selected, disabled) {
        if (activeTab !== 'nl' || !selected || disabled) return '';
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        if (value === 'vlm') {
            return `
                <span class="tagger-model-choice-actions">
                    <button type="button" class="btn btn-secondary btn-small" id="btn-vlm-banner-settings">
                        <span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg></span>
                        <span>${this._escapeHtml(i18n('vlm.openSettings', 'VLM Settings'))}</span>
                    </button>
                    <button type="button" class="btn btn-ghost btn-small" data-vlm-debug-chat>
                        <span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-message"/></svg></span>
                        <span>${this._escapeHtml(i18n('vlm.debugChat', 'API Chat'))}</span>
                    </button>
                </span>
            `;
        }
        if (String(value || '').toLowerCase().includes('toriigate')) {
            return `
                <span class="tagger-model-choice-actions">
                    <button type="button" class="btn btn-secondary btn-small" id="btn-tagger-toriigate-setup">
                        <span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg></span>
                        <span>${this._escapeHtml(i18n('tagger.aestheticOpenSetup', 'Open Setup to install'))}</span>
                    </button>
                </span>
            `;
        }
        return '';
    },


    /** Read aesthetic readiness and active-task state from the shared publisher. */
    async _refreshAestheticTab() {
        const titleEl = document.getElementById('tagger-aesthetic-status-title');
        const setupBtn = document.getElementById('btn-tagger-aesthetic-setup');
        const startBtn = document.getElementById('btn-tagger-aesthetic-start');
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };

        // Optimistic "checking..." state while the request is in flight so the
        // user does not see a stale ready/missing message from the previous
        // open of the modal.
        if (titleEl) {
            titleEl.textContent = i18n('models.checking', 'Checking…');
        }
        if (startBtn) startBtn.disabled = true;

        let isReady = false;
        let message = '';
        try {
            const taskState = await refreshAestheticTaskState();
            isReady = Boolean(taskState?.status?.available);
            message = taskState?.status?.message || '';
        } catch (_e) {
            isReady = false;
        }

        if (titleEl) {
            const key = isReady ? 'tagger.aestheticReady' : 'tagger.aestheticMissing';
            titleEl.setAttribute('data-i18n', key);
            const fallback = isReady
                ? 'Aesthetic scoring is ready on this machine.'
                : 'Aesthetic scoring needs a one-time setup (~2 GB) to install runtimes and models.';
            titleEl.textContent = (message && !isReady) ? message : i18n(key, fallback);
        }
        if (setupBtn) setupBtn.style.display = isReady ? 'none' : '';
        if (startBtn && !isReady) startBtn.disabled = true;
    },

    /** v3.2.1 task #26: Color analysis tab — show counts and wire start/cancel. */
    async _refreshColorTab() {
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        const titleEl = document.getElementById('tagger-color-status-title');
        const countsEl = document.getElementById('tagger-color-status-counts');
        const startBtn = document.getElementById('btn-tagger-color-start');
        const cancelBtn = document.getElementById('btn-tagger-color-cancel');

        let totalImages = 0;
        let missingCount = 0;
        let running = false;
        try {
            const [missingRes, progressRes] = await Promise.all([
                fetch('/api/colors/missing-count', { cache: 'no-store' }),
                fetch('/api/colors/progress', { cache: 'no-store' }),
            ]);
            if (missingRes.ok) {
                const data = await missingRes.json();
                missingCount = Number(data?.missing) || 0;
                totalImages = Number(data?.total) || 0;
            }
            if (progressRes.ok) {
                const data = await progressRes.json();
                running = Boolean(data?.running);
            }
        } catch (_e) {
            // Non-fatal — leave counts at 0.
        }

        if (titleEl) {
            const titleKey = missingCount > 0 ? 'tagger.colorMissing' : 'tagger.colorReady';
            const titleFallback = missingCount > 0
                ? `${missingCount.toLocaleString()} images need color analysis.`
                : 'All analyzed — color sorts and filters are ready.';
            titleEl.setAttribute('data-i18n', titleKey);
            titleEl.textContent = i18n(titleKey, '') || titleFallback;
        }

        if (countsEl) {
            if (totalImages > 0) {
                const analyzed = Math.max(totalImages - missingCount, 0);
                const lang = window.I18n?.getLang?.() === 'zh-CN' ? 'zh-CN' : 'en';
                countsEl.textContent = lang === 'zh-CN'
                    ? `已分析 ${analyzed.toLocaleString()} / ${totalImages.toLocaleString()}（剩余 ${missingCount.toLocaleString()}）`
                    : `Analyzed ${analyzed.toLocaleString()} of ${totalImages.toLocaleString()} (${missingCount.toLocaleString()} remaining)`;
            } else {
                countsEl.textContent = '';
            }
        }

        if (startBtn) {
            startBtn.disabled = running || missingCount === 0;
            startBtn.onclick = async () => {
                if (window.ColorBackfill?.startAnalysis) {
                    await window.ColorBackfill.startAnalysis();
                    // Refresh tab UI after kickoff so cancel button surfaces.
                    setTimeout(() => this._refreshColorTab(), 600);
                }
            };
        }
        if (cancelBtn) {
            cancelBtn.style.display = running ? '' : 'none';
            cancelBtn.onclick = async () => {
                if (window.ColorBackfill?.cancelAnalysis) {
                    await window.ColorBackfill.cancelAnalysis();
                    setTimeout(() => this._refreshColorTab(), 600);
                }
            };
        }
    },

    /** Add a temporary highlight to a model card so the user sees where to click. */
    _highlightModelCard(modelId) {
        if (!modelId) return;
        const tryHighlight = (attempt) => {
            const card = document.querySelector(`.model-card[data-model-id="${CSS.escape(modelId)}"]`);
            if (!card) {
                if (attempt < 12) setTimeout(() => tryHighlight(attempt + 1), 250);
                return;
            }
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.add('is-highlighted');
            setTimeout(() => card.classList.remove('is-highlighted'), 2400);
        };
        tryHighlight(0);
    },

    async refreshVLMBannerStatus() {
        const el = document.getElementById('vlm-banner-current');
        const legacyEl = document.getElementById('vlm-banner-current-legacy');
        if (!el) return;
        const i18n = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
        const setText = (text) => {
            el.textContent = text;
            if (legacyEl) legacyEl.textContent = text;
        };
        try {
            const res = await fetch('/api/vlm/settings');
            const s = await res.json();
            const provider = s.provider || 'openai_compat';
            const model = s.model || '—';
            const endpoint = s.endpoint || '—';
            if (s.api_key_display || s.endpoint) {
                setText(`${provider} · ${model} · ${endpoint}`);
            } else {
                setText(i18n('vlm.notConfigured', 'Not configured — click VLM Settings to set up'));
            }
        } catch (e) {
            setText(i18n('vlm.notConfigured', 'Not configured'));
        }
    },

    /** Intercept the Tag button click — when VLM is selected, route to VLM batch endpoint */
    interceptTagSubmit() {
        // Wait for app to bind original handler, then attach our pre-handler in capture phase
        // The actual button id in index.html is #btn-start-tag (not btn-start-tagging).
        const startBtn = document.getElementById('btn-start-tag');
        if (!startBtn) {
            // Try later
            setTimeout(() => this.interceptTagSubmit(), 500);
            return;
        }
        startBtn.addEventListener('click', (e) => {
            // vlmActive is owned by the dropdown value, NOT by the active tab.
            // The Natural Language tab can have either ToriiGate or VLM
            // selected; only VLM should route to the VLM batch endpoint.
            const select = document.getElementById('tag-model-select');
            const isVlm = select && select.value === 'vlm';
            if (!isVlm) return;  // local + ToriiGate paths run the regular tagger pipeline

            // VLM path
            e.stopPropagation();
            e.preventDefault();

            // Trigger VLM batch instead
            if (window.VLMCaption?.startBatchCaption) {
                window.VLMCaption.startBatchCaption();
            } else {
                document.getElementById('btn-vlm-start')?.click();
            }
        }, true);  // capture phase to fire before existing listeners
    },
});
