/**
 * SD Image Sorter - Keyboard Shortcuts Panel
 */
const KeyboardShortcutsPanel = (function () {
    const PANEL_ID = 'keyboard-shortcuts-panel';
    const SHORTCUTS = {
        global: [
            { key: '?', descKey: 'shortcuts.global.help', fallback: 'Open the keyboard shortcuts panel.' },
            { key: 'Esc', descKey: 'shortcuts.global.escape', fallback: 'Close a modal or cancel the current action.' },
        ],
        gallery: [
            { key: 'Arrow Left/Right', descKey: 'shortcuts.gallery.navigate', fallback: 'Navigate images in the detail modal.' },
            { key: 'Space', descKey: 'shortcuts.gallery.selection', fallback: 'Toggle selection mode.' },
            { key: 'R', descKey: 'shortcuts.gallery.random', fallback: 'Show a random image.' },
        ],
        manual: [
            { key: 'W/A/S/D', descKey: 'shortcuts.manual.move', fallback: 'Send the image to one of the four folders.' },
            { key: 'Space', descKey: 'shortcuts.manual.skip', fallback: 'Skip the current image.' },
            { key: 'Z', descKey: 'shortcuts.manual.undo', fallback: 'Undo the last move.' },
        ],
        censor: [
            { key: 'B', descKey: 'shortcuts.censor.brush', fallback: 'Switch to the brush tool.' },
            { key: 'E', descKey: 'shortcuts.censor.eraser', fallback: 'Switch to the eraser tool.' },
            { key: 'C', descKey: 'shortcuts.censor.clone', fallback: 'Switch to clone mode.' },
        ],
    };

    let isPanelOpen = false;
    let panelElement = null;

    function t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            const translated = window.I18n.t(key);
            if (translated && translated !== key) {
                return translated;
            }
        }
        return fallback || key;
    }

    function getSectionLabel(section) {
        const labels = {
            global: { key: 'shortcuts.section.global', fallback: 'Global', icon: '🌐' },
            gallery: { key: 'shortcuts.section.gallery', fallback: 'Gallery', icon: '🖼️' },
            manual: { key: 'shortcuts.section.manual', fallback: 'Manual Sort', icon: '🎮' },
            censor: { key: 'shortcuts.section.censor', fallback: 'Censor Edit', icon: '🔳' },
        };
        const label = labels[section] || { key: section, fallback: section, icon: '⌨️' };
        return `${label.icon} ${t(label.key, label.fallback)}`;
    }

    function renderPanel(panel) {
        let bodyHtml = '';
        for (const [section, items] of Object.entries(SHORTCUTS)) {
            bodyHtml += `<div class="shortcuts-group"><h4 class="shortcuts-group-title">${getSectionLabel(section)}</h4><ul class="shortcuts-list">`;
            bodyHtml += items.map((shortcut) => (
                `<li class="shortcut-item"><span class="shortcut-key">${shortcut.key}</span><span class="shortcut-description">${t(shortcut.descKey, shortcut.fallback)}</span></li>`
            )).join('');
            bodyHtml += '</ul></div>';
        }

        panel.innerHTML = `
            <div class="shortcuts-panel-content">
                <div class="shortcuts-panel-header">
                    <h3>⌨️ ${t('shortcuts.title', 'Keyboard Shortcuts')}</h3>
                    <button class="shortcuts-panel-close" aria-label="${t('common.close', 'Close')}">&times;</button>
                </div>
                <div class="shortcuts-panel-body">${bodyHtml}</div>
                <div class="shortcuts-panel-footer">
                    <p class="shortcuts-hint">${t('shortcuts.hint', 'Press ? any time to open this panel.')}</p>
                </div>
            </div>`;

        panel.querySelector('.shortcuts-panel-close').addEventListener('click', hide);
        panel.addEventListener('click', function (e) {
            if (e.target === panel) hide();
        });
    }

    function createPanel() {
        if (panelElement) return panelElement;
        const panel = document.createElement('div');
        panel.id = PANEL_ID;
        panel.className = 'keyboard-shortcuts-panel';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');
        renderPanel(panel);
        document.body.appendChild(panel);
        panelElement = panel;
        return panel;
    }

    function show() {
        if (isPanelOpen) return;
        createPanel();
        renderPanel(panelElement);
        panelElement.classList.add('visible');
        isPanelOpen = true;
        document.addEventListener('keydown', handleKeydown);
    }

    function hide() {
        if (!isPanelOpen || !panelElement) return;
        panelElement.classList.remove('visible');
        isPanelOpen = false;
        document.removeEventListener('keydown', handleKeydown);
    }

    function handleKeydown(e) {
        if (e.key === 'Escape') hide();
    }

    function showGuideForCurrentTab() {
        if (!window.Guide || typeof window.Guide.show !== 'function') return false;
        const currentTab = typeof window.Guide.getCurrentTab === 'function'
            ? window.Guide.getCurrentTab()
            : (window.App && window.App.AppState && window.App.AppState.currentView) || 'gallery';
        // Propagate whether the guide actually opened. When a tab has no guide
        // copy, Guide.show() returns false and the caller falls back to the
        // keyboard-shortcuts panel instead of silently doing nothing.
        return window.Guide.show(currentTab) === true;
    }

    function init() {
        document.addEventListener('keydown', function (e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.key === '?' && !isPanelOpen) {
                e.preventDefault();
                show();
            }
        });

        document.addEventListener('languageChanged', function () {
            if (panelElement) {
                renderPanel(panelElement);
            }
        });

        const helpBtn = document.getElementById('btn-help');
        if (helpBtn) {
            helpBtn.addEventListener('click', function () {
                if (!showGuideForCurrentTab()) {
                    show();
                }
            });
        }

        // Mobile mirror of the same help action. The desktop nav-actions row
        // is hidden by CSS at <=768px viewports, so the only way for users on
        // narrow screens / split-screen / tablet to reach the guide is via
        // the hamburger menu.
        const mobileHelpBtn = document.getElementById('mobile-btn-help');
        if (mobileHelpBtn) {
            mobileHelpBtn.addEventListener('click', function () {
                // Close the mobile nav so the guide modal is not stacked
                // underneath the hamburger overlay.
                var overlay = document.getElementById('mobile-nav-overlay');
                if (overlay) {
                    overlay.classList.remove('visible');
                    document.body.style.overflow = '';
                }
                if (!showGuideForCurrentTab()) {
                    show();
                }
            });
        }
    }

    return { init, show, hide, SHORTCUTS };
})();

document.addEventListener('DOMContentLoaded', KeyboardShortcutsPanel.init);
window.KeyboardShortcutsPanel = KeyboardShortcutsPanel;
