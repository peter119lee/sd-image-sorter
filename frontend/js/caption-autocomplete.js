/**
 * Tag autocomplete (v3.5.0 — upgraded from the v3.2.2 single-surface version).
 *
 * Shared type-ahead for every comma-separated tag input:
 *   - Dataset Maker caption editor  (#dataset-editor-textarea)
 *   - Image detail tag editor       (#modal-tags-add-input)
 *   - Mass tag editor "add" box     (#mass-tag-add-tags)
 *   - Caption-editor export preview (.export-preview-main-textarea, attached
 *     by v321-ui.js each render)
 *
 * Source: GET /api/tags/suggest — the user's own library tags merged with
 * the bundled danbooru vocabulary (alias-aware; CJK queries match the
 * bundled MIT Chinese/Japanese alias table). Falls back to a tiny local
 * list when the endpoint is unreachable.
 *
 * Behaviour rules (unchanged from v3.2.2):
 *   - Suggestion-style only — never blocks free typing. Any keystroke
 *     not in {Tab, Enter, Escape, ArrowUp, ArrowDown} commits as raw text.
 *   - Trigger on tag-like ASCII tokens (>= 2 chars) or CJK tokens (>= 1).
 *   - Skip Natural-Language prose: token contains a space AND length > 6.
 *   - Tab/Enter accepts the highlighted suggestion; commits replace the
 *     current token in place and add ", " for the next entry.
 *   - Keydown handling runs in the CAPTURE phase so surfaces with their
 *     own Enter handlers (image detail modal) don't race the accept.
 */
(function () {
    'use strict';

    const DEFAULT_FALLBACK = [
        '1girl', '1boy', 'solo', 'long_hair', 'short_hair', 'blonde_hair',
        'black_hair', 'brown_hair', 'white_hair', 'silver_hair', 'pink_hair',
        'red_hair', 'blue_hair', 'green_hair', 'purple_hair', 'multicolored_hair',
        'looking_at_viewer', 'smile', 'open_mouth', 'closed_mouth', 'blush',
        'school_uniform', 'serafuku', 'shirt', 'skirt', 'dress',
        'breasts', 'large_breasts', 'medium_breasts', 'small_breasts',
        'sitting', 'standing', 'lying', 'kneeling',
        'indoors', 'outdoors', 'simple_background', 'white_background',
        'cowboy_shot', 'upper_body', 'full_body', 'portrait', 'close-up',
        'highres', 'absurdres',
    ];

    const SUGGEST_LIMIT = 12;
    const DEBOUNCE_MS = 120;
    const CJK_RE = /[぀-ヿ㐀-䶿一-鿿豈-﫿]/;

    const STATE = {
        lastSuggestions: [],
        active: -1,
        dropdown: null,
        abort: null,
        seq: 0,
        info: null,
        infoSeq: 0,
    };

    function t(key, fallback, params) {
        const translated = window.I18n?.t?.(key, params);
        if (translated && translated !== key) return translated;
        let text = fallback;
        for (const [name, value] of Object.entries(params || {})) {
            text = text.split(`{${name}}`).join(String(value));
        }
        return text;
    }

    function currentToken(el) {
        const value = el.value || '';
        const cursor = el.selectionStart ?? value.length;
        const left = value.slice(0, cursor);
        // Comma-separated tag inputs break tokens on comma/newline. Insert
        // mode (free-writing prompt boxes) also breaks on spaces and the
        // weight syntax "(tag:1.2)" so suggestions track just the word
        // under the caret.
        let startIdx = Math.max(left.lastIndexOf(','), left.lastIndexOf('\n'));
        if (el.dataset.capAcMode === 'insert') {
            startIdx = Math.max(
                startIdx,
                left.lastIndexOf(' '),
                left.lastIndexOf('('),
                left.lastIndexOf(')'),
                left.lastIndexOf(':'),
            );
        }
        const tokenStart = startIdx >= 0 ? startIdx + 1 : 0;
        const tokenRaw = left.slice(tokenStart);
        const tokenTrimmed = tokenRaw.trimStart();
        const tokenStartActual = tokenStart + (tokenRaw.length - tokenTrimmed.length);
        return {
            text: tokenTrimmed,
            start: tokenStartActual,
            end: cursor,
        };
    }

    function shouldSuggest(token) {
        if (!token) return false;
        if (CJK_RE.test(token)) return !token.includes(' ');
        if (token.length < 2) return false;
        // Only ASCII tag-like tokens otherwise.
        if (!/^[A-Za-z0-9_\-]+$/.test(token)) return false;
        return true;
    }

    function localFallbackMatches(token) {
        const q = token.toLowerCase();
        const prefix = [];
        const contains = [];
        for (const tag of DEFAULT_FALLBACK) {
            if (tag.startsWith(q)) prefix.push(tag);
            else if (tag.includes(q)) contains.push(tag);
        }
        return [...prefix, ...contains]
            .slice(0, 8)
            .map((tag) => ({ tag, count: 0, source: 'library', category: 'unknown', zh: null }));
    }

    async function fetchSuggestions(token) {
        const seq = ++STATE.seq;
        if (STATE.abort) STATE.abort.abort();
        const controller = new AbortController();
        STATE.abort = controller;
        try {
            const url = `/api/tags/suggest?q=${encodeURIComponent(token)}&limit=${SUGGEST_LIMIT}`;
            const r = await fetch(url, { signal: controller.signal });
            if (!r.ok) throw new Error(`suggest ${r.status}`);
            const data = await r.json();
            if (seq !== STATE.seq) return null; // stale response
            return Array.isArray(data.suggestions) ? data.suggestions : [];
        } catch (err) {
            if (err && err.name === 'AbortError') return null;
            if (seq !== STATE.seq) return null;
            return localFallbackMatches(token);
        }
    }

    function formatCount(n) {
        const num = Number(n) || 0;
        if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
        if (num >= 1_000) return `${Math.round(num / 1_000)}k`;
        return num > 0 ? String(num) : '';
    }

    function ensureDropdown() {
        if (STATE.dropdown) return STATE.dropdown;
        const div = document.createElement('div');
        div.className = 'caption-autocomplete-dropdown';
        div.setAttribute('role', 'listbox');
        div.hidden = true;
        document.body.appendChild(div);
        STATE.dropdown = div;
        return div;
    }

    function hideDropdown() {
        if (STATE.dropdown) STATE.dropdown.hidden = true;
        STATE.lastSuggestions = [];
        STATE.active = -1;
    }

    function renderDropdown(el, suggestions) {
        const dd = ensureDropdown();
        STATE.lastSuggestions = suggestions;
        STATE.active = suggestions.length > 0 ? 0 : -1;
        if (suggestions.length === 0) {
            dd.hidden = true;
            return;
        }
        dd.replaceChildren();
        suggestions.forEach((s, idx) => {
            const item = document.createElement('div');
            item.className = 'caption-autocomplete-item' + (idx === 0 ? ' active' : '');
            if (s.source === 'library') item.classList.add('is-library');
            item.dataset.tag = s.tag;

            const dot = document.createElement('span');
            dot.className = `cap-ac-dot cap-ac-dot-${s.category || 'unknown'}`;

            const name = document.createElement('span');
            name.className = 'caption-autocomplete-name';
            name.textContent = s.tag;

            const meta = document.createElement('span');
            meta.className = 'caption-autocomplete-meta';
            if (s.zh) {
                const zh = document.createElement('span');
                zh.className = 'caption-autocomplete-zh';
                zh.textContent = s.zh;
                meta.appendChild(zh);
            }
            if (s.copyright) {
                const copy = document.createElement('span');
                copy.className = 'caption-autocomplete-copy';
                copy.textContent = String(s.copyright).split(',')[0].trim();
                copy.title = String(s.copyright);
                meta.appendChild(copy);
            }
            const count = document.createElement('span');
            count.className = 'caption-autocomplete-count';
            count.textContent = formatCount(s.count);
            meta.appendChild(count);

            const details = document.createElement('button');
            details.type = 'button';
            details.className = 'cap-ac-info-btn';
            details.tabIndex = -1;
            details.textContent = 'i';
            const detailsLabel = t('tagInfo.open', 'Tag details (\u2192)', {});
            details.title = detailsLabel;
            details.setAttribute('aria-label', detailsLabel);
            // Both handlers: mousedown keeps the row's accept and the input's
            // blur from firing, so looking at a tag never commits it.
            details.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
            });
            details.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                showTagInfo(el, s.tag);
            });
            meta.appendChild(details);

            item.append(dot, name, meta);
            item.addEventListener('mousedown', (e) => {
                // mousedown so the click commits before the input blurs.
                e.preventDefault();
                accept(el, idx);
            });
            dd.appendChild(item);
        });
        dd.hidden = false;
        const rect = el.getBoundingClientRect();
        window.PopupPosition?.place(dd, {
            anchor: el,
            placement: 'bottom-start',
            gap: 4,
            width: Math.min(Math.max(rect.width, 240), 360),
            maxHeight: 280,
        });
    }

    function normalizeTag(raw) {
        return String(raw || '').trim().toLowerCase().replace(/\s+/g, '_');
    }

    function tokenizeField(value) {
        return String(value || '').split(/[,\n]/).map((part) => part.trim()).filter(Boolean);
    }

    function copyrightTokens(raw) {
        return String(raw || '').split(',').map((part) => part.trim()).filter(Boolean);
    }

    /** Tags written when the user accepts a suggestion.

     Comma-mode character hits also insert their series/copyright unless
     that token is already in the field. Insert-mode (Prompt Lab) only
     completes the word under the caret. */
    function tokensToInsert(suggestion, existingValue, insertMode) {
        const tag = String((suggestion && suggestion.tag) || '').trim();
        if (!tag) return [];
        if (insertMode) return [tag];
        const seen = new Set([normalizeTag(tag)]);
        for (const token of tokenizeField(existingValue)) {
            const key = normalizeTag(token);
            if (key) seen.add(key);
        }
        const extras = [];
        for (const extra of copyrightTokens(suggestion && suggestion.copyright)) {
            const key = normalizeTag(extra);
            if (!key || seen.has(key)) continue;
            seen.add(key);
            extras.push(key);
        }
        return extras.length ? [tag].concat(extras) : [tag];
    }

    function accept(el, suggestionIdx) {
        const s = STATE.lastSuggestions[suggestionIdx];
        if (!s) return;
        const tok = currentToken(el);
        const value = el.value || '';
        const before = value.slice(0, tok.start);
        const after = value.slice(tok.end);
        const insertMode = el.dataset.capAcMode === 'insert';
        const inserted = tokensToInsert(s, value, insertMode).join(', ');
        // Comma mode appends ", " for the next entry; insert mode (prompt
        // writing boxes) completes the word in place and stays out of the
        // author's flow.
        const sep = insertMode || after.startsWith(',') || after.startsWith('\n')
            ? ''
            : ', ';
        el.value = `${before}${inserted}${sep}${after}`;
        const newCursor = (before + inserted + sep).length;
        el.setSelectionRange(newCursor, newCursor);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        hideDropdown();
    }

    function highlightActive() {
        const dd = STATE.dropdown;
        if (!dd) return;
        for (const [i, node] of Array.from(dd.children).entries()) {
            node.classList.toggle('active', i === STATE.active);
        }
    }

    // ---- Tag knowledge popover -----------------------------------------
    // GET /api/tags/info has answered with everything the app knows about one
    // tag since v3.5.0, and the only door to it was the Separation Console's
    // per-row menu — nowhere near where tags are actually typed.
    //
    // Two rules bound what it may say. Its numbers are vocabulary and library
    // facts: a danbooru post count is how often booru users tagged something,
    // which is not a statement about any model's training set, and the note is
    // there so nobody reads it as one. And when the open project targets a
    // model documented to want natural-language captions, booru tag lore is not
    // advice for that project's captions, so the popover says so rather than
    // presenting the same numbers as guidance.

    function infoOpen() {
        return !!(STATE.info && !STATE.info.hidden);
    }

    function hideTagInfo() {
        if (!STATE.info) return;
        STATE.info.hidden = true;
        STATE.info.replaceChildren();
        document.removeEventListener('mousedown', onDocumentMouseDown, true);
    }

    function onDocumentMouseDown(event) {
        if (!infoOpen()) return;
        const target = event.target;
        if (STATE.info.contains(target)) return;
        if (target instanceof Element && target.closest('.cap-ac-info-btn')) return;
        hideTagInfo();
    }

    function ensureInfoPanel() {
        if (STATE.info) return STATE.info;
        const panel = document.createElement('div');
        panel.className = 'cap-ac-info';
        panel.setAttribute('role', 'dialog');
        panel.hidden = true;
        document.body.appendChild(panel);
        STATE.info = panel;
        return panel;
    }

    /** Sit beside the suggestion list while it is open, so the details never
     *  cover the list they were opened from; fall back to the input itself when
     *  the popover was opened without a dropdown. */
    function placeInfoPanel(surface) {
        const openDropdown = STATE.dropdown && !STATE.dropdown.hidden
            ? STATE.dropdown
            : null;
        window.PopupPosition?.place(STATE.info, {
            anchor: openDropdown || surface,
            placement: 'right-start',
            gap: 8,
            width: 320,
            maxHeight: 420,
        });
    }

    /** Whether this surface's text is governed by the open project's target
     *  model. Only the Dataset Maker caption editor is: the library tag editors,
     *  blacklists and prompt-writing boxes are not that project's captions, and
     *  borrowing its setting there would be a claim about text the setting does
     *  not reach. */
    function isProjectCaptionSurface(el) {
        return !!el && el.dataset?.capAcProject === '1';
    }

    function infoRow(label, value) {
        const row = document.createElement('div');
        row.className = 'cap-ac-info-row';
        const term = document.createElement('span');
        term.className = 'cap-ac-info-label';
        term.textContent = label;
        const detail = document.createElement('span');
        detail.className = 'cap-ac-info-value';
        detail.textContent = value;
        row.append(term, detail);
        return row;
    }

    function renderTagInfo(panel, anchor, query, info) {
        panel.replaceChildren();
        panel.setAttribute(
            'aria-label',
            t('tagInfo.dialogLabel', 'Tag details', {})
        );

        const head = document.createElement('div');
        head.className = 'cap-ac-info-head';
        const dot = document.createElement('span');
        dot.className = `cap-ac-dot cap-ac-dot-${info.category || 'unknown'}`;
        const name = document.createElement('span');
        name.className = 'cap-ac-info-name';
        name.textContent = info.canonical || query;
        head.append(dot, name);
        if (info.zh) {
            const zh = document.createElement('span');
            zh.className = 'cap-ac-info-zh';
            zh.textContent = info.zh;
            head.appendChild(zh);
        }
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'cap-ac-info-close';
        close.textContent = '\u2715';
        const closeLabel = t('tagInfo.close', 'Close', {});
        close.title = closeLabel;
        close.setAttribute('aria-label', closeLabel);
        close.addEventListener('click', hideTagInfo);
        head.appendChild(close);
        panel.appendChild(head);

        const body = document.createElement('div');
        body.className = 'cap-ac-info-body';

        const canonical = info.canonical || query;
        if (canonical && canonical !== query) {
            const alias = document.createElement('p');
            alias.className = 'cap-ac-info-lead';
            alias.textContent = t(
                'tagInfo.aliasOf',
                '"{typed}" is an alias of {canonical}.',
                { typed: query, canonical }
            );
            body.appendChild(alias);
        }

        if (info.found_in_vocab) {
            if (info.category) {
                body.appendChild(infoRow(
                    t('tagInfo.category', 'Category', {}),
                    info.category
                ));
            }
            const exact = Number(info.danbooru_count) || 0;
            const posts = infoRow(
                t('tagInfo.danbooruPosts', 'Danbooru posts', {}),
                formatCount(exact) || '0'
            );
            posts.querySelector('.cap-ac-info-value').title = exact.toLocaleString();
            body.appendChild(posts);
            if (info.copyright) {
                body.appendChild(infoRow(
                    t('tagInfo.copyright', 'Series / copyright', {}),
                    String(info.copyright)
                ));
            }
            if (info.parent_tag) {
                body.appendChild(infoRow(
                    t('tagInfo.parent', 'Parent tag', {}),
                    String(info.parent_tag)
                ));
            }
        } else {
            const miss = document.createElement('p');
            miss.className = 'cap-ac-info-lead';
            miss.textContent = t(
                'tagInfo.notInVocab',
                'Not in the bundled Danbooru vocabulary — the app has no popularity or alias data for it.',
                {}
            );
            body.appendChild(miss);
        }

        body.appendChild(infoRow(
            t('tagInfo.libraryCount', 'In your library', {}),
            t('tagInfo.libraryImages', '{count} images', {
                count: Number(info.library_count) || 0,
            })
        ));

        const lists = [
            ['tagInfo.aliases', 'Also written', info.aliases],
            ['tagInfo.implies', 'Implies', info.implies],
            ['tagInfo.impliedBy', 'Implied by', info.implied_by],
        ];
        for (const [key, fallback, values] of lists) {
            const items = Array.isArray(values) ? values.filter(Boolean) : [];
            if (items.length === 0) continue;
            body.appendChild(infoRow(t(key, fallback, {}), items.slice(0, 8).join(', ')));
        }
        panel.appendChild(body);

        const scope = document.createElement('p');
        scope.className = 'cap-ac-info-note';
        scope.textContent = t(
            'tagInfo.scopeNote',
            'Vocabulary and library facts. Danbooru counts describe how often booru users tagged something; they do not say what any model was trained on.',
            {}
        );
        panel.appendChild(scope);

        if (isProjectCaptionSurface(anchor) && window.TargetModel?.captionDialect?.() === 'natural') {
            const dialect = document.createElement('p');
            dialect.className = 'cap-ac-info-note cap-ac-info-dialect';
            dialect.textContent = t(
                'tagInfo.dialectNote',
                'This project targets a natural-language model, so Booru tag conventions do not apply to its captions. Tags stay useful for library search and review.',
                {}
            );
            panel.appendChild(dialect);
        }

        placeInfoPanel(anchor);
    }

    async function showTagInfo(anchor, tag) {
        const query = String(tag || '').trim();
        if (!query) return;
        const panel = ensureInfoPanel();
        const seq = ++STATE.infoSeq;
        panel.replaceChildren();
        const loading = document.createElement('p');
        loading.className = 'cap-ac-info-lead';
        loading.textContent = t('tagInfo.loading', 'Loading tag details\u2026', {});
        panel.appendChild(loading);
        panel.hidden = false;
        document.addEventListener('mousedown', onDocumentMouseDown, true);
        placeInfoPanel(anchor);

        try {
            const response = await fetch(`/api/tags/info?tag=${encodeURIComponent(query)}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const info = await response.json();
            if (seq !== STATE.infoSeq) return;
            renderTagInfo(panel, anchor, query, info);
        } catch (err) {
            if (seq !== STATE.infoSeq) return;
            panel.replaceChildren();
            const failed = document.createElement('p');
            failed.className = 'cap-ac-info-lead';
            failed.textContent = t(
                'tagInfo.failed',
                'Could not load details for "{tag}": {error}',
                { tag: query, error: String(err?.message || err) }
            );
            panel.appendChild(failed);
            placeInfoPanel(anchor);
        }
    }

    function attach(el, opts) {
        if (!el || el.dataset.captionAutocomplete === '1') return;
        el.dataset.captionAutocomplete = '1';
        if (opts && opts.mode === 'insert') el.dataset.capAcMode = 'insert';
        if (opts && opts.project) el.dataset.capAcProject = '1';

        let inputDebounce = null;
        el.addEventListener('input', () => {
            if (inputDebounce) clearTimeout(inputDebounce);
            // The open popover describes the token that was there a keystroke
            // ago; keeping it would leave stale facts next to new text.
            hideTagInfo();
            inputDebounce = setTimeout(async () => {
                const tok = currentToken(el);
                if (!shouldSuggest(tok.text)) {
                    hideDropdown();
                    return;
                }
                // NL guard: if the token already contains spaces, treat as prose.
                if (tok.text.includes(' ') && tok.text.length > 6) {
                    hideDropdown();
                    return;
                }
                const matches = await fetchSuggestions(tok.text);
                if (matches === null) return; // superseded by a newer keystroke
                // Re-check the token: it may have changed while fetching.
                const now = currentToken(el);
                if (now.text !== tok.text || document.activeElement !== el) {
                    if (document.activeElement !== el) hideDropdown();
                    return;
                }
                renderDropdown(el, matches);
            }, DEBOUNCE_MS);
        });

        // Capture phase: surfaces like the image-detail modal bind their own
        // Enter handler on the same element; accepting a suggestion must win
        // and stop that handler from also firing.
        el.addEventListener('keydown', (e) => {
            // Layered dismissal: Escape takes the details popover first, so a
            // glance at one tag does not also cost the suggestion list.
            if (e.key === 'Escape' && infoOpen()) {
                e.preventDefault();
                e.stopImmediatePropagation();
                hideTagInfo();
                return;
            }
            if (!STATE.dropdown || STATE.dropdown.hidden) return;
            if (e.key === 'ArrowRight') {
                // Only when the caret has nowhere further right to go, so the
                // key keeps its normal meaning while editing mid-line.
                const atEnd = (el.selectionStart ?? 0) === (el.value || '').length
                    && (el.selectionEnd ?? 0) === (el.value || '').length;
                const highlighted = STATE.lastSuggestions[STATE.active];
                if (atEnd && highlighted) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    showTagInfo(el, highlighted.tag);
                }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                STATE.active = (STATE.active + 1) % STATE.lastSuggestions.length;
                highlightActive();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                STATE.active = (STATE.active - 1 + STATE.lastSuggestions.length) % STATE.lastSuggestions.length;
                highlightActive();
            } else if (e.key === 'Tab' || e.key === 'Enter') {
                if (STATE.active >= 0) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    accept(el, STATE.active);
                }
            } else if (e.key === 'Escape') {
                e.stopImmediatePropagation();
                hideDropdown();
            }
        }, true);

        el.addEventListener('blur', () => {
            // Defer in case the blur was triggered by clicking a suggestion.
            setTimeout(hideDropdown, 200);
        });
    }

    function bind() {
        // The Dataset Maker caption editor writes the open project's captions,
        // so the project's target model governs this text and nothing else's.
        const projectEditor = document.getElementById('dataset-editor-textarea');
        if (projectEditor) attach(projectEditor, { project: true });
        const surfaces = [
            'modal-tags-add-input',      // image detail tag editor
            'mass-tag-add-tags',         // mass tag editor: add
            'mass-tag-remove-tags',      // mass tag editor: remove
            'dataset-blacklist',         // Dataset Maker export blacklist
            'tag-pre-blacklist',         // AI tagging pre-blacklist
            'batch-export-blacklist',    // batch export blacklist
        ];
        for (const id of surfaces) {
            const el = document.getElementById(id);
            if (el) attach(el);
        }
        // Prompt Lab writing boxes: complete the word under the caret
        // without inserting comma separators (owner-approved insert mode).
        for (const id of ['pl-build-prompt', 'pl-build-negative']) {
            const el = document.getElementById(id);
            if (el) attach(el, { mode: 'insert' });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind, { once: true });
    } else {
        bind();
    }

    function isOpen() {
        return !!(STATE.dropdown && !STATE.dropdown.hidden && STATE.active >= 0);
    }

    // refreshVocab kept as a no-op for API compatibility (the suggest
    // endpoint queries live data; there is no client-side vocab cache).
    // isOpen lets surfaces with their own Enter handlers (image detail
    // modal) yield to the suggestion accept regardless of listener order.
    window.CaptionAutocomplete = {
        attach,
        isOpen,
        showTagInfo,
        hideTagInfo,
        tokensToInsert,
        refreshVocab: async () => {},
    };
})();
