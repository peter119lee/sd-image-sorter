/**
 * TIPO's home — prompt expansion, where somebody writing a prompt will find it.
 *
 * TIPO (KohakuBlueleaf/KGen) has shipped for a while: an opt-in llama-cpp GGUF
 * runtime, weights that download on demand, output filtered through the shared
 * out-of-vocabulary gate, and a never-auto-apply contract. Its only entrance was
 * the Dataset Maker's separation console, keyed on a whole queue's tag
 * frequencies — useful there, and invisible to anyone writing one prompt. That
 * dataset flow is untouched; this is a second entrance for the single-prompt
 * case, on the page where a prompt is now being written.
 *
 * Three honesty constraints shape the control:
 *
 * 1. **Dialect.** TIPO expands a Booru TAG LIST. `AGENTS.md` treats Krea 2 as a
 *    natural-language-first target, and offering tag expansion for an NL target
 *    is wrong output rather than reduced value. The gate therefore asks
 *    `TargetModel.dialectFor`, which is pinned to `caption_dialect.py` by a
 *    contract test — there is no second copy of that map here.
 * 2. **The download.** First press pulls 100-250 MB of weights. The Model Center
 *    card is deliberately manual-only (`download_supported: false`, no
 *    `prepare_model("tipo")` branch), so a Prepare button here would be a
 *    control that cannot run. Instead the size is stated and confirmed BEFORE
 *    the press that spends it, using the card's own installed-variant list to
 *    decide whether anything needs downloading at all.
 * 3. **Never auto-apply.** Proposals arrive as a default-unchecked checklist and
 *    the confirmed picks are appended to the draft box, which the user owns.
 */
'use strict';

Object.assign(window.ReversePrompt, {
    TIPO_MAX_TAGS: 100,

    /** The text TIPO would work from: the draft box, which the user owns. */
    _draftText() {
        return String(this._el('reverse-draft')?.value || '').trim();
    },

    /**
     * The subset of the draft that can honestly be sent as a tag list.
     *
     * The endpoint's input is a Booru tag list, so a prose clause in there is a
     * false statement about the input rather than a weak hint. This keeps short
     * comma segments without sentence punctuation and drops the rest; when
     * nothing survives, the panel says the draft reads as prose instead of
     * sending a sentence and calling the output a suggestion.
     */
    _draftTagCandidates() {
        return this.promptTextToTags(this._draftText())
            .filter((segment) => !/[.!?;:]/.test(segment) && segment.split(/\s+/).length <= 6)
            .slice(0, this.TIPO_MAX_TAGS);
    },

    /** null = no evidenced opinion (the map is deliberately silent for some). */
    _targetDialect() {
        return window.TargetModel?.dialectFor?.(this.currentTarget()) || null;
    },

    renderTipo() {
        const panel = this._el('reverse-tipo');
        const button = this._el('btn-reverse-tipo');
        const note = this._el('reverse-tipo-note');
        if (!panel || !button) return;

        const naturalTarget = this._targetDialect() === 'natural';
        const candidates = this._draftTagCandidates();
        button.disabled = naturalTarget || candidates.length === 0;

        if (note) {
            this._lockedText(note, naturalTarget
                ? this._t(
                    'reverse.tipoDialectBlocked',
                    'This target is documented to want natural-language prompts, and TIPO expands Booru tag lists, so it is switched off here.',
                    '这个目标模型的文档要求自然语言提示词，而 TIPO 扩写的是 Booru 标签列表，所以在这里停用。'
                )
                : candidates.length === 0
                    ? this._t(
                        'reverse.tipoNeedsTags',
                        'TIPO expands a Booru tag list. Put comma-separated tags in the draft box above to use it.',
                        'TIPO 扩写的是 Booru 标签列表。请先在上面的草稿框里填入以逗号分隔的标签。'
                    )
                    : this._t(
                        'reverse.tipoHelp',
                        'Suggests Booru tags the taggers never scored. Nothing is applied until you check it.',
                        '推荐打标器从未评分的 Booru 标签。除非你勾选，否则不会应用任何内容。'
                    ));
        }
    },

    /** Has the GGUF already been fetched? The Model Center card is the source. */
    async _tipoWeightsInstalled() {
        try {
            const payload = await window.App.API.get('/api/models/status');
            const card = (payload?.models || []).find((item) => item?.id === 'tipo');
            return Array.isArray(card?.installed_variants) && card.installed_variants.length > 0;
        } catch (_error) {
            // Unknown is not "installed": warn rather than spend the download.
            return false;
        }
    },

    async suggestTipo() {
        const button = this._el('btn-reverse-tipo');
        if (!button || button.disabled) return;
        const tags = this._draftTagCandidates();
        if (tags.length === 0) return;

        if (!(await this._tipoWeightsInstalled())) {
            const confirmed = await new Promise((resolve) => {
                const ask = window.App?.showConfirm;
                const message = this._t(
                    'reverse.tipoDownloadWarn',
                    'TIPO is not downloaded yet. The first run fetches 100-250 MB of model files into your data folder, and nothing happens until that finishes. Download it now?',
                    'TIPO 还没有下载。首次运行会把 100-250 MB 的模型文件下载到你的数据文件夹，下载完成之前不会有任何结果。现在下载吗？'
                );
                const title = this._t('reverse.tipoDownloadTitle', 'Download TIPO?', '下载 TIPO？');
                if (typeof ask === 'function') {
                    ask(title, message, () => resolve(true), () => resolve(false));
                } else {
                    resolve(window.confirm(message));
                }
            });
            if (!confirmed) return;
        }

        button.disabled = true;
        this._renderTipoMessage(this._t('reverse.tipoRunning', 'Asking TIPO…', '正在询问 TIPO…'));
        try {
            const report = await window.App.API.post('/api/tags/suggest-upsample', {
                tags,
                target: 'short',
            });
            this._renderTipoProposals(report);
        } catch (error) {
            // A 400 carries the actionable install command verbatim; the shared
            // formatter keeps it intact and localizes everything else.
            this._renderTipoMessage(window.formatUserError(
                error,
                this._t('reverse.tipoFailed', 'Tag suggestion failed', '标签推荐失败')
            ));
        } finally {
            this.renderTipo();
        }
    },

    _tipoResults() {
        const host = this._el('reverse-tipo-results');
        if (host) {
            host.textContent = '';
            host.hidden = false;
        }
        return host;
    },

    _renderTipoMessage(text) {
        const host = this._tipoResults();
        if (!host) return;
        host.appendChild(this._noteLine('reverse-tipo-line', text));
    },

    _renderTipoProposals(report) {
        const host = this._tipoResults();
        if (!host) return;
        const proposals = (report?.proposed_tags || []).filter((entry) => entry?.tag);
        if (proposals.length === 0) {
            host.appendChild(this._noteLine('reverse-tipo-line', this._t(
                'reverse.tipoNone',
                'TIPO found no in-vocabulary tags to add — the draft already covers its ideas.',
                'TIPO 没有找到可补充的词表内标签 — 现有草稿已覆盖它的建议。'
            )));
            return;
        }

        host.appendChild(this._noteLine('reverse-tipo-line', this._t(
            'reverse.tipoProposals',
            'TIPO proposes {count} tag(s) the taggers never scored. Check the ones you want.',
            'TIPO 推荐了 {count} 个打标器从未评分的标签，请勾选你想要的。',
            { count: proposals.length }
        )));

        const list = document.createElement('div');
        list.className = 'reverse-tipo-list';
        const boxes = [];
        for (const proposal of proposals) {
            const row = document.createElement('label');
            row.className = 'reverse-tipo-item';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = String(proposal.tag);
            // DEFAULT UNCHECKED — a proposal is a suggestion, never a decision.
            box.checked = false;
            row.appendChild(box);
            const dot = document.createElement('span');
            dot.className = `cap-ac-dot cap-ac-dot-${proposal.category || 'unknown'}`;
            row.appendChild(dot);
            const name = document.createElement('span');
            this._lockedText(name, String(proposal.tag));
            row.appendChild(name);
            list.appendChild(row);
            boxes.push(box);
        }
        host.appendChild(list);

        const apply = document.createElement('button');
        apply.type = 'button';
        apply.className = 'btn btn-secondary btn-small';
        apply.id = 'btn-reverse-tipo-apply';
        const label = document.createElement('span');
        apply.appendChild(label);
        const refresh = () => {
            const checked = boxes.filter((box) => box.checked).length;
            apply.disabled = checked === 0;
            this._lockedText(label, this._t(
                'reverse.tipoApply',
                'Add {count} checked to the draft',
                '把已勾选的 {count} 个加入草稿',
                { count: checked }
            ));
        };
        list.addEventListener('change', refresh);
        refresh();
        apply.addEventListener('click', () => {
            const picked = boxes.filter((box) => box.checked).map((box) => box.value);
            const added = this._appendToDraft(picked);
            window.App?.showToast?.(this._t(
                'reverse.tipoApplied',
                'Added {count} tag(s) to the draft',
                '已把 {count} 个标签加入草稿',
                { count: added }
            ), 'success');
        });
        host.appendChild(apply);
    },

    _appendToDraft(tags) {
        const box = this._el('reverse-draft');
        if (!box) return 0;
        const fold = (tag) => String(tag || '').trim().toLowerCase().replace(/_/g, ' ');
        const existing = this.promptTextToTags(box.value);
        const seen = new Set(existing.map(fold));
        let added = 0;
        for (const tag of tags) {
            if (seen.has(fold(tag))) continue;
            seen.add(fold(tag));
            existing.push(String(tag).replace(/_/g, ' '));
            added += 1;
        }
        box.value = existing.join(', ');
        box.dispatchEvent(new Event('input', { bubbles: true }));
        return added;
    },
});
