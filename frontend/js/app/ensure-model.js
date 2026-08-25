/**
 * First-use model install: the core app starts without AI weights.
 * Clicking a feature downloads THAT model's files, with a blocking overlay
 * so the UI never looks frozen. Downloads of about 1 GB or more ask first.
 * Classic script; loads after api-features.js (API + showConfirm) and
 * before tagging-flow.js / similar / artist / smart-tag callers.
 */
'use strict';

const FEATURE_INSTALL_CONFIRM_BYTES = 1024 * 1024 * 1024;
const FEATURE_INSTALL_POLL_MS = 750;
const FEATURE_INSTALL_STALL_MS = 3 * 60 * 1000;

function featureInstallT(key, fallback, params) {
    const translated = window.I18n?.t?.(key, params);
    if (translated && translated !== key) return translated;
    if (!params) return fallback;
    return Object.keys(params).reduce((text, name) => (
        text.split(`{${name}}`).join(String(params[name]))
    ), fallback);
}

function featureInstallConfirm(title, message) {
    return new Promise((resolve) => {
        const ask = window.App?.showConfirm || window.showConfirm;
        if (typeof ask === 'function') {
            ask(title, message, () => resolve(true), () => resolve(false));
            return;
        }
        resolve(window.confirm(message));
    });
}

function prepareSpecForTagger(modelName) {
    const name = String(modelName || '').trim() || 'wd-swinv2-tagger-v3';
    if (name === 'toriigate-0.5') {
        return {
            modelId: 'toriigate',
            label: 'ToriiGate 0.5',
            sizeHint: '~9.6 GB',
            confirmBytes: 9.6 * 1024 * 1024 * 1024,
        };
    }
    if (name === 'oppai-oracle-v1.1') {
        return {
            modelId: 'oppai-oracle',
            label: 'OppaiOracle V1.1',
            sizeHint: '~947 MB',
            confirmBytes: 0,
        };
    }
    if (name === 'cl-tagger-v2') {
        return {
            modelId: 'cl-tagger-v2',
            label: 'CL Tagger v2',
            sizeHint: '~2.7 GB',
            confirmBytes: 2.7 * 1024 * 1024 * 1024,
        };
    }
    const heavyWd14 = name === 'wd-eva02-large-tagger-v3';
    return {
        modelId: 'wd14',
        variant: name,
        label: name,
        sizeHint: heavyWd14 ? '~1.2 GB' : '~446 MB',
        confirmBytes: heavyWd14 ? 1.2 * 1024 * 1024 * 1024 : 0,
    };
}

function _featureCardIsReady(card, variant) {
    if (!card) return false;
    if (variant && Array.isArray(card.installed_variants)) {
        return card.installed_variants.includes(variant);
    }
    return card.status === 'ready' || card.available === true;
}

function _ensureInstallOverlay() {
    let overlay = document.getElementById('feature-model-install-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'feature-model-install-overlay';
    overlay.className = 'feature-model-install-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'alertdialog');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = (
        '<div class="feature-model-install-card">'
        + '<p class="feature-model-install-title"></p>'
        + '<p class="feature-model-install-status"></p>'
        + '<div class="feature-model-install-bar" aria-hidden="true">'
        + '<div class="feature-model-install-fill"></div>'
        + '</div>'
        + '<div class="feature-model-install-actions" hidden>'
        + '<button type="button" class="btn btn-primary" data-action="restart-and-continue"></button>'
        + '<button type="button" class="btn btn-ghost" data-action="dismiss-install-overlay"></button>'
        + '</div>'
        + '</div>'
    );
    document.body.appendChild(overlay);
    return overlay;
}

function _setInstallOverlay(label, statusText, percent) {
    const overlay = _ensureInstallOverlay();
    overlay.hidden = false;
    const titleEl = overlay.querySelector('.feature-model-install-title');
    const statusEl = overlay.querySelector('.feature-model-install-status');
    const fillEl = overlay.querySelector('.feature-model-install-fill');
    if (titleEl) {
        titleEl.textContent = featureInstallT(
            'featureInstall.title',
            'Installing {name}…',
            { name: label },
        );
    }
    if (statusEl) statusEl.textContent = statusText || '';
    const barEl = overlay.querySelector('.feature-model-install-bar');
    const actionsEl = overlay.querySelector('.feature-model-install-actions');
    if (barEl) barEl.hidden = false;
    if (actionsEl) actionsEl.hidden = true;
    if (fillEl) {
        const width = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
        fillEl.style.width = `${width}%`;
    }
}

function _hideInstallOverlay() {
    const overlay = document.getElementById('feature-model-install-overlay');
    if (overlay) overlay.hidden = true;
}

function _showInstallRestartPrompt(spec, result) {
    const overlay = _ensureInstallOverlay();
    overlay.hidden = false;
    const titleEl = overlay.querySelector('.feature-model-install-title');
    const statusEl = overlay.querySelector('.feature-model-install-status');
    const barEl = overlay.querySelector('.feature-model-install-bar');
    const actionsEl = overlay.querySelector('.feature-model-install-actions');
    const restartBtn = overlay.querySelector('[data-action="restart-and-continue"]');
    const dismissBtn = overlay.querySelector('[data-action="dismiss-install-overlay"]');
    const label = spec?.label || spec?.id || spec?.modelId || 'model';
    const packages = Array.isArray(result?.installed_packages)
        ? result.installed_packages.join(', ')
        : '';
    const reminder = packages
        ? featureInstallT(
            'models.restartAfterInstallWithPackages',
            'Installed Python packages: {packages}. Restart the app before using this feature.',
            { packages },
        )
        : featureInstallT(
            'models.restartAfterInstall',
            'Restart the app before using this feature.',
        );
    if (titleEl) {
        titleEl.textContent = featureInstallT('models.bulkNeedsRestart', 'Restart required');
    }
    if (statusEl) {
        statusEl.textContent = reminder;
    }
    if (barEl) barEl.hidden = true;
    if (actionsEl) actionsEl.hidden = false;
    if (restartBtn) {
        restartBtn.textContent = featureInstallT(
            'models.restartNowAndContinue',
            'Restart now and continue',
        );
        restartBtn.disabled = false;
        restartBtn.onclick = () => {
            restartBtn.disabled = true;
            if (dismissBtn) dismissBtn.disabled = true;
            const resumeItems = [{
                id: spec?.modelId || spec?.id,
                name: label,
                variant: spec?.variant || null,
            }];
            requestAppRestartAndContinue({
                reason: 'model_dependency_install',
                items: resumeItems,
            }).then((restartResult) => {
                if (restartResult?.status === 'scheduled') {
                    _hideInstallOverlay();
                    return;
                }
                restartBtn.disabled = false;
                if (dismissBtn) dismissBtn.disabled = false;
                if (statusEl) {
                    statusEl.textContent = restartResult?.status === 'error'
                        ? featureInstallT(
                            'models.restartFailed',
                            'Could not restart the app automatically. Close the window and run the launcher again.',
                        )
                        : featureInstallT(
                            'models.restartUnsupported',
                            'This session was not started from the launcher, so the app cannot restart itself. Close the window and run run.bat / run.sh; remaining downloads will continue afterwards.',
                        );
                }
            });
        };
    }
    if (dismissBtn) {
        dismissBtn.textContent = featureInstallT('models.restartQueueDismiss', 'Not now');
        dismissBtn.onclick = () => {
            if (restartBtn?.disabled) return;
            if (typeof showPrepareRestartPrompt === 'function') {
                showPrepareRestartPrompt({
                    items: [{
                        id: spec?.modelId || spec?.id,
                        name: label,
                        variant: spec?.variant || null,
                    }],
                });
            }
            _hideInstallOverlay();
        };
    }
}

function _formatProgressStatus(label, progress) {
    const downloaded = Number(progress?.downloaded || 0);
    const total = Number(progress?.total || 0);
    const filename = progress?.filename || label;
    if (progress?.active && total > 0) {
        const percent = Math.round((downloaded / total) * 100);
        const mb = (downloaded / 1048576).toFixed(0);
        const totalMb = (total / 1048576).toFixed(0);
        return {
            text: featureInstallT(
                'featureInstall.progressBytes',
                '{name}: {downloaded} / {total} MB ({percent}%)',
                { name: filename, downloaded: mb, total: totalMb, percent },
            ),
            percent,
        };
    }
    if (progress?.active) {
        const mb = (downloaded / 1048576).toFixed(0);
        return {
            text: featureInstallT(
                'featureInstall.progressBytesOnly',
                '{name}: {downloaded} MB…',
                { name: filename, downloaded: mb },
            ),
            percent: 0,
        };
    }
    return {
        text: featureInstallT(
            'featureInstall.progressUnknown',
            'Installing {name}…',
            { name: label },
        ),
        percent: 0,
    };
}

async function _pollPrepareUntilSettled(modelId, label) {
    const api = window.App?.API || window.API;
    let lastSignature = null;
    let lastProgressAt = Date.now();
    let stallWarned = false;
    let pollErrorStreak = 0;

    while (true) {
        let payload;
        try {
            payload = await api.get('/api/models/download-progress');
            pollErrorStreak = 0;
        } catch (error) {
            pollErrorStreak += 1;
            if (pollErrorStreak >= 8) {
                throw error;
            }
            await new Promise((resolve) => setTimeout(resolve, FEATURE_INSTALL_POLL_MS));
            continue;
        }

        const signature = payload?.active
            ? `${payload.filename || ''}:${payload.downloaded || 0}`
            : null;
        if (signature !== lastSignature) {
            lastSignature = signature;
            lastProgressAt = Date.now();
            stallWarned = false;
        } else if (!stallWarned && Date.now() - lastProgressAt > FEATURE_INSTALL_STALL_MS) {
            stallWarned = true;
            const toast = window.App?.showToast || window.showToast;
            if (typeof toast === 'function') {
                toast(featureInstallT(
                    'models.downloadStalled',
                    'Download may have stalled. Check your network connection and try again.',
                ), 'warning');
            }
        }

        const formatted = _formatProgressStatus(label, payload);
        _setInstallOverlay(label, formatted.text, formatted.percent);

        const result = payload?.prepare_result;
        if (result && result.active === false && result.model_id === modelId && result.status) {
            return result;
        }

        await new Promise((resolve) => setTimeout(resolve, FEATURE_INSTALL_POLL_MS));
    }
}

async function ensureFeatureModel(modelId, options = {}) {
    const spec = options || {};
    const variant = spec.variant || null;
    const label = spec.label || modelId;
    const sizeHint = spec.sizeHint || '';
    const confirmBytes = Number(spec.confirmBytes || 0);
    const toast = window.App?.showToast || window.showToast;
    const api = window.App?.API || window.API;
    const showToast = (message, level) => {
        if (typeof toast === 'function') toast(message, level);
    };

    if (!api || typeof api.get !== 'function' || typeof api.prepareModel !== 'function') {
        return { ok: false, error: 'api-unavailable' };
    }

    try {
        const status = await api.getModelStatus();
        const card = (status?.models || []).find((item) => item?.id === modelId);
        if (_featureCardIsReady(card, variant)) {
            return { ok: true, alreadyReady: true };
        }
    } catch (_statusErr) {
        // Unknown is not ready: fall through and prepare.
    }

    try {
        const live = await api.get('/api/models/download-progress');
        const activeId = live?.prepare_result?.active
            ? live.prepare_result.model_id
            : (live?.active ? (live.prepare_result?.model_id || live.model_id) : '');
        if (live?.prepare_result?.active && activeId && activeId !== modelId) {
            showToast(featureInstallT(
                'featureInstall.conflict',
                'Cannot install {requested}: {active} is already installing.',
                { requested: label, active: activeId },
            ), 'warning');
            return { ok: false, conflict: true };
        }
        if (live?.prepare_result?.active && activeId === modelId) {
            _setInstallOverlay(
                label,
                featureInstallT('featureInstall.progressUnknown', 'Installing {name}…', { name: label }),
                0,
            );
            const attached = await _pollPrepareUntilSettled(modelId, label);
            return _finishPrepareResult(attached, { modelId, label, variant }, showToast);
        }
    } catch (_progressErr) {
        // Continue; prepare itself will report a conflict if needed.
    }

    if (confirmBytes >= FEATURE_INSTALL_CONFIRM_BYTES) {
        const confirmed = await featureInstallConfirm(
            featureInstallT('featureInstall.confirmTitle', 'Download {name}?', { name: label }),
            featureInstallT(
                'featureInstall.confirmBody',
                'This downloads about {size}. First launch stays small; only this feature\'s files are fetched. Continue?',
                { size: sizeHint || label },
            ),
        );
        if (!confirmed) {
            showToast(featureInstallT('featureInstall.cancelled', 'Download cancelled'), 'info');
            return { ok: false, cancelled: true };
        }
    }

    _setInstallOverlay(
        label,
        featureInstallT('featureInstall.progressUnknown', 'Installing {name}…', { name: label }),
        0,
    );

    let prepareResponse;
    try {
        prepareResponse = await api.prepareModel(modelId, { variant });
    } catch (error) {
        _hideInstallOverlay();
        const message = (typeof window.formatUserError === 'function')
            ? window.formatUserError(error, featureInstallT(
                'featureInstall.failed',
                'Could not install {name}',
                { name: label },
            ))
            : String(error?.message || error);
        showToast(message, 'error');
        return { ok: false, error: message };
    }

    const activeModelId = typeof prepareResponse?.model_id === 'string'
        ? prepareResponse.model_id.trim()
        : '';
    if (activeModelId && activeModelId !== modelId) {
        _hideInstallOverlay();
        showToast(featureInstallT(
            'featureInstall.conflict',
            'Cannot install {requested}: {active} is already installing.',
            { requested: label, active: activeModelId },
        ), 'warning');
        return { ok: false, conflict: true };
    }

    try {
        const result = await _pollPrepareUntilSettled(modelId, label);
        return _finishPrepareResult(result, { modelId, label, variant }, showToast);
    } catch (error) {
        _hideInstallOverlay();
        const message = (typeof window.formatUserError === 'function')
            ? window.formatUserError(error, featureInstallT(
                'featureInstall.failed',
                'Could not install {name}',
                { name: label },
            ))
            : String(error?.message || error);
        showToast(message, 'error');
        return { ok: false, error: message };
    }
}

function _finishPrepareResult(result, spec, showToast) {
    const label = (spec && typeof spec === 'object') ? (spec.label || spec.modelId || '') : spec;
    const status = String(result?.status || '');
    const message = result?.message || '';
    if (status === 'error') {
        _hideInstallOverlay();
        showToast(message || featureInstallT(
            'featureInstall.failed',
            'Could not install {name}',
            { name: label },
        ), 'error');
        return { ok: false, error: message || status };
    }
    if (typeof prepareResultNeedsRestart === 'function'
        ? prepareResultNeedsRestart(result)
        : Boolean(result?.restart_recommended) || status === 'needs_restart') {
        const packages = Array.isArray(result.installed_packages)
            ? result.installed_packages.join(', ')
            : '';
        const reminder = packages
            ? featureInstallT(
                'models.restartAfterInstallWithPackages',
                'Installed Python packages: {packages}. Restart the app before using this feature.',
                { packages },
            )
            : featureInstallT(
                'models.restartAfterInstall',
                'Restart the app before using this feature.',
            );
        showToast(message ? `${message} ${reminder}` : reminder, 'warning');
        _showInstallRestartPrompt(
            (spec && typeof spec === 'object') ? spec : { label, modelId: label },
            result,
        );
        return { ok: false, needsRestart: true };
    }
    _hideInstallOverlay();
    if (status === 'done' || status === 'ok' || status === 'warning') {
        if (message) showToast(message, status === 'warning' ? 'warning' : 'success');
        return { ok: true, status };
    }
    showToast(message || featureInstallT(
        'featureInstall.failed',
        'Could not install {name}',
        { name: label },
    ), 'error');
    return { ok: false, error: status || 'unknown' };
}

window.ensureFeatureModel = ensureFeatureModel;
window.prepareSpecForTagger = prepareSpecForTagger;
window.FEATURE_INSTALL_CONFIRM_BYTES = FEATURE_INSTALL_CONFIRM_BYTES;
