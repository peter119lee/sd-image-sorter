/**
 * Intake — one file in, one retained path out, metadata read on the way.
 *
 * `POST /api/parse-image` is the Reader's un-indexed intake and it already does
 * every part of this that is hard: it streams the upload with a 64 MB cap,
 * refuses a file it cannot decode, keeps the temp file for 24 h, returns its
 * absolute path as `source_temp_path`, and — the reason this page exists —
 * hands back the metadata it parsed out of the file. So the record and the
 * handle for inference arrive in the same response, and reading the record
 * costs nothing.
 *
 * The drag/drop/click wiring is the Reader's `_setupDropZone`, invoked with this
 * object as its receiver. That helper takes its elements as arguments and
 * dispatches only to `this._handleFile`, which is what makes it shareable;
 * `backend/tests/test_frontend_contract.py` pins that so the coupling cannot
 * quietly grow a second dependency.
 */
'use strict';

Object.assign(window.ReversePrompt, {
    _attachDropZone() {
        const zone = this._el('reverse-drop-zone');
        const input = this._el('reverse-file-input');
        if (!zone || zone.dataset.reverseBound === '1') return;
        zone.dataset.reverseBound = '1';
        window.ImageReader._setupDropZone.call(this, zone, input);
        zone.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                input?.click();
            }
        });
    },

    /** The receiver `_setupDropZone` calls. Same signature, this page's flow. */
    async _handleFile(file) {
        if (!file || !String(file.type || '').startsWith('image/')) {
            window.App?.showToast?.(
                this._t('reverse.invalidFile', 'Drop a single image file', '请拖入一张图片文件'),
                'error'
            );
            return;
        }

        this.state.sourcePath = '';
        this.state.recorded = null;
        this.state.inferred = null;
        this.state.fileName = file.name || '';
        this.renderEmpty();
        this._showPreview(file);
        this._setStatus(this._t('reverse.reading', 'Reading the file…', '正在读取文件…'), '');

        try {
            const form = new FormData();
            form.append('file', file);
            const response = await fetch('/api/parse-image', { method: 'POST', body: form });
            const parsed = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(parsed.detail || parsed.error || `HTTP ${response.status}`);
            }

            this.state.sourcePath = String(parsed.source_temp_path || '');
            this.state.recorded = this._recordedFrom(parsed);
            this._setStatus('', '');
            this.renderRecorded(this.state.recorded);
            this.renderInferred(null);
            this.renderRunButtons();
            this.renderTipo();
        } catch (error) {
            this._setStatus(
                window.formatUserError(
                    error,
                    this._t('reverse.readFailed', 'Could not read this image', '无法读取这张图片')
                ),
                'error'
            );
            this.renderRunButtons();
        }
    },

    /**
     * The prompt the FILE recorded, or null. Only the fields the parsers
     * populate for a real generation are read: an empty prompt string means the
     * file recorded nothing, which is a different answer from "we have not
     * looked yet" and must not be dressed up as a result.
     */
    _recordedFrom(parsed) {
        const prompt = String(parsed?.prompt || '').trim();
        if (!prompt) return null;
        return {
            prompt,
            negative: String(parsed?.negative_prompt || '').trim(),
            generator: String(parsed?.generator || '').trim().toLowerCase() === 'unknown'
                ? ''
                : String(parsed?.generator || '').trim(),
        };
    },

    _showPreview(file) {
        const preview = this._el('reverse-preview');
        const zone = this._el('reverse-drop-zone');
        const name = this._el('reverse-file-name');
        if (preview) {
            if (preview._blobUrl) URL.revokeObjectURL(preview._blobUrl);
            const url = URL.createObjectURL(file);
            preview._blobUrl = url;
            preview.src = url;
            preview.hidden = false;
            preview.alt = file.name || '';
        }
        if (zone) zone.classList.add('has-image');
        if (name) this._lockedText(name, file.name || '');
    },

    /** Run the selected mode. The adapter hides which transport that needs. */
    async run() {
        if (this.state.running || !this.state.sourcePath) return;
        const mode = this.currentMode();
        this.state.running = true;
        this.state.cancelRequested = false;
        this.state.jobId = '';
        this.state.abort = null;
        this.renderRunButtons();
        this._setStatus(this._t('reverse.running', 'Working…', '正在处理…'), '');

        try {
            const record = await this.runMode(mode, this.state.sourcePath, {
                onProgress: (snapshot) => this._reportProgress(snapshot),
            });
            this.state.inferred = record;
            this._setStatus('', '');
            this.renderInferred(record);
        } catch (error) {
            this.state.inferred = null;
            this.renderInferred(null);
            // A stop the user asked for is not a failure, and painting it red
            // is how a red status stops meaning anything. Both halves are
            // required: the shipped 409 refusal for a busy runtime ends in
            // "…or cancel it.", which `isCancellationError` matches on text
            // alone, and that IS an error the user must read.
            const abandoned = this.state.cancelRequested
                && window.isCancellationError?.(error);
            this._setStatus(
                abandoned
                    ? this._t('reverse.cancelled', 'Cancelled.', '已取消。')
                    : window.formatUserError(
                        error,
                        this._t('reverse.inferFailed', 'Could not infer a prompt', '无法推测提示词')
                    ),
                abandoned ? '' : 'error'
            );
        } finally {
            this.state.running = false;
            this.state.cancelRequested = false;
            this.state.abort = null;
            this.renderRunButtons();
            this.renderTipo();
        }
    },

    /**
     * A queued job was accepted, not refused, so it gets its own line. The
     * nav chip already names whatever holds the shared model; repeating that
     * here would be a second thing to keep true.
     */
    _reportProgress(snapshot) {
        this._setStatus(String(snapshot?.status || '') === 'queued'
            ? this._t(
                'reverse.queued',
                'Queued. It starts as soon as the shared model is free.',
                '已排队。共享模型空闲后就会开始。'
            )
            : this._t('reverse.running', 'Working…', '正在处理…'), '');
    },

    cancel() {
        if (!this.state.running) return;
        this.state.cancelRequested = true;
        this._setStatus(this._t('reverse.cancelling', 'Cancelling…', '正在取消…'), '');
        // The polled modes notice the flag on their next tick and tell the
        // backend; the un-polled one has only its own request to let go of.
        this.state.abort?.abort();
    },
});
