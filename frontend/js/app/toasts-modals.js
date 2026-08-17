/**
 * app/toasts-modals.js — app.js decomposition, stage 2 (leaf sections).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js,
 * pre-split lines 2092-2666. Classic script: shares ONE global lexical
 * environment with app.js and the other app/ parts; index.html loads
 * every app/ file BEFORE app.js, so these top-level declarations stay
 * visible there. No behavior change intended.
 */
// ============== UI Utilities ==============

function $(selector) {
    return document.querySelector(selector);
}

function $$(selector) {
    return document.querySelectorAll(selector);
}

// Recent folders management
const RECENT_FOLDERS_KEY = 'sd-image-sorter-recent-folders';
const MAX_RECENT_FOLDERS = 5;

function getRecentFolders() {
    try {
        const saved = localStorage.getItem(RECENT_FOLDERS_KEY);
        return saved ? JSON.parse(saved) : [];
    } catch (e) { return []; }
}

function addRecentFolder(path) {
    if (!path || typeof path !== 'string') return;
    const folders = getRecentFolders().filter(f => f !== path);
    const updated = [path, ...folders].slice(0, MAX_RECENT_FOLDERS);
    localStorage.setItem(RECENT_FOLDERS_KEY, JSON.stringify(updated));
}

function showToast(message, type = 'info', options = {}) {
    let container = $('#toast-container');

    // Create container if it doesn't exist
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        container.setAttribute('role', 'status');
        container.setAttribute('aria-live', 'polite');
        container.setAttribute('aria-label', 'Notifications');
        document.body.appendChild(container);
    }


    // Deduplicate: skip if identical message+type toast already visible
    const existingToasts = container.querySelectorAll('.toast');
    for (const existing of existingToasts) {
        const existingMsg = existing.querySelector('.toast-message');
        if (existingMsg && existingMsg.textContent === message && existing.classList.contains(type)) {
            return; // Already showing
        }
    }
    // Limit max visible toasts
    while (container.children.length >= 5) {
        container.firstChild.remove();
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'alert');

    const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
    toast.innerHTML = `
        <span class="toast-icon" aria-hidden="true">${icons[type] || 'ℹ'}</span>
        <span class="toast-message"></span>
    `;
    toast.querySelector('.toast-message').textContent = message;

    // Add action button if provided (e.g., Undo)
    if (options.actionLabel && typeof options.onAction === 'function') {
        const actionBtn = document.createElement('button');
        actionBtn.className = 'toast-action-btn';
        actionBtn.textContent = options.actionLabel;
        actionBtn.onclick = (e) => {
            e.stopPropagation();
            options.onAction();
            toast.remove();
        };
        toast.appendChild(actionBtn);
    }

    container.appendChild(toast);

    // Announce to screen readers using A11y module
    if (window.A11y && typeof window.A11y.announce === 'function') {
        const priority = type === 'error' ? 'assertive' : 'polite';
        window.A11y.announce(message, priority);
    }

    const duration = options.duration || 3000;
    const timeoutId = setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(50px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);

    // Allow manual dismissal and cancel timeout
    if (options.dismissible !== false) {
        toast.style.cursor = 'pointer';
        toast.onclick = () => {
            clearTimeout(timeoutId);
            toast.remove();
        };
    }

    return toast;
}

function createGuideOverlay({ id, title, description, steps = [], note = '', maxWidth = '520px', storageKey, closeLabel = 'Got it!' }) {
    const overlay = document.createElement('div');
    overlay.id = id;
    overlay.className = 'first-use-overlay';

    const stepsHtml = steps.length > 0
        ? `<ol class="guide-steps">${steps.map(step => `<li><strong>${escapeHtml(step.title)}</strong><span>${escapeHtml(step.text)}</span></li>`).join('')}</ol>`
        : '';

    const noteHtml = note ? `<p class="guide-note">${escapeHtml(note)}</p>` : '';

    overlay.innerHTML = `
        <div class="guide-backdrop"></div>
        <div class="guide-card" style="--guide-max-width: ${maxWidth};">
            <h3>${escapeHtml(title)}</h3>
            <p class="guide-description">${escapeHtml(description)}</p>
            ${stepsHtml}
            ${noteHtml}
            <button class="btn btn-primary guide-close-btn" data-guide-close="${escapeHtml(id)}">${escapeHtml(closeLabel)}</button>
        </div>
    `;

    overlay.dataset.storageKey = storageKey || '';
    let cleanedUp = false;

    const cleanup = () => {
        if (cleanedUp) return;
        cleanedUp = true;
        document.removeEventListener('keydown', handleEscape);
        removalObserver.disconnect();
    };

    const closeOverlay = () => {
        if (storageKey) {
            localStorage.setItem(storageKey, 'true');
        }
        cleanup();
        overlay.remove();
    };

    const handleEscape = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeOverlay();
        }
    };

    const removalObserver = new MutationObserver(() => {
        if (!overlay.isConnected) {
            cleanup();
        }
    });

    overlay.querySelector('.guide-backdrop')?.addEventListener('click', closeOverlay);
    overlay.querySelector('[data-guide-close]')?.addEventListener('click', closeOverlay);
    document.addEventListener('keydown', handleEscape);
    removalObserver.observe(document.body || document.documentElement, { childList: true, subtree: true });

    return overlay;
}

function copyTextToClipboard(text, successMessage = 'Copied to clipboard') {
    const value = String(text ?? '');
    if (!value) return Promise.resolve(false);

    const fallbackCopy = () => {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', 'true');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        textarea.setSelectionRange(0, textarea.value.length);
        const copied = document.execCommand('copy');
        textarea.remove();
        return copied;
    };

    if (navigator.clipboard?.writeText) {
        return navigator.clipboard.writeText(value)
            .then(() => {
                showToast(successMessage, 'success');
                return true;
            })
            .catch(() => {
                const copied = fallbackCopy();
                if (copied) showToast(successMessage, 'success');
                return copied;
            });
    }

    const copied = fallbackCopy();
    if (copied) showToast(successMessage, 'success');
    return Promise.resolve(copied);
}

// Focus trap for accessibility
let _lastFocusedElement = null;
let _focusTrapHandler = null;

function readWindowScrollPosition() {
    const mainContent = document.getElementById('main-content');
    return {
        x: window.scrollX || document.documentElement.scrollLeft || document.body.scrollLeft || 0,
        y: window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0,
        mainContentX: mainContent?.scrollLeft || 0,
        mainContentY: mainContent?.scrollTop || 0,
    };
}

function restoreWindowScrollPosition(position) {
    if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
    window.scrollTo(position.x, position.y);
    const mainContent = document.getElementById('main-content');
    if (mainContent && Number.isFinite(position.mainContentX) && Number.isFinite(position.mainContentY)) {
        mainContent.scrollLeft = position.mainContentX;
        mainContent.scrollTop = position.mainContentY;
    }
}

function trapFocus(modal) {
    const focusableElements = modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const firstFocusable = focusableElements[0];
    const lastFocusable = focusableElements[focusableElements.length - 1];

    // Remove existing trap if any
    if (_focusTrapHandler) {
        document.removeEventListener('keydown', _focusTrapHandler);
    }

    _focusTrapHandler = (e) => {
        if (e.key !== 'Tab') return;

        if (e.shiftKey) {
            if (document.activeElement === firstFocusable) {
                e.preventDefault();
                lastFocusable.focus();
            }
        } else {
            if (document.activeElement === lastFocusable) {
                e.preventDefault();
                firstFocusable.focus();
            }
        }
    };

    document.addEventListener('keydown', _focusTrapHandler);
}

function releaseFocus() {
    if (_focusTrapHandler) {
        document.removeEventListener('keydown', _focusTrapHandler);
        _focusTrapHandler = null;
    }
    if (_lastFocusedElement) {
        _lastFocusedElement.focus();
        _lastFocusedElement = null;
    }
}

function showModal(modalId) {
    const modal = $(`#${modalId}`);
    if (modal) {
        // Store the element that had focus before opening modal
        _lastFocusedElement = document.activeElement;
        modal._previousWindowScrollPosition = readWindowScrollPosition();
        modal.classList.add('visible');

        // Populate recent folders datalist when scan modal opens
        if (modalId === 'scan-modal') {
            const recentFolders = getRecentFolders();
            const scanInput = document.getElementById('scan-folder-path');
            if (scanInput && recentFolders.length > 0) {
                let datalist = document.getElementById('recent-folders-list');
                if (!datalist) {
                    datalist = document.createElement('datalist');
                    datalist.id = 'recent-folders-list';
                    scanInput.parentNode.appendChild(datalist);
                    scanInput.setAttribute('list', 'recent-folders-list');
                }
                datalist.innerHTML = recentFolders
                    .map(f => '<option value="' + f.replace(/"/g, '&quot;') + '">')
                    .join('');
            }
            syncScanAdvancedUi({ resetToPreference: true });
            resetScanFolderValidation();
        }

        // Load system hardware info when tag modal opens
        if (modalId === 'tag-modal') {
            _tagMinimizedToBackground = false;
            _hideBgTagProgress();
            syncTaggerModelUi({ applyModelDefaults: false, resetAdvancedToPreference: true });
            if (typeof loadSystemInfo === 'function') loadSystemInfo();
        }

        // Set up focus trap
        trapFocus(modal);

        // Add escape key handler to close modal
        const escapeHandler = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                if (modalId === 'tag-modal') {
                    minimizeTaggingToBackground();
                } else if (modalId === 'filter-modal') {
                    closeFilterModal();
                } else {
                    hideModal(modalId);
                }
                document.removeEventListener('keydown', escapeHandler);
            }
        };
        document.addEventListener('keydown', escapeHandler);

        // Store escape handler for cleanup
        modal._escapeHandler = escapeHandler;

        // Focus the close button for accessibility
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            setTimeout(() => closeBtn.focus(), 100);
        }
    }
}

function hideModal(modalId) {
    const modal = $(`#${modalId}`);
    if (modal) {
        const previousWindowScrollPosition = modal._previousWindowScrollPosition;
        // Quick exit animation (non-blocking — remove class immediately for E2E compatibility)
        const content = modal.querySelector('.modal-content');
        if (content) {
            content.style.transition = 'opacity 120ms ease, transform 120ms ease';
            content.style.opacity = '0';
            content.style.transform = 'scale(0.97)';
        }
        // Remove visible immediately so Playwright/tests can detect closure
        modal.classList.remove('visible');
        if (modalId === 'image-modal') {
            window.Gallery?._cleanupZoomHandlers?.();
        }

        // Clean up animation styles after transition
        if (content) {
            setTimeout(() => {
                content.style.transition = '';
                content.style.opacity = '';
                content.style.transform = '';
            }, 130);
        }

        // Remove escape key handler
        if (modal._escapeHandler) {
            document.removeEventListener('keydown', modal._escapeHandler);
            modal._escapeHandler = null;
        }

        if (modalId === 'confirm-modal') {
            unlockDynamicI18nText('#confirm-title', 'modal.confirm', 'Are you sure?');
            unlockDynamicI18nText('#confirm-message', 'modal.confirmAction', 'This action cannot be undone.');
        } else if (modalId === 'input-modal') {
            unlockDynamicI18nText('#input-modal-title', 'modal.enterValue', 'Enter Value');
            const messageEl = $('#input-modal-message');
            if (messageEl) messageEl.textContent = '';
        }

        // Release focus trap and restore focus
        releaseFocus();
        restoreWindowScrollPosition(previousWindowScrollPosition);
        modal._previousWindowScrollPosition = null;
    }
}

function openVlmSettings() {
    if (window.VLMCaption && typeof window.VLMCaption.openSettingsModal === 'function') {
        window.VLMCaption.openSettingsModal();
        return true;
    }
    if (typeof showModal === 'function') {
        showModal('vlm-settings-modal');
        return true;
    }
    document.getElementById('vlm-settings-modal')?.classList.add('visible');
    return !!document.getElementById('vlm-settings-modal');
}

function openColorAnalysis() {
    showModal('tag-modal');
    const selectColorTab = () => {
        if (window.V321Integration && typeof window.V321Integration.setTaggerTab === 'function') {
            window.V321Integration.setTaggerTab('color');
            if (typeof window.V321Integration._refreshColorTab === 'function') {
                window.V321Integration._refreshColorTab();
            }
            return true;
        }
        const colorTab = document.querySelector('#tag-modal .tagger-tab[data-tagger-tab="color"]');
        colorTab?.click();
        return !!colorTab;
    };
    if (selectColorTab()) return true;
    setTimeout(selectColorTab, 0);
    return true;
}

// ============== FLOW-05/06/07: post-action "next step" CTA banner ==============
// Every pipeline step (scan / tag / sort) used to end with a toast that vanished
// after a few seconds, leaving the user at a dead-end with no hint what to do
// next. This one shared banner replaces that success toast with a persistent,
// dismissible "what's next" prompt that offers one-click routes to the following
// step. Other modules (autosep, manual-sort) reach it via
// window.App.showPipelineNextStep().
function _runPipelineNextStepAction(action) {
    if (typeof action === 'function') { action(); return; }
    if (typeof action !== 'string') return;
    const sep = action.indexOf(':');
    const kind = sep === -1 ? action : action.slice(0, sep);
    const arg = sep === -1 ? '' : action.slice(sep + 1);
    if (kind === 'view') {
        switchView(arg);
    } else if (kind === 'modal') {
        showModal(arg);
    }
}

function hidePipelineNextStep() {
    const banner = document.getElementById('pipeline-next-step');
    if (!banner) return;
    banner.classList.remove('visible');
    banner.hidden = true;
}

// Render a Graphite sprite icon into `el`, replacing whatever was there.
// Callers name a sprite symbol ("i-check"); anything else is treated as plain
// text so a stray string can never become markup. Built with DOM APIs rather
// than innerHTML, so there is no HTML parsing on this path at all.
const _SPRITE_NAME = /^i-[a-z0-9-]+$/;
function _setSpriteIcon(el, icon) {
    el.textContent = '';
    if (!icon) return;
    if (!_SPRITE_NAME.test(icon)) { el.textContent = icon; return; }
    const svgNs = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(svgNs, 'use');
    use.setAttribute('href', '#' + icon);
    svg.appendChild(use);
    el.appendChild(svg);
}

let _pipelineNextStepDismissBound = false;
function showPipelineNextStep(opts = {}) {
    const banner = document.getElementById('pipeline-next-step');
    if (!banner) return;
    const iconEl = banner.querySelector('.pns-icon');
    const titleEl = banner.querySelector('.pns-title');
    const actionsEl = banner.querySelector('.pns-actions');
    if (!iconEl || !titleEl || !actionsEl) return;

    _setSpriteIcon(iconEl, opts.icon || 'i-check');
    titleEl.textContent = opts.title || '';
    actionsEl.innerHTML = '';
    const actions = Array.isArray(opts.actions) ? opts.actions.slice(0, 3) : [];
    actions.forEach((a, i) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-small ' + (i === 0 ? 'btn-primary' : 'btn-ghost');
        btn.textContent = (a.icon ? a.icon + ' ' : '') + (a.label || '');
        btn.addEventListener('click', () => {
            hidePipelineNextStep();
            _runPipelineNextStepAction(a.action);
        });
        actionsEl.appendChild(btn);
    });

    // Bind the dismiss (×) once.
    if (!_pipelineNextStepDismissBound) {
        _pipelineNextStepDismissBound = true;
        banner.querySelector('.pns-dismiss')?.addEventListener('click', hidePipelineNextStep);
    }

    banner.hidden = false;
    void banner.offsetWidth; // force reflow so the slide-in transition replays
    banner.classList.add('visible');
}

function closeFilterModal() {
    hideModal('filter-modal');
    resetFilterModalController();
}

// Custom input modal (replaces native prompt())
let inputModalResolve = null;

function showInputModal(title, message, defaultValue = '') {
    return new Promise((resolve) => {
        // Resolve previous if still pending
        if (inputModalResolve) {
            inputModalResolve(null);
        }
        inputModalResolve = resolve;

        // Set modal content
        const titleEl = $('#input-modal-title');
        const messageEl = $('#input-modal-message');
        const inputEl = $('#input-modal-field');

        lockDynamicI18nText('#input-modal-title', 'modal.enterValue');
        if (titleEl) titleEl.textContent = title || appT('modal.enterValue', 'Enter Value');
        if (messageEl) messageEl.textContent = message || '';
        if (inputEl) {
            inputEl.value = defaultValue;
            inputEl.placeholder = '';
        }

        // Show modal
        showModal('input-modal');

        // Focus input after modal is visible
        setTimeout(() => {
            inputEl?.focus();
            inputEl?.select();
        }, 100);
    });
}

function initInputModal() {
    const inputField = $('#input-modal-field');
    const okBtn = $('#btn-input-ok');
    const cancelBtn = $('#btn-input-cancel');
    const backdrop = $('#input-modal .modal-backdrop');

    const handleOk = () => {
        const value = inputField?.value || '';
        hideModal('input-modal');
        if (inputModalResolve) {
            inputModalResolve(value);
            inputModalResolve = null;
        }
    };

    const handleCancel = () => {
        hideModal('input-modal');
        if (inputModalResolve) {
            inputModalResolve(null);
            inputModalResolve = null;
        }
    };

    okBtn?.addEventListener('click', handleOk);
    cancelBtn?.addEventListener('click', handleCancel);
    backdrop?.addEventListener('click', handleCancel);

    // Handle Enter key in input field
    inputField?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleOk();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            handleCancel();
        }
    });
}

// Global Loading Overlay
function showGlobalLoading(message = 'Loading...') {
    const overlay = $('#global-loading');
    const msgEl = $('#global-loading-msg');
    if (overlay) {
        if (msgEl) msgEl.textContent = message;
        overlay.style.display = 'flex';
    }
}

function hideGlobalLoading() {
    const overlay = $('#global-loading');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

