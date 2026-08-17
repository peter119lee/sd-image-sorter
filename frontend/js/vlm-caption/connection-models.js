/**
 * vlm-caption/connection-models.js — vlm-caption.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/vlm-caption.js pre-cut
 * lines 290-361 + 573-810 (of 1,073): testConnection, fetchModels,
 * applyPreset, then the "Local Model Management" section —
 * _populateModelSuggestions (datalist), loadRecommendedModels (Ollama
 * install/run branches + model cards), confirmDeleteModel (App.showConfirm /
 * window.confirm fallback), deleteModel, pullModel, _startPullPolling and
 * startOllama. The mid-literal `_pullPollInterval: null` data prop (pre-cut
 * line 770) stays verbatim in its original slot between pullModel and
 * _startPullPolling (pinned shape — do NOT hoist it into the core state
 * block). Classic non-strict script: joins the ONE unsealed window.VLMCaption
 * object declared in vlm-caption/core.js, which loads FIRST;
 * vlm-caption/boot.js registers the DOMContentLoaded init LAST.
 */
Object.assign(window.VLMCaption, {
    async testConnection() {
        if (!await this._saveBeforeAction('vlm-status', this._t('vlmSettings.savingThenTesting', 'Saving settings, then testing connection...'))) return;
        try {
            const resp = await fetch('/api/vlm/test', { method: 'POST' });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.status === 'ok') {
                const modelCount = data.models?.length || 0;
                this._showStatus('vlm-status', `${this._t('vlmSettings.connected', 'Connected ✓')} ${modelCount ? `(${modelCount} models)` : ''}`, 'success');
            } else {
                const reason = data.error || data.detail || resp.statusText || 'Unknown';
                this._showStatus('vlm-status', `Failed: ${reason} ${data.error_type ? `[${data.error_type}]` : ''}`, 'error');
            }
        } catch (e) {
            this._showStatus('vlm-status', `Connection error: ${e.message}`, 'error');
        }
    },

    /**
     * Probe the configured VLM API for the highest stable concurrent_requests.
     * Saves form first (same as Test Connection), then ramps concurrent
     * health checks and writes the recommendation into #vlm-concurrent.
     */
    async probeConcurrency() {
        if (!await this._saveBeforeAction(
            'vlm-status',
            this._t('vlmSettings.savingThenProbing', 'Saving settings, then probing max concurrency...')
        )) return;
        const maxLevel = Math.min(16, Math.max(1, parseInt(this._getVal('vlm-concurrent'), 10) || 8));
        // Probe at least up to 8 even if the field is currently 1–2.
        const probeMax = Math.max(8, maxLevel);
        try {
            const resp = await fetch('/api/vlm/probe-concurrency', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ max_level: probeMax, apply: true }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const reason = data.detail || data.error || data.message || resp.statusText || 'Unknown';
                this._showStatus('vlm-status', `Probe failed: ${reason}`, 'error');
                return;
            }
            const recommended = Number(data.recommended) || 0;
            if (recommended > 0) {
                this._setVal('vlm-concurrent', recommended);
                if (this.settings) this.settings.concurrent_requests = recommended;
            }
            const levels = Array.isArray(data.levels) ? data.levels : [];
            const detail = levels.map((row) => {
                const mark = row.success ? '✓' : '✗';
                return `${mark}${row.level}(${row.elapsed_ms || 0}ms)`;
            }).join(' ');
            const msg = data.status === 'ok'
                ? `${this._t('vlmSettings.probeOk', 'Max stable concurrency')}: ${recommended}${detail ? ` — ${detail}` : ''}`
                : (data.message || this._t('vlmSettings.probeFailed', 'Probe did not find a stable level'));
            this._showStatus('vlm-status', msg, data.status === 'ok' ? 'success' : 'error');
        } catch (e) {
            this._showStatus('vlm-status', `Probe error: ${e.message}`, 'error');
        }
    },

    async fetchModels() {
        if (!await this._saveBeforeAction('vlm-model-list-status', this._t('vlmSettings.savingThenFetching', 'Saving settings, then fetching models...'))) return;
        try {
            const resp = await fetch('/api/vlm/models', { method: 'POST' });
            const data = await resp.json().catch(() => ({}));
            const list = document.getElementById('vlm-model-list');
            if (!list) return;
            if (!resp.ok) {
                const reason = data.error || data.detail || resp.statusText || 'Unknown';
                list.innerHTML = `<span class="helper-text">${escapeHtml(reason)}</span>`;
                this._showStatus('vlm-model-list-status', `Failed: ${reason}`, 'error');
            } else if (data.models?.length) {
                list.innerHTML = data.models.map(m =>
                    `<button class="vlm-model-item" data-model="${escapeHtml(m)}">${escapeHtml(m)}</button>`
                ).join('');
                list.querySelectorAll('.vlm-model-item').forEach(btn => {
                    btn.addEventListener('click', () => {
                        this._setVal('vlm-model', btn.dataset.model);
                        this._showStatus('vlm-model-list-status', `Selected: ${btn.dataset.model}`, 'success');
                    });
                });
                this._showStatus('vlm-model-list-status', `${data.models.length} models found`, 'success');
            } else {
                list.innerHTML = '<span class="helper-text">No models found. Type model name manually above.</span>';
                this._showStatus('vlm-model-list-status', 'No models returned', 'info');
            }
        } catch (e) {
            this._showStatus('vlm-model-list-status', `Error: ${e.message}`, 'error');
        }
    },

    async applyPreset(presetId) {
        if (!presetId) return;
        try {
            const resp = await fetch('/api/vlm/presets');
            const data = await resp.json();
            const preset = data.presets?.[presetId];
            if (!preset) return;
            this._setVal('vlm-system-prompt', preset.system_prompt || '');
            // Always show the regular user_prompt in the textarea.
            // The with_tags variant is stored separately and used by the
            // backend when the image has existing tags.
            this._setVal('vlm-user-prompt', preset.user_prompt || '');
            // Store the with_tags prompt in a hidden field so it gets saved
            this._currentPresetWithTags = preset.user_prompt_with_tags || '';
            // Auto-set output_format to match the preset
            if (preset.output_format) {
                this._setOutputFormat(preset.output_format);
            }
            this._showStatus('vlm-status', `Applied preset: ${preset.name}`, 'success');
        } catch (e) {
            this._showStatus('vlm-status', `Error loading presets: ${e.message}`, 'error');
        }
    },

    // --- Local Model Management ---

    _populateModelSuggestions(models) {
        const datalist = document.getElementById('vlm-model-suggestions');
        if (!datalist) return;
        datalist.innerHTML = models.map((m) => {
            const id = String(m?.id || '').trim();
            if (!id) return '';
            const hintParts = [];
            if (m?.name) hintParts.push(String(m.name));
            if (m?.size_gb) hintParts.push(`${m.size_gb} GB`);
            const hint = hintParts.join(' · ');
            return `<option value="${escapeHtml(id)}">${escapeHtml(hint)}</option>`;
        }).join('');
    },

    async loadRecommendedModels() {
        const container = document.getElementById('vlm-local-models');
        if (!container) return;
        try {
            const resp = await fetch('/api/vlm/local-models/recommended');
            const data = await resp.json();

            // MODELS-03: feed the #vlm-model input's typeahead datalist from the
            // recommended set so the free-text box offers a dropdown of known
            // models (id + short hint) without removing the ability to type any
            // model name. Populated regardless of Ollama state — useful for both
            // local and cloud endpoints.
            this._populateModelSuggestions(Array.isArray(data.models) ? data.models : []);

            if (!data.ollama_installed) {
                container.innerHTML = `<div class="vlm-ollama-install">
                    <p>Ollama not installed. Required for local VLM models.</p>
                    <p class="helper-text">${escapeHtml(data.install_instructions || '')}</p>
                </div>`;
                return;
            }

            let html = '';
            if (!data.ollama_running) {
                html += `<div class="vlm-ollama-start">
                    <span>Ollama is not running.</span>
                    <button class="btn btn-small btn-primary" id="btn-vlm-start-ollama">Start Ollama</button>
                </div>`;
            }

            html += '<div class="vlm-model-cards">';
            for (const m of data.models) {
                const installed = m.installed ? ' installed' : '';
                html += `<div class="vlm-model-card${installed}">
                    <div class="vlm-model-card-header">
                        <strong>${escapeHtml(m.name)}</strong>
                        ${m.nsfw_ok ? '<span class="vlm-badge-nsfw">NSFW OK</span>' : ''}
                        ${m.installed ? '<span class="vlm-badge-installed"><svg class="icon" aria-hidden="true"><use href="#i-check"/></svg> Installed</span>' : ''}
                    </div>
                    <p class="helper-text">${escapeHtml(m.description)}</p>
                    <div class="vlm-model-card-meta">
                        <span>${m.size_gb} GB</span> · <span>Min ${m.vram_min_gb} GB VRAM</span>
                    </div>
                    <div class="vlm-model-card-actions">
                        ${m.installed
                            ? `<button class="btn btn-small btn-ghost" data-vlm-use="${escapeHtml(m.id)}">${escapeHtml(this._t('vlmSettings.useModel', 'Use This'))}</button>
                               <button class="btn btn-small btn-ghost danger" data-vlm-delete="${escapeHtml(m.id)}">${escapeHtml(this._t('vlmSettings.deleteModel', 'Delete'))}</button>`
                            : `<button class="btn btn-small btn-primary" data-vlm-pull="${escapeHtml(m.id)}">${escapeHtml(this._t('vlmSettings.downloadModel', 'Download'))}</button>`
                        }
                    </div>
                </div>`;
            }
            html += '</div>';
            container.innerHTML = html;

            // Bind events
            document.getElementById('btn-vlm-start-ollama')?.addEventListener('click', () => this.startOllama());
            container.querySelectorAll('[data-vlm-pull]').forEach(btn => {
                btn.addEventListener('click', () => this.pullModel(btn.dataset.vlmPull));
            });
            container.querySelectorAll('[data-vlm-use]').forEach(btn => {
                btn.addEventListener('click', () => {
                    this._setVal('vlm-model', btn.dataset.vlmUse);
                    this._setVal('vlm-endpoint', 'http://localhost:11434/v1');
                    this._setVal('vlm-provider', 'openai_compat');
                    this._showStatus(
                        'vlm-status',
                        this._t('vlmSettings.modelSelected', 'Selected {model} — endpoint set to Ollama')
                            .replace('{model}', btn.dataset.vlmUse),
                        'success'
                    );
                });
            });
            container.querySelectorAll('[data-vlm-delete]').forEach(btn => {
                btn.addEventListener('click', () => this.confirmDeleteModel(btn.dataset.vlmDelete, btn));
            });
        } catch (e) {
            container.innerHTML = `<p class="helper-text">Failed to load models: ${escapeHtml(e.message)}</p>`;
        }
    },

    confirmDeleteModel(modelName, trigger = null) {
        const normalized = String(modelName || '').trim();
        if (!normalized) return;

        const title = this._t('vlmSettings.deleteModelTitle', 'Delete local model?');
        const message = this._t(
            'vlmSettings.deleteModelConfirm',
            'Delete {model} from Ollama? You can download it again later.'
        ).replace('{model}', normalized);
        const runDelete = () => { void this.deleteModel(normalized, trigger); };

        if (typeof window.App?.showConfirm === 'function') {
            window.App.showConfirm(title, message, runDelete);
        } else if (window.confirm(message)) {
            runDelete();
        }
    },

    async deleteModel(modelName, trigger = null) {
        const normalized = String(modelName || '').trim();
        if (!normalized) return;

        if (trigger) {
            trigger.disabled = true;
            trigger.textContent = this._t('vlmSettings.deletingModel', 'Deleting...');
        }
        this._showStatus(
            'vlm-status',
            this._t('vlmSettings.deletingModelNamed', 'Deleting {model}...').replace('{model}', normalized),
            'info'
        );

        try {
            const resp = await fetch('/api/vlm/local-models/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: normalized }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                this._showStatus(
                    'vlm-status',
                    this._t('vlmSettings.deleteModelFailed', 'Delete failed: {reason}')
                        .replace('{reason}', err.detail || resp.statusText),
                    'error'
                );
                if (trigger) {
                    trigger.disabled = false;
                    trigger.textContent = this._t('vlmSettings.deleteModel', 'Delete');
                }
                return;
            }
            if (this._getVal('vlm-model') === normalized) {
                this._setVal('vlm-model', '');
            }
            this._showStatus(
                'vlm-status',
                this._t('vlmSettings.deleteModelDone', 'Deleted {model}.').replace('{model}', normalized),
                'success'
            );
            await this.loadRecommendedModels();
        } catch (e) {
            this._showStatus(
                'vlm-status',
                this._t('vlmSettings.deleteModelFailed', 'Delete failed: {reason}')
                    .replace('{reason}', e.message || e),
                'error'
            );
            if (trigger) {
                trigger.disabled = false;
                trigger.textContent = this._t('vlmSettings.deleteModel', 'Delete');
            }
        }
    },

    async pullModel(modelName) {
        if (!modelName) modelName = this._getVal('vlm-model');
        if (!modelName) {
            this._showStatus('vlm-status', 'Enter a model name first', 'error');
            return;
        }

        try {
            const resp = await fetch('/api/vlm/local-models/pull', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: modelName }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                this._showStatus('vlm-status', `Pull failed: ${err.detail || resp.statusText}`, 'error');
                return;
            }
            this._showStatus('vlm-status', `Downloading ${modelName}...`, 'info');
            this._startPullPolling();
        } catch (e) {
            this._showStatus('vlm-status', `Error: ${e.message}`, 'error');
        }
    },

    _pullPollInterval: null,
    _startPullPolling() {
        if (this._pullPollInterval) return;
        this._pullPollInterval = setInterval(async () => {
            try {
                const resp = await fetch('/api/vlm/local-models/pull/progress');
                const data = await resp.json();
                if (data.pulling) {
                    this._showStatus('vlm-status', `Downloading: ${data.percent}% — ${data.status}`, 'info');
                } else {
                    clearInterval(this._pullPollInterval);
                    this._pullPollInterval = null;
                    if (data.status?.startsWith('error')) {
                        this._showStatus('vlm-status', `Download failed: ${data.status}`, 'error');
                    } else {
                        this._showStatus('vlm-status', `Download complete ✓`, 'success');
                        this.loadRecommendedModels();
                    }
                }
            } catch (e) {
                clearInterval(this._pullPollInterval);
                this._pullPollInterval = null;
            }
        }, 2000);
    },

    async startOllama() {
        try {
            const resp = await fetch('/api/vlm/local-models/start-ollama', { method: 'POST' });
            if (resp.ok) {
                this._showStatus('vlm-status', 'Ollama started ✓', 'success');
                setTimeout(() => this.loadRecommendedModels(), 2000);
            } else {
                const err = await resp.json().catch(() => ({}));
                this._showStatus('vlm-status', `Cannot start Ollama: ${err.detail || ''}`, 'error');
            }
        } catch (e) {
            this._showStatus('vlm-status', `Error: ${e.message}`, 'error');
        }
    },

});
