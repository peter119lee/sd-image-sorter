/**
 * Censor Editor - detection model UI (split VERBATIM from censor-edit.js; god-file decomposition).
 * Backend model status, capability panel, legacy model select/help, SAM3 common-words popup, recommended-mode marker.
 * Shared top-level bindings (CensorState, ...) are declared in censor/state.js;
 * classic-script global lexical scoping keeps them single instances across parts.
 * Load order is pinned in index.html - see censor/state.js for the full note.
 */
// MODELS-05: surface the backend-recommended detector inline on the
// #censor-model-type dropdown so a new user can see which mode to pick without
// reading the separate "Recommended mode" banner. The option text is owned by
// i18n (data-i18n -> textContent on every languageChanged), so we recompute the
// base label from the data-i18n key and re-append the marker; this runs after
// i18n.applyToDOM() because the languageChanged event is dispatched last.
function markRecommendedDetectorMode() {
    const select = document.getElementById('censor-model-type');
    if (!select) return;
    const recommended = CensorState.backendModelStatus?.recommended_backend || '';
    const label = censorT('censor.recommendedTag', null, 'Recommended');
    Array.from(select.options).forEach((option) => {
        const key = option.getAttribute('data-i18n');
        const base = key && window.I18n?.t ? window.I18n.t(key) : option.textContent.replace(/\s+\([^)]*\)\s*$/, '');
        // Some option labels (e.g. censor.both = "Recommended: Both …") already
        // carry the recommended word in their i18n string; don't double-mark.
        const alreadyMarked = base.includes(label);
        option.textContent = (recommended && option.value === recommended && !alreadyMarked)
            ? `${base} (${label})`
            : base;
    });
}

async function loadCensorModelStatus() {
    const banner = document.getElementById('censor-model-health');
    const simpleGuide = document.getElementById('censor-simple-guide');
    if (CensorState.backendModelStatus) {
        return CensorState.backendModelStatus;
    }
    if (censorModelStatusPromise) {
        return censorModelStatusPromise;
    }

    CensorState.modelStatusError = '';
    CensorState.modelStatusLoading = true;
    renderCensorCapabilityPanel({ loading: true });

    censorModelStatusPromise = (async () => {
        try {
            const result = await window.App.API.get('/api/censor/models');
            CensorState.backendModelStatus = result;

            const legacy = (result.models || []).find(model => model.id === 'legacy');
            CensorState.availableLegacyModels = legacy?.files || [];
            populateCensorModelSelect(legacy);
            const modelTypeSelect = document.getElementById('censor-model-type');
            if (modelTypeSelect && result.recommended_backend) {
                modelTypeSelect.value = result.recommended_backend;
            }
            markRecommendedDetectorMode();
            updateDetectionModelInputs();

            if (banner) {
                const classes = ['model-health-banner', 'model-health-banner-compact', 'is-visible'];
                if (!result.recommended_backend) {
                    classes.push('model-health-banner-danger');
                } else if (!(legacy?.available)) {
                    classes.push('model-health-banner-warning');
                }

                const readyNotes = (result.models || [])
                    .filter(model => model.available)
                    .map(model => model.name)
                    .join(' / ');
                const recommended = result.recommended_backend
                    ? censorT('censor.recommendedMode', { backend: result.recommended_backend }, 'Recommended mode: {backend}.')
                    : censorT('censor.noDetectionBackendReady', null, 'No detection backend is fully ready yet.');
                const defaultLegacy = legacy?.files?.find(file => file.path === legacy?.default_model_path);
                const extraNotes = [];
                if (defaultLegacy) {
                    extraNotes.push(censorT(
                        'censor.legacyDefaultNamed',
                        {
                            name: defaultLegacy.name,
                            profile: defaultLegacy.profile_label,
                        },
                        'Legacy default: {name} ({profile})'
                    ));
                } else if (legacy?.default_model_path) {
                    extraNotes.push(censorT('censor.legacyDefaultPath', { path: legacy.default_model_path }, 'Legacy default: {path}'));
                }
                if ((legacy?.general_model_count || 0) > 0) {
                    extraNotes.push(censorT(
                        'censor.generalModelCount',
                        { count: legacy.general_model_count },
                        '{count} general YOLO model(s) installed for compatibility tests'
                    ));
                }
                const extra = extraNotes.length
                    ? `<br><small>${escapeHtml(extraNotes.join(' · '))}</small>`
                    : '';

                banner.className = classes.join(' ');
                banner.innerHTML = `<strong>${escapeHtml(censorT('censor.modelReadyLabel', null, 'Detection Ready'))}:</strong> ${escapeHtml(readyNotes || censorT('common.none', null, 'None'))} ${escapeHtml(recommended)}${extra}`;
            }
            if (simpleGuide) {
                simpleGuide.textContent = legacy?.simple_user_advice || censorT(
                    'censor.keepRecommendedModeHelp',
                    null,
                    'Keep the recommended mode and only touch custom paths if you know why.'
                );
            }
            renderCensorCapabilityPanel();
            return result;
        } catch (e) {
            CensorState.modelStatusError = e?.message || censorT('censor.modelReadinessLoadFailed', null, 'Model readiness could not be loaded right now.');
            if (banner) {
                banner.className = 'model-health-banner model-health-banner-compact is-visible model-health-banner-warning';
                banner.textContent = censorT('censor.modelReadinessLoadFailed', null, 'Model readiness could not be loaded right now.');
            }
            if (simpleGuide) {
                simpleGuide.textContent = '';
            }
            renderCensorCapabilityPanel();
            return null;
        } finally {
            CensorState.modelStatusLoading = false;
            censorModelStatusPromise = null;
        }
    })();

    return censorModelStatusPromise;
}

function getLegacyModelRecordByPath(path) {
    const normalized = String(path || '').trim();
    if (!normalized) return null;
    return CensorState.availableLegacyModels.find(file => file?.path === normalized) || null;
}

function getSelectedLegacyModelRecord() {
    const manualPath = String(document.getElementById('censor-model-path')?.value || '').trim();
    const selectedPath = manualPath || String(document.getElementById('censor-model-file')?.value || '').trim();
    const legacy = (CensorState.backendModelStatus?.models || []).find(model => model.id === 'legacy');
    return getLegacyModelRecordByPath(selectedPath) || getLegacyModelRecordByPath(legacy?.default_model_path);
}

function formatCensorCapabilityLine(labelKey, value, fallbackLabel) {
    return `${censorT(labelKey, null, fallbackLabel)}: ${value}`;
}

function formatCensorYesNo(value) {
    return value
        ? censorT('censor.yes', null, 'Yes')
        : censorT('censor.no', null, 'No');
}

function buildCapabilityCardHtml(title, badge, lines = [], note = '', { recommended = false } = {}) {
    const safeLines = Array.isArray(lines)
        ? lines.filter(Boolean).map(line => `<div>${escapeHtml(line)}</div>`).join('')
        : '';
    const cardClass = recommended
        ? 'censor-capability-card is-recommended'
        : 'censor-capability-card';
    return `
        <div class="${cardClass}">
            <div class="censor-capability-title">
                <span>${escapeHtml(title)}</span>
                ${badge ? `<span class="censor-capability-badge">${escapeHtml(badge)}</span>` : ''}
            </div>
            <div class="censor-capability-lines">${safeLines}</div>
            ${note ? `<div class="censor-capability-note">${escapeHtml(note)}</div>` : ''}
        </div>
    `;
}

function renderCensorCapabilityPanel(options = {}) {
    const panel = document.getElementById('censor-capability-panel');
    const targetHelp = document.getElementById('censor-target-region-help');
    const promptHelp = document.getElementById('censor-text-prompt-help');
    const promptInput = document.getElementById('censor-text-prompt');
    const simpleGuide = document.getElementById('censor-simple-guide');
    const segmentButton = document.getElementById('btn-segment-text-current');
    const batchRefineButton = document.getElementById('btn-sam3-batch-refine');
    const targetGroup = document.getElementById('censor-target-region-group');
    const targetChecks = Array.from(document.querySelectorAll('.target-region-check'));

    if (!panel) return;

    const isLoading = Boolean(options.loading || CensorState.modelStatusLoading);
    const loadError = String(CensorState.modelStatusError || '').trim();

    if (!CensorState.backendModelStatus) {
        panel.innerHTML = buildCapabilityCardHtml(
            censorT('censor.modelReadinessTitle', null, 'Model readiness'),
            isLoading ? censorT('common.loading', null, 'Loading...') : censorT('censor.unavailable', null, 'Unavailable'),
            isLoading
                ? [
                    censorT('censor.modelReadinessChecking', null, 'Checking local YOLO, NudeNet, and SAM3 availability...'),
                    censorT('censor.modelReadinessPendingHint', null, 'The panel will fill in as soon as the backend responds.'),
                ]
                : [
                    loadError || censorT('censor.modelReadinessLoadFailed', null, 'Model readiness could not be loaded right now.'),
                    censorT('censor.modelReadinessReloadHint', null, 'You can reopen this dialog after the backend finishes loading.'),
                ],
            ''
        );

        targetChecks.forEach(input => {
            input.disabled = true;
        });
        if (targetGroup) {
            targetGroup.style.display = '';
            targetGroup.classList.add('is-disabled');
        }
        if (targetHelp) {
            targetHelp.textContent = isLoading
                ? censorT('censor.quickTargetsLoading', null, 'Quick privacy targets are loading.')
                : censorT('censor.quickTargetsUnavailable', null, 'Quick privacy targets are temporarily unavailable.');
        }
        if (promptHelp) {
            promptHelp.textContent = isLoading
                ? censorT('censor.sam3ReadinessLoading', null, 'Loading SAM3 readiness for the pro prompt tool.')
                : censorT('censor.sam3ReadinessUnavailable', null, 'SAM3 readiness is temporarily unavailable.');
        }
        if (promptInput) {
            promptInput.readOnly = false;
            promptInput.removeAttribute('disabled');
            promptInput.setAttribute('aria-disabled', 'false');
        }
        if (segmentButton) {
            segmentButton.disabled = true;
            segmentButton.title = isLoading
                ? censorT('censor.modelReadinessButtonLoading', null, 'Loading model readiness…')
                : censorT('censor.modelReadinessButtonUnavailable', null, 'Model readiness is unavailable right now.');
        }
        if (batchRefineButton) {
            batchRefineButton.disabled = true;
            batchRefineButton.title = isLoading
                ? censorT('censor.sam3ReadinessButtonLoading', null, 'Loading SAM3 readiness…')
                : censorT('censor.sam3ReadinessButtonUnavailable', null, 'SAM3 readiness is unavailable right now.');
        }
        if (simpleGuide) {
            simpleGuide.textContent = isLoading
                ? censorT('censor.recommendedRouteLoading', null, 'Loading the recommended detection route…')
                : censorT('censor.modelReadinessTemporaryUnavailable', null, 'Model readiness is temporarily unavailable.');
        }
        return;
    }

    const models = CensorState.backendModelStatus?.models || [];
    const legacy = models.find(model => model.id === 'legacy');
    const nudenet = models.find(model => model.id === 'nudenet');
    const sam3 = models.find(model => model.id === 'sam3');
    const selectedLegacy = getSelectedLegacyModelRecord();
    const modelType = document.getElementById('censor-model-type')?.value || 'legacy';
    const quickAutoFallback = getQuickAutoCensorFallbackConfig();

    const cards = [];
    if (selectedLegacy) {
        const caps = selectedLegacy.capabilities || {};
        cards.push(buildCapabilityCardHtml(
            selectedLegacy.name,
            selectedLegacy.profile_label,
            [
                formatCensorCapabilityLine('censor.capabilityInput', caps.input_mode_label || censorT('censor.capabilityFixedModelLabels', null, 'Fixed model labels'), 'Input'),
                formatCensorCapabilityLine('censor.capabilityOutput', caps.output_mode_label || censorT('censor.capabilityLegacyDetection', null, 'Legacy detection'), 'Output'),
                formatCensorCapabilityLine('censor.capabilityScope', caps.class_scope_label || censorT('censor.capabilityUnknown', null, 'Unknown'), 'Scope'),
                formatCensorCapabilityLine('censor.capabilityTextPrompt', formatCensorYesNo(caps.supports_text_prompt), 'Text prompt'),
            ],
            caps.plain_english || selectedLegacy.message || '',
            { recommended: Boolean(selectedLegacy.recommended_for_censor) }
        ));
    }

    if (nudenet) {
        const caps = nudenet.capabilities || {};
        cards.push(buildCapabilityCardHtml(
            nudenet.name,
            nudenet.available ? censorT('common.ready', null, 'Ready') : censorT('censor.downloadsOnFirstUse', null, 'Downloads on first detect'),
            [
                formatCensorCapabilityLine('censor.capabilityInput', caps.input_mode_label || censorT('censor.capabilityBuiltInNsfwLabels', null, 'Built-in NSFW labels'), 'Input'),
                formatCensorCapabilityLine('censor.capabilityOutput', caps.output_mode_label || censorT('censor.capabilityDetectionBoxes', null, 'Detection boxes'), 'Output'),
                formatCensorCapabilityLine('censor.capabilityScope', caps.class_scope_label || censorT('censor.capabilityBuiltInNsfwLabels', null, 'Built-in NSFW labels'), 'Scope'),
                formatCensorCapabilityLine('censor.capabilityTextPrompt', formatCensorYesNo(caps.supports_text_prompt), 'Text prompt'),
            ],
            caps.plain_english || nudenet.message || '',
            { recommended: Boolean(nudenet.recommended) }
        ));
    }

    if (sam3) {
        const caps = sam3.capabilities || {};
        cards.push(buildCapabilityCardHtml(
            sam3.name,
            sam3.available ? censorT('censor.precision', null, 'Precision') : censorT('censor.gpuOnlyOptional', null, 'GPU-only optional'),
            [
                formatCensorCapabilityLine('censor.capabilityInput', caps.input_mode_label || censorT('censor.capabilityTextOrBoxPrompt', null, 'Text prompt or box prompt'), 'Input'),
                formatCensorCapabilityLine('censor.capabilityOutput', caps.output_mode_label || censorT('censor.capabilityPixelMasks', null, 'Pixel masks'), 'Output'),
                formatCensorCapabilityLine('censor.capabilityScope', caps.class_scope_label || censorT('censor.capabilityPromptGuidedSegmentation', null, 'Prompt-guided segmentation'), 'Scope'),
                formatCensorCapabilityLine('censor.capabilityTextPrompt', formatCensorYesNo(caps.supports_text_prompt), 'Text prompt'),
            ],
            caps.plain_english || sam3.message || '',
            { recommended: Boolean(sam3.available) }
        ));
    }

    panel.innerHTML = cards.join('');

    const quickFilterEnabled = shouldUseQuickTargetFilters(modelType);
    const quickFilterDisabled = shouldDisableQuickTargetFilters(modelType);
    targetChecks.forEach(input => {
        input.disabled = quickFilterDisabled;
    });
    if (targetGroup) {
        targetGroup.style.display = '';
        targetGroup.classList.toggle('is-disabled', quickFilterDisabled);
    }

    if (targetHelp) {
        if (modelType === 'both') {
            targetHelp.textContent = censorT('censor.quickTargetsBothHelp', null, 'These quick privacy targets work across Wenaka and NudeNet family labels. They do not control generic COCO classes.');
        } else if (modelType === 'nudenet') {
            targetHelp.textContent = censorT('censor.quickTargetsNudenetHelp', null, 'NudeNet uses its own label system, but these quick privacy targets now map to the matching NudeNet families.');
        } else if (quickFilterEnabled) {
            if (modelType === 'legacy' && selectedLegacy?.profile !== 'privacy-censor' && quickAutoFallback.canAutoRestore) {
                targetHelp.textContent = censorT('censor.quickTargetsFallbackHelp', null, 'These quick privacy targets stay active. When you run Quick Auto Censor, the app will switch back to the recommended privacy detector instead of using this general YOLO test model.');
            } else {
                targetHelp.textContent = censorT('censor.quickTargetsLegacyHelp', null, 'These quick privacy targets map to the fixed privacy classes inside the current local model.');
            }
        } else {
            targetHelp.textContent = censorT('censor.quickTargetsGeneralModelHelp', null, 'These quick privacy targets stay visible so you can see the normal workflow, but the current general segmentation model cannot map them. Switch back to the recommended privacy model or Both if you want clickable privacy presets.');
        }
    }

    if (promptHelp) {
        promptHelp.textContent = sam3?.available
            ? censorT('censor.promptHelpSam3Ready', null, 'Uses SAM3 text-prompt segmentation on the current image. This is the precise pro tool.')
            : censorT(
                'censor.promptHelpSam3Unavailable',
                { message: sam3?.message || '' },
                'You can still type a prompt here, but this machine cannot run SAM3 yet. {message}'
            ).trim();
    }

    if (promptInput) {
        promptInput.readOnly = false;
        promptInput.removeAttribute('disabled');
        promptInput.setAttribute('aria-disabled', 'false');
    }
    if (segmentButton) {
        segmentButton.disabled = !sam3?.available;
        segmentButton.title = sam3?.available
            ? ''
            : (sam3?.message || censorT('censor.sam3UnavailableMessage', null, 'SAM3 is not set up on this machine. Open Model Center and Prepare SAM3 (CUDA).'));
    }
    if (batchRefineButton) {
        batchRefineButton.disabled = !sam3?.available;
        batchRefineButton.title = sam3?.available
            ? ''
            : (sam3?.message || censorT('censor.sam3BatchUnavailableMessage', null, 'SAM3 batch refine is not available in this environment yet.'));
    }

    if (simpleGuide) {
        if (modelType === 'nudenet') {
            simpleGuide.textContent = censorT('censor.simpleGuideNudenet', null, 'NudeNet is the simple path: no text prompt, no custom labels. Use it when you want quick NSFW/body-region boxes.');
        } else if (modelType === 'both') {
            simpleGuide.textContent = censorT('censor.simpleGuideBoth', null, 'Recommended for most people: run NudeNet together with the auto-picked privacy model. If the local model has segmentation masks, the auto-censor path will use them.');
        } else if (selectedLegacy?.profile === 'privacy-censor') {
            simpleGuide.textContent = censorT('censor.simpleGuidePrivacyLegacy', null, 'This local model is the privacy-part route. It only understands its fixed privacy labels, but if it exposes segmentation masks the auto-censor path will use them instead of raw rectangles.');
        } else if (selectedLegacy) {
            simpleGuide.textContent = quickAutoFallback.canAutoRestore
                ? censorT('censor.simpleGuideAdvancedLegacyAutoRestore', { name: selectedLegacy.name }, '{name} is a general fixed-class segmentation model kept for advanced tests. Quick Auto Censor will automatically switch back to the recommended privacy route before it runs.')
                : censorT('censor.simpleGuideAdvancedLegacy', { name: selectedLegacy.name }, '{name} is a general fixed-class segmentation model kept for advanced tests. It can segment its own built-in object classes, but it is not an open-text privacy detector.');
        } else {
            simpleGuide.textContent = censorT('censor.simpleGuideDefault', null, 'Keep the recommended mode and leave custom paths blank unless you are doing advanced model experiments.');
        }
    }
}

function formatLegacyModelOptionLabel(file) {
    const profile = file?.profile_label ? ` - ${file.profile_label}` : '';
    const purpose = file?.recommended_for_censor
        ? censorT('censor.recommendedPrivacyRoute', null, 'Recommended privacy route')
        : censorT('censor.advancedTestOnly', null, 'Advanced test only');
    return `${file.name} (${file.size_mb} MB)${profile} · ${purpose}`;
}

function getVisibleLegacyModels(files, currentValue = '') {
    return files.filter((file) => {
        if (!file?.path) return false;
        if (CensorState.showAdvancedLegacyModels) return true;
        if (file.recommended_for_censor) return true;
        return Boolean(currentValue) && file.path === currentValue;
    });
}

function syncAdvancedLegacyModelUi(legacyModel) {
    const toggle = document.getElementById('censor-show-advanced-models');
    const help = document.getElementById('censor-advanced-models-help');
    if (toggle) {
        toggle.checked = CensorState.showAdvancedLegacyModels;
    }
    if (!help) return;
    help.removeAttribute('data-i18n');

    const generalCount = Number(legacyModel?.general_model_count || 0);
    if (generalCount <= 0) {
        help.textContent = censorT('censor.noAdvancedModelsFound', null, 'No extra general YOLO compatibility models were found locally.');
        return;
    }

    help.textContent = CensorState.showAdvancedLegacyModels
        ? censorT('censor.advancedModelsVisible', { count: generalCount }, '{count} advanced fixed-class YOLO model(s) are visible below. They are for compatibility tests, not normal privacy censoring.')
        : censorT('censor.advancedModelsHidden', { count: generalCount }, '{count} advanced fixed-class YOLO model(s) are hidden to keep the normal workflow simpler. Leave this off unless you intentionally want advanced fixed-class YOLO compatibility tests.');
}

function updateSelectedLegacyModelHelp(legacyModel) {
    const help = document.getElementById('censor-model-file-help');
    if (!help) return;
    help.removeAttribute('data-i18n');

    const manualPath = String(document.getElementById('censor-model-path')?.value || '').trim();
    if (manualPath) {
        help.textContent = censorT('censor.customPathActiveHelp', null, 'Custom path is active. Leave it blank if you want the app to auto-pick the recommended local privacy model.');
        return;
    }

    const selectedPath = String(document.getElementById('censor-model-file')?.value || '').trim();
    const selectedFile = getLegacyModelRecordByPath(selectedPath) || getLegacyModelRecordByPath(legacyModel?.default_model_path);
    if (!selectedFile) {
        help.textContent = censorT('censor.noLocalYoloFound', null, 'No local YOLO model was found. NudeNet can still work if it is installed.');
        updateSelectedLegacyModelStatus(null);
        return;
    }

    const parts = [censorT('censor.selectedModel', { name: selectedFile.name }, 'Selected: {name}')];
    if (selectedFile.profile_label) {
        parts.push(selectedFile.profile_label);
    }
    if (selectedFile.message) {
        parts.push(selectedFile.message);
    }
    help.textContent = parts.join(' · ');
    updateSelectedLegacyModelStatus(selectedFile);
}

function updateSelectedLegacyModelStatus(selectedFile) {
    const status = document.getElementById('censor-model-type-status');
    if (!status) return;
    status.removeAttribute('data-i18n');

    if (!selectedFile) {
        status.textContent = '';
        return;
    }

    status.textContent = censorT('censor.selectedModelOption', { name: selectedFile.name }, 'Use this file: {name}');
}

function populateCensorModelSelect(legacyModel) {
    const select = document.getElementById('censor-model-file');
    if (!select) return;

    const currentValue = CensorState.modelPath || '';
    const files = Array.isArray(legacyModel?.files) ? legacyModel.files : [];
    const visibleFiles = getVisibleLegacyModels(files, currentValue);
    const seen = new Set();
    const options = [`<option value="">${escapeHtml(censorT('censor.autoPickRecommendedLocalModel', null, 'Auto-pick the recommended local model'))}</option>`];

    visibleFiles.forEach(file => {
        if (!file?.path || seen.has(file.path)) return;
        seen.add(file.path);
        const label = formatLegacyModelOptionLabel(file);
        options.push(`<option value="${escapeHtml(file.path)}">${escapeHtml(label)}</option>`);
    });

    select.innerHTML = options.join('');
    if (currentValue && seen.has(currentValue)) {
        select.value = currentValue;
    } else {
        select.value = '';
    }

    const modelPathInput = document.getElementById('censor-model-path');
    if (modelPathInput) {
        modelPathInput.value = currentValue && !seen.has(currentValue) ? currentValue : '';
    }

    syncAdvancedLegacyModelUi(legacyModel);
    updateSelectedLegacyModelHelp(legacyModel);
    renderCensorCapabilityPanel();
}

function updateDetectionModelInputs() {
    const modelType = document.getElementById('censor-model-type')?.value || 'legacy';
    const needsLegacyPath = modelType === 'legacy' || modelType === 'both';
    const modelFileGroup = document.getElementById('censor-model-file')?.closest('.form-group');
    const modelPathGroup = document.getElementById('censor-model-path')?.closest('.form-group');
    const manualPath = String(document.getElementById('censor-model-path')?.value || '').trim();
    const showAdvancedInputs = CensorState.showAdvancedLegacyModels || Boolean(manualPath);

    if (modelFileGroup) modelFileGroup.style.display = needsLegacyPath ? '' : 'none';
    if (modelPathGroup) modelPathGroup.style.display = needsLegacyPath && showAdvancedInputs ? '' : 'none';

    const sam3PromptRow = document.getElementById('sam3-prompt-row');
    if (sam3PromptRow) sam3PromptRow.style.display = modelType === 'sam3' ? '' : 'none';

    const sam3Group = document.getElementById('sam3-confidence-group');
    if (sam3Group) {
        const sam3Model = (CensorState.backendModelStatus?.models || []).find(m => m.id === 'sam3');
        sam3Group.style.display = sam3Model?.available ? '' : 'none';
    }

    renderCensorCapabilityPanel();
}

const SAM3_COMMON_PROMPTS = {
    'Privacy / NSFW': [
        'exposed female breast', 'exposed nipple', 'exposed female genitalia',
        'exposed male genitalia', 'exposed anus', 'exposed buttocks',
    ],
    'Body Parts': [
        'face', 'eyes', 'mouth', 'hands', 'feet', 'navel', 'armpit',
    ],
    'Clothing / Objects': [
        'underwear', 'bra', 'bikini', 'tattoo', 'piercing', 'watermark', 'text', 'logo',
    ],
};

function _showSam3CommonWordsPopup() {
    // Toggle: re-clicking the trigger while the popup is open closes it.
    // The previous design left users stranded — the popup had no close
    // button and was absolutely-positioned with no offsets, so it landed
    // at the parent's (0,0) and silently covered the censor sidebar
    // controls beneath it. Now: viewport-anchored, explicit close (✕),
    // Escape closes, click-outside closes.
    const existing = document.getElementById('sam3-common-popup');
    if (existing) {
        _closeSam3CommonWordsPopup(existing);
        return;
    }

    const btn = document.getElementById('btn-sam3-common-words');
    if (!btn) return;

    const popup = document.createElement('div');
    popup.id = 'sam3-common-popup';
    popup.className = 'sam3-common-popup visible';
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-label', 'SAM3 common prompts');
    popup.style.cssText = [
        'position:fixed',
        'z-index:9100',
        'background:var(--bg-card-solid,#1E1E1E)',
        'border:1px solid var(--glass-border,rgba(var(--accent-rgb), 0.18))',
        'border-radius:12px',
        'padding:10px 12px 12px',
        'width:min(320px, calc(100vw - 24px))',
        'max-height:min(420px, calc(100vh - 96px))',
        'overflow-y:auto',
        'box-shadow:0 16px 40px rgba(0,0,0,0.5)',
        'color:var(--text-primary,#FBF7F2)',
    ].join(';') + ';';

    const closeLabel = (window.tKey?.('censor.sam3CloseHint', 'Close', '关闭')) || 'Close';
    const headerHelp = (window.tKey?.('censor.sam3CommonHelp', 'Click to add. Separate multiple with commas.', '点击添加；多个词用逗号分隔。')) || 'Click to add. Separate multiple with commas.';

    let html = `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <div style="flex:1;font-size:11px;color:var(--text-muted,#A6A6A6);line-height:1.4;">${window.escapeHtml(headerHelp)}</div>
            <button id="sam3-common-popup-close" type="button" aria-label="${window.escapeHtml(closeLabel)}" title="${window.escapeHtml(closeLabel)}" style="background:none;border:none;color:var(--text-muted,#A6A6A6);font-size:18px;line-height:1;cursor:pointer;padding:2px 8px;border-radius:6px;flex-shrink:0;">×</button>
        </div>
    `;
    for (const [group, words] of Object.entries(SAM3_COMMON_PROMPTS)) {
        html += `<div style="font-size:11px;font-weight:700;color:var(--text-secondary,#D6D6D6);margin:8px 0 4px;">${window.escapeHtml(group)}</div>`;
        html += '<div style="display:flex;flex-wrap:wrap;gap:4px;">';
        for (const word of words) {
            html += `<button type="button" class="btn btn-ghost btn-small sam3-word-chip" data-word="${window.escapeHtml(word)}" style="font-size:11px;padding:3px 8px;border-radius:6px;">${window.escapeHtml(word)}</button>`;
        }
        html += '</div>';
    }
    popup.innerHTML = html;
    document.body.appendChild(popup);

    const reposition = () => {
        window.PopupPosition?.place(popup, {
            anchor: btn,
            placement: 'bottom-end',
            gap: 8,
            maxHeight: Math.min(420, Math.max(160, window.innerHeight - 24)),
        });
    };
    reposition();

    // Chip click → add word into the prompt input.
    popup.addEventListener('click', (e) => {
        const chip = e.target.closest('.sam3-word-chip');
        if (!chip) return;
        const word = chip.dataset.word;
        const input = document.getElementById('sam3-custom-prompt');
        if (!input) return;
        const current = input.value.trim();
        if (current) {
            const existing = current.split(',').map((s) => s.trim());
            if (!existing.includes(word)) {
                input.value = current + ', ' + word;
            }
        } else {
            input.value = word;
        }
    });

    document.getElementById('sam3-common-popup-close')?.addEventListener('click', (e) => {
        e.stopPropagation();
        _closeSam3CommonWordsPopup(popup);
    });

    // Defer the click-outside handler so the click that opened the popup
    // doesn't immediately close it on its trailing event.
    setTimeout(() => {
        const outside = (e) => {
            if (popup.contains(e.target)) return;
            if (e.target.id === 'btn-sam3-common-words' || e.target.closest?.('#btn-sam3-common-words')) return;
            _closeSam3CommonWordsPopup(popup);
        };
        const onKeydown = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                _closeSam3CommonWordsPopup(popup);
            }
        };
        document.addEventListener('click', outside, true);
        document.addEventListener('keydown', onKeydown);
        window.addEventListener('resize', reposition);
        window.addEventListener('scroll', reposition, true);
        popup._sam3Cleanup = () => {
            document.removeEventListener('click', outside, true);
            document.removeEventListener('keydown', onKeydown);
            window.removeEventListener('resize', reposition);
            window.removeEventListener('scroll', reposition, true);
        };
    }, 0);
}

function _closeSam3CommonWordsPopup(popup) {
    const target = popup || document.getElementById('sam3-common-popup');
    if (!target) return;
    try { target._sam3Cleanup?.(); } catch (_err) {}
    target.remove();
}

