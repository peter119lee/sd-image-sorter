/**
 * Dataset Maker — right-pane tag-add autocomplete (wraps DM.addImageIds to invalidate the vocab cache).
 * Moved VERBATIM from dataset-maker-part3.js L1015-1158.
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    // ---------- Tag autocomplete in right pane ----------
    (function initTagAutocomplete() {
        const input = document.getElementById('dataset-tag-add-input');
        const dropdown = document.getElementById('dataset-tag-add-dropdown');
        if (!input || !dropdown) return;

        let cachedVocab = null;
        let fetchTimer = null;
        let activeIdx = -1;

        async function getVocab() {
            if (cachedVocab) return cachedVocab;
            try {
                const ids = [];
                const localCaptions = {};
                for (const id of (DM.imageIds || [])) {
                    if (DM.isLocalId?.(id)) {
                        const p = DM.localItemPaths?.get?.(id);
                        const cap = DM.captionEdits?.get?.(id);
                        if (p && cap) localCaptions[p] = cap;
                    } else if (Number(id) > 0) {
                        ids.push(Number(id));
                    }
                }
                const r = await fetch('/api/dataset/vocab', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        image_ids: ids,
                        path_caption_overrides: localCaptions,
                        top_n: 2000,
                    }),
                });
                if (r.ok) {
                    const data = await r.json();
                    cachedVocab = (data.vocab || []).map(t => t.tag || t).filter(Boolean);
                }
            } catch { /* ignore */ }
            if (!cachedVocab || cachedVocab.length === 0) {
                cachedVocab = ['1girl', '1boy', 'solo', 'long_hair', 'short_hair',
                    'looking_at_viewer', 'smile', 'simple_background', 'highres'];
            }
            return cachedVocab;
        }

        function showSuggestions(matches) {
            dropdown.innerHTML = '';
            activeIdx = -1;
            if (matches.length === 0) { dropdown.hidden = true; return; }
            for (const item of matches.slice(0, 8)) {
                const tag = typeof item === 'string' ? item : item.tag;
                if (!tag) continue;
                const copyright = typeof item === 'string' ? '' : (item.copyright || '');
                const div = document.createElement('div');
                div.className = 'tag-suggestion';
                div.dataset.tag = tag;
                if (copyright) div.dataset.copyright = copyright;
                div.textContent = copyright ? `${tag} · ${String(copyright).split(',')[0].trim()}` : tag;
                div.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    acceptTag(tag, copyright);
                });
                dropdown.appendChild(div);
            }
            dropdown.hidden = false;
        }

        function acceptTag(tag, copyright) {
            const ta = document.getElementById('dataset-editor-textarea');
            if (!ta || DM.activeId == null) return;
            const current = ta.value.trim();
            const tags = current ? current.split(',').map(s => s.trim()).filter(Boolean) : [];
            const seen = new Set(tags.map((part) => part.toLowerCase().replace(/\s+/g, '_')));
            const add = (value) => {
                const key = String(value || '').trim().toLowerCase().replace(/\s+/g, '_');
                if (!key || seen.has(key)) return;
                seen.add(key);
                tags.push(key);
            };
            add(tag);
            String(copyright || '').split(',').forEach((part) => add(part));
            ta.value = tags.join(', ');
            DM.captionEdits.set(DM.activeId, ta.value);
            DM._refreshQueueItem(DM.activeId);
            DM._renderTagPills();
            DM._saveSession?.();
            input.value = '';
            dropdown.hidden = true;
        }

        input.addEventListener('input', () => {
            const q = input.value.trim().toLowerCase();
            if (q.length < 1) { dropdown.hidden = true; return; }
            if (fetchTimer) clearTimeout(fetchTimer);
            fetchTimer = setTimeout(async () => {
                // v3.5.0: library + danbooru vocabulary first; the dataset
                // vocab keeps covering tags that only exist in local (not yet
                // imported) captions, which /api/tags/suggest can't see.
                let matches = [];
                try {
                    const r = await (window.apiFetch || fetch)(`/api/tags/suggest?q=${encodeURIComponent(q)}&limit=8`);
                    if (r.ok) {
                        const data = await r.json();
                        matches = (data.suggestions || []).filter(s => s && s.tag);
                    }
                } catch { /* fall through to dataset vocab */ }
                if (matches.length < 8) {
                    const vocab = await getVocab();
                    for (const t of vocab) {
                        if (matches.length >= 8) break;
                        if (t.toLowerCase().includes(q) && !matches.some((item) => (item.tag || item) === t)) {
                            matches.push({ tag: t });
                        }
                    }
                }
                showSuggestions(matches);
            }, 150);
        });

        input.addEventListener('keydown', (e) => {
            const items = dropdown.querySelectorAll('.tag-suggestion');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                activeIdx = Math.min(activeIdx + 1, items.length - 1);
                items.forEach((el, i) => el.classList.toggle('active', i === activeIdx));
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                activeIdx = Math.max(activeIdx - 1, 0);
                items.forEach((el, i) => el.classList.toggle('active', i === activeIdx));
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (activeIdx >= 0 && items[activeIdx]) {
                    acceptTag(items[activeIdx].dataset.tag, items[activeIdx].dataset.copyright);
                } else if (input.value.trim()) {
                    acceptTag(input.value.trim());
                }
            } else if (e.key === 'Escape') {
                dropdown.hidden = true;
            }
        });

        input.addEventListener('blur', () => {
            setTimeout(() => { dropdown.hidden = true; }, 150);
        });

        // Invalidate the vocab cache when the image set changes. The
        // previous version patched a non-existent ``DM._addImages`` (the
        // real public method is ``addImageIds`` on dataset-maker.js), so
        // the guard was always false and the cache never cleared,
        // leaving stale tag suggestions after adding/removing images.
        const origAddImageIds = DM.addImageIds;
        if (typeof origAddImageIds === 'function') {
            DM.addImageIds = function (...args) {
                cachedVocab = null;
                return origAddImageIds.apply(this, args);
            };
        }
    })();
})();
