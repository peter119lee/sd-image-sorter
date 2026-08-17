/**
 * Model setup recovery guide and shared byte formatting.
 * Classic script sharing the app global lexical environment.
 */
function _formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

let _modelSetupGuideCleanup = null;

function showModelSetupGuide(pr) {
    // Build a one-off backdrop+dialog so the structured manual_steps from
    // the backend (Civitai login wall, archive verification failure, ...)
    // become an actionable recovery path instead of a stale toast.
    if (_modelSetupGuideCleanup) {
        _modelSetupGuideCleanup({ restoreFocus: false });
    }

    const provider = String(pr.provider || '').trim();
    const message = String(pr.message || appT('models.prepareFailed', 'Model setup failed'));
    const targetDir = String(pr.target_dir || '').trim();
    const externalUrl = String(pr.external_url || '').trim();
    const steps = Array.isArray(pr.manual_steps) ? pr.manual_steps : [];

    const titleText = appT('models.manualSetupTitle', 'Manual setup required');
    const providerLabel = provider
        ? appT('models.providerLabel', 'Source: {provider}', { provider })
        : '';
    const dirLabel = appT('models.targetDirLabel', 'Save the files into this folder:');
    const openLabel = appT('models.openDownloadPage', 'Open Download Page');
    const copyLabel = appT('models.copyTargetDir', 'Copy folder path');
    const closeLabel = appT('models.guideClose', 'Close');

    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const backdrop = document.createElement('div');
    backdrop.id = 'model-setup-guide-backdrop';
    backdrop.className = 'modal-backdrop visible';
    backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(16, 16, 16, 0.72);z-index:9000;display:flex;align-items:center;justify-content:center;padding:24px;';

    const dialog = document.createElement('div');
    dialog.role = 'dialog';
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'model-setup-guide-title');
    dialog.style.cssText = 'background:var(--bg-card-solid,#1E1E1E);color:var(--text-primary,#FBF7F2);border:1px solid var(--glass-border,rgba(var(--accent-rgb), 0.18));border-radius:16px;padding:24px;max-width:560px;width:100%;max-height:calc(100vh - 48px);overflow-y:auto;box-shadow:0 24px 64px rgba(0,0,0,0.5);';

    const stepsHtml = steps
        .map((step, i) => `<li style="margin:6px 0;line-height:1.5;">${escapeHtml(String(step))}</li>`)
        .join('');

    const targetDirHtml = targetDir
        ? `
            <div style="margin-top:14px;padding:12px;background:rgba(255,255,255,0.04);border-radius:10px;border:1px solid rgba(255,255,255,0.06);">
                <div style="font-size:12px;color:var(--text-muted,#A6A6A6);margin-bottom:6px;">${escapeHtml(dirLabel)}</div>
                <code id="model-setup-guide-dir" style="display:block;word-break:break-all;font-size:12px;line-height:1.4;font-family:ui-monospace,Consolas,monospace;color:var(--text-primary,#FBF7F2);">${escapeHtml(targetDir)}</code>
                <button id="model-setup-guide-copy" type="button" class="btn btn-ghost btn-small" style="margin-top:8px;">${escapeHtml(copyLabel)}</button>
            </div>
        ` : '';

    dialog.innerHTML = `
        <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:12px;">
            <div style="flex:1;">
                <h3 id="model-setup-guide-title" style="margin:0;font-size:18px;line-height:1.3;">${escapeHtml(titleText)}</h3>
                ${providerLabel ? `<div style="font-size:12px;color:var(--text-muted,#A6A6A6);margin-top:4px;">${escapeHtml(providerLabel)}</div>` : ''}
            </div>
            <button id="model-setup-guide-close-x" type="button" aria-label="${escapeHtml(closeLabel)}" style="background:none;border:none;color:var(--text-muted,#A6A6A6);font-size:22px;line-height:1;cursor:pointer;padding:4px 8px;border-radius:8px;">×</button>
        </div>
        <p style="margin:0 0 12px;line-height:1.5;">${escapeHtml(message)}</p>
        ${steps.length ? `<ol style="margin:0;padding-left:22px;">${stepsHtml}</ol>` : ''}
        ${targetDirHtml}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px;flex-wrap:wrap;">
            ${externalUrl ? `<button id="model-setup-guide-open" type="button" class="btn btn-primary">${escapeHtml(openLabel)}</button>` : ''}
            <button id="model-setup-guide-close" type="button" class="btn btn-secondary">${escapeHtml(closeLabel)}</button>
        </div>
    `;

    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    const getFocusableElements = () => Array.from(dialog.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
    )).filter((el) => {
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    });

    const close = ({ restoreFocus = true } = {}) => {
        document.removeEventListener('keydown', onKeydown);
        if (_modelSetupGuideCleanup === close) {
            _modelSetupGuideCleanup = null;
        }
        backdrop.remove();
        if (restoreFocus) previousFocus?.focus?.();
    };
    const onKeydown = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const focusable = getFocusableElements();
            if (focusable.length === 0) {
                e.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    };

    // Backdrop click closes; dialog click does not bubble through.
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) close();
    });
    dialog.addEventListener('click', (e) => e.stopPropagation());
    document.addEventListener('keydown', onKeydown);
    _modelSetupGuideCleanup = close;

    document.getElementById('model-setup-guide-close-x')?.addEventListener('click', close);
    document.getElementById('model-setup-guide-close')?.addEventListener('click', close);

    if (externalUrl) {
        document.getElementById('model-setup-guide-open')?.addEventListener('click', () => {
            try {
                window.open(externalUrl, '_blank', 'noopener,noreferrer');
            } catch (_err) {
                showToast(appT('models.openDownloadPageFailed', 'Could not open the download page automatically.'), 'error');
            }
        });
    }

    if (targetDir) {
        document.getElementById('model-setup-guide-copy')?.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(targetDir);
                showToast(appT('models.targetDirCopied', 'Folder path copied to clipboard'), 'success');
            } catch (_err) {
                // Fallback: select the code element so the user can Ctrl+C.
                const codeEl = document.getElementById('model-setup-guide-dir');
                if (codeEl) {
                    const range = document.createRange();
                    range.selectNodeContents(codeEl);
                    const sel = window.getSelection();
                    sel?.removeAllRanges();
                    sel?.addRange(range);
                }
                showToast(appT('models.targetDirCopyFailed', 'Could not copy automatically — select and copy manually.'), 'warning');
            }
        });
    }

    // Focus management for accessibility.
    setTimeout(() => {
        document.getElementById('model-setup-guide-open')
            ?? document.getElementById('model-setup-guide-close-x')
            ?? null;
        const focusTarget = document.getElementById('model-setup-guide-open')
            || document.getElementById('model-setup-guide-close');
        focusTarget?.focus();
    }, 50);
}
