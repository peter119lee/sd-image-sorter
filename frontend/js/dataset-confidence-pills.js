/**
 * Dataset Maker — tag confidence pills (v3.2.2 T-power-PR2 / C).
 *
 * Renders one pill per tag for the currently-active image in the
 * caption editor. Each pill carries the local tagger's confidence
 * score so the user can spot low-confidence false positives at a
 * glance and one-click delete them from the caption.
 *
 * Data source:
 *   /api/images/{id}  ->  { tags: [{tag, confidence, category}, ...] }
 *
 * The confidence panel is wired AFTER the existing setActive +
 * step navigation so we don't fight with the current editor logic.
 * If a tag has no confidence (e.g. user hand-edited captions), it
 * is shown without a numeric badge but still gets the ✕ button.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    /** Confidence cache, keyed by image_id (positive only — local-source
     *  items have no DB confidence; their pills come from the caption text). */
    DM._confidenceCache = DM._confidenceCache || new Map();

    function $(id) { return document.getElementById(id); }

    function bucket(conf) {
        const c = Number(conf);
        if (!Number.isFinite(c) || c <= 0) return 'unknown';
        if (c >= 0.80) return 'high';
        if (c >= 0.50) return 'mid';
        return 'low';
    }

    function renderPills(tagsWithConf) {
        const list = $('dataset-confidence-pills');
        const hint = $('dataset-confidence-summary-hint');
        const panel = $('dataset-confidence-panel');
        if (!list || !panel) return;
        list.innerHTML = '';

        if (!tagsWithConf || tagsWithConf.length === 0) {
            panel.hidden = true;
            return;
        }
        panel.hidden = false;

        let high = 0, mid = 0, low = 0;
        for (const t of tagsWithConf) {
            const node = document.createElement('span');
            node.className = `dataset-confidence-pill conf-${bucket(t.confidence)}`;
            node.dataset.tag = String(t.tag || '');
            const name = document.createElement('span');
            name.textContent = String(t.tag || '');
            node.appendChild(name);
            if (Number.isFinite(t.confidence) && t.confidence > 0) {
                const conf = document.createElement('span');
                conf.className = 'dataset-confidence-pill-conf';
                conf.textContent = Number(t.confidence).toFixed(2);
                node.appendChild(conf);
                const b = bucket(t.confidence);
                if (b === 'high') high += 1;
                else if (b === 'mid') mid += 1;
                else if (b === 'low') low += 1;
            }
            const x = document.createElement('button');
            x.type = 'button';
            x.className = 'dataset-confidence-pill-x';
            // i18n: title + aria-label go through the shared helper so a
            // non-English UI isn't shown English tooltip/announce text.
            const dropTitle = (DM._t?.('dataset.confidenceDropTag', 'Drop this tag from the caption'))
                || 'Drop this tag from the caption';
            x.title = dropTitle;
            x.setAttribute('aria-label', `${dropTitle}: ${t.tag}`);
            x.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-close\"/></svg>";
            x.addEventListener('click', () => dropTagFromCaption(String(t.tag || '')));
            node.appendChild(x);
            list.appendChild(node);
        }

        if (hint) {
            hint.textContent = DM._t?.('dataset.confidenceSummary',
                '{high}/{mid}/{low} high/mid/low ({total} total)',
                { high, mid, low, total: tagsWithConf.length })
                || `${high}/${mid}/${low} high/mid/low (${tagsWithConf.length} total)`;
        }
    }

    function dropTagFromCaption(tag) {
        const ta = document.getElementById('dataset-editor-textarea');
        if (!ta || !tag) return;
        const text = String(ta.value || '');
        // Token-level removal: split on commas, drop the matching one
        // (case-insensitive, underscore-tolerant), rejoin.
        const target = tag.trim().toLowerCase().replace(/_/g, ' ');
        const tokens = text.split(',').map((s) => s.trim()).filter(Boolean);
        const kept = tokens.filter((tok) => tok.toLowerCase().replace(/_/g, ' ') !== target);
        ta.value = kept.join(', ');
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        // Also drop the pill from the panel.
        const list = document.getElementById('dataset-confidence-pills');
        if (list) {
            for (const pill of Array.from(list.querySelectorAll('.dataset-confidence-pill'))) {
                if (pill.dataset.tag === tag) pill.remove();
            }
        }
    }

    async function fetchConfidence(imageId) {
        if (!imageId || imageId <= 0) return [];
        if (DM._confidenceCache.has(imageId)) return DM._confidenceCache.get(imageId);
        try {
            const r = await (window.apiFetch || fetch)(`/api/images/${imageId}`);
            if (!r.ok) return [];
            const data = await r.json();
            const tags = data.tags || data.image?.tags || [];
            DM._confidenceCache.set(imageId, tags);
            return tags;
        } catch { return []; }
    }

    /** Public hook: after _setActive runs, refresh the panel. */
    DM._refreshConfidencePanel = async function (imageId) {
        if (!imageId) {
            renderPills([]);
            return;
        }
        if (this.isLocalId && this.isLocalId(imageId)) {
            // Local-source items have no DB row. Don't try to fetch
            // confidence — just hide the panel.
            renderPills([]);
            return;
        }
        const tags = await fetchConfidence(Number(imageId));
        renderPills(tags);
    };

    // Hook into _setActive: every time the active image changes, refresh
    // the panel. Registered on the shared active-changed registry (FE-1 2b)
    // instead of re-wrapping DM._setActive.
    if (Array.isArray(DM._activeChangedHooks)) {
        DM._activeChangedHooks.push(function () {
            try { this._refreshConfidencePanel(this.activeId); } catch (_e) { /* */ }
        });
    }

    // Hook into _renderEmptyEditor so the panel disappears when no image
    // is active.
    const original_renderEmptyEditor = DM._renderEmptyEditor;
    if (typeof original_renderEmptyEditor === 'function') {
        DM._renderEmptyEditor = function () {
            const ret = original_renderEmptyEditor.call(this);
            renderPills([]);
            return ret;
        };
    }

    // Invalidate the confidence cache when the dataset changes. Previously
    // the cache was fill-only — removing an image, clearing the dataset, or
    // retagging an image left stale entries that could resurface on a later
    // add of the same id. The global ``dataset:changed`` event is emitted
    // by clear/remove/retag flows; we also expose a manual invalidator.
    DM._invalidateConfidenceCache = function (imageId) {
        if (imageId == null) {
            DM._confidenceCache?.clear?.();
        } else if (DM._confidenceCache) {
            DM._confidenceCache.delete(Number(imageId));
        }
    };
    window.addEventListener('dataset:changed', () => {
        DM._invalidateConfidenceCache();
    });

    // Public API for tests / future PRs.
    DM._renderConfidencePills = renderPills;
})();
