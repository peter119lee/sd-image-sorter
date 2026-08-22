/**
 * SD Image Sorter - Collapsible gallery sidebar sections
 *
 * v3.3.2 UI: the gallery filter sidebar grew several stacked blocks
 * (Filters summary, Folders tree, Collections) and felt bulky. This turns
 * each `[data-sidebar-section]` into a collapsible accordion: clicking the
 * section header toggles its body, the caret rotates, and the collapsed
 * state persists per-section in localStorage so the layout the user shaped
 * survives reloads. Action buttons in the header (🔍 / ⟳ / ＋) keep working
 * because they live outside the toggle button.
 */
(function () {
    'use strict';

    const STORAGE_PREFIX = 'sidebar_section_collapsed_';

    function storageKey(id) {
        return STORAGE_PREFIX + id;
    }

    function readCollapsed(id) {
        try {
            const raw = localStorage.getItem(storageKey(id));
            if (raw === '1') return true;
            if (raw === '0') return false;
        } catch (error) {
            /* private mode — fall through to the default */
        }
        // Default: collapse the All/None filter summary so Folders is first.
        return id === 'filters';
    }

    function writeCollapsed(id, collapsed) {
        try {
            localStorage.setItem(storageKey(id), collapsed ? '1' : '0');
        } catch (error) {
            /* private mode / quota — collapse still works for the session */
        }
    }

    function applyState(section, collapsed) {
        section.classList.toggle('is-collapsed', collapsed);
        const toggle = section.querySelector('.sidebar-section-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }

    function init() {
        const sections = document.querySelectorAll('.filter-sidebar [data-sidebar-section]');
        sections.forEach((section) => {
            const id = section.getAttribute('data-sidebar-section');
            if (!id) return;
            applyState(section, readCollapsed(id));

            const toggle = section.querySelector('.sidebar-section-toggle');
            if (!toggle) return;
            toggle.addEventListener('click', () => {
                const next = !section.classList.contains('is-collapsed');
                applyState(section, next);
                writeCollapsed(id, next);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.SidebarSections = { init };
})();
