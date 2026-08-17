/**
 * app/update-popup.js — app.js decomposition, stage 2 (leaf sections).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js,
 * pre-split lines 2667-3071. Classic script: shares ONE global lexical
 * environment with app.js and the other app/ parts; index.html loads
 * every app/ file BEFORE app.js, so these top-level declarations stay
 * visible there. No behavior change intended.
 */
function getUpdateActionButtons() {
    return ['#btn-app-update', '#mobile-btn-app-update']
        .map((selector) => $(selector))
        .filter(Boolean);
}

function setUpdateButtonState(status = AppState.update.status, checking = false) {
    AppState.update.checking = Boolean(checking);
    AppState.update.status = status || null;

    const buttons = getUpdateActionButtons();
    const currentVersion = status?.current_version || appT('update.versionUnknown', 'current');
    let state = 'idle';
    let label = appT('update.check', 'Check Updates');
    let title = appT('update.checkTitle', 'Check for application updates');

    if (checking) {
        state = 'checking';
        label = appT('update.checking', 'Checking...');
        title = appT('update.checkingTitle', 'Checking the configured update channel for a new version');
    } else if (status?.has_update) {
        state = 'available';
        label = appT('update.availableShort', 'Update {version}', { version: status.latest_version || '' });
        title = appT('update.availableTitle', 'Update from {current} to {latest}', {
            current: currentVersion,
            latest: status.latest_version || '',
        });
    } else if (status?.error || status?.update_unavailable_reason) {
        label = appT('update.retry', 'Retry Update Check');
        title = status?.update_unavailable_reason || status?.error || title;
    } else if (status) {
        label = appT('update.current', 'Latest');
        title = appT('update.currentTitle', 'You are already on version {version}', {
            version: currentVersion,
        });
    }

    buttons.forEach((button) => {
        button.dataset.updateState = state;
        button.disabled = Boolean(checking);
        button.title = title;
        button.setAttribute('aria-label', title);
        if (!button.classList.contains('btn-icon-only')) {
            const textNode = button.querySelector('span:last-child');
            if (textNode) {
                textNode.textContent = label;
            }
        }
    });

    window.requestAnimationFrame?.(() => updateNavigationOverflowState());
}

async function refreshUpdateStatus({ force = false, silent = false } = {}) {
    if (AppState.update.checking) {
        return AppState.update.status;
    }

    setUpdateButtonState(AppState.update.status, true);
    try {
        const status = await API.getUpdateStatus(force);
        setUpdateButtonState(status, false);

        if (!silent) {
            if (status?.has_update) {
                showToast(
                    appT('update.availableToast', 'New version {version} is ready to install.', {
                        version: status.latest_version || '',
                    }),
                    'info'
                );
            } else if (status?.update_unavailable_reason) {
                showToast(status.update_unavailable_reason, 'warning');
            } else if (status?.error) {
                showToast(status.error, 'error');
            } else {
                showToast(
                    appT('update.none', 'You are already on the latest version.'),
                    'success'
                );
            }
        }

        return status;
    } catch (error) {
        setUpdateButtonState(AppState.update.status, false);
        if (!silent) {
            showToast(
                formatUserError(error, appT('update.checkFailed', 'Failed to check for updates')),
                'error'
            );
        }
        throw error;
    }
}

function buildUpdateConfirmMessage(status) {
    const currentVersion = status?.current_version || appT('update.versionUnknown', 'current version');
    const latestVersion = status?.latest_version || appT('update.versionUnknown', 'latest version');
    return appT('update.confirmMessage', 'Update from {current} to {latest} now? The app will restart when the patch is ready.', {
        current: currentVersion,
        latest: latestVersion,
    });
}

async function applyAppUpdate(status = AppState.update.status) {
    if (!status?.has_update) {
        return null;
    }

    showGlobalLoading(appT('update.downloading', 'Downloading update...'));
    setUpdateButtonState(status, true);
    try {
        const result = await API.applyUpdate({ forceCheck: true, relaunch: true });
        if (result?.status !== 'scheduled') {
            hideGlobalLoading();
            setUpdateButtonState(result, false);
            return result;
        }

        showGlobalLoading(appT('update.applying', 'Applying update and restarting...'));
        showToast(
            appT('update.restartSoon', 'Update downloaded. Restarting the app now...'),
            'info'
        );
        return result;
    } catch (error) {
        hideGlobalLoading();
        setUpdateButtonState(status, false);
        showToast(
            formatUserError(error, appT('update.applyFailed', 'Failed to apply the update')),
            'error'
        );
        throw error;
    }
}

async function handleAppUpdateButtonClick() {
    const btn = document.getElementById('btn-app-update');
    if (!btn) return;
    const existing = document.getElementById('update-popup');
    if (existing?.classList.contains('visible')) {
        _hideUpdatePopup();
        return;
    }
    _showUpdatePopup(btn);
}

let _updatePopupEl = null;

function _createUpdatePopup() {
    if (_updatePopupEl) return _updatePopupEl;
    const el = document.createElement('div');
    el.id = 'update-popup';
    el.className = 'update-popup';
    document.body.appendChild(el);
    _updatePopupEl = el;

    document.addEventListener('click', (e) => {
        if (_updatePopupEl?.classList.contains('visible') && !_updatePopupEl.contains(e.target)) {
            const btn = document.getElementById('btn-app-update');
            if (btn && !btn.contains(e.target)) _hideUpdatePopup();
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && _updatePopupEl?.classList.contains('visible')) _hideUpdatePopup();
    });

    return el;
}

function buildUpdateChannelPanel(channel, channelError = null) {
    const hasOverride = Boolean(channel?.has_channel_override);
    const proxyPrefix = channel?.download_url_prefix || '';
    const channelName = channel?.channel_name || (
        hasOverride
            ? appT('update.customChannel', 'Custom channel')
            : appT('update.defaultChannel', 'Official GitHub')
    );
    const currentText = channelError
        ? appT('update.channelLoadFailed', 'Channel settings unavailable')
        : (
            hasOverride
                ? appT('update.channelCustomSummary', '{name} · {proxy}', {
                    name: channelName,
                    proxy: proxyPrefix || appT('update.proxyPrefixMissing', 'No proxy prefix'),
                })
                : appT('update.channelDefaultSummary', 'Official GitHub')
        );
    const openAttr = hasOverride ? ' open' : '';
    const disabledAttr = channelError ? ' disabled' : '';

    return `<details class="guided-advanced-panel update-channel-panel"${openAttr}>
        <summary class="guided-advanced-summary">
            <span class="update-popup-channel-summary">
                <strong>${escapeHtml(appT('update.channelPanelTitle', 'Update proxy / custom channel'))}</strong>
                <span class="update-popup-channel-current">${escapeHtml(currentText)}</span>
            </span>
            <span class="guided-advanced-hint">${escapeHtml(appT('update.channelPanelHint', 'Use this when GitHub is blocked.'))}</span>
        </summary>
        <div class="guided-advanced-body">
            <div class="update-popup-channel-form">
                <label for="update-proxy-prefix">${escapeHtml(appT('update.proxyPrefixLabel', 'Proxy prefix'))}</label>
                <input
                    id="update-proxy-prefix"
                    class="update-popup-proxy-input"
                    type="url"
                    inputmode="url"
                    autocomplete="off"
                    spellcheck="false"
                    data-update-channel-control
                    placeholder="${escapeHtml(appT('update.proxyPlaceholder', 'https://your-proxy/'))}"
                    value="${escapeHtml(proxyPrefix)}"
                    ${disabledAttr}
                >
                <p class="helper-text">${escapeHtml(appT('update.proxyInlineHelp', 'Paste a GitHub proxy prefix. Leave empty and Save to restore official GitHub.'))}</p>
            </div>
            <div class="update-popup-channel-actions">
                <button class="btn btn-primary btn-small" data-update-action="save-proxy" data-update-channel-control${disabledAttr}>${escapeHtml(appT('update.saveProxy', 'Save Proxy'))}</button>
                <button class="btn btn-secondary btn-small" data-update-action="reset-channel" data-update-channel-control${disabledAttr}>${escapeHtml(appT('update.resetChannel', 'Reset Channel'))}</button>
            </div>
            <p class="update-popup-channel-feedback" data-update-channel-feedback hidden></p>
        </div>
    </details>`;
}

function setUpdateChannelControlsBusy(popup, busy) {
    popup.querySelectorAll('[data-update-channel-control]').forEach((control) => {
        control.disabled = Boolean(busy);
    });
}

function setUpdateChannelFeedback(popup, message, tone = 'info') {
    const el = popup.querySelector('[data-update-channel-feedback]');
    if (!el) return;
    el.hidden = !message;
    el.textContent = message || '';
    el.classList.toggle('is-error', tone === 'error');
    el.classList.toggle('is-success', tone === 'success');
}

async function _showUpdatePopup(anchorBtn) {
    const popup = _createUpdatePopup();
    // Source the current version from any channel that already knows it — the
    // gallery-stats-fed AppState.appVersion may not have arrived yet right after
    // launch, but the update check payload (current_version) and the brand
    // badge ('v3.5.0') do. Only fall back to the localized "unknown" as a last
    // resort, and never prefix 'v' onto that fallback (was "vunknown").
    const status = AppState.update.status;
    const knownCurrent = AppState.appVersion
        || status?.current_version
        || (document.getElementById('brand-version')?.textContent || '').replace(/^v/i, '').trim();
    const currentVersion = knownCurrent || appT('update.versionUnknown', 'unknown');
    const githubUrl = AppState.githubUrl || '';
    const hasUpdate = status?.has_update;
    const latestVersion = status?.latest_version || knownCurrent || currentVersion;
    // Prefix 'v' only for a real numeric version, not the "unknown" fallback.
    const fmtVersion = (v) => /^\d/.test(String(v)) ? 'v' + escapeHtml(String(v)) : escapeHtml(String(v));
    const releaseUrl = status?.release_url || (githubUrl ? githubUrl + '/releases/latest' : '');
    const releaseNotes = status?.release_notes || '';
    let channel = AppState.update.channel;
    let channelError = null;

    try {
        channel = await API.getUpdateChannel();
        AppState.update.channel = channel;
        AppState.update.channelError = null;
    } catch (error) {
        channelError = error;
        AppState.update.channelError = error;
    }

    let notesHtml = '';
    if (releaseNotes) {
        const truncated = releaseNotes.length > 200 ? releaseNotes.slice(0, 200) + '...' : releaseNotes;
        notesHtml = `<div class="update-popup-row" style="flex-direction:column;align-items:flex-start;gap:4px;">
            <span class="update-popup-label">${escapeHtml(appT('update.releaseNotes', 'Release Notes'))}</span>
            <span style="font-size:12px;color:var(--text-secondary);line-height:1.5;white-space:pre-wrap;">${escapeHtml(truncated)}</span>
        </div>`;
    }

    let actionsHtml = '';
    if (hasUpdate) {
        actionsHtml = `<div class="update-popup-actions">
            <button class="btn btn-primary" data-update-action="install">${escapeHtml(appT('update.installNow', 'Install Update'))}</button>
        </div>`;
    } else {
        actionsHtml = `<div class="update-popup-actions">
            <button class="btn btn-secondary" data-update-action="check">${escapeHtml(appT('update.checkNow', 'Check for Updates'))}</button>
        </div>`;
    }

    popup.innerHTML = `<div class="update-popup-card">
        <div class="update-popup-header">
            <span class="update-popup-title">${escapeHtml(appT('update.popupTitle', 'Version Info'))}</span>
            <button class="update-popup-close" data-update-action="close" aria-label="Close">&times;</button>
        </div>
        <div class="update-popup-row">
            <span class="update-popup-label">${escapeHtml(appT('update.currentLabel', 'Current Version'))}</span>
            <span class="update-popup-value">${fmtVersion(currentVersion)}</span>
        </div>
        <div class="update-popup-row">
            <span class="update-popup-label">${escapeHtml(appT('update.latestLabel', 'Latest Version'))}</span>
            <span class="update-popup-value${hasUpdate ? ' has-update' : ''}">${fmtVersion(latestVersion)}${hasUpdate ? ' <svg class="icon" aria-hidden="true"><use href="#i-sparkle"/></svg>' : ''}</span>
        </div>
        ${releaseUrl ? `<div class="update-popup-row">
            <span class="update-popup-label">${escapeHtml(appT('update.releasePageLabel', 'Release Page'))}</span>
            <a href="${escapeHtml(releaseUrl)}" target="_blank" rel="noopener" class="update-popup-link">GitHub ↗</a>
        </div>` : ''}
        ${notesHtml ? '<div class="update-popup-divider"></div>' + notesHtml : ''}
        <div class="update-popup-divider"></div>
        ${buildUpdateChannelPanel(channel, channelError)}
        <div class="update-popup-divider"></div>
        ${actionsHtml}
    </div>`;

    popup.classList.add('visible');
    window.PopupPosition?.place(popup, {
        anchor: anchorBtn,
        placement: 'bottom-end',
        gap: 8,
        maxHeight: Math.max(160, window.innerHeight - 24),
    });
    // Single delegated handler — re-renders no longer accumulate listeners on
    // detached DOM nodes. We replace popup.onclick wholesale on each show so
    // closures captured by the previous render are eligible for GC.
    popup.onclick = async (event) => {
        const target = event.target.closest('[data-update-action]');
        if (!target) return;
        const action = target.dataset.updateAction;
        if (action === 'close') {
            _hideUpdatePopup();
        } else if (action === 'install') {
            _hideUpdatePopup();
            showConfirm(
                appT('update.confirmTitle', 'Install Update'),
                buildUpdateConfirmMessage(AppState.update.status),
                () => { void applyAppUpdate(AppState.update.status); }
            );
        } else if (action === 'check') {
            target.disabled = true;
            target.textContent = appT('update.checking', 'Checking...');
            try {
                await refreshUpdateStatus({ force: true, silent: false });
                _hideUpdatePopup();
                const btn2 = document.getElementById('btn-app-update');
                if (btn2 && AppState.update.status) _showUpdatePopup(btn2);
            } catch (err) {
                _hideUpdatePopup();
            }
        } else if (action === 'save-proxy') {
            const input = popup.querySelector('#update-proxy-prefix');
            const proxyPrefix = String(input?.value || '').trim();
            const successMessage = proxyPrefix
                ? appT('update.proxySaved', 'Update proxy saved.')
                : appT('update.proxyReset', 'Update channel reset to official GitHub.');
            setUpdateChannelControlsBusy(popup, true);
            setUpdateChannelFeedback(popup, appT('update.proxySaving', 'Saving update proxy...'));
            try {
                const nextChannel = proxyPrefix
                    ? await API.saveUpdateProxy(proxyPrefix, appT('update.proxyChannelName', 'Custom Proxy'))
                    : await API.resetUpdateChannel();
                AppState.update.channel = nextChannel;
                AppState.update.channelError = null;
                showToast(successMessage, 'success');
                _hideUpdatePopup();
                const btn2 = document.getElementById('btn-app-update');
                if (btn2) void _showUpdatePopup(btn2);
            } catch (error) {
                const message = formatUserError(error, appT('update.proxySaveFailed', 'Failed to save the update proxy'));
                setUpdateChannelControlsBusy(popup, false);
                setUpdateChannelFeedback(popup, message, 'error');
                showToast(message, 'error');
            }
        } else if (action === 'reset-channel') {
            setUpdateChannelControlsBusy(popup, true);
            setUpdateChannelFeedback(popup, appT('update.proxySaving', 'Saving update proxy...'));
            try {
                const nextChannel = await API.resetUpdateChannel();
                AppState.update.channel = nextChannel;
                AppState.update.channelError = null;
                showToast(appT('update.proxyReset', 'Update channel reset to official GitHub.'), 'success');
                _hideUpdatePopup();
                const btn2 = document.getElementById('btn-app-update');
                if (btn2) void _showUpdatePopup(btn2);
            } catch (error) {
                const message = formatUserError(error, appT('update.proxySaveFailed', 'Failed to save the update proxy'));
                setUpdateChannelControlsBusy(popup, false);
                setUpdateChannelFeedback(popup, message, 'error');
                showToast(message, 'error');
            }
        }
    };
}

function _hideUpdatePopup() {
    if (!_updatePopupEl) return;
    _updatePopupEl.classList.remove('visible');
    // Drop child DOM + delegated handler so closures from the previous render
    // can be garbage-collected. The two document-level listeners installed
    // by _createUpdatePopup remain (idempotent setup).
    _updatePopupEl.onclick = null;
    _updatePopupEl.innerHTML = '';
}

