/**
 * guide/engine.js — guide.js decomposition (the runtime; loads
 * SECOND). Extracted VERBATIM from frontend/js/guide.js pre-split
 * lines 554-1076 (of 1,085): the single mutable `Guide` object
 * literal — singleton state (_modalEl, _styleEl, _initialized,
 * _openTab, _returnFocusEl, plus the dynamically-created _escHandler),
 * getCurrentTab (view + sorting sub-view routing), the
 * lang/copy/tab/escape helpers, _injectStyles (#guide-system-styles),
 * the section + shortcut renderers (everything HTML-escaped),
 * _ensureModal (overlay DOM + close/backdrop/action/Tour handlers —
 * the Tour handler checks window.OnboardingTour but calls the bare
 * OnboardingTour lexical binding), show/hide (focus capture/restore,
 * focus trap, Escape attach/remove; show returns exactly `false` for
 * missing copy — the keyboard-shortcuts.js fallback bridge),
 * refreshTranslations, the inline-button lifecycle (localStorage
 * 'guide-visited' first-visit pulse) and the idempotent init. Every
 * method uses `this`; guide/boot.js publishes this one object as
 * window.Guide (identity-preserving, not sealed, not frozen — all
 * pinned by tests/e2e/specs/guide-pins.spec.ts). Classic script:
 * `const Guide` is a shared global lexical binding; the data consts
 * from guide/copy.js resolve via the scope chain at call time.
 * 'use strict' added per-file (the original IIFE was strict
 * throughout); everything below the directive is byte-identical to
 * the pre-split file.
 */
'use strict';

    const Guide = {
        _modalEl: null,
        _styleEl: null,
        _initialized: false,
        _openTab: null,
        _returnFocusEl: null,

        getCurrentTab() {
            const currentView = window.App?.AppState?.currentView
                || document.querySelector('.view.active')?.id?.replace(/^view-/, '')
                || 'gallery';

            if (currentView === 'sorting') {
                const activeSub = document.querySelector('.sorting-sub-tab.active')?.getAttribute('data-sorting-sub');
                return activeSub === 'manual' ? 'manual' : 'autosep';
            }

            return currentView;
        },

        _lang() {
            return window.I18n?.getLang?.() === 'zh-CN' ? 'zh-CN' : 'en';
        },

        _copy() {
            return GUIDE_COPY[this._lang()];
        },

        _tab(tabName) {
            return this._copy().tabs[tabName];
        },

        _escape(value) {
            return String(value ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },

        _injectStyles() {
            if (this._styleEl) return;

            const style = document.createElement('style');
            style.id = 'guide-system-styles';
            style.textContent = `
.guide-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-left: auto;
    padding: 6px 10px;
    min-width: 32px;
    border-radius: var(--r-ctl, 4px);
    border: 1px solid var(--border-strong, #3A3A3E);
    background: var(--surface-2, #1C1C1E);
    color: var(--text-2, #A6A6AB);
    font-size: 14px;
    cursor: pointer;
    transition: all 160ms ease;
}
.guide-inline-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
}
.guide-btn:hover {
    background: var(--surface-3, #232326);
    border-color: var(--border-strong, #3A3A3E);
    color: var(--text, #E8E8EA);
}
/* First-visit hint. This was a looping pulse animation; motion is functional
   only now, so the unseen state is carried by a static accent outline instead. */
.guide-btn--pulse {
    border-color: var(--accent-line, rgba(200, 135, 60, 0.45));
    color: var(--accent, #C8873C);
}

.guide-overlay {
    position: fixed;
    inset: 0;
    z-index: 9100;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 24px;
}
.guide-overlay.visible {
    display: flex;
}
.guide-overlay-backdrop {
    position: absolute;
    inset: 0;
    background: rgba(9, 9, 9, 0.72);
}
.guide-modal {
    position: relative;
    width: min(760px, 100%);
    max-height: min(86vh, 860px);
    display: flex;
    flex-direction: column;
    background: rgba(20, 20, 20, 0.96);
    border: 1px solid rgba(var(--accent-rgb), 0.12);
    border-radius: var(--r-modal, 6px);
    overflow: hidden;
    box-shadow: 0 28px 60px rgba(0, 0, 0, 0.38);
}
.guide-modal-header {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    padding: 22px 24px 18px;
    border-bottom: 1px solid rgba(184, 215, 233, 0.08);
}
.guide-modal-icon {
    font-size: 28px;
    line-height: 1;
}
.guide-modal-title {
    margin: 0;
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary);
}
.guide-modal-subtitle {
    display: block;
    margin-top: 4px;
    font-size: 13px;
    color: var(--text-muted);
}
.guide-modal-close {
    margin-left: auto;
    width: 36px;
    height: 36px;
    border: none;
    border-radius: var(--r-ctl, 4px);
    background: rgba(255,255,255,0.04);
    color: var(--text-secondary);
    cursor: pointer;
}
.guide-modal-body {
    padding: 20px 24px 12px;
    overflow-y: auto;
}
.guide-section {
    padding-bottom: 18px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(184, 215, 233, 0.06);
}
.guide-section:last-child {
    border-bottom: none;
    margin-bottom: 0;
}
.guide-section h4 {
    margin: 0 0 10px;
    color: var(--accent-secondary);
    font-size: 14px;
    font-weight: 700;
}
.guide-section ul {
    margin: 0;
    padding-left: 18px;
    display: grid;
    gap: 8px;
}
.guide-section li {
    color: var(--text-secondary);
    line-height: 1.65;
    font-size: 14px;
}
.guide-modal-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 18px 24px 22px;
    border-top: 1px solid rgba(184, 215, 233, 0.08);
}
.guide-modal-action {
    padding: 10px 16px;
    border-radius: var(--r-ctl, 4px);
    border: 1px solid rgba(var(--accent-rgb), 0.24);
    background: rgba(var(--accent-rgb), 0.12);
    color: #F3E6D6;
    font-weight: 700;
    cursor: pointer;
}
.guide-modal-refresh-i18n {
    padding: 10px 14px;
    border-radius: var(--r-ctl, 4px);
    border: 1px solid rgba(var(--accent-rgb), 0.28);
    background: rgba(var(--accent-rgb), 0.10);
    color: #F3E6D6;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    transition: all 160ms ease;
}
.guide-modal-refresh-i18n:hover {
    background: rgba(var(--accent-rgb), 0.18);
    border-color: rgba(var(--accent-rgb), 0.45);
}
@media (max-width: 520px) {
    .guide-modal-footer {
        flex-direction: column-reverse;
        align-items: stretch;
    }
    .guide-modal-action,
    .guide-modal-refresh-i18n {
        width: 100%;
    }
}
@media (max-width: 768px) {
    .guide-btn {
        margin-left: 0;
        width: fit-content;
    }
    .guide-modal {
        max-height: 90vh;
        border-radius: var(--r-modal, 6px);
    }
    .guide-modal-header,
    .guide-modal-body,
    .guide-modal-footer {
        padding-inline: 18px;
    }
}`;

            document.head.appendChild(style);
            this._styleEl = style;
        },

        _renderSection(title, items) {
            return `
                <section class="guide-section">
                    <h4>${this._escape(title)}</h4>
                    <ul>${items.map((item) => `<li>${this._escape(item)}</li>`).join('')}</ul>
                </section>
            `;
        },

        _renderShortcutsSection(tabName) {
            const lang = this._lang();
            const data = TAB_SHORTCUTS[lang] || TAB_SHORTCUTS.en;
            const globalKeys = data.global || [];
            const tabKeys = data[tabName] || [];
            const allKeys = [...tabKeys, ...globalKeys];
            if (allKeys.length === 0) return '';

            const rows = allKeys.map((s) =>
                `<span class="guide-shortcut-key">${this._escape(s.key)}</span><span class="guide-shortcut-desc">${this._escape(s.desc)}</span>`
            ).join('');

            return `
                <section class="guide-section">
                    <h4>⌨️ ${this._escape(data.sectionTitle)}</h4>
                    <div class="guide-shortcuts-grid">${rows}</div>
                </section>
            `;
        },

        _ensureModal() {
            if (this._modalEl) return;

            const overlay = document.createElement('div');
            overlay.className = 'guide-overlay';
            overlay.id = 'guide-overlay';
            overlay.innerHTML = `
                <div class="guide-overlay-backdrop"></div>
                <div class="guide-modal" role="dialog" aria-modal="true" aria-labelledby="guide-modal-title">
                    <div class="guide-modal-header">
                        <span class="guide-modal-icon" aria-hidden="true"></span>
                        <div>
                            <h3 class="guide-modal-title" id="guide-modal-title"></h3>
                            <span class="guide-modal-subtitle"></span>
                        </div>
                        <button type="button" class="guide-modal-close" aria-label="Close"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button>
                    </div>
                    <div class="guide-modal-body"></div>
                    <div class="guide-modal-footer">
                        <button type="button" class="guide-modal-tour" title="Restart onboarding tour"><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg> Tour</button>
                        <button type="button" class="guide-modal-action"></button>
                    </div>
                </div>
            `;

            document.body.appendChild(overlay);
            this._modalEl = overlay;

            const hide = () => this.hide();
            overlay.querySelector('.guide-overlay-backdrop').addEventListener('click', hide);
            overlay.querySelector('.guide-modal-close').addEventListener('click', hide);
            overlay.querySelector('.guide-modal-action').addEventListener('click', hide);
            overlay.querySelector('.guide-modal-tour').addEventListener('click', () => {
                hide();
                if (window.OnboardingTour) {
                    OnboardingTour.resetState();
                    OnboardingTour.start();
                }
            });
        },

        show(tabName) {
            const copy = this._copy();
            const tab = this._tab(tabName);
            // Return false (not undefined) so callers can fall back to the
            // keyboard-shortcuts panel when a tab has no guide copy.
            if (!tab) return false;

            this._ensureModal();
            this._openTab = tabName;

            const modal = this._modalEl;
            if (!modal.classList.contains('visible')) {
                this._returnFocusEl = document.activeElement instanceof HTMLElement
                    ? document.activeElement
                    : null;
            }
            modal.querySelector('.guide-modal-icon').textContent = tab.icon;
            modal.querySelector('.guide-modal-title').textContent = tab.title;
            modal.querySelector('.guide-modal-subtitle').textContent = copy.subtitle;
            const closeButton = modal.querySelector('.guide-modal-close');
            const tourButton = modal.querySelector('.guide-modal-tour');
            const actionButton = modal.querySelector('.guide-modal-action');
            closeButton.setAttribute('aria-label', copy.closeAria);
            tourButton.textContent = copy.tour;
            tourButton.title = copy.tourTitle;
            actionButton.textContent = copy.close;

            modal.querySelector('.guide-modal-body').innerHTML = [
                this._renderSection(copy.sections.purpose, tab.purpose),
                this._renderSection(copy.sections.steps, tab.steps),
                this._renderSection(copy.sections.features, tab.features),
                this._renderSection(copy.sections.tips, tab.tips),
                this._renderShortcutsSection(tabName),
            ].join('');

            modal.classList.add('visible');
            actionButton.focus();

            if (this._escHandler) {
                document.removeEventListener('keydown', this._escHandler, true);
            }
            this._escHandler = (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.hide();
                    return;
                }
                if (event.key !== 'Tab') return;

                const focusableControls = Array.from(modal.querySelectorAll('button:not([disabled])'))
                    .filter((element) => element instanceof HTMLElement && element.offsetParent !== null);
                if (focusableControls.length === 0) return;

                const firstControl = focusableControls[0];
                const lastControl = focusableControls[focusableControls.length - 1];
                if (event.shiftKey && document.activeElement === firstControl) {
                    event.preventDefault();
                    lastControl.focus();
                } else if (!event.shiftKey && document.activeElement === lastControl) {
                    event.preventDefault();
                    firstControl.focus();
                }
            };
            document.addEventListener('keydown', this._escHandler, true);
            return true;
        },

        hide() {
            if (!this._modalEl) return;
            this._modalEl.classList.remove('visible');
            this._openTab = null;
            if (this._escHandler) {
                document.removeEventListener('keydown', this._escHandler, true);
                this._escHandler = null;
            }
            const returnFocusEl = this._returnFocusEl;
            this._returnFocusEl = null;
            if (returnFocusEl instanceof HTMLElement && returnFocusEl.isConnected) {
                returnFocusEl.focus({ preventScroll: true });
            }
        },

        /**
         * Manually re-fetch lang/*.js + guide-translations.js without a page
         * reload. State (gallery filters, selection, scan progress, modal
         * positions, etc.) is fully preserved because we never touch
         * localStorage and never call location.reload(). This is the manual
         * fallback for users who keep their browser tab open across an app
         * upgrade and never bother to F5.
         *
         * The normal F5 path is already handled server-side: GET / injects
         * ?v=APP_VERSION onto every /static/*.js URL, so a regular refresh
         * after upgrading the backend will pull the fresh language packs.
         */
        async refreshTranslations() {
            const copy = this._copy();
            const buster = '_t=' + Date.now();
            const scripts = [
                '/static/js/lang/en.js?' + buster,
                '/static/js/lang/zh-CN.js?' + buster,
                '/static/js/guide-translations.js?' + buster,
            ];

            const loadOne = (url) => new Promise((resolve, reject) => {
                const tag = document.createElement('script');
                tag.src = url;
                tag.async = false;
                tag.onload = () => resolve(url);
                tag.onerror = () => reject(new Error('Failed to load ' + url));
                document.head.appendChild(tag);
            });

            try {
                for (const url of scripts) {
                    await loadOne(url);
                }
                if (window.I18n && window.I18n.translations) {
                    if (window.I18nLang_en) {
                        window.I18n.translations['en'] = window.I18nLang_en;
                    }
                    if (window.I18nLang_zhCN) {
                        window.I18n.translations['zh-CN'] = window.I18nLang_zhCN;
                    }
                    if (typeof window.I18n.applyToDOM === 'function') {
                        window.I18n.applyToDOM();
                    }
                    try {
                        document.dispatchEvent(new CustomEvent('languageChanged', {
                            detail: { lang: window.I18n.currentLang }
                        }));
                    } catch (_e) {
                        // CustomEvent constructor unavailable in extreme environments;
                        // silently skip and rely on applyToDOM above.
                    }
                }
                if (this._openTab && this._modalEl?.classList.contains('visible')) {
                    this.show(this._openTab);
                }
                if (typeof window.showToast === 'function') {
                    window.showToast(copy.refreshI18nDone || 'Translations refreshed.', 'success');
                }
            } catch (err) {
                if (typeof window.showToast === 'function') {
                    window.showToast(copy.refreshI18nFailed || 'Failed to refresh translations.', 'error');
                } else if (window.console && window.console.error) {
                    window.console.error('refreshTranslations failed', err);
                }
            }
        },

        _button(tabName, pulse) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `guide-btn${pulse ? ' guide-btn--pulse' : ''}`;
            button.dataset.guideTab = tabName;
            button.title = this._copy().button;
            button.setAttribute('aria-label', this._copy().button);
            button.innerHTML = `<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-help"/></svg></span>`;
            button.addEventListener('click', () => this.show(tabName));
            return button;
        },

        _mountButtons() {
            let shouldPulse = false;
            try {
                shouldPulse = !localStorage.getItem('guide-visited');
                localStorage.setItem('guide-visited', '1');
            } catch (_error) {
                shouldPulse = false;
            }

            Object.entries(TAB_ANCHORS).forEach(([tabName, selector]) => {
                if (!selector) return;
                if (document.querySelector(`[data-guide-tab="${tabName}"]`)) return;

                const anchor = document.querySelector(selector);
                if (!anchor) return;

                const button = this._button(tabName, shouldPulse);
                if (anchor.matches('.panel-title, .setup-title')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'guide-inline-header';
                    anchor.parentNode.insertBefore(wrapper, anchor);
                    wrapper.appendChild(anchor);
                    wrapper.appendChild(button);
                    return;
                }

                anchor.appendChild(button);
            });
        },

        _refreshButtons() {
            const label = this._copy().button;
            document.querySelectorAll('.guide-btn').forEach((button) => {
                button.title = label;
                button.setAttribute('aria-label', label);
            });

            if (this._openTab && this._modalEl?.classList.contains('visible')) {
                this.show(this._openTab);
            }
        },

        init() {
            if (this._initialized) return;
            this._initialized = true;
            if (window.I18n?.init && !window.I18n._initialized) {
                window.I18n.init();
                window.I18n.applyToDOM?.();
            }
            this._injectStyles();
            this._mountButtons();
            document.addEventListener('languageChanged', () => this._refreshButtons());
        },
    };
