/**
 * separation-console/tipo.js — separation-console.js decomposition
 * (verbatim Object.assign mixin). Method bodies moved BYTE-IDENTICAL from
 * frontend/js/modules/separation-console.js pre-cut lines 521-661 (of
 * 1,155): the TIPO tag-upsampling assist — _TIPO_TOP_N, suggestUpsample,
 * _renderTipo (default-unchecked checklist) and _appendToCommonTags.
 * Strict per-file (the original IIFE was strict). Joins the ONE unsealed
 * object declared in separation-console/core.js, which loads FIRST;
 * separation-console/boot.js publishes window.SeparationConsole LAST.
 * Family renames applied (sepconT/sepconFold/SEPCON_* — see core.js).
 */
'use strict';
Object.assign(SeparationConsole, {
        // ---- TIPO tag-upsampling assist (roadmap #8, v1) -----------------------
        // WD14-family taggers can only score tags inside their trained label
        // set — a concept without a label is invisible to them AND to the
        // score-band coverage-gaps flow above. TIPO (KohakuBlueleaf/KGen)
        // proposes tags via LM continuation over the danbooru vocabulary,
        // surfacing those blind spots. Strictly human-confirmed: proposals
        // render as a default-unchecked checklist and the confirmed picks
        // land in the export Common tags box (the least destructive landing
        // zone) — v1 never writes DB rows.
        _TIPO_TOP_N: 100,
        _TIPO_MODEL_KEY: 'sd-tipo-model-v1',
        _TIPO_VARIANT_SIZES: { 'v2.1': '1.1 GB', '200m-ft': '210 MB' },

        _tipoSelectedModel() {
            const select = document.getElementById('sepcon-tipo-model');
            const raw = select ? String(select.value || '') : '';
            if (raw === 'v2.1' || raw === '200m-ft') return raw;
            try {
                const stored = localStorage.getItem(this._TIPO_MODEL_KEY);
                if (stored === 'v2.1' || stored === '200m-ft') return stored;
            } catch (_error) { /* keep the default */ }
            return 'v2.1';
        },

        async _tipoWeightsInstalled(modelKey) {
            try {
                const response = await fetch('/api/models/status');
                const payload = await response.json();
                const card = (payload?.models || []).find((item) => item?.id === 'tipo');
                const installed = card?.installed_variants;
                return Boolean(Array.isArray(installed) && installed.includes(modelKey));
            } catch (_error) {
                return false;
            }
        },

        async suggestUpsample() {
            const panel = this._gapsPanel();
            panel.hidden = false;
            const model = this._tipoSelectedModel();
            const size = this._TIPO_VARIANT_SIZES[model] || '1.1 GB';
            const { counts } = this.computeStats();
            const tags = [...counts.entries()]
                .sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]))
                .slice(0, this._TIPO_TOP_N)
                .map(([, entry]) => [...entry.spellings][0]);
            if (tags.length === 0) {
                panel.textContent = sepconT(
                    'The queue has no caption tags yet — load images (and captions) first.',
                    '队列还没有任何 caption 标签 — 请先载入图片和 caption。');
                return;
            }
            if (!(await this._tipoWeightsInstalled(model))) {
                const message = sepconT(
                    `This TIPO model is not downloaded yet. The first run fetches about ${size} into your data folder, and nothing happens until that finishes. Download it now?`,
                    `这个 TIPO 模型还没有下载。首次运行会把约 ${size} 的文件下载到你的数据文件夹，下载完成之前不会有任何结果。现在下载吗？`);
                const confirmed = await new Promise((resolve) => {
                    const ask = window.App?.showConfirm;
                    if (typeof ask === 'function') {
                        ask(sepconT('Download TIPO?', '下载 TIPO？'), message, () => resolve(true), () => resolve(false));
                    } else {
                        resolve(window.confirm(message));
                    }
                });
                if (!confirmed) {
                    panel.hidden = true;
                    return;
                }
            }
            panel.textContent = sepconT(
                `Asking TIPO for missed tags… (first run downloads the model, ~${size})`,
                `正在让 TIPO 推荐缺漏标签…（首次运行会下载模型，约 ${size}）`);
            const btn = document.getElementById('sepcon-tipo-suggest');
            if (btn) btn.disabled = true;
            try {
                const response = await fetch('/api/tags/suggest-upsample', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tags, target: 'short', model }),
                });
                const body = await response.json().catch(() => ({}));
                // A 400 carries the actionable bilingual message (e.g. the
                // exact pip install hint when the opt-in runtime is missing)
                // — surface it verbatim, like the mask editor's auto handler.
                if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
                this._renderTipo(panel, body);
            } catch (e) {
                panel.textContent = sepconT('Tag suggestion failed: ', '标签推荐失败：') + String(e.message || e);
            } finally {
                if (btn) btn.disabled = false;
            }
        },

        _renderTipo(panel, report) {
            panel.textContent = '';
            const proposals = report.proposed_tags || [];
            const head = document.createElement('div');
            head.className = 'sepcon-gaps-head';
            const title = document.createElement('span');
            title.className = 'sepcon-gaps-title';
            title.textContent = proposals.length === 0
                ? sepconT('TIPO found no in-vocabulary tags to add — the caption set already covers its ideas.',
                    'TIPO 没有找到可补充的词表内标签 — 现有 caption 已覆盖它的建议。')
                : sepconT(`TIPO proposes ${proposals.length} tag(s) the taggers never scored — review and pick:`,
                    `TIPO 推荐了 ${proposals.length} 个打标器从未评分的标签 — 请人工挑选：`);
            head.appendChild(title);
            head.appendChild(this._actionBtn('✕', sepconT('Close', '关闭'),
                () => { panel.hidden = true; panel.textContent = ''; }));
            panel.appendChild(head);
            if (proposals.length === 0) return;

            const hint = document.createElement('div');
            hint.className = 'sepcon-gap-line sepcon-tipo-hint';
            hint.textContent = sepconT(
                'Checked tags are appended to the export "Common tags" box (added to every caption at export) — nothing is written to the library. Prefer per-image precision? Hand-edit captions in the editor instead.',
                '勾选的标签会追加到导出的「公共标签」框（导出时加进每条 caption）— 不会写入图库。想按图精修？请直接在编辑器里手改 caption。');
            panel.appendChild(hint);

            const list = document.createElement('div');
            list.className = 'sepcon-tipo-list';
            const boxes = [];
            for (const proposal of proposals) {
                const row = document.createElement('label');
                row.className = 'sepcon-tipo-item';
                const box = document.createElement('input');
                box.type = 'checkbox';
                box.className = 'sepcon-tipo-check';
                box.value = proposal.tag;
                // DEFAULT UNCHECKED — proposals are suggestions, never decisions.
                box.checked = false;
                row.appendChild(box);
                const dot = document.createElement('span');
                dot.className = `cap-ac-dot cap-ac-dot-${proposal.category || 'unknown'}`;
                row.appendChild(dot);
                const name = document.createElement('span');
                name.textContent = proposal.tag;
                row.appendChild(name);
                list.appendChild(row);
                boxes.push(box);
            }
            panel.appendChild(list);

            const apply = document.createElement('button');
            apply.type = 'button';
            apply.className = 'btn btn-secondary btn-small';
            apply.id = 'sepcon-tipo-apply';
            const refreshLabel = () => {
                const checked = boxes.filter(b => b.checked).length;
                apply.disabled = checked === 0;
                apply.textContent = sepconT(
                    `Add ${checked} checked to Common tags`,
                    `把已勾选的 ${checked} 个加入公共标签`);
            };
            list.addEventListener('change', refreshLabel);
            refreshLabel();
            apply.addEventListener('click', () => {
                const picked = boxes.filter(b => b.checked).map(b => b.value);
                const added = this._appendToCommonTags(picked);
                window.App?.showToast?.(
                    sepconT(`Added ${added} tag(s) to Common tags`, `已把 ${added} 个标签加入公共标签`),
                    'success');
            });
            panel.appendChild(apply);
        },

        _appendToCommonTags(tags) {
            const box = document.getElementById('dataset-common-tags');
            if (!box) return 0;
            const existing = String(box.value || '')
                .split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
            const seen = new Set(existing.map(sepconFold));
            let added = 0;
            for (const tag of tags) {
                if (seen.has(sepconFold(tag))) continue;
                seen.add(sepconFold(tag));
                existing.push(tag);
                added += 1;
            }
            box.value = existing.join(', ');
            // Route through the input event so pipeline state and persisted
            // form snapshots see the change like a hand edit.
            box.dispatchEvent(new Event('input', { bubbles: true }));
            return added;
        },

});
