/**
 * gallery/modal-sections.js — gallery.js decomposition (verbatim Object.assign mixin).
 * Method bodies moved BYTE-IDENTICAL from frontend/js/gallery.js pre-cut
 * lines 2661-3226 (of 4,708): model assets + prompt view apply + modal sections/toggles + tags render.
 * Classic script: joins the ONE unsealed window.Gallery object declared in
 * gallery/base.js, which loads FIRST (the shared consts + module helpers
 * live there); index.html lists the family in original line order.
 */
Object.assign(window.Gallery, {
    _renderModalModelAssets(parsedData) {
        const section = document.querySelector('#modal-model-assets-section');
        const grid = document.querySelector('#modal-model-assets-grid');
        if (!section || !grid) return;

        const assets = parsedData?.model_assets || null;
        const hasAssets = assets && (
            assets.primary_model_name ||
            (assets.loras && assets.loras.length) ||
            (assets.yolo_models && assets.yolo_models.length) ||
            (assets.checkpoint_candidates && assets.checkpoint_candidates.length) ||
            (assets.unet_candidates && assets.unet_candidates.length) ||
            (assets.vae_candidates && assets.vae_candidates.length) ||
            (assets.clip_candidates && assets.clip_candidates.length) ||
            (assets.diffusion_model_candidates && assets.diffusion_model_candidates.length) ||
            (assets.model_candidates && assets.model_candidates.length) ||
            (assets.yolo_candidates && assets.yolo_candidates.length) ||
            (assets.global_lora_candidates && assets.global_lora_candidates.length) ||
            (assets.global_yolo_candidates && assets.global_yolo_candidates.length)
        );

        if (!hasAssets) {
            section.style.display = 'none';
            grid.innerHTML = '';
            return;
        }

        const t = (key, fallback) => this._t(key, null, fallback);
        const blocks = [];
        const humanizeSource = (value) => {
            if (!value) return '';
            if (value === 'activity_subgraph_fallback') return t('modal.modelAssetsSourceActivity', 'Active subgraph fallback');
            if (value === 'global_candidate_fallback') return t('modal.modelAssetsSourceGlobal', 'Global candidate fallback');
            if (value === 'global_graph_fallback') return t('modal.modelAssetsSourceGraph', 'Full graph fallback');
            if (value === 'fast_path') return t('modal.modelAssetsSourceFastPath', 'Fast path');
            return String(value).replace(/_/g, ' ');
        };
        const humanizeConfidence = (value) => {
            if (value === 'high') return t('modal.modelAssetsConfidenceHigh', 'High confidence');
            if (value === 'medium') return t('modal.modelAssetsConfidenceMedium', 'Medium confidence');
            if (value === 'low') return t('modal.modelAssetsConfidenceLow', 'Low confidence');
            return '';
        };
        const addListBlock = (label, values) => {
            if (!Array.isArray(values) || values.length === 0) return;
            const uniqueValues = [...new Set(values.map((value) => String(value).trim()).filter(Boolean))];
            if (!uniqueValues.length) return;
            blocks.push(`
                <div class="model-asset-block">
                    <div class="param-item"><span class="param-label">${window.escapeHtml(label)}</span></div>
                    <div class="model-asset-list">
                        ${uniqueValues.map((value) => `<span class="model-asset-pill">${window.escapeHtml(value)}</span>`).join('')}
                    </div>
                </div>
            `);
        };
        const addCandidateBlock = (label, items) => {
            if (!Array.isArray(items) || items.length === 0) return;
            const uniqueItems = [];
            const seenNames = new Set();
            for (const item of items) {
                const name = String(item?.name || '').trim();
                if (!name || seenNames.has(name)) continue;
                seenNames.add(name);
                uniqueItems.push(item);
            }
            if (!uniqueItems.length) return;

            blocks.push(`
                <div class="model-asset-block model-asset-block-secondary">
                    <div class="param-item"><span class="param-label">${window.escapeHtml(label)}</span></div>
                    <div class="model-asset-candidate-list">
                        ${uniqueItems.map((item) => {
                            const metaParts = [
                                humanizeSource(item?.source_mode),
                                item?.node_id ? `${t('modal.modelAssetsNode', 'Node')} ${item.node_id}` : '',
                                item?.class_type ? String(item.class_type) : '',
                                item?.key_path ? String(item.key_path) : (item?.input_key ? String(item.input_key) : ''),
                            ].filter(Boolean);
                            const confidence = String(item?.confidence || 'low').toLowerCase();
                            return `
                                <div class="model-asset-candidate model-asset-candidate-secondary">
                                    <div class="model-asset-candidate-head">
                                        <span class="model-asset-pill">${window.escapeHtml(String(item?.name || ''))}</span>
                                        <span class="model-asset-confidence is-${window.escapeHtml(confidence)}">${window.escapeHtml(humanizeConfidence(confidence))}</span>
                                    </div>
                                    <div class="model-asset-candidate-meta">${window.escapeHtml(metaParts.join(' • '))}</div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `);
        };

        if (assets.primary_model_name) {
            const primaryModelType = assets.primary_model_type || t('generator.unknown', 'Unknown');
            blocks.push(`
                <div class="model-asset-block">
                    <div class="param-item"><span class="param-label">${window.escapeHtml(t('modal.primaryModel', 'Primary Model'))}</span><span class="param-value">${window.escapeHtml(assets.primary_model_name)}</span></div>
                    <div class="param-item"><span class="param-label">${window.escapeHtml(t('modal.primaryModelType', 'Primary Model Type'))}</span><span class="param-value">${window.escapeHtml(primaryModelType)}</span></div>
                </div>
            `);
        }

        if (assets.source) {
            blocks.push(`
                <div class="model-asset-block">
                    <div class="param-item"><span class="param-label">${window.escapeHtml(t('modal.modelAssetsSource', 'Parser Source'))}</span><span class="param-value">${window.escapeHtml(humanizeSource(assets.source))}</span></div>
                </div>
            `);
        }
        if (Array.isArray(assets.sources) && assets.sources.length > 1) {
            addListBlock(t('modal.modelAssetsSources', 'All Sources'), assets.sources.map((value) => humanizeSource(value)));
        }

        addListBlock(t('modal.modelAssetsCheckpoints', 'Checkpoint Candidates'), (assets.checkpoint_candidates || []).map((item) => item.name));
        addListBlock(t('modal.modelAssetsUnets', 'UNet Candidates'), (assets.unet_candidates || []).map((item) => item.name));
        addListBlock(t('modal.modelAssetsVae', 'VAE'), (assets.vae_candidates || []).map((item) => item.name));
        addListBlock(t('modal.modelAssetsClip', 'CLIP / Text Encoder'), (assets.clip_candidates || []).map((item) => item.name));
        addListBlock(t('modal.modelAssetsDiffusion', 'Diffusion Candidates'), (assets.diffusion_model_candidates || []).map((item) => item.name));
        addListBlock(t('modal.modelAssetsModels', 'Additional / Upscale / ControlNet Models'), (assets.model_candidates || []).map((item) => item.name));

        const loraDetails = parsedData?.generation_params?.lora_details || [];
        const loraDetailMap = new Map();
        for (const detail of loraDetails) {
            if (detail?.name) loraDetailMap.set(detail.name, detail);
        }
        const loraNames = (assets.lora_candidates || []).map((item) => {
            const detail = loraDetailMap.get(item.name);
            if (detail && typeof detail.strength_model === 'number') {
                const sm = detail.strength_model;
                const sc = detail.strength_clip;
                if (typeof sc === 'number' && sc !== sm) {
                    return `${item.name}  (M:${sm} / C:${sc})`;
                }
                return `${item.name}  (${sm})`;
            }
            return item.name;
        });
        addListBlock(t('modal.modelAssetsLoras', 'LoRAs'), loraNames);
        addListBlock(t('modal.modelAssetsYolo', 'YOLO / Detector Models'), assets.yolo_models || (assets.yolo_candidates || []).map((item) => item.name));
        addCandidateBlock(t('modal.modelAssetsGlobalLoras', 'Global LoRA Candidates'), assets.global_lora_candidates || []);
        addCandidateBlock(t('modal.modelAssetsGlobalYolo', 'Full-graph YOLO Candidates'), assets.global_yolo_candidates || []);

        grid.innerHTML = blocks.join('');
        section.style.display = '';
    },

    _applyModalPromptView(promptView) {
        const promptText = document.querySelector('#modal-prompt-text');
        const negSection = document.querySelector('#modal-negative-section');
        const negText = document.querySelector('#modal-negative-text');
        const promptHeader = document.querySelector('.modal-prompt h4');
        const toggleBtn = document.querySelector('#btn-toggle-prompt-format');
        const alternateTarget = this._getAlternatePromptTarget(promptView.sourceFormat);

        if (promptText) {
            // Metadata L3: a ComfyUI/unknown image with no prompt usually
            // means the graph's text lives at runtime (wildcards, dynamic
            // prompts) or was stripped — say so instead of a bare "No prompt".
            const generator = String(this._lastModalImage?.generator || '').toLowerCase();
            const isUnrecoverable = !promptView.promptText
                && (generator === 'comfyui' || generator === 'unknown');
            promptText.textContent = promptView.promptText
                || (isUnrecoverable
                    ? this._t('modal.promptUnrecoverable', null, 'No prompt could be recovered from this file — it may be generated at runtime (wildcards / dynamic prompts) or stripped on export.')
                    : this._t('modal.noPrompt', null, 'No prompt'));
            promptText.classList.toggle('prompt-unrecoverable-note', isUnrecoverable);
        }
        if (negText) {
            negText.textContent = promptView.negativeText || '-';
        }
        if (negSection) {
            negSection.style.display = promptView.negativeText ? '' : 'none';
        }
        this._applyModalSidecarCaption(this._lastModalImage);
        if (promptHeader) {
            const fallbackLabel = promptView.targetFormat === 'original'
                ? 'Prompt (Original format)'
                : `Prompt (${promptView.formatLabel} format)`;
            // Write into the label span so the collapse icon survives.
            const headerLabel = promptHeader.querySelector('.section-toggle-label');
            (headerLabel || promptHeader).textContent = this._t(promptView.headerKey || 'modal.prompt', null, fallbackLabel);
        }
        if (toggleBtn) {
            const hasPrompt = !!(promptView.promptText || promptView.negativeText || (promptView.characterPrompts && promptView.characterPrompts.length));
            toggleBtn.disabled = !hasPrompt || (promptView.targetFormat === 'original' && !alternateTarget);
            if (!hasPrompt) {
                toggleBtn.textContent = this._t('modal.noPrompt', null, 'No prompt');
            } else if (promptView.targetFormat === 'original') {
                if (alternateTarget === 'sd') {
                    toggleBtn.textContent = this._t('modal.viewAsSD', null, 'View as SD format');
                } else if (alternateTarget === 'nai') {
                    toggleBtn.textContent = this._t('modal.viewAsNAI', null, 'View as NAI format');
                } else {
                    toggleBtn.textContent = this._t('modal.promptOriginal', null, 'Original format');
                }
            } else {
                toggleBtn.textContent = this._t('modal.viewOriginal', null, 'View original format');
            }
            toggleBtn.title = toggleBtn.textContent;
            toggleBtn.setAttribute('aria-label', toggleBtn.textContent);
        }
        this._modalPromptView = promptView;
    },

    /**
     * Show text recovered from a .txt/.json sidecar beside the prompt block.
     *
     * `sidecar_caption` (migration 042) exists precisely so this text stops
     * masquerading as an SD prompt, so it gets its own labelled section rather
     * than being appended to one the user reads as generation parameters. The
     * gallery-grid column list deliberately omits the field to keep list rows
     * small, so the modal is the only place it can be read.
     */
    _applyModalSidecarCaption(image) {
        const section = document.querySelector('#modal-sidecar-caption-section');
        const text = document.querySelector('#modal-sidecar-caption-text');
        if (!section || !text) return;

        const caption = String(image?.sidecar_caption || '').trim();
        text.textContent = caption || '-';
        section.style.display = caption ? '' : 'none';
    },

    _togglePromptFormat() {
        const view = this._getModalPromptView();
        if (!view || !this._lastModalImage || !this._lastParsedData) return;

        const alternateTarget = this._getAlternatePromptTarget(view.sourceFormat);
        const nextFormat = view.targetFormat === 'original'
            ? alternateTarget
            : 'original';

        if (!nextFormat) return;
        this._applyModalPromptView(this._buildPromptView(this._lastModalImage, this._lastParsedData, nextFormat));
    },

    _renderModalSections(image, parsedData) {
        const $ = (s) => document.querySelector(s);
        // escapeHtml is now available globally from modules/utils/escape.js

        // --- Checkpoint ---
        const cpItem = $('#modal-checkpoint-item');
        const cpText = $('#modal-checkpoint');
        if (image.checkpoint) {
            const checkpointFilterValue = window.App?.normalizeCheckpointFilterValue?.(
                image.checkpoint_normalized || image.checkpoint
            ) || '';
            cpItem.style.display = '';
            cpText.textContent = image.checkpoint;
            // Make checkpoint clickable to filter
            cpText.classList.add('modal-checkpoint-clickable');
            cpText.style.cursor = 'pointer';
            cpText.onclick = () => {
                const AppState = window.App?.AppState;
                if (AppState && checkpointFilterValue) {
                    if (!AppState.filters.checkpoints.includes(checkpointFilterValue)) {
                        window.App?.updateFilters?.((filters) => {
                            filters.checkpoints = [...filters.checkpoints, checkpointFilterValue];
                        });
                    }
                    const closeModal = window.App?.closeModal || window.closeModal;
                    closeModal?.('image-modal');
                    window.App?.updateFilterSummary?.();
                    window.App?.loadImages?.();
                }
            };
        } else {
            cpItem.style.display = 'none';
        }

        // --- Aesthetic Score ---
        const aeItem = $('#modal-aesthetic-item');
        const aeText = $('#modal-aesthetic-score');
        if (aeItem && aeText) {
            if (image.aesthetic_score != null) {
                aeItem.style.display = '';
                aeText.textContent = `${Number(image.aesthetic_score).toFixed(2)} / 10`;
                aeText.style.color = image.aesthetic_score >= 6 ? '#22c55e' : image.aesthetic_score >= 4 ? '#f59e0b' : '#ef4444';
            } else {
                aeItem.style.display = 'none';
            }
        }

        // --- img2img Badge ---
        const img2imgBadge = $('#modal-img2img-badge');
        if (parsedData.is_img2img) {
            img2imgBadge.style.display = '';
        } else {
            img2imgBadge.style.display = 'none';
        }

        // --- Key Generation Parameters (always visible bar) ---
        const keyParamsBar = $('#modal-key-params');
        const gp = parsedData.generation_params || {};
        const keyParamMap = {
            'kp-steps': gp.steps,
            'kp-sampler': gp.sampler || gp.sampler_name,
            'kp-scheduler': gp.scheduler || gp.noise_schedule || gp.schedule_type,
            'kp-cfg': gp.cfg_scale ?? gp.cfg ?? gp.scale,
            'kp-seed': gp.seed,
            'kp-denoise': gp.denoise ?? gp.denoising_strength ?? gp.strength,
        };
        let hasAnyKeyParam = false;
        for (const [elemId, val] of Object.entries(keyParamMap)) {
            const el = $(`#${elemId}`);
            if (el) {
                if (val != null && val !== '') {
                    el.style.display = '';
                    const valSpan = el.querySelector('span');
                    if (valSpan) {
                        valSpan.textContent = typeof val === 'number'
                            ? (Number.isInteger(val) ? val : val.toFixed(4).replace(/0+$/, '').replace(/\.$/, ''))
                            : String(val);
                    }
                    hasAnyKeyParam = true;
                } else {
                    el.style.display = 'none';
                }
            }
        }
        keyParamsBar.style.display = hasAnyKeyParam ? '' : 'none';

        // --- LoRAs ---
        const lorasSection = $('#modal-loras-section');
        const lorasList = $('#modal-loras-list');
        let loras = [];
        if (image.loras) {
            try {
                loras = typeof image.loras === 'string' ? JSON.parse(image.loras) : image.loras;
            } catch (_) { /* ignore */ }
        }
        if (Array.isArray(loras) && loras.length > 0) {
            lorasSection.style.display = '';
            lorasList.innerHTML = loras.map(l => `<span class="lora-pill modal-lora-clickable" data-lora="${window.escapeHtml(l)}">${window.escapeHtml(l)}</span>`).join('');
            // Attach click handlers to filter by LoRA
            lorasList.querySelectorAll('.modal-lora-clickable').forEach(el => {
                el.addEventListener('click', () => {
                    const lora = el.dataset.lora;
                    const AppState = window.App?.AppState;
                    if (AppState && lora) {
                        if (!AppState.filters.loras.includes(lora)) {
                            window.App?.updateFilters?.((filters) => {
                                filters.loras = [...filters.loras, lora];
                            });
                        }
                        const closeModal = window.App?.closeModal || window.closeModal;
                        closeModal?.('image-modal');
                        window.App?.updateFilterSummary?.();
                        window.App?.loadImages?.();
                    }
                });
            });
        } else {
            lorasSection.style.display = 'none';
            lorasList.innerHTML = '';
        }

        // --- Negative Prompt ---
        const negSection = $('#modal-negative-section');
        const negText = $('#modal-negative-text');
        if (image.negative_prompt) {
            negSection.style.display = '';
            negText.textContent = image.negative_prompt;
            negText.style.display = '';
        } else {
            negSection.style.display = 'none';
        }

        // --- Character Prompts (NAI V4) ---
        const charsSection = $('#modal-characters-section');
        const charsList = $('#modal-characters-list');
        if (parsedData.character_prompts && parsedData.character_prompts.length > 0) {
            const characterLabel = window.escapeHtml(this._t('modal.character', null, 'Character'));
            const negLabel = window.escapeHtml(this._t('modal.negativeShort', null, 'Neg'));
            charsSection.style.display = '';
            charsList.innerHTML = parsedData.character_prompts.map((c, i) => {
                const centerStr = c.center ? ` (${c.center.x?.toFixed?.(2) || c.center.x}, ${c.center.y?.toFixed?.(2) || c.center.y})` : '';
                const negHtml = c.negative_prompt
                    ? `<div class="char-negative"><strong>${negLabel}:</strong> ${window.escapeHtml(c.negative_prompt)}</div>`
                    : '';
                return `
                    <div class="character-card">
                        <div class="character-card-header">${characterLabel} ${c.index != null ? c.index + 1 : i + 1}${centerStr}</div>
                        <div>${window.escapeHtml(c.prompt)}</div>
                        ${negHtml}
                    </div>
                `;
            }).join('');
        } else {
            charsSection.style.display = 'none';
            charsList.innerHTML = '';
        }

        // --- Generation Parameters ---
        const paramsSection = $('#modal-params-section');
        const paramsGrid = $('#modal-params-grid');
        const paramEntries = this._buildGenerationParamEntries(image, parsedData);
        if (paramEntries.length > 0) {
            paramsSection.style.display = '';
            paramsGrid.innerHTML = paramEntries.map(({ label, value }) => `
                <div class="param-item">
                    <span class="param-label">${window.escapeHtml(label)}</span>
                    <span class="param-value">${window.escapeHtml(value)}</span>
                </div>
            `).join('');
            paramsGrid.style.display = '';
        } else {
            paramsSection.style.display = 'none';
            paramsGrid.innerHTML = '';
        }

        this._renderModalModelAssets(parsedData);

        // --- Civitai Resources ---
        const civitaiSection = $('#modal-civitai-section');
        const civitaiList = $('#modal-civitai-list');
        const civitaiResources = parsedData?.civitai_resources;
        if (Array.isArray(civitaiResources) && civitaiResources.length > 0) {
            civitaiSection.style.display = '';
            civitaiList.innerHTML = civitaiResources.map(resource => {
                const modelName = window.escapeHtml(resource.model_name || 'Unknown Model');
                const versionName = resource.version_name
                    ? ` <span class="civitai-version">${window.escapeHtml(resource.version_name)}</span>`
                    : '';
                const weight = resource.weight != null
                    ? ` <span class="civitai-weight">(weight: ${resource.weight})</span>`
                    : '';
                const link = resource.civitai_url
                    ? ` <a href="${window.escapeHtml(resource.civitai_url)}" target="_blank" rel="noopener noreferrer" class="civitai-link">View on Civitai →</a>`
                    : '';
                return `<li><strong>${modelName}</strong>${versionName}${weight}${link}</li>`;
            }).join('');
        } else {
            civitaiSection.style.display = 'none';
            civitaiList.innerHTML = '';
        }

        // --- img2img Details ---
        const img2imgSection = $('#modal-img2img-section');
        const img2imgInfo = $('#modal-img2img-info');
        if (parsedData.is_img2img && parsedData.img2img_info && Object.keys(parsedData.img2img_info).length > 0) {
            img2imgSection.style.display = '';
            img2imgInfo.innerHTML = Object.entries(parsedData.img2img_info).map(([key, val]) => {
                const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                return `<div class="param-item"><span class="param-label">${window.escapeHtml(label)}</span><span class="param-value">${window.escapeHtml(String(val))}</span></div>`;
            }).join('');
        } else {
            img2imgSection.style.display = 'none';
            img2imgInfo.innerHTML = '';
        }

        // --- ComfyUI Node Breakdown ---
        const nodesSection = $('#modal-nodes-section');
        const nodesList = $('#modal-nodes-list');
        if (parsedData.prompt_nodes && parsedData.prompt_nodes.length > 0) {
            nodesSection.style.display = '';
            nodesList.innerHTML = parsedData.prompt_nodes.map(node => {
                const roleClass = (node.role || '').toLowerCase().includes('negative') ? 'negative' : 'positive';
                const roleLabel = node.role || 'unknown';
                const nodeTitle = node.node_id ? `Node #${node.node_id}` : (node.class_type || 'Node');
                return `
                    <div class="prompt-node-item">
                        <div class="prompt-node-header">
                            <span>${window.escapeHtml(nodeTitle)}</span>
                            <span class="node-role ${roleClass}">${window.escapeHtml(roleLabel)}</span>
                        </div>
                        <div class="prompt-node-text">${window.escapeHtml(node.text || '')}</div>
                    </div>
                `;
            }).join('');
            nodesList.style.display = '';
        } else {
            nodesSection.style.display = 'none';
            nodesList.innerHTML = '';
        }
    },

    /**
     * Initialize collapsible section toggle handlers (called once).
     */
    _initSectionToggles() {
        if (this._togglesInitialized) return;
        this._togglesInitialized = true;

        document.addEventListener('click', (e) => {
            const toggle = e.target.closest('.section-toggle');
            if (!toggle) return;

            const targetId = toggle.dataset.target;
            if (!targetId) return;

            const target = document.getElementById(targetId);
            if (!target) return;
            const collapseKey = toggle.dataset.collapseKey;

            const icon = toggle.querySelector('.collapse-icon');
            const isCollapsed = target.style.display === 'none';

            if (isCollapsed) {
                target.style.display = '';
                if (icon) icon.textContent = '▼';
                toggle.classList.remove('section-collapsed');
                if (collapseKey) this.modalSectionState[collapseKey] = true;
            } else {
                target.style.display = 'none';
                if (icon) icon.textContent = '▶';
                toggle.classList.add('section-collapsed');
                if (collapseKey) this.modalSectionState[collapseKey] = false;
            }
        });

        document.addEventListener('click', (e) => {
            const button = e.target.closest('.modal-color-mode-btn');
            if (!button) return;
            this._histogramMode = button.dataset.histogramMode || 'rgb';
            document.querySelectorAll('.modal-color-mode-btn').forEach((node) => {
                node.classList.toggle('active', node === button);
            });
            const imgEl = document.getElementById('modal-image');
            if (imgEl) this._extractColorDistribution(imgEl);
        });
    },

    _applyModalSectionStates() {
        document.querySelectorAll('#image-modal .section-toggle').forEach((toggle) => {
            const targetId = toggle.dataset.target;
            const collapseKey = toggle.dataset.collapseKey;
            if (!targetId || !collapseKey) return;
            const target = document.getElementById(targetId);
            if (!target) return;
            const section = toggle.closest('.modal-section');
            // Never leave a visible section header when its body has no real
            // content (QA P3-12). Parent renderers set display:none on empty
            // sections; this is a belt-and-suspenders pass after collapse state.
            if (section && this._isModalSectionBodyEmpty(target)) {
                section.style.display = 'none';
                return;
            }
            const expanded = this.modalSectionState[collapseKey] !== false;
            target.style.display = expanded ? '' : 'none';
            toggle.classList.toggle('section-collapsed', !expanded);
            const icon = toggle.querySelector('.collapse-icon');
            if (icon) icon.textContent = expanded ? '▼' : '▶';
        });
    },

    /**
     * True when a collapsible modal body has nothing useful to show.
     * Used so empty sections don't linger as lone headers.
     */
    _isModalSectionBodyEmpty(target) {
        if (!target) return true;
        const text = String(target.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text || text === '-' || text === '—') return true;
        // Loading placeholders while details hydrate — treat as empty for hide.
        if (/^(Loading|加载|載入)/i.test(text)) return true;
        // Empty list/grid containers (no child nodes with substance).
        if (target.children && target.children.length === 0 && text.length < 2) return true;
        return false;
    },

    _escapeHtml(value) {
        // Delegate to global escapeHtml from modules/utils/escape.js
        return window.escapeHtml(value);
    },

    _renderModalTags(tags = []) {
        const tagsList = document.querySelector('#modal-tags-list');
        if (!tagsList) return;

        if (tags.length === 0) {
            tagsList.textContent = this._t('modal.noTags', null, 'No tags. Run WD14 tagger first.');
            tagsList.style.color = 'var(--text-muted)';
            return;
        }
        tagsList.style.color = '';

        const ratingTags = ['general', 'sensitive', 'questionable', 'explicit'];
        const ratings = tags.filter(t => ratingTags.includes(t.tag));
        const otherTags = tags.filter(t => !ratingTags.includes(t.tag));
        const visibleTags = this.showAllTags ? otherTags : otherTags.slice(0, 40);

        let html = '';
        if (ratings.length > 0) {
            const rating = ratings.reduce((a, b) => a.confidence > b.confidence ? a : b);
            const ratingColors = {
                general: '#22c55e',
                sensitive: '#eab308',
                questionable: '#f97316',
                explicit: '#ef4444'
            };
            html += `<span class="tag" style="background: ${ratingColors[rating.tag]}; color: white; font-weight: 600;">${this._escapeHtml(rating.tag)}</span>`;
        }

        html += visibleTags.map(t => `<span class="tag">${this._escapeHtml(t.tag)}</span>`).join('');
        tagsList.innerHTML = html;

        const toggleBtn = document.querySelector('#btn-toggle-all-tags');
        if (toggleBtn) {
            toggleBtn.style.display = otherTags.length > 40 ? '' : 'none';
            toggleBtn.textContent = this.showAllTags
                ? this._t('modal.showLess', null, 'Show Less')
                : this._t('modal.showMore', null, 'Show More');
        }
    },

});
