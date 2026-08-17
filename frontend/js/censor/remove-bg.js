/**
 * Censor Editor - remove background (split VERBATIM from censor-edit.js; god-file decomposition).
 * AI background-removal modal (preview via /api/censor/remove-background, apply with undo support).
 * Shared top-level bindings (CensorState, ...) are declared in censor/state.js;
 * classic-script global lexical scoping keeps them single instances across parts.
 * Load order is pinned in index.html - see censor/state.js for the full note.
 */
async function showRemoveBackgroundPreview() {
    const activeItem = getActiveCensorItem();
    if (!activeItem || !CensorState.activeId) {
        window.App?.showToast?.(
            censorT('censor.noImageLoaded', null, 'No image loaded'),
            'error'
        );
        return;
    }

    // Create modal for background removal settings
    const modalHtml = `
        <div class="modal active" id="remove-bg-modal" role="dialog" aria-modal="true">
            <div class="modal-backdrop"></div>
            <div class="modal-content">
                <button class="modal-close" id="remove-bg-close" aria-label="Close">
                    <span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></span>
                </button>
                <div class="modal-header">
                    <h2>${censorT('censor.removeBgTitle', null, 'Remove Background')}</h2>
                    <p class="modal-description">${censorT('censor.removeBgDesc', null, 'Use AI to detect and remove the background from the image.')}</p>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label for="remove-bg-fill-mode">${censorT('censor.fillMode', null, 'Background Fill')}</label>
                        <select id="remove-bg-fill-mode" class="input-field">
                            <option value="transparent">${censorT('censor.fillTransparent', null, 'Transparent (PNG)')}</option>
                            <option value="white">${censorT('censor.fillWhite', null, 'White Background')}</option>
                            <option value="black">${censorT('censor.fillBlack', null, 'Black Background')}</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="remove-bg-threshold">${censorT('censor.edgeThreshold', null, 'Edge Threshold')}: <span id="remove-bg-threshold-value">0.5</span></label>
                        <input type="range" id="remove-bg-threshold" min="0" max="1" step="0.05" value="0.5" class="slider">
                        <small>${censorT('censor.edgeThresholdHint', null, 'Higher = stricter detection, lower = more permissive')}</small>
                    </div>
                    <div id="remove-bg-preview-container" style="display: none; margin-top: 1rem;">
                        <img id="remove-bg-preview-img" style="max-width: 100%; height: auto; border: 1px solid var(--border-color); border-radius: 8px; background:
                            repeating-conic-gradient(#ddd 0% 25%, #fff 0% 50%) 50% / 20px 20px;">
                    </div>
                    <div id="remove-bg-status" style="margin-top: 1rem; padding: 0.75rem; border-radius: 4px; display: none;"></div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" id="remove-bg-cancel">${censorT('common.cancel', null, 'Cancel')}</button>
                    <button class="btn btn-primary" id="remove-bg-preview">${censorT('censor.preview', null, 'Preview')}</button>
                    <button class="btn btn-success" id="remove-bg-apply" style="display: none;">${censorT('censor.apply', null, 'Apply')}</button>
                </div>
            </div>
        </div>
    `;

    // Insert modal into DOM
    const modalContainer = document.createElement('div');
    modalContainer.innerHTML = modalHtml;
    document.body.appendChild(modalContainer.firstElementChild);

    const modal = document.getElementById('remove-bg-modal');
    const fillModeSelect = document.getElementById('remove-bg-fill-mode');
    const thresholdSlider = document.getElementById('remove-bg-threshold');
    const thresholdValue = document.getElementById('remove-bg-threshold-value');
    const previewBtn = document.getElementById('remove-bg-preview');
    const applyBtn = document.getElementById('remove-bg-apply');
    const cancelBtn = document.getElementById('remove-bg-cancel');
    const closeBtn = document.getElementById('remove-bg-close');
    const previewContainer = document.getElementById('remove-bg-preview-container');
    const previewImg = document.getElementById('remove-bg-preview-img');
    const statusDiv = document.getElementById('remove-bg-status');

    let currentPreviewData = null;

    // Update threshold value display
    thresholdSlider.addEventListener('input', () => {
        thresholdValue.textContent = thresholdSlider.value;
    });

    // Preview button click
    previewBtn.addEventListener('click', async () => {
        previewBtn.disabled = true;
        previewBtn.textContent = censorT('censor.processing', null, 'Processing...');
        statusDiv.style.display = 'none';
        previewContainer.style.display = 'none';

        try {
            const response = await (window.apiFetch || fetch)('/api/censor/remove-background', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image_id: CensorState.activeId,
                    fill_mode: fillModeSelect.value,
                    edge_threshold: parseFloat(thresholdSlider.value),
                }),
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.detail || 'Background removal failed');
            }

            if (result.status === 'no_match') {
                statusDiv.textContent = result.message || censorT('censor.noForegroundDetected', null, 'No foreground object detected');
                statusDiv.style.display = 'block';
                statusDiv.style.backgroundColor = 'var(--warning-bg, #F7EED5)';
                statusDiv.style.color = 'var(--warning-text, #745915)';
                currentPreviewData = null;
            } else {
                currentPreviewData = result.preview;
                previewImg.src = result.preview;
                previewContainer.style.display = 'block';
                applyBtn.style.display = 'inline-block';
                statusDiv.textContent = censorT('censor.previewReady', null, 'Preview ready. Click Apply to add to canvas.');
                statusDiv.style.display = 'block';
                statusDiv.style.backgroundColor = 'var(--success-bg, #D6EBDE)';
                statusDiv.style.color = 'var(--success-text, #234931)';
            }
        } catch (error) {
            console.error('Background removal error:', error);
            statusDiv.textContent = error.message || censorT('censor.removeBgFailed', null, 'Background removal failed');
            statusDiv.style.display = 'block';
            statusDiv.style.backgroundColor = 'var(--error-bg, #F7DAD8)';
            statusDiv.style.color = 'var(--error-text, #751D19)';
            currentPreviewData = null;
        } finally {
            previewBtn.disabled = false;
            previewBtn.textContent = censorT('censor.preview', null, 'Preview');
        }
    });

    // Apply button click
    applyBtn.addEventListener('click', async () => {
        if (!currentPreviewData) return;

        // Snapshot the pre-apply canvas onto the undo stack — the same push
        // the drawing tools use. (This handler used to call saveUndoState()
        // and updateQueueItemThumbnail(), neither of which ever existed, so
        // Apply threw ReferenceError since the day the modal shipped — found
        // by the censor characterization sweep.)
        pushUndo();

        // Load preview image onto canvas
        const canvas = document.getElementById(CensorState.activeCanvasId || 'censor-canvas');
        const ctx = canvas.getContext('2d');
        const img = new Image();
        img.onload = () => {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            // Canonical post-draw sync (item.currentDataUrl/isModified with
            // base-state + proxy semantics) and queue thumbnail refresh.
            saveCurrentCanvasToState();
            renderQueue();
            window.App?.showToast?.(
                censorT('censor.removeBgApplied', null, 'Background removed successfully'),
                'success'
            );
            modal.remove();
        };
        img.src = currentPreviewData;
    });

    // Close handlers
    const closeModal = () => modal.remove();
    cancelBtn.addEventListener('click', closeModal);
    closeBtn.addEventListener('click', closeModal);
    modal.querySelector('.modal-backdrop').addEventListener('click', closeModal);
}

