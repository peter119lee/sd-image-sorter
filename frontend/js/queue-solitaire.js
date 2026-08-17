/**
 * Queue Manager Solitaire — multi-section image sorting workspace.
 *
 * Replaces the censor workspace when active.  Users deal images from an
 * "Unsorted" pile into named, colour-coded sections, then batch-rename
 * per section.  Filter-to-section lets users auto-sort by tag, rating,
 * resolution, or aesthetic score.
 */
(function () {
    'use strict';

    const SECTION_COLORS = ['gray', 'green', 'gold', 'red', 'blue', 'purple', 'teal', 'pink'];
    const THUMB_SIZE = 52;
    const AUTO_SORT_PROFILES_KEY = 'queue_solitaire_auto_sort_profiles_v1';

    // ── State ──────────────────────────────────────────────────────────
    const state = {
        active: false,
        sections: [],          // [{ id, name, color, items: [imageId, ...], collapsed }]
        selected: new Set(),   // imageId set
        previewId: null,
        undoStack: [],         // [{ description, snapshot }]
        filterActive: false,
        filterMatches: new Set(),
        appliedFilterMode: 'none',
        hoverTimer: null,
        detailCache: new Map(),
        galleryFilterMode: false,
        advancedFilters: null,
        autoSortProfiles: [],
        editingProfileId: '',
        profileDraftSections: [],
        quickAdvancedVisible: false,
    };

    let _nextSectionId = 1;
    let _marqueeInitialized = false;
    let _toolbarInitialized = false;
    function newSectionId() { return `qs-sec-${_nextSectionId++}`; }

    // ── Helpers ────────────────────────────────────────────────────────
    function getCensorState() { return window.__CENSOR_STATE__ || window.CensorState; }

    function escapeQueueHtml(value) {
        if (typeof window.escapeHtml === 'function') {
            return window.escapeHtml(value);
        }
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function safeSectionColor(value) {
        return SECTION_COLORS.includes(value) ? value : 'gray';
    }

    function getQueueItem(id) {
        const cs = getCensorState();
        return cs?.queue?.find(item => item.id === id);
    }

    function getAllImageIds() {
        const cs = getCensorState();
        return (cs?.queue || []).map(item => item.id);
    }

    function getThumbUrl(id) {
        return `/api/image-thumbnail/${id}?size=256`;
    }

    function getFullUrl(id) {
        return `/api/image-file/${id}`;
    }

    function getImageMeta(id) {
        // Try to get cached image data from AppState
        const images = window.App?.AppState?.images || [];
        return images.find(img => img.id === id);
    }

    function cloneFilters(filters) {
        const clone = window.App?.cloneFilterState;
        if (typeof clone === 'function') {
            return clone(filters || null);
        }
        return JSON.parse(JSON.stringify(filters || {}));
    }

    function createFilterState() {
        const create = window.App?.createDefaultFilterState;
        if (typeof create === 'function') {
            return create();
        }
        return {
            generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
            ratings: ['general', 'sensitive', 'questionable', 'explicit'],
            tags: [],
            checkpoints: [],
            loras: [],
            prompts: [],
            artist: null,
            search: '',
            sortBy: 'newest',
            limit: 0,
            minWidth: null,
            maxWidth: null,
            minHeight: null,
            maxHeight: null,
            aspectRatio: '',
            minAesthetic: null,
            maxAesthetic: null
        };
    }

    function normalizeCheckpointValue(value) {
        const normalize = window.App?.normalizeCheckpointFilterValue;
        if (typeof normalize === 'function') {
            return normalize(value);
        }
        let text = String(value || '').trim();
        if (!text) return '';
        text = text.replace(/\\/g, '/').split('/').pop().trim();
        text = text.replace(/\.(safetensors|ckpt|pt|pth|bin|onnx)$/i, '').trim();
        return text;
    }

    function getNormalizedCheckpoint(meta) {
        return normalizeCheckpointValue(meta?.checkpoint_normalized || meta?.checkpoint || '');
    }

    function getNormalizedArtist(meta) {
        const raw = meta?.artist ?? meta?.predicted_artist ?? meta?.artist_name ?? '';
        return String(raw || '').trim().toLowerCase();
    }

    function t(key, fallback, params) {
        const v = window.I18n?.t?.(key, params); return (v && v !== key) ? v : fallback;
    }

    async function fetchImageDetailFallback(id) {
        const cachedMeta = getImageMeta(id) || {};

        try {
            const response = await fetch(`/api/images/${id}`);
            if (!response.ok) throw new Error(`Failed to load image detail: ${response.status}`);
            const payload = await response.json();
            const detail = {
                ...cachedMeta,
                ...(payload.image || {}),
                tags: Array.isArray(payload.tags) ? payload.tags.map(tag => tag.tag) : [],
            };
            state.detailCache.set(id, detail);
            return detail;
        } catch (error) {
            const fallback = {
                ...cachedMeta,
                tags: [],
            };
            state.detailCache.set(id, fallback);
            return fallback;
        }
    }

    async function ensureImageDetail(id) {
        if (state.detailCache.has(id)) {
            return state.detailCache.get(id);
        }

        await ensureImageDetails([id]);
        return state.detailCache.get(id) || {
            ...(getImageMeta(id) || {}),
            tags: [],
        };
    }

    async function ensureImageDetails(ids) {
        const uniqueIds = Array.from(new Set((Array.isArray(ids) ? ids : [ids])
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value > 0)));
        const missingIds = uniqueIds.filter((id) => !state.detailCache.has(id));
        if (!missingIds.length) {
            return;
        }

        const selectionLoader = window.App?.loadSelectionData;
        if (typeof selectionLoader === 'function') {
            const payload = await selectionLoader(missingIds);
            const resolvedIds = new Set();

            (payload?.images || []).forEach((image) => {
                state.detailCache.set(image.id, {
                    ...(getImageMeta(image.id) || {}),
                    ...image,
                    tags: Array.isArray(image.tags) ? image.tags : [],
                });
                resolvedIds.add(image.id);
            });

            missingIds
                .filter((id) => !resolvedIds.has(id))
                .forEach((id) => {
                    state.detailCache.set(id, {
                        ...(getImageMeta(id) || {}),
                        tags: [],
                    });
                });
            return;
        }

        await Promise.all(missingIds.map((id) => fetchImageDetailFallback(id)));
    }

    // ── Undo ──────────────────────────────────────────────────────────
    function pushUndo(description) {
        const snapshot = JSON.stringify(state.sections.map(s => ({
            id: s.id, name: s.name, color: s.color,
            items: [...s.items], collapsed: s.collapsed,
        })));
        state.undoStack.push({ description, snapshot });
        if (state.undoStack.length > 50) state.undoStack.shift();
    }

    function popUndo() {
        const entry = state.undoStack.pop();
        if (!entry) return null;
        const restored = JSON.parse(entry.snapshot);
        state.sections = restored.map(s => ({
            ...s, items: s.items,
        }));
        return entry.description;
    }

    // ── Section Operations ────────────────────────────────────────────
    function addSection(name, color) {
        const id = newSectionId();
        color = color || SECTION_COLORS[state.sections.length % SECTION_COLORS.length];
        state.sections.push({ id, name: name || `Section ${state.sections.length}`, color, items: [], collapsed: false });
        return id;
    }

    function removeSection(sectionId) {
        const idx = state.sections.findIndex(s => s.id === sectionId);
        if (idx < 0 || state.sections[idx].id === 'unsorted') return;
        pushUndo(`Remove section "${state.sections[idx].name}"`);
        const items = state.sections[idx].items;
        state.sections.splice(idx, 1);
        // Move orphan items back to unsorted
        const unsorted = state.sections.find(s => s.id === 'unsorted');
        if (unsorted) unsorted.items.push(...items);
        render();
    }

    function moveItems(imageIds, targetSectionId) {
        if (!imageIds.length) return;
        const targetSection = state.sections.find(s => s.id === targetSectionId);
        if (!targetSection) return;

        pushUndo(`Move ${imageIds.length} image(s) to "${targetSection.name}"`);

        const idSet = new Set(imageIds);
        // Remove from all sections
        for (const section of state.sections) {
            section.items = section.items.filter(id => !idSet.has(id));
        }
        // Add to target (avoid duplicates)
        const existing = new Set(targetSection.items);
        for (const id of imageIds) {
            if (!existing.has(id)) targetSection.items.push(id);
        }

        render();
    }

    // ── Auto-Sort ─────────────────────────────────────────────────────
    async function autoSortByRating() {
        pushUndo('Auto-sort by rating');
        const allIds = getAllImageIds();
        await ensureImageDetails(allIds);
        const ratingMap = {};

        for (const id of allIds) {
            const tags = state.detailCache.get(id)?.tags || [];
            if (tags.includes('explicit')) ratingMap[id] = 'Explicit';
            else if (tags.includes('questionable')) ratingMap[id] = 'Questionable';
            else if (tags.includes('sensitive')) ratingMap[id] = 'Sensitive';
            else if (tags.includes('general')) ratingMap[id] = 'General';
            else ratingMap[id] = 'Unrated';
        }

        const groups = ['General', 'Sensitive', 'Questionable', 'Explicit', 'Unrated'];
        const colors = ['green', 'gold', 'purple', 'red', 'gray'];

        // Reset to just these sections
        state.sections = groups.map((name, i) => ({
            id: name === 'Unrated' ? 'unsorted' : newSectionId(),
            name, color: colors[i],
            items: allIds.filter(id => ratingMap[id] === name),
            collapsed: false,
        }));
        render();
    }

    function autoSortByAesthetic() {
        pushUndo('Auto-sort by aesthetic');
        const allIds = getAllImageIds();
        const great = [], good = [], low = [], unscored = [];

        for (const id of allIds) {
            const meta = getImageMeta(id);
            const score = meta?.aesthetic_score;
            if (score == null) unscored.push(id);
            else if (score >= 7) great.push(id);
            else if (score >= 5) good.push(id);
            else low.push(id);
        }

        state.sections = [
            { id: newSectionId(), name: 'Great (7+)', color: 'gold', items: great, collapsed: false },
            { id: newSectionId(), name: 'Good (5-7)', color: 'green', items: good, collapsed: false },
            { id: newSectionId(), name: 'Low (<5)', color: 'red', items: low, collapsed: false },
            { id: 'unsorted', name: 'Unscored', color: 'gray', items: unscored, collapsed: false },
        ];
        render();
    }

    function autoSortByResolution() {
        pushUndo('Auto-sort by resolution');
        const allIds = getAllImageIds();
        const hd = [], sd = [], unknown = [];

        for (const id of allIds) {
            const meta = getImageMeta(id);
            const w = meta?.width || 0;
            const h = meta?.height || 0;
            const maxDim = Math.max(w, h);
            if (maxDim === 0) unknown.push(id);
            else if (maxDim >= 1920) hd.push(id);
            else sd.push(id);
        }

        state.sections = [
            { id: newSectionId(), name: 'HD (≥1920)', color: 'gold', items: hd, collapsed: false },
            { id: newSectionId(), name: 'SD (<1920)', color: 'blue', items: sd, collapsed: false },
            { id: 'unsorted', name: 'Unknown', color: 'gray', items: unknown, collapsed: false },
        ];
        render();
    }

    // ── Filter → Section ──────────────────────────────────────────────
    function getResolutionBucket(meta) {
        const width = meta?.width || 0;
        const height = meta?.height || 0;
        const maxDim = Math.max(width, height);
        if (!maxDim) return 'unknown';
        return maxDim >= 1920 ? 'hd' : 'sd';
    }

    function getAspectBucket(meta) {
        const width = meta?.width || 0;
        const height = meta?.height || 0;
        if (!width || !height) return '';
        if (width === height) return 'square';
        return width > height ? 'landscape' : 'portrait';
    }

    function getRatingFromMeta(meta) {
        const tags = Array.isArray(meta?.tags) ? meta.tags : [];
        if (tags.includes('explicit')) return 'explicit';
        if (tags.includes('questionable')) return 'questionable';
        if (tags.includes('sensitive')) return 'sensitive';
        if (tags.includes('general')) return 'general';
        return 'unrated';
    }

    function getLoraText(meta) {
        const loras = meta?.loras;
        if (Array.isArray(loras)) return loras.join(' ').toLowerCase();
        if (typeof loras === 'string') {
            try {
                const parsed = JSON.parse(loras);
                if (Array.isArray(parsed)) return parsed.join(' ').toLowerCase();
            } catch (_) {
                return loras.toLowerCase();
            }
            return loras.toLowerCase();
        }
        return '';
    }

    function buildFilterSummaryPartsFromState(filters) {
        if (!filters || !window.formatFilterSummary) return [];
        const summary = window.formatFilterSummary(filters);
        const parts = [];
        const allLabel = t('common.all', 'All');
        const noneLabel = t('common.none', 'None');
        const anyLabel = t('filter.any', 'Any');
        const hasMeaningfulSummary = (value, ...emptyLabels) => {
            if (value == null || value === '') return false;
            const normalized = String(value).trim();
            if (!normalized) return false;
            return ![allLabel, noneLabel, anyLabel, ...emptyLabels]
                .some((label) => normalized === String(label || '').trim());
        };

        if (filters.collectionId) {
            parts.push(t('queueSolitaire.collectionScope', 'Collection scope'));
        }

        if (hasMeaningfulSummary(summary.generators)) {
            parts.push(`${t('filter.generators', 'Generators')}: ${summary.generators}`);
        }
        if (hasMeaningfulSummary(summary.ratings)) {
            parts.push(`${t('filter.ratings', 'Ratings')}: ${summary.ratings}`);
        }
        if (hasMeaningfulSummary(summary.tags)) {
            parts.push(`${t('filter.tags', 'Tags')}: ${summary.tags}`);
        }
        if (hasMeaningfulSummary(summary.prompts)) {
            parts.push(`${t('filter.prompts', 'Prompts')}: ${summary.prompts}`);
        }
        if (hasMeaningfulSummary(summary.search)) {
            parts.push(`${t('filter.search', 'Search')}: ${summary.search}`);
        }
        if (hasMeaningfulSummary(summary.checkpoints)) {
            parts.push(`${t('filter.checkpoints', 'Checkpoints')}: ${summary.checkpoints}`);
        }
        if (hasMeaningfulSummary(summary.loras)) {
            parts.push(`${t('filter.loras', 'LoRAs')}: ${summary.loras}`);
        }
        if (hasMeaningfulSummary(summary.dimensions)) {
            parts.push(`${t('filter.sizeRules', 'Size Rules')}: ${summary.dimensions}`);
        }

        const minAesthetic = filters.minAesthetic;
        const maxAesthetic = filters.maxAesthetic;
        if (minAesthetic != null || maxAesthetic != null) {
            parts.push(`★ ${minAesthetic ?? 0}-${maxAesthetic ?? 10}`);
        }

        return parts;
    }

    function setFilterMatchCountText(text, i18nKey = null) {
        const countEl = document.getElementById('qs-filter-match-count');
        if (!countEl) return;
        if (i18nKey) countEl.setAttribute('data-i18n', i18nKey);
        else countEl.removeAttribute('data-i18n');
        countEl.textContent = text;
    }

    function buildQuickFilterSummaryParts() {
        const parts = [];
        const keyword = document.getElementById('qs-filter-tag')?.value?.trim();
        const generator = document.getElementById('qs-filter-generator')?.value || '';
        const rating = document.getElementById('qs-filter-rating')?.value || '';
        const checkpointQuery = document.getElementById('qs-filter-checkpoint')?.value?.trim();
        const loraQuery = document.getElementById('qs-filter-lora')?.value?.trim();
        const minW = document.getElementById('qs-filter-minw')?.value?.trim();
        const maxW = document.getElementById('qs-filter-maxw')?.value?.trim();
        const minH = document.getElementById('qs-filter-minh')?.value?.trim();
        const maxH = document.getElementById('qs-filter-maxh')?.value?.trim();
        const aspect = document.getElementById('qs-filter-aspect')?.value || '';
        const minAesthetic = document.getElementById('qs-filter-aesthetic')?.value?.trim();
        const maxAesthetic = document.getElementById('qs-filter-aesthetic-max')?.value?.trim();
        const resolution = document.getElementById('qs-filter-resolution')?.value || '';

        if (keyword) parts.push(`${t('filter.search', 'Search')}: ${keyword}`);
        if (generator) parts.push(`${t('filter.generators', 'Generators')}: ${generator}`);
        if (rating) parts.push(`${t('filter.ratings', 'Ratings')}: ${rating}`);
        if (checkpointQuery) parts.push(`${t('filter.checkpoints', 'Checkpoints')}: ${checkpointQuery}`);
        if (loraQuery) parts.push(`${t('filter.loras', 'LoRAs')}: ${loraQuery}`);
        if (resolution) parts.push(`${t('filter.sizeRules', 'Size Rules')}: ${resolution.toUpperCase()}`);

        const dimensions = [];
        if (minW || maxW) dimensions.push(`W ${minW || 0}-${maxW || '∞'}`);
        if (minH || maxH) dimensions.push(`H ${minH || 0}-${maxH || '∞'}`);
        if (aspect) dimensions.push(aspect);
        if (dimensions.length) {
            parts.push(`${t('filter.sizeRules', 'Size Rules')}: ${dimensions.join(', ')}`);
        }

        if (minAesthetic || maxAesthetic) {
            parts.push(`★ ${minAesthetic || 0}-${maxAesthetic || 10}`);
        }

        return parts;
    }

    function updateQueueFilterSummary() {
        const summaryEl = document.getElementById('qs-filter-summary');
        if (!summaryEl) return;
        const setSummaryText = (text, i18nKey = null) => {
            if (i18nKey) {
                summaryEl.setAttribute('data-i18n', i18nKey);
            } else {
                summaryEl.removeAttribute('data-i18n');
            }
            summaryEl.textContent = text;
        };

        let modeLabel = '';
        let parts = [];

        if (state.appliedFilterMode === 'gallery' && state.advancedFilters) {
            modeLabel = t('queueSolitaire.filterSummaryGallery', 'Using Gallery filters');
            parts = buildFilterSummaryPartsFromState(state.advancedFilters);
        } else if (state.appliedFilterMode === 'advanced' && state.advancedFilters) {
            modeLabel = t('queueSolitaire.filterSummaryAdvanced', 'Advanced queue filters');
            parts = buildFilterSummaryPartsFromState(state.advancedFilters);
        } else if (state.appliedFilterMode === 'quick') {
            modeLabel = t('queueSolitaire.filterSummaryQuick', 'Quick queue filters');
            parts = buildQuickFilterSummaryParts();
        }

        if (!modeLabel) {
            setSummaryText(
                t('queueSolitaire.filterSummaryIdle', 'No queue filters active yet.'),
                'queueSolitaire.filterSummaryIdle'
            );
            return;
        }

        if (!parts.length) {
            if (state.appliedFilterMode === 'gallery') {
                setSummaryText(
                    t('queueSolitaire.filterSummaryGalleryAll', 'Gallery filters were copied, but they currently leave the whole queue in scope.'),
                    'queueSolitaire.filterSummaryGalleryAll'
                );
            } else if (state.appliedFilterMode === 'advanced') {
                setSummaryText(
                    t('queueSolitaire.filterSummaryAdvancedAll', 'Advanced queue filters are applied, but they currently leave the whole queue in scope.'),
                    'queueSolitaire.filterSummaryAdvancedAll'
                );
            } else {
                setSummaryText(
                    t('queueSolitaire.filterSummaryQuickAll', 'Quick queue filters are clear, so the whole queue is currently in scope.'),
                    'queueSolitaire.filterSummaryQuickAll'
                );
            }
            return;
        }

        setSummaryText(`${modeLabel}: ${parts.join(' • ')}`);
    }

    function clearQuickFilterInputs() {
        [
            'qs-filter-tag',
            'qs-filter-checkpoint',
            'qs-filter-lora',
            'qs-filter-minw',
            'qs-filter-maxw',
            'qs-filter-minh',
            'qs-filter-maxh',
            'qs-filter-aesthetic',
            'qs-filter-aesthetic-max',
        ].forEach((id) => {
            const input = document.getElementById(id);
            if (input) input.value = '';
        });

        [
            'qs-filter-generator',
            'qs-filter-rating',
            'qs-filter-resolution',
            'qs-filter-aspect',
        ].forEach((id) => {
            const select = document.getElementById(id);
            if (select) select.value = '';
        });

        setFilterMatchCountText(t('queueSolitaire.initialMatching', '0 matching'), 'queueSolitaire.initialMatching');
    }

    function setQuickAdvancedVisible(visible) {
        state.quickAdvancedVisible = Boolean(visible);
        const advancedFields = document.getElementById('qs-filter-advanced-fields');
        const toggle = document.getElementById('qs-filter-advanced');
        if (advancedFields) advancedFields.hidden = !state.quickAdvancedVisible;
        if (toggle) {
            toggle.setAttribute('aria-expanded', state.quickAdvancedVisible ? 'true' : 'false');
            toggle.classList.toggle('active', state.quickAdvancedVisible);
        }
    }

    function toggleQuickAdvancedFields() {
        setQuickAdvancedVisible(!state.quickAdvancedVisible);
    }

    function createProfileSectionDraft() {
        return {
            id: `profile-sec-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
            name: t('queueSolitaire.profileSectionDefault', 'New Section'),
            color: SECTION_COLORS[state.profileDraftSections.length % SECTION_COLORS.length],
            filters: createFilterState(),
        };
    }

    function loadAutoSortProfiles() {
        try {
            const raw = localStorage.getItem(AUTO_SORT_PROFILES_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            state.autoSortProfiles = Array.isArray(parsed) ? parsed.map((profile) => ({
                id: String(profile.id || Date.now().toString(36)),
                name: String(profile.name || t('queueSolitaire.profileDefaultName', 'Queue Profile')),
                sections: Array.isArray(profile.sections) ? profile.sections.map((section, index) => ({
                    id: String(section.id || `profile-sec-${index}`),
                    name: String(section.name || `${t('queueSolitaire.section', 'Section')} ${index + 1}`),
                    color: SECTION_COLORS.includes(section.color) ? section.color : SECTION_COLORS[index % SECTION_COLORS.length],
                    filters: cloneFilters(section.filters || createFilterState()),
                })) : [],
            })) : [];
        } catch (_) {
            state.autoSortProfiles = [];
        }
    }

    function saveAutoSortProfiles() {
        localStorage.setItem(AUTO_SORT_PROFILES_KEY, JSON.stringify(state.autoSortProfiles));
        renderAutoSortProfileMenu();
    }

    function getSelectedProfile() {
        return state.autoSortProfiles.find((profile) => profile.id === state.editingProfileId) || null;
    }

    function syncDraftFromSelectedProfile() {
        const profile = getSelectedProfile();
        state.profileDraftSections = profile
            ? profile.sections.map((section) => ({
                id: section.id,
                name: section.name,
                color: section.color,
                filters: cloneFilters(section.filters || createFilterState()),
            }))
            : [];
    }

    function buildProfileSummary(filters) {
        const parts = buildFilterSummaryPartsFromState(filters);
        return parts.length
            ? parts.join(' • ')
            : t('queueSolitaire.filterSummaryIdle', 'No queue filters active yet.');
    }

    function renderAutoSortProfileMenu() {
        const container = document.getElementById('qs-auto-sort-profiles');
        if (!container) return;

        if (!state.autoSortProfiles.length) {
            container.innerHTML = `<div class="qs-auto-sort-section-label">${escapeQueueHtml(t('queueSolitaire.noProfilesYet', 'No saved profiles yet'))}</div>`;
            return;
        }

        container.innerHTML = state.autoSortProfiles.map((profile) => `
            <button type="button" class="qs-auto-sort-profile-btn" data-profile-id="${escapeQueueHtml(profile.id)}">
                ${escapeQueueHtml(profile.name)}
            </button>
        `).join('');

        container.querySelectorAll('[data-profile-id]').forEach((button) => {
            button.addEventListener('click', async () => {
                const autoSortMenu = document.getElementById('qs-auto-sort-menu');
                if (autoSortMenu) autoSortMenu.style.display = 'none';
                const profile = state.autoSortProfiles.find((entry) => entry.id === button.dataset.profileId);
                if (profile) {
                    await applyAutoSortProfile(profile);
                }
            });
        });
    }

    function openProfileManager() {
        if (!state.autoSortProfiles.length) {
            const profileId = `profile-${Date.now().toString(36)}`;
            state.autoSortProfiles.push({
                id: profileId,
                name: t('queueSolitaire.profileDefaultName', 'Queue Profile'),
                sections: [createProfileSectionDraft()],
            });
            saveAutoSortProfiles();
        }

        state.editingProfileId = state.autoSortProfiles[0]?.id || '';
        syncDraftFromSelectedProfile();
        renderProfileManager();
        const modal = document.getElementById('qs-profile-modal');
        if (modal) modal.hidden = false;
    }

    function closeProfileManager() {
        const modal = document.getElementById('qs-profile-modal');
        if (modal) modal.hidden = true;
    }

    function renderProfileManager() {
        const select = document.getElementById('qs-profile-select');
        const list = document.getElementById('qs-profile-sections');
        if (!select || !list) return;

        if (!state.editingProfileId && state.autoSortProfiles[0]) {
            state.editingProfileId = state.autoSortProfiles[0].id;
            syncDraftFromSelectedProfile();
        }

        select.innerHTML = state.autoSortProfiles.map((profile) => `
            <option value="${escapeQueueHtml(profile.id)}">${escapeQueueHtml(profile.name)}</option>
        `).join('');
        if (state.editingProfileId) {
            select.value = state.editingProfileId;
        }

        list.innerHTML = state.profileDraftSections.map((section, index) => `
            <div class="qs-profile-card" data-section-id="${escapeQueueHtml(section.id)}">
                <div class="qs-profile-card-top">
                    <input class="input-field qs-profile-name" value="${escapeQueueHtml(section.name)}" data-section-id="${escapeQueueHtml(section.id)}" aria-label="${escapeQueueHtml(t('queueSolitaire.sectionName', 'Section name'))}">
                    <select class="input-field qs-profile-color" data-section-id="${escapeQueueHtml(section.id)}" aria-label="${escapeQueueHtml(t('queueSolitaire.sectionColor', 'Section color'))}">
                        ${SECTION_COLORS.map((color) => `<option value="${escapeQueueHtml(color)}" ${section.color === color ? 'selected' : ''}>${escapeQueueHtml(color)}</option>`).join('')}
                    </select>
                    <button type="button" class="btn btn-secondary btn-small qs-profile-edit-filter" data-section-id="${escapeQueueHtml(section.id)}">${escapeQueueHtml(t('queueSolitaire.editSectionFilter', 'Edit Filter'))}</button>
                    <button type="button" class="btn btn-ghost btn-small qs-profile-remove-section" data-section-id="${escapeQueueHtml(section.id)}"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button>
                </div>
                <div class="qs-profile-card-summary">${escapeQueueHtml(buildProfileSummary(section.filters))}</div>
            </div>
        `).join('');

        list.querySelectorAll('.qs-profile-name').forEach((input) => {
            input.addEventListener('input', () => {
                const section = state.profileDraftSections.find((entry) => entry.id === input.dataset.sectionId);
                if (section) {
                    section.name = String(input.value || '').trim() || section.name;
                }
            });
        });

        list.querySelectorAll('.qs-profile-color').forEach((selectEl) => {
            selectEl.addEventListener('change', () => {
                const section = state.profileDraftSections.find((entry) => entry.id === selectEl.dataset.sectionId);
                if (section) {
                    section.color = selectEl.value;
                }
            });
        });

        list.querySelectorAll('.qs-profile-edit-filter').forEach((button) => {
            button.addEventListener('click', () => {
                const section = state.profileDraftSections.find((entry) => entry.id === button.dataset.sectionId);
                if (!section || !window.App?.openFilterModal) return;
                window.App.openFilterModal({
                    mode: 'queue-profile',
                    titleText: t('queueSolitaire.sectionFilterTitle', 'Section Filter'),
                    applyButtonText: t('queueSolitaire.applySectionFilter', 'Save Section Filter'),
                    resetButtonText: t('queueSolitaire.resetSectionFilter', 'Reset Section Filter'),
                    filterState: section.filters,
                    optionData: buildQueueOptionData(),
                    onApply: (filters) => {
                        section.filters = cloneFilters(filters);
                        renderProfileManager();
                    },
                    onReset: (filters) => {
                        section.filters = cloneFilters(filters);
                        renderProfileManager();
                    },
                });
            });
        });

        list.querySelectorAll('.qs-profile-remove-section').forEach((button) => {
            button.addEventListener('click', () => {
                state.profileDraftSections = state.profileDraftSections.filter((entry) => entry.id !== button.dataset.sectionId);
                renderProfileManager();
            });
        });
    }

    async function applyAutoSortProfile(profile) {
        if (!profile?.sections?.length) return;

        const allIds = getAllImageIds();
        await ensureImageDetails(allIds);

        const remaining = new Set(allIds);
        const nextSections = [];

        profile.sections.forEach((section, index) => {
            const matchedIds = [];
            for (const id of allIds) {
                if (!remaining.has(id)) continue;
                const meta = state.detailCache.get(id) || getImageMeta(id);
                if (matchesFilterState(meta, section.filters || createFilterState())) {
                    matchedIds.push(id);
                    remaining.delete(id);
                }
            }

            nextSections.push({
                id: index === 0 ? newSectionId() : newSectionId(),
                name: section.name || `${t('queueSolitaire.section', 'Section')} ${index + 1}`,
                color: section.color || SECTION_COLORS[index % SECTION_COLORS.length],
                items: matchedIds,
                collapsed: false,
            });
        });

        nextSections.push({
            id: 'unsorted',
            name: t('queueSolitaire.unsorted', 'Unsorted'),
            color: 'gray',
            items: Array.from(remaining),
            collapsed: false,
        });

        pushUndo(`Auto-sort profile "${profile.name}"`);
        state.sections = nextSections;
        state.filterMatches.clear();
        state.galleryFilterMode = false;
        state.advancedFilters = null;
        updateQueueFilterSummary();
        render();
        window.App?.showToast?.(
            t('queueSolitaire.profileApplied', 'Applied profile "{name}"', { name: profile.name }).replace('{name}', profile.name),
            'success'
        );
    }

    async function createAutoSortProfile() {
        const name = await window.App?.showInputModal?.(
            t('queueSolitaire.newProfileTitle', 'New Auto-Sort Profile'),
            t('queueSolitaire.newProfileMessage', 'Enter a name for this profile:'),
            `${t('queueSolitaire.profileDefaultName', 'Queue Profile')} ${state.autoSortProfiles.length + 1}`
        );
        if (!name) return;

        const profileId = `profile-${Date.now().toString(36)}`;
        state.autoSortProfiles.push({
            id: profileId,
            name: String(name).trim(),
            sections: [createProfileSectionDraft()],
        });
        state.editingProfileId = profileId;
        syncDraftFromSelectedProfile();
        saveAutoSortProfiles();
        renderProfileManager();
    }

    async function renameAutoSortProfile() {
        const profile = getSelectedProfile();
        if (!profile) return;
        const nextName = await window.App?.showInputModal?.(
            t('queueSolitaire.renameProfileTitle', 'Rename Profile'),
            t('queueSolitaire.renameProfileMessage', 'Enter the new profile name:'),
            profile.name
        );
        if (!nextName) return;
        profile.name = String(nextName).trim() || profile.name;
        saveAutoSortProfiles();
        renderProfileManager();
    }

    function deleteAutoSortProfile() {
        const profile = getSelectedProfile();
        if (!profile) return;
        state.autoSortProfiles = state.autoSortProfiles.filter((entry) => entry.id !== profile.id);
        state.editingProfileId = state.autoSortProfiles[0]?.id || '';
        syncDraftFromSelectedProfile();
        saveAutoSortProfiles();
        renderProfileManager();
    }

    function saveCurrentAutoSortProfile() {
        const profile = getSelectedProfile();
        if (!profile) return;
        profile.sections = state.profileDraftSections.map((section, index) => ({
            id: section.id,
            name: String(section.name || `${t('queueSolitaire.section', 'Section')} ${index + 1}`).trim(),
            color: section.color || SECTION_COLORS[index % SECTION_COLORS.length],
            filters: cloneFilters(section.filters || createFilterState()),
        }));
        saveAutoSortProfiles();
        renderProfileManager();
        window.App?.showToast?.(
            t('queueSolitaire.profileSaved', 'Saved profile "{name}"', { name: profile.name }).replace('{name}', profile.name),
            'success'
        );
    }

    function buildQueueOptionData() {
        const tagCounts = new Map();
        const promptCounts = new Map();
        const checkpointCounts = new Map();
        const loraCounts = new Map();

        for (const detail of state.detailCache.values()) {
            const tags = Array.isArray(detail.tags) ? detail.tags : [];
            for (const tag of tags) {
                if (!tag) continue;
                tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
            }

            const prompt = String(detail.prompt || '');
            prompt.split(',').map(token => token.trim()).filter(Boolean).forEach((token) => {
                promptCounts.set(token, (promptCounts.get(token) || 0) + 1);
            });

            const checkpoint = getNormalizedCheckpoint(detail);
            if (checkpoint) {
                checkpointCounts.set(checkpoint, (checkpointCounts.get(checkpoint) || 0) + 1);
            }

            const loraText = getLoraText(detail);
            loraText.split(/[,|]/).map(token => token.trim()).filter(Boolean).forEach((token) => {
                loraCounts.set(token, (loraCounts.get(token) || 0) + 1);
            });
        }

        const toSortedArray = (map, keyName) => Array.from(map.entries())
            .sort((a, b) => b[1] - a[1])
            .map(([name, count]) => ({ [keyName]: name, count }));

        return {
            tags: toSortedArray(tagCounts, 'tag'),
            prompts: toSortedArray(promptCounts, 'prompt'),
            checkpoints: toSortedArray(checkpointCounts, 'checkpoint'),
            loras: toSortedArray(loraCounts, 'lora'),
        };
    }

    function matchesFilterState(meta, filters) {
        if (!meta) return false;

        const selectedGenerators = Array.isArray(filters.generators) ? filters.generators : [];
        const selectedRatings = Array.isArray(filters.ratings) ? filters.ratings : [];
        const selectedTags = Array.isArray(filters.tags) ? filters.tags.map(tag => String(tag).toLowerCase()) : [];
        const selectedCheckpoints = Array.isArray(filters.checkpoints)
            ? filters.checkpoints.map(v => normalizeCheckpointValue(v).toLowerCase()).filter(Boolean)
            : [];
        const selectedLoras = Array.isArray(filters.loras) ? filters.loras.map(v => String(v).toLowerCase()) : [];
        const selectedPrompts = Array.isArray(filters.prompts) ? filters.prompts.map(v => String(v).toLowerCase()) : [];
        const artistQuery = String(filters.artist || '').trim().toLowerCase();
        const searchQuery = String(filters.search || '').trim().toLowerCase();

        const generatorValue = String(meta.generator || '').toLowerCase();
        const ratingValue = getRatingFromMeta(meta);
        const tags = Array.isArray(meta.tags) ? meta.tags.map(tag => String(tag).toLowerCase()) : [];
        const prompt = String(meta.prompt || '').toLowerCase();
        const filename = String(meta.filename || '').toLowerCase();
        const checkpoint = getNormalizedCheckpoint(meta).toLowerCase();
        const rawCheckpoint = String(meta.checkpoint || '').toLowerCase();
        const loras = getLoraText(meta);
        const aspect = getAspectBucket(meta);
        const width = Number(meta.width || 0);
        const height = Number(meta.height || 0);
        const aesthetic = meta.aesthetic_score == null ? null : Number(meta.aesthetic_score);
        const artist = getNormalizedArtist(meta);

        if (selectedGenerators.length && selectedGenerators.length < 5 && !selectedGenerators.includes(generatorValue)) return false;
        if (selectedRatings.length && selectedRatings.length < 4 && !selectedRatings.includes(ratingValue)) return false;
        if (selectedTags.length && !selectedTags.every(tag => tags.includes(tag))) return false;
        if (selectedCheckpoints.length && !selectedCheckpoints.includes(checkpoint)) return false;
        if (selectedLoras.length && !selectedLoras.every(lora => loras.includes(lora))) return false;
        if (selectedPrompts.length && !selectedPrompts.every(term => prompt.includes(term))) return false;
        if (artistQuery && artist !== artistQuery) return false;
        if (searchQuery && ![prompt, filename, checkpoint, rawCheckpoint, loras, tags.join(' '), artist].join(' ').includes(searchQuery)) return false;
        if (filters.minWidth && width < filters.minWidth) return false;
        if (filters.maxWidth && width > filters.maxWidth) return false;
        if (filters.minHeight && height < filters.minHeight) return false;
        if (filters.maxHeight && height > filters.maxHeight) return false;
        if (filters.aspectRatio && aspect !== filters.aspectRatio) return false;
        if (filters.minAesthetic != null && (aesthetic == null || aesthetic < filters.minAesthetic)) return false;
        if (filters.maxAesthetic != null && (aesthetic == null || aesthetic > filters.maxAesthetic)) return false;

        return true;
    }

    async function applyFilter() {
        state.galleryFilterMode = false;
        state.advancedFilters = null;
        state.appliedFilterMode = 'quick';
        updateQueueFilterSummary();
        const keyword = document.getElementById('qs-filter-tag')?.value?.trim().toLowerCase();
        const generator = document.getElementById('qs-filter-generator')?.value || '';
        const rating = document.getElementById('qs-filter-rating')?.value || '';
        const checkpointQuery = document.getElementById('qs-filter-checkpoint')?.value?.trim().toLowerCase();
        const loraQuery = document.getElementById('qs-filter-lora')?.value?.trim().toLowerCase();
        const minW = parseInt(document.getElementById('qs-filter-minw')?.value) || 0;
        const maxW = parseInt(document.getElementById('qs-filter-maxw')?.value) || 0;
        const minH = parseInt(document.getElementById('qs-filter-minh')?.value) || 0;
        const maxH = parseInt(document.getElementById('qs-filter-maxh')?.value) || 0;
        const aspect = document.getElementById('qs-filter-aspect')?.value || '';
        const minAesthetic = parseFloat(document.getElementById('qs-filter-aesthetic')?.value) || 0;
        const maxAesthetic = parseFloat(document.getElementById('qs-filter-aesthetic-max')?.value) || 0;
        const resolution = document.getElementById('qs-filter-resolution')?.value || '';

        state.filterMatches.clear();
        const allIds = getAllImageIds();
        await ensureImageDetails(allIds);

        for (const id of allIds) {
            const meta = state.detailCache.get(id) || getImageMeta(id);
            if (!meta) continue;
            let match = true;

            if (keyword) {
                const prompt = (meta.prompt || '').toLowerCase();
                const tags = Array.isArray(meta.tags) ? meta.tags.join(' ').toLowerCase() : '';
                const filename = (meta.filename || '').toLowerCase();
                const checkpoint = getNormalizedCheckpoint(meta).toLowerCase();
                const rawCheckpoint = String(meta.checkpoint || '').toLowerCase();
                const loras = getLoraText(meta);
                const generatorText = (meta.generator || '').toLowerCase();
                const artist = getNormalizedArtist(meta);
                const haystack = [prompt, tags, filename, checkpoint, rawCheckpoint, loras, generatorText, artist].join(' ');
                if (!haystack.includes(keyword)) match = false;
            }

            if (generator && String(meta.generator || '').toLowerCase() !== generator) match = false;
            if (rating && getRatingFromMeta(meta) !== rating) match = false;
            if (checkpointQuery) {
                const normalizedCheckpoint = getNormalizedCheckpoint(meta).toLowerCase();
                const rawCheckpoint = String(meta.checkpoint || '').toLowerCase();
                if (!normalizedCheckpoint.includes(checkpointQuery) && !rawCheckpoint.includes(checkpointQuery)) {
                    match = false;
                }
            }
            if (loraQuery && !getLoraText(meta).includes(loraQuery)) match = false;
            if (minW > 0 && (meta.width || 0) < minW) match = false;
            if (maxW > 0 && (meta.width || 0) > maxW) match = false;
            if (minH > 0 && (meta.height || 0) < minH) match = false;
            if (maxH > 0 && (meta.height || 0) > maxH) match = false;
            if (aspect && getAspectBucket(meta) !== aspect) match = false;
            if (minAesthetic > 0 && ((meta.aesthetic_score || 0) < minAesthetic)) match = false;
            if (maxAesthetic > 0 && ((meta.aesthetic_score || 0) > maxAesthetic)) match = false;
            if (resolution && getResolutionBucket(meta) !== resolution) match = false;

            if (match) state.filterMatches.add(id);
        }

        setFilterMatchCountText(
            t('queueSolitaire.matching', '{count} matching', { count: state.filterMatches.size })
                .replace('{count}', state.filterMatches.size)
        );

        updateQueueFilterSummary();
        render();
    }

    async function applyGalleryFilters() {
        const filters = window.App?.AppState?.filters || createFilterState();
        const buildContract = window.App?.buildAdvancedFilterContract;
        state.advancedFilters = typeof buildContract === 'function'
            ? buildContract(filters)
            : (window.App?.cloneFilterState
                ? window.App.cloneFilterState(filters)
                : JSON.parse(JSON.stringify(filters)));
        await applyFilterState(state.advancedFilters, true);
    }

    async function resolveGalleryFilterMatches(filters, imageIds) {
        const api = window.App?.API;
        if (!api?.createSelectionToken || !api?.getSelectionChunk) return null;

        const queueIds = Array.from(new Set((imageIds || [])
            .map((id) => Number(id))
            .filter((id) => Number.isFinite(id) && id > 0)));
        if (!queueIds.length) return new Set();

        const queueSet = new Set(queueIds);
        const matched = new Set();
        const limit = 5000;
        const tokenPayload = await api.createSelectionToken(filters, limit);
        const selectionToken = tokenPayload?.selection_token || tokenPayload?.token;
        if (!selectionToken) return null;

        let offset = 0;
        let guard = 0;
        while (guard < 200) {
            guard += 1;
            const chunk = await api.getSelectionChunk(selectionToken, { offset, limit });
            (chunk?.image_ids || []).forEach((id) => {
                const normalized = Number(id);
                if (queueSet.has(normalized)) matched.add(normalized);
            });

            if (!chunk?.has_more || matched.size >= queueSet.size) break;
            const nextOffset = Number(chunk.next_offset);
            if (!Number.isFinite(nextOffset) || nextOffset <= offset) break;
            offset = nextOffset;
        }

        return matched;
    }

    async function applyFilterState(filters, fromGallery = false) {
        state.galleryFilterMode = fromGallery;
        state.appliedFilterMode = fromGallery ? 'gallery' : 'advanced';
        updateQueueFilterSummary();
        state.filterMatches.clear();
        const allIds = getAllImageIds();

        if (fromGallery) {
            try {
                const backendMatches = await resolveGalleryFilterMatches(filters, allIds);
                if (backendMatches) {
                    state.filterMatches = backendMatches;
                    setFilterMatchCountText(
                        t('queueSolitaire.galleryMatching', '{count} matching (gallery filters)', { count: state.filterMatches.size })
                            .replace('{count}', state.filterMatches.size)
                    );
                    updateQueueFilterSummary();
                    render();
                    return;
                }
            } catch (error) {
                console.warn('Queue Manager could not resolve Gallery filters through the backend; using local queue filters.', error);
            }
        }

        await ensureImageDetails(allIds);

        for (const id of allIds) {
            const meta = state.detailCache.get(id) || getImageMeta(id);
            if (matchesFilterState(meta, filters)) state.filterMatches.add(id);
        }

        setFilterMatchCountText(
            (fromGallery
                ? t('queueSolitaire.galleryMatching', '{count} matching (gallery filters)', { count: state.filterMatches.size })
                : t('queueSolitaire.advancedMatching', '{count} matching (advanced filters)', { count: state.filterMatches.size }))
                .replace('{count}', state.filterMatches.size)
        );

        updateQueueFilterSummary();
        render();
    }

    function openAdvancedFilterModal() {
        const app = window.App;
        if (!app?.openFilterModal) return;
        state.advancedFilters = state.advancedFilters || (app.cloneFilterState
            ? app.cloneFilterState(app.AppState?.filters || {})
            : JSON.parse(JSON.stringify(app.AppState?.filters || {})));
        const optionData = buildQueueOptionData();

        app.openFilterModal({
            mode: 'queue-solitaire',
            titleText: t('queueSolitaire.advancedFiltersTitle', 'Queue Filters'),
            applyButtonText: t('queueSolitaire.applyQueueFilters', 'Apply to Queue'),
            resetButtonText: t('queueSolitaire.resetQueueFilters', 'Reset Queue Filters'),
            filterState: state.advancedFilters,
            optionData,
            onApply: async (filters) => {
                state.advancedFilters = filters;
                await applyFilterState(filters, false);
            },
            onReset: async (filters) => {
                state.advancedFilters = filters;
                await applyFilterState(filters, false);
            },
        });
    }

    function resetFilterForm() {
        clearQuickFilterInputs();
        state.galleryFilterMode = false;
        state.advancedFilters = null;
        state.appliedFilterMode = 'none';
        state.filterMatches.clear();
        setQuickAdvancedVisible(false);
        updateQueueFilterSummary();
        render();
    }

    function selectMatchedItems() {
        if (!state.filterMatches.size) return;
        state.selected.clear();
        for (const id of state.filterMatches) {
            state.selected.add(id);
        }
        render();
    }

    function moveFilteredToSection() {
        const targetId = document.getElementById('qs-filter-target')?.value;
        if (!targetId || !state.filterMatches.size) return;
        moveItems([...state.filterMatches], targetId);
        state.filterMatches.clear();
        render();
    }

    // ── Selection ─────────────────────────────────────────────────────
    function selectImage(id, event) {
        if (event?.ctrlKey || event?.metaKey) {
            if (state.selected.has(id)) state.selected.delete(id);
            else state.selected.add(id);
        } else if (event?.shiftKey) {
            // Range select within the same section
            // For simplicity, just add to selection
            state.selected.add(id);
        } else {
            state.selected.clear();
            state.selected.add(id);
        }
        state.previewId = id;
        render();
    }

    function selectAll(sectionId) {
        const section = state.sections.find(s => s.id === sectionId);
        if (!section) return;
        for (const id of section.items) state.selected.add(id);
        render();
    }

    // ── Rendering ─────────────────────────────────────────────────────
    function render() {
        const container = document.getElementById('qs-sections');
        if (!container) return;
        const previousScrollTop = container.scrollTop;

        // Stats
        const statsEl = document.getElementById('qs-stats');
        const totalImages = state.sections.reduce((sum, s) => sum + s.items.length, 0);
        if (statsEl) {
            const baseStats = t(
                'queueSolitaire.stats',
                '{images} images · {sections} sections · {selected} selected',
                { images: totalImages, sections: state.sections.length, selected: state.selected.size }
            )
                .replace('{images}', totalImages)
                .replace('{sections}', state.sections.length)
                .replace('{selected}', state.selected.size);
            const matchingSuffix = state.filterMatches.size
                ? ` · ${t('queueSolitaire.matching', '{count} matching', { count: state.filterMatches.size }).replace('{count}', state.filterMatches.size)}`
                : '';
            statsEl.textContent = `${baseStats}${matchingSuffix}`;
        }

        updateQueueFilterSummary();

        // Update filter target dropdown
        const filterTarget = document.getElementById('qs-filter-target');
        if (filterTarget) {
            const currentVal = filterTarget.value;
            filterTarget.innerHTML = state.sections.map(s =>
                `<option value="${escapeQueueHtml(s.id)}">${escapeQueueHtml(s.name)} (${s.items.length})</option>`
            ).join('');
            if (currentVal) filterTarget.value = currentVal;
        }

        // Render sections
        container.innerHTML = state.sections.map(section => {
            const isCollapsed = section.collapsed ? 'collapsed' : '';
            const sectionId = escapeQueueHtml(section.id);
            const sectionName = escapeQueueHtml(section.name);
            const sectionColor = escapeQueueHtml(safeSectionColor(section.color));
            const selectAllLabel = escapeQueueHtml(t('queueSolitaire.selectAll', 'Select all'));
            const batchRenameLabel = escapeQueueHtml(t('queueSolitaire.batchRename', 'Batch rename'));
            const removeSectionLabel = escapeQueueHtml(t('queueSolitaire.removeSection', 'Remove section'));
            const dropHereLabel = escapeQueueHtml(t('queueSolitaire.dropHere', 'Drop images here'));
            const gridItems = section.items.map(id => {
                const numericId = Number.parseInt(id, 10);
                if (!Number.isFinite(numericId)) return '';
                const safeId = String(numericId);
                const item = getQueueItem(numericId) || getQueueItem(id);
                const meta = getImageMeta(numericId) || getImageMeta(id);
                const isSelected = state.selected.has(numericId) || state.selected.has(id) ? 'selected' : '';
                const isActive = Number.parseInt(state.previewId, 10) === numericId ? 'active' : '';
                const isProcessed = item?.isProcessed ? 'processed' : '';
                const dotClass = item?.isProcessed ? 'processed' : (item?.regions?.length ? 'detected' : '');
                const aesthetic = meta?.aesthetic_score != null ? meta.aesthetic_score.toFixed(1) : '';
                const isFilterMatch = state.filterMatches.has(numericId) || state.filterMatches.has(id);
                const dimStyle = state.filterMatches.size > 0 && !isFilterMatch ? 'opacity:0.3;' : '';

                return `<div class="qs-thumb-wrap" data-id="${safeId}" style="${dimStyle}">
                    <img class="qs-thumb ${isSelected} ${isActive} ${isProcessed}"
                         src="${escapeQueueHtml(getThumbUrl(numericId))}" alt="" loading="lazy"
                         data-id="${safeId}" data-section="${sectionId}" draggable="true">
                    ${dotClass ? `<span class="qs-thumb-dot ${dotClass}"></span>` : ''}
                    ${aesthetic ? `<span class="qs-thumb-badge aesthetic">${escapeQueueHtml(aesthetic)}</span>` : ''}
                </div>`;
            }).join('');

            const emptyClass = section.items.length === 0 ? 'empty-drop-zone' : '';

            return `<div class="qs-section ${isCollapsed}" data-section="${sectionId}" data-color="${sectionColor}">
                <div class="qs-section-header" data-section="${sectionId}">
                    <span class="qs-section-color"></span>
                    <span class="qs-section-collapse">▼</span>
                    <input type="text" class="qs-section-name" value="${sectionName}" data-section="${sectionId}">
                    <span class="qs-section-count">(${section.items.length})</span>
                    <div class="qs-section-actions">
                        <button class="qs-section-btn qs-btn-select-all" data-section="${sectionId}" title="${selectAllLabel}" aria-label="${selectAllLabel}"><svg class="icon" aria-hidden="true"><use href="#i-check"/></svg></button>
                        <button class="qs-section-btn qs-btn-rename" data-section="${sectionId}" title="${batchRenameLabel}" aria-label="${batchRenameLabel}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg></button>
                        ${section.id !== 'unsorted' ? `<button class="qs-section-btn qs-btn-remove" data-section="${sectionId}" title="${removeSectionLabel}" aria-label="${removeSectionLabel}" style="color:var(--danger);">✕</button>` : ''}
                    </div>
                </div>
                <div class="qs-section-grid ${emptyClass}" data-section="${sectionId}">
                    ${gridItems || `<span style="color:var(--text-muted);font-size:11px;padding:8px;">${dropHereLabel}</span>`}
                </div>
            </div>`;
        }).join('');

        container.querySelectorAll('.qs-thumb-wrap[data-id]').forEach((wrap) => {
            const imageId = Number.parseInt(wrap.dataset.id, 10);
            const item = getQueueItem(imageId);
            const outcomeBadge = createCensorBatchOutcomeBadge(item, 'censor-batch-outcome-solitaire');
            if (outcomeBadge) wrap.appendChild(outcomeBadge);
        });

        // Bind events
        bindSectionEvents(container);

        // Update preview
        renderPreview();

        const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
        container.scrollTop = Math.min(previousScrollTop, maxScrollTop);
    }

    function renderPreview() {
        const imgEl = document.getElementById('qs-preview-img');
        const infoEl = document.getElementById('qs-preview-info');
        if (!imgEl || !infoEl) return;

        if (!state.previewId) {
            imgEl.src = '';
            imgEl.style.display = 'none';
            infoEl.innerHTML = `<span style="color:var(--text-muted)">${escapeQueueHtml(t('queueSolitaire.clickPreview', 'Click an image to preview'))}</span>`;
            return;
        }

        imgEl.src = getFullUrl(state.previewId);
        imgEl.style.display = '';
        const item = getQueueItem(state.previewId);
        const meta = getImageMeta(state.previewId);
        const dims = meta ? `${meta.width || '?'}×${meta.height || '?'}` : '';
        const aesthetic = meta?.aesthetic_score != null ? `★ ${meta.aesthetic_score.toFixed(1)}` : '';
        const checkpoint = getNormalizedCheckpoint(meta);
        const filename = escapeQueueHtml(item?.outputFilename || item?.originalFilename || state.previewId);

        infoEl.innerHTML = `
            <span class="qs-preview-filename">${filename}</span>
            ${dims ? `<span>${escapeQueueHtml(dims)}</span>` : ''}
            ${aesthetic ? `<span>${escapeQueueHtml(aesthetic)}</span>` : ''}
            ${checkpoint ? `<span>🧠 ${escapeQueueHtml(checkpoint)}</span>` : ''}
        `;
    }

    // ── Event Binding ─────────────────────────────────────────────────
    function bindSectionEvents(container) {
        // Thumbnail clicks
        container.querySelectorAll('.qs-thumb').forEach(thumb => {
            thumb.addEventListener('click', (e) => {
                e.stopPropagation();
                selectImage(parseInt(thumb.dataset.id), e);
            });

            // Drag start
            thumb.addEventListener('dragstart', (e) => {
                const id = parseInt(thumb.dataset.id);
                if (!state.selected.has(id)) {
                    state.selected.clear();
                    state.selected.add(id);
                }
                e.dataTransfer.setData('text/plain', JSON.stringify([...state.selected]));
                e.dataTransfer.effectAllowed = 'move';
                thumb.classList.add('dragging');
            });

            thumb.addEventListener('dragend', () => {
                thumb.classList.remove('dragging');
            });

            // Hover preview
            thumb.addEventListener('mouseenter', (e) => {
                clearTimeout(state.hoverTimer);
                state.hoverTimer = setTimeout(() => {
                    showHoverPreview(parseInt(thumb.dataset.id), e);
                }, 400);
            });

            thumb.addEventListener('mouseleave', () => {
                clearTimeout(state.hoverTimer);
                hideHoverPreview();
            });
        });

        // Section drop zones
        container.querySelectorAll('.qs-section-grid, .qs-section-header').forEach(zone => {
            zone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                zone.closest('.qs-section')?.classList.add('drag-over');
            });

            zone.addEventListener('dragleave', () => {
                zone.closest('.qs-section')?.classList.remove('drag-over');
            });

            zone.addEventListener('drop', (e) => {
                e.preventDefault();
                zone.closest('.qs-section')?.classList.remove('drag-over');
                const targetSectionId = zone.dataset.section || zone.closest('[data-section]')?.dataset.section;
                try {
                    const ids = JSON.parse(e.dataTransfer.getData('text/plain'));
                    if (Array.isArray(ids) && targetSectionId) {
                        moveItems(ids, targetSectionId);
                    }
                } catch {}
            });
        });

        // Section header clicks (collapse toggle)
        container.querySelectorAll('.qs-section-collapse').forEach(arrow => {
            arrow.addEventListener('click', (e) => {
                e.stopPropagation();
                const sectionEl = arrow.closest('.qs-section');
                const sectionId = sectionEl?.dataset.section;
                const section = state.sections.find(s => s.id === sectionId);
                if (section) {
                    section.collapsed = !section.collapsed;
                    render();
                }
            });
        });

        // Section name editing
        container.querySelectorAll('.qs-section-name').forEach(input => {
            input.addEventListener('change', (e) => {
                const sectionId = input.dataset.section;
                const section = state.sections.find(s => s.id === sectionId);
                if (section) section.name = input.value.trim() || section.name;
            });
            input.addEventListener('keydown', (e) => e.stopPropagation());
        });

        // Section action buttons
        container.querySelectorAll('.qs-btn-select-all').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                selectAll(btn.dataset.section);
            });
        });

        container.querySelectorAll('.qs-btn-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                removeSection(btn.dataset.section);
            });
        });

        container.querySelectorAll('.qs-btn-rename').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                batchRenameSection(btn.dataset.section);
            });
        });
    }

    // ── Hover Preview ─────────────────────────────────────────────────
    function showHoverPreview(id, event) {
        const popup = document.getElementById('qs-hover-preview');
        const img = document.getElementById('qs-hover-preview-img');
        if (!popup || !img) return;

        img.src = getThumbUrl(id) + '&size=384';
        popup.classList.add('visible');
        window.PopupPosition?.place(popup, {
            x: event.clientX + 16,
            y: event.clientY - 60,
            placement: 'point',
            margin: 8,
        });
    }

    function hideHoverPreview() {
        const popup = document.getElementById('qs-hover-preview');
        if (popup) popup.classList.remove('visible');
    }

    // ── Batch Rename ──────────────────────────────────────────────────
    async function batchRenameSection(sectionId) {
        const section = state.sections.find(s => s.id === sectionId);
        if (!section || !section.items.length) return;

        const title = t('queueSolitaire.renameTitle', 'Batch Rename Section');
        const message = t(
            'queueSolitaire.renameMessage',
            'Rename {count} images in "{section}". Enter a base name such as "keep".',
            { count: section.items.length, section: section.name }
        )
            .replace('{count}', section.items.length)
            .replace('{section}', section.name);
        const baseName = window.App?.showInputModal
            ? await window.App.showInputModal(title, message, section.name.toLowerCase().replace(/\s+/g, '_'))
            : window.prompt(message, section.name.toLowerCase().replace(/\s+/g, '_'));
        if (!baseName) return;

        const cs = getCensorState();
        section.items.forEach((id, index) => {
            const item = cs.queue.find(q => q.id === id);
            if (item) {
                const ext = item.originalFilename.match(/\.[^.]+$/)?.[0] || '.png';
                item.outputFilename = `${baseName}_${String(index + 1).padStart(3, '0')}${ext}`;
            }
        });

        window.App?.showToast?.(
            t(
                'queueSolitaire.renamed',
                'Renamed {count} images with prefix "{name}"',
                { count: section.items.length, name: baseName }
            )
                .replace('{count}', section.items.length)
                .replace('{name}', baseName),
            'success'
        );
    }

    // ── Keyboard Shortcuts ────────────────────────────────────────────
    function handleKeydown(e) {
        if (!state.active) return;
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

        // Number keys 1-9: move selected to section
        if (e.key >= '1' && e.key <= '9') {
            const idx = parseInt(e.key) - 1;
            if (idx < state.sections.length && state.selected.size > 0) {
                moveItems([...state.selected], state.sections[idx].id);
                state.selected.clear();
            }
            e.preventDefault();
            return;
        }

        // Ctrl+A: select all in focused/first section
        if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
            e.preventDefault();
            state.selected.clear();
            for (const section of state.sections) {
                for (const id of section.items) state.selected.add(id);
            }
            render();
            return;
        }

        // Ctrl+Z: undo
        if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
            e.preventDefault();
            const desc = popUndo();
            if (desc) {
                render();
                showUndoToast(
                    t('queueSolitaire.undoToast', 'Undone: {description}', { description: desc })
                        .replace('{description}', desc)
                );
            }
            return;
        }

        // Delete: move selected back to unsorted
        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (state.selected.size > 0) {
                moveItems([...state.selected], 'unsorted');
                state.selected.clear();
            }
            e.preventDefault();
            return;
        }

        // N: new section
        if (e.key === 'n' || e.key === 'N') {
            addNewSection();
            e.preventDefault();
            return;
        }

        // Escape or Enter: close
        if (e.key === 'Escape' || e.key === 'Enter') {
            close();
            e.preventDefault();
        }
    }

    function showUndoToast(msg) {
        const toast = document.getElementById('qs-undo-toast');
        if (!toast) return;
        toast.textContent = msg;
        toast.classList.add('visible');
        setTimeout(() => toast.classList.remove('visible'), 2000);
    }

    // ── Marquee Selection ─────────────────────────────────────────────
    let marqueeState = null;

    function handleMarqueeMouseMove(e) {
        if (!marqueeState) return;
        const x1 = Math.min(marqueeState.startX, e.clientX);
        const y1 = Math.min(marqueeState.startY, e.clientY);
        const x2 = Math.max(marqueeState.startX, e.clientX);
        const y2 = Math.max(marqueeState.startY, e.clientY);
        const el = marqueeState.el;
        if (el) {
            el.style.left = x1 + 'px';
            el.style.top = y1 + 'px';
            el.style.width = (x2 - x1) + 'px';
            el.style.height = (y2 - y1) + 'px';
        }
        // Highlight intersecting thumbnails
        document.querySelectorAll('.qs-thumb').forEach(thumb => {
            const rect = thumb.getBoundingClientRect();
            const intersects = !(rect.right < x1 || rect.left > x2 || rect.bottom < y1 || rect.top > y2);
            const id = parseInt(thumb.dataset.id);
            if (intersects) state.selected.add(id);
        });
    }

    function handleMarqueeMouseUp() {
        if (!marqueeState) return;
        if (marqueeState.el) marqueeState.el.style.display = 'none';
        marqueeState = null;
        render();
    }

    function bindMarqueeDocumentListeners() {
        document.removeEventListener('mousemove', handleMarqueeMouseMove);
        document.removeEventListener('mouseup', handleMarqueeMouseUp);
        document.addEventListener('mousemove', handleMarqueeMouseMove);
        document.addEventListener('mouseup', handleMarqueeMouseUp);
    }

    function unbindMarqueeDocumentListeners() {
        document.removeEventListener('mousemove', handleMarqueeMouseMove);
        document.removeEventListener('mouseup', handleMarqueeMouseUp);
        if (marqueeState?.el) marqueeState.el.style.display = 'none';
        marqueeState = null;
    }

    function initMarquee() {
        bindMarqueeDocumentListeners();
        if (_marqueeInitialized) return;
        const sections = document.getElementById('qs-sections');
        if (!sections) return;
        _marqueeInitialized = true;

        sections.addEventListener('mousedown', (e) => {
            if (e.button !== 0 || e.target.closest('.qs-thumb, .qs-section-header, button, input')) return;
            marqueeState = {
                startX: e.clientX, startY: e.clientY,
                el: document.getElementById('qs-marquee'),
            };
            if (marqueeState.el) marqueeState.el.style.display = 'block';
            e.preventDefault();
        });
    }

    // ── Open / Close ──────────────────────────────────────────────────
    async function addNewSection() {
        const title = t('queueSolitaire.newSectionTitle', 'New Section');
        const message = t('queueSolitaire.newSectionMessage', 'Enter a section name:');
        const name = window.App?.showInputModal
            ? await window.App.showInputModal(title, message, `Section ${state.sections.length}`)
            : window.prompt(message, `Section ${state.sections.length}`);
        if (!name) return;
        addSection(name);
        render();
    }

    function open() {
        state.active = true;
        state.selected.clear();
        state.previewId = null;
        state.undoStack = [];
        state.filterMatches.clear();
        state.appliedFilterMode = 'none';
        state.filterActive = true;
        state.detailCache.clear();
        state.galleryFilterMode = false;
        state.advancedFilters = null;
        setQuickAdvancedVisible(false);
        loadAutoSortProfiles();
        renderAutoSortProfileMenu();

        // Initialize sections from current queue
        const allIds = getAllImageIds();
        state.sections = [
            { id: 'unsorted', name: 'Unsorted', color: 'gray', items: [...allIds], collapsed: false },
        ];

        // Show the solitaire view
        const el = document.getElementById('queue-solitaire');
        if (el) el.classList.add('active');
        const filterBar = document.getElementById('qs-filter-bar');
        if (filterBar) {
            filterBar.classList.add('active');
            filterBar.style.display = 'flex';
        }
        const sections = document.getElementById('qs-sections');
        if (sections) sections.scrollTop = 0;
        clearQuickFilterInputs();

        // Hide the normal censor workspace children
        document.querySelectorAll('.censor-sidebar-v2, .censor-main-v2').forEach(el => {
            el.style.display = 'none';
        });

        document.addEventListener('keydown', handleKeydown);
        render();
        initMarquee();
    }

    function close() {
        state.active = false;

        // Apply section order back to CensorState.queue
        const cs = getCensorState();
        if (cs) {
            const orderedIds = state.sections.flatMap(s => s.items);
            const queueMap = new Map(cs.queue.map(item => [item.id, item]));
            cs.queue = orderedIds.map(id => queueMap.get(id)).filter(Boolean);
        }

        // Hide solitaire, show normal workspace
        const el = document.getElementById('queue-solitaire');
        if (el) el.classList.remove('active');
        const filterBar = document.getElementById('qs-filter-bar');
        if (filterBar) {
            filterBar.classList.toggle('active', state.filterActive);
            filterBar.style.display = state.filterActive ? 'flex' : 'none';
        }

        document.querySelectorAll('.censor-sidebar-v2, .censor-main-v2').forEach(el => {
            el.style.display = '';
        });

        document.removeEventListener('keydown', handleKeydown);
        unbindMarqueeDocumentListeners();

        // Re-render the normal queue
        if (typeof window.renderQueue === 'function') window.renderQueue();
        else if (typeof renderQueue === 'function') renderQueue();
    }

    // ── Toolbar Events (bound once) ───────────────────────────────────
    function initToolbar() {
        if (_toolbarInitialized) return;
        _toolbarInitialized = true;
        loadAutoSortProfiles();
        renderAutoSortProfileMenu();
        document.getElementById('qs-btn-add-section')?.addEventListener('click', addNewSection);
        document.getElementById('qs-btn-done')?.addEventListener('click', close);

        // Filter toggle
        document.getElementById('qs-btn-filter')?.addEventListener('click', () => {
            state.filterActive = !state.filterActive;
            const bar = document.getElementById('qs-filter-bar');
            if (bar) {
                bar.classList.toggle('active', state.filterActive);
                bar.style.display = state.filterActive ? 'flex' : 'none';
            }
        });

        // Filter apply
        document.getElementById('qs-filter-apply')?.addEventListener('click', applyFilter);
        document.getElementById('qs-filter-move')?.addEventListener('click', moveFilteredToSection);
        document.getElementById('qs-filter-advanced')?.addEventListener('click', toggleQuickAdvancedFields);
        document.getElementById('qs-filter-gallery')?.addEventListener('click', applyGalleryFilters);
        document.getElementById('qs-filter-reset')?.addEventListener('click', resetFilterForm);
        document.getElementById('qs-filter-select')?.addEventListener('click', selectMatchedItems);

        // Auto-sort
        const autoSortBtn = document.getElementById('qs-btn-auto-sort');
        const autoSortMenu = document.getElementById('qs-auto-sort-menu');
        autoSortBtn?.addEventListener('click', () => {
            if (autoSortMenu) autoSortMenu.style.display = autoSortMenu.style.display === 'none' ? 'block' : 'none';
        });

        document.getElementById('qs-sort-rating')?.addEventListener('click', () => {
            autoSortMenu.style.display = 'none';
            autoSortByRating();
        });
        document.getElementById('qs-sort-aesthetic')?.addEventListener('click', () => {
            autoSortMenu.style.display = 'none';
            autoSortByAesthetic();
        });
        document.getElementById('qs-sort-resolution')?.addEventListener('click', () => {
            autoSortMenu.style.display = 'none';
            autoSortByResolution();
        });
        document.getElementById('qs-manage-auto-sort')?.addEventListener('click', () => {
            if (autoSortMenu) autoSortMenu.style.display = 'none';
            openProfileManager();
        });

        document.getElementById('qs-profile-close')?.addEventListener('click', closeProfileManager);
        document.getElementById('qs-profile-backdrop')?.addEventListener('click', closeProfileManager);
        document.getElementById('qs-profile-new')?.addEventListener('click', createAutoSortProfile);
        document.getElementById('qs-profile-rename')?.addEventListener('click', renameAutoSortProfile);
        document.getElementById('qs-profile-delete')?.addEventListener('click', deleteAutoSortProfile);
        document.getElementById('qs-profile-save')?.addEventListener('click', saveCurrentAutoSortProfile);
        document.getElementById('qs-profile-add-section')?.addEventListener('click', () => {
            state.profileDraftSections.push(createProfileSectionDraft());
            renderProfileManager();
        });
        document.getElementById('qs-profile-apply')?.addEventListener('click', async () => {
            saveCurrentAutoSortProfile();
            const profile = getSelectedProfile();
            if (profile) {
                closeProfileManager();
                await applyAutoSortProfile(profile);
            }
        });
        document.getElementById('qs-profile-select')?.addEventListener('change', (event) => {
            state.editingProfileId = String(event.target.value || '');
            syncDraftFromSelectedProfile();
            renderProfileManager();
        });

        // Close auto-sort menu on outside click
        document.addEventListener('click', (e) => {
            if (autoSortMenu && !autoSortBtn?.contains(e.target) && !autoSortMenu.contains(e.target)) {
                autoSortMenu.style.display = 'none';
            }
        });
    }

    // ── Init ──────────────────────────────────────────────────────────
    function init() {
        initToolbar();
    }

    // Export
    window.QueueSolitaire = { open, close, init, state };

    document.addEventListener('languageChanged', () => {
        if (state.active) render();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
