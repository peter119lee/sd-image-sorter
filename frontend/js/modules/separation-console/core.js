/**
 * separation-console/core.js — separation-console.js decomposition (family
 * base; MUST LOAD FIRST). Moved BYTE-IDENTICAL from
 * frontend/js/modules/separation-console.js pre-cut lines 1-23 + 25-83 +
 * 130-188 + 1138-1148 (of 1,155): the SEP-1 docblock, the IIFE's
 * 'use strict' (kept as the file-level directive here and ADDED to every
 * other family file — the original IIFE was strict throughout), the module
 * consts, the i18n + tag-fold helpers, and `const SeparationConsole = {`
 * with the state fields, `get dm()`, init(), _ensureHooks() (the OUTWARD
 * DatasetMaker monkeypatch — _activeChangedHooks.push + evidence Map writes
 * wrap, guarded by _hooked/__sepconWrapped; the singleton object is never
 * re-created or re-inited by the split), the Dataset Maker state adapter,
 * _scheduleRefresh() and the object-literal `};` closer. Declares the ONE
 * unsealed script-global object every other separation-console/*.js file
 * Object.assign()s onto — this file must load before the rest of the
 * family; separation-console/boot.js publishes window.SeparationConsole
 * and runs the DOMContentLoaded tail LAST, mirroring the original file
 * tail. Family renames (IIFE-private names promoted to script-globals;
 * tree-wide collision census clean; applied at every use site, comments
 * untouched): t -> sepconT, fold -> sepconFold, SEEN_KEY ->
 * SEPCON_SEEN_KEY, PURPOSE_KEY -> SEPCON_PURPOSE_KEY, INTRINSIC_MIN_RATIO
 * -> SEPCON_INTRINSIC_MIN_RATIO, INTRINSIC_RE -> SEPCON_INTRINSIC_RE.
 * Everything else is byte-identical.
 */
/**
 * Separation Console (SEP-1) — the per-TAG decision surface for LoRA
 * dataset prep.
 *
 * Captioning for LoRA training is variable separation, not description:
 * a tag kept in the caption stays promptable; a tag pruned (blacklisted)
 * is absorbed into the trigger word. That keep/prune decision is made per
 * TAG across the whole set, with frequency as the evidence — so this panel
 * lists every tag in the Dataset Maker queue with its frequency, category
 * color and an "intrinsic trait candidate" marker, and lets the user act
 * on the tag everywhere at once:
 *   ✂  prune  -> toggles the tag in #dataset-blacklist (export-time absorb)
 *   🗑  remove -> deletes the tag from every session caption (captionEdits)
 *   📍 locate -> cycles the editor through images carrying the tag
 *
 * It also hosts the pre-training health check (BE-5' consistency report)
 * and a client-side NL-leak scan (SEP-2): blacklisted traits that still
 * appear in natural-language captions are flagged before export.
 *
 * Reads Dataset Maker state through a thin adapter (captions/captionEdits
 * Maps) so it works today and can later re-point at the shared caption
 * editor store without UI changes.
 */
    'use strict';

    const SEPCON_SEEN_KEY = 'sd-image-sorter-dataset-seen';
    const SEPCON_PURPOSE_KEY = 'sd-image-sorter-separation-purpose';
    const SEPCON_INTRINSIC_MIN_RATIO = 0.5;

    // Trait families that are usually intrinsic to a character (mirrors the
    // backend trait-pruning families; heuristic, marking only — never acts).
    const SEPCON_INTRINSIC_RE = /(_|\s|^)(hair|eyes?|skin)(\s|$)|ahoge|twintails?|twin_braids|ponytail|braid|bangs|sidelocks|heterochromia|horns?|tail|wings?|halo|animal_ears|cat_ears|fox_ears|fang|mole|scar|freckles|dark-skinned|tan(line)?s?$/;

    function sepconT(en, zh) {
        try {
            const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
            return String(lang).toLowerCase().startsWith('zh') ? zh : en;
        } catch (_) { return en; }
    }

    function sepconFold(tag) {
        return String(tag || '').trim().toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ');
    }

    const SeparationConsole = {
        _hooked: false,
        _seen: null,
        _refreshTimer: null,
        _lastHealthReport: null,

        get dm() { return window.DatasetMaker || null; },

        // ---- lifecycle -------------------------------------------------
        init() {
            const details = document.getElementById('dataset-separation-console');
            if (!details) return;
            details.addEventListener('toggle', () => {
                if (details.open) { this._ensureHooks(); this.refresh(); }
            });
            window.addEventListener('dataset:changed', () => {
                this._ensureHooks();
                this._scheduleRefresh();
            });
            document.getElementById('sepcon-search')?.addEventListener('input', () => this._scheduleRefresh(120));
            document.getElementById('sepcon-sort')?.addEventListener('change', () => this.refresh());
            document.getElementById('sepcon-next-unseen')?.addEventListener('click', () => this.jumpToUnseen());
            document.getElementById('sepcon-health-run')?.addEventListener('click', () => this.runHealthCheck());
            document.getElementById('sepcon-tipo-suggest')?.addEventListener('click', () => this.suggestUpsample());
            const tipoModel = document.getElementById('sepcon-tipo-model');
            if (tipoModel) {
                try {
                    const stored = localStorage.getItem('sd-tipo-model-v1');
                    if (stored === 'v2.1' || stored === '200m-ft') tipoModel.value = stored;
                } catch (_error) { /* keep the HTML default */ }
                tipoModel.addEventListener('change', () => {
                    try { localStorage.setItem('sd-tipo-model-v1', tipoModel.value); } catch (_error) {}
                    const other = document.getElementById('reverse-tipo-model');
                    if (other) other.value = tipoModel.value;
                });
            }
            const purpose = document.getElementById('sepcon-purpose');
            if (purpose) {
                try { purpose.value = localStorage.getItem(SEPCON_PURPOSE_KEY) || 'character'; } catch (_) {}
                purpose.addEventListener('change', () => {
                    try { localStorage.setItem(SEPCON_PURPOSE_KEY, purpose.value); } catch (_) {}
                });
            }
            const blacklist = document.getElementById('dataset-blacklist');
            blacklist?.addEventListener('input', () => this._scheduleRefresh(300));
            // QW-1: live token budget under the booru caption box.
            document.getElementById('dataset-editor-textarea')?.addEventListener(
                'input', () => this._updateTokenCounter());
            this._initRethreshold(details);
            this._initReviewIssues(details);
        },
        _ensureHooks() {
            const dm = this.dm;
            if (this._hooked || !dm || !Array.isArray(dm._activeChangedHooks)) return;
            this._hooked = true;
            // QW-2: seen tracking — registered on the shared active-changed
            // registry (FE-1 2b) instead of re-wrapping dm._setActive. Pushed
            // at first console open, so it runs last (as the old outermost
            // runtime wrap did).
            dm._activeChangedHooks.push((id) => {
                this._markSeen(id);
                this._updateTokenCounter();
            });
            // Keep tag rows and review evidence aligned with every caption channel.
            const wrapEvidenceMap = (evidenceMap) => {
                if (!evidenceMap || evidenceMap.__sepconWrapped) return;
                evidenceMap.__sepconWrapped = true;
                const originalSet = evidenceMap.set.bind(evidenceMap);
                const originalDelete = evidenceMap.delete.bind(evidenceMap);
                const originalClear = evidenceMap.clear.bind(evidenceMap);
                evidenceMap.set = (key, value) => {
                    const out = originalSet(key, value);
                    this._markReviewDirty?.();
                    this._scheduleRefresh(400);
                    return out;
                };
                evidenceMap.delete = (key) => {
                    const changed = originalDelete(key);
                    if (changed) {
                        this._markReviewDirty?.();
                        this._scheduleRefresh(400);
                    }
                    return changed;
                };
                evidenceMap.clear = () => {
                    const changed = evidenceMap.size > 0;
                    const out = originalClear();
                    if (changed) {
                        this._markReviewDirty?.();
                        this._scheduleRefresh(400);
                    }
                    return out;
                };
            };
            for (const evidenceMap of [
                dm.captions,
                dm.nlCaptions,
                dm.captionEdits,
                dm.nlEdits,
                dm.captionType,
            ]) {
                wrapEvidenceMap(evidenceMap);
            }
        },

        // ---- adapter over Dataset Maker state ---------------------------
        _queueIds() {
            return (this.dm?.imageIds || []).map(Number).filter(Number.isFinite);
        },

        _effectiveCaption(id) {
            const dm = this.dm;
            if (!dm) return '';
            if (dm.captionEdits?.has?.(id)) return String(dm.captionEdits.get(id) || '');
            return String(dm.captions?.get?.(id) || '');
        },

        _effectiveNl(id) {
            const dm = this.dm;
            if (!dm) return '';
            if (dm.nlEdits?.has?.(id)) return String(dm.nlEdits.get(id) || '');
            return String(dm.nlCaptions?.get?.(id) || '');
        },

        _blacklistEntries() {
            const box = document.getElementById('dataset-blacklist');
            if (!box) return [];
            return String(box.value || '')
                .split(/\r?\n|,/)
                .map(s => s.trim())
                .filter(Boolean);
        },

        _writeBlacklist(entries) {
            const box = document.getElementById('dataset-blacklist');
            if (!box) return;
            box.value = entries.join('\n');
            box.dispatchEvent(new Event('input', { bubbles: true }));
        },

        _scheduleRefresh(delay = 250) {
            const details = document.getElementById('dataset-separation-console');
            if (!details?.open) return;
            if (this._refreshTimer) clearTimeout(this._refreshTimer);
            this._refreshTimer = setTimeout(() => {
                this._refreshTimer = null;
                this.refresh();
            }, delay);
        },
    };

