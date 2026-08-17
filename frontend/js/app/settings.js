/**
 * app/settings.js — app.js decomposition, stage 5 (feature flows).
 * Extracted VERBATIM (byte-identical) from frontend/js/app.js, stage-5
 * pre-cut lines 7444-7771 (of 10,152): settings controls (disk/sound/ui-scale/AI defaults).
 * Classic script: shares ONE global lexical environment with app.js and
 * the other app/ parts; index.html loads every app/ file BEFORE app.js
 * (tag order = original line order). No behavior change intended.
 */
function getThumbnailCacheSettings(data = {}) {
    const settingsLimit = Number(data?.settings?.thumbnail_cache_max_mb);
    if (Number.isFinite(settingsLimit)) return settingsLimit;
    const statsLimit = Number(data?.thumbnail_cache?.max_size_mb);
    if (Number.isFinite(statsLimit)) return statsLimit;
    return 500;
}

async function requestCoreRuntimeRebuild() {
    const runtime = await API.getCacheStatus().then(data => data?.runtime_environment || {}).catch(() => ({}));
    const venvSizeBytes = Number(runtime.venv_size_bytes || 0);
    const venvSize = _formatBytes(venvSizeBytes);
    showConfirm(
        appT('disk.rebuildCoreConfirmTitle', 'Rebuild lightweight runtime on next start?'),
        appT('disk.rebuildCoreConfirmBody', 'This schedules the app-owned Python runtime to be rebuilt the next time you start the app, then core dependencies are reinstalled. User data, images.db, settings, caches, and downloaded models are not deleted. Current runtime size: {size}. Heavy AI Python packages must be prepared again later.', { size: venvSize }),
        async () => {
            const button = $('#btn-rebuild-core-runtime');
            const originalLabel = button?.textContent || '';
            if (button) {
                button.disabled = true;
                button.textContent = appT('disk.rebuildScheduling', 'Scheduling…');
            }
            try {
                await API.rebuildCoreRuntime();
                showToast(appT('disk.rebuildScheduled', 'Lightweight runtime rebuild scheduled. Close the app and start it again to free the old Python environment.'), 'warning');
                loadDiskUsage();
            } catch (error) {
                if (button) {
                    button.disabled = false;
                    button.textContent = originalLabel;
                }
                showToast(formatUserError(error, appT('disk.rebuildFailed', 'Failed to schedule runtime rebuild')), 'error');
            }
        }
    );
}

async function saveDiskSettings() {
    const input = $('#thumbnail-cache-limit-input');
    const button = $('#btn-save-cache-settings');
    if (!input || !button) return;

    const rawValue = Number(input.value);
    if (!Number.isFinite(rawValue) || rawValue < 0 || rawValue > 102400) {
        showToast(appT('disk.limitInvalid', 'Enter a cache limit from 0 to 102400 MB.'), 'warning');
        return;
    }

    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = appT('disk.saving', 'Saving…');
    try {
        const result = await API.setDiskSettings({ thumbnail_cache_max_mb: Math.round(rawValue) });
        renderDiskUsage({
            ...(await API.getCacheStatus().catch(() => ({}))),
            ...result,
        });
        const freedBytes = Number(result?.limit_cleanup?.freed_bytes || 0);
        const message = freedBytes > 0
            ? appT('disk.limitSavedFreed', 'Cache limit saved. Freed {freed}.', { freed: _formatBytes(freedBytes) })
            : appT('disk.limitSaved', 'Cache limit saved.');
        showToast(message, 'success');
    } catch (error) {
        button.disabled = false;
        button.textContent = originalLabel;
        showToast(formatUserError(error, appT('disk.limitSaveFailed', 'Failed to save cache limit')), 'error');
    }
}


function bindDatasetAuditLazyInit() {
    const details = document.getElementById('audit-section');
    if (!details || details.dataset.auditBound === '1') return;
    details.dataset.auditBound = '1';
    details.addEventListener('toggle', () => {
        if (details.open && window.LibraryHealth && typeof window.LibraryHealth.init === 'function') {
            window.LibraryHealth.init();
        }
    });
}

// Mirrors audio.js: the sort blip defaults OFF, so only an explicit stored
// 'true' counts as enabled.
function isSortAudioEnabled() {
    if (window.AudioManager && typeof window.AudioManager.enabled === 'boolean') {
        return window.AudioManager.enabled;
    }
    return localStorage.getItem('sort-audio-enabled') === 'true';
}

function syncSettingsSoundControl() {
    const btn = document.getElementById('btn-settings-sound-toggle');
    if (!btn) return;
    const enabled = isSortAudioEnabled();
    const icon = document.getElementById('settings-sound-icon');
    const label = document.getElementById('settings-sound-label');
    const labelText = enabled
        ? appT('settings.soundOn', 'On')
        : appT('settings.soundOff', 'Muted');
    btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    btn.classList.toggle('is-muted', !enabled);
    btn.setAttribute(
        'aria-label',
        enabled
            ? appT('settings.soundToggleOff', 'Mute manual sort sounds')
            : appT('settings.soundToggleOn', 'Enable manual sort sounds')
    );
    if (icon) icon.innerHTML = enabled ? "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-volume\"/></svg>" : "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-volume-off\"/></svg>";
    if (label) {
        label.dataset.i18n = enabled ? 'settings.soundOn' : 'settings.soundOff';
        label.textContent = labelText;
    }
}

function toggleSettingsSound() {
    let enabled;
    if (window.AudioManager && typeof window.AudioManager.toggle === 'function') {
        enabled = window.AudioManager.toggle();
    } else {
        enabled = localStorage.getItem('sort-audio-enabled') !== 'true';
        localStorage.setItem('sort-audio-enabled', enabled ? 'true' : 'false');
    }
    syncSettingsSoundControl();
    showToast(
        enabled
            ? appT('settings.soundSavedOn', 'Manual sort sounds enabled')
            : appT('settings.soundSavedOff', 'Manual sort sounds muted'),
        'info'
    );
}

function getSettingsUiScaleMode() {
    const mode = String(AppPreferences.getUiScaleMode() || 'auto');
    return ['auto', '1', '1.15', '1.3', '1.4', '1.5'].includes(mode) ? mode : 'auto';
}

function formatScalePercent(scale) {
    const numeric = Number(scale);
    return `${Math.round((Number.isFinite(numeric) ? numeric : 1) * 100)}%`;
}

function syncSettingsUiScaleControl() {
    const select = document.getElementById('settings-ui-scale');
    if (!select) return;
    const mode = getSettingsUiScaleMode();
    select.value = mode;

    const current = document.getElementById('settings-ui-scale-current');
    if (current) {
        delete current.dataset.i18n;
        const percent = formatScalePercent(window.UiScale?.get?.() || 1);
        current.textContent = mode === 'auto'
            ? appT('settings.uiScaleCurrentAuto', 'Auto is active now ({percent}).', { percent })
            : appT('settings.uiScaleCurrentManual', 'Fixed scale is active now ({percent}).', { percent });
    }
}

function handleSettingsUiScaleChange(event) {
    const mode = event.target?.value || 'auto';
    AppPreferences.setUiScaleMode(mode);
    syncSettingsUiScaleControl();
    showToast(
        mode === 'auto'
            ? appT('settings.uiScaleSavedAuto', 'UI scale set to Auto')
            : appT('settings.uiScaleSavedManual', 'UI scale set to {percent}', { percent: formatScalePercent(mode) }),
        'info'
    );
}

function captureArtistDefaultsFromDom() {
    return {
        modelSource: document.getElementById('artist-model-source')?.value || 'huggingface',
        modelPath: document.getElementById('artist-model-path')?.value || '',
        threshold: finiteNumberInRange(document.getElementById('artist-threshold')?.value, 0, 0.25, 0.03),
        useGpu: document.getElementById('artist-use-gpu') ? !!document.getElementById('artist-use-gpu').checked : true,
    };
}

function persistArtistDefaultsFromDom() {
    const saved = AppPreferences.setArtistDefaults(captureArtistDefaultsFromDom());
    syncSettingsPreferenceStatus();
    return saved;
}

function formatGpuPreferenceLabel(value) {
    return value
        ? appT('settings.prefGpu', 'GPU')
        : appT('settings.prefCpu', 'CPU');
}

function syncSettingsPreferenceStatus() {
    const taggerStatus = document.getElementById('settings-tagger-defaults-status');
    if (taggerStatus) {
        delete taggerStatus.dataset.i18n;
        const prefs = AppPreferences.getTaggerDefaults();
        if (prefs?.modelName) {
            const threshold = prefs.threshold != null ? Number(prefs.threshold).toFixed(2) : '0.35';
            const character = prefs.characterThreshold != null ? Number(prefs.characterThreshold).toFixed(2) : '0.85';
            taggerStatus.textContent = appT(
                'settings.taggerDefaultsSaved',
                '{model} · General {threshold} · Character {character} · {gpu} · Batch {batch}',
                {
                    model: prefs.modelName,
                    threshold,
                    character,
                    gpu: formatGpuPreferenceLabel(prefs.useGpu !== false),
                    batch: prefs.batchSize || appT('settings.prefAutoBatch', 'Auto'),
                }
            );
        } else {
            taggerStatus.dataset.i18n = 'settings.taggerDefaultsEmpty';
            taggerStatus.textContent = appT('settings.taggerDefaultsEmpty', 'No saved tagger defaults yet.');
        }
    }

    const artistStatus = document.getElementById('settings-artist-defaults-status');
    if (artistStatus) {
        delete artistStatus.dataset.i18n;
        const prefs = AppPreferences.getArtistDefaults();
        if (prefs?.modelSource) {
            artistStatus.textContent = appT(
                'settings.artistDefaultsSaved',
                '{source} · Threshold {threshold} · {gpu}',
                {
                    source: prefs.modelSource,
                    threshold: Number(prefs.threshold ?? 0.03).toFixed(2),
                    gpu: formatGpuPreferenceLabel(prefs.useGpu !== false),
                }
            );
        } else {
            artistStatus.dataset.i18n = 'settings.artistDefaultsEmpty';
            artistStatus.textContent = appT('settings.artistDefaultsEmpty', 'No saved Artist ID defaults yet.');
        }
    }
}

function saveCurrentAiDefaults() {
    persistTaggerDefaultsFromDom();
    if (window.ArtistIdent && typeof window.ArtistIdent.savePreferences === 'function') {
        window.ArtistIdent.savePreferences();
    } else {
        persistArtistDefaultsFromDom();
    }
    syncSettingsPreferenceStatus();
    showToast(appT('settings.aiDefaultsSaved', 'AI defaults saved'), 'success');
}

function resetArtistDefaultControls() {
    const source = document.getElementById('artist-model-source');
    const path = document.getElementById('artist-model-path');
    const localGroup = document.getElementById('artist-local-model-group');
    const threshold = document.getElementById('artist-threshold');
    const thresholdValue = document.getElementById('artist-threshold-value');
    const useGpu = document.getElementById('artist-use-gpu');

    if (source) source.value = 'huggingface';
    if (path) path.value = '';
    if (localGroup) localGroup.style.display = 'none';
    if (threshold) threshold.value = '0.03';
    if (thresholdValue) thresholdValue.textContent = '0.03';
    if (useGpu) useGpu.checked = true;
}

function resetAiDefaultPreferences() {
    AppPreferences.clearAiDefaults();
    resetTaggerDefaultControls();
    if (window.ArtistIdent && typeof window.ArtistIdent.resetSavedPreferences === 'function') {
        window.ArtistIdent.resetSavedPreferences({ apply: true, silent: true });
    } else {
        resetArtistDefaultControls();
    }
    syncSettingsPreferenceStatus();
    showToast(appT('settings.aiDefaultsReset', 'AI defaults reset'), 'info');
}

function syncSettingsControls() {
    syncSettingsSoundControl();
    syncSettingsUiScaleControl();
    syncSettingsPreferenceStatus();
}

function initSettingsControls() {
    const soundBtn = document.getElementById('btn-settings-sound-toggle');
    if (soundBtn && soundBtn.dataset.bound !== '1') {
        soundBtn.dataset.bound = '1';
        soundBtn.addEventListener('click', toggleSettingsSound);
    }

    // Owner 2026-07-05: toggle rows read as "not working" — only the small
    // button was clickable and its state change was subtle. Make the WHOLE
    // row a click target for every row whose control is a pressed-state
    // button (sound / entry page / ★5 cover; future rows get it for free).
    document.querySelectorAll('.settings-row').forEach((row) => {
        if (row.dataset.rowToggleBound === '1') return;
        const toggle = row.querySelector('button[aria-pressed]');
        if (!toggle) return;
        row.dataset.rowToggleBound = '1';
        row.classList.add('settings-row-toggle');
        row.addEventListener('click', (event) => {
            if (event.target.closest('button')) return; // button handles itself
            toggle.click();
        });
    });

    const uiScale = document.getElementById('settings-ui-scale');
    if (uiScale && uiScale.dataset.bound !== '1') {
        uiScale.dataset.bound = '1';
        uiScale.addEventListener('change', handleSettingsUiScaleChange);
    }

    const saveAi = document.getElementById('btn-settings-save-ai-defaults');
    if (saveAi && saveAi.dataset.bound !== '1') {
        saveAi.dataset.bound = '1';
        saveAi.addEventListener('click', saveCurrentAiDefaults);
    }

    const resetAi = document.getElementById('btn-settings-reset-ai-defaults');
    if (resetAi && resetAi.dataset.bound !== '1') {
        resetAi.dataset.bound = '1';
        resetAi.addEventListener('click', resetAiDefaultPreferences);
    }

    if (document.body && document.body.dataset.settingsLanguageBound !== '1') {
        document.body.dataset.settingsLanguageBound = '1';
        document.addEventListener('languageChanged', syncSettingsControls);
    }

    syncSettingsControls();
}

