/**
 * Gallery Comfort — make the Library feel like a room, not a control panel.
 *
 * Comfort-1:
 * - Soft stage ambient, resume scroll, compact empty, quiet daily stats ribbon
 * Comfort-2:
 * - Space = light peek (hold); Enter still opens full modal (card handler)
 * - Action-bar magnetic enter animation
 * - Entry hero continuity into gallery stage
 * - Optional daily-loop chip (看图 → 选 → 标/分)
 *
 * Classic script; no module imports. Hooks existing events only.
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'sd-gallery-comfort-v1';
    const HERO_ID_KEY = 'sd-gallery-comfort-hero-id';
    const DAILY_LOOP_DISMISS_PREFIX = 'sd-gallery-daily-loop-dismissed-';
    const DAY_MS = 24 * 60 * 60 * 1000;
    const RESUME_MAX_AGE_MS = 14 * DAY_MS;
    const RESUME_TOAST_COOLDOWN_MS = 6 * 60 * 60 * 1000;

    let _saveTimer = null;
    let _restoring = false;
    let _restoreClaimedAt = 0;
    // Hard ceiling on the restore window so a failed restore can never leave
    // scroll-to-top suppressed for the rest of the session.
    const RESTORE_CLAIM_MAX_MS = 5000;
    let _boundScroll = false;
    let _resumeAnnouncedFor = null;
    let _hoverImageId = null;
    let _peekOpen = false;
    let _spaceHeld = false;

    function _todayKey() {
        const d = new Date();
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
    }

    /**
     * app.js keeps AppState inside the sealed window.App context — there is no
     * window.AppState. Every read here used to go through window.AppState?.…,
     * which is permanently undefined, so:
     *   - tryRestoreResume() always bailed at its `count === 0` guard and the
     *     scroll position was NEVER restored (the feature looked implemented
     *     but could not fire), and
     *   - saveResumeNow()'s "only while viewing the gallery" guard never
     *     engaged, which is how a fresh launch overwrote a good position.
     * Resolve the real object once, with the legacy global as a fallback in
     * case a future build re-exports it.
     */
    function _appState() {
        return (window.App && window.App.AppState) || window.AppState || null;
    }

    function _t(key, fallback, params) {
        if (typeof window.appT === 'function') {
            return window.appT(key, fallback, params);
        }
        const val = window.I18n?.t?.(key, params);
        if (val && val !== key) return val;
        let text = fallback || key;
        if (params && typeof params === 'object') {
            for (const [k, v] of Object.entries(params)) {
                text = String(text).split(`{${k}}`).join(String(v));
            }
        }
        return text;
    }

    function _read() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return _defaultState();
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return _defaultState();
            return _normalize(parsed);
        } catch (_e) {
            return _defaultState();
        }
    }

    function _defaultState() {
        return {
            v: 1,
            resume: null,
            dayKey: _todayKey(),
            day: { favorites: 0, selectsPeak: 0, loads: 0 },
            lastResumeToastAt: 0,
        };
    }

    function _normalize(raw) {
        const state = _defaultState();
        if (raw.resume && typeof raw.resume === 'object') {
            state.resume = {
                scrollTop: Number(raw.resume.scrollTop) || 0,
                scope: String(raw.resume.scope || ''),
                sortBy: String(raw.resume.sortBy || ''),
                imageCount: Number(raw.resume.imageCount) || 0,
                savedAt: Number(raw.resume.savedAt) || 0,
            };
        }
        if (raw.dayKey === _todayKey() && raw.day && typeof raw.day === 'object') {
            state.dayKey = raw.dayKey;
            state.day = {
                favorites: Math.max(0, Number(raw.day.favorites) || 0),
                selectsPeak: Math.max(0, Number(raw.day.selectsPeak) || 0),
                loads: Math.max(0, Number(raw.day.loads) || 0),
            };
        }
        state.lastResumeToastAt = Number(raw.lastResumeToastAt) || 0;
        return state;
    }

    function _write(state) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        } catch (_e) {
            /* quota / private mode */
        }
    }

    function _getScrollTop() {
        if (window.Gallery && typeof window.Gallery._getScrollContainer === 'function') {
            const el = window.Gallery._getScrollContainer();
            if (el && el !== document.documentElement && el !== document.body && el !== document.scrollingElement) {
                return el.scrollTop || 0;
            }
        }
        return window.pageYOffset || document.documentElement.scrollTop || 0;
    }

    function _setScrollTop(top) {
        const y = Math.max(0, Number(top) || 0);
        if (window.Gallery && typeof window.Gallery._getScrollContainer === 'function') {
            const el = window.Gallery._getScrollContainer();
            if (el && el !== document.documentElement && el !== document.body && el !== document.scrollingElement) {
                el.scrollTop = y;
                return;
            }
        }
        // behavior:'instant' explicitly: a CSS scroll-behavior:smooth (or an
        // in-flight smooth scroll from the view-switch reset) would otherwise
        // animate this write and let the previous animation win the race.
        try {
            window.scrollTo({ top: y, left: 0, behavior: 'instant' });
        } catch (_e) {
            window.scrollTo(0, y);
        }
    }

    /** Largest offset the current scroller can actually hold. */
    function _maxScrollTop() {
        let el = null;
        if (window.Gallery && typeof window.Gallery._getScrollContainer === 'function') {
            el = window.Gallery._getScrollContainer();
        }
        if (!el || el === document.body || el === document.scrollingElement) {
            el = document.documentElement;
        }
        return Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || 0));
    }

    /**
     * Keep the restore claim (and the position) alive until switchView's reset
     * ladder has finished. Its last write lands at ~700ms and at least one is a
     * behavior:'smooth' scroll, so a plain "we arrived" release let that glide
     * drag the view back to the top over the next second — which looked exactly
     * like the restore had never happened.
     */
    function _releaseRestoreAfterResets(target = null) {
        const holdUntil = Date.now() + 1100;
        const hold = () => {
            if (target != null && Math.abs(_getScrollTop() - target) > 4) {
                // A reset (or its smooth glide) moved us: cancel the animation
                // by re-asserting the target instantly.
                _setScrollTop(target);
            }
            if (Date.now() < holdUntil) {
                requestAnimationFrame(() => setTimeout(hold, 60));
                return;
            }
            _restoring = false;
        };
        hold();
    }

    function _filtersSnapshot() {
        const f = _appState()?.filters || {};
        return {
            scope: String(f.scope || 'current_session'),
            sortBy: String(f.sortBy || ''),
        };
    }

    /**
     * True while the entry overlay covers the app. On relaunch AppState's
     * currentView already reads 'gallery' even though the user is still looking
     * at the entry page and the grid is scrolled to 0 — saving in that window
     * is what erased the position we are trying to keep.
     */
    function _entryOverlayUp() {
        const entry = document.getElementById('entry-page');
        if (!entry || entry.hidden) return false;
        return getComputedStyle(entry).display !== 'none';
    }

    function saveResumeNow() {
        if (_restoring) return;
        if (_appState()?.currentView && _appState().currentView !== 'gallery') return;
        if (_entryOverlayUp()) return;
        const snap = _filtersSnapshot();
        const state = _read();
        const scrollTop = _getScrollTop();
        // Never let a top-of-list write bury a real position. Relaunch, a
        // filter reset and a programmatic jump all momentarily report 0, and
        // "resume at the top" is the same as no resume anyway.
        if (scrollTop < 80 && Number(state.resume?.scrollTop) >= 80) return;
        state.resume = {
            scrollTop,
            scope: snap.scope,
            sortBy: snap.sortBy,
            imageCount: Array.isArray(_appState()?.images) ? _appState().images.length : 0,
            savedAt: Date.now(),
        };
        _write(state);
    }

    function scheduleSaveResume() {
        if (_saveTimer) clearTimeout(_saveTimer);
        _saveTimer = setTimeout(() => {
            _saveTimer = null;
            saveResumeNow();
        }, 400);
    }

    function bindScrollSave() {
        if (_boundScroll) return;
        _boundScroll = true;
        const onScroll = () => scheduleSaveResume();
        window.addEventListener('scroll', onScroll, { passive: true });
        // Also listen on possible gallery scroll containers after layout settles.
        document.addEventListener('scroll', onScroll, { passive: true, capture: true });
        window.addEventListener('beforeunload', saveResumeNow);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') saveResumeNow();
        });
    }

    function tryRestoreResume() {
        const state = _read();
        const resume = state.resume;
        if (!resume || !resume.savedAt) return false;
        if (Date.now() - resume.savedAt > RESUME_MAX_AGE_MS) return false;
        if ((resume.scrollTop || 0) < 80) return false;

        const snap = _filtersSnapshot();
        // Only restore when the user is still in a comparable gallery mode.
        if (resume.scope && snap.scope && resume.scope !== snap.scope) return false;
        if (resume.sortBy && snap.sortBy && resume.sortBy !== snap.sortBy) return false;

        const count = Array.isArray(_appState()?.images) ? _appState().images.length : 0;
        if (count === 0) return false;

        _restoring = true;
        _restoreClaimedAt = Date.now();
        const target = resume.scrollTop;
        // The old fixed rAF + 60ms/120ms ladder gave up ~180ms in, but the
        // virtual list keeps growing the scroll height for a second or more
        // after the first page renders — so the offset was clamped to whatever
        // fit at that instant (usually 0) and the restore silently did nothing.
        // Retry until the position actually holds. "Target beyond the current
        // max scroll" is NOT a stop condition: that is exactly the state a
        // still-filling grid reports. Stop when we land, when the grid has
        // stopped growing while pinned at the bottom, or on the deadline.
        const DEADLINE_MS = 4000;
        const startedAt = Date.now();
        let lastMax = -1;
        let stalledAtBottom = 0;
        const apply = () => {
            _setScrollTop(target);
            const now = _getScrollTop();
            if (Math.abs(now - target) <= 4) {
                // Landed. Do NOT release the claim yet: switchView's reset
                // ladder runs out to 700ms and one of its writes is a
                // behavior:'smooth' scroll, whose glide would otherwise carry
                // us back to 0 over the following second.
                _releaseRestoreAfterResets(target);
                return;
            }
            const max = _maxScrollTop();
            // Pinned at the bottom of a grid that is no longer growing means the
            // library really is shorter than it was — accept where we are.
            if (max === lastMax && now >= max - 4) {
                stalledAtBottom += 1;
            } else {
                stalledAtBottom = 0;
            }
            lastMax = max;
            if (stalledAtBottom >= 4 || Date.now() - startedAt > DEADLINE_MS) {
                _releaseRestoreAfterResets();
                return;
            }
            requestAnimationFrame(() => setTimeout(apply, 80));
        };
        requestAnimationFrame(apply);

        const signature = `${resume.savedAt}:${Math.round(target)}`;
        if (_resumeAnnouncedFor !== signature) {
            _resumeAnnouncedFor = signature;
            const canToast = Date.now() - (state.lastResumeToastAt || 0) > RESUME_TOAST_COOLDOWN_MS;
            if (canToast && typeof window.showToast === 'function') {
                window.showToast(
                    _t('gallery.comfort.restored', 'Welcome back — restored where you left off'),
                    'info',
                );
                state.lastResumeToastAt = Date.now();
                _write(state);
            }
            _showRibbon('restored');
        }
        return true;
    }

    /**
     * Restore when arriving at an ALREADY-loaded gallery (entry page → library).
     * That route reuses the mounted view, so neither gallery-images-loaded nor
     * the switchView wrapper fires and the position was never reapplied. Polls
     * briefly because the grid needs a moment to be tall enough to hold the
     * offset, and gives up quietly if the guards say no.
     */
    function restoreSoon(attempts = 12) {
        // Claim the restore window up front: switchView's scroll reset runs
        // synchronously on the same click, before our first tick.
        _restoring = true;
        _restoreClaimedAt = Date.now();
        let left = Math.max(1, attempts);
        const tick = () => {
            left -= 1;
            const active = _isGalleryActive();
            const ok = active ? tryRestoreResume() : false;
            if (!active || ok || left <= 0) {
                if (!ok) _restoring = false;
                return;
            }
            setTimeout(tick, 220);
        };
        setTimeout(tick, 120);
    }

    function bumpDay(field, amount) {
        const state = _read();
        const today = _todayKey();
        if (state.dayKey !== today) {
            state.dayKey = today;
            state.day = { favorites: 0, selectsPeak: 0, loads: 0 };
        }
        const n = Math.max(0, Number(amount) || 0);
        if (field === 'favorites') state.day.favorites += n || 1;
        if (field === 'loads') state.day.loads += n || 1;
        if (field === 'selectsPeak') {
            state.day.selectsPeak = Math.max(state.day.selectsPeak, n);
        }
        _write(state);
        _refreshRibbon();
    }

    function _ribbonEl() {
        return document.getElementById('gallery-comfort-ribbon');
    }

    function _showRibbon(mode) {
        const el = _ribbonEl();
        if (!el) return;
        const state = _read();
        const day = state.day || {};
        let text = '';

        if (mode === 'restored') {
            text = _t('gallery.comfort.restoredRibbon', 'Restored your place in the library');
        } else {
            const parts = [];
            if (day.favorites > 0) {
                parts.push(_t('gallery.comfort.statFavorites', 'favorited {n}', { n: day.favorites }));
            }
            if (day.selectsPeak > 0) {
                parts.push(_t('gallery.comfort.statSelects', 'selected up to {n}', { n: day.selectsPeak }));
            }
            if (parts.length === 0) {
                el.hidden = true;
                el.textContent = '';
                return;
            }
            text = _t('gallery.comfort.todayRibbon', 'Today · {stats}', { stats: parts.join(' · ') });
        }

        el.textContent = text;
        el.hidden = false;
        el.dataset.mode = mode || 'today';
        // Auto-fade restored message; keep today stats longer.
        if (mode === 'restored') {
            clearTimeout(el._hideTimer);
            el._hideTimer = setTimeout(() => {
                if (el.dataset.mode === 'restored') {
                    _refreshRibbon();
                }
            }, 6000);
        }
    }

    function _refreshRibbon() {
        const el = _ribbonEl();
        if (!el) return;
        const state = _read();
        if (state.dayKey !== _todayKey()) {
            el.hidden = true;
            return;
        }
        const day = state.day || {};
        if ((day.favorites || 0) > 0 || (day.selectsPeak || 0) > 0) {
            _showRibbon('today');
        } else if (el.dataset.mode !== 'restored') {
            el.hidden = true;
        }
    }

    function markGalleryRoom(active) {
        document.documentElement.classList.toggle('gallery-comfort-room', Boolean(active));
        const view = document.getElementById('view-gallery');
        if (view) view.classList.toggle('is-comfort-room', Boolean(active));
        if (active) {
            applyEntryHeroContinuity();
            showDailyLoopChipIfNeeded();
            armActionBarMagnet();
        } else {
            hidePeek();
        }
    }

    /* ---------- Comfort-2: Space light peek ---------- */

    function _isEditingTarget(target) {
        if (!target) return false;
        const tag = (target.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
    }

    function _overlayBlockingPeek() {
        return Boolean(document.querySelector(
            '.modal.visible, .dataset-modal:not([hidden]), .image-workspace.visible, '
            + '#onboarding-overlay:not([hidden]), .guide-overlay.visible, .update-popup.visible',
        ));
    }

    function _resolvePeekImageId() {
        const focused = document.activeElement?.closest?.('.gallery-item[data-id]');
        if (focused) return focused.getAttribute('data-id');
        if (_hoverImageId) return _hoverImageId;
        // Fallback: first selected, then first visible card.
        const selected = document.querySelector('#gallery-grid .gallery-item.selected[data-id]');
        if (selected) return selected.getAttribute('data-id');
        const first = document.querySelector('#gallery-grid .gallery-item[data-id]');
        return first ? first.getAttribute('data-id') : null;
    }

    function _ensurePeekEl() {
        let el = document.getElementById('gallery-comfort-peek');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'gallery-comfort-peek';
        el.className = 'gallery-comfort-peek';
        el.hidden = true;
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-modal', 'false');
        el.setAttribute('aria-label', 'Quick preview');
        el.innerHTML = [
            '<div class="gallery-comfort-peek-scrim" data-peek-dismiss="1"></div>',
            '<div class="gallery-comfort-peek-stage">',
            '  <img class="gallery-comfort-peek-img" alt="" draggable="false" />',
            '  <div class="gallery-comfort-peek-hint" data-i18n="gallery.comfort.peekHint">Hold Space · release to close · Enter for full details</div>',
            '</div>',
        ].join('');
        document.body.appendChild(el);
        el.addEventListener('click', (e) => {
            if (e.target?.dataset?.peekDismiss) hidePeek();
        });
        return el;
    }

    function showPeek(imageId) {
        if (!imageId || !_isGalleryActive()) return;
        if (_appState()?.selectionMode) return;
        if (_overlayBlockingPeek()) return;
        const el = _ensurePeekEl();
        const img = el.querySelector('.gallery-comfort-peek-img');
        const url = (window.API && typeof window.API.getImageUrl === 'function')
            ? window.API.getImageUrl(imageId)
            : `/api/image-file/${imageId}`;
        if (img && img.dataset.imageId !== String(imageId)) {
            img.dataset.imageId = String(imageId);
            img.src = url;
        }
        el.hidden = false;
        document.documentElement.classList.add('gallery-comfort-peeking');
        _peekOpen = true;
        // Soft hero continuity from the peeked image too.
        try {
            document.documentElement.style.setProperty('--comfort-hero-url', `url("${url}")`);
            document.documentElement.classList.add('gallery-comfort-has-hero');
        } catch (_e) { /* ignore */ }
    }

    function hidePeek() {
        const el = document.getElementById('gallery-comfort-peek');
        if (el) el.hidden = true;
        document.documentElement.classList.remove('gallery-comfort-peeking');
        _peekOpen = false;
        _spaceHeld = false;
    }

    function bindSpacePeek() {
        document.addEventListener('keydown', (e) => {
            if (e.code !== 'Space' && e.key !== ' ') return;
            if (e.repeat) {
                if (_peekOpen) e.preventDefault();
                return;
            }
            if (!_isGalleryActive()) return;
            if (_isEditingTarget(e.target) || _isEditingTarget(document.activeElement)) return;
            if (_appState()?.selectionMode) return; // keep Space = toggle select
            if (_overlayBlockingPeek()) return;

            const id = _resolvePeekImageId();
            if (!id) return;

            // Capture: stop card handler from opening the heavy modal.
            e.preventDefault();
            e.stopPropagation();
            _spaceHeld = true;
            showPeek(id);
        }, true);

        document.addEventListener('keyup', (e) => {
            if (e.code !== 'Space' && e.key !== ' ') return;
            if (_spaceHeld || _peekOpen) {
                e.preventDefault();
                hidePeek();
            }
        }, true);

        window.addEventListener('blur', hidePeek);

        // Track hover target for mouse users who hold Space without focus.
        const grid = document.getElementById('gallery-grid');
        grid?.addEventListener('pointerover', (e) => {
            const item = e.target?.closest?.('.gallery-item[data-id]');
            if (item) _hoverImageId = item.getAttribute('data-id');
        });
        grid?.addEventListener('pointerout', (e) => {
            const item = e.target?.closest?.('.gallery-item[data-id]');
            if (item && item.getAttribute('data-id') === _hoverImageId) {
                // Only clear if leaving the card entirely.
                if (!e.relatedTarget || !item.contains(e.relatedTarget)) {
                    _hoverImageId = null;
                }
            }
        });
    }

    /* ---------- Comfort-2: Entry hero continuity ---------- */

    function applyEntryHeroContinuity() {
        let id = null;
        try { id = localStorage.getItem(HERO_ID_KEY); } catch (_e) { id = null; }
        if (!id) {
            document.documentElement.classList.remove('gallery-comfort-has-hero');
            return;
        }
        const url = (window.API && typeof window.API.getImageUrl === 'function')
            ? window.API.getImageUrl(id)
            : `/api/image-file/${id}`;
        document.documentElement.style.setProperty('--comfort-hero-url', `url("${url}")`);
        document.documentElement.classList.add('gallery-comfort-has-hero');
    }

    function stashHeroId(id) {
        if (id == null || id === '') return;
        try { localStorage.setItem(HERO_ID_KEY, String(id)); } catch (_e) { /* ignore */ }
    }

    /* ---------- Comfort-2: Daily loop chip ---------- */

    function showDailyLoopChipIfNeeded() {
        const chip = document.getElementById('gallery-daily-loop');
        if (!chip) return;
        // De-AI default: coaching chips stay off. Opt-in via localStorage only.
        let optedIn = false;
        try {
            optedIn = localStorage.getItem('sd-gallery-daily-loop-opt-in') === '1';
        } catch (_e) {
            optedIn = false;
        }
        if (!optedIn) {
            chip.hidden = true;
            chip.classList.remove('is-opted-in');
            return;
        }
        const key = DAILY_LOOP_DISMISS_PREFIX + _todayKey();
        try {
            if (localStorage.getItem(key) === '1') {
                chip.hidden = true;
                chip.classList.remove('is-opted-in');
                return;
            }
        } catch (_e) { /* keep evaluating */ }
        const count = Array.isArray(_appState()?.images) ? _appState().images.length : 0;
        const total = Number(_appState()?.pagination?.total || 0);
        if (count <= 0 && total <= 0) {
            chip.hidden = true;
            chip.classList.remove('is-opted-in');
            return;
        }
        chip.classList.add('is-opted-in');
        chip.hidden = false;
    }

    function bindDailyLoopChip() {
        const chip = document.getElementById('gallery-daily-loop');
        if (!chip) return;
        chip.querySelector('[data-daily-loop-dismiss]')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            try { localStorage.setItem(DAILY_LOOP_DISMISS_PREFIX + _todayKey(), '1'); } catch (_err) { /* ignore */ }
            chip.hidden = true;
        });
    }

    /* ---------- Comfort-2: Action bar magnetic enter ---------- */

    function armActionBarMagnet() {
        const bar = document.getElementById('gallery-action-bar');
        if (!bar || bar._comfortMagnetArmed) return;
        bar._comfortMagnetArmed = true;
        const sync = () => {
            const visible = !bar.hasAttribute('hidden');
            bar.classList.toggle('is-magnet-in', visible);
        };
        const obs = new MutationObserver(sync);
        obs.observe(bar, { attributes: true, attributeFilter: ['hidden'] });
        sync();
    }

    function onImagesLoaded(detail) {
        const append = Boolean(detail && detail.appendMode);
        if (!append) {
            bumpDay('loads', 1);
            // Fresh load: attempt resume once images exist.
            if (Array.isArray(_appState()?.images) && _appState().images.length > 0) {
                tryRestoreResume();
            }
        }
        markGalleryRoom(_appState()?.currentView === 'gallery');
        _refreshRibbon();
    }

    function onSelectionChanged() {
        const size = _appState()?.selectedIds?.size || 0;
        if (size > 0) bumpDay('selectsPeak', size);
    }

    function onFavoriteToggled() {
        bumpDay('favorites', 1);
    }

    function _isGalleryActive() {
        const view = _appState()?.currentView;
        if (view) return view === 'gallery';
        const el = document.getElementById('view-gallery');
        return Boolean(el && el.classList.contains('active'));
    }

    function init() {
        bindScrollSave();
        markGalleryRoom(_isGalleryActive() || !_appState());
        // Re-assert after boot/entry handoff settles.
        setTimeout(() => markGalleryRoom(_isGalleryActive()), 300);
        setTimeout(() => markGalleryRoom(_isGalleryActive()), 1200);

        window.addEventListener('gallery-images-loaded', (e) => {
            onImagesLoaded(e.detail || {});
        });

        // SelectionStore / app may emit this; also poll-free click fallbacks below.
        window.addEventListener('selection-state-changed', onSelectionChanged);
        document.addEventListener('selection-changed', onSelectionChanged);

        // Favorites: the card's own click handler calls stopPropagation() on the
        // heart (gallery/card-markup.js) so the event never bubbles to
        // #gallery-grid — this listener used to count nothing and the daily
        // ribbon stayed empty no matter how many images you hearted. Listen in
        // the CAPTURE phase, which runs before the card can stop it. Bound on
        // document so a re-rendered / virtualised grid stays covered.
        document.addEventListener('click', (e) => {
            const fav = e.target.closest?.('.gallery-item-fav');
            if (!fav) return;
            if (!e.target.closest?.('#gallery-grid')) return;
            // Count optimistic intent; exact on/off not required for comfort stats.
            setTimeout(onFavoriteToggled, 0);
        }, true);

        // Wrap switchView (classic global) so leaving/entering gallery stays cozy.
        if (typeof window.switchView === 'function' && !window.switchView.__comfortWrapped) {
            const originalSwitchView = window.switchView;
            const wrapped = function comfortSwitchView(viewName) {
                if (_appState()?.currentView === 'gallery' && viewName !== 'gallery') {
                    saveResumeNow();
                }
                const result = originalSwitchView.apply(this, arguments);
                markGalleryRoom(viewName === 'gallery');
                if (viewName === 'gallery') {
                    _refreshRibbon();
                    // Returning with cached images may not fire gallery-images-loaded.
                    setTimeout(() => {
                        if (_appState()?.currentView === 'gallery'
                            && Array.isArray(_appState()?.images)
                            && _appState().images.length > 0) {
                            tryRestoreResume();
                        }
                    }, 80);
                }
                try {
                    window.dispatchEvent(new CustomEvent('view-changed', {
                        detail: { view: viewName },
                    }));
                } catch (_e) { /* ignore */ }
                return result;
            };
            wrapped.__comfortWrapped = true;
            window.switchView = wrapped;
            // Classic scripts may also close over the bare global binding.
            try { switchView = wrapped; } catch (_e) { /* non-assignable */ }
        }

        // Dismiss ribbon click.
        _ribbonEl()?.addEventListener('click', () => {
            const el = _ribbonEl();
            if (el) {
                el.hidden = true;
                el.dataset.mode = '';
            }
        });

        bindSpacePeek();
        bindDailyLoopChip();
        applyEntryHeroContinuity();
        showDailyLoopChipIfNeeded();
        armActionBarMagnet();
        _refreshRibbon();
    }

    // Public tiny API for tests / other modules.
    window.GalleryComfort = {
        saveResumeNow,
        tryRestoreResume,
        restoreSoon,
        // Read by app/selection.js scheduleViewScrollReset so a deliberate
        // resume is not overwritten by the view-switch scroll-to-top.
        isRestoring: () => Boolean(
            _restoring && Date.now() - _restoreClaimedAt < RESTORE_CLAIM_MAX_MS,
        ),
        bumpDay,
        showPeek,
        hidePeek,
        stashHeroId,
        applyEntryHeroContinuity,
        _read,
        _STORAGE_KEY: STORAGE_KEY,
        _HERO_ID_KEY: HERO_ID_KEY,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
