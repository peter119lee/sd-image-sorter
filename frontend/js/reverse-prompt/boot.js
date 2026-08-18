/**
 * reverse-prompt/boot.js — wiring; LOADS LAST in the family.
 *
 * `switchView('reverse')` calls `init()`, which is idempotent: the view is a
 * classic script family in one shared scope, and the tab can be revisited any
 * number of times.
 */
'use strict';

Object.assign(window.ReversePrompt, {
    _bound: false,

    init() {
        this._attachDropZone();
        if (!this._bound) {
            this._bound = true;
            this._el('btn-reverse-run')?.addEventListener('click', () => this.run());
            this._el('btn-reverse-cancel')?.addEventListener('click', () => this.cancel());
            this._el('btn-reverse-tipo')?.addEventListener('click', () => this.suggestTipo());
            this._el('reverse-draft')?.addEventListener('input', () => this.renderTipo());
            this._el('reverse-target-model')?.addEventListener('change', () => this.renderTipo());
            document.querySelectorAll('input[name="reverse-mode"]').forEach((radio) => {
                radio.addEventListener('change', () => this.renderRunButtons());
            });
            // Re-render the dynamic text this page owns: everything on screen is
            // written with the i18n lock held, so a language change would
            // otherwise leave it in the previous language.
            document.addEventListener('languageChanged', () => {
                if (!this.state.sourcePath) {
                    this.renderEmpty();
                    return;
                }
                this.renderRecorded(this.state.recorded);
                this.renderInferred(this.state.inferred);
                this.renderRunButtons();
                this.renderTipo();
            });
        }
        if (!this.state.sourcePath) {
            this.renderEmpty();
        } else {
            this.renderRunButtons();
            this.renderTipo();
        }
    },
});

window.initReversePrompt = () => window.ReversePrompt.init();
