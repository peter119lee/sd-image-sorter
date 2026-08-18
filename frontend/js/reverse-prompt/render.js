/**
 * Telling a record apart from a guess, on screen, without ambiguity.
 *
 * The user's next action is to paste this text into a generator. "The file
 * recorded this prompt" and "we inferred this from the pixels" are different
 * claims resting on different evidence, and a guess wearing the record's
 * clothes costs the user a generation they cannot audit. So:
 *
 *   * each provenance owns its own badge AND its own one-sentence explanation;
 *   * the two live in SEPARATE containers, so rendering an inferred result
 *     cannot structurally overwrite a recorded one — the record is only ever
 *     replaced when a different image is loaded;
 *   * when both are on screen, the inferred box carries a comparison note
 *     saying which of the two actually generated the picture.
 *
 * Nothing here is written through `data-i18n`: these nodes are built at
 * runtime, and the `#app` observer re-applies static keys a frame later, which
 * would reset them. Dynamic text claims `dataset.i18nLocked` instead, the same
 * contract the artist-vocabulary rows use.
 */
'use strict';

Object.assign(window.ReversePrompt, {
    PROVENANCE: Object.freeze({
        recorded: { badgeKey: 'reverse.recordedBadge', noteKey: 'reverse.recordedNote' },
        inferred: { badgeKey: 'reverse.inferredBadge', noteKey: 'reverse.inferredNote' },
    }),

    METHOD_KEYS: Object.freeze({
        tagger: ['reverse.methodTagger', 'read by the WD14 tagger', '由 WD14 打标器识别'],
        vlm: ['reverse.methodVlm', 'written by the vision model', '由视觉模型撰写'],
        grounded: [
            'reverse.methodGrounded',
            'tagged first, then described by the vision model using those tags',
            '先打标，再把标签和图片一起交给视觉模型描述',
        ],
    }),

    _clear(element) {
        if (element) element.textContent = '';
    },

    _badge(kind) {
        const spec = this.PROVENANCE[kind];
        const badge = document.createElement('span');
        badge.className = `reverse-badge reverse-badge-${kind}`;
        this._lockedText(badge, this._t(
            spec.badgeKey,
            kind === 'recorded' ? 'Recorded in the file' : 'Our best guess',
            kind === 'recorded' ? '文件内的记录' : '我们的推测'
        ));
        return badge;
    },

    _copyButton(getText, labelSuffix) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-secondary btn-small reverse-copy';
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        icon.setAttribute('class', 'icon');
        icon.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', '#i-clipboard');
        icon.appendChild(use);
        button.appendChild(icon);
        const label = document.createElement('span');
        this._lockedText(label, this._t('reverse.copy', 'Copy', '复制'));
        button.appendChild(label);
        button.setAttribute('aria-label', `${this._t('reverse.copy', 'Copy', '复制')} ${labelSuffix}`.trim());
        button.addEventListener('click', async () => {
            const text = String(getText() || '');
            if (!text) return;
            try {
                await navigator.clipboard.writeText(text);
                window.App?.showToast?.(this._t('reverse.copied', 'Copied', '已复制'), 'success');
            } catch (error) {
                window.App?.showToast?.(
                    window.formatUserError(error, this._t('reverse.copy', 'Copy', '复制')),
                    'error'
                );
            }
        });
        return button;
    },

    /**
     * Move this text into the draft box the user owns. Explicit, because the
     * draft is where TIPO's picks land and silently reseeding it would throw
     * away edits.
     */
    _useAsDraftButton(getText) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-ghost btn-small reverse-use-draft';
        const label = document.createElement('span');
        this._lockedText(label, this._t('reverse.useAsDraft', 'Use as draft', '作为草稿'));
        button.appendChild(label);
        button.addEventListener('click', () => {
            const box = this._el('reverse-draft');
            if (!box) return;
            box.value = String(getText() || '');
            box.dispatchEvent(new Event('input', { bubbles: true }));
            box.focus();
        });
        return button;
    },

    _textBlock(className, text) {
        const block = document.createElement('pre');
        block.className = className;
        this._lockedText(block, text);
        return block;
    },

    _noteLine(className, text) {
        const line = document.createElement('p');
        line.className = className;
        this._lockedText(line, text);
        return line;
    },

    /**
     * The record the file itself carries. Rendered straight out of intake, so
     * it is on screen before any inference is even offered.
     */
    renderRecorded(recorded) {
        const host = this._el('reverse-recorded');
        if (!host) return;
        this._clear(host);
        if (!recorded || !String(recorded.prompt || '').trim()) {
            host.hidden = true;
            return;
        }

        // The app already owns the display name for every generator it parses
        // ("comfyui" -> "ComfyUI"); a raw backend token reads badly mid-sentence.
        const raw = String(recorded.generator || '').trim();
        const generator = raw
            ? String(window.App?.formatGeneratorLabel?.(raw, '') || raw)
            : '';
        const article = document.createElement('article');
        article.className = 'reverse-result';
        article.dataset.provenance = 'recorded';

        const head = document.createElement('header');
        head.className = 'reverse-result-head';
        head.appendChild(this._badge('recorded'));
        if (generator) {
            const meta = document.createElement('span');
            meta.className = 'reverse-result-meta';
            this._lockedText(meta, generator);
            head.appendChild(meta);
        }
        head.appendChild(this._copyButton(() => recorded.prompt, generator));
        head.appendChild(this._useAsDraftButton(() => recorded.prompt));
        article.appendChild(head);

        article.appendChild(this._noteLine(
            'reverse-result-note',
            generator
                ? this._t(
                    'reverse.recordedNoteWithGenerator',
                    'This is the prompt {generator} stored inside the image file. It is a record of what generated this picture, not a guess.',
                    '这是 {generator} 写进图片文件里的提示词。它是这张图如何生成的记录，不是推测。',
                    { generator }
                )
                : this._t(
                    'reverse.recordedNote',
                    'This is the prompt stored inside the image file. It is a record of what generated this picture, not a guess.',
                    '这是图片文件里保存的提示词。它是这张图如何生成的记录，不是推测。'
                )
        ));
        article.appendChild(this._textBlock('reverse-result-text', recorded.prompt));

        const negative = String(recorded.negative || '').trim();
        if (negative) {
            article.appendChild(this._noteLine(
                'reverse-result-sublabel',
                this._t('reverse.negativeLabel', 'Negative prompt', '负面提示词')
            ));
            article.appendChild(this._textBlock('reverse-result-text is-negative', negative));
        }

        host.appendChild(article);
        host.hidden = false;
    },

    /**
     * The inferred result. Never touches `#reverse-recorded`, so a record on
     * screen stays on screen; when one is present this box says outright which
     * of the two is the real one.
     */
    renderInferred(record) {
        const host = this._el('reverse-inferred');
        if (!host) return;
        this._clear(host);
        if (!record || !String(record.prompt || '').trim()) {
            host.hidden = true;
            return;
        }

        const [methodKey, methodEn, methodZh] = this.METHOD_KEYS[record.mode]
            || this.METHOD_KEYS.grounded;
        const method = this._t(methodKey, methodEn, methodZh);

        const article = document.createElement('article');
        article.className = 'reverse-result';
        article.dataset.provenance = 'inferred';

        const head = document.createElement('header');
        head.className = 'reverse-result-head';
        head.appendChild(this._badge('inferred'));
        if (record.model) {
            const meta = document.createElement('span');
            meta.className = 'reverse-result-meta';
            this._lockedText(meta, record.model);
            head.appendChild(meta);
        }
        head.appendChild(this._copyButton(() => record.prompt, method));
        head.appendChild(this._useAsDraftButton(() => record.prompt));
        article.appendChild(head);

        article.appendChild(this._noteLine(
            'reverse-result-note',
            this._t(
                'reverse.inferredNote',
                'The file records no prompt, so this was inferred from the picture ({method}). Check it before you use it.',
                '文件里没有写下提示词，所以这一份是从画面推测出来的（{method}）。使用前请先核对。',
                { method }
            )
        ));

        // Both on screen at once: say which one actually made the picture.
        if (this.state.recorded && String(this.state.recorded.prompt || '').trim()) {
            article.appendChild(this._noteLine(
                'reverse-result-compare',
                this._t(
                    'reverse.compareNote',
                    'For comparison only. The box above is the record the file itself carries of what generated this picture; this one was inferred from the pixels. They will not match, and the record is the true one.',
                    '仅供对照。上面那一块是文件自身带着的、关于这张图如何生成的记录，这一块是从像素推测的。两者不会一致，真正作准的是上面那一份。'
                )
            ));
        }

        article.appendChild(this._textBlock('reverse-result-text', record.prompt));
        host.appendChild(article);
        host.hidden = false;
    },

    /** The state of the page before anything has been dropped on it. */
    renderEmpty() {
        this._clear(this._el('reverse-recorded'));
        this._clear(this._el('reverse-inferred'));
        const recorded = this._el('reverse-recorded');
        const inferred = this._el('reverse-inferred');
        if (recorded) recorded.hidden = true;
        if (inferred) inferred.hidden = true;
        this._setStatus('', '');
        this.renderRunButtons();
        this.renderTipo();
    },

    /**
     * The run control says which claim the user is about to make. With a record
     * on screen, inference is explicitly a comparison, never a replacement.
     */
    renderRunButtons() {
        const run = this._el('btn-reverse-run');
        if (!run) return;
        const hasRecord = !!(this.state.recorded && String(this.state.recorded.prompt || '').trim());
        run.disabled = this.state.running || !this.state.sourcePath;
        this._lockedText(
            run.querySelector('.reverse-run-label') || run,
            this.state.running
                ? this._t('reverse.running', 'Working…', '正在处理…')
                : hasRecord
                    ? this._t('reverse.runAnyway', 'Infer anyway (compare)', '仍要推测（用于对照）')
                    : this._t('reverse.run', 'Infer the prompt', '推测提示词')
        );

        const noRecord = this._el('reverse-no-record');
        if (noRecord) {
            noRecord.hidden = !this.state.sourcePath || hasRecord;
            if (!noRecord.hidden) {
                this._lockedText(noRecord, this._t(
                    'reverse.noRecordedPrompt',
                    'This file records no prompt of its own, so there is nothing to read. Inference is the only option here.',
                    '这个文件没有记录自己的提示词，所以没有可读的记录，这里只能依靠推测。'
                ));
            }
        }

        const cancel = this._el('btn-reverse-cancel');
        if (cancel) cancel.hidden = !this.state.running;
    },
});
