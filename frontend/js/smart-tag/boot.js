/**
 * smart-tag/boot.js — smart-tag.js decomposition (LOADS LAST).
 * Extracted VERBATIM from frontend/js/smart-tag.js pre-split lines
 * 1099-1245: bindHandlers kept as ONE block (modal controls, the
 * open-VLM-settings button — intentionally duplicated with
 * smart-tag/ollama-banner.js, do NOT DRY — and the Tag-modal hint +
 * MutationObserver), the document.readyState DOM-ready gate that fires
 * bindHandlers exactly once, openUnscoped/openScoped and the single
 * window.SmartTag = {open, openScoped, close, run, cancel} publish.
 * Classic script: the ONLY family file with top-level execution, so its
 * tag must come LAST — every cross-file callee is already defined.
 * Family renames applied ($ -> smartTag$, closeModal ->
 * closeSmartTagModal).
 */
'use strict';
    function bindHandlers() {
        const closeBtn = smartTag$('#btn-smart-tag-close');
        if (closeBtn) closeBtn.addEventListener('click', closeSmartTagModal);
        const cancelModalBtn = smartTag$('#btn-smart-tag-cancel-modal');
        if (cancelModalBtn) cancelModalBtn.addEventListener('click', closeSmartTagModal);

        const runBtn = smartTag$('#btn-smart-tag-run');
        if (runBtn) runBtn.addEventListener('click', runSmartTag);

        const cancelBtn = smartTag$('#btn-smart-tag-cancel-job');
        if (cancelBtn) cancelBtn.addEventListener('click', cancelSmartTag);

        smartTag$('#smart-tag-tagger-1')?.addEventListener('change', () => {
            applyThresholdDefaults(smartTag$('#smart-tag-tagger-1')?.value || '', { force: false });
            applyMaxTagsDefault([
                smartTag$('#smart-tag-tagger-1')?.value || '',
                smartTag$('#smart-tag-tagger-2')?.value || '',
            ].filter(Boolean), { force: false });
            syncSmartTagVoteUi();
            updateAllTaggerHelp();
        });
        smartTag$('#smart-tag-tagger-2')?.addEventListener('change', () => {
            applyMaxTagsDefault([
                smartTag$('#smart-tag-tagger-1')?.value || '',
                smartTag$('#smart-tag-tagger-2')?.value || '',
            ].filter(Boolean), { force: false });
            syncSmartTagVoteUi();
            updateAllTaggerHelp();
        });
        smartTag$('#smart-tag-consensus-mode')?.addEventListener('change', syncSmartTagVoteUi);
        smartTag$('#smart-tag-enable-wd14')?.addEventListener('change', syncSmartTagVoteUi);
        smartTag$('#smart-tag-enable-vlm')?.addEventListener('change', () => {
            syncSmartTagVoteUi();
            refreshOllamaWarning();
        });
        smartTag$('#smart-tag-nl-mode')?.addEventListener('change', () => {
            syncSmartTagVoteUi();
            refreshOllamaWarning();
        });
        ['#smart-tag-general-threshold', '#smart-tag-character-threshold', '#smart-tag-copyright-threshold'].forEach((selector) => {
            const input = smartTag$(selector);
            if (input) input.addEventListener('input', () => { input.dataset.userTouched = 'true'; });
        });
        smartTag$('#smart-tag-max-tags')?.addEventListener('input', (event) => {
            event.currentTarget.dataset.userTouched = 'true';
        });
        smartTag$('#btn-smart-tag-vlm-settings')?.addEventListener('click', () => {
            if (typeof window.App?.openVlmSettings === 'function') {
                window.App.openVlmSettings();
            } else {
                document.getElementById('btn-vlm-settings')?.click();
            }
        });

        // Click-outside on the backdrop closes the modal too.
        const modal = smartTag$('#smart-tag-modal');
        if (modal) {
            const backdrop = modal.querySelector('.modal-backdrop');
            if (backdrop) backdrop.addEventListener('click', closeSmartTagModal);
        }

        // -------- Tag Images modal Smart Tag entry --------
        // Keep Smart Tag available inside #tag-modal instead of sending users
        // to Dataset Maker first. Dismiss only hides the helper copy for this
        // returning user; the Gallery AI Auto Tagging workflow remains intact.
        const HINT_DISMISSED_KEY = 'sd-image-sorter-tag-modal-smart-tag-hint-dismissed';

        const tagModal = document.getElementById('tag-modal');
        const hintBanner = document.getElementById('tag-modal-smart-tag-hint');
        if (tagModal && hintBanner) {
            const isDismissed = () => {
                try { return localStorage.getItem(HINT_DISMISSED_KEY) === '1'; }
                catch { return false; }
            };
            const refreshHintVisibility = () => {
                hintBanner.classList.toggle('is-dismissed', isDismissed());
            };
            // Initial state.
            refreshHintVisibility();

            // Re-evaluate every time the Tag modal becomes visible (because
            // the canonical project class for "open" is .visible, applied
            // via window.showModal). We use a MutationObserver instead of
            // forking showModal to keep this self-contained.
            const obs = new MutationObserver(() => {
                if (tagModal.classList.contains('visible')) {
                    refreshHintVisibility();
                }
            });
            obs.observe(tagModal, { attributes: true, attributeFilter: ['class'] });

            const goBtn = document.getElementById('btn-tag-modal-smart-tag-go');
            if (goBtn) {
                goBtn.addEventListener('click', () => {
                    if (typeof window.hideModal === 'function') {
                        try { window.hideModal('tag-modal'); } catch (_e) {}
                    }
                    setTimeout(() => {
                        try { openModal(); } catch (_e) {}
                    }, 120);
                });
            }
            const dismissBtn = document.getElementById('btn-tag-modal-smart-tag-dismiss');
            if (dismissBtn) {
                dismissBtn.addEventListener('click', () => {
                    try { localStorage.setItem(HINT_DISMISSED_KEY, '1'); } catch {}
                    hintBanner.classList.add('is-dismissed');
                });
            }
        }
    }

    // Defer binding until the DOM is ready (this script may load
    // before or after DOMContentLoaded depending on script order).
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindHandlers, { once: true });
    } else {
        bindHandlers();
    }

    // Public hooks for other modules. Color Analysis sends images to Dataset
    // Maker from the gallery; the live opener is window.SmartTag.open.
    // Plain open: clear any stale one-shot scope so callers that mean
    // "whatever is queued now" (e.g. Dataset Maker) are not poisoned by a
    // previous Gallery-armed open.
    function openUnscoped() {
        pendingExplicitScope = null;
        return openModal();
    }

    // Aurora Phase 3 (#25b): open with an explicit Gallery selection scope.
    function openScoped(scope) {
        const ids = scope && Array.isArray(scope.imageIds)
            ? scope.imageIds.map((n) => Number(n)).filter((n) => Number.isFinite(n) && n > 0)
            : [];
        pendingExplicitScope = ids.length ? { imageIds: ids } : null;
        return openModal();
    }

    window.SmartTag = {
        open: openUnscoped,
        openScoped,
        close: closeSmartTagModal,
        run: runSmartTag,
        cancel: cancelSmartTag,
    };
