/**
 * Censor Editor - detection flows (split VERBATIM from censor-edit.js; god-file decomposition).
 * Quick auto-censor execution plan, single/all detection runs, SAM3 batch refine, text segmentation.
 * Shared top-level bindings (CensorState, ...) are declared in censor/state.js;
 * classic-script global lexical scoping keeps them single instances across parts.
 * Load order is pinned in index.html - see censor/state.js for the full note.
 */
function readCensorDetectionWarnings(result) {
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
        throw new TypeError('Censor detection response must be an object');
    }
    if (!Array.isArray(result.warnings)) {
        throw new TypeError('Censor detection response requires a warnings array');
    }

    const warnings = result.warnings.map((warning) => {
        if (typeof warning !== 'string' || !warning.trim()) {
            throw new TypeError('Censor detection warnings must be non-empty strings');
        }
        return warning.trim();
    });
    return Array.from(new Set(warnings));
}

function getLegacyBackendStatus() {
    return (CensorState.backendModelStatus?.models || []).find(model => model.id === 'legacy') || null;
}

function getQuickAutoCensorFallbackConfig() {
    const legacy = getLegacyBackendStatus();
    const nudenet = (CensorState.backendModelStatus?.models || []).find(model => model.id === 'nudenet') || null;
    const privacyPath = String(
        legacy?.default_model_path
        || legacy?.files?.find(file => file?.recommended_for_censor)?.path
        || ''
    ).trim();

    let fallbackModelType = String(CensorState.backendModelStatus?.recommended_backend || '').trim();
    if (!fallbackModelType) {
        if (nudenet?.available && privacyPath) {
            fallbackModelType = 'both';
        } else if (nudenet?.available) {
            fallbackModelType = 'nudenet';
        } else if (privacyPath) {
            fallbackModelType = 'legacy';
        } else {
            // NudeNet is the first-use download path when nothing is on disk yet.
            fallbackModelType = 'nudenet';
        }
    }

    return {
        legacy,
        nudenet,
        privacyPath,
        fallbackModelType,
        canAutoRestore: Boolean(fallbackModelType || privacyPath),
    };
}

function setPreferredLegacyModelPath(nextPath = '') {
    const normalized = String(nextPath || '').trim();
    const modelPathInput = document.getElementById('censor-model-path');
    const select = document.getElementById('censor-model-file');

    if (modelPathInput) {
        modelPathInput.value = '';
    }

    if (select) {
        const optionExists = Array.from(select.options).some(option => option.value === normalized);
        select.value = optionExists ? normalized : '';
    }

    CensorState.modelPath = normalized;
    localStorage.setItem('censor_model_path', normalized);

    const legacy = getLegacyBackendStatus();
    updateSelectedLegacyModelHelp(legacy);
}

function getSelectedLegacyModelPath() {
    const modelPathInput = document.getElementById('censor-model-path');
    const select = document.getElementById('censor-model-file');
    const manualPath = String(modelPathInput?.value || '').trim();
    if (manualPath) {
        return manualPath;
    }
    return String(select?.value || '').trim();
}

function shouldUseQuickTargetFilters(modelType = document.getElementById('censor-model-type')?.value || 'legacy') {
    const selectedLegacy = getSelectedLegacyModelRecord();
    if (modelType === 'nudenet' || modelType === 'both') {
        return true;
    }
    if (modelType === 'legacy' && selectedLegacy?.profile === 'privacy-censor') {
        return true;
    }
    if (modelType === 'legacy') {
        return getQuickAutoCensorFallbackConfig().canAutoRestore;
    }
    return false;
}

function shouldDisableQuickTargetFilters(modelType = document.getElementById('censor-model-type')?.value || 'legacy') {
    const selectedLegacy = getSelectedLegacyModelRecord();
    if (modelType === 'nudenet' || modelType === 'both') {
        return false;
    }
    if (modelType === 'legacy' && selectedLegacy?.profile === 'privacy-censor') {
        return false;
    }
    return !getQuickAutoCensorFallbackConfig().canAutoRestore;
}

function getSelectedTargetClassesForDetection(modelType) {
    return shouldUseQuickTargetFilters(modelType) ? [...CensorState.targetClasses] : null;
}

// One-time-per-session heads-up: when the run will use NudeNet but its ONNX
// weights are not on disk yet, the first detect blocks for ~2 min while the
// library downloads them. Tell the user so the spinner does not read as frozen.
let _nudenetColdStartWarned = false;
function maybeWarnNudenetColdStart(modelType) {
    if (_nudenetColdStartWarned) return;
    if (modelType !== 'nudenet' && modelType !== 'both') return;
    const nudenet = (CensorState.backendModelStatus?.models || []).find((model) => model.id === 'nudenet');
    // Only warn when we positively know the model is missing. `model_downloaded`
    // is a newer field; if an older backend omits it, stay quiet rather than
    // nag on every run.
    if (!nudenet || nudenet.model_downloaded !== false) return;
    _nudenetColdStartWarned = true;
    window.App.showToast(
        censorT(
            'censor.nudenetFirstUseDownload',
            null,
            'First NudeNet run downloads its model (~12 MB) and shows install progress.'
        ),
        'info'
    );
}

async function resolveQuickAutoCensorExecutionPlan(options = {}) {
    const { silent = false } = options;
    const { showToast } = window.App;

    if (!CensorState.backendModelStatus) {
        await loadCensorModelStatus();
    }

    const modelTypeSelect = document.getElementById('censor-model-type');
    let modelType = modelTypeSelect?.value || 'legacy';
    let selectedLegacy = getSelectedLegacyModelRecord();
    const { fallbackModelType, privacyPath, canAutoRestore } = getQuickAutoCensorFallbackConfig();

    let switchMessage = '';

    if ((modelType === 'legacy' || modelType === 'both') && selectedLegacy?.profile !== 'privacy-censor') {
        if (!canAutoRestore) {
            return {
                ok: false,
                message: censorT(
                    'censor.quickAutoNeedsPrivacyDetector',
                    null,
                    'Quick Auto Censor needs a real privacy detector, but this machine does not have one ready yet.'
                ),
            };
        }

        if (modelType === 'legacy' && fallbackModelType) {
            modelType = fallbackModelType;
            if (modelTypeSelect) {
                modelTypeSelect.value = modelType;
            }
        }

        if (privacyPath) {
            setPreferredLegacyModelPath(privacyPath);
        }

        updateDetectionModelInputs();
        selectedLegacy = getSelectedLegacyModelRecord();

        const routeLabel = modelType === 'both'
            ? censorT('censor.bothMode', null, 'Both mode')
            : (modelType === 'nudenet'
                ? 'NudeNet'
                : censorT('censor.privacyPartDetector', null, 'the privacy-part detector'));

        switchMessage = censorT(
            'censor.quickAutoSwitchedRoute',
            { routeLabel },
            'Quick Auto Censor switched back to {routeLabel} so the general YOLO test model will not blur unrelated parts of the image.'
        );

        if (!silent && switchMessage) {
            showToast(switchMessage, 'warning');
        }
    }

    const targetClasses = getSelectedTargetClassesForDetection(modelType);
    if (Array.isArray(targetClasses) && targetClasses.length === 0) {
        return {
            ok: false,
            message: censorT('censor.quickTargetRequired', null, 'Select at least one quick privacy target first.'),
        };
    }

    if ((modelType === 'nudenet' || modelType === 'both') && typeof window.ensureFeatureModel === 'function') {
        const ensured = await window.ensureFeatureModel('censor-nudenet', {
            label: 'NudeNet',
            sizeHint: '~12 MB',
            confirmBytes: 0,
        });
        if (!ensured.ok) {
            return {
                ok: false,
                message: censorT(
                    'censor.nudenetInstallFailed',
                    null,
                    'NudeNet could not be installed. Open Setup / Download, or try Detect again.'
                ),
            };
        }
        CensorState.backendModelStatus = null;
        await loadCensorModelStatus();
    }

    return {
        ok: true,
        modelType,
        modelPath: getSelectedLegacyModelPath(),
        targetClasses,
        switchMessage,
        selectedLegacy,
    };
}

// ============== Auto Censor Logic ==============

async function runAutoCensorBatch() {
    const { showToast } = window.App;
    if (!hasCensorQueueWork()) {
        showToast(censorT('censor.queueEmpty', null, 'Queue is empty'), 'error');
        return;
    }

    const executionPlan = await resolveQuickAutoCensorExecutionPlan();
    if (!executionPlan?.ok) {
        showToast(executionPlan?.message || censorT('censor.quickAutoStartFailed', null, 'Quick Auto Censor could not start.'), 'warning');
        return;
    }

    // Cold-start heads-up: NudeNet downloads its ONNX weights on the first
    // detect call (a ~2 min blocking fetch inside the library). Without this
    // the batch just sits on the spinner and looks frozen.
    maybeWarnNudenetColdStart(executionPlan.modelType);

    const tracker = window.App.createProgressTracker();

    _resetBatchStatus();
    showLoading(true, censorT('censor.autoCensorPreparing', null, 'Auto Censor · preparing queue...'));

    let count = 0;
    const detectionWarnings = new Set();
    const result = await processCensorBatchItems(async (item, { index, total }) => {
        showLoading(true, window.App.buildProgressText({
            progress: { message: item.originalFilename || item.outputFilename || `Image ${item.id}` },
            completed: index,
            total,
            tracker,
            defaultMessage: censorT('censor.autoCensorRunning', null, 'Running auto-censor...'),
            primaryLabel: censorT('censor.autoCensorPrimary', null, 'Auto Censor')
        }));
        const warnings = await runDetectionForImage(item, true, executionPlan); // true = silent/no-refresh
        warnings.forEach((warning) => detectionWarnings.add(warning));
        count += 1;
    });

    showLoading(false);
    renderQueue();
    // Reload canvas if active item was updated
    if (CensorState.activeId) loadCanvasImage(CensorState.activeId);

    // Honest summary: runDetectionForImage now marks item.batchStatus in silent
    // mode, so a run where every detection threw no longer shows a green
    // "complete" toast. renderQueue() already red-outlines failed thumbs.
    const { failedCount } = _summarizeBatchFailures();
    const { appliedCount, emptyCount } = _summarizeBatchDetections();
    if (failedCount > 0) {
        const okCount = Math.max(0, count - failedCount);
        showToast(
            censorT(
                'censor.batchProcessingPartial',
                { ok: okCount, failed: failedCount, total: result.total },
                `Auto-censor finished — ${okCount} done, ${failedCount} failed (of ${result.total}).`
            ),
            failedCount >= count ? 'error' : 'warning'
        );
    } else if (appliedCount === 0 && emptyCount > 0) {
        // Every image ran cleanly but nothing was detected — never claim the
        // batch was "processed". Point the user at the two knobs that fix it.
        showToast(
            censorT(
                'censor.batchProcessingNoneDetected',
                { total: emptyCount },
                `Auto-censor ran on ${emptyCount} image(s) but found no regions. Try lowering the confidence threshold or switching the detection model.`
            ),
            'warning'
        );
    } else if (emptyCount > 0) {
        // Mixed: some images censored, some had nothing to censor.
        showToast(
            censorT(
                'censor.batchProcessingSomeEmpty',
                { applied: appliedCount, empty: emptyCount },
                `Auto-censor applied to ${appliedCount} image(s); ${emptyCount} had no detectable regions.`
            ),
            'success'
        );
    } else {
        showToast(
            executionPlan.switchMessage
                ? censorT('censor.batchProcessingCompleteAutoRestored', null, 'Batch processing complete. The app auto-restored the privacy detector before running.')
                : censorT('censor.batchProcessingComplete', { count, total: result.total }, 'Batch processing complete'),
            'success'
        );
    }

    if (detectionWarnings.size > 0) {
        showToast(Array.from(detectionWarnings).join(' '), 'warning');
    }
}

// Bake a set of detected regions into an item (proxy edit-op path or full
// canvas dataURL path). Extracted from runDetectionForImage so the 审核 review
// conveyor can bake an approved SUBSET. When `data.combined_mask*` is present it
// is used for precise masks (covers ALL regions), so the review approve path
// passes a mask-stripped `data` when the user excluded any region — then each
// kept region is censored by its own box instead. Returns whether it reloaded
// the active canvas (so the caller can skip a redundant reload).
async function applyDetectedRegionsToItem(item, regions, data = {}) {
    const useBoxShape = CensorState.maskShape === 'box';
    let reloadedActiveItem = false;

    const { maskRegions, boxRegions } = splitDetectionGeometry(regions);
    const combinedMaskSource = {
        mask: useBoxShape ? null : (data.combined_mask || null),
        mask_ref: useBoxShape ? null : (data.combined_mask_ref || null),
        mask_bounds: (!useBoxShape && Array.isArray(data.combined_mask_bounds)) ? cloneNumberArray(data.combined_mask_bounds) : null,
        image_width: data.image_width,
        image_height: data.image_height,
    };
    const shouldUseMask = Boolean(combinedMaskSource.mask || combinedMaskSource.mask_ref) && maskRegions.length > 0;
    const shouldUseBoxes = boxRegions.length > 0;

    if (shouldUseProxyEditMode(item)) {
        if (shouldUseMask || shouldUseBoxes) {
            item.editOperations = [{
                kind: 'geometry_effect',
                style: CensorState.style,
                block_size: Number(CensorState.blockSize || 16),
                blur_radius: Math.max(1, Math.round(CensorState.blockSize / 2)),
                regions,
            }];
            item.currentDataUrl = null;
            item.isProcessed = true;
            if (item.id === CensorState.activeId) {
                await loadCanvasImage(item.id);
                reloadedActiveItem = true;
            } else {
                await renderProxyPreviewDataForItem(item);
            }
        } else {
            item.editOperations = [];
            item.previewDataUrl = null;
            item.currentDataUrl = null;
            item.isProcessed = false;
            item.isModified = false;
            if (item.id === CensorState.activeId) {
                await loadCanvasImage(item.id);
                reloadedActiveItem = true;
            }
        }
    } else {
        // Apply to a temporary canvas to generate DataURL
        const img = await loadImage(item.originalUrl);
        const cvs = document.createElement('canvas');
        cvs.width = img.width;
        cvs.height = img.height;
        const ctx = cvs.getContext('2d');
        ctx.drawImage(img, 0, 0);

        if (shouldUseMask) {
            const maskOperation = createMaskEffectOperation(combinedMaskSource);
            await applyMaskOperationToCanvas(cvs, img, maskOperation, 1, 1);
        }
        if (shouldUseBoxes) {
            applyBoxRegionsToCanvas(cvs, img, boxRegions);
        }

        if (shouldUseMask || shouldUseBoxes) {
            item.currentDataUrl = cvs.toDataURL('image/png');
            item.isProcessed = true;
        } else {
            item.currentDataUrl = null;
            item.isProcessed = false;
        }
        item.previewDataUrl = null;
    }

    return { reloadedActiveItem, shouldUseMask, shouldUseBoxes };
}

async function runDetectionForImage(item, silent = false, executionPlan = null) {
    try {
        let reloadedActiveItem = false;
        const plan = executionPlan || await resolveQuickAutoCensorExecutionPlan({ silent });
        if (!plan?.ok) {
            item.regions = [];
            item.currentDataUrl = null;
            item.previewDataUrl = null;
            item.editOperations = [];
            item.isProcessed = false;
            // Silent (batch) callers rely on batchStatus for an honest summary —
            // a detector that never started is a failure, not a success.
            if (silent) {
                item.batchStatus = 'failed';
                item.batchError = plan?.message || 'Quick Auto Censor could not start.';
            }
            if (!silent && item.id === CensorState.activeId) {
                loadCanvasImage(item.id);
                window.App.showToast(
                    plan?.message || censorT('censor.quickAutoStartFailed', null, 'Quick Auto Censor could not start.'),
                    'warning'
                );
            }
            return [];
        }

        // Single-image runs resolve their own plan; batch runs warn once up
        // front. The latch inside makes a double call harmless.
        if (!silent) maybeWarnNudenetColdStart(plan.modelType);

        const detectBody = {
            image_id: item.id,
            model_path: plan.modelPath,
            model_type: plan.modelType,
            confidence_threshold: CensorState.confidence,
            target_classes: plan.targetClasses,
        };
        if (plan.modelType === 'sam3') {
            const customInput = document.getElementById('sam3-custom-prompt')?.value?.trim();
            if (customInput) {
                detectBody.text_prompts = customInput.split(',').map(s => s.trim()).filter(Boolean);
            }
        }
        const data = await window.App.API.post('/api/censor/detect', detectBody);
        const detectionWarnings = readCensorDetectionWarnings(data);

        const useBoxShape = CensorState.maskShape === 'box';
        const rawRegions = [...(data.detections || [])].sort((a, b) => b.confidence - a.confidence);
        // "Box" shape mode: drop the model's polygon/mask geometry so censoring
        // follows rectangles instead of the precise pixel outline.
        const regions = useBoxShape
            ? rawRegions.map((r) => {
                const { polygon, mask, ...rest } = r;
                return rest;
            })
            : rawRegions;
        item.regions = regions;

        // Precise was requested but a seg-capable model returned only boxes
        // (e.g. a plain detect-only YOLO file): nudge the user toward a -seg model.
        const anyPolygon = regions.some((r) => Array.isArray(r.polygon) && r.polygon.length >= 3);
        const segCapableModel = plan.modelType === 'legacy' || plan.modelType === 'both';
        if (!silent && !useBoxShape && segCapableModel && regions.length > 0 && !anyPolygon) {
            window.App.showToast(
                censorT('censor.maskShapeBoxOnly', null, 'This YOLO model returns boxes only (not a segmentation model). Use a -seg model like Wenaka for precise shapes.'),
                'info'
            );
        }

        // Bake the detected regions into the item. Extracted so the 审核 review
        // conveyor can reuse the exact same baking for an approved region subset.
        // The geometry flags come back so the toast below can describe what ran.
        const bakeResult = await applyDetectedRegionsToItem(item, regions, data);
        reloadedActiveItem = bakeResult.reloadedActiveItem;
        // Detection + bake succeeded (0 regions is a valid result, not a
        // failure). Record it — plus the region count — so a batch summary can
        // report applied vs. found-nothing vs. failed.
        if (silent) {
            item.batchStatus = 'done';
            item.batchRegionCount = regions.length;
        }

        if (!silent && item.id === CensorState.activeId) {
            if (!reloadedActiveItem) {
                await loadCanvasImage(item.id);
            }
            if (regions.length === 0) {
                window.App.showToast(
                    censorT('censor.noMatchingRegionsHint', null, 'No matching regions were found. Try lowering confidence or changing the model.'),
                    'info'
                );
            } else {
                const usedMask = bakeResult.shouldUseMask;
                window.App.showToast(
                    usedMask && bakeResult.shouldUseBoxes
                        ? censorT('censor.autoCensorAppliedMixed', { count: regions.length }, 'Applied mixed auto-censor to {count} region(s)')
                        : (usedMask
                            ? censorT('censor.autoCensorAppliedMask', { count: regions.length }, 'Applied auto-censor mask to {count} matched region(s)')
                            : censorT('censor.autoCensorAppliedBoxes', { count: regions.length }, 'Applied box-based auto-censor to {count} region(s)')),
                    'success'
                );
            }
        }

        if (!silent && detectionWarnings.length > 0) {
            window.App.showToast(detectionWarnings.join(' '), 'warning');
        }
        return detectionWarnings;

    } catch (e) {
        Logger.error(e);
        // Batch callers: mark the item failed so runAutoCensorBatch reports an
        // honest ok/failed split instead of an unconditional success toast.
        if (silent) {
            item.batchStatus = 'failed';
            item.batchError = formatUserError(e, censorT('censor.detectFailed', null, 'Detection failed'));
        }
        if (!silent) {
            window.App.showToast(
                formatUserError(e, censorT('censor.detectFailed', null, 'Detection failed')),
                'error'
            );
        }
        return [];
    }
}

async function segmentCurrentImageByText() {
    if (!CensorState.activeId) {
        window.App.showToast(censorT('censor.noImageSelected', null, 'No image selected'), 'error');
        return;
    }

    const textPrompt = String(document.getElementById('censor-text-prompt')?.value || '').trim();
    if (!textPrompt) {
        window.App.showToast(
            censorT('censor.textPromptRequired', null, 'Enter a text prompt first'),
            'warning'
        );
        return;
    }

    showLoading(true, censorT('censor.loadingSegmentText', {
        prompt: textPrompt,
    }, 'SAM3 text segment · {prompt}'));
    try {
        const result = await window.App.API.post('/api/censor/segment-text', {
            image_id: CensorState.activeId,
            text_prompt: textPrompt,
        });

        if (!result?.mask && !result?.mask_ref) {
            window.App.showToast(
                result?.message || censorT('censor.sam3NoMatch', null, 'No matching regions were found'),
                'info'
            );
            return;
        }

        await applyRasterMaskToActiveCanvas(result);
        window.App.showToast(
            censorT('censor.sam3Applied', {
                prompt: textPrompt,
            }, 'Applied SAM3 mask for "{prompt}"'),
            'success'
        );
    } catch (error) {
        window.App.showToast(
            formatUserError(error, censorT('censor.sam3SegmentFailed', null, 'SAM3 text segmentation failed')),
            'error'
        );
    } finally {
        showLoading(false);
    }
}

async function runDetectionForAll() {
    const { showToast } = window.App;
    if (!hasCensorQueueWork()) {
        showToast(censorT('censor.queueEmpty', null, 'Queue is empty'), 'error');
        return;
    }

    const executionPlan = await resolveQuickAutoCensorExecutionPlan();
    if (!executionPlan?.ok) {
        showToast(executionPlan?.message || censorT('censor.quickAutoStartFailed', null, 'Quick Auto Censor could not start.'), 'warning');
        return;
    }

    _resetBatchStatus();
    const tracker = window.App.createProgressTracker();
    showLoading(true, censorT('censor.loadingDetectPreparing', null, 'Detect All · preparing queue...'));
    let count = 0;
    let failedCount = 0;
    const detectionWarnings = new Set();

    const result = await processCensorBatchItems(async (item, { index, total }) => {
        try {
            showLoading(true, window.App.buildProgressText({
                progress: { message: item.originalFilename || item.outputFilename || `Image ${item.id}` },
                completed: index,
                total,
                tracker,
                defaultMessage: censorT('censor.loadingDetectDefault', null, 'Running detection...'),
                primaryLabel: censorT('censor.loadingDetectPrimary', null, 'Detect All')
            }));
            const warnings = await runDetectionForImage(item, true, executionPlan);
            warnings.forEach((warning) => detectionWarnings.add(warning));
            if (item.batchStatus === 'failed') {
                failedCount += 1;
                return;
            }
            item.batchStatus = 'detected';
            count++;
        } catch (e) {
            Logger.error('Detection error for', item.id, e);
            item.batchStatus = 'failed';
            item.batchError = `${censorT('censor.detectFailed', null, 'Detection failed')}: ${e?.message || e || ''}`.trim();
            failedCount += 1;
        }
    });

    showLoading(false);
    renderQueue();
    if (CensorState.activeId) loadCanvasImage(CensorState.activeId);
    failedCount = Math.max(failedCount, _summarizeBatchFailures().failedCount);
    const total = result.total;
    if (failedCount > 0) {
        showToast(
            censorT('censor.detectPartial', {
                count,
                total,
                failedCount,
            }, 'Detection: {count}/{total} processed · {failedCount} failed (red-outlined thumbnails)'),
            'warning'
        );
    } else {
        showToast(
            executionPlan.switchMessage
                ? censorT('censor.detectCompleteAutoRestored', { count, total }, 'Detection complete: {count}/{total} images processed. The app auto-restored the privacy detector first.')
                : censorT('censor.detectComplete', { count, total }, 'Detection complete: {count}/{total} images processed'),
            'success'
        );
    }

    if (detectionWarnings.size > 0) {
        showToast(Array.from(detectionWarnings).join(' '), 'warning');
    }
}


async function runSam3BatchRefine() {
    const { showToast } = window.App;
    if (CensorState.queue.length === 0) {
        showToast(censorT('censor.queueEmpty', null, 'Queue is empty'), 'error');
        return;
    }

    // Build batch items from queue items that have detection regions with boxes
    const batchItems = [];
    for (const item of CensorState.queue) {
        if (!Array.isArray(item.regions) || item.regions.length === 0) continue;
        for (const region of item.regions) {
            if (Array.isArray(region.box) && region.box.length === 4) {
                batchItems.push({
                    image_id: item.id,
                    box: region.box,
                    text_prompt: null,
                });
            }
        }
    }

    if (batchItems.length === 0) {
        showToast(
            censorT('censor.noDetectionBoxesFound', null, 'No detection boxes found. Run detection first, then use SAM3 to refine the masks.'),
            'warning'
        );
        return;
    }

    // Only reset status for items included in this batch so items untouched by SAM3 keep
    // any prior save/detect status visual.
    const includedIds = new Set(batchItems.map((entry) => entry.image_id));
    CensorState.queue.forEach((item) => {
        if (includedIds.has(item.id)) {
            delete item.batchStatus;
            delete item.batchError;
        }
    });

    showLoading(true, censorT('censor.loadingSam3Batch', {
        current: 0,
        total: batchItems.length,
    }, 'SAM3 Batch Refine · {current}/{total}'));

    try {
        const result = await window.App.API.post('/api/censor/batch-refine-mask', {
            items: batchItems,
            sam3_confidence: CensorState.sam3Confidence,
        });

        showLoading(false);

        const refinedIds = new Set();
        const failedErrorById = new Map();
        for (const refined of result.results || []) {
            if (refined.status === 'ok' && (refined.mask || refined.mask_ref)) {
                refinedIds.add(refined.image_id);
            } else {
                failedErrorById.set(
                    refined.image_id,
                    refined.error || censorT('censor.maskRefineNoResult', null, 'Mask refine returned no result')
                );
            }
        }

        if (result.completed > 0) {
            // Apply refined masks back to queue items
            for (const refined of result.results) {
                if (refined.status !== 'ok' || (!refined.mask && !refined.mask_ref)) continue;
                const item = CensorState.queue.find(i => i.id === refined.image_id);
                if (!item) continue;

                // Apply mask to this item's canvas
                try {
                    const operation = createMaskEffectOperation(refined);
                    if (shouldUseProxyEditMode(item)) {
                        item.editOperations = [
                            ...(item.editOperations || []),
                            operation,
                        ];
                        item.currentDataUrl = null;
                        await renderProxyPreviewDataForItem(item);
                    } else {
                        const img = await loadImage(item.currentDataUrl || item.originalUrl);
                        const cvs = document.createElement('canvas');
                        cvs.width = img.width;
                        cvs.height = img.height;
                        const ctx = cvs.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        await applyMaskOperationToCanvas(cvs, img, operation, 1, 1);
                        item.currentDataUrl = cvs.toDataURL('image/png');
                    }
                    item.isProcessed = true;
                    item.batchStatus = 'refined';
                } catch (maskErr) {
                    Logger.error('Failed to apply SAM3 mask for item', refined.image_id, maskErr);
                    refinedIds.delete(refined.image_id);
                    failedErrorById.set(
                        refined.image_id,
                        `${censorT('censor.maskApplyFailed', null, 'Mask apply failed')}: ${maskErr?.message || ''}`.trim()
                    );
                }
            }
        }

        CensorState.queue.forEach((item) => {
            if (!includedIds.has(item.id)) return;
            if (failedErrorById.has(item.id)) {
                item.batchStatus = 'failed';
                item.batchError = `${censorT('censor.sam3RefineFailed', null, 'SAM3 refine failed')}: ${failedErrorById.get(item.id)}`;
            }
        });

        renderQueue();
        if (CensorState.activeId) loadCanvasImage(CensorState.activeId);

        const refinedCount = result.refined ?? result.completed;
        const fallbackCount = result.fallback ?? 0;
        showToast(
            censorT('censor.sam3BatchComplete', {
                refined: refinedCount,
                fallback: fallbackCount,
                failed: result.failed,
                total: result.total,
            }, 'SAM3 Batch Refine: {refined} refined, {fallback} kept as box, {failed} failed (of {total} boxes)'),
            (result.failed > 0 || fallbackCount > 0) ? 'warning' : 'success'
        );
    } catch (e) {
        showLoading(false);
        Logger.error('SAM3 Batch Refine error:', e);
        CensorState.queue.forEach((item) => {
            if (!includedIds.has(item.id)) return;
            item.batchStatus = 'failed';
            item.batchError = `${censorT('censor.sam3BatchAborted', null, 'SAM3 batch aborted')}: ${e?.message || e || ''}`.trim();
        });
        renderQueue();
        showToast(
            formatUserError(e, censorT('censor.sam3BatchRefineFailed', null, 'SAM3 Batch Refine failed')),
            'error'
        );
    }
}


