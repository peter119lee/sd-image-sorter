/**
 * Folder Browser Widget + System Info Display
 * Provides a folder tree picker for the scan modal
 * and system hardware info display for the tag modal.
 */

// ============== Folder Browser ==============

function fbT(key, fallback) {
    const translated = window.I18n?.t?.(key);
    return translated && translated !== key ? translated : fallback;
}

/**
 * Fetch folder contents from the backend browse-folder API.
 * @param {string} path - Folder path to browse (empty string for root/drives)
 * @returns {Promise<{current: string, parent: string|null, subdirs: Array}>}
 */
async function fetchFolderContents(path) {
    const resp = await fetch('/api/browse-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path || '' })
    });
    if (!resp.ok) {
        const errData = await resp.json().catch(function() { return {}; });
        throw new Error(errData.detail || 'Failed to browse folder');
    }
    return resp.json();
}

/** Currently active folder browser state (null when closed). */
let _folderBrowserState = null;

function _getFolderBrowserContainer(inputElement) {
    const customId = inputElement?.dataset?.folderBrowserContainer || '';
    if (customId) {
        return document.getElementById(customId);
    }
    return document.getElementById('folder-browser-container');
}

/**
 * Show the folder browser panel below the given input element.
 * @param {HTMLInputElement} inputElement - The path input to fill when a folder is selected
 */
async function showFolderBrowser(inputElement) {
    const container = _getFolderBrowserContainer(inputElement);
    if (!container) return;
    const requestedPath = inputElement.value.trim() || '';
    if (_folderBrowserState && _folderBrowserState.inputElement === inputElement) {
        _folderBrowserState.currentPath = requestedPath || _folderBrowserState.currentPath || '';
        _folderBrowserState.selectedPath = _folderBrowserState.currentPath;
        _folderBrowserState.container = container;
        await _renderFolderBrowser(container, _folderBrowserState.currentPath);
        return;
    }
    hideFolderBrowser();
    _folderBrowserState = {
        inputElement: inputElement,
        container: container,
        currentPath: requestedPath,
        selectedPath: null
    };
    await _renderFolderBrowser(container, _folderBrowserState.currentPath);
}

window.showFolderBrowser = showFolderBrowser;
window.hideFolderBrowser = hideFolderBrowser;

/** Hide and destroy the folder browser panel. */
function hideFolderBrowser() {
    const container = _folderBrowserState?.container || document.getElementById('folder-browser-container');
    if (container) container.innerHTML = '';
    _folderBrowserState = null;
}

/**
 * Render the folder browser panel contents.
 * @param {HTMLElement} container - The container element
 * @param {string} path - Path to browse
 */
async function _renderFolderBrowser(container, path) {
    container.innerHTML = `<div class="folder-browser"><div class="folder-browser-loading">${escapeHtml(fbT('folderBrowser.loading', 'Loading folders...'))}</div></div>`;
    try {
        const data = await fetchFolderContents(path);
        _folderBrowserState.currentPath = data.current;
        _folderBrowserState.selectedPath = data.current;
        const parentDisabled = data.parent == null ? ' disabled' : '';
        const currentDisplay = data.current || fbT('folderBrowser.computer', 'Computer');
        let listHtml = '';
        if (data.subdirs.length === 0) {
            listHtml = `<div class="folder-browser-empty">${escapeHtml(fbT('folderBrowser.empty', 'No subfolders found'))}</div>`;
        } else {
            listHtml = data.subdirs.map(function(dir) {
                const arrow = dir.has_children ? '<span class="folder-browser-item-arrow">&#9654;</span>' : '';
                return '<div class="folder-browser-item" data-path="' + escapeHtml(dir.path) + '" data-has-children="' + dir.has_children + '">' +
                    '<span class="folder-browser-item-icon">&#128193;</span>' +
                    '<span class="folder-browser-item-name">' + escapeHtml(dir.name) + '</span>' +
                    arrow + '</div>';
            }).join('');
        }
        container.innerHTML = '<div class="folder-browser">' +
            '<div class="folder-browser-header">' +
            '<button type="button" class="folder-browser-btn" id="folder-browser-up"' + parentDisabled +
            ' title="' + escapeHtml(fbT('folderBrowser.upTitle', 'Go to parent folder')) + '" aria-label="' + escapeHtml(fbT('folderBrowser.upTitle', 'Go to parent folder')) + '">&uarr; ' + escapeHtml(fbT('folderBrowser.up', 'Up')) + '</button>' +
            '<span class="folder-browser-path" title="' + escapeHtml(currentDisplay) + '">' + escapeHtml(currentDisplay) + '</span></div>' +
            '<div class="folder-browser-list">' + listHtml + '</div>' +
            '<div class="folder-browser-footer">' +
            '<button type="button" class="folder-browser-btn" id="folder-browser-cancel">' + escapeHtml(fbT('folderBrowser.cancel', 'Cancel')) + '</button>' +
            '<button type="button" class="folder-browser-btn" id="folder-browser-select" style="background:rgba(var(--accent-rgb), 0.2);color:var(--accent-primary);">' + escapeHtml(fbT('folderBrowser.select', 'Select This Folder')) + '</button>' +
            '</div></div>';
        _attachFolderBrowserEvents(container, data);
    } catch (err) {
        if (path) {
            try { await _renderFolderBrowser(container, ''); return; }
            catch (rootErr) { /* fall through */ }
        }
        container.innerHTML = '<div class="folder-browser">' +
            '<div class="folder-browser-error">' + escapeHtml(fbT('folderBrowser.errorPrefix', 'Error: ')) + escapeHtml(err.message) + '</div>' +
            '<div class="folder-browser-footer">' +
            '<button type="button" class="folder-browser-btn" id="folder-browser-cancel">' + escapeHtml(fbT('folderBrowser.close', 'Close')) + '</button></div></div>';
        const cancelBtn = container.querySelector('#folder-browser-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', hideFolderBrowser);
    }
}

/**
 * Attach click handlers to folder browser elements.
 * @param {HTMLElement} container - The browser container
 * @param {Object} data - The browse-folder API response
 */
function _attachFolderBrowserEvents(container, data) {
    const upBtn = container.querySelector('#folder-browser-up');
    if (upBtn && data.parent != null) {
        upBtn.addEventListener('click', function() {
            _renderFolderBrowser(container, data.parent);
        });
    }
    const cancelBtn = container.querySelector('#folder-browser-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', hideFolderBrowser);
    const selectBtn = container.querySelector('#folder-browser-select');
    if (selectBtn) {
        selectBtn.addEventListener('click', function() {
            if (_folderBrowserState && _folderBrowserState.inputElement) {
                const pathToSet = _folderBrowserState.selectedPath || _folderBrowserState.currentPath || '';
                _folderBrowserState.inputElement.value = pathToSet;
                _folderBrowserState.inputElement.dispatchEvent(new Event('input', { bubbles: true }));
                _folderBrowserState.inputElement.dispatchEvent(new Event('change', { bubbles: true }));
            }
            hideFolderBrowser();
        });
    }
    const items = container.querySelectorAll('.folder-browser-item');
    items.forEach(function(item) {
        item.addEventListener('click', function() {
            const itemPath = item.getAttribute('data-path');
            const hasChildren = item.getAttribute('data-has-children') === 'true';
            items.forEach(function(it) { it.classList.remove('selected'); });
            item.classList.add('selected');
            if (_folderBrowserState) _folderBrowserState.selectedPath = itemPath;
            if (hasChildren) _renderFolderBrowser(container, itemPath);
        });
        item.addEventListener('dblclick', function() {
            const itemPath = item.getAttribute('data-path');
            if (_folderBrowserState && _folderBrowserState.inputElement) {
                _folderBrowserState.inputElement.value = itemPath;
                _folderBrowserState.inputElement.dispatchEvent(new Event('input', { bubbles: true }));
                _folderBrowserState.inputElement.dispatchEvent(new Event('change', { bubbles: true }));
            }
            hideFolderBrowser();
        });
    });
}


// ============== System Info ==============

let _loadSystemInfoPromise = null;

/**
 * Load system hardware info and display it in the tagger modal.
 * Called when the tag-modal is opened. Fails silently (info is optional).
 */
async function loadSystemInfo() {
    if (_loadSystemInfoPromise) {
        return _loadSystemInfoPromise;
    }

    window.__taggerSystemInfoStatus = 'loading';

    _loadSystemInfoPromise = (async () => {
    try {
        const resp = await fetch('/api/system-info');
        if (!resp.ok) {
            window.__taggerSystemInfoStatus = 'error';
            return null;
        }
        const data = await resp.json();
        window.__taggerSystemInfo = data;
        window.__taggerSystemInfoStatus = 'loaded';
        const panel = document.getElementById('system-info-panel');
        const contentEl = document.getElementById('system-info-content');
        const hardwareChip = document.getElementById('tag-hardware-chip');
        const riskChip = document.getElementById('tag-risk-chip');
        const providerChip = document.getElementById('tag-provider-chip');
        const recEl = document.getElementById('tag-system-recommendation');
        const batchEl = document.getElementById('tag-batch-recommendation');
        if (!panel || !contentEl) return;
        const sys = data.system_info || {};
        const modelSelect = document.getElementById('tag-model-select');
        const isCustom = modelSelect?.value === 'custom';
        const selectedModel = isCustom ? 'custom' : String(modelSelect?.value || 'wd-swinv2-tagger-v3');
        const useGpu = document.getElementById('tag-use-gpu')?.checked ?? true;
        const rec = data.recommendations_by_model?.[selectedModel]?.[useGpu ? 'gpu' : 'cpu'] || data.recommendation || {};
        const providers = Array.isArray(sys.onnx_providers) ? sys.onnx_providers.map(String) : [];
        const gpuDevices = Array.isArray(sys.gpu_devices) ? sys.gpu_devices : [];
        const hasCuda = providers.indexOf('CUDAExecutionProvider') !== -1;
        const hasDml = providers.indexOf('DmlExecutionProvider') !== -1;
        const hasTensorRt = providers.indexOf('TensorrtExecutionProvider') !== -1;
        const hasTorchCuda = Boolean(sys.torch_cuda_available);
        const parts = [];
        const gpuNames = gpuDevices
            .map((device) => String(device?.name || '').trim())
            .filter(Boolean);
        const primaryGpuName = String(sys.gpu_name || gpuNames[0] || '').trim();
        if (primaryGpuName) {
            const uniqueGpuNames = Array.from(new Set([primaryGpuName, ...gpuNames]));
            parts.push(uniqueGpuNames.join(' / '));
        } else {
            parts.push(fbT('system.noGpuDetected', 'No GPU detected'));
        }
        if (sys.total_ram_gb) { parts.push(sys.total_ram_gb.toFixed(0) + 'GB RAM'); }
        if (sys.gpu_vram_total_mb) { parts.push((sys.gpu_vram_total_mb / 1024).toFixed(1) + 'GB VRAM'); }
        if (contentEl) {
            contentEl.removeAttribute('data-i18n');
            contentEl.textContent = parts.join(' · ') || fbT('system.detectedHardware', 'Detected Hardware');
        }
        panel.style.display = '';

        if (hardwareChip) {
            hardwareChip.removeAttribute('data-i18n');
            const gpuDetected = Boolean(primaryGpuName);
            hardwareChip.textContent = gpuDetected
                ? fbT('tagger.gpuAvailable', 'GPU available')
                : fbT('tagger.cpuSafe', 'CPU safe');
            hardwareChip.className = 'system-info-chip ' + (gpuDetected ? 'is-safe' : 'is-warning');
        }

        if (riskChip) {
            const riskLevel = String(rec.risk_level || 'medium').toLowerCase();
            const riskClass = riskLevel === 'high'
                ? 'is-danger'
                : (riskLevel === 'medium' ? 'is-warning' : 'is-safe');
            const riskKey = 'tagger.risk' + riskLevel.charAt(0).toUpperCase() + riskLevel.slice(1);
            riskChip.removeAttribute('data-i18n');
            riskChip.textContent = fbT('tagger.riskPrefix', 'Risk: ') + fbT(riskKey, riskLevel);
            riskChip.className = 'system-info-chip ' + riskClass;
        }

        if (providerChip) {
            providerChip.removeAttribute('data-i18n');
            if (hasTensorRt) {
                providerChip.textContent = fbT('tagger.tensorrtReady', 'TensorRT + CUDA ready');
                providerChip.className = 'system-info-chip is-safe';
            } else if (hasCuda) {
                providerChip.textContent = fbT('tagger.cudaReady', 'CUDA ready');
                providerChip.className = 'system-info-chip is-safe';
            } else if (hasDml) {
                providerChip.textContent = fbT('tagger.directmlReady', 'DirectML ready');
                providerChip.className = 'system-info-chip is-warning';
            } else if (hasTorchCuda) {
                providerChip.textContent = fbT('tagger.pytorchCudaOnly', 'PyTorch CUDA only');
                providerChip.className = 'system-info-chip is-warning';
            } else {
                providerChip.textContent = fbT('tagger.cpuRuntime', 'CPU runtime');
                providerChip.className = 'system-info-chip is-warning';
            }
        }

        if (recEl) {
            recEl.removeAttribute('data-i18n');
            recEl.textContent = rec.message || 'The app will use the recommended runtime for your hardware.';
        }

        if (batchEl) {
            batchEl.removeAttribute('data-i18n');
            const recommendedChunk = rec.recommended_batch_size || 8;
            batchEl.textContent = fbT('tagger.chunkHelpRecommended', 'Recommended chunk size: {chunk}. Leave this alone unless you are deliberately tuning throughput.').replace('{chunk}', recommendedChunk);
        }

        // Set recommended runtime chunk size in the advanced dropdown
        const batchSelect = document.getElementById('tagger-batch-size');
        if (batchSelect && rec.recommended_batch_size && batchSelect.dataset.userChosen !== '1') {
            const clamp = window.clampTaggerChunkToAvailableOption;
            batchSelect.value = typeof clamp === 'function'
                ? clamp(rec.recommended_batch_size)
                : String(rec.recommended_batch_size);
        }

        if (typeof syncTaggerModelUi === 'function') {
            syncTaggerModelUi({ applyModelDefaults: true });
        }
        return data;
    } catch (e) {
        window.__taggerSystemInfoStatus = 'error';
        return null;
    }
    })().finally(() => {
        _loadSystemInfoPromise = null;
    });

    return _loadSystemInfoPromise;
}


// ============== Init on DOMContentLoaded ==============

function bindFolderBrowserButton() {
    const btnBrowse = document.getElementById('btn-browse-folder');
    if (btnBrowse && btnBrowse.dataset.browserBound !== '1') {
        btnBrowse.dataset.browserBound = '1';
        btnBrowse.addEventListener('click', function() {
            const pathInput = document.getElementById('scan-folder-path');
            if (pathInput) showFolderBrowser(pathInput);
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindFolderBrowserButton);
} else {
    bindFolderBrowserButton();
}

