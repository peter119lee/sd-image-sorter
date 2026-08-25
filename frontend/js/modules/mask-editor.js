/**
 * Masked-training mask editor (Phase 4).
 *
 * LoRA trainers support masked loss: a grayscale sidecar per image where
 * white = train, black = ignore. This modal edits that mask on a canvas:
 * the stored mask (or a fresh all-white one) renders as a red overlay on
 * the EXCLUDED regions, include/exclude brushes paint white/black, and
 * "auto subject" asks the selected rembg or Lucida backend for a starting
 * mask. Nothing persists until Save (PUT /api/masks/{id}).
 *
 * Entry point: the 🎭 button in the Dataset Maker editor pane, shown for
 * gallery images only (local imports have no stored masks). Wired through
 * DM._activeChangedHooks (FE-1 2b registry) — no monkey-patching.
 */
(function () {
    'use strict';

    function t(en, zh) {
        try {
            const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
            return String(lang).toLowerCase().startsWith('zh') ? zh : en;
        } catch (_) { return en; }
    }

    function selectedAutoMethod() {
        const select = document.getElementById('mask-auto-method');
        const method = String(select?.value || '').trim().toLowerCase();
        if (method !== 'rembg' && method !== 'lucida') {
            throw new Error(`Unsupported auto-mask engine: ${method || '(empty)'}`);
        }
        return method;
    }

    function syncLucidaLicense() {
        const license = document.getElementById('mask-lucida-license');
        if (!license) return;
        license.hidden = document.getElementById('mask-auto-method')?.value !== 'lucida';
    }

    const MaskEditor = {
        _hooked: false,
        _imageId: null,
        _image: null,          // HTMLImageElement (full-res source)
        _maskCanvas: null,     // offscreen, natural size, grayscale content
        _tool: 'include',
        _brushSize: 40,
        _drawing: false,
        _lastPoint: null,
        _dirty: false,
        _hasStoredMask: false,

        get dm() { return window.DatasetMaker || null; },

        init() {
            const modal = document.getElementById('mask-editor-modal');
            if (!modal) return;
            document.getElementById('btn-dataset-mask-edit')?.addEventListener('click', () => {
                const id = Number(this.dm?.activeId);
                if (Number.isFinite(id) && id > 0) this.open(id);
            });
            document.getElementById('mask-editor-close')?.addEventListener('click', () => this.close());
            document.getElementById('mask-editor-cancel')?.addEventListener('click', () => this.close());
            modal.querySelector('.dataset-modal-backdrop')?.addEventListener('click', () => this.close());
            document.getElementById('mask-editor-save')?.addEventListener('click', () => this.save());
            document.getElementById('mask-editor-delete')?.addEventListener('click', () => this.deleteMask());
            document.getElementById('mask-tool-include')?.addEventListener('click', () => this._setTool('include'));
            document.getElementById('mask-tool-exclude')?.addEventListener('click', () => this._setTool('exclude'));
            document.getElementById('mask-tool-invert')?.addEventListener('click', () => this._invert());
            document.getElementById('mask-tool-fill')?.addEventListener('click', () => this._fillWhite());
            document.getElementById('mask-tool-auto')?.addEventListener('click', () => this._autoMask());
            document.getElementById('btn-dataset-mask-auto-all')?.addEventListener('click', () => this._autoMaskAll());
            document.getElementById('mask-auto-method')?.addEventListener('change', syncLucidaLicense);
            syncLucidaLicense();
            const size = document.getElementById('mask-brush-size');
            size?.addEventListener('input', () => {
                this._brushSize = Number(size.value) || 40;
                const label = document.getElementById('mask-brush-size-value');
                if (label) label.textContent = String(this._brushSize);
            });
            this._bindCanvas();

            window.addEventListener('dataset:changed', () => this._ensureHook());
            // DM's parts load dynamically AFTER this static module, so the
            // hook registry may not exist yet — poll briefly until it does
            // (cleared on success), so the entry works even when state is
            // seeded without a dataset:changed event.
            this._hookPoll = setInterval(() => this._ensureHook(), 500);
            this._ensureHook();
        },

        // ---- DM integration (visibility + has-mask indicator) --------------
        _ensureHook() {
            const dm = this.dm;
            if (this._hooked || !dm || !Array.isArray(dm._activeChangedHooks)) return;
            this._hooked = true;
            if (this._hookPoll) {
                clearInterval(this._hookPoll);
                this._hookPoll = null;
            }
            dm._activeChangedHooks.push((id) => this._refreshEntry(Number(id)));
            // Catch up for an activation that happened before registration.
            if (dm.activeId != null) this._refreshEntry(Number(dm.activeId));
        },

        async _refreshEntry(id) {
            const controls = document.getElementById('dataset-mask-controls');
            if (!controls) return;
            const isGallery = Number.isFinite(id) && id > 0;
            controls.hidden = !isGallery;
            if (!isGallery) return;
            const state = document.getElementById('dataset-mask-state');
            if (!state) return;
            state.textContent = '';
            try {
                const response = await fetch('/api/masks/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_ids: [id] }),
                });
                if (!response.ok) return;
                const body = await response.json();
                if (Number(this.dm?.activeId) !== id) return; // stale
                const has = !!body.masks?.[String(id)];
                state.textContent = has ? t('mask saved', '已有遮罩') : '';
                state.classList.toggle('has-mask', has);
            } catch (_) { /* indicator only */ }
        },

        // ---- lifecycle ------------------------------------------------------
        async open(imageId) {
            const modal = document.getElementById('mask-editor-modal');
            if (!modal) return;
            this._imageId = imageId;
            this._dirty = false;
            syncLucidaLicense();
            modal.hidden = false;
            this._status(t('Loading image…', '加载图片中…'));
            try {
                this._image = await this._loadImage(`/api/image-file/${imageId}`);
            } catch (e) {
                this._status(t('Could not load the image: ', '图片加载失败：') + String(e.message || e));
                return;
            }
            this._maskCanvas = document.createElement('canvas');
            this._maskCanvas.width = this._image.naturalWidth;
            this._maskCanvas.height = this._image.naturalHeight;
            const ctx = this._maskCanvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, this._maskCanvas.width, this._maskCanvas.height);
            this._hasStoredMask = false;
            try {
                const stored = await this._loadImage(`/api/masks/${imageId}?t=${Date.now()}`);
                ctx.drawImage(stored, 0, 0, this._maskCanvas.width, this._maskCanvas.height);
                this._hasStoredMask = true;
            } catch (_) { /* 404 = no mask yet: stay all-white */ }
            const display = document.getElementById('mask-editor-canvas');
            display.width = this._image.naturalWidth;
            display.height = this._image.naturalHeight;
            this._render();
            this._status(this._hasStoredMask
                ? t('Loaded the stored mask. Paint, then Save.', '已加载已存遮罩。可继续涂抹后保存。')
                : t('No mask yet — everything is trained. Paint exclusions or use Auto subject.',
                    '尚无遮罩 — 整张都会参与训练。可涂抹排除区域，或用「自动主体」起手。'));
        },

        close() {
            const modal = document.getElementById('mask-editor-modal');
            if (modal) modal.hidden = true;
            this._imageId = null;
            this._image = null;
            this._maskCanvas = null;
        },

        _loadImage(src) {
            return new Promise((resolve, reject) => {
                const image = new Image();
                image.onload = () => resolve(image);
                image.onerror = () => reject(new Error(`load failed: ${src}`));
                image.src = src;
            });
        },

        // ---- rendering --------------------------------------------------------
        _render() {
            const display = document.getElementById('mask-editor-canvas');
            if (!display || !this._image || !this._maskCanvas) return;
            const ctx = display.getContext('2d');
            ctx.clearRect(0, 0, display.width, display.height);
            ctx.drawImage(this._image, 0, 0, display.width, display.height);

            // Red overlay over EXCLUDED (dark) regions: invert the mask, use
            // it as the alpha of a solid red layer.
            const inverted = document.createElement('canvas');
            inverted.width = this._maskCanvas.width;
            inverted.height = this._maskCanvas.height;
            const ictx = inverted.getContext('2d');
            ictx.fillStyle = '#ffffff';
            ictx.fillRect(0, 0, inverted.width, inverted.height);
            ictx.globalCompositeOperation = 'difference';
            ictx.drawImage(this._maskCanvas, 0, 0);

            const overlay = document.createElement('canvas');
            overlay.width = inverted.width;
            overlay.height = inverted.height;
            const octx = overlay.getContext('2d');
            octx.fillStyle = '#e5484d';
            octx.fillRect(0, 0, overlay.width, overlay.height);
            octx.globalCompositeOperation = 'destination-in';
            octx.drawImage(inverted, 0, 0);

            ctx.globalAlpha = 0.45;
            ctx.drawImage(overlay, 0, 0, display.width, display.height);
            ctx.globalAlpha = 1;
        },

        // ---- painting -----------------------------------------------------------
        _bindCanvas() {
            const display = document.getElementById('mask-editor-canvas');
            if (!display) return;
            const point = (event) => {
                const rect = display.getBoundingClientRect();
                return {
                    x: (event.clientX - rect.left) * (display.width / rect.width),
                    y: (event.clientY - rect.top) * (display.height / rect.height),
                };
            };
            display.addEventListener('pointerdown', (event) => {
                if (!this._maskCanvas) return;
                display.setPointerCapture(event.pointerId);
                this._drawing = true;
                this._lastPoint = point(event);
                this._stroke(this._lastPoint, this._lastPoint);
                event.preventDefault();
            });
            display.addEventListener('pointermove', (event) => {
                if (!this._drawing || !this._maskCanvas) return;
                const next = point(event);
                this._stroke(this._lastPoint, next);
                this._lastPoint = next;
            });
            const stop = () => { this._drawing = false; this._lastPoint = null; };
            display.addEventListener('pointerup', stop);
            display.addEventListener('pointercancel', stop);
        },

        _stroke(from, to) {
            const ctx = this._maskCanvas.getContext('2d');
            ctx.strokeStyle = this._tool === 'include' ? '#ffffff' : '#000000';
            ctx.lineWidth = this._brushSize;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.beginPath();
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
            ctx.stroke();
            this._dirty = true;
            this._render();
        },

        _setTool(tool) {
            this._tool = tool;
            document.getElementById('mask-tool-include')?.classList.toggle('is-active', tool === 'include');
            document.getElementById('mask-tool-exclude')?.classList.toggle('is-active', tool === 'exclude');
        },

        _invert() {
            if (!this._maskCanvas) return;
            const ctx = this._maskCanvas.getContext('2d');
            ctx.globalCompositeOperation = 'difference';
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, this._maskCanvas.width, this._maskCanvas.height);
            ctx.globalCompositeOperation = 'source-over';
            this._dirty = true;
            this._render();
        },

        _fillWhite() {
            if (!this._maskCanvas) return;
            const ctx = this._maskCanvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, this._maskCanvas.width, this._maskCanvas.height);
            this._dirty = true;
            this._render();
        },

        async _autoMask() {
            if (!this._imageId) return;
            let method;
            try {
                method = selectedAutoMethod();
            } catch (e) {
                this._status(String(e.message || e));
                return;
            }
            const button = document.getElementById('mask-tool-auto');
            if (button) button.disabled = true;
            this._status(method === 'lucida'
                ? t('Generating Lucida subject mask with the prepared model…',
                    '正在使用已准备的 Lucida 模型生成主体遮罩…')
                : t('Generating subject mask (first run downloads the u2net model)…',
                    '生成主体遮罩中（首次会下载 u2net 模型）…'));
            try {
                const response = await fetch(`/api/masks/${this._imageId}/auto`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ method }),
                });
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
                const generated = await this._loadImage(body.data_url);
                const ctx = this._maskCanvas.getContext('2d');
                ctx.fillStyle = '#000000';
                ctx.fillRect(0, 0, this._maskCanvas.width, this._maskCanvas.height);
                ctx.drawImage(generated, 0, 0, this._maskCanvas.width, this._maskCanvas.height);
                this._dirty = true;
                this._render();
                this._status(t('Auto mask ready — refine with the brushes, then Save.',
                    '自动遮罩完成 — 可用画笔修整后保存。'));
            } catch (e) {
                this._status(String(e.message || e));
            } finally {
                if (button) button.disabled = false;
            }
        },

        async _autoMaskAll() {
            const ids = (this.dm?.imageIds || []).filter((id) => Number.isSafeInteger(id) && id > 0);
            if (!ids.length) {
                window.App?.showToast?.(t('Add gallery images first', '请先加入图库图片'), 'warning');
                return;
            }
            let method;
            try {
                method = selectedAutoMethod();
            } catch (e) {
                window.App?.showToast?.(String(e.message || e), 'error');
                return;
            }
            const button = document.getElementById('btn-dataset-mask-auto-all');
            const state = document.getElementById('dataset-mask-state');
            if (button) button.disabled = true;
            try {
                const started = await fetch('/api/masks/auto-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_ids: ids, method, overwrite: false }),
                });
                const startBody = await started.json().catch(() => ({}));
                if (started.status === 409) {
                    throw new Error(t(
                        'An auto-mask batch is already running',
                        '已有批量遮罩任务在跑',
                    ));
                }
                if (!started.ok) {
                    throw new Error(startBody.detail || startBody.error || `HTTP ${started.status}`);
                }
                const jobId = startBody.job_id || startBody.id;
                let body = startBody;
                for (let i = 0; i < 600; i += 1) {
                    const polled = await fetch(`/api/bulk-jobs/${jobId}`);
                    body = await polled.json().catch(() => ({}));
                    if (state && body.message) state.textContent = body.message;
                    if (['done', 'error', 'cancelled'].includes(String(body.status || ''))) break;
                    await new Promise((resolve) => setTimeout(resolve, 400));
                }
                if (body.status !== 'done') {
                    throw new Error(body.message || body.status || 'Auto-mask batch failed');
                }
                const result = body.result || {};
                const summary = t(
                    `Auto-mask done: saved ${result.saved || 0}, skipped ${result.skipped || 0}, errors ${result.error_count || 0}`,
                    `自动遮罩完成：保存 ${result.saved || 0}，跳过 ${result.skipped || 0}，错误 ${result.error_count || 0}`,
                );
                if (state) state.textContent = summary;
                window.App?.showToast?.(summary, result.error_count ? 'warning' : 'success');
                if (this.dm?.activeId != null) this._refreshEntry(Number(this.dm.activeId));
            } catch (e) {
                window.App?.showToast?.(String(e.message || e), 'error');
            } finally {
                if (button) button.disabled = false;
            }
        },

        // ---- persistence ---------------------------------------------------------
        async save() {
            if (!this._imageId || !this._maskCanvas) return;
            const button = document.getElementById('mask-editor-save');
            if (button) button.disabled = true;
            try {
                const response = await fetch(`/api/masks/${this._imageId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data_url: this._maskCanvas.toDataURL('image/png') }),
                });
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
                window.App?.showToast?.(t('Training mask saved', '训练遮罩已保存'), 'success');
                this._refreshEntry(this._imageId);
                this.close();
            } catch (e) {
                this._status(t('Save failed: ', '保存失败：') + String(e.message || e));
            } finally {
                if (button) button.disabled = false;
            }
        },

        async deleteMask() {
            if (!this._imageId) return;
            try {
                const response = await fetch(`/api/masks/${this._imageId}`, { method: 'DELETE' });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                this._fillWhite();
                this._hasStoredMask = false;
                window.App?.showToast?.(t('Mask removed — the whole image is trained again',
                    '遮罩已移除 — 整张图恢复参与训练'), 'success');
                this._refreshEntry(this._imageId);
            } catch (e) {
                this._status(t('Remove failed: ', '移除失败：') + String(e.message || e));
            }
        },

        _status(message) {
            const el = document.getElementById('mask-editor-status');
            if (el) el.textContent = message || '';
        },
    };

    window.MaskEditor = MaskEditor;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => MaskEditor.init());
    } else {
        MaskEditor.init();
    }
})();
