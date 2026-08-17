/**
 * Smart Folders v1 — pinned filter presets as live sidebar entries.
 *
 * Eagle-style smart folders: a saved filter preset can be PINNED from the
 * filter modal's presets bar; every pinned preset becomes a persistent entry
 * in the gallery sidebar showing a LIVE image count (POST /api/images/count,
 * the same filter contract selection-ids uses). Clicking an entry applies the
 * preset through the exact same path the presets UI uses
 * (window.loadFilterPreset), so filter semantics are untouched.
 *
 * Pins live in their own localStorage key BESIDE the presets store —
 * 'sd-image-sorter-filter-presets' is never touched, so existing presets
 * keep their schema and behavior. Orphaned pins (preset deleted) are pruned
 * on read.
 *
 * Counts refresh when the gallery reloads ('gallery-images-loaded', fresh
 * loads only) and when tagging completes, debounced so bursts of reloads
 * produce one recount sweep.
 */
(function () {
    'use strict';

    const PINS_KEY = 'sd-image-sorter-smart-folder-pins';
    const PRESETS_KEY = 'sd-image-sorter-filter-presets';
    const MAX_VISIBLE = 8;
    const RECOUNT_DEBOUNCE_MS = 600;

    // Dynamic-string helper (separation-console.js pattern): static markup in
    // index.html uses data-i18n keys instead.
    function t(en, zh) {
        try {
            const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
            return String(lang).toLowerCase().startsWith('zh') ? zh : en;
        } catch (_) { return en; }
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    function readJson(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (_) {
            return fallback;
        }
    }

    const SmartFoldersUI = {
        _initialized: false,
        _counts: {},        // preset name -> number (NaN = count failed)
        _countEpoch: 0,
        _recountTimer: null,

        init() {
            if (this._initialized) {
                this.refresh();
                return;
            }
            this._initialized = true;

            document.getElementById('smart-folders-list')
                ?.addEventListener('click', (event) => this._onListClick(event));
            document.getElementById('btn-smart-folders-manage')
                ?.addEventListener('click', () => this._openManage());

            // Live counts: recount when the gallery reloads with fresh data.
            // Append pages don't change library contents, so skip them.
            window.addEventListener('gallery-images-loaded', (event) => {
                if (event?.detail?.appendMode) return;
                this._scheduleRecount();
            });
            // New tags can change what a preset matches (tag/rating filters).
            document.addEventListener('taggingCompleted', () => this._scheduleRecount());
            // Rows are JS-rendered (no data-i18n), so re-render on language switch.
            document.addEventListener('languageChanged', () => this._render());

            this.refresh();
        },

        // ---- pin store -------------------------------------------------
        _presets() {
            const presets = readJson(PRESETS_KEY, {});
            return presets && typeof presets === 'object' && !Array.isArray(presets) ? presets : {};
        },

        getPins() {
            const stored = readJson(PINS_KEY, []);
            if (!Array.isArray(stored)) return [];
            const presets = this._presets();
            // Prune orphans (preset renamed away / deleted / cleared storage)
            // without persisting here — reads stay side-effect free.
            return stored.filter((name) => typeof name === 'string' && presets[name]);
        },

        _writePins(pins) {
            try {
                localStorage.setItem(PINS_KEY, JSON.stringify(pins));
            } catch (_) {
                /* private mode / quota — pins just won't persist */
            }
        },

        isPinned(name) {
            return this.getPins().includes(name);
        },

        togglePin(name) {
            if (!name || !this._presets()[name]) return false;
            const pins = this.getPins();
            const next = pins.includes(name)
                ? pins.filter((pin) => pin !== name)
                : [...pins, name];
            this._writePins(next);
            this.refresh();
            return next.includes(name);
        },

        handlePresetDeleted(name) {
            const pins = this.getPins().filter((pin) => pin !== name);
            this._writePins(pins);
            this.refresh();
        },

        // ---- rendering ---------------------------------------------------
        refresh() {
            this._render();
            this._scheduleRecount(50);
        },

        _render() {
            const section = document.getElementById('smart-folders-section');
            const list = document.getElementById('smart-folders-list');
            if (!section || !list) return;

            const pins = this.getPins();
            section.hidden = pins.length === 0;
            if (pins.length === 0) {
                list.innerHTML = '';
                return;
            }

            const visible = pins.slice(0, MAX_VISIBLE);
            const rows = visible.map((name) => this._rowHtml(name)).join('');
            const overflow = pins.length > MAX_VISIBLE
                ? `<button type="button" class="smart-folder-more" data-smart-folder-manage="1">${escapeHtml(
                    t('+{count} more — manage presets', '还有 {count} 个 — 管理预设')
                        .replace('{count}', String(pins.length - MAX_VISIBLE)))}</button>`
                : '';
            list.innerHTML = rows + overflow;
        },

        _rowHtml(name) {
            const safeName = escapeHtml(name);
            const count = this._counts[name];
            let countText = '…';
            if (typeof count === 'number') {
                countText = Number.isNaN(count) ? '—' : String(count);
            }
            const applyTitle = escapeHtml(t('Apply this filter preset', '应用此筛选预设'));
            return `<button type="button" class="smart-folder-row" data-smart-folder="${safeName}" title="${applyTitle}">`
                + '<span class="smart-folder-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-folders"/></svg></span>'
                + `<span class="smart-folder-name">${safeName}</span>`
                + `<span class="smart-folder-count" data-smart-folder-count="${safeName}">${escapeHtml(countText)}</span>`
                + '</button>';
        },

        _updateRowCount(name) {
            const list = document.getElementById('smart-folders-list');
            if (!list) return;
            for (const node of list.querySelectorAll('[data-smart-folder-count]')) {
                if (node.getAttribute('data-smart-folder-count') !== name) continue;
                const count = this._counts[name];
                node.textContent = (typeof count === 'number' && !Number.isNaN(count)) ? String(count) : '—';
            }
        },

        // ---- live counts ---------------------------------------------------
        _scheduleRecount(delay = RECOUNT_DEBOUNCE_MS) {
            if (this._recountTimer) clearTimeout(this._recountTimer);
            this._recountTimer = setTimeout(() => {
                this._recountTimer = null;
                this._recount();
            }, delay);
        },

        async _recount() {
            const pins = this.getPins().slice(0, MAX_VISIBLE);
            if (pins.length === 0) return;
            const presets = this._presets();
            const api = window.App?.API;
            if (!api?.countImages) return;

            const epoch = ++this._countEpoch;
            // Sequential on purpose: pinned folders are ≤8 cheap COUNTs and this
            // keeps the local backend from seeing a burst of parallel queries.
            for (const name of pins) {
                const preset = presets[name];
                if (!preset) continue;
                try {
                    const result = await api.countImages(preset);
                    if (epoch !== this._countEpoch) return; // superseded sweep
                    this._counts[name] = Number(result?.count);
                } catch (error) {
                    if (epoch !== this._countEpoch) return;
                    this._counts[name] = NaN;
                    window.Logger?.warn?.('Smart folder count failed', error);
                }
                this._updateRowCount(name);
            }
        },

        // ---- interactions ---------------------------------------------------
        _onListClick(event) {
            const manage = event.target.closest('[data-smart-folder-manage]');
            if (manage) {
                this._openManage();
                return;
            }
            const row = event.target.closest('[data-smart-folder]');
            if (!row) return;
            const name = row.getAttribute('data-smart-folder');
            // Apply through the exact same path the presets UI uses so the
            // full filter state (FilterStore sync, gen tabs, reload, toast)
            // behaves identically to loading the preset from the modal.
            window.loadFilterPreset?.(name);
        },

        _openManage() {
            window.App?.openFilterModal?.();
        },
    };

    window.SmartFoldersUI = SmartFoldersUI;
})();
