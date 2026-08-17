/**
 * Tag completion stats modal (v3.2.2 T-power-PR2 / H).
 *
 * Pops once when /api/tag/progress reaches a terminal status (done /
 * cancelled / error) and the response carries ``last_run_stats``.
 *
 * Renders elapsed time, totals (processed / tagged / errors), avg
 * tags-per-image, and the top 10 tags by frequency.
 */
(function () {
    'use strict';

    const MODAL_ID = 'tag-stats-modal';
    const SHOWN_FLAG_ATTR = 'data-shown-for-run';
    let lastShownSignature = '';

    function $(id) { return document.getElementById(id); }
    function tT(key, fallback, params) {
        if (typeof window.appT === 'function') return window.appT(key, fallback, params);
        let s = String(fallback || key);
        if (params) {
            for (const k of Object.keys(params)) {
                s = s.replace(`{${k}}`, String(params[k]));
            }
        }
        return s;
    }

    function ensureModal() {
        let modal = document.getElementById(MODAL_ID);
        if (modal) return modal;
        modal = document.createElement('div');
        modal.id = MODAL_ID;
        modal.className = 'modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', `${MODAL_ID}-title`);
        modal.innerHTML = `
            <div class="modal-backdrop"></div>
            <div class="modal-content tag-stats-content">
                <button class="modal-close" id="${MODAL_ID}-close" aria-label="Close"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button>
                <h3 id="${MODAL_ID}-title" data-i18n="modal.tagStatsTitle">Tagging complete</h3>
                <div class="tag-stats-grid">
                    <div class="tag-stats-cell"><span class="tag-stats-num" id="${MODAL_ID}-processed">0</span><span class="tag-stats-label" data-i18n="modal.tagStatsProcessed">images processed</span></div>
                    <div class="tag-stats-cell"><span class="tag-stats-num" id="${MODAL_ID}-tagged">0</span><span class="tag-stats-label" data-i18n="modal.tagStatsTagged">tagged</span></div>
                    <div class="tag-stats-cell"><span class="tag-stats-num" id="${MODAL_ID}-errors">0</span><span class="tag-stats-label" data-i18n="modal.tagStatsErrors">errors</span></div>
                    <div class="tag-stats-cell"><span class="tag-stats-num" id="${MODAL_ID}-avg">0</span><span class="tag-stats-label" data-i18n="modal.tagStatsAvg">avg tags / image</span></div>
                    <div class="tag-stats-cell"><span class="tag-stats-num" id="${MODAL_ID}-elapsed">0s</span><span class="tag-stats-label" data-i18n="modal.tagStatsElapsed">elapsed</span></div>
                </div>
                <h4 class="tag-stats-section-title" data-i18n="modal.tagStatsTopTags">Top tags this run</h4>
                <ol class="tag-stats-top-list" id="${MODAL_ID}-top-list"></ol>
                <div class="tag-stats-actions">
                    <button class="btn btn-primary" id="${MODAL_ID}-ok" data-i18n="common.close">Close</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        $(`${MODAL_ID}-close`).addEventListener('click', hide);
        $(`${MODAL_ID}-ok`).addEventListener('click', hide);
        const backdrop = modal.querySelector('.modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', hide);

        return modal;
    }

    function formatElapsed(seconds) {
        const s = Math.max(0, Number(seconds) || 0);
        if (s < 60) return `${s.toFixed(0)}s`;
        if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
        return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    }

    function show(stats) {
        if (!stats) return;
        // Idempotency: only show ONE modal per run. Signature is
        // start_time + processed + tagged so two reloads of the same
        // run don't double-pop.
        const sig = `${stats.elapsed_seconds || 0}|${stats.total_processed || 0}|${stats.total_tagged || 0}`;
        if (sig === lastShownSignature) return;
        lastShownSignature = sig;

        const modal = ensureModal();
        $(`${MODAL_ID}-processed`).textContent = String(stats.total_processed || 0);
        $(`${MODAL_ID}-tagged`).textContent = String(stats.total_tagged || 0);
        $(`${MODAL_ID}-errors`).textContent = String(stats.total_errors || 0);
        $(`${MODAL_ID}-avg`).textContent = String((stats.avg_tags_per_image || 0).toFixed(1));
        $(`${MODAL_ID}-elapsed`).textContent = formatElapsed(stats.elapsed_seconds);

        const topList = $(`${MODAL_ID}-top-list`);
        topList.innerHTML = '';
        for (const item of (stats.top_tags || [])) {
            const li = document.createElement('li');
            const name = document.createElement('span');
            name.className = 'tag-stats-top-tag';
            name.textContent = String(item.tag || '');
            const count = document.createElement('span');
            count.className = 'tag-stats-top-count';
            count.textContent = String(item.count || 0);
            li.append(name, count);
            topList.appendChild(li);
        }
        if ((stats.top_tags || []).length === 0) {
            const li = document.createElement('li');
            li.className = 'tag-stats-empty';
            li.textContent = tT('modal.tagStatsNoTopTags', '(No tags written this run.)');
            topList.appendChild(li);
        }

        if (typeof window.showModal === 'function') {
            window.showModal(MODAL_ID);
        } else {
            modal.classList.add('visible');
        }
    }

    function hide() {
        if (typeof window.hideModal === 'function') {
            window.hideModal(MODAL_ID);
        } else {
            const modal = document.getElementById(MODAL_ID);
            if (modal) modal.classList.remove('visible');
        }
    }

    window.TagStatsModal = { show, hide };
})();
