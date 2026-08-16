/**
 * Entry page — v4.0 Aurora shell, Phase 2 (spec: canvas #11a + #12a flow map).
 *
 * Behaviors:
 * - Shown at launch unless the 跳过入口页 setting is on (localStorage).
 * - Pure overlay: the app underneath stays mounted, so returning to the entry
 *   via ESC never loses view state ("Esc 永远回上一级且不丢进度").
 * - Every tile is a shortcut into an existing view (missions are shortcuts,
 *   never cages); navigation reuses the nav-tab click bindings.
 * - Cover display modes (owner FB 2026-07-06, replacing the one-way
 *   不想展示): off / single (换一张) / slideshow / rolling film strips.
 */
(function () {
    'use strict';

    const SKIP_KEY = 'aurora-entry-skip';
    const HERO_OFF_KEY = 'aurora-entry-hero-off'; // legacy flag, kept in sync for the settings toggle
    const HERO_MODE_KEY = 'aurora-entry-hero-mode';
    const HERO_SEED_KEY = 'aurora-entry-hero-seed';
    const LAST_SEEN_KEY = 'aurora-entry-last-seen';
    // Owned by gallery/comfort.js (STORAGE_KEY there); read-only here so the
    // entry page can advertise a resumable browse position. Keep these three
    // in sync with comfort.js if its thresholds change.
    const COMFORT_STATE_KEY = 'sd-gallery-comfort-v1';
    const COMFORT_RESUME_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
    const COMFORT_RESUME_MIN_SCROLL = 80;

    const HERO_MODES = ['off', 'single', 'slideshow', 'film'];
    const SLIDESHOW_INTERVAL_MS = 8000;
    const FILM_ROWS = 4;
    const PROMPT_TEXTURE_MAX_CHARS = 90;
    const HERO_LOAD_ATTEMPTS = 8;
    const CLOCK_TICK_MS = 30 * 1000;

    const state = {
        visible: false,
        summary: null,
        clockTimer: null,
        heroPool: null,
        slideshowTimer: null,
        slideshowIndex: 0,
        activeSlideLayer: 0,
        // Bumped on every renderHero so a probe still in flight from the
        // previous mode cannot paint over the one the user just chose.
        heroToken: 0,
    };

    function t(key, params, fallback) {
        const value = window.I18n && window.I18n.t ? window.I18n.t(key, params) : null;
        if (value && value !== key) return value;
        let text = fallback || key;
        Object.entries(params || {}).forEach(([name, val]) => {
            text = text.replace(new RegExp('\\{' + name + '\\}', 'g'), String(val));
        });
        return text;
    }

    async function api(path) {
        try {
            const response = await (window.apiFetch || fetch)(path, {
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) return null;
            return await response.json();
        } catch (error) {
            if (window.Logger) Logger.warn('Entry fetch failed:', path, error);
            return null;
        }
    }

    function el(id) {
        return document.getElementById(id);
    }

    function isSkipped() {
        try {
            if (new URLSearchParams(window.location.search).has('skip-entry')) return true;
            return localStorage.getItem(SKIP_KEY) === '1';
        } catch (error) {
            return true;
        }
    }

    function heroMode() {
        try {
            const stored = localStorage.getItem(HERO_MODE_KEY);
            if (HERO_MODES.includes(stored)) return stored;
            // Legacy migration: the old one-way 不想展示 flag.
            return localStorage.getItem(HERO_OFF_KEY) === '1' ? 'off' : 'single';
        } catch (e) {
            return 'single';
        }
    }

    function setHeroMode(mode) {
        const next = HERO_MODES.includes(mode) ? mode : 'single';
        try {
            localStorage.setItem(HERO_MODE_KEY, next);
            // Keep the legacy flag in sync so the settings toggle and any
            // older readers agree with the switch.
            localStorage.setItem(HERO_OFF_KEY, next === 'off' ? '1' : '0');
        } catch (e) { /* ignore */ }
        refreshSettingsButtons();
        renderHero(state.summary ? state.summary.hero : null);
    }

    function isHeroOff() {
        return heroMode() === 'off';
    }

    function heroSeed() {
        try { return Math.max(0, parseInt(localStorage.getItem(HERO_SEED_KEY), 10) || 0); } catch (e) { return 0; }
    }

    function createRenderRequests() {
        let lastSeen = null;
        try { lastSeen = localStorage.getItem(LAST_SEEN_KEY); } catch (e) { /* ignore */ }
        const query = new URLSearchParams();
        if (lastSeen) query.set('last_seen', lastSeen);
        query.set('hero_seed', String(heroSeed()));
        return [
            api(`/api/entry/summary?${query.toString()}`),
            api('/api/sort/current'),
        ];
    }

    let initialRenderRequests = isSkipped() ? null : createRenderRequests();

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------

    // --- hero display modes -------------------------------------------

    function stopSlideshow() {
        if (state.slideshowTimer) {
            clearInterval(state.slideshowTimer);
            state.slideshowTimer = null;
        }
    }

    function clearHeroLayers() {
        const bg = el('entry-hero');
        if (bg) bg.textContent = '';
        state.activeSlideLayer = 0;
    }

    function ensureSlideLayers(bg) {
        let layers = bg.querySelectorAll('.hero-slide');
        if (layers.length !== 2) {
            bg.textContent = '';
            for (let i = 0; i < 2; i += 1) {
                const layer = document.createElement('div');
                layer.className = 'hero-slide';
                bg.appendChild(layer);
            }
            layers = bg.querySelectorAll('.hero-slide');
            state.activeSlideLayer = 0;
        }
        return layers;
    }

    function decodes(imageId) {
        return new Promise((resolve) => {
            const probe = new Image();
            probe.onload = () => resolve(true);
            probe.onerror = () => resolve(false);
            probe.src = `/api/image-file/${imageId}`;
        });
    }

    // A row can be indexed while readable and then deleted from disk. Painting
    // its url straight into the layer leaves the cover black with nothing to
    // explain it, so a candidate is only swapped in after the bytes decode.
    async function showSlide(imageId, token) {
        if (!(await decodes(imageId))) return false;
        if (token !== undefined && token !== state.heroToken) return false;
        const bg = el('entry-hero');
        if (!bg) return false;
        const layers = ensureSlideLayers(bg);
        const nextIndex = state.activeSlideLayer ^ 1;
        layers[nextIndex].style.setProperty('--hero-url', `url("/api/image-file/${imageId}")`);
        layers[state.activeSlideLayer].classList.remove('active');
        layers[nextIndex].classList.add('active');
        state.activeSlideLayer = nextIndex;
        return true;
    }

    // Walks the pool from startIndex until one candidate loads, so a handful of
    // stale rows cannot strand the cover on black. Returns the id shown, or null.
    async function showFirstLoadable(ids, startIndex, token) {
        const attempts = Math.min(ids.length, HERO_LOAD_ATTEMPTS);
        for (let n = 0; n < attempts; n += 1) {
            if (token !== undefined && token !== state.heroToken) return null;
            const id = ids[(startIndex + n) % ids.length];
            if (await showSlide(id, token)) return id;
        }
        return null;
    }

    function heroUnavailableCredit(credit) {
        if (credit) credit.textContent = t('entry.heroUnavailable', {}, 'Cover images are missing from disk — rescan to refresh the library');
    }

    async function ensureHeroPool() {
        if (state.heroPool) return state.heroPool;
        const pool = await api('/api/entry/hero-pool?limit=60');
        state.heroPool = (pool && Array.isArray(pool.ids)) ? pool : { ids: [], starred: 0, total: 0 };
        return state.heroPool;
    }

    function renderPromptTexture(imageId) {
        const texture = el('entry-prompt-texture');
        if (!texture) return;
        if (!imageId) {
            texture.textContent = '';
            return;
        }
        api(`/api/images/${imageId}`).then((image) => {
            if (!texture || !state.visible) return;
            const prompt = (image && image.prompt) ? String(image.prompt) : '';
            texture.textContent = prompt ? prompt.slice(0, PROMPT_TEXTURE_MAX_CHARS) : '';
        });
    }

    function poolEmptyCredit(credit) {
        // An empty pool means an empty library — "rate ★5" would be wrong
        // advice here; the actual next step is scanning a folder.
        if (credit) credit.textContent = t('entry.heroPoolEmpty', {}, 'The library is empty — scan a folder and art shows up here');
    }

    function renderSlideshow(token) {
        ensureHeroPool().then((pool) => {
            if (token !== state.heroToken || !state.visible) return;
            const credit = el('entry-hero-credit');
            if (!pool.ids.length) {
                poolEmptyCredit(credit);
                return;
            }
            const advance = () => {
                const start = state.slideshowIndex % pool.ids.length;
                state.slideshowIndex += 1;
                showFirstLoadable(pool.ids, start, token).then((shown) => {
                    if (shown === null && token === state.heroToken && state.visible) {
                        heroUnavailableCredit(credit);
                    }
                });
            };
            advance();
            stopSlideshow();
            state.slideshowTimer = setInterval(advance, SLIDESHOW_INTERVAL_MS);
            if (credit) {
                credit.textContent = t('entry.heroSlideshow', { total: pool.ids.length }, 'Slideshow · {total} images');
            }
        });
    }

    function renderFilm() {
        ensureHeroPool().then((pool) => {
            if (heroMode() !== 'film' || !state.visible) return;
            const bg = el('entry-hero');
            const credit = el('entry-hero-credit');
            if (!bg) return;
            if (!pool.ids.length) {
                poolEmptyCredit(credit);
                return;
            }
            clearHeroLayers();
            const wall = document.createElement('div');
            wall.className = 'hero-film';
            const perRow = Math.max(6, Math.ceil(pool.ids.length / FILM_ROWS));
            for (let row = 0; row < FILM_ROWS; row += 1) {
                const strip = document.createElement('div');
                strip.className = `film-strip${row % 2 ? ' film-strip-rev' : ''}`;
                strip.style.setProperty('--film-duration', `${92 + row * 17}s`);
                const track = document.createElement('div');
                track.className = 'film-track';
                const slice = [];
                for (let n = 0; n < perRow; n += 1) {
                    slice.push(pool.ids[(row * perRow + n) % pool.ids.length]);
                }
                // Content is doubled so the -50% translate loops seamlessly.
                slice.concat(slice).forEach((id) => {
                    const img = document.createElement('img');
                    img.loading = 'lazy';
                    img.decoding = 'async';
                    img.alt = '';
                    // Both copies of an id fail together, so dropping them
                    // keeps the doubled track symmetric and the loop seamless.
                    img.addEventListener('error', () => img.remove());
                    img.src = `/api/image-thumbnail/${id}`;
                    track.appendChild(img);
                });
                strip.appendChild(track);
                wall.appendChild(strip);
            }
            bg.appendChild(wall);
            if (credit) {
                credit.textContent = t('entry.heroFilm', { total: pool.ids.length }, 'Film strips · {total} images');
            }
        });
    }

    function refreshModeSwitch() {
        const mode = heroMode();
        document.querySelectorAll('#entry-hero-mode-switch .hero-mode-btn').forEach((button) => {
            const active = button.dataset.mode === mode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    }

    function renderHero(hero) {
        const bg = el('entry-hero');
        const credit = el('entry-hero-credit');
        const swap = el('entry-hero-swap');
        if (!bg || !credit) return;

        const mode = heroMode();
        const token = (state.heroToken += 1);
        refreshModeSwitch();
        stopSlideshow();
        state.slideshowIndex = 0;
        swap.hidden = true;
        renderPromptTexture(null);

        if (mode === 'off') {
            clearHeroLayers();
            credit.textContent = t('entry.heroModeOff', {}, 'Cover off');
            return;
        }

        if (mode === 'slideshow') {
            ensureSlideLayers(bg);
            renderSlideshow(token);
            return;
        }

        if (mode === 'film') {
            renderFilm();
            return;
        }

        // single (default)
        if (!hero) {
            renderSingleFallback(credit, swap, token);
            return;
        }
        showSlide(hero.id, token).then((shown) => {
            if (token !== state.heroToken || !state.visible) return;
            if (!shown) {
                // Indexed as readable but gone from disk since; the pool walk
                // below still has readable candidates to offer.
                renderSingleFallback(credit, swap, token);
                return;
            }
            credit.textContent = t('entry.heroCredit', { filename: hero.filename }, "Today's cover: {filename} · ★5");
            swap.hidden = hero.pool <= 1;
            renderPromptTexture(hero.id);
        });
    }

    // No ★5 image yet (fresh install / unrated library): fall back to the
    // hero pool (newest works) instead of a blank half-canvas, and keep 换一张
    // usable by walking the pool. slideshowIndex doubles as the cursor.
    function renderSingleFallback(credit, swap, token) {
        ensureHeroPool().then((pool) => {
            if (token !== state.heroToken || !state.visible) return;
            if (!pool.ids.length) {
                clearHeroLayers();
                poolEmptyCredit(credit);
                return;
            }
            const start = state.slideshowIndex % pool.ids.length;
            return showFirstLoadable(pool.ids, start, token).then((shown) => {
                if (token !== state.heroToken || !state.visible) return;
                if (shown === null) {
                    clearHeroLayers();
                    heroUnavailableCredit(credit);
                    return;
                }
                credit.textContent = t('entry.heroLatestFallback', {}, 'Latest works — rate one ★5 to make it the cover');
                swap.hidden = pool.ids.length <= 1;
                renderPromptTexture(shown);
            });
        });
    }

    function renderSummary(summary) {
        if (!summary) return;
        state.summary = summary;

        const galleryCount = el('entry-count-gallery');
        if (galleryCount) galleryCount.textContent = String(summary.library_total ?? '');
        const gallerySub = el('entry-sub-gallery');
        if (gallerySub) {
            // JS-owned: strip the data-i18n default so applyToDOM can't reset it.
            gallerySub.removeAttribute('data-i18n');
            const added = Number(summary.added_today || 0);
            const unviewed = Number(summary.unviewed || 0);
            const total = Number(summary.library_total || 0);
            if (total === 0) {
                // Empty library: the Library is the biggest button, so make it
                // carry the first real step (scan) instead of a browse blurb.
                gallerySub.textContent = t('entry.tileGalleryScanCta', {}, 'Scan a folder to get started →');
                gallerySub.classList.add('is-cta');
            } else {
                gallerySub.classList.remove('is-cta');
                gallerySub.textContent = (added > 0 || unviewed > 0)
                    ? t('entry.tileGallerySub', { added, unviewed }, '{added} new today · {unviewed} not seen yet')
                    : t('entry.tileGallerySubQuiet', {}, 'Browse, filter, and pick images');
            }
        }
        // Keep the long-lived library name on the entry home card in sync.
        if (window.LibraryWorkspace && typeof window.LibraryWorkspace.refreshEntryHome === 'function') {
            window.LibraryWorkspace.refreshEntryHome();
        }

        const streakNum = el('entry-streak-num');
        if (streakNum) streakNum.textContent = String(summary.streak_days || 0);
        const streakToday = el('entry-streak-today');
        if (streakToday) {
            const touched = Number(summary.today_touched || 0);
            streakToday.textContent = touched > 0
                ? t('entry.todayTouched', { count: touched }, '· {count} images handled today')
                : '';
        }

        // Owner FB-2: greeting + stat row fills the art half of the canvas.
        const statTotal = el('entry-stat-total');
        if (statTotal) statTotal.textContent = String(summary.library_total ?? 0);
        const statAdded = el('entry-stat-added');
        if (statAdded) statAdded.textContent = String(summary.added_today || 0);
        const statTouched = el('entry-stat-touched');
        if (statTouched) statTouched.textContent = String(summary.today_touched || 0);

        renderHero(summary.hero);
    }

    function renderSortSession(session) {
        const anchor = el('entry-anchor');
        const organizeTile = el('entry-mission-organize');
        const sortCount = el('entry-count-sort');
        const sortSub = el('entry-sub-sort');
        const resumable = Boolean(
            session && !session.done && (session.image || session.champion)
        );

        if (sortCount) sortCount.textContent = resumable ? String(session.remaining ?? '') : '';
        if (sortSub) {
            sortSub.textContent = resumable
                ? t('entry.tileSortSub', {}, 'Left from last time · pick it back up')
                : t('entry.tileSortIdle', {}, 'Sort a batch with WASD');
        }

        if (!anchor) return;
        if (!resumable) {
            anchor.hidden = true;
            if (organizeTile) organizeTile.hidden = false;
            renderBrowseResume();
            return;
        }

        // The anchor absorbs its parent mission's tile (批量整理 hosts manual sort).
        anchor.hidden = false;
        if (organizeTile) organizeTile.hidden = true;
        renderBrowseResume();

        const total = Number(session.total || 0);
        const remaining = Number(session.remaining || 0);
        const done = Math.max(0, total - remaining);
        el('entry-anchor-title').textContent = t('entry.continueSortTitle', {}, 'Manual Sort');
        el('entry-anchor-detail').textContent = t('entry.continueSortDetail', { remaining }, '{remaining} images left');
        el('entry-anchor-count').textContent = `${done} / ${total}`;
        el('entry-anchor-fill').style.width = total > 0 ? `${Math.round((done / total) * 100)}%` : '0%';
        el('entry-anchor-when').textContent = '';
    }

    /**
     * Human "how long ago" for the resume slab. Deliberately coarse: the point
     * is "is this still my session?", not a timestamp.
     */
    function formatAgo(ms) {
        const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
        if (mins < 2) return t('entry.agoJustNow', {}, 'just now');
        if (mins < 60) return t('entry.agoMinutes', { n: mins }, '{n} min ago');
        const hours = Math.round(mins / 60);
        if (hours < 24) return t('entry.agoHours', { n: hours }, '{n} h ago');
        const days = Math.round(hours / 24);
        return t('entry.agoDays', { n: days }, '{n} d ago');
    }

    /**
     * Surface the browse position gallery/comfort.js already stores, so the
     * library picks up instead of restarting. comfort.js owns the actual scroll
     * restore on load; this slab only advertises it and routes there.
     *
     * Reads localStorage directly rather than through window.GalleryComfort:
     * index.html loads entry-page.js BEFORE gallery/comfort.js, so that global
     * does not exist yet on first render. The key is shared, not duplicated
     * state — comfort.js stays the only writer.
     */
    function readComfortResume() {
        try {
            const raw = localStorage.getItem(COMFORT_STATE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            const resume = parsed && typeof parsed === 'object' ? parsed.resume : null;
            return resume && typeof resume === 'object' ? resume : null;
        } catch (_e) {
            return null;
        }
    }

    function renderBrowseResume() {
        const slab = el('entry-resume');
        if (!slab) return;
        const resume = readComfortResume();

        // Same bar comfort.js uses to decide a restore is worth doing: a real
        // saved offset inside the 14-day window. Anything else is noise.
        const savedAt = Number(resume && resume.savedAt) || 0;
        const scrollTop = Number(resume && resume.scrollTop) || 0;
        const fresh = savedAt > 0 && (Date.now() - savedAt) < COMFORT_RESUME_MAX_AGE_MS;
        if (!fresh || scrollTop < COMFORT_RESUME_MIN_SCROLL) {
            slab.hidden = true;
            return;
        }

        const seen = Number(resume.imageCount) || 0;
        el('entry-resume-when').textContent = formatAgo(savedAt);
        el('entry-resume-detail').textContent = seen > 0
            ? t('entry.resumeDetail', { n: seen }, 'you had scrolled through {n} images')
            : '';
        slab.hidden = false;
    }

    function formatBytes(bytes) {
        const size = Number(bytes || 0);
        if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(1)}G`;
        return `${Math.round(size / (1024 * 1024))}M`;
    }

    function renderClock() {
        const clock = el('entry-sys-clock');
        if (!clock) return;
        const now = new Date();
        clock.textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    }

    function renderSystemLine() {
        renderClock();

        api('/api/tag/progress').then((progress) => {
            const slot = el('entry-sys-tagging');
            if (!slot) return;
            const running = progress && (progress.status === 'running' || progress.status === 'starting');
            if (running && Number(progress.total) > 0) {
                const percent = Math.round((Number(progress.current || 0) / Number(progress.total)) * 100);
                slot.textContent = '';
                const bar = document.createElement('span');
                bar.className = 'sys-accent';
                bar.textContent = '▍';
                slot.appendChild(bar);
                slot.appendChild(document.createTextNode(
                    t('entry.sysTagging', { percent }, 'Tagging {percent}%')
                ));
                slot.hidden = false;
            } else {
                slot.hidden = true;
            }
        });

        api('/api/disk/cache-status').then((status) => {
            const slot = el('entry-sys-cache');
            if (!slot) return;
            // /api/disk/cache-status has no top-level total_bytes; it returns a
            // safe_to_clean[] list (each with size_bytes) plus a thumbnail_cache
            // block. Sum the cleanable caches (with the thumbnail cache as a
            // fallback) so the stat actually renders. Null endpoint → stay hidden.
            const cleanable = Array.isArray(status?.safe_to_clean)
                ? status.safe_to_clean.reduce((sum, e) => sum + (Number(e?.size_bytes) || 0), 0)
                : null;
            const total = cleanable != null ? cleanable : (status?.thumbnail_cache?.total_size_bytes ?? null);
            if (total != null) {
                slot.textContent = t('entry.sysCache', { size: formatBytes(total) }, 'Cache {size}');
                slot.hidden = false;
            }
        });

        api('/api/system-info').then((info) => {
            const slot = el('entry-sys-vram');
            if (!slot || !info) return;
            const total = Number(info.gpu_vram_total_mb || 0);
            const available = Number(info.gpu_vram_available_mb || 0);
            if (total > 0 && available >= 0) {
                const used = ((total - available) / 1024).toFixed(1);
                const cap = (total / 1024).toFixed(1);
                slot.textContent = t('entry.sysVram', { used, total: cap }, 'VRAM {used}/{total}');
                slot.hidden = false;
            }
        });
    }

    function renderModelCenter() {
        api('/api/models/status').then((status) => {
            const tile = el('entry-fn-models');
            if (!tile || !status || !Array.isArray(status.models) || !status.models.length) return;
            const models = status.models;
            const ready = models.filter((model) => (model.status || '') === 'ready').length;
            const count = el('entry-count-models');
            if (count) count.textContent = `${ready}/${models.length}`;

            // "Start here" while the core booru tagger is missing — without
            // WD14 every mission's first step (tagging) is a dead end.
            const wd14 = models.find((model) => model.id === 'wd14');
            const coreMissing = Boolean(wd14 && wd14.status !== 'ready');
            tile.classList.toggle('entry-fn-models-attention', coreMissing);
            const badge = el('entry-models-badge');
            if (badge) badge.hidden = !coreMissing;
            const sub = el('entry-sub-models');
            if (sub) {
                sub.textContent = coreMissing
                    ? t('entry.tileModelsMissing', {}, 'The core tagger is not installed — download it here first')
                    : t('entry.tileModelsSub', {}, 'Download & manage the AI models');
            }
        });
    }

    async function render() {
        const versionEl = el('entry-version');
        const brandVersion = document.getElementById('brand-version');
        if (versionEl && brandVersion) versionEl.textContent = brandVersion.textContent || '';

        renderSystemLine();
        renderModelCenter();

        const requests = initialRenderRequests || createRenderRequests();
        initialRenderRequests = null;
        const [summary, session] = await Promise.all(requests);
        renderSummary(summary);
        renderSortSession(session);
    }

    // ------------------------------------------------------------------
    // Visibility & navigation
    // ------------------------------------------------------------------

    function show() {
        const page = el('entry-page');
        if (!page) return;
        page.hidden = false;
        document.body.classList.add('entry-active');
        state.visible = true;
        render();
        renderClock();
        if (!state.clockTimer) state.clockTimer = setInterval(renderClock, CLOCK_TICK_MS);
    }

    function hide() {
        const page = el('entry-page');
        if (!page) return;
        page.hidden = true;
        document.body.classList.remove('entry-active');
        state.visible = false;
        stopSlideshow();
        if (state.clockTimer) {
            clearInterval(state.clockTimer);
            state.clockTimer = null;
        }
    }

    function navigate(view) {
        if (view === 'gallery' && state.summary && state.summary.server_now) {
            // The user is about to lay eyes on the library: advance the
            // "还没看过" watermark.
            try { localStorage.setItem(LAST_SEEN_KEY, state.summary.server_now); } catch (e) { /* ignore */ }
        }
        // Comfort-2: carry today's cover into the gallery stage so Entry →
        // Library doesn't feel like a hard cut into a blank shell.
        if (view === 'gallery') {
            const heroId = state.summary?.hero?.id
                || state.heroPool?.ids?.[state.slideshowIndex || 0]
                || state.heroPool?.ids?.[0]
                || null;
            if (heroId != null && window.GalleryComfort?.stashHeroId) {
                window.GalleryComfort.stashHeroId(heroId);
            } else if (heroId != null) {
                try { localStorage.setItem('sd-gallery-comfort-hero-id', String(heroId)); } catch (e) { /* ignore */ }
            }
        }
        hide();
        const tab = document.getElementById(`nav-tab-${view}`);
        if (tab) tab.click();
        if (view === 'gallery' && window.GalleryComfort?.applyEntryHeroContinuity) {
            try { window.GalleryComfort.applyEntryHeroContinuity(); } catch (e) { /* ignore */ }
        }
        // Entry → Library reuses the already-mounted gallery, so comfort.js sees
        // neither a fresh load nor its switchView wrapper and the saved scroll
        // position was never reapplied. Ask for it explicitly.
        if (view === 'gallery' && window.GalleryComfort?.restoreSoon) {
            try { window.GalleryComfort.restoreSoon(); } catch (e) { /* ignore */ }
        }
    }

    // ------------------------------------------------------------------
    // Settings toggles (跳过入口页 / ★5 门面)
    // ------------------------------------------------------------------

    function refreshSettingsButtons() {
        const entryBtn = el('btn-settings-entry-toggle');
        if (entryBtn) {
            const shown = !((localStorage.getItem(SKIP_KEY) || '') === '1');
            entryBtn.setAttribute('aria-pressed', String(shown));
            const label = el('settings-entry-label');
            if (label) {
                label.textContent = shown
                    ? t('settings.entryOn', {}, 'Shown')
                    : t('settings.entryOff', {}, 'Skipped');
            }
        }
        const heroBtn = el('btn-settings-entry-hero-toggle');
        if (heroBtn) {
            const shown = !isHeroOff();
            heroBtn.setAttribute('aria-pressed', String(shown));
            const label = el('settings-entry-hero-label');
            if (label) {
                label.textContent = shown
                    ? t('settings.entryHeroOn', {}, 'Shown')
                    : t('settings.entryHeroOff', {}, 'Hidden');
            }
        }
    }

    function wireSettings() {
        const entryBtn = el('btn-settings-entry-toggle');
        if (entryBtn) {
            entryBtn.addEventListener('click', () => {
                const skipped = (localStorage.getItem(SKIP_KEY) || '') === '1';
                try { localStorage.setItem(SKIP_KEY, skipped ? '0' : '1'); } catch (e) { /* ignore */ }
                refreshSettingsButtons();
            });
        }
        const heroBtn = el('btn-settings-entry-hero-toggle');
        if (heroBtn) {
            heroBtn.addEventListener('click', () => {
                // The settings toggle is the coarse on/off; the entry page's
                // mode switch picks between the "on" variants.
                setHeroMode(isHeroOff() ? 'single' : 'off');
            });
        }
        refreshSettingsButtons();
    }

    // ------------------------------------------------------------------
    // ESC-to-entry ("任意步骤 ESC 回入口，进度自动保存" — #12a)
    // ------------------------------------------------------------------

    const OVERLAY_SELECTOR = [
        '.modal.visible',
        '.dataset-modal:not([hidden])',
        '.image-workspace.visible',
        '#onboarding-overlay:not([hidden])',
        '.guide-overlay.visible',
        '.update-popup.visible',
        // Open dropdowns and gallery selection mode own ESC too. Their own
        // handlers close/clear on the same keypress; checking the DOM here is
        // registration-order safe where relying on stopPropagation between
        // same-node capture listeners is not.
        '#gallery-action-more-menu:not([hidden])',
        '#nav-tools-menu:not([hidden])',
        '#gallery-search-suggest:not([hidden])',
        '.caption-autocomplete-dropdown:not([hidden])',
        '#btn-toggle-select[data-state="selecting"]',
    ].join(', ');

    function editingTarget(target) {
        if (!target) return false;
        const tag = (target.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
    }

    function onKeydownCapture(event) {
        if (event.key !== 'Escape' || event.defaultPrevented) return;
        if (state.visible || isSkipped()) return;
        if (editingTarget(document.activeElement)) return;
        // Any visible modal / overlay owns this ESC (checked in capture phase,
        // BEFORE its own handler closes it — otherwise we would misread the
        // post-close state and jump home on the same keypress).
        if (document.querySelector(OVERLAY_SELECTOR)) return;
        show();
    }

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------

    function wire() {
        // Mission tiles scope the top bar to their pipeline (nav-missions.js);
        // plain tool tiles are free exploration and clear any active mission.
        const enterMission = (key) => {
            if (window.NavMissions) window.NavMissions.enter(key);
        };
        const freeNavigate = (view) => {
            if (window.NavMissions) window.NavMissions.exit();
            navigate(view);
        };
        const clicks = {
            'entry-mission-lora': () => {
                enterMission('lora');
                navigate('dataset');
            },
            // v3.5.0: the Pixiv mission tile routes through the batch-bar
            // button so it inherits its guard — with a selection it opens the
            // publish-set workbench with those ids; with none it shows the
            // "pick images first" toast instead of an empty modal blocking
            // the very gallery the user needs to pick from.
            'entry-mission-pixiv': () => {
                enterMission('pixiv');
                navigate('gallery');
                const publishButton = document.getElementById('btn-publish-selected');
                if (publishButton) publishButton.click();
            },
            'entry-mission-organize': () => {
                enterMission('organize');
                navigate('sorting');
            },
            'entry-anchor-continue': () => {
                enterMission('organize');
                navigate('sorting');
            },
            // comfort.js restores the exact offset once the gallery loads; this
            // just gets the user there without a detour.
            'entry-resume-continue': () => freeNavigate('gallery'),
            'entry-fn-gallery': () => freeNavigate('gallery'),
            // Tile promises "Manual Sort" — land on that sub-tab, not
            // whichever one (usually Auto-Separate) was last active.
            'entry-fn-sort': () => {
                freeNavigate('sorting');
                if (typeof window._switchSortingSub === 'function') {
                    window._switchSortingSub('manual');
                }
            },
            'entry-fn-censor': () => freeNavigate('censor'),
            'entry-fn-reader': () => freeNavigate('reader'),
            // Owner FB (2026-07-07): 隐私处理 surfaced from the Reader's tool
            // tabs onto the main page.
            'entry-fn-privacy': () => {
                if (window.NavMissions) window.NavMissions.exit();
                hide();
                if (window.EntryCatalog) window.EntryCatalog.openReaderTool('obfuscation');
            },
            'entry-fn-similar': () => freeNavigate('similar'),
            // Owner FB (2026-07-07): 自由模式 was redundant with the Library
            // tile; 全部工具 is now the function catalog.
            'entry-all-tools': () => {
                if (window.EntryCatalog) window.EntryCatalog.open();
            },
        };
        Object.entries(clicks).forEach(([id, handler]) => {
            const node = el(id);
            if (node) node.addEventListener('click', handler);
        });

        const openSettingsOn = (tab) => {
            if (window.EntryCatalog) {
                window.EntryCatalog.openSettingsTab(tab);
                return;
            }
            const opener = document.getElementById('btn-open-model-manager');
            if (opener) opener.click();
        };

        const settingsBtn = el('entry-settings-btn');
        if (settingsBtn) settingsBtn.addEventListener('click', () => openSettingsOn('general'));

        // Owner FB (2026-07-07): the Model Center tile lands on the AI Models
        // tab directly — before, the combined Settings & Models modal opened
        // on its Settings tab, which read as "this only goes to settings?".
        const modelsTile = el('entry-fn-models');
        if (modelsTile) modelsTile.addEventListener('click', () => openSettingsOn('models'));

        // Owner FB (2026-07-07): language + update check on the main page —
        // proxy the top bar's buttons so behavior can't drift.
        const langBtn = el('entry-lang-btn');
        if (langBtn) {
            langBtn.addEventListener('click', () => {
                const proxy = document.getElementById('btn-language-toggle');
                if (proxy) proxy.click();
            });
        }
        const updateBtn = el('entry-update-btn');
        if (updateBtn) {
            updateBtn.addEventListener('click', () => {
                const proxy = document.getElementById('btn-app-update');
                if (proxy) proxy.click();
            });
        }

        // Dynamic strings (credit line, tile subs, stats) re-render on
        // language switch; data-i18n nodes are handled by I18n.applyToDOM.
        window.addEventListener('languageChanged', () => {
            if (state.visible) render();
        });

        const swap = el('entry-hero-swap');
        if (swap) {
            swap.addEventListener('click', async () => {
                const starredHero = state.summary && state.summary.hero;
                if (heroMode() === 'single' && !starredHero) {
                    // Fallback single (no ★5 yet): walk the pool directly —
                    // re-fetching the summary would just return null again.
                    state.slideshowIndex += 1;
                    // Same render generation — this only advances the cursor.
                    renderSingleFallback(el('entry-hero-credit'), swap, state.heroToken);
                    return;
                }
                try { localStorage.setItem(HERO_SEED_KEY, String((heroSeed() + 1) % 1000000)); } catch (e) { /* ignore */ }
                const summary = await api(`/api/entry/summary?hero_seed=${heroSeed()}`);
                if (summary) {
                    state.summary = { ...(state.summary || {}), hero: summary.hero };
                    renderHero(summary.hero);
                }
            });
        }

        document.querySelectorAll('#entry-hero-mode-switch .hero-mode-btn').forEach((button) => {
            button.addEventListener('click', () => setHeroMode(button.dataset.mode));
        });

        wireSettings();
        document.addEventListener('keydown', onKeydownCapture, true);
    }

    function boot() {
        wire();
        if (!isSkipped()) show();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.EntryPage = { show, hide, render };
})();
