/**
 * Mission-scoped smart nav bar + customizable tab visibility.
 *
 * Owner FB (2026-07-07): "if user click those missions in the main page, the
 * topest bar will smartly switch to only needed tab there, so user know how
 * to going. Yes, make the bar customerize."
 *
 * Three layers decide which direct tabs are visible:
 * 1. Mission mode (persisted): entry-page mission tiles enter a mission; the
 *    bar shows ONLY that mission's tabs in pipeline order with step badges,
 *    plus a chip whose ✕ exits back to the user's own set.
 * 2. Base set (persisted, customizable): a checklist under More decides which
 *    core views stay in the bar. The Library is always shown. Dataset is out
 *    of the default set (owner 2026-07-07) — reached via the LoRA mission,
 *    its More mirror, or the function catalog.
 * 3. Contextual reveal: the active view's tab is always shown, so an open
 *    view never lacks its highlighted tab.
 *
 * Tucked views stay reachable through More-menu mirrors (#nav-tools-{view}).
 * Mirrors carry data-mirror-view, NOT data-view — Playwright page objects
 * click plain [data-view=...] locators and a second match would violate
 * strict mode (the pre-existing promptlab/artist mirrors are grandfathered).
 *
 * Prompt Helper / Style Finder keep their own width-degradation ladder
 * behavior (nav-priority-advanced) and are not part of the checklist.
 */
(function () {
    'use strict';

    const TABS_KEY = 'aurora-nav-tabs';
    const MISSION_KEY = 'aurora-nav-mission';

    const ALL_VIEWS = ['gallery', 'reader', 'sorting', 'censor', 'similar', 'dataset', 'promptlab', 'artist'];
    const DEFAULT_TABS = ['gallery', 'reader', 'sorting', 'censor', 'similar', 'promptlab', 'artist'];
    const LOCKED_TABS = ['gallery'];
    const CUSTOM_VIEWS = ['reader', 'sorting', 'censor', 'similar', 'dataset'];

    const MISSIONS = {
        lora: { labelKey: 'entry.missionLoraTitle', fallback: 'LoRA Dataset', tabs: ['gallery', 'dataset'] },
        pixiv: { labelKey: 'entry.missionPixivTitle', fallback: 'Pixiv Set Publishing', tabs: ['gallery', 'censor'] },
        organize: { labelKey: 'entry.missionOrganizeTitle', fallback: 'Batch Organize', tabs: ['gallery', 'sorting'] },
    };

    function t(key, fallback) {
        const value = window.I18n && window.I18n.t ? window.I18n.t(key) : null;
        return (value && value !== key) ? value : (fallback || key);
    }

    function readJson(key) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : null;
        } catch (error) {
            return null;
        }
    }

    function baseTabs() {
        const stored = readJson(TABS_KEY);
        if (!Array.isArray(stored)) return DEFAULT_TABS.slice();
        const valid = stored.filter((view) => ALL_VIEWS.includes(view));
        LOCKED_TABS.forEach((view) => {
            if (!valid.includes(view)) valid.unshift(view);
        });
        return valid;
    }

    function setBaseTabs(list) {
        try { localStorage.setItem(TABS_KEY, JSON.stringify(list)); } catch (error) { /* ignore */ }
        apply();
    }

    function activeMission() {
        let key = null;
        try { key = localStorage.getItem(MISSION_KEY); } catch (error) { /* ignore */ }
        return MISSIONS[key] ? key : null;
    }

    function enter(missionKey) {
        if (!MISSIONS[missionKey]) return;
        try { localStorage.setItem(MISSION_KEY, missionKey); } catch (error) { /* ignore */ }
        apply();
    }

    function exit() {
        try { localStorage.removeItem(MISSION_KEY); } catch (error) { /* ignore */ }
        apply();
    }

    function currentView() {
        const active = document.querySelector('.nav-tab.active[data-view]');
        return active ? active.dataset.view : null;
    }

    function renderStepBadges(missionTabs) {
        ALL_VIEWS.forEach((view) => {
            const tab = document.getElementById(`nav-tab-${view}`);
            if (!tab) return;
            const existing = tab.querySelector('.nav-step-badge');
            const step = missionTabs ? missionTabs.indexOf(view) : -1;
            if (step === -1) {
                if (existing) existing.remove();
                return;
            }
            if (existing) {
                existing.textContent = String(step + 1);
                return;
            }
            const badge = document.createElement('span');
            badge.className = 'nav-step-badge';
            badge.setAttribute('aria-hidden', 'true');
            badge.textContent = String(step + 1);
            tab.insertBefore(badge, tab.firstChild);
        });
    }

    function renderChip(missionKey) {
        const chip = document.getElementById('nav-mission-chip');
        if (!chip) return;
        if (!missionKey) {
            chip.hidden = true;
            return;
        }
        const mission = MISSIONS[missionKey];
        const label = document.getElementById('nav-mission-chip-label');
        if (label) label.textContent = t(mission.labelKey, mission.fallback);
        chip.hidden = false;
    }

    function apply() {
        const missionKey = activeMission();
        const visible = missionKey ? MISSIONS[missionKey].tabs.slice() : baseTabs();
        const active = currentView();
        if (active && !visible.includes(active)) visible.push(active);

        ALL_VIEWS.forEach((view) => {
            const tab = document.getElementById(`nav-tab-${view}`);
            if (tab) tab.classList.toggle('nav-tab-tucked', !visible.includes(view));
        });

        document.querySelectorAll('[data-mirror-view]').forEach((mirror) => {
            const view = mirror.dataset.mirrorView;
            // Only mirrors for tabs THIS module tucks. A view outside ALL_VIEWS
            // (Reverse Prompt) has its direct tab hidden by the width ladder
            // instead, so `visible` says nothing about whether its tab is on
            // screen — and because the active view is pushed into `visible`,
            // hiding on that basis removed the open view's only remaining entry
            // from the menu the user had just used to get there. That mirror's
            // own ladder rule owns its visibility.
            if (!ALL_VIEWS.includes(view)) return;
            mirror.hidden = visible.includes(view);
        });

        renderStepBadges(missionKey ? MISSIONS[missionKey].tabs : null);
        renderChip(missionKey);

        // The width-degradation ladder re-measures on resize (app.js binds it
        // there); tab visibility changes shift scrollWidth the same way.
        window.dispatchEvent(new Event('resize'));
    }

    // ------------------------------------------------------------------
    // Customize modal (自定义标签栏)
    // ------------------------------------------------------------------

    function syncCustomizeChecks() {
        const tabs = baseTabs();
        document.querySelectorAll('#nav-customize-modal [data-custom-view]').forEach((box) => {
            box.checked = tabs.includes(box.dataset.customView);
        });
    }

    function openCustomize() {
        const modal = document.getElementById('nav-customize-modal');
        if (!modal) return;
        syncCustomizeChecks();
        modal.classList.add('visible');
    }

    function closeCustomize() {
        const modal = document.getElementById('nav-customize-modal');
        if (modal) modal.classList.remove('visible');
    }

    function collectCustomizeSelection() {
        const picked = LOCKED_TABS.slice();
        CUSTOM_VIEWS.forEach((view) => {
            const box = document.querySelector(`#nav-customize-modal [data-custom-view="${view}"]`);
            if (box && box.checked) picked.push(view);
        });
        // Prompt Helper / Style Finder are not in the checklist: carry their
        // current membership over so saving never silently drops them.
        baseTabs().forEach((view) => {
            if (!picked.includes(view) && !CUSTOM_VIEWS.includes(view)) picked.push(view);
        });
        return picked;
    }

    function wireCustomize() {
        const opener = document.getElementById('nav-tools-customize');
        if (opener) {
            opener.addEventListener('click', () => {
                if (typeof window._closeNavToolsMenu === 'function') window._closeNavToolsMenu();
                openCustomize();
            });
        }
        const modal = document.getElementById('nav-customize-modal');
        if (!modal) return;
        modal.querySelectorAll('[data-custom-view]').forEach((box) => {
            box.addEventListener('change', () => setBaseTabs(collectCustomizeSelection()));
        });
        const reset = document.getElementById('nav-customize-reset');
        if (reset) {
            reset.addEventListener('click', () => {
                setBaseTabs(DEFAULT_TABS.slice());
                syncCustomizeChecks();
            });
        }
        const close = document.getElementById('nav-customize-close');
        if (close) close.addEventListener('click', closeCustomize);
        const backdrop = modal.querySelector('.modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', closeCustomize);
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && modal.classList.contains('visible')) {
                event.stopPropagation();
                closeCustomize();
            }
        }, true);
    }

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------

    function wire() {
        document.querySelectorAll('[data-mirror-view]').forEach((mirror) => {
            mirror.addEventListener('click', () => {
                const tab = document.getElementById(`nav-tab-${mirror.dataset.mirrorView}`);
                if (tab) tab.click();
                if (typeof window._closeNavToolsMenu === 'function') window._closeNavToolsMenu();
            });
        });

        const exitButton = document.getElementById('nav-mission-exit');
        if (exitButton) exitButton.addEventListener('click', exit);

        // Contextual reveal: any tab activation (direct click, entry-page
        // navigate, mirror proxy) may change the active view — re-apply on
        // the next frame so the new view's tab is shown.
        document.addEventListener('click', (event) => {
            if (event.target && event.target.closest && event.target.closest('.nav-tab[data-view]')) {
                window.requestAnimationFrame(apply);
            }
        });

        window.addEventListener('languageChanged', () => renderChip(activeMission()));

        wireCustomize();
        apply();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }

    window.NavMissions = { enter, exit, activeMission, apply, baseTabs, setBaseTabs, MISSIONS };
})();
