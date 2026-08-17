/**
 * prompt-lab/render.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 559-847 (of 2,485):
 * renderCategoryBrowser, renderSlotBuilder, renderPresetList, renderTagSetOptions,
 * prompt-resource list rendering + renderPromptDataTools, and renderOutput.
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    // ============== Rendering ==============

    renderCategoryBrowser() {
        const container = document.getElementById('promptlab-categories');
        if (!container) return;

        const categoryNames = Object.keys(this.categories);
        if (categoryNames.length === 0) {
            container.innerHTML = `<div class="empty-state">${this._escapeValue(
                this._t('promptlab.categoriesUnavailable', 'No categories loaded. Check backend connection.')
            )}</div>`;
            return;
        }

        const html = categoryNames.map((cat) => {
            const tags = this.categories[cat] || [];
            const selectedTags = this.slots[cat] || [];
            const encodedCategory = this._safeDataValue(cat);
            const safeCategory = this._escapeValue(cat);
            const isExpanded = container.querySelector(`[data-cat="${encodedCategory}"]`)?.classList.contains('expanded');

            return `
                <div class="cat-group ${isExpanded ? 'expanded' : ''}" data-cat="${encodedCategory}">
                    <div class="cat-header" data-cat="${encodedCategory}">
                        <span class="cat-arrow">${isExpanded ? '▼' : '▶'}</span>
                        <span class="cat-name">${safeCategory}</span>
                        <span class="cat-count">${tags.length}</span>
                        ${selectedTags.length > 0 ? `<span class="cat-selected">${selectedTags.length} selected</span>` : ''}
                    </div>
                    <div class="cat-tags" style="display: ${isExpanded ? 'flex' : 'none'};">
                        ${tags.map((tag) => this._buildTagChip(tag, cat, selectedTags)).join('')}
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;

        container.querySelectorAll('.cat-header').forEach((header) => {
            header.addEventListener('click', () => {
                const group = header.parentElement;
                const tagsDiv = group.querySelector('.cat-tags');
                const arrow = header.querySelector('.cat-arrow');
                const isOpen = tagsDiv.style.display !== 'none';
                tagsDiv.style.display = isOpen ? 'none' : 'flex';
                arrow.textContent = isOpen ? '▶' : '▼';
                group.classList.toggle('expanded', !isOpen);
            });
        });

        container.querySelectorAll('.cat-tag').forEach((tagEl) => {
            tagEl.addEventListener('click', () => {
                const tag = this._decodeDataValue(tagEl.dataset.tag);
                const cat = this._decodeDataValue(tagEl.dataset.cat);
                this.toggleTagInSlot(cat, tag);
            });
        });
    },

    renderSlotBuilder() {
        const container = document.getElementById('promptlab-slots');
        if (!container) return;

        const categoryNames = Object.keys(this.categories);
        if (categoryNames.length === 0) {
            container.innerHTML = `<div class="empty-state">${this._escapeValue(
                this._t('promptlab.loadCategoriesFirst', 'Load categories first')
            )}</div>`;
            return;
        }

        const activeCategories = categoryNames.filter((cat) => {
            const selected = this.slots[cat] || [];
            return selected.length > 0;
        });

        const coreCats = ['character', 'outfit', 'pose', 'expression', 'body', 'background', 'style', 'quality'];
        const displayCats = [...new Set([...coreCats.filter(c => categoryNames.includes(c)), ...activeCategories])];

        const html = displayCats.map((cat) => {
            const selected = this.slots[cat] || [];
            const isLocked = this.locked[cat] || false;
            const weight = this.weights[cat] ?? 50;
            const hasConflict = this.checkConflicts(cat);
            const safeCategory = this._escapeValue(cat);
            const encodedCategory = this._safeDataValue(cat);

            return `
                <div class="slot-row ${hasConflict ? 'has-conflict' : ''}" data-slot="${encodedCategory}">
                    <div class="slot-header">
                        <button class="slot-lock ${isLocked ? 'locked' : ''}" data-cat="${encodedCategory}" title="${isLocked ? 'Unlock' : 'Lock'} (survives randomize)">
                            ${isLocked ? '<svg class="icon" aria-hidden="true"><use href="#i-lock"/></svg>' : '<svg class="icon" aria-hidden="true"><use href="#i-unlock"/></svg>'}
                        </button>
                        <span class="slot-name">${safeCategory}</span>
                        ${hasConflict ? '<span class="conflict-icon" title="Exclusion rule conflict"><svg class="icon" aria-hidden="true"><use href="#i-alert"/></svg></span>' : ''}
                    </div>
                    <div class="slot-tags">
                        ${selected.length > 0 ? selected.map((tag) => this._buildSlotTag(tag, cat)).join('') : `<span class="slot-empty">${this._escapeValue(this._t('promptlab.slotEmpty', 'Click tags in the browser to add'))}</span>`}
                    </div>
                    <div class="slot-weight">
                        <input type="range" min="0" max="100" value="${weight}"
                               class="slot-weight-slider" data-cat="${encodedCategory}" title="Weight: ${weight}%">
                        <span class="slot-weight-value">${weight}%</span>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;

        container.querySelectorAll('.slot-tag-remove').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeTagFromSlot(
                    this._decodeDataValue(btn.dataset.cat),
                    this._decodeDataValue(btn.dataset.tag),
                );
            });
        });

        container.querySelectorAll('.slot-lock').forEach((btn) => {
            btn.addEventListener('click', () => {
                const cat = this._decodeDataValue(btn.dataset.cat);
                this.locked[cat] = !this.locked[cat];
                this.renderSlotBuilder();
            });
        });

        container.querySelectorAll('.slot-weight-slider').forEach((slider) => {
            slider.addEventListener('input', () => {
                const cat = this._decodeDataValue(slider.dataset.cat);
                this.weights[cat] = parseInt(slider.value, 10) || 0;
                slider.nextElementSibling.textContent = `${slider.value}%`;
            });
        });

        this.updateActionState();
    },

    renderPresetList() {
        const container = document.getElementById('promptlab-presets');
        if (!container) return;

        if (this.presets.length === 0) {
            container.innerHTML = `<div class="preset-empty">${this._escapeValue(
                this._t('promptlab.noPresetsDetailed', 'No saved presets. Save your current configuration as a preset.')
            )}</div>`;
            return;
        }

        container.innerHTML = this.presets.map((preset) => `
            <div class="preset-item" data-id="${preset.id}">
                <span class="preset-name">${this._escapeValue(preset.name)}</span>
                <div class="preset-actions">
                    <button class="btn-preset-load" data-id="${preset.id}" title="Load preset"><svg class="icon" aria-hidden="true"><use href="#i-folder"/></svg></button>
                    <button class="btn-preset-delete" data-id="${preset.id}" title="Delete preset"><svg class="icon" aria-hidden="true"><use href="#i-trash"/></svg></button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('.btn-preset-load').forEach((btn) => {
            btn.addEventListener('click', () => this.loadPreset(btn.dataset.id));
        });

        container.querySelectorAll('.btn-preset-delete').forEach((btn) => {
            btn.addEventListener('click', () => this.deletePreset(btn.dataset.id));
        });
    },

    renderTagSetOptions() {
        const selector = document.getElementById('promptlab-set-select');
        if (!selector) return;

        const _pl = window.I18n?.t?.('promptlab.selectTagSet');
        const defaultLabel = (_pl && _pl !== 'promptlab.selectTagSet') ? _pl : '-- Select Tag Set --';
        const options = [
            `<option value="">${this._escapeValue(defaultLabel)}</option>`,
            ...this.tagSets.map((set) => `
                <option value="${this._escapeValue(String(set.id))}">
                    ${this._escapeValue(set.name)}${set.category ? ` (${this._escapeValue(set.category)})` : ''}
                </option>
            `)
        ];

        selector.innerHTML = options.join('');
    },

    _isUserPromptResource(item, builtinPrefix) {
        const id = String(item?.id ?? '').trim();
        return Boolean(id) && !id.startsWith(builtinPrefix);
    },

    _renderResourceEmpty(key, fallback) {
        return `<div class="promptlab-resource-empty">${this._escapeValue(this._t(key, fallback))}</div>`;
    },

    _renderResourceItem(item, actionClass) {
        const safeId = this._safeDataValue(item.id);
        const safeName = this._escapeValue(item.name || item.rule_name || item.id);
        const safeMeta = this._escapeValue(this._formatPromptResourceMeta(item));
        return `
            <div class="promptlab-resource-item">
                <div class="promptlab-resource-main">
                    <span class="promptlab-resource-title">${safeName}</span>
                    ${safeMeta ? `<span class="promptlab-resource-meta">${safeMeta}</span>` : ''}
                </div>
                <button class="btn btn-small btn-ghost ${actionClass}" type="button" data-id="${safeId}" title="${this._escapeValue(this._t('common.delete', 'Delete'))}" aria-label="${this._escapeValue(this._t('common.delete', 'Delete'))}"><svg class="icon" aria-hidden="true"><use href="#i-trash"/></svg></button>
            </div>
        `;
    },

    _formatPromptResourceMeta(item) {
        if (!item) return '';
        if (Array.isArray(item.members) || Array.isArray(item.tags)) {
            const count = item.tag_count ?? (item.members || item.tags || []).length;
            const parts = [];
            if (item.category) parts.push(item.category);
            parts.push(this._t('promptlab.tagCount', '{count} tags', { count }).replace('{count}', count));
            return parts.join(' · ');
        }

        const conditions = (item.conditions || [])
            .map((condition) => condition.tag || condition.condition_tag || '')
            .filter(Boolean);
        const targets = (item.targets || [])
            .map((target) => target.tag || target.excluded_tag || target.category || target.excluded_category || '')
            .filter(Boolean);
        if (!conditions.length && !targets.length) {
            return item.description || '';
        }
        return `${conditions.join(', ') || '*'} → ${targets.join(', ') || '*'}`;
    },

    renderPromptDataTools() {
        const categoryOptions = document.getElementById('promptlab-category-options');
        if (categoryOptions) {
            const categories = Object.keys(this.categories || {}).sort((a, b) => a.localeCompare(b));
            categoryOptions.innerHTML = categories
                .map((category) => `<option value="${this._escapeValue(category)}"></option>`)
                .join('');
        }

        const setList = document.getElementById('promptlab-custom-set-list');
        if (setList) {
            const userSets = this.tagSets.filter((set) => this._isUserPromptResource(set, 'builtin-tag-set'));
            setList.innerHTML = userSets.length
                ? userSets.map((set) => this._renderResourceItem(set, 'btn-promptlab-delete-set')).join('')
                : this._renderResourceEmpty('promptlab.noCustomTagSets', 'No custom tag sets yet.');
        }

        const exclusionList = document.getElementById('promptlab-exclusion-list');
        if (exclusionList) {
            const userRules = this.exclusionRules.filter((rule) => this._isUserPromptResource(rule, 'builtin-exclusion'));
            exclusionList.innerHTML = userRules.length
                ? userRules.map((rule) => this._renderResourceItem(rule, 'btn-promptlab-delete-exclusion')).join('')
                : this._renderResourceEmpty('promptlab.noCustomExclusions', 'No custom exclusions yet.');
        }

        this.setReadyState(this.isReady);
    },

    renderOutput() {
        const outputEl = document.getElementById('promptlab-output');
        if (!outputEl) return;

        outputEl.value = this.generatedPrompt;

        const warningsEl = document.getElementById('promptlab-warnings');
        if (!warningsEl) return;

        const conflicts = this.getAllConflicts();
        if (conflicts.length > 0) {
            const fragment = document.createDocumentFragment();
            conflicts.forEach((conflict) => {
                const item = document.createElement('div');
                item.className = 'warning-item';
                item.textContent = `${conflict}`;
                fragment.appendChild(item);
            });
            warningsEl.replaceChildren(fragment);
            warningsEl.style.display = 'block';
        } else {
            warningsEl.replaceChildren();
            warningsEl.style.display = 'none';
        }

        this.updateActionState();
    },

});
