/**
 * image-reader/render.js — image-reader.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/image-reader.js pre-cut
 * lines 209-213 + 691-804 + 1359-1489 (of 1,749): _formatGeneratorLabel,
 * _cleanModelName (its doc comment travels with it), _toggleFormat,
 * _buildGalleryPromptContext, _buildPromptView (window.Gallery._buildPromptView
 * delegate w/ fallback), _renderPromptSection (its method-local `t` helper
 * stays inside — it deliberately shadows this._t), _renderQuickFacts and
 * _renderResult (same method-local `t` note; top-level render orchestrator).
 * Classic script: joins the ONE unsealed window.ImageReader object declared in
 * image-reader/core.js, which loads FIRST; index.html lists the family in
 * original line order (boot.js invokes init() LAST).
 */
'use strict';

Object.assign(window.ImageReader, {
        _formatGeneratorLabel(generator) {
            return window.App?.formatGeneratorLabel?.(generator, 'Unknown')
                || String(generator || 'unknown');
        },

        /**
         * Strip path prefix and file extension from model name.
         * "Anima\\anime\\name.safetensors" → "name"
         */
        _cleanModelName(fullName) {
            if (!fullName) return '';
            let name = fullName.replace(/\\/g, '/').split('/').pop() || fullName;
            name = name.replace(/\.(safetensors|ckpt|pt|pth|bin)$/i, '');
            return name;
        },

        _toggleFormat() {
            const formats = ['original', 'sd', 'nai'];
            const idx = formats.indexOf(this._promptFormat);
            this._promptFormat = formats[(idx + 1) % formats.length];
            this._updateFormatButton();

            if (this._currentResult) {
                this._renderPromptSection(this._currentResult);
            }
        },

        _buildGalleryPromptContext(result) {
            const metadata = result?.metadata && typeof result.metadata === 'object'
                ? result.metadata
                : {};
            return {
                image: {
                    generator: result?.generator || 'unknown',
                    prompt: result?.prompt || '',
                    negative_prompt: result?.negative_prompt || '',
                    checkpoint: result?.checkpoint || '',
                    metadata_json: metadata,
                },
                parsedData: metadata?._parsed || {
                    generation_params: {},
                    is_img2img: false,
                    img2img_info: {},
                    character_prompts: [],
                    prompt_nodes: [],
                },
            };
        },

        _buildPromptView(result, targetFormat) {
            const gallery = window.Gallery;
            const { image, parsedData } = this._buildGalleryPromptContext(result);

            if (gallery && typeof gallery._buildPromptView === 'function') {
                return gallery._buildPromptView(image, parsedData, targetFormat);
            }

            return {
                promptText: result?.prompt || '',
                negativeText: result?.negative_prompt || '',
                targetFormat: targetFormat || 'original',
                formatLabel: targetFormat || 'Original',
            };
        },

        _renderPromptSection(result, options = {}) {
            const t = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const promptView = this._buildPromptView(result, this._promptFormat);
            const clipboardMetadataMissing = Boolean(options.clipboardMetadataMissing);
            const promptText = clipboardMetadataMissing
                ? t(
                    'reader.clipboardWarningPromptFallback',
                    'This clipboard image likely lost the original image info. Open the original PNG to read the full prompt.',
                )
                : (promptView?.promptText || t('reader.noPrompt', 'No prompt found in this image'));
            this._setText('reader-prompt-text', promptText);
            this._setText('reader-negative-text', promptView?.negativeText || t('reader.noNegative', 'No negative prompt'));

            const negSection = document.getElementById('reader-negative-section');
            if (negSection) {
                negSection.style.display = (!clipboardMetadataMissing && promptView?.negativeText) ? '' : 'none';
            }
            this._renderCharacterPrompts(this._currentResult || result, { clipboardMetadataMissing });
        },

        _renderCharacterPrompts(result, options = {}) {
            const section = document.getElementById('reader-characters-section');
            const list = document.getElementById('reader-characters-list');
            if (!section || !list) return;
            if (options.clipboardMetadataMissing) {
                section.style.display = 'none';
                list.innerHTML = '';
                return;
            }
            const parsed = (result?.metadata && typeof result.metadata === 'object')
                ? (result.metadata._parsed || {})
                : {};
            const chars = Array.isArray(parsed.character_prompts) ? parsed.character_prompts : [];
            if (!chars.length) {
                section.style.display = 'none';
                list.innerHTML = '';
                return;
            }
            const characterLabel = this._t('modal.character', 'Character');
            const negLabel = this._t('modal.negativeShort', 'Neg');
            section.style.display = '';
            list.innerHTML = chars.map((entry, index) => {
                const prompt = String(entry?.prompt || '').trim();
                const negative = String(entry?.negative_prompt || '').trim();
                const center = entry?.center;
                const centerStr = center
                    ? ` (${center.x?.toFixed?.(2) || center.x}, ${center.y?.toFixed?.(2) || center.y})`
                    : '';
                const slot = entry?.index != null ? entry.index + 1 : index + 1;
                const negHtml = negative
                    ? `<div class="char-negative"><strong>${this._escapeHtml(negLabel)}:</strong> ${this._escapeHtml(negative)}</div>`
                    : '';
                return `
                    <div class="character-card">
                        <div class="character-card-header">${this._escapeHtml(characterLabel)} ${slot}${this._escapeHtml(centerStr)}</div>
                        <div>${this._escapeHtml(prompt)}</div>
                        ${negHtml}
                    </div>
                `;
            }).join('');
        },

        _renderQuickFacts(result, gp, options = {}) {
            const container = document.getElementById('reader-quick-facts');
            if (!container) return;

            const facts = [];
            const addFact = (labelKey, fallback, value, title = '') => {
                const clean = String(value ?? '').trim();
                if (!clean || clean === '-') return;
                facts.push({
                    label: this._t(labelKey, fallback),
                    value: clean,
                    title: title || clean,
                });
            };

            addFact('reader.checkpoint', 'Checkpoint', options.checkpoint || result?.checkpoint || gp?.model, options.checkpointRaw || '');
            if (result?.width && result?.height) {
                addFact('reader.editSize', 'Size', `${result.width}x${result.height}`);
            } else {
                addFact('reader.editSize', 'Size', gp?.size);
            }
            addFact('reader.editSeed', 'Seed', gp?.seed ?? gp?.noise_seed);
            addFact('reader.editSteps', 'Steps', gp?.steps);
            addFact('reader.editCfg', 'CFG', gp?.cfg_scale ?? gp?.cfg ?? gp?.['CFG scale']);
            addFact('reader.editSampler', 'Sampler', gp?.sampler || gp?.sampler_name);

            container.hidden = facts.length === 0;
            container.innerHTML = facts.map((fact) => `
                <span class="reader-quick-fact" title="${this._escapeHtml(fact.title)}">
                    <span class="reader-quick-fact-label">${this._escapeHtml(fact.label)}</span>
                    <span class="reader-quick-fact-value">${this._escapeHtml(fact.value)}</span>
                </span>
            `).join('');
        },

        _renderResult(result, filename, options = {}) {
            const t = (key, fallback) => { const v = window.I18n?.t?.(key); return (v && v !== key) ? v : fallback; };
            const gp = this._getGenParams(result);
            const resetFormat = options.resetFormat !== false;

            // Generator badge
            const genEl = document.getElementById('reader-generator');
            if (genEl) {
                const gen = String(result.generator || 'unknown').toLowerCase();
                genEl.textContent = this._formatGeneratorLabel(gen);
                genEl.className = `reader-generator-badge gen-${gen}`;
            }

            // File info
            const infoEl = document.getElementById('reader-file-info');
            if (infoEl) {
                const parts = [filename];
                if (result.width && result.height) parts.push(`${result.width}×${result.height}`);
                if (result.file_size) parts.push(this._formatSize(result.file_size));
                infoEl.textContent = parts.join(' • ');
            }

            // Prompt — use format-aware rendering
            if (resetFormat) {
                this._promptFormat = 'original';
            }
            this._updateFormatButton();
            const clipboardMetadataMissing = this._clipboardMetadataMissing(result, options.sourceKind || this._currentSourceKind);
            this._renderPromptSection(result, { clipboardMetadataMissing });
            // Library opens render category tags from openLibraryImage instead,
            // after _currentLibraryImageId/_currentReaderTags are set — rendering
            // here too would POST /api/prompts/categorize twice per open.
            if (options.sourceKind !== 'library') {
                this._renderReaderCategoryTags(result);
            }

            // Checkpoint — strip path, show clean name, tooltip for full path
            const cpRaw = result.checkpoint || gp.model || '';
            const cpClean = this._cleanModelName(cpRaw);
            this._renderQuickFacts(result, gp, { checkpoint: cpClean, checkpointRaw: cpRaw });
            const cpEl = document.getElementById('reader-checkpoint');
            if (cpEl) {
                cpEl.textContent = cpClean || '-';
                cpEl.title = cpRaw || '';
            }

            // Model Hash — hide entirely for ComfyUI (no hashes available)
            const hashRow = document.querySelector('.reader-hash-row');
            const allHashes = this._getAllHashes(result);
            const hasAnyHash = Object.keys(allHashes).length > 0;
            if (hashRow) {
                if (hasAnyHash && allHashes.model) {
                    const hashEl = document.getElementById('reader-model-hash');
                    if (hashEl) hashEl.textContent = allHashes.model;
                    hashRow.style.display = '';
                } else {
                    hashRow.style.display = 'none';
                }
            }

            // LoRAs — strip paths, show clean names, tooltip for full path
            const lorasEl = document.getElementById('reader-loras');
            const loras = this._getLoras(result);

            if (lorasEl) {
                if (loras.length > 0) {
                    lorasEl.innerHTML = loras.map(l => {
                        const clean = this._cleanModelName(l);
                        const hash = allHashes[`lora:${l}`] || allHashes[`lora:${clean}`] || '';
                        const searchQuery = hash || clean;
                        const searchUrl = `https://civitai.com/search/models?sortBy=models_v9&query=${encodeURIComponent(searchQuery)}`;
                        const hashBadge = hash ? ` <span class="reader-hash-badge" title="${this._escapeHtml(hash)}">${this._escapeHtml(hash.slice(0, 10))}</span>` : '';
                        return `<a href="${searchUrl}" target="_blank" rel="noopener" class="reader-lora-tag" title="${this._escapeHtml(l)}">${this._escapeHtml(clean)}${hashBadge}</a>`;
                    }).join('');
                } else {
                    lorasEl.textContent = t('reader.noLoras', 'No LoRAs detected');
                }
            }

            // All Hashes section — only show if there are hashes (WebUI images)
            const hashesEl = document.getElementById('reader-hashes');
            if (hashesEl) {
                const hashEntries = Object.entries(allHashes);
                const hashSection = document.getElementById('reader-hashes-section');
                if (hashEntries.length > 0) {
                    hashesEl.innerHTML = hashEntries.map(([name, hash]) => {
                        const searchUrl = `https://civitai.com/search/models?sortBy=models_v9&query=${encodeURIComponent(hash)}`;
                        return `<div class="reader-hash-entry">
                            <span class="reader-hash-name">${this._escapeHtml(name)}</span>
                            <a href="${searchUrl}" target="_blank" rel="noopener" class="reader-hash-value" title="Search on Civitai">${this._escapeHtml(hash)}</a>
                        </div>`;
                    }).join('');
                    if (hashSection) hashSection.style.display = '';
                } else {
                    if (hashSection) hashSection.style.display = 'none';
                }
            }

            // Generation params
            const paramsEl = document.getElementById('reader-params');
            if (paramsEl) {
                // Filter out hash fields (shown separately) and empty values
                const skipKeys = new Set(['model_hash', 'lora_hashes', 'ti_hashes', 'Lora hashes', 'TI hashes']);
                const paramPairs = Object.entries(gp)
                    .filter(([k, v]) => v != null && v !== '' && !skipKeys.has(k))
                    .map(([k, v]) => `<div class="reader-param"><span class="reader-param-key">${this._escapeHtml(k)}</span><span class="reader-param-val">${this._escapeHtml(String(v))}</span></div>`);

                if (paramPairs.length > 0) {
                    paramsEl.innerHTML = paramPairs.join('');
                } else {
                    paramsEl.textContent = clipboardMetadataMissing
                        ? t(
                            'reader.clipboardWarningParamsFallback',
                            'Clipboard image likely lost SD generation parameters. Open the original PNG file to inspect them.',
                        )
                        : t('reader.noParams', 'No generation parameters');
                }
            }

            this._populateMetadataEditor(result);
            this._renderModelAssetsSection(result);

            const negativeSection = document.getElementById('reader-negative-section');
            if (negativeSection && !String(result.negative_prompt || '').trim()) {
                this._collapsedState.negative = false;
            }

            this._renderReaderColorDistribution();
            this._syncSectionStates();
        },

});
