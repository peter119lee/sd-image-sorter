/**
 * Aurora Phase 3 — Gallery toolbar (#25a): key:value search, quick filter
 * chips, and the sticky bottom batch action bar.
 *
 * Everything routes through the SAME FilterStore fields the filter modal
 * uses (window.App.updateFilters) — no parallel filter state. The action
 * bar's buttons are the original selection-panel buttons (same ids, same
 * app.js handlers); this module only owns bar visibility, the stats line,
 * the More dropdown, and the selection-scoped Tag button.
 */
(function () {
    'use strict';

    const SEARCH_DEBOUNCE_MS = 500;
    const CACHE_STATS_TTL_MS = 60000;
    const QUEUE_STATS_TTL_MS = 15000;

    // Legacy fallback aliases (used only if modules/gallery-search-query.js
    // failed to load — the full grammar lives there).
    const KEY_ALIASES = {
        tag: 'tag', '标签': 'tag',
        checkpoint: 'checkpoint', model: 'checkpoint', '模型': 'checkpoint',
        lora: 'lora',
        seed: 'seed',
    };

    // Search v2: FilterStore scalar fields the box may write, with the value
    // that means "not filtered". The box only ever clears fields IT set on a
    // previous apply (boxOwnedScalars) — modal/chip-set values are untouched.
    const SCALAR_DEFAULTS = {
        seed: null,
        minAesthetic: null, maxAesthetic: null, aestheticUnscored: null,
        minUserRating: null,
        minWidth: null, maxWidth: null, minHeight: null, maxHeight: null,
        aspectRatio: '',
        brightnessMin: null, brightnessMax: null,
        minSaturation: null, maxSaturation: null,
        colorTemperature: '', brightnessDistribution: '',
        hasMetadata: null, noCaption: null,
        folder: null, artist: null,
        dateFrom: null, dateTo: null,
    };

    const SUGGEST_DEBOUNCE_MS = 200;
    const SUGGEST_LIMIT = 12;
    // Library facet endpoints. checkpoints/loras/prompts live at the ROOT
    // /api/{facet}/library (backend/routers/tags.py APIRouter prefix '/api',
    // routes get_{checkpoints,loras,prompts}_library) — NOT under /api/tags/.
    // They were shipped with a stray '/tags/' segment in search-v2 (@92fa8c2),
    // which 404'd and, because refreshSuggest() silently hides the dropdown on
    // !resp.ok, left checkpoint:/lora:/prompt: autocomplete invisibly dead.
    const LIBRARY_ENDPOINTS = {
        tags: '/api/tags/library',
        checkpoints: '/api/checkpoints/library',
        loras: '/api/loras/library',
        prompts: '/api/prompts/library',
    };

    let searchTimer = null;
    let armedTagIds = null;
    let cacheStats = { at: 0, text: '' };
    let queueStats = { at: 0, text: '', unsupported: false };
    let boxOwnedScalars = new Set();
    let boxOwnedNarrows = new Set();
    // List values (tag:/-tag:/lora:/…) THIS box added to FilterStore, per
    // field. Clearing the box (or deleting the token) removes exactly these;
    // values added via the filter modal / sidebar are never touched.
    let boxOwnedListValues = Object.create(null);
    let suggestTimer = null;
    let suggestAbort = null;
    let suggestState = { items: [], active: -1, ctx: null };

    function t(key, fallback) {
        const v = window.I18n && typeof window.I18n.t === 'function' ? window.I18n.t(key) : null;
        return v && v !== key ? v : fallback;
    }

    function app() {
        return window.App || null;
    }

    // ------------------------------------------------------------------
    // Search box: parse the query language into filter fields.
    // Grammar + parsing live in modules/gallery-search-query.js; this side
    // owns applying the result onto FilterStore, the "understood as" chips,
    // the autocomplete dropdown, and the syntax-help modal.
    // ------------------------------------------------------------------

    function parseQuery(raw) {
        if (window.GallerySearchQuery) return window.GallerySearchQuery.parse(raw);
        // Legacy minimal fallback (tag/checkpoint/lora/seed only).
        const result = {
            tags: [], excludeTags: [], checkpoints: [], excludeCheckpoints: [],
            loras: [], excludeLoras: [], prompts: [], excludePrompts: [],
            generators: [], excludeGenerators: [], ratings: [], excludeRatings: [],
            excludeColors: [], scalars: {}, freeText: [], parts: [], warnings: [],
        };
        const tokens = String(raw || '').match(/(?:[^\s"]+|"[^"]*")+/g) || [];
        tokens.forEach((token) => {
            const sep = token.indexOf(':');
            if (sep > 0) {
                const key = KEY_ALIASES[token.slice(0, sep).toLowerCase()] || KEY_ALIASES[token.slice(0, sep)];
                const value = token.slice(sep + 1).replace(/^"|"$/g, '').trim();
                if (key && value) {
                    if (key === 'tag') { result.tags.push(value); return; }
                    if (key === 'checkpoint') { result.checkpoints.push(value); return; }
                    if (key === 'lora') { result.loras.push(value); return; }
                    if (key === 'seed') {
                        const n = Number(value);
                        if (Number.isFinite(n)) { result.scalars.seed = Math.trunc(n); return; }
                    }
                }
            }
            result.freeText.push(token.replace(/^"|"$/g, ''));
        });
        return result;
    }

    function applySearch(raw) {
        const a = app();
        if (!a || typeof a.updateFilters !== 'function') return;
        const parsed = parseQuery(raw);
        const added = [];
        const narrowsTouched = [];

        a.updateFilters((filters) => {
            // List tokens are box-owned declaratively: values THIS box added
            // follow the query text — dropping the token (or clearing the box)
            // removes them again. Values added via the filter modal / sidebar
            // chips are left alone, same as the scalar ownership below.
            const syncList = (values, field, label) => {
                // Filters restored from an older localStorage snapshot may
                // predate newer list fields (e.g. colorHues) — heal in place.
                if (!Array.isArray(filters[field])) filters[field] = [];
                const parsedValues = new Set(values || []);
                const owned = boxOwnedListValues[field] || (boxOwnedListValues[field] = new Set());
                owned.forEach((value) => {
                    if (parsedValues.has(value)) return;
                    owned.delete(value);
                    const index = filters[field].indexOf(value);
                    if (index !== -1) filters[field].splice(index, 1);
                });
                parsedValues.forEach((value) => {
                    if (!filters[field].includes(value)) {
                        filters[field].push(value);
                        owned.add(value);
                        added.push(`${label}:${value}`);
                    }
                });
            };
            syncList(parsed.tags, 'tags', 'tag');
            syncList(parsed.excludeTags, 'excludeTags', '-tag');
            syncList(parsed.checkpoints, 'checkpoints', 'checkpoint');
            syncList(parsed.excludeCheckpoints, 'excludeCheckpoints', '-checkpoint');
            syncList(parsed.loras, 'loras', 'lora');
            syncList(parsed.excludeLoras, 'excludeLoras', '-lora');
            syncList(parsed.prompts, 'prompts', 'prompt');
            syncList(parsed.excludePrompts, 'excludePrompts', '-prompt');
            syncList(parsed.excludeGenerators, 'excludeGenerators', '-generator');
            syncList(parsed.excludeRatings, 'excludeRatings', '-rating');
            syncList(parsed.excludeColors, 'excludeColors', '-color');
            syncList(parsed.colorHues, 'colorHues', 'color');
            syncList(parsed.excludeColorHues, 'excludeColorHues', '-color');

            // generator:/rating: NARROW their default-everything lists while the
            // token is present; removing the token restores the default set.
            const applyNarrow = (values, field) => {
                const unique = [...new Set(values || [])];
                if (unique.length) {
                    filters[field] = unique;
                    boxOwnedNarrows.add(field);
                    narrowsTouched.push(field);
                } else if (boxOwnedNarrows.has(field)) {
                    const defaults = typeof a.createDefaultFilterState === 'function'
                        ? a.createDefaultFilterState() : null;
                    if (defaults && Array.isArray(defaults[field])) {
                        filters[field] = defaults[field];
                    }
                    boxOwnedNarrows.delete(field);
                    narrowsTouched.push(field);
                }
            };
            applyNarrow(parsed.generators, 'generators');
            applyNarrow(parsed.ratings, 'ratings');

            // Scalars are box-owned: present → write; absent but previously
            // written by the box → reset to the not-filtered default.
            const nextOwned = new Set();
            Object.keys(SCALAR_DEFAULTS).forEach((field) => {
                if (Object.prototype.hasOwnProperty.call(parsed.scalars, field)) {
                    filters[field] = parsed.scalars[field];
                    nextOwned.add(field);
                } else if (boxOwnedScalars.has(field)) {
                    filters[field] = SCALAR_DEFAULTS[field];
                }
            });
            boxOwnedScalars = nextOwned;

            // Free text is declarative: the box owns it.
            filters.search = parsed.freeText.join(' ').trim();
        });

        if (added.length && typeof a.showToast === 'function') {
            a.showToast(
                t('gallerySearch.appliedToast', 'Added to filters: ') + added.join(', '),
                'info'
            );
        }
        if (narrowsTouched.includes('generators') && typeof a.syncGenTabsWithFilters === 'function') {
            a.syncGenTabsWithFilters();
        }
        if (typeof a.updateFilterSummary === 'function') a.updateFilterSummary();
        if (typeof a.loadImages === 'function') a.loadImages();
        syncFromFilters(a.AppState ? a.AppState.filters : null);
    }

    // ------------------------------------------------------------------
    // "Understood as" preview chips (live while typing)
    // ------------------------------------------------------------------

    function keyLabel(key) {
        if (!key) return '';
        const negated = key.startsWith('-');
        const base = negated ? key.slice(1) : key;
        const label = t(`searchKey.${base}`, base);
        return negated ? `${t('searchPreview.exclude', 'exclude')} ${label}` : label;
    }

    function renderSearchPreview(raw, parsed) {
        const host = document.getElementById('gallery-search-preview');
        if (!host) return;
        const hasQuery = String(raw || '').trim().length > 0;
        host.textContent = '';
        if (!hasQuery) {
            host.hidden = true;
            return;
        }

        const intro = document.createElement('span');
        intro.className = 'gsq-preview-label';
        intro.textContent = t('searchPreview.understood', 'Understood as:');
        host.appendChild(intro);

        (parsed.parts || []).forEach((part) => {
            if (part.kind === 'free') return; // merged into one chip below
            const chip = document.createElement('span');
            if (part.kind === 'warn') {
                chip.className = 'gsq-chip gsq-chip-warn';
                const reason = t(part.reasonKey, 'unrecognized value');
                chip.textContent = `${part.value} — ${reason}${part.hint ? ` (${part.hint})` : ''}`;
            } else {
                chip.className = 'gsq-chip';
                chip.textContent = part.op
                    ? `${keyLabel(part.key)} ${part.op} ${part.value}`
                    : `${keyLabel(part.key)}: ${part.value}`;
            }
            host.appendChild(chip);
        });

        const free = (parsed.freeText || []).join(' ').trim();
        if (free) {
            const chip = document.createElement('span');
            chip.className = 'gsq-chip gsq-chip-free';
            chip.textContent = `${t('searchPreview.free', 'free text')}: ${free}`;
            host.appendChild(chip);
        }

        host.hidden = host.childElementCount <= 1;
    }

    function syncClearButton(input, clearBtn) {
        if (clearBtn) clearBtn.hidden = !input.value;
    }

    // ------------------------------------------------------------------
    // Autocomplete: fuzzy value suggestions for the caret token.
    // tag/checkpoint/lora/prompt values come from the library endpoints
    // (?q= does normalized substring matching, Danbooru-search style, over
    // the user's OWN library, with usage counts); enum keys suggest locally.
    // ------------------------------------------------------------------

    function suggestHost() {
        return document.getElementById('gallery-search-suggest');
    }

    function hideSuggest() {
        const host = suggestHost();
        if (host) host.hidden = true;
        const input = document.getElementById('gallery-search-input');
        if (input) input.setAttribute('aria-expanded', 'false');
        suggestState = { items: [], active: -1, ctx: null };
        if (suggestAbort) { suggestAbort.abort(); suggestAbort = null; }
        if (suggestTimer) { clearTimeout(suggestTimer); suggestTimer = null; }
    }

    function isSuggestOpen() {
        const host = suggestHost();
        return !!host && !host.hidden && suggestState.items.length > 0;
    }

    function normalizeLibraryItems(data) {
        const array = data && (data.tags || data.checkpoints || data.loras || data.prompts || data.items);
        if (!Array.isArray(array)) return [];
        return array.map((item) => {
            if (typeof item === 'string') return { value: item, count: null };
            const value = item.tag ?? item.checkpoint ?? item.lora ?? item.prompt
                ?? item.token ?? item.name ?? item.value ?? null;
            const count = Number.isFinite(Number(item.count)) ? Number(item.count) : null;
            return value != null ? { value: String(value), count } : null;
        }).filter(Boolean);
    }

    function renderSuggest(items, ctx) {
        const host = suggestHost();
        const input = document.getElementById('gallery-search-input');
        if (!host || !input) return;
        host.textContent = '';
        suggestState = { items, active: items.length ? 0 : -1, ctx };
        if (!items.length) {
            host.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            return;
        }
        items.forEach((item, index) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'gsq-suggest-item' + (index === suggestState.active ? ' is-active' : '');
            btn.setAttribute('role', 'option');
            btn.dataset.value = item.value;
            const label = document.createElement('span');
            label.className = 'gsq-suggest-value';
            label.textContent = item.value;
            btn.appendChild(label);
            if (item.count != null) {
                const count = document.createElement('span');
                count.className = 'gsq-suggest-count';
                count.textContent = String(item.count);
                btn.appendChild(count);
            }
            // mousedown (not click): runs before the input loses focus.
            btn.addEventListener('mousedown', (event) => {
                event.preventDefault();
                acceptSuggestion(item.value);
            });
            host.appendChild(btn);
        });
        host.hidden = false;
        input.setAttribute('aria-expanded', 'true');
    }

    function moveSuggestActive(delta) {
        if (!suggestState.items.length) return;
        const count = suggestState.items.length;
        suggestState.active = ((suggestState.active + delta) % count + count) % count;
        const host = suggestHost();
        if (!host) return;
        Array.from(host.children).forEach((child, index) => {
            child.classList.toggle('is-active', index === suggestState.active);
        });
    }

    function acceptSuggestion(value) {
        const input = document.getElementById('gallery-search-input');
        const ctx = suggestState.ctx;
        if (!input || !ctx) return;
        const text = input.value;
        const needsQuotes = /\s/.test(value);
        const inserted = needsQuotes ? `"${value}"` : value;
        const next = text.slice(0, ctx.valueStart) + inserted + text.slice(ctx.tokenEnd);
        input.value = next + (next.endsWith(' ') ? '' : ' ');
        const caret = ctx.valueStart + inserted.length + 1;
        input.focus();
        try { input.setSelectionRange(caret, caret); } catch (e) { /* unsupported */ }
        hideSuggest();
        // Re-run the normal pipeline (preview + debounced apply + suggest).
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    async function refreshSuggest() {
        const input = document.getElementById('gallery-search-input');
        if (!input || !window.GallerySearchQuery) return;
        if (document.activeElement !== input) { hideSuggest(); return; }
        const ctx = window.GallerySearchQuery.suggestionContext(input.value, input.selectionStart);
        if (!ctx) { hideSuggest(); return; }

        if (ctx.source === 'enum') {
            const prefix = ctx.prefix.toLowerCase();
            const items = ctx.values
                .filter((value) => !prefix || value.toLowerCase().startsWith(prefix))
                .slice(0, SUGGEST_LIMIT)
                .map((value) => ({ value, count: null }));
            renderSuggest(items, ctx);
            return;
        }

        const endpoint = LIBRARY_ENDPOINTS[ctx.endpointKey];
        if (!endpoint) { hideSuggest(); return; }
        if (suggestAbort) suggestAbort.abort();
        suggestAbort = new AbortController();
        try {
            const resp = await fetch(
                `${endpoint}?q=${encodeURIComponent(ctx.prefix)}&limit=${SUGGEST_LIMIT}`,
                { signal: suggestAbort.signal }
            );
            if (!resp.ok) { hideSuggest(); return; }
            const data = await resp.json();
            // The caret may have moved while the request was in flight.
            const fresh = window.GallerySearchQuery.suggestionContext(input.value, input.selectionStart);
            if (!fresh || fresh.prefix !== ctx.prefix || fresh.key !== ctx.key) return;
            renderSuggest(normalizeLibraryItems(data).slice(0, SUGGEST_LIMIT), fresh);
        } catch (e) {
            if (e && e.name !== 'AbortError') hideSuggest();
        }
    }

    function scheduleSuggest() {
        if (suggestTimer) clearTimeout(suggestTimer);
        suggestTimer = setTimeout(() => {
            suggestTimer = null;
            refreshSuggest();
        }, SUGGEST_DEBOUNCE_MS);
    }

    // ------------------------------------------------------------------
    // Syntax help modal (rows rendered from the parser's own table)
    // ------------------------------------------------------------------

    let helpRendered = false;

    function renderSearchHelp() {
        if (helpRendered) return;
        const host = document.getElementById('search-help-rows');
        if (!host || !window.GallerySearchQuery) return;
        host.textContent = '';
        window.GallerySearchQuery.SYNTAX_ROWS.forEach((row) => {
            const line = document.createElement('div');
            line.className = 'search-help-row';
            const syntax = document.createElement('code');
            syntax.className = 'search-help-syntax';
            syntax.textContent = row.syntax;
            const example = document.createElement('code');
            example.className = 'search-help-example';
            example.textContent = row.example;
            const desc = document.createElement('span');
            desc.className = 'search-help-desc';
            desc.textContent = t(row.descKey, row.descKey);
            line.append(syntax, example, desc);
            host.appendChild(line);
        });
        helpRendered = true;
    }

    function wireSearchSideButtons() {
        const helpBtn = document.getElementById('btn-search-help');
        if (helpBtn) {
            helpBtn.addEventListener('click', () => {
                const a = app();
                renderSearchHelp();
                if (a && typeof a.showModal === 'function') a.showModal('search-help-modal');
            });
        }
        const closeBtn = document.getElementById('btn-close-search-help');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                const a = app();
                if (a && typeof a.hideModal === 'function') a.hideModal('search-help-modal');
            });
        }
        // Language switch re-translates static labels but the help rows are
        // JS-rendered — drop the cache so the next open re-renders. i18n.js
        // dispatches "languageChanged" (camelCase) on document; the browser's
        // own window "languagechange" never fires for in-app switches.
        document.addEventListener('languageChanged', () => {
            helpRendered = false;
            // Re-render immediately if the modal is open right now.
            const modal = document.getElementById('search-help-modal');
            if (modal && modal.classList.contains('visible')) renderSearchHelp();
        });
        document.addEventListener('i18n:changed', () => { helpRendered = false; });

        const filterBtn = document.getElementById('btn-toolbar-filters');
        if (filterBtn) {
            filterBtn.addEventListener('click', () => {
                const a = app();
                if (a && typeof a.openFilterModal === 'function') a.openFilterModal();
            });
        }
    }

    function wireSearch() {
        const input = document.getElementById('gallery-search-input');
        const clearBtn = document.getElementById('gallery-search-clear');
        if (!input) return;

        input.addEventListener('keydown', (event) => {
            if (isSuggestOpen()) {
                if (event.key === 'ArrowDown') { event.preventDefault(); moveSuggestActive(1); return; }
                if (event.key === 'ArrowUp') { event.preventDefault(); moveSuggestActive(-1); return; }
                if (event.key === 'Tab' || (event.key === 'Enter' && suggestState.active >= 0)) {
                    event.preventDefault();
                    acceptSuggestion(suggestState.items[Math.max(0, suggestState.active)].value);
                    return;
                }
                if (event.key === 'Escape') {
                    // Only closes the dropdown — entry-page.js defers to us via
                    // its OVERLAY_SELECTOR check on #gallery-search-suggest.
                    event.preventDefault();
                    event.stopPropagation();
                    hideSuggest();
                    return;
                }
            }
            if (event.key === 'Enter') {
                event.preventDefault();
                if (searchTimer) { clearTimeout(searchTimer); searchTimer = null; }
                hideSuggest();
                applySearch(input.value);
            }
        });
        input.addEventListener('input', () => {
            syncClearButton(input, clearBtn);
            renderSearchPreview(input.value, parseQuery(input.value));
            scheduleSuggest();
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                searchTimer = null;
                applySearch(input.value);
            }, SEARCH_DEBOUNCE_MS);
        });
        // Caret moves without input (click / arrow keys) re-anchor suggestions.
        input.addEventListener('click', scheduleSuggest);
        input.addEventListener('keyup', (event) => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') scheduleSuggest();
        });
        input.addEventListener('blur', () => {
            // mousedown-accept runs before blur; a plain blur just closes.
            setTimeout(() => hideSuggest(), 0);
        });
        // Clear-contract freeze (B1 / P2-1):
        // - #gallery-search-clear is the ONLY search clear control (input is
        //   type=text, so there is no native webkit search ✕).
        // - Clicking it runs applySearch('') which removes box-owned list
        //   values, box-owned scalars, and box-owned gen/rating narrows only.
        // - Modal/sidebar-owned filters (and #btn-clear-filters) are separate:
        //   full reset goes through resetAllFilters, not this button.
        if (clearBtn) {
            if (!clearBtn.getAttribute('aria-label')) {
                clearBtn.setAttribute('aria-label', t('gallerySearch.clear', 'Clear search'));
            }
            clearBtn.addEventListener('click', () => {
                input.value = '';
                syncClearButton(input, clearBtn);
                renderSearchPreview('', parseQuery(''));
                hideSuggest();
                applySearch('');
                input.focus();
            });
        }
    }

    // ------------------------------------------------------------------
    // Quick chips — thin toggles over FilterStore fields
    // ------------------------------------------------------------------

    const CHIPS = [
        {
            id: 'chip-has-metadata',
            isActive: (f) => f.hasMetadata === true,
            toggle: (f, on) => { f.hasMetadata = on ? true : null; },
        },
        {
            id: 'chip-aesthetic-7',
            isActive: (f) => Number(f.minAesthetic) >= 7 && f.aestheticUnscored !== true,
            toggle: (f, on) => {
                f.minAesthetic = on ? 7 : null;
                if (on) f.aestheticUnscored = null;
            },
        },
        {
            id: 'chip-no-caption',
            isActive: (f) => f.noCaption === true,
            toggle: (f, on) => { f.noCaption = on ? true : null; },
        },
    ];

    function wireChips() {
        CHIPS.forEach((chip) => {
            const btn = document.getElementById(chip.id);
            if (!btn) return;
            btn.addEventListener('click', () => {
                const a = app();
                if (!a || typeof a.updateFilters !== 'function') return;
                const currentlyActive = btn.getAttribute('aria-pressed') === 'true';
                a.updateFilters((filters) => chip.toggle(filters, !currentlyActive));
                if (typeof a.updateFilterSummary === 'function') a.updateFilterSummary();
                if (typeof a.loadImages === 'function') a.loadImages();
                syncFromFilters(a.AppState ? a.AppState.filters : null);
            });
        });
    }

    function syncFromFilters(filters) {
        if (!filters) return;
        CHIPS.forEach((chip) => {
            const btn = document.getElementById(chip.id);
            if (!btn) return;
            const active = !!chip.isActive(filters);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
            btn.classList.toggle('is-active', active);
        });
    }

    // ------------------------------------------------------------------
    // Bottom action bar
    // ------------------------------------------------------------------

    function closeMoreMenu() {
        const toggle = document.getElementById('btn-gallery-action-more');
        const menu = document.getElementById('gallery-action-more-menu');
        if (menu) menu.hidden = true;
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }

    function wireMoreMenu() {
        const toggle = document.getElementById('btn-gallery-action-more');
        const menu = document.getElementById('gallery-action-more-menu');
        if (!toggle || !menu) return;

        const close = closeMoreMenu;

        toggle.addEventListener('click', (event) => {
            event.stopPropagation();
            const isOpen = !menu.hidden;
            menu.hidden = isOpen;
            toggle.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        });
        // Any action click closes the menu (the handlers themselves live in app.js).
        menu.addEventListener('click', () => close());
        document.addEventListener('click', (event) => {
            if (!menu.hidden && !menu.contains(event.target) && event.target !== toggle) close();
        });
        // Capture phase + stopPropagation: with the menu open, ESC closes ONLY
        // the menu — it must not fall through to app.js (which would also exit
        // selection mode) or entry-page.js (which would jump to the entry).
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !menu.hidden) {
                event.stopPropagation();
                event.preventDefault();
                close();
            }
        }, true);
    }

    function formatBytes(bytes) {
        const n = Number(bytes) || 0;
        if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)}G`;
        return `${Math.round(n / (1024 * 1024))}M`;
    }

    async function refreshBarStats() {
        const statsEl = document.getElementById('gallery-action-bar-stats');
        if (!statsEl) return;
        const now = Date.now();

        if (now - cacheStats.at > CACHE_STATS_TTL_MS) {
            cacheStats.at = now;
            try {
                const resp = await fetch('/api/thumbnail-cache/stats');
                if (resp.ok) {
                    const data = await resp.json();
                    const bytes = data.total_size_bytes ?? data.stats?.total_size_bytes;
                    if (bytes != null) {
                        cacheStats.text = `${t('actionBar.statsCache', 'Cache')} ${formatBytes(bytes)}`;
                    }
                }
            } catch (e) { /* stats are decorative — never block the bar */ }
        }

        if (!queueStats.unsupported && now - queueStats.at > QUEUE_STATS_TTL_MS) {
            queueStats.at = now;
            try {
                const resp = await fetch('/api/tags/pipeline-queue');
                if (resp.status === 404) {
                    queueStats.unsupported = true;
                } else if (resp.ok) {
                    const data = await resp.json();
                    const depth = Number(data.total_queued || 0);
                    queueStats.text = depth > 0 ? `${t('actionBar.statsQueue', 'Queue')} ${depth}` : '';
                }
            } catch (e) { /* ignore */ }
        }

        renderBarStats();
    }

    function renderBarStats() {
        const statsEl = document.getElementById('gallery-action-bar-stats');
        if (!statsEl) return;
        const parts = [];
        const st = app() && app().AppState;
        const selectedCount = statsEl.dataset.selectedCount || '';
        if (selectedCount) {
            parts.push(`${t('actionBar.statsSelected', 'Selected')} ${selectedCount}`);
        }
        const countEl = document.getElementById('image-count');
        if (countEl && countEl.textContent.trim()) parts.push(countEl.textContent.trim());
        if (cacheStats.text) parts.push(cacheStats.text);
        if (queueStats.text) parts.push(queueStats.text);
        statsEl.textContent = parts.join(' · ');
        void st; // (AppState reserved for future stats)
    }

    function syncActionBar(options) {
        const bar = document.getElementById('gallery-action-bar');
        if (!bar) return;
        const visible = !!(options && options.visible);
        bar.hidden = !visible;
        // Hiding the bar with the More menu open would otherwise leave the
        // menu pre-opened the next time a selection brings the bar back.
        if (!visible) {
            closeMoreMenu();
            return;
        }

        const statsEl = document.getElementById('gallery-action-bar-stats');
        if (statsEl) {
            statsEl.dataset.selectedCount = String(options.selectedCount ?? '');
        }

        const tagBtn = document.getElementById('btn-tag-selected');
        if (tagBtn) {
            const tokenScoped = !!options.tokenScoped;
            tagBtn.disabled = tokenScoped;
            tagBtn.title = tokenScoped
                ? t('actionBar.tagSelectedTokenHint', 'Select-all-matching selections tag via the main AI Tag entry (whole library / untagged).')
                : t('actionBar.tagSelectedTooltip', 'AI-tag the selected images');
        }

        renderBarStats();
        refreshBarStats();
    }

    // ------------------------------------------------------------------
    // Selection-scoped tagging
    // ------------------------------------------------------------------

    function showScopeNote(count) {
        const note = document.getElementById('tag-scope-note');
        const text = document.getElementById('tag-scope-note-text');
        if (!note || !text) return;
        text.textContent = t('tagModal.scopeSelected', 'Tagging only the {count} selected images.')
            .replace('{count}', String(count));
        note.hidden = false;
    }

    function disarmTagSelection() {
        armedTagIds = null;
        const note = document.getElementById('tag-scope-note');
        if (note) note.hidden = true;
    }

    function wireTagSelected() {
        const btn = document.getElementById('btn-tag-selected');
        if (btn) {
            btn.addEventListener('click', () => {
                const a = app();
                const st = a && a.AppState;
                if (!a || !st || st.selectionToken) return;
                const ids = Array.from(st.selectedIds || []);
                if (!ids.length) return;
                armedTagIds = ids;
                showScopeNote(ids.length);
                if (typeof a.showModal === 'function') a.showModal('tag-modal');
                // Aurora Phase 3 (#25b): [打标] lands on the 智能一趟 (Smart Tag)
                // tab with this selection scope, regardless of the last-used tab.
                try { window.V321Integration?.setTaggerTab?.('smart'); } catch (_e) { /* tagger UI not ready */ }
            });
        }
        // The global AI Tag entry always means "whole library" — disarm.
        const globalTagBtn = document.getElementById('btn-tag');
        if (globalTagBtn) globalTagBtn.addEventListener('click', disarmTagSelection);
        const clearBtn = document.getElementById('btn-tag-scope-clear');
        if (clearBtn) clearBtn.addEventListener('click', disarmTagSelection);
    }

    /** Called by app.js startTagging(): returns armed ids (or null). */
    function consumeTagSelectionIds() {
        return Array.isArray(armedTagIds) && armedTagIds.length ? [...armedTagIds] : null;
    }

    // ------------------------------------------------------------------

    function boot() {
        wireSearch();
        wireSearchSideButtons();
        wireChips();
        wireMoreMenu();
        wireTagSelected();
        const a = app();
        if (a && a.AppState) syncFromFilters(a.AppState.filters);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.GalleryToolbar = {
        syncActionBar,
        syncFromFilters,
        consumeTagSelectionIds,
        disarmTagSelection,
        parseQuery,
    };
})();
