/**
 * Comfort-app (Comfort-3) — whole-app "studio room" shell.
 *
 * Completes the comfort initiative beyond Gallery:
 * - Soft zen mode (quieter chrome; not the same as Manual Sort stage zen)
 * - Warmth palette: cool / neutral / warm
 * - View enter transitions
 * - Censor / Dataset / similar workbench "room" class
 * - Settings + keyboard (Z with modifiers avoided; use ` key or settings)
 *
 * Classic script. Coordinates with gallery/comfort.js (GalleryComfort).
 */
(function () {
    'use strict';

    const ZEN_KEY = 'sd-comfort-zen';
    const WARMTH_KEY = 'sd-comfort-warmth';
    const VALID_WARMTH = new Set(['cool', 'neutral', 'warm']);

    function _t(key, fallback, params) {
        if (typeof window.appT === 'function') return window.appT(key, fallback, params);
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

    function isZen() {
        try { return localStorage.getItem(ZEN_KEY) === '1'; } catch (_e) { return false; }
    }

    function setZen(on, { silent } = {}) {
        const enabled = Boolean(on);
        try { localStorage.setItem(ZEN_KEY, enabled ? '1' : '0'); } catch (_e) { /* ignore */ }
        document.documentElement.classList.toggle('comfort-zen', enabled);
        // Soften brand chrome; keep nav reachable (unlike sort-zen which hides it).
        refreshZenButton();
        if (!silent && typeof window.showToast === 'function') {
            window.showToast(
                enabled
                    ? _t('comfort.zenOn', 'Zen mode on — quieter chrome')
                    : _t('comfort.zenOff', 'Zen mode off'),
                'info',
            );
        }
        try {
            window.dispatchEvent(new CustomEvent('comfort-zen-changed', { detail: { enabled } }));
        } catch (_e) { /* ignore */ }
        return enabled;
    }

    function toggleZen() {
        return setZen(!isZen());
    }

    function getWarmth() {
        try {
            const v = localStorage.getItem(WARMTH_KEY) || 'neutral';
            return VALID_WARMTH.has(v) ? v : 'neutral';
        } catch (_e) {
            return 'neutral';
        }
    }

    function setWarmth(mode) {
        const next = VALID_WARMTH.has(mode) ? mode : 'neutral';
        try { localStorage.setItem(WARMTH_KEY, next); } catch (_e) { /* ignore */ }
        document.documentElement.setAttribute('data-comfort-warmth', next);
        refreshWarmthControl();
        try {
            window.dispatchEvent(new CustomEvent('comfort-warmth-changed', { detail: { warmth: next } }));
        } catch (_e) { /* ignore */ }
        return next;
    }

    function markWorkbenchRooms(viewName) {
        const rooms = ['gallery', 'censor', 'dataset', 'similar', 'sorting', 'reader', 'promptlab', 'artist'];
        rooms.forEach((name) => {
            const el = document.getElementById(`view-${name}`);
            if (!el) return;
            const active = name === viewName;
            el.classList.toggle('is-comfort-room', active);
            el.classList.toggle('comfort-room-enter', active);
            if (active) {
                // Retrigger CSS enter animation.
                el.classList.remove('comfort-room-enter-active');
                // force reflow
                void el.offsetWidth;
                el.classList.add('comfort-room-enter-active');
                window.setTimeout(() => {
                    el.classList.remove('comfort-room-enter-active');
                }, 320);
            }
        });
        document.documentElement.setAttribute('data-comfort-view', viewName || '');
    }

    function wrapSwitchView() {
        if (typeof window.switchView !== 'function') return;
        if (window.switchView.__comfortAppWrapped) return;
        const original = window.switchView;
        const wrapped = function comfortAppSwitchView(viewName) {
            document.documentElement.classList.add('comfort-view-switching');
            const result = original.apply(this, arguments);
            markWorkbenchRooms(viewName);
            window.requestAnimationFrame(() => {
                window.setTimeout(() => {
                    document.documentElement.classList.remove('comfort-view-switching');
                }, 220);
            });
            return result;
        };
        wrapped.__comfortAppWrapped = true;
        // Preserve prior comfort gallery wrap flags if any.
        if (original.__comfortWrapped) wrapped.__comfortWrapped = true;
        window.switchView = wrapped;
        try { switchView = wrapped; } catch (_e) { /* ignore */ }
    }

    function refreshZenButton() {
        const btn = document.getElementById('btn-settings-comfort-zen');
        if (!btn) return;
        const on = isZen();
        btn.setAttribute('aria-pressed', String(on));
        const label = document.getElementById('settings-comfort-zen-label');
        if (label) {
            label.textContent = on
                ? _t('comfort.zenOnLabel', 'On')
                : _t('comfort.zenOffLabel', 'Off');
        }
        const icon = document.getElementById('settings-comfort-zen-icon');
        if (icon) icon.innerHTML = on ? "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-moon\"/></svg>" : "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-monitor\"/></svg>";
    }

    function refreshWarmthControl() {
        const select = document.getElementById('settings-comfort-warmth');
        if (!select) return;
        const w = getWarmth();
        if (select.value !== w) select.value = w;
    }

    function wireSettings() {
        const zenBtn = document.getElementById('btn-settings-comfort-zen');
        zenBtn?.addEventListener('click', () => toggleZen());

        const warmth = document.getElementById('settings-comfort-warmth');
        warmth?.addEventListener('change', () => setWarmth(warmth.value));

        refreshZenButton();
        refreshWarmthControl();
    }

    function wireKeyboard() {
        // Ctrl+.  toggles soft zen (avoids conflict with Manual Sort WASD / G L W F).
        document.addEventListener('keydown', (e) => {
            if (!(e.ctrlKey || e.metaKey)) return;
            if (e.key !== '.' && e.code !== 'Period') return;
            const tag = (e.target?.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) {
                return;
            }
            e.preventDefault();
            toggleZen();
        });
    }

    function wireNavZenChip() {
        // Optional floating exit chip when zen is on (nav is dimmed not gone).
        let chip = document.getElementById('comfort-zen-chip');
        if (!chip) {
            chip = document.createElement('button');
            chip.id = 'comfort-zen-chip';
            chip.type = 'button';
            chip.className = 'comfort-zen-chip';
            chip.hidden = true;
            chip.setAttribute('aria-label', 'Exit zen mode');
            chip.innerHTML = '<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-moon"/></svg></span> <span data-i18n="comfort.zenExit">Exit zen</span>';
            document.body.appendChild(chip);
            chip.addEventListener('click', () => setZen(false));
        }
        const sync = () => {
            const entryActive = document.body.classList.contains('entry-active');
            chip.hidden = !isZen() || entryActive;
            const span = chip.querySelector('[data-i18n]');
            if (span) span.textContent = _t('comfort.zenExit', 'Exit zen');
        };
        window.addEventListener('comfort-zen-changed', sync);
        sync();
    }

    function applyBoot() {
        document.documentElement.classList.toggle('comfort-zen', isZen());
        document.documentElement.setAttribute('data-comfort-warmth', getWarmth());
        const view = window.AppState?.currentView
            || document.querySelector('.view.active')?.id?.replace(/^view-/, '')
            || 'gallery';
        markWorkbenchRooms(view);
    }

    function init() {
        applyBoot();
        wrapSwitchView();
        wireSettings();
        wireKeyboard();
        wireNavZenChip();

        // Re-apply after i18n may relabel settings.
        window.addEventListener('languagechange', () => {
            refreshZenButton();
        });
        document.addEventListener('i18n-applied', () => {
            refreshZenButton();
        });
    }

    window.ComfortApp = {
        isZen,
        setZen,
        toggleZen,
        getWarmth,
        setWarmth,
        markWorkbenchRooms,
        _ZEN_KEY: ZEN_KEY,
        _WARMTH_KEY: WARMTH_KEY,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
