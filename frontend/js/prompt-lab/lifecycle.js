/**
 * prompt-lab/lifecycle.js - prompt-lab.js decomposition (verbatim Object.assign mixin).
 * Moved BYTE-IDENTICAL from frontend/js/prompt-lab.js pre-cut lines 56-155 (of 2,485):
 * init() 7-step boot, first-use card show/dismiss/refresh, activateMode (unknown
 * mode normalizes to stats), showFirstUseGuide, and the 4 /api/prompts/* loaders.
 * Classic script: joins the ONE unsealed window.PromptLab object declared
 * in prompt-lab/base.js (loads FIRST); prompt-lab/boot.js declares the
 * initPromptLab boot LAST; index.html lists the family in original line order.
 */
Object.assign(window.PromptLab, {
    async init() {
        if (!this.eventsBound) {
            this.bindEvents();
            this.bindIntelligenceEvents();
            this.eventsBound = true;
        }

        this.setReadyState(false);

        try {
            await this.loadCategories();
            await this.loadTagSets();
            await this.loadExclusionRules();
            await this.loadPresets();
            await this._ensureImageCatalog().catch(() => null);
            this.renderTagSetOptions();
            this.renderPromptDataTools();
            this.renderCategoryBrowser();
            this.renderSlotBuilder();
            this.renderPresetList();
            this.showFirstUseGuide();
            // Load stats for the default Stats tab
            this.loadStats();
        } finally {
            this.setReadyState(true);
        }
    },

    dismissFirstUseCard() {
        localStorage.setItem('promptlab-guide-seen', 'true');
        const card = document.getElementById('promptlab-start-card');
        if (card) card.hidden = true;
    },

    refreshFirstUseCard() {
        const card = document.getElementById('promptlab-start-card');
        const dismissBtn = document.getElementById('promptlab-start-dismiss');
        if (!card) return;
        if (dismissBtn && dismissBtn.dataset.bound !== 'true') {
            dismissBtn.addEventListener('click', () => this.dismissFirstUseCard());
            dismissBtn.dataset.bound = 'true';
        }
        // Quiet by default — Help on this view unhides the card.
        card.hidden = true;
        if (document.getElementById('btn-help') && !document.getElementById('btn-help').dataset.promptlabGuideBound) {
            document.getElementById('btn-help').dataset.promptlabGuideBound = 'true';
            document.getElementById('btn-help').addEventListener('click', () => {
                if (window.App?.AppState?.currentView === 'promptlab') {
                    card.hidden = false;
                }
            });
        }
    },

    activateMode(mode) {
        const safeMode = ['stats', 'compare', 'build', 'random'].includes(mode) ? mode : 'stats';
        document.querySelectorAll('.promptlab-tab').forEach((tab) => {
            tab.classList.toggle('active', tab.dataset.mode === safeMode);
        });
        document.querySelectorAll('.promptlab-mode').forEach((panel) => {
            panel.classList.toggle('active', panel.id === `promptlab-mode-${safeMode}`);
        });
        if (safeMode === 'stats') this.loadStats();
        if (safeMode === 'compare') this.populateImageSelectors();
        if (safeMode === 'build') this.populateBuildSelector();
    },

    showFirstUseGuide() {
        this.refreshFirstUseCard();
    },

    // ============== Data Loading ==============

    async loadCategories() {
        try {
            const result = await window.App.API.get('/api/prompts/categories');
            this.categories = result.categories || {};
        } catch (e) {
            this.categories = {};
        }
    },

    async loadTagSets() {
        try {
            const result = await window.App.API.get('/api/prompts/sets');
            this.tagSets = result.sets || [];
        } catch (e) {
            this.tagSets = [];
        }
    },

    async loadExclusionRules() {
        try {
            const result = await window.App.API.get('/api/prompts/exclusions');
            this.exclusionRules = result.rules || [];
        } catch (e) {
            this.exclusionRules = [];
        }
    },

    async loadPresets() {
        try {
            const result = await window.App.API.get('/api/prompts/presets');
            this.presets = result.presets || [];
        } catch (e) {
            this.presets = [];
        }
    },

});
