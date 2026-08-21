/**
 * prompt-lab/generate.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 944-1217 (of 2,485):
 * generate (/api/prompts/generate), randomize, validate (/api/prompts/validate),
 * preset save/load/delete, applyTagSet, and recategorizeTag
 * (/api/prompts/recategorize).
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    // ============== Generation ==============

    async generate() {
        const { showToast } = window.App;

        if (!this.isReady) {
            showToast(this._t('promptlab.loadingWait', 'Prompt Helper is still loading. Please wait a moment.'), 'info');
            return;
        }

        if (!this.hasBuilderSelection()) {
            showToast(this._t('promptlab.addTagBeforeGenerate', 'Add at least one tag or apply a tag set before generating'), 'warning');
            return;
        }

        try {
            const config = {
                categories: {},
                tag_sets: [],
                quality_preset: 'none',
                count_tag: '',
                include_negative: false,
                count: 1,
            };

            for (const [cat, tags] of Object.entries(this.slots)) {
                if (tags.length > 0) {
                    config.categories[cat] = {
                        tags,
                        weight: (this.weights[cat] ?? 50) / 100,
                        locked: this.locked[cat] || false,
                    };
                }
            }

            const result = await window.App.API.post('/api/prompts/generate', config);
            this._readAffixInputs();
            this.generatedPromptCore = result.positive_prompt || result.prompt || '';
            this.generatedPrompt = this._applyPrependAppend(this.generatedPromptCore);
            this.renderOutput();

            if (result.warnings?.length > 0) {
                showToast(
                    this._t('promptlab.generatedWarnings', 'Generated with {count} warning(s)', { count: result.warnings.length }),
                    'info'
                );
            }
        } catch (e) {
            showToast(formatUserError(e, this._t('promptlab.generateFailed', 'Prompt generation failed')), 'error');
        }
    },

    async randomize() {
        if (!this.isReady) {
            window.App.showToast(this._t('promptlab.loadingWait', 'Prompt Helper is still loading. Please wait a moment.'), 'info');
            return;
        }

        for (const cat of Object.keys(this.categories)) {
            if (this.randomizeExcludedCategories.has(cat)) continue;
            if (this.locked[cat]) continue;

            const tags = this.categories[cat] || [];
            if (tags.length === 0) continue;

            const weight = (this.weights[cat] ?? 50) / 100;
            if (Math.random() > weight) {
                this.slots[cat] = [];
                continue;
            }

            const count = Math.min(tags.length, Math.floor(Math.random() * 3) + 1);
            const shuffled = [...tags].sort(() => Math.random() - 0.5);
            this.slots[cat] = shuffled.slice(0, count);
        }

        if (!this.hasBuilderSelection()) {
            const fallbackOrder = ['character', 'outfit', 'style', 'pose', 'background', 'expression', 'body', 'angle', 'quality'];
            const fallbackCategory = fallbackOrder.find((cat) => (this.categories[cat] || []).length > 0)
                || Object.keys(this.categories).find((cat) => (this.categories[cat] || []).length > 0);

            if (fallbackCategory) {
                const fallbackTags = this.categories[fallbackCategory];
                const fallbackTag = fallbackTags[Math.floor(Math.random() * fallbackTags.length)];
                this.slots[fallbackCategory] = [fallbackTag];
            }
        }

        this.renderCategoryBrowser();
        this.renderSlotBuilder();
        await this.generate();
    },

    async validate() {
        const { showToast } = window.App;

        if (!this.isReady) {
            showToast(this._t('promptlab.loadingWait', 'Prompt Helper is still loading. Please wait a moment.'), 'info');
            return;
        }

        if (!this.hasBuilderSelection()) {
            showToast(this._t('promptlab.addTagBeforeValidate', 'Add at least one tag before validating conflicts'), 'warning');
            return;
        }

        try {
            const allTags = this.getSelectedTags();
            const result = await window.App.API.post('/api/prompts/validate', { tags: allTags });
            const violations = result.violations || result.conflicts || [];

            if (violations.length > 0 || result.valid === false) {
                showToast(
                    this._t('promptlab.conflictsFound', 'Found {count} conflict(s)', { count: violations.length }),
                    'error'
                );
            } else {
                showToast(this._t('promptlab.noConflicts', 'No conflicts detected'), 'success');
            }
        } catch (e) {
            showToast(formatUserError(e, this._t('promptlab.validateFailed', 'Prompt validation failed')), 'error');
        }
    },

    // ============== Presets ==============

    async savePreset() {
        this._readAffixInputs();
        const name = await window.App.showInputModal(
            this._t('promptlab.savePresetTitle', 'Save Preset'),
            this._t('promptlab.savePresetMessage', 'Enter a name for this preset:'),
            ''
        );
        if (!name) return;

        try {
            await window.App.API.post('/api/prompts/presets', {
                name,
                config: {
                    slots: { ...this.slots },
                    weights: { ...this.weights },
                    locked: { ...this.locked },
                    prependTags: this.prependTags,
                    appendTags: this.appendTags,
                }
            });
            await this.loadPresets();
            this.renderPresetList();
            window.App.showToast(
                this._t('promptlab.presetSaved', 'Preset "{name}" saved', { name }),
                'success'
            );
        } catch (e) {
            window.App.showToast(formatUserError(e, this._t('promptlab.presetSaveFailed', 'Failed to save preset')), 'error');
        }
    },

    async loadPreset(id) {
        try {
            const preset = this.presets.find(p => String(p.id) === String(id));
            if (!preset?.config) return;

            this.slots = { ...(preset.config.slots || {}) };
            this.weights = { ...(preset.config.weights || {}) };
            this.locked = { ...(preset.config.locked || {}) };
            this.prependTags = preset.config.prependTags || '';
            this.appendTags = preset.config.appendTags || '';
            this._syncAffixInputs();

            this.invalidateGeneratedPrompt();
            this.renderCategoryBrowser();
            this.renderSlotBuilder();
            window.App.showToast(
                this._t('promptlab.presetLoaded', 'Loaded preset "{name}"', { name: preset.name }),
                'success'
            );
        } catch (e) {
            window.App.showToast(
                formatUserError(e, this._t('promptlab.presetLoadFailed', 'Failed to load preset')),
                'error'
            );
        }
    },

    async deletePreset(id) {
        const { showConfirm, API, showToast } = window.App;

        showConfirm(
            this._t('promptlab.deletePresetTitle', 'Delete Preset'),
            this._t('promptlab.deletePresetMessage', 'Delete this preset? This cannot be undone.'),
            async () => {
                try {
                    await API.delete(`/api/prompts/presets/${id}`);
                    await this.loadPresets();
                    this.renderPresetList();
                    showToast(this._t('promptlab.presetDeleted', 'Preset deleted'), 'info');
                } catch (e) {
                    showToast(
                        formatUserError(e, this._t('promptlab.presetDeleteFailed', 'Failed to delete preset')),
                        'error'
                    );
                }
            }
        );
    },

    // ============== Tag Sets ==============

    applyTagSet(setId) {
        const set = this.tagSets.find(s => String(s.id) === String(setId));
        if (!set) return;

        // Backend shape: { id, name, category, tags: [{tag, weight, required}] }
        const members = set.members || set.tags || [];
        for (const member of members) {
            const cat = member.category || set.category || 'style';
            const tag = member.tag;
            if (!tag) continue;
            if (!this.slots[cat]) this.slots[cat] = [];
            if (!this.slots[cat].includes(tag)) {
                this.slots[cat] = [...this.slots[cat], tag];
            }
        }

        this.invalidateGeneratedPrompt();
        this.renderCategoryBrowser();
        this.renderSlotBuilder();
        window.App.showToast(
            this._t('promptlab.tagSetApplied', 'Applied tag set "{name}"', { name: set.name }),
            'success'
        );
    },

    async recategorizeTag() {
        const tagInput = document.getElementById('promptlab-recat-tag');
        const categoryInput = document.getElementById('promptlab-recat-category');
        const tag = String(tagInput?.value || '').trim();
        const category = String(categoryInput?.value || '').trim();

        if (!tag || !category) {
            window.App.showToast(
                this._t('promptlab.recategorizeMissing', 'Enter both a tag and a category.'),
                'warning'
            );
            return;
        }

        try {
            await window.App.API.post(`/api/prompts/recategorize?tag=${encodeURIComponent(tag)}&category=${encodeURIComponent(category)}`, {});
            Object.keys(this.slots).forEach((slotCategory) => {
                const tags = this.slots[slotCategory] || [];
                if (!tags.includes(tag)) return;
                this.slots[slotCategory] = tags.filter((slotTag) => slotTag !== tag);
                if (!this.slots[category]) this.slots[category] = [];
                if (!this.slots[category].includes(tag)) this.slots[category] = [...this.slots[category], tag];
            });
            await this.loadCategories();
            await this.loadTagSets();
            this.renderTagSetOptions();
            this.renderPromptDataTools();
            this.renderCategoryBrowser();
            this.renderSlotBuilder();
            window.App.showToast(
                this._t('promptlab.recategorized', 'Moved "{tag}" to "{category}".', { tag, category }),
                'success'
            );
        } catch (e) {
            window.App.showToast(
                formatUserError(e, this._t('promptlab.recategorizeFailed', 'Failed to move tag category')),
                'error'
            );
        }
    },

});
