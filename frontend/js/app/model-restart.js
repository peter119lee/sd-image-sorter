/**
 * One-click restart after a proven dependency install, plus a resume queue
 * so remaining model downloads continue after the launcher comes back.
 * Classic script; loads after api-features.js and before ensure-model.js.
 */
'use strict';

const PREPARE_RESUME_STORAGE_KEY = 'sd-image-sorter-prepare-resume-v1';
const PREPARE_RESUME_BANNER_ID = 'prepare-restart-banner';
let _appRestartInFlight = false;

function prepareResultNeedsRestart(result) {
    if (!result || typeof result !== 'object' || Array.isArray(result)) return false;
    return Boolean(result.restart_recommended) || result.status === 'needs_restart';
}

function normalizePrepareResumeItems(items) {
    if (!Array.isArray(items)) return [];
    const seen = new Set();
    const normalized = [];
    items.forEach((item) => {
        const id = String(item?.id || '').trim();
        if (!id || seen.has(id)) return;
        seen.add(id);
        normalized.push({
            id,
            name: String(item?.name || id).trim() || id,
            variant: item?.variant || null,
        });
    });
    return normalized;
}

function readPrepareResumeQueue() {
    const stored = (typeof readStoredJson === 'function')
        ? readStoredJson(PREPARE_RESUME_STORAGE_KEY, null)
        : null;
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return null;
    const items = normalizePrepareResumeItems(stored.items);
    if (!items.length) return null;
    return {
        items,
        reason: String(stored.reason || ''),
        autoResume: stored.autoResume === true,
        savedAt: Number(stored.savedAt) || 0,
    };
}

function writePrepareResumeQueue(payload) {
    const items = normalizePrepareResumeItems(payload?.items);
    if (!items.length) {
        clearPrepareResumeQueue();
        return false;
    }
    if (typeof writeStoredJson !== 'function') return false;
    return writeStoredJson(PREPARE_RESUME_STORAGE_KEY, {
        items,
        reason: String(payload?.reason || '').slice(0, 200),
        autoResume: payload?.autoResume === true,
        savedAt: Number(payload?.savedAt) || Date.now(),
    });
}

function clearPrepareResumeQueue() {
    if (typeof removeStoredKey === 'function') {
        removeStoredKey(PREPARE_RESUME_STORAGE_KEY);
    }
}

function _prepareRestartT(key, fallback, params) {
    if (typeof appT === 'function') return appT(key, fallback, params);
    if (!params) return fallback;
    return Object.keys(params).reduce(
        (text, name) => text.split(`{${name}}`).join(String(params[name])),
        fallback,
    );
}

function ensurePrepareRestartBanner() {
    const gridEl = document.getElementById('model-manager-grid');
    let banner = document.getElementById(PREPARE_RESUME_BANNER_ID);
    if (banner) return banner;
    if (!gridEl || !gridEl.parentElement) return null;
    banner = document.createElement('div');
    banner.id = PREPARE_RESUME_BANNER_ID;
    banner.className = 'prepare-restart-banner';
    banner.setAttribute('role', 'status');
    const notice = document.getElementById('feature-availability-notice');
    const anchor = notice && notice.parentElement === gridEl.parentElement
        ? notice
        : gridEl;
    gridEl.parentElement.insertBefore(banner, anchor);
    return banner;
}

function hidePrepareRestartBanner() {
    const banner = document.getElementById(PREPARE_RESUME_BANNER_ID);
    if (banner) banner.remove();
}

function _bindPrepareRestartBanner(banner, items) {
    const restartBtn = banner.querySelector('[data-action="restart-and-continue"]');
    const dismissBtn = banner.querySelector('[data-action="dismiss-restart-queue"]');
    if (restartBtn) {
        restartBtn.addEventListener('click', () => {
            restartBtn.disabled = true;
            requestAppRestartAndContinue({
                reason: 'model_dependency_install',
                items,
            }).then((result) => {
                if (result?.status === 'scheduled') {
                    if (dismissBtn) dismissBtn.disabled = true;
                    return;
                }
                restartBtn.disabled = false;
                const statusEl = banner.querySelector('[data-role="restart-status"]');
                if (statusEl) {
                    statusEl.textContent = result?.status === 'error'
                        ? _prepareRestartT(
                            'models.restartFailed',
                            'Could not restart the app automatically. Close the window and run the launcher again.',
                        )
                        : _prepareRestartT(
                            'models.restartUnsupported',
                            'This session was not started from the launcher, so the app cannot restart itself. Close the window and run run.bat / run.sh; remaining downloads will continue afterwards.',
                        );
                }
            });
        });
    }
    if (dismissBtn) {
        dismissBtn.addEventListener('click', () => {
            if (_appRestartInFlight) return;
            clearPrepareResumeQueue();
            hidePrepareRestartBanner();
        });
    }
}

function showPrepareRestartPrompt({ items, host } = {}) {
    const remaining = normalizePrepareResumeItems(items);
    if (!remaining.length) return null;
    writePrepareResumeQueue({
        items: remaining,
        reason: 'model_dependency_install',
        autoResume: false,
        savedAt: Date.now(),
    });
    const progressBanner = document.getElementById('bulk-download-progress-banner');
    if (progressBanner && progressBanner !== host) {
        progressBanner.remove();
    }
    const banner = host || ensurePrepareRestartBanner();
    if (!banner) return null;
    banner.id = PREPARE_RESUME_BANNER_ID;
    banner.className = 'prepare-restart-banner';
    banner.setAttribute('role', 'status');
    const title = _prepareRestartT('models.bulkNeedsRestart', 'Restart required');
    const explain = _prepareRestartT(
        'models.bulkRestartExplain',
        'A feature installed Python packages that this process cannot load yet. Restart now and the remaining downloads will continue afterwards.',
    );
    banner.innerHTML = (
        `<div class="prepare-restart-banner-copy">`
        + `<strong>${escapeHtml(title)}</strong>`
        + `<p>${escapeHtml(explain)}</p>`
        + `<p data-role="restart-status"></p>`
        + `</div>`
        + `<div class="prepare-restart-banner-actions">`
        + `<button type="button" class="btn btn-primary" data-action="restart-and-continue">${escapeHtml(_prepareRestartT('models.restartNowAndContinue', 'Restart now and continue'))}</button>`
        + `<button type="button" class="btn btn-ghost" data-action="dismiss-restart-queue">${escapeHtml(_prepareRestartT('models.restartQueueDismiss', 'Not now'))}</button>`
        + `</div>`
    );
    _bindPrepareRestartBanner(banner, remaining);
    if (typeof banner.scrollIntoView === 'function') {
        banner.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    return banner;
}

async function requestAppRestartAndContinue({ reason, items } = {}) {
    const remaining = normalizePrepareResumeItems(items);
    writePrepareResumeQueue({
        items: remaining,
        reason: reason || 'model_dependency_install',
        autoResume: remaining.length > 0,
        savedAt: Date.now(),
    });
    const persistQueue = (autoResume) => writePrepareResumeQueue({
        items: remaining,
        reason: reason || 'model_dependency_install',
        autoResume,
        savedAt: Date.now(),
    });
    const api = window.App?.API || window.API;
    if (!api || typeof api.restartApp !== 'function') {
        persistQueue(true);
        return { status: 'unsupported', reason: 'api-unavailable' };
    }
    _appRestartInFlight = true;
    try {
        const result = await api.restartApp({
            reason: String(reason || 'model_dependency_install').slice(0, 200),
        });
        if (result?.status === 'scheduled') {
            persistQueue(true);
            if (typeof showGlobalLoading === 'function') {
                showGlobalLoading(_prepareRestartT('models.restartingNow', 'Restarting the app...'));
            }
            if (typeof showToast === 'function') {
                showToast(
                    _prepareRestartT(
                        'models.restartScheduled',
                        'Restarting now. Remaining downloads will continue afterwards.',
                    ),
                    'info',
                );
            }
            return result;
        }
        _appRestartInFlight = false;
        persistQueue(true);
        if (typeof showToast === 'function') {
            showToast(
                _prepareRestartT(
                    'models.restartUnsupported',
                    'This session was not started from the launcher, so the app cannot restart itself. Close the window and run run.bat / run.sh; remaining downloads will continue afterwards.',
                ),
                'warning',
            );
        }
        return result || { status: 'unsupported' };
    } catch (error) {
        _appRestartInFlight = false;
        persistQueue(true);
        if (typeof showToast === 'function') {
            const formatted = (typeof formatUserError === 'function')
                ? formatUserError(
                    error,
                    _prepareRestartT(
                        'models.restartFailed',
                        'Could not restart the app automatically. Close the window and run the launcher again.',
                    ),
                )
                : String(error?.message || error);
            showToast(formatted, 'error');
        }
        return { status: 'error', error };
    }
}

function maybeResumePrepareQueue({ resumeNow } = {}) {
    const resume = readPrepareResumeQueue();
    if (!resume) return;
    const bulkBtn = document.getElementById('btn-bulk-download-models');
    if (bulkBtn?.disabled) return;
    const shouldResumeNow = resumeNow === true;
    if (shouldResumeNow) {
        writePrepareResumeQueue({ ...resume, autoResume: false });
        hidePrepareRestartBanner();
        if (typeof showToast === 'function') {
            showToast(
                _prepareRestartT(
                    'models.restartResumeExplain',
                    '{count} model(s) were waiting after the last restart. Setup will continue now.',
                    { count: resume.items.length },
                ),
                'info',
            );
        }
        if (typeof runBulkDownload === 'function') {
            runBulkDownload(resume.items).catch((error) => {
                if (typeof showToast === 'function') {
                    showToast(String(error?.message || error), 'error');
                }
            });
        }
        return;
    }
    showPrepareRestartPrompt({ items: resume.items });
}
