/**
 * CLIP tools — two-image compare (point 6) + one-click near-duplicates (point 5).
 *
 * Thin frontend over the read-only similarity endpoints added for this feature:
 *   GET /api/similarity/compare?id_a&id_b   -> cosine similarity of two images
 *   GET /api/similarity/near/{image_id}     -> top-K closest images (ANN)
 *
 * Both surfaces are reachable from the gallery right-click menu:
 *   - 1 image  -> "Find near-duplicates (CLIP)"
 *   - 2 images -> "Compare 2 images (CLIP)"
 * The near-duplicates modal also lets you compare the query against any result.
 *
 * Self-contained: builds its own overlay + injects its style once, so it does
 * not depend on a predefined modal in index.html.
 */
(function () {
    'use strict';

    const t = (key, fallback, params) => {
        if (typeof window.appT === 'function') return window.appT(key, fallback, params);
        return fallback || key;
    };
    const toast = (msg, level = 'info', dur) => {
        if (typeof window.showToast === 'function') window.showToast(msg, level, dur);
    };
    const thumb = (id, size = 160) => `/api/image-thumbnail/${Number(id)}?size=${size}`;

    function injectStyleOnce() {
        if (document.getElementById('clip-tools-style')) return;
        const style = document.createElement('style');
        style.id = 'clip-tools-style';
        style.textContent = `
        .clip-tools-overlay{position:fixed;inset:0;z-index:9000;display:flex;align-items:center;
            justify-content:center;background:rgba(14, 14, 14, 0.62);}
        .clip-tools-modal{background:var(--bg-elevated,#212121);border:1px solid rgba(255,255,255,0.12);
            border-radius:14px;max-width:min(880px,92vw);max-height:88vh;overflow:auto;
            box-shadow:0 24px 64px rgba(0,0,0,0.5);padding:18px 20px;color:var(--text-primary,#FBF5EF);}
        .clip-tools-head{display:flex;align-items:center;gap:10px;margin-bottom:14px;}
        .clip-tools-head h3{margin:0;font-size:15px;font-weight:700;flex:1;}
        .clip-tools-close{appearance:none;border:none;background:rgba(255,255,255,0.08);
            color:var(--text-primary,#FBF5EF);width:28px;height:28px;border-radius:8px;cursor:pointer;font-size:15px;}
        .clip-tools-close:hover{background:rgba(255,255,255,0.16);}
        .clip-tools-compare{display:flex;align-items:center;gap:18px;justify-content:center;flex-wrap:wrap;}
        .clip-tools-pic{display:flex;flex-direction:column;align-items:center;gap:6px;max-width:300px;}
        .clip-tools-pic img{max-width:280px;max-height:300px;border-radius:10px;border:1px solid rgba(255,255,255,0.12);}
        .clip-tools-pic small{color:var(--text-secondary,#A6A6A6);font-size:11px;max-width:280px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
        .clip-tools-score{text-align:center;min-width:120px;}
        .clip-tools-score .num{font-size:34px;font-weight:800;line-height:1;}
        .clip-tools-score .verdict{font-size:12px;margin-top:6px;color:var(--text-secondary,#A6A6A6);}
        .clip-tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;}
        .clip-tools-card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);
            border-radius:10px;overflow:hidden;cursor:pointer;transition:border-color 120ms ease;}
        .clip-tools-card:hover{border-color:var(--accent-primary,#E2C099);}
        .clip-tools-card img{width:100%;height:130px;object-fit:cover;display:block;background:rgba(0,0,0,0.3);}
        .clip-tools-card .meta{padding:6px 8px;display:flex;align-items:center;justify-content:space-between;gap:6px;}
        .clip-tools-card .pct{font-weight:800;font-size:13px;color:var(--accent-primary,#E2C099);}
        .clip-tools-card .cmp{appearance:none;border:none;background:rgba(255,255,255,0.08);
            color:var(--text-secondary,#D6D6D6);border-radius:6px;font-size:10px;padding:2px 6px;cursor:pointer;}
        .clip-tools-card .cmp:hover{background:var(--accent-primary,#E2C099);color:#fff;}
        .clip-tools-note{color:var(--text-secondary,#A6A6A6);font-size:12px;margin:0 0 12px;}
        .clip-tools-empty{color:var(--text-secondary,#A6A6A6);text-align:center;padding:28px 8px;}
        `;
        document.head.appendChild(style);
    }

    function closeOverlay() {
        document.querySelector('.clip-tools-overlay')?.remove();
        document.removeEventListener('keydown', onEscKey);
    }
    function onEscKey(e) { if (e.key === 'Escape') closeOverlay(); }

    function openOverlay(title) {
        injectStyleOnce();
        closeOverlay();
        const overlay = document.createElement('div');
        overlay.className = 'clip-tools-overlay';
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay(); });

        const modal = document.createElement('div');
        modal.className = 'clip-tools-modal';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');

        const head = document.createElement('div');
        head.className = 'clip-tools-head';
        const h = document.createElement('h3');
        h.textContent = title;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'clip-tools-close';
        close.setAttribute('aria-label', t('common.close', 'Close'));
        close.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-close\"/></svg>";
        close.addEventListener('click', closeOverlay);
        head.append(h, close);

        const body = document.createElement('div');
        body.className = 'clip-tools-body';

        modal.append(head, body);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        document.addEventListener('keydown', onEscKey);
        return body;
    }

    function setLoading(body) {
        body.innerHTML = '';
        const p = document.createElement('div');
        p.className = 'clip-tools-empty';
        p.textContent = t('clipTools.loading', 'Computing CLIP similarity…');
        body.appendChild(p);
    }

    // Translate an HTTP error into a friendly, actionable message.
    async function explainError(response) {
        let detail = '';
        try { const j = await response.json(); detail = (j && (j.detail || j.message)) || ''; } catch (_e) { /* */ }
        if (response.status === 409) {
            return t('clipTools.notIndexed',
                'These images are not indexed yet. Open the Similar tab and run "Index images" first.');
        }
        if (response.status === 404) {
            return t('clipTools.notFound', 'Image not found.');
        }
        return String(detail || `HTTP ${response.status}`);
    }

    function verdictFor(score) {
        if (score >= 0.95) return t('clipTools.verdictDup', 'Near-duplicate');
        if (score >= 0.85) return t('clipTools.verdictVerySimilar', 'Very similar');
        if (score >= 0.6) return t('clipTools.verdictSimilar', 'Somewhat similar');
        if (score >= 0.45) return t('clipTools.verdictLoose', 'Loosely related');
        return t('clipTools.verdictDifferent', 'Different');
    }

    function scoreColor(score) {
        if (score >= 0.95) return '#f87171';        // red — duplicate
        if (score >= 0.85) return '#fbbf24';        // amber — very similar
        if (score >= 0.6) return 'var(--accent-primary,#E2C099)';
        return 'var(--text-secondary,#A6A6A6)';
    }

    function renderCompare(body, data) {
        body.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.className = 'clip-tools-compare';

        const picA = document.createElement('div');
        picA.className = 'clip-tools-pic';
        const imgA = document.createElement('img');
        imgA.src = thumb(data.id_a, 300); imgA.alt = '';
        const capA = document.createElement('small');
        capA.textContent = data.filename_a || `#${data.id_a}`;
        picA.append(imgA, capA);

        const scoreBox = document.createElement('div');
        scoreBox.className = 'clip-tools-score';
        const pct = Math.round((data.similarity || 0) * 100);
        const num = document.createElement('div');
        num.className = 'num';
        num.style.color = scoreColor(data.similarity || 0);
        num.textContent = `${pct}%`;
        const verdict = document.createElement('div');
        verdict.className = 'verdict';
        verdict.textContent = verdictFor(data.similarity || 0);
        scoreBox.append(num, verdict);

        const picB = document.createElement('div');
        picB.className = 'clip-tools-pic';
        const imgB = document.createElement('img');
        imgB.src = thumb(data.id_b, 300); imgB.alt = '';
        const capB = document.createElement('small');
        capB.textContent = data.filename_b || `#${data.id_b}`;
        picB.append(imgB, capB);

        wrap.append(picA, scoreBox, picB);
        body.appendChild(wrap);
    }

    function renderNear(body, queryId, data) {
        body.innerHTML = '';
        const note = document.createElement('p');
        note.className = 'clip-tools-note';
        note.textContent = t('clipTools.nearNote',
            'Closest matches by CLIP embedding (highest first). Click an image to preview, or "Compare" for the exact score.');
        body.appendChild(note);

        const results = (data && data.results) || [];
        if (!results.length) {
            const empty = document.createElement('div');
            empty.className = 'clip-tools-empty';
            empty.textContent = t('clipTools.nearEmpty', 'No other indexed images to compare against.');
            body.appendChild(empty);
            return;
        }

        const grid = document.createElement('div');
        grid.className = 'clip-tools-grid';
        for (const r of results) {
            const card = document.createElement('div');
            card.className = 'clip-tools-card';
            const img = document.createElement('img');
            img.loading = 'lazy'; img.alt = '';
            img.src = thumb(r.id, 200);
            const meta = document.createElement('div');
            meta.className = 'meta';
            const pct = document.createElement('span');
            pct.className = 'pct';
            pct.textContent = `${Math.round((r.similarity || 0) * 100)}%`;
            const cmp = document.createElement('button');
            cmp.type = 'button';
            cmp.className = 'cmp';
            cmp.textContent = t('clipTools.compareBtn', 'Compare');
            cmp.addEventListener('click', (e) => { e.stopPropagation(); ClipTools.compare(queryId, r.id); });
            meta.append(pct, cmp);
            card.append(img, meta);
            card.title = r.filename || `#${r.id}`;
            card.addEventListener('click', () => {
                const app = window.App || {};
                if (typeof app.openGalleryPreview === 'function') app.openGalleryPreview(r.id);
                else if (window.Gallery?.openPreview) window.Gallery.openPreview(r.id);
            });
            grid.appendChild(card);
        }
        body.appendChild(grid);
    }

    const ClipTools = {
        async compare(idA, idB) {
            const a = Number(idA);
            const b = Number(idB);
            if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0 || b <= 0 || a === b) {
                toast(t('clipTools.needTwo', 'Pick two different images to compare.'), 'warning');
                return;
            }
            const body = openOverlay(t('clipTools.compareTitle', 'CLIP similarity'));
            setLoading(body);
            try {
                const r = await (window.apiFetch || fetch)(`/api/similarity/compare?id_a=${a}&id_b=${b}`);
                if (!r.ok) {
                    body.innerHTML = '';
                    const e = document.createElement('div');
                    e.className = 'clip-tools-empty';
                    e.textContent = await explainError(r);
                    body.appendChild(e);
                    return;
                }
                renderCompare(body, await r.json());
            } catch (err) {
                body.innerHTML = '';
                const e = document.createElement('div');
                e.className = 'clip-tools-empty';
                e.textContent = (err && err.message) || String(err);
                body.appendChild(e);
            }
        },

        async near(imageId, limit = 24) {
            const id = Number(imageId);
            if (!Number.isFinite(id) || id <= 0) return;
            const body = openOverlay(t('clipTools.nearTitle', 'Near-duplicates'));
            setLoading(body);
            try {
                const r = await (window.apiFetch || fetch)(`/api/similarity/near/${id}?limit=${Number(limit) || 24}`);
                if (!r.ok) {
                    body.innerHTML = '';
                    const e = document.createElement('div');
                    e.className = 'clip-tools-empty';
                    e.textContent = await explainError(r);
                    body.appendChild(e);
                    return;
                }
                renderNear(body, id, await r.json());
            } catch (err) {
                body.innerHTML = '';
                const e = document.createElement('div');
                e.className = 'clip-tools-empty';
                e.textContent = (err && err.message) || String(err);
                body.appendChild(e);
            }
        },

        close: closeOverlay,
    };

    window.ClipTools = ClipTools;
})();
