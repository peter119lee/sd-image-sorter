/**
 * Character Purity (CCIP) — Dataset Maker health card (roadmap #9, v1).
 *
 * Backend: POST /api/dataset/character-purity (background job) + progress /
 * cancel / status / prepare. CCIP embeds every gallery image in the queue,
 * compares all pairs through a learned metrics graph, anchors the set on its
 * MEDOID (the most typical image) and ranks images by distance-to-medoid.
 * Rows above the threshold are suspected character outliers.
 *
 * ADVISORY ONLY by design: this card never moves, deletes, or edits images —
 * clicking a row only locates it in the Dataset Maker queue for human review.
 * (No move-to-folder helper exists in the dataset UI, so v1 deliberately
 * ships review-only actions instead of new file-move machinery.)
 *
 * Model caveats surfaced in the card copy: multi-character images confuse
 * CCIP, and chibi/style variance legitimately raises distances — which is
 * why the threshold is user-adjustable and re-flags client-side from the
 * stored distances without re-running inference.
 */
(function () {
    'use strict';

    const API_BASE = '/api/dataset/character-purity';
    const POLL_INTERVAL_MS = 800;
    const FALLBACK_THRESHOLD = 0.178;

    function t(en, zh) {
        try {
            const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
            return String(lang).toLowerCase().startsWith('zh') ? zh : en;
        } catch (_) { return en; }
    }

    function $(id) { return document.getElementById(id); }

    function formatMb(bytes) {
        return (Number(bytes || 0) / (1024 * 1024)).toFixed(1);
    }

    const CharacterPurity = {
        _pollTimer: null,
        _statusTimer: null,
        _jobId: null,
        _lastResult: null,
        _skippedLocal: 0,
        _defaultThreshold: FALLBACK_THRESHOLD,

        get dm() { return window.DatasetMaker || null; },

        init() {
            const card = $('dataset-character-purity');
            if (!card) return;
            this._ensureStyles();
            card.addEventListener('toggle', () => {
                if (card.open) this.refreshModelState();
            });
            $('ccip-run')?.addEventListener('click', () => this.run());
            $('ccip-cancel')?.addEventListener('click', () => this.cancel());
            $('ccip-threshold')?.addEventListener('input', () => this._renderResult());
        },

        _setStatus(text) {
            const el = $('ccip-status');
            if (el) el.textContent = text || '';
        },

        _galleryIds() {
            const dm = this.dm;
            if (!dm || !Array.isArray(dm.imageIds)) return { ids: [], skipped: 0 };
            const ids = dm.imageIds
                .filter((id) => !(dm.isLocalId && dm.isLocalId(id)))
                .map(Number)
                .filter((n) => Number.isFinite(n) && n > 0);
            return { ids, skipped: dm.imageIds.length - ids.length };
        },

        // ---- model availability / prepare ---------------------------------

        async refreshModelState() {
            try {
                const response = await fetch(`${API_BASE}/status`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const status = await response.json();
                this._defaultThreshold = Number(status.default_threshold) || FALLBACK_THRESHOLD;
                const thresholdInput = $('ccip-threshold');
                if (thresholdInput && !thresholdInput.value) {
                    thresholdInput.value = String(this._defaultThreshold);
                }
                this._renderModelState(status);
                return status;
            } catch (e) {
                this._setStatus(t('Could not read CCIP model status: ', '无法读取 CCIP 模型状态：') + String(e.message || e));
                return null;
            }
        },

        _renderModelState(status) {
            const prepareWrap = $('ccip-prepare-wrap');
            const runBtn = $('ccip-run');
            if (!prepareWrap || !runBtn) return;
            if (status.available) {
                prepareWrap.hidden = true;
                runBtn.disabled = false;
                if (this._statusTimer) { clearTimeout(this._statusTimer); this._statusTimer = null; }
                return;
            }
            runBtn.disabled = true;
            prepareWrap.hidden = false;
            const note = $('ccip-prepare-note');
            const btn = $('ccip-prepare');
            if (status.preparing) {
                if (btn) btn.disabled = true;
                const dl = status.download || {};
                if (note) {
                    note.textContent = dl.active
                        ? t(`Downloading ${dl.filename}... ${formatMb(dl.downloaded)} / ${dl.total ? formatMb(dl.total) : '?'} MB`,
                            `正在下载 ${dl.filename}... ${formatMb(dl.downloaded)} / ${dl.total ? formatMb(dl.total) : '?'} MB`)
                        : t('Preparing CCIP model...', '正在准备 CCIP 模型...');
                }
                // Keep polling until the download settles.
                if (this._statusTimer) clearTimeout(this._statusTimer);
                this._statusTimer = setTimeout(() => this.refreshModelState(), POLL_INTERVAL_MS);
            } else {
                if (btn) btn.disabled = false;
                if (note) {
                    note.textContent = status.prepare_error
                        ? t('Model download failed: ', '模型下载失败：') + status.prepare_error
                        : t('The CCIP model (~150 MB) is not downloaded yet.', 'CCIP 模型（约 150 MB）尚未下载。');
                }
            }
        },

        async prepare() {
            const btn = $('ccip-prepare');
            if (btn) btn.disabled = true;
            try {
                const response = await fetch(`${API_BASE}/prepare`, { method: 'POST' });
                if (!response.ok && response.status !== 409) {
                    const body = await response.json().catch(() => ({}));
                    throw new Error(body.error || `HTTP ${response.status}`);
                }
            } catch (e) {
                this._setStatus(t('Model download failed to start: ', '模型下载启动失败：') + String(e.message || e));
                if (btn) btn.disabled = false;
                return;
            }
            this.refreshModelState();
        },

        // ---- analysis job ---------------------------------------------------

        async run() {
            const { ids, skipped } = this._galleryIds();
            this._skippedLocal = skipped;
            if (ids.length < 2) {
                this._setStatus(t('Character purity needs at least 2 gallery images in the queue (local imports are not in the DB yet).',
                    '角色纯度分析至少需要队列中的 2 张图库图片（本地导入的图片尚未入库）。'));
                return;
            }
            const thresholdValue = parseFloat($('ccip-threshold')?.value || '');
            const body = { image_ids: ids };
            if (Number.isFinite(thresholdValue)) body.threshold = Math.min(1, Math.max(0, thresholdValue));

            const runBtn = $('ccip-run');
            if (runBtn) runBtn.disabled = true;
            this._setStatus(t('Starting character-purity analysis...', '正在启动角色纯度分析...'));
            try {
                const response = await fetch(API_BASE, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    this._setStatus(data.error || `HTTP ${response.status}`);
                    if (runBtn) runBtn.disabled = false;
                    // A 400 here usually means the model is missing — refresh
                    // the card so the prepare affordance shows up.
                    this.refreshModelState();
                    return;
                }
                this._jobId = data.job_id;
                const cancelBtn = $('ccip-cancel');
                if (cancelBtn) cancelBtn.hidden = false;
                this._schedulePoll();
            } catch (e) {
                this._setStatus(String(e.message || e));
                if (runBtn) runBtn.disabled = false;
            }
        },

        _schedulePoll() {
            if (this._pollTimer) clearTimeout(this._pollTimer);
            this._pollTimer = setTimeout(() => this._poll(), POLL_INTERVAL_MS);
        },

        async _poll() {
            if (!this._jobId) return;
            try {
                const response = await fetch(`${API_BASE}/progress?job_id=${encodeURIComponent(this._jobId)}`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const progress = await response.json();
                if (progress.status === 'done') {
                    this._finishJob();
                    this._lastResult = progress.result;
                    this._renderResult();
                    return;
                }
                if (progress.status === 'failed' || progress.status === 'cancelled') {
                    this._finishJob();
                    this._setStatus(progress.message || progress.status);
                    return;
                }
                this._setStatus(progress.message
                    || t(`Analyzing ${progress.current}/${progress.total}...`, `分析中 ${progress.current}/${progress.total}...`));
                this._schedulePoll();
            } catch (e) {
                this._finishJob();
                this._setStatus(t('Progress polling failed: ', '进度查询失败：') + String(e.message || e));
            }
        },

        _finishJob() {
            this._jobId = null;
            if (this._pollTimer) { clearTimeout(this._pollTimer); this._pollTimer = null; }
            const runBtn = $('ccip-run');
            if (runBtn) runBtn.disabled = false;
            const cancelBtn = $('ccip-cancel');
            if (cancelBtn) cancelBtn.hidden = true;
        },

        async cancel() {
            if (!this._jobId) return;
            try {
                await fetch(`${API_BASE}/cancel`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_id: this._jobId }),
                });
            } catch (_) { /* the poll loop reports the terminal state */ }
        },

        // ---- rendering ------------------------------------------------------

        _activeThreshold() {
            const parsed = parseFloat($('ccip-threshold')?.value || '');
            if (Number.isFinite(parsed)) return Math.min(1, Math.max(0, parsed));
            return Number(this._lastResult?.threshold) || this._defaultThreshold;
        },

        _renderResult() {
            const wrap = $('ccip-results');
            const result = this._lastResult;
            if (!wrap || !result) return;
            const threshold = this._activeThreshold();
            // Re-flag client-side from stored distances so the threshold input
            // adjusts the verdicts live, without re-running inference.
            const outliers = result.items.filter((item) => item.distance > threshold);
            const inlierCount = result.items.length - outliers.length;

            wrap.textContent = '';
            wrap.hidden = false;

            const summary = document.createElement('div');
            summary.className = 'ccip-summary';
            summary.textContent = outliers.length === 0
                ? t(`✅ ${result.extracted} images analyzed — all within threshold ${threshold}.`,
                    `✅ 已分析 ${result.extracted} 张图 — 全部在阈值 ${threshold} 以内。`)
                : t(`${result.extracted} images analyzed — ${outliers.length} suspected outlier(s) above threshold ${threshold}:`,
                    `已分析 ${result.extracted} 张图 — ${outliers.length} 张疑似离群（超过阈值 ${threshold}）：`);
            wrap.appendChild(summary);

            if (result.failed > 0 || this._skippedLocal > 0) {
                const note = document.createElement('div');
                note.className = 'ccip-note';
                const parts = [];
                if (result.failed > 0) {
                    parts.push(t(`${result.failed} unreadable/missing images skipped`, `跳过 ${result.failed} 张缺失/无法读取的图片`));
                }
                if (this._skippedLocal > 0) {
                    parts.push(t(`${this._skippedLocal} local-import images skipped — not in the DB`,
                        `跳过 ${this._skippedLocal} 张本地导入图片 — 尚未入库`));
                }
                note.textContent = `(${parts.join(' · ')})`;
                wrap.appendChild(note);
            }

            wrap.appendChild(this._buildRow(result.medoid_image_id, null, {
                label: t('Anchor — the most typical image of the set (medoid)', '锚点 — 全组最典型的一张图（medoid）'),
                medoid: true,
            }));

            for (const item of outliers) {
                wrap.appendChild(this._buildRow(item.image_id, item.distance, { outlier: true }));
            }

            const footer = document.createElement('div');
            footer.className = 'ccip-note';
            footer.textContent = t(
                `${inlierCount} image(s) within threshold. Advisory only — click a row to locate the image in the queue; nothing is moved or deleted.`,
                `${inlierCount} 张图在阈值以内。仅供参考 — 点击行可在队列中定位该图；不会移动或删除任何文件。`);
            wrap.appendChild(footer);

            this._setStatus('');
        },

        _buildRow(imageId, distance, { label = '', medoid = false, outlier = false } = {}) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'ccip-row' + (medoid ? ' ccip-row-medoid' : '') + (outlier ? ' ccip-row-outlier' : '');
            row.title = t('Locate this image in the Dataset Maker queue', '在数据集队列中定位这张图');

            const thumb = document.createElement('img');
            thumb.className = 'ccip-thumb';
            thumb.loading = 'lazy';
            thumb.alt = '';
            thumb.src = `/api/image-thumbnail/${encodeURIComponent(imageId)}?size=128`;
            row.appendChild(thumb);

            const text = document.createElement('span');
            text.className = 'ccip-row-text';
            text.textContent = label || `#${imageId}`;
            row.appendChild(text);

            if (distance != null) {
                const dist = document.createElement('span');
                dist.className = 'ccip-row-distance';
                dist.textContent = t(`distance ${distance.toFixed(3)}`, `距离 ${distance.toFixed(3)}`);
                row.appendChild(dist);
            }

            row.addEventListener('click', () => {
                this.dm?._setActive?.(imageId);
            });
            return row;
        },

        _ensureStyles() {
            if (document.getElementById('ccip-card-styles')) return;
            const style = document.createElement('style');
            style.id = 'ccip-card-styles';
            style.textContent = `
                /* [hidden] guard: flex/grid display rules would otherwise win. */
                #dataset-character-purity [hidden] { display: none !important; }
                .ccip-toolbar {
                    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
                    margin: 6px 0;
                }
                .ccip-threshold-label {
                    display: inline-flex; align-items: center; gap: 6px;
                    font-size: 12px; opacity: 0.9;
                }
                /* 88px: 76px clipped "0.178" once the number-input spinner
                   ate its ~20px (seen in the 1920x1080 zh-CN screenshot). */
                .ccip-threshold-label input { width: 88px; }
                .ccip-results { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
                .ccip-summary { font-size: 13px; }
                .ccip-note { font-size: 12px; opacity: 0.75; }
                .ccip-row {
                    display: flex; align-items: center; gap: 10px;
                    padding: 4px 8px; text-align: left; cursor: pointer;
                    background: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px; color: inherit; font: inherit;
                }
                .ccip-row:hover { background: rgba(255, 255, 255, 0.09); }
                .ccip-row:focus-visible { outline: 2px solid rgba(var(--accent-rgb), 0.7); outline-offset: 1px; }
                .ccip-row-medoid { border-color: rgba(var(--success-rgb), 0.55); }
                .ccip-row-outlier { border-color: rgba(var(--accent-rgb), 0.55); }
                .ccip-thumb {
                    width: 48px; height: 48px; object-fit: cover;
                    border-radius: 6px; flex: 0 0 auto; background: rgba(0, 0, 0, 0.25);
                }
                .ccip-row-text { flex: 1 1 auto; font-size: 12px; min-width: 0; }
                .ccip-row-distance {
                    font-size: 12px; font-variant-numeric: tabular-nums;
                    opacity: 0.85; flex: 0 0 auto;
                }
            `;
            document.head.appendChild(style);
        },
    };

    // The prepare button lives inside the card markup; bind after init so a
    // missing card (non-dataset views) stays a no-op.
    function boot() {
        CharacterPurity.init();
        document.getElementById('ccip-prepare')?.addEventListener('click', () => CharacterPurity.prepare());
    }

    window.CharacterPurity = CharacterPurity;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
