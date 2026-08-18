/**
 * Reverse Prompt — drop one image, get a prompt, and always say where it came
 * from. Family base; MUST LOAD FIRST (the other reverse-prompt/*.js files
 * Object.assign onto the object declared here).
 *
 * Why this view is not just another interrogator
 * ----------------------------------------------
 * "Drop an image, get tags" is ubiquitous. AUTOMATIC1111 ships
 * modules/interrogate.py and modules/deepbooru.py in core, Forge inherits them,
 * and ComfyUI-WD14-Tagger covers the other ecosystem. Rebuilding that surface
 * buys nothing.
 *
 * What none of them does is read the image's OWN metadata first. This app
 * already parses ComfyUI, NovelAI, WebUI/A1111, Forge and WebP EXIF/XMP, so the
 * order of operations here is the whole point:
 *
 *   1. Intake reads the file. If it recorded a prompt, that IS the answer, and
 *      it is a record of what generated the picture.
 *   2. Only when there is nothing to read does anything infer, and that result
 *      is labelled a guess.
 *
 * The two are never merged into one box and inference never overwrites a
 * record. A guess presented as a record wastes the user's time in a way they
 * cannot detect, because the next thing they do is paste it into a generator.
 * Comparing the two is genuinely useful, so it is offered — beside the record,
 * with its own framing.
 *
 * Nothing on this page writes to the library. Intake keeps the upload in the
 * Reader's temp directory (24 h TTL) and every engine reached from here is
 * keyed on a filesystem path, not an images.id.
 */
'use strict';

window.ReversePrompt = {
    /** Everything the page knows about the one image currently loaded. */
    state: {
        sourcePath: '',   // the retained upload path POST /api/parse-image returns
        fileName: '',
        recorded: null,   // { prompt, negative, generator } when the file carries one
        inferred: null,   // the record the adapter last resolved
        running: false,
        jobId: '',
        cancelRequested: false,
        abort: null,      // the in-flight AbortController for the un-polled mode
    },

    /** Bilingual literal fallback behind a locale key, matching the house helper. */
    _t(key, en, zh, params) {
        let text = en;
        const translated = window.I18n?.t?.(key, params);
        if (translated && translated !== key) {
            text = translated;
        } else {
            try {
                const lang = window.I18n?.getLang?.() || document.documentElement.lang || '';
                if (String(lang).toLowerCase().startsWith('zh') && zh) text = zh;
            } catch (_error) { /* keep the English fallback */ }
            for (const [name, value] of Object.entries(params || {})) {
                text = String(text).split(`{${name}}`).join(String(value));
            }
        }
        return text;
    },

    _el(id) {
        return document.getElementById(id);
    },

    /** The mode radio group's current value; the grounded mode is the default. */
    currentMode() {
        const picked = document.querySelector('input[name="reverse-mode"]:checked');
        return picked ? String(picked.value) : 'grounded';
    },

    /** The target model this page is writing a prompt FOR, or '' for none. */
    currentTarget() {
        return this._el('reverse-target-model')?.value || '';
    },

    /**
     * A tag list rendered as prompt text. Underscores become spaces because
     * every generator in this family wants the spaced form, and the tagger
     * reports the booru form.
     */
    tagsToPromptText(tags) {
        const names = (tags || [])
            .map((entry) => String(typeof entry === 'string' ? entry : entry?.tag || '').trim())
            .filter(Boolean)
            .map((tag) => (/^score_\d/.test(tag) ? tag : tag.replace(/_/g, ' ')));
        return [...new Set(names)].join(', ');
    },

    /** Split a comma-joined booru string back into tags, for the TIPO input. */
    promptTextToTags(text) {
        return String(text || '')
            .split(',')
            .map((part) => part.trim())
            .filter(Boolean);
    },

    /** Dynamic text must claim the i18n lock or the #app observer resets it. */
    _lockedText(element, text) {
        if (!element) return element;
        element.dataset.i18nLocked = '1';
        element.textContent = text;
        return element;
    },

    _setStatus(text, kind) {
        const status = this._el('reverse-status');
        if (!status) return;
        this._lockedText(status, text || '');
        status.className = `reverse-status${kind ? ` is-${kind}` : ''}`;
        status.hidden = !text;
    },
};
