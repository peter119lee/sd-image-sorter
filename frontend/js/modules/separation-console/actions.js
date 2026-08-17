/**
 * separation-console/actions.js — separation-console.js decomposition
 * (verbatim Object.assign mixin). Method bodies moved BYTE-IDENTICAL from
 * frontend/js/modules/separation-console.js pre-cut lines 333-379 +
 * 965-1036 (of 1,155): togglePrune, removeEverywhere, locateNext (AS-IS
 * quirk kept: a non-carrier active id makes indexOf return -1 so the
 * cycle starts at ids[0]), the QW-2 seen tracking
 * (_loadSeen/_markSeen/jumpToUnseen) and the SEP-2 client-side NL-leak
 * scan _renderLeaks (AS-IS: builds a per-trait RegExp per image).
 * Strict per-file (the original IIFE was strict). Joins the ONE unsealed
 * object declared in separation-console/core.js, which loads FIRST;
 * separation-console/boot.js publishes window.SeparationConsole LAST.
 * Family renames applied (sepconT/sepconFold/SEPCON_* — see core.js).
 */
'use strict';
Object.assign(SeparationConsole, {
        // ---- actions ------------------------------------------------------
        togglePrune(key) {
            const entries = this._blacklistEntries();
            const next = entries.filter(entry => sepconFold(entry) !== key);
            if (next.length === entries.length) next.push(key.replace(/ /g, '_'));
            this._writeBlacklist(next);
            this.refresh();
        },

        removeEverywhere(row) {
            const dm = this.dm;
            if (!dm) return;
            for (const id of row.ids) {
                const current = this._effectiveCaption(id);
                const next = current
                    .split(',')
                    .map(s => s.trim())
                    .filter(s => s && sepconFold(s) !== row.key)
                    .join(', ');
                if (next === current) continue;
                if (Number(dm.activeId) === Number(id)) {
                    const box = document.getElementById('dataset-editor-textarea');
                    if (box) {
                        box.value = next;
                        // Route through DM's own edit pipeline (undo stack, diff).
                        box.dispatchEvent(new Event('input', { bubbles: true }));
                        continue;
                    }
                }
                dm.captionEdits.set(Number(id), next);
                dm._refreshQueueItem?.(Number(id));
            }
            window.App?.showToast?.(
                sepconT(`Removed "${row.spelling}" from ${row.ids.length} captions`,
                  `已从 ${row.ids.length} 条 caption 移除「${row.spelling}」`),
                'success');
            this.refresh();
        },

        locateNext(row) {
            const dm = this.dm;
            if (!dm || row.ids.length === 0) return;
            const activeIndex = row.ids.indexOf(Number(dm.activeId));
            const nextId = row.ids[(activeIndex + 1) % row.ids.length];
            dm._setActive?.(nextId);
        },

        // ---- QW-2 seen tracking --------------------------------------------
        _loadSeen() {
            if (this._seen) return this._seen;
            try { this._seen = JSON.parse(localStorage.getItem(SEPCON_SEEN_KEY) || '{}') || {}; }
            catch (_) { this._seen = {}; }
            return this._seen;
        },

        _markSeen(id) {
            const numeric = Number(id);
            if (!Number.isFinite(numeric)) return;
            const seen = this._loadSeen();
            if (seen[numeric]) return;
            seen[numeric] = true;
            try {
                // Prune to the current queue so the store cannot grow forever.
                const idSet = new Set(this._queueIds());
                for (const key of Object.keys(seen)) {
                    if (!idSet.has(Number(key))) delete seen[key];
                }
                localStorage.setItem(SEPCON_SEEN_KEY, JSON.stringify(seen));
            } catch (_) {}
            this._scheduleRefresh(400);
        },

        jumpToUnseen() {
            const dm = this.dm;
            if (!dm) return;
            const seen = this._loadSeen();
            const next = this._queueIds().find(id => !seen[id]);
            if (next == null) {
                window.App?.showToast?.(sepconT('All images reviewed 🎉', '全部图片都已过目 🎉'), 'success');
                return;
            }
            dm._setActive?.(next);
        },

        // ---- SEP-2 client-side NL leak scan ---------------------------------
        _renderLeaks() {
            const box = document.getElementById('sepcon-leaks');
            if (!box) return;
            const traits = this._blacklistEntries().map(sepconFold).filter(Boolean);
            const leaks = new Map(); // trait -> count of images leaking
            if (traits.length) {
                for (const id of this._queueIds()) {
                    const nl = sepconFold(this._effectiveNl(id));
                    if (!nl) continue;
                    for (const trait of traits) {
                        const pattern = new RegExp(
                            `(?<!\\w)${trait.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '[\\s_]+')}(?!\\w)`);
                        if (pattern.test(nl)) leaks.set(trait, (leaks.get(trait) || 0) + 1);
                    }
                }
            }
            if (leaks.size === 0) { box.hidden = true; box.textContent = ''; return; }
            box.hidden = false;
            box.textContent = '';
            const title = document.createElement('div');
            title.className = 'sepcon-leaks-title';
            title.textContent = sepconT('Pruned traits leaking back through NL captions:',
                '已剪除的特征从自然语言描述漏回来了：');
            box.appendChild(title);
            for (const [trait, count] of leaks) {
                const line = document.createElement('div');
                line.className = 'sepcon-leak-line';
                line.textContent = sepconT(
                    `"${trait}" appears in ${count} NL caption(s) — edit them or re-run Smart Tag (the trait list is now sent to the VLM).`,
                    `「${trait}」出现在 ${count} 条 NL 描述中 — 请手动修改，或重跑 Smart Tag（特征清单现在会传给 VLM）。`);
                box.appendChild(line);
            }
        },

});
