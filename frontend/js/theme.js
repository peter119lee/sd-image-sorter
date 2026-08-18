/**
 * Color themes — Graphite (default) and Black + Blue.
 *
 * Both are dark on purpose. tokens.css is an override layer over 21 sheets
 * that still hardcode dark values with no data-theme selector, so a light
 * palette would leave black holes in Censor and Dataset until those sheets
 * converge. Adding one means converting them first, not just adding tokens.
 *
 * The saved id is applied in <head> before CSS so this file only owns the
 * picker UI and later switches. Unknown or missing values fall back to
 * graphite; nothing else is written to localStorage.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'sd-image-sorter-theme';
    var THEMES = ['graphite', 'ink'];
    var DEFAULT_THEME = 'graphite';

    function allowed(id) {
        return THEMES.indexOf(id) !== -1;
    }

    function readSaved() {
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            if (allowed(saved)) return saved;
        } catch (e) { /* private mode */ }
        return DEFAULT_THEME;
    }

    function persist(id) {
        try {
            localStorage.setItem(STORAGE_KEY, id);
        } catch (e) { /* private mode */ }
    }

    function current() {
        var attr = document.documentElement.getAttribute('data-theme');
        return allowed(attr) ? attr : readSaved();
    }

    function apply(id) {
        if (!allowed(id)) id = DEFAULT_THEME;
        document.documentElement.setAttribute('data-theme', id);
        persist(id);
        syncChoices(id);
        var event;
        try {
            event = new CustomEvent('themeChanged', { detail: { theme: id } });
        } catch (e) {
            event = document.createEvent('CustomEvent');
            event.initCustomEvent('themeChanged', true, true, { theme: id });
        }
        document.dispatchEvent(event);
    }

    function t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            var value = window.I18n.t(key);
            if (value && value !== key) return value;
        }
        return fallback;
    }

    function syncChoices(id) {
        var chosen = allowed(id) ? id : DEFAULT_THEME;
        var nodes = document.querySelectorAll('[data-theme-id]');
        for (var i = 0; i < nodes.length; i += 1) {
            var isCurrent = nodes[i].getAttribute('data-theme-id') === chosen;
            nodes[i].setAttribute('aria-selected', isCurrent ? 'true' : 'false');
            nodes[i].classList.toggle('is-current', isCurrent);
        }
        var select = document.getElementById('settings-theme');
        if (select && select.value !== chosen) select.value = chosen;
        var toggle = document.getElementById('btn-theme-toggle');
        if (toggle) {
            toggle.setAttribute('aria-label', t('theme.open', 'Color theme'));
            toggle.setAttribute('title', t('theme.openTooltip', 'Choose a color theme'));
        }
        var entry = document.getElementById('entry-theme-btn');
        if (entry) {
            entry.setAttribute('aria-label', t('theme.open', 'Color theme'));
            entry.setAttribute('title', t('theme.openTooltip', 'Choose a color theme'));
        }
    }

    function menuEl() {
        return document.getElementById('theme-menu');
    }

    /* Both the nav icon and the entry-page button open the same listbox, so
       the open state and the label association have to follow whichever one
       the user actually pressed — otherwise a screen reader announces the
       listbox against a control that is off-screen. */
    var ANCHOR_IDS = ['btn-theme-toggle', 'entry-theme-btn'];

    function setExpanded(anchor) {
        for (var i = 0; i < ANCHOR_IDS.length; i += 1) {
            var node = document.getElementById(ANCHOR_IDS[i]);
            if (!node) continue;
            node.setAttribute('aria-expanded', node === anchor ? 'true' : 'false');
        }
        var menu = menuEl();
        if (menu && anchor && anchor.id) {
            menu.setAttribute('aria-labelledby', anchor.id);
        }
    }

    function closeMenu() {
        var menu = menuEl();
        if (menu) menu.hidden = true;
        setExpanded(null);
    }

    function placeMenu(anchor) {
        var menu = menuEl();
        if (!menu || !anchor) return;
        var rect = anchor.getBoundingClientRect();
        menu.style.top = Math.round(rect.bottom + 6) + 'px';
        menu.style.right = Math.round(Math.max(8, window.innerWidth - rect.right)) + 'px';
        menu.hidden = false;
        setExpanded(anchor);
    }

    function toggleFrom(anchor) {
        var menu = menuEl();
        if (!menu) return;
        if (!menu.hidden) {
            closeMenu();
            return;
        }
        placeMenu(anchor);
    }

    function onDocumentClick(event) {
        var menu = menuEl();
        if (!menu || menu.hidden) return;
        var target = event.target;
        if (menu.contains(target)) return;
        if (target.closest && (
            target.closest('#btn-theme-toggle') ||
            target.closest('#entry-theme-btn')
        )) return;
        closeMenu();
    }

    function onKeydown(event) {
        if (event.key === 'Escape') closeMenu();
    }

    function bind() {
        apply(current());

        var toggle = document.getElementById('btn-theme-toggle');
        if (toggle) {
            toggle.addEventListener('click', function (event) {
                event.stopPropagation();
                toggleFrom(toggle);
            });
        }
        var entry = document.getElementById('entry-theme-btn');
        if (entry) {
            entry.addEventListener('click', function (event) {
                event.stopPropagation();
                toggleFrom(entry);
            });
        }

        var choices = document.querySelectorAll('[data-theme-id]');
        for (var i = 0; i < choices.length; i += 1) {
            choices[i].addEventListener('click', function (event) {
                var id = event.currentTarget.getAttribute('data-theme-id');
                apply(id);
                closeMenu();
                if (typeof window.closeMobileMenu === 'function' &&
                    event.currentTarget.closest &&
                    event.currentTarget.closest('.mobile-nav-menu')) {
                    window.closeMobileMenu();
                }
            });
        }

        var select = document.getElementById('settings-theme');
        if (select) {
            select.addEventListener('change', function () {
                apply(select.value);
            });
        }

        document.addEventListener('click', onDocumentClick);
        document.addEventListener('keydown', onKeydown);
        window.addEventListener('resize', closeMenu);
        document.addEventListener('languageChanged', function () {
            syncChoices(current());
        });
    }

    window.Theme = {
        STORAGE_KEY: STORAGE_KEY,
        THEMES: THEMES.slice(),
        apply: apply,
        current: current,
        toggleFrom: toggleFrom,
        closeMenu: closeMenu,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
